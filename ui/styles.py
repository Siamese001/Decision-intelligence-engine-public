"""Executive Glassmorphism & High-Contrast Dark UI Styling for Streamlit Cockpit."""
from __future__ import annotations

EXECUTIVE_CSS = """
<style>
.stApp {
    background: linear-gradient(135deg, #0a0e17 0%, #111827 50%, #0f172a 100%);
    color: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

.metric-card {
    background: rgba(30, 41, 59, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    padding: 18px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
    backdrop-filter: blur(10px);
}

.client-safe-badge {
    background: #065f46;
    color: #6ee7b7;
    padding: 4px 12px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 0.85rem;
    display: inline-block;
    border: 1px solid #10b981;
}

.advisory-badge {
    background: #312e81;
    color: #a5b4fc;
    padding: 4px 12px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 0.85rem;
    display: inline-block;
    border: 1px solid #6366f1;
}

.quarantine-badge {
    background: #78350f;
    color: #fde68a;
    padding: 4px 10px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 0.8rem;
    display: inline-block;
    border: 1px solid #f59e0b;
}

.stealth-active-banner {
    background: linear-gradient(90deg, rgba(6, 78, 59, 0.9), rgba(15, 23, 42, 0.9));
    border: 1px solid #059669;
    border-radius: 8px;
    padding: 12px 20px;
    margin-bottom: 20px;
}

.advisory-active-banner {
    background: linear-gradient(90deg, rgba(49, 46, 129, 0.9), rgba(15, 23, 42, 0.9));
    border: 1px solid #6366f1;
    border-radius: 8px;
    padding: 12px 20px;
    margin-bottom: 20px;
}

.fast-path-box {
    background: rgba(15, 23, 42, 0.8);
    border: 1px dashed #38bdf8;
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 16px;
}

.advisory-card {
    background: rgba(17, 24, 39, 0.85);
    border-left: 5px solid #6366f1;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    border-right: 1px solid rgba(255, 255, 255, 0.08);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 18px;
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.4);
}

/* Executive Glassmorphic Progress Bars */
.stProgress > div > div > div > div {
    background: linear-gradient(90deg, #10b981 0%, #38bdf8 50%, #6366f1 100%) !important;
    border-radius: 8px !important;
    box-shadow: 0 0 14px rgba(56, 189, 248, 0.45);
}
.stProgress > div > div > div {
    background: rgba(15, 23, 42, 0.85) !important;
    border-radius: 8px !important;
    border: 1px solid rgba(255, 255, 255, 0.12);
}
.stProgress [data-testid="stMarkdownContainer"] p {
    font-weight: 600 !important;
    color: #e2e8f0 !important;
    letter-spacing: 0.02em;
}
.run-card {
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid rgba(56, 189, 248, 0.25);
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 16px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
}
.gate-badge-pass {
    background: #065f46;
    color: #a7f3d0;
    padding: 3px 10px;
    border-radius: 12px;
    font-weight: 700;
    font-size: 0.8rem;
    border: 1px solid #10b981;
}
</style>
"""


def apply_executive_styles(st_module) -> None:
    """Applies the custom executive glassmorphic stylesheet to the active Streamlit app."""
    st_module.markdown(EXECUTIVE_CSS, unsafe_allow_html=True)
