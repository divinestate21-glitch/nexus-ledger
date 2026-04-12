from __future__ import annotations

from typing import Any, Dict, List, Optional

from .relay_manager import RelayManager


class RelayClient:
    def __init__(self, relays: List[str], *, timeout_seconds: float = 0.75) -> None:
        self._relay_manager = RelayManager(relays, timeout_seconds=timeout_seconds)
        self.relay = relays[0]
        self._relay_available = False

    @property
    def relay_manager(self) -> RelayManager:
        return self._relay_manager

    @property
    def relay_online(self) -> bool:
        return self._relay_available

    def _relay_request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        try:
            response = self._relay_manager.request(path, method=method, params=params, payload=payload)
            self.relay = self._relay_manager.active_relay
            self._relay_available = True
            return response
        except Exception as exc:
            self._relay_available = False
            raise RuntimeError(f"Relay request failed: {exc}") from exc

    def _relay_request_all(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
        attempts = self._relay_manager.request_all(path, method=method, params=params, payload=payload)
        successes = [attempt.response for attempt in attempts if attempt.ok]
        if successes:
            self.relay = self._relay_manager.active_relay
            self._relay_available = True
        elif not attempts:
            self._relay_available = False
        return successes

    def register_on_relay(self, did: str, name: str, pubkey: str) -> None:
        attempts = self._relay_request_all(
            "/register",
            method="POST",
            payload={"did": did, "name": name, "pubkey": pubkey},
        )
        if attempts:
            self._relay_available = True
            return

        checks = self._relay_request_all("/health")
        self._relay_available = bool(checks)

    def check_inbox(self, did: str) -> List[Any]:
        if not self._relay_available:
            return []

        payloads = self._relay_request_all("/receive", params={"did": did})
        if not payloads:
            try:
                payloads = [self._relay_request("/receive", params={"did": did})]
            except RuntimeError:
                return []
        return payloads

    def online_agents(self) -> List[Dict[str, Any]]:
        if not self._relay_available:
            return []
        try:
            payload = self._relay_request("/discover")
        except RuntimeError:
            return []

        if isinstance(payload, dict) and isinstance(payload.get("agents"), list):
            return payload["agents"]
        if isinstance(payload, list):
            return payload
        return []
