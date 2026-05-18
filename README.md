# 多机器人摄像头数据采集工具

这个工具用于在 TRON1、TRON2、bunker 之间切换摄像头画面，并把当前分屏画面按任务阶段保存到本地。

## 功能

- TRON1：ROS1，默认 IP `10.192.1.3`，单摄像头全屏显示。
- TRON2：ROS2，默认 IP `10.192.1.4`，顶部摄像头在上方，两个腕部摄像头在下方左右分屏。
- bunker：ROS1，IP 可在界面里临时填写，两个摄像头上下分屏。
- 可在界面里随时修改本机 `ROS_IP`，点击“连接 / 重连”后按新的网络配置启动采集。
- 右侧四个保存按钮：`执行任务前`、`执行任务中`、`执行成功`、`执行失败`。
- 保存文件名格式：`年月日_时分秒_毫秒_按钮名称_机器人名称.jpg`。

## 安装依赖

在有 ROS 环境的机器上运行：

```bash
cd multi_robot_camera_collector
python -m pip install -r requirements.txt
```

还需要系统已经安装对应 ROS Python 包：

- ROS1：`rospy`、`sensor_msgs`、`cv_bridge`
- ROS2：`rclpy`、`sensor_msgs`、`cv_bridge`

## 启动

```bash
cd multi_robot_camera_collector
python run_camera_collector.py
```

如果当前终端或 VS Code 激活了 Conda，ROS1 raw 图像解码时可能遇到 `libp11-kit.so.0: undefined symbol: ffi_type_pointer, version LIBFFI_BASE_7.0`。这是 Conda `libffi` 和系统 ROS 库冲突，可以用系统 Python 启动脚本避开：

```bash
./start.sh --config config.yaml
```

TRON2 是 ROS2 机器人，需要系统安装 ROS2 Foxy 后用 Foxy 环境启动：

```bash
sudo apt-get update
sudo apt-get install ros-foxy-ros-base ros-foxy-rclpy ros-foxy-sensor-msgs ros-foxy-cv-bridge
./start_ros2_foxy.sh --config config.yaml
```

Noetic 和 Foxy 可以共存，但不要在同一个终端手动同时 source 两套环境。连接 TRON1/bunker 用 `start.sh`，连接 TRON2 用 `start_ros2_foxy.sh`。

调试 TRON2 话题时也不要在 `(base)` 或 Noetic 终端里裸跑 `ros2`，用项目里的包装器：

```bash
./ros2_tron2_env.sh ros2 topic list
./ros2_tron2_env.sh ros2 topic info -v /camera/top/color/image_raw/compressed
./ros2_tron2_env.sh ros2 topic hz /camera/top/color/image_raw/compressed
```

如果 TRON2 本机能看到图像帧率，但采集电脑只能看到 publisher、收不到 `hz`，说明相机节点的 DDS 数据通道没有跨机器发出。需要在 TRON2 端确认相机节点启动环境包含 `ROS_LOCALHOST_ONLY=0`、两端 `ROS_DOMAIN_ID` 一致，并且相机节点使用可跨网卡通信的 DDS/UDP 配置。

没有接入机器人时，可以先用模拟画面检查界面和保存逻辑：

```bash
python run_camera_collector.py --mock
```

## 网络切换

界面顶部有两个 IP 输入框：

- `本机 ROS_IP`：填当前电脑在机器人热点/局域网里的 IP。
- `机器人 IP`：当前机器人的 IP。TRON1、TRON2 会自动带出默认值，bunker 可以现场填写。

修改 IP 后点击“连接 / 重连”。ROS1 机器人会按配置生成：

```text
ROS_IP=<本机 ROS_IP>
ROS_HOSTNAME=<本机 ROS_IP>
ROS_MASTER_URI=http://<机器人 IP>:11311
```

ROS2 机器人会设置 `ROS_IP` / `ROS_HOSTNAME`，然后用 `rclpy` 订阅配置里的 topic。

## 修改摄像头 topic

所有机器人、IP、topic 和布局都在 `config.json` 中：

```json
{
  "robots": {
    "TRON1": {
      "cameras": [
        {
          "key": "tron1_camera",
          "name": "TRON1 Camera",
          "topic": "/camera/color/image_raw"
        }
      ]
    }
  }
}
```

如果现场 topic 名称不同，只需要把对应 `topic` 改成实际的 `sensor_msgs/Image` 话题即可。`/compressed` 结尾的话题会按 `sensor_msgs/CompressedImage` 自动解码。

仓库里也保留了 `config.yaml`。如果想用 YAML，可以安装 `PyYAML` 后这样启动：

```bash
python run_camera_collector.py --config config.yaml
```

## 保存位置

默认保存到：

```text
multi_robot_camera_collector/captured_images
```

也可以在界面中选择其他目录。
