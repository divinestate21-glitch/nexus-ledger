# Nexus Ledger - The Activity Log for AI Agents

Every agent interaction. Signed. Logged. Provable.

Built by Mercury & Vickson of Vickson Enterprises ☿️

## Experimental Notice

Nexus Ledger is experimental software. Use it carefully, validate outputs independently, and review operational/security assumptions before production deployment.

## 60-Second Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python server.py
```

In another terminal:

```python
from nexus_ledger import Agent

agent = Agent("Mercury", capabilities=["market_intel"])
agent.log("Completed market research for Iris")
anchor = agent.anchor_proof({"task": "research", "result": "delivered"}, mode="mock")
print(anchor)
```

## API Reference

- `POST /register` - Register an agent with `name`, `capabilities`, and `public_key`
- `GET /discover?capability=X` - Discover registered agents by capability
- `POST /log` - Log a signed activity event
- `POST /anchor` - Anchor proof data hash via SPL Memo
- `GET /verify/<tx_signature>` - Verify provided proof hash/data against the anchored hash
- `GET /ledger` - Return full activity ledger entries
- `GET /health` - Health check

## Design Guarantee

No financial features. No tokens. Just a signed, permanent record of what your agents did.

## License

Business Source License 1.1 (BSL 1.1). See [LICENSE](LICENSE).
