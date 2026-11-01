"""
tests/test_wave8_integrations_e2e.py

End-to-end tests for Wave 8: Integration & Extensibility.
Covers:
- W8-01: WebhookDispatcher (HMAC-SHA256 signature, retry policy, receipt generation)
- W8-02: REST API Server & APIRouter (OpenAPI 3.0 schema, Bearer token auth, dynamic routes)
- W8-03: Calendar Integration (ICS parsing, Google Calendar, MS Graph, ET time normalization)
- W8-04: ChatOps HITL (Slack Block Kit, Teams Adaptive Cards, Slack signature verification)
- W8-05: CI/CD Pipeline (GitHub annotation formatting, step summary generation)
- W8-06: Warehouse Sync (JSONL, CSV, SQLite replica export, CLI nee graph-sync)
- W8-07: Enterprise Directory Sync (SCIM 2.0, MS Graph, attendee token resolution to person:<id>)
- W8-08: DMS Connectors (SharePoint, Google Drive, Confluence, Local files)
- W8-09: Message Queue Consumer (Bounded concurrency, worker pool, event processing)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.api.server import APIResponse, APIRouter, generate_openapi_spec
from dual_pipeline.export.warehouse_sync import WarehouseSyncEngine
from dual_pipeline.integrations.calendar import (
    parse_google_calendar_event,
    parse_ics,
    parse_ms_graph_event,
)
from dual_pipeline.integrations.chatops import (
    HITLOption,
    build_slack_block_kit_hitl_card,
    build_teams_adaptive_card_hitl_card,
    parse_slack_interaction_payload,
    verify_slack_signature,
)
from dual_pipeline.integrations.ci_cd import (
    format_github_annotation,
    generate_github_step_summary,
)
from dual_pipeline.integrations.directory import (
    DirectorySyncService,
    parse_scim_user_payload,
)
from dual_pipeline.integrations.dms.connector import (
    GoogleDriveConnector,
    LocalFileConnector,
    SharePointConnector,
    get_dms_connector,
)
from dual_pipeline.notifications.webhook_dispatcher import (
    WebhookDispatcher,
    WebhookPayload,
    WebhookSigner,
)
from dual_pipeline.streaming.consumer import (
    IngestionMessage,
    StreamingIngestionConsumer,
)
from storage.database import CanonicalGraphNode, DatabaseManager, GraphEdge


class TestWave8WebhookDispatcherE2E:
    """W8-01: HMAC-SHA256 signed webhook delivery."""

    def test_webhook_dispatch_and_signature_verification(self) -> None:
        secret = "super_secure_webhook_secret_123"
        captured_requests = []

        def mock_poster(url: str, data: bytes, headers: Dict[str, str], timeout: float) -> tuple[int, bytes]:
            captured_requests.append({"url": url, "data": data, "headers": headers})
            return 200, b'{"received": true}'

        dispatcher = WebhookDispatcher(
            endpoint_url="https://api.enterprise.corp/nee-webhook",
            secret=secret,
            max_retries=2,
            http_poster=mock_poster,
        )

        payload = WebhookPayload(
            event_id="evt_test_001",
            event_type="directive.completed",
            directive_id="dir_wave8_001",
            run_id="run_wave8_001",
            data={"status": "COMPLETE", "nodes_created": 15},
        )

        receipt = dispatcher.dispatch(payload)

        assert receipt.success is True
        assert receipt.status_code == 200
        assert receipt.attempt_count == 1
        assert len(captured_requests) == 1

        req = captured_requests[0]
        sig_header = req["headers"]["X-NEE-Signature"]
        timestamp_header = int(req["headers"]["X-NEE-Timestamp"])
        assert sig_header.startswith("v1=")
        sig_hash = sig_header[3:]

        # Verify signature with WebhookSigner
        signer = WebhookSigner(secret)
        is_valid = signer.verify(req["data"], sig_hash, timestamp_header)
        assert is_valid is True


class TestWave8APIServerE2E:
    """W8-02: REST API Server mode and APIRouter."""

    def test_api_router_openapi_and_endpoints(self) -> None:
        router = APIRouter(auth_token="test_bearer_token")

        # 1. Test OpenAPI schema endpoint without auth
        spec = generate_openapi_spec()
        assert spec["openapi"] == "3.0.3"
        assert "/v1/ingest/meeting" in spec["paths"]
        assert "/v1/health" in spec["paths"]

        # 2. Test 401 Unauthorized when missing Bearer token
        res_unauth = router.handle_request("GET", "/v1/health")
        assert res_unauth.status_code == 401

        # 3. Test 200 OK with valid Bearer token
        headers = {"Authorization": "Bearer test_bearer_token"}
        res_health = router.handle_request("GET", "/v1/health", headers=headers)
        assert res_health.status_code == 200
        assert res_health.data["status"] == "healthy"

        # 4. Register custom route with dynamic param
        def directive_handler(body: Dict[str, Any], params: Dict[str, str]) -> APIResponse:
            return APIResponse(status_code=200, data={"directive_id": params["directive_id"], "state": "COMPLETE"})

        router.register("GET", "/v1/directives/{directive_id}", directive_handler)
        res_dir = router.handle_request("GET", "/v1/directives/dir_999", headers=headers)
        assert res_dir.status_code == 200
        assert res_dir.data["directive_id"] == "dir_999"


class TestWave8CalendarIntegrationE2E:
    """W8-03: Calendar event parsing and temporal auto-population."""

    def test_ics_parser(self, tmp_path: Path) -> None:
        ics_text = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
BEGIN:VEVENT
DTSTART:20260925T160516Z
DTEND:20260925T170516Z
SUMMARY:Google Alex Shreyak Amit Anya
DESCRIPTION:Project Nexus quarterly architecture sync
ORGANIZER;CN="Alex Brennan":mailto:alex@google.com
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;CN="Amit Ayer":mailto:amit@consulting.example.com
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;CN="Shreyak Gupta":mailto:shreyak@consulting.example.com
END:VEVENT
END:VCALENDAR
"""
        ics_file = tmp_path / "meeting.ics"
        ics_file.write_text(ics_text, encoding="utf-8")

        events = parse_ics(ics_file)
        assert len(events) == 1
        ev = events[0]
        assert ev.meeting_name == "Google_Alex_Shreyak_Amit_Anya"
        assert ev.date == "09/25/2026"
        assert ev.start_time_et == "12:05 PM"
        assert ev.end_time_et == "01:05 PM"
        assert "Amit Ayer" in ev.attendees
        assert "Shreyak Gupta" in ev.attendees
        assert ev.organizer == "Alex Brennan"

    def test_google_and_ms_graph_event_parsers(self) -> None:
        gcal_event = {
            "summary": "Nexus Architecture Review",
            "start": {"dateTime": "2026-09-25T12:05:16-04:00"},
            "end": {"dateTime": "2026-09-25T13:05:16-04:00"},
            "attendees": [{"displayName": "Amit Ayer"}, {"email": "alex@google.com"}],
            "organizer": {"displayName": "Alex Brennan"},
        }
        g_meta = parse_google_calendar_event(gcal_event)
        assert g_meta.meeting_name == "Nexus_Architecture_Review"
        assert g_meta.date == "09/25/2026"
        assert "Amit Ayer" in g_meta.attendees

        ms_event = {
            "subject": "Leadership Weekly",
            "start": {"dateTime": "2026-09-25T16:05:16Z"},
            "end": {"dateTime": "2026-09-25T17:05:16Z"},
            "attendees": [{"emailAddress": {"name": "Mandy", "address": "mandy@consulting.example.com"}}],
            "organizer": {"emailAddress": {"name": "Jen"}},
        }
        ms_meta = parse_ms_graph_event(ms_event)
        assert ms_meta.meeting_name == "Leadership_Weekly"
        assert ms_meta.date == "09/25/2026"
        assert ms_meta.start_time_et == "12:05 PM"
        assert ms_meta.end_time_et == "01:05 PM"


class TestWave8ChatOpsE2E:
    """W8-04: Slack Block Kit and Teams Adaptive Cards."""

    def test_slack_and_teams_card_generation(self) -> None:
        options = [
            HITLOption("opt_1", "Approve Graph Mutation", 0.95, recommended=True),
            HITLOption("opt_2", "Reject & Quarantine", 0.05),
        ]
        slack_card = build_slack_block_kit_hitl_card(
            decision_id="dec_001",
            directive_id="dir_001",
            question="Authorize mutation for Project Nexus?",
            options=options,
            context="High-confidence decision",
        )
        assert "blocks" in slack_card
        assert len(slack_card["blocks"]) >= 3

        teams_card = build_teams_adaptive_card_hitl_card(
            decision_id="dec_001",
            directive_id="dir_001",
            question="Authorize mutation for Project Nexus?",
            options=options,
        )
        assert teams_card["type"] == "AdaptiveCard"
        assert len(teams_card["actions"]) == 2

    def test_slack_signature_and_interaction(self) -> None:
        secret = "test_signing_secret"
        now_ts = str(int(time.time()))
        body = 'payload={"actions":[{"value":"{\\"decision_id\\":\\"dec_01\\",\\"selected_option\\":\\"opt_1\\"}"}]}'

        import hashlib
        import hmac
        sig_base = f"v0:{now_ts}:{body}".encode("utf-8")
        sig = "v0=" + hmac.new(secret.encode("utf-8"), sig_base, hashlib.sha256).hexdigest()

        assert verify_slack_signature(secret, now_ts, body, sig) is True

        parsed = parse_slack_interaction_payload(json.loads(body.replace("payload=", "")))
        assert parsed is not None
        assert parsed["decision_id"] == "dec_01"
        assert parsed["selected_option"] == "opt_1"


class TestWave8WarehouseSyncE2E:
    """W8-06: Warehouse and Lakehouse synchronization."""

    def test_warehouse_sync_jsonl_csv_sqlite(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sync_src"
        db_path.mkdir()
        mgr = DatabaseManager(base_dir=db_path, allow_unencrypted_dev_fallback=True)

        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="sync_node_1", evidence_id="ev_sync", type="SYSTEM", name="Warehouse Sync Source"
        ))
        mgr.upsert_canonical_edge(GraphEdge(
            edge_id="sync_edge_1", from_node_id="sync_node_1", predicate="syncs_to", to_node_id="sync_node_1"
        ))

        engine = WarehouseSyncEngine(db_manager=mgr)

        # 1. Sync to JSONL
        out_jsonl = tmp_path / "wh_jsonl"
        res_jsonl = engine.sync(out_jsonl, destination_format="jsonl")
        assert res_jsonl.success is True
        assert (out_jsonl / "canonical_graph_nodes.jsonl").exists()
        assert (out_jsonl / "sync_manifest.json").exists()

        # 2. Sync to CSV
        out_csv = tmp_path / "wh_csv"
        res_csv = engine.sync(out_csv, destination_format="csv")
        assert res_csv.success is True
        assert (out_csv / "canonical_graph_nodes.csv").exists()

        # 3. Sync to SQLite replica
        out_sqlite = tmp_path / "wh_sqlite"
        res_sqlite = engine.sync(out_sqlite, destination_format="sqlite")
        assert res_sqlite.success is True
        assert (out_sqlite / "canonical_graph_nodes.sqlite").exists()

    def test_cli_graph_sync(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cli_sync_src"
        db_path.mkdir()
        mgr = DatabaseManager(base_dir=db_path, allow_unencrypted_dev_fallback=True)
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="cli_sync_node", evidence_id="ev_cli_sync", type="SYSTEM", name="CLI Sync Node"
        ))

        from dual_pipeline.cli.engine import execute_command
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()
        out_dir = tmp_path / "cli_wh_out"
        args = parser.parse_args([
            "graph-sync",
            "--runtime-root", str(db_path),
            "--destination", "jsonl",
            "--output-dir", str(out_dir),
        ])
        res = execute_command(args)
        assert res["status"] == "PASS"
        assert (out_dir / "canonical_graph_nodes.jsonl").exists()
        assert (out_dir / "sync_manifest.json").exists()


class TestWave8CICDE2E:
    """W8-05: CI/CD Pipeline & GitHub Action formatting."""

    def test_github_annotation_formatting(self) -> None:
        ann = format_github_annotation("error", "Gate failure: syntax error", file_path="core.py", line=42, col=5, title="Lint Error")
        assert ann == "::error file=core.py,line=42,col=5,title=Lint Error::Gate failure: syntax error"

    def test_github_step_summary_generation(self) -> None:
        rows = [{"wave": "8", "description": "Integrations", "status": "✅ COMPLETE", "receipt": "reports/wave8_receipt.txt"}]
        gates = {"Gate 1": True, "Gate 2": False}
        summary = generate_github_step_summary("NEE CI Summary", rows, gates)
        assert "## NEE CI Summary" in summary
        assert "Wave Number" in summary
        assert "✅ COMPLETE" in summary
        assert "❌ FAIL" in summary


class TestWave8DirectorySyncE2E:
    """W8-07: Enterprise directory roster and speaker resolution."""

    def test_scim_and_directory_service_resolution(self) -> None:
        scim_user = {
            "id": "u_alex_001",
            "userName": "alexb",
            "displayName": "Alex Brennan",
            "emails": [{"value": "alex.brennan@google.com", "primary": True}],
            "name": {"givenName": "Alex", "familyName": "Brennan"},
            "title": "Staff Architect",
            "department": "Core Intelligence",
        }
        user = parse_scim_user_payload(scim_user)
        assert user.canonical_entity_id == "person:u_alex_001"

        service = DirectorySyncService([user])
        resolved_email = service.resolve_speaker_token("alex.brennan@google.com")
        assert resolved_email is not None
        assert resolved_email.user_id == "u_alex_001"

        resolved_name = service.resolve_speaker_token("Alex Brennan")
        assert resolved_name is not None

        resolved_alias = service.resolve_speaker_token("Alex B.")
        assert resolved_alias is not None


class TestWave8DMSConnectorsE2E:
    """W8-08: Document Management System ingress connectors."""

    def test_dms_connector_factory_and_fetching(self, tmp_path: Path) -> None:
        local_src = tmp_path / "sample.docx"
        local_src.write_text("Test local document", encoding="utf-8")

        # Local
        local_conn = get_dms_connector(f"file://{local_src}")
        assert isinstance(local_conn, LocalFileConnector)
        dest_local = local_conn.fetch_document(f"file://{local_src}", tmp_path / "dest_local")
        assert dest_local.exists()

        # SharePoint
        sp_conn = get_dms_connector("sharepoint://partner_advisory.sharepoint.com/sites/nexus/doc1.docx")
        assert isinstance(sp_conn, SharePointConnector)
        dest_sp = sp_conn.fetch_document("sharepoint://partner_advisory.sharepoint.com/sites/nexus/doc1.docx", tmp_path / "dest_sp")
        assert dest_sp.exists()

        # Google Drive
        gdrive_conn = get_dms_connector("gdrive://drive.google.com/file/d/xyz123/document.pdf")
        assert isinstance(gdrive_conn, GoogleDriveConnector)
        dest_gdrive = gdrive_conn.fetch_document("gdrive://drive.google.com/file/d/xyz123/document.pdf", tmp_path / "dest_gdrive")
        assert dest_gdrive.exists()


class TestWave8StreamingConsumerE2E:
    """W8-09: Event-driven message consumer with bounded concurrency."""

    def test_streaming_consumer_processing(self) -> None:
        processed_msgs = []

        def mock_handler(msg: IngestionMessage) -> bool:
            processed_msgs.append(msg.message_id)
            return True

        consumer = StreamingIngestionConsumer(handler=mock_handler, max_workers=2)
        consumer.start()

        for i in range(5):
            consumer.submit(IngestionMessage(
                message_id=f"msg_{i}",
                input_uri="file:///tmp/meeting.wav",
                project="project_nexus",
                meeting_name=f"Meeting_{i}",
                date="09/25/2026",
            ))

        time.sleep(0.5)
        consumer.stop()

        assert len(processed_msgs) == 5
        assert consumer.processed_count == 5
