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


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


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


def test_live_create_order_boundary_has_no_retry_wrappers_and_reconciliation_has_no_create_order() -> None:
    live_order_file = _APP_ROOT / "services" / "live_crypto_orders.py"
    reconciliation_file = _APP_ROOT / "services" / "live" / "accounting_reconciliation.py"
    provider_file = _APP_ROOT / "services" / "exchange_connections" / "providers" / "coinbase_advanced.py"

    live_order_tree = ast.parse(live_order_file.read_text(), filename=str(live_order_file))
    provider_tree = ast.parse(provider_file.read_text(), filename=str(provider_file))
    reconciliation_tree = ast.parse(reconciliation_file.read_text(), filename=str(reconciliation_file))

    retry_like = {"retry", "retrying", "tenacity"}
    boundary_decorators: list[str] = []

    for tree in (live_order_tree, provider_tree):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in {"submit", "create_order"}:
                continue
            names = [name for name in (_decorator_name(item) for item in node.decorator_list) if name is not None]
            if any(name in retry_like for name in names):
                boundary_decorators.append(f"{node.name}:{','.join(names)}")

    reconciliation_calls = [node for node in ast.walk(reconciliation_tree) if isinstance(node, ast.Call) and _is_create_order_call(node)]

    assert not boundary_decorators, "Retry decorators are not allowed on live create-order boundary: " + ", ".join(boundary_decorators)
    assert not reconciliation_calls, "Reconciliation code must not call create_order"
