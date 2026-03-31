"""Command-line interface for Nexus Ledger."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .agent import Agent, verify_receipt_dict
from .relay_manager import DEFAULT_RELAYS


def _build_agent(args: argparse.Namespace) -> Agent:
    relays = args.relays if getattr(args, "relays", None) else list(DEFAULT_RELAYS)
    return Agent(
        args.name,
        keys_dir=args.keys_dir,
        db_path=args.db_path,
        relays=relays,
    )


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def cmd_init(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    _print({"name": agent.name, "did": agent.did, "public_key": agent.public_key, "relays": agent.relays})
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    data = json.loads(args.data_json)
    receipt = agent.send(args.event, data, to=args.to, encrypted=args.encrypted)
    _print(receipt)
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    receipts = agent.check_inbox()
    _print(receipts)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    _print(agent.history())
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    _print(agent.online_agents())
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    receipt = json.loads(args.receipt_json)
    ok = verify_receipt_dict(receipt) if isinstance(receipt, dict) else False
    _print({"valid": bool(ok)})
    return 0 if ok else 1


def cmd_trust(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    target = args.target or agent.public_key
    report = agent.get_trust_report(target)
    _print(report)
    return 0


def cmd_task_chain(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    chain = agent.get_task_chain(args.task_id)
    _print(chain)
    return 0


def cmd_anchor(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    chain = args.chain or "base"
    result = agent.anchor_to_eth(chain=chain)
    _print(result)
    return 0


def cmd_anchor_all(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    chain = args.chain or "base"
    result = agent.anchor_all_to_eth(chain=chain)
    _print(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus-ledger")
    parser.add_argument("--name", default="Mercury")
    parser.add_argument("--keys-dir", default="keys")
    parser.add_argument("--db-path", default="nexus.db")
    parser.add_argument("--relays", nargs="*", default=list(DEFAULT_RELAYS))

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_cmd = subparsers.add_parser("init", help="create or load local agent identity")
    init_cmd.set_defaults(func=cmd_init)

    send_cmd = subparsers.add_parser("send", help="send a receipt")
    send_cmd.add_argument("to")
    send_cmd.add_argument("event")
    send_cmd.add_argument("data_json")
    send_cmd.add_argument("--encrypted", action="store_true")
    send_cmd.set_defaults(func=cmd_send)

    inbox_cmd = subparsers.add_parser("inbox", help="check incoming receipts")
    inbox_cmd.set_defaults(func=cmd_inbox)

    history_cmd = subparsers.add_parser("history", help="show local activity log")
    history_cmd.set_defaults(func=cmd_history)

    agents_cmd = subparsers.add_parser("agents", help="list online agents on relay")
    agents_cmd.set_defaults(func=cmd_agents)

    verify_cmd = subparsers.add_parser("verify", help="verify receipt signatures")
    verify_cmd.add_argument("receipt_json")
    verify_cmd.set_defaults(func=cmd_verify)

    trust_cmd = subparsers.add_parser("trust", help="show trust score and report")
    trust_cmd.add_argument("--target", default=None, help="agent public key (default: self)")
    trust_cmd.set_defaults(func=cmd_trust)

    chain_cmd = subparsers.add_parser("task-chain", help="show receipt chain for a task")
    chain_cmd.add_argument("task_id")
    chain_cmd.set_defaults(func=cmd_task_chain)

    anchor_cmd = subparsers.add_parser("anchor", help="anchor latest receipt to Ethereum/Base/Sepolia")
    anchor_cmd.add_argument("--chain", default="base", choices=["base", "ethereum", "sepolia"], help="target chain")
    anchor_cmd.set_defaults(func=cmd_anchor)

    anchor_all_cmd = subparsers.add_parser("anchor-all", help="batch-anchor all receipts to chain")
    anchor_all_cmd.add_argument("--chain", default="base", choices=["base", "ethereum", "sepolia"], help="target chain")
    anchor_all_cmd.set_defaults(func=cmd_anchor_all)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
