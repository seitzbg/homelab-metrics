# uptime-kuma

Grafana dashboard for [Uptime Kuma](https://github.com/louislam/uptime-kuma)'s
built-in Prometheus metrics (`monitor_status`, `monitor_response_time`, cert
expiry): up/down and response time per monitor, and TLS certificate lifetime.

## Enable metrics

Kuma exposes `/metrics` out of the box on its web port. It's protected by an
API key — create one under **Settings → API Keys**.

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml). The API key goes in
the Basic Auth **password** field (Kuma ignores the username). If Kuma runs
behind a TLS reverse proxy, set `scheme: https` and target the proxy host
instead of `:3001`.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable. Panels label series by Kuma's own `monitor_name` /
`monitor_type` and exclude group monitors (`monitor_type!="group"`).
