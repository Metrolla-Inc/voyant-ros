"""Past bugs: each check passes on this tree and fails on the files that had the bug, read with
git (so the job needs the history: fetch-depth 0): the released files (main 7e6f9aa), Voyant's
v1.0.1 (ef121af, merged here; the stop/fail bugs are still in it) and main before the Carbon
update (1061d30, which refused the Carbon). The runtime side of the stop/fail bugs is in the smoke
test (docker/smoke/smoke.sh: docker stop and SIGINT under load and before the first frame, exit 0;
a port already in use, exit 1; clouds from a sensor that is not at voyant-api's default
address)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELEASED = "7e6f9aa01f6fe8b28493adc40b8296506dfe1226"
VENDOR_V101 = "ef121af1cc5aef7acd736a4359ee13918735136a"  # Voyant's v1.0.1 tag
MAIN_BEFORE_CARBON = "1061d309fc9b43685e5c87eabacbc4eed7264620"  # #3 merged, Dev Kit only


def at(commit: str, path: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), "show", f"{commit}:{path}"], check=True,
                          capture_output=True, text=True).stdout


def released(path: str) -> str:
    return at(RELEASED, path)


def current(path: str) -> str:
    return (REPO / path).read_text()


def both(path: str):
    return pytest.mark.parametrize("text,fixed", [(current(path), True), (released(path), False)],
                                   ids=["current", "released"])


def three(path: str):
    """Current (fixed), released and Voyant's v1.0.1 (both have the bug)."""
    return pytest.mark.parametrize(
        "text,fixed", [(current(path), True), (released(path), False),
                       (at(VENDOR_V101, path), False)], ids=["current", "released", "vendor-v1.0.1"])


def _function(source: str, name: str) -> str:
    start = source.index(f"VoyantSensorDriver::{name}()")
    end = source.find("\nvoid VoyantSensorDriver::", start + 1)
    return source[start:end if end > 0 else None]


# 1. docker stop did not stop the node: only SIGINT set the Voyant client's stop flag. SIGTERM went
#    to rclcpp's handler, which the loop waiting for the first frame never looked at, so a driver
#    with no sensor (or a sensor not streaming yet) ignored docker stop and was killed after 10 s.
#    (VoyantClient before the update, CarbonClient after; the vendor's v1.0.1 binds SIGINT only.)
@three("src/sensor_driver.cpp")
def test_sigterm_sets_the_stop_flag(text, fixed):
    assert (re.search(r"(Voyant|Carbon)Client::setupSignalHandling\(SIGTERM\)", text)
            is not None) is fixed


# 2. While waiting for the first frame the node spun a core at 100 % (no sleep) and ignored a ROS
#    shutdown (v1.0.1 sleeps, but still ignores a ROS shutdown).
@three("src/sensor_driver.cpp")
def test_first_frame_wait_sleeps_and_stops(text, fixed):
    body = _function(text, "initialize")
    loop = body[body.index("while("):]
    loop = loop[:loop.index("\n    }\n")]
    good = "rclcpp::ok()" in loop.splitlines()[0] and "sleep_for" in loop
    assert good is fixed


# 3. A driver that could not receive looked healthy: an invalid client (port in use) or an error
#    while publishing was logged, ROS shut down, and the process exited 0 or kept looping (v1.0.1
#    rethrows a start failure, but an error while publishing still shuts ROS down and exits 0).
@three("src/sensor_driver.cpp")
def test_errors_reach_main(text, fixed):
    init = _function(text, "initialize")
    publish = _function(text, "publishPointCloud")
    good = ("throw;" in init and "throw;" in publish and
            "rclcpp::shutdown();" not in init + publish)
    assert good is fixed


@three("src/main.cpp")
def test_main_exits_1_on_error_and_does_not_spin(text, fixed):
    # 4. After SIGINT the Voyant handler ended the loops, then rclcpp::spin waited forever: the
    #    Voyant client had replaced rclcpp's SIGINT handler, so nothing shut ROS down.
    good = ("catch(const std::exception &e)" in text and "code = 1;" in text and
            "rclcpp::spin(" not in text and "SignalHandlerOptions::None" in text)
    assert good is fixed


# 5. The image took whatever voyant-api release was latest on build day: the build has failed on
#    every run since May 2026 (on 21 Nov: voyant-api 1.0.1 has no voyant_frame_wrapper.hpp). The
#    vendor's v1.0.1 Dockerfile still does.
@three("Dockerfile")
def test_sdk_pinned_with_checksum(text, fixed):
    good = ("releases/latest" not in text and "ARG VOYANT_API_VERSION=1.0.1" in text and
            re.search(r"ARG VOYANT_API_SHA256=[0-9a-f]{64}", text) is not None and
            "sha256sum -c /tmp/voyant-api.sha256" in text)
    assert good is fixed


# 6. Cap'n Proto was downloaded without a checksum (and curl without -f saved an error page).
#    voyant-api 1.0 no longer needs it (vendor fef04f0): nothing is downloaded unchecked.
@both("Dockerfile")
def test_capnp_checksum(text, fixed):
    checked = (re.search(r"ARG CAPNP_SHA256=[0-9a-f]{64}", text) is not None and
               "curl -fsSLO" in text and "sha256sum -c capnp.sha256" in text)
    good = checked or "capnproto" not in text.lower()
    assert good is fixed


# 7. Images: pushes to main and to v* branches pushed images (main-jazzy-..., rebuilt on every
#    merge), the build used and wrote a mutable :latest-... registry cache, a tag push could
#    overwrite an existing image tag, no digest was recorded, and actions were not pinned.
def _workflow(text: str) -> dict:
    import yaml
    return yaml.safe_load(text)


@pytest.mark.parametrize("text,fixed", [
    (current(".github/workflows/docker-image.yml"), True),
    (released(".github/workflows/docker-image.yml"), False)], ids=["current", "released"])
def test_images_for_tags_only(text, fixed):
    on = _workflow(text)[True]  # YAML 1.1 reads the key `on` as True
    push = on.get("push", {})
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    good = (push.get("branches") is None and push.get("tags") == ["v*"] and
            "latest" not in code and "imagetools inspect" in code and "digest" in code)
    assert good is fixed


@pytest.mark.parametrize("path", [".github/workflows/docker-image.yml", ".github/workflows/ci.yml"])
def test_actions_pinned_by_sha(path):
    uses = re.findall(r"uses:\s*([^\s#]+)", current(path))
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses


def test_released_actions_were_not_pinned():
    uses = re.findall(r"uses:\s*([^\s#]+)", released(".github/workflows/docker-image.yml"))
    assert not all(re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", u) for u in uses)


# 8. The workflow built linux/arm64, which cannot link: voyant-api's arm64 dev package ships its C++
#    client libraries built for x86-64.
@pytest.mark.parametrize("text,fixed", [
    (current(".github/workflows/docker-image.yml"), True),
    (released(".github/workflows/docker-image.yml"), False)], ids=["current", "released"])
def test_no_arm64_image(text, fixed):
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert ("arm64" not in code and "platforms: linux/amd64" in code) is fixed


# 9. The Carbon was refused and the Dev Kit named instead: main before this update (1061d30) built
#    with voyant-api 0.4.4 and refused LIDAR_MODEL carbon / voyant-carbon pointing at
#    carbon-dev-kit. The owner's target is the Carbon (22 Nov 2026); the Dev Kit is not.
def _contract(source: str, env: dict) -> tuple[int, str]:
    import contextlib
    import io
    import sys
    import types

    module = types.ModuleType("voyant_env_at_commit")
    sys.modules[module.__name__] = module  # dataclasses look their module up
    try:
        exec(compile(source, "voyant_env.py", "exec"), module.__dict__)  # noqa: S102 - our file
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = module.main(["check"], env)
    finally:
        del sys.modules[module.__name__]
    return code, err.getvalue()


@pytest.mark.parametrize("text,fixed", [
    (current("docker/voyant_env.py"), True),
    (at(MAIN_BEFORE_CARBON, "docker/voyant_env.py"), False)], ids=["current", "main-1061d30"])
@pytest.mark.parametrize("model", ["voyant-carbon", "carbon"])
def test_carbon_accepted_and_dev_kit_refused(text, fixed, model):
    base = {"METROLLA_DRIVER_CONTRACT": "1", "SENSOR_IP": "192.168.1.128",
            "HOST_IP": "192.168.1.100"}
    code, err = _contract(text, dict(base, LIDAR_MODEL=model))
    carbon_ok = code == 0 and err == ""
    code, err = _contract(text, dict(base, LIDAR_MODEL="carbon-dev-kit"))
    dev_kit_refused = code == 1 and "the Carbon Dev Kit (Meadowlark) is not a target" in err
    assert (carbon_ok and dev_kit_refused) is fixed


# 10. SENSOR_IP never reached the client: the vendor's v1.0.1 node does not set the client's
#     fpga_target_addr, so the client wrote the push destination, the clock discipline and SDL
#     to voyant-api's default 192.168.1.128:1234 whatever the sensor's address (found while
#     reviewing the merge). The node now declares fpga_target_address and the contract sets it
#     to SENSOR_IP:1234 (tests/unit; the smoke test's sensor is not at the default address).
@pytest.mark.parametrize("text,fixed", [
    (current("src/sensor_driver.cpp"), True),
    (at(VENDOR_V101, "src/sensor_driver.cpp"), False)], ids=["current", "vendor-v1.0.1"])
def test_node_sets_the_sensor_address(text, fixed):
    good = ('declare_parameter<std::string>("fpga_target_address"' in text and
            ".setFpgaTargetAddr(config_.fpga_target_address)" in text)
    assert good is fixed
