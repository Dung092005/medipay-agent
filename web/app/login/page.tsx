"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../../lib/auth-context";

export default function LoginPage() {
  const router = useRouter();
  const { user, loading, signInWithGoogle } = useAuth();

  useEffect(() => {
    if (!loading && user) {
      router.replace(user.role === "admin" ? "/admin/review" : "/");
    }
  }, [user, loading, router]);

  if (loading) {
    return (
      <main className="login-page">
        <div className="login-card">
          <div className="login-spinner" />
          <p>Đang tải...</p>
        </div>
      </main>
    );
  }

  if (user) return null;

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2c.6 5 2 7.4 7 8-5 .6-6.4 3-7 8-.6-5-2-7.4-7-8 5-.6 6.4-3 7-8Z" />
            <path d="M19 16c.2 1.7.8 2.8 2.5 3-1.7.2-2.3 1.3-2.5 3-.2-1.7-.8-2.8-2.5-3 1.7-.2 2.3-1.3 2.5-3Z" />
          </svg>
          <div>
            <strong>BHYT AI</strong>
            <small>Trợ lý y tế số</small>
          </div>
        </div>

        <div className="login-heading">
          <p>Chào mừng bạn</p>
          <h1 id="login-title">Đăng nhập</h1>
          <span>Sử dụng tài khoản Google để truy cập hệ thống.</span>
        </div>

        <button className="login-google-btn" type="button" onClick={signInWithGoogle}>
          <svg viewBox="0 0 24 24" width="20" height="20">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
          </svg>
          Đăng nhập bằng Google
        </button>

        <p className="login-notice">
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 11v5M12 8h.01" />
          </svg>
          Chỉ tài khoản Google được cấp phép mới có thể truy cập.
        </p>
      </section>
    </main>
  );
}
