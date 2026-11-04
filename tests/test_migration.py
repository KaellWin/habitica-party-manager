import copy
import fcntl
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from hpm.app import create_app
from hpm.cli import backup
from hpm.habitica import Habitica
from hpm.rules import canonical, classify, digest
from hpm.simulator import IDS, scored_payload
from hpm.store import Store

from .conftest import drain, prepare_all


def old_database(cfg):
    payload = scored_payload(timestamp=time.time() - 60)
    decision = classify(payload, cfg.master.user_id)
    snapshot = copy.deepcopy(decision.snapshot)
    del snapshot["task"]["priority"]
    assert digest(snapshot) == decision.key  # Identité historique, indépendante de la nouvelle politique.
    now = time.time()
    with sqlite3.connect(cfg.database) as db:
        db.executescript((Path(__file__).parent / "fixtures/v1.sql").read_text())
        db.execute("INSERT INTO settings VALUES('mode','\"live\"')")
        db.execute("INSERT INTO settings VALUES('live_since',?)", (json.dumps(now - 300),))
        for account in cfg.companions:
            db.execute(
                "INSERT INTO companions(name,user_id,enabled,task_id,prepared) VALUES(?,?,?,?,1)",
                (account.name, account.user_id, 1, str(uuid4())),
            )
        for i, status in enumerate(["queued", "retry", "sending", "succeeded", "uncertain", "failed"]):
            key = decision.key if i == 0 else digest({"old": i})
            db.execute(
                "INSERT INTO events(id,received_at,occurred_at,mode,reason,snapshot) VALUES(?,?,?,?,?,?)",
                (key, now, now - 60, "live", "positive_score", canonical(snapshot)),
            )
            db.execute(
                """INSERT INTO operations(
                event_id,companion,user_id,task_id,status,next_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (key, "Heal", IDS["Heal"], str(uuid4()), status, now, now, now),
            )
    return payload


def test_upgrade_preserves_history_and_never_replays_legacy_rewards(cfg, fake, admin, hook, tmp_path):
    old_payload = old_database(cfg)
    snapshot = tmp_path / "before-upgrade.sqlite3"
    backup(cfg.database, snapshot)
    legacy_id = str(uuid4())
    legacy = {
        "id": legacy_id,
        "type": "habit",
        "up": True,
        "down": False,
        "priority": 1,
        "alias": cfg.technical_alias,
        "text": cfg.technical_title,
        "value": 7,
    }
    fake.tasks[IDS["Heal"]][legacy_id] = legacy.copy()
    app = create_app(cfg, Habitica(cfg, transport=httpx.MockTransport(fake.handle)), worker=False)
    with TestClient(app) as client:
        svc = app.state.service
        assert svc.db.setting("mode") == "observe"
        assert fake.calls == []
        assert svc.db.rows("PRAGMA user_version")[0]["user_version"] == 2
        assert len(svc.db.rows("SELECT * FROM events")) == 6
        states = [r["status"] for r in svc.db.rows("SELECT status FROM operations ORDER BY id")]
        assert states == ["cancelled", "cancelled", "uncertain", "succeeded", "uncertain", "failed"]
        assert all(not c["prepared"] for c in svc.status()["companions"])
        assert client.post(hook, json=old_payload).json()["duplicate"] is True
        assert client.post("/api/mode", headers=admin, json={"mode": "live"}).status_code == 409
        report = svc.audit("Heal")
        assert report["legacy_habits"] == [{"id": legacy_id, "text": cfg.technical_title}]
        assert report["conflicts"] == []
        prepare_all(client, admin)
        client.post("/api/mode", headers=admin, json={"mode": "live"}).raise_for_status()
        assert client.post(hook, json=old_payload).json()["duplicate"] is True
        drain(client, 10)
        assert fake.scores == []
        assert (
            client.post(
                "/api/operations/3/resolve",
                headers=admin,
                json={
                    "action": "retry",
                    "note": "ancienne opération à vérifier",
                    "acknowledge_risk": True,
                },
            ).status_code
            == 409
        )
        client.post(hook, json=scored_payload("todo", priority=2)).raise_for_status()
        drain(client)
        assert len(fake.scores) == 3
        assert fake.tasks[IDS["Heal"]][legacy_id] == legacy
    with sqlite3.connect(snapshot) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0] == '"live"'


def test_migration_rolls_back_atomically(cfg):
    old_database(cfg)

    class BrokenStore(Store):
        def migrate(self, db):
            super().migrate(db)
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        BrokenStore(cfg.database)
    with sqlite3.connect(cfg.database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0] == '"live"'
        assert "reward_key" not in {r[1] for r in db.execute("PRAGMA table_info(operations)")}
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='reward_habits'").fetchone()


def test_migration_refuses_running_legacy_instance(cfg):
    old_database(cfg)
    with open(cfg.database + ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Arrêter l'ancienne"):
            Store(cfg.database)
