# Shared trade engine

Version: `TRADE_ENGINE_v1.1`. Implements section 4 of `PMS_Rules_v1(1).docx`.
This is a deterministic execution model and research harness; it submits no broker
orders. Module C allocation, daily orchestration and formal paper execution are
separate phases.

## Run a reviewable example

```powershell
python scripts/replay_trade_engine.py --demo
python -m pytest -q
```

The demo uses explicitly synthetic prices and synthetic A eligibility. It buys a
gap at 103, arms Leg 1, sells half at the following open, moves the stop to 103,
then sells the remainder on a Leg-2 EMA exit at the next open. Gross simulated
profit is ₹2,205 on 882 shares. It returns the complete event history and restart
checkpoint, with `broker_orders_submitted: 0`. This is a behavior example, not a
performance claim. Use the backtester for transaction-cost-adjusted results.

`--input file.json` replays a JSON object with a `position` checkpoint and `bars`.
Each bar must contain `date`, `open`, `high`, `low`, `close`, `ema9`,
`previous_ema9`, `previous_ema21` and a boolean `fundamental_eligible`. Supply the
actual three `entry_sessions` in the checkpoint. The CLI validates the full
calendar; do not substitute calendar weekdays for exchange sessions.

## Lifecycle and rules

| Rule | Implementation |
| --- | --- |
| Signal bridge | `position_from_module_b` accepts a live, production-eligible Setup-1 result, rejects research/Setup-2/expired results, and obtains three entry dates from the exchange calendar. It creates an unactivated proposal for Module C allocation. |
| Entry | Buy stop at signal high; no entry on signal day; fill at max(open, signal high). Recalculate stop and quantity at the actual gap fill. |
| Window | The three sessions after the signal, including the third. Missing stock bars never extend the explicit calendar window. The canonical driver cancels an unfilled order after the third close. |
| Module A | Recheck before a pending order triggers. Failure cancels that pending entry; it does not liquidate a filled position. |
| Stop | max(signal low ×0.995, actual entry ×0.945). A protective stop exits all remaining shares. Gap below stop fills at the open. |
| Position size | Floor shares under both 0.5% capital risk and 10% capital value. Decimal arithmetic avoids exceeding limits through floating-point division. Stop precision is four decimals, rounded up so the risk cap never widens. |
| Leg 1 | Arm on the first strict close above today's EMA9. A later close below yesterday's EMA9 schedules a next-open exit: all at/below entry, half above entry. Equality to EMA does not exit. |
| Half-sale convention | Round the quantity sold up for odd share counts; one share therefore closes completely. Remaining shares move to a stop at entry. |
| Leg 2 | Exit remaining shares at the entry stop, or schedule a next-open exit after a close below yesterday's EMA21. |
| Protective priority | A next-open gap through the protective stop overrides a queued EMA exit. That stop outcome cannot permit re-entry. |
| Re-entry | Only after an executed Leg-2 EMA21 exit. Reuse that exit candle's date/high/low and original three-session window; require current A eligibility. Each closed trade can create only one successor. |
| Replay | Identical phase/date inputs are idempotent while the phase is active; conflicting or out-of-order active inputs fail. Terminal states never execute additional fills. JSON checkpoints preserve quantities, dates, prior inputs and audit history. |

`process_trade_session` is the canonical daily driver: next-open exits, pending
entry, protective stop, close-based EMA decisions, then entry expiry. It requires
explicit boolean A eligibility. Low-level `process_entry_bar` remains available
for intraday-style research: when no calendar is supplied, its counter assumes
every trading bar is delivered. Production callers must use the calendar-aware
bridge/driver, not infer a window from sparse stock bars.

A daily bar cannot establish whether its low occurred before or after an
intraday buy trigger. The engine conservatively assumes an entry/stop double
touch loses at the stop. It never uses a pre-entry opening price as a fictitious
stop fill. The research configuration that disables same-day stops is rejected.
Arming and exiting Leg 1 on the same close is disallowed.

## Backtester integration

The event-driven harness reuses the same lifecycle functions. It:

- Validates OHLC/EMA data and rejects conflicting duplicates before processing.
- Checks A before every pending fill, including re-entries.
- Cancels/trims pending entries when regime capacity falls, while retaining
  filled positions under their normal stops. It trims newest pending proposals
  first; score-based trimming belongs to Module C.
- Uses separate opening and closing eligibility/capacity callbacks. The database
  adapter evaluates opening A membership at 09:15 IST and uses a regime record
  strictly before that session; close signals use close-date information.
- Applies configured transaction costs to both sides. Entry quantities include
  cash needed for costs; failed cash checks leave no phantom holding.
- Sizes from account equity, carries last known marks through missing stock bars,
  and reports ending equity/unrealized P&L separately from cash and realized P&L.
  `return_pct` now reflects ending equity including open holdings and costs.
- Prevents repeat runs on the same mutable harness instance and repeat admission
  of the same accepted signal.

Portfolio heat (3.5%), two positions per sector, ranking and cash reservation are
Module C responsibilities. The research harness's capacity gate does not certify
those portfolio controls; historical saved C selections are not a reconstructed
portfolio allocator. Historical price vintages, intraday filing/price ordering,
fee-schedule verification and broker tick-size rounding remain separate data,
backtest and paper-execution acceptance checks. Existing configured fees are
modeled as assumptions, not certified current brokerage charges.

## Validation

See `trade_engine_validation_2026-10-18.json`. Unit/integration fixtures cover the
entry boundary, calendar gaps, partial/full exits, stop priority, re-entry,
checkpoints, cash/share/P&L conservation and opening-time information boundaries.
An older backtest fixture had three OHLC ranges excluding its open/close; those
ranges were corrected while retaining its expected lifecycle assertions.

No production-qualified stock was available in the preceding Module A/B audit.
These tests and the synthetic demonstration validate code behavior, not live
broker execution, strategy profitability or completion of upstream data gaps.
