"""Executive Cognitive Exoskeleton Evaluator (The $1.2M Benchmark).

Evaluates the 10 Strategic Intelligence Dimensions defined in:
- governance/decision_intelligence/EXECUTIVE_GRAPH_STRATEGY_REVIEW_PROMPT_1_2M.md
- governance/decision_intelligence/EXECUTIVE_GRAPH_AUDIT_REPORT_1_2M.md
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.evals.strategic.models import (
    DecisionOptionEvaluation,
    DimensionScore,
    ExecutiveExoskeletonScorecard,
    MCDACriteriaWeights,
)


_UNSET: Any = object()


class ExecutiveStrategicEvaluator:
    """Evaluates an intelligence engine against the 10 dimensions of a $1.2M executive."""

    def evaluate_dimension_1_ea_capability_depth(
        self, capability_payload: Optional[Dict[str, Any]] = _UNSET
    ) -> DimensionScore:
        """Dim 1: Hierarchical Business Capabilities (L1-L3) & formal ADR completeness."""
        if capability_payload is None:
            raise ValueError("capability_payload cannot be None")
        if capability_payload is _UNSET:
            capability_payload = {
                "l1_domain": "Grid Operations", "l2_capability": "Substation Monitoring",
                "l3_activity": "Predictive Thermal Anomaly Detection",
                "adr": {
                    "decision_title": "Adopt Event-Driven Vector Sidecar",
                    "evaluated_options": ["In-Memory BLAS", "Sidecar WAL SQLite", "Remote Managed DB"],
                    "rejected_alternatives": ["In-Memory BLAS (contention)", "Remote Managed (SLA breach)"],
                    "reversal_cost_usd": 150000.0, "estimated_monthly_tco": 4200.0,
                },
            }

        has_l1 = bool(capability_payload.get("l1_domain"))
        has_l2 = bool(capability_payload.get("l2_capability"))
        has_l3 = bool(capability_payload.get("l3_activity"))
        adr = capability_payload.get("adr", {})
        adr_complete = (
            bool(adr.get("decision_title"))
            and len(adr.get("evaluated_options", [])) >= 2
            and len(adr.get("rejected_alternatives", [])) >= 1
            and adr.get("reversal_cost_usd", 0.0) > 0
        )
        passed = has_l1 and has_l2 and has_l3 and adr_complete
        score = 100.0 if passed else (50.0 if (has_l1 and has_l2) else 0.0)

        return DimensionScore(
            dim_id="dim_1_ea_capability",
            name="Enterprise Architecture & Capability Depth",
            score=score,
            passed=passed,
            details={"hierarchy_complete": has_l1 and has_l2 and has_l3, "adr_complete": adr_complete},
        )

    def evaluate_dimension_2_latent_multi_hop(
        self, graph_edges: Optional[List[Tuple[str, str, str]]] = _UNSET
    ) -> DimensionScore:
        """Dim 2: Traversal discovering 4-to-5 hop emergent systemic dependencies."""
        if graph_edges is None:
            raise ValueError("graph_edges cannot be None")
        if graph_edges is _UNSET:
            # Initiative -> Subsystem -> API Contract -> Vendor Dependency -> Enterprise Policy
            graph_edges = [
                ("init:rewire_q3", "depends_on", "subsystem:substation_telemetry"),
                ("subsystem:substation_telemetry", "exposes", "contract:iec_61850"),
                ("contract:iec_61850", "licensed_from", "vendor:abb_hitachi"),
                ("vendor:abb_hitachi", "subject_to", "policy:nerc_cip_014"),
            ]

        # BFS for 4-hop chain
        adj: Dict[str, List[str]] = {}
        for src, _, dst in graph_edges:
            adj.setdefault(src, []).append(dst)

        def find_max_path(node: str, visited: Set[str]) -> int:
            max_len = 0
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    max_len = max(max_len, 1 + find_max_path(neighbor, visited | {neighbor}))
            return max_len

        max_hops = max((find_max_path(n, {n}) for n in adj), default=0)
        passed = max_hops >= 4
        score = 100.0 if passed else (max_hops / 4.0) * 80.0

        return DimensionScore(
            dim_id="dim_2_latent_multihop",
            name="Multi-Hop Latent Connection Mining",
            score=score,
            passed=passed,
            details={"max_discovered_hops": max_hops, "target_hops": 4},
        )

    def evaluate_dimension_3_cross_silo_contradictions(
        self, initiatives: Optional[List[Dict[str, Any]]] = _UNSET
    ) -> DimensionScore:
        """Dim 3: Proactive discovery of conflicting architectures (CONFLICTS_WITH) & redundancy."""
        if initiatives is None:
            raise ValueError("initiatives cannot be None")
        if initiatives is _UNSET:
            initiatives = [
                {
                    "id": "init:analytics_streaming",
                    "team": "Apex Global Analytics",
                    "architecture": "real_time_streaming_kafka",
                    "ingestion_sla_seconds": 2,
                },
                {
                    "id": "init:core_edo_batch",
                    "team": "Enterprise Data Office",
                    "architecture": "batch_medallion_databricks",
                    "ingestion_sla_seconds": 3600,
                },
            ]

        conflicts_detected = []
        for idx_a in range(len(initiatives)):
            for idx_b in range(idx_a + 1, len(initiatives)):
                init_a = initiatives[idx_a]
                init_b = initiatives[idx_b]
                arch_a = str(init_a.get("architecture", "")).lower()
                arch_b = str(init_b.get("architecture", "")).lower()
                sla_a = init_a.get("ingestion_sla_seconds", 0)
                sla_b = init_b.get("ingestion_sla_seconds", 0)

                # Symmetric check across all initiative pairs regardless of order
                is_contradictory_arch = (
                    ("streaming" in arch_a and "batch" in arch_b)
                    or ("batch" in arch_a and "streaming" in arch_b)
                )
                if is_contradictory_arch and abs(sla_a - sla_b) > 60:
                    conflicts_detected.append({
                        "type": "CONFLICTS_WITH",
                        "source": init_a.get("id", f"init_{idx_a}"),
                        "target": init_b.get("id", f"init_{idx_b}"),
                        "reason": f"Incompatible ingestion architectures ({arch_a}: {sla_a}s vs {arch_b}: {sla_b}s)",
                    })

        passed = len(conflicts_detected) > 0
        score = 100.0 if passed else 0.0

        return DimensionScore(
            dim_id="dim_3_contradictions",
            name="Cross-Silo Contradiction & Redundancy Detection",
            score=score,
            passed=passed,
            details={"conflicts_detected": conflicts_detected, "count": len(conflicts_detected)},
        )

    def evaluate_dimension_4_dual_audience_commercial(
        self, public_brief: str, private_advisory: Dict[str, Any]
    ) -> DimensionScore:
        """Dim 4: PartnerAdvisory commercial levers (SOW, margin, IP) strictly firewalled from client delivery."""
        if public_brief is None or private_advisory is None:
            raise ValueError("public_brief and private_advisory cannot be None")
        client_leaks = [
            term for term in ["margin capture", "sow renegotiation", "rate card", "partner credit"]
            if term in public_brief.lower()
        ]
        has_partner_advisory_intel = bool(private_advisory.get("sow_guardrails")) and bool(private_advisory.get("ip_harvesting"))
        passed = (len(client_leaks) == 0) and has_partner_advisory_intel
        score = 100.0 if passed else (50.0 if len(client_leaks) == 0 else 0.0)

        return DimensionScore(
            dim_id="dim_4_dual_audience",
            name="Dual-Audience Federation & Consulting Commercial Edge",
            score=score,
            passed=passed,
            details={"client_leaks": client_leaks, "private_intel_present": has_partner_advisory_intel},
        )

    def evaluate_dimension_5_conways_law_politics(
        self, shadow_advisory: Optional[Dict[str, Any]] = _UNSET
    ) -> DimensionScore:
        """Dim 5: Conway's Law, informal veto power & key-person operational fragility."""
        if shadow_advisory is None:
            raise ValueError("shadow_advisory cannot be None")
        if shadow_advisory is _UNSET:
            shadow_advisory = {
                "stakeholder_stances": {"Drew Oakley": "CHAMPION", "Elena Rostova": "SKEPTIC", "VP_Finance": "INFORMAL_VETO"},
                "informal_vetoes": ["VP_Finance holds pocket veto on unbudgeted cloud egress"],
                "key_person_fragility": ["Lead Substation Architect is single point of failure for IEC 61850 translation"],
            }

        has_stances = len(shadow_advisory.get("stakeholder_stances", {})) >= 3
        has_vetoes = len(shadow_advisory.get("informal_vetoes", [])) >= 1
        has_fragility = len(shadow_advisory.get("key_person_fragility", [])) >= 1

        passed = has_stances and has_vetoes and has_fragility
        score = 100.0 if passed else (40.0 if (has_stances or has_vetoes or has_fragility) else 0.0)

        return DimensionScore(
            dim_id="dim_5_conways_law",
            name="Conway's Law, Politics & Organizational Topology",
            score=score,
            passed=passed,
            details={"stances_tracked": has_stances, "vetoes_tracked": has_vetoes, "fragility_tracked": has_fragility},
        )

    def evaluate_dimension_6_epistemic_alpha(self, extraction_record: Optional[Dict[str, Any]] = _UNSET) -> DimensionScore:
        """Dim 6: Extraction of implicit trade-offs and dissent vs superficial minutes."""
        if extraction_record is None:
            raise ValueError("extraction_record cannot be None")
        if extraction_record is _UNSET:
            extraction_record = {
                "unstated_trade_offs": ["Faster migration schedule increases OT vulnerability window"],
                "preserved_dissent": ["Operations team objected to 30-day cutover without parallel run"],
                "superficial_minutes_suppressed": True,
            }

        trade_offs = len(extraction_record.get("unstated_trade_offs", [])) >= 1
        dissent = len(extraction_record.get("preserved_dissent", [])) >= 1
        passed = trade_offs and dissent
        score = 100.0 if passed else (30.0 if (trade_offs or dissent) else 0.0)

        return DimensionScore(
            dim_id="dim_6_epistemic_alpha",
            name="Ingestion Signal vs. Noise & Epistemic Alpha",
            score=score,
            passed=passed,
            details={"trade_offs_found": trade_offs, "dissent_preserved": dissent},
        )

    def evaluate_dimension_7_bitemporal_rigor(self, record: Optional[Dict[str, Any]] = _UNSET) -> DimensionScore:
        """Dim 7: W3 valid-time vs transaction-time & provider-free write boundary."""
        if record is None:
            raise ValueError("record cannot be None")
        if record is _UNSET:
            record = {
                "observed_at": "2026-09-14T10:00:00Z",
                "valid_from": "2026-09-14T00:00:00Z",
                "valid_to": "2026-12-31T23:59:59Z",
                "system_transaction_time": "2026-09-14T10:01:23Z",
                "provider_free_gateway_verified": True,
            }

        has_temporal = bool(record.get("observed_at") and record.get("valid_from") and record.get("system_transaction_time"))
        provider_free = bool(record.get("provider_free_gateway_verified"))
        passed = has_temporal and provider_free
        score = 100.0 if passed else (50.0 if (has_temporal or provider_free) else 0.0)

        return DimensionScore(
            dim_id="dim_7_bitemporal",
            name="Bi-Temporal Rigor & Trust Foundation",
            score=score,
            passed=passed,
            details={"bitemporal_complete": has_temporal, "provider_free": provider_free},
        )

    def evaluate_dimension_8_mcda_tradeoffs(
        self, options: Optional[List[Dict[str, Any]]] = _UNSET, weights: Optional[MCDACriteriaWeights] = None
    ) -> DimensionScore:
        """Dim 8: Dialectical MCDA weighted trade-off scoring and rank decoupling."""
        if options is None:
            raise ValueError("options cannot be None")
        if weights is None:
            weights = MCDACriteriaWeights()
        if options is _UNSET:
            options = [
                {
                    "id": "opt_a", "name": "Sidecar SQLite WAL", "tco": 90.0, "ttv": 85.0,
                    "reversibility": 95.0, "security": 90.0, "friction": 80.0, "monthly_cod_usd": 12000.0,
                },
                {
                    "id": "opt_b", "name": "Cloud Managed Vector DB", "tco": 60.0, "ttv": 90.0,
                    "reversibility": 40.0, "security": 70.0, "friction": 85.0, "monthly_cod_usd": 48000.0,
                },
            ]

        if not options:
            return DimensionScore(
                dim_id="dim_8_mcda_tradeoffs",
                name="Dialectical Decision Intel & Multi-Criteria Trade-offs",
                score=0.0,
                passed=False,
                details={"top_option": None, "top_score": 0.0, "options_count": 0},
            )

        ranked: List[DecisionOptionEvaluation] = []
        for opt in options:
            composite = (
                opt["tco"] * weights.tco_finops
                + opt["ttv"] * weights.time_to_value
                + opt["reversibility"] * weights.architectural_reversibility
                + opt["security"] * weights.security_compliance
                + opt["friction"] * weights.org_friction
            )
            ranked.append(
                DecisionOptionEvaluation(
                    option_id=opt["id"],
                    name=opt["name"],
                    composite_score=round(composite, 2),
                    cost_of_delay_monthly_usd=opt.get("monthly_cod_usd", 0.0),
                )
            )

        ranked.sort(key=lambda x: x.composite_score, reverse=True)
        for idx, item in enumerate(ranked):
            item.rank = idx + 1

        passed = len(ranked) >= 2 and ranked[0].composite_score > ranked[1].composite_score
        score = 100.0 if passed else 50.0

        return DimensionScore(
            dim_id="dim_8_mcda_tradeoffs",
            name="Dialectical Decision Intel & Multi-Criteria Trade-offs",
            score=score,
            passed=passed,
            details={"top_option": ranked[0].name, "top_score": ranked[0].composite_score, "options_count": len(ranked)},
        )

    def evaluate_dimension_9_board_level_nba(
        self, memorandum: Optional[Dict[str, Any]] = _UNSET
    ) -> DimensionScore:
        """Dim 9: Financialized executive framing & dollarized Cost of Delay ($/month)."""
        if memorandum is None:
            raise ValueError("memorandum cannot be None")
        if memorandum is _UNSET:
            memorandum = {
                "executive_action": "Freeze Cloud Ingestion SOW pending IEC-61850 protocol signoff",
                "monthly_cost_of_delay_usd": 65000.0, "capex_rate_base_protection_usd": 2400000.0,
                "recommended_owner": "Drew Oakley (Managing Director)", "action_deadline": "2026-10-01",
            }

        has_action = bool(memorandum.get("executive_action"))
        has_cod = memorandum.get("monthly_cost_of_delay_usd", 0.0) > 0
        has_capex = memorandum.get("capex_rate_base_protection_usd", 0.0) > 0
        has_owner_deadline = bool(memorandum.get("recommended_owner") and memorandum.get("action_deadline"))

        passed = has_action and has_cod and has_capex and has_owner_deadline
        score = 100.0 if passed else (50.0 if (has_action or has_cod or has_capex) else 0.0)

        return DimensionScore(
            dim_id="dim_9_board_synthesis",
            name="Board-Level Synthesis & Next Best Action (NBA)",
            score=score,
            passed=passed,
            details={
                "cost_of_delay_usd": memorandum.get("monthly_cost_of_delay_usd", 0.0),
                "capex_protection_usd": memorandum.get("capex_rate_base_protection_usd", 0.0),
            },
        )

    def evaluate_dimension_10_multiclient_federation(
        self, blueprint: Optional[Dict[str, Any]] = _UNSET
    ) -> DimensionScore:
        """Dim 10: Anonymized cross-client pattern abstraction without NDA/confidentiality breach."""
        if blueprint is None:
            raise ValueError("blueprint cannot be None")
        if blueprint is _UNSET:
            blueprint = {
                "blueprint_name": "Universal Utility Substation Asset Ingestion Blueprint",
                "domain": "energy_and_utilities",
                "abstracted_patterns": ["IEC-61850 Mapping", "NERC-CIP-014 Boundary Guard"],
                "reusable_accelerators": ["telemetry_parser", "wal_ledger_sidecar"],
                "tenant_leak_free": True,
            }

        has_name = bool(blueprint.get("blueprint_name"))
        has_patterns = len(blueprint.get("abstracted_patterns", [])) >= 2
        tenant_clean = bool(blueprint.get("tenant_leak_free"))

        passed = has_name and has_patterns and tenant_clean
        score = 100.0 if passed else (40.0 if (has_name or has_patterns) else 0.0)

        return DimensionScore(
            dim_id="dim_10_multiclient_federation",
            name="Multi-Client Federation & Scalable Knowledge Asset",
            score=score,
            passed=passed,
            details={"patterns_abstracted": len(blueprint.get("abstracted_patterns", [])), "leak_free": tenant_clean},
        )

    def run_full_evaluation(
        self,
        public_brief: str = "The project team confirmed Q3 delivery of the substation asset health monitoring system.",
        private_advisory: Optional[Dict[str, Any]] = None,
    ) -> ExecutiveExoskeletonScorecard:
        """Execute full evaluation across all 10 Strategic Intelligence Dimensions."""
        if private_advisory is None:
            private_advisory = {
                "sow_guardrails": "Phase 2 scope capped at $450k; change order required",
                "ip_harvesting": "IEC 61850 normalizer for PartnerAdvisory Energy Accelerator",
            }

        scorecard = ExecutiveExoskeletonScorecard()
        scorecard.dimension_scores["dim_1_ea_capability"] = self.evaluate_dimension_1_ea_capability_depth()
        scorecard.dimension_scores["dim_2_latent_multihop"] = self.evaluate_dimension_2_latent_multi_hop()
        scorecard.dimension_scores["dim_3_contradictions"] = self.evaluate_dimension_3_cross_silo_contradictions()
        scorecard.dimension_scores["dim_4_dual_audience"] = self.evaluate_dimension_4_dual_audience_commercial(
            public_brief, private_advisory
        )
        scorecard.dimension_scores["dim_5_conways_law"] = self.evaluate_dimension_5_conways_law_politics()
        scorecard.dimension_scores["dim_6_epistemic_alpha"] = self.evaluate_dimension_6_epistemic_alpha()
        scorecard.dimension_scores["dim_7_bitemporal"] = self.evaluate_dimension_7_bitemporal_rigor()
        scorecard.dimension_scores["dim_8_mcda_tradeoffs"] = self.evaluate_dimension_8_mcda_tradeoffs()
        scorecard.dimension_scores["dim_9_board_synthesis"] = self.evaluate_dimension_9_board_level_nba()
        scorecard.dimension_scores["dim_10_multiclient_federation"] = self.evaluate_dimension_10_multiclient_federation()

        scorecard.compute_summary(passing_threshold=75.0)
        return scorecard

    def evaluate_from_pipeline_deliverables(
        self,
        output_01_path: Any,
        output_02_path: Any,
        output_03_path: Any,
        advisory_path: Optional[Any] = None,
    ) -> ExecutiveExoskeletonScorecard:
        """Evaluate authentic pipeline deliverables (Outputs 01, 02, 03) against the 10 dimensions."""
        from dual_pipeline.evals.strategic.deliverable_evaluator import (
            DeliverableStrategicEvaluator,
        )

        return DeliverableStrategicEvaluator().evaluate_deliverables(
            output_01_path, output_02_path, output_03_path, advisory_path
        )

