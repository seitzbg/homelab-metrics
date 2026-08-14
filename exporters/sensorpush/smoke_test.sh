#!/usr/bin/env bash
set -euo pipefail; cd "$(dirname "$0")"
docker build -t hl-sensorpush .
docker rm -f hl-sensorpush-smoke >/dev/null 2>&1 || true
docker run -d --name hl-sensorpush-smoke -e SENSORPUSH_EMAIL=x -e SENSORPUSH_PASSWORD=x -p 9825:9825 hl-sensorpush
for i in $(seq 1 15); do curl -sf localhost:9825/metrics >/dev/null && break; sleep 1; done
curl -sf localhost:9825/metrics | grep -q 'sensorpush_scrape_success'
docker rm -f hl-sensorpush-smoke >/dev/null
echo "sensorpush smoke ok"
