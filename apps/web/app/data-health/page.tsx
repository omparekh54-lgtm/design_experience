import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

function freshnessLabel(value: unknown) {
  if (!value) return "Missing";
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) return String(value);
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  return days <= 1 ? "Fresh" : days <= 7 ? `${days}d old` : `${days}d stale`;
}

export default async function DataHealthPage() {
  const [health, service] = await Promise.all([api.dataHealth(), api.health()]);
  const h = health.data || {};
  const status = String(h.status || "unavailable");
  const rows = [
    ["Latest market price", h.latest_price_date],
    ["Latest financial filing", h.latest_financial_filing],
    ["Latest shareholding filing", h.latest_shareholding_filing],
    ["Latest Module E regime", h.latest_regime_date],
    ["Latest Module C ranking", h.latest_module_c_date],
  ];

  return (
    <>
      <div className="page-title"><div><h2>Data Health</h2><div className="subtle">Freshness, completeness and data-quality status across the PMS pipeline.</div></div><span className={`badge ${status==="ok"?"good":"bad"}`}>{status.toUpperCase()}</span></div>
      {(health.error || service.error) && <div className="error">{health.error || service.error}</div>}

      <div className="grid grid-4">
        <div className="card"><div className="metric-label">Price rows</div><div className="metric-value">{Number(h.price_rows || 0).toLocaleString("en-IN")}</div></div>
        <div className="card"><div className="metric-label">Financial filings</div><div className="metric-value">{Number(h.financial_filings || 0).toLocaleString("en-IN")}</div></div>
        <div className="card"><div className="metric-label">Unresolved errors</div><div className={`metric-value ${Number(h.unresolved_data_quality_errors||0)>0?"bad":"good"}`}>{h.unresolved_data_quality_errors ?? "—"}</div></div>
        <div className="card"><div className="metric-label">Live execution</div><div className="metric-value warn">OFF</div><div className="metric-foot">Intentional safety state</div></div>
      </div>

      <section className="section grid grid-2">
        <div className="card">
          <div className="section-head"><h3>Dataset freshness</h3></div>
          {rows.map(([label,value])=><div className="kv" key={String(label)}><span>{label}</span><span><strong>{value?String(value):"—"}</strong> <span className={`badge ${freshnessLabel(value)==="Fresh"?"good":"warn"}`}>{freshnessLabel(value)}</span></span></div>)}
        </div>
        <div className="card">
          <div className="section-head"><h3>Service state</h3></div>
          <div className="kv"><span>API</span><strong className={service.data?.status==="ok"?"good":"bad"}>{String(service.data?.status || "Unavailable")}</strong></div>
          <div className="kv"><span>Database</span><strong>{String(service.data?.database || "Unknown")}</strong></div>
          <div className="kv"><span>Strategy version</span><strong className="mono">{String(service.data?.strategy_version || "—")}</strong></div>
          <div className="kv"><span>Live trading</span><strong className="warn">Disabled</strong></div>
        </div>
      </section>

      <section className="section">
        <div className="card">
          <div className="section-head"><h3>Operational interpretation</h3></div>
          <p className="subtle">A green service status only means the API/database path is healthy. Strategy runs should still be blocked when required point-in-time inputs are stale or missing; Module A already fails closed on missing hard-filter data.</p>
        </div>
      </section>
    </>
  );
}
