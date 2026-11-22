// Copyright (c) 2024-2025 Voyant Photonics, Inc.
//
// This example code is licensed under the MIT License.
// See the LICENSE file in the repository root for full license text.

#include "voyant_ros/sensor_driver.hpp"

int main(int argc, char **argv)
{
  // SIGINT and SIGTERM (docker stop) both set the Voyant client's stop flag
  // (VoyantSensorDriver::initialize), which ends the receive loops. rclcpp installs no signal
  // handlers of its own: they would replace that flag's handler for SIGTERM and shut ROS down
  // while the loops still run.
  rclcpp::init(argc, argv, rclcpp::InitOptions(), rclcpp::SignalHandlerOptions::None);
  int code = 0;
  try
  {
    // The constructor receives and publishes until a stop signal or an error; there is nothing
    // left to spin for afterwards (spinning here waited forever after a SIGINT).
    auto node = std::make_shared<voyant_ros::VoyantSensorDriver>();
  }
  catch(const std::exception &e)
  {
    RCLCPP_FATAL(rclcpp::get_logger("voyant_sensor_node"), "[-] Fatal: %s", e.what());
    code = 1;
  }
  rclcpp::shutdown();
  return code;
}
