"""TeX archive parsing for the document pipeline.

This module converts an arXiv-style ``.tar.gz`` source bundle into the same
``DocumentStructureResult`` shape produced by the PDF document-structure agent.
It intentionally reads archive members in memory instead of extracting them to
disk.
"""
from __future__ import annotations

import io
import os
import posixpath
import re
import tarfile
from dataclasses import dataclass

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)

_TEX_EXT = ".tex"
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
_MAX_MEMBER_BYTES = 10 * 1024 * 1024
_MAX_EXPANDED_CHARS = 2_000_000
_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
_BRACED_ARG = r"((?:[^{}]|\{[^{}]*\})*)"
_SECTION_RE = re.compile(
    rf"\\(?P<section_cmd>section|subsection|subsubsection)\*?(?:\[[^\]]*\])?\{{(?P<section_title>{_BRACED_ARG})\}}"
)
_TITLE_RE = re.compile(rf"\\title(?:\[[^\]]*\])?\{{({_BRACED_ARG})\}}", re.DOTALL)
_AUTHOR_RE = re.compile(rf"\\author(?:\[[^\]]*\])?\{{({_BRACED_ARG})\}}", re.DOTALL)
_BLOCK_ENV_RE = re.compile(
    r"\\begin\{(?P<env>equation\*?|align\*?|eqnarray\*?|gather\*?|multline\*?|figure\*?|table\*?)\}"
    r"(?P<env_body>.*?)"
    r"\\end\{(?P=env)\}",
    re.DOTALL,
)
_DISPLAY_MATH_RE = re.compile(r"(?P<display_math>\$\$.*?\$\$|\\\[.*?\\\])", re.DOTALL)
_CAPTION_RE = re.compile(r"\\caption(?:\[[^\]]*\])?\{([^{}]+)\}", re.DOTALL)
_ABSTRACT_RE = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL)
_TITLEPAGE_RE = re.compile(r"\\begin\{titlepage\}(.*?)\\end\{titlepage\}", re.DOTALL)
_COMMAND_WITH_ARG_RE = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
_COMMAND_RE = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?")


@dataclass(frozen=True)
class TexArchiveSource:
    source_file: str
    expanded_tex: str
    title: str | None
    authors: list[str]


def build_structure_from_tex_archive(
    archive_bytes: bytes,
    *,
    document_id: str,
    source_file: str,
    cartridge_id: str | None = None,
) -> DocumentStructureResult:
    """Build a pipeline document structure from a gzipped tar TeX bundle."""
    source = load_tex_archive(archive_bytes, source_file=source_file)
    blocks, sections = _tex_to_blocks_and_sections(source.expanded_tex, document_id)
    metadata = DocumentMetadata(title=source.title, authors=source.authors, pages=0)
    result = DocumentStructureResult(
        document_id=document_id,
        source_file=source.source_file,
        cartridge_id=cartridge_id,
        metadata=metadata,
        sections=sections,
        blocks=blocks,
    )
    return result


def load_tex_archive(archive_bytes: bytes, *, source_file: str) -> TexArchiveSource:
    if not archive_bytes:
        raise ValueError("empty TeX archive")
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise ValueError("TeX archive is too large")

    members = _read_tex_members(archive_bytes)
    if not members:
        raise ValueError("TeX archive contains no .tex files")

    main_name = _select_main_tex(members)
    expanded = _expand_inputs(main_name, members, seen=set())
    expanded = expanded[:_MAX_EXPANDED_CHARS]
    title = _clean_text(_first_match(_TITLE_RE, expanded)) or _infer_title(expanded)
    authors_raw = _clean_text(_first_match(_AUTHOR_RE, expanded)) or _infer_authors(expanded)
    authors = _split_authors(authors_raw)
    return TexArchiveSource(
        source_file=f"{source_file}:{main_name}",
        expanded_tex=expanded,
        title=title or None,
        authors=authors,
    )


def _read_tex_members(archive_bytes: bytes) -> dict[str, str]:
    members: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile() or not member.name.lower().endswith(_TEX_EXT):
                    continue
                safe_name = _safe_member_name(member.name)
                if safe_name is None:
                    continue
                if member.size > _MAX_MEMBER_BYTES:
                    continue
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                raw = extracted.read(_MAX_MEMBER_BYTES + 1)
                if len(raw) > _MAX_MEMBER_BYTES:
                    continue
                members[safe_name] = _decode_tex(raw)
    except tarfile.TarError as exc:
        raise ValueError("invalid .tar.gz TeX archive") from exc
    return members


def _safe_member_name(name: str) -> str | None:
    normalized = posixpath.normpath(name).lstrip("/")
    if normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def _decode_tex(raw: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _select_main_tex(members: dict[str, str]) -> str:
    scored: list[tuple[int, str]] = []
    for name, text in members.items():
        score = 0
        base = posixpath.basename(name).lower()
        if base == "main.tex":
            score += 50
        if "\\documentclass" in text:
            score += 40
        if "\\begin{document}" in text:
            score += 40
        score += min(len(text) // 2000, 20)
        scored.append((score, name))
    return max(scored, key=lambda item: (item[0], -len(item[1])))[1]


def _expand_inputs(name: str, members: dict[str, str], *, seen: set[str]) -> str:
    if name in seen:
        return ""
    seen.add(name)
    text = _strip_comments(members[name])
    base_dir = posixpath.dirname(name)

    def replace(match: re.Match[str]) -> str:
        ref = match.group(1).strip()
        ref_name = ref if ref.lower().endswith(_TEX_EXT) else f"{ref}.tex"
        candidates = [
            posixpath.normpath(posixpath.join(base_dir, ref_name)),
            posixpath.normpath(ref_name),
        ]
        for candidate in candidates:
            if candidate in members:
                return "\n" + _expand_inputs(candidate, members, seen=seen) + "\n"
        return ""

    return _INPUT_RE.sub(replace, text)


def _strip_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        lines.append(re.sub(r"(?<!\\)%.*", "", line))
    return "\n".join(lines)


def _tex_to_blocks_and_sections(tex: str, document_id: str) -> tuple[list[TypedBlock], list[Section]]:
    body = _document_body(tex)
    blocks: list[TypedBlock] = []
    sections: list[Section] = []
    current_section_id: str | None = None
    order = 0

    def add_block(text: str, block_type: str, *, section_id: str | None = None, raw: dict | None = None) -> None:
        nonlocal order
        cleaned = text.strip()
        if not cleaned:
            return
        blocks.append(
            TypedBlock(
                block_id=f"tex_b{len(blocks) + 1}",
                page=1,
                order=order,
                text=cleaned,
                block_type=block_type,
                confidence=0.85,
                section_id=section_id,
                raw={"parser_source": "tex_archive", **(raw or {})},
            )
        )
        order += 1

    for segment in _segment_tex(body):
        if segment[0] == "section":
            level, title = segment[1], _clean_text(segment[2])
            if not title:
                continue
            section_id = f"sec_{len(sections) + 1}"
            sections.append(
                Section(
                    section_id=section_id,
                    title=title,
                    level=level,
                    order=len(sections),
                    page_start=1,
                    page_end=1,
                )
            )
            current_section_id = section_id
            add_block(title, "section_heading" if level == 1 else "subsection_heading", section_id=section_id)
        elif segment[0] == "equation":
            add_block(_clean_equation(segment[1]), "equation_block", section_id=current_section_id)
        elif segment[0] in {"figure", "table"}:
            caption = _clean_text(_first_match(_CAPTION_RE, segment[1]))
            block_type = "figure_caption" if segment[0] == "figure" else "table_caption"
            if caption:
                add_block(caption, block_type, section_id=current_section_id)
        else:
            for paragraph in _paragraphs(segment[1]):
                add_block(_clean_text(paragraph), "body_paragraph", section_id=current_section_id)

    if sections and blocks:
        _refresh_section_ranges(sections, blocks)
    return blocks, sections


def _document_body(tex: str) -> str:
    match = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", tex, re.DOTALL)
    body = match.group(1) if match else tex
    titlepage = _TITLEPAGE_RE.search(body)
    if titlepage:
        abstract = _first_match(_ABSTRACT_RE, titlepage.group(1))
        body = body[: titlepage.start()] + ("\n\n" + abstract + "\n\n" if abstract else "\n\n") + body[titlepage.end():]
    return body


def _segment_tex(body: str) -> list[tuple]:
    segments: list[tuple] = []
    pos = 0
    combined = re.compile(
        rf"{_SECTION_RE.pattern}|{_BLOCK_ENV_RE.pattern}|{_DISPLAY_MATH_RE.pattern}",
        re.DOTALL,
    )
    for match in combined.finditer(body):
        if match.start() > pos:
            segments.append(("text", body[pos:match.start()]))
        if match.group("section_cmd"):
            level = {"section": 1, "subsection": 2, "subsubsection": 3}[match.group("section_cmd")]
            segments.append(("section", level, match.group("section_title")))
        elif match.group("env"):
            env = match.group("env").rstrip("*")
            kind = "figure" if env == "figure" else "table" if env == "table" else "equation"
            segments.append((kind, match.group("env_body")))
        else:
            segments.append(("equation", match.group("display_math")))
        pos = match.end()
    if pos < len(body):
        segments.append(("text", body[pos:]))
    return segments


def _paragraphs(text: str) -> list[str]:
    text = re.sub(r"\\(?:maketitle|tableofcontents|bibliography|bibliographystyle)\b(?:\{[^{}]*\})?", "\n\n", text)
    return [part for part in re.split(r"\n\s*\n+", text) if part.strip()]


def _clean_equation(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^\$\$|\$\$$", "", cleaned)
    cleaned = re.sub(r"^\\\[|\\\]$", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"\\(?:label|ref|eqref|cite|citep|citet|url|href)\*?(?:\[[^\]]*\])?\{[^{}]*\}", "", text)
    text = re.sub(r"\\(?:vspace|vskip|hspace|setcounter|renewcommand|newcommand)\b(?:\[[^\]]*\])?(?:\{[^{}]*\})*", " ", text)
    text = re.sub(r"\$([^$]+)\$", r"\1", text)
    previous = None
    while previous != text:
        previous = text
        text = _COMMAND_WITH_ARG_RE.sub(r"\1", text)
    text = _COMMAND_RE.sub("", text)
    text = text.replace("\\&", "&").replace("~", " ")
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def _split_authors(authors_raw: str) -> list[str]:
    if not authors_raw:
        return []
    parts = re.split(r"\s+(?:and|AND)\s+|\\\\|;", authors_raw)
    return [part.strip() for part in parts if part.strip()]


def _infer_title(tex: str) -> str | None:
    titlepage = _first_match(_TITLEPAGE_RE, tex)
    if not titlepage:
        return None
    patterns = [
        r"\{\\Large\s+\\bf\s+([^{}]+)\}",
        r"\{\\LARGE\s+\\bf\s+([^{}]+)\}",
        r"\\bfseries\s+([^\\{}]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, titlepage, re.DOTALL)
        if match:
            title = _clean_text(match.group(1))
            if title:
                return title
    return None


def _infer_authors(tex: str) -> str:
    titlepage = _first_match(_TITLEPAGE_RE, tex)
    if not titlepage:
        return ""
    name_matches = re.findall(r"\b([A-Z][A-Za-z.-]+)~+([A-Z][A-Za-z.-]+)\b", titlepage)
    if name_matches:
        return "; ".join(f"{first} {last}" for first, last in name_matches)
    match = re.search(r"\{\\large\s+(.*?)\}", titlepage, re.DOTALL)
    if not match:
        return ""
    authors = re.sub(r"\$.*?\$", "", match.group(1))
    authors = re.sub(r"\\\\(?:\[[^\]]*\])?", ";", authors)
    authors = authors.replace("~", " ")
    authors = re.sub(r"\band\b", ";", authors)
    authors = authors.replace(",", ";")
    return _clean_text(authors)


def _refresh_section_ranges(sections: list[Section], blocks: list[TypedBlock]) -> None:
    block_orders = {b.section_id: b.order for b in blocks if b.section_id}
    ordered = sorted(sections, key=lambda section: block_orders.get(section.section_id, section.order))
    for idx, section in enumerate(ordered):
        section.order = idx
        section.page_start = 1
        section.page_end = 1
