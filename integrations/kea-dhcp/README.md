# kea-dhcp (bundled: mweinelt/kea-exporter)

This stack runs
[kea-exporter](https://github.com/mweinelt/kea-exporter): it queries the
ISC Kea DHCP server's control interface (either a Kea Control Agent HTTP
endpoint, or the DHCP4/DHCP6 daemons' Unix control sockets directly),
reads configuration (subnets/pools) and live statistics, and re-exposes
them as `kea_dhcp4_*`/`kea_dhcp6_*` Prometheus metrics on its own
`/metrics` endpoint. Kea >= 1.3.0 is required.

## Prerequisites

- A running ISC Kea DHCP4 and/or DHCP6 server with its control interface
  reachable from wherever this container runs — either:
  - a **Kea Control Agent** (`kea-ctrl-agent`) HTTP endpoint, which is the
    simplest option: one CA URL auto-discovers and queries every dhcp4/
    dhcp6 module configured under its `control-sockets`, so a single
    `TARGETS` value covers both address families in a dual-stack setup; or
  - the raw **Unix Domain Socket(s)** the dhcp4/dhcp6 daemons themselves
    listen on (`control-socket` in `kea-dhcp4.conf`/`kea-dhcp6.conf`,
    conventionally something like `/run/kea/kea4-ctrl-socket` and
    `/run/kea/kea6-ctrl-socket`) — bind-mounted into the container (see
    the commented `volumes:` in `compose.yaml`) and passed as separate,
    space-separated `TARGETS` entries, one per daemon.
- Whichever mode you use, the exporter needs **read/write** access to the
  socket it talks to (control-socket protocol, not just a status read) —
  see [Kea's control-channel docs](https://kea.readthedocs.io/en/latest/arm/ctrl-channel.html)
  for permission setup, and the [dhcp4](https://kea.readthedocs.io/en/latest/arm/dhcp4-srv.html#management-api-for-the-dhcpv4-server)/[dhcp6](https://kea.readthedocs.io/en/latest/arm/dhcp6-srv.html#management-api-for-the-dhcpv6-server)
  management-API docs for how to enable the control socket/CA in the first
  place.

## Quickstart

```bash
cp .env.example .env
# edit .env: set TARGETS to your Kea Control Agent URL (recommended) or
# control socket path(s) — see the comments in .env.example
docker compose up -d
curl localhost:9547/metrics
```

Unlike some Prometheus exporters, `kea-exporter` needs `TARGETS` reachable
**at startup**: for an HTTP Control-Agent target, module discovery
(`config-get`) runs synchronously in the client constructor, and any
target that fails to construct is dropped; with zero surviving targets
the process calls `sys.exit(1)`. Under `restart: unless-stopped` that
shows up as a crash-restart loop (`docker compose logs` will show
connection-refused/timeout errors) rather than a container that starts
cleanly and serves `0` metrics — confirmed against the pinned `v0.7.1`
image with a deliberately unreachable `TARGETS`. Point `TARGETS` at a
live Kea Control Agent or control socket before bringing the stack up.

## Configuration (environment)

Variable names below are the exporter's own — every one also has an
equivalent CLI flag (`-a/--address`, `-p/--port`, etc.); see the
[upstream README](https://github.com/mweinelt/kea-exporter#readme) for
the full option list.

| Variable | Default | Description |
|---|---|---|
| `TARGETS` | *(required)* | space-separated list of Kea Control Agent URL(s) and/or control socket path(s) |
| `ADDRESS` | `0.0.0.0` | address the exporter's HTTP server binds to |
| `PORT` | `9547` | port the exporter's HTTP server binds to |
| `INTERVAL` | `0` | minimum seconds between two queries to Kea (`0` = query fresh on every `/metrics` scrape) |
| `CLIENT_CERT` / `CLIENT_KEY` | *(none)* | client TLS cert/key, if the Control Agent requires mutual TLS |
| `REQUESTS_CA_BUNDLE` | *(none)* | CA bundle path to validate a self-signed cert on the Control Agent (standard Python `requests` env var, not exporter-specific) |

## Exposed metrics

This dashboard only charts `kea_dhcp4_*` series (matching what the source
deployment monitors); the exporter emits an analogous `kea_dhcp6_*` set
for IPv6 pools (`na_total`/`na_assigned_total`/`pd_total`/etc. instead of
the v4 address-pool names) if you point it at a DHCP6 control
socket/Control Agent module — see the
[exporter source](https://github.com/mweinelt/kea-exporter/blob/main/kea_exporter/exporter.py)
for the full v6 metric list. Metric names below are `kea_dhcp4_`-prefixed
— this table omits the prefix. Every metric also carries the standard
Prometheus `instance` label from the scrape target.

| Metric | Extra labels | Description |
|---|---|---|
| `addresses_total` | `subnet`, `subnet_id`, `pool` | size of a subnet's address pool |
| `addresses_assigned_total` | `subnet`, `subnet_id`, `pool` | addresses currently assigned out of that pool |
| `addresses_declined_total` | `subnet`, `subnet_id`, `pool` | addresses reported declined by a client (DHCPDECLINE) |
| `addresses_declined_reclaimed_total` | `subnet`, `subnet_id`, `pool` | declined addresses that were reclaimed |
| `addresses_reclaimed_total` | `subnet`, `subnet_id`, `pool` | expired addresses that were reclaimed |
| `leases_reused_total` | `subnet`, `subnet_id` | times a lease was renewed in memory rather than reassigned |
| `packets_sent_total` / `packets_received_total` | `operation` | DHCP packet counters by message type (DISCOVER, OFFER, REQUEST, ACK, ...) |
| `allocations_failed_total` | `subnet`, `subnet_id`, `context` | address allocation failures |
| `reservation_conflicts_total` | `subnet`, `subnet_id` | host-reservation conflicts |

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus`
template variable at a Prometheus datasource scraping this exporter's
`/metrics` (job name `kea` in the reference scrape config). Panels: lease
pool utilization and assigned/total addresses per subnet, DHCP packet
rate by type (sent/received), declined addresses, and lease-reuse rate.

**Single-node vs. HA:** the source of this dashboard runs Kea as a
2-node HA pair, and its `Instance` template variable (multi-select, "All"
by default) is what drove that — every panel groups by `instance`
already, via `label_values(kea_dhcp4_addresses_total, instance)`, rather
than hardcoding node names. That means every panel in this bundle
populates the same way whether you point it at one Kea instance or
several — there is no HA-only panel here that goes empty on a
single-node setup (this dashboard doesn't chart Kea's HA state at all,
just pool/packet/lease counters that exist regardless of node count). On
a single node you'll just see one series per panel instead of one per
peer.
