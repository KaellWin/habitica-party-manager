"""Adaptateur du payload vérifié dans awinterstein/habitica v5.50.6.

Aucun eventId/timestamp de livraison n'est supposé. Empreinte de l'état de score
observé ; les champs inconnus et les informations privées ne sont pas conservés.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime

from .rewards import reward_for


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite_number(value) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Decision:
    key: str
    eligible: bool
    reason: str
    snapshot: dict
    occurred_at: float | None = None
    reward_key: str | None = None


def classify(payload: dict, master_id: str) -> Decision:
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    history = task.get("history")
    last = history[-1] if isinstance(history, list) and history and isinstance(history[-1], dict) else {}
    snapshot = {
        "webhookType": payload.get("webhookType"),
        "type": payload.get("type"),
        "direction": payload.get("direction"),
        "delta": payload.get("delta"),
        "user_id": user.get("_id"),
        "task": {
            k: task[k]
            for k in ("_id", "id", "type", "value", "counterUp", "counterDown", "completed", "dateCompleted")
            if k in task
        },
        "history_last": {
            k: last[k] for k in ("date", "value", "scoredUp", "scoredDown", "completed") if k in last
        },
    }
    # Le titre ne participe pas à la déduplication et n'est pas nécessaire au journal.
    key = digest(snapshot)
    # Garder exactement l'identité v0.1 pour reconnaître aussi les livraisons anciennes.
    # La difficulté est une donnée de routage figée, pas un nouvel identifiant d'événement.
    priority = task.get("priority")
    snapshot["task"]["priority"] = priority if finite_number(priority) else None

    def result(reason, eligible=False, occurred=None):
        return Decision(key, eligible, reason, snapshot, occurred)

    if user.get("_id") != master_id:
        return result("foreign_account")
    if payload.get("webhookType") != "taskActivity" or payload.get("type") != "scored":
        return result("not_scored")
    if payload.get("direction") != "up":
        return result("not_positive")
    kind = task.get("type")
    if kind not in ("habit", "daily", "todo"):
        return result("unsupported_task")
    delta = payload.get("delta")
    if not finite_number(delta) or delta <= 0:
        return result("no_positive_delta")
    reward = reward_for(kind, priority) if finite_number(priority) else None
    if reward is None:
        return result("invalid_source_priority")
    if not isinstance(task.get("_id") or task.get("id"), str):
        return result("missing_task_id")
    if not finite_number(task.get("value")):
        return result("missing_score_state")
    if kind in {"daily", "todo"} and task.get("completed") is not True:
        return result("not_completed")
    occurred = None
    if kind in {"habit", "daily"}:
        if not finite_number(last.get("date")) or last["date"] <= 0:
            return result("missing_score_time")
        occurred = last["date"] / 1000
        if kind == "habit" and not finite_number(last.get("scoredUp")):
            return result("missing_habit_counter")
    else:
        try:
            date = datetime.fromisoformat(task["dateCompleted"].replace("Z", "+00:00"))
            if date.tzinfo is None:
                return result("missing_score_time")
            occurred = date.timestamp()
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            return result("missing_score_time")
    return Decision(key, True, "positive_score", snapshot, occurred, reward.key)
