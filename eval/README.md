# Live RAGAS evaluation

The generic live Ragas harness is implemented in `eval/golden_eval.py`.

## BHYT 200 candidate evaluation

The current BHYT run is stored at `eval/results/bhyt-200-current/`. It uses the
200 records in `data/eval/bhyt_good_candidates.json` and calls the configured
GraphRAG agent in read-only mode. These records are still candidates: the
`official_answer` field is an unreviewed reference and is not final legal gold.

The completed runtime-only run produced:

- `bhyt_dataset.jsonl`: evaluator input, one case per candidate.
- `actual_answers.jsonl`: the real model answer, retrieved evidence, citations,
  runtime status, and latency for every case.
- `deterministic_case_scores.jsonl`: runtime/fallback/retrieval observations per case.
- `deterministic_summary.json`: aggregate counts.
- `deterministic_report.md`: short human-readable report.
- `run_manifest.json`: run mode, model metadata, threshold, and reference status.

Open the folder in Explorer:

```powershell
explorer .\eval\results\bhyt-200-current
```

The runtime-only command is:

```powershell
.\.venv\Scripts\python.exe eval\bhyt_eval.py run `
  --candidates data\eval\bhyt_good_candidates.json `
  --out eval\results\bhyt-200-current `
  --deterministic-only `
  --count 200 `
  --threshold 0.60
```

This command does not assign answer-quality points. It is used when Ragas is
not installed or cannot reach its dependency index.

After installing the isolated Ragas environment, score the already captured
answers without calling the model again:

```powershell
.\.eval-ragas-venv\Scripts\python.exe -m pip install "ragas==0.3.9" "langchain-openai<1"

.\.eval-ragas-venv\Scripts\python.exe eval\bhyt_eval.py ragas-score `
  --dataset eval\results\bhyt-200-current\bhyt_dataset.jsonl `
  --actual eval\results\bhyt-200-current\actual_answers.jsonl `
  --out eval\results\bhyt-200-current `
  --ragas eval\results\bhyt-200-current\ragas_scores.jsonl `
  --evaluator-model gpt-4o-mini `
  --embedding-model text-embedding-3-small `
  --concurrency 3

.\.venv\Scripts\python.exe eval\bhyt_eval.py finalize `
  --dataset eval\results\bhyt-200-current\bhyt_dataset.jsonl `
  --actual eval\results\bhyt-200-current\actual_answers.jsonl `
  --ragas eval\results\bhyt-200-current\ragas_scores.jsonl `
  --out eval\results\bhyt-200-current `
  --threshold 0.60
```

After Ragas scoring, inspect `ragas_scores.jsonl`, `case_scores.jsonl`,
`summary.json`, `report.md`, and `failures.md`. Ragas is the evaluator/judge;
the model under test remains `MODEL_NAME` from `.env`.

## Đọc kết quả

1. `report.md`: kết luận, điểm trung bình và phần dự án đang yếu.
2. `failures.md`: từng failure thật, điểm thấp, lý do, actual answer và nơi nên kiểm tra.
3. `summary.json`: thống kê máy đọc được theo metric, nguồn và loại câu hỏi.
4. `case_scores.jsonl`: release gate đầy đủ cho toàn bộ denominator.
5. `ragas_scores.jsonl`: năm metric official RAGAS cho từng source case.
6. `actual_answers.jsonl`: output và retrieved context thật của agent.
7. `golden_dataset.jsonl`: câu hỏi/reference/fact trích từ CSV nguồn thật cùng provenance.
8. `dataset_validation.json`: hash nguồn và kết quả kiểm tra golden dataset.
9. `run_manifest.json`: model, phiên bản RAGAS, threshold và hash artifact.

## Quy tắc PASS/FAIL

- Ngưỡng từng metric cốt lõi và quality score là `0.60`.
- Source cases dùng factual correctness, completeness, response relevancy, faithfulness,
  context precision, context recall và ID context recall.
- Fallback chung chung, retrieval miss, forbidden claim hoặc policy behavior thiếu là FAIL cứng.
- Metric lỗi, thiếu hoặc NaN là `NOT_OBSERVABLE`, không bao giờ được tính PASS.
- Sáu policy cases được chấm bằng gate hành vi deterministic vì không cần retrieval context.

## Nguồn golden dataset

Ba file được join theo `id`:

- `data/raw/metadata_bhyt.csv`
- `data/raw/metadata_vien_phi.csv`
- `data/raw/content.csv`

Mỗi câu user-facing dùng số hiệu/tên văn bản thật; internal document ID chỉ nằm trong
provenance và reference IDs. Validator kiểm tra source hash, content reference, gold
completeness, trùng case ID, secret pattern và gold leakage.

## Chạy lại

RAGAS được tách khỏi `.venv` production để không làm thay đổi dependency dự án:

```powershell
python -m venv .eval-ragas-venv
.\.eval-ragas-venv\Scripts\python.exe -m pip install "ragas==0.3.9" "langchain-openai<1"
.\.venv\Scripts\python.exe eval\golden_eval.py live `
  --source-dir data\raw `
  --out eval\results\canonical-live-ragas `
  --count 30 `
  --ragas-python .eval-ragas-venv\Scripts\python.exe `
  --evaluator-model gpt-4o-mini `
  --embedding-model text-embedding-3-small `
  --concurrency 3 `
  --threshold 0.60
```

Lệnh `live` gọi agent/DB/Neo4j ở chế độ read-only. Không sửa production chỉ để làm
đẹp điểm eval; sửa retrieval/prompt/guardrail rồi chạy lại cùng nguồn và threshold.
