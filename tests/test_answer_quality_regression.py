import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.services.retrieval import policy_response, retrieval_intent

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads(
    (REPO_ROOT / "tests/fixtures/answer_quality_policy_cases.json").read_text(encoding="utf-8")
)
GOLDEN_PATH = REPO_ROOT / "data/eval/golden_bhxh_hoidap_v1.json"
REQUIRED_GOLDEN_FIELDS = {
    "id",
    "source_item_id",
    "question",
    "official_answer",
    "category",
    "answered_at",
    "source_url",
    "legal_basis",
    "temporal_risk",
    "review_status",
    "classification_reason",
}
RAW_PERSONAL_IDENTIFIER = re.compile(r"(?<!\d)\d{8,12}(?!\d)")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_answer_quality_route_contract(case):
    policy = policy_response(case["question"])
    if case["expected"] in {"social", "policy"}:
        assert policy
    else:
        assert policy is None
        assert retrieval_intent(case["question"]) == case["expected"]


def test_bhxh_golden_candidates_are_safe_review_inputs() -> None:
    payload = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    records = payload["records"]

    assert len(records) == 30
    for record in records:
        assert REQUIRED_GOLDEN_FIELDS <= record.keys()
        assert not RAW_PERSONAL_IDENTIFIER.search(record["question"])
        assert not RAW_PERSONAL_IDENTIFIER.search(record["official_answer"])
        assert record["review_status"] == "candidate_gold"


def test_answer_quality_regression_is_cwd_robust() -> None:
    with tempfile.TemporaryDirectory(prefix="answer-quality-cwd-") as outside_repo:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(Path(__file__).resolve()),
                "-q",
                "-k",
                "not test_answer_quality_regression_is_cwd_robust",
            ],
            cwd=outside_repo,
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
