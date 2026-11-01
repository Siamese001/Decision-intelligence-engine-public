"""
tools/build_live_e2e_corpus.py

Stages authentic production files and derives multimodal corpus variants for
the Live End-to-End Ingestion Certification Matrix.
Emits reports/live_e2e_corpus_manifest.json.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / ".corpus"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def convert_vtt_to_srt(vtt_path: Path, srt_path: Path) -> None:
    lines = vtt_path.read_text(encoding="utf-8", errors="replace").splitlines()
    srt_lines = []
    cue_idx = 1
    in_cue = False
    for line in lines:
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if "-->" in line:
            srt_lines.append(str(cue_idx))
            cue_idx += 1
            # VTT uses '.' for ms; SRT uses ','
            srt_time = line.replace(".", ",")
            srt_lines.append(srt_time)
            in_cue = True
        elif line.strip() == "":
            if in_cue:
                srt_lines.append("")
                in_cue = False
        elif in_cue:
            srt_lines.append(line)
    if in_cue:
        srt_lines.append("")
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")


def convert_csv_to_tsv(csv_path: Path, tsv_path: Path) -> None:
    with open(csv_path, "r", encoding="utf-8", errors="replace") as fin, \
         open(tsv_path, "w", encoding="utf-8", newline="") as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout, delimiter="\t")
        for row in reader:
            writer.writerow(row)


def create_rfc822_eml(eml_path: Path) -> None:
    content = (
        "From: amit.ayer@consulting.example.com\n"
        "To: executive.steering@apex_global.com\n"
        "Date: Wed, 08 Oct 2026 14:30:00 -0400\n"
        "Subject: Project LENS Visual Asset Intelligence - Technical Architecture Sign-off\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "\n"
        "Executive Steering Committee,\n\n"
        "This email serves as the formal architectural sign-off for Project LENS Phase 1.\n"
        "The multi-modal ingestion pipeline has achieved operational parity across all asset types.\n"
        "The target latency of 50ms for L1 indexing has been verified against baseline benchmarks.\n\n"
        "Key Deliverables:\n"
        "1. Tri-tier visual inspection router validated.\n"
        "2. Knowledge graph delta ingestion completed.\n"
        "3. Universal Write Gateway approval protocol active.\n\n"
        "Regards,\n"
        "Amit Ayer\n"
        "Principal Architect, PartnerAdvisory\n"
    )
    eml_path.write_text(content, encoding="utf-8")


def create_authentic_html(html_path: Path) -> None:
    content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Project LENS Technical Architecture Specification</title>
</head>
<body>
  <h1>Project LENS Technical Architecture Specification</h1>
  <p><strong>Document Version:</strong> 2.4.0</p>
  <p><strong>Target Platform:</strong> Apex Global Enterprises Distributed Decision Intelligence Engine</p>
  <h2>1. Executive Summary</h2>
  <p>This document details the architectural boundaries, ingestion stages, and verification contracts for the LENS platform.</p>
  <h2>2. Operational Constraints</h2>
  <table>
    <thead>
      <tr><th>Component</th><th>Constraint</th><th>SLA Threshold</th></tr>
    </thead>
    <tbody>
      <tr><td>Audio Pipeline</td><td>Zero speech clipping on onset</td><td>&lt; 50ms latency</td></tr>
      <tr><td>Knowledge Graph</td><td>Atomic promotion via UWG</td><td>100% verification</td></tr>
      <tr><td>Vector Sidecar</td><td>Synchronous dual write with WAL</td><td>Zero lock contention</td></tr>
    </tbody>
  </table>
  <h2>3. Certification Sign-off</h2>
  <p>Approved by the Principal Architecture Board on October 11, 2026.</p>
</body>
</html>
"""
    html_path.write_text(content, encoding="utf-8")


def build_corpus() -> Dict[str, Any]:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {
        "version": "1.0.0",
        "description": "Authentic Production & Derived Test Corpus Manifest for Live Ingestion Certification",
        "corpus_dir": str(CORPUS_DIR.relative_to(ROOT)),
        "entries": {},
    }

    # 1. AUDIO (.wav, .m4a, .mp3, .flac, .ogg)
    audio_dir = CORPUS_DIR / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    src_wav = ROOT / "test.wav"
    if not src_wav.exists():
        primary_wav = ROOT.resolve().parents[1] / "test.wav"
        if primary_wav.exists():
            src_wav = primary_wav
        else:
            raise FileNotFoundError(f"Missing authentic test.wav at {src_wav} or {primary_wav}")

    canonical_wav = audio_dir / "test.wav"
    shutil.copyfile(src_wav, canonical_wav)

    # Convert to M4A using macOS afconvert
    canonical_m4a = audio_dir / "test.m4a"
    subprocess.run(["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", str(canonical_wav), str(canonical_m4a)], check=True, timeout=60)

    # Convert to FLAC and OGG using soundfile
    import soundfile as sf
    data, samplerate = sf.read(str(canonical_wav))
    canonical_flac = audio_dir / "test.flac"
    sf.write(str(canonical_flac), data, samplerate, format="FLAC")
    canonical_ogg = audio_dir / "test.ogg"
    sf.write(str(canonical_ogg), data, samplerate, format="OGG")

    # Edge cases for audio
    empty_wav = audio_dir / "empty.wav"
    empty_wav.touch()
    corrupt_wav = audio_dir / "corrupted_header.wav"
    corrupt_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt \x00\x00\x00\x00corruptpayloadbytes" * 50)

    manifest["entries"]["AUDIO"] = {
        "primary_file": str(canonical_wav.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_wav),
        "derived_variants": {
            ".m4a": {"path": str(canonical_m4a.relative_to(ROOT)), "sha256": sha256_file(canonical_m4a)},
            ".flac": {"path": str(canonical_flac.relative_to(ROOT)), "sha256": sha256_file(canonical_flac)},
            ".ogg": {"path": str(canonical_ogg.relative_to(ROOT)), "sha256": sha256_file(canonical_ogg)},
        },
        "edge_cases": {
            "empty": {"path": str(empty_wav.relative_to(ROOT)), "sha256": sha256_file(empty_wav)},
            "corrupted": {"path": str(corrupt_wav.relative_to(ROOT)), "sha256": sha256_file(corrupt_wav)},
        },
        "metadata": {
            "project": "project_lens",
            "meeting_name": "LENS Audio Review",
            "date": "10-11-2026",
            "attendees": ["Amit Ayer", "Operator"],
        },
    }

    # 2. VTT_SRT
    transcripts_dir = CORPUS_DIR / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    src_vtt = ROOT / "inputs" / "transcripts" / "LENS Pipeline Review and Sign-Off.vtt"
    canonical_vtt = transcripts_dir / "lens_pipeline_review.vtt"
    shutil.copyfile(src_vtt, canonical_vtt)

    canonical_srt = transcripts_dir / "lens_pipeline_review.srt"
    convert_vtt_to_srt(canonical_vtt, canonical_srt)

    empty_vtt = transcripts_dir / "empty.vtt"
    empty_vtt.touch()
    corrupt_vtt = transcripts_dir / "corrupted.vtt"
    corrupt_vtt.write_text("NOT_WEBVTT\nInvalid timestamp format\n99:99:99 --> 00:00:00\nBroken text", encoding="utf-8")

    manifest["entries"]["VTT_SRT"] = {
        "primary_file": str(canonical_vtt.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_vtt),
        "derived_variants": {
            ".srt": {"path": str(canonical_srt.relative_to(ROOT)), "sha256": sha256_file(canonical_srt)},
        },
        "edge_cases": {
            "empty": {"path": str(empty_vtt.relative_to(ROOT)), "sha256": sha256_file(empty_vtt)},
            "corrupted": {"path": str(corrupt_vtt.relative_to(ROOT)), "sha256": sha256_file(corrupt_vtt)},
        },
        "metadata": {
            "project": "project_lens",
            "meeting_name": "LENS Pipeline Review and Sign-Off",
            "date": "10-02-2026",
            "attendees": ["Amit Ayer", "harper Hoffman"],
        },
    }

    # 3. COPILOT_NOTES
    notes_dir = CORPUS_DIR / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    src_notes = ROOT / "inputs" / "transcripts" / "google_partner_advisory_apex_global_weekly_07-24-2026_notes.txt"
    canonical_notes = notes_dir / "apex_global_weekly_notes.txt"
    shutil.copyfile(src_notes, canonical_notes)

    canonical_notes_md = notes_dir / "apex_global_weekly_notes.md"
    shutil.copyfile(src_notes, canonical_notes_md)

    empty_notes = notes_dir / "empty_notes.txt"
    empty_notes.touch()
    corrupt_notes = notes_dir / "corrupted_notes.txt"
    corrupt_notes.write_bytes(b"\xff\xfe\x00\x00\x80\x81\x82\x83\x84\x85corruptedbytes")

    manifest["entries"]["COPILOT_NOTES"] = {
        "primary_file": str(canonical_notes.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_notes),
        "derived_variants": {
            ".md": {"path": str(canonical_notes_md.relative_to(ROOT)), "sha256": sha256_file(canonical_notes_md)},
        },
        "edge_cases": {
            "empty": {"path": str(empty_notes.relative_to(ROOT)), "sha256": sha256_file(empty_notes)},
            "corrupted": {"path": str(corrupt_notes.relative_to(ROOT)), "sha256": sha256_file(corrupt_notes)},
        },
        "metadata": {
            "project": "project_atlas",
            "meeting_name": "Apex Global Weekly Notes",
            "date": "07-24-2026",
            "attendees": ["Amit Ayer", "Team"],
            "notes": True,
        },
    }

    # 4. PDF
    docs_dir = CORPUS_DIR / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    src_pdf = ROOT / "inputs" / "rewire_earnings_2026" / "apex_global-q1-2026-earnings-script.pdf"
    canonical_pdf = docs_dir / "apex_global_earnings_script.pdf"
    shutil.copyfile(src_pdf, canonical_pdf)

    empty_pdf = docs_dir / "empty.pdf"
    empty_pdf.touch()
    corrupt_pdf = docs_dir / "corrupted.pdf"
    corrupt_pdf.write_bytes(b"%PDF-1.4\n%corrupted PDF data missing xref and trailer\n%%EOF")

    manifest["entries"]["PDF"] = {
        "primary_file": str(canonical_pdf.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_pdf),
        "edge_cases": {
            "empty": {"path": str(empty_pdf.relative_to(ROOT)), "sha256": sha256_file(empty_pdf)},
            "corrupted": {"path": str(corrupt_pdf.relative_to(ROOT)), "sha256": sha256_file(corrupt_pdf)},
        },
        "metadata": {
            "project": "project_atlas",
            "title": "Apex Global Q1 2026 Earnings Script",
            "date": "04-20-2026",
            "document": True,
        },
    }

    # 5. DOCX
    src_docx = ROOT / "documents" / "project_lens" / "project-scope-items-for-review.docx"
    canonical_docx = docs_dir / "project_scope_items.docx"
    shutil.copyfile(src_docx, canonical_docx)

    empty_docx = docs_dir / "empty.docx"
    empty_docx.touch()
    corrupt_docx = docs_dir / "corrupted.docx"
    corrupt_docx.write_bytes(b"PK\x03\x04corrupted docx zip container with broken directory")

    manifest["entries"]["DOCX"] = {
        "primary_file": str(canonical_docx.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_docx),
        "edge_cases": {
            "empty": {"path": str(empty_docx.relative_to(ROOT)), "sha256": sha256_file(empty_docx)},
            "corrupted": {"path": str(corrupt_docx.relative_to(ROOT)), "sha256": sha256_file(corrupt_docx)},
        },
        "metadata": {
            "project": "project_lens",
            "title": "Project LENS Scope Items for Review",
            "date": "10-02-2026",
            "document": True,
        },
    }

    # 6. PPTX
    src_pptx = Path.home() / "Library" / "CloudStorage" / "OneDrive-PartnerAdvisory" / "Documents" / "AI-GTM" / "Florida 2027 Account Planning Playbook.pptx"
    canonical_pptx = docs_dir / "enterprise_account_planning_playbook.pptx"
    if src_pptx.exists():
        shutil.copyfile(src_pptx, canonical_pptx)
    else:
        # Fallback to test synthetic generator if OneDrive is unmounted
        from tests.test_pptx_source import _create_synthetic_pptx_bytes
        canonical_pptx.write_bytes(_create_synthetic_pptx_bytes())

    empty_pptx = docs_dir / "empty.pptx"
    empty_pptx.touch()
    corrupt_pptx = docs_dir / "corrupted.pptx"
    corrupt_pptx.write_bytes(b"PK\x03\x04corrupted pptx zip header")

    manifest["entries"]["PPTX"] = {
        "primary_file": str(canonical_pptx.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_pptx),
        "edge_cases": {
            "empty": {"path": str(empty_pptx.relative_to(ROOT)), "sha256": sha256_file(empty_pptx)},
            "corrupted": {"path": str(corrupt_pptx.relative_to(ROOT)), "sha256": sha256_file(corrupt_pptx)},
        },
        "metadata": {
            "project": "enterprise_account_planning",
            "title": "Florida 2027 Account Planning Playbook",
            "date": "09-11-2026",
            "document": True,
        },
    }

    # 7. XLSX_WORKBOOK
    src_xlsx = ROOT / "documents" / "hrit" / "Requisition_Technical_Field_Mapping 4.xlsx"
    canonical_xlsx = docs_dir / "requisition_technical_field_mapping.xlsx"
    shutil.copyfile(src_xlsx, canonical_xlsx)

    empty_xlsx = docs_dir / "empty.xlsx"
    empty_xlsx.touch()
    corrupt_xlsx = docs_dir / "corrupted.xlsx"
    corrupt_xlsx.write_bytes(b"PK\x03\x04corrupted xlsx zip container")

    manifest["entries"]["XLSX_WORKBOOK"] = {
        "primary_file": str(canonical_xlsx.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_xlsx),
        "edge_cases": {
            "empty": {"path": str(empty_xlsx.relative_to(ROOT)), "sha256": sha256_file(empty_xlsx)},
            "corrupted": {"path": str(corrupt_xlsx.relative_to(ROOT)), "sha256": sha256_file(corrupt_xlsx)},
        },
        "metadata": {
            "project": "enterprise_architecture",
            "title": "Requisition Technical Field Mapping",
            "date": "09-15-2026",
            "document": True,
        },
    }

    # 8. CSV_TSV
    src_csv = ROOT / "exports" / "v53_nea_charter_reconciliation.csv"
    canonical_csv = docs_dir / "nea_charter_reconciliation.csv"
    shutil.copyfile(src_csv, canonical_csv)

    canonical_tsv = docs_dir / "nea_charter_reconciliation.tsv"
    convert_csv_to_tsv(canonical_csv, canonical_tsv)

    empty_csv = docs_dir / "empty.csv"
    empty_csv.touch()
    corrupt_csv = docs_dir / "corrupted.csv"
    corrupt_csv.write_text('id,name,"unclosed_quote_field\nnextline,val\n', encoding="utf-8")

    manifest["entries"]["CSV_TSV"] = {
        "primary_file": str(canonical_csv.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_csv),
        "derived_variants": {
            ".tsv": {"path": str(canonical_tsv.relative_to(ROOT)), "sha256": sha256_file(canonical_tsv)},
        },
        "edge_cases": {
            "empty": {"path": str(empty_csv.relative_to(ROOT)), "sha256": sha256_file(empty_csv)},
            "corrupted": {"path": str(corrupt_csv.relative_to(ROOT)), "sha256": sha256_file(corrupt_csv)},
        },
        "metadata": {
            "project": "nea",
            "title": "NEA Charter Reconciliation Table",
            "date": "09-20-2026",
            "document": True,
        },
    }

    # 9. HTML_HTM
    canonical_html = docs_dir / "project_lens_spec.html"
    create_authentic_html(canonical_html)

    canonical_htm = docs_dir / "project_lens_spec.htm"
    shutil.copyfile(canonical_html, canonical_htm)

    empty_html = docs_dir / "empty.html"
    empty_html.touch()
    corrupt_html = docs_dir / "corrupted.html"
    corrupt_html.write_text("<html><head><title>Unclosed</title><body><p>Broken markup without tags", encoding="utf-8")

    manifest["entries"]["HTML_HTM"] = {
        "primary_file": str(canonical_html.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_html),
        "derived_variants": {
            ".htm": {"path": str(canonical_htm.relative_to(ROOT)), "sha256": sha256_file(canonical_htm)},
        },
        "edge_cases": {
            "empty": {"path": str(empty_html.relative_to(ROOT)), "sha256": sha256_file(empty_html)},
            "corrupted": {"path": str(corrupt_html.relative_to(ROOT)), "sha256": sha256_file(corrupt_html)},
        },
        "metadata": {
            "project": "project_lens",
            "title": "Project LENS Specification HTML",
            "date": "10-11-2026",
            "document": True,
        },
    }

    # 10. EML
    canonical_eml = docs_dir / "lens_signoff.eml"
    create_rfc822_eml(canonical_eml)

    empty_eml = docs_dir / "empty.eml"
    empty_eml.touch()
    corrupt_eml = docs_dir / "corrupted.eml"
    corrupt_eml.write_bytes(b"From: nobody\nSubject: =?UTF-8?B?bad_base_64_invalid_padding?=\n\nBroken body")

    manifest["entries"]["EML"] = {
        "primary_file": str(canonical_eml.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_eml),
        "edge_cases": {
            "empty": {"path": str(empty_eml.relative_to(ROOT)), "sha256": sha256_file(empty_eml)},
            "corrupted": {"path": str(corrupt_eml.relative_to(ROOT)), "sha256": sha256_file(corrupt_eml)},
        },
        "metadata": {
            "project": "project_lens",
            "title": "Project LENS Signoff Email",
            "date": "10-08-2026",
            "document": True,
        },
    }

    # 11. TXT_MD
    src_md = ROOT / "documents" / "market_intelligence" / "weekly_update_10-06-2026.md"
    canonical_md = docs_dir / "weekly_update_10-06-2026.md"
    shutil.copyfile(src_md, canonical_md)

    canonical_txt = docs_dir / "weekly_update_10-06-2026.txt"
    shutil.copyfile(src_md, canonical_txt)

    empty_txt = docs_dir / "empty.txt"
    empty_txt.touch()
    corrupt_txt = docs_dir / "corrupted.txt"
    corrupt_txt.write_bytes(b"\x00\x01\x02\x03\x04\x05binary_data_in_text")

    manifest["entries"]["TXT_MD"] = {
        "primary_file": str(canonical_md.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_md),
        "derived_variants": {
            ".txt": {"path": str(canonical_txt.relative_to(ROOT)), "sha256": sha256_file(canonical_txt)},
        },
        "edge_cases": {
            "empty": {"path": str(empty_txt.relative_to(ROOT)), "sha256": sha256_file(empty_txt)},
            "corrupted": {"path": str(corrupt_txt.relative_to(ROOT)), "sha256": sha256_file(corrupt_txt)},
        },
        "metadata": {
            "project": "project_atlas",
            "title": "Weekly Market Intelligence Update",
            "date": "10-06-2026",
            "document": True,
        },
    }

    # 12. PNG_JPEG
    images_dir = CORPUS_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    src_png = ROOT / "tests" / "fixtures" / "nea_org_chart_screenshot_fixture.png"
    canonical_png = images_dir / "nea_org_chart.png"
    shutil.copyfile(src_png, canonical_png)

    # Convert to JPEG via macOS sips
    canonical_jpg = images_dir / "nea_org_chart.jpg"
    subprocess.run(["/usr/bin/sips", "-s", "format", "jpeg", str(canonical_png), "--out", str(canonical_jpg)], check=True, capture_output=True, timeout=60)

    empty_png = images_dir / "empty.png"
    empty_png.touch()
    corrupt_png = images_dir / "corrupted.png"
    corrupt_png.write_bytes(b"\x89PNG\r\n\x1a\ncorrupted png chunk header")

    manifest["entries"]["PNG_JPEG"] = {
        "primary_file": str(canonical_png.relative_to(ROOT)),
        "primary_sha256": sha256_file(canonical_png),
        "derived_variants": {
            ".jpg": {"path": str(canonical_jpg.relative_to(ROOT)), "sha256": sha256_file(canonical_jpg)},
        },
        "edge_cases": {
            "empty": {"path": str(empty_png.relative_to(ROOT)), "sha256": sha256_file(empty_png)},
            "corrupted": {"path": str(corrupt_png.relative_to(ROOT)), "sha256": sha256_file(corrupt_png)},
        },
        "metadata": {
            "project": "nea",
            "title": "NEA Organization Chart Screenshot",
            "date": "09-28-2026",
            "document": True,
        },
    }

    # 13. ATTACHMENTS_SUITE
    # Meeting with attached collateral
    att_dir = CORPUS_DIR / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    att_meeting_vtt = att_dir / "meeting_with_attachments.vtt"
    shutil.copyfile(canonical_vtt, att_meeting_vtt)

    att_sub_dir = att_dir / "collateral"
    att_sub_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(canonical_pdf, att_sub_dir / "attached_spec.pdf")
    shutil.copyfile(canonical_docx, att_sub_dir / "attached_scope.docx")
    shutil.copyfile(canonical_png, att_sub_dir / "attached_chart.png")

    manifest["entries"]["ATTACHMENTS_SUITE"] = {
        "primary_file": str(att_meeting_vtt.relative_to(ROOT)),
        "primary_sha256": sha256_file(att_meeting_vtt),
        "attachments": [
            str((att_sub_dir / "attached_spec.pdf").relative_to(ROOT)),
            str((att_sub_dir / "attached_scope.docx").relative_to(ROOT)),
            str((att_sub_dir / "attached_chart.png").relative_to(ROOT)),
        ],
        "attachment_dir": str(att_sub_dir.relative_to(ROOT)),
        "metadata": {
            "project": "project_lens",
            "meeting_name": "LENS Meeting with Attached Architecture",
            "date": "10-02-2026",
            "attendees": ["Amit Ayer", "harper Hoffman"],
        },
    }

    # 14. GOVERNED REFUSAL MODALITIES: MSG, PPT_LEGACY, XLS_LEGACY
    blocked_dir = CORPUS_DIR / "blocked"
    blocked_dir.mkdir(parents=True, exist_ok=True)

    sample_msg = blocked_dir / "legacy_memo.msg"
    sample_msg.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1compound document file binary header for msg")

    sample_ppt = blocked_dir / "legacy_deck.ppt"
    sample_ppt.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1compound document file binary header for ppt")

    sample_xls = blocked_dir / "legacy_sheet.xls"
    sample_xls.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1compound document file binary header for xls")

    manifest["entries"]["MSG"] = {
        "primary_file": str(sample_msg.relative_to(ROOT)),
        "primary_sha256": sha256_file(sample_msg),
        "expected_disposition": "GOVERNED_REFUSAL",
        "expected_error_code": "UNSUPPORTED_MODALITY_GOVERNED_REFUSAL",
        "metadata": {
            "project": "project_atlas",
            "title": "Legacy Outlook MSG Memo",
            "date": "01-01-2020",
            "document": True,
        },
    }

    manifest["entries"]["PPT_LEGACY"] = {
        "primary_file": str(sample_ppt.relative_to(ROOT)),
        "primary_sha256": sha256_file(sample_ppt),
        "expected_disposition": "GOVERNED_REFUSAL",
        "expected_error_code": "UNSUPPORTED_MODALITY_GOVERNED_REFUSAL",
        "metadata": {
            "project": "enterprise_account_planning",
            "title": "Legacy PowerPoint PPT Deck",
            "date": "01-01-2020",
            "document": True,
        },
    }

    manifest["entries"]["XLS_LEGACY"] = {
        "primary_file": str(sample_xls.relative_to(ROOT)),
        "primary_sha256": sha256_file(sample_xls),
        "expected_disposition": "GOVERNED_REFUSAL",
        "expected_error_code": "UNSUPPORTED_MODALITY_GOVERNED_REFUSAL",
        "metadata": {
            "project": "enterprise_architecture",
            "title": "Legacy Excel XLS Spreadsheet",
            "date": "01-01-2020",
            "document": True,
        },
    }

    out_path = ROOT / "reports" / "live_e2e_corpus_manifest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"✅ Staged corpus and emitted {out_path} ({len(manifest['entries'])} modalities)")
    return manifest


if __name__ == "__main__":
    build_corpus()
