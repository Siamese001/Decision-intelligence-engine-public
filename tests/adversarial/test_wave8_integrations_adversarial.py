"""
tests/adversarial/test_wave8_integrations_adversarial.py

Adversarial and edge-case tests for Wave 8: Integration & Extensibility.
Covers:
- Webhook failure handling, network timeouts, and replay attack prevention
- API router unauthorized access and invalid routes
- Malformed calendar parsing and resilient temporal fallback
- Slack signature forgery and timestamp skew attacks
- Empty and boundary warehouse sync exports
- DMS connector unsupported scheme rejections
- Streaming message consumer failure cascades and DLQ poisoning
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.api.server import APIRouter
from dual_pipeline.export.warehouse_sync import WarehouseSyncEngine
from dual_pipeline.integrations.calendar import (
    parse_google_calendar_event,
    parse_ics,
)
from dual_pipeline.integrations.chatops import verify_slack_signature
from dual_pipeline.integrations.dms.connector import get_dms_connector
from dual_pipeline.notifications.webhook_dispatcher import (
    WebhookDispatcher,
    WebhookPayload,
    WebhookSigner,
)
from dual_pipeline.streaming.consumer import (
    DeadLetterQueue,
    IngestionMessage,
    StreamingIngestionConsumer,
)
from storage.database import DatabaseManager


class TestWave8AdversarialWebhooks:
    """Adversarial tests for webhook signing and dispatch failure modes."""

    def test_webhook_exhaustion_on_network_failure(self) -> None:
        def failing_poster(url: str, data: bytes, headers: Dict[str, str], timeout: float) -> tuple[int, bytes]:
            raise ConnectionResetError("Connection abruptly terminated by peer")

        dispatcher = WebhookDispatcher(
            endpoint_url="https://flaky.api.org/webhook",
            secret="test_secret",
            max_retries=3,
            initial_backoff_sec=0.01,
            http_poster=failing_poster,
        )

        payload = WebhookPayload(
            event_id="evt_fail_01",
            event_type="directive.failed",
            directive_id="dir_fail",
            run_id="run_fail",
        )

        receipt = dispatcher.dispatch(payload)
        assert receipt.success is False
        assert receipt.attempt_count == 3
        assert "Connection abruptly terminated" in (receipt.error_message or "")

    def test_replay_attack_rejected(self) -> None:
        signer = WebhookSigner("secret_replay")
        payload_bytes = b'{"action": "grant_approval"}'
        stale_timestamp = int(time.time()) - 600  # 10 minutes ago (> 300s window)

        sig = signer.sign(payload_bytes, stale_timestamp)
        # Should fail verification due to staleness
        is_valid = signer.verify(payload_bytes, sig, stale_timestamp, max_age_seconds=300)
        assert is_valid is False


class TestWave8AdversarialAPIServer:
    """Adversarial security tests for API router."""

    def test_invalid_bearer_token_rejected(self) -> None:
        router = APIRouter(auth_token="valid_secret_token")
        # Missing auth header
        res_missing = router.handle_request("GET", "/v1/health")
        assert res_missing.status_code == 401

        # Wrong token
        res_wrong = router.handle_request("GET", "/v1/health", headers={"Authorization": "Bearer malicious_token"})
        assert res_wrong.status_code == 403

        # Nonexistent route
        res_404 = router.handle_request("GET", "/v1/invalid/route", headers={"Authorization": "Bearer valid_secret_token"})
        assert res_404.status_code == 404


class TestWave8AdversarialCalendar:
    """Adversarial tests for malformed calendar payloads."""

    def test_malformed_ics_graceful_fallback(self) -> None:
        # Corrupted ICS text without DTSTART/DTEND and broken summary
        corrupted_ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Broken ::: Calendar &&& Meeting
CORRUPTED_LINE_WITHOUT_VALUE
END:VEVENT
END:VCALENDAR
"""
        events = parse_ics(corrupted_ics)
        assert len(events) == 1
        ev = events[0]
        assert ev.meeting_name == "Broken__Calendar__Meeting"
        # Falls back to default times rather than crashing
        assert ev.start_time_et == "12:00 PM"
        assert ev.end_time_et == "01:00 PM"

    def test_empty_google_event_handling(self) -> None:
        empty_event: Dict[str, Any] = {}
        meta = parse_google_calendar_event(empty_event)
        assert meta.meeting_name == "Untitled_Meeting"
        assert meta.attendees == []


class TestWave8AdversarialSlackSignatures:
    """Security tests against Slack signature spoofing."""

    def test_slack_signature_forgery_fails(self) -> None:
        secret = "slack_secret"
        now_ts = str(int(time.time()))
        body = '{"action": "approve"}'
        fake_sig = "v0=deadbeef12345678"

        assert verify_slack_signature(secret, now_ts, body, fake_sig) is False

    def test_slack_timestamp_skew_fails(self) -> None:
        secret = "slack_secret"
        old_ts = str(int(time.time()) - 400)  # > 300s
        body = '{"action": "approve"}'
        assert verify_slack_signature(secret, old_ts, body, "v0=any") is False


class TestWave8AdversarialWarehouseSync:
    """Boundary conditions for warehouse sync engine."""

    def test_sync_empty_graph(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty_db"
        empty_dir.mkdir()
        mgr = DatabaseManager(base_dir=empty_dir, allow_unencrypted_dev_fallback=True)

        engine = WarehouseSyncEngine(db_manager=mgr)
        out_dir = tmp_path / "empty_export"
        res = engine.sync(out_dir, destination_format="jsonl")

        assert res.success is True
        assert res.total_records == 0
        assert (out_dir / "sync_manifest.json").exists()


class TestWave8AdversarialDMS:
    """Edge cases for DMS connector registry."""

    def test_unsupported_dms_scheme_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported DMS URI scheme"):
            get_dms_connector("unsupported_protocol://resource/path")


class TestWave8AdversarialStreamingConsumer:
    """Stress and DLQ routing under repeated message failure."""

    def test_poison_message_routes_to_dlq(self) -> None:
        dlq = DeadLetterQueue()

        def poison_handler(msg: IngestionMessage) -> bool:
            raise RuntimeError("Database connection exploded")

        consumer = StreamingIngestionConsumer(handler=poison_handler, max_workers=1, dlq=dlq)
        consumer.start()

        # Submit poison message with max_retries = 2
        consumer.submit(IngestionMessage(
            message_id="poison_001",
            input_uri="file:///bad/audio.wav",
            project="test_proj",
            meeting_name="Poison_Pill",
            date="09/25/2026",
            max_retries=2,
        ))

        time.sleep(0.6)
        consumer.stop()

        assert dlq.size() == 1
        dlq_item = dlq.get_items()[0]
        assert dlq_item.message.message_id == "poison_001"
        assert "Database connection exploded" in dlq_item.failure_reason
