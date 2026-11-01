#!/usr/bin/env python3
"""Fail-closed NEE proposal, inline-output, approval, and packaging gate.

The utility uses only the Python standard library. It validates canonical JSON
proposal digests, the exact three-table inline decision surface, approval
binding, baseline hashes, approved path scope, non-policy card and graph
immutability, conformance status, and gated ZIP creation.

It does not infer semantic equivalence. A material difference requires a new
proposal and approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


class GateError(RuntimeError):
    """Raised when a governance gate must fail closed."""


INLINE_SECTION_HEADINGS: Tuple[str, ...] = (
    "## 1. Graph Delta and Retrieval Impact",
    "## 2. Mandatory Graph Metrics",
    "## 3. Card Additions and Retrieval Impact",
)

GRAPH_TABLE_HEADERS: Tuple[str, ...] = (
    "Graph component",
    "Baseline",
    "Proposed / actual",
    "Delta",
    "What changed",
    "Retrieval rationale",
)

METRIC_TABLE_HEADERS: Tuple[str, ...] = (
    "Metric",
    "Result",
    "Pass threshold",
    "Status",
    "Comment / required improvement",
)

CARD_TABLE_HEADERS: Tuple[str, ...] = (
    "Card",
    "Addition",
    "Change type",
    "Defense / evidence",
    "Retrieval improvement",
)

METRIC_SPECS: Tuple[Tuple[str, str], ...] = (
    ("Operational orphan rate", "≤5% and no increase"),
    ("Duplicate node count", "0 new high-impact duplicates"),
    ("Duplicate edge rate", "≤1% unexplained and no increase"),
    ("Rare edge-type rate", "≤25% or no increase"),
    ("Claim completeness", "100%"),
    ("Source metadata completeness", "≥95%; 100% for material sources"),
    ("Temporal completeness", "≥90% and no decrease"),
    ("High-impact identity collisions", "0 unresolved"),
    ("P0/P1 SLA breach rate", "0%"),
    ("Decision-safe answer regression", "0 regression on approved test set"),
)

REQUIRED_GRAPH_COMPONENT_KEYS = {
    "source",
    "node",
    "alias",
    "edge",
    "claim",
    "validation",
    "node_card_map",
}

ZERO_GRAPH_RETRIEVAL_RATIONALE = "NA"

NON_SPECIFIC_RETRIEVAL_RATIONALES = {
    "improves retrieval",
    "better retrieval",
    "n/a",
    "na",
    "not applicable",
}


ALLOWED_CARD_CHANGE_TYPES = {
    "body",
    "frontmatter",
    "route",
    "body+route",
    "frontmatter+route",
    "body+frontmatter",
    "body+frontmatter+route",
    "control_policy",
    "no_change",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def proposal_digest(value: Mapping[str, Any]) -> str:
    normalized = dict(value)
    normalized.pop("canonical_serialization_sha256", None)
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateError(f"Required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GateError(f"Expected a JSON object in {path}")
    return value


def verify_l1_to_change_set_reconciliation(proposal: Mapping[str, Any]) -> None:
    """Verify that any planned positive graph operations have corresponding deltas or reason codes."""
    l1_planned_ops = proposal.get("l1_planned_operations")
    if not isinstance(l1_planned_ops, list) or not l1_planned_ops:
        return

    has_planned_positive = any(
        isinstance(op, dict) and op.get("expected_operation") in ("create_entity", "add_relationship", "add_assertion")
        for op in l1_planned_ops
    )
    if not has_planned_positive:
        return

    delta_rows = proposal.get("graph_delta_rows", [])
    has_nonzero_delta = any(_delta_is_nonzero(row.get("delta")) for row in delta_rows if isinstance(row, dict))

    if not has_nonzero_delta:
        zero_op_reason = proposal.get("zero_op_resolution_reason") or proposal.get("extensions", {}).get("zero_op_resolution_reason")
        valid_reasons = {
            "EXACT_DUPLICATE_IDENTICAL_DIGEST",
            "CONFLICT_QUARANTINED",
            "RETRACTED_BY_SOURCE",
            "UNRESOLVED_ENTITY_HITL_REQUIRED",
            "NO_NOVEL_FACTS_AFTER_HYDRATION",
        }
        if zero_op_reason not in valid_reasons:
            raise GateError(
                "GATE_DISCREPANCY_UNEXPLAINED_ZERO_OP: L1 planned positive operations but graph delta is zero "
                f"without an authorized zero_op_resolution_reason ({sorted(valid_reasons)})"
            )


def verify_proposal_file(path: Path) -> Tuple[Dict[str, Any], str]:
    proposal = read_json(path)
    declared = proposal.get("canonical_serialization_sha256")
    actual = proposal_digest(proposal)
    if not declared:
        raise GateError(f"Proposal has no canonical_serialization_sha256: {path}")
    if declared != actual:
        raise GateError(
            f"Proposal digest mismatch for {path}: declared={declared}, actual={actual}"
        )
    verify_l1_to_change_set_reconciliation(proposal)
    return proposal, actual


def _required_text(row: Mapping[str, Any], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GateError(f"{label} has no non-empty {key}")
    return value.strip()


def _require_rows(value: Any, label: str) -> List[Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise GateError(f"{label} must be a non-empty list")
    rows: List[Mapping[str, Any]] = []
    for index, row in enumerate(value, start=1):
        if not isinstance(row, dict):
            raise GateError(f"{label} row {index} is not an object")
        rows.append(row)
    return rows


def _parse_integer_vector(value: str) -> List[int] | None:
    parts = [part.strip().replace(",", "") for part in value.split("/")]
    if not parts or not all(re.fullmatch(r"[+-]?\d+", part) for part in parts):
        return None
    return [int(part) for part in parts]


def _delta_is_nonzero(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        parsed = _parse_integer_vector(value)
        if parsed is not None:
            return any(item != 0 for item in parsed)
        return value.strip() not in {"", "0", "+0", "0.0", "+0.0"}
    return False


def _validate_graph_delta_rows(
    rows_value: Any,
    component_counts: Any,
) -> List[Mapping[str, Any]]:
    rows = _require_rows(rows_value, "graph_delta_rows")
    by_key: Dict[str, Mapping[str, Any]] = {}

    for index, row in enumerate(rows, start=1):
        label = f"graph_delta_rows row {index}"
        component_key = _required_text(row, "component_key", label)
        if component_key in by_key:
            raise GateError(f"Duplicate graph component key: {component_key}")
        by_key[component_key] = row

        _required_text(row, "graph_component", label)
        baseline = _required_text(row, "baseline", label)
        after = _required_text(row, "proposed_or_actual", label)
        delta = _required_text(row, "delta", label)
        _required_text(row, "change", label)
        rationale = _required_text(row, "retrieval_rationale", label)
        if _delta_is_nonzero(delta) and rationale.lower() in NON_SPECIFIC_RETRIEVAL_RATIONALES:
            raise GateError(
                f"{label} has a non-zero delta and requires a mechanism-specific "
                "retrieval_rationale; NA/N/A is allowed only for an all-zero delta"
            )

        baseline_values = _parse_integer_vector(baseline)
        after_values = _parse_integer_vector(after)
        delta_values = _parse_integer_vector(delta)
        if (
            baseline_values is not None
            and after_values is not None
            and delta_values is not None
        ):
            if not (
                len(baseline_values) == len(after_values) == len(delta_values)
            ):
                raise GateError(f"{label} count-vector lengths do not match")
            calculated = [after_values[i] - baseline_values[i] for i in range(len(after_values))]
            if calculated != delta_values:
                raise GateError(
                    f"{label} arithmetic mismatch: expected delta {calculated}, rendered {delta_values}"
                )

    missing_required = sorted(REQUIRED_GRAPH_COMPONENT_KEYS - set(by_key))
    if missing_required:
        raise GateError(
            "Graph delta table is missing required components: "
            + ", ".join(missing_required)
        )

    if isinstance(component_counts, dict):
        for component_key, count_value in component_counts.items():
            if isinstance(count_value, dict):
                before = count_value.get("before")
                after = count_value.get("after")
                delta = count_value.get("delta")
                if component_key not in by_key:
                    if _delta_is_nonzero(delta):
                        raise GateError(
                            f"Non-zero graph component is omitted from inline output: {component_key}"
                        )
                    continue
                row = by_key[component_key]
                expected = {
                    "baseline": str(before),
                    "proposed_or_actual": str(after),
                    "delta": str(delta),
                }
                for field, expected_value in expected.items():
                    if expected_value != "None" and str(row.get(field)).strip() != expected_value:
                        raise GateError(
                            f"Graph row {component_key} does not reconcile to component_counts.{field}: "
                            f"expected={expected_value!r}, actual={row.get(field)!r}"
                        )
            elif _delta_is_nonzero(count_value) and component_key not in by_key:
                raise GateError(
                    f"Non-zero graph component is omitted from inline output: {component_key}"
                )

    return rows


def _validate_metric_rows(rows_value: Any) -> List[Mapping[str, Any]]:
    rows = _require_rows(rows_value, "graph_metric_rows")
    if len(rows) != len(METRIC_SPECS):
        raise GateError(
            f"Graph metric table must contain exactly {len(METRIC_SPECS)} rows; found {len(rows)}"
        )

    for index, ((expected_metric, expected_threshold), row) in enumerate(
        zip(METRIC_SPECS, rows), start=1
    ):
        label = f"graph_metric_rows row {index}"
        metric = _required_text(row, "metric", label)
        result = _required_text(row, "result", label)
        threshold = _required_text(row, "pass_threshold", label)
        status = _required_text(row, "status", label).upper()
        comment = _required_text(row, "comment", label)

        if metric != expected_metric:
            raise GateError(
                f"Metric row {index} is out of order or renamed: expected={expected_metric!r}, actual={metric!r}"
            )
        if threshold != expected_threshold:
            raise GateError(
                f"Metric threshold changed for {metric}: expected={expected_threshold!r}, actual={threshold!r}"
            )
        if " → " not in result or not re.search(r"\([^()]+\)\s*$", result):
            raise GateError(
                f"Metric result must use 'before → after (delta)' for {metric}: {result!r}"
            )
        if status not in {"PASS", "FAIL", "N/A", "EXCEPTION"}:
            raise GateError(f"Invalid metric status for {metric}: {status}")
        if status == "FAIL":
            raise GateError(f"Graph metric failed and blocks the gate: {metric}: {comment}")
        if status == "N/A" and "reason" not in comment.lower():
            raise GateError(f"N/A metric requires a written reason: {metric}")
        if status == "EXCEPTION":
            exception_id = row.get("preapproved_exception_id")
            if not isinstance(exception_id, str) or not exception_id.strip():
                raise GateError(
                    f"Metric exception requires preapproved_exception_id: {metric}"
                )

    return rows


def _extract_expected_card_ids(
    graph_proposal: Mapping[str, Any] | None,
    card_proposal: Mapping[str, Any] | None,
    conformance: Mapping[str, Any] | None,
) -> set[str]:
    expected: set[str] = set()

    if card_proposal:
        cards = card_proposal.get("cards")
        if isinstance(cards, list):
            for item in cards:
                if isinstance(item, dict) and isinstance(item.get("card_id"), str):
                    expected.add(item["card_id"])
        exception = card_proposal.get("control_plane_exception")
        if isinstance(exception, dict) and isinstance(exception.get("card_id"), str):
            expected.add(exception["card_id"])

    if graph_proposal:
        operations = graph_proposal.get("operations")
        if isinstance(operations, list):
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                routes = operation.get("card_routes")
                if isinstance(routes, list):
                    for route in routes:
                        if isinstance(route, str):
                            expected.add(route)
                        elif isinstance(route, dict):
                            for key in ("card_id", "route", "target_card_id"):
                                if isinstance(route.get(key), str):
                                    expected.add(route[key])
                                    break
                if operation.get("object_type") == "node_card_map":
                    after = operation.get("proposed_after")
                    if isinstance(after, dict) and isinstance(after.get("card_id"), str):
                        expected.add(after["card_id"])

    if conformance:
        affected = conformance.get("affected_card_ids")
        if isinstance(affected, list):
            expected.update(str(item) for item in affected)

    return {item for item in expected if item}


def _validate_card_rows(
    rows_value: Any,
    expected_card_ids: set[str],
) -> List[Mapping[str, Any]]:
    rows = _require_rows(rows_value, "card_rows")
    rendered_ids: set[str] = set()

    for index, row in enumerate(rows, start=1):
        label = f"card_rows row {index}"
        card_id = _required_text(row, "card", label)
        if card_id in rendered_ids:
            raise GateError(f"Duplicate card row: {card_id}")
        rendered_ids.add(card_id)
        _required_text(row, "addition", label)
        change_type = _required_text(row, "change_type", label)
        _required_text(row, "defense_or_evidence", label)
        retrieval = _required_text(row, "retrieval_improvement", label)
        if change_type not in ALLOWED_CARD_CHANGE_TYPES:
            raise GateError(f"Unsupported card change_type for {card_id}: {change_type}")
        if retrieval.lower() in {"improves retrieval", "better retrieval", "n/a"}:
            raise GateError(
                f"{label} retrieval_improvement is too generic; state the retrieval mechanism"
            )

    no_change_labels = {"No affected cards", "None"}
    if expected_card_ids:
        missing = sorted(expected_card_ids - rendered_ids)
        extra = sorted(rendered_ids - expected_card_ids)
        if missing or extra:
            raise GateError(
                f"Card table does not reconcile to affected cards: missing={missing}, extra={extra}"
            )
    else:
        if len(rows) != 1 or next(iter(rendered_ids)) not in no_change_labels:
            raise GateError(
                "A zero-card-impact gate must contain exactly one 'No affected cards' row"
            )
        row = rows[0]
        if row.get("change_type") != "no_change":
            raise GateError("Zero-card-impact row must use change_type 'no_change'")

    return rows


def _inline_payload(
    stage: str,
    graph_proposal: Mapping[str, Any] | None = None,
    card_proposal: Mapping[str, Any] | None = None,
    conformance: Mapping[str, Any] | None = None,
) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    stage = stage.upper()
    if stage == "G1":
        if graph_proposal is None or card_proposal is None:
            raise GateError("G1 inline output requires graph and card proposals")
        graph_inline = graph_proposal.get("inline_gate_output")
        card_inline = card_proposal.get("inline_gate_output")
        if not isinstance(graph_inline, dict):
            raise GateError("Graph proposal has no inline_gate_output object")
        if not isinstance(card_inline, dict):
            raise GateError("Card proposal has no inline_gate_output object")
        graph_rows = _validate_graph_delta_rows(
            graph_inline.get("graph_delta_rows"),
            graph_proposal.get("component_counts"),
        )
        metric_rows = _validate_metric_rows(graph_inline.get("graph_metric_rows"))
        expected_cards = _extract_expected_card_ids(graph_proposal, card_proposal, None)
        card_rows = _validate_card_rows(card_inline.get("card_rows"), expected_cards)
        return graph_rows, metric_rows, card_rows

    if stage == "R1":
        if conformance is None:
            raise GateError("R1 inline output requires a conformance record")
        inline = conformance.get("inline_gate_output")
        if not isinstance(inline, dict):
            raise GateError("Conformance record has no inline_gate_output object")
        graph_rows = _validate_graph_delta_rows(
            inline.get("graph_delta_rows"),
            conformance.get("component_counts"),
        )
        metric_rows = _validate_metric_rows(inline.get("graph_metric_rows"))
        expected_cards = _extract_expected_card_ids(None, None, conformance)
        card_rows = _validate_card_rows(inline.get("card_rows"), expected_cards)
        return graph_rows, metric_rows, card_rows

    raise GateError(f"Unsupported inline-output stage: {stage}")


def _graph_retrieval_rationale_for_render(row: Mapping[str, Any]) -> str:
    if not _delta_is_nonzero(row.get("delta")):
        return ZERO_GRAPH_RETRIEVAL_RATIONALE
    return str(row.get("retrieval_rationale", "")).strip()


def _escape_markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def _render_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    header_line = "| " + " | ".join(_escape_markdown_cell(item) for item in headers) + " |"
    separator = "|" + "|".join("---" for _ in headers) + "|"
    body = [
        "| " + " | ".join(_escape_markdown_cell(item) for item in row) + " |"
        for row in rows
    ]
    return "\n".join([header_line, separator, *body])


def render_inline_output(
    stage: str,
    graph_proposal: Mapping[str, Any] | None = None,
    card_proposal: Mapping[str, Any] | None = None,
    conformance: Mapping[str, Any] | None = None,
) -> str:
    graph_rows, metric_rows, card_rows = _inline_payload(
        stage,
        graph_proposal=graph_proposal,
        card_proposal=card_proposal,
        conformance=conformance,
    )

    graph_table = _render_table(
        GRAPH_TABLE_HEADERS,
        (
            (
                row["graph_component"],
                row["baseline"],
                row["proposed_or_actual"],
                row["delta"],
                row["change"],
                _graph_retrieval_rationale_for_render(row),
            )
            for row in graph_rows
        ),
    )
    metric_table = _render_table(
        METRIC_TABLE_HEADERS,
        (
            (
                row["metric"],
                row["result"],
                row["pass_threshold"],
                str(row["status"]).upper(),
                row["comment"],
            )
            for row in metric_rows
        ),
    )
    card_table = _render_table(
        CARD_TABLE_HEADERS,
        (
            (
                row["card"],
                row["addition"],
                row["change_type"],
                row["defense_or_evidence"],
                row["retrieval_improvement"],
            )
            for row in card_rows
        ),
    )

    return (
        f"{INLINE_SECTION_HEADINGS[0]}\n\n{graph_table}\n\n"
        f"{INLINE_SECTION_HEADINGS[1]}\n\n{metric_table}\n\n"
        f"{INLINE_SECTION_HEADINGS[2]}\n\n{card_table}\n"
    )


def validate_inline_output_file(
    inline_output_path: Path,
    stage: str,
    graph_proposal: Mapping[str, Any] | None = None,
    card_proposal: Mapping[str, Any] | None = None,
    conformance: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    try:
        actual = inline_output_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except FileNotFoundError as exc:
        raise GateError(f"Inline gate output is missing: {inline_output_path}") from exc

    expected = render_inline_output(
        stage,
        graph_proposal=graph_proposal,
        card_proposal=card_proposal,
        conformance=conformance,
    )
    if actual != expected:
        actual_lines = actual.splitlines()
        expected_lines = expected.splitlines()
        mismatch = 1
        for mismatch, (actual_line, expected_line) in enumerate(
            zip(actual_lines, expected_lines), start=1
        ):
            if actual_line != expected_line:
                break
        else:
            mismatch = min(len(actual_lines), len(expected_lines)) + 1
        raise GateError(
            "Inline gate output is not the exact generated three-table decision surface; "
            f"first mismatch at line {mismatch}"
        )

    return {
        "status": "pass",
        "stage": stage.upper(),
        "inline_output_sha256": sha256_file(inline_output_path),
        "table_count": 3,
        "metric_rows": len(METRIC_SPECS),
    }


def inventory_tree(root: Path) -> Dict[str, Dict[str, Any]]:
    if not root.is_dir():
        raise GateError(f"Tree does not exist: {root}")
    result: Dict[str, Dict[str, Any]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel_path = path.relative_to(root)
        if "__pycache__" in rel_path.parts or path.suffix == ".pyc":
            continue
        rel = rel_path.as_posix()
        result[rel] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return result


def compare_trees(
    baseline_dir: Path,
    work_dir: Path,
) -> Dict[str, List[str]]:
    baseline = inventory_tree(baseline_dir)
    work = inventory_tree(work_dir)
    baseline_paths = set(baseline)
    work_paths = set(work)
    return {
        "added": sorted(work_paths - baseline_paths),
        "removed": sorted(baseline_paths - work_paths),
        "changed": sorted(
            path
            for path in baseline_paths & work_paths
            if baseline[path]["sha256"] != work[path]["sha256"]
        ),
        "unchanged": sorted(
            path
            for path in baseline_paths & work_paths
            if baseline[path]["sha256"] == work[path]["sha256"]
        ),
    }


def _approval_proposal_digest(
    approval: Mapping[str, Any],
    key: str,
) -> str:
    value = approval.get(key)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        digest = value.get("canonical_serialization_sha256")
        if isinstance(digest, str):
            return digest
    raise GateError(f"Approval record has no valid {key} digest")


def _inline_gate_required(approval: Mapping[str, Any]) -> bool:
    return approval.get("inline_output_gate_required") is True


def _approval_inline_digest(approval: Mapping[str, Any]) -> str:
    value = approval.get("g1_inline_output_sha256")
    if isinstance(value, str) and value:
        return value
    inline = approval.get("inline_output_gate")
    if isinstance(inline, dict) and isinstance(inline.get("g1_sha256"), str):
        return inline["g1_sha256"]
    raise GateError("Approval record has no G1 inline-output SHA-256")


def validate_approval(
    approval_path: Path,
    baseline_zip: Path,
    graph_proposal_path: Path,
    card_proposal_path: Path,
    g1_inline_output_path: Path | None = None,
) -> Dict[str, Any]:
    approval = read_json(approval_path)
    status = approval.get("status")
    if status not in {"approved", "approved_active"}:
        raise GateError(f"Approval status is not active: {status!r}")

    origin = approval.get("operator_origin") or approval.get("approval_origin") or approval.get("source")
    if origin:
        from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
        is_auth, origin_msg = validate_approval_origin(origin)
        if not is_auth:
            raise GateError(f"Approval origin rejected: {origin_msg}")

    expected_gen = approval.get("expected_base_generation")
    actual_gen = approval.get("actual_base_generation")
    if expected_gen and actual_gen and str(expected_gen) != str(actual_gen):
        raise GateError(f"Stale base generation: expected {expected_gen}, but active is {actual_gen}")

    expected_baseline = (
        approval.get("baseline_zip", {}).get("sha256")
        if isinstance(approval.get("baseline_zip"), dict)
        else approval.get("baseline_zip_sha256")
    )
    if not isinstance(expected_baseline, str):
        raise GateError("Approval record has no baseline ZIP SHA-256")
    actual_baseline = sha256_file(baseline_zip)
    if actual_baseline != expected_baseline:
        raise GateError(
            "Baseline ZIP mismatch: "
            f"approved={expected_baseline}, actual={actual_baseline}"
        )

    graph_proposal, graph_digest = verify_proposal_file(graph_proposal_path)
    card_proposal, card_digest = verify_proposal_file(card_proposal_path)
    approved_graph = _approval_proposal_digest(approval, "graph_proposal")
    approved_card = _approval_proposal_digest(approval, "card_proposal")

    if graph_digest != approved_graph:
        raise GateError(
            f"Graph proposal is not approved: approved={approved_graph}, actual={graph_digest}"
        )
    if card_digest != approved_card:
        raise GateError(
            f"Card proposal is not approved: approved={approved_card}, actual={card_digest}"
        )
    if not approval.get("approval_precedes_implementation", False):
        raise GateError("Approval chronology is not established")

    inline_digest: str | None = None
    if _inline_gate_required(approval):
        if g1_inline_output_path is None:
            raise GateError("G1 inline output is required by the approval record")
        inline_result = validate_inline_output_file(
            g1_inline_output_path,
            "G1",
            graph_proposal=graph_proposal,
            card_proposal=card_proposal,
        )
        inline_digest = inline_result["inline_output_sha256"]
        approved_inline = _approval_inline_digest(approval)
        if inline_digest != approved_inline:
            raise GateError(
                "G1 inline output is not approval-bound: "
                f"approved={approved_inline}, actual={inline_digest}"
            )

    return {
        "status": "pass",
        "decision_id": approval.get("decision_id"),
        "baseline_zip_sha256": actual_baseline,
        "graph_proposal_sha256": graph_digest,
        "card_proposal_sha256": card_digest,
        "g1_inline_output_sha256": inline_digest,
    }


def approved_paths(approval: Mapping[str, Any]) -> List[str]:
    direct = approval.get("approved_file_paths")
    if isinstance(direct, list):
        return [str(x) for x in direct]
    scope = approval.get("approved_scope")
    if isinstance(scope, dict) and isinstance(scope.get("approved_file_paths"), list):
        return [str(x) for x in scope["approved_file_paths"]]
    raise GateError("Approval record has no approved_file_paths")


def verify_scope(
    approval_path: Path,
    baseline_dir: Path,
    work_dir: Path,
    graph_path: str = "graph/nee_graph.sqlite",
    policy_card_path: str = "cards/NEE_99_Project_Instructions.md",
) -> Dict[str, Any]:
    approval = read_json(approval_path)
    allowed = set(approved_paths(approval))
    diff = compare_trees(baseline_dir, work_dir)
    actual_changed = set(diff["added"] + diff["removed"] + diff["changed"])
    unapproved = sorted(actual_changed - allowed)
    if unapproved:
        raise GateError(f"Unapproved file mutations: {unapproved}")

    zero_graph = (
        approval.get("approved_scope", {}).get("account_graph_delta") == "zero"
        if isinstance(approval.get("approved_scope"), dict)
        else False
    )
    if zero_graph:
        base_graph = baseline_dir / graph_path
        work_graph = work_dir / graph_path
        if not base_graph.is_file() or not work_graph.is_file():
            raise GateError("Graph file missing during zero-delta verification")
        if sha256_file(base_graph) != sha256_file(work_graph):
            raise GateError("Graph changed despite approved zero graph delta")
        migration_added = [
            p
            for p in diff["added"] + diff["changed"]
            if p.startswith("graph/migrations/")
        ]
        if migration_added:
            raise GateError(
                f"Graph migration changed despite zero graph delta: {migration_added}"
            )

    zero_non_policy_cards = (
        approval.get("approved_scope", {}).get("non_policy_card_delta") == "zero"
        if isinstance(approval.get("approved_scope"), dict)
        else False
    )
    non_policy_card_changes: List[str] = []
    if zero_non_policy_cards:
        for path in diff["added"] + diff["removed"] + diff["changed"]:
            if path.startswith("cards/") and path != policy_card_path:
                non_policy_card_changes.append(path)
        if non_policy_card_changes:
            raise GateError(
                "Non-policy cards changed despite approved zero card delta: "
                f"{sorted(non_policy_card_changes)}"
            )

    return {
        "status": "pass",
        "changed_files": sorted(actual_changed),
        "unapproved_files": [],
        "graph_zero_delta_verified": zero_graph,
        "non_policy_card_zero_delta_verified": zero_non_policy_cards,
        "diff": diff,
    }


def validate_conformance(
    conformance_path: Path,
    approval_path: Path,
    graph_proposal_path: Path | None = None,
    card_proposal_path: Path | None = None,
    g1_inline_output_path: Path | None = None,
    r1_inline_output_path: Path | None = None,
) -> Dict[str, Any]:
    conformance = read_json(conformance_path)
    approval = read_json(approval_path)
    if conformance.get("status") != "pass":
        raise GateError("Conformance status is not pass")
    if conformance.get("approval_decision_id") != approval.get("decision_id"):
        raise GateError("Conformance record is bound to a different approval")
    if conformance.get("unapproved_semantic_changes"):
        raise GateError("Conformance record lists unapproved semantic changes")
    if conformance.get("drift_result") not in {"none", "no_drift"}:
        raise GateError("Conformance record does not prove zero drift")
    if conformance.get("packaging_gate_result") != "pass":
        raise GateError("Packaging gate did not pass")

    if _inline_gate_required(approval):
        if graph_proposal_path is None or card_proposal_path is None:
            raise GateError("Inline-output conformance requires graph and card proposals")
        if g1_inline_output_path is None or r1_inline_output_path is None:
            raise GateError("Both G1 and R1 inline outputs are required for packaging")
        graph_proposal, _ = verify_proposal_file(graph_proposal_path)
        card_proposal, _ = verify_proposal_file(card_proposal_path)
        g1_result = validate_inline_output_file(
            g1_inline_output_path,
            "G1",
            graph_proposal=graph_proposal,
            card_proposal=card_proposal,
        )
        r1_result = validate_inline_output_file(
            r1_inline_output_path,
            "R1",
            conformance=conformance,
        )
        if conformance.get("inline_output_gate_result") != "pass":
            raise GateError("Conformance record does not pass the inline-output gate")
        if conformance.get("g1_inline_output_sha256") != g1_result["inline_output_sha256"]:
            raise GateError("Conformance record G1 inline-output hash mismatch")
        if conformance.get("r1_inline_output_sha256") != r1_result["inline_output_sha256"]:
            raise GateError("Conformance record R1 inline-output hash mismatch")

    return conformance


def create_deterministic_zip(source_dir: Path, output_zip: Path) -> str:
    if not source_dir.is_dir():
        raise GateError(f"Source directory does not exist: {source_dir}")
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    fixed_time = (2026, 7, 11, 0, 0, 0)
    root_name = source_dir.name
    with zipfile.ZipFile(
        output_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
            rel_path = path.relative_to(source_dir)
            if "__pycache__" in rel_path.parts or path.suffix == ".pyc":
                continue
            rel = Path(root_name) / rel_path
            info = zipfile.ZipInfo(rel.as_posix(), date_time=fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())
    return sha256_file(output_zip)


def gated_package(
    approval_path: Path,
    baseline_zip: Path,
    baseline_dir: Path,
    graph_proposal_path: Path,
    card_proposal_path: Path,
    conformance_path: Path,
    source_dir: Path,
    output_zip: Path,
    g1_inline_output_path: Path | None = None,
    r1_inline_output_path: Path | None = None,
) -> Dict[str, Any]:
    approval_result = validate_approval(
        approval_path,
        baseline_zip,
        graph_proposal_path,
        card_proposal_path,
        g1_inline_output_path,
    )
    validate_conformance(
        conformance_path,
        approval_path,
        graph_proposal_path=graph_proposal_path,
        card_proposal_path=card_proposal_path,
        g1_inline_output_path=g1_inline_output_path,
        r1_inline_output_path=r1_inline_output_path,
    )
    verify_scope(approval_path, baseline_dir, source_dir)
    zip_sha = create_deterministic_zip(source_dir, output_zip)
    return {
        "status": "pass",
        "decision_id": approval_result["decision_id"],
        "output_zip": str(output_zip),
        "output_zip_sha256": zip_sha,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    digest = sub.add_parser("digest", help="Print a proposal canonical digest")
    digest.add_argument("proposal", type=Path)

    render = sub.add_parser(
        "render-inline-output",
        help="Generate the exact three-table G1 or R1 decision surface",
    )
    render.add_argument("--stage", required=True, choices=("G1", "R1"))
    render.add_argument("--graph-proposal", type=Path)
    render.add_argument("--card-proposal", type=Path)
    render.add_argument("--conformance", type=Path)
    render.add_argument("--output", required=True, type=Path)

    validate_inline = sub.add_parser(
        "validate-inline-output",
        help="Fail unless a rendered gate contains exactly the three generated tables",
    )
    validate_inline.add_argument("--stage", required=True, choices=("G1", "R1"))
    validate_inline.add_argument("--graph-proposal", type=Path)
    validate_inline.add_argument("--card-proposal", type=Path)
    validate_inline.add_argument("--conformance", type=Path)
    validate_inline.add_argument("--inline-output", required=True, type=Path)

    approval = sub.add_parser("validate-approval", help="Validate approval binding")
    approval.add_argument("--approval", required=True, type=Path)
    approval.add_argument("--baseline-zip", required=True, type=Path)
    approval.add_argument("--graph-proposal", required=True, type=Path)
    approval.add_argument("--card-proposal", required=True, type=Path)
    approval.add_argument("--g1-inline-output", type=Path)

    scope = sub.add_parser("verify-scope", help="Compare a work tree to the baseline")
    scope.add_argument("--approval", required=True, type=Path)
    scope.add_argument("--baseline-dir", required=True, type=Path)
    scope.add_argument("--work-dir", required=True, type=Path)

    package = sub.add_parser("package", help="Create a ZIP only after all gates pass")
    package.add_argument("--approval", required=True, type=Path)
    package.add_argument("--baseline-zip", required=True, type=Path)
    package.add_argument("--baseline-dir", required=True, type=Path)
    package.add_argument("--graph-proposal", required=True, type=Path)
    package.add_argument("--card-proposal", required=True, type=Path)
    package.add_argument("--conformance", required=True, type=Path)
    package.add_argument("--g1-inline-output", type=Path)
    package.add_argument("--r1-inline-output", type=Path)
    package.add_argument("--source-dir", required=True, type=Path)
    package.add_argument("--output-zip", required=True, type=Path)

    return parser


def _load_optional_json(path: Path | None) -> Mapping[str, Any] | None:
    return read_json(path) if path is not None else None


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "digest":
            _, digest = verify_proposal_file(args.proposal)
            result: Any = {"status": "pass", "sha256": digest}
        elif args.command == "render-inline-output":
            graph = _load_optional_json(args.graph_proposal)
            card = _load_optional_json(args.card_proposal)
            conformance = _load_optional_json(args.conformance)
            rendered = render_inline_output(
                args.stage,
                graph_proposal=graph,
                card_proposal=card,
                conformance=conformance,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
            result = {
                "status": "pass",
                "stage": args.stage,
                "output": str(args.output),
                "inline_output_sha256": sha256_file(args.output),
                "table_count": 3,
            }
        elif args.command == "validate-inline-output":
            result = validate_inline_output_file(
                args.inline_output,
                args.stage,
                graph_proposal=_load_optional_json(args.graph_proposal),
                card_proposal=_load_optional_json(args.card_proposal),
                conformance=_load_optional_json(args.conformance),
            )
        elif args.command == "validate-approval":
            result = validate_approval(
                args.approval,
                args.baseline_zip,
                args.graph_proposal,
                args.card_proposal,
                args.g1_inline_output,
            )
        elif args.command == "verify-scope":
            result = verify_scope(
                args.approval,
                args.baseline_dir,
                args.work_dir,
            )
        elif args.command == "package":
            result = gated_package(
                args.approval,
                args.baseline_zip,
                args.baseline_dir,
                args.graph_proposal,
                args.card_proposal,
                args.conformance,
                args.source_dir,
                args.output_zip,
                args.g1_inline_output,
                args.r1_inline_output,
            )
        else:
            raise GateError(f"Unknown command: {args.command}")
    except GateError as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, indent=2))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
