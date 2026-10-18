# Module E — market regime and entry capacity

Implementation: `MODULE_E_v1.1`. Implements section 7 of the PMS rules.
The established official benchmark is `NIFTYMIDSMALLCAP400` (Nifty MidSmallcap
400), alongside India VIX and an independent Nifty 50 session calendar. This
retains the benchmark already used in the Phase-1 ingestion; no index is silently
substituted. Module E creates no entry signals and submits no broker orders.

## Run

```powershell
python scripts/evaluate_module_e.py
python scripts/evaluate_module_e.py --persist
python scripts/evaluate_module_e.py --details
```

The default is read-only. Before market close, the default date is the preceding
calendar day; weekends/holidays resolve to the latest completed official session.
An explicitly requested trading date before its close is rejected. Future dates
are rejected. `--details` prints every per-stock coverage exception; normal output
and persisted metadata retain exact counts and up to 25 exception samples.

**`--persist` saves the regime and cancels/trims formal PAPER pending buy orders
to its capacity.** It never calls a live broker. Read-only mode changes no orders.
The CLI uses the existing `.env` database URL and the `pms,public` search path.

`GET /v1/module-e/{date}` resolves the requested date to its official market
session and returns the saved decision, reason and data-readiness flag. Old
implementation versions, unavailable data or stale calendars display unavailable
capacity zero. Module C independently checks the same metadata before allocating.

## Verified data, independent of Module A

The breadth universe is not A's eligible list. Start from the active NSE master,
then classify liquidity using a complete, validated 20-session average turnover
of at least ₹1 crore. The 20-session liquidity window is an explicit initial
implementation choice consistent with Module B/C, not a newly confirmed owner
threshold. Report missing/stale windows rather than excluding them silently.
Listings known to have fewer than 50 exchange sessions are reported as immature
and excluded because DMA50 cannot yet be measured; unknown listing ages do not
receive that exemption.

Before a usable score is produced:

- Require every mature master stock to have a verified current liquidity window.
- Require complete price/indicator history for every stock classified as liquid.
  Mature stocks need 257 aligned exchange sessions for today's 252-session
  extremes and the preceding five-day trend. A known younger listing uses its
  complete available history since listing, with at least 50 sessions.
- Deduplicate daily sources using the same deterministic preference and OHLCV /
  corporate-action integrity checks as Module B. No A evaluation is performed.
- Require exact official benchmark dates: 205 closes for DMA200 and its five-
  session slope, and six VIX closes for a five-session change. Matching only the
  first/last date is insufficient.

Incomplete coverage produces `band: UNAVAILABLE`, `data_ready: false`, an
explicit reason and **zero** new-entry capacity. Score fields are zero
placeholders in this state, not an assertion that the market itself scored zero.
Partial-universe scores are not represented as verified market breadth.

## Scoring and stability

The specification gives component weights but not their numeric transforms.
These are explicit proposed starting formulas for research, not optimized rules
or a claim of trading profitability.

| Component | Points | Measurement |
| --- | ---: | --- |
| Trend | 30 | 35% of this component for close above DMA50, 45% for above DMA200, 20% for positive five-session DMA200 slope. |
| Breadth | 25 | 40% each for percentage above DMA50/EMA21; 20% for aggregate daily advance/decline observations over ten sessions. A/D 0.5 maps to zero, 1 to neutral and 1.5 to full score. |
| Highs/lows | 15 | 70% for net 52-week high percentage, normalized as `50 + 5 × net%`; 30% for today's net percentage minus the mean of the prior five sessions, normalized as `50 + 10 × change`. |
| Volatility | 15 | 70% for VIX level (`(30 − VIX)/20 ×100`), 30% for its five-session direction (`50 − 3 × change%`). |
| Strategy feedback | 15 | Neutral 7.5 points until ten formal PAPER trades close; then 65% for mean net R and 35% for valid-rally percentage in the verified liquid universe. |

All normalized scores are bounded to 0–100 and non-finite inputs are rejected.
When there are no declines, all-advance A/D is finitely saturated at 2 (already
maximum score); a fully flat universe is neutral 1. The underlying counts and
saturation flag are retained.

Feedback uses the most recent 10–15 mode-verified PAPER trades, recorded fill
fees, and original fill risk. It does not divide rupee P&L by an arbitrary
constant or infer original risk from a stop that moved to breakeven. Missing risk
or ambiguous account modes block a verified result. LIVE and PAPER_SANDBOX
outcomes do not contaminate formal PAPER feedback.

| Three-session mean score | Band | Maximum open trades |
| --- | --- | ---: |
| Below 35 | Defensive | 0 |
| 35 to below 50 | Weak | 2 |
| 50 to below 65 | Neutral | 4 |
| 65 to below 80 | Healthy | 6 |
| 80 and above | Strong | 7 |

Healthy capacity 6 is the explicit selected starting value from the specification's
proposed 5–6 range; configuration supports 5 or 6. Pending orders count toward
capacity. Once the smoothed score enters a lower band, downgrade immediately.
An upgrade needs two consecutive verified closes in the same higher target band.
Changing target bands restarts confirmation.

The hard override is independent of the smoothed score: index **strictly below**
DMA200 and breadth above DMA50 **strictly below** 30% force capacity zero.
Equality alone does not trigger it.

Persist the last three raw scores and upgrade state after each official session.
A fresh process restores only the exact preceding session's matching-version
checkpoint. Missing, invalid or incompatible history resets conservatively.
Cold start requires three verified scores, followed by higher-band confirmation;
a continuously strong market therefore first upgrades on session four. Repeated
weekend requests do not add market closes. Direct identical session replay is
idempotent; conflicting/out-of-order input is rejected.

## Pending-entry controls and persistence

Saving serializes the regime write and PAPER controls in one transaction.
Keep the highest-ranked pending PAPER buys within capacity remaining after open
positions. Trim lowest scores first, with deterministic creation-time/ID ties;
unranked orders have lowest priority. Count both snapshot positions and current
mode-linked fills so post-snapshot fills cannot disappear from the capacity count.
Without an account snapshot, keep no unverifiable pending entries.

Only pending BUY orders in formal PAPER are cancelled. SELL protection, LIVE and
sandbox orders are untouched. Unfilled strategy trades may be marked cancelled;
partially/fully filled holdings remain under their own stops and EMA exits. No
broker cancellation request is sent by this module. An optional stop-to-breakeven
on defensive regimes remains a separate, unenabled research variant.

Regime rows remain unique by market date. Re-evaluating the latest session rebuilds
from the preceding checkpoint, not from a second update to today's smoothed state.
Persisting an older session when later regime rows already exist is rejected;
chronological reconstruction must be handled deliberately rather than leaving
future decisions stale. Market-close entry points now snapshot the resolved
account session before E controls and C allocation.

## Validation and remaining limitations

See `module_e_validation_2026-10-18.json`. Actual data for the latest session,
2026-10-16, has:

- 2,302 master stocks; 157 known immature listings excluded.
- 69 liquid and 26 nonliquid stocks with complete current 20-session windows.
- 2,050 mature stocks without verified current liquidity coverage.
- Verified official benchmark/VIX inputs; no closed formal PAPER trades.

The resulting **UNAVAILABLE / capacity 0** record was saved and verified in the
database. PostgreSQL rollback assertions cover idempotent regime saves, ranked
trimming, zero-capacity cancellation, preservation of partial/filled positions,
and isolation of SELL/LIVE/sandbox orders. All synthetic rows were rolled back.

The earlier sectors/benchmark Phase-1 pass did not complete equity-price history.
Current stock-price gaps must be repaired before E can publish a usable score.
Historical survivorship, price vintages, corporate-action reconstruction, fee
schedules, and account/event timestamp reconstruction still need the separate
backtest/data audits. Daily prices cannot prove intraday fill/filing order. This
implementation does not certify a paper campaign or live broker execution.
