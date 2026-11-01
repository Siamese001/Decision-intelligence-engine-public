import pytest
from dual_pipeline.transcript_qa_core.frozen_gates import check_frozen_dependency_validity
from dual_pipeline.transcript_qa_core.graph_ontology_healer import _heal_workstreams_and_archetype


class MockStateMachine:
    def __init__(self, current_attempt: int = 1):
        self.current_attempt = current_attempt


def test_frozen_dependency_validity_allows_remediation_under_force_rerun():
    sm_att1 = MockStateMachine(current_attempt=1)
    sm_att2 = MockStateMachine(current_attempt=2)
    meta = {"force_rerun": True}
    content = "# Passing Output 1 Content"

    # Under attempt 1, force_rerun invalidates prior release artifacts
    valid_att1, err_att1 = check_frozen_dependency_validity(
        "output_1", content, sm_att1, metadata=meta
    )
    assert valid_att1 is False
    assert "force_rerun" in (err_att1 or "") or "invalidates" in (err_att1 or "")

    # Under attempt 2 (remediation), freshly frozen artifact in current session is NOT invalidated
    valid_att2, err_att2 = check_frozen_dependency_validity(
        "output_1", content, sm_att2, metadata=meta
    )
    assert valid_att2 is True
    assert err_att2 is None


def test_heal_lens_ontology_preserves_tactical_archetype():
    data = {
        "graph_mutations": {},
        "signals": [],
    }
    entities = []
    signals = []
    changes = []
    metadata = {
        "project": "project_lens",
        "archetype": "TACTICAL_ALIGNMENT",
    }
    _heal_workstreams_and_archetype(data, entities, signals, changes, metadata=metadata)
    assert data["meeting_archetype"] == "TACTICAL_ALIGNMENT"
    assert data["graph_mutations"]["meeting_archetype"] == "TACTICAL_ALIGNMENT"


def test_jake_appendix_healer_preserves_grounded_content_in_non_ocr_meeting():
    from dual_pipeline.transcript_qa_core.recap_healer_helpers import _heal_output_2_jake_appendix

    text = (
        "Date: 10/09/2026\n\n"
        "# Jake Stark — PartnerAdvisory, UI and Prompting Lead\n\n"
        "- **Ticket 179 Progression**: Moved item 179 to In Progress following coordination with team.\n\n"
        "- **Standup Facilitation & Scope Assessment**: Moderated discussion on feedback and demo deliverables.\n"
    )
    changes = []
    audit_log = []
    meta = {"date": "10/09/2026"}
    healed = _heal_output_2_jake_appendix(text, changes, audit_log, meta=meta)
    assert "OCR Workstream Leadership" not in healed
    assert "Ticket 179 Progression" in healed


def test_lens_ontology_grounds_dynamodb_and_avoids_ungrounded_mandy():
    from dual_pipeline.transcript_qa_core.lens_ontology import heal_lens_core_systems

    entities = []
    evidence_index = {
        "ev-01": {"exact_text": "We scan the table in DynamoDB for historical records", "text": "We scan the table in DynamoDB for historical records"}
    }
    changes = []
    norm_src = "we have to scan the table in dynamodb and mandy sent an email"

    heal_lens_core_systems(entities, evidence_index, changes, norm_src)

    # DynamoDB should be grounded
    ddb = next((e for e in entities if e["id"] == "system:dynamodb"), None)
    assert ddb is not None
    assert ddb["name"] == "Amazon DynamoDB"

    # Mandy should NOT be grounded because no evidence mentions Mandy
    mandy = next((e for e in entities if e["id"] == "person:mandy-hopmann"), None)
    assert mandy is None

