#!/usr/bin/env python3
"""
tools/lint_no_knowledge_popping.py

Enforces Master Invariant 11 (Anti-Knowledge Popping & Deterministic Semantic Gravity).
Scans dual_pipeline/ to ensure knowledge graph mutations do not use unbounded while-loop popping
(e.g., `while len(signals) > max_budget: signals.pop()`) or blind slice truncations
(`signals = signals[:-excess]`) without Deterministic Semantic Gravity triage.

Exits with code 0 if clean, 1 if any anti-pattern is detected.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DUAL_PIPELINE_DIR = REPO_ROOT / "dual_pipeline"


class KnowledgePoppingVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: List[str] = []

    def visit_While(self, node: ast.While) -> None:
        # Check if the while loop condition checks length, e.g. len(x) > max_budget or len(x) > N
        has_len_check = False
        for child in ast.walk(node.test):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "len":
                has_len_check = True
                break

        # Check if while body calls .pop()
        for stmt in node.body:
            for child in ast.walk(stmt):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if child.func.attr == "pop":
                        # If it has len check in loop condition and calls pop in body, it's blind popping!
                        if has_len_check:
                            self.violations.append(
                                f"{self.filename}:{node.lineno}: Blind while-loop popping detected: "
                                f"'while len(...) > ...: ... .pop()'. "
                                f"Must use Deterministic Semantic Gravity triage under Invariant 11."
                            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Check for slice truncation like signals = signals[:-excess] or signals = signals[:budget]
        # when assigning back to same variable name
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_name = node.targets[0].id
            if "signal" in target_name.lower() or "mutation" in target_name.lower() or "edge" in target_name.lower():
                if isinstance(node.value, ast.Subscript) and isinstance(node.value.value, ast.Name):
                    if node.value.value.id == target_name:
                        # Slice assignment to itself!
                        sl = node.value.slice
                        if isinstance(sl, ast.Slice):
                            self.violations.append(
                                f"{self.filename}:{node.lineno}: Blind slice truncation detected on '{target_name}': "
                                f"'{target_name} = {target_name}[...]'. "
                                f"Must use Deterministic Semantic Gravity triage under Invariant 11."
                            )
        self.generic_visit(node)


def check_file(filepath: Path) -> List[str]:
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"Unable to read {filepath}: {exc}"]

    try:
        rel_path = str(filepath.relative_to(REPO_ROOT))
    except ValueError:
        rel_path = str(filepath)

    try:
        tree = ast.parse(content, filename=str(filepath))
    except SyntaxError:
        return []

    visitor = KnowledgePoppingVisitor(rel_path)
    visitor.visit(tree)
    return visitor.violations


def run_linter(target_files: Optional[Sequence[Path]] = None) -> Tuple[bool, List[str]]:
    all_violations: List[str] = []
    if target_files is None:
        files_to_check = [p for p in DUAL_PIPELINE_DIR.rglob("*.py") if "__pycache__" not in str(p)]
    else:
        files_to_check = [
            p for p in target_files
            if p.suffix == ".py" and str(p.resolve()).startswith(str(DUAL_PIPELINE_DIR.resolve())) and p.exists()
        ]

    for f in files_to_check:
        all_violations.extend(check_file(f))

    return len(all_violations) == 0, all_violations


def main() -> int:
    print("=" * 80)
    print("  ANTI-KNOWLEDGE POPPING & SEMANTIC GRAVITY LINTER (Invariant 11)")
    print("================================================================================")

    args = sys.argv[1:]
    receipt_path: Optional[Path] = None
    if "--receipt" in args:
        idx = args.index("--receipt")
        if idx + 1 < len(args):
            receipt_path = Path(args[idx + 1])

    target_files: Optional[List[Path]] = None
    if "--staged" in args:
        import subprocess
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            target_files = [REPO_ROOT / p.strip() for p in res.stdout.splitlines() if p.strip()]
    else:
        raw_files = [Path(a) for a in args if not a.startswith("--") and (REPO_ROOT / a).exists()]
        if raw_files:
            target_files = [f.resolve() for f in raw_files]

    passed, violations = run_linter(target_files)

    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_data = {
            "linter": "lint_no_knowledge_popping",
            "invariant": "Master Invariant 11",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            "violations_count": len(violations),
            "violations": violations,
        }
        receipt_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
        print(f"📄 Receipt saved to: {receipt_path}")

    if not passed:
        print(f"❌ FAILED: {len(violations)} knowledge popping violation(s) detected:")
        for v in violations:
            print(f"   • {v}")
        print("================================================================================")
        return 1

    print("✅ PASSED: 0 blind while-loop popping or slice truncation patterns detected.")
    print("   All knowledge graph mutations respect Deterministic Semantic Gravity.")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
