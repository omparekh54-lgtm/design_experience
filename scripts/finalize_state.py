"""Serialize certification attempts and invalidate superseded success evidence.

Checkpoint files remain intact; only the current acceptance claim is replaced.
The OS releases the lock even when a process crashes. A killed attempt leaves a
RUNNING certificate, which consumers must reject rather than reuse old success.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from packages.pms.matrix_data import report_lock


def write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_finalizer(fn, certificate: Path, step: str, *, dependent: Path | None = None) -> int:
    try:
        with report_lock(certificate.parent / "finalization"):
            write_state(certificate, {
                "status": f"{step}_RUNNING", "pending_repairs": 1,
                "pending_tasks": [{"check": "FINALIZATION_IN_PROGRESS"}],
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
            if dependent is not None and dependent.exists():
                write_state(dependent, {"status": "STEP10_BLOCKED",
                                       "reason": "Step 1 recertification started"})
            try:
                result = fn()
            except BaseException as exc:
                write_state(certificate, {"status": f"{step}_BLOCKED",
                                         "pending_repairs": 1,
                                         "pending_tasks": [{"check": "FINALIZER_STOPPED",
                                                            "error_type": type(exc).__name__}]})
                raise
            if result != 0:
                state = json.loads(certificate.read_text(encoding="utf-8"))
                # Keep the detailed repair inventory if the finalizer wrote it.
                if state.get("status") == f"{step}_RUNNING":
                    write_state(certificate, {"status": f"{step}_BLOCKED",
                                             "pending_repairs": 1,
                                             "pending_tasks": [{"check": "FINALIZER_STOPPED",
                                                                "exit_code": result}]})
            return result
    except RuntimeError as exc:
        if str(exc) != "Another matrix process owns this report checkpoint":
            raise
        print("STOPPED: another Step 1/Step 10 finalizer is already running", flush=True)
        return 2
