"use client";

import { FormEvent, useState } from "react";
import { ReviewStatus } from "../../lib/review-types";

type DecisionBarProps = {
  status: ReviewStatus;
  onDecision: (status: "accepted" | "rejected", note?: string) => void;
};

export function DecisionBar({ status, onDecision }: DecisionBarProps) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  function submitReject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (reason.trim().length < 5) {
      setError("Nêu ít nhất 5 ký tự để người gửi biết cần sửa gì.");
      return;
    }
    onDecision("rejected", reason.trim());
    setRejecting(false);
  }

  if (status !== "pending") {
    return <div className={`admin-decision-bar is-resolved is-${status}`}><span className="admin-decision-icon">{status === "accepted" ? "✓" : "!"}</span><div><strong>{status === "accepted" ? "Đã chấp nhận thay đổi" : "Đã từ chối thay đổi"}</strong><p>Trạng thái mô phỏng đã được cập nhật trong phiên làm việc hiện tại.</p></div></div>;
  }

  return <div className="admin-decision-bar">
    <div><p className="admin-eyebrow">Quyết định kiểm duyệt</p><strong>Xác nhận trạng thái cho thay đổi này</strong><p>Quyết định sẽ cập nhật hàng đợi và nhật ký trong phiên mô phỏng.</p></div>
    {rejecting ? <form className="admin-reject-form" onSubmit={submitReject}>
      <label htmlFor="reject-reason">Lý do từ chối</label>
      <textarea id="reject-reason" value={reason} onChange={(event) => { setReason(event.target.value); setError(""); }} placeholder="Ví dụ: kiểm tra lại đơn giá OCR..." rows={2} autoFocus />
      {error ? <p className="admin-form-error" role="alert">{error}</p> : null}
      <div className="admin-decision-actions"><button className="admin-button admin-button-quiet" type="button" onClick={() => setRejecting(false)}>Hủy</button><button className="admin-button admin-button-danger" type="submit">Xác nhận từ chối</button></div>
    </form> : <div className="admin-decision-actions"><button className="admin-button admin-button-danger" type="button" onClick={() => setRejecting(true)}>Từ chối</button><button className="admin-button admin-button-primary" type="button" onClick={() => onDecision("accepted")}>Chấp nhận thay đổi</button></div>}
  </div>;
}
