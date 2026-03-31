"""Single-file public API for Nexus Ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .crypto import decrypt_payload, encrypt_payload
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
from .receipt_types import TaskAccepted, TaskConfirmed, TaskDelivered, TaskDisputed, TaskRequest, new_task_id
from .supply_chain import SupplyChainModule
from .relay_manager import DEFAULT_RELAYS, RelayManager
from .transport import (
    HTTPTransport,
    pack_receipt,
    public_key_to_did,
    receive_and_countersign,
    resolve_did,
    send_receipt as transport_send_receipt,
    unpack_receipt,
)
from .trust import TrustScorer
from .ws_transport import LiveConnection


DEFAULT_RELAY_URL = DEFAULT_RELAYS[0]


def verify_receipt_dict(receipt: Dict[str, Any]) -> bool:
    required_fields = [
        "timestamp",
        "event_type",
        "data",
        "parent_receipt_hash",
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


class Agent:
    def __init__(
        self,
        name: str,
        *,
        keys_dir: str = "keys",
        db_path: str = "nexus.db",
        relay: Optional[str] = None,
        relays: Optional[List[str]] = None,
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

        configured_relays = self._normalize_relays(relay=relay, relays=relays)
        self.relays = configured_relays
        self.relay = configured_relays[0]
        self._relay_manager = RelayManager(configured_relays, timeout_seconds=0.75)

        self.erc8004_agent_id = int(erc8004_agent_id)
        self.erc8004_wallet = str(erc8004_wallet).strip() or HACKATHON_WALLET
        self._erc8004 = ERC8004(
            rpc_url=erc8004_rpc_url,
            registration_tx_hash=erc8004_registration_tx,
            default_agent_id=self.erc8004_agent_id,
            default_wallet=self.erc8004_wallet,
        )
        self._relay_available = False
        self._receipt_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._live_connection: Optional[LiveConnection] = None
        self._trust = TrustScorer()
        self._supply_chain = SupplyChainModule(self._ledger, self.public_key)
        self._register_on_relay_safely()

    @staticmethod
    def _normalize_relays(*, relay: Optional[str], relays: Optional[List[str]]) -> List[str]:
        if relays:
            cleaned = [str(item).strip() for item in relays if str(item).strip()]
            if cleaned:
                return cleaned
        if relay:
            primary = str(relay).strip()
            if primary:
                fallback = DEFAULT_RELAYS[1]
                return [primary, fallback] if primary != fallback else [primary]
        return list(DEFAULT_RELAYS)

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
        attempts = self._relay_request_all(
            "/register",
            method="POST",
            payload={"did": self.did, "name": self.name, "pubkey": self.public_key},
        )
        if attempts:
            self._relay_available = True
            return

        checks = self._relay_request_all("/health")
        self._relay_available = bool(checks)

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
        if "_relay_request" in self.__dict__:
            try:
                return [self._relay_request(path, method=method, params=params, payload=payload)]
            except Exception:
                return []

        attempts = self._relay_manager.request_all(path, method=method, params=params, payload=payload)
        successes = [attempt.response for attempt in attempts if attempt.ok]
        if successes:
            self.relay = self._relay_manager.active_relay
            self._relay_available = True
        elif not attempts:
            self._relay_available = False
        return successes

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

    def _latest_task_receipt_hash(self, task_id: str) -> str:
        chain = self.get_task_chain(task_id)
        if not chain:
            return ""
        return str(chain[-1].get("proof_hash", ""))

    def _resolve_task_counterparty_pubkey(self, task_id: str) -> Optional[str]:
        chain = self.get_task_chain(task_id)
        if not chain:
            return None
        latest = chain[-1]
        a = str(latest.get("agent_a_pubkey", ""))
        b = str(latest.get("agent_b_pubkey", ""))
        if a == self.public_key:
            return b
        if b == self.public_key:
            return a
        return b or a or None

    def _wrap_for_transport(self, receipt: Dict[str, Any], recipient_pubkey: str, encrypted: bool) -> Dict[str, Any]:
        if not encrypted:
            return receipt
        return encrypt_payload(receipt, self.private_key, recipient_pubkey)

    def _unwrap_transport_payload(self, payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        if payload.get("encrypted") is True:
            decrypted = decrypt_payload(payload, self.private_key)
            return decrypted, True
        return payload, False

    def send(
        self,
        event_type: str,
        data: Dict[str, Any],
        *,
        to: str,
        encrypted: bool = False,
        parent_receipt_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
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

        parent_hash = str(parent_receipt_hash or "")
        if not parent_hash and str(data.get("task_id", "")).strip():
            parent_hash = self._latest_task_receipt_hash(str(data["task_id"]))

        receipt = self.create_receipt(event_type, data, recipient_pubkey, parent_receipt_hash=parent_hash)
        payload = self._wrap_for_transport(receipt, recipient_pubkey, encrypted=encrypted)
        envelope = json.loads(pack_receipt(payload, self.private_key))
        self._relay_request("/send", method="POST", payload={"to": recipient_did, "envelope": envelope})
        return receipt

    def _process_envelope(self, envelope: Dict[str, Any]) -> List[Dict[str, Any]]:
        envelope_json = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        sender_did = str(envelope.get("sender_did", "")).strip()

        try:
            payload = unpack_receipt(envelope_json, expected_sender_did=sender_did or None)
        except ValueError:
            return []

        try:
            receipt, was_encrypted = self._unwrap_transport_payload(payload)
        except Exception:
            return []

        processed: List[Dict[str, Any]] = []

        if "agent_b_signature" in receipt:
            if self.verify_receipt(receipt):
                stored = self.store_receipt(receipt)
                stored_receipt = dict(receipt)
                stored_receipt["proof_hash"] = stored["proof_hash"]
                processed.append(stored_receipt)
            return processed

        if str(receipt.get("agent_b_pubkey", "")) != self.public_key:
            return processed

        try:
            countersigned = self.countersign_receipt(receipt)
        except ValueError:
            return processed

        stored = self.store_receipt(countersigned)
        countersigned["proof_hash"] = stored["proof_hash"]
        processed.append(countersigned)

        if sender_did:
            response_payload: Dict[str, Any] = countersigned
            if was_encrypted:
                response_payload = self._wrap_for_transport(countersigned, str(receipt["agent_a_pubkey"]), encrypted=True)
            response_envelope = json.loads(pack_receipt(response_payload, self.private_key))
            try:
                self._relay_request(
                    "/send",
                    method="POST",
                    payload={"to": sender_did, "envelope": response_envelope},
                )
            except RuntimeError:
                pass

        return processed

    def _dispatch_receipt(self, receipt: Dict[str, Any]) -> None:
        for callback in list(self._receipt_callbacks):
            try:
                callback(receipt)
            except Exception:
                continue

    def _poll_once(self) -> None:
        self.check_inbox()

    def _on_ws_message(self, payload: Dict[str, Any]) -> None:
        envelopes = self._extract_envelopes(payload)
        for envelope in envelopes:
            for receipt in self._process_envelope(envelope):
                self._dispatch_receipt(receipt)

    def on_receipt(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._receipt_callbacks.append(callback)
        if self._live_connection is None:
            self._live_connection = LiveConnection(
                did=self.did,
                relay_manager=self._relay_manager,
                on_ws_message=self._on_ws_message,
                poll_once=self._poll_once,
                poll_interval_seconds=1.0,
            )
            self._live_connection.start()

    def check_inbox(self) -> list[Dict[str, Any]]:
        if not self._relay_available:
            return []

        payloads = self._relay_request_all("/receive", params={"did": self.did})
        if not payloads:
            try:
                payloads = [self._relay_request("/receive", params={"did": self.did})]
            except RuntimeError:
                return []

        envelope_map: Dict[str, Dict[str, Any]] = {}
        for payload in payloads:
            for envelope in self._extract_envelopes(payload):
                key = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
                envelope_map[key] = envelope

        processed: list[Dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for envelope in envelope_map.values():
            for receipt in self._process_envelope(envelope):
                proof_hash = str(receipt.get("proof_hash", ""))
                if proof_hash and proof_hash in seen_hashes:
                    continue
                if proof_hash:
                    seen_hashes.add(proof_hash)
                processed.append(receipt)
                self._dispatch_receipt(receipt)

        return processed

    def create_receipt(
        self,
        event_type: str,
        data: Dict[str, Any],
        counterparty_pubkey: str,
        *,
        parent_receipt_hash: str = "",
    ) -> Dict[str, Any]:
        if not str(counterparty_pubkey).strip():
            raise ValueError("counterparty_pubkey is required")
        if not isinstance(data, dict):
            raise ValueError("data must be a dict")

        receipt: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": str(event_type),
            "data": data,
            "parent_receipt_hash": str(parent_receipt_hash or ""),
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
            "parent_receipt_hash",
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
        return verify_receipt_dict(receipt)

    def store_receipt(self, receipt: Dict[str, Any]) -> Dict[str, Any]:
        if not self.verify_receipt(receipt):
            raise ValueError("Cannot store invalid receipt")
        receipt_to_store = dict(receipt)
        receipt_to_store.setdefault("parent_receipt_hash", "")
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

    def get_task_chain(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._ledger.get_task_chain(task_id)
        if not rows:
            return []

        parsed: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                data = json.loads(str(item.get("data_json", "{}")))
            except json.JSONDecodeError:
                data = {}
            item["data"] = data
            parsed.append(item)

        by_proof = {str(item.get("proof_hash", "")): item for item in parsed}
        roots = [
            item
            for item in parsed
            if not str(item.get("parent_receipt_hash", ""))
            or str(item.get("parent_receipt_hash", "")) not in by_proof
        ]

        ordered: List[Dict[str, Any]] = []
        current = roots[0] if roots else parsed[0]
        remaining = {id(item): item for item in parsed}
        while current and id(current) in remaining:
            ordered.append(current)
            remaining.pop(id(current), None)
            next_item = None
            for candidate in list(remaining.values()):
                if str(candidate.get("parent_receipt_hash", "")) == str(current.get("proof_hash", "")):
                    next_item = candidate
                    break
            current = next_item

        ordered.extend(remaining.values())
        return ordered

    def request_task(
        self,
        to: str,
        *,
        description: str,
        budget: float,
        deadline: Optional[str] = None,
        task_id: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        tid = str(task_id or new_task_id())
        typed = TaskRequest(task_id=tid, description=description, budget=budget, deadline=deadline or datetime.now(timezone.utc).isoformat())
        return self.send("TaskRequest", typed.as_data(), to=to, encrypted=encrypted)

    def accept_task(self, to: str, *, task_id: str, estimated_delivery: str, encrypted: bool = False) -> Dict[str, Any]:
        typed = TaskAccepted(task_id=task_id, estimated_delivery=estimated_delivery)
        parent = self._latest_task_receipt_hash(task_id)
        return self.send("TaskAccepted", typed.as_data(), to=to, encrypted=encrypted, parent_receipt_hash=parent)

    def deliver_task(
        self,
        task_id: str,
        *,
        artifact_hash: str,
        artifact_url: Optional[str] = None,
        to: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        typed = TaskDelivered(task_id=task_id, artifact_hash=artifact_hash, artifact_url=artifact_url)
        counterparty = to
        if not counterparty:
            pubkey = self._resolve_task_counterparty_pubkey(task_id)
            if not pubkey:
                raise ValueError("Could not infer task counterparty; provide 'to'")
            counterparty = public_key_to_did(pubkey)
        parent = self._latest_task_receipt_hash(task_id)
        return self.send("TaskDelivered", typed.as_data(), to=counterparty, encrypted=encrypted, parent_receipt_hash=parent)

    def confirm_task(
        self,
        task_id: str,
        *,
        rating: int,
        feedback: str,
        to: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        typed = TaskConfirmed(task_id=task_id, rating=rating, feedback=feedback)
        counterparty = to
        if not counterparty:
            pubkey = self._resolve_task_counterparty_pubkey(task_id)
            if not pubkey:
                raise ValueError("Could not infer task counterparty; provide 'to'")
            counterparty = public_key_to_did(pubkey)
        parent = self._latest_task_receipt_hash(task_id)
        return self.send("TaskConfirmed", typed.as_data(), to=counterparty, encrypted=encrypted, parent_receipt_hash=parent)

    def dispute_task(
        self,
        task_id: str,
        *,
        reason: str,
        to: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        typed = TaskDisputed(task_id=task_id, reason=reason)
        counterparty = to
        if not counterparty:
            pubkey = self._resolve_task_counterparty_pubkey(task_id)
            if not pubkey:
                raise ValueError("Could not infer task counterparty; provide 'to'")
            counterparty = public_key_to_did(pubkey)
        parent = self._latest_task_receipt_hash(task_id)
        return self.send("TaskDisputed", typed.as_data(), to=counterparty, encrypted=encrypted, parent_receipt_hash=parent)

    def trust_score(self) -> float:
        return float(self.get_trust_report(self.public_key)["score"])

    def get_trust_report(self, agent_pubkey: str) -> Dict[str, Any]:
        report = self._trust.build_report(agent_pubkey, self._ledger.get_receipts())
        return report

    # ── Supply Chain Trust (v5.0) ──────────────────────────────────────────

    def record_dependency(
        self,
        package: str,
        version: str,
        registry: str,
        source_hash: str,
        expected_hash: str,
        install_command: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a dependency installation with cryptographic proof.

        Args:
            package: Package name (e.g., "axios")
            version: Package version (e.g., "1.7.2")
            registry: Registry source (e.g., "npm", "pypi", "cargo")
            source_hash: SHA-256 hash of the downloaded tarball/artifact
            expected_hash: SHA-256 hash published by the registry
            install_command: The install command used (optional)
            environment: Environment description (optional)

        Returns:
            A receipt dict with all dependency installation details.
            receipt["data"]["hash_match"] is True if the package is safe.
        """
        return self._supply_chain.record_dependency(
            package=package,
            version=version,
            registry=registry,
            source_hash=source_hash,
            expected_hash=expected_hash,
            install_command=install_command,
            environment=environment,
        )

    def verify_dependency(
        self,
        package: str,
        version: str,
        against: str = "registry",
    ) -> bool:
        """Verify a previously recorded dependency.

        Args:
            package: Package name to look up
            version: Package version to check
            against: Verification mode — "registry" (default) checks stored
                     hash_match; a hex hash string compares against that hash.

        Returns:
            True if the dependency is verified safe, False otherwise.
        """
        return self._supply_chain.verify_dependency(
            package=package,
            version=version,
            against=against,
        )

    def dependency_audit(self) -> List[Dict[str, Any]]:
        """Return all dependency installation receipts for this agent.

        Returns:
            List of receipt dicts for all recorded dependencies.
        """
        return self._supply_chain.dependency_audit()
