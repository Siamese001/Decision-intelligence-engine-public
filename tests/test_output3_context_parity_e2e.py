import pytest
from dual_pipeline.canonical_pipeline.context_binding import BoundedContext

class TestOutput3ContextParityE2E:
    def test_context_binding_creation(self):
        ctx = BoundedContext(
            authorized_scope="internal_consulting",
            source_ids=["doc_123"],
            availability_status="AVAILABLE"
        )
        assert ctx.is_available()
        assert ctx.authorized_scope == "internal_consulting"
        assert "doc_123" in ctx.source_ids
        assert ctx.digest is not None

    def test_context_cross_route_parity(self):
        # Verify that context can be applied equally across routes
        ctx_transcript = BoundedContext("tenantA")
        ctx_document = BoundedContext("tenantA")
        assert ctx_transcript.authorized_scope == ctx_document.authorized_scope
