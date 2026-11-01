"""Threshold drift and definition duplication scanners for governance harmonization."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dual_pipeline.parameters import (
    get_engineering_output_limits,
    get_float_parameter,
    get_hitl_ambiguity_margin_threshold,
    get_planning_word_limits,
)
from .common import (
    Finding,
    HarmonizationReport,
    read_text_safe,
    relative,
)

_HITL_MARGIN_PCT = f"{int(round(get_hitl_ambiguity_margin_threshold() * 100))}%"
_HITL_MIN_CONFIDENCE_PCT = f"{int(round(get_float_parameter('hitl.min_autonomous_confidence_floor', 0.65) * 100))}%"
_HITL_ENTROPY_CEILING = str(round(get_float_parameter("hitl.entropy_ambiguity_threshold", 0.75), 2))
_HITL_UNRESOLVED_MARGIN_PCT = f"{int(round(get_float_parameter('hitl.unresolved_entity_margin_threshold', 0.15) * 100))}%"
_COLLAPSE_PCT = f"{round(get_float_parameter('output1.single_speaker_collapse_threshold', 0.85) * 100, 1)}"
_RETRIEVAL_LATENCY_MS = str(int(get_float_parameter("timeouts.retrieval_steady_state_latency_ms", 7500.0)))
_RELEASE_PASS_THRESHOLD = str(int(get_float_parameter("release_thresholds.output1_production_pass_threshold", 90.0)))
_ENG_LIMITS = get_engineering_output_limits()
_PLAN_LIMITS = get_planning_word_limits()
_ENG_MAX_WORDS = str(_ENG_LIMITS["max_words"])
_ENG_MAX_CHARS = str(_ENG_LIMITS["max_chars"])
_ENG_PROGRESS_WORDS = str(_ENG_LIMITS["progress_max_words"])
_ENG_PR_QUEUE_MAX_WORDS = str(_ENG_LIMITS["pr_queue_max_words"])
_ENG_SSOT_POINTER_MAX_WORDS = str(_ENG_LIMITS["ssot_pointer_max_words"])
_PLAN_MAX_WORDS = str(_PLAN_LIMITS["max_plan_words"])

# ── Threshold drift scanners ────────────────────────────────────────────
# Each entry: (name, regex_pattern, ssot_file_stem, ssot_value, description)
THRESHOLD_REGISTRY: list[tuple[str, str, str, str, str]] = [
    (
        "HITL Ambiguity Margin",
        r"(?:(?:confidence\s+margin|ambiguity\s+margin|margin\s+gate|margin\s+rule|margin\s+threshold)[^\n\d%]*?(\d{1,2})\s*%|\b(\d{1,2})%\s*(?:confidence\s+margin|ambiguity\s+margin|margin\s+gate|margin\s+threshold|margin\s+rule|ambiguity\s+rule)|(?:Δ|Delta)\s*(?:<|>=|<=|>)\s*(\d{1,2})\s*%|margin\s*(?:Δ|Delta)?\s*<\s*(\d{1,2})\s*%)",
        "dual_pipeline/parameters.py",
        _HITL_MARGIN_PCT,
        "Operator ambiguity margin gating threshold — SSOT in dual_pipeline.parameters (20% inclusive)",
    ),
    (
        "E2E Test Coverage Score",
        r"(?:min[-_. ]e2e[-_. ]score|e2e.*?coverage.*?threshold|e2e.*?(?:dimension.*?score|score)\s*(?:≥|>=|is\s+|must\s+be\s+|at\s+least\s+|threshold:?\s*))[^\n\d%]*?(\d{2,3})\s*%?",
        "AGENTS",
        "80",
        "Minimum E2E test coverage gate score",
    ),
    (
        "Module Size Budget",
        r"(?:module\s+size\s+budget|module\s+budget|module\s+limit)[^\n\d]*?(\d{3,4})\s*lines?|(\d{3,4})[\s-]*line\s*module\s*(?:size\s+)?budget",
        "AGENTS",
        "400",
        "Maximum lines per Python module",
    ),
    (
        "Retrieval Latency Cap",
        r"(?:retrieval.*?latency|latency.*?retrieval|BGE-M3.*?latency|latency.*?BGE-M3|vector.*?latency|latency.*?vector|latency\s+cap|latency\s+ceiling)[^\n\d]*?(\d[,\d]*)\s*ms",
        "AGENTS",
        _RETRIEVAL_LATENCY_MS,
        "Maximum retrieval evaluation latency in milliseconds",
    ),
    (
        "Pytest Timeout",
        r"--timeout[=\s]+(\d+)",
        "AGENTS",
        "60",
        "Mandatory pytest per-test timeout in seconds",
    ),
    (
        "Meeting Slug Max Length",
        r"(?:slug|meeting.*?name)[^\n\d]*?(?:<=?|max)[^\n\d]*?(\d+)\s*char",
        "AGENTS",
        "40",
        "Maximum characters in meeting name slugs",
    ),
    (
        "Single Speaker Collapse",
        r"(?:single-speaker-collapse|speaker(?:\s+skew)?\s+collapse)[^\n\d%]*?(\d+(?:\.\d+)?)\s*%|turn\s+share\s+exceeds?\s*(\d+(?:\.\d+)?)\s*%",
        "AGENTS",
        _COLLAPSE_PCT,
        "Turn share threshold triggering single-speaker-collapse halt",
    ),
    (
        "Bayesian Prior Epsilon",
        r"epsilon\s*=\s*(0\.\d+)",
        "AGENTS",
        "0.05",
        "Bayesian prior clamping epsilon for zero-probability prevention",
    ),
    (
        "Retrieval Gate Count",
        r"(?:all\s+)?\b(\d+)\s*(?:non-compensating\s+)?(?:retrieval|BGE-M3|release\s+eval)\s*gates",
        "AGENTS",
        "11",
        "Number of non-compensating retrieval evaluation gates",
    ),
    (
        "INV-OUTPUT-MAX-WORDS",
        r"(?:INV-OUTPUT-MAX-WORDS[^\n\d]*?(\d+)|(?:hard\s+maximum|maximum|ceiling)[^\n\d]*?(\d+)\s*(?:whitespace-delimited\s+)?words?[^\n]*?INV-OUTPUT-MAX-WORDS|(\d+)\s*words\s*maximum[^\n]*?INV-OUTPUT-MAX-WORDS)",
        "AGENTS",
        _ENG_MAX_WORDS,
        "INV-OUTPUT-MAX-WORDS — Hard maximum words for engineering completion turns",
    ),
    (
        "INV-OUTPUT-MAX-CHARS",
        r"(?:INV-OUTPUT-MAX-CHARS[^\n\d]*?([\d,]+)|(?:hard\s+maximum|maximum|ceiling)[^\n\d]*?([\d,]+)\s*(?:raw\s+)?characters?[^\n]*?INV-OUTPUT-MAX-CHARS|([\d,]+)\s*characters\s*maximum[^\n]*?INV-OUTPUT-MAX-CHARS)",
        "AGENTS",
        _ENG_MAX_CHARS,
        "INV-OUTPUT-MAX-CHARS — Hard maximum characters for engineering completion turns",
    ),
    (
        "INV-OUTPUT-PROGRESS-MAX-WORDS",
        r"(?:INV-OUTPUT-PROGRESS-MAX-WORDS[^\n\d]*?(\d+)|(?:capped\s+at|at\s+most)[^\n\d]*?(\d+)\s*words[^\n]*?INV-OUTPUT-PROGRESS-MAX-WORDS)",
        "AGENTS",
        _ENG_PROGRESS_WORDS,
        "INV-OUTPUT-PROGRESS-MAX-WORDS — Maximum words per progress update",
    ),
    (
        "INV-WAVE-PLAN-MAX-WORDS",
        r"(?:INV-WAVE-PLAN-MAX-WORDS[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?([\d,]+)`?[\s-]*words?|`?([\d,]+)`?[\s-]word\s+base\s+(?:limit|floor)[^\n]*?INV-WAVE-PLAN-MAX-WORDS|INV-WAVE-PLAN-MAX-WORDS[^\n]*?`?([\d,]+)`?[\s-]*word\s+base\s+(?:limit|floor))",
        "AGENTS",
        _PLAN_MAX_WORDS,
        "INV-WAVE-PLAN-MAX-WORDS — Base maximum words for 1-2 wave implementation plans",
    ),
    (
        "INV-AGENTS-MD-MAX-SIZE",
        r"(?:INV-AGENTS-MD-MAX-SIZE[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?([\d,]+)`?\s*bytes|`?([\d,]+)`?\s*bytes[^\n]*?INV-AGENTS-MD-MAX-SIZE)",
        "governance/policy_registry.yaml",
        "12288",
        "INV-AGENTS-MD-MAX-SIZE — Maximum size in bytes for generated AGENTS.md index",
    ),
    (
        "INV-SSOT-LINK-02",
        r"(?:INV-SSOT-LINK-02[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?(?:<=\s*)?(\d+)`?\s*words|`?(?:<=\s*)?(\d+)`?\s*words[^\n]*?INV-SSOT-LINK-02)",
        "governance/policy_registry.yaml",
        _ENG_SSOT_POINTER_MAX_WORDS,
        "INV-SSOT-LINK-02 — Maximum words for SSOT reference pointers in non-owner files",
    ),
    (
        "INV-PR-QUEUE-MAX-WORDS",
        r"(?:INV-PR-QUEUE-MAX-WORDS[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?([\d,]+)`?\s*words|`?([\d,]+)`?\s*words[^\n]*?INV-PR-QUEUE-MAX-WORDS)",
        "governance/policy_registry.yaml",
        _ENG_PR_QUEUE_MAX_WORDS,
        "INV-PR-QUEUE-MAX-WORDS — Maximum words for PR Queue & Branch Summary reports",
    ),
    (
        "INV-HITL-MIN-CONFIDENCE-FLOOR",
        r"(?:INV-HITL-MIN-CONFIDENCE-FLOOR[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?(\d{1,2})\s*%`?|(?:autonomous\s+confidence\s+floor|minimum\s+autonomous\s+confidence\s+floor|autonomous\s+proceed\s+floor)[^\n\d%]*?(\d{1,2})\s*%)",
        "dual_pipeline/parameters.py",
        _HITL_MIN_CONFIDENCE_PCT,
        "INV-HITL-MIN-CONFIDENCE-FLOOR — Minimum top-option confidence required to proceed autonomously",
    ),
    (
        "INV-HITL-ENTROPY-CEILING",
        r"(?:INV-HITL-ENTROPY-CEILING[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?(0\.\d+)`?|(?:entropy\s+escalation\s+ceiling|normalized\s+Shannon\s+entropy\s+ceiling)[^\n\d]*?(0\.\d+))",
        "dual_pipeline/parameters.py",
        _HITL_ENTROPY_CEILING,
        "INV-HITL-ENTROPY-CEILING — Maximum normalized Shannon entropy before forced HITL escalation",
    ),
    (
        "INV-HITL-UNRESOLVED-ENTITY-MARGIN",
        r"(?:INV-HITL-UNRESOLVED-ENTITY-MARGIN[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?(\d{1,2})\s*%`?|(?:unresolved\s+entity\s+margin\s+threshold|entity\s+disambiguation\s+margin\s+threshold)[^\n\d%]*?(\d{1,2})\s*%)",
        "dual_pipeline/parameters.py",
        _HITL_UNRESOLVED_MARGIN_PCT,
        "INV-HITL-UNRESOLVED-ENTITY-MARGIN — Ambiguity margin threshold for unresolved entity disambiguation",
    ),
    (
        "INV-COMPLEXITY-MAX",
        r"(?:INV-COMPLEXITY-MAX[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?(?:<=\s*)?(\d+)`?|(?:max\s+cognitive\s+complexity|cognitive\s+complexity\s+ceiling)\s*(?:\(<=\s*|\bis\s+|:\s*)?(\d+))",
        "governance/policy_registry.yaml",
        "15",
        "INV-COMPLEXITY-MAX — Maximum cognitive complexity per Python function",
    ),
    (
        "INV-GRAPH-MUTATION-PHYSICAL-PROOF",
        r"(?:INV-GRAPH-MUTATION-PHYSICAL-PROOF[^\n|]*?\|\s*(?:[^|\n]*?\|\s*)?`?(\d+)`?\s*(?:physical\s+)?checks?|(\d+)\s+mandatory\s+physical\s+checks)",
        "governance/policy_registry.yaml",
        "5",
        "INV-GRAPH-MUTATION-PHYSICAL-PROOF — Mandatory physical verification checks per graph mutation",
    ),
    (
        "Release Production Pass Threshold",
        r"(?:output[123]_production_pass_threshold|production_pass_threshold)\"?\s*(?:==|=|:)\s*(\d{2,3})\b",
        "config/runtime/parameters.json",
        _RELEASE_PASS_THRESHOLD,
        "Minimum composite score required for production PASS verdict across Outputs 1, 2, and 3",
    ),
]

GRANDFATHERED_15PCT_FILES: frozenset[str] = frozenset(
    {
        "tools/audit_testing_gaps.py",
        "tools/run_graph_scout.py",
        "tools/run_live_adversarial_debate.py",
        "tools/score_qrels_v2.py",
        "tools/validate_turn_gates.py",
        "governance/TIER2_GRAPHDB_CONCEPT_DEDUP_PROMPT.md",
        "governance/decision_intelligence/ADVERSARIAL_EVAL_HARNESS_DEBATE_V1.md",
        "config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md",
        "config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_8.md",
        "config/prompts/testing/REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md",
        "governance/decision_intelligence/SPEAKER_DISAMBIGUATION_PROMPT.md",
    }
)

_STALE_WORD_LIMIT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"<=\s*120\s*words?", re.IGNORECASE),
        "Stale <=120 words ceiling enforcement claim (target is ~120 words, hard ceiling is 300 words per INV-OUTPUT-MAX-WORDS)",
    ),
    (
        re.compile(r"<=\s*1400\s*char", re.IGNORECASE),
        "Stale <=1400 chars ceiling enforcement claim (hard ceiling is 3,500 characters per INV-OUTPUT-MAX-CHARS)",
    ),
    (
        re.compile(r"strictly\s+never\s+exceed[^\n]*?\b120\s*words?", re.IGNORECASE),
        "Stale strictly never exceed 120 words ceiling enforcement claim",
    ),
]


def _scan_stale_word_limit_claims(files: list[Path], report: HarmonizationReport) -> None:
    """Scan governance, tool, and agent files for stale 120-word or 1400-char ceiling enforcement claims."""
    for f in files:
        rel = relative(f).replace("\\", "/")
        if "audit_governance_harmonization" in rel or "governance-harmonizer" in rel:
            continue
        if not (
            rel.startswith((".agents/", "tools/", "governance/"))
            or "/.agents/" in rel
            or "/tools/" in rel
            or "/governance/" in rel
        ):
            continue
        content = read_text_safe(f)
        if not content:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            for pat, desc in _STALE_WORD_LIMIT_PATTERNS:
                if pat.search(line):
                    report.add(
                        Finding(
                            category="threshold_drift",
                            severity="CRITICAL",
                            title="Stale Word-Count Ceiling Enforcement Claim",
                            description=f"{desc}. Found on line {i}: '{line.strip()}'",
                            ssot_file="AGENTS.md",
                            ssot_value="target ~120 words, <=300 words, <=3500 chars",
                            affected_files=[{"path": rel, "line": i, "current_value": line.strip(), "expected_value": "target ~120 words, <=300 words"}],
                            proposed_fix="Update enforcement claim to match SSOT: target ~120 words, <=300 words, <=3500 chars.",
                        )
                    )


def _evaluate_threshold_occurrences(
    name: str,
    pattern: str,
    ssot_val: str,
    file_contents: dict[Path, str],
) -> list[dict[str, Any]]:
    """Scan file contents for occurrences deviating from expected SSOT value."""
    occurrences: list[dict[str, Any]] = []
    norm_ssot = ssot_val.rstrip("%").strip()
    for f, content in file_contents.items():
        if not content:
            continue
        rel_f = relative(f).replace("\\", "/")
        if name == "HITL Ambiguity Margin" and rel_f in GRANDFATHERED_15PCT_FILES:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            matches = re.findall(pattern, line, re.IGNORECASE)
            for m in matches:
                val = next((g for g in m if g), "") if isinstance(m, tuple) else str(m)
                val = val.replace(",", "").strip()
                if not val:
                    continue
                norm_val = val.rstrip("%").strip()
                try:
                    is_drift = float(norm_val) != float(norm_ssot)
                except ValueError:
                    is_drift = (norm_val != norm_ssot)
                if is_drift:
                    occurrences.append(
                        {
                            "path": relative(f),
                            "line": i,
                            "current_value": val,
                            "expected_value": ssot_val,
                        }
                    )
    return occurrences


def scan_threshold_drift(files: list[Path], report: HarmonizationReport) -> None:
    """Detect threshold values that diverge from SSOT."""
    file_contents: dict[Path, str] = {}
    for f in files:
        # Skip scanner itself and harmonizer skill docs to avoid matching baseline quotes
        rel = relative(f)
        if "audit_governance_harmonization" in rel or "governance-harmonizer" in rel:
            continue
        file_contents[f] = read_text_safe(f)

    for name, pattern, ssot_stem, ssot_val, desc in THRESHOLD_REGISTRY:
        occurrences = _evaluate_threshold_occurrences(name, pattern, ssot_val, file_contents)
        if occurrences:
            report.add(
                Finding(
                    category="threshold_drift",
                    severity="CRITICAL" if name in ("HITL Ambiguity Margin", "INV-OUTPUT-MAX-WORDS", "INV-OUTPUT-MAX-CHARS") else "WARNING",
                    title=f"Threshold Drift: {name}",
                    description=f"{desc}. SSOT value is {ssot_val} but divergent values found.",
                    ssot_file=f"{ssot_stem}.md" if not ssot_stem.endswith((".yaml", ".json", ".py", ".md")) else ssot_stem,
                    ssot_value=ssot_val,
                    affected_files=occurrences,
                    proposed_fix=f"Update all occurrences to match SSOT value: {ssot_val}",
                )
            )

    _scan_stale_word_limit_claims(files, report)


# ── Duplication scanner ─────────────────────────────────────────────────
# Known duplicated governance blocks (fingerprint by key phrases)
DUPLICATION_SIGNATURES: list[tuple[str, list[str], str]] = [
    (
        "Ingestion Directive Lifecycle",
        [
            "intake-directive-open",
            "intake-directive-attach",
            "intake-directive-approve",
            "intake-completion-gate",
        ],
        "Ingestion lifecycle is defined verbatim in AGENTS.md, ingestion-lifecycle.md, and nee-ingestion/SKILL.md",
    ),
    (
        "Progress Bar Requirements",
        [
            "tqdm",
            "run_tests_with_progress.py",
            "progress bar",
            "heartbeat",
        ],
        "Progress bar mandate is repeated identically across AGENTS.md, progress-indicators.md, and nee-testing-protocol",
    ),
    (
        "Wave Progression Standard",
        [
            "Wave 1",
            "Wave N+1",
            "wave ledger table",
            "COMPLETE",
            "OPEN",
            "BLOCKED",
            "NOT_STARTED",
        ],
        "Wave standard is duplicated across AGENTS.md, wave-progression.md, and multiple skills",
    ),
    (
        "BGE-M3 Retrieval Requirements",
        [
            "bge-m3",
            "11 gates",
            "non-compensating",
            "7,500ms",
            "synthetic embedding",
        ],
        "BGE-M3 retrieval requirements are repeated across 12+ files",
    ),
    (
        "HITL Atomic Presentation Rules",
        [
            "ask_user_question",
            "atomic presentation",
            "one at a time",
            "calibrated confidence",
            "hitl_decision_ledger",
        ],
        "HITL presentation rules duplicated across AGENTS.md, hitl-decisions.md, and 6+ skills",
    ),
    (
        "Terminal Outcomes Enum",
        [
            "Allowed Terminal Outcomes",
            "intake-completion-gate",
            "intake-completion-noop",
            "COMPLETE_ALREADY_PRESENT",
            "IngestionCompletionGuard",
        ],
        "Terminal outcome definitions repeated across AGENTS.md, ingestion-lifecycle.md, multiple skills",
    ),
    (
        "Stage U0 Direct Ingestion Mandate",
        [
            "nee ingest-meeting",
            "ingest_meeting.sh",
            "Stage U0",
            "custom script",
            "strictly prohibited",
        ],
        "Direct U0 ingestion mandate duplicated across AGENTS.md, transcript-synthesis.md, nee-ingestion, nee-transcript-synthesis",
    ),
]


def scan_duplication(
    files: list[Path],
    report: HarmonizationReport,
    include_pairwise_rules: bool = True,
) -> None:
    """Detect known governance block duplication patterns across human-readable markdown files."""
    file_contents: dict[str, str] = {}
    for f in files:
        rel = relative(f)
        if not rel.endswith(".md") or rel.startswith("automations/prompts/"):
            continue
        content = read_text_safe(f)
        if content:
            file_contents[rel] = content.lower()

    for name, signatures, desc in DUPLICATION_SIGNATURES:
        matching_files: list[dict[str, Any]] = []
        for rel_path, content in file_contents.items():
            if name == "HITL Atomic Presentation Rules" and rel_path == ".agents/rules/hitl-decisions.md":
                continue
            match_count = sum(1 for sig in signatures if sig.lower() in content)
            # Require majority of signatures to match
            if match_count >= len(signatures) * 0.6:
                matching_files.append(
                    {
                        "path": rel_path,
                        "line": None,
                        "current_value": f"{match_count}/{len(signatures)} signatures matched",
                        "expected_value": "Single SSOT with $ref pointers",
                    }
                )

        if len(matching_files) > 2:  # Duplication = same block in 3+ files
            report.add(
                Finding(
                    category="duplication",
                    severity="WARNING",
                    title=f"Definition Duplication: {name}",
                    description=desc,
                    ssot_file="AGENTS.md",
                    ssot_value=f"Canonical definition in {len(matching_files)} files",
                    affected_files=matching_files,
                    proposed_fix=(
                        "Consolidate to single SSOT in AGENTS.md or dedicated rule file; "
                        "downstream skills should reference via 'See AGENTS.md §<section>' "
                        "or 'See .agents/rules/<rule>.md' instead of duplicating."
                    ),
                )
            )

    # Pairwise rule block semantic deduplication (>= 90% similarity)
    if include_pairwise_rules:
        _scan_rule_block_semantic_duplication(files, report)


def _scan_rule_block_semantic_duplication(files: list[Path], report: HarmonizationReport) -> None:
    """Scan rule and skill files for pairwise block duplication exceeding 90% similarity."""
    try:
        from tools.audit_rules_budget import scan_rule_pairwise_duplicates
        rules_map: dict[str, str] = {}
        for f in files:
            rel = relative(f)
            if (rel.startswith(".agents/rules/") or rel.startswith(".agents/skills/") or rel == "AGENTS.md") and rel.endswith(".md"):
                c = read_text_safe(f)
                if c:
                    rules_map[rel] = c
        rule_dups = scan_rule_pairwise_duplicates(rules_map, threshold_pct=90.0)
        for d in rule_dups:
            report.add(
                Finding(
                    category="duplication",
                    severity="WARNING",
                    title=f"Rule Block Duplication >= 90%: {Path(d.file_a).name} vs {Path(d.file_b).name}",
                    description=f"Rule statement matches with {d.similarity_pct}% similarity: '{d.matched_text_preview}'",
                    ssot_file=d.file_a,
                    ssot_value="Single SSOT definition",
                    affected_files=[
                        {"path": d.file_a, "line": None, "current_value": d.matched_text_preview, "expected_value": "Canonical rule"},
                        {"path": d.file_b, "line": None, "current_value": d.matched_text_preview, "expected_value": "Reference pointer"},
                    ],
                    proposed_fix=f"Deduplicate into single SSOT in {d.file_a} and reference from {d.file_b}.",
                )
            )
    except Exception:
        pass

