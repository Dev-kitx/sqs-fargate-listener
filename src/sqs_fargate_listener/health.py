from __future__ import annotations
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Optional


class HealthServer:
    """
    Lightweight HTTP server exposing /health and /metrics endpoints.
    Runs in a daemon thread — starts with the engine, stops on shutdown.

    Endpoints:
        GET /health  → 200 {"status": "ok"}
        GET /metrics → 200 {stats dict}
    """

    def __init__(self, port: int, get_stats: Callable[[], Dict[str, Any]]):
        self._port = port
        self._get_stats = get_stats
        self._server: Optional[HTTPServer] = None

    def start(self) -> None:
        get_stats = self._get_stats

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    self._respond(200, {"status": "ok"})
                elif self.path == "/metrics":
                    self._respond(200, get_stats())
                else:
                    self._respond(404, {"error": "not found"})

            def _respond(self, code: int, data: dict) -> None:
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args, **kwargs) -> None:
                pass  # suppress default request logging

        self._server = HTTPServer(("", self._port), _Handler)
        threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="sqs-health",
        ).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
