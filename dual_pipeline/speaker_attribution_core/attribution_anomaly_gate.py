"""
dual_pipeline/speaker_attribution_core/attribution_anomaly_gate.py

Pre-Synthesis & Post-Synthesis Attribution Anomaly Gate.
Enforces Gate C Attribution Sanity invariants:
1. SINGLE_SPEAKER_DOMINANCE_COLLAPSE (>= 85% share across >= 10 turns in multi-attendee calls)
2. UNCONSUMED_SPEAKER_HINTS_DEFICIT (0 hints matched when hints exist, or active < hinted speakers)
3. PARTNER_ORGANIZATION_SILENCE (0 turns for a confirmed partner organization)
4. SELF_INTRO_ATTRIBUTION_CONTRADICTION ("I'm <Name>" contradicts assigned speaker)

Orchestrates 1 Upstream Attribution Recovery Retry before failing closed with
AttributionAnomalyHaltError and quarantining unapproved draft deliverables.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    QAFinding,
    SampleSpan,
    UpstreamQAReport,
)
from dual_pipeline.exceptions import AttributionAnomalyHaltError

logger = logging.getLogger("dual_pipeline.speaker_attribution_core.attribution_anomaly_gate")

GATE_C_ATTRIBUTION_ANOMALIES = {
    "SINGLE_SPEAKER_DOMINANCE_COLLAPSE",
    "UNCONSUMED_SPEAKER_HINTS_DEFICIT",
    "PARTNER_ORGANIZATION_SILENCE",
    "SELF_INTRO_ATTRIBUTION_CONTRADICTION",
    "UNGROUNDED_ROSTER_IDENTITY",
    "ACOUSTIC_VOICEPRINT_CONTRADICTION",
}

_INTRO_NON_NAMES = {
    "sorry", "here", "ready", "just", "going", "fine", "good", "happy",
    "sure", "back", "not", "also", "now", "so", "with", "from", "on",
    "in", "at", "to", "all", "glad", "pleased", "excited", "thinking",
    "like", "aware", "getting", "doing", "making", "assuming", "helpful",
    "great", "true", "looking", "trying", "working", "talking", "saying",
    "calling", "wondering", "hoping", "asking", "telling", "showing",
}


def _parse_roster_dict(
    roster_dict: Dict[str, Any],
) -> Tuple[List[str], Dict[str, List[str]]]:
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
        clean_attendee_entry,
        is_blacklisted_attendee,
        normalize_company_name,
    )
    names: List[str] = []
    org_map: Dict[str, List[str]] = {}
    for org_label, members in roster_dict.items():
        norm_org = normalize_company_name(str(org_label).strip())
        mem_list = members if isinstance(members, list) else [members]
        for m in mem_list:
            c_name, _, _ = clean_attendee_entry(str(m).strip())
            if c_name and not is_blacklisted_attendee(c_name):
                names.append(c_name)
                org_map.setdefault(norm_org, []).append(c_name)
    return names, org_map


def _parse_roster_items(
    raw_items: List[Any],
) -> Tuple[List[str], Dict[str, List[str]]]:
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
        clean_attendee_entry,
        is_blacklisted_attendee,
        normalize_company_name,
    )
    names: List[str] = []
    org_map: Dict[str, List[str]] = {}
    for it in raw_items:
        if isinstance(it, dict):
            n = str(it.get("name", "")).strip()
            o = str(it.get("org") or it.get("company") or "").strip()
        else:
            n, _, o = clean_attendee_entry(str(it).strip())
        if n and not is_blacklisted_attendee(n):
            names.append(n)
            if o:
                org_map.setdefault(normalize_company_name(o), []).append(n)
    return names, org_map


def _extract_roster_names_and_orgs(
    metadata: Dict[str, Any],
) -> Tuple[List[str], Dict[str, List[str]]]:
    """Extracts attendee names and groups them by organization."""
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import split_outside_parens
    roster = metadata.get("roster") or metadata.get("attendees")
    if not roster:
        return [], {}
    if isinstance(roster, dict):
        return _parse_roster_dict(roster)
    raw_items = split_outside_parens(roster) if isinstance(roster, str) else list(roster)
    return _parse_roster_items(raw_items)


def _check_single_speaker_dominance(
    envelope: ForensicSignalEnvelope,
    roster_names: List[str],
) -> Optional[QAFinding]:
    """Flags single speaker dominance collapse (>= 85% share across >= 10 turns in multi-attendee calls)."""
    if len(roster_names) < 2 or not envelope.turns:
        return None

    total_turns = len(envelope.turns)
    spk_counts: Dict[str, int] = {}
    for turn in envelope.turns:
        label = turn.roster_person_id or (turn.speaker_cluster_ids[0] if turn.speaker_cluster_ids else "Unknown")
        spk_counts[label] = spk_counts.get(label, 0) + 1

    dominant_spk, dominant_count = max(spk_counts.items(), key=lambda x: x[1])
    ratio = dominant_count / total_turns

    is_collapse = (total_turns >= 10 and ratio >= 0.85) or (total_turns >= 4 and ratio >= 1.0 and len(roster_names) >= 3)
    if not is_collapse:
        return None

    return QAFinding(
        finding_id="f-attr-dominance-collapse",
        gate_id="Gate_C_Attribution_Sanity",
        code="SINGLE_SPEAKER_DOMINANCE_COLLAPSE",
        severity="blocking",
        required_action=(
            f"Speaker '{dominant_spk}' accounts for {dominant_count}/{total_turns} ({int(ratio * 100)}%) "
            f"of dialogue turns in a {len(roster_names)}-person session ({', '.join(roster_names)}). "
            "Multi-speaker acoustic/hint recovery required."
        ),
    )


def _collect_hinted_speakers(hints: List[Any]) -> Tuple[Set[str], int]:
    hinted: Set[str] = set()
    matched = 0
    for h in hints:
        if isinstance(h, dict):
            spk = str(h.get("speaker_name") or h.get("speaker") or "").strip()
            if spk:
                hinted.add(spk.lower())
            if h.get("status") in ("MATCHED", "RECONCILED", "resolved"):
                matched += 1
    return hinted, matched


def _collect_active_speakers(turns: Tuple[ForensicTurnSignal, ...]) -> Set[str]:
    active: Set[str] = set()
    for t in turns:
        if t.roster_person_id and t.roster_person_id.strip():
            active.add(t.roster_person_id.strip().lower())
        for c in t.speaker_cluster_ids:
            if c and c.strip():
                active.add(c.strip().lower())
    return active


def _check_unconsumed_hints_deficit(
    envelope: ForensicSignalEnvelope,
    metadata: Dict[str, Any],
) -> Optional[QAFinding]:
    """Flags unconsumed speaker hints or active speakers fewer than distinct hinted speakers."""
    hints = metadata.get("speaker_hints") or metadata.get("turn_hints") or []
    if not hints or not isinstance(hints, list):
        return None

    hinted_speakers, matched_count = _collect_hinted_speakers(hints)
    if not hinted_speakers:
        return None

    active_speakers = _collect_active_speakers(envelope.turns)

    if len(envelope.turns) >= 5 and matched_count == 0 and len(hints) >= 2:
        return QAFinding(
            finding_id="f-attr-unconsumed-hints",
            gate_id="Gate_C_Attribution_Sanity",
            code="UNCONSUMED_SPEAKER_HINTS_DEFICIT",
            severity="blocking",
            required_action=f"{len(hints)} speaker hints provided for {len(hinted_speakers)} distinct speaker(s), but 0 hints matched.",
        )

    if len(hinted_speakers) >= 2 and len(active_speakers) < len(hinted_speakers):
        return QAFinding(
            finding_id="f-attr-speaker-deficit",
            gate_id="Gate_C_Attribution_Sanity",
            code="UNCONSUMED_SPEAKER_HINTS_DEFICIT",
            severity="blocking",
            required_action=f"Speaker deficit: {len(hinted_speakers)} hinted speakers provided, but only {len(active_speakers)} active in turns.",
        )

    return None


def _check_partner_org_silence(
    envelope: ForensicSignalEnvelope,
    org_map: Dict[str, List[str]],
) -> Optional[QAFinding]:
    """Flags when a confirmed partner organization has 0 turns attributed across all turns."""
    if len(org_map) < 2:
        return None

    active_speakers = {
        (turn.roster_person_id or "").strip().lower()
        for turn in envelope.turns if turn.roster_person_id
    }

    for org, members in org_map.items():
        has_turn = any(any(m.lower() in s or s in m.lower() for s in active_speakers) for m in members)
        if not has_turn:
            return QAFinding(
                finding_id=f"f-attr-partner-silence-{org[:8]}",
                gate_id="Gate_C_Attribution_Sanity",
                code="PARTNER_ORGANIZATION_SILENCE",
                severity="blocking",
                required_action=f"Partner organization '{org}' has {len(members)} attendee(s) on roster but 0 dialogue turns attributed.",
            )
    return None


def _is_self_intro_mismatch(text: str, assigned_speaker: str) -> Optional[str]:
    pattern = re.compile(r"\b(?:[Ii]'m|[Ii]\s+am|[Tt]his\s+is|[Mm]y\s+name\s+is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")
    for m in pattern.finditer(text):
        intro_name = m.group(1).strip()
        intro_low = intro_name.lower()
        if intro_low in _INTRO_NON_NAMES or any(w in _INTRO_NON_NAMES for w in intro_low.split()):
            continue
        spk_low = assigned_speaker.lower()
        if intro_low in spk_low or intro_low.split()[0] == spk_low.split()[0]:
            continue
        return intro_name
    return None


def _check_self_intro_contradictions(
    envelope: ForensicSignalEnvelope,
) -> List[QAFinding]:
    """Flags turns where the speaker self-identifies as <Name> but is attributed to another person."""
    findings: List[QAFinding] = []
    for i, turn in enumerate(envelope.turns):
        spk = turn.roster_person_id or (turn.speaker_cluster_ids[0] if turn.speaker_cluster_ids else "")
        if not spk:
            continue
        mismatch_name = _is_self_intro_mismatch(turn.transcript_verbatim, spk)
        if mismatch_name:
            findings.append(QAFinding(
                finding_id=f"f-attr-contra-{i}",
                gate_id="Gate_C_Attribution_Sanity",
                code="SELF_INTRO_ATTRIBUTION_CONTRADICTION",
                severity="blocking",
                span=turn.span,
                required_action=f"Turn [{turn.turn_id}] self-identifies as '{mismatch_name}' but is attributed to '{spk}'.",
            ))
    return findings


def _load_store_for_gate_c(metadata: Dict[str, Any]) -> Any:
    store = metadata.get("voiceprint_store")
    if store is not None or not metadata.get("voiceprint_db_path"):
        return store
    try:
        from dual_pipeline.audio_ingestion_core.voiceprint_store import VoiceprintStore
        return VoiceprintStore(db_path=metadata.get("voiceprint_db_path"))
    except Exception:
        return None


def _resolve_enrolled_centroids_for_gate_c(
    metadata: Dict[str, Any], roster_names: List[str]
) -> Dict[str, List[float]]:
    raw_enrolled = metadata.get("enrolled_centroids") or {}
    centroids: Dict[str, List[float]] = {
        str(k): [float(x) for x in v]
        for k, v in (raw_enrolled.items() if isinstance(raw_enrolled, dict) else ())
        if isinstance(v, (list, tuple)) and v
    }
    store = _load_store_for_gate_c(metadata)
    if store is None:
        return centroids
    proj = str(metadata.get("project", "GLOBAL"))
    for name in (n for n in roster_names if n not in centroids):
        vp = store.get_voiceprint(name, project=proj)
        if vp and vp.get("embedding"):
            centroids[name] = [float(x) for x in vp["embedding"]]
    return centroids


def _cosine_sim_vec(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    import math
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return (sum(x * y for x, y in zip(a, b)) / (na * nb)) if (na > 1e-12 and nb > 1e-12) else 0.0


def _best_competing_centroid(
    vec: List[float], assigned: str, centroids: Dict[str, List[float]]
) -> Tuple[float, str, float]:
    assigned_cent = next((v for k, v in centroids.items() if k.lower() == assigned.lower()), None)
    sim_assigned = _cosine_sim_vec(vec, assigned_cent) if assigned_cent else 0.0
    competing = [
        (cand_name, _cosine_sim_vec(vec, cand_cent))
        for cand_name, cand_cent in centroids.items()
        if cand_name.lower() != assigned.lower()
    ]
    if not competing:
        return sim_assigned, "", -1.0
    best_other, best_sim = max(competing, key=lambda item: item[1])
    return sim_assigned, best_other, best_sim


def _check_acoustic_voiceprint_contradictions(
    envelope: ForensicSignalEnvelope,
    metadata: Dict[str, Any],
    roster_names: List[str],
) -> List[QAFinding]:
    """Flags turns assigned to Speaker A when their 256-D embedding matches enrolled Speaker B."""
    centroids = _resolve_enrolled_centroids_for_gate_c(metadata, roster_names)
    turn_embs = metadata.get("turn_embeddings") or {}
    if len(centroids) < 2 or not turn_embs:
        return []
    findings: List[QAFinding] = []
    for idx, turn in enumerate(envelope.turns):
        assigned = (turn.roster_person_id or "").strip()
        emb = turn_embs.get(turn.turn_id) or turn_embs.get(str(idx)) or turn_embs.get(idx)
        if not assigned or not isinstance(emb, (list, tuple)) or not emb:
            continue
        sim_assigned, best_other, best_sim = _best_competing_centroid([float(x) for x in emb], assigned, centroids)
        if best_other and best_sim >= 0.82 and sim_assigned < 0.65 and (best_sim - sim_assigned) >= 0.20:
            findings.append(QAFinding(
                finding_id=f"f-attr-vp-contra-{idx}",
                gate_id="Gate_C_Attribution_Sanity",
                code="ACOUSTIC_VOICEPRINT_CONTRADICTION",
                severity="blocking",
                span=turn.span,
                required_action=(
                    f"Turn [{turn.turn_id}] attributed to '{assigned}' (cosine={sim_assigned:.2f}) "
                    f"contradicts enrolled voiceprint of '{best_other}' (cosine={best_sim:.2f})."
                ),
            ))
    return findings


def evaluate_gate_c_attribution_anomalies(
    envelope: ForensicSignalEnvelope,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[QAFinding]:
    """Evaluates Gate C attribution sanity anomaly invariants including voiceprint contradictions."""
    findings: List[QAFinding] = []
    meta = metadata or getattr(envelope, "metadata", None) or {}
    roster_names, org_map = _extract_roster_names_and_orgs(meta)

    f_dom = _check_single_speaker_dominance(envelope, roster_names)
    if f_dom:
        findings.append(f_dom)

    f_hints = _check_unconsumed_hints_deficit(envelope, meta)
    if f_hints:
        findings.append(f_hints)

    f_org = _check_partner_org_silence(envelope, org_map)
    if f_org:
        findings.append(f_org)

    findings.extend(_check_self_intro_contradictions(envelope))
    findings.extend(_check_acoustic_voiceprint_contradictions(envelope, meta, roster_names))
    return findings


def quarantine_unapproved_drafts(
    output_dir: Union[str, Path],
    container_dir: Optional[Union[str, Path]] = None,
) -> List[str]:
    """Quarantines unapproved draft output files so zero collapsed outputs remain on disk."""
    quarantined: List[str] = []
    scan_paths = [Path(output_dir)]
    if container_dir:
        scan_paths.append(Path(container_dir))

    for d in scan_paths:
        if not d.is_dir():
            continue
        cands = [p for p in d.iterdir() if p.is_file() and p.name.startswith(("01_", "02_", "03_")) and not p.name.endswith(".quarantined")]
        for f in cands:
            q_path = f.with_name(f"{f.name}.quarantined")
            try:
                f.rename(q_path)
                quarantined.append(str(q_path))
            except Exception as exc:
                logger.warning("Failed to quarantine draft %s: %s", f, exc)
    return quarantined


def _find_master_audio_path(metadata: Dict[str, Any], input_path: Optional[Union[str, Path]]) -> Optional[str]:
    target = input_path or metadata.get("master_wav_path") or metadata.get("input_path")
    if target and os.path.exists(str(target)):
        return str(target)
    c_dir = Path(metadata.get("container_dir") or "")
    if c_dir.is_dir():
        for pat in ("00_*_master_*.wav", "*.wav"):
            for w in c_dir.glob(pat):
                return str(w)
    return None


def _invalidate_asr_cache(cache_dir: Optional[Union[str, Path]]) -> None:
    c_p = Path(cache_dir or "output/transcript_cache")
    if c_p.is_dir():
        for cand in c_p.glob("*.segments.json"):
            with open(cand, "w", encoding="utf-8") as fp:
                fp.write("[]")


def _run_transcription_recovery(target_path: str, metadata: Dict[str, Any]) -> Optional[str]:
    from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
    transcriber = LocalMLXAudioTranscriber(mock=bool(metadata.get("mock", False)))
    metadata["force_fresh_diarization"] = True
    new_text = transcriber.transcribe(target_path, metadata)
    metadata["source_text"] = new_text

    hints = metadata.get("speaker_hints") or metadata.get("turn_hints")
    if hints:
        from dual_pipeline.speaker_attribution_core.attribution_engine import apply_quote_hints_to_turns
        try:
            new_text, _ = apply_quote_hints_to_turns(new_text, hints)
            metadata["source_text"] = new_text
        except Exception:
            pass
    return metadata.get("source_text", new_text)


_ORDERED_TURN_RE = re.compile(
    r"(?m)^(?:\*\*|\#\#\s*|\#\s*)?(?:\[[^\]]+\](?:\*\*)?\s*)?(?:\*\*)?\[?([A-Za-z0-9_\s\.\-\(\)/]+?)\]?(?:\*\*)?(?:\s*\[[^\]]+\])?(?::\*\*|\*\*:|:)\s*(.+)$"
)


def _rebuild_recovered_turns(text: str) -> List[ForensicTurnSignal]:
    extracted = [(m.group(1).strip(), m.group(2).strip()) for m in _ORDERED_TURN_RE.finditer(text or "") if m.group(2).strip()]
    new_turns: List[ForensicTurnSignal] = []
    for idx, (spk, utt) in enumerate(extracted):
        start_samp = idx * 16000 * 5
        cluster_lbl = spk if (spk and spk != "Unknown") else "SPEAKER_00"
        new_turns.append(ForensicTurnSignal(
            turn_id=f"turn-recov-{idx:03d}",
            span=SampleSpan(start_sample=start_samp, end_sample=start_samp + 16000 * 4),
            transcript_verbatim=utt,
            speaker_cluster_ids=(cluster_lbl,),
            identity_state="enrollment_matched" if spk != "Unknown" else "cluster_only",
            roster_person_id=spk if spk != "Unknown" else None,
            identity_evidence_id="evidence-recovery" if spk != "Unknown" else None,
        ))
    return new_turns


def _match_turn_candidate_for_recovery(
    turn: ForensicTurnSignal,
    emb: Any,
    centroids: Dict[str, List[float]],
    mem: Any,
    roster_names: List[str],
) -> Optional[str]:
    if isinstance(emb, (list, tuple)) and emb and centroids:
        vec = [float(x) for x in emb]
        vp_cands = [(cand_name, _cosine_sim_vec(vec, cand_cent)) for cand_name, cand_cent in centroids.items()]
        best_name, best_sim = max(vp_cands, key=lambda item: item[1])
        if best_sim >= 0.72:
            return best_name
    if mem is not None and roster_names:
        mem_cands = [(cand_name, float(mem.match_speaker_score(cand_name, turn.transcript_verbatim))) for cand_name in roster_names]
        best_name, best_sc = max(mem_cands, key=lambda item: item[1])
        if best_sc >= 0.35:
            return best_name
    return turn.roster_person_id


def _recover_turns_via_voiceprint_and_memory_seeding(
    envelope: ForensicSignalEnvelope,
    metadata: Dict[str, Any],
) -> Optional[List[ForensicTurnSignal]]:
    """Recovers collapsed or contradicted turns using enrolled voiceprints and SpeakerMemory anchors."""
    roster_names, _ = _extract_roster_names_and_orgs(metadata)
    centroids = _resolve_enrolled_centroids_for_gate_c(metadata, roster_names)
    turn_embs = metadata.get("turn_embeddings") or {}
    mem = metadata.get("speaker_memory")
    if not centroids and not mem:
        return None
    recovered: List[ForensicTurnSignal] = []
    reassigned_count = 0
    for idx, turn in enumerate(envelope.turns):
        emb = turn_embs.get(turn.turn_id) or turn_embs.get(str(idx)) or turn_embs.get(idx)
        best_spk = _match_turn_candidate_for_recovery(turn, emb, centroids, mem, roster_names)
        if best_spk and best_spk != turn.roster_person_id:
            reassigned_count += 1
            recovered.append(turn.model_copy(update={
                "roster_person_id": best_spk,
                "speaker_cluster_ids": (best_spk,),
                "identity_state": "enrollment_matched",
                "identity_evidence_id": "evidence-voiceprint-recovery",
            }))
        else:
            recovered.append(turn)
    return recovered if reassigned_count > 0 else None


def execute_upstream_attribution_recovery(
    envelope: ForensicSignalEnvelope,
    metadata: Dict[str, Any],
    input_path: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
) -> Tuple[bool, Optional[ForensicSignalEnvelope]]:
    """Executes 1 Upstream Attribution Recovery Retry with voiceprint and SpeakerMemory seeding."""
    if metadata.get("upstream_recovery_retry_attempted"):
        return False, None
    metadata["upstream_recovery_retry_attempted"] = True
    metadata["clustering_similarity_threshold"] = min(float(metadata.get("clustering_similarity_threshold", 0.75)), 0.65)
    metadata["force_subcluster_split"] = True

    seeded_turns = _recover_turns_via_voiceprint_and_memory_seeding(envelope, metadata)
    if seeded_turns:
        recovered_env = envelope.model_copy(update={
            "turns": tuple(seeded_turns),
            "timeline_length_samples": max((t.span.end_sample for t in seeded_turns), default=16000),
            "metadata": metadata,
        })
        return True, recovered_env

    target_path = _find_master_audio_path(metadata, input_path)
    if not target_path:
        return False, None

    _invalidate_asr_cache(metadata.get("transcript_cache_dir"))
    try:
        new_text = _run_transcription_recovery(target_path, metadata)
    except Exception as exc:
        logger.warning("Recovery Retry: Re-transcription failed: %s", exc)
        return False, None

    if not new_text:
        return False, None

    new_turns = _rebuild_recovered_turns(new_text)
    if not new_turns:
        return False, None

    recovered_env = envelope.model_copy(update={
        "turns": tuple(new_turns),
        "timeline_length_samples": max((t.span.end_sample for t in new_turns), default=16000),
        "metadata": metadata,
    })
    return True, recovered_env


def _run_retry_cycle(
    gatekeeper: Any,
    envelope: ForensicSignalEnvelope,
    metadata: Dict[str, Any],
    input_path: Optional[Union[str, Path]],
    output_dir: Optional[Union[str, Path]],
) -> Tuple[bool, UpstreamQAReport, ForensicSignalEnvelope]:
    success, recov_env = execute_upstream_attribution_recovery(
        envelope=envelope, metadata=metadata, input_path=input_path, output_dir=output_dir,
    )
    if success and recov_env is not None:
        new_rep = gatekeeper.evaluate(recov_env, metadata=metadata)
        blocking = [f.code for f in new_rep.findings if f.severity == "blocking"]
        if not any(c in GATE_C_ATTRIBUTION_ANOMALIES for c in blocking) and new_rep.verdict in ("PASS", "PASS_WITH_LOCKS"):
            return True, new_rep, recov_env
        return False, new_rep, recov_env
    return False, gatekeeper.evaluate(envelope, metadata=metadata), envelope


def handle_gate_c_attribution_failure(
    gatekeeper: Any,
    envelope: ForensicSignalEnvelope,
    qa_rep: UpstreamQAReport,
    metadata: Dict[str, Any],
    input_path: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
) -> Tuple[UpstreamQAReport, ForensicSignalEnvelope]:
    """Handles Gate C attribution failure with retry and draft quarantine."""
    blocking = [f.code for f in qa_rep.findings if f.severity == "blocking"]
    attr_codes = [c for c in blocking if c in GATE_C_ATTRIBUTION_ANOMALIES]
    if not attr_codes:
        return qa_rep, envelope

    if not metadata.get("upstream_recovery_retry_attempted"):
        passed, new_rep, new_env = _run_retry_cycle(gatekeeper, envelope, metadata, input_path, output_dir)
        if passed:
            return new_rep, new_env
        qa_rep, envelope = new_rep, new_env
        attr_codes = [f.code for f in qa_rep.findings if f.severity == "blocking" and f.code in GATE_C_ATTRIBUTION_ANOMALIES] or attr_codes

    quarantined = quarantine_unapproved_drafts(
        output_dir=output_dir or metadata.get("output_dir") or Path.cwd(),
        container_dir=metadata.get("container_dir"),
    )
    raise AttributionAnomalyHaltError(
        f"Gate C Attribution Anomaly unrecovered after upstream recovery retry. "
        f"Blocking codes: {attr_codes}. Quarantined {len(quarantined)} draft files.",
        findings=list(qa_rep.findings),
        quarantined_paths=quarantined,
        anomaly_codes=attr_codes,
    )
