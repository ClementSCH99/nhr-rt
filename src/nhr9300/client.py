"""Pure-standard-library client usable from a 64-bit Python process."""

from __future__ import annotations

import json
import threading
from typing import Any, Iterator, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .errors import NHRError


class NHRServiceClient:
    def __init__(self, base_url: str = "http://127.0.0.1:9300") -> None:
        self.base_url = base_url.rstrip("/")

    def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=10.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            raise NHRError(payload.get("error", str(exc))) from exc

    def instruments(self) -> list[dict[str, Any]]:
        return self._request("GET", "/instruments")

    def configuration(self) -> dict[str, Any]:
        return self._request("GET", "/configuration")

    def connect(self, instrument_id: str) -> dict[str, Any]:
        return self._request("POST", f"/instruments/{instrument_id}/connect", {})

    def disconnect(self, instrument_id: str) -> dict[str, Any]:
        return self._request("POST", f"/instruments/{instrument_id}/disconnect", {})

    def status(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}")

    def measurement(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}/measurement")

    def acquisition(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}/acquisition")

    def configure_limits(
        self, instrument_id: str, limits: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._request("POST", f"/instruments/{instrument_id}/limits", limits)

    def arm(self, instrument_id: str, duration_s: float = 30.0) -> dict[str, Any]:
        return self._request(
            "POST", f"/instruments/{instrument_id}/arm", {"duration_s": duration_s}
        )

    def command(
        self, instrument_id: str, name: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/instruments/{instrument_id}/command",
            {"name": name, **kwargs},
        )

    def start_routine(
        self, instrument_id: str, definition: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/instruments/{instrument_id}/routine", definition
        )

    def routine(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}/routine")

    def stop(self, instrument_id: str) -> dict[str, Any]:
        return self._request("POST", f"/instruments/{instrument_id}/stop", {})

    def stream(
        self,
        instrument_id: str,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        if stop_event is not None and stop_event.is_set():
            return
        request = Request(
            self.base_url + f"/instruments/{instrument_id}/stream",
            headers={"Accept": "text/event-stream"},
        )
        with urlopen(request, timeout=None) as response:
            for raw_line in response:
                if stop_event is not None and stop_event.is_set():
                    return
                line = raw_line.decode("utf-8").strip()
                if line.startswith("data: "):
                    yield json.loads(line[6:])
