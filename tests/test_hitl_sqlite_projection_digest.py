"""Preserve signed HITL record shape when reading the SQLite projection."""

from dual_pipeline.core import digest
from dual_pipeline.hitl_governance_core.ledger import _validate_record_chain
from dual_pipeline.storage.hitl_sqlite_store import _record_to_row, _row_to_record


def test_metadata_only_operator_class_keeps_original_signed_shape() -> None:
    record = {
        "entry_id": "entry-1", "sequence_number": 0,
        "decision_id": "decision-1", "category": "test",
        "operator_id": "operator", "prompt": "Prompt",
        "options": ["yes", "no"], "selected_option": "yes",
        "selected_key": "yes", "rationale": None,
        "previous_entry_hash": "0" * 64,
        "recorded_at": "2026-10-06T00:00:00+00:00",
        "metadata": {"operator_class": "HUMAN"},
    }
    record["entry_digest"] = digest(record)

    reconstructed = _row_to_record(_record_to_row(record))

    assert reconstructed == record
    _validate_record_chain([reconstructed])


def test_sqlite_projection_does_not_hide_other_digest_mismatch() -> None:
    record = {
        "entry_id": "entry-1", "sequence_number": 0,
        "decision_id": "decision-1", "category": "test",
        "operator_id": "operator", "prompt": "Prompt",
        "options": ["yes", "no"], "selected_option": "yes",
        "selected_key": "yes", "rationale": None,
        "previous_entry_hash": "0" * 64,
        "recorded_at": "2026-10-06T00:00:00+00:00",
        "metadata": {"operator_class": "HUMAN"},
    }
    record["entry_digest"] = digest(record)
    row = list(_record_to_row(record))
    row[5] = "Modified prompt"

    reconstructed = _row_to_record(row)
    assert reconstructed["prompt"] == "Modified prompt"
    try:
        _validate_record_chain([reconstructed])
    except Exception as exc:
        assert "digest mismatch" in str(exc).lower()
    else:
        raise AssertionError("Modified SQLite content passed the signed digest check")
