"""Unit tests of the environment contract (docker/voyant_env.py): every variable, default and
refusal, the rendered parameters file, the exec line, and the run/check/selfcheck commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import voyant_env as v
from contract_run import REPO, run

BASE = {"LIDAR_MODEL": "meadowlark", "SENSOR_IP": "192.168.20.20", "HOST_IP": "192.168.20.100"}


def fake_route(sensor_ip: str, port: int) -> str:
    if sensor_ip.startswith("192.168.20."):
        return "192.168.20.100"
    raise OSError(101, "Network is unreachable")


def parse(**changes):
    env = dict(BASE)
    for key, value in changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return v.parse(env, route=fake_route)


def refused(**changes) -> list[str]:
    with pytest.raises(v.Refused) as info:
        parse(**changes)
    return info.value.problems


# ---- accepted values and defaults ------------------------------------------------------------

def test_minimal_defaults():
    s = parse()
    assert s.model.key == "meadowlark"
    assert (s.sensor_ip, s.data_port, s.interface_address) == ("192.168.20.20", 4444, "192.168.20.100")
    assert s.interface_from == "HOST_IP"
    assert (s.frame_id, s.topic, s.resolved_topic) == ("voyant_sensor", "point_cloud", "/point_cloud")
    assert (s.time_mode, s.run_dir, s.params_path) == ("host", "/tmp/voyant", "/tmp/voyant/params.yaml")
    assert s.sensor_id is None and s.namespace == "/"


@pytest.mark.parametrize("name", ["meadowlark", "Meadowlark", "MEADOWLARK", "voyant-meadowlark",
                                  "Voyant_Meadowlark", "mdl", "MDL", "carbon-dev-kit",
                                  "Carbon Dev Kit", "carbon_devkit", "voyant-mdl", " meadowlark "])
def test_model_spellings(name):
    assert parse(LIDAR_MODEL=name).model.key == "meadowlark"


@pytest.mark.parametrize("name", ["carbon", "Carbon", "voyant-carbon", "VOYANT_CARBON"])
def test_carbon_refused_with_the_plan(name):
    (problem,) = refused(LIDAR_MODEL=name)
    assert "voyant-api 0.5 or later" in problem and "VENDOR_UPDATE_PLAN" in problem


@pytest.mark.parametrize("name", ["os1", "rshelios", "voyant", "meadow", "mdl-012", "x"])
def test_unknown_model(name):
    (problem,) = refused(LIDAR_MODEL=name)
    assert "is not a Voyant model this image knows" in problem
    assert "meadowlark, mdl, carbon-dev-kit, carbon-devkit" in problem


def test_host_ip_from_the_route_when_unset():
    s = parse(HOST_IP=None)
    assert (s.interface_address, s.interface_from) == ("192.168.20.100", "route to SENSOR_IP")
    assert s.host_ip is None


def test_no_route_to_the_sensor_is_refused():
    (problem,) = refused(HOST_IP=None, SENSOR_IP="10.9.8.7")
    assert problem.startswith("HOST_IP is not set and this host has no route to SENSOR_IP=10.9.8.7")
    assert "Network is unreachable" in problem


def test_route_is_not_looked_up_when_something_else_is_wrong():
    calls = []

    def route(ip, port):
        calls.append(ip)
        return "1.1.1.1"
    with pytest.raises(v.Refused):
        v.parse({"LIDAR_MODEL": "meadowlark", "SENSOR_IP": "192.168.20.20", "FRAME_ID": "1x"},
                route=route)
    assert calls == []


def test_real_route_to_loopback():
    s = v.parse({"LIDAR_MODEL": "meadowlark", "SENSOR_IP": "127.0.0.2"})
    assert s.interface_address == "127.0.0.1"


@pytest.mark.parametrize("value,addr", [("192.168.20.100", "192.168.20.100"),
                                        ("192.168.20.100/24", "192.168.20.100"),
                                        ("192.168.20.101/16", "192.168.20.101")])
def test_host_ip_forms(value, addr):
    s = parse(HOST_IP=value)
    assert (s.interface_address, s.host_ip) == (addr, value)


@pytest.mark.parametrize("value,text", [
    ("192.168.20.100/33", "has no usable prefix length"),
    ("192.168.20.100/0", "has no usable prefix length"),
    ("192.168.20.100/", "has no usable prefix length"),
    ("eth0", "is not an IPv4 address"),
    ("192.168.20.20", "is the sensor's own address"),
    ("192.168.30.1/24", "is not in the host's subnet 192.168.30.0/24"),
    ("0.0.0.0", "is not a unicast address"),
    ("224.0.0.1", "is not a unicast address"),
])
def test_host_ip_refused(value, text):
    (problem,) = refused(HOST_IP=value)
    assert text in problem and problem.startswith("HOST_IP=")


@pytest.mark.parametrize("value", ["192.168.20.020", "192.168.20", "300.1.1.1", "fe80::1",
                                   "sensor.local", "0.0.0.0", "239.255.48.84",
                                   "255.255.255.255", "240.0.0.1"])
def test_sensor_ip_refused(value):
    (problem,) = refused(SENSOR_IP=value)
    assert problem.startswith(f"SENSOR_IP='{value}' is not")


@pytest.mark.parametrize("value", ["1024", "4444", "65535", "7502"])
def test_data_port(value):
    assert parse(DATA_PORT=value).data_port == int(value)


@pytest.mark.parametrize("value", ["0", "80", "1023", "65536", "04444", "4444 ", "-1", "x"])
def test_data_port_refused(value):
    (problem,) = refused(DATA_PORT=value)
    assert problem.startswith(f"DATA_PORT='{value}' is not a usable UDP port")


@pytest.mark.parametrize("value", ["8080", "80", "4445", ""])
def test_device_port_refused(value):
    (problem,) = refused(DEVICE_PORT=value)
    assert problem.startswith(f"DEVICE_PORT='{value}' is not supported by this image")


@pytest.mark.parametrize("value", ["east", "a", "lidar_2", "a" * 31])
def test_sensor_id(value):
    s = parse(SENSOR_ID=value)
    assert s.namespace == "/" + value and s.resolved_topic == f"/{value}/point_cloud"


@pytest.mark.parametrize("value", ["East", "1east", "east__a", "east-1", "a" * 32, "_east", "e/a"])
def test_sensor_id_refused(value):
    (problem,) = refused(SENSOR_ID=value)
    assert problem.startswith(f"SENSOR_ID='{value}' is not a sensor id")


@pytest.mark.parametrize("value", ["east_lidar", "Voyant", "_x", "a" * 64])
def test_frame_id(value):
    assert parse(FRAME_ID=value).frame_id == value


@pytest.mark.parametrize("value", ["1x", "a-b", "a/b", "a" * 65, "true "])
def test_frame_id_refused(value):
    (problem,) = refused(FRAME_ID=value)
    assert "is not a frame id" in problem


@pytest.mark.parametrize("value,sensor_id,resolved", [
    ("points", None, "/points"), ("points", "east", "/east/points"),
    ("/lidar/points", "east", "/lidar/points"), ("a/b_c", None, "/a/b_c")])
def test_topic(value, sensor_id, resolved):
    s = parse(POINTCLOUD_TOPIC=value, SENSOR_ID=sensor_id)
    assert s.resolved_topic == resolved


@pytest.mark.parametrize("value", ["/", "a//b", "1a", "a__b", "a/", "~/a", "a b", "/" + "a" * 200])
def test_topic_refused(value):
    (problem,) = refused(POINTCLOUD_TOPIC=value)
    assert "is not a ROS topic name" in problem


@pytest.mark.parametrize("value", ["host", "HOST", "Host"])
def test_time_mode_host(value):
    assert parse(TIME_MODE=value).time_mode == "host"


@pytest.mark.parametrize("value", ["sensor", "gps", "ptp", "ntp", "PTP"])
def test_time_mode_sensor_clock_refused(value):
    (problem,) = refused(TIME_MODE=value)
    assert "is not supported by this image" in problem and "host's clock" in problem


def test_time_mode_unknown():
    (problem,) = refused(TIME_MODE="utc")
    assert "is not a time mode" in problem


@pytest.mark.parametrize("name", ["PUBLISH_SCAN", "DUAL_LIDAR"])
@pytest.mark.parametrize("value", ["0", "false", "No", "OFF"])
def test_false_only_flags(name, value):
    parse(**{name: value})


@pytest.mark.parametrize("name", ["PUBLISH_SCAN", "DUAL_LIDAR"])
@pytest.mark.parametrize("value,text", [("1", "is not supported"), ("true", "is not supported"),
                                        ("maybe", "is not a boolean")])
def test_false_only_flags_refused(name, value, text):
    (problem,) = refused(**{name: value})
    assert problem.startswith(f"{name}='{value}' {text}")


def test_dual_lidar_empty_is_false():
    parse(DUAL_LIDAR="")


@pytest.mark.parametrize("value", ["/run/voyant", "/tmp/a.b-c_d"])
def test_run_dir(value):
    assert parse(VOYANT_RUN_DIR=value).params_path == value + "/params.yaml"


@pytest.mark.parametrize("value", ["tmp", "/tmp/../etc", "/tmp/a b", "/tmp/$x"])
def test_run_dir_refused(value):
    (problem,) = refused(VOYANT_RUN_DIR=value)
    assert "is not an absolute directory path" in problem


@pytest.mark.parametrize("name", ["LIDAR_MODEL", "SENSOR_IP", "SENSOR_ID", "DATA_PORT", "HOST_IP",
                                  "FRAME_ID", "POINTCLOUD_TOPIC", "TIME_MODE", "PUBLISH_SCAN",
                                  "VOYANT_RUN_DIR"])
def test_empty_values_refused(name):
    problems = refused(**{name: ""})
    assert problems[0].startswith(f"{name} is set but empty")


@pytest.mark.parametrize("name", ["LIDAR_MODEL", "SENSOR_IP"])
def test_required(name):
    (problem,) = refused(**{name: None})
    assert problem.startswith(f"{name} is not set")


@pytest.mark.parametrize("name,hint", [
    ("LIDAR_TYPE", "LIDAR_MODEL"), ("SENSOR_TYPE", "LIDAR_MODEL"), ("LIDAR_IP", "SENSOR_IP"),
    ("SENSOR_PORT", "DATA_PORT"), ("BINDING_ADDRESS", "DATA_PORT"),
    ("INTERFACE_ADDRESS", "HOST_IP"), ("LIDAR_INTERFACE_DEV", "HOST_IP"),
    ("ROS_FRAME_ID", "FRAME_ID"), ("TOPIC_NAME", "POINTCLOUD_TOPIC"),
    ("TIMESTAMP_MODE", "TIME_MODE"), ("DATA_PROT", "DATA_PORT"), ("SENSOR_IPS", "SENSOR_IP"),
    ("VOYANT_RUNDIR", "VOYANT_RUN_DIR"), ("POINTCLOUD_TOPICS", "POINTCLOUD_TOPIC")])
def test_legacy_names_and_typos(name, hint):
    (problem,) = refused(**{name: "x"})
    assert problem.startswith(f"{name} is not a variable this image knows (did you mean {hint}?)")


def test_multicast_group_is_not_a_setting():
    (problem,) = refused(MULTICAST_GROUP="239.255.48.84")
    assert problem.startswith("MULTICAST_GROUP is not a variable this image knows")


def test_viz_bridge_refused_with_why():
    (problem,) = refused(VIZ_BRIDGE="false")
    assert "without a Foxglove bridge" in problem


@pytest.mark.parametrize("name", ["ROS_LOCALHOST_ONLY", "ROS_DOMAIN_ID", "RMW_IMPLEMENTATION",
                                  "METROLLA_SITE", "USE_MACADDRESS_NS", "PATH"])
def test_other_variables_pass(name):
    parse(**{name: "1"})


@pytest.mark.parametrize("value", ["1"])
def test_switch_one(value):
    parse(METROLLA_DRIVER_CONTRACT=value)


@pytest.mark.parametrize("value", ["2", "yes", "true", " ", "01"])
def test_switch_other_values_refused(value):
    (problem,) = refused(METROLLA_DRIVER_CONTRACT=value)
    assert problem.startswith(f"METROLLA_DRIVER_CONTRACT='{value}' is not a contract version")


def test_every_problem_at_once():
    with pytest.raises(v.Refused) as info:
        v.parse({"LIDAR_MODEL": "os1", "SENSOR_IP": "x", "DATA_PORT": "1", "DEVICE_PORT": "1",
                 "FRAME_ID": "-", "TIME_MODE": "ptp", "PUBLISH_SCAN": "1", "LIDAR_TYPE": "x"},
                route=fake_route)
    assert len(info.value.problems) == 8


# ---- rendering -------------------------------------------------------------------------------

def test_render_parses_and_holds_the_contract():
    s = parse(SENSOR_ID="east", FRAME_ID="east_lidar", DATA_PORT="4500")
    doc = yaml.safe_load(v.render(s))
    assert doc == {"/**": {"ros__parameters": {
        "binding_address": "0.0.0.0:4500", "multicast_group": "224.0.0.0",
        "interface_address": "192.168.20.100", "spn_filter": True, "timestamp_mode": 0,
        "frame_id": "east_lidar", "point_format": 1}}}


@pytest.mark.parametrize("frame", ["true", "null", "yes", "No"])
def test_render_quotes_strings(frame):
    doc = yaml.safe_load(v.render(parse(FRAME_ID=frame)))
    assert doc["/**"]["ros__parameters"]["frame_id"] == frame


def test_node_argv():
    assert v.node_argv(parse()) == [
        "/ros2_ws/install/voyant_ros/lib/voyant_ros/voyant_sensor_node", "--ros-args",
        "-r", "point_cloud:=point_cloud", "--params-file", "/tmp/voyant/params.yaml"]
    assert v.node_argv(parse(SENSOR_ID="east", POINTCLOUD_TOPIC="/lidar/points"))[2:6] == [
        "-r", "__ns:=/east", "-r", "point_cloud:=/lidar/points"]


def test_describe():
    d = v.describe(parse(SENSOR_ID="east"))
    assert d["topic"] == "/east/point_cloud" and d["metadata_topic"] == "/east/device_metadata"
    assert d["parameters"]["binding_address"] == "0.0.0.0:4444"
    assert d["sources"]["LIDAR_MODEL"] == "env" and d["sources"]["FRAME_ID"] == "default"


# ---- the driver source -----------------------------------------------------------------------

def driver_source() -> str:
    return (REPO / "src" / "sensor_driver.cpp").read_text()


def test_contract_matches_the_driver_source():
    assert v.declared_parameters(driver_source()) == {
        "binding_address": "string", "multicast_group": "string", "interface_address": "string",
        "spn_filter": "bool", "timestamp_mode": "int", "frame_id": "string", "point_format": "int"}
    assert v.contract_problems(driver_source()) == []


def test_contract_problems_on_a_renamed_parameter():
    source = driver_source().replace('"spn_filter"', '"valid_only_filter"')
    problems = v.contract_problems(source)
    assert "the node declares spn_filter as None, the contract sets a bool" in problems
    assert "the node declares valid_only_filter, which the contract does not set" in problems


def test_contract_problems_on_a_renamed_topic():
    source = driver_source().replace('("point_cloud"', '("points"')
    assert v.contract_problems(source) == ["the node no longer publishes its cloud on 'point_cloud'"]


# ---- commands --------------------------------------------------------------------------------

def test_check_text():
    code, out, err = run(["check"], {"LIDAR_MODEL": "mdl", "SENSOR_IP": "127.0.0.2"})
    assert code == 0 and err == ""
    assert out.endswith("# exec: /ros2_ws/install/voyant_ros/lib/voyant_ros/voyant_sensor_node "
                        "--ros-args -r point_cloud:=point_cloud --params-file /tmp/voyant/params.yaml\n")
    assert 'interface_address: "127.0.0.1"' in out


def test_check_json():
    code, out, err = run(["check", "--json"], dict(BASE))
    doc = json.loads(out)
    assert code == 0 and doc["interface_address"] == "192.168.20.100"
    assert yaml.safe_load(doc["params_yaml"])["/**"]["ros__parameters"]["point_format"] == 1


def test_check_refused_json():
    code, out, err = run(["check", "--json"], {})
    assert code == 1 and json.loads(out)["refused"][0].startswith("LIDAR_MODEL is not set")
    assert err.startswith("voyant-driver: refusing to start: LIDAR_MODEL is not set")


@pytest.mark.parametrize("argv", [["bash"], ["run", "x"], ["check", "--yaml"],
                                  ["ros2", "launch", "voyant_ros", "sensor_launch.py"]])
def test_unknown_commands(argv):
    code, out, err = run(argv, dict(BASE))
    assert code == 1 and "unknown command" in err


def test_help():
    assert run(["--help"], {})[0] == 0


def test_run_writes_the_file_and_execs(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(v.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    env = dict(BASE, VOYANT_RUN_DIR=str(tmp_path / "run"), ROS_HOME=str(tmp_path / "ros"))
    code, out, err = run([], env)
    assert code == 1  # only because the patched execve returned
    (path, argv, child_env) = calls[0]
    assert path == v.DRIVER_NODE and argv[-1] == str(tmp_path / "run" / "params.yaml")
    assert (tmp_path / "run" / "params.yaml").read_text() == v.render(v.parse(env, route=fake_route))
    assert child_env["ROS_LOG_DIR"] == str(tmp_path / "ros" / "log")
    assert (tmp_path / "ros" / "log").is_dir()
    assert err.startswith("voyant-driver: Voyant Carbon Dev Kit (Meadowlark")


def test_run_default_log_dir(monkeypatch):
    assert v.exec_env({})["ROS_LOG_DIR"] == "/tmp/ros/log"
    assert v.exec_env({"ROS_LOG_DIR": "/x"})["ROS_LOG_DIR"] == "/x"


def test_run_unwritable(monkeypatch, tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        code, out, err = run([], dict(BASE, VOYANT_RUN_DIR=str(ro / "x")))
    finally:
        ro.chmod(0o700)
    assert code == 1 and "cannot write" in err and "--tmpfs /tmp" in err


def test_selfcheck(monkeypatch, tmp_path):
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    monkeypatch.setattr(v, "DRIVER_SOURCE", str(REPO / "src" / "sensor_driver.cpp"))
    monkeypatch.setattr(v, "DRIVER_NODE", str(node))
    code, out, err = run(["selfcheck"], {})
    assert code == 0 and "selfcheck ok" in out
    node.unlink()
    code, out, err = run(["selfcheck"], {})
    assert code == 1 and "missing or not executable" in err


def test_contract_entrypoint_script():
    text = (REPO / "docker" / "contract-entrypoint.sh").read_text()
    assert 'source "/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash"' in text
    assert text.rstrip().endswith('exec python3 -I /opt/metrolla/voyant/voyant_env.py "$@"')


def test_dockerfile_installs_the_contract():
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "COPY docker/contract-entrypoint.sh docker/voyant_env.py /opt/metrolla/voyant/" in dockerfile
    assert "python3 -I /opt/metrolla/voyant/voyant_env.py selfcheck" in dockerfile
    assert Path(REPO / "docker" / "voyant_entrypoint.sh").stat().st_mode & 0o111
