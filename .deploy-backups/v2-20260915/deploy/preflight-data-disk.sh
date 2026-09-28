#!/bin/sh
set -eu
ROOT=/srv/vibetrading-data
mountpoint -q "$ROOT" || { echo "$ROOT is not a mountpoint" >&2; exit 1; }
test -d "$ROOT/runtime_data" && test -d "$ROOT/nats" || { echo "data directories are missing" >&2; exit 1; }
test "$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')" -ge 6291456 || { echo "less than 6GiB free on data disk" >&2; exit 1; }
