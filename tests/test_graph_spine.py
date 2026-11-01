"""Unit tests for Layer 1 Deterministic Graph Spine."""


from dual_pipeline.graph_constrained_projection.graph_spine import GraphSpineResolver
from dual_pipeline.graph_constrained_projection.models import Organization


def test_graph_spine_stakeholder_resolution():
    """Verify that client and partner stakeholders resolve to their exact organizations."""
    resolver = GraphSpineResolver()

    # 1. harper Hoffman must resolve to Apex Global Enterprises (Client Lead)
    mandy = resolver.get_stakeholder("harper Hoffman")
    assert mandy is not None, "harper Hoffman must be resolvable"
    assert mandy.organization == Organization.apex_global.value
    assert mandy.is_client is True
    assert mandy.is_delivery_partner is False

    # 2. Jake Stark must resolve to PartnerAdvisory (Delivery Pod Contributor)
    jake = resolver.get_stakeholder("Jake Stark")
    assert jake is not None, "Jake Stark must be resolvable"
    assert jake.organization == Organization.partner_advisory.value
    assert jake.is_client is False
    assert jake.is_delivery_partner is True

    # 3. Jen Pan must resolve to PartnerAdvisory (Delivery Lead)
    jen = resolver.get_stakeholder("Jen Pan")
    assert jen is not None, "Jen Pan must be resolvable"
    assert jen.organization == Organization.partner_advisory.value
    assert jen.is_client is False
    assert jen.is_delivery_partner is True


def test_graph_spine_sow_milestone_retrieval():
    """Verify contractual SOW milestone target retrieval."""
    resolver = GraphSpineResolver()
    m1 = resolver.get_sow_constraint("lens_milestone_1")
    assert m1 is not None, "lens_milestone_1 constraint must exist"
    assert m1.is_contractual is True
    assert m1.confidence_threshold == 0.90
    assert "90%" in m1.target_metric


def test_graph_spine_unknown_entity():
    """Verify unknown entity resolution."""
    resolver = GraphSpineResolver()
    assert resolver.get_stakeholder("Nonexistent Ghost Stakeholder") is None
    assert resolver.resolve_entity_org("Nonexistent Ghost Stakeholder") == Organization.UNKNOWN.value
