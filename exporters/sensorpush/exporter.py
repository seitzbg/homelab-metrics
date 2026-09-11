#!/usr/bin/env python3
"""
SensorPush -> Prometheus exporter.

The SensorPush G1 WiFi Gateway has no local API — sensor readings are only
available from the SensorPush Gateway Cloud API (https://api.sensorpush.com).
This exporter signs in with a SensorPush account (OAuth2, email+password),
polls the cloud once per POLL_INTERVAL and caches the latest per-sensor
readings, then serves them as Prometheus metrics on :EXPORTER_PORT.

Why a background poller instead of collecting on scrape: the SensorPush API is
rate-limited to ~1 request/minute and the gateway only relays new samples about
once a minute, so there's nothing to gain from scraping faster. Prometheus
scrapes the *cache* — scrape cadence is decoupled from the API rate limit, and
a burst of scrapes can never hammer (or get us throttled by) the cloud API.

Metrics (all gauges unless noted, labeled by sensor name + id):
  sensorpush_temperature_fahrenheit{sensor,sensor_id}
  sensorpush_humidity_percent{sensor,sensor_id}
  sensorpush_dewpoint_fahrenheit{sensor,sensor_id}
  sensorpush_battery_volts{sensor,sensor_id}
  sensorpush_signal_rssi_dbm{sensor,sensor_id}
  sensorpush_sensor_active{sensor,sensor_id}                  1/0
  sensorpush_last_sample_timestamp_seconds{sensor,sensor_id}
  sensorpush_gateway_last_seen_timestamp_seconds{gateway}
  sensorpush_gateway_active{gateway}                          1/0
  sensorpush_scrape_success                                   1/0 (last poll)
  sensorpush_last_scrape_timestamp_seconds
  sensorpush_api_errors_total                                 counter

The SensorPush Gateway Cloud API always reports temperature and dewpoint in
degrees Fahrenheit, regardless of the account's app display preference — the
published API schema pins Sample.temperature and Sample.dewpoint to Fahrenheit
(https://api.sensorpush.com/api/v1/support/swagger/swagger-v1.json). This
exporter therefore exports those values unchanged; there is no source-unit
setting to configure.

Config (environment):
  SENSORPUSH_EMAIL                (required)
  SENSORPUSH_PASSWORD             (required)
  EXPORTER_PORT                   default 9825
  POLL_INTERVAL                   seconds between cloud polls, default 60
  SENSORPUSH_GATEWAY_STALE_SECONDS  mark gateway inactive if last_seen older, default 900
  SENSORPUSH_API_BASE             default https://api.sensorpush.com/api/v1
  SENSORPUSH_HTTP_TIMEOUT         per-request timeout seconds, default 20
  SENSORPUSH_TOKEN_TTL            proactive re-auth interval seconds, default 28800 (8h)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

import requests
from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

log = logging.getLogger("sensorpush-exporter")

API_BASE = os.environ.get("SENSORPUSH_API_BASE", "https://api.sensorpush.com/api/v1").rstrip("/")
EMAIL = os.environ.get("SENSORPUSH_EMAIL")
PASSWORD = os.environ.get("SENSORPUSH_PASSWORD")
PORT = int(os.environ.get("EXPORTER_PORT", "9825"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
# The G1 gateway records a cloud check-in only every few minutes (observed
# ~3 min), so a tight window flaps active<->inactive. 15 min = genuinely down.
GATEWAY_STALE_SECONDS = int(os.environ.get("SENSORPUSH_GATEWAY_STALE_SECONDS", "900"))
HTTP_TIMEOUT = float(os.environ.get("SENSORPUSH_HTTP_TIMEOUT", "20"))
TOKEN_TTL = int(os.environ.get("SENSORPUSH_TOKEN_TTL", "28800"))  # access token is valid ~24h


def _parse_ts(value):
    """Parse a SensorPush ISO-8601 UTC timestamp into a unix epoch float."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


class SensorPushClient:
    """Thin client for the SensorPush Gateway Cloud API (OAuth2 + POST endpoints)."""

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self._token = None
        self._token_ts = 0.0

    def _authenticate(self):
        # Step 1: exchange email+password for a short-lived authorization code.
        r = self.session.post(
            f"{API_BASE}/oauth/authorize",
            json={"email": self.email, "password": self.password},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        code = (r.json() or {}).get("authorization")
        if not code:
            raise RuntimeError("no authorization code in /oauth/authorize response")
        # Step 2: exchange the code for an access token.
        r = self.session.post(
            f"{API_BASE}/oauth/accesstoken",
            json={"authorization": code},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        token = (r.json() or {}).get("accesstoken")
        if not token:
            raise RuntimeError("no accesstoken in /oauth/accesstoken response")
        self._token = token
        self._token_ts = time.time()
        log.info("authenticated with SensorPush cloud (access token cached)")

    def _headers(self):
        # SensorPush expects the raw access token as the Authorization header
        # value (no "Bearer " prefix).
        return {"Authorization": self._token, "Accept": "application/json"}

    def _post(self, path, body):
        if self._token is None or (time.time() - self._token_ts) > TOKEN_TTL:
            self._authenticate()
        url = f"{API_BASE}{path}"
        r = self.session.post(url, json=body, headers=self._headers(), timeout=HTTP_TIMEOUT)
        if r.status_code in (401, 403):
            log.warning("auth rejected (%s) on %s — re-authenticating", r.status_code, path)
            self._authenticate()
            r = self.session.post(url, json=body, headers=self._headers(), timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def sensors(self):
        return self._post("/devices/sensors", {}) or {}

    def gateways(self):
        return self._post("/devices/gateways", {}) or {}

    def samples(self, limit=1):
        return self._post("/samples", {"limit": limit}) or {}


class Poller:
    """Owns the cloud-polling loop and the latest snapshot the collector serves."""

    def __init__(self, client):
        self.client = client
        self.lock = threading.Lock()
        self.errors = 0
        self.snapshot = {
            "sensors": {},
            "gateways": {},
            "success": False,
            "scrape_ts": 0.0,
            "errors": 0,
        }

    def poll_once(self):
        try:
            sensors = self.client.sensors()
            gateways = self.client.gateways()
            samples = self.client.samples(limit=1)
        except Exception as exc:  # network, auth, HTTP, JSON — all non-fatal
            log.exception("poll failed: %s", exc)
            with self.lock:
                self.errors += 1
                self.snapshot["success"] = False
                self.snapshot["scrape_ts"] = time.time()
                self.snapshot["errors"] = self.errors
            return

        sample_map = (samples or {}).get("sensors", {}) or {}
        now = time.time()

        sensor_out = {}
        for sid, meta in (sensors or {}).items():
            meta = meta or {}
            latest = None
            slist = sample_map.get(sid) or []
            if slist:
                latest = slist[0] or {}
            sensor_out[sid] = {
                "name": meta.get("name") or sid,
                "active": bool(meta.get("active", True)),
                "battery": meta.get("battery_voltage"),
                "rssi": meta.get("rssi"),
                "temperature_f": latest.get("temperature") if latest else None,
                "humidity": latest.get("humidity") if latest else None,
                "dewpoint_f": latest.get("dewpoint") if latest else None,
                "observed_ts": _parse_ts(latest.get("observed")) if latest else None,
            }

        gw_out = {}
        for gname, gmeta in (gateways or {}).items():
            gmeta = gmeta or {}
            # Store only the raw check-in timestamp; freshness (gateway_active) is
            # derived at scrape time so a stalled poll cannot keep a gateway
            # "active" indefinitely (see collect()).
            gw_out[gname] = {"last_seen_ts": _parse_ts(gmeta.get("last_seen"))}

        with self.lock:
            self.snapshot = {
                "sensors": sensor_out,
                "gateways": gw_out,
                "success": True,
                "scrape_ts": now,
                "errors": self.errors,
            }
        log.info("poll ok: %d sensor(s), %d gateway(s)", len(sensor_out), len(gw_out))

    def run(self):
        # Sleep first: main() primes an initial poll synchronously, so looping
        # without a leading sleep would double-poll at startup and risk the
        # 1-request/minute API throttle.
        while True:
            time.sleep(POLL_INTERVAL)
            self.poll_once()

    def get(self):
        with self.lock:
            return {
                "sensors": dict(self.snapshot["sensors"]),
                "gateways": dict(self.snapshot["gateways"]),
                "success": self.snapshot["success"],
                "scrape_ts": self.snapshot["scrape_ts"],
                "errors": self.snapshot["errors"],
            }


class SensorPushCollector:
    """Renders the poller's latest snapshot into Prometheus metric families.

    Building fresh families each collect() (rather than holding long-lived Gauge
    children) means a sensor that drops out of the API response simply stops
    being emitted — no stale series linger.
    """

    def __init__(self, poller):
        self.poller = poller

    def collect(self):
        snap = self.poller.get()
        labels = ["sensor", "sensor_id"]

        temp = GaugeMetricFamily(
            "sensorpush_temperature_fahrenheit",
            "Latest sensor temperature in degrees Fahrenheit.", labels=labels)
        hum = GaugeMetricFamily(
            "sensorpush_humidity_percent",
            "Latest sensor relative humidity in percent.", labels=labels)
        dew = GaugeMetricFamily(
            "sensorpush_dewpoint_fahrenheit",
            "Latest sensor dewpoint in degrees Fahrenheit.", labels=labels)
        bat = GaugeMetricFamily(
            "sensorpush_battery_volts",
            "Sensor battery voltage.", labels=labels)
        rssi = GaugeMetricFamily(
            "sensorpush_signal_rssi_dbm",
            "Sensor radio signal strength (RSSI) in dBm.", labels=labels)
        active = GaugeMetricFamily(
            "sensorpush_sensor_active",
            "1 if the sensor is active, 0 otherwise.", labels=labels)
        last_sample = GaugeMetricFamily(
            "sensorpush_last_sample_timestamp_seconds",
            "Unix timestamp of the sensor's most recent sample.", labels=labels)

        for sid, e in snap["sensors"].items():
            lbl = [e["name"], sid]
            if e["temperature_f"] is not None:
                temp.add_metric(lbl, e["temperature_f"])
            if e["humidity"] is not None:
                hum.add_metric(lbl, e["humidity"])
            if e["dewpoint_f"] is not None:
                dew.add_metric(lbl, e["dewpoint_f"])
            if e["battery"] is not None:
                bat.add_metric(lbl, e["battery"])
            if e["rssi"] is not None:
                rssi.add_metric(lbl, e["rssi"])
            active.add_metric(lbl, 1.0 if e["active"] else 0.0)
            if e["observed_ts"] is not None:
                last_sample.add_metric(lbl, e["observed_ts"])

        yield from (temp, hum, dew, bat, rssi, active, last_sample)

        gw_seen = GaugeMetricFamily(
            "sensorpush_gateway_last_seen_timestamp_seconds",
            "Unix timestamp the gateway was last seen by the SensorPush cloud.",
            labels=["gateway"])
        gw_active = GaugeMetricFamily(
            "sensorpush_gateway_active",
            "1 if the gateway checked in within the freshness window, 0 otherwise.",
            labels=["gateway"])
        # Derive freshness from the cached check-in timestamp vs. the current
        # time, NOT from a boolean frozen during the last successful poll: once
        # the cached last_seen ages past the stale window the gateway must read
        # inactive, even while polls are failing and last_seen stops advancing.
        now = time.time()
        for gname, g in snap["gateways"].items():
            last_seen = g["last_seen_ts"]
            if last_seen is not None:
                gw_seen.add_metric([gname], last_seen)
            active = last_seen is not None and (now - last_seen) <= GATEWAY_STALE_SECONDS
            gw_active.add_metric([gname], 1.0 if active else 0.0)
        yield from (gw_seen, gw_active)

        success = GaugeMetricFamily(
            "sensorpush_scrape_success",
            "1 if the last SensorPush cloud poll succeeded, 0 otherwise.")
        success.add_metric([], 1.0 if snap["success"] else 0.0)
        yield success

        scrape_ts = GaugeMetricFamily(
            "sensorpush_last_scrape_timestamp_seconds",
            "Unix timestamp of the last SensorPush cloud poll.")
        scrape_ts.add_metric([], snap["scrape_ts"])
        yield scrape_ts

        errs = CounterMetricFamily(
            "sensorpush_api_errors_total",
            "Total number of failed SensorPush cloud polls since exporter start.")
        errs.add_metric([], snap["errors"])
        yield errs


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s sensorpush-exporter :: %(message)s",
    )
    if not EMAIL or not PASSWORD:
        log.error("SENSORPUSH_EMAIL and SENSORPUSH_PASSWORD must be set")
        sys.exit(1)
    # Migration guard: earlier versions offered SENSORPUSH_SOURCE_TEMP_UNIT and
    # (incorrectly) converted API values as if they followed the account's
    # display unit. The API always returns Fahrenheit, so the setting is gone.
    if os.environ.get("SENSORPUSH_SOURCE_TEMP_UNIT"):
        log.warning(
            "SENSORPUSH_SOURCE_TEMP_UNIT is obsolete and ignored — the SensorPush "
            "API always returns Fahrenheit. Remove it from your environment. If you "
            "had set it to 'c', past exported temperatures were wrong (double-converted); "
            "only data recorded after this upgrade is correct.")

    client = SensorPushClient(EMAIL, PASSWORD)
    poller = Poller(client)

    # Prime one poll synchronously so the very first scrape already has data.
    # (Best-effort: on failure the poll loop retries every POLL_INTERVAL.)
    poller.poll_once()

    threading.Thread(target=poller.run, daemon=True).start()

    registry = CollectorRegistry()
    registry.register(SensorPushCollector(poller))
    start_http_server(PORT, registry=registry)
    log.info("SensorPush exporter listening on :%d (polling every %ds)", PORT, POLL_INTERVAL)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
