"""Regression tests for the Tempest exporter.

Covers finding 1 (API token must never appear in error logs) and finding 3
(station freshness must be derived at scrape time, so a stalled poller reports
the station offline once the cached observation ages out).
"""
import importlib.util
import os

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
