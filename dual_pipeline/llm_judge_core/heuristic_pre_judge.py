"""
dual_pipeline/llm_judge_core/heuristic_pre_judge.py

Deterministic Heuristic Pre-Judge (Agent 4: Micro-Judge & Tier-Arbitrage Specialist).
Implements tiered fail-fast checks (AST structure, JSON schema, vector floor)
prior to frontier LLM micro-judge dispatch, cutting wasted judge tokens by >= 40%
on failing graph candidate iterations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_JUDGE_TOKENS_AVERTED = 12_000


@dataclass
class Tier1Violation:
    code: str
    message: str
    dimension: str


@dataclass
class Tier1GateReceipt:
    passed: bool
    pre_check_tier: str
    violations: List[Tier1Violation] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    tokens_spent: int = 0
    tokens_saved_estimate: int = DEFAULT_JUDGE_TOKENS_AVERTED
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        d["status"] = "PASS" if self.passed else "FAIL"
        d["verdict"] = "PASS" if self.passed else "TIER_1_BLOCKED"
        return d


def _extract_json_snippet(raw_text: str) -> Optional[str]:
    """Extract first valid JSON object or array substring from raw or markdown text."""
    if not raw_text or not raw_text.strip():
        return None
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
    if fence_match:
        return fence_match.group(1).strip()
    stripped = raw_text.strip()
    if (stripped.startswith("[") and stripped.endswith("]")) or (stripped.startswith("{") and stripped.endswith("}")):
        return stripped
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw_text[start : end + 1]
    return None


def check_json_syntax(f3_text: str) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
    """Tier 1: Verify JSON parseability and extract candidate dict."""
    snippet = _extract_json_snippet(f3_text)
    if not snippet:
        return False, None, ["Malformed graph payload: No JSON object found in candidate."]
    try:
        parsed = json.loads(snippet)
        if not isinstance(parsed, dict):
            return False, None, ["Malformed graph payload: Root JSON must be an object/dict."]
        return True, parsed, []
    except json.JSONDecodeError as exc:
        return False, None, [f"JSON syntax error: {exc.msg} at line {exc.lineno}"]


def check_schema_entities(parsed: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Tier 1: Verify presence of graph entities, assertions, or topology keys."""
    errors: List[str] = []
    # Recognized entity/claim container keys
    topology_keys = {"nodes", "entities", "claims", "assertions", "graph", "relations", "edges", "graph_mutations"}
    found_keys = [k for k in topology_keys if k in parsed]

    if not found_keys:
        errors.append("Schema error: Candidate lacks essential graph keys ('nodes', 'entities', 'claims', etc.).")
        return False, errors

    # Check for completely empty collections
    total_elements = 0
    for k in found_keys:
        val = parsed.get(k)
        if isinstance(val, dict):
            for sub_val in val.values():
                if isinstance(sub_val, (list, dict)):
                    total_elements += len(sub_val)
                elif sub_val is not None:
                    total_elements += 1
        elif isinstance(val, list):
            total_elements += len(val)

    if total_elements == 0:
        errors.append("Schema error: Graph containers are completely empty (0 entities/claims).")
        return False, errors

    return True, []


def _validate_node_ids(nodes: List[Any]) -> Tuple[Set[str], List[str]]:
    node_ids: Set[str] = set()
    errors: List[str] = []
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"AST error: Node at index {idx} is not an object.")
            continue
        nid = str(node.get("id") or node.get("entity_id") or node.get("name") or "")
        if not nid:
            errors.append(f"AST error: Node at index {idx} is missing required 'id' or 'name'.")
        else:
            node_ids.add(nid)
    return node_ids, errors


def _count_dangling_edges(edges: List[Any], valid_ids: Set[str]) -> int:
    dangling = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src = str(
            edge.get("from_id")
            or edge.get("source")
            or edge.get("src")
            or edge.get("source_id")
            or edge.get("from")
            or ""
        )
        dst = str(
            edge.get("to_id")
            or edge.get("target")
            or edge.get("dst")
            or edge.get("target_id")
            or edge.get("to")
            or ""
        )
        if (src and src not in valid_ids) or (dst and dst not in valid_ids):
            dangling += 1
    return dangling


def _extract_ast_nodes_and_edges(parsed: Dict[str, Any]) -> Tuple[List[Any], List[Any]]:
    nodes: List[Any] = []
    edges: List[Any] = []
    gm = parsed.get("graph_mutations") if isinstance(parsed.get("graph_mutations"), dict) else {}

    for k in ("nodes", "entities"):
        val = parsed.get(k)
        if isinstance(val, list):
            nodes.extend(val)
    for k in ("entity_upserts", "entities", "nodes"):
        val = gm.get(k)
        if isinstance(val, list):
            nodes.extend(val)

    for k in ("edges", "relations"):
        val = parsed.get(k)
        if isinstance(val, list):
            edges.extend(val)
    for k in ("relationship_signals", "signals", "relations", "edges"):
        val = gm.get(k)
        if isinstance(val, list):
            edges.extend(val)

    return nodes, edges


def check_ast_topology(parsed: Dict[str, Any], f1_text: str = "") -> Tuple[bool, List[str]]:
    """Tier 2: Verify entity node structure and identify dangling edges."""
    errors: List[str] = []
    nodes, edges = _extract_ast_nodes_and_edges(parsed)

    if not nodes:
        if edges:
            errors.append(f"AST error: Candidate contains {len(edges)} edges but 0 entity nodes.")
            return False, errors
        if f1_text and len(f1_text.strip()) >= 500:
            errors.append("AST error: Candidate contains 0 entities and 0 relationship edges for a substantive transcript.")
            return False, errors
        return True, errors

    node_ids, node_errors = _validate_node_ids(nodes)
    errors.extend(node_errors)

    if edges and node_ids:
        dangling = _count_dangling_edges(edges, node_ids)
        if len(edges) > 0 and (dangling / len(edges)) > 0.5:
            errors.append(f"AST error: High ratio of dangling edges ({dangling}/{len(edges)} unlinked).")

    return len(errors) == 0, errors


def check_grounding_floor(
    parsed: Dict[str, Any],
    f1_text: str,
    floor_threshold: float = 0.10,
) -> Tuple[bool, float, List[str]]:
    """Tier 3: Heuristic vector/grounding floor check against source transcript."""
    if not f1_text or not f1_text.strip():
        return True, 1.0, []

    f1_words = set(re.findall(r"\b[A-Za-z0-9_-]{3,}\b", f1_text.lower()))
    if not f1_words:
        return True, 1.0, []

    candidate_str = json.dumps(parsed).lower()
    candidate_words = set(re.findall(r"\b[A-Za-z0-9_-]{3,}\b", candidate_str))
    # Exclude common schema words
    common_schema = {"nodes", "edges", "entities", "claims", "type", "description", "id", "name", "true", "false"}
    candidate_content_words = candidate_words - common_schema

    if not candidate_content_words:
        return True, 1.0, []

    overlap = candidate_content_words.intersection(f1_words)
    score = len(overlap) / len(candidate_content_words)

    if score < floor_threshold:
        return (
            False,
            score,
            [f"Grounding floor failure: Lexical overlap {score:.1%} < minimum floor {floor_threshold:.1%}."],
        )
    return True, score, []


def evaluate_heuristic_pre_judge(
    f3_text: str,
    f1_text: str = "",
    floor_threshold: float = 0.10,
) -> Dict[str, Any]:
    """Execute tiered heuristic fail-fast checks before frontier LLM judge dispatch."""
    t0 = time.perf_counter()

    # 1. Syntax check
    v_syntax, parsed, err_syntax = check_json_syntax(f3_text)
    if not v_syntax or parsed is None:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 0.0,
            "passed_pre_check": False,
            "is_heuristic_pre_check_only": True,
            "pre_judge_tier": "TIER_1_SYNTAX_FAIL",
            "errors": err_syntax,
            "defect_codes": ["ERR_HEURISTIC_SYNTAX"],
            "actionable_remediation_diff": "Format Output 3 as a valid, parseable JSON object.",
            "dimension_1_strategic_edge": 0.0,
            "dimension_2_knowledge_layer_density": 0.0,
            "dimension_3_semantic_atomicity": 0.0,
            "dimension_4_predicate_specificity": 0.0,
            "dimension_5_exclusion_of_action_items": 0.0,
            "dimension_6_evidence_grounding_and_schema": 0.0,
            "dimension_1_typed_entity_extraction": 0.0,
            "dimension_2_ontological_predicates": 0.0,
            "dimension_3_cross_source_grounding": 0.0,
            "dimension_4_temporal_validity": 0.0,
            "dimension_scores": {"syntax": 0.0, "schema": 0.0, "topology": 0.0, "grounding": 0.0},
            "elapsed_ms": elapsed_ms,
            "tokens_saved_estimate": DEFAULT_JUDGE_TOKENS_AVERTED,
        }

    # 2. Schema check
    v_schema, err_schema = check_schema_entities(parsed)
    if not v_schema:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 10.0,
            "passed_pre_check": False,
            "is_heuristic_pre_check_only": True,
            "pre_judge_tier": "TIER_1_SCHEMA_FAIL",
            "errors": err_schema,
            "defect_codes": ["ERR_HEURISTIC_SCHEMA"],
            "actionable_remediation_diff": "Include non-empty entity, claim, or relation containers in graph mutations.",
            "dimension_1_strategic_edge": 2.0,
            "dimension_2_knowledge_layer_density": 2.0,
            "dimension_3_semantic_atomicity": 2.0,
            "dimension_4_predicate_specificity": 1.0,
            "dimension_5_exclusion_of_action_items": 1.0,
            "dimension_6_evidence_grounding_and_schema": 2.0,
            "dimension_1_typed_entity_extraction": 2.0,
            "dimension_2_ontological_predicates": 2.0,
            "dimension_3_cross_source_grounding": 2.0,
            "dimension_4_temporal_validity": 2.0,
            "dimension_scores": {"syntax": 100.0, "schema": 0.0, "topology": 0.0, "grounding": 0.0},
            "elapsed_ms": elapsed_ms,
            "tokens_saved_estimate": DEFAULT_JUDGE_TOKENS_AVERTED,
        }

    # 3. AST topology check
    v_ast, err_ast = check_ast_topology(parsed, f1_text=f1_text)
    if not v_ast:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 25.0,
            "passed_pre_check": False,
            "is_heuristic_pre_check_only": True,
            "pre_judge_tier": "TIER_2_AST_FAIL",
            "errors": err_ast,
            "defect_codes": ["ERR_HEURISTIC_AST_TOPOLOGY"],
            "actionable_remediation_diff": "Ensure all entity nodes have valid IDs and link relations to defined endpoints.",
            "dimension_1_strategic_edge": 5.0,
            "dimension_2_knowledge_layer_density": 5.0,
            "dimension_3_semantic_atomicity": 5.0,
            "dimension_4_predicate_specificity": 3.0,
            "dimension_5_exclusion_of_action_items": 2.0,
            "dimension_6_evidence_grounding_and_schema": 5.0,
            "dimension_1_typed_entity_extraction": 5.0,
            "dimension_2_ontological_predicates": 5.0,
            "dimension_3_cross_source_grounding": 5.0,
            "dimension_4_temporal_validity": 5.0,
            "dimension_scores": {"syntax": 100.0, "schema": 80.0, "topology": 20.0, "grounding": 0.0},
            "elapsed_ms": elapsed_ms,
            "tokens_saved_estimate": DEFAULT_JUDGE_TOKENS_AVERTED,
        }

    # 4. Grounding floor check
    v_ground, ground_score, err_ground = check_grounding_floor(parsed, f1_text, floor_threshold=floor_threshold)
    if not v_ground:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 30.0,
            "passed_pre_check": False,
            "is_heuristic_pre_check_only": True,
            "pre_judge_tier": "TIER_3_GROUNDING_FLOOR_FAIL",
            "errors": err_ground,
            "defect_codes": ["ERR_HEURISTIC_GROUNDING_FLOOR"],
            "actionable_remediation_diff": "Ground graph entity names and descriptions directly in forensic transcript content.",
            "dimension_1_strategic_edge": 6.0,
            "dimension_2_knowledge_layer_density": 6.0,
            "dimension_3_semantic_atomicity": 6.0,
            "dimension_4_predicate_specificity": 4.0,
            "dimension_5_exclusion_of_action_items": 2.0,
            "dimension_6_evidence_grounding_and_schema": 6.0,
            "dimension_1_typed_entity_extraction": 6.0,
            "dimension_2_ontological_predicates": 6.0,
            "dimension_3_cross_source_grounding": 6.0,
            "dimension_4_temporal_validity": 6.0,
            "dimension_scores": {
                "syntax": 100.0,
                "schema": 100.0,
                "topology": 100.0,
                "grounding": round(ground_score * 100.0, 1),
            },
            "elapsed_ms": elapsed_ms,
            "tokens_saved_estimate": DEFAULT_JUDGE_TOKENS_AVERTED,
        }

    # All heuristic checks passed
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": 100.0,
        "passed_pre_check": True,
        "is_heuristic_pre_check_only": True,
        "pre_judge_tier": "PASS_HEURISTIC_PRE_CHECK",
        "errors": [],
        "defect_codes": [],
        "dimension_1_strategic_edge": 20.0,
        "dimension_2_knowledge_layer_density": 20.0,
        "dimension_3_semantic_atomicity": 20.0,
        "dimension_4_predicate_specificity": 10.0,
        "dimension_5_exclusion_of_action_items": 10.0,
        "dimension_6_evidence_grounding_and_schema": 20.0,
        "dimension_1_typed_entity_extraction": 20.0,
        "dimension_2_ontological_predicates": 20.0,
        "dimension_3_cross_source_grounding": 20.0,
        "dimension_4_temporal_validity": 20.0,
        "dimension_scores": {
            "syntax": 100.0,
            "schema": 100.0,
            "topology": 100.0,
            "grounding": round(ground_score * 100.0, 1),
        },
        "elapsed_ms": elapsed_ms,
        "tokens_saved_estimate": 0,
    }


def run_tier1_deterministic_pre_judge(
    target_artifact: str,
    content: str,
    reference_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Tier1GateReceipt:
    """Execute Tier 1 deterministic gate across Outputs 01, 02, and 03 prior to LLM dispatch.

    Enforces zero-token fail-fast verification on structural, schema, and format invariants.
    """
    t0 = time.perf_counter()
    meta = metadata or {}
    norm_key = target_artifact.lower().strip()
    violations: List[Tier1Violation] = []
    diagnostics: Dict[str, Any] = {}
    if not content or not content.strip():
        violations.append(
            Tier1Violation(
                code="ERR_TIER1_EMPTY_CONTENT",
                message=f"Candidate {target_artifact} content is empty or blank.",
                dimension="content_completeness",
            )
        )
        return Tier1GateReceipt(
            passed=False,
            pre_check_tier="TIER1_EMPTY_BLOCKED",
            violations=violations,
            diagnostics={"empty_content": True},
            tokens_spent=0,
            tokens_saved_estimate=DEFAULT_JUDGE_TOKENS_AVERTED,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        )

    if norm_key in ("output_1", "output_01", "01", "1", "forensic", "transcript"):
        from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
        gate1_res = run_qa_gate_1(content, source_text=reference_text, metadata=meta)
        passed = bool(gate1_res.get("passed", False))
        diagnostics["gate1"] = gate1_res
        for fail_msg in gate1_res.get("failures", []):
            violations.append(
                Tier1Violation(
                    code="ERR_TIER1_FORENSIC_STRUCTURAL",
                    message=str(fail_msg),
                    dimension="structural_and_hygiene",
                )
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return Tier1GateReceipt(
            passed=passed,
            pre_check_tier="TIER1_FORENSIC_PASS" if passed else "TIER1_FORENSIC_BLOCKED",
            violations=violations,
            diagnostics=diagnostics,
            tokens_spent=0,
            tokens_saved_estimate=DEFAULT_JUDGE_TOKENS_AVERTED if not passed else 0,
            elapsed_ms=elapsed_ms,
        )

    elif norm_key in ("output_2", "output_02", "02", "2", "recap", "executive_recap"):
        from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
        gate2_res = run_qa_gate_2(content, meta=meta)
        passed = bool(gate2_res.get("passed", False)) and len(gate2_res.get("failures", [])) == 0
        diagnostics["gate2"] = gate2_res
        for fail_msg in gate2_res.get("failures", []):
            violations.append(
                Tier1Violation(
                    code="ERR_TIER1_RECAP_STRUCTURAL",
                    message=str(fail_msg),
                    dimension="recap_structure_and_bounds",
                )
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return Tier1GateReceipt(
            passed=passed,
            pre_check_tier="TIER1_RECAP_PASS" if passed else "TIER1_RECAP_BLOCKED",
            violations=violations,
            diagnostics=diagnostics,
            tokens_spent=0,
            tokens_saved_estimate=DEFAULT_JUDGE_TOKENS_AVERTED if not passed else 0,
            elapsed_ms=elapsed_ms,
        )

    elif norm_key in ("output_3", "output_03", "03", "3", "graph", "canonical_signal_projection"):
        from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
        pre_res = evaluate_heuristic_pre_judge(content, reference_text)
        gate3_res = run_qa_gate_3(content, f1_text=reference_text, metadata=meta)
        pre_passed = bool(pre_res.get("passed_pre_check", True))
        gate3_passed = bool(gate3_res.get("passed", False)) and len(gate3_res.get("failures", [])) == 0
        passed = pre_passed and gate3_passed
        diagnostics["heuristic_pre_judge"] = pre_res
        diagnostics["gate3"] = gate3_res

        for err in pre_res.get("errors", []):
            violations.append(
                Tier1Violation(
                    code=pre_res.get("defect_codes", ["ERR_TIER1_GRAPH_SYNTAX"])[0],
                    message=str(err),
                    dimension="graph_ast_and_syntax",
                )
            )
        for fail_msg in gate3_res.get("failures", []):
            violations.append(
                Tier1Violation(
                    code="ERR_TIER1_GRAPH_SCHEMA",
                    message=str(fail_msg),
                    dimension="graph_schema_and_integrity",
                )
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return Tier1GateReceipt(
            passed=passed,
            pre_check_tier="TIER1_GRAPH_PASS" if passed else "TIER1_GRAPH_BLOCKED",
            violations=violations,
            diagnostics=diagnostics,
            tokens_spent=0,
            tokens_saved_estimate=DEFAULT_JUDGE_TOKENS_AVERTED if not passed else 0,
            elapsed_ms=elapsed_ms,
        )
    else:
        raise ValueError(f"Unknown target deliverable for Tier 1 pre-judge: {target_artifact}")
