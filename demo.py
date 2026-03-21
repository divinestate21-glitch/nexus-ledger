"""Nexus Ledger v3.0.0 standalone full-flow demo."""

from __future__ import annotations

import json
import shutil
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
        relay="memory://relay",
    )
    iris = Agent(
        "Iris",
        keys_dir=str(base / "keys_iris"),
        db_path=str(base / "iris.db"),
        relay="memory://relay",
    )
    attach_relay(mercury, relay)
    attach_relay(iris, relay)

    section("1) 🧠 Agent Boot")
    print(f"🚀 Mercury ready: {mercury.name}")
    print(f"🚀 Iris ready:    {iris.name}")

    section("2) 🪪 DID Identity")
    print(f"🛰️  Mercury DID: {mercury.did}")
    print(f"🛰️  Iris DID:    {iris.did}")

    section("3) 🗂️ Local Logging")
    mercury_log = mercury.log("task_completed", {"job": "market_research", "status": "done"}, counterparty=iris)
    iris_log = iris.log("task_received", {"job": "market_research", "status": "accepted"}, counterparty=mercury)
    print("Mercury log entry:")
    print(pretty(mercury_log))
    print("Iris log entry:")
    print(pretty(iris_log))

    section("4) 🤝 P2P Receipt: Create + Countersign + Verify")
    draft_receipt = mercury.create_receipt(
        "delivery_receipt",
        {"task_id": "HX-042", "artifact": "research.pdf", "result": "delivered"},
        iris.public_key,
    )
    countersigned_receipt = iris.countersign_receipt(draft_receipt)
    mercury_ok = mercury.verify_receipt(countersigned_receipt)
    iris_ok = iris.verify_receipt(countersigned_receipt)
    mercury_store = mercury.store_receipt(countersigned_receipt)
    iris_store = iris.store_receipt(countersigned_receipt)
    print("Countersigned receipt:")
    print(pretty(countersigned_receipt))
    print(f"✅ Mercury verifies: {mercury_ok}")
    print(f"✅ Iris verifies:    {iris_ok}")
    print(f"🧾 Stored proof hash (Mercury): {mercury_store['proof_hash']}")
    print(f"🧾 Stored proof hash (Iris):    {iris_store['proof_hash']}")

    section("5) 📡 Relay Send + Inbox Check")
    outbound = mercury.send(
        "delivery_receipt",
        {"task_id": "HX-043", "artifact": "summary.md", "result": "delivered_via_relay"},
        to="Iris",
    )
    print("Outbound receipt created and sent by Mercury:")
    print(pretty(outbound))

    iris_inbox = iris.check_inbox()
    mercury_inbox = mercury.check_inbox()
    print(f"📥 Iris inbox processed: {len(iris_inbox)} receipt(s)")
    print(f"📥 Mercury inbox processed: {len(mercury_inbox)} receipt(s)")

    final_receipt = mercury_inbox[-1] if mercury_inbox else {}
    if final_receipt:
        print("Final dual-signed receipt returned through relay:")
        print(pretty(final_receipt))

    section("6) 🏁 Snapshot Summary")
    print(f"Mercury activity rows: {len(mercury.all_activity())}")
    print(f"Iris activity rows:    {len(iris.all_activity())}")
    print(f"Mercury receipts:      {len(mercury._ledger.get_receipts())}")
    print(f"Iris receipts:         {len(iris._ledger.get_receipts())}")
    print("\n✨ No server required. No tokens. Just signed proof.")


if __name__ == "__main__":
    main()
