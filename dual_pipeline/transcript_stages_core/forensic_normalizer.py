"""
dual_pipeline/transcript_stages_core/forensic_normalizer.py

Dedicated Stage 01: Forensic Normalizer & Transcript Assembly Agent.
Decoupled from L1 planning cognition pass to ensure strict separation of concerns:
- Native diarized assembly: speaker_map resolution & formatting.
- Undiarized forensic synthesis: epistemic filtering, stage prompt generation,
  LLM synthesis, and Ambiguity Lock Register compilation.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from .constants import _load_stage_prompt, count_native_diarized_turns
from .l1_attribution import (
    apply_speaker_map,
    assemble_native_hitl_transcript,
    check_generic_speakers,
)
from .lexical_normalizer import (
    _build_speaker_grounding_rows,
    format_ambiguity_lock_register,
    normalize_lexical_and_phonetics,
)
from .models import L2SynthesisProvider

logger = logging.getLogger(__name__)

_EXCLUDED_STAGE01_METADATA_KEYS = (
    "source_text",
    "raw_transcript",
    "resolved_transcript",
    "forensic_transcript",
    "recap",
    "operator_hypothesis",
    "executive_tensions",
    "tensions",
    "prior_context",
    "prior_recap_candidate",
    "prior_forensic_candidate",
    "prior_graph_candidate",
    "cross_meeting_xml",
    "presentation_path",
    "output_dir",
    "target_output_dir",
)


def _resolve_forensic_prompt_for_undiarized(metadata: Dict[str, Any], arch: Any) -> str:
    source_mod = str(metadata.get("source_modality") or metadata.get("source_type") or "").upper()
    platform = str(metadata.get("platform", "")).lower()
    if source_mod == "AUDIO_ONLY_UNDIARIZED" or "audio" in source_mod.lower():
        return _load_stage_prompt("audio_forensic", archetype=arch)
    if source_mod == "TEAMS_NATIVE_TRANSCRIPT" or (
        "teams" in platform and "cache" not in str(metadata.get("input_path", ""))
    ):
        return _load_stage_prompt("teams_forensic", archetype=arch)
    return _load_stage_prompt("forensic", archetype=arch)


def _build_undiarized_dynamic_blocks(
    metadata: Dict[str, Any],
    roster: List[Any],
    supplementary: List[Any],
    remediation_directive: Optional[str],
) -> List[str]:
    stage01_metadata = {
        k: v for k, v in metadata.items()
        if k not in _EXCLUDED_STAGE01_METADATA_KEYS and not callable(v)
    }
    blocks = [
        f"<meeting_metadata>\n{json.dumps(stage01_metadata, separators=(',', ':'), default=str)}\n</meeting_metadata>",
        f"<roster>\n{json.dumps(roster, separators=(',', ':'), default=str)}\n</roster>"
    ]
    lexicon_map = metadata.get("lexicon_map") or metadata.get("domain_lexicon")
    if lexicon_map:
        blocks.append(f"<domain_lexicon>\n{json.dumps(lexicon_map, indent=2)}\n</domain_lexicon>")
    known_entities = metadata.get("known_graph_entities")
    if known_entities:
        metadata["graph_grounding_status"] = "GROUNDED"
        blocks.append(
            f"<project_resources>\n"
            f"Verified Project Resources from Canonical Knowledge Graph:\n"
            f"{json.dumps(known_entities, indent=2)}\n"
            f"INSTRUCTION: Use these verified names, roles, and affiliations to accurately identify and spell in-dialogue resource references and resolve ambiguous phonetic mentions.\n"
            f"</project_resources>"
        )
    else:
        metadata["graph_grounding_status"] = "UNGROUNDED"
    if supplementary:
        supp_parts = [
            s.get("content", str(s)) if isinstance(s, dict) else str(s)
            for s in supplementary
        ]
        blocks.append(f"<supplementary_sources>\n{chr(10).join(supp_parts)}\n</supplementary_sources>")
    if remediation_directive:
        logger.info("Injecting remediation_directive into Stage 01 forensic normalizer.")
        blocks.append(
            f"<remediation_directive>\nPREVIOUS ATTEMPT FAILED. FIX THE FOLLOWING:\n"
            f"{remediation_directive}\n</remediation_directive>"
        )
    return blocks


def _evaluate_speaker_collapse(
    transcript: str, metadata: Dict[str, Any]
) -> tuple[int, set[str], int, bool]:
    from dual_pipeline.transcript_pipeline_core.source_modality import extract_unique_speakers
    from dual_pipeline.transcript_stages_core.constants import count_native_diarized_turns

    roster = metadata.get("roster") or metadata.get("attendees") or []
    if isinstance(roster, list):
        roster_count = len(roster)
    elif isinstance(roster, dict):
        roster_count = sum(len(v) for v in roster.values() if isinstance(v, list))
    else:
        roster_count = 0
    speaker_map = metadata.get("speaker_map") or {}
    check_transcript = apply_speaker_map(transcript, speaker_map) if speaker_map else transcript
    hints = metadata.get("speaker_hints") or metadata.get("turn_hints")
    if hints and isinstance(hints, list):
        from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import (
            apply_quote_hints_to_turns,
        )
        check_transcript, _ = apply_quote_hints_to_turns(check_transcript, hints)
    unique_spk = extract_unique_speakers(check_transcript)
    native_turn_count = count_native_diarized_turns(check_transcript)
    return roster_count, unique_spk, native_turn_count, (roster_count >= 2 and len(unique_spk) <= 1)


class ForensicNormalizer:
    """
    Forensic Normalizer Agent (Stage 01).
    Produces Artifact 01 (hitl_resolved_transcript.md).
    """

    def execute(
        self,
        u0_context: Dict[str, Any],
        plan: Any,
        provider: L2SynthesisProvider,
        remediation_directive: Optional[str] = None,
    ) -> str:
        """
        Stage 01 execution: forensic normalization.
        - NATIVE path: apply speaker_map resolving, build transcript.
        - UNDIARIZED path: delegate to provider using the forensic stage prompt.
        """
        logger.info("Executing Forensic Normalizer Agent (Stage 01)")
        metadata = u0_context.setdefault("metadata", {})

        # Ensure date format is canonical MM/DD/YYYY in metadata
        raw_date = str(metadata.get("date", ""))
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
            parts = raw_date.split("-")
            metadata["date"] = f"{parts[1]}/{parts[2]}/{parts[0]}"
        elif re.match(r"^\d{2}-\d{2}-\d{4}$", raw_date):
            metadata["date"] = raw_date.replace("-", "/")

        transcript = normalize_lexical_and_phonetics(u0_context["clean_transcript"], metadata)

        from dual_pipeline.transcript_stages_core.metric_discrepancy_evaluator import (
            evaluate_metric_discrepancies,
        )
        evaluate_metric_discrepancies(
            transcript,
            metadata,
            interactive_resolver=metadata.get("hitl_metric_resolver"),
        )

        roster_count, unique_spk, native_turn_count, is_collapsed = _evaluate_speaker_collapse(
            transcript, metadata
        )
        roster_raw = metadata.get("roster") or metadata.get("attendees") or []
        speaker_map = metadata.get("speaker_map") or {}
        check_transcript = apply_speaker_map(transcript, speaker_map) if speaker_map else transcript

        from dual_pipeline.speaker_attribution_core.collapse_detector import (
            check_speaker_skew_and_collapse,
            verify_collapse_approval_in_ledger,
        )
        if roster_count >= 2 and native_turn_count >= 3:
            check_speaker_skew_and_collapse(check_transcript, roster_raw, metadata)

        diarization_status = getattr(plan, "diarization_status", None) or metadata.get("diarization_status")
        if not diarization_status:
            diarization_status = "NATIVE" if native_turn_count >= 3 else "UNDIARIZED"

        collapse_verified = verify_collapse_approval_in_ledger(metadata) if is_collapsed else False
        if (
            is_collapsed
            and len(unique_spk) == 1
            and native_turn_count >= 3
            and not collapse_verified
        ):
            from .models import HITLRequiredError
            sole_speaker = next(iter(unique_spk))
            raise HITLRequiredError(
                f"Single-speaker collapse detected ({sole_speaker} holds 100% of {native_turn_count} turns "
                f"across {roster_count} roster attendees). Halting autonomous re-attribution per HITL Invariant 7."
            )

        force_escalation = bool(metadata.get("force_stage01_escalation")) and provider is not None
        if (
            diarization_status in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED", "VERIFIED_STEREO_SEPARATION")
            and (not is_collapsed or collapse_verified)
            and not force_escalation
        ):
            raw_out = self._execute_native(transcript, metadata, remediation_directive=remediation_directive)
        else:
            raw_out = self._execute_undiarized(transcript, metadata, provider, remediation_directive)
        return self._post_process_forensic(raw_out, metadata)

    def _post_process_forensic(self, text: str, metadata: Dict[str, Any]) -> str:
        """Real-time Stage 01 post-processor: applies lossless syntactic healing and ASR cleanup."""
        if not text:
            return text
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        healed, changes = DeterministicHealer.heal_output_1(text, metadata=metadata)
        if changes:
            logger.info(
                "Stage 01 Real-Time Healing: %d adjustments applied (%s)",
                len(changes),
                "; ".join(changes[:3]),
            )
        from .silence_gap_classifier import classify_silence_gaps
        healed, _ = classify_silence_gaps(healed, metadata, interactive_resolver=metadata.get("hitl_gap_resolver"))
        return healed

    @staticmethod
    def _is_stitchable_turn(
        spk: str,
        prev_spk: str,
        curr_sec: Optional[int],
        prev_last_sec: Optional[int],
        prev_first_sec: Optional[int],
        max_gap: float,
    ) -> bool:
        if spk.strip().lower() != prev_spk.strip().lower() or curr_sec is None or prev_last_sec is None:
            return False
        if not (0 <= (curr_sec - prev_last_sec) <= max_gap):
            return False
        return prev_first_sec is None or (curr_sec - prev_first_sec) <= 10.0

    @staticmethod
    def _is_stitchable_orphan(
        spk: str,
        prev_spk: str,
        body: str,
        curr_sec: Optional[int],
        prev_last_sec: Optional[int],
        prev_first_sec: Optional[int],
    ) -> bool:
        if not ForensicNormalizer._is_stitchable_turn(spk, prev_spk, curr_sec, prev_last_sec, prev_first_sec, 8.0):
            return False
        stripped = body.strip()
        if stripped.endswith("?"):
            return False
        tokens = stripped.split()
        if len(tokens) > 2:
            return False
        return stripped[0].islower() or stripped.lower() in ("the", "it", "overall", "that", "and", "months.", "so")

    @staticmethod
    def _stitch_acoustic_micro_turns(text: str, max_stitch_gap_sec: float = 5.0) -> str:
        """Stitches brief acoustic micro-turns of the same continuous speaker back into the surrounding turn while conserving all words, respecting intervening speakers, absorbing 1-2 word orphan fragments, and preserving turn timestamps."""
        from dual_pipeline.transcript_qa_core.gate1 import _parse_timestamp_seconds
        from dual_pipeline.transcript_qa_core.gate1_helpers import _extract_all_turns, UNIVERSAL_TURN_PATTERN
        turns = _extract_all_turns(text)
        if not turns:
            return text

        first_m = None
        for m in UNIVERSAL_TURN_PATTERN.finditer(text):
            ts_cand = m.group(1) or m.group(2) or m.group(4) or m.group(5)
            spk_cand = (m.group(3) or "").strip()
            if ts_cand or not spk_cand.startswith("-"):
                first_m = m
                break
        preamble = text[:first_m.start()] if first_m and first_m.start() > 0 else ""
        alr_m = re.search(r"(?:^|\n)##\s+Ambiguity(?:\s+Lock)?\s+Register\b.*", text, re.DOTALL | re.IGNORECASE)
        postamble = alr_m.group(0) if alr_m else ""

        stitched: List[Tuple[str, str, str, Optional[int]]] = []
        for ts, spk, body in turns:
            if not body.strip():
                continue
            curr_sec = _parse_timestamp_seconds(ts) if ts else None
            if stitched:
                prev_ts, prev_spk, prev_body, prev_last_sec = stitched[-1]
                prev_first_sec = _parse_timestamp_seconds(prev_ts) if prev_ts else None
                can_stitch = ForensicNormalizer._is_stitchable_turn(spk, prev_spk, curr_sec, prev_last_sec, prev_first_sec, max_stitch_gap_sec)
                can_absorb = ForensicNormalizer._is_stitchable_orphan(spk, prev_spk, body, curr_sec, prev_last_sec, prev_first_sec)
                if can_stitch or can_absorb:
                    stitched[-1] = (prev_ts, prev_spk, f"{prev_body} {body.strip()}", curr_sec)
                    continue

            stitched.append((ts, spk, body.strip(), curr_sec))

        out_turns = []
        for ts, spk, body, _ in stitched:
            if ts:
                out_turns.append(f"### [{ts}]\n\n**{spk}:** {body}")
            else:
                out_turns.append(f"**{spk}:** {body}")

        dialogue = "\n\n".join(out_turns)
        result = dialogue
        if preamble.strip():
            result = f"{preamble.rstrip()}\n\n{result}"
        if postamble.strip():
            result = f"{result}\n\n{postamble.strip()}\n"
        return result

    def _execute_native(
        self,
        transcript: str,
        metadata: Dict[str, Any],
        remediation_directive: Optional[str] = None,
    ) -> str:
        """Native diarized path: speaker_map resolution + transcript assembly + surgical retry remediation."""
        speaker_map = metadata.get("speaker_map", {})
        check_generic_speakers(
            transcript,
            speaker_map,
            source_type="DIARIZED_TEXT",
            metadata=metadata,
        )
        resolved_text = apply_speaker_map(transcript, speaker_map)
        hints = metadata.get("speaker_hints") or metadata.get("turn_hints")
        if hints and isinstance(hints, list):
            from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import (
                apply_quote_hints_to_turns,
            )
            resolved_text, _ = apply_quote_hints_to_turns(resolved_text, hints)
        from dual_pipeline.transcript_stages_core.lexical_normalizer import (
            _apply_phonetic_maps,
            _apply_speaker_turn_patches,
            _prune_cutoff_marker,
        )
        resolved_text = _prune_cutoff_marker(resolved_text, metadata.get("cutoff_marker"))
        resolved_text = _apply_phonetic_maps(resolved_text, metadata)
        resolved_text = _apply_speaker_turn_patches(resolved_text, metadata.get("speaker_turn_patches", []))
        from dual_pipeline.transcript_stages_core.organic_signal_scrubber import scrub_organic_signal
        resolved_text = scrub_organic_signal(resolved_text, preserve_verbatim_turns=True)
        if "### [" not in resolved_text:
            from dual_pipeline.transcript_pipeline_core.normalizer import normalize_raw_input_text
            norm_res = normalize_raw_input_text(resolved_text, metadata=metadata)
            resolved_text = getattr(norm_res, "text", str(norm_res))
        assembled = assemble_native_hitl_transcript(resolved_text, metadata, speaker_map)
        out = _ensure_alr_and_attribution_summary(assembled, metadata, speaker_map)
        eff_directive = remediation_directive or metadata.get("remediation_directive_forensic")
        last_qa = metadata.get("last_qa_report") or {}
        last_judge = metadata.get("last_judge_report") or {}
        if eff_directive or last_qa or last_judge:
            from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
                SurgicalRemediator,
            )
            remediated, did_rem, rem_audit = SurgicalRemediator.remediate_output_1(
                out,
                qa_report=last_qa,
                judge_report=last_judge,
                directive=str(eff_directive or ""),
                metadata=metadata,
            )
            if did_rem:
                logger.info(
                    "Applied surgical retry remediation on native Stage 01 path (%d adjustments)",
                    len(rem_audit),
                )
                out = remediated
        return out

    def _execute_undiarized(
        self,
        transcript: str,
        metadata: Dict[str, Any],
        provider: L2SynthesisProvider,
        remediation_directive: Optional[str] = None,
    ) -> str:
        """Undiarized path: delegates to the forensic stage prompt via provider."""
        speaker_map = metadata.get("speaker_map", {})
        source_type = metadata.get("source_type", "")
        check_generic_speakers(
            transcript,
            speaker_map,
            source_type=source_type,
            metadata=metadata,
        )

        resolved_transcript = apply_speaker_map(transcript, speaker_map)
        from dual_pipeline.speaker_disambiguation import resolve_shared_room_microphones
        resolved_transcript, metadata = resolve_shared_room_microphones(resolved_transcript, metadata, speaker_map)
        arch = metadata.get("archetype") or metadata.get("meeting_type")
        forensic_prompt = _resolve_forensic_prompt_for_undiarized(metadata, arch)
        roster = metadata.get("roster", [])
        supplementary = metadata.get("supplementary_sources", [])

        from dual_pipeline.prompts import OUTPUT1_CONTRACT, MeetingArchetype
        gov_ctx = OUTPUT1_CONTRACT.compile_governance_context(MeetingArchetype.from_string(arch))

        static_blocks = _build_undiarized_static_blocks(gov_ctx, metadata)
        dynamic_blocks = _build_undiarized_dynamic_blocks(metadata, roster, supplementary, remediation_directive)
        dynamic_blocks.append(f"<raw_transcript>\n{resolved_transcript}\n</raw_transcript>")

        system_prefix = f"{forensic_prompt}\n\n" + "\n\n".join(static_blocks)
        user_content = "\n\n".join(dynamic_blocks) + "\n\n"

        raw_res = provider.synthesize_forensic(system_prefix, user_content)
        out = apply_speaker_map(raw_res, speaker_map) if speaker_map else raw_res

        # Fail-fast check on generic speakers in synthesized Output 01
        check_generic_speakers(
            out,
            speaker_map or {},
            is_undiarized_audio=True,
            source_type=metadata.get("source_type", ""),
            metadata=metadata,
        )

        # Ensure canonical header date exists in H1 and metadata line
        canonical_date = str(metadata.get("date", "") or metadata.get("meeting_date", "")).strip()
        if canonical_date:
            canonical_date = canonical_date.replace("-", "/")
            header_m = re.search(r"(^#\s+[^\n]+)", out)
            if header_m and not re.search(r"\|\s*\d{2}[/-]\d{2}[/-]\d{4}", header_m.group(1)):
                new_h1 = f"{header_m.group(1).rstrip()} | {canonical_date}"
                out = out[:header_m.start()] + new_h1 + out[header_m.end():]

            if not re.search(r"(?:\*\*Date:\*\*|Date:)\s*\d{2}/\d{2}/\d{4}", out):
                header_line = re.search(r"(^#\s+[^\n]+\n)", out)
                if header_line:
                    out = out[:header_line.end()] + f"\n**Date:** {canonical_date}\n" + out[header_line.end():]
                else:
                    out = f"**Date:** {canonical_date}\n\n" + out

        return _ensure_alr_and_attribution_summary(out, metadata, speaker_map)


def _build_undiarized_static_blocks(gov_ctx: str, metadata: Dict[str, Any]) -> List[str]:
    blocks = [
        gov_ctx,
        (
            "<undiarized_execution_guidance>\n"
            "MANDATORY INSTRUCTION: This meeting source is undiarized audio. You MUST output all chronological dialogue turns covering the full meeting.\n"
            "When an individual voice cannot be conclusively distinguished, attribute the turn to the verified attendee roster or surface for HITL resolution.\n"
            "Generic speaker placeholders (e.g. 'Speaker 1', 'Unknown') are strictly prohibited without an explicit Ambiguity Lock Register entry.\n"
            "Under no circumstances should you emit a refusal, stopping note, or withhold transcript dialogue turns.\n"
            "</undiarized_execution_guidance>"
        ),
    ]
    if metadata.get("gate_f_certified") is True and metadata.get("audio_certificate") is not None:
        blocks.append(
            "<gate_f_certification>\n"
            "GATE F CERTIFICATION: PASS (Verified AudioCaptureCertificate present).\n"
            "Constraint C-30 is SATISFIED. You MUST synthesize the full, literal chronological transcript without withholding dialogue turns.\n"
            "</gate_f_certification>"
        )
    return blocks


def _ensure_alr_and_attribution_summary(out: str, metadata: Dict[str, Any], speaker_map: Dict[str, str]) -> str:
    from dual_pipeline.transcript_qa_core.gate1_attribution import (
        _extract_alr_table_rows,
        check_alr_row_count_minimum,
    )

    alr_match = re.search(r"(?:^|\n)#{1,3}\s+Ambiguity Lock Register\b[\s\S]*?(?=\n#{1,3}\s+|\Z)", out)
    if not alr_match:
        note = format_ambiguity_lock_register(metadata, speaker_map)
        out = f"{out.rstrip()}\n\n---\n## Ambiguity Lock Register\n\n{note}\n"
    else:
        passed, actual, min_rows, _ = check_alr_row_count_minimum(out, metadata)
        if not passed:
            note = format_ambiguity_lock_register(metadata, speaker_map)
            new_alr_sec = f"## Ambiguity Lock Register\n\n{note}\n"
            start, end = alr_match.start(), alr_match.end()
            prefix = "\n" if out[start:start+1] == "\n" else ""
            out = out[:start] + prefix + new_alr_sec + out[end:]

    roster = metadata.get("roster") or metadata.get("attendees") or []
    if isinstance(roster, list):
        roster_names = [r.get("name", "") if isinstance(r, dict) else str(r) for r in roster]
    elif isinstance(roster, dict):
        from .lexical_normalizer import _names_from_attendees
        roster_names = _names_from_attendees(roster)
    elif isinstance(roster, str):
        roster_names = [cleaned for part in re.split(r"[;,]", roster) if (cleaned := re.sub(r"\(.*?\)", "", part).strip())]
    else:
        roster_names = []

    if len(roster_names) >= 2:
        from .attribution_builder import AttributionSummaryBuilder
        built_summary = AttributionSummaryBuilder.build_summary(metadata, dialogue_text=out, roster_names=roster_names)
        if built_summary:
            sum_match = re.search(r"(?:^|\n)#{1,3}\s+Attribution Summary\b[\s\S]*?(?=\n#{1,3}\s+(?!Attribution Summary)|\Z)", out, re.IGNORECASE)
            if sum_match:
                start, end = sum_match.start(), sum_match.end()
                prefix = "\n\n" if out[start:start+1] == "\n" else ""
                out = out[:start].rstrip() + prefix + built_summary.strip() + out[end:]
            else:
                out = f"{out.rstrip()}\n\n{built_summary.strip()}\n"

    return out
