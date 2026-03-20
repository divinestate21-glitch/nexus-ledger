"""DIDComm-inspired cross-machine receipt exchange demo."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib import request

from nexus_ledger import Agent


def _fetch_remote_did(endpoint: str) -> str:
    with request.urlopen(f"{endpoint}/did", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    did = str(payload.get("did", "")).strip()
    if not did:
        raise RuntimeError("Remote listener did not return a DID")
    return did


def _clean_paths(paths: list[str]) -> None:
    for file_name in paths:
        path = Path(file_name)
        if path.exists():
            path.unlink()


def run_local_simulation() -> None:
    _clean_paths(["demo_a.db", "demo_b.db"])

    agent_a = Agent("Machine-A", keys_dir="demo_keys_a", db_path="demo_a.db")
    agent_b = Agent("Machine-B", keys_dir="demo_keys_b", db_path="demo_b.db")

    print("=== Local Cross-Machine Simulation (localhost) ===")
    print(f"Agent A DID: {agent_a.did}")
    print(f"Agent B DID: {agent_b.did}")

    listener = agent_a.start_listener(port=8765)
    try:
        response_envelope = agent_b.send_receipt(
            agent_a.did,
            "delivered_research",
            {"result": "complete", "mode": "localhost"},
            transport="http",
            endpoint="http://127.0.0.1:8765",
        )

        final_receipt = agent_b.receive_receipt(response_envelope)

        print("\n=== Countersigned Receipt ===")
        print(json.dumps(final_receipt, indent=2))

        print("\n=== Verification ===")
        print(f"Agent A verify: {agent_a.verify_receipt(final_receipt)}")
        print(f"Agent B verify: {agent_b.verify_receipt(final_receipt)}")

        print("\n=== Stored Locally ===")
        print(f"Agent A stored receipts: {len(agent_a._ledger.get_receipts())}")
        print(f"Agent B stored receipts: {len(agent_b._ledger.get_receipts())}")
    finally:
        listener.shutdown()
        listener.server_close()


def run_listener_mode(port: int) -> None:
    agent_a = Agent("Machine-A", keys_dir="machine_a_keys", db_path="machine_a.db")
    agent_a.start_listener(port=port)

    print("=== Listener Running ===")
    print(f"Agent A DID: {agent_a.did}")
    print(f"HTTP endpoint: http://0.0.0.0:{port}")
    print("Waiting for incoming envelopes. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_sender_mode(host_port: str) -> None:
    endpoint = f"http://{host_port.strip()}"
    agent_b = Agent("Machine-B", keys_dir="machine_b_keys", db_path="machine_b.db")

    remote_did = _fetch_remote_did(endpoint)

    print("=== Sender Mode ===")
    print(f"Agent B DID: {agent_b.did}")
    print(f"Agent A DID (fetched): {remote_did}")

    response_envelope = agent_b.send_receipt(
        remote_did,
        "delivered_research",
        {"result": "complete", "mode": "remote"},
        transport="http",
        endpoint=endpoint,
    )
    final_receipt = agent_b.receive_receipt(response_envelope)

    print("\n=== Countersigned Receipt ===")
    print(json.dumps(final_receipt, indent=2))

    print("\n=== Verification ===")
    print(f"Agent B verify: {agent_b.verify_receipt(final_receipt)}")
    print(f"Agent B stored receipts: {len(agent_b._ledger.get_receipts())}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-machine DIDComm-inspired receipt demo")
    parser.add_argument("--listen", type=int, help="Run as listener on this port")
    parser.add_argument("--send", help="Send to listener host:port")
    args = parser.parse_args()

    if args.listen and args.send:
        raise SystemExit("Use either --listen or --send, not both")

    if args.listen:
        run_listener_mode(args.listen)
        return

    if args.send:
        run_sender_mode(args.send)
        return

    run_local_simulation()


if __name__ == "__main__":
    main()
