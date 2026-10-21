# Unified research commands

All supported research commands execute `replay_service.execute_replay` and the isolated `MatrixReplay` engine. Every run builds fresh A/B/C/E decisions and its own simulated cash, orders and holdings. Saved C rankings, E regime decisions and PAPER account state do not drive execution.

| Entry point | Input consistency | Outputs |
| --- | --- | --- |
| `scripts/run_backtest.py` | One read-only repeatable-read snapshot, input manifest and matrix gates | Journal summary, canonical metrics and execution identity; optional existing backtest tables |
| `scripts/run_research_lab.py --mode variants` | One snapshot and manifest shared by all variants; independent portfolios | Canonical metrics and execution identity for each variant |
| `scripts/run_research_lab.py --mode robustness` | One snapshot and manifest shared by the full parameter grid; independent portfolios | Parameter values, comparison scores, canonical metrics and execution identity |
| `scripts/run_backtest_matrix.py` | Existing consistent snapshot, manifest and gates | Resumable matrix report and full journals |
| `scripts/run_walk_forward.py` | Existing consistent snapshot, separate phase manifests and gates | Frozen train selections and independently reset test journals |

`DatabaseBacktestStrategy.run()` retains its result-returning Python API as a facade for the same service. Its diagnostics are available through `.diagnostics`. The former stored-selection, bar-building, eligibility and regime-query helper methods have been removed. Consumers must use the isolated replay rather than those legacy helpers.

Examples (both `DATABASE_URL` and `PMS_DATABASE_URL` are supported, along with `.env`):

```bash
python scripts/run_backtest.py --symbols NSE:ABC,NSE:XYZ \
  --start 2022-01-01 --end 2024-12-31 --capital 10000000 --no-persist

python scripts/run_research_lab.py --symbols NSE:ABC,NSE:XYZ \
  --start 2022-01-01 --end 2024-12-31 --mode variants

python scripts/run_research_lab.py --symbols NSE:ABC,NSE:XYZ \
  --start 2022-01-01 --end 2024-12-31 --mode robustness --config config/strategy_v1.yaml
```

Use a real historical symbol sample and an existing strategy YAML path. Symbol samples remain labelled samples, not the full historical universe. `--config` and `--capital` apply to the actual replay and are reflected in its identity and persisted configuration. `--no-persist` suppresses single-run database report writes; Lab does not write strategy/account state.

The shared metrics use daily marked NAV returns for ratios and one completed position cycle for trade statistics. A partial exit no longer creates an extra Lab trade. Some old Lab metric names remain as aliases. Profit factor without losing closed cycles is `null` with an explanatory note, rather than infinity. Previous Lab figures are not directly comparable; rerun them.

Diagnostics include `engine=ISOLATED_REPLAY_v2`, strategy version, configuration and policy fingerprints, variant/seed and a complete result fingerprint. Matrix/walk-forward reports retain their dataset and code fingerprints; single/Lab diagnostics also record dataset, code and sample scope. The `matrix_research_only` execution flag is normalized across entry points. Command-specific report labels and paths do not affect the result fingerprint.

Missing inputs or policy settings produce `BLOCKED` and exit code 2 for single/Lab commands; no successful backtest is persisted. Data exceptions are propagated rather than retried using saved decisions. Configuration or argument errors also stop execution. Variant comparisons and robustness optimization must end by 2024-12-31; single-run inspection of later dates is explicitly `EXPLORATORY_HOLDOUT` and `NOT_CERTIFIED`. Use walk-forward's declared holdout procedure for evaluation.

Setup 2 uses its earnings-anchored evaluator when ranked through research C. Production C continues to reject research-only Setup 2 signals. The new fingerprints invalidate old matrix/walk-forward checkpoint identities, so use a fresh report path for a new computation. Existing stored results are retained and no old metrics are relabelled as new replay evidence.

This command unification does not certify source coverage, independent survivorship correctness, profitability or the full ruleset. Financial ingestion and policy configuration must be available before gated replays can complete.
