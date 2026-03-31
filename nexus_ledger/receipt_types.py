"""Typed task receipt vocabulary for Nexus Ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReceiptTypeBase:
    task_id: str

    def validate(self) -> None:
        if not str(self.task_id).strip():
            raise ValueError("task_id is required")

    def as_data(self) -> Dict[str, Any]:
        self.validate()
        return {"task_id": self.task_id}


@dataclass
class TaskRequest(ReceiptTypeBase):
    description: str
    budget: float
    deadline: str = field(default_factory=_utc_now)

    def validate(self) -> None:
        super().validate()
        if not str(self.description).strip():
            raise ValueError("description is required")
        if float(self.budget) < 0:
            raise ValueError("budget must be non-negative")
        if not str(self.deadline).strip():
            raise ValueError("deadline is required")

    def as_data(self) -> Dict[str, Any]:
        self.validate()
        return {
            "task_id": self.task_id,
            "description": str(self.description),
            "budget": float(self.budget),
            "deadline": str(self.deadline),
        }


@dataclass
class TaskAccepted(ReceiptTypeBase):
    estimated_delivery: str

    def validate(self) -> None:
        super().validate()
        if not str(self.estimated_delivery).strip():
            raise ValueError("estimated_delivery is required")

    def as_data(self) -> Dict[str, Any]:
        self.validate()
        return {
            "task_id": self.task_id,
            "estimated_delivery": str(self.estimated_delivery),
        }


@dataclass
class TaskDelivered(ReceiptTypeBase):
    artifact_hash: str
    artifact_url: Optional[str] = None

    def validate(self) -> None:
        super().validate()
        if not str(self.artifact_hash).strip():
            raise ValueError("artifact_hash is required")

    def as_data(self) -> Dict[str, Any]:
        self.validate()
        payload: Dict[str, Any] = {
            "task_id": self.task_id,
            "artifact_hash": str(self.artifact_hash),
        }
        if self.artifact_url:
            payload["artifact_url"] = str(self.artifact_url)
        return payload


@dataclass
class TaskConfirmed(ReceiptTypeBase):
    rating: int
    feedback: str

    def validate(self) -> None:
        super().validate()
        rating = int(self.rating)
        if rating < 1 or rating > 5:
            raise ValueError("rating must be between 1 and 5")
        if not str(self.feedback).strip():
            raise ValueError("feedback is required")

    def as_data(self) -> Dict[str, Any]:
        self.validate()
        return {
            "task_id": self.task_id,
            "rating": int(self.rating),
            "feedback": str(self.feedback),
        }


@dataclass
class TaskDisputed(ReceiptTypeBase):
    reason: str

    def validate(self) -> None:
        super().validate()
        if not str(self.reason).strip():
            raise ValueError("reason is required")

    def as_data(self) -> Dict[str, Any]:
        self.validate()
        return {
            "task_id": self.task_id,
            "reason": str(self.reason),
        }


def new_task_id() -> str:
    return f"task-{uuid4().hex[:12]}"


@dataclass
class DependencyInstall:
    """Receipt type for a dependency installation with hash verification.

    Used by the Supply Chain Trust Module (Nexus Ledger v5.0) to record
    cryptographic proof of what was installed, when, and whether the
    downloaded artifact matched the registry-published hash.
    """

    package: str
    version: str
    registry: str
    source_hash: str
    expected_hash: str
    hash_match: bool
    install_command: str

    def validate(self) -> None:
        if not str(self.package).strip():
            raise ValueError("package is required")
        if not str(self.version).strip():
            raise ValueError("version is required")
        if not str(self.registry).strip():
            raise ValueError("registry is required")
        if not str(self.source_hash).strip():
            raise ValueError("source_hash is required")
        if not str(self.expected_hash).strip():
            raise ValueError("expected_hash is required")
        if not isinstance(self.hash_match, bool):
            raise ValueError("hash_match must be a bool")
        if not str(self.install_command).strip():
            raise ValueError("install_command is required")

    def as_data(self) -> Dict[str, Any]:
        self.validate()
        return {
            "package": str(self.package),
            "version": str(self.version),
            "registry": str(self.registry),
            "source_hash": str(self.source_hash),
            "expected_hash": str(self.expected_hash),
            "hash_match": bool(self.hash_match),
            "install_command": str(self.install_command),
        }

