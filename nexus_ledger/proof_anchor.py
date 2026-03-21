"""Hashing and Solana proof anchoring."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import time_ns
from typing import Any, Dict


MAINNET_RPC_URL = "https://api.mainnet-beta.solana.com"
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
DEFAULT_KEYPAIR_PATH = "~/.config/solana/id.json"


class AnchorError(RuntimeError):
    pass


def _canonical_json(data_dict: Dict[str, Any]) -> str:
    return json.dumps(data_dict, sort_keys=True, separators=(",", ":"))


def hash(data_dict: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(data_dict).encode("utf-8")).hexdigest()


def _load_keypair(path: str) -> Any:
    from solders.keypair import Keypair

    keypair_path = Path(path).expanduser()
    if not keypair_path.exists():
        raise AnchorError(f"Solana keypair not found: {keypair_path}")

    payload = json.loads(keypair_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise AnchorError(f"Invalid keypair file format: {keypair_path}")
    return Keypair.from_bytes(bytes(payload))


def _mock_anchor(proof_hash: str) -> Dict[str, str]:
    tx_signature = f"mock_{time_ns()}"
    return {
        "hash": proof_hash,
        "tx_signature": tx_signature,
        "solscan_url": f"mock://{tx_signature}",
    }


def anchor(data_dict: Dict[str, Any], keypair_path: str = DEFAULT_KEYPAIR_PATH) -> Dict[str, str]:
    proof_hash = hash(data_dict)
    if os.getenv("NEXUS_LEDGER_ANCHOR_MODE", "mainnet").lower() == "mock":
        return _mock_anchor(proof_hash)

    try:
        from solana.rpc.api import Client
        from solders.instruction import Instruction
        from solders.message import Message
        from solders.pubkey import Pubkey
        from solders.transaction import Transaction
    except ImportError as exc:
        raise AnchorError("Missing Solana dependencies. Install with: pip install 'nexus-ledger[solana]'") from exc

    keypair = _load_keypair(keypair_path)
    client = Client(MAINNET_RPC_URL)

    memo_ix = Instruction(Pubkey.from_string(MEMO_PROGRAM_ID), proof_hash.encode("utf-8"), [])
    latest = client.get_latest_blockhash()
    blockhash = latest.value.blockhash

    message = Message.new_with_blockhash([memo_ix], keypair.pubkey(), blockhash)
    tx = Transaction.new_unsigned(message)
    tx.sign([keypair], blockhash)

    response = client.send_transaction(tx)
    tx_signature = response.value if hasattr(response, "value") else response.get("result")
    tx_signature_str = str(tx_signature)

    return {
        "hash": proof_hash,
        "tx_signature": tx_signature_str,
        "solscan_url": f"https://solscan.io/tx/{tx_signature_str}",
    }


def verify(data_dict: Dict[str, Any], expected_hash: str) -> bool:
    return hash(data_dict) == str(expected_hash).strip().lower()
