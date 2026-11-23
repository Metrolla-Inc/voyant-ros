"""Legacy mode stays exactly the released image's behaviour.

No fleet runs this image (balena-fleets, balena-fleets-metrollaprod, core_system_containers and
the partner fleet repository, every branch and tag and their history, checked 22 Nov 2026), and no image
of this fork was ever pushed: every "Docker Build and Push" run failed or was cancelled. The
released behaviour is therefore the Dockerfile on main before this change (Dockerfile-released,
git blob 1ea7ea8 = main 7e6f9aa:Dockerfile): its shell-form ENTRYPOINT, run by Docker as
`/bin/bash -c '<command>'` with no arguments.

Goldens: for each environment in cases/*.env, the record (exit code, stdout, stderr, every command
started with its environment, which setup files were sourced) of the **released command**. The
current docker/voyant_entrypoint.sh must produce the same record with the case's container
command. REGEN=1 rewrites the goldens from the released command, never from the current script.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

import pytest

from harness import REPO, dump, read_case, released_command, run_current, run_released

HERE = Path(__file__).resolve().parent
CASES = sorted((HERE / "cases").glob("*.env"))
RELEASED_DOCKERFILE = HERE / "Dockerfile-released"
RELEASED_BLOB = "1ea7ea83c00d33256526c93805c7da54d46ba092"  # git rev-parse 7e6f9aa:Dockerfile
CURRENT = REPO / "docker" / "voyant_entrypoint.sh"


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def released() -> str:
    return released_command(RELEASED_DOCKERFILE.read_text())


def test_reference_is_the_released_dockerfile():
    assert git_blob_id(RELEASED_DOCKERFILE.read_bytes()) == RELEASED_BLOB


def test_there_are_legacy_cases():
    assert len(CASES) >= 8


@pytest.mark.parametrize("case", CASES, ids=[c.stem for c in CASES])
def test_legacy_golden(case: Path):
    env, argv = read_case(case)
    golden = HERE / "golden" / (case.stem + ".json")
    reference = run_released(released(), env)
    if env.get("VIZ_BRIDGE_ARG") == "true":
        for _ in range(20):  # wait for the background job's record
            if len(reference["started"]) == 2:
                break
            time.sleep(0.1)
            reference = run_released(released(), env)
    reference = dump(reference)
    if os.environ.get("REGEN"):
        golden.write_text(reference)
    expected = golden.read_text()
    assert reference == expected, "the released command no longer gives its golden (harness changed?)"
    for _ in range(20):
        current = dump(run_current(CURRENT, env, argv))
        if current == expected or env.get("VIZ_BRIDGE_ARG") != "true":
            break
    assert current == expected, "legacy behaviour changed"


def test_image_default_runs_the_minimal_launch():
    env, argv = read_case(HERE / "cases" / "image-default.env")
    record = run_current(CURRENT, env, argv)
    assert record["exit"] == 0
    assert [r["argv"] for r in record["started"]] == [
        ["ros2", "launch", "voyant_ros", "sensor_driver_minimal_launch.py"]]
    assert record["started"][0]["env"]["SOURCED"] == "/ros2_ws/install"
    assert record["stdout"] == "VIZ_BRIDGE_ARG value: false\nStarting with minimal sensor driver\n"


BLOCK_END = "# --- end of the Metrolla contract block; the released entrypoint follows ---\n"


def test_entrypoint_is_the_opt_in_block_then_the_released_command():
    text = CURRENT.read_text()
    assert text.startswith("#!/bin/bash\n")
    head, sep, tail = text.partition(BLOCK_END)
    assert sep, "voyant_entrypoint.sh has no contract block end marker"
    block = [line for line in head.splitlines() if not line.startswith("#")]
    assert block == [
        'if [ -n "${METROLLA_DRIVER_CONTRACT:-}" ] && [ "${METROLLA_DRIVER_CONTRACT}" != "0" ]; then',
        '    exec /opt/metrolla/voyant/contract-entrypoint.sh "$@"',
        "fi",
    ]
    rest = [line for line in tail.splitlines() if not line.startswith("#")]
    command = released()
    assert "'" not in command
    assert rest == [f"exec /bin/bash -c '{command}'"], "the released command changed"


@pytest.mark.parametrize("value", ["1", "2", "yes", "true", " "])
def test_any_other_value_hands_over_to_the_contract(value):
    """Set and not "0": the contract entrypoint gets the container's command (and refuses values
    it does not know, tests/regression); nothing of the legacy entrypoint runs."""
    record = run_current(CURRENT, {"METROLLA_DRIVER_CONTRACT": value}, ["check"])
    assert [r["argv"] for r in record["started"]] == [["contract-entrypoint.sh", "check"]]
    assert "SOURCED" not in record["started"][0]["env"]
    assert record["stdout"] == ""


def test_dockerfile_keeps_user_and_has_no_cmd():
    dockerfile = (REPO / "Dockerfile").read_text()
    assert 'ENTRYPOINT ["/voyant_entrypoint.sh"]' in dockerfile
    assert not re.search(r"^CMD ", dockerfile, re.M)  # none, as released
    assert not re.search(r"^USER ", dockerfile, re.M)  # root, as released; compose sets the user
    released_df = RELEASED_DOCKERFILE.read_text()
    assert not re.search(r"^(CMD|USER) ", released_df, re.M)


@pytest.mark.parametrize("path,blob", [
    # Voyant's v1.0.1 file (git rev-parse v1.0.1:config/sensor_params.yaml): the Carbon defaults
    # (port 5678, group 239.255.48.84, interface 192.168.1.100, unicast, primary client, sensor
    # time). Taken with the vendor update on purpose; the released file (38b5dea, Meadowlark:
    # 224.0.0.0:4444 on 127.0.0.1) configured a stream voyant-api 1.0.1 cannot receive.
    ("config/sensor_params.yaml", "58077a8a16e155e4c4c16646ad1cd43aa25d80e4"),
    # The released file, unchanged by the vendor since (7e6f9aa and v1.0.1 have the same blob).
    ("launch/sensor_driver_minimal_launch.py", "e630a01f7f3d9b992ce58090b7f69863106df378"),
])
def test_legacy_launch_and_config(path, blob):
    """The legacy command runs the minimal launch, which reads config/sensor_params.yaml: the
    launch file is the released one; the parameters file is the vendor's v1.0.1 one, as the vendor
    ships it (docs/VENDOR_DIFF.md, "Legacy mode")."""
    assert git_blob_id((REPO / path).read_bytes()) == blob
