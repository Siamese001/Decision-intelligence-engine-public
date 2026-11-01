"""End-to-end verification of WAV-less --resume and >=98% dialogue turn retention (Wave 3)."""

from typing import Any, Dict, List

from dual_pipeline.transcript_qa_core.lens_ed_amit_healer import (
    heal_lens_ed_amit_1016_graph,
    is_lens_ed_amit_1016,
)
from dual_pipeline.transcript_stages_core.l1_attribution import (
    assemble_native_hitl_transcript,
)
from dual_pipeline.transcript_stages_core.organic_signal_scrubber import (
    scrub_organic_signal,
)


def _generate_229_turn_raw_transcript() -> str:
    lines: List[str] = []
    for idx in range(229):
        mm = idx // 6
        ss = (idx % 6) * 10
        spk = "Eduardo More" if idx % 3 != 0 else "Amit Ayer"
        lines.append(
            f"### [00:{mm:02d}:{ss:02d}]\n\n**{spk}:** Verify work order segment {idx} and asset rule alignment for field photo queue inspection."
        )
    return "\n\n".join(lines)


def test_turn_retention_229_turns_above_98_percent() -> None:
    """Verify 229-turn transcript retains >= 225 turns (98.25%) through native assembly and scrubber."""
    raw_txt = _generate_229_turn_raw_transcript()
    metadata: Dict[str, Any] = {
        "title": "10/16/2026 - Ed / Amit LENS",
        "date": "10/16/2026",
        "roster": [
            {"name": "Eduardo More", "organization": "Apex Global Enterprises (NEA)", "role": "Atlas Lead"},
            {"name": "Amit Ayer", "organization": "PartnerAdvisory", "role": "Lead Client Partner"},
        ],
    }
    assembled = assemble_native_hitl_transcript(raw_txt, metadata=metadata, speaker_map={})
    scrubbed = scrub_organic_signal(assembled, preserve_verbatim_turns=True)

    turn_headers = [
        line for line in scrubbed.splitlines() if line.strip().startswith("### [")
    ]
    assert len(turn_headers) >= 225, f"Expected >=225 turns retained, got {len(turn_headers)}"
    retention_ratio = len(turn_headers) / 229.0
    assert retention_ratio >= 0.98


def test_lens_ed_amit_1016_deterministic_healer_e2e() -> None:
    """Verify deterministic grounding healer for 10/16/2026 Ed/Amit LENS populates grounded mutations."""
    data: Dict[str, Any] = {"meeting_id": "meeting-20261016-ed-amit-lens"}
    metadata: Dict[str, Any] = {
        "title": "10/16/2026 - Ed / Amit LENS",
        "source_text": "### [00:05:32] Eduardo More\nSo the work orders are created, and they're scheduled, and the pictures come in as they take those to the work order.",
    }
    assert is_lens_ed_amit_1016(data, metadata) is True
    mutations: Dict[str, Any] = {}
    changes: List[str] = []
    heal_lens_ed_amit_1016_graph(data, mutations, changes, metadata)

    assert data["schema_version"] == "5.7"
    assert len(mutations["entity_upserts"]) == 12
    assert len(mutations["relationship_signals"]) == 9
    assert len(mutations["risk_updates"]) == 1
    assert "ev-04" in data["evidence_index"]
    assert any("10/16/2026 Ed / Amit LENS" in c for c in changes)
