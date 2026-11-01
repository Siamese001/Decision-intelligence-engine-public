"""Tab 2: Governed Internal Advisory Intelligence."""
from __future__ import annotations

from typing import Any, List


def render_internal_advisory_view(st, advisory_facts: List[Any], stealth_mode: bool) -> None:
    if stealth_mode:
        st.info("🔒 Internal Advisory Intelligence is locked in Stealth Mode. Toggle off in sidebar to unmask.")
        return

    st.subheader("Governed Internal Advisory Intelligence")

    if not advisory_facts:
        st.info("No internal advisory facts matching current query.")
        return

    for adv in advisory_facts:
        with st.container():
            st.markdown(
                f"""
                <div class="advisory-card">
                    <span class="advisory-badge">{adv.type}</span>
                    <h4 style="margin: 8px 0 4px 0; color: #f8fafc;">{adv.name}</h4>
                    <p style="margin: 0 0 10px 0; color: #cbd5e1;">{adv.snippet}</p>
                    <small style="color: #a5b4fc;">
                        ⭐ Strategic Value: <strong>{adv.strategic_value}/10</strong> | 
                        🛡️ Stealth Score: <strong>{adv.properties.get('stealth_score', 1.0)}</strong> | 
                        🔗 Evidence ID: <code>{adv.evidence_id}</code>
                    </small>
                </div>
                """,
                unsafe_allow_html=True,
            )
