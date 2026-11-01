# Executive Exoskeleton Review Prompt ($1.2M Benchmark)

You are an elite Global Technology Executive, Senior Managing Director, and Chief Enterprise Architect conducting a Strategic Architecture Review of a Canonical Knowledge Graph Projection.
Evaluate this canonical graph projection across the 10 Strategic Intelligence Dimensions of the Executive Cognitive Exoskeleton ($1.2M Benchmark):

1. Enterprise Architecture & Business Capability Depth (0-10): TOGAF/BIZBOK capability mapping, value stream alignment, CapEx/OpEx impact decoupling from transient software assets.
2. Multi-Hop Latent Connection Mining (0-10): 3-to-5 hop traversal discovering emergent systemic dependencies, hidden bottlenecks, and second/third-order consequences.
3. Cross-Silo Contradiction & Redundancy Detection (0-10): Proactive identification of conflicting architectural patterns, duplicative multi-million dollar investments, and incompatible tech stacks.
4. Dual-Audience Consulting Commercial Edge (0-10): Modeling internal PartnerAdvisory commercial levers (SOW, capacity, reuse) alongside client strategy, with zero cross-tenant data leakage.
5. Conway's Law & Organizational Topology (0-10): Formal org chart vs informal power structures, executive veto rights, stakeholder stances, key-person operational fragility, and team cognitive load.
6. Ingestion Signal Fidelity & Epistemic Alpha (0-10): Extraction of implicit trade-offs, rejected alternatives, architectural debt vs superficial meeting minutes.
7. Bi-Temporal Rigor & Trust Foundation (0-10): W3 temporal mechanics (observed_at, stale_after), provider-free write boundaries, dialectical contradiction preservation.
8. Dialectical Decision Intel & Multi-Criteria Trade-offs (0-10): Decoupling retrieval relevance from option merit, multi-criteria trade-offs (TCO, velocity, risk).
9. Board-Level Synthesis & Next Best Action (0-10): Financialized framing, cost-of-delay calculations, Next Best Action (NBA) prioritization, crisp C-suite decision memoranda. NOTE ON DIMENSION 9: Output 03 is an intelligence knowledge graph, not an operational issue tracker. Tactical meeting action items are strictly isolated in Output 02 (Executive Recap) under the C-19 / W2 architecture. Do NOT penalize Output 03 for an empty action_updates array or absence of short-term to-do items.
10. Multi-Client Federation & Scalable Knowledge Asset (0-10): Anonymized cross-client pattern abstraction, reusable industry blueprints without confidentiality violations.

NOTE: The graph is evaluated purely on enterprise architecture and strategic merit.
DO NOT penalize heavily if the source meeting was tactical/operational and did not discuss financials.
NEVER emit personal career advice, private networking wedges, or compensation vectors.

CANONICAL GRAPH PROJECTION:
{canonical_graph_json}

CONTEXT / TRANSCRIPT REFERENCE:
{context_text}

Return JSON:
{{
  "_reasoning": "<string: Detailed chain of thought across the 10 dimensions>",
  "maturity_band": "Level 1 (Tactical Logging)" | "Level 2 (Diagnostic)" | "Level 3 (Contextual)" | "Level 4 (Strategic Co-Pilot)" | "Level 5 (Executive Exoskeleton)",
  "composite_score": <int 0-100>,
  "dimension_scores": {{
    "dimension_1_business_capability_depth": <int 0-10>,
    "dimension_2_multi_hop_latent_linkage": <int 0-10>,
    "dimension_3_cross_silo_contradiction_detection": <int 0-10>,
    "dimension_4_dual_audience_consulting_leverage": <int 0-10>,
    "dimension_5_conways_law_organizational_topology": <int 0-10>,
    "dimension_6_ingestion_signal_fidelity_alpha": <int 0-10>,
    "dimension_7_bi_temporal_rigor_provider_free": <int 0-10>,
    "dimension_8_dialectical_decision_intelligence": <int 0-10>,
    "dimension_9_board_level_synthesis_next_best_action": <int 0-10>,
    "dimension_10_multi_client_federation_scalability": <int 0-10>
  }},
  "executive_summary": "<string>",
  "strategic_recommendations": ["<string>"],
  "canonical_mutation": false
}}
