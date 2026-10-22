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
import unicodedata
import zlib
from dataclasses import dataclass

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.document_structure.author_extraction import (
    extract_authors_from_tex,
)

_TEX_EXT = ".tex"
_BIB_EXT = ".bib"
_STY_EXT = ".sty"
_USEPACKAGE_RE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}")
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
_MAX_MEMBER_BYTES = 10 * 1024 * 1024
#: tar を走査するときに読み進めてよい展開後バイト数の上限（gzip 爆弾で CPU を焼かない）。
#: 個々の member は :data:`_MAX_MEMBER_BYTES` で縛る。ここは「読み飛ばし」も含めた総量。
_MAX_TAR_SCAN_BYTES = 200 * 1024 * 1024
#: tar に並べてよい member 数の上限（ヘッダだけの爆弾で TarInfo を溜め込まない）。
_MAX_ARCHIVE_MEMBERS = 5_000
_MAX_EXPANDED_CHARS = 2_000_000
_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
_BRACED_ARG = r"((?:[^{}]|\{[^{}]*\})*)"
_SECTION_RE = re.compile(
    rf"\\(?P<section_cmd>section|subsection|subsubsection)(?P<section_star>\*?)\s*(?:\[[^\]]*\])?\s*\{{(?P<section_title>{_BRACED_ARG})\}}"
)
#: ``\\paragraph{見出し}``（段落見出し — 節にはしない）と ``\\appendix``（以降の節は付録）。
_PARAGRAPH_RE = re.compile(
    rf"\\(?P<paragraph_cmd>paragraph|subparagraph)\*?\s*(?:\[[^\]]*\])?\s*\{{(?P<paragraph_title>{_BRACED_ARG})\}}"
)
_PARAGRAPH_LABEL_TAIL_RE = re.compile(
    rf"\\(?:sub)?paragraph\*?\s*(?:\[[^\]]*\])?\s*\{{{_BRACED_ARG}\}}\s*$"
)
_APPENDIX_CMD_RE = re.compile(r"\\(?P<appendix_cmd>appendix)\b(?!\s*\{)")
#: 本文ではない後付け（謝辞・資金・参考文献）。役割判定・主張採否の母集合に入れない。
_BACK_MATTER_ENV_RE = re.compile(
    r"\\begin\s*\{(?P<back_env>acknowledgments|acknowledgements|acknowledgment|acknowledgement|thebibliography)\}"
)
_BACK_MATTER_TITLE_RE = re.compile(
    r"^\s*(?:acknowledge?ments?|funding|funding\s+information|financial\s+support|謝辞)\s*[.:]?\s*$",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(rf"\\title(?:\[[^\]]*\])?\{{({_BRACED_ARG})\}}", re.DOTALL)
_AUTHOR_RE = re.compile(rf"\\author(?:\[[^\]]*\])?\{{({_BRACED_ARG})\}}", re.DOTALL)
_BLOCK_ENV_RE = re.compile(
    r"\\begin\{(?P<env>equation\*?|align\*?|eqnarray\*?|gather\*?|multline\*?|flalign\*?|alignat\*?|figure\*?|table\*?)\}"
    r"(?P<env_body>.*?)"
    r"\\end\{(?P=env)\}",
    re.DOTALL,
)
# ``$$`` は直前が空白・行頭・句読点のときだけ表示数式の開始とみなす（``$h$$\\ldef$$H_0$`` の
# ように隣接するインライン数式の継ぎ目を表示数式と読み違えると、段落が数式の途中で割れる）。
_DISPLAY_MATH_RE = re.compile(r"(?P<display_math>(?<![^\s:.,;(])\$\$.*?\$\$|\\\[.*?\\\])", re.DOTALL)
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
_MAX_SIMPLE_MACROS = 600
_MAX_MACRO_EXPANSION_PASSES = 8
_ALIGNMENT_ENVS = {"align", "eqnarray", "gather", "multline", "flalign", "alignat"}
_MATH_ENV_BASE = _ALIGNMENT_ENVS | {
    "equation", "flalign", "alignat", "math", "displaymath", "split",
}
_TEX_ENV_TOKEN_RE = re.compile(
    r"\\(?P<kind>begin|end)\s*\{(?P<env>[A-Za-z*]+)\}"
)


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
    recovery: dict = {}
    blocks, sections = _tex_to_blocks_and_sections(
        source.expanded_tex,
        document_id,
        bibliography=source.bibliography,
        recovery=recovery,
    )
    author_result = extract_authors_from_tex(source.expanded_tex)
    # 環境の別名マクロ（``\\be`` / ``\\beqa``）を戻した版で数式を数える（ブロック化と同じ見え方）。
    structural_tex = _expand_structural_aliases(
        source.expanded_tex, _extract_simple_macros(source.expanded_tex)
    )
    unclosed_math_environments = _detect_unclosed_math_environments(structural_tex)
    # Issue #420: compute the TeX equation inventory at ingest from the expanded
    # source and persist it on the metadata (display math counts + symbolic
    # labels), so downstream completeness / coverage can detect TeX math without
    # re-storing the large source.
    tex_equation_inventory = _compute_tex_equation_inventory(structural_tex)
    source_bytes = len(source.expanded_tex.encode("utf-8"))
    authors = [_tex_accents(name) for name in (author_result.authors or source.authors)]
    author_extraction = author_result.to_metadata()
    if not author_result.authors and authors:
        author_extraction = {
            "source": "tex_front_matter",
            "confidence": 0.6,
            "needs_review": True,
            "review_reasons": ["tex_author_macro_missing"],
            "candidate_sources": {"tex_front_matter": authors},
        }
    metadata = DocumentMetadata(
        title=source.title,
        authors=authors,
        pages=0,
        parser_reached_eof=True,
        source_bytes_total=source_bytes,
        source_bytes_processed=source_bytes,
        unclosed_math_environments=unclosed_math_environments,
        author_extraction=author_extraction,
        tex_equation_inventory=tex_equation_inventory,
        structure_recovery={"parser_source": "tex_archive", **recovery},
    )
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
    title_macros = _extract_simple_macros(expanded)
    glossary = _extract_glossary(expanded)
    title_raw = _resolve_glossary(_first_match(_TITLE_RE, expanded), title_macros, glossary,
                                  used=set(), headings_short=False)
    title = _clean_title(title_raw, title_macros) or _infer_title(expanded, title_macros)
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


#: 単一ファイル投稿（tar ではなく素の gzip）で復元した .tex に与える名前。
_SINGLE_MEMBER_NAME = "main.tex"

#: 素の gzip を「TeX ソースである」と判断するための印。拡張子も member 名も無いので、
#: 中身そのものに LaTeX の骨格があることだけを根拠にする（PDF や画像を .tex として
#: 取り込まないための fail-closed）。
_TEX_DOCUMENT_MARKERS = ("\\documentclass", "\\begin{document}", "\\documentstyle")


class TexArchiveTooLargeError(ValueError):
    """展開後のサイズが上限を超えた（gzip 爆弾を含む）。``ValueError`` の部分型。"""


def _bounded_gunzip(data: bytes, limit: int) -> bytes | None:
    """gzip を **展開しながら** 上限で打ち切る（``gzip.decompress`` を丸ごと呼ばない）。

    ``gzip.decompress`` は展開し終えてからでないと大きさを確かめられないため、
    数百 KB の gzip 爆弾で数百 MB〜GB のメモリを確保してしまう（2026-10-01 レビュー M3）。
    ここは ``zlib.decompressobj(16 + MAX_WBITS)`` に ``max_length = limit + 1`` を渡し、
    上限を 1 バイトでも超えたら :class:`TexArchiveTooLargeError` を投げる。

    gzip として壊れているときは ``None``（呼び出し側が「読めない」として扱う）。
    """
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = decompressor.decompress(data, limit + 1)
    except zlib.error:
        return None
    if len(raw) <= limit and not decompressor.eof:
        # 途中で切れた gzip（旧 ``gzip.decompress`` は EOFError）— 読めないものとして扱う。
        return None
    if len(raw) > limit or decompressor.unconsumed_tail:
        raise TexArchiveTooLargeError("TeX source is too large when decompressed")
    return raw


def _read_single_gzip_member(archive_bytes: bytes) -> dict[str, str]:
    """tar ではない素の gzip を「1ファイルの TeX ソース」として読む。

    arXiv の TeX Source（``/src/<id>``）は、**複数ファイルの投稿なら .tar.gz、
    単一ファイルの投稿なら .tex を gzip しただけのバイト列**を返す。後者を
    :func:`tarfile.open` は開けないので、ここで畳んでおかないと「TeX ソースを選んだのに
    単一ファイル投稿の論文だけ解析できない」という取りこぼしになる。

    LaTeX の骨格が見当たらないバイト列は**受け取らない**（空 dict を返し、呼び出し側が
    ``.tar.gz`` として不正である旨のエラーに落とす）。
    """
    raw = _bounded_gunzip(archive_bytes, _MAX_MEMBER_BYTES)
    if raw is None:
        return {}
    text = _decode_tex(raw)
    if not any(marker in text for marker in _TEX_DOCUMENT_MARKERS):
        return {}
    return {_SINGLE_MEMBER_NAME: text}


def _read_archive_members(archive_bytes: bytes) -> tuple[dict[str, str], dict[str, str]]:
    tex_members: dict[str, str] = {}
    bib_members: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            # getmembers() は全ヘッダを先に読み切って溜め込むので使わない（member 数と
            # 読み進める総量の両方を縛る — gzip 爆弾で CPU とメモリを焼かない）。
            for index, member in enumerate(tf):
                if index >= _MAX_ARCHIVE_MEMBERS:
                    raise TexArchiveTooLargeError("TeX archive has too many members")
                if member.offset_data + max(0, member.size) > _MAX_TAR_SCAN_BYTES:
                    raise TexArchiveTooLargeError("TeX archive is too large when decompressed")
                if not member.isfile():
                    continue
                lower_name = member.name.lower()
                if not (
                    lower_name.endswith(_TEX_EXT)
                    or lower_name.endswith(_BIB_EXT)
                    or lower_name.endswith(_STY_EXT)
                ):
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
                if lower_name.endswith(_TEX_EXT) or lower_name.endswith(_STY_EXT):
                    # 同梱の .sty（論文ローカルのマクロ・番号付けの宣言）は ``\usepackage`` の
                    # 位置で前付けに読み込む（:func:`_expand_inputs`）。本文の候補にはならない。
                    tex_members[safe_name] = _decode_tex(raw)
                else:
                    bib_members[safe_name] = _decode_tex(raw)
    except tarfile.TarError as exc:
        # tar として読めないバイト列は、単一ファイル投稿（素の gzip）の可能性がある。
        single = _read_single_gzip_member(archive_bytes)
        if not single:
            raise ValueError("invalid .tar.gz TeX archive") from exc
        return single, {}
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
    candidates = {
        name: text for name, text in members.items() if not name.lower().endswith(_STY_EXT)
    } or members
    for name, text in candidates.items():
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

    def inline_package(match: re.Match[str]) -> str:
        # 論文に同梱された .sty（``\usepackage{style}``）だけを読み込む。マクロ（``\be`` 等）と
        # 番号付けの宣言（``\numberwithin``）がここにある論文で、式番号・環境の別名を取り違えない。
        inlined: list[str] = []
        for package in _split_tex_keys(match.group(1)):
            candidates = [
                posixpath.normpath(posixpath.join(base_dir, f"{package}{_STY_EXT}")),
                posixpath.normpath(f"{package}{_STY_EXT}"),
            ]
            for candidate in candidates:
                if candidate in members and candidate not in seen:
                    seen.add(candidate)
                    inlined.append(_strip_comments(members[candidate]))
                    break
        if not inlined:
            return match.group(0)
        return match.group(0) + "\n" + "\n".join(inlined) + "\n"

    text = _USEPACKAGE_RE.sub(inline_package, text)
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
    recovery: dict | None = None,
) -> tuple[list[TypedBlock], list[Section]]:
    macros = _extract_simple_macros(tex)
    body = _expand_structural_aliases(_document_body(tex), macros)
    # 略語参照（``\\gls`` / ``\\pve``）は本文の順で解決する（初出だけフル表記 — U1 seq 3019）。
    body = _resolve_glossary(body, macros, _extract_glossary(tex))
    preamble = _preamble(tex)
    labels = _build_label_map(body, preamble=preamble)
    # 番号付けは脚注の中の式も数える（上）。切り出しでは脚注の中の表示式をインラインにして、脚注を
    # 割らない（割ると脚注の括弧が崩れ、表示式の後の本文が前の段落に連結する — G1 seq 1297）。
    body = _inline_footnote_display_math(body)
    stats = recovery if recovery is not None else {}
    for key in ("table_bodies", "lists", "front_matter_blocks", "punctuation_only_fragments", "footnotes",
                "back_matter_blocks", "paragraph_headings", "equation_connectives"):
        stats.setdefault(key, 0)
    section_numbering = _section_numbering_enabled(body, preamble)
    section_counters = {1: 0, 2: 0, 3: 0}
    in_appendix = False
    back_matter_kind: str | None = None
    stats["footnotes"] += len(_FOOTNOTE_CMD_RE.findall(body))
    blocks: list[TypedBlock] = []
    sections: list[Section] = []
    current_section_id: str | None = None
    order = 0
    # 図表の float（ブロック範囲 [start, end) と label）。組版上の出現位置ではなく本文が参照する
    # 節へ寄せるために控える（U1 seq 3100 / 3109 — 付録の表が結論節の中に落ちる）。
    floats: list[tuple[int, int, list[str]]] = []

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
        if block_type == "body_paragraph" and not _is_meaningful_text(cleaned):
            # 記号・句読点だけの断片（式の間の「,」等）はブロックにしない（IK-0696）。
            stats["punctuation_only_fragments"] += 1
            return
        # 脚注は書かれた位置ではなく所属する文の終わりへ寄せ、本文の文を割らない。脚注の本文は
        # raw.footnotes にも分けて残す（後段が本文の母集合から外せるように — IK-0713）。
        cleaned, footnotes = _relocate_footnotes_to_sentence_end(cleaned)
        if footnotes:
            raw = {**(raw or {}), "footnotes": footnotes}
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

    def add_table_body(env: str, piece: str) -> None:
        rows = _tabular_rows(piece, macros=macros, labels=labels)
        if not rows:
            return
        stats["table_bodies"] += 1
        add_block(
            "\n".join(rows),
            "body_paragraph",
            section_id=current_section_id,
            raw={"container_type": "table", "in_table": True, "table_rows": len(rows),
                 "table_env": env, **_tex_reference_metadata(piece, bibliography)},
        )

    def add_front_matter(kind: str, piece: str) -> None:
        # 前付け（題名・著者・所属・ORCID・連絡先）は本文ではない。落とさず unknown 型で残す
        # （役割判定・主張採否の母集合は body_paragraph だけ — IK-0696）。
        piece = _ORCID_CMD_RE.sub(lambda m: f" (ORCID: {m.group(1).strip()})", piece or "")
        lines = [
            _clean_prose(line, macros=macros, labels=labels)
            for line in _LINE_BREAK_RE.split(piece)
        ]
        text = "\n".join(line for line in lines if _is_meaningful_text(line))
        if not text:
            return
        stats["front_matter_blocks"] += 1
        add_block(
            text,
            "unknown",
            section_id=current_section_id,
            raw={"front_matter": True, "front_matter_kind": kind},
        )

    def add_back_matter(kind: str, text: str) -> None:
        # 謝辞・資金・参考文献は本文ではない。落とさず非本文の型で残す（役割判定・主張採否の
        # 母集合は body_paragraph だけ — 前付けと同じ扱い。IK-0696 系・2610.08908）。
        cleaned = _clean_prose(text, macros=macros, labels=labels)
        if not _is_meaningful_text(cleaned):
            return
        stats["back_matter_blocks"] += 1
        add_block(
            cleaned,
            "reference_entry" if kind == "bibliography" else "unknown",
            section_id=current_section_id,
            raw={"back_matter": True, "back_matter_kind": kind},
        )

    def add_text_piece(text: str) -> None:
        for piece_kind, env, piece in _split_text_segment(
            text, before_first_section=current_section_id is None
        ):
            if back_matter_kind and piece_kind in {"text", "list"}:
                for paragraph in _paragraphs(piece):
                    add_back_matter(back_matter_kind, paragraph)
            elif piece_kind == "table_body":
                add_table_body(env, piece)
            elif piece_kind == "list":
                lines = _list_lines(env, piece, macros=macros, labels=labels)
                if lines:
                    stats["lists"] += 1
                    add_block(
                        "\n".join(lines),
                        "body_paragraph",
                        section_id=current_section_id,
                        raw={"container_type": "list", "list_env": env,
                             **_tex_reference_metadata(piece, bibliography)},
                    )
            elif piece_kind == "front_matter":
                add_front_matter(env, piece)
            elif piece_kind == "abstract":
                for paragraph in _paragraphs(piece):
                    add_block(
                        _clean_prose(paragraph, macros=macros, labels=labels),
                        "body_paragraph",
                        section_id=current_section_id,
                        raw={"abstract": True, **_tex_reference_metadata(paragraph, bibliography)},
                    )
            else:
                for paragraph in _paragraphs(piece):
                    if current_section_id is None and _looks_like_front_matter_paragraph(paragraph):
                        add_front_matter("paragraph", paragraph)
                        continue
                    add_block(
                        _clean_prose(paragraph, macros=macros, labels=labels),
                        "body_paragraph",
                        section_id=current_section_id,
                        raw=_tex_reference_metadata(paragraph, bibliography),
                    )

    for segment in _segment_tex(body):
        if segment[0] == "section":
            level, title = segment[1], _clean_prose(segment[2], macros=macros, labels=labels)
            starred = bool(segment[3]) if len(segment) > 3 else False
            if not title:
                continue
            number = None
            if not starred:
                section_counters[level] += 1
                for deeper in range(level + 1, 4):
                    section_counters[deeper] = 0
                if section_numbering:
                    number = _format_section_number(section_counters, level, appendix=in_appendix)
            back_matter_kind = "acknowledgments" if _BACK_MATTER_TITLE_RE.match(title) else None
            parent_id = None
            for previous in reversed(sections):
                if previous.level < level:
                    parent_id = previous.section_id
                    break
            section_id = f"sec_{len(sections) + 1}"
            sections.append(
                Section(
                    section_id=section_id,
                    title=title,
                    level=level,
                    order=len(sections),
                    page_start=1,
                    page_end=1,
                    parent_section_id=parent_id,
                    section_number=number,
                    is_appendix=in_appendix,
                )
            )
            current_section_id = section_id
            add_block(
                title,
                "section_heading" if level == 1 else "subsection_heading",
                section_id=section_id,
                raw={"back_matter": True, "back_matter_kind": back_matter_kind} if back_matter_kind else None,
            )
        elif segment[0] == "paragraph":
            title = _clean_prose(segment[1], macros=macros, labels=labels)
            if not title:
                continue
            if _BACK_MATTER_TITLE_RE.match(title):
                back_matter_kind = "acknowledgments"
            elif back_matter_kind == "acknowledgments":
                back_matter_kind = None
            stats["paragraph_headings"] += 1
            # 段落見出し（``\\paragraph{Stochasticity.}``）は本文の頭に混ぜず、見出しのブロックにする。
            # 節は作らない（節の一覧・番号は section 系だけ）。
            add_block(
                title.rstrip(" .:"),
                "subsection_heading",
                section_id=current_section_id,
                raw={"paragraph_heading": True,
                     **({"back_matter": True, "back_matter_kind": back_matter_kind} if back_matter_kind else {})},
            )
        elif segment[0] == "appendix":
            in_appendix = True
            section_counters = {1: 0, 2: 0, 3: 0}
            back_matter_kind = None
        elif segment[0] == "back_matter_start":
            back_matter_kind = "acknowledgments"
        elif segment[0] == "back_matter":
            env = segment[1]
            if env == "thebibliography":
                for item in re.split(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}", segment[2])[1:]:
                    add_back_matter("bibliography", item)
            else:
                for paragraph in _paragraphs(segment[2]):
                    add_back_matter("acknowledgments", paragraph)
        elif segment[0] == "equation":
            raw_equation = segment[1]
            env = segment[2] if len(segment) > 2 else None
            label = _extract_equation_label(raw_equation)
            latex = _clean_equation(raw_equation, macros=macros, env=env, labels=labels)
            metadata = _tex_reference_metadata(raw_equation, bibliography)
            printed = [
                labels[key].number
                for key in metadata.get("labels", [])
                if key in labels and labels[key].kind == _REF_KIND_EQUATION and labels[key].number
            ]
            if printed:
                # 印字番号（TeX には書かれず組版で数える番号）を決定論で数えられたときだけ持たせる
                # （IK-0681 / IK-0697 — 地の文の「式 (N)」と同じ数え方）。
                metadata["printed_numbers"] = printed
            add_block(
                latex,
                "equation_block",
                section_id=current_section_id,
                raw={"latex": latex, "extraction_source": "tex_source", **metadata},
                equation_label=label,
            )
        elif segment[0] in {"figure", "table"}:
            float_start = len(blocks)
            caption = _clean_prose(_caption_text(segment[1]), macros=macros, labels=labels)
            block_type = "figure_caption" if segment[0] == "figure" else "table_caption"
            if caption:
                add_block(caption, block_type, section_id=current_section_id)
            if segment[0] == "table":
                # 表組みの本体は落とさず、表由来と分かる本文ブロックにする（GROBID 経路と同じ形）。
                for piece_kind, env, piece in _split_text_segment(segment[1], before_first_section=False):
                    if piece_kind == "table_body":
                        add_table_body(env, piece)
            float_labels = [_label_key(m.group(1)) for m in _LABEL_RE.finditer(segment[1]) if m.group(1).strip()]
            if len(blocks) > float_start and float_labels:
                floats.append((float_start, len(blocks), float_labels))
        else:
            add_text_piece(segment[1])

    moved = _relocate_floats_to_referencing_section(blocks, floats)
    if moved:
        stats["relocated_floats"] = moved

    stats["equation_connectives"] += _mark_equation_connectives(blocks)

    if sections and blocks:
        _refresh_section_ranges(sections, blocks)
    return blocks, sections


def _relocate_floats_to_referencing_section(
    blocks: list[TypedBlock], floats: list[tuple[int, int, list[str]]]
) -> int:
    """図表の float を、本文が最初に参照する段落の直後へ移す（所属節で並べる）。

    LaTeX の float は組版の都合で書かれた位置（例: 結論節の途中）に置かれることがあり、
    出現位置で節を決めると付録の表が結論節のブロック列に挟まる（U1 seq 3100 / 3109）。
    float が置かれた節の本文がその float を参照していれば動かさない。参照が別の節にしか無い
    ときだけ、最初に参照する本文段落の直後へ移し、``raw.relocated_from_section_id`` に元の節を
    残す（情報を落とさない）。参照が無い float は書かれた位置のまま。戻り値は移した float の数。
    """
    if not floats or not blocks:
        return 0
    float_index = {idx for start, end, _labels in floats for idx in range(start, end)}
    first_ref: dict[str, int] = {}
    refs_by_section: dict[str | None, set[str]] = {}
    for idx, block in enumerate(blocks):
        if idx in float_index or block.block_type != "body_paragraph":
            continue
        for ref in (block.raw or {}).get("refs") or []:
            key = ref.get("key") if isinstance(ref, dict) else None
            if not key:
                continue
            first_ref.setdefault(key, idx)
            refs_by_section.setdefault(block.section_id, set()).add(key)
    moves: list[tuple[int, int, int, str | None]] = []
    for start, end, labels in floats:
        own_section = blocks[start].section_id
        if any(label in refs_by_section.get(own_section, set()) for label in labels):
            continue
        anchors = [first_ref[label] for label in labels if label in first_ref]
        if not anchors:
            continue
        anchor = min(anchors)
        target_section = blocks[anchor].section_id
        if target_section == own_section:
            continue
        moves.append((start, end, anchor, target_section))
    if not moves:
        return 0
    moving = {idx for start, end, _a, _t in moves for idx in range(start, end)}
    after_anchor: dict[int, list[TypedBlock]] = {}
    for start, end, anchor, target_section in moves:
        group = []
        for idx in range(start, end):
            block = blocks[idx]
            block.raw = {**(block.raw or {}), "relocated_from_section_id": block.section_id}
            block.section_id = target_section
            group.append(block)
        after_anchor.setdefault(anchor, []).extend(group)
    reordered: list[TypedBlock] = []
    for idx, block in enumerate(blocks):
        if idx in moving:
            continue
        reordered.append(block)
        reordered.extend(after_anchor.get(idx, []))
    for position, block in enumerate(reordered):
        block.block_id = f"tex_b{position + 1}"
        block.order = position
    blocks[:] = reordered
    return len(moves)


_FOOTNOTE_DISPLAY_RE = re.compile(
    r"\\begin\{(?P<env>equation\*?|align\*?|eqnarray\*?|gather\*?|multline\*?|flalign\*?|displaymath)\}"
    r"(?P<inner>.*?)\\end\{(?P=env)\}"
    r"|\$\$(?P<dd>.*?)\$\$|\\\[(?P<br>.*?)\\\]",
    re.DOTALL,
)


def _inline_footnote_display_math(body: str) -> str:
    """脚注の中の表示式（``\\begin{equation}`` / ``\\[…\\]`` / ``$$…$$``）をインライン数式にする。"""
    if "\\footnote" not in (body or ""):
        return body or ""
    parts: list[str] = []
    pos = 0
    for match in _FOOTNOTE_CMD_RE.finditer(body):
        if match.start() < pos:
            continue
        inner, end = _read_balanced_braced(body, match.end())
        if inner is None or not _FOOTNOTE_DISPLAY_RE.search(inner):
            continue

        def inline(display: re.Match[str]) -> str:
            env = (display.group("env") or "").rstrip("*")
            latex = display.group("inner") if display.group("env") else (display.group("dd") or display.group("br") or "")
            latex = _LABEL_RE.sub("", latex)
            latex = re.sub(r"\\(?:nonumber|notag)\b", "", latex)
            latex = re.sub(r"\\(begin|end)\{split\}", r"\\\1{aligned}", latex)
            latex = re.sub(r"\s+", " ", latex).strip()
            if env in _ALIGNMENT_ENVS:
                latex = rf"\begin{{aligned}} {latex} \end{{aligned}}"
            return f" ${latex}$ " if latex else " "

        parts.append(body[pos:match.end()])
        parts.append("{" + _FOOTNOTE_DISPLAY_RE.sub(inline, inner) + "}")
        pos = end
    parts.append(body[pos:])
    return "".join(parts)


def _preamble(tex: str) -> str:
    """``\\begin{document}`` より前（同梱 .sty を読み込んだ後の前付け）。"""
    match = re.search(r"\\begin\{document\}", tex or "")
    return (tex or "")[: match.start()] if match else ""


_SECTION_COUNTER_OVERRIDE_RE = re.compile(
    r"\\(?:re)?newcommand\s*\{?\\the(?:sub)*section|\\(?:setcounter|addtocounter)\s*\{(?:sub)*section\}"
)


def _section_numbering_enabled(body: str, preamble: str = "") -> bool:
    """節番号を決定論で数えてよいか（``\\thesection`` の再定義・カウンタの直接設定が無い）。"""
    return not _SECTION_COUNTER_OVERRIDE_RE.search((preamble or "") + "\n" + (body or ""))


def _format_section_number(counters: dict[int, int], level: int, *, appendix: bool) -> str | None:
    top = counters.get(1, 0)
    if top <= 0:
        return None
    if appendix:
        if top > 26:
            return None
        head = chr(ord("A") + top - 1)
    else:
        head = str(top)
    parts = [head] + [str(counters.get(depth, 0)) for depth in range(2, level + 1)]
    return ".".join(parts)


#: 式の前置きだけの短い断片（「with」「as follows」「where」）の語数の上限。
_CONNECTIVE_MAX_WORDS = 3
_SENTENCE_END_RE = re.compile(r"[.!?。．]\s*$")


def _mark_equation_connectives(blocks: list[TypedBlock]) -> int:
    """数式ブロックの直前にある、文になっていない短いつなぎ語の段落を非本文の型にする。

    ``\\begin{equation}`` の前の「with」「as follows」のような断片は、本文の文ではなく式への
    つなぎである。役割判定・主張採否の母集合を水増ししないよう ``unknown`` にし、
    ``raw.equation_connective`` を付けて残す（落とさない）。数式を含む断片は対象外。
    """
    marked = 0
    for index, block in enumerate(blocks[:-1]):
        if block.block_type != "body_paragraph" or blocks[index + 1].block_type != "equation_block":
            continue
        raw = block.raw or {}
        if raw.get("in_table") or raw.get("container_type") or raw.get("abstract"):
            continue
        text = (block.text or "").strip()
        if "$" in text or _SENTENCE_END_RE.search(text):
            continue
        if len(text.split()) > _CONNECTIVE_MAX_WORDS:
            continue
        block.block_type = "unknown"
        block.raw = {**raw, "equation_connective": True}
        marked += 1
    return marked


def _document_body(tex: str) -> str:
    match = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", tex, re.DOTALL)
    body = match.group(1) if match else tex
    titlepage = _TITLEPAGE_RE.search(body)
    if titlepage:
        abstract = _first_match(_ABSTRACT_RE, titlepage.group(1))
        body = body[: titlepage.start()] + ("\n\n" + abstract + "\n\n" if abstract else "\n\n") + body[titlepage.end():]
    return _place_command_form_abstract(tex, body)


_ABSTRACT_CMD_RE = re.compile(r"\\abstract\s*(?=\{)")


def _find_abstract_command(text: str | None) -> re.Match[str] | None:
    """``\\abstract{…}`` の使用箇所（``\\def\\abstract{`` のような定義は除く）。"""
    for match in _ABSTRACT_CMD_RE.finditer(text or ""):
        before = (text or "")[: match.start()].rstrip()
        if before.endswith(("\\def", "\\gdef", "\\edef", "\\xdef", "{", "\\let")):
            continue
        return match
    return None


def _place_command_form_abstract(tex: str, body: str) -> str:
    """本文に ``abstract`` 環境が無い論文の要旨を、最初の節より前に置く（2610.08908）。

    JHEP 系のクラスは要旨を ``\\abstract{…}`` のコマンドで前付け（``\\begin{document}`` より前）に
    書く。本文だけを読むと要旨が丸ごと落ち、骨格の「要旨」欄に導入の段落が入る。
    前付けに ``\\begin{abstract}`` 環境で書かれた要旨も同じ扱いにする。
    """
    if _ABSTRACT_RE.search(body or ""):
        return body
    abstract = ""
    span: tuple[int, int] | None = None
    match = _find_abstract_command(body)
    if match:
        inner, end = _read_balanced_braced(body, match.end())
        if inner is not None:
            abstract, span = inner, (match.start(), end)
    if not abstract:
        env = _ABSTRACT_RE.search(tex or "")
        if env:
            abstract = env.group(1)
        else:
            match = _find_abstract_command(tex)
            if match:
                inner, _end = _read_balanced_braced(tex, match.end())
                abstract = inner or ""
    if not abstract.strip():
        return body
    if span is not None:
        body = body[: span[0]] + body[span[1]:]
    wrapped = "\n\n\\begin{abstract}" + abstract + "\\end{abstract}\n\n"
    maketitle = re.search(r"\\maketitle\b", body)
    if maketitle:
        return body[: maketitle.end()] + wrapped + body[maketitle.end():]
    return wrapped + body


_ACK_CMD_RE = re.compile(r"\\(?P<ack_cmd>acknowledge?ments?)\b(?!\s*\{)")


def _segment_tex(body: str) -> list[tuple]:
    """本文を区間の並びに分ける。

    種別: ``text`` / ``section``（level, 題, 星付きか）/ ``paragraph``（段落見出しの題）/
    ``appendix`` / ``equation`` / ``figure`` / ``table`` / ``back_matter``（環境名, 中身）/
    ``back_matter_start``（``\\acknowledgments`` のような後付けの開始コマンド）。
    """
    segments: list[tuple] = []
    pos = 0
    combined = re.compile(
        rf"{_SECTION_RE.pattern}|{_PARAGRAPH_RE.pattern}|{_APPENDIX_CMD_RE.pattern}"
        rf"|{_BACK_MATTER_ENV_RE.pattern}|{_ACK_CMD_RE.pattern}"
        rf"|{_BLOCK_ENV_RE.pattern}|{_DISPLAY_MATH_RE.pattern}",
        re.DOTALL,
    )
    scan = 0
    while True:
        match = combined.search(body, scan)
        if not match:
            break
        end = match.end()
        if match.group("back_env"):
            env = match.group("back_env")
            closing = _find_env_end(body, env, match.end())
            if closing is None:
                scan = match.end()
                continue
            segment = ("back_matter", env, body[match.end():closing[0]])
            end = closing[1]
        elif match.group("section_cmd"):
            level = {"section": 1, "subsection": 2, "subsubsection": 3}[match.group("section_cmd")]
            segment = ("section", level, match.group("section_title"), bool(match.group("section_star")))
        elif match.group("paragraph_cmd"):
            segment = ("paragraph", match.group("paragraph_title"))
        elif match.group("appendix_cmd"):
            segment = ("appendix",)
        elif match.group("ack_cmd"):
            segment = ("back_matter_start", match.group("ack_cmd"))
        elif match.group("env"):
            env = match.group("env").rstrip("*")
            kind = "figure" if env == "figure" else "table" if env == "table" else "equation"
            segment = (kind, match.group("env_body"), env)
        else:
            segment = ("equation", match.group("display_math"))
        if match.start() > pos:
            segments.append(("text", body[pos:match.start()]))
        segments.append(segment)
        pos = scan = end
    if pos < len(body):
        segments.append(("text", body[pos:]))
    return segments


# ---------------------------------------------------------------------------
# 略語・用語集（glossaries / glossaries-extra / acronym / acro）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TexGlossaryEntry:
    key: str
    short: str
    long: str = ""
    first: str = ""
    plural: str = ""
    long_plural: str = ""
    acronym: bool = True


_GLOSSARY_DEF_RE = re.compile(
    r"\\(?P<cmd>newacronym|newabbreviation|newglossaryentry|longnewglossaryentry|DeclareAcronym|acrodef|acro|newacro)"
    r"(?![A-Za-z@])\s*(?:\[[^\]]*\])?\s*(?=\{)"
)
_GLOSSARY_REF_RE = re.compile(
    r"\\(?P<cmd>gls|Gls|GLS|glspl|Glspl|GLSpl|glstext|Glstext|glsfirst|Glsfirst|glsname|Glsname|"
    r"glssymbol|glsdesc|Glsdesc|glsentryshort|glsentrylong|glsentryname|glsentrytext|glsentrydesc|"
    r"glsxtrshort|glsxtrlong|glsxtrfull|"
    r"acrshort|Acrshort|ACRshort|acrshortpl|acrlong|Acrlong|acrlongpl|Acrlongpl|acrfull|Acrfull|acrfullpl|"
    r"acs|Acs|acsp|acl|Acl|aclp|Aclp|acf|Acf|acfp|Acfp|ac|Ac|acp|Acp|glslink|glsdisp)"
    r"(?![A-Za-z@])\*?\s*(?:\[[^\]]*\])?\s*(?=\{)"
)
_GLOSSARY_RESET_RE = re.compile(r"\\(?:glsresetall|acresetall|glsreset|acreset)(?![A-Za-z@])\s*(?:\{[^{}]*\})?")
_TEXORPDFSTRING_RE = re.compile(r"\\texorpdfstring\s*(?=\{)")
_HEADING_ARG_RE = re.compile(r"\\(?:part|chapter|section|subsection|subsubsection|paragraph|caption)\*?\s*(?:\[[^\]]*\])?\s*(?=\{)")
_MAX_GLOSSARY_ENTRIES = 2_000


def _split_kv(body: str) -> dict[str, str]:
    """``name={PVE}, description={prior-volume effect}`` を辞書にする（波括弧の入れ子を許す）。"""
    out: dict[str, str] = {}
    items: list[str] = []
    depth = 0
    buf: list[str] = []
    for char in body:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            items.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    items.append("".join(buf))
    for item in items:
        if "=" not in item:
            continue
        key, _sep, value = item.partition("=")
        value = value.strip()
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1]
        out[key.strip().lower()] = value.strip()
    return out


def _extract_glossary(tex: str) -> dict[str, TexGlossaryEntry]:
    """論文の略語・用語集の定義を読む（``\\newacronym`` / ``\\newglossaryentry`` / acronym・acro）。"""
    entries: dict[str, TexGlossaryEntry] = {}
    for match in _GLOSSARY_DEF_RE.finditer(tex or ""):
        if len(entries) >= _MAX_GLOSSARY_ENTRIES:
            break
        cmd = match.group("cmd")
        key, pos = _read_balanced_braced(tex, match.end())
        if key is None:
            continue
        key = key.strip()
        pos = _skip_ws(tex, pos)
        if cmd in {"newacronym", "newabbreviation"}:
            short, pos = _read_balanced_braced(tex, pos)
            pos = _skip_ws(tex, pos)
            long, _pos = _read_balanced_braced(tex, pos)
            if short is None or long is None:
                continue
            entries[key] = TexGlossaryEntry(key, short.strip(), long.strip())
        elif cmd in {"acro", "acrodef", "newacro"}:
            short = key
            if pos < len(tex) and tex[pos] == "[":
                bracket, pos = _read_bracket_arg(tex, pos)
                short = (bracket or key).strip()
                pos = _skip_ws(tex, pos)
            long, _pos = _read_balanced_braced(tex, pos)
            if long is None:
                continue
            entries[key] = TexGlossaryEntry(key, short, long.strip())
        elif cmd == "DeclareAcronym":
            body, _pos = _read_balanced_braced(tex, pos)
            if body is None:
                continue
            kv = _split_kv(body)
            if kv.get("short"):
                entries[key] = TexGlossaryEntry(key, kv["short"], kv.get("long", ""),
                                                plural=kv.get("short-plural-form", ""),
                                                long_plural=kv.get("long-plural-form", ""))
        else:  # newglossaryentry / longnewglossaryentry
            body, pos = _read_balanced_braced(tex, pos)
            if body is None:
                continue
            kv = _split_kv(body)
            name = kv.get("text") or kv.get("name") or ""
            if not name:
                continue
            description = kv.get("description", "")
            if cmd == "longnewglossaryentry":
                pos = _skip_ws(tex, pos)
                description, _pos = _read_balanced_braced(tex, pos)
                description = (description or "").strip()
            # 略語の形の名前（空白を含まない短い語）で説明があるときは、初出を「説明 (略語)」にする。
            looks_abbrev = bool(re.fullmatch(r"\S{1,16}", re.sub(r"\$[^$]*\$", "", name))) and bool(description)
            entries[key] = TexGlossaryEntry(
                key, name, description if looks_abbrev else "",
                first=kv.get("first", ""), plural=kv.get("plural", ""), acronym=looks_abbrev,
            )
    return entries


def _capitalize(text: str, mode: str) -> str:
    if mode == "all":
        return text.upper()
    if mode == "first":
        idx = next((i for i, ch in enumerate(text) if ch.isalpha()), None)
        if idx is not None:
            return text[:idx] + text[idx].upper() + text[idx + 1:]
    return text


def _glossary_text(entry: TexGlossaryEntry | None, key: str, cmd: str, *, first_use: bool) -> str:
    """1 つの参照命令を本文の語にする。定義が読めなければ key をそのまま残す（残骸を出さない）。"""
    base = cmd.lower()
    caps = "all" if cmd in {"GLS", "GLSpl", "ACRshort"} else ("first" if cmd[:1].isupper() else "")
    plural = base.endswith("pl") or base in {"acsp", "aclp", "acfp", "acp"}
    if entry is None:
        return _capitalize(key, caps)
    short = (entry.plural if plural and entry.plural else entry.short + ("s" if plural else ""))
    long = entry.long
    if long and plural:
        long = entry.long_plural or long + "s"
    if base in {"glsentrylong", "acrlong", "acrlongpl", "acl", "aclp", "glsxtrlong", "glsdesc", "glsentrydesc"}:
        text = long or short
    elif base in {"acrfull", "acrfullpl", "acf", "acfp", "glsxtrfull"}:
        text = f"{long} ({short})" if long else short
    elif base in {"gls", "glspl", "ac", "acp"} and first_use:
        if entry.first:
            text = entry.first
        else:
            text = f"{long} ({short})" if long else short
    else:
        text = short
    return _capitalize(text, caps)


def _resolve_glossary(
    text: str,
    macros: dict[str, TexMacro] | None,
    glossary: dict[str, TexGlossaryEntry] | None,
    *,
    used: set[str] | None = None,
    headings_short: bool = True,
) -> str:
    """略語の参照（``\\gls{pve}`` / ``\\pve`` → ``\\gls{pve}\\xspace``）を文書順に語へ置き換える。

    初出は「フル表記 (略語)」、以後は略語（glossaries の既定と同じ）。節題・caption の中の参照は
    略語だけにして初出を消費しない。定義が読めない参照は key を残す。``\\texorpdfstring{TeX}{PDF}``
    は TeX 側だけを残し、``\\xspace`` は除く（U1 seq 3019 / 3094 / 3118）。
    """
    if not text:
        return text or ""
    text = _replace_command_args(text, _TEXORPDFSTRING_RE, lambda args: args[0], 2)
    has_refs = _GLOSSARY_REF_RE.search(text) is not None
    if macros and ("\\" in text):
        # 略語参照へ展開される引数なしの論文マクロ（``\\pve``）を先に開く（初出の順序を本文で決める）。
        def open_macro(match: re.Match[str]) -> str:
            macro = macros.get(match.group(0))
            if macro is None or macro.arg_count != 0:
                return match.group(0)
            expanded = _expand_simple_macros(macro.replacement, macros)
            if _GLOSSARY_REF_RE.search(expanded):
                return expanded
            return match.group(0)

        opened = _CONTROL_SEQUENCE_RE.sub(open_macro, text)
        has_refs = has_refs or opened != text
        text = opened
    if not has_refs:
        return _XSPACE_RE.sub("", text) if "\\xspace" in text else text
    used = used if used is not None else set()
    entries = glossary or {}
    heading_spans: list[tuple[int, int]] = []
    if headings_short:
        for match in _HEADING_ARG_RE.finditer(text):
            _body, end = _read_balanced_braced(text, match.end())
            if _body is not None:
                heading_spans.append((match.end(), end))

    out: list[str] = []
    pos = 0
    for match in _GLOSSARY_REF_RE.finditer(text):
        if match.start() < pos:
            continue
        key, end = _read_balanced_braced(text, match.end())
        if key is None:
            continue
        key = key.strip()
        cmd = match.group("cmd")
        out.append(text[pos:match.start()])
        if cmd in {"glslink", "glsdisp"}:
            end_ws = _skip_ws(text, end)
            shown, shown_end = _read_balanced_braced(text, end_ws)
            if shown is not None:
                out.append(shown)
                pos = shown_end
                used.add(key)
                continue
        in_heading = any(start <= match.start() < stop for start, stop in heading_spans)
        first_use = key not in used and not in_heading
        out.append(_glossary_text(entries.get(key), key, cmd, first_use=first_use))
        if cmd.lower() in {"gls", "glspl", "ac", "acp"} and not in_heading:
            used.add(key)
        # ``\\gls{key}[挿入]`` の挿入句はそのまま後ろに置く。
        pos = end
        if pos < len(text) and text[pos] == "[":
            insert, after = _read_bracket_arg(text, pos)
            if insert is not None:
                out.append(insert)
                pos = after
    out.append(text[pos:])
    resolved = "".join(out)
    resolved = _GLOSSARY_RESET_RE.sub("", resolved)
    return _XSPACE_RE.sub("", resolved)


def _replace_command_args(text: str, pattern: re.Pattern[str], render, arg_count: int) -> str:
    """``\\cmd{a}{b}`` 型の命令を ``render([a, b])`` に置き換える（引数が読めなければ残す）。"""
    if not pattern.search(text):
        return text
    out: list[str] = []
    pos = 0
    for match in pattern.finditer(text):
        if match.start() < pos:
            continue
        args: list[str] = []
        cursor = match.end()
        for _ in range(arg_count):
            cursor = _skip_ws(text, cursor)
            arg, cursor_next = _read_balanced_braced(text, cursor)
            if arg is None:
                break
            args.append(arg)
            cursor = cursor_next
        if len(args) != arg_count:
            continue
        out.append(text[pos:match.start()])
        out.append(render(args))
        pos = cursor
    out.append(text[pos:])
    return "".join(out)


def _paragraphs(text: str) -> list[str]:
    text = re.sub(r"\\(?:maketitle|tableofcontents|bibliography|bibliographystyle)\b(?:\{[^{}]*\})?", "\n\n", text)
    return [part for part in re.split(r"\n\s*\n+", text) if part.strip()]


def _clean_equation(
    text: str,
    *,
    macros: dict[str, TexMacro] | None = None,
    env: str | None = None,
    labels: dict[str, "TexLabelTarget"] | None = None,
) -> str:
    cleaned = text.strip()
    if env == "alignat":
        # ``\\begin{alignat}{2}`` の列数の引数は式の中身ではない。
        cleaned = re.sub(r"^\{\s*\d+\s*\}", "", cleaned).strip()
    cleaned = re.sub(r"^\$\$|\$\$$", "", cleaned)
    cleaned = re.sub(r"^\\\[|\\\]$", "", cleaned)
    cleaned = _LABEL_RE.sub("", cleaned)
    cleaned = _expand_simple_macros(cleaned, _math_mode_macros(macros))
    cleaned = _XSPACE_RE.sub("", cleaned)
    cleaned = _unwrap_ensuremath(cleaned)
    cleaned = _replace_math_refs(cleaned, labels)
    cleaned = _MATH_LAYOUT_COMMAND_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _renderable_equation_latex(cleaned, env=env)


_XSPACE_RE = re.compile(r"\\xspace(?![A-Za-z@])\s*(?:\{\})?")
_UNESCAPED_DOLLAR_RE = re.compile(r"(?<!\\)\$")
_MATH_MODE_MACRO_CACHE: dict[int, tuple[dict, dict]] = {}


def _text_body_as_math(replacement: str) -> str:
    """地の文用のマクロ本体（``$\\Lambda$CDM``）を数式の中に入れられる形にする。

    数式の中で ``$…$`` 入りの本体を展開すると ``$$\\Lambda$CDM$`` になり、先頭の ``$$`` が
    display math の開きと読まれて数百字が 1 数式になる（U1 seq 3022 / 3076 / 3091 / 3112）。
    ``$`` の内側は数式のまま、外側の語は ``\\text{…}`` に包む（``\\ensuremath`` と同じ扱い）。
    """
    parts = _UNESCAPED_DOLLAR_RE.split(replacement)
    out: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            out.append(part)
        elif part.strip():
            out.append(f"\\text{{{part}}}")
        else:
            out.append(part)
    return "".join(out)


def _math_mode_macros(macros: dict[str, TexMacro] | None) -> dict[str, TexMacro]:
    """数式の中で展開するときのマクロ表（本体に ``$`` を含むものだけ数式向けに直す）。"""
    if not macros:
        return {}
    cached = _MATH_MODE_MACRO_CACHE.get(id(macros))
    if cached is not None and cached[0] is macros:
        return cached[1]
    derived = {
        name: (
            TexMacro(macro.name, _text_body_as_math(macro.replacement), macro.arg_count)
            if _UNESCAPED_DOLLAR_RE.search(macro.replacement)
            else macro
        )
        for name, macro in macros.items()
    }
    if len(_MATH_MODE_MACRO_CACHE) > 64:
        _MATH_MODE_MACRO_CACHE.clear()
    _MATH_MODE_MACRO_CACHE[id(macros)] = (macros, derived)
    return derived


def _replace_math_refs(latex: str, labels: dict[str, "TexLabelTarget"] | None = None) -> str:
    def replace(match: re.Match[str]) -> str:
        key = _label_key(match.group("key"))
        if not key:
            return ""
        target = (labels or {}).get(key)
        token = target.number if target and target.number else key
        if match.group("cmd") == "eqref":
            return f"({token})"
        return token

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
            if "\\xspace" in macro.replacement:
                # ``\xspace`` は「後ろが語なら空白を入れる」だけの組版指示。展開先に残すと数式の中で
                # 未知の命令になり、地の文では数式の目印に誤読される（U1 seq 3022 / 3076）。
                macro = TexMacro(macro.name, _XSPACE_RE.sub("", macro.replacement).strip(), macro.arg_count)
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
            before = expanded[pos:match.start()] or ("".join(parts) if parts else "")
            parts.append(expanded[pos:match.start()])
            if replacement[:1].isalpha() and re.search(r"\\[A-Za-z@]+$", before):
                # ``\\to\\gsc`` → ``\\to G_…``（前の制御語と展開結果の英字を詰めない — J0）。
                parts.append(" ")
            parts.append(replacement)
            if (
                end < len(expanded)
                and expanded[end].isalpha()
                and re.search(r"\\[A-Za-z@]+$", replacement)
            ):
                # 展開結果が制御語で終わり、次が英字なら語の区切りを残す（``\\to`` + ``G`` → ``\\to G``。
                # 詰めると ``\\toG`` という別の制御語になる — J0）。
                parts.append(" ")
            pos = end
            changed = True
        if not changed:
            break
        parts.append(expanded[pos:])
        expanded = "".join(parts)
    return expanded


#: 環境の開始・終了・節見出しだけを置き換える別名マクロ（``\\newcommand{\\be}{\\begin{equation}}`` /
#: ``\\newcommand\\acknowledgments{\\section*{Acknowledgments}}``）の置き換え先。
_STRUCTURAL_ALIAS_RE = re.compile(
    r"^\s*(?:\\(?:begin|end)\s*\{[A-Za-z*]+\}"
    r"|\\(?:section|subsection|subsubsection|paragraph)\*?\s*\{[^{}]*\})\s*$"
)


def _expand_structural_aliases(text: str, macros: dict[str, TexMacro] | None) -> str:
    """本文の構造を決める別名マクロだけを先に展開する（IK-0696 系・2610.08908）。

    ``\\be … \\ee`` / ``\\beqa … \\eeqa`` / ``\\bseq … \\eseq`` のように環境を別名で書く論文で、
    数式環境を数式ブロックとして切り出す前に元の ``\\begin{…}`` / ``\\end{…}`` へ戻す。
    戻さないと地の文のマクロ展開（:func:`_text_mode_macro_math`）が ``$\\begin{eqnarray}$`` を
    作り、式の中身が平文化されて ``\\partial`` 等が剥がれた断片が本文に残る。
    展開するのは引数を取らず、置き換え先が環境の印か節見出し 1 つだけのマクロに限る。
    """
    if not text or not macros:
        return text or ""
    aliases = {
        name: macro.replacement.strip()
        for name, macro in macros.items()
        if macro.arg_count == 0 and _STRUCTURAL_ALIAS_RE.match(macro.replacement or "")
    }
    if not aliases:
        return text

    def replace(match: re.Match[str]) -> str:
        return aliases.get(match.group(0), match.group(0))

    return _CONTROL_SEQUENCE_RE.sub(replace, text)


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


def _label_key(value: str) -> str:
    """``\\label`` / ``\\ref`` のキーの正規化（空白を除く — ``eq: tri-def`` → ``eq:tri-def``。J0）。

    LaTeX はキーの空白も字として扱うが、論文内では定義と参照が同じ書き方なので、空白を除いても
    対応は変わらない。下流（式 ID ``eq_tri-def``・ラベル検証）は空白の無いキーを前提にする。
    """
    return re.sub(r"\s+", "", value or "")


def _extract_equation_label(text: str) -> str | None:
    match = _LABEL_RE.search(text)
    return (_label_key(match.group(1)) or None) if match else None


def _split_tex_keys(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _tex_reference_metadata(text: str, bibliography: dict[str, dict] | None = None) -> dict:
    labels = [_label_key(m.group(1)) for m in _LABEL_RE.finditer(text or "") if m.group(1).strip()]
    refs = [
        {"key": _label_key(key), "kind": "ref"}
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


#: TeX のアクセント記法 → Unicode の結合文字（I2 seq 1658: ``Schr\\"odinger`` → ``Schrödinger``）。
_TEX_ACCENT_MARKS = {
    '"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "=": "\u0304",
    ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b", "c": "\u0327", "k": "\u0328",
    "r": "\u030a", "d": "\u0323", "b": "\u0331",
}
_TEX_SPECIAL_LETTERS = {
    "ss": "ß", "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ",
    "aa": "å", "AA": "Å", "l": "ł", "L": "Ł", "i": "ı", "j": "ȷ",
}
_TEX_SYMBOL_ACCENT_RE = re.compile(
    r"\\(?P<mark>[\"'`^~=.])\s*(?:\{\s*(?:\\(?P<bdotless>[ij])\b\s*|(?P<braced>[A-Za-z]))\s*\}"
    r"|\\(?P<dotless>[ij])\b|(?P<bare>[A-Za-z]))"
)
_TEX_LETTER_ACCENT_RE = re.compile(
    r"\\(?P<mark>[uvHckrdb])\s*\{\s*(?:\\(?P<bdotless>[ij])\b\s*|(?P<braced>[A-Za-z]))\s*\}"
)
_TEX_SPECIAL_LETTER_RE = re.compile(r"\{?\\(?P<name>ss|ae|AE|oe|OE|aa|AA|o|O|l|L)(?![A-Za-z])\s*\}?")


def _tex_accents(text: str | None) -> str:
    """TeX のアクセント・特殊文字（``\\"o`` / ``\\'{e}`` / ``\\c{c}`` / ``\\ss``）を Unicode にする（NFC）。"""
    if not text or "\\" not in text:
        return text or ""

    def accent(match: re.Match[str]) -> str:
        base = match.group("braced") or match.groupdict().get("bare") or ""
        dotless = match.group("bdotless") or match.groupdict().get("dotless")
        if dotless:
            base = dotless  # ``\\'{\\i}`` は点なしの i に符号を付ける = 通常の í
        if not base:
            return match.group(0)
        return unicodedata.normalize("NFC", base + _TEX_ACCENT_MARKS[match.group("mark")])

    text = _TEX_SYMBOL_ACCENT_RE.sub(accent, text)
    text = _TEX_LETTER_ACCENT_RE.sub(accent, text)
    return _TEX_SPECIAL_LETTER_RE.sub(lambda m: _TEX_SPECIAL_LETTERS[m.group("name")], text)


def _clean_bib_value(value: str) -> str:
    value = (value or "").strip().rstrip(",").strip()
    if (value.startswith("{") and value.endswith("}")) or (value.startswith('"') and value.endswith('"')):
        value = value[1:-1]
    value = _tex_accents(value)
    value = re.sub(r"\{([^\W\d_])\}", r"\1", value)  # ``Schr{ö}dinger`` の 1 文字の波括弧
    value = re.sub(r"\s+", " ", value)
    return value.strip()


#: 本文中のインライン数式（``$…$`` / ``$$…$$`` / ``\\(…\\)``）。``\\$`` は区切りに数えない。
_INLINE_MATH_SEGMENT_RE = re.compile(
    # 単一の ``$`` を先に試す（``$h$$\\ldef$$H_0$`` のような隣接するインライン数式を
    # ``$$…$$`` と読み違えない。中身の無い ``$$`` は単一側で一致せず ``$$…$$`` に落ちる）。
    r"(?<!\\)\$(?P<d>[^$]+?)(?<!\\)\$|(?<!\\)\$\$(?P<dd>.+?)(?<!\\)\$\$|\\\((?P<p>.+?)\\\)",
    re.DOTALL,
)


def _caption_text(segment: str) -> str:
    """``\\caption[短い]{本文}`` の本文（波括弧の入れ子を許す — 数式入りの caption を落とさない）。"""
    match = re.search(r"\\caption\*?\s*", segment or "")
    if not match:
        return ""
    pos = _skip_ws(segment, match.end())
    if pos < len(segment) and segment[pos] == "[":
        _short, pos = _read_bracket_arg(segment, pos)
        pos = _skip_ws(segment, pos)
    body, _end = _read_balanced_braced(segment, pos)
    return body or ""


def _clean_inline_math(
    latex: str,
    macros: dict[str, TexMacro] | None,
    labels: dict[str, "TexLabelTarget"] | None = None,
) -> str:
    cleaned = _LABEL_RE.sub("", latex or "")
    cleaned = _expand_simple_macros(cleaned, _math_mode_macros(macros))
    cleaned = _XSPACE_RE.sub("", cleaned)
    cleaned = _unwrap_ensuremath(cleaned)
    cleaned = _replace_math_refs(cleaned, labels)
    cleaned = _MATH_LAYOUT_COMMAND_RE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _clean_prose(
    text: str | None,
    *,
    macros: dict[str, TexMacro] | None = None,
    labels: dict[str, "TexLabelTarget"] | None = None,
    _depth: int = 0,
) -> str:
    """本文・caption・節見出しの整形。**インライン数式は ``$…$`` のまま残す**。

    2026-10-10: 旧実装は本文も ``_clean_text`` に通しており、``$`` を外したうえで
    ``\\Lambda`` 等のコマンドと波括弧をすべて消していた（``$R_{\\Lambda_c}$`` →
    ``R__c``、``$\\bar B\\to D^{(*)}\\tau\\bar\\nu$`` → ``B D^(*)``）。その本文を読む後段
    （主張・根拠の引用・前提・要約）は崩れた記法を受け取り、LLM が記法を作り直していた。
    数式の外側だけを従来どおり平文化し、数式は論文の簡単なマクロを展開した LaTeX で残す。
    """
    if not text:
        return ""
    # 脚注は数式の切り分けより先に区切る（脚注の中の数式も「（脚注: …）」の内側に残す）。
    text = _mark_footnotes(text)
    # 引用は段落全体で置く（主語の位置かを、数式で区切った断片ではなく文の並びで判断する）。
    text = _replace_citations(text)
    # 参照の前置きの言語は段落全体で決める（数式で区切った断片に日本語が無くても日本語の段落はある）。
    english = _is_english_text(_INLINE_MATH_SEGMENT_RE.sub(" ", text))
    parts: list[str] = []
    pos = 0

    def append_fragment(fragment: str) -> None:
        # 地の文のマクロが数式で始まる・終わることがある（``\\lcdm`` → ``$\\Lambda$CDM``）。
        # 直前の数式と ``$`` が隣接すると ``$$`` になるので空白で離す。
        if fragment.startswith("$") and "".join(parts).endswith("$"):
            parts.append(" ")
        parts.append(fragment)

    for match in _INLINE_MATH_SEGMENT_RE.finditer(text):
        append_fragment(_clean_text_fragment(text[pos:match.start()], macros, labels, english=english, depth=_depth))
        latex = _clean_inline_math(match.group("dd") or match.group("d") or match.group("p") or "", macros, labels)
        if latex:
            # 隣接する数式は空白で離す（``$a$$b$`` は描画側で ``$$…$$`` と読まれる）。
            if "".join(parts).endswith("$"):
                parts.append(" ")
            parts.append(f"${latex}$")
        pos = match.end()
    append_fragment(_clean_text_fragment(text[pos:], macros, labels, english=english, depth=_depth))
    out = re.sub(r"\s+", " ", "".join(parts)).strip()
    # 地の文のマクロの展開で隣り合った ``$`` + ``$``（``$a$$b$``）を離す（display math と読ませない）。
    return re.sub(r"(?<=[^\s$\\])\$\$(?=[^\s$])", "$ $", out) if "$$" in out else out


_ENSUREMATH_RE = re.compile(r"\\ensuremath\s*(?=\{)")
_ENSUREMATH_ANY_RE = re.compile(r"\\ensuremath(?![A-Za-z@])\s*")


def _unwrap_ensuremath(latex: str) -> str:
    """数式の中の ``\\ensuremath{X}`` を ``X`` にする（入れ子も外す）。

    ``\\ensuremath`` は「数式モードでなければ数式にする」だけのマクロで、数式の中では
    中身そのもの。論文のマクロ（``\\newcommand\\vev[1]{\\ensuremath{\\left\\langle #1\\right\\rangle}}``）
    を展開すると数式に残り、描画できない（2026-10-16, S1 seq 2773 / 2821 / 2833 / 2842）。
    波括弧が閉じていない ``\\ensuremath`` は制御語だけを落とす。地の文での判定
    （:func:`_text_mode_macro_math`）は展開結果の ``\\ensuremath`` を見るので、ここでは外さない。
    """
    if not latex or "\\ensuremath" not in latex:
        return latex
    out = latex
    for _ in range(_MAX_MACRO_EXPANSION_PASSES * 4):
        match = _ENSUREMATH_ANY_RE.search(out)
        if match is None:
            break
        body, end = _read_balanced_braced(out, match.end())
        if body is None:
            out = out[: match.start()] + out[match.end():]
        else:
            out = out[: match.start()] + body + out[end:]
    return out
_MATH_MARKER_RE = re.compile(r"[_^]|\\[A-Za-z]+")


#: 地の文の書体・間隔の命令（数式の目印にしない — ``{\\tt COMET}`` を ``$…$`` に包まない）。
_TEXT_MODE_COMMANDS = frozenset({
    "tt", "it", "bf", "rm", "sc", "sf", "sl", "em", "emph", "textit", "textbf", "texttt", "textrm",
    "textsc", "textsf", "textsl", "textup", "textmd", "underline", "mbox", "hbox", "text", "xspace",
    "ttfamily", "itshape", "bfseries", "scshape", "rmfamily", "sffamily", "upshape", "normalfont",
})


def _is_math_expansion(expanded: str) -> bool:
    if re.search(r"[_^]", expanded):
        return True
    return any(name not in _TEXT_MODE_COMMANDS for name in re.findall(r"\\([A-Za-z]+)", expanded))


def _text_mode_macro_math(
    text: str, macros: dict[str, TexMacro] | None, *, depth: int = 0
) -> tuple[str, list[str]]:
    """地の文で使われた論文のマクロ（``\\RD`` → ``\\ensuremath{R_D}``）を展開し、数式に
    なるものは退避して占位子に置く（平文化で波括弧・コマンドが消されないように）。"""
    stash: list[str] = []
    if not macros:
        return text, stash

    def replace(match: re.Match[str]) -> str:
        macro = macros.get(match.group(0))
        if macro is None or macro.arg_count != 0:
            return match.group(0)
        expanded = _XSPACE_RE.sub("", _expand_simple_macros(macro.replacement, macros)).strip()
        inner = expanded
        ensure = _ENSUREMATH_RE.match(expanded)
        if ensure:
            body, _end = _read_balanced_braced(expanded, ensure.end())
            inner = body if body is not None else expanded
        elif _UNESCAPED_DOLLAR_RE.search(expanded):
            # 本体が自分で数式を区切る地の文のマクロ（``\\lcdm`` → ``$\\Lambda$CDM``）は、外側を
            # ``$…$`` で包まない（包むと ``$$\\Lambda$CDM$`` — U1 seq 3076）。本体を地の文として整形し、
            # 中の数式は数式のまま残す。
            if depth >= _MAX_MACRO_EXPANSION_PASSES:
                return expanded
            stash.append(_clean_prose(expanded, macros=None, _depth=depth + 1))
            return f"\x00{len(stash) - 1}\x00"
        elif not _is_math_expansion(expanded):
            return expanded
        latex = _clean_inline_math(inner, macros)
        if not latex:
            return ""
        stash.append(f"${latex}$")
        return f"\x00{len(stash) - 1}\x00"

    return _CONTROL_SEQUENCE_RE.sub(replace, text), stash


def _clean_text_fragment(
    text: str,
    macros: dict[str, TexMacro] | None = None,
    labels: dict[str, "TexLabelTarget"] | None = None,
    *,
    english: bool | None = None,
    depth: int = 0,
) -> str:
    """数式を含まない地の文の平文化（前後の空白は残す — 数式との継ぎ目を保つ）。"""
    if not text:
        return ""
    text = _XSPACE_RE.sub("", text)
    text, stash = _text_mode_macro_math(text, macros, depth=depth)
    if stash:
        cleaned = _clean_text_fragment(text, None, labels, english=english)
        return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], cleaned)
    text = _tex_accents(text)
    text = _replace_text_refs(text, labels, english=english)
    text = _tex_quotes(text)
    text = _COLOR_ARG_RE.sub("", text)
    text = re.sub(r"\\(?:label|url)\*?(?:\[[^\]]*\])?\{[^{}]*\}", "", text)
    text = re.sub(r"\\href\s*\{[^{}]*\}", "", text)
    text = _LAYOUT_COMMAND_RE.sub(" ", text)
    text = _strip_env_tokens(text)
    # 改行（\\ / \\[10pt]）・間隔の記号は語の区切りにする（「\ \\」「10pt」を残さない）。
    text = _LINE_BREAK_RE.sub(" ", text)
    text = _SPACING_SYMBOL_RE.sub(" ", text)
    text = _CONTROL_SYMBOL_RE.sub(lambda m: "" if m.group(1) in "{}" else m.group(1), text)
    previous = None
    while previous != text:
        previous = text
        text = _COMMAND_WITH_ARG_RE.sub(r"\1", text)
    text = _COMMAND_RE.sub("", text)
    text = text.replace("~", " ")
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\\(?=\W|$)", "", text)
    return re.sub(r"\s+", " ", text)


def _clean_text(text: str | None, labels: dict[str, "TexLabelTarget"] | None = None) -> str:
    if not text:
        return ""
    text = _tex_accents(text)
    text = _mark_footnotes(text)
    text = _replace_text_refs(text, labels)
    text = _tex_quotes(text)
    text = _COLOR_ARG_RE.sub("", text)
    text = re.sub(r"\\(?:label|url)\*?(?:\[[^\]]*\])?\{[^{}]*\}", "", text)
    text = re.sub(r"\\href\s*\{[^{}]*\}", "", text)
    text = _LAYOUT_COMMAND_RE.sub(" ", text)
    text = _strip_env_tokens(text)
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


def _compute_tex_equation_inventory(tex: str) -> dict:
    """Compute the canonical TeX equation inventory at ingest (issue #420).

    Delegates to ``completeness.tex_equation_inventory`` so ingest and the
    completeness / coverage checks share one definition of display-math counting
    and symbolic-label extraction. Resilient: any failure yields an empty
    inventory rather than breaking ingestion.
    """
    try:
        from .completeness import tex_equation_inventory
    except Exception:  # pragma: no cover - import resilience
        try:
            from core.document_pipeline.completeness import tex_equation_inventory
        except Exception:
            return {"display_math_blocks": 0, "labels": [], "label_count": 0}
    try:
        return tex_equation_inventory(tex)
    except Exception:  # pragma: no cover - defensive
        return {"display_math_blocks": 0, "labels": [], "label_count": 0}


def _detect_unclosed_math_environments(tex: str) -> list[str]:
    stack: list[str] = []
    unmatched: set[str] = set()
    uncommented = re.sub(r"(?m)(?<!\\)%.*$", "", tex)
    for match in _TEX_ENV_TOKEN_RE.finditer(uncommented):
        env = match.group("env")
        if env.rstrip("*") not in _MATH_ENV_BASE:
            continue
        if match.group("kind") == "begin":
            stack.append(env)
        elif stack and stack[-1] == env:
            stack.pop()
        else:
            unmatched.add(env)
    unmatched.update(stack)
    return sorted(unmatched)


def _clean_title(text: str | None, macros: dict[str, TexMacro] | None = None) -> str:
    """題名（``documents.title``）の整形。**数式とその中の論文マクロを落とさない**。

    ``documents.title`` は教材一覧・レーダー・比較・ファイル名の代替に平文として読まれるので、
    題名は平文で返す。ただし本文と同じ規律で、インライン数式は :func:`_clean_prose` が論文の
    簡単なマクロを展開した LaTeX として残し、その ``$…$`` 区間だけを
    :func:`core.theory_modules.schema.plain_math`（数式 → 読める平文の正本）で記号を残した
    平文に落とす。旧実装は :func:`_clean_text` で ``$`` を外したあと論文マクロを未知の命令として
    消しており、``adopting $\\Mpc$ over $\\Mpch$ units`` が「adopting over units」になった
    （IK-0801）。
    """
    if not text:
        return ""
    from core.theory_modules.schema import plain_math

    prose = _clean_prose(text, macros=macros)
    plain = _INLINE_MATH_SEGMENT_RE.sub(
        lambda m: plain_math(m.group("dd") or m.group("d") or m.group("p") or ""), prose
    )
    return re.sub(r"\s+", " ", plain).strip()


def _infer_title(tex: str, macros: dict[str, TexMacro] | None = None) -> str | None:
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
            title = _clean_title(match.group(1), macros)
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


# ---------------------------------------------------------------------------
# 参照・脚注・環境の平文化（IK-0696 / IK-0697 / IK-0698, 2026-10-16）
# ---------------------------------------------------------------------------
#
# 旧実装は ``\ref`` / ``\eqref`` / ``\cite`` を引数ごと空文字にし（「In , we」「Eq.,」
# 「Table .」が残った）、``\footnote{…}`` を中身に置き換えて直前の語へ密着させ
# （「theoremWe remind …」）、数式・図表以外の環境（center / itemize / tabular /
# subequations …）を「コマンド → 引数」の一律規則で環境名・列指定の文字列にしていた。
# ここは情報を落とさない（P4）方向で、参照は読める代用に、脚注は区切りを付けて、環境は
# 開始・終了の印だけを落として中身を残す。

#: 参照先の種類（ラベルの定義位置から決める）。
_REF_KIND_EQUATION = "equation"
_REF_KIND_SECTION = "section"
_REF_KIND_FIGURE = "figure"
_REF_KIND_TABLE = "table"
_REF_KIND_OTHER = "other"

#: 引用の固定の目印（著者名・年は TeX の地の文からは分からないので作らない）。論文の言語に依らない
#: 形にする（英語の本文に日本語の目印を混ぜない — 2610.08908 / F0 seq 1218）。
CITATION_MARKER = "[ref]"
#: 引用が文の主語（``\\citet{…} showed`` / 文頭の ``\\citep{…} computed``）のときの既定の語。
#: 著者名は分からないので作らず、「誰かの研究」であることだけを英文として読める形で残す。
CITATION_SUBJECT = "the authors of " + CITATION_MARKER
_TEXTUAL_CITE_COMMANDS = {"citet", "citeauthor", "textcite", "Citet", "Textcite", "Citeauthor"}
_CITE_AFTER_PREPOSITION_RE = re.compile(
    r"\b(?:of|by|in|see|from|to|and|or|with|following|cf\.|e\.g\.,?|i\.e\.,?|Refs?\.|refs?\.)[\s~]*$"
)
_SENTENCE_START_RE = re.compile(r"(?:^|(?<![A-Z][a-z])(?<!\b[A-Za-z])[.!?:])[\s~]*$")
#: 脚注の区切り（本文の文に溶かさない）。
FOOTNOTE_OPEN = "（脚注: "
FOOTNOTE_CLOSE = "）"

_TEXT_REF_RE = re.compile(
    r"\\(?P<cmd>eqref|ref|autoref|cref|Cref|vref|pageref)\*?\s*\{(?P<keys>[^{}]*)\}"
)
_TEXT_CITE_RE = re.compile(
    r"\\(?P<cite_cmd>cite|citep|citet|citealp|citealt|citeauthor|citeyear|citenum|parencite|textcite|autocite"
    r"|Citet|Textcite|Citeauthor)"
    r"\*?(?:\s*\[[^\]]*\]){0,2}\s*\{[^{}]*\}"
)
#: 色の指定（``\\textcolor{blue}{本文}`` の ``{blue}``・``\\color{red}``）。色名は本文の語ではない。
#: ``\\textcolor`` / ``\\colorbox`` は色の引数だけを落とし、本文の引数は後段の平文化で残る。
_COLOR_ARG_RE = re.compile(
    r"(?<=\\textcolor)\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}|(?<=\\colorbox)\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}"
    r"|\\color\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}"
)
_FOOTNOTE_CMD_RE = re.compile(r"\\footnote(?:text)?\s*(?:\[[^\]]*\])?\s*(?=\{)")
_LINE_BREAK_RE = re.compile(r"\\\\\*?(?:\s*\[[^\]]*\])?")
_ENV_BEGIN_TOKEN_RE = re.compile(
    r"\\begin\s*\{(?P<env>[A-Za-z*]+)\}(?:\s*\[[^\]]*\])*"
)
_ENV_END_TOKEN_RE = re.compile(r"\\end\s*\{[A-Za-z*]+\}")
#: 開始の印の後ろに必須引数（幅・列指定）を取る環境。中身ではないので印と一緒に落とす。
_ENV_WITH_LAYOUT_ARGS = {
    "minipage", "tabular", "tabular*", "tabularx", "tabulary", "longtable",
    "wrapfigure", "wraptable", "multicols", "array", "supertabular",
}
_LAYOUT_ARG = r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}"
_TABLE_RULE_RE = re.compile(
    r"\\(?:cline|cmidrule)(?:\([^)]*\))?\s*\{[^{}]*\}|"
    r"\\(?:hline|toprule|midrule|bottomrule|endhead|endfirsthead|endfoot|endlastfoot|tabularnewline|addlinespace)\b"
)
_MULTI_CELL_RE = re.compile(r"\\multicolumn\s*\{[^{}]*\}\s*\{[^{}]*\}|\\multirow\s*\{[^{}]*\}\s*\{[^{}]*\}")
_CONTROL_SYMBOL_RE = re.compile(r"\\([%$#_&{}])")
_SPACING_SYMBOL_RE = re.compile(r"\\[,;:! /@]")
_LAYOUT_COMMAND_RE = re.compile(
    r"\\(?:vspace|vskip|hspace|hskip|setcounter|addtocounter|renewcommand|newcommand|setlength|addvspace)"
    r"\b\*?(?:\[[^\]]*\])?(?:\s*\{[^{}]*\})*"
)

_TABULAR_ENVS = ("tabular", "tabular*", "tabularx", "tabulary", "longtable", "supertabular")
_TABULAR_WIDTH_ENVS = {"tabular*", "tabularx", "tabulary", "supertabular*"}
_LIST_ENVS = ("itemize", "enumerate", "description", "compactitem", "compactenum")
#: 前付け（題名・著者・所属・連絡先・ORCID）のコマンド。本文ブロックにしない。
_FRONT_MATTER_COMMANDS = (
    "title", "author", "affiliation", "affil", "altaffiliation", "address", "email",
    "orcid", "orcidlink", "institute", "inst", "date", "keywords", "pacs",
    "collaboration", "homepage", "thanks", "correspondingauthor", "subtitle",
)
_FRONT_MATTER_CMD_RE = re.compile(
    r"\\(?P<cmd>" + "|".join(sorted(_FRONT_MATTER_COMMANDS, key=len, reverse=True)) + r")\b\*?"
)
_ORCID_CMD_RE = re.compile(r"\\orcid(?:link)?\s*\{([^{}]*)\}")
_ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
#: ブロックにしてよい本文か（記号・句読点だけのブロックを作らない）。
_MEANINGFUL_TEXT_RE = re.compile(r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff]|\$")

#: 参照の直前の語がすでに参照先の種類を言っているときは番号だけを置く（「Eq. (3)」）。
_REF_PRECEDING_NAME_RE = {
    _REF_KIND_EQUATION: re.compile(
        r"(?:\bEqn?s?\b\.?|\bEquations?|式|[)}][\s~]*(?:,|and|or|to|-|–))[\s~]*$", re.IGNORECASE
    ),
    # 「Sect.」「Sects.」・ピリオドなしの「Sec」「Fig」「Tab」も種類の名前として読む（IK-0723:
    # 「Sect.~\cref{sec:x}」が「Sect. Sec. 3」、「Fig~\ref{f}」が「Fig 図 1」になった）。
    _REF_KIND_SECTION: re.compile(
        r"(?:\bSec(?:ts?|s)?\b\.?|\bSections?|§|\bApp(?:endix|endices|\.)|\bChapters?|\bCh\.|節)[\s~]*$",
        re.IGNORECASE,
    ),
    _REF_KIND_FIGURE: re.compile(r"(?:\bFigs?\b\.?|\bFigures?|\bPanels?|図)[\s~]*$", re.IGNORECASE),
    _REF_KIND_TABLE: re.compile(r"(?:\bTabs?\b\.?|\bTables?|表)[\s~]*$", re.IGNORECASE),
}
#: 種類の名前の語だけ（「Eq.」「Appendix」）— 並びの継ぎ目（「) and」）は含めない。
_REF_KIND_NAME_ONLY_RE = {
    _REF_KIND_EQUATION: re.compile(r"(?:\bEqn?s?\b\.?|\bEquations?|式)[\s~]*$", re.IGNORECASE),
    _REF_KIND_SECTION: _REF_PRECEDING_NAME_RE[_REF_KIND_SECTION],
    _REF_KIND_FIGURE: _REF_PRECEDING_NAME_RE[_REF_KIND_FIGURE],
    _REF_KIND_TABLE: _REF_PRECEDING_NAME_RE[_REF_KIND_TABLE],
}
#: 英語の本文での前置き（本文に日本語の文字が無いとき。英文に「式」を混ぜない — G2 seq 1340）。
_REF_PREFIX_EN = {
    _REF_KIND_EQUATION: "Eq. ",
    _REF_KIND_SECTION: "Sec. ",
    _REF_KIND_FIGURE: "Fig. ",
    _REF_KIND_TABLE: "Tab. ",
}
_JAPANESE_TEXT_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_REF_PREFIX = {
    _REF_KIND_EQUATION: "式 ",
    _REF_KIND_SECTION: "§",
    _REF_KIND_FIGURE: "図 ",
    _REF_KIND_TABLE: "表 ",
}


@dataclass(frozen=True)
class TexLabelTarget:
    """``\\label`` の参照先。``number`` は印字番号を決定論で数えられたときだけ入る。"""

    kind: str
    number: str | None = None


def _guess_ref_kind(key: str) -> str:
    lowered = (key or "").strip().lower()
    head = re.split(r"[:._-]", lowered, maxsplit=1)[0]
    if head in {"eq", "eqn", "eqs", "equation", "e"}:
        return _REF_KIND_EQUATION
    if head in {"sec", "ssec", "subsec", "section", "app", "appendix", "ch", "chap", "chapter", "s"}:
        return _REF_KIND_SECTION
    if head in {"fig", "figure", "f"}:
        return _REF_KIND_FIGURE
    if head in {"tab", "table", "tbl", "t"}:
        return _REF_KIND_TABLE
    return _REF_KIND_OTHER


#: 範囲・列挙の継ぎ目の直前にある参照 1 つ（「\\ref{app:B}--」「(\\ref{a}), 」「\\ref{b} and 」）。
#: 末尾から繰り返し剥がし、先頭の語（Appendices / Eqs. / Figs. / Secs.）まで遡る。
_REF_CHAIN_TAIL_RE = re.compile(
    r"\(?\\(?:eqref|ref|autoref|cref|Cref|vref)\*?\s*\{[^{}]*\}\)?"
    r"(?:\\[,;:! ]|[\s~])*(?:---|--|-|–|—|,|&|\band\b|\bor\b|\bto\b)(?:\\[,;:! ]|[\s~])*\(?$",
    re.IGNORECASE,
)


def _strip_ref_chain(preceding: str) -> str:
    """直前の文字列から、範囲・列挙で先に並んだ参照の連なりを剥がした残り（先頭の語の側）を返す。

    ``Appendices~\\ref{app:B}--\\ref{app:F}`` の 2 つ目なら ``Appendices~`` を返す。連なりが
    無ければ入力をそのまま返す。
    """
    current = preceding or ""
    for _ in range(64):
        match = _REF_CHAIN_TAIL_RE.search(current)
        if not match:
            break
        current = current[: match.start()]
    return current


def _render_one_ref(
    cmd: str,
    key: str,
    preceding: str,
    labels: dict[str, TexLabelTarget] | None,
    *,
    english: bool = False,
) -> str:
    target = (labels or {}).get(key)
    kind = target.kind if target else _guess_ref_kind(key)
    if cmd == "eqref":
        kind = _REF_KIND_EQUATION
    token = (target.number if target and target.number else None) or key
    if kind == _REF_KIND_OTHER:
        return token
    # 「Sec.\\,\\ref」「Eq.\\ \\ref」— 細い空白の印も語と参照の間の空白として読む。
    preceding = re.sub(r"(?:\\[,;:! ]|[\s~])+$", " ", preceding or "")
    # 範囲・列挙（「Appendices~\\ref{B}--\\ref{F}」「Figs.~\\ref{a}, \\ref{b} and \\ref{c}」）の
    # 2 つ目以降は、連なりの先頭の語で判断する（先頭の語がある限り番号だけ。「B--Sec. F」にしない）。
    base = _strip_ref_chain(preceding)
    lead = base if base != preceding else preceding
    if cmd not in {"cref", "Cref", "autoref", "eqref"} and any(
        other != kind and pattern.search(lead) for other, pattern in _REF_KIND_NAME_ONLY_RE.items()
    ):
        # 直前の語が別の種類を言っている（「Appendix \\ref{…}」のラベルが組版上は式の番号を指す）
        # ときは、LaTeX が印字するとおり番号だけを置く（「Appendix 式 (A.4)」にしない）。
        return token
    if kind == _REF_KIND_EQUATION and cmd != "eqref" and re.search(r"\($", (preceding or "").rstrip(" ~")):
        # 「Eq.~(\ref{eq:a})」— 括弧は著者が書いているので番号（かラベル）だけを置く。
        return token
    body = f"({token})" if kind == _REF_KIND_EQUATION else token
    # \cref / \autoref も、直前の語がすでに同じ種類を言っていれば番号だけを置く。LaTeX は種類名を
    # 重ねて印字するが、それは原文の重複であって読み手に渡す情報ではない（IK-0723:
    # 「Sect.~\cref{sec:x}」→「Sect. Sec. 3」）。\cref 系は名前の語だけで判断する（並びの継ぎ目
    # 「) and」は名前ではない）。連なりの先頭の語（「Sects.~\cref{a} and \cref{b}」）も見る。
    if cmd in {"cref", "Cref", "autoref"}:
        name_re = _REF_KIND_NAME_ONLY_RE[kind]
        named = bool(name_re.search(preceding or "") or name_re.search(lead))
    else:
        named = bool(
            _REF_PRECEDING_NAME_RE[kind].search(preceding or "") or _REF_PRECEDING_NAME_RE[kind].search(lead)
        )
    if named:
        return body
    return f"{(_REF_PREFIX_EN if english else _REF_PREFIX)[kind]}{body}"


def _tex_quotes(text: str) -> str:
    """TeX の引用符（````…''`` / ```…'``）を ``"…"`` / ``'…'`` にする（地の文の断片のみ・数式は通らない）。"""
    if "`" not in text and "''" not in text:
        return text
    text = text.replace("``", '"').replace("''", '"')
    return text.replace("`", "'")


#: 引用の目印の直後で句点と次の文の大文字が詰まっている箇所（原文 ``\\cite{…}.In the PBS``）。
_CITATION_RUN_ON_RE = re.compile(re.escape(CITATION_MARKER) + r"([.!?])(?=[A-Z])")


def _replace_citations(text: str) -> str:
    if not text or "\\cite" not in text and "cite" not in text:
        return text or ""
    replaced = _TEXT_CITE_RE.sub(lambda m: _render_citation(m, text), text)
    # 原文が ``\\cite{…}.In`` と空白なしで次の文を続けていても、文の区切りの空白を補う
    # （2026-10-16, radar_loop S0 seq 2790: 「[ref].In the PBS」で 2 文が 1 span に融合した）。
    # 語は足さない — 空白 1 字だけ。
    return _CITATION_RUN_ON_RE.sub(lambda m: f"{CITATION_MARKER}{m.group(1)} ", replaced)


def _render_citation(match: re.Match[str], text: str) -> str:
    """引用 1 つを :data:`CITATION_MARKER` に置く。主語の位置なら :data:`CITATION_SUBJECT`。

    主語の位置 = ``\\citet`` 系（natbib が著者名を出す書き方）か、文頭に置かれた引用。
    前置詞の後（``by \\citet{x}``）は主語ではないので目印だけにする。
    """
    cmd = match.group("cite_cmd")
    preceding = text[: match.start()]
    if cmd == "citeyear":
        return CITATION_MARKER
    if _CITE_AFTER_PREPOSITION_RE.search(preceding):
        return CITATION_MARKER
    at_sentence_start = bool(_SENTENCE_START_RE.search(preceding))
    if cmd in _TEXTUAL_CITE_COMMANDS or at_sentence_start:
        if at_sentence_start:
            return CITATION_SUBJECT[0].upper() + CITATION_SUBJECT[1:]
        return CITATION_SUBJECT
    return CITATION_MARKER


def _is_english_text(text: str) -> bool:
    """日本語の文字（かな・漢字）を含まない本文を英語の本文とみなす。"""
    # 取り込みが付ける目印（脚注の区切り）は本文の言語ではない。
    stripped = _TEXT_REF_RE.sub("", text or "").replace(FOOTNOTE_OPEN, " ").replace(FOOTNOTE_CLOSE, " ")
    return not _JAPANESE_TEXT_RE.search(stripped)


def _replace_text_refs(
    text: str,
    labels: dict[str, TexLabelTarget] | None = None,
    *,
    english: bool | None = None,
) -> str:
    """地の文の ``\\ref`` 系・``\\cite`` 系を読める代用に置く（空文字にしない — IK-0697）。

    - 式: 印字番号が数えられれば「式 (3)」、数えられなければラベルを残して「式 (eq:main)」。
      直前の語が「Eq.」「Eqs.」「式」なら「(3)」だけを置く（「Eq. 式 (3)」にしない）。
    - 節・図・表: 「§2」「図 1」「表 2」（直前が「Section」「Fig.」「Table」なら番号だけ）。
    - 引用: 固定の目印 :data:`CITATION_MARKER` を 1 つ（複数キーでも 1 つ・著者名は作らない）。
    """
    if not text or "\\" not in text:
        return text or ""
    text = _replace_citations(text)
    # 本文の言語に合わせて前置きを選ぶ（日本語の文字が無ければ英語の本文として「Eq. (5.5)」）。
    if english is None:
        english = _is_english_text(text)

    def replace(match: re.Match[str]) -> str:
        cmd = match.group("cmd")
        keys = [_label_key(key) for key in _split_tex_keys(match.group("keys"))]
        if cmd == "pageref":
            return ""
        if not keys:
            return ""
        preceding = text[: match.start()]
        rendered = [_render_one_ref(cmd, keys[0], preceding, labels, english=english)]
        rendered.extend(
            _render_one_ref(cmd, key, "式" if cmd == "eqref" else preceding, labels, english=english)
            for key in keys[1:]
        )
        joined = ", ".join(rendered)
        # 「Eq.\\eqref{…}」「Sec.\\ref{…}」のように語と参照が密着していれば空白を 1 つ挟む。
        if joined and preceding and re.search(r"[A-Za-z0-9.,;:]$", preceding):
            return " " + joined
        return joined

    return _TEXT_REF_RE.sub(replace, text)


def _mark_footnotes(text: str) -> str:
    """``\\footnote{…}`` を「（脚注: …）」に置き、本文の語へ密着させない（IK-0698）。"""
    if not text or "\\footnote" not in text:
        return text or ""
    parts: list[str] = []
    pos = 0
    while True:
        match = _FOOTNOTE_CMD_RE.search(text, pos)
        if not match:
            break
        body, end = _read_balanced_braced(text, match.end())
        if body is None:
            parts.append(text[pos:match.end()])
            pos = match.end()
            continue
        parts.append(text[pos:match.start()].rstrip())
        inner = _mark_footnotes(body).strip()
        if inner:
            parts.append(f"{FOOTNOTE_OPEN}{inner}{FOOTNOTE_CLOSE}")
        pos = end
    parts.append(text[pos:])
    return "".join(parts)


#: 文末の句点の直前に来ても文を終えない略語（「Fig. 3」「et al. (2020)」）。小文字で比べる。
_NON_TERMINAL_ABBREVIATIONS = {
    "fig", "figs", "eq", "eqs", "eqn", "eqns", "sec", "secs", "sect", "sects", "tab", "tabs",
    "ref", "refs", "al", "cf", "vs", "e.g", "i.e", "app", "ch", "approx", "resp", "no", "vol", "ed", "eds",
}
_SENTENCE_CLOSERS = "\"'’”)]}"


def _split_footnote_markers(text: str) -> tuple[str, list[tuple[int, str]]]:
    """本文から「（脚注: …）」を外す。戻り値は (脚注を除いた本文, [(本文上の挿入位置, 脚注の印)])。

    脚注の中の脚注（入れ子）は外側の印に含めたまま 1 つとして扱う。閉じが見つからない印は
    本文に残す（情報を落とさない）。
    """
    base: list[str] = []
    found: list[tuple[int, str]] = []
    pos = 0
    length = 0
    while True:
        start = text.find(FOOTNOTE_OPEN, pos)
        if start < 0:
            break
        depth = 0
        index = start
        end = -1
        while index < len(text):
            if text.startswith(FOOTNOTE_OPEN, index):
                depth += 1
                index += len(FOOTNOTE_OPEN)
                continue
            if text.startswith(FOOTNOTE_CLOSE, index):
                depth -= 1
                index += len(FOOTNOTE_CLOSE)
                if depth == 0:
                    end = index
                    break
                continue
            index += 1
        if end < 0:
            break
        before = text[pos:start].rstrip()
        base.append(before)
        length += len(before)
        found.append((length, text[start:end]))
        pos = end
    base.append(text[pos:])
    return "".join(base), found


def _sentence_end_after(text: str, position: int) -> int | None:
    """``position`` 以降で文が終わる位置（句点と閉じ記号の直後）。数式 ``$…$`` の中は見ない。

    文の終わり = 「. ! ?」の後に閉じ記号が続き、その後が空白 + 大文字・数式・角括弧、または本文の末尾。
    句点の直前の語が略語（「Fig.」「et al.」）なら文の終わりとみなさない。見つからなければ None。
    """
    in_math = False
    index = position
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            index += 2
            continue
        if char == "$":
            in_math = not in_math
            index += 1
            continue
        if not in_math and char in ".!?":
            end = index + 1
            while end < len(text) and text[end] in _SENTENCE_CLOSERS:
                end += 1
            rest = text[end:]
            word = re.search(r"([A-Za-z][A-Za-z.]*)$", text[:index])
            abbreviation = bool(word) and word.group(1).lower().rstrip(".") in _NON_TERMINAL_ABBREVIATIONS
            if not abbreviation and (not rest.strip() or re.match(r"\s+[A-Z$\[(\"“]", rest)):
                return end
        index += 1
    return None


def _relocate_footnotes_to_sentence_end(text: str) -> tuple[str, list[str]]:
    """「（脚注: …）」を書かれた位置から所属する文の終わりへ移し、脚注の本文の一覧も返す（IK-0713）。

    TeX の ``\\footnote`` は文の途中（語の直後）にも置かれる。その位置に括弧書きを残すと本文の 1 文が
    脚注で割れ、後段の文分割で「 leading to」だけの断片ができる。文の終わりが判定できなければ
    ブロックの末尾に置く。同じ文に複数あれば書かれた順に並べる。脚注は捨てない（P4）。
    """
    if not text or FOOTNOTE_OPEN not in text:
        return text, []
    base, found = _split_footnote_markers(text)
    if not found:
        return text, []
    groups: dict[int, list[str]] = {}
    for position, marker in found:
        before = base[:position].rstrip()
        terminal = before.rstrip(_SENTENCE_CLOSERS)
        if terminal[-1:] in {".", "!", "?"} and _sentence_end_after(base, len(terminal) - 1) == len(before):
            # 文末の句点の後ろに書かれた脚注はその場（次の文の終わりへ送らない）。
            target = len(before)
        else:
            target = _sentence_end_after(base, position)
            if target is None:
                target = len(base.rstrip())
        groups.setdefault(target, []).append(marker)
    pieces: list[str] = []
    cursor = 0
    for target in sorted(groups):
        pieces.append(base[cursor:target])
        pieces.append("".join(groups[target]))
        cursor = target
    tail = base[cursor:]
    if tail and not tail[0].isspace() and pieces and pieces[-1].endswith(FOOTNOTE_CLOSE):
        tail = " " + tail
    pieces.append(tail)
    out = re.sub(r"[ \t]+", " ", "".join(pieces)).strip()
    bodies = [marker[len(FOOTNOTE_OPEN):-len(FOOTNOTE_CLOSE)].strip() for _, marker in found]
    return out, [body for body in bodies if body]


def _strip_env_tokens(text: str) -> str:
    """数式・図表以外の環境の開始・終了の印（と幅・列指定）を落とし、中身を残す（IK-0696）。"""
    if not text or ("\\begin" not in text and "\\end" not in text):
        return text or ""
    pieces: list[str] = []
    pos = 0
    for match in _ENV_BEGIN_TOKEN_RE.finditer(text):
        if match.start() < pos:
            continue
        end = match.end()
        env = match.group("env")
        if env in _ENV_WITH_LAYOUT_ARGS:
            for _ in range(2 if env in _TABULAR_WIDTH_ENVS else 1):
                arg_pos = _skip_ws(text, end)
                if arg_pos >= len(text) or text[arg_pos] != "{":
                    break
                arg, arg_end = _read_balanced_braced(text, arg_pos)
                if arg is None:
                    break
                end = arg_end
        pieces.append(text[pos:match.start()])
        pieces.append(" ")
        pos = end
    pieces.append(text[pos:])
    return _ENV_END_TOKEN_RE.sub(" ", "".join(pieces))


def _is_meaningful_text(text: str) -> bool:
    return bool(_MEANINGFUL_TEXT_RE.search(text or ""))


def _find_env_end(text: str, env: str, start: int) -> tuple[int, int] | None:
    """``start`` 以降で ``env`` を閉じる ``\\end{env}`` の (開始, 終了)。入れ子を数える。"""
    token_re = re.compile(r"\\(?P<kind>begin|end)\s*\{" + re.escape(env) + r"\}")
    depth = 1
    for match in token_re.finditer(text, start):
        depth += 1 if match.group("kind") == "begin" else -1
        if depth == 0:
            return match.start(), match.end()
    return None


def _split_top_level(text: str, separator: str) -> list[str]:
    """波括弧・入れ子環境の外にある区切り（``\\\\`` か ``&``）で分ける。"""
    parts: list[str] = []
    depth = 0
    env_depth = 0
    buf: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("\\begin", i):
            env_depth += 1
        elif text.startswith("\\end", i):
            env_depth = max(0, env_depth - 1)
        char = text[i]
        if separator == "\\\\" and text.startswith("\\\\", i) and depth == 0 and env_depth == 0:
            parts.append("".join(buf))
            buf = []
            match = _LINE_BREAK_RE.match(text, i)
            i = match.end() if match else i + 2
            continue
        if char == "\\" and i + 1 < n:
            buf.append(text[i:i + 2])
            i += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif separator == "&" and char == "&" and depth == 0 and env_depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    parts.append("".join(buf))
    return parts


def _read_tabular_body(text: str, env: str, after_begin: int) -> tuple[str, int] | None:
    """``\\begin{tabular}`` 直後から位置・幅・列指定を読み飛ばし、(本体, 終了位置) を返す。"""
    pos = _skip_ws(text, after_begin)
    if pos < len(text) and text[pos] == "[":
        _opt, pos = _read_bracket_arg(text, pos)
        pos = _skip_ws(text, pos)
    arg_count = 2 if env in _TABULAR_WIDTH_ENVS else 1
    for _ in range(arg_count):
        pos = _skip_ws(text, pos)
        if pos < len(text) and text[pos] == "{":
            _arg, end = _read_balanced_braced(text, pos)
            if _arg is None:
                return None
            pos = end
    closing = _find_env_end(text, env, pos)
    if closing is None:
        return None
    return text[pos:closing[0]], closing[1]


def _tabular_rows(body: str, *, macros, labels) -> list[str]:
    """表組みの本体を「セル | セル」の行の並びにする（列指定・罫線は落とす）。"""
    body = _TABLE_RULE_RE.sub(" ", body or "")
    body = _MULTI_CELL_RE.sub(" ", body)
    rows: list[str] = []
    for raw_row in _split_top_level(body, "\\\\"):
        cells = [_clean_prose(cell, macros=macros, labels=labels) for cell in _split_top_level(raw_row, "&")]
        cells = [cell for cell in cells if cell]
        if cells and any(_is_meaningful_text(cell) for cell in cells):
            rows.append(" | ".join(cells))
    return rows


def _list_lines(env: str, body: str, *, macros, labels, depth: int = 0) -> list[str]:
    """itemize / enumerate / description の項目を 1 項目 1 行にする（入れ子は字下げ）。"""
    lines: list[str] = []
    chunks = _split_items(body)
    number = 0
    indent = "  " * depth
    for label, chunk in chunks:
        nested: list[str] = []
        rest_parts: list[str] = []
        pos = 0
        for match in re.finditer(r"\\begin\s*\{(?P<env>" + "|".join(_LIST_ENVS) + r")\}", chunk):
            if match.start() < pos:
                continue
            closing = _find_env_end(chunk, match.group("env"), match.end())
            if closing is None:
                continue
            rest_parts.append(chunk[pos:match.start()])
            nested.extend(
                _list_lines(match.group("env"), chunk[match.end():closing[0]], macros=macros, labels=labels, depth=depth + 1)
            )
            pos = closing[1]
        rest_parts.append(chunk[pos:])
        text = _clean_prose("".join(rest_parts), macros=macros, labels=labels)
        term = _clean_prose(label, macros=macros, labels=labels) if label else ""
        if not text and not term and not nested:
            continue
        number += 1
        if env in {"enumerate", "compactenum"}:
            marker = f"{number}. "
        elif term:
            marker = f"{term}: "
        else:
            marker = "- "
        if text or term:
            lines.append(f"{indent}{marker}{text}".rstrip())
        lines.extend(nested)
    return lines


def _split_items(body: str) -> list[tuple[str, str]]:
    """リスト本体を最上位の ``\\item[ラベル]`` で分ける（入れ子のリストの中の \\item は分けない）。"""
    items: list[tuple[str, str]] = []
    depth = 0
    current_label: str | None = None
    buf_start: int | None = None
    token_re = re.compile(r"\\(?P<kind>begin|end)\s*\{[A-Za-z*]+\}|\\item\b")
    for match in token_re.finditer(body):
        kind = match.group("kind")
        if kind == "begin":
            depth += 1
            continue
        if kind == "end":
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        if buf_start is not None:
            items.append((current_label or "", body[buf_start:match.start()]))
        pos = _skip_ws(body, match.end())
        current_label = None
        if pos < len(body) and body[pos] == "[":
            label, pos = _read_bracket_arg(body, pos)
            current_label = label
        buf_start = pos
    if buf_start is not None:
        items.append((current_label or "", body[buf_start:]))
    return items


def _read_front_matter_command(text: str, match: re.Match[str]) -> tuple[str, int] | None:
    pos = _skip_ws(text, match.end())
    while pos < len(text) and text[pos] == "[":
        _opt, pos = _read_bracket_arg(text, pos)
        if _opt is None:
            return None
        pos = _skip_ws(text, pos)
    if pos >= len(text) or text[pos] != "{":
        return None
    body, end = _read_balanced_braced(text, pos)
    if body is None:
        return None
    return body, end


def _split_text_segment(text: str, *, before_first_section: bool) -> list[tuple[str, str, str]]:
    """本文の区間を (種別, 環境名/コマンド, 中身) の並びに分ける。

    種別は ``text``（段落へ）/ ``table_body``（表組みの本体）/ ``list``（箇条書き）/
    ``front_matter``（題名・著者・所属・ORCID 等）。
    """
    pieces: list[tuple[str, str, str]] = []
    env_pattern = "|".join(re.escape(env) for env in (*_TABULAR_ENVS, *_LIST_ENVS))
    combined = re.compile(
        r"\\begin\s*\{(?P<env>" + env_pattern + r")\}"
        r"|" + _FRONT_MATTER_CMD_RE.pattern
        + (r"|\\begin\s*\{(?P<center>center)\}" if before_first_section else "")
        + r"|\\begin\s*\{(?P<abstract>abstract)\}"
    )
    pos = 0
    scan = 0
    while True:
        match = combined.search(text, scan)
        if not match:
            break
        if match.group("env"):
            env = match.group("env")
            if env in _TABULAR_ENVS:
                parsed = _read_tabular_body(text, env, match.end())
                if parsed is None:
                    scan = match.end()
                    continue
                body, end = parsed
                kind = "table_body"
            else:
                closing = _find_env_end(text, env, match.end())
                if closing is None:
                    scan = match.end()
                    continue
                body, end = text[match.end():closing[0]], closing[1]
                kind = "list"
            pieces.append(("text", "", text[pos:match.start()]))
            pieces.append((kind, env, body))
        elif match.groupdict().get("abstract"):
            closing = _find_env_end(text, "abstract", match.end())
            if closing is None:
                scan = match.end()
                continue
            body, end = text[match.end():closing[0]], closing[1]
            pieces.append(("text", "", text[pos:match.start()]))
            pieces.append(("abstract", "abstract", body))
        elif match.groupdict().get("center"):
            closing = _find_env_end(text, "center", match.end())
            if closing is None:
                scan = match.end()
                continue
            body, end = text[match.end():closing[0]], closing[1]
            if not _looks_like_front_matter(body):
                scan = match.end()
                continue
            pieces.append(("text", "", text[pos:match.start()]))
            pieces.append(("front_matter", "center", body))
        else:
            parsed = _read_front_matter_command(text, match)
            if parsed is None:
                scan = match.end()
                continue
            body, end = parsed
            pieces.append(("text", "", text[pos:match.start()]))
            pieces.append(("front_matter", match.group("cmd"), body))
        pos = scan = end
    pieces.append(("text", "", text[pos:]))
    return [piece for piece in pieces if piece[0] != "text" or piece[2].strip()]


def _looks_like_front_matter(raw: str) -> bool:
    """節の前の center 環境・段落が前付け（著者行・所属・ORCID・連絡先）か。"""
    if _ORCID_RE.search(raw or "") or _EMAIL_RE.search(raw or ""):
        return True
    if re.search(r"\\(?:orcid|orcidlink|inst|affil|thanks|email|footnotemark)\b", raw or ""):
        return True
    # 改行区切りの短い行が並び、文末の句点が無い（題名・著者・所属の塊）。
    lines = [line.strip() for line in _LINE_BREAK_RE.split(raw or "") if line.strip()]
    return len(lines) >= 2 and not any(re.search(r"[.!?]\s*$", _clean_text(line)) and len(_clean_text(line)) > 80 for line in lines)


def _build_label_map(body: str, *, preamble: str = "") -> dict[str, TexLabelTarget]:
    """``\\label`` → 参照先（種類と、決定論で数えられるときだけ印字番号）。

    LaTeX の番号付けの規則のうち、確実に再現できる範囲だけを数える（式: 番号付き環境の
    行単位・``\\nonumber`` / ``\\notag`` / ``\\tag`` / subequations、節: section 系、
    図・表: ``\\caption`` の出現順）。``\\numberwithin`` / ``\\theequation`` の再定義 /
    カウンタの直接設定 / ``\\appendix`` 以降の式番号は数えず、ラベルの種類だけを返す
    （違う番号を出すより、ラベルを残す方が正直 — IK-0697 / IK-0681）。
    """
    labels: dict[str, TexLabelTarget] = {}
    # 番号付けの宣言は同梱 .sty を含む前付けにも書かれる（``\\numberwithin{equation}{section}``）。
    declarations = (preamble or "") + "\n" + (body or "")
    # ``\\numberwithin{equation}{section}`` だけは決定論で数えられる（「節番号.通し番号」・節ごとに
    # 振り直し）。それ以外の番号付けの変更は数えずラベルを残す（違う番号を出さない）。
    section_wise = bool(re.search(r"\\(?:numberwithin|counterwithin)\s*\{equation\}\s*\{section\}", declarations))
    # ``\\@addtoreset{equation}{section}`` は numberwithin の古い書き方（節ごとに振り直す）。
    within_pairs = re.findall(
        r"\\(?:numberwithin|counterwithin|@addtoreset)\s*\{([^{}]*)\}\s*\{([^{}]*)\}", declarations
    )
    equation_within = [parent.strip() for counter, parent in within_pairs if counter.strip() == "equation"]
    section_wise = bool(equation_within) and all(parent == "section" for parent in equation_within)
    # ``\\renewcommand{\\theequation}{\\thesection.\\arabic{equation}}`` は numberwithin と同じ形の
    # 宣言（.sty で併記されることが多い）。それ以外の \\theequation の再定義は数えない。
    theequation_defs = re.findall(
        r"\\(?:re)?newcommand\s*\{?\\theequation\}?\s*\{((?:[^{}]|\{[^{}]*\})*)\}", declarations
    )
    section_style_only = all(
        re.sub(r"\s+", "", body_def) == r"\thesection.\arabic{equation}" for body_def in theequation_defs
    )
    eq_numbering = (
        not re.search(r"\\(?:setcounter|addtocounter)\s*\{equation\}", declarations)
        and (not theequation_defs or (section_wise and section_style_only))
        and (not equation_within or section_wise)
    )
    sec_numbering = _section_numbering_enabled(body, preamble)
    if section_wise and not sec_numbering:
        eq_numbering = False
    float_numbering = not re.search(
        r"\\(?:re)?newcommand\s*\{?\\the(?:figure|table)|\\(?:setcounter|addtocounter)\s*\{(?:figure|table)\}",
        declarations,
    )
    counters = {"equation": 0, "section": 0, "subsection": 0, "subsubsection": 0, "figure": 0, "table": 0}
    appendix = False
    current = TexLabelTarget(_REF_KIND_OTHER, None)

    math_envs = r"equation\*?|align\*?|eqnarray\*?|gather\*?|multline\*?|flalign\*?|alignat\*?"
    token_re = re.compile(
        r"\\(?P<sec>section|subsection|subsubsection)(?P<star>\*?)\s*(?:\[[^\]]*\])?\s*\{"
        r"|\\begin\s*\{(?P<math>" + math_envs + r")\}"
        r"|\\begin\s*\{(?P<float>figure|table)\*?\}"
        r"|\\begin\s*\{(?P<sub>subequations)\}"
        r"|\\end\s*\{(?P<subend>subequations)\}"
        r"|\\begin\s*\{(?P<other>[A-Za-z*]+)\}"
        r"|\\label\s*\{(?P<label>[^{}]+)\}"
        r"|\\(?P<appendix>appendix)\b"
        r"|\$\$(?P<dd>.*?)\$\$|\\\[(?P<br>.*?)\\\]",
        re.DOTALL,
    )
    transparent = {"center", "flushleft", "flushright", "minipage", "quote", "quotation",
                   "abstract", "document", "itemize", "description", "small", "footnotesize"}
    sub_parent: str | None = None
    sub_letter = 0
    section_head = ""
    section_target: TexLabelTarget | None = None

    def next_equation_number() -> str | None:
        nonlocal sub_letter
        if sub_parent is not None:
            sub_letter += 1
            return f"{sub_parent}{chr(ord('a') + sub_letter - 1)}" if sub_parent and sub_letter <= 26 else None
        counters["equation"] += 1
        return equation_token(counters["equation"])

    def equation_token(value: int) -> str | None:
        if not eq_numbering:
            return None
        if section_wise:
            # ``\\numberwithin{equation}{section}``: 「節番号.通し番号」（付録は「A.1」）。
            return f"{section_head}.{value}" if section_head else None
        return str(value) if not appendix else None

    pos = 0
    while True:
        match = token_re.search(body, pos)
        if not match:
            break
        pos = match.end()
        if match.group("sec"):
            if match.group("star"):
                continue
            name = match.group("sec")
            counters[name] += 1
            if name == "section":
                counters["subsection"] = counters["subsubsection"] = 0
                if section_wise:
                    counters["equation"] = 0
                    section_head = (
                        chr(ord("A") + counters["section"] - 1) if appendix and counters["section"] <= 26
                        else ("" if appendix else str(counters["section"]))
                    ) if sec_numbering else ""
            elif name == "subsection":
                counters["subsubsection"] = 0
            top = (chr(ord("A") + counters["section"] - 1) if appendix and counters["section"] <= 26 else str(counters["section"]))
            parts = [top]
            if name in {"subsection", "subsubsection"}:
                parts.append(str(counters["subsection"]))
            if name == "subsubsection":
                parts.append(str(counters["subsubsection"]))
            current = TexLabelTarget(_REF_KIND_SECTION, ".".join(parts) if sec_numbering else None)
            section_target = current
        elif match.group("math"):
            env = match.group("math")
            closing = _find_env_end(body, env, match.end())
            if closing is None:
                continue
            env_body = body[match.end():closing[0]]
            pos = closing[1]
            starred = env.endswith("*")
            base = env.rstrip("*")
            rows = [env_body] if base in {"equation", "multline"} else _split_top_level(env_body, "\\\\")
            last = None
            for row in rows:
                if not row.strip() and len(rows) > 1:
                    continue
                tag = re.search(r"\\tag\*?\s*\{([^{}]*)\}", row)
                if tag:
                    number = tag.group(1).strip() or None
                elif starred or re.search(r"\\(?:nonumber|notag)\b", row):
                    # 番号の付かない行は LaTeX の参照先を進めない（\\label は直前の参照先 —
                    # 節・付録・前の式 — を指す。2610.08908 の付録の \\paragraph ラベル）。
                    for label in _LABEL_RE.finditer(row):
                        labels.setdefault(_label_key(label.group(1)), last or current)
                    continue
                else:
                    number = next_equation_number()
                target = TexLabelTarget(_REF_KIND_EQUATION, number)
                for label in _LABEL_RE.finditer(row):
                    labels.setdefault(_label_key(label.group(1)), target)
                last = target
            if last is not None:
                current = last
        elif match.group("float"):
            kind = match.group("float")
            closing = _find_env_end(body, kind + ("*" if body[match.end() - 2] == "*" else ""), match.end())
            if closing is None:
                closing = _find_env_end(body, kind, match.end())
            if closing is None:
                continue
            env_body = body[match.end():closing[0]]
            pos = closing[1]
            float_current = TexLabelTarget(kind, None)
            float_labels: dict[str, TexLabelTarget] = {}
            for token in re.finditer(r"\\(?P<cap>caption)\b|\\label\s*\{(?P<lab>[^{}]+)\}", env_body):
                if token.group("cap"):
                    counters[kind] += 1
                    float_current = TexLabelTarget(kind, str(counters[kind]) if float_numbering else None)
                else:
                    # 同じ図表の中で同じラベルを caption の前後に二重に書いた場合（2610.08908 の
                    # ``\\begin{table}\\label{tab:priors} … \\caption{…}\\label{tab:priors}``）は、LaTeX と
                    # 同じく後の定義（caption の後 = 表の番号）を採る。
                    float_labels[_label_key(token.group("lab"))] = float_current
            for key, target in float_labels.items():
                labels.setdefault(key, target)
        elif match.group("sub"):
            counters["equation"] += 1
            sub_parent = equation_token(counters["equation"]) or ""
            sub_letter = 0
            current = TexLabelTarget(_REF_KIND_EQUATION, sub_parent or None)
        elif match.group("subend"):
            sub_parent = None
        elif match.group("other"):
            if match.group("other").rstrip("*") not in transparent:
                current = TexLabelTarget(_REF_KIND_OTHER, None)
        elif match.group("label"):
            target = current
            if section_target is not None and _PARAGRAPH_LABEL_TAIL_RE.search(body[max(0, match.start() - 400):match.start()]):
                # ``\\paragraph{…}\\label{app:x}`` は著者が段落見出し（節）を指したラベル。LaTeX の
                # 組版上は直前の式の番号になるが、「Appendix A.2」より「Appendix A」と読むのが著者の
                # 意図なので、囲んでいる節の番号を採る（2610.08908 の付録・G1 seq 1243）。
                target = section_target
            labels.setdefault(_label_key(match.group("label")), target)
        elif match.group("appendix"):
            appendix = True
            counters["section"] = 0
        # $$…$$ / \[…\] は番号を持たないので読み飛ばすだけ。
    return labels


def _looks_like_front_matter_paragraph(raw: str) -> bool:
    """最初の節より前の段落が前付けか（ORCID・連絡先・所属の印があるときだけ — 要旨を巻き込まない）。"""
    if _ORCID_RE.search(raw or "") or _EMAIL_RE.search(raw or ""):
        return True
    return bool(re.search(r"\\(?:orcid|orcidlink|inst|affil|affiliation|email|thanks)\b", raw or ""))
