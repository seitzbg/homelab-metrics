# pihole (bundled: eko/pihole-exporter)

Pi-hole v6 dropped its old PHP `admin/api.php` stats endpoint (and the
Prometheus-friendly plaintext it used to return); the modern FTL REST API
(`/api/...`) has no native `/metrics` output. This stack pairs Pi-hole with
[eko/pihole-exporter](https://github.com/eko/pihole-exporter), a
community-maintained Prometheus exporter that authenticates to the Pi-hole
v6 API, polls its stats endpoints, and re-exposes them as `pihole_*`
Prometheus metrics on its own `/metrics` endpoint.

The pinned tag, `v1.2.0`, targets the Pi-hole **v6** REST API exclusively
(session-based auth via `POST /api/auth`, not the v5 `?auth=<token>`
scheme) — it will not work against a Pi-hole v5 instance.

## Prerequisites

- A running Pi-hole v6 instance, reachable over HTTP(S) from wherever this
  container runs.
- A Pi-hole v6 **app password**.

### Creating a Pi-hole v6 app password

1. Log in to the Pi-hole admin web interface.
2. Go to **Settings → Web Interface/API**.
3. Switch the settings view from **Basic** to **Expert** mode.
4. Select **Configure app password** and generate one.

An app password lets the exporter authenticate without using your admin
login password (useful if you don't want that password sitting in a
`.env` file, or if you have 2FA enabled on your login).

## Quickstart

```bash
cp .env.example .env
# edit .env: set PIHOLE_HOSTNAME (and PIHOLE_PORT/PIHOLE_PROTOCOL if not
# plain HTTP on port 80) and PIHOLE_PASSWORD to the app password above
docker compose up -d
curl localhost:9617/metrics
```

## Configuration (environment)

Variable names and defaults are the exporter's own (`ekofr/pihole-exporter`
image, confita `env`+`flags` config loader) — see
[upstream README](https://github.com/eko/pihole-exporter#readme) for the
full list.

| Variable | Default | Description |
|---|---|---|
| `PIHOLE_HOSTNAME` | `127.0.0.1` | Pi-hole hostname or IP to poll |
| `PIHOLE_PORT` | `80` | Pi-hole web interface port |
| `PIHOLE_PROTOCOL` | `http` | `http` or `https` |
| `PIHOLE_PASSWORD` | *(none)* | Pi-hole v6 app password (or your admin password, not recommended) |
| `PORT` | `9617` | port the exporter itself listens on |
| `BIND_ADDR` | `0.0.0.0` | address the exporter binds to |
| `TIMEOUT` | `5s` | HTTP timeout when calling the Pi-hole API |
| `SKIP_TLS_VERIFICATION` | `false` | skip TLS cert verification (only if `PIHOLE_PROTOCOL=https` against a self-signed cert; do not use over an untrusted network) |
| `DEBUG` | `false` | verbose logging |

The exporter can also poll multiple Pi-holes from one instance by passing
comma-separated lists for `PIHOLE_HOSTNAME`/`PIHOLE_PORT`/`PIHOLE_PROTOCOL`/
`PIHOLE_PASSWORD` (see upstream README) — out of scope for this single-target
`.env.example`.

## Exposed metrics

All metrics carry a `hostname` label (the `PIHOLE_HOSTNAME` value of the
polled Pi-hole). Metric names below are `pihole_`-prefixed
(`pihole_status`, `pihole_dns_queries_today`, etc.) — this table omits the
prefix.

| Metric | Extra labels | Description |
|---|---|---|
| `status` | — | 1 if Pi-hole blocking is enabled, 0 otherwise |
| `dns_queries_today` | — | DNS queries made today |
| `ads_blocked_today` | — | Queries blocked today |
| `ads_percentage_today` | — | Percentage of today's queries blocked |
| `domains_being_blocked` | — | Domains on the blocklist |
| `unique_domains` | — | Unique domains seen |
| `unique_clients` | — | Unique clients seen |
| `clients_ever_seen` | — | Clients ever seen |
| `queries_forwarded` | — | Queries forwarded upstream |
| `queries_cached` | — | Queries answered from cache |
| `request_rate` | — | Requests/second |
| `dns_queries_all_types` | — | DNS queries by all types |
| `querytypes` | `type` | Queries by DNS record type |
| `reply` | `type` | Replies by type |
| `top_queries` | `domain` | Top permitted domains |
| `top_ads` | `domain` | Top blocked domains |
| `top_sources` | `source`, `source_name` | Top requesting clients |
| `forward_destinations` | `destination`, `destination_name` | Query share per upstream/cache/blocklist |
| `forward_destinations_responsetime` | `destination`, `destination_name` | Upstream response time |
| `forward_destinations_responsevariance` | `destination`, `destination_name` | Variance in upstream response time |

`/metrics` is served (with an HTTP 200) whether or not the Pi-hole target
is reachable — an unreachable target just leaves the per-poll metric
families empty and logs a warning; it does not crash or exit the exporter.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus` template
variable at a Prometheus datasource scraping this exporter's `/metrics`
(job name `pihole` in the reference scrape config). Panels: status/queries-
today/blocked-today/block-%/blocklist-size/unique-clients overview, query
rate and cumulative queries-today trends, block-% trend, query-type/reply-
type/upstream-destination breakdowns, and top permitted domains/blocked
domains/clients.
