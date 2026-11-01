#!/usr/bin/env python3
"""
tools/lint_anti_hardcoding.py

AST Anti-Hardcoding Linter (Zero-Hardcoding Governance Invariant PD-10).
Scans production pipeline modules to ensure no personal names, static dates,
or graph-wiping assignments are hardcoded into procedural logic.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

EXCLUDED_FILENAMES = {
    "offline_synthesizer.py",
    "offline_graph_generator.py",
    "anti_conflation.py",
}

BANNED_NAME_PATTERNS = [
    "Drew Oakley",
    "reed",
    "jen pan",
]

SUSPICIOUS_STATIC_DATES = [
    "09/24/2026",
    "12/23/2026",
]

class AntiHardcodingVisitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.violations: List[Dict[str, Any]] = []

    def visit_Assign(self, node: ast.Assign):
        # Detect mutations["decision_updates"] = []
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                if isinstance(target.slice, ast.Constant) and target.slice.value == "decision_updates":
                    if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
                        # Check if parent or context is an unconditional wipe
                        self.violations.append({
                            "file": self.filename,
                            "line": node.lineno,
                            "type": "DECISION_WIPE_VIOLATION",
                            "message": "Unconditional erasure of decision_updates (mutations['decision_updates'] = []) is prohibited."
                        })
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            val_lower = node.value.lower()
            # Check suspicious static dates
            for d in SUSPICIOUS_STATIC_DATES:
                if d in node.value:
                    self.violations.append({
                        "file": self.filename,
                        "line": node.lineno,
                        "type": "HARDCODED_DATE_VIOLATION",
                        "message": f"Static date literal '{d}' found in procedural logic."
                    })
            # Check personal name monkey patches
            for name in BANNED_NAME_PATTERNS:
                if re.search(r"\b" + re.escape(name) + r"\b", val_lower) and not ("test" in self.filename or "mock" in self.filename):
                    self.violations.append({
                        "file": self.filename,
                        "line": node.lineno,
                        "type": "HARDCODED_NAME_VIOLATION",
                        "message": f"Hardcoded personal name '{name}' found in production logic."
                    })
        self.generic_visit(node)


def scan_file(path: Path) -> List[Dict[str, Any]]:
    if path.name in EXCLUDED_FILENAMES:
        return []
    try:
        content = path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(path))
        visitor = AntiHardcodingVisitor(str(path))
        visitor.visit(tree)
        return visitor.violations
    except Exception as exc:
        return [{
            "file": str(path),
            "line": 1,
            "type": "PARSE_ERROR",
            "message": f"Failed to parse AST: {exc}"
        }]


def main():
    parser = argparse.ArgumentParser(description="AST Anti-Hardcoding Linter")
    parser.add_argument("target_dir", nargs="?", default="dual_pipeline/transcript_stages_core", help="Directory to scan")
    parser.add_argument("--enforce-receipt", help="Path to write the governance receipt")
    args = parser.parse_args()

    target = Path(args.target_dir)
    if not target.exists():
        print(f"Target path {target} does not exist", file=sys.stderr)
        sys.exit(1)

    all_violations = []
    py_files = list(target.rglob("*.py")) if target.is_dir() else [target]

    for py_file in py_files:
        violations = scan_file(py_file)
        all_violations.extend(violations)

    receipt = {
        "status": "PASS" if not all_violations else "FAIL",
        "scanned_files_count": len(py_files),
        "violations_count": len(all_violations),
        "violations": all_violations,
    }

    if args.enforce_receipt:
        receipt_path = Path(args.enforce_receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(f"Receipt written to {receipt_path}")

    if all_violations:
        print(f"FAIL: Found {len(all_violations)} anti-hardcoding violations:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v['file']}:{v['line']} [{v['type']}] {v['message']}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"PASS: Zero anti-hardcoding violations detected across {len(py_files)} files.")
        sys.exit(0)


if __name__ == "__main__":
    main()
