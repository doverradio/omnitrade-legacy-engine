"""allow live crypto dry run statuses

Revision ID: 20260709_0022
Revises: 20260709_0021
Create Date: 2026-07-09 23:40:00.000000

"""
from collections.abc import Sequence
import re

from alembic import op
import sqlalchemy as sa


revision: str = "20260709_0022"
down_revision: str | None = "20260709_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE_NAME = "live_crypto_orders"
_STATUS_CONSTRAINT_NAME = "ck_live_crypto_orders_status"
_UPGRADE_STATUS_CHECK_SQL = (
    "status IN ('DRY_RUN_READY','DRY_RUN_BLOCKED','PENDING_CONFIRMATION','CONFIRMATION_EXPIRED','VALIDATING',"
    "'RISK_REJECTED','SUBMISSION_PENDING','SUBMITTED','ACKNOWLEDGED','PARTIALLY_FILLED','FILLED','REJECTED',"
    "'CANCELLED','RECONCILIATION_REQUIRED','UNKNOWN')"
)
_DOWNGRADE_STATUS_CHECK_SQL = (
    "status IN ('PENDING_CONFIRMATION','CONFIRMATION_EXPIRED','VALIDATING','RISK_REJECTED','SUBMISSION_PENDING',"
    "'SUBMITTED','ACKNOWLEDGED','PARTIALLY_FILLED','FILLED','REJECTED','CANCELLED','RECONCILIATION_REQUIRED',"
    "'UNKNOWN')"
)


def _list_table_check_constraints() -> list[dict[str, object]]:
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text(
                """
                SELECT
                    con.conname AS constraint_name,
                    pg_get_constraintdef(con.oid, true) AS constraint_def,
                    COALESCE(
                        array_agg(att.attname) FILTER (WHERE att.attname IS NOT NULL),
                        ARRAY[]::text[]
                    ) AS column_names
                FROM pg_constraint con
                LEFT JOIN LATERAL unnest(con.conkey) AS ck(attnum) ON true
                LEFT JOIN pg_attribute att
                    ON att.attrelid = con.conrelid
                    AND att.attnum = ck.attnum
                WHERE con.contype = 'c'
                  AND con.conrelid = to_regclass(:table_name)
                GROUP BY con.oid, con.conname
                ORDER BY con.conname
                """
            ),
            {"table_name": _TABLE_NAME},
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _is_status_constraint(constraint: dict[str, object]) -> bool:
    column_names = constraint.get("column_names") or []
    if any(column_name == "status" for column_name in column_names):
        return True

    constraint_def = str(constraint.get("constraint_def") or "")
    return re.search(r"\bstatus\b", constraint_def.lower()) is not None


def _drop_existing_status_constraints() -> None:
    for constraint in _list_table_check_constraints():
        if not _is_status_constraint(constraint):
            continue
        constraint_name = constraint.get("constraint_name")
        if not isinstance(constraint_name, str) or not constraint_name:
            continue
        op.drop_constraint(constraint_name, _TABLE_NAME, type_="check")


def _create_status_constraint(sql: str) -> None:
    op.create_check_constraint(_STATUS_CONSTRAINT_NAME, _TABLE_NAME, sql)


def upgrade() -> None:
    _drop_existing_status_constraints()
    _create_status_constraint(_UPGRADE_STATUS_CHECK_SQL)


def downgrade() -> None:
    _drop_existing_status_constraints()
    _create_status_constraint(_DOWNGRADE_STATUS_CHECK_SQL)