import Link from "next/link";
import { api, formatMoney, formatNumber } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const today = new Date().toISOString().slice(0, 10);
  const [portfolio, positions, regime, selected, orders] = await Promise.all([
    api.portfolio(), api.positions(), api.moduleE(today), api.moduleC(today, true), api.orders(),
  ]);
  const p = portfolio.data || {};
  const r = regime.data || {};
  const rows = selected.data || [];
  const pos = positions.data || [];
  const pending = orders.data?.orders || orders.data || [];

  return (
    <>
      <div className="page-title">
        <div><h2>Portfolio Dashboard</h2><div className="subtle">Point-in-time view for {today}. Strategy actions are research/paper only.</div></div>
        <span className="pill">Regime: <strong className={r.hard_override ? "bad" : "good"}>{r.band || "Unavailable"}</strong></span>
      </div>
      {portfolio.error && <div className="error">Portfolio API: {portfolio.error}</div>}
      <div className="grid grid-4">
        <div className="card"><div className="metric-label">Open positions</div><div className="metric-value">{p.open_positions ?? pos.length ?? 0}</div><div className="metric-foot">Regime capacity {r.max_open_trades ?? "—"}</div></div>
        <div className="card"><div className="metric-label">Realised P&amp;L</div><div className="metric-value">{formatMoney(p.realized_pnl)}</div><div className="metric-foot">Persisted trade ledger</div></div>
        <div className="card"><div className="metric-label">Gross open value</div><div className="metric-value">{formatMoney(p.gross_open_value)}</div><div className="metric-foot">Current persisted positions</div></div>
        <div className="card"><div className="metric-label">Market regime score</div><div className="metric-value">{formatNumber(r.smoothed_score ?? r.score, 1)}</div><div className="metric-foot">Raw {formatNumber(r.raw_score, 1)} · capacity {r.max_open_trades ?? "—"}</div></div>
      </div>

      <div className="section grid grid-2">
        <div className="card">
          <div className="section-head"><h3>Regime components</h3><Link className="link-button" href="/research">Research</Link></div>
          {Object.entries(r.components || {}).map(([key, value]) => <div className="kv" key={key}><span>{key.replaceAll("_", " ")}</span><strong>{formatNumber(value, 1)}</strong></div>)}
          {!r.components && <div className="empty">No regime snapshot for today.</div>}
        </div>
        <div className="card">
          <div className="section-head"><h3>Pending order candidates</h3><span className="badge warn">Execution disabled</span></div>
          {(Array.isArray(pending) ? pending : []).slice(0, 6).map((o: any, i: number) => <div className="kv" key={o.id || i}><span className="mono">{o.instrument_id || o.symbol || "—"}</span><strong>{o.state || o.status || "PENDING"}</strong></div>)}
          {(!Array.isArray(pending) || pending.length === 0) && <div className="empty">No pending entries.</div>}
        </div>
      </div>

      <section className="section">
        <div className="section-head"><h3>On Deck — Module C selections</h3><span className="subtle">Ranked candidates selected under portfolio constraints</span></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Instrument</th><th>Score</th><th>Selected</th><th>Reasons</th><th></th></tr></thead><tbody>
          {rows.map((row: any) => <tr key={row.instrument_id || row.id}><td className="mono">{row.instrument_id || row.symbol}</td><td>{formatNumber(row.score,1)}</td><td><span className={`badge ${row.selected ? "good" : "warn"}`}>{row.selected ? "Selected" : "Qualified"}</span></td><td className="subtle">{(row.rejected_reasons || []).join(", ") || "—"}</td><td><Link className="link-button" href={`/research/${encodeURIComponent(row.instrument_id || row.symbol)}`}>Open</Link></td></tr>)}
          {rows.length === 0 && <tr><td colSpan={5}><div className="empty">No Module C selections available for {today}.</div></td></tr>}
        </tbody></table></div>
      </section>

      <section className="section">
        <div className="section-head"><h3>Open positions</h3><Link className="link-button" href="/signals">View signals</Link></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Instrument</th><th>State</th><th>Entry</th><th>Stop</th><th>Shares</th><th>Realised P&amp;L</th></tr></thead><tbody>
          {pos.map((row: any) => <tr key={row.id || row.instrument_id}><td className="mono">{row.instrument_id}</td><td>{row.state}</td><td>{formatNumber(row.entry)}</td><td>{formatNumber(row.stop)}</td><td>{row.remaining_shares ?? row.shares}</td><td>{formatMoney(row.realized_pnl)}</td></tr>)}
          {pos.length === 0 && <tr><td colSpan={6}><div className="empty">No open positions.</div></td></tr>}
        </tbody></table></div>
      </section>
    </>
  );
}
