"""Lazy proxy decision service deferring heavy provider and graph initialization."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any, Callable

from ..query_router import QueryDestination, route_query
from .handlers import HandoffHandler
from .service import DecisionService


class LazyDecisionService:
    """Keep provider and retrieval initialization behind the graph-route boundary."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        runtime_service_factory: Callable[[], DecisionService],
        general_handler: HandoffHandler | None = None,
        workflow_handler: HandoffHandler | None = None,
    ) -> None:
        self._light_service = DecisionService(
            query_engine=None,
            runtime_root=runtime_root,
            general_handler=general_handler,
            workflow_handler=workflow_handler,
        )
        self.runtime_root = self._light_service.runtime_root
        self._runtime_service_factory = runtime_service_factory
        self._runtime_service: DecisionService | None = None
        self._runtime_service_lock = Lock()

    @staticmethod
    def route(question: str, **route_kwargs: Any) -> dict[str, Any]:
        return DecisionService.route(question, **route_kwargs)

    def _get_runtime_service(self) -> DecisionService:
        if self._runtime_service is None:
            with self._runtime_service_lock:
                if self._runtime_service is None:
                    self._runtime_service = self._runtime_service_factory()
        return self._runtime_service

    def query(self, question: str, **query_kwargs: Any) -> dict[str, Any]:
        route = route_query(
            question,
            query_type_override=query_kwargs.get("query_type"),
            destination_override=query_kwargs.get("destination"),
            requested_materiality=query_kwargs.get("materiality"),
            delivery_profile=query_kwargs.get("delivery_profile"),
            artifact_type=query_kwargs.get("artifact_type"),
            authorized=True,
        )
        if route.enters_query_runtime:
            from ..system_directory import find_systems_in_text

            if (
                route.destination is QueryDestination.GRAPH_EVIDENCE_UTILITY
                and find_systems_in_text(question)
            ):
                return self._light_service.query(question, **query_kwargs)
            return self._get_runtime_service().query(question, **query_kwargs)
        return self._light_service.query(question, **query_kwargs)

    def evaluate_programmatic_decision(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._get_runtime_service().evaluate_programmatic_decision(*args, **kwargs)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._light_service.get_run(run_id)


__all__ = ["LazyDecisionService"]
