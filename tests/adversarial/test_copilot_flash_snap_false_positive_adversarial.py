from dual_pipeline.live_meeting_recorder_core.flash_snap import evaluate_flash_snap


def test_flash_snap_requires_two_anchors():
    # Only 1 anchor should not trigger
    res = evaluate_flash_snap("just one anchor", anchors=["one", "two", "three"], graph_hit=True)
    assert res is False

    # 2 anchors + graph_hit should trigger
    res2 = evaluate_flash_snap("one and two", anchors=["one", "two", "three"], graph_hit=True)
    assert res2 is True

    # 2 anchors but no graph_hit should not trigger
    res3 = evaluate_flash_snap("one and two", anchors=["one", "two", "three"], graph_hit=False)
    assert res3 is False
