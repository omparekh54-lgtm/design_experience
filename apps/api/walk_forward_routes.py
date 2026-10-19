"""Read-only V2 walk-forward evidence, kept separate from legacy lab results."""

from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

router = APIRouter(prefix="/v1/research/walk-forward/studies", tags=["walk-forward"])


def _engine():
    from apps.api.main import db_engine

    return db_engine()


@router.get("")
def studies(limit: int = Query(30, ge=1, le=100)):
    with _engine().connect() as conn:
        rows = (
            conn.execute(
                text(
                    """select id,created_at,start_date,end_date,strategy_version,metrics->'summary' summary
        from pms.research_runs where run_type='WALK_FORWARD_V2' order by created_at desc,id limit :limit"""
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
    return dict(count=len(rows), runs=[dict(r) for r in rows])


@router.get("/{run_id}")
def study(
    run_id: UUID,
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(20, ge=1, le=100),
):
    params = dict(id=run_id, limit=limit, offset=(page - 1) * limit)
    with _engine().connect() as conn:
        run = (
            conn.execute(
                text(
                    """select id,created_at,start_date,end_date,strategy_version,metrics->'summary' summary,
        metrics->'policy' policy,metrics->'candidates' candidates,config->>'identity' identity,config->>'code_version' code_version,
        config->'datasets' datasets from pms.research_runs where id=:id and run_type='WALK_FORWARD_V2'"""
                ),
                params,
            )
            .mappings()
            .first()
        )
        if not run:
            raise HTTPException(404, "Saved isolated walk-forward study not found")
        # Full candidate metrics/journals stay local; only small fold summaries cross this API.
        rows = (
            conn.execute(
                text(
                    """select c.value from pms.research_runs r cross join lateral
        jsonb_array_elements(r.metrics->'folds') with ordinality c(value,position)
        where r.id=:id and r.run_type='WALK_FORWARD_V2' order by c.position limit :limit offset :offset"""
                ),
                params,
            )
            .scalars()
            .all()
        )
    run = dict(run)
    # Avoid retransmitting universe inventories/hashes for every phase; retain reproducibility identities.
    datasets = run.pop("datasets") or {}
    run["datasets"] = {
        name: {
            phase: {k: data.get(k) for k in ("fingerprint", "scope", "start", "end")}
            for phase, data in phases.items()
        }
        for name, phases in datasets.items()
    }
    return dict(
        run=run,
        folds=rows,
        total=(run.get("summary") or {}).get("folds", 0),
        page=page,
        limit=limit,
        acceptance="NOT_CERTIFIED",
    )
