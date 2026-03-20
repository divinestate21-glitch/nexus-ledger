"""Single-file public API for Nexus Ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ledger import Ledger, receipt_proof_hash, receipt_signing_payload
from proof_anchor import anchor as anchor_proof
from proof_anchor import verify as verify_proof
from protocol import generate_keypair, sign, verify
from transport import (
    HTTPTransport,
    pack_receipt,
    public_key_to_did,
    receive_and_countersign,
    resolve_did,
    send_receipt as transport_send_receipt,
    unpack_receipt,
)


class Agent:
    def __init__(self, name: str, *, keys_dir: str = "keys", db_path: str = "nexus.db") -> None:
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("Agent name is required")

        self.name = clean_name
        self.keys_dir = Path(keys_dir)
        self.keys_dir.mkdir(parents=True, exist_ok=True)

        self.private_key, self.public_key = self._load_or_create_keys()
        self._ledger = Ledger(path=db_path)
        self._listener_server = None

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

    def history(self) -> list[Dict[str, Any]]:
        return self._ledger.by_agent(self.public_key)

    def all_activity(self) -> list[Dict[str, Any]]:
        return self._ledger.all()

    @property
    def did(self) -> str:
        return public_key_to_did(self.public_key)

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
