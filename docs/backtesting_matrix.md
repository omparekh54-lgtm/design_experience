# Backtesting matrix

This phase implements the experiments in section 8 of the PMS rules. It does not mean the user's historical data has already been replayed or that performance has passed acceptance. The user is deferring local execution until the consolidated setup at the end of the remaining implementation phases.

## Predeclared design

The default design has 128 cells. Parameters and periods are declared before observing their results. Every cell starts with its own initial cash, positions, pending entries and regime tracker.

| Comparisons | Cells |
|---|---:|
| B alone, B+A, earnings-anchored Setup 2, B+A+C, B+A+C+E; each with and without re-entry | 10 per period |
| Development 2018–2024, each calendar year 2018–2024, 2018–2019 decline, February–May 2020, calendar 2022 stress | 11 periods × 10 = 110 |
| ADR minimum 2.5/3/4%, window 20/50 sessions, upper cap absent/8%; baseline already in core design | 11 |
| Leg 1 always armed versus default first close above EMA9 | 1 |
| Random ordering of C-qualified candidates using fixed seeds 11, 29, 47, 71, 97 | 5 |
| Double slippage and impact; statutory fee assumptions unchanged | 1 |

Calendar 2022 appears as both an annual and named stress view. Other windows also overlap. Returns and trade counts must never be pooled across matrix cells. Random controls preserve C's eligibility, score threshold, liquidity and portfolio constraints; only candidate priority changes. No best seed is selected. `--quick` omits sensitivities and is explicitly marked reduced; a custom period is also a reduced/custom design.

2025 onward is reserved for the subsequent walk-forward/out-of-sample phase. This runner rejects periods beyond 2024-12-31. It does not select parameters, rewrite production configuration or certify an out-of-sample result.

## Historical replay

`MatrixReplay` uses the shared A/B/C/E and trade code against a PostgreSQL REPEATABLE READ, READ ONLY transaction. It recomputes C allocation from the simulated account and E feedback from that experiment's closed cycles. It does not read current holdings, safety permissions, stored C selections, saved E trackers or production B cancellations. Current `active` flags do not exclude historically listed companies; listing/delisting intervals control membership and ambiguous identities are rejected.

Bars and features are requested chronologically at each session. Future splits do not adjust earlier decision bars. Split/bonus events adjust existing quantities and price levels on their ex-date; fractional entitlements or unsupported held actions block the cell. Dividend income is modeled on ex-date before personal taxes. Actual payment-date reinvestment timing remains a separate audit limitation. A held delisting without a defined liquidation event also blocks the cell rather than inventing an exit price.

Signals enter only in their remaining three-session window. The shared engine still uses next-open EMA exits, conservative same-day stops, actual gap prices and costs. Research C rechecks gap sizing against heat, sector and liquidity limits; conditional re-entries use the same lifecycle and prior-close ranking evidence. Research-only ADR and exit switches do not alter production defaults.

## Gates and evidence

The input manifest fingerprints relevant tables, code, configuration, universe and the forward exchange-calendar dates needed for expiries. Coverage checks identify missing/invalid price sessions, unknown listing dates, insufficient benchmark warmup and unverified adjusted-price feeds. Only raw NSE/Angel data is accepted by the matrix's raw-price gate; adjusted feeds require a separate historical-vintage verification path. Price presence alone cannot certify the entire dataset.

Unconfigured leverage policy, unverified legacy surveillance and missing required references block A-dependent experiments. These checks are not silently disabled. In particular, a 2018 start needs pre-2018 warmup history; a Phase-1 row-count/coverage pass does not establish that this warmup exists.

The JSON checkpoint records input/config/code hashes and status for every cell. Completed cells resume only if their evidence checksum and full local trade journal still match. Changed inputs require a new report path. Atomic writes and an OS file lock protect the checkpoint from interrupted processes and concurrent writers. An interrupted in-progress cell restarts; daily execution within a cell is not checkpointed.

Cell states are PENDING, RUNNING, BLOCKED, FAILED, NO_TRADES and COMPLETED. BLOCKED/FAILED cells have no performance metrics; NO_TRADES is insufficient trade evidence. An entirely computed matrix remains NOT_CERTIFIED and has no automatic performance-pass flag. The proposed frequency diagnostic is 24 closed trades/year, with actual per-calendar-year counts also retained; this is not an automatic acceptance rule.

Metrics include marked NAV return, CAGR, seeded drawdown, daily-return Sharpe/Sortino (252 sessions/year, zero risk-free assumption), Calmar, net closed-cycle expectancy/R multiples, profit factor, holding periods and costs. Partial exits form one trade cycle and entry costs are deducted once. Open holdings remain marked and are never fabricated as closed trades. Undefined ratios are JSON null, never Infinity. Paired C/E comparisons show return differences and drawdown reduction without declaring a winner.

Local output comprises the report JSON, a flat CSV and checksummed per-cell JSON journals containing fills, events and NAV curves. Compact summaries are upserted only into existing `pms.research_runs` with `run_type='BACKTEST_MATRIX'`; no new database migration is required by this phase. PAPER/live orders, account snapshots, strategy decisions and safety state are not written.

## Deferred local operation

These commands are reference instructions for the final setup guide, not a request to run them between phases:

```powershell
python scripts/run_backtest_matrix.py --plan
python scripts/run_backtest_matrix.py --check
python scripts/run_backtest_matrix.py --run
```

The runner loads `.env`, supports DATABASE_URL/PMS_DATABASE_URL, and sets the `pms` search path. `--symbols NSE:ABC,NSE:XYZ` explicitly labels a sample universe. `--config path.yaml` supplies an owner-approved research policy, `--capital` overrides initial capital, `--max-cells` bounds work, `--report` chooses a separate checkpoint, and `--no-persist` keeps JSON/CSV/journals local. Full-universe multi-year replay is substantial offline work; completion time depends on local data, database latency and hardware. Existing completed cells are preserved on a repeat with identical options.

Exit 0 means all planned computations finished, not acceptance. Exit 2 means blocked/failed/incomplete work or setup failure. Ctrl+C exits 130 and preserves completed checkpoints. `--check` is read-only and returns 2 if any cell is blocked. `--plan` works without a database.

Read-only API endpoints `/v1/research/matrix` and `/v1/research/matrix/{id}` expose compact saved evidence. `/backtests/matrix` and its detail page provide status/family filters, pagination, paired comparisons and fingerprints. Visiting them never starts a research run.

Walk-forward testing, independent data/vintage/survivorship auditing, security/reliability, the elapsed 2–3 month paper campaign and final acceptance remain separate phases.
