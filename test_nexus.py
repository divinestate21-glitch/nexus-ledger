from __future__ import annotations

import json
from pathlib import Path

from ledger import Ledger
from nexus_ledger import Agent
from proof_anchor import anchor, hash as hash_proof, verify as verify_proof
from protocol import generate_keypair, sign, verify


def test_protocol_sign_and_verify() -> None:
    private_key, public_key = generate_keypair()
    payload = {"task": "research", "result": "complete"}

    signature = sign(private_key, payload)

    assert verify(public_key, payload, signature) is True
    assert verify(public_key, {"task": "research", "result": "changed"}, signature) is False


def test_ledger_log_all_and_by_agent(tmp_path: Path) -> None:
    db_path = tmp_path / "nexus.db"
    ledger = Ledger(path=str(db_path))

    agent_a = generate_keypair()
    agent_b = generate_keypair()

    entry = ledger.log(agent_a, "delivered_research", {"topic": "market"}, counterparty_key=agent_b[1])
    assert entry["id"] == 1
    assert entry["event_type"] == "delivered_research"

    all_entries = ledger.all()
    assert len(all_entries) == 1
    assert all_entries[0]["signature"] == entry["signature"]

    agent_entries = ledger.by_agent(agent_a[1])
    assert len(agent_entries) == 1
    assert agent_entries[0]["agent_pubkey"] == agent_a[1]


def test_proof_hash_anchor_verify_mock(monkeypatch) -> None:
    monkeypatch.setenv("NEXUS_LEDGER_ANCHOR_MODE", "mock")

    payload = {"task": "research", "result": "complete"}
    expected_hash = hash_proof(payload)

    tx = anchor(payload)

    assert tx["hash"] == expected_hash
    assert tx["tx_signature"].startswith("mock_")
    assert verify_proof(payload, expected_hash) is True
    assert verify_proof(payload, "0" * 64) is False


def test_agent_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NEXUS_LEDGER_ANCHOR_MODE", "mock")

    db_path = tmp_path / "agent.db"
    keys_dir = tmp_path / "keys"

    mercury = Agent("Mercury", keys_dir=str(keys_dir), db_path=str(db_path))
    iris = Agent("Iris", keys_dir=str(keys_dir), db_path=str(db_path))

    mercury.log("delivered_research", {"topic": "market analysis"}, counterparty=iris)
    iris.log("received_research", {"status": "accepted"}, counterparty=mercury)

    tx = mercury.anchor({"task": "research", "result": "complete"})

    assert mercury.verify({"task": "research", "result": "complete"}, tx["hash"]) is True

    history = mercury.history()
    assert len(history) == 1
    assert history[0]["event_type"] == "delivered_research"

    all_activity = mercury.all_activity()
    assert len(all_activity) == 2

    mercury_key = json.loads((keys_dir / "mercury.json").read_text(encoding="utf-8"))
    assert mercury_key["public_key"] == mercury.public_key
