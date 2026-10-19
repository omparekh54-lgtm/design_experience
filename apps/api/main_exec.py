from __future__ import annotations

import json
import os
import threading


from apps.api.main import app
from apps.api.execution_routes import router as execution_router
from apps.api.data_gap_routes import router as data_gap_router
from apps.api.console_routes import router as console_router
from apps.api.matrix_routes import router as matrix_router



def _reference_gap_backfill_worker() -> None:
    """Run the idempotent Phase-1 reference backfill inside the private service process.

    This is opt-in via RUN_REFERENCE_GAP_BACKFILL=1 so a normal restart never launches
    expensive historical ingestion.  The HTTP server starts first; the work runs in a
    daemon thread and writes concise JSON status lines to Render logs for verification.
    """
    try:
        from apps.api.main import db_engine
        from packages.data_layer.reference_gap_ingestion import ReferenceGapIngestor

        ingestor = ReferenceGapIngestor(db_engine())
        steps = (
            ("shareholding", lambda: ingestor.ingest_shareholding()),
            ("index_membership", ingestor.ingest_index_membership_history),
            ("corporate_actions", ingestor.ingest_corporate_actions),
            ("surveillance_latest", ingestor.ingest_latest_surveillance),
        )
        for name, func in steps:
            try:
                result = func()
                print("REFERENCE_GAP_BACKFILL " + json.dumps({"step": name, "ok": True, "result": result}, default=str), flush=True)
            except Exception as exc:  # each dataset is independent; continue and report all failures
                print("REFERENCE_GAP_BACKFILL " + json.dumps({"step": name, "ok": False, "error_type": type(exc).__name__}), flush=True)
    except Exception as exc:
        print("REFERENCE_GAP_BACKFILL " + json.dumps({"step": "bootstrap", "ok": False, "error_type": type(exc).__name__}), flush=True)


@app.on_event("startup")
def maybe_run_reference_gap_backfill() -> None:
    if os.getenv("RUN_REFERENCE_GAP_BACKFILL", "0").strip().lower() in {"1", "true", "yes", "on"}:
        threading.Thread(target=_reference_gap_backfill_worker, name="reference-gap-backfill", daemon=True).start()


app.include_router(execution_router)
app.include_router(data_gap_router)

app.include_router(console_router)
app.include_router(matrix_router)

from apps.api.walk_forward_routes import router as walk_forward_router
app.include_router(walk_forward_router)

from apps.api.integrity_routes import router as integrity_router
app.include_router(integrity_router)
