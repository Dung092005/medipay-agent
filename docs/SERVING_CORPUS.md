# Serving corpus — MediPay (Trung ương + địa phương / Hà Nội)

Ngày build gần nhất: 2026-08-25

## Release đang phục vụ

| | |
|---|---|
| Canonical / dataset id | `snapshot-5dfc6bb64d046a1c` |
| Thư mục CSV | `data/clean/serving_p151_hanoi/` |
| Documents | 318 |
| Relationships | 173 |
| `answer_ready` | 314 |
| Postgres chunks | ~11 546 |
| Qdrant vectors | ~10 112 (alias `medical_legal_active`) |
| Neo4j | 318 nodes / 173 edges |

Phân bố phạm vi: ~65 Trung ương; phần còn lại địa phương (gồm Hà Nội, Đà Nẵng,
Hồ Chí Minh khi metadata nêu rõ). Module Hà Nội bổ sung/ghi đè một số id trùng.

## Routing địa phương (runtime)

- Có nêu tỉnh/thành → ưu tiên tỉnh đó + Trung ương; hạ địa phương khác.
- Không nêu tỉnh → câu quốc gia (trái tuyến, mức hưởng Luật/NĐ…) lấy Trung ương trước;
  chỉ boost Hà Nội khi hỏi hỗ trợ / giá / đăng ký KCB địa phương.

Chi tiết kiến trúc & cách giải thích khi thuyết trình:
xem `docs/HUONG_DAN_THUYET_TRINH.md`.
