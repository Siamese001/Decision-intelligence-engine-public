"""CLI command builder, output formatter, and entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .claims import evaluate_claim
from .constants import CLAIM_KINDS
from .controls import validate_control_files
from .discovery import discover_inventory
from .primitives import _resolved


def _write_output(value: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if output is None:
        print(rendered, end="")
        return
    output = _resolved(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory runtime evidence and enforce scoped runtime/retrieval claims."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser(
        "inventory",
        help="Discover all reachable active pairs, query evidence, and retrieval evaluations.",
    )
    inventory.add_argument("--repository-root", type=Path, default=Path.cwd())
    inventory.add_argument("--extra-root", type=Path, action="append", default=[])
    inventory.add_argument("--scope", choices=["main", "all"], default="all")
    inventory.add_argument("--no-cache", action="store_true", default=False)
    inventory.add_argument("--output", type=Path)

    claim = subparsers.add_parser(
        "claim", help="Evaluate one evidence-bound runtime or retrieval claim."
    )
    claim.add_argument("--repository-root", type=Path, default=Path.cwd())
    claim.add_argument("--extra-root", type=Path, action="append", default=[])
    claim.add_argument("--kind", choices=CLAIM_KINDS, required=True)
    claim.add_argument("--backend", required=True)
    claim.add_argument("--scope-root", type=Path, action="append", default=[])
    claim.add_argument("--complete-scope", action="store_true")
    claim.add_argument("--output", type=Path)

    controls = subparsers.add_parser(
        "enforce-controls",
        help="Fail if AGENTS.md, hook, or documentation enforcement is missing.",
    )
    controls.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "enforce-controls":
        errors = validate_control_files(args.repository_root)
        if errors:
            for error in errors:
                print(f"runtime-evidence control failure: {error}")
            return 1
        print("runtime-evidence controls: PASS")
        return 0

    inventory = discover_inventory(
        args.repository_root,
        extra_roots=args.extra_root,
        scope=getattr(args, "scope", "main"),
        use_cache=not getattr(args, "no_cache", False),
    )
    if args.command == "inventory":
        _write_output(inventory, args.output)
        return 0

    result = evaluate_claim(
        inventory,
        kind=args.kind,
        backend=args.backend,
        scope_roots=args.scope_root,
        complete_scope=args.complete_scope,
    )
    _write_output(result, args.output)
    return 0 if result["supported"] else 2
