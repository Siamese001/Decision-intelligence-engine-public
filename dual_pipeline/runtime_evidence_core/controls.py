"""Repository control file inspection and invariant enforcement."""

from __future__ import annotations

from pathlib import Path

from .constants import (
    AUTOMATIC_EVAL_GATE_BINDINGS,
    CONTROL_BEGIN,
    CONTROL_END,
    CONTROL_FILES,
    DEPRECATION_GATE_BINDINGS,
    HOOK_COMMAND,
    REQUIRED_CONTROL_PHRASES,
    RETRIEVAL_RELEASE_GATE_BINDINGS,
)
from .primitives import _resolved


def _python_file_contains_symbol(path: Path, symbol: str) -> bool:
    """Verify that an identifier or constant exists in Python AST (ignoring comments)."""
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == symbol:
            return True
        if isinstance(node, ast.Attribute) and node.attr == symbol:
            return True
        if isinstance(node, ast.FunctionDef) and node.name == symbol:
            return True
        if isinstance(node, ast.AsyncFunctionDef) and node.name == symbol:
            return True
        if isinstance(node, ast.alias) and (node.name == symbol or node.asname == symbol):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and symbol == node.value:
            return True
    return False


def validate_control_files(repository_root: Path) -> list[str]:  # noqa: C901
    """Ensure agent controls and hooks retain the runtime-evidence guard."""

    repository_root = _resolved(repository_root)
    errors: list[str] = []
    agents_path = repository_root / "AGENTS.md"
    try:
        agents = agents_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"AGENTS.md is unavailable: {exc}"]
    if CONTROL_BEGIN not in agents or CONTROL_END not in agents:
        errors.append("AGENTS.md is missing the runtime-evidence guard sentinels")
    for phrase in REQUIRED_CONTROL_PHRASES:
        if phrase not in agents:
            errors.append(f"AGENTS.md is missing required runtime-evidence control: {phrase}")
    for relative in CONTROL_FILES:
        path = repository_root / relative
        if not path.is_file():
            errors.append(f"required runtime-evidence control file is missing: {relative}")
    for relative in (".githooks/pre-commit", ".githooks/pre-push"):
        path = repository_root / relative
        if path.is_file() and HOOK_COMMAND not in path.read_text(encoding="utf-8"):
            errors.append(f"{relative} does not invoke the runtime-evidence control")
    pre_push = repository_root / ".githooks/pre-push"
    if pre_push.is_file() and "git lfs pre-push" not in pre_push.read_text(encoding="utf-8"):
        errors.append(".githooks/pre-push does not preserve Git LFS forwarding")
    for relative, binding in RETRIEVAL_RELEASE_GATE_BINDINGS.items():
        path = repository_root / relative
        if not path.is_file():
            errors.append(f"{relative} is missing retrieval release gate binding: {binding}")
        elif relative.endswith(".py"):
            if not _python_file_contains_symbol(path, binding):
                errors.append(
                    f"{relative} is missing executable retrieval release gate AST binding: {binding}"
                )
        elif binding not in path.read_text(encoding="utf-8"):
            errors.append(f"{relative} is missing retrieval release gate binding: {binding}")
    for relative, bindings in AUTOMATIC_EVAL_GATE_BINDINGS.items():
        path = repository_root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        for binding in bindings:
            if binding not in text:
                errors.append(f"{relative} is missing automatic evaluation gate binding: {binding}")
    for relative, bindings in DEPRECATION_GATE_BINDINGS.items():
        path = repository_root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        for binding in bindings:
            if binding not in text:
                errors.append(f"{relative} is missing retired-embedding gate binding: {binding}")
    agents_dir = repository_root / ".agents"
    if agents_dir.is_dir():
        for md_path in sorted(agents_dir.rglob("*.md")):
            try:
                if "SSOT_REF_" in md_path.read_text(encoding="utf-8"):
                    errors.append(
                        f"corrupted SSOT_REF_ placeholder found in {md_path.relative_to(repository_root)}"
                    )
            except OSError:
                pass
    return errors
