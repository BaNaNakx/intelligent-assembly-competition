from __future__ import annotations

from pathlib import Path
from math import radians

from assembly.robot_parameters import PhotoPoseParameters, RobotGlobalParameters


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_SETTINGS_PATH = PROJECT_ROOT / "runtime" / "local_settings.json"

VISIONMASTER_IP = "127.0.0.1"
VISIONMASTER_PORT = 7930
TASK_CARD_IMAGE_DIRECTORY = str(PROJECT_ROOT / "runtime" / "visionmaster")
ARCS_ROBOT_IP = "192.168.1.12"
ARCS_JSON_RPC_PORT = 30004

ROBOT_PARAMETERS = RobotGlobalParameters(
    task_sequence=("task1", "task2"),
    step_action_sequence=("locate_block", "pick", "locate_tray", "place", "return_photo"),
    zero_joints_deg=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    block_photo_pose=PhotoPoseParameters(
        x_mm=169.41,
        y_mm=-394.49,
        z_mm=428.00,
        rx_rad=-3.139,
        ry_rad=0.001,
        rz_rad=-0.277,
        joints_deg=(-35.89, 8.39, 109.55, 10.89, 90.0, 55.45),
    ),
    tray_photo_pose=PhotoPoseParameters(
        x_mm=-171.95,
        y_mm=-334.41,
        z_mm=428.02,
        rx_rad=-3.139,
        ry_rad=0.001,
        rz_rad=-0.276,
        joints_deg=(-83.90, 16.77, 115.91, 8.87, 89.87, 7.43),
    ),
    task_card_photo_pose=PhotoPoseParameters(
        x_mm=-209.36,
        y_mm=-551.80,
        z_mm=428.04,
        rx_rad=-3.139,
        ry_rad=0.001,
        rz_rad=-0.276,
        joints_deg=(-84.46, -14.81, 84.92, 9.45, 89.92, 6.87),
    ),
    tool_rx_deg=-180.0,
    tool_ry_deg=0.0,
    vm_xy_scale_k=1.0,
    rz_sign=1.0,
    rz_offset_deg=0.0,
    base_plane_z_mm=0.0,
    block_pick_z_mm=186.0,
    block_place_z_mm=182.0,
    stack_z_mm=208.3,
    lift_distance_mm=100.0,
    suction_tcp_x_mm=-27.50,
    suction_tcp_y_mm=-624.48,
    camera_tcp_x_mm=-13.97,
    camera_tcp_y_mm=-502.29,
    workspace_min_x_mm=-886.5,
    workspace_max_x_mm=886.5,
    workspace_min_y_mm=-886.5,
    workspace_max_y_mm=886.5,
    workspace_min_z_mm=0.0,
    workspace_max_z_mm=886.5,
    workspace_radius_mm=886.5,
    joint_acceleration_rad_s2=radians(30.0),
    joint_velocity_rad_s=radians(60.0),
    transit_acceleration_m_s2=0.4,
    transit_velocity_m_s=0.4,
    precision_acceleration_m_s2=0.3,
    precision_velocity_m_s=0.3,
    completion_timeout_s=60.0,
    poll_interval_s=0.10,
    tool_do_index=1,
    tool_blow_do_index=0,
    tool_do_active_high=True,
    suction_settle_s=0.20,
    release_settle_s=0.20,
    photo_settle_s=1.0,
    model_result_settle_s=1.0,
)
