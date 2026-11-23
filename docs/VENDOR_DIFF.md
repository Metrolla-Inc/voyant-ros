# Vendor update (Voyant): voyant-ros v1.0.1 and voyant-api 1.0.1, for the Voyant Carbon

D3 part C (22 Nov 2026) shipped the Metrolla contract on the old base and planned this update
instead of taking it. The owner then decided (22 Nov 2026) that Voyant's target is the **Carbon**
(the owner has one) and that the Carbon Dev Kit (Meadowlark) is not a target, and all sensor
vendors gave written consent to redistribution. The update is taken here.

## Where the fork is

| | Before (main 1061d30) | Now |
|---|---|---|
| voyant-ros | vendor `0d16e0a` (2026-02-25) | vendor **`v1.0.1` = `ef121af`** (2026-09-09), merged with Metrolla's changes re-applied |
| voyant-api (binary SDK) | 0.4.4, pinned by sha256 | **1.0.1** (`voyant-api-debian-v1.0.1.tar.gz`, sha256 `8058990f10d66670ea2aca3e4f34c132cdc66a34c4e755ec2d04685a0379ebd0`), pinned the same way |
| Cap'n Proto | 1.1.0 built from source, pinned by sha256 | not needed any more (vendor `fef04f0`); not downloaded |
| Sensor | Carbon Dev Kit (Meadowlark), `VoyantClient` | **Voyant Carbon**, `CarbonClient` |
| Firmware | any Meadowlark | **FPGA v1.5.6 / MCU v4.1.0** or later in the same line (voyant-api 0.17 and 1.0: "not compatible with FPGA v1.3.3 / MCU v2.6.0 or older") |
| Image tag | none pushed | first tag **`v1.0.1-metrolla.1`** (tags are `v<vendor version>-metrolla.<n>`; the image workflow refuses any other tag) |

`.github/vendor-watch.json` now records voyant-ros `ef121af` (v1.0.1) and voyant-sdk `b8bf974`
(its v1.0.1 tag) as the bases; on 23 Nov 2026 both were the vendors' newest.

**The owner must confirm the Carbon's firmware (FPGA 1.5.6 / MCU 4.1.0) before the first run, or
update it with Voyant's v7.1.X firmware updater (needs the sensor).** Older firmware is not
compatible with voyant-api 1.0.1; nothing in this repository can check or change it.

## Metrolla's changes on top of v1.0.1

- The contract (`docker/`, opt-in with `METROLLA_DRIVER_CONTRACT=1`), tests, docs, CI and the
  tag-only image workflow (as before; the image tag step now also refuses tags that are not
  `v<vendor version>-metrolla.<n>`). The vendor's `release-debian.yml` is removed: it would publish
  Debian packages (with voyant-api) from any `v*` tag of this fork.
- `Dockerfile`: voyant-api 1.0.1 pinned by version and sha256 (the vendor takes "latest").
- `src/main.cpp`, `src/sensor_driver.cpp` (the stop/fail fix, re-applied to the new client): SIGTERM
  sets `CarbonClient`'s stop flag like SIGINT (`setupSignalHandling(SIGTERM)`); rclcpp installs no
  signal handlers; no `rclcpp::spin` after the receive loop; the first-frame wait also ends on a ROS
  shutdown; a client that cannot start, an error while publishing, or a client that stops by itself
  exits 1. The vendor's v1.0.1 still binds SIGINT only, spins after the loop and exits 0 after an
  error while publishing (tests/regression/test_past_bugs.py checks all three files).
- **New in this update** (`src/sensor_driver.cpp`, `include/voyant_ros/sensor_params.hpp`): the
  node parameter `fpga_target_address`, passed to `CarbonConfig::setFpgaTargetAddr`. The vendor's
  node never sets it, so its client sends its register writes (push destination, clock
  discipline, SDL) to voyant-api's built-in default `192.168.1.128:1234` whatever the sensor's
  address: a Carbon at any other address would get none of them. Its default is that same
  built-in value, so a run with the vendor's `sensor_params.yaml` (legacy mode) is unchanged. The
  contract sets it to `SENSOR_IP:1234`.

## What changed for the output (every consumer of the topics)

From `git diff 0d16e0a v1.0.1` and the voyant-sdk release notes 0.4.5 to 1.0.1, checked against the
simulator in the smoke test:

| | Before (voyant-api 0.4.4, Meadowlark) | After (voyant-api 1.0.1, Carbon) |
|---|---|---|
| Point layout (`point_step` 48, `height` 1, `is_dense` false) | `x y z` f32 0/4/8, `v` f32 16, `snr` f32 20, `drop_reason` u8 24, `timestamp_nsecs` **int32** 28, `point_idx` u32 32 | `x y z` f32 0/4/8, `v` f32 16, `snr` f32 20, `drop_reason` u8 24, `timestamp_nsecs` **uint32** 28, **`azimuth_idx` u16 32, `elevation_idx` u16 34, `calibrated_reflectance` f32 36 (dB), `frame_index` u32 40**; `point_idx` gone |
| Points without a range | n/a | `x y z` NaN where the stored range is not positive (only in diagnostic mode, which the contract does not use) |
| Which points | `spn_filter` true: only `drop_reason` SUCCESS | `diagnostic_mode` false: only valid points (`drop_reason` 1); the simulator's frames: 23,168 points |
| Stamp | the host's clock when the frame began to arrive | **`timestamp_mode` 0 (vendor default): the frame's own start time from the sensor, whose clock the primary client disciplines to the host's** (Voyant claims under 100 µs); `timestamp_mode` 1: the ROS clock when the node converts the frame |
| Device metadata (`voyant_ros/msg/VoyantDeviceMetadata`, latched) | device id and version hashes | **a different message definition**: `device_id`, `product_id`, `serial_number`, `api_version`, `interface_contract_version`, `recording_api_version`, `firmware_version` (MCU), `hdl_version` (FPGA) as strings |
| Parameters | `point_format`, `spn_filter` | gone; new `stream_transport`, `observer_only`, `diagnostic_mode` (and Metrolla's `fpga_target_address`) |
| Network defaults | multicast `224.0.0.0:4444`, joined on `127.0.0.1` | bind `0.0.0.0:5678`, group `239.255.48.84`, interface `192.168.1.100`, **unicast** stream by default |
| Recording tools | the 0.4 frame format | `voyant_bin_to_mcap` / `voyant_mcap_to_bin` follow the 1.0 frame format; voyant-api ships `voyant_recording_migrate` for recordings made before 1.0 |

## What changed for the sensor: the client now writes to it

A primary (non-observer) `CarbonClient`, which is what the driver is (`observer_only` false), on
connect and while running:

- writes the sensor's `network.push_dest_ip` (unicast: back to this client; multicast: the group);
- disciplines the sensor FPGA's clock to the host's, continuously;
- checks the link (comms-health probes) and can send SDL configuration (each SDL change restarts
  laser linearisation, voyant-api 1.0.1); the driver itself sends no SDL command.

Exactly one primary client per sensor: a second Metrolla driver, or Voyant's visualizer, on the
same sensor must be an observer, which the contract does not offer. The sensor's register port is
1234 (voyant-api's default, not a contract setting).

## What the Carbon Dev Kit paths became

- `LIDAR_MODEL` `meadowlark`, `mdl`, `carbon-dev-kit`, `carbon-devkit`, `dev-kit`, `devkit` (any
  case, optional `voyant-` prefix, so also metrolla-edge's old `voyant-carbon-dev-kit`) are refused
  with the reason; `carbon` (`voyant-carbon`) is the one model.
- voyant-api 1.0.1 no longer ships the Meadowlark tools (`voyant_points_mock_stream`, the
  `VoyantClient` libraries); the smoke test uses `voyant_simulator` (Carbon) instead.
- Legacy mode (no contract) runs the released command, unchanged, but it reads the vendor's
  `config/sensor_params.yaml`, which is now v1.0.1's: the Carbon defaults above. A legacy container
  no longer listens for a Meadowlark stream on `224.0.0.0:4444`. No fleet runs this image and none
  was ever pushed (checked 22 Nov 2026), so nothing deployed changes.
- Contract defaults: `DATA_PORT` 5678 (was 4444), `TIME_MODE` `sensor` (was `host`, the only mode),
  group `239.255.48.84` (was `224.0.0.0`).

## arm64: still not possible (report to Voyant)

voyant-api 1.0.1's arm64 `voyant-api-dev` package still ships its C++ client libraries built for
x86-64: `libcarbon_client_cpp.a`, `libcarbon_config.a`, `libvoyant_playback.a` and
`libvoyant_data_recorder.a` (ELF machine 0x3e) beside aarch64 Rust libraries
(`libcarbon_client.a`, `liblogging_utils.a`, `libvoyant_playback_base.a`, `libvoyant_recorder.a`,
machine 0xb7); read from the archive members with `ar x` and the ELF header. 0.4.4 had the same
fault, and CI saw the arm64 link fail. The image is built for linux/amd64 only.

## Licence

voyant-ros and voyant-sdk are MIT (their notices stay as the vendor wrote them). voyant-api, which
the image installs and the node links statically, is **proprietary** (Voyant's notice:
"Redistribution of the binary is not permitted without prior written consent from Voyant
Photonics, Inc."; `/usr/share/doc/voyant-api/copyright` in the image, kept, with its
`copyright-compliance.txt`). Metrolla redistributes it in the driver image **with Voyant's written
consent (owner, 22 Nov 2026)**. Reverse engineering stays prohibited by the same notice.

## Not proven (needs hardware)

Proven against voyant-api 1.0.1's own Carbon simulator only (CI smoke test, and natively during
this update): every cloud is a frame the SDK's own client assembles from the same stream, point for
point; stamps per `TIME_MODE`; the client's register traffic reaches `SENSOR_IP:1234` (the
simulator answers the clock discipline). Not proven:

- the owner's Carbon's firmware version (must be FPGA 1.5.6 / MCU 4.1.0) and its address;
- a first frame from a real Carbon, real frame sizes, rates, drop reasons and packet loss;
- what the firmware does with the client's writes (push destination, clock discipline, SDL), and
  the clock discipline's real accuracy;
- whether the Carbon streams to a `DATA_PORT` other than 5678;
- two drivers (two Carbons) on one host;
- reconnecting after the sensor loses power;
- certification.
