"""Receipt encryption helpers using NaCl box over X25519 keys."""

from __future__ import annotations

import json
from typing import Any, Dict

from nacl import utils
from nacl.public import Box
from nacl.signing import SigningKey, VerifyKey


JsonDict = Dict[str, Any]


def _signing_key(private_ed25519_hex: str) -> SigningKey:
    return SigningKey(bytes.fromhex(str(private_ed25519_hex)))


def _verify_key(public_ed25519_hex: str) -> VerifyKey:
    return VerifyKey(bytes.fromhex(str(public_ed25519_hex)))


def encrypt_payload(payload: JsonDict, sender_private_ed25519_hex: str, recipient_public_ed25519_hex: str) -> JsonDict:
    sender_signing = _signing_key(sender_private_ed25519_hex)
    sender_curve_private = sender_signing.to_curve25519_private_key()
    recipient_curve_public = _verify_key(recipient_public_ed25519_hex).to_curve25519_public_key()

    box = Box(sender_curve_private, recipient_curve_public)
    nonce = utils.random(Box.NONCE_SIZE)
    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ciphertext = box.encrypt(message, nonce).ciphertext

    return {
        "encrypted": True,
        "algorithm": "nacl.box.x25519-xsalsa20-poly1305",
        "sender_pubkey": sender_signing.verify_key.encode().hex(),
        "recipient_pubkey": str(recipient_public_ed25519_hex),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
    }


def decrypt_payload(encrypted_envelope: JsonDict, recipient_private_ed25519_hex: str) -> JsonDict:
    required = ["encrypted", "sender_pubkey", "nonce", "ciphertext"]
    missing = [field for field in required if field not in encrypted_envelope]
    if missing:
        raise ValueError(f"Encrypted envelope missing required fields: {', '.join(missing)}")

    sender_pubkey = str(encrypted_envelope["sender_pubkey"])
    nonce = bytes.fromhex(str(encrypted_envelope["nonce"]))
    ciphertext = bytes.fromhex(str(encrypted_envelope["ciphertext"]))

    recipient_curve_private = _signing_key(recipient_private_ed25519_hex).to_curve25519_private_key()
    sender_curve_public = _verify_key(sender_pubkey).to_curve25519_public_key()

    box = Box(recipient_curve_private, sender_curve_public)
    plaintext = box.decrypt(ciphertext, nonce)
    decoded = json.loads(plaintext.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Decrypted payload must be a JSON object")
    return decoded
