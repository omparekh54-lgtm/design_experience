# Walk-forward testing — isolated V2 design

Implementation is separate from actual historical performance evidence. The user has deferred intermediate local commands until the consolidated setup at the end. The historical studies described here have **not** been executed on the populated user database. No performance acceptance is claimed.

## Predeclared windows

| Fold | Training | Test | Classification |
|---|---|---|---|
| WF_2020 | 2018–2019 | 2020 | Retrospective development |
| WF_2021_2022 | 2018–2020 | 2021–2022 | Retrospective development |
| WF_2023_2024 | 2018–2022 | 2023–2024 | Retrospective development |
| FINAL_2025 | 2018–2024 | 2025 | Separate final holdout; explicit inclusion |

Tests are chronological and disjoint. Training may expand over earlier test periods, as it would have in a chronological process, but it never reaches 2025. The runner rejects overlapping/reversed test windows, future dates and final-holdout dates labelled as development. Explicit custom JSON windows can use completed 2026 dates while retaining a training cutoff before 2025. Names cannot change the classification checks.

Pre-2025 periods have already been included in the development matrix, so these folds are retrospective validation, not proof of pristine unseen data. Including 2025 records a final-holdout evaluation but cannot prove nobody previously inspected those prices/results. Repeated experiments and manually changed designs need to be disclosed during the final acceptance audit.

## Train, freeze, test

The default grid has 27 candidates: ADR minimum 2.5/3/4%, rally minimum 12/15/18%, and rally volume ratio 1.25/1.5/2. Risk sizing, A/C/E logic, cost assumptions, exit rules and re-entry stay at the base configuration. Risk is deliberately not optimized upward.

Every training candidate runs through `MatrixReplay`, which recomputes historical A/B/C/E inputs using the simulated account and dated data. It does not load saved production selections, holdings, feedback or B cancellations. Every train and test replay starts fresh capital and flat holdings; historical indicator/regime warmup is separate from trading/feedback account state. Test metrics include marked open positions, fees and corporate actions under the same limitations documented for the matrix. Positions do not carry across folds and no unmodelled liquidation fill is fabricated at a fold boundary.

All candidates must compute before selection; a missing, failed or blocked candidate blocks the fold instead of shrinking the grid after seeing outcomes. Candidates need at least 24 closed position cycles in training by default. This is an eligibility minimum, not a performance threshold or statistical significance claim. `--min-trades` is an explicit design change recorded in the identity.

The fixed objective `CAGR_MINUS_DRAWDOWN_v1` maximizes training CAGR minus training maximum drawdown in percentage points. Stable ascending SHA256 cell keys break exact ties. Undefined/nonfinite scores and undersized samples cannot win. No test metrics, final-holdout prices or random seeds enter the selector.

The winner's full config hash, training row/hash, dataset fingerprint, objective, policy and candidate are saved before the test begins. A completed test is reused on resume; the runner does not search the test period. Failed/interrupted attempts may be retried, with completed training cells retained. An earlier unassessable fold stops progression to later folds, including the final holdout.

## Evidence and reproducibility

- One repeatable-read, database-enforced read-only input snapshot per invocation.
- Independent training/test input manifests and historical listing-based universes, including inactive companies. Training manifests stop at their own cutoff; future test prices cannot become training bars.
- Existing price/source/calendar/warmup, unresolved critical DQ, reference-table, surveillance-verification and Module A policy gates remain enforced. This phase does not switch them off.
- The outer report identity includes the complete plan, grid, policy, base config, phase manifests and code fingerprint, including both research CLIs.
- OS report locking, atomic JSON checkpoints, inner checkpoint hashes, per-cell evidence hashes and checksummed trade/NAV/event journals. Missing or altered evidence fails closed, including journals in already completed folds.
- Ctrl+C leaves an auditable running checkpoint; the same inputs resume. An input/code/config change requires a new report path. A crash between separate atomic files can produce a detected checksum mismatch; preserve the files and investigate instead of editing hashes to force reuse.
- JSON fold summaries, CSV export, and local per-candidate reports/journals. Research-only persistence uses the existing `pms.research_runs` with type `WALK_FORWARD_V2` and identity-guarded upserts. No migration is needed.

Saved study pages show the frozen selection, training/test dates and metrics, sample sizes, generalization gap (test CAGR minus training CAGR), blockers, and fingerprints. The read-only API lists V2 studies and paginates fold summaries; it cannot start computations. Older lab records remain in their legacy tables and the generic research view, clearly separate from this V2 engine. Legacy result dataclasses remain readable; the former `ResearchLab` walk-forward computation has been replaced.

Summary mean returns and worst drawdown are diagnostics over independent reset accounts, not a compounded investable portfolio. Missing/no-trade evidence is not a strategy pass. `acceptance=NOT_CERTIFIED` and `performance_pass=null` remain explicit even if every computation completes. No automatic configuration promotion, safety unlock or live/paper order is produced.

## Deferred local usage reference

These are reference commands for the final consolidated setup, not tasks to run now:

```bash
python scripts/run_walk_forward.py --plan
python scripts/run_walk_forward.py --check
python scripts/run_walk_forward.py --run --max-cells 5
```

The last command resumes the same report on repeat invocations when inputs match. The default design is 81 training cells plus three selected tests. `--quick` makes four candidates per fold (12 training cells plus three tests), explicitly labelled reduced research. `--include-holdout` adds 27 training cells and one selected 2025 test to the default design. Keep a separate report path when changing the design. `--symbols` explicitly limits and labels the universe; `--config` selects an explicit research YAML; `--no-persist` keeps local evidence only.

`--check` performs read-only preflight on the planned train/test periods without computing performance. Exit 0 means requested computations or preflight completed, 2 means incomplete/blocked/failed setup or evidence, and 130 means interrupted. Neither successful exit nor a positive fold certifies the strategy.

## Remaining evidence requirements

The full populated historical run, independent survivorship/reference-vintage review, parameter sensitivity interpretation, data integrity and security/reliability audits, elapsed 2–3 month paper campaign and final acceptance remain separate work. The matrix's daily OHLC execution model, missing/delisted inventory constraints, historical fee vintages and dividend ex-date cash assumption also apply here. A weak result must be recorded; changing the grid after looking at it creates a new research experiment, not fresh OOS evidence.
