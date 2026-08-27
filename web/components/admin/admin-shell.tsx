"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useAuth } from "../../lib/auth-context";
import { useReviewQueue } from "./review-context";

type AdminIconName = "archive" | "chevron" | "graph" | "logout" | "queue" | "settings" | "shield" | "user";

const unavailableModules: { label: string; description: string; icon: AdminIconName }[] = [
  { label: "Kho văn bản nguồn", description: "Đang phát triển", icon: "archive" },
  { label: "Đồ thị tri thức", description: "Đang phát triển", icon: "graph" },
  { label: "Cài đặt hệ thống", description: "Đang phát triển", icon: "settings" },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, signOut } = useAuth();
  const { pendingCount, selectedReview } = useReviewQueue();

  function handleLogout() {
    signOut();
    router.replace("/login");
  }

  const recordLabel = selectedReview?.domain === "hospital_fee_ocr"
    ? `Bảng kê #${selectedReview.title.match(/\d+/)?.[0] ?? selectedReview.id}`
    : selectedReview?.sourceName ?? "Bản ghi";

  return (
    <div className="admin-portal">
      <aside className="admin-shell-sidebar" aria-label="Điều hướng quản trị">
        <Link className="admin-shell-brand" href="/admin/review" aria-label="BHYT Knowledge Engine">
          <span className="admin-shell-brand-mark"><AdminIcon name="shield" /></span>
          <span><strong>BHYT Knowledge</strong><small>Engine Administration</small></span>
        </Link>

        <nav className="admin-shell-nav">
          <p>Không gian làm việc</p>
          <Link className={`admin-shell-nav-item${pathname === "/admin/review" ? " is-active" : ""}`} href="/admin/review">
            <AdminIcon name="queue" />
            <span><strong>Hàng đợi duyệt</strong><small>Review Queue</small></span>
            <em aria-label={`${pendingCount} bản chờ duyệt`}>{pendingCount}</em>
          </Link>
          {unavailableModules.map((module) => (
            <button className="admin-shell-nav-item" key={module.label} type="button" disabled aria-disabled="true" title={`${module.label}: ${module.description}`}>
              <AdminIcon name={module.icon} />
              <span><strong>{module.label}</strong><small>{module.description}</small></span>
            </button>
          ))}
        </nav>

        <div className="admin-shell-sidebar-footer">
          <div className="admin-shell-security"><AdminIcon name="shield" /><span><strong>Phiên quản trị nội bộ</strong><small>Mock authentication</small></span></div>
          <button type="button" onClick={handleLogout}><AdminIcon name="logout" /><span>Đăng xuất</span></button>
        </div>
      </aside>

      <div className="admin-shell-main">
        <header className="admin-shell-header">
          <nav className="admin-shell-breadcrumb" aria-label="Breadcrumb">
            <span>Admin</span><AdminIcon name="chevron" /><span>Duyệt tri thức</span><AdminIcon name="chevron" /><strong>{recordLabel}</strong>
          </nav>
          <div className="admin-shell-header-actions">
            <span className="admin-dataset-tag">Local Dataset v0.1</span>
            <div className="admin-profile"><span><AdminIcon name="user" /></span><div><strong>{user?.displayName || "Quản trị viên"}</strong><small>{user?.email || "Admin"}</small></div></div>
            <button className="admin-header-logout" type="button" onClick={handleLogout}><AdminIcon name="logout" /><span>Đăng xuất</span></button>
          </div>
        </header>
        <div className="admin-shell-content">{children}</div>
      </div>
    </div>
  );
}

function AdminIcon({ name }: { name: AdminIconName }) {
  const paths: Record<AdminIconName, ReactNode> = {
    archive: <><path d="M4 7h16v13H4z"/><path d="M3 4h18v3H3zM9 11h6"/></>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    graph: <><circle cx="5" cy="12" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="m7 11 9-4M7 13l9 4"/></>,
    logout: <><path d="M10 5H5v14h5M14 8l4 4-4 4M8 12h10"/></>,
    queue: <><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
    shield: <><path d="M12 2 20 5v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z"/><path d="m9 12 2 2 4-5"/></>,
    user: <><circle cx="12" cy="8" r="3"/><path d="M5 21a7 7 0 0 1 14 0"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
