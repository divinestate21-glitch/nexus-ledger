"""Local trust scoring from receipt history."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List


JsonDict = Dict[str, Any]


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class TrustScorer:
    def build_report(self, agent_pubkey: str, rows: List[JsonDict]) -> JsonDict:
        receipts = []
        for row in rows:
            if str(row.get("agent_a_pubkey")) != agent_pubkey and str(row.get("agent_b_pubkey")) != agent_pubkey:
                continue
            data = row.get("data_json")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = {}
            receipts.append({**row, "data": data if isinstance(data, dict) else {}})

        total = len(receipts)
        delivered = [r for r in receipts if str(r.get("event_type")) == "TaskDelivered"]
        confirmed = [r for r in receipts if str(r.get("event_type")) == "TaskConfirmed"]
        disputed = [r for r in receipts if str(r.get("event_type")) == "TaskDisputed"]

        on_time_count = 0
        for row in delivered:
            data = row.get("data", {})
            deadline = _parse_time(data.get("deadline"))
            delivered_at = _parse_time(data.get("delivered_at")) or _parse_time(row.get("timestamp"))
            if deadline is None:
                continue
            if delivered_at is not None and delivered_at <= deadline:
                on_time_count += 1

        rated = [int(r.get("data", {}).get("rating")) for r in confirmed if str(r.get("data", {}).get("rating", "")).isdigit()]

        on_time_rate = (on_time_count / len(delivered)) if delivered else 1.0
        average_rating = (sum(rated) / len(rated)) if rated else 0.0
        rating_norm = min(max(average_rating / 5.0, 0.0), 1.0)
        dispute_rate = (len(disputed) / total) if total else 0.0
        volume_norm = min(total / 20.0, 1.0)
        dispute_component = max(0.0, 1.0 - dispute_rate)

        score = (0.2 * volume_norm) + (0.3 * on_time_rate) + (0.4 * rating_norm) + (0.1 * dispute_component)
        score = min(max(score, 0.0), 1.0)

        return {
            "agent_pubkey": agent_pubkey,
            "score": round(score, 4),
            "factors": {
                "total_receipts": total,
                "delivered_receipts": len(delivered),
                "confirmed_receipts": len(confirmed),
                "disputed_receipts": len(disputed),
                "on_time_delivery_rate": round(on_time_rate, 4),
                "average_rating": round(average_rating, 4),
                "dispute_rate": round(dispute_rate, 4),
                "volume_normalized": round(volume_norm, 4),
            },
            "weights": {
                "volume": 0.2,
                "on_time": 0.3,
                "rating": 0.4,
                "disputes": 0.1,
            },
        }
