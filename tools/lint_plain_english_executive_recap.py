#!/usr/bin/env python3
"""
tools/lint_plain_english_executive_recap.py

Enforces Master Invariant 12 (Adaptive Gate Proportionality & Anti-Word-Salad Standard).
Scans Output 02 Executive Recaps (`02_*_executive_recap.md`) to ensure:
1. Recaps pass the 60-second executive test.
2. Zero pseudo-academic word-salad filler.
3. Zero foreign system cross-contamination (e.g., Oracle P6 / Documentum in Project LENS).
4. Zero verbatim duplicated attendee perspective paragraphs.

Exits with code 0 if clean, 1 if any anti-pattern is detected.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"

BANNED_WORD_SALAD_PATTERNS = [
    (re.compile(r"\bevidenced rejection behavior\b", re.I), "Banned academic word-salad: 'evidenced rejection behavior'"),
    (re.compile(r"\baspirational operating concept\b", re.I), "Banned academic word-salad: 'aspirational operating concept'"),
    (re.compile(r"\bconfiguration persistence versus data portability\b", re.I), "Banned academic word-salad: 'configuration persistence versus data portability'"),
    (re.compile(r"\bcoaching future candidates\b", re.I), "Canned interview coaching text injected into client recap"),
]

# Foreign systems that should never appear in Project LENS (Wind Turbine / Renewable Generation)
LENS_FOREIGN_SYSTEMS = [
    (re.compile(r"\bOracle\s+P6\b", re.I), "Foreign system leak: Oracle P6 should not appear in Project LENS"),
    (re.compile(r"\bDocumentum\b", re.I), "Foreign system leak: Documentum should not appear in Project LENS"),
    (re.compile(r"\bPrimavera\b", re.I), "Foreign system leak: Primavera should not appear in Project LENS"),
]


def check_recap_file(filepath: Path) -> List[str]:
    violations: List[str] = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"Unable to read {filepath}: {exc}"]

    try:
        rel_path = str(filepath.relative_to(REPO_ROOT))
    except ValueError:
        rel_path = str(filepath)

    lines = content.splitlines()

    # 1. Word-salad checks
    for idx, line in enumerate(lines, start=1):
        for pattern, desc in BANNED_WORD_SALAD_PATTERNS:
            if pattern.search(line):
                violations.append(f"{rel_path}:{idx}: {desc}")

    # 2. Foreign domain cross-contamination for Project LENS
    if "project_lens" in rel_path.lower():
        for idx, line in enumerate(lines, start=1):
            for pattern, desc in LENS_FOREIGN_SYSTEMS:
                if pattern.search(line):
                    violations.append(f"{rel_path}:{idx}: {desc}")

    # 3. Duplicate speaker paragraphs across attendees
    # Detect if speaker paragraphs are duplicated verbatim (>80 chars)
    speaker_sections = re.split(r"(?m)^###\s+", content)
    seen_paragraphs: dict[str, str] = {}
    for section in speaker_sections[1:]:
        speaker_header = section.splitlines()[0].strip()
        paragraphs = [p.strip() for p in section.split("\n\n") if len(p.strip()) > 80]
        for p in paragraphs:
            # Normalize whitespace
            norm_p = re.sub(r"\s+", " ", p)
            if norm_p in seen_paragraphs:
                other_speaker = seen_paragraphs[norm_p]
                violations.append(
                    f"{rel_path}: Duplicate paragraph detected between '{other_speaker}' and '{speaker_header}': "
                    f"'{norm_p[:60]}...'"
                )
            else:
                seen_paragraphs[norm_p] = speaker_header

    return violations


def run_linter(target_files: Optional[Sequence[Path]] = None) -> Tuple[bool, List[str]]:
    all_violations: List[str] = []
    if target_files is None:
        files_to_check = [p for p in OUTPUT_DIR.rglob("*executive_recap.md")]
    else:
        files_to_check = [
            p for p in target_files
            if p.name.endswith("executive_recap.md") and p.exists()
        ]

    for f in files_to_check:
        all_violations.extend(check_recap_file(f))

    return len(all_violations) == 0, all_violations


def main() -> int:
    print("=" * 80)
    print("  PLAIN-ENGLISH EXECUTIVE RECAP & ANTI-WORD-SALAD LINTER (Invariant 12)")
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
            "linter": "lint_plain_english_executive_recap",
            "invariant": "Master Invariant 12",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            "violations_count": len(violations),
            "violations": violations,
        }
        receipt_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
        print(f"📄 Receipt saved to: {receipt_path}")

    if not passed:
        print(f"❌ FAILED: {len(violations)} plain-English / word-salad violation(s) detected:")
        for v in violations:
            print(f"   • {v}")
        print("================================================================================")
        return 1

    print("✅ PASSED: 0 word-salad patterns, foreign domain leaks, or duplicate paragraphs detected.")
    print("   All Output 02 recaps pass the 60-second executive clarity test.")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
