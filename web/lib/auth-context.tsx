"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  onAuthStateChanged,
  signInWithPopup,
  signOut as firebaseSignOut,
} from "firebase/auth";
import { auth, authBypassEnabled, firebaseConfigured, googleProvider } from "./firebase";

export type UserRole = "admin" | "user";

export type AuthUser = {
  uid: string;
  email: string;
  displayName: string;
  photoURL: string | null;
  role: UserRole;
  idToken: string;
};

export type ServerStatus = "checking" | "waking" | "ready" | "error";

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  serverStatus: ServerStatus;
  warmupStep: number;
  warmupMessage: string;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
  warmupServer: () => Promise<void>;
  isAdmin: boolean;
  authBypass: boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const GOOGLE_CLIENT_ID =
  process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ||
  "419992923844-mfdcpr3l6bm4rs7bmd57m8lq7jr8lvgs.apps.googleusercontent.com";

const LOCAL_STORAGE_KEY = "bhyt_auth_user";

const LOCAL_GUEST: AuthUser = {
  uid: "dev-anonymous",
  email: "dev@localhost",
  displayName: "Khách trải nghiệm",
  photoURL: null,
  role: "user",
  idToken: "",
};

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [serverStatus, setServerStatus] = useState<ServerStatus>("checking");
  const [warmupStep, setWarmupStep] = useState(1);
  const [warmupMessage, setWarmupMessage] = useState("Đang kết nối máy chủ dịch vụ...");
  const warmupActiveRef = useRef(false);

  // 1. Initialize user from localStorage or Firebase
  useEffect(() => {
    // Check localStorage cache first
    try {
      const cached = localStorage.getItem(LOCAL_STORAGE_KEY);
      if (cached) {
        const parsed = JSON.parse(cached) as AuthUser;
        if (parsed?.uid && parsed?.email) {
          setUser(parsed);
          setLoading(false);
        }
      }
    } catch {
      // Ignore localStorage errors
    }

    if (authBypassEnabled) {
      setUser((prev) => prev || LOCAL_GUEST);
      setLoading(false);
      return;
    }

    if (firebaseConfigured && auth) {
      const unsubscribe = onAuthStateChanged(auth, async (firebaseUser) => {
        if (firebaseUser) {
          const idToken = await firebaseUser.getIdToken();
          const role = await fetchUserRole(firebaseUser.uid, idToken);
          const authUser: AuthUser = {
            uid: firebaseUser.uid,
            email: firebaseUser.email || "",
            displayName: firebaseUser.displayName || "Người dùng",
            photoURL: firebaseUser.photoURL,
            role,
            idToken,
          };
          setUser(authUser);
          try {
            localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(authUser));
          } catch {
            // ignore
          }
        } else {
          // If no cached user, clear
          if (!localStorage.getItem(LOCAL_STORAGE_KEY)) {
            setUser(null);
          }
        }
        setLoading(false);
      });
      return () => unsubscribe();
    } else {
      setLoading(false);
    }
  }, []);

  // 2. Server Warmup Background Poller
  const warmupServer = useCallback(async () => {
    if (warmupActiveRef.current) return;
    warmupActiveRef.current = true;

    setServerStatus("waking");
    setWarmupStep(1);
    setWarmupMessage("Đang đánh thức máy chủ đám mây (Render Instance)...");

    const maxAttempts = 40; // up to 80s for cold start
    let attempt = 0;

    const poll = async () => {
      while (attempt < maxAttempts) {
        attempt++;
        try {
          if (attempt > 3) {
            setWarmupStep(2);
            setWarmupMessage("Đang làm ấm bộ nhớ Vector & Kết nối dữ liệu BHYT...");
          }
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 4000);

          const res = await fetch(`${API_URL}/health`, {
            signal: controller.signal,
            cache: "no-store",
          }).catch(() => null);

          clearTimeout(timeoutId);

          if (res && (res.ok || res.status === 200)) {
            // Optionally pre-warm ready probe in background
            fetch(`${API_URL}/ready`, { cache: "no-store" }).catch(() => {});
            setWarmupStep(3);
            setWarmupMessage("Máy chủ và cơ sở tri thức đã sẵn sàng!");
            setServerStatus("ready");
            warmupActiveRef.current = false;
            return;
          }
        } catch {
          // continue polling
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
      // If reached max attempts, mark ready anyway so user is not permanently blocked
      setServerStatus("ready");
      warmupActiveRef.current = false;
    };

    poll();
  }, []);

  // Automatically trigger warmup on initial mount
  useEffect(() => {
    warmupServer();
  }, [warmupServer]);

  // 3. Google Sign In Handler (Supports GIS popup and Firebase popup)
  const signInWithGoogle = useCallback(async () => {
    if (authBypassEnabled) {
      setUser(LOCAL_GUEST);
      try {
        localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(LOCAL_GUEST));
      } catch {}
      return;
    }

    // Try Firebase first if fully configured
    if (firebaseConfigured && auth && googleProvider) {
      try {
        const result = await signInWithPopup(auth, googleProvider);
        const fbUser = result.user;
        const idToken = await fbUser.getIdToken();
        const role = await fetchUserRole(fbUser.uid, idToken);
        const authUser: AuthUser = {
          uid: fbUser.uid,
          email: fbUser.email || "",
          displayName: fbUser.displayName || "Người dùng",
          photoURL: fbUser.photoURL,
          role,
          idToken,
        };
        setUser(authUser);
        localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(authUser));
        return;
      } catch (err: any) {
        console.warn("Firebase popup failed or not set up, falling back to Google OAuth popup:", err);
      }
    }

    // Direct Google OAuth 2.0 Popup Flow
    return new Promise<void>((resolve, reject) => {
      const redirectUri = typeof window !== "undefined" ? window.location.origin : "http://localhost:3000";
      const oauthUrl = `https://accounts.google.com/o/oauth2/v2/auth?` +
        `client_id=${encodeURIComponent(GOOGLE_CLIENT_ID)}` +
        `&redirect_uri=${encodeURIComponent(redirectUri)}` +
        `&response_type=token%20id_token` +
        `&scope=${encodeURIComponent("openid email profile")}` +
        `&nonce=${crypto.randomUUID()}` +
        `&prompt=select_account`;

      const width = 500;
      const height = 600;
      const left = window.screenX + (window.outerWidth - width) / 2;
      const top = window.screenY + (window.outerHeight - height) / 2;

      const popup = window.open(
        oauthUrl,
        "GoogleSignIn",
        `width=${width},height=${height},left=${left},top=${top},status=no,toolbar=no,menubar=no`
      );

      if (!popup) {
        alert("Vui lòng cho phép popup để đăng nhập bằng Google.");
        reject(new Error("Popup blocked"));
        return;
      }

      // Check popup url interval for hash containing tokens
      const interval = setInterval(async () => {
        try {
          if (!popup || popup.closed) {
            clearInterval(interval);
            resolve();
            return;
          }

          if (popup.location.href.includes(redirectUri)) {
            const hash = popup.location.hash;
            if (hash) {
              const params = new URLSearchParams(hash.replace(/^#/, ""));
              const idToken = params.get("id_token") || "";
              const accessToken = params.get("access_token") || "";

              popup.close();
              clearInterval(interval);

              // Fetch Google User Profile using accessToken or decode idToken
              let profile: any = {};
              if (accessToken) {
                try {
                  const userRes = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
                    headers: { Authorization: `Bearer ${accessToken}` },
                  });
                  if (userRes.ok) {
                    profile = await userRes.json();
                  }
                } catch {
                  // ignore
                }
              }

              if (!profile.email && idToken) {
                try {
                  const payloadBase64 = idToken.split(".")[1];
                  profile = JSON.parse(atob(payloadBase64));
                } catch {
                  // ignore
                }
              }

              const authUser: AuthUser = {
                uid: profile.sub || profile.id || `google-${Date.now()}`,
                email: profile.email || "user@google.com",
                displayName: profile.name || profile.given_name || "Người dùng Google",
                photoURL: profile.picture || null,
                role: "user",
                idToken: idToken || accessToken,
              };

              setUser(authUser);
              try {
                localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(authUser));
              } catch {}

              // Also notify backend in background
              fetchUserRole(authUser.uid, authUser.idToken).catch(() => {});
              resolve();
            }
          }
        } catch {
          // Cross-origin access in popup until redirected back
        }
      }, 500);
    });
  }, []);

  const signOut = useCallback(async () => {
    try {
      localStorage.removeItem(LOCAL_STORAGE_KEY);
    } catch {}

    if (firebaseConfigured && auth) {
      try {
        await firebaseSignOut(auth);
      } catch {}
    }

    setUser(authBypassEnabled ? LOCAL_GUEST : null);
  }, []);

  const isAdmin = user?.role === "admin";

  const value = useMemo(
    () => ({
      user,
      loading,
      serverStatus,
      warmupStep,
      warmupMessage,
      signInWithGoogle,
      signOut,
      warmupServer,
      isAdmin,
      authBypass: authBypassEnabled,
    }),
    [user, loading, serverStatus, warmupStep, warmupMessage, signInWithGoogle, signOut, warmupServer, isAdmin]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

async function fetchUserRole(uid: string, idToken: string): Promise<UserRole> {
  if (!idToken) return "user";
  try {
    const res = await fetch(`${API_URL}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${idToken}` },
    });
    if (res.ok) {
      const data = await res.json();
      return data.role || "user";
    }
    if (res.status === 404) {
      const createRes = await fetch(`${API_URL}/api/v1/auth/register`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${idToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ uid }),
      });
      if (createRes.ok) {
        const data = await createRes.json();
        return data.role || "user";
      }
    }
    return "user";
  } catch {
    return "user";
  }
}
