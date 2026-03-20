"""Standalone demo for Nexus Ledger."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from nexus_ledger import Agent


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexus Ledger demo")
    parser.add_argument("--mainnet", action="store_true", help="Anchor proof on Solana mainnet-beta")
    args = parser.parse_args()

    for file_name in ["demo_nexus.db"]:
        db_path = Path(file_name)
        if db_path.exists():
            db_path.unlink()

    if args.mainnet:
        os.environ["NEXUS_LEDGER_ANCHOR_MODE"] = "mainnet"
    else:
        os.environ["NEXUS_LEDGER_ANCHOR_MODE"] = "mock"

    agent_a = Agent("Mercury", keys_dir="keys", db_path="demo_nexus.db")
    agent_b = Agent("Iris", keys_dir="keys", db_path="demo_nexus.db")

    agent_a.log("delivered_research", {"topic": "market analysis"}, counterparty=agent_b)
    agent_b.log("received_research", {"status": "accepted"}, counterparty=agent_a)
    agent_a.log("delivered_summary", {"format": "brief"}, counterparty=agent_b)

    tx = agent_a.anchor({"task": "research", "result": "complete"})

    print("=== Anchor Result ===")
    print(json.dumps(tx, indent=2))
    print()

    print("=== Full Ledger ===")
    print(json.dumps(agent_a.all_activity(), indent=2))


if __name__ == "__main__":
    main()
