"""Tab 4: Relational Graph Topology & Causal Chains."""
from __future__ import annotations

from typing import Any


def render_relational_graph_view(st, db_manager: Any) -> None:
    st.subheader("🌐 Relational Graph Topology & Causal Chains")
    st.caption("First-class relational graph edges connecting enterprise architecture decisions, grid dependencies, and causal paths.")

    canonical_edges = db_manager.query_canonical_edges(limit=50)
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        st.metric("Total Relational Graph Edges", len(canonical_edges))
    with col_e2:
        predicates = list(set(e.predicate for e in canonical_edges)) if canonical_edges else []
        st.metric("Unique Predicates", len(predicates), delta=", ".join(predicates[:3]) if predicates else "None")

    if not canonical_edges:
        st.info("No relational edges currently registered in canonical graph. Ingesting transcripts with Pipeline B automatically extracts relationships.")
    else:
        for edge in canonical_edges:
            st.markdown(
                f"""
                <div class="metric-card" style="margin-bottom: 8px; border-left: 4px solid #3b82f6;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="color: #38bdf8; font-weight: 700;"><code>{edge.from_node_id}</code></span>
                        <span style="background: #1e3a8a; color: #93c5fd; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 0.8rem;">
                            &rarr; {edge.predicate} &rarr;
                        </span>
                        <span style="color: #34d399; font-weight: 700;"><code>{edge.to_node_id}</code></span>
                    </div>
                    <div style="margin-top: 6px; font-size: 0.82rem; color: #94a3b8;">
                        Confidence: <strong>{int(edge.confidence * 100)}%</strong> | Namespace: <code>{edge.namespace}</code> | Edge ID: <code>{edge.edge_id}</code>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
