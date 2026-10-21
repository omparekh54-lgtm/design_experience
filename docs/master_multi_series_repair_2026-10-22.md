# Equity master series repair, 22 October 2026

Replacement repair run 39677501618 completed with financials SUCCESS, sectors PARTIAL,
and collector FAILED (`master_identity_invalid`). Stored V2 pending remained zero;
1,642 visible filings comprise the previously reprocessed originals, 85 net companion
period additions, and 50 subsequent distinct filing additions. Originals were not deleted.

The archived NSE equity master retrieved at 04:00 IST has SHA256
`342103bdea9b48109eb716128321d76d50d6f1d881aeb12af706802709fc6993`.
It lists HAMPTON in BZ/EQ and PARSVNATH in BE/BZ. Each pair has identical
fields except series. The parser rejected every repeated symbol, also preventing the
SUPREMEENG and TIRUPATIFL classification reviews from checking the live master.

The parser now groups only identical non-series fields, preserves sorted series,
and leaves the single-series field null when ambiguous. Duplicate series and any
conflicting non-series field still fail. Classification reviews verify membership
in the observed series set. New instrument metadata retains the full series set.
This does not authorize an ISIN change or select a tradable series.

## Remaining independently observed source issues

* JSWDULUX database ISIN is INE133A01011. Current master, metadata and EQ quote
  report INE133A01029. The official corporate-actions endpoint reports an
  ex-date/record date of 22-Oct-2026 for a split from Rs10 to Re1, using the old
  ISIN. This needs a dated identity reconciliation preserving prior-session
  prices and original filings. No identifier is overwritten by this repair.
* VALIANTLAB official metadata binds INE0JWS01017, BE, market G. The detailed
  quote returns null metadata and security information, so a current sector
  cannot be established from this response. The legacy quote endpoint returns
  HTTP403. No sector is inferred or backdated.
* The collector failed before financial collection. Therefore the two previously
  unavailable ADANIGREEN XMLs and SWASTIKAIN XML still need successful collector
  archival and validation; their separate accessible probes are not that evidence.

Official source URLs:

* https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
* https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi?functionName=getMetaData&symbol=JSWDULUX
* https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N&series=EQ&symbol=JSWDULUX
* https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=01-09-2026&to_date=05-12-2026&symbol=JSWDULUX
* https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=G&series=BE&symbol=VALIANTLAB

PAPER permissions and campaign state remain unchanged. October22 and October23
sector coverage must be reported separately: observations on October22 become
effective October23 under the existing policy. This repair does not establish
collector success, campaign readiness, or completed elapsed paper sessions.
