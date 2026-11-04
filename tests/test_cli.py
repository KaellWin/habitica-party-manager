import json
import sqlite3

import httpx
import pytest

from hpm.cli import backup, register_webhook
from hpm.config import load_settings
from hpm.habitica import Habitica, retry_after_seconds
from hpm.simulator import IDS


def test_webhook_installation_is_idempotent_and_master_only(cfg, fake):
    api = Habitica(cfg, transport=httpx.MockTransport(fake.handle))
    try:
        assert register_webhook(cfg, api, "http://hpm.internal:8080")["status"] == "configured"
        result = register_webhook(cfg, api, "http://hpm.internal:8080")
        assert result["status"] == "already_configured"
        assert len(fake.hooks) == 1
        assert fake.hooks[0]["options"]["scored"] is True
        assert all(user == IDS["Kaell"] for user, _, _ in fake.calls)
        assert cfg.webhook_token.get_secret_value() not in json.dumps(result)
        with pytest.raises(ValueError, match="diffère"):
            register_webhook(cfg, api, "http://new.internal:8080")
        assert len(fake.hooks) == 1
    finally:
        api.close()


def test_online_backup_is_consistent_and_does_not_overwrite(client, hook, payload, cfg, tmp_path):
    client.post(hook, json=payload)
    destination = tmp_path / "snapshot.sqlite3"
    backup(cfg.database, destination)
    with sqlite3.connect(destination) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    with pytest.raises(ValueError, match="nouvelle destination"):
        backup(cfg.database, destination)
    assert destination.stat().st_mode & 0o777 == 0o600


def test_invalid_config_never_discloses_secrets(tmp_path, monkeypatch):
    path = tmp_path / "invalid.toml"
    path.write_text('base_url = "invalid"\n')
    monkeypatch.setenv("HPM_CONFIG", str(path))
    monkeypatch.setenv("HPM_ADMIN_TOKEN", "real-private-admin-" + "x" * 40)
    monkeypatch.setenv("HPM_WEBHOOK_TOKEN", "real-private-webhook-" + "y" * 40)
    with pytest.raises(ValueError) as caught:
        load_settings()
    assert "private" not in str(caught.value)


def test_retry_after_seconds_and_http_date():
    assert retry_after_seconds("120") == 120
    assert retry_after_seconds("invalid") == 0
    assert retry_after_seconds("Wed, 01 Jan 2020 00:00:00 GMT") == 0
