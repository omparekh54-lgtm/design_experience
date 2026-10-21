# Provisional strategy policies revision one

The strategy now supplies explicit starting leverage and cyclical valuation policies. These complete the missing configuration in the draft PMS rules, using research assumptions chosen for testing. They are not NSE requirements, proven optimal thresholds, or a claim of profitable performance. The owner authorized implementation of starting policies; the numerical assumptions remain labelled provisional until historical and forward validation.

The policy ID is `FUNDAMENTAL_POLICY_R1`, status `PROVISIONAL_BACKTEST`, and strategy version `PMS_v1.2.0_policy_r1`. The changed strategy version prevents old A/C/E decisions from being silently treated as current. A decision snapshot records the applied sector/default limits, valuation settings, benchmark and deterministic policy fingerprint. Research parameter overrides change that fingerprint.

## Leverage thresholds

Debt/equity is an inclusive maximum. Interest coverage is an inclusive minimum. The existing calculator uses operating profit divided by interest expense. Explicitly debt-free and interest-free profitable companies retain their existing handling; missing debt, equity or interest data never becomes an automatic pass.

| Sector group | Maximum debt/equity | Minimum interest coverage |
| --- | ---: | ---: |
| Default for other nonfinancial sectors | 1.0 | 3.0 |
| Information Technology, Fast Moving Consumer Goods, Healthcare | 0.5 | 5.0 |
| Construction Materials, Construction, Metals & Mining, Oil Gas & Consumable Fuels | 1.5 | 3.0 |
| Power, Telecommunication | 2.0 | 2.5 |
| Realty | 1.5 | 2.5 |

The default is the initial general filter. Lower leverage/higher coverage requirements test a stricter quality screen in the first group. The capital-intensive groups test higher debt ceilings; power, telecom and realty also test lower coverage floors. These adjustments need comparison against a uniform policy and neighbouring thresholds before validation. They do not guarantee comparable risk across sectors.

Case, whitespace and ampersand normalization prevent accidental default-policy matches. Explicit aliases cover IT, FMCG, Pharmaceuticals, Metals, Oil & Gas, Telecommunications and Real Estate. Aliases identify policy groups; they do not rewrite historical source classifications. Unknown but present nonfinancial sectors use the disclosed default. Missing sector data still blocks eligibility. Financial Services, banks, NBFCs and insurers remain excluded.

## Cyclical valuation

For flagged metals/mining, commodity, sugar and oil/gas companies, trailing PE strictly above 25 subtracts five points from the Module A score. At PE 25 there is no penalty. High PE does not independently remove eligibility. A missing valuation remains flagged rather than manufactured. The existing trailing PE calculation is historical market capitalisation divided by positive trailing four-quarter PAT.

The cutoff and penalty are provisional test parameters. Cyclical earnings can inflate at cycle peaks, so a low PE is not a standalone buy signal or a substitute for the other A checks.

## Benchmark

The explicit market-regime benchmark is Nifty MidSmallcap 400, canonical code `NIFTYMIDSMALLCAP400`. This resolves the draft's shortened “Midcap 400” wording to the index already collected by the application. Module E rejects a different configured benchmark instead of silently substituting a feed. The Nifty 500 relative-strength benchmark remains a separate input.

Primary references for the index identity and classification hierarchy:

- https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-midsmallcap-400
- https://www.niftyindices.com/resources/industry-classification

These references support naming/classification, not the chosen numerical cutoffs.

## Inspection and validation

Run `python scripts/show_strategy_policy.py` to inspect all limits, aliases, provisional status and fingerprint without a database. Add `--sector "Power"` to inspect a specific effective policy. Explicit `--config` research files can supply alternative thresholds; validation rejects invalid numbers, duplicate normalized sector keys, dangling aliases and unsupported benchmark substitutions.

The tests cover per-sector threshold boundaries, aliases, missing data, financial exclusions, valuation penalty boundaries, benchmark selection, version/fingerprint changes and retained fail-closed behaviour for an explicitly unset policy. Historical surveillance, fundamental coverage, ingestion repair, regime data and portfolio safety gates remain separate requirements. No database update, trading unlock or completed backtest is performed by configuring these policies.

Deploy the policy revision only after the financial ingestion repair and required schema migration are coordinated. Re-run A and E and then C under the new strategy version before using fresh paper proposals. Do not relabel older saved decisions as this revision. Any numerical change should receive a new policy/strategy revision and fresh matrix and walk-forward evidence.
