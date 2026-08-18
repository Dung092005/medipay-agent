import type { Metadata } from "next";
import { AdminAuthProvider } from "../../components/admin/auth-context";
import { AdminRouteFrame } from "../../components/admin/admin-route-frame";
import { ReviewProvider } from "../../components/admin/review-context";
import "./admin.css";

export const metadata: Metadata = {
  title: "BHYT Admin Portal",
  description: "Cổng quản trị và duyệt tri thức BHYT.",
};

export default function AdminLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <AdminAuthProvider><ReviewProvider><AdminRouteFrame>{children}</AdminRouteFrame></ReviewProvider></AdminAuthProvider>;
}
