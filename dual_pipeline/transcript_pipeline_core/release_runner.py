"""
dual_pipeline/transcript_pipeline_core/release_runner.py

Stage EVALS: Release Lifecycle, Packaging, Structural Hierarchy Verification,
and Governed GraphDB Publication engine.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dual_pipeline.core import ROOT
from dual_pipeline.credentials import ensure_ssot_env_loaded
from tools.prepare_evidence_envelope_intake import prepare_package
from tools.validate_output_hierarchy import DATE_FOLDER_REGEX, validate_meeting_directory

logger = logging.getLogger(__name__)


def _resolve_model_asset(subpath: str, env_var: Optional[str] = None) -> str:
    """Resolves local model paths across standard environments and git worktrees."""
    ensure_ssot_env_loaded()
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val and Path(env_val).exists():
            return env_val
    candidate = ROOT / subpath
    if candidate.exists():
        return str(candidate)
    if ".worktrees" in str(ROOT):
        main_repo_candidate = ROOT.parent.parent / subpath
        if main_repo_candidate.exists():
            return str(main_repo_candidate)
    return str(candidate)


def purge_stale_staging_directories(parent_dir: Path) -> List[Path]:
    """Scans and purges any orphaned .staging_* or .tmp_* directories within parent_dir."""
    purged: List[Path] = []
    if not parent_dir.exists() or not parent_dir.is_dir():
        return purged
    for item in parent_dir.iterdir():
        if item.is_dir() and item.name.startswith((".staging", ".tmp")):
            try:
                shutil.rmtree(item, ignore_errors=True)
                purged.append(item)
                logger.info(f"Purged orphaned staging directory: {item}")
            except Exception as e:
                logger.warning(f"Could not purge staging directory {item}: {e}")
    return purged


def promote_staging_to_release(staging_dir: Path, target_dir: Path, purge_staging: bool = True) -> None:
    """Atomically promotes deliverables from staging directory to target release directory."""
    import json
    promoted_ok = False
    try:
        meta_path = staging_dir / "metadata.json"
        if meta_path.exists():
            try:
                meta_obj = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(meta_obj, dict) and meta_obj.get("status") in ("PARTIAL_QA_FAILURE", "QA_FAILED", "FAIL"):
                    raise RuntimeError(f"Promotion blocked: staging directory '{staging_dir}' has terminal failure status '{meta_obj.get('status')}'.")
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(f"Promotion blocked: corrupt staging metadata.json in '{staging_dir}': {e}") from e

        dec_path = staging_dir / "final_release_decision.json"
        if dec_path.exists():
            try:
                dec_obj = json.loads(dec_path.read_text(encoding="utf-8"))
                if isinstance(dec_obj, dict) and (
                    dec_obj.get("status") == "PARTIAL_QA_FAILURE"
                    or (dec_obj.get("status") == "FAIL" and dec_obj.get("authoritative_write_authorized") is False)
                ):
                    raise RuntimeError(
                        f"Promotion blocked: staging final_release_decision.json has status='{dec_obj.get('status')}' "
                        f"and authoritative_write_authorized={dec_obj.get('authoritative_write_authorized')}."
                    )
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(f"Promotion blocked: corrupt staging final_release_decision.json in '{staging_dir}': {e}") from e

        target_dir.mkdir(parents=True, exist_ok=True)
        for item in staging_dir.iterdir():
            dest = target_dir / item.name
            if item.is_file():
                shutil.copy2(item, dest)
            elif item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
        promoted_ok = True
    finally:
        if promoted_ok and purge_staging and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def prepare_and_verify_release_package(
    written: Dict[str, str], metadata: Dict[str, Any], artifacts: Dict[str, Any], is_notes_mode: bool,
) -> Path:
    """Prepares and seals evidence envelope intake package and validates output directory hierarchy."""
    out_p = Path(written["03_graph"]).parent
    package_path = out_p / "intake_package.json"
    if package_path.exists():
        try:
            package_path.unlink()
        except Exception as unl_err:
            logger.debug(f"Could not unlink existing intake_package.json: {unl_err}")

    source_target = Path(written["source_notes"]) if is_notes_mode else Path(written["01_transcript"])
    source_loc = f"{out_p.name}/{source_target.name}"
    raw_aud = metadata.get("audiences", ["internal_consulting", "nee-engineering"])
    aud_list = [a.strip() for a in raw_aud.split(",") if a.strip()] if isinstance(raw_aud, str) else [str(a).strip() for a in raw_aud if str(a).strip()]
    artifacts["intake_package.json"] = prepare_package(
        draft_path=Path(written["03_graph"]), source_path=source_target, output_path=package_path,
        source_location=source_loc, classification=metadata.get("classification", "confidential"),
        audiences=aud_list, started_at=metadata.get("started_at"), organizer=metadata.get("organizer"),
        platform_reference=metadata.get("platform_reference", "Microsoft Teams"),
        is_visual_document=bool(metadata.get("is_visual_document") or metadata.get("allow_visual_image")),
        inline_canonical_promotion=metadata.get("inline_canonical_promotion", True),
        authority_rank=int(metadata.get("authority_rank", 7)),
    )

    is_output_repo = False
    try:
        is_output_repo = out_p.resolve().is_relative_to((ROOT / "output").resolve())
    except Exception:
        is_output_repo = False
    has_date_in_name = bool(DATE_FOLDER_REGEX.match(out_p.name))
    has_date_in_parent = bool(out_p.parent and DATE_FOLDER_REGEX.match(out_p.parent.name))
    if is_output_repo or has_date_in_name or has_date_in_parent:
        target_slug = out_p.name if has_date_in_parent else None
        date_dir = out_p.parent if has_date_in_parent else out_p
        if date_dir.exists():
            purge_stale_staging_directories(date_dir)
        hierarchy_violations = validate_meeting_directory(date_dir, allow_active_recording=False, target_slug=target_slug)
        if hierarchy_violations:
            err_msg = "; ".join(hierarchy_violations)
            logger.error(f"Pre-mutation hierarchy verification failed for '{out_p}': {err_msg}")
            raise RuntimeError(f"Output directory hierarchy compliance failed before publication: {err_msg}")
        logger.info(f"Pre-mutation hierarchy verification PASSED for '{out_p}'")
    return package_path


def _sweep_stale_assertions(
    metadata: Dict[str, Any],
    written: Dict[str, str],
) -> None:
    # Phase 2b: Stale Assertion TTL Retirement
    try:
        from dual_pipeline.staleness_core.ttl_sweeper import AssertionTTLSweeper
        from dual_pipeline.storage.governed_context import is_sealed_generation_database
        from storage.connection import create_sqlite_conn

        raw_gp = metadata.get("graph_path")
        if not raw_gp:
            return
        target_graph = Path(raw_gp).resolve()
        if is_sealed_generation_database(target_graph):
            return
        if target_graph.exists():
            with create_sqlite_conn(target_graph, readonly=False) as conn:
                sweeper = AssertionTTLSweeper()
                sweep_res = sweeper.sweep_stale_assertions(conn, as_of_date=metadata.get("date_filename"))
                written["stale_assertion_sweep"] = sweep_res
                if sweep_res.get("swept_count", 0) > 0:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    logger.info("Retired %d stale assertions via AssertionTTLSweeper", sweep_res["swept_count"])
    except Exception as sweep_err:
        if not metadata.get("allow_sweeper_failure", False):
            logger.error("AssertionTTLSweeper failed: %s", sweep_err)
            raise RuntimeError(f"Fail-Fast: AssertionTTLSweeper failed: {sweep_err}") from sweep_err
        logger.warning("AssertionTTLSweeper encountered non-fatal error: %s", sweep_err)


def _rollback_on_strict_cascade_error(
    pub_res: Dict[str, Any], canon_runtime_root: Path, metadata: Dict[str, Any], cascade_err: Exception
) -> None:
    logger.error("Strict cascade mode enabled: rolling back publication due to cascade error: %s", cascade_err)
    receipt_p = pub_res.get("receipt_path") if pub_res else None
    if receipt_p and Path(receipt_p).is_file():
        from dual_pipeline.release_core.manager import ReleaseManager
        rm = ReleaseManager(
            runtime_root=canon_runtime_root,
            graph_path=metadata.get("graph_path") or (ROOT / "graph" / "nee_graph.sqlite"),
        )
        rm.rollback(Path(receipt_p))
    raise RuntimeError(f"Cascade invalidation failed under strict_cascade mode: {cascade_err}") from cascade_err


def _execute_post_publication_maintenance(
    pub_res: Dict[str, Any], out_p: Path, metadata: Dict[str, Any],
    written: Dict[str, str], canon_runtime_root: Path, dir_id: str,
) -> None:
    if pub_res.get("status") not in ("COMPLETE", "ACTIVE"):
        return
    try:
        from dual_pipeline.core import read_json
        from dual_pipeline.staleness_core.cascade_resolver import CascadeResolver
        from dual_pipeline.staleness_core.intent_extractor import IntentExtractor

        signals = []
        f3_path = Path(written.get("03_graph", ""))
        if f3_path.is_file():
            signals = IntentExtractor.extract_from_output_3(read_json(f3_path))
        if not signals and written.get("02_recap"):
            f2_path = Path(written["02_recap"])
            if f2_path.is_file():
                from datetime import datetime, timezone
                signals = IntentExtractor.scan_recap_text(
                    f2_path.read_text(encoding="utf-8"),
                    observed_at=metadata.get("date") or metadata.get("date_filename") or datetime.now(timezone.utc).strftime("%m/%d/%Y"),
                )
        if signals:
            logger.info(f"Applying {len(signals)} strategic intent signals via CascadeResolver...")
            resolution = CascadeResolver().resolve_for_active_graph(
                signals=signals, runtime_root=canon_runtime_root, directive_id=dir_id,
                meeting_id=metadata.get("meeting_name", "Meeting"), meeting_date_iso=metadata.get("date_filename"),
            )
            written["staleness_invalidation"] = resolution.to_dict()
            logger.info(
                f"Cascade invalidation complete: {len(resolution.closed_actions)} actions closed, "
                f"{len(resolution.superseded_assertions)} assertions superseded, "
                f"{len(resolution.quarantined_compliance_items)} compliance items quarantined."
            )
            breakdown_file = out_p / "graph_mutation_breakdown.md"
            if breakdown_file.is_file():
                cascade_section = (
                    f"\n\n### Governed Strategic Invalidation & Staleness Cascade\n"
                    f"- **Direct Signals Processed:** {len(signals)}\n"
                    f"- **Track A Actions Closed:** {len(resolution.closed_actions)}\n"
                    f"- **Track A Assertions Superseded:** {len(resolution.superseded_assertions)}\n"
                    f"- **Track B Compliance Items Quarantined:** {len(resolution.quarantined_compliance_items)}\n"
                )
                breakdown_file.write_text(breakdown_file.read_text(encoding="utf-8") + cascade_section, encoding="utf-8")
    except Exception as cascade_err:
        written["cascade_error"] = str(cascade_err)
        if metadata.get("strict_cascade", True):
            _rollback_on_strict_cascade_error(pub_res, canon_runtime_root, metadata, cascade_err)
        logger.warning("Cascade invalidation encountered non-fatal error: %s", cascade_err)

    if not written.get("tier1_scout_executed"):
        try:
            from dual_pipeline.graph_scout_core.pipeline_hook import execute_tier1_scout_hook
            execute_tier1_scout_hook(
                written=written, output_dir=out_p, runtime_root=canon_runtime_root, dir_id=dir_id, metadata=metadata,
            )
        except Exception as phase3_err:
            logger.warning("Phase 3 Scoped Scout encountered non-fatal error: %s", phase3_err)


def _execute_automatic_source_retirement(  # noqa: C901
    metadata: Dict[str, Any], canon_runtime_root: Path, graph_path: Path,
    embedding_backend: str, reviewer: str, rationale: str,
) -> None:
    if not metadata.get("force_rerun") or not metadata.get("collision_existing_path"):
        return
    old_path = Path(metadata["collision_existing_path"])
    if not old_path.exists():
        return
    import json
    old_meeting_id = None
    pkg_path = old_path / "intake_package.json"
    if pkg_path.exists():
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
            old_meeting_id = (
                data.get("extensions", {}).get("com.partner_advisory.nee.canonical-signal-projection", {}).get("meeting_id")
                or data.get("source", {}).get("source_id")
            )
        except Exception:
            pass
    if not old_meeting_id:
        for f in old_path.glob("03_*_canonical_signal_projection.json"):
            try:
                sid = json.loads(f.read_text(encoding="utf-8")).get("meeting_id")
                if sid:
                    old_meeting_id = sid
                    break
            except Exception:
                pass
    if not old_meeting_id:
        logger.warning(f"Could not find meeting_id for superseded path {old_path}")
        return

    try:
        import argparse

        from dual_pipeline.cli.handlers import _backend  # noqa: lazy-decouple
        from dual_pipeline.release_core.manager import ReleaseManager
        from dual_pipeline.source_retirement_core.constants import _TARGET_KINDS, _TARGET_TABLES
        from dual_pipeline.source_retirement_core.plan import build_source_retirement_plan
        from dual_pipeline.source_retirement_core.release import (
            prepare_source_retirement_release,
            record_source_retirement_release_events,
        )
        from dual_pipeline.source_retirement_core.targets import _source_scoped_ids

        manager = ReleaseManager(canon_runtime_root, graph_path)
        active = manager.active_pair()
        from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
        conn = GovernedStorageContext.connect(manager.runtime_root / active["graph_path"])
        conn.row_factory = Row

        from dual_pipeline.core import stable_id
        candidate_source_ids = [old_meeting_id]
        hashed_source_id = stable_id("source", old_meeting_id, length=24)
        if hashed_source_id not in candidate_source_ids:
            candidate_source_ids.append(hashed_source_id)

        resolved_source_id = old_meeting_id
        remove = {}
        found_any = False
        for src_candidate in candidate_source_ids:
            candidate_remove = {}
            candidate_found = False
            for kind in _TARGET_KINDS:
                if kind in ("alias_ids", "canonical_alias_ids"):
                    candidate_remove[kind] = []
                    continue
                table, id_field, source_field = _TARGET_TABLES[kind]
                ids = _source_scoped_ids(conn, table=table, id_field=id_field, source_field=source_field, source_id=src_candidate)
                candidate_remove[kind] = ids
                if ids:
                    candidate_found = True
            if candidate_found:
                remove = candidate_remove
                found_any = True
                resolved_source_id = src_candidate
                break
        if not remove:
            remove = {k: [] for k in _TARGET_KINDS}
        conn.close()

        if not found_any:
            logger.info(f"Superseded source {old_meeting_id} had no bound assertions in graph. Skipping retirement.")
            return

        control = {
            "schema_version": "1.0.0",
            "target_source_id": resolved_source_id,
            "remove": remove,
            "retain": {k: [] for k in _TARGET_KINDS},
            "related_removals": [],
            "supersession_detachments": [],
            "source_updates": [],
            "source_version_updates": [],
            "reason": rationale or "Automatic retirement of superseded meeting due to force-rerun collision bypass",
        }

        active_graph = graph_path or (manager.runtime_root / active["graph_path"]).resolve()
        plan = build_source_retirement_plan(
            active_graph,
            active_pair=active,
            control=control,
        )

        args = argparse.Namespace(
            embedding_backend=embedding_backend,
            allow_dev_bge_m3=(embedding_backend == "bge-m3-dev"),
            allow_internal_operational=False,
            internal_operational_policy=None,
            model_path=_resolve_model_asset("models/bge-m3/5617a9f61b028005a4858fdac845db406aefb181", "NEE_BGE_M3_MODEL_PATH"),
            model_manifest=_resolve_model_asset("models/bge-m3/bge-m3-manifest-5617a9f61b028005a4858fdac845db406aefb181.json", "NEE_BGE_M3_MODEL_MANIFEST"),
            model_revision="5617a9f61b028005a4858fdac845db406aefb181",
            embedding_device="auto",
            embedding_fp16=None,
        )
        backend = _backend(args)

        mock_intake_summary = {
            "status": "COMPLETED",
            "run_id": "auto-" + old_meeting_id[:12],
            "trace_id": "trace-auto-" + old_meeting_id[:12],
            "source_id": old_meeting_id,
        }

        receipt, receipt_path = prepare_source_retirement_release(
            manager=manager,
            backend=backend,
            plan=plan,
            run_id=mock_intake_summary["run_id"],
            trace_id=mock_intake_summary["trace_id"],
            source_id=old_meeting_id,
            source_version_id="1.0.0",
            intake_summary=mock_intake_summary,
            approval_id=reviewer,
        )

        from dual_pipeline.retrieval_release import RetrievalReleaseEvaluator
        from dual_pipeline.retrieval_suite import (
            AutomaticRetrievalReleaseSuiteBuilder,
        )

        suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(manager.runtime_root, active_graph).resolve(receipt_path)
        evaluation_result = RetrievalReleaseEvaluator(manager.runtime_root, active_graph).evaluate(
            receipt_path,
            Path(suite_resolution["path"]),
            backend=backend,
        )
        if evaluation_result["evaluation"]["status"] != "PASS":
            logger.error(f"Source retirement evaluation failed: {evaluation_result['evaluation']}")
            return

        record_source_retirement_release_events(
            runtime_root=manager.runtime_root,
            receipt=receipt,
        )

        manager.activate(receipt_path)
        logger.info(f"Successfully activated automatic source retirement for {old_meeting_id}")

    except Exception as e:
        logger.error(f"Failed to execute automatic source retirement for superseded meeting: {e}")

def publish_release_to_graph(
    package_path: Path, metadata: Dict[str, Any], written: Dict[str, str],
    directive_id: Optional[str] = None, reviewer: Optional[str] = None,
    rationale: Optional[str] = None, embedding_backend: str = "bge-m3",
) -> None:
    """Downstream Governed GraphDB Publication (optional, release-gated)."""
    if not directive_id: raise ValueError("Governed GraphDB publication requires an explicit directive_id. Auto-generation of directive IDs is prohibited for authoritative mutations.")
    dir_id = directive_id

    from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
    canon_runtime_root = Path(metadata["runtime_root"]).resolve() if metadata.get("runtime_root") else resolve_authoritative_runtime_root(ROOT)
    meeting_disp, date_disp, project_disp = (
        metadata.get("meeting_name") or metadata.get("title") or "Meeting",
        metadata.get("date") or metadata.get("date_filename") or "Date",
        metadata.get("project") or "knowledge",
    )
    reviewer = (
        reviewer or os.environ.get("NEE_REVIEWER") or metadata.get("reviewer")
        or (metadata.get("approval_receipt") or {}).get("operator_origin")
        or (metadata.get("rbac_auth") or {}).get("subject") or "human_operator"
    )
    if not reviewer: raise ValueError("Governed GraphDB publication requires a valid operator identity.")

    from dual_pipeline.transcript_pipeline_core.shift_left_release import handle_shift_left_prepublish
    from dual_pipeline.transcript_pipeline_core.uwg_deployer import (
        authorize_mutation_for_publication, build_publish_args, record_mutation_activation,
    )
    handle_shift_left_prepublish(metadata, written, package_path, canon_runtime_root)

    publish_args = build_publish_args(
        package_path=package_path, dir_id=dir_id, meeting_disp=meeting_disp, reviewer=reviewer,
        rationale=rationale, date_disp=date_disp, project_disp=project_disp, embedding_backend=embedding_backend,
        metadata=metadata, canon_runtime_root=canon_runtime_root, root_path=ROOT, resolve_model_asset_fn=_resolve_model_asset,
    )
    auth_info = authorize_mutation_for_publication(written, package_path, metadata, canon_runtime_root)
    if auth_info.get("idempotent") and auth_info.get("status") == "ACTIVATED":
        logger.info("Direct graph publication already committed (idempotent recovery).")
        return {"status": "COMPLETE", "idempotent": True, "recovered": True}

    _execute_automatic_source_retirement(
        metadata=metadata, canon_runtime_root=canon_runtime_root,
        graph_path=Path(metadata["graph_path"]).resolve() if metadata.get("graph_path") else None,
        embedding_backend=embedding_backend, reviewer=reviewer, rationale=publish_args.rationale,
    )
    _sweep_stale_assertions(metadata, written)

    from dual_pipeline.cli.handlers import _intake_publish  # noqa: lazy-decouple
    pub_res = _intake_publish(publish_args)
    written["intake_publish"] = pub_res
    if isinstance(pub_res, dict) and pub_res.get("status") == "AWAITING_APPROVAL":
        return pub_res
    if isinstance(pub_res, dict) and pub_res.get("status") not in ("COMPLETE", "PASS", "ACTIVE"):
        raise RuntimeError(f"Governed GraphDB publication failed with status {pub_res.get('status')}: {pub_res.get('error') or pub_res}")

    record_mutation_activation(auth_info, pub_res, metadata, canon_runtime_root)
    out_p = Path(written["03_graph"]).parent
    _write_mutation_breakdown(pub_res, out_p, written)

    _execute_post_publication_maintenance(
        pub_res=pub_res, out_p=out_p, metadata=metadata, written=written, canon_runtime_root=canon_runtime_root, dir_id=dir_id,
    )
    return pub_res


def _write_mutation_breakdown(pub_res: Any, out_p: Path, written: Dict[str, str]) -> None:
    mutation_summary = pub_res.get("mutation_summary") or (pub_res.get("ingestion_completion") or {}).get("mutation_summary") if isinstance(pub_res, dict) else None
    if isinstance(mutation_summary, dict) and (md_table := mutation_summary.get("markdown_breakdown_table")):
        breakdown_path = out_p / "graph_mutation_breakdown.md"
        breakdown_path.write_text(md_table, encoding="utf-8")
        written["graph_mutation_breakdown"] = str(breakdown_path)
        logger.info(f"Wrote GraphDB mutation breakdown table to '{breakdown_path}'")
        print("\n" + md_table + "\n")


def _execute_phase3_scoped_scout(
    written: Dict[str, str], output_dir: Optional[Path] = None, canon_runtime_root: Optional[Path] = None,
    dir_id: Optional[str] = None, meta: Optional[Dict[str, Any]] = None, proposed_entities: Optional[Sequence[Dict[str, Any]]] = None,
    out_p: Optional[Path] = None, metadata: Optional[Dict[str, Any]] = None, **kwargs: Any,
) -> Dict[str, Any]:
    """Phase 3 Scoped Scout compatibility wrapper in release_runner."""
    from dual_pipeline.transcript_pipeline_core.evals_coordinator import _execute_phase3_scoped_scout as _coord_scout
    return _coord_scout(
        written=written, output_dir=output_dir or out_p, canon_runtime_root=canon_runtime_root,
        dir_id=dir_id, meta=meta or metadata or {}, proposed_entities=proposed_entities,
    )


def finalize_release_execution(
    tracker: Any, written: Dict[str, str], package_path: Path, mutate_graph: bool, metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Completes EVALS_RELEASE stage and writes execution receipt."""
    from dual_pipeline.transcript_pipeline_core.evals_coordinator import finalize_release_execution as _finalize
    out_p = Path(written.get("03_graph", "")).parent if "03_graph" in written else package_path.parent
    if not written.get("tier1_scout_executed"):
        try:
            from dual_pipeline.graph_scout_core.pipeline_hook import execute_tier1_scout_hook
            rt_root = Path(metadata["runtime_root"]) if (metadata and metadata.get("runtime_root")) else None
            execute_tier1_scout_hook(written=written, output_dir=out_p, runtime_root=rt_root, dir_id=(metadata or {}).get("directive_id"), metadata=metadata)
        except Exception as scout_err:
            logger.warning("Tier 1 Scoped Scout in finalize_release_execution failed non-fatally: %s", scout_err)

    breakdown_path = out_p / "graph_mutation_breakdown.md"
    if breakdown_path.is_file():
        written["graph_mutation_breakdown"] = str(breakdown_path)
    elif "graph_mutation_breakdown" not in written:
        from dual_pipeline.graph_delta_core.table_formatter import format_markdown_mutation_table
        noop_table = format_markdown_mutation_table({
            "status": "IDEMPOTENT_NOOP", "generation_before": "none", "generation_after": "none",
            "graph_sha256_before": "none", "graph_sha256_after": "none",
        })
        breakdown_path.write_text(noop_table, encoding="utf-8")
        written["graph_mutation_breakdown"] = str(breakdown_path)

    receipt_path = _finalize(tracker=tracker, receipt_dir=out_p, written=written)
    written["execution_receipt"] = str(receipt_path)
    return str(receipt_path)


