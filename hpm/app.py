import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .config import Mode, Settings, load_settings
from .habitica import APIError, Habitica
from .service import Conflict, Service


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModeChange(Input):
    mode: Mode


class EnabledChange(Input):
    enabled: bool


class Prepare(Input):
    audit_id: str = Field(min_length=64, max_length=64)
    create: bool = False
    acknowledge_conflicts: bool = False


class Resolution(Input):
    action: Literal["confirm", "abandon", "retry"]
    note: str = Field(min_length=10, max_length=500)
    acknowledge_risk: bool = False


def create_app(settings: Settings | None = None, api: Habitica | None = None, *, worker=True):
    @asynccontextmanager
    async def lifespan(app):
        logging.getLogger("httpx").setLevel(logging.WARNING)
        cfg = settings or load_settings()
        service = Service(cfg, api)
        app.state.service = service
        service.start(worker=worker)
        try:
            yield
        finally:
            service.close()

    app = FastAPI(
        title="Habitica Party Manager",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security(request: Request, call_next):
        # Limitation avant parsing, y compris les transferts chunked.
        limit = app.state.service.cfg.body_limit
        if request.method in {"POST", "PUT", "PATCH"}:
            size = 0
            parts = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return JSONResponse({"detail": "Corps trop volumineux"}, status_code=413)
                parts.append(chunk)
            request._body = b"".join(parts)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    def service(request: Request):
        return request.app.state.service

    def authenticated(request: Request, svc: Annotated[Service, Depends(service)]):
        value = request.headers.get("Authorization", "")
        expected = "Bearer " + svc.cfg.admin_token.get_secret_value()
        if not hmac.compare_digest(value.encode(), expected.encode()):
            raise HTTPException(401, "Authentification requise", headers={"WWW-Authenticate": "Bearer"})
        # Jeton uniquement en en-tête : pas de cookies, ni de CORS ouvert, ni de jetons en URL.
        return svc

    Admin = Annotated[Service, Depends(authenticated)]

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(APIError)
    async def api_error(request, error):
        return JSONResponse({"detail": error.code, "uncertain": error.uncertain}, status_code=502)

    @app.get("/healthz")
    def health(svc: Annotated[Service, Depends(service)]):
        svc.db.rows("SELECT 1")
        return {"status": "ok"}

    @app.post("/hooks/habitica/{token}", status_code=202)
    async def webhook(token: str, request: Request, svc: Annotated[Service, Depends(service)]):
        if not hmac.compare_digest(token.encode(), svc.cfg.webhook_token.get_secret_value().encode()):
            raise HTTPException(404, "Introuvable")
        try:
            body = json.loads(
                await request.body(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
            )
            if not isinstance(body, dict):
                raise ValueError
            return await run_in_threadpool(svc.ingest, body)
        except (ValueError, RecursionError):
            raise HTTPException(422, "Payload JSON invalide") from None

    @app.get("/api/status")
    def status(svc: Admin):
        return svc.status()

    @app.get("/api/events")
    def events(
        svc: Admin, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)
    ):
        return svc.db.rows("SELECT * FROM events ORDER BY received_at DESC LIMIT ? OFFSET ?", (limit, offset))

    @app.get("/api/audit-log")
    def audit_log(svc: Admin):
        return svc.db.rows("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100")

    @app.post("/api/mode")
    def mode(body: ModeChange, svc: Admin):
        svc.set_mode(body.mode)
        return {"mode": body.mode}

    @app.post("/api/companions/{name}/enabled")
    def enabled(name: str, body: EnabledChange, svc: Admin):
        svc.set_enabled(name, body.enabled)
        return {"name": name, "enabled": body.enabled}

    @app.post("/api/companions/{name}/audit")
    def audit(name: str, svc: Admin):
        return svc.audit(name)

    @app.post("/api/companions/{name}/prepare")
    def prepare(name: str, body: Prepare, svc: Admin):
        return svc.prepare(name, body.audit_id, body.create, body.acknowledge_conflicts)

    @app.get("/api/connection")
    def connection(svc: Admin):
        user = svc.api.request(svc.cfg.master, "GET", "user")
        if not isinstance(user, dict) or user.get("_id", user.get("id")) != svc.cfg.master.user_id:
            raise APIError("account_identity_mismatch")
        hooks = svc.api.request(svc.cfg.master, "GET", "user/webhook")
        # Ne jamais retourner l'URL du webhook qui contient son secret.
        return {
            "master_connected": True,
            "webhooks": [
                {k: h.get(k) for k in ("id", "type", "enabled", "failures", "lastFailureAt", "options")}
                for h in hooks
                if h.get("type") == "taskActivity"
            ],
        }

    @app.post("/api/operations/{op_id}/resolve")
    def resolve(op_id: int, body: Resolution, svc: Admin):
        svc.resolve(op_id, body.action, body.note, body.acknowledge_risk)
        return {"resolved": True}

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app


app = create_app()
