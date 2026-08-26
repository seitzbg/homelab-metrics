# airgradient

Grafana dashboard for [AirGradient](https://www.airgradient.com/) indoor
air-quality monitors (developed against the **AirGradient ONE**, model I-9PSL,
firmware 3.7.0). Current-reading stat tiles plus 24 h trends for CO₂,
PM1/2.5/10, temperature, humidity, TVOC & NOx index, and WiFi signal
(`airgradient_*`). Every panel legends by the `location` label, so multiple
devices stack on the same graph.

## Enable metrics

AirGradient monitors expose a **local Prometheus endpoint** at
`http://<device-ip>/metrics` on port **80** — no exporter needed. Turn on the
local server / "Open Metrics" option in the device configuration (config
portal, or the AirGradient app/dashboard that pushes config to the device), and
give each monitor a stable IP or DNS name your Prometheus can reach. See
AirGradient's local-server API documentation for the exact toggle on your
firmware.

Confirm it's live:

```sh
curl http://<device-ip>/metrics | grep airgradient_
```

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml) — one target per monitor.
Add a `location` label to each (`bedroom`, `office`, …); the dashboard tells
devices apart by that label, not by `instance`.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable.
