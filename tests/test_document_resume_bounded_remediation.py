"""Bounded document repair must keep source/config binding intact."""

import pytest

from dual_pipeline.canonical_pipeline import document_resume
from dual_pipeline.canonical_pipeline.document_resume_binding import document_synthesis_metadata
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality


class _Checkpoint:
    saved: list[tuple[str, dict]] = []

    def __init__(self, *_args, **_kwargs):
        self.saved = []

    def bind(self, _binding, *, force=False):
        return {"reason": "NEW_CHECKPOINT"}

    def get_stage(self, _stage):
        return None

    def save_stage(self, stage, payload):
        self.saved.append((stage, payload))


def _binding(kwargs):
    meta = kwargs["metadata"]
    repaired = bool(meta.get("remediation_directive"))
    return {
        "schema_version": "document-resume-binding-1",
        "reusable": False,
        "reuse_blockers": ["MODEL_REVISION_UNBOUND"],
        "components": {
            "source": meta.get("source_marker", "original"),
            "metadata_sha256": "stable",
            "prompts": {"system": "stable", "document_user_content": "repair" if repaired else "base"},
        },
        "binding_sha256": "repair" if repaired else "base",
    }


def test_bounded_repair_returns_result_without_caching(monkeypatch):
    checkpoint = _Checkpoint()
    monkeypatch.setattr(document_resume, "PipelineCheckpointManager", lambda *_a, **_kw: checkpoint)
    monkeypatch.setattr(document_resume, "_binding", _binding)
    meta = {"_document_checkpoint_path": "/unused", "run_id": "interactive-test"}
    result = (2, object(), object())

    def synthesize(**kwargs):
        kwargs["metadata"]["remediation_directive"] = "Repair the failed graph gate"
        return result

    actual = document_resume.run_synthesis_with_resume(
        synthesize=synthesize,
        metadata=meta,
        modality=CanonicalModality.DOCUMENT,
        source_text="unchanged source",
        max_judge_retries=2,
    )

    assert actual is result
    assert meta["document_resume"]["reason"] == "IN_RUN_REMEDIATION_NOT_REUSABLE"
    assert checkpoint.saved == [("SYNTHESIS_NOT_REUSABLE", {
        "reason": "IN_RUN_REMEDIATION_NOT_REUSABLE", "run_id": "interactive-test",
    })]


def test_source_change_still_blocks_during_bounded_repair(monkeypatch):
    monkeypatch.setattr(document_resume, "PipelineCheckpointManager", _Checkpoint)
    monkeypatch.setattr(document_resume, "_binding", _binding)
    meta = {"_document_checkpoint_path": "/unused"}

    def synthesize(**kwargs):
        kwargs["metadata"]["remediation_directive"] = "Repair the failed graph gate"
        kwargs["metadata"]["source_marker"] = "changed"
        return (2, object(), object())

    with pytest.raises(ValueError, match="SYNTHESIS_BINDING_CHANGED_DURING_RUN"):
        document_resume.run_synthesis_with_resume(
            synthesize=synthesize,
            metadata=meta,
            modality=CanonicalModality.DOCUMENT,
            source_text="unchanged source",
            max_judge_retries=2,
        )


def test_repair_directive_is_not_semantic_document_metadata():
    base = {"project": "project_nexus", "title": "Weekly briefing"}
    amended = {**base, "remediation_directive": "Correct the graph gate"}
    assert document_synthesis_metadata(base) == document_synthesis_metadata(amended)
