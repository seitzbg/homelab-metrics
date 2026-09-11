"""Regression tests for the SensorPush exporter.

Covers finding 3 (gateway freshness derived at scrape time) and finding 5 (the
API is always Fahrenheit, so no source-unit conversion is applied).
"""
import importlib.util
import os

import pytest

pytest.importorskip("prometheus_client")

_spec = importlib.util.spec_from_file_location(
    "sensorpush_exporter", os.path.join(os.path.dirname(__file__), "exporter.py"))
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


def _snapshot(last_seen_ts):
    return {
        "sensors": {},
        "gateways": {"gw1": {"last_seen_ts": last_seen_ts}},
        "success": True, "scrape_ts": last_seen_ts or 0.0, "errors": 0,
    }


class _FakeClient:
    """Returns SensorPush API-format payloads (temperature/dewpoint in °F)."""

    def sensors(self):
        return {"abc": {"name": "office", "active": True,
                        "battery_voltage": 3.0, "rssi": -55}}

    def gateways(self):
        return {"gw1": {"last_seen": "2026-09-11T12:00:00.000Z"}}

    def samples(self, limit=1):
        return {"sensors": {"abc": [{
            "temperature": 68.0, "dewpoint": 42.0, "humidity": 40.0,
            "observed": "2026-09-11T12:00:00.000Z"}]}}


def _family(poller, name):
    for fam in sp.SensorPushCollector(poller).collect():
        if fam.name == name:
            return fam
    raise AssertionError(f"{name} was not emitted")


# ---- Finding 3: gateway freshness derived from last_seen vs. now ----

def test_gateway_inactive_when_last_seen_ages_past_stale_window(monkeypatch):
    poller = sp.Poller(object())
    poller.snapshot = _snapshot(last_seen_ts=1000.0)
    monkeypatch.setattr(sp.time, "time",
                        lambda: 1000.0 + sp.GATEWAY_STALE_SECONDS + 3600)
    assert _family(poller, "sensorpush_gateway_active").samples[0].value == 0.0


def test_gateway_active_while_fresh(monkeypatch):
    poller = sp.Poller(object())
    poller.snapshot = _snapshot(last_seen_ts=1000.0)
    monkeypatch.setattr(sp.time, "time", lambda: 1000.0 + 10)
    assert _family(poller, "sensorpush_gateway_active").samples[0].value == 1.0


def test_fresh_checkin_restores_active(monkeypatch):
    poller = sp.Poller(object())
    poller.snapshot = _snapshot(last_seen_ts=1000.0)
    stale_now = 1000.0 + sp.GATEWAY_STALE_SECONDS + 3600
    monkeypatch.setattr(sp.time, "time", lambda: stale_now)
    assert _family(poller, "sensorpush_gateway_active").samples[0].value == 0.0
    poller.snapshot = _snapshot(last_seen_ts=stale_now)
    assert _family(poller, "sensorpush_gateway_active").samples[0].value == 1.0


# ---- Finding 5: API temperatures are Fahrenheit and exported unchanged ----

def test_api_fahrenheit_values_pass_through_poll_once():
    # Exercise the real API-to-snapshot path: poll_once() must NOT re-scale the
    # already-Fahrenheit temperature/dewpoint the API returns (a 68 reading must
    # stay 68, not become 154.4). Driving poll_once() — not an injected snapshot
    # — is what actually guards the removed Celsius conversion.
    poller = sp.Poller(_FakeClient())
    poller.poll_once()
    assert _family(poller, "sensorpush_temperature_fahrenheit").samples[0].value == 68.0
    assert _family(poller, "sensorpush_dewpoint_fahrenheit").samples[0].value == 42.0
