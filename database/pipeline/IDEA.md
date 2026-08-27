# Kế hoạch dữ liệu BHYT / viện phí cho Supabase

## Phạm vi cố định

Nguồn duy nhất là corpus pháp lý trong `data/raw/`. Dự án không xử lý
bệnh nhân, khách hàng, claim, encounter, bệnh án hay quyết định chi trả cá
thể. Không dùng LLM để tạo authority data.

## Mục tiêu kiến trúc

```text
CSV authority
  ├── Canonical release + raw HTML + hashes
  ├── Directed document relationship graph
  ├── PageIndex structural graph
  ├── Evidence/document/unit/chunk/table links
  └── Native RAG: exact + lexical + Qdrant semantic + graph expansion
```

### Các graph được triển khai

1. **Authority relationship graph**: document nodes và directed edges từ
   `relationships.csv`; giữ predicate, direction, selected flags và adverse
   signal.
2. **PageIndex graph**: cây legal units deterministic với parent, ordinal,
   source span, selector và hash.
3. **Evidence graph**: quan hệ document–unit–chunk và table–cell để citation.

Facet memberships chỉ là derived filter/index, không phải authority graph.
Không triển khai claim/rule graph, similarity graph hoặc community graph.

## Pipeline bắt buộc

1. Validate authority CSV và projection mismatch.
2. Tạo immutable canonical snapshot có release hash.
3. Parse visible HTML, legal units, tables và source provenance.
4. Tạo retrieval chunks theo legal-unit boundary.
5. Stage toàn bộ release vào Supabase PostgreSQL.
6. Tạo lexical index và Qdrant semantic projection.
7. Chạy retrieval smoke/provenance checks.
8. Atomic activate release.

## Chunking contract

- Không dùng mỗi HTML block làm một semantic chunk.
- HTML block ngắn được ghép trong cùng legal unit.
- Legal unit được tách theo paragraph/sentence, sau đó gom các câu đến mục tiêu
  144 tokens (nằm trong khoảng 120–160); câu dị thường bị hard-cap bằng
  tokenizer thật, có chừa headroom cho section context và special tokens.
- Không cắt qua ranh giới legal unit nếu không cần thiết.
- Mỗi chunk có stable ID, document ID, unit ID, order, text hash và source
  span.
- Raw HTML được lưu riêng theo release/document với SHA-256; normalized text
  chỉ là projection phục vụ tìm kiếm, không thay thế source để render.
- Bảng giữ table/cell artifact riêng; semantic chunk không làm mất header hoặc
  giá trị cell.
- Embedding worker dùng tokenizer thật của model để fail fast nếu vượt giới
  hạn.
- Embedding dùng OpenAI `text-embedding-3-small` (1536 chiều); Supabase chỉ
  nhận vector sau khi release đã stage và validate.

## Retrieval contract

- Exact: số hiệu, tiêu đề, Điều/Khoản/Điểm.
- Lexical: PostgreSQL `tsvector`/`tsquery` và rank deterministic.
- Semantic: OpenAI embedding + Qdrant cosine.
- Legal graph: seed từ evidence hit, expand directed edge depth 1, giữ edge
  provenance và áp dụng date/jurisdiction filter trước expansion.
- Fusion: exact/lexical/semantic là evidence channels riêng; RRF chỉ xếp hạng,
  không biến score thành hiệu lực pháp lý.

## Supabase requirements

- bật extension `vector`;
- dùng `vector(n)` và HNSW cosine index;
- không trộn semantic vector vào PostgreSQL; collection Qdrant có release fingerprint;
- mọi bảng có `dataset_id` và active views;
- API chỉ đọc active release;
- bật RLS ở lớp triển khai nếu endpoint public.

## Verification gates

- authority IDs không trùng, relationship không orphan;
- rebuild cùng input cho cùng release/hash/stable IDs;
- 100% retrieval chunk có document/unit và source provenance;
- table selector/hash tồn tại, cell round-trip không mất dữ liệu;
- vector model, dimension, preprocessing và input hash đồng nhất;
- lexical, semantic và hybrid smoke query đều kiểm tra được;
- mọi result trả release version và document/unit/chunk citation;
- active pointer không đổi khi stage/embed/validation thất bại.
- manifest phải báo zero chunk quá giới hạn, zero chunk thiếu provenance và
  zero table non-empty không có source span chính xác.

## Ngoài phạm vi

Ontology extraction, candidate claims, LLM, community detection, dynamic
fusion, multi-agent answer composition, checklist nghiệp vụ và patient Fact
Store không thuộc release này. LegalGraphRAG, MemGraphRAG, Youtu-GraphRAG và
OMD-GraphRAG chỉ là tài liệu tham khảo cho giai đoạn tương lai.
