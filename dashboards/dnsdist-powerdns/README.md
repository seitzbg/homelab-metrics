# dnsdist-powerdns

Grafana dashboard for a **dnsdist → PowerDNS Authoritative** setup, using each
daemon's built-in Prometheus endpoint: query/response rates, rcodes, cache hit
ratio and latency (dnsdist), plus backend query volume and uptime (PowerDNS).

## Enable metrics

- **PowerDNS Authoritative** — in `pdns.conf`: `webserver=yes`, `api=yes`,
  `webserver-address=0.0.0.0`, and add your Prometheus host to
  `webserver-allow-from`. Metrics: `:8081/metrics`.
- **dnsdist** — in `dnsdist.conf`: `webserver("0.0.0.0:8083")` plus
  `setWebserverConfig({acl="<prometheus-ip>/32", apiKey="..."})`. Metrics:
  `:8083/metrics`.

## Scrape

See [`prometheus-scrape.yml`](./prometheus-scrape.yml). The relabel rule gives
both jobs the same `instance` (the hostname, port stripped) so the `$instance`
variable ties dnsdist and PowerDNS panels to the same server.

> **dnsdist + API key:** when an API key is configured, `/metrics` needs the
> `X-API-Key` header, which vanilla Prometheus cannot send. Gate the endpoint
> by ACL only, front it with a header-injecting proxy, or scrape it with an
> agent that supports custom headers (e.g. Grafana Alloy).

## Import

Import `dashboard.json` and select your Prometheus datasource for the
**Prometheus** variable. `$instance`
(`label_values(dnsdist_queries, instance)`) selects the host.
