import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from .config import Account, Settings


class APIError(Exception):
    def __init__(self, code: str, *, retryable=False, uncertain=False, retry_after=0):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain
        self.retry_after = retry_after


def retry_after_seconds(value: str | None) -> float:
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class Habitica:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        # Le contexte système respecte SSL_CERT_FILE, y compris une CA privée montée en lecture seule.
        self.client = httpx.Client(
            base_url=settings.base_url + "/api/v3/",
            timeout=settings.request_timeout,
            follow_redirects=False,
            verify=ssl.create_default_context(),
            transport=transport,
        )

    def close(self):
        self.client.close()

    def request(self, account: Account, method: str, path: str, body=None):
        mutating = method != "GET"
        try:
            token = account.token()
        except ValueError:
            raise APIError("missing_api_key") from None
        try:
            response = self.client.request(
                method,
                path,
                json=body,
                headers={
                    "x-api-user": account.user_id,
                    "x-api-key": token,
                    "x-client": self.settings.master.user_id + "-habitica-party-manager",
                },
            )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise APIError("connection_unavailable", retryable=True) from None
        except httpx.HTTPError:
            raise APIError("transport_error", retryable=not mutating, uncertain=mutating) from None
        code = response.status_code
        if code == 429:
            raise APIError(
                "http_429",
                retryable=True,
                retry_after=retry_after_seconds(response.headers.get("Retry-After")),
            )
        if code >= 500:
            raise APIError(f"http_{code}", retryable=not mutating, uncertain=mutating)
        if not 200 <= code < 300:
            raise APIError(f"http_{code}")
        try:
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("success") is not True or "data" not in payload:
                raise ValueError
            return payload["data"]
        except (ValueError, TypeError):
            raise APIError("invalid_api_response", retryable=not mutating, uncertain=mutating) from None

    def tasks(self, account):
        data = self.request(account, "GET", "tasks/user")
        if not isinstance(data, list) or any(not isinstance(t, dict) for t in data):
            raise APIError("invalid_tasks_response")
        return data

    def task(self, account, task_id):
        return self.request(account, "GET", "tasks/" + quote(task_id, safe=""))

    @staticmethod
    def validate_habit(task, account):
        if not isinstance(task, dict):
            raise APIError("unsafe_technical_habit")
        challenge, group = task.get("challenge") or {}, task.get("group") or {}
        if (
            not isinstance(task, dict)
            or task.get("type") != "habit"
            or task.get("up") is not True
            or task.get("down") is not False
            or not (task.get("id") or task.get("_id"))
            or task.get("userId", account.user_id) != account.user_id
            or not isinstance(challenge, dict)
            or not isinstance(group, dict)
            or challenge.get("id")
            or group.get("id")
        ):
            raise APIError("unsafe_technical_habit")

    def create_habit(self, account):
        cfg = self.settings
        task = self.request(
            account,
            "POST",
            "tasks/user",
            {
                "type": "habit",
                "text": cfg.technical_title,
                "alias": cfg.technical_alias,
                "notes": "Gérée par Habitica Party Manager. Une validation positive par action de Kaell.",
                "up": True,
                "down": False,
                "priority": cfg.habit_priority,
                "frequency": cfg.habit_reset,
            },
        )
        self.validate_habit(task, account)
        return task

    def score_up(self, account, task_id):
        # Direction fixée ici ; l'API interne ne propose aucune opération négative.
        return self.request(account, "POST", "tasks/" + quote(task_id, safe="") + "/score/up")
