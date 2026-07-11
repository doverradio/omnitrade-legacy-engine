from __future__ import annotations

import ast
from pathlib import Path


_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_ALLOWED_CREATE_ORDER_CALLERS = {
    Path("services/live_crypto_orders.py"),
}


def _python_files() -> list[Path]:
    return sorted(_APP_ROOT.rglob("*.py"))


def _is_create_order_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "create_order"


def test_coinbase_create_order_has_single_sanctioned_application_boundary() -> None:
    violations: list[str] = []

    for file_path in _python_files():
        if file_path.name == "coinbase_advanced.py":
            continue

        tree = ast.parse(file_path.read_text(), filename=str(file_path))
        create_order_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_create_order_call(node)]
        if not create_order_calls:
            continue

        relative = file_path.relative_to(_APP_ROOT)
        if relative not in _ALLOWED_CREATE_ORDER_CALLERS:
            violations.append(str(relative))

    assert not violations, "Unexpected live order provider callers: " + ", ".join(sorted(violations))
