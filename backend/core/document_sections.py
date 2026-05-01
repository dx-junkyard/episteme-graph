"""Document-level section reconstruction utilities."""

from __future__ import annotations

import re
from typing import Any


_NUMBERED_HEADING_RE = re.compile(r"^((?:[1-9]\d*)(?:\.[1-9]\d*)*)\s+(.{3,140})$")
_APPENDIX_HEADING_RE = re.compile(r"^(appendix)\s+([A-Z])(?:[.:]?\s+(.{0,120}))?$", re.IGNORECASE)


def detect_section_heading(text: str) -> dict[str, Any] | None:
    for line in (text or "").splitlines()[:14]:
        line = line.strip()
        if not line:
            continue
        numbered = _NUMBERED_HEADING_RE.match(line)
        if numbered:
            title = numbered.group(2).strip()
            lower = title.lower()
            if lower.startswith(("arxiv", "jhep", "doi", "received", "accepted", "published")):
                continue
            number = numbered.group(1)
            return {
                "number": number,
                "title": f"{number} {title}",
                "level": number.count(".") + 1,
            }
        appendix = _APPENDIX_HEADING_RE.match(line)
        if appendix:
            suffix = appendix.group(3).strip() if appendix.group(3) else ""
            title = f"Appendix {appendix.group(2)}" + (f" {suffix}" if suffix else "")
            return {"number": f"appendix_{appendix.group(2).lower()}", "title": title, "level": 1}
    return None


def section_id_from_heading(document_id: str, heading: dict[str, Any]) -> str:
    number = str(heading.get("number") or "section").lower()
    sec_num = re.sub(r"[^0-9a-z]+", "_", number).strip("_")
    return f"{document_id}:sec_{sec_num or 'section'}"


def enrich_chunks_with_sections(chunks: list[dict]) -> list[dict]:
    """Assign inherited section metadata to chunks within each document."""
    current_by_doc: dict[str, dict[str, Any]] = {}
    order_by_doc: dict[str, int] = {}
    enriched: list[dict] = []
    sorted_chunks = sorted(chunks, key=lambda c: (str(c.get("document_id") or c.get("material_id") or ""), int(c.get("chunk_index") or 0)))
    for chunk in sorted_chunks:
        out = dict(chunk)
        document_id = str(out.get("document_id") or out.get("material_id") or "document")
        heading = detect_section_heading(out.get("raw_text") or out.get("text") or "")
        if heading:
            order_by_doc[document_id] = order_by_doc.get(document_id, 0) + 1
            current_by_doc[document_id] = {
                "section_id": section_id_from_heading(document_id, heading),
                "section_title": heading["title"],
                "section_level": int(heading.get("level") or 1),
                "section_order": order_by_doc[document_id],
            }
        section = current_by_doc.get(document_id)
        if not section:
            order_by_doc.setdefault(document_id, 0)
            group = max(1, (int(out.get("chunk_index") or 0) // 8) + 1)
            section = {
                "section_id": f"{document_id}:chunk_group_{group}",
                "section_title": f"Unsectioned chunk group {group}",
                "section_level": 0,
                "section_order": group,
            }
        out.update(section)
        enriched.append(out)
    return enriched


def build_document_structure(chunks: list[dict]) -> list[dict]:
    sections: dict[str, dict] = {}
    for chunk in enrich_chunks_with_sections(chunks):
        section_id = chunk.get("section_id") or ""
        if not section_id:
            continue
        sections.setdefault(section_id, {
            "section_id": section_id,
            "section_title": chunk.get("section_title") or section_id,
            "section_level": chunk.get("section_level") or 0,
            "section_order": chunk.get("section_order") or 0,
            "chunks": [],
        })
        sections[section_id]["chunks"].append(chunk.get("id") or chunk.get("chunk_id"))
    return sorted(sections.values(), key=lambda item: (item.get("section_order") or 0, item.get("section_id") or ""))
