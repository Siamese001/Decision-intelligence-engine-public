"""
dual_pipeline/transcript_qa_core/frozen_gates.py

Helper utilities for bypassing QA gates and evaluations on permanently frozen prior artifacts.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def build_frozen_gate(artifact_label: str) -> Dict[str, Any]:
    return {
        "status": "PASS",
        "passed": True,
        "reason": "FROZEN_PRIOR_ARTIFACT",
        "details": f"{artifact_label} is frozen from a verified prior release; QA evaluation skipped.",
        "failures": [],
        "gates": {},
        "frozen": True,
    }


def is_stage_frozen(state_machine: Any, stage_key: str) -> bool:
    return bool(state_machine and hasattr(state_machine, "is_frozen") and state_machine.is_frozen(stage_key))


def check_frozen_dependency_validity(
    art_key: str,
    content: Optional[str],
    state_machine: Any,
    metadata: Optional[Dict[str, Any]] = None,
    candidate_text: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Validates that frozen artifact does not contradict current upstream identity, dependencies, or certified SHA."""
    import hashlib

    meta = metadata or {}
    if meta and (meta.get("notes_mode") or meta.get("skip_output_1")) and art_key == "output_1":
        return True, None
    if not content:
        return False, f"Missing frozen content for {art_key}"

    if candidate_text is not None:
        cand_sha = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
        froz_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if cand_sha != froz_sha:
            return False, f"Frozen content tampered or mismatched for {art_key}: candidate SHA ({cand_sha[:8]}) != frozen SHA ({froz_sha[:8]})"

    exp_sha = (
        meta.get(f"{art_key}_sha256")
        or meta.get("artifact_manifest", {}).get(f"{art_key}_sha256")
        or meta.get("frozen_shas", {}).get(art_key)
    )
    if exp_sha:
        froz_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if froz_sha != exp_sha:
            if (meta.get("resume") or meta.get("regenerate")) and state_machine and hasattr(state_machine, "is_frozen") and state_machine.is_frozen(art_key):
                logger.info(f"Resume/Regenerate: aligning {art_key} metadata hash to state machine frozen content ({froz_sha[:8]}).")
                meta[f"{art_key}_sha256"] = froz_sha
                if "artifact_manifest" in meta and isinstance(meta["artifact_manifest"], dict):
                    meta["artifact_manifest"][f"{art_key}_sha256"] = froz_sha
            else:
                return False, f"Frozen content hash mismatch for {art_key}: expected {exp_sha[:8]}, got {froz_sha[:8]}"

    regen = meta.get("regenerate")
    is_regenerating_this = False
    if regen:
        from dual_pipeline.l0.output_spec import parse_regenerate_spec
        try:
            regen_set = parse_regenerate_spec(regen)
            is_regenerating_this = (art_key in regen_set)
        except Exception:
            pass

    # Force rerun only invalidates this artifact if it is targeted for regeneration or all outputs are being rerun
    current_att = getattr(state_machine, "current_attempt", 1) if state_machine else 1
    force_rerun_active = bool(
        meta.get("force_rerun")
        and current_att <= 1
        and (not regen or is_regenerating_this)
    )

    is_selective_preserve = bool(regen and not is_regenerating_this)

    if not is_selective_preserve and (
        meta.get("upstream_transcript_changed")
        or (meta.get("output_1_changed") and art_key != "output_1")
        or meta.get("speaker_resolution_changed")
        or (meta.get("remediation_directive_recap") and art_key == "output_2" and not meta.get("resume"))
        or force_rerun_active
    ):
        return False, f"Upstream transcript or dependency changed invalidates {art_key}"

    if art_key in ("output_2", "output_3"):
        content_lower = content.lower()
        if "noemi monnerville" in content_lower:
            if re.search(r"contractor[\s\:\-]+.*noemi\s+monnerville|noemi\s+monnerville[^\.\n]*contractor", content_lower):
                return False, f"Frozen {art_key} contains stale affiliation 'Contractor' for Noemi Monnerville (confirmed PartnerAdvisory)"
        if "krishnakumar venkataraman" in content_lower:
            return False, f"Frozen {art_key} contains unconfirmed surname guess 'Krishnakumar Venkataraman'"

    return True, None


def apply_frozen_to_qa_report(
    qa_report: Dict[str, Any],
    state_machine: Any,
    metadata: Optional[Dict[str, Any]] = None,
    candidate_texts: Optional[Dict[str, str]] = None,
) -> None:
    if not (state_machine and hasattr(state_machine, "is_frozen")):
        return
    cands = candidate_texts or qa_report.get("artifacts") or qa_report.get("healed_artifacts")
    if not cands and metadata:
        cands = metadata.get("artifacts")

    for art_key, rep_key in (
        ("output_1", "output_01_forensic_transcript"),
        ("output_2", "output_02_executive_recap"),
        ("output_3", "output_03_canonical_signal_projection"),
    ):
        if metadata and (metadata.get("notes_mode") or metadata.get("skip_output_1")) and art_key == "output_1":
            continue
        if state_machine.is_frozen(art_key) and rep_key in qa_report:
            content = state_machine.get_frozen_content(art_key) if hasattr(state_machine, "get_frozen_content") else None
            cand_text = cands.get(art_key) if isinstance(cands, dict) else None
            valid, err = check_frozen_dependency_validity(art_key, content, state_machine, metadata, candidate_text=cand_text)
            if valid:
                qa_report[rep_key]["status"] = "PASS"
                qa_report[rep_key]["passed"] = True
                qa_report[rep_key]["frozen"] = True
                qa_report[rep_key]["failures"] = []
            else:
                if cand_text is not None and "candidate SHA" in str(err) and qa_report[rep_key].get("passed"):
                    logger.info(
                        "Artifact '%s' candidate updated deterministically and passed live QA gates; "
                        "updating frozen cache without unfreezing.",
                        art_key,
                    )
                    if hasattr(state_machine, "frozen_artifacts") and isinstance(state_machine.frozen_artifacts, dict):
                        state_machine.frozen_artifacts[art_key] = cand_text
                    qa_report[rep_key]["status"] = "PASS"
                    qa_report[rep_key]["passed"] = True
                    qa_report[rep_key]["frozen"] = True
                    qa_report[rep_key]["failures"] = []
                else:
                    if hasattr(state_machine, "unfreeze_artifact"):
                        state_machine.unfreeze_artifact(art_key, force=True)
                    if cand_text is not None and "candidate SHA" in str(err):
                        logger.info("Artifact '%s' candidate differs from frozen cache; unfreezing and preserving live QA gate result.", art_key)
                    else:
                        qa_report[rep_key]["status"] = "FAIL"
                        qa_report[rep_key]["passed"] = False
                        qa_report[rep_key]["frozen"] = False
                        qa_report[rep_key]["failures"] = [
                            f"FROZEN_ARTIFACT_DEPENDENCY_INVALIDATED: {err or 'Upstream dependency change'}"
                        ]
    qa_report["passed"] = all(
        qa_report[k].get("passed", False)
        for k in ("output_01_forensic_transcript", "output_02_executive_recap", "output_03_canonical_signal_projection")
        if k in qa_report
    )
