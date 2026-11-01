"""High-salience, real-time in-memory graph retriever for Live Executive Copilot.
Maintains token indexes and attendee salience mapping for entity disambiguation,
graph traversal, and structured assertion extraction under Rule C-19 and engagement scoping.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from .copilot_connection_policy import (
    ScopeBinding,
    calculate_engagement_affinity,
    discover_topologically_inherited_engagements,
    is_assertion_in_scope,
    is_enterprise_policy,
    is_node_in_scope,
    resolve_permitted_engagements,
)
from .session_identity import match_engagement, normalize_engagement_id
from .copilot_neighborhood import CopilotNeighborhoodStore, compute_neighborhood_chains
from .copilot_roster_bridge import expand_role_query_tokens, rank_nodes_by_tokens, seed_attendee_identities
from .copilot_predicates import (
    ACTION_PREDICATES,
    PRIORITY_PREDICATES,
    PRIORITY_RELATIONSHIPS,
    QUESTION_PREDICATES,
    normalize_copilot_predicate,
)

from .prefetch_query_builder import (
    DOMAIN_ACRONYM_MAP,
    _PROJECT_SYNONYMS,
    build_prefetch_query_specs,
    normalize_project_terms,
)
from .session_context_packet import (
    CopilotPrefetchBridge,
    SessionContextPacket,
    _find_attendee_seeds,
)
from .copilot_retrieval_fusion import (
    compute_copilot_rrf_weights, cross_encoder_rerank, expand_1hop_graph_neighbors,
    expand_multi_queries, maximal_marginal_relevance, reciprocal_rank_fusion,
)
from .copilot_retriever_ranking import (
    collect_multi_query_rankings,
    compute_temporal_decay,
    determine_assertion_provenance,
    extract_query_tokens_and_seeds,
    parse_assertion_epoch,
    resolve_assertion_epoch,
    retrieve_dense_ranking,
    retrieve_lexical_ranking,
    score_provenance_assertion,
    tokenize_text,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_retriever")


__all__ = [
    "CopilotGraphRetriever",
    "CopilotPrefetchBridge",
    "SessionContextPacket",
    "CopilotNeighborhoodStore",
    "compute_neighborhood_chains",
    "PRIORITY_RELATIONSHIPS",
    "PRIORITY_PREDICATES",
    "QUESTION_PREDICATES",
    "ACTION_PREDICATES",
    "build_prefetch_query_specs",
    "normalize_project_terms",
    "has_c19_marker",
]

# Rule C-19 Career Leverage & Internal Advisory Firewall SSOT
from .c19_firewall import (
    C19_FORBIDDEN_MARKERS,
    has_c19_marker,
    is_c19_quarantined_assertion,
    is_c19_quarantined_node,
)


def _tokenize_text(text: str) -> Set[str]:
    """Extracts normalized alphanumeric query tokens excluding stopwords."""
    return tokenize_text(text)


def _extract_query_tokens(query: Any) -> Set[str]:
    """Extracts alphanumeric tokens and acronym/topic/role expansions from a query string or context model."""
    return extract_query_tokens_and_seeds(query, set())[0]


_determine_assertion_provenance = determine_assertion_provenance


class CopilotGraphRetriever:
    """
    High-performance in-memory graph retriever tailored for live conversational meetings.
    Builds inverted token indexes with IDF weights for candidate lookup and scores matches using
    token specificity, attendee affinity, and bidirectional relational salience.
    """

    def __init__(
        self,
        topology: Optional[Any] = None,
        attendees: Optional[List[Dict[str, str]]] = None,
        graph_sha256: Optional[str] = None,
        generation: Optional[Any] = None,
        neighborhood_store: Optional[CopilotNeighborhoodStore] = None,
        engagement_id: Optional[str] = None,
        session_id: Optional[str] = None,
        permitted_engagements: Optional[Set[str]] = None,
        sidecar_path: Optional[Union[str, Path]] = None,
        enable_cross_encoder: bool = False,
        enable_1hop_graph_expansion: bool = True,
        cross_encoder_fn: Optional[Callable[[str, str], float]] = None,
        embed_fn: Optional[Callable[[str], Any]] = None,
        runtime_root: Optional[Union[str, Path]] = None,
    ) -> None:
        self._topology = topology
        self._attendees = attendees or []
        self.graph_sha256 = graph_sha256
        self.generation = generation
        self.neighborhood_store = neighborhood_store
        self.engagement_id = engagement_id
        self.session_id = session_id
        self.runtime_root = Path(runtime_root).resolve() if runtime_root else None
        self.sidecar_path = Path(sidecar_path).resolve() if sidecar_path else None
        self.enable_cross_encoder = enable_cross_encoder
        self.enable_1hop_graph_expansion = enable_1hop_graph_expansion
        self.cross_encoder_fn = cross_encoder_fn
        self.embed_fn = embed_fn
        self._dense_matrix: Optional[Any] = None
        self._dense_record_ids: List[str] = []
        self._record_id_to_idx: Dict[str, int] = {}
        self._dense_record_to_assertion_id: Dict[str, str] = {}

        self.permitted_engagements = set(permitted_engagements) if permitted_engagements else set()
        self.topological_engagements: Set[str] = (
            discover_topologically_inherited_engagements(self._topology, self.engagement_id)
            if (self._topology and self.engagement_id)
            else set()
        )
        if self.engagement_id or self.topological_engagements:
            base = (
                set(self.permitted_engagements)
                if self.permitted_engagements
                else ({self.engagement_id} if self.engagement_id else set())
            )
            self.permitted_engagements = base | self.topological_engagements
        self._scope_binding = ScopeBinding(
            session_id=session_id or "", engagement_id=engagement_id or "", generation=generation or 0
        )
        self._inverted_index: Dict[str, Set[str]] = defaultdict(set)
        self._token_idf: Dict[str, float] = {}
        self._attendee_node_ids: Set[str] = set()
        self._attendee_tokens: Set[str] = set()
        self._salient_assertions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._allowed_nodes: Set[str] = set()
        self._claim_inverted_index: Dict[str, List[str]] = defaultdict(list)
        self._indexed_assertion_records: Dict[str, Dict[str, Any]] = {}
        self._node_latest_epoch, self._reference_epoch, self._scope_filtered_count = {}, 0.0, 0

        if self._topology is not None:
            self._build_indexes()
        self._init_sidecar_vectors()

    def _init_sidecar_vectors(self) -> None:
        if self.sidecar_path is None:
            self.sidecar_path = self._discover_sidecar_path()
        if self.sidecar_path and self.sidecar_path.is_file():
            self._load_dense_vectors(self.sidecar_path)

    def _discover_sidecar_path(self) -> Optional[Path]:
        try:
            from dual_pipeline.runtime_root import resolve_active_pair, resolve_authoritative_runtime_root
            nee_env = os.environ.get("NEE_RUNTIME_ROOT")
            r_root = self.runtime_root or (Path(nee_env).resolve() if nee_env else resolve_authoritative_runtime_root())
            pair_data, actual_pair_path = resolve_active_pair(r_root, enforce_integrity=False)
            cand = (actual_pair_path.parent / str(pair_data.get("sidecar_path") or "")).resolve()
            if cand.is_file():
                return cand
        except Exception:
            pass
        return None

    def _load_dense_vectors(self, sidecar_path: Path) -> None:
        try:
            import numpy as np
            from storage.connection import create_sqlite_conn
            con = create_sqlite_conn(sidecar_path, readonly=True)
            cur = con.cursor()
            rows = cur.execute("SELECT record_id, vector FROM dense_vector").fetchall()

            # Map record_id -> assertion_id from retrieval_record table if present
            try:
                rr_rows = cur.execute("SELECT record_id, artifact_json FROM retrieval_record").fetchall()
                for rid, art_raw in rr_rows:
                    try:
                        art = json.loads(art_raw)
                        aid = (
                            art.get("assertion_id")
                            or art.get("id")
                            or (art.get("extensions") or {}).get("com.partner_advisory.nee.graph-object-id")
                        )
                        if aid:
                            self._dense_record_to_assertion_id[rid] = aid
                    except Exception:
                        pass
            except Exception:
                pass

            con.close()
            if not rows:
                return
            self._dense_record_ids = [r[0] for r in rows]
            self._record_id_to_idx = {rid: idx for idx, rid in enumerate(self._dense_record_ids)}
            for idx, rid in enumerate(self._dense_record_ids):
                if rid not in self._dense_record_to_assertion_id:
                    self._dense_record_to_assertion_id[rid] = rid
                aid = self._dense_record_to_assertion_id[rid]
                if aid not in self._record_id_to_idx:
                    self._record_id_to_idx[aid] = idx

            raw = np.array([np.frombuffer(r[1], dtype=np.float32) for r in rows], dtype=np.float32)
            norms = np.linalg.norm(raw, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._dense_matrix = raw / norms
            logger.info("Cached %d dense vectors from %s", len(rows), sidecar_path)
        except Exception as e:

            logger.warning("Could not load dense vectors from %s: %s", sidecar_path, e)

    def score_dense_assertions(self, query_vector: Any, top_k: int = 50) -> List[Tuple[str, float]]:
        """Computes vectorized single-pass BLAS cosine similarities across all active dense vectors."""
        if self._dense_matrix is None and self.sidecar_path and self.sidecar_path.is_file():
            self._load_dense_vectors(self.sidecar_path)
        if self._dense_matrix is None or not self._dense_record_ids:
            return []
        try:
            import numpy as np
            q = np.asarray(query_vector, dtype=np.float32)
            q_norm = np.linalg.norm(q)
            if q_norm > 0:
                q = q / q_norm
            sims = np.dot(self._dense_matrix, q)
            k = min(top_k, len(sims))
            if k <= 0:
                return []
            top_indices = np.argpartition(-sims, k - 1)[:k]
            top_indices = top_indices[np.argsort(-sims[top_indices])]
            return [(self._dense_record_ids[i], float(sims[i])) for i in top_indices]
        except Exception as e:
            logger.warning("Dense vector scoring failed: %s", e)
            return []

    def update_topology(self, topology: Any) -> None:
        """Hot-reloads the active graph topology generation."""
        self._topology = topology
        self._scope_filtered_count = 0
        self.topological_engagements = (
            discover_topologically_inherited_engagements(self._topology, self.engagement_id)
            if (self._topology and self.engagement_id)
            else set()
        )
        if self.engagement_id or self.topological_engagements:
            base = (
                set(self.permitted_engagements)
                if self.permitted_engagements
                else ({self.engagement_id} if self.engagement_id else set())
            )
            self.permitted_engagements = base | self.topological_engagements
        for s in (
            self._inverted_index,
            self._claim_inverted_index,
            self._indexed_assertion_records,
            self._token_idf,
            self._attendee_node_ids,
            self._attendee_tokens,
            self._salient_assertions,
            self._allowed_nodes,
            self._node_latest_epoch,
        ):
            s.clear()
        self._reference_epoch = 0.0
        self._build_indexes()

    def _is_c19_quarantined(self, node_id: str, node: Any) -> bool:
        """Verifies whether a node or its properties violate the Rule C-19 Career Firewall."""
        return is_c19_quarantined_node(node_id, node)

    def _index_nodes(self) -> None:
        attendee_names: Set[str] = set()
        for att in self._attendees:
            if isinstance(att, dict):
                name, role = str(att.get("name") or ""), str(att.get("role") or "")
                org = str(att.get("org") or att.get("organization") or "")
                self._attendee_tokens.update(_tokenize_text(f"{name} {role} {org}"))
                if name.strip():
                    attendee_names.add(name.strip().lower())
            elif isinstance(att, str) and att.strip():
                self._attendee_tokens.update(_tokenize_text(att))
                attendee_names.add(att.strip().lower())
        self._allowed_nodes = set()
        for node_id, node in self._topology.nodes.items():
            if not isinstance(node, dict) or self._is_c19_quarantined(node_id, node):
                continue
            c_name, d_name = str(node.get("canonical_name") or ""), str(node.get("display_name") or "")
            node_type = str(node.get("node_type") or "")
            aliases = " ".join(node.get("aliases", [])) if isinstance(node.get("aliases"), list) else ""
            tokens = _tokenize_text(f"{node_id} {c_name} {d_name} {node_type} {aliases}")

            is_att = (
                bool(tokens & self._attendee_tokens)
                or (c_name.strip().lower() in attendee_names)
                or (d_name.strip().lower() in attendee_names)
            )

            if self.engagement_id and not is_node_in_scope(
                node_id, node, self.engagement_id, self.permitted_engagements, is_attendee=is_att
            ):
                continue

            self._allowed_nodes.add(node_id)

            for token in tokens:
                self._inverted_index[token].add(node_id)
            if is_att or (tokens & self._attendee_tokens):
                self._attendee_node_ids.add(node_id)
        seed_attendee_identities(
            self._attendees, self._topology.nodes, self._inverted_index,
            self._attendee_tokens, self._attendee_node_ids, self._allowed_nodes,
            self._salient_assertions, getattr(self._topology, "assertions", None),
        )

        total_nodes = max(1, len(self._topology.nodes))
        for token, matching_nodes in self._inverted_index.items():
            df = len(matching_nodes)
            self._token_idf[token] = math.log((total_nodes + 1.0) / (df + 1.0)) + 1.0

    @staticmethod
    def _is_assertion_status_eligible(assertion: Dict[str, Any], claim: str) -> bool:
        if re.match(r"^(?:-\s*)?\*\*(?:Apex Global\s+Energy|PartnerAdvisory|Attendees|Participants|Roster)\b", claim.strip(), re.IGNORECASE):
            return False
        c_status = str(assertion.get("canonical_status") or assertion.get("status") or "").strip().lower()
        if c_status in ("historical_candidate", "proposed", "retracted", "superseded", "rejected"):
            return False
        ret_elig, can_elig = assertion.get("retrieval_eligible"), assertion.get("canonical_eligible")
        if ret_elig is not None and not bool(ret_elig):
            return False
        if c_status == "canonical" and can_elig is not None and not bool(can_elig):
            return False
        if c_status == "candidate" and ret_elig is None and can_elig is not None and not bool(can_elig):
            return False
        return not (assertion.get("confidence_score") is not None and float(assertion["confidence_score"]) < 0.65)

    def _is_assertion_allowed(self, assertion: Dict[str, Any], claim: str) -> Optional[Tuple[str, str, str]]:
        if not isinstance(assertion, dict) or has_c19_marker(claim) or is_c19_quarantined_assertion(assertion):
            return None
        if not self._is_assertion_status_eligible(assertion, claim):
            return None
        if not self.engagement_id:
            from .copilot_connection_policy import is_enterprise_policy
            if not is_enterprise_policy(assertion):
                self._scope_filtered_count += 1
                return None
        elif not is_assertion_in_scope(assertion, self.engagement_id, self.permitted_engagements):
            self._scope_filtered_count += 1
            return None
        subj, obj = str(assertion.get("subject_node_id") or assertion.get("subject_node") or ""), str(assertion.get("object_node_id") or assertion.get("object_node") or "")
        pred = str(assertion.get("predicate") or assertion.get("legacy_predicate") or "").upper()
        if (not subj and not obj) or (subj and subj not in self._allowed_nodes) or (obj and obj in self._topology.nodes and obj not in self._allowed_nodes):
            return None
        return subj, obj, pred

    def _record_assertion_salience(self, subj: str, obj: str, obs_epoch: float, record: Dict[str, Any]) -> None:
        if obs_epoch > self._reference_epoch:
            self._reference_epoch = obs_epoch
        aid = record.get("assertion_id")
        if subj and not any(a.get("assertion_id") == aid for a in self._salient_assertions[subj]):
            self._salient_assertions[subj].append({**record, "direction": "outbound", "related_node": obj})
            self._node_latest_epoch[subj] = max(obs_epoch, self._node_latest_epoch.get(subj, 0.0))
        if obj and obj != subj and not str(obj).startswith("literal:") and not any(a.get("assertion_id") == aid for a in self._salient_assertions[obj]):
            self._salient_assertions[obj].append({**record, "direction": "inbound", "related_node": subj})
            self._node_latest_epoch[obj] = max(obs_epoch, self._node_latest_epoch.get(obj, 0.0))

    def _resolve_endpoint_label(self, nid: str) -> str:
        nd = getattr(self._topology, "nodes", {}).get(nid, {}) if self._topology else {}
        lbl = str(nd.get("display_name") or nd.get("canonical_name") or nd.get("name") or nd.get("label") or "").strip() if isinstance(nd, dict) else ""
        if not lbl and nid and not re.search(r"\b(?:entity|node)-[0-9a-f]{6,}\b", nid, re.IGNORECASE):
            lbl = re.sub(r"^(?:person|org|project|workstream|anchor|system|metric|role|policy)[_:\s]+", "", nid, flags=re.IGNORECASE).replace("_", " ").strip()
            lbl = lbl.title() if nid.lower().startswith("person_") else lbl
        return "" if re.search(r"\b(?:entity|node)-[0-9a-f]{6,}\b", lbl, re.IGNORECASE) else lbl

    def _resolve_effective_claim(self, aid: str, claim: str, subj: str, pred: str, obj: str) -> Tuple[str, bool]:
        if str(aid).startswith("asrt_edge_") or (claim and not re.match(r"^[A-Za-z][A-Za-z0-9_]{2,}\s*->\s*\S+$", claim.strip())):
            return claim or f"{pred} -> {obj}", False
        s_lbl, o_lbl = self._resolve_endpoint_label(subj), self._resolve_endpoint_label(obj)
        return (f"{s_lbl} {pred.lower().replace('_', ' ')} {o_lbl}", False) if (s_lbl and o_lbl) else (claim or f"{pred} -> {obj}", True)

    def _index_assertions(self) -> None:
        assertions_dict = getattr(self._topology, "assertions", None)
        if not isinstance(assertions_dict, dict):
            return
        for assertion_id, assertion in assertions_dict.items():
            if not isinstance(assertion, dict):
                continue
            claim = str(assertion.get("claim_text") or assertion.get("object_literal") or "")
            parsed = self._is_assertion_allowed(assertion, claim)
            if not parsed:
                continue
            subj, obj, pred = parsed
            eff_claim, has_opaque = self._resolve_effective_claim(assertion_id, claim, subj, pred, obj)
            status = str(assertion.get("canonical_status") or assertion.get("status") or "ACTIVE")
            observed = str(assertion.get("source_date_normalized") or assertion.get("observed_at") or assertion.get("valid_from") or "")
            source_id = str(assertion.get("primary_source_id") or assertion.get("extraction_run_id") or "")
            is_prio = normalize_copilot_predicate(pred) in PRIORITY_PREDICATES
            supp_ev = getattr(self._topology, "supporting_evidence", {})
            ev_list = supp_ev.get(assertion_id, []) if isinstance(supp_ev, dict) else []
            is_primary, is_pred, prov_scope, origin_eng = _determine_assertion_provenance(assertion, self.engagement_id, self.topological_engagements)
            obs_epoch = resolve_assertion_epoch({**assertion, "assertion_id": assertion_id, "evidence_ids": ev_list}, self._topology)
            asrt_tokens = _tokenize_text(eff_claim) | _tokenize_text(f"{subj} {obj} {pred}")
            for tok in asrt_tokens:
                self._claim_inverted_index[tok].append(assertion_id)
            record = {
                **assertion, "assertion_id": assertion_id, "predicate": pred, "claim_text": eff_claim,
                "status": status, "is_priority": is_prio, "subject": subj, "object": obj,
                "observed_at": observed, "source_date": observed, "source_id": source_id,
                "is_primary": is_primary, "is_predecessor": is_pred, "provenance_scope": prov_scope,
                "origin_engagement_id": origin_eng, "evidence_ids": ev_list,
                "_tokens": asrt_tokens, "_observed_epoch": obs_epoch, "_has_opaque_endpoint": has_opaque,
            }
            self._indexed_assertion_records[assertion_id] = record
            self._record_assertion_salience(subj, obj, obs_epoch, record)

    def _build_indexes(self) -> None:
        """Constructs inverted token indexes, attendee affinity sets, IDF tables, and bidirectional assertion registries."""
        if not self._topology or not hasattr(self._topology, "nodes"):
            return
        self._index_nodes()
        self._index_assertions()
        for nid, asrts in self._salient_assertions.items():
            asrts.sort(
                key=lambda a: (
                    1 if self._is_substantive_human_assertion(a) else 0,
                    0 if str(a.get("assertion_id") or "").startswith("asrt_attendee_") else 1,
                    1 if a.get("is_priority") else 0,
                    float(a.get("_observed_epoch") or 0.0),
                ),
                reverse=True,
            )
        total_docs = max(1, len(self._topology.nodes) + len(self._indexed_assertion_records))
        for tok in set(self._inverted_index) | set(self._claim_inverted_index):
            df = len(self._inverted_index.get(tok, ())) + len(self._claim_inverted_index.get(tok, ()))
            self._token_idf[tok] = math.log((total_docs + 1.0) / (df + 1.0)) + 1.0
        logger.debug("Indexed %d tokens, %d attendees, %d anchors", len(self._inverted_index), len(self._attendee_node_ids), len(self._salient_assertions))

    @staticmethod
    def _node_assertion_sort_key(a: Dict[str, Any]) -> Tuple[int, int, float]:
        return (
            1 if a.get("is_priority") else 0,
            0 if a.get("is_predecessor") else 1,
            float(a.get("_observed_epoch") or 0.0),
        )

    def _compute_node_context_multiplier(self, node_id: str) -> float:
        multiplier = 1.0
        if node_id in self._attendee_node_ids:
            multiplier *= 1.5
        if any(a.get("is_priority") for a in self._salient_assertions.get(node_id, [])):
            multiplier *= 1.3
        ref_ep = float(getattr(self, "_reference_epoch", 0.0) or 0.0)
        latest_ep = float((getattr(self, "_node_latest_epoch", None) or {}).get(node_id, 0.0) or 0.0)
        if ref_ep > 0.0 and latest_ep > 0.0:
            age_days = max(0.0, (ref_ep - latest_ep) / 86400.0)
            multiplier *= 0.70 + 0.30 * max(0.25, math.pow(0.5, age_days / 45.0))
        return multiplier

    def _format_node_context(
        self,
        node_id: str,
        max_assertions_per_entity: int,
    ) -> List[str]:
        node_data = self._topology.nodes.get(node_id, {})
        c_name = node_data.get("canonical_name", node_id)
        n_type = node_data.get("node_type", "entity")
        org = node_data.get("organization") or node_data.get("org", "")
        org_str = f" [Org: {org}]" if org else ""
        lines = [f"- Entity: {c_name} ({n_type}){org_str}"]

        assertions = sorted(
            self._salient_assertions.get(node_id, []),
            key=self._node_assertion_sort_key,
            reverse=True,
        )[:max_assertions_per_entity]

        for a in assertions:
            claim = a.get("claim_text") or a.get("predicate")
            if a.get("is_predecessor") or a.get("provenance_scope") == "PREDECESSOR":
                origin = a.get("origin_engagement_id") or a.get("engagement_id") or "Predecessor"
                lines.append(f"  * {a.get('predicate')}: {claim} [Predecessor Context: {origin}]")
            else:
                lines.append(f"  * {a.get('predicate')}: {claim}")

        # Fallback to forward edges if assertions are sparse
        fwd_adj = getattr(self._topology, "forward_adjacency", None)
        if not assertions and isinstance(fwd_adj, dict):
            for e in fwd_adj.get(node_id, [])[:2]:
                rel = e.get("relationship_family") or e.get("edge_type", "REL")
                target = e.get("to_node_name") or e.get("to_node", "target")
                lines.append(f"  * {rel}: {target}")

        # Connected multi-hop chains (Wave 3)
        if getattr(self, "neighborhood_store", None):
            chains = self.neighborhood_store.get_chains_for_node(node_id, limit=2)
            for c in chains:
                lines.append(f"  * Connected: {c.get('chain_str', '')}")

        return lines

    def retrieve_context(
        self,
        dialogue: Any,
        max_entities: int = 4,
        max_assertions_per_entity: int = 3,
    ) -> str:
        """Extracts high-salience, verified enterprise context for dialogue turns in < 1ms."""
        if not self._topology or not self._inverted_index:
            return ""

        query_tokens = _extract_query_tokens(dialogue)
        if not query_tokens:
            return ""

        candidate_scores: Dict[str, float] = defaultdict(float)
        for token in query_tokens:
            token_idf = self._token_idf.get(token, 1.0)
            for node_id in self._inverted_index.get(token, set()):
                candidate_scores[node_id] += token_idf
            if token in self._allowed_nodes:
                candidate_scores[token] += 2.0

        if not candidate_scores:
            return ""

        ranked = [
            (raw_score * self._compute_node_context_multiplier(node_id), node_id)
            for node_id, raw_score in candidate_scores.items()
        ]
        ranked.sort(key=lambda x: x[0], reverse=True)
        top_nodes = ranked[:max_entities]

        lines: List[str] = ["Verified Graph Knowledge:"]
        for _score, node_id in top_nodes:
            lines.extend(self._format_node_context(node_id, max_assertions_per_entity))

        return "\n".join(lines) if len(lines) > 1 else ""

    def extract_candidate_assertions(
        self,
        dialogue: Any,
        max_entities: int = 6,
        max_assertions_per_entity: int = 5,
    ) -> List[Dict[str, Any]]:
        """Extracts candidate assertions for Wave 4 grounding verification in < 0.2ms."""
        if not self._topology or not self._inverted_index:
            return []
        query_tokens = _extract_query_tokens(dialogue)
        if not query_tokens:
            return []
        candidate_scores: Dict[str, float] = defaultdict(float)
        for token in query_tokens:
            token_idf = self._token_idf.get(token, 1.0)
            for node_id in self._inverted_index.get(token, set()):
                candidate_scores[node_id] += token_idf
            if token in self._allowed_nodes:
                candidate_scores[token] += 2.0
        if not candidate_scores:
            return []
        ranked = sorted(candidate_scores.items(), key=lambda x: x[1], reverse=True)
        top_nodes = [nid for nid, _ in ranked[:max_entities]]
        candidates: List[Dict[str, Any]] = []
        seen_aids: Set[str] = set()
        for nid in top_nodes:
            for a in self._salient_assertions.get(nid, [])[:max_assertions_per_entity]:
                aid = a.get("assertion_id")
                if aid and aid not in seen_aids:
                    seen_aids.add(aid)
                    candidates.append(a)
        return candidates

    def _drain_node_assertions(
        self,
        node_id: str,
        seen_ids: Set[str],
        priority_only: bool,
        limit: int,
    ) -> List[Dict[str, Any]]:
        batch: List[Dict[str, Any]] = []
        for a in self._salient_assertions.get(node_id, []):
            aid = a.get("assertion_id")
            if not aid or aid in seen_ids or (priority_only and not a.get("is_priority")):
                continue
            seen_ids.add(aid)
            batch.append(a)
            if len(batch) >= limit:
                break
        return batch

    def _collect_node_assertions(
        self,
        matched_nodes: Set[str],
        seen_ids: Set[str],
        priority_only: bool,
        max_results: int,
        query_tokens: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        nodes = getattr(self._topology, "nodes", {}) if self._topology else {}
        ordered_nodes = rank_nodes_by_tokens(
            matched_nodes,
            query_tokens or set(),
            nodes,
            self._inverted_index,
            self._token_idf,
            getattr(self, "_node_latest_epoch", None),
            float(getattr(self, "_reference_epoch", 0.0) or 0.0),
        )
        for pass_limit in (3, 10):
            for node_id in ordered_nodes:
                needed = max_results - len(results)
                if needed <= 0:
                    return results
                results.extend(self._drain_node_assertions(node_id, seen_ids, priority_only, min(pass_limit, needed)))
        return results

    def _match_unmatched_claim(self, a: Any, query_tokens: Set[str]) -> Optional[str]:
        if not isinstance(a, dict):
            return None
        claim = str(a.get("object_literal") or a.get("claim_text") or "")
        if not self._is_assertion_allowed(a, claim):
            return None
        if query_tokens & _tokenize_text(claim):
            return claim
        return None

    def _build_unmatched_claim_record(self, aid: str, a: Dict[str, Any], claim: str) -> Dict[str, Any]:
        pred, subj, obj = str(a.get("predicate") or "").upper(), str(a.get("subject_node_id") or ""), str(a.get("object_node_id") or "")
        observed = str(a.get("source_date_normalized") or a.get("observed_at") or "")
        is_primary, is_pred, prov_scope, origin_eng = _determine_assertion_provenance(
            a, self.engagement_id, self.topological_engagements
        )
        asrt_tokens = _tokenize_text(claim) | _tokenize_text(f"{subj} {obj} {pred}")
        return {
            **a, "assertion_id": aid, "predicate": pred, "claim_text": claim or f"{subj} {pred} {obj}",
            "subject": subj, "object": obj, "observed_at": observed, "source_date": observed,
            "source_id": str(a.get("primary_source_id") or a.get("extraction_run_id") or ""),
            "is_priority": normalize_copilot_predicate(pred) in PRIORITY_PREDICATES,
            "is_primary": is_primary, "is_predecessor": is_pred, "provenance_scope": prov_scope,
            "origin_engagement_id": origin_eng, "_tokens": asrt_tokens,
        }

    def _scan_unindexed_claims(
        self,
        assertions_dict: Dict[str, Any],
        query_tokens: Set[str],
        seen_ids: Set[str],
        max_results: int,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for aid, a in assertions_dict.items():
            if aid in seen_ids:
                continue
            claim = self._match_unmatched_claim(a, query_tokens)
            if claim is not None:
                seen_ids.add(aid)
                results.append(self._build_unmatched_claim_record(aid, a, claim))
                if len(results) >= max_results:
                    break
        return results

    def _collect_indexed_claim_candidates(self, query_tokens: Set[str]) -> List[str]:
        aid_scores: Dict[str, float] = defaultdict(float)
        for tok in query_tokens:
            idf = self._token_idf.get(tok, 1.0)
            for aid in self._claim_inverted_index.get(tok, []):
                aid_scores[aid] += idf
        return sorted(
            aid_scores,
            key=lambda a: (
                1 if self._indexed_assertion_records.get(a, {}).get("is_primary") else 0,
                1 if self._is_substantive_human_assertion(self._indexed_assertion_records.get(a, {})) else 0,
                aid_scores[a],
                a,
            ),
            reverse=True,
        )

    def _scan_indexed_claims(
        self,
        assertions_dict: Dict[str, Any],
        query_tokens: Set[str],
        seen_ids: Set[str],
        max_results: int,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for aid in self._collect_indexed_claim_candidates(query_tokens):
            if aid in seen_ids:
                continue
            rec = self._indexed_assertion_records.get(aid)
            if rec is None:
                continue
            seen_ids.add(aid)
            results.append(rec)
            if len(results) >= max_results:
                break
        return results

    def _scan_unmatched_claims(
        self,
        query_tokens: Set[str],
        seen_ids: Set[str],
        max_results: int,
    ) -> List[Dict[str, Any]]:
        assertions_dict = getattr(self._topology, "assertions", {})
        if not self._claim_inverted_index:
            return self._scan_unindexed_claims(assertions_dict, query_tokens, seen_ids, max_results)
        return self._scan_indexed_claims(assertions_dict, query_tokens, seen_ids, max_results)

    _extract_query_tokens_and_seeds = staticmethod(extract_query_tokens_and_seeds)
    _score_assertion = score_provenance_assertion
    _retrieve_lexical_ranking = retrieve_lexical_ranking
    _retrieve_dense_ranking = retrieve_dense_ranking
    _collect_multi_query_rankings = collect_multi_query_rankings

    def _apply_decay_to_rrf_pairs(
        self, fused_pairs: List[Tuple[str, float]], query_str: str
    ) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
        ref_ep = float(getattr(self, "_reference_epoch", 0.0) or 0.0)
        fused_scores: Dict[str, float] = {}
        for aid, rrf_val in fused_pairs:
            asrt_rec = self._indexed_assertion_records.get(aid)
            decay_m = compute_temporal_decay(asrt_rec, ref_ep, query_str) if asrt_rec else 1.0
            fused_scores[aid] = rrf_val * decay_m
        ordered = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
        candidates = [
            self._indexed_assertion_records[aid]
            for aid, _ in ordered
            if aid in self._indexed_assertion_records
        ]
        return candidates, fused_scores

    def retrieve_structured_assertions(
        self,
        query: Any,
        max_results: int = 10,
        priority_only: bool = False,
        query_vector: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves structured, de-duplicated assertions matching a query string or context model."""
        if not self._topology:
            return []

        if hasattr(self, "_scope_binding") and self.session_id and self.engagement_id:
            if not self._scope_binding.is_valid(self.session_id, self.engagement_id, self.generation):
                return []

        if getattr(self, "neighborhood_store", None) is None:
            from .copilot_circuit_breaker import get_copilot_circuit_breaker
            get_copilot_circuit_breaker().record_fallback("neighborhood_store_absent")

        multi_queries = expand_multi_queries(query)
        q_vec = query_vector if query_vector is not None else getattr(query, "query_vector", None)
        if q_vec is None and self.embed_fn is not None and "primary" in multi_queries:
            try:
                q_vec = self.embed_fn(multi_queries["primary"])
            except Exception as e:
                logger.warning("Query embedding generation failed: %s", e)

        query_tokens, direct_seed_nodes = self._extract_query_tokens_and_seeds(query, self._allowed_nodes)
        if not query_tokens and not direct_seed_nodes and q_vec is None:
            return []

        query_str = multi_queries.get("primary", "")
        extra_permitted = resolve_permitted_engagements(query_str, self.engagement_id, self._topology)
        effective_permitted = ((self.permitted_engagements or set()) | extra_permitted) if extra_permitted else self.permitted_engagements

        results: List[Dict[str, Any]] = []
        try:
            rankings = self._collect_multi_query_rankings(
                multi_queries, query_tokens, direct_seed_nodes, priority_only, max_results,
                permitted_engagements=effective_permitted,
            )

            num_lex = len(rankings)
            dense_ranking = (
                self._retrieve_dense_ranking(q_vec, top_k=max(50, max_results * 5), query_str=query_str, permitted_engagements=effective_permitted)
                if q_vec is not None else []
            )
            if dense_ranking:
                rankings.append(dense_ranking)
            if not rankings:
                return []
            rrf_w = compute_copilot_rrf_weights(num_lex, bool(dense_ranking))
            candidates, fused_scores = self._apply_decay_to_rrf_pairs(reciprocal_rank_fusion(rankings, weights=rrf_w), query_str)

            if self.enable_1hop_graph_expansion:
                expanded = expand_1hop_graph_neighbors(
                    candidates,
                    self._topology,
                    self._salient_assertions,
                    self.engagement_id,
                    effective_permitted,
                )
                for exp in expanded:
                    eaid = exp.get("assertion_id")
                    if eaid and eaid not in fused_scores:
                        if self._score_assertion(exp, query_tokens, query_str, permitted_engagements=effective_permitted) <= 0.0:
                            continue
                        candidates.append(exp)
                        base_val = min(fused_scores.values()) if fused_scores else 0.01
                        fused_scores[eaid] = 0.5 * base_val

            if self.enable_cross_encoder:
                candidates = cross_encoder_rerank(candidates, query_str, self.cross_encoder_fn)

            results = maximal_marginal_relevance(
                candidates,
                fused_scores,
                max_results=max_results,
                dense_matrix=self._dense_matrix,
                record_id_to_idx=self._record_id_to_idx,
            )

            from .copilot_connection_policy import is_enterprise_policy
            results = [
                a for a in results
                if not re.search(r"\b(?:is an identified|is a reported speaker in)\b", str(a.get("claim_text") or a.get("object_literal") or ""), re.IGNORECASE)
                and (
                    is_assertion_in_scope(a, self.engagement_id, effective_permitted)
                    if self.engagement_id
                    else is_enterprise_policy(a)
                )
            ]

            for r in results:
                if self.generation and "_pinned_generation" not in r:
                    r["_pinned_generation"] = self.generation
                if self.graph_sha256 and "_pinned_graph_sha" not in r:
                    r["_pinned_graph_sha"] = self.graph_sha256
            return results
        finally:
            if extra_permitted:
                from .copilot_circuit_breaker import get_copilot_circuit_breaker
                get_copilot_circuit_breaker().record_cross_engagement_result(len(results), query_str)
        return results

    @staticmethod
    def _is_substantive_human_assertion(c: Dict[str, Any]) -> bool:
        aid = str(c.get("assertion_id") or "")
        if aid.startswith(("asrt_attendee_", "asrt_edge_")) or c.get("_has_opaque_endpoint"):
            return False
        claim = str(c.get("claim_text") or c.get("object_literal") or "")
        if "is an active meeting participant with role" in claim.lower() or " -> " in claim:
            return False
        subj, obj = str(c.get("subject") or c.get("subject_node_id") or ""), str(c.get("object") or c.get("object_node_id") or "")
        return subj.startswith(("person:", "attendee:", "workstream:")) or obj.startswith(("person:", "attendee:", "workstream:"))

    @classmethod
    def _filter_substantive_predicates(
        cls, candidates: List[Dict[str, Any]], target_predicates: Set[str], max_items: int
    ) -> List[Dict[str, Any]]:
        sub = [
            c for c in candidates
            if str(c.get("predicate") or "").upper() != "HAS_CLAIM_TEXT"
            and not c.get("_has_opaque_endpoint")
            and not (str(c.get("assertion_id") or "").startswith("asrt_edge_") and " -> " in str(c.get("claim_text") or ""))
            and not re.search(r"\b(?:entity|node)-[0-9a-f]{6,}\b", str(c.get("claim_text") or ""), re.IGNORECASE)
            and not re.search(r"\bis an identified\b", str(c.get("claim_text") or c.get("object_literal") or ""), re.IGNORECASE)
        ]
        filt = [c for c in sub if normalize_copilot_predicate(c.get("predicate")) in target_predicates]
        pool = filt if filt else [c for c in sub if normalize_copilot_predicate(c.get("predicate")) not in ("HAS_CLAIM_TEXT", "")]
        non_stubs = [c for c in pool if not str(c.get("assertion_id") or "").startswith("asrt_attendee_")]
        if non_stubs:
            pool = non_stubs
        pool.sort(key=lambda c: (1 if cls._is_substantive_human_assertion(c) else 0, float(c.get("_observed_epoch") or 0.0)), reverse=True)
        return pool[:max_items]

    def get_next_questions(self, dialogue: Any, max_questions: int = 3) -> List[Dict[str, Any]]:
        """Synthesizes high-salience questions anchored in open decisions, questions, and risks."""
        candidates = list(self.retrieve_structured_assertions(dialogue, max_results=15))
        _, chain_assertions = self.get_connected_chains(dialogue, limit=3)
        seen_ids = {c.get("assertion_id") for c in candidates if c.get("assertion_id")}
        for ca in chain_assertions:
            aid = ca.get("assertion_id")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                candidates.append(ca)
        return self._filter_substantive_predicates(candidates, QUESTION_PREDICATES, max_questions)

    def get_next_actions(self, dialogue: Any, max_actions: int = 3) -> List[Dict[str, Any]]:
        """Synthesizes high-salience actions anchored in ownership, deliverables, and dependencies."""
        candidates = list(self.retrieve_structured_assertions(dialogue, max_results=15))
        _, chain_assertions = self.get_connected_chains(dialogue, limit=3)
        seen_ids = {c.get("assertion_id") for c in candidates if c.get("assertion_id")}
        for ca in chain_assertions:
            aid = ca.get("assertion_id")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                candidates.append(ca)
        return self._filter_substantive_predicates(candidates, ACTION_PREDICATES, max_actions)

    def _match_person_node_id(self, tokens: Set[str]) -> Optional[str]:
        candidate_ids: Set[str] = set()
        for t in tokens:
            for cid in self._inverted_index.get(t, set()):
                nd = self._topology.nodes.get(cid, {})
                names = f"{nd.get('canonical_name') or ''} {nd.get('display_name') or ''}".lower()
                if any(tok in names for tok in tokens):
                    candidate_ids.add(cid)
        if not candidate_ids:
            return None
        ranked_ids = rank_nodes_by_tokens(
            candidate_ids, tokens, self._topology.nodes, self._inverted_index, self._token_idf,
            getattr(self, "_node_latest_epoch", None), float(getattr(self, "_reference_epoch", 0.0) or 0.0),
        )
        person_ranked = [cid for cid in ranked_ids if str(self._topology.nodes.get(cid, {}).get("node_type") or "").lower() == "person" or cid.startswith("person:")]
        return person_ranked[0] if person_ranked else (ranked_ids[0] if ranked_ids else None)

    @staticmethod
    def _filter_person_assertions_by_engagement(raw_salient: List[Dict[str, Any]], engagement_id: Optional[str]) -> List[Dict[str, Any]]:
        if not engagement_id:
            return raw_salient
        norm_eng = normalize_engagement_id(engagement_id)
        return [
            a for a in raw_salient
            if ((a_eng := normalize_engagement_id(a.get("engagement_id") or a.get("project_scope"))) and match_engagement(a_eng, norm_eng))
            or is_enterprise_policy(a)
        ]

    def get_person_context(self, person_name: str, dialogue: Any) -> Dict[str, Any]:
        """Retrieves tactical person profile, affiliations, and assertions related to target attendee."""
        if not self._topology:
            return {"target": person_name, "found": False, "assertions": []}
        tokens = _tokenize_text(person_name)
        matched_node_id = self._match_person_node_id(tokens)
        assertions: List[Dict[str, Any]] = []
        node_info: Dict[str, Any] = {}
        if matched_node_id:
            node_info = self._topology.nodes.get(matched_node_id, {})
            raw_salient = self._filter_person_assertions_by_engagement(list(self._salient_assertions.get(matched_node_id, [])), self.engagement_id)
            substantive = [a for a in raw_salient if not str(a.get("assertion_id") or "").startswith(("asrt_attendee_", "asrt_edge_"))]
            seed_text_for_filter = " ".join(dialogue.retrieval_seeds()) if hasattr(dialogue, "retrieval_seeds") else str(dialogue or "")
            topic_tokens = _tokenize_text(seed_text_for_filter) - tokens
            assertions = (
                [a for a in substantive if topic_tokens & (a.get("_tokens") or _tokenize_text(str(a.get("claim_text") or "")))]
                if (topic_tokens and substantive) else (substantive if substantive else raw_salient)
            )
        if not assertions or all(str(a.get("assertion_id") or "").startswith("asrt_attendee_") for a in assertions):
            seed_text = " ".join(dialogue.retrieval_seeds()) if hasattr(dialogue, "retrieval_seeds") else str(dialogue)
            retrieved = self.retrieve_structured_assertions(f"{person_name} {seed_text}", max_results=5)
            non_stub_ret = [a for a in retrieved if not str(a.get("assertion_id") or "").startswith("asrt_attendee_")]
            assertions = non_stub_ret if non_stub_ret else (assertions or retrieved)
        return {"target": person_name, "node_id": matched_node_id, "found": bool(matched_node_id or assertions), "node_info": node_info, "assertions": assertions[:5]}

    def get_graph_context(self, dialogue: Any) -> List[Dict[str, Any]]:
        """Returns verified graph assertions for general context inquiry."""
        return self.retrieve_structured_assertions(dialogue, max_results=8)

    def get_connected_chains(self, dialogue: Any, limit: int = 5) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Surfaces cross-SOW multi-hop chains ranked by dialogue salience and flow state."""
        from .copilot_connect import get_connected_chains
        return get_connected_chains(self, dialogue, limit=limit)

    def get_gaps(self, dialogue: Any) -> Tuple[List[Dict[str, Any]], bool]:
        """Retrieves assertions across different meetings/dates and detects semantic contradictions."""
        from .copilot_connect import find_contradiction_gaps
        return find_contradiction_gaps(self, dialogue)


def search_session_context(session_turns: List[Dict[str, Any]], query: str, top_k: int = 2) -> str:
    """Retrieves relevant prior dialogue turns from full session memory outside recent window in <0.5ms."""
    if len(session_turns) <= 4:
        return ""
    q_tokens = _extract_query_tokens(query)
    if not q_tokens:
        return ""
    scored = [
        (float(overlap), idx, t)
        for idx, t in enumerate(session_turns[:-4])
        if (overlap := len(q_tokens & _tokenize_text(t.get("text", "")))) >= 2
    ]
    if not scored:
        return ""
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    lines = ["[Relevant Prior Dialogue In Meeting]:"] + [f"- {t.get('timestamp', '')} {t.get('speaker', '')}: {t.get('text', '')}" for _, _, t in scored[:top_k]]
    return "\n".join(lines)


class GraphLoadStatus(str, Enum):
    LOADED_ACTIVE_PAIR = "LOADED_ACTIVE_PAIR"
    ABSENT = "ABSENT"
    ERROR = "ERROR"
    HASH_MISMATCH = "HASH_MISMATCH"


def _init_neighborhood_store(
    in_mem: Any, retriever: CopilotGraphRetriever, attendees: Optional[List[Dict[str, str]]], engagement_id: Optional[str], timeout_seconds: float
) -> CopilotNeighborhoodStore:
    seeds = set(retriever._attendee_node_ids)
    nodes = getattr(in_mem, "nodes", {})
    if engagement_id and isinstance(nodes, dict):
        norm = engagement_id.lower().replace("-", "_")
        for cand in (engagement_id, f"project:{norm}", norm):
            if cand in nodes:
                seeds.add(cand)
        clean_eng = engagement_id.lower().replace("project:", "").strip(":-_")
        if clean_eng:
            for nid, nd in nodes.items():
                if not isinstance(nd, dict):
                    continue
                if clean_eng in str(nd.get("engagement_id") or "").lower() or clean_eng in nid.lower() or clean_eng in str(nd.get("canonical_name") or "").lower():
                    seeds.add(nid)
                    if len(seeds) >= 10:
                        break
    if attendees and isinstance(nodes, dict):
        seeds.update(_find_attendee_seeds(attendees, nodes))
    if not seeds and retriever._allowed_nodes:
        seeds.update(list(retriever._allowed_nodes)[:5])
    chains, nh_status = compute_neighborhood_chains(
        topology=in_mem, seed_node_ids=seeds, timeout_seconds=timeout_seconds, engagement_id=engagement_id, permitted_engagements=retriever.permitted_engagements
    )
    return CopilotNeighborhoodStore(chains=chains, status=nh_status, engagement_id=engagement_id, permitted_engagements=retriever.permitted_engagements)


def load_copilot_graph(
    attendees: Optional[List[Dict[str, str]]] = None, runtime_root: Optional[Union[str, Path]] = None,
    engagement_id: Optional[str] = None, session_id: Optional[str] = None, precompute_timeout_seconds: float = 0.150,
    graph_path: Optional[Union[str, Path]] = None, bypass_cache: bool = False,
) -> Tuple[GraphLoadStatus, Optional[Any], Optional[CopilotGraphRetriever]]:
    """Loads active knowledge graph generation into memory with fail-closed verification."""
    from .copilot_cache_graph import execute_load_copilot_graph
    return execute_load_copilot_graph(
        attendees=attendees, runtime_root=runtime_root, engagement_id=engagement_id, session_id=session_id,
        precompute_timeout_seconds=precompute_timeout_seconds, graph_path=graph_path, retriever_cls=CopilotGraphRetriever,
        graph_load_status_cls=GraphLoadStatus, seed_identities_fn=seed_attendee_identities,
        init_neighborhood_fn=_init_neighborhood_store, inherit_engagements_fn=discover_topologically_inherited_engagements,
        neighborhood_store_cls=CopilotNeighborhoodStore, scope_binding_cls=ScopeBinding, bypass_cache=bypass_cache,
    )


def prefetch_session_packet(
    in_memory_graph: Optional[Any], retriever: Optional[CopilotGraphRetriever], project_name: str = "", meeting_name: str = "",
    attendees: Optional[List[Dict[str, str]]] = None, timeout_seconds: float = 5.0, engagement_id: Optional[str] = None, session_id: Optional[str] = None,
) -> Any:
    """Pre-fetches a SessionContextPacket from in-memory graph and retriever at session start."""
    return CopilotPrefetchBridge(in_memory_graph=in_memory_graph, retriever=retriever, timeout_seconds=timeout_seconds).prefetch(
        project_name=project_name, meeting_name=meeting_name, attendees=attendees, engagement_id=engagement_id, session_id=session_id
    )
