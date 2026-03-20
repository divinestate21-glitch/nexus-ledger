"""Nexus Ledger SDK Agent client."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

import aiohttp
from nacl.signing import SigningKey


def _canonical_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _sign_payload(payload: Dict[str, Any], private_key_hex: str) -> str:
    key = SigningKey(bytes.fromhex(private_key_hex))
    signed = key.sign(_canonical_json(payload).encode("utf-8"))
    return signed.signature.hex()


class Agent:
    def __init__(self, name: str, capabilities: List[str], hub_url: str = "http://localhost:9000") -> None:
        self.name = str(name).strip()
        self.capabilities = [str(c).strip() for c in capabilities if str(c).strip()]
        self.hub_url = hub_url.rstrip("/")

        self._signing_key = SigningKey.generate()
        self.private_key = self._signing_key.encode().hex()
        self.public_key = self._signing_key.verify_key.encode().hex()
        self.agent_id = self.name

        registration = self._run(self._register())
        registered = registration.get("registered", {})
        self.agent_id = str(registered.get("agent_id", self.agent_id))

    def discover(self, capability: str) -> List[Dict[str, Any]]:
        result = self._run(self._request("GET", "/discover", params={"capability": capability}))
        return list(result.get("matches", []))

    def log(self, message: str, event_type: str = "task_completed", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"message": str(message)}
        if isinstance(extra, dict):
            payload.update(extra)

        signed_payload = {
            "agent_id": self.agent_id,
            "event_type": event_type,
            "payload": payload,
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        signature = _sign_payload(signed_payload, self.private_key)

        body = {
            "agent_id": self.agent_id,
            "event_type": event_type,
            "payload": payload,
            "client_timestamp": signed_payload["client_timestamp"],
            "signature": signature,
            "public_key": self.public_key,
        }
        result = self._run(self._request("POST", "/log", json=body))
        return dict(result.get("logged", {}))

    def anchor_proof(self, data: Dict[str, Any], mode: str = "auto") -> Dict[str, Any]:
        body = {"agent_id": self.agent_id, "data": data, "mode": mode}
        result = self._run(self._request("POST", "/anchor", json=body))
        return dict(result.get("anchor", {}))

    def verify_proof(
        self,
        tx_signature: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        proof_hash: Optional[str] = None,
        mode: str = "auto",
    ) -> Dict[str, Any]:
        params: Dict[str, str] = {"mode": mode}
        if proof_hash:
            params["proof_hash"] = proof_hash
        if data is not None:
            params["data"] = json.dumps(data, sort_keys=True)
        return self._run(self._request("GET", f"/verify/{tx_signature}", params=params))

    def get_ledger(self) -> Dict[str, Any]:
        return self._run(self._request("GET", "/ledger"))

    async def _register(self) -> Dict[str, Any]:
        body = {
            "agent_id": self.name,
            "name": self.name,
            "capabilities": self.capabilities,
            "public_key": self.public_key,
        }
        return await self._request("POST", "/register", json=body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.hub_url}{path}"
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, params=params, json=json) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"{method} {path} failed ({resp.status}): {text}")
                return await resp.json()

    def _run(self, coro: "asyncio.Future[Dict[str, Any]]") -> Dict[str, Any]:
        return asyncio.run(coro)
