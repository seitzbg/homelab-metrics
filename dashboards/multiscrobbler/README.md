# multiscrobbler

Grafana dashboard for [multi-scrobbler](https://github.com/FoxxMD/multi-scrobbler)'s
built-in Prometheus metrics: tracks played/scrobbled and deduped, per-source
and per-client activity, and scrobble errors.

## Enable metrics

multi-scrobbler serves Prometheus metrics at `/api/metrics` on its web port
(`9078`) with no extra configuration.

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml). **Keep the job name
`multi-scrobbler`** — the dashboard filters on it.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable.
