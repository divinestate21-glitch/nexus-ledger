"""Agent registry with capability-based discovery."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from protocol import AgentIdentity, new_id, utc_now


class AgentRegistry:
    def __init__(self, db_path: str = "nexus.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                public_key TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                registered_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name)")
        self._conn.commit()

    def register(self, name: str, capabilities: List[str], public_key: str, agent_id: Optional[str] = None) -> AgentIdentity:
        clean_caps = sorted({str(cap).strip() for cap in capabilities if str(cap).strip()})
        if not clean_caps:
            raise ValueError("capabilities must contain at least one non-empty capability")
        resolved_id = (agent_id or new_id("agent")).strip()
        if not resolved_id:
            raise ValueError("agent_id resolved to empty value")

        identity = AgentIdentity(
            agent_id=resolved_id,
            name=str(name).strip() or resolved_id,
            public_key=str(public_key).strip(),
            capabilities=clean_caps,
            registered_at=utc_now(),
        )
        self._conn.execute(
            """
            INSERT INTO agents (agent_id, name, public_key, capabilities_json, registered_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                name=excluded.name,
                public_key=excluded.public_key,
                capabilities_json=excluded.capabilities_json
            """,
            (
                identity.agent_id,
                identity.name,
                identity.public_key,
                json.dumps(identity.capabilities),
                identity.registered_at,
            ),
        )
        self._conn.commit()
        return identity

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        row = self._conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        if row is None:
            return None
        return AgentIdentity(
            agent_id=str(row["agent_id"]),
            name=str(row["name"]),
            public_key=str(row["public_key"]),
            capabilities=json.loads(str(row["capabilities_json"])),
            registered_at=str(row["registered_at"]),
        )

    def discover(self, capability: str) -> List[Dict[str, Any]]:
        needle = str(capability).strip().lower()
        rows = self._conn.execute("SELECT * FROM agents ORDER BY registered_at").fetchall()
        matches: List[Dict[str, Any]] = []
        for row in rows:
            caps = [str(c) for c in json.loads(str(row["capabilities_json"]))]
            if needle in {c.lower() for c in caps}:
                matches.append(
                    AgentIdentity(
                        agent_id=str(row["agent_id"]),
                        name=str(row["name"]),
                        public_key=str(row["public_key"]),
                        capabilities=caps,
                        registered_at=str(row["registered_at"]),
                    ).to_dict()
                )
        return matches

    def all_agents(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM agents ORDER BY registered_at").fetchall()
        return [
            AgentIdentity(
                agent_id=str(row["agent_id"]),
                name=str(row["name"]),
                public_key=str(row["public_key"]),
                capabilities=json.loads(str(row["capabilities_json"])),
                registered_at=str(row["registered_at"]),
            ).to_dict()
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
