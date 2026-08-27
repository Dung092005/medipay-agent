# Git Guideline

Tài liệu này quy định cách quản lý mã nguồn, đặt tên nhánh, đặt tên commit và quy trình làm việc của nhóm.

---

# 1. Quy định về nhánh

## main

Nhánh chính thức của dự án.

- Chứa mã nguồn ổn định.
- Sẵn sàng triển khai (deploy).
- Không được commit trực tiếp vào nhánh này.

---

## develop

Nhánh phát triển chính.

- Dùng để tổng hợp các tính năng mới.
- Chỉ nhận mã nguồn đã được kiểm tra.
- Tất cả Pull Request (PR) đều được gộp vào nhánh này trước.

---

## feature/*

Dùng để phát triển tính năng mới.

Ví dụ:

```bash
feature/login
feature/chatbot
feature/rag-pipeline
feature/bhyt-ocr
```

---

## fix/*

Dùng để sửa lỗi.

Ví dụ:

```bash
fix/login-error
fix/database-timeout
fix/pdf-parser
```

---

# 2. Quy định về commit

## Ngôn ngữ

- Toàn bộ commit phải được viết bằng tiếng Anh.
- Sử dụng động từ ở thì hiện tại.
- Nội dung phải ngắn gọn và dễ hiểu.

---

## Cấu trúc

```text
<type>: <description>
```

---

## Các loại commit

| Type | Ý nghĩa |
|------|----------|
| feat | Thêm tính năng mới |
| fix | Sửa lỗi |
| docs | Cập nhật tài liệu |
| style | Chỉnh sửa định dạng |
| refactor | Cải thiện mã nguồn |
| test | Viết hoặc chỉnh sửa bài kiểm thử |
| chore | Công việc bảo trì hệ thống |

---

## Ví dụ

```bash
feat: add OCR extraction service

feat: implement graph RAG pipeline

fix: resolve database connection error

fix: correct API response format

docs: update README

refactor: optimize OCR processing flow

test: add unit tests

chore: update dependencies
```

---

## Những commit không được chấp nhận

```bash
update

fix

code

123

test
```

---

# 3. Quy trình làm việc

## Bước 1

Lấy mã nguồn mới nhất.

```bash
git checkout develop
git pull origin develop
```

---

## Bước 2

Tạo nhánh mới.

```bash
git checkout -b feature/bhyt-ocr
```

---

## Bước 3

Lập trình và commit.

```bash
git add .
git commit -m "feat: add OCR extraction service"
```

---

## Bước 4

Đẩy mã nguồn lên GitHub.

```bash
git push origin feature/bhyt-ocr
```

---

## Bước 5

Tạo Pull Request (PR).

```text
feature/bhyt-ocr
        │
        ▼
     develop
```

---

## Bước 6

Review mã nguồn.

Người review cần kiểm tra:

- Logic xử lý.
- Khả năng hoạt động của chương trình.
- Quy tắc đặt tên.
- Nội dung commit.

---

## Bước 7

Gộp mã nguồn.

```text
feature/*
      │
      ▼
develop
      │
      ▼
main
```

---

# 4. Quy tắc chung

- Không commit trực tiếp vào `main`.
- Không push mã nguồn chưa được kiểm tra.
- Mỗi Pull Request chỉ nên tập trung vào một chức năng.
- Mỗi commit chỉ nên giải quyết một vấn đề.
- Luôn đồng bộ nhánh `develop` trước khi bắt đầu công việc mới.