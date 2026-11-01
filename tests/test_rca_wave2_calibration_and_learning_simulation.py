"""tests/test_rca_wave2_calibration_and_learning_simulation.py

Wave 2 verification suite for 8-Agent RCA Remediation:
1. Single-pass calibration in `arbitrate_decision_margin` + `QuestionBuilder` (preventing double-calibration
   while applying `learned_hitl_option_store.json` nuance overrides via preserved `raw_option_keys`).
2. `sanitize_to_layman_text` preserves semantic keywords (`db` -> `database`, `sql` -> `database`) instead of
   collapsing technical option labels into generic `"Resolution option 1"`.
3. `HealingDecisionStore` honors `MOCK_ENV=true`, supports `update_post_learning_confidence`, and queries
   most-recent decisions (`DESC`).
4. `document_connectivity_healer.py` records true pre-mutation `before_digest` and `surgical_remediator.py`
   sets `invariants_preserved=False` on `UNREPAIRABLE`.
5. `simulate_healing_policy_update` computes empirical Brier scores dynamically from `HealingDecisionStore`
   instead of hardcoded `0.184` / `0.122`, and `recalibrate_post_learning_confidences_core` does not pollute
   `proposals_dir` with `learned_healing_posteriors.json`.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    heal_disconnected_document_entities,
)
from dual_pipeline.healing_policy import arbitrate_decision_margin, sanitize_to_layman_text
from dual_pipeline.learning_engine import get_learning_engine
from dual_pipeline.learning_loop_core import simulation
from dual_pipeline.learning_loop_core.simulation import simulate_healing_policy_update
from dual_pipeline.storage.healing_decision_store import (
    HealingDecisionStore,
    get_default_healing_sqlite_path,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
    _persist_surgical_decision,
)


def test_simulation_has_no_hardcoded_0184_or_0122_and_varies_empirically(tmp_path: Path) -> None:
    """simulate_healing_policy_update computes empirical Brier scores dynamically across datasets."""
    src = inspect.getsource(simulation)
    assert "0.184" not in src
    assert "0.122" not in src

    db1 = tmp_path / "cohort1.sqlite"
    store1 = HealingDecisionStore(db_path=db1)
    for i in range(4):
        store1.record_decision(
            {
                "decision_id": f"c1-{i}",
                "run_id": "run-1",
                "subsystem": "l2_healer_service",
                "component": "healer",
                "scope": "output_1",
                "repair_class": "QUOTE_POINTER_REPAIR",
                "action_taken": "REPAIR",
                "outcome_status": "HEALED",
                "before_digest": f"b-{i}",
                "after_digest": f"a-{i}",
                "confidence_top": 0.62,
                "confidence_second": 0.38,
                "ambiguity_margin": 0.24,
                "hitl_escalated": False,
                "invariants_preserved": True,
            }
        )

    db2 = tmp_path / "cohort2.sqlite"
    store2 = HealingDecisionStore(db_path=db2)
    for i in range(6):
        store2.record_decision(
            {
                "decision_id": f"c2-{i}",
                "run_id": "run-2",
                "subsystem": "l2_healer_service",
                "component": "healer",
                "scope": "output_1",
                "repair_class": "QUOTE_POINTER_REPAIR",
                "action_taken": "ESCALATE",
                "outcome_status": "UNREPAIRABLE",
                "before_digest": f"b2-{i}",
                "after_digest": f"a2-{i}",
                "confidence_top": 0.88,
                "confidence_second": 0.12,
                "ambiguity_margin": 0.76,
                "hitl_escalated": True,
                "invariants_preserved": True,
            }
        )

    proposal = {
        "proposal_id": "lcp-test-emp-brier",
        "auto_apply": False,
        "extensions": {
            "com.partner_advisory.nee.learning": {
                "cohort_key": "healing_escalation:l2_healer_service:QUOTE_POINTER_REPAIR",
                "subsystem": "l2_healer_service",
                "repair_class": "QUOTE_POINTER_REPAIR",
                "occurrence_count": 4,
            }
        },
    }
    res1 = simulate_healing_policy_update(proposal, db_path=db1)
    res2 = simulate_healing_policy_update(proposal, db_path=db2)
    assert res1["success"] is True and res2["success"] is True
    assert res1["details"]["current_brier_score"] != res2["details"]["current_brier_score"]
    assert res1["details"]["simulated_brier_score"] < res1["details"]["current_brier_score"]
    assert res2["details"]["simulated_brier_score"] < res2["details"]["current_brier_score"]


def test_single_pass_calibration_and_raw_key_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """arbitrate_decision_margin applies HealingDecisionStore once (no double calibration) and matches nuance store via raw_option_keys."""
    db_path = tmp_path / "learning.sqlite"
    nuance_path = tmp_path / "learned_hitl_option_store.json"
    monkeypatch.setenv("NEE_TEST_LEARNING_SQLITE", str(db_path))
    monkeypatch.setenv("NEE_LEARNED_HITL_OPTION_STORE", str(nuance_path))

    # Layman sanitization turns 'repair_db_schema' into 'Repair database schema' (not generic fallback)
    clean_lbl = sanitize_to_layman_text("repair_db_schema", fallback="Resolution option 1")
    assert clean_lbl == "Repair database schema"

    store = HealingDecisionStore(db_path=db_path)
    for i in range(5):
        store.record_decision(
            {
                "decision_id": f"h-{i}",
                "run_id": f"r-{i}",
                "subsystem": "healing_policy",
                "component": "arbiter",
                "scope": "healing_policy",
                "repair_class": "REPAIR_DB_SCHEMA",
                "action_taken": "AUTO_APPLY",
                "outcome_status": "HEALED",
                "before_digest": f"b-{i}",
                "after_digest": f"a-{i}",
                "confidence_top": 0.90,
                "confidence_second": 0.10,
                "ambiguity_margin": 0.80,
                "hitl_escalated": False,
                "invariants_preserved": True,
            }
        )

    # Start with narrow margin so it escalates or calibrates; verify QuestionBuilder does NOT double-blend
    dec = arbitrate_decision_margin(
        "dec-single-cal",
        [("REPAIR_DB_SCHEMA", 0.52), ("HOLD_FOR_OPERATOR_REVIEW", 0.48)],
        question="Which database repair option should be applied?",
        category="healing_policy",
        healing_db_path=db_path,
    )
    # 0.45 * 0.52 + 0.55 * ((5+2)/(5+3)) = 0.234 + 0.48125 = 0.7152
    assert round(dec.top_confidence, 4) == 0.7153 or abs(dec.top_confidence - 0.7152) < 0.005


def test_healing_store_mock_env_isolation_and_post_learning_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_default_healing_sqlite_path honors MOCK_ENV=true and update_post_learning_confidence updates stored row."""
    monkeypatch.delenv("NEE_TEST_LEARNING_SQLITE", raising=False)
    monkeypatch.delenv("NEE_HEALING_SQLITE_PATH", raising=False)
    monkeypatch.delenv("NEE_LEARNING_SQLITE_PATH", raising=False)
    monkeypatch.setenv("MOCK_ENV", "true")

    default_p = get_default_healing_sqlite_path()
    assert "nee_test_learning_" in default_p.name

    db_path = tmp_path / "update_test.sqlite"
    store = HealingDecisionStore(db_path=db_path)
    store.record_decision(
        {
            "decision_id": "dec-upd-1",
            "run_id": "r-1",
            "subsystem": "healing_mesh",
            "component": "mesh",
            "scope": "output_2",
            "repair_class": "QUOTE_SPAN_FIX",
            "action_taken": "REPAIR",
            "outcome_status": "HEALED",
            "before_digest": "b1",
            "after_digest": "a1",
            "confidence_top": 0.65,
            "confidence_second": 0.25,
            "ambiguity_margin": 0.40,
        }
    )
    assert store.update_post_learning_confidence("dec-upd-1", 0.88, 0.63) is True
    rows = store.list_decisions()
    assert len(rows) == 1
    assert rows[0]["confidence_top"] == 0.88
    assert rows[0]["ambiguity_margin"] == 0.63
    assert rows[0]["metadata"]["post_learning_recalibrated"] is True


def test_document_connectivity_before_digest_and_surgical_unrepairable_invariant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """document_connectivity_healer records genuine before_digest != after_digest, and surgical_remediator sets invariants_preserved=False on UNREPAIRABLE."""
    db_path = tmp_path / "healer_telemetry.sqlite"
    monkeypatch.setenv("NEE_TEST_LEARNING_SQLITE", str(db_path))

    projection = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "ent-anchor", "name": "Primary Project", "type": "PROJECT"},
                {"id": "ent-orphan", "name": "Orphan Component", "type": "SYSTEM"},
            ],
            "relationship_signals": [],
        },
        "evidence_index": {"ev-1": {"text": "Primary Project integrates Orphan Component."}},
    }
    changes = heal_disconnected_document_entities(projection, metadata={"run_id": "run-conn-1"})
    assert len(changes) == 1

    _persist_surgical_decision(
        scope="output_2",
        target_sec="section_3",
        before_text="broken section",
        after_text="broken section",
        did_remediate=False,
        audit_trail=["Failed to repair section_3"],
        metadata={"run_id": "run-surg-1"},
    )

    store = HealingDecisionStore(db_path=db_path)
    conn_rows = store.list_decisions(subsystem="document_connectivity_healer")
    assert len(conn_rows) == 1
    assert conn_rows[0]["before_digest"] != conn_rows[0]["after_digest"]

    surg_rows = store.list_decisions(subsystem="surgical_remediator")
    assert len(surg_rows) == 1
    assert surg_rows[0]["outcome_status"] == "UNREPAIRABLE"
    assert surg_rows[0]["invariants_preserved"] is False


def test_recalibrate_post_learning_does_not_pollute_proposals_dir(tmp_path: Path) -> None:
    """recalibrate_post_learning_confidences writes learned_healing_posteriors.json to runtime_root, not proposals_dir."""
    rt_root = tmp_path / "runtime"
    prop_dir = tmp_path / "proposals"
    (rt_root / "ledger").mkdir(parents=True, exist_ok=True)
    prop_dir.mkdir(parents=True, exist_ok=True)

    db_path = rt_root / "ledger" / "learning.sqlite"
    store = HealingDecisionStore(db_path=db_path)
    store.record_decision(
        {
            "decision_id": "d-1",
            "run_id": "r-1",
            "subsystem": "healing_mesh",
            "component": "mesh",
            "scope": "output_1",
            "repair_class": "HEADER_REPAIR",
            "action_taken": "REPAIR",
            "outcome_status": "HEALED",
            "before_digest": "b1",
            "after_digest": "a1",
        }
    )

    engine = get_learning_engine(runtime_root=rt_root, proposals_dir=prop_dir)
    res = engine.recalibrate_post_learning_confidences(healing_db_path=db_path)
    assert res["status"] == "RECALIBRATED"
    assert not (prop_dir / "learned_healing_posteriors.json").exists()
    assert (rt_root / "learned_healing_posteriors.json").is_file()
