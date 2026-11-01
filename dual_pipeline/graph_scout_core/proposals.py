"""Proposal generation for Governed Graph Health Scout."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..calibration import create_calibration_proposal
from ..core import stable_id, write_json
from .models import ClutterFinding, ContradictionFinding, DuplicateEntityFinding


def emit_proposals(
    duplicate_findings: Sequence[DuplicateEntityFinding],
    contradiction_findings: Sequence[ContradictionFinding],
    output_dir: Path,
    run_id: str,
    clutter_findings: Optional[Sequence[ClutterFinding]] = None,
) -> List[Dict[str, Any]]:
    """Generate schema-valid learning change proposals for findings."""
    proposals_dir = output_dir / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    created_proposals: List[Dict[str, Any]] = []

    for f in duplicate_findings:
        proposal_artifact = create_calibration_proposal(
            proposal_type="alias_addition",
            target_parameter=f"{f.canonical_name_a} -> {f.canonical_name_b}",
            current_value=f.canonical_name_a,
            proposed_value=f.canonical_name_b,
            problem_description=(
                f"Near-duplicate entities detected across sources with similarity {f.similarity_score:.2f}. "
                f"Requires governed alias reconciliation."
            ),
            run_ids=[run_id],
            evidence_event_ids=[
                stable_id("evidence-duplicate", f.node_id_a, f.node_id_b, length=24)
            ],
            evaluation_plan=[
                "Validate alias addition in golden entity-resolution regression suite",
                "Verify non-regression across 11 BGE-M3 retrieval release gates",
            ],
            risk="medium" if f.hitl_required else "low",
            extensions={
                "com.partner_advisory.nee.graph-scout": {
                    "finding_type": "near_duplicate_entity",
                    "node_id_a": f.node_id_a,
                    "node_id_b": f.node_id_b,
                    "canonical_name_a": f.canonical_name_a,
                    "canonical_name_b": f.canonical_name_b,
                    "node_type": f.node_type_a,
                    "similarity_score": f.similarity_score,
                    "confidence_merge": f.confidence_merge,
                    "confidence_distinct": f.confidence_distinct,
                    "margin_delta": f.margin_delta,
                    "hitl_required": f.hitl_required,
                }
            },
        )
        file_name = f"{proposal_artifact['artifact_id']}.json"
        proposal_file = proposals_dir / file_name
        write_json(proposal_file, proposal_artifact)
        f.proposal_path = str(proposal_file)
        created_proposals.append(proposal_artifact)

    for c in contradiction_findings:
        proposal_artifact = create_calibration_proposal(
            proposal_type="workflow",
            target_parameter=f"supersede_assertion:{c.assertion_id_older}",
            current_value=c.claim_older,
            proposed_value=c.claim_newer,
            problem_description=(
                f"Temporal contradiction detected on '{c.subject_name}' ({c.predicate}). "
                f"Older claim from {c.timestamp_older} superseded by newer claim from {c.timestamp_newer}."
            ),
            run_ids=[run_id],
            evidence_event_ids=[
                stable_id("evidence-supersession", c.assertion_id_older, c.assertion_id_newer, length=24)
            ],
            evaluation_plan=[
                "Verify supersession history in assertion audit table",
                "Validate affected retrieval queries for currentness precision",
            ],
            risk="medium" if c.hitl_required else "low",
            extensions={
                "com.partner_advisory.nee.graph-scout": {
                    "finding_type": "temporal_contradiction",
                    "subject_node_id": c.subject_node_id,
                    "predicate": c.predicate,
                    "assertion_id_older": c.assertion_id_older,
                    "assertion_id_newer": c.assertion_id_newer,
                    "timestamp_older": c.timestamp_older,
                    "timestamp_newer": c.timestamp_newer,
                    "confidence_supersede": c.confidence_supersede,
                    "confidence_coexist": c.confidence_coexist,
                    "margin_delta": c.margin_delta,
                    "hitl_required": c.hitl_required,
                }
            },
        )
        file_name = f"{proposal_artifact['artifact_id']}.json"
        proposal_file = proposals_dir / file_name
        write_json(proposal_file, proposal_artifact)
        c.proposal_path = str(proposal_file)
        created_proposals.append(proposal_artifact)

    for cl in (clutter_findings or []):
        if cl.finding_type == "orphan_node":
            proposal_artifact = create_calibration_proposal(
                proposal_type="workflow",
                target_parameter=f"archive_orphan:{cl.target_id}",
                current_value=cl.label,
                proposed_value="QUARANTINED_ORPHAN",
                problem_description=(
                    f"Unreferenced orphan node detected: '{cl.label}' ({cl.target_id}). "
                    f"Recommended action: {cl.action_recommended}."
                ),
                run_ids=[run_id],
                evidence_event_ids=[
                    stable_id("evidence-orphan", cl.target_id, length=24)
                ],
                evaluation_plan=[
                    "Verify node has 0 edges and 0 assertions in canonical graph",
                    "Archive node to quarantine to maintain graph hygiene",
                ],
                risk="low",
                extensions={
                    "com.partner_advisory.nee.graph-scout": {
                        "finding_type": "orphan_node",
                        "node_id": cl.target_id,
                        "label": cl.label,
                        "action_recommended": cl.action_recommended,
                        "details": cl.details,
                    }
                },
            )
            file_name = f"{proposal_artifact['artifact_id']}.json"
            proposal_file = proposals_dir / file_name
            write_json(proposal_file, proposal_artifact)
            created_proposals.append(proposal_artifact)

    return created_proposals
