# AGENTS.md

Guide for coding agents working in this repository. It covers two jobs:
**installing** a bundle into someone's Prometheus + Grafana, and **changing**
the repo. The bundle's own `README.md` is the source of truth for anything
specific to that service; this file covers what holds across all of them.

## Installing a bundle

Work through these steps in order. Each ends on a check; a step is done only
when its check passes. Where a bundle's README gives its own scrape config or
check, use that one: proxmox, for example, serves PVE data only from
`/pve?target=<node>`, and its `/metrics` holds just the exporter's own
metrics.

1. **Pick the bundle and read its README.** The table in [`README.md`](README.md)
   maps each service to a folder, and the folder sits in a tier:
   - `exporters/`: an exporter this repo ships (code, `compose.yaml`,
     `.env.example`, dashboard). `exporters/zfs-zpool` is a node_exporter
     textfile collector run by a systemd timer, not a container.
   - `integrations/`: pinned community exporters (`compose.yaml`,
     `.env.example`, dashboard).
   - `dashboards/`: the service exposes its own metrics; the folder has the
     dashboard and a `prometheus-scrape.yml` snippet, nothing to run. See
     [`dashboards/README.md`](dashboards/README.md).

   `dashboards/loki-logs` reads logs from Loki rather than Prometheus
   metrics: skip steps 2 to 4 for it and continue at step 5.

   Read the folder's `README.md` end to end. Done when you can list the
   bundle's credentials, environment variables, panel plugins, and any
   `*.rules.yml` it needs.

2. **Run the exporter** (`exporters/`, `integrations/`). Copy `.env.example`
   to `.env` and fill it in. Credentials (API tokens, passwords, account
   emails) come from the user: ask for them, and keep them in `.env` only.
   Then `docker compose up -d` in the folder. Done when every host port in
   the compose file's `ports:` answers `curl -s http://<host>:<port>/metrics`
   with the metrics the README documents (`integrations/media-clients` runs
   two exporters on two ports). The two other shapes follow their README
   instead: `exporters/zfs-zpool` installs a script and systemd timer and is
   done when node_exporter serves its `zfs_pool_*` metrics; a `dashboards/`
   bundle is done when the service's own metrics endpoint answers.

3. **Add the Prometheus scrape job.** If the README or a
   `prometheus-scrape.yml` gives a scrape config, use it as written and fill
   in its placeholders (`TARGET_HOST`, `<pve-node-...>`). Otherwise add one
   target per port from step 2, using the job name the README names, if any
   (the Tempest alert rules expect `job="tempest"`). `exporters/zfs-zpool`
   rides on the existing node_exporter job. Reload Prometheus. Done when
   `up` is 1 for every new target and a query for a metric the README lists
   returns series (for proxmox, `pve_node_info`).

4. **Load rule files**, if the folder has a `*.rules.yml`. Add it to
   `rule_files:` and reload. `integrations/unifi`'s recording rules feed two
   of its panels; `exporters/tempest`'s are optional alerts. Done when
   `promtool check rules <file>` passes and the rules appear on Prometheus's
   `/rules` page.

5. **Connect Grafana to the data.** Grafana needs a Prometheus datasource
   whose URL reaches the Prometheus from step 3 from where Grafana runs
   (a Loki datasource for `dashboards/loki-logs`). Reuse one that exists, or
   add it under Connections → Data sources. Done when the datasource's
   "Save & test" succeeds; for loki-logs, also when a query on any of your
   log stream labels returns lines in Explore.

6. **Install the panel plugins** the README lists, with
   `grafana-cli plugins install <id>` or `GF_INSTALL_PLUGINS`, then restart
   Grafana. Only `exporters/tempest` needs any today. Done when each plugin
   shows under Administration → Plugins.

7. **Import every `dashboard*.json` in the folder.** Use Grafana's
   Dashboards → Import, or the API:
   `POST /api/dashboards/db` with `{"dashboard": <file with "id": null>, "overwrite": false}`.
   Use `/api/dashboards/db`, not `/api/dashboards/import`: the dashboards
   select their datasource through a `DS_PROMETHEUS` (or `DS_LOKI`) template
   variable, not import-time inputs. Point that variable at the datasource
   from step 5. Done when the panels show data, apart from any the README
   says need extra setup.

### Across bundles

- Every compose file publishes its metrics port on all interfaces. If
  Prometheus runs on the same host, offer to bind it to `127.0.0.1`.
- `exporters/monifactory-rcon` and `integrations/media-clients` both publish
  host port 8000. Installing both on one host means changing the host side of
  one mapping, and the scrape target with it.
- Third-party images are pinned to tested tags. Keep the pin unless the user
  asks for an upgrade.

## Changing the repo

- `make verify` is the gate: the private-identifier scrub, JSON validity,
  `docker compose config`, `promtool check config` on every
  `prometheus-scrape.yml`, `promtool test rules` on every `query_test.yml`,
  and a round-trip import of every dashboard into a throwaway Grafana. It
  needs Docker. It passes before a change is done.
- Exporters with code carry their own tests (`pytest` in `exporters/tempest`,
  `exporters/sensorpush`, `exporters/monifactory-rcon`;
  `./test_prom_output.sh` in `exporters/zfs-zpool`). The tempest and
  sensorpush tests skip silently when `prometheus_client` is missing, so run
  them in a virtualenv with the folder's `requirements.txt`.
- A dashboard query with non-obvious behavior gets a promtool case in the
  folder's `query_test.yml`.
- Dashboards reach Prometheus and Loki only through the `${DS_PROMETHEUS}` /
  `${DS_LOKI}` template variables and carry no `__inputs`; the built-in
  Grafana annotation datasource (`-- Grafana --`) stays as it is. Hostnames,
  IPs, and job names in queries are generic or templated, so the dashboard
  works on any install.
- When a change alters setup or behavior, update the bundle's `README.md`,
  and the table in the top-level `README.md` when bundles are added or
  renamed.
