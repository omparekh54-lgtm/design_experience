# Formal PAPER execution

Implementation: `PAPER_EXECUTION_v1.0`, integrated with
`DAILY_ORCHESTRATOR_v1.1`. This is forward simulation using observed end-of-day
bars and fake money. There is no external broker submission or real-money mode.
The existing interactive execution API remains isolated in `PAPER_SANDBOX`.

## Run

Use the existing local virtual environment and `.env` database connection:

```powershell
git pull --ff-only origin main
python scripts/run_market_close.py --check
python scripts/run_market_close.py
```

The scheduled daily command now:

1. Settles previously admitted PAPER orders and positions against today's observed
   completed session, before decision preflight. Protective exits remain independent
   of whether new entries are enabled or A/E are available.
2. Reconciles PAPER, persists its snapshot, evaluates E, and runs A/B/C.
3. Saves the successful decision journal, then admits selected C proposals as PAPER
   buy-stop orders for the next exchange session.

Both GitHub and Celery already use this command/service at 19:00, 19:15 and
19:30 IST. Ingestion must finish first; configuring a schedule does not prove
that its deployed worker/secrets are active. Successful repeats retry admission
idempotently, without repeating already processed fills.

The individual operations are also available:

```powershell
python scripts/run_paper_execution.py settle
python scripts/run_paper_execution.py admit
```

These are mutating operations. `--check` on the market-close command is the
read-only data preflight. Formal processing only accepts today's IST date after
15:30 with an observed official exchange session. Historical simulations belong
in the backtester; past-date fills are not manufactured as forward evidence.
No Angel One key is needed for fake-money execution.

## Admission and portfolio controls

Admission requires the latest matching successful daily journal, configuration
hash, PAPER mode, current orchestrator implementation, completed settlement and
successful reconciliation. It consumes only this run's timestamp-bounded C
selections and implementation version. Instrument IDs must match the canonical
symbol. A/B eligibility is refreshed; an expired, changed, research or unsupported
signal cannot create an order.

The global safety record must explicitly permit new PAPER orders, with no lock
or emergency stop. This implementation never enables orders, clears a lock or
promotes a mode. A ready current-close E record supplies tomorrow's capacity.
Existing PAPER holdings and pending orders reserve slots, sector counts, risk and
cash including estimated fees. Unknown/unmanaged PAPER orders, unreconciled
positions, invalid marks or missing stop metadata block new entries.

At admission and at an actual gap fill, enforce: maximum seven positions and the
configured/E capacity; maximum two per normalized sector; 3.5% portfolio heat;
0.5% per-trade price risk; 10% NAV position value; 2% of prior 20-session ADTV;
and enough cash including costs. Shares cannot exceed the admitted quantity.
Gap entries use the shared stop/sizing rules and may shrink. Commit-time cash
and held-share checks prevent overdrafts and short sales even after planning.

## Lifecycle and information boundaries

The canonical shared trade engine supplies entry, stop, Leg-1 arming/half exits,
Leg-2 exits and re-entry eligibility. Entry orders exist before a later session's
open, and never fill from their creation-day candle. Their original three sourced
exchange dates determine expiry; a missing stock bar does not extend the window.

Pending fills recheck A at 09:15 IST, prior-session technical/liquidity measures,
index membership and announced-result blackout. Filled holdings do not leave
because A or E blocks new entries. Intraday filing/trigger ordering cannot be
inferred from a daily high: an intraday trigger on a session with a financial,
shareholding or earnings announcement requires review. A gap-at-open entry has
an explicit opening-time ordering.

Opening gaps through protective stops take priority. EMA exits decided at a close
execute at a later open. Daily entry/stop double touches use the conservative
shared-engine stop outcome. Missing EMA histories still allow a valid observed
price stop; they block new entries and report protection-only management.

A Leg-2 EMA exit can supply one successor signal. It passes through the same C
ranking, fresh A/B eligibility and portfolio limits; it is never an automatic
unranked buy. Parent consumption and child admission commit together. Stop exits
and Leg-1 full exits cannot generate re-entry. Stale re-entry windows cannot hide
newer native B signals.

SELL proceeds and released slots/heat are not reused to finance another entry
on the same daily bar. This is deliberately conservative about intraday ordering.
After a partially settled attempt, a still-unprocessed entry cannot retroactively
use sale proceeds on retry. Modelled fills are timestamped at session close,
marked as daily-bar simulations and retain their actual recording time; they do
not claim tick-level execution times or prices.

## Persistence and accounting

No migration is required. `trades`, mode-linked `broker_orders`, `broker_fills`,
`portfolio_ledger`, `trade_state_events` and `worker_runs` provide the store.
Each checkpoint preserves shared-engine state, processed inputs, original entry
window, parent signal, configuration, sector, score and daily-run identity.

One transaction-scoped PAPER account lock is shared by the executor, internal
broker and C/E persistence. Trade/order row locks serialize state updates.
Deterministic trade/order/fill identities and recorded session/bar fingerprints
prevent duplicate admissions and fills. A conflicting active-session replay is
rejected. Expected per-trade failures roll back to a savepoint; a database failure
rolls back its enclosing transaction. Missing checkpoints or mismatched stored
quantities require review, rather than guessing a position.

Trade realized P&L remains gross for the existing E feedback contract, whose
original fill event records entry, stop and shares. The ledger allocates entry
fees proportionally to sells and records net realized P&L. Moving-average cost
basis resets when a position closes, so a later trade cannot inherit the previous
trade's cost. Portfolio snapshots include remaining entry costs in unrealized P&L
and compute risk only from the requested account mode.

Paper validation counts only fills stamped with this formal implementation.
Manual sandbox and legacy helper fills are excluded. Successful internal
reconciliation status `MATCHED` is recognized; failed/blocked/interrupted worker
runs remain visible as campaign failures. Configured costs include fees, slippage
and impact assumptions; these are not certified brokerage quotes.

## Limits and current status

Current reference/price/financial gaps and the existing safety lock remain.
Orders are disabled in the actual account, and implementation work did not start
a validated campaign or create production trades. A valid defensive regime may
complete decision generation with admission blocked by zero capacity; inspect
`paper_execution_status` separately from the decision journal's status.

A missed exchange session or absent observed position bar requires review. Split
and bonus events affecting an admitted position also require explicit position
reconciliation; the executor does not guess adjusted share quantities or apply
an old stop to a new price basis. Corrected terminal-trade bars require an audit,
not automatic rewriting of past fills. These cases cannot be counted as clean
forward-validation evidence. Real intraday fills, corporate-action automation
and independent broker reconciliation are not established by daily simulation.

See `paper_execution_validation_2026-10-19.json` for test and database evidence.
The 2–3 month paper campaign and final acceptance audit remain separate phases.
