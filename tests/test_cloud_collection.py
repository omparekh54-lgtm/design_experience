import csv
import io
import json
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from packages.data_layer.cloud_sources import (
    Document,
    OfficialHTTP,
    SafeRedirect,
    SourceError,
    IST,
    allowed_url,
    number,
    parse_bhavcopy,
    parse_indices,
    parse_surveillance,
    parse_shareholding,
    paged_financials,
    parse_master,
    BENCHMARKS,
)
from packages.data_layer.cloud_storage import CloudStore
from packages.pms.cloud_collection import (
    collection_gate,
    parse_meetings,
    run_collection,
)
from packages.pms.module_a_data import normalize_context
from packages.pms.config import load_strategy_config
from scripts import run_cloud_collection as cli

DAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 16, 19, tzinfo=IST)


def document(body, url="https://www.nseindia.com/api/test"):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
    if isinstance(body, str):
        body = body.encode()
    return Document(url, body, "f" * 64, NOW, len(body))


def csv_body(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def price_zip(**changes):
    row = dict(
        TradDt=str(DAY),
        TckrSymb="TEST",
        SctySrs="EQ",
        ISIN="INE000000001",
        OpnPric="100",
        HghPric="102",
        LwPric="99",
        ClsPric="101",
        TtlTradgVol="1000",
        TtlTrfVal="100500.25",
    )
    row.update(changes)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("BhavCopy_NSE_CM_0_0_0_20261016_F_0000.csv", csv_body([row]))
    return out.getvalue()


def index_csv(**changes):
    rows = [
        {
            "Index Name": name,
            "Index Date": "16-10-2026",
            "Open Index Value": "100",
            "High Index Value": "102",
            "Low Index Value": "99",
            "Closing Index Value": "101",
        }
        for name in BENCHMARKS
    ]
    rows[0].update(changes)
    return csv_body(rows)


def test_price_archive_keeps_actual_turnover_and_raw_values():
    r = parse_bhavcopy(price_zip(), DAY)[0]
    assert r["turnover"] == Decimal("100500.25")
    assert r["volume"] == 1000 and r["close"] == Decimal(101)


@pytest.mark.parametrize(
    "changes",
    [
        {"TradDt": "2026-10-15"},
        {"OpnPric": "NaN"},
        {"HghPric": "99"},
        {"LwPric": "101"},
        {"TtlTradgVol": "1.5"},
        {"TtlTrfVal": "-1"},
    ],
)
def test_invalid_price_archive_is_not_fresh_success(changes):
    with pytest.raises(SourceError):
        parse_bhavcopy(price_zip(**changes), DAY)


def test_unexpected_zip_member_rejected_without_extracting(tmp_path):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("../payload.csv", "bad")
    with pytest.raises(SourceError):
        parse_bhavcopy(out.getvalue(), DAY)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "bad"])
def test_nonfinite_or_negative_inputs_rejected(value):
    with pytest.raises(SourceError):
        number(value)


def test_all_four_benchmarks_required_with_dates():
    assert {r["benchmark_code"] for r in parse_indices(index_csv(), DAY)} == set(
        BENCHMARKS.values()
    )
    with pytest.raises(SourceError):
        parse_indices(index_csv(**{"Index Date": "15-10-2026"}), DAY)
    with pytest.raises(SourceError):
        parse_indices(index_csv(**{"Closing Index Value": "Infinity"}), DAY)
    with pytest.raises(SourceError):
        parse_indices(index_csv(**{"Index Name": "Unsupported"}), DAY)


def test_master_validates_listing_date_and_identity():
    row = {
        "SYMBOL": "TEST",
        "NAME OF COMPANY": "Test",
        "SERIES": "EQ",
        "DATE OF LISTING": "01-Jan-2020",
        "ISIN NUMBER": "INE000000001",
    }
    assert parse_master(csv_body([row]))["TEST"]["listed_from"] == date(2020, 1, 1)
    with pytest.raises(SourceError):
        parse_master(csv_body([row, row]))
    with pytest.raises(SourceError):
        parse_master(csv_body([{**row, "ISIN NUMBER": "UNKNOWN"}]))


def test_master_preserves_multiple_series_without_selecting_one():
    row = {"SYMBOL": "TEST", "NAME OF COMPANY": "Test", "SERIES": "EQ",
           "DATE OF LISTING": "01-Jan-2020", "ISIN NUMBER": "INE000000001",
           "FACE VALUE": "1"}
    rows = [row, {**row, "SERIES": "BZ"}, {**row, "SERIES": "BE"}]
    forward = parse_master(csv_body(rows))["TEST"]
    assert forward == parse_master(csv_body(list(reversed(rows))))["TEST"]
    assert forward["series"] is None
    assert forward["series_list"] == ["BE", "BZ", "EQ"]
    for field, value in [("ISIN NUMBER", "INE000000002"),
                         ("NAME OF COMPANY", "Other"),
                         ("DATE OF LISTING", "02-Jan-2020"), ("FACE VALUE", "2")]:
        with pytest.raises(SourceError, match="master_identity_invalid"):
            parse_master(csv_body([row, {**row, "SERIES": "BZ", field: value}]))
    with pytest.raises(SourceError, match="master_identity_invalid"):
        parse_master(csv_body([*rows, rows[-1]]))


def surveillance_csv(gsm="0", lt="100", st="100"):
    return csv_body(
        [
            {
                "Symbol": "TEST",
                "Status": "A",
                "Series": "EQ",
                "GSM": gsm,
                "Long_Term_Additional_Surveillance_Measure (Long Term ASM)": lt,
                "Short_Term_Additional_Surveillance_Measure (Short Term ASM)": st,
            }
        ]
    )


def test_surveillance_zero_is_active_and_100_is_not():
    assert parse_surveillance(surveillance_csv())["TEST"]["GSM"] == "GSM0"
    row = parse_surveillance(surveillance_csv("100", "2", "1"))["TEST"]
    assert row["GSM"] is None and row["ASM"] == "LT2+ST1"
    with pytest.raises(SourceError):
        parse_surveillance(surveillance_csv("99"))
    with pytest.raises(SourceError):
        parse_surveillance(b"Symbol,Status\nTEST,A")


@pytest.mark.parametrize(
    "url",
    [
        "http://www.nseindia.com/api/test",
        "https://evil.example/a",
        "https://secret@www.nseindia.com/a",
        "https://www.nseindia.com:444/a",
    ],
)
def test_attachment_host_and_transport_restrictions(url):
    with pytest.raises(SourceError):
        allowed_url(url)


def test_http_retries_only_transient_errors_and_retains_no_body():
    import urllib.error

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def geturl(self):
            return "https://www.nseindia.com/api/test"

        def read(self, n):
            return b"[]"

    class Opener:
        calls = 0

        def open(self, *a, **k):
            self.calls += 1
            if self.calls < 3:
                raise urllib.error.HTTPError("safe", 503, "private message", {}, None)
            return Response()

    opener = Opener()
    delays = []
    http = OfficialHTTP(opener=opener, sleep=delays.append)
    doc = http.fetch("https://www.nseindia.com/api/test")
    assert opener.calls == 3 and delays == [1, 2] and doc.body == b"[]"
    assert http.documents[0].body == b"" and http.documents[0].size == 2


def test_http_404_does_not_retry_or_echo_remote_error():
    import urllib.error

    class Opener:
        def open(self, *a, **k):
            raise urllib.error.HTTPError("safe", 404, "private credential", {}, None)

    with pytest.raises(SourceError, match="source_http_404") as error:
        OfficialHTTP(
            opener=Opener(), sleep=lambda n: pytest.fail("unexpected retry")
        ).fetch("https://www.nseindia.com/api/test")
    assert "private" not in str(error.value)


def financial_row(seq="1"):
    return dict(
        symbol="TEST",
        qe_Date="30-Sep-2026",
        broadcast_Date="16-Oct-2026 16:00:00",
        creation_Date="16-Oct-2026 16:00:17",
        consolidated="Consolidated",
        type_Sub="Original",
        seq_Id=seq,
        xbrl="https://nsearchives.nseindia.com/corporate/test.xml",
    )


def test_modern_financial_fields_and_pagination_are_not_silently_dropped():
    class HTTP:
        def api(self, *a, **k):
            p = k["page"]
            return document(
                {"data": [financial_row(str(p))], "totalCount": 2, "page": p - 1}
            )

    rows = paged_financials(HTTP(), DAY, DAY)
    assert len(rows) == 2 and rows[0].statement_scope == "consolidated"
    assert rows[0].filed_at == datetime(2026, 10, 16, 16, 0, 17)


def test_financial_null_broadcast_revision_uses_actual_publication():
    from packages.data_layer.exchange_financials import NSEIntegratedFinancialsProvider

    parser = object.__new__(NSEIntegratedFinancialsProvider)
    r = parser._parse_row(
        {
            **financial_row(),
            "broadcast_Date": None,
            "revised_Date": "16-Oct-2026 17:00:00",
            "type_Sub": "Revision",
        }
    )
    assert r.filed_at.hour == 17 and r.submission_type == "Revision"


@pytest.mark.parametrize(
    "kind", ["repeat", "truncate", "wrong_page", "bad_record", "outside_window"]
)
def test_financial_truncation_and_ignored_filters_block(kind):
    class HTTP:
        def api(self, *a, **k):
            p = k["page"]
            row = financial_row("1" if kind == "repeat" else str(p))
            if kind == "bad_record":
                row.pop("qe_Date")
            if kind == "outside_window":
                row["creation_Date"] = "15-Oct-2026 16:00:17"
                row["broadcast_Date"] = "15-Oct-2026 16:00:00"
            return document(
                {
                    "data": [] if kind == "truncate" and p == 2 else [row],
                    "totalCount": 2,
                    "page": 0 if kind == "wrong_page" else p - 1,
                }
            )

    with pytest.raises(SourceError):
        paged_financials(HTTP(), DAY, DAY, max_pages=3)


def test_financial_pagination_restarts_whole_snapshot_after_live_count_changes():
    class HTTP:
        def __init__(self):
            self.snapshot=0
            self.calls=[]
        def api(self,*a,**k):
            page=k['page']
            if page==1:self.snapshot+=1
            self.calls.append((self.snapshot,page))
            return document({'data':[financial_row(str(page))],
                'totalCount':3 if self.snapshot==1 and page==2 else 2,'page':page-1})
    h=HTTP()
    rows=paged_financials(h,DAY,DAY,max_pages=3)
    assert len(rows)==2 and h.calls==[(1,1),(1,2),(2,1),(2,2)]


def test_financial_pagination_continuous_changes_remain_blocked_after_three_attempts():
    class HTTP:
        def __init__(self):self.calls=0
        def api(self,*a,**k):
            self.calls+=1
            page=k['page']
            return document({'data':[financial_row(str(page))],
                'totalCount':2 if page==1 else 3,'page':page-1})
    h=HTTP()
    with pytest.raises(SourceError,match='pagination_changed'):
        paged_financials(h,DAY,DAY,max_pages=3)
    assert h.calls==6


def shp_xml(
    *,
    pledged=None,
    zero_flag="false",
    period="2026-09-30",
    symbol="TEST",
    extra_members="",
):
    def ctx(cid, member):
        return f'<context id="{cid}"><entity><identifier>{symbol}</identifier></entity><period><instant>{period}</instant></period><scenario><explicitMember dimension="CategoryOfShareholdersAxis">{member}</explicitMember>{extra_members}</scenario></context>'

    xml = (
        "<root>"
        + ctx("promoter", "ShareholdingOfPromoterAndPromoterGroupMember")
        + ctx("total", "ShareholdingPatternMember")
    )
    xml += f'<context id="main"><entity><identifier>{symbol}</identifier></entity><period><instant>{period}</instant></period></context>'
    xml += '<NumberOfShares contextRef="total">1000</NumberOfShares><NumberOfShares contextRef="promoter">500</NumberOfShares>'
    if pledged is not None:
        xml += f'<NumberOfSharesPledgedOrOtherwiseEncumbered contextRef="promoter">{pledged}</NumberOfSharesPledgedOrOtherwiseEncumbered>'
    if zero_flag is not None:
        xml += f'<WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged contextRef="main">{zero_flag}</WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged>'
    return (xml + "</root>").encode()


def shp_record():
    return dict(
        symbol="TEST",
        date="30-Sep-2026",
        pr_and_prgrp="50",
        broadcastDate="16-Oct-2026 16:00:00",
    )


def test_promoter_pledge_denominator_is_promoter_shares():
    r = parse_shareholding(shp_xml(pledged="50", zero_flag=None), shp_record())
    assert r["promoter_pledge_pct"] == 10 and r["promoter_holding_pct"] == 50
    assert r["total_shares"] == 1000 and r["filed_at"].tzinfo == IST


def test_zero_pledge_requires_explicit_disclosure():
    assert parse_shareholding(shp_xml(), shp_record())["promoter_pledge_pct"] == 0
    with pytest.raises(SourceError):
        parse_shareholding(shp_xml(zero_flag=None), shp_record())


def bse_shp_xml(*, symbol="TEST", isin="INE000A01010", code="533012",
                scheme="http://www.bseindia.com/in-bse-shp/ScripCode",
                header_context="header", header_period="2026-09-30"):
    # Minimal synthetic reproduction of the official LPDC identity structure;
    # no individual shareholder disclosures are included in the fixture.
    xml = shp_xml().decode().replace(
        "<identifier>TEST</identifier>",
        f'<identifier scheme="{scheme}">{code}</identifier>')
    header = (f'<context id="header"><entity><identifier scheme="{scheme}">{code}'
              f'</identifier></entity><period><startDate>2026-07-01</startDate>'
              f'<endDate>{header_period}</endDate></period></context>'
              f'<Symbol contextRef="{header_context}">{symbol}</Symbol>'
              f'<ISIN contextRef="{header_context}">{isin}</ISIN>'
              f'<ScripCode contextRef="{header_context}">{code}</ScripCode>')
    return xml.replace("</root>", header + "</root>").encode()


def test_shareholding_accepts_bse_code_only_with_linked_symbol_and_isin():
    record = dict(shp_record(), isin="INE000A01010")
    assert parse_shareholding(bse_shp_xml(), record)["total_shares"] == 1000


@pytest.mark.parametrize("changes", [
    {"symbol": "OTHER"}, {"isin": "INE111A01010"}, {"code": "TEST"},
    {"scheme": "https://untrusted.example/ScripCode"},
    {"header_context": "missing"}, {"header_period": "2026-06-30"},
])
def test_shareholding_rejects_unlinked_bse_identity(changes):
    with pytest.raises(SourceError):
        parse_shareholding(bse_shp_xml(**changes), dict(shp_record(), isin="INE000A01010"))


def test_shareholding_bse_identity_requires_listing_isin():
    with pytest.raises(SourceError):
        parse_shareholding(bse_shp_xml(), shp_record())


def test_shareholding_current_xml_identity_can_resolve_stale_listing_isin():
    record = dict(shp_record(), isin='INE111A01010')
    assert parse_shareholding(bse_shp_xml(), record,
        expected_isin='INE000A01010')['total_shares'] == 1000
    # A matching summary symbol alone cannot admit an old or unrelated XML ISIN.
    with pytest.raises(SourceError, match='shareholding_isin_mismatch'):
        parse_shareholding(bse_shp_xml(isin='INE111A01010'), record,
                          expected_isin='INE000A01010')


def test_new_encumbrance_taxonomy_uses_promoter_count_not_percentage_of_issuer():
    xml = shp_xml(pledged='50', zero_flag=None).replace(
        b'NumberOfSharesPledgedOrOtherwiseEncumbered', b'NumberOfSharesEncumbered')
    assert parse_shareholding(xml, shp_record())['promoter_pledge_pct'] == 10
    conflicting = xml.replace(b'</root>',
        b'<NumberOfSharesPledgedOrOtherwiseEncumbered contextRef="promoter">1'
        b'</NumberOfSharesPledgedOrOtherwiseEncumbered></root>')
    with pytest.raises(SourceError, match='pledge_ambiguous'):
        parse_shareholding(conflicting, shp_record())


def test_scrr_denominator_reconciles_equity_and_depository_receipts():
    xml = shp_xml().replace(b'</root>',
        b'<NumberOfFullyPaidUpEquityShares contextRef="total">900</NumberOfFullyPaidUpEquityShares>'
        b'<NumberOfSharesUnderlyingOutstandingDepositoryReceipts contextRef="total">100'
        b'</NumberOfSharesUnderlyingOutstandingDepositoryReceipts></root>')
    result = parse_shareholding(xml, dict(shp_record(), pr_and_prgrp='55.56'))
    assert result['total_shares'] == 1000
    assert result['percentage_reconciliation']['percentage_denominator'] == 900
    assert abs(result['promoter_holding_pct'] - Decimal('55.56')) < Decimal('.01')
    with pytest.raises(SourceError, match='totals_invalid'):
        parse_shareholding(xml.replace(b'>900<', b'>899<'), shp_record())


def test_shareholding_rejects_duplicate_context_ids():
    xml = shp_xml().replace(b'</root>',
        b'<context id="total"><entity><identifier>TEST</identifier></entity>'
        b'<period><instant>2026-09-30</instant></period></context></root>')
    with pytest.raises(SourceError, match="duplicate_context"):
        parse_shareholding(xml, shp_record())


@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "OTHER"},
        {"period": "2026-06-30"},
        {
            "extra_members": '<explicitMember dimension="OtherAxis">Subgroup</explicitMember>'
        },
        {"pledged": "501"},
    ],
)
def test_shareholding_cannot_mix_entities_periods_or_subgroups(changes):
    with pytest.raises(SourceError):
        parse_shareholding(shp_xml(**changes), shp_record())


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value[0] if self.value else None

    def all(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value or None

    def scalars(self):
        return self


class Connection:
    def __init__(self, engine):
        self.engine = engine

    def execute(self, sql, params=None):
        self.engine.calls.append((str(sql), params))
        query = str(sql)
        if (
            query.startswith("insert into pms.provider_snapshots")
            and "from jsonb_to_recordset" in query
        ):
            for r in json.loads(params["rows"]):
                self.engine.pending[(params["dataset"], r["key"])] = r["record"]
        if "select p.metadata->'record'" in query:
            return Result(
                [
                    {"record": r}
                    for (dataset, key), r in self.engine.pending.items()
                    if dataset == params["pending"]
                    and (params["ack"], key) not in self.engine.acks
                ]
            )
        if (
            query.startswith("insert into pms.provider_snapshots")
            and params
            and "metadata" in params
            and "dataset" in params
        ):
            meta = json.loads(params["metadata"])
            if meta.get("complete") and meta.get("record_key"):
                self.engine.acks.add((params["dataset"], meta["record_key"]))
        if "select metadata->>'record_key'" in query:
            return Result(
                [
                    key
                    for dataset, key in self.engine.acks
                    if dataset == params["dataset"]
                ]
            )
        if "select max((metadata" in query:
            return Result(self.engine.discovery_day)
        if "pg_get_viewdef" in str(sql):
            from packages.data_layer.cloud_storage import WRITABLE_TARGETS

            return Result(
                self.engine.layout
                or [
                    dict(relname=k, relkind="r", definition=None)
                    for k in WRITABLE_TARGETS
                ]
            )
        if "pg_try_advisory_lock" in str(sql):
            return Result(self.engine.lock)
        if "select id,nse_symbol" in str(sql):
            return Result(self.engine.universe)
        if "select status,started_at" in str(sql):
            return Result(self.engine.gate_rows)
        return Result([])

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class Engine:
    def __init__(self):
        self.calls = []
        self.lock = True
        self.gate_rows = []
        self.layout = None
        self.pending = {}
        self.acks = set()
        self.discovery_day = None
        self.universe = [
            dict(
                id=1,
                symbol="TEST",
                isin="INE000000001",
                listed_from=date(2020, 1, 1),
                sector="Services",
                metadata={},
            )
        ]

    def connect(self):
        return Connection(self)

    def begin(self):
        return Connection(self)


def test_missing_or_expired_collection_receipt_never_admits_entries():
    e = Engine()
    c = e.connect()
    assert not collection_gate(c, DAY, NOW)
    for status, stamp, complete in [
        ("FAILED", NOW, True),
        ("PARTIAL", NOW, True),
        ("SUCCESS", NOW - timedelta(hours=3), True),
        ("SUCCESS", NOW + timedelta(seconds=1), True),
        ("SUCCESS", NOW, False),
    ]:
        e.gate_rows = [
            dict(
                status=status,
                started_at=NOW,
                finished_at=stamp,
                details=dict(
                    version="CLOUD_COLLECTION_v1",
                    trade_date=str(DAY),
                    complete=complete,
                    raw_archive={"status": "VERIFIED", "anonymous_read_denied": True},
                ),
            )
        ]
        assert not collection_gate(c, DAY, NOW)
    e.gate_rows = [
        dict(
            status="SUCCESS",
            started_at=NOW,
            finished_at=NOW,
            details=dict(
                version="CLOUD_COLLECTION_v1", trade_date=str(DAY), complete=True,
                raw_archive={"status": "VERIFIED", "anonymous_read_denied": True}
            ),
        )
    ]
    assert collection_gate(c, DAY, NOW)
    e.gate_rows[0]["details"]["raw_archive"]["anonymous_read_denied"] = False
    assert not collection_gate(c, DAY, NOW)
    e.gate_rows[0]["details"].pop("raw_archive")
    assert not collection_gate(c, DAY, NOW)


def test_master_isin_conflict_cannot_rewrite_identity():
    e = Engine()
    s = CloudStore(e)
    with pytest.raises(SourceError):
        s.master({"TEST": dict(isin="INE999999999")}, document("x"))
    assert all(not q.startswith(("update", "insert")) for q, p in e.calls)


def test_master_cannot_create_second_active_identity_for_same_isin():
    s=CloudStore(None)
    s.instruments=lambda:{'OLD':{'isin':'INE545A01024'}}
    with pytest.raises(SourceError,match='symbol_change_requires_review'):
        s.master({'NEW':{'isin':'INE545A01024'}},document('x'))


def test_reviewed_old_price_identity_is_bounded_and_does_not_change_master_gate():
    from packages.data_layer.cloud_storage import reviewed_price_isin_matches
    r = dict(isin='INE0B9K01033',metadata={'reviewed_isin_history': [dict(
        previous_isin='INE0B9K01025',current_isin='INE0B9K01033',reviewed=True,
        observed_on='2026-10-20',allow_previous_through='2026-10-19',
        master_sha256='a'*64,quote_sha256='b'*64)]})
    assert reviewed_price_isin_matches(r,'INE0B9K01025',date(2026,10,13))
    assert not reviewed_price_isin_matches(r,'INE0B9K01025',date(2026,10,20))
    assert not reviewed_price_isin_matches(r,'INE999999999',date(2026,10,13))
    r['metadata']['reviewed_isin_history'][0]['reviewed'] = False
    assert not reviewed_price_isin_matches(r,'INE0B9K01025',date(2026,10,13))


def test_incomplete_price_universe_stays_partial_even_when_rows_are_stored():
    e = Engine()
    s = CloudStore(e)
    result = s.prices([], document("x"), DAY, {"TEST": {"status": "A"}})
    assert result["complete"] is False and result["missing_symbols"] == ["TEST"]
    assert any("provider_snapshots" in q for q, p in e.calls)


def test_ambiguous_price_series_is_rejected():
    e = Engine()
    s = CloudStore(e)
    r = parse_bhavcopy(price_zip(), DAY)[0]
    with pytest.raises(SourceError):
        s.prices([r, r], document("x"), DAY, {"TEST": {"status": "A"}})


def test_surveillance_missing_universe_cannot_close_previous_restrictions():
    e = Engine()
    s = CloudStore(e)
    with pytest.raises(SourceError):
        s.surveillance({}, [document("x")], DAY)
    assert not any(q.startswith("update") for q, p in e.calls)


def test_empty_active_surveillance_is_valid_only_with_complete_indicator_snapshot():
    e = Engine()
    s = CloudStore(e)
    r = s.surveillance(
        {"TEST": dict(status="A", series="EQ", ASM=None, GSM=None)},
        [document("x")],
        DAY,
    )
    assert r["complete"] and r["restrictions"] == 0
    assert any(
        "provider_snapshots" in q
        and json.loads(p["metadata"])["types"] == ["ASM", "GSM"]
        for q, p in e.calls
        if p and "metadata" in p
    )


def test_cloud_status_is_point_in_time_and_can_mark_suspension():
    cfg = load_strategy_config()
    base = dict(
        metadata={"cloud_status": {"observed_at": NOW.isoformat(), "suspended": True}},
        listed_from=date(2020, 1, 1),
        delisted_on=None,
        trading_symbol="TEST-EQ",
    )
    assert normalize_context(base, NOW - timedelta(minutes=1), cfg)["suspended"] is None
    assert normalize_context(base, NOW, cfg)["suspended"] is True


def test_changed_meeting_keeps_conservative_blackout():
    r = parse_meetings(
        document(
            [
                dict(
                    bm_symbol="TEST",
                    bm_date="20-Oct-2026",
                    bm_timestamp="16-Oct-2026 16:00:00",
                    bm_desc="Financial results meeting rescheduled",
                )
            ]
        ),
        NOW,
    )
    assert r[0]["raw"]["change_notice"] is True
    assert r[0]["event_date"] == date(2026, 10, 20)


def test_overlapping_collection_returns_busy_without_journal_or_mutations():
    e = Engine()
    e.lock = False
    r = run_collection(e, now=NOW)
    assert r["status"] == "BUSY" and r["complete"] is False
    assert len(e.calls) == 1


@pytest.mark.parametrize("status", ["SUCCESS", "PARTIAL", "FAILED"])
def test_journal_completion_reflects_actual_collection_and_unlocks(monkeypatch, status):
    import packages.pms.cloud_collection as module

    class Collector:
        def __init__(self, *a, **k):
            self.http = SimpleNamespace(documents=[])
            self.store = a[0]

        def collect(self, *a, **k):
            if status == "FAILED":
                raise SourceError("source_http_403")
            return dict(
                status=status, stages={"prices": {"complete": status == "SUCCESS"}}
            )

    monkeypatch.setattr(module, "CloudCollector", Collector)
    e = Engine()
    r = run_collection(e, now=NOW)
    assert r["status"] == status and r["complete"] == (status == "SUCCESS")
    assert any("pg_advisory_unlock" in q for q, p in e.calls)
    finished = [p for q, p in e.calls if q.startswith("update pms.worker_runs")][0]
    assert json.loads(finished["details"])["complete"] == (status == "SUCCESS")
    assert not any("safety_state" in q or "broker_orders" in q for q, p in e.calls)


def test_plan_never_connects_or_probes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli, "CloudCollector", lambda *a, **k: pytest.fail("plan accessed network")
    )
    assert cli.main(["--plan", "--output", str(tmp_path / "plan.json")]) == 0
    assert json.loads(capsys.readouterr().out)["campaign_started"] is False


def test_probe_failure_is_nonzero_and_redacted(monkeypatch, tmp_path, capsys):
    class Collector:
        def __init__(self, *a, **k):
            self.now = NOW
            self.http = SimpleNamespace(documents=[])

        def collect(self, *a, **k):
            raise RuntimeError("private-database-password")

    monkeypatch.setattr(cli, "CloudCollector", Collector)
    assert cli.main(["--probe", "--output", str(tmp_path / "probe.json")]) == 2
    assert "private-database-password" not in capsys.readouterr().out


def test_run_date_override_is_forbidden():
    with pytest.raises(SystemExit):
        cli.main(["--run", "--date", str(DAY)])


@pytest.mark.parametrize('offset', [0, -1, 8])
def test_initial_catch_up_refuses_current_future_and_old_dates_before_db(offset):
    with pytest.raises(SourceError, match='past_completed_session'):
        run_collection(None, now=NOW, catch_up_date=NOW.date() - timedelta(days=offset))


def test_initial_catch_up_does_not_backdate_current_reference_snapshots(monkeypatch):
    import packages.pms.cloud_collection as module
    target = NOW.date() - timedelta(days=1)
    seen = []
    store = SimpleNamespace(classifications=lambda http, day, limit, listed_through:
        seen.append(('classification', day, limit, listed_through)) or {'remaining': 0})
    collector = module.CloudCollector(store, now=NOW, catch_up_date=target,
                                      max_classifications=500)
    collector.core = lambda day, apply: seen.append(('prices', day)) or {
        'status': 'SUCCESS', 'stages': {}}
    collector.references = lambda day, apply: seen.append(('references', day)) or {
        'status': 'SUCCESS', 'stages': {}}
    assert collector.collect(target, apply=True)['status'] == 'SUCCESS'
    assert seen == [('prices', target), ('references', NOW.date()),
                    ('classification', NOW.date(), 500, target)]


def test_successful_references_never_promote_partial_price_coverage():
    from packages.pms.cloud_collection import CloudCollector
    store=SimpleNamespace(classifications=lambda *a,**k: {'remaining':0})
    c=CloudCollector(store,now=NOW)
    c.core=lambda *a,**k: {'status':'PARTIAL','stages':{'prices':{'complete':False}}}
    c.references=lambda *a,**k: {'status':'SUCCESS','stages':{'financials':{'processed':1}}}
    result=c.collect(DAY,apply=True)
    assert result['status']=='PARTIAL'
    assert result['stages']['financials']['processed']==1
    assert result['stages']['prices']['complete'] is False


def test_dated_source_symbol_maps_only_through_unique_isin():
    s=CloudStore(None)
    s.instruments=lambda: {'CURRENT':{'isin':'INE545A01024'}}
    source={'OLD':{'status':'A','series':'EQ'}}
    states,aliases=s.session_surveillance([{'symbol':'OLD','isin':'INE545A01024'}],source,DAY)
    assert states['CURRENT']==source['OLD'] and source.keys()=={'OLD'}
    assert aliases[0]['source_symbol']=='OLD' and aliases[0]['trade_date']==str(DAY)
    s.instruments=lambda: {'CURRENT':{'isin':'INE545A01024'},'OTHER':{'isin':'INE545A01024'}}
    with pytest.raises(SourceError,match='ambiguous'):
        s.session_surveillance([{'symbol':'OLD','isin':'INE545A01024'}],source,DAY)


def test_initial_catch_up_limits_are_explicit():
    from scripts.bootstrap_cloud_collection import validate_target
    validate_target(NOW.date() - timedelta(days=1), NOW, 24)
    with pytest.raises(SourceError):
        validate_target(NOW.date() - timedelta(days=1), NOW, 25)


def test_next_api_classification_discovers_non_eq_series():
    from packages.data_layer.cloud_sources import fetch_classification

    class HTTP:
        def api(self, path, **params):
            assert path == "NextApi/apiClient/GetQuoteApi"
            if params["functionName"] == "getMetaData":
                return document({"symbol": "TEST", "activeSeries": ["BE"]})
            assert params["series"] == "BE"
            return document(
                {
                    "equityResponse": [
                        {
                            "metaData": {"symbol": "TEST", "isinCode": "INE000000001"},
                            "secInfo": {
                                "sector": "Services",
                                "industryInfo": "Testing",
                            },
                        }
                    ]
                }
            )

    _, classification = fetch_classification(HTTP(), "TEST", "INE000000001")
    assert classification["series"] == "BE" and classification["sector"] == "Services"


def test_next_api_cannot_classify_wrong_isin():
    from packages.data_layer.cloud_sources import fetch_classification

    class HTTP:
        def api(self, path, **params):
            if params["functionName"] == "getMetaData":
                return document({"symbol": "TEST", "activeSeries": ["EQ"]})
            return document(
                {
                    "equityResponse": [
                        {
                            "metaData": {"symbol": "TEST", "isinCode": "INE999999999"},
                            "secInfo": {"sector": "Services"},
                        }
                    ]
                }
            )

    with pytest.raises(SourceError):
        fetch_classification(HTTP(), "TEST", "INE000000001")


def test_verified_rename_reuses_existing_instrument_id():
    e = Engine()
    e.universe[0]["metadata"] = {
        "sector_classification": {
            "quote_symbol": "RENAMED",
            "symbol_alias": {
                "symbol": "RENAMED",
                "effective_from": "2026-09-01",
                "source_url": "https://nsearchives.nseindia.com/content/circulars/CML12345.pdf",
            },
        }
    }
    store = CloudStore(e)
    known = store.instruments()
    assert list(known) == ["RENAMED"] and known["RENAMED"]["id"] == 1
    assert known["RENAMED"]["canonical_symbol"] == "TEST"


def test_new_official_benchmark_source_is_consumed_without_duplicate_sessions():
    from packages.pms.module_e_pipeline import ModuleEPipeline
    from packages.pms.market_calendar import MarketCalendar

    e = Engine()
    ModuleEPipeline(e)._benchmark_rows("NIFTY50", DAY, DAY)
    query = e.calls[-1][0]
    assert "NSE_DAILY_INDEX" in query and "distinct on(trade_date)" in query


def test_positive_price_coverage_stores_turnover_and_receipt():
    e = Engine()
    store = CloudStore(e)
    row = parse_bhavcopy(price_zip(), DAY)[0]
    result = store.prices([row], document("x"), DAY, {"TEST": {"status": "A"}})
    assert result["complete"] is True
    payload = next(p["rows"] for q, p in e.calls if "jsonb_to_recordset" in q)
    assert json.loads(payload)[0]["turnover"] == "100500.25"


def test_cloud_gate_blocks_cached_daily_admission(monkeypatch):
    from packages.pms.formal_paper import FormalPaperExecution

    monkeypatch.setenv("PMS_CLOUD_COLLECTION_REQUIRED", "1")
    e = Engine()
    paper = FormalPaperExecution(e, clock=lambda: NOW)
    monkeypatch.setattr(
        paper, "session", lambda day: (DAY, SimpleNamespace(), "READY", None)
    )
    monkeypatch.setattr(paper, "lock", lambda conn: None)
    r = paper.admit(DAY)
    assert r["status"] == "BLOCKED" and r["paper_orders_created"] == 0
    assert not any("broker_orders" in sql for sql, params in e.calls)


def test_cloud_gate_blocks_pending_buys_before_entry_checks(monkeypatch):
    from packages.pms.formal_paper import FormalPaperExecution

    monkeypatch.setenv("PMS_CLOUD_COLLECTION_REQUIRED", "1")
    e = Engine()
    paper = FormalPaperExecution(e, clock=lambda: NOW)
    cap, reasons = paper._entry_gate(e.connect(), DAY, SimpleNamespace(sessions=[]))
    assert cap == 0 and reasons == ["cloud_collection_missing_partial_failed_or_stale"]


def test_view_layout_routes_only_to_integrated_writable_tables():
    from packages.data_layer.cloud_storage import WRITABLE_TARGETS

    e = Engine()
    e.layout = []
    for logical, target in WRITABLE_TARGETS.items():
        e.layout.extend(
            [
                dict(
                    relname=logical,
                    relkind="v",
                    definition="select * from pms." + target,
                ),
                dict(relname=target, relkind="r", definition=None),
            ]
        )
    store = CloudStore(e)
    store.execute(
        e.connect(),
        "insert into pms.daily_prices(instrument_id) values(:id)",
        {"id": 1},
    )
    assert "pms.cloud_daily_prices" in e.calls[-1][0]
    assert "insert into pms.daily_prices" not in e.calls[-1][0]


def test_unintegrated_view_fails_before_data_write():
    e = Engine()
    e.layout = [
        dict(
            relname="daily_prices",
            relkind="v",
            definition="select * from public.market_bars",
        )
    ]
    with pytest.raises(SourceError, match="migration_010"):
        CloudStore(e).execute(
            e.connect(), "insert into pms.daily_prices(instrument_id) values(1)"
        )
    assert not any(sql.startswith("insert") for sql, p in e.calls)


def test_unprocessed_discovery_survives_source_window_and_new_process():
    from packages.pms.cloud_collection import record_key

    e = Engine()
    raw = financial_row()
    assert CloudStore(e).queue_records("PENDING", "ACK", [raw]) == [raw]
    # A later process sees no such record in the rolling source response.
    assert CloudStore(e).queue_records("PENDING", "ACK", []) == [raw]
    CloudStore(e).ack("ACK", document("x"), record_key(raw))
    assert CloudStore(e).queue_records("PENDING", "ACK", []) == []


def test_discovery_watermark_catches_outages_instead_of_skipping_dates():
    e = Engine()
    e.discovery_day = DAY - timedelta(days=14)
    assert CloudStore(e).financial_window(DAY) == DAY - timedelta(days=15)
    e.discovery_day = DAY - timedelta(days=100)
    with pytest.raises(SourceError):
        CloudStore(e).financial_window(DAY)


def category_fact(member, count, cid):
    return (f'<context id="{cid}"><entity><identifier>TEST</identifier></entity>'
        '<period><instant>2026-09-30</instant></period><scenario>'
        f'<explicitMember dimension="CategoryOfShareholdersAxis">{member}</explicitMember>'
        f'</scenario></context><NumberOfShares contextRef="{cid}">{count}</NumberOfShares>')


def test_omitted_promoter_section_needs_explicit_zero_and_category_reconciliation():
    xml = shp_xml().decode().replace('<NumberOfShares contextRef="promoter">500</NumberOfShares>', '')
    categories = category_fact('PublicShareholdingMember', 900, 'public') + category_fact(
        'SharesHeldByNonPromoterNonPublicShareholdersMember', 100, 'nonpublic')
    xml = xml.replace('</root>', categories + '</root>').encode()
    result = parse_shareholding(xml, dict(shp_record(), pr_and_prgrp='0'))
    assert result['promoter_holding_pct'] == result['promoter_pledge_pct'] == 0
    for bad, summary in [(xml.replace(b'>900<', b'>899<'), '0'), (xml, '1')]:
        with pytest.raises(SourceError, match='promoter_zero_unverified'):
            parse_shareholding(bad, dict(shp_record(), pr_and_prgrp=summary))


@pytest.mark.parametrize('declared,summary', [('0.5', '50'), ('0.5556', '55.56')])
def test_xml_percentage_selects_reconciled_table_basis(declared, summary):
    extra = category_fact('PublicShareholdingMember', 400, 'public') + category_fact(
        'SharesHeldByNonPromoterNonPublicShareholdersMember', 100, 'nonpublic')
    extra += f'<ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="promoter">{declared}</ShareholdingAsAPercentageOfTotalNumberOfShares>'
    xml = shp_xml().replace(b'</root>', (extra + '</root>').encode())
    result = parse_shareholding(xml, dict(shp_record(), pr_and_prgrp=summary))
    assert abs(result['promoter_holding_pct'] - Decimal(summary)) < Decimal('.01')
    assert result['total_shares'] == 1000
    # A corrupted API summary cannot select another denominator against the XML.
    with pytest.raises(SourceError, match='percentage_mismatch'):
        parse_shareholding(xml, dict(shp_record(), pr_and_prgrp='50' if summary != '50' else '55.56'))
    with pytest.raises(SourceError, match='xml_percentage_mismatch'):
        parse_shareholding(xml.replace(f'>{declared}<'.encode(), b'>0.6<'),
                          dict(shp_record(), pr_and_prgrp='60'))


def test_scrr_percentage_keeps_trusts_and_excludes_only_nonpublic_drs():
    extra = category_fact('PublicShareholdingMember', 400, 'public') + category_fact(
        'SharesHeldByNonPromoterNonPublicShareholdersMember', 100, 'nonpublic')
    extra += '''
      <NumberOfFullyPaidUpEquityShares contextRef="total">800</NumberOfFullyPaidUpEquityShares>
      <NumberOfSharesUnderlyingOutstandingDepositoryReceipts contextRef="total">200</NumberOfSharesUnderlyingOutstandingDepositoryReceipts>
      <NumberOfFullyPaidUpEquityShares contextRef="nonpublic">10</NumberOfFullyPaidUpEquityShares>
      <NumberOfSharesUnderlyingOutstandingDepositoryReceipts contextRef="nonpublic">90</NumberOfSharesUnderlyingOutstandingDepositoryReceipts>
      <ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="promoter">0.5495</ShareholdingAsAPercentageOfTotalNumberOfShares>
    '''
    xml = shp_xml(pledged='50').replace(b'</root>', (extra + '</root>').encode())
    record = dict(shp_record(), pr_and_prgrp='54.95')
    result = parse_shareholding(xml, record)
    assert result['total_shares'] == 1000
    assert result['promoter_holding_pct'] == Decimal(50000)/910
    assert result['promoter_pledge_pct'] == 10
    assert result['percentage_reconciliation']['percentage_denominator'] == 910
    for bad in [xml.replace(b'>10<', b'>9<'), xml.replace(b'>90<', b'>91<'),
                xml.replace(b'>200<', b'>80<')]:
        with pytest.raises(SourceError, match='totals_invalid'):
            parse_shareholding(bad, record)
    with pytest.raises(SourceError, match='xml_percentage_mismatch'):
        parse_shareholding(xml.replace(b'>0.5495<', b'>0.56<'), dict(record, pr_and_prgrp='56'))


def test_reviewed_xml_identity_defect_is_document_bound(monkeypatch):
    from hashlib import sha256
    from packages.data_layer import shareholding_identity
    xml = bse_shp_xml(isin='INE000A01011')
    record = dict(shp_record(), isin='INE000A01010', xbrl='https://nsearchives.nseindia.com/corporate/xbrl/test.xml')
    review = dict(xml_sha256=sha256(xml).hexdigest(), symbol='TEST',
        current_isin='INE000A01010', summary_isin='INE000A01010',
        xml_symbol='TEST', xml_isin='INE000A01011', source_url=record['xbrl'],
        period=record['date'], reason='REVIEWED_XML_ISIN_TYPO', master_sha256='a'*64, quote_sha256='b'*64)
    monkeypatch.setattr(shareholding_identity, 'reviews', lambda: [review])
    result = parse_shareholding(xml, record, expected_isin='INE000A01010')
    assert result['total_shares'] == 1000
    assert result['identity_review']['xml_isin'] == 'INE000A01011'
    assert record['isin'] == 'INE000A01010'
    for body, row, expected in [
        (xml+b' ', record, 'INE000A01010'),
        (xml, {**record, 'xbrl': record['xbrl']+'?changed'}, 'INE000A01010'),
        (xml, record, 'INE111A01010'),
        (xml, {**record, 'isin': 'INE111A01010'}, 'INE000A01010'),
    ]:
        with pytest.raises(SourceError, match='shareholding_isin_mismatch'):
            parse_shareholding(body, row, expected_isin=expected)


@pytest.mark.parametrize('quote_isin,meta_isin,market,allowed', [
    (None, 'INE000000001', 'G', True), ('', 'INE000000001', 'G', True),
    ('INE999999999', 'INE000000001', 'G', False),
    (None, 'INE999999999', 'G', False), (None, 'INE000000001', 'N', False),
])
def test_call_auction_classification_requires_independent_current_identity(quote_isin, meta_isin, market, allowed):
    from packages.data_layer.cloud_sources import fetch_classification
    class HTTP:
        def api(self, path, **params):
            if params['functionName'] == 'getMetaData':
                return document({'symbol': 'TEST', 'isin': meta_isin,
                                 'activeSeries': ['BE'], 'marketType': 'G'})
            return document({'equityResponse': [{
                'metaData': {'symbol': 'TEST', 'isinCode': quote_isin,
                             'series': 'BE', 'marketType': market},
                'secInfo': {'sector': 'Capital Goods'}}]})
    if allowed:
        _, result = fetch_classification(HTTP(), 'TEST', 'INE000000001')
        assert result['identity_basis'] == 'METADATA_AND_QUOTE'
        assert result['metadata_sha256'] == 'f'*64
    else:
        with pytest.raises(SourceError, match='classification_identity_mismatch'):
            fetch_classification(HTTP(), 'TEST', 'INE000000001')


def test_reviewed_listing_day_context_keeps_actual_date_and_filing_time(monkeypatch):
    from hashlib import sha256
    from packages.data_layer import shareholding_identity
    xml = bse_shp_xml(header_period='2026-10-01').replace(b'2026-09-30', b'2026-10-01')
    record = dict(shp_record(), isin='INE000A01010', xbrl='https://nsearchives.nseindia.com/corporate/xbrl/listing.xml')
    review = dict(xml_sha256=sha256(xml).hexdigest(), symbol='TEST',
        current_isin='INE000A01010', summary_isin='INE000A01010',
        xml_symbol='TEST', xml_isin='INE000A01010', source_url=record['xbrl'],
        period=record['date'], xml_period='2026-10-01',
        reason='REVIEWED_LISTING_DATE_CONTEXT', master_sha256='a'*64, quote_sha256='b'*64)
    monkeypatch.setattr(shareholding_identity, 'reviews', lambda: [review])
    values = parse_shareholding(xml, record, expected_isin='INE000A01010')
    assert values['period_end'] == date(2026, 10, 1)
    assert values['filed_at'] == datetime(2026, 10, 16, 16, tzinfo=IST)
    assert values['identity_review']['summary_period'] == record['date']
    monkeypatch.setattr(shareholding_identity, 'reviews', lambda: [])
    with pytest.raises(SourceError):
        parse_shareholding(xml, record, expected_isin='INE000A01010')


def test_empty_quote_envelope_does_not_block_verified_non_eq_fallback():
    from packages.data_layer.cloud_sources import fetch_classification
    class HTTP:
        def api(self, path, **params):
            if params['functionName'] == 'getMetaData':
                return document({'symbol': 'TEST'})
            if params['series'] == 'EQ':
                return document({'equityResponse': [{'metaData': None, 'secInfo': None}]})
            assert params['series'] == 'BE'
            return document({'equityResponse': [{
                'metaData': {'symbol': 'TEST', 'isinCode': 'INE000000001'},
                'secInfo': {'sector': 'Services'}}]})
    _, result = fetch_classification(HTTP(), 'TEST', 'INE000000001')
    assert result['series'] == 'BE'
    assert result['identity_basis'] == 'DETAILED_QUOTE'


@pytest.mark.parametrize('logical', [
    'daily_prices', 'financial_filings', 'financial_facts', 'earnings_calendar',
    'shares_outstanding_history', 'index_constituents_history',
])
@pytest.mark.parametrize('broken', ['overlay_missing', 'view_not_integrated'])
def test_layout_report_identifies_exact_blocker_without_weakening_guard(logical, broken):
    from packages.data_layer.cloud_storage import WRITABLE_TARGETS
    e = Engine()
    e.layout = [dict(relname=name, relkind='r', definition=None) for name in WRITABLE_TARGETS]
    target = WRITABLE_TARGETS[logical]
    next(r for r in e.layout if r['relname'] == logical).update(
        relkind='v', definition=f'select * from pms.{target}' if broken == 'overlay_missing'
        else 'select * from public.unrelated_source')
    if broken == 'view_not_integrated':
        e.layout.append(dict(relname=target, relkind='r', definition=None))
    store = CloudStore(e)
    report = store.inspect_tables()
    assert report['status'] == 'BLOCKED'
    assert len(report['problems']) == 1
    item = report['problems'][0]
    assert item['logical'] == logical and item['overlay'] == target
    assert item['problem'] == ('writable_overlay_missing_or_not_a_table' if broken == 'overlay_missing'
                                else 'view_does_not_include_writable_overlay')
    assert item['write_target'] is None
    with pytest.raises(SourceError, match='migration_010') as err:
        store.resolve_tables()
    assert err.value.layout_report == report
    assert not any(sql.lstrip().startswith(('insert', 'update', 'delete', 'alter', 'create')) for sql, _ in e.calls)


def test_step1_explains_layout_failure_without_starting_repairs(monkeypatch):
    from packages.pms import historical_dataset
    from scripts import finalize_step1_core
    from packages.data_layer.cloud_storage import WRITABLE_TARGETS
    monkeypatch.setattr(historical_dataset, 'require_financial_period_identity', lambda *a: None)
    e = Engine()
    e.layout = [dict(relname=name, relkind='r', definition=None)
                for name in WRITABLE_TARGETS if name != 'financial_facts']
    with pytest.raises(RuntimeError, match=r'pms.financial_facts: logical_object_missing'):
        finalize_step1_core.fail_fast_historical_prerequisites(e)
    assert not any(sql.lstrip().startswith(('insert', 'update', 'delete', 'alter', 'create')) for sql, _ in e.calls)
