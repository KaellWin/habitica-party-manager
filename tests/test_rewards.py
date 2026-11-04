import copy

import httpx
import pytest

from hpm.rewards import REWARDS
from hpm.simulator import IDS, scored_payload

from .conftest import drain


@pytest.mark.parametrize("kind", ["habit", "daily", "todo"])
@pytest.mark.parametrize("priority", [0.1, 1, 1.5, 2])
def test_every_type_and_difficulty_scores_one_matching_habit(live, hook, fake, kind, priority):
    payload = scored_payload(kind, priority=priority)
    live.post(hook, json=payload).raise_for_status()
    live.post(hook, json=payload).raise_for_status()
    drain(live, 10)
    assert len(fake.scores) == 3
    for user, task_id in fake.scores:
        assert user != IDS["Kaell"]
        task = fake.tasks[user][task_id]
        assert task["type"] == "habit"
        assert task["priority"] == priority
        assert task["up"] is True and task["down"] is False
        assert task["alias"].startswith("hpm-progression-kaell-" + kind + "-")
    operations = live.app.state.service.db.rows("SELECT * FROM operations")
    assert all(op["source_type"] == kind and op["source_priority"] == priority for op in operations)


def test_same_difficulty_different_types_use_distinct_habits(live, hook, fake):
    for kind in ["habit", "daily", "todo"]:
        live.post(hook, json=scored_payload(kind, priority=1.5)).raise_for_status()
    drain(live, 10)
    assert len(fake.scores) == 9
    assert len({task for user, task in fake.scores if user == IDS["Heal"]}) == 3


@pytest.mark.parametrize("priority", [None, 0, 3, "2", True, {"priority": 2}])
def test_missing_or_invalid_difficulty_fails_closed(live, hook, fake, priority):
    payload = scored_payload(priority=priority)
    response = live.post(hook, json=payload).raise_for_status().json()
    assert response["reason"] == "invalid_source_priority"
    drain(live)
    assert fake.scores == []


def test_routing_is_frozen_and_difficulty_is_not_a_new_event_id(live, hook, fake):
    payload = scored_payload("daily", priority=0.1)
    live.post(hook, json=payload).raise_for_status()
    edited = copy.deepcopy(payload)
    edited["task"]["priority"] = 2
    assert live.post(hook, json=edited).json()["duplicate"] is True
    drain(live)
    assert len(fake.scores) == 3
    assert all(fake.tasks[user][task]["priority"] == 0.1 for user, task in fake.scores)
    live.post(hook, json=scored_payload("daily", sequence=2, priority=2)).raise_for_status()
    drain(live)
    assert len(fake.scores) == 6
    assert all(fake.tasks[user][task]["priority"] == 2 for user, task in fake.scores[3:])


def test_wrong_target_difficulty_never_scores_that_companion(live, hook, fake):
    task = next(t for t in fake.tasks[IDS["Heal"]].values() if t["alias"].endswith("daily-medium"))
    task["priority"] = 2
    live.post(hook, json=scored_payload("daily", priority=1.5)).raise_for_status()
    drain(live)
    assert len(fake.scores) == 2
    row = live.app.state.service.db.rows("SELECT * FROM operations WHERE companion='Heal'")[0]
    assert row["status"] == "failed"
    assert row["error"] == "technical_habit_difficulty_mismatch"


def test_partial_creation_lost_response_resumes_by_alias(client, admin, fake):
    svc = client.app.state.service
    original = fake.handle
    lost = False

    def remote(request):
        nonlocal lost
        response = original(request)
        if not lost and request.method == "POST" and request.url.path == "/api/v3/tasks/user":
            lost = True
            raise httpx.ReadTimeout("creation committed, response lost")
        return response

    svc.api.close()
    from hpm.habitica import Habitica

    svc.api = Habitica(svc.cfg, transport=httpx.MockTransport(remote))
    audit = client.post("/api/companions/Heal/audit", headers=admin).raise_for_status().json()
    request = {"audit_id": audit["audit_id"], "create": True}
    response = client.post("/api/companions/Heal/prepare", headers=admin, json=request)
    assert response.status_code == 502 and response.json()["uncertain"] is True
    assert len(fake.tasks[IDS["Heal"]]) == 1
    assert not svc.db.rows("SELECT prepared FROM companions WHERE name='Heal'")[0]["prepared"]
    audit = client.post("/api/companions/Heal/audit", headers=admin).raise_for_status().json()
    assert audit["missing_habits"] == 11
    request["audit_id"] = audit["audit_id"]
    client.post("/api/companions/Heal/prepare", headers=admin, json=request).raise_for_status()
    assert len(fake.tasks[IDS["Heal"]]) == len(REWARDS) == 12


def test_dry_run_records_type_and_difficulty_without_creating_tasks(client, admin, hook, fake):
    client.post("/api/mode", headers=admin, json={"mode": "dry-run"}).raise_for_status()
    client.post(hook, json=scored_payload("todo", priority=2)).raise_for_status()
    operations = client.get("/api/status", headers=admin).json()["last_operations"]
    assert len(operations) == 3
    assert all(op["reward_key"] == "todo-hard" and op["status"] == "simulated" for op in operations)
    assert fake.calls == []
