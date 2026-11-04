"""Opérations d'installation explicites, jamais exécutées au démarrage."""

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, uuid5

from .config import load_settings
from .habitica import APIError, Habitica


def register_webhook(cfg, api, callback_base, update=False):
    parsed = urlsplit(callback_base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("L'URL de rappel doit être une origine HTTP(S) sans identifiants")
    hook_id = str(uuid5(NAMESPACE_URL, cfg.master.user_id + ":habitica-party-manager:v1"))
    body = {
        "id": hook_id,
        "type": "taskActivity",
        "label": "Habitica Party Manager",
        "url": callback_base.rstrip("/") + "/hooks/habitica/" + cfg.webhook_token.get_secret_value(),
        "enabled": True,
        "options": {
            "scored": True,
            "created": False,
            "updated": False,
            "deleted": False,
            "checklistScored": False,
        },
    }
    existing = api.request(cfg.master, "GET", "user/webhook")
    hook = next((h for h in existing if h.get("id") == hook_id), None)
    if hook:
        if all(hook.get(k) == body[k] for k in ("url", "enabled", "type", "options")):
            return {"id": hook_id, "status": "already_configured"}
        if not update:
            raise ValueError("Le webhook HPM existe mais diffère ; vérifier puis utiliser --update")
        del body["id"]
        api.request(cfg.master, "PUT", "user/webhook/" + hook_id, body)
    else:
        api.request(cfg.master, "POST", "user/webhook", body)
    return {"id": hook_id, "status": "configured"}


def backup(source, destination):
    source_path, destination_path = Path(source).resolve(), Path(destination).resolve()
    if source_path == destination_path or destination_path.exists():
        raise ValueError("Choisir une nouvelle destination de sauvegarde")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect("file:" + quote(str(source_path)) + "?mode=ro", uri=True) as src:
            with sqlite3.connect(destination_path) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Sauvegarde SQLite invalide")
    except BaseException:
        destination_path.unlink(missing_ok=True)
        raise
    destination_path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description="Installation et sauvegarde HPM")
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("register-webhook", help="Créer uniquement le webhook de Kaell")
    hook.add_argument("--callback-base", required=True)
    hook.add_argument("--update", action="store_true")
    snap = sub.add_parser("backup", help="Copie cohérente SQLite en ligne")
    snap.add_argument("--source", default="/data/hpm.sqlite3")
    snap.add_argument("--destination", required=True)
    args = parser.parse_args()
    try:
        if args.command == "backup":
            backup(args.source, args.destination)
            print(json.dumps({"status": "backup_verified"}))
            return
        cfg = load_settings()
        api = Habitica(cfg)
        try:
            print(json.dumps(register_webhook(cfg, api, args.callback_base, args.update)))
        finally:
            api.close()
    except (ValueError, APIError):
        # Aucune exception HTTP brute : elle pourrait contenir le secret de rappel.
        parser.exit(1, "Échec de l'opération ; vérifier configuration, accès et état du webhook HPM.\n")


if __name__ == "__main__":
    main()
