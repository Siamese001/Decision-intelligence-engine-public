import pytest
from dual_pipeline.canonical_pipeline.context_binding import BoundedContext

class TestOutput3ContextParityAdversarial:
    def test_missing_context_fails_closed(self):
        ctx = BoundedContext(
            authorized_scope="UNKNOWN",
            availability_status="UNAVAILABLE"
        )
        assert not ctx.is_available()

    def test_cross_tenant_leakage_prevention(self):
        ctxA = BoundedContext(authorized_scope="tenantA")
        ctxB = BoundedContext(authorized_scope="tenantB")
        assert ctxA.digest != ctxB.digest
        assert ctxA.authorized_scope != ctxB.authorized_scope
