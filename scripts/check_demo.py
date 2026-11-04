#!/usr/bin/env python3
"""Essai HTTP complet de compose.demo.yaml ; refuse une véritable instance Habitica."""

import time

import httpx

ADMIN = "demo-admin-" + "0" * 36


def main():
    with httpx.Client(
        base_url="http://127.0.0.1:8088", timeout=10, headers={"Authorization": "Bearer " + ADMIN}
    ) as app:
        for _attempt in range(100):
            try:
                if app.get("/healthz").is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        state = app.get("/api/status").raise_for_status().json()
        if state["base_url"] != "http://habitica-mock:3000":
            raise SystemExit("Refus : cet essai exige la configuration Habitica simulée.")
        with httpx.Client(base_url="http://127.0.0.1:8099", timeout=10) as fake:
            baseline = fake.get("/simulation-status").raise_for_status().json()["total"]
            app.post("/api/mode", json={"mode": "observe"}).raise_for_status()
            for name in ("Heal", "Magus", "War"):
                audit = app.post(f"/api/companions/{name}/audit").raise_for_status().json()
                app.post(
                    f"/api/companions/{name}/prepare",
                    json={
                        "audit_id": audit["audit_id"],
                        "create": True,
                        "acknowledge_conflicts": True,
                    },
                ).raise_for_status()
            app.post("/api/mode", json={"mode": "dry-run"}).raise_for_status()
            fake.post("/simulate/habit").raise_for_status()
            assert fake.get("/simulation-status").json()["total"] == baseline
            app.post("/api/mode", json={"mode": "live"}).raise_for_status()
            for kind in ("habit", "daily", "todo"):
                for priority in (0.1, 1, 1.5, 2):
                    result = (
                        fake.post("/simulate/" + kind, params={"priority": priority})
                        .raise_for_status()
                        .json()
                    )
                    assert result["receiver_status"] == 202, result
            for kind in ("duplicate", "negative", "duplicate"):
                result = fake.post("/simulate/" + kind).raise_for_status().json()
                assert result["receiver_status"] == 202, result
            for _attempt in range(150):
                total = fake.get("/simulation-status").json()["total"]
                if total == baseline + 36:
                    break
                time.sleep(0.2)
            assert total == baseline + 36, total
            scores = fake.get("/simulation-status").json()["scored_habits"][baseline:]
            for kind in ("habit", "daily", "todo"):
                for level, priority in (("trivial", 0.1), ("easy", 1), ("medium", 1.5), ("hard", 2)):
                    matching = [s for s in scores if s["alias"].endswith(f"-{kind}-{level}")]
                    assert len(matching) == 3 and all(s["priority"] == priority for s in matching)
                    assert len({s["user_id"] for s in matching}) == 3
            print("OK : 12 couples type/difficulté → 36 récompenses ; doublons, négatif et simulation → 0.")
            print("La démonstration reste active. Arrêt : docker compose -f compose.demo.yaml down")


if __name__ == "__main__":
    main()
