#!/usr/bin/env python3
"""Subscribe to the driver's point cloud (inside the image, as the driver's uid) and match every
cloud with a frame the reference receiver (frame_dump, the Voyant client without ROS) assembled
from the same multicast stream.

Usage: check_cloud.py --topic T --frame F --reference FILE [--metadata-topic M] [--clouds N]
                      [--timeout S]
Prints a JSON report; exit 0 when every check passed, 1 otherwise.

Checks: the topic carries sensor_msgs/PointCloud2 with frame F and the MDL_STANDARD layout (x, y,
z, v, snr float32 at 0, 4, 8, 16, 20; drop_reason uint8 at 24; timestamp_nsecs int32 at 28;
point_idx uint32 at 32; point_step 48; height 1); after the first cloud, every cloud is one
reference frame's points with drop_reason SUCCESS, value for value (FNV-1a over the packed
points) and in order; the stamp is the host's clock when the frame arrived (within 50 ms of the
reference receiver's own stamp of that frame, and before the cloud arrived); clouds are 0.1 s
apart; the latched device metadata names the frame's device.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2

FIELDS = [("x", 0, 7, 1), ("y", 4, 7, 1), ("z", 8, 7, 1), ("v", 16, 7, 1), ("snr", 20, 7, 1),
          ("drop_reason", 24, 2, 1), ("timestamp_nsecs", 28, 5, 1), ("point_idx", 32, 6, 1)]
POINT = struct.Struct("<3f4x2fB3xiI12x")  # the ROS point, 48 bytes
PACKED = struct.Struct("<5fBiI")  # frame_dump's packing, 29 bytes
SUCCESS = 1


def fnv(points) -> str:
    h = 1469598103934665603
    for p in points:
        for byte in PACKED.pack(*p):
            h = ((h ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def read_reference(path: str) -> list[dict]:
    frames = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            recv, sec, nsec, idx, n_all, n_ok, h_all, h_ok, device = line.split()
            frames.append({"recv": int(recv) * 1e-9, "stamp": int(sec) + int(nsec) * 1e-9,
                           "index": int(idx), "n_all": int(n_all), "n_ok": int(n_ok),
                           "fnv_all": h_all, "fnv_ok": h_ok, "device": device})
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--metadata-topic")
    ap.add_argument("--clouds", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    got: list[tuple[float, PointCloud2]] = []
    meta = []
    rclpy.init()
    node = Node("voyant_smoke_check")
    qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST)
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
    report: dict = {"topic": args.topic, "received": len(got)}
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
        if fields != FIELDS or msg.point_step != POINT.size or msg.height != 1:
            problems.append(f"layout {fields} point_step {msg.point_step} height {msg.height}")
            break
        points = list(POINT.iter_unpack(bytes(msg.data)))
        if len(points) != msg.width:
            problems.append(f"{len(points)} points in the data, width {msg.width}")
        if any(p[5] != SUCCESS for p in points):
            problems.append("a point with drop_reason other than SUCCESS was published")
        digest = fnv(points)
        frame = by_hash.get(digest)
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        stamps.append(stamp)
        if frame is None:
            problems.append(f"cloud of {len(points)} points stamped {stamp:.3f} matches no reference "
                            "frame")
            continue
        matched.append({"index": frame["index"], "points": len(points), "of": frame["n_all"],
                        "stamp_vs_reference_s": round(stamp - frame["stamp"], 4),
                        "arrival_lag_s": round(arrival - stamp, 4)})
        if abs(stamp - frame["stamp"]) > 0.05:
            problems.append(f"stamp {stamp:.3f} is {stamp - frame['stamp']:.3f} s from the reference "
                            f"receiver's stamp of frame {frame['index']}")
        if not 0.0 <= arrival - stamp <= 1.0:
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
            report["metadata"] = {"device_id": m.device_id, "frame_id": m.header.frame_id}
            devices = {f["device"] for f in reference}
            if m.device_id not in devices or m.header.frame_id != args.frame:
                problems.append(f"metadata {report['metadata']}, reference devices {devices}")
    report["problems"] = problems
    print(json.dumps(report, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
