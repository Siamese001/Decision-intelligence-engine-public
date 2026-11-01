"""
Wave 7 Acceptance Tests:
Dynamic ASR Lexical Priming & Output 01-03 / GraphDB Signal Contamination Firewall
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.learning_adapters.correction_feedback_ingestor import (
    CorrectionFeedbackIngestor,
)
from dual_pipeline.live_meeting_recorder_core.lexical_priming import LexicalPrimer
from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import (
    extract_phonetic_drift_candidates,
)
from dual_pipeline.speaker_memory import SpeakerMemory
from dual_pipeline.transcript_stages_core.graph_post_extractor import (
    quarantine_unverified_speaker_mutations,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
)
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator
from dual_pipeline.uwg_core.node_promotion import promote_candidate_endpoint_nodes


def test_wave7_lexical_primer_injects_learned_aliases_and_phonetics(tmp_path: Path) -> None:
    mem = SpeakerMemory(memory_path=tmp_path / "speaker_memory.json")
    mem.record_attribution(
        name="Stefan Mueller",
        utterance="We deployed the SCADA telemetry gateway.",
        topics=["SCADA Telemetry", "Substation Mesh"],
    )
    prof = mem.get_profile("Stefan Mueller")
    assert prof is not None
    prof.phonetic_aliases.append("Stephan Miller")

    primer = LexicalPrimer(
        project_name="apex_trans",
        learned_aliases={"scata": "SCADA", "garbaria": "Garbaria"},
        speaker_memory=mem,
    )
    prompt = primer.build_whisper_prompt(
        meeting_name="SCADA Sync",
        attendees=[{"name": "Stefan Mueller"}],
        max_chars=800,
    )
    assert len(prompt) <= 800
    assert "Stefan Mueller" in prompt
    assert "Garbaria" in prompt
    assert "Substation Mesh" in prompt

    # Verify post-processing replaces learned aliases
    healed = primer.post_process_transcript("We reviewed the garbaria integration.")
    assert "Garbaria" in healed

    # Verify phonetic drift extraction and ingestor recording
    drifts = extract_phonetic_drift_candidates(
        "We deployed the agrivoltaics pipeline",
        "We deployed the agrivoltaic pipeline",
    )
    assert ("agrivoltaic", "agrivoltaics") in drifts

    ingestor = CorrectionFeedbackIngestor(runtime_root=tmp_path)
    corr = ingestor.record_phonetic_drift_candidate(
        asr_token="agrivoltaic",
        canonical_token="agrivoltaics",
        operator_id="op-1",
        session_id="sess-1",
    )
    assert corr.category == "phonetic_alias"


def test_wave7_output03_and_role_reconciler_firewall_blocks_unverified_speakers() -> None:
    payload = {
        "evidence_index": {
            "ev-good": {
                "speaker": "Alice Smith",
                "text": "I will deliver the GIS schema.",
                "attribution_provenance_tier": "ENROLLED_VOICEPRINT",
            },
            "ev-bad": {
                "speaker": "SPEAKER_00",
                "text": "I decided to retire the legacy cluster.",
                "attribution_provenance_tier": "UNDIARIZED_QUARANTINED",
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:alice-smith", "type": "person", "name": "Alice Smith"},
                {"id": "person:speaker-00", "type": "person", "name": "SPEAKER_00"},
            ],
            "decision_updates": [
                {
                    "decision_id": "dec:bad-1",
                    "description": "Retire legacy cluster",
                    "evidence_ids": ["ev-bad"],
                },
                {
                    "decision_id": "dec:good-1",
                    "description": "Approve GIS schema",
                    "evidence_ids": ["ev-good"],
                },
            ],
            "action_updates": [
                {
                    "action_id": "act:bad-1",
                    "owner_id": "person:speaker-00",
                    "description": "Retire cluster",
                    "evidence_ids": ["ev-bad"],
                }
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-bad-role",
                    "from_id": "person:speaker-00",
                    "to_id": "role:architect",
                    "relationship": "HAS_ROLE",
                    "evidence_ids": ["ev-bad"],
                },
                {
                    "signal_id": "sig-good-role",
                    "from_id": "person:alice-smith",
                    "to_id": "role:lead",
                    "relationship": "HAS_ROLE",
                    "evidence_ids": ["ev-good"],
                },
            ],
        },
    }

    quarantine_unverified_speaker_mutations(
        payload,
        metadata={"unverified_speakers": ["SPEAKER_00"], "enforce_speaker_firewall": True},
    )
    gm = payload["graph_mutations"]
    assert [d["decision_id"] for d in gm["decision_updates"]] == ["dec:good-1"]
    assert gm["action_updates"] == []
    assert [s["signal_id"] for s in gm["relationship_signals"]] == ["sig-good-role"]
    assert len(payload["_quarantined_unverified_speaker_mutations"]) == 3

    # Verify speaker_role_reconciler skips unverified/generic speakers
    rec_data = {"graph_mutations": {"entity_upserts": [], "relationship_signals": []}, "evidence_index": {}}
    reconcile_speaker_role_resolutions(
        rec_data,
        metadata={
            "auto_role_resolutions": {
                "SPEAKER_00": "Principal Architect",
                "Session Context": "Director",
            },
            "unverified_speakers": ["SPEAKER_00"],
        },
    )
    assert rec_data["graph_mutations"]["entity_upserts"] == []


def test_wave7_xgates_and_uwg_node_promotion_block_unverified_speaker_lineage(tmp_path: Path) -> None:
    evaluator = XExitGatesEvaluator()
    bad_data = {
        "evidence_index": {
            "ev-1": {
                "speaker": "Speaker 1",
                "text": "We will migrate the database.",
                "attribution_provenance_tier": "UNDIARIZED_QUARANTINED",
            }
        },
        "graph_mutations": {
            "decision_updates": [
                {
                    "decision_id": "dec:1",
                    "description": "Migrate database",
                    "evidence_ids": ["ev-1"],
                }
            ],
            "action_updates": [],
            "relationship_signals": [],
        },
    }
    with pytest.raises(ValueError, match="X3 FAIL"):
        evaluator._validate_speaker_attribution_lineage(
            bad_data,
            metadata={},
            transcript="**UNRESOLVED — Speaker 1**: We will migrate the database.",
            recap="",
        )

    # Verify UWG node_promotion blocks candidate person: nodes with unverified speaker provenance
    db_path = tmp_path / "test_uwg.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT, description TEXT, status TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE node_state (node_id TEXT PRIMARY KEY, validation_state TEXT, approval_state TEXT, retrieval_eligibility TEXT, legacy_status_text TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO node VALUES ('person:speaker_00', 'person', 'Speaker 00', 'UNDIARIZED_QUARANTINED speaker', 'candidate', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO node VALUES ('person:alice_smith', 'person', 'Alice Smith', 'Verified attendee', 'candidate', '2026-01-01')"
    )
    conn.commit()

    def _table_exists(c: sqlite3.Connection, name: str) -> bool:
        return name in ("node", "node_state")

    promote_candidate_endpoint_nodes(
        conn,
        before={
            "subject_node_id": "person:speaker_00",
            "object_node_id": "person:alice_smith",
            "attribution_provenance_tier": "VERIFIED",
        },
        validation_state="validated",
        now="2026-10-31T00:00:00Z",
        table_exists_fn=_table_exists,
    )
    conn.commit()

    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    status_bad = ro_conn.execute("SELECT status FROM node WHERE node_id='person:speaker_00'").fetchone()[0]
    status_good = ro_conn.execute("SELECT status FROM node WHERE node_id='person:alice_smith'").fetchone()[0]
    ro_conn.close()
    conn.close()

    assert status_bad == "candidate"
    assert status_good == "canonical"
