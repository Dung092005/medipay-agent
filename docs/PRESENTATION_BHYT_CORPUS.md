# MediPay BHYT Corpus — slide data

**Release active:** `snapshot-5dfc6bb64d046a1c`  
**Chi tiết kiến trúc / cách nói:** `docs/HUONG_DAN_THUYET_TRINH.md`

## One-sentence pitch

Chatbot BHYT/viện phí trả lời từ corpus pháp lý đã kiểm chứng: **Trung ương đầy đủ**
+ **địa phương có trong bộ dữ liệu** (ưu tiên vận hành Hà Nội), truy xuất đa kênh
trên Postgres · Qdrant · Neo4j.

## Serving hiện tại

| Thành phần | Số lượng |
|---|---:|
| Documents | **318** |
| Trung ương (phạm vi) | **~65** |
| Địa phương (gồm HN / ĐN / HCM…) | phần còn lại |
| Hà Nội (`province=Hà Nội`) | **6** |
| Chunks (Postgres) | **~11 546** |
| Vectors (Qdrant) | **~10 112** |
| Relationships (Neo4j) | **173** |

## Module Hà Nội (6) — hay demo

| Số hiệu | Vai trò |
|---|---|
| 19/2025/NQ-HĐND | Mức hỗ trợ đóng BHYT |
| 17/2026/NQ-HĐND | Sửa mức hỗ trợ |
| 14/2026/NQ-HĐND | An sinh thủ đô |
| 321/KH-UBND | Kế hoạch triển khai |
| SYT KCB ban đầu 2026 | Đăng ký nơi KCB ban đầu tại HN |
| 91/2026/NQ-HĐND | Khung giá DV KCB |

## Design principle (nói trên slide)

```text
Corpus CSV (metadata + content + relationships)
        │
        ▼ ingest / embed / activate cùng một dataset_id
Postgres (chữ + FTS) + Qdrant (semantic) + Neo4j (quan hệ)
        │
        ▼ runtime
Seed đa kênh → Expand → Rerank / routing tỉnh → Generate + citation
```

- Câu quốc gia (trái tuyến, mức hưởng Luật/NĐ) → Trung ương trước.
- Câu nêu tỉnh hoặc hỗ trợ/giá/đăng ký địa phương → boost đúng tỉnh (mặc định ưu tiên HN khi phù hợp).

## Câu demo gợi ý

1. `xin chào` — xã giao, không dump luật  
2. `Khám trái tuyến được hưởng BHYT như thế nào?` — Trung ương  
3. `Hỗ trợ đăng ký khám chữa bệnh ban đầu tại Hà Nội` — ưu tiên Hà Nội  

Số liệu release mới nhất: `docs/SERVING_CORPUS.md`.
