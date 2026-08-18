"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { reviewFixtures } from "../../lib/review-mock-data";
import type { ReviewDetail, ReviewDomain, ReviewStatus } from "../../lib/review-types";

type ReviewDecision = "accepted" | "rejected";

type ReviewContextValue = {
  reviews: ReviewDetail[];
  filteredReviews: ReviewDetail[];
  selectedReview?: ReviewDetail;
  selectedId: string;
  domainFilter: "all" | ReviewDomain;
  statusFilter: "all" | ReviewStatus;
  pendingCount: number;
  notice: string;
  setSelectedId: (id: string) => void;
  setDomainFilter: (value: "all" | ReviewDomain) => void;
  setStatusFilter: (value: "all" | ReviewStatus) => void;
  decide: (status: ReviewDecision, note?: string) => void;
  dismissNotice: () => void;
};

const ReviewContext = createContext<ReviewContextValue | null>(null);

export function ReviewProvider({ children }: { children: React.ReactNode }) {
  const [reviews, setReviews] = useState<ReviewDetail[]>(reviewFixtures);
  const [selectedId, setSelectedId] = useState(reviewFixtures[0]?.id ?? "");
  const [domainFilter, setDomainFilter] = useState<"all" | ReviewDomain>("all");
  const [statusFilter, setStatusFilter] = useState<"all" | ReviewStatus>("all");
  const [notice, setNotice] = useState("");

  const filteredReviews = useMemo(
    () => reviews.filter((review) => (domainFilter === "all" || review.domain === domainFilter) && (statusFilter === "all" || review.status === statusFilter)),
    [reviews, domainFilter, statusFilter],
  );
  const selectedReview = filteredReviews.find((review) => review.id === selectedId) ?? filteredReviews[0];
  const pendingCount = reviews.filter((review) => review.status === "pending").length;

  const decide = useCallback((status: ReviewDecision, note?: string) => {
    if (!selectedReview) return;
    const selectedReviewId = selectedReview.id;
    setReviews((current) => current.map((review) => review.id === selectedReviewId ? {
      ...review,
      status,
      audit: [...review.audit, {
        id: `${review.id}-${status}-${review.audit.length + 1}`,
        action: status,
        actor: "Quản trị viên",
        at: "vừa xong",
        note,
      }],
    } : review));
    setNotice(status === "accepted" ? "Đã chấp nhận thay đổi và cập nhật trạng thái bản duyệt." : "Đã từ chối thay đổi và lưu lý do kiểm duyệt.");
  }, [selectedReview]);

  const dismissNotice = useCallback(() => setNotice(""), []);

  const value = useMemo<ReviewContextValue>(() => ({
    reviews,
    filteredReviews,
    selectedReview,
    selectedId,
    domainFilter,
    statusFilter,
    pendingCount,
    notice,
    setSelectedId,
    setDomainFilter,
    setStatusFilter,
    decide,
    dismissNotice,
  }), [reviews, filteredReviews, selectedReview, selectedId, domainFilter, statusFilter, pendingCount, notice, decide, dismissNotice]);

  return <ReviewContext.Provider value={value}>{children}</ReviewContext.Provider>;
}

export function useReviewQueue() {
  const context = useContext(ReviewContext);
  if (!context) throw new Error("useReviewQueue phải được dùng bên trong ReviewProvider.");
  return context;
}
