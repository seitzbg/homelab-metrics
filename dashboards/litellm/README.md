# litellm

Grafana dashboard for the [LiteLLM](https://github.com/BerriAI/litellm) proxy's
built-in Prometheus metrics (`litellm_*`): request rate/latency, tokens and
spend by model and key, deployment health, and per-model routing.

## Enable metrics

LiteLLM only exposes `/metrics` when the Prometheus callback is on. In
`config.yaml`:

```yaml
litellm_settings:
  callbacks: ["prometheus"]
```

(You can also trim the metric/label set there — see LiteLLM's Prometheus
docs — to keep cardinality down.)

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml). Two gotchas baked
into the snippet:

- **Auth:** `/metrics/` runs the proxy's key auth, so the scrape sends a
  bearer token. The master key always works.
- **Trailing slash:** scrape `/metrics/`, not `/metrics` (the bare path
  307-redirects, which Prometheus won't follow).

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable. A `$model` variable
(`label_values(litellm_proxy_total_requests_metric_total, requested_model)`)
scopes the per-model panels.
