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
                version="CLOUD_COLLECTION_v1", trade_date=str(DAY), complete=True
            ),
        )
    ]
    assert collection_gate(c, DAY, NOW)


def test_master_isin_conflict_cannot_rewrite_identity():
    e = Engine()
    s = CloudStore(e)
    with pytest.raises(SourceError):
        s.master({"TEST": dict(isin="INE999999999")}, document("x"))
    assert all(not q.startswith(("update", "insert")) for q, p in e.calls)


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
