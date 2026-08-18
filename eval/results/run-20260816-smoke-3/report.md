# Báo cáo live RAGAS evaluation

- Kết luận: **FAIL**
- Tổng số case: 3
- Pass: 0
- Fail: 3
- Không quan sát được: 0
- Pass rate: 0.0%
- Fallback chung chung: 3/3
- Ngưỡng release gate: 0.60

## Điểm trung bình trên các câu hỏi trích từ dataset thật

- factual_correctness: 0.000
- completeness: 0.000
- response_relevancy: 0.000
- faithfulness: 0.000
- context_precision: 0.056
- context_recall: 0.000
- id_context_recall: 0.000
- quality_score: 0.008

## Dự án đang yếu ở đâu

- factual_correctness: 0.000
- completeness: 0.000
- response_relevancy: 0.000
- faithfulness: 0.000

Các metric thấp cho biết: context_recall/id_context_recall thấp là retrieval bỏ sót nguồn; context_precision thấp là lấy nhiều context nhiễu; faithfulness thấp là câu trả lời không bám context; factual_correctness/completeness thấp là trả sai hoặc thiếu fact; response_relevancy thấp là trả không đúng trọng tâm.

## Kết quả theo nguồn

- metadata_bhyt.csv: 0/3 pass; 3 fallback
- metadata_vien_phi.csv: 0/0 pass; 0 fallback
- synthetic_policy: 0/0 pass; 0 fallback

## Kết quả theo loại câu hỏi

- coverage_metadata: 0/1 pass; 1 fallback
- document_lookup: 0/1 pass; 1 fallback
- policy_date: 0/1 pass; 1 fallback

## Phân bố failure

- FALLBACK_ANSWER: 3 case
- LOW_COMPLETENESS: 3 case
- LOW_CONTEXT_PRECISION: 3 case
- LOW_CONTEXT_RECALL: 3 case
- LOW_FACTUAL_CORRECTNESS: 3 case
- LOW_FAITHFULNESS: 3 case
- LOW_ID_CONTEXT_RECALL: 3 case
- LOW_QUALITY_SCORE: 3 case
- LOW_RESPONSE_RELEVANCY: 3 case
- RETRIEVAL_MISS: 3 case

## Tính trung thực của kết quả

- Golden source cases được join từ metadata_bhyt.csv, metadata_vien_phi.csv và content.csv; hash nguồn nằm trong dataset_validation.json.
- Actual answers được gọi từ agent production ở chế độ read-only và lưu cả retrieved context.
- Các metric ngữ nghĩa dùng official RAGAS; metric lỗi/NaN được đánh dấu NOT_OBSERVABLE, tuyệt đối không tính PASS.
- Fallback chung chung, retrieval miss, vi phạm policy và các gate dưới 0.60 đều là failure thật.

## File để tự kiểm tra

1. golden_dataset.jsonl — câu hỏi/reference/fact và provenance.
2. actual_answers.jsonl — output và retrieval trace thật.
3. ragas_scores.jsonl — điểm official RAGAS từng case.
4. case_scores.jsonl — gate cuối và nguyên nhân từng case.
5. failures.md — chỉ các case không pass.
