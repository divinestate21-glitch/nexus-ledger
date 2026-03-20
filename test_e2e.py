from __future__ import annotations

import os
from pathlib import Path

from ledger import ActivityLedger
from proof_anchor import anchor_data, verify_anchor
from protocol import generate_ed25519_keypair
from registry import AgentRegistry


def test_e2e() -> None:
    os.environ["NEXUS_LEDGER_ANCHOR_MODE"] = "mock"
    db_path = "test_nexus.db"
    if Path(db_path).exists():
        Path(db_path).unlink()

    registry = AgentRegistry(db_path=db_path)
    ledger = ActivityLedger(db_path=db_path)

    try:
        _, mercury_pk = generate_ed25519_keypair()
        _, iris_pk = generate_ed25519_keypair()
        _, atlas_pk = generate_ed25519_keypair()

        mercury = registry.register("Mercury", ["market_intel", "coordination"], mercury_pk, agent_id="Mercury")
        iris = registry.register("Iris", ["app_development", "design"], iris_pk, agent_id="Iris")
        atlas = registry.register("Atlas", ["infrastructure", "security"], atlas_pk, agent_id="Atlas")

        ledger.append("agent_registered", mercury.to_dict(), agent_id=mercury.agent_id)
        ledger.append("agent_registered", iris.to_dict(), agent_id=iris.agent_id)
        ledger.append("agent_registered", atlas.to_dict(), agent_id=atlas.agent_id)

        matches = registry.discover("app_development")
        assert len(matches) == 1
        assert matches[0]["agent_id"] == "Iris"
        ledger.append(
            "agents_discovered",
            {"capability": "app_development", "agent_ids": [a["agent_id"] for a in matches]},
            agent_id="Mercury",
        )

        log_entry = ledger.append(
            "task_completed",
            {"task": "market research", "for": "Iris", "result": "delivered"},
            agent_id="Mercury",
        )
        assert log_entry["event_type"] == "task_completed"

        proof_data = {"task": "research", "result": "delivered", "for": "Iris"}
        anchor = anchor_data(proof_data, mode="mock", db_path=db_path)
        assert anchor["tx_signature"].startswith("mocktx_")
        assert len(anchor["proof_hash"]) == 64

        ledger.append(
            "proof_anchored",
            {"data": proof_data, "anchor": anchor},
            agent_id="Mercury",
            tx_signature=anchor["tx_signature"],
            proof_hash=anchor["proof_hash"],
        )

        verified = verify_anchor(anchor["tx_signature"], data=proof_data, mode="mock", db_path=db_path)
        assert verified["verified"] is True

        ledger.append(
            "proof_verified",
            {"verification": verified},
            agent_id="Mercury",
            tx_signature=anchor["tx_signature"],
            proof_hash=anchor["proof_hash"],
            status="verified",
        )

        entries = ledger.all()
        assert len(entries) >= 7
        assert any(e["event_type"] == "proof_anchored" for e in entries)
        assert any(e["event_type"] == "proof_verified" for e in entries)
        assert all(e["verified"] is True for e in entries)
    finally:
        registry.close()
        ledger.close()
