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


def cmd_verify_dep(args: argparse.Namespace) -> int:
    """Verify a package dependency against recorded receipts."""
    agent = _build_agent(args)
    against = getattr(args, "against", "registry") or "registry"
    ok = agent.verify_dependency(args.package, args.version, against=against)
    status = "SAFE" if ok else "ALERT"
    _print({
        "status": status,
        "package": args.package,
        "version": args.version,
        "registry": getattr(args, "registry", ""),
        "verified": ok,
    })
    return 0 if ok else 1


def cmd_audit_deps(args: argparse.Namespace) -> int:
    """List all dependency installation receipts."""
    agent = _build_agent(args)
    receipts = agent.dependency_audit()
    _print(receipts)
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

    # Supply Chain Trust commands (v5.0)
    verify_dep_cmd = subparsers.add_parser(
        "verify-dep",
        help="verify a package dependency hash against recorded receipts",
    )
    verify_dep_cmd.add_argument("--package", required=True, help="Package name (e.g., axios)")
    verify_dep_cmd.add_argument("--version", required=True, help="Package version (e.g., 1.14.1)")
    verify_dep_cmd.add_argument("--registry", default="", help="Registry name (e.g., npm, pypi)")
    verify_dep_cmd.add_argument(
        "--against",
        default="registry",
        help="Verification mode: 'registry' (default) or a specific hash",
    )
    verify_dep_cmd.set_defaults(func=cmd_verify_dep)

    audit_deps_cmd = subparsers.add_parser(
        "audit-deps",
        help="list all recorded dependency installation receipts",
    )
    audit_deps_cmd.set_defaults(func=cmd_audit_deps)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
