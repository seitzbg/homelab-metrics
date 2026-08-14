#!/usr/bin/env bash
# ZFS pool -> Prometheus textfile collector.
#
# Emits ZFS pool metrics as Prometheus textfile metrics. node_exporter /
# Alloy's unix exporter does not expose zpool latency/io-size histograms,
# queue depths, scrub state or per-vdev error counts; this fills that gap.
#
# Data model (deliberate — see the dashboard):
#   * Everything that can be a COUNTER is a counter. We never take a
#     1-second instantaneous `zpool iostat` sample — that prints "-" on an
#     idle pool and produces graph holes every quiet minute. Cumulative
#     kstat counters are always present; Grafana does rate().
#   * Latency / IO-size are real Prometheus histograms built from the
#     cumulative bucket counts of `zpool iostat -w` / `-r`.
#   * Capacity / health / queue depth are gauges (always present, so they
#     never had the hole problem).
#
# Aligned with Richard Elling's zpool_influxdb measurement set
# (zpool_stats / zpool_latency / zpool_io_size / zpool_vdev / scan stats)
# but expressed as Prometheus metrics.
#
# Requires gawk (GNU awk): the scrub/scan-state parser uses gawk's
# three-argument match(str, regex, array) extension, which is not available
# in mawk (Debian/Ubuntu's default /usr/bin/awk) or busybox awk. The
# throughput counters additionally require Linux ZFS's
# /proc/spl/kstat/zfs/<pool>/objset-0x* kstat files (they're skipped,
# without error, on platforms/pools where that path doesn't exist).
#
# Testing without a real pool: set ZPOOL_STATUS_FIXTURE to a fixture
# directory (see fixtures/) and the collector reads captured `zpool list` /
# `zpool status` / `zpool iostat -Hpw|-Hpr|-Hpq` / kstat-objset samples from
# it instead of shelling out to `zpool` and /proc. Set OUT to override the
# output file path directly. See test_prom_output.sh.
set -euo pipefail

TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
OUT="${OUT:-${TEXTFILE_DIR}/zpool.prom}"
TMP="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

if [ -n "${ZPOOL_STATUS_FIXTURE:-}" ]; then
  # Fixture mode — fixture directory layout (see fixtures/):
  #   zpool-list.txt        one `zpool list -Hp -o name,size,alloc,free,fragmentation,capacity,health` capture
  #   status/<pool>.txt     one `zpool status <pool>` capture per pool
  #   iostat-w/<pool>.txt   one `zpool iostat -Hpw <pool>` capture per pool
  #   iostat-r/<pool>.txt   one `zpool iostat -Hpr <pool>` capture per pool
  #   iostat-q/<pool>.txt   one `zpool iostat -Hpq <pool>` capture per pool
  #   kstat/<pool>/objset-0x*  one or more captured kstat objset files per pool
  zpool_list_full_raw()  { cat "${ZPOOL_STATUS_FIXTURE}/zpool-list.txt" 2>/dev/null; }
  zpool_list_names_raw() { cut -f1 "${ZPOOL_STATUS_FIXTURE}/zpool-list.txt" 2>/dev/null; }
  zpool_status_raw()     { cat "${ZPOOL_STATUS_FIXTURE}/status/$1.txt" 2>/dev/null; }
  zpool_iostat_w_raw()   { cat "${ZPOOL_STATUS_FIXTURE}/iostat-w/$1.txt" 2>/dev/null; }
  zpool_iostat_r_raw()   { cat "${ZPOOL_STATUS_FIXTURE}/iostat-r/$1.txt" 2>/dev/null; }
  zpool_iostat_q_raw()   { cat "${ZPOOL_STATUS_FIXTURE}/iostat-q/$1.txt" 2>/dev/null; }
  kstat_dir_for()         { printf '%s/kstat/%s' "$ZPOOL_STATUS_FIXTURE" "$1"; }
else
  zpool_list_full_raw()  { zpool list -Hp -o name,size,alloc,free,fragmentation,capacity,health 2>/dev/null; }
  zpool_list_names_raw() { zpool list -Hp -o name 2>/dev/null; }
  zpool_status_raw()     { zpool status "$1" 2>/dev/null; }
  zpool_iostat_w_raw()   { zpool iostat -Hpw "$1" 2>/dev/null; }
  zpool_iostat_r_raw()   { zpool iostat -Hpr "$1" 2>/dev/null; }
  zpool_iostat_q_raw()   { zpool iostat -Hpq "$1" 2>/dev/null; }
  kstat_dir_for()         { printf '/proc/spl/kstat/zfs/%s' "$1"; }
fi

pools="$(zpool_list_names_raw || true)"

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
# HELP zfs_pool_reads_total Cumulative read operations (summed over datasets).
# TYPE zfs_pool_reads_total counter
# HELP zfs_pool_writes_total Cumulative write operations (summed over datasets).
# TYPE zfs_pool_writes_total counter
# HELP zfs_pool_read_bytes_total Cumulative bytes read (summed over datasets).
# TYPE zfs_pool_read_bytes_total counter
# HELP zfs_pool_written_bytes_total Cumulative bytes written (summed over datasets).
# TYPE zfs_pool_written_bytes_total counter
# HELP zfs_pool_latency_seconds I/O wait latency histogram (cumulative).
# TYPE zfs_pool_latency_seconds histogram
# HELP zfs_pool_io_size_bytes I/O request size histogram (cumulative).
# TYPE zfs_pool_io_size_bytes histogram
# HELP zfs_pool_queue_depth Outstanding I/O queue depth (instantaneous).
# TYPE zfs_pool_queue_depth gauge
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
  zpool_list_full_raw |
  while IFS=$'\t' read -r name size alloc free frag cap health; do
    [ -n "$name" ] || continue
    printf 'zfs_pool_size_bytes{pool="%s"} %s\n'  "$name" "$size"
    printf 'zfs_pool_alloc_bytes{pool="%s"} %s\n' "$name" "$alloc"
    printf 'zfs_pool_free_bytes{pool="%s"} %s\n'  "$name" "$free"
    [ "$frag" != "-" ] && printf 'zfs_pool_fragmentation_ratio{pool="%s"} %s\n' "$name" "$(gawk -v v="$frag" 'BEGIN{print v/100}')"
    [ "$cap"  != "-" ] && printf 'zfs_pool_capacity_ratio{pool="%s"} %s\n'      "$name" "$(gawk -v v="$cap"  'BEGIN{print v/100}')"
    if [ "$health" = "ONLINE" ]; then printf 'zfs_pool_health{pool="%s"} 1\n' "$name"; else printf 'zfs_pool_health{pool="%s"} 0\n' "$name"; fi
    printf 'zfs_pool_health_state{pool="%s",state="%s"} 1\n' "$name" "$health"
  done

  for pool in $pools; do
    [ -n "$pool" ] || continue
    kdir="$(kstat_dir_for "$pool")"

    # ---- Cumulative throughput counters (sum the per-dataset kstats) ----
    # zpool iostat without an interval only reports since-boot *averages*,
    # not totals, so it cannot be a counter. The per-objset kstats carry
    # true monotonic reads/writes/nread/nwritten — sum them per pool.
    if compgen -G "${kdir}/objset-0x"* >/dev/null 2>&1; then
      gawk -v p="$pool" '
        $1=="reads"    {r  += $3}
        $1=="writes"   {w  += $3}
        $1=="nread"    {nr += $3}
        $1=="nwritten" {nw += $3}
        END {
          printf "zfs_pool_reads_total{pool=\"%s\"} %d\n",        p, r;
          printf "zfs_pool_writes_total{pool=\"%s\"} %d\n",       p, w;
          printf "zfs_pool_read_bytes_total{pool=\"%s\"} %d\n",   p, nr;
          printf "zfs_pool_written_bytes_total{pool=\"%s\"} %d\n", p, nw;
        }' "${kdir}"/objset-0x* 2>/dev/null
    fi

    # ---- Latency histogram (cumulative; -p makes the bucket raw ns) ----
    # Columns: lat tot_r tot_w disk_r disk_w syncq_r syncq_w asyncq_r asyncq_w scrub trim rebuild
    zpool_iostat_w_raw "$pool" | gawk -v p="$pool" '
      function cls(name, col,   key) {
        key = name SUBSEP col;
        run[key] += $(col);
        # le in seconds: bucket boundary is the upper bound in ns
        print "zfs_pool_latency_seconds_bucket{pool=\"" p "\"," name ",le=\"" ($1/1e9) "\"} " run[key];
      }
      NF>=11 {
        cls("class=\"total_wait\",op=\"read\"",   2);  cls("class=\"total_wait\",op=\"write\"",  3);
        cls("class=\"disk_wait\",op=\"read\"",    4);  cls("class=\"disk_wait\",op=\"write\"",   5);
        cls("class=\"syncq_wait\",op=\"read\"",   6);  cls("class=\"syncq_wait\",op=\"write\"",  7);
        cls("class=\"asyncq_wait\",op=\"read\"",  8);  cls("class=\"asyncq_wait\",op=\"write\"", 9);
        cls("class=\"scrub_wait\"",              10);  cls("class=\"trim_wait\"",               11);
        if (NF>=12) cls("class=\"rebuild_wait\"", 12);
      }
      END {
        for (k in run) {
          split(k, a, SUBSEP);
          print "zfs_pool_latency_seconds_bucket{pool=\"" p "\"," a[1] ",le=\"+Inf\"} " run[k];
          print "zfs_pool_latency_seconds_count{pool=\"" p "\"," a[1] "} " run[k];
        }
      }'

    # ---- I/O size histogram (cumulative; -p makes the bucket raw bytes) ----
    # Columns: size sr_ind sr_agg sw_ind sw_agg ar_ind ar_agg aw_ind aw_agg scr_ind scr_agg tr_ind tr_agg rb_ind rb_agg
    zpool_iostat_r_raw "$pool" | gawk -v p="$pool" '
      function cls(name, col,   key) {
        key = name SUBSEP col;
        run[key] += $(col);
        print "zfs_pool_io_size_bytes_bucket{pool=\"" p "\"," name ",le=\"" $1 "\"} " run[key];
      }
      NF>=9 {
        cls("class=\"sync_read\",agg=\"ind\"",   2); cls("class=\"sync_read\",agg=\"agg\"",   3);
        cls("class=\"sync_write\",agg=\"ind\"",  4); cls("class=\"sync_write\",agg=\"agg\"",  5);
        cls("class=\"async_read\",agg=\"ind\"",  6); cls("class=\"async_read\",agg=\"agg\"",  7);
        cls("class=\"async_write\",agg=\"ind\"", 8); cls("class=\"async_write\",agg=\"agg\"", 9);
        if (NF>=11) { cls("class=\"scrub\",agg=\"ind\"",  10); cls("class=\"scrub\",agg=\"agg\"",  11); }
        if (NF>=13) { cls("class=\"trim\",agg=\"ind\"",   12); cls("class=\"trim\",agg=\"agg\"",   13); }
        if (NF>=15) { cls("class=\"rebuild\",agg=\"ind\"",14); cls("class=\"rebuild\",agg=\"agg\"",15); }
      }
      END {
        for (k in run) {
          split(k, a, SUBSEP);
          print "zfs_pool_io_size_bytes_bucket{pool=\"" p "\"," a[1] ",le=\"+Inf\"} " run[k];
          print "zfs_pool_io_size_bytes_count{pool=\"" p "\"," a[1] "} " run[k];
        }
      }'

    # ---- Queue depth (gauge; pend/activ per scheduler queue) ----
    # -Hpq: name alloc free rop wop rbw wbw then 7 queues x (pend,activ)
    zpool_iostat_q_raw "$pool" | gawk -v p="$pool" '
      NF>=21 {
        split("syncq_read syncq_write asyncq_read asyncq_write scrubq trimq rebuildq", q, " ");
        for (i=0; i<7; i++) {
          printf "zfs_pool_queue_depth{pool=\"%s\",queue=\"%s\",state=\"pending\"} %s\n", p, q[i+1], $(8+i*2);
          printf "zfs_pool_queue_depth{pool=\"%s\",queue=\"%s\",state=\"active\"} %s\n",  p, q[i+1], $(9+i*2);
        }
      }'

    # ---- Scan (scrub / resilver) state + per-vdev errors ----
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
      # progress line: "  X.XX% done" or "(scan progress ...)"
      inprog && match($0, /([0-9.]+)% done/, pm) { prog = pm[1]/100 }
      # config section: device lines have STATE + READ WRITE CKSUM ints
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
