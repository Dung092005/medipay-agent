"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  onAuthStateChanged,
  signInWithPopup,
  signOut as firebaseSignOut,
} from "firebase/auth";
import { auth, authBypassEnabled, firebaseConfigured, googleProvider } from "./firebase";

type UserRole = "admin" | "user";

type AuthUser = {
  uid: string;
  email: string;
  displayName: string;
  photoURL: string | null;
  role: UserRole;
  idToken: string;
};

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
  isAdmin: boolean;
  authBypass: boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const LOCAL_GUEST: AuthUser = {
  uid: "dev-anonymous",
  email: "dev@localhost",
  displayName: "Local Guest",
  photoURL: null,
  role: "user",
  idToken: "",
};

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(() => Boolean(auth) && !authBypassEnabled);

  useEffect(() => {
    if (authBypassEnabled || !firebaseConfigured || !auth) {
      setUser(LOCAL_GUEST);
      setLoading(false);
      return;
    }
    const unsubscribe = onAuthStateChanged(auth, async (firebaseUser) => {
      if (firebaseUser) {
        const idToken = await firebaseUser.getIdToken();
        const role = await fetchUserRole(firebaseUser.uid, idToken);
        setUser({
          uid: firebaseUser.uid,
          email: firebaseUser.email || "",
          displayName: firebaseUser.displayName || "",
          photoURL: firebaseUser.photoURL,
          role,
          idToken,
        });
      } else {
        setUser(null);
      }
      setLoading(false);
    });
    return () => unsubscribe();
  }, []);

  const signInWithGoogle = useCallback(async () => {
    if (authBypassEnabled) {
      setUser(LOCAL_GUEST);
      return;
    }
    if (!auth || !googleProvider) {
      throw new Error("Firebase Auth is not configured. Set NEXT_PUBLIC_FIREBASE_* or enable NEXT_PUBLIC_AUTH_BYPASS=true");
    }
    await signInWithPopup(auth, googleProvider);
  }, []);

  const signOut = useCallback(async () => {
    if (authBypassEnabled || !auth) {
      setUser(authBypassEnabled ? LOCAL_GUEST : null);
      return;
    }
    await firebaseSignOut(auth);
    setUser(null);
  }, []);

  const isAdmin = user?.role === "admin";

  const value = useMemo(
    () => ({
      user,
      loading,
      signInWithGoogle,
      signOut,
      isAdmin,
      authBypass: authBypassEnabled,
    }),
    [user, loading, signInWithGoogle, signOut, isAdmin]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

async function fetchUserRole(uid: string, idToken: string): Promise<UserRole> {
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
