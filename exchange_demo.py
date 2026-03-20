"""Cross-machine receipt exchange demo for Nexus Ledger."""

from __future__ import annotations

import json
from pathlib import Path

from nexus_ledger import Agent


def main() -> None:
    for file_name in ["agent_a.db", "agent_b.db"]:
        db_path = Path(file_name)
        if db_path.exists():
            db_path.unlink()

    agent_a = Agent("Mercury-A", keys_dir="keys_agent_a", db_path="agent_a.db")
    agent_b = Agent("Iris-B", keys_dir="keys_agent_b", db_path="agent_b.db")

    print("=== Cross-Machine Receipt Exchange ===")
    print(f"Agent A pubkey: {agent_a.public_key}")
    print(f"Agent B pubkey: {agent_b.public_key}")
    print()

    print("1) Agent A creates receipt")
    unsigned_by_b = agent_a.create_receipt(
        "delivered_research",
        {"task_id": "T-9001", "result": "complete"},
        agent_b.public_key,
    )
    print(json.dumps(unsigned_by_b, indent=2))
    print()

    print("2) Agent A exports receipt (simulate network send)")
    outbound_json = agent_a.export_receipt(unsigned_by_b)
    print(outbound_json)
    print()

    print("3) Agent B imports receipt")
    imported_for_b = agent_b.import_receipt(outbound_json)
    print(json.dumps(imported_for_b, indent=2))
    print()

    print("4) Agent B verifies Agent A signature and countersigns")
    countersigned = agent_b.countersign_receipt(imported_for_b)
    print(json.dumps(countersigned, indent=2))
    print()

    print("5) Agent B exports countersigned receipt back to Agent A")
    return_json = agent_b.export_receipt(countersigned)
    print(return_json)
    print()

    print("6) Agent A imports countersigned receipt")
    imported_for_a = agent_a.import_receipt(return_json)
    print(json.dumps(imported_for_a, indent=2))
    print()

    print("7) Agent A verifies both signatures")
    verified = agent_a.verify_receipt(imported_for_a)
    print(f"Both signatures valid: {verified}")
    print()

    if not verified:
        raise RuntimeError("Cross-machine verification failed")

    print("8) Both agents store the same verified receipt locally")
    stored_a = agent_a.store_receipt(imported_for_a)
    stored_b = agent_b.store_receipt(imported_for_a)
    print("Stored in Agent A ledger:")
    print(json.dumps(stored_a, indent=2))
    print("Stored in Agent B ledger:")
    print(json.dumps(stored_b, indent=2))
    print()

    print("9) Both agents can prove the transaction happened")
    print(f"Agent A receipts count: {len(agent_a._ledger.get_receipts())}")
    print(f"Agent B receipts count: {len(agent_b._ledger.get_receipts())}")
    print("Two agents. Two machines. One unfakeable receipt.")


if __name__ == "__main__":
    main()
