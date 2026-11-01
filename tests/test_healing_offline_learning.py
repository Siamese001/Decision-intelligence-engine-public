"""Wave 2 verification suite for Offline System Learning Engine & CLI Integration for Healing Decisions."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.storage.healing_decision_store import HealingDecisionStore
from tools.snapshot_agent_learning_registry_core.primary_components import PRIMARY_COMPONENTS


def test_offline_healing_learning_cycle_and_jsonl_drain(tmp_path: Path, monkeypatch) -> None:
    runtime_root = tmp_path / ".local"
    ledger_dir = runtime_root / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    db_path = ledger_dir / "learning.sqlite"
    jsonl_path = ledger_dir / "live_outcome_events.jsonl"
    proposals_dir = tmp_path / "proposals"
    monkeypatch.setenv("NEE_TEST_LEARNING_SQLITE", str(db_path))

    store = HealingDecisionStore(db_path=db_path)
    # 1. Seed 3 recurring successful deterministic repairs (should form a healing_repair cohort)
    for idx in range(3):
        store.record_decision({
            "decision_id": f"heal-rep-{idx}",
            "run_id": f"run-rep-{idx}",
            "subsystem": "deterministic_healing_mesh",
            "component": "DeterministicHealer:output_1",
            "scope": "output_1",
            "repair_class": "NORMALIZE_DATE_FORMAT",
            "action_taken": "Normalized ISO date 2026-10-15 -> 10/15/2026",
            "outcome_status": "HEALED",
            "before_digest": f"b-{idx}",
            "after_digest": f"a-{idx}",
        })

    # 2. Seed 2 unrepairable / HITL escalations via JSONL live_outcome_events.jsonl to test JSONL drain
    jsonl_lines = [
        json.dumps({
            "event_id": "evt-live-exh-1",
            "run_id": "run-exh-1",
            "outcome_type": "RETRY_EXHAUSTION",
            "source_agent": "CanonicalProjectionHealerAgent",
            "target_component": "CanonicalProjectionHealerAgent",
            "category": "output_3",
            "retry_class": "canonical_projection_repair",
            "defect_message": "Orphan node budget exceeded",
            "was_successful": False,
        }),
        json.dumps({
            "event_id": "evt-live-exh-2",
            "run_id": "run-exh-2",
            "outcome_type": "RETRY_EXHAUSTION",
            "source_agent": "CanonicalProjectionHealerAgent",
            "target_component": "CanonicalProjectionHealerAgent",
            "category": "output_3",
            "retry_class": "canonical_projection_repair",
            "defect_message": "Orphan node budget exceeded",
            "was_successful": False,
        }),
    ]
    jsonl_path.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

    engine = ClosedLoopLearningEngine(runtime_root=runtime_root, proposals_dir=proposals_dir)
    cycle = engine.run_healing_learning_cycle(
        db_path=db_path,
        outcome_ledger_path=jsonl_path,
        min_occurrences=2,
        generate_proposals=True,
    )

    assert cycle["signals_count"] == 5
    cohort_keys = {c["cohort_key"] for c in cycle["cohorts"]}
    assert "healing_repair:deterministic_healing_mesh:NORMALIZE_DATE_FORMAT:output_1" in cohort_keys
    assert "healing_escalation:CanonicalProjectionHealerAgent:CANONICAL_PROJECTION_REPAIR:output_3" in cohort_keys

    assert len(cycle["proposals"]) == 2
    for prop in cycle["proposals"]:
        validate_artifact(prop)
        assert prop["auto_apply"] is False
        assert prop["approval_required"] is True

    assert len(cycle["simulations"]) == 2
    for sim in cycle["simulations"]:
        assert sim["success"] is True
        assert sim["status"] == "SIMULATED_PASS"
        assert sim["details"]["zero_mutation_guaranteed"] is True
        assert sim["details"]["brier_score_delta"] < 0.0

    posteriors_file = runtime_root / "learned_healing_posteriors.json"
    assert posteriors_file.is_file()
    post_data = json.loads(posteriors_file.read_text(encoding="utf-8"))
    assert post_data["total_decisions"] == 5


def test_govern_learning_proposals_healing_scan_cli_and_registry(tmp_path: Path) -> None:
    db_path = tmp_path / "learning.sqlite"
    proposals_dir = tmp_path / "proposals"
    store = HealingDecisionStore(db_path=db_path)
    for i in range(2):
        store.record_decision({
            "decision_id": f"cli-heal-{i}",
            "run_id": f"run-cli-{i}",
            "subsystem": "l2_healer_service",
            "component": "UnifiedL2HealerService:PIPELINE_D_BRIEFING",
            "scope": "PIPELINE_D_BRIEFING",
            "repair_class": "SURGICAL_L2_REPAIR",
            "action_taken": "Converted passive action verbs",
            "outcome_status": "HEALED",
            "before_digest": f"cb-{i}",
            "after_digest": f"ca-{i}",
        })

    proc = subprocess.run(
        [
            sys.executable,
            "tools/govern_learning_proposals.py",
            "healing-scan",
            "--db-path",
            str(db_path),
            "--proposals-dir",
            str(proposals_dir),
            "--min-occurrences",
            "2",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "[HEALING-SCAN]" in proc.stdout
    assert "Proposals=1" in proc.stdout

    healer_names = {
        "ForensicTranscriptHealerAgent",
        "CanonicalProjectionHealerAgent",
        "RetrievalPerspectiveHealerAgent",
        "BriefingAndDecisionHealerAgent",
    }
    found = [c for c in PRIMARY_COMPONENTS if c["name"] in healer_names]
    assert len(found) == 4
    for comp in found:
        assert comp["has_durable_learning"] is True
        assert comp["target_entry_point"] is not None
        assert "HealingDecisionStore" in comp["learning_mechanism"]
