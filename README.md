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
pip install nexus-ledger
```

**If that doesn't work:**

| Problem | Fix |
|---------|-----|
| `command not found: pip` | Try `pip3 install nexus-ledger` |
| Still not found | Try `python3 -m pip install nexus-ledger` |
| "externally managed environment" | Try `python3 -m pip install nexus-ledger --user` |
| Still stuck | `python3 -m venv .venv && source .venv/bin/activate && pip install nexus-ledger` |

**Requirements:** Python 3.10+. That's it.

Optional — for on-chain proof anchoring on Solana:

```bash
pip install nexus-ledger[solana]
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
python exchange_demo.py
python cross_machine_demo.py
```

- default mode is mock anchoring
- `--mainnet` sends real SPL Memo anchors to Solana mainnet-beta

## Cross-Machine Usage

Your keypair IS your identity. Your DID is your address.

Each agent exposes a `did:key:z6Mk...` string derived directly from its Ed25519 public key. There is no registry and no central server.

Machine A:

```python
from nexus_ledger import Agent

agent = Agent("Machine-A")
agent.start_listener(8765)
```

Machine B:

```python
from nexus_ledger import Agent

agent_b = Agent("Machine-B")
agent_a_did = "did:key:z6Mk..."

response_envelope = agent_b.send_receipt(
    agent_a_did,
    "delivered_research",
    {"result": "complete"},
    endpoint="http://machine-a:8765",
)
final_receipt = agent_b.receive_receipt(response_envelope)
```

`cross_machine_demo.py` supports both localhost simulation and real two-machine runs:

```bash
python cross_machine_demo.py
python cross_machine_demo.py --listen 8765
python cross_machine_demo.py --send 192.168.1.10:8765
```

No central server. No registration. Just two agents talking directly.

## License

Business Source License 1.1 (BSL 1.1). See [LICENSE](LICENSE).
