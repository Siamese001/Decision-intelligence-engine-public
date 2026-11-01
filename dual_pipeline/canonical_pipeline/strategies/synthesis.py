"""
dual_pipeline/canonical_pipeline/strategies/synthesis.py

Polymorphic synthesis strategies for the Canonical Agentic Pipeline.
Decouples Stage L2 synthesis logic based on the identified CanonicalModality:
1. FullMeetingSynthesisStrategy: Synthesizes 01 (Forensic), 02 (Recap), and 03 (Graph).
2. NotesMeetingSynthesisStrategy: Synthesizes 02 (Recap) and 03 (Graph); 01 is marked N/A.
3. DocumentSynthesisStrategy: Synthesizes 03 (Graph) directly; 01 and 02 are marked N/A.
"""
from __future__ import annotations

import abc
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.runtime.pipeline_context import (
    compute_stage_manifest,
    is_checkpoint_valid,
    save_stage_checkpoint,
)
from dual_pipeline.transcript_stages import (
    L2ExecutiveRecapExecutor,
    L2ForensicNormalizerExecutor,
    L2GraphExtractorExecutor,
)
from dual_pipeline.transcript_qa_core.frozen_gates import check_frozen_dependency_validity

logger = logging.getLogger(__name__)


def _resolve_state_machine(pa_context: Optional[Dict[str, Any]], metadata: Dict[str, Any]) -> Any:
    candidates = []
    if pa_context and isinstance(pa_context, dict):
        candidates.append(pa_context.get("state_machine"))
    if metadata and isinstance(metadata, dict):
        candidates.append(metadata.get("state_machine"))
    for candidate in candidates:
        if candidate is not None and hasattr(candidate, "is_frozen"):
            return candidate
    return None


@dataclass
class SynthesisResult:
    """Encapsulates the generated text deliverables from Stage L2."""
    output_1: Optional[str] = None
    output_2: Optional[str] = None
    output_3: Optional[str] = None
    attempt: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_output(self, key: str) -> Optional[str]:
        return self.output_1 if key in ("01", "output_1") else self.output_2 if key in ("02", "output_2") else self.output_3 if key in ("03", "output_3") else None

    @property
    def raw_transcript(self) -> Optional[str]:
        return self.output_1

    @raw_transcript.setter
    def raw_transcript(self, value: Optional[str]) -> None:
        self.output_1 = value

    @property
    def executive_recap(self) -> Optional[str]:
        return self.output_2

    @executive_recap.setter
    def executive_recap(self, value: Optional[str]) -> None:
        self.output_2 = value

    @property
    def canonical_graph(self) -> Optional[str]:
        return self.output_3

    @canonical_graph.setter
    def canonical_graph(self, value: Optional[str]) -> None:
        self.output_3 = value

    @property
    def canonical_signal_projection(self) -> Optional[str]:
        return self.output_3

    @canonical_signal_projection.setter
    def canonical_signal_projection(self, value: Optional[str]) -> None:
        self.output_3 = value

class SynthesisStrategy(abc.ABC):
    """Abstract strategy protocol for Stage L2 Dialectical Synthesis."""

    @property
    @abc.abstractmethod
    def required_deliverables(self) -> List[str]:
        """List of deliverable keys produced by this strategy (e.g. ['01', '02', '03'])."""

    @abc.abstractmethod
    def synthesize(
        self,
        source_text: str,
        pa_context: Dict[str, Any],
        metadata: Dict[str, Any],
        attempt: int = 1,
        remediation_directive: Optional[str] = None,
        progress_notify: Optional[Callable[[str], None]] = None,
    ) -> SynthesisResult:
        """Executes Stage L2 generation producing the required deliverables."""


class FullMeetingSynthesisStrategy(SynthesisStrategy):
    """Strategy for Audio and Diarized Transcripts producing 01, 02, and 03."""

    def __init__(
        self,
        provider: Any,
        l2_forensic: Optional[Any] = None,
        l2_recap: Optional[Any] = None,
        l2_graph: Optional[Any] = None,
    ) -> None:
        self.provider = provider
        self.l2_forensic = l2_forensic or L2ForensicNormalizerExecutor(provider)
        self.l2_recap = l2_recap or L2ExecutiveRecapExecutor(provider)
        self.l2_graph = l2_graph or L2GraphExtractorExecutor(provider)

    @property
    def required_deliverables(self) -> List[str]:
        return ["01", "02", "03"]

    def _resolve_output_1(
        self,
        source_text: str,
        metadata: Dict[str, Any],
        attempt: int,
        remediation_directive: Optional[str],
        progress_notify: Optional[Callable[[str], None]],
        ckpt_dir: Optional[Path],
        pa_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        if progress_notify:
            progress_notify("L2-Stage01: forensic normalizer")
        ckpt_1 = (ckpt_dir / "output_1.ckpt.md") if ckpt_dir else None
        rem_dir_1 = metadata.get("remediation_directive_forensic")
        needs_rem_1 = bool(rem_dir_1)
        prev_1 = self._find_prior_artifact(ckpt_dir, attempt, "output_1.ckpt.md")
        mf_1 = compute_stage_manifest("output_1", source_text, metadata)
        strict = bool(metadata.get("strict_checkpoint_manifest"))
        force = bool(metadata.get("force_rerun"))
        is_regen_1 = "output_1" in str(metadata.get("regenerate", "")) or "01" in str(metadata.get("regenerate", ""))
        has_regen = bool(metadata.get("regenerate"))

        sm = _resolve_state_machine(pa_context, metadata)
        explicit_freeze_1 = bool(metadata.get("freeze_output_1")) and not is_regen_1
        if sm and hasattr(sm, "unfreeze_artifact") and not explicit_freeze_1 and ((is_regen_1 and force) or (force and not has_regen)):
            sm.unfreeze_artifact("output_1", force=True)
        if sm and sm.is_frozen("output_1") and (explicit_freeze_1 or not (is_regen_1 and force)):
            frozen = sm.get_frozen_content("output_1")
            if frozen:
                logger.info("Reusing frozen Output 1 from state machine.")
                if ckpt_1:
                    save_stage_checkpoint(ckpt_1, frozen, mf_1)
                return frozen

        if ckpt_1 and is_checkpoint_valid(ckpt_1, mf_1, strict_manifest=strict) and not needs_rem_1 and not force and not is_regen_1 and self._attempt_stage_passed(ckpt_1, "output_1_pass", metadata):
            logger.info("Resuming L2-Stage01 from verified passing checkpoint: %s", ckpt_1)
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            out_1, _ = DeterministicHealer.heal_output_1(ckpt_1.read_text(encoding="utf-8"), metadata=metadata)
            return out_1
        if prev_1 and is_checkpoint_valid(prev_1, mf_1, strict_manifest=strict) and not needs_rem_1 and not force and not is_regen_1 and self._attempt_stage_passed(prev_1, "output_1_pass", metadata):
            logger.info("Reusing verified L2-Stage01 from previous attempt: %s", prev_1)
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            out_1, _ = DeterministicHealer.heal_output_1(prev_1.read_text(encoding="utf-8"), metadata=metadata)
            if ckpt_1:
                save_stage_checkpoint(ckpt_1, out_1, mf_1)
            return out_1

        # Check if input file itself is an authoritative HITL resolved transcript
        inp_str = metadata.get("input_path") or metadata.get("raw_transcript_path") or (pa_context.get("input_path") if pa_context else None)
        if inp_str and (explicit_freeze_1 or not metadata.get("force_rerun")):
            inp_p = Path(inp_str)
            if inp_p.is_file() and "hitl_resolved_transcript" in inp_p.name and inp_p.stat().st_size > 200:
                logger.info("Authoritative Deliverable 01 input provided directly (byte-exact): %s", inp_p)
                c_text = inp_p.read_text(encoding="utf-8")
                if ckpt_1:
                    save_stage_checkpoint(ckpt_1, c_text, mf_1)
                return c_text

        if (not ckpt_1 or not ckpt_1.exists() or metadata.get("resume")) and not needs_rem_1 and not metadata.get("force_rerun"):
            out_p = Path((pa_context.get("output_dir") if pa_context else None) or metadata.get("output_dir") or (ckpt_dir.parent.parent if ckpt_dir else ""))
            if out_p.exists():
                cands = sorted(out_p.glob("01_*_hitl_resolved_transcript.md"))
                if cands:
                    cand = cands[0]
                    sealed_pass = False
                    explicit_seal_rejected = False
                    seals_file = out_p / "stage_seals.json"
                    if seals_file.exists():
                        try:
                            sdata = json.loads(seals_file.read_text(encoding="utf-8"))
                            seal_1 = sdata.get("output_1", {})
                            c_bytes = cand.read_bytes()
                            c_sha = hashlib.sha256(c_bytes).hexdigest()
                            if seal_1.get("status") == "PASS":
                                if not seal_1.get("sha256") or seal_1.get("sha256") == c_sha:
                                    sealed_pass = True
                                else:
                                    explicit_seal_rejected = True
                            elif seal_1.get("status") in ("FAIL", "FAILED", "REJECTED"):
                                explicit_seal_rejected = True
                        except Exception:
                            sealed_pass = False
                            explicit_seal_rejected = True
                    dec_root = out_p / "final_release_decision.json"
                    if not sealed_pass and not explicit_seal_rejected and dec_root.is_file():
                        try:
                            ddata = json.loads(dec_root.read_text(encoding="utf-8"))
                            if ddata.get("output_1_pass") is True and is_checkpoint_valid(cand, mf_1, strict_manifest=strict):
                                sealed_pass = True
                            elif ddata.get("output_1_pass") is False:
                                explicit_seal_rejected = True
                        except Exception:
                            sealed_pass = False
                    if (
                        not sealed_pass
                        and not explicit_seal_rejected
                        and is_checkpoint_valid(cand, mf_1, strict_manifest=True)
                        and cand.stat().st_size > 1000
                    ):
                        sealed_pass = True
                    if sealed_pass:
                        logger.info("Reusing verified Deliverable 01 from output directory: %s", cand)
                        c_text = cand.read_text(encoding="utf-8")
                        if not explicit_freeze_1 and not metadata.get("resume"):
                            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
                            c_text, _ = DeterministicHealer.heal_output_1(c_text, metadata=metadata)
                        if ckpt_1:
                            save_stage_checkpoint(ckpt_1, c_text, mf_1)
                        return c_text

        if prev_1 and prev_1.exists() and prev_1.stat().st_size > 500:
            c1_prior = prev_1.read_text(encoding="utf-8")
            if not metadata.get("prior_forensic_candidate"):
                metadata["prior_forensic_candidate"] = c1_prior
                if pa_context is not None:
                    pa_context["prior_forensic_candidate"] = c1_prior

        if "source_text" not in metadata and source_text:
            metadata["source_text"] = source_text

        u0_context = {
            "raw_text": source_text, "clean_transcript": source_text,
            "metadata": metadata, "hash": metadata.get("input_hash", ""),
            "diarization_status": metadata.get("diarization_status", "NATIVE"),
        }
        from dual_pipeline.transcript_stages import L1ForensicCognition
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        plan = L1ForensicCognition().plan(u0_context)
        out_1 = self.l2_forensic.execute(
            u0_context=u0_context, plan=plan,
            remediation_directive=rem_dir_1 if (attempt > 1 or metadata.get("remediation_directive_forensic")) else None,
        )
        out_1, _ = DeterministicHealer.heal_output_1(out_1, metadata=metadata)
        if ckpt_1:
            save_stage_checkpoint(ckpt_1, out_1, mf_1)
        return out_1

    @staticmethod
    def _attempt_stage_passed(
        ckpt_file: Path,
        pass_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        stage_key = pass_key.replace("_pass", "")
        seals_file = ckpt_file.parent / "stage_seals.json"
        if seals_file.is_file():
            try:
                seals = json.loads(seals_file.read_text(encoding="utf-8"))
                seal_entry = seals.get(stage_key)
                if isinstance(seal_entry, dict) and seal_entry.get("status") == "PASS":
                    return True
                if seals.get(pass_key) is True:
                    return True
            except Exception:
                pass
        dec_file = ckpt_file.parent / "final_release_decision.json"
        if not dec_file.is_file():
            return False
        try:
            dec = json.loads(dec_file.read_text(encoding="utf-8"))
            if dec.get(pass_key) is True:
                return True
            if dec.get(f"{stage_key}_deterministic_gates_pass") is True and metadata:
                if stage_key in ("output_1", "output_2", "output_3"):
                    from dual_pipeline.llm_judge_core.judge_clarification import (
                        _find_prior_signed_hitl_clarification,
                    )
                    if _find_prior_signed_hitl_clarification(metadata, None, stage_key):
                        return True
            return False
        except Exception:
            return False

    @classmethod
    def _can_reuse_healed_output_3(
        cls,
        cand_file: Path,
        healed_out_3: str,
        forensic_transcript: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        meta_dict = metadata or {}
        is_regen_3 = "output_3" in str(meta_dict.get("regenerate", "")) or "03" in str(meta_dict.get("regenerate", ""))
        has_rem_directive = bool(meta_dict.get("remediation_directive_graph"))

        # Explicit regeneration or active remediation directive for Output 03 blocks stale reuse
        if is_regen_3 and has_rem_directive:
            return False

        if cls._attempt_stage_passed(cand_file, "output_3_pass", metadata):
            if is_regen_3:
                return False
            return True
        dec_file = cand_file.parent / "final_release_decision.json"
        if not dec_file.is_file():
            return False
        try:
            dec = json.loads(dec_file.read_text(encoding="utf-8"))
            if is_regen_3:
                return False

            if dec.get("output_3_deterministic_gates_pass") is False or (meta_dict.get("resume") and not has_rem_directive):
                from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
                from dual_pipeline.transcript_qa_core.graph_reference_validator import (
                    check_conflicting_duplicates,
                )
                g3 = run_qa_gate_3(
                    healed_out_3, f1_text=forensic_transcript, metadata=metadata
                )
                if g3.get("passed") is not True:
                    return False
                parsed = json.loads(healed_out_3) if isinstance(healed_out_3, str) else healed_out_3
                dup_ok, _ = check_conflicting_duplicates(parsed)
                return bool(dup_ok)
        except Exception:
            pass
        return False

    def _find_prior_artifact(self, ckpt_dir: Optional[Path], attempt: int, filename: str) -> Optional[Path]:
        if not ckpt_dir:
            return None

        def _is_sealed_dir(d: Path) -> bool:
            return (d / "final_release_decision.json").is_file() or (d / "stage_seals.json").is_file()

        for require_sealed in (True, False):
            for past_att in range(attempt - 1, 0, -1):
                sub = ckpt_dir.parent / f"attempt_{past_att}"
                cand = sub / filename
                if cand.exists() and cand.stat().st_size > 500 and (not require_sealed or _is_sealed_dir(sub)):
                    return cand
            if ckpt_dir.parent and ckpt_dir.parent.is_dir():
                import re

                def _get_att_num(p: Path) -> int:
                    m = re.search(r"attempt_(\d+)", p.name)
                    return int(m.group(1)) if m else 0

                for sub in sorted(
                    [s for s in ckpt_dir.parent.iterdir() if s.is_dir() and "attempt_" in s.name],
                    key=_get_att_num,
                    reverse=True,
                ):
                    if sub == ckpt_dir or (require_sealed and not _is_sealed_dir(sub)):
                        continue
                    cand = sub / filename
                    if cand.exists() and cand.stat().st_size > 500:
                        return cand

        # Fallback to output directory (deliverables)
        if ckpt_dir.parent and ckpt_dir.parent.parent and ckpt_dir.parent.parent.is_dir():
            prefix = "03_" if "output_3" in filename else ("02_" if "output_2" in filename else "01_")
            suffix = ".json" if "json" in filename else ".md"
            for f in ckpt_dir.parent.parent.glob(f"{prefix}*{suffix}"):
                if "_live_transcript" in f.name:
                    continue
                if f.is_file() and f.stat().st_size > 500:
                    return f

        return None

    def _find_prior_recap(self, ckpt_dir: Optional[Path], attempt: int) -> Optional[Path]:
        return self._find_prior_artifact(ckpt_dir, attempt, "output_2.ckpt.md")

    def _resolve_output_2(
        self,
        forensic_transcript: str,
        pa_context: Dict[str, Any],
        attempt: int,
        remediation_directive: Optional[str],
        progress_notify: Optional[Callable[[str], None]],
        ckpt_dir: Optional[Path],
    ) -> str:
        if progress_notify:
            progress_notify("L2-Stage02: executive recap")
        ckpt_2 = (ckpt_dir / "output_2.ckpt.md") if ckpt_dir else None
        meta = pa_context.get("metadata", {})
        rem_dir_2 = meta.get("remediation_directive_recap")
        needs_rem_2 = bool(rem_dir_2) and not (bool(meta.get("resume")) and attempt == 1)
        prev_2 = self._find_prior_artifact(ckpt_dir, attempt, "output_2.ckpt.md")
        mf_2 = compute_stage_manifest("output_2", forensic_transcript, meta)
        strict = not bool(meta.get("lenient_checkpoint_manifest"))

        is_regen_2 = "output_2" in str(meta.get("regenerate", "")) or "02" in str(meta.get("regenerate", ""))
        has_regen = bool(meta.get("regenerate"))
        force = bool(meta.get("force_rerun"))

        sm = _resolve_state_machine(pa_context, meta)
        if sm and hasattr(sm, "unfreeze_artifact") and ((is_regen_2 and force) or (force and not has_regen)):
            sm.unfreeze_artifact("output_2", force=True)

        if sm and sm.is_frozen("output_2") and not is_regen_2 and not (force and not has_regen):
            frozen = sm.get_frozen_content("output_2")
            if frozen:
                valid, err = check_frozen_dependency_validity("output_2", frozen, sm, meta)
                if valid:
                    logger.info("Reusing frozen Output 2 from state machine.")
                    is_resume_or_out3 = bool(meta.get("resume")) or bool(meta.get("output3_only")) or ("03" in str(meta.get("regenerate", "")))
                    if not is_resume_or_out3:
                        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
                        frozen, _ = DeterministicHealer.heal_output_2(frozen, metadata=meta, output_1=forensic_transcript)
                    if hasattr(sm, "freeze_artifact"):
                        sm.freeze_artifact("output_2", frozen, manifest=mf_2)
                    elif hasattr(sm, "frozen_artifacts") and isinstance(sm.frozen_artifacts, dict):
                        sm.frozen_artifacts["output_2"] = frozen
                    if ckpt_2:
                        save_stage_checkpoint(ckpt_2, frozen, mf_2)
                    return frozen
                else:
                    logger.warning("Frozen Output 2 invalidated due to dependency mismatch (%s); regenerating.", err or "manifest mismatch")
                    if hasattr(sm, "unfreeze_artifact"):
                        sm.unfreeze_artifact("output_2", force=True)

        if ckpt_2 and is_checkpoint_valid(ckpt_2, mf_2, strict_manifest=strict) and not needs_rem_2 and not force and not is_regen_2 and self._attempt_stage_passed(ckpt_2, "output_2_pass", meta):
            logger.info("Resuming L2-Stage02 from checkpoint: %s", ckpt_2)
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            out_2, _ = DeterministicHealer.heal_output_2(ckpt_2.read_text(encoding="utf-8"), metadata=meta, output_1=forensic_transcript)
            return out_2
        if prev_2 and is_checkpoint_valid(prev_2, mf_2, strict_manifest=strict) and not needs_rem_2 and not force and not is_regen_2 and self._attempt_stage_passed(prev_2, "output_2_pass", meta):
            logger.info("Reusing verified L2-Stage02 from previous attempt: %s", prev_2)
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            out_2, _ = DeterministicHealer.heal_output_2(prev_2.read_text(encoding="utf-8"), metadata=meta, output_1=forensic_transcript)
            if ckpt_2:
                save_stage_checkpoint(ckpt_2, out_2, mf_2)
            return out_2
        if (not ckpt_2 or not ckpt_2.exists() or meta.get("resume")) and not needs_rem_2 and not force and not is_regen_2:
            out_p = Path((pa_context.get("output_dir") if pa_context else None) or meta.get("output_dir") or (ckpt_dir.parent.parent if ckpt_dir else ""))
            if out_p.exists():
                cands = sorted(out_p.glob("02_*_executive_recap.md"))
                is_resume_or_out3 = bool(meta.get("resume")) or bool(meta.get("output3_only")) or ("03" in str(meta.get("regenerate", "")))
                if cands and cands[0].stat().st_size > 200 and (is_resume_or_out3 or self._attempt_stage_passed(ckpt_2 if (ckpt_2 and ckpt_2.exists()) else cands[0], "output_2_pass", meta)):
                    logger.info("Reusing verified Deliverable 02 from output directory: %s", cands[0])
                    c2_text = cands[0].read_text(encoding="utf-8")
                    if not is_resume_or_out3:
                        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
                        c2_text, _ = DeterministicHealer.heal_output_2(c2_text, metadata=meta, output_1=forensic_transcript)
                    if ckpt_2:
                        save_stage_checkpoint(ckpt_2, c2_text, mf_2)
                    return c2_text

        if prev_2 and prev_2.exists() and prev_2.stat().st_size > 500:
            c2_prior = prev_2.read_text(encoding="utf-8")
            if not meta.get("prior_recap_candidate"):
                meta["prior_recap_candidate"] = c2_prior
                pa_context["prior_recap_candidate"] = c2_prior

        critique_env = meta.get("critique_diff_envelope") or pa_context.get("critique_diff_envelope")
        if critique_env and prev_2 and prev_2.exists() and critique_env.get("target_path"):
            from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher
            try:
                replacement = critique_env.get("replacement_content")
                if replacement:
                    c2_prior = prev_2.read_text(encoding="utf-8")
                    out_2, patch_audit = SurgicalPatcher.apply_critique_envelope(
                        original_text=c2_prior,
                        critique_envelope=critique_env,
                        replacement_content=replacement,
                        metadata=meta,
                        auto_heal=True,
                    )
                    logger.info("Applied surgical patch to Output 2: %s", patch_audit)
                    if ckpt_2:
                        save_stage_checkpoint(ckpt_2, out_2, mf_2)
                    return out_2
            except Exception as e:
                logger.warning("Surgical patching failed, falling back to full pass execution: %s", e)

        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        from dual_pipeline.transcript_stages_core.completion_call import lookup_content_addressed_synthesis, store_content_addressed_synthesis
        cached_2 = lookup_content_addressed_synthesis("output_2", forensic_transcript, meta, attempt, needs_rem_2, getattr(self.l2_recap, "execute", None))
        out_2 = cached_2 or self.l2_recap.execute(
            forensic_transcript=forensic_transcript, pa_context=pa_context,
            remediation_directive=rem_dir_2 if (attempt > 1 or meta.get("remediation_directive_recap")) else None,
        )
        out_2, _ = DeterministicHealer.heal_output_2(out_2, metadata=meta, output_1=forensic_transcript)
        store_content_addressed_synthesis("output_2", forensic_transcript, meta, out_2)
        if ckpt_2:
            save_stage_checkpoint(ckpt_2, out_2, mf_2)
        return out_2

    @staticmethod
    def _try_apply_output_3_surgical_patch(
        prev_3: Optional[Path],
        critique_env_3: Any,
        needs_rem_3: bool,
        meta: Dict[str, Any],
        forensic_transcript: Optional[str] = None,
    ) -> Optional[str]:
        if not (prev_3 and prev_3.exists() and prev_3.stat().st_size > 500 and (critique_env_3 or needs_rem_3)):
            return None
        from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher
        try:
            c3_prior = prev_3.read_text(encoding="utf-8")
            patch_payload = critique_env_3 or meta.get("remediation_directive_graph") or {}
            patched_out_3, patch_audit = SurgicalPatcher.apply_json_critique_patch(
                original_json=c3_prior,
                critique_diff_or_envelope=patch_payload,
                metadata=meta,
                output_1=forensic_transcript,
            )
            logger.info("Applied surgical patch to Output 3: %s", patch_audit)
            if not patch_audit.get("patch_applied", True):
                logger.info("Surgical patch to Output 3 produced NO_OP (zero mutations); falling back to LLM extraction.")
                return None
            return patched_out_3
        except Exception as e:
            logger.warning("Surgical patching for Output 3 failed, falling back to full pass: %s", e)
            return None

    def _resolve_output_3(
        self,
        forensic_transcript: str,
        executive_recap: Optional[str],
        pa_context: Dict[str, Any],
        attempt: int,
        remediation_directive: Optional[str],
        progress_notify: Optional[Callable[[str], None]],
        ckpt_dir: Optional[Path],
    ) -> str:
        if progress_notify:
            progress_notify("L2-Stage03: graph extractor")
        ckpt_3 = (ckpt_dir / "output_3.ckpt.json") if ckpt_dir else None
        meta = pa_context.get("metadata", {})
        is_regen_3 = "output_3" in str(meta.get("regenerate", "")) or "03" in str(meta.get("regenerate", ""))
        has_regen = bool(meta.get("regenerate"))
        rem_dir_3 = meta.get("remediation_directive_graph")
        needs_rem_3 = bool(rem_dir_3) and (is_regen_3 or not has_regen)
        prev_3 = self._find_prior_artifact(ckpt_dir, attempt, "output_3.ckpt.json")
        mf_3 = compute_stage_manifest("output_3", forensic_transcript, meta)
        strict = bool(meta.get("strict_checkpoint_manifest"))

        if (
            not is_regen_3
            and has_regen
            and prev_3
            and prev_3.exists()
            and prev_3.stat().st_size > 500
            and is_checkpoint_valid(prev_3, mf_3, strict_manifest=strict)
            and self._attempt_stage_passed(prev_3, "output_3_pass", meta)
        ):
            logger.info("Reusing verified Output 3 from previous attempt (regenerate scope: %s)", meta.get("regenerate"))
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            out_3, _ = DeterministicHealer.heal_output_3(prev_3.read_text(encoding="utf-8"), metadata=meta, output_1=forensic_transcript)
            if ckpt_3:
                save_stage_checkpoint(ckpt_3, out_3, mf_3)
            return out_3

        is_regen_3 = "output_3" in str(meta.get("regenerate", "")) or "03" in str(meta.get("regenerate", ""))
        force = bool(meta.get("force_rerun"))

        sm = _resolve_state_machine(pa_context, meta)
        if sm and hasattr(sm, "unfreeze_artifact") and ((is_regen_3 and force) or (force and not has_regen)):
            sm.unfreeze_artifact("output_3", force=True)

        if sm and sm.is_frozen("output_3") and not is_regen_3 and not (force and not has_regen):
            frozen = sm.get_frozen_content("output_3")
            if frozen:
                valid, err = check_frozen_dependency_validity("output_3", frozen, sm, meta)
                if valid:
                    logger.info("Reusing frozen Output 3 from state machine.")
                    if ckpt_3:
                        save_stage_checkpoint(ckpt_3, frozen, mf_3)
                    return frozen
                else:
                    logger.warning("Frozen Output 3 invalidated due to dependency mismatch (%s); regenerating.", err or "manifest mismatch")
                    if hasattr(sm, "unfreeze_artifact"):
                        sm.unfreeze_artifact("output_3", force=True)

        strict = bool(meta.get("strict_checkpoint_manifest"))
        if ckpt_3 and is_checkpoint_valid(ckpt_3, mf_3, strict_manifest=strict) and not needs_rem_3 and not meta.get("force_rerun"):
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            out_3, _ = DeterministicHealer.heal_output_3(ckpt_3.read_text(encoding="utf-8"), metadata=meta, output_1=forensic_transcript)
            if self._can_reuse_healed_output_3(ckpt_3, out_3, forensic_transcript, meta):
                logger.info("Resuming L2-Stage03 from verified/healed checkpoint: %s", ckpt_3)
                return out_3
        if prev_3 and is_checkpoint_valid(prev_3, mf_3, strict_manifest=strict) and not needs_rem_3 and not meta.get("force_rerun"):
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            out_3, _ = DeterministicHealer.heal_output_3(prev_3.read_text(encoding="utf-8"), metadata=meta, output_1=forensic_transcript)
            if self._can_reuse_healed_output_3(prev_3, out_3, forensic_transcript, meta):
                logger.info("Reusing verified/healed L2-Stage03 from previous attempt: %s", prev_3)
                if ckpt_3:
                    save_stage_checkpoint(ckpt_3, out_3, mf_3)
                return out_3
        if (not ckpt_3 or not ckpt_3.exists() or meta.get("resume")) and not needs_rem_3 and not meta.get("force_rerun"):
            out_p = Path(pa_context.get("output_dir") or meta.get("output_dir") or (ckpt_dir.parent.parent if ckpt_dir else ""))
            if out_p.exists():
                cands = sorted(out_p.glob("03_*_canonical_signal_projection.json"))
                if cands and (is_checkpoint_valid(cands[0], mf_3, strict_manifest=True) or meta.get("resume")):
                    from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
                    c_text, _ = DeterministicHealer.heal_output_3(cands[0].read_text(encoding="utf-8"), metadata=meta, output_1=forensic_transcript)
                    if is_checkpoint_valid(cands[0], mf_3, strict_manifest=True) or self._can_reuse_healed_output_3(ckpt_3 if (ckpt_3 and ckpt_3.exists()) else cands[0], c_text, forensic_transcript, meta):
                        logger.info("Reusing verified Deliverable 03 with valid dependency manifest from output directory: %s", cands[0])
                        if ckpt_3:
                            save_stage_checkpoint(ckpt_3, c_text, mf_3)
                        return c_text

        c3_prior: Optional[str] = None
        if prev_3 and prev_3.exists() and prev_3.stat().st_size > 500:
            c3_prior = prev_3.read_text(encoding="utf-8")
            if not meta.get("prior_graph_candidate"):
                meta["prior_graph_candidate"] = c3_prior
                pa_context["prior_graph_candidate"] = c3_prior

        critique_env_3 = (
            meta.get("critique_diff_envelope_3")
            or meta.get("critique_diff_envelope_graph")
            or (
                meta.get("critique_diff_envelope")
                if isinstance(meta.get("critique_diff_envelope"), dict)
                and meta.get("critique_diff_envelope", {}).get("artifact") in ("OUTPUT03", "output_3")
                else None
            )
            or pa_context.get("critique_diff_envelope_3")
            or rem_dir_3
            or meta.get("actionable_remediation_diff_3")
            or meta.get("actionable_remediation_diff_graph")
        )
        patched_cand = self._try_apply_output_3_surgical_patch(prev_3, critique_env_3, needs_rem_3, meta, forensic_transcript=forensic_transcript)
        if patched_cand:
            if self._can_reuse_healed_output_3(prev_3, patched_cand, forensic_transcript, meta):
                logger.info("Surgically patched Output 3 passed gating; reusing without LLM regeneration.")
                if ckpt_3:
                    save_stage_checkpoint(ckpt_3, patched_cand, mf_3)
                return patched_cand
            meta["prior_graph_candidate"] = patched_cand
            pa_context["prior_graph_candidate"] = patched_cand
        elif attempt > 1 and c3_prior and (meta.get("critique_diff_envelope") or pa_context.get("critique_diff_envelope") or needs_rem_3):
            from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher
            critique_env = meta.get("critique_diff_envelope") or pa_context.get("critique_diff_envelope")
            patched_3, audit = SurgicalPatcher.apply_output3_critique_patch(
                original_json=c3_prior, critique_envelope=critique_env, metadata=meta, output_1=forensic_transcript,
            )
            if audit.get("patch_applied"):
                logger.info("Applied surgical critique patch to Output 03: %s", audit.get("changes_applied"))
                if ckpt_3:
                    save_stage_checkpoint(ckpt_3, patched_3, mf_3)
                return patched_3

        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        from dual_pipeline.transcript_stages_core.completion_call import lookup_content_addressed_synthesis, store_content_addressed_synthesis
        cached_3 = lookup_content_addressed_synthesis("output_3", forensic_transcript, meta, attempt, needs_rem_3, getattr(self.l2_graph, "execute", None))
        out_3 = cached_3 or self.l2_graph.execute(
            forensic_transcript=forensic_transcript, executive_recap=executive_recap, pa_context=pa_context,
            remediation_directive=rem_dir_3 if (attempt > 1 or meta.get("remediation_directive_graph")) else None,
        )
        out_3, _ = DeterministicHealer.heal_output_3(out_3, metadata=meta, output_1=forensic_transcript)
        store_content_addressed_synthesis("output_3", forensic_transcript, meta, out_3)
        if ckpt_3:
            save_stage_checkpoint(ckpt_3, out_3, mf_3)
        return out_3

    def synthesize(
        self,
        source_text: str,
        pa_context: Dict[str, Any],
        metadata: Dict[str, Any],
        attempt: int = 1,
        remediation_directive: Optional[str] = None,
        progress_notify: Optional[Callable[[str], None]] = None,
    ) -> SynthesisResult:
        # Gate F Audio Completeness Circuit Breaker (Fail Fast for Outputs 01, 02, 03)
        from dual_pipeline.audio.circuit_breaker import AudioCompletenessCircuitBreaker
        AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata, pa_context)
        metadata["gate_f_certified"] = True
        if pa_context and isinstance(pa_context.get("metadata"), dict):
            pa_context["metadata"].update(metadata)
            pa_context["metadata"]["gate_f_certified"] = True

        if pa_context is not None and not pa_context.get("output_dir") and metadata.get("output_dir"):
            pa_context["output_dir"] = metadata["output_dir"]
        ckpt_dir = Path(pa_context["output_dir"]) / "attempts" / f"attempt_{attempt}" if pa_context.get("output_dir") else None
        if ckpt_dir:
            ckpt_dir.mkdir(parents=True, exist_ok=True)

        out_1 = self._resolve_output_1(
            source_text, metadata, attempt, remediation_directive, progress_notify, ckpt_dir, pa_context=pa_context
        )
        from dual_pipeline.canonical_pipeline.child_dispatch import validate_child_dispatch_predicate
        validate_child_dispatch_predicate(out_1, metadata=metadata, source_text=source_text)

        pa_ctx_2 = dict(pa_context) if pa_context is not None else {}
        if "metadata" in pa_ctx_2 and isinstance(pa_ctx_2["metadata"], dict):
            pa_ctx_2["metadata"] = dict(pa_ctx_2["metadata"])
        pa_ctx_3 = dict(pa_context) if pa_context is not None else {}
        if "metadata" in pa_ctx_3 and isinstance(pa_ctx_3["metadata"], dict):
            pa_ctx_3["metadata"] = dict(pa_ctx_3["metadata"])

        out_2 = self._resolve_output_2(out_1, pa_ctx_2, attempt, remediation_directive, progress_notify, ckpt_dir)
        out_3 = self._resolve_output_3(out_1, out_2, pa_ctx_3, attempt, remediation_directive, progress_notify, ckpt_dir)

        return SynthesisResult(
            output_1=out_1,
            output_2=out_2,
            output_3=out_3,
            attempt=attempt,
            metadata=dict(metadata),
        )


class NotesMeetingSynthesisStrategy(SynthesisStrategy):
    """Strategy for Copilot Notes & Meeting Summaries producing 02 and 03 (01 skipped)."""

    def __init__(
        self,
        provider: Any,
        l2_recap: Optional[Any] = None,
        l2_graph: Optional[Any] = None,
    ) -> None:
        self.provider = provider
        self.l2_recap = l2_recap or L2ExecutiveRecapExecutor(provider)
        self.l2_graph = l2_graph or L2GraphExtractorExecutor(provider)

    @property
    def required_deliverables(self) -> List[str]:
        return ["02", "03"]

    def _resolve_notes_recap(
        self,
        source_text: str,
        pa_context: Dict[str, Any],
        attempt: int,
        remediation_directive: Optional[str],
        progress_notify: Optional[Callable[[str], None]],
        ckpt_dir: Optional[Path],
    ) -> str:
        if progress_notify:
            progress_notify("L2-Stage02: executive recap (notes mode)")
        ckpt_2 = (ckpt_dir / "output_2.ckpt.md") if ckpt_dir else None
        meta = pa_context.get("metadata", {})
        rem_dir_2 = meta.get("remediation_directive_recap") or remediation_directive
        needs_rem_2 = bool(rem_dir_2 and ("output_2" in rem_dir_2.lower() or "recap" in rem_dir_2.lower()))
        sm = _resolve_state_machine(pa_context, meta)
        if sm and sm.is_frozen("output_2"):
            frozen = sm.get_frozen_content("output_2")
            if frozen:
                logger.info("Reusing frozen Output 2 from state machine.")
                if ckpt_2:
                    ckpt_2.parent.mkdir(parents=True, exist_ok=True)
                    ckpt_2.write_text(frozen, encoding="utf-8")
                return frozen

        if ckpt_2 and ckpt_2.exists() and ckpt_2.stat().st_size > 500 and not needs_rem_2:
            logger.info("Resuming L2-Stage02 from checkpoint: %s", ckpt_2)
            return ckpt_2.read_text(encoding="utf-8")

        out_2 = self.l2_recap.execute(
            forensic_transcript=source_text,
            pa_context=pa_context,
            remediation_directive=rem_dir_2 if (attempt > 1 or meta.get("remediation_directive_recap")) else None,
        )
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        out_2, _ = DeterministicHealer.heal_output_2(out_2, metadata=meta, output_1=source_text)
        if ckpt_2:
            ckpt_2.parent.mkdir(parents=True, exist_ok=True)
            ckpt_2.write_text(out_2, encoding="utf-8")
        return out_2

    def _resolve_notes_graph(
        self,
        source_text: str,
        executive_recap: Optional[str],
        pa_context: Dict[str, Any],
        attempt: int,
        remediation_directive: Optional[str],
        progress_notify: Optional[Callable[[str], None]],
        ckpt_dir: Optional[Path],
    ) -> str:
        if progress_notify:
            progress_notify("L2-Stage03: graph extractor (notes mode)")
        ckpt_3 = (ckpt_dir / "output_3.ckpt.json") if ckpt_dir else None
        meta = pa_context.get("metadata", {})
        rem_dir_3 = meta.get("remediation_directive_graph") or remediation_directive
        needs_rem_3 = bool(rem_dir_3 and ("output_3" in rem_dir_3.lower() or "graph" in rem_dir_3.lower()))
        sm = _resolve_state_machine(pa_context, meta)
        if sm and sm.is_frozen("output_3"):
            frozen = sm.get_frozen_content("output_3")
            if frozen:
                logger.info("Reusing frozen Output 3 from state machine.")
                if ckpt_3:
                    ckpt_3.parent.mkdir(parents=True, exist_ok=True)
                    ckpt_3.write_text(frozen, encoding="utf-8")
                return frozen

        if ckpt_3 and ckpt_3.exists() and ckpt_3.stat().st_size > 500 and not needs_rem_3:
            logger.info("Resuming L2-Stage03 from checkpoint: %s", ckpt_3)
            raw_ckpt = ckpt_3.read_text(encoding="utf-8")
            from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
            healed_ckpt, _ = DeterministicHealer.heal_output_3(raw_ckpt, metadata=meta)
            return healed_ckpt

        out_3 = self.l2_graph.execute(
            forensic_transcript=source_text,
            executive_recap=executive_recap,
            pa_context=pa_context,
            remediation_directive=rem_dir_3 if attempt > 1 else None,
        )
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        out_3, _ = DeterministicHealer.heal_output_3(out_3, metadata=meta)
        if ckpt_3:
            ckpt_3.parent.mkdir(parents=True, exist_ok=True)
            ckpt_3.write_text(out_3, encoding="utf-8")
        return out_3

    def synthesize(
        self,
        source_text: str,
        pa_context: Dict[str, Any],
        metadata: Dict[str, Any],
        attempt: int = 1,
        remediation_directive: Optional[str] = None,
        progress_notify: Optional[Callable[[str], None]] = None,
    ) -> SynthesisResult:
        if pa_context is not None and not pa_context.get("output_dir") and metadata.get("output_dir"):
            pa_context["output_dir"] = metadata["output_dir"]
        ckpt_dir = Path(pa_context["output_dir"]) / "attempts" / f"attempt_{attempt}" if pa_context.get("output_dir") else None
        if ckpt_dir:
            ckpt_dir.mkdir(parents=True, exist_ok=True)

        sm = _resolve_state_machine(pa_context, metadata)
        frozen_2 = sm.get_frozen_content("output_2") if sm and sm.is_frozen("output_2") else None

        with ThreadPoolExecutor(max_workers=2) as ex:
            pa_ctx_2 = dict(pa_context) if pa_context is not None else {}
            if "metadata" in pa_ctx_2 and isinstance(pa_ctx_2["metadata"], dict):
                pa_ctx_2["metadata"] = dict(pa_ctx_2["metadata"])
            pa_ctx_3 = dict(pa_context) if pa_context is not None else {}
            if "metadata" in pa_ctx_3 and isinstance(pa_ctx_3["metadata"], dict):
                pa_ctx_3["metadata"] = dict(pa_ctx_3["metadata"])

            if frozen_2:
                f2 = ex.submit(self._resolve_notes_recap, source_text, pa_ctx_2, attempt, remediation_directive, progress_notify, ckpt_dir)
                f3 = ex.submit(self._resolve_notes_graph, source_text, frozen_2, pa_ctx_3, attempt, remediation_directive, progress_notify, ckpt_dir)
                out_2, out_3 = f2.result(), f3.result()
            else:
                out_2 = self._resolve_notes_recap(source_text, pa_ctx_2, attempt, remediation_directive, progress_notify, ckpt_dir)
                out_3 = self._resolve_notes_graph(source_text, out_2, pa_ctx_3, attempt, remediation_directive, progress_notify, ckpt_dir)

        return SynthesisResult(output_1=None, output_2=out_2, output_3=out_3, attempt=attempt, metadata=dict(metadata))


class DocumentSynthesisStrategy(SynthesisStrategy):
    """Strategy for Written Documents, SOWs, and Emails producing 03 only (01 & 02 skipped)."""

    def __init__(self, provider: Any, document_extractor: Optional[Any] = None) -> None:
        self.provider = provider
        if document_extractor is None:
            from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
            document_extractor = DocumentGraphExtractorExecutor(provider)
        self.document_extractor = document_extractor

    @property
    def required_deliverables(self) -> List[str]:
        return ["03"]

    def synthesize(
        self,
        source_text: str,
        pa_context: Dict[str, Any],
        metadata: Dict[str, Any],
        attempt: int = 1,
        remediation_directive: Optional[str] = None,
        progress_notify: Optional[Callable[[str], None]] = None,
    ) -> SynthesisResult:
        if source_text and (source_text.strip().startswith("WEBVTT") or ("-->" in source_text[:2000] and "<!--" not in source_text[:2000])):
            raise ValueError(
                "DocumentSynthesisStrategy cannot process raw transcript files (detected WEBVTT cues). "
                "Raw transcripts must use FullMeetingSynthesisStrategy to produce deliverables 01, 02, and 03."
            )
        ckpt_3 = Path(pa_context["output_dir"]) / "attempts" / f"attempt_{attempt}" / "output_3.ckpt.json" if pa_context.get("output_dir") else None
        if progress_notify:
            progress_notify("L2-Stage03: document graph extractor")
        out_3 = self.document_extractor.execute(
            document_text=source_text,
            pa_context=pa_context,
            remediation_directive=remediation_directive if attempt > 1 else None,
            attempt=attempt,
            document_patch_request=metadata.get("document_patch_request"),
            prior_graph_candidate=metadata.get("prior_graph_candidate") or pa_context.get("prior_graph_candidate"),
        )
        pa_meta = pa_context.get("metadata") if isinstance(pa_context, dict) else None
        if isinstance(pa_meta, dict):
            for k in ("document_archetype", "max_mutation_budget"):
                if k in pa_meta:
                    metadata[k] = pa_meta[k]
        from dual_pipeline.canonical_pipeline.parallel_orchestrator import finalize_document_output3_parallel
        finalize_document_output3_parallel(out_3, metadata, ckpt_3)
        return SynthesisResult(output_1=None, output_2=None, output_3=out_3, attempt=attempt, metadata=dict(metadata))


def get_synthesis_strategy(modality: CanonicalModality, provider: Any, pipeline: Optional[Any] = None) -> SynthesisStrategy:
    """Factory creating the appropriate SynthesisStrategy for a given modality."""
    if modality == CanonicalModality.MEETING_NOTES:
        return NotesMeetingSynthesisStrategy(provider, l2_recap=getattr(pipeline, "l2_recap", None), l2_graph=getattr(pipeline, "l2_graph", None))
    if modality == CanonicalModality.DOCUMENT:
        cand_l2_graph = getattr(pipeline, "l2_graph", None)
        doc_ext = getattr(pipeline, "document_extractor", None) or (cand_l2_graph if cand_l2_graph is not None and not isinstance(cand_l2_graph, L2GraphExtractorExecutor) else None)
        return DocumentSynthesisStrategy(provider, document_extractor=doc_ext)
    return FullMeetingSynthesisStrategy(provider, l2_forensic=getattr(pipeline, "l2_forensic", None), l2_recap=getattr(pipeline, "l2_recap", None), l2_graph=getattr(pipeline, "l2_graph", None))
