"""参照の健全性（知識の転用層 P4-3）。

正本: ``docs/features/knowledge_transfer_design.md`` §6。親文書の診断 C-14（束を作った
ときにしか走らない ``check_refs`` しか ID 破断を検出する仕組みが無い）への是正。

**何を検査するか**（すべて live 行・DB のみ・純 SQL。LLM も embedding も呼ばない = KT3）。

A. **参照の検査**（「参照先が存在しないリンク」= 破断。``status`` を ``broken`` にする）:

1. 理論操作グラフ（``theory_component_graphs`` の最新行）の main / equation_detail
   ノードの ``linked_claim_ids`` が ``theory_claims_live`` に着地しているか。
   同じくノードの ``linked_equation_ids`` が ``knowledge_equations`` に着地しているか。
2. ``theory_components_live`` の ``evidence_claims`` / ``linked_claim_ids`` が claims に、
   ``linked_equation_ids`` が ``knowledge_equations``（live 行）に着地しているか。
3. ``theory_claims_live.chunk_id IS NULL``（出典チャンクに着地していない主張）。
   ただし ``origin='equation_synthesis'``（式から合成された主張）は本文のチャンクから
   切り出されたものではないので対象外（V-9: 常時赤の計器を作らない）。
4. ``learning_units_live`` の ``linked_claim_ids`` / ``linked_component_ids`` が
   各表に着地しているか。
5. グラフの辺の ``evidence.evidence_derivation_ids`` が ``knowledge_derivation_steps``
   に着地しているか（step ID は裸 ``step_001`` と合成 ``{derivation_id}:{step_id}`` の
   両方が使われうるので、どちらでも引けるようにする）。
6. 式の詳細ノードが ``parent_component_id`` で主グラフのノードに属しているか
   （空・不在はどちらも「主グラフに吊られていない」という同じ事実）。
7. グラフに埋め込まれた DSL の辺の ``evidence_refs``（主張・式）が着地しているか。

B. **存在の検査**（「そもそも無い」「上流で打ち切った」= 欠落。**破断ではない**ので
``status`` は ``ok`` のまま、``facts`` に事実として書く）。参照だけを見て「切れはありません」
と言うと、**層がまるごと空の教材でも計器が緑になる**（2026-09-19 実測: 12/12 本が ``ok``）:

8. 層が空（主グラフ / 式 / 導出ステップ / 学ぶ単位 / 要素の説明 / 図・画像）。
9. run の ``stage_outputs`` に残る打ち切り（``coverage.truncated`` / ``reasons``）と、
   上限・素材なしで実行されなかった段階（``skipped_by_limit`` / ``skipped_reason``）。
   教員が明示的に選んだ ``skipped_by_option`` は欠落として報告しない。
10. 取り込みの完全性（``document_completeness`` artifact の ``review_reasons``）。

語彙（層・段階・完全性の事実文）の正本は :mod:`core.coverage_facts`。

**不変条項（本モジュールに掛かるもの）**:

- **KT3 決定論・LLM 0 回**: ``core.llm`` も FastAPI も import しない（開発ルール2）。
- **KT5 情報を落とさない**: 結果は「解決済みフラグ」ではなく**検査時点の事実**。
  DB へ何も書かない（削除も更新も挿入も発行しない）。切れている参照は
  件数に潰さず ``details`` に列挙する（打ち切らない）。
- **KT7 / T-3 数値を見せない**: ``facts`` に数字を書かない（「切れがあります」までで、
  何件かは言わない）。件数バッジを作らない。読み手は ``details`` の配列長として
  数えられるだけ。
- **KO5 live ビューを読む**: 基表 ``theory_claims`` / ``theory_components`` は読まない
  （``backend/tests/test_knowledge_objects_guardrails.py`` の allowlist が固定）。
- 表示ラベル（``details[].from_label``）には**内部 ID を出さない**（ノードは ``label``、
  コンポーネントは ``name``、主張は本文の先頭）。``ref`` は「どの ID が切れているか」
  という運用上の事実そのものなので ID のまま列挙する（T-3）。

呼び出し点は 2 つ:

- パイプライン ``_stage_completed``（best-effort・失敗は握る）→ run の
  ``stage_outputs.reference_health`` に検査時点の事実を残す（生成ログの一部）。
- ``GET /api/admin/documents/{id}/reference-health``（読み取り専用。既定は run に保存済みの
  事実を :func:`load_recorded_reference_health` で読み、``?recheck=true`` のときだけ
  その場で検査し直す。**再検査の結果も保存しない**）。
- 束の取り込み（``core/knowledge_import/apply.py``）→ 取り込み run の
  ``stage_outputs.reference_health``（P4-R7）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text as sa_text

from core import coverage_facts
from core import display_projection as _dp

logger = logging.getLogger(__name__)

__all__ = [
    "DETAIL_KINDS",
    "FACT_NOT_CHECKED",
    "FACT_NO_MATERIAL",
    "FACT_OK",
    "GAP_KINDS",
    "LEGACY_FACT_OK",
    "KIND_FACTS",
    "SOURCE_RECHECKED",
    "SOURCE_RECORDED",
    "STATUS_BROKEN",
    "GAP_DETACHED_DETAIL",
    "FACT_DETACHED_DETAIL",
    "STATUS_OK",
    "STATUS_UNCHECKED",
    "build_reference_health",
    "check_document_references",
    "load_recorded_reference_health",
    "unchecked_result",
]

#: 結果の出所（``source``）。保存済みの検査結果か、その場で検査し直したか。
SOURCE_RECORDED = "recorded"
SOURCE_RECHECKED = "rechecked"

STATUS_OK = "ok"
STATUS_BROKEN = "broken"
STATUS_UNCHECKED = "unchecked"

#: 破断の種別（``details`` のキー）。増やすときは :data:`KIND_FACTS` にも事実文を足す。
DETAIL_KINDS: tuple[str, ...] = (
    "graph_node_claim",
    "graph_node_equation",
    "graph_edge_derivation",
    "dsl_edge_evidence",
    "component_claim",
    "component_equation",
    "claim_without_chunk",
    "unit_claim",
    "unit_component",
)

#: 欠落の種別（``details`` のキー。**破断ではない** ので ``status`` を変えない）。
#: 事実文は :mod:`core.coverage_facts` の表から項目ごとに引く（種別ごとの固定文ではない）。
GAP_EMPTY_LAYERS = "empty_layers"
GAP_COMPLETENESS = "completeness_reasons"
#: 主グラフに吊られていない式の詳細ノード。**破断ではなく欠落**（R-5）。旧 run
#: （node↔部品の突合が claim 交差に広がる前）のグラフは detail ノードの親が空のものが
#: 多く、これを破断に数えると既存教材が一斉に ``broken`` へ振れる。参照先が「無い」
#: のではなく「まだ結ばれていない」事実なので、他の欠落と同じ区画に置く。
GAP_DETACHED_DETAIL = "detail_node_parent"
GAP_KINDS: tuple[str, ...] = (
    GAP_EMPTY_LAYERS,
    coverage_facts.GAP_TRUNCATED,
    coverage_facts.GAP_SKIPPED,
    GAP_COMPLETENESS,
    GAP_DETACHED_DETAIL,
)

#: 事実文（固定文・言い換えない・**数字を書かない** = T-3）。
#:
#: ``FACT_OK`` は「**検査した参照は**すべて解決している」であって「この教材は健全だ」
#: ではない。層がまるごと空でも、上流で入力を打ち切っていても、参照そのものは解決する
#: （2026-09-19 実測: 12/12 本が ok）。読み手が取り違えないよう主語を明示する。
FACT_OK = "検査した参照はすべて解決しています。"
FACT_NO_MATERIAL = "この教材にはまだ解析結果がないため、参照の整合は確認できません。"
FACT_NOT_CHECKED = "参照の整合はまだ確認されていません。"

#: 2026-09-19 より前の ``FACT_OK``。**参照の破断しか見ていない**検査の結果なので、
#: この文を持つ保存済みスナップショットは読み手に返さず、その場で検査し直させる
#: （:func:`load_recorded_reference_health` が ``None`` を返し、呼び出し側が再検査する）。
#: 旧い教材が「切れはありません」と言い続けるのを、再解析を待たずに止めるための移行規則。
LEGACY_FACT_OK = "参照の切れはありません。"

KIND_FACTS: dict[str, str] = {
    "graph_node_claim": "理論操作グラフのノードが参照している主張のうち、見つからないものがあります。",
    "graph_node_equation": "理論操作グラフのノードが参照している式のうち、見つからないものがあります。",
    "graph_edge_derivation": "理論操作グラフのつながりが根拠にしている導出ステップのうち、見つからないものがあります。",
    "dsl_edge_evidence": "概念グラフのつながりが根拠にしている主張・式のうち、見つからないものがあります。",
    "component_claim": "コンポーネントが根拠にしている主張のうち、見つからないものがあります。",
    "component_equation": "コンポーネントが参照している式のうち、見つからないものがあります。",
    "claim_without_chunk": "出典チャンクに結び付いていない主張があります。",
    "unit_claim": "学ぶ単位が参照している主張のうち、見つからないものがあります。",
    "unit_component": "学ぶ単位が参照しているコンポーネントのうち、見つからないものがあります。",
}

#: 欠落 ``detail_node_parent`` の事実文（``KIND_FACTS`` は破断の表なので別に持つ）。
FACT_DETACHED_DETAIL = "式の詳細ノードのうち、主グラフのどのノードにも属していないものがあります。"

#: ``chunk_id`` を持たないのが**正常**な claim の由来（V-9）。式から合成された claim は
#: 本文のチャンクから切り出されていないので、出典チャンク未着地を破断に数えない。
_CHUNKLESS_CLAIM_ORIGINS: frozenset[str] = frozenset({"equation_synthesis"})

#: グラフのどの層を検査するか（``debug`` 層は fallback / inferred の置き場なので対象外）。
CHECKED_GRAPH_LAYERS = ("main", "equation_detail")

#: ``from_label`` に出す本文の長さ（表示用の切り詰め。件数ではないので数値は外に出ない）。
_LABEL_SNIPPET_MAX = 80

#: 「裸の内部 ID」を表示ラベルに出さないための判定（UUID / agent 側 ID 形）。
#: 語彙は ``core.display_projection`` の系統別定数（教員向け運用情報の局所判定）を参照する。
_UUID_RE = _dp.UUID_FULL_RE
_INTERNAL_ID_RE = _dp.REFERENCE_HEALTH_INTERNAL_ID_RE  # 正本は display_projection（DP2）


# ---------------------------------------------------------------------------
# 小さなヘルパー（純粋）
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value or "").strip()


def _id_list(value: Any) -> list[str]:
    """JSONB の ID 配列を文字列リストにする（dict 形の要素も許容する）。"""
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("claim_id") or item.get("id") or item.get("ref")
        ref = _text(item)
        if ref:
            out.append(ref)
    return out


def _display_label(*candidates: Any) -> str:
    """表示ラベル（最初の非空・かつ裸の内部 ID でない候補）。見つからなければ空文字。

    ここで空文字を返すのは「ラベルが無い」という事実であって、内部 ID で埋めない。
    """
    for candidate in candidates:
        label = _text(candidate)
        if not label:
            continue
        if _UUID_RE.match(label) or _INTERNAL_ID_RE.match(label):
            continue
        return label[:_LABEL_SNIPPET_MAX]
    return ""


def _scope_keys(source_scope: Any) -> set[str]:
    """``source_scope`` の ``span_id`` / ``legacy_ids`` をキー候補として集める。

    ``routes/theory_components.py::_resolve_claim_reference_index`` と同じ流儀
    （DB UUID / agent 側 ID / legacy_ids のいずれでも参照が引けるようにする）。
    """
    keys: set[str] = set()
    if not isinstance(source_scope, Mapping):
        return keys
    span_id = _text(source_scope.get("span_id"))
    if span_id:
        keys.add(span_id)
    legacy_ids = source_scope.get("legacy_ids")
    if isinstance(legacy_ids, (list, tuple)):
        keys.update(_text(v) for v in legacy_ids if _text(v))
    return keys


def _row_get(row: Any, key: str) -> Any:
    """Mapping / RowMapping / 属性アクセスのいずれでも読めるようにする。"""
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _claim_keys(row: Any) -> set[str]:
    keys = {_text(_row_get(row, "id")), _text(_row_get(row, "agent_claim_id"))}
    keys |= _scope_keys(_row_get(row, "source_scope"))
    keys.discard("")
    return keys


def _component_keys(row: Any) -> set[str]:
    keys = {_text(_row_get(row, "id")), _text(_row_get(row, "agent_component_id"))}
    keys |= _scope_keys(_row_get(row, "source_scope"))
    keys.discard("")
    return keys


def _equation_keys(row: Any) -> set[str]:
    keys = {_text(_row_get(row, "id")), _text(_row_get(row, "agent_equation_id"))}
    keys.discard("")
    return keys


def _derivation_keys(row: Any) -> set[str]:
    """導出ステップの参照キー（合成 ID と裸 ID の両方で引けるようにする）。

    ``knowledge_derivation_steps.agent_step_id`` は **チェーン内でしか一意でない**
    step ID を文書内で一意にするため ``{derivation_id}:{step_id}`` の合成 ID になって
    いる（KO2 の明示例外）。一方、理論操作グラフの辺は裸の ``step_001`` を持つ。
    どちらの綴りでも「その導出ステップは在る」と読めるようにする
    （綴りの違いを「参照切れ」として報告すると、本当の切れが埋もれる）。
    チェーン自体への参照（``agent_derivation_id``）も解決済みとして扱う。
    """
    step_id = _text(_row_get(row, "agent_step_id"))
    keys = {
        _text(_row_get(row, "id")),
        step_id,
        _text(_row_get(row, "agent_derivation_id")),
        _text(_row_get(row, "stable_key")),
    }
    if ":" in step_id:
        keys.add(step_id.rsplit(":", 1)[1])
    keys.discard("")
    return keys


def _collect_keys(rows: Iterable[Any], key_fn) -> set[str]:
    known: set[str] = set()
    for row in rows or []:
        known |= key_fn(row)
    return known


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 純粋部（行のリスト → 事実）
# ---------------------------------------------------------------------------


def unchecked_result(fact: str = FACT_NOT_CHECKED, *, checked_at: str | None = None) -> dict:
    """「確認できなかった」という事実（DB 不達・材料なし・検査失敗に共通の形）。"""
    return {
        "status": STATUS_UNCHECKED,
        "checked_at": checked_at or _now_iso(),
        "facts": [fact],
        "details": {},
    }


def build_reference_health(
    *,
    claims: Sequence[Any] = (),
    components: Sequence[Any] = (),
    equations: Sequence[Any] = (),
    units: Sequence[Any] = (),
    graph_nodes: Sequence[Any] = (),
    graph_edges: Sequence[Any] = (),
    dsl_edges: Sequence[Any] = (),
    derivations: Sequence[Any] = (),
    layer_presence: Mapping[str, Any] | None = None,
    stage_outputs: Mapping | None = None,
    completeness: Mapping | None = None,
    checked_at: str | None = None,
) -> dict:
    """live 行のリストから参照の健全性の事実を組み立てる（純粋関数・DB に触らない）。

    Args:
        claims: ``theory_claims_live`` の行（``id`` / ``agent_claim_id`` /
            ``source_scope`` / ``chunk_id`` / ``text``）。
        components: ``theory_components_live`` の行（``id`` / ``agent_component_id`` /
            ``name`` / ``source_scope`` / ``evidence_claims`` / ``linked_claim_ids`` /
            ``linked_equation_ids``）。
        equations: ``knowledge_equations`` の live 行（``id`` / ``agent_equation_id`` / ``label``）。
        units: ``learning_units_live`` の行（``id`` / ``label`` / ``linked_claim_ids`` /
            ``linked_component_ids``）。
        graph_nodes: 最新 ``theory_component_graphs.graph_json`` の ``nodes``。
        graph_edges: 同 ``edges``（``evidence.evidence_derivation_ids`` を見る）。
        dsl_edges: 同 ``dsl.edges``（``evidence_refs`` の主張・式を見る）。
        derivations: ``knowledge_derivation_steps`` の live 行。
        layer_presence: 層 → 存在するか（:data:`core.coverage_facts.LAYER_KEYS`）。
            **``None`` なら存在の検査自体をしない**（キーが無い層も検査しない）。
        stage_outputs: run の ``stage_outputs``（打ち切り・未実行の段階を拾う）。
        completeness: ``document_completeness`` artifact の payload。
        checked_at: 検査時刻（ISO 文字列）。省略時は現在時刻。

    Returns:
        ``{"status", "checked_at", "facts", "details"}``。``facts`` に数字は入らない（T-3）。
        ``status`` が ``broken`` になるのは**参照の破断**だけで、欠落（層が空・打ち切り・
        未実行・完全性）は ``ok`` のまま ``facts`` に併記する。
    """
    details: dict[str, list[dict]] = {}

    def _add(kind: str, ref: str, from_label: str) -> None:
        ref = _text(ref)
        if not ref:
            return
        bucket = details.setdefault(kind, [])
        entry = {"ref": ref, "from_label": from_label}
        if entry not in bucket:
            bucket.append(entry)

    known_claims = _collect_keys(claims, _claim_keys)
    known_components = _collect_keys(components, _component_keys)
    known_equations = _collect_keys(equations, _equation_keys)
    known_derivations = _collect_keys(derivations, _derivation_keys)

    has_material = bool(
        claims or components or equations or units or graph_nodes or derivations
    )

    # ① グラフノード → claim / equation、式の詳細ノード → 親（主グラフ）
    main_node_ids: set[str] = set()
    for node in graph_nodes or []:
        if isinstance(node, Mapping) and (_text(node.get("graph_layer")) or "main") == "main":
            for key in ("component_id", "id", "agent_component_id"):
                value = _text(node.get(key))
                if value:
                    main_node_ids.add(value)
    for node in graph_nodes or []:
        if not isinstance(node, Mapping):
            continue
        layer = _text(node.get("graph_layer")) or "main"
        if layer not in CHECKED_GRAPH_LAYERS:
            continue
        label = _display_label(node.get("label"), node.get("description"))
        for ref in _id_list(node.get("linked_claim_ids")):
            if ref not in known_claims:
                _add("graph_node_claim", ref, label)
        for ref in _id_list(node.get("linked_equation_ids")):
            if ref not in known_equations:
                _add("graph_node_equation", ref, label)
        if layer == "equation_detail":
            # 主グラフに吊られていない式の詳細ノード（親が空 / 親が主グラフに無い）。
            # ``ref`` はそのノード自身の ID（どのノードが宙に浮いているかが運用上の事実）。
            # 欠落の区画（``GAP_DETACHED_DETAIL``）に積み、``status`` は変えない（R-5）。
            parent = _text(node.get("parent_component_id"))
            if not parent or parent not in main_node_ids:
                _add(
                    GAP_DETACHED_DETAIL,
                    _text(node.get("component_id")) or _text(node.get("id")),
                    label,
                )

    # ② component → claim / equation
    for comp in components or []:
        label = _display_label(_row_get(comp, "name"))
        comp_claim_refs = _id_list(_row_get(comp, "evidence_claims")) + _id_list(
            _row_get(comp, "linked_claim_ids")
        )
        for ref in comp_claim_refs:
            if ref not in known_claims:
                _add("component_claim", ref, label)
        for ref in _id_list(_row_get(comp, "linked_equation_ids")):
            if ref not in known_equations:
                _add("component_equation", ref, label)

    # ③ claim → chunk（出典チャンクに着地していない主張）
    #
    # V-9: 式から合成された claim（``origin='equation_synthesis'``）は、そもそも本文の
    # チャンクから切り出されたものではないので chunk_id を持たない。これを破断に数えると
    # 計器が**常時赤**になり、本当の破断が読めなくなる（常時赤の計器は無視される）。
    # 由来が合成である限り「切れ」ではないので対象から外す。
    for claim in claims or []:
        if _text(_row_get(claim, "chunk_id")):
            continue
        if _text(_row_get(claim, "origin")) in _CHUNKLESS_CLAIM_ORIGINS:
            continue
        _add(
            "claim_without_chunk",
            _text(_row_get(claim, "id")),
            _display_label(_row_get(claim, "text")),
        )

    # ④ learning unit → claim / component
    for unit in units or []:
        label = _display_label(_row_get(unit, "label"))
        for ref in _id_list(_row_get(unit, "linked_claim_ids")):
            if ref not in known_claims:
                _add("unit_claim", ref, label)
        for ref in _id_list(_row_get(unit, "linked_component_ids")):
            if ref not in known_components:
                _add("unit_component", ref, label)

    # ⑤ グラフの辺 → 導出ステップ
    for edge in graph_edges or []:
        if not isinstance(edge, Mapping):
            continue
        evidence = edge.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        label = _display_label(edge.get("relation"), edge.get("edge_type"))
        for ref in _id_list(evidence.get("evidence_derivation_ids")):
            if ref not in known_derivations:
                _add("graph_edge_derivation", ref, label)

    # ⑥ DSL の辺 → 主張・式（概念グラフの根拠）
    for edge in dsl_edges or []:
        if not isinstance(edge, Mapping):
            continue
        refs = edge.get("evidence_refs")
        refs = refs if isinstance(refs, Mapping) else {}
        label = _display_label(edge.get("verb"), edge.get("predicate"), edge.get("edge_type"))
        for ref in _id_list(refs.get("claim_ids")):
            if ref not in known_claims:
                _add("dsl_edge_evidence", ref, label)
        for ref in _id_list(refs.get("equation_ids")):
            if ref not in known_equations:
                _add("dsl_edge_evidence", ref, label)

    if not has_material:
        return unchecked_result(FACT_NO_MATERIAL, checked_at=checked_at)

    broken = {kind: entries for kind, entries in details.items() if kind in DETAIL_KINDS}
    facts = [KIND_FACTS[kind] for kind in DETAIL_KINDS if details.get(kind)]
    if not facts:
        facts = [FACT_OK]

    # ⑦ 存在の検査（欠落。status は変えない — 参照は解決していても「無い」ものは在る）。
    if details.get(GAP_DETACHED_DETAIL):
        facts.append(FACT_DETACHED_DETAIL)
    facts.extend(_collect_gaps(details, layer_presence, stage_outputs, completeness))

    return {
        "status": STATUS_BROKEN if broken else STATUS_OK,
        "checked_at": checked_at or _now_iso(),
        "facts": facts,
        "details": details,
    }


def _collect_gaps(
    details: dict[str, list[dict]],
    layer_presence: Mapping[str, Any] | None,
    stage_outputs: Mapping | None,
    completeness: Mapping | None,
) -> list[str]:
    """欠落（層が空 / 打ち切り / 未実行 / 完全性）を ``details`` に積み、事実文を返す。

    **破断ではない**ので ``status`` には影響しない。語彙の正本は :mod:`core.coverage_facts`。
    """
    facts: list[str] = []

    def _gap(kind: str, ref: str, from_label: str, fact: str) -> None:
        ref = _text(ref)
        if not ref:
            return
        bucket = details.setdefault(kind, [])
        entry = {"ref": ref, "from_label": from_label}
        if entry in bucket:
            return
        bucket.append(entry)
        if fact not in facts:
            facts.append(fact)

    if isinstance(layer_presence, Mapping):
        for layer in coverage_facts.LAYER_KEYS:
            if layer not in layer_presence:
                continue  # 見ていない層は「無い」と言わない（未検査 ≠ 空）
            if layer_presence.get(layer):
                continue
            _gap(
                GAP_EMPTY_LAYERS,
                layer,
                coverage_facts.LAYER_LABELS.get(layer, ""),
                coverage_facts.empty_layer_fact(layer),
            )

    for gap_kind, stage in coverage_facts.iter_stage_coverage_gaps(stage_outputs):
        fact = (
            coverage_facts.truncated_stage_fact(stage)
            if gap_kind == coverage_facts.GAP_TRUNCATED
            else coverage_facts.skipped_stage_fact(stage)
        )
        _gap(gap_kind, stage, coverage_facts.stage_label(stage), fact)

    if isinstance(completeness, Mapping) and completeness.get("complete") is not True:
        reasons = completeness.get("review_reasons")
        if isinstance(reasons, (list, tuple)):
            for reason in reasons:
                key = _text(reason)
                _gap(GAP_COMPLETENESS, key, "", coverage_facts.completeness_fact(key))

    return facts


# ---------------------------------------------------------------------------
# DB 読み部（live ビューのみ・書き込みなし）
# ---------------------------------------------------------------------------

_CLAIMS_SQL = """
    SELECT id::text AS id, agent_claim_id, source_scope, chunk_id::text AS chunk_id,
           text, origin
    FROM theory_claims_live
    WHERE document_id = CAST(:document_id AS uuid)
"""

_COMPONENTS_SQL = """
    SELECT id::text AS id, agent_component_id, name, source_scope,
           evidence_claims, linked_claim_ids, linked_equation_ids
    FROM theory_components_live
    WHERE document_id = CAST(:document_id AS uuid)
"""

# knowledge_equations には live ビューが無いので superseded_at を明示で外す（KO5 と同じ意味）。
_EQUATIONS_SQL = """
    SELECT id::text AS id, agent_equation_id, label
    FROM knowledge_equations
    WHERE document_id = CAST(:document_id AS uuid) AND superseded_at IS NULL
"""

_UNITS_SQL = """
    SELECT id::text AS id, label, linked_claim_ids, linked_component_ids
    FROM learning_units_live
    WHERE document_id = CAST(:document_id AS uuid)
"""

_GRAPH_SQL = """
    SELECT graph_json
    FROM theory_component_graphs
    WHERE document_id = CAST(:document_id AS uuid)
    ORDER BY updated_at DESC
    LIMIT 1
"""

# knowledge_derivation_steps にも live ビューは無いので superseded_at を明示で外す。
_DERIVATIONS_SQL = """
    SELECT id::text AS id, agent_derivation_id, agent_step_id, stable_key
    FROM knowledge_derivation_steps
    WHERE document_id = CAST(:document_id AS uuid) AND superseded_at IS NULL
"""

# 層がまるごと空かどうかの存在確認（行を引かずに有無だけを見る）。
# 要素の説明は「議論のきっかけ」（role='discussion_seed'）を除いた本体だけを数える。
_PRESENCE_SQL = """
    SELECT
      EXISTS (SELECT 1 FROM document_figures f
              WHERE f.document_id = CAST(:document_id AS uuid)) AS figures,
      EXISTS (SELECT 1 FROM element_explanations e
              WHERE e.document_id = CAST(:document_id AS uuid)
                AND e.kind = 'contextual' AND e.role IS NULL
                AND e.status IN ('candidate', 'approved')) AS element_explanations
"""

#: 取り込みの完全性レポートの artifact stage 名
#: （``core/document_pipeline/orchestrator.py::_record_document_completeness``）。
COMPLETENESS_STAGE = "document_completeness"


def _rows(session: Any, sql: str, document_id: str) -> list[Any]:
    return list(
        session.execute(sa_text(sql), {"document_id": document_id}).mappings().all()
    )


def _derivation_rows(session: Any, document_id: str) -> list[Any] | None:
    """導出ステップの live 行。読めなければ ``None``（= 検査しない・空とも言わない）。"""
    try:
        return _rows(session, _DERIVATIONS_SQL, document_id)
    except Exception:  # noqa: BLE001 — 読めない層を「切れている」とは言わない（fail-soft）
        logger.warning(
            "reference_health: derivation lookup failed for document %s", document_id,
            exc_info=True,
        )
        return None


def _graph(session: Any, document_id: str) -> Mapping:
    row = session.execute(sa_text(_GRAPH_SQL), {"document_id": document_id}).fetchone()
    if not row:
        return {}
    graph = row[0]
    return graph if isinstance(graph, Mapping) else {}


def _graph_list(graph: Mapping, *path: str) -> list[Any]:
    """graph_json の ``nodes`` / ``edges`` / ``dsl.edges`` を安全に取り出す。"""
    node: Any = graph
    for key in path:
        if not isinstance(node, Mapping):
            return []
        node = node.get(key)
    return list(node) if isinstance(node, list) else []


def _layer_presence(
    session: Any,
    document_id: str,
    *,
    equations: Sequence[Any],
    units: Sequence[Any],
    derivations: Sequence[Any] | None,
    graph_nodes: Sequence[Any],
) -> dict[str, bool]:
    """層ごとの有無（存在の検査の入力）。読めなかった層はキーごと落とす（未検査 ≠ 空）。"""
    presence: dict[str, bool] = {
        "graph_main": any(
            isinstance(node, Mapping)
            and (_text(node.get("graph_layer")) or "main") == "main"
            for node in graph_nodes or []
        ),
        "equations": bool(equations),
        "learning_units": bool(units),
    }
    if derivations is not None:
        presence["derivation_steps"] = bool(derivations)
    try:
        row = session.execute(
            sa_text(_PRESENCE_SQL), {"document_id": document_id}
        ).mappings().fetchone()
    except Exception:  # noqa: BLE001 — 読めない層は「無い」と言わない（fail-soft）
        logger.warning(
            "reference_health: presence probe failed for document %s", document_id,
            exc_info=True,
        )
        return presence
    if row:
        presence["figures"] = bool(row.get("figures"))
        presence["element_explanations"] = bool(row.get("element_explanations"))
    return presence


def _run_context(session: Any, document_id: str) -> tuple[Mapping | None, Mapping | None]:
    """打ち切り・未実行・完全性の材料（``stage_outputs`` と完全性 artifact）。

    run の選択は **自前 SQL を書かず** ``persistence.resolve_artifact_runs`` の
    1 語彙（``adopted`` / ``latest``）に委ねる（C-8: run 選択ポリシを分裂させない）。
    既定は採用 run。ただし**最新 run が走行中**のときはそちらを見る — パイプライン完了
    直前のスナップショットはその走行中の run を説明しており、採用ポインタはまだ前の run を
    指しているため（``latest`` は created_at 降順の 1 件なので、古い走行中 run が新しい
    採用 run を隠すことはない）。

    読めなければ ``(None, None)``（= 存在の検査をしない。未確認を「問題なし」と言わない）。
    """
    try:
        from core.document_pipeline import persistence  # 遅延 import（重い依存を持ち込まない）

        adopted = persistence.resolve_artifact_runs(
            session, [document_id], policy="adopted"
        ).get(document_id)
        latest = persistence.resolve_artifact_runs(
            session, [document_id], policy="latest"
        ).get(document_id)
    except Exception:  # noqa: BLE001 — 材料が読めないだけで検査全体を落とさない
        logger.warning(
            "reference_health: run context lookup failed for document %s", document_id,
            exc_info=True,
        )
        return (None, None)
    chosen = adopted
    if latest and _text(latest.get("status")) == "running":
        chosen = latest
    if not chosen:
        chosen = latest
    if not isinstance(chosen, Mapping):
        return (None, None)
    stage_outputs = _as_mapping(chosen.get("stage_outputs"))
    # ``resolve_artifact_runs`` は生成ログ表の artifact を stage_outputs["_artifacts"] へ
    # hydrate して返す（KO6。キー名は persistence.ARTIFACTS_KEY と同じ）。
    artifacts = _as_mapping((stage_outputs or {}).get("_artifacts"))
    completeness = _as_mapping((artifacts or {}).get(COMPLETENESS_STAGE))
    return (stage_outputs, completeness)


def _as_mapping(value: Any) -> Mapping | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def check_document_references(session: Any, document_id: str) -> dict:
    """1 論文の参照の健全性を検査する（読み取り専用・LLM 0 回）。

    Args:
        session: DB セッション（live ビューだけを SELECT する。**書き込まない**）。
        document_id: ``documents.id``（UUID）。material_id 形は呼び出し側で解決しておく。

    Returns:
        :func:`build_reference_health` の戻り値。検査できなかったとき（document_id 空 /
        DB 例外）は :func:`unchecked_result`（fail-soft — 呼び出し側を止めない）。
    """
    doc_id = _text(document_id)
    if not doc_id:
        return unchecked_result()
    try:
        equations = _rows(session, _EQUATIONS_SQL, doc_id)
        units = _rows(session, _UNITS_SQL, doc_id)
        derivations = _derivation_rows(session, doc_id)
        graph = _graph(session, doc_id)
        graph_nodes = _graph_list(graph, "nodes")
        stage_outputs, completeness = _run_context(session, doc_id)
        return build_reference_health(
            claims=_rows(session, _CLAIMS_SQL, doc_id),
            components=_rows(session, _COMPONENTS_SQL, doc_id),
            equations=equations,
            units=units,
            graph_nodes=graph_nodes,
            # 導出ステップを読めなかったときは辺の根拠を検査しない（「読めない」を
            # 「切れている」と言い換えない）。
            graph_edges=_graph_list(graph, "edges") if derivations is not None else (),
            dsl_edges=_graph_list(graph, "dsl", "edges"),
            derivations=derivations or (),
            layer_presence=_layer_presence(
                session,
                doc_id,
                equations=equations,
                units=units,
                derivations=derivations,
                graph_nodes=graph_nodes,
            ),
            stage_outputs=stage_outputs,
            completeness=completeness,
        )
    except Exception:  # noqa: BLE001 — 検査は best-effort（KT5: 未確認は未確認と言う）
        logger.warning(
            "reference_health: check failed for document %s", doc_id, exc_info=True
        )
        return unchecked_result()


def load_recorded_reference_health(session: Any, document_id: str) -> dict | None:
    """run に**保存済み**の検査結果を読む（``stage_outputs.reference_health``）。

    照会 API の既定の読み口（P4-R10）。毎回の再検査はグラフ・主張・部品・単位を
    全走査するため、教材を開くたびに同じ全走査を走らせない。採用 run があればそれを、
    無ければ「この教材の最新の run で ``reference_health`` を持つもの」を読む。

    Returns:
        保存済みの検査結果（``SOURCE_RECORDED`` を付けた dict）。保存が無い・読めない
        ときは ``None``（呼び出し側が「まだ確認されていません」を返すか再検査する）。
    """
    doc_id = _text(document_id)
    if not doc_id:
        return None
    try:
        row = session.execute(
            sa_text(
                """
                SELECT r.stage_outputs -> 'reference_health' AS health,
                       r.id::text AS run_id
                FROM document_analysis_runs r
                LEFT JOIN documents d ON d.id = r.document_id
                WHERE r.document_id = CAST(:doc AS uuid)
                  AND r.stage_outputs -> 'reference_health' IS NOT NULL
                ORDER BY (d.active_analysis_run_id = r.id) DESC NULLS LAST,
                         r.completed_at DESC NULLS LAST,
                         r.created_at DESC,
                         r.id DESC
                LIMIT 1
                """
            ),
            {"doc": doc_id},
        ).fetchone()
    except Exception:  # noqa: BLE001 — 読めなければ「保存が無い」と同じ扱い（fail-soft）
        logger.warning(
            "reference_health: recorded lookup failed for document %s", doc_id, exc_info=True
        )
        return None
    if not row or not row[0]:
        return None
    health = row[0]
    if isinstance(health, str):
        try:
            health = json.loads(health)
        except ValueError:
            return None
    if not isinstance(health, dict) or not health.get("status"):
        return None
    facts = health.get("facts")
    if isinstance(facts, (list, tuple)) and LEGACY_FACT_OK in facts:
        # 旧版の検査（参照の破断のみ）の結果。そのまま返すと「壊れていません」と
        # 読めてしまうので、保存が無いのと同じ扱いにして呼び出し側に再検査させる。
        return None
    out = dict(health)
    out["source"] = SOURCE_RECORDED
    out["run_id"] = _text(row[1])
    return out
