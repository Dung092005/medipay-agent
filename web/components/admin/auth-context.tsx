"use client";

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";

const ADMIN_SESSION_KEY = "bhyt-admin-session";
const ADMIN_SESSION_VALUE = "mock-admin-authenticated";
const AUTH_EVENT = "bhyt-admin-auth-change";

type LoginResult = { success: true } | { success: false; message: string };

type AdminAuthContextValue = {
  isAuthenticated: boolean;
  isReady: boolean;
  login: (username: string, password: string) => LoginResult;
  logout: () => void;
};

const AdminAuthContext = createContext<AdminAuthContextValue | null>(null);

function subscribeToAuth(onStoreChange: () => void) {
  window.addEventListener("storage", onStoreChange);
  window.addEventListener(AUTH_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener(AUTH_EVENT, onStoreChange);
  };
}

function getAuthSnapshot() {
  return window.localStorage.getItem(ADMIN_SESSION_KEY) === ADMIN_SESSION_VALUE;
}

function getServerAuthSnapshot() {
  return false;
}

function subscribeToHydration() {
  return () => undefined;
}

function getClientHydrationSnapshot() {
  return true;
}

function getServerHydrationSnapshot() {
  return false;
}

function notifyAuthChange() {
  window.dispatchEvent(new Event(AUTH_EVENT));
}

export function AdminAuthProvider({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useSyncExternalStore(subscribeToAuth, getAuthSnapshot, getServerAuthSnapshot);
  const isReady = useSyncExternalStore(subscribeToHydration, getClientHydrationSnapshot, getServerHydrationSnapshot);

  const login = useCallback((username: string, password: string): LoginResult => {
    if (username.trim() !== "admin" || password !== "admin") {
      return { success: false, message: "Tài khoản hoặc mật khẩu không chính xác." };
    }

    window.localStorage.setItem(ADMIN_SESSION_KEY, ADMIN_SESSION_VALUE);
    notifyAuthChange();
    return { success: true };
  }, []);

  const logout = useCallback(() => {
    window.localStorage.removeItem(ADMIN_SESSION_KEY);
    notifyAuthChange();
  }, []);

  const value = useMemo(() => ({ isAuthenticated, isReady, login, logout }), [isAuthenticated, isReady, login, logout]);

  return <AdminAuthContext.Provider value={value}>{children}</AdminAuthContext.Provider>;
}

export function useAdminAuth() {
  const context = useContext(AdminAuthContext);
  if (!context) throw new Error("useAdminAuth phải được dùng bên trong AdminAuthProvider.");
  return context;
}
