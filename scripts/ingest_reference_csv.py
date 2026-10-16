from __future__ import annotations

import argparse
import csv
import os
from datetime import date, datetime

from sqlalchemy import create_engine, text

from packages.data_layer.reference_data import CorporateAction
from packages.data_layer.reference_storage import ReferenceDataStorage, parse_nse_shareholding_row


def read_rows(path: str):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


def parse_date(value: str) -> date:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(value)


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest official NSE/BSE reference CSV exports")
    p.add_argument("--shareholding", action="append", default=[], metavar="SYMBOL:FILE")
    p.add_argument("--pledge", action="append", default=[], metavar="SYMBOL:FILE")
    p.add_argument("--index", action="append", default=[], metavar="INDEX:DATE:FILE")
    p.add_argument("--corporate-actions", action="append", default=[], metavar="SYMBOL:FILE")
    p.add_argument("--asm", action="append", default=[], metavar="DATE:FILE")
    p.add_argument("--gsm", action="append", default=[], metavar="DATE:FILE")
    args = p.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    engine = create_engine(url, pool_pre_ping=True)
    storage = ReferenceDataStorage(engine)

    # Shareholding/pledge official exports are parsed through the same tolerant
    # field alias parser. If a pledge export lacks shareholding facts, it updates
    # a separate point-in-time record and never fabricates missing percentages.
    for spec in args.shareholding + args.pledge:
        symbol, path = spec.split(":", 1)
        records = []
        for row in read_rows(path):
            try:
                records.append(parse_nse_shareholding_row(f"NSE:{symbol.upper()}", row))
            except ValueError:
                continue
        storage.upsert_shareholding(records)
        print(symbol, "shareholding/pledge rows", len(records))

    for spec in args.index:
        index_code, effective, path = spec.split(":", 2)
        symbols = []
        for row in read_rows(path):
            symbol = row.get("Symbol") or row.get("symbol") or row.get("SYMBOL")
            if symbol:
                symbols.append(f"NSE:{symbol.strip().upper()}")
        storage.replace_index_membership(index_code.upper(), parse_date(effective), symbols)
        print(index_code, "members", len(symbols))

    for spec in args.corporate_actions:
        symbol, path = spec.split(":", 1)
        actions = []
        for row in read_rows(path):
            kind = (row.get("Purpose") or row.get("action_type") or row.get("Action") or "").upper()
            ex = row.get("Ex-Date") or row.get("ex_date") or row.get("Ex Date")
            if not ex:
                continue
            action_type = "BONUS" if "BONUS" in kind else "SPLIT" if "SPLIT" in kind or "FACE VALUE" in kind else "DIVIDEND" if "DIVIDEND" in kind else kind
            actions.append(CorporateAction(f"NSE:{symbol.upper()}", action_type, parse_date(ex), source="NSE"))
        storage.upsert_corporate_actions(actions)
        print(symbol, "corporate actions", len(actions))

    for surveillance_type, specs in (("ASM", args.asm), ("GSM", args.gsm)):
        for spec in specs:
            effective, path = spec.split(":", 1)
            observed = datetime.combine(parse_date(effective), datetime.min.time())
            with engine.begin() as conn:
                for row in read_rows(path):
                    symbol = row.get("Symbol") or row.get("symbol") or row.get("SYMBOL")
                    if not symbol:
                        continue
                    trading = f"{symbol.strip().upper()}-EQ"
                    pk = conn.execute(text("select id from instruments where exchange='NSE' and trading_symbol=:s order by id desc limit 1"), {"s": trading}).scalar_one_or_none()
                    if pk is None:
                        continue
                    conn.execute(text("""
                        insert into surveillance_history(instrument_id,surveillance_type,stage,effective_from,source)
                        values (:id,:kind,:stage,:effective,'NSE')
                    """), {"id": int(pk), "kind": surveillance_type, "stage": row.get("Stage") or row.get("stage"), "effective": observed})
            print(surveillance_type, effective, "ingested")


if __name__ == "__main__":
    main()
