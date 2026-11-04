import fcntl
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .rules import canonical


class Store:
    """Transactions locales courtes ; le verrou de processus impose un seul worker."""

    def __init__(self, path: str):
        self.path = path
        self.process_lock = None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2}:
                raise RuntimeError("Version SQLite non prise en charge ; restaurer une version compatible")
        if version == 2:
            return
        # Une ancienne instance ne doit pas distribuer des opérations pendant la migration.
        with open(self.path + ".lock", "a+") as migration_lock:
            try:
                fcntl.flock(migration_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("Arrêter l'ancienne instance HPM avant de migrer la base") from None
            with self.connect() as db:
                self.migrate(db)

    def migrate(self, db):
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 2:
            return
        if version == 0:
            schema = """
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS companions(
                    name TEXT PRIMARY KEY, user_id TEXT NOT NULL, enabled INTEGER NOT NULL,
                    task_id TEXT, audit_json TEXT, prepared INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events(
                    id TEXT PRIMARY KEY, received_at REAL NOT NULL, occurred_at REAL,
                    mode TEXT NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL,
                    receipts INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS operations(
                    id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
                    companion TEXT NOT NULL, user_id TEXT NOT NULL, task_id TEXT,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_at REAL NOT NULL, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(event_id, companion)
                );
                CREATE INDEX IF NOT EXISTS queue_due ON operations(status, next_at);
                CREATE TABLE IF NOT EXISTS audit_log(
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
                );
            """
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
        db.execute("""CREATE TABLE reward_habits(
            companion TEXT NOT NULL, user_id TEXT NOT NULL, reward_key TEXT NOT NULL,
            task_id TEXT NOT NULL, PRIMARY KEY(companion,reward_key), UNIQUE(user_id,task_id)
        )""")
        db.execute("ALTER TABLE operations ADD COLUMN reward_key TEXT")
        db.execute("ALTER TABLE operations ADD COLUMN source_type TEXT")
        db.execute("ALTER TABLE operations ADD COLUMN source_priority REAL")
        if version == 1:
            self.set_in(db, "mode", "observe")
            self.set_in(db, "live_since", time.time())
            db.execute("UPDATE companions SET prepared=0,audit_json=NULL")
            db.execute(
                "UPDATE operations SET status='cancelled',error='reward_policy_upgrade',updated_at=? "
                "WHERE status IN ('queued','retry')",
                (time.time(),),
            )
            self.audit_in(db, "schema_upgraded", {"from": 1, "to": 2, "mode": "observe"})
        db.execute("PRAGMA user_version=2")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def acquire(self):
        self.process_lock = open(self.path + ".lock", "a+")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.process_lock.close()
            self.process_lock = None
            raise RuntimeError("Une seule instance HPM est autorisée pour cette base") from None
        with self.connect() as db:
            db.execute(
                "UPDATE operations SET status='uncertain', error='process_interrupted', updated_at=? "
                "WHERE status='sending'",
                (time.time(),),
            )

    def release(self):
        if self.process_lock:
            self.process_lock.close()
            self.process_lock = None

    def rows(self, sql, params=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def setting(self, key, default=None):
        with self.connect() as db:
            return self.get_in(db, key, default)

    @staticmethod
    def get_in(db, key, default=None):
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    @staticmethod
    def set_in(db, key, value):
        db.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, canonical(value)),
        )

    @staticmethod
    def audit_in(db, action, detail):
        db.execute(
            "INSERT INTO audit_log(at,action,detail) VALUES(?,?,?)", (time.time(), action, canonical(detail))
        )
