#!/usr/bin/env bash
# ZFS pool -> Prometheus textfile collector.
#
# Emits pool capacity/health, scrub/resilver state, and per-vdev
# read/write/checksum error counters as Prometheus textfile metrics, for
# node_exporter's --collector.textfile.directory. node_exporter's own
# collectors don't expose any of this (zpool capacity/fragmentation, scan
# state, per-vdev error counts) so this fills that gap.
#
# Deliberately scoped to what `zpool list` and `zpool status` alone provide:
# capacity/health/scrub-state/vdev-error metrics. Per-op latency/IO-size
# histograms and queue-depth stats (from `zpool iostat -w/-r/-q`) and
# ARC/ZIL stats (from /proc/spl/kstat/zfs) are intentionally left out — they
# depend on kernel-module internals and exact `zpool iostat` column layouts
# that vary across ZFS versions/platforms, which makes them brittle outside
# one known fleet.
#
# Requires gawk (GNU awk): the scrub/scan-state parser uses gawk's
# three-argument match(str, regex, array) extension, which is not available
# in mawk (Debian/Ubuntu's default /usr/bin/awk) or busybox awk.
#
# Testing without a real pool: set ZPOOL_STATUS_FIXTURE to a captured
# `zpool list` + `zpool status` sample (see fixtures/zpool-status.txt) and
# the collector reads from it instead of shelling out to `zpool`. Set OUT to
# override the output file path directly. See test_prom_output.sh.
set -euo pipefail

TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
OUT="${OUT:-${TEXTFILE_DIR}/zpool.prom}"
TMP="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

if [ -n "${ZPOOL_STATUS_FIXTURE:-}" ]; then
  # Fixture mode: fixture file holds two sections, delimited by markers,
  # matching the real `zpool list -Hp -o ...` and `zpool status` output.
  zpool_list_raw() {
    awk '/^=== zpool list/{f=1;next} /^=== zpool status/{f=0} f' "$ZPOOL_STATUS_FIXTURE"
  }
  zpool_status_raw() {
    # $1 = pool name. The fixture's status section can hold multiple
    # pools' output concatenated (as real `zpool status` with no args
    # does) — extract just the block for the requested pool.
    awk -v pool="$1" '
      /^=== zpool status/ { f=1; next }
      f && /^  pool: / { on = ($0 == "  pool: " pool) }
      f && on { print }
    ' "$ZPOOL_STATUS_FIXTURE"
  }
else
  zpool_list_raw() { zpool list -Hp -o name,size,alloc,free,fragmentation,capacity,health 2>/dev/null; }
  zpool_status_raw() { zpool status "$1" 2>/dev/null; }
fi

LIST_OUTPUT="$(zpool_list_raw || true)"

{
  cat <<'EOF'
# HELP zfs_pool_size_bytes Total pool size.
# TYPE zfs_pool_size_bytes gauge
# HELP zfs_pool_alloc_bytes Allocated space in the pool.
# TYPE zfs_pool_alloc_bytes gauge
# HELP zfs_pool_free_bytes Free space in the pool.
# TYPE zfs_pool_free_bytes gauge
# HELP zfs_pool_capacity_ratio Fraction of pool capacity used (0-1).
# TYPE zfs_pool_capacity_ratio gauge
# HELP zfs_pool_fragmentation_ratio Pool fragmentation (0-1).
# TYPE zfs_pool_fragmentation_ratio gauge
# HELP zfs_pool_health Pool health, 1 if ONLINE else 0.
# TYPE zfs_pool_health gauge
# HELP zfs_pool_health_state Pool health state (info metric).
# TYPE zfs_pool_health_state gauge
# HELP zfs_pool_scrub_state Scan state (info metric: idle/scrub/resilver/finished).
# TYPE zfs_pool_scrub_state gauge
# HELP zfs_pool_scrub_in_progress 1 while a scrub or resilver is running.
# TYPE zfs_pool_scrub_in_progress gauge
# HELP zfs_pool_scrub_progress_ratio Fraction of the running scan completed (0-1).
# TYPE zfs_pool_scrub_progress_ratio gauge
# HELP zfs_pool_last_scrub_timestamp_seconds Unix time the last scan finished.
# TYPE zfs_pool_last_scrub_timestamp_seconds gauge
# HELP zfs_pool_scrub_repaired_bytes Bytes repaired by the last completed scan.
# TYPE zfs_pool_scrub_repaired_bytes gauge
# HELP zfs_pool_scrub_errors Errors reported by the last completed scan.
# TYPE zfs_pool_scrub_errors gauge
# HELP zfs_pool_vdev_errors_total Per-vdev cumulative error counters.
# TYPE zfs_pool_vdev_errors_total counter
# HELP zfs_pool_vdev_state Per-vdev state (info metric).
# TYPE zfs_pool_vdev_state gauge
# HELP zfs_textfile_last_run_timestamp_seconds Unix time of last successful run.
# TYPE zfs_textfile_last_run_timestamp_seconds gauge
EOF

  # ---- Capacity / health (gauges; -p parseable, -H no header) ----
  echo "$LIST_OUTPUT" | while IFS=$'\t' read -r name size alloc free frag cap health; do
    [ -n "$name" ] || continue
    printf 'zfs_pool_size_bytes{pool="%s"} %s\n'  "$name" "$size"
    printf 'zfs_pool_alloc_bytes{pool="%s"} %s\n' "$name" "$alloc"
    printf 'zfs_pool_free_bytes{pool="%s"} %s\n'  "$name" "$free"
    [ "$frag" != "-" ] && printf 'zfs_pool_fragmentation_ratio{pool="%s"} %s\n' "$name" "$(gawk -v v="$frag" 'BEGIN{print v/100}')"
    [ "$cap"  != "-" ] && printf 'zfs_pool_capacity_ratio{pool="%s"} %s\n'      "$name" "$(gawk -v v="$cap"  'BEGIN{print v/100}')"
    if [ "$health" = "ONLINE" ]; then printf 'zfs_pool_health{pool="%s"} 1\n' "$name"; else printf 'zfs_pool_health{pool="%s"} 0\n' "$name"; fi
    printf 'zfs_pool_health_state{pool="%s",state="%s"} 1\n' "$name" "$health"
  done

  # ---- Scan (scrub / resilver) state + per-vdev errors, one pool at a time ----
  echo "$LIST_OUTPUT" | cut -f1 | while IFS= read -r pool; do
    [ -n "$pool" ] || continue
    zpool_status_raw "$pool" | gawk -v p="$pool" '
      function emitstate(s) {
        for (i=1;i<=4;i++) {
          st = (i==1?"idle":(i==2?"scrub":(i==3?"resilver":"finished")));
          printf "zfs_pool_scrub_state{pool=\"%s\",state=\"%s\"} %d\n", p, st, (st==s?1:0);
        }
      }
      /^[[:space:]]*scan:/ {
        line = $0;
        if (line ~ /scrub in progress/)        { state="scrub";    inprog=1 }
        else if (line ~ /resilver in progress/){ state="resilver"; inprog=1 }
        else if (line ~ /scrub repaired/)      { state="finished"; inprog=0;
          if (match(line, /repaired ([0-9.]+[BKMGTP]?)/, m)) rep=m[1];
          if (match(line, /with ([0-9]+) errors/, e)) errs=e[1];
          if (match(line, /on (.+)$/, d)) when=d[1];
        }
        else                                   { state="idle";     inprog=0 }
      }
      inprog && match($0, /([0-9.]+)% done/, pm) { prog = pm[1]/100 }
      /^[[:space:]]+[A-Za-z0-9]/ && $2 ~ /^(ONLINE|DEGRADED|FAULTED|OFFLINE|UNAVAIL|REMOVED|SPLIT)$/ && NF>=5 {
        v=$1;
        printf "zfs_pool_vdev_state{pool=\"%s\",vdev=\"%s\",state=\"%s\"} 1\n", p, v, $2;
        printf "zfs_pool_vdev_errors_total{pool=\"%s\",vdev=\"%s\",type=\"read\"} %s\n",  p, v, $3+0;
        printf "zfs_pool_vdev_errors_total{pool=\"%s\",vdev=\"%s\",type=\"write\"} %s\n", p, v, $4+0;
        printf "zfs_pool_vdev_errors_total{pool=\"%s\",vdev=\"%s\",type=\"cksum\"} %s\n", p, v, $5+0;
      }
      END {
        if (state=="") state="idle";
        emitstate(state);
        printf "zfs_pool_scrub_in_progress{pool=\"%s\"} %d\n", p, (inprog?1:0);
        if (inprog) printf "zfs_pool_scrub_progress_ratio{pool=\"%s\"} %s\n", p, (prog==""?0:prog);
        if (errs!="") printf "zfs_pool_scrub_errors{pool=\"%s\"} %s\n", p, errs;
        if (when!="") {
          cmd = "date -d \"" when "\" +%s 2>/dev/null";
          cmd | getline ts; close(cmd);
          if (ts!="") printf "zfs_pool_last_scrub_timestamp_seconds{pool=\"%s\"} %s\n", p, ts;
        }
        if (rep!="") {
          n=rep; sub(/[BKMGTP]$/,"",n); u=substr(rep,length(rep),1);
          mul=1; if(u=="K")mul=1024; else if(u=="M")mul=1024^2; else if(u=="G")mul=1024^3;
          else if(u=="T")mul=1024^4; else if(u=="P")mul=1024^5;
          printf "zfs_pool_scrub_repaired_bytes{pool=\"%s\"} %d\n", p, n*mul;
        }
      }'
  done

  printf 'zfs_textfile_last_run_timestamp_seconds %s\n' "$(date +%s)"
} > "$TMP"

chmod 0644 "$TMP"
mv "$TMP" "$OUT"
trap - EXIT
