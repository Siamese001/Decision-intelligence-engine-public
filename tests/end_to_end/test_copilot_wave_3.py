from tests.adversarial.test_copilot_retrieval_scope_adversarial import (
    test_project_a_never_returns_project_b,
)
from tests.end_to_end.test_copilot_graph_traversal_e2e import test_copilot_graph_traversal


def test_wave_3_all():
    test_copilot_graph_traversal()
    test_project_a_never_returns_project_b()
