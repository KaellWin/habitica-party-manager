#!/usr/bin/env python3
"""Préparer l'hôte Ubuntu. Exécution manuelle avec sudo, sans écraser l'existant."""

import argparse
import getpass
import os
import secrets
from pathlib import Path


def write_new(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w") as stream:
        stream.write(content + "\n")
    os.chown(path, 0, 10001)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/srv/habitica-party-manager")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, "Exécuter avec sudo sur votre serveur Ubuntu.\n")
    root = Path(args.root)
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(root, 0, 10001)
    for name in ("data", "config", "secrets"):
        folder = root / name
        folder.mkdir(mode=0o750, exist_ok=True)
        os.chown(folder, 10001 if name == "data" else 0, 10001)
    template = Path(__file__).resolve().parents[1] / "config/config.example.toml"
    config = root / "config/config.toml"
    if not config.exists():
        write_new(config, template.read_text())
    for name in ("admin_token", "webhook_token"):
        path = root / "secrets" / name
        if not path.exists():
            write_new(path, secrets.token_urlsafe(48))
    for name in ("kaell", "heal", "magus", "war"):
        path = root / "secrets" / (name + "_api_key")
        if path.exists():
            continue
        key = getpass.getpass(
            f"Clé API Habitica de {name} (saisie masquée, jamais envoyée au cloud) : "
        ).strip()
        if not key:
            parser.exit(1, "Clé vide : relancer pour terminer la préparation.\n")
        write_new(path, key)
    print("Configuration préparée. Vérifier base_url dans config.toml et le réseau dans Portainer.")
    print("Le jeton d'administration est dans secrets/admin_token ; ne pas le transmettre dans le chat.")


if __name__ == "__main__":
    main()
