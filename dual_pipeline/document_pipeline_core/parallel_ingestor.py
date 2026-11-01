"""
dual_pipeline/document_pipeline_core/parallel_ingestor.py

Governed Parallel Multi-Document Ingestion Coordinator.
Coordinates concurrent execution of DocumentPipeline across multiple documents,
pre-computing candidate retrieval embeddings into per-document vector staging stores
in parallel and coordinating conflict-free publication to GraphDB.
"""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality, detect_modality
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.credentials import CredentialManager
from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline

logger = logging.getLogger(__name__)


@dataclass
class DocumentTask:
    """Normalized ingestion task specification."""
    input_path: Path
    metadata: Dict[str, Any] = field(default_factory=dict)
    output_dir: str = "auto"
    modality: Optional[CanonicalModality] = None
    attachments: Optional[List[str]] = None
    attachment_dir: Optional[str] = None
    presentation_path: Optional[str] = None


class ParallelDocumentIngestor:
    """Orchestrates concurrent document ingestion workers with isolated locks and staging."""

    def __init__(
        self,
        max_workers: Optional[int] = 8,
        provider_type: str = "offline",
        model_name: Optional[str] = None,
        reasoning_effort: str = "high",
        embedding_backend: str = "bge-m3",
        mutate_graph: bool = True,
        auto_mutate_on_pass: bool = True,
        fast_judge: bool = True,
        group_commit: bool = False,
        reviewer: Optional[str] = None,
        rationale: Optional[str] = None,
        runtime_root: Optional[Path] = None,
        project: str = "auto",
    ) -> None:
        self._auto_scale_workers = max_workers is None
        if max_workers is None:
            from dual_pipeline.canonical_pipeline.parallel_orchestrator import resolve_optimal_parallel_workers
            self.max_workers = resolve_optimal_parallel_workers(None)
        else:
            self.max_workers = max(1, min(int(max_workers), 16))
        self.provider_type, self.model_name = provider_type, model_name
        self.reasoning_effort, self.embedding_backend = reasoning_effort, embedding_backend
        self.mutate_graph, self.auto_mutate_on_pass = mutate_graph, auto_mutate_on_pass
        self.fast_judge, self.group_commit = fast_judge, group_commit
        self.reviewer = reviewer or CredentialManager.get_credential("NEE_REVIEWER") or "parallel-document-coordinator"
        self.rationale = rationale or "Governed parallel document ingestion batch"
        self.runtime_root, self.project = runtime_root, project
        self._backend_lock = threading.Lock()
        self._resolved_backend: Any = None

    def _resolve_backend_name(self) -> str:
        b_name = str(self.embedding_backend) if self.embedding_backend else "bge-m3"
        if b_name != "bge-m3":
            return b_name
        try:
            from dual_pipeline.release_core.manager import ReleaseManager
            from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
            rt = self.runtime_root or resolve_authoritative_runtime_root()
            if ReleaseManager(runtime_root=rt).active_pair().get("embedding_backend") in ("flagembedding-bge-m3-dev", "bge-m3-dev"):
                return "bge-m3-dev"
        except Exception:
            pass
        return b_name

    def _get_backend(self) -> Any:
        """Lazily resolve and cache embedding backend instance thread-safely."""
        if not isinstance(self.embedding_backend, str) and hasattr(self.embedding_backend, "backend_id"):
            return self.embedding_backend
        with self._backend_lock:
            if self._resolved_backend is not None:
                return self._resolved_backend
            from dual_pipeline.index_core.backends import get_backend
            b_name = self._resolve_backend_name()
            try:
                self._resolved_backend = get_backend(b_name, allow_dev_backend=True, allow_internal_backend=True)
            except Exception as err:
                logger.warning("Could not instantiate backend %s: %s", b_name, err)
                return None
            return self._resolved_backend

    def _normalize_tasks(
        self,
        documents: List[Union[str, Path, Dict[str, Any]]],
        default_metadata: Optional[Dict[str, Any]] = None,
        output_dir: str = "auto",
    ) -> List[DocumentTask]:
        tasks: List[DocumentTask] = []
        base_meta = copy.deepcopy(default_metadata or {})
        for doc in documents:
            if isinstance(doc, dict):
                raw_path = doc.get("input_path") or doc.get("path")
                if not raw_path:
                    raise ValueError("Document spec dictionary requires 'input_path' or 'path'")
                p = Path(raw_path).expanduser().resolve()
                meta = copy.deepcopy(base_meta)
                meta.update(doc.get("metadata") or {})
                tasks.append(DocumentTask(
                    input_path=p, metadata=meta, output_dir=doc.get("output_dir") or output_dir,
                    modality=doc.get("modality"), attachments=doc.get("attachments"),
                    attachment_dir=doc.get("attachment_dir"),
                    presentation_path=doc.get("presentation_path") or doc.get("presentation"),
                ))
            else:
                p = Path(doc).expanduser().resolve()
                tasks.append(DocumentTask(input_path=p, metadata=copy.deepcopy(base_meta), output_dir=output_dir))
        return tasks

    def _resolve_task_modality(self, task: DocumentTask, meta: Dict[str, Any]) -> CanonicalModality:
        if task.modality is not None:
            return task.modality
        if meta.get("canonical_modality"):
            try:
                return CanonicalModality(meta["canonical_modality"])
            except Exception:
                pass
        return detect_modality(task.input_path, metadata=meta)

    def _resolve_project_name(self, task: DocumentTask, meta: Dict[str, Any]) -> str:
        if self.project and self.project != "auto":
            return self.project
        if meta.get("project") and meta.get("project") != "auto":
            return str(meta["project"])
        from dual_pipeline.query_router_core.project_router import determine_project_directory

        cand = determine_project_directory({"title": task.input_path.stem, "file_path": str(task.input_path)})
        return cand or "project_nexus"

    def _prepare_task_metadata(self, task: DocumentTask, eff_mod: CanonicalModality) -> Dict[str, Any]:
        meta = dict(task.metadata)
        if self.runtime_root:
            meta["runtime_root"] = self.runtime_root
        meta["canonical_modality"] = eff_mod.value
        meta["project"] = self._resolve_project_name(task, meta)
        meta.setdefault("fast_judge", self.fast_judge)
        meta.setdefault("prestage_vectors", True)
        if self.fast_judge:
            meta.setdefault("enable_5_agent_intelligence", False)
        if self.group_commit:
            meta["auto_mutate_on_pass"] = False

        if eff_mod == CanonicalModality.DOCUMENT:
            meta.update({"is_document": True, "output3_only": True})
            meta.setdefault("source_type", "document")
            meta.setdefault("outputs", ["03"])
        else:
            meta.pop("is_document", None)
            meta.pop("output3_only", None)
            if eff_mod == CanonicalModality.MEETING_NOTES:
                meta.update({"notes_mode": True, "is_notes": True, "skip_output_1": True})
                meta.setdefault("outputs", ["02", "03"])
                meta.setdefault("source_type", "copilot_notes")
            else:
                meta.setdefault("outputs", ["01", "02", "03"])
        return meta

    def _run_task_pipeline(
        self,
        task: DocumentTask,
        meta: Dict[str, Any],
        eff_mod: CanonicalModality,
        directive_id: str,
        progress_callback: Optional[Callable[[int, int, str], None]],
    ) -> Dict[str, Any]:
        should_mutate = False if self.group_commit else self.mutate_graph
        auto_mutate = False if self.group_commit else self.auto_mutate_on_pass
        common_kwargs = {
            "input_path": task.input_path,
            "metadata": meta,
            "output_dir": task.output_dir,
            "mutate_graph": should_mutate,
            "auto_mutate_on_pass": auto_mutate,
            "directive_id": directive_id,
            "reviewer": self.reviewer,
            "rationale": self.rationale,
            "embedding_backend": self.embedding_backend,
            "progress_callback": progress_callback,
        }
        if eff_mod == CanonicalModality.DOCUMENT:
            pipeline = DocumentPipeline(
                provider_type=self.provider_type,
                model_name=self.model_name,
                reasoning_effort=self.reasoning_effort,
            )
            return pipeline.run(fast_judge=self.fast_judge, **common_kwargs)
        pipeline = CanonicalAgenticPipeline(
            provider_type=self.provider_type,
            model_name=self.model_name,
            reasoning_effort=self.reasoning_effort,
            modality=eff_mod,
        )
        return pipeline.run(
            presentation_path=task.presentation_path,
            attachments=task.attachments,
            attachment_dir=task.attachment_dir,
            **common_kwargs,
        )

    def _execute_single_document(
        self,
        task: DocumentTask,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Execute a single pipeline run within isolated worker context."""
        eff_mod = self._resolve_task_modality(task, dict(task.metadata))
        meta = self._prepare_task_metadata(task, eff_mod)
        proj = meta.get("project") or "nexus"
        clean_stem = re.sub(r"[^a-z0-9_]+", "", task.input_path.stem.lower().replace(" ", "_").replace("-", "_"))[:32]
        doc_hash = hashlib.sha256(str(task.input_path).encode("utf-8")).hexdigest()[:8]
        directive_id = meta.get("directive_id") or f"directive-{proj}-{clean_stem}-{doc_hash}-v1"

        res = self._run_task_pipeline(task, meta, eff_mod, directive_id, progress_callback)
        self._maybe_stage_document_vectors(task, res)
        return res

    def _maybe_stage_document_vectors(self, task: DocumentTask, res: Dict[str, Any]) -> None:
        """Pre-compute candidate retrieval embeddings into per-document vector staging store."""
        out_p = Path(res.get("output_dir") or task.output_dir)
        if not out_p.is_dir():
            return
        staged_db = out_p / "vector_staging.sqlite"
        if staged_db.is_file():
            res["vector_staging_path"] = str(staged_db)
            return

        records = self._extract_staging_records(out_p)
        if not records:
            return

        from dual_pipeline.index_core.vector_staging import encode_and_stage_records

        backend = self._get_backend()
        if backend is None:
            return

        try:
            staged_count = encode_and_stage_records(staged_db, backend, records)
            if staged_count > 0:
                res["vector_staging_path"] = str(staged_db)
        except Exception as stg_err:
            logger.warning("Failed staging vectors into %s: %s", staged_db, stg_err)

    @staticmethod
    def _format_projection_item_text(item: Dict[str, Any]) -> str:
        if "name" in item:
            name_str = str(item.get("name") or "").strip()
            desc_str = str(item.get("description") or "").strip()
            return f"{name_str}\n{desc_str}".strip() if desc_str else name_str
        if "subject" in item or "predicate" in item:
            return f"{item.get('subject', '')} {item.get('predicate', '')} {item.get('object', '')}".strip()
        for key in ("title", "summary", "description", "text"):
            val = item.get(key)
            if val:
                return str(val).strip()
        return ""

    def _records_from_projection(self, sig_file: Path) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        try:
            muts = json.loads(sig_file.read_text(encoding="utf-8")).get("graph_mutations", {})
            categories = (
                "entity_upserts",
                "relationship_signals",
                "decision_updates",
                "proposal_updates",
                "risk_updates",
                "strategic_intent_signals",
            )
            for cat_key in categories:
                for item in muts.get(cat_key) or []:
                    if not isinstance(item, dict):
                        continue
                    t = self._format_projection_item_text(item)
                    if t:
                        text_hash = hashlib.sha256(t.encode("utf-8")).hexdigest()
                        records.append({
                            "record_id": item.get("id") or text_hash[:16],
                            "retrieval_text": t,
                            "retrieval_text_hash": text_hash,
                        })
        except Exception:
            pass
        return records

    def _extract_staging_records(self, out_p: Path) -> List[Dict[str, Any]]:
        for sig_file in out_p.glob("03_*_canonical_signal_projection.json"):
            records = self._records_from_projection(sig_file)
            if records:
                return records

        meta_f = out_p / "metadata.json"
        if meta_f.is_file():
            try:
                m = json.loads(meta_f.read_text(encoding="utf-8"))
                text = f"Document Title: {m.get('title') or m.get('meeting_name') or 'Document'}"
                return [{
                    "record_id": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                    "retrieval_text": text,
                    "retrieval_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }]
            except Exception:
                pass
        return []

    def _resolve_run_info(self, r: Dict[str, Any], intake_engine: Any) -> Tuple[Optional[str], int]:
        if r.get("run_id"):
            return str(r["run_id"]), int(r.get("graph_operation_count", 1))
        out_p = Path(r["output_dir"]) if r.get("output_dir") else None
        ip_path = Path(r["intake_package"]) if r.get("intake_package") else (out_p / "intake_package.json" if out_p else None)
        if ip_path and ip_path.is_file():
            try:
                ing_res = intake_engine.ingest(ip_path)
                if ing_res and ing_res.get("run_id"):
                    return str(ing_res["run_id"]), int(ing_res.get("graph_operation_count", 0))
            except Exception as err:
                logger.warning("Ingest package %s failed: %s", ip_path, err)
        return None, 0

    def _collect_group_inputs(
        self,
        successful_runs: List[Dict[str, Any]],
        intake_engine: Any,
    ) -> Tuple[List[str], List[Path], int]:
        run_ids: List[str] = []
        staged_vectors: List[Path] = []
        total_ops = 0
        for r in successful_runs:
            rid, ops = self._resolve_run_info(r, intake_engine)
            if rid:
                run_ids.append(rid)
                total_ops += ops
            out_dir = r.get("output_dir")
            if out_dir:
                stg = Path(out_dir) / "vector_staging.sqlite"
                if stg.is_file():
                    staged_vectors.append(stg)
        return run_ids, staged_vectors, total_ops

    def _execute_group_commit(
        self,
        successful_runs: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        from dual_pipeline.canonical_pipeline.parallel_orchestrator import stage7_publication_mutex
        from dual_pipeline.intake_engine import IntakeEngine
        from dual_pipeline.release_core.manager import ReleaseManager
        from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
        rt = self.runtime_root or resolve_authoritative_runtime_root()
        with stage7_publication_mutex(rt):
            mgr = ReleaseManager(runtime_root=rt)
            active = mgr.active_pair()
            active_graph = mgr._resolve(active["graph_path"])
            intake_engine = IntakeEngine(graph_path=active_graph, runtime_root=mgr.runtime_root)
            run_ids, staged_vectors, total_ops = self._collect_group_inputs(successful_runs, intake_engine)
            if not run_ids:
                return None
            if total_ops == 0:
                return {"status": "COMPLETE", "mutation_outcome": "IDEMPOTENT_NOOP", "run_ids": run_ids}

            backend = self._get_backend()
            prep = mgr.prepare_many(
                run_ids=run_ids,
                approval_id=f"group-commit-{int(time.time())}",
                backend=backend,
                staged_vector_paths=staged_vectors,
            )
            receipt_p = mgr.releases_dir / f"release-{prep['next_pair']['generation_id']}.json"

            from dual_pipeline.retrieval_release import validate_activation_evaluation
            receipt_dict = json.loads(receipt_p.read_text(encoding="utf-8")) if receipt_p.is_file() else None
            eval_required = True
            try:
                val_eval = validate_activation_evaluation(mgr.runtime_root, receipt_dict)
                if val_eval and not val_eval.get("required", True):
                    eval_required = False
            except Exception:
                eval_required = True

            if eval_required:
                from dual_pipeline.retrieval_release import RetrievalReleaseEvaluator
                from dual_pipeline.retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder

                suite_res = AutomaticRetrievalReleaseSuiteBuilder(mgr.runtime_root, active_graph).resolve(receipt_p)
                eval_res = RetrievalReleaseEvaluator(mgr.runtime_root, active_graph).evaluate(
                    receipt_p,
                    Path(suite_res["path"]),
                    backend=backend,
                )
                evaluation = eval_res.get("evaluation", {})
                if evaluation.get("status") != "PASS":
                    raise RuntimeError(f"Group commit retrieval evaluation failed: {evaluation}")

            return mgr.activate(receipt_p)

    def _collect_task_result(
        self,
        task: DocumentTask,
        future: concurrent.futures.Future,
        results: Dict[str, Any],
        successful_paths: List[str],
        failed_paths: List[str],
        successful_runs: List[Dict[str, Any]],
    ) -> None:
        path_str = str(task.input_path)
        from config.request_deadline import remaining_timeout
        try:
            res = future.result(timeout=remaining_timeout(300.0))
            results[path_str] = res
            successful_paths.append(path_str)
            successful_runs.append(res)
        except Exception as exc:
            logger.error("Parallel ingestion failed for %s: %s", path_str, exc, exc_info=True)
            results[path_str] = {"status": "FAILED", "error": str(exc), "input_path": path_str}
            failed_paths.append(path_str)

    @staticmethod
    def _handle_batch_timeout(
        future_to_task: Dict[concurrent.futures.Future, DocumentTask],
        results: Dict[str, Any],
        failed_paths: List[str],
    ) -> None:
        for future, task in future_to_task.items():
            path_str = str(task.input_path)
            if path_str not in results:
                future.cancel()
                results[path_str] = {
                    "status": "FAILED",
                    "error": "Parallel ingestion task timed out",
                    "input_path": path_str,
                }
                failed_paths.append(path_str)

    def _run_group_commit_step(self, successful_runs: List[Dict[str, Any]], results: Dict[str, Any]) -> bool:
        if not (self.group_commit and successful_runs):
            return False
        try:
            gc_res = self._execute_group_commit(successful_runs)
            results["_group_commit"] = gc_res
            return isinstance(gc_res, dict) and gc_res.get("status") == "FAILED"
        except Exception as gc_exc:
            logger.error("Group commit failed: %s", gc_exc, exc_info=True)
            results["_group_commit"] = {"status": "FAILED", "error": str(gc_exc)}
            return True

    def ingest_batch(
        self,
        documents: List[Union[str, Path, Dict[str, Any]]],
        default_metadata: Optional[Dict[str, Any]] = None,
        output_dir: str = "auto",
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Execute parallel ingestion across the document batch."""
        start_time = time.monotonic()
        tasks = self._normalize_tasks(documents, default_metadata, output_dir)
        total = len(tasks)
        if total == 0:
            return self._build_batch_summary("COMPLETE", 0, [], [], {}, 0.0)

        results: Dict[str, Any] = {}
        successful_paths: List[str] = []
        failed_paths: List[str] = []
        successful_runs: List[Dict[str, Any]] = []

        if self.embedding_backend:
            self._get_backend()

        from config.request_deadline import remaining_timeout
        from dual_pipeline.canonical_pipeline.parallel_orchestrator import resolve_optimal_parallel_workers

        eff_workers = (
            resolve_optimal_parallel_workers(None, task_count=total)
            if self._auto_scale_workers
            else max(1, min(self.max_workers, total))
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=eff_workers) as executor:
            future_to_task = {
                executor.submit(self._execute_single_document, task, progress_callback): task
                for task in tasks
            }
            try:
                for future in concurrent.futures.as_completed(future_to_task, timeout=remaining_timeout(600.0)):
                    self._collect_task_result(
                        future_to_task[future], future, results,
                        successful_paths, failed_paths, successful_runs,
                    )
            except concurrent.futures.TimeoutError:
                self._handle_batch_timeout(future_to_task, results, failed_paths)

        gc_failed = self._run_group_commit_step(successful_runs, results)
        elapsed = time.monotonic() - start_time
        if gc_failed:
            status = "PARTIAL_FAILURE"
        elif len(successful_paths) == total:
            status = "COMPLETE"
        else:
            status = "PARTIAL_SUCCESS" if successful_paths else "FAILED"
        return self._build_batch_summary(status, total, successful_paths, failed_paths, results, elapsed, eff_workers)

    def _build_batch_summary(
        self,
        status: str,
        total: int,
        ok_paths: List[str],
        fail_paths: List[str],
        results: Dict[str, Any],
        elapsed: float,
        parallel_workers_used: Optional[int] = None,
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "total_documents": total,
            "successful_count": len(ok_paths),
            "failed_count": len(fail_paths),
            "successful_paths": ok_paths,
            "failed_paths": fail_paths,
            "results": results,
            "duration_seconds": round(elapsed, 3),
            "parallel_workers": parallel_workers_used if parallel_workers_used is not None else self.max_workers,
        }


def ingest_documents_parallel(
    documents: List[Union[str, Path, Dict[str, Any]]],
    max_workers: Optional[int] = 8,
    default_metadata: Optional[Dict[str, Any]] = None,
    output_dir: str = "auto",
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience function executing bounded parallel document ingestion."""
    coordinator = ParallelDocumentIngestor(max_workers=max_workers, **kwargs)
    return coordinator.ingest_batch(
        documents=documents,
        default_metadata=default_metadata,
        output_dir=output_dir,
        progress_callback=progress_callback,
    )
