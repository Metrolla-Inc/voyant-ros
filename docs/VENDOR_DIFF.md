# Vendor update plan (Voyant): not taken in D3, written down instead

D3 asked for each driver fork to move to its vendor's current release where that is public and
safe without hardware. For voyant-ros it is public (driver MIT, Voyant-Photonics/voyant-ros; SDK
examples MIT, Voyant-Photonics/voyant-sdk) but **not safe without hardware**, so this fork ships
the Metrolla contract on its current base and this plan. The owner decides; the steps below are
ready to run.

## Where the fork is

| | Fork (this change) | Vendor now |
|---|---|---|
| voyant-ros | base `0d16e0a` (2026-02-25, "Update dockerfile for v0.4.4 voyant-api release"), merged into the fork 2026-05-06 (b074d97) | `v1.0.1` = `ef121af` (2026-09-09): 28 commits, 19 tags ahead |
| voyant-api (SDK, binary) | **0.4.4**, pinned with its sha256 since this change (before: whatever release was latest, which broke every build since May 2026) | **1.0.1** (`voyant-api-debian-v1.0.1.tar.gz`, sha256 `8058990f10d66670ea2aca3e4f34c132cdc66a34c4e755ec2d04685a0379ebd0`) |
| Sensor family | Meadowlark (Carbon Dev Kit), `VoyantClient` | Carbon, `CarbonClient` |

Metrolla's own changes on top of the base: the image workflow (b0f93a6, 1afe4be, 19b0168; replaced
by this change), `docker-compose.yaml` (b0f93a6, unused), vendor-watch (#1), and this change (the
contract, docker/, tests/, docs/, the stop/fail fix in `src/main.cpp` and `src/sensor_driver.cpp`,
the pinned SDK and Cap'n Proto). `.github/vendor-watch.json` watches both repositories (voyant-ros
from `0d16e0a`, voyant-sdk from its `v0.4.4` tag), so the weekly issue keeps saying "behind",
correctly.

## What the update changes (anything that can change output or touch the sensor)

From `git diff 0d16e0a v1.0.1` and the voyant-sdk release notes 0.4.5 to 1.0.1:

- **Different sensor generation.** The driver moves from `VoyantClient` (Meadowlark point stream)
  to `CarbonClient`. voyant-api 0.15 removed the Meadowlark tools; 0.17 and 1.0 state "Not
  compatible with FPGA v1.3.3 / MCU v2.6.0 or older" and need **FPGA v1.5.6 / MCU v4.1.0** (v7.1.X
  updater). A Meadowlark unit stops working after the update; a Carbon unit does not work before.
- **The client writes to the sensor.** A primary (non-observer) client points the sensor's
  `network.push_dest_ip` at its multicast group on connect (0.13), disciplines the sensor FPGA's
  clock to the host's continuously (0.12.1; always on from 0.13), and sends SDL configuration
  (each SDL change restarts laser linearisation, 1.0.1). Only one primary client per sensor; others
  must be `observer_only`.
- **Network defaults:** `binding_address` `0.0.0.0:5678` (was `:4444`), multicast group
  `239.255.48.84` (was `224.0.0.0`), `interface_address` `192.168.1.100` (was `127.0.0.1`),
  `stream_transport` `unicast` by default (the sensor sends to this client only) or `multicast`.
- **Stamps:** frames carry the FPGA timestamp by default, the FPGA clock being disciplined to the
  host's (sub-100 µs claimed); 0.4.4 stamped with the host's clock on arrival.
- **Cloud layout:** fields become `x y z v snr drop_reason timestamp_nsecs(uint32) azimuth_idx
  elevation_idx calibrated_reflectance frame_index` (was `... timestamp_nsecs(int32) point_idx`,
  point_step 48); `point_format` and `spn_filter` parameters are gone; new `observer_only`,
  `diagnostic_mode`, `stream_transport`.
- **Metadata message:** `VoyantDeviceMetadata` changes from version hashes to strings (product id,
  serial, api, interface contract, firmware, HDL versions): a different message type for any
  consumer.
- Upstream also fixed "errors exit 0" the same way as this fork (`throw;`, `return 1`, 16f64bb), and
  the first-frame wait now sleeps; SIGTERM is still not bound and `rclcpp::spin` still follows the
  loop, so this fork's stop fix still applies.

## arm64: not possible with Voyant's packages (report to Voyant)

voyant-api's arm64 `voyant-api-dev` package ships its C++ client libraries built for x86-64: in
0.4.4 `libvoyant_client.a`, `libvoyant_protos.a`, `libvoyant_playback.a` and
`libvoyant_data_recorder.a`; in 1.0.1 still `libcarbon_client_cpp.a`, `libcarbon_config.a`,
`libvoyant_playback.a` and `libvoyant_data_recorder.a` (checked with `ar x` and `file`, the ELF
machine field only). The arm64 link fails ("Relocations in generic ELF (EM: 62)", CI run of this
change), so the image is built for linux/amd64 only, before and after the update, until Voyant
fixes the package. The Rust-side libraries and the tools are aarch64.

## Why not now

1. It swaps which hardware works. Which Voyant units Metrolla has, or will deploy, and their
   firmware, is not recorded anywhere this lane can read (no fleet has ever run this driver).
2. The new client changes the sensor's own settings (push destination, clock, SDL). Proving that
   is right needs a sensor; a simulator cannot show what the firmware does with it.
3. The output changes for every consumer (fields, stamps, metadata type), which the product's
   model table and any downstream code must follow in the same release.

## Steps when the owner says go (target: vendor v1.0.1, SDK 1.0.1)

1. Record the target units' FPGA/MCU versions; they must be FPGA 1.5.6 / MCU 4.1.0.
2. `git merge v1.0.1` (vendor remote `upstream`); keep this fork's `docker/`, `tests/`, `docs/`,
   workflows; re-apply the SIGTERM part of the stop fix to the new `main.cpp`/`sensor_driver.cpp`
   (`CarbonClient::setupSignalHandling(SIGTERM)`, `SignalHandlerOptions::None`, no spin after the
   loop).
3. Dockerfile: `VOYANT_API_VERSION=1.0.1`, `VOYANT_API_SHA256=8058990f...`.
4. Contract: `LIDAR_MODEL` `carbon` (refuse `meadowlark` with the reason), default `DATA_PORT` 5678,
   `HOST_IP` -> `interface_address`, `TIME_MODE` `host` (FPGA clock disciplined to the host) and
   decide whether `sensor` means anything, a variable for `stream_transport` only if the product
   needs multicast; `observer_only` false (the driver is the primary client), and say so in the
   hand-off (no second primary client on a unit). Regenerate the contract goldens; legacy goldens
   stay the released ones.
5. Smoke: voyant-api 1.0.1 ships `voyant_simulator` (Carbon); the same reference-receiver check
   with `CarbonClient`.
6. With a sensor (needs hardware): first frame, stamps against the host clock, SDL settings left as
   the owner wants them, push destination, two drivers on one host, reconnect after power loss.
7. Update `.github/vendor-watch.json` bases (voyant-ros `ef121af`, voyant-sdk `v1.0.1` =
   `b8bf974`), the model table (part E), and the generator hand-off.

## Licence (owner and counsel)

voyant-ros and the voyant-sdk repository are MIT. voyant-api, the library this driver links and
the image installs (`voyant-api`, `voyant-api-dev` .debs from the voyant-sdk releases), is
**proprietary**: "Source: proprietary - not publicly available"; "The binary distributions of
this software may be used free of charge with Voyant Photonics products ... Reverse engineering,
decompilation, disassembly ... is prohibited. Redistribution of the binary is not permitted
without prior written consent from Voyant Photonics, Inc." (`/usr/share/doc/voyant-api/copyright`,
same text in 0.4.4 and 1.0.1). A driver image pushed to ghcr.io, and installed on customer units,
contains that binary (statically linked into `voyant_sensor_node`, and the .debs' tools). That is
redistribution: **written consent from Voyant is needed before an image is pushed or shipped**
(owner decision; D10 covers licence terms). Building the image in CI and running the SDK's own
simulator in the smoke test is use of the downloaded binary, not redistribution; nothing is
pushed by pull requests.
