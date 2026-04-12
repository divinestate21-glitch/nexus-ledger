# Nexus Ledger v4.3.0

**The hash proves it wasn't tampered with. The knowledge graph proves it's actually good.**

Nexus Ledger gives agents verifiable, dual-signed delivery receipts. Graphify adds a canonical structural fingerprint with adversarial quality scoring — so you verify what the code *is*, not just that it arrived.

Drop-in trust layer for LangChain, CrewAI, AutoGen, OpenClaw, or any Python agent. No rewrite needed.

🌐 **[Landing Page](https://divinestate21-glitch.github.io/nexus-ledger/)** · 📦 **[PyPI](https://pypi.org/project/nexus-ledger/)** · 🔗 **[Live Relay](https://grand-gentleness-production-a6d5.up.railway.app)**

## Install

```bash
pip install nexus-ledger
```

Optional dependencies:
```bash
pip install 'nexus-ledger[graphify]'    # Knowledge graph verification
pip install 'nexus-ledger[transport]'   # WebSocket support
pip install 'nexus-ledger[ethereum]'    # Ethereum anchoring
pip install 'nexus-ledger[erc8004]'     # On-chain identity
```

## Quick Start — Add Trust to Your Agents in 5 Lines

```python
from nexus_ledger import Agent

alice = Agent("Alice")
bob = Agent("Bob")

alice.request_task("Bob", description="market research", budget=100)
print(alice.trust_score())  # → 0.46
alice.anchor_to_eth(chain="base")  # Anchor to Ethereum
```

## Full Task Lifecycle

```python
# TaskRequest → TaskAccepted → TaskDelivered → TaskConfirmed
alice.request_task("Bob", description="competitor analysis", budget=50, task_id="deal-001")
bob.accept_task("Alice", task_id="deal-001", estimated_delivery="2026-03-25")
bob.deliver_task("deal-001", artifact_hash="sha256:report_v1", to="Alice")
alice.confirm_task("deal-001", rating=5, feedback="Outstanding", to="Bob")
```

## What's New in v4.3.0

- **Graphify Bridge** — canonical knowledge graph receipts for code delivery verification
- **Adversarial Quality Scoring** — dead code detection, functionality density analysis, suspicious pattern flagging
- **Graph Canonicalization** — deterministic hashing across LLM extraction variance
- **VerifiedDelivery** — end-to-end graph-verified deliver + verify-and-decide flow
- **44 Tests Passing** — full coverage including real Graphify integration tests

### Previous (v4.2.x)

- **Agent Refactored** — Agent + TaskManager + RelayClient architecture
- **Sybil-Resistant Trust** — diversity weighting, 30-day temporal decay, 3-counterparty minimum
- **Ethereum Anchoring** — receipts on Base L2, Ethereum mainnet, or Sepolia
- **Relay Server** — HTTP + WebSocket with health dashboard

## Features

| Feature | Description |
|---------|-------------|
| 🔐 **Signed Receipts** | Dual-signed Ed25519 receipts with hash-linked chains |
| 🔒 **E2E Encryption** | NaCl box encryption (Ed25519 → Curve25519) |
| ⭐ **Trust Scores** | Sybil-resistant local scoring (0.0 → 1.0) |
| ⛓️ **Ethereum Anchoring** | Receipt hashes on Base L2 / mainnet / Sepolia |
| 🌐 **Relay Network** | HTTP + WebSocket with multi-relay failover |
| 🔍 **DID Identity** | Cryptographic DID:key identities + ERC-8004 |
| 💻 **CLI** | 10 commands: init, send, inbox, history, agents, verify, trust, task-chain, anchor, anchor-all |
| 🧪 **44 Tests** | Full pytest suite passing in 0.21s |

## CLI

```bash
nexus-ledger init                          # Create agent identity
nexus-ledger send Iris TaskRequest '{...}' # Send a receipt
nexus-ledger inbox                         # Check incoming
nexus-ledger history                       # View all activity
nexus-ledger agents                        # List online agents
nexus-ledger verify '{...}'                # Verify signatures
nexus-ledger trust                         # Show trust report
nexus-ledger task-chain deal-001           # View receipt chain
nexus-ledger anchor --chain base           # Anchor to Ethereum
nexus-ledger anchor-all --chain sepolia    # Batch anchor all
```

## Relay Server

```bash
python relay_server.py --port 8765
```

Provides: agent registration, discovery, message routing, WebSocket push delivery, health dashboard.

Live relay: [grand-gentleness-production-a6d5.up.railway.app](https://grand-gentleness-production-a6d5.up.railway.app)

## Graphify Bridge — Verify What Code Actually Is

Standard delivery receipts hash the artifact. Graphify goes deeper — it extracts a knowledge graph from the delivered code, canonicalizes it for deterministic hashing (tolerating LLM extraction variance), and scores structural quality with adversarial checks.

```python
from nexus_ledger import Agent

alice = Agent("Alice")
bob = Agent("Bob")

# Bob delivers with graph verification
vd = bob.verified_delivery()
receipt = vd.deliver(
    task_id="deal-001",
    codebase_path="/path/to/delivered/code",
    artifact_hash="sha256:...",
    to="Alice",
)
# receipt["data"]["graph_hash"]    → canonical structural fingerprint
# receipt["data"]["graph_metrics"] → nodes, edges, communities, quality

# Alice independently verifies and auto-decides
vd_alice = alice.verified_delivery()
result = vd_alice.verify_and_decide(
    task_id="deal-001",
    codebase_path="/path/to/reviewed/code",
    delivered_receipt=receipt,
)
# result["event_type"] → "TaskConfirmed" or "TaskDisputed"
```

**What it catches:** dead code padding, structural-only scaffolding (imports/inherits without real behavior), suspiciously uniform community structure, low edge density. Grades A through F with detailed factor breakdown.

## Architecture

```
Agent (identity + high-level API)
├── TaskManager (request/accept/deliver/confirm/dispute)
├── RelayClient (relay communication + failover)
├── Ledger (SQLite receipt storage)
├── TrustScorer (Sybil-resistant scoring)
├── EthAnchor (Ethereum proof anchoring)
├── Crypto (NaCl encryption + Ed25519 signing)
└── GraphifyBridge (knowledge graph verification)
    ├── GraphifyReceipt (canonical graph fingerprinting)
    ├── GraphDiff (structural comparison)
    ├── CodeQualityScorer (adversarial quality grading)
    └── VerifiedDelivery (end-to-end verified flow)
```

## Trust Scoring

Trust scores are computed from receipt history with Sybil resistance:
- **Diversity weighting** — more unique counterparties = higher trust
- **Temporal decay** — 30-day half-life on old receipts
- **Counterparty minimum** — score capped at 0.5 until 3+ unique counterparties
- **Rating integration** — weighted by delivery confirmation ratings

## Encryption

When `encrypted=True`, receipt payloads are encrypted with NaCl Box (X25519 keys derived from Ed25519 identities). Relay servers see only signed envelope metadata and ciphertext.

## Demo

```bash
python demo.py
```

## Built By

**Mercury & Vickson** of [Vickson Enterprises](https://x.com/bunnyhop0veru) ☿️

*Your agents already work. Nexus makes them trustworthy. Works with any Python agent framework — no vendor lock-in, no rewrite.*
