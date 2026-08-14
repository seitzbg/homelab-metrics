#!/usr/bin/env python3
"""SleepHQ -> Prometheus exporter (most recent night per machine).

SleepHQ is a cloud CPAP reporting platform; there is no local API. This
exporter polls https://sleephq.com/api/v1 for each machine's most recent
machine_date record and exposes it as Prometheus gauges on :EXPORTER_PORT.

Why a background poller instead of collecting on scrape: the underlying data
changes at most once a day (a night's summary is finalised when the machine's
SD data is uploaded), so there is nothing to gain from hitting the API at
Prometheus' scrape cadence — and doing so would burn the 7200s-lived OAuth
token far faster than necessary. Prometheus scrapes the *cache*.

IMPORTANT — this exporter only ever emits the LATEST night. Prometheus refuses
samples with timestamps in the past, so historical nights cannot be published
through a scrape endpoint at all; they are loaded once via backfill.py ->
promtool -> TSDB blocks. See the stack README for that procedure.

Because a night's value is republished until the next night lands, each night
appears in Prometheus as a flat line spanning the following day rather than a
single point. Graph these with a daily step and `last_over_time`, not `rate`.

Metrics (all gauges, labelled machine/machine_id/brand/model):
  sleephq_usage_seconds                    machine-on time (see time_offset caveat)
  sleephq_usage_score                      0-100
  sleephq_ahi{type="total|hypopnea|..."}   events/hour
  sleephq_ahi_score                        0-100
  sleephq_leak_score                       0-100
  sleephq_pressure_cmh2o{stat="av|med|upper|min|max"}
  sleephq_epap_cmh2o{stat=...}
  sleephq_leak_rate_lpm{stat=...}
  sleephq_flow_limit{stat=...}
  sleephq_resp_rate_bpm{stat=...}
  sleephq_spo2_percent{stat=...}           O2 Ring only
  sleephq_pulse_rate_bpm{stat=...}         O2 Ring only
  sleephq_movement{stat=...}               O2 Ring only
  sleephq_machine_setting{setting=...}
  sleephq_machine_settings_info{mode,mask,epr,...}   always 1
  sleephq_night_timestamp_seconds          unix ts of the night being reported
  sleephq_night_age_seconds                how stale that night is (alert on this)
  sleephq_scrape_success                   1/0
  sleephq_last_scrape_timestamp_seconds
  sleephq_api_errors_total                 counter

Config (environment):
  SLEEPHQ_CLIENT_ID       (required)
  SLEEPHQ_CLIENT_SECRET   (required)
  EXPORTER_PORT           default 9826
  POLL_INTERVAL           seconds between API polls, default 21600 (6h)
  SLEEPHQ_HTTP_TIMEOUT    per-request timeout seconds, default 30
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from sleephq_common import METRIC_HELP, SleepHQClient, machine_labels, samples_for_night

log = logging.getLogger("sleephq-exporter")

CLIENT_ID = os.environ.get("SLEEPHQ_CLIENT_ID")
CLIENT_SECRET = os.environ.get("SLEEPHQ_CLIENT_SECRET")
PORT = int(os.environ.get("EXPORTER_PORT", "9826"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "21600"))
HTTP_TIMEOUT = float(os.environ.get("SLEEPHQ_HTTP_TIMEOUT", "30"))

# One page is plenty for "the latest night" — machine_dates comes back with the
# newest records on page 1, and we only keep the max date anyway.
LATEST_PAGE_SIZE = 30


def _date_to_ts(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


class Poller:
    """Owns the API polling loop and the latest snapshot the collector serves."""

    def __init__(self, client):
        self.client = client
        self.lock = threading.Lock()
        self.errors = 0
        self.snapshot = {"samples": [], "nights": {}, "success": False, "scrape_ts": 0.0, "errors": 0}

    def poll_once(self):
        try:
            teams = self.client.teams()
            samples, nights = [], {}
            for team in teams:
                team_id = (team.get("attributes") or {}).get("id") or team.get("id")
                for machine in self.client.machines(team_id):
                    labels = machine_labels(machine)
                    mid = labels["machine_id"]
                    dates = self.client.machine_dates(mid, per_page=LATEST_PAGE_SIZE, max_pages=1)
                    if not dates:
                        log.warning("machine %s (%s) returned no nights", mid, labels["machine"])
                        continue
                    latest = max(dates)
                    samples.extend(samples_for_night(dates[latest], labels))
                    nights[mid] = (latest, labels)
        except Exception as exc:  # network, auth, HTTP, JSON — all non-fatal
            log.exception("poll failed: %s", exc)
            with self.lock:
                self.errors += 1
                self.snapshot = dict(self.snapshot, success=False, scrape_ts=time.time(), errors=self.errors)
            return

        with self.lock:
            self.snapshot = {
                "samples": samples,
                "nights": nights,
                "success": True,
                "scrape_ts": time.time(),
                "errors": self.errors,
            }
        log.info("poll ok: %d machine(s), %d sample(s)", len(nights), len(samples))

    def run(self):
        # Sleep first: main() primes an initial poll synchronously, so looping
        # without a leading sleep would double-poll at startup.
        while True:
            time.sleep(POLL_INTERVAL)
            self.poll_once()

    def get(self):
        with self.lock:
            return dict(self.snapshot)


class SleepHQCollector:
    """Renders the poller's latest snapshot into Prometheus metric families.

    Families are rebuilt on every collect() rather than held as long-lived Gauge
    children, so a machine that disappears from the account stops being emitted
    instead of freezing at its last value.
    """

    def __init__(self, poller):
        self.poller = poller

    def collect(self):
        snap = self.poller.get()

        # Group samples by (metric, label-key-set): Prometheus requires every
        # series in a family to carry an identical label set, and the summary
        # metrics add a "stat" label that the scalar ones don't.
        families = {}
        for metric, labels, value in snap["samples"]:
            key = (metric, tuple(sorted(labels)))
            fam = families.get(key)
            if fam is None:
                fam = GaugeMetricFamily(
                    metric,
                    METRIC_HELP.get(metric, "SleepHQ metric."),
                    labels=sorted(labels),
                )
                families[key] = fam
            fam.add_metric([str(labels[k]) for k in sorted(labels)], value)
        yield from families.values()

        night_ts = GaugeMetricFamily(
            "sleephq_night_timestamp_seconds",
            "Unix timestamp (UTC midnight) of the night currently being reported.",
            labels=["machine", "machine_id"])
        night_age = GaugeMetricFamily(
            "sleephq_night_age_seconds",
            "Age of the most recent night's data. Alert on this to catch a stalled upload.",
            labels=["machine", "machine_id"])
        now = time.time()
        for mid, (date_str, labels) in snap["nights"].items():
            ts = _date_to_ts(date_str)
            if ts is None:
                continue
            night_ts.add_metric([labels["machine"], mid], ts)
            night_age.add_metric([labels["machine"], mid], max(0.0, now - ts))
        yield from (night_ts, night_age)

        success = GaugeMetricFamily(
            "sleephq_scrape_success", "1 if the last SleepHQ API poll succeeded, 0 otherwise.")
        success.add_metric([], 1.0 if snap["success"] else 0.0)
        yield success

        scrape_ts = GaugeMetricFamily(
            "sleephq_last_scrape_timestamp_seconds", "Unix timestamp of the last SleepHQ API poll.")
        scrape_ts.add_metric([], snap["scrape_ts"])
        yield scrape_ts

        errs = CounterMetricFamily(
            "sleephq_api_errors_total", "Total failed SleepHQ API polls since exporter start.")
        errs.add_metric([], snap["errors"])
        yield errs


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s sleephq-exporter :: %(message)s",
    )
    if not CLIENT_ID or not CLIENT_SECRET:
        log.error("SLEEPHQ_CLIENT_ID and SLEEPHQ_CLIENT_SECRET must be set")
        sys.exit(1)

    client = SleepHQClient(CLIENT_ID, CLIENT_SECRET, timeout=HTTP_TIMEOUT)
    poller = Poller(client)

    # Prime one poll synchronously so the very first scrape already has data.
    # Best-effort: on failure the loop retries every POLL_INTERVAL.
    poller.poll_once()

    threading.Thread(target=poller.run, daemon=True).start()

    registry = CollectorRegistry()
    registry.register(SleepHQCollector(poller))
    start_http_server(PORT, registry=registry)
    log.info("SleepHQ exporter listening on :%d (polling every %ds)", PORT, POLL_INTERVAL)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
