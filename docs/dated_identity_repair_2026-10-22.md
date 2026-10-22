# Dated JSWDULUX identity reconciliation

NSE/CML/77001, dated 19 October 2026, explicitly changes JSWDULUX to
INE133A01029 for trades from 22 October 2026. Its original PDF SHA256 is
`eb9b435bdd25be1d4615be761ce5d58f0a87438d2d709629f5f922e46ff3b588`.
The official event lists the original INE133A01011 for the 10-to-1 subdivision.
The live equity master and detailed quote independently bind the new identifier
to JSW Dulux Limited, EQ, normal market. CDSL DP2026-728 also lists the new
Rs1 equity identifier; this corroborating probe is not a collector receipt.

The targeted identities mode archives and verifies the exact NSE circular,
fresh master, fresh quote and exact original-ISIN split action before a transaction can update this one active
instrument. It requires PAPER entries disabled, retains the instrument ID and
external mapping, and appends a reviewed old/new identity history. Unknown
identifiers, conflicting history, changed circular bytes, unarchived sources,
future effective dates and mismatching issuer/series/market fail closed.

The former ISIN is accepted for source prices only through 21 October; the new
ISIN is rejected for earlier prices. Prior-period shareholding XMLs may retain
their original ISIN when an already reviewed date-bounded identity supports it;
the XML header and reported period must still validate. No financial filing,
timestamp, period, price or source payload is rewritten. The exact published
split action retains its original ISIN and terms in the action review manifest.

The repair workflow reconciles identities before its financial/sector jobs,
then attempts the actual completed-session collector and a read-only readiness
assessment even when collection is blocked. Readiness does not enable entries,
unlock safety, freeze activation or create a campaign. Missing current operational
proofs remain blockers rather than being inferred from old receipts.
The existing operations verification also runs after the repair workflow ends,
with a current-main SHA guard, to obtain fresh deployment, scheduler, private
archive, isolated restore and read-only rehearsal receipts. It never activates.

VALIANTLAB is still subject to the normal classification gate. The official
G/BE quote currently returns null security fields. Search snippets and a business
segment description are not archived current exchange classification evidence.
No classification waiver or synthetic sector is introduced by this change.

Sources:

* https://nsearchives.nseindia.com/content/circulars/CML77001.pdf
* https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
* https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N&series=EQ&symbol=JSWDULUX
* https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=01-09-2026&to_date=05-12-2026&symbol=JSWDULUX
* https://www.cdslindia.com/eservices/Publications/DownloadFile?eventID=DP2026-728&method=communique
