"""
dual_pipeline/transcript_stages_core/metric_discrepancy_evaluator.py

Numerical Metric Discrepancy & Enterprise Asset Reconciler.
Identifies conflicting numerical statements for critical enterprise assets (licenses,
seat counts, budgets, dollar commitments, FTE allocations) where relative variance
exceeds 15% (Delta > 0.15).

Surfaces atomic HitlRequirementEnvelopes / HITLRequiredError to prevent unverified
differing numbers from being passively dumped into the Ambiguity Lock Register (ALR)
without authentic human operator resolution.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.hitl_core.envelope import create_hitl_envelope
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.parameters import (
    get_float_parameter,
    get_hitl_ambiguity_margin_threshold,
)


def get_hitl_metric_discrepancy_threshold() -> float:
    """Returns SSOT threshold for numerical metric discrepancy variance (default: 0.15)."""
    return get_float_parameter("hitl.metric_discrepancy_threshold", 0.15)

from .models import HITLRequiredError

logger = logging.getLogger(__name__)

# Patterns for extracting numbers in context
NUMBER_WITH_COMMAS = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d{3,})\b")

# Semantic domain keyword patterns
DOMAIN_KEYWORDS: Dict[str, List[re.Pattern]] = {
    "gemini_enterprise_licenses": [
        re.compile(r"\b(?:licenses?|seats?|provisioned|unactivated|active users?|logged in)\b", re.IGNORECASE),
        re.compile(r"\bgemini\s+enterprise\b", re.IGNORECASE),
    ],
    "contract_financials": [
        re.compile(
            r"\b(?:\$|usd|dollars?|budget|contract\s+value|sow\s+(?:budget|value|cost|spend)|spend(?!\s+(?:time|a\s+lot\s+of\s+time|minutes?|hours?|days?|months?|years?)))\b",
            re.IGNORECASE,
        ),
    ],
    "headcount_allocation": [
        re.compile(r"\b(?:headcount|ftes?|engineers?|consultants?|developers?|resources?)\b", re.IGNORECASE),
    ],
}


def compute_metric_variance(values: List[float]) -> float:
    """Compute relative metric variance: Delta = (max - min) / max."""
    if len(values) < 2:
        return 0.0
    max_v = max(values)
    min_v = min(values)
    if max_v <= 0:
        return 0.0
    return (max_v - min_v) / max_v


def _parse_turn_speaker_and_content(line_clean: str, default_speaker: str) -> tuple[str, str]:
    m_spk = re.match(r"^([A-Z][a-zA-Z\s\.\-]+?)(?:\s*--[^\:]*|\s*\([^\)]*\))?\s*:\s*(.*)", line_clean)
    if m_spk:
        return m_spk.group(1).strip(), m_spk.group(2).strip()
    return default_speaker, line_clean


def _parse_valid_metric_number(raw_str: str, post_match: str, pre_match: str = "") -> Optional[float]:
    if post_match.startswith(("%", "percent")):
        return None
    try:
        num_val = float(raw_str.replace(",", ""))
    except ValueError:
        return None
    if num_val in (2025, 2026, 2027) or num_val < 50:
        return None
    if 1950 <= num_val <= 2035 and re.search(r"\b(?:since|late|early|year|in|from|until|before|after)\s+$", pre_match.lower()):
        return None
    return num_val


def _extract_domain_candidates(content: str, speaker: str, locator: str) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for match in NUMBER_WITH_COMMAS.finditer(content):
        raw_str = match.group(0)
        pre_match = content[max(0, match.start() - 15):match.start()]
        post_match = content[match.end():match.end() + 10].lstrip()
        num_val = _parse_valid_metric_number(raw_str, post_match, pre_match)
        if num_val is not None:
            candidates.append({
                "value": num_val,
                "raw_token": raw_str,
                "speaker": speaker,
                "locator": locator,
                "turn": content[:120],
            })
    return candidates


def extract_numerical_metrics(transcript: str) -> Dict[str, List[Dict[str, Any]]]:
    """Scan transcript turns and group extracted numbers by semantic domain.

    Returns mapping of domain_name -> list of candidate metric extractions:
    [{"value": float, "raw_token": str, "turn": str, "speaker": str, "locator": str}]
    """
    domain_metrics: Dict[str, List[Dict[str, Any]]] = {k: [] for k in DOMAIN_KEYWORDS}
    current_speaker = "Unknown"
    current_locator = "[00:00:00]"

    for line in transcript.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue

        # Detect timestamp locator
        m_ts = re.search(r"(\d{2}:\d{2}(?::\d{2})?)", line_clean)
        if m_ts:
            current_locator = f"[{m_ts.group(1)}]"

        current_speaker, content_to_scan = _parse_turn_speaker_and_content(line_clean, current_speaker)

        # Check domain keyword presence
        for domain, patterns in DOMAIN_KEYWORDS.items():
            if all(p.search(content_to_scan) for p in patterns):
                domain_metrics[domain].extend(
                    _extract_domain_candidates(content_to_scan, current_speaker, current_locator)
                )

    return {k: v for k, v in domain_metrics.items() if len(v) >= 2}


def _record_in_store(store: Any, domain: str, resolved_metric: str, receipt: Dict[str, Any]) -> None:
    if store is not None and hasattr(store, "record_decision"):
        try:
            import inspect
            sig = inspect.signature(store.record_decision)
            if "decision_id" in sig.parameters:
                import uuid
                store.record_decision(
                    decision_id=f"hitl_metric_{domain}_{uuid.uuid4().hex[:12]}",
                    category="metric_discrepancy",
                    prompt=f"Authoritative Metric Reconciliation: {domain}",
                    options=[{"key": resolved_metric, "label": resolved_metric, "confidence": 1.0}],
                    selected_option=resolved_metric,
                    rationale="Operator verified authoritative metric baseline via HITL Gate",
                    operator_id="human_operator",
                    metadata={"domain": domain, "receipt": receipt},
                )
            else:
                store.record_decision(
                    decision_type="METRIC_RECONCILIATION",
                    subject=domain,
                    choice=resolved_metric,
                    confidence=1.0,
                    rationale="Operator verified authoritative metric baseline via HITL Gate",
                    extra_payload=receipt,
                )
            if hasattr(store, "export_learned_priors"):
                store.export_learned_priors()
        except Exception as e:
            logger.debug("Store record_decision note: %s", e)


def _append_to_hitl_ledger(repo_root: Optional[Path], store: Any, receipt: Dict[str, Any]) -> None:
    # Direct un-Merkled JSON append is strictly barred to prevent ledger corruption.
    pass


def apply_hitl_metric_resolution(
    context_or_metadata: Dict[str, Any],
    domain: str,
    resolved_metric: str,
    repo_root: Optional[Path] = None,
    store: Optional[Any] = None,
) -> Dict[str, Any]:
    """Binds operator-resolved metric to metadata and records cryptographic receipt."""
    metadata = (
        context_or_metadata.get("metadata")
        if (isinstance(context_or_metadata, dict) and isinstance(context_or_metadata.get("metadata"), dict))
        else context_or_metadata
    )
    auth_metrics = metadata.setdefault("authoritative_metrics", {})
    auth_metrics[domain] = resolved_metric

    resolutions = metadata.setdefault("hitl_metric_resolutions", {})
    resolutions[domain] = resolved_metric

    timestamp = datetime.now(timezone.utc).isoformat()
    receipt = {
        "decision_type": "METRIC_RECONCILIATION",
        "type": "metric_reconciliation",
        "domain": domain,
        "resolved_metric": resolved_metric,
        "timestamp_utc": timestamp,
        "status": "RESOLVED_HITL",
        "hitl_receipt_bound": True,
    }
    metadata.setdefault("hitl_metric_receipts", {})[domain] = receipt

    # Also record in ALR hitl_resolutions
    hitl_res = metadata.setdefault("hitl_resolutions", {})
    hitl_res[f"metric_{domain}"] = {
        "ambiguity": f"Contemporaneous metric divergence in {domain.replace('_', ' ')}",
        "locator": "[Session Span]",
        "statement_id": f"stmt-metric-{abs(hash(domain)) % 100:02d}",
        "resolution": f"Authoritative baseline established: {resolved_metric}",
        "basis": f"Calibrated Multi-Account Context Reconciliation (HITL Baseline Established, receipt {hashlib_sha256(resolved_metric)[:8]})",
    }

    target_store = store or _init_decision_store(None)
    _record_in_store(target_store, domain, resolved_metric, receipt)
    _append_to_hitl_ledger(repo_root, target_store, receipt)
    return receipt


def hashlib_sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _init_decision_store(store: Optional[HITLDecisionStore]) -> HITLDecisionStore:
    try:
        return store or HITLDecisionStore()
    except Exception as e:
        logger.debug("Failed initializing default HITLDecisionStore: %s; using transient store.", e)
        import tempfile
        transient_path = Path(tempfile.gettempdir()) / "transient_hitl_metric_ledger.json"
        return HITLDecisionStore(transient_path)


def _build_metric_options(distinct_values: List[float]) -> List[Dict[str, Any]]:
    top_val = distinct_values[0]
    options = [
        {
            "key": f"provisioned_baseline_{top_val}",
            "label": f"(Recommended) Authoritative provisioned baseline: {top_val:,.0f} units — Confidence: HIGH (95%)",
            "value": f"{top_val:,.0f} units (Provisioned Baseline)",
            "confidence": 0.95,
            "confidence_score": 0.95,
            "confidence_level": "HIGH (95%)",
            "tier": "HIGH",
            "is_recommended": True,
            "recommended": True,
        },
    ]
    for idx, val in enumerate(distinct_values[1:4], 2):
        conf = max(0.60, 0.95 - (idx - 1) * 0.10)
        tier = "HIGH" if conf >= 0.85 else ("MEDIUM" if conf >= 0.50 else "LOW")
        desc = (
            "Active consumption baseline"
            if idx == 2
            else ("Departmental allocation baseline" if idx == 3 else "Alternative citation baseline")
        )
        options.append({
            "key": f"{desc.lower().replace(' ', '_')}_{val}",
            "label": f"{desc}: {val:,.0f} units — Confidence: {tier} ({conf * 100:.0f}%)",
            "value": f"{val:,.0f} units ({desc})",
            "confidence": conf,
            "confidence_score": conf,
            "confidence_level": f"{tier} ({conf * 100:.0f}%)",
            "tier": tier,
            "is_recommended": False,
            "recommended": False,
        })
    return options


def _try_interactive_resolve(
    domain: str,
    prompt: str,
    options: List[Dict[str, Any]],
    decision_store: Any,
    interactive_resolver: Any,
    metadata: Dict[str, Any],
    repo_root: Optional[Path],
) -> bool:
    envelope = create_hitl_envelope(
        category="metric_discrepancy",
        domain=domain,
        question=prompt,
        options=options,
        store=decision_store,
        threshold=get_hitl_metric_discrepancy_threshold(),
    )
    try:
        chosen = str(interactive_resolver(envelope))
        apply_hitl_metric_resolution(metadata, domain, chosen, repo_root=repo_root, store=decision_store)
        return True
    except Exception as e:
        logger.warning("Interactive metric resolver error: %s", e)
        return False


def _process_domain_discrepancy(
    domain: str,
    candidates: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    decision_store: Any,
    interactive_resolver: Optional[Any],
    repo_root: Optional[Path],
) -> None:
    values = [c["value"] for c in candidates]
    variance = compute_metric_variance(values)
    if variance <= get_hitl_metric_discrepancy_threshold():
        return

    distinct_values = sorted(list(set(values)), reverse=True)
    val_strs = [f"{v:,.0f}" for v in distinct_values]
    citations = [f"{c['speaker']}: {c['value']:,.0f} ({c['turn'][:50]}...)" for c in candidates[:4]]
    options = _build_metric_options(distinct_values)
    try:
        if decision_store is not None and hasattr(decision_store, "calibrate_options"):
            options = decision_store.calibrate_options(options, category="metric_discrepancy")
            for opt in options:
                if "confidence_score" in opt:
                    score = float(opt["confidence_score"])
                    tier = "HIGH" if score >= 0.85 else ("MEDIUM" if score >= 0.50 else "LOW")
                    opt["confidence_tier"] = tier
                    opt["tier"] = tier
                    opt["confidence_level"] = f"{tier} ({int(round(score * 100))}%)"
                    opt["is_recommended"] = bool(opt.get("recommended", opt.get("is_recommended", False)))
    except Exception:
        pass
    prompt = (
        f"### HITL Decision Required (Authoritative Metric Reconciliation — 1 of 1)\n\n"
        f"**Domain:** {domain.replace('_', ' ').title()}\n"
        f"**Divergence Detected:** Relative spread of {variance * 100:.1f}% across cited numbers ({', '.join(val_strs)}).\n"
        f"**Conversational Citations:** {'; '.join(citations)}\n\n"
        f"Please select or specify the authoritative baseline figure to be recorded in the executive recap and Ambiguity Lock Register:"
    )

    if interactive_resolver is not None:
        if _try_interactive_resolve(domain, prompt, options, decision_store, interactive_resolver, metadata, repo_root):
            return

    metadata["invalidate_prior_outputs"] = True
    if "_orig_metadata" in metadata and isinstance(metadata["_orig_metadata"], dict):
        metadata["_orig_metadata"]["invalidate_prior_outputs"] = True

    raise HITLRequiredError(
        prompt,
        hitl_type="metric_discrepancy",
        missing_fields=[f"metric:{domain}"],
        options=options,
        prompt=prompt,
        evidence={"domain": domain, "variance": variance, "values": distinct_values},
    )


def evaluate_metric_discrepancies(
    transcript: str,
    metadata: Dict[str, Any],
    store: Optional[HITLDecisionStore] = None,
    interactive_resolver: Optional[Any] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Inspect transcript for metric divergence (>15% variance) and require HITL resolution.

    Raises HITLRequiredError if conflicting metric numbers exist without operator resolution.
    """
    if metadata.get("metrics_confirmed_by_operator") or metadata.get("bypass_metric_discrepancy_gate"):
        return metadata

    auth_metrics = metadata.get("authoritative_metrics", {})
    metric_resolutions = metadata.get("hitl_metric_resolutions", {})
    metric_receipts = metadata.get("hitl_metric_receipts", {})

    discrepancies = extract_numerical_metrics(transcript)
    if not discrepancies:
        return metadata

    decision_store = _init_decision_store(store)

    for domain, candidates in discrepancies.items():
        if domain in auth_metrics or domain in metric_resolutions or domain in metric_receipts:
            continue
        _process_domain_discrepancy(
            domain, candidates, metadata, decision_store, interactive_resolver, repo_root
        )

    return metadata
