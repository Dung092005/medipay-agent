"use client";

import { ReviewDetail } from "../../../components/admin/review-detail";
import { ReviewQueue } from "../../../components/admin/review-queue";
import { useReviewQueue } from "../../../components/admin/review-context";

export default function AdminReviewPage() {
  const {
    filteredReviews,
    selectedReview,
    selectedId,
    domainFilter,
    statusFilter,
    notice,
    setSelectedId,
    setDomainFilter,
    setStatusFilter,
    decide,
    dismissNotice,
  } = useReviewQueue();

  return (
    <main className="admin-review-page">
      <div className="admin-review-layout">
        <ReviewQueue reviews={filteredReviews} selectedId={selectedReview?.id ?? selectedId} domainFilter={domainFilter} statusFilter={statusFilter} onSelect={setSelectedId} onDomainFilter={setDomainFilter} onStatusFilter={setStatusFilter} />
        {selectedReview ? <ReviewDetail key={selectedReview.id} review={selectedReview} onDecision={decide} /> : (
          <div className="admin-empty-detail">
            <span className="admin-empty-icon" aria-hidden="true" />
            <h1>Không có thay đổi phù hợp</h1>
            <p>Điều chỉnh bộ lọc để xem các bản duyệt khác trong hàng đợi.</p>
          </div>
        )}
      </div>
      {notice ? <div className="admin-toast" role="status"><span aria-hidden="true">✓</span><span>{notice}</span><button type="button" aria-label="Đóng thông báo" onClick={dismissNotice}>Đóng</button></div> : null}
    </main>
  );
}
