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
_BIB_EXT = ".bib"
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
_LABEL_RE = re.compile(r"\\label\s*\{([^{}]+)\}")
_REF_RE = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref)\s*\{([^{}]+)\}")
_CITE_RE = re.compile(r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear)\*?(?:\[[^\]]*\]){0,2}\s*\{([^{}]+)\}")
_BIB_ENTRY_RE = re.compile(r"@(?P<entry_type>[A-Za-z]+)\s*\{\s*(?P<key>[^,\s]+)\s*,(?P<body>.*?)(?=^@\w+\s*\{|\Z)", re.DOTALL | re.MULTILINE)
_BIB_FIELD_RE = re.compile(r"(?P<field>[A-Za-z][A-Za-z0-9_-]*)\s*=\s*(?P<value>\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,\n]+)", re.DOTALL)
_ABSTRACT_RE = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL)
_TITLEPAGE_RE = re.compile(r"\\begin\{titlepage\}(.*?)\\end\{titlepage\}", re.DOTALL)
_COMMAND_WITH_ARG_RE = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
_COMMAND_RE = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?")
_CONTROL_SEQUENCE_RE = re.compile(r"\\(?:[A-Za-z@]+|.)")
_MATH_LAYOUT_COMMAND_RE = re.compile(r"\\hspace\*?\s*\{[^{}]*\}")
_MATH_REF_COMMAND_RE = re.compile(r"\\(?P<cmd>eqref|ref|autoref|cref|Cref)\s*\{(?P<key>[^{}]+)\}")
_MACRO_DEF_COMMANDS = ("def", "gdef", "edef", "xdef")
_NEWCOMMAND_NAMES = ("newcommand", "renewcommand", "providecommand")
_MAX_SIMPLE_MACROS = 200
_MAX_MACRO_EXPANSION_PASSES = 8
_ALIGNMENT_ENVS = {"align", "eqnarray", "gather", "multline"}


@dataclass(frozen=True)
class TexMacro:
    name: str
    replacement: str
    arg_count: int = 0


@dataclass(frozen=True)
class TexArchiveSource:
    source_file: str
    expanded_tex: str
    title: str | None
    authors: list[str]
    bibliography: dict[str, dict]


def build_structure_from_tex_archive(
    archive_bytes: bytes,
    *,
    document_id: str,
    source_file: str,
    cartridge_id: str | None = None,
) -> DocumentStructureResult:
    """Build a pipeline document structure from a gzipped tar TeX bundle."""
    source = load_tex_archive(archive_bytes, source_file=source_file)
    blocks, sections = _tex_to_blocks_and_sections(
        source.expanded_tex, document_id, bibliography=source.bibliography
    )
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

    members, bib_members = _read_archive_members(archive_bytes)
    if not members:
        raise ValueError("TeX archive contains no .tex files")

    main_name = _select_main_tex(members)
    expanded = _expand_inputs(main_name, members, seen=set())
    expanded = expanded[:_MAX_EXPANDED_CHARS]
    title = _clean_text(_first_match(_TITLE_RE, expanded)) or _infer_title(expanded)
    authors_raw = _clean_text(_first_match(_AUTHOR_RE, expanded)) or _infer_authors(expanded)
    authors = _split_authors(authors_raw)
    bibliography = _parse_bibliography(bib_members)
    return TexArchiveSource(
        source_file=f"{source_file}:{main_name}",
        expanded_tex=expanded,
        title=title or None,
        authors=authors,
        bibliography=bibliography,
    )


def _read_archive_members(archive_bytes: bytes) -> tuple[dict[str, str], dict[str, str]]:
    tex_members: dict[str, str] = {}
    bib_members: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                lower_name = member.name.lower()
                if not (lower_name.endswith(_TEX_EXT) or lower_name.endswith(_BIB_EXT)):
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
                if lower_name.endswith(_TEX_EXT):
                    tex_members[safe_name] = _decode_tex(raw)
                else:
                    bib_members[safe_name] = _decode_tex(raw)
    except tarfile.TarError as exc:
        raise ValueError("invalid .tar.gz TeX archive") from exc
    return tex_members, bib_members


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


def _tex_to_blocks_and_sections(
    tex: str,
    document_id: str,
    *,
    bibliography: dict[str, dict] | None = None,
) -> tuple[list[TypedBlock], list[Section]]:
    macros = _extract_simple_macros(tex)
    body = _document_body(tex)
    blocks: list[TypedBlock] = []
    sections: list[Section] = []
    current_section_id: str | None = None
    order = 0

    def add_block(
        text: str,
        block_type: str,
        *,
        section_id: str | None = None,
        raw: dict | None = None,
        equation_label: str | None = None,
    ) -> None:
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
                equation_label=equation_label,
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
            raw_equation = segment[1]
            env = segment[2] if len(segment) > 2 else None
            label = _extract_equation_label(raw_equation)
            latex = _clean_equation(raw_equation, macros=macros, env=env)
            metadata = _tex_reference_metadata(raw_equation, bibliography)
            add_block(
                latex,
                "equation_block",
                section_id=current_section_id,
                raw={"latex": latex, "extraction_source": "tex_source", **metadata},
                equation_label=label,
            )
        elif segment[0] in {"figure", "table"}:
            caption = _clean_text(_first_match(_CAPTION_RE, segment[1]))
            block_type = "figure_caption" if segment[0] == "figure" else "table_caption"
            if caption:
                add_block(caption, block_type, section_id=current_section_id)
        else:
            for paragraph in _paragraphs(segment[1]):
                add_block(
                    _clean_text(paragraph),
                    "body_paragraph",
                    section_id=current_section_id,
                    raw=_tex_reference_metadata(paragraph, bibliography),
                )

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
            segments.append((kind, match.group("env_body"), env))
        else:
            segments.append(("equation", match.group("display_math")))
        pos = match.end()
    if pos < len(body):
        segments.append(("text", body[pos:]))
    return segments


def _paragraphs(text: str) -> list[str]:
    text = re.sub(r"\\(?:maketitle|tableofcontents|bibliography|bibliographystyle)\b(?:\{[^{}]*\})?", "\n\n", text)
    return [part for part in re.split(r"\n\s*\n+", text) if part.strip()]


def _clean_equation(text: str, *, macros: dict[str, TexMacro] | None = None, env: str | None = None) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^\$\$|\$\$$", "", cleaned)
    cleaned = re.sub(r"^\\\[|\\\]$", "", cleaned)
    cleaned = _LABEL_RE.sub("", cleaned)
    cleaned = _expand_simple_macros(cleaned, macros or {})
    cleaned = _replace_math_refs(cleaned)
    cleaned = _MATH_LAYOUT_COMMAND_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _renderable_equation_latex(cleaned, env=env)


def _replace_math_refs(latex: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group("key").strip()
        if not key:
            return ""
        if match.group("cmd") == "eqref":
            return f"({key})"
        return key

    return _MATH_REF_COMMAND_RE.sub(replace, latex or "")


def _extract_simple_macros(tex: str) -> dict[str, TexMacro]:
    """Collect simple TeX macros that can be expanded with bounded parsing.

    This intentionally handles only zero-argument and one-required-argument
    definitions. More complex TeX macros are skipped to avoid corrupting source
    math.
    """
    macros: dict[str, TexMacro] = {}
    pos = 0
    while len(macros) < _MAX_SIMPLE_MACROS:
        idx = tex.find("\\", pos)
        if idx == -1:
            break
        command, after = _read_control_sequence(tex, idx)
        if not command:
            pos = idx + 1
            continue
        name = command[1:]
        if name in _MACRO_DEF_COMMANDS:
            parsed = _parse_def_macro(tex, after)
        elif name in _NEWCOMMAND_NAMES:
            parsed = _parse_newcommand_macro(tex, after)
        else:
            parsed = None
        if parsed:
            macro, end = parsed
            if _is_safe_simple_macro(macro):
                macros[macro.name] = macro
            pos = end
        else:
            pos = after
    return macros


def _expand_simple_macros(latex: str, macros: dict[str, TexMacro]) -> str:
    if not latex or not macros:
        return latex
    expanded = latex
    for _ in range(_MAX_MACRO_EXPANSION_PASSES):
        changed = False
        parts: list[str] = []
        pos = 0
        for match in _CONTROL_SEQUENCE_RE.finditer(expanded):
            macro = macros.get(match.group(0))
            if macro is None or match.start() < pos:
                continue
            replacement = macro.replacement
            end = match.end()
            if macro.arg_count == 1:
                arg_start = _skip_ws(expanded, end)
                if arg_start >= len(expanded) or expanded[arg_start] != "{":
                    continue
                arg, arg_end = _read_balanced_braced(expanded, arg_start)
                if arg is None:
                    continue
                replacement = replacement.replace("#1", arg)
                end = arg_end
            parts.append(expanded[pos:match.start()])
            parts.append(replacement)
            pos = end
            changed = True
        if not changed:
            break
        parts.append(expanded[pos:])
        expanded = "".join(parts)
    return expanded


def _parse_def_macro(text: str, pos: int) -> tuple[TexMacro, int] | None:
    pos = _skip_ws(text, pos)
    macro_name, pos = _read_control_sequence(text, pos)
    if not macro_name:
        return None
    pos = _skip_ws(text, pos)
    arg_count = 0
    if pos < len(text) and text[pos] == "#":
        if pos + 1 >= len(text) or text[pos + 1] != "1":
            return None
        arg_count = 1
        pos = _skip_ws(text, pos + 2)
        if pos < len(text) and text[pos] == "#":
            return None
    if pos >= len(text) or text[pos] != "{":
        return None
    replacement, end = _read_balanced_braced(text, pos)
    if replacement is None:
        return None
    return TexMacro(macro_name, replacement.strip(), arg_count), end


def _parse_newcommand_macro(text: str, pos: int) -> tuple[TexMacro, int] | None:
    pos = _skip_ws(text, pos)
    if pos < len(text) and text[pos] == "*":
        pos = _skip_ws(text, pos + 1)
    if pos < len(text) and text[pos] == "{":
        macro_token, pos = _read_balanced_braced(text, pos)
        if macro_token is None:
            return None
        macro_name = macro_token.strip()
    else:
        macro_name, pos = _read_control_sequence(text, pos)
    if not macro_name:
        return None
    pos = _skip_ws(text, pos)
    arg_count = 0
    if pos < len(text) and text[pos] == "[":
        arg_text, pos = _read_bracket_arg(text, pos)
        if arg_text is None:
            return None
        arg_text = arg_text.strip()
        if arg_text not in ("", "0", "1"):
            return None
        arg_count = int(arg_text or "0")
        pos = _skip_ws(text, pos)
    if pos < len(text) and text[pos] == "[":
        return None
    if pos >= len(text) or text[pos] != "{":
        return None
    replacement, end = _read_balanced_braced(text, pos)
    if replacement is None:
        return None
    return TexMacro(macro_name, replacement.strip(), arg_count), end


def _read_control_sequence(text: str, pos: int) -> tuple[str | None, int]:
    if pos >= len(text) or text[pos] != "\\":
        return None, pos
    match = _CONTROL_SEQUENCE_RE.match(text, pos)
    if not match:
        return None, pos
    return match.group(0), match.end()


def _read_balanced_braced(text: str, pos: int) -> tuple[str | None, int]:
    if pos >= len(text) or text[pos] != "{":
        return None, pos
    depth = 0
    escaped = False
    for idx in range(pos, len(text)):
        char = text[idx]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[pos + 1:idx], idx + 1
    return None, pos


def _read_bracket_arg(text: str, pos: int) -> tuple[str | None, int]:
    if pos >= len(text) or text[pos] != "[":
        return None, pos
    end = text.find("]", pos + 1)
    if end == -1:
        return None, pos
    return text[pos + 1:end], end + 1


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _is_safe_simple_macro(macro: TexMacro) -> bool:
    if not macro.name.startswith("\\"):
        return False
    if macro.arg_count not in (0, 1):
        return False
    invalid_arg_refs = re.sub(r"#1", "", macro.replacement)
    if "#" in invalid_arg_refs:
        return False
    if macro.arg_count == 0 and "#1" in macro.replacement:
        return False
    if len(macro.name) > 80 or len(macro.replacement) > 500:
        return False
    if re.search(r"\\(?:def|gdef|edef|xdef|newcommand|renewcommand|providecommand)\b", macro.replacement):
        return False
    return True


def _renderable_equation_latex(latex: str, *, env: str | None = None) -> str:
    """Keep TeX source-backed equations renderable after stripping env wrappers."""
    cleaned = (latex or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"\\(?:nonumber|notag)\b", "", cleaned).strip()
    has_environment = bool(re.search(r"\\begin\{[^{}]+\}", cleaned))
    has_alignment = bool(re.search(r"(?<!\\)&", cleaned) or re.search(r"\\\\", cleaned))
    if env in _ALIGNMENT_ENVS:
        return rf"\begin{{aligned}} {cleaned} \end{{aligned}}"
    if has_alignment and not has_environment:
        return rf"\begin{{aligned}} {cleaned} \end{{aligned}}"
    return cleaned


def _extract_equation_label(text: str) -> str | None:
    match = _LABEL_RE.search(text)
    return match.group(1).strip() if match else None


def _split_tex_keys(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _tex_reference_metadata(text: str, bibliography: dict[str, dict] | None = None) -> dict:
    labels = [m.group(1).strip() for m in _LABEL_RE.finditer(text or "") if m.group(1).strip()]
    refs = [
        {"key": key, "kind": "ref"}
        for match in _REF_RE.finditer(text or "")
        for key in _split_tex_keys(match.group(1))
    ]
    citations = []
    bibliography = bibliography or {}
    for match in _CITE_RE.finditer(text or ""):
        for key in _split_tex_keys(match.group(1)):
            item = {"key": key}
            if key in bibliography:
                item["bib"] = bibliography[key]
            citations.append(item)
    metadata: dict = {}
    if labels:
        metadata["labels"] = labels
    if refs:
        metadata["refs"] = refs
    if citations:
        metadata["citations"] = citations
    return metadata


def _parse_bibliography(bib_members: dict[str, str]) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for source_file, text in bib_members.items():
        for match in _BIB_ENTRY_RE.finditer(text or ""):
            key = match.group("key").strip()
            if not key:
                continue
            fields: dict[str, str] = {}
            for field_match in _BIB_FIELD_RE.finditer(match.group("body") or ""):
                field = field_match.group("field").strip().lower()
                value = _clean_bib_value(field_match.group("value"))
                if field and value:
                    fields[field] = value
            entries[key] = {
                "key": key,
                "entry_type": match.group("entry_type").strip().lower(),
                "source_file": source_file,
                **fields,
            }
    return entries


def _clean_bib_value(value: str) -> str:
    value = (value or "").strip().rstrip(",").strip()
    if (value.startswith("{") and value.endswith("}")) or (value.startswith('"') and value.endswith('"')):
        value = value[1:-1]
    value = re.sub(r"\s+", " ", value)
    return value.strip()


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
