"""Minimal signing protocol for Nexus Ledger."""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


JsonDict = Dict[str, Any]


def _canonical_json(data_dict: JsonDict) -> bytes:
    return json.dumps(data_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")


def generate_keypair() -> Tuple[str, str]:
    """Return (private_key_hex, public_key_hex)."""
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    return signing_key.encode().hex(), verify_key.encode().hex()


def sign(private_key: str, data_dict: JsonDict) -> str:
    """Sign a JSON-serializable dict and return hex signature."""
    message = _canonical_json(data_dict)
    signature = SigningKey(bytes.fromhex(private_key)).sign(message).signature
    return signature.hex()


def verify(public_key: str, data_dict: JsonDict, signature: str) -> bool:
    """Verify signature for a dict."""
    message = _canonical_json(data_dict)
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(message, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False
