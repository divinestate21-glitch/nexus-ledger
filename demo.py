"""Nexus Ledger v4.0.0 standalone full-flow demo."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType
from typing import Any, Dict

from nexus_ledger import Agent


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
            agent = {
                "did": did,
                "name": str(payload["name"]),
                "pubkey": str(payload["pubkey"]),
            }
            self._agents[did] = agent
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
            envelope = payload["envelope"]
            self._inbox.setdefault(to_did, []).append({"envelope": envelope})
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


def section(title: str) -> None:
    print(f"\n{'=' * 18} {title} {'=' * 18}")


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)


def reset_demo_dir(base: Path) -> None:
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)


def main() -> None:
    base = Path(".demo_runtime")
    reset_demo_dir(base)

    relay = InMemoryRelay()

    mercury = Agent(
        "Mercury",
        keys_dir=str(base / "keys_mercury"),
        db_path=str(base / "mercury.db"),
        relays=["http://104.236.251.94:8765", "http://localhost:8765"],
    )
    iris = Agent(
        "Iris",
        keys_dir=str(base / "keys_iris"),
        db_path=str(base / "iris.db"),
        relays=["http://104.236.251.94:8765", "http://localhost:8765"],
    )

    attach_relay(mercury, relay)
    attach_relay(iris, relay)

    section("1) Multi-Relay + Identity")
    print(f"Mercury relays: {mercury.relays}")
    print(f"Iris relays:    {iris.relays}")
    print(f"Mercury DID:    {mercury.did}")
    print(f"Iris DID:       {iris.did}")

    section("2) Live Receipt Callback + WS Fallback")
    live_events: list[dict[str, Any]] = []
    mercury.on_receipt(lambda receipt: live_events.append(receipt))

    section("3) Task Request/Accept/Deliver/Confirm (Typed Receipts + Chains)")
    deadline = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    request_receipt = mercury.request_task("Iris", description="market research", budget=100, deadline=deadline, encrypted=True)
    iris.check_inbox()
    accept_receipt = iris.accept_task("Mercury", task_id=request_receipt["data"]["task_id"], estimated_delivery=deadline, encrypted=True)
    mercury.check_inbox()
    deliver_receipt = iris.deliver_task(
        request_receipt["data"]["task_id"],
        artifact_hash="sha256:abc123",
        artifact_url="https://example.com/artifact",
        to="Mercury",
        encrypted=True,
    )
    mercury.check_inbox()
    confirm_receipt = mercury.confirm_task(request_receipt["data"]["task_id"], rating=5, feedback="excellent", to="Iris", encrypted=True)
    iris.check_inbox()
    mercury.check_inbox()

    chain = mercury.get_task_chain(request_receipt["data"]["task_id"])
    print("Task request:")
    print(pretty(request_receipt))
    print("Task accepted:")
    print(pretty(accept_receipt))
    print("Task delivered:")
    print(pretty(deliver_receipt))
    print("Task confirmed:")
    print(pretty(confirm_receipt))
    print(f"Chain length: {len(chain)}")
    print(f"Live callbacks fired: {len(live_events)}")

    section("4) Trust Scoring")
    mercury_report = mercury.get_trust_report(mercury.public_key)
    iris_report = mercury.get_trust_report(iris.public_key)
    print("Mercury trust report:")
    print(pretty(mercury_report))
    print("Iris trust report:")
    print(pretty(iris_report))

    section("5) CLI-Compatible Verification")
    final_receipt = mercury.get_task_chain(request_receipt["data"]["task_id"])[-1]
    print(f"Final receipt proof hash: {final_receipt['proof_hash']}")

    print("\nNexus Ledger v4.0 complete: multi-relay, live transport, typed chains, encrypted receipts, trust scoring.")


if __name__ == "__main__":
    main()
