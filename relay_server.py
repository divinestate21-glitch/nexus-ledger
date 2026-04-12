#!/usr/bin/env python3
"""
Nexus Ledger Relay Server — deployable HTTP relay for agent-to-agent communication.

Run:
    python relay_server.py                    # localhost:8765
    python relay_server.py --port 9000        # localhost:9000
    python relay_server.py --host 0.0.0.0     # all interfaces

The relay provides:
    - Agent registration and discovery
    - Message routing between agents
    - Inbox polling (HTTP) and WebSocket push
    - Health monitoring

No authentication by default — suitable for development and trusted networks.
For production, deploy behind a reverse proxy with TLS and auth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    import websockets
    from websockets.asyncio.server import serve as ws_serve
    HAS_WS = True
except ImportError:
    HAS_WS = False


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("nexus-relay")


class NexusRelay:
    """In-memory relay state — agent registry, inboxes, and WebSocket connections."""

    def __init__(self) -> None:
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._inbox: Dict[str, List[Dict[str, Any]]] = {}
        self._ws_clients: Dict[str, list] = {}  # DID -> list of websocket connections
        self._stats = {"messages_routed": 0, "agents_registered": 0, "started_at": time.time()}

    def register(self, did: str, name: str, pubkey: str) -> Dict[str, Any]:
        agent = {"did": did, "name": name, "pubkey": pubkey, "registered_at": time.time()}
        self._agents[did] = agent
        self._inbox.setdefault(did, [])
        self._stats["agents_registered"] = len(self._agents)
        log.info(f"Registered agent: {name} ({did[:20]}...)")
        return {"ok": True, "agent": agent}

    def discover(self, name: Optional[str] = None) -> Dict[str, Any]:
        agents = list(self._agents.values())
        if name:
            name_lower = name.strip().lower()
            agents = [a for a in agents if a["name"].strip().lower() == name_lower]
        return {"agents": agents}

    async def send(self, to_did: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
        self._inbox.setdefault(to_did, []).append({
            "envelope": envelope,
            "received_at": time.time(),
        })
        self._stats["messages_routed"] += 1

        # Push via WebSocket if recipient is connected
        if to_did in self._ws_clients:
            message = json.dumps({"type": "receipt", "envelope": envelope})
            dead = []
            for ws in self._ws_clients[to_did]:
                try:
                    await ws.send(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._ws_clients[to_did].remove(ws)

        return {"queued": True}

    def receive(self, did: str) -> Dict[str, Any]:
        messages = self._inbox.get(did, [])
        self._inbox[did] = []
        return {"messages": messages}

    def health(self) -> Dict[str, Any]:
        uptime = time.time() - self._stats["started_at"]
        return {
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "agents_registered": self._stats["agents_registered"],
            "messages_routed": self._stats["messages_routed"],
            "version": "1.0.0",
        }

    def add_ws_client(self, did: str, ws: Any) -> None:
        self._ws_clients.setdefault(did, []).append(ws)
        log.info(f"WebSocket connected: {did[:20]}...")

    def remove_ws_client(self, did: str, ws: Any) -> None:
        if did in self._ws_clients:
            self._ws_clients[did] = [w for w in self._ws_clients[did] if w is not ws]
            if not self._ws_clients[did]:
                del self._ws_clients[did]


# ─── HTTP Server (aiohttp) ───────────────────────────────────────────

def build_app(relay: NexusRelay) -> "web.Application":
    """Build the aiohttp web application."""
    if not HAS_AIOHTTP:
        raise RuntimeError("aiohttp is required. Install with: pip install aiohttp")

    app = web.Application()

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response(relay.health())

    async def handle_register(request: web.Request) -> web.Response:
        data = await request.json()
        result = relay.register(
            did=str(data["did"]),
            name=str(data["name"]),
            pubkey=str(data["pubkey"]),
        )
        return web.json_response(result)

    async def handle_discover(request: web.Request) -> web.Response:
        name = request.query.get("name")
        return web.json_response(relay.discover(name=name))

    async def handle_send(request: web.Request) -> web.Response:
        data = await request.json()
        result = await relay.send(
            to_did=str(data["to"]),
            envelope=data["envelope"],
        )
        return web.json_response(result)

    async def handle_receive(request: web.Request) -> web.Response:
        did = request.query.get("did", "")
        return web.json_response(relay.receive(did=did))

    async def handle_agents(request: web.Request) -> web.Response:
        return web.json_response(relay.discover())

    async def handle_index(request: web.Request) -> web.Response:
        info = relay.health()
        html = f"""<!DOCTYPE html>
<html>
<head><title>Nexus Ledger Relay</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; background: #0a0a0a; color: #e0e0e0; }}
h1 {{ color: #00bcd4; }}
pre {{ background: #1a1a2e; padding: 16px; border-radius: 8px; overflow-x: auto; }}
a {{ color: #00bcd4; }}
.stat {{ color: #ffb300; font-weight: bold; }}
</style>
</head>
<body>
<h1>🔗 Nexus Ledger Relay</h1>
<p>Agent-to-agent trust layer relay server.</p>
<pre>
Status:       <span class="stat">{info['status']}</span>
Uptime:       <span class="stat">{info['uptime_seconds']}s</span>
Agents:       <span class="stat">{info['agents_registered']}</span>
Messages:     <span class="stat">{info['messages_routed']}</span>
Version:      {info['version']}
</pre>
<h2>API Endpoints</h2>
<pre>
GET  /health              Health check
POST /register            Register an agent (did, name, pubkey)
GET  /discover            List agents (optional ?name=filter)
POST /send                Send envelope to agent (to, envelope)
GET  /receive?did=...     Poll inbox for agent
GET  /agents              List all registered agents
WS   /ws?did=...          WebSocket live connection
</pre>
<h2>Quick Start</h2>
<pre>
pip install nexus-ledger

from nexus_ledger import Agent
agent = Agent("MyAgent", relays=["http://localhost:8765"])
</pre>
<p><a href="https://github.com/divinestate21-glitch/nexus-ledger">GitHub</a> · <a href="https://pypi.org/project/nexus-ledger/">PyPI</a></p>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/register", handle_register)
    app.router.add_get("/discover", handle_discover)
    app.router.add_get("/agents", handle_agents)
    app.router.add_post("/send", handle_send)
    app.router.add_get("/receive", handle_receive)

    return app


# ─── WebSocket Server ────────────────────────────────────────────────

async def ws_handler(relay: NexusRelay, websocket: Any) -> None:
    """Handle a single WebSocket connection."""
    did = None
    try:
        # Expect first message to be registration: {"did": "...", "name": "..."}
        raw = await asyncio.wait_for(websocket.recv(), timeout=10)
        data = json.loads(raw)
        did = str(data.get("did", ""))
        if not did:
            await websocket.close(4001, "Missing DID")
            return

        relay.add_ws_client(did, websocket)
        await websocket.send(json.dumps({"type": "connected", "did": did}))

        # Keep alive — relay pushes messages via send()
        async for message in websocket:
            # Client can send pings or additional commands
            try:
                cmd = json.loads(message)
                if cmd.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass

    except Exception as e:
        log.warning(f"WebSocket error: {e}")
    finally:
        if did:
            relay.remove_ws_client(did, websocket)


# ─── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Nexus Ledger Relay Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--ws-port", type=int, default=8766, help="WebSocket port (default: 8766)")
    parser.add_argument("--no-ws", action="store_true", help="Disable WebSocket server")
    args = parser.parse_args()

    relay = NexusRelay()

    if not HAS_AIOHTTP:
        print("ERROR: aiohttp is required. Install with: pip install aiohttp")
        print("  Or: pip install 'nexus-ledger[transport]'")
        return

    app = build_app(relay)

    log.info(f"🔗 Nexus Ledger Relay starting on {args.host}:{args.port}")
    log.info(f"   HTTP:      http://{args.host}:{args.port}")
    if HAS_WS and not args.no_ws:
        log.info(f"   WebSocket: ws://{args.host}:{args.ws_port}")

    async def start() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()
        log.info(f"✅ Relay server running on http://{args.host}:{args.port}")

        if HAS_WS and not args.no_ws:
            ws_server = await ws_serve(
                lambda ws: ws_handler(relay, ws),
                args.host,
                args.ws_port,
            )
            log.info(f"✅ WebSocket server running on ws://{args.host}:{args.ws_port}")

        # Keep running
        try:
            await asyncio.Future()  # Run forever
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("Shutting down...")
            await runner.cleanup()

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        log.info("Relay server stopped.")


if __name__ == "__main__":
    main()
