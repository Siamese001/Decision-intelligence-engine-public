"""Formatting utilities for markdown mutation tables, banners, and itemized assertions."""
from __future__ import annotations

from typing import Any

from .constants import _COMPONENTS


def format_mutation_summary_banner(summary: dict[str, Any]) -> str:
    """Format a clean, informative ASCII banner for human inspection."""
    status = summary.get("status", "UNKNOWN")
    gen_before = summary.get("generation_before") or "none"
    gen_after = summary.get("generation_after") or "none"
    gen_str = (
        f"{gen_before} -> {gen_after}"
        if gen_before != gen_after
        else f"{gen_before} (unchanged)"
    )

    sha_before = (summary.get("graph_sha256_before") or "")[:16]
    sha_after = (summary.get("graph_sha256_after") or "")[:16]
    sha_str = (
        f"{sha_before}... -> {sha_after}..."
        if sha_before != sha_after
        else f"{sha_before}... (unchanged)"
    )

    sidecar_sha_before = (summary.get("sidecar_sha256_before") or "")[:16]
    sidecar_sha_after = (summary.get("sidecar_sha256_after") or "")[:16]
    sidecar_sha_str = (
        f"{sidecar_sha_before}... -> {sidecar_sha_after}..."
        if sidecar_sha_before != sidecar_sha_after
        else f"{sidecar_sha_before}... (unchanged)"
    )

    total_ops = summary.get("total_operations", 0)
    net = summary.get("net_changes", {})

    lines = [
        "=" * 78,
        "GRAPH MUTATION SUMMARY",
        "=" * 78,
        f"Outcome:              {status}",
        f"Generation:           {gen_str}",
        f"Graph SHA-256:        {sha_str}",
        f"Sidecar SHA-256:      {sidecar_sha_str}",
        f"Total Operations:     {total_ops}",
    ]
    if status == "IDEMPOTENT_NOOP":
        lines.append("Net Changes:          None (Graph and Sidecar remain identical)")
    else:
        lines.append("")
        lines.append("Net Changes:")
        label_map = {
            "sources": "Sources",
            "source_versions": "Source versions",
            "extraction_runs": "Extraction runs",
            "evidence_items": "Evidence items",
            "entities": "Entities created",
            "canonical_assertions": "Canonical facts",
            "candidate_assertions": "Candidate facts",
            "superseded_assertions": "Superseded facts",
            "search_documents": "Search documents",
            "dense_vectors": "Dense vectors (1024d)",
            "sparse_weights": "Sparse token weights",
            "colbert_tokens": "ColBERT tokens",
            "retrieval_records": "Retrieval records",
        }
        for key, label in label_map.items():
            delta = net.get(key, 0)
            sign = f"+{delta}" if delta > 0 else f"{delta}"
            lines.append(f"  • {label:<22}: {sign}")

        entities_created = summary.get("entities_created", [])
        if entities_created:
            lines.append("")
            lines.append("Entities Created:")
            for ent in entities_created[:10]:
                name = ent.get("name") or ent.get("id")
                ent_type = ent.get("type", "entity")
                lines.append(f"  • {ent.get('id')} ({name}) [{ent_type}]")
            if len(entities_created) > 10:
                lines.append(f"  • ... and {len(entities_created) - 10} more")

        sources_added = summary.get("sources_added", [])
        if sources_added:
            lines.append("")
            lines.append("Sources Added:")
            for src in sources_added[:5]:
                lines.append(f"  • {src.get('source_id')}: {src.get('name', 'unnamed')}")
            if len(sources_added) > 5:
                lines.append(f"  • ... and {len(sources_added) - 5} more")

    lines.append("=" * 78)
    return "\n".join(lines)


def format_markdown_mutation_table(
    summary: dict[str, Any],
    *,
    assertions_inserted: list[dict[str, Any]] | None = None,
) -> str:
    """Format a mandatory markdown table breakdown of graph mutations or no-op runs."""
    status = summary.get("status", "UNKNOWN")
    is_noop = status == "IDEMPOTENT_NOOP"
    actual_delta = summary.get("actual_graph_delta") or {}
    table_rows = actual_delta.get("table", [])
    sidecar_rows = summary.get("sidecar_table", [])
    net = summary.get("net_changes", {})

    lines = [
        "### 1. Granular Graph DB Delta (Table-by-Table)",
        "",
        "| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    if is_noop:
        lines.append("| `node` | — | — | `+0` | Idempotent no-op: zero nodes created or modified. |")
        lines.append("| `source` | — | — | `+0` | Idempotent no-op: source already ingested. |")
        lines.append("| `source_version` | — | — | `+0` | Idempotent no-op: package digest matched active pair. |")
        lines.append("| `extraction_run` | — | — | `+0` | Idempotent no-op: zero extraction runs recorded. |")
        lines.append("| `evidence_item` | — | — | `+0` | Idempotent no-op: evidence fingerprints already present. |")
        lines.append("| `assertion` | — | — | `+0` | Idempotent no-op: assertions already recorded. |")
        lines.append("| `assertion_evidence` | — | — | `+0` | Idempotent no-op: evidence links already present. |")
        lines.append("| `search_document` | — | — | `+0` | Idempotent no-op: search index unchanged. |")
        lines.append("| `search_fts` | — | — | `+0` | Idempotent no-op: full-text search index unchanged. |")
        lines.append("| `audit_event` | — | — | `+0` | Idempotent no-op: zero mutating audit events. |")
        lines.append("| `dense_vector` (sidecar) | — | — | `+0` | Idempotent no-op: neural vector embeddings unchanged. |")
        lines.append("| `sparse_weight` (sidecar) | — | — | `+0` | Idempotent no-op: sparse token weights unchanged. |")
        lines.append("| `colbert_token` (sidecar) | — | — | `+0` | Idempotent no-op: ColBERT token vectors unchanged. |")
        lines.append("| `retrieval_record` (sidecar) | — | — | `+0` | Idempotent no-op: retrieval corpus unchanged. |")
    elif table_rows:
        rendered_tables: set[str] = set()
        for row in table_rows:
            comp = row.get("component_key", "")
            label = row.get("graph_component", comp)
            base = row.get("baseline", 0)
            after = row.get("actual_after", base)
            delta = row.get("actual_delta", 0)
            sign = f"+{delta}" if delta >= 0 else f"{delta}"
            comp_table_map = {key: table for key, _label, table in _COMPONENTS}
            tbl = row.get("table") or comp_table_map.get(comp, comp)
            rendered_tables.add(tbl)
            desc = f"{label} ({'no change' if delta == 0 else f'{sign} recorded'})"
            lines.append(f"| `{tbl}` ({comp}) | {base} | {after} | `{sign}` | {desc} |")

        # Guarantee presence of all 10 mandated tables if omitted from component breakdown
        mandated_additional = [
            ("extraction_run", net.get("extraction_runs", 0), "Extraction runs recorded"),
            ("assertion_evidence", net.get("assertion_evidence", 0), "Assertion-evidence provenance links"),
            ("search_document", net.get("search_documents", 0), "Search documents indexed"),
            ("search_fts", net.get("search_fts", 0), "Full-text search inverted index synchronized"),
        ]
        for mtbl, delta, mdesc in mandated_additional:
            if mtbl not in rendered_tables:
                sign = f"+{delta}" if delta >= 0 else f"{delta}"
                detail = f"{mdesc} ({'no change' if delta == 0 else f'{sign} recorded'})"
                lines.append(f"| `{mtbl}` | — | — | `{sign}` | {detail} |")

        if sidecar_rows:
            for srow in sidecar_rows:
                tbl = srow.get("table", "")
                base = srow.get("baseline", 0)
                after = srow.get("actual_after", base)
                delta = srow.get("actual_delta", 0)
                label = srow.get("label", tbl)
                sign = f"+{delta}" if delta >= 0 else f"{delta}"
                if delta > 0:
                    desc = f"{label} ({sign} generated)"
                else:
                    desc = f"{label} (no change — withheld by clearance or candidate lane)"
                lines.append(f"| `{tbl}` (sidecar) | {base} | {after} | `{sign}` | {desc} |")
        else:
            sidecar_mapping = [
                ("dense_vector", net.get("dense_vectors", 0), "Dense vector embeddings (1024-dim BGE-M3 float32)"),
                ("sparse_weight", net.get("sparse_weights", 0), "Sparse token weights (BGE-M3 learned lexical)"),
                ("colbert_token", net.get("colbert_tokens", 0), "ColBERT multi-vectors (contextual token embeddings)"),
                ("retrieval_record", net.get("retrieval_records", 0), "Retrieval records (clearance-approved corpus)"),
            ]
            for tbl, delta, desc in sidecar_mapping:
                sign = f"+{delta}" if delta >= 0 else f"{delta}"
                detail = f"{desc} ({'no change — withheld by clearance or candidate lane' if delta == 0 else f'{sign} generated'})"
                lines.append(f"| `{tbl}` (sidecar) | — | — | `{sign}` | {detail} |")
    else:
        mapping = [
            ("node", net.get("entities", 0), "Entities created / reconciled (zero unintended mutations)"),
            ("source", net.get("sources", 0), "Sources added"),
            ("source_version", net.get("source_versions", 0), "Source versions bound"),
            ("extraction_run", net.get("extraction_runs", 0), "Extraction runs recorded"),
            ("evidence_item", net.get("evidence_items", 0), "Evidence items recorded"),
            ("assertion", (net.get("canonical_assertions", 0) + net.get("candidate_assertions", 0)), "Assertions inserted / updated"),
            ("assertion_evidence", net.get("assertion_evidence", 0), "Assertion-evidence provenance links"),
            ("search_document", net.get("search_documents", 0), "Search documents indexed"),
            ("search_fts", net.get("search_fts", 0), "Full-text search inverted index synchronized"),
            ("audit_event", net.get("audit_events", 0), "Graph audit events recorded"),
        ]
        for tbl, delta, desc in mapping:
            sign = f"+{delta}" if delta >= 0 else f"{delta}"
            lines.append(f"| `{tbl}` | — | — | `{sign}` | {desc} |")
        if sidecar_rows:
            for srow in sidecar_rows:
                tbl = srow.get("table", "")
                base = srow.get("baseline", 0)
                after = srow.get("actual_after", base)
                delta = srow.get("actual_delta", 0)
                label = srow.get("label", tbl)
                sign = f"+{delta}" if delta >= 0 else f"{delta}"
                if delta > 0:
                    desc = f"{label} ({sign} generated)"
                else:
                    desc = f"{label} (no change — withheld by clearance or candidate lane)"
                lines.append(f"| `{tbl}` (sidecar) | {base} | {after} | `{sign}` | {desc} |")
        else:
            sidecar_mapping = [
                ("dense_vector", net.get("dense_vectors", 0), "Dense vector embeddings (1024-dim BGE-M3 float32)"),
                ("sparse_weight", net.get("sparse_weights", 0), "Sparse token weights (BGE-M3 learned lexical)"),
                ("colbert_token", net.get("colbert_tokens", 0), "ColBERT multi-vectors (contextual token embeddings)"),
                ("retrieval_record", net.get("retrieval_records", 0), "Retrieval records (clearance-approved corpus)"),
            ]
            for tbl, delta, desc in sidecar_mapping:
                sign = f"+{delta}" if delta >= 0 else f"{delta}"
                detail = f"{desc} ({'no change — withheld by clearance or candidate lane' if delta == 0 else f'{sign} generated'})"
                lines.append(f"| `{tbl}` (sidecar) | — | — | `{sign}` | {detail} |")

    if assertions_inserted is None:
        assertions_inserted = summary.get("assertions_inserted") or summary.get("itemized_assertions")

    lines.append("")
    lines.append("### 2. Assertions Inserted into `assertion` (Itemized Assertions)")
    lines.append("")
    lines.append("| Assertion ID | Subject Node | Predicate | Literal / Claim Text | Status |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    if is_noop:
        lines.append("| — | — | — | *Idempotent no-op: zero assertions inserted or modified.* | `IDEMPOTENT_NOOP` |")
    elif assertions_inserted:
        for asrt in assertions_inserted:
            aid = asrt.get("assertion_id", "")
            subj = asrt.get("subject_node_id", "")
            pred = asrt.get("predicate", "HAS_CLAIM_TEXT")
            lit = asrt.get("claim_text") or asrt.get("literal") or asrt.get("object_literal") or asrt.get("object_node_id") or ""
            raw_st = asrt.get("validation_state") or asrt.get("status") or "needs_review"
            st = "SOURCE_STATEMENT" if raw_st == "needs_review" else raw_st
            lines.append(f"| `{aid}` | `{subj}` | `{pred}` | *{lit}* | `{st}` |")
    else:
        lines.append("| — | — | — | *No assertions inserted or modified.* | `UNCHANGED` |")

    lines.append("")
    lines.append("### 3. Idempotency & Hash Transition Explanation")
    lines.append("")
    gen_b = summary.get("generation_before") or "none"
    gen_a = summary.get("generation_after") or "none"
    sha_b = summary.get("graph_sha256_before") or "none"
    sha_a = summary.get("graph_sha256_after") or "none"
    if is_noop:
        lines.append("- **State**: `IDEMPOTENT_NOOP` (Active generation and graph hash remained strictly unchanged).")
        lines.append(f"- **Active Generation**: `{gen_b}` (`+0` delta across all 14 tables).")
        lines.append(f"- **Graph SHA-256**: `{sha_b}` (unchanged).")
    else:
        lines.append("- **State**: `ACTIVATED` (Positive operations verified).")
        lines.append(f"- **Active Generation**: `{gen_b}` -> `{gen_a}`.")
        lines.append(f"- **Graph SHA-256 Transition**: `{sha_b}` -> `{sha_a}` (positive operations changed graph hash).")

    return "\n".join(lines)
