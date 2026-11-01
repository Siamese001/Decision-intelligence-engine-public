#!/usr/bin/env python3
"""
Generate Detailed Workstream & Role Decomposition PDF.
Multi-page comprehensive visual reference breaking down all 90 workstreams, verticals, and roles.
"""

from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.backends.backend_pdf import PdfPages
from collections import defaultdict, Counter

def build_detailed_pdf():
    out_dir = Path("reports/visualizations")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "nea_q4_detailed_workstream_decomposition.pdf"
    
    with open("reports/visualizations/nea_q4_workstreams_decomp.json") as f:
        workstreams = json.load(f)
        
    NAVY = "#0F172A"
    SLATE = "#334155"
    LIGHT_BG = "#F8FAFC"
    CARD_BG = "#FFFFFF"
    BORDER_COLOR = "#CBD5E1"
    BLUE_ACCENT = "#1D4ED8"
    TEAL_ACCENT = "#0F766E"
    AMBER_ACCENT = "#B45309"
    ROSE_ACCENT = "#BE123C"
    INDIGO_ACCENT = "#4338CA"
    MUTED_TEXT = "#64748B"
    
    plt.rcParams.update({
        'font.sans-serif': 'DejaVu Sans',
        'font.family': 'sans-serif',
        'axes.edgecolor': BORDER_COLOR,
        'axes.linewidth': 0.8
    })

    with PdfPages(pdf_path) as pdf:
        
        # -------------------------------------------------------------
        # Page 1: Granular Workstream Breakdown by Vertical (NAV > $1M)
        # -------------------------------------------------------------
        fig1 = plt.figure(figsize=(11, 8.5), facecolor=LIGHT_BG)
        
        h1 = fig1.add_axes([0.05, 0.88, 0.90, 0.08])
        h1.axis('off')
        h1.text(0.0, 0.65, "Detailed Workstream Decomposition: Major NAV Streams (> $1.5M)", 
                fontsize=16, fontweight='bold', color=NAVY)
        h1.text(0.0, 0.15, "Granular Portfolio Breakdown of Top-Tier Funded Initiatives and Leadership", 
                fontsize=10, color=SLATE)
        h1.text(1.0, 0.65, "PAGE 1 / 2", fontsize=9, fontweight='bold', color=MUTED_TEXT, ha='right')

        # Filter top NAV workstreams
        top_streams = sorted([w for w in workstreams if w['nav_k'] >= 1800], key=lambda x: -x['nav_k'])[:15]
        
        # Table of Top Streams
        ax_t1 = fig1.add_axes([0.05, 0.10, 0.90, 0.74])
        ax_t1.axis('off')
        
        headers = ["Workstream Name", "Strategic Vertical", "PM Lead", "SM / Delivery", "NAV ($K)", "Staffing %", "Status"]
        rows = []
        for w in top_streams:
            rows.append([
                w['name'][:34],
                w['vertical'][:22],
                w['pm'][:18] if w['pm'] else "Unassigned",
                w['sm'][:16] if w['sm'] else "Unassigned",
                f"${w['nav_k']:,}K",
                f"{w['progress_pct']}%",
                w['status']
            ])
            
        t1 = ax_t1.table(cellText=rows, colLabels=headers, loc='center', cellLoc='left',
                         colWidths=[0.30, 0.20, 0.15, 0.14, 0.08, 0.06, 0.09])
        t1.auto_set_font_size(False)
        t1.set_fontsize(7.5)
        t1.scale(1, 1.45)
        
        for (r, c), cell in t1.get_celld().items():
            cell.set_edgecolor(BORDER_COLOR)
            if r == 0:
                cell.set_facecolor("#F1F5F9")
                cell.set_text_props(weight='bold', color=NAVY)
            else:
                cell.set_facecolor(CARD_BG if r % 2 == 1 else "#F8FAFC")
                cell.set_text_props(color=SLATE)
                if c == 4:
                    cell.set_text_props(weight='bold', color=BLUE_ACCENT)
                elif c == 5 and int(rows[r-1][5].replace('%', '')) < 85:
                    cell.set_text_props(weight='bold', color=ROSE_ACCENT)

        pdf.savefig(fig1, dpi=300)
        plt.close(fig1)

        # -------------------------------------------------------------
        # Page 2: Unfilled Future Hire Deficit Decomposition (All 25 Slots)
        # -------------------------------------------------------------
        fig2 = plt.figure(figsize=(11, 8.5), facecolor=LIGHT_BG)
        
        h2 = fig2.add_axes([0.05, 0.88, 0.90, 0.08])
        h2.axis('off')
        h2.text(0.0, 0.65, "Granular Capacity Deficit: Complete Future Hire Roster (#1–#26)", 
                fontsize=16, fontweight='bold', color=NAVY)
        h2.text(0.0, 0.15, "Full Decomposition of 30.4 Unfilled FTEs Across Strategic Workstreams", 
                fontsize=10, color=SLATE)
        h2.text(1.0, 0.65, "PAGE 2 / 2", fontsize=9, fontweight='bold', color=MUTED_TEXT, ha='right')

        fh_data = [
            ["Slot", "Workstream Association", "Target Role Scope", "Level", "Strategic Business Impact"],
            ["FH #1", "DSLOT Alpha / EMRE", "Staff Applied Scientist / Dev", "Staff", "Detail solar layout optimization & engineering"],
            ["FH #2", "Generation Entitlement", "Staff Software Developer", "Staff", "Automates gen entitlement pipeline"],
            ["FH #3", "SWARM - Validation & Pilot", "Staff Systems Engineer", "Staff", "Fleet telemetry and edge validation"],
            ["FH #4", "Regulatory Lifecycle (Farrukh)", "Senior / Staff Developer", "Staff", "Relieves Dave Sunderman on regulatory filings"],
            ["FH #5", "DSLOT Alpha / EMRE", "Staff Data Engineer", "Staff", "High-throughput solar CAD telemetry pipelines"],
            ["FH #6", "Gas Eng Preliminary Permitting", "Staff Full Stack Dev", "Staff", "Accelerates Apex Global Services permitting tooling"],
            ["FH #7", "Gas Eng Preliminary Permitting", "Staff Backend Developer", "Staff", "Engineering CAD conversion automation"],
            ["FH #8", "Conduit Q4: Scheduling (PF2)", "Staff Platform Developer", "Staff", "Cross-system asset schedule synchronization"],
            ["FH #9", "GridComposer Q4 (Stephan N.)", "Staff Software Developer", "Staff", "NEA core algorithmic trading platform scaling"],
            ["FH #10", "Contractor Control Tower", "Staff Full Stack Dev", "Staff", "Vendor analytics and contractor spend control"],
            ["FH #11", "PGD Central Maint. Conduit", "Staff Systems Integrator", "Staff", "Connects turbine maintenance to Maximo"],
            ["FH #12", "DER Management / DocIQ", "Staff AI / ML Engineer", "Staff", "Document extraction & DER telemetry"],
            ["FH #13", "Navigator / NEOS Phase 2", "Staff Backend Developer", "Staff", "Project advancement and portfolio approvals"],
            ["FH #15", "GridComposer Q4 (Stephan N.)", "Staff Software Developer", "Staff", "Trade execution queue & realtime pricing"],
            ["FH #16", "Conduit Q4: Scheduling (PF2)", "Staff Integration Engineer", "Staff", "Scheduling pipeline resilience"],
            ["FH #17", "AI Governance / Policy", "Staff Prompt / AI Engineer", "Staff", "Enterprise AI compliance and model safety"],
            ["FH #18", "Conduit Q4: Scheduling (PF2)", "Staff Data Platform Dev", "Staff", "Power flow scheduling data architecture"],
            ["FH #19", "Purchase Intake Agent Q4", "Staff Full Stack Lead", "Staff", "Direct relief for Dave Sunderman on $7.5M Intake"],
            ["FH #20", "Regulatory Lifecycle", "Staff Software Developer", "Staff", "Automated compliance documentation ingestion"],
            ["FH #21", "PGD Central Maint. Conduit", "Staff Backend Integrator", "Staff", "Field technician work order syncing"],
            ["FH #22", "Process Atlas / PL Expansion", "Staff Software Developer", "Staff", "Corporate process intelligence tooling"],
            ["FH #23", "Contractor Control Tower", "Staff Analytics Developer", "Staff", "Contractor onboarding and KPI telemetry"],
            ["FH #24", "AI Software Factory (Anuj C.)", "Staff AI / ML Engineer", "Staff", "Core LLM tool chaining and factory scaffolding"],
            ["FH #25", "AI Software Factory (Anuj C.)", "Staff Full Stack AI Dev", "Staff", "Enterprise partner prototype delivery"],
            ["FH #26", "AI Software Factory (Anuj C.)", "Staff Systems Engineer", "Staff", "Automated eval harnesses and production deployment"]
        ]
        
        ax_t2 = fig2.add_axes([0.05, 0.06, 0.90, 0.78])
        ax_t2.axis('off')
        
        t2 = ax_t2.table(cellText=fh_data[1:], colLabels=fh_data[0], loc='center', cellLoc='left',
                         colWidths=[0.08, 0.28, 0.24, 0.08, 0.32])
        t2.auto_set_font_size(False)
        t2.set_fontsize(6.8)
        t2.scale(1, 1.35)
        
        for (r, c), cell in t2.get_celld().items():
            cell.set_edgecolor(BORDER_COLOR)
            if r == 0:
                cell.set_facecolor("#F1F5F9")
                cell.set_text_props(weight='bold', color=NAVY)
            else:
                cell.set_facecolor(CARD_BG if r % 2 == 1 else "#F8FAFC")
                cell.set_text_props(color=SLATE)
                if c == 0:
                    cell.set_text_props(weight='bold', color=ROSE_ACCENT)
                elif c == 3:
                    cell.set_text_props(color=INDIGO_ACCENT)

        pdf.savefig(fig2, dpi=300)
        plt.close(fig2)

    print(f"Generated Detailed Decomposition PDF: {pdf_path} ({pdf_path.stat().st_size:,} bytes)")

if __name__ == "__main__":
    build_detailed_pdf()
