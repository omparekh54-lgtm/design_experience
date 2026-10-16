import { api, formatMoney, formatNumber } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function BacktestsPage() {
  const [backtests, research, stress, walk] = await Promise.all([
    api.backtests(), api.researchRuns(), api.stress(), api.walkForward(),
  ]);
  const runs = backtests.data?.runs || [];
  const rr = research.data?.runs || [];
  const sr = stress.data?.runs || [];
  const wr = walk.data?.runs || [];

  return (
    <>
      <div className="page-title"><div><h2>Backtests &amp; Validation</h2><div className="subtle">Versioned research evidence from the shared production strategy engine.</div></div><span className="pill">No live execution</span></div>
      <div className="grid grid-4">
        <div className="card"><div className="metric-label">Backtest runs</div><div className="metric-value">{runs.length}</div></div>
        <div className="card"><div className="metric-label">Research runs</div><div className="metric-value">{rr.length}</div></div>
        <div className="card"><div className="metric-label">Stress runs</div><div className="metric-value">{sr.length}</div></div>
        <div className="card"><div className="metric-label">Walk-forward runs</div><div className="metric-value">{wr.length}</div></div>
      </div>

      <section className="section">
        <div className="section-head"><h3>Latest backtests</h3><span className="subtle">Includes transaction costs and slippage assumptions.</span></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Run</th><th>Period</th><th>Initial capital</th><th>Net P&amp;L</th><th>Return</th><th>Status</th></tr></thead><tbody>
          {runs.map((r:any)=><tr key={r.id}><td className="mono">{String(r.id).slice(0,8)}</td><td>{String(r.started_on)} → {String(r.ended_on)}</td><td>{formatMoney(r.initial_capital)}</td><td>{formatMoney(r.realized_net_pnl)}</td><td>{formatNumber(r.return_pct,2)}%</td><td><span className={`badge ${r.status==="COMPLETED"?"good":"warn"}`}>{r.status}</span></td></tr>)}
          {runs.length===0&&<tr><td colSpan={6}><div className="empty">No backtests persisted yet.</div></td></tr>}
        </tbody></table></div>
      </section>

      <div className="section grid grid-2">
        <div className="card"><div className="section-head"><h3>Stress validation</h3></div>{sr.slice(0,8).map((r:any)=><div className="kv" key={r.id}><span>{r.variant || "Stress study"}</span><strong className="mono">{String(r.id).slice(0,8)}</strong></div>)}{sr.length===0&&<div className="empty">No stress studies.</div>}</div>
        <div className="card"><div className="section-head"><h3>Walk-forward / OOS</h3></div>{wr.slice(0,8).map((r:any)=><div className="kv" key={r.id}><span>{r.variant || "Walk-forward"}</span><strong className="mono">{String(r.id).slice(0,8)}</strong></div>)}{wr.length===0&&<div className="empty">No OOS studies.</div>}</div>
      </div>
    </>
  );
}
