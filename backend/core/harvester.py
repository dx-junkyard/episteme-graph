"""arXiv paper harvester with commercial-publisher filtering."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
import xmltodict

from core.storage import StorageManager

ARXIV_API = "http://export.arxiv.org/api/query"

# Publishers whose papers are NOT open-access by default.
COMMERCIAL_PUBLISHERS = [
    "elsevier",
    "springer",
    "nature publishing",
    "ieee",
    "wiley",
    "taylor & francis",
    "sage publications",
    "american chemical society",
]

_COMMERCIAL_RE = re.compile("|".join(COMMERCIAL_PUBLISHERS), re.IGNORECASE)


@dataclass
class PaperMeta:
    """Lightweight metadata for an arXiv paper."""

    arxiv_id: str
    title: str
    authors: list[str]
    summary: str
    categories: list[str]
    pdf_url: str
    published: str
    license: str = ""
    commercial_flag: bool = False


def _extract_id(id_url: str) -> str:
    """Extract the bare arXiv ID from the full URL."""
    return id_url.rstrip("/").split("/abs/")[-1]


def _is_commercial(entry: dict) -> bool:
    """Heuristic check: does the entry mention a known commercial publisher?"""
    blob = str(entry).lower()
    return bool(_COMMERCIAL_RE.search(blob))


def search_arxiv(query: str, max_results: int = 20) -> list[PaperMeta]:
    """Search arXiv and return parsed metadata, annotating commercial papers."""
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    resp = requests.get(ARXIV_API, params=params, timeout=30)
    resp.raise_for_status()

    parsed = xmltodict.parse(resp.text)
    feed = parsed.get("feed", {})
    entries = feed.get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]

    results: list[PaperMeta] = []
    for entry in entries:
        # Extract authors
        authors_raw = entry.get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = [a.get("name", "") for a in authors_raw]

        # Extract categories
        cats_raw = entry.get("category", [])
        if isinstance(cats_raw, dict):
            cats_raw = [cats_raw]
        categories = [c.get("@term", "") for c in cats_raw]

        # Extract PDF link
        links = entry.get("link", [])
        if isinstance(links, dict):
            links = [links]
        pdf_url = ""
        for link in links:
            if link.get("@title") == "pdf":
                pdf_url = link.get("@href", "")
                break

        arxiv_id = _extract_id(entry.get("id", ""))
        license_url = entry.get("arxiv:license", {})
        if isinstance(license_url, dict):
            license_url = license_url.get("#text", "") or license_url.get("@href", "")
        elif not isinstance(license_url, str):
            license_url = ""

        meta = PaperMeta(
            arxiv_id=arxiv_id,
            title=entry.get("title", "").replace("\n", " ").strip(),
            authors=authors,
            summary=entry.get("summary", "").strip(),
            categories=categories,
            pdf_url=pdf_url,
            published=entry.get("published", ""),
            license=str(license_url),
            commercial_flag=_is_commercial(entry),
        )
        results.append(meta)

    return results


def fetch_and_store(meta: PaperMeta, storage: StorageManager) -> str:
    """Download the PDF for *meta* and upload it to MinIO.

    Returns the MinIO object name.
    """
    if not meta.pdf_url:
        raise ValueError(f"No PDF URL for {meta.arxiv_id}")

    resp = requests.get(meta.pdf_url, timeout=60)
    resp.raise_for_status()

    year = meta.published[:4] if meta.published else "unknown"
    safe_id = meta.arxiv_id.replace("/", "_")
    object_name = f"arxiv/{year}/{safe_id}.pdf"

    storage.upload_pdf("raw-papers", object_name, resp.content)
    return object_name


def store_uploaded_pdf(
    pdf_data: bytes,
    filename: str,
    storage: StorageManager,
) -> tuple[str, str]:
    """Store a user-uploaded PDF in MinIO.

    Returns (paper_id, object_name).
    """
    safe_name = re.sub(r"[^\w.\-]", "_", filename.rsplit(".", 1)[0])
    paper_id = f"upload_{safe_name}"
    object_name = f"uploads/{paper_id}.pdf"

    storage.upload_pdf("raw-papers", object_name, pdf_data)
    return paper_id, object_name
