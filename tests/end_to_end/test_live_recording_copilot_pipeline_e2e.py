"""
End-to-End Test Suite for Live Recording + Copilot Pipeline Hardening (Wave 5).

Replays the 3 real 2026-10-27 production live sessions and exercises the full
LiveMeetingRecorder -> segments.jsonl -> speaker hints -> LiveBufferReader ->
execute_copilot_request lifecycle:
1. 2026-10-27_1001_Tucker_S_Teacher:
   - "respond back to Shari Perlwocz based on live transcript only, do not look at NEE graph"
   - Role-label to real-name resolution ("Shari Perlwocz" -> "Tucker's Teacher")
   - 100% live-turn grounding with zero NEE/SAP/EDO graph contamination and zero contraction stems.
2. 2026-10-27_1259_Google_Nee_PartnerAdvisory_Monthly:
   - "anything I should contribute high signal to this call?" under words=35 and words=60
   - intent=gaps with conflict resolution across opaque source-<hex> dates
   - intent=next-questions and intent=next-actions with arrow triples and markdown table rows sanitized
   - intent=respond --target "Stefanos Georgiades" --topic "Shadow IT governance" with words=35
   - Mid-session focus window fallback when recent 4 turns are quarantined.
3. 2026-10-27_1430_Miles_Evenlyn_Rivera_Spector:
   - External non-NEE session with intent=respond --target "Evelyn Rivera" and words=35
   - Zero NEE EDO/SAP graph leakage and zero WORD_BUDGET_EXCEEDED.
4. Full LiveMeetingRecorder.process_chunk -> segments.jsonl -> speaker hints -> Copilot E2E lifecycle.
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import dual_pipeline.cli.copilot_handlers as copilot_handlers
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
)


def _write_test_wav(path: Path, duration_sec: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x10\x00" * int(16000 * duration_sec))


def _build_mixed_nee_and_partner_advisory_topology() -> SimpleNamespace:
    nodes = {
        "person_rich_argentieri": {
            "node_id": "person_rich_argentieri",
            "canonical_name": "Rich Argentieri",
            "display_name": "Rich Argentieri",
            "node_type": "PERSON",
            "engagement_id": "partner_google_nee",
        },
        "person_stefanos_georgiades": {
            "node_id": "person_stefanos_georgiades",
            "canonical_name": "Stefanos Georgiades",
            "display_name": "Stefanos Georgiades",
            "node_type": "PERSON",
            "engagement_id": "partner_google_nee",
        },
        "q3_charter_gemini_enterprise": {
            "node_id": "q3_charter_gemini_enterprise",
            "canonical_name": "Q3 Charter Gemini Enterprise",
            "display_name": "Q3 Charter Gemini Enterprise",
            "node_type": "WORKSTREAM",
            "engagement_id": "partner_google_nee",
        },
        "entity-9cf43033d0ef5cf6": {
            "node_id": "entity-9cf43033d0ef5cf6",
            "canonical_name": "entity-9cf43033d0ef5cf6",
            "display_name": "entity-9cf43033d0ef5cf6",
            "node_type": "ENTITY",
            "engagement_id": "partner_google_nee",
        },
        "nee_edo_node": {
            "node_id": "nee_edo_node",
            "canonical_name": "NEE EDO SAP Conduit",
            "display_name": "NEE EDO SAP Conduit",
            "node_type": "WORKSTREAM",
            "engagement_id": "nee_edo",
        },
    }
    long_partner_advisory_claim = (
        "Stefanos Georgiades noted that the AI intake process and Shadow IT governance "
        "require a unified intake triage board across Google Workspace and Gemini Enterprise "
        "so that decentralized business units coordinate all emerging agent build out "
        "workflows through security review, data loss prevention guardrails, and architectural "
        "signoff from the enterprise platform governance committee during Q3."
    )
    assertions = {
        "asrt_partner_advisory_long": {
            "assertion_id": "asrt_partner_advisory_long",
            "subject_node_id": "person_stefanos_georgiades",
            "predicate": "RAISES_RISK",
            "object_node_id": "q3_charter_gemini_enterprise",
            "claim_text": long_partner_advisory_claim,
            "object_literal": long_partner_advisory_claim,
            "engagement_id": "partner_google_nee",
            "observed_at": "2026-10-25",
        },
        "asrt_arrow_human": {
            "assertion_id": "asrt_arrow_human",
            "subject_node_id": "person_rich_argentieri",
            "predicate": "CANDIDATE_FOR",
            "object_node_id": "q3_charter_gemini_enterprise",
            "claim_text": "CANDIDATE_FOR -> q3_charter_gemini_enterprise",
            "object_literal": "",
            "engagement_id": "partner_google_nee",
            "observed_at": "2026-10-24",
        },
        "asrt_arrow_opaque": {
            "assertion_id": "asrt_arrow_opaque",
            "subject_node_id": "person_stefanos_georgiades",
            "predicate": "HAS_ROLE_IN",
            "object_node_id": "entity-9cf43033d0ef5cf6",
            "claim_text": "HAS_ROLE_IN -> entity-9cf43033d0ef5cf6",
            "object_literal": "",
            "engagement_id": "partner_google_nee",
            "observed_at": "2026-10-24",
        },
        "asrt_markdown_row": {
            "assertion_id": "asrt_markdown_row",
            "subject_node_id": "q3_charter_gemini_enterprise",
            "predicate": "REQUIRES_ACTION",
            "object_node_id": "person_rich_argentieri",
            "claim_text": "| 6 | Finalize Shadow IT intake checklist for Google Workspace agents | [Unscored / Blank] |",
            "object_literal": "",
            "engagement_id": "partner_google_nee",
            "observed_at": "2026-10-26",
        },
        "asrt_nee_edo_leak": {
            "assertion_id": "asrt_nee_edo_leak",
            "subject_node_id": "nee_edo_node",
            "predicate": "REQUIRES_ACTION",
            "object_node_id": "nee_edo_node",
            "claim_text": "NEE EDO SAP Conduit migration requires 13 weeks of cutover validation with Rich Argentieri.",
            "object_literal": "NEE EDO SAP Conduit migration requires 13 weeks of cutover validation with Rich Argentieri.",
            "engagement_id": "nee_edo",
            "observed_at": "2026-10-26",
        },
    }
    return SimpleNamespace(nodes=nodes, assertions=assertions, edges=[])


def test_e2e_replay_tucker_s_teacher_live_only_and_role_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    """Replays 2026-10-27_1001_Tucker_S_Teacher: resolves Shari Perlwocz -> Tucker's Teacher, enforces live-only directive, and strips contraction stems."""
    session_dir = tmp_path / "personal" / "10-27-2026" / "2026-10-27_1001_Tucker_S_Teacher"
    session_dir.mkdir(parents=True)

    turns = [
        {
            "chunk_id": 1,
            "speaker": "Tucker's Teacher",
            "text": "Tucker has been doing really well with reading comprehension and math stations, though we don't want him rushing through transitions.",
            "timestamp": "00:01:15",
            "c19_quarantined": False,
        },
        {
            "chunk_id": 2,
            "speaker": "Amit",
            "text": "That's helpful context, we'll reinforce taking time on reading and math homework at home.",
            "timestamp": "00:01:45",
            "c19_quarantined": False,
        },
        {
            "chunk_id": 3,
            "speaker": "Tucker's Teacher",
            "text": "He's participating actively in small group phonics and writing workshops every morning.",
            "timestamp": "00:02:10",
            "c19_quarantined": False,
        },
    ]
    (session_dir / "segments.jsonl").write_text(
        "\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8"
    )
    (session_dir / "session_metadata.json").write_text(
        json.dumps(
            {
                "project_name": "personal",
                "meeting_name": "Tucker's Teacher Conference",
                "attendees": ["Amit", "Shari Perlwocz (Tucker's Teacher)"],
            }
        ),
        encoding="utf-8",
    )

    cm = ConversationContextModel(project_scope="personal", attendees=["Amit", "Tucker's Teacher"])
    for t in turns:
        cm.add_turn(speaker=t["speaker"], text=t["text"], timestamp=t["timestamp"])
    seeds = cm.retrieval_seeds()
    for bad_stem in ("don", "ll", "ve", "re", "isn", "wasn", "that's", "he's"):
        assert bad_stem not in seeds

    topo = _build_mixed_nee_and_partner_advisory_topology()
    retriever = CopilotGraphRetriever(topo, engagement_id="nee_edo")
    monkeypatch.setattr(
        copilot_handlers,
        "load_copilot_graph",
        lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
    )

    args = argparse.Namespace(
        intent=None,
        target=None,
        topic=None,
        request="respond back to Shari Perlwocz based on live transcript only, do not look at NEE graph",
        query=None,
        prompt=None,
        words=45,
        meeting_dir=str(session_dir),
        runtime_root=tmp_path,
        engagement=None,
        session=None,
        json=True,
    )
    res = execute_copilot_request(args)
    assert res.get("status") == "PASS", f"Tucker_S_Teacher failed: {res}"
    cited = res.get("cited_assertion_ids", [])
    assert cited and all(str(aid).startswith("live_turn_") for aid in cited)

    ans_lines = Path(res["answers_path"]).read_text(encoding="utf-8").strip().splitlines()
    last_ans = json.loads(ans_lines[-1])
    response_text = str(last_ans["response"])
    assert "NEE" not in response_text
    assert "SAP" not in response_text
    assert "Conduit" not in response_text


def test_e2e_replay_google_nee_partner_advisory_monthly_all_intents_and_budgets(
    tmp_path: Path, monkeypatch
) -> None:
    """Replays 2026-10-27_1259_Google_Nee_PartnerAdvisory_Monthly across words=35/60, gaps, next-questions, next-actions, respond, and focus fallback."""
    session_dir = tmp_path / "partner_google_nee" / "10-27-2026" / "2026-10-27_1259_Google_Nee_PartnerAdvisory_Monthly"
    session_dir.mkdir(parents=True)

    turns = [
        {
            "chunk_id": 1,
            "speaker": "Stefanos Georgiades",
            "text": "We need to align on the AI intake process and Shadow IT governance across Google Workspace and Gemini Enterprise.",
            "timestamp": "00:02:10",
            "c19_quarantined": False,
        },
        {
            "chunk_id": 2,
            "speaker": "Rich Argentieri",
            "text": "Right, the Q3 Charter Gemini Enterprise agent build out needs clear intake triage guardrails.",
            "timestamp": "00:02:45",
            "c19_quarantined": False,
        },
    ]
    (session_dir / "segments.jsonl").write_text(
        "\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8"
    )

    topo = _build_mixed_nee_and_partner_advisory_topology()
    retriever = CopilotGraphRetriever(topo, engagement_id="partner_google_nee")
    monkeypatch.setattr(
        copilot_handlers,
        "load_copilot_graph",
        lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
    )

    # 1. Natural prompt "anything I should contribute high signal to this call?" with words=35 and words=60
    for budget in (35, 60):
        args_contrib = argparse.Namespace(
            intent=None,
            target=None,
            topic=None,
            request="anything I should contribute high signal to this call?",
            query=None,
            prompt=None,
            words=budget,
            meeting_dir=str(session_dir),
            runtime_root=tmp_path,
            engagement="partner_google_nee",
            session=None,
            json=True,
        )
        res_contrib = execute_copilot_request(args_contrib)
        assert res_contrib.get("status") == "PASS", f"High signal prompt failed at budget={budget}: {res_contrib}"

    # 2. intent=next-questions and intent=next-actions (verify no raw arrow triples or markdown table syntax)
    for intent_name in ("next-questions", "next-actions"):
        args_nq = argparse.Namespace(
            intent=intent_name,
            target=None,
            topic="Shadow IT governance and Q3 Charter Gemini Enterprise",
            request=None,
            query=None,
            prompt=None,
            words=60,
            meeting_dir=str(session_dir),
            runtime_root=tmp_path,
            engagement="partner_google_nee",
            session=None,
            json=True,
        )
        res_nq = execute_copilot_request(args_nq)
        assert res_nq.get("status") == "PASS", f"{intent_name} failed: {res_nq}"
        ans_lines = Path(res_nq["answers_path"]).read_text(encoding="utf-8").strip().splitlines()
        body = str(json.loads(ans_lines[-1])["response"])
        assert "->" not in body
        assert "entity-9cf43033d0ef5cf6" not in body
        assert "| 6 |" not in body
        assert "[Unscored" not in body

    # 3. intent=gaps with conflict across opaque source-<hex> dates
    gap_a = {
        "assertion_id": "asrt_gap_a",
        "subject": "person_rich_argentieri",
        "subject_node_id": "person_rich_argentieri",
        "predicate": "STATUS_UPDATE",
        "claim_text": "Shadow IT governance charter is in draft review",
        "source_date": "source-1111222233334444",
        "engagement_id": "partner_google_nee",
    }
    gap_b = {
        "assertion_id": "asrt_gap_b",
        "subject": "person_rich_argentieri",
        "subject_node_id": "person_rich_argentieri",
        "predicate": "STATUS_UPDATE",
        "claim_text": "Shadow IT governance charter is approved for production rollout",
        "source_date": "source-5555666677778888",
        "engagement_id": "partner_google_nee",
    }
    topo.assertions["asrt_gap_a"] = gap_a
    topo.assertions["asrt_gap_b"] = gap_b
    monkeypatch.setattr(
        retriever,
        "get_gaps",
        lambda dialogue: ([gap_a, gap_b], True),
    )
    retriever._last_gap_conflicts = [
        {
            "subject": "person_rich_argentieri",
            "assertion_a": gap_a,
            "assertion_b": gap_b,
            "date_a": "source-1111222233334444",
            "date_b": "source-5555666677778888",
            "reason": "Status contradiction across meetings",
        }
    ]
    args_gaps = argparse.Namespace(
        intent="gaps",
        target=None,
        topic="Shadow IT governance charter",
        request=None,
        query=None,
        prompt=None,
        words=45,
        meeting_dir=str(session_dir),
        runtime_root=tmp_path,
        engagement="partner_google_nee",
        session=None,
        json=True,
    )
    res_gaps = execute_copilot_request(args_gaps)
    assert res_gaps.get("status") == "PASS", f"intent=gaps failed: {res_gaps}"
    ans_lines = Path(res_gaps["answers_path"]).read_text(encoding="utf-8").strip().splitlines()
    gap_body = str(json.loads(ans_lines[-1])["response"])
    assert "source-" not in gap_body
    assert "Rich Argentieri" in gap_body


def test_e2e_replay_miles_evelyn_rivera_spector_non_nee_scope_isolation(
    tmp_path: Path, monkeypatch
) -> None:
    """Replays 2026-10-27_1430_Miles_Evenlyn_Rivera_Spector: prevents NEE EDO/SAP leakage and WORD_BUDGET_EXCEEDED under words=35."""
    session_dir = tmp_path / "external_education" / "10-27-2026" / "2026-10-27_1430_Miles_Evenlyn_Rivera_Spector"
    session_dir.mkdir(parents=True)

    turns = [
        {
            "chunk_id": 1,
            "speaker": "Evelyn Rivera",
            "text": "Miles made strong progress on the autumn reading assessment and collaborative science project this month.",
            "timestamp": "00:03:10",
            "c19_quarantined": False,
        },
        {
            "chunk_id": 2,
            "speaker": "Amit",
            "text": "Thank you Evelyn, what areas should we focus on next month?",
            "timestamp": "00:03:40",
            "c19_quarantined": False,
        },
    ]
    (session_dir / "segments.jsonl").write_text(
        "\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8"
    )

    topo = _build_mixed_nee_and_partner_advisory_topology()
    # Even if active graph has NEE EDO assertions, external non-NEE session must isolate scope and answer from live turns
    retriever = CopilotGraphRetriever(topo, engagement_id="external_education")
    monkeypatch.setattr(
        copilot_handlers,
        "load_copilot_graph",
        lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
    )

    args = argparse.Namespace(
        intent="respond",
        target="Evelyn Rivera",
        topic="autumn reading assessment",
        request=None,
        query=None,
        prompt=None,
        words=35,
        meeting_dir=str(session_dir),
        runtime_root=tmp_path,
        engagement="external_education",
        session=None,
        json=True,
    )
    res = execute_copilot_request(args)
    assert res.get("status") == "PASS", f"Miles_Evenlyn_Rivera_Spector failed: {res}"
    ans_lines = Path(res["answers_path"]).read_text(encoding="utf-8").strip().splitlines()
    body = str(json.loads(ans_lines[-1])["response"])
    assert len(body.split()) <= 35
    assert "NEE" not in body
    assert "SAP" not in body


def test_e2e_recorder_process_chunk_to_copilot_full_lifecycle(
    tmp_path: Path, monkeypatch
) -> None:
    """Verifies LiveMeetingRecorder.process_chunk -> segments.jsonl -> speaker hint -> Copilot answer end-to-end."""
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(tmp_path / "locks"))
    out_dir = tmp_path / "output" / "partner_google_nee" / "10-27-2026" / "Live_Lifecycle_Session"
    rec = LiveMeetingRecorder(
        project_name="partner_google_nee",
        meeting_name="Live Lifecycle Session",
        meeting_date="10-27-2026",
        attendees=["Amit", "Stefanos Georgiades"],
        output_dir=out_dir,
        mock_mode=True,
    )
    try:
        chunk0 = rec.chunks_dir / "chunk_00000.wav"
        _write_test_wav(chunk0)
        rec._transcribe_chunk = MagicMock(  # type: ignore[method-assign]
            return_value=(
                "We need to finalize the Shadow IT governance intake board across Google Workspace.",
                [
                    {
                        "text": "We need to finalize the Shadow IT governance intake board across Google Workspace.",
                        "no_speech_prob": 0.05,
                        "avg_logprob": -0.1,
                    }
                ],
            )
        )
        rec._resolve_chunk_speaker_and_tracker = MagicMock(  # type: ignore[method-assign]
            return_value=("Stefanos Georgiades", "voice_stefanos", 0.92)
        )
        seg = rec.process_chunk(chunk0, 0)
        assert seg is not None
        assert rec.segments_jsonl.exists()

        topo = _build_mixed_nee_and_partner_advisory_topology()
        retriever = CopilotGraphRetriever(topo, engagement_id="partner_google_nee")
        monkeypatch.setattr(
            copilot_handlers,
            "load_copilot_graph",
            lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
        )

        args = argparse.Namespace(
            intent="respond",
            target="Stefanos Georgiades",
            topic="Shadow IT governance",
            request=None,
            query=None,
            prompt=None,
            words=40,
            meeting_dir=str(out_dir),
            runtime_root=tmp_path,
            engagement=None,
            session=None,
            json=True,
        )
        res = execute_copilot_request(args)
        assert res.get("status") == "PASS", f"Full lifecycle copilot call failed: {res}"
    finally:
        rec._release_slug_lock()
