"""Executive Cognitive Exoskeleton: Golden CTO Analytics & Insight Engine.

Implements the 5 Golden CTO Queries and executive insight generation
mandated by the $1.2M Technology Executive benchmark.
Enforces provider-free analytical execution directly against the GraphDB.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.database import create_sqlite_conn, sqlite3


@dataclass(frozen=True)
class ExecutiveFinding:
    pattern: str
    headline: str
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'STRATEGIC_OPPORTUNITY'
    financial_impact_usd: float
    details: Dict[str, Any]
    strategic_recommendation: str


class ExecutiveAnalyticsEngine:
    """Analytical engine executing multi-hop strategic queries and executive synthesis."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    def _table_exists(self, table_name: str) -> bool:
        cursor = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        )
        return cursor.fetchone() is not None

    def _has_column(self, table_name: str, column_name: str) -> bool:
        if not self._table_exists(table_name):
            return False
        cursor = self.connection.execute(f'PRAGMA table_info("{table_name}")')
        columns = {row["name"] for row in cursor.fetchall()}
        return column_name in columns

    @classmethod
    def from_path(cls, db_path: Path | str) -> "ExecutiveAnalyticsEngine":
        path = Path(db_path).resolve()
        conn = create_sqlite_conn(str(path))
        return cls(conn)

    def detect_cross_silo_contradictions(
        self, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Query 1: Detect cross-silo architectural contradictions and incompatible standards."""
        if not self._table_exists("inter_initiative_edge") or not self._table_exists("node"):
            return []
        query = """
        SELECT 
            e.edge_id,
            e.tenant_id,
            i1.node_id AS initiative_alpha_id,
            i1.canonical_name AS initiative_alpha,
            COALESCE(o1.canonical_name, 'Unassigned') AS org_unit_alpha,
            e.relationship_type,
            i2.node_id AS initiative_beta_id,
            i2.canonical_name AS initiative_beta,
            COALESCE(o2.canonical_name, 'Unassigned') AS org_unit_beta,
            e.evidence_statement,
            e.estimated_financial_waste_usd,
            e.created_at
        FROM inter_initiative_edge e
        JOIN node i1 ON e.from_initiative_id = i1.node_id
        JOIN node i2 ON e.to_initiative_id = i2.node_id
        LEFT JOIN edge rel1 ON rel1.from_node_id = i1.node_id AND rel1.edge_type = 'OWNS'
        LEFT JOIN node o1 ON rel1.to_node_id = o1.node_id
        LEFT JOIN edge rel2 ON rel2.from_node_id = i2.node_id AND rel2.edge_type = 'OWNS'
        LEFT JOIN node o2 ON rel2.to_node_id = o2.node_id
        WHERE e.relationship_type = 'CONFLICTS_WITH'
        """
        params: List[Any] = []
        if tenant_id:
            query += " AND e.tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY e.estimated_financial_waste_usd DESC"

        cursor = self.connection.execute(query, params)
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            item["strategic_recommendation"] = (
                f"Establish an immediate architectural bridging contract between {item['org_unit_alpha']} "
                f"({item['initiative_alpha']}) and {item['org_unit_beta']} ({item['initiative_beta']}) "
                f"before integration to avert ${item.get('estimated_financial_waste_usd', 0):,.0f} in re-engineering waste."
            )
            results.append(item)
        return results

    def harvest_duplicative_platform_spend(
        self, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Query 2: Harvest multi-million dollar duplicative software and platform spend."""
        if (
            not self._table_exists("business_capability")
            or not self._table_exists("edge")
            or not self._table_exists("node")
        ):
            return []
        query = """
        SELECT 
            c.capability_id,
            c.tenant_id,
            c.canonical_name AS business_capability,
            c.business_domain,
            COUNT(DISTINCT n.node_id) AS separate_implementations,
            GROUP_CONCAT(DISTINCT n.canonical_name || ' [' || COALESCE(o.canonical_name, 'Org-Unassigned') || ']') AS competing_projects,
            SUM(COALESCE(v.annual_spend_usd, 0.0)) AS combined_vendor_spend_usd,
            SUM(COALESCE(e.estimated_financial_waste_usd, 0.0)) AS estimated_financial_waste_usd
        FROM business_capability c
        JOIN edge rel_cap ON rel_cap.to_node_id = c.capability_id AND rel_cap.edge_type = 'IMPLEMENTS_CAPABILITY'
        JOIN node n ON rel_cap.from_node_id = n.node_id
        LEFT JOIN edge rel_org ON rel_org.from_node_id = n.node_id AND rel_org.edge_type = 'OWNS'
        LEFT JOIN node o ON rel_org.to_node_id = o.node_id
        LEFT JOIN vendor_contract_dependency v ON v.governing_client_sponsor_id = n.node_id
        LEFT JOIN inter_initiative_edge e ON (e.from_initiative_id = n.node_id OR e.to_initiative_id = n.node_id) AND e.relationship_type = 'DUPLICATES'
        """
        params: List[Any] = []
        if tenant_id:
            query += " WHERE c.tenant_id = ?"
            params.append(tenant_id)
        query += """
        GROUP BY c.capability_id, c.tenant_id
        HAVING COUNT(DISTINCT n.node_id) > 1
        ORDER BY separate_implementations DESC, combined_vendor_spend_usd DESC
        """

        cursor = self.connection.execute(query, params)
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            total_waste = item.get("estimated_financial_waste_usd") or (
                item.get("combined_vendor_spend_usd", 0.0) * 0.4
            )
            item["estimated_financial_waste_usd"] = total_waste
            item["strategic_recommendation"] = (
                f"Consolidate {item['separate_implementations']} disparate implementations of '{item['business_capability']}' "
                f"into a shared Enterprise Platform Substrate, unlocking up to ${total_waste:,.0f} in license and delivery savings."
            )
            results.append(item)
        return results

    def trace_hidden_decision_blockers(
        self, tenant_id: Optional[str] = None, max_hops: int = 4
    ) -> List[Dict[str, Any]]:
        """Query 3: Trace blocked initiatives to unmade decisions and detractor stakeholders."""
        if (
            not self._table_exists("architectural_decision")
            or not self._table_exists("edge")
            or not self._table_exists("node")
        ):
            return []
        query = """
        SELECT 
            i.node_id AS initiative_id,
            d.tenant_id,
            i.canonical_name AS initiative_name,
            i.status AS initiative_status,
            d.adr_id,
            d.decision_title,
            d.status AS decision_status,
            s.stance AS stakeholder_stance,
            s.effective_influence_tier,
            s.friction_driver,
            p.canonical_name AS blocking_person_name
        FROM node i
        JOIN edge rel_dep ON i.node_id = rel_dep.from_node_id AND rel_dep.edge_type = 'DEPENDS_ON'
        JOIN architectural_decision d ON rel_dep.to_node_id = d.adr_id OR rel_dep.to_node_id = d.formal_approver_id
        LEFT JOIN stakeholder_political_stance s ON (d.adr_id = s.initiative_node_id OR i.node_id = s.initiative_node_id)
        LEFT JOIN node p ON s.person_node_id = p.node_id
        WHERE (d.status = 'PROPOSED' OR i.status = 'BLOCKED')
        """
        params: List[Any] = []
        if tenant_id:
            query += " AND d.tenant_id = ?"
            params.append(tenant_id)
        query += " ORDER BY i.canonical_name"

        cursor = self.connection.execute(query, params)
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            blocker = item.get("blocking_person_name") or "Unassigned Stakeholder"
            friction = item.get("friction_driver") or "Governance Ambiguity"
            item["strategic_recommendation"] = (
                f"Proactively resolve decision '{item['decision_title']}' by addressing {blocker}'s "
                f"concern ('{friction}') via a targeted FinOps/risk control framework rather than forcing top-down escalation."
            )
            results.append(item)
        return results

    def analyze_key_person_fragility(
        self, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Query 4: Identify key-person operational dependencies and single points of failure."""
        if not self._table_exists("node") or not self._table_exists("edge"):
            return []
        has_tech_debt = self._table_exists("technical_debt_item")
        td_col = (
            "SUM(CASE WHEN td.debt_id IS NOT NULL THEN 1 ELSE 0 END) AS associated_tech_debts, "
            "SUM(COALESCE(td.annual_carrying_cost_usd, 0.0)) AS total_annual_carrying_cost_usd"
            if has_tech_debt else "0 AS associated_tech_debts, 0.0 AS total_annual_carrying_cost_usd"
        )
        td_join = "LEFT JOIN technical_debt_item td ON td.affected_node_id = n.node_id" if has_tech_debt else ""
        order_by = "critical_systems_owned DESC, associated_tech_debts DESC" if has_tech_debt else "critical_systems_owned DESC"
        query = f"""
        SELECT 
            p.node_id AS person_node_id,
            p.canonical_name AS key_person,
            COALESCE(p.display_name, p.canonical_name) AS display_title,
            COUNT(DISTINCT n.node_id) AS critical_systems_owned,
            GROUP_CONCAT(DISTINCT n.canonical_name) AS systems_list,
            {td_col}
        FROM node p
        JOIN edge e ON e.from_node_id = p.node_id AND e.edge_type IN ('LEADS', 'OWNS', 'SINGLE_EXPERT')
        JOIN node n ON e.to_node_id = n.node_id
        {td_join}
        WHERE p.node_type = 'person'
        GROUP BY p.node_id
        HAVING COUNT(DISTINCT n.node_id) >= 2
        ORDER BY {order_by}
        """
        cursor = self.connection.execute(query)
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            item["strategic_recommendation"] = (
                f"Execute immediate knowledge externalization and shadow co-delivery for {item['key_person']} "
                f"across {item['critical_systems_owned']} core systems to eliminate severe key-person outage vulnerability."
            )
            results.append(item)
        return results

    def identify_reusable_ip_assets(
        self, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Query 5: Identify generalizable client capabilities for PartnerAdvisory IP acceleration."""
        if not self._table_exists("assertion") or not self._table_exists("node"):
            return []
        has_scope = self._has_column("assertion", "applicability_scope")
        has_role = self._has_column("assertion", "knowledge_role")
        if has_scope and has_role:
            where = "WHERE a.applicability_scope IN ('generalizable', 'client_specific') AND a.knowledge_role IN ('capability', 'external_pattern', 'design_principle', 'target_outcome')"
            summary_expr = "n.description AS technical_summary"
            role_expr = "MAX(CASE WHEN a.applicability_scope = 'generalizable' THEN 1 ELSE 0 END) AS is_generalizable, GROUP_CONCAT(DISTINCT a.knowledge_role) AS knowledge_roles_present"
        else:
            where = "WHERE n.node_type IN ('capability', 'initiative', 'system')"
            summary_expr = "COALESCE(n.description, 'Verified architectural node') AS technical_summary"
            role_expr = "1 AS is_generalizable, 'verified_knowledge' AS knowledge_roles_present"
        query = f"""
        SELECT 
            a.subject_node_id AS component_id,
            n.canonical_name AS component_name,
            {summary_expr},
            COUNT(DISTINCT a.assertion_id) AS verified_assertions,
            {role_expr}
        FROM assertion a
        JOIN node n ON a.subject_node_id = n.node_id
        {where}
        GROUP BY a.subject_node_id
        HAVING COUNT(DISTINCT a.assertion_id) >= 2
        ORDER BY verified_assertions DESC
        """
        cursor = self.connection.execute(query)
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            item["strategic_recommendation"] = (
                f"Abstract '{item['component_name']}' into an anonymized PartnerAdvisory Industry Solution Blueprint, "
                f"leveraging its {item['verified_assertions']} verified assertions to drive high-margin client advisory pull-through."
            )
            results.append(item)
        return results

    def run_all_executive_analyses(
        self, tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute all 5 Golden CTO Queries and return a structured executive intelligence packet."""
        contradictions = self.detect_cross_silo_contradictions(tenant_id)
        duplications = self.harvest_duplicative_platform_spend(tenant_id)
        blockers = self.trace_hidden_decision_blockers(tenant_id)
        fragility = self.analyze_key_person_fragility(tenant_id)
        reusable_ip = self.identify_reusable_ip_assets(tenant_id)

        total_waste_usd = sum(
            c.get("estimated_financial_waste_usd", 0.0) for c in contradictions
        ) + sum(d.get("estimated_financial_waste_usd", 0.0) for d in duplications)

        return {
            "tenant_id": tenant_id or "all_tenants",
            "summary_metrics": {
                "total_identified_financial_waste_usd": total_waste_usd,
                "architectural_contradictions_count": len(contradictions),
                "duplicative_capabilities_count": len(duplications),
                "hidden_decision_blockers_count": len(blockers),
                "key_person_vulnerabilities_count": len(fragility),
                "reusable_ip_accelerators_count": len(reusable_ip),
            },
            "findings": {
                "cross_silo_contradictions": contradictions,
                "duplicative_spend": duplications,
                "hidden_decision_blockers": blockers,
                "key_person_fragility": fragility,
                "reusable_ip_assets": reusable_ip,
            },
        }

    def format_executive_brief_markdown(
        self, analysis_packet: Dict[str, Any]
    ) -> str:
        """Format an executive intelligence packet into a Board/C-Suite decision memo."""
        metrics = analysis_packet["summary_metrics"]
        findings = analysis_packet["findings"]

        lines = [
            "# Executive Strategic Intelligence Briefing: Cognitive Exoskeleton Synthesis",
            f"**Tenant Scope:** `{analysis_packet['tenant_id']}` | **Financial Waste Identified:** `${metrics['total_identified_financial_waste_usd']:,.0f}`",
            "",
            "## 1. Executive Summary & Value Realization",
            f"- **Architectural Contradictions:** {metrics['architectural_contradictions_count']} cross-silo conflicts requiring structural alignment.",
            f"- **Duplicative Capability Spend:** {metrics['duplicative_capabilities_count']} redundant enterprise platforms ripe for consolidation.",
            f"- **Critical Decision Blockers:** {metrics['hidden_decision_blockers_count']} initiatives stalled by pending decisions and stakeholder friction.",
            f"- **Key-Person Operational SPOFs:** {metrics['key_person_vulnerabilities_count']} leaders with concentrated system dependencies.",
            f"- **PartnerAdvisory IP Harvesting Opportunities:** {metrics['reusable_ip_accelerators_count']} reusable solution blueprints.",
            "",
            "---",
            "## 2. Priority Cross-Silo Contradictions",
        ]

        if findings["cross_silo_contradictions"]:
            for item in findings["cross_silo_contradictions"]:
                lines.extend([
                    f"### ⚠️ Collision: {item['initiative_alpha']} ({item['org_unit_alpha']}) vs. {item['initiative_beta']} ({item['org_unit_beta']})",
                    f"- **Estimated Financial Waste:** `${item.get('estimated_financial_waste_usd', 0):,.0f}`",
                    f"- **Evidence Statement:** {item['evidence_statement']}",
                    f"- **Strategic Intervention:** {item['strategic_recommendation']}",
                    "",
                ])
        else:
            lines.append("*(No active cross-silo contradictions detected)*\n")

        lines.extend([
            "---",
            "## 3. Duplicative Software & Platform Spend",
        ])

        if findings["duplicative_spend"]:
            for item in findings["duplicative_spend"]:
                lines.extend([
                    f"### 🔄 Capability Redundancy: {item['business_capability']} ({item['business_domain']})",
                    f"- **Competing Projects:** {item['competing_projects']}",
                    f"- **Combined Vendor Spend:** `${item.get('combined_vendor_spend_usd', 0):,.0f}` | **Recoverable Waste:** `${item.get('estimated_financial_waste_usd', 0):,.0f}`",
                    f"- **Strategic Intervention:** {item['strategic_recommendation']}",
                    "",
                ])
        else:
            lines.append("*(No multi-team capability duplications detected)*\n")

        lines.extend([
            "---",
            "## 4. Hidden Decision Blockers & Stakeholder Friction",
        ])

        if findings["hidden_decision_blockers"]:
            for item in findings["hidden_decision_blockers"]:
                lines.extend([
                    f"### 🛑 Blocker: {item['initiative_name']} ➔ Decision '{item['decision_title']}'",
                    f"- **Decision Status:** `{item['decision_status']}` | **Initiative Status:** `{item['initiative_status']}`",
                    f"- **Blocking Stakeholder:** {item.get('blocking_person_name', 'Unknown')} ({item.get('stakeholder_stance', 'Unstated')})",
                    f"- **Underlying Friction:** {item.get('friction_driver', 'None stated')}",
                    f"- **Tactical Unblocking Action:** {item['strategic_recommendation']}",
                    "",
                ])
        else:
            lines.append("*(No hidden decision blockers detected)*\n")

        return "\n".join(lines)
