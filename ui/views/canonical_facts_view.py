"""Tab 1: Client-Safe Enterprise Architecture Nodes."""
from __future__ import annotations

from typing import Any, List


def render_canonical_facts_view(st, canonical_facts: List[Any]) -> None:
    st.subheader("Client-Safe Enterprise Architecture Nodes")

    if not canonical_facts:
        st.info("No canonical facts matching current query filter.")
        return

    for f in canonical_facts:
        edges_block = ""
        if getattr(f, "connected_edges", None):
            edge_items = []
            for e in f.connected_edges:
                pred = e.get("predicate", "RELATION")
                target = e.get("to_node_id") if e.get("from_node_id") == f.node_id else e.get("from_node_id")
                edge_items.append(f"<li><strong>{pred}</strong> &rarr; <code>{target}</code></li>")
            edges_block = f"""
            <div style="margin-top: 8px; padding-top: 6px; border-top: 1px dashed rgba(255,255,255,0.15);">
                <span style="font-size: 0.8rem; color: #6ee7b7; font-weight: 600;">🌐 Relational Graph Connections:</span>
                <ul style="margin: 4px 0 0 16px; padding: 0; font-size: 0.82rem; color: #94a3b8;">
                    {''.join(edge_items)}
                </ul>
            </div>
            """

        with st.container():
            st.markdown(
                f"""
                <div class="metric-card" style="margin-bottom: 12px; border-left: 4px solid #10b981;">
                    <span class="client-safe-badge">{f.namespace}</span>
                    <span style="color: #94a3b8; font-size: 0.85rem; margin-left: 8px;">TYPE: {f.type}</span>
                    <h4 style="margin: 8px 0 4px 0; color: #38bdf8;">{f.name}</h4>
                    <p style="margin: 0 0 8px 0; color: #cbd5e1;">{f.snippet}</p>
                    <small style="color: #94a3b8;">
                        ⭐ Strategic Value: <strong>{f.strategic_value}/10</strong> | 
                        🎯 Confidence: <strong>{int(f.confidence * 100)}%</strong> | 
                        🔗 Evidence ID: <code>{f.evidence_id}</code>
                    </small>
                    {edges_block}
                </div>
                """,
                unsafe_allow_html=True,
            )
