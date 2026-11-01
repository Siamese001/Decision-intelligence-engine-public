import os
from unittest.mock import MagicMock

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    GraphLoadStatus,
    has_c19_marker,
    load_copilot_graph,
)
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer


def test_c19_marker():
    assert has_c19_marker("Some text with $1.2M in it")
    assert not has_c19_marker("wait for 2027")

def test_egress_sanitizer():
    with pytest.raises(EgressSanitizationError):
        LocalPreEgressSanitizer.sanitize_messages([{"content": "sk_test_redacted_placeholder"}])
    with pytest.raises(EgressSanitizationError):
        LocalPreEgressSanitizer.sanitize_messages([{"content": "Some text $1.2M"}])

def test_fallback_graph_load():
    os.environ["COPILOT_ALLOW_FALLBACK_GRAPH"] = "0"
    os.environ["NEE_RUNTIME_ROOT"] = "/tmp/does_not_exist"
    status, topology, retriever = load_copilot_graph(attendees=[])
    assert status == GraphLoadStatus.ABSENT

def test_copilot_disables_on_error():
    adv = LiveCopilotAdvisor("proj", "meet", MagicMock(), None)
    adv.disabled = True
    assert adv._should_throttle(1.0, is_priority=False)
