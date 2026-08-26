# Tier 3 — parameterized dashboards

Dashboards for producers that **already expose their own metrics** — a
built-in `/metrics` endpoint, a self-metrics port, or an agent you run on the
target host. There's no exporter to ship here; each folder holds:

- `dashboard.json` — the Grafana dashboard, with its datasource templated to a
  `${DS_PROMETHEUS}` (or `${DS_LOKI}`) variable and internal identifiers
  genericized.
- `prometheus-scrape.yml` — a ready-to-paste `scrape_configs:` job (except
  `loki-logs`, which reads a Loki datasource and has no scrape).
- `README.md` — how to turn metrics on for that producer.

## Dashboard → producer

| Dashboard | Producer | Endpoint / port |
|---|---|---|
| [`traefik`](./traefik) | Traefik built-in metrics | metrics entrypoint `:8082` |
| [`garage`](./garage) | Garage admin API | `:3903/metrics` |
| [`gitlab`](./gitlab) | GitLab Omnibus bundled exporters | 8 ports on the GitLab host |
| [`litellm`](./litellm) | LiteLLM built-in exporter | `:4000/metrics/` |
| [`uptime-kuma`](./uptime-kuma) | Uptime Kuma built-in metrics | `:3001/metrics` |
| [`dnsdist-powerdns`](./dnsdist-powerdns) | dnsdist + PowerDNS webservers | `:8083` / `:8081` |
| [`multiscrobbler`](./multiscrobbler) | multi-scrobbler built-in metrics | `:9078/api/metrics` |
| [`observability-stack`](./observability-stack) | Prometheus / Loki / Grafana self-metrics | `:9090` / `:3100` / `:3000` |
| [`loki-logs`](./loki-logs) | Loki datasource (logs, not a scrape) | — |
| [`windows`](./windows) | windows_exporter (on host) | `:9182` |
| [`opnsense`](./opnsense) | node_exporter + telegraf (on firewall) | `:9100` / `:9273` |
| [`time-ntp`](./time-ntp) | chrony_exporter + node_timex | `:9123` |
| [`airgradient`](./airgradient) | AirGradient ONE built-in metrics | `:80/metrics` |

## Using a scrape snippet

Each `prometheus-scrape.yml` is a self-contained `scrape_configs:` block.
Merge its jobs into your Prometheus config's `scrape_configs:` and replace
`TARGET_HOST` with your host (keep any job names the folder's README flags —
several dashboards filter panels by `job`). Reload Prometheus.

## Importing a dashboard

Import `dashboard.json` in Grafana (**Dashboards → New → Import**). On import
Grafana prompts for the dashboard's datasource variable — pick your
Prometheus datasource for **Prometheus** (or your Loki datasource for
**Loki** on `loki-logs`). Where a dashboard has an `$instance` / `$job` /
`$host` variable, select your target after import.
