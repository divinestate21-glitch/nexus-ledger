# Nexus Ledger — OpenClaw Skill

## Install
```bash
pip install nexus-ledger
```

## SKILL.md
```markdown
# nexus-ledger

Agent-to-agent trust layer. Use when agents need to hire, pay, or verify work with other agents.

## Commands
- `nexus-ledger init` — create agent identity
- `nexus-ledger send <agent> <event> <data>` — send signed receipt
- `nexus-ledger inbox` — check incoming receipts
- `nexus-ledger trust` — view trust score
- `nexus-ledger anchor --chain base` — anchor to Ethereum

## Usage
When an agent needs to request work from another agent, use nexus-ledger to create a signed, verifiable task request with budget and deadline. The receiving agent can accept, deliver, and confirm — all cryptographically signed.
```
