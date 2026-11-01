"""Source-bound connector from retrieved sidecar records to decision inputs.

The connector consumes record identifiers already returned by retrieval and
keeps the handoff explicit:

* each sidecar record is checked against its sealed source-bound artifact;
* graph assertion/evidence pairs are resolved through the read-only runtime
  source adapter; and
* the shared reference monitor filters every source before a decision
  projection can receive its source IDs or derived evidence.

It is read-only. It does not run retrieval, choose a runtime, alter a graph,
or require a human disposition.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from storage.connection import create_sqlite_conn

from ._validators import require_sha256, require_text
from .authorization import AuthorizationContext
from .core import artifact_digest, digest, seal_artifact, sha256_file
from .decision_use_cases import evaluate_use_case, validate_use_case
from .exceptions import DualPipelineValueError
from .runtime_decision_adapter import (
    RuntimeDecisionSourceAdapter,
    RuntimeDecisionSourceError,
)

SHA256_DIGEST_LENGTH = 64
SOURCE_SELECTION_ARTIFACT = "decision_intelligence_retrieval_source_selection"
USE_CASE_PROJECTION_ARTIFACT = "decision_intelligence_retrieval_use_case_projection"
RETRIEVAL_LANES = frozenset({"approved", "candidate", "external_public"})
ASSERTION_EXTENSION_KEY = "com.partner_advisory.nee.graph-assertion-ids"
EVIDENCE_EXTENSION_KEY = "com.partner_advisory.nee.graph-evidence-ids"


class RetrievalDIBridgeError(DualPipelineValueError):
    """Raised when a retrieval result cannot safely enter DI."""


def _text(value: object, *, field: str) -> str:
    return require_text(value, field=field, error_type=RetrievalDIBridgeError)


def _sha256(value: object, *, field: str) -> str:
    return require_sha256(value, field=field, error_type=RetrievalDIBridgeError)


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalDIBridgeError(f"{field} must be an object")
    return dict(value)


def _unique_texts(
    value: Iterable[str], *, field: str, allow_empty: bool = False
) -> list[str]:
    if isinstance(value, (str, bytes)):
        raise RetrievalDIBridgeError(f"{field} must be an iterable of text")
    try:
        raw = list(value)
    except TypeError as exc:
        raise RetrievalDIBridgeError(f"{field} must be an iterable of text") from exc
    result = [_text(item, field=field) for item in raw]
    if not result and not allow_empty:
        raise RetrievalDIBridgeError(f"{field} must be nonempty")
    if len(set(result)) != len(result):
        raise RetrievalDIBridgeError(f"{field} must not contain duplicates")
    return sorted(result)


def _sealed(value: object, *, artifact_type: str) -> dict[str, Any]:
    artifact = _mapping(value, field=artifact_type)
    seal = artifact.get("canonical_digest")
    if (
        not isinstance(seal, Mapping)
        or seal.get("algorithm") != "sha256"
        or seal.get("scope") != "artifact_without_canonical_digest"
        or seal.get("value") != artifact_digest(artifact)
    ):
        raise RetrievalDIBridgeError(f"{artifact_type} canonical digest is invalid")
    if artifact.get("artifact_type") != artifact_type:
        raise RetrievalDIBridgeError(f"expected {artifact_type} artifact")
    return artifact


def _sidecar_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _record_artifact(
    *,
    record_id: object,
    lane: object,
    record_type: object,
    provenance_kind: object,
    retrieval_text_hash: object,
    artifact_json: object,
) -> dict[str, Any]:
    """Validate just enough sidecar state to bind source mappings fail-closed."""

    normalized_id = _text(record_id, field="sidecar record_id")
    normalized_lane = _text(lane, field="sidecar lane")
    if normalized_lane not in RETRIEVAL_LANES:
        raise RetrievalDIBridgeError("sidecar record has an unsupported lane")
    if _text(provenance_kind, field="sidecar provenance_kind") != "source_bound":
        raise RetrievalDIBridgeError("sidecar record is not source-bound")
    if not isinstance(artifact_json, str):
        raise RetrievalDIBridgeError("sidecar artifact_json must be text")
    try:
        artifact = json.loads(artifact_json)
    except json.JSONDecodeError as exc:
        raise RetrievalDIBridgeError("sidecar artifact_json is not valid JSON") from exc
    artifact = _mapping(artifact, field="sidecar retrieval artifact")
    seal = artifact.get("canonical_digest")
    if (
        not isinstance(seal, Mapping)
        or seal.get("algorithm") != "sha256"
        or seal.get("scope") != "artifact_without_canonical_digest"
        or seal.get("value") != artifact_digest(artifact)
    ):
        raise RetrievalDIBridgeError("sidecar retrieval artifact canonical digest is invalid")
    normalized_record_type = _text(record_type, field="sidecar record_type")
    if (
        artifact.get("record_id") != normalized_id
        or artifact.get("lane") != normalized_lane
        or artifact.get("record_type") != normalized_record_type
    ):
        raise RetrievalDIBridgeError("sidecar row differs from its sealed retrieval artifact")
    if artifact.get("provenance_kind") != "source_bound":
        raise RetrievalDIBridgeError("sealed retrieval artifact is not source-bound")
    retrieval_text = _text(artifact.get("retrieval_text"), field="retrieval artifact text")
    expected_text_hash = hashlib.sha256(retrieval_text.encode("utf-8")).hexdigest()
    if (
        artifact.get("retrieval_text_hash") != expected_text_hash
        or retrieval_text_hash != expected_text_hash
    ):
        raise RetrievalDIBridgeError("retrieval artifact text hash is invalid")
    extensions = _mapping(artifact.get("extensions"), field="retrieval artifact extensions")
    assertion_ids = _unique_texts(
        extensions.get(ASSERTION_EXTENSION_KEY, ()),
        field="retrieval artifact graph assertion IDs",
    )
    evidence_ids = _unique_texts(
        extensions.get(EVIDENCE_EXTENSION_KEY, ()),
        field="retrieval artifact graph evidence IDs",
    )
    return {
        "record_id": normalized_id,
        "lane": normalized_lane,
        "record_type": normalized_record_type,
        "graph_snapshot": _text(artifact.get("graph_snapshot"), field="record graph_snapshot"),
        "graph_assertion_ids": assertion_ids,
        "evidence_ids": evidence_ids,
        "artifact_digest": _sha256(seal["value"], field="retrieval artifact digest"),
    }


class SidecarRetrievalDecisionBridge:
    """Map selected sidecar records into monitored decision inputs.

    The caller supplies record IDs from a retrieval result. This class does
    not rank them; it verifies that they can cross the source, ACL, and
    temporal boundary.
    """

    def __init__(self, *, graph_path: Path, sidecar_path: Path):
        self.graph_path = graph_path.expanduser().resolve()
        self.sidecar_path = sidecar_path.expanduser().resolve()
        if not self.graph_path.is_file() or not self.sidecar_path.is_file():
            raise RetrievalDIBridgeError("bridge graph and sidecar paths must exist")
        try:
            self.source_adapter = RuntimeDecisionSourceAdapter(self.graph_path)
        except RuntimeDecisionSourceError as exc:
            raise RetrievalDIBridgeError(f"runtime source adapter failed: {exc}") from exc
        self.graph_sha256 = sha256_file(self.graph_path)
        self.sidecar_sha256 = sha256_file(self.sidecar_path)
        self._records, self._invalid_record_ids = self._load_sidecar_records()

    def _load_sidecar_records(self) -> tuple[dict[str, dict[str, Any]], set[str]]:
        connection = None
        try:
            connection = create_sqlite_conn(Path(self.sidecar_path), readonly=True)
            rows = connection.execute(
                """
                SELECT record_id, lane, record_type, provenance_kind, retrieval_text_hash, artifact_json
                  FROM retrieval_record
                 ORDER BY record_id
                """
            ).fetchall()
        except Exception as exc:
            raise RetrievalDIBridgeError(
                "sidecar does not expose the governed retrieval_record schema"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
        records: dict[str, dict[str, Any]] = {}
        invalid: set[str] = set()
        for row in rows:
            record_id = row["record_id"]
            try:
                record = _record_artifact(
                    record_id=record_id,
                    lane=row["lane"],
                    record_type=row["record_type"],
                    provenance_kind=row["provenance_kind"],
                    retrieval_text_hash=row["retrieval_text_hash"],
                    artifact_json=row["artifact_json"],
                )
            except RetrievalDIBridgeError:
                if isinstance(record_id, str) and record_id.strip():
                    invalid.add(record_id.strip())
                continue
            if record["record_id"] in records:
                raise RetrievalDIBridgeError("sidecar contains duplicate record IDs")
            records[record["record_id"]] = record
        return records, invalid

    def binding(self) -> dict[str, Any]:
        """Return immutable connector inputs without selecting a pair."""

        return {
            "graph_path": str(self.graph_path),
            "graph_sha256": self.graph_sha256,
            "sidecar_path": str(self.sidecar_path),
            "sidecar_sha256": self.sidecar_sha256,
            "runtime_source_adapter": self.source_adapter.binding(),
            "valid_sidecar_record_count": len(self._records),
            "invalid_sidecar_record_count": len(self._invalid_record_ids),
            "provider_calls": 0,
            "graph_mutations": 0,
            "release_activation": False,
        }

    def select_retrieval_records(
        self,
        *,
        record_ids: Iterable[str],
        audience: str,
        authorization_context: AuthorizationContext,
        known_at: str,
    ) -> dict[str, Any]:
        """Produce a source-safe selection over records supplied by retrieval."""

        selected_ids = _unique_texts(
            record_ids, field="selected retrieval record IDs", allow_empty=True
        )
        unknown = sorted(
            set(selected_ids) - set(self._records) - self._invalid_record_ids
        )
        if unknown:
            raise RetrievalDIBridgeError("retrieval selection names an unknown sidecar record")
        exposed_records: list[dict[str, Any]] = []
        unmapped_count = 0
        monitor_filtered_count = 0
        invalid_count = 0
        for record_id in selected_ids:
            if record_id in self._invalid_record_ids:
                invalid_count += 1
                continue
            record = self._records[record_id]
            source_result = self.source_adapter.visible_source_assertions_for_retrieval_record(
                graph_assertion_ids=record["graph_assertion_ids"],
                evidence_ids=record["evidence_ids"],
                audience=audience,
                authorization_context=authorization_context,
                known_at=known_at,
            )
            if source_result["source_mapping_complete"] is not True:
                unmapped_count += 1
                continue
            if (
                source_result["matched_source_assertion_count"] == 0
                or source_result["visible_source_assertion_count"]
                != source_result["matched_source_assertion_count"]
            ):
                monitor_filtered_count += 1
                continue
            sources = source_result["source_assertions"]
            source_assertion_ids = [item["source_assertion_id"] for item in sources]
            labels = self.source_adapter.reference_monitor.derived_labels(
                source_assertion_ids
            )
            exposed_records.append(
                {
                    "record_id": record_id,
                    "lane": record["lane"],
                    "record_type": record["record_type"],
                    "graph_snapshot": record["graph_snapshot"],
                    "sidecar_record_artifact_digest": record["artifact_digest"],
                    "source_assertion_ids": source_assertion_ids,
                    "source_envelope_digests": sorted(
                        item["source_envelope_digest"] for item in sources
                    ),
                    "classification": labels["classification"],
                    "audiences": labels["audiences"],
                }
            )
        source_ids = sorted(
            {
                source_id
                for record in exposed_records
                for source_id in record["source_assertion_ids"]
            }
        )
        snapshots = {item["graph_snapshot"] for item in exposed_records}
        if len(snapshots) > 1:
            raise RetrievalDIBridgeError(
                "selected sidecar records bind more than one graph snapshot"
            )
        return seal_artifact(
            {
                "schema_version": "1.0",
                "artifact_type": SOURCE_SELECTION_ARTIFACT,
                "bridge_binding": self.binding(),
                "audience": _text(audience, field="audience"),
                "known_at": _text(known_at, field="known_at"),
                "requested_record_ids_digest": digest(selected_ids),
                "requested_record_count": len(selected_ids),
                "visible_records": sorted(
                    exposed_records, key=lambda item: item["record_id"]
                ),
                "visible_source_assertion_ids": source_ids,
                "visible_graph_snapshot": next(iter(snapshots), None),
                "unmapped_record_count": unmapped_count,
                "monitor_filtered_record_count": monitor_filtered_count,
                "invalid_record_count": invalid_count,
                "monitor_filtered_before_exposure": True,
                "provider_calls": 0,
                "graph_mutations": 0,
                "release_activation": False,
            }
        )

    def project_use_case(
        self,
        *,
        use_case: Mapping[str, Any],
        source_selection: Mapping[str, Any],
        as_of: str,
        known_at: str,
        authorization_context: AuthorizationContext,
    ) -> dict[str, Any]:
        """Project one decision use case over a monitored retrieval selection."""

        selection = verify_source_selection(source_selection)
        if selection.get("bridge_binding") != self.binding():
            raise RetrievalDIBridgeError("source selection is bound to another graph or sidecar")
        item = validate_use_case(use_case)
        if selection.get("audience") != item["audience"]:
            raise RetrievalDIBridgeError("source selection audience differs from use case")
        if selection.get("known_at") != known_at:
            raise RetrievalDIBridgeError("source selection known_at differs from use case")
        source_ids = selection.get("visible_source_assertion_ids")
        if not isinstance(source_ids, list):
            raise RetrievalDIBridgeError("source selection visible source IDs are invalid")
        try:
            inputs = self.source_adapter.inputs_for_use_case(
                use_case=item,
                authorization_context=authorization_context,
                as_of=as_of,
                known_at=known_at,
                allowed_source_assertion_ids=source_ids,
            )
        except RuntimeDecisionSourceError as exc:
            raise RetrievalDIBridgeError(f"retrieval DI input projection failed: {exc}") from exc
        evaluation = evaluate_use_case(
            use_case=item,
            as_of=as_of,
            known_at=known_at,
            authorization_context=authorization_context,
            reference_monitor=self.source_adapter.reference_monitor,
            observations=inputs["observations"],
            evidence_packets=inputs["evidence_packets"],
        )
        displayed_source_ids = sorted(
            {
                source_id
                for item in [*inputs["observations"], *inputs["evidence_packets"]]
                for source_id in item.get("source_assertion_ids", [])
            }
        )
        if not set(displayed_source_ids).issubset(set(source_ids)):
            raise RetrievalDIBridgeError(
                "decision projection exposed a source outside retrieval selection"
            )
        return seal_artifact(
            {
                "schema_version": "1.0",
                "artifact_type": USE_CASE_PROJECTION_ARTIFACT,
                "bridge_binding": self.binding(),
                "source_selection_digest": selection["canonical_digest"]["value"],
                "use_case_id": item["use_case_id"],
                "question_digest": digest({"question": item["question"]}),
                "as_of": _text(as_of, field="as_of"),
                "known_at": _text(known_at, field="known_at"),
                "state": evaluation["state"],
                "answer_posture": evaluation["answer_posture"],
                "gaps": list(evaluation["decision_context"]["gaps"]),
                "displayed_source_assertion_ids": displayed_source_ids,
                "source_selection_consistent": True,
                "candidate_outline": {
                    "question": evaluation["question"],
                    "sections": list(evaluation["candidate_answer_sections"]),
                    "posture": evaluation["answer_posture"],
                },
                "provider_calls": 0,
                "graph_mutations": 0,
                "release_activation": False,
            }
        )


def verify_source_selection(value: object) -> dict[str, Any]:
    """Verify a sealed selection remains non-authorizing and source-safe."""

    selection = _sealed(value, artifact_type=SOURCE_SELECTION_ARTIFACT)
    if (
        selection.get("provider_calls") != 0
        or selection.get("graph_mutations") != 0
        or selection.get("release_activation") is not False
        or selection.get("monitor_filtered_before_exposure") is not True
    ):
        raise RetrievalDIBridgeError("source selection exceeds bridge authority")
    records = selection.get("visible_records")
    if not isinstance(records, list) or any(not isinstance(item, Mapping) for item in records):
        raise RetrievalDIBridgeError("source selection visible records are invalid")
    source_ids = _unique_texts(
        selection.get("visible_source_assertion_ids", ()),
        field="visible_source_assertion_ids",
        allow_empty=True,
    )
    exposed_ids = sorted(
        {
            source_id
            for record in records
            for source_id in _unique_texts(
                record.get("source_assertion_ids", ()),
                field="visible record source_assertion_ids",
            )
        }
    )
    if source_ids != exposed_ids:
        raise RetrievalDIBridgeError(
            "source selection IDs do not exactly match visible retrieval records"
        )
    return selection


def verify_use_case_projection(value: object) -> dict[str, Any]:
    """Verify a decision projection remains source-bound and non-mutating."""

    projection = _sealed(value, artifact_type=USE_CASE_PROJECTION_ARTIFACT)
    if (
        projection.get("provider_calls") != 0
        or projection.get("graph_mutations") != 0
        or projection.get("release_activation") is not False
        or projection.get("answer_posture") not in {"CANDIDATE_ONLY", "NO_ANSWER"}
        or projection.get("source_selection_consistent") is not True
    ):
        raise RetrievalDIBridgeError("use-case projection exceeds bridge authority")
    outline = _mapping(projection.get("candidate_outline"), field="candidate_outline")
    if outline.get("posture") != projection.get("answer_posture"):
        raise RetrievalDIBridgeError("candidate outline posture differs from use-case posture")
    _text(outline.get("question"), field="candidate_outline question")
    _unique_texts(
        outline.get("sections", ()),
        field="candidate_outline sections",
        allow_empty=projection.get("answer_posture") == "NO_ANSWER",
    )
    return projection


__all__ = [
    "SOURCE_SELECTION_ARTIFACT",
    "SidecarRetrievalDecisionBridge",
    "USE_CASE_PROJECTION_ARTIFACT",
    "RetrievalDIBridgeError",
    "verify_source_selection",
    "verify_use_case_projection",
]

