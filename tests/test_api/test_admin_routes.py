from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from src.api.auth import require_admin
from src.main import app


class Result:
    def __init__(self, rows=None, scalar=None):
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.scalar


class Session:
    def __init__(self):
        now = datetime.now(UTC)
        self.row = {
            "review_id": "review-1", "domain": "legal_document", "source_id": "doc-1",
            "title": "Luật BHYT", "status": "pending", "confidence": 0.9,
            "summary": "Cần kiểm tra", "payload": {}, "submitted_by": "pipeline",
            "assigned_to": "", "decision_note": "", "created_at": now,
            "updated_at": now, "decided_at": None,
        }

    async def execute(self, statement, _params=None):
        sql = str(statement)
        if "UPDATE review_queue_items" in sql:
            self.row["status"] = "accepted"
            self.row["assigned_to"] = "admin-1"
            self.row["decision_note"] = "đã kiểm tra"
            return Result(scalar="review-1")
        if "INSERT INTO review_audit_events" in sql:
            return Result()
        if "review_audit_events" in sql:
            return Result(rows=[])
        if "review_queue_items" in sql:
            return Result(rows=[self.row])
        raise AssertionError(sql)

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_admin_review_queue_reads_and_decides_with_database_contract(client):
    session = Session()

    @asynccontextmanager
    async def fake_scope():
        yield session

    async def admin_user():
        return {"uid": "admin-1", "role": "admin"}

    app.dependency_overrides[require_admin] = admin_user
    try:
        with patch("src.api.auth_routes.session_scope", fake_scope):
            listed = await client.get("/api/v1/auth/admin/reviews")
            decided = await client.patch(
                "/api/v1/auth/admin/reviews/review-1",
                json={"status": "accepted", "note": "đã kiểm tra"},
            )
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert listed.status_code == 200
    assert listed.json()[0]["review_id"] == "review-1"
    assert decided.status_code == 200
    assert decided.json()["status"] == "accepted"
