"""Runtime decision source adapter class."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from ..authorization import AuthorizationContext
from ..core import digest, sha256_file
from ..reference_monitor import ReferenceMonitor, SourceAssertionRegistry
from .constants import (
    MAX_CONTEXT_RECORDS,
    RUNTIME_SOURCE_ADAPTER_ID,
    RUNTIME_SOURCE_ADAPTER_VERSION,
    RuntimeDecisionSourceError,
    _nonempty_unique_text_list,
    _text,
    _tokens,
)
from .projections import (
    _context_packet,
    _evidence_from_mapping,
    _observation_from_mapping,
    _projection_for_use_case,
    _score,
    _visible_records,
)
from .retrieval_resolution import resolve_visible_source_assertions
from .row_reader import _normalise_row, _read_rows


class RuntimeDecisionSourceAdapter:
    """Read a governed SQLite graph and expose only source-bound DI inputs."""

    def __init__(self, graph_path: Path):
        self.graph_path = graph_path.expanduser().resolve()
        if not self.graph_path.is_file():
            raise RuntimeDecisionSourceError(f"graph does not exist: {self.graph_path}")
        self.graph_sha256 = sha256_file(self.graph_path)
        skipped: dict[str, int] = {}
        records: list[dict[str, Any]] = []
        for row in _read_rows(self.graph_path):
            try:
                records.append(_normalise_row(row))
            except RuntimeDecisionSourceError as exc:
                key = str(exc)
                skipped[key] = skipped.get(key, 0) + 1
        if not records:
            raise RuntimeDecisionSourceError(
                "graph has no retrievable source assertions with complete DI lineage"
            )
        self._records = sorted(
            records, key=lambda item: item["source_assertion"]["source_assertion_id"]
        )
        self.registry = SourceAssertionRegistry(
            item["source_assertion"] for item in self._records
        )
        self.reference_monitor = ReferenceMonitor(self.registry)
        self.registry_digest = digest(
            [item["source_assertion"] for item in self._records]
        )
        self.skipped_record_counts = dict(sorted(skipped.items()))

    def binding(self) -> dict[str, Any]:
        """Return a non-authorizing, deterministic runtime adapter binding."""
        return {
            "adapter_id": RUNTIME_SOURCE_ADAPTER_ID,
            "adapter_version": RUNTIME_SOURCE_ADAPTER_VERSION,
            "graph_path": str(self.graph_path),
            "graph_sha256": self.graph_sha256,
            "source_assertion_count": len(self._records),
            "source_assertion_registry_digest": self.registry_digest,
            "skipped_record_counts": dict(self.skipped_record_counts),
            "provider_calls": 0,
            "graph_mutations": 0,
            "release_activation": False,
        }

    def visible_source_assertions_for_retrieval_record(
        self,
        *,
        graph_assertion_ids: Iterable[str],
        evidence_ids: Iterable[str],
        audience: str,
        authorization_context: AuthorizationContext,
        known_at: str,
    ) -> dict[str, Any]:
        """Resolve a sidecar record's graph references through the monitor."""
        return resolve_visible_source_assertions(
            self.reference_monitor,
            self._records,
            graph_assertion_ids=graph_assertion_ids,
            evidence_ids=evidence_ids,
            audience=audience,
            authorization_context=authorization_context,
            known_at=known_at,
        )

    def inputs_for_use_case(
        self,
        *,
        use_case: Mapping[str, Any],
        authorization_context: AuthorizationContext,
        as_of: str,
        known_at: str,
        allowed_source_assertion_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Build W3/W6 inputs for exactly one W15 use case."""
        use_case_id = _text(use_case.get("use_case_id"), field="use_case_id")
        scope_id = _text(use_case.get("scope_id"), field="use_case scope_id")
        audience = _text(use_case.get("audience"), field="use_case audience")
        question = _text(use_case.get("question"), field="use_case question")
        claim_ids = _nonempty_unique_text_list(
            use_case.get("claim_ids"), field="use_case claim_ids"
        )
        selected_records = self._records
        if allowed_source_assertion_ids is not None:
            if isinstance(allowed_source_assertion_ids, (str, bytes)):
                raise RuntimeDecisionSourceError(
                    "allowed_source_assertion_ids must be an iterable of text IDs"
                )
            raw_allowed_ids = list(allowed_source_assertion_ids)
            if any(
                not isinstance(item, str) or not item.strip() for item in raw_allowed_ids
            ):
                raise RuntimeDecisionSourceError(
                    "allowed_source_assertion_ids must contain only nonblank text IDs"
                )
            allowed_ids = {item.strip() for item in raw_allowed_ids}
            if len(allowed_ids) != len(raw_allowed_ids):
                raise RuntimeDecisionSourceError(
                    "allowed_source_assertion_ids must not contain duplicates"
                )
            known_ids = {
                item["source_assertion"]["source_assertion_id"] for item in self._records
            }
            unknown = sorted(allowed_ids - known_ids)
            if unknown:
                raise RuntimeDecisionSourceError(
                    "allowed_source_assertion_ids are not in the source registry"
                )
            selected_records = [
                item
                for item in self._records
                if item["source_assertion"]["source_assertion_id"] in allowed_ids
            ]
        query_terms = _tokens(question)
        scored = [
            (_score(record, query_terms), record)
            for record in selected_records
        ]
        context_candidates = [
            record
            for score, record in sorted(
                scored,
                key=lambda item: (
                    -item[0],
                    item[1]["source_assertion"]["source_assertion_id"],
                ),
            )
            if score > 0
        ][:MAX_CONTEXT_RECORDS]
        mapped_candidates = [
            record
            for record in selected_records
            if _projection_for_use_case(record, use_case_id=use_case_id) is not None
        ]
        candidates_by_id = {
            record["source_assertion"]["source_assertion_id"]: record
            for record in [*context_candidates, *mapped_candidates]
        }
        visible = _visible_records(
            self.reference_monitor,
            records=candidates_by_id.values(),
            audience=audience,
            authorization_context=authorization_context,
            known_at=known_at,
        )
        visible_by_id = {
            record["source_assertion"]["source_assertion_id"]: record for record in visible
        }
        mapped_visible = [
            record
            for record in visible
            if _projection_for_use_case(record, use_case_id=use_case_id) is not None
        ]
        observations: list[dict[str, Any]] = []
        evidence_packets: list[dict[str, Any]] = []
        projection_errors: list[str] = []
        for record in mapped_visible:
            try:
                projection = _projection_for_use_case(record, use_case_id=use_case_id)
                assert projection is not None
                for item in projection["observations"]:
                    observations.append(
                        _observation_from_mapping(
                            record=record,
                            use_case_id=use_case_id,
                            scope_id=scope_id,
                            observation=item,
                        )
                    )
                for item in projection["evidence"]:
                    evidence_packets.append(
                        _evidence_from_mapping(
                            self.reference_monitor,
                            record=record,
                            use_case_id=use_case_id,
                            evidence=item,
                        )
                    )
            except RuntimeDecisionSourceError as exc:
                projection_errors.append(str(exc))
        mapped_ids = {
            record["source_assertion"]["source_assertion_id"] for record in mapped_visible
        }
        context_visible = [
            record
            for record in context_candidates
            if record["source_assertion"]["source_assertion_id"] in visible_by_id
            and record["source_assertion"]["source_assertion_id"] not in mapped_ids
        ]
        for record in context_visible:
            for claim_id in claim_ids:
                evidence_packets.append(
                    _context_packet(
                        self.reference_monitor,
                        record=record,
                        claim_id=claim_id,
                        use_case_id=use_case_id,
                    )
                )
        observation_ids = [item["observation_id"] for item in observations]
        evidence_ids = [item["evidence_id"] for item in evidence_packets]
        if len(set(observation_ids)) != len(observation_ids) or len(set(evidence_ids)) != len(
            evidence_ids
        ):
            raise RuntimeDecisionSourceError("runtime projection generated duplicate IDs")
        return {
            "observations": sorted(observations, key=lambda item: item["observation_id"]),
            "evidence_packets": sorted(evidence_packets, key=lambda item: item["evidence_id"]),
            "selection": {
                "use_case_id": use_case_id,
                "question_digest": digest({"question": question}),
                "lexical_context_candidate_count": len(context_candidates),
                "visible_context_source_count": len(context_visible),
                "declared_projection_candidate_count": len(mapped_candidates),
                "visible_declared_projection_count": len(mapped_visible),
                "projection_error_count": len(projection_errors),
                "projection_errors": sorted(set(projection_errors)),
                "typed_projection_missing": not mapped_visible,
                "monitor_filtered_before_exposure": True,
                "retrieval_source_filter_applied": allowed_source_assertion_ids is not None,
                "retrieval_source_assertion_count": len(selected_records),
                "conservative_unmapped_context_expiry": "recorded_at_plus_one_second",
                "provider_calls": 0,
                "graph_mutations": 0,
                "release_activation": False,
            },
        }
