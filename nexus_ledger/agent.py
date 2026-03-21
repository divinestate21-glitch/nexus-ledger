"""Single-file public API for Nexus Ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import parse, request
from urllib.error import HTTPError, URLError

from .erc8004 import (
    BASE_MAINNET_RPC,
    HACKATHON_AGENT_ID,
    HACKATHON_WALLET,
    REGISTRATION_TX_HASH,
    ERC8004,
)
from .ledger import Ledger, receipt_proof_hash, receipt_signing_payload
from .proof_anchor import anchor as anchor_proof
from .proof_anchor import verify as verify_proof
from .protocol import generate_keypair, sign, verify
from .transport import (
    HTTPTransport,
    pack_receipt,
    public_key_to_did,
    receive_and_countersign,
    resolve_did,
    send_receipt as transport_send_receipt,
    unpack_receipt,
)


DEFAULT_RELAY_URL = "http://104.236.251.94:8765"


class Agent:
    def __init__(
        self,
        name: str,
        *,
        keys_dir: str = "keys",
        db_path: str = "nexus.db",
        relay: str = DEFAULT_RELAY_URL,
        erc8004_agent_id: int = HACKATHON_AGENT_ID,
        erc8004_wallet: str = HACKATHON_WALLET,
        erc8004_registration_tx: str = REGISTRATION_TX_HASH,
        erc8004_rpc_url: str = BASE_MAINNET_RPC,
    ) -> None:
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("Agent name is required")

        self.name = clean_name
        self.keys_dir = Path(keys_dir)
        self.keys_dir.mkdir(parents=True, exist_ok=True)

        self.private_key, self.public_key = self._load_or_create_keys()
        self._ledger = Ledger(path=db_path)
        self._listener_server = None
        self.relay = str(relay).strip() or DEFAULT_RELAY_URL
        self.erc8004_agent_id = int(erc8004_agent_id)
        self.erc8004_wallet = str(erc8004_wallet).strip() or HACKATHON_WALLET
        self._erc8004 = ERC8004(
            rpc_url=erc8004_rpc_url,
            registration_tx_hash=erc8004_registration_tx,
            default_agent_id=self.erc8004_agent_id,
            default_wallet=self.erc8004_wallet,
        )
        self._relay_available = False
        self._relay_timeout_seconds = 0.75
        self._register_on_relay_safely()

    def _key_file_path(self) -> Path:
        safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in self.name).strip("_")
        filename = f"{safe or 'agent'}.json"
        return self.keys_dir / filename

    def _load_or_create_keys(self) -> tuple[str, str]:
        key_path = self._key_file_path()
        if key_path.exists():
            payload = json.loads(key_path.read_text(encoding="utf-8"))
            return str(payload["private_key"]), str(payload["public_key"])

        private_key, public_key = generate_keypair()
        key_path.write_text(
            json.dumps(
                {
                    "name": self.name,
                    "private_key": private_key,
                    "public_key": public_key,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return private_key, public_key

    def log(self, event_type: str, data: Dict[str, Any], counterparty: Optional["Agent"] = None) -> Dict[str, Any]:
        counterparty_key = counterparty.public_key if counterparty else None
        return self._ledger.log(
            self.private_key,
            event_type,
            data,
            counterparty_key=counterparty_key,
        )

    def anchor(self, data_dict: Dict[str, Any], keypair_path: str = "~/.config/solana/id.json") -> Dict[str, str]:
        return anchor_proof(data_dict, keypair_path=keypair_path)

    def verify(self, data_dict: Dict[str, Any], expected_hash: str) -> bool:
        return verify_proof(data_dict, expected_hash)

    def erc8004_identity(self) -> Dict[str, Any]:
        return self._erc8004.get_agent_identity(self.erc8004_agent_id)

    def rate_counterparty(self, receipt: Dict[str, Any], rating: int, comment: str) -> Dict[str, Any]:
        if not isinstance(receipt, dict):
            raise ValueError("receipt must be a dict")
        receipt_hash = receipt_proof_hash(receipt)
        return self._erc8004.post_reputation(self.erc8004_agent_id, receipt_hash, rating, comment)

    def get_on_chain_reputation(self) -> Dict[str, Any]:
        return self._erc8004.get_reputation(self.erc8004_agent_id)

    def history(self) -> list[Dict[str, Any]]:
        return self._ledger.by_agent(self.public_key)

    def all_activity(self) -> list[Dict[str, Any]]:
        return self._ledger.all()

    @property
    def did(self) -> str:
        return public_key_to_did(self.public_key)

    @property
    def relay_online(self) -> bool:
        return self._relay_available

    def _register_on_relay_safely(self) -> None:
        try:
            self._relay_request(
                "/register",
                method="POST",
                payload={"did": self.did, "name": self.name, "pubkey": self.public_key},
            )
            self._relay_available = True
        except Exception:
            try:
                self._relay_request("/health")
                self._relay_available = True
            except Exception:
                self._relay_available = False

    def _relay_request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        base = self.relay.rstrip("/")
        rel = path if path.startswith("/") else f"/{path}"
        query = f"?{parse.urlencode(params)}" if params else ""
        url = f"{base}{rel}{query}"

        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with request.urlopen(req, timeout=self._relay_timeout_seconds) as response:
                text = response.read().decode("utf-8").strip()
        except (HTTPError, URLError, TimeoutError) as exc:
            self._relay_available = False
            raise RuntimeError(f"Relay request failed: {exc}") from exc

        self._relay_available = True
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _parse_discovery_response(self, payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict) and "agents" in payload:
            payload = payload["agents"]
        if isinstance(payload, list):
            if not payload:
                return None
            payload = payload[0]
        if isinstance(payload, dict):
            candidate = payload.get("agent")
            if isinstance(candidate, dict):
                payload = candidate
            did = str(payload.get("did", "")).strip()
            pubkey = str(payload.get("pubkey", payload.get("public_key", ""))).strip()
            name = str(payload.get("name", "")).strip()
            if did:
                if not pubkey:
                    pubkey = resolve_did(did).hex()
                normalized = dict(payload)
                normalized["did"] = did
                normalized["name"] = name
                normalized["pubkey"] = pubkey
                return normalized
        return None

    def _extract_envelopes(self, payload: Any) -> list[Dict[str, Any]]:
        if isinstance(payload, dict):
            if isinstance(payload.get("messages"), list):
                items: list[Any] = payload["messages"]
            elif isinstance(payload.get("envelopes"), list):
                items = payload["envelopes"]
            elif "envelope" in payload:
                items = [payload["envelope"]]
            elif all(key in payload for key in ("sender_did", "signature", "payload")):
                items = [payload]
            else:
                items = []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []

        envelopes: list[Dict[str, Any]] = []
        for item in items:
            candidate: Any = item.get("envelope") if isinstance(item, dict) and "envelope" in item else item
            if isinstance(candidate, str):
                try:
                    candidate = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            if isinstance(candidate, dict) and all(k in candidate for k in ("sender_did", "signature", "payload")):
                envelopes.append(candidate)
        return envelopes

    def find(self, name: str) -> Optional[Dict[str, Any]]:
        query = str(name).strip()
        if not query:
            raise ValueError("Agent name is required")
        if not self._relay_available:
            return None
        try:
            payload = self._relay_request("/discover", params={"name": query})
        except RuntimeError:
            return None
        return self._parse_discovery_response(payload)

    def online_agents(self) -> list[Dict[str, Any]]:
        if not self._relay_available:
            return []
        try:
            payload = self._relay_request("/discover")
        except RuntimeError:
            return []

        if isinstance(payload, dict) and isinstance(payload.get("agents"), list):
            raw_agents = payload["agents"]
        elif isinstance(payload, list):
            raw_agents = payload
        else:
            raw_agents = []

        result: list[Dict[str, Any]] = []
        for item in raw_agents:
            parsed = self._parse_discovery_response(item)
            if parsed is not None:
                result.append(parsed)
        return result

    def send(self, event_type: str, data: Dict[str, Any], *, to: str) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("data must be a dict")
        if not self._relay_available:
            raise RuntimeError("Relay is unavailable; agent is running in local-only mode")

        target = str(to).strip()
        if not target:
            raise ValueError("Recipient name or DID is required")

        if target.startswith("did:key:"):
            recipient_did = target
            recipient_pubkey = resolve_did(target).hex()
        else:
            found = self.find(target)
            if found is None:
                raise ValueError(f"Agent '{target}' not found on relay")
            recipient_did = str(found["did"])
            recipient_pubkey = str(found["pubkey"])

        receipt = self.create_receipt(event_type, data, recipient_pubkey)
        envelope = json.loads(pack_receipt(receipt, self.private_key))
        self._relay_request("/send", method="POST", payload={"to": recipient_did, "envelope": envelope})
        return receipt

    def check_inbox(self) -> list[Dict[str, Any]]:
        if not self._relay_available:
            return []
        try:
            payload = self._relay_request("/receive", params={"did": self.did})
        except RuntimeError:
            return []

        envelopes = self._extract_envelopes(payload)
        processed: list[Dict[str, Any]] = []
        for envelope in envelopes:
            envelope_json = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
            sender_did = str(envelope.get("sender_did", "")).strip()

            try:
                receipt = unpack_receipt(envelope_json, expected_sender_did=sender_did or None)
            except ValueError:
                continue
            if "agent_b_signature" in receipt:
                if self.verify_receipt(receipt):
                    self.store_receipt(receipt)
                    processed.append(receipt)
                continue

            try:
                countersigned = self.countersign_receipt(receipt)
            except ValueError:
                continue
            self.store_receipt(countersigned)
            processed.append(countersigned)

            if sender_did:
                response_envelope = json.loads(pack_receipt(countersigned, self.private_key))
                try:
                    self._relay_request(
                        "/send",
                        method="POST",
                        payload={"to": sender_did, "envelope": response_envelope},
                    )
                except RuntimeError:
                    continue

        return processed

    def create_receipt(self, event_type: str, data: Dict[str, Any], counterparty_pubkey: str) -> Dict[str, Any]:
        if not str(counterparty_pubkey).strip():
            raise ValueError("counterparty_pubkey is required")
        if not isinstance(data, dict):
            raise ValueError("data must be a dict")

        receipt: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": str(event_type),
            "data": data,
            "agent_a_pubkey": self.public_key,
            "agent_b_pubkey": str(counterparty_pubkey),
        }
        receipt["agent_a_signature"] = sign(self.private_key, receipt_signing_payload(receipt))
        return receipt

    def countersign_receipt(self, receipt: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(receipt, dict):
            raise ValueError("receipt must be a dict")

        required_fields = [
            "timestamp",
            "event_type",
            "data",
            "agent_a_pubkey",
            "agent_b_pubkey",
            "agent_a_signature",
        ]
        missing = [field for field in required_fields if field not in receipt]
        if missing:
            raise ValueError(f"Receipt missing required fields: {', '.join(missing)}")

        if str(receipt["agent_b_pubkey"]) != self.public_key:
            raise ValueError("Receipt is not addressed to this agent")

        payload = receipt_signing_payload(receipt)
        if not verify(str(receipt["agent_a_pubkey"]), payload, str(receipt["agent_a_signature"])):
            raise ValueError("Invalid agent A signature")

        countersigned = dict(receipt)
        countersigned["agent_b_signature"] = sign(self.private_key, payload)
        return countersigned

    def verify_receipt(self, receipt: Dict[str, Any]) -> bool:
        required_fields = [
            "timestamp",
            "event_type",
            "data",
            "agent_a_pubkey",
            "agent_b_pubkey",
            "agent_a_signature",
            "agent_b_signature",
        ]
        if not isinstance(receipt, dict):
            return False
        if any(field not in receipt for field in required_fields):
            return False

        payload = receipt_signing_payload(receipt)
        agent_a_ok = verify(str(receipt["agent_a_pubkey"]), payload, str(receipt["agent_a_signature"]))
        agent_b_ok = verify(str(receipt["agent_b_pubkey"]), payload, str(receipt["agent_b_signature"]))
        return agent_a_ok and agent_b_ok

    def store_receipt(self, receipt: Dict[str, Any]) -> Dict[str, Any]:
        if not self.verify_receipt(receipt):
            raise ValueError("Cannot store invalid receipt")
        receipt_to_store = dict(receipt)
        receipt_to_store["proof_hash"] = receipt_proof_hash(receipt_to_store)
        return self._ledger.store_receipt(receipt_to_store)

    def export_receipt(self, receipt: Dict[str, Any]) -> str:
        return json.dumps(receipt, sort_keys=True, separators=(",", ":"))

    def import_receipt(self, json_string: str) -> Dict[str, Any]:
        receipt = json.loads(json_string)
        if not isinstance(receipt, dict):
            raise ValueError("Receipt JSON must decode to an object")
        return receipt

    def export_did(self) -> str:
        return self.did

    def send_receipt(
        self,
        to_agent_or_did: Any,
        event_type: str,
        data: Dict[str, Any],
        transport: str = "http",
        endpoint: Optional[str] = None,
    ) -> str:
        recipient_did: Optional[str] = None
        recipient_pubkey: Optional[str] = None

        if isinstance(to_agent_or_did, Agent):
            recipient_did = to_agent_or_did.did
            recipient_pubkey = to_agent_or_did.public_key
        else:
            target = str(to_agent_or_did).strip()
            if not target:
                raise ValueError("Recipient DID is required")
            if target.startswith("did:key:"):
                recipient_did = target
                recipient_pubkey = resolve_did(target).hex()
            else:
                recipient_pubkey = target
                recipient_did = public_key_to_did(target)

        receipt = self.create_receipt(event_type, data, recipient_pubkey)
        response = transport_send_receipt(
            self,
            recipient_did,
            receipt,
            transport=transport,
            endpoint=endpoint,
        )
        return response

    def receive_receipt(self, envelope_json: str) -> Dict[str, Any]:
        receipt = unpack_receipt(envelope_json)
        if "agent_b_signature" in receipt:
            if not self.verify_receipt(receipt):
                raise ValueError("Received countersigned receipt is invalid")
            self.store_receipt(receipt)
            return receipt
        return receive_and_countersign(self, envelope_json)

    def start_listener(self, port: int = 8765):
        def _callback(inbound_envelope: str) -> str:
            countersigned = self.receive_receipt(inbound_envelope)
            return pack_receipt(countersigned, self.private_key)

        if self._listener_server is not None:
            self._listener_server.shutdown()
            self._listener_server.server_close()

        self._listener_server = HTTPTransport().start_listener(
            port,
            _callback,
            did_provider=self.export_did,
        )
        return self._listener_server
