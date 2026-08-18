"use client";

import { ReviewDomain, ReviewListItem, ReviewStatus } from "../../lib/review-types";

type ReviewQueueProps = {
  reviews: ReviewListItem[];
  selectedId: string;
  domainFilter: "all" | ReviewDomain;
  statusFilter: "all" | ReviewStatus;
  onSelect: (id: string) => void;
  onDomainFilter: (value: "all" | ReviewDomain) => void;
  onStatusFilter: (value: "all" | ReviewStatus) => void;
};

function domainLabel(domain: ReviewDomain) {
  return domain === "legal_document" ? "Văn bản BHYT" : "Bảng phí OCR";
}

function statusLabel(status: ReviewStatus) {
  return status === "pending" ? "Chờ duyệt" : status === "accepted" ? "Đã chấp nhận" : "Đã từ chối";
}

export function ReviewQueue({ reviews, selectedId, domainFilter, statusFilter, onSelect, onDomainFilter, onStatusFilter }: ReviewQueueProps) {
  const pendingCount = reviews.filter((review) => review.status === "pending").length;

  return (
    <aside className="admin-review-queue" aria-label="Danh sách thay đổi chờ duyệt">
      <div className="admin-queue-heading">
        <div>
          <p className="admin-eyebrow">Hàng đợi kiểm duyệt</p>
          <h2>Thay đổi tri thức</h2>
        </div>
        <span className="admin-count-badge" aria-label={`${pendingCount} thay đổi chờ duyệt`}>{pendingCount}</span>
      </div>

      <div className="admin-filter-row">
        <label>
          <span className="sr-only">Lọc theo nguồn</span>
          <select value={domainFilter} onChange={(event) => onDomainFilter(event.target.value as "all" | ReviewDomain)}>
            <option value="all">Tất cả nguồn</option>
            <option value="legal_document">Văn bản BHYT</option>
            <option value="hospital_fee_ocr">Bảng phí OCR</option>
          </select>
        </label>
        <label>
          <span className="sr-only">Lọc theo trạng thái</span>
          <select value={statusFilter} onChange={(event) => onStatusFilter(event.target.value as "all" | ReviewStatus)}>
            <option value="all">Mọi trạng thái</option>
            <option value="pending">Chờ duyệt</option>
            <option value="accepted">Đã chấp nhận</option>
            <option value="rejected">Đã từ chối</option>
          </select>
        </label>
      </div>

      <div className="admin-review-list">
        {reviews.length === 0 ? <p className="admin-empty-state">Không có thay đổi phù hợp bộ lọc.</p> : reviews.map((review) => (
          <button className={`admin-review-item${review.id === selectedId ? " is-selected" : ""}`} key={review.id} type="button" onClick={() => onSelect(review.id)}>
            <span className="admin-review-item-topline">
              <span className={`admin-domain-mark ${review.domain === "legal_document" ? "is-law" : "is-ocr"}`} aria-hidden="true">{review.domain === "legal_document" ? "§" : "⌁"}</span>
              <span className="admin-review-domain">{domainLabel(review.domain)}</span>
              <span className={`admin-status-dot is-${review.status}`} />
            </span>
            <strong>{review.title}</strong>
            <span className="admin-review-file">{review.sourceName}</span>
            <span className="admin-review-item-meta">
              <span>{review.submittedAt}</span>
              <span>{review.changedFileCount} file</span>
              <span>{Math.round(review.confidence * 100)}% tin cậy</span>
            </span>
            <span className="admin-review-item-footer">
              <span className={`admin-status-label is-${review.status}`}>{statusLabel(review.status)}</span>
              <span>{review.flags[0]}</span>
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}
