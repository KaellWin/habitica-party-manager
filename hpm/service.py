import json
import logging
import random
import threading
import time

from .companions import Conflict, HabitBank
from .config import Settings
from .habitica import APIError, Habitica
from .rewards import POLICY, REWARDS
from .rules import canonical, classify, digest
from .store import Store

log = logging.getLogger("hpm")


class Service:
    def __init__(self, settings: Settings, api: Habitica | None = None):
        self.cfg = settings
        self.db = Store(settings.database)
        self.db.acquire()
        self.api = api or Habitica(settings)
        self.guard = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.accounts = {a.name: a for a in settings.companions}
        with self.db.connect() as db:
            if not db.execute("SELECT 1 FROM settings WHERE key='mode'").fetchone():
                Store.set_in(db, "mode", settings.initial_mode)
                Store.set_in(db, "live_since", time.time())
            for account in settings.companions:
                binding_key = "binding_" + account.name
                binding = digest(
                    {
                        "user_id": account.user_id,
                        "task_id": account.task_id,
                        "alias": settings.technical_alias,
                        "policy": POLICY,
                    }
                )
                old = db.execute("SELECT * FROM companions WHERE name=?", (account.name,)).fetchone()
                if old and old["user_id"] != account.user_id:
                    db.execute("DELETE FROM reward_habits WHERE companion=?", (account.name,))
                    db.execute("DELETE FROM companions WHERE name=?", (account.name,))
                    db.execute(
                        "UPDATE operations SET status='cancelled', error='account_changed' "
                        "WHERE companion=? AND status IN ('queued','retry')",
                        (account.name,),
                    )
                    old = None
                if not old:
                    db.execute(
                        "INSERT INTO companions(name,user_id,enabled) VALUES(?,?,?)",
                        (account.name, account.user_id, account.enabled),
                    )
                elif Store.get_in(db, binding_key) != binding:
                    db.execute("UPDATE companions SET prepared=0,task_id=NULL WHERE name=?", (account.name,))
                    db.execute("DELETE FROM reward_habits WHERE companion=?", (account.name,))
                Store.set_in(db, binding_key, binding)

    def start(self, worker=True):
        if self.db.process_lock is None:
            self.db.acquire()
        self.stop.clear()
        if worker:
            self.thread = threading.Thread(target=self.run, name="hpm-dispatch", daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=self.cfg.request_timeout * 2 + 5)
        self.api.close()
        self.db.release()

    def run(self):
        while not self.stop.is_set():
            try:
                self.dispatch_once()
            except Exception:
                # Pas de repr(exception) : les clients HTTP peuvent y inclure des URL secrètes.
                log.error(canonical({"event": "worker_error", "action": "inspect_database"}))
            self.stop.wait(self.cfg.worker_interval)

    def companion(self, name):
        if name not in self.accounts:
            raise Conflict("Compagnon inconnu ou retiré de la configuration")
        return self.accounts[name]

    def enabled(self, name, row):
        return name in self.accounts and self.accounts[name].enabled and bool(row["enabled"])

    def ingest(self, payload):
        decision = classify(payload, self.cfg.master.user_id)
        now = time.time()
        with self.guard, self.db.connect() as db:
            existing = db.execute("SELECT id FROM events WHERE id=?", (decision.key,)).fetchone()
            if existing:
                db.execute("UPDATE events SET receipts=receipts+1 WHERE id=?", (decision.key,))
                return {"id": decision.key, "duplicate": True, "reason": "duplicate"}
            mode = Store.get_in(db, "mode")
            reason = decision.reason
            eligible = decision.eligible
            if eligible and decision.occurred_at > now + 300:
                eligible, reason = False, "future_score_time"
            cutoff_ms = int(Store.get_in(db, "live_since", now) * 1000) / 1000
            if eligible and mode == "live" and decision.occurred_at < cutoff_ms:
                eligible, reason = False, "before_live_activation"
            db.execute(
                "INSERT INTO events(id,received_at,occurred_at,mode,reason,snapshot) VALUES(?,?,?,?,?,?)",
                (decision.key, now, decision.occurred_at, mode, reason, canonical(decision.snapshot)),
            )
            if eligible and mode in {"dry-run", "live"}:
                for row in db.execute("SELECT * FROM companions").fetchall():
                    if not self.enabled(row["name"], row):
                        continue
                    status = "simulated" if mode == "dry-run" else "queued"
                    target = db.execute(
                        "SELECT task_id FROM reward_habits WHERE companion=? AND user_id=? AND reward_key=?",
                        (row["name"], row["user_id"], decision.reward_key),
                    ).fetchone()
                    reward = REWARDS[decision.reward_key]
                    db.execute(
                        """INSERT INTO operations(
                        event_id,companion,user_id,task_id,status,next_at,created_at,updated_at,
                        reward_key,source_type,source_priority) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            decision.key,
                            row["name"],
                            row["user_id"],
                            target["task_id"] if target else None,
                            status,
                            now,
                            now,
                            now,
                            reward.key,
                            reward.source_type,
                            reward.priority,
                        ),
                    )
            log.info(canonical({"event": "received", "id": decision.key, "mode": mode, "reason": reason}))
        return {"id": decision.key, "duplicate": False, "reason": reason}

    def set_mode(self, mode):
        with self.guard, self.db.connect() as db:
            if mode == "live":
                missing = [
                    r["name"]
                    for r in db.execute("SELECT * FROM companions")
                    if self.enabled(r["name"], r)
                    and (
                        not r["prepared"]
                        or db.execute(
                            "SELECT count(*) FROM reward_habits WHERE companion=? AND user_id=?",
                            (r["name"], r["user_id"]),
                        ).fetchone()[0]
                        != len(REWARDS)
                    )
                ]
                if missing:
                    raise Conflict("Auditer et préparer les compagnons : " + ", ".join(missing))
            previous = Store.get_in(db, "mode")
            if previous == mode:
                return
            Store.set_in(db, "mode", mode)
            Store.set_in(db, "live_since", time.time())
            db.execute(
                "UPDATE operations SET status='cancelled', error='mode_changed', updated_at=? "
                "WHERE status IN ('queued','retry')",
                (time.time(),),
            )
            Store.audit_in(db, "mode_changed", {"previous": previous, "mode": mode})

    def set_enabled(self, name, enabled):
        self.companion(name)
        with self.guard, self.db.connect() as db:
            db.execute("UPDATE companions SET enabled=? WHERE name=?", (enabled, name))
            if not enabled:
                db.execute(
                    "UPDATE operations SET status='cancelled', error='companion_disabled', updated_at=? "
                    "WHERE companion=? AND status IN ('queued','retry')",
                    (time.time(), name),
                )
            Store.audit_in(db, "companion_enabled", {"companion": name, "enabled": enabled})

    def audit(self, name):
        with self.guard:
            return HabitBank(self.cfg, self.api, self.db).audit(self.companion(name))

    def prepare(self, name, audit_id, create, acknowledge_conflicts):
        with self.guard:
            return HabitBank(self.cfg, self.api, self.db).prepare(
                self.companion(name), audit_id, create, acknowledge_conflicts
            )

    def finish(self, op_id, status, error=None, delay=0):
        now = time.time()
        with self.db.connect() as db:
            db.execute(
                "UPDATE operations SET status=?,error=?,next_at=?,updated_at=? WHERE id=?",
                (status, error, now + delay, now, op_id),
            )
        log.info(canonical({"event": "operation", "id": op_id, "status": status, "error": error}))

    def dispatch_once(self):
        # Une opération par itération : un compagnon en erreur n'empêche pas les autres de progresser.
        with self.guard:
            if self.db.setting("mode") != "live":
                return False
            with self.db.connect() as db:
                row = db.execute(
                    "SELECT * FROM operations WHERE status IN ('queued','retry') AND next_at<=? "
                    "ORDER BY next_at,id LIMIT 1",
                    (time.time(),),
                ).fetchone()
                if not row:
                    return False
                op = dict(row)
                recipient = db.execute("SELECT * FROM companions WHERE name=?", (op["companion"],)).fetchone()
                target = db.execute(
                    "SELECT task_id FROM reward_habits WHERE companion=? AND user_id=? AND reward_key=?",
                    (op["companion"], op["user_id"], op["reward_key"]),
                ).fetchone()
                safe = (
                    recipient
                    and self.enabled(op["companion"], recipient)
                    and recipient["prepared"]
                    and recipient["user_id"] == op["user_id"]
                    and target
                    and target["task_id"] == op["task_id"]
                    and op["reward_key"] in REWARDS
                )
                if not safe:
                    db.execute(
                        "UPDATE operations SET status='cancelled',error='recipient_changed' WHERE id=?",
                        (op["id"],),
                    )
                    return True
                db.execute("UPDATE operations SET attempts=attempts+1 WHERE id=?", (op["id"],))
            account = self.companion(op["companion"])
            sending = False
            try:
                task = self.api.task(account, op["task_id"])
                self.api.validate_reward(task, account, REWARDS[op["reward_key"]])
                if (task.get("id") or task.get("_id")) != op["task_id"]:
                    raise APIError("technical_task_mismatch")
                # Commit AVANT le POST : arrêt à cet endroit => résultat incertain après redémarrage.
                with self.db.connect() as db:
                    db.execute(
                        "UPDATE operations SET status='sending',updated_at=? WHERE id=?",
                        (time.time(), op["id"]),
                    )
                sending = True
                self.api.score_up(account, op["task_id"])
                self.finish(op["id"], "succeeded")
            except APIError as error:
                if error.uncertain and sending:
                    self.finish(op["id"], "uncertain", error.code)
                elif error.retryable and op["attempts"] + 1 < self.cfg.max_attempts:
                    delay = max(error.retry_after, min(300, 2 ** (op["attempts"] + 1)) + random.uniform(0, 1))
                    self.finish(op["id"], "retry", error.code, delay)
                else:
                    self.finish(op["id"], "failed", error.code)
            except Exception:
                self.finish(op["id"], "uncertain" if sending else "failed", "unexpected_error")
            return True

    def resolve(self, op_id, action, note, acknowledge_risk):
        with self.guard, self.db.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
            if not row or row["status"] not in {"uncertain", "failed"}:
                raise Conflict("Seules les opérations incertaines ou échouées peuvent être résolues")
            if action == "retry" and row["reward_key"] not in REWARDS:
                raise Conflict("Ancienne opération : confirmer ou abandonner après vérification, sans rejeu")
            if action == "retry" and (not acknowledge_risk or Store.get_in(db, "mode") != "live"):
                raise Conflict(
                    "La reprise exige le mode actif et l'acceptation explicite du risque de doublon"
                )
            status = {"confirm": "succeeded", "abandon": "cancelled", "retry": "queued"}[action]
            db.execute(
                "UPDATE operations SET status=?,error='operator_resolution',"
                "attempts=0,next_at=?,updated_at=? "
                "WHERE id=?",
                (status, time.time(), time.time(), op_id),
            )
            Store.audit_in(
                db,
                "operation_resolved",
                {"id": op_id, "action": action, "note": note, "risk_acknowledged": acknowledge_risk},
            )

    def status(self):
        companions = self.db.rows("SELECT * FROM companions ORDER BY name")
        for row in companions:
            row["enabled"] = self.enabled(row["name"], row)
            row["configured"] = row["name"] in self.accounts
            row["audit"] = json.loads(row.pop("audit_json") or "null")
            row["habits"] = self.db.rows(
                "SELECT reward_key,task_id FROM reward_habits WHERE companion=? ORDER BY reward_key",
                (row["name"],),
            )
        return {
            "mode": self.db.setting("mode"),
            "reward_policy": POLICY,
            "reward_catalog": [
                {
                    "key": r.key,
                    "source_type": r.source_type,
                    "priority": r.priority,
                    "type_label": r.type_label,
                    "difficulty_label": r.difficulty_label,
                }
                for r in REWARDS.values()
            ],
            "master": self.cfg.master.name,
            "master_user_id": self.cfg.master.user_id,
            "base_url": self.cfg.base_url,
            "companions": companions,
            "events": self.db.rows(
                "SELECT count(*) AS unique_events,coalesce(sum(receipts),0) AS receipts FROM events"
            )[0],
            "operations": self.db.rows("SELECT status,count(*) AS count FROM operations GROUP BY status"),
            "last_operations": self.db.rows("SELECT * FROM operations ORDER BY id DESC LIMIT 50"),
        }
