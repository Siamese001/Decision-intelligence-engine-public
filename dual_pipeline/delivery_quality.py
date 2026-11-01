"""Deterministic delivery-profile and artifact release gates."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .contracts import create_artifact
from .core import stable_id

CLIENT_PROFILES = {"client_technical", "client_executive", "commercial"}
UNDEFINED_JARGON = re.compile(r"\b(?:M-A|P/V|L2D|C0\.3|X1|X2|X3)\b", re.IGNORECASE)


def evaluate_delivery_quality(
    *,
    run_id: str,
    answer: Mapping[str, Any] | None,
    delivery_profile: str,
    artifact_type: str,
    coverage_complete: bool,
    render_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    text = json.dumps(answer or {}, ensure_ascii=False, sort_keys=True)
    client = delivery_profile in CLIENT_PROFILES
    render = dict(render_validation or {})
    checks = {
        "source_coverage": bool(coverage_complete),
        "terminology_defined": not client or UNDEFINED_JARGON.search(text) is None,
        "business_language": not client or len(text) <= 24_000,
        "audience_fit": answer is not None,
        "render_review": artifact_type != "architecture_deck" or render.get("render_review") is True,
        "visual_hierarchy": artifact_type != "architecture_deck" or render.get("visual_hierarchy") is True,
        "principle_mapping": artifact_type != "architecture_deck" or render.get("principle_mapping") is True,
        "persona_relevance": artifact_type != "architecture_deck" or render.get("persona_relevance") is True,
    }
    defects = []
    defect_by_check = {
        "source_coverage": "SOURCE_COVERAGE_INCOMPLETE",
        "terminology_defined": "UNDEFINED_CLIENT_TERM",
        "business_language": "CLIENT_JARGON_OR_LENGTH",
        "audience_fit": "AUDIENCE_FIT_MISSING",
        "render_review": "RENDER_REVIEW_MISSING",
        "visual_hierarchy": "VISUAL_HIERARCHY_FAILED",
        "principle_mapping": "PRINCIPLE_MAPPING_FAILED",
        "persona_relevance": "PERSONA_RELEVANCE_FAILED",
    }
    for check, passed in checks.items():
        if not passed:
            defects.append(defect_by_check[check])
    passed = not defects
    artifact_id = stable_id("delivery-quality", run_id, delivery_profile, artifact_type, length=26)
    return create_artifact(
        "delivery-quality-decision", artifact_id,
        decision_id=artifact_id, run_id=run_id,
        delivery_profile=delivery_profile, artifact_type=artifact_type,
        status="PASS" if passed else "FAIL", checks=checks,
        defect_codes=defects, client_release_allowed=passed,
        automatic_provider_call_count=0, extensions={},
    )
