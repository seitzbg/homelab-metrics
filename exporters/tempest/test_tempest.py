"""Regression tests for the Tempest exporter.

Covers: the API token never appearing in error logs; station freshness derived
at scrape time, so a stalled poller reports the station offline once the cached
observation ages out; a successful poll completing; and an offline station not
serving a frozen battery reading.
"""
import importlib.util
import os
import time

import pytest

pytest.importorskip("prometheus_client")
pytest.importorskip("requests")
import requests  # noqa: E402  (guarded by importorskip above)

# Load the sibling exporter.py under a unique module name so this test does not
# collide with the other exporters' `exporter` modules when pytest runs them all.
_spec = importlib.util.spec_from_file_location(
    "tempest_exporter", os.path.join(os.path.dirname(__file__), "exporter.py"))
tempest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tempest)


class _FakeClient:
    station_id = "230296"


def _snapshot(obs_ts):
    return {
        "station": "home", "station_id": "230296", "obs": {},
        "obs_ts": obs_ts, "battery_volts": None,
        "success": True, "scrape_ts": obs_ts or 0.0, "errors": 0,
    }


def _metrics(poller):
    return {fam.name: fam.samples[0].value
            for fam in tempest.TempestCollector(poller).collect()}


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class _WeatherFlowAPI:
    """Stands in for the requests.Session the client talks to WeatherFlow with.

    obs_ts is the station observation epoch (None = the station endpoint
    returns no observations, as it does once the station is offline);
    battery is a (volts, obs epoch) device reading, or an exception to raise;
    outage, when set, is raised by every request (the API unreachable).
    """

    def __init__(self, obs_ts, battery):
        self.obs_ts = obs_ts
        self.battery = battery
        self.outage = None

    def get(self, url, params=None, timeout=None):
        if self.outage is not None:
            raise self.outage
        if "/observations/station/" in url:
            obs = [] if self.obs_ts is None else [
                {"timestamp": self.obs_ts, "air_temperature": 12.5}]
            return _Response({"station_id": 230296, "public_name": "home", "obs": obs})
        if "/stations/" in url:
            return _Response({"stations": [{"devices": [
                {"device_type": "ST", "device_id": 7}]}]})
        if isinstance(self.battery, Exception):
            raise self.battery
        volts, epoch = self.battery
        row = [epoch] + [0] * (tempest.BATTERY_OBS_INDEX - 1) + [volts]
        return _Response({"obs": [row]})


def _poller(api):
    client = tempest.TempestClient("token", "230296")
    client.session = api
    return tempest.Poller(client)


def _online_value(poller):
    for fam in tempest.TempestCollector(poller).collect():
        if fam.name == "tempest_station_online":
            return fam.samples[0].value
    raise AssertionError("tempest_station_online was not emitted")


# ---- Finding 1: sanitized error summaries never leak the token ----

def test_error_summary_drops_token_from_http_error():
    resp = requests.Response()
    resp.status_code = 500
    exc = requests.exceptions.HTTPError(
        "500 Server Error for url: "
        "https://swd.weatherflow.com/swd/rest/observations/station/1?token=SECRET",
        response=resp)
    summary = tempest._error_summary(exc)
    assert "SECRET" not in summary
    assert "token" not in summary
    assert "500" in summary            # status still observable


def test_error_summary_drops_url_from_connection_error():
    exc = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='swd.weatherflow.com'): Max retries exceeded "
        "with url: /swd/rest/observations/station/1?token=SECRET")
    summary = tempest._error_summary(exc)
    assert "SECRET" not in summary
    assert summary == "ConnectionError"


# ---- Finding 3: freshness derived from the cached obs timestamp vs. now ----

def test_station_goes_offline_when_cached_obs_ages_past_stale_window(monkeypatch):
    poller = tempest.Poller(_FakeClient())
    poller.snapshot = _snapshot(obs_ts=1000.0)          # last good obs at t=1000
    # No further successful polls; the clock advances an hour past the window.
    monkeypatch.setattr(tempest.time, "time",
                        lambda: 1000.0 + tempest.STALE_SECONDS + 3600)
    assert _online_value(poller) == 0.0


def test_station_online_while_cached_obs_is_fresh(monkeypatch):
    poller = tempest.Poller(_FakeClient())
    poller.snapshot = _snapshot(obs_ts=1000.0)
    monkeypatch.setattr(tempest.time, "time", lambda: 1000.0 + 10)
    assert _online_value(poller) == 1.0


def test_fresh_poll_restores_online(monkeypatch):
    poller = tempest.Poller(_FakeClient())
    poller.snapshot = _snapshot(obs_ts=1000.0)
    stale_now = 1000.0 + tempest.STALE_SECONDS + 3600
    monkeypatch.setattr(tempest.time, "time", lambda: stale_now)
    assert _online_value(poller) == 0.0
    # A subsequent successful poll caches a fresh observation timestamp.
    poller.snapshot = _snapshot(obs_ts=stale_now)
    assert _online_value(poller) == 1.0


# ---- Successful polls and battery freshness, through the HTTP seam ----

def test_successful_poll_serves_observation_and_battery():
    now = time.time()
    poller = _poller(_WeatherFlowAPI(obs_ts=now, battery=(2.59, now)))
    poller.poll_once()
    metrics = _metrics(poller)
    assert metrics["tempest_air_temperature_celsius"] == 12.5
    assert metrics["tempest_battery_volts"] == 2.59
    assert "tempest_battery_percent" in metrics
    assert metrics["tempest_station_online"] == 1.0


def test_offline_station_does_not_export_a_frozen_battery():
    # The station endpoint returns no observations once the station is offline,
    # but the device endpoint keeps handing back its last reading, however old.
    nine_days_ago = time.time() - 9 * 86400
    poller = _poller(_WeatherFlowAPI(obs_ts=None, battery=(2.59, nine_days_ago)))
    poller.poll_once()
    metrics = _metrics(poller)
    assert "tempest_battery_volts" not in metrics
    assert "tempest_battery_percent" not in metrics
    assert metrics["tempest_station_online"] == 0.0
    assert metrics["tempest_scrape_success"] == 1.0


def test_battery_ages_out_when_polls_keep_failing(monkeypatch):
    now = time.time()
    api = _WeatherFlowAPI(obs_ts=now, battery=(2.55, now))
    poller = _poller(api)
    poller.poll_once()
    api.outage = TimeoutError("read timed out")
    poller.poll_once()
    monkeypatch.setattr(tempest.time, "time",
                        lambda: now + tempest.STALE_SECONDS + 60)
    metrics = _metrics(poller)
    assert "tempest_battery_volts" not in metrics
    assert metrics["tempest_scrape_success"] == 0.0


def test_failed_battery_poll_keeps_the_last_fresh_reading():
    now = time.time()
    api = _WeatherFlowAPI(obs_ts=now, battery=(2.55, now))
    poller = _poller(api)
    poller.poll_once()
    api.battery = RuntimeError("device obs 502")
    poller.poll_once()
    assert _metrics(poller)["tempest_battery_volts"] == 2.55
