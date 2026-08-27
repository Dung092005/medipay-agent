from pathlib import Path

from database.postgres.migrations.runner import migration_files, migration_sql, sync_database_url


def test_migration_files_are_ordered_and_checksummed(tmp_path: Path):
    (tmp_path / "20260102_second.sql").write_text("select 2;", encoding="utf-8")
    (tmp_path / "20260101_first.sql").write_text("select 1;", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")

    migrations = migration_files(tmp_path)

    assert [item.version for item in migrations] == ["20260101_first", "20260102_second"]
    assert len(migrations[0].checksum) == 64


def test_migration_sql_removes_nested_transaction_wrappers(tmp_path: Path):
    path = tmp_path / "20260101_first.sql"
    path.write_text("BEGIN;\nSELECT 1;\nCOMMIT;\n", encoding="utf-8")

    assert migration_sql(path).strip() == "SELECT 1;"


def test_sync_database_url():
    assert sync_database_url("postgresql+asyncpg://user:pass@host/db") == "postgresql://user:pass@host/db"
    assert sync_database_url("postgresql://user:pass@host/db") == "postgresql://user:pass@host/db"
