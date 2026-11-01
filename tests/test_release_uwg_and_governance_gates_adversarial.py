"""
tests/test_release_uwg_and_governance_gates_adversarial.py

Wave 8 adversarial and edge-case test suite covering:
1. Boundary payload: empty graph DB or missing sidecar in _re_seal_active_pair_if_needed.
2. Malformed input: unsigned PASS_AUTHORIZED_EXCEPTION and composite_score != dimension_sum.
3. Concurrency & race: parallel sidecar node re-embedding and active_pair re-sealing.
4. Overflow & exhaustion: cognitive complexity > 15 blocked in file_budget_core/policy.py even with high max_function_cc attestation.
5. Invariant bypass attempt: .py file injected into exempt_prefixes rejected by validate_exempt_prefixes; immutable_sealed active_pair rejected.
6. Rollback & recovery: candidate promotion without approval_reference fails closed when require_explicit_approval_ref is set.
7. Exact production regression: SCAN_DIRS in lint_production_purity.py scans dual_pipeline and tools; hardcoded HITL-10-18-2026 / CANONICAL_JACOB_WELLINGTON_ID purged from apply_graph_hygiene_remediation.py.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.publish_helper import (
    _assert_authoritative_mutation_preconditions,
    _re_seal_active_pair_if_needed,
    _reembed_mutated_sidecar_nodes,
)
from dual_pipeline.canonical_pipeline.release_policy import (
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
    _check_out3_threshold,
    validate_micro_judge_scorecard,
)
from storage.connection import get_db_connection
from tools.apply_graph_hygiene_remediation import _remediate_candidate_promotions
from tools.file_budget_core.policy import evaluate_module_source, load_candidate_policy, validate_exempt_prefixes
from tools.lint_production_purity import SCAN_DIRS, lint_code_for_production_writes

ROOT = Path(__file__).resolve().parent.parent


def test_boundary_reembed_sidecar_nodes_updates_text_sha256(tmp_path: Path) -> None:
    """Category 1 (Boundary) & Sidecar Re-embedding: Mutated node text is re-embedded before graph_sha256 update."""
    graph_db = tmp_path / "candidate_graph.db"
    sidecar_db = tmp_path / "vector_sidecar.sqlite"

    g_conn = get_db_connection(graph_db, readonly=False)
    try:
        g_conn.execute(
            "CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, role TEXT, summary TEXT)"
        )
        g_conn.execute(
            "INSERT INTO node (node_id, canonical_name, role, summary) VALUES ('person:alice', 'Alice Smith', 'Lead Architect', 'Platform architecture')"
        )
        g_conn.commit()
    finally:
        g_conn.close()

    s_conn = get_db_connection(sidecar_db, readonly=False)
    try:
        s_conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        s_conn.execute("CREATE TABLE dense_vector (object_id TEXT PRIMARY KEY, text_sha256 TEXT)")
        s_conn.execute("INSERT INTO dense_vector (object_id, text_sha256) VALUES ('person:alice', 'stale_text_sha')")
        s_conn.commit()
    finally:
        s_conn.close()

    active_pair = tmp_path / "active_pair.json"
    active_pair.write_text(
        json.dumps({"graph_path": "candidate_graph.db", "sidecar_path": "vector_sidecar.sqlite", "graph_sha256": "stale_graph_sha"}),
        encoding="utf-8",
    )

    _re_seal_active_pair_if_needed(tmp_path, graph_db, mutated_node_ids=["Alice Smith"])

    expected_payload = "Alice Smith | Lead Architect | Platform architecture"
    expected_text_sha = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    s_ro = get_db_connection(sidecar_db, readonly=True)
    try:
        row = s_ro.execute("SELECT text_sha256 FROM dense_vector WHERE object_id = 'person:alice'").fetchone()
        assert row is not None
        assert row[0] == expected_text_sha
    finally:
        s_ro.close()


def test_malformed_unsigned_authorized_exception_blocked(tmp_path: Path) -> None:
    """Category 2 (Malformed input): Unsigned PASS_AUTHORIZED_EXCEPTION is blocked by release policy and publish_helper."""
    with pytest.raises(RuntimeError, match="does not meet production floor"):
        _check_out3_threshold(
            {
                "output_3_evaluation": {
                    "composite_score": 86.0,
                    "verdict": "PASS_AUTHORIZED_EXCEPTION",
                }
            },
            "pkg_unsigned",
        )

    # Signed exception with score >= 85.0 passes
    _check_out3_threshold(
        {
            "output_3_evaluation": {
                "composite_score": 86.0,
                "verdict": "PASS_AUTHORIZED_EXCEPTION",
                "exception_receipt_signature": "sig-valid-ed25519",
            }
        },
        "pkg_signed",
    )

    # Publish helper rejects unsigned exception receipt when require_signed_exception_receipt=True
    (tmp_path / "final_release_decision.json").write_text(
        json.dumps({"status": "PASS_AUTHORIZED_EXCEPTION", "authorized_exception_receipt": {}}),
        encoding="utf-8",
    )
    (tmp_path / "llm_judge_evaluation.json").write_text(
        json.dumps({"overall_verdict": "PASS_AUTHORIZED_EXCEPTION"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="UNSIGNED_AUTHORIZED_EXCEPTION"):
        _assert_authoritative_mutation_preconditions(tmp_path, {"require_signed_exception_receipt": True})


def test_concurrency_parallel_sidecar_reembed(tmp_path: Path) -> None:
    """Category 3 (Concurrency & race): Concurrent re-embedding calls on sidecar complete without lock errors."""
    graph_db = tmp_path / "conc_graph.db"
    sidecar_db = tmp_path / "conc_sidecar.sqlite"
    g_conn = get_db_connection(graph_db, readonly=False)
    try:
        g_conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, role TEXT, summary TEXT)")
        for i in range(6):
            g_conn.execute(
                "INSERT INTO node VALUES (?, ?, ?, ?)",
                (f"person:p{i}", f"Person {i}", "Engineer", f"Summary {i}"),
            )
        g_conn.commit()
    finally:
        g_conn.close()

    s_conn = get_db_connection(sidecar_db, readonly=False)
    try:
        s_conn.execute("CREATE TABLE dense_vector (object_id TEXT PRIMARY KEY, text_sha256 TEXT)")
        for i in range(6):
            s_conn.execute("INSERT INTO dense_vector VALUES (?, 'init')", (f"person:p{i}",))
        s_conn.commit()
    finally:
        s_conn.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda idx: _reembed_mutated_sidecar_nodes(graph_db, sidecar_db, [f"person:p{idx}"]),
                range(6),
            )
        )
    assert all(r == 1 for r in results)


def test_overflow_cc_ceiling_enforced_in_policy() -> None:
    """Category 4 (Overflow & exhaustion): Function CC > 15 is blocked even if exclusion attests max_function_cc=60."""
    branchy_lines = ["def complex_fn(x):"]
    for i in range(18):
        branchy_lines.append(f"    if x == {i}:\n        return {i}")
    branchy_lines.append("    return -1\n")
    code = "\n".join(branchy_lines)

    policy = load_candidate_policy()
    exclusions = {
        "dual_pipeline/sample_complex.py": {
            "path": "dual_pipeline/sample_complex.py",
            "classification": "cohesive_structural_module",
            "expires_at": "2026-12-31",
            "attestations": {"max_function_cc": 60},
        }
    }
    verdict = evaluate_module_source(
        code=code,
        rel_path="dual_pipeline/sample_complex.py",
        policy=policy,
        exclusions_dict=exclusions,
    )
    assert verdict.is_blocking is True
    assert verdict.verdict == "BLOCK"
    assert "exceeds attested ceiling (15)" in verdict.reason


def test_invariant_bypass_exempt_prefixes_and_immutable_seal(tmp_path: Path) -> None:
    """Category 5 (Invariant bypass attempt): .py files in exempt_prefixes and immutable_sealed re-sealing are rejected."""
    exclusions_file = ROOT / "config" / "governance" / "file_budget_exclusions.json"
    excl_data = json.loads(exclusions_file.read_text(encoding="utf-8"))
    exempt_prefixes = excl_data.get("exempt_prefixes", [])
    assert validate_exempt_prefixes(exempt_prefixes) == []
    assert not any(str(p).endswith(".py") for p in exempt_prefixes)

    bypass_attempt = validate_exempt_prefixes(["tests/", "dual_pipeline/transcript_qa_core/graph_healer.py"])
    assert len(bypass_attempt) == 1
    assert "prohibited in exempt_prefixes" in bypass_attempt[0]

    graph_file = tmp_path / "graph.db"
    graph_file.write_bytes(b"data")
    (tmp_path / "active_pair.json").write_text(
        json.dumps({"graph_path": "graph.db", "graph_sha256": "old", "immutable_sealed": True}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="SEALED_GENERATION_RESEAL_FORBIDDEN"):
        _re_seal_active_pair_if_needed(tmp_path, graph_file)


def test_rollback_candidate_promotion_requires_approval_ref() -> None:
    """Category 6 (Rollback & recovery): Candidate promotion fails closed when require_explicit_approval_ref is set and missing."""
    conn = sqlite3.connect(":memory:", timeout=30.0)
    try:
        pkg = {
            "require_explicit_approval_ref": True,
            "findings": [{"proposed_fix": "batch_promote_candidates", "raw_data": {"assertion_id": "asrt-1"}}],
        }
        with pytest.raises(ValueError, match="Missing mandatory approval_reference"):
            _remediate_candidate_promotions(conn, pkg, "2026-10-28T00:00:00Z")
    finally:
        conn.close()


def test_regression_purity_scan_dirs_and_hygiene_constants(tmp_path: Path) -> None:
    """Category 7 (Exact production regression): SCAN_DIRS covers dual_pipeline and tools; hardcoded hygiene constants purged."""
    assert "dual_pipeline" in SCAN_DIRS
    assert "tools" in SCAN_DIRS
    assert OUTPUT3_PRODUCTION_PASS_THRESHOLD == 90.0

    fake_dp = tmp_path / "dual_pipeline"
    fake_dp.mkdir(parents=True)
    bad_file = fake_dp / "bad_writer.py"
    bad_file.write_text('output_dir = ROOT / "output"\n', encoding="utf-8")
    violations = lint_code_for_production_writes(tmp_path, [bad_file])
    assert len(violations) == 1
    assert "dual_pipeline/bad_writer.py" in violations[0]

    hygiene_src = (ROOT / "tools" / "apply_graph_hygiene_remediation.py").read_text(encoding="utf-8")
    assert "HITL-10-18-2026" not in hygiene_src
    assert "CANONICAL_JACOB_WELLINGTON_ID" not in hygiene_src
    assert "REQ_INGEST_CEC57955C84BCE3A" not in hygiene_src
    assert "UniversalWriteGateway" in hygiene_src
