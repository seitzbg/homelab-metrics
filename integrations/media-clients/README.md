# media-clients (bundled: esanchezm/prometheus-qbittorrent-exporter + onedr0p/exportarr)

This stack runs two independent third-party exporters as two services in
one `compose.yaml`:

- [**prometheus-qbittorrent-exporter**](https://github.com/esanchezm/prometheus-qbittorrent-exporter)
  (`esanchezm`) — polls the qBittorrent WebUI API and re-exposes server
  state/transfer counters and a per-status/per-category torrent count as
  `qbittorrent_*` Prometheus metrics.
- [**exportarr**](https://github.com/onedr0p/exportarr) (`onedr0p`), run
  in its `sabnzbd` mode — polls the SABnzbd WebUI API and re-exposes
  queue/server/disk statistics as `sabnzbd_*` Prometheus metrics.
  Exportarr is an AIO exporter for several *arr-ecosystem apps (Sonarr,
  Radarr, Lidarr, Prowlarr, Readarr, Bazarr, SABnzbd); which app it
  exports is selected by a CLI subcommand, not an env var — that's why
  `compose.yaml` sets `command: ["sabnzbd"]` on the `sabnzbd-exporter`
  service rather than an env var toggle.

Both containers' `/metrics` output is entirely defined by their upstream
project, not by this repo — see the exporters' own source/README (linked
above) for the authoritative metric list. See `dashboard.json` for the
subset this bundle's dashboard charts.

## Prerequisites

- A running qBittorrent instance with the WebUI enabled (Tools > Options
  > Web UI), reachable from wherever this container runs.
- A running SABnzbd instance with its API key available (Config >
  General > "API Key" in the SABnzbd WebUI), reachable from wherever
  this container runs.

## Quickstart

```bash
cp .env.example .env
# edit .env: set QBITTORRENT_HOST/_PORT/_USER/_PASS and URL/API_KEY — see
# the comments in .env.example (the two exporters' vars share one file
# but don't collide). QBITTORRENT_PORT and PORT are both effectively
# required — see the Configuration tables below for why.
docker compose up -d
curl localhost:8000/metrics   # qbittorrent-exporter
curl localhost:8081/metrics   # sabnzbd-exporter (exportarr sabnzbd)
```

Neither exporter needs its target actually **reachable** at startup —
only its required variables need to be **set**. `qbittorrent-exporter`
exits(1) if `QBITTORRENT_HOST`/`QBITTORRENT_PORT` are unset (see the
`.env.example` comment), but a wrong-but-set host/port still starts
cleanly. `exportarr` similarly requires `URL` and either `API_KEY` or
`API_KEY_FILE` to be *set* to start at all (`Config.Validate()` fails
otherwise). Confirmed against both pinned images with a deliberately
unreachable target: neither container crash-loops (unlike, e.g., this
repo's `kea-dhcp` bundle) — but the two exporters degrade differently
once scraped. `qbittorrent-exporter`'s `/metrics` still returns `200`,
with `qbittorrent_up 0` and every other series present at `0`.
`exportarr`'s `/metrics` returns `500` with a self-describing Prometheus
error series (`sabnzbd_collector_error{target="..."}`) instead of the
`sabnzbd_*` metrics — this is expected exportarr behavior on a scrape
against an unreachable target, not a broken image; point `URL` at a live
SABnzbd instance to get real metrics back.

## Configuration (environment)

### qbittorrent-exporter

| Variable | Default | Description |
|---|---|---|
| `QBITTORRENT_HOST` | *(required)* | qBittorrent WebUI hostname (bare host — the exporter joins host+port itself) |
| `QBITTORRENT_PORT` | *(required — no default)* | WebUI port. Confirmed against the pinned image: unset means `sys.exit(1)` ("No port specified"), a crash-restart loop under `restart: unless-stopped`, not a clean start |
| `QBITTORRENT_USER` / `QBITTORRENT_PASS` | *(empty)* | WebUI credentials (or use `QBITTORRENT_API_KEY` instead, qBittorrent 5.2+) |
| `QBITTORRENT_API_KEY` | *(none)* | Bearer-token auth, alternative to user/pass |
| `QBITTORRENT_SSL` | `false` | Set `true` if the WebUI is behind HTTPS |
| `EXPORTER_ADDRESS` / `EXPORTER_PORT` | `0.0.0.0` / `8000` | address/port the exporter's own HTTP server binds to |
| `METRICS_PREFIX` | `qbittorrent` | prefix on every emitted metric name — `dashboard.json` assumes the default |

Full option list: [upstream README](https://github.com/esanchezm/prometheus-qbittorrent-exporter#readme).

### sabnzbd-exporter (`exportarr sabnzbd`)

These are exportarr's own variable names, generic across every app it
supports — they only affect the `sabnzbd-exporter` service here.

| Variable | Default | Description |
|---|---|---|
| `URL` | *(required)* | full base URL to the SABnzbd WebUI/API |
| `API_KEY` | *(required, or `API_KEY_FILE`)* | SABnzbd API key |
| `API_KEY_FILE` | *(none)* | path (in-container) to a file holding the key; overrides `API_KEY` |
| `PORT` | *(required — image default is `9707`, not `8081`)* | port exportarr's own HTTP server binds to. The `exportarr` image's Dockerfile hardcodes `ENV PORT=9707` (a generic default shared by every *arr app it can export — instances must each pick a unique port when run side by side); confirmed against the pinned v2.3.0 image's startup log. Set explicitly to `8081` to match this compose's `ports:` mapping and the `sabnzbd` job's scrape target |
| `INTERFACE` | `0.0.0.0` | interface exportarr's own HTTP server binds to |
| `DISABLE_SSL_VERIFY` | `false` | skip TLS cert verification against `URL` |
| `LOG_LEVEL` / `LOG_FORMAT` | `info` / `console` | logging verbosity/format |

Full option list, and the other apps exportarr can export (each needs its
own container instance + `command:`): [upstream README](https://github.com/onedr0p/exportarr#readme).

## Exposed metrics

Every metric below is the upstream exporter's own; this bundle changes
nothing about what's collected. `dashboard.json` uses a subset of each.

**qbittorrent_\* highlights** (all carry a `server` label = the
`host:port` target polled): `up`, `dht_nodes`, `total_peer_connections`,
`dl_info_data_total` / `up_info_data_total` (counters, rate() in the
dashboard), `alltime_dl_total` / `alltime_ul_total`, `torrents_count`
(labeled `status`, `category`).

**sabnzbd_\* highlights**: `status`, `speed_bps`, `queue_length`,
`queue_warnings`, `remaining_bytes`, `total_bytes`, `time_estimate_seconds`,
`downloaded_bytes`, `article_cache_articles` / `article_cache_bytes`,
`disk_total_bytes` / `disk_used_bytes` (labeled `folder`),
`server_downloaded_bytes` (labeled `server` = Usenet provider hostname).

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus`
template variable at a Prometheus datasource scraping both exporters
(reference job names `qbittorrent` and `sabnzbd`). Two rows: qBittorrent
(status, transfer rate, torrents by state/category) and SABnzbd (status,
queue, disk space, per-server download totals). No host/instance
filtering variables — every panel is a plain metric query, so it
populates the same regardless of how many targets your Prometheus
scrapes under these two job names.
