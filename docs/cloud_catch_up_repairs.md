# Catch-up source contracts and recovery

Run 39417491237 stopped on `index_membership_identity_unresolved`. Its completed
price imports and filing checkpoints remain preserved. Restart from the repaired
main revision; GitHub's rerun button would execute the old revision again.

The current Nifty500 file has 501 source rows. The PMS cash-equity projection has
497 members; three RR-series REIT units and the exact reviewed DUMMYHEG index
adjustment are retained as explicit exclusions in the source receipt. They do not
become invented equity instruments or prices. Unknown equities, duplicate rows,
changed dummy identities and conflicting ISINs still block the snapshot. This is a
current observation, not a reconstruction of historical membership. Validate the
complete identity snapshot before downloading the filing backlog.

Banking and insurance XBRL filings use separate income/PAT concepts. The source
payload records the exact income basis (bank total income, general-insurer
operating income, or life-insurer net premium), rather than labelling these as
industrial sales. Module A's financial-sector exclusion remains in force.

Shareholding parsing binds the current ISIN and NSE symbol inside a period-matched
XML header before accepting a stale outer API identifier. BSE numeric entities
remain linked through that same header. Encumbrance counts use the full promoter
category and promoter-share denominator; absent disclosures are not assumed zero.
An omitted promoter section needs an explicit zero summary and complete public /
non-public category reconciliation. Equity shares outstanding exclude separately
reported depository-receipt counts only after reconciling fully/partly-paid equity
and total counts. Percentage bases must reconcile with the XML's declared ratio,
its rounding precision and category totals before comparison with the API summary.
Employee trusts remain included in actual equity shares outstanding.

`reviewed_shareholding_identities.json` contains narrowly reviewed filing defects
and historical identity events. Each review is bound to the exact XML SHA256,
source URL, summary symbol/ISIN/period and current master identity, with independent
official master/quote hashes. Raw source identifiers remain preserved. No review
authorizes a different XML document or a general identifier rewrite. The reviewed
AONESTEELS listing-day context keeps the XML's actual October 1 date instead of
backdating those share counts to the September 30 API report date; filing time is
unchanged. Unreviewed date/identity differences remain blockers.

Call-auction classifications can omit the detailed-quote ISIN. A missing value
requires the separate official metadata to match the exact current ISIN, symbol,
series and market; explicit conflicting quote identifiers still fail. Both source
hashes are retained. Failed filing/classification attempts move behind untouched
records without being acknowledged or removed from remaining counts.

Bootstrap uses up to 24 passes of 100 filings per feed and 150 classifications.
The latter budget covers the fresh-classification backlog of 2,528 active issuers;
24 times 100 could not. Each pass retains private raw archival, verified TLS,
locked PAPER safety, capacity bounds and incremental commits. Partial, busy,
skipped or failed results are never a completed Phase 3. PAPER remains disabled.

The manual `PMS catch-up source preflight` workflow verifies all unacknowledged
financial/shareholding XML (including newly discovered revisions), every due
classification, current master/index identities, actions, meetings and the target
session's core source formats. Its connections enforce default transaction
read-only before any query. It writes no database receipts, facts, permissions or
orders and requires no storage credential. Its artifact contains counts, fixed
error codes, issuer symbols and document hashes; raw XML is never published.
Eight source workers, a 45-minute network deadline, a 1 GiB aggregate download
budget and 9,000-record ceiling bound the review. A source-check pass is evidence
for a fresh main catch-up dispatch, not proof of imported data, private archival,
future source availability or campaign readiness. Review every reported exception
before dispatching the writer workflow.
