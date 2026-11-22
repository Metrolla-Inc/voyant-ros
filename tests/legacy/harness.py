"""Run an entrypoint of this image outside the image, in a sandbox, and record what it does: exit
code, stdout, stderr, and every command it starts (argv) with the environment that command gets,
including which setup files were sourced and in what order.

The sandbox is a temporary directory laid out like the image: /ros2_ws/install/setup.bash and
/opt/ros/jazzy/setup.bash (each only records that it was sourced), `ros2` as a recorder on PATH,
and /opt/metrolla/voyant/contract-entrypoint.sh as a recorder. The image's ENV is given to the
script, absolute paths are rewritten into the sandbox, and every sandbox path is mapped back in
the record, so a record reads as if taken in the image.

Two kinds of entrypoint are run:
  - the released one: the Dockerfile's shell-form ENTRYPOINT, which Docker runs as
    `/bin/bash -c '<command>'` with no arguments (released_command() extracts the command from a
    Dockerfile the way Docker joins continuation lines);
  - the current one: docker/voyant_entrypoint.sh with the container's arguments.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REWRITES = ("/opt/ros/", "/opt/metrolla/", "/ros2_ws/")
# The image's ENV: the Dockerfile's ENV lines (build args at their defaults) and ros:jazzy-ros-core.
IMAGE_ENV = {
    "ROS_DISTRO": "jazzy",
    "DEBIAN_FRONTEND": "noninteractive",
    "RMW_IMPLEMENTATION_ARG": "rmw_fastrtps_cpp",
    "VIZ_BRIDGE_ARG": "false",
    "BUILD_HOME": "/var/lib/build",
}
BASE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
RECORDED_ENV = ("SOURCED", "ROS_DISTRO", "METROLLA_DRIVER_CONTRACT", "VIZ_BRIDGE_ARG")

RECORDER = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["HARNESS_RECORD"], "a") as f:
    f.write(json.dumps({"argv": [os.path.basename(sys.argv[0])] + sys.argv[1:],
                        "env": dict(os.environ)}) + "\\n")
"""


def released_command(dockerfile: str) -> str:
    """The command of a shell-form ENTRYPOINT, joined as Docker joins continuation lines (the
    escape character and the newline go, everything else stays)."""
    lines = dockerfile.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("ENTRYPOINT "))
    parts = []
    for line in lines[start:]:
        if line.endswith("\\"):
            parts.append(line[:-1])
            continue
        parts.append(line)
        break
    command = "".join(parts)[len("ENTRYPOINT "):]
    assert not command.startswith("["), "not a shell-form ENTRYPOINT"
    return command


def _layout(root: Path) -> None:
    for setup, label in (("opt/ros/jazzy/setup.bash", "/opt/ros/jazzy"),
                         ("ros2_ws/install/setup.bash", "/ros2_ws/install")):
        path = root / setup
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'export SOURCED="${{SOURCED:+$SOURCED:}}{label}"\n')
    bindir = root / "bin"
    bindir.mkdir()
    (bindir / "ros2").write_text(RECORDER)
    (bindir / "ros2").chmod(0o755)
    contract = root / "opt/metrolla/voyant/contract-entrypoint.sh"
    contract.parent.mkdir(parents=True)
    contract.write_text(RECORDER)
    contract.chmod(0o755)


def _sandboxed(text: str, root: Path) -> str:
    for prefix in REWRITES:
        text = text.replace(prefix, str(root) + prefix)
    return text


def _run(root: Path, cmd: list[str], env: dict[str, str]) -> dict:
    record_file = root / "record.jsonl"
    run_env = dict(BASE_ENV)
    run_env["PATH"] = f"{root}/bin:" + run_env["PATH"]
    run_env["HARNESS_RECORD"] = str(record_file)
    run_env.update(IMAGE_ENV)
    run_env.update(env)
    proc = subprocess.run(cmd, env=run_env, capture_output=True, text=True, timeout=60, check=False)

    def back(value: str) -> str:
        return value.replace(str(root), "")

    started = []
    if record_file.exists():
        for line in record_file.read_text().splitlines():
            raw = json.loads(line)
            exec_env = {k: back(v) for k, v in raw["env"].items() if k in RECORDED_ENV or k in env}
            started.append({"argv": [back(a) for a in raw["argv"]],
                            "env": dict(sorted(exec_env.items()))})
    started.sort(key=lambda r: r["argv"])  # background jobs may start in either order
    return {"exit": proc.returncode, "stdout": back(proc.stdout), "stderr": back(proc.stderr),
            "started": started}


def run_released(command: str, env: dict[str, str]) -> dict:
    """What the released image did: /bin/bash -c '<command>', no arguments."""
    with tempfile.TemporaryDirectory(prefix="voyant-legacy-") as tmp:
        root = Path(tmp)
        _layout(root)
        return _run(root, ["/bin/bash", "-c", _sandboxed(command, root)], env)


def run_current(script: Path, env: dict[str, str], argv: list[str]) -> dict:
    """What the current image does: /voyant_entrypoint.sh with the container's arguments."""
    with tempfile.TemporaryDirectory(prefix="voyant-legacy-") as tmp:
        root = Path(tmp)
        _layout(root)
        sandboxed = root / "voyant_entrypoint.sh"
        sandboxed.write_text(_sandboxed(script.read_text(), root))
        return _run(root, ["/bin/bash", str(sandboxed), *argv], env)


def read_case(path: Path) -> tuple[dict[str, str], list[str]]:
    """KEY=VALUE lines (# comments); a line 'argv: a b c' sets the container's command (default:
    none, as the image has no CMD)."""
    env: dict[str, str] = {}
    argv: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("argv:"):
            argv = line[len("argv:"):].split()
            continue
        key, sep, value = line.partition("=")
        assert sep, f"{path}: not KEY=VALUE: {line!r}"
        env[key] = value
    return env, argv


def dump(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"
