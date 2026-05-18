from __future__ import annotations

import io
import os
import queue
import time
from dataclasses import asdict
from multiprocessing import Event, Queue
from typing import Any


def run_camera_worker(
    robot: dict[str, Any],
    local_ros_ip: str,
    robot_ip: str,
    frame_queue: Queue,
    status_queue: Queue,
    stop_event: Event,
    mock: bool = False,
) -> None:
    """Entry point for the camera process.

    ROS networking variables must be set before importing rospy/rclpy. Keeping
    ROS code in this child process lets the UI switch robots and IPs cleanly.
    """
    os.environ["ROS_IP"] = local_ros_ip
    os.environ["ROS_HOSTNAME"] = local_ros_ip
    os.environ["ROBOT_IP"] = robot_ip

    if robot["ros_version"] == "ros1":
        template = robot.get("ros_master_uri_template") or "http://{robot_ip}:11311"
        os.environ["ROS_MASTER_URI"] = template.format(robot_ip=robot_ip)

    try:
        if mock:
            _run_mock_worker(robot, frame_queue, status_queue, stop_event)
        elif robot["ros_version"] == "ros1":
            _run_ros1_worker(robot, frame_queue, status_queue, stop_event)
        elif robot["ros_version"] == "ros2":
            _run_ros2_worker(robot, frame_queue, status_queue, stop_event)
        else:
            raise RuntimeError(f"Unsupported ROS version: {robot['ros_version']}")
    except Exception as exc:  # pragma: no cover - depends on ROS runtime
        status_queue.put(("error", f"{robot['name']} worker stopped: {exc}"))


def serialize_robot(robot: Any) -> dict[str, Any]:
    return asdict(robot)


def _publish_frame(frame_queue: Queue, camera_key: str, image_bgr: np.ndarray) -> None:
    import cv2

    ok, encoded = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        return
    payload = (camera_key, time.time(), encoded.tobytes())
    _put_latest(frame_queue, payload)


def _put_latest(target_queue: Queue, payload: tuple[str, float, bytes]) -> None:
    try:
        target_queue.put_nowait(payload)
        return
    except queue.Full:
        pass

    try:
        target_queue.get_nowait()
    except queue.Empty:
        pass

    try:
        target_queue.put_nowait(payload)
    except queue.Full:
        pass


def _image_msg_to_bgr(msg: Any, bridge: Any) -> np.ndarray:
    import cv2
    import numpy as np

    if getattr(msg, "encoding", "") in ("rgb8", "bgr8", "mono8"):
        desired = "bgr8"
    else:
        desired = "passthrough"
    image = bridge.imgmsg_to_cv2(msg, desired_encoding=desired)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.dtype != np.uint8:
        image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    if image.shape[-1] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


def _run_ros1_worker(
    robot: dict[str, Any],
    frame_queue: Queue,
    status_queue: Queue,
    stop_event: Event,
) -> None:
    import rospy
    from cv_bridge import CvBridge
    from sensor_msgs.msg import Image

    bridge = CvBridge()
    node_name = f"camera_collector_{robot['name'].lower()}_{int(time.time())}"
    rospy.init_node(node_name, anonymous=True, disable_signals=True)

    def callback_factory(camera_key: str):
        def on_image(msg: Image) -> None:
            try:
                _publish_frame(frame_queue, camera_key, _image_msg_to_bgr(msg, bridge))
            except Exception as exc:
                status_queue.put(("warn", f"{camera_key}: {exc}"))

        return on_image

    for camera in robot["cameras"]:
        rospy.Subscriber(camera["topic"], Image, callback_factory(camera["key"]), queue_size=1)

    topics = ", ".join(camera["topic"] for camera in robot["cameras"])
    status_queue.put(("info", f"ROS1 connected: {robot['name']} [{topics}]"))
    rate = rospy.Rate(20)
    while not stop_event.is_set() and not rospy.is_shutdown():
        rate.sleep()


def _run_ros2_worker(
    robot: dict[str, Any],
    frame_queue: Queue,
    status_queue: Queue,
    stop_event: Event,
) -> None:
    import rclpy
    from cv_bridge import CvBridge
    from sensor_msgs.msg import Image

    rclpy.init(args=None)
    node = rclpy.create_node(f"camera_collector_{robot['name'].lower()}")
    bridge = CvBridge()

    def callback_factory(camera_key: str):
        def on_image(msg: Image) -> None:
            try:
                _publish_frame(frame_queue, camera_key, _image_msg_to_bgr(msg, bridge))
            except Exception as exc:
                status_queue.put(("warn", f"{camera_key}: {exc}"))

        return on_image

    subscriptions = []
    for camera in robot["cameras"]:
        subscriptions.append(
            node.create_subscription(
                Image,
                camera["topic"],
                callback_factory(camera["key"]),
                10,
            )
        )

    topics = ", ".join(camera["topic"] for camera in robot["cameras"])
    status_queue.put(("info", f"ROS2 connected: {robot['name']} [{topics}]"))
    try:
        while not stop_event.is_set():
            rclpy.spin_once(node, timeout_sec=0.05)
    finally:
        for subscription in subscriptions:
            node.destroy_subscription(subscription)
        node.destroy_node()
        rclpy.shutdown()


def _run_mock_worker(
    robot: dict[str, Any],
    frame_queue: Queue,
    status_queue: Queue,
    stop_event: Event,
) -> None:
    import cv2
    import numpy as np

    status_queue.put(("info", f"Mock stream started: {robot['name']}"))
    colors = [(55, 125, 230), (80, 190, 120), (210, 110, 70)]
    tick = 0
    while not stop_event.is_set():
        for index, camera in enumerate(robot["cameras"]):
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            color = colors[index % len(colors)]
            frame[:] = (24, 28, 34)
            cv2.rectangle(frame, (28, 28), (612, 452), color, 6)
            cv2.putText(
                frame,
                robot["name"],
                (48, 110),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.8,
                color,
                4,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                camera["name"],
                (48, 180),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.2,
                (235, 235, 235),
                3,
                cv2.LINE_AA,
            )
            cv2.circle(frame, (70 + (tick * 8) % 500, 330), 34, color, -1)
            _publish_frame(frame_queue, camera["key"], frame)
        tick += 1
        time.sleep(0.08)
