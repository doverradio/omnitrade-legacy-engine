from __future__ import annotations

import ast
from pathlib import Path


_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_ALLOWED_CREATE_ORDER_CALLERS = {
    Path("services/live_crypto_orders.py"),
}
_ALLOWED_COINBASE_IMPORTERS = {
    Path("services/exchange_connections/providers/registry.py"),
    Path("services/exchange_connections/providers/coinbase_advanced.py"),
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


def _called_symbol_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
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


def test_generic_services_do_not_import_coinbase_client_directly() -> None:
    violations: list[str] = []

    for file_path in _python_files():
        tree = ast.parse(file_path.read_text(), filename=str(file_path))
        relative = file_path.relative_to(_APP_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.services.exchange_connections.providers.coinbase_advanced":
                if relative not in _ALLOWED_COINBASE_IMPORTERS:
                    violations.append(str(relative))

    assert not violations, "Direct Coinbase imports must stay inside provider adapter/registry: " + ", ".join(sorted(set(violations)))


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


def test_reconciliation_and_ledger_do_not_execute_profit_policies_or_move_capital() -> None:
    reconciliation_file = _APP_ROOT / "services" / "live" / "accounting_reconciliation.py"
    ledger_file = _APP_ROOT / "services" / "capital_ledger" / "service.py"

    forbidden = {
        "approve_profit_cycle",
        "reject_profit_cycle",
        "evaluate_profit_cycle",
        "upsert_profit_policy",
        "withdraw",
        "transfer",
        "compound",
        "execute_policy",
    }

    violations: list[str] = []
    for label, file_path in (("reconciliation", reconciliation_file), ("capital_ledger", ledger_file)):
        tree = ast.parse(file_path.read_text(), filename=str(file_path))
        calls = {
            name
            for name in (_called_symbol_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call))
            if name is not None
        }

        # Exact policy APIs are forbidden; broad capital-movement names are prefix checked.
        exact_hits = calls.intersection({
            "approve_profit_cycle",
            "reject_profit_cycle",
            "evaluate_profit_cycle",
            "upsert_profit_policy",
            "execute_policy",
        })
        prefix_hits = {name for name in calls if name.startswith(("withdraw", "transfer", "compound"))}
        for name in sorted(exact_hits.union(prefix_hits).intersection(forbidden)):
            violations.append(f"{label}:{name}")

    assert not violations, "Autonomy boundary violation(s): " + ", ".join(violations)


def test_reconciliation_module_has_no_profit_policy_service_imports() -> None:
    reconciliation_file = _APP_ROOT / "services" / "live" / "accounting_reconciliation.py"
    tree = ast.parse(reconciliation_file.read_text(), filename=str(reconciliation_file))

    disallowed_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "capital_campaign_profit" in node.module:
            disallowed_imports.append(node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "capital_campaign_profit" in alias.name:
                    disallowed_imports.append(alias.name)

    assert not disallowed_imports, "Reconciliation must not depend on profit-policy services: " + ", ".join(disallowed_imports)


def test_dry_run_path_does_not_call_submission_or_capital_movement_symbols() -> None:
    live_order_file = _APP_ROOT / "services" / "live_crypto_orders.py"
    tree = ast.parse(live_order_file.read_text(), filename=str(live_order_file))

    dry_run_node: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "dry_run":
            dry_run_node = node
            break

    assert dry_run_node is not None, "live crypto dry_run function must exist"

    called_names = {
        name
        for name in (_called_symbol_name(node) for node in ast.walk(dry_run_node) if isinstance(node, ast.Call))
        if name is not None
    }

    forbidden = {
        "create_order",
        "reconcile",
        "reconcile_live_order_and_fills",
        "approve_profit_cycle",
        "reject_profit_cycle",
        "evaluate_profit_cycle",
        "upsert_profit_policy",
        "withdraw",
        "transfer",
        "compound",
        "execute_policy",
    }
    exact_hits = called_names.intersection(forbidden)
    prefix_hits = {name for name in called_names if name.startswith(("withdraw", "transfer", "compound"))}
    violations = sorted(exact_hits.union(prefix_hits))
    assert not violations, "Dry-run autonomy boundary violation(s): " + ", ".join(violations)


def test_provider_registry_cannot_enable_submission_flag() -> None:
    registry_file = _APP_ROOT / "services" / "exchange_connections" / "providers" / "registry.py"
    tree = ast.parse(registry_file.read_text(), filename=str(registry_file))

    forbidden_targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "live_crypto_order_submission_enabled":
                    forbidden_targets.append("attribute_assign")
                if isinstance(target, ast.Subscript):
                    forbidden_targets.append("subscript_assign")

    assert not forbidden_targets, "Provider registry must not mutate submission flags"


def test_mission_control_and_venue_intelligence_cannot_call_create_order() -> None:
    mission_control_file = _APP_ROOT / "services" / "mission_control_intelligence.py"
    venue_doc_file = _APP_ROOT.parents[2] / "docs" / "VENUE_INTELLIGENCE_ENGINE.md"

    mission_tree = ast.parse(mission_control_file.read_text(), filename=str(mission_control_file))
    mission_calls = [node for node in ast.walk(mission_tree) if isinstance(node, ast.Call) and _is_create_order_call(node)]
    assert not mission_calls, "Mission Control service must not call create_order"

    venue_text = venue_doc_file.read_text().lower()
    assert "create_order" not in venue_text, "Venue Intelligence docs must not define create_order execution paths"


def test_safe_vps_entrypoint_cannot_call_create_order_or_mutate_environment_flags() -> None:
    script_file = _APP_ROOT.parent / "scripts" / "run_live_crypto_dry_run.py"
    tree = ast.parse(script_file.read_text(), filename=str(script_file))

    called_names = {
        name
        for name in (_called_symbol_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call))
        if name is not None
    }
    assert "create_order" not in called_names, "Safe VPS script must not call create_order"

    env_mutations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"setenv", "putenv"}:
                env_mutations.append(node.func.attr)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute):
                    if isinstance(target.value.value, ast.Name) and target.value.value.id == "os" and target.value.attr == "environ":
                        env_mutations.append("os.environ assignment")

    assert not env_mutations, "Safe VPS script must not mutate environment flags: " + ", ".join(env_mutations)


def test_dry_run_evidence_review_helper_cannot_call_create_order_or_mutate_environment_flags() -> None:
    script_file = _APP_ROOT.parent / "scripts" / "review_live_crypto_dry_run_evidence.py"
    tree = ast.parse(script_file.read_text(), filename=str(script_file))

    called_names = {
        name
        for name in (_called_symbol_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call))
        if name is not None
    }
    assert "create_order" not in called_names, "Dry-run evidence review helper must not call create_order"

    env_mutations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"setenv", "putenv"}:
                env_mutations.append(node.func.attr)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute):
                    if isinstance(target.value.value, ast.Name) and target.value.value.id == "os" and target.value.attr == "environ":
                        env_mutations.append("os.environ assignment")

    assert not env_mutations, "Dry-run evidence review helper must not mutate environment flags: " + ", ".join(env_mutations)


def test_initializer_entrypoint_cannot_call_create_order_or_mutate_environment_flags() -> None:
    script_file = _APP_ROOT.parent / "scripts" / "initialize_live_crypto_environment.py"
    tree = ast.parse(script_file.read_text(), filename=str(script_file))

    called_names = {
        name
        for name in (_called_symbol_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call))
        if name is not None
    }
    assert "create_order" not in called_names, "Initializer script must not call create_order"

    env_mutations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"setenv", "putenv"}:
                env_mutations.append(node.func.attr)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute):
                    if isinstance(target.value.value, ast.Name) and target.value.value.id == "os" and target.value.attr == "environ":
                        env_mutations.append("os.environ assignment")

    assert not env_mutations, "Initializer script must not mutate environment flags: " + ", ".join(env_mutations)
