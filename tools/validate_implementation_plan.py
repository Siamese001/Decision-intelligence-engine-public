#!/usr/bin/env python3
"""Mechanical enforcement validator for wave-based implementation plans.

Enforces governance rules defined in AGENTS.md and .agents/rules/wave-progression.md:
1. Every implementation plan must be wave-based (decomposed into sequential numbered waves).
2. Wave numbering must start at 0 or 1 and increment consecutively without gaps.
3. Every wave must declare explicit objective & scope, milestones & deliverables,
   acceptance criteria, and a runtime receipt or completion gate.
4. Each wave must specify at least one concrete deliverable or target file.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.hitl_governance_core.decision_clearance import (
    is_decision_ratified_in_ledger,
    parse_decisions_from_table,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths
from tools.implementation_plan_core.evidence_requirements import validate_execution_evidence

# Historical / frozen documentation plans grandfathered from strict wave schema
DEFAULT_LEGACY_EXEMPTIONS = {
    "POST_IMPLEMENTATION_EVALUATION_ASSURANCE_PLAN.md", "DECISION_INTELLIGENCE_P0_P2_IMPLEMENTATION_PLAN.md",
    "DUAL_PIPELINE_EVAL_HARNESS_IMPLEMENTATION_PLAN.md", "DUAL_PIPELINE_SHADOW_LEARNING_IMPLEMENTATION_PLAN.md",
    "GRAPH_RETRIEVAL_ENHANCEMENT_WAVE_PLAN.md", "LLM_AS_JUDGE_REFACTOR_IMPLEMENTATION_PLAN.md",
    "NEE_DECISION_ROUTING_REFACTOR_WAVE_PLAN.md", "QUERY_RETRIEVAL_EVIDENCE_WAVE_IMPLEMENTATION_PLAN.md",
    "AUTOMATIC_EVALUATION_RUNS_WAVE_PLAN.md", "LLM_AS_JUDGE_MEETING_PIPELINE_WAVE_PLAN.md",
}

# Regex patterns for wave parsing
RE_TITLE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
RE_WAVE_HEADER = re.compile(r"^(#{2,3})\s+Wave\s+(\d+)(?:[:\s\-]+(.*))?$", re.IGNORECASE)

# Patterns for required wave subsections
RE_OBJECTIVE = re.compile(
    r"(?:^#{3,4}\s+.*(?:objective|scope).*)|(?:^\s*(?:[-*+]\s+)?\*\*(?:.*(?:objective|scope).*?)\*\*)",
    re.IGNORECASE,
)
RE_MILESTONES = re.compile(
    r"(?:^#{3,4}\s+.*(?:milestone|deliverable).*)|(?:^\s*(?:[-*+]\s+)?\*\*(?:.*(?:milestone|deliverable).*?)\*\*)",
    re.IGNORECASE,
)
RE_CRITERIA = re.compile(
    r"(?:^#{3,4}\s+.*(?:acceptance\s+criteria|criteria).*)|(?:^\s*(?:[-*+]\s+)?\*\*(?:.*(?:acceptance\s+criteria|criteria).*?)\*\*)",
    re.IGNORECASE,
)
RE_RECEIPT_GATE = re.compile(
    r"(?:^#{3,4}\s+.*(?:receipt|completion\s+gate|exit\s+gate|verification).*)|(?:^\s*(?:[-*+]\s+)?\*\*(?:.*(?:receipt|completion\s+gate|exit\s+gate|verification).*?)\*\*)",
    re.IGNORECASE,
)
RE_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.+)$")


class WaveSection:
    """Represents a parsed wave block within an implementation plan."""

    def __init__(self, number: int, title: str, start_line: int, header_level: int = 2):
        self.number = number
        self.title = title
        self.start_line = start_line
        self.header_level = header_level
        self.lines: List[Tuple[int, str]] = []  # (line_number, line_text)
        self.has_objective = False
        self.has_milestones = False
        self.has_criteria = False
        self.has_receipt_gate = False
        self.deliverable_items: List[str] = []

    def analyze(self) -> None:
        """Inspect contents of the wave block for required governance sections."""
        current_sub: Optional[str] = None
        for _line_no, line in self.lines:
            stripped = line.strip()
            if not stripped:
                continue

            if RE_OBJECTIVE.search(stripped):
                self.has_objective = True
                current_sub = "objective"
                continue
            elif RE_MILESTONES.search(stripped):
                self.has_milestones = True
                current_sub = "milestones"
                continue
            elif RE_CRITERIA.search(stripped):
                self.has_criteria = True
                current_sub = "criteria"
                continue
            elif RE_RECEIPT_GATE.search(stripped):
                self.has_receipt_gate = True
                current_sub = "receipt"
                continue
            elif stripped.startswith("### "):
                current_sub = "other"
                continue

            item_match = RE_ITEM.match(stripped)
            if item_match:
                content = item_match.group(1).strip()
                if (
                    current_sub == "milestones"
                    or current_sub is None
                    and any(
                        k in content.lower()
                        for k in ("deliverable", "milestone", "[new]", "[modify]", "[delete]")
                    )
                ):
                    self.deliverable_items.append(content)


def _extract_waves(lines: List[str]) -> List[WaveSection]:
    waves: List[WaveSection] = []
    current_wave: Optional[WaveSection] = None
    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        wave_match = RE_WAVE_HEADER.match(stripped)
        if wave_match:
            hashes = wave_match.group(1)
            wave_num = int(wave_match.group(2))
            wave_title = (wave_match.group(3) or "").strip()
            current_wave = WaveSection(wave_num, wave_title, idx, header_level=len(hashes))
            waves.append(current_wave)
            continue

        if current_wave is not None and stripped.startswith("#"):
            heading_hashes = len(stripped) - len(stripped.lstrip("#"))
            if heading_hashes <= current_wave.header_level:
                current_wave = None

        if current_wave is not None:
            current_wave.lines.append((idx, raw_line))
    return waves


def _validate_wave_subsections(waves: List[WaveSection], filename: str, errors: List[str]) -> None:
    for w in waves:
        w.analyze()
        prefix = f"{filename}: Line {w.start_line} (Wave {w.number})"

        if not w.has_objective:
            errors.append(
                f"{prefix}: Missing required 'Objective & Scope' section "
                f"(expected '### Objective & Scope' or '**Objective & Scope**')."
            )

        if not w.has_milestones:
            errors.append(
                f"{prefix}: Missing required 'Milestones & Deliverables' section "
                f"(expected '### Milestones & Deliverables' or '**Milestones**')."
            )
        elif not w.deliverable_items:
            errors.append(
                f"{prefix}: 'Milestones & Deliverables' must contain at least one itemized output or target file."
            )

        if not w.has_criteria:
            errors.append(
                f"{prefix}: Missing required 'Acceptance Criteria' section "
                f"(expected '### Acceptance Criteria' or '**Acceptance Criteria**')."
            )

        if not w.has_receipt_gate:
            errors.append(
                f"{prefix}: Missing required 'Runtime Receipt & Completion Gate' section "
                f"(expected '### Runtime Receipt & Completion Gate', '### Exit Gate', or '**Runtime Receipt**')."
            )


def _validate_decisions_table(content: str, filename: str, errors: List[str]) -> None:
    table_decs = parse_decisions_from_table(content, source_file=filename)
    if not table_decs:
        return
    s_path, j_path = resolve_ledger_paths(repo_root=ROOT)
    for td in table_decs:
        if td.requires_asking:
            handling_lower = td.handling.lower()
            is_claimed_resolved = any(k in handling_lower for k in ("resolved", "autonomous", "self-resolved", "bypassed"))
            is_ratified, reason = is_decision_ratified_in_ledger(td.decision_id, s_path, j_path)
            if is_claimed_resolved and not is_ratified:
                errors.append(
                    f"{filename}: Line {td.source_line or 0}: Decision '{td.decision_id}' has confidence gap {td.gap:.1%} <= 30% "
                    f"and is marked '{td.handling}' without an authentic operator answer recorded in the ledger. "
                    f"Autonomous resolution of qualifying decisions is strictly prohibited under the SSOT Ambiguity Rule (R1)."
                )


def validate_plan_content(content: str, filename: str = "plan.md", strict: bool = True) -> Tuple[bool, List[str]]:
    """Validate markdown implementation plan text against wave-based governance schema.

    Returns:
        (is_compliant: bool, errors: list[str])
    """
    errors: List[str] = []
    lines = content.splitlines()

    # 1. Document Title Verification
    has_title = False
    for line in lines:
        if line.startswith("# ") and line[2:].strip():
            has_title = True
            break
    if not has_title:
        errors.append(f"{filename}: Missing required top-level title ('# <Plan Title>').")

    # 2. Extract Wave Sections
    waves = _extract_waves(lines)
    if not waves:
        errors.append(
            f"{filename}: Implementation plan must be wave-based. Found 0 wave sections ('## Wave <N>'). "
            "Flat or unstructured plans are strictly prohibited."
        )
        return False, errors

    # 3. Validate Sequential Wave Numbering
    start_num = waves[0].number
    if start_num not in (0, 1):
        errors.append(
            f"{filename}: First wave must be numbered Wave 0 or Wave 1. Found Wave {start_num} at line {waves[0].start_line}."
        )

    expected_num = start_num
    for w in waves:
        if w.number != expected_num:
            errors.append(
                f"{filename}: Non-sequential wave numbering at line {w.start_line}. "
                f"Expected Wave {expected_num}, found Wave {w.number}."
            )
            expected_num = w.number + 1
        else:
            expected_num += 1

    # 4. Validate Required Subsections and Deliverables per Wave
    _validate_wave_subsections(waves, filename, errors)
    if strict:
        errors.extend(validate_execution_evidence(content, waves, filename))

    # 5. Validate Operator Decisions & Anti-Autonomous Resolution Invariant
    _validate_decisions_table(content, filename, errors)

    return len(errors) == 0, errors


def validate_file(
    path: Path, legacy_exemptions: Optional[set[str]] = None, strict: bool = True
) -> Tuple[bool, List[str]]:
    """Validate a single implementation plan file on disk."""
    if legacy_exemptions and path.name in legacy_exemptions:
        return True, [f"[EXEMPT] Skipped legacy plan: {path.name}"]

    if not path.is_file():
        return False, [f"File not found: {path}"]

    # Single Plan Location Policy (Wave 7 / Finding X-18): root implementation_plan.md is blocked on main
    try:
        rel = path.relative_to(ROOT)
        if len(rel.parts) == 1 and rel.name == "implementation_plan.md":
            proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
            )
            if proc.stdout.strip() == "main":
                return False, [
                    "[SINGLE_PLAN_LOCATION_VIOLATION] Root 'implementation_plan.md' is blocked on main. "
                    "Governed implementation plans must reside in docs/plans/<branch-slug>.md."
                ]
    except Exception:
        pass

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, [f"Failed reading {path}: {exc}"]

    return validate_plan_content(content, filename=str(path.name), strict=strict)


def get_staged_plan_files(repo_root: Path) -> List[Path]:
    """Retrieve staged markdown files matching implementation plan naming patterns."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root, capture_output=True, text=True, check=True,
        )
        staged: List[Path] = []
        for line in res.stdout.splitlines():
            clean = line.strip()
            if not clean.endswith(".md") or clean.startswith("output/") or "/referenced_artifacts/" in clean:
                continue
            lower = clean.lower()
            if "implementation_plan" in lower or lower.endswith("_plan.md"):
                full_path = repo_root / clean
                if full_path.is_file():
                    staged.append(full_path)
        return staged
    except Exception:
        return []


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate wave-based implementation plans against repository governance invariants."
    )
    parser.add_argument(
        "--file", type=Path, help="Path to a specific implementation plan file to validate."
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Validate any implementation plan files currently staged in git.",
    )
    parser.add_argument(
        "--check-all",
        action="store_true",
        help="Validate all implementation plan files across documentation/ and active brain sessions.",
    )
    parser.add_argument(
        "--legacy-exempt",
        nargs="*",
        default=list(DEFAULT_LEGACY_EXEMPTIONS),
        help="List of plan filenames to exempt from strict wave schema.",
    )
    parser.add_argument("--quiet", action="store_true", help="Only output errors on failure.")

    args = parser.parse_args(argv)
    exempt_set = set(args.legacy_exempt)
    target_files: List[Path] = []

    if args.file:
        target_files.append(args.file.resolve())
    elif args.staged:
        target_files = get_staged_plan_files(ROOT)
        if not target_files:
            if not args.quiet:
                print("No staged implementation plans found to validate.")
            return 0
    elif args.check_all:
        doc_dir = ROOT / "documentation"
        if doc_dir.is_dir():
            for p in doc_dir.glob("*.md"):
                lower = p.name.lower()
                if "plan" in lower:
                    target_files.append(p)
        try:
            from tools.validate_implementation_summary_core.auditor import find_plan_candidates

            for cand in find_plan_candidates(ROOT):
                if cand not in target_files:
                    target_files.append(cand)
        except Exception:
            pass
    else:
        # Default: if run with no args, check staged files
        target_files = get_staged_plan_files(ROOT)
        if not target_files:
            print(
                "Usage: python tools/validate_implementation_plan.py [--file <path> | --staged | --check-all]"
            )
            return 0

    total_files = len(target_files)
    failed_files = 0
    all_errors: List[str] = []

    for path in target_files:
        is_valid, errors = validate_file(path, legacy_exemptions=exempt_set, strict=not args.check_all)
        if not is_valid:
            failed_files += 1
            all_errors.extend(errors)
        else:
            if not args.quiet:
                status = "[EXEMPT]" if any("EXEMPT" in e for e in errors) else "[PASS]"
                print(f"{status} {path.name}")

    if failed_files > 0:
        print(
            f"\n[FAIL] {failed_files} of {total_files} implementation plan(s) failed wave governance validation:\n"
        )
        for err in all_errors:
            print(f"  - {err}")
        print(
            "\nGovernance Remedy:\n"
            "  1. Decompose plan into sequential numbered waves: '## Wave 1: <Title>'.\n"
            "  2. In every wave, include '### Milestones & Deliverables' with itemized outputs.\n"
            "  3. In every wave, include '### Acceptance Criteria' with pass/fail requirements.\n"
            "  4. In every wave, include '### Runtime Receipt & Completion Gate'.\n"
            "  5. Ensure advancing to Wave N+1 is gated on observed behavior and independently checked evidence.\n"
        )
        return 1

    if not args.quiet:
        print(
            f"\n[SUCCESS] All {total_files} implementation plan(s) satisfy wave-based governance."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
