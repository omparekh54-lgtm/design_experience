import Link from "next/link";
import { api, formatNumber } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function SignalsPage() {
  const today = new Date().toISOString().slice(0, 10);
  const [rankings, orders] = await Promise.all([api.moduleC(today, false), api.orders()]);
  const rows = rankings.data?.rankings || [];
  const pending = orders.data?.order_candidates || [];

  return (
    <>
      <div className="page-title"><div><h2>Signals &amp; On Deck</h2><div className="subtle">Module C rankings, pending entries and rejection reasons for {today}.</div></div><span className="badge warn">No broker execution</span></div>
      {(rankings.error || orders.error) && <div className="error">{rankings.error || orders.error}</div>}

      <div className="grid grid-3">
        <div className="card"><div className="metric-label">Ranked candidates</div><div className="metric-value">{rows.length}</div></div>
        <div className="card"><div className="metric-label">Selected</div><div className="metric-value">{rows.filter((x:any)=>x.selected).length}</div></div>
        <div className="card"><div className="metric-label">Pending entries</div><div className="metric-value">{pending.length}</div></div>
      </div>

      <section className="section">
        <div className="section-head"><h3>Module C ranking board</h3><span className="subtle">Score ≥60 qualifies before portfolio constraints.</span></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Instrument</th><th>Score</th><th>Qualified</th><th>Selected</th><th>Rejected reasons</th><th></th></tr></thead><tbody>
          {rows.map((row:any)=>{ const instrument=`${row.exchange || "NSE"}:${row.symbol}`; return <tr key={instrument}><td className="mono">{instrument}</td><td>{formatNumber(row.score,1)}</td><td><span className={`badge ${row.qualified?"good":"bad"}`}>{row.qualified?"Yes":"No"}</span></td><td>{row.selected?"Yes":"No"}</td><td className="subtle">{(row.rejected_reasons||[]).join(", ")||"—"}</td><td><Link className="link-button" href={`/research/${encodeURIComponent(instrument)}`}>Research</Link></td></tr>})}
          {rows.length===0 && <tr><td colSpan={6}><div className="empty">No ranking snapshot is available for today.</div></td></tr>}
        </tbody></table></div>
      </section>

      <section className="section">
        <div className="section-head"><h3>Pending entry candidates</h3><span className="subtle">Strategy state only; not broker orders.</span></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Instrument</th><th>Signal date</th><th>State</th><th>Entry</th><th>Stop</th><th>Shares</th></tr></thead><tbody>
          {pending.map((row:any)=><tr key={row.id}><td className="mono">{row.exchange}:{row.symbol}</td><td>{String(row.signal_date)}</td><td>{row.state}</td><td>{formatNumber(row.entry)}</td><td>{formatNumber(row.stop)}</td><td>{row.shares}</td></tr>)}
          {pending.length===0 && <tr><td colSpan={6}><div className="empty">No active pending entries.</div></td></tr>}
        </tbody></table></div>
      </section>
    </>
  );
}
