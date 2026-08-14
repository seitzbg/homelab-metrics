# mariadb (bundled: prometheus/mysqld_exporter)

This stack runs the official
[mysqld_exporter](https://github.com/prometheus/mysqld_exporter): it
connects to a MySQL or MariaDB server, runs `SHOW GLOBAL STATUS` / `SHOW
SLAVE STATUS` (and other introspection queries) on each scrape, and
re-exposes the results as `mysql_*` Prometheus metrics on its own
`/metrics` endpoint.

**A note if you've used mysqld_exporter before:** older tutorials
configure it with a single `DATA_SOURCE_NAME=user:pass@(host:port)/` env
var. That was removed as a **breaking change in v0.15.0** (June 2023) —
the pinned tag here (`v0.20.0`) does not support it. Modern configuration
is either a mounted `my.cnf`-style credentials file (`--config.my-cnf`) or
the flags/env-var combination this bundle uses (see `.env.example`):
`--mysqld.address` / `--mysqld.username` flags (interpolated into
`compose.yaml`'s `command:` from `.env`) plus the password read directly
by the exporter from `MYSQLD_EXPORTER_PASSWORD`.

## Prerequisites

- A running MySQL (>= 5.6) or MariaDB (>= 10.3) server, reachable over TCP
  from wherever this container runs.
- A dedicated least-privilege monitoring user.

### Creating the monitoring user

Run on the database server (adjust the `@'...'` host clause to match how
the exporter container will actually connect — `@'127.0.0.1'` if it's on
the same host and reaching MySQL via the host network, `@'%'` or a
specific subnet if it's crossing a container network or a different host;
avoid `@'%'` in production if you can scope it tighter):

```sql
CREATE USER 'exporter'@'%' IDENTIFIED BY 'CHANGE_ME'
  WITH MAX_USER_CONNECTIONS 3;
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'%';
```

- `PROCESS` — process list metrics (`SHOW PROCESSLIST`).
- `REPLICATION CLIENT` — replica/slave status metrics (`SHOW SLAVE
  STATUS`); only relevant if the target is a replica.
- `SELECT` — `information_schema`/`performance_schema` collectors (table
  sizes, query digests, etc., if enabled).
- `MAX_USER_CONNECTIONS 3` caps how many concurrent connections the
  exporter's connection pool can open per scrape, so monitoring can't
  starve the server under load. Not supported on all MySQL/MariaDB
  versions (e.g. MariaDB 10.1) — drop the clause if `CREATE USER` errors.

## Quickstart

```bash
cp .env.example .env
# edit .env: MYSQLD_ADDRESS (host:port), MYSQLD_USERNAME, MYSQLD_EXPORTER_PASSWORD
docker compose up -d
curl localhost:9104/metrics
```

`/metrics` is served (HTTP 200) even if the exporter can't currently reach
`MYSQLD_ADDRESS` — a failed connection just fails that scrape's collectors
and pushes `mysql_up 0`; it does not crash or exit the container.

## Configuration (environment)

| Variable | Default | Description |
|---|---|---|
| `MYSQLD_ADDRESS` | `127.0.0.1:3306` | `host:port` of the server to monitor — becomes the exporter's `--mysqld.address` flag |
| `MYSQLD_USERNAME` | `exporter` | monitoring user created above — becomes `--mysqld.username` |
| `MYSQLD_EXPORTER_PASSWORD` | *(none)* | password for that user — read directly by the exporter process (its own env var name) |

`MYSQLD_ADDRESS`/`MYSQLD_USERNAME` are `docker compose` variable
substitutions baked into `command:` at compose-parse time (not container
env vars the exporter reads); `MYSQLD_EXPORTER_PASSWORD` is injected into
the container's environment via `env_file` and read by the exporter
itself. Both mechanisms read the same `.env` file.

Full flag/env-var reference (TLS, custom queries, `config.my-cnf`, the
multi-target `/probe` mode, etc.):
[upstream README](https://github.com/prometheus/mysqld_exporter#readme).

## Exposed metrics

Metric names below are `mysql_`-prefixed — this table omits the prefix.
`global_status_*` names come 1:1 from MySQL/MariaDB's own `SHOW GLOBAL
STATUS` variable names (lowercased), so the set varies slightly by server
version/vendor; the ones this dashboard uses are listed here.

| Metric | Extra labels | Description |
|---|---|---|
| `up` | — | 1 if the last scrape of this target succeeded, 0 otherwise |
| `global_status_queries` | — | total queries executed (counter) |
| `global_status_threads_connected` | — | current open connections |
| `global_status_slow_queries` | — | total queries exceeding `long_query_time` (counter) |
| `global_status_bytes_received` / `_bytes_sent` | — | network traffic to/from clients (counter) |
| `global_status_aborted_connects` | — | failed connection attempts (counter) |
| `global_status_uptime` | — | seconds since the server started |
| `slave_status_slave_io_running` / `_slave_sql_running` | — | 1 if that replication thread is running, 0 otherwise (replicas only) |
| `slave_status_seconds_behind_master` | — | replication lag in seconds (replicas only) |

Every metric also carries the standard Prometheus `instance` label from
the scrape target.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus`
template variable at a Prometheus datasource scraping this exporter's
`/metrics` (job name `mysql` in the reference scrape config). Panels:
replica lag trend and a replication-status table (I/O thread, SQL thread,
lag — replica-only, empty on a standalone server with no `slave_status`
rows), plus a server overview (queries/sec, connected threads, slow
queries/sec, network bytes/sec, aborted connects/sec, uptime).

The `Instance` template variable (multi-select, "All" by default) filters
every panel by the Prometheus `instance` label — populated dynamically via
`label_values(mysql_up, instance)`, so it works against any number of
monitored servers without editing the dashboard.
