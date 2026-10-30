#!/usr/bin/env sh
set -eu
DATA_MOUNT=${DATA_MOUNT:-/srv/vibetrading-data}
mountpoint -q "$DATA_MOUNT" || { echo "refusing to start: $DATA_MOUNT is not a mountpoint" >&2; exit 20; }
for dir in "$DATA_MOUNT/runtime_data" "$DATA_MOUNT/nats"; do
  test -d "$dir" || { echo "missing data directory: $dir" >&2; exit 21; }
  test -w "$dir" || { echo "data directory is not writable: $dir" >&2; exit 22; }
done
FREE=$(df -P -B1 "$DATA_MOUNT" | awk 'NR==2 {print $4}')
echo "data disk preflight OK: mount=$DATA_MOUNT free=$FREE"

