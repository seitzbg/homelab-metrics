# sensorpush-exporter

Prometheus exporter for [SensorPush](https://sensorpush.com/) temperature/humidity
sensors and their WiFi gateway. The SensorPush G1 WiFi Gateway has no local API —
sensor readings are only available from the SensorPush Gateway Cloud API
(`https://api.sensorpush.com`). This exporter signs in with a SensorPush account
(OAuth2, email + password), polls the cloud once per `POLL_INTERVAL` and caches
the latest per-sensor readings, then serves them as Prometheus metrics.

Polling is decoupled from scraping: the SensorPush API is rate-limited to
roughly 1 request/minute and the gateway only relays new samples about once a
minute, so a background poller with a cache is used instead of collecting on
every scrape. Prometheus scrapes the cache — scrape cadence never hits (or
gets throttled by) the cloud API.

## Prerequisites

- A SensorPush account (email + password) with at least one sensor and gateway
  registered.

## Quickstart

```bash
cp .env.example .env
# edit .env with your SensorPush account email/password
docker compose up -d
curl localhost:9825/metrics
```

## Configuration (environment)

| Variable | Default | Description |
|---|---|---|
| `SENSORPUSH_EMAIL` | *(required)* | SensorPush account email |
| `SENSORPUSH_PASSWORD` | *(required)* | SensorPush account password |
| `EXPORTER_PORT` | `9825` | port the exporter listens on |
| `POLL_INTERVAL` | `60` | seconds between SensorPush cloud polls |
| `SENSORPUSH_GATEWAY_STALE_SECONDS` | `900` | mark the gateway inactive if not seen within this many seconds |
| `SENSORPUSH_API_BASE` | `https://api.sensorpush.com/api/v1` | SensorPush API base URL |
| `SENSORPUSH_HTTP_TIMEOUT` | `20` | per-request HTTP timeout, seconds |
| `SENSORPUSH_TOKEN_TTL` | `28800` | proactive re-auth interval, seconds (access token is valid ~24h) |

## Exposed metrics

All gauges unless noted. Per-sensor metrics are labeled `{sensor, sensor_id}`;
per-gateway metrics are labeled `{gateway}`.

| Metric | Type | Description |
|---|---|---|
| `sensorpush_temperature_fahrenheit` | gauge | Latest sensor temperature, °F |
| `sensorpush_humidity_percent` | gauge | Latest sensor relative humidity, % |
| `sensorpush_dewpoint_fahrenheit` | gauge | Latest sensor dewpoint, °F |
| `sensorpush_battery_volts` | gauge | Sensor battery voltage |
| `sensorpush_signal_rssi_dbm` | gauge | Sensor radio signal strength (RSSI), dBm |
| `sensorpush_sensor_active` | gauge | 1 if the sensor is active, 0 otherwise |
| `sensorpush_last_sample_timestamp_seconds` | gauge | Unix timestamp of the sensor's most recent sample |
| `sensorpush_gateway_last_seen_timestamp_seconds` | gauge | Unix timestamp the gateway was last seen by the SensorPush cloud |
| `sensorpush_gateway_active` | gauge | 1 if the gateway checked in within the freshness window, 0 otherwise |
| `sensorpush_scrape_success` | gauge | 1 if the last SensorPush cloud poll succeeded, 0 otherwise (the exporter's own "up" signal) |
| `sensorpush_last_scrape_timestamp_seconds` | gauge | Unix timestamp of the last SensorPush cloud poll |
| `sensorpush_api_errors_total` | counter | Total failed SensorPush cloud polls since exporter start |

Note: the per-sensor and per-gateway metric families only populate after the
first successful cloud poll — `sensorpush_scrape_success`,
`sensorpush_last_scrape_timestamp_seconds`, and `sensorpush_api_errors_total`
are always emitted, even before the first successful poll or with invalid
credentials, and are the metrics to alert on for exporter health.

### Temperature units

The SensorPush Gateway Cloud API always returns temperature and dewpoint in
degrees Fahrenheit, regardless of the unit selected for display in the
SensorPush app (the [published API schema](https://api.sensorpush.com/api/v1/support/swagger/swagger-v1.json)
pins both fields to Fahrenheit). The exporter passes them through unchanged, so
`sensorpush_temperature_fahrenheit` / `sensorpush_dewpoint_fahrenheit` are true
°F. Convert to Celsius in Grafana if you prefer.

There is no source-unit setting. Earlier versions had a
`SENSORPUSH_SOURCE_TEMP_UNIT` variable that, when set to `c`, incorrectly
re-converted the already-Fahrenheit values (e.g. a 68 °F reading was exported as
154.4). **Migration:** remove `SENSORPUSH_SOURCE_TEMP_UNIT` from your
environment (it is now ignored, and the exporter logs a warning if it is still
set). If you had it set to `c`, temperatures recorded before this upgrade are
wrong; only data collected afterward is correct.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus` template
variable at your Prometheus datasource. Panels: gateway/sensor status
overview, current temperature/humidity per sensor, temperature/humidity/
dewpoint/battery trends, and signal strength / data-freshness.

## Testing

```bash
cd exporters/sensorpush
pip install -r requirements.txt   # prometheus_client + requests
python -m pytest test_sensorpush.py -v
```

`test_sensorpush.py` drives `SensorPushCollector` with a fake poller and a
frozen clock — no cloud calls. It asserts that `sensorpush_gateway_active`
drops to 0 once the cached check-in ages past `SENSORPUSH_GATEWAY_STALE_SECONDS`
(and returns to 1 after a fresh check-in), and that temperatures pass through
as Fahrenheit. The tests skip automatically if `prometheus_client` is absent.
