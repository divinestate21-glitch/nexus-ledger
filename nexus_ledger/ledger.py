"""Local signed activity log stored in SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from nacl.signing import SigningKey

from .protocol import sign


JsonDict = Dict[str, Any]
AgentKey = Union[str, Tuple[str, str], Dict[str, str]]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_from_private(private_key: str) -> str:
    return SigningKey(bytes.fromhex(private_key)).verify_key.encode().hex()


def _canonical_json(data_dict: JsonDict) -> str:
    return json.dumps(data_dict, sort_keys=True, separators=(",", ":"))


def receipt_signing_payload(receipt: JsonDict) -> JsonDict:
    return {
        "timestamp": str(receipt["timestamp"]),
        "event_type": str(receipt["event_type"]),
        "data": receipt["data"],
        "agent_a_pubkey": str(receipt["agent_a_pubkey"]),
        "agent_b_pubkey": str(receipt["agent_b_pubkey"]),
    }


def receipt_proof_hash(receipt: JsonDict) -> str:
    stored_payload = {
        "timestamp": str(receipt["timestamp"]),
        "event_type": str(receipt["event_type"]),
        "data": receipt["data"],
        "agent_a_pubkey": str(receipt["agent_a_pubkey"]),
        "agent_a_signature": str(receipt["agent_a_signature"]),
        "agent_b_pubkey": str(receipt["agent_b_pubkey"]),
        "agent_b_signature": str(receipt["agent_b_signature"]),
    }
    return hashlib.sha256(_canonical_json(stored_payload).encode("utf-8")).hexdigest()


def _resolve_keys(agent_key: AgentKey) -> Tuple[str, str]:
    if isinstance(agent_key, tuple) and len(agent_key) == 2:
        private_key, public_key = agent_key
        return str(private_key), str(public_key)

    if isinstance(agent_key, dict):
        private_key = str(agent_key.get("private_key", ""))
        public_key = str(agent_key.get("public_key", ""))
        if private_key and not public_key:
            public_key = _public_from_private(private_key)
        if not private_key or not public_key:
            raise ValueError("agent_key dict must contain private_key and public_key")
        return private_key, public_key

    if isinstance(agent_key, str):
        private_key = agent_key
        return private_key, _public_from_private(private_key)

    raise TypeError("agent_key must be private key string, (private, public), or key dict")


def _resolve_counterparty(counterparty_key: Optional[AgentKey]) -> Optional[str]:
    if counterparty_key is None:
        return None
    if isinstance(counterparty_key, tuple) and len(counterparty_key) == 2:
        return str(counterparty_key[1])
    if isinstance(counterparty_key, dict):
        public_key = str(counterparty_key.get("public_key", ""))
        if public_key:
            return public_key
        private_key = str(counterparty_key.get("private_key", ""))
        return _public_from_private(private_key) if private_key else None
    return str(counterparty_key)


class Ledger:
    def __init__(self, path: str = "nexus.db") -> None:
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_pubkey TEXT NOT NULL,
                counterparty_pubkey TEXT,
                event_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                signature TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                agent_a_pubkey TEXT NOT NULL,
                agent_a_signature TEXT NOT NULL,
                agent_b_pubkey TEXT NOT NULL,
                agent_b_signature TEXT NOT NULL,
                proof_hash TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_agent ON ledger(agent_pubkey)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON ledger(timestamp)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_a ON receipts(agent_a_pubkey)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_b ON receipts(agent_b_pubkey)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_timestamp ON receipts(timestamp)")
        self._conn.commit()

    def log(
        self,
        agent_key: AgentKey,
        event_type: str,
        data: JsonDict,
        counterparty_key: Optional[AgentKey] = None,
    ) -> JsonDict:
        private_key, public_key = _resolve_keys(agent_key)
        counterparty_pubkey = _resolve_counterparty(counterparty_key)
        timestamp = _utc_timestamp()

        signed_payload: JsonDict = {
            "timestamp": timestamp,
            "agent_pubkey": public_key,
            "counterparty_pubkey": counterparty_pubkey,
            "event_type": str(event_type),
            "data": data,
        }
        signature = sign(private_key, signed_payload)

        cur = self._conn.execute(
            """
            INSERT INTO ledger (
                timestamp, agent_pubkey, counterparty_pubkey, event_type, data_json, signature
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                public_key,
                counterparty_pubkey,
                str(event_type),
                json.dumps(data, sort_keys=True),
                signature,
            ),
        )
        self._conn.commit()

        return {
            "id": int(cur.lastrowid),
            "timestamp": timestamp,
            "agent_pubkey": public_key,
            "counterparty_pubkey": counterparty_pubkey,
            "event_type": str(event_type),
            "data_json": json.dumps(data, sort_keys=True),
            "signature": signature,
        }

    def all(self) -> List[JsonDict]:
        rows = self._conn.execute(
            """
            SELECT id, timestamp, agent_pubkey, counterparty_pubkey, event_type, data_json, signature
            FROM ledger
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def by_agent(self, public_key: str) -> List[JsonDict]:
        rows = self._conn.execute(
            """
            SELECT id, timestamp, agent_pubkey, counterparty_pubkey, event_type, data_json, signature
            FROM ledger
            WHERE agent_pubkey = ?
            ORDER BY id ASC
            """,
            (public_key,),
        ).fetchall()
        return [dict(row) for row in rows]

    def store_receipt(self, receipt_dict: JsonDict) -> JsonDict:
        required_fields = [
            "timestamp",
            "event_type",
            "data",
            "agent_a_pubkey",
            "agent_a_signature",
            "agent_b_pubkey",
            "agent_b_signature",
        ]
        missing = [field for field in required_fields if field not in receipt_dict]
        if missing:
            raise ValueError(f"Receipt missing required fields: {', '.join(missing)}")

        data = receipt_dict["data"]
        if not isinstance(data, dict):
            raise ValueError("receipt['data'] must be a dict")

        normalized_receipt = {
            "timestamp": str(receipt_dict["timestamp"]),
            "event_type": str(receipt_dict["event_type"]),
            "data": data,
            "agent_a_pubkey": str(receipt_dict["agent_a_pubkey"]),
            "agent_a_signature": str(receipt_dict["agent_a_signature"]),
            "agent_b_pubkey": str(receipt_dict["agent_b_pubkey"]),
            "agent_b_signature": str(receipt_dict["agent_b_signature"]),
        }
        proof_hash = str(receipt_dict.get("proof_hash") or receipt_proof_hash(normalized_receipt))

        cur = self._conn.execute(
            """
            INSERT INTO receipts (
                timestamp,
                event_type,
                data_json,
                agent_a_pubkey,
                agent_a_signature,
                agent_b_pubkey,
                agent_b_signature,
                proof_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_receipt["timestamp"],
                normalized_receipt["event_type"],
                _canonical_json(data),
                normalized_receipt["agent_a_pubkey"],
                normalized_receipt["agent_a_signature"],
                normalized_receipt["agent_b_pubkey"],
                normalized_receipt["agent_b_signature"],
                proof_hash,
            ),
        )
        self._conn.commit()

        return {
            "id": int(cur.lastrowid),
            "timestamp": normalized_receipt["timestamp"],
            "event_type": normalized_receipt["event_type"],
            "data_json": _canonical_json(data),
            "agent_a_pubkey": normalized_receipt["agent_a_pubkey"],
            "agent_a_signature": normalized_receipt["agent_a_signature"],
            "agent_b_pubkey": normalized_receipt["agent_b_pubkey"],
            "agent_b_signature": normalized_receipt["agent_b_signature"],
            "proof_hash": proof_hash,
        }

    def get_receipts(self) -> List[JsonDict]:
        rows = self._conn.execute(
            """
            SELECT
                id,
                timestamp,
                event_type,
                data_json,
                agent_a_pubkey,
                agent_a_signature,
                agent_b_pubkey,
                agent_b_signature,
                proof_hash
            FROM receipts
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_receipts_with(self, counterparty_pubkey: str) -> List[JsonDict]:
        rows = self._conn.execute(
            """
            SELECT
                id,
                timestamp,
                event_type,
                data_json,
                agent_a_pubkey,
                agent_a_signature,
                agent_b_pubkey,
                agent_b_signature,
                proof_hash
            FROM receipts
            WHERE agent_a_pubkey = ? OR agent_b_pubkey = ?
            ORDER BY id ASC
            """,
            (counterparty_pubkey, counterparty_pubkey),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
