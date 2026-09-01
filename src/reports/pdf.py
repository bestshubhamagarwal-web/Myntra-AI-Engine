"""Minimal PDF writer (stdlib). Charts are drawn from the same snapshot JSON."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence


def _pdf_escape(text: str) -> str:
    cleaned = (
        (text or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )
    return cleaned


def _wrap(text: str, width: int = 92) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def write_report_pdf(
    path: Path,
    *,
    title: str,
    header_lines: Sequence[str],
    narrative: str,
    top_themes: Sequence[dict[str, Any]],
    period: str,
) -> None:
    """Bar widths use share_of_voice from the same snapshot as the narrative."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [title, ""]
    lines.extend(header_lines)
    lines.append("")
    lines.append("Narrative")
    lines.extend(_wrap(narrative, 95))
    lines.append("")
    lines.append(f"Top opportunity areas ({period}) — from theme_metrics snapshot")
    max_sov = max((float(row.get("share_of_voice") or 0) for row in top_themes), default=0.0)
    max_sov = max(max_sov, 1e-6)

    page_w, page_h = 612, 792
    y = page_h - 50
    content_ops: list[str] = ["BT /F1 12 Tf 50 742 Td 14 TL"]
    for line in lines:
        content_ops.append(f"({_pdf_escape(line)[:120]}) Tj T*")
        y -= 14
        if y < 220:
            break
    content_ops.append("ET")

    bar_y = 180
    for row in list(top_themes)[:5]:
        sov = float(row.get("share_of_voice") or 0)
        width = max(4, int(400 * (sov / max_sov)))
        content_ops.append(f"0.2 0.35 0.55 rg 50 {bar_y} {width} 12 re f")
        label = f"{str(row.get('name') or row.get('theme_id', ''))[:40]} n={row.get('mention_count')}"
        content_ops.append(
            f"BT /F1 8 Tf 50 {bar_y + 14} Td ({_pdf_escape(label)}) Tj ET"
        )
        bar_y -= 32

    stream = "\n".join(content_ops).encode("latin-1", "replace")
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    path.write_bytes(bytes(out))
