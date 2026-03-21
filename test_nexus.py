from __future__ import annotations

from pathlib import Path
from types import MethodType
from typing import Any, Dict

from nexus_ledger import Agent
from nexus_ledger.ledger import Ledger
from nexus_ledger.protocol import generate_keypair


class InMemoryRelay:
    def __init__(self) -> None:
        self._agents: Dict[str, Dict[str, str]] = {}
        self._inbox: Dict[str, list[Dict[str, Any]]] = {}

    def handle(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Dict[str, Any] | None = None,
        payload: Dict[str, Any] | None = None,
    ) -> Any:
        route = str(path).split("?", 1)[0]
        method = method.upper()
        params = params or {}
        payload = payload or {}

        if route == "/health":
            return {"status": "ok"}

        if route == "/register" and method == "POST":
            did = str(payload["did"])
            self._agents[did] = {
                "did": did,
                "name": str(payload["name"]),
                "pubkey": str(payload["pubkey"]),
            }
            self._inbox.setdefault(did, [])
            return {"ok": True}

        if route == "/discover":
            name = str(params.get("name", "")).strip().lower()
            agents = list(self._agents.values())
            if name:
                agents = [agent for agent in agents if agent["name"].strip().lower() == name]
            return {"agents": agents}

        if route == "/send" and method == "POST":
            to_did = str(payload["to"])
            self._inbox.setdefault(to_did, []).append({"envelope": payload["envelope"]})
            return {"queued": True}

        if route == "/receive":
            did = str(params.get("did", ""))
            messages = self._inbox.get(did, [])
            self._inbox[did] = []
            return {"messages": messages}

        raise RuntimeError(f"Unsupported relay route: {route} {method}")


def attach_relay(agent: Agent, relay: InMemoryRelay) -> None:
    def _relay_request(
        self: Agent,
        path: str,
        *,
        method: str = "GET",
        params: Dict[str, Any] | None = None,
        payload: Dict[str, Any] | None = None,
    ) -> Any:
        return relay.handle(path, method=method, params=params, payload=payload)

    agent._relay_request = MethodType(_relay_request, agent)  # type: ignore[attr-defined]
    agent._relay_available = True  # type: ignore[attr-defined]
    agent._relay_request(  # type: ignore[attr-defined]
        "/register",
        method="POST",
        payload={"did": agent.did, "name": agent.name, "pubkey": agent.public_key},
    )


def test_agent_creation(tmp_path: Path) -> None:
    agent = Agent("Mercury", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "agent.db"), relay="memory://relay")
    assert agent.name == "Mercury"
    assert len(agent.private_key) == 64
    assert len(agent.public_key) == 64
    assert agent.did.startswith("did:key:z")


def test_logging(tmp_path: Path) -> None:
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")

    row = mercury.log("task_completed", {"task": "R-1"}, counterparty=iris)
    history = mercury.history()

    assert row["event_type"] == "task_completed"
    assert len(history) == 1
    assert history[0]["counterparty_pubkey"] == iris.public_key


def test_receipt_create_countersign_verify(tmp_path: Path) -> None:
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")

    draft = mercury.create_receipt("delivery_receipt", {"task_id": "T-1"}, iris.public_key)
    final = iris.countersign_receipt(draft)

    assert mercury.verify_receipt(final) is True
    assert iris.verify_receipt(final) is True


def test_export_import(tmp_path: Path) -> None:
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")

    draft = mercury.create_receipt("delivery_receipt", {"task_id": "T-2"}, iris.public_key)
    final = iris.countersign_receipt(draft)

    encoded = iris.export_receipt(final)
    decoded = mercury.import_receipt(encoded)

    assert decoded == final
    assert mercury.verify_receipt(decoded) is True


def test_relay_connectivity(tmp_path: Path) -> None:
    relay = InMemoryRelay()

    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys_a"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys_b"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")
    attach_relay(mercury, relay)
    attach_relay(iris, relay)

    found = mercury.find("Iris")
    assert found is not None
    assert found["did"] == iris.did

    outbound = mercury.send("delivery_receipt", {"task_id": "T-3", "result": "ok"}, to="Iris")
    assert outbound["agent_b_pubkey"] == iris.public_key

    iris_inbox = iris.check_inbox()
    mercury_inbox = mercury.check_inbox()

    assert len(iris_inbox) == 1
    assert len(mercury_inbox) == 1
    assert mercury.verify_receipt(mercury_inbox[0]) is True


def test_ledger_direct_usage(tmp_path: Path) -> None:
    ledger = Ledger(path=str(tmp_path / "local.db"))
    agent_a = generate_keypair()
    agent_b = generate_keypair()

    entry = ledger.log(agent_a, "manual_log", {"k": "v"}, counterparty_key=agent_b[1])
    rows = ledger.all()

    assert entry["event_type"] == "manual_log"
    assert len(rows) == 1
    assert rows[0]["agent_pubkey"] == agent_a[1]
