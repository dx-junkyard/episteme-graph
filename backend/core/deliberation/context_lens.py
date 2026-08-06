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
      # ── 既存9キー（名も意味も不変）──────────────────────────────────────
      "element_type": "theory_claim"|"theory_component"|"equation"|"figure"|
                       "section"|"thesis"|"derivation"|"symbol"|"evidence"|"part"|"stage",
      "element_id": str | None,       # None = 表示のみ（非ナビゲーション）
                                      # evidence / derivation も §16 以降は ID を持ち得る
      "document_id": str | None,
      "label": str,
      "relation": str,                # RELATION_LABELS のキーである保証あり
      "relation_label": str,
      "relation_status": "source_backed"|"candidate"|"confirmed",
      "evidence_refs": [str, ...],
      "navigable": bool,
      # ── ITEM v2 追加5キー（純増。element_context_presentation_redesign.md §4.1）──
      "sublabel": str,                # 1行の区別材料・事実文。label と同一にしない（CP1）
      "qualifier": str,               # 種別内サブ種別の統制語彙キー（chain_type /
                                      # stage キー / definition_status / graph_layer）。
                                      # 訳語は element-vocab.js（CP4）
      "group": str,                   # 区画キー（ITEM_GROUPS）。未知値は「その他」区画へ
      "unresolved": bool,             # ラダーが尽き一般名で代替した（淡色表示）
      "label_source": str,            # 来歴。教員のみ（学習者射影で落とす）
    }

`focus` も同設計書 §4.2 で ``headline`` / ``intrinsic`` / ``placement`` /
``contextual_role_source`` / ``review_notes``（+ equation のみ ``derivations``）が
純増している。既存キー（label / intrinsic_summary / contextual_role /
contextual_role_status / provenance / generic）は不変。

`build(ref)` は `shared_part`（scope='domain'）のときのみ ``None`` を返す（設計書の
対象は document-scoped 要素型 = figure / theory_component / theory_claim / equation +
W層設計書 §16 で追加した evidence / derivation）。それ以外は例外を握って fail-soft の
縮退結果を返し、呼び出し元が 500 を気にしなくてよいようにする。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.document_pipeline.figure_images import normalize_figure_join_key
from core.element_vocab import link_status_fact, theory_stage_key
from core.figure_presentation import presentation_payload
from core.library import schema as library_schema
from core.library import store as library_store_mod
from core.deliberation import identity_links as identity_links_mod
from core.deliberation import labels as labels_mod
from core.deliberation import refs as refs_mod
from core.deliberation import store as store_mod
from core.text_excerpt import excerpt, first_sentence, looks_like_tex_math
from core.deliberation.schema import (
    ANNOTATION_KIND_INTERPRETATION,
    ANNOTATION_KIND_MEANING,
    ANNOTATION_STATUS_COMMITTED,
    CONTEXT_ROLE_STATUS_UNIDENTIFIED,
    CONTEXT_STATUS_CANDIDATE,
    CONTEXT_STATUS_CONFIRMED,
    CONTEXT_STATUS_SOURCE_BACKED,
    ELEMENT_DERIVATION,
    ELEMENT_EQUATION,
    ELEMENT_EVIDENCE,
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

# ITEM.navigable は element_type がこの6種のときだけ True になり得る（設計書 §5 +
# W層設計書 §16。evidence / derivation は ``refs.py`` の解決対象に入ったため中心に
# できる = navigable になり得る。section / thesis / symbol / stage / part は解決対象
# ではないので引き続き表示のみ）。
_NAVIGABLE_ELEMENT_TYPES = (
    ELEMENT_FIGURE,
    ELEMENT_THEORY_COMPONENT,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_EQUATION,
    ELEMENT_EVIDENCE,
    ELEMENT_DERIVATION,
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
    # ITEM が記号そのものなので「の記号を用いる」ではなく「を用いる」（§4.1 の訳語修正）。
    "uses_symbol": "を用いる",
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
    # ── 提示再設計（element_context_presentation_redesign.md §4.1）で追加した
    #    **向き付き**の語彙（CP2）。導出の入力と出力を同じ語に潰さない。────────
    "feeds_derivation": "の導出に入力として使われる",
    "produced_by_derivation": "の導出で得られる",
    "used_in_derivation": "の導出の中で使われる",
    "belongs_to_stage": "の理論段階に属する",
    "used_in_operation_step": "の操作ステップで使われる",
    "defines_symbol": "で記号を定義する",
}

# ── 区画キー（group, 設計書 §4.3）───────────────────────────────────────────────
# ITEM をフロントの4区画（①これは何か / ②位置づけ / ③何をもとに構成されるか /
# ④どこへ行くか）へ振り分けるための統制語彙。未知値はフロントで「その他」区画へ
# 落ちる（P4: 項目自体は落とさない）。
GROUP_STAGE = "stage"
GROUP_THESIS = "thesis"
GROUP_CLAIM = "claim"
GROUP_SECTION = "section"
GROUP_SYMBOL_DEFINED = "symbol_defined"
GROUP_SYMBOL_USED = "symbol_used"
GROUP_EQUATION_UP = "equation_up"
GROUP_EQUATION_DOWN = "equation_down"
GROUP_DERIVATION_IN = "derivation_in"
GROUP_DERIVATION_OUT = "derivation_out"
GROUP_CLAIM_REQUIRED = "claim_required"
GROUP_EVIDENCE = "evidence"
GROUP_OPERATION = "operation"
GROUP_FIGURE = "figure"
GROUP_COMPONENT = "component"
GROUP_RELATED = "related"

ITEM_GROUPS: tuple[str, ...] = (
    GROUP_STAGE,
    GROUP_THESIS,
    GROUP_CLAIM,
    GROUP_SECTION,
    GROUP_SYMBOL_DEFINED,
    GROUP_SYMBOL_USED,
    GROUP_EQUATION_UP,
    GROUP_EQUATION_DOWN,
    GROUP_DERIVATION_IN,
    GROUP_DERIVATION_OUT,
    GROUP_CLAIM_REQUIRED,
    GROUP_EVIDENCE,
    GROUP_OPERATION,
    GROUP_FIGURE,
    GROUP_COMPONENT,
    GROUP_RELATED,
)

# 文脈上の役割（focus.contextual_role）の来歴（設計書 §4.2 / §4.4）。
ROLE_SOURCE_COMMITTED = "committed"
ROLE_SOURCE_SELF_DESCRIBED = "self_described"
ROLE_SOURCE_STRUCTURAL = "structural"
ROLE_SOURCE_UNIDENTIFIED = "unidentified"

# ── 切り詰めの上限（CP5: 実装は core.text_excerpt.excerpt の1本のみ）──────────
_LABEL_LIMIT = 60
_SUBLABEL_LIMIT = 90
_FOCUS_LABEL_LIMIT = 80

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
    sublabel: str = "",
    qualifier: str = "",
    group: str = GROUP_RELATED,
    unresolved: bool = False,
    label_source: str = "",
) -> dict[str, Any]:
    """ITEM を1件組み立てる（upper/lower 共通。設計書 §5 → §4.1 の ITEM v2）。

    既存9キー（element_type / element_id / document_id / label / relation /
    relation_label / relation_status / evidence_refs / navigable）は**名も意味も
    不変**で、追加5キー（sublabel / qualifier / group / unresolved / label_source）は
    純増（旧フロントは既存キーだけで動き続ける）。

    ``RELATION_LABELS[relation]`` は通常の辞書アクセスで引く。関係の入力ミス・
    語彙追加漏れは KeyError として即座に検出される（silent に relation_label 空欄の
    まま出荷しない）。

    sublabel は「1行の区別材料・事実文」で、``label`` と同一文字列にはしない（CP1）。
    内部 ID 形の sublabel は捨てる（EC3′: 遮断は最後の砦であって、生成側で先に弾く）。
    """
    navigable = bool(element_id) and element_type in _NAVIGABLE_ELEMENT_TYPES
    head = str(label or "")
    sub = str(sublabel or "").strip()
    if sub and (sub == head or labels_mod.is_internal_id_like(sub)):
        sub = ""
    return {
        "element_type": element_type,
        "element_id": element_id,
        "document_id": document_id,
        "label": head,
        "relation": relation,
        "relation_label": RELATION_LABELS[relation],
        "relation_status": relation_status,
        "evidence_refs": [str(e) for e in (evidence_refs or []) if str(e or "").strip()],
        "navigable": navigable,
        "sublabel": sub,
        "qualifier": str(qualifier or ""),
        "group": str(group or GROUP_RELATED),
        "unresolved": bool(unresolved),
        "label_source": str(label_source or ""),
    }


def _item_from_label(
    element_type: str,
    element_id: str | None,
    document_id: str | None,
    label: "labels_mod.Label",
    relation: str,
    relation_status: str,
    *,
    group: str = GROUP_RELATED,
    evidence_refs: list[str] | None = None,
    qualifier: str | None = None,
) -> dict[str, Any]:
    """``labels.Label``（ラベルラダーの結果）から ITEM を1件組み立てる。"""
    return _item(
        element_type,
        element_id,
        document_id,
        label.text,
        relation,
        relation_status,
        evidence_refs=evidence_refs,
        sublabel=label.sublabel,
        qualifier=label.qualifier if qualifier is None else qualifier,
        group=group,
        unresolved=label.unresolved,
        label_source=label.label_source,
    )


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一の関係を2度出さない（設計書 §4.4）。

    キーは ``(element_type, element_id or label, relation)``。ラベル規則の変更で
    重複判定が揺れないよう ``label`` はキーから外し、``element_id`` が無い
    （表示のみの）項目だけラベルで区別する。統合時は ``sublabel`` が非空の側を残す
    （区別材料を落とさない = CP7）。
    """
    index: dict[tuple[Any, ...], int] = {}
    out: list[dict[str, Any]] = []
    for item in items:
        key = (item["element_type"], item["element_id"] or item["label"], item["relation"])
        position = index.get(key)
        if position is None:
            index[key] = len(out)
            out.append(item)
            continue
        kept = out[position]
        if not (kept.get("sublabel") or "") and (item.get("sublabel") or ""):
            kept["sublabel"] = item["sublabel"]
        if not (kept.get("qualifier") or "") and (item.get("qualifier") or ""):
            kept["qualifier"] = item["qualifier"]
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


# 構造要約（③）に使ってよい区画と、その区画ごとの事実文テンプレート。
# 「upper[0] の label + relation_label」という機械連結は廃止し（RC7）、位置づけを
# 語れる区画（理論段階・中心命題・主張）だけを素材にする（設計書 §4.4 ③）。
_STRUCTURAL_ROLE_TEMPLATES: dict[str, str] = {
    GROUP_STAGE: "{label}の段階に位置づけられる",
    GROUP_THESIS: "{label}を支える",
    GROUP_CLAIM: "{label}を裏づける",
}
_STRUCTURAL_ROLE_GROUP_ORDER = (GROUP_STAGE, GROUP_THESIS, GROUP_CLAIM)


def _structural_role(upper_items: list[dict[str, Any]]) -> tuple[str | None, str]:
    """上位項目から「この文脈での役割」の事実文を合成する（設計書 §4.4 ③）。

    素材は ``group ∈ {stage, thesis, claim}`` かつ ``unresolved=False`` かつ
    ``relation_status='source_backed'`` の項目に限る（AI 候補・解決失敗ラベルを
    役割文にしない）。該当が無ければ ``(None, unidentified)``。
    """
    for group in _STRUCTURAL_ROLE_GROUP_ORDER:
        for item in upper_items:
            if item.get("group") != group or item.get("unresolved"):
                continue
            if item.get("relation_status") != CONTEXT_STATUS_SOURCE_BACKED:
                continue
            label = str(item.get("label") or "").strip()
            if not label or labels_mod.is_internal_id_like(label):
                continue
            return _STRUCTURAL_ROLE_TEMPLATES[group].format(label=label), CONTEXT_STATUS_SOURCE_BACKED
    return None, CONTEXT_ROLE_STATUS_UNIDENTIFIED


def _derive_contextual_role(
    upper_items: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    *,
    fallback_role: str | None = None,
    fallback_status: str | None = None,
) -> tuple[str | None, str, str]:
    """focus.contextual_role / contextual_role_status / contextual_role_source を
    導出する（設計書 §2.2/§3.1 → 提示再設計 §4.4）。

    優先順位:
      1. 人間が確定した注釈（committed）
      2. 要素が**自己記述**する役割（``fallback_role``。equation は
         ``role_in_argument`` 訳 + 参加 stage 訳の決定論合成、component は
         ``narrative_role`` / ``thesis_context.role_in_thesis``）
      3. 構造要約（``_structural_role``。位置づけを語れる区画からのみ合成）
      4. unidentified（推測で穴埋めしない。学習者射影はこの場合だけ役割を落とす）
    """
    committed_text, committed_status = _committed_contextual_role(annotations)
    if committed_text:
        return committed_text, committed_status, ROLE_SOURCE_COMMITTED
    fallback_text = str(fallback_role or "").strip()
    if fallback_text:
        return (
            fallback_text,
            (fallback_status or CONTEXT_STATUS_SOURCE_BACKED),
            ROLE_SOURCE_SELF_DESCRIBED,
        )
    text, status = _structural_role(upper_items)
    if text:
        return text, status, ROLE_SOURCE_STRUCTURAL
    return None, CONTEXT_ROLE_STATUS_UNIDENTIFIED, ROLE_SOURCE_UNIDENTIFIED


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


def _equation_label(
    record: dict[str, Any] | None, *, symbols: list[dict[str, Any]] | None = None
) -> "labels_mod.Label":
    """数式のラベルラダー（§5.1）。**生 TeX・内部 ID をラベル素材にしない**。

    旧実装は ``plain_text → latex → …`` の 80 字機械切りだったため、読み下しの無い
    式では必ず「コマンド途中で切れた生 TeX」がラベルになっていた（RC2）。正本は
    ``core/deliberation/labels.py`` の ``equation_label``。
    """
    return labels_mod.equation_label(record, symbols=symbols)


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
        items.append(
            _item(
                "thesis", None, document_id, headline, "supports_thesis",
                _status_for_link("explicit"), group=GROUP_THESIS,
            )
        )
    if _claim_in_ids(thesis.get("supporting_subclaim_ids"), claim_lookup, element_id):
        items.append(
            _item(
                "thesis", None, document_id, headline, "supports_thesis",
                _status_for_link("explicit"), group=GROUP_THESIS,
            )
        )
    support = thesis.get("support_structure") if isinstance(thesis.get("support_structure"), dict) else {}
    for section_name, entries in support.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and _claim_in_ids(entry.get("claim_ids"), claim_lookup, element_id):
                label = _SUPPORT_SECTION_LABELS.get(str(section_name), str(section_name))
                items.append(
                    _item(
                        "thesis", None, document_id, f"支持構造「{label}」", "supports_thesis",
                        _status_for_link("explicit"), group=GROUP_THESIS,
                    )
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
                    _item(
                        "thesis", None, document_id, f"支持構造「{label}」", "supports_thesis",
                        _status_for_link("explicit"), group=GROUP_THESIS,
                    )
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
            items.append(
                _item(
                    "thesis", None, document_id, headline, "supports_thesis", status,
                    evidence_refs=[node_id], group=GROUP_THESIS,
                )
            )
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
                snippet = excerpt(entry.get("text"), _LABEL_LIMIT)
                if snippet:
                    label = f"{label}: {snippet}"
            items.append(
                _item(
                    "thesis", None, document_id, label, "supports_thesis", status,
                    evidence_refs=[node_id], group=GROUP_THESIS,
                )
            )
            continue
        # 未知の node id 形式でも落とさず、推測せずそのまま残す（P4）。素の参照は
        # evidence_refs（教員の識別子欄）にも入る。
        items.append(
            _item(
                "thesis", None, document_id, node_id, "supports_thesis", status,
                evidence_refs=[node_id], group=GROUP_THESIS,
            )
        )
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
                _item(
                    "section", None, document_id, label, "appears_in_section",
                    _status_for_link("explicit"), group=GROUP_SECTION,
                )
            )
    return items


def _chain_role_keys(chain: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    """chain 本体 + 各 step の指定キーに現れる id 集合（純粋関数）。"""
    out: set[str] = set()
    sources = [chain] + [s for s in (chain.get("steps") or []) if isinstance(s, dict)]
    for source in sources:
        for key in keys:
            for raw in source.get(key) or []:
                value = str(raw or "").strip()
                if value:
                    out.add(value)
    return out


def _matching_step_ids(
    chain: dict[str, Any], keys: tuple[str, ...], member_check: Callable[[str], bool]
) -> list[str]:
    """対象が現れる step_id を**全件**返す（``break`` で最初の1件に潰さない・RC3）。"""
    hits: list[str] = []
    for step in chain.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for key in keys:
            if any(member_check(str(x)) for x in (step.get(key) or [])):
                step_id = str(step.get("step_id") or "").strip()
                if step_id and step_id not in hits:
                    hits.append(step_id)
                break
    return hits


_EQUATION_INPUT_KEYS = ("input_equation_ids",)
_EQUATION_OUTPUT_KEYS = ("output_equation_ids",)
_EQUATION_INTERMEDIATE_KEYS = ("intermediate_equation_ids",)
_CLAIM_CHAIN_KEYS = (
    "input_claim_ids", "output_claim_ids", "required_claim_ids", "assumption_ids",
)
_EQUATION_CHAIN_KEYS = (
    "input_equation_ids", "output_equation_ids", "intermediate_equation_ids",
)

# 中間量であることの区別材料（sublabel の先頭に添える事実文）。
_INTERMEDIATE_NOTE = "中間量として"


def _derivation_membership_facts(
    chains: list[dict[str, Any]],
    document_id: str | None,
    member_check: Callable[[str], bool],
    *,
    kind: str = "claim",
) -> list[dict[str, Any]]:
    """対象（claim または equation の id）が derivation_chain artifact のどこに
    現れるかの事実項目。``member_check`` は生の id 文字列を受け取り一致判定する
    （claim は翻訳込みの判定を呼び出し側が渡す）。

    ``kind="equation"`` のときは**向きを分けて判定する**（設計書 §5.2 / CP2）:
    入力 → ``feeds_derivation``（group=derivation_out）/ 出力 →
    ``produced_by_derivation``（group=derivation_in）/ 入力かつ出力・中間量 →
    ``used_in_derivation``（sublabel に「中間量として」）/ 向きが判らないときは
    向きを主張せず ``used_in_derivation``。旧実装は入出力・前提を1リストに混ぜて
    存在判定していたため、導出の向きが構造的に失われていた（RC3）。

    ``element_id`` には chain の ``derivation_id`` を入れる（設計書 §16 で derivation が
    ``refs.py`` の解決対象になったため、この項目から導出そのものを中心に据えられる）。
    step まで特定できた場合も element_id は chain 単位のまま（step は独立の要素にしない）で、
    step_id は ``evidence_refs`` に残す（内部 ID をラベルに出さない = EC3′）。
    ``derivation_id`` が空の chain は従来どおり非ナビゲーション。
    """
    is_equation = kind == "equation"
    scan_keys = _EQUATION_CHAIN_KEYS if is_equation else _CLAIM_CHAIN_KEYS
    items: list[dict[str, Any]] = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        if not any(member_check(x) for x in _chain_role_keys(chain, scan_keys)):
            continue
        derivation_id = str(chain.get("derivation_id") or "")
        nav_id = derivation_id or None
        step_ids = _matching_step_ids(chain, scan_keys, member_check)
        evidence_refs = ([derivation_id] if derivation_id else []) + step_ids

        relation = "belongs_to_derivation"
        group = GROUP_DERIVATION_OUT
        note = ""
        if is_equation:
            in_inputs = any(member_check(x) for x in _chain_role_keys(chain, _EQUATION_INPUT_KEYS))
            in_outputs = any(member_check(x) for x in _chain_role_keys(chain, _EQUATION_OUTPUT_KEYS))
            in_middle = any(
                member_check(x) for x in _chain_role_keys(chain, _EQUATION_INTERMEDIATE_KEYS)
            )
            if in_middle or (in_inputs and in_outputs):
                relation, group, note = "used_in_derivation", GROUP_DERIVATION_OUT, _INTERMEDIATE_NOTE
            elif in_inputs:
                relation, group = "feeds_derivation", GROUP_DERIVATION_OUT
            elif in_outputs:
                relation, group = "produced_by_derivation", GROUP_DERIVATION_IN
            else:
                relation, group = "used_in_derivation", GROUP_DERIVATION_OUT

        label = labels_mod.derivation_label(chain)
        sublabel = labels_mod.derivation_operation_summary(chain, translate=True)
        conditions = [str(c).strip() for c in (chain.get("conditions") or []) if str(c or "").strip()]
        parts = [p for p in (note, ("操作: " + sublabel) if sublabel else "") if p]
        if conditions:
            condition = excerpt(first_sentence(conditions[0]), _SUBLABEL_LIMIT)
            if condition:
                parts.append("条件: " + condition)
        items.append(
            _item(
                "derivation", nav_id, document_id, label.text, relation,
                _status_for_link("explicit"), evidence_refs=evidence_refs,
                sublabel=" ／ ".join(parts) or label.sublabel,
                qualifier=label.qualifier,
                group=group,
                unresolved=label.unresolved,
                label_source=label.label_source,
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
        # 英語 stage 表示名（"Equation system"）ではなく訳語を出す（CP4 / RC10）。
        label = labels_mod.graph_node_label(node)
        items.append(
            _item_from_label(
                "stage", None, document_id, label, "participates_in_stage",
                _status_for_link("inferred"), evidence_refs=sorted(overlap)[:3],
                group=GROUP_STAGE,
            )
        )
    return items


# ---------------------------------------------------------------------------
# 数式レンズの下請け（設計書 §5.1 / §5.3 / §5.4。すべて純粋関数）
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")

# graph_layer の語彙（component_graph/schema.py と同じ3層）。
_GRAPH_LAYER_MAIN = "main"
_GRAPH_LAYER_EQUATION_DETAIL = "equation_detail"
_GRAPH_LAYER_DEBUG = "debug"

# 事実文（CP10「空は沈黙ではない」）。
_FACT_NEEDS_MATH_REVIEW = "この式の数式表現は自動抽出の検証が必要な状態です。"
_FACT_GRAPH_NODE_REVIEW = "この式に対応する理論グラフのノードに要確認の指摘があります。"
_FACT_DEFINITION_MISSING = "論文中に明示的な定義が見つからない記号があります。"
_NOTE_DEBUG_NODES = "確定していない補完ノードが {count} 件ありますが、文脈には表示していません"


def _is_source_language(text: Any) -> bool:
    """自由文が原文（＝日本語ではない）言語かどうかの粗い判定。

    「（論文の原文）」の出所注記を出すかの判断にだけ使う（§2.4）。表示パスに
    翻訳を入れないため、判定を外しても意味は失われない（注記の有無が変わるだけ）。
    """
    body = str(text or "").strip()
    return bool(body) and not _CJK_RE.search(body)


def _equation_symbol_records(
    symbol_records: list[dict[str, Any]], equation_id: str
) -> list[tuple[dict[str, Any], bool]]:
    """この式に関係する記号レコードを ``(record, defined_by_focus)`` で返す（§5.4）。

    defining（この式が定義する）と used（使うだけ）を**分けて**返すのが要点。
    旧実装は or で合流させて一律「の記号を用いる」にしていた（RC5）。
    """
    out: list[tuple[dict[str, Any], bool]] = []
    for record in symbol_records:
        if not isinstance(record, dict):
            continue
        defining = [str(x) for x in (record.get("defining_equation_ids") or [])]
        used = [str(x) for x in (record.get("used_in_equation_ids") or [])]
        if equation_id in defining:
            out.append((record, True))
        elif equation_id in used:
            out.append((record, False))
    return out


def _symbol_items(
    symbol_records: list[dict[str, Any]], equation_id: str, document_id: str | None
) -> list[dict[str, Any]]:
    """記号 ITEM（label=記号そのもの / sublabel=意味）。

    意味を ``evidence_refs`` に押し込まない（RC5 の根治）。逐語は evidence_refs にも
    残すが、読み手に届くのは ``sublabel`` である。
    """
    items: list[dict[str, Any]] = []
    for record, defined_here in _equation_symbol_records(symbol_records, equation_id):
        label = labels_mod.symbol_label(record, defined_by_focus=defined_here)
        if label.unresolved and not label.text:
            continue
        evidences = [
            str(e).strip()
            for e in (record.get("definition_evidence_texts") or [])
            if str(e or "").strip()
        ]
        items.append(
            _item_from_label(
                "symbol", None, document_id, label,
                "defines_symbol" if defined_here else "uses_symbol",
                _status_for_link("explicit"),
                evidence_refs=evidences[:1],
                group=GROUP_SYMBOL_DEFINED if defined_here else GROUP_SYMBOL_USED,
            )
        )
    return items


def _equation_symbol_entries(
    symbol_records: list[dict[str, Any]], semantics: dict[str, Any], equation_id: str
) -> list[dict[str, Any]]:
    """focus.intrinsic.symbols（記号 + 意味 + この式で定義されるか）。"""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record, defined_here in _equation_symbol_records(symbol_records, equation_id):
        label = labels_mod.symbol_label(record)
        symbol = label.text
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        entries.append(
            {
                "symbol": symbol,
                "meaning": label.sublabel,
                "defined_here": bool(defined_here),
                "definition_status": label.qualifier,
            }
        )
    if entries:
        return entries[:8]
    # SymbolRegistry が無い旧 run は equation_semantics の defined_symbols へ縮退。
    for raw in semantics.get("defined_symbols") or []:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        meaning = str(raw.get("meaning") or "").strip() or first_sentence(raw.get("evidence_text"))
        entries.append(
            {
                "symbol": symbol,
                "meaning": excerpt(meaning, _SUBLABEL_LIMIT),
                "defined_here": True,
                "definition_status": str(raw.get("definition_status") or ""),
            }
        )
    return entries[:8]


def _graph_node_items_for_equation(
    nodes: list[dict[str, Any]],
    equation_id: str,
    document_id: str | None,
    *,
    component_lookup: dict[str, str],
    equation_labeler: Callable[[str], Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """TheoryOperationGraph のノードを ``graph_layer`` で3分岐して ITEM 化する（CP3）。

    戻り値は ``(理論段階の項目, 式の詳細層の項目, debug 層の件数)``。

    * ``main`` — 理論段階。``element_type='stage'`` / ``element_id=None`` /
      navigable=False が正（stage は refs の解決対象ではない）。label は stage キーの
      **訳語**、sublabel は ``node.description`` の第1文（#308 が「長い説明はここへ」と
      定めた欄の初回利用）。
    * ``equation_detail`` — traceability 用の式単位ステップ。``Define eq_tex_b16`` 形の
      ID ラベルは採用しない。navigable は **theory_components に解決できるときだけ**
      True（CP8 fail-closed。開けない「深く検討」導線を作らない）。
    * ``debug`` — レーンに出さない。件数だけ事実文で返す（P4）。
    """
    stage_items: list[dict[str, Any]] = []
    detail_items: list[dict[str, Any]] = []
    debug_count = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if equation_id not in [str(x) for x in (node.get("linked_equation_ids") or [])]:
            continue
        layer = str(node.get("graph_layer") or _GRAPH_LAYER_MAIN)
        status = _status_for_link(_link_kind_from_backing_status(node.get("source_backing_status")))
        label = labels_mod.graph_node_label(node, equation_labeler=equation_labeler)
        node_id = str(node.get("id") or "").strip()
        if layer == _GRAPH_LAYER_DEBUG:
            debug_count += 1
            continue
        if layer == _GRAPH_LAYER_MAIN:
            stage_items.append(
                _item_from_label(
                    "stage", None, document_id, label, "belongs_to_stage", status,
                    evidence_refs=[node_id] if node_id else [],
                    group=GROUP_STAGE,
                )
            )
            continue
        resolved_id = component_lookup.get(node_id) if node_id else None
        detail_items.append(
            _item_from_label(
                ELEMENT_THEORY_COMPONENT, resolved_id, document_id, label,
                "used_in_operation_step", status,
                evidence_refs=[node_id] if node_id else [],
                group=GROUP_OPERATION,
                qualifier=_GRAPH_LAYER_EQUATION_DETAIL,
            )
        )
    return stage_items, detail_items, debug_count


def _main_stage_for_equation(
    nodes: list[dict[str, Any]], equation_id: str
) -> tuple[str, str]:
    """この式が属する理論段階の ``(stage キー, 説明文)``（無ければ ``("", "")``）。"""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("graph_layer") or _GRAPH_LAYER_MAIN) != _GRAPH_LAYER_MAIN:
            continue
        if equation_id not in [str(x) for x in (node.get("linked_equation_ids") or [])]:
            continue
        key = theory_stage_key(node.get("label")) or theory_stage_key(node.get("visual_label"))
        description = excerpt(first_sentence(node.get("description")), _SUBLABEL_LIMIT)
        if key:
            return key, description
    return "", ""


def _equation_focus_role(chain: dict[str, Any], equation_id: str) -> str:
    """導出における中心式の役割（input / output / intermediate / unspecified）。"""
    inputs = _chain_role_keys(chain, _EQUATION_INPUT_KEYS)
    outputs = _chain_role_keys(chain, _EQUATION_OUTPUT_KEYS)
    middles = _chain_role_keys(chain, _EQUATION_INTERMEDIATE_KEYS)
    if equation_id in middles or (equation_id in inputs and equation_id in outputs):
        return "intermediate"
    if equation_id in inputs:
        return "input"
    if equation_id in outputs:
        return "output"
    return "unspecified"


def _equation_mini(
    eq_index: dict[str, dict[str, Any]], equation_id: str
) -> dict[str, Any]:
    """derivations[] の入出力に載せる最小の式参照（MINI。§4.2）。"""
    record = eq_index.get(equation_id)
    label = _equation_label(record)
    return {
        "element_type": ELEMENT_EQUATION,
        "element_id": equation_id if record else None,
        "label": label.text,
        "navigable": bool(record),
    }


def _first_step_reason(chain: dict[str, Any]) -> str:
    """step の ``reason`` のうち最初の可読な1文（内部 ID を含むものは採らない）。"""
    for step in chain.get("steps") or []:
        if not isinstance(step, dict):
            continue
        reason = first_sentence(step.get("reason"))
        if reason and not labels_mod.is_internal_id_like(reason):
            cut = excerpt(reason, _SUBLABEL_LIMIT)
            if cut and not labels_mod.is_internal_id_like(cut):
                return cut
    return ""


def _equation_derivation_stories(
    chains: list[dict[str, Any]],
    equation_id: str,
    eq_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """equation focus の「入力 →[操作]→ 出力 + この式の役割」カード（§4.4 derivations[]）。

    ITEM の向き付き関係語だけでも区別可能性は達成されるが、読み手の3つ目の問い
    （どこから・どの操作で導かれ、どこへつながるか）に一望で答えるための構造化ブロック。
    """
    stories: list[dict[str, Any]] = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        if equation_id not in _chain_role_keys(chain, _EQUATION_CHAIN_KEYS):
            continue
        label = labels_mod.derivation_label(chain)
        derivation_id = str(chain.get("derivation_id") or "").strip()
        inputs = sorted(_chain_role_keys(chain, _EQUATION_INPUT_KEYS))
        outputs = sorted(_chain_role_keys(chain, _EQUATION_OUTPUT_KEYS))
        stories.append(
            {
                "derivation_id": derivation_id,
                "label": label.text,
                "sublabel": label.sublabel,
                "chain_type": str(chain.get("chain_type") or ""),
                "operation_text": labels_mod.derivation_operation_summary(chain, translate=True),
                "focus_role": _equation_focus_role(chain, equation_id),
                "inputs": [_equation_mini(eq_index, eid) for eid in inputs],
                "outputs": [_equation_mini(eq_index, eid) for eid in outputs],
                "eliminated_symbols": [
                    str(s) for s in (chain.get("eliminated_symbols") or []) if str(s or "").strip()
                ],
                "retained_symbols": [
                    str(s) for s in (chain.get("retained_symbols") or []) if str(s or "").strip()
                ],
                "reason": _first_step_reason(chain),
                "relation_status": _status_for_link("explicit"),
                "evidence_refs": _evidence_ids_of_chain(chain),
                "navigable": bool(derivation_id),
            }
        )
    return stories


def _evidence_record_for(artifacts: dict[str, Any], evidence_id: str) -> dict[str, Any] | None:
    """evidence_registry artifact から1件の evidence レコードを引く（純粋関数・§16）。"""
    for record in _list(artifacts.get("evidence_registry"), "records"):
        if str(record.get("evidence_id") or "") == str(evidence_id):
            return record
    return None


def _derivation_chain_for(
    chains: list[dict[str, Any]], derivation_id: str
) -> dict[str, Any] | None:
    """derivation_chain artifact から1本の chain を引く（純粋関数・§16）。"""
    for chain in chains:
        if isinstance(chain, dict) and str(chain.get("derivation_id") or "") == str(derivation_id):
            return chain
    return None


def _derivation_identifier_label(chain: dict[str, Any]) -> str:
    """導出の**識別子つき**表示ラベル（純粋関数）。

    これは「どの導出か」を教員がトレースするための識別子表示であり、**ITEM の
    ラベルには使わない**（内部 ID をラベルに出さない = EC3′。ITEM は
    ``labels.derivation_label`` の可読ラダーを使う）。derivation を中心に据えたときの
    focus 見出しだけがこれを使う（中心要素は「どれを開いているか」の同定が第一で、
    識別子の表示が必要なため）。
    """
    derivation_id = str((chain or {}).get("derivation_id") or "").strip()
    operation = str((chain or {}).get("operation") or "").strip()
    if not operation:
        operation = str((chain or {}).get("operation_family") or "").strip()
    if derivation_id and operation:
        return f"導出「{derivation_id}」({operation})"
    return f"導出「{derivation_id}」" if derivation_id else "導出"


def _derivation_operation_summary(chain: dict[str, Any]) -> str:
    """chain の step 操作列を「op1 → op2 → …」に連結する（純粋関数・非LLM）。

    正本は ``labels.derivation_operation_summary``（訳語を当てるかは引数で選ぶ）。
    ここは artifact の生キーのまま返す既存呼び出し元のための薄い委譲。
    """
    return labels_mod.derivation_operation_summary(chain)


def _evidence_ids_of_chain(chain: dict[str, Any]) -> list[str]:
    """chain 本体 + 各 step の ``source_evidence_ids`` を重複なく集める（純粋関数）。"""
    out: list[str] = []
    seen: set[str] = set()

    def _add(values: Any) -> None:
        for raw in values or []:
            key = str(raw or "").strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)

    _add((chain or {}).get("source_evidence_ids"))
    for step in (chain or {}).get("steps") or []:
        if isinstance(step, dict):
            _add(step.get("source_evidence_ids"))
    return out


def _chain_ids_of(chain: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    """chain 本体 + 各 step の指定キーの ID を重複なく集める（純粋関数）。"""
    out: list[str] = []
    seen: set[str] = set()
    for source in [chain or {}] + [s for s in (chain or {}).get("steps") or [] if isinstance(s, dict)]:
        for key in keys:
            for raw in source.get(key) or []:
                value = str(raw or "").strip()
                if value and value not in seen:
                    seen.add(value)
                    out.append(value)
    return out


def _chains_referencing_evidence(
    chains: list[dict[str, Any]], document_id: str | None, evidence_id: str
) -> list[dict[str, Any]]:
    """当該 evidence を根拠に挙げている導出の上位項目（純粋関数・§16）。"""
    items: list[dict[str, Any]] = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        if str(evidence_id) not in _evidence_ids_of_chain(chain):
            continue
        derivation_id = str(chain.get("derivation_id") or "")
        items.append(
            _item_from_label(
                "derivation", derivation_id or None, document_id,
                labels_mod.derivation_label(chain),
                "belongs_to_derivation", _status_for_link("explicit"),
                evidence_refs=[derivation_id] if derivation_id else [],
                group=GROUP_DERIVATION_OUT,
            )
        )
    return items


def _claim_ids_resting_on_evidence(
    claim_objects: list[dict[str, Any]], evidence_id: str
) -> list[str]:
    """``source_evidence_ids`` に当該 evidence を含む claim の生 ID（純粋関数・§16）。"""
    out: list[str] = []
    for claim in claim_objects:
        if not isinstance(claim, dict):
            continue
        ids = [str(x or "").strip() for x in (claim.get("source_evidence_ids") or [])]
        if str(evidence_id) in ids:
            raw_id = str(claim.get("claim_id") or "").strip()
            if raw_id and raw_id not in out:
                out.append(raw_id)
    return out


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
        upper.append(
            _item(
                "section", None, document_id, section_label, "appears_in_section",
                _status_for_link("explicit"), group=GROUP_SECTION,
            )
        )

    def _claim_row_for(db_id: str | None) -> dict[str, Any] | None:
        if not db_id:
            return None
        return _safe(lambda: _claims_by_id([db_id]), {}).get(db_id)

    def _related_claim_item(raw_id: Any, relation: str, group: str, status: str) -> dict[str, Any]:
        """関連 claim の ITEM（label=本文の抜粋 / sublabel=種別 + 逐語根拠）。"""
        raw = str(raw_id or "")
        db_id = claim_lookup.get(raw)
        row_data = _claim_row_for(db_id) or {"text": raw}
        return _item_from_label(
            "theory_claim", db_id, document_id, labels_mod.claim_label(row_data),
            relation, status, evidence_refs=[raw] if raw and not db_id else [], group=group,
        )

    if claim_obj and claim_obj.get("parent_claim_id"):
        upper.append(
            _related_claim_item(
                claim_obj.get("parent_claim_id"), "subclaim_of", GROUP_CLAIM,
                _status_for_link("explicit"),
            )
        )

    for comp in _safe(lambda: _components_supporting_claim(document_id, ref.element_id), []):
        upper.append(
            _item_from_label(
                "theory_component", comp.get("id"), document_id, labels_mod.component_label(comp),
                "supports_component", _status_for_link("explicit"), group=GROUP_COMPONENT,
            )
        )

    if claim_obj:
        for sub_raw in claim_obj.get("subclaim_ids") or []:
            lower.append(
                _related_claim_item(sub_raw, "has_subclaim", GROUP_CLAIM, _status_for_link("explicit"))
            )

        eq_index = _equation_by_id(refs_mod.equation_records(document_id, artifacts=artifacts))
        symbol_records = _list(artifacts.get("symbol_registry"), "records")
        for eq_id, status in [
            (eq_id, _status_for_link("explicit")) for eq_id in claim_obj.get("equation_ids") or []
        ] + [
            (eq_id, _status_for_link("inferred"))
            for eq_id in claim_obj.get("inferred_equation_ids") or []
        ]:
            eq_record = eq_index.get(str(eq_id))
            lower.append(
                _item_from_label(
                    "equation", str(eq_id) if eq_record else None, document_id,
                    _equation_label(eq_record, symbols=symbol_records), "quantified_by", status,
                    evidence_refs=[str(eq_id)], group=GROUP_EQUATION_DOWN,
                )
            )

        for fig_id in claim_obj.get("figure_ids") or []:
            caption = fig_caption_by_id.get(str(fig_id)) or ""
            lower.append(
                _item_from_label(
                    "figure", str(fig_id), document_id,
                    labels_mod.figure_label({"caption_text": caption}),
                    "evidenced_by_figure", _status_for_link("explicit"),
                    evidence_refs=[str(fig_id)], group=GROUP_FIGURE,
                )
            )

        for evidence_id in claim_obj.get("source_evidence_ids") or []:
            # §16: evidence は解決対象になったので element_id を入れて中心に据えられる
            # ようにする（引用が artifact に見つからない ID も落とさず表示は残す）。
            # §5.6: 逐語そのものをラベルにする（「本文の根拠箇所」への置換が要らなくなる）。
            evidence_record = _evidence_record_for(artifacts, str(evidence_id)) or {}
            lower.append(
                _item_from_label(
                    "evidence", str(evidence_id) or None, document_id,
                    labels_mod.evidence_label(evidence_record, section_label=section_label),
                    "rests_on_evidence",
                    _status_for_link("explicit"), evidence_refs=[str(evidence_id)],
                    group=GROUP_EVIDENCE,
                )
            )

    for fig in fig_records:
        linked = [str(x) for x in (fig.get("linked_claim_ids") or [])]
        if any(claim_lookup.get(cid) == ref.element_id for cid in linked):
            fig_id = str(fig.get("figure_id") or "")
            if fig_id:
                lower.append(
                    _item_from_label(
                        "figure", fig_id, document_id,
                        labels_mod.figure_label({"caption_text": fig.get("caption")}),
                        "evidenced_by_figure", _status_for_link("explicit"),
                        evidence_refs=[fig_id], group=GROUP_FIGURE,
                    )
                )

    chains = _list(artifacts.get("derivation_chain"), "chains")
    lower.extend(
        _derivation_membership_facts(
            chains, document_id, lambda raw: claim_lookup.get(raw) == ref.element_id, kind="claim"
        )
    )

    upper = _cap_lane(_dedupe_items(upper), notes, "上位構造")
    lower = _cap_lane(_dedupe_items(lower), notes, "下位構造")

    annotations = _annotations_for(ELEMENT_THEORY_CLAIM, ref.element_id, document_id)
    role_text, role_status, role_source = _derive_contextual_role(upper, annotations)

    provenance = [f"theory_claims:{ref.element_id}"]
    span_id = row["source_scope"].get("span_id")
    if span_id:
        provenance.append(f"span:{span_id}")
    if claim_obj:
        provenance.append("claim_object_builder")

    headline = labels_mod.claim_label(row)
    body = str(row.get("text") or row.get("normalized_text") or "")
    focus = {
        "element_type": ELEMENT_THEORY_CLAIM,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": headline.text,
        "headline": headline.text,
        "intrinsic_summary": "" if body == headline.text else body,
        "intrinsic": {
            "kind_key": str(row.get("claim_type") or ""),
            "summary": body,
            "summary_is_source_language": _is_source_language(body),
            "facts": [],
        },
        "placement": {
            "section_label": section_label,
            "stage": None,
            "thesis_role": next(
                (i["label"] for i in upper if i["element_type"] == "thesis"), ""
            ),
        },
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "contextual_role_source": role_source,
        "review_notes": [],
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
    source_extraction = (
        record.get("source_extraction") if isinstance(record.get("source_extraction"), dict) else {}
    )
    reconstruction = (
        record.get("reconstruction") if isinstance(record.get("reconstruction"), dict) else {}
    )
    symbol_records = _list(artifacts.get("symbol_registry"), "records")

    upper: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []

    def _claim_item(raw_id: Any, relation: str, status: str, group: str) -> dict[str, Any]:
        raw = str(raw_id or "")
        db_id = claim_lookup.get(raw)
        claim_row: dict[str, Any] | None = None
        if db_id:
            claim_row = _safe(lambda: _claims_by_id([db_id]), {}).get(db_id)
        return _item_from_label(
            "theory_claim", db_id, document_id,
            labels_mod.claim_label(claim_row or {"text": raw}), relation, status,
            evidence_refs=[raw] if raw and not claim_row else [], group=group,
        )

    def _equation_item_label(equation_id: str) -> "labels_mod.Label":
        return _equation_label(eq_index.get(str(equation_id)), symbols=symbol_records)

    for cid in sem.get("linked_claim_ids") or []:
        upper.append(_claim_item(cid, "quantifies", _status_for_link("explicit"), GROUP_CLAIM))
    for cid in sem.get("inferred_claim_ids") or []:
        upper.append(_claim_item(cid, "quantifies", _status_for_link("inferred"), GROUP_CLAIM))

    thesis = artifacts.get("thesis_reconstruction")
    thesis_items: list[dict[str, Any]] = []
    if isinstance(thesis, dict):
        thesis_items = _thesis_upper_items_for_equation(thesis, ref.element_id, document_id)
        upper.extend(thesis_items)
    elif "thesis_reconstruction" not in artifacts:
        notes.append("thesis_reconstruction artifact が無いため中心命題との関係を判定できません")

    graph = _safe(lambda: _load_component_graph(document_id), {"nodes": [], "edges": []})
    if not graph.get("nodes") and "component_graph" not in artifacts:
        notes.append("component_graph が保存されていないため、理論コンポーネントとの関係を判定できません")
    # CP8: equation_detail ノードの navigable は theory_components に解決できるときだけ。
    component_lookup = _safe(lambda: _component_id_lookup(document_id), {})
    stage_items, detail_items, debug_count = _graph_node_items_for_equation(
        graph.get("nodes", []), ref.element_id, document_id,
        component_lookup=component_lookup,
        equation_labeler=lambda eq_id: _equation_item_label(eq_id),
    )
    upper.extend(stage_items)
    upper.extend(detail_items)
    if debug_count:
        # CP3 / P4: debug 層はレーンに出さないが、あったことは事実として残す。
        notes.append(_NOTE_DEBUG_NODES.format(count=debug_count))

    chains = _list(artifacts.get("derivation_chain"), "chains")
    for item in _derivation_membership_facts(
        chains, document_id, lambda raw: raw == ref.element_id, kind="equation"
    ):
        # 入力側（この式が使われる先）は上位、出力側（この式を生む導出）は下位。
        (lower if item["group"] == GROUP_DERIVATION_IN else upper).append(item)

    for out_id in sem.get("output_equation_ids") or []:
        out_record = eq_index.get(str(out_id))
        upper.append(
            _item_from_label(
                "equation", str(out_id) if out_record else None, document_id,
                _equation_item_label(out_id), "leads_to", _status_for_link("explicit"),
                evidence_refs=[str(out_id)], group=GROUP_EQUATION_DOWN,
            )
        )

    # 掲載節（RC12: artifact に実在するのに equation focus からは構造的に欠けていた）。
    source_location = (
        source_extraction.get("source_location")
        if isinstance(source_extraction.get("source_location"), dict)
        else {}
    )
    section_id = str(source_location.get("section_id") or "").strip()
    section_items = _section_items_from_ids(
        [section_id] if section_id else [], _sections_by_id(artifacts), document_id
    )
    upper.extend(section_items)
    section_label = section_items[0]["label"] if section_items else ""

    for in_id in sem.get("input_equation_ids") or []:
        in_record = eq_index.get(str(in_id))
        lower.append(
            _item_from_label(
                "equation", str(in_id) if in_record else None, document_id,
                _equation_item_label(in_id), "derives_from", _status_for_link("explicit"),
                evidence_refs=[str(in_id)], group=GROUP_EQUATION_UP,
            )
        )

    # 記号（§5.4: defines / uses を分け、意味を sublabel に載せる）。
    lower.extend(_symbol_items(symbol_records, ref.element_id, document_id))

    for chain in chains:
        if not isinstance(chain, dict):
            continue
        for step in chain.get("steps") or []:
            if not isinstance(step, dict):
                continue
            outputs = [str(x) for x in (step.get("output_equation_ids") or [])]
            if ref.element_id in outputs:
                for req_raw in step.get("required_claim_ids") or []:
                    lower.append(
                        _claim_item(
                            req_raw, "requires", _status_for_link("explicit"), GROUP_CLAIM_REQUIRED
                        )
                    )

    # 原文根拠（RC12: semantics.source_evidence_ids を逐語ラベルの ITEM にする）。
    for evidence_id in sem.get("source_evidence_ids") or []:
        evidence_record = _evidence_record_for(artifacts, str(evidence_id))
        if not evidence_record:
            continue
        lower.append(
            _item_from_label(
                "evidence", str(evidence_id), document_id,
                labels_mod.evidence_label(evidence_record, section_label=section_label),
                "rests_on_evidence", _status_for_link("explicit"),
                evidence_refs=[str(evidence_id)], group=GROUP_EVIDENCE,
            )
        )

    upper = _cap_lane(_dedupe_items(upper), notes, "上位構造")
    lower = _cap_lane(_dedupe_items(lower), notes, "下位構造")

    # ── focus v2（§4.2）──────────────────────────────────────────────────────
    stage_key, stage_description = _main_stage_for_equation(graph.get("nodes", []), ref.element_id)
    annotations = _annotations_for(ELEMENT_EQUATION, ref.element_id, document_id)
    self_described = labels_mod.self_described_role("equation", record, stage_key=stage_key)
    role_text, role_status, role_source = _derive_contextual_role(
        upper, annotations, fallback_role=self_described,
        fallback_status=CONTEXT_STATUS_SOURCE_BACKED,
    )

    headline = _equation_label(record, symbols=symbol_records)
    summary = str(sem.get("summary") or "").strip()
    needs_math_review = bool(source_extraction.get("needs_math_review"))
    reading = str(reconstruction.get("plain_text") or source_extraction.get("plain_text") or "").strip()
    if needs_math_review or looks_like_tex_math(reading):
        reading = ""

    facts: list[str] = []
    link_fact = link_status_fact(sem.get("link_status"))
    if link_fact:
        facts.append(link_fact)

    symbol_entries = _equation_symbol_entries(symbol_records, sem, ref.element_id)
    review_notes: list[str] = []
    if needs_math_review:
        review_notes.append(_FACT_NEEDS_MATH_REVIEW)
    if any(entry["definition_status"] == "definition_missing" for entry in symbol_entries):
        review_notes.append(_FACT_DEFINITION_MISSING)
    if any(
        item["relation_status"] != CONTEXT_STATUS_SOURCE_BACKED for item in (stage_items + detail_items)
    ):
        review_notes.append(_FACT_GRAPH_NODE_REVIEW)

    focus = {
        "element_type": ELEMENT_EQUATION,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": headline.text,
        "headline": headline.text,
        # CP1: 見出しと意味の一行に同じ文字列を入れない。
        "intrinsic_summary": "" if summary == headline.text else summary,
        "intrinsic": {
            "kind_key": str(sem.get("equation_type") or record.get("equation_type") or ""),
            "role_key": str(sem.get("role_in_argument") or record.get("role_in_argument") or ""),
            "summary": summary,
            "summary_is_source_language": _is_source_language(summary),
            "reading": reading,
            "symbols": symbol_entries,
            "conditions": [
                str(a).strip() for a in (sem.get("assumptions") or []) if str(a or "").strip()
            ],
            "facts": facts,
        },
        "placement": {
            "section_label": section_label,
            "stage": {"key": stage_key, "description": stage_description} if stage_key else None,
            "thesis_role": thesis_items[0]["label"] if thesis_items else "",
        },
        "derivations": _equation_derivation_stories(chains, ref.element_id, eq_index),
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "contextual_role_source": role_source,
        "review_notes": review_notes,
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

    # グラフノード・依存が参照する式の可読ラベル用（生の equation_id を出さない）。
    eq_index = _equation_by_id(
        _safe(lambda: refs_mod.equation_records(document_id, artifacts=artifacts), [])
    )
    symbol_records = _list(artifacts.get("symbol_registry"), "records")

    def _equation_item_label(equation_id: Any) -> "labels_mod.Label":
        return _equation_label(eq_index.get(str(equation_id)), symbols=symbol_records)

    upper: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []

    def _node_label(other_node: dict[str, Any] | None, raw_ref: str) -> "labels_mod.Label":
        """グラフノード由来の相手ラベル（内部 ID・英語 stage 生名をそのまま出さない）。"""
        if other_node:
            return labels_mod.graph_node_label(other_node, equation_labeler=_equation_item_label)
        return labels_mod.Label(text=raw_ref, label_source=labels_mod.LABEL_SOURCE_TEXT)

    if node:
        parent_raw = str(node.get("parent_component_id") or "").strip()
        if parent_raw:
            parent_db = component_lookup.get(parent_raw)
            parent_node = nodes_by_id.get(parent_db) if parent_db else None
            upper.append(
                _item_from_label(
                    "theory_component", parent_db if parent_node else None, document_id,
                    _node_label(parent_node, parent_raw), "member_of", _status_for_link("explicit"),
                    evidence_refs=[parent_raw], group=GROUP_COMPONENT,
                )
            )

        if node.get("is_thesis_anchor"):
            # thesis_context 由来の central_thesis 項目とラベルを揃えて重複排除させる。
            upper.append(
                _item(
                    "thesis", None, document_id, headline, "supports_thesis",
                    _status_for_link("explicit"), group=GROUP_THESIS,
                )
            )

        for member_raw in node.get("member_component_ids") or []:
            member_db = component_lookup.get(str(member_raw))
            member_node = nodes_by_id.get(member_db) if member_db else None
            lower.append(
                _item_from_label(
                    "theory_component", member_db if member_node else None, document_id,
                    _node_label(member_node, str(member_raw)), "contains",
                    _status_for_link("explicit"), evidence_refs=[str(member_raw)],
                    group=GROUP_COMPONENT,
                )
            )

        for eq_id in node.get("linked_equation_ids") or []:
            status = _status_for_link(_link_kind_from_backing_status(node.get("source_backing_status")))
            lower.append(
                _item_from_label(
                    "equation", str(eq_id), document_id, _equation_item_label(eq_id),
                    "uses_equation", status, evidence_refs=[str(eq_id)], group=GROUP_EQUATION_UP,
                )
            )

    for edge in graph.get("edges", []):
        src_id = str(edge.get("source_component_id") or "")
        dst_id = str(edge.get("target_component_id") or "")
        status = _status_for_link(_link_kind_from_backing_status(edge.get("source_backing_status")))
        edge_kind = str(edge.get("edge_type") or edge.get("relation") or "").strip().upper()
        if src_id == ref.element_id and dst_id:
            other = nodes_by_id.get(dst_id)
            relation = "requires" if edge_kind in ("REQUIRES", "DEPENDS_ON") else "relates_to_component"
            lower.append(
                _item_from_label(
                    "theory_component", dst_id if other else None, document_id,
                    _node_label(other, dst_id), relation, status, evidence_refs=[dst_id],
                    group=GROUP_COMPONENT,
                )
            )
        elif dst_id == ref.element_id and src_id:
            other = nodes_by_id.get(src_id)
            upper.append(
                _item_from_label(
                    "theory_component", src_id if other else None, document_id,
                    _node_label(other, src_id), "used_by_component", status,
                    evidence_refs=[src_id], group=GROUP_COMPONENT,
                )
            )

    for dep in row.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        for raw_ref in dep.get("component_refs") or []:
            dep_db = component_lookup.get(str(raw_ref))
            dep_node = nodes_by_id.get(dep_db) if dep_db else None
            lower.append(
                _item_from_label(
                    "theory_component", dep_db if dep_node else None, document_id,
                    _node_label(dep_node, str(raw_ref)), "requires", _status_for_link("explicit"),
                    evidence_refs=[str(raw_ref)], group=GROUP_COMPONENT,
                )
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
            lower.append(
                _item_from_label(
                    "theory_claim", db_id, document_id,
                    labels_mod.claim_label(claim_row or {"text": db_id}), "backed_by_claim",
                    _status_for_link("explicit"), group=GROUP_CLAIM,
                )
            )
            continue
        if artifact_claim_text is None:
            artifact_claim_text = _artifact_claim_text_index(_list(artifacts.get("claim_object_builder"), "claims"))
        artifact_text = artifact_claim_text.get(cid)
        if artifact_text:
            lower.append(
                _item_from_label(
                    "theory_claim", None, document_id,
                    labels_mod.claim_label({"text": artifact_text}), "backed_by_claim",
                    _status_for_link("explicit"), evidence_refs=[cid], group=GROUP_CLAIM,
                )
            )
        else:
            lower.append(
                _item(
                    "theory_claim", None, document_id, cid, "backed_by_claim",
                    _status_for_link("explicit"), group=GROUP_CLAIM,
                )
            )

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
    role_in_thesis = labels_mod.self_described_role(ELEMENT_THEORY_COMPONENT, row) or str(
        (thesis_context or {}).get("role_in_thesis") or ""
    ).strip()
    role_text, role_status, role_source = _derive_contextual_role(
        upper, annotations, fallback_role=role_in_thesis, fallback_status=CONTEXT_STATUS_SOURCE_BACKED
    )

    provenance = [f"theory_components:{ref.element_id}"]
    if node:
        provenance.append("component_graph")
    if thesis_context_items or role_in_thesis:
        provenance.append("thesis_context")
    if section_items:
        provenance.append("chunks")

    headline = labels_mod.component_label(row)
    summary = str(row.get("summary") or "")
    focus = {
        "element_type": ELEMENT_THEORY_COMPONENT,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": headline.text,
        "headline": headline.text,
        "intrinsic_summary": "" if summary == headline.text else summary,
        "intrinsic": {
            "kind_key": str(row.get("component_type") or ""),
            "summary": summary,
            "summary_is_source_language": _is_source_language(summary),
            "facts": [],
        },
        "placement": {
            "section_label": section_items[0]["label"] if section_items else "",
            "stage": None,
            "thesis_role": next((i["label"] for i in upper if i["element_type"] == "thesis"), ""),
        },
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "contextual_role_source": role_source,
        "review_notes": [],
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
            upper.append(
                _item_from_label(
                    "theory_claim", claim_db, document_id,
                    labels_mod.claim_label(claim_row or {"text": str(cid)}),
                    "provides_evidence_for", _status_for_link("explicit"),
                    evidence_refs=[str(cid)] if not claim_row else [], group=GROUP_CLAIM,
                )
            )
            if claim_db:
                linked_claim_db_ids.append(claim_db)

        for comp_raw in matched_record.get("linked_component_candidates") or []:
            comp_db = component_lookup.get(str(comp_raw))
            comp_row = None
            if comp_db:
                comp_row = _safe(lambda: _components_by_id([comp_db]), {}).get(comp_db)
            upper.append(
                _item_from_label(
                    "theory_component", comp_db, document_id,
                    labels_mod.component_label(comp_row or {"name": str(comp_raw)}),
                    "related_component_candidate", _status_for_link("inferred"),
                    evidence_refs=[str(comp_raw)] if not comp_row else [], group=GROUP_COMPONENT,
                )
            )

    if linked_claim_db_ids:
        linked_claim_id_set = set(linked_claim_db_ids)
        for comp in _safe(lambda: _load_components_with_evidence_claims(document_id), []):
            evidence_claims = {str(x) for x in (comp.get("evidence_claims") or [])}
            if linked_claim_id_set & evidence_claims:
                upper.append(
                    _item_from_label(
                        "theory_component", comp.get("id"), document_id,
                        labels_mod.component_label(comp), "related_component_candidate",
                        _status_for_link("inferred"), group=GROUP_COMPONENT,
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
                        group=GROUP_THESIS,
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
    role_text, role_status, role_source = _derive_contextual_role(upper, annotations)

    provenance = [f"document_figures:{ref.element_id}"]
    if matched_record:
        provenance.append("figure_table_semantics")
    if matched_apparatus:
        provenance.append("apparatus_semantics")

    headline = labels_mod.figure_label(
        dict(fig, effective_mode=presentation.get("effective_mode")),
        part_labels=[i["label"] for i in lower if i["element_type"] == "part"],
    )
    caption = str(fig.get("caption_text") or "")
    focus = {
        "element_type": ELEMENT_FIGURE,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": headline.text,
        "headline": headline.text,
        "intrinsic_summary": "" if caption == headline.text else caption,
        "intrinsic": {
            "kind_key": str(presentation.get("effective_mode") or ""),
            "summary": caption,
            "summary_is_source_language": _is_source_language(caption),
            "facts": [],
        },
        "placement": {
            "section_label": next(
                (i["label"] for i in upper if i["element_type"] == "section"), ""
            ),
            "stage": None,
            "thesis_role": next((i["label"] for i in upper if i["element_type"] == "thesis"), ""),
        },
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "contextual_role_source": role_source,
        "review_notes": [],
        "provenance": provenance,
    }
    return {"focus": focus, "upper": upper, "lower": lower, "notes": notes}


def _build_evidence(ref: ElementRef) -> dict[str, Any] | None:
    """evidence（PDF 原文の引用）を中心に据えた投影（設計書 §16）。

    上位 = この引用に依拠する claim / この引用を根拠に挙げる導出・グラフノード /
    掲載セクション。下位 = 空（原文の引用そのものが最下層であり、これ以上の内訳は
    無い）。空であることは事実文（notes）で明示し、推測で埋めない。
    """
    document_id = ref.document_id or ""
    artifacts = refs_mod.document_run_artifacts(document_id)
    record = _evidence_record_for(artifacts, ref.element_id)
    if record is None:
        return None
    notes: list[str] = []
    claim_lookup = _safe(lambda: _claim_id_lookup(document_id), {})
    section_label = ""

    upper: list[dict[str, Any]] = []

    claim_objects = _list(artifacts.get("claim_object_builder"), "claims")
    if "claim_object_builder" not in artifacts:
        notes.append("旧 run のため claim_object_builder artifact が無く、依拠する主張を判定できません")
    for raw_claim_id in _claim_ids_resting_on_evidence(claim_objects, ref.element_id):
        claim_db = claim_lookup.get(raw_claim_id)
        claim_row = (
            _safe(lambda: _claims_by_id([claim_db]), {}).get(claim_db) if claim_db else None
        )
        upper.append(
            _item_from_label(
                "theory_claim", claim_db, document_id,
                labels_mod.claim_label(claim_row or {"text": raw_claim_id}),
                "provides_evidence_for", _status_for_link("explicit"),
                evidence_refs=[raw_claim_id] if not claim_row else [], group=GROUP_CLAIM,
            )
        )

    chains = _list(artifacts.get("derivation_chain"), "chains")
    upper.extend(_chains_referencing_evidence(chains, document_id, ref.element_id))

    graph = _safe(lambda: _load_component_graph(document_id), {"nodes": [], "edges": []})
    for node in graph.get("nodes", []):
        linked = [str(x) for x in (node.get("linked_evidence_ids") or [])]
        if ref.element_id in linked:
            status = _status_for_link(_link_kind_from_backing_status(node.get("source_backing_status")))
            upper.append(
                _item_from_label(
                    "theory_component", node.get("id"), document_id,
                    labels_mod.graph_node_label(node), "supports_component", status,
                    evidence_refs=[str(node.get("id") or "")], group=GROUP_COMPONENT,
                )
            )

    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    section_id = str(source.get("section_id") or "").strip()
    if not section_id:
        block = _blocks_by_id(artifacts).get(str(source.get("block_id") or ""))
        section_id = str((block or {}).get("section_id") or "").strip()
    if section_id:
        label = _section_label(_sections_by_id(artifacts).get(section_id))
        if label:
            upper.append(
                _item(
                    "section", None, document_id, label, "appears_in_section",
                    _status_for_link("explicit"), group=GROUP_SECTION,
                )
            )
            section_label = label

    upper = _cap_lane(_dedupe_items(upper), notes, "上位構造")
    notes.append("この要素は原文の引用そのものです（下位構造はありません）")

    annotations = _annotations_for(ELEMENT_EVIDENCE, ref.element_id, document_id)
    role_text, role_status, role_source = _derive_contextual_role(upper, annotations)

    quote = str(record.get("evidence_text") or "").strip()
    provenance = [f"evidence_registry:{ref.element_id}"]
    block_id = str(source.get("block_id") or "").strip()
    if block_id:
        provenance.append(f"block:{block_id}")

    # 逐語そのものが identity なので、見出しと本文が同じ文字列になるのは正しい
    # （CP1 の対象外。§5.6「逐語をラベルに」）。切り詰めだけ excerpt に統一する。
    focus = {
        "element_type": ELEMENT_EVIDENCE,
        "element_id": ref.element_id,
        "document_id": document_id,
        "label": (excerpt(quote, _FOCUS_LABEL_LIMIT) or ref.element_id),
        "headline": labels_mod.evidence_label(record, section_label=section_label).text,
        "intrinsic_summary": quote,
        "intrinsic": {
            "kind_key": str(record.get("evidence_role") or ""),
            "summary": quote,
            "summary_is_source_language": _is_source_language(quote),
            "facts": [],
        },
        "placement": {"section_label": section_label, "stage": None, "thesis_role": ""},
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "contextual_role_source": role_source,
        "review_notes": [],
        "provenance": provenance,
    }
    return {"focus": focus, "upper": upper, "lower": [], "notes": notes}


def _build_derivation(ref: ElementRef) -> dict[str, Any] | None:
    """derivation（導出チェーン）を中心に据えた投影（設計書 §16）。

    上位 = この導出にリンクするグラフノード・コンポーネント / 導出が産出する claim /
    掲載セクション。下位 = chain 内の数式 step（navigable な equation）/ 前提となる
    claim / 根拠 evidence。
    """
    document_id = ref.document_id or ""
    artifacts = refs_mod.document_run_artifacts(document_id)
    chains = _list(artifacts.get("derivation_chain"), "chains")
    chain = _derivation_chain_for(chains, ref.element_id)
    if chain is None:
        return None
    notes: list[str] = []
    claim_lookup = _safe(lambda: _claim_id_lookup(document_id), {})
    component_lookup = _safe(lambda: _component_id_lookup(document_id), {})
    eq_index = _equation_by_id(refs_mod.equation_records(document_id, artifacts=artifacts))
    symbol_records = _list(artifacts.get("symbol_registry"), "records")

    def _equation_item_label(equation_id: Any) -> "labels_mod.Label":
        return _equation_label(eq_index.get(str(equation_id)), symbols=symbol_records)

    upper: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []

    def _claim_item(raw_id: str, relation: str) -> dict[str, Any]:
        db_id = claim_lookup.get(str(raw_id))
        claim_row = _safe(lambda: _claims_by_id([db_id]), {}).get(db_id) if db_id else None
        return _item_from_label(
            "theory_claim", db_id, document_id,
            labels_mod.claim_label(claim_row or {"text": str(raw_id)}), relation,
            _status_for_link("explicit"),
            evidence_refs=[str(raw_id)] if not claim_row else [],
            group=GROUP_CLAIM_REQUIRED if relation == "requires" else GROUP_CLAIM,
        )

    graph = _safe(lambda: _load_component_graph(document_id), {"nodes": [], "edges": []})
    if not graph.get("nodes") and "component_graph" not in artifacts:
        notes.append("component_graph が保存されていないため、理論コンポーネントとの関係を判定できません")
    for node in graph.get("nodes", []):
        linked = [str(x) for x in (node.get("linked_derivation_ids") or [])]
        linked += [str(x) for x in (node.get("supporting_derivation_ids") or [])]
        if ref.element_id in linked:
            status = _status_for_link(_link_kind_from_backing_status(node.get("source_backing_status")))
            upper.append(
                _item_from_label(
                    "theory_component", node.get("id"), document_id,
                    labels_mod.graph_node_label(node, equation_labeler=_equation_item_label),
                    "supports_component", status,
                    evidence_refs=[str(node.get("id") or "")], group=GROUP_COMPONENT,
                )
            )

    for raw_component_id in chain.get("linked_component_ids") or []:
        component_db = component_lookup.get(str(raw_component_id))
        component_row = (
            _safe(lambda: _components_by_id([component_db]), {}).get(component_db)
            if component_db else None
        )
        upper.append(
            _item_from_label(
                "theory_component", component_db, document_id,
                labels_mod.component_label(component_row or {"name": str(raw_component_id)}),
                "supports_component", _status_for_link("explicit"),
                evidence_refs=[str(raw_component_id)] if not component_row else [],
                group=GROUP_COMPONENT,
            )
        )

    for raw_claim_id in _chain_ids_of(chain, ("output_claim_ids",)):
        upper.append(_claim_item(raw_claim_id, "provides_evidence_for"))

    upper.extend(
        _safe(
            lambda: _section_items_from_ids(
                [str(s) for s in (chain.get("source_section_ids") or [])],
                _sections_by_id(artifacts),
                document_id,
            ),
            [],
        )
    )

    for equation_id in _chain_ids_of(
        chain, ("input_equation_ids", "intermediate_equation_ids", "output_equation_ids")
    ):
        record = eq_index.get(equation_id)
        lower.append(
            _item_from_label(
                "equation", equation_id if record else None, document_id,
                _equation_item_label(equation_id), "uses_equation",
                _status_for_link("explicit"), evidence_refs=[equation_id],
                group=GROUP_EQUATION_UP,
            )
        )

    for raw_claim_id in _chain_ids_of(chain, ("input_claim_ids", "required_claim_ids", "assumption_ids")):
        lower.append(_claim_item(raw_claim_id, "requires"))

    for evidence_id in _evidence_ids_of_chain(chain):
        evidence_record = _evidence_record_for(artifacts, evidence_id) or {}
        lower.append(
            _item_from_label(
                "evidence", evidence_id, document_id,
                labels_mod.evidence_label(evidence_record), "rests_on_evidence",
                _status_for_link("explicit"), evidence_refs=[evidence_id],
                group=GROUP_EVIDENCE,
            )
        )

    upper = _cap_lane(_dedupe_items(upper), notes, "上位構造")
    lower = _cap_lane(_dedupe_items(lower), notes, "下位構造")

    annotations = _annotations_for(ELEMENT_DERIVATION, ref.element_id, document_id)
    role_text, role_status, role_source = _derive_contextual_role(upper, annotations)

    takeaway = str(chain.get("teaching_takeaway") or "").strip()
    operations = _derivation_operation_summary(chain)
    summary = takeaway or (f"操作: {operations}" if operations else "")
    headline = labels_mod.derivation_label(chain)

    focus = {
        "element_type": ELEMENT_DERIVATION,
        "element_id": ref.element_id,
        "document_id": document_id,
        # 中心に据えた導出は「どれを開いているか」の同定が第一なので、focus 見出しは
        # 識別子つきのまま（ITEM 側は可読ラダー）。headline に可読な一行を併載する。
        "label": _derivation_identifier_label(chain),
        "headline": headline.text,
        "intrinsic_summary": summary,
        "intrinsic": {
            "kind_key": str(chain.get("chain_type") or ""),
            "role_key": str(chain.get("operation") or chain.get("operation_family") or ""),
            "summary": summary,
            "summary_is_source_language": _is_source_language(summary),
            "conditions": [
                str(c).strip() for c in (chain.get("conditions") or []) if str(c or "").strip()
            ],
            "eliminated_symbols": [
                str(x) for x in (chain.get("eliminated_symbols") or []) if str(x or "").strip()
            ],
            "retained_symbols": [
                str(x) for x in (chain.get("retained_symbols") or []) if str(x or "").strip()
            ],
            "facts": [],
        },
        "placement": {
            "section_label": next(
                (i["label"] for i in upper if i["element_type"] == "section"), ""
            ),
            "stage": None,
            "thesis_role": "",
        },
        "contextual_role": role_text,
        "contextual_role_status": role_status,
        "contextual_role_source": role_source,
        "review_notes": [],
        "provenance": [f"derivation_chain:{ref.element_id}"],
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
    ELEMENT_EVIDENCE: _build_evidence,
    ELEMENT_DERIVATION: _build_derivation,
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
