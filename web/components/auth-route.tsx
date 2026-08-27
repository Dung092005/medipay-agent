"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../lib/auth-context";

export function AuthRoute({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [user, loading, router]);

  if (loading) {
    return (
      <main className="auth-loading" aria-live="polite">
        <div className="login-spinner" />
        <p>Đang kiểm tra đăng nhập...</p>
      </main>
    );
  }

  if (!user) return null;

  return <>{children}</>;
}
