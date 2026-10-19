import Link from "next/link";
import { notFound } from "next/navigation";
import { api, formatMoney, timeIST, display } from "@/lib/api";
import { Header, Errors, Metric, Empty, Badge, Panel, KeyValue } from "@/components/ui";
export const dynamic = 'force-dynamic';
export default async function TradePage({ params }: {
    params: Promise<{
        id: string;
    }>;
}) { const { id } = await params; if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))
    notFound(); const result = await api.trade(id); if (result.status === 404)
    notFound(); const d = result.data, t = d?.trade; return <><Header eyebrow="Formal PAPER · trade timeline" title={t?.instrument || 'Trade details'} description={`Trade ${id}`}><Link className="link-button secondary" href="/paper-trading">← Portfolio</Link></Header><Errors items={[result.error]}/>{t ? <><div className="grid grid-4"><Metric label="State" value={<Badge value={t.state}/>}/><Metric label="Entry / stop" value={formatMoney(t.entry)} foot={`Stop ${formatMoney(t.stop)}`}/><Metric label="Remaining shares" value={t.remaining_shares} foot={`Initial ${t.shares}`}/><Metric label="Gross realized P&L" value={formatMoney(t.realized_pnl)} foot="Net costs are recorded in the portfolio ledger"/></div><div className="section"><Panel title="Lifecycle events">{d?.events.length ? <ol className="stages">{d.events.map(e => <li key={e.id}><span className="stage-marker good"/><div><strong>{e.event_type.replaceAll('_', ' ')}</strong><small>{timeIST(e.event_time)} · {e.state.replaceAll('_', ' ')}</small><div className="stat-line">{['entry', 'stop', 'price', 'shares', 'reason'].filter(k => e.payload[k] != null).map(k => <span key={k}>{k}: <strong>{display(e.payload[k])}</strong></span>)}</div></div></li>)}</ol> : <Empty title="No lifecycle events recorded"/>}</Panel></div><div className="section"><Panel title="Fill accounting">{d?.fills.map((f, i) => <KeyValue key={`${f.fill_time}-${i}`} label={`${timeIST(f.fill_time)} · ${f.side} ${f.quantity}`} value={<>{formatMoney(f.price)} · costs {formatMoney(f.fees)}</>}/>)}{!d?.fills.length ? <Empty title="No fills recorded" detail="This trade may still be pending or canceled before entry."/> : null}</Panel></div></> : null}</>; }
