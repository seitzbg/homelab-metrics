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

docker run --rm -i --entrypoint promtool prom/prometheus:v2.53.0 check metrics < "$OUTFILE"

echo "zfs-zpool prom output ok"
