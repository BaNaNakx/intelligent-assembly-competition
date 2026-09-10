'Tkinter desktop entry for the offline competition workflow.'

from __future__ import annotations

import ctypes
import sys
import threading
import tkinter as tk
from collections.abc import Mapping
from math import degrees, radians
from pathlib import Path
from tkinter import font as tkfont, messagebox, scrolledtext, ttk

from .arcs_jsonrpc import ArcsMotionSafetyStatus, ArcsRobotSnapshot
from .competition_clock import CompetitionClock
from .competition_runtime import OfflineCompetitionRunner
from .local_settings import LocalSettingsStore, RememberedConnection
from .models import Color
from .offline_launch_settings import OfflineLaunchSettings
from .robot_parameters import (
    ROBOT_GLOBAL_PARAMETERS,
    RobotGlobalParameters,
    PhotoPoseParameters,
    RobotParameterStore,
)
from .settings import load_competition_config


def _enable_windows_high_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def _apply_tk_dpi_scaling(root: tk.Tk) -> None:
    pixels_per_inch = float(root.winfo_fpixels("1i"))
    root.tk.call("tk", "scaling", max(1.0, pixels_per_inch / 72.0))


def parse_task_command(command: str) -> tuple[str, ...]:
    commands = {
        "执行任务一": ("task1",),
        "执行任务二": ("task2",),
    }
    if command not in commands:
        raise ValueError("只支持“执行任务一”或“执行任务二”。")
    return commands[command]


def _format_saved_pose(pose: tuple[float, ...]) -> str:
    x, y, z, rx, ry, rz = pose
    return (
        f"X={x:.2f}  Y={y:.2f}  Z={z:.2f} mm  "
        f"RX={rx:.4f}  RY={ry:.4f}  RZ={rz:.4f} rad"
    )


def _photo_pose_fields(prefix: str) -> tuple[tuple[str, str], ...]:
    return (
        (f"{prefix}_x_mm", "X / mm"),
        (f"{prefix}_y_mm", "Y / mm"),
        (f"{prefix}_z_mm", "Z / mm"),
        (f"{prefix}_rx_rad", "RX / rad"),
        (f"{prefix}_ry_rad", "RY / rad"),
        (f"{prefix}_rz_rad", "RZ / rad"),
        *((f"{prefix}_j{index}_deg", f"J{index} / °") for index in range(1, 7)),
    )


PARAMETER_FIELD_GROUPS = (
    ("速度与高度", (
        ("transit_velocity_m_s", "快速直线速度 / m·s⁻¹"),
        ("transit_acceleration_m_s2", "快速直线加速度 / m·s⁻²"),
        ("precision_velocity_m_s", "慢速直线速度 / m·s⁻¹"),
        ("precision_acceleration_m_s2", "慢速直线加速度 / m·s⁻²"),
        ("joint_velocity_deg_s", "关节速度 / °·s⁻¹"),
        ("joint_acceleration_deg_s2", "关节加速度 / °·s⁻²"),
        ("block_pick_z_mm", "九色物块统一抓取高度 / mm"),
        ("block_place_z_mm", "九色物块统一放置高度 / mm"),
        ("stack_z_mm", "第七步叠放高度 / mm"),
    )),
    ("TCP 校准", (
        ("suction_tcp_x_mm", "吸盘 TCP X / mm"),
        ("suction_tcp_y_mm", "吸盘 TCP Y / mm"),
        ("camera_tcp_x_mm", "相机 TCP X / mm"),
        ("camera_tcp_y_mm", "相机 TCP Y / mm"),
        ("tool_do_index", "吸气工具 DO 编号"),
        ("tool_blow_do_index", "吹气工具 DO 编号"),
        ("tool_do_active_high", "DO 开启值（true / false）"),
        ("suction_settle_s", "吸气稳定时间 / s"),
        ("release_settle_s", "吹气释放时间 / s"),
    )),
    ("任务卡拍照位（共用）", _photo_pose_fields("task_card_photo")),
    ("物块区拍照位", _photo_pose_fields("block_photo")),
    ("托盘区拍照位", _photo_pose_fields("tray_photo")),
)


class CompetitionLauncherApp:
    def __init__(
        self,
        root: tk.Tk,
        project_root: Path,
        *,
        connection_defaults: Mapping[str, str | int] | None = None,
        robot_defaults: RobotGlobalParameters = ROBOT_GLOBAL_PARAMETERS,
        memory_path: Path | None = None,
    ) -> None:
        self._root = root
        self._project_root = project_root
        self._connection_defaults = dict(connection_defaults or {})
        self._robot_defaults = robot_defaults
        self._settings_store = LocalSettingsStore(
            memory_path or project_root / "runtime" / "local_settings.json"
        )
        self._configure_style()
        self._root.title("具身智能精密装配大赛")
        self._root.geometry("960x600")
        self._root.minsize(640, 400)
        self._root.resizable(True, True)
        shell = ttk.Frame(root, style="Surface.TFrame")
        shell.pack(fill="both", expand=True)
        hero = ttk.Frame(shell, style="Surface.TFrame", padding=(64, 48))
        hero.pack(fill="both", expand=True)
        content = ttk.Frame(hero, style="Surface.TFrame")
        content.place(relx=0.0, rely=0.5, anchor="w", relwidth=1.0)
        ttk.Label(
            content,
            text="榫卯团队 · 线下赛控制台",
            style="Accent.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            content,
            text="具身智能精密装配",
            style="Hero.TLabel",
        ).pack(anchor="w", pady=(18, 8))
        ttk.Label(
            content,
            text="连接 VisionMaster、千问与 AUBO ARCS，统一完成识别、规划和装配。",
            style="Body.TLabel",
            wraplength=760,
        ).pack(anchor="w")
        ttk.Separator(content).pack(fill="x", pady=28)
        ttk.Button(
            content,
            text="进入一键比赛",
            command=self._open_setup,
            style="Primary.TButton",
            width=20,
        ).pack(anchor="w")

    def _configure_style(self) -> None:
        self._root.configure(background="#F3F5F7")
        style = ttk.Style(self._root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("App.TFrame", background="#F3F5F7")
        style.configure("Surface.TFrame", background="#FFFFFF")
        style.configure(
            "Hero.TLabel",
            background="#FFFFFF",
            foreground="#202B33",
            font=("Microsoft YaHei UI", 26, "bold"),
        )
        style.configure(
            "Accent.TLabel",
            background="#FFFFFF",
            foreground="#D96520",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure(
            "Body.TLabel",
            background="#FFFFFF",
            foreground="#65727C",
        )
        style.configure(
            "Primary.TButton",
            background="#DF6D27",
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(16, 9),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#C95C1B"), ("disabled", "#C8CDD1")],
        )
        style.configure(
            "Danger.TButton",
            background="#B84A4A",
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(12, 7),
        )
        style.map(
            "Danger.TButton",
            background=[("disabled", "#D7DCE0"), ("active", "#9F3C3C")],
            foreground=[("disabled", "#8A949B")],
        )
        style.configure(
            "Topbar.TFrame",
            background="#25313A",
        )
        style.configure(
            "Topbar.TLabel",
            background="#25313A",
            foreground="#FFFFFF",
        )
        style.configure(
            "Sidebar.TButton",
            anchor="w",
            background="#EEF1F3",
            foreground="#44515A",
            borderwidth=0,
            padding=(12, 9),
        )
        style.map("Sidebar.TButton", background=[("active", "#E2E7EA")])
        style.configure(
            "SidebarActive.TButton",
            anchor="w",
            background="#FBEADD",
            foreground="#B94F12",
            borderwidth=0,
            padding=(12, 9),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("SidebarActive.TButton", background=[("active", "#F5DDCC")])
        style.configure("ParameterSidebar.TFrame", background="#EEF1F3")
        style.configure("Panel.TLabelframe", background="#FFFFFF")
        style.configure(
            "Panel.TLabelframe.Label",
            background="#FFFFFF",
            foreground="#25313A",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure("TNotebook", background="#F3F5F7", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8))
        line_height = tkfont.Font(root=self._root, family="Microsoft YaHei UI", size=10).metrics("linespace")
        style.configure("Process.Treeview", rowheight=max(32, int(line_height * 1.9)),
                        background="#FFFFFF", fieldbackground="#FFFFFF", borderwidth=0)
        style.configure("Process.Treeview.Heading", padding=(10, 10),
                        background="#E8EDF1", foreground="#25313A",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Run.Horizontal.TProgressbar", background="#2E8B62",
                        troughcolor="#E5ECE7", borderwidth=0)

    def _open_setup(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("比赛前连接配置")
        dialog.configure(background="#F3F5F7")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.geometry("760x650")
        dialog.minsize(620, 580)
        dialog.resizable(True, True)
        config = load_competition_config(
            self._project_root / "config" / "competition_config.toml"
        )
        default_connection = RememberedConnection(
            str(
                self._connection_defaults.get(
                    "visionmaster_host", config.visionmaster.tcp.host
                )
            ),
            int(
                self._connection_defaults.get(
                    "visionmaster_port", config.visionmaster.tcp.port
                )
            ),
            str(self._connection_defaults.get("arcs_host", config.aubo_arcs.host)),
            int(self._connection_defaults.get("arcs_port", config.aubo_arcs.port)),
            str(
                self._connection_defaults.get(
                    "task_card_image_directory",
                    config.visionmaster.tcp.task_card_image_directory,
                )
            ),
        )
        remembered = self._settings_store.load(default_connection, self._robot_defaults)
        values = {
            "api_key": tk.StringVar(),
            "vision_host": tk.StringVar(value=remembered.connection.visionmaster_host),
            "vision_port": tk.StringVar(value=str(remembered.connection.visionmaster_port)),
            "arcs_host": tk.StringVar(value=remembered.connection.arcs_host),
            "arcs_port": tk.StringVar(value=str(remembered.connection.arcs_port)),
            "image_directory": tk.StringVar(
                value=remembered.connection.task_card_image_directory
            ),
        }
        frame = ttk.Frame(dialog, padding=28, style="Surface.TFrame")
        frame.grid(sticky="nsew", padx=18, pady=18)
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        ttk.Label(
            frame,
            text="连接比赛设备",
            style="Hero.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(
            frame,
            text=(
                "Python 与 VisionMaster 使用 TCP 通信；ARCS 地址直接指向实体机械臂控制器。\n"
                "API 密钥仅保存在内存中，不写入磁盘。"
            ),
            justify="left",
            style="Body.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 18))
        self._entry(frame, 2, "百炼 API 密钥", values["api_key"], show="*")
        self._entry(frame, 3, "VisionMaster IP（宿主机）", values["vision_host"])
        self._entry(frame, 4, "VisionMaster 端口", values["vision_port"])
        self._entry(frame, 5, "ARCS/实体机械臂控制器 IP", values["arcs_host"])
        self._entry(frame, 6, "ARCS JSON-RPC 端口", values["arcs_port"])
        self._entry(frame, 7, "VM 图片保存文件夹", values["image_directory"])
        ttk.Button(
            frame,
            text="确认并进入运行窗口",
            command=lambda: self._start(
                dialog,
                values,
                config,
                remembered.robot_parameters,
            ),
            width=22,
            style="Primary.TButton",
        ).grid(row=11, column=0, columnspan=2, sticky="e", pady=(22, 0))

    @staticmethod
    def _entry(
        frame: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        show: str | None = None,
    ) -> None:
        ttk.Label(frame, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=5
        )
        ttk.Entry(frame, textvariable=variable, width=42, show=show or "").grid(
            row=row, column=1, pady=5, sticky="ew"
        )

    def _start(
        self,
        dialog: tk.Toplevel,
        values: dict[str, tk.StringVar],
        config,
        robot_parameters: RobotGlobalParameters,
    ) -> None:
        try:
            settings = OfflineLaunchSettings.from_form(
                api_key=values["api_key"].get(),
                visionmaster_host=values["vision_host"].get(),
                visionmaster_port=values["vision_port"].get(),
                arcs_host=values["arcs_host"].get(),
                arcs_port=values["arcs_port"].get(),
                task_card_image_directory=values["image_directory"].get(),
            )
        except ValueError as exc:
            messagebox.showerror("配置错误", str(exc), parent=dialog)
            return
        connection = RememberedConnection(
            settings.visionmaster_host,
            settings.visionmaster_port,
            settings.arcs_host,
            settings.arcs_port,
            settings.task_card_image_directory,
        )
        try:
            self._settings_store.save(connection, robot_parameters)
        except OSError as exc:
            messagebox.showwarning(
                "配置记忆失败",
                f"本次仍可继续运行，但无法记住配置：{exc}",
                parent=dialog,
            )
        dialog.destroy()
        self._show_runtime(
            settings,
            settings.apply_to(config),
            connection,
            robot_parameters,
        )

    def _show_runtime(
        self,
        settings: OfflineLaunchSettings,
        config,
        connection: RememberedConnection,
        initial_robot_parameters: RobotGlobalParameters,
    ) -> None:
        for child in self._root.winfo_children():
            child.destroy()
        self._root.configure(background="#F3F5F7")
        self._root.geometry("1180x760")
        self._root.minsize(720, 460)
        self._root.resizable(True, True)

        header = ttk.Frame(self._root, style="Topbar.TFrame", padding=(18, 13))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="榫卯团队 · 线下赛运行中心",
            style="Topbar.TLabel",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left")
        ttk.Label(
            header,
            text=(
                f"VM  {connection.visionmaster_host}:{connection.visionmaster_port}    "
                f"ARCS  {connection.arcs_host}:{connection.arcs_port}"
            ),
            style="Topbar.TLabel",
        ).pack(side="right")

        notebook = ttk.Notebook(self._root)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        display_frame = ttk.Frame(notebook, padding=10, style="App.TFrame")
        monitor_frame = ttk.Frame(notebook, padding=10, style="App.TFrame")
        notebook.add(display_frame, text="主操作")
        notebook.add(monitor_frame, text="机械臂调试")

        toolbar = ttk.Frame(display_frame)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="文本指令", style="Body.TLabel").pack(side="left", padx=(0, 8))
        task_command = tk.StringVar()
        command_entry = ttk.Entry(
            toolbar,
            name="task_command_entry",
            textvariable=task_command,
            width=28,
        )
        command_entry.pack(side="left", fill="x", expand=True)
        execute_button = ttk.Button(
            toolbar,
            text="执行指令",
            width=12,
            style="Primary.TButton",
        )
        execute_button.pack(side="left", padx=(8, 0))
        reset_button = ttk.Button(
            toolbar,
            text="重置",
            width=12,
            style="Danger.TButton",
        )
        reset_button.pack(side="left", padx=(6, 0))
        task_status = tk.StringVar(value="空闲")
        state_label = ttk.Label(display_frame, textvariable=task_status, style="Body.TLabel")
        state_label.pack(fill="x", pady=(4, 2))
        console = scrolledtext.ScrolledText(
            display_frame,
            state="disabled",
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            background="#FFFFFF",
            foreground="#26323A",
            insertbackground="#26323A",
            relief="flat",
            borderwidth=1,
        )
        progress_value = tk.DoubleVar(value=0)
        progress_row = ttk.Frame(display_frame, style="App.TFrame")
        progress_row.pack(fill="x", pady=6)
        progress_text = tk.StringVar(value="0%")
        progress_value.trace_add("write", lambda *_: progress_text.set(f"{progress_value.get():.0f}%"))
        ttk.Label(progress_row, textvariable=progress_text, width=5, anchor="e", style="Body.TLabel").pack(side="right")
        ttk.Progressbar(progress_row, variable=progress_value, maximum=100,
                        style="Run.Horizontal.TProgressbar").pack(side="left", fill="x", expand=True, padx=(0, 8))
        tables = ttk.Notebook(display_frame)
        tables.pack(fill="both", expand=True)
        task_tables = {}
        task_pages = {}
        task_titles = {"task1": "任务一 · 物品识别", "task2": "任务二 · 装配过程"}
        step_columns = ("status", "block", "target", "locate_block", "pick",
                        "locate_tray", "place", "stack", "return_photo")
        phases = (("photo", "移动到共用任务卡拍照位"),
                  ("capture", "VM 拍照确认 0011 · 读取最新图片"),
                  ("recognize", "千问识别任务内容"))
        for task in ("task1", "task2"):
            page = ttk.Frame(tables, style="Surface.TFrame")
            page.rowconfigure(0, weight=1)
            page.columnconfigure(0, weight=1)
            columns = ("status",) if task == "task1" else step_columns
            table = ttk.Treeview(page, columns=columns, show="tree headings",
                                 height=9, style="Process.Treeview")
            table.heading("#0", text="步骤 / 关键过程")
            table_font = tkfont.Font(root=self._root, family="Microsoft YaHei UI", size=10)
            label_width = max(280, int(table_font.measure(phases[1][1]) + 24))
            table.column("#0", width=label_width, minwidth=label_width, stretch=True)
            headers = dict(zip(step_columns, ("状态", "待装配物块", "装配目标", "寻找物块", "抓取",
                                              "寻找托盘", "放置托盘", "叠放物块", "返回物块区")))
            for name in columns:
                table.heading(name, text=headers[name])
                width = max(84, table_font.measure(headers[name]) + 24, table_font.measure("✓ 已完成") + 24)
                table.column(name, width=width, minwidth=width, anchor="center", stretch=False)
            table.tag_configure("运行中", background="#FFF2D9", foreground="#87520D")
            table.tag_configure("已完成", background="#EDF8F1", foreground="#23613F")
            table.tag_configure("失败", background="#FDECEA", foreground="#A33232")
            table.grid(row=0, column=0, sticky="nsew")
            vertical = ttk.Scrollbar(page, orient="vertical", command=table.yview)
            horizontal = ttk.Scrollbar(page, orient="horizontal", command=table.xview)
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            for phase, label in phases:
                table.insert("", "end", iid=phase, text=label,
                             values=("待执行", *("—",) * (len(columns) - 1)))
            task_tables[task], task_pages[task] = table, page
            tables.add(page, text=task_titles[task])
        step_table = task_tables["task2"]
        console.configure(height=4)
        console.pack(side="bottom", fill="x", pady=(8, 0), before=tables)
        progress_done = set()
        step_expected_actions = {}
        current_capture = {"task": "task1", "display": "task1", "phases": {}}
        status_text = {"待执行": "待执行", "运行中": "● 运行中", "已完成": "✓ 已完成", "失败": "✕ 失败"}

        def set_phase(table, phase, status):
            table.set(phase, "status", status_text.get(status, status))
            table.item(phase, tags=(status,))

        def update_progress(event: dict) -> None:
            phase, status = event["phase"], event.get("status", "")
            names = {"photo": "移动到任务卡拍照位", "capture": "等待 VM 确认并读取最新图片",
                     "recognize": "千问识别", "plan": "装配方案已生成",
                     "step": "装配动作", "task_done": "任务完成"}
            if phase == "placement_pose":
                placement_pose_table.set(
                    event["color"], "pose1", _format_saved_pose(event["pose1"])
                )
                placement_pose_table.set(
                    event["color"], "pose2", _format_saved_pose(event["pose2"])
                )
                task_status.set(f'{event["display_color"]}物块装配位姿已记录')
                return
            task_status.set(f'{names.get(phase, phase)} · {status}')
            target = parameter_store.snapshot().task_sequence
            if phase in ("photo", "capture", "recognize"):
                selected_task = event.get("task")
                if selected_task not in task_titles:
                    raise ValueError("进度事件缺少有效任务类型。")
                current_capture.update(task=selected_task, display=selected_task)
                tables.select(task_pages[selected_task])
                current_capture["phases"][phase] = status
                table = task_tables[selected_task]
                for name, value in current_capture["phases"].items():
                    set_phase(table, name, value)
                    if value == "已完成":
                        progress_done.add((selected_task, name))
            elif phase == "plan":
                tables.select(task_pages["task2"])
                for step in event["steps"]:
                    item = f'step_{step["index"]}'
                    is_stack = step.get("destination_kind", "tray") == "block"
                    expected = (
                        ("locate_block", "pick", "stack")
                        if is_stack
                        else parameter_store.snapshot().step_action_sequence
                    )
                    step_expected_actions[item] = expected
                    action_values = {
                        action: ("待执行" if action in expected else "—")
                        for action in step_columns[3:]
                    }
                    step_table.insert("", "end", iid=item,
                                      text=f'步骤 {step["index"]}',
                                      values=(
                                          "待执行",
                                          f'{step["block"]}物块',
                                          f'{step.get("target", step.get("tray", ""))}'
                                          f'{"物块" if is_stack else "托盘"}',
                                          *(action_values[action] for action in step_columns[3:]),
                                      ))
            elif phase == "step":
                item = f'step_{event["index"]}'
                if step_table.exists(item):
                    step_table.set(item, event["action"], status_text.get(status, status))
                    completed = all(step_table.set(item, action) == "✓ 已完成"
                                    for action in step_expected_actions[item])
                    row_status = "已完成" if completed else ("失败" if status == "失败" else "运行中")
                    step_table.set(item, "status", status_text[row_status])
                    step_table.item(item, tags=(row_status,))
                    step_table.see(item)
                if status == "已完成":
                    progress_done.add(("step", item, event["action"]))
            total = 3 * len(target) + (
                6 * len(parameter_store.snapshot().step_action_sequence) + 3
                if "task2" in target else 0
            )
            progress_value.set(min(100, 100 * len(progress_done) / total))

        clock_holder = {"clock": CompetitionClock.start()}
        clock_holder["clock"].pause()
        run_state = {"started": False, "running": False, "failed": False, "resetting": False,
                     "generation": 0, "requires_stop": False, "needs_new_session": False}

        def report(text: str) -> None:
            elapsed = clock_holder["clock"].elapsed_seconds()
            timed_text = f"[{elapsed // 60}m{elapsed % 60}s] {text}"
            self._root.after(0, lambda: self._append(console, timed_text))

        parameter_store = RobotParameterStore(initial_robot_parameters)

        def create_session():
            generation = run_state["generation"]

            def current_report(text):
                elapsed = clock_holder["clock"].elapsed_seconds()
                timed_text = f"[{elapsed // 60}m{elapsed % 60}s] {text}"
                self._root.after(0, lambda: self._append(console, timed_text)
                                 if generation == run_state["generation"] else None)

            def current_progress(event):
                self._root.after(0, lambda: update_progress(event)
                                 if generation == run_state["generation"] else None)

            return OfflineCompetitionRunner(
                settings=settings,
                config=config,
                runtime_root=self._project_root / "runtime" / "competition",
                report=current_report,
                robot_parameters=parameter_store,
                progress=current_progress,
            ).create_session()

        try:
            session_holder = {"session": create_session()}
        except Exception as exc:
            report(f"初始化失败：{exc}")
            execute_button.configure(state="disabled")
            reset_button.configure(state="normal")
            return

        status_values = {
            "connection": tk.StringVar(value="正在连接 ARCS……"),
            "safety": tk.StringVar(value="安全状态：--"),
            "motion": tk.StringVar(value="运动状态：--"),
            "joints": tk.StringVar(value="关节角：--"),
            "pose": tk.StringVar(value="TCP 位姿：--"),
            "joint_states": tk.StringVar(value="关节状态：--"),
        }
        connection_bar = ttk.Frame(monitor_frame, style="App.TFrame")
        connection_bar.pack(fill="x", pady=(0, 6))
        ttk.Label(connection_bar, textvariable=status_values["connection"], style="Body.TLabel").pack(anchor="w")
        ttk.Label(connection_bar, textvariable=status_values["safety"], style="Body.TLabel").pack(anchor="w")

        teach_snapshot = {"snapshot": None}
        teach_message = tk.StringVar(value="先读取当前点位，再保存到对应拍照位")
        parameter_box = ttk.Frame(monitor_frame, style="Surface.TFrame", padding=8)
        parameter_box.pack(fill="both", expand=True)
        parameter_values = parameter_store.form_values()
        parameter_values["joint_velocity_deg_s"] = f"{degrees(initial_robot_parameters.joint_velocity_rad_s):g}"
        parameter_values["joint_acceleration_deg_s2"] = f"{degrees(initial_robot_parameters.joint_acceleration_rad_s2):g}"
        parameter_variables = {name: tk.StringVar(value=value) for name, value in parameter_values.items()}
        parameter_tabs = ttk.Notebook(parameter_box)
        parameter_tabs.pack(fill="both", expand=True)
        for group_name, field_definitions in PARAMETER_FIELD_GROUPS[:2]:
            page = ttk.Frame(parameter_tabs, padding=10, style="Surface.TFrame")
            parameter_tabs.add(page, text=group_name)
            if group_name == "TCP 校准":
                actions = ttk.Frame(page, style="Surface.TFrame")
                actions.pack(fill="x", pady=(0, 8))
                for prefix, label in (("suction_tcp", "读取该位姿吸盘 TCP"), ("camera_tcp", "读取该位姿相机 TCP")):
                    ttk.Button(actions, text=label, command=lambda name=prefix: capture_pose(name)).pack(side="left", padx=3)
                delta_label = tk.StringVar()
                ttk.Label(page, text="TCP-Delta = 吸盘 − 相机（只计算 X / Y）", style="Accent.TLabel").pack(anchor="w", pady=6)
                ttk.Label(page, textvariable=delta_label, style="Body.TLabel").pack(anchor="w", pady=6)
            self._build_parameter_tab(page, field_definitions, parameter_variables)

        photo_page = ttk.Frame(parameter_tabs, padding=8, style="Surface.TFrame")
        parameter_tabs.add(photo_page, text="拍照点位")
        photo_columns = ttk.Panedwindow(photo_page, orient="horizontal")
        photo_columns.pack(fill="both", expand=True)
        current_panel = ttk.Frame(photo_columns, padding=(0, 0, 10, 0), style="Surface.TFrame")
        saved_panel = ttk.Frame(photo_columns, style="Surface.TFrame")
        photo_columns.add(current_panel, weight=1)
        photo_columns.add(saved_panel, weight=2)
        current_row = ttk.Frame(current_panel, style="Surface.TFrame")
        current_row.pack(fill="x")
        ttk.Button(current_row, text="读取当前点位", command=lambda: capture_pose("current")).pack(side="left")
        teach_notice = ttk.Label(current_panel, textvariable=teach_message, style="Body.TLabel", wraplength=280)
        teach_notice.pack(fill="x", pady=6)
        current_joints = ttk.Treeview(current_panel, columns=("rad", "deg"), show="tree headings",
                                     height=6, style="Process.Treeview")
        current_joints.heading("#0", text="关节")
        current_joints.heading("rad", text="弧度 / rad")
        current_joints.heading("deg", text="角度 / °")
        current_joints.column("#0", width=50, minwidth=45, stretch=False)
        current_joints.column("rad", width=110, minwidth=80, anchor="center")
        current_joints.column("deg", width=100, minwidth=70, anchor="center")
        for index in range(1, 7):
            current_joints.insert("", "end", iid=str(index), text=f"J{index}", values=("—", "—"))
        current_joints.pack(fill="x", pady=8)
        photo_tabs = ttk.Notebook(saved_panel)
        photo_tabs.pack(fill="both", expand=True)
        photo_snapshots = []
        for group_name, field_definitions in PARAMETER_FIELD_GROUPS[2:]:
            prefix = field_definitions[0][0].removesuffix("_x_mm")
            page = ttk.Frame(photo_tabs, padding=8, style="Surface.TFrame")
            photo_tabs.add(page, text=group_name)
            ttk.Button(page, text="保存当前点位到此拍照位",
                       command=lambda name=prefix: save_teach_pose(name)).pack(anchor="w", pady=4)
            snapshot_text = tk.StringVar()
            snapshot_label = ttk.Label(page, textvariable=snapshot_text, style="Body.TLabel", wraplength=400)
            snapshot_label.pack(fill="x", pady=6)
            page.bind("<Configure>", lambda event, label=snapshot_label: label.configure(wraplength=max(160, event.width - 25)))
            photo_snapshots.append((prefix, snapshot_text))
            self._build_parameter_tab(page, field_definitions[6:], parameter_variables)

        def update_photo_snapshots(*_args):
            for prefix, text in photo_snapshots:
                coordinates = "  ".join(f"{axis.upper()}={parameter_variables[f'{prefix}_{axis}_mm'].get()}"
                                        for axis in ("x", "y", "z"))
                angles = "  ".join(f"{axis.upper()}={parameter_variables[f'{prefix}_{axis}_rad'].get()}"
                                  for axis in ("rx", "ry", "rz"))
                text.set(f"TCP 读取快照（只读）\n{coordinates} mm\n{angles} rad\nJ1–J6：可修改的运动目标")
        for prefix, _ in photo_snapshots:
            for name, _ in _photo_pose_fields(prefix)[:6]:
                parameter_variables[name].trace_add("write", update_photo_snapshots)
        update_photo_snapshots()

        placement_page = ttk.Frame(parameter_tabs, padding=8, style="Surface.TFrame")
        parameter_tabs.add(placement_page, text="装配位姿")
        ttk.Label(
            placement_page,
            text="前六步放置时自动记录位姿1；位姿2保持X/Y/RX/RY/RZ不变，Z自动增加100 mm。",
            style="Body.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        placement_table_frame = ttk.Frame(placement_page, style="Surface.TFrame")
        placement_table_frame.pack(fill="both", expand=True)
        placement_table_frame.rowconfigure(0, weight=1)
        placement_table_frame.columnconfigure(0, weight=1)
        placement_pose_table = ttk.Treeview(
            placement_table_frame,
            columns=("pose1", "pose2"),
            show="tree headings",
            height=9,
            style="Process.Treeview",
        )
        placement_pose_table.heading("#0", text="物块颜色")
        placement_pose_table.heading("pose1", text="各色物块装配位姿1")
        placement_pose_table.heading("pose2", text="各色物块装配位姿2")
        placement_pose_table.column("#0", width=90, minwidth=80, stretch=False)
        placement_pose_table.column("pose1", width=420, minwidth=300, stretch=True)
        placement_pose_table.column("pose2", width=420, minwidth=300, stretch=True)
        for color in Color:
            placement_pose_table.insert(
                "", "end", iid=color.value, text=color.display_name, values=("—", "—")
            )
        placement_pose_table.grid(row=0, column=0, sticky="nsew")
        placement_scroll = ttk.Scrollbar(
            placement_table_frame, orient="horizontal", command=placement_pose_table.xview
        )
        placement_scroll.grid(row=1, column=0, sticky="ew")
        placement_pose_table.configure(xscrollcommand=placement_scroll.set)

        parameter_actions = ttk.Frame(parameter_box, style="Surface.TFrame")
        parameter_actions.pack(side="bottom", fill="x", pady=(8, 0), before=parameter_tabs)
        parameter_message = tk.StringVar(value="已载入本机记忆参数。")
        parameter_notice = ttk.Label(parameter_actions, textvariable=parameter_message, style="Body.TLabel", wraplength=500)
        parameter_notice.pack(side="left", fill="x", expand=True)

        def mark_parameters_dirty(*_args) -> None:
            parameter_message.set("参数有未应用修改。")

        for variable in parameter_variables.values():
            variable.trace_add("write", mark_parameters_dirty)

        def update_delta(*_args):
            try:
                dx = float(parameter_variables["suction_tcp_x_mm"].get()) - float(parameter_variables["camera_tcp_x_mm"].get())
                dy = float(parameter_variables["suction_tcp_y_mm"].get()) - float(parameter_variables["camera_tcp_y_mm"].get())
                delta_label.set(f"TCP-Delta（吸盘 − 相机）：X = {dx:.3f} mm    Y = {dy:.3f} mm")
            except ValueError:
                delta_label.set("TCP-Delta：请输入有效的 X/Y")
        for name in ("suction_tcp_x_mm", "suction_tcp_y_mm", "camera_tcp_x_mm", "camera_tcp_y_mm"):
            parameter_variables[name].trace_add("write", update_delta)
        update_delta()

        def save_teach_pose(prefix):
            if run_state["running"] or run_state["resetting"]:
                messagebox.showinfo("请先停止", "运行时不能重新示教拍照位。", parent=self._root)
                return
            snapshot = teach_snapshot["snapshot"]
            if snapshot is None:
                messagebox.showinfo("请先读取", "先点击“读取当前点位”，再保存到所选拍照位。", parent=self._root)
                return
            tcp = snapshot.tool_pose_m_rad
            pose = PhotoPoseParameters(*(value * 1000 for value in tcp[:3]),
                                       *tcp[3:], tuple(degrees(q) for q in snapshot.joint_positions_rad))
            overrides = pose.to_form_values(prefix)
            try:
                updated = parameter_store.update_from_form(overrides)
                self._settings_store.save(connection, updated)
                for name, value in overrides.items():
                    parameter_variables[name].set(value)
                parameter_message.set("已将读取点位保存并记忆到所选拍照位。")
            except (ValueError, OSError) as exc:
                messagebox.showerror("保存失败", str(exc), parent=self._root)

        def capture_pose(prefix):
            if run_state["running"] or run_state["resetting"]:
                messagebox.showinfo("请先停止", "只允许在流程停止且机器人静止时记录位姿。", parent=self._root)
                return
            session = session_holder["session"]
            generation = run_state["generation"]

            def read():
                try:
                    snapshot, safety = session.robot_telemetry()
                    if not safety.steady or safety.collision_occurred:
                        raise ValueError("机器人未静止或有碰撞状态，未记录坐标。")
                    tcp = snapshot.tool_pose_m_rad
                    if prefix == "current":
                        def remember():
                            if generation != run_state["generation"] or run_state["running"]:
                                return
                            teach_snapshot["snapshot"] = snapshot
                            teach_message.set("已读取当前 TCP 和六轴关节角，可保存到三个拍照位")
                            for index, angle in enumerate(snapshot.joint_positions_rad, 1):
                                current_joints.item(str(index), values=(f"{angle:.8f}", f"{degrees(angle):.4f}"))
                        self._root.after(0, remember)
                        return
                    if prefix in ("suction_tcp", "camera_tcp"):
                        overrides = {f"{prefix}_x_mm": str(tcp[0] * 1000),
                                     f"{prefix}_y_mm": str(tcp[1] * 1000)}
                    else:
                        pose = PhotoPoseParameters(*(value * 1000 for value in tcp[:3]),
                                                   *tcp[3:], tuple(degrees(q) for q in snapshot.joint_positions_rad))
                        overrides = pose.to_form_values(prefix)

                    def save():
                        if generation != run_state["generation"] or run_state["running"]:
                            return
                        try:
                            updated = parameter_store.update_from_form(overrides)
                            self._settings_store.save(connection, updated)
                            for name, value in overrides.items():
                                parameter_variables[name].set(value)
                            parameter_message.set("当前静止位姿已读取并记忆。")
                        except (ValueError, OSError) as exc:
                            messagebox.showerror("保存失败", str(exc), parent=self._root)
                    self._root.after(0, save)
                except Exception as exc:
                    self._root.after(0, lambda message=str(exc): messagebox.showerror("读取失败", message, parent=self._root))
            threading.Thread(target=read, daemon=True).start()

        def apply_parameters() -> None:
            overrides = {
                name: variable.get() for name, variable in parameter_variables.items()
            }
            try:
                overrides["joint_velocity_rad_s"] = str(radians(float(overrides.pop("joint_velocity_deg_s"))))
                overrides["joint_acceleration_rad_s2"] = str(radians(float(overrides.pop("joint_acceleration_deg_s2"))))
                updated = session_holder["session"].update_robot_parameters(overrides)
            except ValueError as exc:
                messagebox.showerror("参数错误", str(exc), parent=self._root)
                parameter_message.set(f"参数未保存：{exc}")
                return
            try:
                self._settings_store.save(connection, updated)
            except OSError as exc:
                messagebox.showwarning(
                    "参数记忆失败",
                    f"参数已用于本次运行，但无法写入本机记忆文件：{exc}",
                    parent=self._root,
                )
            normalized = updated.to_form_values()
            for name, value in normalized.items():
                parameter_variables[name].set(value)
            parameter_variables["joint_velocity_deg_s"].set(f"{degrees(updated.joint_velocity_rad_s):g}")
            parameter_variables["joint_acceleration_deg_s2"].set(f"{degrees(updated.joint_acceleration_rad_s2):g}")
            parameter_message.set(
                "参数已记忆：速度从下一指令生效；坐标/高度从下一抓取或放置动作生效。"
            )
            report("机械臂全局参数已更新，已下发的动作保持不变。")

        ttk.Button(
            parameter_actions,
            text="应用全部参数并记忆",
            command=apply_parameters,
            width=20,
            style="Primary.TButton",
        ).pack(side="right", padx=(8, 0))

        def mark_failed() -> None:
            run_state["running"] = False
            run_state["failed"] = True
            task_status.set("运行失败，可重置")
            for table in task_tables.values():
                for item in table.get_children():
                    if "运行中" in table.item(item, "tags"):
                        for column in table["columns"]:
                            if table.set(item, column) == "● 运行中":
                                table.set(item, column, "✕ 失败")
                        table.item(item, tags=("失败",))
            reset_button.configure(state="normal")

        worker_holder = {"thread": None}

        def process(session) -> None:
            try:
                session.start()
                if session is not session_holder["session"] or run_state["resetting"]:
                    return
                report("比赛流程已完成。")

                def mark_completed() -> None:
                    if session is not session_holder["session"] or run_state["resetting"]:
                        return
                    run_state.update(started=False, running=False, requires_stop=False,
                                     needs_new_session=True)
                    task_status.set("任务已完成")
                    execute_button.configure(state="normal")
                    command_entry.configure(state="normal")

                self._root.after(0, mark_completed)
            except Exception as exc:
                if session is session_holder["session"] and not run_state["resetting"]:
                    report(f"运行失败：{exc}")
                    self._root.after(0, lambda: mark_failed() if session is session_holder["session"] and not run_state["resetting"] else None)

        def prepare_task_view(task: str) -> None:
            progress_done.clear()
            step_expected_actions.clear()
            current_capture.update(task=task, display=task, phases={})
            progress_value.set(0)
            for task_name, table in task_tables.items():
                tables.tab(task_pages[task_name], text=task_titles[task_name])
                for item in table.get_children():
                    if item.startswith("step_"):
                        table.delete(item)
                    else:
                        set_phase(table, item, "待执行")
            if task == "task2":
                for item in placement_pose_table.get_children():
                    placement_pose_table.item(item, values=("—", "—"))
            tables.select(task_pages[task])

        def start_process(tasks: tuple[str, ...]) -> None:
            if run_state["started"] or run_state["running"]:
                return
            if run_state["resetting"]:
                return
            if run_state["needs_new_session"]:
                previous_generation = run_state["generation"]
                run_state["generation"] += 1
                try:
                    session_holder["session"] = create_session()
                except Exception as exc:
                    run_state["generation"] = previous_generation
                    messagebox.showerror("启动失败", str(exc), parent=self._root)
                    return
                run_state["needs_new_session"] = False
            parameter_store.update_from_form({"task_sequence": ",".join(tasks)})
            parameter_variables["task_sequence"].set(",".join(tasks))
            prepare_task_view(tasks[0])
            run_state.update(started=True, running=True, failed=False, requires_stop=True)
            clock_holder["clock"] = CompetitionClock.start()
            execute_button.configure(state="disabled")
            command_entry.configure(state="disabled")
            task_status.set("任务运行中")
            report(f"{task_command.get()}已启动。")
            worker_holder["thread"] = threading.Thread(
                target=process,
                args=(session_holder["session"],),
                daemon=True,
            )
            worker_holder["thread"].start()

        def submit_command() -> None:
            try:
                tasks = parse_task_command(task_command.get())
            except ValueError as exc:
                task_status.set("指令错误")
                messagebox.showerror("指令错误", str(exc), parent=self._root)
                return
            report(f"收到文本指令：{task_command.get()}")
            start_process(tasks)

        execute_button.configure(command=submit_command)
        command_entry.bind("<Return>", lambda _event: submit_command())

        def reset_after_failure() -> None:
            if run_state["resetting"]:
                return
            run_state["resetting"] = True
            run_state["generation"] += 1
            session = session_holder["session"]
            session.cancel_event.set()
            reset_button.configure(state="disabled")
            execute_button.configure(state="disabled")
            command_entry.configure(state="disabled")
            task_status.set("正在停止并等待旧流程退出……")

            def reset_worker():
                try:
                    if run_state["requires_stop"]:
                        session.reset()
                    thread = worker_holder["thread"]
                    if thread is not None:
                        thread.join()
                    self._root.after(0, finish_reset)
                except Exception as exc:
                    def failed(message=str(exc)):
                        run_state["resetting"] = False
                        task_status.set("停止未确认，请检查 ARCS 后重试重置")
                        reset_button.configure(state="normal")
                        report(message)
                    self._root.after(0, failed)

            def finish_reset():
                try:
                    session_holder["session"] = create_session()
                except Exception as exc:
                    run_state["resetting"] = False
                    reset_button.configure(state="normal")
                    report(f"重置失败：{exc}")
                    return
                run_state.update(started=False, running=False, failed=False, resetting=False,
                                 requires_stop=False, needs_new_session=False)
                clock_holder["clock"] = CompetitionClock.start()
                clock_holder["clock"].pause()
                self._replace_text(console, "")
                progress_done.clear()
                step_expected_actions.clear()
                current_capture.update(task="task1", display="task1", phases={})
                progress_value.set(0)
                for task, table in task_tables.items():
                    tables.tab(task_pages[task], text=task_titles[task])
                    for item in table.get_children():
                        if item.startswith("step_"):
                            table.delete(item)
                        else:
                            set_phase(table, item, "待执行")
                for item in placement_pose_table.get_children():
                    placement_pose_table.item(item, values=("—", "—"))
                task_status.set("空闲")
                execute_button.configure(state="normal")
                command_entry.configure(state="normal")
                reset_button.configure(state="normal")
                report("已重置；参数保留。请确认物块和吸盘实际状态后重新开始。")

            threading.Thread(target=reset_worker, daemon=True).start()

        reset_button.configure(command=reset_after_failure)

        def poll_robot() -> None:
            def read() -> None:
                try:
                    snapshot, safety = session_holder["session"].robot_telemetry()
                    values = _format_robot_telemetry(snapshot, safety)
                except Exception as exc:
                    values = _robot_telemetry_error(str(exc))

                def finish() -> None:
                    for key, value in values.items():
                        status_values[key].set(value)
                    self._root.after(1000, poll_robot)

                self._root.after(0, finish)

            threading.Thread(target=read, daemon=True).start()

        report("连接配置已载入；请输入“执行任务一”或“执行任务二”。")
        poll_robot()
        notebook.select(display_frame)

    @staticmethod
    def _build_parameter_tab(
        tab: ttk.Frame,
        field_definitions: tuple[tuple[str, str], ...],
        variables: dict[str, tk.StringVar],
        readonly_names: set[str] = frozenset(),
    ) -> None:
        canvas = tk.Canvas(
            tab,
            highlightthickness=0,
            background="#FFFFFF",
        )
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas, padding=12, style="Surface.TFrame")
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        form.columnconfigure(1, weight=1)
        for row, (name, label) in enumerate(field_definitions):
            ttk.Label(form, text=label, style="Body.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=4
            )
            ttk.Entry(form, textvariable=variables[name], width=18,
                      state="readonly" if name in readonly_names else "normal").grid(
                row=row, column=1, sticky="ew", pady=4, ipady=3
            )
        form.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window, width=event.width),
        )

    @staticmethod
    def _append(console: scrolledtext.ScrolledText, text: str) -> None:
        console.configure(state="normal")
        console.insert("end", text + "\n")
        console.see("end")
        console.configure(state="disabled")

    @staticmethod
    def _append_fragment(console: scrolledtext.ScrolledText, text: str) -> None:
        console.configure(state="normal")
        console.insert("end", text)
        console.see("end")
        console.configure(state="disabled")

    @staticmethod
    def _replace_text(console: scrolledtext.ScrolledText, text: str) -> None:
        console.configure(state="normal")
        console.delete("1.0", "end")
        console.insert("end", text)
        console.configure(state="disabled")


def launch_gui(
    project_root: Path | None = None,
    *,
    connection_defaults: Mapping[str, str | int] | None = None,
    robot_defaults: RobotGlobalParameters = ROBOT_GLOBAL_PARAMETERS,
    memory_path: Path | None = None,
) -> None:
    _enable_windows_high_dpi_awareness()
    root = tk.Tk()
    _apply_tk_dpi_scaling(root)
    CompetitionLauncherApp(
        root,
        project_root or Path.cwd(),
        connection_defaults=connection_defaults,
        robot_defaults=robot_defaults,
        memory_path=memory_path,
    )
    root.mainloop()


def editable_parameter_names() -> tuple[str, ...]:
    return tuple(
        name
        for _, field_definitions in PARAMETER_FIELD_GROUPS
        for name, _ in field_definitions
    )


def _format_robot_telemetry(
    snapshot: ArcsRobotSnapshot,
    safety: ArcsMotionSafetyStatus,
) -> dict[str, str]:
    joints = "  ".join(
        f"J{index}={degrees(value):.2f}°"
        for index, value in enumerate(snapshot.joint_positions_rad, start=1)
    )
    x, y, z, rx, ry, rz = snapshot.tool_pose_m_rad
    pose = (
        f"X={x * 1000:.2f} mm  Y={y * 1000:.2f} mm  Z={z * 1000:.2f} mm  "
        f"RX={degrees(rx):.2f}°  RY={degrees(ry):.2f}°  RZ={degrees(rz):.2f}°"
    )
    collision = safety.collision_occurred or snapshot.collision_occurred
    return {
        "connection": f"ARCS 已连接：{snapshot.robot_name}",
        "safety": (
            f"安全状态：{'已上电' if safety.powered_on else '未上电'} / "
            f"{'限位正常' if safety.within_safety_limits else '超出安全限位'} / "
            f"{'检测到碰撞' if collision else '无碰撞'}"
        ),
        "motion": f"运动状态：{'静止' if safety.steady else '运动中'}",
        "joints": f"关节角：{joints}",
        "pose": f"TCP 位姿：{pose}",
        "joint_states": f"关节状态：{' / '.join(snapshot.joint_states)}",
    }


def _robot_telemetry_error(message: str) -> dict[str, str]:
    return {
        "connection": f"ARCS 状态读取失败：{message}",
        "safety": "安全状态：--",
        "motion": "运动状态：--",
        "joints": "关节角：--",
        "pose": "TCP 位姿：--",
        "joint_states": "关节状态：--",
    }


def _format_timed_stream_fragment(
    text: str,
    elapsed_seconds: int,
    at_line_start: bool,
) -> tuple[str, bool]:
    prefix = f"[{elapsed_seconds // 60}m{elapsed_seconds % 60}s] "
    output: list[str] = []
    for segment in text.splitlines(keepends=True):
        if at_line_start and segment.rstrip("\r\n"):
            output.append(prefix)
        output.append(segment)
        at_line_start = segment.endswith(("\r", "\n"))
    return "".join(output), at_line_start
