"""GROBID TEI XML → DocumentStructureResult 変換パーサ。

TEI XML の論文固有構造（abstract / sections / paragraphs / equations /
figure captions / table captions / references）を解析して TypedBlock と
Section のリストを生成する。

設計方針:
- structure-first: タグ構造・見出し階層を優先し、意味解釈は行わない
- references / acknowledgments は除外して body_paragraph に混入させない
- PyMuPDF 補完なしで単体動作できる（bbox / font_size は None）
- parser_source = "grobid_tei" を各 block.raw に付与する
- 情報を落とさない: 付録（``<back><div type="annex">``）・本文直下の
  ``<figure>``・表本体も TEI にある限り block として残す
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .schema import DocumentMetadata, Section, TypedBlock

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup, Tag
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False


_EXCLUDED_HEADINGS = re.compile(
    r"(references?|bibliography|acknowledgm|funding|competing\s+interest|"
    r"conflict\s+of\s+interest|author\s+contribution|data\s+availability)",
    re.IGNORECASE,
)

# TEI namespace prefix used by GROBID
_TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}

# GROBID puts appendices under ``<back>`` instead of ``<body>``; the wrapper div
# carries one of these ``type`` values. Walking only ``<body>`` silently dropped
# whole appendix chapters (they never reached blocks / chunks).
_APPENDIX_DIV_TYPES = frozenset(
    {
        "annex",
        "appendix",
        "appendices",
        "appendixes",
        "supplement",
        "supplementary",
        "supplementary-material",
        "supplementary_material",
    }
)

# 走り込み見出し（PRL / PRD 系）: ``Introduction.—Detecting ...`` のように段落
# 先頭へ埋め込まれた見出し。GROBID は <head> を起こさないので、head が無い div の
# 段落に限りこの決定論規則で section を起こす。
# - 見出し候補はピリオドを含まない 60 字以内の短い語句（``FIG. 1`` 等を弾く）
# - 直後にダッシュ（``-`` / ``–`` / ``—``）と本文が続くこと
# - 本文の先頭が小文字でないこと（文中の偶然一致を避ける）
_RUN_IN_HEADING_RE = re.compile(r"^([A-Z][^.\n]{1,58}?)\.\s*[-–—]{1,2}\s*(?=[^\sa-z])")
_RUN_IN_HEADING_MAX_WORDS = 8

# 見出し番号: ``3`` / ``3.1`` / ``A.2``（付録）。@n が無い TEI では head 本文の
# 先頭からも読む。単独大文字は ``A.`` のように明示のピリオドがある場合だけ採る
# （``A new formalism ...`` のような普通の見出しを付録 A と誤認しないため）。
_SECTION_NUMBER_FULL_RE = re.compile(r"(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)")
_HEAD_TEXT_NUMBER_RE = re.compile(
    r"^(?:(\d+(?:\.\d+)*)\.?|([A-Z](?:\.\d+)+)\.?|([A-Z])\.)\s+(?=\S)"
)

# GROBID が表の数値を ``<abstract>`` の末尾へ連結して出すことがある（REVTeX の
# 長大な表が要旨と同じ領域に置かれた PDF）。要旨と表を1ブロックに混ぜると要旨の
# 引用・埋め込みが表の数値ごと汚れるため、末尾の数値羅列だけを別ブロックへ切り出す。
# 情報は捨てず、切り出した側に provenance を付けて残す。
_NUMERIC_TOKEN_RE = re.compile(r"^[+\-±(<>=~]*\d[\d.,]*(?:[eE×xX^][+\-]?\d+)?[)%]*$")

# figDesc がラベルを持たず <head> 側に ``FIG. 1.`` だけが入る GROBID 出力を
# 繋ぎ直すための判定。
_FIGURE_LABEL_PREFIX_RE = re.compile(
    r"^(?i:figure|fig\.?|table|tab\.?|図|表)\s*\.?\s*[0-9IVXLC]"
)


@dataclass
class _ParseState:
    """TEI ウォーク中の可変状態（順序カウンタ・見出し索引・回収統計）。"""

    sections: list[Section] = field(default_factory=list)
    blocks: list[TypedBlock] = field(default_factory=list)
    section_order: int = 0
    block_order: int = 0
    # 正規化済み見出し番号 ("3.1") → section_id
    number_index: dict[str, str] = field(default_factory=dict)
    # (level, section_id) のスタック。番号から level を導いたとき親の解決に使う。
    level_stack: list[tuple[int, str]] = field(default_factory=list)
    used_section_ids: set[str] = field(default_factory=set)
    stats: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str, amount: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + amount


@dataclass
class GROBIDParseResult:
    """GROBIDTEIParser の解析結果。"""
    metadata: DocumentMetadata
    sections: list[Section]
    blocks: list[TypedBlock]


class GROBIDTEIParser:
    """GROBID が返す TEI XML を解析して DocumentStructure を構築する。

    Parameters
    ----------
    tei_xml:
        GROBID の processFulltextDocument が返す TEI XML 文字列。
    """

    def parse(self, tei_xml: str) -> GROBIDParseResult:
        """TEI XML を解析して GROBIDParseResult を返す。

        GROBID が利用不可だった場合など空文字列が渡された場合は
        空の結果を返す（呼び出し側で PyMuPDF にフォールバックする）。
        """
        if not _BS4_AVAILABLE:
            raise RuntimeError("beautifulsoup4 is not installed. Run: pip install beautifulsoup4")
        if not tei_xml or not tei_xml.strip():
            return GROBIDParseResult(
                metadata=DocumentMetadata(),
                sections=[],
                blocks=[],
            )

        try:
            soup = BeautifulSoup(tei_xml, "xml")
        except Exception:
            soup = BeautifulSoup(tei_xml, "lxml")

        metadata = self._parse_metadata(soup)
        sections, blocks = self._parse_body(soup, metadata)

        return GROBIDParseResult(metadata=metadata, sections=sections, blocks=blocks)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _parse_metadata(self, soup) -> DocumentMetadata:
        title = None
        title_tag = soup.find("titleStmt")
        if title_tag:
            t = title_tag.find("title", level="a")
            if t is None:
                t = title_tag.find("title")
            if t:
                title = t.get_text(strip=True) or None

        # Issue #372: extract authors ONLY from the front-matter <teiHeader>
        # (fileDesc/sourceDesc/titleStmt). GROBID records citation authors under
        # <text><back><listBibl>, so a document-wide soup.find_all("author")
        # would mix ~100 reference authors into the document author list.
        header = soup.find("teiHeader") or soup.find("fileDesc")
        authors: list[str] = []
        seen: set[str] = set()
        if header is not None:
            for author_tag in header.find_all("author"):
                # Defensive: never read authors that sit inside a bibliography
                # list even if it appears under the header.
                if author_tag.find_parent("listBibl") is not None:
                    continue
                persname = author_tag.find("persName")
                if not persname:
                    continue
                forename = persname.find("forename")
                surname = persname.find("surname")
                parts = []
                if forename:
                    parts.append(forename.get_text(strip=True))
                if surname:
                    parts.append(surname.get_text(strip=True))
                name = " ".join(p for p in parts if p).strip()
                if name and name not in seen:
                    seen.add(name)
                    authors.append(name)

        author_extraction = {
            "source": "grobid_tei" if authors else "none",
            "confidence": 0.95 if authors else 0.0,
            "needs_review": False,
            "review_reasons": [],
            "candidate_sources": {"grobid_tei": list(authors)} if authors else {},
        }
        return DocumentMetadata(
            title=title,
            authors=authors,
            pages=0,
            author_extraction=author_extraction,
        )

    # ------------------------------------------------------------------
    # Body parsing
    # ------------------------------------------------------------------

    def _parse_body(
        self, soup, metadata: DocumentMetadata
    ) -> tuple[list[Section], list[TypedBlock]]:
        state = _ParseState()

        # --- Abstract ---
        abstract_tag = soup.find("abstract")
        if abstract_tag:
            abstract_text = abstract_tag.get_text(separator=" ", strip=True)
            if abstract_text:
                sec_id = "sec_abstract"
                state.sections.append(Section(
                    section_id=sec_id,
                    title="Abstract",
                    level=1,
                    order=state.section_order,
                    page_start=1,
                ))
                state.used_section_ids.add(sec_id)
                state.section_order += 1
                prose, numeric_dump = self._split_trailing_numeric_dump(abstract_text)
                self._append_block(
                    state,
                    text=prose,
                    block_type="body_paragraph",
                    section_id=sec_id,
                    raw={"parser_source": "grobid_tei", "tei_section_id": "abstract"},
                )
                if numeric_dump:
                    self._append_block(
                        state,
                        text=numeric_dump,
                        block_type="body_paragraph",
                        section_id=sec_id,
                        raw={
                            "parser_source": "grobid_tei",
                            "tei_section_id": "abstract",
                            "numeric_table_like": True,
                            "detached_from_abstract": True,
                        },
                    )
                    state.bump("abstract_numeric_dumps")

        # --- Body ---
        body_tag = soup.find("body")
        if body_tag:
            self._process_container(
                container=body_tag,
                state=state,
                level=1,
                parent_section_id=None,
                is_appendix=False,
                container_name="body",
            )

        # --- Back matter: appendices (annex) only ---
        # GROBID keeps appendices in <back><div type="annex">…; references and
        # acknowledgments live in sibling divs and are filtered out both by the
        # accepted type list and by _EXCLUDED_HEADINGS.
        back_tag = soup.find("back")
        if back_tag:
            for child in back_tag.children:
                name = getattr(child, "name", None)
                if name == "div":
                    div_type = str(child.get("type") or "").strip().lower()
                    if div_type not in _APPENDIX_DIV_TYPES:
                        continue
                    before = len(state.sections)
                    self._process_appendix_container(child, state)
                    state.bump("appendix_sections", max(0, len(state.sections) - before))
                elif name == "figure":
                    self._process_figure(
                        fig_tag=child,
                        state=state,
                        section_id=None,
                        tei_section_id="back",
                        is_appendix=True,
                        container_name="back",
                    )

        metadata.structure_recovery = dict(state.stats)
        return state.sections, state.blocks

    def _process_appendix_container(self, div, state: _ParseState) -> None:
        """``<back><div type="annex">`` を歩く。

        wrapper div 自身が head を持つ場合は1つの付録セクションとして扱い、
        head を持たない（GROBID の既定）場合は透過コンテナとして子 div を
        レベル1の付録セクションにする。
        """
        head_tag = div.find("head", recursive=False)
        heading = head_tag.get_text(strip=True) if head_tag else ""
        if heading:
            self._process_div(
                div=div,
                state=state,
                level=1,
                parent_section_id=None,
                is_appendix=True,
            )
            return
        self._process_container(
            container=div,
            state=state,
            level=1,
            parent_section_id=None,
            is_appendix=True,
            container_name="annex",
        )

    def _process_container(
        self,
        container,
        state: _ParseState,
        *,
        level: int,
        parent_section_id: str | None,
        is_appendix: bool,
        container_name: str,
    ) -> None:
        """head を持たないコンテナ（``<body>`` / annex wrapper）の直下を歩く。

        ``<body>`` 直下には div の兄弟として ``<figure>`` / ``<figure type="table">``
        が置かれることがあり、div の子だけを見る従来実装ではまるごと落ちていた。
        """
        run_in_section_id: str | None = None
        for child in container.children:
            name = getattr(child, "name", None)
            if name is None:
                continue
            if name == "div":
                run_in_section_id = None
                self._process_div(
                    div=child,
                    state=state,
                    level=level,
                    parent_section_id=parent_section_id,
                    is_appendix=is_appendix,
                )
            elif name == "p":
                started = self._maybe_start_run_in_section(
                    text=child.get_text(separator=" ", strip=True),
                    state=state,
                    parent_section_id=parent_section_id,
                    is_appendix=is_appendix,
                )
                if started:
                    run_in_section_id = started
                self._process_paragraph(
                    p_tag=child,
                    state=state,
                    section_id=run_in_section_id or parent_section_id,
                    tei_section_id=container_name,
                    is_appendix=is_appendix,
                )
            elif name == "formula":
                self._process_formula(
                    formula_tag=child,
                    state=state,
                    section_id=run_in_section_id or parent_section_id,
                    tei_section_id=container_name,
                    is_appendix=is_appendix,
                )
            elif name == "figure":
                self._process_figure(
                    fig_tag=child,
                    state=state,
                    section_id=None,
                    tei_section_id=container_name,
                    is_appendix=is_appendix,
                    container_name=container_name,
                )

    def _process_div(
        self,
        div,
        state: _ParseState,
        *,
        level: int,
        parent_section_id: str | None,
        is_appendix: bool,
    ) -> None:
        head_tag = div.find("head", recursive=False)
        heading = head_tag.get_text(strip=True) if head_tag else ""

        # References / Acknowledgments は除外
        if heading and _EXCLUDED_HEADINGS.search(heading):
            return

        number = self._resolve_section_number(div, head_tag, heading)
        tei_section_id = self._tei_section_id(div, head_tag)
        sec_id = self._unique_section_id(f"sec_{tei_section_id}", state)

        if heading:
            resolved_level, resolved_parent = self._resolve_level_and_parent(
                number=number,
                state=state,
                nesting_level=level,
                nesting_parent=parent_section_id,
            )
            state.sections.append(Section(
                section_id=sec_id,
                title=heading,
                level=resolved_level,
                order=state.section_order,
                page_start=1,
                parent_section_id=resolved_parent,
                section_number=number,
                is_appendix=is_appendix,
            ))
            state.used_section_ids.add(sec_id)
            state.section_order += 1
            if number:
                state.number_index.setdefault(number, sec_id)
            while state.level_stack and state.level_stack[-1][0] >= resolved_level:
                state.level_stack.pop()
            state.level_stack.append((resolved_level, sec_id))
            child_level = resolved_level + 1
        else:
            child_level = level + 1

        current_section_id = sec_id if heading else parent_section_id
        run_in_section_id: str | None = None

        # 直下の p / formula / figure を処理
        for child in div.children:
            if not hasattr(child, "name") or child.name is None:
                continue

            if child.name == "p":
                if not heading:
                    started = self._maybe_start_run_in_section(
                        text=child.get_text(separator=" ", strip=True),
                        state=state,
                        parent_section_id=parent_section_id,
                        is_appendix=is_appendix,
                    )
                    if started:
                        run_in_section_id = started
                self._process_paragraph(
                    p_tag=child,
                    state=state,
                    section_id=run_in_section_id or current_section_id,
                    tei_section_id=tei_section_id,
                    is_appendix=is_appendix,
                )
            elif child.name == "formula":
                self._process_formula(
                    formula_tag=child,
                    state=state,
                    section_id=run_in_section_id or current_section_id,
                    tei_section_id=tei_section_id,
                    is_appendix=is_appendix,
                )
            elif child.name == "figure":
                self._process_figure(
                    fig_tag=child,
                    state=state,
                    section_id=run_in_section_id or current_section_id,
                    tei_section_id=tei_section_id,
                    is_appendix=is_appendix,
                    container_name="div",
                )
            elif child.name == "div":
                self._process_div(
                    div=child,
                    state=state,
                    level=child_level,
                    parent_section_id=run_in_section_id or current_section_id,
                    is_appendix=is_appendix,
                )

    # ------------------------------------------------------------------
    # Abstract hygiene（要旨末尾に連結された表の数値羅列の切り出し）
    # ------------------------------------------------------------------

    # 数値羅列と判定する窓の長さ・窓内比率・切り出す最小トークン数。
    _NUMERIC_DUMP_WINDOW = 40
    _NUMERIC_DUMP_WINDOW_RATIO = 0.7
    _NUMERIC_DUMP_TAIL_RATIO = 0.5
    _NUMERIC_DUMP_MIN_TOKENS = 40

    @classmethod
    def _split_trailing_numeric_dump(cls, text: str) -> tuple[str, str]:
        """``(要旨本文, 末尾の数値羅列)`` に分ける。該当が無ければ後者は空文字。

        判定は決定論的で、①長さ ``_NUMERIC_DUMP_WINDOW`` の窓が数値トークン
        ``_NUMERIC_DUMP_WINDOW_RATIO`` 以上 ②そこから末尾までも
        ``_NUMERIC_DUMP_TAIL_RATIO`` 以上 ③本文側が空にならない、を全て満たす
        最初の位置でだけ切る。テキストは捨てない（呼び出し側が別ブロックに残す）。
        """
        tokens = (text or "").split()
        n = len(tokens)
        if n < cls._NUMERIC_DUMP_MIN_TOKENS * 2:
            return text, ""

        flags = [1 if _NUMERIC_TOKEN_RE.match(tok) else 0 for tok in tokens]
        suffix = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            suffix[i] = suffix[i + 1] + flags[i]

        window = cls._NUMERIC_DUMP_WINDOW
        need_window = cls._NUMERIC_DUMP_WINDOW_RATIO * window
        for i in range(1, n - cls._NUMERIC_DUMP_MIN_TOKENS + 1):
            if i + window > n:
                break
            if suffix[i] - suffix[i + window] < need_window:
                continue
            tail_len = n - i
            if suffix[i] < cls._NUMERIC_DUMP_TAIL_RATIO * tail_len:
                continue
            # 窓の先頭は本文の最後の数語を含むことがあるので、最初の数値トークンまで
            # 切り口を前へ送る（本文側の文を数値羅列へ巻き込まない）。
            cut = i
            limit = min(n, i + window)
            while cut < limit and not flags[cut]:
                cut += 1
            if cut >= n:
                break
            prose = " ".join(tokens[:cut]).strip()
            dump = " ".join(tokens[cut:]).strip()
            if not prose or not dump:
                break
            return prose, dump
        return text, ""

    # ------------------------------------------------------------------
    # Section numbering / hierarchy
    # ------------------------------------------------------------------

    @staticmethod
    def _tei_section_id(div, head_tag) -> str:
        n_attr = str(div.get("n") or "").strip()
        if not n_attr and head_tag is not None:
            n_attr = str(head_tag.get("n") or "").strip()
        if n_attr:
            return f"div_{n_attr.rstrip('.')}"
        return f"div_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _unique_section_id(candidate: str, state: _ParseState) -> str:
        if candidate not in state.used_section_ids:
            return candidate
        return f"{candidate}_{uuid.uuid4().hex[:4]}"

    @classmethod
    def _resolve_section_number(cls, div, head_tag, heading: str) -> str | None:
        """見出し番号を ``@n``（div / head）→ head 本文の順に決定論的に読む。"""
        for raw_value in (div.get("n"), head_tag.get("n") if head_tag is not None else None):
            normalized = cls._normalize_section_number(raw_value)
            if normalized:
                return normalized
        return cls._section_number_from_head_text(heading)

    @staticmethod
    def _normalize_section_number(value) -> str | None:
        text = str(value or "").strip().rstrip(".").strip()
        if not text:
            return None
        if not _SECTION_NUMBER_FULL_RE.fullmatch(text):
            return None
        return text

    @staticmethod
    def _section_number_from_head_text(heading: str) -> str | None:
        match = _HEAD_TEXT_NUMBER_RE.match(heading or "")
        if not match:
            return None
        return next((g for g in match.groups() if g), None)

    @staticmethod
    def _number_depth(number: str) -> int:
        return number.count(".") + 1

    @classmethod
    def _resolve_level_and_parent(
        cls,
        *,
        number: str | None,
        state: _ParseState,
        nesting_level: int,
        nesting_parent: str | None,
    ) -> tuple[int, str | None]:
        """``3.1`` のような見出し番号から level / 親セクションを導出する。

        番号が無ければ従来どおり TEI のネスト構造をそのまま使う。
        """
        if not number:
            return nesting_level, nesting_parent

        level = cls._number_depth(number)
        if level <= 1:
            return 1, None

        parent_number = number.rsplit(".", 1)[0]
        parent_id = state.number_index.get(parent_number)
        if parent_id:
            return level, parent_id

        # 番号の親が見つからない（GROBID が親見出しを落とした等）場合は、
        # 直近の浅いセクションを親にする。推測で番号を作り直さない。
        for stacked_level, stacked_id in reversed(state.level_stack):
            if stacked_level < level:
                return level, stacked_id
        return level, nesting_parent

    # ------------------------------------------------------------------
    # Run-in headings（走り込み見出し）
    # ------------------------------------------------------------------

    @classmethod
    def _detect_run_in_heading(cls, text: str) -> str | None:
        match = _RUN_IN_HEADING_RE.match((text or "").strip())
        if not match:
            return None
        title = match.group(1).strip()
        if not title or len(title.split()) > _RUN_IN_HEADING_MAX_WORDS:
            return None
        return title

    def _maybe_start_run_in_section(
        self,
        *,
        text: str,
        state: _ParseState,
        parent_section_id: str | None,
        is_appendix: bool,
    ) -> str | None:
        """段落先頭の走り込み見出しから section を起こす（本文は削らない）。"""
        title = self._detect_run_in_heading(text)
        if not title:
            return None
        sec_id = self._unique_section_id(
            f"sec_runin_{uuid.uuid4().hex[:6]}", state
        )
        state.sections.append(Section(
            section_id=sec_id,
            title=title,
            level=1,
            order=state.section_order,
            page_start=1,
            parent_section_id=parent_section_id,
            is_appendix=is_appendix,
        ))
        state.used_section_ids.add(sec_id)
        state.section_order += 1
        while state.level_stack and state.level_stack[-1][0] >= 1:
            state.level_stack.pop()
        state.level_stack.append((1, sec_id))
        state.bump("run_in_headings")
        return sec_id

    # ------------------------------------------------------------------
    # Blocks
    # ------------------------------------------------------------------

    def _append_block(
        self,
        state: _ParseState,
        *,
        text: str,
        block_type: str,
        section_id: str | None,
        raw: dict,
        equation_label: str | None = None,
    ) -> None:
        state.blocks.append(TypedBlock(
            block_id=f"blk_{uuid.uuid4().hex[:8]}",
            page=1,
            order=state.block_order,
            text=text,
            block_type=block_type,
            section_id=section_id,
            equation_label=equation_label,
            raw=raw,
        ))
        state.block_order += 1

    def _process_paragraph(
        self,
        p_tag,
        state: _ParseState,
        *,
        section_id: str | None,
        tei_section_id: str,
        is_appendix: bool = False,
    ) -> None:
        text = p_tag.get_text(separator=" ", strip=True)
        if not text:
            return
        raw = {
            "parser_source": "grobid_tei",
            "tei_section_id": tei_section_id,
        }
        if is_appendix:
            raw["in_appendix"] = True
            state.bump("appendix_blocks")
        self._append_block(
            state,
            text=text,
            block_type="body_paragraph",
            section_id=section_id,
            raw=raw,
        )

    def _process_formula(
        self,
        formula_tag,
        state: _ParseState,
        *,
        section_id: str | None,
        tei_section_id: str,
        is_appendix: bool = False,
    ) -> None:
        text = formula_tag.get_text(separator=" ", strip=True)
        if not text:
            return

        label_tag = formula_tag.find("label")
        equation_label = label_tag.get_text(strip=True) if label_tag else None

        formula_xml_id = formula_tag.get("xml:id") or formula_tag.get("n") or ""
        raw = {
            "parser_source": "grobid_tei",
            "tei_section_id": tei_section_id,
            "tei_formula_id": formula_xml_id,
        }
        if is_appendix:
            raw["in_appendix"] = True
            state.bump("appendix_blocks")
        # Issue #368: preserve table / container provenance so the equation
        # semantics acceptance gate does not auto-confirm a table-cell formula
        # as a standalone independent equation.
        table_provenance = self._table_container_provenance(formula_tag)
        if table_provenance:
            raw.update(table_provenance)
        self._append_block(
            state,
            text=text,
            block_type="equation_block",
            section_id=section_id,
            raw=raw,
            equation_label=equation_label,
        )

    @staticmethod
    def _table_container_provenance(formula_tag) -> dict | None:
        """Return table provenance if the formula is inside a <figure type=table>.

        Walks the TEI ancestors of the formula; a ``<figure type="table">``
        container means the formula is a table cell, not a standalone numbered
        equation (issue #368).
        """
        parent = getattr(formula_tag, "parent", None)
        while parent is not None and getattr(parent, "name", None) is not None:
            if parent.name == "figure" and (parent.get("type") or "").lower() == "table":
                table_id = parent.get("xml:id") or parent.get("n") or ""
                provenance: dict = {"container_type": "table", "in_table": True}
                if table_id:
                    provenance["table_id"] = str(table_id)
                return provenance
            parent = getattr(parent, "parent", None)
        return None

    # ------------------------------------------------------------------
    # Figures / tables
    # ------------------------------------------------------------------

    def _process_figure(
        self,
        fig_tag,
        state: _ParseState,
        *,
        section_id: str | None,
        tei_section_id: str,
        is_appendix: bool = False,
        container_name: str = "div",
    ) -> None:
        fig_type = str(fig_tag.get("type") or "figure").strip().lower()
        is_table = fig_type == "table"
        fig_xml_id = str(fig_tag.get("xml:id") or fig_tag.get("n") or "")

        caption = self._figure_caption_text(fig_tag)
        if caption:
            block_type = "table_caption" if is_table else "figure_caption"
            raw = {
                "parser_source": "grobid_tei",
                "tei_section_id": tei_section_id,
                "tei_fig_type": fig_type,
                "tei_container": container_name,
            }
            if fig_xml_id:
                raw["tei_fig_id"] = fig_xml_id
            if is_appendix:
                raw["in_appendix"] = True
            self._append_block(
                state,
                text=caption,
                block_type=block_type,
                section_id=section_id,
                raw=raw,
            )
            state.bump("table_captions" if is_table else "figure_captions")
            if container_name != "div":
                state.bump("container_level_figures")

        if not is_table:
            return

        # 表本体は落とさずテキストとして残す（P4）。equation_block ではなく
        # 表由来と分かる provenance 付きの本文ブロックにする。
        table_text, row_count = self._table_body_text(fig_tag)
        if table_text:
            raw = {
                "parser_source": "grobid_tei",
                "tei_section_id": tei_section_id,
                "tei_fig_type": fig_type,
                "tei_container": container_name,
                "container_type": "table",
                "in_table": True,
                "table_rows": row_count,
            }
            if fig_xml_id:
                raw["table_id"] = fig_xml_id
                raw["tei_fig_id"] = fig_xml_id
            if is_appendix:
                raw["in_appendix"] = True
            self._append_block(
                state,
                text=table_text,
                block_type="body_paragraph",
                section_id=section_id,
                raw=raw,
            )
            state.bump("table_bodies")

        # Issue #368: formulas nested inside a <figure type="table"> are table
        # cells, not standalone numbered equations. Emit them so they are not
        # lost, but with table provenance (attached by _process_formula) so the
        # equation semantics gate keeps them out of the auto-confirmed set.
        for formula_tag in fig_tag.find_all("formula"):
            self._process_formula(
                formula_tag=formula_tag,
                state=state,
                section_id=section_id,
                tei_section_id=tei_section_id,
                is_appendix=is_appendix,
            )

    @staticmethod
    def _figure_caption_text(fig_tag) -> str:
        """caption 本文を返す（``<figDesc>`` 優先・無ければ ``<head>``）。

        GROBID は ``FIG. 1.`` を ``<head>``、説明文を ``<figDesc>`` に分けることが
        あり、figDesc だけを取ると図番号が落ちて ``figure_key`` を導出できない。
        figDesc がラベルで始まらず head がラベルで始まる場合だけ連結する。
        """
        desc_tag = fig_tag.find("figDesc")
        desc_text = desc_tag.get_text(separator=" ", strip=True) if desc_tag else ""
        head_tag = fig_tag.find("head", recursive=False)
        head_text = head_tag.get_text(separator=" ", strip=True) if head_tag else ""

        if desc_text and head_text:
            if not _FIGURE_LABEL_PREFIX_RE.match(desc_text) and _FIGURE_LABEL_PREFIX_RE.match(head_text):
                return f"{head_text} {desc_text}".strip()
        return desc_text or head_text

    @staticmethod
    def _table_body_text(fig_tag) -> tuple[str, int]:
        """``<table>`` の行をテキスト化して返す（行 = 改行 / セル = " | "）。"""
        table_tag = fig_tag.find("table")
        if table_tag is None:
            return "", 0
        lines: list[str] = []
        for row in table_tag.find_all("row"):
            cells = [
                cell.get_text(separator=" ", strip=True)
                for cell in row.find_all("cell")
            ]
            line = " | ".join(c for c in cells if c)
            if line:
                lines.append(line)
        if not lines:
            fallback = table_tag.get_text(separator=" ", strip=True)
            return (fallback, 0) if fallback else ("", 0)
        return "\n".join(lines), len(lines)
