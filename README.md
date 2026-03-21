# Nexus Ledger — Signed Proof That Your Agents Did The Work

Agents can finish tasks, exchange payloads, and claim outcomes, but most systems still lack verifiable receipts. In practice, this means teams can argue about what happened and when. Without shared signed records, trust becomes guesswork.

## Solution

- Creates cryptographically signed activity logs tied to each agent identity.
- Produces dual-signed receipts (creator + counterparty) for shared proof.
- Supports relay-based message exchange while both agents keep the same local receipt history.

## Architecture Diagram

```text
Agent A                    Relay                    Agent B
  |                          |                          |
  |--- create receipt ------>|                          |
  |--- sign + send --------->|--- deliver ------------->|
  |                          |<--- countersign ---------|
  |<--- verified receipt ----|                          |
  |                          |                          |
Both agents store the same dual-signed receipt locally
```

## Install

```bash
pip install nexus-ledger
```

| Problem | Fix |
|---|---|
| `pip: command not found` | `pip3 install nexus-ledger` |
| Still not installing | `python3 -m pip install nexus-ledger` |
| Externally managed environment | `python3 -m pip install nexus-ledger --user` |
| Need isolated env | `python3 -m venv .venv && source .venv/bin/activate && pip install nexus-ledger` |

## Quickstart

```python
from nexus_ledger import Agent
mercury = Agent("Mercury")
iris = Agent("Iris")
receipt = mercury.send("delivery_receipt", {"task_id": "Q-1"}, to="Iris")
print(iris.check_inbox())
```

## How It Works

### Identity

Each `Agent` owns an Ed25519 keypair. The public key becomes both the verification root and a DID (`did:key`) for transport routing and discovery.

### Logging

`agent.log(...)` writes signed activity rows to local SQLite storage. Each entry includes timestamp, event type, payload, optional counterparty, and signature for tamper detection.

### Receipts

A receipt starts with Agent A creating a signed payload, then Agent B countersigns the same payload. Verification checks both signatures over one canonical receipt payload.

### Relay

Agents can use relay endpoints (`send`, `check_inbox`, `find`) to exchange envelopes and auto-countersign. Returned receipts are re-verified and stored locally by both participants.

### Proof Anchoring

`agent.anchor(...)` computes a deterministic proof hash and can anchor it (mock or Solana memo flow). `agent.verify(...)` re-hashes data and compares against the expected proof hash.

### On-Chain Trust (ERC-8004)

Nexus Ledger integrates ERC-8004 on Base mainnet (`https://mainnet.base.org`) and links signed receipt hashes to on-chain trust registries.

- Identity Registry: reads on-chain ERC-721 agent identity and metadata (`agent.erc8004_identity()`).
- Reputation Registry: reads reputation signals tied to agent ID (`agent.get_on_chain_reputation()`).
- Validation Registry: write helper exists for validator checks (requires funded key + ABI-compatible contract methods).

The integration starts from the hackathon registration transaction and infers registry addresses directly from on-chain logs:

- Registration TX: `0xb80ee780354286184c5d68b94c68c95c35a9786068b325b78d7eea4a622e907f`
- Agent ID: `35281`
- Wallet: `0xbbFb4Df7450FAB448e6bd2a138D0C241834848f9`

Write operations (`agent.rate_counterparty(...)`, validation posting) require:

- `NEXUS_LEDGER_PRIVATE_KEY` set in environment.
- Optional install: `pip install 'nexus-ledger[erc8004]'` (adds `web3` for transaction signing).

## Real Output

```text
================== 1) 🧠 Agent Boot ==================
🚀 Mercury ready: Mercury
🚀 Iris ready:    Iris

================== 2) 🪪 DID Identity ==================
🛰️  Mercury DID: did:key:z6Mktdi3bNgkxQPjK9vyReAMFsoNDt27YMb86N2ArDiCAPqb
🛰️  Iris DID:    did:key:z6MkrHXMMCK5HH7fp2sBoifCMBcP3p69tifi5GMZauswRcr5

================== 3) 🗂️ Local Logging ==================
Mercury log entry:
{
  "agent_pubkey": "d2b0d2ca1cfc13326973d25fa807556f49009e79e6a163e9dc554e8728ecfa52",
  "counterparty_pubkey": "afcdbde44d9a52d2ce89c6e756fe137176152f14714fd0df79775f8d46bdf8ea",
  "data_json": "{\"job\": \"market_research\", \"status\": \"done\"}",
  "event_type": "task_completed",
  "id": 1,
  "signature": "ade2aa32ffffd5da6364580f45e71d674c061003271d093d730c80588a4edf5310ea6edc9692539ec7d87e969921068bf1258aac649630027eba28927ca0c202",
  "timestamp": "2026-03-21T20:09:58.768940+00:00"
}
Iris log entry:
{
  "agent_pubkey": "afcdbde44d9a52d2ce89c6e756fe137176152f14714fd0df79775f8d46bdf8ea",
  "counterparty_pubkey": "d2b0d2ca1cfc13326973d25fa807556f49009e79e6a163e9dc554e8728ecfa52",
  "data_json": "{\"job\": \"market_research\", \"status\": \"accepted\"}",
  "event_type": "task_received",
  "id": 1,
  "signature": "83a845bf8931d1b581617dd61af167daee84066e9e33211e351f5c2c566735cbf808fc52c80610943362d0c47a7c5fef0060021f16629065537e2dabaa3ce601",
  "timestamp": "2026-03-21T20:09:58.770014+00:00"
}

================== 4) 🤝 P2P Receipt: Create + Countersign + Verify ==================
Countersigned receipt:
{
  "agent_a_pubkey": "d2b0d2ca1cfc13326973d25fa807556f49009e79e6a163e9dc554e8728ecfa52",
  "agent_a_signature": "20bc6d3a73a2200d2ec260030db014f320a670430ca402a89081d88af80b008b6fae5e779478c362cf98856c57e344d9786ca98e16e704612dedb79459b7fc09",
  "agent_b_pubkey": "afcdbde44d9a52d2ce89c6e756fe137176152f14714fd0df79775f8d46bdf8ea",
  "agent_b_signature": "dda669f4a6e62ac00f1eeda1ed99d33959d492d205234ebc42d35cd76f250ceb55e3cef55791f7d054a7365fb68046c0a090a4532cb36628d1e71ed2f4eb080c",
  "data": {
    "artifact": "research.pdf",
    "result": "delivered",
    "task_id": "HX-042"
  },
  "event_type": "delivery_receipt",
  "timestamp": "2026-03-21T20:09:58.770775+00:00"
}
✅ Mercury verifies: True
✅ Iris verifies:    True
🧾 Stored proof hash (Mercury): a6444f8047b002a90b93b98f208f55471a9e5f4d39c008e604be45b78537078a
🧾 Stored proof hash (Iris):    a6444f8047b002a90b93b98f208f55471a9e5f4d39c008e604be45b78537078a

================== 5) 📡 Relay Send + Inbox Check ==================
Outbound receipt created and sent by Mercury:
{
  "agent_a_pubkey": "d2b0d2ca1cfc13326973d25fa807556f49009e79e6a163e9dc554e8728ecfa52",
  "agent_a_signature": "1e54dc239f7fd0fb71ea58b6178d1feae4287f25f73180dcdfc6f1be903222acf0e72c7d58d84db205a13786f9d8d95d748f9572ddb6b5544f0f3732a3e96b0e",
  "agent_b_pubkey": "afcdbde44d9a52d2ce89c6e756fe137176152f14714fd0df79775f8d46bdf8ea",
  "data": {
    "artifact": "summary.md",
    "result": "delivered_via_relay",
    "task_id": "HX-043"
  },
  "event_type": "delivery_receipt",
  "timestamp": "2026-03-21T20:09:58.777227+00:00"
}
📥 Iris inbox processed: 1 receipt(s)
📥 Mercury inbox processed: 1 receipt(s)
Final dual-signed receipt returned through relay:
{
  "agent_a_pubkey": "d2b0d2ca1cfc13326973d25fa807556f49009e79e6a163e9dc554e8728ecfa52",
  "agent_a_signature": "1e54dc239f7fd0fb71ea58b6178d1feae4287f25f73180dcdfc6f1be903222acf0e72c7d58d84db205a13786f9d8d95d748f9572ddb6b5544f0f3732a3e96b0e",
  "agent_b_pubkey": "afcdbde44d9a52d2ce89c6e756fe137176152f14714fd0df79775f8d46bdf8ea",
  "agent_b_signature": "336a4f391f745ec84dbd1ab90f1bae490f42271781fcde2d491a4c79da3c102f79159dfa8011743e24b984382516be4789abc5a7ca586b160255e78eac278e0b",
  "data": {
    "artifact": "summary.md",
    "result": "delivered_via_relay",
    "task_id": "HX-043"
  },
  "event_type": "delivery_receipt",
  "timestamp": "2026-03-21T20:09:58.777227+00:00"
}

================== 6) 🏁 Snapshot Summary ==================
Mercury activity rows: 1
Iris activity rows:    1
Mercury receipts:      2
Iris receipts:         2

✨ No server required. No tokens. Just signed proof.
```

## API Reference

| Method | Description |
|---|---|
| `Agent(name, keys_dir="keys", db_path="nexus.db", relay=...)` | Create/load identity, local ledger, and relay settings. |
| `log(event_type, data, counterparty=None)` | Write a signed local activity record. |
| `history()` | Return ledger rows authored by this agent. |
| `all_activity()` | Return all ledger rows in local DB. |
| `create_receipt(event_type, data, counterparty_pubkey)` | Build Agent A signed receipt draft. |
| `countersign_receipt(receipt)` | Add Agent B signature to a valid receipt draft. |
| `verify_receipt(receipt)` | Validate both signatures on a countersigned receipt. |
| `store_receipt(receipt)` | Store verified receipt and proof hash locally. |
| `export_receipt(receipt)` | Serialize receipt JSON string for transport. |
| `import_receipt(json_string)` | Parse receipt JSON back into dict form. |
| `send(event_type, data, to=...)` | Create and dispatch a receipt via relay discovery/send flow. |
| `check_inbox()` | Pull relay envelopes, countersign or finalize, and store receipts. |
| `find(name)` | Query relay for an agent by name. |
| `online_agents()` | List currently discoverable relay agents. |
| `anchor(data_dict, keypair_path=...)` | Anchor deterministic proof hash (mock or Solana). |
| `verify(data_dict, expected_hash)` | Validate data hash against expected proof hash. |

## Built By

Built by Mercury & Vickson of Vickson Enterprises ☿️

No financial features. No tokens. Just signed proof.
