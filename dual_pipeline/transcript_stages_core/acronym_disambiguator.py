"""
dual_pipeline/transcript_stages_core/acronym_disambiguator.py

Phonetic ASR Acronym & Domain Term Disambiguation Engine.
Detects ambiguous enterprise acronyms/homophones in speech transcripts,
evaluates confidence margin against the 15% threshold, and surfaces atomic
HitlRequirementEnvelopes to ensure verified terms are locked into the ALR.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.hitl_core.envelope import create_hitl_envelope
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

logger = logging.getLogger(__name__)

# Known domain acronym ambiguity pairs: {corrupted_or_ambiguous_pattern: (clean_opt_a, clean_opt_b, default_a_conf, default_b_conf)}
AMBIGUOUS_ACRONYM_PAIRS: Dict[str, Dict[str, Any]] = {
    r"\bBGD\b": {
        "term": "PGD",
        "description": "Power Generation Division vs Battery Group Division",
        "options": [
            {"key": "PGD", "label": "PGD (Power Generation Division)", "confidence": 0.53},
            {"key": "BGD", "label": "BGD (Battery Group Division)", "confidence": 0.47},
        ],
    },
    r"\bSSW\b": {
        "term": "SOW",
        "description": "Statement of Work vs Software / Scope",
        "options": [
            {"key": "SOW", "label": "SOW (Statement of Work)", "confidence": 0.54},
            {"key": "SSW", "label": "SSW (Site Specific Work)", "confidence": 0.46},
        ],
    },
    r"\bSteer\s+Code\b": {
        "term": "SteerCo",
        "description": "Steering Committee vs Steer Code",
        "options": [
            {"key": "SteerCo", "label": "SteerCo (Steering Committee)", "confidence": 0.55},
            {"key": "Steer Code", "label": "Steer Code (Guidance Code)", "confidence": 0.45},
        ],
    },
    r"\bSolom\b": {
        "term": "PartnerAdvisory",
        "description": "PartnerAdvisory Consulting vs Solom",
        "options": [
            {"key": "PartnerAdvisory", "label": "PartnerAdvisory (Consulting Partner)", "confidence": 0.56},
            {"key": "Solom", "label": "Solom (Individual / Team)", "confidence": 0.44},
        ],
    },
}


def disambiguate_asr_acronyms(
    text: str,
    metadata: Dict[str, Any],
    store: Optional[HITLDecisionStore] = None,
    interactive_resolver: Optional[Any] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Detect and disambiguate phonetic acronym corruptions via HITL requirement envelopes.

    Updates transcript text with resolved term and adds record to metadata['hitl_resolutions']
    to ensure inclusion in the Output 01 Ambiguity Lock Register table.
    """
    if not text:
        return text, metadata

    hitl_resolutions = metadata.setdefault("hitl_resolutions", {})
    decision_store = store or HITLDecisionStore()

    for pattern, info in AMBIGUOUS_ACRONYM_PAIRS.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue

        matched_str = match.group(0)
        options = info["options"]
        desc = info["description"]

        envelope = create_hitl_envelope(
            category="asr_acronym_disambiguation",
            domain="enterprise_acronym",
            question=f"Disambiguate ASR acronym '{matched_str}': {desc}",
            options=options,
            store=decision_store,
            threshold=get_hitl_ambiguity_margin_threshold(),
        )

        selected_key: str = options[0]["key"]
        if interactive_resolver is not None:
            try:
                selected_key = str(interactive_resolver(envelope))
            except Exception as e:
                logger.warning("Interactive acronym resolver failed: %s; falling back to top option.", e)

        # Resolve envelope and persist to ledger
        is_op = interactive_resolver is not None
        receipt = envelope.resolve_and_persist(
            selection=selected_key,
            operator_id="human_operator" if is_op else "autonomous_context",
            store=decision_store,
        )

        # Apply substitution to transcript
        text = re.sub(pattern, selected_key, text, flags=re.IGNORECASE)

        # Append to ALR resolutions in metadata
        hitl_key = f"acronym_{selected_key.lower()}"
        receipt_id = receipt.get("receipt_digest", "")[:12] if isinstance(receipt, dict) else ""
        receipt_entry_id = (receipt.get("decision_id") or receipt.get("entry_id")) if isinstance(receipt, dict) else ""
        basis_text = (
            f"Operator Confirmed (HITL Acronym Disambiguation, receipt {receipt_id})"
            if is_op
            else f"Autonomous Lexicon Normalization (receipt {receipt_id})"
        )
        hitl_resolutions[hitl_key] = {
            "ambiguity": f"Phonetic ASR ambiguity: '{matched_str}' vs '{selected_key}'",
            "locator": "[00:00:00 – Session End]",
            "statement_id": f"stmt-acronym-{abs(hash(matched_str)) % 100:02d}",
            "resolution": f"Standardized to verified domain term '{selected_key}'.",
            "basis": basis_text,
            "receipt_id": receipt_entry_id or receipt_id,
            "verified_entry_id": receipt_entry_id,
        }
        logger.info("Disambiguated ASR acronym '%s' -> '%s' (ALR key %s)", matched_str, selected_key, hitl_key)

    return text, metadata
