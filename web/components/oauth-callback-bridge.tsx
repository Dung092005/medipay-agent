"use client";

import { useEffect, useRef } from "react";

/**
 * When the Google OAuth popup redirects back to our own origin, the returned
 * tokens live in the URL hash. If we let the full app boot inside that popup,
 * `AuthRoute` immediately navigates to /login and wipes the hash before the
 * opener can read it — which is why login used to require two clicks.
 *
 * This bridge runs before the rest of the tree. If it detects that the current
 * window is the OAuth popup (has an opener + tokens in the hash), it forwards
 * the hash to the opener via postMessage and closes itself, without ever
 * mounting the auth-guarded app.
 */
export function OAuthCallbackBridge({ children }: { children: React.ReactNode }) {
  const infoRef = useRef<{ isCallback: boolean; hash: string } | null>(null);

  if (infoRef.current === null) {
    let isCallback = false;
    let hash = "";
    if (typeof window !== "undefined") {
      try {
        hash = window.location.hash || "";
        isCallback =
          Boolean(window.opener) &&
          window.opener !== window &&
          /[#&](access_token|id_token)=/.test(hash);
      } catch {
        isCallback = false;
      }
    }
    infoRef.current = { isCallback, hash };
  }

  const { isCallback, hash } = infoRef.current;

  useEffect(() => {
    if (!isCallback) return;
    try {
      window.opener?.postMessage(
        { type: "google-oauth-callback", hash },
        window.location.origin
      );
    } catch {
      // ignore
    }
    try {
      window.close();
    } catch {
      // ignore
    }
  }, [isCallback, hash]);

  if (isCallback) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          fontFamily: "system-ui, -apple-system, sans-serif",
          color: "#334155",
          fontSize: "0.95rem",
        }}
      >
        Đang hoàn tất đăng nhập, vui lòng đợi…
      </div>
    );
  }

  return <>{children}</>;
}
