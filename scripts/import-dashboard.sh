#!/usr/bin/env bash
set -euo pipefail
DASH="$1"; PORT=3999; NAME=hl-metrics-grafana

# Static guard: every datasource reference in the file must be a template
# variable (${DS_PROMETHEUS}, ${DS_LOKI}, or any other ${...} picker var),
# or the built-in Grafana annotation datasource. Dashboard JSON represents
# that built-in two ways depending on schema version/export path: the
# older `{"type":"grafana","uid":"-- Grafana --"}` and the newer
# `{"type":"datasource","uid":"grafana"}` (verified round-trips unchanged
# through a real Grafana 11.2.0 save/fetch — not treated as an unknown/
# broken datasource reference). Both are exempt. A literal external UID is
# not portable to a stranger's Grafana and is a hard failure here.
BAD=$(jq -r '
  [.. | objects | select(has("datasource")) | .datasource]
  | .[]
  | if type=="object" then (.uid // empty)
    elif type=="string" then .
    else empty end
' "$DASH" | grep -vE '^-- Grafana --$|^grafana$|^\$\{[A-Za-z0-9_]+\}$' || true)
if [ -n "$BAD" ]; then
  echo "IMPORT FAIL: literal datasource uid(s) present (expected \${DS_PROMETHEUS}/\${DS_LOKI}/a \${...} template var/-- Grafana --):"
  echo "$BAD"
  exit 1
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p $PORT:3000 \
  -e GF_AUTH_ANONYMOUS_ENABLED=true -e GF_AUTH_ANONYMOUS_ORG_ROLE=Admin \
  grafana/grafana:11.2.0 >/dev/null
for i in $(seq 1 30); do curl -sf "http://localhost:$PORT/api/health" >/dev/null && break; sleep 2; done

# dummy datasources so the DS_PROMETHEUS/DS_LOKI "datasource" template
# variables have an option to resolve to.
curl -sf -X POST "http://localhost:$PORT/api/datasources" -H 'Content-Type: application/json' \
  -d '{"name":"prometheus","type":"prometheus","access":"proxy","url":"http://localhost:9090"}' >/dev/null || true
curl -sf -X POST "http://localhost:$PORT/api/datasources" -H 'Content-Type: application/json' \
  -d '{"name":"loki","type":"loki","access":"proxy","url":"http://localhost:3100"}' >/dev/null || true

# Save via /api/dashboards/db (NOT /api/dashboards/import — that endpoint is
# for the legacy "inputs" substitution flow; our dashboards already carry
# their datasource selection as templating variables, so a plain save is
# the correct and sufficient round-trip check).
BODY=$(jq -n --slurpfile d "$DASH" '{dashboard: ($d[0] + {id:null}), overwrite:true}')
RESP=$(curl -s -w '\n%{http_code}' -X POST "http://localhost:$PORT/api/dashboards/db" \
  -H 'Content-Type: application/json' -d "$BODY")
CODE=$(echo "$RESP" | tail -1); OUT=$(echo "$RESP" | sed '$d')
docker rm -f "$NAME" >/dev/null 2>&1 || true

if [ "$CODE" != "200" ]; then
  echo "IMPORT FAIL ($CODE): $OUT"; exit 1
fi
echo "import ok: $DASH"; exit 0
