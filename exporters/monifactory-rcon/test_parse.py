import os
import socket
import struct
import threading
import time

import exporter
from exporter import render_metrics

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return fh.read()


def test_render_metrics_parses_tps_and_entities():
    out = render_metrics(_fixture("forge_tps.txt"), _fixture("entity_list.txt"))
    assert ('minecraft_tps{dimension="_overall"} 20.0' in out
            or 'minecraft_tps{dimension="_overall"} 20.000' in out)
    assert 'minecraft_entities{type="minecraft:zombie"} 30' in out
    assert 'minecraft_rcon_up 1' in out


def test_render_metrics_empty_response_reports_down():
    # A response that never arrived (empty) must not look like a healthy scrape.
    out = render_metrics("", "")
    assert "minecraft_rcon_up 0" in out
    assert "minecraft_tps{" not in out
    assert "minecraft_entities{" not in out


def test_render_metrics_unparseable_response_reports_down():
    # Garbage with no `forge tps` lines is a failed collection, not up=1.
    out = render_metrics("no tps here", "junk\n  not: an entity row")
    assert "minecraft_rcon_up 0" in out
    assert "minecraft_tps{" not in out


# ---- RCON protocol / timeout regression (findings: slow reply, multi-packet) ----

def _packet(req_id, body):
    payload = struct.pack("<ii", req_id, 0) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _recv_all(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise AssertionError("client closed early")
        buf += chunk
    return buf


def _read_one_packet(conn):
    (length,) = struct.unpack("<i", _recv_all(conn, 4))
    return _recv_all(conn, length)


class MockRcon:
    """Minimal Source-RCON server: authenticates, then replies to each command
    after `delay` seconds, optionally splitting the reply into several packets."""

    def __init__(self, replies, delay=0.0):
        self.replies = replies          # command body (str) -> reply body (str)
        self.delay = delay
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._sock.close()

    def _serve(self):
        conn, _ = self._sock.accept()
        with conn:
            _read_one_packet(conn)                 # auth request
            conn.sendall(_packet(1, ""))           # auth OK (id != -1)
            while True:
                try:
                    pkt = _read_one_packet(conn)
                except (AssertionError, OSError):
                    return
                cmd = pkt[8:-2].decode("utf-8", "replace")
                reply = self.replies.get(cmd, "")
                if self.delay:
                    time.sleep(self.delay)
                for chunk in reply if isinstance(reply, list) else [reply]:
                    conn.sendall(_packet(2, chunk))


def _point_exporter_at(port):
    exporter.RCON_HOST = "127.0.0.1"
    exporter.RCON_PORT = port
    exporter.RCON_PASSWORD = "test"


def test_rcon_command_collects_delayed_reply():
    # Reply arrives 0.8s after the command — past the old 0.6s idle timeout that
    # dropped it, but well within RCON_TIMEOUT. It must be collected, not lost.
    body = _fixture("forge_tps.txt")
    with MockRcon({"forge tps": body}, delay=0.8) as srv:
        _point_exporter_at(srv.port)
        got = exporter.rcon_command("forge tps")
    assert "Mean TPS: 20.000" in got


def test_rcon_command_joins_multipacket_reply():
    # forge output can span multiple RCON packets; both halves must survive.
    body = _fixture("forge_tps.txt")
    half = len(body) // 2
    with MockRcon({"forge tps": [body[:half], body[half:]]}) as srv:
        _point_exporter_at(srv.port)
        got = exporter.rcon_command("forge tps")
    assert got == body
