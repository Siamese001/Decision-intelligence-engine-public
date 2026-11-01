"""
dual_pipeline/transcript_pipeline_core/context_builder.py

Stage C0 Context Gate, Speaker Attribution Disambiguation, Remediation Seeding,
and Stage PA Packet Assembly modular engine.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from dual_pipeline.canonical_pipeline.context_binding import BoundedContext
from typing import Any, Dict, Optional, Tuple

from .statement_filter import filter_stage03_statement_payload

logger = logging.getLogger(__name__)


def _execute_c0_phase(
    plan: Any,
    u0_context: Dict[str, Any],
    l0_context: Dict[str, Any],
    transcript_text: str,
    c0_native: Any,
    c0_undiarized: Any,
    auto_scaffolder: Any,
) -> Dict[str, Any]:
    if plan.diarization_status in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED", "VERIFIED_STEREO_SEPARATION"):
        c0_res = c0_native.execute(l0_context)
    else:
        pre_l0_context = {
            "transcript": transcript_text,
            "hash": u0_context["hash"],
            "metadata": u0_context["metadata"],
            "statements": [],
            "diarization_status": plan.diarization_status,
            "source_type": plan.source_type,
            "meeting_type_hint": plan.meeting_type_hint,
        }

        if plan.roster_gate_needed and not u0_context["metadata"].get("ingestion_context"):
            scaffold_data = auto_scaffolder.execute(transcript_text)
            if scaffold_data:
                logger.info("Auto-scaffolder successfully provided fallback ingestion context.")
                pre_l0_context["metadata"]["ingestion_context"] = scaffold_data

        c0_context = c0_undiarized.execute(pre_l0_context)
        l0_context["conformance_checked"] = c0_context.get("conformance_checked", True)
        l0_context["roster_gate_cleared"] = c0_context.get("roster_gate_cleared", True)
        l0_context["metadata"] = c0_context.get("metadata", u0_context["metadata"])
        u0_context["metadata"] = l0_context["metadata"]
        c0_res = c0_context

    u0_context["metadata"] = l0_context["metadata"]
    c0_res["metadata"] = l0_context["metadata"]
    return c0_res


def _sync_attributed_speakers_to_roster(metadata: Dict[str, Any]) -> None:
    """Ensure newly attributed speakers from speaker_map are represented in roster and pre_enrichment_roles."""
    speaker_map = metadata.get("speaker_map")
    if not isinstance(speaker_map, dict) or not speaker_map:
        return
    from dual_pipeline.transcript_stages_core.c0_gates import _extract_role_from_string
    roster = metadata.get("roster")
    if not isinstance(roster, list):
        roster = []
        metadata["roster"] = roster
    existing_names = {
        str(r.get("name", "")).strip().lower()
        for r in roster
        if isinstance(r, dict) and r.get("name")
    }
    pre_roles = metadata.setdefault("pre_enrichment_roles", {})
    for mapped_val in speaker_map.values():
        if not isinstance(mapped_val, str) or not mapped_val.strip():
            continue
        name, role = _extract_role_from_string(mapped_val.strip())
        clean_name = name.strip()
        if not clean_name or clean_name.upper().startswith("SPEAKER_"):
            continue
        if clean_name.lower() not in existing_names:
            roster.append({"name": clean_name, "role": role or "Participant"})
            existing_names.add(clean_name.lower())
        if clean_name not in pre_roles:
            pre_roles[clean_name] = role or "Participant"


def _refresh_graph_prior_context_after_c0(metadata: Dict[str, Any], transcript_text: str) -> None:
    """Refresh OfflineGraphContextRetriever after C0 roster resolution so newly resolved attendees populate known_graph_entities."""
    try:
        from dual_pipeline.query_core.offline_retriever import OfflineGraphContextRetriever
        retriever = OfflineGraphContextRetriever()
        packet = retriever.retrieve_meeting_prior_context(
            project_name=metadata.get("project_name") or metadata.get("project"),
            attendees=metadata.get("attendees") or metadata.get("roster"),
            date_str=metadata.get("date"),
            transcript_text=transcript_text or metadata.get("clean_transcript") or metadata.get("transcript_text"),
        )
        if packet is not None and not packet.is_empty:
            metadata["graph_context_packet"] = packet.to_dict()
            metadata["graph_lexical_anchors"] = packet.lexical_terms
            existing_known = list(metadata.get("known_graph_entities") or [])
            seen_ids = {e.get("node_id") for e in existing_known if isinstance(e, dict) and e.get("node_id")}
            for e in packet.entities:
                if e.node_id not in seen_ids:
                    existing_known.append({
                        "node_id": e.node_id,
                        "canonical_name": e.canonical_name,
                        "display_name": e.display_name,
                        "node_type": e.node_type,
                        "organization": e.organization,
                        "role": e.role,
                    })
                    seen_ids.add(e.node_id)
            metadata["known_graph_entities"] = existing_known
            if packet.formatted_prior_context:
                prior_ctx = str(metadata.get("prior_context") or "")
                if packet.formatted_prior_context not in prior_ctx:
                    metadata["prior_context"] = (
                        (prior_ctx + "\n\n" if prior_ctx else "")
                        + f"--- CANONICAL GRAPH PRIOR CONTEXT ---\n{packet.formatted_prior_context}"
                    )
    except Exception as exc:
        logger.debug("Post-C0 graph context refresh skipped: %s", exc)


def _run_post_attribution_title_and_role_gates(
    u0_context: Dict[str, Any],
    l0_context: Dict[str, Any],
    c0_context: Dict[str, Any],
    transcript_text: str,
) -> None:
    _sync_attributed_speakers_to_roster(l0_context["metadata"])
    l0_context["transcript"] = transcript_text
    l0_context["transcript_text"] = transcript_text
    from dual_pipeline.transcript_stages_core.c05_title_gate import C05TitleCompletenessGate
    from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
        C06GraphDBRoleVerificationGate,
    )
    C05TitleCompletenessGate().execute(l0_context)
    C06GraphDBRoleVerificationGate().execute(l0_context)
    _refresh_graph_prior_context_after_c0(l0_context["metadata"], transcript_text)
    u0_context["metadata"] = l0_context["metadata"]
    c0_context["metadata"] = l0_context["metadata"]


def _check_is_pre_resolved(transcript_text: str, input_path: Optional[str]) -> bool:
    input_p = Path(input_path) if input_path else None
    from dual_pipeline.llm_judge_core.judges.forensic import check_generic_speaker_ratio
    is_excessive_generic, _, _, _ = check_generic_speaker_ratio(transcript_text)
    if is_excessive_generic:
        return False
    if input_p and ("01_" in input_p.name and "hitl_resolved_transcript" in input_p.name):
        return True
    markers = ("HITL-Resolved Transcript", "Verbatim Forensic Transcript Feed", "Forensic Transcript")
    return "# " in transcript_text and any(m in transcript_text for m in markers)


def _resolve_speaker_attribution_if_needed(
    plan: Any,
    u0_context: Dict[str, Any],
    l0_context: Dict[str, Any],
    transcript_text: str,
    is_notes_mode: bool,
    is_pre_resolved_input: bool,
) -> str:
    interactive_resolver = u0_context["metadata"].pop("interactive_resolver", None)
    if is_notes_mode or is_pre_resolved_input:
        return transcript_text

    d_store = u0_context["metadata"].get("decision_store") or u0_context["metadata"].get("hitl_store")
    from dual_pipeline.speaker_attribution_core import AudioSpeakerAttributionEngine
    attribution_engine = AudioSpeakerAttributionEngine(decision_store=d_store)
    attribution_res = attribution_engine.analyze_and_resolve(
        transcript=transcript_text,
        metadata=u0_context["metadata"],
        interactive_resolver=interactive_resolver,
        autonomous_if_decisive=u0_context["metadata"].get("autonomous_attribution", True),
    )
    if attribution_res.speaker_map:
        u0_context["metadata"]["speaker_map"] = attribution_res.speaker_map
        l0_context["metadata"]["speaker_map"] = attribution_res.speaker_map
        transcript_text = attribution_res.resolved_transcript
        u0_context["clean_transcript"] = attribution_res.resolved_transcript
    return transcript_text


def resolve_c0_context_and_attribution(
    plan: Any,
    u0_context: Dict[str, Any],
    l0_context: Dict[str, Any],
    transcript_text: str,
    input_path: Optional[str],
    is_notes_mode: bool,
    c0_native: Any,
    c0_undiarized: Any,
    auto_scaffolder: Any,
) -> Tuple[Dict[str, Any], str, bool]:
    """
    Executes Stage C0 context gates, auto-scaffolding, and speaker attribution.
    """
    c0_context = _execute_c0_phase(
        plan, u0_context, l0_context, transcript_text, c0_native, c0_undiarized, auto_scaffolder
    )
    is_pre_resolved_input = _check_is_pre_resolved(transcript_text, input_path)
    transcript_text = _resolve_speaker_attribution_if_needed(
        plan, u0_context, l0_context, transcript_text, is_notes_mode, is_pre_resolved_input
    )
    _run_post_attribution_title_and_role_gates(u0_context, l0_context, c0_context, transcript_text)
    return c0_context, transcript_text, is_pre_resolved_input


def _pin_prior_output1_evaluation(
    output_dir: Optional[str],
    transcript_text: str,
    metadata: Dict[str, Any],
) -> None:
    judge_file = Path(output_dir) / "llm_judge_evaluation.json" if output_dir else None
    if not (judge_file and judge_file.is_file()):
        return
    try:
        from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY

        o1_files = sorted(Path(output_dir).glob("01_*_hitl_resolved_transcript.md")) if output_dir else []
        disk_t = o1_files[0].read_text(encoding="utf-8") if o1_files else None
        if disk_t:
            healed_sha = hashlib.sha256(disk_t.encode("utf-8")).hexdigest()
        else:
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            healed_t, _ = DeterministicHealer.heal_output_1(transcript_text, [])
            healed_sha = hashlib.sha256(healed_t.encode("utf-8")).hexdigest()
        raw_sha = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()

        with open(judge_file, "r", encoding="utf-8") as jf:
            prior_eval = json.load(jf)
        pinned_evals = metadata.setdefault("pinned_evaluations", {})
        p_out1 = prior_eval.get("output_1_evaluation")
        if (
            isinstance(p_out1, dict)
            and p_out1.get("verdict") == "PASS"
            and p_out1.get("composite_score", 0) >= CANONICAL_RELEASE_POLICY.output1_pass_threshold
        ):
            pinned_evals["output_1"] = dict(p_out1, artifact_sha256=healed_sha, content_sha256=healed_sha, raw_sha256=raw_sha)
            logger.info(f"Pinned prior passing output_1 evaluation (score={p_out1.get('composite_score')}) for sha256={healed_sha[:10]}...")

        p_out2 = prior_eval.get("output_2_evaluation")
        if (
            isinstance(p_out2, dict)
            and p_out2.get("verdict") == "PASS"
            and p_out2.get("composite_score", 0) >= CANONICAL_RELEASE_POLICY.output2_pass_threshold
            and output_dir
        ):
            for f in sorted(Path(output_dir).glob("02_*_executive_recap.md")):
                f2_t = f.read_text(encoding="utf-8")
                f2_sha = hashlib.sha256(f2_t.encode("utf-8")).hexdigest()
                pinned_evals["output_2"] = dict(p_out2, artifact_sha256=f2_sha, content_sha256=f2_sha)
                logger.info(f"Pinned prior passing output_2 evaluation (score={p_out2.get('composite_score')}) for sha256={f2_sha[:10]}...")
                break

        p_out3 = prior_eval.get("output_3_evaluation")
        if (
            isinstance(p_out3, dict)
            and p_out3.get("verdict") == "PASS"
            and p_out3.get("composite_score", 0) >= CANONICAL_RELEASE_POLICY.output3_pass_threshold
            and output_dir
        ):
            for f in sorted(Path(output_dir).glob("03_*_canonical_signal_projection.json")):
                f3_t = f.read_text(encoding="utf-8")
                f3_sha = hashlib.sha256(f3_t.encode("utf-8")).hexdigest()
                pinned_evals["output_3"] = dict(p_out3, artifact_sha256=f3_sha, content_sha256=f3_sha)
                logger.info(f"Pinned prior passing output_3 evaluation (score={p_out3.get('composite_score')}) for sha256={f3_sha[:10]}...")
                break
    except Exception as e:
        logger.warning(f"Could not load prior judge evaluation for pins: {e}")


def initialize_remediation_state_machine(
    metadata: Dict[str, Any],
    output_dir: Optional[str],
    max_judge_retries: int,
    is_pre_resolved_input: bool,
    is_notes_mode: bool,
    transcript_text: str = "",
) -> Any:
    """
    Initializes SelectiveRemediationStateMachine, seeds existing artifacts,
    handles forced reruns and augmentation, and freezes pre-resolved Output 01.
    """
    from dual_pipeline.remediation_core import SelectiveRemediationStateMachine

    effective_attempts = min(3, max(1, int(max_judge_retries) + 1))
    state_machine = SelectiveRemediationStateMachine(max_attempts=effective_attempts)
    if not metadata.get("force_rerun"):
        state_machine.seed_from_output_dir(output_dir, metadata.get("meeting_name"), metadata=metadata)

    should_unfreeze = bool(metadata.get("force_rerun") or metadata.get("augmentation_mode", False))
    if should_unfreeze and not metadata.get("resume", False):
        for art in ("output_1", "output_2", "output_3"):
            if state_machine.is_frozen(art):
                state_machine.unfreeze_artifact(art)
        logger.info("Force-rerun or Augmentation active: Unfroze prior artifacts to force clean L2 synthesis.")

    if metadata.get("regenerate"):
        state_machine.seed_for_selective_regeneration(output_dir, metadata["regenerate"], metadata)
        logger.info(f"Selective regeneration applied (--regenerate {metadata['regenerate']}).")


    if is_pre_resolved_input and not is_notes_mode:
        regen = metadata.get("regenerate")
        is_regen_01 = False
        if regen:
            from dual_pipeline.l0.output_spec import parse_regenerate_spec
            try:
                is_regen_01 = "output_1" in parse_regenerate_spec(regen)
            except Exception:
                pass
        if not (metadata.get("force_rerun") and (not regen or is_regen_01)):
            state_machine.freeze_artifact("output_1", transcript_text)
            logger.info(
                f"Pre-resolved transcript detected: froze output_1 ({len(transcript_text)} chars). "
                "Skipping Stage 01 LLM regeneration."
            )
            _pin_prior_output1_evaluation(output_dir, transcript_text, metadata)

    return state_machine


def assemble_pa_packet(
    pa_stage: Any,
    l0_context: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Assembles Stage PA Packet Assembly context."""
    return pa_stage.execute(l0_context, l0_context.get("metadata", metadata))


def _read_prior_artifact(temp_sm: Any, output_dir: str, artifact_key: str, glob_pat: str) -> Optional[str]:
    content = temp_sm.get_frozen_content(artifact_key)
    if not content and Path(output_dir).is_dir():
        cands = list(Path(output_dir).glob(glob_pat))
        if not cands:
            cands = [p for p in Path(output_dir).rglob(glob_pat) if not any(part in ("attempts", "checkpoints", ".local") for part in p.parts)]
        for cand in sorted(cands):
            try:
                return cand.read_text(encoding="utf-8")
            except Exception:
                pass
    return content


def _summarize_prior_output3(f3: str) -> str:
    try:
        import json
        p_data = json.loads(f3)
        muts = p_data.get("graph_mutations") if isinstance(p_data.get("graph_mutations"), dict) else {}
        entities = muts.get("entity_upserts") or p_data.get("entity_upserts") or p_data.get("entities") or []
        decisions = muts.get("decision_updates") or p_data.get("decision_updates") or p_data.get("decisions") or []
        m_lines = []
        if entities:
            e_ids = [
                str(e.get("id") or e.get("entity_id") or "")
                for e in entities
                if isinstance(e, dict) and (e.get("id") or e.get("entity_id"))
            ]
            m_lines.append(f"Prior Entities: {', '.join(e_ids[:20])}")
        if decisions:
            d_titles = [
                str(d.get("decision_id") or d.get("id") or d.get("claim_text", "")[:40])
                for d in decisions
                if isinstance(d, dict)
            ]
            m_lines.append(f"Prior Decisions: {', '.join(d_titles[:10])}")
        if m_lines:
            return "--- PRIOR OUTPUT 03 (Entity & Decision Manifest) ---\n" + "\n".join(m_lines)
    except Exception:
        pass
    return f"--- PRIOR OUTPUT 03 (Canonical Signal Projection Summary) ---\n{f3[:1000]}"


def load_prior_meeting_context(
    output_dir: str, metadata: Dict[str, Any], transcript_text: Optional[str] = None
) -> None:
    """Pre-load existing artifacts into prior_context when present (skip on force_rerun or invalidate_prior_outputs)."""
    scope = metadata.get("project") or metadata.get("tenant") or "GLOBAL"
    source_id = metadata.get("file_hash") or ""
    metadata["bounded_context"] = BoundedContext(
        authorized_scope=scope,
        source_ids=[source_id] if source_id else [],
        bounds={"max_tokens": 8000, "confidentiality": "STRICT"},
        availability_status="AVAILABLE",
    )
    prior_context_blocks = []
    if not metadata.get("force_rerun") and not metadata.get("invalidate_prior_outputs"):
        from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
        temp_sm = SelectiveRemediationStateMachine(max_attempts=1)
        temp_sm.seed_from_output_dir(output_dir, metadata.get("meeting_name"), metadata=metadata)

        f1 = _read_prior_artifact(temp_sm, output_dir, "output_1", "01_*_hitl_resolved_transcript.md")
        if f1:
            metadata["prior_forensic_transcript"] = f1
            # Do not dump full verbatim transcript into prior_context_blocks; record presence only
            prior_context_blocks.append(f"--- PRIOR OUTPUT 01 (Forensic Transcript Present: {len(f1.splitlines())} lines) ---")

        f2 = _read_prior_artifact(temp_sm, output_dir, "output_2", "02_*_executive_recap.md")
        if f2:
            metadata["prior_executive_recap"] = f2
            # Bounded recap summary: keep summary, preserving action items if truncated
            act_m = re.search(r"(?:##\s+(?:Action|Next Steps)[\s\S]*?)(?=\n##|\Z)", f2, re.IGNORECASE)
            act_s = f"\n\n{act_m.group(0)}" if (act_m and len(f2) > 1500) else ""
            f2_bounded = f2 if len(f2) <= 1500 else (f2[:1500] + "\n...[truncated]" + act_s)
            prior_context_blocks.append(f"--- PRIOR OUTPUT 02 (Executive Recap Summary) ---\n{f2_bounded}")

        f3 = _read_prior_artifact(temp_sm, output_dir, "output_3", "03_*_canonical_signal_projection.json")
        if f3 and temp_sm.is_frozen("output_3"):
            metadata["prior_canonical_projection"] = f3
        if f1 or f2 or (f3 and temp_sm.is_frozen("output_3")):
            metadata["has_prior_meeting_artifacts"] = True
            if f3 and temp_sm.is_frozen("output_3"):
                prior_context_blocks.append(_summarize_prior_output3(f3))

    from dual_pipeline.canonical_pipeline.parallel_orchestrator import (
        load_cross_and_graph_context_parallel,
    )
    load_cross_and_graph_context_parallel(metadata, prior_context_blocks, transcript_text)

    from .learned_priors_context import append_learned_priors_context
    append_learned_priors_context(metadata, prior_context_blocks)

    if prior_context_blocks:
        existing = metadata.get("prior_context", "")
        metadata["prior_context"] = (existing + "\n\n" if existing else "") + "\n\n".join(prior_context_blocks)
        logger.info("Loaded prior context blocks into metadata['prior_context'].")
