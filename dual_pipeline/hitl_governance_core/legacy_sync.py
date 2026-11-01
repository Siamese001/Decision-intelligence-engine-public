"""
dual_pipeline/hitl_governance_core/legacy_sync.py

Synchronizes decisions from legacy root-level hitl_decision_ledger.json
into the governed Merkle-chained .local/ledger store and SQLite database.
Resolves dual-ledger divergence and unifies historical Bayesian priors.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)


def sync_legacy_ledger_records(
    legacy_path: Path | str,
    target_store: Any,
) -> int:
    """Imports decisions from legacy unversioned ledger into governed HITLDecisionStore."""
    path = Path(legacy_path).resolve()
    if not path.is_file():
        return 0

    try:
        raw_text = path.read_text(encoding="utf-8")
        if not raw_text.strip():
            return 0
        data = json.loads(raw_text)
        decisions: List[dict[str, Any]] = data.get("decisions", []) if isinstance(data, dict) else []
        if not decisions:
            return 0

        existing = target_store.get_history()
        existing_ids = {
            str(r.get("decision_id") or r.get("ambiguity_id") or r.get("entry_id") or "")
            for r in existing
        }

        imported_count = 0
        for entry in decisions:
            entry_id = str(entry.get("ambiguity_id") or entry.get("decision_id") or "")
            if not entry_id or entry_id in existing_ids:
                continue

            candidates = entry.get("candidates", [])
            options = []
            for c in candidates:
                if isinstance(c, dict):
                    options.append({
                        "option_id": c.get("option_id", ""),
                        "text": c.get("text", ""),
                        "confidence": c.get("confidence", 0.5),
                        "rationale": c.get("rationale", ""),
                    })

            sel_opt_id = entry.get("selected_option_id", "")
            target_store.record_decision(
                entry_id=entry_id,
                category=entry.get("domain") or "legacy_sync",
                prompt=f"Legacy decision for {entry_id} in {entry.get('segment_id', '')}",
                options=options,
                selected_option=sel_opt_id,
                selected_key=sel_opt_id,
                rationale=entry.get("rationale", "Synchronized from legacy root ledger."),
                operator_id="legacy_migrator",
                delta_margin=entry.get("delta_margin", 0.0),
                raw_decision=entry,
            )
            existing_ids.add(entry_id)
            imported_count += 1

        if imported_count > 0:
            logger.info(f"Synchronized {imported_count} legacy HITL decisions into governed store.")
        return imported_count
    except Exception as e:
        logger.warning(f"Failed to synchronize legacy ledger from {legacy_path}: {e}")
        return 0


def enrich_legacy_records(target_store: Any) -> int:
    """Enriches legacy records lacking outcome_type or margin metadata and re-seals Merkle digests."""
    from dual_pipeline.core import digest
    from .telemetry import _derive_enriched_telemetry

    with target_store._ledger_lock():
        records = target_store._load_records(verify_integrity=False)
        if not records:
            return 0

        updated_count = 0
        enriched_records = []
        prev_hash = "0" * 64

        for rec in records:
            needs_enrichment = (
                "outcome_type" not in rec
                or "margin_at_decision" not in rec
                or "confidence_interval_at_decision" not in rec
            )
            if needs_enrichment:
                updated_count += 1
                meta = _derive_enriched_telemetry(
                    operator_id=str(rec.get("operator_id", "human_operator")),
                    selected_key=str(rec.get("selected_key", "")),
                    selected_option=rec.get("selected_option"),
                    options=rec.get("options", []),
                    category=str(rec.get("category", "")),
                    metadata=rec.get("metadata"),
                    records=enriched_records,
                )
                rec["outcome_type"] = meta["outcome_type"]
                rec["margin_at_decision"] = meta["margin_at_decision"]
                rec["confidence_interval_at_decision"] = meta["confidence_interval_at_decision"]
                rec["sample_count_at_presentation"] = meta["sample_count_at_presentation"]
                rec["metadata"] = meta

            rec["previous_entry_hash"] = prev_hash
            rec_sans_digest = {k: v for k, v in rec.items() if k != "entry_digest"}
            entry_digest = digest(rec_sans_digest)
            rec["entry_digest"] = entry_digest
            prev_hash = entry_digest
            enriched_records.append(rec)

        if updated_count > 0:
            target_store._save_records(enriched_records)

        return updated_count
