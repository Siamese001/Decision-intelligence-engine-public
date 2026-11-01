#!/usr/bin/env python3
"""Generate the 3840x2160 (16:9) 4K Lego Block Git Rebase vs. PR Merge diagram."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import matplotlib.patches as patches
import matplotlib.pyplot as plt

CANVAS_W = 3840
CANVAS_H = 2160
BRICK_W = 330
BRICK_H = 76
LEVEL_SPACING = 145
BASE_Y = 480

PALETTE: dict[str, Any] = {
    "bg": "#070B14",
    "card_bg": "#0F172A",
    "card_border": "#1E293B",
    "header_bg": "#1E293B",
    "text_primary": "#F8FAFC",
    "text_secondary": "#94A3B8",
    "accent_blue": "#38BDF8",
    "connector_main": "#64748B",
    "connector_branch": "#C084FC",
    "lego_gray": {"body": "#475569", "stud": "#64748B", "edge": "#94A3B8", "text": "#F8FAFC"},
    "lego_blue": {"body": "#1D4ED8", "stud": "#3B82F6", "edge": "#93C5FD", "text": "#FFFFFF"},
    "lego_green": {"body": "#15803D", "stud": "#22C55E", "edge": "#86EFAC", "text": "#FFFFFF"},
    "lego_yellow": {"body": "#B45309", "stud": "#D97706", "edge": "#FDE68A", "text": "#FFFFFF"},
    "lego_orange": {"body": "#C2410C", "stud": "#EA580C", "edge": "#FDBA74", "text": "#FFFFFF"},
    "lego_purple": {"body": "#7E22CE", "stud": "#A855F7", "edge": "#E9D5FF", "text": "#FFFFFF"},
}

COL_CONFIG: list[dict[str, Any]] = [
    {
        "num": "0",
        "title": "BEFORE REBASE",
        "badge": "Diverged / Stranded Base",
        "badge_color": "#DC2626",
        "desc_lines": [
            "Your branch branched off yesterday's Blue base.",
            "Three new PRs landed on main, leaving your code",
            "stranded 10 commits behind.",
        ],
        "t_main": "main points to: Orange PR #3 (Level 5)",
        "t_you": "Your branch based on: Blue Base (Level 2)",
    },
    {
        "num": "1",
        "title": "REBASE: SYNC BASE",
        "badge": "Unplug Purple & Catch Up",
        "badge_color": "#D97706",
        "desc_lines": [
            "Git saves your Purple brick as a raw diff patch,",
            "resets your branch to Blue, and rolls forward",
            "to absorb Green, Yellow, and Orange.",
        ],
        "t_main": "main points to: Orange PR #3 (Level 5)",
        "t_you": "Your branch synced to: Orange PR #3 (Level 5)",
    },
    {
        "num": "2",
        "title": "REBASE: REPLAY ON TOP",
        "badge": "Conflict Check & Test-Fit",
        "badge_color": "#2563EB",
        "desc_lines": [
            "Git replays Purple on top of Orange. Any conflicts",
            "are resolved privately on your branch.",
            "main is still completely untouched.",
        ],
        "t_main": "main points to: Orange PR #3 (Level 5)",
        "t_you": "Your branch rebased to: Purple Roof (Level 6)",
    },
    {
        "num": "3",
        "title": "GOVERNED PR MERGE",
        "badge": "Permanent Promotion to Main",
        "badge_color": "#16A34A",
        "desc_lines": [
            "After passing all 32 review gates, the PR cements",
            "Purple onto the main tower and advances the",
            "official main signpost up to the roof.",
        ],
        "t_main": "main points to: Purple Roof (Level 6) [UPDATED]",
        "t_you": "Your branch: Merged & Closed cleanly",
    },
]


def _draw_studs(
    ax: Any, x0: float, y0: float, w: float, h: float, cfg: dict[str, str], is_ghost: bool, alpha: float
) -> None:
    num_studs, stud_w, stud_h = 6, 26, 12
    spacing = (w - 48) / (num_studs - 1)
    edge_style = "--" if is_ghost else "-"
    fill_alpha = (0.28 if is_ghost else 0.95) * alpha
    for i in range(num_studs):
        sx = x0 + 24 + i * spacing
        sy = y0 + h
        if not is_ghost:
            ax.add_patch(
                patches.FancyBboxPatch(
                    (sx - stud_w / 2 + 2, sy - 4),
                    stud_w,
                    stud_h,
                    boxstyle="round,pad=0,rounding_size=5",
                    facecolor="#000000",
                    edgecolor="none",
                    alpha=0.35 * alpha,
                    zorder=6,
                )
            )
            ax.add_patch(
                patches.Ellipse(
                    (sx, sy + stud_h - 2),
                    stud_w - 6,
                    4.5,
                    facecolor="#FFFFFF",
                    edgecolor="none",
                    alpha=0.45 * alpha,
                    zorder=8,
                )
            )
        ax.add_patch(
            patches.FancyBboxPatch(
                (sx - stud_w / 2, sy - 2),
                stud_w,
                stud_h,
                boxstyle="round,pad=0,rounding_size=5",
                facecolor=cfg["stud"],
                edgecolor=cfg["edge"],
                linewidth=2.0,
                linestyle=edge_style,
                alpha=fill_alpha,
                zorder=7,
            )
        )


def draw_lego_brick(
    ax: Any,
    cx: float,
    cy: float,
    w: float,
    h: float,
    kind: str,
    label: str,
    sublabel: str | None = None,
    is_ghost: bool = False,
    alpha: float = 1.0,
) -> None:
    cfg = PALETTE[kind]
    edge_style = "--" if is_ghost else "-"
    fill_alpha = (0.28 if is_ghost else 0.95) * alpha
    x0, y0 = cx - w / 2, cy - h / 2
    if not is_ghost:
        ax.add_patch(
            patches.FancyBboxPatch(
                (x0 + 6, y0 - 6),
                w,
                h,
                boxstyle="round,pad=0,rounding_size=12",
                facecolor="#000000",
                edgecolor="none",
                alpha=0.45 * alpha,
                zorder=4,
            )
        )
        ax.add_patch(
            patches.FancyBboxPatch(
                (x0 + 4, y0 + h - 10),
                w - 8,
                6,
                boxstyle="round,pad=0,rounding_size=3",
                facecolor="#FFFFFF",
                edgecolor="none",
                alpha=0.28 * alpha,
                zorder=6,
            )
        )
    ax.add_patch(
        patches.FancyBboxPatch(
            (x0, y0),
            w,
            h,
            boxstyle="round,pad=0,rounding_size=12",
            facecolor=cfg["body"],
            edgecolor=cfg["edge"],
            linewidth=2.8,
            linestyle=edge_style,
            alpha=fill_alpha,
            zorder=5,
        )
    )
    _draw_studs(ax, x0, y0, w, h, cfg, is_ghost, alpha)
    if sublabel:
        ax.text(
            cx, cy + 11, label, ha="center", va="center", fontsize=12,
            fontweight="bold", color=cfg["text"], alpha=alpha, zorder=9, family="sans-serif",
        )
        ax.text(
            cx, cy - 13, sublabel, ha="center", va="center", fontsize=10.5,
            fontweight="normal", color=cfg["text"], alpha=0.92 * alpha, zorder=9, family="sans-serif",
        )
    else:
        ax.text(
            cx, cy, label, ha="center", va="center", fontsize=13.5,
            fontweight="bold", color=cfg["text"], alpha=alpha, zorder=9, family="sans-serif",
        )


def draw_top_signpost(
    ax: Any, cx: float, target_top_y: float, text: str, color: str, text_color: str = "#FFFFFF"
) -> None:
    arrow_bottom = target_top_y + 4
    arrow_top = arrow_bottom + 22
    ax.annotate(
        "", xy=(cx, arrow_bottom), xytext=(cx, arrow_top),
        arrowprops={"arrowstyle": "-|>", "color": color, "lw": 3.0, "mutation_scale": 14},
        zorder=12,
    )
    ax.text(
        cx, arrow_top + 18, f"  {text}  ", ha="center", va="center",
        fontsize=13, fontweight="bold", color=text_color,
        bbox={"boxstyle": "round,pad=0.42", "facecolor": color, "edgecolor": "#FFFFFF", "lw": 1.8},
        zorder=13, family="sans-serif",
    )


def _draw_stage_overlay(
    ax: Any, idx: int, lane_main_x: float, lane_you_x: float, levels: list[float]
) -> None:
    _, y_l2, y_l3, y_l4, y_l5, y_l6 = levels
    y_pocket = BASE_Y + LEVEL_SPACING * 5.3
    if idx == 0:
        draw_top_signpost(ax, lane_main_x, y_l5 + BRICK_H / 2 + 12, "main", "#EA580C")
        ax.plot(
            [lane_main_x + BRICK_W / 2, lane_you_x, lane_you_x],
            [y_l2, y_l2, y_l3 - BRICK_H / 2],
            color=PALETTE["connector_branch"], linewidth=4.5, zorder=4,
        )
        ax.annotate(
            "", xy=(lane_you_x, y_l3 - BRICK_H / 2), xytext=(lane_you_x, y_l2),
            arrowprops={"arrowstyle": "-|>", "color": PALETTE["connector_branch"], "lw": 4, "mutation_scale": 16},
            zorder=4,
        )
        draw_lego_brick(ax, lane_you_x, y_l3, BRICK_W, BRICK_H, "lego_purple", "feat/graphdb", "Wave 1-3 Commits")
        draw_top_signpost(ax, lane_you_x, y_l3 + BRICK_H / 2 + 12, "Your Branch", "#9333EA")
        ax.text(
            lane_you_x, (y_l4 + y_l5) / 2, "DIVERGED!\n10 Commits Behind",
            ha="center", va="center", fontsize=14, fontweight="bold", color="#F87171",
            bbox={"boxstyle": "round,pad=0.5", "facecolor": "#450A0A", "edgecolor": "#EF4444", "lw": 1.8},
            zorder=10,
        )
    elif idx == 1:
        draw_top_signpost(ax, lane_main_x, y_l5 + BRICK_H / 2 + 12, "main", "#EA580C")
        ax.add_patch(
            patches.FancyBboxPatch(
                (lane_you_x - BRICK_W / 2 - 10, y_pocket - BRICK_H / 2 - 10),
                BRICK_W + 20, BRICK_H + 24, boxstyle="round,pad=0,rounding_size=14",
                facecolor="#2E1065", edgecolor="#A855F7", linewidth=2.5, linestyle="--", zorder=4,
            )
        )
        draw_lego_brick(
            ax, lane_you_x, y_pocket, BRICK_W, BRICK_H, "lego_purple",
            "(+diff patch)", "Held in Temp Memory", is_ghost=True, alpha=0.9,
        )
        ax.text(
            lane_you_x, y_pocket + BRICK_H / 2 + 28, "TEMPORARY REBASE POCKET",
            ha="center", va="center", fontsize=12.5, fontweight="bold", color="#E9D5FF", zorder=6,
        )
        ax.annotate(
            "", xy=(lane_you_x, y_l5 - 30), xytext=(lane_you_x, y_l2),
            arrowprops={"arrowstyle": "-|>", "color": "#38BDF8", "lw": 5, "mutation_scale": 22}, zorder=10,
        )
        ax.text(
            lane_you_x, (y_l2 + y_l5) / 2 - 15, "Fast-Forward Base:\nAbsorb PR 1, 2, 3",
            ha="center", va="center", fontsize=13.5, fontweight="bold", color="#38BDF8",
            bbox={"boxstyle": "round,pad=0.5", "facecolor": "#0C4A6E", "edgecolor": "#0284C7", "lw": 1.8},
            zorder=11,
        )
        draw_top_signpost(ax, lane_you_x, y_l5 + 15, "Your Branch Base", "#9333EA")
    elif idx == 2:
        draw_top_signpost(ax, lane_main_x, y_l5 + BRICK_H / 2 + 12, "main", "#EA580C")
        ax.plot(
            [lane_main_x + BRICK_W / 2, lane_you_x, lane_you_x],
            [y_l5, y_l5, y_l6 - BRICK_H / 2],
            color=PALETTE["connector_branch"], linewidth=4.5, zorder=4,
        )
        ax.annotate(
            "", xy=(lane_you_x, y_l6 - BRICK_H / 2), xytext=(lane_you_x, y_l5),
            arrowprops={"arrowstyle": "-|>", "color": PALETTE["connector_branch"], "lw": 4, "mutation_scale": 16},
            zorder=4,
        )
        draw_lego_brick(
            ax, lane_you_x, y_l6, BRICK_W, BRICK_H, "lego_purple", "feat/graphdb'", "Replayed on Top of Orange"
        )
        draw_top_signpost(ax, lane_you_x, y_l6 + BRICK_H / 2 + 12, "Your Branch", "#9333EA")
        ax.text(
            lane_you_x, (y_l2 + y_l4) / 2, "32 CI Gates Pass\n0 Conflicts Detected",
            ha="center", va="center", fontsize=14, fontweight="bold", color="#4ADE80",
            bbox={"boxstyle": "round,pad=0.5", "facecolor": "#052E16", "edgecolor": "#16A34A", "lw": 1.8},
            zorder=10,
        )
    elif idx == 3:
        draw_lego_brick(
            ax, lane_main_x, y_l6, BRICK_W, BRICK_H, "lego_purple", "feat/graphdb", "Cemented into main!"
        )
        ax.plot([lane_you_x, lane_main_x + BRICK_W / 2 + 10], [y_l6, y_l6], color="#16A34A", linewidth=4.5, zorder=4)
        ax.annotate(
            "", xy=(lane_main_x + BRICK_W / 2 + 8, y_l6), xytext=(lane_you_x, y_l6),
            arrowprops={"arrowstyle": "-|>", "color": "#16A34A", "lw": 4, "mutation_scale": 16}, zorder=4,
        )
        draw_top_signpost(ax, lane_main_x, y_l6 + BRICK_H / 2 + 12, "main", "#16A34A")
        draw_top_signpost(ax, lane_you_x, y_l6 + BRICK_H / 2 + 12, "PR Merged", "#9333EA")
        ax.text(
            lane_you_x, (y_l2 + y_l4) / 2, "GOVERNED MERGE\nNew Public Foundation",
            ha="center", va="center", fontsize=14, fontweight="bold", color="#86EFAC",
            bbox={"boxstyle": "round,pad=0.5", "facecolor": "#064E3B", "edgecolor": "#059669", "lw": 1.8},
            zorder=10,
        )


def _draw_column(ax: Any, idx: int, col: dict[str, Any]) -> None:
    col_w, col_gap, start_x, panel_y0, panel_h = 860, 55, 96, 340, 1460
    px = start_x + idx * (col_w + col_gap)
    ax.add_patch(
        patches.FancyBboxPatch(
            (px, panel_y0), col_w, panel_h, boxstyle="round,pad=0,rounding_size=20",
            facecolor=PALETTE["card_bg"], edgecolor=PALETTE["card_border"], linewidth=2.5, zorder=1,
        )
    )
    ax.add_patch(
        patches.FancyBboxPatch(
            (px, panel_y0 + panel_h - 150), col_w, 150, boxstyle="round,pad=0,rounding_size=20",
            facecolor=PALETTE["header_bg"], edgecolor=PALETTE["card_border"], linewidth=2, zorder=2,
        )
    )
    ax.text(
        px + 35, panel_y0 + panel_h - 45, f"STAGE {col['num']}: {col['title']}",
        fontsize=22, fontweight="bold", color=PALETTE["text_primary"], va="center", zorder=3,
    )
    ax.text(
        px + 35, panel_y0 + panel_h - 95, f"  {col['badge']}  ",
        ha="left", va="center", fontsize=13.5, fontweight="bold", color="#FFFFFF",
        bbox={"boxstyle": "round,pad=0.42", "facecolor": col["badge_color"], "edgecolor": "none"}, zorder=4,
    )
    for line_i, line_t in enumerate(col["desc_lines"]):
        ax.text(
            px + 35, panel_y0 + panel_h - 175 - line_i * 26, line_t,
            fontsize=15.5, color=PALETTE["text_secondary"], va="top", zorder=3,
        )
    lane_main_x, lane_you_x = px + 230, px + 630
    ax.text(lane_main_x, panel_y0 + panel_h - 275, "[main Track]", ha="center", va="center", fontsize=17.5, fontweight="bold", color="#94A3B8", zorder=3)
    ax.text(lane_you_x, panel_y0 + panel_h - 275, "[Your Branch Track]", ha="center", va="center", fontsize=17.5, fontweight="bold", color="#C084FC", zorder=3)
    ax.plot([lane_main_x, lane_main_x], [BASE_Y - 40, panel_y0 + panel_h - 300], color="#1E293B", linewidth=2.5, linestyle=":", zorder=2)
    ax.plot([lane_you_x, lane_you_x], [BASE_Y - 40, panel_y0 + panel_h - 300], color="#1E293B", linewidth=2.5, linestyle=":", zorder=2)

    levels = [BASE_Y + LEVEL_SPACING * i for i in range(6)]
    top_stem = levels[4] if idx < 3 else levels[5]
    ax.plot([lane_main_x, lane_main_x], [levels[0], top_stem], color=PALETTE["connector_main"], linewidth=5, zorder=3)

    main_bricks = [
        ("lego_gray", "Older Commits", "main Foundation"),
        ("lego_blue", "fix/ingest-hang", "Yesterday's Base"),
        ("lego_green", "PR #1: partner", "Merged to main"),
        ("lego_yellow", "PR #2: doc-edo", "Merged to main"),
        ("lego_orange", "PR #3: hygiene", "Current main Top"),
    ]
    for b_idx, (kind, lbl, sub) in enumerate(main_bricks):
        draw_lego_brick(ax, lane_main_x, levels[b_idx], BRICK_W, BRICK_H, kind, lbl, sub)

    _draw_stage_overlay(ax, idx, lane_main_x, lane_you_x, levels)

    box_y = panel_y0 + 20
    ax.add_patch(
        patches.FancyBboxPatch(
            (px + 20, box_y), col_w - 40, 85, boxstyle="round,pad=0,rounding_size=12",
            facecolor="#020617", edgecolor="#334155", linewidth=1.8, zorder=2,
        )
    )
    ax.text(px + col_w / 2, box_y + 52, col["t_main"], ha="center", va="center", fontsize=15, fontweight="bold", color="#F1F5F9", zorder=3)
    ax.text(px + col_w / 2, box_y + 24, col["t_you"], ha="center", va="center", fontsize=15, fontweight="bold", color="#A855F7", zorder=3)


def generate_diagram(output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(CANVAS_W / 100, CANVAS_H / 100), dpi=100)
    fig.patch.set_facecolor(PALETTE["bg"])
    ax.set_facecolor(PALETTE["bg"])
    ax.set_xlim(0, CANVAS_W)
    ax.set_ylim(0, CANVAS_H)
    ax.axis("off")

    ax.text(96, 2050, "GIT ARCHITECTURE & GOVERNANCE • VISUAL MENTAL MODEL", fontsize=24, fontweight="bold", color=PALETTE["accent_blue"], va="baseline")
    ax.text(96, 1950, "Why Rebase Precedes PR Merge: The Lego Block Stacking Lifecycle", fontsize=48, fontweight="bold", color=PALETTE["text_primary"], va="baseline")
    ax.text(
        96, 1885,
        "How Git detaches your feature branch, synchronizes with mainline PRs, replays changes on the latest base, and safely promotes to main.",
        fontsize=24, color=PALETTE["text_secondary"], va="baseline",
    )
    ax.plot([96, 3744], [1845, 1845], color="#1E293B", linewidth=3)

    for idx, col in enumerate(COL_CONFIG):
        _draw_column(ax, idx, col)

    strip_y0 = 45
    ax.add_patch(
        patches.FancyBboxPatch(
            (96, strip_y0), 3648, 250, boxstyle="round,pad=0,rounding_size=18",
            facecolor="#0F172A", edgecolor="#38BDF8", linewidth=2.5, zorder=2,
        )
    )
    ax.text(140, strip_y0 + 195, "GOVERNANCE TAKEAWAYS: WHY THE REBASE -> PR MERGE ORDER CANNOT BE SKIPPED", fontsize=22, fontweight="bold", color=PALETTE["accent_blue"], va="center", zorder=3)

    takeaways = [
        (140, "1. Zero-Friction Mainline", "If you merge without rebasing, you force main to resolve conflicts\nacross diverging branches, risking unseen build and schema regressions."),
        (1380, "2. Private Sandbox Failure", "Rebase catches all integration breaks in your private worktree.\nIf Purple clashes with Green, Yellow, or Orange, main stays 100% safe."),
        (2580, "3. Fast-Path Governed Promotion", "Because Stage 2 proved that Purple sits cleanly on top of Orange,\nStage 3 PR Merge is a trivial, zero-risk signpost move taking <5 seconds."),
    ]
    for tx, heading, body in takeaways:
        ax.text(tx, strip_y0 + 145, heading, fontsize=19, fontweight="bold", color="#F8FAFC", va="center", zorder=3)
        ax.text(tx, strip_y0 + 115, body, fontsize=15, color="#94A3B8", va="top", zorder=3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, format="png", dpi=100, facecolor=PALETTE["bg"], bbox_inches=None)
    plt.close(fig)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Generate 4K Git Rebase vs. PR Merge Lego diagram.")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "documentation" / "governance" / "git_rebase_lego_blocks_4k.png",
        help="Target path for 3840x2160 PNG output.",
    )
    args = parser.parse_args()
    generate_diagram(args.output)
    print(f"Generated 3840x2160 4K diagram at: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
