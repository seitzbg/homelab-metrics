# tempest-exporter

Prometheus exporter for a [WeatherFlow Tempest](https://weatherflow.com/tempest-weather-system/)
weather station. The Tempest hub broadcasts observations over LAN UDP (port
50222), but that only reaches devices on the hub's own network — so instead of
listening for UDP, this exporter polls the WeatherFlow REST API
(`https://swd.weatherflow.com/swd/rest`) once per `POLL_INTERVAL`, caches the
latest station observation, and serves it as Prometheus metrics. That makes it
usable from wherever Prometheus runs, regardless of VLAN.

Polling is decoupled from scraping: WeatherFlow asks integrations to poll no
faster than once a minute, and the Tempest only reports a new observation about
once a minute anyway. A background poller keeps the latest snapshot and
Prometheus scrapes that cache, so scrape cadence never hits (or gets throttled
by) the cloud API.

> If your Prometheus host *is* on the same LAN/VLAN as the hub, you may prefer a
> UDP-listening path; this exporter is the cloud path for when it isn't (and it
> pairs naturally with Home Assistant's local `weatherflow` integration
> consuming the UDP feed at the same time).

## Prerequisites

- A WeatherFlow Tempest station reporting to the cloud, and its numeric
  **station id** (visible in the URL at `tempestwx.com/station/<id>`).
- A **personal access token**: tempestwx.com → **Settings → Data Authorizations
  → Create Token**.
- For the **Wind compass** panel only, the Grafana community panel plugin
  [`oceandatatools-compass-panel`](https://grafana.com/grafana/plugins/oceandatatools-compass-panel/)
  (`grafana-cli plugins install oceandatatools-compass-panel`, or add it to
  `GF_INSTALL_PLUGINS`). Every other panel uses built-in Grafana panels; without
  the plugin only that one compass panel shows "plugin not found".

## Quickstart

```bash
cp .env.example .env
# edit .env: set TEMPEST_TOKEN and TEMPEST_STATION_ID
docker compose up -d
curl localhost:9827/metrics
```

## Configuration (environment)

| Variable | Default | Description |
|---|---|---|
| `TEMPEST_TOKEN` | *(required)* | WeatherFlow personal access token (sent as a `?token=` query param) |
| `TEMPEST_STATION_ID` | *(required)* | numeric station id, e.g. `12345` |
| `TEMPEST_DEVICE_ID` | *(auto)* | numeric Tempest device id, for the battery reading; auto-discovered from station metadata if unset |
| `EXPORTER_PORT` | `9827` | port the exporter listens on |
| `POLL_INTERVAL` | `60` | seconds between WeatherFlow cloud polls |
| `TEMPEST_API_BASE` | `https://swd.weatherflow.com/swd/rest` | WeatherFlow REST API base URL |
| `TEMPEST_HTTP_TIMEOUT` | `20` | per-request HTTP timeout, seconds |
| `TEMPEST_STALE_SECONDS` | `300` | mark the station offline if the newest observation is older than this |

## Units

The REST `obs` payload is **always metric** regardless of the account's display
preference (°C, m/s, millibars, mm, km) — trusting the account's unit setting
would mislabel the values. The exporter therefore keeps every series unit-pure
with an explicit suffix (`_celsius`, `_meters_per_second`, `_millibars`,
`_millimeters`, `_kilometers`). Any Fahrenheit / mph / inHg / inch display
conversion is done in the dashboard's PromQL, so the stored metrics stay
canonical.

## Exposed metrics

All gauges unless noted. Weather metrics are labeled `{station, station_id}`;
the exporter-health metrics (`tempest_scrape_success`,
`tempest_last_scrape_timestamp_seconds`, `tempest_api_errors_total`) are
unlabeled.

| Metric | Description |
|---|---|
| `tempest_air_temperature_celsius` | Outside air temperature |
| `tempest_feels_like_celsius` | Apparent "feels like" temperature |
| `tempest_heat_index_celsius` | Heat index |
| `tempest_wind_chill_celsius` | Wind chill |
| `tempest_dew_point_celsius` | Dew point |
| `tempest_wet_bulb_temperature_celsius` | Wet-bulb temperature |
| `tempest_delta_t_celsius` | Delta-T (dry-bulb minus wet-bulb) |
| `tempest_relative_humidity_percent` | Relative humidity |
| `tempest_station_pressure_millibars` | Raw station pressure |
| `tempest_sea_level_pressure_millibars` | Sea-level-adjusted pressure |
| `tempest_barometric_pressure_millibars` | Barometric pressure |
| `tempest_air_density_kilograms_per_cubic_meter` | Air density |
| `tempest_wind_avg_meters_per_second` | Average wind speed over the reporting interval |
| `tempest_wind_gust_meters_per_second` | Maximum wind gust over the reporting interval |
| `tempest_wind_lull_meters_per_second` | Minimum wind lull over the reporting interval |
| `tempest_wind_direction_degrees` | Wind direction (0–359, meteorological) |
| `tempest_solar_radiation_watts_per_square_meter` | Solar radiation |
| `tempest_uv_index` | UV index |
| `tempest_brightness_lux` | Illuminance |
| `tempest_precip_rate_millimeters_per_minute` | Precipitation in the last reporting minute |
| `tempest_precip_accum_local_day_millimeters` | Precipitation so far today (station-local) |
| `tempest_precip_accum_last_1hr_millimeters` | Precipitation in the last hour |
| `tempest_lightning_strike_count` | Lightning strikes in the reporting interval |
| `tempest_lightning_strike_count_last_1hr` | Lightning strikes in the last hour |
| `tempest_lightning_strike_count_last_3hr` | Lightning strikes in the last 3 hours |
| `tempest_lightning_strike_last_distance_kilometers` | Distance to the most recent strike |
| `tempest_lightning_strike_last_timestamp_seconds` | Unix timestamp of the most recent strike |
| `tempest_battery_volts` | Station battery voltage (see below) |
| `tempest_battery_percent` | Estimated battery state-of-charge |
| `tempest_last_observation_timestamp_seconds` | Unix timestamp of the most recent observation |
| `tempest_station_online` | 1 if an observation arrived within the freshness window, else 0 |
| `tempest_scrape_success` | 1 if the last cloud poll succeeded, else 0 (the exporter's own "up" signal) |
| `tempest_last_scrape_timestamp_seconds` | Unix timestamp of the last cloud poll |
| `tempest_api_errors_total` | *(counter)* Total failed cloud polls since start |

An observation field that a given station doesn't report is simply not emitted
(no stale/zero series). The exporter-health trio is always emitted — even before
the first successful poll or with an invalid token — and is what to alert on for
exporter health.

### Battery

Battery voltage isn't in the station observation, so the exporter also polls the
per-**device** observation (`obs_st`, a fixed-order array; battery volts at index
16) and exports `tempest_battery_volts` plus a `tempest_battery_percent`
estimate. The percent is interpolated from voltage with the Tempest's LTO
(lithium-titanate) discharge curve — the same curve Home Assistant's WeatherFlow
integration uses — so the two report matching percentages. There's no fuel
gauge; percent is an estimate from voltage. At **≥ 2.455 V** the station runs in
full-performance Mode 0; lower voltages throttle sensor cadence. The battery poll
is best-effort: if the device call fails it keeps the last known value and never
sinks the weather metrics.

## Dashboard

`dashboard.json` — import into Grafana and point the `Prometheus` template
variable at your Prometheus datasource. Panels cover current conditions
(temperature / feels-like / humidity / pressure / UV / solar), wind (speed and
gusts, plus a **compass**, a "wind from" cardinal readout, and a direction-over-
time ribbon colored by 8-point sector), rain, lightning, and station health
(battery, online state, last observation). The compass panel needs the
`oceandatatools-compass-panel` plugin (see Prerequisites); the rest are built-in.

## Testing

```bash
cd exporters/tempest
pip install -r requirements.txt   # prometheus_client + requests
python -m pytest test_tempest.py -v
```

`test_tempest.py` drives `TempestCollector` with a fake poller and a frozen
clock — no cloud calls. It asserts that the API token never appears in a
sanitized error summary, and that `tempest_station_online` drops to 0 once the
cached observation ages past `TEMPEST_STALE_SECONDS` (and returns to 1 after a
fresh poll). The tests skip automatically if `prometheus_client` is absent.
