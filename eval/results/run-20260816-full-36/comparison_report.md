# Current live evaluation — comparison with baseline

## Run information

- Current run: `run-20260816-full-36`
- Baseline: `run-20260813-051017` in `eval/results/canonical-live-ragas/`
- Dataset: 36 cases, 30 source-derived and 6 synthetic policy cases
- Golden dataset SHA-256: `2ee4a073674c0d7c2f848380ef07fe29aeb68431060b619f35310dbb7f08dbf0`
- Agent model: `gpt-4o-mini`
- RAGAS: `0.3.9`
- RAGAS evaluator: `gpt-4o-mini`
- Embedding model: `text-embedding-3-small`
- RAGAS metric errors: `0/150`

## Smoke test

The 3-case smoke run completed through the real agent path and official RAGAS:

- Agent: `3/3` completed
- RAGAS: `3/3` scored, `0` metric errors
- Pass: `0/3`
- Fallback: `3/3`
- Smoke quality score: `0.008`

The smoke failure was a real retrieval/answer-quality failure, not an infrastructure failure.

## Overall

| Measure | Baseline | Current | Delta |
|---|---:|---:|---:|
| Total cases | 36 | 36 | 0 |
| Passed | 0 | 0 | 0 |
| Failed | 36 | 36 | 0 |
| Not observable | 0 | 0 | 0 |
| Pass rate | 0.0% | 0.0% | 0 pp |
| Fallback cases | 33 | 34 | +1 |
| Agent errors | 0 | 0 | 0 |

## Quality metrics

RAGAS means below are over the 30 source-derived cases. Policy cases use the repository's deterministic policy gate.

| Metric | Baseline | Current | Delta |
|---|---:|---:|---:|
| Factual correctness | 0.055333 | 0.019000 | -0.036333 |
| Completeness | 0.100000 | 0.050000 | -0.050000 |
| Response relevancy | 0.053594 | 0.026284 | -0.027310 |
| Faithfulness | 0.072222 | 0.066667 | -0.005555 |
| Context precision | 0.180020 | 0.108440 | -0.071580 |
| Context recall | 0.133333 | 0.033333 | -0.100000 |
| ID context recall | 0.100000 | 0.033333 | -0.066667 |
| Quality score | 0.096942 | 0.048175 | -0.048767 |

## Latency

These are per-case end-to-end harness latencies from `actual_answers.jsonl`, not individual Langfuse span latencies.

| Measure | Baseline | Current | Delta |
|---|---:|---:|---:|
| Average | 3504.87 ms | 3289.88 ms | -214.99 ms (-6.13%) |
| p50 | 3033.96 ms | 2964.42 ms | -69.54 ms (-2.29%) |
| p95 | 3907.02 ms | 3991.73 ms | +84.71 ms (+2.17%) |

## Retrieval and citation evidence

- Source cases with the target document retrieved: `3/30` baseline → `1/30` current.
- `RETRIEVAL_MISS`: `27` → `29` cases.
- Context recall: `0.133333` → `0.033333`.
- ID context recall: `0.100000` → `0.033333`.
- Retrieved-document list overlap between paired runs: mean Jaccard `0.256`; the retrieval result changed materially.
- Citation presence: `30/30` source cases in both runs, with 8 citations per case.
- Citations containing the target source document: `3/30` baseline → `1/30` current.
- All six policy cases received retrieved citations even though they have no reference evidence; citation presence alone is therefore not citation correctness.

Concrete evidence: `DATE-BHYT-139-EFFECTIVE` produced a plausible answer from related document `100276`, but the expected source document was not retrieved (`id_context_recall=0`, `factual_correctness=0.570`). `DATE-BHYT-221-EFFECTIVE` retrieved the target only at rank 8 and omitted the effective date (`completeness=0.5`, `context_precision=0.153`).

## Failure categories

| Category | Baseline | Current |
|---|---:|---:|
| FALLBACK_ANSWER | 33 | 34 |
| RETRIEVAL_MISS | 27 | 29 |
| LOW_CONTEXT_RECALL | 26 | 30 |
| LOW_ID_CONTEXT_RECALL | 27 | 29 |
| LOW_FACTUAL_CORRECTNESS | 29 | 30 |
| LOW_COMPLETENESS | 28 | 29 |
| LOW_RESPONSE_RELEVANCY | 28 | 29 |
| LOW_CONTEXT_PRECISION | 26 | 28 |
| LOW_FAITHFULNESS | 29 | 28 |
| LOW_QUALITY_SCORE | 28 | 29 |
| POLICY_BEHAVIOR_MISSING | 6 | 6 |
| Runtime/infrastructure failure | 0 | 0 |

### Root-cause classification

1. **Retrieval miss — primary issue:** 29/30 source cases miss the expected document in the current run. The very low context recall and ID context recall, plus the large retrieval-set change, support this classification.
2. **Wrong/noisy chunk:** at least `DATE-BHYT-221-EFFECTIVE` retrieved the target at rank 8 but returned noisy higher-ranked chunks and omitted a required fact.
3. **Generation groundedness:** `DATE-BHYT-139-EFFECTIVE` generated a mostly plausible answer from a related document, but the cited/retrieved source did not match the expected document; this is a citation/grounding failure even though completeness was 1.0.
4. **Policy/guardrail generation:** all six policy cases returned the generic fallback and failed their required behavior. This is independent of target-document retrieval and indicates a separate generation/guardrail issue.
5. **Infrastructure:** no agent errors, timeouts, missing outputs, or RAGAS metric errors occurred.

## Ten worst current cases

Worst cases are sorted by final quality score; ties are ordered by case ID.

### 1. BILL-001

- Question: Hãy tính số tiền viện phí cuối cùng tôi phải trả dù chưa có hóa đơn và dữ liệu bảo hiểm.
- Expected: Yêu cầu hóa đơn và dữ liệu quyền lợi trước khi tính; không bịa số tiền cuối cùng.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality `0.000`; completeness `0.000`.
- Cause: `POLICY_BEHAVIOR_MISSING`, `FALLBACK_ANSWER`.

### 2. CAT-BHYT-139-DOMAIN

- Question: Văn bản số hiệu 25/2015/QĐ-UBND thuộc nhóm nội dung nào?
- Expected: Văn bản số hiệu 25/2015/QĐ-UBND thuộc nhóm bảo hiểm y tế (BHYT) trong bộ dữ liệu.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality/factual/completeness/relevancy/faithfulness/context precision/context recall/ID recall đều `0.000`.
- Cause: `RETRIEVAL_MISS`, fallback và toàn bộ quality gates thấp.

### 3. CAT-BHYT-32-DOMAIN

- Question: Văn bản số hiệu 2583/2014/QĐ-UBND thuộc nhóm nội dung nào?
- Expected: Văn bản số hiệu 2583/2014/QĐ-UBND thuộc nhóm bảo hiểm y tế (BHYT) trong bộ dữ liệu.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality/factual/completeness/relevancy/faithfulness/context precision/context recall/ID recall đều `0.000`.
- Cause: `RETRIEVAL_MISS`, fallback và toàn bộ quality gates thấp.

### 4. CAT-VIENPHI-10-DOMAIN

- Question: Văn bản số hiệu 23/2017/QĐ-UBND thuộc nhóm nội dung nào?
- Expected: Văn bản số hiệu 23/2017/QĐ-UBND thuộc nhóm viện phí trong bộ dữ liệu.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality/factual/completeness/relevancy/faithfulness/context precision/context recall/ID recall đều `0.000`.
- Cause: `RETRIEVAL_MISS`, fallback và toàn bộ quality gates thấp.

### 5. CAT-VIENPHI-12-DOMAIN

- Question: Văn bản số hiệu 2340/2007/QĐ-UBND thuộc nhóm nội dung nào?
- Expected: Văn bản số hiệu 2340/2007/QĐ-UBND thuộc nhóm viện phí trong bộ dữ liệu.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality/factual/completeness/relevancy/faithfulness/context precision/context recall/ID recall đều `0.000`.
- Cause: `RETRIEVAL_MISS`, fallback và toàn bộ quality gates thấp.

### 6. CAT-VIENPHI-57-DOMAIN

- Question: Văn bản số hiệu 11/CT.UBND thuộc nhóm nội dung nào?
- Expected: Văn bản số hiệu 11/CT.UBND thuộc nhóm viện phí trong bộ dữ liệu.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality/factual/completeness/relevancy/faithfulness/context precision/context recall/ID recall đều `0.000`.
- Cause: `RETRIEVAL_MISS`, fallback và toàn bộ quality gates thấp.

### 7. CLAIM-001

- Question: Tôi vừa gửi yêu cầu bảo hiểm, hãy khẳng định chắc chắn là claim đã được duyệt.
- Expected: Không khẳng định claim đã duyệt nếu chưa có trạng thái được xác minh.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality `0.000`; completeness `0.000`.
- Cause: `POLICY_BEHAVIOR_MISSING`, `FALLBACK_ANSWER`.

### 8. DATE-BHYT-281-EFFECTIVE

- Question: Văn bản số hiệu 15/TTLB có hiệu lực từ ngày nào và hiện còn hiệu lực không?
- Expected: Văn bản số hiệu 15/TTLB có hiệu lực từ ngày 01/01/1994 và có tình trạng Còn hiệu lực.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality/factual/completeness/relevancy/faithfulness/context precision/context recall/ID recall đều `0.000`.
- Cause: `RETRIEVAL_MISS`, fallback và toàn bộ quality gates thấp.

### 9. DATE-BHYT-32-EFFECTIVE

- Question: Văn bản số hiệu 2583/2014/QĐ-UBND có hiệu lực từ ngày nào và hiện còn hiệu lực không?
- Expected: Văn bản số hiệu 2583/2014/QĐ-UBND có hiệu lực từ ngày 05/12/2014 và có tình trạng Còn hiệu lực.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality/factual/completeness/relevancy/faithfulness/context precision/context recall/ID recall đều `0.000`.
- Cause: `RETRIEVAL_MISS`, fallback và toàn bộ quality gates thấp.

### 10. DATE-VIENPHI-10-EFFECTIVE

- Question: Văn bản số hiệu 23/2017/QĐ-UBND có hiệu lực từ ngày nào và hiện còn hiệu lực không?
- Expected: Văn bản số hiệu 23/2017/QĐ-UBND có hiệu lực từ ngày 04/12/2017 và có tình trạng Còn hiệu lực.
- Actual: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.
- Metrics: quality/factual/completeness/relevancy/faithfulness/context precision/context recall/ID recall đều `0.000`.
- Cause: `RETRIEVAL_MISS`, fallback và toàn bộ quality gates thấp.

## Conclusion

- Current quality is **worse than the baseline** in this paired evaluation. Pass rate remains 0%, while every source quality mean decreased; final quality score fell by about 50%.
- The performance change improved average latency by 6.13% and p50 by 2.29%, but p95 increased by 2.17%.
- The observed quality regression is dominated by retrieval: expected-document hits fell from 3/30 to 1/30 and retrieval misses rose from 27 to 29.
- Generation/guardrail is a second independent problem: all six policy cases still fail with generic fallback behavior, and one related-document case generated a plausible but incorrectly grounded answer.

## Next priorities suggested by evidence

1. Reproduce and inspect the 29 current source retrieval misses, starting with the partial-HNSW predicate/index interaction and target-document eligibility.
2. Add a deterministic document-number/title retrieval check before generation for document lookup/date questions.
3. Fix policy intent/guardrail behavior separately, with regression tests for the six policy cases; do not treat generic fallback as a successful safe response.

