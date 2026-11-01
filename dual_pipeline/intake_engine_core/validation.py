"""Validation and binding helpers for IntakeEngine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import digest, read_json, sha256_file
from dual_pipeline.index import CLASSIFICATION_RANK
from dual_pipeline.intake_core.comparison import (
    _intake_cache_context,
    _valid_cached_comparison,
)
from dual_pipeline.semantic_cache import SemanticCache


def prepare_semantic_cache(
    engine: Any,
    bundle: dict[str, Any],
) -> tuple[
    SemanticCache,
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    cache = SemanticCache(engine.runtime_root)
    if (
        engine.semantic_cache_mode is not None
        and engine.semantic_cache_mode != cache.mode
    ):
        raise ValueError(
            "SEMANTIC_CACHE_MODE_MISMATCH: requested "
            f"{engine.semantic_cache_mode}, runtime is {cache.mode}; "
            "use semantic-cache-config before ingestion"
        )
    if engine.retriever is not None:
        cache.bind_embedding_backend(engine.retriever.backend)
    else:
        cache.last_binding_result = {
            "status": "SKIPPED_NO_RETRIEVER",
            "purged_entry_count": 0,
        }
    bindings: dict[str, dict[str, Any]] = {}
    cached: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    if cache.mode == "off" or cache.health_reason != "CACHE_READY":
        observations.append(
            {
                "candidate_id": None,
                "mode": cache.mode,
                "hit": False,
                "reason": cache.health_reason,
                "embedding_binding": cache.last_binding_result,
            }
        )
        return cache, bindings, cached, observations
    if engine.retriever is None:
        observations.append(
            {
                "candidate_id": None,
                "mode": cache.mode,
                "hit": False,
                "reason": "CACHE_SKIPPED_NO_RETRIEVER",
                "embedding_binding": cache.last_binding_result,
            }
        )
        return cache, bindings, cached, observations
    if engine.active_pair_binding is None:
        observations.append(
            {
                "candidate_id": None,
                "mode": cache.mode,
                "hit": False,
                "reason": "CACHE_SKIPPED_NO_ACTIVE_PAIR",
                "embedding_binding": cache.last_binding_result,
            }
        )
        return cache, bindings, cached, observations
    if engine.active_pair_binding.get("graph_sha256") != sha256_file(engine.graph_path):
        observations.append(
            {
                "candidate_id": None,
                "mode": cache.mode,
                "hit": False,
                "reason": "CACHE_SKIPPED_ACTIVE_PAIR_GRAPH_MISMATCH",
                "embedding_binding": cache.last_binding_result,
            }
        )
        return cache, bindings, cached, observations
    for candidate in bundle["candidates"]:
        candidate_id = candidate["candidate_id"]
        context = _intake_cache_context(
            candidate=candidate,
            active_pair=engine.active_pair_binding,
            retriever=engine.retriever,
            retrieval_audiences=engine.retrieval_audiences,
            authorization_context=engine.authorization_context,
        )
        try:
            encoded = engine.retriever.backend.encode(candidate["normalized_text"])
            dense = encoded.get("dense")
            if not isinstance(dense, list):
                raise ValueError("embedding backend returned no dense intent vector")
            vector = [float(value) for value in dense]
            lookup = cache.lookup(context=context, intent_vector=vector)
            comparison = (
                _valid_cached_comparison(
                    lookup.output.get("comparison"),
                    candidate=candidate,
                    retriever=engine.retriever,
                )
                if lookup.hit and isinstance(lookup.output, dict)
                else None
            )
            if lookup.hit and comparison is None:
                lookup_reason = "CACHE_MISS_OUTPUT_BINDING_INVALID"
            else:
                lookup_reason = lookup.reason
            bindings[candidate_id] = {
                "context": context,
                "intent_digest": digest(candidate["normalized_text"]),
                "intent_vector": vector,
                "encoded_query": encoded,
            }
            if comparison is not None:
                cached[candidate_id] = comparison
            observations.append(
                {
                    "candidate_id": candidate_id,
                    "mode": cache.mode,
                    "hit": comparison is not None,
                    "reason": lookup_reason,
                    "entry_id": lookup.entry_id,
                    "similarity": lookup.similarity,
                    "context_digest": digest(cache.compatibility(context)),
                    "embedding_binding": cache.last_binding_result,
                }
            )
        except (OSError, TypeError, ValueError) as exc:
            observations.append(
                {
                    "candidate_id": candidate_id,
                    "mode": cache.mode,
                    "hit": False,
                    "reason": f"CACHE_EMBEDDING_ERROR:{type(exc).__name__}",
                    "embedding_binding": cache.last_binding_result,
                }
            )
    return cache, bindings, cached, observations


def require_bundle_access(engine: Any, bundle: dict[str, Any]) -> None:
    if engine.authorization_context is None:
        raise PermissionError("production intake requires an authorization context")
    clearance = engine.authorization_context.classification_clearance
    authorized_audiences = set(engine.authorization_context.audiences)
    denied = []
    for candidate in bundle["candidates"]:
        candidate_classification = candidate["classification"]
        candidate_audiences = set(candidate["audiences"])
        if (
            candidate_classification not in CLASSIFICATION_RANK
            or CLASSIFICATION_RANK[candidate_classification] > CLASSIFICATION_RANK[clearance]
            or not (candidate_audiences & authorized_audiences)
        ):
            denied.append(candidate["candidate_id"])
    if denied:
        raise PermissionError(
            "production intake authorization clearance or audiences do not admit "
            f"the source candidates: {denied[:5]}"
        )


def production_binding(engine: Any, graph_hash: str) -> dict[str, Any]:
    if engine.retriever is not None and engine.retriever.backend.production_qualified:
        backend = engine.retriever.backend
        if (
            engine.authorization_context is None
            or engine.authorization_context.approved_model_manifest_digest
            != backend.model_manifest_digest
        ):
            raise PermissionError(
                "production intake authorization is not bound to the BGE-M3 model manifest"
            )
        validation = getattr(engine.retriever, "validation", None)
        metadata = validation.get("metadata") if isinstance(validation, dict) else None
        if not isinstance(metadata, dict):
            raise RuntimeError(
                "production intake requires a validated active sidecar generation"
            )
        binding = {
            "production_qualified": True,
            "embedding_backend": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "model_or_index_generation": metadata.get("generation_id"),
        }
        if (
            metadata.get("graph_sha256") != graph_hash
            or not isinstance(binding["model_manifest_digest"], str)
            or not isinstance(binding["model_or_index_generation"], str)
        ):
            raise RuntimeError("production intake graph/model generation binding is incomplete")
    elif engine.active_pair_binding and engine.active_pair_binding.get("production_qualified"):
        pair = engine.active_pair_binding
        binding = {
            "production_qualified": True,
            "embedding_backend": pair.get("embedding_backend"),
            "model_id": pair.get("model_id"),
            "model_revision": pair.get("model_revision"),
            "model_manifest_digest": pair.get("model_manifest_digest"),
            "model_or_index_generation": pair.get("generation_id"),
        }
        if (
            pair.get("graph_sha256") != graph_hash
            or engine.authorization_context is None
            or engine.authorization_context.approved_model_manifest_digest
            != binding["model_manifest_digest"]
        ):
            raise PermissionError(
                "production review authorization or graph is not bound to the active model generation"
            )
    else:
        raise RuntimeError("production intake requires an explicit validated model generation")
    if engine.active_pair_binding is not None:
        pair_expected = {
            "embedding_backend": engine.active_pair_binding.get("embedding_backend"),
            "model_id": engine.active_pair_binding.get("model_id"),
            "model_revision": engine.active_pair_binding.get("model_revision"),
            "model_manifest_digest": engine.active_pair_binding.get("model_manifest_digest"),
            "model_or_index_generation": engine.active_pair_binding.get("generation_id"),
        }
        if any(binding[key] != value for key, value in pair_expected.items()):
            raise RuntimeError(
                "production intake retriever does not match the active model generation"
            )
    return binding


def validate_production_run(
    run_dir: Path,
    summary: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    binding_keys = (
        "production_qualified",
        "embedding_backend",
        "model_id",
        "model_revision",
        "model_manifest_digest",
        "model_or_index_generation",
    )
    if any(summary.get(key) != binding[key] for key in binding_keys):
        raise PermissionError(
            "existing intake is not bound to the active production model generation"
        )
    summary_authorization = summary.get("intake_authorization") or summary.get("authorization")
    if not isinstance(summary_authorization, dict) or not summary_authorization.get(
        "context_digest"
    ):
        raise PermissionError(
            "existing intake has no sealed enterprise-production authorization evidence"
        )
    intake_events: list[dict[str, Any]] = []
    for event_path in sorted((run_dir / "learning_events").glob("*.json")):
        event = read_json(event_path)
        validate_artifact(event)
        if event.get("event_type") == "intake_run":
            intake_events.append(event)
    if len(intake_events) != 1:
        raise RuntimeError("production intake requires one sealed source-package event")
    event = intake_events[0]
    payload = read_json(
        run_dir / "ledger_payloads" / event["payload_reference"].rsplit("/", 1)[-1]
    )
    if (
        digest(payload) != event["payload_digest"]
        or event.get("model_or_index_generation") != binding["model_or_index_generation"]
        or any(payload.get(key) != binding[key] for key in binding_keys)
        or payload.get("authorization_context_digest")
        != summary_authorization["context_digest"]
        or payload.get("intake_authorization") != summary_authorization
        or summary.get("authorization") != summary_authorization
        or summary.get("batch_admission") != payload.get("batch_admission")
    ):
        raise RuntimeError(
            "sealed production intake evidence does not match its model generation"
        )
    return payload
