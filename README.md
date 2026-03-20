# Nexus Ledger — Signed proof that your agents did the work

Built by Mercury & Vickson of Vickson Enterprises ☿️

Nexus Ledger is a pure sign + log + anchor library.

```python
from nexus_ledger import Agent

agent = Agent("Mercury")
agent.log("delivered_research", {"topic": "market analysis"})
tx = agent.anchor({"task": "research", "result": "complete"})
```

## Core model

- keypair = identity
- signature = proof
- Solana memo anchor = permanent record

No server needed. No registration. No financial features.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional Solana anchoring dependencies:

```bash
pip install -e '.[solana]'
```

## Full example

```python
from nexus_ledger import Agent

agent_a = Agent("Mercury")
agent_b = Agent("Iris")

agent_a.log("delivered_research", {"topic": "market analysis"}, counterparty=agent_b)

tx = agent_a.anchor({"task": "research", "result": "complete"})
assert agent_a.verify({"task": "research", "result": "complete"}, tx["hash"])

print(agent_a.history())
```

## Demo

```bash
python demo.py
python demo.py --mainnet
```

- default mode is mock anchoring
- `--mainnet` sends real SPL Memo anchors to Solana mainnet-beta

## License

Business Source License 1.1 (BSL 1.1). See [LICENSE](LICENSE).
