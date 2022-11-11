#!/usr/bin/python3
"""Show all sensor data of Solo12.

Show all data from observation, status and applied action in a simple TUI.  Press M to
toggle between position control mode (holding all joints at their current position) and
zero torque mode.
"""
import argparse
import enum
import typing

import numpy as np
import urwid as u
import tabulate

from robot_interfaces_solo import solo12


class Robot:
    class ControlMode(enum.Enum):
        ZERO_TORQUE = 1
        HOLD_POSITION = 2

    def __init__(self, config_file: str, fake_robot: bool = False) -> None:
        self.kp = 3.0
        self.kd = 0.05
        self.control_mode = Robot.ControlMode.HOLD_POSITION

        config = solo12.Config.from_file(config_file)
        robot_data = solo12.SingleProcessData()
        if fake_robot:
            self.robot_backend = solo12.create_fake_backend(robot_data, config)
        else:
            self.robot_backend = solo12.create_backend(robot_data, config)
        self.robot_frontend = solo12.Frontend(robot_data)

        self.t = 0

    def initialize(self):
        # Initializes the robot (e.g. performs homing).
        self.robot_backend.initialize()

        # start by sending a zero-torque action (this is needed to start the backend
        # loop)
        action = solo12.Action.Zero()
        self.t = self.robot_frontend.append_desired_action(action)

        # get the initial joint positions to construct the desired action
        observation = self.robot_frontend.get_observation(self.t)

        self.desired_action = solo12.Action()
        self.desired_action.joint_torques = np.array([0.0] * 12)
        self.desired_action.joint_positions = observation.joint_positions
        self.desired_action.joint_velocities = np.array([0.0] * 12)
        self.desired_action.joint_position_gains = np.array([self.kp] * 12)
        self.desired_action.joint_velocity_gains = np.array([self.kd] * 12)

    def update(self):
        # get data
        obs = self.robot_frontend.get_observation(self.t)
        applied_action = self.robot_frontend.get_applied_action(self.t)
        status = self.robot_frontend.get_status(self.t)

        # send action and update t
        self.t = self.robot_frontend.append_desired_action(self.desired_action)

        return obs, status, applied_action

    def set_control_mode(self, mode: ControlMode):
        self.control_mode = mode

        if self.control_mode == Robot.ControlMode.ZERO_TORQUE:
            self.desired_action = solo12.Action.Zero()
        elif self.control_mode == Robot.ControlMode.HOLD_POSITION:
            observation = self.robot_frontend.get_observation(self.t)
            self.desired_action = solo12.Action()
            self.desired_action.joint_torques = np.array([0.0] * 12)
            self.desired_action.joint_positions = observation.joint_positions
            self.desired_action.joint_velocities = np.array([0.0] * 12)
            self.desired_action.joint_position_gains = np.array([self.kp] * 12)
            self.desired_action.joint_velocity_gains = np.array([self.kd] * 12)


class SliderBar(u.ProgressBar):
    def __init__(self, label):
        super().__init__(None, "progressbar.complete", 0.0, 1.0)

        self.label = label
        self.slider_position = 0

    def set_position(self, position):
        self.slider_position = position
        self.set_completion(position)

    def get_text(self):
        return f"slider {self.label}: {self.slider_position:.4f}"


def labeled_vector(label: str, vector: typing.Sequence, fmt: str = "% .4f") -> str:
    n = len(vector)
    fmt_str = "%s " + " ".join([fmt] * n)
    return fmt_str % (label, *vector)


class Window:
    def __init__(self, robot: Robot):
        self.robot = robot

        # define all the text fields
        self.obs_motor_data_text = u.Text("")
        self.obs_imu_acc_texts = u.Text("")
        self.obs_imu_gyro_texts = u.Text("")
        self.obs_imu_linacc_texts = u.Text("")
        self.obs_imu_att_texts = u.Text("")
        self.obs_packet_loss_command_text = u.Text("")
        self.obs_packet_loss_sensor_text = u.Text("")
        self.obs_slider_bars = [SliderBar(i) for i in range(4)]
        self.status_action_repetitions_text = u.Text("")
        self.status_error_state_text = u.Text("")
        self.status_error_msg_text = u.Text("")
        self.applied_action_text = u.Text("")
        self.control_mode_text = u.Text("")

        # define the window layout
        motor_data_box = u.LineBox(
            self.obs_motor_data_text,
            title="Motor Data",
        )

        imu_data_box = u.LineBox(
            u.Pile(
                [
                    self.obs_imu_acc_texts,
                    self.obs_imu_gyro_texts,
                    self.obs_imu_linacc_texts,
                    self.obs_imu_att_texts,
                ]
            ),
            title="IMU",
        )

        packet_loss_box = u.LineBox(
            u.BoxAdapter(
                u.ListBox(
                    [
                        self.obs_packet_loss_command_text,
                        self.obs_packet_loss_sensor_text,
                    ]
                ),
                height=4,  # to match height of IMU box
            ),
            title="Packet Loss",
        )

        sliders_box = u.LineBox(
            u.Pile(self.obs_slider_bars),
            title="slider_positions",
        )

        observation_rows = u.Pile(
            [motor_data_box, sliders_box, u.Columns([imu_data_box, packet_loss_box])]
        )
        observation = u.LineBox(observation_rows, title="Observation")

        status = u.LineBox(
            u.Pile(
                [
                    self.status_action_repetitions_text,
                    self.status_error_state_text,
                    self.status_error_msg_text,
                ]
            ),
            title="Status",
        )

        applied_action = u.LineBox(
            self.applied_action_text,
            title="Applied Action",
        )

        body = u.Filler(u.Pile([observation, status, applied_action]))
        header = u.Padding(
            u.BigText("Solo12 Sensor Data", u.font.HalfBlock5x4Font()),
            align="center",
            width="clip",
        )
        footer = u.Pile(
            [self.control_mode_text, u.Text("Q/ESC: exit | M: toggle control mode")]
        )
        self.mainframe = u.Frame(body=body, header=header, footer=footer)

        palette = [
            ("progressbar.complete", "default", "dark gray"),
        ]

        self.loop = u.MainLoop(
            self.mainframe, palette=palette, unhandled_input=self._key_press_handler
        )

    def _key_press_handler(self, key: str) -> None:
        if key in ("q", "Q", "esc"):
            raise u.ExitMainLoop()
        if key in ("m", "M"):
            if self.robot.control_mode == Robot.ControlMode.ZERO_TORQUE:
                self.set_control_mode(Robot.ControlMode.HOLD_POSITION)
            else:
                self.set_control_mode(Robot.ControlMode.ZERO_TORQUE)

    def set_control_mode(self, mode: Robot.ControlMode):
        self.robot.set_control_mode(mode)
        self.control_mode_text.set_text(f"Active Control Mode: {mode.name}")

    def run(self) -> None:
        update_interval_s = 0.001

        self.set_control_mode(Robot.ControlMode.HOLD_POSITION)

        # set update callback
        def update_window(loop: u.MainLoop, win):
            win.update_data()
            loop.set_alarm_in(update_interval_s, update_window, win)

        self.loop.set_alarm_in(update_interval_s, update_window, self)
        self.loop.run()

    def update_data(self) -> None:
        """Update the text fields based on the given observation."""

        observation, status, applied_action = self.robot.update()

        # Observation
        obs = observation

        motor_data = [
            ["Joint Index"] + list(map(str, range(12))),
            ["joint_positions"] + list(obs.joint_positions),
            ["joint_velocities"] + list(obs.joint_velocities),
            ["joint_torques"] + list(obs.joint_torques),
            ["joint_target_torques"] + list(obs.joint_target_torques),
            ["joint_encoder_index"] + list(obs.joint_encoder_index),
        ]
        self.obs_motor_data_text.set_text(
            tabulate.tabulate(motor_data, headers="firstrow", floatfmt=" .4f")
        )

        for slider_bar, position in zip(self.obs_slider_bars, obs.slider_positions):
            slider_bar.set_position(position)

        self.obs_imu_acc_texts.set_text(
            labeled_vector("imu_accelerometer:      ", obs.imu_accelerometer)
        )
        self.obs_imu_gyro_texts.set_text(
            labeled_vector("imu_gyroscope:          ", obs.imu_gyroscope)
        )
        self.obs_imu_linacc_texts.set_text(
            labeled_vector("imu_linear_acceleration:", obs.imu_linear_acceleration)
        )
        self.obs_imu_att_texts.set_text(
            labeled_vector("imu_attitude:           ", obs.imu_attitude)
        )

        packet_loss_fmt = "{label}: {lost}/{total} ({ratio:.0f} %)"
        self.obs_packet_loss_command_text.set_text(
            packet_loss_fmt.format(
                label="Command packet loss",
                lost=obs.num_lost_command_packets,
                total=obs.num_sent_command_packets,
                ratio=(
                    obs.num_lost_command_packets / obs.num_sent_command_packets * 100
                    if obs.num_sent_command_packets
                    else 0
                ),
            )
        )
        self.obs_packet_loss_sensor_text.set_text(
            packet_loss_fmt.format(
                label="Sensor  packet loss",
                lost=obs.num_lost_sensor_packets,
                total=obs.num_sent_sensor_packets,
                ratio=(
                    obs.num_lost_sensor_packets / obs.num_sent_sensor_packets * 100
                    if obs.num_sent_sensor_packets
                    else 0
                ),
            )
        )

        # Status
        self.status_action_repetitions_text.set_text(
            f"action_repetitions: {status.action_repetitions}"
        )
        self.status_error_state_text.set_text(f"error_status: {status.error_status}")
        self.status_error_msg_text.set_text(status.get_error_message())

        # Applied Action
        applied_action_data = [
            ["Joint Index"] + list(map(str, range(12))),
            ["joint_torques"] + list(applied_action.joint_torques),
            ["joint_positions"] + list(applied_action.joint_positions),
            ["joint_velocities"] + list(applied_action.joint_velocities),
            ["joint_position_gains"] + list(applied_action.joint_position_gains),
            ["joint_velocity_gains"] + list(applied_action.joint_velocity_gains),
        ]
        self.applied_action_text.set_text(
            tabulate.tabulate(applied_action_data, headers="firstrow", floatfmt=" .4f")
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config_file",
        type=str,
        help="YAML file with Solo12 driver configuration.",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use fake robot driver (for testing without actual robot).",
    )
    args = parser.parse_args()

    robot = Robot(args.config_file, args.fake)
    robot.initialize()

    win = Window(robot)
    win.run()


if __name__ == "__main__":
    main()
