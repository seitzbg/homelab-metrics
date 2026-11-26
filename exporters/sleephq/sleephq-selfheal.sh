#!/usr/bin/env bash
# Daily loader for the finished SleepHQ nights.
#
# The exporter only ever publishes the LATEST record, and that record is a
# partial upload for most of the time it is the latest, so the live scrape
# never holds a night's final value. This re-emits the last LOOKBACK_DAYS days
# straight from SleepHQ and loads them into Prometheus as TSDB blocks, one
# sample at 12:00 UTC of each date. The dashboard reads exactly those samples.
#
# SleepHQ keeps revising a record for a day or more after it first appears, so
# a night loaded on one run can differ on the next. Which value Prometheus
# serves when two blocks hold the same (series, timestamp) is not documented,
# so the previous 12:00 UTC samples for the nights being reloaded are deleted
# first through the TSDB admin API (Prometheus needs --web.enable-admin-api).
#
# Run it from this folder (or through a symlink to it): it needs compose.yaml,
# .env and backfill.py beside it. Prometheus must run in Docker on this host.
#
# Config (environment):
#   PROM_CONTAINER   Prometheus container to load into, default "prometheus"
#   PROM_DATA_DIR    host path of its TSDB directory; default: whatever that
#                    container mounts at /prometheus
#   PROM_IMAGE       image providing promtool; default: the container's image
#   LOOKBACK_DAYS    days to reload, default 8 (late revisions + a week of
#                    missed runs)
set -euo pipefail

BUNDLE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PROM_CONTAINER="${PROM_CONTAINER:-prometheus}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-8}"

log() { echo "sleephq-selfheal: $*"; }

PROM_IMAGE="${PROM_IMAGE:-$(docker inspect -f '{{.Config.Image}}' "$PROM_CONTAINER")}"
PROM_DATA_DIR="${PROM_DATA_DIR:-$(docker inspect \
  -f '{{range .Mounts}}{{if eq .Destination "/prometheus"}}{{.Source}}{{end}}{{end}}' "$PROM_CONTAINER")}"
if [ -z "$PROM_DATA_DIR" ] || [ ! -d "$PROM_DATA_DIR" ]; then
  log "cannot find the TSDB directory of $PROM_CONTAINER -- set PROM_DATA_DIR"
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/blocks"
# Both containers run as the calling user so they can write to $WORK.
RUN_AS="$(id -u):$(id -g)"

FROM="$(date -u -d "-${LOOKBACK_DAYS} days" +%F)"
log "generating OpenMetrics from $FROM"

# backfill.py is not baked into the exporter image (it is an operator tool), so
# mount this folder. Credentials reach the container through compose's env_file.
# `compose run` attaches stdin by default; keep it off whatever called us.
docker compose -f "$BUNDLE/compose.yaml" run --rm --no-deps -T --user "$RUN_AS" \
  -v "$BUNDLE:/src:ro" -v "$WORK:/work" \
  --entrypoint python sleephq-exporter \
  /src/backfill.py --from-date "$FROM" --out /work/heal.openmetrics </dev/null

if ! grep -q '^sleephq_' "$WORK/heal.openmetrics"; then
  log "no samples in range -- nothing to heal"
  exit 0
fi

log "building TSDB blocks"
docker run --rm --user "$RUN_AS" -v "$WORK:/data" -w /data \
  --entrypoint promtool "$PROM_IMAGE" \
  tsdb create-blocks-from openmetrics --max-block-duration=720h \
  /data/heal.openmetrics /data/blocks

if ! ls -d "$WORK"/blocks/01* >/dev/null 2>&1; then
  log "promtool produced no blocks -- aborting without touching $PROM_DATA_DIR"
  exit 1
fi

# Stage the blocks on the TSDB's own filesystem before deleting anything, so a
# full disk or a failed copy stops the run while the stored samples still exist.
# Prometheus ignores a directory whose name is not a block id; after the deletes
# each block is published with a rename, which needs no space.
STAGE="$(mktemp -d "$PROM_DATA_DIR/.sleephq-selfheal.XXXXXX")"
trap 'rm -rf "$WORK" "$STAGE"' EXIT
log "staging blocks in $PROM_DATA_DIR"
cp -r "$WORK"/blocks/01* "$STAGE"/
chown -R --reference="$PROM_DATA_DIR" "$STAGE"

# Delete only what the new blocks replace: the (machine, night) pairs in the
# file. A machine that has no record for a date in this dump keeps its stored
# sample. start == end deletes that instant only; live scrape samples sit at
# other instants and are kept.
mapfile -t PAIRS < <(awk '/^sleephq_/ && match($0, /machine_id="[0-9]+"/) {
    print substr($0, RSTART + 12, RLENGTH - 13), $NF }' "$WORK/heal.openmetrics" | sort -u)
if [ "${#PAIRS[@]}" -eq 0 ]; then
  log "no machine_id/timestamp pairs parsed from the dump -- aborting without touching $PROM_DATA_DIR"
  exit 1
fi
log "deleting the previous samples for ${#PAIRS[@]} machine-night(s)"
# A failed delete must not stop the load. Points already deleted exist only in
# the new blocks, and the oldest of them is outside tomorrow's lookback. Load
# everything, then report the failure.
delete_failed=0
for pair in "${PAIRS[@]}"; do
  mid="${pair%% *}"; ts="${pair##* }"
  # match[]={__name__=~"sleephq_.+",machine_id="<mid>"}, URL-encoded.
  if ! docker exec "$PROM_CONTAINER" wget -q -O /dev/null --post-data='' \
      "http://localhost:9090/api/v1/admin/tsdb/delete_series?match[]=%7B__name__%3D~%22sleephq_.%2B%22%2Cmachine_id%3D%22${mid}%22%7D&start=${ts}&end=${ts}"; then
    log "delete_series failed for machine $mid at $ts (is --web.enable-admin-api set on $PROM_CONTAINER?)"
    delete_failed=1
    break
  fi
done

log "publishing blocks into $PROM_DATA_DIR and restarting $PROM_CONTAINER"
for block in "$STAGE"/01*; do
  mv "$block" "$PROM_DATA_DIR"/
done
docker restart "$PROM_CONTAINER" >/dev/null

if [ "$delete_failed" -ne 0 ]; then
  log "blocks loaded, but a previous sample could not be deleted first -- a revised night may still serve its old value"
  exit 1
fi
log "done (lookback ${LOOKBACK_DAYS}d from $FROM)"
