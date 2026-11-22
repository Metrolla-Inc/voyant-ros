# Metrolla driver contract (Voyant image)

The image `ghcr.io/metrolla-inc/voyant-ros` (built from `Dockerfile` on `main`) takes its settings
from the environment the same way as the other Metrolla driver images (Seyond, Hesai, LSLiDAR,
Quanergy, Ouster, RoboSense), and refuses a value it does not understand.

The contract is **opt-in**. Without `METROLLA_DRIVER_CONTRACT` (or with it empty or `0`) the image
behaves as the released Dockerfile did: its shell-form `ENTRYPOINT` command now lives in
`docker/voyant_entrypoint.sh` behind one block, byte for byte (tests/legacy pins it against the
released Dockerfile, and the smoke test against what Docker builds from those lines), and still
ignores any command given to the container. No fleet runs this image and no image of this fork
was ever pushed (every "Docker Build and Push" run failed or was cancelled; balena-fleets,
balena-fleets-metrollaprod, core_system_containers and the partner fleet repository, every branch, tag
and their history, checked 22 Nov 2026). The image's configuration changes in one visible way:
`Config.Entrypoint` is `["/voyant_entrypoint.sh"]` instead of `["/bin/bash", "-c", "<command>"]`;
the process the legacy image runs is the same.

## Platform

linux/amd64 only. voyant-api's arm64 development package ships x86-64 C++ libraries, so the driver
cannot be built for arm64 (docs/VENDOR_UPDATE_PLAN.md); the previous workflow asked for arm64 too
and could not have produced it.

## Which sensors

The image is built with voyant-api **0.4.4** (pinned; see docs/VENDOR_UPDATE_PLAN.md), whose
`VoyantClient` receives the **Meadowlark** point stream: Voyant's Carbon Dev Kit (device ids
`MDL-nnn`). Voyant's production **Carbon** sensors need voyant-api 0.5 or later (`CarbonClient`),
which is the vendor update that this change plans but does not take.

## Switch

| `METROLLA_DRIVER_CONTRACT` | Behaviour |
|---|---|
| unset, empty, `0` | legacy: the released command (`source /ros2_ws/install/setup.bash`, then `ros2 launch voyant_ros sensor_driver_minimal_launch.py`, which reads the vendor's `config/sensor_params.yaml` baked into the image: multicast `224.0.0.0`, port 4444, joined on `127.0.0.1`, frame `voyant_sensor`, topic `/point_cloud`) |
| `1` | contract: the environment below is checked, the node's parameters rendered, the node started |
| anything else | refused, exit 1 |

## Variables (with `METROLLA_DRIVER_CONTRACT=1`)

| Variable | Required | Accepted | Default | Becomes |
|---|---|---|---|---|
| `LIDAR_MODEL` | yes | `meadowlark` (also `mdl`, `carbon-dev-kit`, `carbon-devkit`); any case, `_`, `-` or space, optional `voyant-` prefix. `carbon` is refused with the reason (see above) | - | - (one model) |
| `SENSOR_IP` | yes | the sensor's IPv4 address | - | finds `HOST_IP` when that is not set; logged. The driver joins a multicast group and cannot filter by sender, so **each sensor on a host needs its own `DATA_PORT`** |
| `SENSOR_ID` | no | `[a-z][a-z0-9_]{0,30}`, no `__` | none | ROS namespace `/<SENSOR_ID>` (default `/`) |
| `DATA_PORT` | no | 1024-65535 | `4444` | `binding_address` `0.0.0.0:<port>`: the UDP port the sensor streams to |
| `DEVICE_PORT` | no | must be unset | - | refused when set: the driver only receives; it never talks to the sensor |
| `HOST_IP` | no | IPv4, optional `/1`-`/32`; not the sensor's address; with a prefix the sensor must be in the subnet | the host's own address on the route to `SENSOR_IP` (looked up when the container starts; refused when there is no route) | `interface_address`: the host interface the driver joins the multicast group on |
| `FRAME_ID` | no | `[A-Za-z_][A-Za-z0-9_]{0,63}` | `voyant_sensor` | `frame_id` (the cloud's `header.frame_id`) |
| `POINTCLOUD_TOPIC` | no | a ROS topic name, absolute or relative to the namespace | `point_cloud` (`/point_cloud`, or `/<SENSOR_ID>/point_cloud`) | remap of the node's `point_cloud` |
| `TIME_MODE` | no | `host` (any case); `sensor`, `gps`, `ptp`, `ntp` refused | `host` | `timestamp_mode` 0: voyant-api 0.4.4 stamps each frame with the **host's clock when the frame starts to arrive** (measured: packets recorded from the simulator and replayed minutes later get the replay time, not the recorded one); it passes no sensor clock through, and Voyant warns against using Meadowlark units' message stamps |
| `PUBLISH_SCAN` | no | false values only | false | - (no laser scan output) |
| `VOYANT_RUN_DIR` | no | absolute path | `/tmp/voyant` | where `params.yaml` is written |
| `DUAL_LIDAR` | no | false values only (empty = false) | false | - (one container per sensor) |

Fixed in contract mode: `multicast_group` `224.0.0.0` (the Meadowlark stream's group, vendor
config), `point_format` 1 (MDL_STANDARD), `spn_filter` true: only points whose `drop_reason` is
SUCCESS are published, as in the released image (whose `sensor_params.yaml` says
`valid_only_filter: false`, a key the node does not read; it reads `spn_filter`, default true).

The cloud: `sensor_msgs/PointCloud2`, unorganised (`height` 1), `is_dense` false, `point_step` 48,
fields `x`, `y`, `z`, `v` (radial velocity), `snr` float32 at 0, 4, 8, 16, 20; `drop_reason`
uint8 at 24; `timestamp_nsecs` int32 at 28; `point_idx` uint32 at 32. **There is no `intensity`
field.** QoS reliable, keep last 100. Device metadata (`voyant_ros/msg/VoyantDeviceMetadata`:
device id and version hashes) once, latched, on `<namespace>/device_metadata`.

Refusals: every problem is one line on stderr starting `voyant-driver: refusing to start:`, all
problems at once, exit 1. A legacy or vendor name (`LIDAR_TYPE`, `LIDAR_IP`, `BINDING_ADDRESS`,
`INTERFACE_ADDRESS`, `LIDAR_INTERFACE_DEV`, `TIMESTAMP_MODE`, `TOPIC_NAME`, `MULTICAST_GROUP`,
`VIZ_BRIDGE`, ...) is refused with the contract's name or the reason; an unknown variable under
the contract's prefixes is refused with the closest contract name. Other variables (`ROS_*`,
`RMW_*`, `METROLLA_*`, `USE_MACADDRESS_NS`, ...) pass through.

Commands with the switch on: none or `run` = run; `check` prints the rendered parameters file and
the exec line; `check --json` the same as JSON; `selfcheck` checks the contract against the node
source and binary in the image (run at image build); any other command is refused.

## How it runs

`/voyant_entrypoint.sh` execs `/opt/metrolla/voyant/contract-entrypoint.sh`, which sources ROS 2
and the workspace and execs `python3 -I /opt/metrolla/voyant/voyant_env.py`. That checks the
environment, writes `<VOYANT_RUN_DIR>/params.yaml` (every parameter the node declares, nothing
else; `selfcheck` fails the image build if the node's declared parameters change) and execs
`/ros2_ws/install/voyant_ros/lib/voyant_ros/voyant_sensor_node --ros-args [-r __ns:=/<SENSOR_ID>]
-r point_cloud:=<topic> --params-file <file>`. The node is PID 1. No launch file runs.

Stopping: `docker stop` (SIGTERM) and SIGINT end the node with exit 0 in under a second, under
load and while it still waits for its first frame; a client that cannot start (port held by
another process) ends it with exit 1. See "Vendor code changes".

## For the product (metrolla-edge generator)

```yaml
image: ghcr.io/metrolla-inc/voyant-ros:<tag from main after this change, pinned by digest>
user: "97000:97000"
read_only: true
tmpfs: [/tmp]
network_mode: host
ipc: host
cap_drop: [ALL]
security_opt: ["no-new-privileges:true"]
environment:
  METROLLA_DRIVER_CONTRACT: "1"
  LIDAR_MODEL: meadowlark          # metrolla-edge model "voyant-meadowlark" (prefix optional)
  SENSOR_IP: 192.168.20.20
  HOST_IP: 192.168.20.100/24       # the unit's address on the sensor network (recommended)
  SENSOR_ID: <sensors[].id>
  DATA_PORT: "4444"                # a second sensor on the host needs its own port
  FRAME_ID: <frame>                # optional
  POINTCLOUD_TOPIC: <topic>        # optional
  TIME_MODE: host                  # the only mode
# no command, no config file mount, no DEVICE_PORT
```

Same uid for every ROS container on the host: with host IPC, Fast DDS (the image's default RMW)
delivers between processes through shared memory, and a subscriber running as another uid gets
discovery but no data (seen in the Ouster smoke test; this smoke test runs its subscriber as the
driver's uid).

Host firewall: allow UDP `DATA_PORT` in from the sensor, to the multicast group `224.0.0.0`
(IGMP joins go out on the `HOST_IP` interface). The driver sends nothing to the sensor.

Sensor side (not done by the driver): the Meadowlark dev kit is at `192.168.20.20` and expects the
host at `192.168.20.100/24` (voyant-sdk's `automated_voyant_client_setup.py`); it streams to
`224.0.0.0:4444` (the vendor's config and examples); its control API is HTTP on
`192.168.20.20:8080` (voyant-api's `voyant_lidar_client` default), used to start and configure the
sensor. None of this was checked on a sensor.

## Vendor code changes

One commit of our own (`src/main.cpp`, `src/sensor_driver.cpp`), about stopping and failing:

1. SIGTERM sets the Voyant client's stop flag like SIGINT (`VoyantClient::setupSignalHandling`
   for both). Before, SIGTERM went to rclcpp's handler, which the loop waiting for the first frame
   never checked: a node without a streaming sensor ignored `docker stop` and was killed after
   10 s.
2. The first-frame wait sleeps 10 ms per poll (it spun a core at 100 %) and also ends on a ROS
   shutdown.
3. rclcpp installs no signal handlers (`SignalHandlerOptions::None`) and `main` no longer calls
   `rclcpp::spin` after the receive loop: after SIGINT that spin waited forever, since the Voyant
   handler had replaced rclcpp's.
4. A client that does not start, or an error while publishing, reaches `main`, which exits 1
   (logged `Fatal`). Before, both were logged, ROS was shut down and the process exited 0 or kept
   looping. The vendor's own code does the same (`throw;`, `return 1`) since 16f64bb (May 2026, part of the update below).

These reach legacy mode too, inside `ros2 launch`. Legacy `docker stop` itself is unchanged: the
released entrypoint's bash is PID 1 and does not pass SIGTERM on, so Docker kills the container
after 10 s (exit 137), as before.

## Not proven (needs hardware)

Certification needs a real Voyant sensor. Proven only against voyant-api 0.4.4's own Meadowlark
simulator (`voyant_points_mock_stream`, 23,040 points per frame at 10 Hz, CI smoke): every cloud
is a frame the SDK's client assembles from the same stream, point for point; invalid points are
dropped; stamps are the host's clock. Not proven: a real Meadowlark unit's stream (frame sizes,
real drop reasons, packet loss at real rates, multicast on a real NIC with IGMP snooping
switches), the factory network settings above, two sensors on one host, any Carbon sensor (not
supported by this image).
