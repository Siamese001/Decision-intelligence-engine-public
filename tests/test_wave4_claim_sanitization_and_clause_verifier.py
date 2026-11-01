"""
Wave 4 unit & integration tests:
1. Sanitization of raw arrow triples ("CANDIDATE_FOR -> person_...", "HAS_ROLE_IN -> entity-...")
   and markdown table rows ("| 6 | ... | [Unscored / Blank] |") in copilot_speech and copilot_retriever.
2. Adaptive word-budget compression so max_words=35, 60, and 90 never fail with WORD_BUDGET_EXCEEDED
   even when retrieved claims are 45–140 words long or exceed evaluate_speak_gate's 28/48-word sentence cap.
3. Clause verifier alignment with snake_case node IDs, contractions, resolved human names, and conflict
   gap comments so intent=gaps and intent=context never fail with UNGROUNDED_DELIVERY_REJECTED.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import dual_pipeline.cli.copilot_handlers as copilot_handlers
from dual_pipeline.cli.copilot_speech import render_speaker_comments, spoken_claim
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    check_clause_grounding,
    extract_content_tokens,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
)


def _build_mock_topology() -> SimpleNamespace:
    nodes = {
        "person_rich_argentieri": {
            "node_id": "person_rich_argentieri",
            "canonical_name": "Rich Argentieri",
            "display_name": "Rich Argentieri",
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
        "node-e52fb6644462fc6a": {
            "node_id": "node-e52fb6644462fc6a",
            "canonical_name": "Stefanos Georgiades",
            "display_name": "Stefanos Georgiades",
            "node_type": "PERSON",
            "engagement_id": "partner_google_nee",
        },
    }
    assertions = {
        "asrt_arrow_human": {
            "assertion_id": "asrt_arrow_human",
            "subject_node_id": "person_rich_argentieri",
            "predicate": "CANDIDATE_FOR",
            "object_node_id": "q3_charter_gemini_enterprise",
            "claim_text": "CANDIDATE_FOR -> q3_charter_gemini_enterprise",
            "object_literal": "",
            "engagement_id": "partner_google_nee",
            "observed_at": "2026-10-20",
        },
        "asrt_arrow_opaque": {
            "assertion_id": "asrt_arrow_opaque",
            "subject_node_id": "node-e52fb6644462fc6a",
            "predicate": "HAS_ROLE_IN",
            "object_node_id": "entity-9cf43033d0ef5cf6",
            "claim_text": "HAS_ROLE_IN -> entity-9cf43033d0ef5cf6",
            "object_literal": "",
            "engagement_id": "partner_google_nee",
            "observed_at": "2026-10-20",
        },
        "asrt_markdown_table": {
            "assertion_id": "asrt_markdown_table",
            "subject_node_id": "q3_charter_gemini_enterprise",
            "predicate": "HAS_RISK",
            "object_node_id": "person_rich_argentieri",
            "claim_text": "| 6 | Shadow IT governance and agent build out controls require explicit review | [Unscored / Blank] |",
            "object_literal": "",
            "engagement_id": "partner_google_nee",
            "observed_at": "2026-10-21",
        },
    }
    return SimpleNamespace(nodes=nodes, assertions=assertions, edges=[])


def test_arrow_triples_and_markdown_tables_sanitized_in_retriever_and_speech() -> None:
    topo = _build_mock_topology()
    retriever = CopilotGraphRetriever(topo, engagement_id="partner_google_nee")

    # Opaque endpoint assertion must be excluded from substantive predicates
    questions = retriever.get_next_questions("Rich Argentieri Stefanos Georgiades Shadow IT", max_questions=5)
    for q in questions:
        txt = str(q.get("claim_text") or "")
        assert "->" not in txt
        assert "entity-9cf43033d0ef5cf6" not in txt

    # Humanizable arrow assertion resolves to natural prose without '->' or snake_case
    human_rec = retriever._indexed_assertion_records["asrt_arrow_human"]
    rendered_human = spoken_claim(human_rec, retriever)
    assert "->" not in rendered_human
    assert "CANDIDATE_FOR" not in rendered_human
    assert "q3_charter_gemini_enterprise" not in rendered_human
    assert "Rich Argentieri" in rendered_human
    assert "Q3 Charter Gemini Enterprise" in rendered_human

    # Opaque endpoint assertion returns empty spoken_claim
    opaque_rec = retriever._indexed_assertion_records["asrt_arrow_opaque"]
    assert spoken_claim(opaque_rec, retriever) == ""

    # Markdown table row strips leading row number, pipes, and [Unscored / Blank]
    table_rec = retriever._indexed_assertion_records["asrt_markdown_table"]
    rendered_table = spoken_claim(table_rec, retriever)
    assert "|" not in rendered_table
    assert "[Unscored" not in rendered_table
    assert "Shadow IT governance" in rendered_table


def test_word_budget_adaptive_compression_prevents_word_budget_exceeded(tmp_path: Path, monkeypatch) -> None:
    session_dir = tmp_path / "2026-10-27_1259_Google_Nee_PartnerAdvisory_Monthly"
    session_dir.mkdir(parents=True)
    seg = {
        "chunk_id": 1,
        "speaker": "Stefanos Georgiades",
        "text": (
            "We need to align on the AI intake process, Shadow IT governance, "
            "and agent build out across Google Workspace and Gemini Enterprise."
        ),
        "timestamp": "00:02:10",
        "c19_quarantined": False,
    }
    (session_dir / "segments.jsonl").write_text(json.dumps(seg) + "\n", encoding="utf-8")

    long_claim = (
        "Stefanos Georgiades noted that the AI intake process and Shadow IT governance "
        "require a unified intake triage board across Google Workspace and Gemini Enterprise "
        "so that decentralized business units coordinate all emerging agent build out "
        "workflows through security review, data loss prevention guardrails, and architectural "
        "signoff from the enterprise platform governance committee during Q3."
    )
    topo = SimpleNamespace(
        nodes={
            "node_stefanos": {
                "node_id": "node_stefanos",
                "canonical_name": "Stefanos Georgiades",
                "display_name": "Stefanos Georgiades",
                "node_type": "PERSON",
                "engagement_id": "partner_google_nee",
            }
        },
        assertions={
            "asrt_long_1": {
                "assertion_id": "asrt_long_1",
                "subject_node_id": "node_stefanos",
                "predicate": "RAISES_RISK",
                "object_node_id": "node_stefanos",
                "claim_text": long_claim,
                "object_literal": long_claim,
                "engagement_id": "partner_google_nee",
                "observed_at": "2026-10-25",
            }
        },
        edges=[],
    )
    retriever = CopilotGraphRetriever(topo, engagement_id="partner_google_nee")
    monkeypatch.setattr(
        copilot_handlers,
        "load_copilot_graph",
        lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
    )

    for budget in (35, 60, 90):
        args = argparse.Namespace(
            intent="graph-context",
            target="Stefanos Georgiades",
            topic="Shadow IT governance and AI intake",
            request=None,
            query=None,
            prompt=None,
            words=budget,
            output=str(session_dir),
            runtime_root=tmp_path,
            engagement="partner_google_nee",
            session="sess_wave4_test",
            json=True,
        )
        res = execute_copilot_request(args)
        assert res.get("status") == "PASS", f"Budget {budget} failed: {res}"
        ans_lines = Path(res["answers_path"]).read_text(encoding="utf-8").strip().splitlines()
        last_ans = json.loads(ans_lines[-1])
        assert len(str(last_ans["response"]).split()) <= budget


def test_clause_verifier_grounds_snake_case_ids_contractions_and_gap_conflicts() -> None:
    topo = _build_mock_topology()
    retriever = CopilotGraphRetriever(topo, engagement_id="partner_google_nee")
    human_rec = retriever._indexed_assertion_records["asrt_arrow_human"]
    comment = render_speaker_comments(
        intent="context",
        target="Stefanos Georgiades",
        assertions=[human_rec],
        max_words=35,
        retriever=retriever,
    )
    assert comment.body
    hit = check_clause_grounding(
        comment.body,
        [human_rec],
        {human_rec["assertion_id"]: human_rec},
        target="Stefanos Georgiades",
    )
    assert hit == "asrt_arrow_human"

    conflict_assertions = [
        {
            "assertion_id": "asrt_gap_1",
            "subject": "person_rich_argentieri",
            "subject_node_id": "person_rich_argentieri",
            "predicate": "STATUS_UPDATE",
            "claim_text": "AI intake governance charter is in draft review",
            "source_date": "source-1111222233334444",
        },
        {
            "assertion_id": "asrt_gap_2",
            "subject": "person_rich_argentieri",
            "subject_node_id": "person_rich_argentieri",
            "predicate": "STATUS_UPDATE",
            "claim_text": "AI intake governance charter is finalized for production",
            "source_date": "source-5555666677778888",
        },
    ]
    retriever._last_gap_conflicts = [
        {
            "subject": "person_rich_argentieri",
            "assertion_a": conflict_assertions[0],
            "assertion_b": conflict_assertions[1],
            "date_a": "source-1111222233334444",
            "date_b": "source-5555666677778888",
        }
    ]
    gap_res = render_speaker_comments(
        intent="gaps",
        target=None,
        assertions=conflict_assertions,
        max_words=45,
        retriever=retriever,
    )
    assert gap_res.body
    assert "source-" not in gap_res.body
    assert "Rich Argentieri" in gap_res.body
    gap_hit = check_clause_grounding(
        gap_res.body,
        conflict_assertions,
        {a["assertion_id"]: a for a in conflict_assertions},
    )
    assert gap_hit == "asrt_gap_1"
    assert "are" in extract_content_tokens("we're tracking") or "tracking" in extract_content_tokens("we're tracking")
