"""The image workflow's tag step (.github/workflows/docker-image.yml), run with bash: images are
pushed only for tags named v<vendor version>-metrolla.<n> (owner, 22 Nov 2026: Metrolla tags never
reuse a vendor's tag; Voyant's own tags are v0.x and v1.0.x), from the tagged commit. The other
workflow guards are in tests/regression/test_past_bugs.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "docker-image.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _tag_step_script() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text())
    job = doc["jobs"]["build-and-push"]
    return next(s for s in job["steps"] if s.get("id") == "tag")["run"]


def _run_tag_step(tmp_path, ref_name, ref_type="tag", head=SHA):
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "git").write_text(f"#!/bin/sh\necho {head}\n")
    (fake / "git").chmod(0o755)
    out = tmp_path / "out"
    out.write_text("")
    env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", REF_TYPE=ref_type,
               REF_NAME=ref_name, GITHUB_SHA=SHA, GITHUB_OUTPUT=str(out))
    proc = subprocess.run(["bash", "-e", "-c", _tag_step_script()], env=env, capture_output=True,
                          text=True, check=False)
    return proc.returncode, out.read_text(), proc.stderr


def test_tag_names_metrolla_pattern(tmp_path):
    """Owner, 22 Nov 2026: Metrolla tags are v<vendor version>-metrolla.<n> and never reuse a
    vendor's tag; the push job accepts exactly that and pushes the image under the same name. The
    first Voyant Carbon image is v1.0.1-metrolla.1."""
    for good in ("v1.0.1-metrolla.1", "v1.0.1-metrolla.12", "v1.0.0-metrolla.1",
                 "vjazzy-0.15.1-metrolla.1"):
        code, out, err = _run_tag_step(tmp_path, good)
        assert code == 0, (good, err)
        assert out == f"tag={good}\n"
    # Voyant's own tags (v1.0.1, v0.4.4), this fork's old tags (v0.0.1, v0.0.2) and near misses.
    for bad in ("v1.0.1", "v0.4.4", "v0.0.1", "v0.0.2", "v1.0.0-metrolla", "v1.0.1-metrolla.0",
                "v1.0.1-metrolla.1-rc", "1.0.1-metrolla.1", "v-metrolla.1", "v1.0.1-Metrolla.1"):
        code, out, err = _run_tag_step(tmp_path, bad)
        assert code == 1 and out == "", bad
        assert "v<vendor version>-metrolla.<n>" in err
    code, _, err = _run_tag_step(tmp_path, "main", ref_type="branch")
    assert code == 1 and "tags only" in err


def test_tag_step_refuses_a_checkout_that_is_not_the_tagged_commit(tmp_path):
    code, out, err = _run_tag_step(tmp_path, "v1.0.1-metrolla.1", head="f" * 40)
    assert code == 1 and out == "" and "not the tagged commit" in err


def test_no_overwrite_check_comes_before_the_push():
    doc = yaml.safe_load(WORKFLOW.read_text())
    names = [s.get("name", "") for s in doc["jobs"]["build-and-push"]["steps"]]
    assert names.index("Compute the image tag from the git tag") < \
        names.index("Refuse to overwrite an existing image tag") < \
        names.index("Build and push Docker image for amd64")
