"""分野マップのベクトル係留層 — 語彙とプロトタイプ合成テキストの正本。

正本: ``docs/features/atlas_vector_anchoring_design.md`` §3 / §4。

本モジュールは**純粋**（DB / LLM / FastAPI に触れない）。骨格ノードから
「何を埋め込むか」を決める合成規則（:func:`build_anchor_source_text`）と、
migration 074 の CHECK 制約と一致する語彙定数だけを持つ。

ラベル正規化は :func:`core.atlas_gaps.schema.normalize_label` を**再輸出**する
（別実装を作らない — 別名の一意キー ``normalized_alias`` と、カテゴリギャップの
``cluster_key`` は同じ規則で正規化されなければ「既存概念の言い換え」の突合が
ずれるため）。
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Sequence

# 別名の正規化は既存の正本を流用する（設計書 §3 — 再実装しない）。
from core.atlas_gaps.schema import normalize_label

#: 別名行の status 語彙（migration 074 の CHECK と一致させる）。
#: v1 に candidate 状態は無い — 登録操作そのものが教員の確定だから（VA1）。
ALIAS_STATUSES = ("confirmed", "dismissed")

#: 別名の出所語彙（``gap_signal`` = ギャップ候補の近傍注記からの登録）。
ALIAS_SOURCES = ("gap_signal", "manual")

#: 監査 ``metadata.action`` の語彙（entity_type は ``AUDIT_ENTITY_ATLAS_VECTOR``）。
AUDIT_ACTIONS = ("vectors_refresh", "alias_register", "alias_dismiss")

#: アンカーの種別（骨格の region / concept。migration 074 の CHECK と一致）。
NODE_KINDS = ("region", "concept")

#: 根拠として合成テキストに載せる evidence 引用の上限（設計書 §4）。
MAX_EVIDENCE_QUOTES = 5

#: evidence 引用1件あたりの最大文字数（設計書 §4）。
MAX_EVIDENCE_QUOTE_CHARS = 200

# 合成テキストの行頭ラベル（プロトタイプの構造を決める唯一の場所）。
_LINE_PREFIX_ALIASES = "別名: "
_LINE_PREFIX_REGION = "領域: "
_LINE_PREFIX_EVIDENCE = "根拠: "

#: 各行内の列挙の区切り。
_ITEM_SEPARATOR = " / "


def _clean(value: object) -> str:
    """前後の空白を落とし、改行・連続空白を1個の半角スペースへ畳む。

    合成テキストは行構造そのものが意味（``別名:`` / ``領域:`` / ``根拠:``）なので、
    素材側の改行がそのまま入ると構造が壊れる。ここで必ず1行へ潰す。
    """
    return " ".join(str(value or "").split())


def build_anchor_source_text(
    label: str,
    *,
    aliases: Optional[Iterable[str]] = None,
    region_label: str = "",
    child_labels: Optional[Sequence[str]] = None,
    evidence_quotes: Optional[Sequence[str]] = None,
) -> str:
    """骨格ノードのプロトタイプ合成テキスト（決定論・純関数）。

    設計書 §4 の形::

        {label}
        別名: {confirmed aliases を normalized 昇順}      ← あれば
        領域: {親 region label}（concept の場合）          ← あれば
        根拠: {confirmed placements の evidence quote}     ← あれば

    ``label`` 以外は全て任意で、**確定情報が増えるほどプロトタイプが精密になる**
    （教員の裁定が座標系を育てる、の実体）。region のプロトタイプは
    ``child_labels`` に配下 concept の label を渡して label + 概念列挙にする。

    決定論の担保:

    - 別名は :func:`normalize_label` の結果で昇順に整列し、重複を落とす
      （登録順・大小文字の揺れで ``source_hash`` が変わらないようにする）。
    - evidence 引用は**呼び出し側が決めた順序**（placement id 昇順）を保ち、
      各 :data:`MAX_EVIDENCE_QUOTE_CHARS` 字で切って先頭
      :data:`MAX_EVIDENCE_QUOTES` 件だけ載せる。
    - 空の素材は行ごと出さない（空行・空ラベルを残さない）。

    Args:
        label: ノードの表示ラベル（必須。空なら空文字を返す）。
        aliases: 確定済み別名。
        region_label: concept の親 region label（region 自身には渡さない）。
        child_labels: region の配下 concept label 列（concept には渡さない）。
        evidence_quotes: 確定配置の evidence 引用（呼び出し側で順序確定済み）。

    Returns:
        改行区切りの合成テキスト。
    """
    head = _clean(label)
    if not head:
        return ""

    lines = [head]

    # region のプロトタイプ = label + 配下 concept label 列挙（設計書 §4）。
    children = [_clean(c) for c in (child_labels or ())]
    children = [c for c in children if c and c != head]
    if children:
        seen: set[str] = set()
        unique_children: list[str] = []
        for child in children:
            if child in seen:
                continue
            seen.add(child)
            unique_children.append(child)
        lines.append(_ITEM_SEPARATOR.join(unique_children))

    # 別名は normalized 昇順（決定論。表示は元表記のうち最初に現れたもの）。
    if aliases:
        by_norm: dict[str, str] = {}
        for alias in aliases:
            text = _clean(alias)
            if not text:
                continue
            key = normalize_label(text)
            if not key or key in by_norm:
                continue
            by_norm[key] = text
        ordered = [by_norm[key] for key in sorted(by_norm)]
        if ordered:
            lines.append(_LINE_PREFIX_ALIASES + _ITEM_SEPARATOR.join(ordered))

    region = _clean(region_label)
    if region:
        lines.append(_LINE_PREFIX_REGION + region)

    if evidence_quotes:
        quotes: list[str] = []
        for quote in evidence_quotes:
            text = _clean(quote)[:MAX_EVIDENCE_QUOTE_CHARS]
            if not text:
                continue
            quotes.append(text)
            if len(quotes) >= MAX_EVIDENCE_QUOTES:
                break
        if quotes:
            lines.append(_LINE_PREFIX_EVIDENCE + _ITEM_SEPARATOR.join(quotes))

    return "\n".join(lines)


def source_hash(text: str) -> str:
    """合成テキストの sha256（hex）。

    ``atlas_anchor_embeddings.source_hash`` に格納し、refresh 時に不変なら
    再埋め込みをスキップする（設計書 §3 要点3 — コスト節約・冪等）。
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def is_valid_alias_status(status: object) -> bool:
    return str(status or "") in ALIAS_STATUSES


def is_valid_alias_source(source: object) -> bool:
    return str(source or "") in ALIAS_SOURCES


def is_valid_node_kind(kind: object) -> bool:
    return str(kind or "") in NODE_KINDS


__all__ = [
    "ALIAS_SOURCES",
    "ALIAS_STATUSES",
    "AUDIT_ACTIONS",
    "MAX_EVIDENCE_QUOTES",
    "MAX_EVIDENCE_QUOTE_CHARS",
    "NODE_KINDS",
    "build_anchor_source_text",
    "is_valid_alias_source",
    "is_valid_alias_status",
    "is_valid_node_kind",
    "normalize_label",
    "source_hash",
]
