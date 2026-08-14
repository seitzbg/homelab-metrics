#!/usr/bin/env bash
set -uo pipefail; cd "$(dirname "$0")/.."; rc=0
./scripts/scrub-check.sh . || rc=1
while IFS= read -r f; do jq -e . "$f" >/dev/null || { echo "bad json: $f"; rc=1; }; done < <(find exporters integrations dashboards -name dashboard.json)
while IFS= read -r f; do docker compose -f "$f" config -q || { echo "bad compose: $f"; rc=1; }; done < <(find exporters integrations -name compose.yaml)
while IFS= read -r f; do
  dir=$(cd "$(dirname "$f")" && pwd); base=$(basename "$f")
  docker run --rm -i --entrypoint promtool -v "$dir:/cfg:ro" prom/prometheus:v2.53.0 check config "/cfg/$base" >/dev/null 2>&1 \
    || echo "note: promtool check (snippet, expected partial): $f"
done < <(find dashboards -name prometheus-scrape.yml)
while IFS= read -r f; do ./scripts/import-dashboard.sh "$f" || rc=1; done < <(find exporters integrations dashboards -name dashboard.json)
[ $rc -eq 0 ] && echo "VERIFY OK"; exit $rc
