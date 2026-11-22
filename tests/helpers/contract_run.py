"""Run docker/voyant_env.py's command line in-process and capture what it prints."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import voyant_env

REPO = Path(__file__).resolve().parents[2]


def run(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = voyant_env.main(argv, env)
    return code, out.getvalue(), err.getvalue()


def record(argv: list[str], env: dict[str, str]) -> str:
    code, out, err = run(argv, env)
    return f"exit: {code}\n--- stdout\n{out}--- stderr\n{err}"


def read_case(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        assert sep, f"{path}: not KEY=VALUE: {line!r}"
        env[key] = value
    return env
