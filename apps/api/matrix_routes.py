"""Read-only matrix evidence. No HTTP endpoint starts historical computations."""

from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

router = APIRouter(prefix="/v1/research/matrix", tags=["research-matrix"])


def _engine():
    from apps.api.main import db_engine

    return db_engine()


@router.get("")
def matrix_runs(limit: int = Query(30, ge=1, le=100)):
    with _engine().connect() as conn:
        rows = (
            conn.execute(
                text(
                    """select id,created_at,start_date,end_date,strategy_version,
          metrics->'summary' summary,config->'dataset'->>'scope' universe_scope
          from pms.research_runs where run_type='BACKTEST_MATRIX' order by created_at desc,id limit :limit"""
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
    return {"count": len(rows), "runs": [dict(r) for r in rows]}


@router.get("/{run_id}")
def matrix_detail(
    run_id: UUID,
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(50, ge=1, le=100),
    status: str = Query(
        "", pattern="^(|COMPLETED|NO_TRADES|BLOCKED|FAILED|PENDING|RUNNING)$"
    ),
    family: str = Query("", pattern="^(|CORE|ADR|EXIT|RANDOM|COST)$"),
):
    params = {
        "id": run_id,
        "status": status,
        "family": family,
        "limit": limit,
        "offset": (page - 1) * limit,
    }
    base = """from pms.research_runs r cross join lateral jsonb_array_elements(r.metrics->'cells') with ordinality c(value,position)
      where r.id=:id and r.run_type='BACKTEST_MATRIX' and (:status='' or c.value->>'status'=:status)
      and (:family='' or c.value->'case'->>'family'=:family)"""
    with _engine().connect() as conn:
        run = (
            conn.execute(
                text(
                    """select id,created_at,start_date,end_date,strategy_version,metrics->'summary' summary,
          metrics->>'holdout' holdout,config->>'identity' identity,config->>'code_version' code_version,
          config->'dataset'->>'fingerprint' dataset_fingerprint,config->'dataset'->>'scope' universe_scope
          from pms.research_runs where id=:id and run_type='BACKTEST_MATRIX'"""
                ),
                params,
            )
            .mappings()
            .first()
        )
        if not run:
            raise HTTPException(404, "Saved backtest matrix not found")
        total = int(conn.execute(text("select count(*) " + base), params).scalar_one())
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
    return {
        "run": dict(run),
        "cells": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "acceptance": "NOT_CERTIFIED",
    }
