"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAdminAuth } from "../../../components/admin/auth-context";

export default function AdminLoginPage() {
  const router = useRouter();
  const { isAuthenticated, isReady, isAdmin, signInWithGoogle } = useAdminAuth();

  useEffect(() => {
    if (isReady && isAuthenticated && isAdmin) {
      router.replace("/admin/review");
    } else if (isReady && isAuthenticated && !isAdmin) {
      router.replace("/");
    }
  }, [isAuthenticated, isReady, isAdmin, router]);

  if (isReady && isAuthenticated && isAdmin) return null;

  return (
    <main className="admin-login-page">
      <section className="admin-login-card" aria-labelledby="admin-login-title">
        <div className="admin-login-brand">
          <span><LoginIcon name="shield" /></span>
          <div><strong>BHYT Admin Portal</strong><small>Hệ thống quản trị tri thức</small></div>
        </div>
        <div className="admin-login-heading">
          <p>Truy cập được kiểm soát</p>
          <h1 id="admin-login-title">Đăng nhập quản trị</h1>
          <span>Dành cho cán bộ được phân quyền duyệt và vận hành kho tri thức BHYT.</span>
        </div>

        <button className="admin-login-google" type="button" onClick={() => signInWithGoogle()}>
          <svg viewBox="0 0 24 24" width="20" height="20">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
          </svg>
          Đăng nhập bằng Google
        </button>

        <p className="admin-login-notice"><LoginIcon name="info" />Chỉ tài khoản Google được cấp quyền admin mới truy cập được trang này.</p>
      </section>
    </main>
  );
}

type LoginIconName = "info" | "shield";

function LoginIcon({ name }: { name: LoginIconName }) {
  const paths: Record<LoginIconName, React.ReactNode> = {
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></>,
    shield: <><path d="M12 2 20 5v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z"/><path d="m9 12 2 2 4-5"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
