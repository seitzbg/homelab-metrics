# proxmox (bundled: prometheus-pve/prometheus-pve-exporter)

Proxmox VE has no native `/metrics` endpoint. This stack runs
[prometheus-pve-exporter](https://github.com/prometheus-pve/prometheus-pve-exporter),
the reference Prometheus exporter for Proxmox VE: it authenticates to the
PVE API, polls node/cluster/guest/storage status, and re-exposes it as
`pve_*` Prometheus metrics.

Unlike most exporters here, this one is a **multi-target proxy**, not a
single fixed-target daemon: one running exporter instance can be scraped
once per PVE node (or cluster), passing the node to poll as a `target=`
query parameter. Run one exporter (this compose file) and point Prometheus
at it once per node you want metrics from.

## Prerequisites

- One or more Proxmox VE nodes, reachable over HTTPS from wherever this
  container runs.
- A Proxmox API token for a read-only user.

### Creating a Proxmox API token

On any PVE node (as root, via `pveum` — the PVE user/permission CLI):

```bash
# 1. Create a dedicated user for metrics collection.
pveum user add prometheus@pve --comment "Prometheus PVE exporter"

# 2. Grant it the built-in read-only auditor role at the root path (covers
#    node/cluster/guest/storage status — no write access).
pveum acl modify / --users prometheus@pve --roles PVEAuditor

# 3. Create an API token for that user. --privsep 0 means the token
#    inherits the user's PVEAuditor role directly; the default (--privsep 1)
#    instead requires a *separate* ACL entry for the token itself:
#      pveum acl modify / --tokens 'prometheus@pve!prompve-scraper' --roles PVEAuditor
pveum user token add prometheus@pve prompve-scraper --privsep 0
```

The last command prints a `value` field once — that's the token secret,
used only once. Put it (and the token id `prometheus@pve!prompve-scraper`,
split into user + token name below) into `.env`.

## Quickstart

```bash
cp .env.example .env
# edit .env: set PVE_USER, PVE_TOKEN_NAME, PVE_TOKEN_VALUE (from above)
docker compose up -d
curl localhost:9221/metrics        # exporter's own self-metrics (always up)
curl 'localhost:9221/pve?target=<pve-node-hostname-or-ip>'   # actual PVE metrics
```

## The `target=`-based scrape pattern

The exporter listens on **`:9221`** and exposes two distinct paths:

- `/metrics` — the exporter's *own* process/scrape metrics
  (`pve_collection_duration_seconds`, `pve_request_errors_total`). Served
  unconditionally, with no PVE connectivity required — this is what a
  liveness/wiring check should hit.
- `/pve?target=<node>&module=default&cluster=1&node=1` — the actual PVE
  metrics for `<node>`, fetched live from the PVE API on every scrape. If
  `<node>` is unreachable this returns HTTP 500 (the collection error is
  also counted in `pve_request_errors_total` from `/metrics`).

Add this to Prometheus, matching the dashboard's expected job name (`pve`):

```yaml
scrape_configs:
  - job_name: pve
    static_configs:
      - targets:
          - <pve-node-1-hostname-or-ip>
          - <pve-node-2-hostname-or-ip>
    metrics_path: /pve
    params:
      module: [default]
      cluster: ['1']
      node: ['1']
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: <host-running-this-container>:9221
```

The `relabel_configs` block is what makes each scraped node show up under
its own `instance` label (the actual PVE node) rather than under the
exporter's own address — that's the label the dashboard's `$instance`
variable (`label_values(pve_node_info, instance)`) filters on.

For a cluster, scrape cluster-wide metrics (`cluster=1&node=0`) from just
one node (or round-robin across a few), and scrape node-only metrics
(`cluster=0&node=1`) from every node — see upstream's "Note on scraping
large clusters" for why. This compose file ships a single exporter
instance; a cluster is still one exporter, scraped multiple times (once
per node) via the `targets:` list above.

## Configuration (environment)

Variable names are the exporter's own
(`src/pve_exporter/config.py: config_from_env()`) — see
[upstream README](https://github.com/prometheus-pve/prometheus-pve-exporter#readme)
for the full reference.

| Variable | Description |
|---|---|
| `PVE_USER` | Proxmox user, e.g. `prometheus@pve`. Setting this at all switches the exporter to env-var config mode. |
| `PVE_TOKEN_NAME` | API token id (the part after `!` in `user@realm!tokenid`) — token auth. |
| `PVE_TOKEN_VALUE` | API token secret — token auth. |
| `PVE_PASSWORD` | User's password — password auth, used instead of the two above. |
| `PVE_VERIFY_SSL` | `true`/`false` — verify the PVE node's TLS cert. Proxmox's default cert is self-signed; `false` unless you've replaced it. |
| `PVE_MODULE` | Config "module" name. Defaults to `default`; only relevant if you also keep a multi-module `pve.yml` around. |

The exporter has no fixed single target — `target=` on each scrape
request selects the PVE node, so there's nothing here for a PVE
hostname/IP itself.

## Exposed metrics

`pve_*`-prefixed metrics covering node/cluster status, guest (QEMU/LXC)
CPU/memory/disk/network, storage capacity, HA state, subscription status,
replication, and backup coverage — see the
[upstream README's example output](https://github.com/prometheus-pve/prometheus-pve-exporter#exported-metrics)
for the full list with labels.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus` template
variable at a Prometheus datasource scraping this exporter (job `pve`).
Pick the PVE node to inspect via the `$instance` dashboard variable
(populated from `pve_node_info`'s `instance` label).

The dashboard was carried over from its original environment unmodified
in scope (all panels kept). Most panels — guest CPU/memory/disk/network,
storage, resource-allocation summary — come entirely from this bundle's
`pve_*` metrics. A few panels source from metrics this bundle does
**not** provide, and will stay empty without them:

- **CPU temperature (bare metal)** and the **NVIDIA GPU** row: these read
  `node_hwmon_temp_celsius`/`node_thermal_zone_temp` (from
  [node_exporter](https://github.com/prometheus/node_exporter)) and
  `nvidia_smi_*` (from an NVIDIA SMI exporter such as
  [nvidia_gpu_exporter](https://github.com/utkuozdemir/nvidia_gpu_exporter)),
  both filtered on a `group` label (`group="proxmox"` for bare-metal
  x86, `group="arm"` for ARM). That label comes from your own Prometheus
  relabeling — set it up on your node_exporter/GPU-exporter scrape jobs
  if you want these panels populated; otherwise leave them empty.
- **Guests memory usage**: for QEMU guests, prefers a per-guest
  node_exporter (scraped separately, matched on hostname) over PVE's own
  RSS figure — see the panel's own description for why. Falls back to
  PVE's host-RSS figure (labelled `host RSS`) when no such exporter is
  scraped.
