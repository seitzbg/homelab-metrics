# zfs-zpool-textfile

A Prometheus [textfile collector](https://github.com/prometheus/node_exporter#textfile-collector)
for ZFS pools. `node_exporter`'s built-in collectors don't expose pool
capacity/fragmentation, scrub/resilver state, or per-vdev error counts —
this script fills that gap by shelling out to `zpool list` / `zpool status`
and writing a `.prom` file that `node_exporter` picks up.

The collector iterates every pool on the host (`zpool list -Hp -o name`) —
there is no hardcoded pool name, so it adapts automatically as pools are
added or removed.

Scoped deliberately to what `zpool list` and `zpool status` alone provide:
capacity, health, scrub/scan state, and per-vdev read/write/checksum error
counters. Per-op latency/IO-size histograms and queue-depth stats (from
`zpool iostat -w/-r/-q`) and ARC/ZIL stats (from `/proc/spl/kstat/zfs`) are
intentionally out of scope — they depend on kernel-module internals and
exact `zpool iostat` column layouts that vary across ZFS versions and
platforms, which makes them brittle to ship generically.

## Prerequisites

- ZFS with at least one pool, and permission to run `zpool` as the user the
  collector runs as (root, in practice — `zpool status`/`zpool list` are
  privileged on most distros).
- `gawk` (GNU awk). The scrub/scan-state parser uses gawk's three-argument
  `match(str, regex, array)` extension, which mawk (Debian/Ubuntu's default
  `/usr/bin/awk`) and busybox awk do not support. Install it explicitly —
  don't assume the system `awk` is gawk:
  ```bash
  apt-get install -y gawk   # Debian/Ubuntu
  ```
- `node_exporter` running with `--collector.textfile.directory` pointed at
  the same directory this collector writes to.

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
works just as well if you'd rather not add a timer unit.)

## Configuration (environment)

| Variable | Default | Description |
|---|---|---|
| `TEXTFILE_DIR` | `/var/lib/node_exporter/textfile_collector` | Directory the `.prom` file is written into |
| `OUT` | `${TEXTFILE_DIR}/zpool.prom` | Full output path override (also used by the test) |
| `ZPOOL_STATUS_FIXTURE` | *(unset)* | Path to a captured `zpool list`/`zpool status` sample; when set, the collector reads from it instead of running `zpool` — see [Testing](#testing) |

## Exposed metrics

All gauges unless noted. Everything is labeled `{pool}`; per-vdev metrics
additionally carry `{vdev}`.

| Metric | Type | Description |
|---|---|---|
| `zfs_pool_size_bytes` | gauge | Total pool size |
| `zfs_pool_alloc_bytes` | gauge | Allocated space in the pool |
| `zfs_pool_free_bytes` | gauge | Free space in the pool |
| `zfs_pool_capacity_ratio` | gauge | Fraction of pool capacity used (0-1) |
| `zfs_pool_fragmentation_ratio` | gauge | Pool fragmentation (0-1) |
| `zfs_pool_health` | gauge | 1 if `ONLINE`, else 0 |
| `zfs_pool_health_state{state}` | gauge | Info metric: exact health string (`ONLINE`/`DEGRADED`/`FAULTED`/...) |
| `zfs_pool_scrub_state{state}` | gauge | Info metric: one of `idle`/`scrub`/`resilver`/`finished`, 1 for the active state |
| `zfs_pool_scrub_in_progress` | gauge | 1 while a scrub or resilver is running |
| `zfs_pool_scrub_progress_ratio` | gauge | Fraction of the running scan completed (0-1), only while running |
| `zfs_pool_last_scrub_timestamp_seconds` | gauge | Unix time the last scan finished, only after a completed scan |
| `zfs_pool_scrub_repaired_bytes` | gauge | Bytes repaired by the last completed scan |
| `zfs_pool_scrub_errors` | gauge | Errors reported by the last completed scan |
| `zfs_pool_vdev_state{vdev,state}` | gauge | Info metric: 1 for each vdev's current state |
| `zfs_pool_vdev_errors_total{vdev,type}` | counter | Per-vdev cumulative read/write/checksum error counters (`type="read\|write\|cksum"`) |
| `zfs_textfile_last_run_timestamp_seconds` | gauge | Unix time of the last successful collector run — the metric to alert on for collector health |

## Testing

`zpool-textfile.sh` accepts a `ZPOOL_STATUS_FIXTURE` env var so it can be
tested without a real pool. `fixtures/zpool-status.txt` is a captured
`zpool list` + `zpool status` sample covering a healthy pool (finished
scrub, no errors) and a degraded pool (scrub in progress, a faulted device
with read/write/checksum errors).

`promtool` is not installed locally — it's only available via the
`prom/prometheus` Docker image — so run the test on a host with Docker:

```bash
bash test_prom_output.sh
```

This runs the collector against the fixture, writes a temporary `.prom`
file, and validates it with `docker run --rm -i --entrypoint promtool
prom/prometheus:v2.53.0 check metrics`.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus` template
variable at your Prometheus datasource. `Instance` and `Pool` variables
scope every panel. Panels: pool health/capacity table, capacity-used trend,
unhealthy-pool count, scrub/resilver-running count, oldest-scrub age,
collector freshness, per-vdev device states, and per-vdev error counts.
