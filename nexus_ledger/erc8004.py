"""ERC-8004 Base mainnet integration utilities."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib import request
from urllib.error import URLError

import os

BASE_MAINNET_RPC = os.getenv("NEXUS_BASE_RPC", "https://mainnet.base.org")
REGISTRATION_TX_HASH = os.getenv("NEXUS_REGISTRATION_TX", "0xb80ee780354286184c5d68b94c68c95c35a9786068b325b78d7eea4a622e907f")
HACKATHON_AGENT_ID = int(os.getenv("NEXUS_AGENT_ID", "35281"))
HACKATHON_WALLET = os.getenv("NEXUS_WALLET", "0xbbFb4Df7450FAB448e6bd2a138D0C241834848f9")

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
FUNCTION_SELECTORS = {
    "name()": "06fdde03",
    "symbol()": "95d89b41",
    "ownerOf(uint256)": "6352211e",
    "tokenURI(uint256)": "c87b56dd",
    "reputationOf(uint256)": "d85d3d27",
    "getReputation(uint256)": "b79011de",
    "getAgentReputation(uint256)": "f9f2f419",
    "scoreOf(uint256)": "f2d873a6",
    "postReputation(uint256,bytes32,uint8,string)": "9d9f3522",
    "postFeedback(uint256,bytes32,uint8,string)": "1f7a8011",
    "postValidation(uint256,bytes32,bool)": "776ecf9d",
    "recordValidation(uint256,bytes32,bool)": "cf177b6f",
}


class ERC8004Error(RuntimeError):
    """Raised when ERC-8004 RPC operations fail."""


@dataclass
class RegistryAddresses:
    identity_registry: Optional[str]
    reputation_registry: Optional[str]
    validation_registry: Optional[str]
    registration_block: Optional[int]
    registration_tx_hash: str


class ERC8004:
    def __init__(
        self,
        *,
        rpc_url: str = BASE_MAINNET_RPC,
        registration_tx_hash: str = REGISTRATION_TX_HASH,
        default_agent_id: int = HACKATHON_AGENT_ID,
        default_wallet: str = HACKATHON_WALLET,
    ) -> None:
        self.rpc_url = str(rpc_url).strip() or BASE_MAINNET_RPC
        self.registration_tx_hash = str(registration_tx_hash).strip() or REGISTRATION_TX_HASH
        self.default_agent_id = int(default_agent_id)
        self.default_wallet = self._normalize_address(default_wallet)
        self._registry_cache: Optional[RegistryAddresses] = None

    def get_agent_identity(self, agent_id: int) -> Dict[str, Any]:
        registries = self._discover_registry_addresses(agent_id=agent_id, wallet=self.default_wallet)
        if not registries.identity_registry:
            raise ERC8004Error("Could not infer ERC-8004 identity registry address from registration transaction")

        contract = registries.identity_registry
        owner = self._decode_address(self._eth_call(contract, self._encode_call("ownerOf(uint256)", [self._encode_uint256(agent_id)])))

        token_uri = None
        try:
            token_uri = self._decode_string(
                self._eth_call(contract, self._encode_call("tokenURI(uint256)", [self._encode_uint256(agent_id)]))
            )
        except ERC8004Error:
            token_uri = None

        name = None
        symbol = None
        try:
            name = self._decode_string(self._eth_call(contract, self._encode_call("name()", [])))
        except ERC8004Error:
            name = None
        try:
            symbol = self._decode_string(self._eth_call(contract, self._encode_call("symbol()", [])))
        except ERC8004Error:
            symbol = None

        metadata = self._fetch_metadata(token_uri) if token_uri else None

        return {
            "agent_id": int(agent_id),
            "wallet": owner,
            "identity_registry": contract,
            "collection": {"name": name, "symbol": symbol},
            "token_uri": token_uri,
            "metadata": metadata,
            "registration_tx_hash": registries.registration_tx_hash,
            "registration_block": registries.registration_block,
            "reputation_registry": registries.reputation_registry,
            "validation_registry": registries.validation_registry,
            "rpc_url": self.rpc_url,
        }

    def get_reputation(self, agent_id: int) -> Dict[str, Any]:
        registries = self._discover_registry_addresses(agent_id=agent_id, wallet=self.default_wallet)
        contract = registries.reputation_registry
        if not contract:
            return {
                "agent_id": int(agent_id),
                "reputation_registry": None,
                "signals": [],
                "summary": {},
                "error": "Could not infer reputation registry address from registration transaction",
            }

        topic_agent = self._topic_uint256(agent_id)
        from_block = self._to_hex(registries.registration_block or 0)

        logs: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for topics in ([None, topic_agent], [None, None, topic_agent]):
            for entry in self._eth_get_logs(address=contract, topics=topics, from_block=from_block):
                key = (str(entry.get("transactionHash", "")), str(entry.get("logIndex", "")))
                logs[key] = entry

        summary = self._read_reputation_summary(contract, agent_id)

        return {
            "agent_id": int(agent_id),
            "reputation_registry": contract,
            "signals": [self._normalize_log(log) for _, log in sorted(logs.items())],
            "summary": summary,
            "from_block": registries.registration_block,
        }

    def post_reputation(self, agent_id: int, receipt_hash: str, rating: int, comment: str) -> Dict[str, Any]:
        registries = self._discover_registry_addresses(agent_id=agent_id, wallet=self.default_wallet)
        contract = registries.reputation_registry
        if not contract:
            raise ERC8004Error("Reputation registry address unavailable")

        private_key = os.getenv("NEXUS_LEDGER_PRIVATE_KEY", "").strip()
        if not private_key:
            raise ERC8004Error("Set NEXUS_LEDGER_PRIVATE_KEY to submit on-chain reputation")

        if not (0 <= int(rating) <= 255):
            raise ValueError("rating must be between 0 and 255")

        receipt_bytes32 = self._normalize_bytes32(receipt_hash)
        payloads = [
            self._encode_call(
                "postReputation(uint256,bytes32,uint8,string)",
                [
                    self._encode_uint256(agent_id),
                    self._encode_bytes32(receipt_bytes32),
                    self._encode_uint256(int(rating)),
                    self._encode_string(comment),
                ],
            ),
            self._encode_call(
                "postFeedback(uint256,bytes32,uint8,string)",
                [
                    self._encode_uint256(agent_id),
                    self._encode_bytes32(receipt_bytes32),
                    self._encode_uint256(int(rating)),
                    self._encode_string(comment),
                ],
            ),
        ]

        last_error: Optional[Exception] = None
        for payload in payloads:
            try:
                tx_hash = self._send_transaction(contract, payload, private_key)
                return {
                    "tx_hash": tx_hash,
                    "agent_id": int(agent_id),
                    "reputation_registry": contract,
                    "receipt_hash": receipt_bytes32,
                    "rating": int(rating),
                    "comment": str(comment),
                }
            except Exception as exc:  # pragma: no cover - network/write path
                last_error = exc

        raise ERC8004Error(f"Failed to submit reputation transaction: {last_error}")

    def post_validation(self, agent_id: int, receipt_hash: str, validation_result: bool) -> Dict[str, Any]:
        registries = self._discover_registry_addresses(agent_id=agent_id, wallet=self.default_wallet)
        contract = registries.validation_registry
        if not contract:
            raise ERC8004Error("Validation registry address unavailable")

        private_key = os.getenv("NEXUS_LEDGER_PRIVATE_KEY", "").strip()
        if not private_key:
            raise ERC8004Error("Set NEXUS_LEDGER_PRIVATE_KEY to submit on-chain validation")

        receipt_bytes32 = self._normalize_bytes32(receipt_hash)
        encoded_bool = self._encode_uint256(1 if validation_result else 0)
        payloads = [
            self._encode_call(
                "postValidation(uint256,bytes32,bool)",
                [self._encode_uint256(agent_id), self._encode_bytes32(receipt_bytes32), encoded_bool],
            ),
            self._encode_call(
                "recordValidation(uint256,bytes32,bool)",
                [self._encode_uint256(agent_id), self._encode_bytes32(receipt_bytes32), encoded_bool],
            ),
        ]

        last_error: Optional[Exception] = None
        for payload in payloads:
            try:
                tx_hash = self._send_transaction(contract, payload, private_key)
                return {
                    "tx_hash": tx_hash,
                    "agent_id": int(agent_id),
                    "validation_registry": contract,
                    "receipt_hash": receipt_bytes32,
                    "validation_result": bool(validation_result),
                }
            except Exception as exc:  # pragma: no cover - network/write path
                last_error = exc

        raise ERC8004Error(f"Failed to submit validation transaction: {last_error}")

    def _discover_registry_addresses(self, *, agent_id: int, wallet: str) -> RegistryAddresses:
        if self._registry_cache is not None:
            return self._registry_cache

        receipt = self._rpc("eth_getTransactionReceipt", [self.registration_tx_hash])
        if not isinstance(receipt, dict):
            raise ERC8004Error("Could not load registration transaction receipt from Base RPC")

        logs = receipt.get("logs", [])
        ordered_addresses: List[str] = []

        tx_to = receipt.get("to")
        if isinstance(tx_to, str) and tx_to:
            ordered_addresses.append(self._normalize_address(tx_to))

        for log in logs:
            address = self._normalize_address(log.get("address", ""))
            if address and address not in ordered_addresses:
                ordered_addresses.append(address)

        identity_registry = self._find_identity_registry(logs, agent_id, wallet)

        remaining = [addr for addr in ordered_addresses if addr and addr != identity_registry]
        reputation_registry = self._normalize_env_address(os.getenv("ERC8004_REPUTATION_REGISTRY"))
        validation_registry = self._normalize_env_address(os.getenv("ERC8004_VALIDATION_REGISTRY"))

        if not reputation_registry and remaining:
            reputation_registry = remaining[0]
        if not validation_registry and len(remaining) > 1:
            validation_registry = remaining[1]

        block_number = self._from_hex(receipt.get("blockNumber"))

        self._registry_cache = RegistryAddresses(
            identity_registry=identity_registry,
            reputation_registry=reputation_registry,
            validation_registry=validation_registry,
            registration_block=block_number,
            registration_tx_hash=self.registration_tx_hash,
        )
        return self._registry_cache

    def _find_identity_registry(self, logs: Any, agent_id: int, wallet: str) -> Optional[str]:
        target_topic = self._topic_uint256(agent_id)
        wallet_topic = self._topic_address(wallet)

        if isinstance(logs, list):
            for log in logs:
                topics = log.get("topics", [])
                if not isinstance(topics, list) or len(topics) < 4:
                    continue
                if str(topics[0]).lower() != TRANSFER_TOPIC:
                    continue
                if str(topics[2]).lower() != wallet_topic:
                    continue
                if str(topics[3]).lower() != target_topic:
                    continue
                return self._normalize_address(log.get("address", ""))

        return self._normalize_env_address(os.getenv("ERC8004_IDENTITY_REGISTRY"))

    def _read_reputation_summary(self, contract: str, agent_id: int) -> Dict[str, Any]:
        candidates = {
            "reputationOf(uint256)": "reputation_of",
            "getReputation(uint256)": "get_reputation",
            "getAgentReputation(uint256)": "get_agent_reputation",
            "scoreOf(uint256)": "score_of",
        }
        summary: Dict[str, Any] = {}
        for signature, label in candidates.items():
            try:
                raw = self._eth_call(contract, self._encode_call(signature, [self._encode_uint256(agent_id)]))
                summary[label] = self._decode_uint256(raw)
            except ERC8004Error:
                continue
        return summary

    def _eth_get_logs(self, *, address: str, topics: List[Optional[str]], from_block: str) -> List[Dict[str, Any]]:
        params = {
            "address": address,
            "fromBlock": from_block,
            "toBlock": "latest",
            "topics": topics,
        }
        result = self._rpc("eth_getLogs", [params])
        return result if isinstance(result, list) else []

    def _eth_call(self, to: str, data: str) -> str:
        result = self._rpc("eth_call", [{"to": to, "data": data}, "latest"])
        if not isinstance(result, str):
            raise ERC8004Error("Invalid eth_call response")
        if result == "0x":
            raise ERC8004Error("Empty eth_call response")
        return result

    def _rpc(self, method: str, params: List[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.rpc_url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except URLError as exc:
            raise ERC8004Error(f"Base RPC request failed: {exc}") from exc

        decoded = json.loads(raw)
        if "error" in decoded:
            raise ERC8004Error(f"Base RPC error for {method}: {decoded['error']}")
        return decoded.get("result")

    def _send_transaction(self, to: str, data: str, private_key: str) -> str:
        try:
            from web3 import Web3
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise ERC8004Error("web3 is required for write operations. Install with: pip install 'nexus-ledger[erc8004]'") from exc

        web3 = Web3(Web3.HTTPProvider(self.rpc_url))
        account = web3.eth.account.from_key(private_key)

        tx = {
            "chainId": 8453,
            "to": Web3.to_checksum_address(to),
            "from": account.address,
            "nonce": web3.eth.get_transaction_count(account.address),
            "value": 0,
            "data": data,
            "gasPrice": web3.eth.gas_price,
        }
        tx["gas"] = web3.eth.estimate_gas(tx)

        signed = account.sign_transaction(tx)
        tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    def _fetch_metadata(self, token_uri: str) -> Optional[Dict[str, Any]]:
        uri = str(token_uri).strip()
        if not uri:
            return None

        if uri.startswith("ipfs://"):
            uri = "https://ipfs.io/ipfs/" + uri[len("ipfs://") :]

        if not (uri.startswith("http://") or uri.startswith("https://")):
            return None

        req = request.Request(uri, headers={"Accept": "application/json"}, method="GET")
        try:
            with request.urlopen(req, timeout=10) as response:
                payload = response.read().decode("utf-8")
        except Exception:
            return None

        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    def _normalize_log(self, log: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "address": self._normalize_address(log.get("address", "")),
            "block_number": self._from_hex(log.get("blockNumber")),
            "tx_hash": log.get("transactionHash"),
            "log_index": self._from_hex(log.get("logIndex")),
            "topics": log.get("topics", []),
            "data": log.get("data"),
        }

    @staticmethod
    def _to_hex(value: int) -> str:
        return hex(int(value))

    @staticmethod
    def _from_hex(value: Any) -> Optional[int]:
        if isinstance(value, str) and value.startswith("0x"):
            return int(value, 16)
        return None

    @staticmethod
    def _normalize_address(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith("0x"):
            return "0x" + text[2:].lower()
        return "0x" + text.lower()

    @staticmethod
    def _normalize_env_address(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        addr = ERC8004._normalize_address(value)
        return addr if len(addr) == 42 else None

    @staticmethod
    def _normalize_bytes32(value: str) -> str:
        raw = str(value).strip().lower()
        if raw.startswith("0x"):
            raw = raw[2:]
        if len(raw) == 64:
            return "0x" + raw
        raise ValueError("receipt_hash must be 32-byte hex")

    @staticmethod
    def _function_selector(signature: str) -> str:
        if signature not in FUNCTION_SELECTORS:
            raise ValueError(f"Unsupported function signature: {signature}")
        return FUNCTION_SELECTORS[signature]

    @classmethod
    def _encode_call(cls, signature: str, args_encoded: List[str]) -> str:
        selector = cls._function_selector(signature)
        if not args_encoded:
            return "0x" + selector

        static_parts: List[str] = []
        dynamic_parts: List[str] = []
        offset = 32 * len(args_encoded)

        for item in args_encoded:
            if item.startswith("dynamic:"):
                encoded = item[len("dynamic:") :]
                static_parts.append(cls._encode_uint256_raw(offset))
                dynamic_parts.append(encoded)
                offset += len(encoded) // 2
            else:
                static_parts.append(item)

        return "0x" + selector + "".join(static_parts) + "".join(dynamic_parts)

    @staticmethod
    def _encode_uint256(value: int) -> str:
        return ERC8004._encode_uint256_raw(int(value))

    @staticmethod
    def _encode_uint256_raw(value: int) -> str:
        return hex(int(value) & ((1 << 256) - 1))[2:].rjust(64, "0")

    @staticmethod
    def _encode_bytes32(value: str) -> str:
        raw = value[2:] if value.startswith("0x") else value
        if len(raw) != 64:
            raise ValueError("bytes32 values must be 32-byte hex")
        return raw.lower()

    @staticmethod
    def _encode_string(value: str) -> str:
        raw = str(value).encode("utf-8")
        padded_len = ((len(raw) + 31) // 32) * 32
        padded = raw.ljust(padded_len, b"\x00").hex()
        encoded = ERC8004._encode_uint256_raw(len(raw)) + padded
        return "dynamic:" + encoded

    @staticmethod
    def _decode_uint256(value: str) -> int:
        raw = value[2:] if value.startswith("0x") else value
        if len(raw) < 64:
            raise ERC8004Error("Invalid uint256 response length")
        return int(raw[:64], 16)

    @staticmethod
    def _decode_address(value: str) -> str:
        raw = value[2:] if value.startswith("0x") else value
        if len(raw) < 64:
            raise ERC8004Error("Invalid address response length")
        return "0x" + raw[-40:].lower()

    @staticmethod
    def _decode_string(value: str) -> str:
        raw = value[2:] if value.startswith("0x") else value
        if len(raw) < 128:
            raise ERC8004Error("Invalid ABI string response length")

        offset = int(raw[:64], 16)
        start = offset * 2
        if len(raw) < start + 64:
            raise ERC8004Error("Invalid ABI string offset")

        length = int(raw[start : start + 64], 16)
        data_start = start + 64
        data_end = data_start + (length * 2)
        if len(raw) < data_end:
            raise ERC8004Error("Invalid ABI string data length")

        return bytes.fromhex(raw[data_start:data_end]).decode("utf-8")

    @staticmethod
    def _topic_uint256(value: int) -> str:
        return "0x" + ERC8004._encode_uint256_raw(value)

    @staticmethod
    def _topic_address(address: str) -> str:
        clean = address[2:] if address.startswith("0x") else address
        return "0x" + clean.lower().rjust(64, "0")


_default_client: Optional[ERC8004] = None


def _client() -> ERC8004:
    global _default_client
    if _default_client is None:
        _default_client = ERC8004()
    return _default_client


def get_agent_identity(agent_id: int) -> Dict[str, Any]:
    return _client().get_agent_identity(agent_id)


def post_reputation(agent_id: int, receipt_hash: str, rating: int, comment: str) -> Dict[str, Any]:
    return _client().post_reputation(agent_id, receipt_hash, rating, comment)


def get_reputation(agent_id: int) -> Dict[str, Any]:
    return _client().get_reputation(agent_id)


def post_validation(agent_id: int, receipt_hash: str, validation_result: bool) -> Dict[str, Any]:
    return _client().post_validation(agent_id, receipt_hash, validation_result)
