'Tkinter desktop entry for the automatic online competition workflow.'

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .competition_clock import CompetitionClock
from .competition_runtime import CompetitionRunner
from .launch_settings import CompetitionLaunchSettings
from .qwen_vision import ModelStreamEvent
from .settings import load_competition_config


class CompetitionLauncherApp:
    def __init__(self, root: tk.Tk, project_root: Path) -> None:
        self._root = root
        self._project_root = project_root
        self._root.title("具身智能精密装配大赛")
        self._root.geometry("680x420")
        self._root.minsize(480, 300)
        self._root.resizable(True, True)
        ttk.Label(
            root,
            text="具身智能精密装配大赛",
            font=("Microsoft YaHei UI", 20, "bold"),
        ).pack(pady=(45, 12))
        ttk.Label(
            root,
            text="点击后填写本次比赛的临时连接信息；API 密钥不会写入磁盘。",
        ).pack(pady=(0, 25))
        ttk.Button(root, text="一键比赛", command=self._open_setup, width=24).pack()

    def _open_setup(self) -> None:
        dialog = tk.Toplevel(self._root)
        dialog.title("比赛前连接配置")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.geometry("640x360")
        dialog.minsize(520, 310)
        dialog.resizable(True, True)
        config = load_competition_config(
            self._project_root / "config" / "competition_config.toml"
        )
        fields = {
            "api_key": tk.StringVar(),
            "vision_host": tk.StringVar(value=config.visionmaster.tcp.host),
            "vision_port": tk.StringVar(value=str(config.visionmaster.tcp.port)),
            "arcs_host": tk.StringVar(value=config.aubo_arcs.host),
            "arcs_port": tk.StringVar(value=str(config.aubo_arcs.port)),
        }
        frame = ttk.Frame(dialog, padding=18)
        frame.grid(sticky="nsew")
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        ttk.Label(
            frame,
            text=(
                "宿主机运行 Python 与 VisionMaster：默认使用 VisionMaster 本机地址，无需另填宿主机端口。\n"
                "VMware 虚拟机 IP 与 ARCS IP 相同，只需填写一次。"
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self._entry(frame, 1, "百炼 API 密钥", fields["api_key"], show="*")
        self._entry(frame, 2, "VisionMaster IP（宿主机）", fields["vision_host"])
        self._entry(frame, 3, "VisionMaster 端口", fields["vision_port"])
        self._entry(frame, 4, "ARCS/VMware 虚拟机 IP", fields["arcs_host"])
        self._entry(frame, 5, "ARCS JSON-RPC 端口", fields["arcs_port"])
        ttk.Button(
            frame,
            text="确认并开始比赛",
            command=lambda: self._start(dialog, fields, config),
            width=22,
        ).grid(row=6, column=0, columnspan=2, pady=(14, 0))

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
        self, dialog: tk.Toplevel, fields: dict[str, tk.StringVar], config
    ) -> None:
        try:
            settings = CompetitionLaunchSettings.from_form(
                api_key=fields["api_key"].get(),
                visionmaster_host=fields["vision_host"].get(),
                visionmaster_port=fields["vision_port"].get(),
                arcs_host=fields["arcs_host"].get(),
                arcs_port=fields["arcs_port"].get(),
            )
        except ValueError as exc:
            messagebox.showerror("配置错误", str(exc), parent=dialog)
            return
        dialog.destroy()
        self._show_runtime(settings, settings.apply_to(config))

    def _show_runtime(self, settings: CompetitionLaunchSettings, config) -> None:
        for child in self._root.winfo_children():
            child.destroy()
        self._root.geometry("800x400")
        self._root.minsize(500, 250)
        self._root.resizable(True, True)

        header = ttk.Frame(self._root)
        header.pack(fill="x", padx=8, pady=(6, 3))
        ttk.Label(
            header, text="五分钟仿真演示", font=("Microsoft YaHei UI", 14, "bold")
        ).pack(side="left")
        countdown = tk.StringVar(value="05:00")
        ttk.Label(
            header,
            textvariable=countdown,
            font=("Consolas", 20, "bold"),
            foreground="#B00020",
        ).pack(side="right")
        pause_text = tk.StringVar(value="暂停计时")
        pause_button = ttk.Button(header, textvariable=pause_text, width=9)
        pause_button.pack(side="right", padx=(0, 8))

        body = ttk.Frame(self._root)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        sidebar = ttk.Frame(body, padding=(0, 0, 6, 0))
        sidebar.pack(side="left", fill="y")
        content = ttk.Frame(body)
        content.pack(side="left", fill="both", expand=True)

        display_frame = ttk.Frame(content)
        log_frame = ttk.Frame(content)
        for frame in (display_frame, log_frame):
            frame.grid(row=0, column=0, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        ttk.Button(
            sidebar,
            text="五分钟展示",
            width=11,
            command=display_frame.tkraise,
        ).pack(fill="x", pady=(0, 8))

        console = scrolledtext.ScrolledText(
            display_frame,
            state="disabled",
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            height=8,
        )
        console.pack(fill="both", expand=True)

        ttk.Label(
            log_frame,
            text="三份比赛日志预览",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        log_preview = scrolledtext.ScrolledText(
            log_frame,
            state="disabled",
            wrap="word",
            font=("Microsoft YaHei UI", 9),
        )
        log_preview.pack(fill="both", expand=True)

        clock = CompetitionClock.start()
        timer_finished = False

        def report(text: str) -> None:
            elapsed = clock.elapsed_seconds()
            timed_text = f"[{elapsed // 60}m{elapsed % 60}s] {text}"
            self._root.after(0, lambda: self._append(console, timed_text))

        stream_state = {
            "task1_reasoning": False,
            "task1_retry": False,
            "task1_reasoning_line_start": True,
        }

        def report_model_stream(event: ModelStreamEvent) -> None:
            if event.stage != "任务一":
                report(f"[{event.stage}][{event.kind}] {event.text}")
                return
            if event.kind == "推理":
                if not stream_state["task1_reasoning"]:
                    heading = (
                        "【任务一｜自动复核推理】"
                        if stream_state["task1_retry"]
                        else "【任务一｜千问视觉推理】"
                    )
                    elapsed = clock.elapsed_seconds()
                    self._root.after(
                        0,
                        lambda text=f"[{elapsed // 60}m{elapsed % 60}s] {heading}\n":
                        self._append_fragment(console, text),
                    )
                    stream_state["task1_reasoning"] = True
                    stream_state["task1_reasoning_line_start"] = True
                elapsed = clock.elapsed_seconds()
                timed_fragment, line_start = _format_timed_stream_fragment(
                    event.text,
                    elapsed,
                    stream_state["task1_reasoning_line_start"],
                )
                stream_state["task1_reasoning_line_start"] = line_start
                self._root.after(
                    0,
                    lambda text=timed_fragment: self._append_fragment(console, text),
                )
                return
            if stream_state["task1_reasoning"]:
                self._root.after(0, lambda: self._append_fragment(console, "\n\n"))
                stream_state["task1_reasoning"] = False
                stream_state["task1_reasoning_line_start"] = True
            if event.kind == "结果生成":
                return
            if event.kind == "校验" and "自动复核" in event.text:
                stream_state["task1_retry"] = True
            if event.kind == "最终输出":
                elapsed = clock.elapsed_seconds()
                self._root.after(
                    0,
                    lambda text=f"[{elapsed // 60}m{elapsed % 60}s] {event.text}\n":
                    self._append_fragment(console, text),
                )
                return
            report(f"[{event.stage}][{event.kind}] {event.text}")

        try:
            session = CompetitionRunner(
                settings=settings,
                config=config,
                runtime_root=self._project_root / "runtime" / "competition",
                report=report,
                model_stream_report=report_model_stream,
            ).create_session()
        except Exception as exc:
            report(f"初始化失败：{exc}")
            return

        def timeout() -> None:
            nonlocal timer_finished
            if timer_finished:
                return
            timer_finished = True
            pause_button.configure(state="disabled")
            report("五分钟演示计时结束，已禁止新的任务操作。")

            def stop_robot() -> None:
                try:
                    session.stop_for_timeout()
                except Exception as exc:
                    report(f"ARCS 停止请求结果：{exc}")

            threading.Thread(target=stop_robot, daemon=True).start()

        def toggle_timer() -> None:
            if timer_finished:
                return
            if clock.is_paused:
                clock.resume()
                pause_text.set("暂停计时")
                report("演示计时已继续。")
            else:
                clock.pause()
                pause_text.set("继续计时")
                report("演示计时已暂停。")

        pause_button.configure(command=toggle_timer)

        def update_clock() -> None:
            if timer_finished:
                return
            seconds = clock.remaining_seconds()
            countdown.set(f"{seconds // 60:02d}:{seconds % 60:02d}")
            if seconds == 0:
                timeout()
                return
            self._root.after(100, update_clock)

        def process() -> None:
            try:
                session.start()
                report("比赛流程已完成。")
            except Exception as exc:
                report(f"运行失败：{exc}")

        def export_logs() -> None:
            destination = filedialog.askdirectory(
                parent=self._root,
                title="选择三份比赛日志的保存文件夹",
                mustexist=True,
            )
            if not destination:
                return
            log_frame.tkraise()
            self._replace_text(log_preview, "正在读取ARCS原生记录并生成三份日志，请稍候……")

            def export() -> None:
                try:
                    result = session.export_logs(Path(destination))
                    previews = []
                    for path in (result.vm_path, result.llm_path, result.arcs_path):
                        previews.append(path.read_text(encoding="utf-8-sig"))
                    self._root.after(
                        0,
                        lambda: self._replace_text(
                            log_preview, "\n\n".join(previews)
                        ),
                    )
                    report(f"三份比赛日志已保存到：{destination}")
                except Exception as exc:
                    error_text = f"日志导出失败：{exc}"
                    report(error_text)
                    self._root.after(
                        0,
                        lambda text=error_text: self._replace_text(log_preview, text),
                    )

            threading.Thread(target=export, daemon=True).start()

        ttk.Button(
            sidebar,
            text="打印日志",
            width=11,
            command=export_logs,
        ).pack(fill="x")
        display_frame.tkraise()

        report("五分钟演示计时已启动，比赛流程自动开始。")
        update_clock()
        threading.Thread(target=process, daemon=True).start()

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


def launch_gui(project_root: Path | None = None) -> None:
    root = tk.Tk()
    CompetitionLauncherApp(root, project_root or Path.cwd())
    root.mainloop()


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
