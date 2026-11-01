from tests.adversarial.test_copilot_observability_fault_adversarial import (
    test_fault_injection_yields_outcome_code_and_counter,
    test_read_only_jsonl_broadcasts_and_logs,
)
from tests.end_to_end.test_copilot_answer_receipt_e2e import (
    test_answer_receipt_schema_validates,
    test_ws_feedback_creates_ledger_row,
)


def test_wave_8_all():
    test_answer_receipt_schema_validates()
    test_ws_feedback_creates_ledger_row()
    test_fault_injection_yields_outcome_code_and_counter()
    test_read_only_jsonl_broadcasts_and_logs()
