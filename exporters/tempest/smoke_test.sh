#!/usr/bin/env bash
set -euo pipefail; cd "$(dirname "$0")"
docker build -t hl-tempest .
docker rm -f hl-tempest-smoke >/dev/null 2>&1 || true
docker run -d --name hl-tempest-smoke -e TEMPEST_TOKEN=x -e TEMPEST_STATION_ID=x -p 9827:9827 hl-tempest
for i in $(seq 1 15); do curl -sf localhost:9827/metrics >/dev/null && break; sleep 1; done
curl -sf localhost:9827/metrics | grep -q 'tempest_scrape_success'
docker rm -f hl-tempest-smoke >/dev/null
echo "tempest smoke ok"
