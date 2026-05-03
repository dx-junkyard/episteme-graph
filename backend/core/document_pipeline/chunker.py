"""Section-aware source chunker (issue #226).

DocumentStructureAgent の出力 `DocumentStructureResult` を入力に取り、section /
block 境界を尊重した source chunk を作る。固定長中心の旧 `chunk_pdf_pages` を
置き換える。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# DocumentStructureAgent 由来の dataclass はランタイム必須ではないため、
# 型注釈は文字列で参照する。テストでは monkeypatch で代替可能。

# 1 chunk あたりの最大文字数。embedding API のトークン制約と RAG 表示の見やすさ
# を踏まえた経験値。section が長い場合は block 単位で分割する。
DEFAULT_MAX_CHARS = 1800
# section/block 単位の chunk 結合下限。これを下回る短い chunk は隣の chunk に
# マージする（あまりに細切れになると検索ヒットの精度が下がるため）。
MIN_CHARS = 200


@dataclass
class SourceChunk:
    chunk_index: int
    text: str
    section_id: str | None
    block_ids: list[str] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "text": self.text,
            "section_id": self.section_id,
            "block_ids": list(self.block_ids),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "metadata": dict(self.metadata),
        }


def build_source_chunks(
    structure,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[SourceChunk]:
    """`DocumentStructureResult` から source chunk を生成する。

    Strategy:
        1. blocks を section_id でグループ化。section 順は section.order、
           無セクションの block は document 末尾扱い。
        2. block を順に積み、累計 max_chars を超えたら chunk を確定する。
        3. 1 つの block 単独で max_chars を超える場合は文字数で機械分割するが、
           block_ids にはその block のみを記録する。
        4. 末尾の極小 chunk は前の chunk へ吸収する。
    """
    blocks = list(structure.blocks)
    sections_by_id = {s.section_id: s for s in structure.sections}
    section_order = {
        s.section_id: (s.order, s.page_start) for s in structure.sections
    }

    # section_id -> [TypedBlock] （section 内では block.order 昇順）
    grouped: dict[str | None, list] = {}
    for b in blocks:
        key = b.section_id
        grouped.setdefault(key, []).append(b)
    for key in grouped:
        grouped[key].sort(key=lambda b: (b.page, b.order))

    # section_id 順を確定。None セクションは末尾に。
    section_keys: list[str | None] = []
    for sid in sorted(
        (k for k in grouped if k is not None),
        key=lambda k: section_order.get(k, (10**9, 10**9)),
    ):
        section_keys.append(sid)
    if None in grouped:
        section_keys.append(None)

    chunks: list[SourceChunk] = []
    chunk_index = 0
    for sid in section_keys:
        for chunk in _chunk_section_blocks(
            section_id=sid,
            section_obj=sections_by_id.get(sid) if sid else None,
            blocks=grouped[sid],
            max_chars=max_chars,
            start_index=chunk_index,
        ):
            chunks.append(chunk)
            chunk_index += 1

    chunks = _absorb_short_tail_chunks(chunks)
    # 安定した chunk_index を再採番する（吸収後）
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


def _chunk_section_blocks(
    section_id: str | None,
    section_obj,
    blocks: list,
    max_chars: int,
    start_index: int,
) -> Iterable[SourceChunk]:
    section_title = getattr(section_obj, "title", None) if section_obj else None

    pending_text_parts: list[str] = []
    pending_block_ids: list[str] = []
    pending_pages: list[int] = []
    pending_chars = 0

    def emit() -> SourceChunk | None:
        if not pending_text_parts:
            return None
        text = "\n\n".join(t for t in pending_text_parts if t).strip()
        if not text:
            return None
        return SourceChunk(
            chunk_index=0,  # 後で全体採番
            text=text,
            section_id=section_id,
            block_ids=list(pending_block_ids),
            page_start=min(pending_pages) if pending_pages else None,
            page_end=max(pending_pages) if pending_pages else None,
            metadata={
                "section_title": section_title,
            },
        )

    for block in blocks:
        text = (block.text or "").strip()
        if not text:
            continue
        block_chars = len(text)

        # 単一 block で超過 → 文字数機械分割
        if block_chars > max_chars:
            if pending_text_parts:
                yield_chunk = emit()
                if yield_chunk:
                    yield yield_chunk
                pending_text_parts.clear()
                pending_block_ids.clear()
                pending_pages.clear()
                pending_chars = 0
            for sub in _split_long_text(text, max_chars):
                yield SourceChunk(
                    chunk_index=0,
                    text=sub,
                    section_id=section_id,
                    block_ids=[block.block_id],
                    page_start=block.page,
                    page_end=block.page,
                    metadata={
                        "section_title": section_title,
                        "block_type": block.block_type,
                        "split_long_block": True,
                    },
                )
            continue

        # 通常: pending に追加。超過したら前を確定してから追加。
        if pending_chars + block_chars + 2 > max_chars and pending_text_parts:
            yield_chunk = emit()
            if yield_chunk:
                yield yield_chunk
            pending_text_parts.clear()
            pending_block_ids.clear()
            pending_pages.clear()
            pending_chars = 0

        pending_text_parts.append(text)
        pending_block_ids.append(block.block_id)
        pending_pages.append(block.page)
        pending_chars += block_chars + 2

    last = emit()
    if last:
        yield last


def _split_long_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        return [text]
    parts: list[str] = []
    i = 0
    while i < len(text):
        parts.append(text[i : i + max_chars])
        i += max_chars
    return parts


def _absorb_short_tail_chunks(chunks: list[SourceChunk]) -> list[SourceChunk]:
    """末尾が極端に短い chunk を直前の chunk に吸収する。"""
    if not chunks:
        return chunks
    result: list[SourceChunk] = []
    for c in chunks:
        if (
            result
            and len(c.text) < MIN_CHARS
            and result[-1].section_id == c.section_id
        ):
            prev = result[-1]
            prev.text = prev.text + "\n\n" + c.text
            prev.block_ids.extend(c.block_ids)
            if c.page_end is not None:
                prev.page_end = max(prev.page_end or c.page_end, c.page_end)
            continue
        result.append(c)
    return result
