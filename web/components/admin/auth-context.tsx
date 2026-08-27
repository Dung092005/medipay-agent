"use client";

import { createContext, useCallback, useContext, useMemo } from "react";
import { useAuth } from "../../lib/auth-context";

type LoginResult = { success: true } | { success: false; message: string };

type AdminAuthContextValue = {
  isAuthenticated: boolean;
  isReady: boolean;
  isAdmin: boolean;
  signInWithGoogle: () => Promise<void>;
  login: (username: string, password: string) => LoginResult;
  logout: () => void;
  user: { email: string; displayName: string; photoURL: string | null } | null;
};

const AdminAuthContext = createContext<AdminAuthContextValue | null>(null);

export function AdminAuthProvider({ children }: { children: React.ReactNode }) {
  const { user, loading, signInWithGoogle, signOut, isAdmin } = useAuth();

  const isAuthenticated = !loading && !!user;
  const isReady = !loading;

  const login = useCallback((username: string, password: string): LoginResult => {
    void username;
    void password;
    return { success: false, message: "Vui lòng đăng nhập bằng tài khoản Google." };
  }, []);

  const logout = useCallback(() => {
    signOut();
  }, [signOut]);

  const value = useMemo(
    () => ({
      isAuthenticated,
      isReady,
      isAdmin,
      signInWithGoogle,
      login,
      logout,
      user: user ? { email: user.email, displayName: user.displayName, photoURL: user.photoURL } : null,
    }),
    [isAuthenticated, isReady, isAdmin, signInWithGoogle, login, logout, user]
  );

  return <AdminAuthContext.Provider value={value}>{children}</AdminAuthContext.Provider>;
}

export function useAdminAuth() {
  const context = useContext(AdminAuthContext);
  if (!context) throw new Error("useAdminAuth must be used inside AdminAuthProvider.");
  return context;
}
