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
    / "20260804_0066_add_limit_entry_claim_lineage.py"
)


class _FakeOp:
    def __init__(self) -> None:
        self.added_columns: list[tuple[str, object]] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.created_foreign_keys: list[tuple[str, str, str, tuple, tuple]] = []
        self.dropped_constraints: list[tuple[str, str, str]] = []
        self.created_unique_constraints: list[tuple[str, str, tuple]] = []

    def add_column(self, table_name: str, column: object) -> None:
        self.added_columns.append((table_name, column))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped_columns.append((table_name, column_name))

    def create_foreign_key(self, name, source_table, referent_table, local_cols, remote_cols, **kwargs) -> None:
        self.created_foreign_keys.append((name, source_table, referent_table, tuple(local_cols), tuple(remote_cols)))

    def create_unique_constraint(self, name, table_name, columns, **kwargs) -> None:
        self.created_unique_constraints.append((name, table_name, tuple(columns)))

    def drop_constraint(self, name: str, table_name: str, type_: str) -> None:
        self.dropped_constraints.append((name, table_name, type_))


def _load_module():
    if "alembic" not in sys.modules:
        sys.modules["alembic"] = types.SimpleNamespace(op=types.SimpleNamespace())

    spec = importlib.util.spec_from_file_location("migration_20260804_0066", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain() -> None:
    module = _load_module()
    assert module.revision == "20260804_0066"
    assert module.down_revision == "20260803_0065"


def test_upgrade_adds_lineage_columns_and_constraints() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()

    added_names = [column.name for _table, column in fake_op.added_columns]
    assert added_names == ["paper_account_id", "package_id", "activation_id", "claim_id", "custody_id"]
    assert all(table == "autonomous_limit_entry_attempts" for table, _column in fake_op.added_columns)

    fk_names = {item[0] for item in fake_op.created_foreign_keys}
    assert {"fk_alea_package", "fk_alea_activation", "fk_alea_claim", "fk_alea_custody"}.issubset(fk_names)

    unique_names = {item[0] for item in fake_op.created_unique_constraints}
    assert {"uq_alea_claim_id", "uq_alea_package_id"}.issubset(unique_names)


def test_downgrade_drops_everything_upgrade_added() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    module.downgrade()

    dropped_columns = {name for _table, name in fake_op.dropped_columns}
    assert {"custody_id", "claim_id", "activation_id", "package_id", "paper_account_id"} == dropped_columns
    dropped_constraint_names = {item[0] for item in fake_op.dropped_constraints}
    assert {"uq_alea_package_id", "uq_alea_claim_id", "fk_alea_custody", "fk_alea_claim", "fk_alea_activation", "fk_alea_package"}.issubset(dropped_constraint_names)


def test_migration_recreates_after_downgrade() -> None:
    module = _load_module()
    fake_op = _FakeOp()
    module.op = fake_op

    module.upgrade()
    module.downgrade()
    module.upgrade()

    assert len(fake_op.added_columns) == 10  # 5 columns x 2 upgrade passes


def test_identifier_lengths_fit_postgresql() -> None:
    identifiers = [
        "fk_alea_package", "fk_alea_activation", "fk_alea_claim", "fk_alea_custody",
        "uq_alea_claim_id", "uq_alea_package_id",
    ]
    assert all(len(name) <= 63 for name in identifiers)
