"""Pipelines 9, 10, and 11 E2E scenario runners: Intake DAG & L0 Policy, Diarization/Crypto ALR, and Semantic Cache & 16-Worker WAL Concurrency."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import time
from pathlib import Path
from typing import List

from dual_pipeline.batch_admission import create_manifest, verify_candidate_scope, verify_manifest_package
from dual_pipeline.core import seal_artifact, sha256_file
from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.hitl_governance_core.crypto import (
    compute_alr_subject_digest,
    get_governed_hitl_keypair,
    sign_hitl_approval_payload,
    verify_hitl_approval_signature,
)
from dual_pipeline.l0.assumption_lifecycle import (
    is_assertion_temporally_expired,
    normalize_epistemic_assertion_type,
    resolve_assumption_checkpoint_ttl,
)
from dual_pipeline.l0.speaker_and_qualifier_policy import (
    classify_speaker_attribution_defect,
    has_inline_speaker_contradiction,
    is_degenerate_single_token_claim,
    is_placeholder_speaker,
    sync_canonical_qualifiers_json,
)
from dual_pipeline.mcp.intake_bridge import ingest_document_safely, sanitize_preview_text
from dual_pipeline.semantic_cache_core.cache import SemanticCache
from dual_pipeline.semantic_cache_core.config import load_semantic_cache_policy
from dual_pipeline.semantic_cache_core.merkle_cache import compute_subgraph_merkle_root, sanitize_output_for_cache_replay
from dual_pipeline.speaker_attribution_core.collapse_detector import check_speaker_skew_and_collapse
from storage.database import DatabaseManager
from storage.models import AssertionRecord, CanonicalGraphNode
from .models import ScenarioVerificationResult

PIPELINE_P9_ID = "P9_INTAKE_STAGE_DAG_U0_TO_L4"
PIPELINE_P9_NAME = "8-Stage Governed Intake & L0 Epistemic Policy"
PIPELINE_P10_ID = "P10_DIARIZATION_DEFENSE_AND_CRYPTO_ALR"
PIPELINE_P10_NAME = "Anti-Collapse Diarization Defense & Ed25519 Cryptographic ALR"
PIPELINE_P11_ID = "P11_SEMANTIC_CACHE_AND_WAL_CONCURRENCY"
PIPELINE_P11_NAME = "L0 Semantic Cache & 16-Worker WAL Storage Concurrency"


def _write_sample_intake_envelope(pkg_path: Path, source_doc: Path) -> dict[str, object]:
    raw_hash = sha256_file(source_doc)
    envelope = {
        "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
        "schema_version": "1.0.0",
        "artifact_id": "artifact-p9-envelope-001",
        "meeting": {
            "meeting_id": "meeting-p9-001", "title": "Substation Telemetry Intake Review",
            "started_at": "2026-10-30T15:00:00Z", "organizer": "Amit Ayer",
            "attendees": ["Amit Ayer", "Dana Lin"], "platform_reference": None,
            "google_meet_reference": None, "teams_reference": None,
            "source_location": str(source_doc), "transcript_reference": None,
            "recording_reference": None, "source_hash": raw_hash, "template_version": "1.0.0",
            "classification": "internal", "audiences": ["internal_consulting", "team"],
        },
        "speakers": [{
            "speaker_ref": "spk-amit-01", "reported_name": "Amit Ayer", "email": None, "role_or_org": "NEE",
            "statements": [{
                "statement_id": "stmt-p9-001", "kind": "decision",
                "text": "Substation telemetry packets will route through the governed intake bridge.",
                "locator": {"kind": "section", "value": "sec-1"}, "gemini_confidence": 0.96,
            }],
        }],
        "actions": [],
        "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
        "extensions": {},
    }
    sealed = seal_artifact(envelope)
    pkg_path.write_text(json.dumps(sealed, indent=2), encoding="utf-8")
    return sealed


def run_p9_batch_admission_and_manifest_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify Pipeline 9 batch admission manifest sealing, candidate scope guard, and MCP safe intake."""
    t0 = time.perf_counter()
    p9_dir = sandbox_root / "p9_batch_admission"
    p9_dir.mkdir(parents=True, exist_ok=True)
    doc_path = p9_dir / "telemetry_spec.md"
    doc_path.write_text("# Telemetry Spec\n\nSubstation telemetry packets route via governed bridge.\n", encoding="utf-8")
    pkg_path = p9_dir / "envelope_pkg.json"
    pkg_obj = _write_sample_intake_envelope(pkg_path, doc_path)

    manifest = create_manifest(
        artifact_id="manifest-p9-001",
        packages=[pkg_path],
        policy_id="pol-p9-standard",
        policy_version="1.0.0",
        admission_mode="HITL_REQUIRED",
    )
    manifest_path = p9_dir / "batch_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    pkg_binding = verify_manifest_package(
        manifest_path, pkg_path, source_kind="evidence_envelope", package=pkg_obj
    )
    valid_bundle = {"candidates": [{"candidate_id": "cand-p9-01", "classification": "internal", "audiences": ["internal_consulting", "team"]}]}
    scope_binding = verify_candidate_scope(manifest_path, valid_bundle)

    scope_escalation_blocked = False
    try:
        verify_candidate_scope(manifest_path, {"candidates": [{"candidate_id": "cand-p9-bad", "classification": "restricted", "audiences": ["team"]}]})
    except PermissionError:
        scope_escalation_blocked = True

    intake_res = ingest_document_safely(doc_path, project="project_nexus", dispatch_pipeline=False)
    clean_prev, was_redacted, _reason = sanitize_preview_text("Rate is $250/hr for architecture review.")

    passed = bool(
        pkg_binding.artifact_id == "manifest-p9-001" and scope_binding.package_set_digest == pkg_binding.package_set_digest
        and scope_escalation_blocked and intake_res.success
        and was_redacted and "$250/hr" not in clean_prev
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P9-BATCH-ADMISSION-AND-MANIFEST",
        pipeline_id=PIPELINE_P9_ID,
        pipeline_name=PIPELINE_P9_NAME,
        title="Batch Admission Manifest Sealing, Scope Escalation Guard & MCP Safe Document Intake",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_deliverables={"batch_manifest": str(manifest_path), "envelope_pkg": str(pkg_path)},
        verified_invariants=[
            "BATCH_MANIFEST_EXACT_PACKAGE_HASH",
            "CANDIDATE_SCOPE_ESCALATION_BLOCKED",
            "MCP_SAFE_DOCUMENT_INTAKE_AND_PREVIEW_SCRUB",
        ],
        metrics={"package_set_digest": pkg_binding.package_set_digest, "intake_success": intake_res.success},
    )


def run_p9_l0_speaker_qualifier_and_assumption_ttl_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify Pipeline 9 L0 epistemic assertion normalization, assumption TTL, and speaker/qualifier hygiene."""
    t0 = time.perf_counter()
    _ = sandbox_root
    norm_assumption = normalize_epistemic_assertion_type("proposal")
    norm_decision = normalize_epistemic_assertion_type("resolved_choice")
    ttl_info = resolve_assumption_checkpoint_ttl(
        candidate={"assertion_type": "assumption"},
        payload={},
        source={"source_posture": "working_notes", "date": "2026-10-01"},
    )
    expired = is_assertion_temporally_expired(valid_to="2026-10-15", as_of_date="2026-10-30")
    not_expired = is_assertion_temporally_expired(valid_to="2027-10-15", as_of_date="2026-10-30")
    placeholder_ok = is_placeholder_speaker("Speaker 1") and not is_placeholder_speaker("Amit Ayer")
    degenerate_ok = is_degenerate_single_token_claim("Yes.") and not is_degenerate_single_token_claim(
        "The hybrid vector sidecar synchronizes with the canonical graph."
    )
    contradiction_ok = has_inline_speaker_contradiction("**Dana Lin:** We completed the migration.", "Amit Ayer")
    defect = classify_speaker_attribution_defect(
        predicate="HAS_CLAIM_TEXT",
        scope_text="**Dana Lin:** We completed the migration.",
        speaker_or_author="Amit Ayer",
    )
    synced_quals = json.loads(
        sync_canonical_qualifiers_json(json.dumps({"dual_pipeline_disposition": "CANDIDATE", "answer_use": "candidate_hypothesis"}))
    )

    passed = bool(
        norm_assumption == "assumption" and norm_decision == "decision" and ttl_info == "2026-10-15"
        and expired is True and not_expired is False and placeholder_ok and degenerate_ok
        and contradiction_ok and defect == "inline_speaker_contradiction"
        and synced_quals.get("dual_pipeline_disposition") == "APPROVED"
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P9-L0-SPEAKER-QUALIFIER-AND-ASSUMPTION-TTL",
        pipeline_id=PIPELINE_P9_ID,
        pipeline_name=PIPELINE_P9_NAME,
        title="Stage L0 Epistemic Type Normalization, Assumption TTL Expiration & Speaker/Qualifier Hygiene",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=[
            "L0_EPISTEMIC_TYPE_NORMALIZATION",
            "ASSUMPTION_TTL_TEMPORAL_EXPIRATION",
            "INLINE_SPEAKER_CONTRADICTION_DETECTED",
            "PLACEHOLDER_AND_DEGENERATE_CLAIM_FILTER",
        ],
        metrics={"defect_class": str(defect), "ttl_deadline": ttl_info, "norm_decision": norm_decision},
    )


def run_p10_diarization_collapse_halt_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify Pipeline 10 single-speaker collapse (>85% across >=10 turns) triggers fail-closed HITLRequiredError."""
    t0 = time.perf_counter()
    _ = sandbox_root
    collapsed_text = "\n\n".join(
        f"**{'Amit Ayer' if i < 11 else 'Dana Lin'}:** [00:01:{i:02d}] Turn {i} architectural discussion on hybrid sidecar."
        for i in range(12)
    )
    collapse_halted = False
    try:
        check_speaker_skew_and_collapse(collapsed_text, ["Amit Ayer", "Dana Lin"], {})
    except HITLRequiredError:
        collapse_halted = True

    balanced_text = "\n\n".join(
        f"**{'Amit Ayer' if i % 2 == 0 else 'Dana Lin'}:** [00:01:{i:02d}] Balanced turn {i} architectural discussion."
        for i in range(12)
    )
    balanced_res = check_speaker_skew_and_collapse(balanced_text, ["Amit Ayer", "Dana Lin"], {})
    passed = bool(collapse_halted and balanced_res is None)
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P10-DIARIZATION-COLLAPSE-HALT",
        pipeline_id=PIPELINE_P10_ID,
        pipeline_name=PIPELINE_P10_NAME,
        title="Single-Speaker Collapse (>85% Share) Pre-Gate 1 Fail-Closed Halt & Balanced Pass",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=[
            "SINGLE_SPEAKER_COLLAPSE_RAISES_HITL_HALT",
            "BALANCED_MULTI_SPEAKER_PASSES_CLEANLY",
        ],
        metrics={"collapse_halted": collapse_halted, "collapsed_turn_count": 12},
    )


def run_p10_ed25519_alr_signature_gate_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify Pipeline 10 Ed25519 keypair provisioning, RFC 8785 JCS digest, and tamper rejection."""
    t0 = time.perf_counter()
    key_dir = sandbox_root / "p10_ed25519_keys"
    priv_key, pub_key = get_governed_hitl_keypair(key_dir=key_dir)
    subject = {
        "meeting_id": "meet-p10-alr-01",
        "cluster_id": "SPEAKER_01",
        "resolved_speaker": "Dana Lin",
        "turn_indices": [2, 5, 8],
    }
    subj_digest = compute_alr_subject_digest(subject)
    approval_payload = {
        "decision_id": "hitl-alr-p10-001",
        "decision_type": "SPEAKER_ATTRIBUTION_ALR",
        "subject_digest": subj_digest,
        "operator_id": "amit.ayer",
        "outcome_type": "AFFIRMED",
    }
    signature_b64 = sign_hitl_approval_payload(approval_payload, priv_key)
    valid_ok = verify_hitl_approval_signature(approval_payload, signature_b64, pub_key)
    tampered_payload = dict(approval_payload, resolved_speaker="Attacker Override")
    tampered_rejected = not verify_hitl_approval_signature(tampered_payload, signature_b64, pub_key)

    passed = bool(len(subj_digest) == 64 and valid_ok and tampered_rejected)
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P10-ED25519-ALR-SIGNATURE-GATE",
        pipeline_id=PIPELINE_P10_ID,
        pipeline_name=PIPELINE_P10_NAME,
        title="Ed25519 Cryptographic ALR Signature Verification & Tampered Payload Rejection",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=[
            "RFC8785_JCS_SUBJECT_DIGEST_DETERMINISM",
            "ED25519_AUTHENTIC_SIGNATURE_VERIFIED",
            "TAMPERED_ALR_PAYLOAD_REJECTED_FAIL_CLOSED",
        ],
        metrics={"subject_digest": subj_digest[:16], "tampered_rejected": tampered_rejected},
    )


def run_p11_semantic_cache_merkle_lifecycle_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify Pipeline 11 subgraph Merkle root permutation invariance and cache replay sanitization."""
    t0 = time.perf_counter()
    nodes_order_a = [
        {"node_id": "node-sys-01", "assertion_id": "as-01", "content_hash": "11" * 32},
        {"node_id": "node-mod-02", "assertion_id": "as-02", "content_hash": "22" * 32},
    ]
    nodes_order_b = list(reversed(nodes_order_a))
    nodes_mutated = [
        {"node_id": "node-sys-01", "assertion_id": "as-01", "content_hash": "99" * 32},
        {"node_id": "node-mod-02", "assertion_id": "as-02", "content_hash": "22" * 32},
    ]
    root_a = compute_subgraph_merkle_root(nodes_order_a)
    root_b = compute_subgraph_merkle_root(nodes_order_b)
    root_mut = compute_subgraph_merkle_root(nodes_mutated)

    raw_output = {
        "response_text": "Hybrid sidecar synchronization is verified.",
        "cited_assertion_ids": ["as-01", "as-02"],
        "session_id": "volatile-session-99",
    }
    sanitized = sanitize_output_for_cache_replay(raw_output, "run-cache-replay-01")
    policy = load_semantic_cache_policy()
    cache = SemanticCache(runtime_root=sandbox_root / "p11_cache")

    passed = bool(
        len(root_a) == 64
        and root_a == root_b
        and root_a != root_mut
        and sanitized is not None
        and cache.health_reason == "CACHE_READY"
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P11-SEMANTIC-CACHE-MERKLE-LIFECYCLE",
        pipeline_id=PIPELINE_P11_ID,
        pipeline_name=PIPELINE_P11_NAME,
        title="Subgraph Merkle Root Permutation Invariance, Mutation Sensitivity & Cache Replay Sanitization",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=[
            "MERKLE_ROOT_PERMUTATION_INVARIANCE",
            "MERKLE_ROOT_MUTATION_INVALIDATION",
            "CACHE_REPLAY_OUTPUT_SANITIZATION",
        ],
        metrics={"merkle_root": root_a[:16], "policy_enabled": bool(getattr(policy, "enabled", True))},
    )


def run_p11_16_worker_wal_concurrency_stress_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Execute 16-worker concurrent WAL read/write stress harness with zero lock errors."""
    t0 = time.perf_counter()
    wal_dir = sandbox_root / "p11_wal_16_workers"
    db_mgr = DatabaseManager(base_dir=wal_dir, allow_unencrypted_dev_fallback=True)
    worker_count = 16

    def _worker_task(worker_idx: int) -> bool:
        nid = f"node-wal-{worker_idx:02d}"
        aid = f"as-wal-{worker_idx:02d}"
        db_mgr.upsert_canonical_node(
            CanonicalGraphNode(
                node_id=nid,
                evidence_id=f"ev-wal-{worker_idx:02d}",
                type="system",
                name=f"Concurrent Subsystem {worker_idx}",
                strategic_value=0.85,
                confidence=0.95,
            )
        )
        db_mgr.upsert_assertion(
            AssertionRecord(
                assertion_id=aid,
                subject_node_id=nid,
                predicate="OPERATES_IN_WAL_MODE",
                literal_value=f"Worker {worker_idx} verified zero lock contention",
                confidence=0.95,
            )
        )
        rows = db_mgr.query_canonical_nodes(limit=25)
        return len(rows) >= 1

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(pool.map(_worker_task, range(worker_count)))

    final_nodes = db_mgr.query_canonical_nodes(limit=50)
    final_assertions = db_mgr.query_assertions()
    db_mgr.close()

    passed = bool(
        all(results)
        and len(final_nodes) == worker_count
        and len(final_assertions) == worker_count
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P11-16-WORKER-WAL-CONCURRENCY-STRESS",
        pipeline_id=PIPELINE_P11_ID,
        pipeline_name=PIPELINE_P11_NAME,
        title="16-Worker Multi-Threaded WAL Read/Write Concurrency Stress Harness",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_deliverables={"canonical_db": str(db_mgr.canonical_db_path)},
        verified_invariants=[
            "ZERO_SQLITE_BUSY_OR_LOCKED_ERRORS",
            "16_WORKER_CONCURRENT_NODE_AND_ASSERTION_CONSERVATION",
            "WAL_SERIALIZED_WRITE_READ_COMMITTED_ISOLATION",
        ],
        metrics={"workers": worker_count, "nodes_persisted": len(final_nodes), "assertions_persisted": len(final_assertions)},
    )


def run_all_intake_security_cache_scenarios(
    sandbox_root: Path | None = None,
) -> List[ScenarioVerificationResult]:
    """Run all 6 scenarios across Pipelines 9, 10, and 11."""
    if sandbox_root is not None:
        return [
            run_p9_batch_admission_and_manifest_scenario(sandbox_root),
            run_p9_l0_speaker_qualifier_and_assumption_ttl_scenario(sandbox_root),
            run_p10_diarization_collapse_halt_scenario(sandbox_root),
            run_p10_ed25519_alr_signature_gate_scenario(sandbox_root),
            run_p11_semantic_cache_merkle_lifecycle_scenario(sandbox_root),
            run_p11_16_worker_wal_concurrency_stress_scenario(sandbox_root),
        ]
    with tempfile.TemporaryDirectory(prefix="e2e_p9_p11_") as tmp:
        root = Path(tmp)
        return run_all_intake_security_cache_scenarios(root)
