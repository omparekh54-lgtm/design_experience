from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone, timedelta
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from apps.api.main_exec import app
from apps.api import integrity_routes
from packages.pms.config import load_strategy_config
from packages.pms.data_integrity import (
    AuditScope,
    IntegrityAuditor,
    REQUIRED,
    EXTRA_INPUTS,
    schema_findings,
    summarize,
    decision_finding,
    write_report,
    read_report,
    persist_audit,
    audit_manifest,
)
from packages.pms.integrity_checks import CHECKS, REVIEWS
from packages.pms.matrix_data import consistent_inputs
from scripts.run_data_integrity_audit import main, export_csv
from test_matrix_data_api import Engine, Result

CFG = load_strategy_config()
SCOPE = AuditScope(date(2018, 1, 1), date(2024, 12, 31))
SCHEMA = [
    dict(table_name=table, column_name=col)
    for table, cols in REQUIRED.items()
    for col in cols
]
DATA = dict(
    fingerprint="test-input",
    universe=[dict(id=1, instrument_id="NSE:TEST")],
    scope="HISTORICAL_MASTER",
)


class ScanResult(Result):
    def one(self):
        return self.value

    def __iter__(self):
        return iter(self.value)


class Snapshot:
    def __init__(self, findings=None, decisions=(), schema=None, fail=None):
        self.connection = self
        self.calls = []
        self.rollback_count = 0
        self.findings = findings or {}
        self.decisions = decisions
        self.schema = SCHEMA if schema is None else schema
        self.fail = fail

    @contextmanager
    def begin_nested(self):
        try:
            yield
        except Exception:
            self.rollback_count += 1
            raise

    def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, params or {}))
        assert not any(
            word in sql.lower()
            for word in ("delete from", "update pms", "insert into", "alter table")
        )
        if "information_schema.columns" in sql:
            return ScanResult(self.schema)
        if "set_config" in sql:
            return ScanResult(None)
        check = next((c for c in CHECKS if c.sql in sql), None)
        assert check, sql
        if check.code == self.fail:
            raise RuntimeError("postgres://user:private-password@private-host")
        if check.code == "A_DECISION_KNOWABILITY":
            return ScanResult(self.decisions)
        return ScanResult(self.findings.get(check.code, dict(total=0, samples=[])))


def auditor(monkeypatch, snapshot=None, checks=None, manifest=None):
    import packages.pms.data_integrity as module

    monkeypatch.setattr(
        module, "audit_manifest", lambda *a: manifest if manifest is not None else DATA
    )
    return IntegrityAuditor(
        snapshot or Snapshot(), SCOPE, CFG, code_version="test-code", checks=checks
    )


def test_registry_unique_fixed_and_complete_source_groups():
    assert (
        len(CHECKS) == 45 and len({c.code for c in CHECKS}) == 45 and len(REVIEWS) == 5
    )
    assert {c.group for c in CHECKS} >= {
        "UNIVERSE",
        "COVERAGE",
        "PRICES",
        "FUNDAMENTALS",
        "POINT_IN_TIME",
        "REFERENCE",
        "ACTIONS",
        "PROVENANCE",
        "DECISIONS",
        "QUALITY",
    }
    assert all(c.sql.lower().lstrip().startswith("select") for c in CHECKS)
    assert all(c.remediation for c in CHECKS)


@pytest.mark.parametrize(
    "changes",
    [
        {"end": date(2017, 1, 1)},
        {"end": date(2099, 1, 1)},
        {"sample_limit": 0},
        {"sample_limit": 101},
        {"statement_timeout_ms": 999},
        {"statement_timeout_ms": 600001},
    ],
)
def test_scope_rejects_invalid_input(changes):
    with pytest.raises(ValueError):
        replace(SCOPE, **changes)


def test_schema_missing_columns_are_detected_for_views_as_well_as_tables():
    assert schema_findings(SCHEMA) == []
    rows = [
        r
        for r in SCHEMA
        if not (
            r["table_name"] == "financial_filings"
            and r["column_name"] == "filing_timestamp"
        )
    ]
    assert schema_findings(rows) == [
        dict(table="financial_filings", missing_columns=["filing_timestamp"])
    ]


def test_clean_automated_checks_require_independent_reviews(monkeypatch):
    report = auditor(monkeypatch).run()
    s = report["summary"]
    assert s["status"] == "REVIEW_REQUIRED" and s["complete"]
    assert len(report["checks"]) == 51 and s["counts"]["REVIEW_REQUIRED"] == 5
    assert s["counts"]["NOT_APPLICABLE"] == 1
    assert s["automated_checks_passed"] and not s["full_data_certification"]
    assert s["acceptance"] == "NOT_CERTIFIED"
    assert report["identity"]


def test_sample_counts_use_total_not_sample_length_and_no_repair(monkeypatch):
    report = auditor(
        monkeypatch,
        Snapshot({"PRICE_VALUES": dict(total=103, samples=[{"instrument_id": 1}])}),
    ).run()
    row = next(r for r in report["checks"] if r["code"] == "PRICE_VALUES")
    assert (
        row["finding_count"] == 103
        and len(row["samples"]) == 1
        and row["status"] == "FAIL"
    )
    assert report["summary"]["status"] == "FAILED"


def test_cross_source_difference_requires_review_not_automatic_corruption(monkeypatch):
    report = auditor(
        monkeypatch, Snapshot({"PRICE_SOURCE_CONFLICTS": dict(total=5, samples=[])})
    ).run()
    row = next(r for r in report["checks"] if r["code"] == "PRICE_SOURCE_CONFLICTS")
    assert (
        row["status"] == "REVIEW_REQUIRED"
        and report["summary"]["automated_checks_passed"]
    )


def test_sql_failure_rolls_back_savepoint_continues_and_redacts_credentials(
    monkeypatch,
):
    source = Snapshot(fail="PRICE_VALUES")
    report = auditor(monkeypatch, source).run()
    failed = next(r for r in report["checks"] if r["code"] == "PRICE_VALUES")
    assert failed["status"] == "ERROR" and failed["finding_count"] is None
    assert source.rollback_count == 1 and len(report["checks"]) == 51
    assert report["summary"]["status"] == "FAILED" and "password" not in json.dumps(
        report
    )
    assert (
        next(r for r in report["checks"] if r["code"] == "OPEN_CRITICAL_DQ")["status"]
        == "PASS"
    )


def test_partial_selection_is_incomplete_and_never_passes(monkeypatch):
    report = auditor(monkeypatch, checks=CHECKS[:1]).run()
    assert (
        report["summary"]["status"] == "INCOMPLETE"
        and report["summary"]["counts"]["NOT_RUN"] == 44
    )
    assert not report["summary"]["automated_checks_passed"]


def test_schema_failure_skips_dependent_queries_preserves_review_and_identity(
    monkeypatch,
):
    source = Snapshot(schema=[])
    report = auditor(monkeypatch, source).run()
    assert len(source.calls) == 1 and report["summary"]["counts"]["FAIL"] == 1
    assert (
        report["summary"]["counts"]["NOT_RUN"] == 45
        and report["summary"]["status"] == "INCOMPLETE"
    )
    assert report["identity"] and report["dataset"] is None


def test_empty_and_sampled_universes_do_not_become_global_passes(monkeypatch):
    report = auditor(monkeypatch, manifest=dict(DATA, universe=[])).run()
    assert report["summary"]["counts"]["FAIL"] == 1
    assert any(r["code"] == "EMPTY_UNIVERSE" for r in report["checks"])
    report = auditor(
        monkeypatch, manifest=dict(DATA, scope="EXPLICIT_SYMBOL_SAMPLE")
    ).run()
    assert (
        report["summary"]["scope"] == "EXPLICIT_SYMBOL_SAMPLE"
        and not report["summary"]["full_data_certification"]
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2020-01-01T10:00:00+05:30", None),
        ("2020-01-03T10:00:00+05:30", "filing_after_decision"),
        ("2020-01-01", "ambiguous_timestamp_without_timezone"),
        ("2020-02-30T10:00:00+00:00", "malformed_filing_timestamp"),
        ("not-a-time", "malformed_filing_timestamp"),
        (None, "eligible_decision_without_filing_timestamp"),
    ],
)
def test_decision_timestamp_parsing_uses_timezones_and_never_unsafe_casts(
    raw, expected
):
    assert (
        decision_finding(
            dict(
                eligible=True,
                filing_timestamp=raw,
                as_of=datetime(2020, 1, 2, tzinfo=timezone.utc),
            )
        )
        == expected
    )


def test_ineligible_missing_filing_is_allowed_and_future_eligible_is_failed(
    monkeypatch,
):
    assert decision_finding(dict(eligible=False, filing_timestamp=None)) is None
    decisions = [
        dict(
            id=1,
            eligible=True,
            filing_timestamp="2020-01-03T10:00:00+05:30",
            as_of=datetime(2020, 1, 2, tzinfo=timezone.utc),
        )
    ]
    report = auditor(monkeypatch, Snapshot(decisions=decisions)).run()
    row = next(r for r in report["checks"] if r["code"] == "A_DECISION_KNOWABILITY")
    assert (
        row["status"] == "FAIL"
        and row["finding_count"] == 1
        and row["observed_decisions"] == 1
    )


def test_reports_checksum_csv_and_partial_progress_are_auditable(monkeypatch, tmp_path):
    path = tmp_path / "audit.json"
    seen = []

    def progress(report):
        write_report(report, path)
        seen.append(read_report(path)["summary"]["status"])

    report = auditor(monkeypatch).run(on_progress=progress)
    assert seen[0] == "INCOMPLETE" and seen[-1] == "REVIEW_REQUIRED"
    assert read_report(path)["report_hash"] == report["report_hash"]
    export_csv(report, path)
    assert "PRICE_VALUES" in path.with_suffix(".csv").read_text()
    path.write_text(path.read_text().replace("NOT_CERTIFIED", "CERTIFIED"))
    with pytest.raises(ValueError, match="checksum"):
        read_report(path)


def test_interruption_preserves_incomplete_report(monkeypatch, tmp_path):
    path = tmp_path / "partial.json"

    def progress(report):
        write_report(report, path)
        if len(report["checks"]) == 3:
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        auditor(monkeypatch).run(on_progress=progress)
    assert read_report(path)["summary"]["status"] == "INCOMPLETE"


def test_manifest_includes_operational_decisions_and_all_provider_provenance(
    monkeypatch,
):
    import packages.pms.data_integrity as module

    monkeypatch.setattr(module, "input_manifest", lambda *a, **kw: dict(DATA))
    engine = Engine([[dict(bucket="aa", rows=1, digest="hash")] for _ in EXTRA_INPUTS])
    report = audit_manifest(engine, SCOPE, None, datetime.now(timezone.utc))
    assert set(report["audit_additional_inputs"]) == set(EXTRA_INPUTS)
    assert len(report["fingerprint"]) == 64 and report["fingerprint"] != "test-input"
    assert all("pms." in sql for sql, _ in engine.calls)


def test_persistence_writes_only_finished_research_record_and_checks_identity(
    monkeypatch,
):
    report = auditor(monkeypatch).run()
    engine = Engine([None])
    persist_audit(engine, report)
    sql, params = engine.calls[0]
    assert (
        "pms.research_runs" in sql
        and "DATA_INTEGRITY_AUDIT" in sql
        and "config->>'identity'" in sql
    )
    assert "data_quality_issues" not in sql and "update safety" not in sql
    assert json.loads(params["metrics"])["summary"]["acceptance"] == "NOT_CERTIFIED"
    with pytest.raises(ValueError):
        persist_audit(engine, dict(report, finished=False))


def test_api_is_read_only_bounds_pagination_and_rejects_unknown_status(monkeypatch):
    engine = Engine([[dict(id="x", summary={})]])
    monkeypatch.setattr(integrity_routes, "_engine", lambda: engine)
    client = TestClient(app)
    assert client.get("/v1/research/data-integrity").json()["count"] == 1
    assert "DATA_INTEGRITY_AUDIT" in engine.calls[-1][0]
    assert client.post("/v1/research/data-integrity").status_code == 405
    assert client.get("/v1/research/data-integrity?limit=101").status_code == 422
    ident = "00000000-0000-4000-8000-000000000001"
    engine.values = [
        dict(id=ident, summary={}),
        2,
        [dict(code="PRICE_VALUES", status="FAIL")],
    ]
    response = client.get(
        f"/v1/research/data-integrity/{ident}?page=2&limit=1&status=FAIL"
    )
    assert response.status_code == 200 and response.json()["total"] == 2
    assert (
        engine.calls[-1][1]["offset"] == 1 and engine.calls[-1][1]["status"] == "FAIL"
    )
    assert (
        client.get(f"/v1/research/data-integrity/{ident}?status=UNKNOWN").status_code
        == 422
    )
    assert client.get("/v1/research/data-integrity/not-uuid").status_code == 422
    engine.values = [None]
    assert client.get(f"/v1/research/data-integrity/{ident}").status_code == 404


def test_cli_plan_offline_and_no_implicit_execution_cutoff(capsys):
    assert main(["--plan"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["checks"]) == 45 and len(report["independent_reviews"]) == 5
    assert not report["source_writes"] and not report["full_certification"]
    with pytest.raises(SystemExit):
        main(["--run"])


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--plan", "--run"],
        ["--plan", "--checks", "INJECTED"],
        ["--run", "--end", "2099-01-01"],
        ["--run", "--end", "2024-12-31", "--sample-limit", "0"],
    ],
)
def test_cli_rejects_invalid_input(args):
    with pytest.raises(SystemExit):
        main(args)


def test_audit_manifest_retains_duplicate_identities_for_findings_but_matrix_stays_strict():
    from test_matrix_data_api import manifest_values, CELL
    from packages.pms.matrix_data import input_manifest

    values = manifest_values()
    values[-1].append(dict(values[-1][0], id=2))
    with pytest.raises(ValueError, match="ambiguous"):
        input_manifest(Engine(values), [CELL])
    values = manifest_values()
    values[-1].append(dict(values[-1][0], id=2))
    manifest = input_manifest(Engine(values), [CELL], allow_ambiguous=True)
    assert (
        len(manifest["universe"]) == 2
        and manifest["universe"][0]["instrument_id"]
        == manifest["universe"][1]["instrument_id"]
    )


def test_csv_extension_cannot_overwrite_json_report(tmp_path):
    with pytest.raises(ValueError, match="json"):
        export_csv({"checks": []}, tmp_path / "evidence.csv")
    assert not (tmp_path / "evidence.csv").exists()


def test_cli_rejects_non_json_report_before_database_access():
    with pytest.raises(SystemExit):
        main(["--run", "--end", "2024-12-31", "--report", "evidence.csv"])
