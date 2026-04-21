from __future__ import annotations

import json
import socket
import time
import urllib.request

import pytest

from src.sqs_fargate_listener.health import HealthServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _get(port: int, path: str) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=2) as resp:
        return resp.status, json.loads(resp.read())


def _get_status(port: int, path: str) -> int:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.fixture()
def running_server():
    port = _free_port()
    stats = {"messages_processed": 5, "messages_failed": 1}
    server = HealthServer(port, lambda: stats)
    server.start()
    # Give the daemon thread a moment to bind
    time.sleep(0.05)
    yield port, server
    server.stop()


def test_health_endpoint_returns_ok(running_server):
    port, _ = running_server
    status, body = _get(port, "/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_metrics_endpoint_returns_stats(running_server):
    port, _ = running_server
    status, body = _get(port, "/metrics")
    assert status == 200
    assert body["messages_processed"] == 5
    assert body["messages_failed"] == 1


def test_unknown_path_returns_404(running_server):
    port, _ = running_server
    code = _get_status(port, "/unknown")
    assert code == 404


def test_stop_shuts_down_server():
    port = _free_port()
    server = HealthServer(port, lambda: {})
    server.start()
    time.sleep(0.05)
    server.stop()
    # After stop, connections should be refused
    with pytest.raises(OSError):
        _get(port, "/health")


def test_stop_noop_when_not_started():
    server = HealthServer(_free_port(), lambda: {})
    server.stop()  # should not raise
