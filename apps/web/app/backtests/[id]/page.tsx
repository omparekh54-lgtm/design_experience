import Link from "next/link";
import { notFound } from "next/navigation";
import { api, formatMoney, display, formatNumber, record } from "@/lib/api";
import { Header, Errors, Metric, Panel, Empty, Badge } from "@/components/ui";
export const dynamic = 'force-dynamic';
export default async function Backtest({ params }: {
    params: Promise<{
        id: string;
    }>;
}) { const { id } = await params; if (!/^[0-9a-f-]{36}$/i.test(id))
    notFound(); const result = await api.backtest(id); if (result.status === 404)
    notFound(); const run = result.data?.run; return <><Header eyebrow="Historical simulation" title="Backtest detail" description={`Saved run ${id}`}><Link className="link-button secondary" href="/backtests">← Backtests</Link></Header><Errors items={[result.error]}/>{run ? <><div className="grid grid-4"><Metric label="Status" value={<Badge value={run.status}/>}/><Metric label="Initial capital" value={formatMoney(run.initial_capital)}/><Metric label="Net realized P&L" value={formatMoney(run.realized_net_pnl)}/><Metric label="Account return" value={run.return_pct == null ? '—' : `${formatNumber(run.return_pct)}%`}/></div><section className="section"><Panel title="Saved configuration & run evidence"><pre className="json">{JSON.stringify(run, null, 2)}</pre></Panel></section><section className="section"><Panel title="Historical fills"><div className="table-wrap"><table className="table"><thead><tr><th>Date</th><th>Instrument</th><th>Side</th><th>Quantity</th><th>Price</th><th>Costs</th></tr></thead><tbody>{result.data?.fills.map((f, i) => <tr key={display(f.id) || i}><td>{display(f.trade_date)}</td><td>{display(f.instrument_id)}</td><td>{display(f.side)}</td><td>{display(f.quantity || f.shares)}</td><td>{formatMoney(f.price)}</td><td>{formatMoney(record(f.costs).total ?? f.total_costs ?? f.costs)}</td></tr>)}{!result.data?.fills.length ? <tr><td colSpan={6}><Empty title="No fills stored for this run"/></td></tr> : null}</tbody></table></div></Panel></section></> : null}</>; }
