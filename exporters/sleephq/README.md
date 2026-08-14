# sleephq-exporter

Prometheus exporter for [SleepHQ](https://sleephq.com) (cloud CPAP therapy
reporting platform for ResMed/Viatom devices and O2 rings). SleepHQ has no
local API — data is only available from the SleepHQ cloud API
(`https://sleephq.com/api/v1`). This exporter authenticates with a SleepHQ
OAuth2 client id/secret (password grant), polls the API once per
`POLL_INTERVAL` for each machine's most recent night, caches the result, and
serves it as Prometheus metrics.

Only the **latest night** is ever served live: Prometheus rejects scraped
samples with past timestamps, so a `/metrics` endpoint can never publish
history. Loading years of prior nights is a one-time, out-of-band step — see
[Historical backfill](#historical-backfill).

## Prerequisites

- A SleepHQ account (https://sleephq.com) with at least one CPAP machine or
  O2 Ring registered.
- A SleepHQ OAuth2 client id + secret. Sign in to your SleepHQ account and
  generate API credentials from your account settings; see the official
  [SleepHQ API docs](https://sleephq.com/api-docs/index.html) for the
  current reference. Use `grant_type=password` — `client_credentials`
  returns a token that looks valid but every subsequent call 500s (see
  "SleepHQ API quirks" below).

## Quickstart

```bash
cp .env.example .env
# edit .env with your SleepHQ client id/secret
docker compose up -d
curl localhost:9826/metrics
```

## Configuration (environment)

| Variable | Default | Description |
|---|---|---|
| `SLEEPHQ_CLIENT_ID` | *(required)* | SleepHQ OAuth2 client id |
| `SLEEPHQ_CLIENT_SECRET` | *(required)* | SleepHQ OAuth2 client secret |
| `EXPORTER_PORT` | `9826` | port the exporter listens on |
| `POLL_INTERVAL` | `21600` (6h) | seconds between SleepHQ API polls |
| `SLEEPHQ_HTTP_TIMEOUT` | `30` | per-request HTTP timeout, seconds |

## Exposed metrics

All gauges unless noted. Per-night metrics are labelled `machine` /
`machine_id` / `brand` / `model`, plus any extra label shown below.

| Metric | Notes |
|---|---|
| `sleephq_usage_seconds` | Machine-on time for the night. **Not necessarily "hours slept"** — see caveat below. |
| `sleephq_usage_score`, `sleephq_ahi_score`, `sleephq_leak_score` | SleepHQ's own 0–100 scores |
| `sleephq_ahi{type=...}` | `total`, `hypopnea`, `all_apnea`, `clear_airway`, `obstructive_apnea`, `unidentified_apnea` — events **per hour**, not counts |
| `sleephq_pressure_cmh2o{stat=...}` | `av`, `med`, `upper`, `min`, `max` — `upper` is the 95th percentile |
| `sleephq_epap_cmh2o{stat=...}`, `sleephq_leak_rate_lpm{stat=...}`, `sleephq_flow_limit{stat=...}`, `sleephq_resp_rate_bpm{stat=...}` | same `stat` set |
| `sleephq_spo2_percent{stat=...}`, `sleephq_pulse_rate_bpm{stat=...}`, `sleephq_movement{stat=...}` | O2 Ring only |
| `sleephq_machine_setting{setting=...}` | `pressure_min`, `pressure_max`, `epr_level`, `humidity_level`, `ramp_pressure` |
| `sleephq_machine_settings_info{mode,mask,epr,ramp,response,climate_control,smart_start}` | always `1`; use for setting-change annotations |
| `sleephq_night_timestamp_seconds` | Unix timestamp (UTC midnight) of the night currently being reported |
| `sleephq_night_age_seconds` | age of the most recent night's data — **alert on this** to catch a stalled upload |
| `sleephq_scrape_success` | 1 if the last SleepHQ API poll succeeded, 0 otherwise (the exporter's own "up" signal) |
| `sleephq_last_scrape_timestamp_seconds` | Unix timestamp of the last SleepHQ API poll |
| `sleephq_api_errors_total` | counter — total failed SleepHQ API polls since exporter start |

Field sets are **disjoint per device type**: an AirSense record carries no
`spo2`/`pulse_rate`/`movement`, and an O2 Ring carries no
`ahi`/`pressure`/`leak_rate`. `sleephq_scrape_success`,
`sleephq_last_scrape_timestamp_seconds`, and `sleephq_api_errors_total` are
always emitted, even before the first successful poll or with invalid
credentials, and are the metrics to alert on for exporter health.

Because a night's value is republished until the next night lands, each
night appears in Prometheus as a flat line spanning the following day rather
than a single point. Graph these with a daily step and `last_over_time`, not
`rate`.

## Caveat: `usage` is not "hours slept"

`sleephq_usage_seconds` is machine-on time, attributed to whatever calendar
date SleepHQ's `time_offset` account setting assigns the night to (`0` =
UTC midnight, which can merge or split what a user would call one night's
sleep). Treat it as *machine-on time per calendar date* until checked
against the SleepHQ web UI for a known night.

## SleepHQ API quirks

Handled in `sleephq_common.py` (see its module docstring for detail); worth
knowing before you extend this exporter:

1. `grant_type=client_credentials` returns a valid-looking token whose every
   subsequent API call then 500s with `undefined method 'anonymous?' for
   nil`. Must be `grant_type=password` (no separate username/password
   needed — the client id/secret pair is itself user-scoped).
2. Cloudflare 403s the default `Python-urllib` User-Agent. Reads like
   rate-limiting; `requests`' own UA (which this exporter sets explicitly)
   passes.
3. Pagination is `per_page` + `page`. The JSON:API-style `page[size]` is
   accepted and **silently returns zero records** — an empty success, not
   an error.
4. `attributes.usage` is a **string** of seconds, not an integer, even
   though every sibling summary field is numeric.

Do **not** derive schema from `garett09/sleephq-mcp` — its fixtures claim
`ahi_summary:{av,oa,ca,h}`, which is invented and contradicts the real API.

## Historical backfill

`backfill.py` renders SleepHQ history as an OpenMetrics text dump with
explicit per-night timestamps, for loading into Prometheus via `promtool
tsdb create-blocks-from openmetrics`. This is necessary because Prometheus
rejects scraped samples with past timestamps — a `/metrics` endpoint can
only ever publish the current value, so `exporter.py` only ever serves the
latest night; everything older has to be loaded once, out of band.

```bash
export SLEEPHQ_CLIENT_ID=...
export SLEEPHQ_CLIENT_SECRET=...
python3 backfill.py --out sleephq.openmetrics   # default: everything through yesterday

docker run --rm -v "$PWD:/data" -w /data --entrypoint promtool \
  prom/prometheus:latest tsdb create-blocks-from openmetrics \
  --max-block-duration=720h /data/sleephq.openmetrics /data/blocks
```

The `blocks/` output must be owned by uid `65534` before `promtool` runs —
the `prom/prometheus` image executes as `nobody`, and against a root-owned
directory it fails with `setting up sandbox dir: permission denied`:
`chown -R 65534:65534 blocks`.

Copy the resulting block directories into your Prometheus data directory and
restart Prometheus so they are picked up immediately (they would otherwise
merge only at the next compaction cycle):

```bash
scp -r blocks/* root@YOUR_PROM_HOST:/path/to/prometheus-data/
ssh root@YOUR_PROM_HOST 'chown -R 65534:65534 /path/to/prometheus-data && docker restart prometheus'
```

Verify — expect the earliest backfilled night to be queryable:

```bash
curl -s 'http://YOUR_PROM_HOST:9090/api/v1/query?query=count(sleephq_ahi)'
```

`SCRAPE_LABELS` in `backfill.py` (`job`, `instance`, plus any extra labels)
must be kept identical to whatever your live Prometheus scrape job assigns
to the `sleephq-exporter` target — mismatched labels turn the backfilled
history and the live series into two disjoint series that never unify.

### Filling a gap

The exporter only ever publishes the **latest** night. Any night that
scrolled past while the exporter was down is captured by nothing and must
be backfilled explicitly. Use `--from-date`/`--to-date` to emit only the
missing nights, rather than regenerating the whole history and stacking
overlapping blocks on top of the existing ones:

```bash
python3 backfill.py --from-date 2026-07-18 --to-date 2026-07-19 --out gap.openmetrics
```

To find gaps, query one instant per day at exactly 12:00 UTC — an unaligned
`query_range` step can silently miss the once-a-day sample and make a
healthy series look empty.

### Optional: a dedicated long-retention Prometheus

The exporter's scrape job typically shares a Prometheus with everything else
on your network, which is probably tuned for a short retention (days to
weeks). CPAP history is worth keeping much longer, and it's tiny (a full
year of nightly summaries is on the order of tens of kilobytes), so pointing
`backfill.py`'s output at a **second, dedicated Prometheus instance** with a
long retention (e.g. `--storage.tsdb.retention.time=5y`) is a reasonable
option if you'd rather not raise retention fleet-wide. This is entirely
optional — any Prometheus with `promtool tsdb` backfill support works fine.

If you do set an explicit long retention, note that
`--storage.tsdb.retention.time=0` does **not** mean "keep forever" —
Prometheus treats `0` as *unset* and silently falls back to the 15-day
default, deleting every backfilled block at the first compaction. Confirm
after any change: `docker logs <container> | grep 'retention updated'` must
report the duration you set, not `15d`.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus` template
variable at your Prometheus datasource (or the dedicated long-retention one,
if you set one up). Panels: latest-night summary, AHI/usage/leak trends,
pressure/leak/flow percentile bands, and O2 Ring vitals (if applicable).
