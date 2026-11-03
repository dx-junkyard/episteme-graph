"""agent 側の参照 ID の表記ゆれを吸収する純関数（読み時の正規化だけを担う）。

A層の各 agent は同じ claim を別の綴りで指す。``ThesisReconstructionAgent`` /
``DSLLinkingAgent`` は ``"claim:{block_id}:{span_id}"`` と接頭辞付きで書き、
``ClaimQualificationAgent`` / ``ClaimObjectBuilder`` と ``theory_claims.source_scope
.legacy_ids`` は接頭辞の無い ``"{block_id}:{span_id}"`` で持つ。読み手が素の文字列で
突合すると、同じ claim を指しているのに解決できず、

- 開幕画面の「この論文の主張」に ``claim:blk_9fd917ff:span_001`` という内部 ID が
  そのまま出る（``core/discuss/opening.py::_claim_ref_item`` は解決できない id を
  label にする）、
- グラフ対話レビューの根拠 claim が「未解決」になる、
- ``export_validation_gate`` が DSL edge の evidence を宙吊り（dangling）と報告する、

という3つの症状になっていた。**正規化はここ1箇所**に置き、3経路がこれを使う。

ID を書き換える層ではない（DB にも artifact にも書き戻さない）。**別名を増やす方向**の
関数だけを持ち、情報は落とさない — :func:`claim_ref_variants` は元の綴りを必ず先頭に残す。

DB・FastAPI・LLM を import しない。
"""

from __future__ import annotations

from collections.abc import Iterable

#: ``ThesisReconstructionAgent`` / ``DSLLinkingAgent`` が claim 参照に付ける接頭辞。
#: 本文側（``claim_object_builder`` の ``claim_span_001_3_sub02`` 等）は接頭辞を
#: 持たないので、``claim_`` で始まる ID を巻き込まないよう ``:`` 付きで判定する。
CLAIM_REF_PREFIX = "claim:"


def normalize_claim_ref(value: object) -> str:
    """claim 参照を接頭辞なしの綴りへ揃える。

    ``"claim:blk_x:span_001"`` → ``"blk_x:span_001"``。接頭辞が無い ID・空文字・
    接頭辞だけの文字列はそのまま（前後の空白だけ落とす）返す。
    """
    text = str(value or "").strip()
    if not text.startswith(CLAIM_REF_PREFIX):
        return text
    stripped = text[len(CLAIM_REF_PREFIX):].strip()
    return stripped or text


def claim_ref_variants(value: object) -> list[str]:
    """1つの claim 参照が名乗り得る綴りを、**元の綴りを先頭に**並べて返す。

    索引を作る側（``id -> 本文``）と引く側の両方で使う。元の綴りを落とさないので、
    接頭辞付きのキーで引く既存の読み手も壊れない。
    """
    text = str(value or "").strip()
    if not text:
        return []
    normalized = normalize_claim_ref(text)
    if normalized == text:
        return [text]
    return [text, normalized]


def expand_claim_refs(values: Iterable[object]) -> list[str]:
    """参照の並びを綴りの異形ごと展開する（順序保持・重複除去）。"""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        for variant in claim_ref_variants(value):
            if variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out
