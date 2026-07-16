from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import sqlalchemy as sa


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260715_0040_add_authoritative_canonical_package_activation.py"
)


class _FakeOp:
    def __init__(self) -> None:
        self.created_tables: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.dropped_tables: list[str] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...], bool]] = []
        self.dropped_indexes: list[tuple[str, str]] = []
        self.created_constraints: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str]] = []

    def create_table(self, table_name: str, *args, **kwargs) -> None:
        self.created_tables.append((table_name, args, kwargs))

    def drop_table(self, table_name: str) -> None:
        self.dropped_tables.append(table_name)

    def create_index(self, name: str, table_name: str, columns: list[str], unique: bool = False, **kwargs) -> None:
        _ = kwargs
        self.created_indexes.append((name, table_name, tuple(columns), unique))

    def drop_index(self, name: str, table_name: str) -> None:
        self.dropped_indexes.append((name, table_name))

    def create_check_constraint(self, name: str, table_name: str, condition: str) -> None:
        self.created_constraints.append(("check", name, condition))

    def drop_constraint(self, name: str, table_name: str, type_: str) -> None:
        self.dropped_constraints.append((name, type_))



def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260715_0040", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260715_0040"
    assert module.down_revision == "20260715_0039"



def test_upgrade_and_downgrade_compile_shape() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    assert [item[0] for item in fake_op.created_tables] == ["canonical_preview_packages", "canonical_proving_activations"]
    assert any(name == "ix_cpp_idempotency" for name, _, _, _ in fake_op.created_indexes)
    assert any(name == "uq_cpa_active_scope" for name, _, _, _ in fake_op.created_indexes)
    assert any(name == "ck_live_approval_events_checkpoint_type" for name, _ in fake_op.dropped_constraints)

    cpp_table = fake_op.created_tables[0]
    cpa_table = fake_op.created_tables[1]
    cpp_constraints = {getattr(item, "name", None) for item in cpp_table[1] if getattr(item, "name", None)}
    cpa_constraints = {getattr(item, "name", None) for item in cpa_table[1] if getattr(item, "name", None)}
    assert {"uq_cpp_package_id", "uq_cpp_idempotency_key", "uq_cpp_preview_id", "uq_cpp_decision_id", "uq_cpp_risk_event_id"}.issubset(cpp_constraints)
    assert {"uq_cpa_activation_id", "uq_cpa_package_id", "uq_cpa_dry_run_order", "ck_cpa_state"}.issubset(cpa_constraints)

    module.downgrade()
    assert fake_op.dropped_tables == ["canonical_proving_activations", "canonical_preview_packages"]



def test_migration_recreates_after_downgrade() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    module.downgrade()
    module.upgrade()

    assert len(fake_op.created_tables) == 4
    assert fake_op.created_tables[0][0] == "canonical_preview_packages"
    assert fake_op.created_tables[2][0] == "canonical_preview_packages"



def test_identifier_lengths_fit_postgresql() -> None:
    identifiers = [
        "uq_cpp_package_id",
        "uq_cpp_idempotency_key",
        "uq_cpp_preview_id",
        "uq_cpp_decision_id",
        "uq_cpp_risk_event_id",
        "uq_cpp_campaign_owner",
        "ck_cpp_package_state",
        "uq_cpa_activation_id",
        "uq_cpa_package_id",
        "uq_cpa_dry_run_order",
        "ck_cpa_state",
        "uq_cpa_active_scope",
        "ck_live_approval_events_checkpoint_type",
    ]
    assert all(len(name) <= 63 for name in identifiers)
