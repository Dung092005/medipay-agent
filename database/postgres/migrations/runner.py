"""Apply the ordered PostgreSQL migrations with a checksum and advisory lock.

This command is deliberately a one-shot operational job.  The API never calls
it during startup.  Existing installations can use ``--baseline`` after a
reviewed schema inventory; fresh installations should run ``database/postgres/schema.sql``
first and then the forward migrations.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATION_NAME = re.compile(r"^\d+_[a-z0-9_]+\.sql$")
LOCK_KEY = "medipay:postgres:migrations:v1"


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str


def migration_files(directory: Path) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not MIGRATION_NAME.fullmatch(path.name):
            continue
        content = path.read_bytes()
        migrations.append(Migration(path.stem, path, hashlib.sha256(content).hexdigest()))
    return migrations


def sync_database_url(value: str) -> str:
    """Convert SQLAlchemy's asyncpg URL to a psycopg-compatible URL."""
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", value.strip())


def migration_sql(path: Path) -> str:
    """Execute migration bodies inside the runner's single transaction."""
    sql = path.read_text(encoding="utf-8")
    sql = re.sub(r"^\s*begin\s*;", "", sql, count=1, flags=re.IGNORECASE)
    sql = re.sub(r";\s*commit\s*;\s*$", ";", sql, count=1, flags=re.IGNORECASE)
    return sql


def ensure_tracking_table(connection: psycopg.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version text PRIMARY KEY,
            checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def apply(
    connection: psycopg.Connection,
    migrations: list[Migration],
    *,
    baseline: bool = False,
    dry_run: bool = False,
) -> list[str]:
    """Apply or report migrations; fail closed on a changed applied file."""
    ensure_tracking_table(connection)
    applied = {
        str(row[0]): str(row[1])
        for row in connection.execute("SELECT version, checksum FROM public.schema_migrations").fetchall()
    }
    actions: list[str] = []
    for migration in migrations:
        previous = applied.get(migration.version)
        if previous is not None:
            if previous != migration.checksum:
                raise RuntimeError(f"Migration checksum changed: {migration.version}")
            actions.append(f"skip {migration.version}")
            continue
        if baseline:
            actions.append(f"baseline {migration.version}")
            if not dry_run:
                connection.execute(
                    "INSERT INTO public.schema_migrations(version, checksum) VALUES (%s, %s)",
                    (migration.version, migration.checksum),
                )
            continue
        actions.append(f"apply {migration.version}")
        if not dry_run:
            connection.execute(migration_sql(migration.path))
            connection.execute(
                "INSERT INTO public.schema_migrations(version, checksum) VALUES (%s, %s)",
                (migration.version, migration.checksum),
            )
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).parent)
    parser.add_argument("--database-url", default=os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", ""))
    parser.add_argument("--baseline", action="store_true", help="Record reviewed existing migrations without executing SQL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("MIGRATION_DATABASE_URL or DATABASE_URL is required")
    migrations = migration_files(args.directory)
    with psycopg.connect(sync_database_url(args.database_url), autocommit=False) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_KEY,))
        actions = apply(connection, migrations, baseline=args.baseline, dry_run=args.dry_run)
        if args.dry_run:
            connection.rollback()
        else:
            connection.commit()
    for action in actions:
        print(action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
