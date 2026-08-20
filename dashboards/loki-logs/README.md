# loki-logs

Grafana dashboard for browsing logs in [Loki](https://grafana.com/oss/loki/):
log volume over time and a live, filterable log stream.

Unlike the other dashboards here, this one has **no Prometheus scrape** — it
reads from a **Loki datasource**. Its variable is `${DS_LOKI}` (a `loki`-type
datasource picker), not `${DS_PROMETHEUS}`.

## Prerequisites

- A Loki instance receiving your logs (via Promtail, Grafana Alloy, Fluent
  Bit, Docker driver, …).
- A **Loki datasource** configured in Grafana.

## Label scheme

The stream selectors use two labels — `group` and `host` — surfaced as the
`$group` and `$host` dashboard variables
(`label_values(group)` / `label_values({group=~"$group"}, host)`) plus a
`$search` free-text filter. If your log streams use different label keys,
either add `group`/`host` labels in your log shipper, or edit the two
variables and the panel queries to match your own labels.

## Import

Import `dashboard.json` and select your Loki datasource for the **Loki**
variable.
