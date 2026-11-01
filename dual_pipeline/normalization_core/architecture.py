"""Architecture diagram normalization by visual/spatial evidence region."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..contracts import create_artifact, validate_artifact
from ..core import digest, normalize_space, stable_id, trace_id
from .constants import AUTHORITY_RANKS
from .meeting import (
    _context_extension,
    _extension,
    _mention,
    normalize_evidence_envelope,
)


def normalize_architecture(
    package: dict[str, Any], *, run_context: str = "unbound"
) -> dict[str, Any]:
    """Validate and decompose an architecture diagram package by evidence region."""

    validate_artifact(package)
    if not package["contract_id"].endswith(":architecture-diagram-intake:1.0.0"):
        raise ValueError("expected an architecture-diagram intake contract")
    source = package["source"]
    package_digest = package["canonical_digest"]["value"]
    intake_id = stable_id("intake", package["artifact_id"], package_digest, length=24)
    run_id = stable_id(
        "run-intake", package["artifact_id"], package_digest, run_context, length=24
    )
    current_trace_id = trace_id(run_id, package_digest)
    posture = _extension(package, "com.partner_advisory.nee.source-posture", "derivative_internal")
    authority_rank = int(
        _extension(package, "com.partner_advisory.nee.authority-rank", AUTHORITY_RANKS.get(posture, 4))
    )
    evidence: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    component_by_ref = {item["component_ref"]: item for item in package["components"]}

    def add_candidate(
        *,
        ref: str,
        candidate_type: str,
        assertion_type: str,
        text: str,
        region: dict[str, Any],
        confidence: float,
        mentions: list[str],
        materiality: str,
    ) -> None:
        candidate_id = stable_id("candidate", run_id, ref, length=26)
        evidence_id = stable_id("evidence", source["source_version_id"], ref, region, length=26)
        evidence.append(
            {
                "evidence_id": evidence_id,
                "source_id": source["source_id"],
                "source_version_id": source["source_version_id"],
                "locator": region,
                "relationship": "supports",
                "directness": "derivative",
                "authority_rank": authority_rank,
                "content_digest": digest(text),
            }
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_type": candidate_type,
                "assertion_type": assertion_type,
                "normalized_text": normalize_space(text),
                "materiality": materiality,
                "entity_mentions": [
                    _mention(
                        candidate_id,
                        index,
                        label,
                        stable_identifier=None,
                        context=f"Architecture diagram {source['diagram_id']} / {region['value']}",
                    )
                    for index, label in enumerate(mentions, 1)
                ],
                "evidence_ids": [evidence_id],
                "source_locator": region,
                "classification": source["classification"],
                "audiences": source["audiences"],
                "extraction_confidence": confidence,
            }
        )

    for component in package["components"]:
        add_candidate(
            ref=component["component_ref"],
            candidate_type="architecture_component",
            assertion_type="architecture_component",
            text=f"Architecture component: {component['reported_label']} ({component['candidate_type']}).",
            region=component["region"],
            confidence=component["extraction_confidence"],
            mentions=[component["reported_label"]],
            materiality="high",
        )
    for relationship in package["relationships"]:
        source_label = component_by_ref[relationship["from_component_ref"]]["reported_label"]
        target_label = component_by_ref[relationship["to_component_ref"]]["reported_label"]
        add_candidate(
            ref=relationship["relationship_ref"],
            candidate_type="architecture_relationship",
            assertion_type="architecture_relationship",
            text=f"{source_label} {relationship['reported_predicate']} {target_label}.",
            region=relationship["region"],
            confidence=relationship["extraction_confidence"],
            mentions=[source_label, target_label],
            materiality="high",
        )
    for ambiguity in package["open_ambiguities"]:
        labels = [
            component_by_ref[ref]["reported_label"]
            for ref in ambiguity["affected_refs"]
            if ref in component_by_ref
        ]
        add_candidate(
            ref=ambiguity["ambiguity_id"],
            candidate_type="open_question",
            assertion_type="question",
            text=ambiguity["description"],
            region=ambiguity["region"],
            confidence=1.0,
            mentions=labels,
            materiality="high",
        )

    return create_artifact(
        "normalized-candidate-bundle",
        stable_id("candidate-bundle", run_id, length=26),
        intake_id=intake_id,
        run_id=run_id,
        trace_id=current_trace_id,
        source_id=source["source_id"],
        source_version_id=source["source_version_id"],
        evidence=evidence,
        candidates=candidates,
        extensions=_context_extension(
            source_kind="architecture_diagram",
            attendees=[],
            workstream=None,
            source_posture=posture,
            authority_rank=authority_rank,
        )
        | {
            "com.partner_advisory.nee.source": {
                "name": source["diagram_id"],
                "date": source["source_date"],
                "package_digest": package_digest,
                "source_location": f"{source['diagram_id']}#{source['page_id']}",
            }
        },
    )


def normalize_file(path: Path, *, run_context: str = "unbound") -> dict[str, Any]:
    package = json.loads(path.read_text(encoding="utf-8"))
    contract = package.get("contract_id", "")
    if contract.endswith(":evidence-envelope-intake:1.0.0"):
        return normalize_evidence_envelope(package, run_context=run_context)
    raise ValueError(
        f"unsupported intake contract: {contract!r}. All legacy intake mechanisms have been "
        "deleted; the only intake mechanism is the Evidence Envelope JSON format "
        "(urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0)."
    )
