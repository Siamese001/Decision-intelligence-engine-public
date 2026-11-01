#!/usr/bin/env python3
"""Decentralized Test Manifest Loader & SSOT CI Gate Resolver.

Enables feature branches and subagents to register test suites via isolated JSON
manifest files under tests/manifests/ without modifying monolithic tuples in
tools/run_ci_gates.py, eliminating 3-way git rebase merge conflicts.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_DIR = ROOT / "tests" / "manifests"
TEST_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_\.]+$")

logger = logging.getLogger("tools.test_manifest_loader")


@dataclass
class TestManifest:
    """Represents a decentralized test registration manifest."""

    name: str
    description: str = ""
    branch: str = ""
    deterministic_tests: list[str] = field(default_factory=list)
    e2e_tests: list[str] = field(default_factory=list)
    bge_tests: list[str] = field(default_factory=list)
    created_at: str = ""

    def total_tests(self) -> int:
        return len(self.deterministic_tests) + len(self.e2e_tests) + len(self.bge_tests)


def validate_manifest_payload(data: Any, source: str = "<memory>") -> tuple[bool, list[str]]:
    """Validates the schema and test module identifiers of a manifest payload."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return False, [f"Manifest in {source} must be a JSON object, got {type(data).__name__}"]

    name = data.get("name")
    if not name or not isinstance(name, str):
        errors.append(f"Manifest in {source} must declare a non-empty string 'name'")

    for key in ("deterministic_tests", "e2e_tests", "bge_tests"):
        val = data.get(key)
        if val is None:
            continue
        if not isinstance(val, list):
            errors.append(f"Field '{key}' in {source} must be a list of strings")
            continue
        for idx, item in enumerate(val):
            if not isinstance(item, str) or not item.strip():
                errors.append(f"Field '{key}[{idx}]' in {source} must be a non-empty string")
            elif not TEST_IDENTIFIER_RE.match(item.strip()):
                errors.append(f"Field '{key}[{idx}]' in {source} has invalid test identifier: '{item}'")

    return len(errors) == 0, errors


def load_manifest_file(path: Path, strict: bool = False) -> TestManifest | None:
    """Loads and validates a single JSON test manifest file."""
    if not path.is_file():
        if strict:
            raise FileNotFoundError(f"Manifest file does not exist: {path}")
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        msg = f"Failed to parse JSON in {path}: {exc}"
        if strict:
            raise ValueError(msg) from exc
        logger.warning(msg)
        return None

    valid, errors = validate_manifest_payload(data, source=str(path))
    if not valid:
        msg = f"Validation failed for manifest {path}: {'; '.join(errors)}"
        if strict:
            raise ValueError(msg)
        logger.warning(msg)
        return None

    return TestManifest(
        name=str(data["name"]),
        description=str(data.get("description", "")),
        branch=str(data.get("branch", "")),
        deterministic_tests=list(data.get("deterministic_tests") or []),
        e2e_tests=list(data.get("e2e_tests") or []),
        bge_tests=list(data.get("bge_tests") or []),
        created_at=str(data.get("created_at", "")),
    )


def discover_manifests(manifest_dir: Path | None = None, strict: bool = False) -> list[TestManifest]:
    """Scans manifest_dir for valid test manifests in deterministic order."""
    target_dir = manifest_dir or DEFAULT_MANIFEST_DIR
    if not target_dir.is_dir():
        return []

    manifests: list[TestManifest] = []
    for file_path in sorted(target_dir.glob("*.json")):
        # Skip hidden files or temporary files
        if file_path.name.startswith("."):
            continue
        manifest = load_manifest_file(file_path, strict=strict)
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def _deduplicate_preserve_order(items: Sequence[str]) -> tuple[str, ...]:
    """Preserves order while removing duplicates."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return tuple(result)


def resolve_all_tests(
    base_deterministic: Sequence[str] = (),
    base_e2e: Sequence[str] = (),
    base_bge: Sequence[str] = (),
    manifest_dir: Path | None = None,
    strict: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """
    Merges base CI test tuples with all decentralized manifests found in manifest_dir.
    Returns (all_deterministic_tests, all_e2e_tests, all_bge_tests).
    """
    manifests = discover_manifests(manifest_dir, strict=strict)

    combined_det = list(base_deterministic)
    combined_e2e = list(base_e2e)
    combined_bge = list(base_bge)

    for m in manifests:
        combined_det.extend(m.deterministic_tests)
        combined_e2e.extend(m.e2e_tests)
        combined_bge.extend(m.bge_tests)

    return (
        _deduplicate_preserve_order(combined_det),
        _deduplicate_preserve_order(combined_e2e),
        _deduplicate_preserve_order(combined_bge),
    )


def cmd_list(manifest_dir: Path) -> int:
    """CLI sub-command: list discovered test manifests and test counts."""
    manifests = discover_manifests(manifest_dir)
    print("=" * 80)
    print(f"  DECENTRALIZED TEST MANIFESTS IN: {manifest_dir}")
    print("=" * 80)
    if not manifests:
        print("  (No test manifests found)")
        print("=" * 80)
        return 0

    for m in manifests:
        print(f"📦 Manifest: {m.name}")
        if m.branch:
            print(f"   Branch:        {m.branch}")
        if m.description:
            print(f"   Description:   {m.description}")
        print(f"   Deterministic: {len(m.deterministic_tests)} tests")
        print(f"   E2E:           {len(m.e2e_tests)} tests")
        print(f"   BGE:           {len(m.bge_tests)} tests")
        print()

    print("=" * 80)
    return 0


def cmd_validate(manifest_dir: Path) -> int:
    """CLI sub-command: validate all test manifests in manifest_dir."""
    if not manifest_dir.is_dir():
        print(f"ℹ️ Manifest directory does not exist: {manifest_dir}")
        return 0

    has_errors = False
    files = sorted(manifest_dir.glob("*.json"))
    print(f"Validating {len(files)} manifest(s) in {manifest_dir}...")
    for f in files:
        try:
            load_manifest_file(f, strict=True)
            print(f"  ✅ {f.name}: Valid")
        except Exception as exc:
            print(f"  ❌ {f.name}: {exc}")
            has_errors = True

    return 1 if has_errors else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decentralized Test Manifest Loader")
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=DEFAULT_MANIFEST_DIR,
        help="Path to directory containing test manifest JSON files",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="List all registered test manifests")
    subparsers.add_parser("validate", help="Validate all manifests")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args.manifest_dir)
    return cmd_list(args.manifest_dir)


if __name__ == "__main__":
    import sys
    sys.exit(main())
