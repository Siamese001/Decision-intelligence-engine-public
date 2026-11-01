"""Cross-Artifact Reconciliation Agent for Output 01, 02, and 03 Parity.

Executes a bounded Observe -> Decide -> Act -> Evaluate loop (max_steps=2, max_tool_calls=4)
to detect and surgically repair action-item owner drift and quote-pointer misalignment across
Output 02 (Structured Notes) and Output 03 (Executive Summary) while strictly preserving
Output 01 (Verbatim Transcript) speaker attributions as frozen and immutable.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.a2a.protocol import ClaimCheckPointer
from dual_pipeline.agent_architecture_core.outcome_streaming import stream_reconciler_outcomes
from dual_pipeline.agent_architecture_core.runtime_loop import (
    AgentRuntimeLoop,
    LoopTerminalOutcome,
    RuntimeLoopConfig,
)


@dataclass(frozen=True)
class SurgicalRepairDirective:
    """Targeted repair directive for Output 02 or Output 03 cross-artifact drift."""

    directive_id: str
    target_artifact: str  # "Output_02" | "Output_03"
    section: str
    defect_type: str  # "OWNER_DRIFT" | "UNANCHORED_QUOTE" | "ENTITY_SPELLING_DRIFT"
    original_span: str
    replacement_span: str
    grounding_quote: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciliationResult:
    """Verified cross-artifact reconciliation outcome and surgical repair ledger."""

    output_01_sha256: str
    output_01_immutable_verified: bool
    repaired_output_02: str
    repaired_output_03: str
    directives: List[SurgicalRepairDirective]
    parity_passed: bool
    speaker_collapse_detected: bool
    requires_hitl_escalation: bool
    steps_executed: int
    tool_calls_used: int
    terminal_outcome: str
    claim_checks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output_01_sha256": self.output_01_sha256,
            "output_01_immutable_verified": self.output_01_immutable_verified,
            "repaired_output_02": self.repaired_output_02,
            "repaired_output_03": self.repaired_output_03,
            "directives": [d.to_dict() for d in self.directives],
            "parity_passed": self.parity_passed,
            "speaker_collapse_detected": self.speaker_collapse_detected,
            "requires_hitl_escalation": self.requires_hitl_escalation,
            "steps_executed": self.steps_executed,
            "tool_calls_used": self.tool_calls_used,
            "terminal_outcome": self.terminal_outcome,
            "claim_checks": list(self.claim_checks),
        }


_OWNER_RE = re.compile(r"(\*\*Owner:\*\*|Owner:)\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.M)
_QUOTE_RE = re.compile(r'\[Quote:\s*"([^"]+)"\]')


def _extract_dialogue_lines(output_01_text: str) -> List[Tuple[str, str]]:
    """Extracts (speaker, utterance) pairs from Output 01 verbatim transcript."""
    pairs: List[Tuple[str, str]] = []
    for raw_line in output_01_text.splitlines():
        line = raw_line.strip()
        if ":" in line and not line.startswith("#"):
            spk, utt = line.split(":", 1)
            spk_clean = re.sub(r"[\*\#\[\]\(\)0-9\:\-]+", " ", spk).strip()
            utt_clean = utt.strip().strip('"')
            if spk_clean and utt_clean:
                pairs.append((spk_clean, utt_clean))
    return pairs


def _detect_mapping_owner_drift(
    o3_map: Mapping[str, Any],
    o2_map: Mapping[str, Any],
    target_list: List[SurgicalRepairDirective],
    learned: Mapping[str, str],
    integrator: Optional[Any],
    stream_outcomes: bool,
    trace_id: str,
) -> List[SurgicalRepairDirective]:
    for item_id, o3_owner in o3_map.items():
        canonical_owner = str(o2_map.get(item_id, "")).strip()
        o3_str = str(o3_owner).strip()
        if not o3_str or not canonical_owner or o3_str == canonical_owner or learned.get(o3_str) == canonical_owner:
            continue
        target_list.append(
            SurgicalRepairDirective(
                directive_id=f"repair-owner-{item_id}", target_artifact="Output_03", section="Action Items",
                defect_type="OWNER_DRIFT", original_span=f"Owner: {o3_str}", replacement_span=f"Owner: {canonical_owner}",
                grounding_quote=f"Grounded owner in Output 01/02: {canonical_owner}", confidence=0.94,
            )
        )
        stream_reconciler_outcomes(
            integrator, stream_outcomes=stream_outcomes, drift_pairs=[(o3_str, canonical_owner, "OWNER_DRIFT")],
            repaired_count=1, output_01_immutable_verified=True, trace_id=trace_id,
            extra_metadata={"raw_alias_pair": f"{o3_str}->{canonical_owner}"},
        )
    return target_list


def _collect_o2_owner_drift(
    o1_owners: Sequence[str],
    o2_matches: Sequence[Any],
    learned: Mapping[str, str],
) -> List[SurgicalRepairDirective]:
    if not o1_owners:
        return []
    directives: List[SurgicalRepairDirective] = []
    for idx, m in enumerate(o2_matches):
        o2_owner = m.group(2).strip()
        canonical_o1 = o1_owners[min(idx, len(o1_owners) - 1)]
        if learned.get(o2_owner) != canonical_o1 and o2_owner != canonical_o1:
            directives.append(
                SurgicalRepairDirective(
                    directive_id=f"repair-o2-owner-{idx + 1}", target_artifact="Output_02", section="Action Items",
                    defect_type="OWNER_DRIFT", original_span=m.group(0), replacement_span=f"{m.group(1)} {canonical_o1}",
                    grounding_quote=f"Grounded owner in Output 01: {canonical_o1}", confidence=0.94,
                )
            )
    return directives


def _collect_o3_owner_drift(
    o1_str: str,
    o2_owners: Sequence[str],
    o3_matches: Sequence[Any],
    learned: Mapping[str, str],
) -> List[SurgicalRepairDirective]:
    if not o2_owners or not o3_matches:
        return []
    directives: List[SurgicalRepairDirective] = []
    for idx, m in enumerate(o3_matches):
        o3_owner = m.group(2).strip()
        canonical_owner = o2_owners[min(idx, len(o2_owners) - 1)]
        if learned.get(o3_owner) != canonical_owner and o3_owner != canonical_owner and canonical_owner.lower() in o1_str.lower():
            directives.append(
                SurgicalRepairDirective(
                    directive_id=f"repair-owner-{idx + 1}", target_artifact="Output_03", section="Action Items",
                    defect_type="OWNER_DRIFT", original_span=m.group(0), replacement_span=f"{m.group(1)} {canonical_owner}",
                    grounding_quote=f"Grounded owner in Output 01/02: {canonical_owner}", confidence=0.94,
                )
            )
    return directives


def _detect_owner_drift_directives(
    output_01_text: Any,
    output_02_text: Any,
    output_03_text: Any = None,
    integrator: Optional[Any] = None,
    stream_outcomes: bool = False,
    trace_id: str = "trace-owner-drift",
    *,
    alias_registry_path: Optional[Any] = None,
) -> List[SurgicalRepairDirective]:
    """Detects when Output 03 action-item owner diverges from Output 02 / Output 01."""
    from pathlib import Path
    from dual_pipeline.transcript_qa_core.alias_registry import load_learned_aliases

    learned = load_learned_aliases(Path(alias_registry_path) if alias_registry_path else None)
    if isinstance(output_01_text, Mapping) and isinstance(output_02_text, Mapping):
        t_list = output_03_text if isinstance(output_03_text, list) else []
        return _detect_mapping_owner_drift(output_01_text, output_02_text, t_list, learned, integrator, stream_outcomes, trace_id)

    o1_str = str(output_01_text)
    o1_owners = [m.group(2).strip() for m in _OWNER_RE.finditer(o1_str)]
    o2_matches = list(_OWNER_RE.finditer(str(output_02_text or "")))
    o2_owners = [m.group(2).strip() for m in o2_matches]
    o3_matches = list(_OWNER_RE.finditer(str(output_03_text or "")))

    directives = _collect_o2_owner_drift(o1_owners, o2_matches, learned)
    directives.extend(_collect_o3_owner_drift(o1_str, o2_owners, o3_matches, learned))
    return directives


def _detect_unanchored_quote_directives(
    dialogue_pairs: Sequence[Tuple[str, str]],
    target_name: str,
    artifact_text: str,
) -> List[SurgicalRepairDirective]:
    """Detects [Quote: "..."] spans in Output 02/03 that drift from verbatim Output 01."""
    directives: List[SurgicalRepairDirective] = []
    full_utterances = [utt for _, utt in dialogue_pairs]
    for idx, m in enumerate(_QUOTE_RE.finditer(artifact_text)):
        q_text = m.group(1).strip()
        if any(q_text in utt for utt in full_utterances):
            continue
        q_tokens = {t.lower() for t in q_text.split() if len(t) >= 4}
        best_utt = ""
        best_overlap = 0
        for utt in full_utterances:
            u_tokens = {t.lower().strip(".,?!") for t in utt.split() if len(t) >= 4}
            overlap = len(q_tokens & u_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_utt = utt
        if best_utt and best_overlap >= 2:
            directives.append(
                SurgicalRepairDirective(
                    directive_id=f"repair-quote-{target_name.lower()}-{idx + 1}",
                    target_artifact=target_name,
                    section="Key Quotes & Evidence",
                    defect_type="UNANCHORED_QUOTE",
                    original_span=m.group(0),
                    replacement_span=f'[Quote: "{best_utt}"]',
                    grounding_quote=best_utt,
                    confidence=0.91,
                )
            )
    return directives


def _apply_directives(
    output_02: str,
    output_03: str,
    directives: Sequence[SurgicalRepairDirective],
) -> Tuple[str, str]:
    rep_02, rep_03 = output_02, output_03
    for d in directives:
        if d.target_artifact == "Output_02" and d.original_span in rep_02:
            rep_02 = rep_02.replace(d.original_span, d.replacement_span, 1)
        elif d.target_artifact == "Output_03" and d.original_span in rep_03:
            rep_03 = rep_03.replace(d.original_span, d.replacement_span, 1)
    return rep_02, rep_03


def _collect_all_drift_directives(
    output_01_text: str,
    dialogue_pairs: Sequence[Tuple[str, str]],
    rep_02: str,
    rep_03: str,
) -> List[SurgicalRepairDirective]:
    directives = _detect_owner_drift_directives(output_01_text, rep_02, rep_03)
    directives.extend(_detect_unanchored_quote_directives(dialogue_pairs, "Output_02", rep_02))
    directives.extend(_detect_unanchored_quote_directives(dialogue_pairs, "Output_03", rep_03))
    return directives


class CrossArtifactReconciliationAgent:
    """Reconciles entity, owner, and quote-pointer drift across Outputs 01, 02, and 03."""

    def __init__(
        self,
        *,
        max_steps: int = 2,
        max_tool_calls: int = 4,
        timeout_seconds: float = 20.0,
        integrator: Optional[Any] = None,
        stream_outcomes: bool = False,
    ) -> None:
        if max_steps <= 0 or max_steps > 4:
            raise ValueError("max_steps must be within [1, 4].")
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.integrator = integrator
        self.stream_outcomes = stream_outcomes

    def _run_reconciliation_step(
        self,
        loop: AgentRuntimeLoop,
        step_idx: int,
        output_01_text: str,
        dialogue_pairs: Sequence[Tuple[str, str]],
        o1_ptr: ClaimCheckPointer,
        directives: List[SurgicalRepairDirective],
        rep_02: str,
        rep_03: str,
    ) -> Tuple[str, str, Dict[str, Any]]:
        if step_idx == 1:
            directives.extend(_collect_all_drift_directives(output_01_text, dialogue_pairs, rep_02, rep_03))
            task, _ = loop.dispatch_subagent_task(
                recipient_agent_id="ingestion_specialist",
                objective="Audit cross-artifact owner and quote parity",
                scope_in=["Output_02", "Output_03"],
                scope_out=["Output_01_Speaker_Attribution"],
                allowed_tools=["verify_contract_compliance"],
                deliverable={"directives_emitted": len(directives)},
                definition_of_done="All Output 02/03 drift spans cataloged",
                claim_checks=[o1_ptr],
            )
            return rep_02, rep_03, {
                "tool_calls_used": 2, "tokens_used": 100, "a2a_task_id": task.task_id,
                "state_updates": {"scanned": True, "directives_emitted": len(directives)},
            }
        rep_02_new, rep_03_new = _apply_directives(rep_02, rep_03, directives)
        return rep_02_new, rep_03_new, {
            "tool_calls_used": 1, "tokens_used": 90,
            "state_updates": {"repaired": True, "rep_03_digest": hashlib.sha256(rep_03_new.encode("utf-8")).hexdigest()[:12]},
        }

    def _emit_reconciler_stream(
        self, directives: Sequence[SurgicalRepairDirective], immutable_ok: bool, trace_id: str
    ) -> None:
        drift_pairs: List[Tuple[str, str, str]] = []
        for d in directives:
            if d.defect_type in ("OWNER_DRIFT", "ENTITY_SPELLING_DRIFT"):
                m_orig = _OWNER_RE.search(d.original_span)
                m_repl = _OWNER_RE.search(d.replacement_span)
                f_id = m_orig.group(2).strip() if m_orig else d.original_span.strip()
                t_id = m_repl.group(2).strip() if m_repl else d.replacement_span.strip()
                drift_pairs.append((f_id, t_id, d.defect_type))
        extra_meta = (
            {
                "raw_alias_pair": f"{drift_pairs[0][0]}->{drift_pairs[0][1]}",
                "raw_alias_pairs": [f"{f}->{t}" for f, t, _ in drift_pairs],
            }
            if drift_pairs
            else None
        )
        stream_reconciler_outcomes(
            self.integrator, stream_outcomes=self.stream_outcomes, drift_pairs=drift_pairs,
            repaired_count=len(directives), output_01_immutable_verified=immutable_ok, trace_id=trace_id,
            extra_metadata=extra_meta,
        )

    def reconcile(
        self,
        *,
        output_01_text: str,
        output_02_text: str,
        output_03_text: str,
        speaker_turn_shares: Optional[Mapping[str, float]] = None,
        allow_output_01_mutation: bool = False,
    ) -> ReconciliationResult:
        """Executes cross-artifact parity checks and surgical Output 02/03 repair."""
        if allow_output_01_mutation:
            raise PermissionError("OUTPUT_01_SPEAKER_ATTRIBUTION_FROZEN: CrossArtifactReconciliationAgent cannot mutate Output 01.")
        if not output_01_text or not output_01_text.strip():
            raise ValueError("output_01_text must be non-empty.")

        before_o1_sha = hashlib.sha256(output_01_text.encode("utf-8")).hexdigest()
        collapse_detected = any(float(v) > 0.85 for v in (speaker_turn_shares or {}).values())
        dialogue_pairs = _extract_dialogue_lines(output_01_text)
        loop = AgentRuntimeLoop(
            RuntimeLoopConfig(
                agent_id="cross_artifact_reconciliation_agent", goal="Align Output 02 and Output 03 owners and quotes against frozen Output 01",
                max_steps=self.max_steps, max_tool_calls=self.max_tool_calls, timeout_seconds=self.timeout_seconds,
                allowed_tools=("verify_contract_compliance", "validate_rfc8259_canonical_hash"),
            ),
            integrator=self.integrator, stream_outcomes=self.stream_outcomes, _suppress_loop_success=True,
        )
        directives: List[SurgicalRepairDirective] = []
        rep_02, rep_03 = output_02_text, output_03_text
        o1_ptr = ClaimCheckPointer("src:transcript:output_01", "ev:output_01:verbatim", before_o1_sha, {"artifact": "Output_01"})

        def act_fn(_s: Dict[str, Any], _d: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
            nonlocal rep_02, rep_03
            rep_02, rep_03, res = self._run_reconciliation_step(
                loop, step_idx, output_01_text, dialogue_pairs, o1_ptr, directives, rep_02, rep_03
            )
            return res

        def evaluate_fn(_s: Dict[str, Any], _a: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
            clean = len(_collect_all_drift_directives(output_01_text, dialogue_pairs, rep_02, rep_03)) == 0
            return {"done": step_idx >= 2 or (step_idx == 1 and clean), "quality_score": 1.0 if clean else 0.5}

        loop_res = loop.execute(
            initial_state={"scanned": False},
            observe_fn=lambda _s, i: {"step": i, "directives_count": len(directives), "collapse": collapse_detected},
            decide_fn=lambda _o, i: {"action": f"step_{i}", "delegated": i == 1, "candidates": [(f"step_{i}", 0.93), ("noop", 0.07)]},
            act_fn=act_fn, evaluate_fn=evaluate_fn,
        )
        after_o1_sha = hashlib.sha256(output_01_text.encode("utf-8")).hexdigest()
        immutable_ok = before_o1_sha == after_o1_sha
        rem_drift = len(_collect_all_drift_directives(output_01_text, dialogue_pairs, rep_02, rep_03))
        self._emit_reconciler_stream(directives, immutable_ok, loop_res.trace_id)
        return ReconciliationResult(
            after_o1_sha, immutable_ok, rep_02, rep_03, directives,
            (rem_drift == 0 and immutable_ok and not collapse_detected), collapse_detected, collapse_detected,
            loop_res.steps_executed, loop_res.total_tool_calls,
            loop_res.terminal_outcome.value if isinstance(loop_res.terminal_outcome, LoopTerminalOutcome) else str(loop_res.terminal_outcome),
            [o1_ptr.to_dict()],
        )


CrossTranscriptEntityReconciliationAgent = CrossArtifactReconciliationAgent

__all__ = [
    "CrossArtifactReconciliationAgent",
    "CrossTranscriptEntityReconciliationAgent",
    "ReconciliationResult",
    "SurgicalRepairDirective",
]
