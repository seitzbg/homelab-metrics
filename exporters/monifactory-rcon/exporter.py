#!/usr/bin/env python3
"""Minecraft (Forge) RCON -> Prometheus exporter for the Monifactory server.

Pulls server-internal stats that the external mc-monitor server-list ping
cannot see, purely over RCON (no server-side mod, no restart):

  * `forge tps`         -> mean TPS + mean tick time (MSPT), per dimension + overall
  * `forge entity list` -> total loaded entities + per-type counts

Pure Python stdlib (Source RCON protocol implemented inline) so it runs in a
plain `python:slim` container with no build step and no pip deps. A background
thread polls RCON every SCRAPE_INTERVAL seconds and caches the rendered
Prometheus text; the HTTP handler just serves the cache, so Prometheus scrape
latency is decoupled from RCON and RCON is hit at a fixed cadence.

Env:
  RCON_HOST        game server host       (default: monifactory)
  RCON_PORT        RCON port              (default: 25575)
  RCON_PASSWORD    RCON password          (required)
  BIND_PORT        HTTP /metrics port     (default: 8000)
  SCRAPE_INTERVAL  RCON poll seconds      (default: 30)
"""
import os
import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RCON_HOST = os.environ.get("RCON_HOST", "monifactory")
RCON_PORT = int(os.environ.get("RCON_PORT", "25575"))
RCON_PASSWORD = os.environ.get("RCON_PASSWORD", "")
BIND_PORT = int(os.environ.get("BIND_PORT", "8000"))
SCRAPE_INTERVAL = float(os.environ.get("SCRAPE_INTERVAL", "30"))

# Minecraft colour codes are the section sign + one char; rcon may also emit
# ANSI resets. Strip both so the regexes see clean text.
_SECTION = re.compile("§.")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# "Dim minecraft:overworld (minecraft:overworld): Mean tick time: 0.728 ms. Mean TPS: 20.000"
_TPS_DIM = re.compile(
    r"Dim\s+(\S+)\s+\([^)]*\):\s*Mean tick time:\s*([0-9.]+)\s*ms\.\s*Mean TPS:\s*([0-9.]+)"
)
# "Overall: Mean tick time: 0.954 ms. Mean TPS: 20.000"
_TPS_OVERALL = re.compile(
    r"Overall:\s*Mean tick time:\s*([0-9.]+)\s*ms\.\s*Mean TPS:\s*([0-9.]+)"
)
_ENT_TOTAL = re.compile(r"Total:\s*([0-9]+)")
_ENT_ROW = re.compile(r"^\s*([0-9]+):\s*(\S+)")

_SERVERDATA_AUTH = 3
_SERVERDATA_EXECCOMMAND = 2


class RconError(Exception):
    pass


def _send(sock, req_id, pkt_type, body):
    payload = struct.pack("<ii", req_id, pkt_type) + body.encode("utf-8") + b"\x00\x00"
    sock.sendall(struct.pack("<i", len(payload)) + payload)


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RconError("connection closed by server")
        buf += chunk
    return buf


def _drain(sock):
    """Read all queued response packets until the socket goes idle.

    forge command output can span multiple RCON packets; read length-prefixed
    packets until a recv times out (no more data), then join the bodies.
    """
    bodies = []
    while True:
        try:
            raw_len = _recv_exact(sock, 4)
        except socket.timeout:
            break
        (length,) = struct.unpack("<i", raw_len)
        packet = _recv_exact(sock, length)
        # req_id (4), type (4), body (null-terminated) + pad null
        body = packet[8:-2].decode("utf-8", "replace")
        bodies.append(body)
    return "".join(bodies)


def rcon_command(cmd):
    with socket.create_connection((RCON_HOST, RCON_PORT), timeout=5) as sock:
        sock.settimeout(5)
        _send(sock, 1, _SERVERDATA_AUTH, RCON_PASSWORD)
        # auth reply: req_id == -1 means bad password
        raw_len = _recv_exact(sock, 4)
        (length,) = struct.unpack("<i", raw_len)
        packet = _recv_exact(sock, length)
        (auth_id,) = struct.unpack("<i", packet[:4])
        if auth_id == -1:
            raise RconError("RCON authentication failed")
        _send(sock, 2, _SERVERDATA_EXECCOMMAND, cmd)
        # short idle timeout to drain the (possibly multi-packet) response
        sock.settimeout(0.6)
        return _drain(sock)


def _clean(text):
    return _ANSI.sub("", _SECTION.sub("", text))


def render_metrics(tps_text, entity_text):
    """Pure function: parse `forge tps` / `forge entity list` output and
    return the Prometheus exposition text.

    No RCON I/O here — takes the raw command output as plain strings, so it
    can be unit-tested against captured fixtures without a live server.
    """
    lines = [
        "# HELP minecraft_rcon_up 1 if the last RCON scrape succeeded.",
        "# TYPE minecraft_rcon_up gauge",
        "minecraft_rcon_up 1",
        "# HELP minecraft_tps Mean ticks per second (from `forge tps`).",
        "# TYPE minecraft_tps gauge",
    ]
    tps_lines, mspt_lines, ent_lines = [], [], []
    total_entities = None

    tps_raw = _clean(tps_text)
    for dim, mspt, tps in _TPS_DIM.findall(tps_raw):
        tps_lines.append(f'minecraft_tps{{dimension="{dim}"}} {tps}')
        mspt_lines.append(f'minecraft_mspt_milliseconds{{dimension="{dim}"}} {mspt}')
    m = _TPS_OVERALL.search(tps_raw)
    if m:
        tps_lines.append(f'minecraft_tps{{dimension="_overall"}} {m.group(2)}')
        mspt_lines.append(f'minecraft_mspt_milliseconds{{dimension="_overall"}} {m.group(1)}')

    ent_raw = _clean(entity_text)
    tm = _ENT_TOTAL.search(ent_raw)
    if tm:
        total_entities = tm.group(1)
    for line in ent_raw.splitlines():
        rm = _ENT_ROW.match(line)
        if rm:
            ent_lines.append(f'minecraft_entities{{type="{rm.group(2)}"}} {rm.group(1)}')

    lines += tps_lines
    lines += ["# HELP minecraft_mspt_milliseconds Mean tick time in milliseconds (from `forge tps`).",
              "# TYPE minecraft_mspt_milliseconds gauge"]
    lines += mspt_lines
    lines += ["# HELP minecraft_entities Loaded entities by type (from `forge entity list`).",
              "# TYPE minecraft_entities gauge"]
    lines += ent_lines
    if total_entities is not None:
        lines += ["# HELP minecraft_entities_total Total loaded entities (from `forge entity list`).",
                  "# TYPE minecraft_entities_total gauge",
                  f"minecraft_entities_total {total_entities}"]
    return "\n".join(lines) + "\n"


def _down_text():
    """Minimal exposition text served when the last RCON scrape failed."""
    return (
        "# HELP minecraft_rcon_up 1 if the last RCON scrape succeeded.\n"
        "# TYPE minecraft_rcon_up gauge\n"
        "minecraft_rcon_up 0\n"
    )


def collect():
    """Run the RCON commands, parse the output, and return the full
    Prometheus exposition text (parsed metrics + the RCON-timing gauge,
    which is a property of this I/O call, not of the pure parser)."""
    started = time.time()
    tps_raw = rcon_command("forge tps")
    ent_raw = rcon_command("forge entity list")
    body = render_metrics(tps_raw, ent_raw)
    duration = time.time() - started
    return (
        body
        + "# HELP minecraft_rcon_scrape_duration_seconds Time to run the RCON commands.\n"
        + "# TYPE minecraft_rcon_scrape_duration_seconds gauge\n"
        + f"minecraft_rcon_scrape_duration_seconds {duration:.4f}\n"
    )


_state = {"text": _down_text()}
_lock = threading.Lock()


def poll_loop():
    while True:
        try:
            text = collect()
        except Exception as exc:  # noqa: BLE001 - report any failure as up=0
            text = _down_text()
            print(f"rcon scrape failed: {exc}", flush=True)
        with _lock:
            _state["text"] = text
        time.sleep(SCRAPE_INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/"):
            self.send_response(404)
            self.end_headers()
            return
        with _lock:
            body = _state["text"]
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence per-request stderr logging
        pass


def main():
    if not RCON_PASSWORD:
        raise SystemExit("RCON_PASSWORD is required")
    threading.Thread(target=poll_loop, daemon=True).start()
    print(f"mc-rcon-exporter serving /metrics on :{BIND_PORT} "
          f"(rcon {RCON_HOST}:{RCON_PORT}, every {SCRAPE_INTERVAL:g}s)", flush=True)
    ThreadingHTTPServer(("", BIND_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
