from __future__ import annotations

import threading
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assembly.arcs_jsonrpc import ArcsMotionSafetyStatus, ArcsRobotSnapshot
from assembly.competition_gui import CompetitionLauncherApp, parse_task_command
from assembly.local_settings import RememberedConnection
from assembly.offline_launch_settings import OfflineLaunchSettings
from assembly.offline_state import OfflineTaskKind
from assembly.robot_parameters import RobotGlobalParameters
from assembly.visionmaster_tcp import VisionMasterTcpImageClient, VisionMasterTcpConfig, measurement_request
from tests.test_offline_workflow import build_workflow


class DocumentRevisionTests(unittest.TestCase):
    def test_all_measurement_commands_are_unadorned_and_color_ids_distinct(self):
        for code in range(11, 20):
            self.assertEqual(measurement_request(str(code)), f"wukuai,{code}")
        for code in range(21, 27):
            self.assertEqual(measurement_request(str(code)), f"tuopan,{code}")
        for code in ("20", "99", "11;12", "11\\n"):
            with self.assertRaises(ValueError):
                measurement_request(code)

    def test_cancelled_vm_request_opens_no_connection(self):
        event = threading.Event()
        event.set()
        client = VisionMasterTcpImageClient(VisionMasterTcpConfig(), event)
        with patch("assembly.visionmaster_tcp.socket.create_connection") as connect:
            with self.assertRaises(RuntimeError):
                client.request_measurement("11")
        connect.assert_not_called()

    def test_configured_task_runs_without_model_classification(self):
        with TemporaryDirectory() as directory:
            workflow, collector, robot, model = build_workflow(
                directory, (OfflineTaskKind.TASK2,))
            model.classifications = iter((OfflineTaskKind.TASK1,))
            workflow.run()
            self.assertEqual(model.task2_images, [b"BM-card-1"])
            self.assertEqual(model.task1_images, [])
            self.assertEqual(model.classification_calls, 0)
            self.assertEqual(collector.card_numbers, [2])
            self.assertEqual(robot.card_photo_slots, [1])
            self.assertEqual(len(collector.vm_requests), 13)

    def test_text_commands_are_exact(self):
        self.assertEqual(parse_task_command("执行任务一"), ("task1",))
        self.assertEqual(parse_task_command("执行任务二"), ("task2",))
        for command in ("", "全流程", "仅任务一", "执行任务一 ", "执行任务3"):
            with self.assertRaisesRegex(ValueError, "只支持"):
                parse_task_command(command)

    def test_two_selected_tasks_run_in_selected_order(self):
        with TemporaryDirectory() as directory:
            workflow, collector, robot, model = build_workflow(
                directory, (OfflineTaskKind.TASK2, OfflineTaskKind.TASK1))
            workflow.run()
            self.assertEqual(model.task2_images, [b"BM-card-1"])
            self.assertEqual(model.task1_images, [b"BM-card-2"])
            self.assertEqual(model.classification_calls, 0)
            self.assertEqual(collector.card_numbers, [2, 1])
            self.assertEqual(robot.card_photo_slots, [1, 1])
            self.assertEqual(len(collector.vm_requests), 13)

    def test_reset_during_model_response_blocks_assembly(self):
        with TemporaryDirectory() as directory:
            workflow, _, robot, model = build_workflow(directory, (OfflineTaskKind.TASK2,))
            event = threading.Event()
            original = model.analyze_task2
            def analyze(*args, **kwargs):
                result = original(*args, **kwargs)
                event.set()
                return result
            def check():
                if event.is_set():
                    raise RuntimeError("reset")
            model.analyze_task2 = analyze
            workflow._check_cancelled = check
            with self.assertRaisesRegex(RuntimeError, "reset"):
                workflow.run()
            self.assertEqual(robot.actions, [])
            self.assertEqual(robot.finished, 0)

    def test_main_gui_builds_teaching_delta_progress_and_running_reset(self):
        root = tk.Tk()
        root.withdraw()
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        sessions = []

        class Session:
            def __init__(self, **kwargs):
                self.cancel_event = threading.Event()
                self.parameters = kwargs["robot_parameters"]
                self.report = kwargs["report"]
                self.stops = 0
                sessions.append(self)

            def create_session(self):
                return self

            def start(self):
                self.cancel_event.wait(1)

            def reset(self):
                self.stops += 1
                self.cancel_event.set()

            def robot_telemetry(self):
                return (ArcsRobotSnapshot("rob1", (0.0,) * 6,
                        (0.1, -0.2, 0.4, -3.139, 0.0, 0.0), ("ok",) * 6, False),
                        ArcsMotionSafetyStatus(True, True, True, False))

            def update_robot_parameters(self, overrides):
                return self.parameters.update_from_form(overrides)

        def descendants(widget):
            return [child for item in widget.winfo_children()
                    for child in [item, *descendants(item)]]

        with TemporaryDirectory() as directory, patch("assembly.competition_gui.OfflineCompetitionRunner", Session):
            app = CompetitionLauncherApp(root, Path(directory), memory_path=Path(directory) / "settings.json")
            app._show_runtime(OfflineLaunchSettings("key", "127.0.0.1", 7930, "127.0.0.1", 30004),
                              None, RememberedConnection("vm", 7930, "arcs", 30004), RobotGlobalParameters())
            buttons = {str(w.cget("text")): w for w in descendants(root) if isinstance(w, __import__("tkinter").ttk.Button)}
            self.assertIn("读取当前点位", buttons)
            self.assertIn("读取该位姿吸盘 TCP", buttons)
            self.assertIn("读取该位姿相机 TCP", buttons)
            self.assertNotIn("选择文件夹并导出日志", buttons)
            self.assertNotIn("全流程", buttons)
            self.assertNotIn("仅任务一", buttons)
            self.assertNotIn("仅任务二", buttons)
            command = next(w for w in descendants(root)
                           if isinstance(w, __import__("tkinter").ttk.Entry)
                           and str(w).endswith(".task_command_entry"))
            command.insert(0, "执行任务一")
            root.after(20, lambda: buttons["执行指令"].invoke())
            def reset():
                if str(buttons["重置"].cget("state")) == "disabled":
                    errors.append("运行时重置不可用")
                buttons["重置"].invoke()
            root.after(70, reset)
            root.after(350, root.quit)
            root.mainloop()
            self.assertEqual(sessions[0].stops, 1)
            self.assertGreaterEqual(len(sessions), 2)
            self.assertEqual(str(buttons["执行指令"].cget("state")), "normal")
            self.assertEqual(errors, [])
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()


if __name__ == "__main__":
    unittest.main()
