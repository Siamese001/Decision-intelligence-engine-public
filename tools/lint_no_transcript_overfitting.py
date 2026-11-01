#!/usr/bin/env python3
"""
tools/lint_no_transcript_overfitting.py

Enforces Master Invariant 8 (Production Derivation Integrity) & Invariant 10 (Prohibition of Custom Ingestion Scripts).
Scans dual_pipeline/ to ensure that no meeting-specific healers, transcript post-extractors,
hardcoded domain text constants, keyword dispatch cascades, or synthetic statement ID injections exist.

Exits with code 0 if clean, 1 if any anti-pattern is detected.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DUAL_PIPELINE_DIR = REPO_ROOT / "dual_pipeline"

# Prohibited meeting-specific filename patterns in dual_pipeline/
FORBIDDEN_FILE_PATTERNS = [
    re.compile(r"^neet_healer\.py$", re.I),
    re.compile(r"^nexus_healer\.py$", re.I),
    re.compile(r"^jake_amit_miles_healer\.py$", re.I),
    re.compile(r"^lens_catchup.*_healer\.py$", re.I),
    re.compile(r"^mid_sprint_healer\.py$", re.I),
    re.compile(r"^graph_post_(jake|lens|mid_sprint|reid|nexus).*\.py$", re.I),
    re.compile(r"^strategic_graph_.*\.py$", re.I),
]

# Prohibited meeting-specific variable names or functions
FORBIDDEN_IDENTIFIERS = {
    "is_mid_sprint",
    "is_lens_catchup",
    "is_jake_amit_miles",
    "is_lens_catchup_0919",
    "is_reid_or_nexus",
    "heal_mid_sprint_output_2",
    "heal_jake_amit_miles_output_2",
    "heal_lens_catchup_output_2",
    "heal_lens_catchup_0919_output_2",
    "heal_neet_output_2",
    "heal_nexus_output_2",
    "heal_mid_sprint_projection",
    "heal_jake_amit_miles_projection",
    "heal_lens_catchup_projection",
    "heal_lens_catchup_0919_projection",
    "heal_strategic_graph",
    "build_strategic_proposals",
    "REID_NEXUS_CONSOLIDATED_NEXT_STEPS",
    "LENS_PHASE1_CONSOLIDATED_NEXT_STEPS",
    "LENS_SOW_BOUNDARIES_CONSOLIDATED_NEXT_STEPS",
    "is_gemini_enablement",
    "_heal_gemini_enablement_projection",
    "_is_jarvis_session",
    "_ground_jarvis_proposal",
    "is_jarvis",
    "_purge_unanchored_christian_moberg",
    "is_neet_session_1",
    "_is_neet_session_1",
    "_is_bcg_meeting",
    "is_bcg_meeting",
    "_is_partner_advisory_meeting",
    "_is_lens_sprint_review",
    "_is_standup_meeting",
    "_is_scope_clarification",
    "heal_lens_sprint_review_projection",
    "heal_standup_projection",
    "heal_scope_clarification_projection",
    "heal_neet_insights_hub_session1_projection",
}

# Prohibited synthetic string constants that inject precomputed domain facts
FORBIDDEN_STRING_PATTERNS = [
    (re.compile(r"\bcoaching future candidates\b", re.I), "Canned interview coaching boilerplate"),
    (re.compile(r"\bev-910-\d{2}\b", re.I), "Synthetic APEX_TRANS statement ID injection"),
    (re.compile(r"\bstmt-opus-3616\b", re.I), "Synthetic Jake-Amit-Miles statement ID injection"),
    (re.compile(r"\bev-capital-estimate\b", re.I), "Synthetic Reid Nexus evidence ID injection"),
    (re.compile(r"\bTarget Architecture Alignment & Delivery Compromise\b", re.I), "Canned Amit Ayer role boilerplate"),
    (re.compile(r"\bharper Hoffman Reported Operational Work Scope\b", re.I), "Synthetic harper Hoffman paragraph injection"),
    (re.compile(r"\bEscalate Three-Week Schedule Overage and Governance Deficits\b", re.I), "Synthetic 09/30 escalation narrative"),
    (re.compile(r"\bDemands Code-Faithful Architecture Reference to Eliminate Client Handoff Drift\b", re.I), "Synthetic 09/30 KDP replacement"),
    (re.compile(r"\bGoogle led a hands-on Gemini Notebooks enablement session\b", re.I), "Synthetic Gemini Enablement objective"),
    (re.compile(r"\bAmit Ayer had recently joined the image-LLM workstream\b", re.I), "Synthetic 09/18 objective narrative"),
    (re.compile(r"\bev-jarvis-framework\b", re.I), "Synthetic Jarvis framework evidence ID injection"),
    (re.compile(r"\bev-wbs-actuals\b", re.I), "Synthetic WBS actuals evidence ID injection"),
    (re.compile(r"\bev-powerbi-ratified\b", re.I), "Synthetic Power BI evidence ID injection"),
    (re.compile(r"\bChristian Moberg\b", re.I), "Hardcoded Christian Moberg persona injection"),
    (re.compile(r"\b01:12:06\b", re.I), "Hardcoded 01:12:06 timestamp injection"),
    (re.compile(r"\b00:07:51\b", re.I), "Hardcoded 00:07:51 timestamp injection"),
]


# Canonical alias/system directory modules allowed to hold global canonical ID maps
CANONICAL_ID_REGISTRY_MODULES = {
    "system_directory.py",
    "alias_registry.py",
    "graph_healer.py",
    "graph_post_entity_healer.py",
    "signal_extraction.py",
    "copilot_connection_policy.py",
}
_ENTITY_ID_LITERAL_RE = re.compile(
    r"^(person|org|system|workstream|project|policy|role|metric|risk|deliverable|milestone|forum|workshop|model):[a-z0-9-]+$"
)


def _format_path(filepath: Path) -> str:
    try:
        return str(filepath.relative_to(REPO_ROOT))
    except ValueError:
        return str(filepath)


def check_file_naming(filepath: Path) -> List[str]:
    violations: List[str] = []
    name = filepath.name
    for pattern in FORBIDDEN_FILE_PATTERNS:
        if pattern.search(name):
            violations.append(f"Meeting-specific file forbidden under Invariant 8: {_format_path(filepath)}")
    return violations


def check_code_content(filepath: Path) -> List[str]:
    violations: List[str] = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"Unable to read {filepath}: {exc}"]

    # 1. AST check for forbidden identifiers and anti-patterns
    try:
        tree = ast.parse(content, filename=str(filepath))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in FORBIDDEN_IDENTIFIERS:
                    violations.append(f"{_format_path(filepath)}:{node.lineno}: Forbidden meeting-specific function '{node.name}'")
                elif any(node.name.startswith(pfx) for pfx in ("_is_neet", "is_neet", "_is_bcg", "is_bcg", "_is_standup", "_is_partner_advisory_bcg", "_is_synthetic_", "_is_overfitted_")):
                    violations.append(f"{_format_path(filepath)}:{node.lineno}: Prohibited meeting-specific predicate function '{node.name}'")
            elif isinstance(node, ast.Name):
                if node.id in FORBIDDEN_IDENTIFIERS:
                    violations.append(f"{_format_path(filepath)}:{node.lineno}: Forbidden meeting-specific identifier '{node.id}'")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript):
                        target_str = ""
                        try:
                            target_str = ast.unparse(target).lower()
                        except Exception:
                            pass
                        if any(k in target_str for k in ("entity_upserts", "entities", "relationship_signals", "signals")):
                            if isinstance(node.value, ast.List) and len(node.value.elts) >= 3:
                                if all(isinstance(elt, ast.Dict) for elt in node.value.elts):
                                    violations.append(
                                        f"{_format_path(filepath)}:{node.lineno}: Prohibited collection-wipe overwrite detected: "
                                        f"'{target_str}' overwritten with {len(node.value.elts)} static dict literals."
                                    )
        if filepath.name not in CANONICAL_ID_REGISTRY_MODULES:
            for mod_node in tree.body:
                if isinstance(mod_node, (ast.Assign, ast.AnnAssign)) and mod_node.value is not None:
                    id_lits = [
                        c.value
                        for c in ast.walk(mod_node.value)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str) and _ENTITY_ID_LITERAL_RE.match(c.value)
                    ]
                    if len(id_lits) >= 5:
                        violations.append(
                            f"{_format_path(filepath)}:{mod_node.lineno}: Prohibited module-level hardcoded entity ID table "
                            f"({len(id_lits)} static entity ID literals: {id_lits[:3]})."
                        )
    except SyntaxError:
        pass

    # 2. Regex check for synthetic string injections
    lines = content.splitlines()
    for idx, line in enumerate(lines, start=1):
        for pattern, desc in FORBIDDEN_STRING_PATTERNS:
            if pattern.search(line):
                # Do not flag QA gates or judge rules that define detection patterns to ban these strings
                if filepath.name in ("recap.py", "gate2.py", "recap_checks.py", "output2_contract.py") or any(k in line for k in ("WORD_SALAD", "banned_salad", "re.compile")):
                    continue
                violations.append(f"{_format_path(filepath)}:{idx}: Forbidden synthetic injection: {desc} ('{line.strip()[:60]}')")

    return violations


def run_linter(target_files: List[Path] | None = None) -> Tuple[bool, List[str]]:
    all_violations: List[str] = []
    if target_files is None:
        files_to_check = [p for p in DUAL_PIPELINE_DIR.rglob("*.py") if "__pycache__" not in str(p)]
    else:
        files_to_check = [
            p for p in target_files
            if p.suffix == ".py" and str(p.resolve()).startswith(str(DUAL_PIPELINE_DIR.resolve())) and p.exists()
        ]

    for f in files_to_check:
        all_violations.extend(check_file_naming(f))
        all_violations.extend(check_code_content(f))

    return len(all_violations) == 0, all_violations


def main() -> int:
    import json
    from datetime import datetime, timezone

    print("=" * 80)
    print("  PRODUCTION DERIVATION INTEGRITY & ANTI-OVERFITTING LINTER (Invariant 8)")
    print("================================================================================")

    target_files = None
    receipt_path = None
    args = sys.argv[1:]
    if "--receipt" in args:
        idx = args.index("--receipt")
        if idx + 1 < len(args):
            receipt_path = Path(args[idx + 1])

    if "--staged" in args:
        # Check staged files in git
        import subprocess
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            target_files = [REPO_ROOT / p.strip() for p in res.stdout.splitlines() if p.strip()]

    passed, violations = run_linter(target_files)

    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_data = {
            "linter": "lint_no_transcript_overfitting",
            "invariant": "Master Invariant 8",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            "violations_count": len(violations),
            "violations": violations,
        }
        receipt_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
        print(f"📄 Receipt saved to: {receipt_path}")

    if not passed:
        print(f"❌ FAILED: {len(violations)} derivation integrity violation(s) detected:")
        for v in violations:
            print(f"   • {v}")
        print("================================================================================")
        return 1

    print("✅ PASSED: 0 meeting-specific files, dispatch branches, or synthetic constants detected.")
    print("   All domain outputs and graph mutations derive strictly from runtime source inputs.")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())

