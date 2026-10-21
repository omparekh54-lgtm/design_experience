# Historical execution consistency

The isolated `MatrixReplay` path now applies these execution rules:

- Module A eligibility is checked at 09:15 before a pending order can fill and again after close before the order survives into the next session.
- Capacity reductions cancel the lowest-ranked pending orders first: score descending, stop risk ascending, ADTV descending, then signal date and symbol for deterministic ties. Open checks use the preceding close's available ranking; close checks use the newly generated C analysis. Filled holdings are retained. When a fresh ranking is unavailable, the original admitted ranking is retained.
- A Leg 2 exit must settle before its exit candle can be offered to C as a re-entry candidate. The candidate competes with other signals under C's portfolio limits. Only an actual C selection creates the pending order, which can execute from the next session. The exit candle's original three-session entry window and one-use parent marker are preserved.
- C approval is recorded by symbol and signal date. Execution requires approval from a preceding close, and shares cannot exceed that allocation even if NAV grows before the trigger. Cash, heat, sector, blackout and liquidity guards can reduce the fill further.
- When the open is below the entry trigger and the daily high reaches it, a financial filing, shareholding filing or earnings announcement during 09:15–15:30 makes event order unknowable. The replay is blocked with `intraday_filing_trigger_order_unknown` before recording an entry. A gap at the open remains ordered using information known at that open. The same timestamp query is shared with formal PAPER execution.

Daily candles do not reconstruct intraday prices. These checks deliberately refuse ambiguous evidence; timestamped intraday prices would be needed to resolve it. A capacity reduction can free reservations after C's allocation pass; replacement candidates are considered on the next close rather than backdated into an earlier allocation.

This change applies to the isolated matrix replay and shared lifecycle engine. The `DatabaseBacktestStrategy` compatibility adapter now delegates to the isolated replay, as described in `research_commands.md`; stored-selection execution has been removed. Low-level/B-only research callers retain the explicit automatic re-entry option. No assertion of complete ruleset compliance or strategy profitability is implied.

Existing backtest results must be rerun with the new code. No historical database rows, source ingestion, production settings or live positions are rewritten by this change.
