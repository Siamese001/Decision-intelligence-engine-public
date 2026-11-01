from tests.adversarial.test_copilot_temporal_edge_cases import (
    test_normalize_project_terms,
    test_session_packet_v2_fields,
    test_two_project_fixture_leaks_nothing,
)
from tests.end_to_end.test_copilot_temporal_currency_e2e import test_temporal_currency


def test_wave_4_all():
    test_temporal_currency()
    test_two_project_fixture_leaks_nothing()
    test_session_packet_v2_fields()
    test_normalize_project_terms()
