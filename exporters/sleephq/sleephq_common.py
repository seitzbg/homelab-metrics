"""Shared SleepHQ API client + machine_date -> metric-sample mapping.

Imported by both exporter.py (live scrape of the most recent night) and
backfill.py (one-shot OpenMetrics dump of the full history). Keeping the
mapping in one place means the backfilled blocks and the live series carry
identical metric names and labels — otherwise the seam between history and
"from now on" would show up as two disjoint series in Grafana.

Four SleepHQ API quirks are encoded here; all four were confirmed against a
live account and each one fails in a way that does NOT look like its cause:

  1. grant_type MUST be "password". "client_credentials" returns HTTP 200 with
     a perfectly valid-looking access_token, but every subsequent API call then
     fails HTTP 500 "undefined method 'anonymous?' for nil" — the token has no
     resource owner bound to it. No username/password is needed; the client
     id/secret pair is itself user-scoped.
  2. Cloudflare 403s the default Python-urllib User-Agent. requests' own UA
     passes, but we set an explicit one so a future dependency swap can't
     silently reintroduce a 403 that reads like rate-limiting.
  3. Pagination is per_page + page. The JSON:API-style page[size] is accepted
     and silently returns ZERO records — an empty success, not an error, so a
     naive client concludes the account has no data.
  4. attributes.usage is a STRING of seconds, not an integer (the OpenAPI spec
     does type it "string", but every summary sibling is numeric, so it reads
     like a typo). Everything numeric goes through _num().
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("sleephq")

API_BASE = "https://sleephq.com/api/v1"
TOKEN_URL = "https://sleephq.com/oauth/token"
USER_AGENT = "homelab-metrics-sleephq-exporter/1.0"

# Percentile-style summaries all share the same inner shape. NOTE: "upper" is
# the 95th percentile — there is no p95/percentile_95 key, which is the naming
# most third-party clients wrongly assume.
STAT_KEYS = ("av", "med", "upper", "min", "max")

# summary field -> (metric name, help text). Each emits one series per stat key.
SUMMARY_METRICS = {
    "pressure_summary": ("sleephq_pressure_cmh2o", "CPAP delivered pressure in cmH2O."),
    "epap_summary": ("sleephq_epap_cmh2o", "Expiratory positive airway pressure in cmH2O."),
    "leak_rate_summary": ("sleephq_leak_rate_lpm", "Mask leak rate in litres/minute."),
    "flow_limit_summary": ("sleephq_flow_limit", "Flow limitation index (0-1, unitless)."),
    "resp_rate_summary": ("sleephq_resp_rate_bpm", "Respiratory rate in breaths/minute."),
    "spo2_summary": ("sleephq_spo2_percent", "Blood oxygen saturation in percent."),
    "pulse_rate_summary": ("sleephq_pulse_rate_bpm", "Pulse rate in beats/minute."),
    "movement_summary": ("sleephq_movement", "Movement/restlessness index (unitless)."),
}

# ahi_summary components. These are per-hour indices (events/hour), NOT raw
# event counts — an AHI of 0.94 means ~1 event per hour, not 1 event all night.
AHI_TYPES = (
    "total",
    "hypopnea",
    "all_apnea",
    "clear_airway",
    "obstructive_apnea",
    "unidentified_apnea",
)

# Numeric machine_settings worth trending. The rest are strings and go out as
# labels on the sleephq_machine_settings_info series instead.
SETTING_NUMERIC = ("pressure_min", "pressure_max", "epr_level", "humidity_level", "ramp_pressure")
SETTING_LABELS = ("mode", "mask", "epr", "ramp", "response", "climate_control", "smart_start")


def _num(value):
    """Coerce an API value to float, tolerating the string-typed `usage` field."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SleepHQClient:
    """Thin SleepHQ API client: password-grant OAuth2 + paginated GETs."""

    def __init__(self, client_id, client_secret, timeout=30):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self._token = None

    def authenticate(self):
        r = self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                # See quirk (1) — client_credentials yields a userless token.
                "grant_type": "password",
                "scope": "read",
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        token = (r.json() or {}).get("access_token")
        if not token:
            raise RuntimeError("no access_token in /oauth/token response")
        self._token = token
        self.session.headers["Authorization"] = f"Bearer {token}"
        log.info("authenticated with SleepHQ (token cached)")

    def _get(self, path, params=None):
        """GET with one transparent re-auth. Tokens live 7200s and the refresh
        token SleepHQ hands back is documented as non-functional, so the only
        supported renewal is minting a fresh one on 401."""
        if self._token is None:
            self.authenticate()
        url = f"{API_BASE}{path}"
        r = self.session.get(url, params=params, timeout=self.timeout)
        if r.status_code == 401:
            log.warning("401 on %s — re-authenticating", path)
            self.authenticate()
            r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def teams(self):
        return self._get("/teams").get("data", []) or []

    def machines(self, team_id):
        return self._get(f"/teams/{team_id}/machines").get("data", []) or []

    def machine_dates(self, machine_id, per_page=100, max_pages=60):
        """Return every machine_date for a machine, newest-first order not guaranteed.

        Paginates with per_page/page — see quirk (3). per_page has no observed
        server-side cap, but we page anyway so a multi-year account doesn't rely
        on one enormous response.
        """
        out = {}
        for page in range(1, max_pages + 1):
            data = self._get(
                f"/machines/{machine_id}/machine_dates",
                params={"per_page": per_page, "page": page},
            ).get("data", []) or []
            if not data:
                break
            for rec in data:
                attrs = rec.get("attributes") or {}
                if attrs.get("date"):
                    out[attrs["date"]] = attrs
        return out


def machine_labels(machine):
    """Extract the stable identity labels for a machine resource."""
    attrs = machine.get("attributes") or {}
    return {
        "machine": attrs.get("name") or str(attrs.get("id")),
        "machine_id": str(attrs.get("id")),
        "brand": attrs.get("brand") or "",
        "model": attrs.get("model") or "",
    }


def samples_for_night(attrs, base_labels):
    """Map one machine_date record to [(metric, labels, value), ...].

    Fields absent for a given device type are simply skipped: the AirSense
    record carries no spo2/pulse/movement keys and the O2 Ring carries no
    ahi/pressure/leak keys, so every caller must tolerate a partial field set
    rather than assuming one canonical record shape.
    """
    out = []

    def emit(metric, value, **extra):
        value = _num(value)
        if value is None:
            return
        labels = dict(base_labels)
        labels.update(extra)
        out.append((metric, labels, value))

    # Machine-on time. NOT necessarily "hours slept" — see the time_offset
    # caveat in the stack README before presenting this as sleep duration.
    emit("sleephq_usage_seconds", attrs.get("usage"))

    usage_summary = attrs.get("usage_summary") or {}
    emit("sleephq_usage_score", usage_summary.get("score"))

    ahi = attrs.get("ahi_summary") or {}
    for kind in AHI_TYPES:
        emit("sleephq_ahi", ahi.get(kind), type=kind)
    emit("sleephq_ahi_score", ahi.get("score"))

    leak = attrs.get("leak_rate_summary") or {}
    emit("sleephq_leak_score", leak.get("score"))

    for field, (metric, _help) in SUMMARY_METRICS.items():
        summary = attrs.get(field) or {}
        for stat in STAT_KEYS:
            emit(metric, summary.get(stat), stat=stat)

    settings = attrs.get("machine_settings") or {}
    for key in SETTING_NUMERIC:
        emit("sleephq_machine_setting", settings.get(key), setting=key)
    if settings:
        info_labels = dict(base_labels)
        for key in SETTING_LABELS:
            info_labels[key] = str(settings.get(key) or "")
        out.append(("sleephq_machine_settings_info", info_labels, 1.0))

    return out


METRIC_HELP = {
    "sleephq_usage_seconds": "Machine-on time for the night, in seconds.",
    "sleephq_usage_score": "SleepHQ usage/adherence score for the night (0-100).",
    "sleephq_ahi": "Apnea-Hypopnea Index component, in events per hour.",
    "sleephq_ahi_score": "SleepHQ AHI score for the night (0-100).",
    "sleephq_leak_score": "SleepHQ mask-leak score for the night (0-100).",
    "sleephq_machine_setting": "Numeric machine setting in effect for the night.",
    "sleephq_machine_settings_info": "Non-numeric machine settings in effect for the night.",
}
METRIC_HELP.update({m: h for m, h in SUMMARY_METRICS.values()})
