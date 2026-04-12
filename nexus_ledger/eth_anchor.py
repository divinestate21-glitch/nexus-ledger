"""Ethereum receipt anchoring — post receipt hashes on-chain as immutable proof."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional
from urllib import request
from urllib.error import URLError

# Default to Base mainnet (L2 — cheap, fast, Ethereum-secured)
DEFAULT_ETH_RPC = "https://mainnet.base.org"
# Fallback: Ethereum Sepolia testnet for zero-cost anchoring
SEPOLIA_RPC = "https://ethereum-sepolia-rpc.publicnode.com"


class EthAnchorError(RuntimeError):
    """Raised when Ethereum anchoring fails."""


def _canonical_json(data_dict: Dict[str, Any]) -> str:
    return json.dumps(data_dict, sort_keys=True, separators=(",", ":"))


def receipt_hash(receipt: Dict[str, Any]) -> str:
    """SHA-256 hash of a Nexus Ledger receipt for on-chain anchoring."""
    payload = {
        "timestamp": str(receipt.get("timestamp", "")),
        "event_type": str(receipt.get("event_type", "")),
        "data": receipt.get("data", {}),
        "agent_a_pubkey": str(receipt.get("agent_a_pubkey", "")),
        "agent_b_pubkey": str(receipt.get("agent_b_pubkey", "")),
        "agent_a_signature": str(receipt.get("agent_a_signature", "")),
        "agent_b_signature": str(receipt.get("agent_b_signature", "")),
        "parent_receipt_hash": str(receipt.get("parent_receipt_hash", "")),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _eth_rpc(rpc_url: str, method: str, params: list) -> Any:
    """Raw JSON-RPC call to an Ethereum node."""
    body = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }).encode("utf-8")
    req = request.Request(
        rpc_url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if "error" in result:
                raise EthAnchorError(f"RPC error: {result['error']}")
            return result.get("result")
    except URLError as e:
        raise EthAnchorError(f"RPC connection failed: {e}") from e


def anchor_to_ethereum(
    receipt: Dict[str, Any],
    *,
    private_key: Optional[str] = None,
    rpc_url: Optional[str] = None,
    chain: str = "base",
) -> Dict[str, Any]:
    """
    Anchor a Nexus Ledger receipt hash to Ethereum (or Base L2).
    
    Sends a self-transfer with the receipt hash in calldata.
    Returns the transaction hash and verification info.
    
    Args:
        receipt: The Nexus Ledger receipt dict
        private_key: Ethereum private key (hex, no 0x prefix). 
                     Falls back to NEXUS_ETH_PRIVATE_KEY env var.
        rpc_url: RPC endpoint. Defaults to Base mainnet.
        chain: "base" (default, cheap) or "sepolia" (free testnet) or "ethereum" (mainnet)
    """
    key = private_key or os.getenv("NEXUS_ETH_PRIVATE_KEY", "")
    if not key:
        # Return a verifiable proof without on-chain anchoring
        proof = receipt_hash(receipt)
        return {
            "status": "local_only",
            "receipt_hash": proof,
            "message": "No ETH private key — proof generated locally. Set NEXUS_ETH_PRIVATE_KEY to anchor on-chain.",
            "verify_command": f"python -c \"from nexus_ledger.eth_anchor import verify_receipt; print(verify_receipt(receipt, '{proof}'))\"",
        }

    try:
        from eth_account import Account
        from eth_account.signers.local import LocalAccount
    except ImportError:
        raise EthAnchorError(
            "Missing web3 dependencies. Install with: pip install 'nexus-ledger[ethereum]'"
        ) from None

    if rpc_url is None:
        rpc_url = {
            "base": DEFAULT_ETH_RPC,
            "sepolia": SEPOLIA_RPC,
            "ethereum": "https://ethereum-rpc.publicnode.com",
        }.get(chain, DEFAULT_ETH_RPC)

    proof = receipt_hash(receipt)
    calldata = "0x" + proof.encode("utf-8").hex()

    account: LocalAccount = Account.from_key(key if key.startswith("0x") else f"0x{key}")
    sender = account.address

    # Get nonce
    nonce_hex = _eth_rpc(rpc_url, "eth_getTransactionCount", [sender, "latest"])
    nonce = int(nonce_hex, 16)

    # Get gas price
    gas_price_hex = _eth_rpc(rpc_url, "eth_gasPrice", [])
    gas_price = int(gas_price_hex, 16)

    # Build transaction (self-transfer with receipt hash as calldata)
    tx = {
        "nonce": nonce,
        "to": sender,  # self-transfer
        "value": 0,
        "gas": 50000,
        "gasPrice": gas_price,
        "data": calldata,
        "chainId": {"base": 8453, "sepolia": 11155111, "ethereum": 1}.get(chain, 8453),
    }

    signed = account.sign_transaction(tx)
    raw_tx = "0x" + signed.raw_transaction.hex()

    tx_hash = _eth_rpc(rpc_url, "eth_sendRawTransaction", [raw_tx])

    explorer_base = {
        "base": "https://basescan.org",
        "sepolia": "https://sepolia.etherscan.io",
        "ethereum": "https://etherscan.io",
    }.get(chain, "https://basescan.org")

    return {
        "status": "anchored",
        "receipt_hash": proof,
        "tx_hash": tx_hash,
        "chain": chain,
        "explorer_url": f"{explorer_base}/tx/{tx_hash}",
        "sender": sender,
        "calldata": calldata,
        "verify_command": f"Cast calldata at {explorer_base}/tx/{tx_hash} should contain: {proof}",
    }


def verify_receipt(receipt: Dict[str, Any], expected_hash: str) -> bool:
    """Verify a receipt matches its expected hash."""
    return receipt_hash(receipt) == str(expected_hash).strip().lower()


def batch_anchor(
    receipts: list,
    **kwargs,
) -> Dict[str, Any]:
    """
    Anchor multiple receipt hashes in a single transaction.
    Concatenates all hashes into one calldata payload.
    """
    if not receipts:
        raise EthAnchorError("No receipts to anchor")

    hashes = [receipt_hash(r) for r in receipts]
    combined = hashlib.sha256("|".join(hashes).encode("utf-8")).hexdigest()

    # Create a synthetic "batch" receipt for anchoring
    batch_receipt = {
        "timestamp": receipts[-1].get("timestamp", ""),
        "event_type": "batch_anchor",
        "data": {"receipt_count": len(receipts), "combined_hash": combined, "individual_hashes": hashes},
        "agent_a_pubkey": receipts[0].get("agent_a_pubkey", ""),
        "agent_b_pubkey": receipts[0].get("agent_b_pubkey", ""),
        "agent_a_signature": "",
        "agent_b_signature": "",
        "parent_receipt_hash": "",
    }

    result = anchor_to_ethereum(batch_receipt, **kwargs)
    result["batch_info"] = {
        "receipt_count": len(receipts),
        "combined_hash": combined,
        "individual_hashes": hashes,
    }
    return result
