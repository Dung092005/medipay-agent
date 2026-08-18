"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAdminAuth } from "./auth-context";
import { AdminShell } from "./admin-shell";

export function AdminRouteFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { isAuthenticated, isReady } = useAdminAuth();
  const isLoginRoute = pathname === "/admin/login";

  useEffect(() => {
    if (isReady && !isLoginRoute && !isAuthenticated) router.replace("/admin/login");
  }, [isAuthenticated, isLoginRoute, isReady, router]);

  if (isLoginRoute) return children;

  if (!isReady || !isAuthenticated) {
    return <main className="admin-auth-loading" aria-live="polite"><span className="admin-loading-mark" aria-hidden="true" /><p>Đang kiểm tra phiên quản trị...</p></main>;
  }

  return <AdminShell>{children}</AdminShell>;
}
