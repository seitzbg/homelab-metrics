# monifactory-rcon-exporter

Prometheus exporter for a Minecraft Forge server's internal performance stats
— TPS, mean tick time (MSPT), and loaded-entity counts — pulled purely over
[RCON](https://wiki.vg/RCON) (the Source RCON protocol). No server-side mod
and no server restart required; only `enable-rcon=true` in `server.properties`.

Pure Python stdlib — the RCON protocol is implemented inline, so the image
has no pip dependencies and no build step. A background thread polls RCON
every `SCRAPE_INTERVAL` seconds and caches the rendered Prometheus text; the
HTTP handler just serves the cache, so Prometheus scrape latency never blocks
on (or drives the cadence of) the RCON connection.

The parser (turning `forge tps` / `forge entity list` text output into
Prometheus metrics) is a pure function, `render_metrics(tps_text, entity_text)`,
separated from the RCON socket I/O — see `test_parse.py` for a unit test
against captured fixtures that runs with no server and no Docker.

## Prerequisites

- A Forge (or NeoForge) Minecraft server with RCON enabled in
  `server.properties`:
  ```properties
  enable-rcon=true
  rcon.port=25575
  rcon.password=changeme
  ```
- Network reachability from wherever this exporter runs to the server's RCON
  port (25575 by default).

## Quickstart

```bash
cp .env.example .env
# edit .env with your server's RCON host/port/password
docker compose up -d
curl localhost:8000/metrics
```

## Configuration (environment)

| Variable | Default | Description |
|---|---|---|
| `RCON_HOST` | *(required)* | Minecraft server host/IP |
| `RCON_PORT` | `25575` | RCON port |
| `RCON_PASSWORD` | *(required)* | RCON password (`rcon.password` in `server.properties`) |
| `BIND_PORT` | `8000` | port the exporter listens on |
| `SCRAPE_INTERVAL` | `30` | seconds between RCON polls (`forge tps` + `forge entity list`) |
| `RCON_TIMEOUT` | `5` | seconds to wait for connect/auth and for a command's first response packet; a slow-but-valid reply within this window is collected rather than dropped |

## Exposed metrics

All gauges. `minecraft_tps` and `minecraft_mspt_milliseconds` carry a
`dimension` label per world plus a synthetic `_overall` series; `minecraft_entities`
carries a `type` label per loaded entity type.

| Metric | Description |
|---|---|
| `minecraft_tps{dimension}` | Mean ticks per second, from `forge tps` (20 is healthy) |
| `minecraft_mspt_milliseconds{dimension}` | Mean tick time in milliseconds, from `forge tps` |
| `minecraft_entities{type}` | Loaded entity count by type, from `forge entity list` |
| `minecraft_entities_total` | Total loaded entities server-wide |
| `minecraft_rcon_up` | 1 if the last RCON scrape succeeded, 0 otherwise (the exporter's own "up" signal) |
| `minecraft_rcon_scrape_duration_seconds` | Time to run the two RCON commands on the last successful scrape |

When a scrape fails (RCON unreachable, bad password, connection reset), only
`minecraft_rcon_up 0` is served — that's the metric to alert on for exporter
health. A response that arrives empty or unparseable (for example, a reply that
never came within `RCON_TIMEOUT`, or came back garbled) is also reported as
`minecraft_rcon_up 0`, not as a successful scrape carrying no TPS/entity data.

## Testing the parser

```bash
cd exporters/monifactory-rcon
python -m pytest test_parse.py -v
```

`test_parse.py` feeds captured `forge tps` / `forge entity list` output
(`fixtures/`) straight into `render_metrics()` and asserts on the resulting
exposition text — no RCON connection or Docker needed.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus` template
variable at your Prometheus datasource; `Instance` and `Container name`
variables scope the panels to one server. The "Server performance — TPS &
tick time (from RCON)" row is fed entirely by this exporter. The remaining
rows (server-list ping/players, game-container CPU/memory, host memory) also
plot metrics from a status-ping exporter (e.g.
[mc-monitor](https://github.com/itzg/mc-monitor)), cAdvisor, and node_exporter
respectively — optional companions, not part of this bundle, that a
Minecraft-server observability setup commonly pairs with RCON stats.
