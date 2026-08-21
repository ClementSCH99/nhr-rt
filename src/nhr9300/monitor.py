"""Strictly read-only localhost web monitor for the public service client."""

from __future__ import annotations

import argparse
import json
import logging

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any, Protocol
from urllib.parse import urlparse

from .client import NHRServiceClient


LOGGER = logging.getLogger(__name__)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class RuntimeReader(Protocol):
    """The monitor's deliberately narrow view of the public client."""

    def runtime(self, instrument_id: str) -> dict[str, Any]: ...


class LocalMonitorServer(ThreadingHTTPServer):
    """Local-only monitor server kept separate from the 32-bit IVI service."""

    allow_reuse_address = False


def require_local_service_url(service_url: str) -> None:
    """Reject a service target outside the established localhost boundary."""
    parsed = urlparse(service_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in LOCAL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("service_url must be an unauthenticated localhost HTTP URL")


class MonitorRequestHandler(BaseHTTPRequestHandler):
    runtime_reader: RuntimeReader
    instrument_id: str
    refresh_interval_s = 1.0
    trend_points = 600

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def _send_headers(
        self, status: HTTPStatus, content_type: str, content_length: int
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        include_body: bool = True,
    ) -> None:
        self._send_headers(status, content_type, len(body))
        if include_body:
            self.wfile.write(body)

    def _send_json(
        self,
        status: HTTPStatus,
        value: Any,
        *,
        include_body: bool = True,
    ) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._send_bytes(
            status,
            body,
            "application/json; charset=utf-8",
            include_body=include_body,
        )

    def _serve(self, *, include_body: bool) -> None:
        path = urlparse(self.path).path
        if path == "/api/config":
            self._send_json(
                HTTPStatus.OK,
                {
                    "instrument_id": self.instrument_id,
                    "refresh_interval_s": self.refresh_interval_s,
                    "trend_points": self.trend_points,
                    "read_only": True,
                },
                include_body=include_body,
            )
            return
        if path == "/api/runtime":
            try:
                snapshot = self.runtime_reader.runtime(self.instrument_id)
            except Exception as exc:
                LOGGER.warning("Runtime snapshot unavailable: %s", exc)
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "error": "service_unavailable",
                        "message": str(exc),
                        "instrument_id": self.instrument_id,
                    },
                    include_body=include_body,
                )
                return
            self._send_json(HTTPStatus.OK, snapshot, include_body=include_body)
            return
        asset = ASSETS.get(path)
        if asset is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "not_found"},
                include_body=include_body,
            )
            return
        name, content_type = asset
        body = files("nhr9300").joinpath("monitor_assets", name).read_bytes()
        self._send_bytes(
            HTTPStatus.OK,
            body,
            content_type,
            include_body=include_body,
        )

    def do_GET(self) -> None:
        self._serve(include_body=True)

    def do_HEAD(self) -> None:
        self._serve(include_body=False)

    def _reject_write(self) -> None:
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "read_only_monitor", "allowed": ["GET", "HEAD"]},
        )

    do_POST = _reject_write
    do_PUT = _reject_write
    do_PATCH = _reject_write
    do_DELETE = _reject_write
    do_OPTIONS = _reject_write


def build_monitor(
    runtime_reader: RuntimeReader,
    instrument_id: str,
    host: str = "127.0.0.1",
    port: int = 9400,
    *,
    refresh_interval_s: float = 1.0,
    trend_points: int = 600,
) -> ThreadingHTTPServer:
    """Build a monitor that exposes no NHR control route."""
    if host not in LOCAL_HOSTS:
        raise ValueError("The monitor may bind to localhost only")
    if not instrument_id.strip():
        raise ValueError("instrument_id must not be empty")
    if refresh_interval_s <= 0:
        raise ValueError("refresh_interval_s must be positive")
    if trend_points <= 1:
        raise ValueError("trend_points must be greater than one")
    handler = type(
        "ConfiguredMonitorHandler",
        (MonitorRequestHandler,),
        {
            "runtime_reader": runtime_reader,
            "instrument_id": instrument_id,
            "refresh_interval_s": refresh_interval_s,
            "trend_points": trend_points,
        },
    )
    return LocalMonitorServer((host, port), handler)


def serve_monitor(
    service_url: str,
    instrument_id: str,
    host: str = "127.0.0.1",
    port: int = 9400,
) -> None:
    """Run the local monitor using only the public ``NHRServiceClient``."""
    require_local_service_url(service_url)
    client = NHRServiceClient(service_url)
    server = build_monitor(client, instrument_id, host, port)
    bound_host, bound_port = server.server_address[:2]
    print(f"NHR9300 monitor: http://{bound_host}:{bound_port}", flush=True)
    print(f"NHR9300 instrument: {instrument_id} (read-only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Monitor shutdown requested by operator (Ctrl+C)")
    finally:
        server.server_close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Run the strictly read-only local NHR9300 web monitor"
    )
    parser.add_argument("--service-url", default="http://127.0.0.1:9300")
    parser.add_argument("--instrument-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9400, type=int)
    args = parser.parse_args()
    serve_monitor(args.service_url, args.instrument_id, args.host, args.port)


if __name__ == "__main__":
    main()
