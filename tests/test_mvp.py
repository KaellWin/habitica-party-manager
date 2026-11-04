import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from hpm.app import create_app
from hpm.habitica import Habitica
from hpm.simulator import IDS, scored_payload

from .conftest import drain, prepare_all


@pytest.mark.parametrize("kind", ["habit", "daily", "todo"])
def test_positive_task_rewards_exactly_three(live, admin, hook, fake, kind):
    payload = scored_payload(kind)
    assert live.post(hook, json=payload).status_code == 202
    drain(live)
    assert sorted(user for user, _ in fake.scores) == sorted(IDS[name] for name in ["Heal", "Magus", "War"])
    assert all(user != IDS["Kaell"] for user, _ in fake.scores)
    assert live.get("/api/status", headers=admin).json()["operations"] == [
        {"status": "succeeded", "count": 3}
    ]


def test_successive_habit_and_home_assistant_duplicate(live, hook, fake):
    # Même tâche et même milliseconde : le compteur et la valeur distinguent les validations.
    stamp = time.time()
    one = scored_payload(sequence=1, timestamp=stamp)
    two = scored_payload(sequence=2, timestamp=stamp)
    for payload in (one, two, one, two):
        assert live.post(hook, json=payload).status_code == 202
    drain(live, 10)
    assert len(fake.scores) == 6


def test_canonical_duplicate_ignores_object_order(live, hook, payload, fake):
    live.post(hook, json=payload)
    reordered = dict(reversed(list(payload.items())))
    response = live.post(hook, content=json.dumps(reordered, indent=4))
    assert response.json()["duplicate"] is True
    drain(live, 10)
    assert len(fake.scores) == 3


@pytest.mark.parametrize(
    "change",
    [
        {"direction": "down", "delta": -1},
        {"type": "created"},
        {"type": "updated"},
        {"type": "deleted"},
        {"type": "checklistScored"},
        {"delta": 0},
        {"delta": -1},
        {"user": {"_id": IDS["Heal"]}},
        {"webhookType": "globalActivity"},
    ],
)
def test_ignored_actions(live, hook, payload, fake, change):
    payload.update(change)
    assert live.post(hook, json=payload).status_code == 202
    drain(live)
    assert fake.scores == []


@pytest.mark.parametrize(
    "task",
    [
        {"type": "reward", "id": "task", "value": 1},
        {"type": "habit", "id": "task", "value": 1},
        {"type": "daily", "id": "task", "value": 1, "completed": False},
        {"type": "habit", "id": "task", "value": 1, "history": [{"date": 123}]},
        {"type": ["habit"]},
        {},
        None,
    ],
)
def test_unverified_payload_is_observed_without_reward(live, hook, payload, fake, task):
    payload["task"] = task
    assert live.post(hook, json=payload).status_code == 202
    drain(live)
    assert fake.scores == []


def test_observation_simulation_and_off_never_write(client, admin, hook, fake):
    for index, mode in enumerate(["observe", "dry-run", "off"]):
        client.post("/api/mode", headers=admin, json={"mode": mode})
        response = client.post(hook, json=scored_payload(sequence=index + 1))
        assert response.status_code == 202
        drain(client)
    assert fake.calls == []
    state = client.get("/api/status", headers=admin).json()
    assert state["events"]["unique_events"] == 3
    assert state["operations"] == [{"status": "simulated", "count": 3}]


def test_observed_events_never_replayed(client, admin, hook, payload, fake):
    client.post(hook, json=payload)
    prepare_all(client, admin)
    client.post("/api/mode", headers=admin, json={"mode": "live"})
    client.post(hook, json=payload)
    drain(client)
    assert fake.scores == []


def test_old_or_future_event_not_backfilled(live, hook, fake):
    old = live.post(hook, json=scored_payload(timestamp=time.time() - 60)).json()
    future = live.post(hook, json=scored_payload(sequence=2, timestamp=time.time() + 600)).json()
    assert old["reason"] == "before_live_activation"
    assert future["reason"] == "future_score_time"
    drain(live)
    assert fake.scores == []


def test_disabled_companion_and_stop_cancel_queue(live, admin, hook, fake):
    live.post(hook, json=scored_payload())
    live.post("/api/companions/Heal/enabled", headers=admin, json={"enabled": False})
    drain(live)
    assert len(fake.scores) == 2
    live.post(hook, json=scored_payload(sequence=2))
    live.post("/api/mode", headers=admin, json={"mode": "off"})
    live.post("/api/mode", headers=admin, json={"mode": "live"})
    drain(live)
    assert len(fake.scores) == 2


@pytest.mark.parametrize("failure", [429, httpx.ConnectError("connection refused")])
def test_retry_isolated_per_companion(live, hook, fake, failure):
    fake.failures[(IDS["Heal"], "POST")] = [failure]
    live.post(hook, json=scored_payload())
    drain(live)
    assert len(fake.scores) == 2
    svc = live.app.state.service
    assert svc.db.rows("SELECT status FROM operations WHERE companion='Heal'")[0]["status"] == "retry"
    with svc.db.connect() as db:
        db.execute("UPDATE operations SET next_at=0 WHERE companion='Heal'")
    drain(live)
    assert len(fake.scores) == 3


@pytest.mark.parametrize(
    "failure", [500, 502, 504, httpx.ReadTimeout("after request sent"), httpx.WriteError("partial write")]
)
def test_ambiguous_post_is_never_automatically_retried(live, hook, fake, failure):
    fake.failures[(IDS["Heal"], "POST")] = [failure]
    live.post(hook, json=scored_payload())
    drain(live, 10)
    row = live.app.state.service.db.rows("SELECT * FROM operations WHERE companion='Heal'")[0]
    assert row["status"] == "uncertain"
    assert row["attempts"] == 1
    assert len(fake.scores) == 2


def test_read_failure_can_retry_before_post(live, hook, fake):
    fake.failures[(IDS["Heal"], "GET")] = [503]
    live.post(hook, json=scored_payload())
    drain(live)
    assert len(fake.scores) == 2
    assert (
        live.app.state.service.db.rows("SELECT status FROM operations WHERE companion='Heal'")[0]["status"]
        == "retry"
    )


def test_http_401_is_terminal_and_does_not_block_other_accounts(live, hook, fake):
    fake.failures[(IDS["Heal"], "POST")] = [401]
    live.post(hook, json=scored_payload())
    drain(live, 10)
    assert len(fake.scores) == 2
    assert (
        live.app.state.service.db.rows("SELECT status FROM operations WHERE companion='Heal'")[0]["status"]
        == "failed"
    )


def test_technical_task_became_negative_capable(live, hook, fake):
    next(t for t in fake.tasks[IDS["Heal"]].values() if t["alias"].endswith("-habit-easy"))["down"] = True
    live.post(hook, json=scored_payload())
    drain(live)
    assert len(fake.scores) == 2


def test_restart_preserves_queue_and_dedup(cfg, fake, admin, hook):
    def new_app():
        return create_app(cfg, Habitica(cfg, transport=httpx.MockTransport(fake.handle)), worker=False)

    with TestClient(new_app()) as first:
        prepare_all(first, admin)
        first.post("/api/mode", headers=admin, json={"mode": "live"})
        payload = scored_payload()
        first.post(hook, json=payload)
        drain(first, 1)
        with first.app.state.service.db.connect() as db:
            db.execute("UPDATE operations SET status='sending' WHERE companion='Magus'")
    with TestClient(new_app()) as second:
        assert second.post(hook, json=payload).json()["duplicate"] is True
        drain(second, 10)
        assert len(fake.scores) == 2  # Magus reste incertain, War reprend, Heal déjà terminé.
        states = second.get("/api/status", headers=admin).json()["operations"]
        assert {x["status"]: x["count"] for x in states} == {"succeeded": 2, "uncertain": 1}


def test_concurrent_duplicate_receipts(live, hook, fake):
    payload = scored_payload()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: live.post(hook, json=payload), range(16)))
    assert all(r.status_code == 202 for r in results)
    drain(live, 20)
    assert len(fake.scores) == 3


def test_manual_resolution_requires_risk_ack(live, admin, hook, fake):
    fake.failures[(IDS["Heal"], "POST")] = [500]
    live.post(hook, json=scored_payload())
    drain(live)
    op = live.app.state.service.db.rows("SELECT id FROM operations WHERE status='uncertain'")[0]["id"]
    body = {"action": "retry", "note": "Vérification manuelle de l'historique"}
    assert live.post(f"/api/operations/{op}/resolve", headers=admin, json=body).status_code == 409
    body["acknowledge_risk"] = True
    assert live.post(f"/api/operations/{op}/resolve", headers=admin, json=body).status_code == 200
    drain(live)
    assert len(fake.scores) == 3


def test_actual_remote_success_then_lost_response_is_quarantined(live, hook, fake):
    original = fake.handle
    lost = False

    def remote(request):
        nonlocal lost
        result = original(request)
        if request.method == "POST" and "/score/" in request.url.path and not lost:
            lost = True
            raise httpx.ReadTimeout("response lost after scoring")
        return result

    live.app.state.service.api.client.close()
    live.app.state.service.api = Habitica(live.app.state.service.cfg, transport=httpx.MockTransport(remote))
    live.post(hook, json=scored_payload())
    drain(live, 10)
    assert len(fake.scores) == 3
    assert (
        live.app.state.service.db.rows("SELECT count(*) AS n FROM operations WHERE status='uncertain'")[0][
            "n"
        ]
        == 1
    )
