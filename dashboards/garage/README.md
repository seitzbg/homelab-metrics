# garage

Grafana dashboard for [Garage](https://garagehq.deuxfleurs.fr/)'s built-in
Prometheus metrics (`garage_*`, `cluster_*`): cluster/layout health, S3 &
API request rates and latency, block and object storage, and the resync
queue.

## Enable metrics

Garage serves metrics from its **admin API**. Enable it in `garage.toml`:

```toml
[admin]
api_bind_addr = "[::]:3903"
metrics_token = "a-long-random-token"   # optional but recommended
```

With `metrics_token` set, `/metrics` requires `Authorization: Bearer <token>`;
omit it and the endpoint is open (guard it with your network/ACL instead).

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml). For a cluster, list
every node as a target so per-node panels populate; each node reports its
own `instance` label.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable.
