from __future__ import annotations

import gc
import json
import threading
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import font, ttk
from unittest.mock import patch

from assembly.arcs_jsonrpc import ArcsMotionSafetyStatus, ArcsRobotSnapshot
from assembly.competition_gui import CompetitionLauncherApp, _enable_windows_high_dpi_awareness
from assembly.local_settings import LocalSettingsStore, RememberedConnection
from assembly.offline_launch_settings import OfflineLaunchSettings
from assembly.robot_parameters import RobotGlobalParameters


class PreviewSession:
    sessions = []

    def __init__(self, **kwargs):
        self.cancel_event = threading.Event()
        self.parameters = kwargs["robot_parameters"]
        self.progress = kwargs["progress"]
        self.report = kwargs["report"]
        self.sessions.append(self)

    def create_session(self):
        return self

    def start(self):
        for task in self.parameters.snapshot().task_sequence:
            for phase in ("photo", "capture", "recognize"):
                for status in ("运行中", "已完成"):
                    event = {"phase": phase, "task": task, "status": status}
                    self.progress(event)
            if task == "task2":
                steps = [
                    {"index": i, "block": a, "target": b, "destination_kind": "tray"}
                    for i, (a, b) in enumerate(
                        zip(("红色", "绿色", "橙色", "蓝色", "黄色", "紫色"),
                            ("黄色", "红色", "蓝色", "紫色", "橙色", "绿色")), 1)
                ]
                steps.append({"index": 7, "block": "粉色", "target": "红色", "destination_kind": "block"})
                self.progress({"phase": "plan", "steps": steps})
                for i in range(1, 7):
                    for action in self.parameters.snapshot().step_action_sequence:
                        self.progress({"phase": "step", "index": i, "action": action, "status": "已完成"})
                for action in ("locate_block", "pick", "stack"):
                    self.progress({"phase": "step", "index": 7, "action": action, "status": "已完成"})
                self.report("任务卡2装配内容：红色方块→黄色托盘；绿色方块→红色托盘……（界面测试数据）")
            else:
                self.report("任务卡1识别结果：电脑、齿轮、雨伞、螺丝刀、螺母、杯子（界面测试数据）")

    def reset(self):
        self.cancel_event.set()

    def robot_telemetry(self):
        return (ArcsRobotSnapshot("预览 · 无设备连接", (0.1, 0.2, 0.3, 0.4, 0.5, 0.6),
                (0.1, -0.2, 0.4, -3.139, 0, 0), ("ok",) * 6, False),
                ArcsMotionSafetyStatus(True, True, True, False))

    def update_robot_parameters(self, values):
        return self.parameters.update_from_form(values)


def descendants(widget):
    return [child for item in widget.winfo_children() for child in [item, *descendants(item)]]


def build_app(root, directory):
    app = CompetitionLauncherApp(root, Path(__file__).resolve().parents[1],
                                 memory_path=Path(directory) / "settings.json")
    app._show_runtime(OfflineLaunchSettings("test", "127.0.0.1", 7930, "127.0.0.1", 30004),
                      None, RememberedConnection("VM预览", 7930, "ARCS预览", 30004), RobotGlobalParameters())
    return app


class GuiDocument2Tests(unittest.TestCase):
    def setUp(self):
        gc.disable()
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.directory = TemporaryDirectory()
        self.fake = patch("assembly.competition_gui.OfflineCompetitionRunner", PreviewSession)
        self.fake.start()
        self.dialogs = patch("assembly.competition_gui.messagebox.showinfo", side_effect=lambda *args, **kwargs: self.errors.append(args))
        self.dialogs.start()
        self.network = patch("socket.create_connection", side_effect=AssertionError("UI 测试禁止连接设备"))
        self.network.start()
        self.app = build_app(self.root, self.directory.name)
        self.session = PreviewSession.sessions[-1]
        self.pump()

    def pump(self, milliseconds=250):
        self.root.after(milliseconds, self.root.quit)
        self.root.mainloop()

    def tearDown(self):
        self.pump()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.network.stop()
        self.fake.stop()
        self.dialogs.stop()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])
        PreviewSession.sessions.clear()
        del self.session, self.app, self.root
        gc.collect()
        gc.enable()

    def test_selected_task_updates_only_its_progress_table(self):
        tables = [w for w in descendants(self.root) if isinstance(w, ttk.Treeview) and w.exists("photo")]
        task1, task2 = tables
        for phase in ("photo", "capture", "recognize"):
            self.session.progress({"phase": phase, "task": "task2", "status": "运行中"})
            self.pump()
            self.assertEqual(task1.set(phase, "status"), "待执行")
            self.assertEqual(task2.set(phase, "status"), "● 运行中")
            self.session.progress({"phase": phase, "task": "task2", "status": "已完成"})
        self.pump()
        self.assertEqual(task2.set("photo", "status"), "✓ 已完成")
        self.assertEqual(task2.set("capture", "status"), "✓ 已完成")
        self.assertEqual(task2.set("recognize", "status"), "✓ 已完成")

    def test_completed_round_integrated_rows_and_reset(self):
        buttons = {w.cget("text"): w for w in descendants(self.root) if isinstance(w, ttk.Button)}
        command = next(w for w in descendants(self.root)
                       if isinstance(w, ttk.Entry) and str(w).endswith(".task_command_entry"))
        command.insert(0, "执行任务二")
        buttons["执行指令"].invoke()
        self.pump(250)
        table = next(w for w in descendants(self.root) if isinstance(w, ttk.Treeview) and w.exists("step_7"))
        self.assertEqual(len(table.get_children()), 10)
        self.assertEqual(table.set("step_7", "stack"), "✓ 已完成")
        self.assertEqual(table.set("step_7", "return_photo"), "—")
        self.assertEqual(table.set("step_7", "status"), "✓ 已完成")
        bar = next(w for w in descendants(self.root) if isinstance(w, ttk.Progressbar))
        self.assertEqual(float(bar.cget("value")), 100)
        buttons["重置"].invoke()
        self.pump(250)
        self.assertEqual(len(table.get_children()), 3)
        self.assertEqual(table.set("photo", "status"), "待执行")
        self.assertEqual(float(bar.cget("value")), 0)

    def test_completed_command_can_be_followed_by_another_command(self):
        buttons = {w.cget("text"): w for w in descendants(self.root) if isinstance(w, ttk.Button)}
        command = next(w for w in descendants(self.root)
                       if isinstance(w, ttk.Entry) and str(w).endswith(".task_command_entry"))
        command.insert(0, "执行任务一")
        buttons["执行指令"].invoke()
        self.pump(250)
        self.assertEqual(str(buttons["执行指令"].cget("state")), "normal")
        command.delete(0, "end")
        command.insert(0, "执行任务二")
        buttons["执行指令"].invoke()
        self.pump(250)
        self.assertGreaterEqual(len(PreviewSession.sessions), 2)
        self.assertEqual(PreviewSession.sessions[-1].parameters.snapshot().task_sequence, ("task2",))

    def test_read_save_teaching_and_parameter_memory(self):
        buttons = {w.cget("text"): w for w in descendants(self.root) if isinstance(w, ttk.Button)}
        buttons["读取当前点位"].invoke()
        self.pump(300)
        save_buttons = [w for w in descendants(self.root) if isinstance(w, ttk.Button)
                        and w.cget("text") == "保存当前点位到此拍照位"]
        save_buttons[0].invoke()
        self.pump()
        params = self.session.parameters.snapshot()
        self.assertAlmostEqual(params.task_card_photo_pose.x_mm, 100)
        self.assertAlmostEqual(params.task_card_photo_pose.joints_rad[0], 0.1)
        saved = json.loads((Path(self.directory.name) / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(float(saved["robot_parameters"]["task_card_photo_x_mm"]), 100)
        self.assertEqual(params.block_photo_pose, RobotGlobalParameters().block_photo_pose)
        buttons["读取该位姿吸盘 TCP"].invoke()
        self.pump(300)
        self.assertEqual(self.session.parameters.snapshot().suction_tcp_y_mm, -200)

    def test_tree_rows_scale_with_font_at_125_and_150_percent(self):
        for scaling in (1.6666667, 2.0):
            self.root.tk.call("tk", "scaling", scaling)
            self.app._configure_style()
            height = int(ttk.Style(self.root).lookup("Process.Treeview", "rowheight"))
            line = font.Font(root=self.root, family="Microsoft YaHei UI", size=10).metrics("linespace")
            self.assertGreaterEqual(height, int(line * 1.8))

    def test_result_console_and_save_button_survive_small_window(self):
        self.root.geometry("720x460")
        self.root.deiconify()
        self.pump()
        text = next(w for w in descendants(self.root) if isinstance(w, tk.Text))
        self.assertGreater(text.winfo_height(), 30)
        self.assertLessEqual(text.winfo_rooty() + text.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        notebook = next(w for w in self.root.winfo_children() if isinstance(w, ttk.Notebook))
        notebook.select(1)
        self.pump()
        save = next(w for w in descendants(self.root) if isinstance(w, ttk.Button)
                    and w.cget("text") == "应用全部参数并记忆")
        self.assertGreater(save.winfo_width(), 100)
        self.assertGreater(save.winfo_height(), 20)
        self.assertLessEqual(save.winfo_rooty() + save.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())

    def test_setup_only_requests_folder_not_filename_or_fixed_wait(self):
        self.app._open_setup()
        self.pump()
        labels = [str(w.cget("text")) for w in descendants(self.root) if isinstance(w, ttk.Label)]
        self.assertIn("VM 图片保存文件夹", labels)
        self.assertFalse(any("图片名" in label or "拍照等待时间" in label for label in labels))

    def test_obsolete_memory_fields_do_not_reset_saved_values(self):
        path = Path(self.directory.name) / "legacy.json"
        data = {"schema_version": 2, "connection": {
            "visionmaster_host": "192.168.4.5", "visionmaster_port": 8000,
            "arcs_host": "192.168.4.6", "arcs_port": 30004,
            "task_card_image_directory": "C:/Pictures/VM",
            "task_card1_image_filename": "", "task_card_capture_wait_s": "unused"},
            "robot_parameters": {"vm_xy_scale_k": "0.8", "block_pick_z_mm": "200",
                                 "task_card_photo_j1_deg": "-50"}}
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = LocalSettingsStore(path).load(RememberedConnection("default", 7930, "default", 30004),
                                              RobotGlobalParameters())
        self.assertEqual(loaded.connection.visionmaster_host, "192.168.4.5")
        self.assertEqual(loaded.robot_parameters.vm_xy_scale_k, 0.8)
        self.assertEqual(loaded.robot_parameters.block_pick_z_mm, 200)
        self.assertEqual(loaded.robot_parameters.task_card_photo_pose.joints_deg[0], -50)


def preview():
    _enable_windows_high_dpi_awareness()
    with TemporaryDirectory() as directory, patch("assembly.competition_gui.OfflineCompetitionRunner", PreviewSession), \
            patch("socket.create_connection", side_effect=AssertionError("预览禁止连接设备")):
        root = tk.Tk()
        build_app(root, directory)
        root.title("线下赛前端预览 - 无设备连接")
        root.after(600000, root.destroy)
        root.mainloop()


if __name__ == "__main__":
    unittest.main()
