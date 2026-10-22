# Finalize PMS Steps 1 and 10 locally

These are the only two finalization commands. Run them from the repository root with the normal `.env` / Neon connection configured.

## Command 1 — Step 1 historical dataset

```bash
python scripts/finalize_step1.py
```

The command is resumable and shows a `STEP 1` progress bar. It repairs/rechecks official benchmark/calendar history, official NSE prices, current sector classifications, point-in-time historical sector-index intervals, legacy surveillance history, reference history, and historical financials. It then runs the full historical integrity audit plus the independent evidence review pack.

`100% STEP 1 COMPLETE` is printed only if there are no remaining historical acceptance blockers. The signed-by-content local evidence bundle is written to:

`artifacts/final_acceptance/step1_certificate.json`

If any source, coverage, provenance, interval, or audit item is still unresolved, the command exits non-zero below 100% and records the exact blockers in that certificate. It never backdates current sectors or fabricates missing values.

## Command 2 — Step 10 backtesting matrix

Run only after Command 1 reaches 100%:

```bash
python scripts/finalize_step10.py
```

The command verifies the Step-1 certificate, keeps 2025+ reserved as out-of-sample, runs leakage/replay regression tests, checks all matrix data gates, runs the full 2023–2024 matrix twice, audits inactive/delisted inclusion for survivorship, and compares deterministic result fingerprints.

The progress bar advances with completed matrix cells. `100% STEP 10 COMPLETE` is printed only if every matrix cell has a successful terminal state and both complete replays have the same dataset/result fingerprints. The final evidence bundle is:

`artifacts/final_acceptance/step10_certificate.json`

## Meaning of 100%

If both commands exit successfully at 100%, Steps 1 and 10 have no remaining acceptance work under the declared historical scope and rules. A run that stops below 100% is deliberately not called complete; inspect the generated certificate for the exact remaining source/evidence blocker and rerun the same command after it is resolved.
