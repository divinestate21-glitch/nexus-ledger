"""DIDComm-inspired transport for cross-machine receipt exchange."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Callable, Dict, Optional
from urllib import request
from urllib.error import HTTPError, URLError

from nacl.signing import SigningKey

from protocol import sign, verify


JsonDict = Dict[str, Any]

_DID_PREFIX = "did:key:z"
_MULTICODEC_ED25519_PREFIX = b"\xed\x01"
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {ch: idx for idx, ch in enumerate(_BASE58_ALPHABET)}


class TransportError(RuntimeError):
    """Raised when transport operations fail."""


def _canonical_json(data_dict: JsonDict) -> bytes:
    return json.dumps(data_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b58_encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number > 0:
        number, remainder = divmod(number, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded

    leading_zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    return ("1" * leading_zeroes) + (encoded or "1")


def _b58_decode(encoded: str) -> bytes:
    number = 0
    for char in encoded:
        if char not in _BASE58_INDEX:
            raise ValueError("Invalid base58btc character")
        number = number * 58 + _BASE58_INDEX[char]

    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_ones = len(encoded) - len(encoded.lstrip("1"))
    return (b"\x00" * leading_ones) + raw


def public_key_to_did(public_key_hex: str) -> str:
    public_key = bytes.fromhex(str(public_key_hex))
    if len(public_key) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return _DID_PREFIX + _b58_encode(_MULTICODEC_ED25519_PREFIX + public_key)


def private_key_to_did(private_key_hex: str) -> str:
    signing_key = SigningKey(bytes.fromhex(str(private_key_hex)))
    return public_key_to_did(signing_key.verify_key.encode().hex())


def resolve_did(did_string: str) -> bytes:
    did = str(did_string)
    if not did.startswith(_DID_PREFIX):
        raise ValueError("Unsupported DID format")

    decoded = _b58_decode(did[len(_DID_PREFIX) :])
    if not decoded.startswith(_MULTICODEC_ED25519_PREFIX):
        raise ValueError("Unsupported DID multicodec")

    public_key = decoded[len(_MULTICODEC_ED25519_PREFIX) :]
    if len(public_key) != 32:
        raise ValueError("Invalid Ed25519 public key length in DID")
    return public_key


def pack_receipt(receipt_dict: JsonDict, sender_private_key: str) -> str:
    sender_did = private_key_to_did(sender_private_key)
    signed_payload = {
        "sender_did": sender_did,
        "payload": receipt_dict,
    }
    envelope = {
        "type": "nexus.didcomm.receipt.v1",
        "sender_did": sender_did,
        "signature": sign(sender_private_key, signed_payload),
        "payload": receipt_dict,
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"))


def unpack_receipt(envelope_json: str, expected_sender_did: Optional[str] = None) -> JsonDict:
    envelope = json.loads(envelope_json)
    if not isinstance(envelope, dict):
        raise ValueError("Envelope JSON must decode to an object")

    required = ["sender_did", "signature", "payload"]
    missing = [field for field in required if field not in envelope]
    if missing:
        raise ValueError(f"Envelope missing required fields: {', '.join(missing)}")

    sender_did = str(envelope["sender_did"])
    if expected_sender_did and sender_did != str(expected_sender_did):
        raise ValueError("Unexpected sender DID")

    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ValueError("Envelope payload must be an object")

    signed_payload = {
        "sender_did": sender_did,
        "payload": payload,
    }
    sender_pubkey_hex = resolve_did(sender_did).hex()
    if not verify(sender_pubkey_hex, signed_payload, str(envelope["signature"])):
        raise ValueError("Invalid envelope signature")

    return payload


class HTTPTransport:
    def send(self, url: str, envelope: str) -> str:
        target_url = str(url).strip()
        if not target_url:
            raise ValueError("HTTP endpoint URL is required")

        body = envelope.encode("utf-8")
        req = request.Request(
            target_url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=10) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TransportError(f"HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise TransportError(f"HTTP transport failed: {exc.reason}") from exc

    def start_listener(
        self,
        port: int,
        callback: Callable[[str], str],
        did_provider: Optional[Callable[[], str]] = None,
    ) -> ThreadingHTTPServer:
        callback_fn = callback
        did_provider_fn = did_provider

        class ListenerHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/did" and did_provider_fn is not None:
                    did_body = json.dumps({"did": did_provider_fn()}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(did_body)))
                    self.end_headers()
                    self.wfile.write(did_body)
                    return

                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                content_length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(content_length).decode("utf-8")
                try:
                    response_body = callback_fn(payload)
                    if not isinstance(response_body, str):
                        response_body = json.dumps(response_body)
                    body = response_body.encode("utf-8")
                    self.send_response(200)
                except Exception as exc:  # pragma: no cover - defensive server path
                    body = json.dumps({"error": str(exc)}).encode("utf-8")
                    self.send_response(400)

                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        server = ThreadingHTTPServer(("0.0.0.0", int(port)), ListenerHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server


class FileTransport:
    def send(self, filepath: str, envelope: str) -> str:
        path = Path(filepath)
        if path.is_dir() or str(filepath).endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            filename = datetime.now(timezone.utc).strftime("envelope_%Y%m%dT%H%M%S%fZ.json")
            output = path / filename
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            output = path

        output.write_text(envelope, encoding="utf-8")
        return str(output)

    def receive(self, filepath: str) -> str:
        path = Path(filepath)
        if path.is_dir():
            envelopes = sorted(path.glob("*.json"), key=lambda p: p.stat().st_mtime)
            if not envelopes:
                raise FileNotFoundError("No envelope JSON files found")
            path = envelopes[-1]
        return path.read_text(encoding="utf-8")


def send_receipt(
    agent: Any,
    to_did: str,
    receipt: JsonDict,
    transport: str = "http",
    endpoint: Optional[str] = None,
) -> str:
    if not endpoint:
        raise ValueError("endpoint is required")

    recipient_public_key = resolve_did(to_did).hex()
    if str(receipt.get("agent_b_pubkey")) != recipient_public_key:
        raise ValueError("Receipt recipient does not match to_did")

    envelope = pack_receipt(receipt, agent.private_key)
    mode = str(transport).strip().lower()

    if mode == "http":
        return HTTPTransport().send(endpoint, envelope)
    if mode == "file":
        FileTransport().send(endpoint, envelope)
        return envelope
    raise ValueError(f"Unsupported transport: {transport}")


def receive_and_countersign(agent: Any, envelope_json: str) -> JsonDict:
    envelope = json.loads(envelope_json)
    if not isinstance(envelope, dict):
        raise ValueError("Envelope JSON must decode to an object")

    sender_did = str(envelope.get("sender_did", ""))
    receipt = unpack_receipt(envelope_json, expected_sender_did=sender_did)

    expected_sender_pubkey = resolve_did(sender_did).hex()
    if str(receipt.get("agent_a_pubkey")) != expected_sender_pubkey:
        raise ValueError("Envelope sender DID does not match receipt signer")

    countersigned = agent.countersign_receipt(receipt)
    agent.store_receipt(countersigned)
    return countersigned
