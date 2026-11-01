"""
tools/file_budget_core/policy.py

Shared policy loader, evaluator, exclusion validator, and baseline migration engine.
Governs candidate module size policy semantics and shadow evaluation for Wave 2.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class PolicyVerdict:
    verdict: str  # "PASS", "WARNING", "BLOCK", "ERROR"
    is_blocking: bool
    warning: bool
    severity: str  # "NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"
    reason: str
    policy_identity: str
    lines: int
    max_cc: int
    has_branch_logic: bool
    classification: str
    baseline_allowed_lines: Optional[int] = None
    is_exception_active: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidatePolicy:
    schema_version: str
    policy_id: str
    status: str
    clean_ceiling: int
    warning_ceiling_standard: int
    warning_ceiling_declarative: int
    exception_ceiling_cohesive: int
    exception_ceiling_table: int
    exception_ceiling_registry: int
    max_cognitive_complexity: int
    rules: Dict[str, Any]
    scope_resolution: Dict[str, Any]


def load_candidate_policy(policy_path: Optional[Path] = None) -> CandidatePolicy:
    if policy_path is not None:
        path = policy_path
    else:
        gov_path = ROOT / "config" / "governance" / "module_budget_policy.json"
        legacy_path = ROOT / "reports" / "module_budget_policy" / "w1" / "candidate_policy.json"
        path = gov_path if gov_path.is_file() else legacy_path

    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {}
    b = data.get("boundaries", {})
    comp = data.get("complexity_reconciliation", {})

    return CandidatePolicy(
        schema_version=data.get("schema_version", "1.0.0"),
        policy_id=data.get("policy_id", "candidate-module-budget-policy:1.0.0"),
        status=data.get("status", "DRAFT_CANDIDATE"),
        clean_ceiling=b.get("clean_ceiling", 400),
        warning_ceiling_standard=b.get("warning_ceiling_standard", 650),
        warning_ceiling_declarative=b.get("warning_ceiling_declarative", 800),
        exception_ceiling_cohesive=b.get("exception_ceiling_cohesive", 900),
        exception_ceiling_table=b.get("exception_ceiling_table", 1200),
        exception_ceiling_registry=b.get("exception_ceiling_registry", 3000),
        max_cognitive_complexity=comp.get("standard_cognitive_complexity_ceiling", 15),
        rules=data.get("boundary_rules", {}),
        scope_resolution=data.get("legacy_scope_resolution", {}),
    )


def is_ast_pure_declarative(tree: ast.AST) -> bool:
    non_declarative_types = (
        ast.If,
        ast.IfExp,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.ExceptHandler,
        ast.With,
        ast.AsyncWith,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Lambda,
        ast.Call,
        ast.BoolOp,
        ast.ListComp,
        ast.DictComp,
        ast.SetComp,
        ast.GeneratorExp,
    )
    if hasattr(ast, "Match"):
        non_declarative_types += (ast.Match,)
    return not any(isinstance(n, non_declarative_types) for n in ast.walk(tree))


def _make_verdict(v: str, blk: bool, warn: bool, sev: str, r: str, pid: str, l: int, cc: int, br: bool, cls: str, base: Optional[int] = None, exc: bool = False) -> PolicyVerdict:
    return PolicyVerdict(
        verdict=v, is_blocking=blk, warning=warn, severity=sev, reason=r,
        policy_identity=pid, lines=l, max_cc=cc, has_branch_logic=br,
        classification=cls, baseline_allowed_lines=base, is_exception_active=exc,
    )



def _check_exclusion(
    norm_rel: str, lines: int, max_cc: int, has_branch: bool,
    exclusions_dict: Optional[Dict[str, Any]], policy: CandidatePolicy,
) -> Optional[PolicyVerdict]:
    if not exclusions_dict or norm_rel not in exclusions_dict:
        return None
    excl = exclusions_dict[norm_rel]
    expires_at = excl.get("expires_at")
    if not expires_at:
        return _make_verdict("ERROR", True, False, "CRITICAL", f"Exclusion for '{norm_rel}' is missing mandatory 'expires_at' date.", policy.policy_id, lines, max_cc, has_branch, "invalid_exclusion")
    try:
        exp_date = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if exp_date.tzinfo is None:
            exp_date = exp_date.replace(tzinfo=timezone.utc)
        if exp_date < datetime.now(timezone.utc):
            return _make_verdict("BLOCK", True, False, "HIGH", f"Exclusion for '{norm_rel}' expired on {expires_at}.", policy.policy_id, lines, max_cc, has_branch, "expired_exclusion")
    except Exception as e:
        return _make_verdict("ERROR", True, False, "CRITICAL", f"Exclusion for '{norm_rel}' has malformed expiry date: {e}", policy.policy_id, lines, max_cc, has_branch, "malformed_exclusion")

    classification = excl.get("classification", "approved_exception")
    ceilings = {
        "cohesive_structural_module": policy.exception_ceiling_cohesive,
        "pure_declarative_table": policy.exception_ceiling_table,
        "declarative_registry": policy.exception_ceiling_registry,
    }
    class_ceiling = ceilings.get(classification, 3000)
    if lines > class_ceiling:
        return _make_verdict("BLOCK", True, False, "HIGH", f"Module lines ({lines}) exceed approved exception ceiling ({class_ceiling}) for {classification}.", policy.policy_id, lines, max_cc, has_branch, classification, exc=True)

    attestations = excl.get("attestations") if isinstance(excl.get("attestations"), dict) else {}
    raw_attested_cc = int(attestations.get("max_function_cc", policy.max_cognitive_complexity))
    attested_cc_ceiling = min(raw_attested_cc, policy.max_cognitive_complexity)
    if max_cc > attested_cc_ceiling:
        return _make_verdict("BLOCK", True, False, "CRITICAL", f"Module max CC ({max_cc}) exceeds attested ceiling ({attested_cc_ceiling}) for {classification}.", policy.policy_id, lines, max_cc, has_branch, classification, exc=True)

    return _make_verdict("WARNING" if lines > 400 else "PASS", False, lines > 400, "LOW" if lines > 400 else "NONE", f"Permitted under active exception ({classification}, expires {expires_at}).", policy.policy_id, lines, max_cc, has_branch, classification, exc=True)


def validate_exempt_prefixes(exempt_prefixes: List[str]) -> List[str]:
    """Ensure exempt_prefixes contains only directory prefixes and zero individual .py modules."""
    violations: List[str] = []
    for prefix in exempt_prefixes:
        norm = str(prefix).strip()
        if norm.endswith(".py"):
            violations.append(
                f"Individual Python module '{norm}' is prohibited in exempt_prefixes; use ratcheted baseline or expiring exclusions."
            )
    return violations


def _check_baseline(
    norm_rel: str, lines: int, max_cc: int, has_branch: bool,
    baseline_dict: Optional[Dict[str, int]], policy: CandidatePolicy,
) -> Optional[PolicyVerdict]:
    if not baseline_dict or norm_rel not in baseline_dict:
        return None
    baseline_lines = baseline_dict[norm_rel]
    if lines > baseline_lines:
        return _make_verdict("BLOCK", True, False, "HIGH", f"Functional growth beyond registered baseline: {lines} > recorded {baseline_lines} lines (no silent baseline increase).", policy.policy_id, lines, max_cc, has_branch, "baseline_overflow", base=baseline_lines)
    return _make_verdict("WARNING" if lines > 400 else "PASS", False, lines > 400, "LOW" if lines > 400 else "NONE", f"Permitted under registered legacy baseline burndown allowance ({lines} <= {baseline_lines} lines).", policy.policy_id, lines, max_cc, has_branch, "legacy_baseline", base=baseline_lines)


def _check_boundaries(
    lines: int, max_cc: int, pure_decl: bool, policy: CandidatePolicy,
) -> PolicyVerdict:
    pid = policy.policy_id
    has_branch = not pure_decl

    if max_cc > policy.max_cognitive_complexity:
        return _make_verdict("BLOCK", True, False, "CRITICAL", f"Cognitive complexity violation: max function CC is {max_cc} (exceeds hard ceiling of {policy.max_cognitive_complexity}).", pid, lines, max_cc, has_branch, "complexity_violation")

    if lines <= policy.clean_ceiling:
        return _make_verdict("PASS", False, False, "NONE", f"Within clean module size budget ({lines} <= {policy.clean_ceiling} lines).", pid, lines, max_cc, has_branch, "clean_module")

    if lines <= policy.warning_ceiling_standard:
        return _make_verdict("WARNING", False, True, "LOW", f"Nonblocking warning: {lines} lines within standard warning allowance (401-650 lines) with compliant complexity ({max_cc} <= {policy.max_cognitive_complexity}).", pid, lines, max_cc, has_branch, "warning_standard")

    if lines <= policy.warning_ceiling_declarative:
        if pure_decl and max_cc <= 5:
            return _make_verdict("WARNING", False, True, "MEDIUM", f"Nonblocking warning: {lines} lines of verified declarative structure (zero branch logic, CC <= 5).", pid, lines, max_cc, False, "warning_declarative")
        return _make_verdict("BLOCK", True, False, "HIGH", f"Module has {lines} lines (exceeds 650-line ordinary ceiling) and contains executable branch logic or high CC ({max_cc}). Pure declarative structure required for 651-800 allowance.", pid, lines, max_cc, has_branch, "exceeds_ordinary_warning")

    return _make_verdict("BLOCK", True, False, "HIGH", f"Module has {lines} lines (exceeds maximum unexempted warning ceiling of {policy.warning_ceiling_declarative} lines). Scoped exception or modular refactor required.", pid, lines, max_cc, has_branch, "oversized_unexempted")


def evaluate_module_source(
    code: str,
    rel_path: str,
    policy: Optional[CandidatePolicy] = None,
    exclusions_dict: Optional[Dict[str, Any]] = None,
    baseline_dict: Optional[Dict[str, int]] = None,
    file_path: Optional[Path] = None,
) -> PolicyVerdict:
    active_policy = policy or load_candidate_policy()
    pid = active_policy.policy_id
    lines = (code.count("\n") + (0 if code.endswith("\n") else 1)) if code else 0

    try:
        tree = ast.parse(code, filename=rel_path)
    except Exception as e:
        return _make_verdict("ERROR", True, False, "CRITICAL", f"Parsing error: failed to parse Python AST ({e})", pid, lines, 0, False, "unparseable")

    from tools.file_budget_core.mmi import calculate_ast_metrics_from_source

    metrics = calculate_ast_metrics_from_source(code, tree=tree, filename=rel_path)

    max_cc = metrics.max_cc
    pure_decl = (
        False
        if metrics.has_branch_logic
        else (
            is_ast_pure_declarative(tree)
            if lines > active_policy.warning_ceiling_standard
            else True
        )
    )
    norm_rel = rel_path.replace("\\", "/")

    excl_verdict = _check_exclusion(norm_rel, lines, max_cc, not pure_decl, exclusions_dict, active_policy)
    if excl_verdict:
        return excl_verdict

    base_verdict = _check_baseline(norm_rel, lines, max_cc, not pure_decl, baseline_dict, active_policy)
    if base_verdict:
        return base_verdict

    return _check_boundaries(lines, max_cc, pure_decl, active_policy)


def _load_cached_exclusions(root: Path) -> Optional[Dict[str, Any]]:
    excl_file = root / "config" / "governance" / "file_budget_exclusions.json"
    from .ratchet import read_git_main_json

    main_data = read_git_main_json(excl_file)
    if main_data and isinstance(main_data.get("exclusions"), list):
        return {e["path"].replace("\\", "/"): e for e in main_data["exclusions"] if "path" in e}
    if not excl_file.is_file():
        return None
    try:
        data = json.loads(excl_file.read_text(encoding="utf-8"))
        return {e["path"].replace("\\", "/"): e for e in data.get("exclusions", []) if "path" in e}
    except Exception:
        return None


def _load_cached_baseline(root: Path) -> Optional[Dict[str, int]]:
    reports_base = root / "reports" / "file_budget_burndown_baseline.json"
    config_base = root / "config" / "governance" / "file_budget_burndown_baseline.json"
    base_file = reports_base if reports_base.is_file() else config_base
    from .ratchet import load_ratchet_baseline

    bdata = load_ratchet_baseline(base_file)
    mods = bdata.get("modules")
    return mods if isinstance(mods, dict) else None


def evaluate_module_file(
    file_path: Path,
    repo_root: Optional[Path] = None,
    policy: Optional[CandidatePolicy] = None,
    use_exclusions: bool = True,
    use_baseline: bool = True,
    exclusions_dict: Optional[Dict[str, Any]] = None,
    baseline_dict: Optional[Dict[str, int]] = None,
) -> PolicyVerdict:
    root = repo_root or ROOT
    try:
        rel = str(file_path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        rel = str(file_path).replace("\\", "/")

    if not file_path.is_file():
        return _make_verdict("ERROR", True, False, "CRITICAL", f"File not found: {file_path}", (policy or load_candidate_policy()).policy_id, 0, 0, False, "missing_file")

    code = file_path.read_text(encoding="utf-8", errors="replace")

    if exclusions_dict is None and use_exclusions:
        exclusions_dict = _load_cached_exclusions(root)

    if baseline_dict is None and use_baseline:
        baseline_dict = _load_cached_baseline(root)

    return evaluate_module_source(
        code=code,
        rel_path=rel,
        policy=policy,
        exclusions_dict=exclusions_dict,
        baseline_dict=baseline_dict,
        file_path=file_path,
    )
