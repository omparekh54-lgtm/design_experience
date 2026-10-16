import Link from "next/link";
import { api, formatNumber } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ResearchHubPage() {
  const today = new Date().toISOString().slice(0, 10);
  const [rankings, runs, stress, walk] = await Promise.all([
    api.moduleC(today, true), api.researchRuns(), api.stress(), api.walkForward(),
  ]);
  const selected = rankings.data?.rankings || [];
  const researchRuns = runs.data?.runs || [];
  const stressRuns = stress.data?.runs || [];
  const walkRuns = walk.data?.runs || [];

  return (
    <>
      <div className="page-title"><div><h2>Research Lab</h2><div className="subtle">Strategy validation, stock research and out-of-sample evidence.</div></div><span className="pill">Point-in-time research stack</span></div>
      <div className="grid grid-4">
        <div className="card"><div className="metric-label">Selected today</div><div className="metric-value">{selected.length}</div></div>
        <div className="card"><div className="metric-label">Research runs</div><div className="metric-value">{researchRuns.length}</div></div>
        <div className="card"><div className="metric-label">Stress runs</div><div className="metric-value">{stressRuns.length}</div></div>
        <div className="card"><div className="metric-label">Walk-forward runs</div><div className="metric-value">{walkRuns.length}</div></div>
      </div>

      <section className="section">
        <div className="section-head"><h3>Selected stocks to inspect</h3><span className="subtle">Open a stock to see Module A + B evidence.</span></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Instrument</th><th>Module C score</th><th>Components</th><th></th></tr></thead><tbody>
          {selected.map((row:any)=>{const instrument=`${row.exchange||"NSE"}:${row.symbol}`;return <tr key={instrument}><td className="mono">{instrument}</td><td>{formatNumber(row.score,1)}</td><td className="subtle">{Object.entries(row.components||{}).map(([k,v])=>`${k}:${formatNumber(v,1)}`).join(" · ")||"—"}</td><td><Link className="link-button" href={`/research/${encodeURIComponent(instrument)}`}>Open research</Link></td></tr>})}
          {selected.length===0 && <tr><td colSpan={4}><div className="empty">No selected Module C candidates for today.</div></td></tr>}
        </tbody></table></div>
      </section>

      <div className="section grid grid-2">
        <div className="card"><div className="section-head"><h3>Latest stress studies</h3></div>{stressRuns.slice(0,5).map((r:any)=><div className="kv" key={r.id}><span>{r.variant || r.run_type || "Stress test"}</span><strong className="mono">{String(r.id).slice(0,8)}</strong></div>)}{stressRuns.length===0&&<div className="empty">No stress runs persisted.</div>}</div>
        <div className="card"><div className="section-head"><h3>Latest walk-forward studies</h3></div>{walkRuns.slice(0,5).map((r:any)=><div className="kv" key={r.id}><span>{r.variant || "Walk forward"}</span><strong className="mono">{String(r.id).slice(0,8)}</strong></div>)}{walkRuns.length===0&&<div className="empty">No walk-forward runs persisted.</div>}</div>
      </div>
    </>
  );
}
