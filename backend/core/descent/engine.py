"""構造の降下路 — 降下エンジン（非LLM・決定論・読み取り専用）。

正本設計書は ``docs/features/structure_descent_design.md``（SD1〜SD6 / §6 精査記録）。
足場ダイヤル・楽屋・（v2 の）点検口はすべて本エンジンを通る（別実装禁止）。

- **SD1 段を引くのは常に本人**: サーバは全段を一度に返すだけで、開示順の制御は
  フロントにある。**開示履歴を記録しない**（本モジュールに書き込み経路は無い）。
- **SD2 非LLM・決定論**: 梯子・降下路の生成は読み時決定論。LLM を呼ばない。
- **SD5 数えない**: 使用数・的中数・較正率の集計クエリを作らない。
- confidence 等の生数値は出さない（P7/UC9 と同系の原則）。

読む素材（すべて既存資産の読み出しのみ・A層非改変）:

- ``theory_component_graphs``（``core/personal_graph/queries.py::fetch_component_graph``
  を再利用。stage 事実文は ``core/deliberation/context_lens.py::_main_stage_for_equation``
  と同型ロジック）
- symbol_registry artifact（``core/deliberation/refs.py::symbol_records``。
  v1 の中段は「定義・スコープ・表記ゆれ」で構成する — unit・典型スケールは実データに
  存在しないため使わない。§6 精査記録①）
- derivation_chain artifact（出典リビール。逐語 ``reason`` + 式表示ラベル）
- 二層説明の generic 層（``core/deliberation/decomposition.py::explanations_for_element``
  の approved のみ — candidate を学習者に出さない）
- cartridge の ``notation_patterns``（楽屋の最初の段 = 規約差）

語彙・ラベルの正本は ``core/element_vocab.py``（``THEORY_STAGE_LABELS`` /
``SYMBOL_SCOPE_LABELS`` / ``DEFINITION_STATUS_LABELS`` / ``operation_label``）。
式の表示ラベルは ``core/reconstruction/derivation_source.py::_equation_label_index`` と
同型（latex・内部 ID を出さない）。

本モジュールは FastAPI / LLM / routes / services を import しない（開発ルール2）。
"""

from __future__ import annotations

import logging
from typing import Any

from core import element_explanations as element_explanations_store
from core.cartridges import load_cartridge
from core.course_data import course_cartridge_id
from core.deliberation.decomposition import explanations_for_element
from core.deliberation.labels import equation_label
from core.deliberation.refs import (
    derivation_records,
    document_run_artifacts,
    equation_records,
    symbol_records,
)
from core.deliberation.schema import (
    ELEMENT_EQUATION,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    SCOPE_DOCUMENT,
    ElementRef,
)
from core.descent.resolve import (
    ELEMENT_TYPE_CLAIM,
    ELEMENT_TYPE_COMPONENT,
    ELEMENT_TYPE_EQUATION,
    SUPPORTED_ELEMENT_TYPES,
    ResolvedElement,
    resolve_element,
)
from core.element_vocab import (
    definition_missing_fact,
    definition_status_label,
    operation_label,
    symbol_scope_label,
    theory_stage_key,
    theory_stage_label,
)
from core.personal_graph.queries import fetch_component_graph
from core.text_excerpt import excerpt, first_sentence

logger = logging.getLogger(__name__)

__all__ = [
    "BACKSTAGE_DECLARATION",
    "SUPPORTED_ELEMENT_TYPES",
    "build_backstage_path",
    "build_ladder",
]

# 楽屋の宣言一行（設計書 §4。逐語をガードレールで固定する — 変更はオーナー判断）。
BACKSTAGE_DECLARATION = "ここでの質問と閲覧は集計に入りません。記録はあなたにだけ残ります"

# 1段目の想起プロンプト（問いの形の固定テンプレ。答え・stage 名を含めない — SD3 /
# 橋本の pretesting 修正「事実文を先に出さない」）。
RECALL_PROMPTS: dict[str, str] = {
    ELEMENT_TYPE_EQUATION: (
        "この式はどの理論段階の一手にあたるか、まず自分の語で言ってみてください"
    ),
    ELEMENT_TYPE_COMPONENT: (
        "この構成要素は理論の流れの中でどんな働きを担っているか、"
        "まず自分の語で言ってみてください"
    ),
    ELEMENT_TYPE_CLAIM: (
        "この主張は理論の流れの中でどこを支えているか、まず自分の語で言ってみてください"
    ),
}

# 記号段の上限（context_lens の focus.intrinsic.symbols と同じ 8 件）。
SYMBOLS_MAX = 8

# stage 事実文の description 部の上限（stage_fact rung の可読性のため。逐語性が要る
# reveal の reason には適用しない）。
_STAGE_DESCRIPTION_LIMIT = 160

# 出典リビールの事実文（素材ゼロを隠さない — 空は沈黙ではない）。
REVEAL_NOTE_WITH_ITEMS = "出典の導出チェーンの記述を逐語で示しています。"
REVEAL_NOTE_EMPTY = "この要素に対応する導出ステップは出典から同定できていません。"

# graph_layer の語彙（component_graph/schema.py と同じ3層）。
_GRAPH_LAYER_MAIN = "main"

# 公開 element_type → W層内部語彙（generic 説明の ElementRef 用）。
_W_ELEMENT_TYPES = {
    ELEMENT_TYPE_EQUATION: ELEMENT_EQUATION,
    ELEMENT_TYPE_COMPONENT: ELEMENT_THEORY_COMPONENT,
    ELEMENT_TYPE_CLAIM: ELEMENT_THEORY_CLAIM,
}


def _str_list(value: Any) -> list[str]:
    return [str(x) for x in value if str(x or "").strip()] if isinstance(value, list) else []


def _graph_nodes(graph: dict) -> list[dict[str, Any]]:
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


def _node_ids(node: dict[str, Any]) -> set[str]:
    """ノードの同定キー候補（保存時の DB UUID と agent 側 ID の両方。persistence.py 参照）。"""
    ids = {
        str(node.get(key) or "").strip()
        for key in ("id", "node_id", "component_id", "agent_component_id")
    }
    return {i for i in ids if i}


def _node_stage(node: dict[str, Any]) -> tuple[str, str]:
    """main ノードから ``(stage 訳語, 説明文)`` を引く（引けなければ ``("", "")``）。

    stage キーの導出は ``context_lens._main_stage_for_equation`` と同型
    （label → visual_label の順で ``theory_stage_key``）。訳語は
    ``element_vocab.THEORY_STAGE_LABELS`` が正本。
    """
    key = theory_stage_key(node.get("label")) or theory_stage_key(node.get("visual_label"))
    if not key:
        return "", ""
    label = theory_stage_label(key)
    if not label:
        return "", ""
    description = excerpt(first_sentence(node.get("description")), _STAGE_DESCRIPTION_LIMIT)
    return label, description


def _node_matches_element(node: dict[str, Any], resolved: ResolvedElement) -> bool:
    """ノードが要素本体に対応するか（equation はリンク、component は ID、claim はリンク）。"""
    if resolved.element_type == ELEMENT_TYPE_EQUATION:
        return resolved.element_id in _str_list(node.get("linked_equation_ids"))
    if resolved.element_type == ELEMENT_TYPE_COMPONENT:
        return bool(_node_ids(node) & resolved.match_ids)
    # claim: graph 側の claim id は世代により agent 側 ID / DB UUID の両形があるため
    # match_ids（DB UUID ∪ legacy_ids ∪ span_id）との交差で判定する。
    return bool(set(_str_list(node.get("linked_claim_ids"))) & resolved.match_ids)


def _stage_fact(nodes: list[dict[str, Any]], resolved: ResolvedElement) -> tuple[str, str]:
    """要素が属する理論段階の ``(stage 訳語, 説明文)``。引けなければ ``("", "")``。

    まず main 層のノードで直接一致を探し、無ければ detail 層の一致ノードから
    ``parent_component_id`` / ``member_component_ids`` で main ノードへ遡る。
    推測で穴埋めしない（引けなければ stage rung 自体を出さない）。
    """
    detail_matches: list[dict[str, Any]] = []
    for node in nodes:
        if not _node_matches_element(node, resolved):
            continue
        if str(node.get("graph_layer") or _GRAPH_LAYER_MAIN) == _GRAPH_LAYER_MAIN:
            label, description = _node_stage(node)
            if label:
                return label, description
        else:
            detail_matches.append(node)

    main_nodes = [
        n for n in nodes
        if str(n.get("graph_layer") or _GRAPH_LAYER_MAIN) == _GRAPH_LAYER_MAIN
    ]
    for detail in detail_matches:
        detail_ids = _node_ids(detail)
        parent_raw = str(detail.get("parent_component_id") or "").strip()
        for main_node in main_nodes:
            if (parent_raw and parent_raw in _node_ids(main_node)) or (
                set(_str_list(main_node.get("member_component_ids"))) & detail_ids
            ):
                label, description = _node_stage(main_node)
                if label:
                    return label, description
    return "", ""


def _related_equation_ids(
    nodes: list[dict[str, Any]], resolved: ResolvedElement
) -> list[str]:
    """要素に関係する equation_id の順序付きリスト（重複なし・決定論）。

    equation は自分自身。component / claim はグラフの一致ノードの
    ``linked_equation_ids`` 経由で引く（引けなければ空 — 記号 rung は出さない）。
    """
    if resolved.element_type == ELEMENT_TYPE_EQUATION:
        return [resolved.element_id]
    out: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if not _node_matches_element(node, resolved):
            continue
        for eq_id in _str_list(node.get("linked_equation_ids")):
            if eq_id not in seen:
                seen.add(eq_id)
                out.append(eq_id)
    return out


def _symbol_items(
    records: list[dict[str, Any]], equation_ids: list[str]
) -> list[dict[str, Any]]:
    """記号段の項目（定義・スコープ・表記ゆれ。§6 精査記録① — unit・スケールは使わない）。

    ``meaning`` は定義の**逐語引用**（``definition_evidence_texts`` の最初の非空）。
    定義が無い記号は ``missing_fact``（「論文中に明示的な定義が見つかりません」）で
    正直に出す（隠さず・推測もしない）。上限 :data:`SYMBOLS_MAX` 件。
    """
    eq_set = {str(e) for e in equation_ids if str(e or "").strip()}
    if not eq_set:
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        defining = set(_str_list(record.get("defining_equation_ids")))
        used = set(_str_list(record.get("used_in_equation_ids")))
        if not (eq_set & (defining | used)):
            continue
        symbol = str(record.get("canonical_symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        status = str(record.get("definition_status") or "").strip()
        review_reasons = _str_list(record.get("review_reasons"))
        missing = status == "definition_missing" or "definition_missing" in review_reasons
        meaning = ""
        for evidence in record.get("definition_evidence_texts") or []:
            text = str(evidence or "").strip()
            if text:
                meaning = text
                break
        item: dict[str, Any] = {
            "symbol": symbol,
            "meaning": meaning,
            "scope_label": symbol_scope_label(record.get("scope")),
            "variants": _str_list(record.get("notation_variants")),
            "definition_status_label": definition_status_label(
                "definition_missing" if missing else status
            ),
        }
        if missing:
            item["missing_fact"] = definition_missing_fact()
        items.append(item)
        if len(items) >= SYMBOLS_MAX:
            break
    return items


def _equation_label_index(
    document_id: str, artifacts: dict[str, Any]
) -> dict[str, str]:
    """equation_id → 表示ラベルの索引（``derivation_source._equation_label_index`` と同型）。

    ``core.deliberation.labels.equation_label`` 経由で、latex・raw_text・内部 ID は
    使わない（labels.py の規約をそのまま継承。索引に無い ID はラベルを出さない）。
    """
    index: dict[str, str] = {}
    for record in equation_records(document_id, artifacts=artifacts):
        eq_id = str(record.get("equation_id") or "")
        if not eq_id:
            continue
        label = equation_label(record).text
        if label:
            index[eq_id] = label
    return index


def _reveal_rung(
    chains: list[dict[str, Any]],
    equation_ids: list[str],
    label_index: dict[str, str],
) -> dict[str, Any]:
    """出典リビール（rung 4）。当該式が関与する derivation step の逐語 reason。

    素材ゼロでも rung 自体は返し、``note`` で正直に伝える（空は沈黙ではない）。
    ``DerivationStep`` のキーは ``input_equation_ids`` / ``operation`` /
    ``output_equation_ids`` / ``reason``（``justification`` というキーは無い）。
    """
    eq_set = {str(e) for e in equation_ids if str(e or "").strip()}
    items: list[dict[str, Any]] = []
    if eq_set:
        for chain in chains:
            steps = chain.get("steps") if isinstance(chain, dict) else None
            for step in steps if isinstance(steps, list) else []:
                if not isinstance(step, dict):
                    continue
                step_inputs = _str_list(step.get("input_equation_ids"))
                step_outputs = _str_list(step.get("output_equation_ids"))
                if not (eq_set & (set(step_inputs) | set(step_outputs))):
                    continue
                operation = str(step.get("operation") or "").strip()
                items.append(
                    {
                        "operation_label": operation_label(operation) or operation,
                        "reason": str(step.get("reason") or "").strip(),
                        "input_labels": [
                            label_index[e] for e in step_inputs if e in label_index
                        ],
                        "output_labels": [
                            label_index[e] for e in step_outputs if e in label_index
                        ],
                    }
                )
    return {
        "kind": "reveal",
        "items": items,
        "note": REVEAL_NOTE_WITH_ITEMS if items else REVEAL_NOTE_EMPTY,
    }


def compose_ladder(
    element_type: str,
    *,
    stage_label: str,
    stage_description: str,
    symbol_items: list[dict[str, Any]],
    reveal: dict[str, Any],
) -> dict[str, Any]:
    """素材から梯子を決定論的に組む純粋関数（テスト可能な合成部）。

    段の順序は固定: ①想起プロンプト（問いの形・答えを含めない）②stage 骨格事実文
    （引けなければ出さない — 推測穴埋め禁止）③記号（素材が無ければ出さない）
    ④出典リビール（常に返す。素材ゼロは note で正直に）。
    """
    rungs: list[dict[str, Any]] = [
        {"kind": "recall_prompt", "text": RECALL_PROMPTS[element_type]}
    ]
    if stage_label:
        text = f"理論の流れの中では「{stage_label}」の段階にあたります。"
        if stage_description:
            text += " " + stage_description
        rungs.append({"kind": "stage_fact", "text": text, "stage_label": stage_label})
    if symbol_items:
        rungs.append({"kind": "symbols", "items": symbol_items})
    rungs.append(reveal)
    return {"available": True, "rungs": rungs}


def build_ladder(
    course_data: dict | None, course_id: str, element_type: str, element_id: str
) -> dict[str, Any]:
    """足場ダイヤルの梯子を組む（設計書 §2。全段を一度に返す — 開示順制御はフロント）。

    要素がコース sources 内で解決できなければ ``{"available": False}``（fail-closed。
    エラーにしない）。``course_id`` は呼び出し文脈の記録用引数で、スコープの実体は
    ``course_data``（受講ゲート通過済みの版ビュー）から導く。
    """
    resolved = resolve_element(element_type, element_id, course_data)
    if resolved is None:
        return {"available": False}

    artifacts = document_run_artifacts(resolved.document_id)
    nodes = _graph_nodes(fetch_component_graph(resolved.document_id))

    stage_label, stage_description = _stage_fact(nodes, resolved)
    equation_ids = _related_equation_ids(nodes, resolved)
    symbol_items = _symbol_items(
        symbol_records(resolved.document_id, artifacts=artifacts), equation_ids
    )
    reveal = _reveal_rung(
        derivation_records(resolved.document_id, artifacts=artifacts),
        equation_ids,
        _equation_label_index(resolved.document_id, artifacts),
    )
    return compose_ladder(
        element_type,
        stage_label=stage_label,
        stage_description=stage_description,
        symbol_items=symbol_items,
        reveal=reveal,
    )


# ---------------------------------------------------------------------------
# 楽屋の降下路（設計書 §4）
# ---------------------------------------------------------------------------


def _notation_pattern_items(course_data: dict | None) -> list[dict[str, Any]]:
    """cartridge の ``notation_patterns``（規約差の段）。cartridge 不在は空リストへ縮退。"""
    cartridge_id = course_cartridge_id(course_data if isinstance(course_data, dict) else None)
    try:
        cartridge = load_cartridge(cartridge_id or None)
    except Exception:  # noqa: BLE001
        logger.warning(
            "descent: cartridge load failed for %r (skipping notation patterns)",
            cartridge_id,
            exc_info=True,
        )
        return []
    items: list[dict[str, Any]] = []
    for entry in cartridge.ontology.notation_patterns:
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("pattern") or "").strip()
        if not pattern:
            continue
        items.append(
            {
                "id": str(entry.get("id") or ""),
                "pattern": pattern,
                "concept_type": str(entry.get("concept_type") or ""),
            }
        )
    return items


def _generic_explanation_items(resolved: ResolvedElement) -> list[dict[str, Any]]:
    """二層説明の generic 層（approved のみ — candidate を学習者に出さない）。

    ``role`` 付きの行（discussion_seed 等の生成物）は説明本文ではないため除外する。
    読み取り失敗は空リストへ縮退（fail-soft）。
    """
    try:
        ref = ElementRef(
            scope=SCOPE_DOCUMENT,
            element_type=_W_ELEMENT_TYPES[resolved.element_type],
            element_id=resolved.element_id,
            document_id=resolved.document_id,
        )
        ref.validate()
        rows = explanations_for_element(ref)
    except Exception:  # noqa: BLE001
        logger.warning(
            "descent: generic explanation read failed for %s:%s",
            resolved.element_type,
            resolved.element_id,
            exc_info=True,
        )
        return []
    items: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != element_explanations_store.STATUS_APPROVED:
            continue
        if row.get("kind") != element_explanations_store.KIND_GENERIC:
            continue
        if row.get("role") is not None:
            continue
        body = str(row.get("body") or "").strip()
        if body:
            items.append({"body": body})
    return items


def compose_backstage_path(
    notation_items: list[dict[str, Any]],
    symbol_items: list[dict[str, Any]],
    generic_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """素材から楽屋の降下路を組む純粋関数。素材が無い step は出さない（空 step を並べない）。"""
    steps: list[dict[str, Any]] = []
    if notation_items:
        steps.append({"kind": "notation_patterns", "items": notation_items})
    if symbol_items:
        steps.append({"kind": "symbol_definitions", "items": symbol_items})
    if generic_items:
        steps.append({"kind": "generic_explanations", "items": generic_items})
    return {"declaration": BACKSTAGE_DECLARATION, "steps": steps}


def build_backstage_path(
    course_data: dict | None, course_id: str, element_type: str, element_id: str
) -> dict[str, Any]:
    """楽屋の降下路（設計書 §4: 規約差 → 記号定義 → 前提概念の generic 説明）。

    **閲覧の記録を一切しない**（GET はサーバに何も書かない — SD4/SD5 の読み取り面）。
    要素が解決できなくても宣言一行は返す（規約差の段は cartridge 由来のため
    要素なしでも出せる）。
    """
    notation_items = _notation_pattern_items(course_data)

    symbol_items: list[dict[str, Any]] = []
    generic_items: list[dict[str, Any]] = []
    resolved = resolve_element(element_type, element_id, course_data)
    if resolved is not None:
        artifacts = document_run_artifacts(resolved.document_id)
        nodes = _graph_nodes(fetch_component_graph(resolved.document_id))
        equation_ids = _related_equation_ids(nodes, resolved)
        symbol_items = _symbol_items(
            symbol_records(resolved.document_id, artifacts=artifacts), equation_ids
        )
        generic_items = _generic_explanation_items(resolved)

    return compose_backstage_path(notation_items, symbol_items, generic_items)
