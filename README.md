# homelab-metrics

A curated collection of Prometheus exporters, Grafana dashboards, and
docker-compose stacks for self-hosted homelab monitoring.

## Tiers

Integrations in this repo are organized into three tiers:

- **Tier 1 — Bespoke exporters**: small, purpose-built exporters for niche
  hardware or services that have no existing Prometheus integration.
- **Tier 2 — Bundled third-party exporter stacks**: docker-compose stacks
  pairing a popular self-hosted application with a community-maintained
  exporter for it.
- **Tier 3 — Parameterized dashboards**: dashboards and scrape-config
  snippets for applications that already expose their own `/metrics`
  endpoint natively.

(Full per-integration documentation and an index of dashboards is added as
integrations land.)
