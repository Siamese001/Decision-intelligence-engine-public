"""
dual_pipeline/cli/golden_label_handlers.py

CLI subcommand handlers for `nee golden-label`.
Enforces offline labeling of past meeting moments into local gitignored storage
and governed export to sanitized committed golden datasets.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from tools.build_copilot_golden import (
    TOTAL_GOLDEN_CASES,
    export_golden_cases,
    load_golden_cases,
    validate_golden_cases,
)

logger = logging.getLogger("dual_pipeline.cli.golden_label_handlers")


def _load_local_store(store_path: Path) -> List[Dict[str, Any]]:
    if not store_path.exists():
        return []
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Could not read local label store %s: %s", store_path, exc)
        return []


def _save_local_store(store_path: Path, moments: List[Dict[str, Any]]) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(moments, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def handle_golden_label(args: argparse.Namespace) -> Dict[str, Any]:
    """Handles `nee golden-label` commands."""
    action = getattr(args, "action", "list")
    store_path = getattr(args, "local_store", Path(".local/copilot_labels/raw_moments.json"))
    golden_path = getattr(args, "output", Path("tests/evals/copilot_golden/v1/golden_cases.jsonl"))

    if action == "add":
        cid = getattr(args, "id", None)
        strata = getattr(args, "strata", None)
        prompt = getattr(args, "prompt", None)
        if not cid or not strata or not prompt:
            raise ValueError("--id, --strata, and --prompt are required for action 'add'")

        moments = _load_local_store(store_path)
        existing = next((m for m in moments if m.get("id") == cid), None)
        moment_data = {
            "id": cid,
            "strata": strata,
            "prompt": prompt,
            "expected_action": getattr(args, "expected_action", "speak"),
            "expected_assertion_ids": getattr(args, "assertion_ids", []),
            "ideal_point": getattr(args, "ideal_point", ""),
            "score": int(getattr(args, "score", 5)),
        }
        if existing:
            existing.update(moment_data)
        else:
            moments.append(moment_data)
        _save_local_store(store_path, moments)
        print(f"Recorded moment '{cid}' to {store_path} (total: {len(moments)})")
        return {"status": "PASS", "action": "add", "id": cid, "total_local": len(moments)}

    elif action == "list":
        moments = _load_local_store(store_path)
        print(f"Local Store ({store_path}): {len(moments)} items")
        for m in moments:
            print(f"  [{m.get('strata')}] {m.get('id')}: {m.get('prompt')[:60]}... -> {m.get('expected_action')}")
        return {"status": "PASS", "action": "list", "count": len(moments)}

    elif action == "validate":
        target = golden_path if golden_path.exists() else store_path
        cases = load_golden_cases(target) if target.suffix == ".jsonl" else _load_local_store(target)
        valid, errors = validate_golden_cases(cases)
        status = "PASS" if valid else "FAIL"
        print(f"Validation for {target}: {status} ({len(cases)}/{TOTAL_GOLDEN_CASES} cases)")
        if errors:
            for err in errors:
                print(f"  - {err}")
        return {"status": status, "cases_count": len(cases), "errors": errors}

    elif action == "stats":
        cases = load_golden_cases(golden_path) if golden_path.exists() else []
        counts = Counter(c.get("strata") for c in cases)
        print(f"Golden dataset ({golden_path}): {len(cases)} cases")
        for st, cnt in sorted(counts.items()):
            print(f"  {st}: {cnt}")
        return {"status": "PASS", "total": len(cases), "strata_counts": dict(counts)}

    elif action == "export":
        moments = _load_local_store(store_path)
        if not moments:
            from tools.build_copilot_golden import get_default_golden_cases
            moments = get_default_golden_cases()
        count = export_golden_cases(moments, golden_path)
        print(f"Exported {count} sanitized golden cases to {golden_path}")
    raise ValueError(f"Unknown golden-label action: {action}")


def add_golden_label_parser(subparsers: argparse._SubParsersAction) -> None:
    """Registers golden-label CLI command parser."""
    command = subparsers.add_parser(
        "golden-label",
        help="Label and manage golden evaluation cases for Copilot signal quality",
    )
    command.add_argument("--action", choices=["add", "list", "export", "validate", "stats"], default="list", help="Labeling action")
    command.add_argument("--id", help="Case ID")
    command.add_argument("--strata", choices=["grounded", "abstain", "ownership_traps", "commitment", "c19", "cross_project"], help="Target strata")
    command.add_argument("--prompt", help="Natural language prompt or query")
    command.add_argument("--expected-action", choices=["speak", "silent", "abstain", "trap"], default="speak", help="Expected copilot action")
    command.add_argument("--ideal-point", help="Ideal talking point or rationale")
    command.add_argument("--score", type=int, choices=[1, 2, 3, 4, 5], default=5, help="Quality / relevance score (1-5)")
    command.add_argument("--assertion-ids", nargs="*", default=[], help="Expected assertion IDs")
    command.add_argument("--local-store", type=Path, default=Path(".local/copilot_labels/raw_moments.json"), help="Path to local raw moments store")
    command.add_argument("--output", type=Path, default=Path("tests/evals/copilot_golden/v1/golden_cases.jsonl"), help="Path to export sanitized golden cases")
    from dual_pipeline.cli.parser import _add_runtime
    _add_runtime(command)
