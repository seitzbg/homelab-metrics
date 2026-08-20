# windows

Grafana dashboard for Windows hosts monitored with
[windows_exporter](https://github.com/prometheus-community/windows_exporter):
CPU, memory, disk and network, service state, and OS info (`windows_*`).

## Enable metrics

Install **windows_exporter** on each Windows host (MSI or `winget`); it runs
as a service and serves `/metrics` on `:9182`. Make sure the host firewall
allows your Prometheus to reach that port.

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml) — one target per
Windows machine. The job name is free-form; the dashboard picks hosts through
its `$job` / `$hostname` / `$instance` variables.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable.
