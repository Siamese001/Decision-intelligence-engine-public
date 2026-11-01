"""UniversalWriteGateway implementation."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from ..a2a_finalization import (
    reverify_ingress_finalization_normalized_bundle,
    validated_ingress_finalization_reference,
    verified_finalization_binding,
)
from ..authorization import AuthorizationContext
from ..contracts import create_artifact, validate_artifact
from ..core import (
    assert_safe_mutable_path,
    connect_sqlite,
    digest,
    exact_file_copy,
    file_lock,
    read_json,
    sha256_bytes,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from ..graph_mutation_proof import NOT_APPLICABLE_NO_OPERATIONS, MutationGuard
from ..index import (
    EmbeddingBackend,
    HybridRetriever,
    build_sidecar,
    retrieval_metadata_from_artifact,
)
from ..label_quality import approved_validation_state, assert_label_quality_no_regression
from ..release import ReleaseManager, release_action_resources
from ..storage.governed_context import GLOBAL_WRITE_SERIALIZER
from .constants import (
    _A2A_FINALIZATION_BINDING_FIELDS,
    DIRECT_PROMOTION_POSTURES,
    UWG_FACT_PROMOTION_GOVERNANCE,
    UWG_GOVERNANCE_APPROVAL_CONTRACT,
    UWG_PROMOTION_POLICY_PATH,
)
from .policy import (
    _assert_backend_matches,
    _iso_date,
    _load_uwg_promotion_policy,
    _pair_binding,
    _policy_binding,
    _replayed_a2a_payload_governance,
    _same_pair,
    _seal_receipt,
    _table_exists,
    _utc_timestamp,
    _verify_sealed_receipt,
)

def _promote_single_candidate(
    connection: Any, assertion_id: str, validation_state: str,
    evidence: dict[str, Any], decision: dict[str, Any], evaluation: dict[str, Any], now: str,
) -> dict[str, Any]:
    before = connection.execute("SELECT * FROM assertion WHERE assertion_id=?", (assertion_id,)).fetchone()
    if not before or before["canonical_status"] != "candidate":
        raise RuntimeError(f"staging graph no longer contains the expected candidate assertion: {assertion_id}")
    changed = connection.execute(
        """UPDATE assertion
           SET validation_state=?,canonical_status='canonical',
               canonical_eligible=1,retrieval_eligible=1,approval_reference=?,
               hitl_required=0,validation_priority=NULL,validation_owner=NULL,
               updated_at=?
           WHERE assertion_id=? AND canonical_status='candidate'
             AND currentness_state='current'""",
        (
            validation_state,
            evidence["approval_reference"],
            now,
            assertion_id,
        ),
    ).rowcount
    if changed != 1:
        raise RuntimeError(f"UWG candidate promotion update failed: {assertion_id}")
    documents = connection.execute(
        """UPDATE search_document
           SET canonical_status='canonical',currentness_state='current',
               retrieval_eligible=1,updated_at=?
           WHERE assertion_id=?""",
        (now, assertion_id),
    ).rowcount
    if documents < 1:
        raise RuntimeError(
            f"UWG candidate assertion has no linked search document: {assertion_id}"
        )
    if _table_exists(connection, "assertion_label_state"):
        connection.execute(
            """UPDATE assertion_label_state
               SET validation_state=?,updated_at=?
               WHERE assertion_id=?""",
            (validation_state, now, assertion_id),
        )
    from .node_promotion import promote_candidate_endpoint_nodes
    promote_candidate_endpoint_nodes(connection, before, validation_state, now, _table_exists)
    operation_id = stable_id(
        "uwg-operation",
        decision["promotion_id"],
        assertion_id,
        length=28,
    )
    after = connection.execute(
        "SELECT * FROM assertion WHERE assertion_id=?", (assertion_id,)
    ).fetchone()
    operation = {
        "operation_id": operation_id,
        "assertion_id": assertion_id,
        "before_row_digest": digest(dict(before)),
        "after_row_digest": digest(dict(after)),
        "direct_evidence_ids": evidence["bindings"][assertion_id],
        "evaluation_id": evaluation["evaluation_id"],
        "evaluation_digest": evaluation["canonical_digest"]["value"],
        "promotion_id": decision["promotion_id"],
        "promotion_decision_digest": decision["canonical_digest"]["value"],
    }
    connection.execute(
        """INSERT INTO audit_event
           (audit_event_id,actor_or_process,action,record_type,record_id,previous_value_json,
            new_value_json,source_id,event_timestamp,reason,validation_reference,
            approval_reference,migration_or_ingestion_run_id,release_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            stable_id("audit-uwg", operation_id, length=28),
            "nee-universal-write-gateway",
            "uwg_promote_candidate_assertion",
            "assertion",
            assertion_id,
            json.dumps(
                {
                    "validation_state": before["validation_state"],
                    "canonical_status": before["canonical_status"],
                    "canonical_eligible": before["canonical_eligible"],
                    "retrieval_eligible": before["retrieval_eligible"],
                    "row_digest": operation["before_row_digest"],
                },
                sort_keys=True,
            ),
            json.dumps(operation, sort_keys=True),
            before["primary_source_id"],
            now,
            "One-time UWG promotion after candidate-lane runtime evaluation.",
            evaluation["evaluation_id"],
            evidence["approval_reference"],
            decision["origin_run_id"],
            "uwg-1.0.0",
        ),
    )
    return operation


class UniversalWriteGateway:
    """Evaluate candidate retrieval and make a narrow, one-time graph write."""

    def __init__(self, runtime_root: Path, seed_graph: Path) -> None:
        self.manager = ReleaseManager(runtime_root, seed_graph)
        self.runtime_root = self.manager.runtime_root
        self.uwg_root = self.runtime_root / "uwg"
        self.evaluations_dir = self.uwg_root / "evaluations"
        self.decisions_dir = self.uwg_root / "decisions"
        self.approvals_dir = self.uwg_root / "approvals"

    def _assert_runtime_target(self, path: Path, *, label: str) -> Path:
        return assert_safe_mutable_path(self.runtime_root, path, label=label)

    def _require_fresh_staging_graph(
        self, *, generation_id: str, expected_active: dict[str, Any]
    ) -> Path:
        """Return the only graph path a UWG mutator may write.

        The write helpers are intentionally callable from Python for the
        release flow, so their boundary cannot rely on a public caller having
        copied the active graph first.  Derive the path from the release
        manager, require the active-pair snapshot to remain current, and prove
        the unopened staging copy is byte-identical to that active graph.
        """

        generation_dir = self.manager.generations_dir / generation_id
        graph_path = generation_dir / "graph.sqlite"
        self.manager._assert_mutable_layout(generation_dir, graph_path)
        if graph_path.parent.resolve() != generation_dir.resolve() or graph_path.name != "graph.sqlite":
            raise RuntimeError("UWG staging graph path is not a canonical generation graph")
        active_now = self.manager.active_pair()
        if not _same_pair(active_now, expected_active):
            raise RuntimeError(
                f"L0_ROUTE_GENERATION_DRIFT: UWG staging write is stale relative to the active pair "
                f"({expected_active.get('generation_id')} != {active_now.get('generation_id')})"
            )
        active_graph = self.manager._resolve(expected_active["graph_path"])
        if graph_path.resolve() == active_graph.resolve():
            raise RuntimeError("UWG staging write may never target the active graph")
        if not graph_path.is_file():
            raise RuntimeError("UWG staging graph copy is missing")
        if (generation_dir / "pair_manifest.json").exists():
            raise RuntimeError("UWG staging generation is already sealed")
        if sha256_file(graph_path) != expected_active["graph_sha256"]:
            raise RuntimeError("UWG staging graph is not an unmodified active-pair copy")
        return graph_path

    def _evaluation_output_path(
        self, evaluation_id: str, supplied: Path | None
    ) -> Path:
        candidate = (
            supplied
            if supplied is not None
            else self.evaluations_dir / f"{evaluation_id}.json"
        )
        return self._assert_runtime_target(
            Path(candidate).expanduser().absolute(), label="UWG evaluation output"
        )

    @staticmethod
    def _require_evaluation_authorization(
        pair: dict[str, Any],
        authorization_context: AuthorizationContext | None,
        *,
        benchmark_file_sha256: str,
        policy_file_sha256: str,
    ) -> None:
        if not pair["production_qualified"]:
            return
        if authorization_context is None:
            raise PermissionError("production UWG evaluation requires authorization")
        authorization_context.require("knowledge:query")
        if (
            authorization_context.approved_model_manifest_digest
            != pair["model_manifest_digest"]
        ):
            raise PermissionError(
                "UWG evaluation authorization is not bound to the active BGE-M3 model"
            )
        authorization_context.require_resources(
            {
                "uwg_benchmark": benchmark_file_sha256,
                "uwg_policy": policy_file_sha256,
                "active_generation": sha256_bytes(pair["generation_id"].encode("utf-8")),
            }
        )

    @staticmethod
    def _candidate_ids(value: dict[str, Any]) -> list[str]:
        return sorted(value["candidate_assertion_ids"])

    @staticmethod
    def _assert_contract(value: dict[str, Any], contract_id: str) -> None:
        validate_artifact(value)
        if value.get("contract_id") != contract_id:
            raise ValueError(f"expected {contract_id}; got {value.get('contract_id')!r}")

    def _candidate_sidecar_records(
        self, pair: dict[str, Any], candidate_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        targets = set(candidate_ids)
        records: dict[str, dict[str, Any]] = {}
        sidecar_path = self.manager._resolve(pair["sidecar_path"])
        connection = connect_sqlite(sidecar_path, readonly=True)
        try:
            for row in connection.execute("SELECT lane,artifact_json FROM retrieval_record"):
                artifact = json.loads(row["artifact_json"])
                assertion_id = artifact.get("extensions", {}).get(
                    "com.partner_advisory.nee.graph-object-id"
                )
                if assertion_id not in targets:
                    continue
                if row["lane"] != "candidate":
                    raise RuntimeError(
                        "UWG target assertion is not exclusively in the candidate retrieval lane: "
                        f"{assertion_id}"
                    )
                try:
                    retrieval_metadata = retrieval_metadata_from_artifact(artifact)
                except (KeyError, RuntimeError, ValueError) as exc:
                    raise RuntimeError(
                        "UWG target assertion has invalid retrieval governance metadata: "
                        f"{assertion_id}"
                    ) from exc
                if (
                    retrieval_metadata["retrieval_kind"]
                    != UWG_FACT_PROMOTION_GOVERNANCE["retrieval_kind"]
                    or retrieval_metadata["answer_use"]
                    != UWG_FACT_PROMOTION_GOVERNANCE["answer_use"]
                    or retrieval_metadata["epistemic_status"] != "candidate"
                ):
                    raise RuntimeError(
                        "UWG target is not a factual candidate retrieval record: "
                        f"{assertion_id}"
                    )
                if assertion_id in records:
                    raise RuntimeError(
                        "UWG target assertion has multiple candidate retrieval records: "
                        f"{assertion_id}"
                    )
                records[assertion_id] = artifact
        finally:
            connection.close()
        missing = sorted(targets - set(records))
        if missing:
            raise RuntimeError(
                "UWG targets are absent from the active candidate sidecar: " f"{missing}"
            )
        return records

    @staticmethod
    def _is_hex64(val: Any) -> bool:
        return isinstance(val, str) and len(val) == 64 and all(c in "0123456789abcdef" for c in val)

    @staticmethod
    def _is_valid_a2a_finalization_binding(binding: Any) -> bool:
        if not isinstance(binding, dict) or set(binding) != _A2A_FINALIZATION_BINDING_FIELDS:
            return False
        if binding.get("verification_state") != "finalization_archive_replayed":
            return False
        digest_fields = (
            "receipt_canonical_digest", "receipt_file_sha256",
            "package_file_sha256", "package_canonical_digest", "archive_binding_digest",
        )
        if not all(UniversalWriteGateway._is_hex64(binding.get(f)) for f in digest_fields):
            return False
        return all(isinstance(binding.get(f), str) and bool(binding[f]) for f in ("receipt_id", "archive_root_id"))

    @staticmethod
    def _is_valid_a2a_span_binding(binding: Any, required: set[str], by_evidence: dict[str, Any]) -> bool:
        if not isinstance(binding, dict) or set(binding) != required:
            return False
        eid = binding.get("evidence_id")
        if not isinstance(eid, str) or not eid or eid in by_evidence:
            return False
        if binding.get("decision") not in {"CONFIRM", "REWRITE"}:
            return False
        quote = binding.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            return False
        if binding.get("quote_sha256") != sha256_bytes(quote.encode("utf-8")):
            return False
        loc = binding.get("canonical_locator")
        if not isinstance(loc, dict) or set(loc) != {"kind", "value", "quote_digest"}:
            return False
        return loc.get("kind") == "uri_fragment" and loc.get("quote_digest") == binding.get("quote_sha256")

    @staticmethod
    def _is_valid_candidate_evidence_ids(candidate_evidence_ids: Any, evidence_by_id: dict[str, Any]) -> bool:
        if not isinstance(candidate_evidence_ids, list) or not candidate_evidence_ids:
            return False
        if any(not isinstance(eid, str) or not eid for eid in candidate_evidence_ids):
            return False
        return len(candidate_evidence_ids) == len(set(candidate_evidence_ids)) and set(candidate_evidence_ids) <= set(evidence_by_id)

    def _reverify_a2a_bundle(self, finalization_reference: Any, finalization_binding: dict[str, Any], assertion_id: str) -> tuple[Any, dict[str, Any]]:
        try:
            reference = validated_ingress_finalization_reference(finalization_reference)
            verification, replayed_bundle = reverify_ingress_finalization_normalized_bundle(
                runtime_root=self.runtime_root,
                reference=reference,
                expected_binding=finalization_binding,
            )
            if verified_finalization_binding(verification) != finalization_binding:
                raise RuntimeError("fresh A2A finalization replay binding drifted")
            return reference, replayed_bundle
        except ValueError as exc:
            raise RuntimeError(
                f"UWG A2A fact target has no re-verifiable finalization evidence: {assertion_id}"
            ) from exc

    @staticmethod
    def _extract_expected_candidate_and_gov(
        replayed_bundle: dict[str, Any], candidate_id: str, reference: Any, qualifiers: dict[str, Any], assertion_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        expected_candidates = {
            c.get("candidate_id"): c
            for c in replayed_bundle.get("candidates", [])
            if isinstance(c, dict) and isinstance(c.get("candidate_id"), str)
        }
        gov_by_cand = replayed_bundle.get("extensions", {}).get("com.partner_advisory.nee.a2a-packet-candidate-governance", {})
        cand = expected_candidates.get(candidate_id)
        gov = gov_by_cand.get(candidate_id) if isinstance(gov_by_cand, dict) else None
        if not isinstance(cand, dict) or not isinstance(gov, dict):
            raise RuntimeError(f"UWG A2A fact target is absent from the replayed finalized packet: {assertion_id}")
        expected_payload_gov = _replayed_a2a_payload_governance(gov, reference)
        for field, val in expected_payload_gov.items():
            if qualifiers.get(field) != val:
                raise RuntimeError(f"UWG A2A graph governance differs from the replayed finalized packet: {assertion_id}")
        return cand, expected_payload_gov

    def _a2a_source_span_bindings(
        self,
        qualifiers: dict[str, Any], *, assertion_id: str
    ) -> dict[str, Any] | None:
        """Replay and validate every A2A field persisted for a fact candidate."""

        source_kind = qualifiers.get("source_kind")
        if source_kind is None:
            return None
        if source_kind != "a2a_fact_vector_packet":
            raise RuntimeError(
                "UWG target has an unsupported persisted source kind: " f"{assertion_id}"
            )
        finalization_binding = qualifiers.get("a2a_finalization_binding")
        if not UniversalWriteGateway._is_valid_a2a_finalization_binding(finalization_binding):
            raise RuntimeError(
                "UWG A2A fact target is missing a replayed finalization receipt: "
                f"{assertion_id}"
            )
        reference, replayed_bundle = self._reverify_a2a_bundle(
            qualifiers.get("a2a_finalization_reference"), finalization_binding, assertion_id
        )
        candidate_id = qualifiers.get("dual_pipeline_candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise RuntimeError(
                "UWG A2A fact target has no replayable candidate identity: "
                f"{assertion_id}"
            )
        expected_candidate, expected_payload_governance = UniversalWriteGateway._extract_expected_candidate_and_gov(
            replayed_bundle, candidate_id, reference, qualifiers, assertion_id
        )
        bindings = qualifiers.get("a2a_source_span_bindings")
        required = {
            "selection_ledger_sha256", "span_ledger_sha256", "reviewer", "decided_at",
            "human_attestation", "reviewer_authorization_reference", "fact_id", "decision",
            "span_proposal_id", "evidence_id", "source_id", "rationale", "quote",
            "quote_sha256", "visible_start_offset", "visible_end_offset",
            "raw_response_sha256", "visible_text_sha256", "canonical_locator",
        }
        if not isinstance(bindings, list) or not bindings:
            raise RuntimeError(
                "UWG A2A fact target is missing selected source-span provenance: "
                f"{assertion_id}"
            )
        by_evidence: dict[str, dict[str, Any]] = {}
        for binding in bindings:
            if not UniversalWriteGateway._is_valid_a2a_span_binding(binding, required, by_evidence):
                raise RuntimeError(
                    "UWG A2A source-span provenance is malformed: " f"{assertion_id}"
                )
            by_evidence[binding["evidence_id"]] = binding
        if bindings != expected_payload_governance.get("a2a_source_span_bindings"):
            raise RuntimeError(
                "UWG A2A selected source spans differ from the replayed finalized packet: "
                f"{assertion_id}"
            )
        evidence_by_id = {
            evidence.get("evidence_id"): evidence
            for evidence in replayed_bundle.get("evidence", [])
            if isinstance(evidence, dict) and isinstance(evidence.get("evidence_id"), str)
        }
        candidate_evidence_ids = expected_candidate.get("evidence_ids")
        if not UniversalWriteGateway._is_valid_candidate_evidence_ids(candidate_evidence_ids, evidence_by_id):
            raise RuntimeError("UWG replayed A2A evidence identifiers are invalid")
        return {
            "bindings": by_evidence,
            "candidate": expected_candidate,
            "governance": expected_payload_governance,
            "source_id": replayed_bundle.get("source_id"),
            "source_version_id": replayed_bundle.get("source_version_id"),
            "evidence_by_id": evidence_by_id,
        }

    def _validate_candidate_fact_promotion_governance(
        self,
        graph_path: Path, candidate_ids: list[str]
    ) -> None:
        """Fail closed unless graph-persisted governance permits fact promotion.

        Candidate-lane presence is deliberately insufficient.  Citation-only,
        guidance, source-chunk, action, relationship, and other non-assertion
        candidates can be useful retrieval records, but none may be promoted
        through UWG as a factual assertion.
        """

        target_ids = sorted(candidate_ids)
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("UWG promotion candidate assertion IDs must be unique")
        connection = connect_sqlite(graph_path, readonly=True)
        try:
            for assertion_id in target_ids:
                row = connection.execute(
                    """SELECT assertion_id,assertion_type,qualifiers_json
                       FROM assertion WHERE assertion_id=?""",
                    (assertion_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        f"UWG candidate assertion does not exist: {assertion_id}"
                    )
                try:
                    qualifiers = json.loads(row["qualifiers_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        "UWG target has invalid persisted retrieval governance: "
                        f"{assertion_id}"
                    ) from exc
                if not isinstance(qualifiers, dict):
                    raise RuntimeError(
                        "UWG target has invalid persisted retrieval governance: "
                        f"{assertion_id}"
                    )
                actual_governance = {
                    field: qualifiers.get(field)
                    for field in UWG_FACT_PROMOTION_GOVERNANCE
                }
                if (
                    row["assertion_type"] != "extracted_claim"
                    or qualifiers.get("dual_pipeline_candidate_type") != "assertion"
                    or actual_governance != UWG_FACT_PROMOTION_GOVERNANCE
                ):
                    raise RuntimeError(
                        "UWG target is not a fact-promotion eligible factual candidate "
                        f"assertion: {assertion_id}"
                    )
                self._a2a_source_span_bindings(
                    qualifiers, assertion_id=assertion_id
                )
        finally:
            connection.close()

    @staticmethod
    def _validate_benchmark(benchmark: dict[str, Any]) -> dict[str, Any]:
        """Require a policy-bound benchmark with all safety test families."""

        policy = _load_uwg_promotion_policy()
        expected_binding = _policy_binding(policy)
        if benchmark["acceptance_policy"] != expected_binding:
            raise ValueError(
                "UWG benchmark acceptance policy is not the current repository-owned policy"
            )
        if benchmark["thresholds"] != policy["thresholds"]:
            raise ValueError(
                "UWG benchmark thresholds must exactly match the repository-owned policy"
            )
        candidate_ids = set(benchmark["candidate_assertion_ids"])
        case_ids = [
            case["case_id"]
            for name in ("cases", "negative_cases", "lane_isolation_cases", "acl_cases")
            for case in benchmark[name]
        ]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("UWG benchmark case IDs must be unique")
        expected_ids = {
            assertion_id
            for case in benchmark["cases"]
            for assertion_id in case["expected_assertion_ids"]
        }
        if expected_ids != candidate_ids:
            raise ValueError(
                "UWG benchmark cases must cover exactly the candidate assertion IDs in scope"
            )
        for name in ("negative_cases", "lane_isolation_cases", "acl_cases"):
            forbidden_ids = {
                assertion_id
                for case in benchmark[name]
                for assertion_id in case["forbidden_assertion_ids"]
            }
            if forbidden_ids != candidate_ids:
                raise ValueError(
                    f"UWG {name} must cover exactly the candidate assertion IDs in scope"
                )
        return policy

    @staticmethod
    def _retrieved_assertion_ids(result: dict[str, Any]) -> list[str]:
        retrieved_ids: list[str] = []
        for item in result["results"]:
            assertion_id = item["record"].get("extensions", {}).get(
                "com.partner_advisory.nee.graph-object-id"
            )
            if isinstance(assertion_id, str) and assertion_id not in retrieved_ids:
                retrieved_ids.append(assertion_id)
        return retrieved_ids

    @classmethod
    def _evaluate_absence_cases(
        cls,
        retriever: HybridRetriever,
        cases: list[dict[str, Any]],
        *,
        lane: str,
        top_k: int,
        retrieval_mode: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for case in cases:
            started = time.monotonic()
            result = retriever.retrieve_lane(
                case["query"],
                lane,
                classification=case["classification"],
                audiences=case["audiences"],
                top_k=top_k,
                retrieval_mode=retrieval_mode,
            )
            latency_ms = (time.monotonic() - started) * 1000
            retrieved_ids = cls._retrieved_assertion_ids(result)
            forbidden = sorted(case["forbidden_assertion_ids"])
            violating = sorted(set(retrieved_ids) & set(forbidden))
            results.append(
                {
                    "case_id": case["case_id"],
                    "lane": lane,
                    "forbidden_assertion_ids": forbidden,
                    "retrieved_assertion_ids": retrieved_ids,
                    "violating_assertion_ids": violating,
                    "pass": not violating,
                    "latency_ms": latency_ms,
                }
            )
        return results

    def evaluate(
        self,
        benchmark_path: Path,
        *,
        backend: EmbeddingBackend,
        output_path: Path | None = None,
        evaluation_id: str | None = None,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        """Run only local candidate-lane retrieval and seal the result."""

        benchmark_file = Path(benchmark_path).expanduser().resolve()
        benchmark = read_json(benchmark_file)
        self._assert_contract(benchmark, "urn:nee:dual-pipeline:uwg-benchmark:1.0.0")
        policy = self._validate_benchmark(benchmark)
        pair = self.manager.active_pair()
        _assert_backend_matches(pair, backend)
        benchmark_file_sha256 = sha256_file(benchmark_file)
        self._require_evaluation_authorization(
            pair,
            authorization_context,
            benchmark_file_sha256=benchmark_file_sha256,
            policy_file_sha256=sha256_file(UWG_PROMOTION_POLICY_PATH),
        )
        candidate_ids = self._candidate_ids(benchmark)
        self._candidate_sidecar_records(pair, candidate_ids)
        self._validate_candidate_fact_promotion_governance(
            self.manager._resolve(pair["graph_path"]), candidate_ids
        )
        retriever = HybridRetriever(self.manager._resolve(pair["sidecar_path"]), backend)
        thresholds = benchmark["thresholds"]
        case_results: list[dict[str, Any]] = []
        for case in benchmark["cases"]:
            started = time.monotonic()
            result = retriever.retrieve_lane(
                case["query"],
                "candidate",
                classification=case["classification"],
                audiences=case["audiences"],
                top_k=thresholds["top_k"],
                retrieval_mode="candidate_evaluation",
            )
            latency_ms = (time.monotonic() - started) * 1000
            retrieved_ids = self._retrieved_assertion_ids(result)
            expected = set(case["expected_assertion_ids"])
            first_rank = next(
                (
                    index
                    for index, assertion_id in enumerate(retrieved_ids, 1)
                    if assertion_id in expected
                ),
                None,
            )
            relevant = expected & set(retrieved_ids)
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "expected_assertion_ids": sorted(expected),
                    "retrieved_assertion_ids": retrieved_ids,
                    "first_relevant_rank": first_rank,
                    "recall_at_k": len(relevant) / len(expected),
                    "reciprocal_rank": 0 if first_rank is None else 1 / first_rank,
                    "latency_ms": latency_ms,
                }
            )
        negative_results = self._evaluate_absence_cases(
            retriever,
            benchmark["negative_cases"],
            lane="candidate",
            top_k=thresholds["top_k"],
            retrieval_mode="candidate_evaluation",
        )
        lane_isolation_results = self._evaluate_absence_cases(
            retriever,
            benchmark["lane_isolation_cases"],
            lane="approved",
            top_k=thresholds["top_k"],
            retrieval_mode="factual",
        )
        acl_results = self._evaluate_absence_cases(
            retriever,
            benchmark["acl_cases"],
            lane="candidate",
            top_k=thresholds["top_k"],
            retrieval_mode="candidate_evaluation",
        )
        all_case_results = (
            case_results + negative_results + lane_isolation_results + acl_results
        )
        aggregate = {
            "recall_at_k": sum(item["recall_at_k"] for item in case_results)
            / len(case_results),
            "mrr": sum(item["reciprocal_rank"] for item in case_results) / len(case_results),
            "max_latency_ms": max(item["latency_ms"] for item in all_case_results),
            "negative_leakage_rate": sum(
                bool(item["violating_assertion_ids"]) for item in negative_results
            )
            / len(negative_results),
            "lane_leakage_rate": sum(
                bool(item["violating_assertion_ids"]) for item in lane_isolation_results
            )
            / len(lane_isolation_results),
            "acl_leakage_rate": sum(
                bool(item["violating_assertion_ids"]) for item in acl_results
            )
            / len(acl_results),
        }
        status = "PASS" if (
            aggregate["recall_at_k"] >= thresholds["min_recall_at_k"]
            and aggregate["mrr"] >= thresholds["min_mrr"]
            and aggregate["max_latency_ms"] <= thresholds["max_latency_ms"]
            and aggregate["negative_leakage_rate"] <= thresholds["max_negative_leakage_rate"]
            and aggregate["lane_leakage_rate"] <= thresholds["max_lane_leakage_rate"]
            and aggregate["acl_leakage_rate"] <= thresholds["max_acl_leakage_rate"]
        ) else "FAIL"
        evaluation_id = evaluation_id or stable_id(
            "uwg-evaluation", benchmark["benchmark_id"], pair["generation_id"], utc_now(), length=28
        )
        evaluation = create_artifact(
            "uwg-runtime-evaluation",
            evaluation_id,
            evaluation_id=evaluation_id,
            benchmark_id=benchmark["benchmark_id"],
            benchmark_digest=benchmark["canonical_digest"]["value"],
            benchmark_file_sha256=benchmark_file_sha256,
            origin_run_id=benchmark["origin_run_id"],
            candidate_pair=_pair_binding(pair),
            candidate_assertion_ids=candidate_ids,
            case_results=case_results,
            negative_results=negative_results,
            lane_isolation_results=lane_isolation_results,
            acl_results=acl_results,
            aggregate=aggregate,
            thresholds=thresholds,
            acceptance_policy=_policy_binding(policy),
            status=status,
            evaluated_at=utc_now(),
            extensions={
                "com.partner_advisory.nee.uwg": {
                    "retrieval_lane": "candidate",
                    "external_l2_calls": 0,
                    "active_pair_generation": pair["generation_id"],
                    "policy_file_sha256": sha256_file(UWG_PROMOTION_POLICY_PATH),
                }
            },
        )
        target = self._evaluation_output_path(evaluation_id, output_path)
        write_json_exclusive(target, evaluation)
        return {"evaluation": evaluation, "evaluation_path": str(target)}

    @staticmethod
    def _validate_decision_scope(
        evaluation: dict[str, Any], decision: dict[str, Any], active: dict[str, Any]
    ) -> list[str]:
        if evaluation["status"] != "PASS":
            raise RuntimeError("UWG promotion requires a passing runtime evaluation")
        if decision["evaluation_id"] != evaluation["evaluation_id"]:
            raise ValueError("UWG promotion decision names a different evaluation")
        if decision["evaluation_digest"] != evaluation["canonical_digest"]["value"]:
            raise ValueError("UWG promotion decision has a different evaluation digest")
        if decision["origin_run_id"] != evaluation["origin_run_id"]:
            raise ValueError("UWG promotion decision has a different origin run")
        if not _same_pair(decision["candidate_pair"], evaluation["candidate_pair"]):
            raise ValueError("UWG decision and evaluation bind different candidate pairs")
        if not _same_pair(evaluation["candidate_pair"], active):
            raise RuntimeError(
                f"L0_ROUTE_GENERATION_DRIFT: UWG evaluation is stale relative to active candidate pair "
                f"({evaluation['candidate_pair'].get('generation_id')} != {active.get('generation_id')})"
            )
        candidate_ids = sorted(evaluation["candidate_assertion_ids"])
        if sorted(decision["candidate_assertion_ids"]) != candidate_ids:
            raise ValueError(
                "UWG decision must promote exactly the evaluated candidate assertion IDs"
            )
        return candidate_ids

    @staticmethod
    def _validate_eval_result_group(
        results: list[dict[str, Any]],
        result_name: str,
        aggregate_name: str,
        expected_lane: str,
        candidate_ids: set[str],
        aggregate_val: Any,
    ) -> list[str]:
        c_ids = [r["case_id"] for r in results]
        group_forbidden = {
            aid for r in results for aid in r["forbidden_assertion_ids"]
        }
        if group_forbidden != candidate_ids:
            raise ValueError(
                f"UWG runtime evaluation {result_name} does not cover its promotion scope"
            )
        calculated = sum(bool(r["violating_assertion_ids"]) for r in results) / len(results)
        if aggregate_val != calculated:
            raise ValueError(
                f"UWG runtime evaluation has an invalid {aggregate_name} aggregate"
            )
        for result in results:
            if result["lane"] != expected_lane:
                raise ValueError(
                    f"UWG runtime evaluation {result_name} ran in the wrong retrieval lane"
                )
            expected_violations = sorted(
                set(result["retrieved_assertion_ids"]) & set(result["forbidden_assertion_ids"])
            )
            if (
                result["violating_assertion_ids"] != expected_violations
                or result["pass"] != (not expected_violations)
            ):
                raise ValueError(
                    f"UWG runtime evaluation has an invalid {result_name} case result"
                )
        return c_ids

    @staticmethod
    def _validate_eval_positive_cases(case_results: list[dict[str, Any]], candidate_ids: set[str], aggregate: dict[str, Any]) -> None:
        if {
            aid for result in case_results for aid in result["expected_assertion_ids"]
        } != candidate_ids:
            raise ValueError(
                "UWG runtime evaluation positive cases do not cover its promotion scope"
            )
        calculated_recall = sum(r["recall_at_k"] for r in case_results) / len(case_results)
        calculated_mrr = sum(r["reciprocal_rank"] for r in case_results) / len(case_results)
        if aggregate.get("recall_at_k") != calculated_recall or aggregate.get("mrr") != calculated_mrr:
            raise ValueError("UWG runtime evaluation has invalid positive retrieval aggregates")

    @staticmethod
    def _is_aggregate_passing(aggregate: dict[str, Any], thresholds: dict[str, Any]) -> bool:
        return (
            aggregate["recall_at_k"] >= thresholds["min_recall_at_k"]
            and aggregate["mrr"] >= thresholds["min_mrr"]
            and aggregate["max_latency_ms"] <= thresholds["max_latency_ms"]
            and aggregate["negative_leakage_rate"] <= thresholds["max_negative_leakage_rate"]
            and aggregate["lane_leakage_rate"] <= thresholds["max_lane_leakage_rate"]
            and aggregate["acl_leakage_rate"] <= thresholds["max_acl_leakage_rate"]
        )

    @staticmethod
    def _validate_evaluation_policy(evaluation: dict[str, Any]) -> None:
        """Reject hand-authored PASS evaluations that drift from the safety policy."""

        policy = _load_uwg_promotion_policy()
        thresholds = policy["thresholds"]
        if evaluation["acceptance_policy"] != _policy_binding(policy):
            raise ValueError(
                "UWG runtime evaluation is not bound to the current repository-owned policy"
            )
        if evaluation["thresholds"] != thresholds:
            raise ValueError(
                "UWG runtime evaluation thresholds differ from the repository-owned policy"
            )
        result_groups = (
            ("negative_results", "negative_leakage_rate", "candidate"),
            ("lane_isolation_results", "lane_leakage_rate", "approved"),
            ("acl_results", "acl_leakage_rate", "candidate"),
        )
        candidate_ids = set(evaluation["candidate_assertion_ids"])
        case_ids = [result["case_id"] for result in evaluation["case_results"]]
        for result_name, aggregate_name, expected_lane in result_groups:
            c_ids = UniversalWriteGateway._validate_eval_result_group(
                evaluation[result_name],
                result_name,
                aggregate_name,
                expected_lane,
                candidate_ids,
                evaluation["aggregate"][aggregate_name],
            )
            case_ids.extend(c_ids)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("UWG runtime evaluation case IDs must be unique")
        UniversalWriteGateway._validate_eval_positive_cases(
            evaluation["case_results"], candidate_ids, evaluation["aggregate"]
        )
        observed_max_latency = max(
            result["latency_ms"]
            for results in (
                evaluation["case_results"],
                evaluation["negative_results"],
                evaluation["lane_isolation_results"],
                evaluation["acl_results"],
            )
            for result in results
        )
        if evaluation["aggregate"]["max_latency_ms"] != observed_max_latency:
            raise ValueError("UWG runtime evaluation has an invalid max_latency_ms aggregate")
        required_pass = UniversalWriteGateway._is_aggregate_passing(evaluation["aggregate"], thresholds)
        if evaluation["status"] == "PASS" and not required_pass:
            raise ValueError("UWG runtime evaluation declares PASS despite failed policy gates")

    @staticmethod
    def _source_snapshot_bindings(decision: dict[str, Any]) -> list[dict[str, str]]:
        bindings: list[dict[str, str]] = []
        for assertion_binding in decision["evidence_bindings"]:
            assertion_id = assertion_binding["assertion_id"]
            for snapshot in assertion_binding["evidence_snapshots"]:
                bindings.append(
                    {
                        "assertion_id": assertion_id,
                        "evidence_id": snapshot["evidence_id"],
                        "source_id": snapshot["source_id"],
                        "source_version_id": snapshot["source_version_id"],
                        "source_content_hash": snapshot["source_content_hash"],
                    }
                )
        return UniversalWriteGateway._canonical_source_snapshot_bindings(bindings)

    @staticmethod
    def _canonical_source_snapshot_bindings(
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Return a closed, deterministic source-snapshot scope.

        This deliberately accepts neither extra fields nor a partially bound
        evidence reference.  The same normalized representation is written to
        the graph approval, compared to the reviewer decision, and checked
        against the candidate graph immediately before promotion.
        """

        fields = {
            "assertion_id",
            "evidence_id",
            "source_id",
            "source_version_id",
            "source_content_hash",
        }
        normalized: list[dict[str, str]] = []
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != fields:
                raise ValueError(
                    "UWG source snapshot bindings must contain exactly assertion, evidence, "
                    "source, version, and content-hash fields"
                )
            value: dict[str, str] = {}
            for field in fields:
                raw = binding[field]
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError(
                        f"UWG source snapshot binding {field} must be a nonempty string"
                    )
                value[field] = raw
            normalized.append(value)
        return sorted(
            normalized,
            key=lambda value: (
                value["assertion_id"],
                value["evidence_id"],
                value["source_version_id"],
                value["source_content_hash"],
            ),
        )

    @staticmethod
    def _exact_approval_scope(
        *,
        promotion_id: str,
        candidate_ids: list[str],
        source_snapshot_bindings: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "allowed_actions": ["canonical_assertion_promotion"],
            "uwg_promotion": {
                "promotion_id": promotion_id,
                "candidate_assertion_ids": sorted(candidate_ids),
                "source_snapshot_bindings": (
                    UniversalWriteGateway._canonical_source_snapshot_bindings(
                        source_snapshot_bindings
                    )
                ),
            },
        }

    @staticmethod
    def _validate_exact_approval_scope(
        approval: Any,
        *,
        decision: dict[str, Any],
        candidate_ids: list[str],
        source_snapshot_bindings: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Require a one-time, expiring graph approval with no ambient scope."""

        if approval["approval_type"] != "ingestion_promotion":
            raise RuntimeError("UWG promotion requires an ingestion_promotion graph approval")
        now = dt.datetime.now(dt.timezone.utc)
        if _utc_timestamp(approval["approved_at"], label="graph approval approved_at") > now:
            raise RuntimeError("UWG graph approval is not yet effective")
        expires_at = approval["expires_at"]
        if expires_at is None:
            raise RuntimeError("UWG promotion requires an expiring exact-scope graph approval")
        if _utc_timestamp(expires_at, label="graph approval expires_at") <= now:
            raise RuntimeError("UWG graph approval is expired")
        try:
            scope = json.loads(approval["scope_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("UWG graph approval has invalid scope JSON") from exc
        expected_scope = UniversalWriteGateway._exact_approval_scope(
            promotion_id=decision["promotion_id"],
            candidate_ids=candidate_ids,
            source_snapshot_bindings=source_snapshot_bindings,
        )
        if scope != expected_scope:
            raise RuntimeError(
                "UWG graph approval scope is not the exact one-time promotion scope"
            )
        if decision["approval_scope_digest"] != digest(scope):
            raise RuntimeError("UWG decision is not bound to the exact graph approval scope")
        return scope

    @staticmethod
    def _check_assertion_status_and_conflicts(connection: Any, assertion_id: str) -> dict[str, Any]:
        assertion = connection.execute(
            "SELECT assertion_id,assertion_type,canonical_status,currentness_state,primary_source_id,object_literal,scope_text,valid_from,valid_to,superseded_at,superseded_by_assertion_id,resolution_state,qualifiers_json FROM assertion WHERE assertion_id=?",
            (assertion_id,),
        ).fetchone()
        if not assertion:
            raise RuntimeError(f"UWG candidate assertion does not exist: {assertion_id}")
        if (
            assertion["canonical_status"] != "candidate"
            or assertion["currentness_state"] != "current"
            or assertion["resolution_state"] != "resolved"
            or assertion["superseded_at"] is not None
            or assertion["superseded_by_assertion_id"] is not None
        ):
            raise RuntimeError(
                f"UWG target must remain a resolved, current, unsuperseded candidate assertion: {assertion_id}"
            )
        today = dt.datetime.now(dt.timezone.utc).date()
        valid_from = assertion["valid_from"]
        valid_to = assertion["valid_to"]
        if valid_from is not None and _iso_date(valid_from, label="assertion valid_from") > today:
            raise RuntimeError(f"UWG target is outside its current validity window: {assertion_id}")
        if valid_to is not None and _iso_date(valid_to, label="assertion valid_to") <= today:
            raise RuntimeError(f"UWG target is outside its current validity window: {assertion_id}")
        contradictions = [
            row["evidence_id"]
            for row in connection.execute(
                "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? AND evidence_relationship='contradicts' ORDER BY evidence_id",
                (assertion_id,),
            )
        ]
        if contradictions:
            raise RuntimeError(
                f"UWG target has contradictory evidence and cannot be promoted: {assertion_id}: {contradictions}"
            )
        conflicts = [
            row["conflict_id"]
            for row in connection.execute(
                "SELECT conflict_id FROM assertion_conflict WHERE active=1 AND resolution_state='unresolved' AND (assertion_id_1=? OR assertion_id_2=?) ORDER BY conflict_id",
                (assertion_id, assertion_id),
            )
        ]
        if conflicts:
            raise RuntimeError(
                f"UWG target has unresolved assertion conflicts and cannot be promoted: {assertion_id}: {conflicts}"
            )
        return assertion

    @staticmethod
    def _check_a2a_graph_assertion_match(
        assertion: dict[str, Any],
        assertion_qualifiers: dict[str, Any],
        a2a_replay: dict[str, Any],
        connection: Any,
        assertion_id: str,
    ) -> None:
        expected_candidate = a2a_replay["candidate"]
        expected_qualifiers = {
            "dual_pipeline_candidate_id": expected_candidate["candidate_id"],
            "dual_pipeline_candidate_type": expected_candidate["candidate_type"],
            "dual_pipeline_disposition": "CANDIDATE",
            **a2a_replay["governance"],
        }
        if (
            assertion["assertion_type"] != "extracted_claim"
            or assertion["primary_source_id"] != a2a_replay["source_id"]
            or assertion["scope_text"] != expected_candidate["normalized_text"]
            or assertion["object_literal"] != expected_candidate["normalized_text"]
            or assertion_qualifiers != expected_qualifiers
        ):
            raise RuntimeError(
                f"UWG A2A graph assertion content differs from the replayed finalized packet: {assertion_id}"
            )
        graph_evidence_ids = {
            row["evidence_id"]
            for row in connection.execute(
                "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? AND evidence_relationship='supports'",
                (assertion_id,),
            )
        }
        if graph_evidence_ids != set(expected_candidate["evidence_ids"]):
            raise RuntimeError(
                f"UWG A2A graph evidence IDs differ from the replayed finalized packet: {assertion_id}"
            )

    @staticmethod
    def _check_evidence_authority_rank(evidence: dict[str, Any]) -> None:
        if (
            evidence["evidence_directness"] != "direct"
            or evidence["evidence_authority_rank"] < 7
            or evidence["source_authority_rank"] < 7
            or evidence["version_authority_rank"] < 7
            or evidence["source_posture"] not in DIRECT_PROMOTION_POSTURES
            or evidence["location_status"] != "exact"
            or not evidence["locator_value"].strip()
            or not isinstance(evidence["direct_quote"], str)
            or not evidence["direct_quote"].strip()
        ):
            raise RuntimeError(
                "UWG promotion requires direct, exact, high-authority evidence from "
                "an authoritative internal or external first-party source"
            )

    @staticmethod
    def _verify_a2a_evidence_span_binding(
        binding: Optional[dict[str, Any]],
        expected_evidence: Optional[dict[str, Any]],
        evidence: dict[str, Any],
        assertion_id: str,
        evidence_id: str,
    ) -> None:
        if (
            binding is None
            or not isinstance(expected_evidence, dict)
            or binding["source_id"] != evidence["source_id"]
            or expected_evidence.get("source_id") != evidence["source_id"]
            or expected_evidence.get("source_version_id") != evidence["source_version_id"]
            or expected_evidence.get("locator", {}).get("kind") != evidence["locator_kind"]
            or expected_evidence.get("locator", {}).get("value") != evidence["locator_value"]
            or evidence["evidence_fingerprint"]
            != digest(
                {
                    "source_version_id": expected_evidence["source_version_id"],
                    "locator": expected_evidence["locator"],
                    "content_digest": expected_evidence["content_digest"],
                }
            )
            or binding["canonical_locator"].get("value") != evidence["locator_value"]
            or binding["quote_sha256"] != sha256_bytes(evidence["direct_quote"].encode("utf-8"))
        ):
            raise RuntimeError(
                f"UWG A2A evidence is not bound to the selected frozen source span: {assertion_id}: {evidence_id}"
            )

    @staticmethod
    def _check_single_evidence_item_snapshot(
        connection: Any,
        assertion_id: str,
        snapshot: dict[str, Any],
        a2a_replay: Optional[dict[str, Any]],
        a2a_span_bindings: Optional[dict[str, Any]],
    ) -> str:
        evidence_id = snapshot["evidence_id"]
        evidence = connection.execute(
            "SELECT e.evidence_id,e.evidence_fingerprint,e.source_id,e.locator_kind,e.locator_value,e.direct_quote,e.evidence_directness,e.source_authority_rank AS evidence_authority_rank,e.location_status,e.source_version_id,s.source_posture,s.authority_rank AS source_authority_rank,sv.authority_rank AS version_authority_rank,sv.content_hash AS source_content_hash "
            "FROM assertion_evidence ae JOIN evidence_item e ON e.evidence_id=ae.evidence_id JOIN source s ON s.source_id=e.source_id JOIN source_version sv ON sv.source_version_id=e.source_version_id WHERE ae.assertion_id=? AND ae.evidence_id=? AND ae.evidence_relationship='supports'",
            (assertion_id, evidence_id),
        ).fetchone()
        if not evidence:
            raise RuntimeError(
                f"UWG decision evidence is not exact supporting evidence for {assertion_id}: {evidence_id}"
            )
        actual_snapshot = {
            "evidence_id": evidence["evidence_id"],
            "source_id": evidence["source_id"],
            "source_version_id": evidence["source_version_id"],
            "source_content_hash": evidence["source_content_hash"],
        }
        expected_snapshot = {key: snapshot[key] for key in actual_snapshot}
        if actual_snapshot != expected_snapshot:
            raise RuntimeError(
                f"UWG decision source snapshot binding does not match graph evidence: {assertion_id}: {evidence_id}"
            )
        UniversalWriteGateway._check_evidence_authority_rank(evidence)
        if a2a_span_bindings is not None and a2a_replay is not None:
            binding = a2a_span_bindings.get(evidence_id)
            expected_evidence = a2a_replay["evidence_by_id"].get(evidence_id)
            UniversalWriteGateway._verify_a2a_evidence_span_binding(
                binding, expected_evidence, evidence, assertion_id, evidence_id
            )
        return str(evidence["source_id"])

    def _validate_source_snapshot_bindings(
        self,
        graph_path: Path,
        candidate_ids: list[str],
        source_snapshot_bindings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Verify the exact first-party evidence snapshot against the graph.

        Registration invokes this before it writes the approval row. Promotion
        invokes it again after evaluation.  This double check prevents a
        reviewer decision from being attached to a changed source version or a
        different evidence item after the signed registration step.
        """

        target_ids = sorted(candidate_ids)
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("UWG promotion candidate assertion IDs must be unique")
        snapshots = UniversalWriteGateway._canonical_source_snapshot_bindings(
            source_snapshot_bindings
        )
        if len(snapshots) != len(
            {(value["assertion_id"], value["evidence_id"]) for value in snapshots}
        ):
            raise ValueError(
                "UWG source snapshot bindings duplicate an assertion/evidence pair"
            )
        bindings: dict[str, list[dict[str, str]]] = {}
        for snapshot in snapshots:
            bindings.setdefault(snapshot["assertion_id"], []).append(snapshot)
        if set(bindings) != set(target_ids):
            raise ValueError(
                "UWG source snapshot bindings must cover exactly the promotion scope"
            )
        connection = connect_sqlite(graph_path, readonly=True)
        try:
            source_ids: set[str] = set()
            for assertion_id in target_ids:
                assertion = UniversalWriteGateway._check_assertion_status_and_conflicts(connection, assertion_id)
                try:
                    assertion_qualifiers = json.loads(assertion["qualifiers_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"UWG target has invalid persisted source governance: {assertion_id}"
                    ) from exc
                if not isinstance(assertion_qualifiers, dict):
                    raise RuntimeError(
                        f"UWG target has invalid persisted source governance: {assertion_id}"
                    )
                a2a_replay = self._a2a_source_span_bindings(
                    assertion_qualifiers, assertion_id=assertion_id
                )
                a2a_span_bindings = (
                    a2a_replay["bindings"] if a2a_replay is not None else None
                )
                if a2a_replay is not None:
                    UniversalWriteGateway._check_a2a_graph_assertion_match(
                        assertion, assertion_qualifiers, a2a_replay, connection, assertion_id
                    )
                for snapshot in bindings[assertion_id]:
                    sid = UniversalWriteGateway._check_single_evidence_item_snapshot(
                        connection, assertion_id, snapshot, a2a_replay, a2a_span_bindings
                    )
                    source_ids.add(sid)
        finally:
            connection.close()
        return {
            "source_ids": sorted(source_ids),
            "bindings": {
                key: sorted(value["evidence_id"] for value in snapshots)
                for key, snapshots in sorted(bindings.items())
            },
            "source_snapshot_bindings": snapshots,
        }

    def _validate_evidence_bindings(
        self,
        graph_path: Path,
        candidate_ids: list[str],
        decision: dict[str, Any],
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate decision evidence plus its active exact-scope approval."""

        del evaluation  # The decision/evaluation pair is checked before this stage.
        source_snapshot_bindings = self._source_snapshot_bindings(decision)
        self._validate_candidate_fact_promotion_governance(graph_path, candidate_ids)
        evidence = self._validate_source_snapshot_bindings(
            graph_path,
            candidate_ids,
            source_snapshot_bindings,
        )
        connection = connect_sqlite(graph_path, readonly=True)
        try:
            approval = connection.execute(
                """SELECT approval_id,approval_reference,approval_type,scope_json,
                          approved_at,expires_at
                   FROM governance_approval
                   WHERE (approval_id=? OR approval_reference=?)
                     AND status='approved' AND active=1
                   ORDER BY approved_at DESC LIMIT 1""",
                (decision["approval_id"], decision["approval_id"]),
            ).fetchone()
            if not approval:
                raise RuntimeError(
                    "UWG promotion requires an active graph governance approval"
                )
            UniversalWriteGateway._validate_exact_approval_scope(
                approval,
                decision=decision,
                candidate_ids=candidate_ids,
                source_snapshot_bindings=evidence["source_snapshot_bindings"],
            )
        finally:
            connection.close()
        evidence["approval_reference"] = approval["approval_reference"]
        return evidence

    @staticmethod
    def _validate_approval_registration(
        request: dict[str, Any],
        active: dict[str, Any],
        authorization_context: AuthorizationContext | None,
        *,
        request_file_sha256: str,
    ) -> tuple[list[str], list[dict[str, str]], dict[str, Any]]:
        """Validate the sealed approval artifact and its signed operator binding.

        The approval artifact is intentionally not a free-form SQL payload. It
        can authorize one current candidate pair, one immutable list of
        candidate assertions, and one immutable list of source snapshots only.
        The caller must present a fresh RS256-backed authorization context
        whose subject and exact resource claims agree with the artifact.
        """

        if not _same_pair(request["candidate_pair"], active):
            raise RuntimeError(
                "UWG governance approval request is stale relative to the active candidate pair"
            )
        candidate_ids = sorted(request["candidate_assertion_ids"])
        if request["candidate_assertion_ids"] != candidate_ids:
            raise ValueError(
                "UWG governance approval candidate assertion IDs must be sorted"
            )
        snapshots = UniversalWriteGateway._canonical_source_snapshot_bindings(
            request["source_snapshot_bindings"]
        )
        if request["source_snapshot_bindings"] != snapshots:
            raise ValueError(
                "UWG governance approval source snapshot bindings must be canonically sorted"
            )
        if authorization_context is None:
            raise PermissionError(
                "UWG governance approval registration requires signed authorization"
            )
        authorization_context.require("knowledge:release", "knowledge:governance")
        if request["approved_by"] != authorization_context.subject:
            raise PermissionError(
                "UWG governance approval approved_by must match the signed authorization subject"
            )
        if (
            active["production_qualified"]
            and authorization_context.approved_model_manifest_digest
            != active["model_manifest_digest"]
        ):
            raise PermissionError(
                "UWG governance approval authorization is not bound to the active BGE-M3 model"
            )
        authorization_context.require_resources(
            {
                "uwg_governance_approval": request_file_sha256,
                "active_generation": sha256_bytes(
                    active["generation_id"].encode("utf-8")
                ),
                "uwg_policy": sha256_file(UWG_PROMOTION_POLICY_PATH),
                "release_approval": sha256_bytes(
                    request["approval_id"].encode("utf-8")
                ),
            }
        )
        approved_at = _utc_timestamp(
            request["approved_at"], label="UWG governance approval approved_at"
        )
        expires_at = _utc_timestamp(
            request["expires_at"], label="UWG governance approval expires_at"
        )
        now = dt.datetime.now(dt.timezone.utc)
        if approved_at > now:
            raise RuntimeError("UWG governance approval is not yet effective")
        if expires_at <= now:
            raise RuntimeError("UWG governance approval is expired")
        if expires_at <= approved_at:
            raise ValueError("UWG governance approval expiry must follow approval time")
        scope = UniversalWriteGateway._exact_approval_scope(
            promotion_id=request["promotion_id"],
            candidate_ids=candidate_ids,
            source_snapshot_bindings=snapshots,
        )
        return candidate_ids, snapshots, scope

    def _stage_governance_approval(
        self,
        *,
        generation_id: str,
        expected_active: dict[str, Any],
        request: dict[str, Any],
        scope: dict[str, Any],
        source_ids: list[str],
        authorization_context: AuthorizationContext,
    ) -> dict[str, Any]:
        """Insert one immutable, audited governance approval in a copied graph."""

        graph_path = self._require_fresh_staging_graph(
            generation_id=generation_id, expected_active=expected_active
        )
        candidate_ids = sorted(request["candidate_assertion_ids"])
        snapshots = self._canonical_source_snapshot_bindings(
            request["source_snapshot_bindings"]
        )
        expected_scope = self._exact_approval_scope(
            promotion_id=request["promotion_id"],
            candidate_ids=candidate_ids,
            source_snapshot_bindings=snapshots,
        )
        if scope != expected_scope:
            raise RuntimeError("UWG governance approval scope drifted before staging")
        fresh_evidence = self._validate_source_snapshot_bindings(
            self.manager._resolve(expected_active["graph_path"]), candidate_ids, snapshots
        )
        self._validate_candidate_fact_promotion_governance(
            self.manager._resolve(expected_active["graph_path"]), candidate_ids
        )
        if sorted(fresh_evidence["source_ids"]) != sorted(source_ids):
            raise RuntimeError("UWG governance approval source scope drifted before staging")
        before_hash = sha256_file(graph_path)
        write_lock = GLOBAL_WRITE_SERIALIZER.get_lock(str(graph_path))
        with write_lock:
            guard = MutationGuard(graph_path, label=f"uwg-approval:{request['approval_id']}")
            connection = connect_sqlite(graph_path)
            now = utc_now()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """SELECT approval_id,approval_reference FROM governance_approval
                       WHERE approval_id=? OR approval_reference=?""",
                    (request["approval_id"], request["approval_reference"]),
                ).fetchall()
                if existing:
                    raise RuntimeError(
                        "UWG governance approval ID or reference already exists in the candidate graph"
                    )
                scope_json = json.dumps(scope, ensure_ascii=False, sort_keys=True)
                request_digest = request["canonical_digest"]["value"]
                connection.execute(
                    """INSERT INTO governance_approval
                       (approval_id,approval_reference,approval_type,scope_json,baseline_zip_sha256,
                        baseline_graph_sha256,proposal_sha256,decision_record_sha256,card_proposal_sha256,
                        gate_o1_sha256,approval_statement_sha256,approved_at,expires_at,status,approved_by,
                        release_version,active,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        request["approval_id"],
                        request["approval_reference"],
                        "ingestion_promotion",
                        scope_json,
                        None,
                        expected_active["graph_sha256"],
                        request_digest,
                        request_digest,
                        None,
                        None,
                        request_digest,
                        request["approved_at"],
                        request["expires_at"],
                        "approved",
                        request["approved_by"],
                        request["release_version"],
                        1,
                        now,
                    ),
                )
                approval_record = {
                    "approval_id": request["approval_id"],
                    "approval_reference": request["approval_reference"],
                    "approval_type": "ingestion_promotion",
                    "approval_scope_digest": digest(scope),
                    "request_digest": request_digest,
                    "candidate_assertion_ids": request["candidate_assertion_ids"],
                    "source_snapshot_binding_digest": digest(
                        request["source_snapshot_bindings"]
                    ),
                    "authorization_context_digest": authorization_context.context_digest,
                }
                connection.execute(
                    """INSERT INTO audit_event
                       (audit_event_id,actor_or_process,action,record_type,record_id,previous_value_json,
                        new_value_json,source_id,event_timestamp,reason,validation_reference,
                        approval_reference,migration_or_ingestion_run_id,release_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        stable_id(
                            "audit-uwg-governance-approval",
                            request["approval_id"],
                            request_digest,
                            length=28,
                        ),
                        "nee-universal-write-gateway",
                        "register_uwg_exact_scope_approval",
                        "governance_approval",
                        request["approval_id"],
                        None,
                        json.dumps(approval_record, ensure_ascii=False, sort_keys=True),
                        source_ids[0] if len(source_ids) == 1 else None,
                        now,
                        "Signed operator registration for exact UWG candidate and source scope.",
                        None,
                        request["approval_reference"],
                        None,
                        request["release_version"],
                    ),
                )
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        after_hash = sha256_file(graph_path)
        integrity = sqlite_integrity(graph_path)
        if (
            before_hash == after_hash
            or integrity["integrity_check"] != "ok"
            or integrity["foreign_key_violation_count"]
        ):
            raise RuntimeError("UWG governance approval graph write validation failed")
        physical_proof = guard.finalize(
            record_ids=[request["approval_id"]],
            audit_events=1,
        )
        return {
            "status": "COMMITTED",
            "operation_count": 1,
            "approval_id": request["approval_id"],
            "approval_scope_digest": digest(scope),
            "graph_sha256_before": before_hash,
            "graph_sha256_after": after_hash,
            "integrity": integrity,
            "committed_at": now,
            "physical_proof": physical_proof,
        }

    def register_governance_approval(
        self,
        approval_request_path: Path,
        *,
        backend: EmbeddingBackend,
        authorization_context: AuthorizationContext | None,
    ) -> dict[str, Any]:
        """Prepare an immutable pair containing one signed UWG approval row.

        This is the only UWG-supported way to install the active graph approval
        required before evaluation. It never edits the active graph in place;
        operators must explicitly activate the returned paired-release receipt.
        """

        request_file = Path(approval_request_path).expanduser().resolve()
        request = read_json(request_file)
        self._assert_contract(request, UWG_GOVERNANCE_APPROVAL_CONTRACT)
        request_file_sha256 = sha256_file(request_file)
        self.manager._assert_mutable_layout()
        with file_lock(self.manager.lock_path):
            active = self.manager.active_pair()
            _assert_backend_matches(active, backend)
            candidate_ids, snapshots, scope = self._validate_approval_registration(
                request,
                active,
                authorization_context,
                request_file_sha256=request_file_sha256,
            )
            assert authorization_context is not None
            evidence = self._validate_source_snapshot_bindings(
                self.manager._resolve(active["graph_path"]),
                candidate_ids,
                snapshots,
            )
            self._validate_candidate_fact_promotion_governance(
                self.manager._resolve(active["graph_path"]), candidate_ids
            )
            self._candidate_sidecar_records(active, candidate_ids)
            generation_id = stable_id(
                "generation-uwg-governance-approval",
                active["generation_id"],
                request["canonical_digest"]["value"],
                backend.backend_id,
                backend.model_revision,
                backend.model_manifest_digest,
                length=28,
            )
            generation_dir = self.manager.generations_dir / generation_id
            receipt_path = (
                self.manager.releases_dir
                / f"uwg-governance-approval-{generation_id}.json"
            )
            self.manager._assert_mutable_layout(generation_dir, receipt_path)
            if receipt_path.exists():
                existing = read_json(receipt_path)
                _verify_sealed_receipt(existing, status="PREPARED")
                registration = existing.get("uwg", {}).get("approval_registration", {})
                if (
                    existing.get("release_kind")
                    != "uwg_governance_approval_registration"
                    or registration.get("request_digest")
                    != request["canonical_digest"]["value"]
                    or registration.get("approval_id") != request["approval_id"]
                ):
                    raise RuntimeError(
                        "UWG governance approval receipt identity collides with different inputs"
                    )
                self.manager.validate_pair(existing["next_pair"])
                return {**existing, "receipt_path": str(receipt_path)}
            if generation_dir.exists():
                raise FileExistsError(
                    f"incomplete UWG governance approval generation exists: {generation_dir}"
                )
            self.manager._verify_backend_snapshot(backend)
            generation_dir.mkdir(parents=True)
            try:
                graph_path = generation_dir / "graph.sqlite"
                sidecar_path = generation_dir / "sidecar.sqlite"
                exact_file_copy(self.manager._resolve(active["graph_path"]), graph_path)
                graph_receipt = self._stage_governance_approval(
                    generation_id=generation_id,
                    expected_active=active,
                    request=request,
                    scope=scope,
                    source_ids=evidence["source_ids"],
                    authorization_context=authorization_context,
                )
                label_quality_validation = assert_label_quality_no_regression(
                    self.manager._resolve(active["graph_path"]),
                    graph_path,
                )
                graph_hash = sha256_file(graph_path)
                graph_snapshot = stable_id("graph-snapshot", graph_hash, length=24)
                active_sidecar_path = (
                    self.manager._resolve(active["sidecar_path"]) if active.get("sidecar_path") else None
                )
                index_receipt = build_sidecar(
                    graph_path=graph_path,
                    sidecar_path=sidecar_path,
                    generation_id=generation_id,
                    graph_snapshot=graph_snapshot,
                    backend=backend,
                    prior_sidecar_path=active_sidecar_path,
                )
                self.manager._verify_backend_snapshot(backend)
                next_pair = {
                    "schema_version": "1.0.0",
                    "generation_id": generation_id,
                    "graph_snapshot": graph_snapshot,
                    "graph_sha256": graph_hash,
                    "sidecar_sha256": index_receipt["sidecar_sha256"],
                    "graph_path": self.manager._relative(graph_path),
                    "sidecar_path": self.manager._relative(sidecar_path),
                    "embedding_backend": backend.backend_id,
                    "model_id": backend.model_id,
                    "model_revision": backend.model_revision,
                    "model_manifest_digest": backend.model_manifest_digest,
                    "production_qualified": backend.production_qualified,
                    "previous_generation_id": active["generation_id"],
                    "activated_at": None,
                }
                pair_validation = self.manager.validate_pair(next_pair)
                write_json(generation_dir / "pair_manifest.json", next_pair)
                archived_request = self._archive_artifact(self.approvals_dir, request)
                receipt = _seal_receipt(
                    {
                        "schema_version": "1.0.0",
                        "receipt_id": stable_id(
                            "uwg-governance-approval-receipt", generation_id, length=28
                        ),
                        "status": "PREPARED",
                        "release_kind": "uwg_governance_approval_registration",
                        "run_id": stable_id(
                            "uwg-governance-approval", request["approval_id"], length=28
                        ),
                        "trace_id": digest(
                            ["uwg-governance-approval", request["approval_id"], generation_id]
                        )[:32],
                        "source_id": evidence["source_ids"][0],
                        "source_ids": evidence["source_ids"],
                        "approval_id": request["approval_id"],
                        "previous_pair": active,
                        "next_pair": next_pair,
                        "graph_write_receipt": graph_receipt,
                        "label_quality_validation": label_quality_validation,
                        "embedding_generation_receipt": index_receipt,
                        "pair_validation": pair_validation,
                        "production_qualified": backend.production_qualified,
                        "uwg": {
                            "approval_registration": {
                                "approval_id": request["approval_id"],
                                "approval_reference": request["approval_reference"],
                                "request_digest": request["canonical_digest"]["value"],
                                "request_archive": self.manager._relative(archived_request),
                                "approval_scope_digest": digest(scope),
                                "candidate_assertion_ids": candidate_ids,
                                "source_snapshot_bindings": evidence["source_snapshot_bindings"],
                            },
                            "w0_audit": {"status": "PASS", "operation_count": 1},
                            "e0_audit": {
                                "status": "PASS",
                                "sidecar_generation_id": generation_id,
                                "record_count": index_receipt["record_count"],
                            },
                        },
                        "authorization": authorization_context.audit_record(),
                        "prepared_at": utc_now(),
                    }
                )
                write_json_exclusive(receipt_path, receipt)
                return {**receipt, "receipt_path": str(receipt_path)}
            except Exception:
                if generation_dir.exists():
                    shutil.rmtree(generation_dir, ignore_errors=True)
                raise

    def _stage_graph_promotion(
        self,
        *,
        generation_id: str,
        expected_active: dict[str, Any],
        candidate_ids: list[str],
        evaluation: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        graph_path = self._require_fresh_staging_graph(
            generation_id=generation_id, expected_active=expected_active
        )
        self._validate_evaluation_policy(evaluation)
        scoped_ids = self._validate_decision_scope(evaluation, decision, expected_active)
        if sorted(candidate_ids) != scoped_ids:
            raise RuntimeError("UWG staging promotion candidate scope drifted")
        evidence = self._validate_evidence_bindings(
            self.manager._resolve(expected_active["graph_path"]),
            scoped_ids,
            decision,
            evaluation,
        )
        before_hash = sha256_file(graph_path)
        write_lock = GLOBAL_WRITE_SERIALIZER.get_lock(str(graph_path))
        with write_lock:
            guard = MutationGuard(graph_path, label=f"uwg-promotion:{decision['promotion_id']}")
            connection = connect_sqlite(graph_path)
            operations: list[dict[str, Any]] = []
            now = utc_now()
            try:
                connection.execute("BEGIN IMMEDIATE")
                validation_state = approved_validation_state(connection)
                for assertion_id in candidate_ids:
                    operations.append(_promote_single_candidate(connection, assertion_id, validation_state, evidence, decision, evaluation, now))
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"UWG staging graph has {len(violations)} foreign-key violations")
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        integrity = sqlite_integrity(graph_path)
        after_hash = sha256_file(graph_path)
        if integrity["integrity_check"] != "ok" or integrity["foreign_key_violation_count"] or before_hash == after_hash:
            raise RuntimeError("UWG graph promotion did not produce a valid changed graph")
        # The staging graph is a private copy: no concurrent writer can touch it between
        # the commit above and this proof, so the audit_event row delta is exact.
        physical_proof = guard.finalize(
            record_ids=[op["assertion_id"] for op in operations],
            audit_events=len(operations),
            operation_targets=[("promote_assertion", op["assertion_id"]) for op in operations],
        ) if operations else dict(NOT_APPLICABLE_NO_OPERATIONS)
        return {
            "status": "COMMITTED",
            "transaction_required": True,
            "operation_count": len(operations),
            "operations": operations,
            "graph_sha256_before": before_hash,
            "graph_sha256_after": after_hash,
            "integrity": integrity,
            "committed_at": now,
            "physical_proof": physical_proof,
        }

    def _archive_artifact(self, directory: Path, value: dict[str, Any]) -> Path:
        target = self._assert_runtime_target(
            directory / f"{value['artifact_id']}-{value['canonical_digest']['value'][:16]}.json",
            label="UWG artifact archive",
        )
        if target.exists():
            existing = read_json(target)
            self._assert_contract(existing, value["contract_id"])
            if existing["canonical_digest"]["value"] != value["canonical_digest"]["value"]:
                raise RuntimeError(
                    "UWG immutable artifact archive path collides with different content"
                )
            return target
        write_json_exclusive(target, value)
        return target

    def prepare(
        self,
        evaluation_path: Path,
        decision_path: Path,
        *,
        backend: EmbeddingBackend,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        """Prepare an immutable promotion generation; it does not activate it."""

        evaluation_file = Path(evaluation_path).expanduser().resolve()
        decision_file = Path(decision_path).expanduser().resolve()
        evaluation = read_json(evaluation_file)
        decision = read_json(decision_file)
        self._assert_contract(evaluation, "urn:nee:dual-pipeline:uwg-runtime-evaluation:1.0.0")
        self._assert_contract(decision, "urn:nee:dual-pipeline:uwg-promotion-decision:1.0.0")
        self._validate_evaluation_policy(evaluation)
        active = self.manager.active_pair()
        _assert_backend_matches(active, backend)
        candidate_ids = self._validate_decision_scope(evaluation, decision, active)
        self.manager._require_release_authorization(
            backend.production_qualified,
            authorization_context,
            model_manifest_digest=backend.model_manifest_digest,
            resources={
                "uwg_evaluation": sha256_file(evaluation_file),
                "uwg_promotion_decision": sha256_file(decision_file),
                "uwg_policy": sha256_file(UWG_PROMOTION_POLICY_PATH),
                "release_approval": sha256_bytes(decision["approval_id"].encode("utf-8")),
            },
        )
        self._candidate_sidecar_records(active, candidate_ids)
        evidence = self._validate_evidence_bindings(
            self.manager._resolve(active["graph_path"]), candidate_ids, decision, evaluation
        )
        generation_id = stable_id(
            "generation-uwg",
            active["generation_id"],
            evaluation["canonical_digest"]["value"],
            decision["canonical_digest"]["value"],
            backend.backend_id,
            backend.model_revision,
            backend.model_manifest_digest,
            length=28,
        )
        generation_dir = self.manager.generations_dir / generation_id
        receipt_path = self.manager.releases_dir / f"uwg-release-{generation_id}.json"
        self.manager._assert_mutable_layout(generation_dir, receipt_path)
        if receipt_path.exists():
            existing = read_json(receipt_path)
            _verify_sealed_receipt(existing, status="PREPARED")
            if (
                existing.get("uwg", {}).get("evaluation_digest")
                != evaluation["canonical_digest"]["value"]
                or existing.get("uwg", {}).get("promotion_decision_digest")
                != decision["canonical_digest"]["value"]
            ):
                raise RuntimeError("UWG release receipt identity collides with different inputs")
            self.manager.validate_pair(existing["next_pair"])
            return {**existing, "receipt_path": str(receipt_path)}
        if generation_dir.exists():
            raise FileExistsError(f"incomplete UWG generation exists: {generation_dir}")
        self.manager._verify_backend_snapshot(backend)
        generation_dir.mkdir(parents=True)
        try:
            graph_path = generation_dir / "graph.sqlite"
            sidecar_path = generation_dir / "sidecar.sqlite"
            exact_file_copy(self.manager._resolve(active["graph_path"]), graph_path)
            graph_receipt = self._stage_graph_promotion(
                generation_id=generation_id,
                expected_active=active,
                candidate_ids=candidate_ids,
                evaluation=evaluation,
                decision=decision,
            )
            label_quality_validation = assert_label_quality_no_regression(
                self.manager._resolve(active["graph_path"]),
                graph_path,
            )
            graph_hash = sha256_file(graph_path)
            graph_snapshot = stable_id("graph-snapshot", graph_hash, length=24)
            active_sidecar_path = (
                self.manager._resolve(active["sidecar_path"]) if active.get("sidecar_path") else None
            )
            index_receipt = build_sidecar(
                graph_path=graph_path,
                sidecar_path=sidecar_path,
                generation_id=generation_id,
                graph_snapshot=graph_snapshot,
                backend=backend,
                prior_sidecar_path=active_sidecar_path,
            )
            self.manager._verify_backend_snapshot(backend)
            next_pair = {
                "schema_version": "1.0.0",
                "generation_id": generation_id,
                "graph_snapshot": graph_snapshot,
                "graph_sha256": graph_hash,
                "sidecar_sha256": index_receipt["sidecar_sha256"],
                "graph_path": self.manager._relative(graph_path),
                "sidecar_path": self.manager._relative(sidecar_path),
                "embedding_backend": backend.backend_id,
                "model_id": backend.model_id,
                "model_revision": backend.model_revision,
                "model_manifest_digest": backend.model_manifest_digest,
                "production_qualified": backend.production_qualified,
                "previous_generation_id": active["generation_id"],
                "activated_at": None,
            }
            pair_validation = self.manager.validate_pair(next_pair)
            self._validate_promoted_sidecar(next_pair, candidate_ids)
            write_json(generation_dir / "pair_manifest.json", next_pair)
            archived_evaluation = self._archive_artifact(self.evaluations_dir, evaluation)
            archived_decision = self._archive_artifact(self.decisions_dir, decision)
            receipt = _seal_receipt(
                {
                    "schema_version": "1.0.0",
                    "receipt_id": stable_id("uwg-release-receipt", generation_id, length=28),
                    "status": "PREPARED",
                    "release_kind": "uwg_promotion",
                    "run_id": decision["origin_run_id"],
                    "trace_id": digest(["uwg", decision["promotion_id"], generation_id])[:32],
                    "source_id": evidence["source_ids"][0],
                    "source_ids": evidence["source_ids"],
                    "approval_id": decision["approval_id"],
                    "previous_pair": active,
                    "next_pair": next_pair,
                    "graph_write_receipt": graph_receipt,
                    "label_quality_validation": label_quality_validation,
                    "embedding_generation_receipt": index_receipt,
                    "pair_validation": pair_validation,
                    "production_qualified": backend.production_qualified,
                    "uwg": {
                        "evaluation_id": evaluation["evaluation_id"],
                        "evaluation_digest": evaluation["canonical_digest"]["value"],
                        "evaluation_archive": self.manager._relative(archived_evaluation),
                        "promotion_id": decision["promotion_id"],
                        "promotion_decision_digest": decision["canonical_digest"]["value"],
                        "promotion_decision_archive": self.manager._relative(archived_decision),
                        "candidate_assertion_ids": candidate_ids,
                        "direct_evidence_bindings": evidence["bindings"],
                        "source_snapshot_bindings": evidence["source_snapshot_bindings"],
                        "w0_audit": {"status": "PASS", "operation_count": len(candidate_ids)},
                        "e0_audit": {
                            "status": "PASS",
                            "sidecar_generation_id": generation_id,
                            "record_count": index_receipt["record_count"],
                        },
                    },
                    "authorization": (
                        authorization_context.audit_record() if authorization_context else None
                    ),
                    "prepared_at": utc_now(),
                }
            )
            write_json_exclusive(receipt_path, receipt)
            return {**receipt, "receipt_path": str(receipt_path)}
        except Exception:
            if generation_dir.exists():
                shutil.rmtree(generation_dir, ignore_errors=True)
            raise

    def _validate_promoted_sidecar(
        self, pair: dict[str, Any], candidate_ids: list[str]
    ) -> None:
        targets = set(candidate_ids)
        promoted: set[str] = set()
        connection = connect_sqlite(self.manager._resolve(pair["sidecar_path"]), readonly=True)
        try:
            for row in connection.execute("SELECT lane,artifact_json FROM retrieval_record"):
                artifact = json.loads(row["artifact_json"])
                assertion_id = artifact.get("extensions", {}).get(
                    "com.partner_advisory.nee.graph-object-id"
                )
                if assertion_id not in targets:
                    continue
                if row["lane"] != "approved":
                    raise RuntimeError(
                        "UWG promoted assertion remained outside the approved sidecar lane: "
                        f"{assertion_id}"
                    )
                promoted.add(assertion_id)
        finally:
            connection.close()
        if promoted != targets:
            raise RuntimeError("UWG promoted sidecar does not contain every approved target")

    def _receipt_path(self, receipt_path: Path) -> Path:
        return self._assert_runtime_target(
            Path(receipt_path).expanduser().absolute(), label="UWG release receipt"
        )

    def _activation_path(self, generation_id: str) -> Path:
        return self._assert_runtime_target(
            self.manager.releases_dir / f"uwg-activation-{generation_id}.json",
            label="UWG activation receipt",
        )

    @staticmethod
    def _verify_activation(value: dict[str, Any], receipt: dict[str, Any]) -> None:
        _verify_sealed_receipt(value, status="ACTIVE")
        active_pair = value.get("active_pair")
        if (
            value.get("receipt_id") != receipt.get("receipt_id")
            or value.get("release_kind") != receipt.get("release_kind")
            or not isinstance(active_pair, dict)
            or not _same_pair(active_pair, receipt["next_pair"])
        ):
            raise RuntimeError("UWG activation receipt is not bound to its prepared release")

    def activate(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        """Atomically activate a prepared UWG pair without rerunning intake."""

        receipt_file = self._receipt_path(receipt_path)
        receipt = read_json(receipt_file)
        _verify_sealed_receipt(receipt, status="PREPARED")
        release_action_resources(receipt, "activate")
        from ..retrieval_release import validate_activation_evaluation

        retrieval_evaluation = validate_activation_evaluation(
            self.manager.runtime_root, receipt
        )
        self.manager._require_receipt_action_authorization(
            receipt,
            authorization_context,
            action="activate",
            additional_resources=(
                {
                    "retrieval_release_evaluation": retrieval_evaluation[
                        "evaluation_digest"
                    ]
                }
                if retrieval_evaluation.get("required")
                else None
            ),
        )
        self.manager.validate_pair(receipt["next_pair"])
        activation_path = self._activation_path(receipt["next_pair"]["generation_id"])
        self.manager._assert_mutable_layout(receipt_file, activation_path)
        with file_lock(self.manager.lock_path):
            current = self.manager.active_pair()
            if activation_path.exists():
                existing = read_json(activation_path)
                self._verify_activation(existing, receipt)
                if _same_pair(current, receipt["previous_pair"]):
                    write_json(self.manager.active_path, existing["active_pair"])
                elif not _same_pair(current, receipt["next_pair"]):
                    raise RuntimeError("UWG activation receipt conflicts with the active pair")
                return existing
            if not _same_pair(current, receipt["previous_pair"]):
                raise RuntimeError("active pair changed after UWG release preparation")
            active_pair = dict(receipt["next_pair"])
            active_pair["activated_at"] = utc_now()
            activation = _seal_receipt(
                {
                    "schema_version": "1.0.0",
                    "activation_id": stable_id(
                        "uwg-activation", receipt["receipt_id"], length=28
                    ),
                    "status": "ACTIVE",
                    "release_kind": receipt["release_kind"],
                    "receipt_id": receipt["receipt_id"],
                    "run_id": receipt["run_id"],
                    "previous_generation_id": receipt["previous_pair"]["generation_id"],
                    "active_pair": active_pair,
                    "uwg": receipt["uwg"],
                    "retrieval_release_evaluation": retrieval_evaluation,
                    "activated_at": active_pair["activated_at"],
                    "idempotent": False,
                    "authorization": (
                        authorization_context.audit_record() if authorization_context else None
                    ),
                }
            )
            write_json_exclusive(activation_path, activation)
            try:
                write_json(self.manager.active_path, active_pair)
            except Exception:
                if activation_path.exists():
                    activation_path.unlink()
                raise
            return activation

    def rollback(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        """Restore the exact predecessor pair for one activated UWG release."""

        receipt_file = self._receipt_path(receipt_path)
        receipt = read_json(receipt_file)
        _verify_sealed_receipt(receipt, status="PREPARED")
        release_action_resources(receipt, "rollback")
        self.manager._require_receipt_action_authorization(
            receipt, authorization_context, action="rollback"
        )
        self.manager.validate_pair(receipt["previous_pair"])
        rollback_path = self._assert_runtime_target(
            self.manager.releases_dir
            / f"uwg-rollback-{receipt['next_pair']['generation_id']}.json",
            label="UWG rollback receipt",
        )
        self.manager._assert_mutable_layout(receipt_file, rollback_path)
        with file_lock(self.manager.lock_path):
            if rollback_path.exists():
                existing = read_json(rollback_path)
                _verify_sealed_receipt(existing, status="ROLLED_BACK")
                return existing
            current = self.manager.active_pair()
            if not _same_pair(current, receipt["next_pair"]):
                raise RuntimeError("active pair is not the UWG generation being rolled back")
            restored = dict(receipt["previous_pair"])
            restored["activated_at"] = utc_now()
            rollback = _seal_receipt(
                {
                    "schema_version": "1.0.0",
                    "rollback_id": stable_id("uwg-rollback", receipt["receipt_id"], length=28),
                    "status": "ROLLED_BACK",
                    "release_kind": receipt["release_kind"],
                    "receipt_id": receipt["receipt_id"],
                    "run_id": receipt["run_id"],
                    "rolled_back_generation_id": receipt["next_pair"]["generation_id"],
                    "restored_pair": restored,
                    "uwg": receipt["uwg"],
                    "rolled_back_at": utc_now(),
                    "authorization": (
                        authorization_context.audit_record() if authorization_context else None
                    ),
                }
            )
            write_json(self.manager.active_path, restored)
            try:
                write_json_exclusive(rollback_path, rollback)
            except Exception:
                write_json(self.manager.active_path, current)
                raise
            return rollback

    def _dispatch_learning_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_type = request.get("request_type")
        if request_type == "learning_parameter_commit":
            return self.commit_learning_parameters(request)
        if request_type == "learning_parameter_rollback":
            rollback_arg = request.get("rollback_receipt") or request.get("rollback_path") or request
            return self.rollback_learning_parameters(rollback_arg)
        return None

    @staticmethod
    def _assert_not_mock(request: dict[str, Any]) -> None:
        if any(request.get(k) for k in ("is_mock", "mock", "short_circuit")):
            raise RuntimeError("MOCK_SYNTHESIS_NOT_AUTHORITATIVE: UWG commit rejected on mock/short-circuit.")

    def process_commit_request(
        self,
        request: dict[str, Any],
        *,
        backend: EmbeddingBackend | None = None,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        """Programmatic entrypoint for X3C_COMMIT_REQUEST_TO_UWG."""
        learning_res = self._dispatch_learning_request(request)
        if learning_res is not None:
            return learning_res

        self._assert_not_mock(request)

        receipt_path = request.get("receipt_path") or request.get("prepared_receipt_path")
        if receipt_path:
            return self.activate(
                self._receipt_path(Path(receipt_path)),
                authorization_context=authorization_context,
            )

        approval_request_path = request.get("approval_request_path") or request.get("approval_path")
        evaluation_path = request.get("evaluation_path")
        decision_path = request.get("decision_path")

        if not (approval_request_path or (evaluation_path and decision_path)):
            raise ValueError(
                "UWG commit request requires either 'receipt_path', 'approval_request_path', "
                "or both 'evaluation_path' and 'decision_path'."
            )

        if backend is None:
            active = self.manager.active_pair()
            backend = getattr(self.manager, "_backend_for_pair", lambda _: self.manager._backend())(active)

        if approval_request_path and not decision_path:
            return self.register_governance_approval(
                Path(approval_request_path),
                backend=backend,
                authorization_context=authorization_context,
            )

        if evaluation_path and decision_path:
            prepared_receipt = self.prepare(
                Path(evaluation_path),
                Path(decision_path),
                backend=backend,
                authorization_context=authorization_context,
            )
            return self.activate(
                self._receipt_path(Path(prepared_receipt["receipt_path"])),
                authorization_context=authorization_context,
            )

    @staticmethod
    def _parse_learning_proposal_and_approval(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
        from .learning_commit import _parse_learning_proposal_and_approval as _parse
        return _parse(request)

    @staticmethod
    def _resolve_and_validate_learning_params(proposal: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        from .learning_commit import _resolve_and_validate_learning_params as _resolve
        return _resolve(proposal, request)

    def _snapshot_active_for_learning(self) -> tuple[dict[str, Any] | None, str, str]:
        mgr = getattr(self, "manager", None)
        active_path = getattr(mgr, "active_path", None)
        if mgr is not None and active_path is not None and active_path.is_file():
            active = mgr.active_pair()
            return active, active.get("graph_sha256", "0" * 64), active.get("generation_id", "gen-active")
        return None, "0" * 64, "gen-unbootstrapped"

    def _verify_active_pair_unchanged(
        self,
        active_before: dict[str, Any] | None,
        graph_sha256: str,
        generation_id: str,
    ) -> None:
        if active_before is None:
            return
        active_after = self.manager.active_pair()
        if active_after.get("graph_sha256") != graph_sha256:
            raise RuntimeError("CRITICAL_INVARIANT_VIOLATION: graph_sha256 mutated during parameter commit")
        if active_after.get("generation_id") != generation_id:
            raise RuntimeError("CRITICAL_INVARIANT_VIOLATION: generation_id mutated during parameter commit")

    def commit_learning_parameters(self, request: dict[str, Any]) -> dict[str, Any]:
        """Governed UWG lane for atomic system learning parameter commits."""
        from .learning_commit import commit_learning_parameters as _commit
        return _commit(self, request)

    def rollback_learning_parameters(
        self,
        rollback_target: dict[str, Any] | str | Path,
    ) -> dict[str, Any]:
        """Executes exact bidirectional rollback using rollback receipt."""
        from .learning_commit import rollback_learning_parameters as _rollback
        return _rollback(self, rollback_target)


