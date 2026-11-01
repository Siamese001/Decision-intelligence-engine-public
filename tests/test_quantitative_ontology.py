"""Unit and integration tests for Wave 1: Typed Ontology & Computable Constraint Parameters."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.graph_writer_core.assertions import _build_assertion_qualifiers
from dual_pipeline.strategic_reasoning import (
    build_strategic_channels,
    classify_constraint_category,
    extract_quantitative_constraints,
    hydrate_quantitative_metadata,
    infer_object_literal_type,
    is_constraint_claim,
    strategic_profile_for_record,
)


def test_extract_quantitative_constraints_across_all_metric_types() -> None:
    text = (
        "Apex Global Enterprises enforces a 1:8 internal anchor ratio across 4 active workstreams, "
        "a 5-10% technical qualification bar pass rate, a 14-day IT deprovisioning notice window, "
        "a 42-hour weekly billing minimum at $185/hr ($450k/quarter), and a 45% higher onshore rate cap."
    )
    extracted = extract_quantitative_constraints(text)
    by_type = {item["metric_type"]: item for item in extracted}

    assert "headcount_ratio" in by_type
    assert by_type["headcount_ratio"]["numeric_value"] == 8.0
    assert by_type["headcount_ratio"]["unit"] == "engineers_per_anchor"

    assert "pass_rate_pct" in by_type
    assert by_type["pass_rate_pct"]["min_value"] == 5.0
    assert by_type["pass_rate_pct"]["max_value"] == 10.0
    assert by_type["pass_rate_pct"]["numeric_value"] == 7.5

    assert "rate_premium_pct" in by_type
    assert by_type["rate_premium_pct"]["numeric_value"] == 45.0

    assert "currency_rate" in by_type
    assert "weekly_billing_hours" in by_type
    assert by_type["weekly_billing_hours"]["numeric_value"] == 42.0

    assert "notice_window_days" in by_type
    assert by_type["notice_window_days"]["numeric_value"] == 14.0

    assert "active_workstreams" in by_type
    assert by_type["active_workstreams"]["numeric_value"] == 4.0


def test_infer_object_literal_type_and_constraint_categories() -> None:
    assert infer_object_literal_type("Maintain 1:8 internal anchor ratio") == "numeric_ratio"
    assert infer_object_literal_type("Qualification pass rate is 5-10% locally") == "percentage"
    assert infer_object_literal_type("Rate capped at $185/hr") == "currency_rate"
    assert infer_object_literal_type("Requires two weeks prior deprovisioning notice") == "duration_days"
    assert infer_object_literal_type("Deploy 10 pods in West Palm Beach") == "headcount"
    assert infer_object_literal_type("General qualitative context statement") == "text"

    assert classify_constraint_category("5-10% technical qualification bar pass rate") == "talent_bar"
    assert classify_constraint_category("1:8 internal anchor ratio and segregated pods") == "capacity_scaling"
    assert classify_constraint_category("42-hour weekly minimum and 45% higher rate card") == "commercial_pricing"
    assert classify_constraint_category("two weeks prior IT deprovisioning notice") == "schedule_notice"
    assert classify_constraint_category("3 days per week onsite dedicated seating") == "operational_continuity"


def test_assertion_qualifiers_and_hydration_preserve_quantitative_constraints() -> None:
    candidate = {
        "candidate_id": "cand-quant-001",
        "candidate_type": "assertion",
        "extraction_confidence": 0.92,
        "normalized_text": "NEE enforces a 1:8 ratio and 5-10% pass rate across 4 active workstreams.",
    }
    payload = {
        "candidate": candidate,
        "disposition": "APPROVED",
        "subject_node_id": "org_nee",
        "predicate": "HAS_CLAIM_TEXT",
        "object_node_id": None,
    }
    qualifiers = _build_assertion_qualifiers(candidate, payload)
    assert "quantitative_constraints" in qualifiers
    assert qualifiers["inferred_literal_type"] == "numeric_ratio"
    assert any(c["metric_type"] == "headcount_ratio" for c in qualifiers["quantitative_constraints"])

    # Verify hydration reads qualifiers_json and populates graph_path
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE assertion (assertion_id TEXT, object_literal TEXT, scope_text TEXT, qualifiers_json TEXT)"
    )
    conn.execute(
        "INSERT INTO assertion VALUES (?, ?, ?, ?)",
        (
            "asrt-001",
            candidate["normalized_text"],
            candidate["normalized_text"],
            json.dumps(qualifiers),
        ),
    )
    row = conn.execute("SELECT * FROM assertion WHERE assertion_id='asrt-001'").fetchone()
    path: dict[str, object] = {"assertion_id": "asrt-001"}
    quant = hydrate_quantitative_metadata(row, path)
    assert len(quant) >= 3
    assert path.get("constraint_category") in {"talent_bar", "capacity_scaling"}
    assert path.get("quantitative_constraints") == quant


def test_strategic_channels_preserve_quantitative_constraints_by_record() -> None:
    text = "Maintain 1:8 ratio and 5-10% qualification bar pass rate."
    assert is_constraint_claim(text)
    profile = strategic_profile_for_record(
        record_type="assertion",
        lane="approved",
        classification="internal",
        text=text,
    )
    assert profile["knowledge_role"] == "constraint"
    packet = {
        "approved": [
            {
                "record": {
                    "record_id": "rec-quant-001",
                    "record_type": "assertion",
                    "classification": "internal",
                    "retrieval_text": text,
                    "evidence_ids": ["ev-001"],
                },
                "retrieval_metadata": {"answer_use": "decision_constraint"},
            }
        ],
        "candidate": [],
        "external_public": [],
    }
    portfolio = build_strategic_channels(packet)
    assert "rec-quant-001" in portfolio["quantitative_constraints_by_record"]
    assert any(
        c["metric_type"] == "headcount_ratio"
        for c in portfolio["quantitative_constraints_by_record"]["rec-quant-001"]
    )


def test_extract_quantitative_constraints_ignores_bracketed_timestamps_and_zero_numerators() -> None:
    text = (
        "**Stefanos: [00:12:38]** Discussed timeline at [00:30:15] and 0:10 with a 1:8 internal anchor ratio."
    )
    extracted = extract_quantitative_constraints(text)
    ratios = [item for item in extracted if item["metric_type"] == "headcount_ratio"]
    assert len(ratios) == 1
    assert ratios[0]["numeric_value"] == 8.0

