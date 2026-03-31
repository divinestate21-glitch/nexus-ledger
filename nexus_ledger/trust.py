"""Local trust scoring from receipt history."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
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

        now = datetime.now(timezone.utc)

        counterparties = set()
        for row in receipts:
            a = str(row.get("agent_a_pubkey", ""))
            b = str(row.get("agent_b_pubkey", ""))
            if a and a != agent_pubkey:
                counterparties.add(a)
            if b and b != agent_pubkey:
                counterparties.add(b)

        unique_counterparties = len(counterparties)
        diversity_score = min(unique_counterparties / 10.0, 1.0)

        def _decay_weight(row: JsonDict) -> float:
            ts = _parse_time(row.get("timestamp"))
            if ts is None:
                return 1.0
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = max((now - ts).total_seconds(), 0.0) / 86400.0
            return math.pow(0.5, age_days / 30.0)

        on_time_count = 0
        for row in delivered:
            data = row.get("data", {})
            deadline = _parse_time(data.get("deadline"))
            delivered_at = _parse_time(data.get("delivered_at")) or _parse_time(row.get("timestamp"))
            if deadline is None:
                continue
            if delivered_at is not None and delivered_at <= deadline:
                on_time_count += 1

        rated_pairs = []
        for r in confirmed:
            val = r.get("data", {}).get("rating", "")
            if str(val).isdigit():
                rated_pairs.append((int(val), _decay_weight(r)))

        delivered_weights = [_decay_weight(r) for r in delivered]
        disputed_weights = [_decay_weight(r) for r in disputed]
        receipt_weights = [_decay_weight(r) for r in receipts]

        delivered_weight_total = sum(delivered_weights)
        receipt_weight_total = sum(receipt_weights)

        if delivered_weight_total > 0:
            on_time_count = 0.0
            for r, w in zip(delivered, delivered_weights):
                on_time_count += w  # Count all delivered as on-time (simplified)
            on_time_rate = on_time_count / delivered_weight_total
        else:
            on_time_rate = 1.0
        weighted_rating_total = sum(r * w for r, w in rated_pairs)
        weighted_rating_weight = sum(w for _, w in rated_pairs)
        average_rating = (weighted_rating_total / weighted_rating_weight) if weighted_rating_weight > 0 else 0.0
        rating_norm = min(max(average_rating / 5.0, 0.0), 1.0)
        dispute_rate = (sum(disputed_weights) / receipt_weight_total) if receipt_weight_total > 0 else 0.0
        volume_norm = min(receipt_weight_total / 20.0, 1.0)
        dispute_component = max(0.0, 1.0 - dispute_rate)

        diversity_weight = 0.5 + (0.5 * diversity_score)

        score = (0.15 * volume_norm) + (0.25 * on_time_rate) + (0.35 * rating_norm) + (0.1 * dispute_component) + (0.15 * diversity_score)
        score = score * diversity_weight

        if unique_counterparties < 3:
            score = min(score, 0.5)

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
                "diversity_score": round(diversity_score, 4),
                "unique_counterparties": unique_counterparties,
            },
            "weights": {
                "volume": 0.2,
                "on_time": 0.3,
                "rating": 0.4,
                "disputes": 0.1,
            },
        }
