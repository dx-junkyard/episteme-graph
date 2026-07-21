"""要素中心コンテキストビュー（Element-Centered Context Lens, Issue #498）の投影。

設計書 `docs/features/element_context_lens_design.md`。W層の既存2面
（decomposition.py=内訳・positioning.py=文脈的位置づけ）に続く3つ目の読み取り専用投影で、
選択要素を中心に「上位構造（Why）」「選択要素自体（What）」「下位構造（How）」を
1階層ずつ合成して返す。A層（`src/episteme_graph/agents/`）は読むだけで書き換えない
（W1 継承）。書き込みは一切行わない。

不変条項（設計書 §2 を継承）:
  - 固有情報（要素本体の定義・意味）と文脈依存情報（この論文・この図での役割）を
    区別し、文脈依存情報を共通部品や要素本体へ複製しない。
  - PDF 原文・既存構造から決定論的に辿れる関係（source_backed）と AI が提案する
    関係（candidate）を明確に区別する。人間が確定した関係のみ confirmed。
  - 上位構造が一件も無い場合は「上位構造との関係は未同定」（unidentified）とし、
    推測で穴埋めしない。
  - artifact 欠損・旧 run・関係なしは空のレーンまたは事実文（notes）へ縮退する
    （fail-soft。1要素型の例外が他要素型・呼び出し元全体を壊さない）。

契約（設計書 §5、呼び出し元は本モジュールの `build()` の戻り値をそのまま UI へ渡す
想定なのでキー名は変更しない）::

    {
      "focus": {
        "element_type": str, "element_id": str, "document_id": str | None,
        "label": str, "intrinsic_summary": str, "contextual_role": str | None,
        "contextual_role_status": "source_backed"|"confirmed"|"candidate"|"unidentified",
        "provenance": [str, ...],
        "generic": GENERIC | None,  # 設計書 §6 Phase 3。汎用×固有の結線
      },
      "upper": [ITEM, ...],
      "lower": [ITEM, ...],
      "notes": [str, ...],
    }

    GENERIC = {
      "entry_id": str, "name": str, "summary": str,
      "standardization_status": "standard"|"field_standard"|"emerging_common"|"novel"|"unknown",
    }
    # GENERIC は confirmed な同一性リンク先の L層エントリ（active のみ）。リンク無し・
    # エントリが active でない・読み取り失敗は None（fail-soft。文脈依存情報
    # （contextual_role 等）とは別欄で持ち、「一般に何か」と「この論文の役割」を
    # 混ぜない — 設計書 §2.1）。

    ITEM = {
      "element_type": "theory_claim"|"theory_component"|"equation"|"figure"|
                       "section"|"thesis"|"derivation"|"symbol"|"evidence"|"part"|"stage",
      "element_id": str | None,       # None = 表示のみ（非ナビゲーション）
      "document_id": str | None,
      "label": str,
      "relation": str,                # RELATION_LABELS のキーである保証あり
      "relation_label": str,
      "relation_status": "source_backed"|"candidate"|"confirmed",
      "evidence_refs": [str, ...],
      "navigable": bool,
    }

`build(ref)` は `shared_part`（scope='domain'）のときのみ ``None`` を返す（設計書の
対象は document-scoped 4要素型）。それ以外は例外を握って fail-soft の縮退結果を返し、
呼び出し元が 500 を気にしなくてよいようにする。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.document_pipeline.figure_images import normalize_figure_join_key
from core.figure_presentation import presentation_payload
from core.library import schema as library_schema
from core.library import store as library_store_mod
from core.deliberation import identity_links as identity_links_mod
from core.deliberation import refs as refs_mod
from core.deliberation import store as store_mod
from core.deliberation.schema import (
    ANNOTATION_KIND_INTERPRETATION,
    ANNOTATION_KIND_MEANING,
    ANNOTATION_STATUS_COMMITTED,
    CONTEXT_ROLE_STATUS_UNIDENTIFIED,
    CONTEXT_STATUS_CANDIDATE,
    CONTEXT_STATUS_CONFIRMED,
    CONTEXT_STATUS_SOURCE_BACKED,
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_SHARED_PART,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    IDENTITY_LINK_STATUS_CONFIRMED,
    SCOPE_DOCUMENT,
    ElementRef,
)

logger = logging.getLogger(__name__)

# 各レーン（上位/下位）の表示上限（設計書「Cap each lane at 20 items」）。超過分は
# 黙って切り捨てず notes に事実として記録する。
_CONTEXT_LANE_MAX = 20

# ITEM.navigable は element_type がこの4種のときだけ True になり得る（設計書 §5）。
_NAVIGABLE_ELEMENT_TYPES = (
    ELEMENT_FIGURE,
    ELEMENT_THEORY_COMPONENT,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_EQUATION,
)

# ── 関係語彙（内部語彙 → 読み手向け動詞句）。主語は常に focus（設計書 §3.2）。────────
# 「focus が ITEM に ~する」の形で読める動詞句にする。ITEM["relation"] は必ず
# このキーの一つでなければならない（テストで固定する）。RELATION_LABELS[relation] は
# ``.get`` ではなく通常の辞書アクセスで引く（context_lens.py 内で未登録の relation を
# 使うと即座に KeyError になる = 語彙の抜け漏れを検出できずに出荷することを防ぐ）。
RELATION_LABELS: dict[str, str] = {
    # 設計書に例示された初期語彙。
    "provides_evidence_for": "に証拠を与える",
    "quantifies": "を定量化する",
    "supports_thesis": "（中心命題）を支持する",
    "supports_component": "の根拠となる",
    "appears_in_section": "に掲載されている",
    "belongs_to_derivation": "の導出に属する",
    "member_of": "の構成要素である",
    "contains": "を構成要素として含む",
    "derives_from": "から導かれる",
    "leads_to": "の導出につながる",
    "uses_symbol": "の記号を用いる",
    "rests_on_evidence": "を根拠とする",
    "has_subclaim": "をサブ主張として持つ",
    "subclaim_of": "の下位主張である",
    "quantified_by": "によって定量化される",
    "evidenced_by_figure": "が図によって裏付けられる",
    "requires": "を前提とする",
    "related_component_candidate": "に関係する可能性がある",
    # 理論コンポーネントグラフ（TheoryOperationGraph）の走査に必要な追加語彙。
    "backed_by_claim": "を根拠として持つ",
    "uses_equation": "の数式を用いる",
    "relates_to_component": "に関連する",
    "used_by_component": "に利用される",
    # TheoryOperationGraph の main ステージノードとの claim 交差から導出する上位項目
    # （課題B）。A層の明示リンクではなく決定論的な集合演算の結果なので candidate。
    "participates_in_stage": "の理論段階に関与する",
}

# thesis_reconstruction artifact の support_structure セクション名 → 日本語ラベル
# （agent schema.py の SUPPORT_SECTIONS を日本語化。positioning.py の
# _SUPPORT_SECTION_LABELS と同内容だが、context_lens.py は positioning.py の私用
# ヘルパーに依存しないよう自前で持つ）。
_SUPPORT_SECTION_LABELS = {
    "direct_supports": "直接支持",
    "assumptions": "前提",
    "derivation_core": "導出の核",
    "correction_sources": "訂正の源",
    "uncertainty_sources": "不確実性の源",
    "diagnostic_consequences": "診断的帰結",
    "future_requirements": "将来要件",
}


# ---------------------------------------------------------------------------
# 状態マッピング（設計書の status mapping rule の正本・単一の testable helper）
# ---------------------------------------------------------------------------


def _status_for_link(source_kind: str) -> str:
    """関係の出所種別を根拠状態語彙へマップする（設計書の status mapping rule）。

    ``source_kind``:
      - ``"explicit"``  : A層 artifact の明示リンク（thesis の claim_ids/equation_ids/
        anchor_node_ids・is_thesis_anchor、derivation chain 所属、section 配置、
        graph の parent/member、theory_components.evidence_claims、symbol_registry、
        ClaimObjectRecord の equation_ids/figure_ids/subclaim_ids/parent_claim_id 等）
        → source_backed。
      - ``"inferred"``  : ``inferred_*`` フィールド・``linked_component_candidates``・
        apparatus/vision 由来・``suggested_mode`` 由来、または graph node/edge の
        ``source_backing_status`` が ``source_backed`` でない場合 → candidate。
      - ``"committed"`` : 人間確定（committed な element_annotations、confirmed な
        element_identity_links、``reviewed_analysis_profile`` 由来のパーツ、
        ``mode_review_status='reviewed'``）→ confirmed。

    未知の ``source_kind`` は安全側（source_backed を僭称しない）で candidate に倒す。
    """
    if source_kind == "committed":
        return CONTEXT_STATUS_CONFIRMED
    if source_kind == "explicit":
        return CONTEXT_STATUS_SOURCE_BACKED
    return CONTEXT_STATUS_CANDIDATE


def _link_kind_from_backing_status(source_backing_status: str | None) -> str:
    """TheoryOperationGraph の node/edge が持つ source_backing_status を
    ``_status_for_link`` の source_kind（explicit/inferred）へ変換する。"""
    return "explicit" if str(source_backing_status or "").strip() == "source_backed" else "inferred"


# ---------------------------------------------------------------------------
# 純粋ヘルパ（DB非依存・fake データで単体テスト可能）
# ---------------------------------------------------------------------------


def _list(value: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    items = value.get(key)
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def _section_label(section: dict[str, Any] | None) -> str:
    if not section:
        return ""
    return str(section.get("title") or "").strip()


def _sections_by_id(artifacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    structure = artifacts.get("document_structure")
    sections = structure.get("sections") if isinstance(structure, dict) else None
    if not isinstance(sections, list):
        return {}
    return {
        str(s["section_id"]): s for s in sections if isinstance(s, dict) and s.get("section_id")
    }


def _blocks_by_id(artifacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    structure = artifacts.get("document_structure")
    blocks = structure.get("blocks") if isinstance(structure, dict) else None
    if not isinstance(blocks, list):
        return {}
    return {
        str(b["block_id"]): b for b in blocks if isinstance(b, dict) and b.get("block_id")
    }


def _matching_figure_record(
    records: list[dict[str, Any]], figure: dict[str, Any]
) -> dict[str, Any] | None:
    """DB 図行に対応する figure/apparatus artifact record を返す。

    ``document_figures.id`` は UUID だが、caption-first の
    ``figure_table_semantics.figure_id`` は章番号付きラベルの句読点を保持した
    ``fig_6.8`` 形式（または caption block ID）である。一方 ``document_figures.figure_key``
    は非英数字をアンダースコアへ畳み込んだ ``fig_6_8`` 形式に正規化済みのため、
    素朴な文字列一致は章番号付きラベルで恒常的に失敗する。画像解析後の
    ``apparatus_semantics.figure_id`` は DB UUID である。したがって UUID の完全一致を
    まず試み、続く ``figure_key`` 系の比較は両辺を ``normalize_figure_join_key``
    （``figure_images.py`` の正本規則）で正規化してから行う。caption_block_id の
    対応キーはそのまま（非正規化）で最後に試す。

    空文字同士は一致とみなさない（正規化後に空文字列になった値同士も同様）。
    caption が無い残余 embedded image 同士を誤って接続するのを防ぐためである。
    """
    figure_id = str(figure.get("id") or "").strip()
    figure_key_norm = normalize_figure_join_key(figure.get("figure_key"))
    caption_block_id = str(figure.get("caption_block_id") or "").strip()

    for record in records:
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("figure_id") or "").strip()
        record_id_norm = normalize_figure_join_key(record_id)
        record_key_norm = normalize_figure_join_key(record.get("figure_key"))
        location = record.get("source_location")
        record_caption_block_id = str(
            location.get("caption_block_id") if isinstance(location, dict) else ""
        ).strip()

        if figure_id and record_id == figure_id:
            return record
        if figure_key_norm and (record_id_norm == figure_key_norm or record_key_norm == figure_key_norm):
            return record
        if caption_block_id and record_caption_block_id == caption_block_id:
            return record
    return None


def _component_source_scope_figure_keys(comp: dict[str, Any]) -> tuple[str, str]:
    """装置候補コンポーネントの source_scope から figure_id / figure_key を取り出す。"""
    scope = comp.get("source_scope") if isinstance(comp.get("source_scope"), dict) else {}
    return (
        str(scope.get("figure_id") or "").strip(),
        str(scope.get("figure_key") or "").strip(),
    )


def _has_figure_scope_key(comp: dict[str, Any]) -> bool:
    """当該コンポーネントの source_scope が figure_id/figure_key のいずれかを
    持つか（= F2 適用後に persist された図対応済み行か）を判定する。"""
    figure_id, figure_key = _component_source_scope_figure_keys(comp)
    return bool(figure_id or figure_key)


def _apparatus_component_matches_figure(comp: dict[str, Any], fig: dict[str, Any]) -> bool:
    """装置候補コンポーネントの source_scope が当該 fig（document_figures 行）に
    対応するか判定する。

    ``figure_id``（document_figures.id の DB UUID）または ``figure_key`` の一致で
    判定する。``_matching_figure_record`` と同様、空文字同士の一致は対応とみなさない
    （図キーを持たない legacy 行を誤って全図に紐づけないため）。
    """
    comp_figure_id, comp_figure_key = _component_source_scope_figure_keys(comp)
    if not comp_figure_id and not comp_figure_key:
        return False
    fig_id = str(fig.get("id") or "").strip()
    fig_key = str(fig.get("figure_key") or "").strip()
    if comp_figure_id and fig_id and comp_figure_id == fig_id:
        return True
    if comp_figure_key and fig_key and comp_figure_key == fig_key:
        return True
    return False


def _item(
    element_type: str,
    element_id: str | None,
    document_id: str | None,
    label: str,
    relation: str,
    relation_status: str,
    *,
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    """ITEM を1件組み立てる（upper/lower 共通。設計書 §5 の外形）。

    ``RELATION_LABELS[relation]`` は通常の辞書アクセスで引く。関係の入力ミス・
    語彙追加漏れは KeyError として即座に検出される（silent に relation_label 空欄の
    まま出荷しない）。
    """
    navigable = bool(element_id) and element_type in _NAVIGABLE_ELEMENT_TYPES
    return {
        "element_type": element_type,
        "element_id": element_id,
        "document_id": document_id,
        "label": str(label or ""),
        "relation": relation,
        "relation_label": RELATION_LABELS[relation],
        "relation_status": relation_status,
        "evidence_refs": [str(e) for e in (evidence_refs or []) if str(e or "").strip()],
        "navigable": navigable,
    }


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (item["element_type"], item["element_id"], item["relation"], item["label"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _cap_lane(items: list[dict[str, Any]], notes: list[str], lane_label: str) -> list[dict[str, Any]]:
    if len(items) > _CONTEXT_LANE_MAX:
        omitted = len(items) - _CONTEXT_LANE_MAX
        notes.append(f"{lane_label}に他 {omitted} 件あるが表示上限のため省略しました")
        return items[:_CONTEXT_LANE_MAX]
    return items


def _committed_contextual_role(annotations: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """committed な meaning/interpretation 注釈から文脈上の役割を拾う（人間確定が最優先）。"""
    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        if ann.get("status") != ANNOTATION_STATUS_COMMITTED:
            continue
        if ann.get("kind") not in (ANNOTATION_KIND_MEANING, ANNOTATION_KIND_INTERPRETATION):
            continue
        body = ann.get("body") if isinstance(ann.get("body"), dict) else {}
        text = str(body.get("text") or "").strip()
        if text:
            return text, _status_for_link("committed")
    return None, None


def _derive_contextual_role(
    upper_items: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    *,
    fallback_role: str | None = None,
    fallback_status: str | None = None,
) -> tuple[str | None, str]:
    """focus.contextual_role / contextual_role_status を導出する（設計書 §2.2/§3.1）。

    優先順位: 1) 人間が確定した注釈 2) 要素が自己記述する役割文（``fallback_role``。
    コンポーネントの ``thesis_context.role_in_thesis`` のような決定論的に導出済みの
    役割説明）3) 最初の上位構造項目から機械的に組み立てた事実文 4) 上位構造が一件も無い
    場合は None + unidentified（推測で穴埋めしない）。

    ``fallback_role`` は本来「この文脈での役割」そのものなので、機械的に組み立てる
    上位項目ベースの事実文より優先する（ただし人間確定注釈には劣後させる）。
    """
    committed_text, committed_status = _committed_contextual_role(annotations)
    if committed_text:
        return committed_text, committed_status
    fallback_text = str(fallback_role or "").strip()
    if fallback_text:
        return fallback_text, (fallback_status or CONTEXT_STATUS_SOURCE_BACKED)
    if not upper_items:
        return None, CONTEXT_ROLE_STATUS_UNIDENTIFIED
    top = upper_items[0]
    sentence = f"{top['label']}{top['relation_label']}".strip()
    return (sentence or None), top["relation_status"]


def _claim_id_lookup_from_rows(rows: list[tuple[str, dict[str, Any]]]) -> dict[str, str]:
    """theory_claims の (id, source_scope) 行列から「artifact 由来の claim id → DB
    UUID」の索引を組み立てる（純粋関数。DB接続は呼び出し側の責務）。

    ``source_scope.legacy_ids`` は persistence.py の ``_claim_legacy_keys`` が
    span_id と ``claim_{safe_span_id}``（= ClaimObjectBuilder が採番する
    ClaimObjectRecord.claim_id と同じ書式）の両方を積んでいるため、この索引だけで
    ClaimObjectBuilder / EquationSemantics / DerivationChain / ThesisReconstruction /
    ComponentAssembly / ComponentGraph が使う claim id 表記（agent 側 id）と
    theory_claims.id（DB UUID）を双方向的に解決できる。DB UUID 自身も
    恒等写像として含めるため、既に remap 済みの id（例:
    theory_components.evidence_claims）を渡しても素通りする。
    """
    lookup: dict[str, str] = {}
    for claim_db_id, source_scope in rows:
        db_id = str(claim_db_id)
        lookup[db_id] = db_id
        scope = source_scope if isinstance(source_scope, dict) else {}
        for legacy_id in scope.get("legacy_ids") or []:
            key = str(legacy_id or "").strip()
            if key:
                lookup[key] = db_id
        span_id = scope.get("span_id")
        if span_id:
            lookup.setdefault(str(span_id), db_id)
    return lookup


def _component_id_lookup_from_rows(rows: list[tuple[str, dict[str, Any]]]) -> dict[str, str]:
    """theory_components の (id, source_scope) 行列から「agent 側 component_id →
    DB UUID」の索引を組み立てる（純粋関数）。persistence.py の persist_components が
    ``source_scope.legacy_ids = [agent_component_id]`` を保存する規約に基づく。
    """
    lookup: dict[str, str] = {}
    for comp_db_id, source_scope in rows:
        db_id = str(comp_db_id)
        lookup[db_id] = db_id
        scope = source_scope if isinstance(source_scope, dict) else {}
        for legacy_id in scope.get("legacy_ids") or []:
            key = str(legacy_id or "").strip()
            if key:
                lookup[key] = db_id
    return lookup


def _equation_by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(r.get("equation_id")): r
        for r in records
        if isinstance(r, dict) and r.get("equation_id")
    }


def _equation_label(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    src = record.get("source_extraction") if isinstance(record.get("source_extraction"), dict) else {}
    rec = record.get("reconstruction") if isinstance(record.get("reconstruction"), dict) else {}
    text = (
        rec.get("plain_text") or rec.get("latex")
        or src.get("plain_text") or src.get("latex")
        or record.get("label") or record.get("equation_id") or ""
    )
    return str(text)[:80]


def _artifact_claim_text_index(claim_objects: list[dict[str, Any]]) -> dict[str, str]:
    """ClaimObjectBuilder artifact の ``claims[]`` から「claim_id → 本文」の索引を
    組み立てる（純粋関数）。

    ``theory_components.evidence_claims`` には DB UUID（remap 済みの親 claim）と
    agent 側の atomic sub-claim ID（``claim_span_001_sub01`` 形式。sub-claim は
    theory_claims 行にならないため remap されず素通りする）が混在する。sub-claim ID
    は ``_claim_id_lookup`` では解決できないため、この索引を DB 未解決 ID の表示ラベル
    代替源として使う（本文優先・空なら normalized_text）。
    """
    index: dict[str, str] = {}
    for c in claim_objects:
        if not isinstance(c, dict):
            continue
        claim_id = str(c.get("claim_id") or "").strip()
        if not claim_id:
            continue
        text = str(c.get("text") or "").strip() or str(c.get("normalized_text") or "").strip()
        if text:
            index[claim_id] = text
    return index


def _claim_object_for(
    claims: list[dict[str, Any]], claim_lookup: dict[str, str], element_id: str
) -> dict[str, Any] | None:
    """ClaimObjectRecord のうち、claim_lookup 経由で element_id（DB UUID）に一致する
    ものを1件返す（claim_id の書式差を吸収する）。"""
    for c in claims:
        if not isinstance(c, dict):
            continue
        raw_id = str(c.get("claim_id") or "")
        if raw_id and claim_lookup.get(raw_id) == element_id:
            return c
    return None


def _claim_in_ids(raw_ids: Any, claim_lookup: dict[str, str], element_id: str) -> bool:
    return any(claim_lookup.get(str(rid)) == element_id for rid in (raw_ids or []))


def _thesis_upper_items_for_claim(
    thesis: dict[str, Any], claim_lookup: dict[str, str], element_id: str, document_id: str | None
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not isinstance(thesis, dict):
        return items
    central = thesis.get("central_thesis") if isinstance(thesis.get("central_thesis"), dict) else {}
    headline = str(thesis.get("headline_claim") or "").strip() or "中心命題"
    if _claim_in_ids(central.get("claim_ids"), claim_lookup, element_id):
        items.append(_item("thesis", None, document_id, headline, "supports_thesis", _status_for_link("explicit")))
    if _claim_in_ids(thesis.get("supporting_subclaim_ids"), claim_lookup, element_id):
        items.append(_item("thesis", None, document_id, headline, "supports_thesis", _status_for_link("explicit")))
    support = thesis.get("support_structure") if isinstance(thesis.get("support_structure"), dict) else {}
    for section_name, entries in support.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and _claim_in_ids(entry.get("claim_ids"), claim_lookup, element_id):
                label = _SUPPORT_SECTION_LABELS.get(str(section_name), str(section_name))
                items.append(
                    _item("thesis", None, document_id, f"支持構造「{label}」", "supports_thesis", _status_for_link("explicit"))
                )
    return items


def _thesis_upper_items_for_equation(
    thesis: dict[str, Any], element_id: str, document_id: str | None
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not isinstance(thesis, dict):
        return items
    central = thesis.get("central_thesis") if isinstance(thesis.get("central_thesis"), dict) else {}
    headline = str(thesis.get("headline_claim") or "").strip() or "中心命題"
    if element_id in [str(x) for x in (central.get("equation_ids") or [])]:
        items.append(_item("thesis", None, document_id, headline, "supports_thesis", _status_for_link("explicit")))
    support = thesis.get("support_structure") if isinstance(thesis.get("support_structure"), dict) else {}
    for section_name, entries in support.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and element_id in [str(x) for x in (entry.get("equation_ids") or [])]:
                label = _SUPPORT_SECTION_LABELS.get(str(section_name), str(section_name))
                items.append(
                    _item("thesis", None, document_id, f"支持構造「{label}」", "supports_thesis", _status_for_link("explicit"))
                )
    return items


def _thesis_context_upper_items(
    thesis: dict[str, Any] | None,
    thesis_context: dict[str, Any] | None,
    document_id: str | None,
) -> list[dict[str, Any]]:
    """theory_components.thesis_context.supports_thesis_node_ids を thesis 構造で解決し、
    コンポーネント → 中心命題 / 支持構造 の上位項目を組み立てる（純粋関数）。

    ``supports_thesis_node_ids`` は component_assembly が claim/equation の重なりから
    決定論的に導出したノード参照（persistence.py の ``_thesis_ref_nodes`` と同じ
    ``central_thesis`` / ``support:<section>:<idx>`` 語彙）なので source_backed とする。
    これがコンポーネントをグラフノードでなくても本文の主な流れ（中心命題・支持構造）へ
    結びつける主経路である（設計書 §4.2 の「中心命題との関係」）。thesis_reconstruction
    artifact が無い / 参照先が解決できない場合でもノード ID をそのままラベルにして残す
    （P4: 情報を落とさない・推測で穴埋めしない）。
    """
    if not isinstance(thesis_context, dict):
        return []
    node_ids = thesis_context.get("supports_thesis_node_ids")
    if not isinstance(node_ids, list) or not node_ids:
        return []
    thesis = thesis if isinstance(thesis, dict) else {}
    headline = str(thesis.get("headline_claim") or "").strip() or "中心命題"
    support = thesis.get("support_structure") if isinstance(thesis.get("support_structure"), dict) else {}
    status = _status_for_link("explicit")
    items: list[dict[str, Any]] = []
    for raw in node_ids:
        node_id = str(raw or "").strip()
        if not node_id:
            continue
        if node_id == "central_thesis":
            items.append(_item("thesis", None, document_id, headline, "supports_thesis", status, evidence_refs=[node_id]))
            continue
        if node_id.startswith("support:"):
            parts = node_id.split(":")
            section_name = parts[1] if len(parts) > 1 else ""
            idx_raw = parts[2] if len(parts) > 2 else ""
            section_label = _SUPPORT_SECTION_LABELS.get(section_name, section_name or node_id)
            label = f"支持構造「{section_label}」"
            entries = support.get(section_name) if isinstance(support.get(section_name), list) else []
            entry = None
            try:
                entry = entries[int(idx_raw)] if idx_raw != "" else None
            except (ValueError, IndexError):
                entry = None
            if isinstance(entry, dict):
                excerpt = str(entry.get("text") or "").strip()
                if excerpt:
                    label = f"{label}: {excerpt[:60]}"
            items.append(_item("thesis", None, document_id, label, "supports_thesis", status, evidence_refs=[node_id]))
            continue
        # 未知の node id 形式でも落とさず、推測せずそのまま残す（P4）。
        items.append(_item("thesis", None, document_id, node_id, "supports_thesis", status, evidence_refs=[node_id]))
    return items


def _section_items_from_ids(
    section_ids: list[str],
    sections_by_id: dict[str, dict[str, Any]],
    document_id: str | None,
) -> list[dict[str, Any]]:
    """section_id の集合 → 掲載セクション上位項目（純粋関数）。

    ラベル（見出し）が引けない section_id は上位に出さない（節見出しの無い chunk 由来
    ノイズを本流の位置づけに混ぜない）。掲載セクションは決定論的に辿れるため
    source_backed。
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sid in section_ids:
        key = str(sid or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        label = _section_label(sections_by_id.get(key))
        if label:
            items.append(
                _item("section", None, document_id, label, "appears_in_section", _status_for_link("explicit"))
            )
    return items


def _derivation_membership_facts(
    chains: list[dict[str, Any]], document_id: str | None, member_check: Callable[[str], bool]
) -> list[dict[str, Any]]:
    """対象（claim または equation の id）が derivation_chain artifact のどこに
    現れるかの事実項目（非ナビゲーション）。``member_check`` は生の id 文字列を
    受け取り一致判定する（claim は翻訳込みの判定を呼び出し側が渡す）。
    """
    items: list[dict[str, Any]] = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        derivation_id = str(chain.get("derivation_id") or "")
        chain_ids: list[str] = []
        for key in (
            "input_claim_ids", "output_claim_ids", "assumption_ids",
            "input_equation_ids", "output_equation_ids", "intermediate_equation_ids",
        ):
            chain_ids.extend(str(x) for x in (chain.get(key) or []))
        step_hit: str | None = None
        for step in chain.get("steps") or []:
            if not isinstance(step, dict):
                continue
            step_ids: list[str] = []
            for key in (
                "input_claim_ids", "output_claim_ids", "required_claim_ids",
                "assumption_ids", "input_equation_ids", "output_equation_ids",
            ):
                step_ids.extend(str(x) for x in (step.get(key) or []))
            if any(member_check(x) for x in step_ids):
                step_hit = str(step.get("step_id") or "")
                break
        if step_hit:
            label = f"導出「{derivation_id}」のステップ「{step_hit}」"
            items.append(
                _item(
                    "derivation", None, document_id, label, "belongs_to_derivation",
                    _status_for_link("explicit"), evidence_refs=[step_hit],
                )
            )
        elif any(member_check(x) for x in chain_ids):
            label = f"導出「{derivation_id}」"
            items.append(
                _item(
                    "derivation", None, document_id, label, "belongs_to_derivation",
                    _status_for_link("explicit"), evidence_refs=[derivation_id],
                )
            )
    return items


def _stage_participation_items(
    graph_nodes: list[dict[str, Any]],
    claim_lookup: dict[str, str],
    component_claim_ids: list[Any],
    document_id: str | None,
) -> list[dict[str, Any]]:
    """component の evidence_claims と TheoryOperationGraph の main ステージノードの
    linked_claim_ids が交差する場合、「この要素はどの理論段階に関与するか」を上位項目
    として返す（純粋関数。課題B）。

    グラフが component_id を持たない main ノード（``theory_op_XXXX`` 形式・
    theory_components 行に対応しない集約ノード）へも claim 集合の交差経由で接続できる
    ようにする。グラフ側の claim id は agent 側 ID のまま（remap されない）、
    component 側は DB UUID へ remap 済みという表記差を ``claim_lookup``
    （agent ID → DB UUID、DB UUID は恒等写像）で両辺とも正規化してから比較する。

    対象は ``graph_layer == "main"`` のノードのみ（式単位の ``equation_detail`` /
    非確定な ``debug`` 層は理論段階の集約ラベルを持たないため対象外。設計書の main/
    detail 2層分離を尊重する）。

    claim 集合の交差は決定論的な集合演算だが、component_assembly が component と
    main ノードを直接結ぶ明示リンクを持たない（node は theory_components 行に
    対応しない）ため、A層の明示リンク（explicit）とは呼べない。したがって
    relation_status は常に candidate（inferred）に倒す — 図レンズの
    ``related_component_candidate`` と同じ扱い。
    """
    component_ids = {
        claim_lookup.get(str(cid), str(cid)) for cid in (component_claim_ids or []) if str(cid or "").strip()
    }
    if not component_ids:
        return []
    items: list[dict[str, Any]] = []
    for node in graph_nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("graph_layer") or "main") != "main":
            continue
        node_claim_ids = {
            claim_lookup.get(str(cid), str(cid))
            for cid in (node.get("linked_claim_ids") or [])
            if str(cid or "").strip()
        }
        overlap = component_ids & node_claim_ids
        if not overlap:
            continue
        label = str(node.get("label") or "").strip() or str(node.get("id") or "")
        items.append(
            _item(
                "stage", None, document_id, label, "participates_in_stage",
                _status_for_link("inferred"), evidence_refs=sorted(overlap)[:3],
            )
        )
    return items


def _evidence_quote(artifacts: dict[str, Any], evidence_id: str) -> str:
    items = _list(artifacts.get("evidence_registry"), "records")
    for item in items:
        if str(item.get("evidence_id") or "") == str(evidence_id):
            return str(item.get("evidence_text") or "").strip()
    return ""


def _safe(value_fn: Callable[[], Any], default: Any) -> Any:
    """DB/artifact 読み出しの部分失敗を1レーン分だけ握って縮退させる（W6）。"""
    try:
        return value_fn()
    except Exception:  # noqa: BLE001
        logger.warning("context_lens sub-step failed", exc_info=True)
        return default


# ---------------------------------------------------------------------------
# DB 読み出しヘルパ（core.postgres.get_session 経由・try/finally で close）
# ---------------------------------------------------------------------------


def _load_claim_row(claim_id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id::text AS id, document_id, claim_type, text, normalized_text,
                       support_status, review_status, evidence_text, source_scope
                FROM theory_claims WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": claim_id},
        ).mappings().first()
    finally:
        session.close()
    if not row:
        return None
    data = dict(row)
    data["source_scope"] = data.get("source_scope") if isinstance(data.get("source_scope"), dict) else {}
    return data


def _claims_by_id(ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = [str(i) for i in ids if str(i or "").strip()]
    if not ids:
        return {}
    session = get_session()
    try:
        rows = session.execute(
            sa_text("SELECT id::text AS id, text, claim_type FROM theory_claims WHERE id::text = ANY(:ids)"),
            {"ids": ids},
        ).mappings().all()
    finally:
        session.close()
    return {str(r["id"]): dict(r) for r in rows}


def _claim_id_lookup(document_id: str) -> dict[str, str]:
    session = get_session()
    try:
        rows = session.execute(
            sa_text("SELECT id::text AS id, source_scope FROM theory_claims WHERE document_id = :doc"),
            {"doc": document_id},
        ).fetchall()
    finally:
        session.close()
    return _claim_id_lookup_from_rows([(r[0], r[1] if isinstance(r[1], dict) else {}) for r in rows])


def _components_supporting_claim(document_id: str, claim_id: str) -> list[dict[str, Any]]:
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text AS id, name FROM theory_components
                WHERE document_id = :doc AND evidence_claims @> CAST(:claim_json AS jsonb)
                """
            ),
            {"doc": document_id, "claim_json": json.dumps([claim_id])},
        ).mappings().all()
    finally:
        session.close()
    return [dict(r) for r in rows]


def _load_component_row(component_id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id::text AS id, document_id, name, component_type, summary, status,
                       review_status, dependencies, evidence_claims, source_scope,
                       thesis_context, source_chunks
                FROM theory_components WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": component_id},
        ).mappings().first()
    finally:
        session.close()
    if not row:
        return None
    data = dict(row)
    data["dependencies"] = data.get("dependencies") if isinstance(data.get("dependencies"), list) else []
    data["evidence_claims"] = data.get("evidence_claims") if isinstance(data.get("evidence_claims"), list) else []
    data["source_scope"] = data.get("source_scope") if isinstance(data.get("source_scope"), dict) else {}
    data["thesis_context"] = data.get("thesis_context") if isinstance(data.get("thesis_context"), dict) else None
    data["source_chunks"] = data.get("source_chunks") if isinstance(data.get("source_chunks"), list) else []
    return data


def _components_by_id(ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = [str(i) for i in ids if str(i or "").strip()]
    if not ids:
        return {}
    session = get_session()
    try:
        rows = session.execute(
            sa_text("SELECT id::text AS id, name FROM theory_components WHERE id::text = ANY(:ids)"),
            {"ids": ids},
        ).mappings().all()
    finally:
        session.close()
    return {str(r["id"]): dict(r) for r in rows}


def _component_id_lookup(document_id: str) -> dict[str, str]:
    session = get_session()
    try:
        rows = session.execute(
            sa_text("SELECT id::text AS id, source_scope FROM theory_components WHERE document_id = :doc"),
            {"doc": document_id},
        ).fetchall()
    finally:
        session.close()
    return _component_id_lookup_from_rows([(r[0], r[1] if isinstance(r[1], dict) else {}) for r in rows])


def _load_apparatus_components(document_id: str) -> list[dict[str, Any]]:
    """当該 document の装置・部品候補 theory_components 一覧（migration 041 語彙）。

    F2（persistence.py の source_scope マージ）以降、agent 側 source_scope の
    ``figure_id`` / ``figure_key`` が DB 行に残るため、figure 単位の厳密な対応付けが
    可能になる。``source_scope`` を併せて返すのはそのため（呼び出し側 `_build_figure`
    が figure_id/figure_key の一致で図ごとに絞り込む）。F2 適用前に persist された
    行や、図キーを持たない legacy 行は ``source_scope`` に ``figure_id``/``figure_key``
    が無いため、呼び出し側は document 単位の縮退表示 + notes にフォールバックする
    （P4: 情報を落とさない）。
    """
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text AS id, name, component_type, status, review_status, summary,
                       source_scope
                FROM theory_components
                WHERE document_id = :doc AND component_type IN ('apparatus', 'instrument', 'part')
                ORDER BY created_at ASC
                """
            ),
            {"doc": document_id},
        ).mappings().all()
    finally:
        session.close()
    result: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["source_scope"] = d.get("source_scope") if isinstance(d.get("source_scope"), dict) else {}
        result.append(d)
    return result


def _load_components_with_evidence_claims(document_id: str) -> list[dict[str, Any]]:
    """当該 document の theory_components 一覧（id/name/evidence_claims のみ）。

    図 → component（claim 交差）判定専用の読み取り専用ローダー（F3）。
    ``evidence_claims`` は persist 時に DB UUID へ remap 済み（persistence.py の
    ``_remap_string_list``）なので、``_claim_id_lookup`` で解決した図の
    linked_claim_ids（DB UUID）と素の集合演算で交差判定できる。W1: A層は書き換えない
    （読むだけ）。
    """
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text AS id, name, evidence_claims FROM theory_components WHERE document_id = :doc"
            ),
            {"doc": document_id},
        ).mappings().all()
    finally:
        session.close()
    result: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["evidence_claims"] = d.get("evidence_claims") if isinstance(d.get("evidence_claims"), list) else []
        result.append(d)
    return result


def _chunk_section_ids(chunk_ids: list[str]) -> list[str]:
    """component.source_chunks（chunk id）→ 掲載セクション id の集合を引く。

    ``source_chunks`` が chunks.id に対応しない値（agent 側 index 等）でも
    ``id::text = ANY(:ids)`` は単に一致 0 件になるだけで例外にはならない
    （呼び出し側は best-effort。fail-soft）。section_id が NULL の chunk は除外する。
    """
    ids = [str(c) for c in chunk_ids if str(c or "").strip()]
    if not ids:
        return []
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT DISTINCT section_id FROM chunks "
                "WHERE id::text = ANY(:ids) AND section_id IS NOT NULL"
            ),
            {"ids": ids},
        ).fetchall()
    finally:
        session.close()
    return [str(r[0]) for r in rows if r[0]]


def _load_component_graph(document_id: str) -> dict[str, list[dict[str, Any]]]:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT graph_json FROM theory_component_graphs WHERE document_id = :doc "
                "ORDER BY updated_at DESC LIMIT 1"
            ),
            {"doc": document_id},
        ).fetchone()
    finally:
        session.close()
    graph = row[0] if row and isinstance(row[0], dict) else {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    return {
        "nodes": [n for n in nodes if isinstance(n, dict)],
        "edges": [e for e in edges if isinstance(e, dict)],
    }


def _load_figure_row(figure_id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id::text AS id, document_id, figure_key, figure_label, caption_text,
                       caption_block_id, page, bbox,
                       suggested_mode, mode_reason, analysis_profile, reviewed_mode,
                       mode_review_status, reviewed_analysis_mode, reviewed_analysis_profile,
                       analysis_review_status
                FROM document_figures WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": figure_id},
        ).mappings().first()
    finally:
        session.close()
    return dict(row) if row else None


def _annotations_for(element_type: str, element_id: str, document_id: str | None) -> list[dict[str, Any]]:
    return _safe(
        lambda: store_mod.list_annotations_for_element(element_type, element_id, document_id=document_id),
        [],
    )


# ---------------------------------------------------------------------------
# focus.generic（設計書 §6 Phase 3: 汎用×固有の結線）
# ---------------------------------------------------------------------------
#
# confirmed な同一性リンク先の L層エントリ（active のみ）を focus 直下に「汎用説明」
# として添える。文脈依存情報（upper/lower・contextual_role）とは別欄で持つことで、
# 「一般に何か」と「この論文で何の役割か」を混ぜない（設計書 §2.1 の不変条項を継承）。


def _confirmed_identity_link_for_instance(ref: ElementRef) -> dict[str, Any] | None:
    """当該インスタンスに付いた confirmed な同一性リンクを1件返す（無ければ None）。

    候補（candidate）・却下（rejected）は対象にしない — 人間が確定した同一視のみを
    事実として扱う。複数 confirmed があれば作成順で最初の1件を使う。
    """
    if not ref.document_id:
        return None
    links = identity_links_mod.list_for_instance(ref.element_type, ref.element_id, ref.document_id)
    for link in links:
        if str(link.get("status") or "") == IDENTITY_LINK_STATUS_CONFIRMED:
            return link
    return None


def _generic_block_for_focus(ref: ElementRef) -> dict[str, Any] | None:
    """focus.generic を組み立てる（confirmed identity link → active な L層エントリ）。

    リンク無し・エントリが active でない・読み取り失敗は ``None``（fail-soft。
    既存の縮退契約 — focus 自体は必ず返る — を壊さない）。domain-scoped 要素
    （shared_part）は対象外（``build()`` 自体がその型を扱わない）。
    """
    if ref.scope != SCOPE_DOCUMENT:
        return None
    try:
        link = _confirmed_identity_link_for_instance(ref)
        if not link:
            return None
        shared_part_id = str(link.get("shared_part_id") or "").strip()
        if not shared_part_id:
            return None
        entry = library_store_mod.get_entry(shared_part_id)
        if not entry or str(entry.get("status") or "") != library_schema.STATUS_ACTIVE:
            return None
        return {
            "entry_id": shared_part_id,
            "name": str(entry.get("name") or ""),
            "summary": str(entry.get("summary") or ""),
            "standardization_status": str(
                entry.get("standardization_status") or library_schema.STANDARDIZATION_STATUS_UNKNOWN
            ),
        }
    except Exception:  # noqa: BLE001
        logger.warning(
            "context_lens generic block failed for %s:%s", ref.element_type, ref.element_id, exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# 要素型別プロジェクタ
# ---------------------------------------------------------------------------


def _build_claim(ref: ElementRef) -> dict[str, Any] | None:
    row = _load_claim_row(ref.element_id)
    if row is None:
        return None
    document_id = ref.document_id or row.get("document_id") or ""
    notes: list[str] = []
    artifacts = refs_mod.document_run_artifacts(document_id)
    claim_lookup = _safe(lambda: _claim_id_lookup(document_id), {})

    claim_objects = _list(artifacts.get("claim_object_builder"), "claims")
    if "claim_object_builder" not in artifacts:
        notes.append("旧 run のため claim_object_builder artifact が無く、一部の関係を表示できません")
    claim_obj = _claim_object_for(claim_objects, claim_lookup, ref.element_id)

    fig_records = _list(artifacts.get("figure_table_semantics"), "figures")
    if not fig_records and "figure_table_semantics" not in artifacts:
        notes.append("figure_table_semantics artifact が無いため図との関係を判定できません")
    # 図の表示ラベルは UUID そのままではなく caption を優先する（読み手向け・W8 とは
    # 無関係の可読性対応。caption が無ければ figure_id へフォールバック）。
    fig_caption_by_id = {
        str(f.get("figure_id")): str(f.get("caption") or "").strip()
        for f in fig_records
        if f.get("figure_id")
    }

    upper: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []

    thesis = artifacts.get("thesis_reconstruction")
    if isinstance(thesis, dict):
        upper.extend(_thesis_upper_items_for_claim(thesis, claim_lookup, ref.element_id, document_id))
    elif "thesis_reconstruction" not in artifacts:
        notes.append("thesis_reconstruction artifact が無いため中心命題との関係を判定できません")

    section_label = ""
    if claim_obj and claim_obj.get("section_title"):
        section_label = str(claim_obj.get("section_title") or "")
    else:
        section_id = row["source_scope"].get("section_id")
        if section_id:
            section_label = _section_label(_sections_by_id(artifacts).get(str(section_id)))
    if section_label:
        upper.append(_item("section", None, document_id, section_label, "appears_in_section", _status_for_link("explicit")))

    if claim_obj and claim_obj.get("parent_claim_id"):
        parent_raw = str(claim_obj.get("parent_claim_id"))
        parent_db = claim_lookup.get(parent_raw)
        parent_label = parent_raw
        if parent_db:
            parent_row = _safe(lambda: _claims_by_id([parent_db]), {}).get(parent_db)
            if parent_row:
                parent_label = parent_row.get("text") or parent_label
        upper.append(
            _item("theory_claim", parent_db, document_id, str(parent_label)[:80], "subclaim_of", _status_for_link("explicit"))
        )

    for comp in _safe(lambda: _components_supporting_claim(document_id, ref.element_id), []):
        upper.append(
            _item("theory_component", comp.get("id"), document_id, comp.get("name") or "component", "supports_component", _status_for_link("explicit"))
        )

    if claim_obj:
        for sub_raw in claim_obj.get("subclaim_ids") or []:
            sub_db = claim_lookup.get(str(sub_raw))
            label = str(sub_raw)
            if sub_db:
                sub_row = _safe(lambda: _claims_by_id([sub_db]), {}).get(sub_db)
                if sub_row:
                    label = sub_row.get("text") or label
            lower.append(_item("theory_claim", sub_db, document_id, str(label)[:80], "has_subclaim", _status_for_link("explicit")))

        eq_index = _equation_by_id(refs_mod.equation_records(document_id))
        for eq_id in claim_obj.get("equation_ids") or []:
            record = eq_index.get(str(eq_id))
            lower.append(
                _item("equation", str(eq_id) if record else None, document_id, _equation_label(record) or str(eq_id), "quantified_by", _status_for_link("explicit"))
            )
        for eq_id in claim_obj.get("inferred_equation_ids") or []:
            record = eq_index.get(str(eq_id))
            lower.append(
                _item("equation", str(eq_id) if record else None, document_id, _equation_label(record) or str(eq_id), "quantified_by", _status_for_link("inferred"))
            )

        for fig_id in claim_obj.get("figure_ids") or []:
            fig_label = fig_caption_by_id.get(str(fig_id)) or str(fig_id)
            lower.append(_item("figure", str(fig_id), document_id, fig_label[:80], "evidenced_by_figure", _status_for_link("explicit")))

        for evidence_id in claim_obj.get("source_evidence_ids") or []:
            quote = _evidence_quote(artifacts, evidence_id)
            lower.append(
                _item(
                    "evidence", None, document_id, quote or str(evidence_id), "rests_on_evidence",
                    _status_for_link("explicit"), evidence_refs=[str(evidence_id)],
                )
            )

    for fig in fig_records:
        linked = [str(x) for x in (fig.get("linked_claim_ids") or [])]
        if any(claim_lookup.get(cid) == ref.element_id for cid in linked):
            fig_id = str(fig.get("figure_id") or "")
            if fig_id:
                fig_label = str(fig.get("caption") or "").strip() or fig_id
                lower.append(_item("figure", fig_id, document_id, fig_label[:80], "evidenced_by_figure", _status_for_link("explicit")))

    chains = _list(artifacts.get("derivation_chain"), "chains")
    lower.extend(
        _derivation_membership_facts(chains, document_id, lambda raw: claim_lookup.get(raw) == ref.element_id)
    )

    upper = _cap_lane(_dedupe_items(upper), notes, "上位構造")
    lower = _cap_lane(_dedupe_items(lower), notes, "下位構造")

    annotations = _annotations_for(ELEMENT_THEORY_CLAIM, ref.element_id, document_id)
    role_text, role_status = _derive_contextual_role(upper, annotations)

    provenance = [f"theory_claims:{ref.element_id}"]
    span_id = row["source_scope"].get("span_id")
    if span_id:
        provenance.append(f"span:{span_id}")
    if claim_obj:
        provenance.append("claim_object_builder")

    focus = {
        "element_type": ELEMENT_THEORY_CLAIM,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": (row.get("text") or "claim")[:80],
        "intrinsic_summary": row.get("text") or row.get("normalized_text") or "",
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "provenance": provenance,
    }
    return {"focus": focus, "upper": upper, "lower": lower, "notes": notes}


def _build_equation(ref: ElementRef) -> dict[str, Any] | None:
    document_id = ref.document_id or ""
    records = refs_mod.equation_records(document_id)
    eq_index = _equation_by_id(records)
    record = eq_index.get(ref.element_id)
    if record is None:
        return None
    artifacts = refs_mod.document_run_artifacts(document_id)
    claim_lookup = _safe(lambda: _claim_id_lookup(document_id), {})
    notes: list[str] = []

    sem = record.get("semantics") if isinstance(record.get("semantics"), dict) else {}

    upper: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []

    def _claim_item(raw_id: Any, relation: str, status: str) -> dict[str, Any]:
        db_id = claim_lookup.get(str(raw_id))
        label = str(raw_id)
        if db_id:
            claim_row = _safe(lambda: _claims_by_id([db_id]), {}).get(db_id)
            if claim_row:
                label = claim_row.get("text") or label
        return _item("theory_claim", db_id, document_id, str(label)[:80], relation, status)

    for cid in sem.get("linked_claim_ids") or []:
        upper.append(_claim_item(cid, "quantifies", _status_for_link("explicit")))
    for cid in sem.get("inferred_claim_ids") or []:
        upper.append(_claim_item(cid, "quantifies", _status_for_link("inferred")))

    thesis = artifacts.get("thesis_reconstruction")
    if isinstance(thesis, dict):
        upper.extend(_thesis_upper_items_for_equation(thesis, ref.element_id, document_id))
    elif "thesis_reconstruction" not in artifacts:
        notes.append("thesis_reconstruction artifact が無いため中心命題との関係を判定できません")

    graph = _safe(lambda: _load_component_graph(document_id), {"nodes": [], "edges": []})
    if not graph.get("nodes") and "component_graph" not in artifacts:
        notes.append("component_graph が保存されていないため、理論コンポーネントとの関係を判定できません")
    for node in graph.get("nodes", []):
        if ref.element_id in [str(x) for x in (node.get("linked_equation_ids") or [])]:
            status = _status_for_link(_link_kind_from_backing_status(node.get("source_backing_status")))
            upper.append(
                _item("theory_component", node.get("id"), document_id, str(node.get("label") or ""), "supports_component", status)
            )

    chains = _list(artifacts.get("derivation_chain"), "chains")
    upper.extend(_derivation_membership_facts(chains, document_id, lambda raw: raw == ref.element_id))

    for out_id in sem.get("output_equation_ids") or []:
        out_record = eq_index.get(str(out_id))
        upper.append(
            _item("equation", str(out_id) if out_record else None, document_id, _equation_label(out_record) or str(out_id), "leads_to", _status_for_link("explicit"))
        )

    for in_id in sem.get("input_equation_ids") or []:
        in_record = eq_index.get(str(in_id))
        lower.append(
            _item("equation", str(in_id) if in_record else None, document_id, _equation_label(in_record) or str(in_id), "derives_from", _status_for_link("explicit"))
        )

    symbol_records = _list(artifacts.get("symbol_registry"), "records")
    for srec in symbol_records:
        defining = [str(x) for x in (srec.get("defining_equation_ids") or [])]
        used = [str(x) for x in (srec.get("used_in_equation_ids") or [])]
        if ref.element_id in defining or ref.element_id in used:
            symbol = str(srec.get("canonical_symbol") or "").strip()
            if not symbol:
                continue
            evidences = [str(e).strip() for e in (srec.get("definition_evidence_texts") or []) if str(e or "").strip()]
            lower.append(
                _item("symbol", None, document_id, symbol, "uses_symbol", _status_for_link("explicit"), evidence_refs=evidences[:1])
            )

    for chain in chains:
        if not isinstance(chain, dict):
            continue
        for step in chain.get("steps") or []:
            if not isinstance(step, dict):
                continue
            outputs = [str(x) for x in (step.get("output_equation_ids") or [])]
            if ref.element_id in outputs:
                for req_raw in step.get("required_claim_ids") or []:
                    lower.append(_claim_item(req_raw, "requires", _status_for_link("explicit")))

    upper = _cap_lane(_dedupe_items(upper), notes, "上位構造")
    lower = _cap_lane(_dedupe_items(lower), notes, "下位構造")

    annotations = _annotations_for(ELEMENT_EQUATION, ref.element_id, document_id)
    role_text, role_status = _derive_contextual_role(upper, annotations)

    label = _equation_label(record) or ref.element_id
    focus = {
        "element_type": ELEMENT_EQUATION,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": label,
        "intrinsic_summary": label,
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "provenance": [f"equation_semantics:{ref.element_id}"],
    }
    return {"focus": focus, "upper": upper, "lower": lower, "notes": notes}


def _build_component(ref: ElementRef) -> dict[str, Any] | None:
    row = _load_component_row(ref.element_id)
    if row is None:
        return None
    document_id = ref.document_id or row.get("document_id") or ""
    notes: list[str] = []

    graph = _safe(lambda: _load_component_graph(document_id), {"nodes": [], "edges": []})
    nodes_by_id = {str(n.get("id")): n for n in graph.get("nodes", []) if n.get("id")}
    node = nodes_by_id.get(ref.element_id)
    if not graph.get("nodes"):
        notes.append("component_graph が保存されていないため、上位/下位のグラフ関係を判定できません")
    component_lookup = _safe(lambda: _component_id_lookup(document_id), {})
    # evidence_claims の解決（課題A）と main ステージノードとの claim 交差判定
    # （課題B）の両方で使う。
    claim_lookup = _safe(lambda: _claim_id_lookup(document_id), {})

    # 本流（中心命題・支持構造・掲載セクション）への接続に thesis_reconstruction
    # artifact と document_structure を使う。読み取り失敗はグラフ由来の上位/下位を
    # 壊さないよう {} へ縮退させる（W6）。
    artifacts = _safe(lambda: refs_mod.document_run_artifacts(document_id), {})
    thesis = artifacts.get("thesis_reconstruction")
    thesis = thesis if isinstance(thesis, dict) else None
    headline = str((thesis or {}).get("headline_claim") or "").strip() or "中心命題"

    upper: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []

    if node:
        parent_raw = str(node.get("parent_component_id") or "").strip()
        if parent_raw:
            parent_db = component_lookup.get(parent_raw)
            parent_node = nodes_by_id.get(parent_db) if parent_db else None
            label = parent_node.get("label") if parent_node else parent_raw
            upper.append(
                _item("theory_component", parent_db if parent_node else None, document_id, str(label or parent_raw), "member_of", _status_for_link("explicit"))
            )

        if node.get("is_thesis_anchor"):
            # thesis_context 由来の central_thesis 項目とラベルを揃えて重複排除させる。
            upper.append(_item("thesis", None, document_id, headline, "supports_thesis", _status_for_link("explicit")))

        for member_raw in node.get("member_component_ids") or []:
            member_db = component_lookup.get(str(member_raw))
            member_node = nodes_by_id.get(member_db) if member_db else None
            label = member_node.get("label") if member_node else str(member_raw)
            lower.append(
                _item("theory_component", member_db if member_node else None, document_id, str(label or member_raw), "contains", _status_for_link("explicit"))
            )

        for eq_id in node.get("linked_equation_ids") or []:
            status = _status_for_link(_link_kind_from_backing_status(node.get("source_backing_status")))
            lower.append(_item("equation", str(eq_id), document_id, str(eq_id), "uses_equation", status))

    for edge in graph.get("edges", []):
        src_id = str(edge.get("source_component_id") or "")
        dst_id = str(edge.get("target_component_id") or "")
        status = _status_for_link(_link_kind_from_backing_status(edge.get("source_backing_status")))
        edge_kind = str(edge.get("edge_type") or edge.get("relation") or "").strip().upper()
        if src_id == ref.element_id and dst_id:
            other = nodes_by_id.get(dst_id)
            label = other.get("label") if other else dst_id
            relation = "requires" if edge_kind in ("REQUIRES", "DEPENDS_ON") else "relates_to_component"
            lower.append(_item("theory_component", dst_id if other else None, document_id, str(label or dst_id), relation, status))
        elif dst_id == ref.element_id and src_id:
            other = nodes_by_id.get(src_id)
            label = other.get("label") if other else src_id
            upper.append(_item("theory_component", src_id if other else None, document_id, str(label or src_id), "used_by_component", status))

    for dep in row.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        for raw_ref in dep.get("component_refs") or []:
            dep_db = component_lookup.get(str(raw_ref))
            dep_node = nodes_by_id.get(dep_db) if dep_db else None
            label = dep_node.get("label") if dep_node else str(raw_ref)
            lower.append(
                _item("theory_component", dep_db if dep_node else None, document_id, str(label or raw_ref), "requires", _status_for_link("explicit"))
            )

    # evidence_claims の解決（課題A）。theory_components.evidence_claims は DB UUID
    # （remap 済みの親 claim）と agent 側 atomic sub-claim ID（DB に存在せず remap
    # されない）が混在するため、まず claim_lookup で解決できる ID だけを1回の
    # バッチ取得（N+1 回避）にまとめ、解決できない ID は ClaimObjectBuilder artifact
    # の本文で補い、それも無ければ生ID表示に縮退する（P4: 情報を落とさない）。
    evidence_claim_ids = [str(c) for c in (row.get("evidence_claims") or []) if str(c or "").strip()]
    resolved_db_ids: list[str] = []
    for cid in evidence_claim_ids:
        db_id = claim_lookup.get(cid)
        if db_id and db_id not in resolved_db_ids:
            resolved_db_ids.append(db_id)
    # DB 取得の失敗をこの1呼び出しだけに絞ることで、失敗しても後段の artifact
    # フォールバックは影響を受けずに機能する。
    claim_rows = _safe(lambda: _claims_by_id(resolved_db_ids), {}) if resolved_db_ids else {}
    artifact_claim_text: dict[str, str] | None = None
    for cid in evidence_claim_ids:
        db_id = claim_lookup.get(cid)
        if db_id:
            claim_row = claim_rows.get(db_id)
            label = claim_row.get("text") if claim_row else db_id
            lower.append(
                _item("theory_claim", db_id, document_id, str(label or db_id)[:80], "backed_by_claim", _status_for_link("explicit"))
            )
            continue
        if artifact_claim_text is None:
            artifact_claim_text = _artifact_claim_text_index(_list(artifacts.get("claim_object_builder"), "claims"))
        artifact_text = artifact_claim_text.get(cid)
        if artifact_text:
            lower.append(
                _item(
                    "theory_claim", None, document_id, str(artifact_text)[:80], "backed_by_claim",
                    _status_for_link("explicit"), evidence_refs=[cid],
                )
            )
        else:
            lower.append(_item("theory_claim", None, document_id, cid, "backed_by_claim", _status_for_link("explicit")))

    # 理論段階（main ステージノード）との claim 交差（課題B）。グラフノードが
    # ある場合は親 main ノードが member_of 経由で既に上位に出ているため、
    # node が無い（component_graph 未生成 or ノード非対応）ときだけ追加する。
    if node is None:
        upper.extend(
            _safe(
                lambda: _stage_participation_items(
                    graph.get("nodes", []), claim_lookup, evidence_claim_ids, document_id
                ),
                [],
            )
        )

    # 中心命題・支持構造への接続（thesis_context・主軸）。グラフノードでなくても
    # component_assembly が決定論的に導出した thesis_context から本流へ結びつける。
    thesis_context = row.get("thesis_context") if isinstance(row.get("thesis_context"), dict) else None
    thesis_context_items = _thesis_context_upper_items(thesis, thesis_context, document_id)
    if thesis_context_items and thesis is None and "thesis_reconstruction" not in artifacts:
        notes.append("thesis_reconstruction artifact が無いため、中心命題との対応はノード参照のみ表示しています")
    upper.extend(thesis_context_items)

    # 掲載セクション（source_chunks 由来・best-effort）。
    section_items = _safe(
        lambda: _section_items_from_ids(
            _chunk_section_ids([str(c) for c in (row.get("source_chunks") or [])]),
            _sections_by_id(artifacts),
            document_id,
        ),
        [],
    )
    upper.extend(section_items)

    upper = _cap_lane(_dedupe_items(upper), notes, "上位構造")
    lower = _cap_lane(_dedupe_items(lower), notes, "下位構造")

    annotations = _annotations_for(ELEMENT_THEORY_COMPONENT, ref.element_id, document_id)
    role_in_thesis = str((thesis_context or {}).get("role_in_thesis") or "").strip()
    role_text, role_status = _derive_contextual_role(
        upper, annotations, fallback_role=role_in_thesis, fallback_status=CONTEXT_STATUS_SOURCE_BACKED
    )

    provenance = [f"theory_components:{ref.element_id}"]
    if node:
        provenance.append("component_graph")
    if thesis_context_items or role_in_thesis:
        provenance.append("thesis_context")
    if section_items:
        provenance.append("chunks")

    focus = {
        "element_type": ELEMENT_THEORY_COMPONENT,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": row.get("name") or "component",
        "intrinsic_summary": row.get("summary") or "",
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "provenance": provenance,
    }
    return {"focus": focus, "upper": upper, "lower": lower, "notes": notes}


def _figure_part_items_from_profile(
    profile: dict[str, Any], analysis_review_status: str, document_id: str | None
) -> list[dict[str, Any]]:
    """presentation_payload の analysis_profile（functions/subjects）から図の下位構造
    パーツ項目を組み立てる（純粋関数。#496 の構造化解析ペイン相当）。

    ``analysis_review_status == "reviewed"`` のときのみ人間確定（confirmed）。
    それ以外（AI 候補のまま）は candidate に留める（W2 継承）。
    """
    status = _status_for_link("committed") if analysis_review_status == "reviewed" else _status_for_link("inferred")
    items: list[dict[str, Any]] = []
    for key in ("functions", "subjects"):
        for part in profile.get(key) or []:
            if not isinstance(part, dict):
                continue
            name = str(part.get("name") or part.get("id") or "").strip()
            if name:
                items.append(_item("part", None, document_id, name, "contains", status))
    return items


def _figure_part_items_from_apparatus_record(
    record: dict[str, Any], document_id: str | None
) -> list[dict[str, Any]]:
    """apparatus_semantics artifact の ApparatusRecord.parts から図の下位構造パーツ
    項目を組み立てる（純粋関数）。vision 由来の候補のため常に candidate。
    """
    items: list[dict[str, Any]] = []
    for part in (record or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        name = str(part.get("name") or "").strip()
        if not name:
            continue
        evidence_quote = str(part.get("evidence_quote") or "").strip()
        items.append(
            _item(
                "part", None, document_id, name, "contains", _status_for_link("inferred"),
                evidence_refs=[evidence_quote] if evidence_quote else [],
            )
        )
    return items


def _figure_apparatus_component_items(
    components: list[dict[str, Any]], document_id: str | None
) -> list[dict[str, Any]]:
    """装置・部品候補 theory_components の一覧から、図の下位構造の navigable な
    theory_component 項目を組み立てる（純粋関数）。

    渡された ``components`` を無条件にアイテム化するだけで、当該図への絞り込み
    （figure_id/figure_key 一致 or document 単位への縮退）は呼び出し側
    （``_build_figure``）の責務とする。document 単位に縮退した場合は呼び出し側が
    事実として notes に明示すること（P4）。
    """
    items: list[dict[str, Any]] = []
    for comp in components:
        status = (
            _status_for_link("committed")
            if str(comp.get("review_status") or "") == "teacher_approved"
            else _status_for_link("inferred")
        )
        items.append(
            _item("theory_component", comp.get("id"), document_id, comp.get("name") or "apparatus", "contains", status)
        )
    return items


def _build_figure(ref: ElementRef) -> dict[str, Any] | None:
    fig = _load_figure_row(ref.element_id)
    if fig is None:
        return None
    document_id = ref.document_id or fig.get("document_id") or ""
    notes: list[str] = []
    artifacts = refs_mod.document_run_artifacts(document_id)
    claim_lookup = _safe(lambda: _claim_id_lookup(document_id), {})
    component_lookup = _safe(lambda: _component_id_lookup(document_id), {})

    upper: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []

    caption_block_id = fig.get("caption_block_id")
    if caption_block_id:
        block = _blocks_by_id(artifacts).get(str(caption_block_id))
        if block:
            section = _sections_by_id(artifacts).get(str(block.get("section_id") or ""))
            label = _section_label(section)
            if label:
                upper.append(_item("section", None, document_id, label, "appears_in_section", _status_for_link("explicit")))

    fig_records = _list(artifacts.get("figure_table_semantics"), "figures")
    matched_record = _matching_figure_record(fig_records, fig)
    if matched_record is None:
        if "figure_table_semantics" not in artifacts:
            notes.append("figure_table_semantics artifact が無いため、関連 claim を判定できません")
        elif not fig_records:
            notes.append("figure_table_semantics に図レコードが無いため、関連 claim を判定できません")
        else:
            notes.append("図レコードと figure_table_semantics の対応を特定できません")

    linked_claim_db_ids: list[str] = []
    if matched_record:
        for cid in matched_record.get("linked_claim_ids") or []:
            claim_db = claim_lookup.get(str(cid))
            claim_row = _safe(lambda: _claims_by_id([claim_db]), {}).get(claim_db) if claim_db else None
            label = claim_row.get("text") if claim_row else str(cid)
            upper.append(
                _item("theory_claim", claim_db, document_id, str(label)[:80], "provides_evidence_for", _status_for_link("explicit"))
            )
            if claim_db:
                linked_claim_db_ids.append(claim_db)

        for comp_raw in matched_record.get("linked_component_candidates") or []:
            comp_db = component_lookup.get(str(comp_raw))
            label = str(comp_raw)
            if comp_db:
                comp_row = _safe(lambda: _components_by_id([comp_db]), {}).get(comp_db)
                if comp_row:
                    label = comp_row.get("name") or label
            upper.append(
                _item("theory_component", comp_db, document_id, label, "related_component_candidate", _status_for_link("inferred"))
            )

    if linked_claim_db_ids:
        linked_claim_id_set = set(linked_claim_db_ids)
        for comp in _safe(lambda: _load_components_with_evidence_claims(document_id), []):
            evidence_claims = {str(x) for x in (comp.get("evidence_claims") or [])}
            if linked_claim_id_set & evidence_claims:
                upper.append(
                    _item(
                        "theory_component", comp.get("id"), document_id,
                        comp.get("name") or "component", "related_component_candidate",
                        _status_for_link("inferred"),
                    )
                )

    thesis = artifacts.get("thesis_reconstruction")
    if isinstance(thesis, dict) and linked_claim_db_ids:
        headline = str(thesis.get("headline_claim") or "").strip() or "中心命題"
        for claim_db in linked_claim_db_ids:
            if _thesis_upper_items_for_claim(thesis, claim_lookup, claim_db, document_id):
                upper.append(
                    _item(
                        "thesis", None, document_id, headline, "supports_thesis",
                        _status_for_link("explicit"), evidence_refs=[f"claim:{claim_db}"],
                    )
                )
                break

    apparatus_artifact = artifacts.get("apparatus_semantics")
    apparatus_records = _list(apparatus_artifact, "apparatus_records")
    matched_apparatus = _matching_figure_record(apparatus_records, fig)
    if (
        not matched_apparatus
        and isinstance(apparatus_artifact, dict)
        and apparatus_artifact.get("skipped_by_option") is True
    ):
        notes.append("画像解析オプションが無効だったため、図の部品構造は解析されていません")

    presentation = _safe(
        lambda: presentation_payload(dict(fig), matched_apparatus or {}, caption_text=str(fig.get("caption_text") or "")),
        {},
    )
    analysis_review_status = str(presentation.get("analysis_review_status") or "")
    profile = presentation.get("analysis_profile") if isinstance(presentation.get("analysis_profile"), dict) else {}
    profile_items = _figure_part_items_from_profile(profile, analysis_review_status, document_id)
    if profile_items:
        lower.extend(profile_items)
    elif matched_apparatus:
        lower.extend(_figure_part_items_from_apparatus_record(matched_apparatus, document_id))

    apparatus_components = _safe(lambda: _load_apparatus_components(document_id), [])
    if any(_has_figure_scope_key(c) for c in apparatus_components):
        # F2 適用後: 図対応キー（figure_id/figure_key）を持つ行が1つでもあれば、
        # 当該図に一致するものだけを図単位で表示する(「論文単位」note は不要)。
        figure_scoped_components = [
            c for c in apparatus_components if _apparatus_component_matches_figure(c, fig)
        ]
    else:
        # 全行が legacy（図対応キーなし）の場合は従来どおり document 単位の縮退表示
        # + notes（P4: 情報を落とさない）。
        figure_scoped_components = apparatus_components
        if figure_scoped_components:
            notes.append("装置・部品候補は論文単位の一覧です（図ごとの厳密な対応付けは未対応）")
    lower.extend(_figure_apparatus_component_items(figure_scoped_components, document_id))

    upper = _cap_lane(_dedupe_items(upper), notes, "上位構造")
    lower = _cap_lane(_dedupe_items(lower), notes, "下位構造")

    annotations = _annotations_for(ELEMENT_FIGURE, ref.element_id, document_id)
    role_text, role_status = _derive_contextual_role(upper, annotations)

    provenance = [f"document_figures:{ref.element_id}"]
    if matched_record:
        provenance.append("figure_table_semantics")
    if matched_apparatus:
        provenance.append("apparatus_semantics")

    focus = {
        "element_type": ELEMENT_FIGURE,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": fig.get("figure_label") or fig.get("figure_key") or "figure",
        "intrinsic_summary": fig.get("caption_text") or "",
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "provenance": provenance,
    }
    return {"focus": focus, "upper": upper, "lower": lower, "notes": notes}


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------


_BUILDERS: dict[str, Callable[[ElementRef], dict[str, Any] | None]] = {
    ELEMENT_THEORY_CLAIM: _build_claim,
    ELEMENT_THEORY_COMPONENT: _build_component,
    ELEMENT_EQUATION: _build_equation,
    ELEMENT_FIGURE: _build_figure,
}


def _degenerate_result(ref: ElementRef) -> dict[str, Any]:
    """要素が見つからない/読み取りに失敗した場合の fail-soft な既定形（設計書 W6）。"""
    return {
        "focus": {
            "element_type": ref.element_type,
            "element_id": ref.element_id,
            "document_id": ref.document_id,
            "label": ref.element_id,
            "intrinsic_summary": "",
            "contextual_role": None,
            "contextual_role_status": CONTEXT_ROLE_STATUS_UNIDENTIFIED,
            "provenance": [],
        },
        "upper": [],
        "lower": [],
        "notes": ["要素が見つからないか読み取りに失敗したため、文脈は表示できません"],
    }


def build(ref: ElementRef) -> dict[str, Any] | None:
    """ElementRef の要素中心コンテキストビュー（focus/upper/lower/notes）を組み立てる。

    ``shared_part``（scope='domain'）は本設計の対象外（論文への出現を前提とする
    document-scoped 4要素型のみ投影を持つ）のため ``None`` を返す。それ以外は
    どんな例外・欠損があっても必ず契約どおりの dict を返す（fail-soft・W6）。

    ``focus.generic``（設計書 §6 Phase 3）: confirmed な同一性リンク先の L層エントリ
    （active のみ）を汎用説明として添える。リンク無し・読み取り失敗は ``None``。
    """
    if ref.element_type == ELEMENT_SHARED_PART:
        return None
    builder = _BUILDERS.get(ref.element_type)
    if builder is None:
        return None
    try:
        result = builder(ref)
    except Exception:  # noqa: BLE001
        logger.warning(
            "context_lens build failed for %s:%s", ref.element_type, ref.element_id, exc_info=True
        )
        result = None
    result = result if result is not None else _degenerate_result(ref)
    result["focus"]["generic"] = _generic_block_for_focus(ref)
    return result
