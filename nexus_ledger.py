"""Single-file public API for Nexus Ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from ledger import Ledger
from proof_anchor import anchor as anchor_proof
from proof_anchor import verify as verify_proof
from protocol import generate_keypair


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
