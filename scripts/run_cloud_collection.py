"""Official automated EOD collection; no broker keys and no order permissions changed."""

from __future__ import annotations
import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.data_layer.cloud_sources import IST, SourceError
from packages.pms.cloud_collection import CloudCollector, run_collection


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument(
        "--probe",
        action="store_true",
        help="Network/schema probe, no database or trading; XML sampling only",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="Explicit database ingestion; today after market close",
    )
    p.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Probe date only; choose a known completed session",
    )
    p.add_argument("--max-filings", type=int, default=200)
    p.add_argument("--output", type=Path)
    a = p.parse_args(argv)
    if a.date and not a.probe:
        p.error("--date is for --probe only")
    if a.output and a.output.exists():
        p.error("Output exists; preserve prior evidence")
    if not 1 <= a.max_filings <= 500:
        p.error("--max-filings must be 1..500")
    if a.plan:
        report = dict(
            status="PLAN",
            datasets=[
                "calendar",
                "master",
                "raw_prices",
                "four_benchmarks",
                "ASM_GSM_and_suspensions",
                "financial_XML",
                "shareholding_XML_and_shares",
                "earnings",
                "corporate_actions",
                "NIFTY500_membership",
                "sector_classifications",
            ],
            database_writes=False,
            campaign_started=False,
            activation="Phase 3 configures PMS_CLOUD_COLLECTION_ENABLED=true; PAPER activation is separate",
            limitations=[
                "Probe samples XML; it is not full-universe verification",
                "Initial XML/classification backlogs remain PARTIAL until processed",
                "Actual private archive access and operational alerts require deployment verification",
            ],
        )
    elif a.probe:
        collector = CloudCollector(now=datetime.now(IST))
        target = a.date or collector.now.date()
        if target > collector.now.date():
            p.error("Probe date cannot be in the future")
        try:
            report = collector.collect(target)
        except Exception as exc:
            report = dict(
                status="FAILED",
                error_type=type(exc).__name__,
                error_code=(
                    str(exc)
                    if isinstance(exc, SourceError)
                    else "source_probe_exception"
                ),
            )
        report.update(
            mode="PROBE",
            database_writes=False,
            full_universe_verified=False,
            campaign_started=False,
            trade_date=target.isoformat(),
            observed_at=datetime.now(IST).isoformat(),
            documents=[
                dict(
                    url=d.url,
                    sha256=d.sha256,
                    bytes=d.size,
                    observed_at=d.observed_at.isoformat(),
                )
                for d in collector.http.documents
            ],
        )
    else:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
        from packages.pms.database import online_engine
        from apps.api.main import _render_safe_database_url

        engine = None
        try:
            raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
            if not raw:
                raise SourceError("database_secret_missing")
            engine = online_engine(_render_safe_database_url(raw), worker=True)
            from packages.data_layer.private_archive import PrivateArchive
            from packages.data_layer.cloud_sources import OfficialHTTP
            archive = PrivateArchive.from_env()
            report = run_collection(engine, max_filings=a.max_filings,
                                    http=OfficialHTTP(archive=archive), require_archive=True)
        except Exception as exc:
            report = dict(
                status="FAILED",
                error_type=type(exc).__name__,
                error_code=(
                    str(exc)
                    if isinstance(exc, SourceError)
                    else "collection_unavailable"
                ),
                campaign_started=False,
            )
        finally:
            if engine is not None:
                engine.dispose()
    output = a.output or ROOT / "artifacts" / (
        "cloud-collection-" + str(uuid4()) + ".json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, default=str, indent=2, allow_nan=False)
    with output.open("x", encoding="utf-8") as f:
        f.write(rendered + "\n")
    print(rendered)
    return 0 if report["status"] in {"PLAN", "SUCCESS", "SKIPPED"} else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            "Collection unavailable (" + type(exc).__name__ + "); details withheld",
            file=sys.stderr,
        )
        raise SystemExit(2)
