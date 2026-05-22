#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONFIG_PATH="${TRON2_CONFIG:-$SCRIPT_DIR/config.json}"

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

unset CONDA_PREFIX
unset CONDA_DEFAULT_ENV
unset CONDA_SHLVL
unset CONDA_PROMPT_MODIFIER
unset CONDA_EXE
unset _CONDA_EXE
unset _CONDA_ROOT
unset CONDA_PYTHON_EXE

clear_ros_env

set +u
# shellcheck disable=SC1091
source /opt/ros/foxy/setup.bash
set -u

export LD_PRELOAD="/lib/x86_64-linux-gnu/libffi.so.7${LD_PRELOAD:+:$LD_PRELOAD}"
export ROS_LOCALHOST_ONLY=0
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
FASTRTPS_DEFAULT_PROFILES_FILE="$(
  PYTHONPATH="$SCRIPT_DIR" /usr/bin/python3.8 -m camera_collector.fastdds_profile --config "$CONFIG_PATH" --robot TRON2
)"
export FASTRTPS_DEFAULT_PROFILES_FILE
echo "Fast DDS profile: $FASTRTPS_DEFAULT_PROFILES_FILE" >&2

exec "$@"
