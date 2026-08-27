from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any

import httpx
from data_pipeline.api import create_app
from data_pipeline.api_models import (
    Category,
    DocumentResponse,
    RelationshipDirection,
    RelationshipItem,
    SearchHit,
    StatsResponse,
    TableCellResponse,
    TableResponse,
)
from data_pipeline.api_repository import (
    ActiveDataset,
    RelationshipPage,
    SearchPage,
    _document_response,
    _relationship_item,
    _search_hit,
)


class FakeEmbeddings:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return [0.1, 0.2, 0.3]


class FakeRepository:
    def __init__(self) -> None:
        self.active: ActiveDataset | None = ActiveDataset(
            dataset_id="r-test",
            dataset_version="dataset-sha-1",
            collection_name="legal_graph_chunks__r-test",
            manifest={
                "pipeline_version": "3.0.0",
                "embedding_model": "example/model",
                "embedding_dimensions": 3,
                "canonical_document_rows": 2,
                "external_document_stub_rows": 1,
                "chunk_rows": 4,
            },
            published_at=datetime(2026, 8, 7, tzinfo=UTC),
        )
        self.last_search: dict[str, Any] | None = None
        self.last_relationships: dict[str, Any] | None = None
        self.document = DocumentResponse(
            dataset_version="dataset-sha-1",
            id="doc-1",
            title="Quy định BHYT",
            so_ky_hieu="01/QĐ",
            node_kind="canonical",
            categories=["bhyt"],
            content_available=True,
            content_text="Nội dung",
        )

    def ping(self) -> bool:
        return True

    def current_dataset(self) -> ActiveDataset | None:
        return self.active

    def search(
        self,
        vector: list[float],
        *,
        category: Category | None,
        status: str | None,
        limit: int,
    ) -> SearchPage | None:
        if self.active is None:
            return None
        self.last_search = {
            "vector": vector,
            "category": category,
            "status": status,
            "limit": limit,
        }
        return SearchPage(
            dataset_version=self.active.dataset_version,
            hits=[
                SearchHit(
                    chunk_id="doc-1:00000",
                    document_id="doc-1",
                    score=0.91,
                    section_title="Điều 1",
                    text="Mức hưởng bảo hiểm y tế",
                    title="Quy định BHYT",
                )
            ],
        )

    def get_document(self, document_id: str, *, include_content: bool) -> DocumentResponse | None:
        if document_id != self.document.id:
            return None
        return self.document if include_content else self.document.model_copy(update={"content_text": None})

    def get_document_html(self, document_id: str) -> tuple[str, str, str] | None:
        if document_id != self.document.id:
            return None
        return "dataset-sha-1", "<p>Nội dung&nbsp;gốc</p>\n", "raw-hash"

    def get_legal_unit(self, unit_id: str) -> Any:
        return None

    def get_table(self, table_id: str, *, cell_limit: int) -> TableResponse | None:
        if table_id != "table-1":
            return None
        return TableResponse(
            dataset_version="dataset-sha-1", table_id="table-1", document_id="doc-1",
            table_ordinal=1, source_selector="table:nth-of-type(1)",
            source_fragment_sha256="fragment", table_text_sha256="text",
            row_count=2, column_count=2, extraction_version="v1",
            cells=[TableCellResponse(row_index=1, column_index=1, value="Mã")][:cell_limit],
        )

    def relationships(
        self,
        document_id: str,
        *,
        direction: RelationshipDirection,
        limit: int,
    ) -> RelationshipPage | None:
        if document_id != self.document.id:
            return None
        self.last_relationships = {"direction": direction, "limit": limit}
        return RelationshipPage(
            dataset_version="dataset-sha-1",
            items=[
                RelationshipItem(
                    edge_key="edge-1",
                    source_id="doc-1",
                    target_id="doc-2",
                    relationship_type="Căn cứ",
                )
            ],
        )

    def stats(self) -> StatsResponse | None:
        if self.active is None:
            return None
        return StatsResponse(
            dataset_version=self.active.dataset_version,
            canonical_nodes=2,
            external_nodes=1,
            content_rows=2,
            available_content=2,
            category_rows=2,
            relationship_rows=1,
            adverse_edges=0,
            chunk_rows=4,
        )


class AsgiClient:
    """Minimal synchronous test facade over HTTPX's ASGI transport."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.embeddings = FakeEmbeddings()
        self.client = AsgiClient(
            create_app(repository=self.repository, embeddings=self.embeddings, api_key="")
        )

    def test_health_and_current_dataset(self) -> None:
        live = self.client.get("/health/live")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "ok", "dataset_version": None})
        self.assertIn("X-Request-ID", live.headers)

        ready = self.client.get("/health/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["dataset_version"], "dataset-sha-1")

        response = self.client.get("/v1/datasets/current")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dataset_id"], "r-test")
        self.assertEqual(response.json()["counts"]["canonical_documents"], 2)

    def test_search_contract_and_validation(self) -> None:
        response = self.client.post(
            "/v1/search",
            json={
                "query": "  mức hưởng BHYT  ",
                "category": "bhyt",
                "status": " Còn hiệu lực ",
                "limit": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dataset_version"], "dataset-sha-1")
        self.assertEqual(response.json()["hits"][0]["document_id"], "doc-1")
        self.assertEqual(self.embeddings.queries, ["mức hưởng BHYT"])
        self.assertEqual(self.repository.last_search["category"], Category.BHYT)
        self.assertEqual(self.repository.last_search["status"], "Còn hiệu lực")

    def test_retrieve_returns_query_plan_and_channel_provenance(self) -> None:
        response = self.client.post("/v1/retrieve", json={"query": "mức hưởng BHYT", "limit": 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["query_plan"]["intent"], "eligibility")
        self.assertEqual(response.json()["hits"][0]["channel"], "semantic")
        self.assertIn("legal_graph_channel_unavailable", response.json()["warnings"])

        invalid = self.client.post("/v1/search", json={"query": "   ", "limit": 21})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["code"], "invalid_request")
        self.assertEqual(invalid.json()["request_id"], invalid.headers["X-Request-ID"])

    def test_document_content_is_opt_in_and_missing_is_stable_404(self) -> None:
        compact = self.client.get("/v1/documents/doc-1")
        self.assertEqual(compact.status_code, 200)
        self.assertIsNone(compact.json()["content_text"])

        full = self.client.get("/v1/documents/doc-1?include_content=true")
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.json()["content_text"], "Nội dung")

        html = self.client.get("/v1/documents/doc-1/html")
        self.assertEqual(html.status_code, 200)
        self.assertEqual(html.text, "<p>Nội dung&nbsp;gốc</p>\n")
        self.assertEqual(html.headers["x-raw-html-sha256"], "raw-hash")

    def test_table_endpoint_returns_cells_and_provenance(self) -> None:
        response = self.client.get("/v1/tables/table-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source_selector"], "table:nth-of-type(1)")
        self.assertEqual(response.json()["cells"][0]["value"], "Mã")

        missing = self.client.get("/v1/documents/not-found")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "document_not_found")
        self.assertNotIn("not-found", missing.json()["message"])

    def test_relationships_are_bounded_and_typed(self) -> None:
        response = self.client.get(
            "/v1/documents/doc-1/relationships?direction=outbound&limit=25"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["relationships"][0]["relationship_type"], "Căn cứ")
        self.assertEqual(self.repository.last_relationships["direction"], RelationshipDirection.OUTBOUND)
        self.assertEqual(self.repository.last_relationships["limit"], 25)

        invalid = self.client.get("/v1/documents/doc-1/relationships?limit=301")
        self.assertEqual(invalid.status_code, 422)

    def test_no_active_dataset_is_not_ready(self) -> None:
        self.repository.active = None
        ready = self.client.get("/health/ready")
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json()["status"], "not_ready")

        dataset = self.client.get("/v1/datasets/current")
        self.assertEqual(dataset.status_code, 503)
        self.assertEqual(dataset.json()["code"], "dataset_not_ready")

    def test_optional_api_key_protects_v1_but_not_health(self) -> None:
        secured = AsgiClient(
            create_app(repository=self.repository, embeddings=self.embeddings, api_key="secret")
        )
        self.assertEqual(secured.get("/health/live").status_code, 200)
        denied = secured.get("/v1/stats")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["code"], "unauthorized")
        allowed = secured.get("/v1/stats", headers={"X-API-Key": "secret"})
        self.assertEqual(allowed.status_code, 200)

    def test_dataset_payload_mapping_uses_canonical_metadata(self) -> None:
        document = _document_response(
            {
                "dataset_version": "v1",
                "id": "external-1",
                "title": "External",
                "is_external": True,
                "payload": {
                    "metadata": {
                        "so_ky_hieu": "02/QĐ",
                        "status_filter": "Còn hiệu lực",
                        "ngay_ban_hanh": "2026-08-07",
                        "resolution_status": "relationship_endpoint_only",
                    }
                },
                "content_text": None,
                "content_payload": None,
                "categories": [],
            }
        )
        self.assertEqual(document.so_ky_hieu, "02/QĐ")
        self.assertEqual(document.status_filter, "Còn hiệu lực")
        self.assertEqual(document.resolution_status, "relationship_endpoint_only")

        hit = _search_hit(
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "score": 0.8,
                "text": "text",
                "chunk_payload": {
                    "section_title": "legacy",
                    "unit_id": "legacy-unit",
                    "source_start": 99,
                    "source_end": 100,
                },
                "section_title": "Điều 2",
                "unit_id": "unit-2",
                "source_start": 10,
                "source_end": 20,
                "title": "Title",
                "is_external": False,
                "node_payload": {
                    "metadata": {"so_ky_hieu": "03/QĐ", "status_filter": "Hết hiệu lực"}
                },
            }
        )
        self.assertEqual(hit.so_ky_hieu, "03/QĐ")
        self.assertEqual(hit.status, "Hết hiệu lực")
        self.assertEqual(hit.section_title, "Điều 2")
        self.assertEqual(hit.unit_id, "unit-2")
        self.assertEqual(hit.source_start, 10)
        self.assertEqual(hit.source_end, 20)

        relationship = _relationship_item(
            {
                "edge_key": "edge",
                "source_id": "doc-1",
                "target_id": "doc-2",
                "relationship_type": "Bãi bỏ",
                "source_title": "",
                "target_title": "",
                "payload": {
                    "metadata": {
                        "relationship_is_adverse": True,
                        "source_title_raw": "Nguồn",
                        "target_title_raw": "Đích",
                    }
                },
            }
        )
        self.assertTrue(relationship.relationship_is_adverse)
        self.assertEqual(relationship.source_title, "Nguồn")


if __name__ == "__main__":
    unittest.main()
