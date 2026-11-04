import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from hpm.app import create_app
from hpm.config import Account, Settings
from hpm.habitica import Habitica
from hpm.simulator import IDS, SimulatedHabitica, scored_payload


@pytest.fixture
def cfg(tmp_path):
    key = tmp_path / "api_key"
    key.write_text("fake-test-key")
    return Settings(
        base_url="http://habitica.test",
        database=str(tmp_path / "hpm.sqlite3"),
        admin_token="test-admin-" + "a" * 40,
        webhook_token="test-webhook-" + "b" * 40,
        master=Account(name="Kaell", user_id=IDS["Kaell"], token_file=str(key)),
        companions=[
            Account(name=name, user_id=IDS[name], token_file=str(key)) for name in IDS if name != "Kaell"
        ],
    )


@pytest.fixture
def fake():
    return SimulatedHabitica()


@pytest.fixture
def client(cfg, fake):
    api = Habitica(cfg, transport=httpx.MockTransport(fake.handle))
    with TestClient(create_app(cfg, api, worker=False)) as client:
        yield client


@pytest.fixture
def admin(cfg):
    return {"Authorization": "Bearer " + cfg.admin_token.get_secret_value()}


@pytest.fixture
def hook(cfg):
    return "/hooks/habitica/" + cfg.webhook_token.get_secret_value()


@pytest.fixture
def payload():
    return copy.deepcopy(scored_payload())


def prepare_all(client, admin):
    for name in ("Heal", "Magus", "War"):
        response = client.post(f"/api/companions/{name}/audit", headers=admin)
        assert response.status_code == 200, response.text
        audit = response.json()
        response = client.post(
            f"/api/companions/{name}/prepare",
            headers=admin,
            json={
                "audit_id": audit["audit_id"],
                "create": True,
                "acknowledge_conflicts": True,
            },
        )
        assert response.status_code == 200, response.text


@pytest.fixture
def live(client, admin):
    prepare_all(client, admin)
    response = client.post("/api/mode", headers=admin, json={"mode": "live"})
    assert response.status_code == 200, response.text
    return client


def drain(client, count=3):
    for _ in range(count):
        client.app.state.service.dispatch_once()
