import httpx
import pytest
from fastapi.testclient import TestClient

from hpm.app import create_app
from hpm.config import Account
from hpm.habitica import Habitica
from hpm.simulator import IDS, scored_payload

from .conftest import drain


@pytest.mark.parametrize("count", [1, 4])
def test_variable_number_of_companions(cfg, fake, count):
    extra_id = "00000000-0000-4000-8000-000000000099"
    extra = Account(name="Druid", user_id=extra_id, token_file=cfg.master.token_file)
    fake.tasks[extra_id] = {}
    settings = cfg.model_copy(update={"companions": [*cfg.companions, extra][:count]})
    api = Habitica(settings, transport=httpx.MockTransport(fake.handle))
    with TestClient(create_app(settings, api, worker=False)) as app:
        svc = app.app.state.service
        for account in settings.companions:
            audit = svc.audit(account.name)
            svc.prepare(account.name, audit["audit_id"], True, True)
        svc.set_mode("live")
        svc.ingest(scored_payload())
        drain(app, count)
        assert len(fake.scores) == count
        assert all(user != IDS["Kaell"] for user, _ in fake.scores)


def test_retry_limit_is_terminal(live, hook, fake):
    svc = live.app.state.service
    fake.failures[(IDS["Heal"], "GET")] = [503] * svc.cfg.max_attempts
    live.post(hook, json=scored_payload())
    for _ in range(svc.cfg.max_attempts + 3):
        drain(live)
        with svc.db.connect() as db:
            db.execute("UPDATE operations SET next_at=0 WHERE status='retry'")
    op = svc.db.rows("SELECT * FROM operations WHERE companion='Heal'")[0]
    assert op["status"] == "failed"
    assert op["attempts"] == svc.cfg.max_attempts
    assert len(fake.scores) == 2


def test_target_configuration_change_requires_new_preparation(cfg, fake, admin, hook):
    original = create_app(cfg, Habitica(cfg, transport=httpx.MockTransport(fake.handle)), worker=False)
    with TestClient(original) as app:
        svc = app.app.state.service
        for account in cfg.companions:
            audit = svc.audit(account.name)
            svc.prepare(account.name, audit["audit_id"], True, True)
        svc.set_mode("live")
        svc.ingest(scored_payload())
    changed = cfg.model_copy(update={"technical_alias": "hpm-new-policy"})
    replacement = create_app(
        changed, Habitica(changed, transport=httpx.MockTransport(fake.handle)), worker=False
    )
    with TestClient(replacement) as app:
        assert all(not c["prepared"] for c in app.get("/api/status", headers=admin).json()["companions"])
        drain(app)
        assert fake.scores == []
