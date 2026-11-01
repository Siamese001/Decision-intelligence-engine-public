import datetime

from dual_pipeline.live_meeting_recorder_core.temporal_policy import classify_temporal_state


def test_temporal_currency():
    as_of = datetime.date(2025, 1, 15)

    # 1. Current
    ast_current = {
        "is_assertion_cleared": True,
        "retrieval_eligible": 1,
        "validation_status": "active",
        "currentness": "current",
        "valid_from": "2025-01-01",
        "valid_to": "2025-12-31",
        "predicate": "HAS_RISK",
        "source_date_normalized": "2025-01-10",
        "has_unresolved_conflict": False,
        "evidence_support": 1.0
    }
    allowed, label, score = classify_temporal_state(ast_current, as_of)
    assert allowed is True
    assert label == ""
    # age is 5 days, half life for HAS_RISK is 3 days
    # score = 0.5 ** (5/3) = 0.31498
    assert 0.31 < score < 0.32

    # 2. Historical
    ast_hist = dict(ast_current)
    ast_hist["currentness"] = "historical"
    allowed, _, _ = classify_temporal_state(ast_hist, as_of)
    assert allowed is False

    # 3. Superseded
    ast_sup = dict(ast_current)
    ast_sup["superseded_by"] = "ast_123"
    allowed, _, _ = classify_temporal_state(ast_sup, as_of)
    assert allowed is False

    # 4. Expired
    ast_exp = dict(ast_current)
    ast_exp["valid_to"] = "2025-01-10" # before as_of
    allowed, _, _ = classify_temporal_state(ast_exp, as_of)
    assert allowed is False

    # 5. Soft labels
    ast_soft = dict(ast_current)
    ast_soft["validation_status"] = "candidate"
    ast_soft["has_unresolved_conflict"] = True
    ast_soft["source_date_normalized"] = ""
    allowed, label, _ = classify_temporal_state(ast_soft, as_of)
    assert allowed is True
    assert "[UNCONFIRMED]" in label
    assert "[UNDATED]" in label
    assert "[CONTESTED]" in label
