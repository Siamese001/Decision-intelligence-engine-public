"""Tab 3: Multi-Dimensional Soft-Quarantine Inspector (Law 5)."""
from __future__ import annotations

from typing import Any


def render_quarantine_view(st, db_manager: Any) -> None:
    st.subheader("Multi-Dimensional Soft-Quarantine Inspector (Law 5)")
    st.caption(
        "Demonstrates non-destructive tagging: tactical operational to-dos and calendar invites are soft-quarantined "
        "rather than pruned, preserving complete forensic auditability."
    )

    all_canonical = db_manager.query_canonical_nodes(include_quarantined=True)
    quarantined = [n for n in all_canonical if n.is_quarantined]

    col_q1, col_q2 = st.columns([1, 1])
    with col_q1:
        st.metric("Total Canonical Nodes", len(all_canonical))
    with col_q2:
        st.metric("Soft-Quarantined (Tactical)", len(quarantined), delta="Suppressed from Search")

    for q_node in quarantined:
        st.markdown(
            f"""
            <div class="metric-card" style="margin-bottom: 12px; border-left: 4px solid #f59e0b;">
                <span class="quarantine-badge">SOFT QUARANTINED</span>
                <span style="color: #94a3b8; font-size: 0.85rem; margin-left: 8px;">REASON: {q_node.quarantine_reason}</span>
                <h4 style="margin: 8px 0 4px 0; color: #fde68a;">{q_node.name}</h4>
                <p style="margin: 0 0 8px 0; color: #cbd5e1;">{q_node.properties.get('context', q_node.name)}</p>
                <small style="color: #94a3b8;">
                    Tacticality: <strong>{q_node.tacticality}/10</strong> | 
                    Strategic Value: <strong>{q_node.strategic_value}/10</strong> | 
                    Evidence ID: <code>{q_node.evidence_id}</code>
                </small>
            </div>
            """,
            unsafe_allow_html=True,
        )
