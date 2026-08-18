"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAdminAuth } from "../../../components/admin/auth-context";

export default function AdminLoginPage() {
  const router = useRouter();
  const { isAuthenticated, isReady, login } = useAdminAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (isReady && isAuthenticated && !success) router.replace("/admin/review");
  }, [isAuthenticated, isReady, router, success]);

  function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (!username.trim() || !password) {
      setError("Vui lòng nhập đầy đủ tài khoản và mật khẩu.");
      return;
    }

    const result = login(username, password);
    if (!result.success) {
      setError(result.message);
      return;
    }

    setSuccess(true);
    window.setTimeout(() => router.replace("/admin/review"), 650);
  }

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

        <form className="admin-login-form" onSubmit={submitLogin} noValidate>
          <label htmlFor="admin-username">Tài khoản</label>
          <div className="admin-login-field"><LoginIcon name="user" /><input id="admin-username" name="username" value={username} onChange={(event) => { setUsername(event.target.value); setError(""); }} autoComplete="username" placeholder="Nhập tài khoản quản trị" aria-invalid={Boolean(error)} autoFocus /></div>

          <label htmlFor="admin-password">Mật khẩu</label>
          <div className="admin-login-field"><LoginIcon name="lock" /><input id="admin-password" name="password" type={showPassword ? "text" : "password"} value={password} onChange={(event) => { setPassword(event.target.value); setError(""); }} autoComplete="current-password" placeholder="Nhập mật khẩu" aria-invalid={Boolean(error)} /><button type="button" aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"} aria-pressed={showPassword} onClick={() => setShowPassword((current) => !current)}><LoginIcon name={showPassword ? "eyeOff" : "eye"} /></button></div>

          {error ? <p className="admin-login-error" role="alert"><LoginIcon name="alert" />{error}</p> : null}
          <button className="admin-login-submit" type="submit" disabled={success}>{success ? "Đăng nhập thành công" : "Đăng nhập vào hệ thống quản trị"}<LoginIcon name={success ? "check" : "arrow"} /></button>
        </form>

        <Link className="admin-login-back" href="/"><LoginIcon name="back" />Quay lại trang chủ người dùng</Link>
        <p className="admin-login-notice"><LoginIcon name="info" />Đây là môi trường xác thực mô phỏng dành cho bản demo nội bộ.</p>
      </section>
      {success ? <div className="admin-login-toast" role="status"><LoginIcon name="check" /><span><strong>Đăng nhập thành công</strong><small>Đang chuyển tới hàng đợi duyệt...</small></span></div> : null}
    </main>
  );
}

type LoginIconName = "alert" | "arrow" | "back" | "check" | "eye" | "eyeOff" | "info" | "lock" | "shield" | "user";

function LoginIcon({ name }: { name: LoginIconName }) {
  const paths: Record<LoginIconName, React.ReactNode> = {
    alert: <><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/></>,
    arrow: <><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></>,
    back: <><path d="M19 12H5"/><path d="m11 18-6-6 6-6"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    eye: <><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></>,
    eyeOff: <><path d="m3 3 18 18"/><path d="M10.6 6.2c.5-.1.9-.2 1.4-.2 6.5 0 10 6 10 6a18 18 0 0 1-2.1 2.8M6.6 6.6A16.8 16.8 0 0 0 2 12s3.5 6 10 6c1.7 0 3.2-.4 4.4-1"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></>,
    shield: <><path d="M12 2 20 5v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z"/><path d="m9 12 2 2 4-5"/></>,
    user: <><circle cx="12" cy="8" r="3"/><path d="M5 21a7 7 0 0 1 14 0"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
