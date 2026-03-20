"""Nexus Ledger API server."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from aiohttp import web

from ledger import ActivityLedger
from proof_anchor import ProofAnchorError, anchor_data, verify_anchor
from protocol import generate_ed25519_keypair, verify_signature
from registry import AgentRegistry


class NexusLedgerService:
    def __init__(self, db_path: str = "nexus.db") -> None:
        self.db_path = db_path
        self.registry = AgentRegistry(db_path=db_path)
        self.ledger = ActivityLedger(db_path=db_path)

    def close(self) -> None:
        self.registry.close()
        self.ledger.close()


async def register_agent(request: web.Request) -> web.Response:
    service: NexusLedgerService = request.app["service"]
    body = await request.json()

    name = str(body.get("name", "")).strip()
    if not name:
        raise web.HTTPBadRequest(text="name is required")

    capabilities = body.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise web.HTTPBadRequest(text="capabilities must be a list")

    provided_public_key = str(body.get("public_key", "")).strip()
    generated_private_key: Optional[str] = None
    if not provided_public_key:
        generated_private_key, provided_public_key = generate_ed25519_keypair()

    agent_id = str(body.get("agent_id", "")).strip() or None
    identity = service.registry.register(
        name=name,
        capabilities=[str(c) for c in capabilities],
        public_key=provided_public_key,
        agent_id=agent_id,
    )

    service.ledger.append(
        "agent_registered",
        {
            "name": identity.name,
            "capabilities": identity.capabilities,
            "public_key": identity.public_key,
        },
        agent_id=identity.agent_id,
    )

    response: Dict[str, Any] = {"registered": identity.to_dict()}
    if generated_private_key:
        response["generated_private_key"] = generated_private_key
    return web.json_response(response)


async def discover_agents(request: web.Request) -> web.Response:
    service: NexusLedgerService = request.app["service"]
    capability = str(request.query.get("capability", "")).strip()
    if not capability:
        raise web.HTTPBadRequest(text="capability query parameter is required")

    matches = service.registry.discover(capability)
    service.ledger.append(
        "agents_discovered",
        {
            "capability": capability,
            "match_count": len(matches),
            "agent_ids": [m["agent_id"] for m in matches],
        },
    )
    return web.json_response({"capability": capability, "matches": matches})


async def log_activity(request: web.Request) -> web.Response:
    service: NexusLedgerService = request.app["service"]
    body = await request.json()

    agent_id = str(body.get("agent_id", "")).strip() or None
    event_type = str(body.get("event_type", "")).strip() or "activity_logged"
    payload = body.get("payload", {})
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="payload must be a JSON object")

    signed_payload: Dict[str, Any] = {
        "agent_id": agent_id,
        "event_type": event_type,
        "payload": payload,
        "client_timestamp": str(body.get("client_timestamp", "")).strip(),
    }
    actor_signature = str(body.get("signature", "")).strip()
    actor_verified = False

    if actor_signature:
        public_key = str(body.get("public_key", "")).strip()
        if not public_key and agent_id:
            identity = service.registry.get(agent_id)
            public_key = identity.public_key if identity else ""
        if not public_key:
            raise web.HTTPBadRequest(text="public_key is required when signature is provided")
        actor_verified = verify_signature(signed_payload, actor_signature, public_key)
        if not actor_verified:
            raise web.HTTPBadRequest(text="signature verification failed")

    entry = service.ledger.append(
        event_type,
        {
            "payload": payload,
            "actor_signature": actor_signature or None,
            "actor_verified": actor_verified,
            "client_timestamp": signed_payload["client_timestamp"] or None,
        },
        agent_id=agent_id,
    )
    return web.json_response({"logged": entry})


async def anchor_proof_handler(request: web.Request) -> web.Response:
    service: NexusLedgerService = request.app["service"]
    body = await request.json()

    data = body.get("data")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="data must be a JSON object")

    agent_id = str(body.get("agent_id", "")).strip() or None
    mode = str(body.get("mode", "")).strip() or os.getenv("NEXUS_LEDGER_ANCHOR_MODE", "auto")

    try:
        anchored = anchor_data(data, mode=mode, db_path=service.db_path)
    except ProofAnchorError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    service.ledger.append(
        "proof_anchored",
        {"data": data, "anchor": anchored},
        agent_id=agent_id,
        tx_signature=anchored.get("tx_signature"),
        proof_hash=anchored.get("proof_hash"),
    )

    return web.json_response({"anchor": anchored})


async def verify_proof_handler(request: web.Request) -> web.Response:
    service: NexusLedgerService = request.app["service"]
    tx_signature = request.match_info["tx_signature"]

    proof_hash = str(request.query.get("proof_hash", "")).strip() or None
    data_param = str(request.query.get("data", "")).strip()
    parsed_data: Optional[Dict[str, Any]] = None

    if data_param:
        try:
            maybe_data = json.loads(data_param)
        except json.JSONDecodeError as exc:
            raise web.HTTPBadRequest(text=f"Invalid data JSON: {exc}") from exc
        if not isinstance(maybe_data, dict):
            raise web.HTTPBadRequest(text="data query parameter must decode to a JSON object")
        parsed_data = maybe_data

    mode = str(request.query.get("mode", "")).strip() or os.getenv("NEXUS_LEDGER_ANCHOR_MODE", "auto")

    try:
        result = verify_anchor(
            tx_signature,
            data=parsed_data,
            proof_hash=proof_hash,
            mode=mode,
            db_path=service.db_path,
        )
    except ProofAnchorError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    service.ledger.append(
        "proof_verified",
        {
            "verification": result,
            "input": {"proof_hash": proof_hash, "has_data": parsed_data is not None},
        },
        tx_signature=tx_signature,
        proof_hash=result.get("provided_hash") or result.get("anchored", {}).get("proof_hash"),
        status="verified" if result.get("verified") else "mismatch",
    )

    return web.json_response(result)


async def get_ledger(request: web.Request) -> web.Response:
    service: NexusLedgerService = request.app["service"]
    entries = service.ledger.all()
    return web.json_response({"entries": entries, "count": len(entries)})


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "nexus-ledger"})


async def on_cleanup(app: web.Application) -> None:
    service: NexusLedgerService = app["service"]
    service.close()


def create_app(db_path: str = "nexus.db") -> web.Application:
    app = web.Application()
    app["service"] = NexusLedgerService(db_path=db_path)
    app.router.add_post("/register", register_agent)
    app.router.add_get("/discover", discover_agents)
    app.router.add_post("/log", log_activity)
    app.router.add_post("/anchor", anchor_proof_handler)
    app.router.add_get("/verify/{tx_signature}", verify_proof_handler)
    app.router.add_get("/ledger", get_ledger)
    app.router.add_get("/health", health)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    web.run_app(create_app(db_path=os.getenv("NEXUS_LEDGER_DB", "nexus.db")), port=9000)


if __name__ == "__main__":
    main()
