# Financial overlay and repair follow-up — 22 October 2026

The first targeted run processed all 1,507 stored filing identities without parser
errors. Its completion count remained 1,076 because the reader UNION ALL still
exposed original external-map rows beside exact V2 local replacements. A read-only
identity reconciliation found 1,076 external rows and 1,076 matching V2 replacements.
This is a reader precedence defect; it is not permission to delete original data.

Migration 014 suppresses an external row only when the local V2 replacement
matches instrument, period end, filing timestamp, source, consolidation, revision
and filing kind. Its old facts are hidden as a group, so a V2 null cannot fall back
to an earlier invalid fact. Physical map rows, source tables, source facts and
original timestamps remain untouched. The view change is reversible and preserves
the existing view shape and grants. Standalone table deployments are unchanged.

Sector collection refreshed 1,599 records in sixteen passes but repaired only 513
of the original 1,063 dated gaps; existing coverage was being refreshed first by
instrument ID. Missing next-day dated coverage now takes priority over routine
refreshes, retaining failed-symbol rotation and next-day knowability.

Collector verification reached 21 October and stopped at an IRFC corporate-action
identity conflict. The exact IRFC EQ dividend record states face value 10 and
INR 1.15 on 23 October, but carries a nonmatching identifier. Independent current
NSE master and detailed quote agree on issuer, symbol, EQ series, current ISIN and
face value. The exact record is reviewed, retaining its original identifier; this
is not a general old-ISIN alias or permission to rewrite historical identifiers.
A changed source record still blocks.

The two ADANIGREEN XML URLs that returned 404 during the run were available on a
fresh bounded check. Both returned SHA256
`31ac3c71adc1fc38305c10214a79a837cb8fa7c96172299874f0ba4318f21956`.
The SWASTIKAIN XML also became parseable with quarter sales/PAT; source SHA256
`f23e3bf79270e0cf461f319b96c0ca3a4914583102b2ee16250f845fe8f1fb1c`.
These checks justify retrying the official sources; they do not acknowledge an
unarchived filing or waive source validation in the hosted collector.

PAPER remains locked, campaign identities remain empty, and no elapsed sessions
are claimed. A successful subsequent collector and dated sector reconciliation
are still required. Newly observed sectors cannot repair earlier session coverage.
