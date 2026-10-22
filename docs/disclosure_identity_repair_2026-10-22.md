# Disclosure identity checks, 22 October 2026

PR55's five collector passes reduced the shareholding queue to 133. The final
worker failed with `action_isin_mismatch`, and retained a TCC shareholding error.
Its archive verification succeeded; no campaign was activated.

## TCC: price observation is not a filing identity date

The API summary for September 30 retains `INE887D01016`. The actual XML
`https://nsearchives.nseindia.com/corporate/xbrl/SHP_1743485_21102026073056_WEB.xml`
has SHA256 `377d109db982226e346dd7b4a5f499e298fae8fb6884170b349c4ebf27c9f53b`.
Its undimensioned MainD header binds TCC, `INE887D01024`, BSE 512038 and the
July 1–September 30 period. The collector archived this identical body.
The current official master and detailed EQ quote independently agree on this
ISIN and issuer. Quote SHA256:
`56c2f5aa0140518e8c8c4e646543cf72e3652ae78ab36992d6a21b0e5e6c4984`.

The historical price allowance only has an observation cutoff; it has no
`current_effective_from`. It cannot force the old ISIN onto a financial period.
Only an explicit dated reconciliation can select a prior-period filing ISIN.
The XML must still pass the normal period/header/entity/fact reconciliation.
The original summary and price-history mapping remain unchanged. JSWDULUX's
explicitly dated reconciliation continues to require its prior-period identity.

## 360ONE: exact dividend record review

NSE's action API now includes the October 23 interim dividend of Rs 7.50 using
`INE466L01020`. The current master and detailed EQ quote independently identify
360 ONE WAM LIMITED as `INE466L01038`, face value 1. Only this exact raw event
hash is reviewed: `af70b99cb3477c78b971ffa9c77ae24192fe7a7449baeac11ee705224409448c`.
Source URLs and document hashes are retained in the review registry. The raw
identifier, dividend amount, record date and ex-date are retained. Other events
and changed source payloads remain rejected. No general historical alias is added.

## Verification and limits

Focused regression tests check the TCC observation/date distinction, the
JSWDULUX effective-date boundary and rejection of a changed 360ONE dividend.
The repair workflow watches action-registry changes and retains existing shared
concurrency and advisory locks. Tests and CI must pass before merge. Collection
and read-only readiness must then be rerun; these source probes are not collector
success evidence. PAPER entries stay disabled, with no campaign activation.
