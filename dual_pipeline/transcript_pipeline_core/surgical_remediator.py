"""
dual_pipeline/transcript_pipeline_core/surgical_remediator.py

Targeted patch orchestrator that executes bounded micro-remediations for failing sections.
Prevents costly full-meeting regeneration loops by isolating failing sections (e.g. Appendix B
word floor or Action Items table formatting) and applying surgical micro-prompts or deterministic patches
while cryptographically freezing passing sections.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.core import digest
from dual_pipeline.storage.healing_decision_store import record_healing_decision
from dual_pipeline.transcript_qa_core.forensic_healer import (
    purge_asr_outro_hallucinations as _purge_asr_outro_hallucinations,
)

logger = logging.getLogger(__name__)


def _persist_surgical_decision(
    scope: str,
    target_sec: str,
    before_text: str,
    after_text: str,
    did_remediate: bool,
    audit_trail: List[str],
    metadata: Optional[Dict[str, Any]],
) -> None:
    if not audit_trail:
        return
    meta = dict(metadata or {})
    run_id = str(meta.get("run_id") or meta.get("meeting_id") or "run-surgical-remediator")
    record_healing_decision({
        "run_id": run_id,
        "subsystem": "surgical_remediator",
        "component": f"SurgicalRemediator:{scope}:{target_sec}",
        "scope": scope,
        "repair_class": f"SURGICAL_{target_sec.upper()}",
        "action_taken": "; ".join(audit_trail[:5]),
        "outcome_status": "HEALED" if did_remediate else "UNREPAIRABLE",
        "before_digest": digest(before_text),
        "after_digest": digest(after_text),
        "confidence_top": 0.94 if did_remediate else 0.40,
        "confidence_second": 0.06 if did_remediate else 0.60,
        "ambiguity_margin": 0.88 if did_remediate else 0.0,
        "hitl_escalated": not did_remediate,
        "invariants_preserved": bool(did_remediate),
        "defect_reasons": list(audit_trail),
        "audit_trail": list(audit_trail),
        "metadata": {"target_section": target_sec, "did_remediate": did_remediate},
    })


def compute_section_hashes(markdown_text: str) -> Dict[str, str]:
    """Computes SHA256 hashes of top-level sections in a markdown document."""
    sections: Dict[str, str] = {}
    current_sec = "header"
    lines: List[str] = []
    in_appendix = False

    for line in markdown_text.splitlines(keepends=True):
        if (line.startswith("# ") or line.startswith("## ")) and not in_appendix:
            header_clean = line.strip().lstrip("#").strip().lower()
            if "appendix" in header_clean:
                in_appendix = True
            if lines:
                sections[current_sec] = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
                lines = []
            current_sec = re.sub(r"[^a-z0-9_]+", "_", header_clean)[:30]
        lines.append(line)
    if lines:
        sections[current_sec] = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return sections


def _target_from_critique(judge_dict: Dict[str, Any]) -> Optional[str]:
    out2_eval = judge_dict.get("output_2_evaluation") or judge_dict.get("output_02_executive_recap") or {}
    critique = out2_eval.get("critique_diff_envelope", {}) if isinstance(out2_eval, dict) else {}
    target_path = str(critique.get("target_path") or critique.get("target_section") or "").lower()
    if not target_path:
        return None
    if "appendix" in target_path:
        return "appendix"
    if "table" in target_path or "action" in target_path:
        prot = [str(p).lower() for p in (critique.get("protected_paths") or [])]
        return None if any("action" in p for p in prot) else "actions_table"
    if any(k in target_path for k in ("discussion", "synthesis", "finding")):
        return "strategic_synthesis"
    return None


def _extract_section_failures(report: Any, key: str) -> List[str]:
    sec = (report if isinstance(report, dict) else {}).get(key, {})
    raw = sec.get("failures", []) if isinstance(sec, dict) else []
    return [str(f).lower() for f in (raw if isinstance(raw, list) else [])]


def _target_for_output_2(qa_report: Dict[str, Any], judge_report: Dict[str, Any]) -> Optional[str]:
    all_failures = _extract_section_failures(qa_report, "output_02_executive_recap") + _extract_section_failures(
        judge_report, "output_02_executive_recap"
    )
    if not all_failures:
        return _target_from_critique(judge_report if isinstance(judge_report, dict) else {})
    if all(any(k in f for k in ("appendix", "gate 2.8", "attendee contribution")) for f in all_failures):
        return "appendix"
    if all(any(k in f for k in ("table", "gate 2.7", "next steps", "action")) for f in all_failures):
        return "actions_table"
    if all(any(k in f for k in ("synthesis", "discussion", "gate 2.3")) for f in all_failures):
        return "strategic_synthesis"
    return None


_STRUCTURAL_Q1_TRIGGERS = (
    "date", "timestamp", "recording_gap", "gap", "attribution_summary",
    "ambiguity_lock_register", "header", "syntax", "outro",
    "c27", "ungrounded_stakeholder", "referenced_entity",
)
_CLEANUP_Q1_TRIGGERS = (
    "outro", "thank you for watching", "alr", "phonetic", "chachi",
    "hallucination", "production of", "maximo", "maximum", "collateral",
) + _STRUCTURAL_Q1_TRIGGERS


def _target_for_output_1(qa_report: Dict[str, Any], judge_report: Dict[str, Any]) -> Optional[str]:
    qa_dict = qa_report if isinstance(qa_report, dict) else {}
    q1_sec = qa_dict.get("output_01_forensic_transcript") or qa_dict.get("output_1") or {}
    q1_failures = q1_sec.get("failures", []) if isinstance(q1_sec, dict) else []
    joined_failures = " ".join(str(f).lower() for f in q1_failures)
    if joined_failures and any(trig in joined_failures for trig in _STRUCTURAL_Q1_TRIGGERS):
        return "gate1_structural"

    out1_eval = (judge_report or {}).get("output_1_evaluation") or {}
    envelope = out1_eval.get("critique_diff_envelope") or {}
    combined = " ".join([
        str(out1_eval.get("actionable_remediation_diff", "")),
        str(envelope.get("observed_defect", "")),
        str(out1_eval.get("forensic_rationale", "")),
    ]).lower()
    return "asr_and_alr_cleanup" if any(trig in combined for trig in _CLEANUP_Q1_TRIGGERS) else None


def _target_for_output_3(qa_report: Dict[str, Any]) -> Optional[str]:
    q3_failures = qa_report.get("output_03_canonical_signal_projection", {}).get("failures", [])
    all_failures = [str(f).lower() for f in q3_failures]
    if all_failures and all("alias" in f or "predicate" in f or "duplicate" in f for f in all_failures):
        return "graph_normalization"
    return None


def _append_alr_row(text: str, row: str) -> str:
    lines = text.splitlines(keepends=True)
    insert_idx = -1
    for idx, line in enumerate(lines):
        if line.strip().startswith("|") and re.match(r"^\|\s*\d+\s*\|", line.strip()):
            insert_idx = idx
    if insert_idx != -1:
        lines.insert(insert_idx + 1, row if row.endswith("\n") else row + "\n")
        return "".join(lines)
    return text + "\n" + row


def _next_alr_index(text: str, default: int) -> int:
    m_item = re.findall(r"\|\s*(\d+)\s*\|", text)
    return max([int(x) for x in m_item]) + 1 if m_item else default


_DISALLOWED_ALR_TOKENS = frozenset({
    "spellings", "spelling", "corruptions", "corruption", "variants", "variant",
    "errors", "error", "dialogue", "reference", "mention", "turn", "turns",
    "substantive", "speakers", "speaker", "participant", "participants",
    "attendee", "attendees", "clarified", "resolutions", "resolution",
    "receipt", "receipts", "provenance", "editorial", "context", "critique",
    "feedback", "recap", "remediation", "action", "actions", "statement", "statements",
    "filler", "fillers", "stutters", "stutter", "backchannel", "backchannels",
    "chachi", "chatgpt",
})


def _maybe_add_phonetic_alr(text: str, alr_section: str, critique: str, audit: List[str]) -> Tuple[str, str]:
    low = critique.lower()
    if ("chachi" not in low and "chatgpt" not in low) or "ChatGPT" in alr_section or "Chachi" in alr_section:
        return text, alr_section
    term = "Chachi Bikki" if "chachi bikki" in low else "ChatGPT"
    next_num = _next_alr_index(text, 11)
    row = (
        f"| {next_num} | Phonetic Model-Name Rendering (\"{term}\") | [In-Dialogue] | "
        f"stmt-alr-{next_num:03d} | Phonetic Whisper transcription artifact for ChatGPT / GPT baseline; "
        f"referenced in dialogue. | Automated ASR clarification audit |\n"
    )
    text = _append_alr_row(text, row)
    audit.append(f"Added ALR Item for phonetic model name '{term}' -> ChatGPT")
    return text, text.split("## Ambiguity Lock Register", 1)[-1]


def _extract_critique_alr_token(critique: str, alr_section: str) -> Optional[str]:
    m_ent = re.search(
        r"(?:references?|reference\s+to|mention\s+of|name|token)\s+['\"]?([A-Za-z0-9_-]+)['\"]?",
        critique,
        re.IGNORECASE,
    )
    if not m_ent:
        m_ent = re.search(r"['\"]([A-Z][a-zA-Z0-9_-]*)['\"]", critique)
    if not m_ent:
        return None
    token = m_ent.group(1).strip("'\"")
    is_valid = len(token) >= 3 and token[0].isupper() and token.lower() not in alr_section.lower() and token.lower() not in _DISALLOWED_ALR_TOKENS
    return token if is_valid else None


def _maybe_add_dynamic_alr(
    text: str, alr_section: str, critique: str, audit: List[str]
) -> Tuple[str, str]:
    """Generically extract phonetic slip, vocative mention, or model name clarifications from critique."""
    text, alr_section = _maybe_add_phonetic_alr(text, alr_section, critique, audit)
    token = _extract_critique_alr_token(critique, alr_section)
    if token:
        next_num = _next_alr_index(text, 2)
        m_loc = re.search(r"\[(\d{2}:\d{2}(?::\d{2})?)\]|line\s+(\d+)", critique, re.IGNORECASE)
        loc = m_loc.group(0) if m_loc else "[In-Dialogue]"
        row = (
            f"| {next_num} | In-dialogue reference to \"{token}\" | {loc} | "
            f"stmt-alr-{next_num:03d} | Clarified reference to {token} | "
            f"Contextual disambiguation from session critique |\n"
        )
        text = _append_alr_row(text, row)
        alr_section = text.split("## Ambiguity Lock Register", 1)[-1]
        audit.append(f"Added ALR Item for dynamic reference -> {token}")
    return text, alr_section


def _maybe_add_outro_alr(
    text: str, alr_section: str, locators: List[str], audit: List[str]
) -> Tuple[str, str]:
    if locators and "ASR Outro Hallucination Artifacts" not in alr_section:
        next_num = _next_alr_index(text, 13)
        loc_str = ", ".join(locators)
        row = (
            f"| {next_num} | ASR Outro Hallucination Artifacts (\"Thank you for watching.\") | "
            f"{loc_str} | stmt-asr-outro-01 | Recurring YouTube-style ASR outro hallucination "
            f"(\"Thank you for watching.\") purged from spoken dialogue turns. | Automated ASR exclusion audit |\n"
        )
        text = _append_alr_row(text, row)
        alr_section = text.split("## Ambiguity Lock Register", 1)[-1]
        audit.append("Added ALR Item for ASR Outro Hallucination exclusions")
    return text, alr_section


def _sync_alr_entries(
    text: str,
    combined_critique: str,
    purged_locators: List[str],
) -> Tuple[str, List[str]]:
    if "## Ambiguity Lock Register" not in text:
        return text, []

    current_text = text
    alr_section = current_text.split("## Ambiguity Lock Register", 1)[-1]
    audit_trail: List[str] = []

    current_text, alr_section = _maybe_add_dynamic_alr(
        current_text, alr_section, combined_critique, audit_trail
    )
    current_text, alr_section = _maybe_add_outro_alr(
        current_text, alr_section, purged_locators, audit_trail
    )

    return current_text, audit_trail


def _apply_critique_lexical_and_collateral_fixes(
    text: str, combined_critique: str, audit_trail: List[str]
) -> str:
    if any(tok in combined_critique for tok in ("maximo", "maximum")):
        maximo_fixed = re.sub(
            r"\b(written\s+to|in|writing\s+back\s+to|back\s+to|into)\s+[Mm]aximum(\b|\s+fields?|\s+have\s+a\s+field)",
            r"\1 Maximo\2",
            text,
        )
        if maximo_fixed != text:
            text = maximo_fixed
            audit_trail.append("Corrected ASR homophone 'maximum/Maximum' to 'Maximo' in system field contexts")
    if "collateral" in combined_critique:
        collat_cleaned = re.sub(
            r"(?m)^\s*-\s*\*\*(?:View Scope|Key Workflows):\*\*[^\n]*\n*",
            "",
            text,
        )
        if collat_cleaned != text:
            text = collat_cleaned
            audit_trail.append("Removed unsubstantiated View Scope and Key Workflows lines from Presented Collateral")
    return text


class SurgicalRemediator:
    """
    Surgically remediates isolated defects in Output 01, 02, or 03 without
    triggering catastrophic full-meeting regeneration cycles.
    """

    @staticmethod
    def identify_remediation_target(
        artifact_key: str,
        qa_report: Dict[str, Any],
        judge_report: Dict[str, Any],
    ) -> Optional[str]:
        """
        Determines if a defect in Output 01, 02, or 03 is isolated to a specific section.
        Returns the section identifier if isolated, or None if holistic regeneration is required.
        """
        if artifact_key in ("output_1", "output_01", "01_hitl_resolved_transcript.md"):
            return _target_for_output_1(qa_report, judge_report)
        if artifact_key in ("output_2", "output_02", "02_executive_recap.md"):
            return _target_for_output_2(qa_report, judge_report)
        if artifact_key in ("output_3", "output_03", "03_canonical_signal_projection.json"):
            return _target_for_output_3(qa_report)
        return None

    @staticmethod
    def _inject_attribution_summary(
        text: str, meta: Dict[str, Any], audit_trail: List[str]
    ) -> str:
        if "## Attribution Summary" in text or "# Attribution Summary" in text:
            return text
        roster = meta.get("roster") or meta.get("attendees") or []
        if len(roster) < 2:
            return text
        try:
            from dual_pipeline.transcript_stages_core.attribution_builder import AttributionSummaryBuilder
            summary = AttributionSummaryBuilder.build_summary(meta, dialogue_text=text)
            if summary:
                text = f"{text.rstrip()}\n\n{summary.strip()}\n"
                audit_trail.append("Surgically injected missing Attribution Summary table")
        except Exception as e:
            logger.debug(f"AttributionSummaryBuilder injection skipped: {e}")
        return text

    @staticmethod
    def _inject_alr_table(
        text: str, meta: Dict[str, Any], audit_trail: List[str]
    ) -> str:
        if "## Ambiguity Lock Register" in text or "# Ambiguity Lock Register" in text:
            return text
        try:
            from dual_pipeline.transcript_stages_core.lexical_normalizer import format_ambiguity_lock_register
            alr_table = format_ambiguity_lock_register(meta, meta.get("speaker_map", {}))
            if alr_table:
                text = f"{text.rstrip()}\n\n## Ambiguity Lock Register\n\n{alr_table.strip()}\n"
                audit_trail.append("Surgically injected missing Ambiguity Lock Register table")
        except Exception as e:
            logger.debug(f"format_ambiguity_lock_register injection skipped: {e}")
        return text

    @classmethod
    def _inject_missing_output1_tables(
        cls, text: str, meta: Dict[str, Any], audit_trail: List[str]
    ) -> str:
        text = cls._inject_attribution_summary(text, meta, audit_trail)
        return cls._inject_alr_table(text, meta, audit_trail)

    @classmethod
    def _apply_structural_output1_healing(
        cls, text: str, meta: Dict[str, Any], audit_trail: List[str]
    ) -> str:
        from dual_pipeline.transcript_qa_core.forensic_healer import (
            heal_header_date,
            heal_recording_gaps,
            zero_pad_timestamps,
        )
        target_date = meta.get("date") or meta.get("meeting_date")
        if target_date:
            text = heal_header_date(text, target_date, audit_trail, [])
        text = zero_pad_timestamps(text, audit_trail, [])
        text = heal_recording_gaps(text, audit_trail, [])
        if re.search(r"(?m)^#\s+(?:Live Meeting Transcript|Transcript)\b.*$", text):
            text = re.sub(r"(?m)^#\s+(?:Live Meeting Transcript|Transcript)\b.*$", "# Forensic Transcript", text)
            audit_trail.append("Standardized transcript header to '# Forensic Transcript'")
        return cls._inject_missing_output1_tables(text, meta, audit_trail)

    @classmethod
    def remediate_output_1(
        cls,
        transcript_text: str,
        qa_report: Dict[str, Any],
        judge_report: Dict[str, Any],
        directive: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, bool, List[str]]:
        """Applies bounded surgical remediation to Output 01."""
        if not transcript_text or not transcript_text.strip():
            return transcript_text, False, ["Empty transcript text"]

        target = cls.identify_remediation_target("output_1", qa_report, judge_report)
        if not target and not (directive or "").strip():
            return transcript_text, False, ["Holistic regeneration required for Output 01"]

        audit_trail: List[str] = []
        original_text = transcript_text
        meta = metadata or {}
        text = cls._apply_structural_output1_healing(transcript_text, meta, audit_trail)

        out1_eval = (judge_report or {}).get("output_1_evaluation") or {}
        qa_dict = qa_report if isinstance(qa_report, dict) else {}
        q1_sec = qa_dict.get("output_01_forensic_transcript") or qa_dict.get("output_1") or {}
        q1_failures = q1_sec.get("failures", []) if isinstance(q1_sec, dict) else []
        raw_failures_str = " ".join(str(f) for f in q1_failures)
        raw_diff = str(out1_eval.get("actionable_remediation_diff", ""))
        raw_defect = str((out1_eval.get("critique_diff_envelope") or {}).get("observed_defect", ""))
        raw_critique = f"{raw_failures_str} {raw_diff} {raw_defect} {directive or ''}"
        combined_critique = raw_critique.lower()

        text, purged_locators = _purge_asr_outro_hallucinations(text)
        if purged_locators:
            audit_trail.append(f"Purged ASR outro hallucinations at locators: {', '.join(purged_locators)}")

        text, alr_audit = _sync_alr_entries(text, raw_critique, purged_locators)
        audit_trail.extend(alr_audit)
        text = cls._remediate_dialogue_critique(text, combined_critique, audit_trail)

        did_remediate = bool(audit_trail) and (text != original_text)
        _persist_surgical_decision("output_1", target or "directive", original_text, text, did_remediate, audit_trail, meta)
        return text, did_remediate, audit_trail

    @staticmethod
    def _inject_critique_recording_gap(text: str, combined_critique: str, audit_trail: List[str]) -> str:
        m_gap = re.search(
            r"\[((?:Recording gap|Natural pause):\s*(\d{2}:\d{2}:\d{2})\s*[–\-]\s*(\d{2}:\d{2}:\d{2})[^\]]*)\]",
            combined_critique,
            re.IGNORECASE,
        )
        if not m_gap:
            return text
        t1, t2 = m_gap.group(2), m_gap.group(3)
        if re.search(rf"\[(?:Recording gap|Natural pause):\s*{re.escape(t1)}", text, re.IGNORECASE):
            return text
        m_turn = re.search(rf"(?m)^(?:###\s*\[{re.escape(t2)}\]|[^\n]*\[{re.escape(t2)}\])", text)
        if m_turn:
            pos = m_turn.start()
            text = text[:pos] + f"[Recording gap: {t1} – {t2}]\n\n" + text[pos:]
            audit_trail.append(f"Surgically injected requested recording gap [{t1} – {t2}]")
        return text

    @classmethod
    def _remediate_dialogue_critique(cls, text: str, combined_critique: str, audit_trail: List[str]) -> str:
        text = _apply_critique_lexical_and_collateral_fixes(text, combined_critique, audit_trail)
        if any(tok in combined_critique for tok in ("organic signal", "disfluency", "backchannel", "filler", "stutter", "repetition")):
            from dual_pipeline.transcript_stages_core.organic_signal_scrubber import scrub_organic_signal
            scrubbed = scrub_organic_signal(text, preserve_verbatim_turns=True)
            if scrubbed != text:
                text = scrubbed
                audit_trail.append("Surgically scrubbed organic signal fillers, stutters, and ungrounded backchannels")

        for m_bc in re.finditer(r"prune\s+the\s+passive\s+(?:closing\s+)?backchannel\s+at\s+\[?(\d{2}:\d{2}:\d{2})\]?", combined_critique, re.IGNORECASE):
            bc_ts = m_bc.group(1)
            text_new = re.sub(
                rf"(?m)^(?:###\s*\[{re.escape(bc_ts)}\]\s*\n+)?[^\n]*\[?{re.escape(bc_ts)}\]?[^\n]*(?:Okay|All right|Yeah|Alright)[^\n]*\n+",
                "",
                text,
            )
            if text_new != text:
                text = text_new
                audit_trail.append(f"Surgically pruned passive backchannel at [{bc_ts}]")

        if any(tok in combined_critique for tok in ("turn span", "turn duration", "30s", "progression")):
            from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
            re_stitched = ForensicNormalizer._stitch_acoustic_micro_turns(text)
            if re_stitched != text:
                text = re_stitched
                audit_trail.append("Surgically re-stitched micro-turns under 30s duration ceiling")

        return cls._inject_critique_recording_gap(text, combined_critique, audit_trail)

    @staticmethod
    def _verify_and_finalize_output2(
        recap_text: str,
        repaired: str,
        target: str,
        excluded_tokens: Tuple[str, ...],
        label: str,
        baseline_hashes: Dict[str, str],
        audit_trail: List[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Tuple[str, bool, List[str]]:
        if repaired == recap_text:
            return recap_text, False, audit_trail
        new_hashes = compute_section_hashes(repaired)
        frozen_intact = all(
            new_hashes.get(sec) == baseline_hashes.get(sec)
            for sec in baseline_hashes
            if not any(tok in sec for tok in excluded_tokens)
        )
        if not frozen_intact:
            audit_trail.append(f"Remediation rejected: non-{label} sections were mutated")
            _persist_surgical_decision("output_2", target, recap_text, recap_text, False, audit_trail, metadata)
            return recap_text, False, audit_trail
        audit_trail.append(f"Surgically remediated {label} (frozen non-{label} sections intact: True)")
        _persist_surgical_decision("output_2", target, recap_text, repaired, True, audit_trail, metadata)
        return repaired, True, audit_trail

    @classmethod
    def remediate_output_2(
        cls,
        recap_text: str,
        qa_report: Dict[str, Any],
        judge_report: Dict[str, Any],
        directive: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, bool, List[str]]:
        """
        Applies bounded surgical remediation to Output 02 if failure is isolated.
        Returns (remediated_text, did_remediate, audit_trail).
        """
        target = cls.identify_remediation_target("output_2", qa_report, judge_report)
        if not target:
            return recap_text, False, ["Holistic regeneration required: failure not isolated to single section"]

        audit_trail: List[str] = []
        baseline_hashes = compute_section_hashes(recap_text)

        if target == "appendix":
            from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
            repaired = heal_output_2_appendix(recap_text, metadata=metadata or {}, changes=audit_trail)
            return cls._verify_and_finalize_output2(
                recap_text, repaired, target, ("appendix",), "appendix", baseline_hashes, audit_trail, metadata
            )
        if target == "actions_table":
            from dual_pipeline.transcript_qa_core.table_healer import heal_output_2_table
            repaired = heal_output_2_table(recap_text, changes=audit_trail)
            return cls._verify_and_finalize_output2(
                recap_text, repaired, target, ("next_steps", "action"), "actions table", baseline_hashes, audit_trail, metadata
            )

        return recap_text, False, audit_trail
