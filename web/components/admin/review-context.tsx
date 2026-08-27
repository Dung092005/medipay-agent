"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { decideAdminReview, fetchAdminReviews, type ReviewQueueItem } from "../../lib/api";
import { useAuth } from "../../lib/auth-context";
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

function toReviewDetail(item: ReviewQueueItem): ReviewDetail {
  const payload = item.payload as Partial<ReviewDetail>;
  const files = Array.isArray(payload.files) && payload.files.length ? payload.files : [{
    path: item.source_id || "review-payload",
    beforeLabel: "canonical",
    afterLabel: "review",
    lines: [],
    additions: 0,
    deletions: 0,
  }];
  return {
    id: item.review_id,
    domain: item.domain,
    title: item.title,
    sourceName: payload.sourceName || item.source_id,
    submittedAt: item.created_at,
    status: item.status,
    confidence: item.confidence,
    changedFileCount: item.payload.changedFileCount as number || files.length,
    flags: Array.isArray(payload.flags) ? payload.flags : [],
    summary: item.summary,
    branchLabel: payload.branchLabel || "release-review",
    submittedBy: item.submitted_by || "pipeline",
    files,
    chunks: Array.isArray(payload.chunks) ? payload.chunks : [],
    entities: Array.isArray(payload.entities) ? payload.entities : [],
    relations: Array.isArray(payload.relations) ? payload.relations : [],
    ocrFields: Array.isArray(payload.ocrFields) ? payload.ocrFields : [],
    audit: item.audit.map((event) => ({
      id: String(event.event_id || `${item.review_id}-${event.action}`),
      action: event.action as "submitted" | "accepted" | "rejected",
      actor: String(event.actor_uid || "admin"),
      at: String(event.created_at || ""),
      note: event.note ? String(event.note) : undefined,
    })),
  };
}

export function ReviewProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [reviews, setReviews] = useState<ReviewDetail[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [domainFilter, setDomainFilter] = useState<"all" | ReviewDomain>("all");
  const [statusFilter, setStatusFilter] = useState<"all" | ReviewStatus>("all");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (user?.role !== "admin") return;
    let cancelled = false;
    fetchAdminReviews("all", "all")
      .then((items) => {
        if (cancelled) return;
        const mapped = items.map(toReviewDetail);
        setReviews(mapped);
        setSelectedId(mapped[0]?.id ?? "");
      })
      .catch((error: unknown) => {
        if (!cancelled) setNotice(error instanceof Error ? error.message : "Không thể tải hàng đợi kiểm duyệt");
      });
    return () => { cancelled = true; };
  }, [user?.role]);

  const filteredReviews = useMemo(
    () => reviews.filter((review) => (domainFilter === "all" || review.domain === domainFilter) && (statusFilter === "all" || review.status === statusFilter)),
    [reviews, domainFilter, statusFilter],
  );
  const selectedReview = filteredReviews.find((review) => review.id === selectedId) ?? filteredReviews[0];
  const pendingCount = reviews.filter((review) => review.status === "pending").length;

  const decide = useCallback((status: ReviewDecision, note?: string) => {
    if (!selectedReview) return;
    const selectedReviewId = selectedReview.id;
    void decideAdminReview(selectedReviewId, status, note)
      .then((updated) => {
        setReviews((current) => current.map((review) => review.id === selectedReviewId ? toReviewDetail(updated) : review));
        setNotice(status === "accepted" ? "Đã chấp nhận thay đổi và cập nhật trạng thái bản duyệt." : "Đã từ chối thay đổi và lưu lý do kiểm duyệt.");
      })
      .catch((error: unknown) => setNotice(error instanceof Error ? error.message : "Không thể cập nhật bản kiểm duyệt"));
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
