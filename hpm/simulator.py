"""Instance Habitica simulée, réservée aux tests et à compose.demo.yaml.

Les payloads sont synthétiques, construits d'après le code du fork v5.50.6.
Ils ne constituent pas une capture réelle du serveur de l'utilisateur.
"""

import copy
import os
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

IDS = {
    "Kaell": "1afbfa65-f077-4e0a-8287-af7e82e27672",
    "Heal": "9d230257-cd6f-446a-a609-2be8753d9ed6",
    "Magus": "292ee24a-009c-42d9-8c82-ddded710246c",
    "War": "aee3d540-e2aa-428d-a437-f309093213af",
}
SOURCE_TASK_ID = "00000000-0000-4000-8000-000000000001"


def scored_payload(kind="habit", *, sequence=1, timestamp=None):
    stamp = timestamp or time.time()
    task = {
        "id": SOURCE_TASK_ID,
        "_id": SOURCE_TASK_ID,
        "type": kind,
        "value": sequence,
        "text": "Palier de pas simulé",
        "userId": IDS["Kaell"],
    }
    if kind == "habit":
        task.update(
            counterUp=sequence,
            counterDown=0,
            history=[
                {
                    "date": int(stamp * 1000),
                    "value": sequence,
                    "scoredUp": sequence,
                    "scoredDown": 0,
                }
            ],
        )
    elif kind == "daily":
        task.update(
            completed=True,
            history=[
                {
                    "date": int(stamp * 1000),
                    "value": sequence,
                    "completed": True,
                    "isDue": True,
                }
            ],
        )
    elif kind == "todo":
        task.update(completed=True, dateCompleted=datetime.fromtimestamp(stamp, timezone.utc).isoformat())
    return {
        "webhookType": "taskActivity",
        "type": "scored",
        "direction": "up",
        "delta": 1,
        "task": task,
        "user": {"_id": IDS["Kaell"], "stats": {"exp": sequence, "gp": sequence}},
    }


class SimulatedHabitica:
    def __init__(self):
        self.tasks = {value: {} for value in IDS.values()}
        self.calls = []
        self.scores = []
        self.failures = {}
        self.hooks = []
        self.guard = threading.Lock()
        self.sequence = 0
        self.last_payload = None

    def handle(self, request: httpx.Request):
        with self.guard:
            user = request.headers.get("x-api-user")
            method, path = request.method, request.url.path
            self.calls.append((user, method, path))
            if user not in self.tasks or not request.headers.get("x-api-key"):
                return httpx.Response(401, json={"success": False})
            failure_key = (user, method)
            if self.failures.get(failure_key):
                failure = self.failures[failure_key].pop(0)
                if isinstance(failure, Exception):
                    raise failure
                return httpx.Response(failure, json={"success": False}, headers={"Retry-After": "2"})
            tasks = self.tasks[user]
            result = None
            code = 200
            if path == "/api/v3/user" and method == "GET":
                result = {"_id": user, "stats": {"lvl": 10}}
            elif path == "/api/v3/user/webhook":
                if method == "POST":
                    body = __import__("json").loads(request.content)
                    result = {"id": str(uuid4()), "enabled": True, "failures": 0, **body}
                    self.hooks.append(result)
                    code = 201
                else:
                    result = self.hooks
            elif path == "/api/v3/tasks/user" and method == "GET":
                result = list(tasks.values())
            elif path == "/api/v3/tasks/user" and method == "POST":
                body = __import__("json").loads(request.content)
                if any(t.get("alias") == body.get("alias") for t in tasks.values()):
                    return httpx.Response(400, json={"success": False})
                task_id = str(uuid4())
                result = {"id": task_id, "_id": task_id, "userId": user, "value": 0, **body}
                tasks[task_id] = result
                code = 201
            elif path.startswith("/api/v3/tasks/"):
                task_id = path.split("/")[4]
                if task_id not in tasks:
                    return httpx.Response(404, json={"success": False})
                if method == "GET":
                    result = tasks[task_id]
                elif path.endswith("/score/up") and method == "POST":
                    tasks[task_id]["value"] += 1
                    self.scores.append((user, task_id))
                    result = {"exp": len(self.scores), "gp": len(self.scores), "delta": 1}
            if result is None:
                return httpx.Response(404, json={"success": False})
            return httpx.Response(code, json={"success": True, "data": copy.deepcopy(result)})


backend = SimulatedHabitica()
app = FastAPI(title="Habitica simulée — aucune donnée réelle")


@app.api_route("/api/v3/{path:path}", methods=["GET", "POST"])
async def api_route(path: str, request: Request):
    fake = httpx.Request(
        request.method,
        "http://simulated/api/v3/" + path,
        headers=dict(request.headers),
        content=await request.body(),
    )
    response = backend.handle(fake)
    return JSONResponse(response.json(), status_code=response.status_code, headers=dict(response.headers))


@app.post("/simulate/{kind}")
async def simulate(kind: str):
    if kind not in {"habit", "daily", "todo", "duplicate", "negative"}:
        return JSONResponse({"detail": "Type inconnu"}, status_code=400)
    if kind == "duplicate":
        payload = backend.last_payload
        if payload is None:
            return JSONResponse({"detail": "Émettre une validation avant le doublon"}, status_code=409)
    else:
        backend.sequence += 1
        payload = scored_payload("habit" if kind == "negative" else kind, sequence=backend.sequence)
        if kind == "negative":
            payload["direction"], payload["delta"] = "down", -1
        backend.last_payload = payload
    url = os.environ.get("HPM_DEMO_WEBHOOK_URL")
    if not url:
        return JSONResponse({"detail": "URL de simulation absente"}, status_code=503)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
    return {"receiver_status": response.status_code, "receiver": response.json()}


@app.get("/simulation-status")
def simulation_status():
    return {"scores": backend.scores, "total": len(backend.scores)}
