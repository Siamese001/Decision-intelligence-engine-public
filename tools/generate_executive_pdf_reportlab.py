#!/usr/bin/env python3
"""
Generate Ultra-High-Contrast Publication-Grade Executive PDF: NEA Q4 Charters & Major Roles.
Engineered for maximum legibility and accessibility:
- 100% Pure Black text on Pure White background
- Solid crisp black borders and structured grayscale hierarchy
- Zero reliance on low-contrast pastel or colored text
- Larger, bolder typography
- Strictly 3 landscape pages
"""

from pathlib import Path
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

# Ultra-High-Contrast Palette
BLACK = colors.HexColor("#000000")
DARK_CHARCOAL = colors.HexColor("#111827")
WHITE = colors.HexColor("#FFFFFF")
LIGHT_GRAY = colors.HexColor("#F3F4F6")
MID_GRAY = colors.HexColor("#E5E7EB")
BORDER_BLACK = colors.HexColor("#000000")
BORDER_DARK = colors.HexColor("#374151")

class HighContrastNumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_footer(num_pages)
            super().showPage()
        super().save()

    def draw_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 8.5)
        self.setFillColor(BLACK)
        self.drawString(36, 20, "Apex Global Analytics (NEA) — Q4 2026 Strategic Planning Portfolio Snapshot | Confidential Internal Advisory")
        self.drawRightString(792 - 36, 20, f"Page {self._pageNumber} of {page_count}")
        self.setStrokeColor(BLACK)
        self.setLineWidth(1.0)
        self.line(36, 30, 792 - 36, 30)
        self.restoreState()

def build_pdf():
    out_dir = Path("reports/visualizations")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "nea_q4_charters_and_major_roles_snapshot.pdf"
    
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=32,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=BLACK
    )
    subtitle_style = ParagraphStyle(
        'DocSub',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=13,
        textColor=BLACK
    )
    badge_style = ParagraphStyle(
        'Badge',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=BLACK,
        alignment=2
    )
    section_h2 = ParagraphStyle(
        'SectionH2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=15,
        textColor=BLACK,
        spaceAfter=5
    )
    th_white = ParagraphStyle(
        'THWhite',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=WHITE
    )
    th_black = ParagraphStyle(
        'THBlack',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=BLACK
    )
    td_style = ParagraphStyle(
        'TD',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=BLACK
    )
    td_bold = ParagraphStyle(
        'TDBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=11,
        textColor=BLACK
    )
    kpi_num = ParagraphStyle(
        'KpiNum',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=20,
        textColor=BLACK
    )
    kpi_label = ParagraphStyle(
        'KpiLbl',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=BLACK
    )
    kpi_sub = ParagraphStyle(
        'KpiSub',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9.5,
        textColor=DARK_CHARCOAL
    )

    story = []

    # =========================================================================
    # PAGE 1: PORTFOLIO CHARTERS & STRATEGIC VERTICALS (ULTRA-HIGH CONTRAST)
    # =========================================================================
    header_table = Table(
        [
            [
                Paragraph("Apex Global Analytics (NEA) — Q4 Portfolio Landscape", title_style),
                Paragraph("EXECUTIVE SNAPSHOT<br/><font size='7.5'>Source: Q4 Active Planning Store</font>", badge_style)
            ],
            [
                Paragraph("Charters, Strategic Verticals & Net Asset Value ($162.1M Total Active Scope)", subtitle_style),
                ""
            ]
        ],
        colWidths=[520, 200]
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8))

    # KPI Stat Cards Table (Solid Black Borders, High Contrast)
    kpi_cols = [
        [Paragraph("90 Active", kpi_num), Paragraph("Total Workstreams", kpi_label), Paragraph("Across 8 Core Verticals", kpi_sub)],
        [Paragraph("$162.1M", kpi_num), Paragraph("Total Net Asset Value", kpi_label), Paragraph("Funded Q4 Delivery Scope", kpi_sub)],
        [Paragraph("404.9", kpi_num), Paragraph("Requested FTEs", kpi_label), Paragraph("376 Assignable People", kpi_sub)],
        [Paragraph("30.4 FTEs", kpi_num), Paragraph("Unfilled Capacity Gap", kpi_label), Paragraph("95% Staffed (Future Hires #1–26)", kpi_sub)],
    ]
    kpi_cells = []
    for col in kpi_cols:
        t = Table([[col[0]], [col[1]], [col[2]]], colWidths=[172])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), WHITE),
            ('BOX', (0,0), (-1,-1), 1.2, BORDER_BLACK),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ]))
        kpi_cells.append(t)
        
    kpi_bar = Table([kpi_cells], colWidths=[180, 180, 180, 180])
    kpi_bar.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(kpi_bar)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Strategic Verticals & Charter Alignment Matrix", section_h2))
    
    verticals_matrix = [
        [
            Paragraph("Strategic Vertical", th_white),
            Paragraph("Primary Charters & High-NAV Products", th_white),
            Paragraph("Executive Leadership", th_white),
            Paragraph("Total NAV", th_white),
            Paragraph("Streams", th_white),
            Paragraph("Staffing Reality & Key Delivery Risk", th_white)
        ],
        [
            Paragraph("<b>Bus Ops & Services AI</b>", td_bold),
            Paragraph("Purchase Intake Agent ($7.5M), Regulatory Lifecycle ($1.9M), Top Risk Dashboard ($1.2M), AP Automation, Contractor Control Tower ($1.9M)", td_style),
            Paragraph("Farrukh Khan (Bus Ops)<br/>Matt Barrows (Enterprise AI)", td_style),
            Paragraph("<b>$69.3M</b>", td_bold),
            Paragraph("34", td_style),
            Paragraph("<b>CRITICAL BOTTLENECK:</b> Dave Sunderman 6x over-allocated across $9.6M NAV; 3 unfilled Future Hire slots stalling Intake expansion.", td_style)
        ],
        [
            Paragraph("<b>Trading & Energy Mngt</b>", td_bold),
            Paragraph("Trading Plan / GridComposer Q4 ($12.0M), NextShield ($3.4M), Power Markets, Optos MarketEdge ($5.1M), GridFlow", td_style),
            Paragraph("Stephan Nitsche (Energy Mngt)<br/>Chris Nunalee (NEA Ops)", td_style),
            Paragraph("<b>$54.4M</b>", td_bold),
            Paragraph("26", td_style),
            Paragraph("<b>FLAGSHIP REVENUE STREAM:</b> GridComposer is 93% staffed with 2 open developer seats (FH #15) stalling execution queue release.", td_style)
        ],
        [
            Paragraph("<b>Infrastructure & Const.</b>", td_bold),
            Paragraph("Detail Solar Layout Optimization Tool (DSLOT) Alpha ($6.0M), Construction Staffing & Data Enablement ($4.9M), Panel Allocation ($5.1M)", td_style),
            Paragraph("Kevin McWhorter (VP Infra)<br/>Chris Nunalee (NEA Ops)", td_style),
            Paragraph("<b>$27.9M</b>", td_bold),
            Paragraph("14", td_style),
            Paragraph("<b>CRITICAL DELIVERY FAILURE:</b> Construction Staffing is only 38% staffed (1 Dev on $4.9M scope). Directly impacts PartnerAdvisory's $6.4M pursuit.", td_style)
        ],
        [
            Paragraph("<b>Enterprise AI & Astra</b>", td_bold),
            Paragraph("AI Software Factory — Partner Prototype ($592K), Anthropic Claude.AI Tooling ($2.4M), AI FinOps ($951K), AI Governance ($527K)", td_style),
            Paragraph("Anuj Chokshi (Enterprise AI)<br/>Matt Barrows (Enterprise AI)", td_style),
            Paragraph("<b>$10.5M</b>", td_bold),
            Paragraph("9", td_style),
            Paragraph("<b>VACANT PROTOTYPE SQUAD:</b> AI Software Factory has 3 of 4 seats vacant (FH #24, #25, #26). Claude tooling is only 20% staffed.", td_style)
        ]
    ]

    vert_table = Table(verticals_matrix, colWidths=[110, 245, 115, 60, 45, 145])
    vert_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), BLACK),
        ('BOX', (0,0), (-1,-1), 1.2, BORDER_BLACK),
        ('INNERGRID', (0,0), (-1,-1), 0.6, BORDER_BLACK),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(vert_table)
    story.append(Spacer(1, 8))

    status_summary = Table(
        [
            [
                Paragraph("<b>Portfolio Delivery Status Breakdown:</b> 72% In Progress (65 streams) | 16% Proposed (14 streams) | 8% Needs Revision (7 streams) | 4% Approved (4 streams)", td_bold)
            ]
        ],
        colWidths=[720]
    )
    status_summary.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_GRAY),
        ('BOX', (0,0), (-1,-1), 1.0, BORDER_BLACK),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(status_summary)

    # =========================================================================
    # PAGE 2: MAJOR ROLES, STAFFING COMPOSITION & FUTURE HIRE DEFICITS
    # =========================================================================
    story.append(PageBreak())

    header_table2 = Table(
        [
            [
                Paragraph("Major Roles & Staffing Composition", title_style),
                Paragraph("ROLE LANDSCAPE<br/><font size='7.5'>425 Total Assigned Seats</font>", badge_style)
            ],
            [
                Paragraph("Bench Seniority Distribution, Contractor Reliance, Delivery Bottlenecks & 30.4 Open FTEs", subtitle_style),
                ""
            ]
        ],
        colWidths=[520, 200]
    )
    header_table2.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table2)
    story.append(Spacer(1, 6))

    sen_data = [
        [Paragraph("Role Seniority Tier", th_white), Paragraph("Count", th_white), Paragraph("% Bench", th_white), Paragraph("Strategic Implications", th_white)],
        [Paragraph("<b>Contractors</b>", td_style), Paragraph("226", td_bold), Paragraph("53.2%", td_style), Paragraph("Extremely high external contractor reliance (53% of total bench); continuity risk.", td_style)],
        [Paragraph("<b>Staff Software Developers</b>", td_style), Paragraph("105", td_bold), Paragraph("24.7%", td_style), Paragraph("Core technical spine; heavily loaded across active streams.", td_style)],
        [Paragraph("<b>Senior Developers</b>", td_style), Paragraph("64", td_bold), Paragraph("15.1%", td_style), Paragraph("Feature lead capacity; highly constrained on complex flows.", td_style)],
        [Paragraph("<b>Principal Applied Scientists</b>", td_style), Paragraph("21", td_bold), Paragraph("4.9%", td_style), Paragraph("Concentrated in DSLOT, Trading, and Panel Allocation.", td_style)],
        [Paragraph("<b>Unfilled Capacity Gap</b>", td_bold), Paragraph("<b>30.4</b>", td_bold), Paragraph("<b>7.2%</b>", td_bold), Paragraph("<b>25 Future Hire slots (FH #1–#26) all scoped at Staff level.</b>", td_bold)]
    ]
    sen_tbl = Table(sen_data, colWidths=[105, 38, 42, 165])
    sen_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), BLACK),
        ('BOX', (0,0), (-1,-1), 1.0, BORDER_BLACK),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_BLACK),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
    ]))

    sm_data = [
        [Paragraph("Delivery Lead / Scrum Master", th_white), Paragraph("Workstreams", th_white), Paragraph("Delivery Overload Status", th_white)],
        [Paragraph("<b>William White</b>", td_bold), Paragraph("<b>22 streams</b>", td_bold), Paragraph("Severe bottleneck: Infrastructure, Panel Allocation, Tower", td_style)],
        [Paragraph("<b>Kaleena McHugo</b>", td_bold), Paragraph("<b>18 streams</b>", td_bold), Paragraph("High load: TechSales, Forge Platform, Permitting", td_style)],
        [Paragraph("<b>Ram Rajaraman</b>", td_bold), Paragraph("<b>15 streams</b>", td_bold), Paragraph("High load: Trading Plan, GridComposer, NextShield, Billing", td_style)],
        [Paragraph("<b>Jake Wellington</b>", td_bold), Paragraph("<b>15 streams</b>", td_bold), Paragraph("Critical load: Purchase Intake ($7.5M), Conduit Q4", td_style)],
        [Paragraph("<b>William Lane</b>", td_bold), Paragraph("<b>12 streams</b>", td_bold), Paragraph("Enterprise AI lead: AI Factory, Claude, FinOps", td_style)],
        [Paragraph("<b>Brett Gavagni</b>", td_bold), Paragraph("<b>7 streams</b>", td_bold), Paragraph("Conduit & PGD Central Maintenance integrations", td_style)]
    ]
    sm_tbl = Table(sm_data, colWidths=[115, 80, 165])
    sm_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), BLACK),
        ('BOX', (0,0), (-1,-1), 1.0, BORDER_BLACK),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_BLACK),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
    ]))

    split_row = Table([[sen_tbl, sm_tbl]], colWidths=[355, 365])
    split_row.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(split_row)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Complete Future Hire (FH #1–#26) Deficit Roster — 30.4 Open FTEs", section_h2))

    fh_table_content = [
        [
            Paragraph("Slot", th_white),
            Paragraph("Target Workstream Association", th_white),
            Paragraph("Vertical / Host Lead", th_white),
            Paragraph("Required Role Scope", th_white),
            Paragraph("Level", th_white),
            Paragraph("Strategic Business Impact", th_white)
        ],
        [Paragraph("<b>FH #1</b>", td_bold), Paragraph("DSLOT Alpha / EMRE ($6.0M)", td_style), Paragraph("Kevin McWhorter", td_style), Paragraph("Staff Applied Scientist / Dev", td_bold), Paragraph("Staff", td_style), Paragraph("Detail solar layout optimization; unblocks CAD algorithms", td_style)],
        [Paragraph("<b>FH #4</b>", td_bold), Paragraph("Regulatory Lifecycle ($1.9M)", td_style), Paragraph("Farrukh Khan", td_style), Paragraph("Senior / Staff Software Developer", td_bold), Paragraph("Staff", td_style), Paragraph("Relieves Dave Sunderman on regulatory compliance filings", td_style)],
        [Paragraph("<b>FH #5</b>", td_bold), Paragraph("DSLOT Alpha / EMRE ($6.0M)", td_style), Paragraph("Kevin McWhorter", td_style), Paragraph("Staff Data / Telemetry Engineer", td_bold), Paragraph("Staff", td_style), Paragraph("High-throughput solar CAD telemetry data pipelines", td_style)],
        [Paragraph("<b>FH #8</b>", td_bold), Paragraph("Conduit Q4: Scheduling PF2 ($3.1M)", td_style), Paragraph("Jarod Bryant", td_style), Paragraph("Staff Platform Developer", td_bold), Paragraph("Staff", td_style), Paragraph("Cross-system asset schedule synchronization across renewables", td_style)],
        [Paragraph("<b>FH #9</b>", td_bold), Paragraph("Trading Plan / GridComposer ($12M)", td_style), Paragraph("Stephan Nitsche", td_style), Paragraph("Staff Software Developer", td_bold), Paragraph("Staff", td_style), Paragraph("NEA core algorithmic trading platform scaling & order execution", td_style)],
        [Paragraph("<b>FH #10</b>", td_bold), Paragraph("Contractor Control Tower ($1.9M)", td_style), Paragraph("John Andersen III", td_style), Paragraph("Staff Full Stack Developer", td_bold), Paragraph("Staff", td_style), Paragraph("Vendor analytics, contractor onboarding & spend control", td_style)],
        [Paragraph("<b>FH #15</b>", td_bold), Paragraph("Trading Plan / GridComposer ($12M)", td_style), Paragraph("Stephan Nitsche", td_style), Paragraph("Staff Software Developer", td_bold), Paragraph("Staff", td_style), Paragraph("Trade execution queue & realtime pricing pipeline", td_style)],
        [Paragraph("<b>FH #19</b>", td_bold), Paragraph("Purchase Intake Agent Q4 ($7.5M)", td_style), Paragraph("Farrukh Khan", td_style), Paragraph("Staff Full Stack Tech Lead", td_bold), Paragraph("Staff", td_style), Paragraph("Direct relief for Dave Sunderman on $7.5M Purchase Intake engine", td_style)],
        [Paragraph("<b>FH #20</b>", td_bold), Paragraph("Regulatory Lifecycle ($1.9M)", td_style), Paragraph("Farrukh Khan", td_style), Paragraph("Staff Software Developer", td_bold), Paragraph("Staff", td_style), Paragraph("Automated compliance documentation ingestion", td_style)],
        [Paragraph("<b>FH #24</b>", td_bold), Paragraph("AI Software Factory ($592K)", td_style), Paragraph("Anuj Chokshi", td_style), Paragraph("Staff AI / ML Systems Engineer", td_bold), Paragraph("Staff", td_style), Paragraph("Core LLM tool chaining, factory scaffolding, & prompt engineering", td_style)],
        [Paragraph("<b>FH #25</b>", td_bold), Paragraph("AI Software Factory ($592K)", td_style), Paragraph("Anuj Chokshi", td_style), Paragraph("Staff Full Stack AI Developer", td_bold), Paragraph("Staff", td_style), Paragraph("Enterprise partner prototype delivery squad", td_style)],
        [Paragraph("<b>FH #26</b>", td_bold), Paragraph("AI Software Factory ($592K)", td_style), Paragraph("Anuj Chokshi", td_style), Paragraph("Staff Systems / Eval Engineer", td_bold), Paragraph("Staff", td_style), Paragraph("Automated evaluation harnesses & production CI/CD", td_style)],
    ]
    fh_tbl = Table(fh_table_content, colWidths=[42, 175, 105, 145, 42, 211])
    fh_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), BLACK),
        ('BOX', (0,0), (-1,-1), 1.0, BORDER_BLACK),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_BLACK),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 1.8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1.8),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(fh_tbl)

    # =========================================================================
    # PAGE 3: ACTIONABLE PLACEMENT SNAPSHOT — FOUR POD PLAYS (HIGH CONTRAST)
    # =========================================================================
    story.append(PageBreak())

    header_table3 = Table(
        [
            [
                Paragraph("Actionable Placement Snapshot: Four High-Value Pod Plays", title_style),
                Paragraph("POD PLACEMENTS<br/><font size='7.5'>$33.0M Target Net Asset Value</font>", badge_style)
            ],
            [
                Paragraph("Direct Role-to-Gap Placement Strategy for PartnerAdvisory Across Mission-Critical Delivery Bottlenecks", subtitle_style),
                ""
            ]
        ],
        colWidths=[520, 200]
    )
    header_table3.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table3)
    story.append(Spacer(1, 8))

    def make_high_contrast_card(title, nav, leads, gap_text, roles):
        t_header = Table(
            [[Paragraph(f"<b>{title}</b>", ParagraphStyle('PT', fontName='Helvetica-Bold', fontSize=10, textColor=WHITE)),
              Paragraph(f"<b>{nav}</b>", ParagraphStyle('PN', fontName='Helvetica-Bold', fontSize=9.5, textColor=WHITE, alignment=2))]],
            colWidths=[240, 95]
        )
        t_header.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), BLACK),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ]))

        roles_list = "<br/>".join([f"• <b>{r}</b>" for r in roles])
        
        t_body = Table(
            [
                [Paragraph(f"<b>Executive Leads:</b> {leads}", td_bold)],
                [Paragraph(f"<b>Delivery Gap:</b> {gap_text}", td_style)],
                [Paragraph(f"<b>EXACT ROLES TO PITCH:</b><br/>{roles_list}", ParagraphStyle('RL', fontName='Helvetica-Bold', fontSize=8, leading=11, textColor=BLACK))]
            ],
            colWidths=[335]
        )
        t_body.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), WHITE),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('LINEBELOW', (0,0), (0,0), 0.8, BORDER_BLACK),
            ('LINEBELOW', (0,1), (0,1), 0.8, BORDER_BLACK),
        ]))

        card = Table([[t_header], [t_body]], colWidths=[345])
        card.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 1.2, BORDER_BLACK),
            ('BACKGROUND', (0,0), (-1,-1), WHITE),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ]))
        return card

    card1 = make_high_contrast_card(
        "POD 1: Infrastructure & Construction", "$10.9M NAV",
        "Kevin McWhorter (VP Infra) / Chris Nunalee (NEA Ops)",
        "<b>38% Staffed:</b> Construction Staffing ($4.9M NAV) has only 1 Dev assigned. DSLOT Alpha ($6.0M NAV) is 95% staffed but platform engineers are bottlenecked.",
        ["1 Product Manager (lead Construction Staffing)", "1 Staff Applied Scientist (DSLOT layout optimization)", "2–3 Data / Cloud Software Engineers (FH #1, #5)"]
    )

    card2 = make_high_contrast_card(
        "POD 2: Trade Floor / GridComposer", "$12.0M NAV",
        "Stephan Nitsche (Energy Mngt) / Chris Nunalee (NEA Ops)",
        "<b>Flagship Revenue Driver:</b> Sitting at 93% staffed. 2 critical open developer seats directly stall algorithmic execution queue release.",
        ["1 Product Manager (algorithmic trade execution)", "1 Staff Software Developer (Future Hire #15)", "1 Senior Systems Reliability Engineer"]
    )

    card3 = make_high_contrast_card(
        "POD 3: Bus Ops AI Relief", "$9.6M NAV",
        "Farrukh Khan (Bus Ops AI) / Matt Barrows (Enterprise AI)",
        "<b>6x Over-Allocation:</b> Lead Dave Sunderman is over-allocated across 6 streams, including the critical $7.5M Purchase Intake Agent and Regulatory Lifecycle ($1.9M).",
        ["1 Senior Technical / Delivery Lead (relieve Dave Sunderman)", "3 Staff Software Developers (FH #4, #19, #20)"]
    )

    card4 = make_high_contrast_card(
        "POD 4: AI Software Factory", "$592K Prototype",
        "Anuj Chokshi (Enterprise AI) / Matt Barrows (Enterprise AI)",
        "<b>75% Vacant:</b> 3 of 4 seats vacant on this new partner prototype (FH #24, #25, #26). High-visibility greenfield to establish an end-to-end execution squad.",
        ["3 Staff Software / AI Engineers (Prompt & Systems)", "Turnkey Pod Delivery Solution for Enterprise AI"]
    )

    grid_table = Table([[card1, card2], [card3, card4]], colWidths=[360, 360])
    grid_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(grid_table)
    story.append(Spacer(1, 8))

    action_box = Table(
        [
            [
                Paragraph("<b>Recommended PartnerAdvisory Action Plan:</b> Schedule a 15-minute executive review with Drew Oakley and Cameron Mercer. Anchor the discussion directly on the <b>30.4 unfilled FTE deficit</b> across these four pods, highlighting that NEA cannot hire FTEs in time for Q4 delivery milestones. Pitch pre-vetted pods to capture immediate capacity arbitrage.", td_style)
            ]
        ],
        colWidths=[720]
    )
    action_box.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_GRAY),
        ('BOX', (0,0), (-1,-1), 1.2, BORDER_BLACK),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(action_box)

    def paint_white_bg(canvas_obj, doc_obj):
        canvas_obj.saveState()
        canvas_obj.setFillColor(WHITE)
        canvas_obj.rect(0, 0, 792, 612, stroke=0, fill=1)
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=paint_white_bg, onLaterPages=paint_white_bg, canvasmaker=HighContrastNumberedCanvas)
    print(f"Successfully generated High-Contrast Executive PDF at: {pdf_path} ({pdf_path.stat().st_size:,} bytes)")

if __name__ == "__main__":
    build_pdf()
