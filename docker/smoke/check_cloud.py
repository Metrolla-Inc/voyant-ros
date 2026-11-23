#!/usr/bin/env python3
"""Subscribe to the driver's point cloud (inside the image, as the driver's uid) and match every
cloud with a frame the reference receiver (frame_dump, voyant-api's CarbonClient without ROS)
received from the same stream.

Usage: check_cloud.py --topic T --frame F --reference FILE --time-mode sensor|host
                      [--metadata-topic M] [--clouds N] [--timeout S]
Prints a JSON report; exit 0 when every check passed, 1 otherwise.

Checks: the topic carries sensor_msgs/PointCloud2 with frame F and voyant-ros v1.0.1's layout
(include/voyant_ros/point_types.hpp: x, y, z, v, snr float32 at 0, 4, 8, 16, 20; drop_reason uint8
at 24; timestamp_nsecs uint32 at 28; azimuth_idx, elevation_idx uint16 at 32, 34;
calibrated_reflectance float32 at 36; frame_index uint32 at 40; point_step 48; height 1;
is_dense false); after the first cloud, every cloud is one reference frame's valid points
(drop_reason 1), value for value and in order (FNV-1a over the same 37 bytes per point
frame_dump hashes); every published point is valid; the stamp follows TIME_MODE: "sensor" is the
frame's own time, exactly the reference receiver's (sec, nanosec), "host" is the host's clock when
the node converted the frame (within 0.1 s of the reference receiver's arrival time, after the
frame's own time and before the cloud arrived); clouds are 0.1 s apart; the latched device
metadata names the frame's device and voyant-api 1.0.1.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2

# name, offset, datatype (sensor_msgs/PointField: UINT8 2, UINT16 4, UINT32 6, FLOAT32 7), count
FIELDS = [("x", 0, 7, 1), ("y", 4, 7, 1), ("z", 8, 7, 1), ("v", 16, 7, 1), ("snr", 20, 7, 1),
          ("drop_reason", 24, 2, 1), ("timestamp_nsecs", 28, 6, 1), ("azimuth_idx", 32, 4, 1),
          ("elevation_idx", 34, 4, 1), ("calibrated_reflectance", 36, 7, 1),
          ("frame_index", 40, 6, 1)]
POINT_STEP = 48
VALID = 1  # DROP_REASON_VALID
API_VERSION = "1.0.1"


def records(data: bytes, width: int):
    """frame_dump's 37 bytes per point: the fields without the padding (12-15, 25-27, 44-47)."""
    for i in range(width):
        p = data[i * POINT_STEP:(i + 1) * POINT_STEP]
        yield p[0:12] + p[16:25] + p[28:44]


def fnv(recs) -> str:
    h = 1469598103934665603
    for rec in recs:
        for byte in rec:
            h = ((h ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def read_reference(path: str) -> list[dict]:
    frames = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            recv, sec, nsec, idx, n_all, n_ok, h_all, h_ok, device = line.split()
            frames.append({"recv": int(recv) * 1e-9, "sec": int(sec), "nsec": int(nsec),
                           "stamp": int(sec) + int(nsec) * 1e-9, "index": int(idx),
                           "n_all": int(n_all), "n_ok": int(n_ok), "fnv_all": h_all,
                           "fnv_ok": h_ok, "device": device})
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--time-mode", required=True, choices=["sensor", "host"])
    ap.add_argument("--metadata-topic")
    ap.add_argument("--clouds", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    got: list[tuple[float, PointCloud2]] = []
    meta = []
    rclpy.init()
    node = Node("voyant_smoke_check")
    qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                     history=HistoryPolicy.KEEP_LAST)
    node.create_subscription(PointCloud2, args.topic, lambda m: got.append((time.time(), m)), qos)
    if args.metadata_topic:
        from voyant_ros.msg import VoyantDeviceMetadata  # noqa: PLC0415 - from the workspace
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             history=HistoryPolicy.KEEP_LAST,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        node.create_subscription(VoyantDeviceMetadata, args.metadata_topic, meta.append, latched)
    deadline = time.time() + args.timeout
    while time.time() < deadline and (len(got) < args.clouds + 1 or
                                      (args.metadata_topic and not meta)):
        rclpy.spin_once(node, timeout_sec=0.2)
    node.destroy_node()
    rclpy.shutdown()
    time.sleep(1.0)  # let the reference receiver write the last frames

    problems: list[str] = []
    report: dict = {"topic": args.topic, "time_mode": args.time_mode, "received": len(got)}
    if len(got) < 2:
        problems.append(f"received {len(got)} clouds on {args.topic} in {args.timeout:.0f} s")
    reference = read_reference(args.reference)
    by_hash = {}
    for frame in reference:
        by_hash.setdefault(frame["fnv_ok"], frame)
    report["reference_frames"] = len(reference)
    stamps = []
    matched = []
    for arrival, msg in got[1:]:  # the first cloud may predate the reference receiver
        fields = [(f.name, f.offset, f.datatype, f.count) for f in msg.fields]
        report.setdefault("frame_id", msg.header.frame_id)
        report.setdefault("layout", {"width": msg.width, "height": msg.height,
                                     "point_step": msg.point_step, "fields": fields,
                                     "is_dense": msg.is_dense})
        if msg.header.frame_id != args.frame:
            problems.append(f"frame_id {msg.header.frame_id!r}, expected {args.frame!r}")
        if fields != FIELDS or msg.point_step != POINT_STEP or msg.height != 1 or msg.is_dense:
            problems.append(f"layout {fields} point_step {msg.point_step} height {msg.height} "
                            f"is_dense {msg.is_dense}")
            break
        data = bytes(msg.data)
        if len(data) != msg.width * POINT_STEP:
            problems.append(f"{len(data)} bytes of data for width {msg.width}")
            continue
        if any(data[i * POINT_STEP + 24] != VALID for i in range(msg.width)):
            problems.append("a point with drop_reason other than valid (1) was published")
        digest = fnv(records(data, msg.width))
        frame = by_hash.get(digest)
        sec, nsec = msg.header.stamp.sec, msg.header.stamp.nanosec
        stamp = sec + nsec * 1e-9
        stamps.append(stamp)
        if frame is None:
            problems.append(f"cloud of {msg.width} points stamped {stamp:.3f} matches no reference "
                            "frame")
            continue
        matched.append({"index": frame["index"], "points": msg.width, "of": frame["n_all"],
                        "stamp_minus_frame_time_s": round(stamp - frame["stamp"], 6),
                        "stamp_minus_reference_arrival_s": round(stamp - frame["recv"], 4),
                        "arrival_lag_s": round(arrival - stamp, 4)})
        if msg.width != frame["n_ok"]:
            problems.append(f"cloud of {msg.width} points, the frame had {frame['n_ok']} valid")
        if args.time_mode == "sensor":
            if (sec, nsec) != (frame["sec"], frame["nsec"]):
                problems.append(f"stamp {sec}.{nsec:09d} is not frame {frame['index']}'s own time "
                                f"{frame['sec']}.{frame['nsec']:09d}")
        else:
            if abs(stamp - frame["recv"]) > 0.1:
                problems.append(f"host stamp {stamp:.3f} is {stamp - frame['recv']:.3f} s from the "
                                f"reference receiver's arrival of frame {frame['index']}")
            if not 0.0 < stamp - frame["stamp"] < 1.0:
                problems.append(f"host stamp {stamp:.3f} is not after frame {frame['index']}'s own "
                                f"time {frame['stamp']:.3f}")
        if not -0.01 <= arrival - stamp <= 1.0:
            problems.append(f"stamp {stamp:.3f} is {arrival - stamp:.3f} s before the cloud arrived")
    report["matched"] = matched
    gaps = [round(b - a, 4) for a, b in zip(stamps, stamps[1:])]
    report["stamp_gaps_s"] = gaps
    if any(abs(g - 0.1) > 0.03 for g in gaps):
        problems.append(f"clouds not 0.1 s apart: {gaps}")
    if args.metadata_topic:
        if not meta:
            problems.append(f"no device metadata on {args.metadata_topic}")
        else:
            m = meta[0]
            report["metadata"] = {
                "device_id": m.device_id, "frame_id": m.header.frame_id,
                "product_id": m.product_id, "serial_number": m.serial_number,
                "api_version": m.api_version,
                "interface_contract_version": m.interface_contract_version,
                "firmware_version": m.firmware_version, "hdl_version": m.hdl_version}
            devices = {f["device"] for f in reference}
            if m.device_id not in devices or m.header.frame_id != args.frame or \
                    m.api_version != API_VERSION:
                problems.append(f"metadata {report['metadata']}, reference devices {devices}, "
                                f"expected api {API_VERSION}")
    report["problems"] = problems
    print(json.dumps(report, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
