"""Wave 1 Adversarial & Edge-Case Tests: Resume Journal Reconciliation, Approval Receipt Preservation, and U0 Checkpoints."""

from __future__ import annotations

import concurrent.futures
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.canonical_pipeline.publish_helper import (
    _validate_auth_and_runtime,
    check_idempotent_or_continue,
)
from dual_pipeline.canonical_pipeline.publication_journal import (
    PublicationReconciliationRequired,
    guarded_publication,
)
from dual_pipeline.transcript_stages_core.output01_parser import (
    _has_valid_passing_track1_outputs,
)


def test_adversarial_boundary_and_malformed_json_in_track1_check() -> None:
    """Category 1 & 2: Boundary payloads (empty file, missing dir) and malformed JSON receipts."""
    assert _has_valid_passing_track1_outputs("", {}) is False
    assert _has_valid_passing_track1_outputs(None, {}) is False

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_w1_adv_") as tmp:
        out_p = Path(tmp)
        f1 = out_p / "01_test_hitl_resolved_transcript.md"
        f2 = out_p / "02_test_executive_recap.md"
        f3 = out_p / "03_test_canonical_signal_projection.json"
        f1.write_text("", encoding="utf-8")
        f2.write_text("recap", encoding="utf-8")
        f3.write_text("{}", encoding="utf-8")

        # Malformed truncated JSON in final_release_decision.json must fail closed to False without crashing
        (out_p / "final_release_decision.json").write_text("{truncated_json", encoding="utf-8")
        assert _has_valid_passing_track1_outputs(f1, {}) is False


def test_adversarial_resume_preserves_activated_mutation_approval_receipt() -> None:
    """Category 5 & 6: Invariant & Recovery — --resume must NOT unlink ACTIVATED mutation_approval_receipt.json."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_w1_rcpt_") as tmp:
        out_p = Path(tmp)
        rcpt_file = out_p / "mutation_approval_receipt.json"
        rcpt_payload = {
            "request_id": "req-123",
            "status": "ACTIVATED",
            "activated_generation_id": "generation-551408c0f635ead59056038ee19d",
            "operator_origin": "human_operator",
        }
        rcpt_file.write_text(json.dumps(rcpt_payload), encoding="utf-8")

        meta_resume = {
            "resume": True,
            "freeze_output_1": True,
            "auto_regenerate_track1": True,
            "regenerate": "02,03",
            "runtime_root": str(out_p),
        }
        fake_runner = MagicMock()
        fake_runner.ROOT = out_p

        with patch(
            "dual_pipeline.hitl_governance_core.mutation_authorization.validate_mutation_authorization",
            return_value={"idempotent": True, "status": "ACTIVATED"},
        ), patch(
            "dual_pipeline.canonical_pipeline.publication_bindings.canonical_publication_runtime",
            return_value=out_p,
        ):
            _, _, early = _validate_auth_and_runtime(out_p, meta_resume, {"03_graph": "x"}, fake_runner)
            assert early == {"status": "COMPLETE", "idempotent": True, "recovered": True}
            assert rcpt_file.is_file() is True

        # Conversely, explicit force_rerun=True must unlink the stale ACTIVATED receipt
        meta_force = dict(meta_resume, force_rerun=True)
        with patch(
            "dual_pipeline.hitl_governance_core.mutation_authorization.validate_mutation_authorization",
            return_value={"idempotent": False, "status": "APPLYING"},
        ), patch(
            "dual_pipeline.canonical_pipeline.publication_bindings.canonical_publication_runtime",
            return_value=out_p,
        ):
            _validate_auth_and_runtime(out_p, meta_force, {"03_graph": "x"}, fake_runner)
            assert rcpt_file.is_file() is False


def test_adversarial_concurrent_guarded_publication_reconciliation() -> None:
    """Category 3 & 6: Concurrency & crash recovery on publication_journal with allow_resume=True."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_w1_conc_") as tmp:
        rt = Path(tmp)
        directive_id = "directive-infrastructure_automation-test-10-02-2026-v1"
        call_count = 0

        def bindings_fn():
            return {"package_sha256": "abc123456789", "directive_id": directive_id}

        def publish_fn():
            nonlocal call_count
            call_count += 1
            return {"status": "COMPLETE", "generation_id": "generation-111"}

        def run_once():
            return guarded_publication(
                rt, directive_id, bindings_fn, publish_fn, allow_resume=True
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(run_once) for _ in range(4)]
            results = [f.result() for f in futures]

        assert call_count == 1
        assert all(r["generation_id"] == "generation-111" for r in results)

        # When bindings change on a new revision, guarded_publication raises PublicationReconciliationRequired
        with pytest.raises(PublicationReconciliationRequired):
            guarded_publication(
                rt,
                directive_id,
                lambda: {"package_sha256": "changed_999", "directive_id": directive_id},
                publish_fn,
                allow_resume=True,
            )
