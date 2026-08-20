# traefik

Grafana dashboard for [Traefik](https://traefik.io/)'s built-in Prometheus
metrics (`traefik_*`): request rate/latency by service and entrypoint, HTTP
status classes, open connections, TLS cert expiry, and config reloads.

## Enable metrics

Traefik does not expose metrics unless you turn them on in its **static**
configuration and give them a dedicated entrypoint:

```yaml
# traefik static config (flags shown; the file/env equivalents also work)
--metrics.prometheus=true
--entryPoints.metrics.address=:8082
--metrics.prometheus.entryPoint=metrics
```

## Scrape

Point Prometheus at that entrypoint — see [`prometheus-scrape.yml`](./prometheus-scrape.yml)
(replace `TARGET_HOST` with the Traefik host/container). The metrics
entrypoint is unauthenticated, so keep it off any public interface.

## Import

Import `dashboard.json` into Grafana and select your Prometheus datasource
for the **Prometheus** variable. Panels filter by an `$instance` variable
(`label_values(traefik_config_last_reload_success, instance)`).
