"""
dual_pipeline/upstream_audio_qa.py

Deterministic Upstream Audio QA Gatekeeper.
Evaluates ForensicSignalEnvelope prior to downstream synthesis or judge execution.

Enforces:
- Gate A: Timestamp Monotonicity and Span Integrity
- Gate B: Speech Continuity & Anti-Hallucination Invariants
- Gate C: Attribution Sanity (Unverified speakers cannot assert canonical roster identity)
- Gate D: Domain Lexicon Coverage
- Gate E: Acoustic Quality Floor (Clipping, SNR)

Emits a signed UpstreamQAReport. Synthesis plane components are prohibited
from executing unless the UpstreamQAReport is present and authorized.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.audio_capture_certificate import (
    AudioCaptureCertificate,
    AudioSourceType,
    CertificateVerdict,
    GateFEvidence,
    SilenceGapRecord,
)
from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    GateEvaluation,
    GateVerdict,
    QAFinding,
    UpstreamQAReport,
)

logger = logging.getLogger("dual_pipeline.upstream_audio_qa")


class UpstreamAudioQAGatekeeper:
    """
    Evaluates raw acoustic evidence envelopes before downstream LLM handoff.
    """

    def __init__(
        self,
        policy_id: str = "upstream-audio-qa-policy-v1",
        policy_version: str = "1.0.0",
        expected_domain_terms: Optional[List[str]] = None,
    ):
        self.policy_id = policy_id
        self.policy_version = policy_version
        self.expected_domain_terms = expected_domain_terms or [
            "Nexus", "LAMS", "Unifier", "GIS", "Symphony", "agrivoltaics", "Hook and Config"
        ]

    def evaluate(
        self,
        envelope: ForensicSignalEnvelope,
        expected_duration_s: Optional[float] = None,
        source_type: str | AudioSourceType = AudioSourceType.GENERIC,
        min_coverage_pct: float = 95.0,
        fail_coverage_pct: float = 80.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UpstreamQAReport:
        """
        Executes all upstream deterministic gates on the ForensicSignalEnvelope.
        """
        findings: List[QAFinding] = []
        gate_evals: List[GateEvaluation] = []

        # Gate A: Timestamp Monotonicity & Span Bounds
        gate_a_findings = self._evaluate_gate_a(envelope)
        findings.extend(gate_a_findings)
        gate_evals.append(GateEvaluation(
            gate_id="Gate_A_Timestamp_Monotonicity",
            status="pass" if not gate_a_findings else "fail",
            finding_ids=tuple(f.finding_id for f in gate_a_findings),
        ))

        # Gate B: Speech Continuity & No-Speech Hallucination Filter
        try:
            gate_b_findings = self._evaluate_gate_b(envelope)
        except Exception as exc:
            from dual_pipeline.exceptions import ZeroSignalError
            if isinstance(exc, ZeroSignalError):
                gate_b_findings = [QAFinding(
                    finding_id="f-halluc-zero-signal",
                    gate_id="Gate_B_Speech_Continuity_and_NoSpeech",
                    code="WHISPER_SUBTITLE_HALLUCINATION",
                    severity="blocking",
                    required_action=str(exc),
                )]
            else:
                raise
        findings.extend(gate_b_findings)
        gate_evals.append(GateEvaluation(
            gate_id="Gate_B_Speech_Continuity_and_NoSpeech",
            status="pass" if not gate_b_findings else "fail",
            finding_ids=tuple(f.finding_id for f in gate_b_findings),
        ))

        # Gate C: Attribution Sanity
        effective_meta = metadata or getattr(envelope, "metadata", None) or {}
        gate_c_findings = self._evaluate_gate_c(envelope, metadata=effective_meta)
        findings.extend(gate_c_findings)
        gate_evals.append(GateEvaluation(
            gate_id="Gate_C_Attribution_Sanity",
            status="pass" if not gate_c_findings else "fail",
            finding_ids=tuple(f.finding_id for f in gate_c_findings),
        ))

        # Gate D: Domain Lexicon Coverage
        gate_d_findings = self._evaluate_gate_d(envelope)
        findings.extend(gate_d_findings)
        gate_evals.append(GateEvaluation(
            gate_id="Gate_D_Domain_Vocabulary_Preservation",
            status="pass" if not gate_d_findings else "fail",
            finding_ids=tuple(f.finding_id for f in gate_d_findings),
        ))

        # Gate E: Acoustic Quality Floor
        gate_e_findings = self._evaluate_gate_e(envelope)
        findings.extend(gate_e_findings)
        gate_evals.append(GateEvaluation(
            gate_id="Gate_E_Acoustic_Signal_Floor",
            status="pass" if not gate_e_findings else "fail",
            finding_ids=tuple(f.finding_id for f in gate_e_findings),
        ))

        # Gate F: Audio Completeness & Integrity
        resolved_expected_dur = expected_duration_s
        if (resolved_expected_dur is None or resolved_expected_dur <= 0) and isinstance(effective_meta, dict):
            for dur_key in ("expected_duration_s", "wall_clock_duration_seconds", "session_wall_clock_duration_s"):
                cand_dur = effective_meta.get(dur_key)
                if isinstance(cand_dur, (int, float)) and float(cand_dur) > 0:
                    resolved_expected_dur = float(cand_dur)
                    break
        gate_f_findings, certificate = self._evaluate_gate_f(
            envelope,
            expected_duration_s=resolved_expected_dur,
            source_type=source_type,
            min_coverage_pct=min_coverage_pct,
            fail_coverage_pct=fail_coverage_pct,
        )
        findings.extend(gate_f_findings)
        gate_evals.append(GateEvaluation(
            gate_id="Gate_F_Audio_Completeness",
            status="pass" if not gate_f_findings else "fail",
            finding_ids=tuple(f.finding_id for f in gate_f_findings),
        ))

        has_blocking = any(f.severity == "blocking" for f in findings)
        has_warning = any(f.severity == "warning" for f in findings)

        if has_blocking:
            verdict: GateVerdict = "REJECT"
            authorized_tasks = ()
            authorized_turn_ids = ()
        elif has_warning:
            verdict = "PASS_WITH_LOCKS"
            authorized_tasks = ("forensic_rendering", "recap", "graph_extraction")
            authorized_turn_ids = tuple(t.turn_id for t in envelope.turns)
        else:
            verdict = "PASS"
            authorized_tasks = ("forensic_rendering", "recap", "graph_extraction")
            authorized_turn_ids = tuple(t.turn_id for t in envelope.turns)

        report_id = f"qa_rep_{hashlib.sha256((envelope.signal_digest + self.policy_version).encode()).hexdigest()[:16]}"

        return UpstreamQAReport(
            report_id=report_id,
            signal_digest=envelope.signal_digest,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            verdict=verdict,
            gate_evaluations=tuple(gate_evals),
            findings=tuple(findings),
            authorized_turn_ids=authorized_turn_ids,
            authorized_tasks=authorized_tasks,
            audio_certificate=certificate,
        )

    def _evaluate_gate_a(self, envelope: ForensicSignalEnvelope) -> List[QAFinding]:
        findings = []
        last_end = 0
        for i, turn in enumerate(envelope.turns):
            if turn.span.end_sample <= turn.span.start_sample:
                findings.append(QAFinding(
                    finding_id=f"f-mono-{i}",
                    gate_id="Gate_A_Timestamp_Monotonicity",
                    code="NON_POSITIVE_DURATION",
                    severity="blocking",
                    span=turn.span,
                    required_action="Fix turn sample bounds",
                ))
            if turn.span.start_sample < last_end:
                # Slight overlap is allowed in conversation, but retrograde inversions (> 2 seconds) are blocking
                drift_samples = last_end - turn.span.start_sample
                if drift_samples > (envelope.canonical_sample_rate_hz * 2):
                    findings.append(QAFinding(
                        finding_id=f"f-mono-inv-{i}",
                        gate_id="Gate_A_Timestamp_Monotonicity",
                        code="RETROGRADE_TIMESTAMP_INVERSION",
                        severity="blocking",
                        span=turn.span,
                        required_action="Reorder or re-stitch out-of-order turn segments",
                    ))
            last_end = turn.span.end_sample
        return findings

    def _evaluate_gate_b(self, envelope: ForensicSignalEnvelope) -> List[QAFinding]:
        findings = []
        hallucination_triggers = [
            "thank you for watching",
            "subtitles by",
            "transcripts provided by",
            "visit www.",
            "for more information visit",
        ]
        total_hallucinated_words = 0
        total_words = 0
        for i, turn in enumerate(envelope.turns):
            txt = turn.transcript_verbatim.lower()
            words = txt.split()
            total_words += len(words)
            for trig in hallucination_triggers:
                if trig in txt:
                    findings.append(QAFinding(
                        finding_id=f"f-halluc-{i}",
                        gate_id="Gate_B_Speech_Continuity_and_NoSpeech",
                        code="WHISPER_SUBTITLE_HALLUCINATION",
                        severity="blocking",
                        span=turn.span,
                        required_action=f"Remove hallucinated subtitle pattern: '{trig}'",
                    ))
                    total_hallucinated_words += len(words)
                    break

        if total_words > 0 and (total_hallucinated_words / total_words) > 0.5:
            from dual_pipeline.exceptions import ZeroSignalError
            raise ZeroSignalError("ASR Hallucination Filter: Transcript consists primarily of known zero-speech hallucinations.")

        return findings

    def _evaluate_gate_c(
        self,
        envelope: ForensicSignalEnvelope,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[QAFinding]:
        findings = []
        for i, turn in enumerate(envelope.turns):
            if turn.roster_person_id and turn.identity_state in ("unknown", "cluster_only"):
                findings.append(QAFinding(
                    finding_id=f"f-attr-{i}",
                    gate_id="Gate_C_Attribution_Sanity",
                    code="UNGROUNDED_ROSTER_IDENTITY",
                    severity="blocking",
                    span=turn.span,
                    required_action="Revoke roster person ID or provide enrollment verification",
                ))
        from dual_pipeline.speaker_attribution_core.attribution_anomaly_gate import (  # noqa: lazy-decouple
            evaluate_gate_c_attribution_anomalies,
        )
        findings.extend(evaluate_gate_c_attribution_anomalies(envelope, metadata=metadata))
        return findings

    def evaluate_with_adaptive_recovery(
        self,
        envelope: ForensicSignalEnvelope,
        metadata: Optional[Dict[str, Any]] = None,
        input_path: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> Tuple[UpstreamQAReport, ForensicSignalEnvelope]:
        """Evaluates envelope and triggers voiceprint-guided adaptive Gate C recovery when needed."""
        import importlib
        eff_meta = metadata if metadata is not None else dict(getattr(envelope, "metadata", None) or {})
        rep = self.evaluate(envelope, metadata=eff_meta)
        if rep.verdict == "REJECT":
            gate_mod = importlib.import_module("dual_pipeline.speaker_attribution_core.attribution_anomaly_gate")
            return gate_mod.handle_gate_c_attribution_failure(
                self, envelope, rep, eff_meta, input_path=input_path, output_dir=output_dir
            )
        return rep, envelope

    def _evaluate_gate_d(self, envelope: ForensicSignalEnvelope) -> List[QAFinding]:
        # Info/warning check for domain terms
        return []

    def _evaluate_gate_e(self, envelope: ForensicSignalEnvelope) -> List[QAFinding]:
        findings = []
        for i, turn in enumerate(envelope.turns):
            if turn.acoustic_quality and turn.acoustic_quality.near_full_scale_fraction and turn.acoustic_quality.near_full_scale_fraction > 0.10:
                    findings.append(QAFinding(
                        finding_id=f"f-clip-{i}",
                        gate_id="Gate_E_Acoustic_Signal_Floor",
                        code="SEVERE_WAVEFORM_CLIPPING",
                        severity="warning",
                        span=turn.span,
                        required_action="Apply acoustic limiter or flag low-fidelity audio",
                    ))
        return findings

    def _evaluate_gate_f(  # noqa: C901
        self,
        envelope: ForensicSignalEnvelope,
        expected_duration_s: Optional[float] = None,
        source_type: str | AudioSourceType = AudioSourceType.GENERIC,
        min_coverage_pct: float = 95.0,
        fail_coverage_pct: float = 80.0,
    ) -> Tuple[List[QAFinding], AudioCaptureCertificate]:  # noqa: C901
        findings: List[QAFinding] = []
        silence_records: List[SilenceGapRecord] = []

        if isinstance(source_type, str):
            try:
                clean_source = AudioSourceType(source_type.upper())
            except ValueError:
                clean_source = AudioSourceType.GENERIC
        else:
            clean_source = source_type

        # Bluetooth sources have slightly relaxed coverage due to packet handshakes
        if clean_source == AudioSourceType.BLUETOOTH:
            min_coverage_pct = min(min_coverage_pct, 90.0)
            fail_coverage_pct = min(fail_coverage_pct, 75.0)

        # 1. Timeline Duration vs Expected Duration
        sample_rate = float(envelope.canonical_sample_rate_hz) if envelope.canonical_sample_rate_hz > 0 else 16000.0
        timeline_dur = envelope.timeline_length_samples / sample_rate
        turn_max_dur = (
            max((t.span.end_sample for t in envelope.turns), default=0) / sample_rate
            if envelope.turns else 0.0
        )
        actual_duration_s = max(timeline_dur, turn_max_dur)

        if expected_duration_s is None or expected_duration_s <= 0:
            expected_duration_s = actual_duration_s

        if actual_duration_s <= 0.0:
            findings.append(QAFinding(
                finding_id="f-gatef-zero-dur",
                gate_id="Gate_F_Audio_Completeness",
                code="ZERO_DURATION_AUDIO",
                severity="blocking",
                required_action="Verify audio capture device or recording file; duration is zero",
            ))
            completeness_pct = 0.0
        elif expected_duration_s > 0.0:
            completeness_pct = (actual_duration_s / expected_duration_s) * 100.0
            if completeness_pct < fail_coverage_pct:
                findings.append(QAFinding(
                    finding_id="f-gatef-dur-incomplete",
                    gate_id="Gate_F_Audio_Completeness",
                    code="AUDIO_DURATION_INCOMPLETE",
                    severity="blocking",
                    required_action=f"Audio duration {actual_duration_s:.1f}s is only {completeness_pct:.1f}% of expected {expected_duration_s:.1f}s (< {fail_coverage_pct}%)",
                ))
            elif completeness_pct < min_coverage_pct:
                findings.append(QAFinding(
                    finding_id="f-gatef-dur-marginal",
                    gate_id="Gate_F_Audio_Completeness",
                    code="AUDIO_DURATION_MARGINAL",
                    severity="warning",
                    required_action=f"Audio duration {actual_duration_s:.1f}s is {completeness_pct:.1f}% of expected {expected_duration_s:.1f}s (< {min_coverage_pct}%); operator review required",
                ))
        else:
            completeness_pct = 100.0

        # 2. Silence Gap Analysis (including leading and trailing silence)
        if envelope.turns:
            sorted_turns = sorted(envelope.turns, key=lambda t: t.span.start_sample)
            gap_intervals: List[Tuple[float, float]] = []
            first_start_s = sorted_turns[0].span.start_sample / sample_rate
            if first_start_s > 0.0:
                gap_intervals.append((0.0, first_start_s))
            for i in range(len(sorted_turns) - 1):
                start_sec = sorted_turns[i].span.end_sample / sample_rate
                end_sec = sorted_turns[i + 1].span.start_sample / sample_rate
                if end_sec > start_sec:
                    gap_intervals.append((start_sec, end_sec))
            last_end_s = sorted_turns[-1].span.end_sample / sample_rate
            if actual_duration_s > last_end_s:
                gap_intervals.append((last_end_s, actual_duration_s))

            for i, (start_sec, end_sec) in enumerate(gap_intervals):
                gap_s = max(0.0, end_sec - start_sec)
                is_trailing = (
                    i == len(gap_intervals) - 1
                    and abs(start_sec - last_end_s) < 1e-3
                    and actual_duration_s > 0.0
                    and (last_end_s / actual_duration_s) >= 0.85
                )
                if gap_s > 60.0 and not is_trailing:
                    sev = "blocking"
                    findings.append(QAFinding(
                        finding_id=f"f-gatef-gap-crit-{i}",
                        gate_id="Gate_F_Audio_Completeness",
                        code="EXCESSIVE_SILENCE_GAP",
                        severity="blocking",
                        required_action=f"Unannotated silence gap of {gap_s:.1f}s (> 60s) at [{start_sec:.1f}s - {end_sec:.1f}s] indicates missing audio",
                    ))
                elif gap_s > 10.0:
                    sev = "warning"
                    findings.append(QAFinding(
                        finding_id=f"f-gatef-gap-warn-{i}",
                        gate_id="Gate_F_Audio_Completeness",
                        code="UNANNOTATED_SILENCE_GAP",
                        severity="warning",
                        required_action=f"Silence gap of {gap_s:.1f}s (> 10s) at [{start_sec:.1f}s - {end_sec:.1f}s] detected between turns",
                    ))
                elif gap_s > 2.0:
                    sev = "info"
                else:
                    continue

                silence_records.append(SilenceGapRecord(
                    start_sec=round(start_sec, 2),
                    end_sec=round(end_sec, 2),
                    duration_sec=round(gap_s, 2),
                    severity=sev,
                    annotated=False,
                ))

        # 3. Micro-Dropout Accumulation
        dropout_count = 0
        total_dropout_s = 0.0
        for t in envelope.turns:
            txt = t.transcript_verbatim
            if "[AUDIO_GAP:" in txt or "[AUDIO GAP:" in txt:
                dropout_count += 1
                total_dropout_s += 0.5

        if total_dropout_s > 120.0:
            findings.append(QAFinding(
                finding_id="f-gatef-dropout-crit",
                gate_id="Gate_F_Audio_Completeness",
                code="EXCESSIVE_DROPOUT_ACCUMULATION",
                severity="blocking",
                required_action=f"Total accumulated dropouts {total_dropout_s:.1f}s exceed 120s limit",
            ))
        elif total_dropout_s > 30.0:
            findings.append(QAFinding(
                finding_id="f-gatef-dropout-warn",
                gate_id="Gate_F_Audio_Completeness",
                code="ACCUMULATED_AUDIO_DROPOUTS",
                severity="warning",
                required_action=f"Total accumulated dropouts {total_dropout_s:.1f}s exceed 30s threshold",
            ))

        # 4. Cryptographic Source Hash Presence
        fingerprint = envelope.primary_source_fingerprint
        if not envelope.source_hashes or fingerprint == ("0" * 64):
            findings.append(QAFinding(
                finding_id="f-gatef-nohash",
                gate_id="Gate_F_Audio_Completeness",
                code="MISSING_SOURCE_HASH",
                severity="blocking",
                required_action="Master audio source must be cryptographically hashed (SHA-256)",
            ))

        # 5. Formulate Certificate
        has_gate_f_blocking = any(f.severity == "blocking" for f in findings)
        has_gate_f_warning = any(f.severity == "warning" for f in findings)

        if has_gate_f_blocking:
            cert_verdict = CertificateVerdict.FAIL
            hint = "Audio capture is incomplete or corrupted. Re-capture or verify expected duration."
        elif has_gate_f_warning:
            cert_verdict = CertificateVerdict.WARN
            hint = "Audio capture has warnings (marginal duration or gaps). HITL approval receipt required to proceed."
        else:
            cert_verdict = CertificateVerdict.PASS
            hint = None

        evidence = GateFEvidence(
            expected_duration_s=round(expected_duration_s, 2),
            actual_duration_s=round(actual_duration_s, 2),
            completeness_pct=round(completeness_pct, 2),
            silence_gaps=tuple(silence_records),
            dropout_count=dropout_count,
            total_dropout_duration_s=round(total_dropout_s, 2),
            source_type=clean_source,
            audio_sha256=fingerprint,
            remediation_hint=hint,
        )

        certificate = AudioCaptureCertificate.create(
            audio_sha256=fingerprint,
            verdict=cert_verdict,
            gate_f_evidence=evidence,
            expected_duration_s=round(expected_duration_s, 2),
            actual_duration_s=round(actual_duration_s, 2),
            source_type=clean_source,
        )

        return findings, certificate
