"""Append-only activity ledger with Ed25519-signed entries."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from protocol import generate_ed25519_keypair, new_id, sign_payload, utc_now, verify_signature


class ActivityLedger:
    def __init__(self, db_path: str = "nexus.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        private_key, public_key = generate_ed25519_keypair()
        self._signing_key = private_key
        self.public_key = public_key
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_ledger (
                entry_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                agent_id TEXT,
                status TEXT NOT NULL,
                tx_signature TEXT,
                proof_hash TEXT,
                payload_json TEXT NOT NULL,
                signature TEXT NOT NULL,
                signer_public_key TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_ledger(timestamp)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_event ON activity_ledger(event_type)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_agent ON activity_ledger(agent_id)")
        self._conn.commit()

    def append(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        agent_id: Optional[str] = None,
        status: str = "ok",
        tx_signature: Optional[str] = None,
        proof_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        entry = {
            "entry_id": new_id("entry"),
            "timestamp": utc_now(),
            "event_type": str(event_type).strip(),
            "agent_id": agent_id,
            "status": str(status).strip() or "ok",
            "tx_signature": tx_signature,
            "proof_hash": proof_hash,
            "payload": payload,
        }
        signature = sign_payload(entry, self._signing_key)
        entry["signature"] = signature
        entry["signer_public_key"] = self.public_key

        self._conn.execute(
            """
            INSERT INTO activity_ledger (
                entry_id, timestamp, event_type, agent_id, status, tx_signature,
                proof_hash, payload_json, signature, signer_public_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["entry_id"],
                entry["timestamp"],
                entry["event_type"],
                entry["agent_id"],
                entry["status"],
                entry["tx_signature"],
                entry["proof_hash"],
                json.dumps(entry["payload"]),
                signature,
                self.public_key,
            ),
        )
        self._conn.commit()
        return dict(entry)

    def all(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT entry_id, timestamp, event_type, agent_id, status, tx_signature,
                   proof_hash, payload_json, signature, signer_public_key
            FROM activity_ledger
            ORDER BY timestamp
            """
        ).fetchall()

        entries: List[Dict[str, Any]] = []
        for row in rows:
            entry = {
                "entry_id": str(row["entry_id"]),
                "timestamp": str(row["timestamp"]),
                "event_type": str(row["event_type"]),
                "agent_id": row["agent_id"],
                "status": str(row["status"]),
                "tx_signature": row["tx_signature"],
                "proof_hash": row["proof_hash"],
                "payload": json.loads(str(row["payload_json"])),
                "signature": str(row["signature"]),
                "signer_public_key": str(row["signer_public_key"]),
            }
            signable = {
                "entry_id": entry["entry_id"],
                "timestamp": entry["timestamp"],
                "event_type": entry["event_type"],
                "agent_id": entry["agent_id"],
                "status": entry["status"],
                "tx_signature": entry["tx_signature"],
                "proof_hash": entry["proof_hash"],
                "payload": entry["payload"],
            }
            entry["verified"] = verify_signature(signable, entry["signature"], entry["signer_public_key"])
            entries.append(entry)
        return entries

    def export_json(self) -> str:
        return json.dumps(self.all(), indent=2, sort_keys=True)

    def close(self) -> None:
        self._conn.close()
