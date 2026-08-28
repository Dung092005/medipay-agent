"""Deterministic planning, safety routing and channel fusion for Legal RAG."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from src.models.graph import RetrievalResult

# Vietnamese instruments use both year-qualified signatures
# (``60/2026/NQ-HĐND``) and abbreviated administrative forms such as
# ``11/CT.UBND`` or ``15/TTLB``. Keep dates out by requiring an alphabetic
# token after the slash in the abbreviated branch.
_DOCUMENT_NUMBER = re.compile(
    # ``Ð`` is present in a subset of legacy CSV signatures while the
    # normalized runtime representation uses Vietnamese ``Đ``.  Accept both
    # at extraction time; ``normalize_identifier`` canonicalizes them before
    # lookup.
    r"\b\d{1,6}/\d{4}/[A-ZĐÐ0-9][A-ZĐÐ0-9./-]*\b"
    r"|\b\d{1,6}/[A-ZĐÐ][A-ZĐÐ0-9./-]*\b",
    re.IGNORECASE,
)
_LEGAL_LABEL = re.compile(r"\b(?:điều|khoản)\s+\d+[a-zđ]?|\b[a-zđ]\)", re.IGNORECASE)
_INTERNAL_REFERENCE = re.compile(
    r"\bđiểm\s+([a-zđ])\s+khoản\s+(\d+)\s+điều\s+(\d+)\b",
    re.IGNORECASE,
)
_RETRIEVAL_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.IGNORECASE)
_RETRIEVAL_STOPWORDS = {
    "và", "là", "có", "được", "cho", "của", "theo", "trong", "với", "từ", "này",
    "khi", "để", "một", "các", "những", "về", "không", "người", "việc", "tại", "đến",
    "thì", "bị", "sẽ", "đã", "hay", "hoặc", "nếu", "cần", "phải", "nên", "gì",
}
_GREETING_PATTERN = re.compile(
    r"^(?:xin\s+)?chào(?:\s+(?:bạn|mọi\s+người|ad|bot|em|anh|chị|nhé|nha|ạ|ơi|nhe))*$|"
    r"^(?:hi|hello|hey|alo)(?:\s+(?:bạn|mọi\s+người|ad|bot|em|anh|chị|nhé|nha|ạ|ơi))*$",
    re.IGNORECASE,
)
_THANKS_PATTERN = re.compile(
    r"^(?:cảm\s+ơn|cám\s+ơn|thank\s+you|thanks)(?:\s+(?:bạn|ad|bot|em|anh|chị|nhé|nha|ạ|nhiều))*$",
    re.IGNORECASE,
)
_BYE_PATTERN = re.compile(
    r"^(?:tạm\s+biệt|bye|goodbye)(?:\s+(?:bạn|ad|bot|em|anh|chị|nhé|nha|ạ))*$",
    re.IGNORECASE,
)


def normalize_identifier(value: str) -> str:
    """Normalize legacy Vietnamese character corruption in legal signatures."""
    return value.replace("Ð", "Đ").replace("ð", "đ").strip().upper()


def extract_document_numbers(query: str) -> list[str]:
    return sorted({normalize_identifier(match.group(0)) for match in _DOCUMENT_NUMBER.finditer(query)})


def extract_legal_labels(query: str) -> list[str]:
    return sorted({match.group(0) for match in _LEGAL_LABEL.finditer(query)}, key=str.casefold)


def retrieval_intent(query: str) -> str:
    lowered = query.casefold()
    prose_without_signatures = _DOCUMENT_NUMBER.sub("", query)
    if any(
        token in lowered
        for token in (
            "hiệu lực", "hiện hành", "hiện nay", "từ ngày", "trước ngày",
            "thay thế", "bãi bỏ",
        )
    ) or re.search(r"\b(?:19|20)\d{2}\b", prose_without_signatures):
        return "temporal"
    if any(token in lowered for token in ("liên quan", "dẫn chiếu", "sửa đổi", "căn cứ")):
        return "relational"
    if _LEGAL_LABEL.search(query):
        return "legal_unit"
    if extract_document_numbers(query):
        return "lookup"
    return "thematic"


def decompose_query(query: str, *, limit: int = 3) -> list[str]:
    """Create a small deterministic set of independent sub-queries.

    This is deliberately conservative: only explicit conjunctions are split,
    and each fragment must retain enough content to be a meaningful retrieval
    request.  It feeds the bounded embedding/Qdrant batch path; final answer
    generation remains single-user and unbatched.
    """
    normalized = " ".join(query.split())
    if not normalized or limit <= 1:
        return [normalized] if normalized else []
    parts = re.split(r"\s+(?:và|đồng thời|cũng như)\s+", normalized, flags=re.IGNORECASE)
    if len(parts) <= 1:
        return [normalized]
    selected = [part.strip(" ,;:.?") for part in parts if len(part.strip()) >= 12]
    if len(selected) < 2:
        return [normalized]
    return selected[: max(2, min(limit, 3))]


def requires_clause_expansion(query: str) -> bool:
    """Whether a question may need neighbouring units of a legal provision.

    This is deliberately a language-level question detector, not a list of
    insurance benefits, payment concepts, or expected answers.  The expanded
    units remain candidates and must still pass source-derived ranking.
    """
    normalized = " ".join(query.casefold().split())
    return bool(
        re.search(
            r"\b(?:bao\s+nhiêu|bao\s+lâu|khi\s+nào|từ\s+khi\s+nào|trường\s+hợp\s+nào|điều\s+kiện\s+nào|quyền\s+lợi\s+gì|mức\s+hưởng|được\s+hưởng|có\s+được)\b|\bcó\b[\s\S]{0,100}\bkhông\b|%",
            normalized,
        )
    )


def extract_query_phrases(query: str, *, limit: int = 6) -> list[str]:
    """Return bounded query-derived phrases for intra-document recall.

    No domain vocabulary is maintained here: phrases are contiguous content
    tokens from the user's own wording.  Longer phrases rank ahead because
    they are normally more selective in a legal corpus.
    """
    tokens = [
        token.casefold()
        for token in _RETRIEVAL_TOKEN.findall(query)
        if len(token) > 1 and token.casefold() not in _RETRIEVAL_STOPWORDS
    ]
    phrases = [" ".join(tokens[index : index + width]) for width in (3, 2) for index in range(len(tokens) - width + 1)]
    return list(dict.fromkeys(phrases))[: max(0, limit)]


def extract_query_terms(query: str, *, limit: int = 24) -> list[str]:
    """Return bounded, non-stopword tokens from the user's wording."""
    return list(
        dict.fromkeys(
            token.casefold()
            for token in _RETRIEVAL_TOKEN.findall(query)
            if len(token) > 2 and token.casefold() not in _RETRIEVAL_STOPWORDS
        )
    )[: max(0, limit)]


def exclude_unverified_legacy_subordinate_sources(
    query: str, hits: Sequence[RetrievalResult]
) -> list[RetrievalResult]:
    """Do not treat an old administrative reproduction as current law.

    A decision, circular, or guidance document can quote an older statutory
    rule verbatim.  That is useful research context, but it is not adequate
    public authority for a present-day entitlement question when its legal
    status is not tied to a retained official source.  Explicit document
    lookups and clearly historical questions are deliberately preserved.
    """
    if not hits or extract_document_numbers(query):
        return list(hits)
    lowered = query.casefold()
    if any(marker in lowered for marker in ("trước ngày", "vào năm", "lịch sử", "thời điểm đó")):
        return list(hits)

    retained: list[RetrievalResult] = []
    for item in hits:
        years = [
            int(value)
            for value in re.findall(
                r"\b(?:19|20)\d{2}\b",
                " ".join((item.issued_date, item.effective_from, item.document_number, item.title)),
            )
        ]
        instrument = " ".join((item.document_type, item.title)).casefold()
        subordinate = any(marker in instrument for marker in ("quyết định", "thông tư", "công văn", "hướng dẫn"))
        if subordinate and max(years, default=0) < 2024 and not item.legal_status_verified:
            continue
        retained.append(item)
    return retained


def scope_evidence_matches_query(
    query: str, item: RetrievalResult, *, candidate_pool: Sequence[RetrievalResult]
) -> bool:
    """Require scoped child evidence to carry a query-specific concept.

    Scope expansion makes nearby clauses recallable, but a broad parent must
    not make an unrelated sibling look relevant.  Specificity is calculated
    dynamically from the candidate pool: a query term occurring in no more
    than half of candidates is informative for this request.  There is no
    domain vocabulary or question-to-answer mapping here.
    """
    query_tokens = [token.casefold() for token in _RETRIEVAL_TOKEN.findall(query)]
    query_terms = {
        token for token in query_tokens
        if len(token) >= 4 and token not in _RETRIEVAL_STOPWORDS
    }
    if not query_terms:
        return True
    pool = list(candidate_pool) or [item]
    query_phrases = set(zip(query_tokens, query_tokens[1:], query_tokens[2:]))

    def phrases(candidate: RetrievalResult) -> set[tuple[str, str, str]]:
        tokens = [
            token.casefold()
            for token in _RETRIEVAL_TOKEN.findall(f"{candidate.section_title} {candidate.content}")
        ]
        return set(zip(tokens, tokens[1:], tokens[2:]))

    # Exact three-token overlap is a stronger and entirely query-derived
    # signal than a single legal word.  If any scope candidate has such a
    # phrase, only those candidates are allowed through.
    phrase_matches = {candidate.chunk_id: bool(query_phrases & phrases(candidate)) for candidate in pool}
    if any(phrase_matches.values()):
        return phrase_matches.get(item.chunk_id, False)

    # If the corpus contains no phrase match, use dynamic term selectivity as
    # a recall-oriented fallback rather than encoding a domain word list.
    term_frequency = {
        term: sum(
            term in {
                token.casefold()
                for token in _RETRIEVAL_TOKEN.findall(f"{candidate.section_title} {candidate.content}")
            }
            for candidate in pool
        )
        for term in query_terms
    }
    distinctive = {term for term in query_terms if term_frequency[term] * 2 <= len(pool)}
    if not distinctive:
        return True
    source_terms = {
        token.casefold()
        for token in _RETRIEVAL_TOKEN.findall(f"{item.section_title} {item.content}")
    }
    return bool(distinctive & source_terms)


def extract_internal_legal_references(values: Sequence[str]) -> list[str]:
    """Return canonical intra-document legal references found in source text.

    A current implementing decree often names the beneficiary in one clause
    and gives the percentage in another clause by referring to the same
    ``điểm/khoản/Điều``.  This extracts only citations already present in
    canonical evidence; it never manufactures a legal reference from a user
    query or model output.
    """
    found: list[str] = []
    for value in values:
        for match in _INTERNAL_REFERENCE.finditer(value):
            reference = " ".join(
                ("điểm", match.group(1).casefold(), "khoản", match.group(2), "Điều", match.group(3))
            )
            if reference not in found:
                found.append(reference)
    return found


def is_metadata_question(query: str) -> bool:
    lowered = query.casefold()
    return bool(extract_document_numbers(query)) and any(
        token in lowered
        for token in (
            "tiêu đề", "tên văn bản", "tên đầy đủ", "tên của", "hiệu lực", "ban hành",
            "loại văn bản", "danh mục", "category", "tình trạng", "số ký hiệu", "số hiệu",
            "thuộc nhóm", "nhóm nội dung",
        )
    )


def is_simple_status_metadata_question(query: str) -> bool:
    """Allow exact metadata answers for a direct status/date lookup only.

    Comparative, temporal-chain and relational questions still go through
    lexical/semantic/graph fusion; this helper is intentionally narrow so a
    metadata shortcut cannot hide a needed graph relation.
    """
    lowered = query.casefold()
    if not is_metadata_question(query):
        return False
    if not any(token in lowered for token in ("hiệu lực", "tình trạng", "ban hành")):
        return False
    complex_markers = (
        "thay thế", "bãi bỏ", "sửa đổi", "dẫn chiếu", "liên quan", "trước ngày",
        "sau ngày", "từ ngày nào đến", "so sánh", "mốc thời gian",
    )
    return not any(marker in lowered for marker in complex_markers)


def policy_response(query: str) -> str | None:
    """Return a deterministic safe response before any external retrieval."""
    lowered = query.casefold()
    social = " ".join(re.sub(r"[^0-9a-zà-ỹđ]+", " ", lowered).split())
    if _GREETING_PATTERN.match(social):
        return "Xin chào! Tôi có thể hỗ trợ bạn tra cứu thông tin BHYT và viện phí."
    if _THANKS_PATTERN.match(social):
        return "Rất vui được hỗ trợ bạn."
    if _BYE_PATTERN.match(social):
        return "Tạm biệt bạn. Khi cần hỗ trợ thông tin BHYT và viện phí, tôi luôn sẵn sàng hỗ trợ!"
    if any(token in lowered for token in ("bỏ qua hướng dẫn", "ignore previous", "system prompt", "prompt nội bộ")):
        return (
            "Tôi không thể thực hiện yêu cầu thay đổi quy tắc vận hành hoặc tiết lộ "
            "thông tin bảo mật hoặc thông tin bí mật. Tôi chỉ hỗ trợ câu hỏi BHYT và viện phí dựa trên nguồn hợp lệ."
        )
    if any(token in lowered for token in ("otp", "cvv", "mật khẩu")):
        return (
            "Tôi không tiếp nhận hoặc lặp lại OTP, CVV hay mật khẩu. Không lưu các secret này; hãy dùng "
            "kênh thanh toán an toàn và chính thức."
        )
    if any(token in lowered for token in ("số thẻ", "cccd", "hồ sơ bệnh án", "bệnh án của")):
        return (
            "Tôi không thể cung cấp hồ sơ hoặc số thẻ của người khác. Cần xác minh danh tính và quyền đại diện "
            "trước khi cung cấp dữ liệu; tôi chỉ có thể hướng dẫn quy trình chung đã ẩn thông tin cá nhân."
        )
    if any(token in lowered for token in ("kê đơn", "uống thuốc", "chẩn đoán bệnh", "liều thuốc")):
        return "Tôi không thể chẩn đoán hoặc kê đơn. Với triệu chứng hay liều dùng, hãy liên hệ bác sĩ hoặc cơ sở y tế; tôi chỉ hỗ trợ thông tin chính sách và viện phí có nguồn."
    if any(token in lowered for token in ("claim đã được duyệt", "yêu cầu đã được duyệt", "đã được phê duyệt")):
        return "Tôi không thể xác nhận tình trạng phê duyệt yêu cầu thanh toán khi không có thông báo chính thức. Hãy kiểm tra kênh của cơ quan bảo hiểm hoặc cơ sở tiếp nhận."
    # A question about a person's own plan cannot be answered safely without
    # their plan/coverage facts.  Do not treat every general legal question
    # containing "quyền lợi" as an individual-eligibility request: that used
    # to bypass retrieval entirely for questions such as the statutory BHYT
    # five-consecutive-years co-payment rule.
    personal_plan_markers = (
        "gói bảo hiểm này", "gói của tôi", "gói bhyt của tôi",
        "quyền lợi của tôi", "tôi còn được hưởng", "tôi được hưởng",
    )
    if any(marker in lowered for marker in personal_plan_markers):
        return "Để xác định quyền lợi, cần tên hoặc mã gói bảo hiểm/văn bản áp dụng và ngày điều trị hoặc ngày hiệu lực. Tôi không thể khẳng định quyền lợi khi thiếu các thông tin này."
    if "viện phí" in lowered and any(token in lowered for token in ("bao nhiêu", "ước tính", "tổng tiền", "tính", "cuối cùng")):
        return "Để đối chiếu viện phí an toàn, cần hóa đơn hoặc bảng kê chi tiết, nơi khám, tuyến/chuyển tuyến, mức hưởng BHYT và thời điểm điều trị. Không nên kết luận số tiền khi thiếu các đầu vào này."
    return None


def no_answer_response(query: str = "", *, reason: str = "no_evidence") -> str:
    """Return a stable abstention that explains why a claim was not made."""
    if reason == "ambiguous":
        return (
            "Tôi tìm thấy nhiều văn bản có thể phù hợp nhưng chưa thể xác định đúng văn bản. "
            "Vui lòng cung cấp số hiệu đầy đủ, cơ quan ban hành hoặc ngày hiệu lực."
        )
    if reason == "unverified":
        return (
            "Tôi chưa thể xác minh nội dung này từ nguồn chính thức có trích dẫn hợp lệ; "
            "vì vậy chưa thể đưa ra kết luận pháp lý."
        )
    return (
        "Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp "
        "để giải đáp câu hỏi này."
    )


def requires_evidence_verification(query: str) -> bool:
    """Identify claims where unsupported legal advice is especially harmful."""
    lowered = query.casefold()
    return any(token in lowered for token in (
        "hiệu lực", "còn hiệu lực", "hết hiệu lực", "bãi bỏ", "thay thế",
        "mức hưởng", "mức chi trả", "được chi trả", "được hưởng",
        "có được", "bao nhiêu tiền", "thanh toán", "hiện nay", "hiện hành",
    ))


def semantic_document_focus(
    hits: Sequence[RetrievalResult], *, documents: int = 1, chunks_per_document: int = 3
) -> list[RetrievalResult]:
    """Keep answer-bearing neighbouring passages from the strongest semantic document.

    A question can match both a document's scope and its operative clause.  Pure
    passage-level fusion may retain the scope but discard the clause because it
    diversifies too aggressively across documents.  This creates a compact
    second channel from documents that receive multiple independent semantic
    hits, without another embedding or Qdrant request.
    """
    if documents <= 0 or chunks_per_document <= 0:
        return []
    grouped: defaultdict[str, list[tuple[int, RetrievalResult]]] = defaultdict(list)
    for rank, hit in enumerate(hits, start=1):
        if hit.document_id:
            grouped[hit.document_id].append((rank, hit))
    candidates = [
        (
            -sum(float(hit.score) for _, hit in rows[:chunks_per_document]),
            rows[0][0],
            document_id,
            rows,
        )
        for document_id, rows in grouped.items()
        if len(rows) >= 2
    ]
    focused: list[RetrievalResult] = []
    for _, _, _, rows in sorted(candidates)[:documents]:
        focused.extend(hit.model_copy(deep=True) for _, hit in rows[:chunks_per_document])
    return focused


def rerank_legal_candidates(
    query: str, hits: Sequence[RetrievalResult]
) -> list[RetrievalResult]:
    """Re-rank candidates from any retrieval channel with one legal policy.

    Dense and lexical channels have incomparable raw scores, so this function
    is applied inside each channel before rank fusion. It combines query
    coverage with verified currentness and source authority without any
    question-to-document mapping or answer-specific rule.
    """
    tokens = [
        token.casefold() for token in _RETRIEVAL_TOKEN.findall(query)
        if len(token) > 1 and token.casefold() not in _RETRIEVAL_STOPWORDS
    ]
    unique_tokens = list(dict.fromkeys(tokens))
    phrases = list(dict.fromkeys(zip(tokens, tokens[1:])))
    triples = list(dict.fromkeys(zip(tokens, tokens[1:], tokens[2:])))
    if not unique_tokens:
        return [item.model_copy(deep=True) for item in hits]

    query_years = {int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", query)}
    current_year = date.today().year
    asks_current = any(
        marker in query.casefold() for marker in ("hiện nay", "hiện hành", "mới nhất")
    ) or any(year >= current_year for year in query_years) or requires_evidence_verification(query)
    # A date is not automatically a historical question: “mức đóng năm
    # 2026” asks for the current/future regime. Only a clearly older date (or
    # explicit historical wording) permits obsolete sources to compete on an
    # equal footing.
    historical_query = (bool(query_years) and max(query_years) < current_year and not asks_current) or any(
        marker in query.casefold()
        for marker in ("trước ngày", "tại ngày", "vào năm", "thời điểm đó", "lịch sử")
    )
    ranked: list[RetrievalResult] = []
    query_terms = set(unique_tokens)
    triple_frequency: defaultdict[tuple[str, str, str], int] = defaultdict(int)
    if triples:
        for candidate in hits:
            candidate_tokens = [
                token.casefold()
                for token in _RETRIEVAL_TOKEN.findall(f"{candidate.section_title} {candidate.content}")
            ]
            for triple in set(zip(candidate_tokens, candidate_tokens[1:], candidate_tokens[2:])) & set(triples):
                triple_frequency[triple] += 1
    for original in hits:
        item = original.model_copy(deep=True)
        passage_tokens = [
            token.casefold()
            for token in _RETRIEVAL_TOKEN.findall(
                " ".join((item.section_title, item.content))
            )
        ]
        metadata_tokens = [
            token.casefold()
            for token in _RETRIEVAL_TOKEN.findall(
                " ".join(
                    (
                        item.title,
                        item.document_number,
                        item.document_type,
                        item.issuer,
                        item.jurisdiction,
                    )
                )
            )
        ]
        passage_terms = set(passage_tokens)
        metadata_terms = set(metadata_tokens)
        token_coverage = len(query_terms & passage_terms) / len(query_terms)
        metadata_coverage = len(query_terms & metadata_terms) / len(query_terms)
        source_phrases = set(zip(passage_tokens, passage_tokens[1:]))
        source_triples = set(zip(passage_tokens, passage_tokens[1:], passage_tokens[2:]))
        phrase_coverage = (
            sum(phrase in source_phrases for phrase in phrases) / len(phrases)
            if phrases else 0.0
        )
        # Exact multi-token matches are high-precision legal anchors. Their
        # strength is derived from inverse frequency within this candidate
        # pool, so a rare operative phrase outranks a generic phrase without
        # encoding any domain vocabulary.
        phrase_specificity = min(
            1.0,
            sum(1.0 / triple_frequency[triple] for triple in set(triples) & source_triples),
        )
        raw_score = float(item.score)
        # Repository operative scans score by the number of matching query
        # terms. That raw count is useful for recall but rewards verbose,
        # generic administrative passages over a short governing statute.
        # Keep it bounded before applying source authority/currentness so the
        # legal ranker, rather than term-count magnitude, decides the winner.
        if any(channel in item.channels for channel in ("document_operatives", "document_recall_operatives")):
            raw_score = min(raw_score, 2.0)
        # Coverage is a bounded tie-breaker over semantic relevance, not an
        # independent legal conclusion.  It prevents a single generic term
        # from dominating a multi-condition query.
        status_bonus = (
            0.04
            if item.legal_status_verified and item.legal_status.casefold().startswith("còn hiệu lực")
            else 0.0
        )
        status_penalty = 0.0
        known_status = item.legal_status.casefold()
        # Status imported from a curated corpus can be stale or describe a
        # partial amendment instead of the particular provision.  It must not
        # demote a newer law unless the status is backed by the retained
        # official status source.  Authority and date are still useful
        # ranking signals for unverified source records.
        if (
            item.legal_status_verified
            and not historical_query
            and any(marker in known_status for marker in ("hết hiệu lực", "bãi bỏ", "thay thế"))
        ):
            status_penalty = -0.45
        year_values = [
            int(value)
            for value in re.findall(
                r"\b(?:19|20)\d{2}\b",
                " ".join((item.issued_date, item.effective_from, item.document_number, item.title)),
            )
        ]
        publication_year = max(year_values, default=0)
        recency_bonus = 0.0
        if not historical_query and publication_year:
            recency_bonus = 0.20 * max(0.0, min(1.0, (publication_year - 1990) / 40))
        # For a question explicitly about the current regime, an old document
        # whose status cannot be tied to an official source is still useful
        # historical context, but cannot compete with an official current
        # source merely because it repeats more query words.  Explicit
        # document-number lookups are exempt: those ask about that document.
        currentness_penalty = 0.0
        if asks_current and not extract_document_numbers(query):
            if not item.legal_status_verified and publication_year and publication_year < 2024:
                currentness_penalty = -0.22
            elif not item.legal_status_verified:
                currentness_penalty = -0.08
            # An old source that claims to be active can still be relevant,
            # but should not displace a newer primary law/decree for a query
            # explicitly about the present regime. This is a rank penalty,
            # not an exclusion, so a genuinely still-operative old rule can
            # remain available when no newer source answers the question.
            elif publication_year and publication_year < 2024:
                currentness_penalty = -0.16
        authority = " ".join((item.document_type, item.title)).casefold()
        authority_bonus = 0.0
        title = item.title.strip().casefold()
        document_type = item.document_type.strip().casefold()
        if "luật" in document_type or title.startswith(("luật ", "bộ luật ")):
            authority_bonus = 0.35
        elif "nghị định" in document_type or title.startswith("nghị định"):
            authority_bonus = 0.25
        elif "văn bản hợp nhất" in authority:
            authority_bonus = 0.20
        elif any(value in authority for value in ("thông tư", "nghị quyết")):
            authority_bonus = 0.12
        elif "quyết định" in authority:
            authority_bonus = 0.05
        rerank_score = (
            raw_score
            + 0.16 * token_coverage
            + 0.10 * phrase_coverage
            + 0.45 * phrase_specificity
            + 0.03 * metadata_coverage
            + status_bonus
            + status_penalty
            + recency_bonus
            + authority_bonus
            + currentness_penalty
        )
        item.score = rerank_score
        item.rank_details = {
            **item.rank_details,
            "semantic_raw_score": raw_score,
            "query_token_coverage": token_coverage,
            "query_phrase_coverage": phrase_coverage,
            "query_phrase_specificity": phrase_specificity,
            "metadata_token_coverage": metadata_coverage,
            "semantic_rerank_score": rerank_score,
            "current_status_bonus": status_bonus,
            "superseded_status_penalty": status_penalty,
            "recency_bonus": recency_bonus,
            "authority_bonus": authority_bonus,
            "currentness_penalty": currentness_penalty,
        }
        ranked.append(item)
    return sorted(ranked, key=lambda item: (-item.score, item.document_id, item.chunk_id))


# Backward-compatible import for evaluation scripts while callers migrate to
# the channel-agnostic name above.
rerank_semantic_by_query_overlap = rerank_legal_candidates


def weighted_rrf(
    channel_hits: dict[str, Sequence[RetrievalResult]],
    *,
    limit: int,
    max_per_document: int = 2,
    channel_weights: dict[str, float] | None = None,
) -> list[RetrievalResult]:
    """Fuse channel ranks while preserving raw scores and evidence provenance."""
    if max_per_document <= 0:
        return []
    weights = {
        "exact": 2.0,
        "lexical": 1.15,
        "semantic": 1.0,
        "semantic_focus": 1.35,
        "semantic_scope": 1.6,
        "legal_reference": 1.5,
        "document_operatives": 1.45,
        "title_document_operatives": 1.55,
        # Candidate documents are recalled independently from the passage
        # ANN/BM25 lists. Their passages have already passed a minimum
        # query-derived term match and the shared legal reranker, so this
        # channel can rescue a concise operative clause without making a
        # document title itself evidence.
        # A document-bounded exact passage is stronger evidence than a
        # corpus-wide semantic hit. Give it enough rank weight to survive
        # fusion when the clause appears only in the lexical view.
        "document_recall_operatives": 5.0,
        "document_recall_semantic": 1.7,
        "document_anchor": 3.5,
        "legal_graph": 0.7,
        "page_index": 1.35,
    }
    if channel_weights:
        weights.update(channel_weights)
    aggregate: defaultdict[str, float] = defaultdict(float)
    selected: dict[str, RetrievalResult] = {}
    details: defaultdict[str, dict[str, float]] = defaultdict(dict)
    for channel, hits in channel_hits.items():
        weight = weights.get(channel, 1.0)
        for rank, hit in enumerate(hits, start=1):
            aggregate[hit.chunk_id] += weight / (60 + rank)
            details[hit.chunk_id][f"{channel}_rank"] = float(rank)
            details[hit.chunk_id][f"{channel}_raw_score"] = float(hit.score)
            existing = selected.get(hit.chunk_id)
            if existing is None or hit.score > existing.score:
                selected[hit.chunk_id] = hit.model_copy(deep=True)
            selected[hit.chunk_id].channels = sorted(set([*selected[hit.chunk_id].channels, channel]))
    ordered = sorted(selected, key=lambda identifier: (-aggregate[identifier], identifier))
    diverse: list[RetrievalResult] = []
    per_document: defaultdict[str, int] = defaultdict(int)
    for identifier in ordered:
        item = selected[identifier]
        if per_document[item.document_id] >= max_per_document:
            continue
        per_document[item.document_id] += 1
        item.score = aggregate[identifier]
        item.rank_details = {
            **item.rank_details,
            **details[identifier],
            "rrf_score": aggregate[identifier],
        }
        diverse.append(item)
        if len(diverse) >= limit:
            break
    return diverse


_JURISDICTIONS = {
    "hà nội": "Hà Nội",
    "ha noi": "Hà Nội",
    "hn": "Hà Nội",
    "đà nẵng": "Đà Nẵng",
    "da nang": "Đà Nẵng",
    "hồ chí minh": "Hồ Chí Minh",
    "ho chi minh": "Hồ Chí Minh",
    "tp hcm": "Hồ Chí Minh",
    "tp.hcm": "Hồ Chí Minh",
    "tphcm": "Hồ Chí Minh",
    "sài gòn": "Hồ Chí Minh",
    "sai gon": "Hồ Chí Minh",
    "hải phòng": "Hải Phòng",
    "hai phong": "Hải Phòng",
    "cần thơ": "Cần Thơ",
    "can tho": "Cần Thơ",
}


def detect_jurisdiction(query: str) -> str:
    lowered = " ".join(query.casefold().split())
    for alias, canonical in _JURISDICTIONS.items():
        pattern = rf"\b{re.escape(alias)}\b"
        if re.search(pattern, lowered):
            return canonical
    return ""


def route_retrieval_channels(
    query: str, channels: dict[str, Sequence[RetrievalResult]]
) -> dict[str, list[RetrievalResult]]:
    target_province = detect_jurisdiction(query)
    lowered = query.casefold()
    asks_local = "địa phương" in lowered
    asks_national = any(marker in lowered for marker in ("theo luật", "luật bhyt", "luật bảo hiểm y tế"))

    routed: dict[str, list[RetrievalResult]] = {}
    for name, hits in channels.items():
        items = list(hits)
        if target_province:
            # Explicit jurisdiction: target province first, then Trung uong, then other localities
            def sort_key(item: RetrievalResult):
                is_target = item.province == target_province
                is_central = item.jurisdiction == "Trung ương"
                return (0 if is_target else (1 if is_central else 2), -float(item.score))

            routed[name] = sorted(items, key=sort_key)
        elif asks_national:
            # National question: Trung uong first
            def sort_key_national(item: RetrievalResult):
                is_central = item.jurisdiction == "Trung ương"
                return (0 if is_central else 1, -float(item.score))

            routed[name] = sorted(items, key=sort_key_national)
        elif asks_local:
            # Unspecified local question: default local province (e.g. Ha Noi) first
            def sort_key_local(item: RetrievalResult):
                is_hanoi = item.province == "Hà Nội"
                is_central = item.jurisdiction == "Trung ương"
                return (0 if is_hanoi else (1 if is_central else 2), -float(item.score))

            routed[name] = sorted(items, key=sort_key_local)
        else:
            routed[name] = items

    return routed


def rewrite_user_query(query: str) -> str:
    normalized = " ".join(query.casefold().split())
    replacements = [
        (r"\bthu tuc\b", "thủ tục"),
        (r"\bkham\b", "khám"),
        (r"\bngoai tuyen\b", "ngoại tuyến"),
        (r"\bla\b", "là"),
        (r"\bntn\b", "như thế nào"),
        (r"\bkhám ngoại tuyến\s+là\s+ntn\b", "khám chữa bệnh không đúng tuyến bảo hiểm y tế như thế nào"),
        (r"\bkhám trái tuyến\s+là\s+ntn\b", "khám chữa bệnh không đúng tuyến bảo hiểm y tế như thế nào"),
        (r"\bkhám ngoại tuyến\b", "khám chữa bệnh không đúng tuyến bảo hiểm y tế"),
        (r"\bkhám trái tuyến\b", "khám chữa bệnh không đúng tuyến bảo hiểm y tế"),
        (r"\b(?:người|trẻ)\s+dưới\s+(?:6|sáu)\s+tuổi\b", "trẻ em dưới 6 tuổi"),
        (r"\bblđ\b", "bộ lao động"),
        (r"\bblđtbxh\b", "bộ lao động thương binh và xã hội"),
        (r"\blà\s+như thế nào\b", "như thế nào"),
    ]
    result = normalized
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result)
    return " ".join(result.split())


def expand_query_variants(query: str) -> list[str]:
    variants = [query]
    lowered = query.casefold()
    if "trái tuyến" in lowered or "ngoại tuyến" in lowered:
        variants.append(re.sub(r"\b(?:trái|ngoại)\s+tuyến\b", "không đúng tuyến", query, flags=re.I))
    if re.search(r"\b(?:người|trẻ)\s+dưới\s+(?:6|sáu)\s+tuổi\b", lowered):
        variants.append(re.sub(r"\b(?:người|trẻ)\s+dưới\s+(?:6|sáu)\s+tuổi\b", "trẻ em dưới 6 tuổi", query, flags=re.I))
    return list(dict.fromkeys(variants))


def filter_relevant_evidence(query: str, hits: Sequence[RetrievalResult]) -> list[RetrievalResult]:
    lowered_query = query.casefold()
    is_trai_tuyen = "ngoại tuyến" in lowered_query or "trái tuyến" in lowered_query or "không đúng tuyến" in lowered_query
    filtered: list[RetrievalResult] = []
    for item in hits:
        content = f"{item.title} {item.section_title} {item.content}".casefold()
        if is_trai_tuyen:
            if "vneid" in content or "khai sinh liên thông" in content:
                continue
            if "từ chối thanh toán" in content and "tự đi khám" not in content and "mức hưởng" not in content:
                continue
            if "không đúng tuyến" in content or "trái tuyến" in content or "tự đi khám" in content:
                filtered.append(item)
                continue
        else:
            filtered.append(item)
    return filtered


