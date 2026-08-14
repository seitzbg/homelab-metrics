# zfs-zpool-textfile

A Prometheus [textfile collector](https://github.com/prometheus/node_exporter#textfile-collector)
for ZFS pools. `node_exporter`'s built-in ZFS collector only exposes ARC/ZIL
kstat counters — it has no pool capacity/fragmentation, no scrub/resilver
state, no per-vdev error counts, and no I/O latency/size histograms or queue
depths. This script fills that gap by shelling out to `zpool list` /
`zpool status` / `zpool iostat -Hpw|-Hpr|-Hpq` and reading the Linux ZFS
`/proc/spl/kstat/zfs/<pool>/objset-0x*` kstat files, then writes a `.prom`
file that `node_exporter` picks up.

The collector iterates every pool on the host (`zpool list -Hp -o name`) —
there is no hardcoded pool name, so it adapts automatically as pools are
added or removed.

Data model (deliberate — see the dashboard):

- Everything that can be a COUNTER is a counter. A single instantaneous
  `zpool iostat` sample prints `-` on an idle pool and produces graph holes
  every quiet minute, so throughput is instead built by summing the
  cumulative, monotonic per-dataset kstat counters (`reads`/`writes`/
  `nread`/`nwritten` from `/proc/spl/kstat/zfs/<pool>/objset-0x*`); Grafana
  does `rate()`.
- Latency / IO-size are real Prometheus histograms built from the
  cumulative bucket counts of `zpool iostat -Hpw` / `-Hpr`.
- Capacity / health / queue depth are gauges (always present, so they never
  had the hole problem).

Aligned with Richard Elling's [zpool_influxdb](https://github.com/richardelling/tools)
measurement set (`zpool_stats` / `zpool_latency` / `zpool_io_size` /
`zpool_vdev` / scan stats) but expressed as Prometheus metrics.

## Prerequisites

- ZFS with at least one pool, and permission to run `zpool` as the user the
  collector runs as (root, in practice — `zpool status`/`zpool list`/
  `zpool iostat` are privileged on most distros).
- `gawk` (GNU awk). The scrub/scan-state parser uses gawk's three-argument
  `match(str, regex, array)` extension, which mawk (Debian/Ubuntu's default
  `/usr/bin/awk`) and busybox awk do not support. Install it explicitly —
  don't assume the system `awk` is gawk:
  ```bash
  apt-get install -y gawk   # Debian/Ubuntu
  ```
- Linux ZFS with `/proc/spl/kstat/zfs/<pool>/objset-0x*` present, for the
  throughput counters (`zfs_pool_reads_total`, `zfs_pool_writes_total`,
  `zfs_pool_read_bytes_total`, `zfs_pool_written_bytes_total`). On a
  platform/pool where that path doesn't exist, those four families are
  silently skipped for that pool — everything else (capacity, scrub state,
  vdev errors, latency, IO-size, queue depth, which all come from `zpool`
  subcommands rather than `/proc`) is unaffected.
- `zpool iostat -w`/`-r`/`-q` support (OpenZFS 2.x+) for the latency/IO-size
  histograms and queue-depth gauges.
- `node_exporter` running with `--collector.textfile.directory` pointed at
  the same directory this collector writes to.
- (For the paired dashboard's "ARC / ZIL" row) `node_exporter` with its
  built-in ZFS collector enabled — see [Dashboard](#dashboard).

## Install

```bash
install -m 0755 zpool-textfile.sh /usr/local/bin/zpool-textfile.sh
install -d -m 0755 /var/lib/node_exporter/textfile_collector
install -m 0644 zpool-textfile.service /etc/systemd/system/zpool-textfile.service
systemctl daemon-reload
```

Point `node_exporter` at the textfile directory:

```
node_exporter --collector.textfile.directory=/var/lib/node_exporter/textfile_collector
```

The service is `Type=oneshot` — it writes one `.prom` file and exits. Run it
periodically with a systemd timer:

```ini
# /etc/systemd/system/zpool-textfile.timer
[Unit]
Description=Run zpool-textfile.service periodically

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
```

```bash
systemctl enable --now zpool-textfile.timer
```

(A plain cron entry — `* * * * * root /usr/local/bin/zpool-textfile.sh` —
works just as well if you'd rather not add a timer unit. The histogram/kstat
reads are cheap regardless of pool size, so a 60s cadence is fine even on
busy pools.)

## Configuration (environment)

| Variable | Default | Description |
|---|---|---|
| `TEXTFILE_DIR` | `/var/lib/node_exporter/textfile_collector` | Directory the `.prom` file is written into |
| `OUT` | `${TEXTFILE_DIR}/zpool.prom` | Full output path override (also used by the test) |
| `ZPOOL_STATUS_FIXTURE` | *(unset)* | Path to a fixture directory of captured `zpool`/kstat output; when set, the collector reads from it instead of running `zpool`/reading `/proc` — see [Testing](#testing) |

## Exposed metrics

All gauges unless noted. Everything is labeled `{pool}`.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `zfs_pool_size_bytes` | gauge | `pool` | Total pool size |
| `zfs_pool_alloc_bytes` | gauge | `pool` | Allocated space in the pool |
| `zfs_pool_free_bytes` | gauge | `pool` | Free space in the pool |
| `zfs_pool_capacity_ratio` | gauge | `pool` | Fraction of pool capacity used (0-1) |
| `zfs_pool_fragmentation_ratio` | gauge | `pool` | Pool fragmentation (0-1) |
| `zfs_pool_health` | gauge | `pool` | 1 if `ONLINE`, else 0 |
| `zfs_pool_health_state` | gauge | `pool,state` | Info metric: exact health string (`ONLINE`/`DEGRADED`/`FAULTED`/...) |
| `zfs_pool_reads_total` | counter | `pool` | Cumulative read operations, summed across the pool's datasets (from kstat) |
| `zfs_pool_writes_total` | counter | `pool` | Cumulative write operations, summed across the pool's datasets (from kstat) |
| `zfs_pool_read_bytes_total` | counter | `pool` | Cumulative bytes read, summed across datasets (from kstat) |
| `zfs_pool_written_bytes_total` | counter | `pool` | Cumulative bytes written, summed across datasets (from kstat) |
| `zfs_pool_latency_seconds` (`_bucket`/`_count`) | histogram | `pool,class,op,le` | I/O wait latency, cumulative buckets built from `zpool iostat -Hpw`; `class` = `total_wait`/`disk_wait`/`syncq_wait`/`asyncq_wait`/`scrub_wait`/`trim_wait`/`rebuild_wait`, `op` = `read`/`write` (where applicable) |
| `zfs_pool_io_size_bytes` (`_bucket`/`_count`) | histogram | `pool,class,agg,le` | I/O request size, cumulative buckets built from `zpool iostat -Hpr`; `class` = `sync_read`/`sync_write`/`async_read`/`async_write`/`scrub`/`trim`/`rebuild`, `agg` = `ind` (individual) / `agg` (aggregated) |
| `zfs_pool_queue_depth` | gauge | `pool,queue,state` | Outstanding I/O queue depth from `zpool iostat -Hpq`; `queue` = `syncq_read`/`syncq_write`/`asyncq_read`/`asyncq_write`/`scrubq`/`trimq`/`rebuildq`, `state` = `pending`/`active` |
| `zfs_pool_scrub_state` | gauge | `pool,state` | Info metric: one of `idle`/`scrub`/`resilver`/`finished`, 1 for the active state |
| `zfs_pool_scrub_in_progress` | gauge | `pool` | 1 while a scrub or resilver is running |
| `zfs_pool_scrub_progress_ratio` | gauge | `pool` | Fraction of the running scan completed (0-1), only while running |
| `zfs_pool_last_scrub_timestamp_seconds` | gauge | `pool` | Unix time the last scan finished, only after a completed scan |
| `zfs_pool_scrub_repaired_bytes` | gauge | `pool` | Bytes repaired by the last completed scan |
| `zfs_pool_scrub_errors` | gauge | `pool` | Errors reported by the last completed scan |
| `zfs_pool_vdev_state` | gauge | `pool,vdev,state` | Info metric: 1 for each vdev's current state |
| `zfs_pool_vdev_errors_total` | counter | `pool,vdev,type` | Per-vdev cumulative read/write/checksum error counters (`type="read\|write\|cksum"`) |
| `zfs_textfile_last_run_timestamp_seconds` | gauge | *(none)* | Unix time of the last successful collector run — the metric to alert on for collector health |

## Testing

`zpool-textfile.sh` accepts a `ZPOOL_STATUS_FIXTURE` env var pointing at a
fixture directory, so it can be tested without a real pool. `fixtures/`
holds a captured sample for two pools — `tank` (healthy, mirrored, finished
scrub, no errors) and `backup` (degraded raidz1 with a faulted disk and
nonzero read/write/checksum errors, scrub in progress) — laid out as:

```
fixtures/
  zpool-list.txt         # `zpool list -Hp -o name,size,alloc,free,fragmentation,capacity,health`
  status/<pool>.txt      # `zpool status <pool>`
  iostat-w/<pool>.txt    # `zpool iostat -Hpw <pool>`
  iostat-r/<pool>.txt    # `zpool iostat -Hpr <pool>`
  iostat-q/<pool>.txt    # `zpool iostat -Hpq <pool>`
  kstat/<pool>/objset-0x*  # captured /proc/spl/kstat/zfs/<pool>/objset-0x* samples
```

`promtool` is not installed locally, and `gawk` isn't guaranteed to be
present either — both are only pulled in via Docker, so run the test on a
host with Docker:

```bash
bash test_prom_output.sh
```

This runs the collector (inside a throwaway `alpine` container that has
`gawk`) against the fixture, writes a temporary `.prom` file, and validates
it with `docker run --rm -i --entrypoint promtool prom/prometheus:v2.53.0
check metrics`.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus` template
variable at your Prometheus datasource. `Instance` and `Pool` variables
scope every panel. Rows: pool health & capacity, throughput/IOPS/bandwidth/
queue depth, latency distribution (repeated per instance), I/O size
distribution (repeated per instance), devices (vdev state + per-vdev
errors), and ARC/ZIL.

The **ARC / ZIL** row (`node_zfs_arc_*`, `node_zfs_zil_*`) is fed by
`node_exporter`'s own built-in ZFS collector, not by this script — it's a
standard companion on any node_exporter running on a ZFS host (enabled by
default when node_exporter detects `/proc/spl/kstat/zfs/arcstats`). Every
other row is fed entirely by `zpool-textfile.sh`'s metrics above.
