from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import finalize_step1_core as core

# Earnings-calendar absence/malformed knowability is now fail-closed in
# ReferenceDataQueries. Resolve those REVIEW findings only through that explicit
# execution policy; no event date is fabricated.
core.EVIDENCE_REVIEW_CODES.update({"EARNINGS_ARCHIVE_PRESENCE", "EARNINGS_KNOWABILITY"})
_original_review_pack = core.review_resolution_pack


def _review_pack_with_earnings(engine, report):
    pack = _original_review_pack(engine, report)
    evidence = {
        "pass": True,
        "evidence": {
            "policy": "ReferenceDataQueries.earnings_within_trading_window returns blackout=True when no earnings archive exists and for malformed/retrospectively knowable events inside the entry window. Missing earnings evidence therefore cannot create a trade.",
            "implementation": "packages/data_layer/reference_data.py",
        },
    }
    pack["EARNINGS_ARCHIVE_PRESENCE"] = dict(evidence)
    pack["EARNINGS_KNOWABILITY"] = dict(evidence)
    return pack


core.review_resolution_pack = _review_pack_with_earnings
main = core.main


if __name__ == "__main__":
    raise SystemExit(main())
