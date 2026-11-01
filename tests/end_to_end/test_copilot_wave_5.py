from tests.adversarial.test_copilot_live_graph_delta_edge_cases import test_live_vs_graph_delta
from tests.adversarial.test_copilot_prompt_injection_adversarial import (
    test_injection_turn_inside_tags,
    test_parse_attendees_empty,
    test_speaker_collapse_anonymizes_labels,
)
from tests.end_to_end.test_copilot_context_assembly_e2e import (
    test_decision_stated_at_turn_5_present_at_turn_120,
    test_system_message_is_byte_identical,
)


def test_wave_5_all():
    test_system_message_is_byte_identical()
    test_decision_stated_at_turn_5_present_at_turn_120()
    test_injection_turn_inside_tags()
    test_speaker_collapse_anonymizes_labels()
    test_parse_attendees_empty()
    test_live_vs_graph_delta()
