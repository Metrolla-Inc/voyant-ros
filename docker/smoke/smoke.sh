#!/usr/bin/env bash
# Smoke tier of the Voyant driver image (TESTING.md). Needs Docker and python3; no sudo: the fake
# sensor sends its multicast stream on the loopback interface.
# Usage: docker/smoke/smoke.sh IMAGE
#
#   1. Image facts: entrypoint, no command, root user, voyant-api 0.4.4; the released shell-form
#      ENTRYPOINT, built by Docker from the released Dockerfile's lines, is the command
#      docker/voyant_entrypoint.sh runs, byte for byte.
#   2. Fake sensor: voyant-api's own Meadowlark simulator (voyant_points_mock_stream, shipped in
#      the image) streams synthetic frames (23,040 points, 10 Hz) to the multicast group; a
#      reference receiver (frame_dump.cpp, the Voyant client without ROS) assembles the same
#      stream, and every cloud the driver publishes must be one of its frames, point for point.
#   3. Legacy mode (no contract, root, the image's own entrypoint and vendor parameters):
#      /point_cloud, frame voyant_sensor.
#   4. Contract checks and refusals in the image: byte-identical to tests/regression/golden.
#   5. Contract runs as the product runs it (uid 97000, read-only root, tmpfs /tmp, cap-drop ALL,
#      no-new-privileges, host network and IPC, no command): the node is PID 1 with the rendered
#      parameters; clouds on the contract's topic and frame; invalid points dropped; device
#      metadata; docker stop under load (four times) and SIGINT end it with exit 0; docker stop
#      with no sensor ends it at once (exit 0) and it idles while waiting; a port it cannot bind
#      ends it with exit 1.
set -euo pipefail

IMAGE=${1:?usage: smoke.sh IMAGE}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
WORK=$(mktemp -d)
chmod 0777 "$WORK"
NAME=voyant-smoke-$$
FAKE=voyant-smoke-fake-$$
REF=voyant-smoke-ref-$$
FAILED=0
HARDENED=(--user 97000:97000 --read-only --tmpfs /tmp --cap-drop ALL
          --security-opt no-new-privileges:true --network host --ipc host)
SUMMARY=${GITHUB_STEP_SUMMARY:-/dev/null}
BLOCKER_PID=

fail() { echo "SMOKE FAIL: $*" >&2; FAILED=1; }
ok() { echo "ok: $*"; }
expect() { local msg=$1; shift; if "$@"; then ok "$msg"; else fail "$msg"; fi; }
cleanup() {
    docker rm -f "$NAME" "$FAKE" "$REF" >/dev/null 2>&1 || true
    if [[ -n "$BLOCKER_PID" ]]; then kill "$BLOCKER_PID" 2>/dev/null || true; fi
    rm -rf "$WORK"
}
trap cleanup EXIT

# start_fake PORT [-d]: the SDK's Meadowlark simulator on the loopback interface (-d marks the
# centre of every frame with a non-SUCCESS drop reason).
start_fake() {
    local port=$1; shift
    docker rm -f "$FAKE" >/dev/null 2>&1 || true
    docker run -d --name "$FAKE" --network host --user 97000:97000 --read-only \
        --entrypoint voyant_points_mock_stream "$IMAGE" \
        --bind-addr 127.0.0.1:0 --group-addr "224.0.0.0:$port" "$@" >/dev/null
}
stop_fake() { docker rm -f "$FAKE" >/dev/null 2>&1 || true; }

# start_ref LABEL PORT: the reference receiver, 600 frames (60 s) at most.
start_ref() {
    docker rm -f "$REF" >/dev/null 2>&1 || true
    docker run -d --name "$REF" --network host --user 97000:97000 --read-only -v "$WORK:/work" \
        --entrypoint /work/frame_dump "$IMAGE" "0.0.0.0:$2" 224.0.0.0 127.0.0.1 600 "/work/$1.ref" >/dev/null
}
stop_ref() { docker rm -f "$REF" >/dev/null 2>&1 || true; }

# check_cloud LABEL USER TOPIC FRAME [METADATA_TOPIC]: the cloud check, in the image, as USER.
# The checker runs as the driver's uid: with host IPC, Fast DDS delivers through shared memory,
# and a subscriber of another uid gets discovery but no data.
check_cloud() {
    local label=$1 user=$2 topic=$3 frame=$4 meta=${5:-}
    set +e
    docker run --rm --network host --ipc host --user "$user" --read-only --tmpfs /tmp \
        -e ROS_HOME=/tmp/ros -e ROS_LOG_DIR=/tmp/ros/log -v "$HERE:/smoke:ro" -v "$WORK:/work:ro" \
        --entrypoint bash "$IMAGE" -c \
        "source /ros2_ws/install/setup.bash && exec python3 /smoke/check_cloud.py --topic '$topic' \
         --frame '$frame' --reference '/work/$label.ref' ${meta:+--metadata-topic '$meta'} \
         --clouds 5 --timeout 90" > "$WORK/$label.check.json" 2> "$WORK/$label.check.err"
    local code=$?
    set -e
    cat "$WORK/$label.check.json"
    if [[ $code -ne 0 ]]; then
        tail -20 "$WORK/$label.check.err" >&2
        echo "--- $label driver log (tail)"; docker logs --tail 30 "$NAME" 2>&1 || true
        echo "--- reference receiver log"; docker logs --tail 5 "$REF" 2>&1 || true
        return 1
    fi
}

# stop_exit [SIGNAL]: docker stop (SIGTERM, 10 s grace) or docker kill -s SIGNAL, then wait;
# prints "exit-code seconds".
stop_exit() {
    local code start end
    start=$(date +%s.%N)
    if [[ -n "${1:-}" ]]; then
        docker kill -s "$1" "$NAME" >/dev/null
        timeout 15 docker wait "$NAME" >/dev/null || docker stop -t 1 "$NAME" >/dev/null
    else
        docker stop -t 10 "$NAME" >/dev/null
    fi
    end=$(date +%s.%N)
    code=$(docker inspect -f '{{.State.ExitCode}}' "$NAME")
    if [[ "$code" != 0 ]]; then
        echo "--- exit $code; log (tail)" >&2
        docker logs --tail 30 "$NAME" >&2 2>&1 || true
    fi
    echo "$code $(python3 -c "print(round($end - $start, 1))")"
}

# ---------------------------------------------------------------------------------------------
echo "== 1. image facts"
facts=$(docker image inspect -f '{{json .Config.Entrypoint}} {{json .Config.Cmd}} user={{.Config.User}} workdir={{.Config.WorkingDir}}' "$IMAGE")
echo "$facts"
expect "entrypoint /voyant_entrypoint.sh, no command, root, workdir /ros2_ws" \
    test "$facts" = '["/voyant_entrypoint.sh"] null user= workdir=/ros2_ws'
expect "the image's /voyant_entrypoint.sh is this checkout's" \
    cmp -s <(docker run --rm --entrypoint cat "$IMAGE" /voyant_entrypoint.sh) "$REPO/docker/voyant_entrypoint.sh"
expect "voyant-api 0.4.4 installed" test \
    "$(docker run --rm --entrypoint dpkg-query "$IMAGE" -W -f '${Package}=${Version} ' voyant-api voyant-api-dev)" \
    = "voyant-api=0.4.4-1 voyant-api-dev=0.4.4-1 "

# The released ENTRYPOINT as Docker built it: the released Dockerfile's SHELL and ENTRYPOINT lines
# on top of this image (no network, a second), then its Config.Entrypoint.
mkdir -p "$WORK/released"
{
    echo "FROM $IMAGE"
    grep '^SHELL ' "$REPO/tests/legacy/Dockerfile-released"
    sed -n '/^ENTRYPOINT /,/^    fi$/p' "$REPO/tests/legacy/Dockerfile-released"
} > "$WORK/released/Dockerfile"
docker build -q -t "voyant-smoke-released:$$" "$WORK/released" >/dev/null
released=$(docker image inspect -f '{{json .Config.Entrypoint}}' "voyant-smoke-released:$$")
docker rmi "voyant-smoke-released:$$" >/dev/null
python3 - "$released" "$REPO/docker/voyant_entrypoint.sh" > "$WORK/released.cmp" <<'EOF'
import json, sys
entry = json.loads(sys.argv[1])
line = [l for l in open(sys.argv[2]).read().splitlines() if l.startswith("exec /bin/bash -c '")][0]
print("same" if entry[:2] == ["/bin/bash", "-c"] and len(entry) == 3
      and line == "exec /bin/bash -c '" + entry[2] + "'" else "differs: %r" % entry)
EOF
cat "$WORK/released.cmp"
expect "the released shell-form ENTRYPOINT is /bin/bash -c with exactly the command voyant_entrypoint.sh runs" \
    grep -qx same "$WORK/released.cmp"

echo "== 2. reference receiver: build frame_dump against the image's voyant-api"
docker run --rm -v "$HERE:/smoke:ro" -v "$WORK:/work" --entrypoint bash "$IMAGE" -c \
    "g++ -std=c++17 -O2 /smoke/frame_dump.cpp -I/usr/include/voyant_api \
     -I/usr/include/voyant_api/cbindings -I/usr/include/voyant_api/protos \
     -I/usr/include/voyant_api/proto_wrappers -lvoyant_client -lpoints_client -lvoyant_protos \
     -llogging_utils -lcapnp -lkj -lpthread -ldl -lm -o /work/frame_dump && chmod 0755 /work/frame_dump"
expect "frame_dump built" test -x "$WORK/frame_dump"

# ---------------------------------------------------------------------------------------------
echo "== 3. legacy mode (no contract): the image's entrypoint and the vendor's sensor_params.yaml"
start_fake 4444
start_ref legacy 4444
docker run -d --name "$NAME" --network host --ipc host "$IMAGE" >/dev/null
expect "legacy: /point_cloud, frame voyant_sensor, host stamps, every cloud a reference frame" \
    check_cloud legacy 0:0 /point_cloud voyant_sensor /device_metadata
docker logs "$NAME" > "$WORK/legacy.log" 2>&1 || true
expect "legacy: the released entrypoint's output" \
    grep -q "Starting with minimal sensor driver" "$WORK/legacy.log"
read -r code secs < <(stop_exit)
echo "legacy: docker stop exit $code after ${secs}s (informational: the released entrypoint's bash is PID 1 and does not pass SIGTERM on)"
echo "Legacy docker stop: exit $code after ${secs} s (bash is PID 1; not changed)." >> "$SUMMARY"
docker rm -f "$NAME" >/dev/null
stop_ref

# ---------------------------------------------------------------------------------------------
echo "== 4. contract check and refusals in the image (goldens)"
for case in "$REPO"/tests/regression/cases/*.env; do
    stem=$(basename "$case" .env)
    switch=(-e METROLLA_DRIVER_CONTRACT=1)
    grep -q '^METROLLA_DRIVER_CONTRACT=' "$case" && switch=()
    for json in "" "--json"; do
        golden="$REPO/tests/regression/golden/contract/$stem${json:+.json}.golden"
        set +e
        # shellcheck disable=SC2086  # $json is empty or one word
        docker run --rm "${HARDENED[@]}" "${switch[@]}" --env-file "$case" "$IMAGE" check $json \
            > "$WORK/out" 2> "$WORK/err"
        code=$?
        set -e
        { echo "exit: $code"; echo "--- stdout"; cat "$WORK/out"; echo "--- stderr"; cat "$WORK/err"; } > "$WORK/got"
        if cmp -s "$WORK/got" "$golden"; then ok "check $stem ${json}"; else
            fail "check $stem ${json}: differs from its golden"; diff "$golden" "$WORK/got" | head -20 || true; fi
    done
done
set +e
docker run --rm "${HARDENED[@]}" -e METROLLA_DRIVER_CONTRACT=1 "$IMAGE" > "$WORK/out" 2>&1
code=$?
set -e
expect "contract with nothing set: exit 1, refusing to start" \
    bash -c "[[ $code -eq 1 ]] && grep -q 'voyant-driver: refusing to start: LIDAR_MODEL is not set' '$WORK/out'"
set +e
docker run --rm "${HARDENED[@]}" -e METROLLA_DRIVER_CONTRACT=yes "$IMAGE" > "$WORK/out" 2>&1
code=$?
set -e
expect "contract switch 'yes': exit 1, refused" \
    bash -c "[[ $code -eq 1 ]] && grep -q \"METROLLA_DRIVER_CONTRACT='yes' is not a contract version\" '$WORK/out'"

# ---------------------------------------------------------------------------------------------
echo "== 5. contract runs (uid 97000, read-only, tmpfs /tmp, cap-drop ALL, host network and IPC)"
contract_start() {  # extra -e args...
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" "${HARDENED[@]}" -e METROLLA_DRIVER_CONTRACT=1 \
        -e LIDAR_MODEL=meadowlark -e SENSOR_IP=127.0.0.2 "$@" "$IMAGE" >/dev/null
}

start_ref east 4444
contract_start -e SENSOR_ID=east -e FRAME_ID=east_lidar
expect "contract: /east/point_cloud, frame east_lidar, host stamps, every cloud a reference frame, metadata" \
    check_cloud east 97000:97000 /east/point_cloud east_lidar /east/device_metadata
pid1=$(docker exec "$NAME" cat /proc/1/cmdline | tr '\0' ' ')
echo "PID 1: $pid1"
expect "the node is PID 1 with the rendered parameters" \
    test "$pid1" = "/ros2_ws/install/voyant_ros/lib/voyant_ros/voyant_sensor_node --ros-args -r __ns:=/east -r point_cloud:=point_cloud --params-file /tmp/voyant/params.yaml "
docker run --rm "${HARDENED[@]}" -e METROLLA_DRIVER_CONTRACT=1 -e LIDAR_MODEL=meadowlark \
    -e SENSOR_IP=127.0.0.2 -e SENSOR_ID=east -e FRAME_ID=east_lidar "$IMAGE" check | sed '$d' > "$WORK/expected.yaml"
expect "the file the node read is what check renders" \
    cmp -s <(docker exec "$NAME" cat /tmp/voyant/params.yaml) "$WORK/expected.yaml"
expect "running as uid 97000" test "$(docker exec "$NAME" id -u)" = 97000
stats=$(docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' "$NAME")
echo "Contract, simulator at 23,040 points x 10 Hz: CPU and memory $stats" | tee -a "$SUMMARY"
read -r code secs < <(stop_exit)
expect "docker stop under load: exit 0 (got $code after ${secs}s)" test "$code" = 0
expect "docker stop under load took under 5 s (${secs}s)" python3 -c "import sys; sys.exit(0 if $secs < 5 else 1)"
docker logs "$NAME" > "$WORK/east.log" 2>&1 || true
expect "the node logged its clean stop" grep -q "Shutting down the node" "$WORK/east.log"
stop_ref
for i in 1 2 3; do
    contract_start -e SENSOR_ID=east
    sleep $((4 + i))
    read -r code secs < <(stop_exit)
    expect "repeat docker stop $i under load: exit 0 (got $code after ${secs}s)" test "$code" = 0
done
contract_start
sleep 6
read -r code secs < <(stop_exit INT)
expect "SIGINT under load: exit 0 (got $code after ${secs}s)" test "$code" = 0
expect "SIGINT under load took under 5 s (${secs}s)" python3 -c "import sys; sys.exit(0 if $secs < 5 else 1)"
docker rm -f "$NAME" >/dev/null
stop_fake

# Invalid points dropped: the simulator marks the centre of each frame; port 4500; absolute topic.
start_fake 4500 -d
start_ref drop 4500
contract_start -e DATA_PORT=4500 -e POINTCLOUD_TOPIC=/lidar/points
expect "contract (port 4500, absolute topic): /lidar/points, only the SUCCESS points of each frame" \
    check_cloud drop 97000:97000 /lidar/points voyant_sensor
expect "points were dropped (published fewer than the frame had)" python3 - "$WORK/drop.check.json" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))["matched"]
sys.exit(0 if m and all(x["points"] < x["of"] for x in m) else 1)
EOF
read -r code secs < <(stop_exit)
expect "docker stop: exit 0 (got $code)" test "$code" = 0
docker rm -f "$NAME" >/dev/null
stop_ref
stop_fake

# No sensor: waits idle, stops at once.
contract_start -e DATA_PORT=4600
sleep 6
cpu=$(docker stats --no-stream --format '{{.CPUPerc}}' "$NAME" | tr -d '%')
echo "waiting for a sensor: CPU ${cpu}%"
expect "waiting for the first frame idles (CPU ${cpu}% < 20%)" python3 -c "import sys; sys.exit(0 if float('$cpu') < 20 else 1)"
read -r code secs < <(stop_exit)
expect "docker stop with no sensor: exit 0 (got $code after ${secs}s)" test "$code" = 0
expect "docker stop with no sensor took under 5 s (${secs}s)" python3 -c "import sys; sys.exit(0 if $secs < 5 else 1)"
docker logs "$NAME" > "$WORK/nosensor.log" 2>&1 || true
expect "it said it stopped before the first frame" grep -q "Stopped before the first frame arrived" "$WORK/nosensor.log"
docker rm -f "$NAME" >/dev/null

# A port it cannot bind (held without SO_REUSEADDR): exit 1, not a silent hang.
python3 -c "import socket, time; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(('0.0.0.0', 4700)); time.sleep(120)" &
BLOCKER_PID=$!
sleep 1
contract_start -e DATA_PORT=4700
set +e
timeout 30 docker wait "$NAME" > "$WORK/wait" 2>&1
set -e
code=$(docker inspect -f '{{.State.ExitCode}}' "$NAME")
running=$(docker inspect -f '{{.State.Running}}' "$NAME")
docker logs "$NAME" > "$WORK/busy.log" 2>&1 || true
tail -5 "$WORK/busy.log"
expect "port in use: the node exits 1 (running=$running, exit $code)" test "$running/$code" = false/1
expect "port in use: logged as fatal" grep -q "Fatal" "$WORK/busy.log"
docker rm -f "$NAME" >/dev/null
kill "$BLOCKER_PID" 2>/dev/null || true
BLOCKER_PID=

if [[ $FAILED -ne 0 ]]; then
    echo "SMOKE: FAILED" >&2
    exit 1
fi
echo "SMOKE: all checks passed"
