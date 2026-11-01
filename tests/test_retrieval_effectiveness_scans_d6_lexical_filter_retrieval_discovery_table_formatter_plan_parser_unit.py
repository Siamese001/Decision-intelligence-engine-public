"""Unit tests for retrieval_effectiveness_scans, d6_lexical_filter, retrieval_discovery, table_formatter, and plan_parser."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.ambiguity_core.d6_lexical_filter import (
    is_suppressed_or_common_word,
    is_valid_name_syntax,
    should_surface_unconfirmed_candidate,
)
from dual_pipeline.graph_delta_core.table_formatter import (
    format_markdown_mutation_table,
    format_mutation_summary_banner,
)
from dual_pipeline.graph_scout_core.retrieval_effectiveness_scans import (
    _is_stale_description,
    scan_october_retrieval_effectiveness_gaps,
    scan_stale_role_and_unresolved_descriptions,
)
from dual_pipeline.hitl_governance_core.decision_clearance_core.plan_parser import (
    parse_confidence_pairs,
    parse_decisions_from_markdown,
    parse_decisions_from_table,
    parse_no_decision_attestations,
    validate_decision_collection,
)
from dual_pipeline.runtime_evidence_core.retrieval_discovery import (
    _artifact_digest,
    collect_retrieval_evaluations,
    collect_retrieval_suites,
)


def test_d6_lexical_filter_suppression_syntax_and_surfacing() -> None:
    assert is_suppressed_or_common_word("Monday") is True
    assert is_suppressed_or_common_word("PartnerAdvisory") is True
    assert is_suppressed_or_common_word("running") is True
    assert is_suppressed_or_common_word("Kristine") is False

    assert is_valid_name_syntax("Kristine", "spoke with", "I spoke with Kristine yesterday.", is_in_directory=True) is True
    assert is_valid_name_syntax("Monday", "on", "See you on Monday", is_in_directory=False) is False

    assert should_surface_unconfirmed_candidate("Kristine", turn_frequency=3, has_relational_verb=True) is True
    assert should_surface_unconfirmed_candidate("Monday", turn_frequency=5, has_relational_verb=True) is False


def test_retrieval_effectiveness_scans_on_sqlite() -> None:
    assert _is_stale_description("") is True
    assert _is_stale_description("Role remains unresolved in current source") is True
    assert _is_stale_description("Principal Cloud Architect leading platform") is False

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE node (
            node_id TEXT,
            canonical_name TEXT,
            description TEXT,
            active INTEGER
        );
        CREATE TABLE assertion (
            assertion_id TEXT,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            currentness_state TEXT,
            canonical_status TEXT,
            retrieval_eligible INTEGER,
            superseded_by_assertion_id TEXT
        );
        INSERT INTO node VALUES ('person:jane_doe', 'Jane Doe', 'role remains unresolved', 1);
        INSERT INTO node VALUES ('person:john_smith', 'John Smith', 'Lead Architect', 1);
        INSERT INTO assertion VALUES (
            'a1', 'person:jane_doe', 'HAS_ROLE', NULL, 'Director', 'Director of Engineering',
            'current', 'canonical', 1, NULL
        );
        INSERT INTO assertion VALUES (
            'a2', 'person:jane_doe', 'NOTES', NULL, 'unknown role', 'Collaborated with John Smith on unknown role',
            'current', 'canonical', 1, NULL
        );
        """
    )
    stale = scan_stale_role_and_unresolved_descriptions(cur)
    assert len(stale) == 1
    assert stale[0].finding_type == "stale_role_or_bio_caveat"

    all_findings = scan_october_retrieval_effectiveness_gaps(cur)
    assert len(all_findings) >= 1


def test_retrieval_discovery_evaluations_and_suites(tmp_path: Path) -> None:
    rt_root = tmp_path / "rt"
    eval_dir = rt_root / "retrieval_evaluations"
    suite_dir = rt_root / "retrieval_evaluation_suites"
    eval_dir.mkdir(parents=True)
    suite_dir.mkdir(parents=True)

    pair = {
        "embedding_backend": "bge-m3",
        "generation_id": "gen-1",
        "graph_snapshot": "snap-1",
        "graph_sha256": "a" * 64,
        "sidecar_sha256": "b" * 64,
        "model_id": "BAAI/bge-m3",
        "model_revision": "rev-1",
        "production_qualified": True,
    }
    eval_payload = {
        "contract_id": "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0",
        "evaluation_id": "eval-1",
        "execution_tier": "E3",
        "status": "PASS",
        "evaluated_at": "2026-10-31T12:00:00Z",
        "prepared_receipt_id": "prep-1",
        "prepared_receipt_digest": "c" * 64,
        "suite_id": "suite-1",
        "suite_digest": "d" * 64,
        "acceptance_policy": {"gate": "strict"},
        "coverage": {"total": 10},
        "aggregate": {"ndcg": 0.95},
        "gates": {"all_passed": True},
        "baseline_pair": pair,
        "candidate_pair": pair,
    }
    eval_payload["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": _artifact_digest(eval_payload),
    }
    (eval_dir / "retrieval-release-evaluation-1.json").write_text(json.dumps(eval_payload), encoding="utf-8")

    issues: list[dict[str, str]] = []
    evals = collect_retrieval_evaluations([rt_root], [], issues)
    assert len(evals) == 1
    assert evals[0]["evaluation_id"] == "eval-1"
    assert issues == []

    (suite_dir / "retrieval-release-suite-bad.json").write_text("{bad", encoding="utf-8")
    suites = collect_retrieval_suites([rt_root], [], issues)
    assert suites == []
    assert len(issues) == 1


def test_table_formatter_banner_and_markdown_table() -> None:
    noop_summary = {
        "status": "IDEMPOTENT_NOOP",
        "generation_before": "gen-1",
        "generation_after": "gen-1",
        "graph_sha256_before": "a" * 64,
        "graph_sha256_after": "a" * 64,
        "sidecar_sha256_before": "b" * 64,
        "sidecar_sha256_after": "b" * 64,
        "total_operations": 0,
    }
    banner = format_mutation_summary_banner(noop_summary)
    assert "IDEMPOTENT_NOOP" in banner
    assert "None (Graph and Sidecar remain identical)" in banner

    md_noop = format_markdown_mutation_table(noop_summary)
    assert "Idempotent no-op" in md_noop

    mut_summary = {
        "status": "MUTATED",
        "generation_before": "gen-1",
        "generation_after": "gen-2",
        "graph_sha256_before": "a" * 64,
        "graph_sha256_after": "c" * 64,
        "sidecar_sha256_before": "b" * 64,
        "sidecar_sha256_after": "d" * 64,
        "total_operations": 3,
        "net_changes": {"entities": 2, "canonical_assertions": 1},
        "entities_created": [{"id": "n1", "name": "Node 1", "type": "person"}],
        "sources_added": [{"source_id": "s1", "name": "Source 1"}],
        "actual_graph_delta": {
            "table": [{"component_key": "entities", "graph_component": "Entities", "baseline": 10, "actual_after": 12, "actual_delta": 2}]
        },
    }
    mut_banner = format_mutation_summary_banner(mut_summary)
    assert "Entities Created:" in mut_banner
    mut_md = format_markdown_mutation_table(
        mut_summary,
        assertions_inserted=[{"assertion_id": "a1", "subject": "n1", "predicate": "REL", "object": "n2"}],
    )
    assert "Granular Graph DB Delta" in mut_md


def test_plan_parser_tables_markdown_and_attestations(tmp_path: Path) -> None:
    assert parse_confidence_pairs("Option A (85%) vs Option B (60%)") == (0.85, 0.60)
    assert parse_confidence_pairs("Single (90%)") == (0.90, 0.0)
    assert parse_confidence_pairs("No percentages") == (0.50, 0.50)

    md = """
| ID | Decision | Options (confidence) | Δ | Handling |
| :--- | :--- | :--- | :--- | :--- |
| DEC-01 | Wave 1 Storage Mode | SQLite WAL (90%) vs Memory (50%) | 40% | AUTONOMOUS |
"""
    table_decs = parse_decisions_from_table(md, source_file="plan.md")
    assert len(table_decs) == 1
    assert table_decs[0].decision_id == "DEC-01"
    assert table_decs[0].wave == 1

    json_md = """
```json:decisions
[
  {"decision_id": "DEC-02", "wave": 2, "status": "OPEN", "question": "Proceed?", "options": [{"label": "Yes"}]}
]
```
"""
    decs, errs = parse_decisions_from_markdown(json_md, tmp_path / "plan.md")
    assert errs == []
    assert len(decs) == 1
    assert validate_decision_collection(decs) == (True, [])

    att_md = "NO_HITL_DECISIONS_REQUIRED: Wave 1 is purely additive unit tests."
    atts = parse_no_decision_attestations(att_md, tmp_path / "plan.md")
    assert isinstance(atts, list)
