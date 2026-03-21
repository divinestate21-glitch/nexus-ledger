"""Live relay demo for Nexus Ledger magic send/receive."""

from __future__ import annotations

import json
from pathlib import Path

from nexus_ledger import Agent


LIVE_RELAY = "http://104.236.251.94:8765"


def _clean(paths: list[str]) -> None:
    for file_name in paths:
        path = Path(file_name)
        if path.exists():
            path.unlink()


def main() -> None:
    _clean(["relay_mercury.db", "relay_iris.db"])

    mercury = Agent("Mercury", keys_dir="relay_keys_mercury", db_path="relay_mercury.db", relay=LIVE_RELAY)
    iris = Agent("Iris", keys_dir="relay_keys_iris", db_path="relay_iris.db", relay=LIVE_RELAY)

    print("=== Live Relay Demo ===")
    print(f"Relay: {LIVE_RELAY}")
    print(f"Mercury DID: {mercury.did}")
    print(f"Iris DID: {iris.did}")
    print(f"Mercury relay online: {mercury.relay_online}")
    print(f"Iris relay online: {iris.relay_online}")
    print()

    if not mercury.relay_online or not iris.relay_online:
        print("Relay unavailable. Agents are in local-only mode.")
        return

    print("=== Discovery ===")
    print("Mercury finds Iris:")
    print(json.dumps(mercury.find("Iris"), indent=2))
    print("Online agents:")
    print(json.dumps(mercury.online_agents(), indent=2))
    print()

    print("=== Mercury Sends Receipt To Iris ===")
    outbound = mercury.send("delivered_research", {"result": "complete"}, to="Iris")
    print(json.dumps(outbound, indent=2))
    print()

    print("=== Iris Checks Inbox (Auto-Countersign + Store) ===")
    iris_receipts = iris.check_inbox()
    print(json.dumps(iris_receipts, indent=2))
    print()

    print("=== Mercury Checks Inbox (Gets Returned Countersigned Receipt) ===")
    mercury_receipts = mercury.check_inbox()
    print(json.dumps(mercury_receipts, indent=2))
    print()

    if iris_receipts:
        latest = iris_receipts[-1]
        print("=== Verification ===")
        print(f"Mercury verifies: {mercury.verify_receipt(latest)}")
        print(f"Iris verifies: {iris.verify_receipt(latest)}")
        print(f"Mercury stored receipts: {len(mercury._ledger.get_receipts())}")
        print(f"Iris stored receipts: {len(iris._ledger.get_receipts())}")
    else:
        print("No new receipts for Iris.")


if __name__ == "__main__":
    main()
