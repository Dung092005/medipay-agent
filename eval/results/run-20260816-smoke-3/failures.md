# Failures thật của live evaluation

- Trạng thái run: **FAIL**
- Ngưỡng metric/gate: **0.60**
- Fail: 3; không quan sát được: 0

Mỗi mục dưới đây lấy trực tiếp từ output agent và metric của đúng run này.

## DOC-BHYT-187-TITLE — FAIL — P1

- Nhóm lỗi: LOW_COMPLETENESS, FALLBACK_ANSWER, RETRIEVAL_MISS, LOW_FACTUAL_CORRECTNESS, LOW_RESPONSE_RELEVANCY, LOW_FAITHFULNESS, LOW_CONTEXT_PRECISION, LOW_CONTEXT_RECALL, LOW_ID_CONTEXT_RECALL, LOW_QUALITY_SCORE
- Điểm: completeness=0.000, id_context_precision=0.000, id_context_recall=0.000, factual_correctness=0.000, response_relevancy=0.000, faithfulness=0.000, context_precision=0.000, context_recall=0.000, quality_score=0.000
- Vì sao sai: thiếu fact: title; câu trả lời là fallback chung chung; không truy xuất được document nguồn đích; factual_correctness=0.000 < 0.60; completeness=0.000 < 0.60; response_relevancy=0.000 < 0.60; faithfulness=0.000 < 0.60; context_precision=0.000 < 0.60; context_recall=0.000 < 0.60; id_context_recall=0.000 < 0.60; quality_score=0.000 < 0.60
- Document truy xuất: 173380, 158024, 173463, 172977, 173380, 173149, 167199, 169449, 158729, 183011, 12326, 36793, 36793, 12326, 158024
- Fact thiếu: title
- Nơi nên kiểm tra: src/agents/prompts.py, src/agents/nodes/graphrag_nodes.py, src/integrations/neo4j.py
- Câu hỏi: VÄƒn báº£n sá»‘ hiá»‡u 60/2026/NQ-HÄND cÃ³ tÃªn Ä‘áº§y Ä‘á»§ lÃ  gÃ¬?
- Câu trả lời thực tế: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.

## DATE-BHYT-187-EFFECTIVE — FAIL — P1

- Nhóm lỗi: LOW_COMPLETENESS, FALLBACK_ANSWER, RETRIEVAL_MISS, LOW_FACTUAL_CORRECTNESS, LOW_RESPONSE_RELEVANCY, LOW_FAITHFULNESS, LOW_CONTEXT_PRECISION, LOW_CONTEXT_RECALL, LOW_ID_CONTEXT_RECALL, LOW_QUALITY_SCORE
- Điểm: completeness=0.000, id_context_precision=0.000, id_context_recall=0.000, factual_correctness=0.000, response_relevancy=0.000, faithfulness=0.000, context_precision=0.000, context_recall=0.000, quality_score=0.000
- Vì sao sai: thiếu fact: effective_date, status; câu trả lời là fallback chung chung; không truy xuất được document nguồn đích; factual_correctness=0.000 < 0.60; completeness=0.000 < 0.60; response_relevancy=0.000 < 0.60; faithfulness=0.000 < 0.60; context_precision=0.000 < 0.60; context_recall=0.000 < 0.60; id_context_recall=0.000 < 0.60; quality_score=0.000 < 0.60
- Document truy xuất: 173380, 167199, 158024, 172977, 173380, 173463, 184970, 153497, 187259, 58291, 153497
- Fact thiếu: effective_date, status
- Nơi nên kiểm tra: src/agents/prompts.py, src/agents/nodes/graphrag_nodes.py, src/integrations/neo4j.py
- Câu hỏi: VÄƒn báº£n sá»‘ hiá»‡u 60/2026/NQ-HÄND cÃ³ hiá»‡u lá»±c tá»« ngÃ y nÃ o vÃ  hiá»‡n cÃ²n hiá»‡u lá»±c khÃ´ng?
- Câu trả lời thực tế: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.

## CAT-BHYT-187-DOMAIN — FAIL — P1

- Nhóm lỗi: LOW_COMPLETENESS, FALLBACK_ANSWER, RETRIEVAL_MISS, LOW_FACTUAL_CORRECTNESS, LOW_RESPONSE_RELEVANCY, LOW_FAITHFULNESS, LOW_CONTEXT_PRECISION, LOW_CONTEXT_RECALL, LOW_ID_CONTEXT_RECALL, LOW_QUALITY_SCORE
- Điểm: completeness=0.000, id_context_precision=0.000, id_context_recall=0.000, factual_correctness=0.000, response_relevancy=0.000, faithfulness=0.000, context_precision=0.167, context_recall=0.000, quality_score=0.025
- Vì sao sai: thiếu fact: domain; câu trả lời là fallback chung chung; không truy xuất được document nguồn đích; factual_correctness=0.000 < 0.60; completeness=0.000 < 0.60; response_relevancy=0.000 < 0.60; faithfulness=0.000 < 0.60; context_precision=0.167 < 0.60; context_recall=0.000 < 0.60; id_context_recall=0.000 < 0.60; quality_score=0.025 < 0.60
- Document truy xuất: 169449, 173463, 169478, 174421, 173343, 179797, 173398, 173621, 173906, 172675, 168128, 168125, 169449, 168128, 168125
- Fact thiếu: domain
- Nơi nên kiểm tra: src/agents/prompts.py, src/agents/nodes/graphrag_nodes.py, src/integrations/neo4j.py
- Câu hỏi: VÄƒn báº£n sá»‘ hiá»‡u 60/2026/NQ-HÄND thuá»™c nhÃ³m ná»™i dung nÃ o?
- Câu trả lời thực tế: Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp để giải đáp câu hỏi này.

