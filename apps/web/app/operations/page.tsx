import { api, formatMoney, formatNumber } from "@/lib/api";

export const dynamic = "force-dynamic";

function badge(ok: boolean) {
  return ok ? "badge good" : "badge bad";
}

export default async function OperationsPage() {
  const [safety, readiness, paper, reconciliation, workers, audit] = await Promise.all([
    api.executionSafety(),
    api.executionReadiness(),
    api.latestPaperValidation(),
    api.latestReconciliation(),
    api.workers(),
    api.audit(20),
  ]);

  const s = safety.data || {};
  const r = readiness.data || {};
  const p = paper.data || {};
  const rec = reconciliation.data || {};
  const gates = r.gates || {};
  const workerRows = workers.data?.runs || [];
  const events = audit.data?.events || [];

  return (
    <>
      <div className="page-title">
        <div>
          <h2>Operations & Readiness</h2>
          <div className="subtle">Paper validation, controlled live-validation evidence, safety gates and audit history.</div>
        </div>
        <span className={`badge ${s.emergency_stop || s.locked_reason ? "bad" : "good"}`}>{String(s.execution_mode || "Unavailable")}</span>
      </div>

      {(safety.error || readiness.error) && <div className="error">{safety.error || readiness.error}</div>}

      <div className="grid grid-4">
        <div className="card"><div className="metric-label">Selected mode</div><div className="metric-value">{String(s.execution_mode || "—")}</div><div className="metric-foot">LIVE_AUTO unavailable</div></div>
        <div className="card"><div className="metric-label">Highest eligible stage</div><div className="metric-value">{String(r.stage || "—")}</div><div className="metric-foot">{String(r.readiness_status || "Not evaluated")}</div></div>
        <div className="card"><div className="metric-label">New orders</div><div className={`metric-value ${s.new_orders_enabled ? "good" : "warn"}`}>{s.new_orders_enabled ? "ENABLED" : "OFF"}</div><div className="metric-foot">Separate operator switch</div></div>
        <div className="card"><div className="metric-label">Safety lock</div><div className={`metric-value ${s.locked_reason ? "bad" : "good"}`}>{s.locked_reason ? "LOCKED" : "CLEAR"}</div><div className="metric-foot">{String(s.locked_reason || "No active lock")}</div></div>
      </div>

      <section className="section grid grid-2">
        <div className="card">
          <div className="section-head"><h3>Readiness gates</h3></div>
          {Object.entries(gates).map(([key,value]) => <div className="kv" key={key}><span>{key.replaceAll("_", " ")}</span><span className={badge(Boolean(value))}>{Boolean(value) ? "PASS" : "BLOCK"}</span></div>)}
          {Object.keys(gates).length === 0 && <div className="empty">Readiness has not been evaluated.</div>}
        </div>
        <div className="card">
          <div className="section-head"><h3>Paper validation</h3></div>
          <div className="kv"><span>Status</span><span className={badge(Boolean(p.passed))}>{p.passed ? "PASSED" : "NOT PASSED"}</span></div>
          <div className="kv"><span>Period</span><strong>{p.started_on && p.ended_on ? `${p.started_on} → ${p.ended_on}` : "—"}</strong></div>
          <div className="kv"><span>Fills</span><strong>{p.metrics?.fills ?? "—"}</strong></div>
          <div className="kv"><span>Snapshots</span><strong>{p.metrics?.snapshots ?? "—"}</strong></div>
          <div className="kv"><span>Max drawdown</span><strong>{p.metrics?.max_drawdown_pct != null ? `${formatNumber(p.metrics.max_drawdown_pct)}%` : "—"}</strong></div>
          <div className="kv"><span>Ending NAV</span><strong>{p.metrics?.ending_nav != null ? formatMoney(p.metrics.ending_nav) : "—"}</strong></div>
        </div>
      </section>

      <section className="section grid grid-2">
        <div className="card">
          <div className="section-head"><h3>Latest reconciliation</h3></div>
          <div className="kv"><span>Status</span><strong>{String(rec.status || "Never run")}</strong></div>
          <div className="kv"><span>Orders match</span><span className={badge(Boolean(rec.orders_match))}>{rec.orders_match ? "YES" : "NO"}</span></div>
          <div className="kv"><span>Positions match</span><span className={badge(Boolean(rec.positions_match))}>{rec.positions_match ? "YES" : "NO"}</span></div>
          <div className="kv"><span>Cash match</span><span className={badge(Boolean(rec.cash_match))}>{rec.cash_match ? "YES" : "NO"}</span></div>
        </div>
        <div className="card">
          <div className="section-head"><h3>Live-validation evidence</h3></div>
          <div className="kv"><span>Completed</span><strong>{String(r.details?.live_validation_completed ?? false)}</strong></div>
          <div className="kv"><span>Observed executions</span><strong>{r.details?.live_validation_observations ?? 0}</strong></div>
          <div className="kv"><span>Minimum required</span><strong>{r.details?.minimum_live_validation_observations ?? "—"}</strong></div>
          <div className="kv"><span>Unreconciled observations</span><strong className={Number(r.details?.unreconciled_live_observations || 0) > 0 ? "bad" : "good"}>{r.details?.unreconciled_live_observations ?? 0}</strong></div>
          <p className="subtle">Approved validation proposals are evidence records only. This screen does not submit broker orders.</p>
        </div>
      </section>

      <section className="section">
        <div className="section-head"><h3>Recent worker runs</h3></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Worker</th><th>Job</th><th>Status</th><th>Started</th></tr></thead><tbody>
          {workerRows.slice(0, 12).map((row: any) => <tr key={row.id}><td>{row.worker_name}</td><td>{row.job_name}</td><td><span className={`badge ${row.status === "SUCCESS" ? "good" : row.status === "RUNNING" ? "warn" : "bad"}`}>{row.status}</span></td><td>{String(row.started_at || "—")}</td></tr>)}
          {workerRows.length === 0 && <tr><td colSpan={4}><div className="empty">No worker history available.</div></td></tr>}
        </tbody></table></div>
      </section>

      <section className="section">
        <div className="section-head"><h3>Audit trail</h3><span className="subtle">Latest operational events</span></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Entity</th></tr></thead><tbody>
          {events.map((row: any) => <tr key={row.id}><td>{String(row.created_at || "—")}</td><td>{row.event_type}</td><td>{row.actor}</td><td className="mono">{row.entity_type ? `${row.entity_type}:${row.entity_id || ""}` : "—"}</td></tr>)}
          {events.length === 0 && <tr><td colSpan={4}><div className="empty">No audit events available.</div></td></tr>}
        </tbody></table></div>
      </section>
    </>
  );
}
