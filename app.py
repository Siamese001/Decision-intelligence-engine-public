"""
Executive Decision Intelligence Engine & Strategic Advisory Cockpit.
Dual-Artery Enterprise Hybrid Architecture (Apple MacBook Pro M5 Pro, 48GB RAM).

Namespaces:
- CLIENT_SAFE: Client-facing compliance, architecture, grid dependencies, and deliverables.
- INTERNAL_ADVISORY: Confidential strategic intelligence (Stakeholder Dynamics, Decision Influence, Adoption Risk, Blockers).
"""
from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from pipeline_c_retrieval import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    CopilotFusionEngine,
    PolicyGatedRetrievalEngine,
)
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    EvidenceRecord,
    InternalAdvisoryNode,
)
from ui.styles import apply_executive_styles
from ui.views import (
    render_canonical_facts_view,
    render_card_views_tab,
    render_internal_advisory_view,
    render_pipeline_runs_view,
    render_quarantine_view,
    render_relational_graph_view,
)

# ─────────────────────────────────────────────────────────────────────────────
# Streamlit Page Configuration & Styles
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Decision Intelligence Engine | Enterprise Copilot",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_executive_styles(st)

# ─────────────────────────────────────────────────────────────────────────────
# Database & Engine Initialization
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_engine_instances():
    storage_path = Path("storage_data").resolve()
    db_mgr = DatabaseManager(base_dir=storage_path)

    # Seed demo items if database is empty
    existing_canonical = db_mgr.query_canonical_nodes(include_quarantined=True)
    if not existing_canonical:
        ev1 = EvidenceRecord(
            evidence_id="ev_demo_01",
            meeting_id="meet_nee_01",
            speaker="Amit Ayer",
            text="Decoupled SQLite WAL architecture with compute fork before extraction to guarantee zero biometric leakage.",
            start_timestamp=10.0,
            end_timestamp=15.5,
            is_final=True,
        )
        ev2 = EvidenceRecord(
            evidence_id="ev_demo_02",
            meeting_id="meet_nee_01",
            speaker="Lead PM",
            text="Please schedule calendar invite for follow-up sprint planning tomorrow.",
            start_timestamp=22.0,
            end_timestamp=25.0,
            is_final=True,
        )
        db_mgr.append_evidence(ev1)
        db_mgr.append_evidence(ev2)

        c1 = CanonicalGraphNode(
            node_id="c_node_wal_arch",
            evidence_id="ev_demo_01",
            type="ARCHITECTURAL_DECISION",
            name="Dual-Artery SQLite WAL Decoupling",
            properties={"tier": "P0", "context": ev1.text},
            strategic_value=9.5,
            tacticality=1.0,
            confidence=0.98,
            is_quarantined=False,
        )
        c2 = CanonicalGraphNode(
            node_id="c_node_sched_todo",
            evidence_id="ev_demo_02",
            type="OPERATIONAL_TODO",
            name="Schedule Sprint Planning Calendar Invite",
            properties={"assignee": "Lead PM", "context": ev2.text},
            strategic_value=1.0,
            tacticality=9.0,
            confidence=0.90,
            is_quarantined=True,
            quarantine_reason="TACTICAL_SUPPRESSION: High tacticality with low strategic value",
        )
        db_mgr.upsert_canonical_node(c1)
        db_mgr.upsert_canonical_node(c2)

        adv1 = InternalAdvisoryNode(
            node_id="adv_node_adoption",
            evidence_id="ev_demo_01",
            type="ADOPTION_RISK",
            name="Client Migration Hesitancy on Local Metal",
            category=AdvisoryCategory.ADOPTION_RISK,
            properties={"signal": "Infrastructure leads prefer AWS cloud over local M5 Pro runtimes."},
            strategic_value=8.8,
            confidence=0.95,
            stealth_score=0.9,
            namespace=INTERNAL_ADVISORY_NAMESPACE,
        )
        adv2 = InternalAdvisoryNode(
            node_id="adv_node_influence",
            evidence_id="ev_demo_01",
            type="DECISION_INFLUENCE",
            name="CTO Architecture Steering Path",
            category=AdvisoryCategory.DECISION_INFLUENCE,
            properties={"signal": "CTO sponsors zero data retention policy for generative engines."},
            strategic_value=9.2,
            confidence=0.96,
            stealth_score=1.0,
            namespace=INTERNAL_ADVISORY_NAMESPACE,
        )
        db_mgr.upsert_internal_advisory_node(adv1)
        db_mgr.upsert_internal_advisory_node(adv2)

    retrieval_engine = PolicyGatedRetrievalEngine(db_manager=db_mgr)
    fusion_engine = CopilotFusionEngine(retrieval_engine=retrieval_engine)
    return db_mgr, retrieval_engine, fusion_engine


db_manager, retrieval_engine, fusion_engine = get_engine_instances()

# ─────────────────────────────────────────────────────────────────────────────
# Session State & Sidebar Controls
# ─────────────────────────────────────────────────────────────────────────────

if "STEALTH_MODE" not in st.session_state:
    st.session_state["STEALTH_MODE"] = True

if "live_audio_stream" not in st.session_state:
    st.session_state["live_audio_stream"] = [
        {
            "speaker": "Amit Ayer",
            "text": "The local STT engine on M5 Pro Apple Metal is transcribing live at sub-50ms latency.",
            "timestamp": "14:42:01",
            "is_ephemeral": True,
        },
        {
            "speaker": "Executive Sponsor",
            "text": "Confirming we require zero cloud retention for our proprietary grid topologies.",
            "timestamp": "14:42:15",
            "is_ephemeral": True,
        }
    ]

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/lightning-bolt.png", width=64)
    st.title("System Control")
    st.caption("Dual-Artery Hybrid Architecture v5.8")
    st.markdown("---")

    stealth_toggle = st.toggle(
        "🛡️ Client-Safe Stealth Mode",
        value=st.session_state["STEALTH_MODE"],
        help=(
            "When TRUE (default), all retrieval and UI views are strictly restricted to CLIENT_SAFE. "
            "Turn OFF only in private executive sessions to unmask INTERNAL_ADVISORY intelligence."
        ),
    )
    st.session_state["STEALTH_MODE"] = stealth_toggle

    st.markdown("---")
    st.subheader("Hardware & Security Telemetry")
    st.markdown("- **Host**: Apple MacBook Pro 16\" (M5 Pro)")
    st.markdown("- **Memory**: 48GB Unified RAM (Metal Accelerated)")
    st.markdown("- **Local STT**: `mlx-whisper` (large-v3-turbo)")
    st.markdown("- **Local Advisory LLM**: `mlx-lm` (8B Instruct)")
    st.markdown("- **Shadow Graph**: SQLCipher AES-256 + Keychain")

    st.markdown("---")
    if st.session_state["STEALTH_MODE"]:
        st.success(f"🔒 Active Namespace: `{CLIENT_SAFE_NAMESPACE}`")
        st.info("Screen Share Safe: Zero internal advisory or political dynamics loaded into memory.")
    else:
        st.warning(f"⚡ Unmasked: `{INTERNAL_ADVISORY_NAMESPACE}`")
        st.error("⚠️ CONFIDENTIAL: Do not screen share with client or external delivery teams.")

# ─────────────────────────────────────────────────────────────────────────────
# Main View: Fast Path Banner & Status
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    """
    <div class="fast-path-box">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <strong style="color: #38bdf8;">🎙️ LIVE AMBIENT FAST PATH (UNTRUSTED &bull; EPHEMERAL)</strong>
            <span style="color: #94a3b8; font-size: 0.8rem;">Law 3: Read-Only &bull; Non-Canonical</span>
        </div>
    """,
    unsafe_allow_html=True,
)
for event in st.session_state["live_audio_stream"][-2:]:
    st.markdown(
        f"<small style='color: #cbd5e1;'>[{event['timestamp']}] <strong>{event['speaker']}</strong>: &ldquo;{event['text']}&rdquo;</small><br/>",
        unsafe_allow_html=True,
    )
st.markdown("</div>", unsafe_allow_html=True)

if st.session_state["STEALTH_MODE"]:
    st.markdown(
        """
        <div class="stealth-active-banner">
            <h3 style="margin: 0; color: #34d399;">🛡️ STEALTH MODE ACTIVE &mdash; CLIENT SAFE</h3>
            <p style="margin: 4px 0 0 0; color: #a7f3d0; font-size: 0.95rem;">
                Pipeline C Retrieval restricted strictly to <code>NAMESPACE: CLIENT_SAFE</code>.
                Approved for executive screen sharing with Apex Global Enterprises (NEE) and delivery stakeholders.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.title("Apex Global Enterprises Decision Intelligence Copilot")
    st.markdown("**Enterprise Architecture, Grid Dependencies & Governed Compliance**")
else:
    st.markdown(
        """
        <div class="advisory-active-banner">
            <h3 style="margin: 0; color: #a5b4fc;">⚡ INTERNAL ADVISORY MODE &mdash; STRATEGIC COCKPIT</h3>
            <p style="margin: 4px 0 0 0; color: #c7d2fe; font-size: 0.95rem;">
                Confidential Strategic Lens &bull; Stakeholder Dynamics &bull; Decision Influence &bull; Adoption Risk &bull; Blockers
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.title("Strategic Advisory & Account Intelligence Cockpit")
    st.markdown("**Executive Navigation Engine for Program Leadership**")

# ─────────────────────────────────────────────────────────────────────────────
# Policy-Gated Search & Copilot Fusion
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("🔍 Policy-Gated Knowledge Retrieval (Pipeline C)")

search_col, action_col = st.columns([4, 1])
with search_col:
    search_query = st.text_input(
        "Search Knowledge Graph",
        placeholder="e.g., WAL mode, Migration, Compliance, Architecture, Blocker...",
        label_visibility="collapsed",
    )
with action_col:
    run_fusion = st.button("⚡ Synthesize Copilot", use_container_width=True)

fusion_progress_placeholder = st.empty()
if run_fusion:
    pbar = fusion_progress_placeholder.progress(0, text="Initializing Copilot Context Fusion...")
    time.sleep(0.04)
    pbar.progress(25, text="Step 1/4 (25%): Applying auth and visibility policy filters (Law 6)...")
    time.sleep(0.04)
    pbar.progress(60, text="Step 2/4 (60%): Scoring hybrid candidates & cross-referencing nodes...")
    fusion_payload = fusion_engine.fuse_context(
        query=search_query,
        live_fast_path_events=st.session_state["live_audio_stream"],
        stealth_mode=st.session_state["STEALTH_MODE"],
    )
    pbar.progress(90, text="Step 3/4 (90%): Blending ambient stream with canonical facts...")
    time.sleep(0.04)
    pbar.progress(100, text="Step 4/4 (100%): Context fusion synthesis complete!")
    time.sleep(0.08)
    fusion_progress_placeholder.empty()
else:
    fusion_payload = fusion_engine.fuse_context(
        query=search_query,
        live_fast_path_events=st.session_state["live_audio_stream"],
        stealth_mode=st.session_state["STEALTH_MODE"],
    )

with st.expander("🤖 Copilot Executive Fusion Brief", expanded=True):
    st.markdown(fusion_payload.executive_synthesis)

# ─────────────────────────────────────────────────────────────────────────────
# Main Content Tabs Delegated to Modular Views
# ─────────────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🏛️ Governed Canonical Facts",
    "🎯 Internal Advisory Lens" if not st.session_state["STEALTH_MODE"] else "🔒 Advisory Locked",
    "🛡️ Soft-Quarantine Transparency (Law 5)",
    "🌐 Relational Graph & Causal Chains",
    "🃏 Governed Card Views & Facet Explorer",
    "🚀 Pipeline Execution & Long Runs",
])

with tab1:
    render_canonical_facts_view(st, fusion_payload.reconciled_canonical_facts)

with tab2:
    render_internal_advisory_view(st, fusion_payload.reconciled_advisory_facts, st.session_state["STEALTH_MODE"])

with tab3:
    render_quarantine_view(st, db_manager)

with tab4:
    render_relational_graph_view(st, db_manager)

with tab5:
    render_card_views_tab(st)

with tab6:
    render_pipeline_runs_view(st)
