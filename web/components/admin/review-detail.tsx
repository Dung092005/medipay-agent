"use client";

import { useState } from "react";
import { ChangedFile, KnowledgeTab, ReviewDetail as ReviewDetailType } from "../../lib/review-types";
import { DecisionBar } from "./decision-bar";
import { DiffViewer } from "./diff-viewer";
import { KnowledgePreview } from "./knowledge-preview";

type ReviewDetailProps = {
  review: ReviewDetailType;
  onDecision: (status: "accepted" | "rejected", note?: string) => void;
};

export function ReviewDetail({ review, onDecision }: ReviewDetailProps) {
  const [activeFile, setActiveFile] = useState<ChangedFile>(review.files[0]);
  const [activeTab, setActiveTab] = useState<KnowledgeTab>(review.domain === "hospital_fee_ocr" ? "ocr" : "chunks");

  return <div className="admin-review-detail">
    <header className="admin-detail-header">
      <div>
        <div className="admin-detail-title-row"><span className={`admin-domain-mark large ${review.domain === "legal_document" ? "is-law" : "is-ocr"}`}>{review.domain === "legal_document" ? "§" : "⌁"}</span><div><p className="admin-eyebrow">{review.domain === "legal_document" ? "Văn bản pháp quy" : "Bảng kê viện phí · OCR"}</p><h1>{review.title}</h1></div><span className={`admin-status-label is-${review.status}`}>{review.status === "pending" ? "Chờ duyệt" : review.status === "accepted" ? "Đã chấp nhận" : "Đã từ chối"}</span></div>
        <p className="admin-detail-summary">{review.summary}</p>
      </div>
      <div className="admin-detail-meta"><span>Đề xuất bởi <strong>{review.submittedBy}</strong></span><span>{review.submittedAt}</span><span className="admin-branch-label">⌘ {review.branchLabel}</span></div>
    </header>

    <div className="admin-change-summary"><span className="admin-change-icon">±</span><div><strong>{review.changedFileCount} file thay đổi</strong><span>Đề xuất cập nhật source và dữ liệu tri thức.</span></div><div className="admin-summary-stats"><span className="is-addition">+{review.files.reduce((sum, file) => sum + file.additions, 0)}</span><span className="is-deletion">−{review.files.reduce((sum, file) => sum + file.deletions, 0)}</span></div></div>

    <div className="admin-file-tabs" role="tablist" aria-label="File thay đổi">
      {review.files.map((file) => <button className={activeFile.path === file.path ? "is-active" : ""} key={file.path} type="button" role="tab" aria-selected={activeFile.path === file.path} onClick={() => setActiveFile(file)}><span>▱</span>{file.path}<small>+{file.additions} −{file.deletions}</small></button>)}
    </div>

    <div className="admin-review-columns"><div className="admin-source-column"><DiffViewer file={activeFile} /></div><div className="admin-evidence-column"><KnowledgePreview review={review} activeTab={activeTab} onTabChange={setActiveTab} /></div></div>
    <DecisionBar status={review.status} onDecision={onDecision} />
    <div className="admin-audit-line"><span className="admin-eyebrow">Audit trail</span>{review.audit.map((event) => <span key={event.id}><strong>{event.actor}</strong> {event.action === "submitted" ? "đã gửi" : event.action === "accepted" ? "đã chấp nhận" : "đã từ chối"} · {event.at}{event.note ? ` · ${event.note}` : ""}</span>)}</div>
  </div>;
}
