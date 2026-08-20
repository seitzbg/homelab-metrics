# observability-stack

Grafana dashboard that monitors the monitoring stack itself — **Prometheus,
Loki, and Grafana** — from their own `/metrics`: ingestion/query rates, TSDB
and Loki on-disk size, series/stream counts, and the host filesystem the
stack lives on.

## Enable metrics

All three expose `/metrics` natively; Grafana's is on by default
(`[metrics] enabled = true` in `grafana.ini` if it was turned off). Nothing
else to install.

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml). **Keep the job names**
`prometheus` / `loki` / `grafana` — the dashboard filters on them.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable, then pick your stack's host in the `$instance`
variable (used by the host-filesystem panels).

> Two "Loki store on-disk (du)" panels read a custom `loki_disk_usage_bytes`
> series — a directory-size metric shipped via a node_exporter textfile
> collector. That's an optional extra; those panels stay empty unless you
> publish that series yourself. Every other panel uses the stack's native
> metrics.
