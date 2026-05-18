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
        self.local_ip_var = StringVar(value=config.default_local_ros_ip)
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
        self.root.minsize(960, 640)

        shell = ttk.Frame(self.root, padding=10)
        shell.pack(fill=tk.BOTH, expand=True)

        top_bar = ttk.Frame(shell)
        top_bar.pack(fill=tk.X, side=tk.TOP)

        ttk.Label(top_bar, text="机器人").pack(side=tk.LEFT)
        robot_combo = ttk.Combobox(
            top_bar,
            textvariable=self.robot_var,
            values=self.robot_names,
            state="readonly",
            width=10,
        )
        robot_combo.pack(side=tk.LEFT, padx=(6, 14))
        robot_combo.bind("<<ComboboxSelected>>", lambda _: self._on_robot_selected())

        ttk.Label(top_bar, text="本机 ROS_IP").pack(side=tk.LEFT)
        ttk.Entry(top_bar, textvariable=self.local_ip_var, width=16).pack(side=tk.LEFT, padx=(6, 14))

        ttk.Label(top_bar, text="机器人 IP").pack(side=tk.LEFT)
        ttk.Entry(top_bar, textvariable=self.robot_ip_var, width=16).pack(side=tk.LEFT, padx=(6, 14))

        ttk.Checkbutton(top_bar, text="模拟画面", variable=self.mock).pack(side=tk.LEFT, padx=(0, 14))
        ttk.Button(top_bar, text="连接 / 重连", command=self.connect).pack(side=tk.LEFT)
        ttk.Button(top_bar, text="停止", command=self.stop_worker).pack(side=tk.LEFT, padx=(8, 0))

        save_bar = ttk.Frame(shell)
        save_bar.pack(fill=tk.X, pady=(8, 10), side=tk.TOP)
        ttk.Label(save_bar, text="保存目录").pack(side=tk.LEFT)
        ttk.Entry(save_bar, textvariable=self.save_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 8))
        ttk.Button(save_bar, text="选择", command=self._choose_save_dir).pack(side=tk.LEFT)

        body = ttk.Frame(shell)
        body.pack(fill=tk.BOTH, expand=True)

        self.preview = tk.Label(body, bg="#101418", bd=0)
        self.preview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        action_bar = ttk.Frame(body, width=160)
        action_bar.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        action_bar.pack_propagate(False)

        ttk.Label(action_bar, text="保存当前画面").pack(anchor=tk.W, pady=(0, 8))
        for label in CAPTURE_LABELS:
            ttk.Button(
                action_bar,
                text=label,
                command=lambda capture_label=label: self.save_snapshot(capture_label),
            ).pack(fill=tk.X, pady=5)

        ttk.Label(action_bar, text="当前话题").pack(anchor=tk.W, pady=(18, 4))
        self.topic_list = tk.Text(action_bar, height=10, width=20, wrap=tk.WORD)
        self.topic_list.configure(state=tk.DISABLED)
        self.topic_list.pack(fill=tk.X)

        status = ttk.Label(shell, textvariable=self.status_var, anchor=tk.W)
        status.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _choose_save_dir(self) -> None:
        directory = filedialog.askdirectory(initialdir=self.save_dir_var.get() or str(Path.cwd()))
        if directory:
            self.save_dir_var.set(directory)

    def _on_robot_changed(self) -> None:
        robot = self.current_robot
        self.robot_ip_var.set(self.robot_runtime_ips.get(robot.name, robot.ip))
        self._clear_frames()
        self._render_topics(robot)
        self.status_var.set(f"已选择 {robot.name}")

    def _on_robot_selected(self) -> None:
        was_running = self.worker is not None
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
                camera_key, timestamp, jpeg = self.frame_queue.get_nowait()
            except Empty:
                break
            image = Image.open(io.BytesIO(jpeg)).convert("RGB")
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
        canvas = Image.new("RGB", (width, height), "#101418")

        if robot.layout == "single":
            camera = robot.cameras[0]
            self._paste_panel(canvas, (0, 0, width, height), camera.key, camera.name)
        elif robot.layout == "top_two_bottom":
            top_h = height // 2
            bottom_h = height - top_h
            panels = [
                (robot.cameras[0], (0, 0, width, top_h)),
                (robot.cameras[1], (0, top_h, width // 2, height)),
                (robot.cameras[2], (width // 2, top_h, width, height)),
            ]
            for camera, box in panels:
                self._paste_panel(canvas, box, camera.key, camera.name)
        elif robot.layout == "vertical_two":
            first_h = height // 2
            panels = [
                (robot.cameras[0], (0, 0, width, first_h)),
                (robot.cameras[1], (0, first_h, width, height)),
            ]
            for camera, box in panels:
                self._paste_panel(canvas, box, camera.key, camera.name)
        else:
            panel_h = height // max(len(robot.cameras), 1)
            for index, camera in enumerate(robot.cameras):
                self._paste_panel(canvas, (0, index * panel_h, width, (index + 1) * panel_h), camera.key, camera.name)

        return canvas

    def _paste_panel(self, canvas: Image.Image, box: tuple[int, int, int, int], camera_key: str, title: str) -> None:
        x0, y0, x1, y1 = box
        panel_w = max(x1 - x0, 1)
        panel_h = max(y1 - y0, 1)
        slot = Image.new("RGB", (panel_w, panel_h), "#151b21")
        source = self.latest_frames.get(camera_key)

        if source is None:
            slot = self._placeholder(panel_w, panel_h, title)
        else:
            fitted = ImageOps.contain(source, (panel_w, panel_h), method=Image.Resampling.BILINEAR)
            px = (panel_w - fitted.width) // 2
            py = (panel_h - fitted.height) // 2
            slot.paste(fitted, (px, py))

        draw = ImageDraw.Draw(slot)
        draw.rectangle((0, 0, panel_w - 1, panel_h - 1), outline="#33404a", width=2)
        draw.rectangle((0, 0, panel_w, 34), fill="#101418")
        draw.text((12, 9), title, fill="#f2f5f7", font=_font(14))

        timestamp = self.latest_frame_times.get(camera_key)
        if timestamp is not None:
            age = max(time.time() - timestamp, 0)
            draw.text((panel_w - 86, 9), f"{age:0.1f}s", fill="#9fb0bd", font=_font(13))

        canvas.paste(slot, (x0, y0))

    def _placeholder(self, width: int, height: int, title: str) -> Image.Image:
        image = Image.new("RGB", (width, height), "#151b21")
        draw = ImageDraw.Draw(image)
        message = "等待摄像头数据"
        draw.text((max(width // 2 - 72, 10), max(height // 2 - 14, 46)), message, fill="#8b98a5", font=_font(16))
        draw.text((12, max(height - 30, 40)), title, fill="#596874", font=_font(12))
        return image

    def save_snapshot(self, capture_label: str) -> None:
        image = self.rendered_image or self._compose_current_view(
            max(self.preview.winfo_width(), 960),
            max(self.preview.winfo_height(), 540),
        )
        save_dir = Path(self.save_dir_var.get()).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)

        robot_name = self.current_robot.name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        safe_label = _safe_filename(capture_label)
        safe_robot = _safe_filename(robot_name)
        output = save_dir / f"{timestamp}_{safe_label}_{safe_robot}.jpg"
        image.save(output, "JPEG", quality=95)
        self.status_var.set(f"已保存: {output}")

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
