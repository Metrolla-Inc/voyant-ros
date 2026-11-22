#!/bin/bash
# Metrolla driver contract, opt-in (docs/METROLLA_DRIVER_CONTRACT.md). Only when
# METROLLA_DRIVER_CONTRACT is set and not "0" does the image switch to the environment contract;
# otherwise the image's released entrypoint runs exactly as before (tests/legacy pins it).
if [ -n "${METROLLA_DRIVER_CONTRACT:-}" ] && [ "${METROLLA_DRIVER_CONTRACT}" != "0" ]; then
    exec /opt/metrolla/voyant/contract-entrypoint.sh "$@"
fi
# --- end of the Metrolla contract block; the released entrypoint follows ---
# Until this change the Dockerfile's ENTRYPOINT was this command in shell form, which Docker runs
# as `/bin/bash -c '<command>'` (the Dockerfile's SHELL) and to which it passes no arguments: a
# command given to the container was ignored, and still is. Same process, same string, byte for
# byte (tests/legacy/test_legacy.py checks it against the released Dockerfile).
exec /bin/bash -c 'source /ros2_ws/install/setup.bash &&     echo "VIZ_BRIDGE_ARG value: $VIZ_BRIDGE_ARG" &&     if [ "$VIZ_BRIDGE_ARG" = "true" ]; then     echo "Starting with Foxglove bridge enabled" &&     ros2 launch foxglove_bridge foxglove_bridge_launch.xml &     ros2 launch voyant_ros sensor_driver_minimal_launch.py;     else     echo "Starting with minimal sensor driver" &&     ros2 launch voyant_ros sensor_driver_minimal_launch.py;     fi'
