from __future__ import annotations

import ast
from pathlib import Path


_SERVICES_ROOT = Path(__file__).resolve().parents[2] / "app" / "services"
_TARGET_PATHS = [
    _SERVICES_ROOT / "research_activation.py",
    _SERVICES_ROOT / "research_agents",
    _SERVICES_ROOT / "research_campaign",
    _SERVICES_ROOT / "research_laboratory",
    _SERVICES_ROOT / "research_memory",
    _SERVICES_ROOT / "research_persistence",
    _SERVICES_ROOT / "evolution",
    _SERVICES_ROOT / "evolution_analytics",
    _SERVICES_ROOT / "orchestration" / "continuous_pipeline_worker.py",
]
_PROHIBITED_IMPORT_PREFIXES = (
    "app.services.live",
    "app.services.live_crypto_orders",
    "app.api.routes.live_crypto_orders",
    "app.services.exchange_connections.providers.coinbase_advanced",
)
_PROHIBITED_IDENTIFIERS = {
    "CoinbaseAdvancedClient",
    "record_live_approval_checkpoint",
    "orchestrate_live_execution",
    "live_crypto_order_submission_enabled",
    "create_order",
}


def _python_files() -> list[Path]:
    files: list[Path] = []
    for path in _TARGET_PATHS:
        if path.is_file():
            files.append(path)
            continue
        files.extend(sorted(path.rglob("*.py")))
    return files


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_research_and_evolution_modules_have_no_live_submission_dependency_path() -> None:
    violations: list[str] = []

    for file_path in _python_files():
        tree = ast.parse(file_path.read_text(), filename=str(file_path))
        imported_modules = _imported_modules(tree)
        identifiers = _identifiers(tree)

        for module_name in imported_modules:
            if module_name.startswith(_PROHIBITED_IMPORT_PREFIXES):
                violations.append(f"{file_path.relative_to(_SERVICES_ROOT.parent)} imports {module_name}")

        prohibited_names = sorted(_PROHIBITED_IDENTIFIERS.intersection(identifiers))
        if prohibited_names:
            violations.append(
                f"{file_path.relative_to(_SERVICES_ROOT.parent)} references prohibited live identifiers: {', '.join(prohibited_names)}"
            )

    assert not violations, "\n".join(violations)