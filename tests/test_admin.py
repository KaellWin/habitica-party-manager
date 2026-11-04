import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from hpm.config import Settings
from hpm.simulator import IDS
from hpm.store import Store


def test_authentication_and_security_headers(client, hook, payload):
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/mode", json={"mode": "live"}).status_code == 401
    assert client.post("/hooks/habitica/wrong", json=payload).status_code == 404
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}


def test_invalid_payload_and_body_limit(client, hook):
    assert client.post(hook, content="not-json").status_code == 422
    assert client.post(hook, json=[]).status_code == 422
    assert client.post(hook, content='{"delta":NaN}').status_code == 422
    assert client.post(hook, content="x" * 1048577).status_code == 413


def test_live_requires_preparation(client, admin):
    assert client.post("/api/mode", headers=admin, json={"mode": "live"}).status_code == 409


def test_existing_tasks_audited_and_never_modified(client, admin, fake):
    task_id = str(uuid4())
    daily = {"id": task_id, "type": "daily", "text": "Brossage des dents", "completed": False}
    fake.tasks[IDS["Heal"]][task_id] = daily.copy()
    report = client.post("/api/companions/Heal/audit", headers=admin).json()
    assert report["conflicts"][0]["warning"] == "penalites_cron_possibles"
    body = {"audit_id": report["audit_id"], "create": True}
    assert client.post("/api/companions/Heal/prepare", headers=admin, json=body).status_code == 409
    body["acknowledge_conflicts"] = True
    assert client.post("/api/companions/Heal/prepare", headers=admin, json=body).status_code == 200
    assert fake.tasks[IDS["Heal"]][task_id] == daily
    assert all(method not in {"PUT", "DELETE"} for _, method, _ in fake.calls)
    again = client.post("/api/companions/Heal/audit", headers=admin).json()
    body["audit_id"] = again["audit_id"]
    assert client.post("/api/companions/Heal/prepare", headers=admin, json=body).status_code == 200
    assert len(fake.tasks[IDS["Heal"]]) == 2


def test_changed_tasks_require_new_audit(client, admin, fake):
    report = client.post("/api/companions/Heal/audit", headers=admin).json()
    fake.tasks[IDS["Heal"]]["new"] = {"id": "new", "type": "daily", "text": "Nouvelle tâche"}
    response = client.post(
        "/api/companions/Heal/prepare",
        headers=admin,
        json={
            "audit_id": report["audit_id"],
            "create": True,
            "acknowledge_conflicts": True,
        },
    )
    assert response.status_code == 409


def test_simulation_prevents_creation(client, admin, fake):
    report = client.post("/api/companions/Heal/audit", headers=admin).json()
    client.post("/api/mode", headers=admin, json={"mode": "dry-run"})
    response = client.post(
        "/api/companions/Heal/prepare",
        headers=admin,
        json={
            "audit_id": report["audit_id"],
            "create": True,
        },
    )
    assert response.status_code == 409
    assert not any(method == "POST" for _, method, _ in fake.calls)


def test_secrets_never_returned(client, admin, cfg, fake):
    fake.hooks.append(
        {
            "type": "taskActivity",
            "url": "http://hpm/hooks/" + cfg.webhook_token.get_secret_value(),
            "enabled": True,
            "failures": 0,
        }
    )
    for path in ["/api/status", "/api/events", "/api/connection", "/api/audit-log"]:
        response = client.get(path, headers=admin)
        assert response.status_code == 200
        assert cfg.admin_token.get_secret_value() not in response.text
        assert cfg.webhook_token.get_secret_value() not in response.text
        assert "fake-test-key" not in response.text


def test_single_worker_lock(cfg):
    first, second = Store(cfg.database), Store(cfg.database)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="seule instance"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_config_refuses_master_as_companion(cfg):
    data = cfg.model_dump()
    data["companions"][0]["user_id"] = IDS["Kaell"]
    with pytest.raises(ValidationError, match="maître"):
        Settings.model_validate(data)


def test_event_log_keeps_only_allowlisted_fields(client, hook, payload):
    payload["user"]["apiToken"] = "sensitive-value"
    payload["task"]["notes"] = "Private note"
    client.post(hook, json=payload)
    row = client.app.state.service.db.rows("SELECT snapshot FROM events")[0]
    assert "sensitive-value" not in row["snapshot"]
    assert "Private note" not in row["snapshot"]
    assert json.loads(row["snapshot"])["type"] == "scored"
