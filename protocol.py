"""Core protocol helpers for Nexus Ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import uuid
from typing import Any, Dict, List, Tuple, Union

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


JsonDict = Dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def canonical_json(data: JsonDict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def generate_ed25519_keypair() -> Tuple[str, str]:
    """Return a tuple of (private_key_hex, public_key_hex)."""
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    return signing_key.encode().hex(), verify_key.encode().hex()


def _to_signing_key(signing_key: Union[str, SigningKey]) -> SigningKey:
    if isinstance(signing_key, SigningKey):
        return signing_key
    return SigningKey(bytes.fromhex(signing_key))


def sign_payload(payload: JsonDict, signing_key: Union[str, SigningKey]) -> str:
    key = _to_signing_key(signing_key)
    message = canonical_json(payload).encode("utf-8")
    signed = key.sign(message)
    return signed.signature.hex()


def verify_signature(payload: JsonDict, signature_hex: str, verify_key_hex: str) -> bool:
    message = canonical_json(payload).encode("utf-8")
    try:
        verify_key = VerifyKey(bytes.fromhex(verify_key_hex))
        verify_key.verify(message, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError):
        return False


@dataclass
class AgentIdentity:
    agent_id: str
    name: str
    public_key: str
    capabilities: List[str]
    registered_at: str

    def to_dict(self) -> JsonDict:
        return asdict(self)


__all__ = [
    "AgentIdentity",
    "canonical_json",
    "generate_ed25519_keypair",
    "new_id",
    "sign_payload",
    "utc_now",
    "verify_signature",
]
