import Link from "next/link";
import { api, formatNumber } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function StockResearchPage({ params }: { params: Promise<{ instrument: string }> }) {
  const { instrument: raw } = await params;
  const instrument = decodeURIComponent(raw).toUpperCase();
  const today = new Date().toISOString().slice(0, 10);
  const [a, b] = await Promise.all([api.moduleA(instrument, today), api.moduleB(instrument, today)]);
  const snapshot = a.data?.snapshot || {};
  const decision = a.data?.decision || {};
  const technical = b.data || {};
  const signal = technical.signal || null;

  return (
    <>
      <div className="page-title">
        <div><div className="subtle"><Link href="/research">Research</Link> / Stock</div><h2 className="mono">{instrument}</h2><div className="subtle">Fundamental eligibility and technical setup evidence as of {today}.</div></div>
        <div style={{display:"flex",gap:8}}><span className={`badge ${decision.eligible?"good":"bad"}`}>Module A {decision.eligible?"Eligible":"Ineligible"}</span><span className={`badge ${technical.eligible_universe?"good":"bad"}`}>Module B {technical.eligible_universe?"Eligible":"Rejected"}</span></div>
      </div>
      {(a.error || b.error) && <div className="error">{a.error || b.error}</div>}

      <div className="grid grid-4">
        <div className="card"><div className="metric-label">Module A score</div><div className="metric-value">{formatNumber(decision.score,1)}</div><div className="metric-foot">100-point fundamental score</div></div>
        <div className="card"><div className="metric-label">Sales YoY</div><div className="metric-value">{formatNumber(snapshot.sales_yoy_pct,1)}%</div><div className="metric-foot">Quarterly growth</div></div>
        <div className="card"><div className="metric-label">PAT YoY</div><div className="metric-value">{formatNumber(snapshot.pat_yoy_pct,1)}%</div><div className="metric-foot">Quarterly growth</div></div>
        <div className="card"><div className="metric-label">Promoter pledge</div><div className="metric-value">{formatNumber(snapshot.promoter_pledge_pct,1)}%</div><div className="metric-foot">Hard filter ≤20%</div></div>
      </div>

      <div className="section grid grid-2">
        <div className="card">
          <div className="section-head"><h3>Module A score components</h3><span className="badge">{formatNumber(decision.score,1)}/100</span></div>
          {Object.entries(decision.components || {}).map(([key,value])=><div key={key}><div className="kv"><span>{key.replaceAll("_"," ")}</span><strong>{formatNumber(value,1)}</strong></div><div className="progress"><span style={{width:`${Math.min(100,Number(value||0)*4)}%`}} /></div></div>)}
          {!decision.components && <div className="empty">No component data available.</div>}
        </div>
        <div className="card">
          <div className="section-head"><h3>Quality &amp; growth facts</h3></div>
          <div className="kv"><span>Quarterly sales</span><strong>₹{formatNumber(snapshot.quarterly_sales_cr,1)} cr</strong></div>
          <div className="kv"><span>PAT</span><strong>₹{formatNumber(snapshot.pat_cr,1)} cr</strong></div>
          <div className="kv"><span>Operating profit YoY</span><strong>{formatNumber(snapshot.operating_profit_yoy_pct,1)}%</strong></div>
          <div className="kv"><span>EPS YoY</span><strong>{formatNumber(snapshot.eps_yoy_pct,1)}%</strong></div>
          <div className="kv"><span>OCF / PAT</span><strong>{formatNumber(snapshot.ocf_to_pat,2)}</strong></div>
          <div className="kv"><span>Debt / Equity</span><strong>{formatNumber(snapshot.debt_to_equity,2)}</strong></div>
          <div className="kv"><span>Interest coverage</span><strong>{formatNumber(snapshot.interest_coverage,2)}</strong></div>
          <div className="kv"><span>Market cap</span><strong>₹{formatNumber(snapshot.market_cap_cr,0)} cr</strong></div>
        </div>
      </div>

      <div className="section grid grid-2">
        <div className="card">
          <div className="section-head"><h3>Why Module A decided this</h3></div>
          <div className="metric-label">Failed rules</div>
          <div style={{display:"flex",flexWrap:"wrap",gap:7,marginTop:10}}>{(decision.failed_rules||[]).map((x:string)=><span key={x} className="badge bad">{x}</span>)}{!(decision.failed_rules||[]).length&&<span className="badge good">No failed hard rules</span>}</div>
          <div className="metric-label" style={{marginTop:18}}>Stage-3 triggers</div>
          <div style={{display:"flex",flexWrap:"wrap",gap:7,marginTop:10}}>{(decision.triggers||[]).map((x:string)=><span key={x} className="badge good">{x}</span>)}{!(decision.triggers||[]).length&&<span className="subtle">No acceleration triggers.</span>}</div>
          <div className="metric-label" style={{marginTop:18}}>Data flags</div>
          <div style={{display:"flex",flexWrap:"wrap",gap:7,marginTop:10}}>{(decision.flags||[]).map((x:string)=><span key={x} className="badge warn">{x}</span>)}{!(decision.flags||[]).length&&<span className="subtle">No data-quality flags.</span>}</div>
        </div>
        <div className="card">
          <div className="section-head"><h3>Module B technical setup</h3><span className={`badge ${signal?"good":"warn"}`}>{signal?"Signal found":"No signal"}</span></div>
          <div className="kv"><span>Universe eligible</span><strong>{technical.eligible_universe?"Yes":"No"}</strong></div>
          <div className="kv"><span>Rejected reasons</span><strong>{(technical.rejected_reasons||[]).join(", ")||"—"}</strong></div>
          {signal && <>
            <div className="kv"><span>Signal date</span><strong>{String(signal.signal_date)}</strong></div>
            <div className="kv"><span>Buy-stop high</span><strong>₹{formatNumber(signal.signal_high,2)}</strong></div>
            <div className="kv"><span>Signal low</span><strong>₹{formatNumber(signal.signal_low,2)}</strong></div>
            <div className="kv"><span>Rally</span><strong>{formatNumber(signal.rally_pct,1)}%</strong></div>
            <div className="kv"><span>Velocity</span><strong>{formatNumber(signal.velocity,2)}% / day</strong></div>
            <div className="kv"><span>Entry window</span><strong>{signal.entry_window_days} sessions</strong></div>
          </>}
        </div>
      </div>
    </>
  );
}
