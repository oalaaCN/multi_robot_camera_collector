from __future__ import annotations

import argparse
import io
import multiprocessing as mp
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from queue import Empty
from tkinter import BooleanVar, StringVar, filedialog, messagebox, ttk
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageOps

from camera_collector.config import AppConfig, RobotConfig, load_config
from camera_collector.ros_worker import run_camera_worker, serialize_robot


CAPTURE_LABELS = ["执行任务前", "执行任务中", "执行成功", "执行失败"]


class CameraCollectorApp:
    def __init__(self, root: tk.Tk, config: AppConfig, mock: bool) -> None:
        self.root = root
        self.config = config
        self.mock = BooleanVar(value=mock)
        self.robot_names = list(config.robots.keys())
        self.robot_var = StringVar(value=self.robot_names[0])
        self.active_robot_name = self.robot_names[0]
        self.robot_runtime_ips = {name: robot.ip for name, robot in config.robots.items()}
        self.local_runtime_ips = {name: robot.local_ros_ip for name, robot in config.robots.items()}
        self.local_ip_var = StringVar(value=config.robots[self.robot_names[0]].local_ros_ip)
        self.robot_ip_var = StringVar(value=config.robots[self.robot_names[0]].ip)
        self.save_dir_var = StringVar(value=str(config.save_dir))
        self.status_var = StringVar(value="请选择机器人并连接")

        self.ctx = mp.get_context("spawn")
        self.frame_queue: mp.Queue | None = None
        self.status_queue: mp.Queue | None = None
        self.stop_event: mp.Event | None = None
        self.worker: mp.Process | None = None

        self.latest_frames: dict[str, Image.Image] = {}
        self.latest_frame_times: dict[str, float] = {}
        self.rendered_image: Image.Image | None = None
        self.tk_image: object | None = None

        self._build_ui()
        self._on_robot_changed()
        self.root.after(self.config.refresh_ms, self._poll_worker)

    def _build_ui(self) -> None:
        self.root.title("Multi-Robot Camera Collector")
        self.root.geometry(f"{self.config.window_width}x{self.config.window_height}")
        self.root.minsize(1080, 680)
        self.root.configure(bg="#eef2f6")
        self._setup_style()

        shell = ttk.Frame(self.root, padding=14, style="App.TFrame")
        shell.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(shell, style="App.TFrame")
        header.pack(fill=tk.X, side=tk.TOP)
        ttk.Label(header, text="Multi-Robot Camera Collector", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.status_var, style="StatusPill.TLabel").pack(side=tk.RIGHT)

        top_bar = ttk.Frame(shell, padding=(14, 12), style="Panel.TFrame")
        top_bar.pack(fill=tk.X, side=tk.TOP, pady=(12, 10))
        top_bar.columnconfigure(1, weight=0)
        top_bar.columnconfigure(3, weight=0)
        top_bar.columnconfigure(5, weight=0)
        top_bar.columnconfigure(8, weight=1)

        ttk.Label(top_bar, text="机器人", style="Field.TLabel").grid(row=0, column=0, sticky=tk.W)
        robot_combo = ttk.Combobox(
            top_bar,
            textvariable=self.robot_var,
            values=self.robot_names,
            state="readonly",
            width=12,
        )
        robot_combo.grid(row=0, column=1, sticky=tk.W, padx=(8, 18))
        robot_combo.bind("<<ComboboxSelected>>", lambda _: self._on_robot_selected())

        ttk.Label(top_bar, text="本机 ROS_IP", style="Field.TLabel").grid(row=0, column=2, sticky=tk.W)
        ttk.Entry(top_bar, textvariable=self.local_ip_var, width=17).grid(row=0, column=3, sticky=tk.W, padx=(8, 18))

        ttk.Label(top_bar, text="机器人 IP", style="Field.TLabel").grid(row=0, column=4, sticky=tk.W)
        ttk.Entry(top_bar, textvariable=self.robot_ip_var, width=17).grid(row=0, column=5, sticky=tk.W, padx=(8, 18))

        ttk.Checkbutton(top_bar, text="模拟画面", variable=self.mock).grid(row=0, column=6, sticky=tk.W, padx=(0, 16))
        ttk.Button(top_bar, text="连接 / 重连", command=self.connect, style="Primary.TButton").grid(
            row=0, column=7, sticky=tk.EW, padx=(0, 8)
        )
        ttk.Button(top_bar, text="停止", command=self.stop_worker).grid(row=0, column=8, sticky=tk.W)

        save_bar = ttk.Frame(shell, padding=(14, 10), style="Panel.TFrame")
        save_bar.pack(fill=tk.X, pady=(0, 12), side=tk.TOP)
        save_bar.columnconfigure(1, weight=1)
        ttk.Label(save_bar, text="保存目录", style="Field.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(save_bar, textvariable=self.save_dir_var).grid(row=0, column=1, sticky=tk.EW, padx=(0, 8))
        ttk.Button(save_bar, text="选择", command=self._choose_save_dir).grid(row=0, column=2, sticky=tk.E)

        body = ttk.Frame(shell, style="App.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        preview_wrap = tk.Frame(body, bg="#101418", bd=0, highlightthickness=1, highlightbackground="#c9d3de")
        preview_wrap.grid(row=0, column=0, sticky=tk.NSEW)
        preview_wrap.rowconfigure(0, weight=1)
        preview_wrap.columnconfigure(0, weight=1)

        self.preview = tk.Label(preview_wrap, bg="#101418", bd=0)
        self.preview.grid(row=0, column=0, sticky=tk.NSEW)

        action_bar = ttk.Frame(body, width=230, padding=(14, 14), style="Panel.TFrame")
        action_bar.grid(row=0, column=1, sticky=tk.NS, padx=(12, 0))
        action_bar.grid_propagate(False)

        ttk.Label(action_bar, text="原始截图", style="Section.TLabel").pack(anchor=tk.W)
        ttk.Label(action_bar, text="保存最新 ROS camera 帧", style="Hint.TLabel").pack(anchor=tk.W, pady=(2, 12))
        for label in CAPTURE_LABELS:
            ttk.Button(
                action_bar,
                text=label,
                style="Capture.TButton",
                command=lambda capture_label=label: self.save_snapshot(capture_label),
            ).pack(fill=tk.X, pady=4)

        ttk.Separator(action_bar).pack(fill=tk.X, pady=(16, 12))
        ttk.Label(action_bar, text="当前话题", style="Section.TLabel").pack(anchor=tk.W)
        self.topic_list = tk.Text(action_bar, height=12, width=24, wrap=tk.WORD, bd=0, padx=10, pady=10)
        self.topic_list.configure(
            state=tk.DISABLED,
            bg="#f8fafc",
            fg="#334155",
            insertbackground="#334155",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#d7dee7",
        )
        self.topic_list.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        status = ttk.Label(shell, textvariable=self.status_var, anchor=tk.W, style="Footer.TLabel")
        status.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background="#eef2f6")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#eef2f6", foreground="#0f172a", font=("Segoe UI", 16, "bold"))
        style.configure("Field.TLabel", background="#ffffff", foreground="#475569", font=("Segoe UI", 10, "bold"))
        style.configure("Section.TLabel", background="#ffffff", foreground="#0f172a", font=("Segoe UI", 11, "bold"))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#64748b", font=("Segoe UI", 9))
        style.configure("StatusPill.TLabel", background="#dbeafe", foreground="#1e3a8a", padding=(10, 4))
        style.configure("Footer.TLabel", background="#eef2f6", foreground="#475569", font=("Segoe UI", 9))
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 6), font=("Segoe UI", 10))
        style.configure("Primary.TButton", padding=(12, 7), foreground="#ffffff", background="#2563eb")
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])
        style.configure("Capture.TButton", padding=(10, 8), font=("Segoe UI", 10, "bold"))
        style.configure("TEntry", padding=(6, 4))
        style.configure("TCombobox", padding=(6, 4))

    def _choose_save_dir(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.save_dir_var.get() or str(Path.cwd()))
        if directory:
            self.save_dir_var.set(directory)

    def _on_robot_changed(self) -> None:
        robot = self.current_robot
        self.local_ip_var.set(self.local_runtime_ips.get(robot.name, robot.local_ros_ip))
        self.robot_ip_var.set(self.robot_runtime_ips.get(robot.name, robot.ip))
        self._clear_frames()
        self._render_topics(robot)
        self.status_var.set(f"已选择 {robot.name}")

    def _on_robot_selected(self) -> None:
        was_running = self.worker is not None
        self.local_runtime_ips[self.active_robot_name] = self.local_ip_var.get().strip()
        self.robot_runtime_ips[self.active_robot_name] = self.robot_ip_var.get().strip()
        self.active_robot_name = self.robot_var.get()
        self._on_robot_changed()
        if was_running:
            self.connect()

    @property
    def current_robot(self) -> RobotConfig:
        return self.config.robots[self.robot_var.get()]

    def _render_topics(self, robot: RobotConfig) -> None:
        self.topic_list.configure(state=tk.NORMAL)
        self.topic_list.delete("1.0", tk.END)
        for camera in robot.cameras:
            self.topic_list.insert(tk.END, f"{camera.name}\n{camera.topic}\n\n")
        self.topic_list.configure(state=tk.DISABLED)

    def connect(self) -> None:
        robot = self.current_robot
        robot_ip = self.robot_ip_var.get().strip()
        local_ip = self.local_ip_var.get().strip()
        self.local_runtime_ips[robot.name] = local_ip
        self.robot_runtime_ips[robot.name] = robot_ip

        if robot.ros_version == "ros1" and not robot_ip and not self.mock.get():
            messagebox.showerror("缺少 IP", f"{robot.name} 是 ROS1，请先填写机器人 IP。")
            return
        if not local_ip:
            messagebox.showerror("缺少 ROS_IP", "请填写本机 ROS_IP。")
            return

        self.stop_worker()
        self._clear_frames()

        self.frame_queue = self.ctx.Queue(maxsize=32)
        self.status_queue = self.ctx.Queue(maxsize=32)
        self.stop_event = self.ctx.Event()
        self.worker = self.ctx.Process(
            target=run_camera_worker,
            args=(
                serialize_robot(robot),
                local_ip,
                robot_ip,
                self.frame_queue,
                self.status_queue,
                self.stop_event,
                self.mock.get(),
            ),
            daemon=True,
        )
        self.worker.start()

        if robot.ros_version == "ros1":
            master = (robot.ros_master_uri_template or "http://{robot_ip}:11311").format(robot_ip=robot_ip)
            self.status_var.set(f"正在连接 {robot.name}: ROS_IP={local_ip}, ROS_MASTER_URI={master}")
        else:
            self.status_var.set(f"正在连接 {robot.name}: ROS2, ROS_IP={local_ip}")

    def stop_worker(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=1.5)
            if self.worker.is_alive():
                self.worker.terminate()
                self.worker.join(timeout=1.0)
        self.worker = None
        self.stop_event = None
        self.frame_queue = None
        self.status_queue = None

    def _clear_frames(self) -> None:
        self.latest_frames.clear()
        self.latest_frame_times.clear()
        self.rendered_image = None

    def _poll_worker(self) -> None:
        self._drain_status_queue()
        self._drain_frame_queue()
        self._update_preview()
        self.root.after(self.config.refresh_ms, self._poll_worker)

    def _drain_status_queue(self) -> None:
        if self.status_queue is None:
            return
        while True:
            try:
                level, message = self.status_queue.get_nowait()
            except Empty:
                break
            prefix = "错误" if level == "error" else "提示"
            self.status_var.set(f"{prefix}: {message}")

    def _drain_frame_queue(self) -> None:
        if self.frame_queue is None:
            return
        while True:
            try:
                camera_key, timestamp, image_bytes = self.frame_queue.get_nowait()
            except Empty:
                break
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            self.latest_frames[camera_key] = image
            self.latest_frame_times[camera_key] = timestamp

    def _update_preview(self) -> None:
        width = max(self.preview.winfo_width(), 320)
        height = max(self.preview.winfo_height(), 240)
        composed = self._compose_current_view(width, height)
        self.rendered_image = composed
        self.tk_image = _image_to_tk(composed)
        self.preview.configure(image=self.tk_image)

    def _compose_current_view(self, width: int, height: int) -> Image.Image:
        robot = self.current_robot
        canvas = Image.new("RGB", (width, height), "#0b1015")
        gap = 8 if min(width, height) >= 520 else 4
        x0, y0, x1, y1 = gap, gap, max(width - gap, gap + 1), max(height - gap, gap + 1)
        inner_w = max(x1 - x0, 1)
        inner_h = max(y1 - y0, 1)

        if robot.layout == "single":
            camera = robot.cameras[0]
            self._paste_panel(canvas, (x0, y0, x1, y1), camera.key, camera.name)
        elif robot.layout == "top_two_bottom":
            top_h = y0 + (inner_h - gap) // 2
            bottom_y = top_h + gap
            mid_x = x0 + (inner_w - gap) // 2
            panels = [
                (robot.cameras[0], (x0, y0, x1, top_h)),
                (robot.cameras[1], (x0, bottom_y, mid_x, y1)),
                (robot.cameras[2], (mid_x + gap, bottom_y, x1, y1)),
            ]
            for camera, box in panels:
                self._paste_panel(canvas, box, camera.key, camera.name)
        elif robot.layout == "vertical_two":
            first_h = y0 + (inner_h - gap) // 2
            panels = [
                (robot.cameras[0], (x0, y0, x1, first_h)),
                (robot.cameras[1], (x0, first_h + gap, x1, y1)),
            ]
            for camera, box in panels:
                self._paste_panel(canvas, box, camera.key, camera.name)
        else:
            camera_count = max(len(robot.cameras), 1)
            available_h = max(inner_h - gap * (camera_count - 1), camera_count)
            panel_h = available_h // camera_count
            for index, camera in enumerate(robot.cameras):
                panel_y0 = y0 + index * (panel_h + gap)
                panel_y1 = y1 if index == camera_count - 1 else panel_y0 + panel_h
                self._paste_panel(canvas, (x0, panel_y0, x1, panel_y1), camera.key, camera.name)

        return canvas

    def _paste_panel(self, canvas: Image.Image, box: tuple[int, int, int, int], camera_key: str, title: str) -> None:
        x0, y0, x1, y1 = box
        panel_w = max(x1 - x0, 1)
        panel_h = max(y1 - y0, 1)
        header_h = 38
        inset = 8
        slot = Image.new("RGB", (panel_w, panel_h), "#111827")
        source = self.latest_frames.get(camera_key)

        if source is None:
            slot = self._placeholder(panel_w, panel_h, title)
        else:
            image_area = (max(panel_w - inset * 2, 1), max(panel_h - header_h - inset * 2, 1))
            fitted = ImageOps.contain(source, image_area, method=Image.Resampling.BILINEAR)
            px = (panel_w - fitted.width) // 2
            py = header_h + max((panel_h - header_h - fitted.height) // 2, inset)
            slot.paste(fitted, (px, py))

        draw = ImageDraw.Draw(slot)
        draw.rectangle((0, 0, panel_w - 1, panel_h - 1), outline="#263342", width=1)
        draw.rectangle((0, 0, panel_w, header_h), fill="#0f172a")
        draw.text((12, 10), title, fill="#f8fafc", font=_font(14))

        timestamp = self.latest_frame_times.get(camera_key)
        if timestamp is not None:
            age = max(time.time() - timestamp, 0)
            age_text = f"{age:0.1f}s"
            draw.rounded_rectangle((panel_w - 72, 8, panel_w - 12, 30), radius=10, fill="#1e293b")
            draw.text((panel_w - 59, 11), age_text, fill="#bfdbfe", font=_font(12))

        canvas.paste(slot, (x0, y0))

    def _placeholder(self, width: int, height: int, title: str) -> Image.Image:
        image = Image.new("RGB", (width, height), "#111827")
        draw = ImageDraw.Draw(image)
        message = "等待摄像头数据"
        draw.rectangle((0, 0, width, 38), fill="#0f172a")
        draw.text((12, 10), title, fill="#f8fafc", font=_font(14))
        draw.text((max(width // 2 - 72, 10), max(height // 2 - 14, 50)), message, fill="#94a3b8", font=_font(16))
        return image

    def save_snapshot(self, capture_label: str) -> None:
        robot = self.current_robot
        frames_to_save = [(camera, self.latest_frames.get(camera.key)) for camera in robot.cameras]
        frames_to_save = [(camera, image) for camera, image in frames_to_save if image is not None]

        if not frames_to_save:
            messagebox.showwarning("暂无图像", "还没有收到 ROS camera 帧，暂时无法截图。")
            return

        save_dir = Path(self.save_dir_var.get()).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        safe_label = _safe_filename(capture_label)
        safe_robot = _safe_filename(robot.name)
        saved_outputs: list[Path] = []
        for camera, image in frames_to_save:
            safe_camera = _safe_filename(camera.key)
            output = save_dir / f"{timestamp}_{safe_label}_{safe_robot}_{safe_camera}.png"
            image.save(output, "PNG")
            saved_outputs.append(output)

        if len(saved_outputs) == 1:
            self.status_var.set(f"已保存原始 ROS 帧: {saved_outputs[0]}")
        else:
            self.status_var.set(f"已保存 {len(saved_outputs)} 张原始 ROS 帧: {save_dir}")

    def close(self) -> None:
        self.stop_worker()
        self.root.destroy()


def _safe_filename(value: str) -> str:
    value = value.strip().replace(" ", "_")
    return re.sub(r'[\\/:*?"<>|]+', "_", value)


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _image_to_tk(image: Image.Image) -> object:
    from PIL import ImageTk

    return ImageTk.PhotoImage(image)


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_config = Path(__file__).resolve().parents[1] / "config.json"
    parser = argparse.ArgumentParser(description="Collect multi-robot ROS camera snapshots.")
    parser.add_argument("--config", type=Path, default=default_config, help="Path to config.yaml")
    parser.add_argument("--mock", action="store_true", help="Use generated test frames instead of ROS topics")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    config = load_config(args.config)
    mp.freeze_support()
    root = tk.Tk()
    CameraCollectorApp(root, config, mock=args.mock)
    root.mainloop()
