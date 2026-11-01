"""Core service implementation for decision routing, evidence, and frames."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core import stable_id, write_json
from ..decision_composer import compose_decision_markdown
from ..decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
    evaluate_decision,
)
from ..decision_models import (
    ResponseTerminalState,
)
from ..hitl_governance import DEFAULT_MARGIN_THRESHOLD
from ..query_router import QueryDestination, route_query
from ..retrieval_service import RetrievalService
from .envelope import build_response_envelope
from .handlers import (
    HandoffHandler,
    _clarification,
    _default_general_handler,
    _default_workflow_handler,
    _load_run_artifact,
)
from .programmatic import evaluate_programmatic_decision_plan


def _write_decision_artifacts(target_dir: Path, pack: Any, result: Any, witness: Any) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    write_json(target_dir / "decision_evidence_pack.json", pack)
    write_json(target_dir / "decision_result.json", result)
    if witness:
        write_json(target_dir / "decision_witness_subgraph.json", witness)
    (target_dir / "decision_result.md").write_text(
        compose_decision_markdown(result), encoding="utf-8"
    )


def _compute_request_id(
    question: str,
    destination_val: str,
    options: Any,
    criteria: Any,
    constraints: Any,
    assumptions: Any,
    time_horizon: Any,
    query_type: Any,
    destination: Any,
    materiality: Any,
    delivery_profile: Any,
    artifact_type: Any,
) -> str:
    return stable_id(
        "decision-service-request",
        question,
        destination_val,
        options if options is not None else [],
        criteria if criteria is not None else [],
        constraints if constraints is not None else [],
        assumptions if assumptions is not None else [],
        time_horizon if time_horizon is not None else "",
        query_type if query_type is not None else "",
        destination if destination is not None else "",
        materiality if materiality is not None else "",
        delivery_profile if delivery_profile is not None else "",
        artifact_type if artifact_type is not None else "",
        length=26,
    )


class DecisionService:
    """Execute one atomic route through one shared response contract."""

    def __init__(
        self,
        *,
        query_engine: Any,
        runtime_root: Path,
        general_handler: HandoffHandler | None = None,
        workflow_handler: HandoffHandler | None = None,
        query_defaults: Mapping[str, Any] | None = None,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self.query_engine = query_engine
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.general_handler = general_handler or _default_general_handler
        self.workflow_handler = workflow_handler or _default_workflow_handler
        self.query_defaults = dict(query_defaults or {})
        self.retrieval_service = retrieval_service or RetrievalService(
            query_engine=query_engine,
            runtime_root=self.runtime_root,
        )

    @property
    def _cached_topology(self) -> Any:
        return getattr(self.retrieval_service, "_cached_topology", None)

    @_cached_topology.setter
    def _cached_topology(self, value: Any) -> None:
        if self.retrieval_service is not None:
            self.retrieval_service._cached_topology = value

    def _get_topology(self) -> tuple[Any, dict[str, Any] | None]:
        if self.retrieval_service is not None:
            return self.retrieval_service.get_topology()
        return None, None

    @staticmethod
    def route(question: str, **route_kwargs: Any) -> dict[str, Any]:
        decision = route_query(question, **route_kwargs)
        return {
            "destination": decision.destination.value,
            "query_type": decision.query_type,
            "materiality": decision.materiality,
            "eligibility_status": decision.eligibility_status,
            "reason_codes": list(decision.reason_codes),
            "selected_handler": {
                QueryDestination.GENERAL_ASSISTANT_HANDOFF: "GeneralHandler",
                QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF: "WorkflowHandler",
                QueryDestination.GRAPH_EVIDENCE_UTILITY: "DirectEvidenceHandler",
                QueryDestination.NEE_DECISION_INTELLIGENCE: "DecisionHandler",
                QueryDestination.ROUTE_AMBIGUOUS: "ClarificationHandler",
            }[decision.destination],
            "retrieval_permitted": decision.enters_query_runtime,
            "provider_reasoning_permitted": (
                decision.destination is QueryDestination.NEE_DECISION_INTELLIGENCE
            ),
            "graph_traversal_permitted": decision.enters_query_runtime,
            "policy_digest": decision.policy_digest,
            "conflicting_destinations": list(decision.conflicting_destinations),
            "resolution_options": list(decision.resolution_options),
        }

    _envelope = staticmethod(build_response_envelope)

    @staticmethod
    def _clarification(decision_question: str, missing_fields: Sequence[str]) -> dict[str, Any]:
        return _clarification(decision_question, missing_fields)

    def _try_handoff(
        self, route: Any, question: str, request_id: str, started: float
    ) -> dict[str, Any] | None:
        if route.destination is QueryDestination.GENERAL_ASSISTANT_HANDOFF:
            handler, res = "GeneralHandler", dict(self.general_handler(question))
        elif route.destination is QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF:
            handler, res = "WorkflowHandler", dict(self.workflow_handler(question))
        else:
            return None
        return self._envelope(
            request_id=request_id,
            run_id=None,
            destination=route.destination.value,
            handler=handler,
            terminal_state=ResponseTerminalState.HANDOFF_READY,
            result=res,
            retrieval_call_count=0,
            provider_call_count=0,
            started=started,
        )

    def _try_fast_system_lookup(
        self, route: Any, question: str, request_id: str, started: float
    ) -> dict[str, Any] | None:
        if route.destination is not QueryDestination.GRAPH_EVIDENCE_UTILITY:
            return None
        if getattr(route, "query_type", "exact_lookup") != "exact_lookup":
            return None
        import re
        if re.search(
            r"\b(?:current\s+status|status\s+history|migration\s+status|project\s+status|blockers?|action\s+items?|who\s+owns|when\s+did|timeline|deadline)\b",
            question,
            re.IGNORECASE,
        ) and not re.search(
            r"\b(?:system\s+of\s+record|writeback|acronym|integration\s+posture|system\s+directory)\b",
            question,
            re.IGNORECASE,
        ):
            return None
        from ..system_directory import find_systems_in_text

        systems = find_systems_in_text(question)
        if not systems:
            return None
        top = systems[0]
        fast_result = {
            "status": "COMPLETED",
            "query_type": "exact_lookup",
            "system_id": top.system_id,
            "canonical_name": top.canonical_name,
            "acronym": top.acronym,
            "governing_project": top.primary_governing_project,
            "system_of_record_for": top.system_of_record_for,
            "domain_scope": top.domain_scope,
            "integration_posture": top.integration_posture,
            "writeback_posture": top.writeback_posture,
            "writeback_support": top.to_dict()["writeback_support"],
            "writeback_thesis": top.writeback_thesis,
            "citations": list(top.key_evidence_refs),
            "systems": [s.to_dict() for s in systems],
        }
        return self._envelope(
            request_id=request_id,
            run_id=None,
            destination=route.destination.value,
            handler="DirectEvidenceHandler",
            terminal_state=ResponseTerminalState.EVIDENCE_ANSWER_PRODUCED,
            result=fast_result,
            retrieval_call_count=0,
            provider_call_count=0,
            started=started,
        )

    def query(
        self,
        question: str,
        *,
        options: Sequence[str] | None = None,
        criteria: Sequence[str] | None = None,
        constraints: Sequence[str] | None = None,
        assumptions: Sequence[str] | None = None,
        time_horizon: str | None = None,
        query_type: str | None = None,
        output_dir: Path | None = None,
        destination: str | None = None,
        materiality: str | None = None,
        delivery_profile: str | None = None,
        artifact_type: str | None = None,
        **query_kwargs: Any,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        route = route_query(
            question,
            query_type_override=query_type,
            destination_override=destination,
            requested_materiality=materiality,
            delivery_profile=delivery_profile,
            artifact_type=artifact_type,
            authorized=True,
        )
        request_id = _compute_request_id(
            question,
            route.destination.value,
            options,
            criteria,
            constraints,
            assumptions,
            time_horizon,
            query_type,
            destination,
            materiality,
            delivery_profile,
            artifact_type,
        )
        handoff = self._try_handoff(route, question, request_id, started)
        if handoff is not None:
            return handoff
        if route.destination is QueryDestination.ROUTE_AMBIGUOUS:
            return self._envelope(
                request_id=request_id,
                run_id=None,
                destination=route.destination.value,
                handler="ClarificationHandler",
                terminal_state=ResponseTerminalState.CLARIFICATION_REQUIRED,
                result={
                    "question": question,
                    "missing_fields": ["route"],
                    "clarification_question": (
                        "Should this be a direct graph-evidence lookup, a decision, "
                        "general writing, or an operational workflow?"
                    ),
                    "conflicting_destinations": list(route.conflicting_destinations),
                    "resolution_options": list(route.resolution_options),
                },
                retrieval_call_count=0,
                provider_call_count=0,
                started=started,
            )

        frame: dict[str, Any] | None = None
        if route.destination is QueryDestination.NEE_DECISION_INTELLIGENCE:
            frame = build_decision_frame(
                question=question,
                options=options,
                criteria=criteria,
                constraints=constraints,
                assumptions=assumptions,
                time_horizon=time_horizon,
            )
            if frame["frame_state"] == "CLARIFICATION_REQUIRED":
                return self._envelope(
                    request_id=request_id,
                    run_id=None,
                    destination=route.destination.value,
                    handler="ClarificationHandler",
                    terminal_state=ResponseTerminalState.CLARIFICATION_REQUIRED,
                    result=self._clarification(question, frame["missing_fields"]),
                    retrieval_call_count=0,
                    provider_call_count=0,
                    started=started,
                )

        fast_lookup = self._try_fast_system_lookup(route, question, request_id, started)
        if fast_lookup is not None:
            return fast_lookup

        retrieval = self._execute_retrieval(
            question, route, output_dir, materiality, delivery_profile, artifact_type, frame, query_kwargs
        )
        result = retrieval.query_result
        run_id = result.get("run_id")
        provider_calls = int(result.get("total_external_model_call_count", 0) or 0)
        if route.destination is QueryDestination.GRAPH_EVIDENCE_UTILITY:
            is_completed = result.get("status") == "COMPLETED"
            terminal = (
                ResponseTerminalState.EVIDENCE_ANSWER_PRODUCED
                if is_completed
                else ResponseTerminalState.REQUEST_FAILED
            )
            return self._envelope(
                request_id=request_id,
                run_id=run_id,
                destination=route.destination.value,
                handler="DirectEvidenceHandler",
                terminal_state=terminal,
                result=result,
                retrieval_call_count=1,
                provider_call_count=provider_calls,
                started=started,
                error=None if is_completed else {"reason": result.get("x3_outcome", result.get("status"))},
            )

        assert frame is not None
        return self._evaluate_and_persist_decision(
            frame, retrieval, result, run_id, request_id, route, provider_calls, started, output_dir
        )

    def _execute_retrieval(
        self, question: str, route: Any, output_dir: Any, materiality: Any,
        delivery_profile: Any, artifact_type: Any, frame: Any, query_kwargs: dict[str, Any],
    ) -> Any:
        from ..model_registry import ModelTier

        engine_arguments = dict(self.query_defaults)
        engine_arguments.setdefault("model_tier", ModelTier.QUERY_REASONING.value)
        engine_arguments.update(query_kwargs)
        if frame is not None:
            engine_arguments["decision_frame"] = frame
        return self.retrieval_service.execute(
            question,
            query_type=route.query_type,
            output_dir=output_dir,
            materiality=materiality,
            destination=route.destination.value,
            delivery_profile=delivery_profile,
            artifact_type=artifact_type,
            **engine_arguments,
        )

    def _evaluate_and_persist_decision(
        self, frame: dict[str, Any], retrieval: Any, result: dict[str, Any],
        run_id: Any, request_id: str, route: Any, provider_calls: int, started: float,
        output_dir: Any,
    ) -> dict[str, Any]:
        run_dir = self.runtime_root / "runs" / str(run_id)
        evidence_pack = build_decision_evidence_pack(
            query_result=result,
            evidence_packet=retrieval.evidence_packet,
            retrieval_report=retrieval.retrieval_report,
        )
        allow_recommendation = bool(
            result.get("status") == "COMPLETED"
            and result.get("answer_delivery") == "grounded_synthesis"
            and result.get("synthesis_status") == "completed"
        )
        topology, active_pair = self._get_topology()
        decision_result = evaluate_decision(
            frame=frame,
            evidence_pack=evidence_pack,
            answer=result.get("answer") if isinstance(result.get("answer"), Mapping) else None,
            execution={
                "query_run_id": run_id,
                "query_status": result.get("status"),
                "x3_outcome": result.get("x3_outcome"),
                "answer_delivery": result.get("answer_delivery"),
                "l2_disposition": result.get("l2_disposition"),
                "l2_reason_code": result.get("l2_reason_code"),
                "provider_failure_code": result.get("provider_failure_code"),
                "provider_call_count": provider_calls,
                "graph_sha256_before": result.get("graph_sha256_before"),
                "graph_sha256_after": result.get("graph_sha256_after"),
            },
            allow_recommendation=allow_recommendation,
            query_type=route.query_type,
            topology=topology,
            active_pair=active_pair,
        )
        witness = decision_result.get("extensions", {}).get("com.partner_advisory.nee.decision-witness-subgraph")
        if run_id is not None:
            _write_decision_artifacts(run_dir / "decision_results" / request_id, evidence_pack, decision_result, witness)
        if output_dir is not None:
            _write_decision_artifacts(Path(output_dir).expanduser().resolve(), evidence_pack, decision_result, witness)
        return self._envelope(
            request_id=request_id,
            run_id=run_id,
            destination=route.destination.value,
            handler="DecisionHandler",
            terminal_state=decision_result["terminal_state"],
            result=decision_result,
            retrieval_call_count=1,
            provider_call_count=provider_calls,
            started=started,
            error={"stage": "L2", "reason_code": result.get("provider_failure_code")} if result.get("provider_failure_code") else None,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return _load_run_artifact(self.runtime_root, run_id)

    def evaluate_programmatic_decision(
        self,
        decision_question: str,
        options: Sequence[Mapping[str, Any]],
        criteria: Sequence[Mapping[str, Any]],
        evidence_pack: Mapping[str, Any],
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    ) -> dict[str, Any]:
        """Programmatic entrypoint for dialectical trade-off matrix evaluation (PDME)."""
        return evaluate_programmatic_decision_plan(
            decision_question=decision_question,
            options=options,
            criteria=criteria,
            evidence_pack=evidence_pack,
            margin_threshold=margin_threshold,
        )


__all__ = ["DecisionService"]
