# opnsense

Grafana dashboard for [OPNsense](https://opnsense.org/) firewalls, combining
two on-box agents: node_exporter (host CPU/mem/disk/interfaces) and Telegraf's
pf input (packet-filter states, per-interface traffic, gateway health).

## Enable metrics

Install both plugins from **System → Firmware → Plugins**, then enable each
under **Services**:

- **os-node_exporter** → listens on `:9100`.
- **os-telegraf** → enable the *Prometheus* output (and the *pf* input);
  listens on `:9273`.

Add a firewall rule allowing your Prometheus host to reach those ports.

> On FreeBSD-based OPNsense, Telegraf's `pf` input must **run as root** or its
> pf counters come back empty — check the plugin's "Run as Root" option if
> packet-filter panels are blank.

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml) — one target per
firewall on each port. **Keep the job names** `opnsense-node` /
`opnsense-telegraf`; the dashboard filters on them.

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable.
