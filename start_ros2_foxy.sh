#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ARGS=("$@")

clear_ros_env() {
  unset AMENT_PREFIX_PATH
  unset CMAKE_PREFIX_PATH
  unset COLCON_PREFIX_PATH
  unset LD_LIBRARY_PATH
  unset PKG_CONFIG_PATH
  unset PYTHONPATH
  unset ROS_DISTRO
  unset ROS_ETC_DIR
  unset ROS_MASTER_URI
  unset ROS_PACKAGE_PATH
  unset ROS_PYTHON_VERSION
  unset ROS_ROOT
  unset ROS_VERSION
}

if [ ! -f /opt/ros/foxy/setup.bash ]; then
  echo "ROS2 Foxy is not installed: /opt/ros/foxy/setup.bash not found" >&2
  echo "Install it first: sudo apt-get install ros-foxy-ros-base ros-foxy-rclpy ros-foxy-sensor-msgs ros-foxy-cv-bridge" >&2
  exit 1
fi

unset CONDA_PREFIX
unset CONDA_DEFAULT_ENV
unset CONDA_SHLVL
unset CONDA_PROMPT_MODIFIER
unset CONDA_EXE
unset _CONDA_EXE
unset _CONDA_ROOT
unset CONDA_PYTHON_EXE

clear_ros_env

set --
set +u
# shellcheck disable=SC1091
source /opt/ros/foxy/setup.bash
set -u

export LD_PRELOAD="/lib/x86_64-linux-gnu/libffi.so.7${LD_PRELOAD:+:$LD_PRELOAD}"
export ROS_LOCALHOST_ONLY=0
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTRTPS_DEFAULT_PROFILES_FILE="$SCRIPT_DIR/fastdds_tron2.xml"

exec /usr/bin/python3.8 "$SCRIPT_DIR/run_camera_collector.py" "${APP_ARGS[@]}"
