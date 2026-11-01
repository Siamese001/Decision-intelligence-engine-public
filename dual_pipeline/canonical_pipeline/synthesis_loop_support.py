"""Selection helpers for the canonical synthesis retry loop."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, Tuple

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality

logger = logging.getLogger("CanonicalAgenticPipeline.lifecycle")


def select_high_water_candidates(
    state_machine: Any, modality: CanonicalModality, synthesis_result: Any,
) -> bool:
    """Apply candidates only when every deliverable crosses its own release threshold."""
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

    h1 = state_machine.get_high_water_mark_score("output_1") if modality != CanonicalModality.MEETING_NOTES else 100
    h2 = state_machine.get_high_water_mark_score("output_2")
    h3 = state_machine.get_high_water_mark_score("output_3")
    logger.info("Cross-attempt high-water mark check: O1=%s, O2=%s, O3=%s", h1, h2, h3)
    t1 = CANONICAL_RELEASE_POLICY.output1_pass_threshold
    t2 = CANONICAL_RELEASE_POLICY.output2_pass_threshold
    t3 = CANONICAL_RELEASE_POLICY.output3_pass_threshold
    if not (h1 >= t1 and h2 >= t2 and h3 >= t3):
        return False
    logger.info(
        "Monotonic Assembly: All deliverables meet release pass thresholds (O1>=%d, O2>=%d, O3>=%d). Assembling winning triad.",
        t1, t2, t3,
    )
    c1_best = state_machine.get_best_candidate_content("output_1")
    c2_best = state_machine.get_best_candidate_content("output_2")
    c3_best = state_machine.get_best_candidate_content("output_3")
    if c1_best and modality != CanonicalModality.MEETING_NOTES:
        synthesis_result.output_1 = c1_best
        if hasattr(state_machine, "freeze_artifact"):
            state_machine.freeze_artifact("output_1", c1_best)
    if c2_best:
        synthesis_result.output_2 = c2_best
        if hasattr(state_machine, "freeze_artifact"):
            state_machine.freeze_artifact("output_2", c2_best)
    if c3_best:
        synthesis_result.output_3 = c3_best
        if hasattr(state_machine, "freeze_artifact"):
            state_machine.freeze_artifact("output_3", c3_best)
    return True


def validate_pre_synthesis_constraints(
    modality: CanonicalModality,
    source_text: str,
    metadata: Dict[str, Any],
    out_p: Any,
) -> None:
    """Validates knowable package and source constraints before costly synthesis."""
    if not isinstance(source_text, str) or not source_text.strip():
        raise ValueError("Pre-synthesis constraint violation: source text is empty or invalid.")

    project = metadata.get("project")
    if project is not None and (not isinstance(project, str) or not project.strip()):
        raise ValueError("Pre-synthesis constraint violation: project must be a non-empty string if provided.")

    if metadata.get("output2_only") and metadata.get("output3_only"):
        raise ValueError("Pre-synthesis constraint violation: contradictory options output2_only and output3_only.")

    if metadata.get("output3_only") and modality == CanonicalModality.MEETING_NOTES:
        raise ValueError("Pre-synthesis constraint violation: contradictory options for meeting notes.")


def compute_candidate_digest(synthesis_result: Any) -> str | None:
    """Computes a SHA256 digest of synthesized candidate artifacts."""
    if synthesis_result is None:
        return None
    hasher = hashlib.sha256()
    for art in (
        getattr(synthesis_result, "output_1", None),
        getattr(synthesis_result, "output_2", None),
        getattr(synthesis_result, "output_3", None),
    ):
        hasher.update((art or "").encode("utf-8"))
    return hasher.hexdigest()


def prepare_synthesis_attempts(
    modality: CanonicalModality,
    source_text: str,
    metadata: Dict[str, Any],
    out_p: Any,
) -> None:
    """Validates pre-synthesis constraints and resets attempts/ only on deliberate fresh reset."""
    validate_pre_synthesis_constraints(modality, source_text, metadata, out_p)
    from dual_pipeline.canonical_pipeline.attachments import is_deliberate_reset
    attempts_dir = out_p / "attempts" if hasattr(out_p, "joinpath") or hasattr(out_p, "__truediv__") else None
    if attempts_dir and attempts_dir.exists():
        import shutil
        if is_deliberate_reset(metadata):
            shutil.rmtree(attempts_dir, ignore_errors=True)
        elif not metadata.get("resume"):
            att1 = attempts_dir / "attempt_1"
            for p in sorted(attempts_dir.iterdir(), key=lambda d: d.name, reverse=True):
                if p.is_dir() and p.name.startswith("attempt_"):
                    try:
                        num = int(p.name.split("_")[1])
                        if num > 1:
                            j_file = p / "llm_judge_evaluation.json"
                            if j_file.is_file() and att1.is_dir():
                                j_data = json.loads(j_file.read_text(encoding="utf-8"))
                                o3_ev = j_data.get("output_3_evaluation") or {}
                                o2_ev = j_data.get("output_2_evaluation") or {}
                                if (
                                    o3_ev.get("failure_type") == "TIMEOUT"
                                    or "ERR_JUDGE_TIMEOUT" in (o3_ev.get("defect_codes") or [])
                                    or o2_ev.get("pass") is True
                                ):
                                    for f in p.iterdir():
                                        if f.is_file():
                                            shutil.copy2(f, att1 / f.name)
                                    att1 = Path()  # Only promote the latest attempt once
                            shutil.rmtree(p, ignore_errors=True)
                    except (ValueError, IndexError, Exception):
                        pass


def check_candidate_loop_termination(
    attempt: int,
    synthesis_result: Any,
    prior_digest: str | None,
) -> tuple[bool, str | None]:
    """Terminates iterative loop if subsequent attempt generates identical artifacts as prior failed attempt."""
    cand_digest = compute_candidate_digest(synthesis_result)
    if attempt > 1 and cand_digest is not None and cand_digest == prior_digest:
        return True, cand_digest
    return False, cand_digest


def handle_identical_candidate_fallback(
    attempt: int,
    max_attempts: int,
    metadata: Dict[str, Any],
    pa_context: Dict[str, Any],
    synthesis_strategy: Any,
    source_text: str,
    current_directive: Optional[str],
    prior_digest: Optional[str],
) -> tuple[bool, Any, Optional[str]]:
    """Falls back to full regeneration with judge feedback when candidate is identical to prior attempt."""
    if metadata.get("_did_full_regeneration_fallback"):
        logger.warning(
            "Attempt %d/%d produced identical candidate artifacts as prior failed attempt; "
            "terminating synthesis loop to eliminate futile LLM/judge cycles.",
            attempt, max_attempts,
        )
        return False, None, prior_digest

    metadata["_did_full_regeneration_fallback"] = True
    metadata["force_stage01_escalation"] = True
    pa_context["force_stage01_escalation"] = True
    logger.warning(
        "Attempt %d/%d produced identical candidate artifacts as prior failed attempt; "
        "falling back to full regeneration with judge feedback before aborting.",
        attempt, max_attempts,
    )
    metadata.pop("document_patch_request", None)
    pa_context.pop("document_patch_request", None)
    metadata.pop("prior_graph_candidate", None)
    pa_context.pop("prior_graph_candidate", None)
    new_result = synthesis_strategy.synthesize(
        source_text=source_text,
        pa_context=pa_context,
        metadata=metadata,
        attempt=attempt,
        remediation_directive=current_directive,
    )
    new_digest = compute_candidate_digest(new_result)
    if new_digest == prior_digest:
        logger.warning(
            "Full regeneration fallback also produced identical candidate artifacts; terminating loop."
        )
        return False, new_result, new_digest
    return True, new_result, new_digest


def inject_prior_candidates_into_context(
    state_machine: Any,
    metadata: Dict[str, Any],
    pa_context: Dict[str, Any],
) -> None:
    """Inject prior candidates into context for targeted differential patching."""
    cand_recap = state_machine.get_prior_candidate("output_2")
    if cand_recap:
        metadata["prior_recap_candidate"] = cand_recap
        pa_context["prior_recap_candidate"] = cand_recap
    cand_graph = state_machine.get_prior_candidate("output_3")
    if cand_graph:
        metadata["prior_graph_candidate"] = cand_graph
        pa_context["prior_graph_candidate"] = cand_graph


def get_prior_attempt_count(out_p: Any, metadata: Dict[str, Any]) -> int:
    """Calculates cumulative attempts across prior executions so restarts do not reset attempt counters."""
    if metadata.get("prior_attempts_count") is not None:
        return int(metadata["prior_attempts_count"])
    attempts_dir = out_p / "attempts" if hasattr(out_p, "joinpath") or hasattr(out_p, "__truediv__") else None
    if attempts_dir and attempts_dir.exists():
        prior_folders = [p for p in attempts_dir.iterdir() if p.is_dir() and p.name.startswith("attempt_")]
        return len(prior_folders)
    return 0


def verify_approval_not_stale(
    metadata: Dict[str, Any],
    current_deliverable_digest: str,
) -> None:
    """Fails closed when an earlier approval is attempted to be reused against modified deliverables or on restart."""
    if metadata.get("is_stale_approval") is True:
        raise ValueError("STALE_APPROVAL_REUSED: Stale approval flag set; approval cannot be reused.")
    approval_rec = metadata.get("approval_receipt") or metadata.get("mutation_approval_receipt")
    if approval_rec and isinstance(approval_rec, dict):
        bound_digest = approval_rec.get("proposal_digest")
        if bound_digest and bound_digest != current_deliverable_digest:
            raise ValueError(
                f"STALE_APPROVAL_REUSED: Approval digest '{bound_digest[:12]}' does not match current candidate digest '{current_deliverable_digest[:12]}'."
            )


def _has_stage_ckpt(sub: Path, art_key: str) -> bool:
    fname = {"output_1": "output_1.ckpt.md", "output_2": "output_2.ckpt.md"}.get(art_key, "output_3.ckpt.json")
    p = sub / fname
    return p.is_file() and p.stat().st_size > 200


def _seed_prior_candidates(sub: Path, art_key: str, metadata: Dict[str, Any], pa_context: Dict[str, Any]) -> None:
    if art_key == "output_2" and not metadata.get("prior_recap_candidate"):
        ckpt_2 = sub / "output_2.ckpt.md"
        if ckpt_2.is_file() and ckpt_2.stat().st_size > 200:
            c2_txt = ckpt_2.read_text(encoding="utf-8")
            metadata["prior_recap_candidate"] = c2_txt
            pa_context["prior_recap_candidate"] = c2_txt
    if art_key == "output_3" and not metadata.get("prior_graph_candidate"):
        ckpt_3 = sub / "output_3.ckpt.json"
        if ckpt_3.is_file() and ckpt_3.stat().st_size > 500:
            c3_txt = ckpt_3.read_text(encoding="utf-8")
            metadata["prior_graph_candidate"] = c3_txt
            pa_context["prior_graph_candidate"] = c3_txt


def seed_regeneration_remediation_directives(
    out_p: Any, state_machine: Any, metadata: Dict[str, Any], pa_context: Dict[str, Any],
) -> None:
    """Injects prior attempt directives when selective regeneration is triggered."""
    import json
    import re
    from pathlib import Path
    from dual_pipeline.remediation_core import RemediationDiffInjector

    att_dir = Path(out_p) / "attempts" if not isinstance(out_p, Path) else out_p / "attempts"
    if not att_dir.is_dir():
        return

    def _get_att_num(p: Path) -> int:
        m = re.search(r"attempt_(\d+)", p.name)
        return int(m.group(1)) if m else 0

    attempt_subdirs = sorted(
        [sub for sub in att_dir.iterdir() if sub.is_dir() and "attempt_" in sub.name],
        key=_get_att_num,
        reverse=True,
    )
    for sub in attempt_subdirs:
        j_path = sub / "llm_judge_evaluation.json"
        q_path = sub / "qa_gates_report.json"
        if j_path.exists() and q_path.exists():
            try:
                j_rep = json.loads(j_path.read_text(encoding="utf-8"))
                q_rep = json.loads(q_path.read_text(encoding="utf-8"))
                metadata.setdefault("last_judge_report", j_rep)
                metadata.setdefault("last_qa_report", q_rep)
                pa_context.setdefault("last_judge_report", j_rep)
                pa_context.setdefault("last_qa_report", q_rep)
                for art_key in ("output_1", "output_2", "output_3"):
                    if art_key not in state_machine.frozen_artifacts and _has_stage_ckpt(sub, art_key):
                        ev_sub = j_rep.get(f"{art_key}_evaluation") or {}
                        if ev_sub.get("failure_type") == "TIMEOUT" or "ERR_JUDGE_TIMEOUT" in (ev_sub.get("defect_codes") or []):
                            continue
                        rem_k = f"remediation_directive_{'forensic' if art_key == 'output_1' else ('recap' if art_key == 'output_2' else 'graph')}"
                        if not metadata.get(rem_k):
                            d = RemediationDiffInjector.extract_directive(art_key, j_rep, q_rep)
                            if d:
                                metadata[rem_k] = d
                                pa_context[rem_k] = d
                        _seed_prior_candidates(sub, art_key, metadata, pa_context)
                break
            except Exception:
                pass


def check_deliverable_01_deploy_action(
    f1_path: Path, out_p: Path, synthesis_result: Any, meta: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    Determines whether Deliverable 01 should be written to disk, preventing overwrite
    of an already sealed or frozen Deliverable 01 unless explicitly regenerated.
    Returns (should_write, content_to_write).
    """
    import hashlib
    content = getattr(synthesis_result, "output_1", None) or ""
    if not f1_path.exists():
        return True, content

    cur_text = f1_path.read_text(encoding="utf-8")
    cur_sha = hashlib.sha256(cur_text.encode("utf-8")).hexdigest()
    regen_spec = str(meta.get("regenerate") or "").lower()
    explicit_regen_01 = "01" in regen_spec or "output_1" in regen_spec

    # Preserve existing Deliverable 01 when explicitly frozen (Track 1)
    if meta.get("freeze_output_1") and not explicit_regen_01:
        return False, cur_text

    sm = meta.get("state_machine")
    if sm and hasattr(sm, "is_frozen") and sm.is_frozen("output_1") and not explicit_regen_01:
        frozen_content = sm.get_frozen_content("output_1")
        frozen_sha = getattr(sm, "get_frozen_sha256", lambda k: None)("output_1") or (
            hashlib.sha256(frozen_content.encode("utf-8")).hexdigest() if frozen_content else None
        )
        if not frozen_sha or cur_sha == frozen_sha:
            return False, cur_text
        if frozen_content:
            return False, cur_text

    seals_file = out_p / "stage_seals.json"
    if seals_file.exists() and not explicit_regen_01:
        try:
            sdata = json.loads(seals_file.read_text(encoding="utf-8"))
            seal_1 = sdata.get("output_1", {})
            if seal_1.get("status") == "PASS":
                if not seal_1.get("sha256") or cur_sha == seal_1.get("sha256"):
                    return False, cur_text
        except Exception:
            pass

    if meta.get("force_rerun") and (explicit_regen_01 or not regen_spec):
        return True, content

    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if content and content_sha != cur_sha:
        return True, content

    return False, cur_text


def purge_stale_meeting_deliverables_for_document(out_p: Path, modality: CanonicalModality) -> None:
    """Purge any stale 01_* or 02_* deliverables when deploying a document container."""
    if modality == CanonicalModality.DOCUMENT:
        for stale_p in list(out_p.glob("01_*_hitl_resolved_transcript.md")) + list(out_p.glob("02_*_executive_recap.md")):
            stale_p.unlink(missing_ok=True)





