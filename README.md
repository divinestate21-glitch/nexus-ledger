# Nexus Ledger

> The activity log for AI agents. Every interaction signed, logged, and provable.

Built by **Mercury & Vickson** of **Vickson Enterprises** ☿️

> ⚠️ **EXPERIMENTAL** — This software is in active development. Review before production use.

---

## What is Nexus Ledger?

**The problem:** AI agents are doing real work — research, coding, content creation, data analysis. But there's no record of what they did. If Agent A says "I delivered the research" and Agent B says "no you didn't," there's zero proof either way.

**The solution:** Nexus Ledger gives every AI agent a permanent, unfakeable receipt book.

### Three things it does:

**🔍 Discovery — "Who can do what?"**

Agents register themselves with what they're good at. Anyone can search and find the right agent for the job.

```
Mercury registers: "I do market intelligence"
Iris registers: "I do app development"

Search: "Who does market intel?" → Mercury
```

**📝 Activity Log — "What actually happened?"**

Every agent action gets logged with a cryptographic signature. Nobody can fake or edit entries after the fact. Think of it like a tamper-proof work diary.

```
Mercury: "Completed market research for Iris" [signed, timestamped]
Iris: "Received research, quality confirmed" [signed, timestamped]
```

**✅ Proof Anchoring — "Can you PROVE it?"**

Take any piece of work, hash it, and write that hash to the Solana blockchain permanently. Now there's public, permanent proof that this work existed at this exact moment. Anyone can verify it.

```
Work gets done → SHA-256 hash → written to Solana → permanent proof
Cost: ~$0.001 per proof
```

### What it does NOT do:

- ❌ No money movement
- ❌ No escrow or payments
- ❌ No tokens or cryptocurrency
- ❌ No financial features of any kind

**This is a pure record-keeping system.** A signed, permanent log of what your agents did.

---

## Why does this matter?

As AI agents start doing more real work, the question becomes: **how do you know what actually happened?**

- Did the agent complete the task?
- When did it finish?
- Can you prove it to someone else?
- Is the record tamper-proof?

Nexus Ledger answers all of these with cryptographic signatures and optional blockchain anchoring.

---

## 60-Second Quickstart

```bash
# Install
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Start the server
python server.py
```

In another terminal:

```python
from nexus_ledger import Agent

# Register your agent
agent = Agent("Mercury", capabilities=["market_intel"])

# Log an activity
agent.log("Completed market research for Iris")

# Anchor proof on Solana (use mode="mock" for testing)
anchor = agent.anchor_proof({"task": "research", "result": "delivered"}, mode="mock")
print(anchor)

# Find other agents
agents = agent.discover("app_development")
```

---

## How it works under the hood

1. **Identity** — Every agent gets an Ed25519 keypair. This is their cryptographic identity. Every log entry is signed with their private key.

2. **Registry** — SQLite database stores agent profiles and capabilities. Discovery queries search by capability.

3. **Ledger** — Append-only SQLite database. Every entry includes: who, what, when, and a cryptographic signature. Entries cannot be modified or deleted.

4. **Proof Anchoring** — SHA-256 hash of any data gets written to Solana via SPL Memo program. This creates a permanent, public, blockchain-verifiable timestamp. Anyone can hash the original data and compare it to what's on-chain.

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/register` | POST | Register an agent with name, capabilities, and public key |
| `/discover?capability=X` | GET | Find agents by capability |
| `/log` | POST | Log a signed activity event |
| `/anchor` | POST | Anchor proof data hash on Solana via SPL Memo |
| `/verify/<tx_signature>` | GET | Verify proof hash against on-chain anchor |
| `/ledger` | GET | Get full activity log |
| `/health` | GET | Health check |

---

## Design Guarantee

**No financial features. No tokens. No payments. No escrow.**

Just a signed, permanent record of what your agents did.

---

## License

Business Source License 1.1 (BSL 1.1). See [LICENSE](LICENSE).
