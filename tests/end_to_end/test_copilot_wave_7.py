from tests.adversarial.test_copilot_concurrency_adversarial import (
    test_coalescing_queue,
    test_concurrency_lock_and_snapshot,
)
from tests.end_to_end.test_copilot_deadline_failover_e2e import (
    test_degraded_cue_when_no_budget,
    test_failover_when_budget_remains,
    test_finish_reason_length,
)


def test_wave_7_all():
    test_concurrency_lock_and_snapshot()
    test_coalescing_queue()
    test_failover_when_budget_remains()
    test_degraded_cue_when_no_budget()
    test_finish_reason_length()
