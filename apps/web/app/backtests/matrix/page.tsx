import {api,display,record,timeIST} from "@/lib/api";
import {Header,Errors,Panel,Empty,Badge,Notice} from "@/components/ui";
export const dynamic='force-dynamic';
export default async function Matrices(){
  const result=await api.matrices();
  return <><Header eyebrow="Predeclared experiments" title="Backtesting matrix" description="Compare strategy components, re-entry, parameters and difficult market periods."><a className="link-button secondary" href="/backtests">← All research</a></Header>
    <Notice title="Computation is separate from acceptance" tone="neutral">The default design contains 128 independent experiments. Data from 2025 onward is reserved for out-of-sample work. A completed run does not certify profitability or historical data integrity.</Notice>
    <Errors items={[result.error]}/><section className="section"><Panel title="Saved matrix runs"><div className="table-wrap"><table className="table"><thead><tr><th>Created · IST</th><th>Period</th><th>Status</th><th>Computed / planned</th><th>Universe</th><th></th></tr></thead><tbody>
    {result.data?.runs.map(r=>{const summary=record(r.summary);return <tr key={display(r.id)}><td>{timeIST(r.created_at)}</td><td>{display(r.start_date)} → {display(r.end_date)}</td><td><Badge value={summary.status}/><small>{display(summary.design_scope)}</small></td><td>{display(summary.computed_cells)} / {display(summary.cells)}</td><td>{display(r.universe_scope)}</td><td><a className="link" href={`/backtests/matrix/${encodeURIComponent(display(r.id))}`}>Compare cells →</a></td></tr>;})}
    {!result.data?.runs.length?<tr><td colSpan={6}><Empty title={result.error?'Matrix evidence unavailable':'No matrix has been run yet'} detail="Results appear after the local research runner saves its checkpoints and research record. Opening this page does not start a backtest."/></td></tr>:null}
    </tbody></table></div></Panel></section></>;
}
