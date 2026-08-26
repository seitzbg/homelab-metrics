# homelab-metrics

A curated collection of Prometheus exporters, Grafana dashboards, and
`docker compose` stacks for self-hosted homelab monitoring — the bespoke
exporters and dashboards from my own setup, sanitized and parameterized so
anyone can drop them in.

Every dashboard's datasource is a template variable (`${DS_PROMETHEUS}` /
`${DS_LOKI}`), so you pick your own datasource on import; no hardcoded hosts,
IPs, or internal names.

## Tiers

Each integration lands in one of three tiers, by how much it ships:

- **Tier 1 — [`exporters/`](./exporters)** — bespoke, purpose-built exporters
  for a service or device with no existing Prometheus integration. Turnkey:
  the exporter code, a `compose.yaml`, a `.env.example`, and its dashboard.
- **Tier 2 — [`integrations/`](./integrations)** — a popular self-hosted app
  paired with a community-maintained exporter, bundled as a `compose.yaml` +
  `.env.example` + dashboard.
- **Tier 3 — [`dashboards/`](./dashboards)** — apps that already expose their
  own `/metrics` (or an on-host agent). Just the dashboard plus a ready-to-
  paste scrape snippet — nothing to run here. See the
  [Tier-3 index](./dashboards/README.md).

## Dashboards

| Dashboard | Tier | Backing exporter / producer |
|---|---|---|
| [sensorpush](./exporters/sensorpush) | 1 | bespoke SensorPush Cloud API exporter |
| [sleephq](./exporters/sleephq) | 1 | bespoke SleepHQ (CPAP) API exporter |
| [monifactory-rcon](./exporters/monifactory-rcon) | 1 | bespoke Minecraft/Forge RCON exporter |
| [zfs-zpool](./exporters/zfs-zpool) | 1 | bespoke `zpool` node_exporter textfile collector |
| [tempest](./exporters/tempest) | 1 | bespoke WeatherFlow Tempest cloud API exporter |
| [pihole](./integrations/pihole) | 2 | [ekofr/pihole-exporter](https://github.com/eko/pihole-exporter) |
| [proxmox](./integrations/proxmox) | 2 | [prometheus-pve-exporter](https://github.com/prometheus-pve/prometheus-pve-exporter) |
| [unifi](./integrations/unifi) (×3: PDU power, switches, WiFi) | 2 | [unpoller](https://github.com/unpoller/unpoller) |
| [mariadb](./integrations/mariadb) | 2 | [mysqld_exporter](https://github.com/prometheus/mysqld_exporter) |
| [kea-dhcp](./integrations/kea-dhcp) | 2 | [kea-exporter](https://github.com/mweinelt/kea-exporter) |
| [media-clients](./integrations/media-clients) | 2 | qbittorrent-exporter + sabnzbd (exportarr) |
| [traefik](./dashboards/traefik) | 3 | Traefik built-in metrics |
| [garage](./dashboards/garage) | 3 | Garage admin API metrics |
| [gitlab](./dashboards/gitlab) | 3 | GitLab Omnibus bundled exporters |
| [litellm](./dashboards/litellm) | 3 | LiteLLM built-in exporter |
| [uptime-kuma](./dashboards/uptime-kuma) | 3 | Uptime Kuma built-in metrics |
| [dnsdist-powerdns](./dashboards/dnsdist-powerdns) | 3 | dnsdist + PowerDNS webservers |
| [multiscrobbler](./dashboards/multiscrobbler) | 3 | multi-scrobbler built-in metrics |
| [observability-stack](./dashboards/observability-stack) | 3 | Prometheus / Loki / Grafana self-metrics |
| [loki-logs](./dashboards/loki-logs) | 3 | Loki datasource (logs) |
| [windows](./dashboards/windows) | 3 | windows_exporter |
| [opnsense](./dashboards/opnsense) | 3 | node_exporter + telegraf (on-firewall) |
| [time-ntp](./dashboards/time-ntp) | 3 | chrony_exporter + node_timex |
| [airgradient](./dashboards/airgradient) | 3 | AirGradient ONE built-in Prometheus (`/metrics`) |

26 dashboards across the three tiers.

## Quickstart

1. Pick a folder for the thing you want to monitor.
2. **Tier 1 / 2:** `cp .env.example .env`, edit it, then `docker compose up -d`.
   **Tier 3:** merge that folder's `prometheus-scrape.yml` into your Prometheus
   config (replace `TARGET_HOST`) and reload.
3. Import the folder's `dashboard.json` into Grafana and select your
   Prometheus (or Loki) datasource for the datasource variable.
4. Read the folder's `README.md` for prerequisites and any per-service gotchas.

## Contributing / verifying

`make verify` runs the full gate used to build this repo:

- `scripts/scrub-check.sh` — a denylist for internal hostnames/IPs/secrets, so
  nothing private slips into a dashboard or config.
- JSON validity, `docker compose config`, and `promtool check` on every
  compose file and scrape snippet.
- A real Grafana round-trip import of every `dashboard.json`, asserting each
  carries only template-variable datasources (no host-specific UIDs).

## License

[MIT](./LICENSE) © 2026 Bryan Seitz.
