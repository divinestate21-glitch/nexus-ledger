"""Relay manager with failover across multiple relay endpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import parse, request
from urllib.error import HTTPError, URLError


import os
DEFAULT_PRIMARY_RELAY = os.getenv("NEXUS_RELAY_PRIMARY", "http://104.236.251.94:8765")
DEFAULT_FALLBACK_RELAY = os.getenv("NEXUS_RELAY_FALLBACK", "http://localhost:8765")
DEFAULT_RELAYS = [DEFAULT_PRIMARY_RELAY, DEFAULT_FALLBACK_RELAY]


@dataclass
class RelayAttempt:
    relay: str
    ok: bool
    response: Any = None
    error: Optional[Exception] = None


class RelayManager:
    """HTTP relay requester with transparent failover."""

    def __init__(self, relays: List[str], timeout_seconds: float = 0.75) -> None:
        cleaned = [str(relay).strip().rstrip("/") for relay in relays if str(relay).strip()]
        if not cleaned:
            cleaned = list(DEFAULT_RELAYS)
        self.relays = cleaned
        self.timeout_seconds = float(timeout_seconds)
        self._active_index = 0

    @property
    def active_relay(self) -> str:
        return self.relays[self._active_index]

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Try current relay first, then fail over through all configured relays."""
        ordered = self._ordered_relays()
        last_error: Optional[Exception] = None

        for relay in ordered:
            try:
                response = self._request_to_relay(relay, path, method=method, params=params, payload=payload)
                self._active_index = self.relays.index(relay)
                return response
            except Exception as exc:
                last_error = exc
                continue

        raise RuntimeError(f"All relays failed for {path}: {last_error}")

    def request_all(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> List[RelayAttempt]:
        """Issue the same request to every relay and collect successes/failures."""
        attempts: List[RelayAttempt] = []
        for relay in self.relays:
            try:
                response = self._request_to_relay(relay, path, method=method, params=params, payload=payload)
                attempts.append(RelayAttempt(relay=relay, ok=True, response=response))
            except Exception as exc:
                attempts.append(RelayAttempt(relay=relay, ok=False, error=exc))
        if attempts:
            for attempt in attempts:
                if attempt.ok:
                    self._active_index = self.relays.index(attempt.relay)
                    break
        return attempts

    def _ordered_relays(self) -> List[str]:
        active = self.active_relay
        others = [relay for relay in self.relays if relay != active]
        return [active] + others

    def _request_to_relay(
        self,
        relay: str,
        path: str,
        *,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        rel = path if path.startswith("/") else f"/{path}"
        query = f"?{parse.urlencode(params)}" if params else ""
        url = f"{relay}{rel}{query}"

        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8").strip()
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError(f"Relay request failed for {relay}: {exc}") from exc

        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
