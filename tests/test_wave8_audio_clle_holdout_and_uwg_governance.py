"""Tests for Wave 8: Closed-Loop Learning Engine, Acoustic Holdout Replay & UWG Governance."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.learning_adapters.correction_feedback_ingestor import (
    CorrectionFeedbackIngestor,
)
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.parameters import ParameterRegistry
from dual_pipeline.parameters_ops import (
    AUDIO_VOICE_PARAMETER_BOUNDS,
    validate_audio_voice_parameter_bounds,
)
from dual_pipeline.system_learning.holdout_replay_evaluator import (
    HoldoutReplayEvaluator,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import VoiceprintStore
from tools.lens_active_learning_hitl_adjudicator import ActiveLearningAdjudicator


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_audio_voice_parameter_bounds_validation(tmp_path: Path) -> None:
    """Verify AUDIO_VOICE_PARAMETER_BOUNDS enforces bounds and rejects out-of-range values."""
    param_key = "audio_ingestion.voiceprint_similarity_threshold"
    assert param_key in AUDIO_VOICE_PARAMETER_BOUNDS
    assert validate_audio_voice_parameter_bounds(param_key, 0.78) is True
    assert validate_audio_voice_parameter_bounds(param_key, 0.30) is False

    cfg_path = tmp_path / "parameters.json"
    cfg_path.write_text(
        json.dumps({"audio_ingestion": {"voiceprint_similarity_threshold": 0.82}}),
        encoding="utf-8",
    )
    reg = ParameterRegistry(config_path=cfg_path)
    reg.set(param_key, 0.78, persist=True, config_path=cfg_path)
    assert reg.get(param_key) == pytest.approx(0.78)

    with pytest.raises(ValueError, match="out of bounds"):
        reg.set(param_key, 0.99, persist=True, config_path=cfg_path)


def test_clle_audio_voice_cohort_holdout_uwg_and_rollback(tmp_path: Path) -> None:
    """Verify cohort detection (>=3 across >=2 sources), holdout replay, UWG apply, SQLite status, and rollback."""
    runtime_root = tmp_path / "runtime"
    proposals_dir = runtime_root / "proposals"
    runtime_root.mkdir(parents=True, exist_ok=True)

    params_path = runtime_root / "parameters.json"
    params_path.write_text(
        json.dumps(
            {
                "audio_ingestion": {
                    "voiceprint_similarity_threshold": 0.82,
                    "roster_priority_boost": 0.08,
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    priors_path = runtime_root / "learned_speaker_priors.json"
    priors_path.write_text(
        json.dumps({"version": "1.0.0", "speaker_priors": {"Alice Smith": 0.60}}, indent=2),
        encoding="utf-8",
    )

    engine = ClosedLoopLearningEngine(
        runtime_root=runtime_root,
        proposals_dir=proposals_dir,
    )

    # 1. Cohort below threshold (only 2 events / 1 source) -> no cohort
    sparse_events = [
        {"parameter_name": "audio_ingestion.voiceprint_similarity_threshold", "source_id": "mtg_01"},
        {"parameter_name": "audio_ingestion.voiceprint_similarity_threshold", "source_id": "mtg_01"},
    ]
    assert engine.analyze_audio_voice_cohorts(sparse_events) == []

    # 2. Cohort meeting >= 3 occurrences across >= 2 distinct sources
    cohort_events = [
        {"event_id": "ev1", "parameter_name": "audio_ingestion.voiceprint_similarity_threshold", "source_id": "mtg_01"},
        {"event_id": "ev2", "parameter_name": "audio_ingestion.voiceprint_similarity_threshold", "source_id": "mtg_02"},
        {"event_id": "ev3", "parameter_name": "audio_ingestion.voiceprint_similarity_threshold", "source_id": "mtg_02"},
    ]
    cohorts = engine.analyze_audio_voice_cohorts(cohort_events)
    assert len(cohorts) == 1
    cohort = cohorts[0]
    assert cohort["cohort_key"].startswith("audio_voice_recognition:")
    assert cohort["occurrence_count"] == 3
    assert cohort["distinct_source_count"] == 2

    holdout_cases = [
        {
            "source_id": "holdout_01",
            "cosine_similarity": 0.80,
            "baseline_threshold": 0.82,
            "is_true_speaker": True,
            "leaks_unverified_to_graph": False,
        },
        {
            "source_id": "holdout_02",
            "cosine_similarity": 0.88,
            "baseline_threshold": 0.82,
            "is_true_speaker": True,
            "leaks_unverified_to_graph": False,
        },
        {
            "source_id": "holdout_03",
            "cosine_similarity": 0.68,
            "baseline_threshold": 0.82,
            "is_true_speaker": False,
            "leaks_unverified_to_graph": False,
        },
    ]

    prop = engine.generate_proposal_for_cohort(
        cohort,
        proposal_type="threshold",
        target_override={
            "category": "audio_voice_recognition",
            "parameter_name": "audio_ingestion.voiceprint_similarity_threshold",
            "current_value": 0.82,
            "proposed_value": 0.78,
            "holdout_cases": holdout_cases,
            "priors_path": str(priors_path),
            "priors_updates": {"speaker_priors": {"Alice Smith": 0.85}},
        },
    )

    # Verify SQLite learning_proposals row created with PROPOSED status
    db_path = runtime_root / "ledger" / "learning.sqlite"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status FROM learning_proposals WHERE proposal_id = ?",
        (prop["proposal_id"],),
    ).fetchone()
    conn.close()
    assert row == ("PROPOSED",)

    # 3. Holdout replay simulation: beneficial proposal passes
    sim_res = engine.simulate_proposal(prop)
    assert sim_res.status == "SIMULATED_PASS"
    assert sim_res.success is True
    assert sim_res.details["regression_count"] == 0
    assert sim_res.details["false_enrollment_rate"] == 0.0
    assert sim_res.details["unverified_graph_leakage"] == 0
    assert sim_res.details["attribution_accuracy_delta"] > 0.0

    # Check that a regressive / false-enrollment proposal is vetoed
    regressive_cases = [
        {
            "source_id": "holdout_01",
            "cosine_similarity": 0.80,
            "baseline_threshold": 0.75,
            "is_true_speaker": True,
            "leaks_unverified_to_graph": False,
        },
        {
            "source_id": "holdout_02",
            "cosine_similarity": 0.86,
            "baseline_threshold": 0.75,
            "is_true_speaker": False,
            "leaks_unverified_to_graph": True,
        },
    ]
    evaluator = HoldoutReplayEvaluator()
    veto_res = evaluator.evaluate_audio_voice_proposal(
        {
            "proposal_id": "prop-regressive",
            "extensions": {"com.partner_advisory.nee.learning": {"proposed_value": 0.85}},
        },
        holdout_cases=regressive_cases,
    )
    assert veto_res["vetoed"] is True
    assert veto_res["verdict"] == "VETOED"
    assert veto_res["regression_count"] == 1
    assert veto_res["false_enrollment_rate"] > 0.0
    assert veto_res["unverified_graph_leakage"] == 1

    # 4. Apply approved proposal via UWG and verify SHA-256 and SQLite APPLIED status
    params_sha_before = _sha256_file(params_path)
    priors_sha_before = _sha256_file(priors_path)

    approval_receipt = create_signed_calibration_approval_receipt(
        prop,
        approved_by="operator_test",
        rationale="Verified improvement on holdout audio meetings.",
    )
    applied = engine.apply_approved_proposal(
        prop,
        approval_receipt,
        parameter_path=params_path,
        skip_decision_clearance=True,
    )
    assert applied["status"] == "APPLIED"

    params_sha_after = _sha256_file(params_path)
    priors_sha_after = _sha256_file(priors_path)
    assert params_sha_after != params_sha_before
    assert priors_sha_after != priors_sha_before

    updated_reg = ParameterRegistry(config_path=params_path)
    assert updated_reg.get("audio_ingestion.voiceprint_similarity_threshold") == pytest.approx(0.78)
    updated_priors = json.loads(priors_path.read_text(encoding="utf-8"))
    assert updated_priors["speaker_priors"]["Alice Smith"] == pytest.approx(0.85)

    conn = sqlite3.connect(str(db_path))
    row_applied = conn.execute(
        "SELECT status FROM learning_proposals WHERE proposal_id = ?",
        (prop["proposal_id"],),
    ).fetchone()
    conn.close()
    assert row_applied == ("APPLIED",)

    # 5. Rollback restores prior parameter & priors state and updates SQLite status
    rolled_back = engine.rollback_proposal(
        prop["proposal_id"],
        parameter_path=params_path,
    )
    assert rolled_back["status"] == "ROLLED_BACK"

    restored_reg = ParameterRegistry(config_path=params_path)
    assert restored_reg.get("audio_ingestion.voiceprint_similarity_threshold") == pytest.approx(0.82)
    restored_priors = json.loads(priors_path.read_text(encoding="utf-8"))
    assert restored_priors["speaker_priors"]["Alice Smith"] == pytest.approx(0.60)

    conn = sqlite3.connect(str(db_path))
    row_rb = conn.execute(
        "SELECT status FROM learning_proposals WHERE proposal_id = ?",
        (prop["proposal_id"],),
    ).fetchone()
    conn.close()
    assert row_rb == ("ROLLED_BACK",)


def test_active_learning_adjudicator_feeds_correction_ingestor_and_voiceprint_store(
    tmp_path: Path,
) -> None:
    """Verify ActiveLearningAdjudicator ranks uncertain clusters and feeds ingestor + VoiceprintStore."""
    inv_file = tmp_path / "inventory.json"
    anchors_file = tmp_path / "anchors.json"
    queue_file = tmp_path / "queue.json"
    log_file = tmp_path / "adjudication_log.json"

    inv_file.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "t1",
                        "original_acoustic_cluster": "Cluster_A",
                        "reference_speaker": "Amit Ayer",
                        "adjudication_status": "CONFIRMED_ANCHOR",
                        "utterance_text": "Hello team.",
                        "start_time_str": "00:01",
                    },
                    {
                        "turn_id": "t2",
                        "original_acoustic_cluster": "Cluster_B",
                        "reference_speaker": "Unknown",
                        "adjudication_status": "UNRESOLVED_QUEUE",
                        "utterance_text": "Let us review the cloud architecture milestones.",
                        "start_time_str": "00:10",
                    },
                    {
                        "turn_id": "t3",
                        "original_acoustic_cluster": "Cluster_B",
                        "reference_speaker": "Unknown",
                        "adjudication_status": "UNRESOLVED_QUEUE",
                        "utterance_text": "We also updated the deployment pipeline.",
                        "start_time_str": "00:25",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    anchors_file.write_text(json.dumps({"anchors": {}}), encoding="utf-8")
    queue_file.write_text(json.dumps({"total_unresolved": 2, "unresolved_turns": []}), encoding="utf-8")

    adjudicator = ActiveLearningAdjudicator(
        inventory_file=inv_file,
        anchors_file=anchors_file,
        queue_file=queue_file,
        log_file=log_file,
    )
    ranked = adjudicator.rank_uncertain_clusters_for_hitl()
    assert len(ranked) == 1
    assert ranked[0][0] == "Cluster_B"
    assert ranked[0][1]["unresolved_turns"] == 2

    ingestor = CorrectionFeedbackIngestor(runtime_root=tmp_path)
    vp_store = VoiceprintStore(db_path=tmp_path / "voiceprints.sqlite")
    vec_256 = [1.0] + [0.0] * 255

    record = adjudicator.apply_cluster_decision(
        cluster_name="Cluster_B",
        assigned_speaker="Jake Stark",
        operator_notes="Verified acoustic match",
        ingestor=ingestor,
        voiceprint_store=vp_store,
        cluster_embedding=vec_256,
    )
    assert record["turns_affected"] == 2
    assert record["assigned_speaker"] == "Jake Stark"
    matched = vp_store.match_voiceprint(vec_256, project="project_lens")
    assert matched is not None
    assert matched[0] == "Jake Stark"


def test_conversational_dynamics_priors_and_automated_audio_learning_cycle(tmp_path: Path) -> None:
    """Verify Markov turn-transition, co-attendance, cadence priors, and automated audio learning cycle."""
    from dual_pipeline.speaker_attribution_core.perspective_scorer import score_candidate_perspectives
    from dual_pipeline.speaker_attribution_core.voiceprint_auto_enrollment import auto_enroll_voiceprints
    from dual_pipeline.transcript_metadata_core.speaker_priors_registry import (
        load_audio_voice_learning_events,
        record_verified_session_dynamics,
    )

    priors_path = tmp_path / "learned_speaker_priors.json"
    ordered_turns = [
        ("Alice Smith", "Can you walk through the migration timeline?"),
        ("Bob Jones", "Yes, we completed phase one yesterday."),
        ("Alice Smith", "What about the database cutover?"),
        ("Bob Jones", "That is scheduled for Friday."),
    ]
    for _ in range(2):
        record_verified_session_dynamics(
            ["Alice Smith", "Bob Jones"],
            ordered_turns=ordered_turns,
            speaker_wpm={"Alice Smith": 140.0, "Bob Jones": 160.0},
            priors_path=priors_path,
        )

    score, pts = score_candidate_perspectives(
        metadata={
            "speaker_priors_path": str(priors_path),
            "attendees": ["Alice Smith", "Bob Jones"],
            "speaker_map": {"Speaker 1": "Alice Smith"},
        },
        candidate_name="Bob Jones",
        org="PartnerAdvisory",
        role="Architect",
        spk_id="Speaker 2",
        full_text="That is scheduled for Friday.",
        full_text_lower="that is scheduled for friday.",
        turns=["That is scheduled for Friday."],
        ordered_turns=[("Speaker 1", "What about the database cutover?"), ("Speaker 2", "That is scheduled for Friday.")],
        wpm=158.0,
    )
    assert score >= 0.20
    assert any("Markov turn-transition" in p for p in pts)
    assert any("co-attendance" in p for p in pts)
    assert any("speaking cadence" in p for p in pts)

    # Verify auto_enroll_voiceprints emits override learning events and run_audio_voice_learning_cycle ingests them
    rt_root = tmp_path / "runtime"
    vp_store = VoiceprintStore(db_path=tmp_path / "vp_auto.sqlite")
    vec_bob = [0.0, 1.0] + [0.0] * 254

    for idx, src_id in enumerate(("mtg_alpha", "mtg_beta", "mtg_beta")):
        meta = {
            "project": "NEE",
            "source_id": src_id,
            "audio_sha256": f"sha_session_{idx}_{src_id}",
            "runtime_root": str(rt_root),
            "speaker_priors_path": str(priors_path),
            "cluster_centroids": {"SPEAKER_00": vec_bob},
            "hitl_verified_speakers": {"SPEAKER_00", "Bob Jones"},
            "hitl_speaker_overrides": {"SPEAKER_00": "Wrong Candidate"},
        }
        enrolled = auto_enroll_voiceprints(meta, {"SPEAKER_00": "Bob Jones"}, voiceprint_store=vp_store)
        assert len(enrolled) == 1

    persisted_events = load_audio_voice_learning_events(rt_root)
    assert len(persisted_events) == 3

    engine = ClosedLoopLearningEngine(runtime_root=rt_root, proposals_dir=rt_root / "proposals")
    cycle_res = engine.run_audio_voice_learning_cycle(
        target_overrides={
            "audio_voice_recognition:audio_ingestion.voiceprint_similarity_threshold": {
                "category": "audio_voice_recognition",
                "parameter_name": "audio_ingestion.voiceprint_similarity_threshold",
                "current_value": 0.82,
                "proposed_value": 0.78,
                "holdout_cases": [
                    {"source_id": "h1", "cosine_similarity": 0.80, "baseline_threshold": 0.82, "is_true_speaker": True},
                    {"source_id": "h2", "cosine_similarity": 0.88, "baseline_threshold": 0.82, "is_true_speaker": True},
                    {"source_id": "h3", "cosine_similarity": 0.65, "baseline_threshold": 0.82, "is_true_speaker": False},
                ],
            }
        }
    )
    assert cycle_res["events_analyzed"] == 3
    assert len(cycle_res["cohorts"]) == 1
    assert len(cycle_res["proposals"]) == 1
    assert cycle_res["simulations"][0]["status"] == "SIMULATED_PASS"
    assert cycle_res["simulations"][0]["success"] is True
