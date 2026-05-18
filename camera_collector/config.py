from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CameraConfig:
    key: str
    name: str
    topic: str


@dataclass(frozen=True)
class RobotConfig:
    name: str
    ros_version: str
    ip: str
    layout: str
    cameras: list[CameraConfig]
    ros_master_uri_template: str | None = None


@dataclass(frozen=True)
class AppConfig:
    save_dir: Path
    default_local_ros_ip: str
    window_width: int
    window_height: int
    refresh_ms: int
    robots: dict[str, RobotConfig]


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as f:
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ModuleNotFoundError as exc:
                raise RuntimeError("YAML config needs PyYAML. Install it or use config.json.") from exc
            raw: dict[str, Any] = yaml.safe_load(f)
        else:
            raw = json.load(f)

    base_dir = path.parent
    save_dir = Path(raw.get("save_dir", "captured_images"))
    if not save_dir.is_absolute():
        save_dir = base_dir / save_dir

    window = raw.get("window", {})
    robots: dict[str, RobotConfig] = {}
    for robot_name, robot_raw in raw["robots"].items():
        cameras = [
            CameraConfig(
                key=str(camera["key"]),
                name=str(camera.get("name", camera["key"])),
                topic=str(camera["topic"]),
            )
            for camera in robot_raw["cameras"]
        ]
        robots[robot_name] = RobotConfig(
            name=robot_name,
            ros_version=str(robot_raw["ros_version"]).lower(),
            ip=str(robot_raw.get("ip") or ""),
            layout=str(robot_raw["layout"]),
            cameras=cameras,
            ros_master_uri_template=robot_raw.get("ros_master_uri_template"),
        )

    return AppConfig(
        save_dir=save_dir,
        default_local_ros_ip=str(raw.get("default_local_ros_ip", "")),
        window_width=int(window.get("width", 1280)),
        window_height=int(window.get("height", 820)),
        refresh_ms=int(window.get("refresh_ms", 40)),
        robots=robots,
    )
