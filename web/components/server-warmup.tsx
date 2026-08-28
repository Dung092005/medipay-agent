"use client";

import { useAuth } from "../lib/auth-context";

export function ServerWarmupOverlay() {
  const { serverStatus, warmupStep, warmupMessage } = useAuth();

  if (serverStatus === "ready") {
    return null;
  }

  const progressPercent = warmupStep === 1 ? 35 : warmupStep === 2 ? 75 : 100;

  return (
    <div className="server-warmup-overlay" aria-live="polite">
      <div className="server-warmup-card">
        {/* Glow backdrop effect */}
        <div className="warmup-glow" />

        {/* Brand Icon */}
        <div className="warmup-icon-wrapper">
          <div className="warmup-icon-pulse" />
          <div className="warmup-brand-mark">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2c.6 5 2 7.4 7 8-5 .6-6.4 3-7 8-.6-5-2-7.4-7-8 5-.6 6.4-3 7-8Z" />
              <path d="M19 16c.2 1.7.8 2.8 2.5 3-1.7.2-2.3 1.3-2.5 3-.2-1.7-.8-2.8-2.5-3 1.7-.2 2.3-1.3 2.5-3Z" />
            </svg>
          </div>
        </div>

        {/* Headings */}
        <div className="warmup-content">
          <span className="warmup-badge">
            <span className="warmup-badge-dot" />
            Đang khởi động hệ thống
          </span>
          <h2 className="warmup-title">Đang đánh thức máy chủ AI...</h2>
          <p className="warmup-subtitle">
            Hệ thống đang chuẩn bị môi trường suy luận và làm ấm cơ sở dữ liệu pháp luật BHYT để đảm bảo câu hỏi của bạn được phản hồi nhanh nhất.
          </p>
        </div>

        {/* Progress Bar */}
        <div className="warmup-progress-container">
          <div className="warmup-progress-bar" style={{ width: `${progressPercent}%` }} />
        </div>

        {/* Live Step Tracker */}
        <div className="warmup-steps">
          <div className={`warmup-step ${warmupStep >= 1 ? "active" : ""}`}>
            <span className="step-num">{warmupStep > 1 ? "✓" : "1"}</span>
            <span className="step-label">Khởi động máy chủ</span>
          </div>
          <div className={`warmup-step ${warmupStep >= 2 ? "active" : ""}`}>
            <span className="step-num">{warmupStep > 2 ? "✓" : "2"}</span>
            <span className="step-label">Làm ấm Vector & DB</span>
          </div>
          <div className={`warmup-step ${warmupStep >= 3 ? "active" : ""}`}>
            <span className="step-num">3</span>
            <span className="step-label">Sẵn sàng phản hồi</span>
          </div>
        </div>

        {/* Current status message */}
        <p className="warmup-status-msg">
          <span className="warmup-spinner" />
          {warmupMessage}
        </p>
      </div>
    </div>
  );
}
