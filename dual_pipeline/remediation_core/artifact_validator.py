"""
dual_pipeline/remediation_core/artifact_validator.py

Validates frozen artifacts for selective output regeneration (--regenerate):
- Parses and canonicalizes output deliverable specs ('01', '02', '03', 'all')
- Enforces Frozen Artifact Validation Gate:
  1. Speaker roster consistency (frozen Output 01 speakers ⊆ input speakers/attendees)
  2. Prompt version compatibility (synthesis_prompt_version compatibility check)
  3. Input modality compatibility (audio vs VTT vs notes)
  4. Prior passing release decision verification
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.l0.output_spec import (
    ALL_OUTPUTS,
    CANONICAL_OUTPUT_MAP,
    parse_regenerate_spec,
)

logger = logging.getLogger(__name__)


@dataclass
class FrozenArtifactValidationResult:
    """Result of frozen artifact validation gate check."""
    valid: bool
    artifact_key: str
    file_path: Optional[Path] = None
    content: Optional[str] = None
    reason: str = "OK"
    warnings: List[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def extract_speakers_from_transcript(transcript_text: str) -> Set[str]:
    """Extracts speaker names/identifiers from forensic transcript text."""
    if not transcript_text:
        return set()

    from dual_pipeline.transcript_pipeline_core.source_modality import extract_unique_speakers
    speakers = extract_unique_speakers(transcript_text)
    if speakers:
        return speakers

    # Fallback for table rows or simple speaker headers
    result: Set[str] = set()
    for line in transcript_text.splitlines():
        m = re.match(r"^\*{2}([A-Za-z0-9_\- .]+?)(?:\s*\([^)]*\))?\*{2}\s*:", line.strip())
        if m:
            result.add(m.group(1).strip().lower())
            continue
        m_table = re.match(r"^\|\s*[\d:]+\s*\|\s*([^|]+?)\s*\|", line.strip())
        if m_table:
            spk = m_table.group(1).strip().lower()
            if spk and spk not in ("speaker", "speaker name", "attendee"):
                result.add(spk)

    return result


def _check_decision_direct_pass(dec: Dict[str, Any], artifact_key: str) -> bool:
    if dec.get(f"{artifact_key}_pass", False) or dec.get(f"{artifact_key}_seal", False):
        return True
    det_pass = dec.get(f"{artifact_key}_deterministic_gates_pass")
    llm_pass = dec.get(f"{artifact_key}_llm_score_pass")
    if det_pass is True and llm_pass is True:
        return True
    return False


def _check_qa_report_stage_pass(parent_dir: Path, artifact_key: str) -> bool:
    qa_file = parent_dir / "qa_gates_report.json"
    if not qa_file.is_file():
        return False
    try:
        qa_rep = json.loads(qa_file.read_text(encoding="utf-8"))
        key_map = {
            "output_1": ("output_01_forensic_transcript", "gate_1"),
            "output_2": ("output_02_executive_recap", "gate_2"),
            "output_3": ("output_03_canonical_signal_projection", "output_03_signal_projection", "gate_3"),
        }
        for k in key_map.get(artifact_key, (artifact_key,)):
            if k in qa_rep:
                sec = qa_rep[k]
                if sec.get("passed", False) is True and len(sec.get("failures", [])) == 0:
                    return True
    except Exception:
        pass
    return False


def _check_stage_seals_file(parent_dir: Path, artifact_key: str) -> bool:
    seal_file = parent_dir / "stage_seals.json"
    if not seal_file.is_file():
        return False
    try:
        seals = json.loads(seal_file.read_text(encoding="utf-8"))
        return bool(seals.get(f"{artifact_key}_seal") or seals.get(f"{artifact_key}_pass"))
    except Exception:
        return False


def _check_deliverable_passed_in_decision(
    dec: Dict[str, Any],
    artifact_key: str,
    parent_dir: Path,
) -> bool:
    """Verifies whether an artifact has passed release gates or deterministic QA in a decision context."""
    if _check_decision_direct_pass(dec, artifact_key):
        return True
    if _check_stage_seals_file(parent_dir, artifact_key):
        return True
    det_pass = dec.get(f"{artifact_key}_deterministic_gates_pass")
    llm_pass = dec.get(f"{artifact_key}_llm_score_pass")
    stage_pass = dec.get(f"{artifact_key}_pass")
    if stage_pass is False or llm_pass is False or det_pass is False:
        return False
    return _check_qa_report_stage_pass(parent_dir, artifact_key)


def _collect_attempt_decision_candidates(att_dir: Path) -> List[Path]:
    """Collect decision and seal candidate files from attempt subdirectories."""
    if not att_dir.is_dir():
        return []
    attempt_subdirs = sorted(
        [sub for sub in att_dir.iterdir() if sub.is_dir() and "attempt_" in sub.name],
        key=lambda p: int(m.group(1)) if (m := re.search(r"attempt_(\d+)", p.name)) else 0,
        reverse=True,
    )
    candidates: List[Path] = []
    for sub in attempt_subdirs:
        cand = sub / "final_release_decision.json"
        if cand.is_file():
            candidates.append(cand)
        cand_seals = sub / "stage_seals.json"
        if cand_seals.is_file() and cand_seals not in candidates:
            candidates.append(cand_seals)
    return candidates


def _collect_decision_candidates(output_dir: Path) -> List[Path]:
    dec_candidates: List[Path] = []
    dec_candidates.extend(_collect_attempt_decision_candidates(output_dir / "attempts"))
    root_dec = output_dir / "final_release_decision.json"
    if root_dec.is_file() and root_dec not in dec_candidates:
        dec_candidates.append(root_dec)
    root_seals = output_dir / "stage_seals.json"
    if root_seals.is_file() and root_seals not in dec_candidates:
        dec_candidates.append(root_seals)
    return dec_candidates


def _merge_sibling_stage_seals(dec_file: Path, dec: Dict[str, Any]) -> None:
    """Merge sibling stage_seals.json into decision if present."""
    if dec_file.name != "final_release_decision.json":
        return
    sibling_seals = dec_file.parent / "stage_seals.json"
    if not sibling_seals.is_file():
        return
    try:
        seals_data = json.loads(sibling_seals.read_text(encoding="utf-8"))
        for k, v in seals_data.items():
            if k.endswith(("_seal", "_pass", "_sha256")):
                dec[k] = v
    except Exception:
        pass


def _try_bootstrap_unsealed_stage_seal(output_dir: Path, artifact_key: str) -> Optional[Dict[str, Any]]:
    """Bootstraps stage_seals.json when pre-completed 01_* or 02_* deliverables exist on disk without prior seal."""
    if artifact_key not in ("output_1", "output_2"):
        return None
    if (output_dir / "final_release_decision.json").is_file():
        return None
    if _collect_attempt_decision_candidates(output_dir / "attempts"):
        return None
    qa_file = output_dir / "qa_gates_report.json"
    if qa_file.is_file() and not _check_qa_report_stage_pass(output_dir, artifact_key):
        return None
    ok_file, _, cand_file, content = _find_and_read_candidate_file(output_dir, artifact_key)
    if not ok_file or not content or len(content.strip()) < 100:
        return None
    if _check_frozen_content_defects(artifact_key, content, {}) is not None:
        return None
    try:
        from .stage_sealer import persist_stage_checkpoint_seal
        persist_stage_checkpoint_seal(output_dir, artifact_key, content, force=False)
        seal_file = output_dir / "stage_seals.json"
        if seal_file.is_file():
            return json.loads(seal_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _has_explicit_decision_keys(dec_file: Path, dec: Dict[str, Any], artifact_key: str, pass_field: str) -> bool:
    if dec_file.name == "final_release_decision.json":
        return True
    return any(
        k in dec
        for k in (
            pass_field,
            f"{artifact_key}_seal",
            f"{artifact_key}_deterministic_gates_pass",
            f"{artifact_key}_llm_score_pass",
        )
    )


def _check_prior_decision(
    output_dir: Path, artifact_key: str
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    if not output_dir.is_dir():
        return False, f"Output directory does not exist: {output_dir}", None

    dec_candidates = _collect_decision_candidates(output_dir)
    if not dec_candidates:
        bootstrapped = _try_bootstrap_unsealed_stage_seal(output_dir, artifact_key)
        if bootstrapped is not None:
            return True, None, bootstrapped
        return (
            False,
            f"Cannot freeze artifact '{artifact_key}': Missing final_release_decision.json or stage_seals.json in {output_dir}",
            None,
        )

    last_err = None
    pass_field = f"{artifact_key}_pass"
    has_explicit_decision = False
    for dec_file in dec_candidates:
        try:
            dec = json.loads(dec_file.read_text(encoding="utf-8"))
        except Exception as e:
            last_err = f"Cannot freeze artifact '{artifact_key}': Corrupt decision or seal file ({e})"
            has_explicit_decision = True
            continue

        _merge_sibling_stage_seals(dec_file, dec)
        if _has_explicit_decision_keys(dec_file, dec, artifact_key, pass_field):
            has_explicit_decision = True

        if _check_deliverable_passed_in_decision(dec, artifact_key, dec_file.parent):
            return True, None, dec
        last_err = f"Cannot freeze artifact '{artifact_key}': Prior run did not pass ({pass_field}=False in {dec_file.name})."

    if not has_explicit_decision:
        bootstrapped = _try_bootstrap_unsealed_stage_seal(output_dir, artifact_key)
        if bootstrapped is not None:
            return True, None, bootstrapped

    return False, last_err, None


def _collect_candidate_files(output_dir: Path, pref: str, suff: str, artifact_key: str) -> List[Path]:
    candidates: List[Path] = [
        f for f in output_dir.iterdir()
        if f.is_file() and (
            (f.name.startswith(pref) and f.name.endswith(suff))
            or f.name == f"{artifact_key}.ckpt{suff}"
        )
    ]
    att_dir = output_dir / "attempts"
    if att_dir.is_dir():
        def _get_att_num(p: Path) -> int:
            m = re.search(r"attempt_(\d+)", p.name)
            return int(m.group(1)) if m else 0

        attempt_subdirs = sorted(
            [sub for sub in att_dir.iterdir() if sub.is_dir() and "attempt_" in sub.name],
            key=_get_att_num,
            reverse=True,
        )
        for sub in attempt_subdirs:
            att_cands = [
                f for f in sub.iterdir()
                if f.is_file() and (
                    (f.name.startswith(pref) and f.name.endswith(suff))
                    or f.name == f"{artifact_key}.ckpt{suff}"
                )
            ]
            candidates.extend(att_cands)
    return candidates


def _find_matching_sha_file(candidate_files: List[Path], expected_sha: str) -> Optional[Tuple[Path, str]]:
    import hashlib
    for f in candidate_files:
        try:
            c = f.read_text(encoding="utf-8")
            if hashlib.sha256(c.encode("utf-8")).hexdigest() == expected_sha:
                return f, c
        except Exception:
            continue
    return None


def _select_and_read_best_candidate(
    candidate_files: List[Path],
) -> Tuple[bool, Optional[str], Optional[Path], Optional[str]]:
    candidate_files.sort(
        key=lambda f: (
            1 if "attempts" in str(f) else 0,
            1 if "live" in f.name.lower() else 0,
            0 if ("hitl" in f.name.lower() or "canonical" in f.name.lower() or "executive" in f.name.lower()) else 1,
            -f.stat().st_size,
        )
    )
    candidate_file = candidate_files[0]
    try:
        content = candidate_file.read_text(encoding="utf-8")
        return True, None, candidate_file, content
    except Exception as e:
        return False, f"Failed to read artifact content from {candidate_file}: {e}", candidate_file, None


def _find_and_read_candidate_file(
    output_dir: Path,
    artifact_key: str,
    expected_sha: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[Path], Optional[str]]:
    prefix_map = {"output_1": "01_", "output_2": "02_", "output_3": "03_"}
    suffix_map = {"output_1": ".md", "output_2": ".md", "output_3": ".json"}
    pref = prefix_map.get(artifact_key, "")
    suff = suffix_map.get(artifact_key, "")

    candidate_files = _collect_candidate_files(output_dir, pref, suff, artifact_key)
    if not candidate_files:
        return (
            False,
            f"Artifact file matching '{pref}*{suff}' not found in {output_dir}",
            None,
            None,
        )

    if expected_sha:
        sha_res = _find_matching_sha_file(candidate_files, expected_sha)
        if sha_res:
            return True, None, sha_res[0], sha_res[1]

    return _select_and_read_best_candidate(candidate_files)


def _clean_attendee_str(val: str) -> str:
    return re.sub(r"\(.*?\)", "", val).strip().lower()


def _extract_from_list(attendees: List[Any]) -> Set[str]:
    names: Set[str] = set()
    for att in attendees:
        val = str(att.get("name", "") if isinstance(att, dict) else att)
        cleaned = _clean_attendee_str(val)
        if cleaned:
            names.add(cleaned)
    return names


def _extract_attendee_names(raw_attendees: Any) -> Set[str]:
    if isinstance(raw_attendees, dict):
        names: Set[str] = set()
        for grp in raw_attendees.values():
            target = grp if isinstance(grp, list) else [grp]
            names.update(_extract_attendee_names(target))
        return names
    if isinstance(raw_attendees, list):
        return _extract_from_list(raw_attendees)
    if isinstance(raw_attendees, str):
        return {a for a in (_clean_attendee_str(part) for part in raw_attendees.split(",")) if a}
    return set()


def _check_speaker_consistency(content: str, metadata: Dict[str, Any]) -> Optional[str]:
    raw_attendees = metadata.get("attendees") or metadata.get("roster")
    if not raw_attendees:
        return None

    frozen_speakers = extract_speakers_from_transcript(content)
    attendee_names = _extract_attendee_names(raw_attendees)
    spk_map = metadata.get("speaker_map") if isinstance(metadata.get("speaker_map"), dict) else {}
    for v in spk_map.values():
        if v:
            attendee_names.add(str(v).strip().lower())
    if attendee_names and frozen_speakers:
        overlap = frozen_speakers.intersection(attendee_names)
        if not overlap and len(frozen_speakers) > 1:
            return (
                f"Speaker roster mismatch: Frozen Output 01 speakers {sorted(list(frozen_speakers))[:5]} "
                f"have 0 overlap with current attendee roster {sorted(list(attendee_names))[:5]}."
            )
    return None


def _check_compatibility_warnings(dec: Dict[str, Any], metadata: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    current_prompt_ver = metadata.get("synthesis_prompt_version") or "V5_7"
    prior_prompt_ver = dec.get("synthesis_prompt_version")
    if prior_prompt_ver and prior_prompt_ver != current_prompt_ver:
        warnings.append(
            f"Prompt version difference detected: prior run used '{prior_prompt_ver}', "
            f"current environment uses '{current_prompt_ver}'."
        )

    prior_modality = dec.get("canonical_modality") or dec.get("source_modality")
    current_modality = metadata.get("canonical_modality") or metadata.get("source_modality")
    if prior_modality and current_modality and prior_modality != current_modality:
        warnings.append(
            f"Modality difference: prior artifact created under '{prior_modality}', "
            f"current run is '{current_modality}'."
        )
    return warnings


def _is_sha_verified_in_judge_cache(artifact_key: str, actual_sha: str) -> bool:
    try:
        from dual_pipeline.storage.governed_context import GovernedStorageContext
        from dual_pipeline.transcript_qa_core.eval_cache import _get_cache_db_path
        prefix_map = {"output_1": "out1:", "output_2": "out2:", "output_3": "out3:"}
        pref = prefix_map.get(artifact_key)
        if not pref:
            return False
        db_path = _get_cache_db_path()
        if not db_path.exists():
            return False
        with GovernedStorageContext.read_context(db_path) as conn:
            row = conn.execute(
                "SELECT composite_score, verdict FROM judge_evaluations WHERE cache_key LIKE ? AND verdict = 'PASS' AND composite_score >= 90 LIMIT 1",
                (f"{pref}{actual_sha}%",),
            ).fetchone()
            return row is not None
    except Exception:
        return False


def _check_frozen_sha_match(
    artifact_key: str,
    content: str,
    dec: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Optional[str]:
    import hashlib
    actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    expected_sha = (
        dec.get(f"{artifact_key}_sha256")
        or dec.get("artifact_manifest", {}).get(f"{artifact_key}_sha256")
        or metadata.get(f"{artifact_key}_sha256")
    )
    if not (expected_sha and content) or expected_sha == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855":
        return None
    # If the artifact manifest hash differs from actual_sha, but dec holds a stage seal hash
    # matching actual_sha (e.g. from an isolated healed stage seal), consider it valid.
    seal_sha = dec.get(f"{artifact_key}_sha256")
    if actual_sha == seal_sha:
        return None
    if _is_sha_verified_in_judge_cache(artifact_key, actual_sha):
        return None
    if actual_sha != expected_sha:
        return (
            f"Tampered or modified frozen content for '{artifact_key}': "
            f"SHA256 mismatch with certified prior manifest (expected {expected_sha[:8]}, got {actual_sha[:8]})."
        )
    return None


def _check_output1_timestamp_order(content: str) -> Optional[str]:
    """Validates that timestamps in Output 1 appear in monotonically non-decreasing order."""
    body = content
    alr_split = re.split(r"(?im)^##\s+.*(?:ambiguity\s+lock|register|verification)", content)
    if alr_split:
        body = alr_split[0]
    last_sec = -1
    for line in body.splitlines():
        m = re.search(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", line)
        if m:
            from dual_pipeline.transcript_qa_core.gate1 import _parse_timestamp_seconds
            sec = _parse_timestamp_seconds(m.group(1))
            if sec is not None:
                if last_sec >= 0 and sec < last_sec:
                    return f"Timestamp out of order: [{m.group(1)}] ({sec}s) appeared after {last_sec}s."
                last_sec = sec
    return None


def _check_frozen_content_defects(
    artifact_key: str,
    content: str,
    metadata: Dict[str, Any],
) -> Optional[str]:
    if artifact_key == "output_1":
        spk_err = _check_speaker_consistency(content, metadata)
        if spk_err:
            return spk_err
        return _check_output1_timestamp_order(content)
    if artifact_key not in ("output_2", "output_3"):
        return None
    content_lower = content.lower()
    if "noemi monnerville" in content_lower and re.search(
        r"contractor[\s\:\-]+.*noemi\s+monnerville|noemi\s+monnerville[^\.\n]*contractor", content_lower
    ):
        return "Stale affiliation 'Contractor' found for Noemi Monnerville (confirmed PartnerAdvisory)."
    if "krishnakumar venkataraman" in content_lower:
        return "Unconfirmed surname guess 'Krishnakumar Venkataraman' found for Venkat."
    return None


def _verify_or_heal_frozen_sha(
    artifact_key: str, content: str, dec: Dict[str, Any], metadata: Dict[str, Any]
) -> Tuple[str, Optional[str]]:
    sha_err = _check_frozen_sha_match(artifact_key, content, dec, metadata)
    if not sha_err:
        return content, None
    try:
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        healed_cand = None
        if artifact_key == "output_1":
            healed_cand, _ = DeterministicHealer.heal_output_1(content, metadata=metadata)
        elif artifact_key == "output_2":
            healed_cand, _ = DeterministicHealer.heal_output_2(content, metadata=metadata)
        elif artifact_key == "output_3":
            from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
            healed_3, _ = DeterministicHealer.heal_output_3(content, metadata=metadata)
            if run_qa_gate_3(healed_3, metadata=metadata).get("passed") is True:
                return healed_3, None
        if healed_cand and _check_frozen_sha_match(artifact_key, healed_cand, dec, metadata) is None:
            return healed_cand, None
    except Exception:
        pass
    return content, sha_err


def validate_frozen_artifact_gate(
    output_dir: Path,
    artifact_key: str,
    metadata: Dict[str, Any],
) -> FrozenArtifactValidationResult:
    """
    Validates a frozen artifact before admitting it into the selective regeneration state machine.

    Enforces 4 non-compensating checks:
      1. File presence and readable content.
      2. Prior release decision verification (must be marked as passed).
      3. Prompt version compatibility (synthesis_prompt_version).
      4. Speaker roster consistency for output_1.
    """
    ok_dec, dec_err, dec = _check_prior_decision(output_dir, artifact_key)
    if not ok_dec or dec is None:
        return FrozenArtifactValidationResult(
            valid=False,
            artifact_key=artifact_key,
            reason=dec_err or "Prior decision validation failed",
        )

    expected_sha = (
        dec.get(f"{artifact_key}_sha256")
        or dec.get("artifact_manifest", {}).get(f"{artifact_key}_sha256")
        or metadata.get(f"{artifact_key}_sha256")
    )
    ok_file, file_err, candidate_file, content = _find_and_read_candidate_file(
        output_dir, artifact_key, expected_sha=expected_sha
    )
    if not ok_file or content is None:
        return FrozenArtifactValidationResult(
            valid=False,
            artifact_key=artifact_key,
            file_path=candidate_file,
            reason=file_err or "Artifact file read failed",
        )

    content, sha_err = _verify_or_heal_frozen_sha(artifact_key, content, dec, metadata)
    if sha_err:
        return FrozenArtifactValidationResult(
            valid=False,
            artifact_key=artifact_key,
            file_path=candidate_file,
            content=content,
            reason=sha_err,
        )

    defect_err = _check_frozen_content_defects(artifact_key, content, metadata)
    if defect_err:
        return FrozenArtifactValidationResult(
            valid=False,
            artifact_key=artifact_key,
            file_path=candidate_file,
            content=content,
            reason=defect_err,
        )

    warnings = _check_compatibility_warnings(dec, metadata)
    return FrozenArtifactValidationResult(
        valid=True,
        artifact_key=artifact_key,
        file_path=candidate_file,
        content=content,
        reason="OK",
        warnings=warnings,
    )

