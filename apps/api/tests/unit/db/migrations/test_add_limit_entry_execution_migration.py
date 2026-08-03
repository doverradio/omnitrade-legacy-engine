from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260803_0065_add_limit_entry_execution.py"
)


class _FakeOp:
    def __init__(self) -> None:
        self.added_columns: list[tuple[str, object]] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.created_tables: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.dropped_tables: list[str] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...], bool, dict[str, object]]] = []
        self.dropped_indexes: list[tuple[str, str]] = []
        self.created_constraints: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str]] = []

    def add_column(self, table_name: str, column: object) -> None:
        self.added_columns.append((table_name, column))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped_columns.append((table_name, column_name))

    def create_table(self, table_name: str, *args, **kwargs) -> None:
        self.created_tables.append((table_name, args, kwargs))

    def drop_table(self, table_name: str) -> None:
        self.dropped_tables.append(table_name)

    def create_index(self, name: str, table_name: str, columns: list[str], unique: bool = False, **kwargs) -> None:
        self.created_indexes.append((name, table_name, tuple(columns), unique, kwargs))

    def drop_index(self, name: str, table_name: str) -> None:
        self.dropped_indexes.append((name, table_name))

    def create_check_constraint(self, name: str, table_name: str, condition: str) -> None:
        self.created_constraints.append(("check", name, condition))

    def drop_constraint(self, name: str, table_name: str, type_: str) -> None:
        self.dropped_constraints.append((name, type_))


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260803_0065", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260803_0065"
    assert module.down_revision == "20260801_0064"


def test_upgrade_adds_limit_columns_to_live_crypto_orders() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()

    added_column_names = [column.name for _table, column in fake_op.added_columns if _table == "live_crypto_orders"]
    assert added_column_names == ["limit_price", "time_in_force"]
    assert any(name == "ck_lco_limit_price_matches_order_type" for _kind, name, _cond in fake_op.created_constraints)


def test_upgrade_creates_attempt_table_with_stage_machine_constraints() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()

    assert [item[0] for item in fake_op.created_tables] == ["autonomous_limit_entry_attempts"]
    table = fake_op.created_tables[0]
    constraint_names = {getattr(item, "name", None) for item in table[1] if getattr(item, "name", None)}
    assert {
        "fk_alea_campaign_definition",
        "fk_alea_live_crypto_order",
        "fk_alea_replaces_attempt",
        "uq_alea_idempotency_key",
        "ck_alea_side_buy_only",
        "ck_alea_stage",
        "ck_alea_never_chase_above_max",
        "ck_alea_replacement_bounded",
        "ck_alea_filled_within_requested",
    }.issubset(constraint_names)

    index_names = {item[0] for item in fake_op.created_indexes}
    assert {"ix_alea_stage_next_attempt", "ix_alea_campaign_instrument", "uq_alea_active_campaign_instrument_scope"}.issubset(index_names)

    active_scope_index = next(item for item in fake_op.created_indexes if item[0] == "uq_alea_active_campaign_instrument_scope")
    assert active_scope_index[3] is True  # unique=True
    assert "postgresql_where" in active_scope_index[4]


def test_downgrade_drops_table_and_columns() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    module.downgrade()

    assert fake_op.dropped_tables == ["autonomous_limit_entry_attempts"]
    assert ("live_crypto_orders", "time_in_force") in fake_op.dropped_columns
    assert ("live_crypto_orders", "limit_price") in fake_op.dropped_columns
    assert ("ck_lco_limit_price_matches_order_type", "check") in fake_op.dropped_constraints


def test_stage_check_constraint_covers_every_documented_stage() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op
    module.upgrade()

    table = fake_op.created_tables[0]
    stage_constraint = next(
        item.sqltext.text for item in table[1] if getattr(item, "name", None) == "ck_alea_stage"
    )
    for stage in (
        "PROPOSED", "READY", "REJECTED", "SUBMITTED", "OPEN", "PARTIALLY_FILLED",
        "FILLED", "EXPIRED", "CANCEL_REQUESTED", "CANCELLED", "REPLACED",
        "RECONCILIATION_REQUIRED",
    ):
        assert f"'{stage}'" in stage_constraint


def test_identifier_lengths_fit_postgresql() -> None:
    identifiers = [
        "ck_lco_limit_price_matches_order_type",
        "fk_alea_campaign_definition",
        "fk_alea_live_crypto_order",
        "fk_alea_replaces_attempt",
        "uq_alea_idempotency_key",
        "ck_alea_side_buy_only",
        "ck_alea_stage",
        "ck_alea_preferred_limit_price_positive",
        "ck_alea_max_profitable_entry_price_positive",
        "ck_alea_never_chase_above_max",
        "ck_alea_requested_base_quantity_positive",
        "ck_alea_filled_within_requested",
        "ck_alea_replacement_bounded",
        "ck_alea_retry_count_non_negative",
        "ix_alea_stage_next_attempt",
        "ix_alea_campaign_instrument",
        "uq_alea_active_campaign_instrument_scope",
    ]
    assert all(len(name) <= 63 for name in identifiers)
