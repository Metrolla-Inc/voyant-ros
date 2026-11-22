"""Contract goldens: `check` (and `check --json`) output of docker/voyant_env.py for each
environment in cases/*.env, accepted and refused. Accepted cases set HOST_IP or use a loopback
SENSOR_IP, so the output is the same on every machine (the route to 127.x is 127.0.0.1). The smoke
test runs the same cases in the built image and compares them with these files byte for byte.
REGEN=1 rewrites them."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from contract_run import read_case, record
from harness import IMAGE_ENV

HERE = Path(__file__).resolve().parent
CASES = sorted((HERE / "cases").glob("*.env"))


def test_cases_exist():
    assert len(CASES) >= 15
    assert sum(c.stem.startswith("refused-") for c in CASES) >= 10


@pytest.mark.parametrize("case", CASES, ids=[c.stem for c in CASES])
@pytest.mark.parametrize("argv", [["check"], ["check", "--json"]], ids=["text", "json"])
def test_contract_golden(case, argv):
    env = read_case(case)
    env.setdefault("METROLLA_DRIVER_CONTRACT", "1")  # as the image gets it
    env = {**IMAGE_ENV, **env}  # the Dockerfile's ENV is in every container's environment
    got = record(argv, env)
    golden = HERE / "golden" / "contract" / f"{case.stem}{'.json' if '--json' in argv else ''}.golden"
    if os.environ.get("REGEN"):
        golden.write_text(got)
    assert got == golden.read_text()
    refused = case.stem.startswith("refused-")
    assert got.startswith("exit: 1\n" if refused else "exit: 0\n")
