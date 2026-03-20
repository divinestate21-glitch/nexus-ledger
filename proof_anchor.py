"""On-chain proof anchoring via SPL Memo on Solana."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional

from protocol import canonical_json

try:
    from solana.rpc.api import Client
    from solders.instruction import Instruction
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solders.transaction import Transaction
except ImportError:  # pragma: no cover - optional dependency path
    Client = None  # type: ignore[assignment]
    Instruction = None  # type: ignore[assignment]
    Keypair = None  # type: ignore[assignment]
    Message = None  # type: ignore[assignment]
    Pubkey = None  # type: ignore[assignment]
    Signature = None  # type: ignore[assignment]
    Transaction = None  # type: ignore[assignment]


MAINNET_RPC_URL = "https://api.mainnet-beta.solana.com"
MAINNET_NETWORK = "mainnet-beta"
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
DEFAULT_KEYPAIR_PATH = "~/.config/solana/id.json"


class ProofAnchorError(RuntimeError):
    pass


class _MockAnchorStore:
    def __init__(self, db_path: str = "nexus.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mock_proof_anchors (
                tx_signature TEXT PRIMARY KEY,
                proof_hash TEXT NOT NULL,
                anchored_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def put(self, tx_signature: str, proof_hash: str, anchored_at: str) -> None:
        self._conn.execute(
            """
            INSERT INTO mock_proof_anchors (tx_signature, proof_hash, anchored_at)
            VALUES (?, ?, ?)
            ON CONFLICT(tx_signature) DO UPDATE SET
                proof_hash=excluded.proof_hash,
                anchored_at=excluded.anchored_at
            """,
            (tx_signature, proof_hash, anchored_at),
        )
        self._conn.commit()

    def get(self, tx_signature: str) -> Optional[Dict[str, str]]:
        row = self._conn.execute(
            "SELECT tx_signature, proof_hash, anchored_at FROM mock_proof_anchors WHERE tx_signature = ?",
            (tx_signature,),
        ).fetchone()
        if row is None:
            return None
        return {"tx_signature": str(row[0]), "proof_hash": str(row[1]), "anchored_at": str(row[2])}


_MOCK_STORE: Optional[_MockAnchorStore] = None


def _mock_store(db_path: str = "nexus.db") -> _MockAnchorStore:
    global _MOCK_STORE
    if _MOCK_STORE is None:
        _MOCK_STORE = _MockAnchorStore(db_path=db_path)
    return _MOCK_STORE


def _solscan_tx_url(signature: str) -> str:
    return f"https://solscan.io/tx/{signature}"


def _proof_hash(data: Dict[str, Any]) -> str:
    canonical = canonical_json(data).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _ensure_solana_deps() -> None:
    if not all([Client, Instruction, Keypair, Message, Pubkey, Signature, Transaction]):
        raise ProofAnchorError("Solana dependencies are missing. Install optional extras: solders, solana")


def _load_keypair(path: str) -> Any:
    keypair_path = Path(path).expanduser()
    if not keypair_path.exists():
        raise ProofAnchorError(f"Solana keypair not found at {keypair_path}")
    payload = json.loads(keypair_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ProofAnchorError(f"Invalid Solana keypair file: {keypair_path}")
    return Keypair.from_bytes(bytes(payload))


def _extract_signature(response: Any) -> str:
    if isinstance(response, dict):
        result = response.get("result")
        if isinstance(result, str):
            return result
    if hasattr(response, "value"):
        return str(response.value)
    return str(response)


def _extract_memo_hash(raw_result: Any) -> str:
    if hasattr(raw_result, "to_json"):
        data = json.loads(raw_result.to_json())
    elif isinstance(raw_result, dict):
        data = raw_result
    else:
        raise ProofAnchorError("Unexpected Solana transaction format")

    tx_obj = data.get("transaction", data)
    message = tx_obj.get("message", {}) if isinstance(tx_obj, dict) else {}
    instructions = message.get("instructions", []) if isinstance(message, dict) else []

    for item in instructions:
        if not isinstance(item, dict):
            continue
        if str(item.get("programId", "")) != MEMO_PROGRAM_ID and str(item.get("program", "")).lower() not in {
            "spl-memo",
            "spl-memo-v1",
        }:
            continue
        parsed = item.get("parsed")
        if isinstance(parsed, str) and parsed.strip():
            return parsed.strip()
        if isinstance(parsed, dict):
            memo = parsed.get("memo")
            if isinstance(memo, str) and memo.strip():
                return memo.strip()

    raise ProofAnchorError("Memo instruction not found in transaction")


def anchor_data(
    data: Dict[str, Any],
    *,
    mode: str = "auto",
    rpc_url: str = MAINNET_RPC_URL,
    keypair_path: str = DEFAULT_KEYPAIR_PATH,
    db_path: str = "nexus.db",
) -> Dict[str, str]:
    resolved_mode = (mode or os.getenv("NEXUS_LEDGER_ANCHOR_MODE", "auto")).strip().lower()
    proof_hash = _proof_hash(data)

    if resolved_mode == "mock":
        from protocol import new_id, utc_now

        tx_signature = new_id("mocktx")
        anchored_at = utc_now()
        _mock_store(db_path=db_path).put(tx_signature, proof_hash, anchored_at)
        return {
            "mode": "mock",
            "network": "mocknet",
            "program_id": MEMO_PROGRAM_ID,
            "proof_hash": proof_hash,
            "tx_signature": tx_signature,
            "anchored_at": anchored_at,
            "explorer_url": f"mock://{tx_signature}",
        }

    _ensure_solana_deps()
    keypair = _load_keypair(keypair_path)
    client = Client(rpc_url)

    memo_ix = Instruction(Pubkey.from_string(MEMO_PROGRAM_ID), proof_hash.encode("utf-8"), [])
    recent_blockhash = client.get_latest_blockhash().value.blockhash
    message = Message.new_with_blockhash([memo_ix], keypair.pubkey(), recent_blockhash)
    tx = Transaction.new_unsigned(message)
    tx.sign([keypair], recent_blockhash)

    response = client.send_transaction(tx)
    tx_signature = _extract_signature(response)
    return {
        "mode": "solana",
        "network": MAINNET_NETWORK,
        "program_id": MEMO_PROGRAM_ID,
        "proof_hash": proof_hash,
        "tx_signature": tx_signature,
        "explorer_url": _solscan_tx_url(tx_signature),
    }


def get_anchor(
    tx_signature: str,
    *,
    mode: str = "auto",
    rpc_url: str = MAINNET_RPC_URL,
    db_path: str = "nexus.db",
) -> Dict[str, str]:
    resolved_mode = (mode or os.getenv("NEXUS_LEDGER_ANCHOR_MODE", "auto")).strip().lower()

    if resolved_mode == "mock":
        anchor = _mock_store(db_path=db_path).get(tx_signature)
        if anchor is None:
            raise ProofAnchorError(f"Mock anchor not found: {tx_signature}")
        return {
            "mode": "mock",
            "network": "mocknet",
            "program_id": MEMO_PROGRAM_ID,
            "tx_signature": anchor["tx_signature"],
            "proof_hash": anchor["proof_hash"],
            "anchored_at": anchor["anchored_at"],
            "explorer_url": f"mock://{anchor['tx_signature']}",
        }

    _ensure_solana_deps()
    client = Client(rpc_url)
    response = client.get_transaction(
        Signature.from_string(tx_signature),
        encoding="jsonParsed",
        max_supported_transaction_version=0,
    )
    result = response.value if hasattr(response, "value") else response.get("result")
    if result is None:
        raise ProofAnchorError(f"Transaction not found: {tx_signature}")

    return {
        "mode": "solana",
        "network": MAINNET_NETWORK,
        "program_id": MEMO_PROGRAM_ID,
        "tx_signature": tx_signature,
        "proof_hash": _extract_memo_hash(result),
        "explorer_url": _solscan_tx_url(tx_signature),
    }


def verify_anchor(
    tx_signature: str,
    *,
    data: Optional[Dict[str, Any]] = None,
    proof_hash: Optional[str] = None,
    mode: str = "auto",
    rpc_url: str = MAINNET_RPC_URL,
    db_path: str = "nexus.db",
) -> Dict[str, Any]:
    anchor = get_anchor(tx_signature, mode=mode, rpc_url=rpc_url, db_path=db_path)
    provided_hash = (proof_hash or (_proof_hash(data) if data is not None else "")).strip().lower()
    anchored_hash = str(anchor["proof_hash"]).strip().lower()

    if not provided_hash:
        return {
            "verified": False,
            "reason": "No proof data or proof_hash provided",
            "anchored": anchor,
        }

    return {
        "verified": provided_hash == anchored_hash,
        "provided_hash": provided_hash,
        "anchored": anchor,
    }
