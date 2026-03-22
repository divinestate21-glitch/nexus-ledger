from __future__ import annotations

import json
from pathlib import Path
from types import MethodType
from typing import Any, Dict

from nexus_ledger import Agent
from nexus_ledger.agent import verify_receipt_dict
from nexus_ledger.cli import build_parser
from nexus_ledger.ledger import Ledger
from nexus_ledger.protocol import generate_keypair
from nexus_ledger.relay_manager import RelayManager
from nexus_ledger.receipt_types import TaskConfirmed


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
    assert len(agent.relays) >= 2


def test_receipt_create_countersign_verify_with_parent_hash(tmp_path: Path) -> None:
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")

    draft = mercury.create_receipt("TaskRequest", {"task_id": "T-1"}, iris.public_key)
    final = iris.countersign_receipt(draft)

    assert draft["parent_receipt_hash"] == ""
    assert mercury.verify_receipt(final) is True
    assert iris.verify_receipt(final) is True


def test_relay_task_chain_flow(tmp_path: Path) -> None:
    relay = InMemoryRelay()
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys_a"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys_b"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")
    attach_relay(mercury, relay)
    attach_relay(iris, relay)

    request = mercury.request_task("Iris", description="market research", budget=100, task_id="T-3")
    iris.check_inbox()
    accept = iris.accept_task("Mercury", task_id="T-3", estimated_delivery="2026-03-22T12:00:00+00:00")
    mercury.check_inbox()
    deliver = iris.deliver_task("T-3", artifact_hash="sha256:abc", to="Mercury")
    mercury.check_inbox()
    confirm = mercury.confirm_task("T-3", rating=5, feedback="excellent", to="Iris")
    iris.check_inbox()
    mercury.check_inbox()

    chain = mercury.get_task_chain("T-3")
    assert len(chain) >= 4
    assert request["parent_receipt_hash"] == ""
    assert accept["parent_receipt_hash"] != ""
    assert deliver["parent_receipt_hash"] != ""
    assert confirm["parent_receipt_hash"] != ""


def test_encrypted_receipt_round_trip(tmp_path: Path) -> None:
    relay = InMemoryRelay()
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys_a"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys_b"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")
    attach_relay(mercury, relay)
    attach_relay(iris, relay)

    mercury.send("TaskRequest", {"task_id": "enc-1", "description": "x", "budget": 1, "deadline": "2026-03-22T12:00:00+00:00"}, to="Iris", encrypted=True)
    iris_inbox = iris.check_inbox()
    mercury_inbox = mercury.check_inbox()

    assert len(iris_inbox) == 1
    assert len(mercury_inbox) == 1
    assert verify_receipt_dict(mercury_inbox[0]) is True


def test_live_callback_receives_processed_receipts(tmp_path: Path) -> None:
    relay = InMemoryRelay()
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys_a"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys_b"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")
    attach_relay(mercury, relay)
    attach_relay(iris, relay)

    seen: list[Dict[str, Any]] = []
    mercury.on_receipt(lambda receipt: seen.append(receipt))

    iris.send("TaskRequest", {"task_id": "cb-1", "description": "x", "budget": 1, "deadline": "2026-03-22T12:00:00+00:00"}, to="Mercury")
    mercury.check_inbox()

    assert len(seen) >= 1


def test_trust_score_and_report(tmp_path: Path) -> None:
    ledger = Ledger(path=str(tmp_path / "trust.db"))
    alice = generate_keypair()
    bob = generate_keypair()

    receipt = {
        "timestamp": "2026-03-21T20:00:00+00:00",
        "event_type": "TaskConfirmed",
        "data": TaskConfirmed(task_id="t-1", rating=5, feedback="great").as_data(),
        "parent_receipt_hash": "",
        "agent_a_pubkey": alice[1],
        "agent_a_signature": "00",
        "agent_b_pubkey": bob[1],
        "agent_b_signature": "00",
    }

    # Store a syntactically valid row for trust scoring purposes.
    ledger._conn.execute(
        """
        INSERT INTO receipts (
            timestamp, event_type, data_json, task_id, parent_receipt_hash,
            agent_a_pubkey, agent_a_signature, agent_b_pubkey, agent_b_signature, proof_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt["timestamp"],
            receipt["event_type"],
            json.dumps(receipt["data"], sort_keys=True),
            "t-1",
            "",
            receipt["agent_a_pubkey"],
            receipt["agent_a_signature"],
            receipt["agent_b_pubkey"],
            receipt["agent_b_signature"],
            "proof",
        ),
    )
    ledger._conn.commit()

    agent = Agent("Mercury", keys_dir=str(tmp_path / "keys"), db_path=str(tmp_path / "trust.db"), relay="memory://relay")
    report = agent.get_trust_report(alice[1])
    assert 0.0 <= report["score"] <= 1.0
    assert report["factors"]["total_receipts"] >= 1


def test_cli_parser_commands() -> None:
    parser = build_parser()

    args = parser.parse_args(["init"])
    assert args.command == "init"

    args = parser.parse_args(["send", "Iris", "TaskRequest", '{"task_id":"t1","description":"x","budget":1,"deadline":"2026-03-22T12:00:00+00:00"}'])
    assert args.command == "send"

    args = parser.parse_args(["verify", '{"event_type":"TaskConfirmed"}'])
    assert args.command == "verify"


def test_relay_manager_failover_order() -> None:
    manager = RelayManager(["http://primary:8765", "http://secondary:8765"])

    calls: list[str] = []

    def fake_request(relay: str, path: str, *, method: str = "GET", params: Dict[str, Any] | None = None, payload: Dict[str, Any] | None = None) -> Any:
        calls.append(relay)
        if "primary" in relay:
            raise RuntimeError("down")
        return {"ok": True}

    manager._request_to_relay = fake_request  # type: ignore[assignment]
    result = manager.request("/health")

    assert result == {"ok": True}
    assert calls == ["http://primary:8765", "http://secondary:8765"]
