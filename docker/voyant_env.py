#!/usr/bin/env python3
"""Environment contract of the Metrolla Voyant driver image (docs/METROLLA_DRIVER_CONTRACT.md).

Reads the container's environment, refuses to start on any value it does not understand, writes
the node's ROS parameters file and (subcommand ``run``) execs ``voyant_sensor_node`` with it, so
signals reach the driver itself.

Standard library only: it runs in the image before the driver and in the unit and regression tests
outside the image, where there is no ROS.

Subcommands:
  run              validate, write the parameters file, exec the driver (the image's default)
  check [--json]   validate and print what ``run`` would write and exec; nothing is started
  selfcheck        check the contract against the driver source and binary in this image (run at
                   image build)

Exit codes: 0 ok; 1 refused (a value this image does not understand, nowhere to write, or an
unknown command).
"""

from __future__ import annotations

import difflib
import ipaddress
import json
import os
import re
import socket
import sys
from dataclasses import dataclass, field
from typing import Callable, Mapping

PROG = "voyant-driver"
EXIT_OK = 0
EXIT_REFUSED = 1

WORKSPACE = "/ros2_ws"
DRIVER_NODE = WORKSPACE + "/install/voyant_ros/lib/voyant_ros/voyant_sensor_node"
# The driver source in the image (COPY . ./voyant-ros); selfcheck reads its declared parameters.
DRIVER_SOURCE = WORKSPACE + "/src/voyant-ros/src/sensor_driver.cpp"
DEFAULT_RUN_DIR = "/tmp/voyant"
DEFAULT_ROS_HOME = "/tmp/ros"

DEFAULT_DATA_PORT = 4444  # the Meadowlark stream's port (vendor config/sensor_params.yaml)
MULTICAST_GROUP = "224.0.0.0"  # the Meadowlark stream's multicast group (vendor config)
MIN_PORT = 1024  # the driver runs as a non-root uid with no capability: no port below 1024
MAX_PORT = 65535
DEFAULT_FRAME_ID = "voyant_sensor"  # the vendor config's frame_id (what the legacy image uses)
NODE_TOPIC = "point_cloud"  # the node's publisher name (src/sensor_driver.cpp), relative
DEFAULT_TOPIC = NODE_TOPIC  # /point_cloud, or /<SENSOR_ID>/point_cloud

TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off")


# --------------------------------------------------------------------------------------------
# Models. The image is built with voyant-api 0.4.4 (Dockerfile VOYANT_API_VERSION), whose
# VoyantClient receives the Meadowlark point stream: Voyant's Carbon Dev Kit. Voyant's production
# Carbon sensors need voyant-api 0.5 or later (CarbonClient), i.e. the vendor update in
# docs/VENDOR_UPDATE_PLAN.md.
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Model:
    key: str
    label: str
    names: tuple[str, ...]  # accepted spellings after normalising


MODELS_LIST = (
    Model("meadowlark", "Voyant Carbon Dev Kit (Meadowlark, device id MDL-nnn)",
          ("meadowlark", "mdl", "carbon-dev-kit", "carbon-devkit")),
)
MODELS = {name: m for m in MODELS_LIST for name in m.names}
# Names that are Voyant products this image cannot receive, with why.
NOT_THIS_IMAGE = {
    "carbon": "Voyant Carbon sensors need voyant-api 0.5 or later (CarbonClient); this image is "
              "built with voyant-api 0.4.4, which receives the Carbon Dev Kit (Meadowlark) stream "
              "only (docs/VENDOR_UPDATE_PLAN.md)",
}


def normalise_model(value: str) -> str:
    """LIDAR_MODEL to a lookup key: any case, '_', '-' or ' ', optional 'voyant-' prefix."""
    key = re.sub(r"[_ ]", "-", value.strip().lower())
    if key.startswith("voyant-"):
        key = key[len("voyant-"):]
    return key


def model_list() -> str:
    return ", ".join(name for m in MODELS_LIST for name in m.names)


# --------------------------------------------------------------------------------------------
# Environment variables.
# --------------------------------------------------------------------------------------------

REQUIRED = ("LIDAR_MODEL", "SENSOR_IP")
OPTIONAL = ("SENSOR_ID", "DATA_PORT", "DEVICE_PORT", "HOST_IP", "FRAME_ID", "POINTCLOUD_TOPIC",
            "TIME_MODE", "PUBLISH_SCAN", "VOYANT_RUN_DIR")
COMPAT = ("DUAL_LIDAR",)  # read only to refuse a dual-lidar request; one container per sensor
KNOWN = REQUIRED + OPTIONAL + COMPAT
# Names the vendor's parameters, the repo's compose file or other drivers use for the same thing.
ALIASES = {
    "LIDAR_TYPE": "LIDAR_MODEL", "SENSOR_TYPE": "LIDAR_MODEL", "SENSOR_MODEL": "LIDAR_MODEL",
    "LIDAR_IP": "SENSOR_IP", "DEVICE_IP": "SENSOR_IP",
    "SENSOR_PORT": "DATA_PORT", "LIDAR_PORT": "DATA_PORT", "BINDING_ADDRESS": "DATA_PORT",
    "INTERFACE_ADDRESS": "HOST_IP", "HOST_ADDRESS": "HOST_IP", "LIDAR_INTERFACE_DEV": "HOST_IP",
    "ROS_FRAME_ID": "FRAME_ID", "TOPIC_NAME": "POINTCLOUD_TOPIC", "TIMESTAMP_MODE": "TIME_MODE",
    "USE_LIDAR_CLOCK": "TIME_MODE",
}
# Variables of the released image that mean nothing in contract mode, with why.
NOT_IN_CONTRACT = {
    "VIZ_BRIDGE": "contract mode starts the driver only, without a Foxglove bridge; unset it",
}
# Any other variable that starts with one of these is refused as a probable typo. METROLLA_ is not
# among them: the stack may pass its own METROLLA_* variables to every service.
STRICT_PREFIXES = ("VOYANT_", "LIDAR_", "SENSOR_", "DATA_", "DEVICE_", "HOST_", "TIME_", "FRAME_",
                   "POINTCLOUD_", "PUBLISH_", "BINDING_", "INTERFACE_", "MULTICAST_", "TOPIC_",
                   "TIMESTAMP_", "USE_LIDAR", "ROS_FRAME", "DUAL_")

ACCEPTED = {
    "LIDAR_MODEL": f"a Voyant model this image receives: {model_list()}",
    "SENSOR_IP": "the sensor's own IPv4 address, for example 192.168.20.20",
    "SENSOR_ID": "a lowercase letter then up to 30 lowercase letters, digits or single '_'",
    "DATA_PORT": f"a UDP port {MIN_PORT}-{MAX_PORT} (default {DEFAULT_DATA_PORT})",
    "DEVICE_PORT": "unset: this driver only receives the sensor's stream and has no device port",
    "HOST_IP": "the host's IPv4 address on the sensor network, optionally with /1-/32",
    "FRAME_ID": f"letters, digits and '_', not starting with a digit (default {DEFAULT_FRAME_ID})",
    "POINTCLOUD_TOPIC": f"a ROS topic name (default {DEFAULT_TOPIC})",
    "TIME_MODE": "host (the default and only mode of this image)",
    "PUBLISH_SCAN": "a false value (0, false, no, off): this driver has no laser scan output",
    "VOYANT_RUN_DIR": f"an absolute directory path (default {DEFAULT_RUN_DIR})",
}

_PORT_RE = re.compile(r"[1-9][0-9]{0,4}")
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
_IPV4_RE = re.compile(rf"{_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}")
_PREFIX_RE = re.compile(r"[1-9]|[12][0-9]|3[0-2]")
_SENSOR_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,30}")  # metrolla-edge deployment schema, sensors[].id
_FRAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_TOPIC_RE = re.compile(r"/?[A-Za-z][A-Za-z0-9_]*(?:/[A-Za-z][A-Za-z0-9_]*)*")


def route_source(sensor_ip: str, port: int) -> str:
    """The host's own address on the route to the sensor (a UDP connect sends nothing)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((sensor_ip, port))
        return sock.getsockname()[0]


@dataclass
class Settings:
    model: Model
    sensor_ip: str
    sensor_id: str | None
    data_port: int
    host_ip: str | None  # as given (may carry a prefix)
    interface_address: str  # the address the driver joins the multicast group on
    interface_from: str  # "HOST_IP" or "route to SENSOR_IP"
    frame_id: str
    topic: str
    time_mode: str
    run_dir: str
    sources: dict[str, str] = field(default_factory=dict)  # variable -> "env" or "default"

    @property
    def namespace(self) -> str:
        return "/" + self.sensor_id if self.sensor_id else "/"

    @property
    def resolved_topic(self) -> str:
        if self.topic.startswith("/"):
            return self.topic
        return self.namespace.rstrip("/") + "/" + self.topic

    @property
    def params_path(self) -> str:
        return self.run_dir.rstrip("/") + "/params.yaml"


class Refused(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def shown(value: str) -> str:
    """A value as it appears in a message: quoted, escaped, cut short."""
    text = value if len(value) <= 64 else value[:61] + "..."
    if text.isprintable() and text.isascii():
        return "'" + text + "'"
    return ascii(text)


def _ipv4(name: str, value: str, problems: list[str], what: str) -> ipaddress.IPv4Address | None:
    if not _IPV4_RE.fullmatch(value):
        problems.append(f"{name}={shown(value)} is not an IPv4 address; accepted: {what} in dotted "
                        "decimal without leading zeros, for example 192.168.20.20")
        return None
    address = ipaddress.IPv4Address(value)
    if address.is_unspecified or address.is_multicast or address.is_reserved or \
            address == ipaddress.IPv4Address("255.255.255.255"):
        problems.append(f"{name}={shown(value)} is not a unicast address; accepted: {what}, not "
                        "0.0.0.0, multicast (224-239.x.x.x), 240.x.x.x and above or broadcast")
        return None
    return address


def _false_only(name: str, value: str, problems: list[str], why: str) -> None:
    word = value.lower()
    if word in FALSE_WORDS:
        return
    if word in TRUE_WORDS:
        problems.append(f"{name}={shown(value)} is not supported by this image; accepted: unset or "
                        f"a false value ({', '.join(FALSE_WORDS)}). {why}")
        return
    problems.append(f"{name}={shown(value)} is not a boolean; accepted: unset or a false value "
                    f"({', '.join(FALSE_WORDS)}, any case). {why}")


def parse(env: Mapping[str, str],
          route: Callable[[str, int], str] = route_source) -> Settings:
    """Validate the environment. Raises Refused with one line per problem. ``route`` finds the
    host's address towards the sensor when HOST_IP is not set (only when everything else is
    valid)."""
    problems: list[str] = []
    sources: dict[str, str] = {}

    def get(name: str) -> str | None:
        value = env.get(name)
        if value is None:
            sources[name] = "default"
            return None
        sources[name] = "env"
        return value

    # The switch itself (voyant_entrypoint.sh hands over for any value but unset, empty and 0).
    switch = env.get("METROLLA_DRIVER_CONTRACT")
    if switch is not None and switch != "1":
        problems.append(f"METROLLA_DRIVER_CONTRACT={shown(switch)} is not a contract version this "
                        "image knows; accepted: 1 (this contract), or unset, empty or 0 for the "
                        "image's released behaviour")

    # Empty values: a template that rendered nothing must not fall back to a default silently.
    for name in REQUIRED + OPTIONAL:  # an empty DUAL_LIDAR means false, as in the Seyond image
        if name in env and env[name] == "" and name != "DEVICE_PORT":
            problems.append(f"{name} is set but empty; accepted: {ACCEPTED[name]}"
                            + ("" if name in REQUIRED else ", or unset it for the default"))
    present = {k: v for k, v in env.items() if k in KNOWN and v != ""}

    # LIDAR_MODEL
    model = None
    raw = present.get("LIDAR_MODEL")
    if "LIDAR_MODEL" not in env:
        problems.append("LIDAR_MODEL is not set; accepted (any case, '_' or '-', optional "
                        f"'voyant-' prefix): {model_list()}")
    elif raw is not None:
        key = normalise_model(raw)
        if key in MODELS:
            model = MODELS[key]
        elif key in NOT_THIS_IMAGE:
            problems.append(f"LIDAR_MODEL={shown(raw)} is not supported by this image: "
                            f"{NOT_THIS_IMAGE[key]}; accepted: {model_list()}")
        else:
            problems.append(f"LIDAR_MODEL={shown(raw)} is not a Voyant model this image knows; "
                            "accepted (any case, '_' or '-', optional 'voyant-' prefix): "
                            f"{model_list()}")
    get("LIDAR_MODEL")

    # SENSOR_IP
    sensor = None
    if "SENSOR_IP" not in env:
        problems.append("SENSOR_IP is not set; accepted: the sensor's own IPv4 address, for example "
                        "192.168.20.20 (the Meadowlark factory address)")
    elif "SENSOR_IP" in present:
        sensor = _ipv4("SENSOR_IP", present["SENSOR_IP"], problems, "the sensor's own IPv4 address")
    get("SENSOR_IP")

    # SENSOR_ID
    sensor_id = None
    value = get("SENSOR_ID")
    if value:
        if not _SENSOR_ID_RE.fullmatch(value) or "__" in value:
            problems.append(f"SENSOR_ID={shown(value)} is not a sensor id; accepted: 1-31 characters, "
                            "a lowercase letter then lowercase letters, digits or single '_' (it "
                            "becomes the node's ROS namespace)")
        else:
            sensor_id = value

    # DATA_PORT: the port the sensor sends its multicast stream to.
    data_port = DEFAULT_DATA_PORT
    value = get("DATA_PORT")
    if value:
        if not _PORT_RE.fullmatch(value) or not MIN_PORT <= int(value) <= MAX_PORT:
            problems.append(f"DATA_PORT={shown(value)} is not a usable UDP port; accepted: a whole "
                            f"number {MIN_PORT}-{MAX_PORT} without leading zeros (default "
                            f"{DEFAULT_DATA_PORT}, the port the sensor streams to)")
        else:
            data_port = int(value)

    # DEVICE_PORT: the driver never talks to the sensor.
    value = get("DEVICE_PORT")
    if value is not None:
        problems.append(f"DEVICE_PORT={shown(value)} is not supported by this image; accepted: "
                        "unset. The Voyant driver only receives the sensor's multicast stream on "
                        "DATA_PORT; it does not use the sensor's control interface")

    # HOST_IP: the host's address on the sensor network, where the driver joins the group.
    host_ip = None
    host = None
    value = get("HOST_IP")
    if value:
        address, _, prefix = value.partition("/")
        if "/" in value and not _PREFIX_RE.fullmatch(prefix):
            problems.append(f"HOST_IP={shown(value)} has no usable prefix length; accepted: the "
                            "host's IPv4 address on the sensor network, optionally with /1-/32, for "
                            "example 192.168.20.100/24")
        else:
            host = _ipv4("HOST_IP", address, problems,
                         "the host's IPv4 address on the sensor network (optionally with /1-/32)")
            if host is not None:
                host_ip = value
                if sensor is not None and host == sensor:
                    problems.append(f"HOST_IP={shown(value)} is the sensor's own address; accepted: "
                                    "the host's address on the sensor network")
                    host_ip = None
                elif sensor is not None and prefix:
                    network = ipaddress.IPv4Interface(value).network
                    if sensor not in network:
                        problems.append(f"HOST_IP={shown(value)}: the sensor at {sensor} is not in "
                                        f"the host's subnet {network}; accepted: a host address in "
                                        "the sensor's subnet")
                        host_ip = None

    # FRAME_ID
    frame_id = DEFAULT_FRAME_ID
    value = get("FRAME_ID")
    if value:
        if not _FRAME_RE.fullmatch(value):
            problems.append(f"FRAME_ID={shown(value)} is not a frame id; accepted: 1-64 characters, "
                            "letters, digits and '_', not starting with a digit (default "
                            f"{DEFAULT_FRAME_ID})")
        else:
            frame_id = value

    # POINTCLOUD_TOPIC
    topic = DEFAULT_TOPIC
    value = get("POINTCLOUD_TOPIC")
    if value:
        if not _TOPIC_RE.fullmatch(value) or "__" in value or len(value) > 200:
            problems.append(f"POINTCLOUD_TOPIC={shown(value)} is not a ROS topic name; accepted: "
                            "'/'-separated tokens of letters, digits and single '_', each starting "
                            "with a letter, absolute (/x) or relative to the SENSOR_ID namespace "
                            f"(default {DEFAULT_TOPIC})")
        else:
            topic = value

    # TIME_MODE. voyant-api 0.4.4's client stamps each frame with the host's clock when the frame
    # starts to arrive; it passes no sensor clock through (Voyant: do not use the message stamps
    # of Meadowlark units).
    time_mode = "host"
    value = get("TIME_MODE")
    if value:
        word = value.lower()
        if word in ("sensor", "gps", "ptp", "ntp"):
            problems.append(f"TIME_MODE={shown(value)} is not supported by this image; accepted: "
                            "host (the default). voyant-api 0.4.4 stamps every frame with the "
                            "host's clock as it arrives and passes no sensor clock through; keep "
                            "the host's clock synchronised instead")
        elif word != "host":
            problems.append(f"TIME_MODE={shown(value)} is not a time mode; accepted, any case: host "
                            "(stamp with the host's clock as the frame arrives, the default and "
                            "only mode of this image)")

    # PUBLISH_SCAN: the driver has no LaserScan output.
    value = get("PUBLISH_SCAN")
    if value:
        _false_only("PUBLISH_SCAN", value, problems,
                    "voyant_ros publishes point clouds only, no laser scan")

    # VOYANT_RUN_DIR
    run_dir = DEFAULT_RUN_DIR
    value = get("VOYANT_RUN_DIR")
    if value:
        if not value.startswith("/") or not re.fullmatch(r"/[A-Za-z0-9_./-]*", value) or \
                "/../" in value + "/":
            problems.append(f"VOYANT_RUN_DIR={shown(value)} is not an absolute directory path; "
                            "accepted: an absolute path of letters, digits, '_', '.', '-' and '/' "
                            f"(default {DEFAULT_RUN_DIR})")
        else:
            run_dir = value

    # DUAL_LIDAR (the Seyond image's flag)
    value = env.get("DUAL_LIDAR")
    if value:
        _false_only("DUAL_LIDAR", value, problems, "Run one container per Voyant sensor")

    # Unknown variables under this image's prefixes.
    for name in sorted(env):
        if name in KNOWN or not (name.startswith(STRICT_PREFIXES) or name in NOT_IN_CONTRACT):
            continue
        if name in NOT_IN_CONTRACT:
            hint = f" ({NOT_IN_CONTRACT[name]})"
        else:
            close = [ALIASES[name]] if name in ALIASES else \
                difflib.get_close_matches(name, KNOWN, n=1, cutoff=0.6)
            hint = f" (did you mean {close[0]}?)" if close else ""
        problems.append(f"{name} is not a variable this image knows{hint}; accepted: "
                        f"{', '.join(KNOWN)}")

    # Where to join the stream: HOST_IP, or the host's address on the route to the sensor.
    interface_address, interface_from = "", ""
    if not problems:
        assert sensor is not None
        if host is not None:
            interface_address, interface_from = str(host), "HOST_IP"
        else:
            try:
                interface_address = route(str(sensor), data_port)
                interface_from = "route to SENSOR_IP"
            except OSError as exc:
                problems.append(f"HOST_IP is not set and this host has no route to SENSOR_IP="
                                f"{sensor} ({exc.strerror or exc}); accepted: set HOST_IP to the "
                                "host's address on the sensor network, for example "
                                "192.168.20.100/24")

    if problems:
        raise Refused(problems)
    assert model is not None and sensor is not None
    return Settings(model=model, sensor_ip=str(sensor), sensor_id=sensor_id, data_port=data_port,
                    host_ip=host_ip, interface_address=interface_address,
                    interface_from=interface_from, frame_id=frame_id, topic=topic,
                    time_mode=time_mode, run_dir=run_dir, sources=sources)


# --------------------------------------------------------------------------------------------
# Rendering: the node's parameters (src/sensor_driver.cpp, getParams) as a ROS parameters file.
# --------------------------------------------------------------------------------------------


def parameters(s: Settings) -> list[tuple[str, str, object, str]]:
    """(name, type, value, why) for every parameter the node declares, in declaration order."""
    return [
        ("binding_address", "string", f"0.0.0.0:{s.data_port}",
         "DATA_PORT on every address (the multicast stream)"),
        ("multicast_group", "string", MULTICAST_GROUP, "the Meadowlark stream's group"),
        ("interface_address", "string", s.interface_address,
         f"join the group on this host address ({s.interface_from})"),
        ("spn_filter", "bool", True,
         "publish valid points only (drop_reason SUCCESS), as the released image does"),
        ("timestamp_mode", "int", 0, "TIME_MODE host: the client's frame stamp (host clock)"),
        ("frame_id", "string", s.frame_id, "FRAME_ID"),
        ("point_format", "int", 1, "MDL_STANDARD: x y z v snr drop_reason timestamp_nsecs point_idx"),
    ]


def _yaml_value(kind: str, value: object) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind == "int":
        return str(int(value))  # type: ignore[call-overload]
    return json.dumps(str(value))  # a double-quoted YAML string


def render(s: Settings) -> str:
    lines = [
        "# Rendered from the environment by the voyant-driver image entrypoint; do not edit.",
        f"# LIDAR_MODEL {s.model.key} = {s.model.label}; SENSOR_IP {s.sensor_ip} (the driver joins "
        f"the multicast group {MULTICAST_GROUP} on port {s.data_port} and does not filter by sender)",
        f"# TIME_MODE {s.time_mode}: frames are stamped with the host's clock as they arrive",
        f"# topic {s.resolved_topic}, frame {s.frame_id}",
        "/**:",
        "  ros__parameters:",
    ]
    for name, kind, value, why in parameters(s):
        lines.append(f"    {name}: {_yaml_value(kind, value)}  # {why}")
    return "\n".join(lines) + "\n"


def node_argv(s: Settings) -> list[str]:
    argv = [DRIVER_NODE, "--ros-args"]
    if s.sensor_id:
        argv += ["-r", f"__ns:={s.namespace}"]
    argv += ["-r", f"{NODE_TOPIC}:={s.topic}", "--params-file", s.params_path]
    return argv


def describe(s: Settings) -> dict:
    return {
        "model": {"key": s.model.key, "label": s.model.label},
        "sensor_ip": s.sensor_ip,
        "data_port": s.data_port,
        "multicast_group": MULTICAST_GROUP,
        "interface_address": s.interface_address,
        "interface_from": s.interface_from,
        "topic": s.resolved_topic,
        "metadata_topic": s.namespace.rstrip("/") + "/device_metadata",
        "frame_id": s.frame_id,
        "time_mode": s.time_mode,
        "params_file": s.params_path,
        "parameters": {name: value for name, _, value, _ in parameters(s)},
        "argv": node_argv(s),
        "sources": dict(sorted(s.sources.items())),
    }


# --------------------------------------------------------------------------------------------
# Command line.
# --------------------------------------------------------------------------------------------


def refuse(problems: list[str], as_json: bool = False) -> int:
    if as_json:
        print(json.dumps({"refused": problems}, indent=2))
    for problem in problems:
        print(f"{PROG}: refusing to start: {problem}", file=sys.stderr)
    return EXIT_REFUSED


def write_params(s: Settings, text: str) -> None:
    os.makedirs(s.run_dir, mode=0o700, exist_ok=True)
    tmp = s.params_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, s.params_path)


def exec_env(env: Mapping[str, str]) -> dict[str, str]:
    """The driver's environment: ROS_HOME and ROS_LOG_DIR default under /tmp, so an arbitrary
    uid with a read-only root filesystem and a tmpfs on /tmp can log."""
    out = dict(env)
    out.setdefault("ROS_HOME", DEFAULT_ROS_HOME)
    out.setdefault("ROS_LOG_DIR", out["ROS_HOME"].rstrip("/") + "/log")
    return out


def _settings(env: Mapping[str, str], as_json: bool = False):
    try:
        return parse(env), EXIT_OK
    except Refused as exc:
        return None, refuse(exc.problems, as_json)


def cmd_run(env: Mapping[str, str]) -> int:
    s, code = _settings(env)
    if s is None:
        return code
    child_env = exec_env(env)
    try:
        write_params(s, render(s))
        os.makedirs(child_env["ROS_LOG_DIR"], exist_ok=True)
    except OSError as exc:
        return refuse([f"cannot write {exc.filename or s.run_dir} ({exc.strerror}) as uid "
                       f"{os.getuid()}; accepted: a writable /tmp, for example docker run "
                       "--tmpfs /tmp or compose 'tmpfs: [/tmp]' with a read-only root filesystem "
                       "(or point VOYANT_RUN_DIR and ROS_LOG_DIR at a writable volume)"])
    print(f"{PROG}: {s.model.label} at {s.sensor_ip}: group {MULTICAST_GROUP} port "
          f"{s.data_port} on {s.interface_address} ({s.interface_from}), topic "
          f"{s.resolved_topic}, frame {s.frame_id}, time {s.time_mode}, parameters "
          f"{s.params_path}", file=sys.stderr, flush=True)
    argv = node_argv(s)
    try:
        os.execve(argv[0], argv, child_env)
    except OSError as exc:
        print(f"{PROG}: cannot start the driver {argv[0]}: {exc.strerror} (a broken image)",
              file=sys.stderr)
    return EXIT_REFUSED  # reached only when execve failed


def cmd_check(env: Mapping[str, str], as_json: bool) -> int:
    s, code = _settings(env, as_json)
    if s is None:
        return code
    if as_json:
        out = describe(s)
        out["params_yaml"] = render(s)
        print(json.dumps(out, indent=2))
    else:
        sys.stdout.write(render(s))
        print("# exec: " + " ".join(node_argv(s)))
    return EXIT_OK


_DECLARE_RE = re.compile(r'declare_parameter<\s*(std::string|bool|int)\s*>\(\s*"([a-z_]+)"')
_TYPES = {"std::string": "string", "bool": "bool", "int": "int"}


def declared_parameters(source: str) -> dict[str, str]:
    """name -> type of every parameter the node declares (src/sensor_driver.cpp)."""
    return {name: _TYPES[kind] for kind, name in _DECLARE_RE.findall(source)}


def contract_problems(source: str) -> list[str]:
    """What in the driver source no longer matches what the contract sets."""
    problems = []
    declared = declared_parameters(source)
    sample = parse({"LIDAR_MODEL": "meadowlark", "SENSOR_IP": "192.168.20.20",
                    "HOST_IP": "192.168.20.100"})
    for name, kind, _, _ in parameters(sample):
        if declared.get(name) != kind:
            problems.append(f"the node declares {name} as {declared.get(name)}, the contract sets "
                            f"a {kind}")
    for name in sorted(set(declared) - {p[0] for p in parameters(sample)}):
        problems.append(f"the node declares {name}, which the contract does not set")
    if f'create_publisher<sensor_msgs::msg::PointCloud2>("{NODE_TOPIC}"' not in source:
        problems.append(f"the node no longer publishes its cloud on '{NODE_TOPIC}'")
    return problems


def cmd_selfcheck(env: Mapping[str, str]) -> int:
    """The parameters the contract sets are exactly the ones the node in this image declares,
    with the same types, and the node binary is there."""
    with open(DRIVER_SOURCE, encoding="utf-8") as handle:
        problems = contract_problems(handle.read())
    if not os.access(DRIVER_NODE, os.X_OK):
        problems.append(f"{DRIVER_NODE} is missing or not executable")
    if problems:
        for problem in problems:
            print(f"{PROG}: selfcheck failed: {problem}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"{PROG}: selfcheck ok: the contract's parameters match {DRIVER_SOURCE}; "
          f"{DRIVER_NODE} present")
    return EXIT_OK


USAGE = (f"usage: {PROG} [run | check [--json] | selfcheck]  (configuration from the environment; "
         "see docs/METROLLA_DRIVER_CONTRACT.md)")


def main(argv: list[str], env: Mapping[str, str]) -> int:
    if not argv or argv == ["run"]:
        return cmd_run(env)
    if argv[0] == "check" and argv[1:] in ([], ["--json"]):
        return cmd_check(env, argv[1:] == ["--json"])
    if argv == ["selfcheck"]:
        return cmd_selfcheck(env)
    if argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return EXIT_OK
    print(f"{PROG}: unknown command {shown(' '.join(argv))}; {USAGE}. To run something else in "
          "this image, override the entrypoint", file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
