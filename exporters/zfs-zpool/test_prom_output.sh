#!/usr/bin/env bash
# Runs the collector against the captured fixture (no real zpool needed) and
# validates the resulting Prometheus text-exposition output with promtool.
#
# Needs Docker for two things:
#   - gawk (the collector requires GNU awk's 3-arg match()) isn't guaranteed
#     on the test host, so the collector itself runs inside a throwaway
#     alpine container that has it.
#   - promtool isn't installed anywhere on the test host either; it's only
#     available via the `prom/prometheus` image.
# Run this script on any host with Docker available.
set -euo pipefail
cd "$(dirname "$0")"

OUTFILE="$PWD/.zpool-test.prom"
trap 'rm -f "$OUTFILE"' EXIT

docker run --rm -v "$PWD:/work" -w /work alpine:3.20 sh -c '
  apk add --no-cache gawk bash >/dev/null &&
  ZPOOL_STATUS_FIXTURE=fixtures OUT=/work/.zpool-test.prom bash zpool-textfile.sh
'

# ---- Regression assertions on the exposition ----
fail=0
want()   { if ! grep -qF -- "$1" "$OUTFILE"; then echo "MISSING:    $1"; fail=1; fi; }
absent() { if   grep -qF -- "$1" "$OUTFILE"; then echo "UNEXPECTED: $1"; fail=1; fi; }

# Finding 8: the request-size histogram `le` is the bucket's INCLUSIVE UPPER
# bound (2*size-1), not the lower-edge label OpenZFS prints. Fixture tank has
# sync_read/ind counts 1,4,10,16 at sizes 512,4096,8388608,16777216, so the
# cumulative buckets are 1@le=1023, 5@le=8191, 15@le=16777215, and the 2^24
# overflow bucket (count 16) has NO finite upper bound — only +Inf (=31).
want   'zfs_pool_io_size_bytes_bucket{pool="tank",class="sync_read",agg="ind",le="1023"} 1'
want   'zfs_pool_io_size_bytes_bucket{pool="tank",class="sync_read",agg="ind",le="8191"} 5'
want   'zfs_pool_io_size_bytes_bucket{pool="tank",class="sync_read",agg="ind",le="16777215"} 15'
want   'zfs_pool_io_size_bytes_bucket{pool="tank",class="sync_read",agg="ind",le="+Inf"} 31'
absent 'le="33554431"'   # 2*16777216-1: a false finite bound for the overflow bucket

# Finding 9: `zpool status -p` prints error counts as exact integers, so a large
# count survives instead of being truncated (fixture backup/sdd = 1041/512/89,
# which the old suffixed "1.02K" format would have coerced to 1.02).
want 'zfs_pool_vdev_errors_total{pool="backup",vdev="sdd",type="read"} 1041'
want 'zfs_pool_vdev_errors_total{pool="backup",vdev="sdd",type="write"} 512'
want 'zfs_pool_vdev_errors_total{pool="backup",vdev="sdd",type="cksum"} 89'

[ "$fail" -eq 0 ] || { echo "zfs-zpool regression assertions FAILED"; exit 1; }

docker run --rm -i --entrypoint promtool prom/prometheus:v2.53.0 check metrics < "$OUTFILE"

echo "zfs-zpool prom output ok"
