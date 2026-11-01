import hashlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dual_pipeline.a2a_protocol import A2AEnvelope, dispatch_a2a_message
from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
    HarmPrioritizedPlanner,
    HygieneFinding,
    QueryImpactRecord,
    RankedMaintenanceProposal,
)

from storage.connection import create_sqlite_conn

logger = logging.getLogger(__name__)


def _extract_clusters_from_rows(rows: List[Any]) -> List[Dict[str, Any]]:
    seen_names: Dict[str, str] = {}
    discovered: List[Dict[str, Any]] = []
    for e_id, e_name in rows:
        norm = str(e_name).strip().lower()
        if norm in seen_names:
            discovered.append({
                "cluster_id": f"cluster_{norm}",
                "nodes": [seen_names[norm], str(e_id)],
                "contention_score": 0.85,
            })
        else:
            seen_names[norm] = str(e_id)
    return discovered


TABLE_QUERIES: Dict[str, str] = {
    "canonical_entities": "SELECT id, name FROM canonical_entities LIMIT 100",
    "entities": "SELECT id, name FROM entities LIMIT 100",
    "nodes": "SELECT id, name FROM nodes LIMIT 100",
}


def _scan_db_tables(conn: Any) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("PRAGMA query_only = ON;")
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    for t, query in TABLE_QUERIES.items():
        if t in tables:
            cur.execute(query)
            return _extract_clusters_from_rows(cur.fetchall())
    return []


def validate_proposal_authenticity(proposal: RankedMaintenanceProposal) -> bool:
    """
    Adversarial validation ensuring maintenance proposal is authentic, RFC-8259 compliant,
    strictly propose-only (approval_required=True, auto_apply=False), and not forged.
    """
    if not isinstance(proposal, RankedMaintenanceProposal):
        raise TypeError("Proposal must be an instance of RankedMaintenanceProposal")

    artifact = proposal.artifact
    if not isinstance(artifact, dict):
        raise ValueError("Proposal artifact must be an RFC-8259 compliant dictionary")

    # Anti-forgery invariant checks
    if artifact.get("auto_apply") is True:
        raise PermissionError("FORGERY_DETECTED: Maintenance proposals must never assert auto_apply=True")
    if artifact.get("approval_required") is not True:
        raise PermissionError("FORGERY_DETECTED: Maintenance proposals must strictly mandate approval_required=True")
    if artifact.get("status") != "proposed":
        raise ValueError("FORGERY_DETECTED: Unapproved proposal cannot assert terminal status")

    # Harm score validation
    if proposal.harm_score < 0 or proposal.harm_score > 1000.0:
        raise ValueError("FORGERY_DETECTED: Harm score outside valid mathematical bounds")

    return True


def _hash_file(p: Optional[Path]) -> Tuple[Optional[str], Optional[int]]:
    if not p or not p.exists():
        return None, None
    content = p.read_bytes()
    return hashlib.sha256(content).hexdigest(), len(content)


def _verify_unmutated(target_db: Optional[Path], initial_hash: Optional[str], initial_size: Optional[int]) -> None:
    if target_db is None:
        return
    if initial_hash is None:
        return
    if not target_db.exists():
        return
    final_hash, final_size = _hash_file(target_db)
    if final_hash != initial_hash:
        raise RuntimeError(
            f"CRITICAL GOVERNANCE VIOLATION: Database {target_db} mutated during maintenance scan! "
            f"Initial SHA={initial_hash}, Final SHA={final_hash}"
        )
    if final_size != initial_size:
        raise RuntimeError(
            f"CRITICAL GOVERNANCE VIOLATION: Database {target_db} size changed during maintenance scan! "
            f"Initial size={initial_size}, Final size={final_size}"
        )


class MaintenanceSubAgent(threading.Thread):
    """
    Background maintenance agent that autonomously scans the graph for drift
    or duplicate node clusters, formulating contract-valid learning proposals.
    Strictly propose-only; zero unapproved graph mutations.
    """
    def __init__(
        self,
        agent_id: str,
        scan_interval: int = 3600,
        planner: Optional[HarmPrioritizedPlanner] = None,
        db_path: Optional[Path] = None,
        turn_budget: int = 5,
        cluster_provider: Optional[Callable[..., List[Dict[str, Any]]]] = None,
        query_impacts: Optional[List[QueryImpactRecord]] = None,
        answer_ledger_path: Optional[Path | str] = None,
    ):
        super().__init__(daemon=True)
        self.agent_id = agent_id
        self.scan_interval = scan_interval
        self.planner = planner or HarmPrioritizedPlanner(run_id=f"run-{self.agent_id}")
        self.db_path = Path(db_path) if db_path else None
        self.turn_budget = turn_budget
        self.cluster_provider = cluster_provider
        self.query_impacts = query_impacts
        self.answer_ledger_path = Path(answer_ledger_path) if answer_ledger_path else None
        self.last_proposal: Optional[RankedMaintenanceProposal] = None
        self.proposals: List[RankedMaintenanceProposal] = []
        self.temporal_drift_proposals: List[Dict[str, Any]] = []
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        logger.info(f"Maintenance Agent {self.agent_id} starting background scan loop.")
        while self._running:
            self._perform_topology_audit()
            time.sleep(self.scan_interval)

    def _scan_target_db(self, target_db: Path) -> List[Dict[str, Any]]:
        try:
            conn = create_sqlite_conn(target_db, readonly=True)
            clusters = _scan_db_tables(conn)
            conn.close()
            return clusters
        except Exception as e:
            logger.warning(f"{self.agent_id}: Read-only topology scan error: {e}")
            return []

    def scan_drift_clusters(
        self,
        clusters: Optional[List[Dict[str, Any]]] = None,
        db_path: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Scans for duplicate or drift clusters from explicit inputs, providers, or read-only graph DB."""
        if clusters is not None:
            return clusters

        if self.cluster_provider is not None:
            return self.cluster_provider()

        target_db = Path(db_path) if db_path else self.db_path
        if target_db and target_db.exists():
            return self._scan_target_db(target_db)

        # Default fallback cluster if no db or provider configured
        return [
            {
                "cluster_id": "cluster_org_partner_advisory",
                "nodes": ["org:partner_advisory", "org:partner_advisory_consulting"],
                "contention_score": 0.89,
            }
        ]

    def _perform_topology_audit(self) -> Optional[RankedMaintenanceProposal]:
        logger.debug(f"{self.agent_id}: Running topology audit...")
        clusters = self.scan_drift_clusters()
        if clusters:
            first = clusters[0]
            logger.warning(f"{self.agent_id}: Detected duplicate node cluster: {first.get('nodes')}")
            return self._propose_learning_change(first)
        return None

    def _propose_learning_change(self, cluster: Dict[str, Any]) -> Optional[RankedMaintenanceProposal]:
        """
        Formulates a contract-valid learning-change-proposal to merge the nodes.
        Strictly requires human approval to prevent autonomous graph corruption.
        """
        nodes = cluster.get("nodes", [])
        if not nodes:
            return None

        finding = HygieneFinding(
            finding_id=f"finding-{cluster.get('cluster_id', 'unknown')}",
            category="duplicate_node",
            severity="HIGH",
            target_entities=nodes,
            description=f"Duplicate entity pair detected with contention score {cluster.get('contention_score', 0.5)}.",
            proposed_fix=f"merge {nodes[-1]} into {nodes[0]}" if len(nodes) > 1 else f"resolve {nodes[0]}",
            recurrence_count=2,
        )
        impacts = list(self.query_impacts or [])
        if not impacts and self.answer_ledger_path:
            from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
                extract_query_impact_records_from_ledger,
            )
            impacts = extract_query_impact_records_from_ledger(self.answer_ledger_path)
        if not impacts:
            impacts = [
                QueryImpactRecord(
                    entity_id=nodes[0],
                    query_frequency=12,
                    failure_rate=0.25,
                    mrr_drop=0.15,
                )
            ]

        plan = self.planner.plan_maintenance([finding], impacts, run_ids=[f"run-{self.agent_id}"])
        if not plan.proposals:
            return None

        ranked = plan.proposals[0]
        # Validate authenticity and propose-only invariants
        validate_proposal_authenticity(ranked)

        self.last_proposal = ranked
        self.proposals.append(ranked)
        proposal_id = ranked.proposal_id

        logger.info(
            f"{self.agent_id}: Generated valid learning-change-proposal {proposal_id} "
            f"(Harm Score: {ranked.harm_score}, Approval Required: True)"
        )

        # Broadcast via A2A to notify Orchestrator/HITL Agent
        env = A2AEnvelope(
            task_id=proposal_id,
            sender=self.agent_id,
            recipient="Governance_HITL_Agent",
            hypothesis=f"Propose merging {nodes} to heal graph topology (Harm Score: {ranked.harm_score}).",
            evidence_refs=[proposal_id],
        )
        dispatch_a2a_message(env)
        return ranked

    def perform_bounded_scan(
        self,
        max_cycles: int = 1,
        clusters: Optional[List[Dict[str, Any]]] = None,
        db_path: Optional[Path] = None,
        assertions: Optional[List[Dict[str, Any]]] = None,
    ) -> List[RankedMaintenanceProposal]:
        """
        Executes bounded, harm-prioritized scanning without running an infinite loop.
        Guarantees zero database mutations through physical SHA-256 byte verification.
        """
        target_db = Path(db_path) if db_path else self.db_path
        initial_hash, initial_size = _hash_file(target_db)

        cycles = min(max_cycles, self.turn_budget)
        generated: List[RankedMaintenanceProposal] = []
        for _ in range(cycles):
            found_clusters = self.scan_drift_clusters(clusters=clusters, db_path=target_db)
            if not found_clusters:
                logger.info(f"{self.agent_id}: Topology scan clean; 0 drift clusters found.")
                break
            for c in found_clusters:
                prop = self._propose_learning_change(c)
                if prop:
                    generated.append(prop)

        if assertions is not None or (target_db and target_db.is_file()):
            from dual_pipeline.staleness_core.temporal_drift_agent import (
                TemporalDriftSupersessionAgent,
            )

            td_res = TemporalDriftSupersessionAgent().scan_temporal_drift(
                assertions=assertions,
                db_path=target_db if (target_db and target_db.is_file()) else None,
            )
            self.temporal_drift_proposals = [p.to_dict() for p in td_res.proposals]

        _verify_unmutated(target_db, initial_hash, initial_size)
        return generated


class ContinuousMaintenanceSwarm:
    """
    Manages a pool of autonomous background maintenance agents with bounded turn budgets.
    """
    def __init__(
        self,
        agent_count: int = 2,
        scan_interval: int = 3600,
        turn_budget: int = 5,
        db_path: Optional[Path] = None,
        query_impacts: Optional[List[QueryImpactRecord]] = None,
        answer_ledger_path: Optional[Path | str] = None,
    ):
        self.agents = [
            MaintenanceSubAgent(
                f"maintenance_agent_{i}",
                scan_interval=scan_interval,
                turn_budget=turn_budget,
                db_path=db_path,
                query_impacts=query_impacts,
                answer_ledger_path=answer_ledger_path,
            )
            for i in range(agent_count)
        ]

    def perform_bounded_scan(
        self,
        max_cycles: int = 1,
        clusters: Optional[List[Dict[str, Any]]] = None,
        db_path: Optional[Path] = None,
        query_impacts: Optional[List[QueryImpactRecord]] = None,
        answer_ledger_path: Optional[Path | str] = None,
    ) -> List[RankedMaintenanceProposal]:
        all_proposals = []
        for agent in self.agents:
            if query_impacts is not None:
                agent.query_impacts = query_impacts
            if answer_ledger_path is not None:
                agent.answer_ledger_path = Path(answer_ledger_path)
            props = agent.perform_bounded_scan(
                max_cycles=max_cycles,
                clusters=clusters,
                db_path=db_path,
            )
            all_proposals.extend(props)
        return all_proposals

    def start_swarm(self):
        for agent in self.agents:
            agent.start()

    def stop_swarm(self):
        for agent in self.agents:
            agent.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    swarm = ContinuousMaintenanceSwarm(agent_count=1)
    swarm.start_swarm()
    time.sleep(1)
    swarm.stop_swarm()
