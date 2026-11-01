"""Tests for PRF diarization evidence preservation and independent truth binding (Wave 1)."""
from __future__ import annotations

import copy
import json
import os
import wave
from pathlib import Path
from typing import Any, Dict, List

import jsonschema
import pytest

from tools.qualify_prf_diarization import (
    REPO_ROOT,
    SCHEMA_PATH,
    compute_alignment_metrics,
    sha256_file,
    verify_counterfactual_invariance,
)
from tools.qualify_prf_core.evidence import CACHE_ROOT


MANIFEST_PATH = REPO_ROOT / "reports/prf_rca_evidence_manifest.json"
REF_MANIFEST_PATH = REPO_ROOT / "data/reference/prf_20261006_independent_reference_set.json"
ID_CORRECTIONS_PATH = REPO_ROOT / "data/reference/prf_20261006_identity_corrections.json"
WAVE1_RECEIPT_PATH = REPO_ROOT / "reports/prf_diarization_wave1.json"


def test_manifest_revalidation_and_source_immutability():
    """Verifies that all evidence manifest files exist and revalidate exactly with zero mutations."""
    assert MANIFEST_PATH.exists(), f"Evidence manifest missing: {MANIFEST_PATH}"
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    files = manifest.get("files", [])
    assert len(files) == 18, f"Expected 18 evidence files in manifest, got {len(files)}"

    for entry in files:
        p = Path(entry["path"])
        assert p.exists(), f"Evidence file does not exist: {p}"
        assert p.stat().st_size == entry["bytes"], f"Byte count mismatch for {p}"
        actual_sha = sha256_file(p)
        assert actual_sha == entry["sha256"], f"SHA-256 digest mismatch for {p}: expected {entry['sha256']}, got {actual_sha}"


def test_selected_cache_binding_and_fragmentation_metrics():
    """Verifies the exact properties and fragmentation metrics of selected cache 584009ff5464f464."""
    cache_path = CACHE_ROOT / "output/transcript_cache/00_10-06-2026_Prf_Scope_Sow_Clement_Venkat_master_154116_584009ff5464f464.segments.json"
    assert cache_path.exists(), f"Selected cache missing: {cache_path}"

    with open(cache_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    total_turns = len(segments)
    assert total_turns == 411, f"Expected 411 turns, got {total_turns}"

    one_word_turns = sum(1 for s in segments if len(s.get("text", "").strip().split()) == 1)
    assert one_word_turns == 155, f"Expected 155 one-word turns, got {one_word_turns}"

    at_most_3_words = sum(1 for s in segments if len(s.get("text", "").strip().split()) <= 3)
    assert at_most_3_words == 205, f"Expected 205 turns with <=3 words, got {at_most_3_words}"

    client_turns = sum(1 for s in segments if s.get("speaker") in ("Unknown", "[Client Speaker]", "Client Speaker"))
    assert client_turns == 166, f"Expected 166 generic/unknown turns, got {client_turns}"

    # Verify every segment has empty words array in historical cache
    for s in segments:
        assert s.get("words", []) == [], "Expected empty words array in un-repaired segments sidecar"

    total_words = sum(len(s.get("text", "").strip().split()) for s in segments)
    client_words = sum(len(s.get("text", "").strip().split()) for s in segments if s.get("speaker") in ("Unknown", "[Client Speaker]", "Client Speaker"))
    named_words = total_words - client_words

    assert total_words == 3126, f"Expected 3126 whitespace words, got {total_words}"
    assert client_words == 296, f"Expected 296 generic words, got {client_words}"
    assert named_words == 2830, f"Expected 2830 named words, got {named_words}"

    named_word_cov = round((named_words / total_words) * 100, 2)
    assert named_word_cov == 90.53, f"Expected 90.53% named-word coverage, got {named_word_cov}%"
    assert named_word_cov < 95.0, "Historical PRF artifact must FAIL the 95% named-word coverage gate"


def test_same_cluster_unknown_sandwiches_catalog():
    """Verifies that all 105 same-cluster Unknown sandwiches are accurately cataloged in the reference set."""
    assert REF_MANIFEST_PATH.exists(), f"Reference manifest missing: {REF_MANIFEST_PATH}"
    with open(REF_MANIFEST_PATH, "r", encoding="utf-8") as f:
        ref_data = json.load(f)

    sandwiches = ref_data.get("same_cluster_unknown_sandwiches", [])
    assert len(sandwiches) == 105, f"Expected 105 sandwiches, got {len(sandwiches)}"
    assert ref_data.get("total_same_cluster_sandwiches") == 105

    # Check that speaker distribution matches acoustic RCA findings
    by_spk: Dict[str, int] = {}
    for sw in sandwiches:
        spk = sw["surrounding_cluster"]
        by_spk[spk] = by_spk.get(spk, 0) + 1
        assert sw["review_policy"] == "CANDIDATE_FOR_CONTEXTUAL_REVIEW_NOT_AUTOMATIC_MERGE"
        assert sw["duration_sec"] >= 0.0

    assert by_spk.get("SPEAKER_06") == 78, "Expected 78 Clement (SPEAKER_06) sandwiches"
    assert by_spk.get("SPEAKER_07") == 10
    assert by_spk.get("SPEAKER_03") == 5
    assert by_spk.get("SPEAKER_00") == 3
    assert by_spk.get("SPEAKER_02") == 3
    assert by_spk.get("SPEAKER_04") == 3
    assert by_spk.get("SPEAKER_05") == 3


def test_channel_and_header_metadata():
    """Verifies audio parameters, chunk prefix matching, and chunk 105 header anomaly."""
    master_path = CACHE_ROOT / "output/transcript_cache/nea/10-06-2026/00_10-06-2026_Prf_Scope_Sow_Clement_Venkat_master_154116.wav"
    mono_path = CACHE_ROOT / "output/transcript_cache/00_10-06-2026_Prf_Scope_Sow_Clement_Venk_9233e352db9b_16k_mono.wav"
    staging_dir = CACHE_ROOT / "output/transcript_cache/nea/10-06-2026/.staging_Prf_Scope_Sow_Clement_Venkat_154116"

    assert master_path.exists()
    assert mono_path.exists()
    assert staging_dir.exists()

    with wave.open(str(master_path), "rb") as wm:
        assert wm.getnchannels() == 2, "Master audio must be stereo"
        assert wm.getframerate() == 16000, "Master audio must be 16kHz"
        m_frames = wm.getnframes()
        assert m_frames == 25362412, f"Expected 25362412 frames, got {m_frames}"
        assert round(m_frames / 16000, 5) == 1585.15075
        m_pcm = wm.readframes(m_frames)

    with wave.open(str(mono_path), "rb") as w_mono:
        assert w_mono.getnchannels() == 1, "Normalized audio must be mono"
        assert w_mono.getframerate() == 16000
        mono_frames = w_mono.getnframes()
        assert mono_frames == m_frames, "Master and normalized mono must have exact duration frame parity"

    chunks = sorted(staging_dir.glob("chunk_*.wav"))
    assert len(chunks) == 106, f"Expected 106 staging chunks, got {len(chunks)}"

    # Verify PCM prefix matching
    offset = 0
    for idx, cpath in enumerate(chunks):
        with open(cpath, "rb") as cf:
            cdata = cf.read()
        data_idx = cdata.find(b"data")
        pcm_chunk = cdata[data_idx + 8 :] if data_idx != -1 else cdata[44:]
        m_slice = m_pcm[offset : offset + len(pcm_chunk)]
        assert m_slice == pcm_chunk[: len(m_slice)], f"PCM mismatch at chunk {idx}"
        offset += len(pcm_chunk)

    assert offset == 101324486, f"Expected 101324486 matched PCM bytes, got {offset}"
    remaining = len(m_pcm) - offset
    assert remaining == 125162, f"Expected 125162 additional master bytes, got {remaining}"

    # Verify chunk 105 header anomaly
    c105 = staging_dir / "chunk_00105.wav"
    with wave.open(str(c105), "rb") as w105:
        decl_frames = w105.getnframes()
        decl_duration = decl_frames / w105.getframerate()
        assert decl_frames == 1073741823, f"Expected malformed declared frames in chunk 105, got {decl_frames}"
        assert decl_duration > 60000.0, "Declared duration in chunk 105 header must reflect truncation corruption"


def test_scoring_freeze_denominators_and_worked_examples():
    """Verifies that dropping words, duplicating text, or inserting text cannot artificially inflate scores."""
    # Base reference
    ref_words = [
        {"word_id": "w1", "text": "I", "speaker": "Clement", "start": 0.0, "end": 0.5},
        {"word_id": "w2", "text": "think", "speaker": "Clement", "start": 0.5, "end": 1.0},
        {"word_id": "w3", "text": "as", "speaker": "Clement", "start": 1.0, "end": 1.5},
        {"word_id": "w4", "text": "we", "speaker": "Clement", "start": 1.5, "end": 2.0},
        {"word_id": "w5", "text": "talk", "speaker": "Clement", "start": 2.0, "end": 2.5},
    ]

    # Perfect prediction
    hyp_perfect = [
        {"word_id": "h1", "ref_index": 0, "text": "I", "speaker": "Clement"},
        {"word_id": "h2", "ref_index": 1, "text": "think", "speaker": "Clement"},
        {"word_id": "h3", "ref_index": 2, "text": "as", "speaker": "Clement"},
        {"word_id": "h4", "ref_index": 3, "text": "we", "speaker": "Clement"},
        {"word_id": "h5", "ref_index": 4, "text": "talk", "speaker": "Clement"},
    ]
    m_perfect = compute_alignment_metrics(ref_words, hyp_perfect)
    assert m_perfect["named_attribution_accuracy_pct"] == 100.0
    assert m_perfect["reference_named_coverage_pct"] == 100.0

    # Worked Example 1: Insertion of spurious word
    hyp_with_insertion = copy.deepcopy(hyp_perfect)
    hyp_with_insertion.insert(2, {"word_id": "h_ins", "ref_index": None, "text": "is", "speaker": "Clement"})
    m_ins = compute_alignment_metrics(ref_words, hyp_with_insertion)
    assert m_ins["insertions"] == 1
    # Accuracy is correct_matches (5) / named_hyp_words (6) = 83.33%
    assert m_ins["named_attribution_accuracy_pct"] < 100.0, "Insertion must be penalized in accuracy denominator"

    # Worked Example 2: Deletion (dropping speech)
    hyp_dropped = hyp_perfect[2:]  # drop first 2 words
    m_dropped = compute_alignment_metrics(ref_words, hyp_dropped)
    assert m_dropped["deletions"] == 2
    assert m_dropped["reference_named_coverage_pct"] == 60.0, "Dropping words must penalize reference coverage denominator"

    # Worked Example 3: Duplication of named word
    hyp_duplicated = copy.deepcopy(hyp_perfect)
    hyp_duplicated.append({"word_id": "h_dup", "ref_index": 0, "text": "I", "speaker": "Clement"})
    m_dup = compute_alignment_metrics(ref_words, hyp_duplicated)
    assert m_dup["reference_named_coverage_pct"] == 100.0, "Duplicate word cannot count twice toward coverage"
    assert m_dup["named_attribution_accuracy_pct"] < 100.0, "Duplicate word must penalize named accuracy denominator"

    # Worked Example 4: Speaker Permutation
    hyp_permuted = copy.deepcopy(hyp_perfect)
    for h in hyp_permuted:
        h["speaker"] = "Venkat"
    m_perm = compute_alignment_metrics(ref_words, hyp_permuted)
    assert m_perm["named_attribution_accuracy_pct"] == 0.0, "Permuted speaker predictions must drop accuracy to 0%"


def test_counterfactual_invariance():
    """Verifies counterfactual property: changing reference does NOT alter hypothesis bytes, and score responds."""
    ref_a = [
        {"word_id": "w1", "text": "hello", "speaker": "Clement"},
        {"word_id": "w2", "text": "world", "speaker": "Clement"},
    ]
    ref_b = [
        {"word_id": "w1", "text": "hello", "speaker": "Venkat"},
        {"word_id": "w2", "text": "world", "speaker": "Venkat"},
    ]
    hyp = [
        {"word_id": "h1", "ref_index": 0, "text": "hello", "speaker": "Clement"},
        {"word_id": "h2", "ref_index": 1, "text": "world", "speaker": "Clement"},
    ]

    res = verify_counterfactual_invariance(hyp, ref_a, ref_b)
    assert res["hypothesis_bytes_invariant"] is True, "Hypothesis bytes must not mutate when reference changes"
    assert res["score_responsiveness_verified"] is True, "Changing reference speaker must change the computed accuracy score"
    assert res["score_a"]["named_attribution_accuracy_pct"] == 100.0
    assert res["score_b"]["named_attribution_accuracy_pct"] == 0.0


def test_identity_corrections_precedence_and_candidate_decoupling():
    """Verifies Noemi Monnerville PartnerAdvisory precedence and Venkat Puppala candidate decoupling."""
    assert ID_CORRECTIONS_PATH.exists(), f"Identity corrections file missing: {ID_CORRECTIONS_PATH}"
    with open(ID_CORRECTIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    corrections = {c["entity_id"]: c for c in data.get("corrections", [])}
    assert "noemi_monnerville" in corrections
    assert "venkat_candidate" in corrections

    # Noemi Monnerville check
    noemi = corrections["noemi_monnerville"]
    assert noemi["corrected_affiliation"] == "PartnerAdvisory"
    assert noemi["previous_affiliation"] == "Contractor"
    assert noemi["authority"] == "USER_DIRECTIVE_AUTHORITATIVE"
    assert noemi["role"] == "Web User Experience Designer"
    assert noemi["voice_confirmation_status"] == "PENDING_ACOUSTIC_VERIFICATION"

    # Venkat check
    venkat = corrections["venkat_candidate"]
    assert venkat["candidate_canonical_name"] == "Venkat Puppala"
    assert venkat["status"] == "UNRESOLVED_CANDIDATE_ONLY"
    assert venkat["meeting_specific_binding"] == "PENDING_OPERATOR_CONFIRMATION"
    assert venkat["voice_confirmation_status"] == "PENDING_ACOUSTIC_VERIFICATION"
    assert "DO_NOT_INVENT_OR_GUESS_SURNAME" in venkat["prohibited_actions"]
    assert "DO_NOT_SUBSTITUTE_KRISHNAKUMAR_VENKATARAMAN" in venkat["prohibited_actions"]


def test_receipt_schema_validation():
    """Verifies that the generated Wave 1 receipt strictly conforms to speaker_quality_receipt.schema.json."""
    assert WAVE1_RECEIPT_PATH.exists(), f"Wave 1 receipt missing: {WAVE1_RECEIPT_PATH}"
    assert SCHEMA_PATH.exists(), f"Receipt schema missing: {SCHEMA_PATH}"

    with open(WAVE1_RECEIPT_PATH, "r", encoding="utf-8") as rf:
        receipt = json.load(rf)

    with open(SCHEMA_PATH, "r", encoding="utf-8") as sf:
        schema = json.load(sf)

    # Validate against Draft 2020-12 schema
    jsonschema.validate(instance=receipt, schema=schema)

    assert receipt["wave"] == "Wave 1"
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["denominators"]["released_text_metrics"]["named_word_coverage_pct"] == 90.53
    assert receipt["denominators"]["released_text_metrics"]["released_coverage_gate_status"] == "FAIL"
    assert receipt["denominators"]["independent_reference_metrics"]["measurement_status"] == "UNMEASURED"
    assert receipt["independent_reference_origin"]["status"] == "BOUND"
    assert receipt["unknown_status"]["same_cluster_sandwiches"] == 105
    assert len(receipt["source_hashes"]) == 18


def test_retire_false_qualification_paths():
    """Verifies that legacy simulation and qualification tools have retired false claims and reference copying."""
    from tools.evaluate_lens_diarization_variants import simulate_variant_4_channel_aware_plus_anchors, run_benchmark
    from tools.verify_lens_diarization_qualification import LensDiarizationQualificationVerifier

    # 1. Counterfactual reference check on Variant 4
    # Changing reference_speaker must NOT change predicted_speaker in simulate_variant_4
    dummy_turns_1 = [
        {"turn_id": "t1", "original_acoustic_cluster": "Speaker 1", "reference_speaker": "Jake Stark", "primary_channel": "ch1_remote_isolated", "ch0_rms": 100.0, "ch1_rms": 1000.0},
    ]
    dummy_turns_2 = [
        {"turn_id": "t1", "original_acoustic_cluster": "Speaker 1", "reference_speaker": "Amit Ayer", "primary_channel": "ch1_remote_isolated", "ch0_rms": 100.0, "ch1_rms": 1000.0},
    ]
    preds_1 = simulate_variant_4_channel_aware_plus_anchors(dummy_turns_1)
    preds_2 = simulate_variant_4_channel_aware_plus_anchors(dummy_turns_2)
    assert preds_1[0]["predicted_speaker"] == preds_2[0]["predicted_speaker"], "Variant 4 must not copy reference_speaker"

    # 2. Benchmark receipt disclosure check
    bench = run_benchmark(mode="bounded_candidates")
    assert bench.get("historical_qualification_status") == "INSUFFICIENT_ACOUSTIC_QUALIFICATION"

    # 3. Qualification verifier slice accuracy check
    verifier = LensDiarizationQualificationVerifier()
    analytics = verifier.compute_disaggregated_analytics()
    assert analytics.get("historical_qualification_status") == "INSUFFICIENT_ACOUSTIC_QUALIFICATION"
    assert "status_proxy_basis" in analytics["short_turns"]
    assert "status_proxy_basis" in analytics["overlapping_speech"]
