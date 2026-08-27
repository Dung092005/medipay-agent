from types import SimpleNamespace

from database.corpus.evaluate_retrieval_ablation import bounded_document_selector


def test_bounded_document_selector_limits_document_monopoly() -> None:
    points = [
        SimpleNamespace(payload={"document_id": "a"}),
        SimpleNamespace(payload={"document_id": "a"}),
        SimpleNamespace(payload={"document_id": "a"}),
        SimpleNamespace(payload={"document_id": "b"}),
        SimpleNamespace(payload={"document_id": "c"}),
    ]
    selected = bounded_document_selector(points, limit=4, max_per_document=2)
    assert [point.payload["document_id"] for point in selected] == ["a", "a", "b", "c"]
