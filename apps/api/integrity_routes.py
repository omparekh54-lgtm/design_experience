"""Read-only saved integrity evidence; never starts source scans or repairs."""

from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

router = APIRouter(prefix="/v1/research/data-integrity", tags=["data-integrity"])


def _engine():
    from apps.api.main import db_engine

    return db_engine()


@router.get("")
def audit_runs(limit: int = Query(30, ge=1, le=100)):
    with _engine().connect() as conn:
        rows = (
            conn.execute(
                text(
                    """select id,created_at,start_date,end_date,metrics->'summary' summary,
        config->'dataset'->>'fingerprint' dataset_fingerprint from pms.research_runs
        where run_type='DATA_INTEGRITY_AUDIT' order by created_at desc,id limit :limit"""
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
    return dict(count=len(rows), runs=[dict(r) for r in rows])


@router.get("/{run_id}")
def audit_detail(
    run_id: UUID,
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(25, ge=1, le=100),
    status: str = Query(
        "", pattern="^(|PASS|FAIL|ERROR|REVIEW_REQUIRED|NOT_APPLICABLE|NOT_RUN)$"
    ),
):
    params = dict(id=run_id, limit=limit, offset=(page - 1) * limit, status=status)
    base = """from pms.research_runs r cross join lateral jsonb_array_elements(r.metrics->'checks') with ordinality c(value,position)
    where r.id=:id and r.run_type='DATA_INTEGRITY_AUDIT' and (:status='' or c.value->>'status'=:status)"""
    with _engine().connect() as conn:
        row = (
            conn.execute(
                text(
                    """select id,created_at,start_date,end_date,metrics->'summary' summary,
        metrics->>'report_hash' report_hash,config->>'identity' identity,config->>'code_version' code_version,
        config->>'config_hash' config_hash,config->>'schema_fingerprint' schema_fingerprint,config->'dataset'->>'fingerprint' dataset_fingerprint,
        config->'dataset'->>'scope' universe_scope from pms.research_runs where id=:id and run_type='DATA_INTEGRITY_AUDIT'"""
                ),
                params,
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "Saved data integrity audit not found")
        total = conn.execute(text("select count(*) " + base), params).scalar_one()
        rows = (
            conn.execute(
                text(
                    "select c.value "
                    + base
                    + " order by c.position limit :limit offset :offset"
                ),
                params,
            )
            .scalars()
            .all()
        )
    return dict(
        run=dict(row),
        checks=rows,
        total=total,
        page=page,
        limit=limit,
        acceptance="NOT_CERTIFIED",
    )
