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

linux/amd64 only. voyant-api's arm64 development package (0.4.4 and 1.0.1) ships x86-64 C++
libraries, so the driver cannot be built for arm64 (docs/VENDOR_DIFF.md); the previous workflow
asked for arm64 too and could not have produced it.

## Which sensors

The image is the vendor's voyant-ros **v1.0.1** built with voyant-api **1.0.1** (pinned by sha256;
docs/VENDOR_DIFF.md), whose `CarbonClient` drives the **Voyant Carbon** with firmware **FPGA v1.5.6
/ MCU v4.1.0** (older firmware is not compatible; Voyant's v7.1.X updater updates it). The Carbon
is Metrolla's target (owner, 22 Nov 2026). The Carbon Dev Kit (Meadowlark) is not: voyant-api 1.0
no longer receives it, and its names are refused.

## Image tags

`v<vendor version>-metrolla.<n>`, never a vendor's own tag: the first is **`v1.0.1-metrolla.1`**.
The image workflow pushes only for such a tag, from the tagged commit, never over an existing
image tag, and records the digest. Tags are the owner's.

## Switch

| `METROLLA_DRIVER_CONTRACT` | Behaviour |
|---|---|
| unset, empty, `0` | legacy: the released command (`source /ros2_ws/install/setup.bash`, then `ros2 launch voyant_ros sensor_driver_minimal_launch.py`, which reads the vendor's `config/sensor_params.yaml` baked into the image, v1.0.1's since the update: bind `0.0.0.0:5678`, group `239.255.48.84`, interface `192.168.1.100`, unicast, primary client, sensor time, frame `voyant_sensor`, topic `/point_cloud`; the client targets voyant-api's default sensor address `192.168.1.128:1234`) |
| `1` | contract: the environment below is checked, the node's parameters rendered, the node started |
| anything else | refused, exit 1 |

## Variables (with `METROLLA_DRIVER_CONTRACT=1`)

| Variable | Required | Accepted | Default | Becomes |
|---|---|---|---|---|
| `LIDAR_MODEL` | yes | `carbon`; any case, `_`, `-` or space, optional `voyant-` prefix (so metrolla-edge's `voyant-carbon`). The Carbon Dev Kit's names (`meadowlark`, `mdl`, `carbon-dev-kit`, `carbon-devkit`, `dev-kit`, `devkit`) are refused with the reason | - | - (one model) |
| `SENSOR_IP` | yes | the sensor's IPv4 address | - | `fpga_target_address` `<SENSOR_IP>:1234`: the sensor's register port, where the client writes the push destination, disciplines the clock and sends SDL; also finds `HOST_IP` when that is not set |
| `SENSOR_ID` | no | `[a-z][a-z0-9_]{0,30}`, no `__` | none | ROS namespace `/<SENSOR_ID>` (default `/`) |
| `DATA_PORT` | no | 1024-65535 | `5678` | `binding_address` `0.0.0.0:<port>`: the UDP port the client receives the stream on. The Carbon's own push port is believed to be 5678 (voyant-api's default); another value is unproven on a sensor |
| `DEVICE_PORT` | no | must be unset | - | refused when set: the sensor's register port is 1234, the sensor's, not a setting |
| `HOST_IP` | no | IPv4, optional `/1`-`/32`; not the sensor's address; with a prefix the sensor must be in the subnet | the host's own address on the route to `SENSOR_IP` (looked up when the container starts; refused when there is no route) | `interface_address`: the host interface the client joins the multicast group on |
| `FRAME_ID` | no | `[A-Za-z_][A-Za-z0-9_]{0,63}` | `voyant_sensor` | `frame_id` (the cloud's `header.frame_id`) |
| `POINTCLOUD_TOPIC` | no | a ROS topic name, absolute or relative to the namespace | `point_cloud` (`/point_cloud`, or `/<SENSOR_ID>/point_cloud`) | remap of the node's `point_cloud` |
| `TIME_MODE` | no | `sensor` or `host` (any case); `gps`, `ptp`, `ntp` refused (keep the host's clock synchronised instead) | `sensor` | `sensor`: `timestamp_mode` 0, the frame's own start time from the sensor, whose clock the client disciplines to the host's continuously (Voyant claims under 100 µs). `host`: `timestamp_mode` 1, the ROS clock when the node converts the frame (about one frame, 0.1 s, after the frame's start) |
| `PUBLISH_SCAN` | no | false values only | false | - (no laser scan output) |
| `VOYANT_RUN_DIR` | no | absolute path | `/tmp/voyant` | where `params.yaml` is written |
| `DUAL_LIDAR` | no | false values only (empty = false) | false | - (one container per sensor) |

Fixed in contract mode: `multicast_group` `239.255.48.84` (the vendor's), `stream_transport`
`unicast` (the sensor sends to this client only; the vendor's default), `observer_only` false (the
driver is the sensor's one primary client: no second primary client, such as Voyant's visualizer
in primary mode or a second driver, on the same sensor), `diagnostic_mode` false (valid points
only).

The cloud: `sensor_msgs/PointCloud2`, unorganised (`height` 1), `is_dense` false, `point_step` 48,
fields `x`, `y`, `z`, `v` (radial velocity, m/s), `snr` (linear) float32 at 0, 4, 8, 16, 20;
`drop_reason` uint8 at 24 (always 1, valid); `timestamp_nsecs` uint32 at 28 (since the frame's
start); `azimuth_idx`, `elevation_idx` uint16 at 32, 34; `calibrated_reflectance` float32 at 36
(dB); `frame_index` uint32 at 40. **There is no `intensity` field** (`calibrated_reflectance` is
the nearest). QoS reliable, keep last 100. Device metadata (`voyant_ros/msg/VoyantDeviceMetadata`:
device id, product id, serial, voyant-api and interface-contract versions, MCU firmware and FPGA
versions) once, latched, on `<namespace>/device_metadata`.

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
image: ghcr.io/metrolla-inc/voyant-ros:v1.0.1-metrolla.1@<digest>   # after the owner tags
user: "97000:97000"
read_only: true
tmpfs: [/tmp]
network_mode: host
ipc: host
cap_drop: [ALL]
security_opt: ["no-new-privileges:true"]
environment:
  METROLLA_DRIVER_CONTRACT: "1"
  LIDAR_MODEL: carbon              # metrolla-edge model "voyant-carbon" (prefix optional)
  SENSOR_IP: <the Carbon's address> # voyant-api's default target is 192.168.1.128 (unverified)
  HOST_IP: 192.168.1.100/24        # the unit's address on the sensor network (recommended)
  SENSOR_ID: <sensors[].id>
  DATA_PORT: "5678"
  FRAME_ID: <frame>                # optional
  POINTCLOUD_TOPIC: <topic>        # optional
  TIME_MODE: sensor                # default; or host
# no command, no config file mount, no DEVICE_PORT
```

Same uid for every ROS container on the host: with host IPC, Fast DDS (the image's default RMW)
delivers between processes through shared memory, and a subscriber running as another uid gets
discovery but no data (seen in the Ouster smoke test; this smoke test runs its subscriber as the
driver's uid).

Host firewall: allow UDP `DATA_PORT` (5678) in from the sensor (unicast to the host, or to the
group `239.255.48.84` if the stream is ever switched to multicast), and UDP out to the sensor's
register port `SENSOR_IP:1234` with its replies (the client's register reads and writes and the
clock discipline).

**The driver writes to the sensor** (docs/VENDOR_DIFF.md): the push destination, the clock
discipline, link checks; SDL settings only if asked (the driver asks none). One primary client per
sensor.

Sensor side: voyant-api's built-in defaults put the sensor's register port at `192.168.1.128:1234`
and the host at `192.168.1.100` (the client's defaults and Voyant's visualizer preset); Voyant does
not document these as the Carbon's factory settings, and none of it was checked on a sensor. The
Carbon needs firmware FPGA v1.5.6 / MCU v4.1.0.

One Carbon per host is what is proven (against the simulator); two Carbons on one host (two
clients each binding port 5678, both unicast) is unproven and may need separate ports or hosts.

## Vendor code changes

One commit of our own (`src/main.cpp`, `src/sensor_driver.cpp`), about stopping and failing:

1. SIGTERM sets the Carbon client's stop flag like SIGINT (`CarbonClient::setupSignalHandling`
   for both). Before, SIGTERM went to rclcpp's handler, which the loop waiting for the first frame
   never checked: a node without a streaming sensor ignored `docker stop` and was killed after
   10 s.
2. The first-frame wait sleeps 10 ms per poll (the vendor's v1.0.1 does too; before it spun a core
   at 100 %) and also ends on a ROS shutdown.
3. rclcpp installs no signal handlers (`SignalHandlerOptions::None`) and `main` no longer calls
   `rclcpp::spin` after the receive loop: after SIGINT that spin waited forever, since the Voyant
   handler had replaced rclcpp's.
4. A client that does not start, an error while publishing, or a client that stops by itself
   reaches `main`, which exits 1 (logged `Fatal`). Before, these were logged, ROS was shut down and
   the process exited 0 or kept looping. (The vendor's v1.0.1 rethrows a start failure, but an error
   while publishing still exits 0.)
5. The node parameter `fpga_target_address` (default voyant-api's own `192.168.1.128:1234`) is
   passed to the client (`setFpgaTargetAddr`); the contract sets it to `SENSOR_IP:1234`. The
   vendor's node never set it, so it drove only a Carbon at voyant-api's default address.

These reach legacy mode too, inside `ros2 launch` (5 with its default, so legacy behaves as
the vendor's node). Legacy `docker stop` itself is unchanged: the
released entrypoint's bash is PID 1 and does not pass SIGTERM on, so Docker kills the container
after 10 s (exit 137), as before.

## Not proven (needs hardware)

Certification needs a real Voyant Carbon. Proven only against voyant-api 1.0.1's own Carbon
simulator (`voyant_simulator`, 23,168 points per frame at 10 Hz, with its FPGA register port; CI
smoke test, and natively during the update): every cloud is a frame the SDK's own client
assembles from the same stream, point for point; invalid points are dropped; `TIME_MODE` sensor
stamps are the frames' own times and `host` stamps the host's clock; the client's register
traffic reaches `SENSOR_IP:1234`; stops and failures exit as above. Not proven: the owner's
Carbon's firmware (must be FPGA 1.5.6 / MCU 4.1.0) and address; a first frame from a real Carbon
(frame sizes, real drop reasons, packet loss at real rates); what the firmware does with the
client's writes (push destination, clock discipline) and the real clock accuracy; a `DATA_PORT`
other than 5678; two Carbons on one host; reconnecting after a power loss.
