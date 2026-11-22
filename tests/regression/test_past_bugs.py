"""Past bugs: each check passes on this tree and fails on the released files (main 7e6f9aa, read
with git, so the job needs the history: fetch-depth 0). The runtime side of the first four is in
the smoke test (docker/smoke/smoke.sh: docker stop and SIGINT under load and before the first
frame, exit 0; a port already in use, exit 1)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELEASED = "7e6f9aa01f6fe8b28493adc40b8296506dfe1226"


def released(path: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), "show", f"{RELEASED}:{path}"], check=True,
                          capture_output=True, text=True).stdout


def current(path: str) -> str:
    return (REPO / path).read_text()


def both(path: str):
    return pytest.mark.parametrize("text,fixed", [(current(path), True), (released(path), False)],
                                   ids=["current", "released"])


def _function(source: str, name: str) -> str:
    start = source.index(f"VoyantSensorDriver::{name}()")
    end = source.find("\nvoid VoyantSensorDriver::", start + 1)
    return source[start:end if end > 0 else None]


# 1. docker stop did not stop the node: only SIGINT set the Voyant client's stop flag. SIGTERM went
#    to rclcpp's handler, which the loop waiting for the first frame never looked at, so a driver
#    with no sensor (or a sensor not streaming yet) ignored docker stop and was killed after 10 s.
@both("src/sensor_driver.cpp")
def test_sigterm_sets_the_stop_flag(text, fixed):
    assert ("VoyantClient::setupSignalHandling(SIGTERM)" in text) is fixed


# 2. While waiting for the first frame the node spun a core at 100 % (no sleep) and ignored a ROS
#    shutdown.
@both("src/sensor_driver.cpp")
def test_first_frame_wait_sleeps_and_stops(text, fixed):
    body = _function(text, "initialize")
    loop = body[body.index("while("):]
    loop = loop[:loop.index("\n    }\n")]
    good = "rclcpp::ok()" in loop.splitlines()[0] and "sleep_for" in loop
    assert good is fixed


# 3. A driver that could not receive looked healthy: an invalid client (port in use) or an error
#    while publishing was logged, ROS shut down, and the process exited 0 or kept looping.
@both("src/sensor_driver.cpp")
def test_errors_reach_main(text, fixed):
    init = _function(text, "initialize")
    publish = _function(text, "publishPointCloud")
    good = ("throw;" in init and "throw;" in publish and
            "rclcpp::shutdown();" not in init + publish)
    assert good is fixed


@both("src/main.cpp")
def test_main_exits_1_on_error_and_does_not_spin(text, fixed):
    # 4. After SIGINT the Voyant handler ended the loops, then rclcpp::spin waited forever: the
    #    Voyant client had replaced rclcpp's SIGINT handler, so nothing shut ROS down.
    good = ("catch(const std::exception &e)" in text and "code = 1;" in text and
            "rclcpp::spin(" not in text and "SignalHandlerOptions::None" in text)
    assert good is fixed


# 5. The image took whatever voyant-api release was latest on build day: the build has failed on
#    every run since May 2026 (on 21 Nov: voyant-api 1.0.1 has no voyant_frame_wrapper.hpp).
@both("Dockerfile")
def test_sdk_pinned_with_checksum(text, fixed):
    good = ("releases/latest" not in text and "ARG VOYANT_API_VERSION=0.4.4" in text and
            re.search(r"ARG VOYANT_API_SHA256=[0-9a-f]{64}", text) is not None and
            "sha256sum -c /tmp/voyant-api.sha256" in text)
    assert good is fixed


# 6. Cap'n Proto was downloaded without a checksum (and curl without -f saved an error page).
@both("Dockerfile")
def test_capnp_checksum(text, fixed):
    good = (re.search(r"ARG CAPNP_SHA256=[0-9a-f]{64}", text) is not None and "curl -fsSLO" in text
            and "sha256sum -c capnp.sha256" in text)
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
