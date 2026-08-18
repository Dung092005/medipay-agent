# Live RAGAS evaluation

Evaluation chuẩn nằm duy nhất tại `eval/results/canonical-live-ragas/`.

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
