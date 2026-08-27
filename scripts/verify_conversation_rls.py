#!/usr/bin/env python3
"""Disposable local RLS isolation smoke for owner-scoped conversations."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

import psycopg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())
    conversation = str(uuid.uuid4())
    turn = str(uuid.uuid4())
    with psycopg.connect(args.database_url, autocommit=False) as db:
        db.execute("INSERT INTO public.users(uid) VALUES (%s), (%s)", (owner, other))
        db.execute(
            "INSERT INTO public.conversations(conversation_id, owner_uid, title) VALUES (%s, %s, 'rls-smoke')",
            (conversation, owner),
        )
        db.execute(
            """
            INSERT INTO public.conversation_turns(
                turn_id, conversation_id, owner_uid, turn_index, user_message, assistant_response
            ) VALUES (%s, %s, %s, 1, 'q', 'a')
            """,
            (turn, conversation, owner),
        )
        db.commit()
    try:
        with psycopg.connect(args.database_url, autocommit=False) as db:
            db.execute("SET ROLE authenticated")
            db.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (owner,))
            owner_visible = int(db.execute("SELECT count(*) FROM public.conversation_turns").fetchone()[0])
            db.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (other,))
            other_visible = int(db.execute("SELECT count(*) FROM public.conversation_turns").fetchone()[0])
            db.rollback()
    finally:
        with psycopg.connect(args.database_url) as db:
            db.execute("DELETE FROM public.conversations WHERE conversation_id = %s", (conversation,))
            db.execute("DELETE FROM public.users WHERE uid IN (%s, %s)", (owner, other))
            db.commit()
    report = {"owner_visible": owner_visible, "other_visible": other_visible, "pass": owner_visible == 1 and other_visible == 0}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
