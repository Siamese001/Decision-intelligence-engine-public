"""Single adapter over the existing QueryEngine retrieval/evidence runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .core import read_json


@dataclass(frozen=True)
class RetrievalExecution:
    query_result: dict[str, Any]
    evidence_packet: dict[str, Any]
    retrieval_report: dict[str, Any] | None


class RetrievalService:
    """Call QueryEngine exactly once and expose its already-persisted evidence."""

    def __init__(self, *, query_engine: Any, runtime_root: Path) -> None:
        self.query_engine = query_engine
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self._cached_topology: Any = None

    def execute(
        self,
        question: str,
        *,
        decision_frame: Mapping[str, Any] | None = None,
        request_context: Any | None = None,
        comparison_mode: bool = False,
        **query_kwargs: Any,
    ) -> RetrievalExecution:
        if self.query_engine is None:
            raise RuntimeError("the graph query runtime is not configured")
        import inspect
        sig = inspect.signature(self.query_engine.ask)
        kwargs = dict(query_kwargs)
        if decision_frame is not None:
            kwargs["decision_frame"] = decision_frame
        if request_context is not None:
            kwargs["request_context"] = request_context
        if comparison_mode:
            kwargs["comparison_mode"] = comparison_mode
        valid_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        result = dict(self.query_engine.ask(question, **valid_kwargs))
        run_id = result.get("run_id")
        evidence_path = self.runtime_root / "runs" / str(run_id) / "evidence_packet.json"
        evidence_packet = (
            read_json(evidence_path)
            if run_id is not None and evidence_path.is_file()
            else {
                "approved": [],
                "candidate": [],
                "external_public": [],
                "graph_paths": [],
                "counterevidence": [],
                "gaps": ["Query did not produce a decision evidence packet."],
            }
        )
        report = result.get("retrieval_evidence")
        return RetrievalExecution(
            query_result=result,
            evidence_packet=evidence_packet,
            retrieval_report=dict(report) if isinstance(report, Mapping) else None,
        )

    def get_topology(self) -> tuple[Any, dict[str, Any] | None]:
        active_pair_path = self.runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            return None, None
        try:
            active_pair = read_json(active_pair_path)
            graph_rel = active_pair.get("graph_path")
            if not graph_rel:
                return None, active_pair
            graph_path = (self.runtime_root / graph_rel).resolve()
            if not graph_path.is_file():
                return None, active_pair
            if (
                self._cached_topology is not None
                and self._cached_topology.graph_sha256 == active_pair.get("graph_sha256")
            ):
                return self._cached_topology, active_pair

            from .in_memory_graph import InMemoryGraphTopology

            topology = InMemoryGraphTopology.load_from_sqlite(
                graph_path,
                expected_sha256=active_pair.get("graph_sha256"),
            )
            self._cached_topology = topology
            return topology, active_pair
        except (ValueError, RuntimeError) as exc:
            if "sha256" in str(exc).lower() or "digest" in str(exc).lower() or "integrity" in str(exc).lower():
                raise
            return None, None
        except (OSError, KeyError):
            return None, None


__all__ = ["RetrievalExecution", "RetrievalService"]
