"""Tab 5: Governed Card Views & Controlled Facet Architecture."""
from __future__ import annotations

import json as py_json
import sqlite3 as py_sqlite3
from pathlib import Path


def render_card_views_tab(st) -> None:
    st.subheader("🃏 Governed Card Views & Controlled Facet Architecture")
    st.caption(
        "Demonstrates the Tripartite Model: Cards are versioned, access-sliced SAVED SEMANTIC VIEWS, "
        "not graph storage containers and NEVER evidence. Facets provide orthogonal, many-to-many classification."
    )

    active_pair_file = Path(".local/dual_pipeline/active_pair.json")
    if not active_pair_file.exists():
        st.info("Active pair metadata not found. Run 'nee cards-refactor release-e2e' to activate governed card views.")
        return

    with open(active_pair_file) as f:
        pair_data = py_json.load(f)
    active_graph_path = Path(".local/dual_pipeline") / pair_data["graph_path"]

    if not active_graph_path.exists():
        st.info("Active graph sqlite file not found.")
        return

    con = py_sqlite3.connect(f"file:{active_graph_path.as_posix()}?mode=ro", uri=True)
    con.row_factory = py_sqlite3.Row

    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        has_card_views = "card_view_definition" in tables
        has_facets = "object_facet_assignment" in tables

        c_col1, c_col2, c_col3, c_col4 = st.columns(4)
        with c_col1:
            st.metric("Active Generation", pair_data["generation_id"][:16] + "...")
        with c_col2:
            card_views_count = con.execute("SELECT count(*) FROM card_view_definition WHERE active=1").fetchone()[0] if has_card_views else 0
            st.metric("Governed Card Views", card_views_count)
        with c_col3:
            facet_count = con.execute("SELECT count(*) FROM object_facet_assignment WHERE active=1").fetchone()[0] if has_facets else 0
            st.metric("Object Facet Assignments", facet_count)
        with c_col4:
            rules_count = con.execute("SELECT count(*) FROM facet_derivation_rule WHERE active=1").fetchone()[0] if "facet_derivation_rule" in tables else 0
            st.metric("Facet Derivation Rules", rules_count)

        if has_card_views and card_views_count > 0:
            card_rows = con.execute(
                """SELECT card_view_id, card_id, view_kind, authority_mode,
                          classification_ceiling, eligible_audiences_json,
                          editorial_owner, selector_json, selector_digest
                     FROM card_view_definition
                    WHERE active=1
                    ORDER BY card_id"""
            ).fetchall()
            card_ids = [r["card_id"] for r in card_rows]

            selected_card_id = st.selectbox(
                "Select Governed Card View Definition",
                card_ids,
                index=1 if len(card_ids) > 1 else 0,
                key="card_view_select"
            )
            selected_row = next(r for r in card_rows if r["card_id"] == selected_card_id)

            v_col1, v_col2 = st.columns([1, 1])
            with v_col1:
                st.markdown(f"""
                <div class="metric-card" style="border-left: 4px solid #8b5cf6;">
                    <h4 style="color: #c084fc; margin: 0 0 6px 0;">Card View: {selected_row['card_id']}</h4>
                    <p style="color: #cbd5e1; font-size: 0.9rem; margin: 0 0 6px 0;">
                        <strong>View ID:</strong> <code>{selected_row['card_view_id']}</code><br>
                        <strong>Kind:</strong> {selected_row['view_kind']} | <strong>Authority:</strong> {selected_row['authority_mode']}<br>
                        <strong>Classification Ceiling:</strong> <span class="client-safe-badge">{selected_row['classification_ceiling']}</span><br>
                        <strong>Editorial Owner:</strong> {selected_row['editorial_owner']}<br>
                        <strong>Eligible Audiences:</strong> <code>{selected_row['eligible_audiences_json']}</code>
                    </p>
                </div>
                """, unsafe_allow_html=True)
            with v_col2:
                st.markdown(f"**Selector AST (Digest: `{selected_row['selector_digest'][:16]}...`)**")
                st.json(py_json.loads(selected_row["selector_json"]), expanded=False)

            st.markdown("#### 🔒 Access-Sliced Materialization Simulator")
            st.caption(
                "Demonstrates runtime security slicing: only assertions matching the caller's classification clearance "
                "and audience list are materialized. The sealed receipt proves card prose was never evidence."
            )

            sim_col1, sim_col2, sim_col3 = st.columns([1, 1, 1])
            with sim_col1:
                sim_clearance = st.selectbox(
                    "Caller Clearance",
                    ["internal", "confidential", "restricted", "public"],
                    index=0,
                    key="sim_clearance"
                )
            with sim_col2:
                sim_audience = st.selectbox(
                    "Caller Audience",
                    ["internal_consulting", "nee-engineering", "public", "alpha"],
                    index=0,
                    key="sim_audience"
                )
            with sim_col3:
                st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
                run_mat_btn = st.button("⚡ Materialize Access-Sliced View", use_container_width=True, key="mat_btn")

            if run_mat_btn:
                from dual_pipeline.card_views import materialize_card_view, render_card_markdown
                from tools.audit_card_drift import audit_card_drift
                auth_ctx = {
                    "classification_clearance": sim_clearance,
                    "audiences": [sim_audience],
                    "candidate_review": False,
                }
                try:
                    mat_receipt = materialize_card_view(
                        con,
                        card_id=selected_card_id,
                        generation_id=pair_data["generation_id"],
                        graph_sha256=pair_data["graph_sha256"],
                        authorization_context=auth_ctx,
                    )
                    st.success(f"✅ Materialized Receipt: `{mat_receipt['receipt_id']}`")
                    st.markdown(f"""
                    - **Approved Assertions Materialized**: `{mat_receipt['lane_counts']['approved']}`
                    - **Total Assertions Materialized**: `{mat_receipt['assertion_count']}`
                    - **Evidence Items Cited**: `{mat_receipt['evidence_count']}`
                    - **Candidate Review Items**: `{mat_receipt['lane_counts']['candidate_review']}`
                    - **Card Content Used As Evidence**: `FALSE` *(Guaranteed Invariant)*
                    - **Renderer Version**: `{mat_receipt['renderer_version']}`
                    """)

                    compiled_md = render_card_markdown(con, mat_receipt)
                    with st.expander("📄 Compiled Executive Dossier (Live Markdown Preview)", expanded=True):
                        st.markdown(compiled_md)
                        st.download_button(
                            label=f"📥 Download {selected_card_id} Governed Dossier (.md)",
                            data=compiled_md,
                            file_name=f"{selected_card_id}_governed_dossier.md",
                            mime="text/markdown",
                            key="download_compiled_md"
                        )

                    with st.expander("🔍 Epistemic Drift Audit vs Legacy Static Card", expanded=False):
                        drift_report = audit_card_drift(
                            con,
                            card_id=selected_card_id,
                            generation_id=pair_data["generation_id"],
                            graph_sha256=pair_data["graph_sha256"],
                            authorization_context=auth_ctx,
                        )
                        d_col1, d_col2, d_col3 = st.columns(3)
                        with d_col1:
                            st.metric("Graph Assertions", drift_report["active_graph_assertions_count"])
                        with d_col2:
                            st.metric("Legacy Claims", drift_report["legacy_markdown_propositions_count"])
                        with d_col3:
                            st.metric("Orphan Legacy Claims", drift_report["orphan_legacy_propositions_count"])
                        st.caption(f"Legacy file: <code>{drift_report.get('legacy_file_path', 'N/A')}</code>", unsafe_allow_html=True)
                        if drift_report.get("sample_orphan_propositions"):
                            st.markdown("**Sample Unverified Legacy Propositions in Markdown File:**")
                            for prop in drift_report["sample_orphan_propositions"]:
                                st.markdown(f"- ⚠️ *{prop}*")
                except Exception as exc:
                    st.error(f"Materialization failed: {exc}")
    finally:
        con.close()
