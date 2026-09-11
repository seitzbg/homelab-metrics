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


def _snapshot(last_seen_ts, sensor=None):
    return {
        "sensors": ({} if sensor is None else sensor),
        "gateways": {"gw1": {"last_seen_ts": last_seen_ts}},
        "success": True, "scrape_ts": last_seen_ts or 0.0, "errors": 0,
    }


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

def test_temperature_and_dewpoint_exported_unchanged():
    sensor = {"abc": {
        "name": "office", "active": True, "battery": None, "rssi": None,
        "temperature_f": 68.0, "humidity": 40.0, "dewpoint_f": 42.0,
        "observed_ts": None,
    }}
    poller = sp.Poller(object())
    poller.snapshot = _snapshot(last_seen_ts=1000.0, sensor=sensor)
    assert _family(poller, "sensorpush_temperature_fahrenheit").samples[0].value == 68.0
    assert _family(poller, "sensorpush_dewpoint_fahrenheit").samples[0].value == 42.0
