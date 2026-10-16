import Link from "next/link";
import type { ReactNode } from "react";

const links = [
  ["Dashboard", "/"],
  ["Signals", "/signals"],
  ["Research", "/research"],
  ["Backtests", "/backtests"],
  ["Data Health", "/data-health"],
];

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark">P</div><span>PMS Console</span></div>
        <nav className="nav">
          {links.map(([label, href]) => <Link key={href} href={href}>{label}</Link>)}
        </nav>
        <div className="sidebar-note">Research / paper mode only. Live execution remains disabled.</div>
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
