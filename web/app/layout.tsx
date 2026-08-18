import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "MediPay Agent",
  description: "Trợ lý hành chính y tế cho BHYT và thanh toán viện phí.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body>{children}</body>
    </html>
  );
}
