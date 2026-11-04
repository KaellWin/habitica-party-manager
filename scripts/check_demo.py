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
            for kind in ("habit", "daily", "todo", "duplicate", "negative", "habit", "duplicate"):
                result = fake.post("/simulate/" + kind).raise_for_status().json()
                assert result["receiver_status"] == 202, result
            for _attempt in range(100):
                total = fake.get("/simulation-status").json()["total"]
                if total == baseline + 12:
                    break
                time.sleep(0.2)
            assert total == baseline + 12, total
            print("OK : 4 validations positives → 12 récompenses ; doublons, négatif et simulation → 0.")
            print("La démonstration reste active. Arrêt : docker compose -f compose.demo.yaml down")


if __name__ == "__main__":
    main()
