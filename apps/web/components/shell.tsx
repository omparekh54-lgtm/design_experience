import Link from "next/link";
import type { ReactNode } from "react";

const links = [
  ["Dashboard", "/"],
  ["Signals", "/signals"],
  ["Paper Trading", "/paper-trading"],
  ["Research", "/research"],
  ["Backtests", "/backtests"],
  ["Data Health", "/data-health"],
  ["Operations", "/operations"],
];

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark">P</div><span>PMS Console</span></div>
        <nav className="nav">
          {links.map(([label, href]) => <Link key={href} href={href}>{label}</Link>)}
        </nav>
        <div className="sidebar-note">Finstack/open-source data + fake-money paper execution. Real-money broker execution is disabled.</div>
      </aside>
      <main className="main">
        <header className="topbar">
          <h1>Automated Portfolio Management System</h1>
          <span className="pill"><span className="dot" /> backend connected when API is available</span>
        </header>
        <div className="content">{children}</div>
      </main>
    </div>
  );
}
