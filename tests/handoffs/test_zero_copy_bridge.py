"""Test suite for Pipeline A ImmutableRingBuffer zero-copy handoffs."""
from __future__ import annotations

import pytest

from dual_pipeline.l0.contracts import HandoffEnvelope
from dual_pipeline.l0.provenance import mint_claim_check
from pipeline_a_intake.bridges.immutable_ring import (
    BackpressurePolicy,
    BufferBackpressureError,
    ImmutableRingBuffer,
)


def test_ring_buffer_zero_copy_and_consumer_offsets():
    """Verifies that enqueuing and reading from ring buffer returns read-only views."""
    ring = ImmutableRingBuffer(capacity=4)
    raw = b"16kHz-pcm-audio-chunk-data"
    claim = mint_claim_check(raw, storage_uri="ring://slot1")
    env = HandoffEnvelope(
        envelope_id="env_1",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="AUDIO_FRAME",
        claim_check=claim,
        payload={},
    )

    slot = ring.enqueue(env, raw)
    assert slot.sequence_id == 1
    assert slot.buffer_view.readonly is True
    assert slot.to_bytes() == raw

    # Read unconsumed for consumer "pipeline_b"
    unconsumed = ring.read_unconsumed_for_consumer("pipeline_b")
    assert len(unconsumed) == 1
    assert unconsumed[0].sequence_id == 1

    # Second read has zero unconsumed
    unconsumed2 = ring.read_unconsumed_for_consumer("pipeline_b")
    assert len(unconsumed2) == 0

    # Independent consumer "pipeline_d" still sees the slot
    unconsumed_d = ring.read_unconsumed_for_consumer("pipeline_d")
    assert len(unconsumed_d) == 1


def test_ring_buffer_backpressure_fail_fast():
    """Verifies that FAIL_FAST raises BufferBackpressureError when capacity is exceeded."""
    ring = ImmutableRingBuffer(capacity=2, backpressure_policy=BackpressurePolicy.FAIL_FAST)
    for i in range(2):
        raw = f"data_{i}".encode("utf-8")
        claim = mint_claim_check(raw, storage_uri=f"ring://slot_{i}")
        env = HandoffEnvelope(
            envelope_id=f"env_{i}",
            source_pipeline="PIPELINE_A",
            target_pipeline="PIPELINE_B",
            payload_type="AUDIO_FRAME",
            claim_check=claim,
            payload={},
        )
        ring.enqueue(env, raw)

    # Third enqueue must raise
    raw_overflow = b"overflow_data"
    claim_overflow = mint_claim_check(raw_overflow, storage_uri="ring://slot_overflow")
    env_overflow = HandoffEnvelope(
        envelope_id="env_overflow",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="AUDIO_FRAME",
        claim_check=claim_overflow,
        payload={},
    )
    with pytest.raises(BufferBackpressureError):
        ring.enqueue(env_overflow, raw_overflow)


def test_ring_buffer_digest_mismatch_rejected():
    """Verifies that tampering with raw data triggers digest integrity rejection."""
    ring = ImmutableRingBuffer(capacity=4)
    raw = b"authentic-data"
    claim = mint_claim_check(raw, storage_uri="ring://slot1")
    tampered_raw = b"tampered-data"

    env = HandoffEnvelope(
        envelope_id="env_tampered",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="AUDIO_FRAME",
        claim_check=claim,  # Claim check digest matches authentic-data
        payload={},
    )

    with pytest.raises(ValueError, match="Data integrity violation"):
        ring.enqueue(env, tampered_raw)
