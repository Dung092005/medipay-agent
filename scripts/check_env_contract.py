#!/usr/bin/env python3
"""Validate environment presence without printing secret values."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import dotenv_values

PUBLIC_FIREBASE_FIELDS = (
    "NEXT_PUBLIC_FIREBASE_API_KEY",
    "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN",
    "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
    "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET",
    "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID",
    "NEXT_PUBLIC_FIREBASE_APP_ID",
)


def _value(values: dict[str, str | None], name: str) -> str:
    value = str(values.get(name) or os.getenv(name) or "").strip()
    return "" if value.startswith("<") and value.endswith(">") else value


def _firebase_admin_valid(values: dict[str, str | None]) -> bool:
    raw = _value(values, "FIREBASE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return bool(_value(values, "GOOGLE_APPLICATION_CREDENTIALS"))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and all(
        str(parsed.get(field, "")).strip()
        for field in ("type", "project_id", "client_email", "private_key")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--mode", choices=("local", "production"), default="local")
    args = parser.parse_args()

    if not args.env_file.is_file():
        print(f"Missing {args.env_file}; copy .env.example to .env first.")
        return 2

    values = dotenv_values(args.env_file)
    missing: list[str] = []
    missing.extend(name for name in PUBLIC_FIREBASE_FIELDS if not _value(values, name))

    if args.mode == "local":
        for name in ("OPENAI_API_KEY", "MODEL_NAME"):
            if not _value(values, name):
                missing.append(name)
    else:
        required = (
            "RUNTIME_DATABASE_URL",
            "DATABASE_URL",
            "QDRANT_URL",
            "QDRANT_API_KEY",
            "NEO4J_URI",
            "NEO4J_PASSWORD",
            "OPENAI_API_KEY",
            "MODEL_NAME",
            "METRICS_TOKEN",
        )
        for name in required:
            if name in {"RUNTIME_DATABASE_URL", "DATABASE_URL"}:
                continue
            if not _value(values, name):
                missing.append(name)
        if not (_value(values, "RUNTIME_DATABASE_URL") or _value(values, "DATABASE_URL")):
            missing.append("RUNTIME_DATABASE_URL/DATABASE_URL")
        if not _firebase_admin_valid(values):
            missing.append("FIREBASE_SERVICE_ACCOUNT_JSON/GOOGLE_APPLICATION_CREDENTIALS")
        origins = [item.strip() for item in _value(values, "CORS_ORIGINS").split(",") if item.strip()]
        if not origins or any(not item.startswith("https://") for item in origins) or "*" in origins:
            missing.append("CORS_ORIGINS (explicit HTTPS origins)")

    if missing:
        print(json.dumps({"pass": False, "mode": args.mode, "missing": sorted(set(missing))}))
        return 1

    print(json.dumps({"pass": True, "mode": args.mode, "secret_values_logged": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
