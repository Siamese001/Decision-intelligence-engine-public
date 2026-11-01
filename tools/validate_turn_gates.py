#!/usr/bin/env python3
"""Mechanical Turn-Gate Validator for NEE Decision Intelligence Engine.

Enforces pre-turn (inventory, classification, hooks, operating contract)
and post-turn (HITL ambiguity, mutation breakdown, hierarchy, scratchpad) gates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REQUIRED_GRAPHDB_TABLES = (
    "node", "source", "source_version", "extraction_run", "evidence_item",
    "assertion", "assertion_evidence", "search_document", "search_fts", "audit_event",
)


def validate_pre_turn_gate(repo_root: Path | None = None) -> dict[str, Any]:
    """Execute all pre-turn gate checks."""
    root = Path(repo_root or ROOT).resolve()
    issues: list[str] = []
    checks: dict[str, Any] = {}

    # Check 1: Runtime evidence discovery
    try:
        from dual_pipeline.runtime_evidence import discover_inventory
        inventory = discover_inventory(root)
        active_pairs = inventory.get("runtime_pairs", [])
        checks["runtime_inventory"] = {"status": "PASS", "active_pairs_count": len(active_pairs), "inventory_digest": inventory.get("inventory_digest", "")}
    except Exception as exc:
        issues.append(f"Runtime evidence discovery failed: {exc}")
        checks["runtime_inventory"] = {"status": "FAIL", "error": str(exc)}

    # Check 2: Classification policy presence
    policy_path = root / "policy" / "pre-intake-classification-policy.json"
    try:
        policy_data = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else {}
        has_rules = bool(policy_data.get("signal_rules") and policy_data.get("domain_rules"))
        checks["classification_policy"] = {"status": "PASS" if has_rules else "FAIL", "rules_count": len(policy_data.get("signal_rules", []))}
        if not has_rules:
            issues.append(f"Pre-intake classification policy missing or incomplete: {policy_path}")
    except Exception as exc:
        issues.append(f"Invalid classification policy JSON: {exc}")
        checks["classification_policy"] = {"status": "FAIL", "error": str(exc)}

    # Check 3: Governance hooks presence and completeness
    hooks_path = root / ".agents" / "hooks.json"
    try:
        hooks_data = json.loads(hooks_path.read_text(encoding="utf-8")) if hooks_path.is_file() else {}
        hook_count = len(hooks_data.get("hooks", []))
        checks["hooks_configuration"] = {"status": "PASS" if hook_count >= 11 else "FAIL", "hook_count": hook_count}
        if hook_count < 11:
            issues.append(f"Expected >= 11 governance hooks in {hooks_path}, found {hook_count}")
    except Exception as exc:
        issues.append(f"Invalid hooks JSON: {exc}")
        checks["hooks_configuration"] = {"status": "FAIL", "error": str(exc)}

    # Check 4: Conformance map integrity
    conformance_path = root / ".agents" / "rules" / "conformance-map.md"
    if not conformance_path.is_file():
        issues.append(f"Conformance map missing: {conformance_path}")
        checks["conformance_map"] = {"status": "FAIL"}
    else:
        conf_text = conformance_path.read_text(encoding="utf-8")
        has_stale = "(AGENTS.md L" in conf_text
        checks["conformance_map"] = {"status": "FAIL" if has_stale else "PASS", "has_stale_headers": has_stale}
        if has_stale:
            issues.append("Conformance map contains stale hardcoded line-number headers.")

    # Check 5: Root agent operating contract & memory bound (<200 lines)
    op_contract = validate_agent_operating_contract(root)
    checks["operating_contract"] = op_contract
    if op_contract["status"] != "PASS":
        issues.extend(op_contract["issues"])

    # Check 6: Workspace skills context budget & governance
    try:
        from tools.audit_skills_budget import audit_skills_budget
        skills_rep = audit_skills_budget(skills_dir=root / ".agents" / "skills")
        checks["skills_budget"] = {"status": "PASS" if skills_rep.is_overall_compliant else "FAIL", "skills_count": skills_rep.total_skills,
                                   "tokens": skills_rep.total_description_tokens, "utilization_pct": skills_rep.budget_utilization_pct}
        if not skills_rep.is_overall_compliant:
            issues.extend(skills_rep.errors)
    except Exception as exc:
        issues.append(f"Skills budget audit failed: {exc}")
        checks["skills_budget"] = {"status": "FAIL", "error": str(exc)}

    return {"gate": "PRE_TURN", "status": "PASS" if not issues else "FAIL", "issues": issues, "checks": checks}


def validate_agent_operating_contract(repo_root: Path | None = None) -> dict[str, Any]:
    """Validate root AGENTS.md concise memory bounds (<200 lines) and structural integrity."""
    root = Path(repo_root or ROOT).resolve()
    issues: list[str] = []
    agents_md = root / "AGENTS.md"
    lines: list[str] = []
    if not agents_md.is_file():
        issues.append("AGENTS.md missing at repository root.")
    else:
        text = agents_md.read_text(encoding="utf-8")
        lines = text.splitlines()
        if len(lines) > 200:
            issues.append(f"AGENTS.md exceeds recommended 200 lines memory bound ({len(lines)} lines).")
        req_headers = [
            "Core Operational Invariants", "Primary Execution Principles", "Wave-Based Execution Gating",
            "Dual-Lock Human Decisions", "Zero-Jargon Layman Communication", "Zero Graph Mutation Invariant",
            "Runtime and retrieval claims are evidence-bound", "Skills, agents, sub-agents, hooks, and rules unified architecture",
        ]
        for header in req_headers:
            if header not in text:
                issues.append(f"AGENTS.md missing required section: '{header}'")
    rules_dir = root / ".agents" / "rules"
    if not rules_dir.is_dir() or not any(rules_dir.glob("*.md")):
        issues.append(".agents/rules/ directory missing or has no markdown rules.")
    skills_dir = root / ".agents" / "skills"
    if not skills_dir.is_dir() or not any(skills_dir.iterdir()):
        issues.append(".agents/skills/ directory missing or empty.")
    return {"status": "PASS" if not issues else "FAIL", "issues": issues, "line_count": len(lines)}


def evaluate_hitl_ambiguity(options: Sequence[dict[str, Any]], margin_threshold: float = 0.15) -> dict[str, Any]:
    """Evaluate candidate HITL options against the 15% ambiguity margin rule."""
    if not options:
        return {"requires_hitl": False, "status": "EMPTY", "top_option": None, "margin": 0.0, "recommendation": "No options evaluated."}
    scored = sorted([(float(opt.get("confidence_score", opt.get("confidence", 0.0))), opt) for opt in options], key=lambda x: x[0], reverse=True)
    top_score, top_opt = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = round(top_score - second_score, 4)
    requires_hitl = margin < margin_threshold
    rec = (f"Margin Δ = {margin:.2%} is less than {margin_threshold:.0%}: surface atomic HITL decision."
           if requires_hitl else f"Margin Δ = {margin:.2%} is >= {margin_threshold:.0%}: proceed autonomously with top option.")
    return {"requires_hitl": requires_hitl, "margin": margin, "margin_threshold": margin_threshold, "top_option": top_opt,
            "second_score": second_score, "top_score": top_score, "action": "SURFACE_HITL_ATOMIC" if requires_hitl else "PROCEED_AUTONOMOUSLY", "recommendation": rec}


def validate_mutation_breakdown_table(markdown_text: str) -> dict[str, Any]:
    """Verify that a response communicating a GraphDB mutation includes the required breakdown table."""
    missing_tables = [t for t in REQUIRED_GRAPHDB_TABLES if f"`{t}`" not in markdown_text and f"| {t} " not in markdown_text and f"|{t}|" not in markdown_text]
    issues = [f"Missing required SQLite tables in mutation breakdown: {missing_tables}"] if missing_tables else []
    has_hash_or_noop = any(k in markdown_text for k in ("graph_sha256", "IDEMPOTENT_NOOP")) or "idempotent" in markdown_text.lower()
    if not has_hash_or_noop:
        issues.append("Missing idempotency or graph_sha256 transition explanation.")
    return {"status": "PASS" if not issues else "FAIL", "missing_tables": missing_tables, "has_hash_transition": has_hash_or_noop, "issues": issues}


def validate_transcript_output_structure(repo_root: Path | None = None) -> dict[str, Any]:
    """Verify that transcript synthesis output directories adhere to output/<project>/<MM-DD-YYYY>/."""
    root = Path(repo_root or ROOT).resolve()
    output_dir = root / "output"
    if not output_dir.is_dir():
        return {"status": "PASS", "message": "No output directory present."}
    issues: list[str] = []
    checked_count = 0
    date_pattern = re.compile(r"^(?:0?[1-9]|1[0-2])[-.](?:0?[1-9]|[12]\d|3[01])[-.](?:19|20)\d{2}(?:\s+\([^)]+\))?$")
    for project_dir in output_dir.iterdir():
        if project_dir.is_symlink() or not project_dir.is_dir() or project_dir.name in {"transcript_cache", ".DS_Store"}:
            continue
        for child in project_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            is_cat = not date_pattern.match(child.name) and any(c.is_dir() and date_pattern.match(c.name) for c in child.iterdir())
            targets = [gc for gc in child.iterdir() if gc.is_dir() and not gc.name.startswith(".")] if is_cat else [child]
            for t in targets:
                checked_count += 1
                if not date_pattern.match(t.name):
                    issues.append(f"Directory '{t.relative_to(root)}' violates MM-DD-YYYY naming convention.")
    cache_dir = output_dir / "transcript_cache"
    if cache_dir.is_dir():
        from tools.validate_output_hierarchy import validate_transcript_cache
        issues.extend(validate_transcript_cache(cache_dir))
    return {"status": "PASS" if not issues else "FAIL", "checked_directories": checked_count, "issues": issues}


def _check_scratchpad_pillars(markdown_text: str) -> tuple[list[str], bool, bool, bool, bool, bool]:
    has_header = any(k in markdown_text for k in ("Agent Reasoning Scratchpad", "### 🧠", "<scratchpad>"))
    has_model = any(k in markdown_text for k in ("Active Model", "Model ID", "Model & Provider", "Provider", "FRONTIER_"))
    has_thinking = any(k in markdown_text for k in ("Cognitive Breakdown", "Thinking Deliberation", "Thinking & Deliberation", "Thinking:"))
    has_steps = "Reasoning Steps" in markdown_text
    has_tools = any(k in markdown_text for k in ("API & Tool Invocations", "Tool Invocations", "API Calls", "Tool:"))

    pillars = [
        (has_header, "Missing mandatory 'Agent Reasoning Scratchpad' header."),
        (has_model, "Missing Pillar 1: Active Model & Provider telemetry."),
        (has_thinking, "Missing Pillar 2: Cognitive Breakdown & Thinking Deliberation."),
        (has_steps, "Missing Pillar 3: Ordered Reasoning Steps."),
        (has_tools, "Missing Pillar 4: API & Tool Invocations audit."),
    ]
    issues = [msg for present, msg in pillars if not present]
    return issues, has_header, has_model, has_thinking, has_steps, has_tools


def _verify_declared_model_identity(declared_raw: str, evidence: dict[str, Any] | None, scope: str) -> list[str]:
    issues: list[str] = []
    if "[Active Model Identifier" in declared_raw or declared_raw.startswith("[e.g."):
        issues.append(f"Model telemetry contains unrendered template placeholder: '{declared_raw}'")
        return issues

    try:
        from dual_pipeline.host_metadata_adapter import resolve_host_identity, verify_identity_evidence
        if evidence is not None:
            is_ok, reason = verify_identity_evidence(declared_raw, evidence, scope=scope)
            if not is_ok:
                issues.append(f"Model identity verification failed: {reason}")
            return issues

        host_report = resolve_host_identity(scope=scope)
        if host_report.status == "UNAVAILABLE":
            if "unavailable" not in declared_raw.lower() and "unverified" not in declared_raw.lower():
                issues.append(
                    f"Model identity '{declared_raw}' is unverified: host metadata is inaccessible. "
                    "Must declare 'Unavailable / Unverified (Host Metadata Inaccessible)'."
                )
        elif host_report.status == "POLICY_MISMATCH":
            if "[POLICY MISMATCH" not in declared_raw:
                issues.append(f"Host model '{host_report.raw_model}' violates approved policy. Must report truthfully as POLICY MISMATCH.")
        else:
            is_ok, reason = verify_identity_evidence(declared_raw, host_report.to_dict(), scope=scope)
            if not is_ok:
                issues.append(f"Model identity verification failed: {reason}")
    except Exception as exc:
        issues.append(f"Model identity verification error: {exc}")
    return issues


def validate_scratchpad_transparency(
    markdown_text: str,
    evidence: dict[str, Any] | None = None,
    scope: str = "host_assistant",
) -> dict[str, Any]:
    """Verify that an agent response includes a compliant Reasoning Scratchpad & Telemetry block."""
    issues, has_header, has_model, has_thinking, has_steps, has_tools = _check_scratchpad_pillars(markdown_text)
    model_match = re.search(r"-\s*\*\*Active Model(?:\s*&\s*Provider)?\*\*:\s*([^\n]+)", markdown_text, re.IGNORECASE)
    if model_match:
        issues.extend(_verify_declared_model_identity(model_match.group(1).strip(), evidence, scope))

    return {
        "status": "PASS" if not issues else "FAIL",
        "has_header": has_header,
        "has_model_telemetry": has_model,
        "has_cognitive_thinking": has_thinking,
        "has_reasoning_steps": has_steps,
        "has_tool_invocations": has_tools,
        "issues": issues,
    }


def _check_candidate_text_presence(
    candidate_response: str | None,
    response_route: str | None,
    require_candidate_text: bool,
) -> tuple[dict[str, Any], list[str]]:
    needs_text = require_candidate_text or (bool(response_route) and response_route in {"engineering", "query", "copilot"})
    has_text = bool(candidate_response and candidate_response.strip())
    if needs_text and not has_text:
        route_label = response_route or "unspecified"
        return {"status": "FAIL", "reason": "Candidate text missing"}, [
            f"Missing candidate response text for route '{route_label}': Post-turn response validation requires explicit candidate text."
        ]
    status = "PASS" if has_text else "PREFLIGHT_ONLY"
    return {"status": status, "evaluated": has_text}, []


def _validate_route_response(
    candidate_response: str,
    response_route: str | None,
    execution_evidence: dict[str, Any] | None,
    plan_only: bool,
    require_scratchpad: bool,
    word_limit: int | None,
) -> tuple[dict[str, Any], list[str]]:
    checks: dict[str, Any] = {}
    issues: list[str] = []
    if response_route == "engineering":
        from tools.engineering_output import Observation, validate_engineering_response
        obs = None
        if execution_evidence and "observation" in execution_evidence:
            raw = execution_evidence["observation"]
            obs = raw if isinstance(raw, Observation) else Observation(**raw)
        eng_eval = validate_engineering_response(candidate_response, observation=obs, plan_only=plan_only)
        checks["engineering_output"] = eng_eval.to_dict()
        issues.extend(eng_eval.errors)
    elif response_route in {"copilot", "query"}:
        from tools.direct_answer_gate import validate_direct_answer
        direct_eval = validate_direct_answer(candidate_response, response_route, word_limit)
        checks["direct_answer"] = direct_eval
        issues.extend(direct_eval["issues"])
    elif require_scratchpad or "Agent Reasoning Scratchpad" in candidate_response:
        scratchpad_eval = validate_scratchpad_transparency(candidate_response, evidence=execution_evidence)
        checks["scratchpad_transparency"] = scratchpad_eval
        issues.extend(scratchpad_eval["issues"])
    return checks, issues


def validate_post_turn_gate(
    repo_root: Path | None = None,
    candidate_response: str | None = None,
    candidate_options: Sequence[dict[str, Any]] | None = None,
    require_scratchpad: bool = False,
    execution_evidence: dict[str, Any] | None = None,
    response_route: str | None = None,
    word_limit: int | None = None,
    require_candidate_text: bool = False,
    plan_only: bool = False,
) -> dict[str, Any]:
    """Execute all post-turn gate checks."""
    root = Path(repo_root or ROOT).resolve()
    issues: list[str] = []
    checks: dict[str, Any] = {}

    presence_check, presence_issues = _check_candidate_text_presence(
        candidate_response, response_route, require_candidate_text
    )
    checks["response_text_presence"] = presence_check
    issues.extend(presence_issues)

    if candidate_options is not None:
        hitl_eval = evaluate_hitl_ambiguity(candidate_options)
        checks["hitl_ambiguity"] = hitl_eval
        if not hitl_eval["requires_hitl"] and candidate_response and "### HITL Decision" in candidate_response:
            issues.append(
                f"Surfaced HITL decision when margin was {hitl_eval['margin']:.2%} (>= 15%). "
                "Must proceed autonomously under recorded receipt."
            )

    if candidate_response and ("GraphDB Mutation" in candidate_response or "graph mutation" in candidate_response.lower()):
        breakdown_eval = validate_mutation_breakdown_table(candidate_response)
        checks["mutation_breakdown"] = breakdown_eval
        issues.extend(breakdown_eval["issues"])

    transcript_eval = validate_transcript_output_structure(root)
    checks["transcript_output_hierarchy"] = transcript_eval
    issues.extend(transcript_eval["issues"])

    if candidate_response:
        route_checks, route_issues = _validate_route_response(
            candidate_response, response_route, execution_evidence, plan_only, require_scratchpad, word_limit
        )
        checks.update(route_checks)
        issues.extend(route_issues)

    overall_status = "PASS" if not issues else "FAIL"
    return {"gate": "POST_TURN", "status": overall_status, "issues": issues, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Pre-Turn and Post-Turn governance gates.")
    parser.add_argument("--mode", choices=["pre-turn", "post-turn", "all"], default="all")
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--response-text", type=str, default=None)
    parser.add_argument("--response-file", type=Path, default=None)
    parser.add_argument("--response-route", type=str, default=None, choices=["engineering", "query", "copilot", "governance", "other"])
    parser.add_argument("--require-candidate-text", action="store_true")
    parser.add_argument("--evidence-file", type=Path, default=None)
    args = parser.parse_args(argv)

    results: dict[str, Any] = {}
    exit_code = 0

    if args.mode in ("pre-turn", "all"):
        pre_result = validate_pre_turn_gate(args.repository_root)
        results["pre_turn"] = pre_result
        if pre_result["status"] != "PASS":
            exit_code = 1

    if args.mode in ("post-turn", "all"):
        c_text = args.response_file.read_text(encoding="utf-8") if args.response_file and args.response_file.is_file() else args.response_text
        ev = json.loads(args.evidence_file.read_text(encoding="utf-8")) if args.evidence_file and args.evidence_file.is_file() else None
        post_result = validate_post_turn_gate(
            repo_root=args.repository_root,
            candidate_response=c_text,
            execution_evidence=ev,
            response_route=args.response_route,
            require_candidate_text=args.require_candidate_text,
        )
        results["post_turn"] = post_result
        if post_result["status"] != "PASS":
            exit_code = 1

    print(json.dumps(results, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
