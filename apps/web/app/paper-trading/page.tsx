import { formatMoney } from "@/lib/api";
import { cancelPaperOrder, placePaperOrder, processPaperOrders } from "./actions";

export const dynamic = "force-dynamic";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || process.env.PMS_API_BASE_URL || "http://127.0.0.1:8000";

async function getAccount() {
  try {
    const res = await fetch(`${API_BASE}/v1/execution/paper-sandbox/account`, { cache: "no-store" });
    if (!res.ok) return { data: null, error: await res.text() };
    return { data: await res.json(), error: null };
  } catch (error) {
    return { data: null, error: error instanceof Error ? error.message : "Failed to load paper account" };
  }
}

export default async function PaperTradingPage() {
  const result = await getAccount();
  const account = result.data || {};
  const positions = account.positions || [];
  const orders = account.orders || [];

  return (
    <>
      <div className="page-title">
        <div>
          <h2>Paper Trading</h2>
          <div className="subtle">Fake-money execution only. No broker account and no real-money orders.</div>
        </div>
        <span className="badge good">PAPER_SANDBOX</span>
      </div>

      {result.error && <div className="error">{result.error}</div>}

      <div className="grid grid-4">
        <div className="card"><div className="metric-label">Starting cash</div><div className="metric-value">{formatMoney(account.starting_cash ?? 10000000)}</div></div>
        <div className="card"><div className="metric-label">Cash available</div><div className="metric-value">{formatMoney(account.cash ?? 0)}</div></div>
        <div className="card"><div className="metric-label">Realized P&amp;L</div><div className={`metric-value ${Number(account.realized_pnl || 0) >= 0 ? "good" : "bad"}`}>{formatMoney(account.realized_pnl ?? 0)}</div></div>
        <div className="card"><div className="metric-label">Recorded fills</div><div className="metric-value">{account.fills ?? 0}</div><div className="metric-foot">Latest market date: {String(account.latest_market_date || "—")}</div></div>
      </div>

      <section className="section grid grid-2">
        <div className="card">
          <div className="section-head"><h3>Place fake-money order</h3><span className="subtle">NSE equities</span></div>
          <form action={placePaperOrder} className="form-grid">
            <label>Symbol<input name="symbol" placeholder="RELIANCE" required /></label>
            <label>Side<select name="side" defaultValue="BUY"><option>BUY</option><option>SELL</option></select></label>
            <label>Quantity<input name="quantity" type="number" min="1" step="1" defaultValue="1" required /></label>
            <label>Order type<select name="order_type" defaultValue="MARKET"><option>MARKET</option><option>LIMIT</option><option>STOP_MARKET</option></select></label>
            <label>Limit price<input name="limit_price" type="number" min="0" step="0.01" placeholder="Only for LIMIT" /></label>
            <label>Trigger price<input name="trigger_price" type="number" min="0" step="0.01" placeholder="Only for STOP" /></label>
            <button className="link-button" type="submit">Create paper order</button>
          </form>
          <p className="subtle">Orders are stored first. MARKET/LIMIT/STOP fills are simulated against an observed daily market bar and include estimated delivery costs.</p>
        </div>

        <div className="card">
          <div className="section-head"><h3>Process open orders</h3></div>
          <p className="subtle">This processes all open PAPER_SANDBOX orders against the latest stored trading day. It never sends anything to a broker.</p>
          <form action={processPaperOrders}><button className="link-button" type="submit">Process latest market bar</button></form>
          <div className="kv"><span>Real money</span><strong className="good">NO</strong></div>
          <div className="kv"><span>Broker required</span><strong className="good">NO</strong></div>
          <div className="kv"><span>Counts toward formal PMS validation</span><strong className="warn">NO</strong></div>
        </div>
      </section>

      <section className="section">
        <div className="section-head"><h3>Sandbox positions</h3></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Symbol</th><th>Exchange</th><th>Quantity</th><th>Average buy price</th></tr></thead><tbody>
          {positions.map((row: any) => <tr key={`${row.exchange}:${row.symbol}`}><td>{row.symbol}</td><td>{row.exchange}</td><td>{row.quantity}</td><td>{row.avg_price != null ? formatMoney(row.avg_price) : "—"}</td></tr>)}
          {positions.length === 0 && <tr><td colSpan={4}><div className="empty">No sandbox positions yet.</div></td></tr>}
        </tbody></table></div>
      </section>

      <section className="section">
        <div className="section-head"><h3>Recent sandbox orders</h3></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Created</th><th>Side</th><th>Type</th><th>Qty</th><th>Status</th><th>Action</th></tr></thead><tbody>
          {orders.map((row: any) => <tr key={row.id}><td>{String(row.created_at || "—")}</td><td>{row.side}</td><td>{row.order_type}</td><td>{row.quantity}</td><td><span className={`badge ${row.status === "FILLED" ? "good" : row.status === "OPEN" ? "warn" : "bad"}`}>{row.status}</span></td><td>{row.status === "OPEN" ? <form action={cancelPaperOrder}><input type="hidden" name="order_id" value={row.id} /><button className="link-button" type="submit">Cancel</button></form> : "—"}</td></tr>)}
          {orders.length === 0 && <tr><td colSpan={6}><div className="empty">No paper orders yet.</div></td></tr>}
        </tbody></table></div>
      </section>
    </>
  );
}
