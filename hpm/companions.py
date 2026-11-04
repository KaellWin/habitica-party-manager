"""Audit et préparation explicite des douze destinations de chaque compagnon."""

import json
import time
from uuid import UUID

from .habitica import APIError
from .rewards import POLICY, REWARDS
from .rules import canonical, digest
from .store import Store


class Conflict(Exception):
    pass


class HabitBank:
    def __init__(self, cfg, api, db):
        self.cfg, self.api, self.db = cfg, api, db

    def audit(self, account):
        user = self.api.request(account, "GET", "user")
        if not isinstance(user, dict) or user.get("_id", user.get("id")) != account.user_id:
            raise APIError("account_identity_mismatch")
        tasks = self.api.tasks(account)
        habits, selected_ids = [], set()
        for reward in REWARDS.values():
            candidates = [t for t in tasks if t.get("alias") == reward.alias(self.cfg)]
            if len(candidates) > 1:
                raise Conflict("Alias technique dupliqué : " + reward.alias(self.cfg))
            task_id = None
            if candidates:
                self.api.validate_reward(candidates[0], account, reward)
                task_id = candidates[0].get("id") or candidates[0].get("_id")
                try:
                    task_id = str(UUID(task_id))
                except (ValueError, TypeError):
                    raise APIError("invalid_task_id") from None
                selected_ids.add(task_id)
            habits.append(
                {
                    "reward_key": reward.key,
                    "source_type": reward.source_type,
                    "source_priority": reward.priority,
                    "type_label": reward.type_label,
                    "difficulty_label": reward.difficulty_label,
                    "alias": reward.alias(self.cfg),
                    "task_id": task_id,
                }
            )
        legacy = [
            t
            for t in tasks
            if t.get("alias") == self.cfg.technical_alias
            or (account.task_id and (t.get("id") or t.get("_id")) == account.task_id)
        ]
        excluded = selected_ids | {t.get("id") or t.get("_id") for t in legacy}
        conflicts = sorted(
            [
                {
                    "id": t.get("id") or t.get("_id"),
                    "type": t.get("type"),
                    "text": t.get("text", ""),
                    "warning": "penalites_cron_possibles"
                    if t.get("type") == "daily"
                    else "automatisation_externe_ou_doublon_a_verifier",
                }
                for t in tasks
                if (t.get("id") or t.get("_id")) not in excluded and t.get("type") in {"daily", "habit"}
            ],
            key=lambda item: str(item["id"]),
        )
        state = {
            "policy": POLICY,
            "user_id": account.user_id,
            "conflicts": conflicts,
            "habits": habits,
            "legacy_habits": sorted(
                [{"id": t.get("id") or t.get("_id"), "text": t.get("text", "")} for t in legacy],
                key=lambda item: str(item["id"]),
            ),
        }
        report = {
            "companion": account.name,
            "connected": True,
            **state,
            "missing_habits": sum(h["task_id"] is None for h in habits),
            "audit_id": digest(state),
            "at": time.time(),
        }
        with self.db.connect() as db:
            db.execute("UPDATE companions SET audit_json=? WHERE name=?", (canonical(report), account.name))
        return report

    def prepare(self, account, audit_id, create, acknowledge_conflicts):
        if self.db.setting("mode") not in {"observe", "off"}:
            raise Conflict(
                "Préparer les habitudes en mode observation ou arrêté ; aucune création en simulation"
            )
        previous = self.db.rows("SELECT audit_json FROM companions WHERE name=?", (account.name,))[0]
        if not previous["audit_json"] or json.loads(previous["audit_json"])["audit_id"] != audit_id:
            raise Conflict("Lire le rapport d'audit avant de préparer")
        report = self.audit(account)
        if report["audit_id"] != audit_id:
            raise Conflict("Les tâches ont changé ; consulter le nouvel audit")
        if report["conflicts"] and not acknowledge_conflicts:
            raise Conflict("Vérifier les tâches et automatisations existantes puis acquitter les conflits")
        if report["missing_habits"] and not create:
            raise Conflict("Habitudes manquantes ; autoriser leur création après audit")
        with self.db.connect() as db:
            db.execute("UPDATE companions SET prepared=0 WHERE name=?", (account.name,))
        targets = []
        for habit in report["habits"]:
            task_id = habit["task_id"]
            if task_id is None:
                # Une création ambiguë reste retrouvable par alias au prochain audit ; pas de retry aveugle.
                task = self.api.create_habit(account, REWARDS[habit["reward_key"]])
                try:
                    task_id = str(UUID(task.get("id") or task.get("_id")))
                except (ValueError, TypeError):
                    raise APIError("invalid_task_id") from None
            targets.append({**habit, "task_id": task_id})
        if len({h["task_id"] for h in targets}) != len(REWARDS):
            raise APIError("duplicate_technical_task_id")
        with self.db.connect() as db:
            db.execute("DELETE FROM reward_habits WHERE companion=?", (account.name,))
            db.executemany(
                "INSERT INTO reward_habits(companion,user_id,reward_key,task_id) VALUES(?,?,?,?)",
                [(account.name, account.user_id, h["reward_key"], h["task_id"]) for h in targets],
            )
            db.execute("UPDATE companions SET prepared=1 WHERE name=?", (account.name,))
            Store.audit_in(
                db,
                "companion_prepared",
                {
                    "companion": account.name,
                    "policy": POLICY,
                    "habits": targets,
                    "conflicts_acknowledged": acknowledge_conflicts,
                },
            )
        return {"companion": account.name, "prepared": True, "habits": targets}
