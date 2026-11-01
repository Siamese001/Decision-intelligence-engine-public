#!/usr/bin/env python3
"""
Module Budget & Complexity Linter (400-Line Budget Governance & MMI).

Enforces the AGENTS.md invariant and layer-architectural-integrity.md rule:
- Maximum 400 lines of code per Python module across governed pipeline packages.
- Multi-Dimensional Modularity Index (MMI) attestation override for cohesive modules.
- Non-blocking Amber Zone (401-600 lines) for modules with low cognitive complexity.
- Excludes test files, contract definitions, and generated schemas.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.file_budget_core import (
    DEFAULT_MAX_COGNITIVE_COMPLEXITY,
    DEFAULT_MAX_EXTERNAL_IMPORTS,
    calculate_ast_metrics,
    collect_oversized_modules,
    count_lines,
    evaluate_module_file,
    generate_baseline,
    generate_mmi_snapshot,
    load_ratchet_baseline,
    verify_attestations_against_ast,
)

DEFAULT_MAX_LINES = 400
AMBER_ZONE_MARGIN = 250

GOVERNED_DIRS = [
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "dual_pipeline",
    "unified_pipeline",
    "graph",
    "evals",
    "config",
    "storage",
    "scripts",
    "tools",
]

EXCLUSIONS_PATH = ROOT / "config" / "governance" / "file_budget_exclusions.json"
_REPORTS_BASELINE_PATH = ROOT / "reports" / "file_budget_burndown_baseline.json"
_CONFIG_BASELINE_PATH = ROOT / "config" / "governance" / "file_budget_burndown_baseline.json"
BASELINE_PATH = _REPORTS_BASELINE_PATH if _REPORTS_BASELINE_PATH.is_file() else _CONFIG_BASELINE_PATH
MMI_SNAPSHOT_PATH = ROOT / "reports" / "mmi_snapshot.json"

PERMITTED_NON_PRODUCTION_CLASSIFICATIONS = {
    "legacy_historical_script",
    "one_off_migration",
    "offline_diagnostic",
    "benchmark_runner",
    "governed_refactor_utility",
}


def _load_cohesive_specs() -> Dict[str, Any]:
    if EXCLUSIONS_PATH.is_file():
        try:
            with open(EXCLUSIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("permitted_production_cohesive_classifications", {})
        except Exception:
            pass
    return {}


PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS = _load_cohesive_specs()


def _validate_expiry_date(path: str, entry: Dict[str, Any]) -> Tuple[bool, str]:
    expires_at = entry.get("expires_at") or entry.get("expiry")
    if not expires_at:
        return (
            False,
            f"Exclusion for '{path}' is missing mandatory 'expires_at' date (Wave 4 Honest Ratchet).",
        )
    try:
        exp_date = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if exp_date.tzinfo is None:
            exp_date = exp_date.replace(tzinfo=timezone.utc)
        if exp_date < datetime.now(timezone.utc):
            return False, f"Exclusion for '{path}' expired on {expires_at}."
    except Exception as e:
        return False, f"Exclusion for '{path}' has invalid 'expires_at' date '{expires_at}': {e}"
    return True, "Valid"


def _validate_cohesive_attestation(
    path: str,
    entry: Dict[str, Any],
    classification: str,
    spec: Any,
    repo_root: Optional[Path],
    verify_ast: bool = True,
) -> Tuple[bool, str]:
    attestations = entry.get("attestations")
    if not isinstance(attestations, dict):
        return (
            False,
            f"Exclusion for '{path}' ({classification}) requires an 'attestations' dictionary.",
        )

    if not verify_ast:
        return True, "Valid"

    root = repo_root or ROOT
    target_file = (root / path).resolve()
    if target_file.is_file():
        valid, reason = verify_attestations_against_ast(
            target_file, classification, spec, attestations
        )
        if not valid:
            return False, f"Attestation verification failed for '{path}': {reason}"
    return True, "Valid"


def validate_exclusion_entry(
    entry: Any,
    repo_root: Optional[Path] = None,
    cohesive_specs: Optional[Dict[str, Any]] = None,
    verify_ast: bool = True,
) -> Tuple[bool, str]:
    """Validates an exclusion entry against non-production and MMI governance."""
    if not isinstance(entry, dict):
        return (
            False,
            f"Exclusion must be an object with path, classification, and justification (got {type(entry).__name__}).",
        )

    path = entry.get("path", "")
    if not path:
        return False, "Exclusion entry is missing required 'path' property."

    classification = entry.get("classification", "").strip()
    if not classification:
        return False, f"Exclusion for '{path}' lacks mandatory 'classification'."

    if classification == "production" or classification.startswith("production_"):
        return (
            False,
            f"Production script '{path}' cannot escape budget governance via exclusion (classification='{classification}').",
        )

    justification = entry.get("justification", "").strip()
    if len(justification) < 10:
        return False, f"Exclusion for '{path}' requires non-empty justification (minimum 10 chars)."

    valid_exp, exp_reason = _validate_expiry_date(path, entry)
    if not valid_exp:
        return False, exp_reason

    if classification in PERMITTED_NON_PRODUCTION_CLASSIFICATIONS:
        return True, "Valid"

    active_specs = (
        cohesive_specs
        if cohesive_specs is not None
        else PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS
    )
    if classification in active_specs:
        return _validate_cohesive_attestation(
            path, entry, classification, active_specs[classification], repo_root, verify_ast=verify_ast
        )

    return False, f"Exclusion for '{path}' has unrecognized classification '{classification}'."


def _load_excluded_patterns(verify_ast: bool = False) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    patterns: List[str] = ["tests/", "contracts/", "evals/"]
    cohesive_exemptions: Dict[str, Dict[str, Any]] = {}
    if EXCLUSIONS_PATH.is_file():
        try:
            with open(EXCLUSIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                from tools.file_budget_core.policy import validate_exempt_prefixes

                pref_errs = validate_exempt_prefixes(data.get("exempt_prefixes", []))
                for pe in pref_errs:
                    print(f"⚠️  [GOVERNANCE REJECTED] {pe}", file=sys.stderr)
                patterns.extend(
                    p for p in data.get("exempt_prefixes", []) if not str(p).strip().endswith(".py")
                )
                specs = data.get("permitted_production_cohesive_classifications", {})

                for item in data.get("exclusions", []):
                    valid, reason = validate_exclusion_entry(
                        item, repo_root=ROOT, cohesive_specs=specs, verify_ast=verify_ast
                    )
                    if valid:
                        path_str = item["path"]
                        patterns.append(path_str)
                        if item.get("classification") in specs:
                            cohesive_exemptions[path_str] = item
                    else:
                        print(
                            f"⚠️  [GOVERNANCE REJECTED] Exclusion for '{item.get('path', 'unknown')}': {reason}",
                            file=sys.stderr,
                        )

                for legacy_script in data.get("grandfathered_scripts", []):
                    if isinstance(legacy_script, dict):
                        valid, _ = validate_exclusion_entry(
                            legacy_script, repo_root=ROOT, cohesive_specs=specs, verify_ast=verify_ast
                        )
                        if valid:
                            patterns.append(legacy_script["path"])
        except Exception:
            pass
    return sorted(list(set(patterns))), cohesive_exemptions


EXCLUDED_PATTERNS, COHESIVE_EXEMPTIONS = _load_excluded_patterns(verify_ast=False)


def is_excluded(
    rel_path: str, ratchet: bool = False, baseline_modules: Optional[dict] = None
) -> bool:
    fname = Path(rel_path).name
    if fname.startswith("test_") or fname == "__init__.py":
        return True
    if any(p in rel_path for p in EXCLUDED_PATTERNS):
        return True
    if not ratchet:
        b_mods = (
            baseline_modules
            if baseline_modules is not None
            else load_ratchet_baseline(BASELINE_PATH).get("modules", {})
        )
        if rel_path in b_mods:
            return True
    return False


def get_staged_files(repo_root: Path) -> List[Path]:
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        files = []
        for line in res.stdout.splitlines():
            p = (repo_root / line.strip()).resolve()
            if p.suffix == ".py" and p.exists():
                files.append(p)
        return files
    except Exception:
        return []


def _resolve_target_files(repo_root: Path, staged: bool, explicit_files: List[str]) -> List[Path]:
    if staged:
        return get_staged_files(repo_root)
    if explicit_files:
        return [Path(f).resolve() for f in explicit_files if Path(f).suffix == ".py"]
    targets: List[Path] = []
    for gdir in GOVERNED_DIRS:
        dp = repo_root / gdir
        if dp.exists():
            for p in dp.rglob("*.py"):
                targets.append(p.resolve())
    return targets


def _collect_oversized_modules(repo_root: Path, max_lines: int) -> dict[str, int]:
    return collect_oversized_modules(
        repo_root,
        max_lines,
        lambda rel: is_excluded(rel, ratchet=True),
    )


def get_tiered_max_lines(rel_path: str, default_max: int = DEFAULT_MAX_LINES) -> int:
    """Tiered Semantic Budget:
    - Pure Data Models / Schemas: up to 800 lines.
    - Cohesive State Machines / Pipeline Engines: up to 650 lines.
    - Utility & Helper Modules: 400 lines standard floor.
    """
    path_lower = rel_path.lower()
    if any(k in path_lower for k in ("contract", "schema", "models", "types", "payload")):
        return max(default_max, 800)
    if any(
        k in path_lower for k in ("state_machine", "pipeline", "engine", "orchestrat", "runner")
    ):
        return max(default_max, 650)
    return default_max


def _eval_module_budget(
    rel: str,
    fp: Path,
    lines: int,
    max_lines: int,
    ratchet: bool,
    baseline_modules: dict,
    tiered: bool = True,
) -> Tuple[Optional[str], bool, Optional[str]]:
    """Evaluates module against shared module size & complexity policy.
    Returns (violation_reason, has_graduated, amber_warning).
    """
    if lines <= max_lines:
        if ratchet and rel in baseline_modules:
            return (None, True, None)
        return (None, False, None)

    verdict = evaluate_module_file(
        fp,
        repo_root=ROOT,
        use_exclusions=True,
        use_baseline=True,
        baseline_dict=baseline_modules,
    )
    if verdict.is_blocking:
        if verdict.classification == "complexity_violation" and lines <= DEFAULT_MAX_LINES + AMBER_ZONE_MARGIN:
            from tools.lint_cognitive_complexity import is_excluded as is_cc_excluded
            from tools.shift_left_core.cognitive import is_module_complexity_baselined

            if is_cc_excluded(rel) or is_module_complexity_baselined(rel, verdict.max_cc):
                return (None, False, verdict.reason)
        return (verdict.reason, False, None)

    if rel in baseline_modules and lines <= max_lines:
        return (None, True, None)

    if verdict.warning:
        return (None, False, verdict.reason)

    return (None, False, None)


def _print_budget_report(
    title: str,
    scanned_count: int,
    mode_desc: str,
    graduated: List[Tuple[str, int]],
    amber_warnings: List[Tuple[str, int, str]],
    violations: List[Tuple[str, int, str]],
    max_lines: int,
) -> int:
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)
    print(f"Scanned {scanned_count} module(s) {mode_desc}.")

    if graduated:
        print(
            f"\n🎉 {len(graduated)} module(s) have graduated to standard budget (<= {max_lines} lines):"
        )
        for rel_path, count in graduated:
            print(f"  - {rel_path}: {count} lines")

    if amber_warnings:
        print(
            f"\n⚠️  AMBER WARNING: Found {len(amber_warnings)} module(s) in amber zone (401-600 lines) with compliant metrics:"
        )
        for rel_path, count, info in amber_warnings:
            print(f"  - {rel_path}: {info}")

    if violations:
        print(f"\n❌ FAILED: Found {len(violations)} budget violation(s):\n")
        for rel_path, count, reason in sorted(violations, key=lambda x: x[1], reverse=True):
            print(f"  - {rel_path}: {count} lines — {reason}")
        print(
            "\nRemediation: Refactor large modules into modular '<module>_core/' packages or attest cohesive architecture."
        )
        print("=" * 80)
        return 1

    print("✅ PASSED: All scanned modules satisfy budget invariants.")
    print("=" * 80)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint Python module file budgets (max 400 lines & MMI)."
    )
    parser.add_argument(
        "--max-lines", type=int, default=DEFAULT_MAX_LINES, help="Max line limit per file"
    )
    parser.add_argument("--staged", action="store_true", help="Only check staged git files")
    parser.add_argument(
        "--ratchet",
        action="store_true",
        help="Enforce ratchet against reports/file_budget_burndown_baseline.json",
    )
    parser.add_argument(
        "--generate-baseline", action="store_true", help="Generate or update baseline JSON"
    )
    parser.add_argument(
        "--mmi-report",
        action="store_true",
        help="Emit Multi-Dimensional Modularity Index (MMI) snapshot",
    )
    parser.add_argument(
        "--mmi-output", type=Path, default=MMI_SNAPSHOT_PATH, help="Output path for MMI snapshot"
    )
    parser.add_argument(
        "--baseline-path", type=Path, default=BASELINE_PATH, help="Path to ratchet baseline JSON"
    )
    parser.add_argument("files", nargs="*", help="Specific files to check")
    args = parser.parse_args()

    repo_root = ROOT
    if args.generate_baseline:
        return generate_baseline(
            repo_root,
            args.baseline_path,
            args.max_lines,
            lambda rel: is_excluded(rel, ratchet=True),
        )

    baseline_data = load_ratchet_baseline(args.baseline_path)
    baseline_modules = baseline_data.get("modules", {}) if baseline_data else {}
    inflation_violations = baseline_data.get("_inflation_violations", []) if baseline_data else []
    target_files = _resolve_target_files(repo_root, args.staged, args.files)

    if args.mmi_report:
        return generate_mmi_snapshot(
            repo_root=repo_root,
            target_files=target_files,
            output_path=args.mmi_output,
            baseline_modules=baseline_modules,
            cohesive_exemptions=COHESIVE_EXEMPTIONS,
            get_tiered_max_lines_fn=get_tiered_max_lines,
            default_max_lines=args.max_lines,
            amber_zone_margin=AMBER_ZONE_MARGIN,
        )

    violations: List[Tuple[str, int, str]] = [
        ("reports/file_budget_burndown_baseline.json", 0, reason)
        for reason in inflation_violations
    ]
    graduated: List[Tuple[str, int]] = []
    amber_warnings: List[Tuple[str, int, str]] = []

    for fp in target_files:
        try:
            rel = str(fp.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            rel = str(fp).replace("\\", "/")
        if rel in COHESIVE_EXEMPTIONS:
            valid_coh, reason_coh = validate_exclusion_entry(
                COHESIVE_EXEMPTIONS[rel], repo_root=repo_root, verify_ast=True
            )
            if not valid_coh:
                violations.append((rel, count_lines(fp), reason_coh))
                continue
        if is_excluded(rel, ratchet=args.ratchet, baseline_modules=baseline_modules):
            continue

        lines = count_lines(fp)
        v_reason, is_grad, amber_info = _eval_module_budget(
            rel, fp, lines, args.max_lines, args.ratchet, baseline_modules
        )
        if v_reason:
            violations.append((rel, lines, v_reason))
        elif is_grad:
            graduated.append((rel, lines))
        elif amber_info:
            amber_warnings.append((rel, lines, amber_info))

    title = (
        "MODULE SIZE RATCHET LINTER"
        if args.ratchet
        else f"MODULE SIZE & COMPLEXITY BUDGET LINTER (Max {args.max_lines} Lines)"
    )
    mode_desc = (
        f"under ratchet baseline ({len(baseline_modules)} registered)"
        if args.ratchet
        else f"against max {args.max_lines} lines"
    )
    return _print_budget_report(
        title, len(target_files), mode_desc, graduated, amber_warnings, violations, args.max_lines
    )


if __name__ == "__main__":
    sys.exit(main())
