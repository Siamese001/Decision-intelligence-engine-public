#!/usr/bin/env python3
"""Static boundary between deterministic ingestion and its optional provider."""
from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_ROOTS = {
    "dual_pipeline.intake",
    "dual_pipeline.normalization",
    "dual_pipeline.identity",
    "dual_pipeline.index",
    "dual_pipeline.release",
    "dual_pipeline.preflight",
    "ingestion.pipeline",
}
FORBIDDEN_MODULE_PREFIXES = {
    "dual_pipeline.l2",
    "dual_pipeline.openai_reasoning",
    "dual_pipeline.ingestion_openai",
    "boto3",
    "botocore",
    "dual_pipeline.external_reasoning",
    "dual_pipeline.reasoning_provider",
    "openai",
    "anthropic",
    "google.generativeai",
}
FORBIDDEN_PROVIDER_SYMBOLS = {
    "OpenAIReasoningProvider",
    "ExternalReasoningProvider",
    "ExternalL2Provider",
    "GeminiReasoningProvider",
    "AnthropicReasoningProvider",
}
FORBIDDEN_PROVIDER_LITERALS = {
    "api.openai.com",
    "gpt-5-mini",
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
}


def _module_inventory() -> dict[str, Path]:
    inventory: dict[str, Path] = {}
    for package_dir in (ROOT / "dual_pipeline", ROOT / "ingestion"):
        for path in package_dir.rglob("*.py"):
            relative = path.relative_to(ROOT).with_suffix("")
            parts = list(relative.parts)
            if parts[-1] == "__init__":
                parts.pop()
            inventory[".".join(parts)] = path
    return inventory


def _is_forbidden_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_MODULE_PREFIXES
    )


def _direct_imports(module: str, path: Path) -> tuple[set[str], list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    imports: set[str] = set()
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                if _is_forbidden_module(alias.name):
                    violations.append(
                        f"{module}:{node.lineno} imports forbidden module {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative_name = "." * node.level + (node.module or "")
                imported_from = importlib.util.resolve_name(relative_name, package)
            else:
                imported_from = node.module or ""
            if imported_from:
                imports.add(imported_from)
                if _is_forbidden_module(imported_from):
                    violations.append(
                        f"{module}:{node.lineno} imports forbidden module {imported_from}"
                    )
            for alias in node.names:
                child_module = (
                    f"{imported_from}.{alias.name}"
                    if imported_from
                    else alias.name
                )
                imports.add(child_module)
                if _is_forbidden_module(child_module):
                    violations.append(
                        f"{module}:{node.lineno} imports forbidden module {child_module}"
                    )
                if alias.name in FORBIDDEN_PROVIDER_SYMBOLS:
                    violations.append(
                        f"{module}:{node.lineno} imports forbidden provider {alias.name}"
                    )
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_PROVIDER_SYMBOLS:
            violations.append(
                f"{module}:{node.lineno} references forbidden provider {node.id}"
            )
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_PROVIDER_SYMBOLS:
            violations.append(
                f"{module}:{node.lineno} references forbidden provider {node.attr}"
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if module == "dual_pipeline.model_registry":
                continue
            normalized_literal = node.value.casefold()
            if (
                node.value in FORBIDDEN_PROVIDER_SYMBOLS
                or any(
                    forbidden in normalized_literal
                    for forbidden in FORBIDDEN_PROVIDER_LITERALS
                )
            ):
                violations.append(
                    f"{module}:{node.lineno} dynamically references forbidden L2 dependency"
                )

    return imports, violations


def _protected_closure() -> tuple[set[str], list[str]]:
    inventory = _module_inventory()
    missing = sorted(PROTECTED_ROOTS - set(inventory))
    if missing:
        raise AssertionError(f"protected ingestion modules are missing: {missing}")

    closure: set[str] = set()
    violations: list[str] = []
    pending = list(PROTECTED_ROOTS)
    while pending:
        module = pending.pop()
        if module in closure:
            continue
        closure.add(module)
        imports, direct_violations = _direct_imports(module, inventory[module])
        violations.extend(direct_violations)
        pending.extend(
            dependency
            for dependency in imports
            if dependency in inventory and dependency not in closure
        )
    return closure, sorted(set(violations))


class IngestionExternalL2BoundaryTests(unittest.TestCase):
    def test_core_ingestion_closure_excludes_external_provider_modules(self) -> None:
        closure, violations = _protected_closure()
        self.assertFalse(violations, "\n".join(violations))
        self.assertFalse(
            sorted(module for module in closure if _is_forbidden_module(module))
        )

    def test_optional_provider_is_outside_core_ingestion_closure(self) -> None:
        closure, _ = _protected_closure()
        self.assertNotIn("dual_pipeline.ingestion_openai", closure)
        self.assertNotIn("dual_pipeline.l2", closure)

    def test_boundary_covers_shared_ingestion_runtime_modules(self) -> None:
        closure, _ = _protected_closure()
        self.assertTrue(
            {
                "dual_pipeline.authorization",
                "dual_pipeline.contracts",
                "dual_pipeline.core",
                "dual_pipeline.telemetry",
            }.issubset(closure)
        )


if __name__ == "__main__":
    unittest.main()
