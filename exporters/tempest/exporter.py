#!/usr/bin/env python3
"""
WeatherFlow Tempest -> Prometheus exporter.

The Tempest weather station reports to the WeatherFlow cloud. Its hub also
broadcasts observations over LAN UDP (port 50222), but this exporter is meant
to run wherever Prometheus lives — typically a host on a different network or
VLAN than the hub, where it cannot hear that broadcast — so instead it polls
the WeatherFlow REST API once per POLL_INTERVAL, caches the latest station
observation, and serves it as Prometheus metrics on :EXPORTER_PORT. (Home
Assistant, on the same LAN/VLAN as the hub, can consume the local UDP feed
directly; this exporter is the decoupled cloud path for Grafana.)

Why a background poller instead of collecting on scrape: WeatherFlow asks
integrations to poll no faster than once a minute, and the Tempest only reports
a new observation about once a minute anyway. Prometheus scrapes the *cache*, so
scrape cadence is decoupled from the API and a burst of scrapes can never hammer
(or get us throttled by) the cloud.

Units: the REST "obs" payload is always metric regardless of the account's
display preference (Celsius, m/s, millibars, mm, km). We additionally pin
units_*=metric on the request as belt-and-suspenders, and export metric base
units with explicit suffixes. Any Fahrenheit / mph / inHg display conversion is
done in the Grafana panels, keeping the exported series unit-pure.

Metrics (all gauges unless noted, labeled by station name + id):
  tempest_air_temperature_celsius            tempest_feels_like_celsius
  tempest_heat_index_celsius                 tempest_wind_chill_celsius
  tempest_dew_point_celsius                  tempest_wet_bulb_temperature_celsius
  tempest_delta_t_celsius                    tempest_relative_humidity_percent
  tempest_station_pressure_millibars         tempest_sea_level_pressure_millibars
  tempest_barometric_pressure_millibars      tempest_air_density_kilograms_per_cubic_meter
  tempest_wind_avg_meters_per_second         tempest_wind_gust_meters_per_second
  tempest_wind_lull_meters_per_second        tempest_wind_direction_degrees
  tempest_solar_radiation_watts_per_square_meter
  tempest_uv_index                           tempest_brightness_lux
  tempest_precip_rate_millimeters_per_minute tempest_precip_accum_local_day_millimeters
  tempest_precip_accum_last_1hr_millimeters
  tempest_lightning_strike_count             tempest_lightning_strike_count_last_1hr
  tempest_lightning_strike_count_last_3hr    tempest_lightning_strike_last_distance_kilometers
  tempest_lightning_strike_last_timestamp_seconds
  tempest_battery_volts                      (from the device obs; Mode 0 >=2.455V)
  tempest_battery_percent                    (SOC estimate from V; matches HA's %)
  tempest_last_observation_timestamp_seconds
  tempest_station_online                     1/0 (obs fresher than STALE window)
  tempest_scrape_success                     1/0 (last poll)
  tempest_last_scrape_timestamp_seconds
  tempest_api_errors_total                   counter

Config (environment):
  TEMPEST_TOKEN            (required) WeatherFlow personal access token
  TEMPEST_STATION_ID       (required) numeric station id (e.g. 230296)
  TEMPEST_DEVICE_ID        optional Tempest device id for battery; auto-discovered if unset
  EXPORTER_PORT            default 9827
  POLL_INTERVAL            seconds between cloud polls, default 60
  TEMPEST_API_BASE         default https://swd.weatherflow.com/swd/rest
  TEMPEST_HTTP_TIMEOUT     per-request timeout seconds, default 20
  TEMPEST_STALE_SECONDS    mark station offline if newest obs older, default 300
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

import requests
from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

log = logging.getLogger("tempest-exporter")

API_BASE = os.environ.get("TEMPEST_API_BASE", "https://swd.weatherflow.com/swd/rest").rstrip("/")
TOKEN = os.environ.get("TEMPEST_TOKEN")
STATION_ID = os.environ.get("TEMPEST_STATION_ID")
# Optional numeric WeatherFlow device id of the Tempest (type "ST"). Left unset,
# the exporter auto-discovers it from the station metadata; set it to skip that
# lookup or to pin a specific device.
DEVICE_ID = os.environ.get("TEMPEST_DEVICE_ID") or None
PORT = int(os.environ.get("EXPORTER_PORT", "9827"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
HTTP_TIMEOUT = float(os.environ.get("TEMPEST_HTTP_TIMEOUT", "20"))
# The Tempest reports ~once/minute; 5 min without a fresh obs = genuinely offline
# (hub lost power/WiFi, or cloud upload stalled), not just jitter between reports.
STALE_SECONDS = int(os.environ.get("TEMPEST_STALE_SECONDS", "300"))

# Battery voltage is NOT in the station observation — it lives in the per-device
# observation (obs_st), whose obs entry is a fixed-order array. Index 16 is the
# battery voltage in volts (per the WeatherFlow obs_st spec, verified live).
BATTERY_OBS_INDEX = 16

# Voltage -> state-of-charge (%) for the Tempest's LTO (lithium titanate) cell.
# This is the piecewise-linear curve pyweatherflowudp uses, so the exported
# tempest_battery_percent matches the % Home Assistant's WeatherFlow integration
# shows (e.g. 2.504 V -> 91%). There is no fuel gauge; % is an estimate from V.
LTO_BATTERY_CURVE = [
    (2.00, 0), (2.10, 5), (2.15, 10), (2.16, 20), (2.19, 30), (2.20, 40),
    (2.23, 50), (2.28, 60), (2.32, 70), (2.40, 80), (2.50, 90), (2.52, 95),
    (2.70, 100),
]


def battery_soc_percent(volts):
    """Interpolate battery state-of-charge (%) from voltage via LTO_BATTERY_CURVE."""
    if volts <= LTO_BATTERY_CURVE[0][0]:
        return float(LTO_BATTERY_CURVE[0][1])
    if volts >= LTO_BATTERY_CURVE[-1][0]:
        return float(LTO_BATTERY_CURVE[-1][1])
    for (v_lo, p_lo), (v_hi, p_hi) in zip(LTO_BATTERY_CURVE, LTO_BATTERY_CURVE[1:]):
        if v_lo <= volts <= v_hi:
            return p_lo + (volts - v_lo) / (v_hi - v_lo) * (p_hi - p_lo)
    return float(LTO_BATTERY_CURVE[-1][1])  # unreachable given the guards above


def _error_summary(exc):
    """Log-safe one-line description of a poll failure.

    requests bakes the request URL — whose query string carries ?token=... —
    into the message of HTTP, connection, and timeout errors, so the raw
    exception text (and any traceback) must never be logged. Report only the
    exception type and, when the error carries an HTTP response, its status
    code — enough to see what failed without leaking the credential.
    """
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status is not None:
        return f"{type(exc).__name__} (HTTP {status})"
    return type(exc).__name__

# obs key -> (metric name, help). The obs payload is documented as always metric,
# so every value is exported as-is under a metric-suffixed name. Keys absent from
# a given observation are simply skipped (defensive .get), so a station that does
# not report a field never emits a stale/zero series for it.
OBS_METRICS = [
    ("air_temperature", "tempest_air_temperature_celsius", "Outside air temperature (degrees Celsius)."),
    ("feels_like", "tempest_feels_like_celsius", "Apparent 'feels like' temperature (degrees Celsius)."),
    ("heat_index", "tempest_heat_index_celsius", "Heat index (degrees Celsius)."),
    ("wind_chill", "tempest_wind_chill_celsius", "Wind chill (degrees Celsius)."),
    ("dew_point", "tempest_dew_point_celsius", "Dew point (degrees Celsius)."),
    ("wet_bulb_temperature", "tempest_wet_bulb_temperature_celsius", "Wet bulb temperature (degrees Celsius)."),
    ("delta_t", "tempest_delta_t_celsius", "Delta-T, dry-bulb minus wet-bulb (degrees Celsius)."),
    ("relative_humidity", "tempest_relative_humidity_percent", "Relative humidity (percent)."),
    ("station_pressure", "tempest_station_pressure_millibars", "Raw station pressure (millibars)."),
    ("sea_level_pressure", "tempest_sea_level_pressure_millibars", "Sea-level-adjusted pressure (millibars)."),
    ("barometric_pressure", "tempest_barometric_pressure_millibars", "Barometric pressure (millibars)."),
    ("air_density", "tempest_air_density_kilograms_per_cubic_meter", "Air density (kg/m^3)."),
    ("wind_avg", "tempest_wind_avg_meters_per_second", "Average wind speed over the reporting interval (m/s)."),
    ("wind_gust", "tempest_wind_gust_meters_per_second", "Maximum wind gust over the reporting interval (m/s)."),
    ("wind_lull", "tempest_wind_lull_meters_per_second", "Minimum wind lull over the reporting interval (m/s)."),
    ("wind_direction", "tempest_wind_direction_degrees", "Wind direction (degrees, 0-359, meteorological)."),
    ("solar_radiation", "tempest_solar_radiation_watts_per_square_meter", "Solar radiation (W/m^2)."),
    ("uv", "tempest_uv_index", "UV index."),
    ("brightness", "tempest_brightness_lux", "Illuminance (lux)."),
    ("precip", "tempest_precip_rate_millimeters_per_minute", "Precipitation accumulated in the last reporting minute (mm)."),
    ("precip_accum_local_day", "tempest_precip_accum_local_day_millimeters", "Precipitation accumulated so far today, station-local (mm)."),
    ("precip_accum_last_1hr", "tempest_precip_accum_last_1hr_millimeters", "Precipitation accumulated in the last hour (mm)."),
    ("lightning_strike_count", "tempest_lightning_strike_count", "Lightning strikes in the reporting interval (count)."),
    ("lightning_strike_count_last_1hr", "tempest_lightning_strike_count_last_1hr", "Lightning strikes in the last hour (count)."),
    ("lightning_strike_count_last_3hr", "tempest_lightning_strike_count_last_3hr", "Lightning strikes in the last 3 hours (count)."),
    ("lightning_strike_last_distance", "tempest_lightning_strike_last_distance_kilometers", "Distance to the most recent lightning strike (km)."),
    ("lightning_strike_last_epoch", "tempest_lightning_strike_last_timestamp_seconds", "Unix timestamp of the most recent lightning strike."),
]


class TempestClient:
    """Thin client for the WeatherFlow REST station + device observation endpoints."""

    def __init__(self, token, station_id, device_id=None):
        self.token = token
        self.station_id = station_id
        # None until resolved from station metadata (or pinned via TEMPEST_DEVICE_ID).
        self.device_id = device_id
        self.session = requests.Session()

    def station_observation(self):
        url = f"{API_BASE}/observations/station/{self.station_id}"
        params = {
            "token": self.token,
            # Pin metric units so the response is deterministic even if the
            # account's display preference is Imperial.
            "units_temp": "c",
            "units_wind": "mps",
            "units_pressure": "mb",
            "units_precip": "mm",
            "units_distance": "km",
        }
        r = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json() or {}

    def _resolve_device_id(self):
        """Find the Tempest (device_type 'ST') device id from the station metadata."""
        r = self.session.get(
            f"{API_BASE}/stations/{self.station_id}",
            params={"token": self.token}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json() or {}
        # /stations/{id} returns {"stations":[{...}]}; be lenient if it ever
        # returns the station object directly.
        stations = data.get("stations") or ([data] if data.get("devices") else [])
        for st in stations:
            for dev in st.get("devices") or []:
                if dev.get("device_type") == "ST":
                    return dev.get("device_id")
        return None

    def battery_volts(self):
        """Latest Tempest battery voltage (V), or None if unavailable.

        Battery is absent from the station observation, so this reads the
        per-device obs_st array and pulls the battery-voltage slot.
        """
        if self.device_id is None:
            self.device_id = self._resolve_device_id()
            if self.device_id is None:
                return None
        r = self.session.get(
            f"{API_BASE}/observations/device/{self.device_id}",
            params={"token": self.token}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        obs = (r.json() or {}).get("obs") or []
        row = obs[0] if obs else None
        if not row or len(row) <= BATTERY_OBS_INDEX:
            return None
        return row[BATTERY_OBS_INDEX]


class Poller:
    """Owns the cloud-polling loop and the latest snapshot the collector serves."""

    def __init__(self, client):
        self.client = client
        self.lock = threading.Lock()
        self.errors = 0
        self.snapshot = {
            "station": str(client.station_id),
            "station_id": str(client.station_id),
            "obs": {},
            "obs_ts": None,
            "battery_volts": None,
            "success": False,
            "scrape_ts": 0.0,
            "errors": 0,
        }

    def poll_once(self):
        try:
            data = self.client.station_observation()
        except Exception as exc:  # network, auth, HTTP, JSON — all non-fatal
            # requests embeds the request URL (with ?token=...) in the exception
            # message, so log only a sanitized summary — never the raw exception.
            log.error("poll failed: %s", _error_summary(exc))
            with self.lock:
                self.errors += 1
                self.snapshot["success"] = False
                self.snapshot["scrape_ts"] = time.time()
                self.snapshot["errors"] = self.errors
            return

        obs_list = data.get("obs") or []
        latest = (obs_list[0] if obs_list else {}) or {}
        obs_ts = latest.get("timestamp")
        name = data.get("public_name") or data.get("station_name") or str(self.client.station_id)
        now = time.time()

        # Battery lives in a separate device call; a failure here must not sink the
        # weather data, so it is best-effort and keeps the last known value on error.
        battery = self.snapshot.get("battery_volts")
        try:
            fetched = self.client.battery_volts()
            if fetched is not None:
                battery = float(fetched)
        except Exception as exc:
            log.warning("battery poll failed: %s", _error_summary(exc))

        with self.lock:
            self.snapshot = {
                "station": name,
                "station_id": str(data.get("station_id") or self.client.station_id),
                "obs": dict(latest),
                "obs_ts": float(obs_ts) if obs_ts is not None else None,
                "battery_volts": battery,
                "success": True,
                "scrape_ts": now,
                "errors": self.errors,
            }
        log.info("poll ok: station=%s fields=%d online=%s battery=%sV",
                 name, len(latest), online, battery)

    def run(self):
        # Sleep first: main() primes an initial poll synchronously, so looping
        # without a leading sleep would double-poll at startup.
        while True:
            time.sleep(POLL_INTERVAL)
            self.poll_once()

    def get(self):
        with self.lock:
            snap = dict(self.snapshot)
            snap["obs"] = dict(self.snapshot["obs"])
            return snap


class TempestCollector:
    """Renders the poller's latest snapshot into Prometheus metric families.

    Building fresh families each collect() (rather than holding long-lived Gauge
    children) means a field that drops out of the observation simply stops being
    emitted — no stale series linger.
    """

    def __init__(self, poller):
        self.poller = poller

    def collect(self):
        snap = self.poller.get()
        labels = ["station", "station_id"]
        lbl = [snap["station"], snap["station_id"]]
        obs = snap["obs"]

        for key, metric, helptext in OBS_METRICS:
            value = obs.get(key)
            if value is None:
                continue
            fam = GaugeMetricFamily(metric, helptext, labels=labels)
            fam.add_metric(lbl, float(value))
            yield fam

        if snap.get("battery_volts") is not None:
            battery = GaugeMetricFamily(
                "tempest_battery_volts",
                "Tempest station battery voltage (volts). >=2.455 is full-performance "
                "Mode 0; lower voltages throttle wind/sensor cadence (Modes 1-3).",
                labels=labels)
            battery.add_metric(lbl, snap["battery_volts"])
            yield battery

            pct = GaugeMetricFamily(
                "tempest_battery_percent",
                "Estimated battery state-of-charge (percent), interpolated from voltage "
                "with the Tempest LTO curve — matches Home Assistant's battery %.",
                labels=labels)
            pct.add_metric(lbl, battery_soc_percent(snap["battery_volts"]))
            yield pct

        if snap["obs_ts"] is not None:
            obs_ts = GaugeMetricFamily(
                "tempest_last_observation_timestamp_seconds",
                "Unix timestamp of the station's most recent observation.", labels=labels)
            obs_ts.add_metric(lbl, snap["obs_ts"])
            yield obs_ts

        # Freshness is derived at scrape time from the cached observation
        # timestamp, NOT from a boolean frozen during the last poll: if polls
        # start failing, the station must go offline once the cached obs ages
        # past the stale window, even though no new poll has run.
        online = GaugeMetricFamily(
            "tempest_station_online",
            "1 if the station reported an observation within the freshness window, 0 otherwise.",
            labels=labels)
        latest_obs_ts = snap["obs_ts"]
        fresh = latest_obs_ts is not None and (time.time() - latest_obs_ts) <= STALE_SECONDS
        online.add_metric(lbl, 1.0 if fresh else 0.0)
        yield online

        success = GaugeMetricFamily(
            "tempest_scrape_success",
            "1 if the last WeatherFlow cloud poll succeeded, 0 otherwise.")
        success.add_metric([], 1.0 if snap["success"] else 0.0)
        yield success

        scrape_ts = GaugeMetricFamily(
            "tempest_last_scrape_timestamp_seconds",
            "Unix timestamp of the last WeatherFlow cloud poll.")
        scrape_ts.add_metric([], snap["scrape_ts"])
        yield scrape_ts

        errs = CounterMetricFamily(
            "tempest_api_errors_total",
            "Total number of failed WeatherFlow cloud polls since exporter start.")
        errs.add_metric([], snap["errors"])
        yield errs


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s tempest-exporter :: %(message)s",
    )
    if not TOKEN or not STATION_ID:
        log.error("TEMPEST_TOKEN and TEMPEST_STATION_ID must be set")
        sys.exit(1)

    client = TempestClient(TOKEN, STATION_ID, device_id=DEVICE_ID)
    poller = Poller(client)

    # Prime one poll synchronously so the very first scrape already has data.
    # (Best-effort: on failure the poll loop retries every POLL_INTERVAL.)
    poller.poll_once()

    threading.Thread(target=poller.run, daemon=True).start()

    registry = CollectorRegistry()
    registry.register(TempestCollector(poller))
    start_http_server(PORT, registry=registry)
    log.info("Tempest exporter listening on :%d (polling station %s every %ds)",
             PORT, STATION_ID, POLL_INTERVAL)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
