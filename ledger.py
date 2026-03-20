"""Local signed activity log stored in SQLite."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from nacl.signing import SigningKey

from protocol import sign


JsonDict = Dict[str, Any]
AgentKey = Union[str, Tuple[str, str], Dict[str, str]]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_from_private(private_key: str) -> str:
    return SigningKey(bytes.fromhex(private_key)).verify_key.encode().hex()


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
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_agent ON ledger(agent_pubkey)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON ledger(timestamp)")
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

    def close(self) -> None:
        self._conn.close()
