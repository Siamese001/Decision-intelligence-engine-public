#!/usr/bin/env python3
"""
Pipeline B: Slow Path Transactional Worker (Laws 1, 2, 4, 5 Enforcement)
Target: Apple MacBook Pro 16" (M5 Pro, 48GB RAM, Apple Metal)

Consumes items from `durable_work_queue` with idempotency, aggregates evidence
records into cohesive discourse windows (30s-90s), executes the Compute Fork
(`CLIENT_SAFE` vs `INTERNAL_ADVISORY`), runs Quality & Stealth Judges with
Incremental Entity Resolution, and commits facts and assertions to
CANONICAL_GRAPH.db & SHADOW_GRAPH.db.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pipeline_b_extraction.contracts import (
    DiscourseBatch,
    ForkedExtractionResult,
)
from storage.database import (
    DatabaseManager,
    EvidenceRecord,
    WorkItem,
)
from storage.identifiers import deterministic_assertion_id, deterministic_edge_id

# Optional OpenTelemetry tracing integration
try:
    from dual_pipeline.telemetry import get_tracer
    tracer = get_tracer("pipeline_b_extraction.worker")
except Exception:
    tracer = None

logger = logging.getLogger("pipeline_b_extraction.worker")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [SLOW-WORKER] %(message)s"
    ))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


@dataclass
class WorkerConfig:
    worker_id: str = "slow_path_worker_1"
    batch_size: int = 5
    poll_interval_seconds: float = 0.5
    lock_timeout_seconds: float = 180.0
    max_retries: int = 3
    discourse_window_seconds: float = 0.0


def _build_single_discourse_batch(
    work_items: List[WorkItem],
    evidence_records: List[EvidenceRecord],
) -> DiscourseBatch:
    combined = " ".join(r.text for r in evidence_records if r.text)
    speakers = ", ".join(sorted(set(r.speaker for r in evidence_records if r.speaker)))
    return DiscourseBatch(
        work_items=list(work_items),
        evidence_records=list(evidence_records),
        combined_text=combined,
        speaker_summary=speakers,
        primary_evidence_id=evidence_records[0].evidence_id,
    )


def _split_pairs_into_windows(
    pairs: List[Tuple[WorkItem, EvidenceRecord]],
    window_seconds: float,
) -> List[DiscourseBatch]:
    batches: List[DiscourseBatch] = []
    current_w: List[WorkItem] = []
    current_e: List[EvidenceRecord] = []
    window_start = 0.0

    for w_item, e_rec in pairs:
        e_start = getattr(e_rec, "start_timestamp", 0.0)
        if not current_e:
            window_start = e_start

        if current_e and (e_start - window_start) > window_seconds:
            batches.append(_build_single_discourse_batch(current_w, current_e))
            current_w = []
            current_e = []
            window_start = e_start

        current_w.append(w_item)
        current_e.append(e_rec)

    if current_e:
        batches.append(_build_single_discourse_batch(current_w, current_e))

    return batches


class SlowPathWorker:
    """
    Idempotent async worker consuming durable work queue events.
    Enforces Laws 1 (Compute Fork), 2 (Purged Leverage), 4 (Durable Queue), and 5 (Soft-Quarantine).
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        fork_coordinator: Optional[Any] = None,
        quality_judge: Optional[Any] = None,
        stealth_judge: Optional[Any] = None,
        config: Optional[WorkerConfig] = None,
    ):
        self.db_manager = db_manager
        self.config = config or WorkerConfig()

        if fork_coordinator is not None:
            self.fork_coordinator = fork_coordinator
        else:
            try:
                from pipeline_b_extraction.extractors import ComputeForkCoordinator
                self.fork_coordinator = ComputeForkCoordinator()
            except (ImportError, Exception):
                self.fork_coordinator = None

        if quality_judge is not None:
            self.quality_judge = quality_judge
        else:
            try:
                from pipeline_b_extraction.judges import QualityJudge
                self.quality_judge = QualityJudge()
            except (ImportError, Exception):
                self.quality_judge = None

        if stealth_judge is not None:
            self.stealth_judge = stealth_judge
        else:
            try:
                from pipeline_b_extraction.judges import StealthJudge
                self.stealth_judge = StealthJudge()
            except (ImportError, Exception):
                self.stealth_judge = None

        try:
            from pipeline_b_extraction.dlq import DeadLetterQueueManager
            self.dlq_manager = DeadLetterQueueManager(db_manager=db_manager)
        except Exception:
            self.dlq_manager = None

        self._is_running = False
        self._loop_task: Optional[asyncio.Task] = None

    # ── Lifecycle Management ──────────────────────────────────────────────

    async def start(self) -> None:
        """Starts the background worker polling loop."""
        if self._is_running:
            return
        self._is_running = True
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info(f"SlowPathWorker started [{self.config.worker_id}]")

    async def stop(self) -> None:
        """Stops the worker polling loop."""
        if not self._is_running:
            return
        self._is_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        logger.info("SlowPathWorker stopped.")

    # ── Polling & Execution Loop ──────────────────────────────────────────

    def _aggregate_into_discourse_batches(
        self,
        leased: List[Tuple[WorkItem, EvidenceRecord]],
    ) -> List[DiscourseBatch]:
        """
        Discourse Window Extraction (Law 3): Aggregates evidence records into cohesive
        discourse windows (30s-90s) before invoking extraction models.
        """
        if not leased:
            return []

        win_sec = getattr(self.config, "discourse_window_seconds", 0.0)
        if win_sec <= 0:
            return [_build_single_discourse_batch([w], [e]) for w, e in leased]

        meeting_groups: Dict[str, List[Tuple[WorkItem, EvidenceRecord]]] = {}
        for w_item, e_rec in leased:
            m_id = e_rec.meeting_id or "default"
            meeting_groups.setdefault(m_id, []).append((w_item, e_rec))

        batches: List[DiscourseBatch] = []
        for pairs in meeting_groups.values():
            batches.extend(_split_pairs_into_windows(pairs, win_sec))
        return batches

    async def run_once(self) -> int:
        """
        Executes a single batch poll-and-process cycle.
        Returns the number of processed work items.
        """
        leased = await asyncio.to_thread(
            self.db_manager.lease_work_batch,
            self.config.batch_size,
            self.config.worker_id,
            self.config.lock_timeout_seconds,
        )

        if not leased:
            return 0

        discourse_batches = self._aggregate_into_discourse_batches(leased)
        tasks = [self._process_discourse_batch(batch) for batch in discourse_batches]
        from config.request_deadline import remaining_timeout
        t_budget = remaining_timeout(180.0)
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=t_budget)
        return len(leased)

    async def _run_loop(self) -> None:
        while self._is_running:
            try:
                count = await self.run_once()
                if count == 0:
                    await asyncio.sleep(self.config.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected worker loop exception: {e}", exc_info=True)
                await asyncio.sleep(self.config.poll_interval_seconds)

    async def _persist_nodes(
        self,
        canonical_nodes: List[Any],
        existing_nodes: List[Any],
        discourse_evidence: EvidenceRecord,
    ) -> Dict[str, str]:
        node_id_map: Dict[str, str] = {}
        for c_node in canonical_nodes:
            orig_id = c_node.node_id
            resolved_node = self.quality_judge.resolve_entity(c_node, existing_nodes)
            if orig_id != resolved_node.node_id:
                node_id_map[orig_id] = resolved_node.node_id
            tagged_node = self.quality_judge.judge_node(resolved_node, discourse_evidence)
            await asyncio.to_thread(self.db_manager.upsert_canonical_node, tagged_node)
        return node_id_map

    async def _persist_edges(
        self,
        canonical_edges: List[Any],
        node_id_map: Dict[str, str],
        discourse_evidence: EvidenceRecord,
    ) -> None:
        for c_edge in canonical_edges:
            if c_edge.from_node_id in node_id_map or c_edge.to_node_id in node_id_map:
                c_edge.from_node_id = node_id_map.get(c_edge.from_node_id, c_edge.from_node_id)
                c_edge.to_node_id = node_id_map.get(c_edge.to_node_id, c_edge.to_node_id)
                c_edge.edge_id = deterministic_edge_id(
                    discourse_evidence.evidence_id,
                    c_edge.from_node_id,
                    c_edge.predicate,
                    c_edge.to_node_id,
                )
            await asyncio.to_thread(self.db_manager.upsert_canonical_edge, c_edge)

    async def _persist_assertions(
        self,
        assertions: List[Any],
        node_id_map: Dict[str, str],
        discourse_evidence: EvidenceRecord,
    ) -> None:
        for asrt in assertions:
            if asrt.subject_node_id in node_id_map:
                asrt.subject_node_id = node_id_map[asrt.subject_node_id]
                asrt.assertion_id = deterministic_assertion_id(
                    discourse_evidence.evidence_id,
                    asrt.subject_node_id,
                    asrt.predicate,
                )
            if asrt.object_node_id and asrt.object_node_id in node_id_map:
                asrt.object_node_id = node_id_map[asrt.object_node_id]
            validated_asrt = self.quality_judge.judge_assertion(asrt)
            bindings = [(discourse_evidence.evidence_id, discourse_evidence.text[:500])]
            await asyncio.to_thread(self.db_manager.upsert_assertion, validated_asrt, bindings)

    # ── Single Discourse Batch Processing Pipeline ────────────────────────

    async def _process_discourse_batch(self, batch: DiscourseBatch) -> None:
        """
        Processes an aggregated discourse batch:
          1. Compute checkpoint digest for idempotency.
          2. Execute Compute Fork (Sanitized Cloud CLIENT_SAFE vs Local Metal INTERNAL_ADVISORY).
          3. Apply Quality Judge metadata tags + Incremental Entity Resolution.
          4. Persist Canonical Nodes, Edges, and W2 Assertions.
          5. Apply Stealth Judge metadata tags and persist to SHADOW_GRAPH.db.
          6. Complete all work items in durable queue.
        """
        span_ctx = None
        if tracer:
            span_ctx = tracer.start_as_current_span("pipeline.b.worker_process_batch")
            span = span_ctx.__enter__()
            span.set_attribute("batch.item_count", len(batch.work_items))
            span.set_attribute("worker.id", self.config.worker_id)

        try:
            primary_evidence = batch.evidence_records[0]
            discourse_evidence = EvidenceRecord(
                evidence_id=batch.primary_evidence_id,
                meeting_id=primary_evidence.meeting_id or "default_meeting",
                speaker=batch.speaker_summary or primary_evidence.speaker,
                text=batch.combined_text,
                start_timestamp=getattr(primary_evidence, "start_timestamp", 0.0),
                end_timestamp=getattr(batch.evidence_records[-1], "end_timestamp", None),
                is_final=True,
            )

            chk_raw = f"{discourse_evidence.evidence_id}:{discourse_evidence.text}".encode("utf-8")
            checkpoint = hashlib.sha256(chk_raw).hexdigest()

            fork_result: ForkedExtractionResult = await self.fork_coordinator.execute_fork(discourse_evidence)

            existing_nodes = await asyncio.to_thread(
                self.db_manager.query_canonical_nodes,
                0.0,
                True,
                1000,
            )

            node_id_map = await self._persist_nodes(
                fork_result.canonical_nodes, existing_nodes, discourse_evidence
            )
            await self._persist_edges(
                fork_result.canonical_edges, node_id_map, discourse_evidence
            )
            await self._persist_assertions(
                fork_result.assertions, node_id_map, discourse_evidence
            )

            for a_node in fork_result.advisory_nodes:
                tagged_adv = self.stealth_judge.judge_node(a_node, discourse_evidence)
                await asyncio.to_thread(self.db_manager.upsert_internal_advisory_node, tagged_adv)

            for w_item in batch.work_items:
                await asyncio.to_thread(
                    self.db_manager.complete_work_item,
                    w_item.event_id,
                    checkpoint,
                )

            logger.info(
                f"Successfully processed discourse batch ({len(batch.work_items)} items) -> "
                f"{len(fork_result.canonical_nodes)} nodes, {len(fork_result.canonical_edges)} edges, "
                f"{len(fork_result.assertions)} assertions, {len(fork_result.advisory_nodes)} advisory"
            )
        except Exception as e:
            logger.error(f"Failure processing discourse batch: {e}", exc_info=True)
            for w_item in batch.work_items:
                if self.dlq_manager is not None:
                    await asyncio.to_thread(
                        self.dlq_manager.route_failure,
                        event_id=w_item.event_id,
                        evidence_id=batch.primary_evidence_id,
                        error=e,
                        retry_count=w_item.retry_count + 1,
                        max_retries=self.config.max_retries,
                        payload_snippet=batch.combined_text,
                    )
                else:
                    await asyncio.to_thread(
                        self.db_manager.fail_work_item,
                        w_item.event_id,
                        str(e),
                        self.config.max_retries,
                    )
        finally:
            if span_ctx:
                span_ctx.__exit__(None, None, None)


ExtractionQueueWorker = SlowPathWorker
