"""Property-based and stateful model-checking suite focused primarily on Copilot & Mock Live Recordings."""
from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from dual_pipeline.a2a.protocol import (
    A2ATask,
    A2ATaskStatus,
    ClaimCheckPointer,
    DelegationBudget,
)
from dual_pipeline.a2a.task_ledger import A2ATaskLedger
from dual_pipeline.cli.copilot_handlers import normalize_copilot_argv
from dual_pipeline.hitl_governance import compute_bayesian_calibrated_score
from dual_pipeline.l0.assumption_lifecycle import (
    CONTROLLED_EPISTEMIC_TYPES,
    normalize_epistemic_assertion_type,
)
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    create_attempt_record,
    record_copilot_attempt,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import (
    AbstainReasonCode,
    SpeakTier,
    evaluate_speak_gate,
)
from dual_pipeline.parameters import needs_operator_decision
from dual_pipeline.semantic_cache_core.merkle_cache import compute_subgraph_merkle_root
from tools.e2e_scenario_agent_core import (
    ELEVEN_BGE_M3_GATE_NAMES,
    compute_rfc8259_digest,
    evaluate_11_gate_matrix,
)


@settings(max_examples=35, deadline=None)
@given(
    intent_flag=st.sampled_from(["next-questions", "next-actions", "respond", "graph-context", "gaps", "connect"]),
    words_budget=st.integers(min_value=15, max_value=90),
)
def test_property_copilot_argv_normalization_and_request_parser(
    intent_flag: str, words_budget: int
) -> None:
    """Property: normalize_copilot_argv always routes -copilot to copilot subcommand and preserves --words."""
    norm = normalize_copilot_argv(["-copilot", "--intent", intent_flag, "--words", str(words_budget)])
    assert norm[0] == "copilot"
    assert "--intent" in norm
    assert intent_flag in norm
    assert norm[-2:] == ["--words", str(words_budget)]

    parsed = parse_copilot_request(f"Respond to Stefanos on SCADA Gateway Protocol within {words_budget} words")
    assert parsed.inferred_intent in ("respond", "graph-context", "next-questions", "next-actions", "gaps", "connect")
    assert "<copilot_request>" in parsed.to_xml_bounded_prompt()


@settings(max_examples=35, deadline=None)
@given(
    max_words=st.integers(min_value=5, max_value=20),
    extra_words=st.integers(min_value=1, max_value=15),
    unattested_num=st.integers(min_value=10, max_value=999),
)
def test_property_copilot_speak_gate_word_budget_and_number_invariants(
    max_words: int, extra_words: int, unattested_num: int
) -> None:
    """Property: Speak gate strictly abstains (T0) whenever word budget is exceeded or numbers are unattested."""
    ast_rec = {
        "assertion_id": "asrt_prop_01",
        "claim_text": "Stefanos Georgiades leads SCADA Gateway Protocol intake review across 2 sprints.",
        "predicate": "REQUIRES_ACTION",
        "canonical_status": "ACTIVE",
    }
    over_budget_text = " ".join(["Stefanos"] * (max_words + extra_words)) + "."
    dec_budget = evaluate_speak_gate(over_budget_text, assertion=ast_rec, max_words=max_words)
    assert dec_budget.should_speak is False
    assert dec_budget.tier == SpeakTier.T0
    assert dec_budget.abstain_reason == AbstainReasonCode.WORD_BUDGET_EXCEEDED

    num_text = f"Stefanos leads SCADA review across {unattested_num} sprints."
    dec_num = evaluate_speak_gate(num_text, assertion=ast_rec, max_words=50)
    assert dec_num.should_speak is False
    assert dec_num.tier == SpeakTier.T0
    assert dec_num.abstain_reason == AbstainReasonCode.NUMBER_MISMATCH


@settings(max_examples=40, deadline=None)
@given(
    base_score=st.floats(min_value=0.05, max_value=0.95, allow_nan=False, allow_infinity=False),
    affirmed=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    rejected=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)
def test_property_bayesian_calibration_bounds_and_monotonicity(
    base_score: float, affirmed: float, rejected: float
) -> None:
    """Property: Calibrated scores stay strictly within [0.05, 0.95] and increase monotonically with affirmations."""
    s1 = float(compute_bayesian_calibrated_score(base_score, affirmed, rejected))
    s2 = float(compute_bayesian_calibrated_score(base_score, affirmed + 5.0, rejected))
    assert 0.05 <= s1 <= 0.95
    assert 0.05 <= s2 <= 0.95
    assert s2 >= s1 - 1e-9


@settings(max_examples=40, deadline=None)
@given(
    top=st.floats(min_value=0.25, max_value=1.0, allow_nan=False, allow_infinity=False),
    spread=st.floats(min_value=0.0, max_value=0.20, allow_nan=False, allow_infinity=False),
)
def test_property_ssot_margin_le_20pct_always_surfaces(top: float, spread: float) -> None:
    """Property: Whenever top - second <= 0.20, needs_operator_decision returns True."""
    second = max(0.0, top - spread)
    if round(top - second, 6) <= 0.20:
        assert needs_operator_decision(top, second) is True


@settings(max_examples=30, deadline=None)
@given(gate_idx=st.integers(min_value=0, max_value=len(ELEVEN_BGE_M3_GATE_NAMES) - 1))
def test_property_11_gates_non_compensating_conjunction(gate_idx: int) -> None:
    """Property: Flipping any single gate input below threshold guarantees overall status == FAIL."""
    overrides = [
        {"real_bge_m3": False}, {"pair_binding": False}, {"coverage_complete": False},
        {"core_recall_at_k": 0.95}, {"delta_mrr": 0.95}, {"critical_failure_count": 1},
        {"max_rank_regression": 2}, {"repeat_rank_drift": 1}, {"forbidden_leakage_count": 1},
        {"max_latency_ms": 9999.0}, {"bm25_execution": False},
    ]
    res = evaluate_11_gate_matrix(overrides[gate_idx])
    assert res["gates"][ELEVEN_BGE_M3_GATE_NAMES[gate_idx]] is False
    assert res["status"] == "FAIL"


@settings(max_examples=30, deadline=None)
@given(
    run_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=4, max_size=24),
    passed_cnt=st.integers(min_value=0, max_value=29),
)
def test_property_rfc8259_digest_key_order_invariance(run_id: str, passed_cnt: int) -> None:
    """Property: compute_rfc8259_digest is invariant to key insertion order and ignores canonical_digest."""
    p1 = {"run_id": run_id, "passed": passed_cnt, "canonical_digest": "old"}
    p2 = {"canonical_digest": "different", "passed": passed_cnt, "run_id": run_id}
    assert compute_rfc8259_digest(p1) == compute_rfc8259_digest(p2)


@settings(max_examples=30, deadline=None)
@given(
    node_ids=st.lists(st.text(alphabet="abcdef0123456789", min_size=6, max_size=16), min_size=2, max_size=8, unique=True)
)
def test_property_subgraph_merkle_root_permutation_invariance(node_ids: list[str]) -> None:
    """Property: Subgraph Merkle root is invariant to evidence node ordering."""
    nodes = [{"node_id": nid, "assertion_id": f"as-{nid}", "content_hash": nid * 4} for nid in node_ids]
    assert compute_subgraph_merkle_root(nodes) == compute_subgraph_merkle_root(list(reversed(nodes)))


@settings(max_examples=30, deadline=None)
@given(raw_type=st.one_of(st.none(), st.text(min_size=0, max_size=32)))
def test_property_l0_epistemic_assertion_type_closed_domain(raw_type: str | None) -> None:
    """Property: normalize_epistemic_assertion_type always maps arbitrary strings into CONTROLLED_EPISTEMIC_TYPES."""
    assert normalize_epistemic_assertion_type(raw_type) in CONTROLLED_EPISTEMIC_TYPES


class CopilotMockRecordingStateMachine(RuleBasedStateMachine):
    """Stateful model-checking machine driving a Mock Live Recording session and Copilot Attempt Ledger."""

    def __init__(self) -> None:
        super().__init__()
        self._tmp = tempfile.TemporaryDirectory(prefix="prop_copilot_mock_")
        self.root = Path(self._tmp.name)
        self.cm = ConversationContextModel(
            project_scope="partner_google_nee",
            attendees=["Amit Ayer", "Stefanos Georgiades", "Dana Lin"],
        )
        self.valid_turns = 0
        self.quarantined_turns = 0
        self.attempts_recorded = 0

    def teardown(self) -> None:
        self._tmp.cleanup()

    @rule(
        speaker=st.sampled_from(["Amit Ayer", "Stefanos Georgiades", "Dana Lin"]),
        is_question=st.booleans(),
    )
    def ingest_mock_live_turn(self, speaker: str, is_question: bool) -> None:
        self.valid_turns += 1
        sec = float((self.valid_turns + self.quarantined_turns) * 10)
        txt = (
            f"Can we verify the SCADA Gateway Protocol milestone {self.valid_turns}?"
            if is_question
            else f"We will deliver SCADA Gateway Protocol component {self.valid_turns}."
        )
        self.cm.add_turn(speaker=speaker, text=txt, timestamp="00:01:00", turn_sec=sec)

    @rule()
    def ingest_c19_quarantined_turn(self) -> None:
        self.quarantined_turns += 1
        sec = float((self.valid_turns + self.quarantined_turns) * 10)
        self.cm.add_turn(
            speaker="Amit Ayer",
            text="Confidential career leverage executive compensation band note.",
            timestamp="00:01:30",
            turn_sec=sec,
        )

    @rule(success=st.booleans())
    def record_copilot_attempt_step(self, success: bool) -> None:
        self.attempts_recorded += 1
        rec = create_attempt_record(
            terminal_outcome="SUCCESS" if success else "FAIL",
            terminal_reason="OK" if success else "UNGROUNDED_CLAIM",
            parsed_intent="respond",
            session_id="sess-prop-mock-live",
            context_model=self.cm,
        )
        record_copilot_attempt(rec, output_dir=self.root)

    @invariant()
    def mock_recording_and_copilot_invariants_hold(self) -> None:
        focus = self.cm.focus_turns()
        assert all("$1.2M" not in t["text"] and t["speaker"] != "[QUARANTINED]" for t in focus)
        assert all("1.2m" not in s.lower() for s in self.cm.retrieval_seeds())
        assert len(self.cm.compute_hash()) == 64
        att_file = self.root / "copilot_attempts.jsonl"
        if self.attempts_recorded > 0:
            assert att_file.is_file()
            lines = [ln for ln in att_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
            assert len(lines) == self.attempts_recorded


class A2ALedgerStateMachine(RuleBasedStateMachine):
    """Stateful model-checking machine verifying A2ATaskLedger lifecycle transitions and idempotency."""

    def __init__(self) -> None:
        super().__init__()
        self._tmp = tempfile.TemporaryDirectory(prefix="prop_a2a_")
        self.ledger = A2ATaskLedger(Path(self._tmp.name) / "a2a.sqlite")
        self.expected_status: dict[str, A2ATaskStatus] = {}
        self.counter = 0

    def teardown(self) -> None:
        self._tmp.cleanup()

    @rule()
    def submit_new_task(self) -> None:
        self.counter += 1
        tid = f"task-{self.counter}"
        task = A2ATask(
            task_id=tid, parent_task_id=None, correlation_id="corr-prop",
            idempotency_key=f"idem-{self.counter}", sender_agent_id="parent", recipient_agent_id="worker",
            objective="Run stateful property check", status=A2ATaskStatus.SUBMITTED,
            scope_in=["tools"], scope_out=[], allowed_tools=["read_file"],
            budget=DelegationBudget(max_turns=3, max_tool_calls=10, timeout_seconds=15.0, max_cost=0.25),
            claim_checks=[ClaimCheckPointer(source_id="src-1", evidence_id="ev-1", content_digest="a" * 64)],
            input_payload={"seq": self.counter},
        )
        self.ledger.record_task_submission(task)
        assert self.ledger.record_task_submission(task).task_id == tid
        self.expected_status[tid] = A2ATaskStatus.SUBMITTED

    @rule()
    def advance_tasks(self) -> None:
        for tid, st_val in list(self.expected_status.items()):
            if st_val == A2ATaskStatus.SUBMITTED:
                self.ledger.update_task_status(tid, A2ATaskStatus.ACCEPTED)
                self.expected_status[tid] = A2ATaskStatus.ACCEPTED
            elif st_val == A2ATaskStatus.ACCEPTED:
                self.ledger.update_task_status(tid, A2ATaskStatus.WORKING)
                self.expected_status[tid] = A2ATaskStatus.WORKING
            elif st_val == A2ATaskStatus.WORKING:
                self.ledger.update_task_status(tid, A2ATaskStatus.COMPLETED, output_payload={"ok": True})
                self.expected_status[tid] = A2ATaskStatus.COMPLETED

    @invariant()
    def ledger_matches_model_state(self) -> None:
        for tid, exp in self.expected_status.items():
            actual = self.ledger.get_task(tid)
            assert actual is not None and actual.status == exp


TestCopilotMockRecordingStateMachine = CopilotMockRecordingStateMachine.TestCase
TestCopilotMockRecordingStateMachine.settings = settings(max_examples=15, stateful_step_count=10, deadline=None)

TestA2ALedgerStateMachine = A2ALedgerStateMachine.TestCase
TestA2ALedgerStateMachine.settings = settings(max_examples=15, stateful_step_count=10, deadline=None)
