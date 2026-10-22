# COUNCODOS filing identity review, 22 October 2026

PR56 collector `aa74c5bb-aa3f-444e-9cc2-541a86ef1cda` processed 132
shareholding records and stopped PARTIAL on one COUNCODOS filing. Financials,
classifications and corporate actions completed without errors; this does not
make the overall collector complete.

The exact official XML is
https://nsearchives.nseindia.com/corporate/xbrl/SHP_1742905_21102026035014_WEB.xml
with SHA256 `8083b3b8c39e960feed32efbb4e10448a0c4d3adeb3f25e293b77e64eca95f2e`.
Its undimensioned MainD header identifies COUNCODOS, COUNTRY CONDO'S LIMITED,
BSE code 531624 and July 1–September 30, but says `INE695B01027`.
The API summary, current official NSE master and current BE detailed quote all
identify `INE695B01025`. The XML's final check digit is invalid.

Master: https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
SHA256 `342103bdea9b48109eb716128321d76d50d6f1d881aeb12af706802709fc6993`.
Quote:
https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N&series=BE&symbol=COUNCODOS
SHA256 `540683734f1b784ae8e2dda7ce40a3c6d1d315d11031c7783f6914d54b5679e9`.
The independent quote matches issuer, symbol, BE series, current ISIN and face
value 1 from the master.

The existing document-bound review mechanism now admits only this exact XML
hash, source URL, symbol, original summary ISIN, current instrument ISIN and
September reporting period. All ordinary fact, entity, percentage, share-total
and pledge checks still execute. The original XML, summary, filing timestamp
and period are retained; no instrument overwrite or general alias is created.

The captured official XML and API summary are offline regression fixtures.
Tests require exact period/timestamp preservation and reject changes to the
body, URL, period, summary identity, symbol or expected current identity.
The repair workflow watches this registry and keeps the same concurrency,
advisory lock and disabled PAPER-entry checks. CI must pass before merge and
the actual collector plus readiness verification must run again. Source tests
are not collector completion or forward campaign evidence.
