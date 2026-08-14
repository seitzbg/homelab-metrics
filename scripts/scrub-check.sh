#!/usr/bin/env bash
set -uo pipefail
# Case-sensitive denylist.
PATTERNS='fiber\.house|bsd-unix|\b(192\.168|10)\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+|\b(seitz|bseitz|munro)\b|\.vault|prometheus-sleep|\b49361\b'
# Case-INsensitive denylist: matched via a separate `-i` pass so the rest of
# PATTERNS above stays case-sensitive (a blanket `-i` on the whole gate would
# risk false positives on common words). The internal Grafana datasource
# name for the CPAP long-retention Prometheus leaked as CamelCase
# (`PrometheusSleep`); any case mangling of that token (PROMETHEUS_SLEEP,
# PrometheusSLEEP, prometheus_SLEEP, ...) must still be caught.
CI_PATTERNS='prometheus[-_ ]?sleep'
EXCLUDES=(--exclude-dir=.git --exclude-dir=__pycache__ --exclude='scrub-check.sh' --exclude-dir=scrub_fixtures)
targets=("${@:-.}")
hits=$( { grep -rInHE  "$PATTERNS"    "${targets[@]}" "${EXCLUDES[@]}" 2>/dev/null
          grep -rInHEi "$CI_PATTERNS" "${targets[@]}" "${EXCLUDES[@]}" 2>/dev/null
        } | awk '!seen[$0]++' \
          | grep -vE '^\./\.gitignore:[0-9]+:.*\*\.vault\*')
if [ -n "$hits" ]; then echo "SCRUB FAIL:"; echo "$hits"; exit 1; fi
echo "scrub-check: clean"; exit 0
