import Link from "next/link";
import { Suspense, type ReactNode } from "react";
import { api, todayIST } from "@/lib/api";
import { Navigation } from "./navigation";
import { Refresh } from "./refresh";
async function Connection() { const result = await api.health(); return <span className={`connection ${result.data?.status === 'ok' ? 'good' : 'warn'}`}><span className="dot"/>{result.data?.status === 'ok' ? 'API reachable' : 'API unavailable'}</span>; }
export function Shell({ children }: {
    children: ReactNode;
}) { return <div className="shell"><a className="skip-link" href="#main-content">Skip to content</a><aside className="sidebar"><Link href="/" className="brand"><span className="brand-mark">P</span><span>PMS<span className="brand-sub">RESEARCH & PAPER</span></span></Link><div className="nav-label">WORKSPACE</div><Navigation /><div className="sidebar-note"><span className="badge neutral">PAPER ONLY</span><p>Decisions, evidence and simulated execution.</p><span className="subtle">Real-money execution disabled.</span></div></aside><div className="main"><header className="topbar"><div><span className="topbar-title">Portfolio workspace</span><span className="topbar-date">{todayIST()} · IST</span></div><div className="toolbar"><Suspense fallback={<span className="connection">Connecting…</span>}><Connection /></Suspense><Refresh /></div></header><main id="main-content" className="content">{children}</main><footer className="footer">PMS Console · Recorded evidence and end-of-day simulation · All times IST</footer></div></div>; }
