import type { ReactNode } from "react";
import AtmosphericFriction from "./atmospheric-friction";
import SiteHeader, { type SiteHeaderLink } from "./site-header";

const dashboardLinks: SiteHeaderLink[] = [
  { label: "Check a caller", href: "/check", style: "login" },
  { label: "Past calls", href: "/past", style: "login" },
  { label: "Home", href: "/", style: "login" },
  { label: "Start inspection", href: "/inspect", style: "button" },
];

export default function DashboardShell({
  children,
  particles = true,
  particleCount = 350,
  context,
}: {
  children: ReactNode;
  particles?: boolean;
  particleCount?: number;
  context?: ReactNode;
}) {
  return (
    <div className="dashboard-shell dashboard-shell-v2">
      <div className="dashboard-glow" aria-hidden="true" />
      {particles && <AtmosphericFriction className="dashboard-atmosphere" particleCount={particleCount} accent="#CC8066" viewport />}
      <SiteHeader className="dashboard-site-header" links={dashboardLinks} context={context} />
      <div className="dashboard-content">{children}</div>
    </div>
  );
}
