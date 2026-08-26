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
give each monitor a stable IP or DNS name your Prometheus can reach.

> **Note — the exact toggle varies by model/firmware.** The endpoint and metric
> names here were verified against the AirGradient ONE (I-9PSL) on firmware
> 3.7.0. On other models or firmware the menu label and path may differ, so
> confirm against [AirGradient's official
> documentation](https://www.airgradient.com/documentation/) (see its
> integration guides) and treat the `curl` below as the source of truth for
> what your device emits.

Confirm it's live:

```sh
curl http://<device-ip>/metrics | grep airgradient_
```

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml) — one target per monitor.

> **Note — the `location` label comes from your scrape config, not the device.**
> The monitors don't emit a `location`; you attach it per target in the scrape
> job (`labels: {location: bedroom}`). Every panel legends by `{{location}}`, so
> if you omit it — or reuse the same value — all devices collapse into one
> series and you can't tell them apart. Give each target a distinct `location`.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable.
