from __future__ import annotations

import json
from pathlib import Path
from types import MethodType
from typing import Any, Dict

from nexus_ledger import Agent
from nexus_ledger.agent import verify_receipt_dict
from nexus_ledger.cli import build_parser
from nexus_ledger.crypto import decrypt_payload, encrypt_payload
from nexus_ledger.erc8004 import TRANSFER_TOPIC
from nexus_ledger.eth_anchor import batch_anchor, receipt_hash, verify_receipt
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


def _sample_receipt(task_id: str = "t-1") -> Dict[str, Any]:
    a = generate_keypair()
    b = generate_keypair()
    return {
        "timestamp": "2026-03-23T00:00:00+00:00",
        "event_type": "TaskConfirmed",
        "data": {"task_id": task_id, "rating": 5, "feedback": "solid"},
        "parent_receipt_hash": "",
        "agent_a_pubkey": a[1],
        "agent_a_signature": "00",
        "agent_b_pubkey": b[1],
        "agent_b_signature": "00",
    }


def test_erc8004_module_identity_transfer_topic() -> None:
    from nexus_ledger import erc8004

    assert hasattr(erc8004, "TRANSFER_TOPIC")
    assert erc8004.TRANSFER_TOPIC == TRANSFER_TOPIC
    assert TRANSFER_TOPIC == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def test_eth_anchor_receipt_hash_matches_manual_sha256() -> None:
    import hashlib

    receipt = _sample_receipt("hash-1")
    got = receipt_hash(receipt)

    manual_payload = {
        "timestamp": receipt["timestamp"],
        "event_type": receipt["event_type"],
        "data": receipt["data"],
        "agent_a_pubkey": receipt["agent_a_pubkey"],
        "agent_b_pubkey": receipt["agent_b_pubkey"],
        "agent_a_signature": receipt["agent_a_signature"],
        "agent_b_signature": receipt["agent_b_signature"],
        "parent_receipt_hash": receipt["parent_receipt_hash"],
    }
    manual = hashlib.sha256(json.dumps(manual_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    assert got == manual
    assert len(got) == 64


def test_eth_anchor_verify_receipt_positive() -> None:
    receipt = _sample_receipt("verify-ok")
    expected = receipt_hash(receipt)
    assert verify_receipt(receipt, expected) is True


def test_eth_anchor_verify_receipt_negative() -> None:
    receipt = _sample_receipt("verify-bad")
    bad_expected = "0" * 64
    assert verify_receipt(receipt, bad_expected) is False


def test_eth_anchor_batch_anchor_multiple_receipts_local_only() -> None:
    receipts = [_sample_receipt("b1"), _sample_receipt("b2"), _sample_receipt("b3")]

    result = batch_anchor(receipts)

    assert result["status"] == "local_only"
    assert "batch_info" in result
    assert result["batch_info"]["receipt_count"] == 3
    assert len(result["batch_info"]["individual_hashes"]) == 3
    assert len(result["batch_info"]["combined_hash"]) == 64


def test_encryption_round_trip_encrypt_then_decrypt_message() -> None:
    sender_priv, sender_pub = generate_keypair()
    recipient_priv, recipient_pub = generate_keypair()
    payload = {"msg": "hello", "n": 7}

    encrypted = encrypt_payload(payload, sender_priv, recipient_pub)
    decrypted = decrypt_payload(encrypted, recipient_priv)

    assert encrypted["encrypted"] is True
    assert encrypted["sender_pubkey"] == sender_pub
    assert encrypted["recipient_pubkey"] == recipient_pub
    assert decrypted == payload


def test_bad_signature_rejection_for_tampered_receipt(tmp_path: Path) -> None:
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys_a"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys_b"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")

    draft = mercury.create_receipt("TaskRequest", {"task_id": "tamper-1"}, iris.public_key)
    signed = iris.countersign_receipt(draft)
    assert mercury.verify_receipt(signed) is True

    tampered = dict(signed)
    tampered["data"] = dict(signed["data"])
    tampered["data"]["task_id"] = "tamper-2"

    assert mercury.verify_receipt(tampered) is False


def test_cli_parser_init_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["init"])
    assert args.command == "init"


def test_cli_parser_send_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["send", "Iris", "TaskRequest", '{"task_id":"t1","description":"x","budget":1,"deadline":"2026-03-22T12:00:00+00:00"}'])
    assert args.command == "send"


def test_cli_parser_other_subcommands() -> None:
    parser = build_parser()

    assert parser.parse_args(["inbox"]).command == "inbox"
    assert parser.parse_args(["history"]).command == "history"
    assert parser.parse_args(["agents"]).command == "agents"
    assert parser.parse_args(["verify", '{"event_type":"TaskConfirmed"}']).command == "verify"
    assert parser.parse_args(["trust"]).command == "trust"
    assert parser.parse_args(["task-chain", "task-123"]).command == "task-chain"
    assert parser.parse_args(["anchor"]).command == "anchor"
    assert parser.parse_args(["anchor-all"]).command == "anchor-all"


def test_agent_trust_score_between_zero_and_one(tmp_path: Path) -> None:
    relay = InMemoryRelay()
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys_a"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys_b"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")
    attach_relay(mercury, relay)
    attach_relay(iris, relay)

    mercury.request_task("Iris", description="trust task", budget=10, task_id="trust-1")
    iris.check_inbox()
    iris.accept_task("Mercury", task_id="trust-1", estimated_delivery="2026-03-22T12:00:00+00:00")
    mercury.check_inbox()
    iris.deliver_task("trust-1", artifact_hash="sha256:trust", to="Mercury")
    mercury.check_inbox()
    mercury.confirm_task("trust-1", rating=4, feedback="good", to="Iris")
    iris.check_inbox()

    score = iris.trust_score()
    assert 0.0 <= score <= 1.0


def test_agent_anchor_to_eth_local_only_structure(tmp_path: Path) -> None:
    relay = InMemoryRelay()
    mercury = Agent("Mercury", keys_dir=str(tmp_path / "keys_a"), db_path=str(tmp_path / "mercury.db"), relay="memory://relay")
    iris = Agent("Iris", keys_dir=str(tmp_path / "keys_b"), db_path=str(tmp_path / "iris.db"), relay="memory://relay")
    attach_relay(mercury, relay)
    attach_relay(iris, relay)

    mercury.request_task("Iris", description="anchor", budget=1, task_id="anchor-1")
    iris.check_inbox()
    mercury.check_inbox()
    result = mercury.anchor_to_eth(chain="sepolia")

    assert result["status"] == "local_only"
    assert "receipt_hash" in result
    assert "message" in result
    assert "verify_command" in result


def test_relay_manager_failover_tries_fallback_after_primary_failure() -> None:
    manager = RelayManager(["http://primary:8765", "http://fallback:8765"])
    calls: list[str] = []

    def fake_request(relay: str, path: str, *, method: str = "GET", params: Dict[str, Any] | None = None, payload: Dict[str, Any] | None = None) -> Any:
        calls.append(relay)
        if relay.endswith("primary:8765"):
            raise RuntimeError("primary down")
        return {"ok": True, "relay": relay}

    manager._request_to_relay = fake_request  # type: ignore[assignment]
    result = manager.request("/health")

    assert result["ok"] is True
    assert calls == ["http://primary:8765", "http://fallback:8765"]
