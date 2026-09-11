#!/usr/bin/env bash
set -uo pipefail; cd "$(dirname "$0")/.."; rc=0
./scripts/scrub-check.sh . || rc=1
while IFS= read -r f; do jq -e . "$f" >/dev/null || { echo "bad json: $f"; rc=1; }; done < <(find exporters integrations dashboards -name 'dashboard*.json')
while IFS= read -r f; do docker compose -f "$f" config -q || { echo "bad compose: $f"; rc=1; }; done < <(find exporters integrations -name compose.yaml)
while IFS= read -r f; do
  dir=$(cd "$(dirname "$f")" && pwd); base=$(basename "$f")
  # These scrape snippets are complete, valid Prometheus configs, so a
  # promtool failure is a real error and must fail the gate. Two bugs fixed
  # here: (1) the old `|| echo note` swallowed the exit code, letting invalid
  # YAML/scrape options pass; (2) `docker run -i` forwarded this loop's stdin
  # (the `find` process substitution) into the container, draining it so only
  # the FIRST of the snippets was ever checked. Drop -i and redirect stdin.
  if ! out=$(docker run --rm --entrypoint promtool -v "$dir:/cfg:ro" prom/prometheus:v2.53.0 check config "/cfg/$base" </dev/null 2>&1); then
    echo "bad scrape config: $f"; echo "$out"; rc=1
  fi
done < <(find dashboards -name prometheus-scrape.yml)
# promtool unit tests that assert dashboard query behavior (query_test.yml).
while IFS= read -r f; do
  dir=$(cd "$(dirname "$f")" && pwd); base=$(basename "$f")
  if ! out=$(docker run --rm --entrypoint promtool -v "$dir:/cfg:ro" prom/prometheus:v2.53.0 test rules "/cfg/$base" </dev/null 2>&1); then
    echo "bad query test: $f"; echo "$out"; rc=1
  fi
done < <(find exporters integrations dashboards -name 'query_test*.yml')
while IFS= read -r f; do ./scripts/import-dashboard.sh "$f" || rc=1; done < <(find exporters integrations dashboards -name 'dashboard*.json')
[ $rc -eq 0 ] && echo "VERIFY OK"; exit $rc
