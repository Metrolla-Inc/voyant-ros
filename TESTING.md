# Tests (Metrolla)

Three tiers, separately named jobs in `.github/workflows/ci.yml`. `.github/workflows/docker-image.yml`
builds the image for amd64 and arm64 on pull requests (not pushed); `vendor-watch-tests.yml` tests
the weekly vendor comparison separately. The vendor's pre-commit hooks (`.pre-commit-config.yaml`)
are not run in CI.

| Tier | Command | What it proves |
|---|---|---|
| unit | `python -m pytest tests/unit tests/legacy` (+ shellcheck, hadolint in CI) | the contract (docker/voyant_env.py): every variable, default and refusal, model names, the HOST_IP route lookup, the rendered parameters file, the exec line, the run/check/selfcheck commands, and that the parameters it sets are exactly the ones `src/sensor_driver.cpp` declares; the legacy entrypoint is the released command behind the opt-in block |
| regression | `python -m pytest tests/regression tests/legacy` (needs the git history: the past-bug tests read main 7e6f9aa) | goldens: the released command's record (what it prints, sources and starts, with which environment) for 8 environments (fleet, repo and datablade compose, switch 0/empty, contract names without the switch, operator command, VIZ_BRIDGE_ARG=true), from the released Dockerfile (`tests/legacy/Dockerfile-released`, git blob pinned), which `docker/voyant_entrypoint.sh` must reproduce; contract `check` and `check --json` output for 18 environments (accepted and refused); past bugs, each failing on the released files: SIGTERM not bound, first-frame wait spinning and deaf to shutdown, errors exiting 0, spin after SIGINT, voyant-api taken unpinned from "latest", Cap'n Proto unchecked, images for branches / mutable cache tags / overwritable tags / no digest, unpinned actions |
| smoke | `docker/smoke/smoke.sh IMAGE` (needs Docker and python3) | the real image: entrypoint, no command, root, voyant-api 0.4.4; the released shell-form ENTRYPOINT, built by Docker from the released lines, is exactly the command the new entrypoint runs; against voyant-api's own Meadowlark simulator (23,040 points, 10 Hz, loopback multicast) and a reference receiver built in the image from the same SDK (`docker/smoke/frame_dump.cpp`): legacy mode publishes `/point_cloud` frame `voyant_sensor`, every cloud one reference frame point for point; contract `check` and refusals byte-identical to the goldens; contract as uid 97000, read-only root, tmpfs /tmp, cap-drop ALL, no-new-privileges, host network and IPC: PID 1 is the node with the rendered parameters, clouds on the contract's topic and frame matching the reference, host stamps, device metadata, invalid points dropped, four `docker stop` and one SIGINT under load with exit 0 in under 5 s, `docker stop` with no sensor exit 0 at once and idle CPU while waiting, a port held by another process exit 1 |

Run the Python tiers locally: `python -m venv .venv && .venv/bin/pip install pytest==9.0.1
pyyaml==6.0.3`, then the commands above. `REGEN=1` rewrites goldens (legacy ones only from the
released command, never from the current script).

Not covered here (needs hardware): see docs/METROLLA_DRIVER_CONTRACT.md, "Not proven".
