from __future__ import annotations

import ctypes
import json
import sys
import threading
import tkinter as tk
from dataclasses import asdict, fields
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from alignment_test import AlignmentTestConfig, AuboArcsClient, CameraAlignmentTest


PARAMETER_GROUPS = (
    ("连接与编号", (("vm_host", "VisionMaster IP"), ("vm_port", "VisionMaster 端口"),
                  ("arcs_host", "ARCS IP"), ("arcs_port", "ARCS 端口"),
                  ("robot_name", "机器人名称"), ("trigger", "物块11–16 / 托盘21–26"))),
    ("TCP-Delta", (("suction_tcp_x_mm", "吸盘对准 TCP X / mm"), ("suction_tcp_y_mm", "吸盘对准 TCP Y / mm"),
                   ("camera_tcp_x_mm", "相机对准 TCP X / mm"), ("camera_tcp_y_mm", "相机对准 TCP Y / mm"))),
    ("速度与视觉", (("velocity_m_s", "快速速度 / m/s"), ("acceleration_m_s2", "快速加速度 / m/s²"),
                     ("precision_velocity_m_s", "慢速速度 / m/s"), ("precision_acceleration_m_s2", "慢速加速度 / m/s²"),
                     ("vm_xy_scale_k", "VM X/Y 比例 k"), ("rz_sign", "RZ 方向"), ("rz_offset_deg", "RZ 偏置 / °"),
                     ("motion_timeout_s", "单动作超时 / s"), ("poll_interval_s", "轮询间隔 / s"),
                     ("max_planar_radius_mm", "工作半径及 Z 上限 / mm"))),
    ("高度与吸盘", (("block_pick_z_mm", "六色物块抓取 Z / mm"), ("block_place_z_mm", "六色物块放置 Z / mm"),
                     ("lift_distance_mm", "相对抬升 / mm"), ("tool_do_index", "工具 DO 索引"),
                     ("tool_do_active_high", "高电平有效 true/false"), ("suction_settle_s", "吸取等待 / s"),
                     ("release_settle_s", "释放等待 / s"))),
    ("叠放（暂不使用）", (("stack_z_mm", "叠放 Z / mm（仅记忆）"),)),
)


def load_config(path: Path) -> AlignmentTestConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = {field.name for field in fields(AlignmentTestConfig)}
        return AlignmentTestConfig(**{key: value for key, value in raw.items() if key in allowed})
    except (OSError, ValueError, TypeError):
        return AlignmentTestConfig()


def save_config(path: Path, config: AlignmentTestConfig) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class AlignmentTestApp:
    def __init__(self, root, memory_path=None):
        self.root = root
        self.memory_path = memory_path or Path(__file__).with_name("local_settings.json")
        config = load_config(self.memory_path)
        self.values = {name: tk.StringVar(value=str(value)) for name, value in asdict(config).items()}
        self.current = None
        self.worker = None
        self.busy = False
        self.resetting = False
        self.requires_stop = False
        root.title("线下赛 · 相机与吸盘对准测试")
        root.geometry("940x740")
        root.minsize(700, 500)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        main = ttk.Frame(root, padding=16)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="相机对准 → TCP-Delta → 吸盘对准",
                  font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w", pady=8)
        ttk.Label(main, text="XY 使用实际 TCP + k × VM 偏移；TCP-Delta 只有 X/Y，单位 mm。").pack(anchor="w")
        tabs = ttk.Notebook(main)
        tabs.pack(fill="both", expand=True, pady=12)
        self.delta = tk.StringVar()
        for group, definitions in PARAMETER_GROUPS:
            page = ttk.Frame(tabs, padding=12)
            tabs.add(page, text=group)
            if group == "TCP-Delta":
                for prefix, text in (("suction_tcp", "读取该位姿吸盘 TCP"), ("camera_tcp", "读取该位姿相机 TCP")):
                    ttk.Button(page, text=text, command=lambda p=prefix: self.capture(p)).pack(anchor="w", pady=4)
                ttk.Label(page, textvariable=self.delta).pack(anchor="w", pady=8)
            canvas = tk.Canvas(page, highlightthickness=0)
            scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
            form = ttk.Frame(canvas, padding=6)
            window = canvas.create_window(0, 0, window=form, anchor="nw")
            form.columnconfigure(1, weight=1)
            for row, (name, text) in enumerate(definitions):
                ttk.Label(form, text=text).grid(row=row, column=0, sticky="w", padx=6, pady=5)
                ttk.Entry(form, textvariable=self.values[name]).grid(row=row, column=1, sticky="ew", pady=5)
            form.bind("<Configure>", lambda _event, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda e, c=canvas, w=window: c.itemconfigure(w, width=e.width))
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(fill="both", expand=True)
        for name in ("suction_tcp_x_mm", "suction_tcp_y_mm", "camera_tcp_x_mm", "camera_tcp_y_mm"):
            self.values[name].trace_add("write", lambda *_: self.update_delta())
        self.update_delta()
        toolbar = ttk.Frame(main)
        toolbar.pack(fill="x", pady=6)
        self.buttons = []
        for action, text in (("align", "仅相机与吸盘对准"), ("pick", "物块抓取测试"), ("place", "托盘放置测试")):
            button = ttk.Button(toolbar, text=text, command=lambda a=action: self.start(a))
            button.pack(side="left", padx=3)
            self.buttons.append(button)
        ttk.Button(toolbar, text="应用并记忆", command=self.apply).pack(side="left", padx=3)
        self.reset_button = ttk.Button(toolbar, text="重置", command=self.reset)
        self.reset_button.pack(side="right")
        self.status = tk.StringVar(value="空闲：请先核对现场位置、TCP-Delta 和高度")
        ttk.Label(main, textvariable=self.status).pack(anchor="w", pady=6)
        self.console = scrolledtext.ScrolledText(main, height=7, state="disabled", wrap="word")
        self.console.pack(fill="x")

    def get_config(self):
        data = {}
        for name, default in asdict(AlignmentTestConfig()).items():
            raw = self.values[name].get().strip()
            if isinstance(default, bool):
                if raw.lower() not in {"true", "false"}:
                    raise ValueError("高电平有效只允许 true / false。")
                data[name] = raw.lower() == "true"
            else:
                data[name] = type(default)(raw)
        return AlignmentTestConfig(**data)

    def apply(self):
        try:
            config = self.get_config()
            save_config(self.memory_path, config)
            self.status.set("参数已记忆；下次测试生效，当前动作不变。")
            return config
        except (ValueError, OSError) as exc:
            messagebox.showerror("参数未保存", str(exc), parent=self.root)
            return None

    def update_delta(self):
        try:
            x = float(self.values["suction_tcp_x_mm"].get()) - float(self.values["camera_tcp_x_mm"].get())
            y = float(self.values["suction_tcp_y_mm"].get()) - float(self.values["camera_tcp_y_mm"].get())
            self.delta.set(f"TCP-Delta：X={x:.3f} mm    Y={y:.3f} mm")
        except ValueError:
            self.delta.set("TCP-Delta：请输入有效 X/Y")

    def report(self, text):
        def append():
            self.console.configure(state="normal")
            self.console.insert("end", text + "\n")
            self.console.see("end")
            self.console.configure(state="disabled")
        self.root.after(0, append)

    def capture(self, prefix):
        if self.busy or self.resetting or self.requires_stop:
            return
        config = self.apply()
        if config is None:
            return
        self.busy = True
        self.current = None
        self.set_buttons(False)
        def read():
            try:
                client = AuboArcsClient(config.arcs_host, config.arcs_port, config.robot_name)
                safety = client.get_motion_safety_status()
                if not safety.steady or safety.collision_occurred:
                    raise ValueError("请等待机器人静止且无碰撞后再读取。")
                tcp = client.get_tcp_pose()
                def finish():
                    if not self.resetting:
                        self.values[prefix + "_x_mm"].set(str(tcp[0] * 1000))
                        self.values[prefix + "_y_mm"].set(str(tcp[1] * 1000))
                        self.apply()
                    self.busy = False
                    self.set_buttons(True)
                self.root.after(0, finish)
            except Exception as exc:
                self.report(f"读取失败：{exc}")
                self.root.after(0, self.finish_worker)
        self.worker = threading.Thread(target=read, daemon=True)
        self.worker.start()

    def set_buttons(self, enabled):
        for button in self.buttons:
            button.configure(state="normal" if enabled and not self.resetting and not self.requires_stop else "disabled")

    def finish_worker(self):
        self.busy = False
        self.set_buttons(True)

    def start(self, action):
        if self.busy or self.resetting:
            return
        config = self.apply()
        if config is None:
            return
        self.current = CameraAlignmentTest(config, report=self.report)
        current = self.current
        self.requires_stop = True
        self.busy = True
        self.set_buttons(False)
        self.status.set("测试运行中")
        def worker():
            try:
                current.run(action)
                self.requires_stop = False
                self.report("测试完成。")
            except Exception as exc:
                self.report(f"测试停止：{exc}")
            finally:
                self.root.after(0, self.finish_worker)
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def reset(self):
        if self.resetting:
            return
        self.resetting = True
        self.set_buttons(False)
        self.reset_button.configure(state="disabled")
        self.status.set("正在停止并等待旧测试退出")
        if self.current is not None:
            self.current.cancelled.set()
        def worker():
            try:
                if self.requires_stop and self.current is not None:
                    self.current.reset()
                if self.worker is not None:
                    self.worker.join()
                def finish():
                    self.resetting = False
                    self.busy = False
                    self.current = None
                    self.requires_stop = False
                    self.set_buttons(True)
                    self.reset_button.configure(state="normal")
                    self.status.set("已重置，参数保留；请确认吸盘和现场物块状态")
                self.root.after(0, finish)
            except Exception as exc:
                self.report(f"停止未确认：{exc}")
                def failed():
                    self.resetting = False
                    self.reset_button.configure(state="normal")
                    self.status.set("请检查 ARCS 后再次重置")
                self.root.after(0, failed)
        threading.Thread(target=worker, daemon=True).start()


def launch():
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    root.tk.call("tk", "scaling", max(1.0, float(root.winfo_fpixels("1i")) / 72.0))
    AlignmentTestApp(root)
    root.mainloop()


if __name__ == "__main__":
    launch()
