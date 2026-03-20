"""Nexus Ledger demo: register agents, log activity, anchor proof, query ledger."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

from aiohttp import web

from server import create_app

sys.path.insert(0, str(Path(__file__).parent / "src"))
from nexus_ledger import Agent  # noqa: E402


async def _start_server(db_path: str, host: str = "127.0.0.1", port: int = 9000) -> web.AppRunner:
    app = create_app(db_path=db_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    return runner


def main() -> None:
    os.environ["NEXUS_LEDGER_ANCHOR_MODE"] = "mock"
    db_path = "demo_nexus.db"
    if Path(db_path).exists():
        Path(db_path).unlink()

    async def _run() -> None:
        runner = await _start_server(db_path=db_path)
        try:
            mercury = Agent("Mercury", capabilities=["market_intel", "coordination"])
            iris = Agent("Iris", capabilities=["app_development", "design"])
            atlas = Agent("Atlas", capabilities=["infrastructure", "security"])

            print("=== Registered Agents ===")
            print(mercury.agent_id, iris.agent_id, atlas.agent_id)

            matches = mercury.discover("app_development")
            print("\n=== Discovery: app_development ===")
            print(json.dumps(matches, indent=2))

            mercury.log("Completed market research for Iris")
            iris.log("Delivered UI prototype for Mercury")
            atlas.log("Provisioned staging infrastructure")

            proof = {"task": "research", "result": "delivered", "agent": "Mercury"}
            anchor = mercury.anchor_proof(proof, mode="mock")
            print("\n=== Anchored Proof ===")
            print(json.dumps(anchor, indent=2))

            verified = mercury.verify_proof(anchor["tx_signature"], data=proof, mode="mock")
            print("\n=== Verification ===")
            print(json.dumps(verified, indent=2))

            ledger = mercury.get_ledger()
            print("\n=== Ledger Snapshot ===")
            print(f"entries: {ledger.get('count')}")
            print(json.dumps(ledger.get("entries", [])[-5:], indent=2))
        finally:
            await runner.cleanup()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
