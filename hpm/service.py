import json
import logging
import random
import threading
import time
from uuid import UUID

from .config import Settings
from .habitica import APIError, Habitica
from .rules import canonical, classify, digest
from .store import Store

log = logging.getLogger("hpm")


class Conflict(Exception):
    pass


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
                    }
                )
                old = db.execute("SELECT * FROM companions WHERE name=?", (account.name,)).fetchone()
                if old and old["user_id"] != account.user_id:
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
                    db.execute(
                        """INSERT INTO operations(
                        event_id,companion,user_id,task_id,status,next_at,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?)""",
                        (decision.key, row["name"], row["user_id"], row["task_id"], status, now, now, now),
                    )
            log.info(canonical({"event": "received", "id": decision.key, "mode": mode, "reason": reason}))
        return {"id": decision.key, "duplicate": False, "reason": reason}

    def set_mode(self, mode):
        with self.guard, self.db.connect() as db:
            if mode == "live":
                missing = [
                    r["name"]
                    for r in db.execute("SELECT * FROM companions")
                    if self.enabled(r["name"], r) and not r["prepared"]
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
        account = self.companion(name)
        user = self.api.request(account, "GET", "user")
        if not isinstance(user, dict) or user.get("_id", user.get("id")) != account.user_id:
            raise APIError("account_identity_mismatch")
        tasks = self.api.tasks(account)
        candidates = [
            t
            for t in tasks
            if (
                (t.get("id", t.get("_id")) == account.task_id)
                if account.task_id
                else t.get("alias") == self.cfg.technical_alias
            )
        ]
        if len(candidates) > 1:
            raise Conflict("Plusieurs habitudes techniques candidates ; sélectionner task_id")
        selected = candidates[0] if candidates else None
        if selected:
            self.api.validate_habit(selected, account)
        conflicts = [
            {
                "id": t.get("id", t.get("_id")),
                "type": t.get("type"),
                "text": t.get("text", ""),
                "warning": "penalites_cron_possibles"
                if t.get("type") == "daily"
                else "automatisation_externe_ou_doublon_a_verifier",
            }
            for t in tasks
            if t is not selected and t.get("type") in {"daily", "habit"}
        ]
        # Empreinte indépendante des scores : un audit reste valable si une tâche est simplement scorée.
        audit_state = {
            "user_id": account.user_id,
            "conflicts": conflicts,
            "selected": selected.get("id", selected.get("_id")) if selected else None,
        }
        report = {
            "companion": name,
            "connected": True,
            **audit_state,
            "audit_id": digest(audit_state),
            "at": time.time(),
        }
        with self.db.connect() as db:
            db.execute("UPDATE companions SET audit_json=? WHERE name=?", (canonical(report), name))
        return report

    def prepare(self, name, audit_id, create, acknowledge_conflicts):
        with self.guard:
            if self.db.setting("mode") == "dry-run":
                raise Conflict(
                    "La simulation interdit toute création ; utiliser le mode observation pour préparer"
                )
            account = self.companion(name)
            previous = self.db.rows("SELECT audit_json FROM companions WHERE name=?", (name,))[0]
            if not previous["audit_json"] or json.loads(previous["audit_json"])["audit_id"] != audit_id:
                raise Conflict("Lire le rapport d'audit avant de préparer")
            report = self.audit(name)
            if report["audit_id"] != audit_id:
                raise Conflict("Les tâches ont changé ; consulter le nouvel audit")
            if report["conflicts"] and not acknowledge_conflicts:
                raise Conflict(
                    "Vérifier les tâches et automatisations existantes puis acquitter les conflits"
                )
            if report["selected"]:
                task_id = report["selected"]
            elif create and not account.task_id:
                # Si la réponse se perd : relecture par alias au prochain audit, jamais de création aveugle.
                task = self.api.create_habit(account)
                task_id = task.get("id", task.get("_id"))
            else:
                raise Conflict("Habitude absente ; autoriser sa création ou corriger task_id")
            try:
                task_id = str(UUID(task_id))
            except (ValueError, TypeError):
                raise APIError("invalid_task_id") from None
            with self.db.connect() as db:
                db.execute("UPDATE companions SET task_id=?,prepared=1 WHERE name=?", (task_id, name))
                Store.audit_in(
                    db,
                    "companion_prepared",
                    {"companion": name, "task_id": task_id, "conflicts_acknowledged": acknowledge_conflicts},
                )
            return {"companion": name, "task_id": task_id, "prepared": True}

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
                safe = (
                    recipient
                    and self.enabled(op["companion"], recipient)
                    and recipient["prepared"]
                    and recipient["user_id"] == op["user_id"]
                    and recipient["task_id"] == op["task_id"]
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
                self.api.validate_habit(task, account)
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
        return {
            "mode": self.db.setting("mode"),
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
