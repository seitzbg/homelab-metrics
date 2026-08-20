# time-ntp

Grafana dashboard for clock discipline and NTP health. Two layers:

- **Fleet clock discipline** — from node_exporter's `node_timex_*` collector
  (offset, frequency, sync status) on every host you already scrape.
- **NTP server detail** — from
  [chrony_exporter](https://github.com/SuperQ/chrony_exporter) on your chrony
  servers: stratum, root dispersion, per-source reachability and offsets, and
  `serverstats` request load.

## Enable metrics

- The fleet panels need **node_exporter** with its default `timex` collector
  (enabled by default) on your hosts — nothing extra to add.
- The server panels need **chrony_exporter** running beside `chronyd`; it
  serves `/metrics` on `:9123`.

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml) for the chrony_exporter
job (one target per NTP server). node_exporter is assumed to be scraped
already by your existing setup.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable. `$instance` (`label_values(chrony_up, instance)`)
selects an NTP server.
