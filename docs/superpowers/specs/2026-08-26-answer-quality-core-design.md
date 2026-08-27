# MediPay Answer-Quality Core Design

**Ngày:** 2026-08-26  
**Trạng thái:** Đã duyệt trong hội thoại; chưa triển khai  
**Phạm vi:** Backend RAG cho BHYT và viện phí; BHXH chỉ khi có bằng chứng trong corpus

## 1. Mục tiêu

Nâng độ chính xác của chatbot MediPay trên corpus hiện có mà không phụ thuộc vào corpus production 682 văn bản của P-151. Hệ thống ưu tiên bằng chứng pháp lý và chấp nhận tăng độ trễ để giảm trả lời sai.

Các nguyên tắc bắt buộc:

- Không có bằng chứng thì không kết luận.
- Mỗi kết luận nghiệp vụ quan trọng phải được một passage có provenance hỗ trợ.
- Tỷ lệ, số tiền, ngày, điều kiện, ngoại lệ và tình trạng hiệu lực phải được kiểm tra riêng.
- BHYT và viện phí là phạm vi chính.
- Câu BHXH/thai sản chỉ được trả lời khi corpus có evidence phù hợp; nếu không phải abstain.
- Không dùng web search làm evidence trực tiếp trong runtime.
- Không dùng hoặc kiểm tra lại các credential đã bị lộ.

## 2. Ngoài phạm vi

- Không mở rộng corpus bằng crawl hàng loạt trong đợt này.
- Không đổi embedding model hoặc embedding dimensions.
- Không rechunk/re-embed toàn bộ corpus nếu chưa có ablation chứng minh lợi ích.
- Không thay frontend, deployment, database schema hay release đang active.
- Không chạy full RAGAS scoring.
- Không tra cứu hồ sơ cá nhân, OTP, mã BHXH hoặc trạng thái chi trả của một người.
- Không commit thay đổi.

## 3. Hiện trạng và giới hạn

- Group project có 318 documents, 314 documents có nội dung phục vụ trả lời, 11.546 passages và 10.112 semantic vectors.
- Canonical chunker đã tương đương P-151 về kích thước; chunk nhỏ hơn không phải đòn bẩy chính.
- Query normalization, constrained HyDE, legal reranking và PostgreSQL FTS đã tồn tại.
- Qdrant runtime hiện dense-only, thiếu BM25 hybrid và server-side RRF.
- Answer path hiện có xu hướng trả evidence thô khi generation/verification không đạt.
- Logic orchestration tập trung nhiều trong `src/services/chat.py`; đợt này không tái cấu trúc toàn bộ file mà chỉ giới hạn thay đổi cần thiết cho answer quality.
- Worktree có nhiều thay đổi chưa commit. Việc triển khai phải bảo toàn thay đổi hiện hữu và chỉ sửa các file được liệt kê trong kế hoạch.

Baseline đã quan sát: 33 test lõi retrieval/chat/eval-candidate pass.

## 4. Kiến trúc mục tiêu

Luồng xử lý:

`Question -> Domain/Policy Gate -> Query Plan -> Hybrid Retrieval -> Fusion/Rerank -> Evidence Verification -> Answer Composition -> Claim Guard -> Response`

### 4.1 Domain và policy gate

Gate phân loại câu hỏi thành:

- BHYT/viện phí được hỗ trợ;
- BHXH có thể thử retrieval nhưng bắt buộc evidence gate;
- câu social/capability;
- câu yêu cầu dữ liệu cá nhân hoặc hành vi không an toàn;
- ngoài phạm vi.

Gate không được biến câu hỏi pháp lý khó thành câu trả lời policy chung chung. Exact document lookup, temporal question và relational question phải tiếp tục tới retrieval phù hợp.

### 4.2 Query plan

Planner phân loại `lookup`, `legal_unit`, `temporal`, `relational` hoặc `thematic`.

Mỗi plan có thể dùng tối đa các query view sau:

- câu hỏi gốc;
- câu đã chuẩn hóa thuật ngữ BHYT;
- tối đa một constrained HyDE;
- tối đa ba sub-query khi câu hỏi thực sự có nhiều vế.

HyDE chỉ phục vụ retrieval. Nó không được thêm số hiệu văn bản, tỷ lệ, số tiền, năm, cơ quan hoặc địa phương không có trong câu hỏi.

### 4.3 Hybrid retrieval

Các channel giữ vai trò độc lập:

- PostgreSQL exact lookup cho số hiệu, title và legal unit;
- PostgreSQL FTS cho lexical recall;
- Qdrant dense semantic;
- Qdrant BM25 sparse khi collection hỗ trợ;
- Neo4j cho quan hệ sửa đổi, thay thế, bãi bỏ và hướng dẫn;
- page/legal-unit expansion cho Điều/Khoản lân cận.

Qdrant adapter phải phát hiện capability và cache kết quả trong thời gian hữu hạn. Khi BM25 khả dụng, dense và sparse được fusion bằng RRF phía Qdrant. Khi không khả dụng, adapter trả dense bình thường và PostgreSQL FTS vẫn cung cấp lexical channel.

Mọi query phải giữ `dataset_id` và `answer_ready=true`. Không được trộn release.

### 4.4 Fusion và legal reranking

Candidate pool được fusion bằng weighted RRF rồi rerank theo:

- độ khớp query-derived terms/phrases;
- exact document/legal-unit match;
- authority và phạm vi áp dụng;
- tình trạng hiệu lực và thời điểm câu hỏi;
- địa phương được nêu rõ, với văn bản trung ương ưu tiên cho câu hỏi toàn quốc;
- provenance đầy đủ;
- diversity theo document và legal unit.

Title, metadata và graph edge có thể hỗ trợ routing nhưng không tự trở thành bằng chứng cho một quyền lợi hoặc mức hưởng.

### 4.5 Evidence verification

Evidence được giữ khi có content đủ nghĩa và định danh nguồn. Passage lý tưởng có `document_id`, `chunk_id`, source span và text/source hash. Evidence nhiễu, khác chủ đề, khác địa phương hoặc chỉ là structural text phải bị loại.

Với câu temporal hoặc mức hưởng có rủi ro cao, thiếu status/provenance làm cho evidence không đủ để kết luận. Evidence mâu thuẫn không được tự động giải quyết bằng độ tương đồng vector; hệ thống phải nêu mâu thuẫn hoặc abstain.

### 4.6 Answer composition

Thứ tự ưu tiên:

1. deterministic source-rule answer cho quy tắc rõ ràng;
2. deterministic source-fact answer cho metadata/fact rõ ràng;
3. LLM synthesis cho câu nhiều nguồn hoặc cần diễn đạt tự nhiên;
4. fail-safe nếu không đủ evidence.

Câu trả lời hướng tới cấu trúc ngắn gọn:

- kết luận trực tiếp;
- điều kiện và ngoại lệ;
- căn cứ pháp lý;
- cảnh báo thời điểm/phạm vi nếu có.

Không dump context, UUID, chunk hash, metadata key hoặc prompt nội bộ.

### 4.7 Claim guard

Answer được tách thành các claim có loại: status, entitlement, condition, exception, procedure, document hoặc general.

Mỗi claim phải có evidence ID và citation tương ứng. Guard kiểm tra thêm:

- số/tỷ lệ/số tiền trong claim có xuất hiện trong evidence;
- ngày và tình trạng hiệu lực có provenance phù hợp;
- subject của claim khớp văn bản/citation;
- claim không vượt quá phạm vi evidence.

Claim không được hỗ trợ bị loại. Nếu claim bị loại là phần cốt lõi của câu trả lời, toàn bộ response chuyển sang fail-safe thay vì trả phần còn lại gây hiểu nhầm.

## 5. Tavily

Tavily chỉ là công cụ enrichment offline:

`Backlog -> Tavily search trong official domains -> Identity/status gate -> Audit artifact -> Human review -> Rebuild release`

Nó không được gọi trong request path của chatbot và kết quả tìm kiếm không được dùng trực tiếp làm citation. Chỉ kết quả khớp số hiệu, cơ quan/năm và official domain mới có thể đi vào review artifact. Mọi merge vào canonical corpus là một quy trình riêng, ngoài phạm vi triển khai answer-quality core.

## 6. Failure handling

- Qdrant BM25 không khả dụng: dense + PostgreSQL FTS.
- Một retrieval channel timeout: tiếp tục với channel còn lại và đánh dấu degraded.
- Dataset/release mismatch: không trả lời từ evidence trộn release.
- LLM generation lỗi nhưng evidence rõ: deterministic answer.
- Evidence thiếu hoặc claim guard loại phần cốt lõi: fail-safe.
- Evidence mâu thuẫn: mô tả mâu thuẫn có citation hoặc abstain.
- Query cá nhân/OTP/hồ sơ: từ chối phù hợp và không retrieval dữ liệu cá nhân.

Fail-safe phải nói ngắn gọn lý do chưa đủ căn cứ và gợi ý người dùng cung cấp thông tin không nhạy cảm cần thiết hoặc liên hệ cơ quan có thẩm quyền.

## 7. Ranh giới thay đổi

Các file dự kiến được sửa:

- `src/integrations/qdrant.py`
- `src/services/retrieval.py`
- `src/services/claims.py`
- `src/agents/nodes/graphrag_nodes.py`
- `src/services/chat.py` chỉ tại integration points cần thiết
- test tương ứng dưới `tests/`

Có thể tạo một module nhỏ chuyên answer verification/composition nếu việc thêm logic vào `graphrag_nodes.py` làm trách nhiệm không rõ. Không thực hiện refactor rộng ngoài luồng này.

## 8. Kiểm thử và acceptance gates

Triển khai theo TDD. Mỗi hành vi mới phải có test fail trước khi code production được sửa.

Test bắt buộc:

- Qdrant hybrid query tạo dense + BM25 prefetch và RRF khi được hỗ trợ;
- capability failure/timeouts fallback về dense, không làm request fail;
- dataset và answer-ready filters luôn được giữ;
- weighted fusion giữ provenance và diversity;
- deterministic answer không dump context;
- unsupported numeric/temporal/entitlement claim bị loại;
- core claim thiếu evidence dẫn tới fail-safe;
- sanitizer chặn UUID, hashes và metadata keys;
- BHXH không có evidence phải abstain;
- các policy/safety case vẫn pass.

Regression:

- 33 test baseline tiếp tục pass;
- targeted backend suite pass;
- schema/quality checks của golden dataset hiện có pass;
- một subset BHYT/viện phí từ super-golden P-151 được đưa vào regression fixture khi không phụ thuộc production-only hashes;
- không gọi full RAGAS và không gọi cloud bằng credential đã lộ.

Tiêu chí hoàn thành:

- Không test regression nào fail.
- Dense-only deployment vẫn hoạt động.
- Hybrid path có test contract độc lập.
- Không có raw-context fallback cho câu evidence-backed thông thường.
- Không có unsupported high-risk claim trong test suite.
- Fail-safe xuất hiện đúng khi evidence thiếu/mâu thuẫn.

## 9. Triển khai an toàn trong dirty worktree

- Không reset, checkout hoặc xóa thay đổi hiện có.
- Không commit.
- Ghi nhận status/diff trước khi sửa từng file.
- Dùng `apply_patch` cho chỉnh sửa có chủ đích.
- Chạy test theo từng component rồi chạy targeted aggregate suite.
- Không sửa `.env` và không hiển thị secret.

