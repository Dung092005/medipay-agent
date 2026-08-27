from src.models.graph import RetrievalResult
from src.services.retrieval import (
    detect_jurisdiction,
    policy_response,
    route_retrieval_channels,
)


def _hit(
    chunk_id: str,
    *,
    scope: str,
    province: str = "",
    score: float = 0.8,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id=chunk_id,
        content=chunk_id,
        jurisdiction=scope,
        province=province,
        score=score,
        channels=["semantic"],
    )


def test_detect_jurisdiction_accepts_diacritic_and_unaccented_aliases():
    assert detect_jurisdiction("hỗ trợ tại Hà Nội") == "Hà Nội"
    assert detect_jurisdiction("thu tuc tai ha noi") == "Hà Nội"
    assert detect_jurisdiction("giá dịch vụ ở da nang") == "Đà Nẵng"
    assert detect_jurisdiction("dang ky kham tai TP HCM") == "Hồ Chí Minh"
    assert detect_jurisdiction("dang ky kham tai tphcm") == "Hồ Chí Minh"


def test_explicit_jurisdiction_keeps_central_and_target_before_other_locality():
    central = _hit("central", scope="Trung ương", score=0.82)
    hanoi = _hit("hanoi", scope="Địa phương", province="Hà Nội", score=0.70)
    danang = _hit("danang", scope="Địa phương", province="Đà Nẵng", score=0.98)

    routed = route_retrieval_channels(
        "hỗ trợ đăng ký khám chữa bệnh ban đầu tại Hà Nội",
        {"semantic": [danang, central, hanoi]},
    )

    assert [item.chunk_id for item in routed["semantic"]] == ["hanoi", "central", "danang"]


def test_national_route_prefers_central_over_local_hanoi():
    central = _hit("central", scope="Trung ương", score=0.76)
    hanoi = _hit("hanoi", scope="Địa phương", province="Hà Nội", score=0.96)

    routed = route_retrieval_channels(
        "Khám trái tuyến được hưởng BHYT như thế nào theo Luật?",
        {"semantic": [hanoi, central]},
    )

    assert routed["semantic"][0].chunk_id == "central"


def test_unspecified_local_support_question_defaults_to_hanoi():
    central = _hit("central", scope="Trung ương", score=0.80)
    hanoi = _hit("hanoi", scope="Địa phương", province="Hà Nội", score=0.72)

    routed = route_retrieval_channels(
        "mức hỗ trợ và đăng ký khám chữa bệnh ban đầu tại địa phương",
        {"semantic": [central, hanoi]},
    )

    assert routed["semantic"][0].chunk_id == "hanoi"


def test_social_policy_route_remains_unchanged():
    assert policy_response("xin chào")
