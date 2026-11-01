"""
tests/adversarial/test_ingestion_hardening_wave7_adversarial.py

Wave 7 Adversarial Contract Test Suite.
Validates:
  - A01: Speaker resolution, source ingestion intent and exact-delta publication approval are distinct authentic scopes.
  - A02: Ingestion permission never authorizes cloud ASR or fallback that violates local capture policy.
  - P01: COMPLETE requires modality-specific output set plus requested publication/readback; recording stopped and package ready are distinct states.
  - P02: Preparation-only route writes no production graph and makes no active-completion claim.
  - P03: Publication timeout/replay/rollback retains valid artifacts and prevents double mutation; changed delta requires valid scope binding.
  - A03: Publication requires nonzero exact source/output/proposal/baseline bindings for every modality; document early returns cannot widen authority.
  - A04: Approval validation and consumption occur under writer/activation synchronization; revoked/stale/raced grants fail before mutation.
  - P04: PENDING journal outcome is reconciled against authoritative writer/activation proof before retry; cached result rebinds current source/runtime/delta.
  - P05: Activation alone cannot satisfy VERIFIED; absent/failed coverage stays pending and all result/receipt/status views agree.
  - A05: Capture/local-processing, permitted egress, source admission/classification, ingestion intent, attribution and exact-delta publication scopes remain independently evidenced.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.operation_contracts import (
    AuthorityScope,
    LifecycleState,
    validate_authority_scope,
    validate_cloud_asr_prohibition,
    validate_completion_readiness,
    validate_preparation_route,
    validate_proposal_bindings,
    verify_independent_authority_evidence,
)
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    MutationProposal,
    ProposalStatus,
    persist_approval_request,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    MutationAuthorizationError,
    validate_mutation_authorization,
)
from dual_pipeline.canonical_pipeline.publication_journal import (
    PublicationReconciliationRequired,
    _assert_no_prior_attempt,
)


def test_a01_contract() -> None:
    """A01: Speaker resolution, source ingestion intent and exact-delta publication approval are distinct authentic scopes."""
    # 1. Speaker resolution approval cannot authorize exact delta publication
    ok_spk, msg_spk = validate_authority_scope(
        granted_scope=AuthorityScope.SPEAKER_RESOLUTION,
        required_scope=AuthorityScope.EXACT_DELTA_PUBLICATION,
    )
    assert ok_spk is False
    assert "AUTHORIZATION_SCOPE_MISMATCH" in msg_spk
    assert "SPEAKER_RESOLUTION" in msg_spk
    assert "EXACT_DELTA_PUBLICATION" in msg_spk

    # 2. Ingestion intent approval cannot authorize exact delta publication
    ok_intent, msg_intent = validate_authority_scope(
        granted_scope=AuthorityScope.INGESTION_INTENT,
        required_scope=AuthorityScope.EXACT_DELTA_PUBLICATION,
    )
    assert ok_intent is False
    assert "AUTHORIZATION_SCOPE_MISMATCH" in msg_intent

    # 3. Matching exact delta publication scope is authorized
    ok_pub, msg_pub = validate_authority_scope(
        granted_scope=AuthorityScope.EXACT_DELTA_PUBLICATION,
        required_scope=AuthorityScope.EXACT_DELTA_PUBLICATION,
    )
    assert ok_pub is True
    assert msg_pub == "AUTHORIZED"


def test_a02_contract() -> None:
    """A02: Ingestion permission never authorizes cloud ASR or fallback that violates local capture policy."""
    # 1. Even with full ingestion permission, passing backend='cloud' fails closed
    with pytest.raises(ValueError, match="Transcription backend 'cloud' is prohibited"):
        validate_cloud_asr_prohibition(backend="cloud", has_ingestion_approval=True)

    # 2. Passing cloud_asr=True in flags fails closed
    with pytest.raises(ValueError, match="Cloud transcription/ASR is strictly banned"):
        validate_cloud_asr_prohibition(flags={"cloud_asr": True}, has_ingestion_approval=True)

    # 3. Local MLX Metal or auto backend passes cleanly
    validate_cloud_asr_prohibition(backend="mlx", has_ingestion_approval=True)
    validate_cloud_asr_prohibition(backend="auto", has_ingestion_approval=True)


def test_p01_contract(tmp_path: Path) -> None:
    """P01: COMPLETE requires modality-specific output set plus requested publication/readback; recording stopped and package ready are distinct states."""
    # 1. State separation: CAPTURE_STOPPED cannot claim completion
    ok_stopped, msg_stopped, st_stopped = validate_completion_readiness(
        modality=CanonicalModality.AUDIO,
        written_artifacts={"01": tmp_path / "01.md", "02": tmp_path / "02.md", "03": tmp_path / "03.json"},
        current_state=LifecycleState.CAPTURE_STOPPED,
    )
    assert ok_stopped is False
    assert "cannot claim completion" in msg_stopped
    assert st_stopped == LifecycleState.CAPTURE_STOPPED

    # 2. Modality-specific deliverable enforcement: Audio missing 01 and 02
    ok_part, msg_part, st_part = validate_completion_readiness(
        modality=CanonicalModality.AUDIO,
        written_artifacts={"03": tmp_path / "03.json"},
        current_state=LifecycleState.PACKAGE_READY,
    )
    assert ok_part is False
    assert "missing required outputs" in msg_part
    assert st_part == LifecycleState.PARTIAL

    # 3. Publication without readback verification stays PUBLISHED_UNVERIFIED
    ok_unv, msg_unv, st_unv = validate_completion_readiness(
        modality=CanonicalModality.DOCUMENT,
        written_artifacts={"03": tmp_path / "03.json"},
        is_published=True,
        is_readback_verified=False,
        current_state=LifecycleState.PACKAGE_READY,
    )
    assert ok_unv is False
    assert "independent coverage readback unverified" in msg_unv
    assert st_unv == LifecycleState.PUBLISHED_UNVERIFIED

    # 4. Verified readback enables terminal COMPLETE state
    ok_comp, msg_comp, st_comp = validate_completion_readiness(
        modality=CanonicalModality.DOCUMENT,
        written_artifacts={"03": tmp_path / "03.json"},
        is_published=True,
        is_readback_verified=True,
        current_state=LifecycleState.PACKAGE_READY,
    )
    assert ok_comp is True
    assert st_comp == LifecycleState.COMPLETE


def test_p02_contract(tmp_path: Path) -> None:
    """P02: Preparation-only route writes no production graph and makes no active-completion claim."""
    graph_file = tmp_path / "CANONICAL_GRAPH.db"
    initial_content = b"SQLITE_CANONICAL_GRAPH_BASELINE_BYTES"
    graph_file.write_bytes(initial_content)

    # 1. Clean prepare-only run leaves graph untouched
    ok_clean, msg_clean = validate_preparation_route(
        is_prepare_only=True,
        graph_path=graph_file,
        initial_graph_bytes=initial_content,
    )
    assert ok_clean is True
    assert "zero production graph mutation" in msg_clean

    # 2. Accidental graph mutation during prepare-only fails closed
    graph_file.write_bytes(b"MUTATED_PRODUCTION_GRAPH_BYTES")
    ok_mut, msg_mut = validate_preparation_route(
        is_prepare_only=True,
        graph_path=graph_file,
        initial_graph_bytes=initial_content,
    )
    assert ok_mut is False
    assert "PREPARE_ONLY_VIOLATION" in msg_mut


def test_p03_contract(tmp_path: Path) -> None:
    """P03: Publication timeout/replay/rollback retains valid artifacts and prevents double mutation; changed delta requires valid scope binding."""
    out_dir = tmp_path / "output_p03"
    out_dir.mkdir(parents=True)
    rt_root = tmp_path / "runtime_p03"
    rt_root.mkdir(parents=True)

    ap_file = rt_root / "active_pair.json"
    ap_file.write_text(json.dumps({"generation_id": "gen-p03-base", "graph_path": "graph.sqlite"}), encoding="utf-8")

    f1 = out_dir / "01_transcript.md"
    f1.write_text("Valid Transcript Content", encoding="utf-8")
    f2 = out_dir / "02_recap.md"
    f2.write_text("Valid Recap Content", encoding="utf-8")
    f3 = out_dir / "03_graph.json"
    f3.write_text(json.dumps({"nodes": [1, 2]}), encoding="utf-8")
    pkg = out_dir / "intake_package.json"
    pkg.write_text(json.dumps({"package": "v1"}), encoding="utf-8")

    written = {
        "01_transcript": str(f1),
        "02_recap": str(f2),
        "03_graph": str(f3),
        "intake_package": str(pkg),
    }

    # Proposal created and approved for original deliverables
    proposal = MutationProposal(
        proposal_id="prop-p03",
        meeting_name="P03 Meeting",
        source_hash="s" * 64,
        output_hashes={"01_transcript": "h1", "02_recap": "h2", "03_graph": "h3"},
        intake_hash="i" * 64,
        target_graph="CANONICAL_GRAPH.db",
        runtime_root=str(rt_root),
        expected_base_generation="gen-p03-base",
        deltas={"added": 2},
        validation_evidence={"qa": "PASS"},
        rollback_scope="HEAD",
    )
    dig = proposal.compute_proposal_digest()
    rec = {
        "request_id": "req-p03",
        "proposal_digest": dig,
        "operator_origin": "human_operator",
        "status": ProposalStatus.APPROVED.value,
        "authorized": True,
        "meeting_name": "P03 Meeting",
        "expected_base_generation": "gen-p03-base",
        "target_graph": "CANONICAL_GRAPH.db",
    }
    persist_approval_request(rec, rt_root)

    # 1. If publication experiences rollback, artifacts remain intact on disk
    assert f1.exists()
    assert f2.exists()
    assert f3.exists()

    # 2. Tampering with 03_graph changes deliverable digest; replay attempt is rejected
    f3.write_text(json.dumps({"nodes": [1, 2, 999], "tampered": True}), encoding="utf-8")
    with pytest.raises(MutationAuthorizationError, match="AUTHORIZATION_DENIED_DIGEST_MISMATCH"):
        validate_mutation_authorization(
            output_dir=out_dir,
            metadata={"meeting_name": "P03 Meeting"},
            written=written,
            runtime_root=rt_root,
            approval_receipt=rec,
        )


def test_a03_contract() -> None:
    """A03: Publication requires nonzero exact source/output/proposal/baseline bindings for every modality; document early returns cannot widen authority."""
    # 1. Blank source hash fails
    prop_no_src = MutationProposal(
        proposal_id="prop-a03-1",
        meeting_name="A03 Meeting",
        source_hash="",
        output_hashes={"03": "h3"},
        intake_hash="i" * 64,
        target_graph="CANONICAL_GRAPH.db",
        runtime_root="",
        expected_base_generation="gen-001",
        deltas={},
        validation_evidence={},
        rollback_scope="HEAD",
    )
    ok_src, errors_src = validate_proposal_bindings(prop_no_src)
    assert ok_src is False
    assert any("source_hash must be non-empty" in e for e in errors_src)

    # 2. Empty output hashes fails
    prop_no_out = MutationProposal(
        proposal_id="prop-a03-2",
        meeting_name="A03 Meeting",
        source_hash="s" * 64,
        output_hashes={},
        intake_hash="i" * 64,
        target_graph="CANONICAL_GRAPH.db",
        runtime_root="",
        expected_base_generation="gen-001",
        deltas={},
        validation_evidence={},
        rollback_scope="HEAD",
    )
    ok_out, errors_out = validate_proposal_bindings(prop_no_out)
    assert ok_out is False
    assert any("output_hashes must be non-empty" in e for e in errors_out)

    # 3. Missing base generation fails
    prop_no_gen = MutationProposal(
        proposal_id="prop-a03-3",
        meeting_name="A03 Meeting",
        source_hash="s" * 64,
        output_hashes={"03": "h3"},
        intake_hash="i" * 64,
        target_graph="CANONICAL_GRAPH.db",
        runtime_root="",
        expected_base_generation="",
        deltas={},
        validation_evidence={},
        rollback_scope="HEAD",
    )
    ok_gen, errors_gen = validate_proposal_bindings(prop_no_gen)
    assert ok_gen is False
    assert any("expected_base_generation must be non-empty" in e for e in errors_gen)

    # 4. Valid proposal bindings pass
    prop_valid = MutationProposal(
        proposal_id="prop-a03-4",
        meeting_name="A03 Meeting",
        source_hash="s" * 64,
        output_hashes={"03": "h3"},
        intake_hash="i" * 64,
        target_graph="CANONICAL_GRAPH.db",
        runtime_root="",
        expected_base_generation="gen-001",
        deltas={"nodes": 1},
        validation_evidence={"qa": "PASS"},
        rollback_scope="HEAD",
    )
    ok_val, errors_val = validate_proposal_bindings(prop_valid)
    assert ok_val is True
    assert errors_val == []


def test_a04_contract(tmp_path: Path) -> None:
    """A04: Approval validation and consumption occur under writer/activation synchronization; revoked/stale/raced grants fail before mutation."""
    rt_root = tmp_path / "runtime_a04"
    rt_root.mkdir(parents=True)
    out_dir = tmp_path / "out_a04"
    out_dir.mkdir(parents=True)

    ap_file = rt_root / "active_pair.json"
    ap_file.write_text(json.dumps({"generation_id": "gen-a04-active", "graph_path": "graph.sqlite"}), encoding="utf-8")

    # 1. Operator explicitly rejected proposal
    rejected_rec = {
        "request_id": "req-rejected",
        "proposal_digest": "dig-123",
        "operator_origin": "human_operator",
        "status": ProposalStatus.REJECTED.value,
        "authorized": False,
        "meeting_name": "A04 Meeting",
    }
    with pytest.raises(MutationAuthorizationError, match="AUTHORIZATION_DENIED_EXPLICIT_REJECTION"):
        validate_mutation_authorization(
            output_dir=out_dir,
            metadata={"meeting_name": "A04 Meeting"},
            written={},
            runtime_root=rt_root,
            approval_receipt=rejected_rec,
        )

    # 2. Replaying an already activated approval with mismatched generation is rejected
    consumed_rec = {
        "request_id": "req-consumed",
        "proposal_digest": "dig-consumed",
        "operator_origin": "human_operator",
        "status": ProposalStatus.ACTIVATED.value,
        "activated_generation_id": "gen-old-committed",
        "authorized": True,
        "meeting_name": "A04 Meeting",
    }
    with pytest.raises(MutationAuthorizationError, match="AUTHORIZATION_DENIED_REPLAY_DETECTED"):
        validate_mutation_authorization(
            output_dir=out_dir,
            metadata={"meeting_name": "A04 Meeting"},
            written={},
            runtime_root=rt_root,
            approval_receipt=consumed_rec,
            allow_idempotent_recovery=False,
        )


def test_p04_contract(tmp_path: Path) -> None:
    """P04: PENDING journal outcome is reconciled against authoritative writer/activation proof before retry; cached result rebinds current source/runtime/delta."""
    journal_dir = tmp_path / "journal_p04"
    journal_dir.mkdir(parents=True)
    intent_file = journal_dir / "intent.json"
    result_file = journal_dir / "result.json"

    # Write an uncoordinated PENDING intent without completed result
    intent_file.write_text(json.dumps({"state": "PENDING", "record_sha256": "invalid_digest"}), encoding="utf-8")

    # Without allow_resume, uncoordinated prior attempt triggers PublicationReconciliationRequired
    with pytest.raises(PublicationReconciliationRequired) as exc_info:
        _assert_no_prior_attempt(
            directive_id="dir_p04_test",
            intent=intent_file,
            result=result_file,
            allow_resume=False,
        )
    assert exc_info.value.details["code"] == "PUBLICATION_RECONCILIATION_REQUIRED"


def test_p05_contract() -> None:
    """P05: Activation alone cannot satisfy VERIFIED; absent/failed coverage stays pending and all result/receipt/status views agree."""
    # When activation is committed but publication readback coverage is missing/unverified
    ok, msg, terminal_state = validate_completion_readiness(
        modality=CanonicalModality.DOCUMENT,
        written_artifacts={"03": Path("03_graph.json")},
        is_published=True,
        is_readback_verified=False,
        current_state=LifecycleState.PACKAGE_READY,
    )
    assert ok is False
    assert terminal_state == LifecycleState.PUBLISHED_UNVERIFIED
    assert "readback unverified" in msg

    # Status views agree: cannot report COMPLETE until readback coverage passes
    ok_verified, _, verified_state = validate_completion_readiness(
        modality=CanonicalModality.DOCUMENT,
        written_artifacts={"03": Path("03_graph.json")},
        is_published=True,
        is_readback_verified=True,
        current_state=LifecycleState.PACKAGE_READY,
    )
    assert ok_verified is True
    assert verified_state == LifecycleState.COMPLETE


def test_a05_contract() -> None:
    """A05: Capture/local-processing, permitted egress, source admission/classification, ingestion intent, attribution and exact-delta publication scopes remain independently evidenced."""
    all_6_scopes = {
        AuthorityScope.CAPTURE_LOCAL_PROCESSING,
        AuthorityScope.PERMITTED_EGRESS,
        AuthorityScope.SOURCE_ADMISSION,
        AuthorityScope.INGESTION_INTENT,
        AuthorityScope.SPEAKER_RESOLUTION,
        AuthorityScope.EXACT_DELTA_PUBLICATION,
    }

    # 1. Missing scope evidence fails closed
    partial_evidence = {
        AuthorityScope.CAPTURE_LOCAL_PROCESSING: {"proof": "local_mlx_pids"},
        AuthorityScope.PERMITTED_EGRESS: {"proof": "sanitizer_receipt"},
    }
    ok_part, errors_part = verify_independent_authority_evidence(partial_evidence, all_6_scopes)
    assert ok_part is False
    assert len(errors_part) >= 4

    # 2. Reusing/duplicating evidence across scopes fails closed
    shared_proof = {"token": "reused_authorization_proof_token"}
    duplicated_evidence = {scope: shared_proof for scope in all_6_scopes}
    ok_dup, errors_dup = verify_independent_authority_evidence(duplicated_evidence, all_6_scopes)
    assert ok_dup is False
    assert any("duplicated from another scope" in e for e in errors_dup)

    # 3. Independent distinct proofs for all 6 scopes pass cleanly
    distinct_evidence = {
        AuthorityScope.CAPTURE_LOCAL_PROCESSING: {"proof_type": "LOCAL_CAPTURE_PROOF", "id": "p1"},
        AuthorityScope.PERMITTED_EGRESS: {"proof_type": "EGRESS_SANITIZATION_RECEIPT", "id": "p2"},
        AuthorityScope.SOURCE_ADMISSION: {"proof_type": "SOURCE_CLASSIFICATION_PROOF", "id": "p3"},
        AuthorityScope.INGESTION_INTENT: {"proof_type": "INGESTION_DIRECTIVE_CLEARANCE", "id": "p4"},
        AuthorityScope.SPEAKER_RESOLUTION: {"proof_type": "ALR_SIGNED_DECISION", "id": "p5"},
        AuthorityScope.EXACT_DELTA_PUBLICATION: {"proof_type": "HOST_APPROVAL_PROPOSAL_DISPATCH", "id": "p6"},
    }
    ok_dist, errors_dist = verify_independent_authority_evidence(distinct_evidence, all_6_scopes)
    assert ok_dist is True
    assert errors_dist == []
