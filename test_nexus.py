from __future__ import annotations

import json
from pathlib import Path

from ledger import Ledger
from nexus_ledger import Agent
from proof_anchor import anchor, hash as hash_proof, verify as verify_proof
from protocol import generate_keypair, sign, verify
from transport import FileTransport, pack_receipt, public_key_to_did, resolve_did, unpack_receipt


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


def test_create_receipt(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys"
    mercury = Agent("Mercury", keys_dir=str(keys_dir), db_path=str(tmp_path / "a.db"))
    iris = Agent("Iris", keys_dir=str(keys_dir), db_path=str(tmp_path / "b.db"))

    receipt = mercury.create_receipt("delivered_research", {"task_id": "123"}, iris.public_key)

    assert receipt["event_type"] == "delivered_research"
    assert receipt["agent_a_pubkey"] == mercury.public_key
    assert receipt["agent_b_pubkey"] == iris.public_key
    assert "agent_a_signature" in receipt
    assert "agent_b_signature" not in receipt


def test_countersign_receipt(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys"
    mercury = Agent("Mercury", keys_dir=str(keys_dir), db_path=str(tmp_path / "a.db"))
    iris = Agent("Iris", keys_dir=str(keys_dir), db_path=str(tmp_path / "b.db"))

    receipt = mercury.create_receipt("delivered_research", {"task_id": "123"}, iris.public_key)
    countersigned = iris.countersign_receipt(receipt)

    assert countersigned["agent_a_signature"] == receipt["agent_a_signature"]
    assert "agent_b_signature" in countersigned
    assert mercury.verify_receipt(countersigned) is True


def test_verify_receipt_valid_and_invalid(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys"
    mercury = Agent("Mercury", keys_dir=str(keys_dir), db_path=str(tmp_path / "a.db"))
    iris = Agent("Iris", keys_dir=str(keys_dir), db_path=str(tmp_path / "b.db"))

    receipt = mercury.create_receipt("delivered_research", {"task_id": "123"}, iris.public_key)
    valid_receipt = iris.countersign_receipt(receipt)
    assert mercury.verify_receipt(valid_receipt) is True
    assert iris.verify_receipt(valid_receipt) is True

    invalid_receipt = dict(valid_receipt)
    invalid_receipt["data"] = {"task_id": "tampered"}
    assert mercury.verify_receipt(invalid_receipt) is False


def test_receipt_export_import_round_trip(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys"
    mercury = Agent("Mercury", keys_dir=str(keys_dir), db_path=str(tmp_path / "a.db"))
    iris = Agent("Iris", keys_dir=str(keys_dir), db_path=str(tmp_path / "b.db"))

    receipt = mercury.create_receipt("delivered_research", {"task_id": "123"}, iris.public_key)
    countersigned = iris.countersign_receipt(receipt)

    encoded = iris.export_receipt(countersigned)
    decoded = mercury.import_receipt(encoded)
    assert decoded == countersigned
    assert mercury.verify_receipt(decoded) is True


def test_store_and_retrieve_receipts(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys"
    mercury = Agent("Mercury", keys_dir=str(keys_dir), db_path=str(tmp_path / "a.db"))
    iris = Agent("Iris", keys_dir=str(keys_dir), db_path=str(tmp_path / "b.db"))

    receipt = mercury.create_receipt("delivered_research", {"task_id": "123"}, iris.public_key)
    countersigned = iris.countersign_receipt(receipt)

    stored_a = mercury.store_receipt(countersigned)
    stored_b = iris.store_receipt(countersigned)
    assert stored_a["proof_hash"] == stored_b["proof_hash"]

    a_receipts = mercury._ledger.get_receipts()
    b_receipts = iris._ledger.get_receipts()
    assert len(a_receipts) == 1
    assert len(b_receipts) == 1

    with_iris = mercury._ledger.get_receipts_with(iris.public_key)
    with_mercury = iris._ledger.get_receipts_with(mercury.public_key)
    assert len(with_iris) == 1
    assert len(with_mercury) == 1
    assert with_iris[0]["proof_hash"] == stored_a["proof_hash"]


def test_did_generation_from_ed25519_pubkey() -> None:
    _, public_key = generate_keypair()
    did = public_key_to_did(public_key)

    assert did.startswith("did:key:z")


def test_did_resolution_back_to_pubkey() -> None:
    _, public_key = generate_keypair()
    did = public_key_to_did(public_key)

    assert resolve_did(did).hex() == public_key


def test_envelope_pack_unpack_round_trip(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys"
    sender = Agent("Sender", keys_dir=str(keys_dir), db_path=str(tmp_path / "sender.db"))
    receiver = Agent("Receiver", keys_dir=str(keys_dir), db_path=str(tmp_path / "receiver.db"))

    receipt = sender.create_receipt("delivered_research", {"result": "complete"}, receiver.public_key)
    envelope = pack_receipt(receipt, sender.private_key)
    unpacked = unpack_receipt(envelope, expected_sender_did=sender.did)

    assert unpacked == receipt


def test_file_transport_send_receive(tmp_path: Path) -> None:
    transport = FileTransport()
    path = tmp_path / "envelope.json"
    envelope = json.dumps({"sender_did": "did:key:zTest", "signature": "abc", "payload": {"x": 1}})

    written_path = transport.send(str(path), envelope)
    received = transport.receive(written_path)

    assert received == envelope


def test_full_receipt_exchange_via_file_transport(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys"
    agent_a = Agent("Agent-A", keys_dir=str(keys_dir), db_path=str(tmp_path / "a.db"))
    agent_b = Agent("Agent-B", keys_dir=str(keys_dir), db_path=str(tmp_path / "b.db"))
    transport = FileTransport()

    outbound_path = tmp_path / "outbound.json"
    inbound_path = tmp_path / "return.json"

    outbound_envelope = agent_a.send_receipt(
        agent_b.did,
        "delivered_research",
        {"task_id": "T-42", "result": "complete"},
        transport="file",
        endpoint=str(outbound_path),
    )

    received_by_b = transport.receive(str(outbound_path))
    countersigned = agent_b.receive_receipt(received_by_b)
    return_envelope = pack_receipt(countersigned, agent_b.private_key)
    transport.send(str(inbound_path), return_envelope)

    received_by_a = transport.receive(str(inbound_path))
    final_receipt = agent_a.receive_receipt(received_by_a)

    assert outbound_envelope
    assert agent_a.verify_receipt(final_receipt) is True
    assert agent_b.verify_receipt(final_receipt) is True
    assert len(agent_a._ledger.get_receipts()) == 1
    assert len(agent_b._ledger.get_receipts()) == 1
