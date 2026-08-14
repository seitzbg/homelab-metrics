#!/usr/bin/env bash
set -euo pipefail; cd "$(dirname "$0")"
docker build -t hl-sleephq .
docker rm -f hl-sleephq-smoke >/dev/null 2>&1 || true
docker run -d --name hl-sleephq-smoke -e SLEEPHQ_CLIENT_ID=x -e SLEEPHQ_CLIENT_SECRET=x -p 9826:9826 hl-sleephq
for i in $(seq 1 15); do curl -sf localhost:9826/metrics >/dev/null && break; sleep 1; done
curl -sf localhost:9826/metrics | grep -q 'sleephq_scrape_success'
docker rm -f hl-sleephq-smoke >/dev/null
echo "sleephq smoke ok"
