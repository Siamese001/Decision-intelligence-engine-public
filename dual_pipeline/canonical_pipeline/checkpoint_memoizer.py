"""
dual_pipeline/canonical_pipeline/checkpoint_memoizer.py

Agent 5 Sub-Stage Checkpoint Memoizer & Circuit Breaker Adapter.
Enforces permanent sub-stage artifact persistence for Outputs 01/02 and
wires the fast-failure circuit breaker into the dialectical synthesis loop.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.canonical_pipeline.attachments import is_deliberate_reset
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality

logger = logging.getLogger("CanonicalAgenticPipeline.checkpoint_memoizer")


def _first_present(mapping: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        val = mapping.get(k)
        if val is not None and val != "":
            return val
    return None


def _check_det_and_llm_pass(dec: Dict[str, Any], stage_key: str, pass_key: str, stage_pass: Any) -> Optional[bool]:
    det_pass = _first_present(dec, (f"{stage_key}_deterministic_gates_pass", f"{pass_key}_deterministic_gates_pass"))
    llm_pass = _first_present(dec, (f"{stage_key}_llm_score_pass", f"{pass_key}_llm_score_pass"))
    has_stage_seal = bool(dec.get(f"{stage_key}_seal"))
    if has_stage_seal and det_pass is not False and llm_pass is not False:
        return True
    if (stage_pass is False and not has_stage_seal) or det_pass is False or llm_pass is False:
        return False
    llm_skipped = bool(_first_present(dec, ("short_circuit", f"{stage_key}_skipped"))) or dec.get("validation_status") == "SKIPPED_DETERMINISTIC_QA_FAILURE"
    if det_pass is True and (llm_pass is True or (llm_pass is not False and has_stage_seal) or (llm_skipped and has_stage_seal)):
        return True
    return None


def _is_decision_passing(dec: Dict[str, Any], pass_key: str) -> bool:
    """Evaluates whether decision dictionary represents a passed stage."""
    stage_key = pass_key.replace("_pass", "")
    stage_seal_obj = dec.get(stage_key)
    if isinstance(stage_seal_obj, dict) and stage_seal_obj.get("status") == "PASS":
        return True
    stage_pass = _first_present(dec, (pass_key, f"{stage_key}_pass"))
    if stage_pass is True or dec.get(f"{pass_key}_seal") is True:
        return True
    res = _check_det_and_llm_pass(dec, stage_key, pass_key, stage_pass)
    if res is not None:
        return res
    return dec.get("decision") in ("PASS", "APPROVE") or dec.get("status") == "PASS"


def _has_decision_seal(dec: Dict[str, Any], stage_key: str, pass_key: str) -> bool:
    if _first_present(dec, ("sealed", "receipt_seal", "seal", f"{stage_key}_seal", f"{pass_key}_seal", f"{stage_key}_receipt_seal")):
        return True
    if dec.get(f"{stage_key}_pass") is True or dec.get(pass_key) is True:
        return True
    stage_obj = dec.get(stage_key)
    if isinstance(stage_obj, dict) and stage_obj.get("status") == "PASS":
        return True
    manifest = dec.get("artifact_manifest")
    if isinstance(manifest, dict):
        return bool(_first_present(manifest, ("decision_seal_sha256", f"{stage_key}_seal_sha256", f"{stage_key}_sha256")))
    return False


def _digest_matches(actual: Any, expected: Optional[str]) -> bool:
    if not expected:
        return True
    if actual is None:
        return True
    if isinstance(actual, str) and actual == "":
        return False
    return actual == expected


def _is_decision_valid_and_matching(
    dec: Dict[str, Any],
    pass_key: str,
    expected_source: Optional[str] = None,
    expected_config: Optional[str] = None,
    expected_collateral: Optional[str] = None,
    expected_binding: Optional[str] = None,
) -> bool:
    """Verifies that decision is sealed, matches digests, and passed."""
    stage_key = pass_key.replace("_pass", "")
    if not _has_decision_seal(dec, stage_key, pass_key):
        return False
    manifest = dec.get("artifact_manifest") if isinstance(dec.get("artifact_manifest"), dict) else {}
    checks = (
        (_first_present(dec, ("source_hash", "input_hash", "audio_sha256", "source_digest", "source_canonical_digest")), expected_source),
        (_first_present(dec, ("config_digest", "configuration_hash", "config_hash")), expected_config),
        (dec.get("collateral_digest") or manifest.get("collateral_digest"), expected_collateral),
        (dec.get("binding_sha256") or manifest.get("binding_sha256"), expected_binding),
    )
    if not all(_digest_matches(act, exp) for act, exp in checks):
        return False
    return _is_decision_passing(dec, pass_key)


def _attempt_hash_mismatch(
    att_dir: Path,
    expected_source_hash: Optional[str],
    expected_collateral: Optional[str] = None,
    expected_binding: Optional[str] = None,
) -> bool:
    """Checks attempt metadata.json for source/input/audio/collateral/binding hash mismatch."""
    meta_file = att_dir / "metadata.json"
    if not meta_file.is_file():
        return False
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if expected_source_hash:
            att_hash = meta.get("source_hash") or meta.get("input_hash") or meta.get("audio_sha256")
            if att_hash and att_hash != expected_source_hash:
                return True
        if expected_collateral:
            att_c = meta.get("collateral_digest")
            if att_c and att_c != expected_collateral:
                return True
        if expected_binding:
            att_b = meta.get("binding_sha256")
            if att_b and att_b != expected_binding:
                return True
        return False
    except Exception:
        return False


def _read_attempt_checkpoint(
    att_dir: Path,
    ckpt_filename: str,
    pass_key: str,
    expected_source_hash: Optional[str] = None,
    expected_config_digest: Optional[str] = None,
    expected_collateral: Optional[str] = None,
    expected_binding: Optional[str] = None,
    allow_unsealed_graph_retry: bool = False,
) -> Optional[str]:
    """Reads checkpoint content from an attempt directory if present, sealed, and passed."""
    ckpt_file = att_dir / ckpt_filename
    if not (ckpt_file.is_file() and ckpt_file.stat().st_size > 200):
        return None
    if _attempt_hash_mismatch(att_dir, expected_source_hash, expected_collateral, expected_binding):
        logger.warning("Rejecting attempt checkpoint %s: metadata hash mismatch", att_dir)
        return None
    dec_file = att_dir / "final_release_decision.json"
    stage_seals_file = att_dir / "stage_seals.json"
    dec = _load_attempt_metadata(dec_file, stage_seals_file)
    if not dec:
        logger.warning("Rejecting attempt checkpoint %s: missing decision or stage seal", att_dir)
        return None

    if _is_decision_valid_and_matching(
        dec, pass_key, expected_source_hash, expected_config_digest, expected_collateral, expected_binding
    ):
        return ckpt_file.read_text(encoding="utf-8")
    logger.warning("Rejecting attempt checkpoint %s: failed validation or mismatch", att_dir)
    return None


def _read_json_safe(path: Path, label: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read %s from %s: %s", label, path, exc)
        return None


def _merge_stage_seals(dec: Dict[str, Any], seals: Dict[str, Any]) -> None:
    for k, v in seals.items():
        if isinstance(v, dict):
            if v.get("status") == "PASS":
                dec[f"{k}_pass"] = True
                dec[f"{k}_seal"] = True
            if v.get("sha256"):
                dec[f"{k}_sha256"] = v["sha256"]
            dec[k] = v
        elif k.endswith(("_seal", "_pass", "_sha256")) or k not in dec:
            dec[k] = v


def _load_attempt_metadata(dec_file: Path, stage_seals_file: Path) -> Optional[Dict[str, Any]]:
    if dec_file.is_file():
        dec = _read_json_safe(dec_file, "attempt decision")
        if dec is None:
            return None
        if stage_seals_file.is_file():
            seals = _read_json_safe(stage_seals_file, "stage seals")
            if isinstance(seals, dict):
                _merge_stage_seals(dec, seals)
        return dec
    if stage_seals_file.is_file():
        return _read_json_safe(stage_seals_file, "stage seals")
    return {}


def _find_checkpoint_in_attempts(
    attempts_dir: Path, ckpt_filename: str, pass_key: str,
    expected_source_hash: Optional[str] = None, expected_config_digest: Optional[str] = None,
    expected_collateral: Optional[str] = None, expected_binding: Optional[str] = None,
    allow_unsealed_graph_retry: bool = False,
) -> Optional[str]:
    """Inspects prior attempt directories in attempts_dir for passing deliverable content."""
    attempt_dirs = sorted(
        [d for d in attempts_dir.iterdir() if d.is_dir() and d.name.startswith("attempt_")],
        key=lambda d: int(d.name.split("_")[1]) if d.name.split("_")[1].isdigit() else 0,
        reverse=True,
    )
    kw = dict(expected_source_hash=expected_source_hash, expected_config_digest=expected_config_digest, expected_collateral=expected_collateral, expected_binding=expected_binding)
    for att_dir in attempt_dirs:
        content = _read_attempt_checkpoint(att_dir, ckpt_filename, pass_key, allow_unsealed_graph_retry=False, **kw)
        if content:
            return content
    return None


def _is_deployed_sha_valid(stage_key: str, content: str, expected_sha: Any, cand_name: str) -> bool:
    """Verifies deployed checkpoint SHA against expected digest, judge cache, or Gate 3."""
    if not expected_sha:
        return True
    import hashlib
    from dual_pipeline.remediation_core.artifact_validator import _is_sha_verified_in_judge_cache
    actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if actual_sha == expected_sha or _is_sha_verified_in_judge_cache(stage_key, actual_sha):
        return True
    if stage_key == "output_3":
        from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
        if run_qa_gate_3(content).get("passed") is True:
            return True
    logger.warning(
        "Rejecting deployed checkpoint %s: sha256 mismatch (%s != %s); falling back to attempts",
        cand_name,
        actual_sha[:12],
        str(expected_sha)[:12],
    )
    return False


def _read_deployed_checkpoint(
    out_p: Path, artifact_pattern: str, pass_key: str,
    expected_source: Optional[str] = None, expected_config: Optional[str] = None,
    expected_collateral: Optional[str] = None, expected_binding: Optional[str] = None,
) -> Optional[str]:
    """Inspects deployed output file and its accompanying sealed decision."""
    deployed = list(out_p.glob(artifact_pattern))
    if not deployed:
        return None
    cand = deployed[0]
    if not (cand.is_file() and cand.stat().st_size > 200):
        return None
    dec_file, stage_seals_file = out_p / "final_release_decision.json", out_p / "stage_seals.json"
    if not (dec_file.is_file() or stage_seals_file.is_file()):
        return None
    try:
        dec = _load_attempt_metadata(dec_file, stage_seals_file)
        if dec and _is_decision_valid_and_matching(
            dec, pass_key, expected_source, expected_config, expected_collateral, expected_binding
        ):
            content = cand.read_text(encoding="utf-8")
            stage_key = pass_key.replace("_pass", "")
            expected_sha = (
                dec.get(f"{stage_key}_sha256")
                or (dec.get("artifact_manifest") or {}).get(f"{stage_key}_sha256")
            )
            if not _is_deployed_sha_valid(stage_key, content, expected_sha, cand.name):
                return None
            return content
    except Exception as exc:
        logger.warning("Could not read deployed decision from %s: %s", out_p, exc)
    return None


def _find_passing_checkpoint_file(
    out_p: Path, artifact_pattern: str, ckpt_filename: str, pass_key: str, metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Finds content of a passing artifact from deployed output files or attempt directories."""
    expected_source = (metadata.get("source_hash") or metadata.get("input_hash") or metadata.get("audio_sha256")) if metadata else None
    expected_config = metadata.get("config_digest") if metadata else None
    expected_collateral = metadata.get("collateral_digest") if metadata else None
    expected_binding = metadata.get("binding_sha256") if metadata else None
    is_graph_retry = bool(metadata and (metadata.get("graph_only_retry") or metadata.get("admission_retry") or metadata.get("retry_scope") == "graph_only"))
    deployed_content = _read_deployed_checkpoint(
        out_p, artifact_pattern, pass_key, expected_source, expected_config, expected_collateral, expected_binding
    )
    if deployed_content:
        return deployed_content
    attempts_dir = out_p / "attempts"
    if attempts_dir.is_dir():
        return _find_checkpoint_in_attempts(
            attempts_dir, ckpt_filename, pass_key, expected_source_hash=expected_source,
            expected_config_digest=expected_config, expected_collateral=expected_collateral,
            expected_binding=expected_binding, allow_unsealed_graph_retry=is_graph_retry,
        )
    return None


def _should_regenerate_output(state_machine: Any, num: str, regen_spec: str, metadata: Dict[str, Any]) -> bool:
    scope = str(metadata.get("retry_scope") or "").lower()
    is_resume = bool(metadata.get("resume"))
    explicit_regen = bool(regen_spec and str(regen_spec).strip().lower() not in ("", "all", "none"))
    regen = (
        f"0{num}" in regen_spec
        or f"output_{num}" in regen_spec
        or bool(scope and scope != "all" and (f"0{num}" in scope or f"output_{num}" in scope))
        or (not explicit_regen and num == "1" and bool(metadata.get("remediation_directive_forensic")) and not is_resume)
        or (not explicit_regen and num == "2" and bool(metadata.get("remediation_directive_recap")) and not is_resume)
        or (not explicit_regen and num == "3" and bool(metadata.get("remediation_directive_graph")) and not is_resume)
    )
    if regen and hasattr(state_machine, "unfreeze_artifact"):
        state_machine.unfreeze_artifact(f"output_{num}", force=True)
    return regen


def _freeze_deliverable(state_machine: Any, key: str, content: str, metadata: Dict[str, Any], out_p: Optional[Path] = None, preserve_exact: bool = False) -> None:
    try:
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        if key == "output_1" and not preserve_exact and not metadata.get("freeze_output_1"):
            content, _ = DeterministicHealer.heal_output_1(content, metadata=metadata)
        elif key == "output_2" and not preserve_exact:
            out_1 = state_machine.get_frozen_content("output_1") if hasattr(state_machine, "get_frozen_content") else None
            content, _ = DeterministicHealer.heal_output_2(content, metadata=metadata, output_1=out_1)
    except Exception as exc:
        logger.debug("Sub-stage healer skipped during checkpoint freeze for %s: %s", key, exc)
    if out_p is not None and out_p.is_dir():
        try:
            from dual_pipeline.remediation_core.stage_sealer import persist_stage_checkpoint_seal
            persist_stage_checkpoint_seal(out_p, key, content, force=True)
        except Exception as exc:
            logger.debug("Stage checkpoint seal persistence skipped for %s: %s", key, exc)
    if hasattr(state_machine, "register_deliverable_pass"):
        state_machine.register_deliverable_pass(key, content)
    else:
        state_machine.freeze_artifact(key, content)
        state_machine.permanently_frozen.add(key)
        state_machine.passing_stages.add(key)
    metadata[f"{key}_memoized"] = True
    logger.info("Sub-stage memoizer: Memoized passing %s checkpoint (zero re-tokenization).", key)


def _count_transcript_turns(text: str) -> int:
    """Counts speaker dialogue turns in markdown or raw transcript text."""
    from dual_pipeline.transcript_stages_core.output01_parser import count_output_01_turns
    return count_output_01_turns(text)


def _is_output_1_truncated(content_1: str, metadata: Dict[str, Any]) -> bool:
    """Detects when a stale 1-turn checkpoint is compared against a multi-turn source transcript."""
    expected_turns = int(metadata.get("turn_count") or 0)
    if not expected_turns and isinstance(metadata.get("source_text"), str):
        expected_turns = _count_transcript_turns(metadata["source_text"])
    if expected_turns < 3:
        return False
    ckpt_turns = _count_transcript_turns(content_1)
    return 0 < ckpt_turns < max(2, int(expected_turns * 0.25))


def _read_input_deliverable_01(metadata: Dict[str, Any]) -> Optional[str]:
    raw_inp = metadata.get("input_path") or metadata.get("raw_transcript_path") or metadata.get("source_path")
    if not raw_inp:
        return None
    inp_p = Path(raw_inp)
    if inp_p.is_file() and "hitl_resolved_transcript" in inp_p.name and inp_p.stat().st_size > 200:
        return inp_p.read_text(encoding="utf-8")
    return None


def _read_json_dict(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_unverified_partial_dir(out_p: Path, stage_key: str) -> bool:
    """Returns True if out_p contains unverified partial deliverables or failed stage decisions without a stage seal."""
    if not out_p.is_dir():
        return False
    seals_data = _read_json_dict(out_p / "stage_seals.json")
    st = (seals_data.get("stages") or {}).get(stage_key)
    if isinstance(st, dict) and st.get("status") == "PASS":
        return False
    m_data = _read_json_dict(out_p / "metadata.json")
    if m_data.get("status") == "PARTIAL_QA_FAILURE" or m_data.get("partial_deliverables_saved") is True:
        return True
    d_data = _read_json_dict(out_p / "final_release_decision.json")
    if d_data.get(f"{stage_key}_pass") is False or d_data.get(f"{stage_key}_llm_score_pass") is False:
        return True
    is_fail = d_data.get("status") in ("FAIL", "REJECTED") or d_data.get("decision") in ("FAIL", "REJECT")
    return bool(is_fail and d_data.get(f"{stage_key}_pass") is not True)


def _unfreeze_output_1_if_updated(state_machine: Any, metadata: Dict[str, Any]) -> bool:
    if not metadata.get("speaker_binding_updated_on_resume"):
        return False
    if hasattr(state_machine, "unfreeze_artifact"):
        state_machine.unfreeze_artifact("output_1", force=True)
    return True


def _memoize_output_1(out_p: Path, state_machine: Any, metadata: Dict[str, Any], regen_spec: str) -> bool:
    """Attempts to memoize Output 1 into state_machine."""
    if _unfreeze_output_1_if_updated(state_machine, metadata) or _should_regenerate_output(state_machine, "1", regen_spec, metadata):
        return False
    if state_machine.is_frozen("output_1"):
        return True

    c_text = _read_input_deliverable_01(metadata)
    if c_text and not _is_output_1_truncated(c_text, metadata):
        _freeze_deliverable(state_machine, "output_1", c_text, metadata, out_p=out_p, preserve_exact=True)
        return True

    content_1 = _find_passing_checkpoint_file(
        out_p, "01_*_hitl_resolved_transcript.md", "output_1.ckpt.md", "output_1_pass", metadata=metadata
    )
    allow_fallback = (
        (not metadata.get("force_rerun") or bool(metadata.get("freeze_output_1") or metadata.get("resume")))
        and not _is_unverified_partial_dir(out_p, "output_1")
    )
    if not content_1 and out_p.is_dir() and allow_fallback:
        cands = [c for c in sorted(out_p.glob("01_*_hitl_resolved_transcript.md")) if "live_transcript" not in c.name.lower()]
        if cands and cands[0].stat().st_size > 200:
            content_1 = cands[0].read_text(encoding="utf-8")
    if content_1 and not _is_output_1_truncated(content_1, metadata):
        _freeze_deliverable(state_machine, "output_1", content_1, metadata, out_p=out_p, preserve_exact=True)
        return True
    return False


def _memoize_output_2(
    out_p: Path,
    state_machine: Any,
    metadata: Dict[str, Any],
    regen_spec: str,
) -> bool:
    """Attempts to memoize Output 2 into state_machine."""
    if _should_regenerate_output(state_machine, "2", regen_spec, metadata):
        return False
    if state_machine.is_frozen("output_2"):
        return True

    content_2 = _find_passing_checkpoint_file(
        out_p,
        "02_*_executive_recap.md",
        "output_2.ckpt.md",
        "output_2_pass",
        metadata=metadata,
    )
    is_stage2_frozen_context = (
        bool(metadata.get("resume"))
        or bool(metadata.get("output3_only"))
        or (bool(regen_spec) and ("03" in regen_spec or "output_3" in regen_spec) and not ("02" in regen_spec or "output_2" in regen_spec))
    )
    if (
        not content_2
        and out_p.is_dir()
        and is_stage2_frozen_context
        and not metadata.get("force_rerun")
        and not _is_unverified_partial_dir(out_p, "output_2")
    ):
        cands = sorted(out_p.glob("02_*_executive_recap.md"))
        if cands and cands[0].stat().st_size > 200:
            content_2 = cands[0].read_text(encoding="utf-8")
    if content_2:
        _freeze_deliverable(state_machine, "output_2", content_2, metadata, out_p=out_p, preserve_exact=True)
        return True
    return False


def _memoize_output_3(
    out_p: Path,
    state_machine: Any,
    metadata: Dict[str, Any],
    regen_spec: str,
) -> bool:
    """Attempts to memoize Output 3 into state_machine."""
    if _should_regenerate_output(state_machine, "3", regen_spec, metadata):
        return False
    if state_machine.is_frozen("output_3"):
        return True

    content_3 = _find_passing_checkpoint_file(
        out_p,
        "03_*_canonical_signal_projection.json",
        "output_3.ckpt.json",
        "output_3_pass",
        metadata=metadata,
    )
    if content_3:
        _freeze_deliverable(state_machine, "output_3", content_3, metadata)
        return True
    return False


def memoize_substage_checkpoints(
    out_p: Path,
    state_machine: Any,
    metadata: Dict[str, Any],
    modality: CanonicalModality,
) -> Dict[str, bool]:
    """
    Sub-stage checkpoint memoization for Stage 01/02/03 artifacts across execution attempts.
    Reuses passing Output 01, Output 02, and Output 03 artifacts without regeneration on retries.
    """
    is_graph_retry = bool(
        metadata.get("graph_only_retry")
        or metadata.get("admission_retry")
        or metadata.get("retry_scope") == "graph_only"
    )
    if is_deliberate_reset(metadata) and not is_graph_retry:
        return {"output_1": False, "output_2": False, "output_3": False}

    memoized = {"output_1": False, "output_2": False, "output_3": False}
    regen_spec = str(metadata.get("regenerate") or "").lower()

    if modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
        memoized["output_1"] = _memoize_output_1(out_p, state_machine, metadata, regen_spec)

    if modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT, CanonicalModality.MEETING_NOTES):
        memoized["output_2"] = _memoize_output_2(out_p, state_machine, metadata, regen_spec)

    memoized["output_3"] = _memoize_output_3(out_p, state_machine, metadata, regen_spec)

    return memoized


def _extract_qa_failures(qa_report: Any) -> list[str]:
    if not isinstance(qa_report, dict):
        return []
    qa_fails: list[str] = list(qa_report.get("failed_gates") or [])
    for out_key in ("output_01_forensic_transcript", "output_02_executive_recap", "output_03_canonical_signal_projection"):
        sub = qa_report.get(out_key)
        if isinstance(sub, dict) and not sub.get("passed", True):
            qa_fails.append(f"{out_key}:{sub.get('failed_gates') or sub.get('failures') or [sub.get('status', 'FAIL')]}")
    return qa_fails


def _extract_qa_and_judge_failures(qa_report: Any, judge_report: Any) -> tuple[list[str], list[str], Dict[str, Any]]:
    qa_fails = _extract_qa_failures(qa_report)
    if not isinstance(judge_report, dict):
        return qa_fails, [], {}
    diag = judge_report.get("judge_failure_diagnostics")
    raw_diag_fails = diag.get("failures") if isinstance(diag, dict) else None
    judge_fails = [f"{f.get('output')}:{f.get('observed') or f.get('criterion')}" for f in (raw_diag_fails or [])]
    for out_key in ("output_1_evaluation", "output_2_evaluation", "output_3_evaluation"):
        sub = judge_report.get(out_key)
        if isinstance(sub, dict) and sub.get("verdict") not in ("PASS", "PASS_ADVISORY", "SKIPPED", None):
            judge_fails.append(f"{out_key}:v={sub.get('verdict')}:score={sub.get('composite_score')}")
    o3_raw = judge_report.get("output_3_evaluation")
    return qa_fails, judge_fails, (o3_raw if isinstance(o3_raw, dict) else {})


def check_circuit_breaker(cb: Any, eval_result: Any, synthesis_result: Any, attempt: int, max_attempts: int) -> bool:
    """Records failure in circuit breaker and checks if loop should terminate immediately."""
    if cb is None or eval_result is None:
        return False
    if getattr(eval_result, "authoritative_write_authorized", False):
        cb.record_success()
        return False

    final_decision = getattr(eval_result, "final_decision", {}) or {}
    qa_fails, judge_fails, o3_ev = _extract_qa_and_judge_failures(
        getattr(eval_result, "qa_report", {}) or {}, getattr(eval_result, "judge_report", {}) or {}
    )
    error_payload = {
        "final_decision": {
            "status": final_decision.get("status"), "verdict": final_decision.get("verdict"),
            "retry_scope": final_decision.get("retry_scope"),
            "failed_outputs": [k for k in ("output_1_pass", "output_2_pass", "output_3_pass") if final_decision.get(k) is False],
        },
        "qa_failures": qa_fails, "judge_failures": judge_fails,
        "output_3_score": o3_ev.get("composite_score"), "output_3_defects": o3_ev.get("defect_codes", []),
    }
    if cb.record_failure(error_payload, candidate=synthesis_result):
        logger.warning("Circuit breaker tripped at attempt %d/%d: %s.", attempt, max_attempts, getattr(cb, "trip_reason", "Repeated candidate failures"))
        return True
    return False


def check_fast_fail_or_circuit_breaker(
    cb: Any,
    eval_result: Any,
    synthesis_result: Any,
    attempt: int,
    max_attempts: int,
) -> Optional[str]:
    """
    Evaluates circuit breaker, HITL suspension, and deterministic-only fail-fast conditions.
    Returns the terminal status string if loop should halt immediately, or None.
    """
    if eval_result is not None:
        dec = getattr(eval_result, "final_decision", {}) or {}
        judge_rep = getattr(eval_result, "judge_report", {}) or {}
        status = dec.get("status") or judge_rep.get("status") or getattr(eval_result, "status", None)
        hitl_active = (
            status in ("REVIEW_REQUIRED", "HITL_PENDING", "PENDING_OPERATOR_DECISION")
            or dec.get("requires_hitl") is True
            or judge_rep.get("requires_hitl") is True
            or getattr(eval_result, "requires_hitl", False)
            or getattr(eval_result, "is_hitl_pending", False)
        )
        if hitl_active:
            term_status = status if status in ("REVIEW_REQUIRED", "HITL_PENDING", "PENDING_OPERATOR_DECISION") else "REVIEW_REQUIRED"
            logger.info("HITL suspension intercepted at attempt %d/%d (status: %s).", attempt, max_attempts, term_status)
            return term_status

    if cb is not None and check_circuit_breaker(cb, eval_result, synthesis_result, attempt, max_attempts):
        return "CIRCUIT_BREAKER_TRIPPED"

    from dual_pipeline.transcript_pipeline_core.qa_runner import _is_deterministic_only_failure
    qa_rep = getattr(eval_result, "qa_report", {})
    dec = getattr(eval_result, "final_decision", {})
    if _is_deterministic_only_failure(qa_rep, dec):
        logger.warning(
            f"RC-4 Fail-Fast: Attempt {attempt}/{max_attempts} failed strictly due to deterministic Python gate checks. "
            "Halting synthesis loop immediately to eliminate futile paid LLM retries."
        )
        return dec.get("status", "FAIL")

    return None
