#!/usr/bin/env bash
# The Voyant driver image's environment contract (METROLLA_DRIVER_CONTRACT=1;
# docs/METROLLA_DRIVER_CONTRACT.md).
#
# /voyant_entrypoint.sh execs this script when METROLLA_DRIVER_CONTRACT is set and not "0". It
# sources ROS 2 and the driver workspace, then hands over to voyant_env.py, which checks the
# environment (refusing to start on any value it does not understand), writes the node's
# parameters file under /tmp and execs voyant_sensor_node with it, so the node is the process
# that receives SIGTERM.
set -eo pipefail

# setup.bash reads unset variables; it must not run under `set -u`.
# shellcheck source=/dev/null
source "/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash"
# shellcheck source=/dev/null
source "/ros2_ws/install/setup.bash"

exec python3 -I /opt/metrolla/voyant/voyant_env.py "$@"
