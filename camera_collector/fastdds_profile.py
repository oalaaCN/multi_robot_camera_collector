from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from xml.sax.saxutils import escape


def configure_fastdds_profile(
    local_ip: str,
    robot_ip: str = "",
    *,
    validate_local_ip: bool = True,
    output_dir: Path | None = None,
) -> Path:
    local_ip = local_ip.strip()
    robot_ip = robot_ip.strip()
    if not local_ip:
        raise RuntimeError("ROS2 needs a local interface IP for the Fast DDS whitelist.")

    if validate_local_ip:
        assigned_ips = _local_ipv4_addresses()
        if assigned_ips and local_ip not in assigned_ips:
            available = ", ".join(assigned_ips)
            raise RuntimeError(
                f"Fast DDS whitelist IP {local_ip} is not assigned to this computer. "
                f"Available IPv4 addresses: {available}"
            )

    profile_text = _render_profile(local_ip, robot_ip)
    profile_path = _write_profile(profile_text, output_dir)
    os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(profile_path)
    return profile_path


def _write_profile(profile_text: str, output_dir: Path | None) -> Path:
    candidate_dirs = [output_dir] if output_dir else []
    if not candidate_dirs:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            candidate_dirs.append(Path(runtime_dir))
        candidate_dirs.append(Path(tempfile.gettempdir()))

    last_error: OSError | None = None
    seen_dirs: set[Path] = set()
    for base_dir in candidate_dirs:
        if base_dir is None or base_dir in seen_dirs:
            continue
        seen_dirs.add(base_dir)
        profile_path = base_dir / f"fastdds_tron2_{os.getpid()}.xml"
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(profile_text, encoding="utf-8")
            return profile_path
        except OSError as exc:
            last_error = exc
            if output_dir:
                break

    raise RuntimeError(f"Failed to write Fast DDS profile: {last_error}") from last_error


def _render_profile(local_ip: str, robot_ip: str) -> str:
    initial_peers = ""
    if robot_ip:
        initial_peers = f"""
        <initialPeersList>
          <locator>
            <udpv4>
              <address>{escape(robot_ip)}</address>
            </udpv4>
          </locator>
        </initialPeersList>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
  <transport_descriptors>
    <transport_descriptor>
      <transport_id>tron2_wifi_udp</transport_id>
      <type>UDPv4</type>
      <interfaceWhiteList>
        <address>{escape(local_ip)}</address>
      </interfaceWhiteList>
    </transport_descriptor>
  </transport_descriptors>

  <participant profile_name="tron2_participant" is_default_profile="true">
    <rtps>
      <builtin>{initial_peers}
      </builtin>
      <userTransports>
        <transport_id>tron2_wifi_udp</transport_id>
      </userTransports>
      <useBuiltinTransports>false</useBuiltinTransports>
    </rtps>
  </participant>
</profiles>
"""


def _local_ipv4_addresses() -> list[str]:
    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []

    addresses: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if "inet" not in fields:
            continue
        cidr = fields[fields.index("inet") + 1]
        addresses.append(cidr.split("/", 1)[0])
    return addresses


def _read_robot_ips(config_path: Path, robot_name: str) -> tuple[str, str]:
    with config_path.open("r", encoding="utf-8") as config_file:
        if config_path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ModuleNotFoundError as exc:
                raise RuntimeError("YAML config needs PyYAML. Install it or use config.json.") from exc
            raw: dict[str, Any] = yaml.safe_load(config_file)
        else:
            raw = json.load(config_file)

    robot = raw["robots"][robot_name]
    return (
        str(robot.get("local_ros_ip") or raw.get("default_local_ros_ip") or ""),
        str(robot.get("ip") or ""),
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Fast DDS profile for TRON2.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--robot", default="TRON2")
    parser.add_argument("--local-ip", default="")
    parser.add_argument("--robot-ip", default="")
    parser.add_argument("--no-validate-local-ip", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    config_local_ip, config_robot_ip = _read_robot_ips(args.config, args.robot)
    local_ip = (
        args.local_ip
        or os.environ.get("TRON2_LOCAL_IP")
        or os.environ.get("ROS_IP")
        or os.environ.get("ROS_HOSTNAME")
        or config_local_ip
    )
    robot_ip = args.robot_ip or os.environ.get("TRON2_ROBOT_IP") or os.environ.get("ROBOT_IP") or config_robot_ip
    profile_path = configure_fastdds_profile(
        local_ip,
        robot_ip,
        validate_local_ip=not args.no_validate_local_ip,
    )
    print(profile_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
