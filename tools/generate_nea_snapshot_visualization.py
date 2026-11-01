#!/usr/bin/env python3
"""
Generate Executive Snapshot Visualization PDF: NEA Q4 Charters & Major Roles.
Produces high-resolution, presentation-grade vector PDF snapshot across 3 landscape pages.
"""

from pathlib import Path
import textwrap
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.backends.backend_pdf import PdfPages

def create_snapshot_pdf(output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Professional executive color palette
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

    with PdfPages(output_path) as pdf:
        
        # =========================================================================
        # PAGE 1: PORTFOLIO CHARTERS & STRATEGIC VERTICALS
        # =========================================================================
        fig = plt.figure(figsize=(11, 8.5), facecolor=LIGHT_BG)
        
        header_ax = fig.add_axes([0.05, 0.86, 0.90, 0.10])
        header_ax.axis('off')
        header_ax.text(0.0, 0.70, "Apex Global Analytics (NEA) — Q4 Portfolio Landscape", 
                       fontsize=18, fontweight='bold', color=NAVY)
        header_ax.text(0.0, 0.20, "Charters, Strategic Verticals & Net Asset Value ($162.1M Total Portfolio)", 
                       fontsize=11, color=SLATE)
        header_ax.text(1.0, 0.70, "EXECUTIVE SNAPSHOT", fontsize=9.5, fontweight='bold', 
                       color=BLUE_ACCENT, ha='right', bbox=dict(boxstyle='round,pad=0.4', facecolor='#EFF6FF', edgecolor='#BFDBFE'))
        header_ax.text(1.0, 0.20, "Source: Q4 Active Planning Store", fontsize=8.5, color=MUTED_TEXT, ha='right')

        # KPI metric cards (4 cards)
        kpi_data = [
            ("90", "Active Workstreams", "Across 8 Core Verticals", BLUE_ACCENT),
            ("404.9", "Requested FTEs", "376 Assignable People", INDIGO_ACCENT),
            ("30.4", "Unfilled FTEs", "95% Currently Staffed", ROSE_ACCENT),
            ("$162.1M", "Total Net Asset Value", "Funded Q4 Delivery Scope", TEAL_ACCENT)
        ]
        
        for idx, (val, label, sub, color) in enumerate(kpi_data):
            ax_kpi = fig.add_axes([0.05 + idx * 0.23, 0.73, 0.21, 0.11])
            ax_kpi.axis('off')
            rect = patches.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.04,rounding_size=0.08",
                                          facecolor=CARD_BG, edgecolor=BORDER_COLOR, linewidth=1)
            ax_kpi.add_patch(rect)
            ax_kpi.text(0.08, 0.65, val, fontsize=17, fontweight='bold', color=color, va='center')
            ax_kpi.text(0.08, 0.35, label, fontsize=9.5, fontweight='bold', color=NAVY, va='center')
            ax_kpi.text(0.08, 0.15, sub, fontsize=7.5, color=MUTED_TEXT, va='center')

        # Chart 1: NAV by Strategic Vertical
        ax_nav = fig.add_axes([0.22, 0.42, 0.38, 0.28])
        verticals = [
            "Enterprise AI & Astra",
            "Infrastructure & Const.",
            "Trading & Energy Mngt",
            "Bus Ops & Services AI"
        ]
        nav_values = [10.5, 27.9, 54.4, 69.3]
        ws_counts = [9, 14, 26, 34]
        colors = [INDIGO_ACCENT, TEAL_ACCENT, BLUE_ACCENT, AMBER_ACCENT]
        
        bars = ax_nav.barh(verticals, nav_values, color=colors, height=0.55, edgecolor='none', zorder=3)
        ax_nav.set_facecolor(CARD_BG)
        ax_nav.grid(axis='x', linestyle='--', alpha=0.5, zorder=0)
        ax_nav.set_title("Est. Net Asset Value ($M) by Strategic Vertical", fontsize=11, fontweight='bold', color=NAVY, pad=10, loc='left')
        ax_nav.set_xlabel("Net Asset Value ($ Millions)", fontsize=9, color=SLATE)
        ax_nav.tick_params(colors=SLATE, labelsize=8.5)
        
        for bar, val, ws in zip(bars, nav_values, ws_counts):
            ax_nav.text(val + 1.2, bar.get_y() + bar.get_height()/2, f"${val:.1f}M ({ws} streams)", 
                        va='center', fontsize=8, fontweight='bold', color=SLATE)
        ax_nav.set_xlim(0, 90)

        # Chart 2: Workstream Status Distribution
        ax_status = fig.add_axes([0.65, 0.42, 0.30, 0.28])
        statuses = ["In Progress", "Proposed", "Needs Revision", "Approved"]
        status_counts = [65, 14, 7, 4]
        status_colors = [TEAL_ACCENT, BLUE_ACCENT, ROSE_ACCENT, INDIGO_ACCENT]
        
        wedges, texts, autotexts = ax_status.pie(
            status_counts, labels=statuses, autopct='%1.0f%%', pctdistance=0.75,
            startangle=140, colors=status_colors,
            wedgeprops=dict(width=0.45, edgecolor=CARD_BG, linewidth=2),
            textprops=dict(fontsize=8, color=NAVY)
        )
        for at in autotexts:
            at.set_fontsize(8.5)
            at.set_weight('bold')
            at.set_color('white')
        ax_status.set_title("Workstream Delivery Status", fontsize=11, fontweight='bold', color=NAVY, pad=10, loc='left')

        # Bottom Table: Core Charters & Major Focus Areas
        ax_tbl = fig.add_axes([0.05, 0.08, 0.90, 0.29])
        ax_tbl.axis('off')
        ax_tbl.text(0.0, 0.95, "Primary Charters & Portfolio Alignment Matrix", fontsize=11, fontweight='bold', color=NAVY)
        
        table_data = [
            ["Strategic Vertical", "Primary Charters & Initiatives", "Key Executive Leads", "Total NAV", "Staffing Gap / Delivery Risk"],
            ["Bus Ops & Services AI", "Purchase Intake ($7.5M), Top Risk, Reg Discovery, AP Auto", "Farrukh Khan / Matt Barrows", "$69.3M", "Dave Sunderman 6x over-allocated; 3 Future Hires"],
            ["Trading & Energy Mngt", "GridComposer ($12M), NextShield, Power Markets, GridFlow", "Stephan Nitsche / Chris Nunalee", "$54.4M", "2 open seats; core trade release critical path"],
            ["Infrastructure & Const.", "DSLOT Alpha ($6M), Construction Staffing ($4.9M), Enablement", "Kevin McWhorter / Chris Nunalee", "$27.9M", "Construction Staffing 38% staffed (1 Dev on $4.9M)"],
            ["Enterprise AI & Astra", "AI Software Factory ($592K), Claude.AI, AI FinOps, Governance", "Anuj Chokshi / Matt Barrows", "$10.5M", "Factory is 75% vacant (3 open seats); Claude at 20%"]
        ]
        
        tbl = ax_tbl.table(cellText=table_data[1:], colLabels=table_data[0],
                           loc='center', cellLoc='left',
                           colWidths=[0.17, 0.35, 0.18, 0.08, 0.22])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(7.5)
        tbl.scale(1, 1.45)
        
        for (row, col), cell in tbl.get_celld().items():
            cell.set_edgecolor(BORDER_COLOR)
            if row == 0:
                cell.set_facecolor("#F1F5F9")
                cell.set_text_props(weight='bold', color=NAVY)
            else:
                cell.set_facecolor(CARD_BG if row % 2 == 1 else "#F8FAFC")
                cell.set_text_props(color=SLATE)
                if col == 3:
                    cell.set_text_props(weight='bold', color=BLUE_ACCENT)
                elif col == 4:
                    cell.set_text_props(color=ROSE_ACCENT if any(k in cell.get_text().get_text() for k in ["over-allocated", "38%", "vacant"]) else SLATE)

        pdf.savefig(fig, dpi=300)
        fig.savefig(output_path.with_name("nea_q4_snapshot_page1.png"), dpi=200)
        plt.close(fig)

        # =========================================================================
        # PAGE 2: MAJOR ROLES, STAFFING COMPOSITION & DEFICITS
        # =========================================================================
        fig2 = plt.figure(figsize=(11, 8.5), facecolor=LIGHT_BG)
        
        h2 = fig2.add_axes([0.05, 0.86, 0.90, 0.10])
        h2.axis('off')
        h2.text(0.0, 0.70, "Major Roles & Staffing Composition", fontsize=18, fontweight='bold', color=NAVY)
        h2.text(0.0, 0.20, "Role Level Distribution, Contractor Reliance & Unfilled FTE Breakdown", fontsize=11, color=SLATE)
        h2.text(1.0, 0.70, "ROLE LANDSCAPE", fontsize=9.5, fontweight='bold', 
                color=INDIGO_ACCENT, ha='right', bbox=dict(boxstyle='round,pad=0.4', facecolor='#EEF2FF', edgecolor='#C7D2FE'))
        h2.text(1.0, 0.20, "Total Assigned: 425 Seats", fontsize=8.5, color=MUTED_TEXT, ha='right')

        # Chart 1: Role Level Breakdown
        ax_roles = fig2.add_axes([0.05, 0.48, 0.43, 0.32])
        role_labels = ["Contractor", "Staff Level", "Senior Level", "Unfilled FTEs", "Principal", "Associate"]
        role_counts = [226, 105, 64, 30.4, 21, 5]
        role_colors = [SLATE, BLUE_ACCENT, TEAL_ACCENT, ROSE_ACCENT, INDIGO_ACCENT, MUTED_TEXT]
        
        bars2 = ax_roles.bar(role_labels, role_counts, color=role_colors, width=0.55, zorder=3)
        ax_roles.set_facecolor(CARD_BG)
        ax_roles.grid(axis='y', linestyle='--', alpha=0.5, zorder=0)
        ax_roles.set_title("Staffing Composition by Role Seniority Tier", fontsize=11, fontweight='bold', color=NAVY, pad=10, loc='left')
        ax_roles.set_ylabel("Headcount Assigned", fontsize=9, color=SLATE)
        ax_roles.tick_params(colors=SLATE, labelsize=8)
        plt.setp(ax_roles.get_xticklabels(), rotation=20, ha='right')
        
        for b, v in zip(bars2, role_counts):
            ax_roles.text(b.get_x() + b.get_width()/2, v + 4, f"{v:.0f}" if isinstance(v, int) else f"{v:.1f}", 
                          ha='center', fontsize=8, fontweight='bold', color=SLATE)
        ax_roles.set_ylim(0, 260)

        # Chart 2: Scrum Master Allocation Load
        ax_sm = fig2.add_axes([0.60, 0.48, 0.35, 0.32])
        sm_names = ["Brett Gavagni", "William Lane", "Jake Wellington", "Ram Rajaraman", "Kaleena McHugo", "William White"]
        sm_streams = [7, 12, 15, 15, 18, 22]
        
        bars3 = ax_sm.barh(sm_names, sm_streams, color=INDIGO_ACCENT, height=0.55, zorder=3)
        ax_sm.set_facecolor(CARD_BG)
        ax_sm.grid(axis='x', linestyle='--', alpha=0.5, zorder=0)
        ax_sm.set_title("Scrum Master / Delivery Lead Load", fontsize=11, fontweight='bold', color=NAVY, pad=10, loc='left')
        ax_sm.set_xlabel("Number of Assigned Workstreams", fontsize=9, color=SLATE)
        ax_sm.tick_params(colors=SLATE, labelsize=8)
        
        for b, v in zip(bars3, sm_streams):
            ax_sm.text(v + 0.4, b.get_y() + b.get_height()/2, f"{v}", 
                       va='center', fontsize=8, fontweight='bold', color=SLATE)
        ax_sm.set_xlim(0, 26)

        # Bottom Section: Future Hire Seats & Open Role Demand
        ax_fh = fig2.add_axes([0.05, 0.08, 0.90, 0.33])
        ax_fh.axis('off')
        ax_fh.text(0.0, 0.95, "Unfilled Capacity Profile: 30.4 Open FTEs (Future Hires #1–#26)", fontsize=11, fontweight='bold', color=NAVY)
        
        fh_table_data = [
            ["Target Functional Domain", "Open Slots Identified", "Target Level", "Primary Role Scope", "Strategic Delivery Impact"],
            ["Infrastructure & Data", "Future Hire #1, #2, #4", "Staff / Senior", "Data Engineer, Cloud Platform Dev", "Accelerates $10.9M Infrastructure & DSLOT Alpha"],
            ["Bus Ops AI Relief", "Future Hire #5, #20, #23", "Staff", "Software Developer, Integration Lead", "Relieves Dave Sunderman over-allocation on $7.5M Intake"],
            ["Trading & GridComposer", "Future Hire #15, #16", "Staff", "Full Stack Developer, Trade Platform", "Unblocks GridComposer Q4 93% staffing bottleneck"],
            ["AI Software Factory", "Future Hire #24, #25, #26", "Staff", "AI Engineer, Prompt/Eval Engineer", "Seeds entire prototype build (3 of 4 open seats)"],
            ["Asset Opti & Conduit", "Future Hire #6, #7, #8, #10", "Staff", "Backend Developer, Conduit Integrator", "Completes PGD Renewables & Conduit PF4 delivery"]
        ]
        
        tbl_fh = ax_fh.table(cellText=fh_table_data[1:], colLabels=fh_table_data[0],
                             loc='center', cellLoc='left',
                             colWidths=[0.17, 0.17, 0.11, 0.26, 0.29])
        tbl_fh.auto_set_font_size(False)
        tbl_fh.set_fontsize(7.5)
        tbl_fh.scale(1, 1.45)
        
        for (row, col), cell in tbl_fh.get_celld().items():
            cell.set_edgecolor(BORDER_COLOR)
            if row == 0:
                cell.set_facecolor("#F1F5F9")
                cell.set_text_props(weight='bold', color=NAVY)
            else:
                cell.set_facecolor(CARD_BG if row % 2 == 1 else "#F8FAFC")
                cell.set_text_props(color=SLATE)
                if col == 1:
                    cell.set_text_props(weight='bold', color=ROSE_ACCENT)

        pdf.savefig(fig2, dpi=300)
        fig2.savefig(output_path.with_name("nea_q4_snapshot_page2.png"), dpi=200)
        plt.close(fig2)

        # =========================================================================
        # PAGE 3: ACTIONABLE PLACEMENT SNAPSHOT — THE FOUR POD PLAYS
        # =========================================================================
        fig3 = plt.figure(figsize=(11, 8.5), facecolor=LIGHT_BG)
        
        h3 = fig3.add_axes([0.05, 0.86, 0.90, 0.10])
        h3.axis('off')
        h3.text(0.0, 0.70, "Actionable Placement Snapshot: Four High-Value Pod Plays", fontsize=16.5, fontweight='bold', color=NAVY)
        h3.text(0.0, 0.20, "Direct Role-to-Gap Placement for PartnerAdvisory across $33.0M Net Asset Value", fontsize=10.5, color=SLATE)
        h3.text(1.0, 0.70, "POD PLACEMENTS", fontsize=9.5, fontweight='bold', 
                color=ROSE_ACCENT, ha='right', bbox=dict(boxstyle='round,pad=0.4', facecolor='#FFF1F2', edgecolor='#FECDD3'))
        h3.text(1.0, 0.20, "30.4 FTE Immediate Capacity Arbitrage", fontsize=8.5, color=MUTED_TEXT, ha='right')

        # 4 Pod Cards in a 2x2 Grid with generous vertical breathing room
        pods = [
            {
                "rect": [0.05, 0.47, 0.43, 0.37],
                "title": "POD 1: Infrastructure & Const.",
                "nav": "$10.9M NAV",
                "leads": "Kevin McWhorter / Chris Nunalee",
                "gap_lines": [
                    "Construction Staffing ($4.9M) at 38% staffed (1 Dev).",
                    "DSLOT Alpha ($6.0M) at 95% staffed with open seats."
                ],
                "roles": ["1 Product Manager", "1 Staff Applied Scientist", "2–3 Data / Software Engineers"],
                "color": BLUE_ACCENT,
                "accent_bg": "#EFF6FF"
            },
            {
                "rect": [0.52, 0.47, 0.43, 0.37],
                "title": "POD 2: Trade Floor / GridComposer",
                "nav": "$12.0M NAV",
                "leads": "Stephan Nitsche / Chris Nunalee",
                "gap_lines": [
                    "NEA's flagship Q4 trading stream is 93% staffed.",
                    "2 open critical seats directly stalling trade release."
                ],
                "roles": ["1 Product Manager", "1 Staff Software Developer (Future Hire #15)"],
                "color": TEAL_ACCENT,
                "accent_bg": "#F0FDFA"
            },
            {
                "rect": [0.05, 0.07, 0.43, 0.37],
                "title": "POD 3: Bus Ops AI Relief",
                "nav": "$9.6M NAV",
                "leads": "Farrukh Khan / Matt Barrows",
                "gap_lines": [
                    "Lead Dave Sunderman 6x over-allocated across 6 streams.",
                    "Critical bottlenecks on $7.5M Purchase Intake & AP Auto."
                ],
                "roles": ["1 Senior Technical / Delivery Lead", "3 Staff Software Developers (FH #5, #20, #23)"],
                "color": AMBER_ACCENT,
                "accent_bg": "#FFFBEB"
            },
            {
                "rect": [0.52, 0.07, 0.43, 0.37],
                "title": "POD 4: AI Software Factory",
                "nav": "$592K Prototype",
                "leads": "Anuj Chokshi / Matt Barrows",
                "gap_lines": [
                    "3 of 4 seats vacant on new prototype (FH #24, #25, #26).",
                    "Immediate greenfield opportunity to establish execution squad."
                ],
                "roles": ["3 Staff Software / AI Engineers (Prompt & Systems)", "Turnkey Prototype Delivery Squad"],
                "color": INDIGO_ACCENT,
                "accent_bg": "#EEF2FF"
            }
        ]

        for p in pods:
            ax_p = fig3.add_axes(p["rect"])
            ax_p.axis('off')
            bg = patches.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.03,rounding_size=0.05",
                                        facecolor=CARD_BG, edgecolor=BORDER_COLOR, linewidth=1.2)
            ax_p.add_patch(bg)
            
            # Header pill
            pill = patches.FancyBboxPatch((0.04, 0.83), 0.92, 0.13, boxstyle="round,pad=0.02,rounding_size=0.04",
                                          facecolor=p["accent_bg"], edgecolor='none')
            ax_p.add_patch(pill)
            ax_p.text(0.07, 0.895, p["title"], fontsize=9.5, fontweight='bold', color=p["color"], va='center')
            ax_p.text(0.93, 0.895, p["nav"], fontsize=9, fontweight='bold', color=NAVY, ha='right', va='center')
            
            # Leads
            ax_p.text(0.06, 0.75, f"Executive Leads: {p['leads']}", fontsize=8.2, fontweight='bold', color=SLATE)
            
            # Delivery Gap
            ax_p.text(0.06, 0.65, "Delivery Gap:", fontsize=7.8, fontweight='bold', color=ROSE_ACCENT)
            for g_idx, gl in enumerate(p["gap_lines"]):
                ax_p.text(0.06, 0.57 - g_idx * 0.06, f"• {gl}", fontsize=7.5, color=SLATE)
            
            # Roles to Pitch box
            role_box = patches.Rectangle((0.05, 0.05), 0.90, 0.38, facecolor="#F8FAFC", edgecolor=BORDER_COLOR, linewidth=0.8)
            ax_p.add_patch(role_box)
            ax_p.text(0.08, 0.36, "EXACT ROLES TO PITCH:", fontsize=7.8, fontweight='bold', color=NAVY)
            
            for r_idx, r in enumerate(p["roles"]):
                ax_p.text(0.08, 0.27 - r_idx * 0.08, f"• {r}", fontsize=7.5, fontweight='bold', color=p["color"])

        pdf.savefig(fig3, dpi=300)
        fig3.savefig(output_path.with_name("nea_q4_snapshot_page3.png"), dpi=200)
        plt.close(fig3)

    print(f"Successfully generated refined PDF snapshot at: {output_path} ({output_path.stat().st_size:,} bytes)")

if __name__ == "__main__":
    out = Path("reports/visualizations/nea_q4_charters_and_major_roles_snapshot.pdf")
    create_snapshot_pdf(out)
