# unifi (bundled: unpoller)

[unpoller](https://github.com/unpoller/unpoller) polls a UniFi Network
controller's API and re-exposes per-device metrics — switch port
throughput/errors/PoE, AP/radio/client WiFi health, and USP-PDU-Pro
per-outlet power — that the controller's own UI doesn't expose over
Prometheus. One exporter instance, one `/metrics` endpoint (`:9130`),
scraped under a single `unpoller` job — but it feeds three separate
dashboards in this bundle, one per device family.

## Prerequisites

- A running UniFi Network controller (UniFi OS console or self-hosted
  Network Application), reachable over HTTPS from wherever this container
  runs.
- A dedicated **read-only** UniFi admin account for unpoller to poll with,
  rather than reusing your own login.
- PDU-power metrics (`unpoller_device_outlet_*`) require an actual UniFi
  PDU (USP-PDU-Pro or similar) adopted on the site — without one, the
  `dashboard-pdu-power.json` panels have nothing to show. This is expected;
  the dashboard is still shipped in full.

### Creating a read-only UniFi admin

1. In the UniFi Network application, go to **Admins** (or **Admins &
   Users** on a UniFi OS console) and add a new admin.
2. Restrict it to local access only (uncheck/disable remote/cloud access)
   so it isn't tied to a Ubiquiti cloud account.
3. Assign the **View Only** (read-only) role, with access to whichever
   sites you want polled.
4. Set a username and password. unpoller authenticates with these via
   `UP_UNIFI_DEFAULT_USER`/`UP_UNIFI_DEFAULT_PASS` below.

Newer UniFi Network releases also support per-admin **API keys** as an
alternative to a password — unpoller's own example config notes this is
"exclusive of user/pass auth" (set one or the other, not both). If you'd
rather use one, generate it from that admin account's own settings and set
`UP_UNIFI_DEFAULT_API_KEY` instead (see `.env.example`).

## Quickstart

```bash
cp .env.example .env
# edit .env: set UP_UNIFI_DEFAULT_URL and the read-only admin's
# UP_UNIFI_DEFAULT_USER/UP_UNIFI_DEFAULT_PASS (or UP_UNIFI_DEFAULT_API_KEY)
docker compose up -d
curl localhost:9130/metrics
```

## Configuration (environment)

Variable names carry unpoller's own `UP_` prefix (its env-var config
scheme, `golift.io/cnfg`) — verified against the pinned image tag's
source, not guessed. Full reference: unpoller's
[`examples/up.conf.example`](https://github.com/unpoller/unpoller/blob/main/examples/up.conf.example)
(the TOML equivalent of every `UP_*` var below).

| Variable | Default | Description |
|---|---|---|
| `UP_UNIFI_DEFAULT_URL` | `https://127.0.0.1:8443` | UniFi controller URL (no path after host:port; for a UDM/UDM-Pro don't add `:8443`) |
| `UP_UNIFI_DEFAULT_USER` | *(none)* | Read-only admin username |
| `UP_UNIFI_DEFAULT_PASS` | *(none)* | Read-only admin password |
| `UP_UNIFI_DEFAULT_API_KEY` | *(none)* | Per-admin API key, exclusive of user/pass |
| `UP_UNIFI_DEFAULT_VERIFY_SSL` | `false` | Verify the controller's TLS cert (self-signed by default) |
| `UP_POLLER_DEBUG` | `false` | Verbose per-device debug logging |
| `UP_PROMETHEUS_DISABLE` | `false` | Disable the Prometheus output plugin |
| `UP_PROMETHEUS_NAMESPACE` | `unpoller` | Metric name prefix (`<namespace>_device_*`) |
| `UP_PROMETHEUS_HTTP_LISTEN` | `0.0.0.0:9130` | Address/port `/metrics` is served on |

`/metrics` is served whether or not the UniFi controller is reachable —
an unreachable/misconfigured controller just leaves the `unpoller_*`
metric families empty (and logs an auth/connection error); it doesn't
crash or exit the exporter.

## Exposed metrics

All series carry a device `name` label (plus `site_name`, `source`, and
device-family-specific labels). Metric names below are `unpoller_`-prefixed
(the `UP_PROMETHEUS_NAMESPACE` default) — this table omits the prefix.

**Switches** (`dashboard-switches.json`, job `unpoller`):

| Metric | Extra labels | Description |
|---|---|---|
| `device_cpu_utilization_ratio` | — | Switch CPU utilization (0–1) |
| `device_memory_utilization_ratio` | — | Switch memory utilization (0–1) |
| `device_uptime_seconds` | — | Switch uptime |
| `device_port_receive_rate_bytes` / `device_port_transmit_rate_bytes` | `port_name` | Per-port throughput |
| `device_port_receive_errors_total` / `device_port_transmit_errors_total` | `port_name` | Per-port error counters |
| `device_port_sfp_rx_power` / `device_port_sfp_tx_power` | `port_name` | SFP optical power (dBm) |

**Access points / WiFi** (`dashboard-wifi.json`, job `unpoller`):

| Metric | Extra labels | Description |
|---|---|---|
| `device_radio_stations` | `band`, `station_type` | Connected client count per radio |
| `device_vap_average_client_signal` | `essid`, `band` | Average client signal (dBm) per SSID |
| `device_vap_receive_bytes_total` / `device_vap_transmit_bytes_total` | `essid` | Per-SSID traffic counters |
| `device_radio_channel` / `device_radio_ht` | `band` | Current channel / channel width |
| `device_radio_channel_utilization_total_ratio` / `_receive_ratio` / `_transmit_ratio` | `band` | Channel airtime utilization (total / ours received / ours transmitted) |
| `device_radio_transmit_power` | `band` | Radio Tx power (dBm) |
| `device_radio_transmit_retries` | `band` | Per-poll Tx retry count |

**PDU power** (`dashboard-pdu-power.json`, job `unpoller`, USP-PDU-Pro only):

| Metric | Extra labels | Description |
|---|---|---|
| `device_outlet_ac_power_consumption` | — | Total PDU power draw (W) |
| `device_outlet_outlet_power` | `outlet_name`, `outlet_index` | Per-outlet power (W) |
| `device_outlet_outlet_current` | `outlet_name`, `outlet_index` | Per-outlet current (A) |
| `device_outlet_outlet_voltage` | `outlet_name`, `outlet_index` | Per-outlet voltage (V) |

## Dashboards

Three dashboards, one exporter, one scrape job (`unpoller`). Each has its
own device-selector template variable (`$sw`/`$ap`/`$name`), populated via
`label_values()` against the family's own metrics — so it fills in with
whatever your controller actually has adopted.

- **`dashboard-switches.json`** — "UniFi — Switches". CPU/memory/uptime,
  active-port count, per-port RX/TX throughput and error rate, SFP optical
  power. Variable: `$sw` (switch name).
- **`dashboard-wifi.json`** — "UniFi — Access Points / WiFi". Client
  counts and signal, per-SSID throughput, channel assignment/width,
  channel utilization and external-interference estimate, Tx retries, and
  a derived per-radio RF health score/trend. Variable: `$ap` (AP name).
  **The two RF-health panels need `unifi-radio-health.rules.yml` — see
  below.**
- **`dashboard-pdu-power.json`** — "UniFi — USP PDU Pro Power". Total
  draw, average outlet voltage, count of outlets currently drawing power,
  and per-outlet power/current/voltage. Variable: `$name` (PDU name).
  Requires a USP-PDU-Pro (or similar) adopted on the site — see
  Prerequisites.

Import into Grafana and point each dashboard's `Prometheus` template
variable at a Prometheus datasource scraping this exporter's `/metrics`.

### `unifi-radio-health.rules.yml`

Two panels in `dashboard-wifi.json` ("Radio RF health score" and "RF
health trend") query a derived series, `unifi:radio_health:score`, that
is **not** something unpoller exports directly — it's a Prometheus
recording rule computed from several raw `unpoller_device_radio_*`/
`unpoller_device_vap_*` metrics (airtime headroom, channel cleanliness,
client signal, link quality, weighted into a single 0–100 score per
radio). `unifi-radio-health.rules.yml` in this directory is that rule set,
unmodified from the source it was extracted from. Load it into your own
Prometheus (`rule_files:` in `prometheus.yml`, or the equivalent for your
Prometheus Operator/Alloy setup) to populate those two panels; without it
they show "No data" — every other panel in all three dashboards works
from unpoller's raw metrics alone.
