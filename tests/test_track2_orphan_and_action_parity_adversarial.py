"""
Wave 2 Adversarial & Edge-Case Tests: Track 2 Orphan Entity Healing, Action Parity & Frozen Micro-Judge Skips.

Covers >= 3 mandatory adversarial categories:
1. Boundary payload (empty Output 02 action table, 0 entities in `_heal_orphan_entities`).
2. Malformed input (malformed markdown action rows, invalid due dates, missing columns).
3. Exact production regression (`_heal_orphan_entities` `NameError: name 'is_doc' is not defined`
   when healing unconnected `person:` nodes under `org:partner_advisory` in `nea` project context).
4. Invariant bypass attempt (`--output3-only` attempting to skip X2/X3 meeting gates by masquerading as `is_document=True`).
"""

from __future__ import annotations

from dual_pipeline.transcript_qa_core.evaluator import _build_skip_eval
from dual_pipeline.transcript_qa_core.graph_healer import (
    _heal_orphan_entities,
    heal_graph_projection,
)
from tools.prepare_evidence_envelope_intake import parse_output02_action_ledger


def test_boundary_empty_action_ledger_and_empty_entities() -> None:
    """Category 1: Boundary payload — empty strings, tables with only headers, and empty entity lists."""
    assert parse_output02_action_ledger("") == []
    assert parse_output02_action_ledger("# Action & Governance Ledger\n\n| Action | Owner |\n| --- | --- |\n| None | N/A |\n") == []

    data: dict = {"graph_mutations": {"entity_upserts": [], "relationship_signals": []}}
    changes: list[str] = []
    _heal_orphan_entities(data, data["graph_mutations"], changes, is_doc=False, metadata={})
    assert changes == []


def test_malformed_action_ledger_rows_and_dates() -> None:
    """Category 2: Malformed input — unclosed pipes, non-ISO dates, and unexpected status tokens."""
    malformed_recap = """# Action & Governance Ledger

| Action Item | Owner / DRI | Due Date | Status |
| :--- | :--- | :--- | :--- |
| Deliver schema validation report | person:brien-mcelroy | 11/15/2026 | IN_PROGRESS_CUSTOM |
| | | | |
| Follow up on firewall rules | Amit Ayer | TBD-Invalid-Date | open |
"""
    actions = parse_output02_action_ledger(malformed_recap)
    assert len(actions) == 2
    assert actions[0]["reported_owner"] == "Speaker_Alpha Mcelroy"
    assert actions[0]["due_date"] == "2026-11-15"
    assert actions[0]["status"] == "planned"
    assert actions[1]["reported_owner"] == "Amit Ayer"
    assert actions[1]["due_date"] is None
    assert actions[1]["status"] == "open"


def test_production_regression_heal_orphan_entities_name_error_and_output3_only_is_doc() -> None:
    """Categories 3 & 4: Exact production regression (`NameError: is_doc`) and `--output3-only` `is_doc` conflation."""
    data = {
        "meeting_id": "meeting-nea-20261020",
        "evidence_index": {
            "ev-01": {
                "speaker": "Eduardo Juan More",
                "exact_text": "Eduardo reviewed the Apex Global transmission substation telemetry.",
                "locator": {"kind": "timestamp", "value": "00:01:00"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]},
                {"id": "person:eduardo-juan-more", "type": "person", "name": "Eduardo Juan More", "description": "Apex Global lead.", "evidence_ids": ["ev-01"]},
            ],
            "relationship_signals": [],
        },
    }
    changes: list[str] = []
    # Previously raised NameError: name 'is_doc' is not defined at line 688, or skipped when output3_only=True
    healed = heal_graph_projection(
        data,
        changes,
        metadata={"project": "nea", "output3_only": True, "is_document": False},
        output_1="### [00:01:00]\n\n**Eduardo Juan More:** Eduardo reviewed the Apex Global transmission substation telemetry.",
    )
    assert healed["meeting_archetype"] != "DOCUMENT_MODE"
    signals = healed["graph_mutations"]["relationship_signals"]
    assert any(s.get("from_id") == "person:eduardo-juan-more" for s in signals)
