# Exact remaining source financials and bounded collection

The stored filing reader has zero pending V2 rows. This is separate from the
collector's three unacknowledged discovery records. Earlier accessible companion
URLs do not satisfy those exact record keys, periods and statement scopes.

## Official ADANIGREEN standalone render fallback

Two API-listed standalone XML URLs still return HTTP404:

* `INTEGRATED_FILING_INDAS_1743132_21102026051216_WEB.xml`
* `INTEGRATED_FILING_INDAS_1743330_21102026061638_WEB.xml`

The same original and revision API records explicitly pair them with:

* https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_202751_21102026171220_iXBRL_WEB.html
* https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_202816_21102026181642_iXBRL_WEB.html

The fallback only follows an official paired record sequence after XML404. It
archives the actual render, verifies symbol, issuer, expected stored ISIN, scope,
INR currency, declared rounding and both explicit column periods. Only named
statement totals are parsed. Parenthesized negatives are retained, EPS is not
scaled as money, and missing values remain unavailable. No consolidated filing
is substituted. Original XML URI, exchange timestamps, revisions and raw summary
fields remain intact; actual source URI/hash/archive key are added as provenance.
The acknowledgement uses the original discovery key, not the enriched payload.

HTML is admitted only via an explicit flag for the exact official financial
render path. Generic HTML responses remain rejected. The HTML parser does not
execute scripts or load remote resources; entity declarations are forbidden.

## SWASTIKAIN declared half-year duration

https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1743365_21102026062732_WEB.xml

This exact XML declares Half yearly, 1 April–30 September 2026, standalone.
SHA256: `735b53aa826c9e403fdee8aad3791dc8fed89f79152ae3bf8f5fb198318a6fde`.
It contains revenue and PAT; those values cannot be labelled a three-month
quarter. Preserve a distinct `half_year` filing using the actual six-month
contexts. Module A and Module B quarterly calculations accept only explicitly
quarterly records. No half-year division or YTD subtraction is performed.

## Bounded backlog drain

After the current collector releases the shared workflow/advisory locks, the
next targeted workflow repairs financials and runs up to six sequential actual
collector passes of 200 disclosures each. Each pass has a 30-minute source
deadline and checks PAPER entries remain disabled. Stop on hard errors, source
errors, lock contention, no progress or non-queue incompleteness. A PARTIAL
receipt never becomes success because retries were exhausted. Real worker
SUCCESS plus `complete=true` remains required. Readiness runs afterward and
retains date, safety, elapsed-session and operational proof requirements.

The historical sector gaps for 22 October are not backdated; current quotes
observed on 22 October became effective on 23 October. No campaign is activated.
