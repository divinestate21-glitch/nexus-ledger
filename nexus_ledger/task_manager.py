from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .receipt_types import TaskAccepted, TaskConfirmed, TaskDelivered, TaskDisputed, TaskRequest, new_task_id
from .transport import public_key_to_did


class TaskManager:
    def __init__(
        self,
        *,
        ledger: Any,
        relay: Any,
        identity: Any,
    ) -> None:
        self._ledger = ledger
        self._relay = relay
        self._identity = identity

    def get_task_chain(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._ledger.get_task_chain(task_id)
        if not rows:
            return []

        parsed: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                data = json.loads(str(item.get("data_json", "{}")))
            except json.JSONDecodeError:
                data = {}
            item["data"] = data
            parsed.append(item)

        by_proof = {str(item.get("proof_hash", "")): item for item in parsed}
        roots = [
            item
            for item in parsed
            if not str(item.get("parent_receipt_hash", ""))
            or str(item.get("parent_receipt_hash", "")) not in by_proof
        ]

        ordered: List[Dict[str, Any]] = []
        current = roots[0] if roots else parsed[0]
        remaining = {id(item): item for item in parsed}
        while current and id(current) in remaining:
            ordered.append(current)
            remaining.pop(id(current), None)
            next_item = None
            for candidate in list(remaining.values()):
                if str(candidate.get("parent_receipt_hash", "")) == str(current.get("proof_hash", "")):
                    next_item = candidate
                    break
            current = next_item

        ordered.extend(remaining.values())
        return ordered

    def _latest_task_receipt_hash(self, task_id: str) -> str:
        chain = self.get_task_chain(task_id)
        if not chain:
            return ""
        return str(chain[-1].get("proof_hash", ""))

    def _resolve_task_counterparty_pubkey(self, task_id: str) -> Optional[str]:
        chain = self.get_task_chain(task_id)
        if not chain:
            return None
        latest = chain[-1]
        a = str(latest.get("agent_a_pubkey", ""))
        b = str(latest.get("agent_b_pubkey", ""))
        if a == self._identity.public_key:
            return b
        if b == self._identity.public_key:
            return a
        return b or a or None

    def request_task(
        self,
        to: str,
        *,
        description: str,
        budget: float,
        deadline: Optional[str] = None,
        task_id: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        tid = str(task_id or new_task_id())
        typed = TaskRequest(
            task_id=tid,
            description=description,
            budget=budget,
            deadline=deadline or datetime.now(timezone.utc).isoformat(),
        )
        return self._relay.send("TaskRequest", typed.as_data(), to=to, encrypted=encrypted)

    def accept_task(self, to: str, *, task_id: str, estimated_delivery: str, encrypted: bool = False) -> Dict[str, Any]:
        typed = TaskAccepted(task_id=task_id, estimated_delivery=estimated_delivery)
        parent = self._latest_task_receipt_hash(task_id)
        return self._relay.send("TaskAccepted", typed.as_data(), to=to, encrypted=encrypted, parent_receipt_hash=parent)

    def deliver_task(
        self,
        task_id: str,
        *,
        artifact_hash: str,
        artifact_url: Optional[str] = None,
        to: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        typed = TaskDelivered(task_id=task_id, artifact_hash=artifact_hash, artifact_url=artifact_url)
        counterparty = to
        if not counterparty:
            pubkey = self._resolve_task_counterparty_pubkey(task_id)
            if not pubkey:
                raise ValueError("Could not infer task counterparty; provide 'to'")
            counterparty = public_key_to_did(pubkey)
        parent = self._latest_task_receipt_hash(task_id)
        return self._relay.send("TaskDelivered", typed.as_data(), to=counterparty, encrypted=encrypted, parent_receipt_hash=parent)

    def confirm_task(
        self,
        task_id: str,
        *,
        rating: int,
        feedback: str,
        to: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        typed = TaskConfirmed(task_id=task_id, rating=rating, feedback=feedback)
        counterparty = to
        if not counterparty:
            pubkey = self._resolve_task_counterparty_pubkey(task_id)
            if not pubkey:
                raise ValueError("Could not infer task counterparty; provide 'to'")
            counterparty = public_key_to_did(pubkey)
        parent = self._latest_task_receipt_hash(task_id)
        return self._relay.send("TaskConfirmed", typed.as_data(), to=counterparty, encrypted=encrypted, parent_receipt_hash=parent)

    def dispute_task(
        self,
        task_id: str,
        *,
        reason: str,
        to: Optional[str] = None,
        encrypted: bool = False,
    ) -> Dict[str, Any]:
        typed = TaskDisputed(task_id=task_id, reason=reason)
        counterparty = to
        if not counterparty:
            pubkey = self._resolve_task_counterparty_pubkey(task_id)
            if not pubkey:
                raise ValueError("Could not infer task counterparty; provide 'to'")
            counterparty = public_key_to_did(pubkey)
        parent = self._latest_task_receipt_hash(task_id)
        return self._relay.send("TaskDisputed", typed.as_data(), to=counterparty, encrypted=encrypted, parent_receipt_hash=parent)
