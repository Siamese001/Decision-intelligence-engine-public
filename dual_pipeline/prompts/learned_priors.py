"""
dual_pipeline/prompts/learned_priors.py

Dynamic Learned Operator Priors injection engine.
Extracts empirical priors from the governed HITLDecisionStore and formats
bounded, XML-escaped <learned_operator_priors> blocks for injection into
synthesis and judge prompts.
"""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Optional

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore

logger = logging.getLogger(__name__)


def _collect_prior_entries(
    learned_priors: Dict[str, Dict[str, Any]], category: Optional[str]
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    cat_filter = category.lower() if category else None
    for cat_name, cat_entries in learned_priors.items():
        if cat_filter and cat_filter not in cat_name.lower():
            continue
        for key, details in cat_entries.items():
            aff = details.get("affirmed_weight", 0.0)
            rej = details.get("rejected_weight", 0.0)
            samples = details.get("sample_size", 0)
            conf = details.get("confidence_score", 0.5)
            if samples > 0 or aff > 0.0 or rej > 0.0:
                selected.append({
                    "category": cat_name,
                    "key": key,
                    "confidence_score": conf,
                    "affirmed_weight": aff,
                    "rejected_weight": rej,
                    "sample_size": samples,
                })
    return selected


def _format_prior_xml_lines(entry: Dict[str, Any]) -> List[str]:
    esc_cat = html.escape(str(entry["category"]))
    esc_key = html.escape(str(entry["key"]))
    conf = f"{entry['confidence_score']:.2f}"
    samples = str(entry["sample_size"])
    aff = f"{entry['affirmed_weight']:.2f}"
    rej = f"{entry['rejected_weight']:.2f}"
    if entry.get("affirmed_weight", 0.0) == 0.0 and entry.get("rejected_weight", 0.0) > 0.0:
        action = "avoid"
    else:
        action = "prefer" if entry["confidence_score"] >= 0.5 else "avoid"
    return [
        f'  <prior category="{esc_cat}" key="{esc_key}" confidence="{conf}" samples="{samples}" affirmed_weight="{aff}" rejected_weight="{rej}">',
        f"    Operator historical feedback indicates {action} for pattern '{esc_key}' in domain '{esc_cat}' (confidence: {conf}).",
        "  </prior>",
    ]


def compile_learned_priors_block(
    category: Optional[str] = None,
    store: Optional[HITLDecisionStore] = None,
    max_priors: int = 20,
) -> str:
    """Compile an XML-isolated block of learned operator priors for prompt injection."""
    try:
        decision_store = store or HITLDecisionStore()
        exported = decision_store.export_learned_priors()
        learned_priors: Dict[str, Dict[str, Any]] = exported.get("learned_priors", {})
    except Exception as e:
        logger.warning("Failed to load learned operator priors: %s", e)
        return ""

    if not learned_priors:
        return ""

    selected = _collect_prior_entries(learned_priors, category)
    if not selected:
        return ""

    selected.sort(
        key=lambda x: (x["sample_size"], x["affirmed_weight"], x["confidence_score"]),
        reverse=True,
    )
    lines: List[str] = [
        "<learned_operator_priors>",
        "  <!-- Governed Bayesian priors derived from historical operator resolutions -->",
    ]
    for entry in selected[:max_priors]:
        lines.extend(_format_prior_xml_lines(entry))
    lines.append("</learned_operator_priors>")
    return "\n".join(lines)


def inject_learned_priors_into_metadata(
    metadata: Dict[str, Any],
    category: Optional[str] = None,
    store: Optional[HITLDecisionStore] = None,
) -> None:
    """Inject learned priors XML block into metadata for downstream prompt assembly."""
    priors_block = compile_learned_priors_block(category=category, store=store)
    if priors_block:
        metadata["learned_operator_priors"] = priors_block
        prior_ctx = metadata.get("prior_context", "")
        metadata["prior_context"] = (prior_ctx + "\n\n" if prior_ctx else "") + priors_block
