"""
dual_pipeline/runtime/pipeline_context.py

Strongly-typed, immutable pipeline invocation and stage execution context models.
Eliminates 20-parameter call chains and filesystem-as-IPC in favor of strongly-typed
in-memory payload passing and deterministic stage checkpointing.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass(frozen=True)
class PipelineInvocationContext:
    """Immutable context holding invocation-wide configuration, credentials, and runtime parameters."""

    pipeline_name: str
    input_path: str
    output_dir: Optional[str] = None
    project: Optional[str] = None
    meeting: Optional[str] = None
    meeting_date: Optional[str] = None
    meeting_time: Optional[str] = None
    attendees: Optional[Union[str, List[Any], Dict[str, Any]]] = None
    provider_type: str = "llm"
    model: Optional[str] = None
    judge_model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    directive_id: Optional[str] = None
    mutate_graph: bool = True
    reviewer: Optional[str] = None
    rationale: Optional[str] = None
    is_notes_mode: bool = False
    max_judge_retries: int = 2
    metadata: Dict[str, Any] = field(default_factory=dict)

    def context_hash(self) -> str:
        """Return deterministic SHA-256 fingerprint of the invocation parameters."""
        canonical = {
            "pipeline_name": self.pipeline_name,
            "input_path": self.input_path,
            "project": self.project,
            "meeting": self.meeting,
            "meeting_date": self.meeting_date,
            "provider_type": self.provider_type,
            "model": self.model,
            "judge_model": self.judge_model,
        }
        raw_bytes = json.dumps(canonical, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw_bytes).hexdigest()


@dataclass(frozen=True)
class U0IngressPayload:
    """Immutable payload emitted by Stage U0 Ingress."""

    raw_input: str
    transcript_text: str
    input_hash: str
    is_file: bool
    source_file_hash: Optional[str] = None
    u0_context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class L1CognitionPayload:
    """Immutable payload emitted by Stage L1 Forensic Cognition."""

    forensic_transcript: str
    dialogue_text: str
    speaker_map: Dict[str, str] = field(default_factory=dict)
    alr_records: List[Dict[str, Any]] = field(default_factory=list)
    confidence_score: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class L2SynthesisPayload:
    """Immutable payload emitted by Stage L2 Synthesis."""

    forensic_transcript: str
    executive_recap: str
    graph_projection_json: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class XExitGatesPayload:
    """Immutable payload emitted by Stages X1-X3 Exit Gates."""

    passed: bool
    qa_report: Dict[str, Any] = field(default_factory=dict)
    judge_report: Dict[str, Any] = field(default_factory=dict)
    final_decision: Dict[str, Any] = field(default_factory=dict)
    forensic_transcript: str = ""
    executive_recap: str = ""
    graph_projection_json: str = ""


@dataclass(frozen=True)
class EvalsPayload:
    """Immutable payload emitted by retrieval evals and terminal gatekeepers."""

    terminal_outcome: str
    overall_status: str
    receipt_digest: str
    active_generation: Optional[str] = None
    active_graph_sha256: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageCheckpoint:
    """Checkpoint metadata and payload serialization for stage resumption."""

    stage_name: str
    stage_digest: str
    payload_dict: Dict[str, Any]
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "stage_digest": self.stage_digest,
            "payload_dict": self.payload_dict,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StageCheckpoint:
        return cls(
            stage_name=data["stage_name"],
            stage_digest=data["stage_digest"],
            payload_dict=data["payload_dict"],
            created_at=data.get("created_at", time.time()),
        )


@dataclass(frozen=True)
class ContentAddressedDependencyManifest:
    """
    Content-addressed dependency manifest for stage checkpoint validation.
    Guarantees checkpoint reuse occurs ONLY when all relevant inputs, speaker maps,
    model revisions, configurations, and collateral fingerprints match exactly.
    """

    stage_name: str
    source_digest: str
    speaker_digest: str
    config_digest: str
    model_id: Optional[str] = None
    prompt_digest: Optional[str] = None
    collateral_digest: Optional[str] = None
    metadata_digest: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def manifest_digest(self) -> str:
        canonical = {
            "stage_name": self.stage_name,
            "source_digest": self.source_digest,
            "speaker_digest": self.speaker_digest,
            "config_digest": self.config_digest,
            "model_id": self.model_id,
            "prompt_digest": self.prompt_digest,
            "collateral_digest": self.collateral_digest,
            "metadata_digest": self.metadata_digest,
        }
        raw_bytes = json.dumps(canonical, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw_bytes).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "source_digest": self.source_digest,
            "speaker_digest": self.speaker_digest,
            "config_digest": self.config_digest,
            "model_id": self.model_id,
            "prompt_digest": self.prompt_digest,
            "collateral_digest": self.collateral_digest,
            "metadata_digest": self.metadata_digest,
            "created_at": self.created_at,
            "manifest_digest": self.manifest_digest(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ContentAddressedDependencyManifest:
        return cls(
            stage_name=data["stage_name"],
            source_digest=data.get("source_digest", ""),
            speaker_digest=data.get("speaker_digest", ""),
            config_digest=data.get("config_digest", ""),
            model_id=data.get("model_id"),
            prompt_digest=data.get("prompt_digest"),
            collateral_digest=data.get("collateral_digest"),
            metadata_digest=data.get("metadata_digest"),
            created_at=data.get("created_at", time.time()),
        )


def _load_drop_log_data(metadata: Dict[str, Any]) -> Optional[Any]:
    drop_log_data = metadata.get("collateral_drop_log")
    if drop_log_data:
        return drop_log_data
    out_dir = metadata.get("output_dir") or metadata.get("run_dir")
    if not out_dir:
        return None
    dl_file = Path(out_dir) / "collateral_drop_log.json"
    if not dl_file.is_file():
        return None
    try:
        return json.loads(dl_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_collateral_digest(metadata: Dict[str, Any]) -> Optional[str]:
    existing = metadata.get("collateral_digest")
    if existing:
        return str(existing)
    collat = (
        metadata.get("attachments")
        or metadata.get("attachment")
        or metadata.get("collateral_manifest")
        or metadata.get("referenced_artifacts")
    )
    collat_items = []
    if collat:
        collat_items.append(collat)
    drop_log_data = _load_drop_log_data(metadata)
    if drop_log_data:
        collat_items.append(drop_log_data)
    if not collat_items:
        return None
    digest = hashlib.sha256(json.dumps(collat_items, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    metadata["collateral_digest"] = digest
    return digest


def compute_stage_manifest(
    stage_name: str,
    source_text: str,
    metadata: Dict[str, Any],
    model_id: Optional[str] = None,
    prompt_text: Optional[str] = None,
) -> ContentAddressedDependencyManifest:
    """Computes a deterministic content-addressed dependency manifest for a stage."""
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest() if source_text else ""
    spk_obj = {
        "speaker_map": metadata.get("speaker_map") or {},
        "attendees": metadata.get("attendees") or {},
        "roster": metadata.get("roster") or [],
        "identity_corrections": metadata.get("identity_corrections") or [],
    }
    speaker_digest = hashlib.sha256(json.dumps(spk_obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    cfg_raw = json.dumps(
        {
            "project": metadata.get("project"),
            "meeting": metadata.get("meeting"),
            "date": metadata.get("meeting_date"),
        },
        sort_keys=True,
    )
    config_digest = hashlib.sha256(cfg_raw.encode("utf-8")).hexdigest()
    prompt_digest = (
        hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else None
    )
    collateral_digest = _resolve_collateral_digest(metadata)
    _ephemeral_meta_keys = {
        "state_machine",
        "recorder",
        "circuit_breaker",
        "gate_f_certified",
        "child_dispatch_certified",
        "judge_clarifications",
        "hitl_resolutions",
        "subject",
        "subject_sha256",
        "context_sha256",
        "prior_forensic_candidate",
        "prior_recap_candidate",
        "prior_graph_candidate",
        "output_1_memoized",
        "output_2_memoized",
        "output_3_memoized",
    }
    meta_digest = hashlib.sha256(
        json.dumps(
            {k: str(v) for k, v in metadata.items() if k not in _ephemeral_meta_keys},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    resolved_model = model_id or metadata.get("model") or metadata.get("model_id")
    return ContentAddressedDependencyManifest(
        stage_name=stage_name,
        source_digest=source_digest,
        speaker_digest=speaker_digest,
        config_digest=config_digest,
        model_id=resolved_model,
        prompt_digest=prompt_digest,
        collateral_digest=collateral_digest,
        metadata_digest=meta_digest,
    )


def is_checkpoint_valid(
    ckpt_file: Optional[Path],
    expected_manifest: ContentAddressedDependencyManifest,
    strict_manifest: bool = False,
) -> bool:
    """Verifies that a checkpoint file exists, is non-empty, and has an identical manifest digest."""
    if not ckpt_file or not ckpt_file.exists() or ckpt_file.stat().st_size <= 500:
        return False
    manifest_file = ckpt_file.with_suffix(ckpt_file.suffix + ".manifest.json")
    if not manifest_file.exists():
        manifest_file = ckpt_file.parent / f"{ckpt_file.stem}.manifest.json"
    if not manifest_file.exists():
        return not strict_manifest
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        saved_manifest = ContentAddressedDependencyManifest.from_dict(data)
        saved_digest = saved_manifest.manifest_digest()
        if "manifest_digest" in data and data["manifest_digest"] != saved_digest:
            return False
        if not strict_manifest:
            return (
                saved_manifest.stage_name == expected_manifest.stage_name
                and saved_manifest.source_digest == expected_manifest.source_digest
                and saved_manifest.speaker_digest == expected_manifest.speaker_digest
            )
        return saved_digest == expected_manifest.manifest_digest()
    except Exception:
        return False


def save_stage_checkpoint(
    ckpt_file: Path,
    content: str,
    manifest: ContentAddressedDependencyManifest,
) -> None:
    """Persists stage checkpoint file atomically alongside its content-addressed dependency manifest."""
    ckpt_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file = ckpt_file.with_suffix(ckpt_file.suffix + ".manifest.json")

    tmp_fd1, tmp_str1 = tempfile.mkstemp(
        prefix=f".tmp_{ckpt_file.name}_", suffix=".tmp", dir=ckpt_file.parent
    )
    tmp_p1 = Path(tmp_str1)
    try:
        with os.fdopen(tmp_fd1, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        tmp_fd2, tmp_str2 = tempfile.mkstemp(
            prefix=f".tmp_{manifest_file.name}_", suffix=".tmp", dir=ckpt_file.parent
        )
        tmp_p2 = Path(tmp_str2)
        try:
            with os.fdopen(tmp_fd2, "w", encoding="utf-8") as f:
                f.write(json.dumps(manifest.to_dict(), indent=2))
                f.flush()
                os.fsync(f.fileno())
            tmp_p1.replace(ckpt_file)
            tmp_p2.replace(manifest_file)
        finally:
            tmp_p2.unlink(missing_ok=True)
    finally:
        tmp_p1.unlink(missing_ok=True)


def invalidate_stage_checkpoint(ckpt_file: Path) -> None:
    """Removes a stale or invalid checkpoint and its associated dependency manifest."""
    if ckpt_file.exists():
        try:
            ckpt_file.unlink()
        except OSError:
            pass
    manifest_file = ckpt_file.with_suffix(ckpt_file.suffix + ".manifest.json")
    if manifest_file.exists():
        try:
            manifest_file.unlink()
        except OSError:
            pass
