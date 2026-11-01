"""Term overloading, taxonomy misalignment, and missing reference scanners."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .common import (
    AGENTS_MD,
    Finding,
    HarmonizationReport,
    read_text_safe,
    relative,
)

# ── Term overloading scanner ────────────────────────────────────────────
OVERLOADED_TERMS: list[tuple[str, list[tuple[str, str]], str]] = [
    (
        "wave",
        [
            ("engineering implementation plan phase", r"Wave\s+\d+.*(?:Objective|Scope|Milestone|Deliverable|Acceptance)"),
            ("strategic reasoning stage", r"W[1-7]\s+(?:Context|Criteria|Temporal|Daily|Risk|Strategic|Blueprint)"),
        ],
        "The term 'wave' is overloaded between implementation plan phases (Wave 1-N) and "
        "strategic reasoning stages (W1-W7). Consider disambiguating with 'implementation wave' "
        "vs 'reasoning stage' or 'W-stage'.",
    ),
    (
        "completion gate",
        [
            ("intake directive terminal gate", r"intake.completion.gate|intake-completion-gate"),
            ("wave plan subsection", r"Runtime Receipt.*Completion Gate|Completion Gate.*wave"),
        ],
        "The term 'completion gate' refers to both the intake directive terminal lifecycle "
        "command and the wave plan subsection header. Consider 'intake terminal gate' vs "
        "'wave verification gate'.",
    ),
    (
        "receipt",
        [
            ("wave runtime receipt", r"wave.*runtime\s+receipt|receipt.*wave"),
            ("release receipt", r"release.*receipt|receipt.*release|activation.*receipt"),
            ("PR receipt", r"pr.*receipt|merge.*receipt|push.*receipt"),
            ("ALR cryptographic receipt", r"Ed25519|ALR.*receipt|cryptographic.*receipt"),
        ],
        "The term 'receipt' is used for 14+ distinct receipt taxonomies. Consider a "
        "unified receipt taxonomy document with explicit type prefixes.",
    ),
]


def scan_overloading(files: list[Path], report: HarmonizationReport) -> None:
    """Detect overloaded governance terms used with conflicting semantics."""
    file_contents: dict[str, str] = {}
    for f in files:
        content = read_text_safe(f)
        if content:
            file_contents[relative(f)] = content

    for term, meanings, desc in OVERLOADED_TERMS:
        meaning_files: dict[str, list[str]] = defaultdict(list)
        for rel_path, content in file_contents.items():
            for meaning_label, pattern in meanings:
                if re.search(pattern, content, re.IGNORECASE):
                    meaning_files[meaning_label].append(rel_path)

        # Flag if 2+ meanings each appear in files
        active_meanings = {k: v for k, v in meaning_files.items() if v}
        if len(active_meanings) >= 2:
            affected = []
            for meaning, fpaths in active_meanings.items():
                for fp in fpaths[:3]:  # Cap at 3 examples per meaning
                    affected.append(
                        {
                            "path": fp,
                            "line": None,
                            "current_value": f"Uses '{term}' as: {meaning}",
                            "expected_value": "Disambiguated term",
                        }
                    )
            report.add(
                Finding(
                    category="overloading",
                    severity="INFO",
                    title=f"Term Overloading: '{term}'",
                    description=desc,
                    ssot_file=".agents/rules/receipt-taxonomies.md" if term == "receipt" else "(multiple)",
                    ssot_value=f"{len(active_meanings)} distinct semantic meanings",
                    affected_files=affected,
                    proposed_fix=desc.split(". Consider")[-1].strip() if ". Consider" in desc else "",
                )
            )


# ── Taxonomy misalignment scanner ───────────────────────────────────────
TAXONOMY_CHECKS: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    (
        "Intake Stage Labels",
        "AGENTS.md",
        "U0 (Ingress) -> L1 (Identity) -> L0 (Routing)",
        [
            (".agents/skills/nee-ingestion/SKILL.md", r"\|\s*L1\s*\|\s*Normalization|\|\s*L0\s*\|\s*Identity"),
        ],
    ),
    (
        "Wave Plan Mandatory Subsections",
        "AGENTS.md",
        "4 subsections: Objective & Scope, Milestones & Deliverables, Acceptance Criteria, Runtime Receipt & Completion Gate",
        [
            (".agents/rules/wave-progression.md", "3 subsections|omits.*Objective"),
        ],
    ),
    (
        "Temporal Representation SSOT Alignment",
        ".agents/rules/temporal-representation-ssot.md",
        "Tier 1: ISO-8601 for DB/telemetry; Tier 2: MM/DD/YYYY for projections (observed_at, stale_after); Tier 3: MM-DD-YYYY for directories",
        [
            (".agents/rules/temporal-representation-ssot.md", r"observed_at.*ISO-8601"),
        ],
    ),
]


def scan_taxonomy_alignment(files: list[Path], report: HarmonizationReport) -> None:
    """Detect taxonomy misalignment between SSOT and downstream files."""
    file_contents: dict[str, str] = {}
    for f in files:
        content = read_text_safe(f)
        if content:
            file_contents[relative(f)] = content

    for name, ssot_file, ssot_desc, checks in TAXONOMY_CHECKS:
        affected = []
        for check_path, check_pattern in checks:
            # Find matching file in scanned set
            for rel_path, content in file_contents.items():
                if (check_path in rel_path or rel_path.endswith(check_path.lstrip("."))) and re.search(
                    check_pattern, content, re.IGNORECASE | re.DOTALL
                ):
                    affected.append(
                        {
                            "path": rel_path,
                            "line": None,
                            "current_value": "Diverges from SSOT",
                            "expected_value": ssot_desc,
                        }
                    )

        if affected:
            report.add(
                Finding(
                    category="taxonomy",
                    severity="CRITICAL" if "Temporal" in name else "WARNING",
                    title=f"Taxonomy Misalignment: {name}",
                    description=f"Downstream files diverge from SSOT ({ssot_file}): {ssot_desc}",
                    ssot_file=ssot_file,
                    ssot_value=ssot_desc,
                    affected_files=affected,
                    proposed_fix=f"Align all downstream files with {ssot_file} definition: {ssot_desc}",
                )
            )


# ── Missing back-reference scanner ──────────────────────────────────────
EXPECTED_AGENTS_MD_CONCEPTS: list[tuple[str, str]] = [
    ("learning-change-proposal", r"learning.change.proposal"),
    ("ParameterRegistry", r"ParameterRegistry"),
    ("graph_scout", r"graph.scout"),
]


def scan_missing_backrefs(files: list[Path], report: HarmonizationReport) -> None:
    """Detect concepts defined in rules/skills but absent from AGENTS.md."""
    agents_content = read_text_safe(AGENTS_MD) if AGENTS_MD.exists() else ""

    for concept_name, pattern in EXPECTED_AGENTS_MD_CONCEPTS:
        if not re.search(pattern, agents_content, re.IGNORECASE):
            # Concept is missing from AGENTS.md — find where it IS defined
            defined_in = []
            for f in files:
                if f == AGENTS_MD:
                    continue
                content = read_text_safe(f)
                if re.search(pattern, content, re.IGNORECASE):
                    defined_in.append(
                        {
                            "path": relative(f),
                            "line": None,
                            "current_value": f"Defines '{concept_name}'",
                            "expected_value": "Back-referenced in AGENTS.md",
                        }
                    )

            if defined_in:
                report.add(
                    Finding(
                        category="missing_backref",
                        severity="INFO",
                        title=f"Missing AGENTS.md Reference: {concept_name}",
                        description=(
                            f"The concept '{concept_name}' is defined in {len(defined_in)} "
                            f"downstream file(s) but has no mention in AGENTS.md."
                        ),
                        ssot_file="AGENTS.md",
                        ssot_value="(absent)",
                        affected_files=defined_in,
                        proposed_fix=(
                            f"Add '{concept_name}' to the appropriate AGENTS.md section, "
                            f"or add a conformance-map entry."
                        ),
                    )
                )


# ── Enumeration completeness scanner ────────────────────────────────────
def scan_unenumerated_receipts(files: list[Path], report: HarmonizationReport) -> None:
    """Detect references to 'isolated 4 receipts' without explicit enumeration."""
    for f in files:
        content = read_text_safe(f)
        if not content:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if re.search(r"isolated\s+4\s+receipts?|(?<!wave\s)\b4\s+receipts\b", line, re.IGNORECASE):
                # Check if subsequent lines enumerate them
                context = "\n".join(content.splitlines()[max(0, i - 1) : i + 5])
                if not re.search(
                    r"(?:receipt_\w+|\w+_receipt\.json|\w+_evaluation\.json|1\.\s|2\.\s|3\.\s|4\.\s|qa_gates_report|qa,\s*judge)",
                    context,
                ):
                    report.add(
                        Finding(
                            category="missing_backref",
                            severity="WARNING",
                            title="Unenumerated Receipt Set: 'isolated 4 receipts'",
                            description=(
                                "References 'isolated 4 receipts' but does not explicitly "
                                "enumerate the 4 receipt filenames."
                            ),
                            ssot_file=relative(f),
                            ssot_value="4 receipts (unenumerated)",
                            affected_files=[
                                {
                                    "path": relative(f),
                                    "line": i,
                                    "current_value": line.strip()[:120],
                                    "expected_value": "Explicit list of 4 receipt filenames",
                                }
                            ],
                            proposed_fix="Enumerate the 4 receipt filenames inline or reference a canonical list.",
                        )
                    )


# ── Policy registry synchronization scanner ────────────────────────────
def _validate_registered_policy_paths(
    registered_policies: dict[str, dict[str, Any]],
    report: HarmonizationReport,
) -> None:
    from .common import REPO_ROOT

    for pid, policy in registered_policies.items():
        enf = policy.get("enforcer")
        rul = policy.get("rule_reference")
        missing_paths = []
        if not enf or not (REPO_ROOT / enf).exists():
            missing_paths.append(f"enforcer: {enf}")
        if not rul or not (REPO_ROOT / rul).exists():
            missing_paths.append(f"rule_reference: {rul}")
        if missing_paths:
            report.add(
                Finding(
                    category="policy_sync",
                    severity="CRITICAL",
                    title=f"Invalid Policy Registry Reference: {pid}",
                    description=f"Policy {pid} declares non-existent path(s): {', '.join(missing_paths)}",
                    ssot_file="governance/policy_registry.yaml",
                    ssot_value=f"enforcer: {enf}, rule_reference: {rul}",
                    proposed_fix=f"Correct paths for policy {pid} in governance/policy_registry.yaml",
                )
            )


def _resolve_dotted(mapping: dict[str, Any], dotted_key: str) -> tuple[bool, Any]:
    cur: Any = mapping
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= 1e-6
    return str(a) == str(b)


def _check_runtime_param_parity(
    pid: str,
    policy: dict[str, Any],
    params: dict[str, Any],
    report: HarmonizationReport,
) -> None:
    rp = policy.get("runtime_parameter")
    if not rp or "->" not in str(rp):
        return
    dotted_key = str(rp).split("->", 1)[1].strip()
    found, actual = _resolve_dotted(params, dotted_key)
    expected = policy.get("threshold")
    if not found or not _values_equal(expected, actual):
        report.add(
            Finding(
                category="policy_sync",
                severity="CRITICAL",
                title=f"Policy Registry vs Runtime Parameter Drift: {pid}",
                description=(
                    f"Policy {pid} threshold ({expected}) diverges from runtime parameter "
                    f"'{dotted_key}' ({actual if found else 'MISSING'}) in config/runtime/parameters.json."
                ),
                ssot_file="config/runtime/parameters.json",
                ssot_value=str(actual if found else "MISSING"),
                affected_files=[{"path": "governance/policy_registry.yaml", "line": None, "current_value": str(expected), "expected_value": str(actual)}],
                proposed_fix=f"Align {pid} threshold in governance/policy_registry.yaml with config/runtime/parameters.json ({dotted_key}).",
            )
        )


def _check_pyproject_parity(
    pid: str,
    policy: dict[str, Any],
    pyproj: dict[str, Any],
    report: HarmonizationReport,
) -> None:
    pb = policy.get("pyproject_binding")
    if not pb:
        return
    dotted_key = str(pb).strip()
    found, actual = _resolve_dotted(pyproj, dotted_key)
    expected = policy.get("threshold")
    if not found or not _values_equal(expected, actual):
        report.add(
            Finding(
                category="policy_sync",
                severity="CRITICAL",
                title=f"Policy Registry vs pyproject.toml Drift: {pid}",
                description=(
                    f"Policy {pid} threshold ({expected}) diverges from pyproject.toml binding "
                    f"'{dotted_key}' ({actual if found else 'MISSING'})."
                ),
                ssot_file="governance/policy_registry.yaml",
                ssot_value=str(expected),
                affected_files=[{"path": "pyproject.toml", "line": None, "current_value": str(actual if found else "MISSING"), "expected_value": str(expected)}],
                proposed_fix=f"Align {dotted_key} in pyproject.toml with {pid} threshold ({expected}).",
            )
        )


def _validate_three_way_value_parity(
    registered_policies: dict[str, dict[str, Any]],
    report: HarmonizationReport,
    repo_root: Path | None = None,
) -> None:
    import tomllib
    from .common import REPO_ROOT, load_json_with_dup_check

    root = repo_root or REPO_ROOT
    params_path = root / "config" / "runtime" / "parameters.json"
    pyproj_path = root / "pyproject.toml"
    params: dict[str, Any] = {}
    pyproj: dict[str, Any] = {}
    if params_path.exists():
        try:
            raw_p, _ = load_json_with_dup_check(params_path)
            params = raw_p.get("parameters", raw_p) if isinstance(raw_p, dict) else {}
        except Exception:
            params = {}
    if pyproj_path.exists():
        try:
            pyproj = tomllib.loads(read_text_safe(pyproj_path))
        except Exception:
            pyproj = {}

    for pid, policy in registered_policies.items():
        _check_runtime_param_parity(pid, policy, params, report)
        _check_pyproject_parity(pid, policy, pyproj, report)


def _is_governed_inv_surface(f: Path) -> bool:
    s = str(f).replace("\\", "/")
    if any(p in s for p in ("/.agents/rules/", "/.agents/skills/", "/config/prompts/")):
        return True
    return s.endswith("/AGENTS.md") or s.endswith("/.agents/hooks.json")


def _scan_unregistered_inv_tokens(
    files: list[Path],
    registered_policies: dict[str, dict[str, Any]],
    report: HarmonizationReport,
) -> None:
    inv_pattern = re.compile(r"\b(INV-[A-Z0-9]+(?:-[A-Z0-9]+)*)(?!\*)\b")
    rule_or_skill_files = [f for f in files if _is_governed_inv_surface(f)]

    unregistered_tokens: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in rule_or_skill_files:
        content = read_text_safe(f)
        if not content:
            continue
        rel = relative(f)
        for line_no, line in enumerate(content.splitlines(), 1):
            for match in inv_pattern.finditer(line):
                token = match.group(1)
                if token not in registered_policies:
                    unregistered_tokens[token].append(
                        {
                            "path": rel,
                            "line": line_no,
                            "current_value": token,
                            "expected_value": "Registered in governance/policy_registry.yaml",
                        }
                    )

    for token, occurrences in unregistered_tokens.items():
        report.add(
            Finding(
                category="policy_sync",
                severity="CRITICAL",
                title=f"Unregistered Policy Invariant: {token}",
                description=(
                    f"The invariant token '{token}' appears in {len(occurrences)} location(s) "
                    "across rules/skills/prompts/hooks/AGENTS.md but is not registered in governance/policy_registry.yaml."
                ),
                ssot_file="governance/policy_registry.yaml",
                ssot_value="(unregistered)",
                affected_files=occurrences[:5],
                proposed_fix=f"Register '{token}' in governance/policy_registry.yaml with valid enforcer and rule_reference.",
            )
        )


def scan_policy_registry_sync(
    files: list[Path],
    report: HarmonizationReport,
    repo_root: Path | None = None,
) -> None:
    """Verify all INV-* tokens and 3-way threshold bindings in policy_registry.yaml."""
    import yaml
    from .common import GOVERNANCE_DIR

    root_gov = (repo_root / "governance") if repo_root else GOVERNANCE_DIR
    registry_path = root_gov / "policy_registry.yaml"
    if not registry_path.exists():
        report.add(
            Finding(
                category="policy_sync",
                severity="CRITICAL",
                title="Missing Policy Registry",
                description="governance/policy_registry.yaml not found on disk.",
                ssot_file="governance/policy_registry.yaml",
                ssot_value="(missing)",
            )
        )
        return

    try:
        data = yaml.safe_load(read_text_safe(registry_path)) or {}
    except Exception as exc:
        report.add(
            Finding(
                category="policy_sync",
                severity="CRITICAL",
                title="Corrupted Policy Registry YAML",
                description=f"Failed to parse governance/policy_registry.yaml: {exc}",
                ssot_file="governance/policy_registry.yaml",
                ssot_value="(syntax_error)",
            )
        )
        return

    registered_policies = {p["id"]: p for p in data.get("policies", []) if "id" in p}
    _validate_registered_policy_paths(registered_policies, report)
    _validate_three_way_value_parity(registered_policies, report, repo_root=repo_root)
    _scan_unregistered_inv_tokens(files, registered_policies, report)
