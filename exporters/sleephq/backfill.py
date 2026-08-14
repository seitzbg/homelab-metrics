#!/usr/bin/env python3
"""One-shot SleepHQ history -> OpenMetrics dump, for promtool TSDB backfill.

Prometheus rejects scraped samples with past timestamps, so a scrape endpoint
can never carry history. The supported path is to render the history as
OpenMetrics text with explicit timestamps, convert it to TSDB blocks with
`promtool tsdb create-blocks-from openmetrics`, and drop those blocks into the
data directory. This script does the first step.

Usage:
    SLEEPHQ_CLIENT_ID=... SLEEPHQ_CLIENT_SECRET=... \
        python3 backfill.py --out sleephq.openmetrics [--skip-recent-days 2]

Then (see the stack README for the containerised version):
    promtool tsdb create-blocks-from openmetrics --max-block-duration=720h \
        sleephq.openmetrics ./blocks

Sample timestamps are pinned to 12:00 UTC of each night's `date`. Midnight
would sit exactly on a day boundary where an off-by-one in either direction
lands the sample in the wrong day; noon is unambiguous under any rounding.

--skip-recent-days exists because promtool's own documentation warns it is not
safe to backfill into the last 3 hours (Prometheus' current head block, which
is actively being mutated). Skipping the most recent couple of days keeps the
generated blocks clear of the head block AND clear of whatever the live
exporter has already recorded, so history and live data never overlap.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from sleephq_common import METRIC_HELP, SleepHQClient, machine_labels, samples_for_night

log = logging.getLogger("sleephq-backfill")

# Labels the LIVE exporter's scrape job stamps onto its series -- `job`/
# `instance` from the scrape target, plus whatever extra labels your
# Prometheus static_config adds. The backfill MUST carry the identical set,
# or historical and live samples become distinct series that never unify: a
# query window covering both then returns two series (one from the block,
# one from the scrape), which is exactly what breaks "last night" stat
# tiles. Keep this in lockstep with your own Prometheus scrape config for
# the sleephq job (see the README).
SCRAPE_LABELS = {
    "job": "sleephq",
    "instance": "sleephq-exporter:9826",
    "service": "sleephq",
    "group": "health",
}


def escape(value):
    """Escape a label value per the OpenMetrics text format."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render(series, out):
    """Write OpenMetrics text. Samples for a metric must be contiguous, and the
    whole exposition must end with '# EOF' or promtool rejects the file."""
    for metric in sorted(series):
        out.write(f"# HELP {metric} {METRIC_HELP.get(metric, 'SleepHQ metric.')}\n")
        out.write(f"# TYPE {metric} gauge\n")
        # Sort by timestamp so each series is monotonic in time — promtool
        # tolerates unsorted input but sorted input compacts far better.
        for labels, value, ts in sorted(series[metric], key=lambda r: (r[2], sorted(r[0].items()))):
            rendered = ",".join(f'{k}="{escape(v)}"' for k, v in sorted(labels.items()))
            out.write(f"{metric}{{{rendered}}} {value} {ts}\n")
    out.write("# EOF\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sleephq.openmetrics", help="output file ('-' for stdout)")
    # 0 means "everything except today", which is the safe maximum -- NOT "no
    # filtering". Samples land at 12:00 UTC, so yesterday's is >=12h old however
    # early this runs, far outside promtool's 3h head-block danger window.
    # Anything higher leaves a permanent hole: the exporter only publishes the
    # LATEST night, so nights inside the skip window are captured by nothing.
    ap.add_argument("--skip-recent-days", type=int, default=0,
                    help="omit the N most recent days beyond today (default 0 = "
                         "emit everything up to and including yesterday)")
    # Gap-fill support. The exporter only ever publishes the LATEST night, so any
    # night that scrolled past while it wasn't running is never captured by it --
    # it has to be backfilled. Use these to emit just the missing days instead of
    # regenerating the whole history and stacking overlapping blocks.
    ap.add_argument("--from-date", metavar="YYYY-MM-DD",
                    help="only emit nights on or after this date (inclusive)")
    ap.add_argument("--to-date", metavar="YYYY-MM-DD",
                    help="only emit nights on or before this date (inclusive)")
    ap.add_argument("--per-page", type=int, default=100)
    args = ap.parse_args()

    def _parse(flag, value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            ap.error(f"{flag} must be YYYY-MM-DD, got {value!r}")

    from_date = _parse("--from-date", args.from_date)
    to_date = _parse("--to-date", args.to_date)
    if from_date and to_date and from_date > to_date:
        ap.error(f"--from-date {from_date} is after --to-date {to_date}")

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s sleephq-backfill :: %(message)s")

    client_id = os.environ.get("SLEEPHQ_CLIENT_ID")
    client_secret = os.environ.get("SLEEPHQ_CLIENT_SECRET")
    if not client_id or not client_secret:
        log.error("SLEEPHQ_CLIENT_ID and SLEEPHQ_CLIENT_SECRET must be set")
        return 1

    client = SleepHQClient(client_id, client_secret)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.skip_recent_days)).date()

    series = {}
    total_nights = skipped = filtered = 0
    emitted_days = set()
    for team in client.teams():
        team_id = (team.get("attributes") or {}).get("id") or team.get("id")
        for machine in client.machines(team_id):
            labels = machine_labels(machine)
            dates = client.machine_dates(labels["machine_id"], per_page=args.per_page)
            log.info("machine %s (%s %s): %d nights",
                     labels["machine_id"], labels["brand"], labels["model"], len(dates))
            for date_str in sorted(dates):
                try:
                    day = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if day >= cutoff:
                    skipped += 1
                    continue
                if (from_date and day < from_date) or (to_date and day > to_date):
                    filtered += 1
                    continue
                emitted_days.add(day)
                ts = int(datetime.combine(
                    day, datetime.min.time(), tzinfo=timezone.utc).timestamp()) + 43200  # 12:00 UTC
                for metric, lbls, value in samples_for_night(dates[date_str], labels):
                    series.setdefault(metric, []).append(({**lbls, **SCRAPE_LABELS}, value, ts))
                total_nights += 1

    if not series:
        log.error("no samples generated — nothing to backfill")
        return 1

    count = sum(len(v) for v in series.values())
    span = (f"{min(emitted_days)}..{max(emitted_days)}" if emitted_days else "none")
    log.info("%d nights -> %d samples across %d metrics "
             "(span %s, skipped %d recent, %d outside --from/--to)",
             total_nights, count, len(series), span, skipped, filtered)

    if args.out == "-":
        render(series, sys.stdout)
    else:
        with open(args.out, "w") as fh:
            render(series, fh)
        log.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
