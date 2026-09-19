"""参照の健全性（知識の転用層 P4-3）。

正本: ``docs/features/knowledge_transfer_design.md`` §6。親文書の診断 C-14（束を作った
ときにしか走らない ``check_refs`` しか ID 破断を検出する仕組みが無い）への是正。

**何を検査するか**（すべて live 行・DB のみ・純 SQL。LLM も embedding も呼ばない = KT3）:

1. 理論操作グラフ（``theory_component_graphs`` の最新行）の main / equation_detail
   ノードの ``linked_claim_ids`` が ``theory_claims_live`` に着地しているか。
2. ``theory_components_live`` の ``evidence_claims`` / ``linked_claim_ids`` が claims に、
   ``linked_equation_ids`` が ``knowledge_equations``（live 行）に着地しているか。
3. ``theory_claims_live.chunk_id IS NULL``（出典チャンクに着地していない主張）。
   ただし ``origin='equation_synthesis'``（式から合成された主張）は本文のチャンクから
   切り出されたものではないので対象外（V-9: 常時赤の計器を作らない）。
4. ``learning_units_live`` の ``linked_claim_ids`` / ``linked_component_ids`` が
   各表に着地しているか。

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

logger = logging.getLogger(__name__)

__all__ = [
    "DETAIL_KINDS",
    "FACT_NOT_CHECKED",
    "FACT_NO_MATERIAL",
    "FACT_OK",
    "KIND_FACTS",
    "SOURCE_RECHECKED",
    "SOURCE_RECORDED",
    "STATUS_BROKEN",
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
    "component_claim",
    "component_equation",
    "claim_without_chunk",
    "unit_claim",
    "unit_component",
)

#: 事実文（固定文・言い換えない・**数字を書かない** = T-3）。
FACT_OK = "参照の切れはありません。"
FACT_NO_MATERIAL = "この教材にはまだ解析結果がないため、参照の整合は確認できません。"
FACT_NOT_CHECKED = "参照の整合はまだ確認されていません。"

KIND_FACTS: dict[str, str] = {
    "graph_node_claim": "理論操作グラフのノードが参照している主張のうち、見つからないものがあります。",
    "component_claim": "コンポーネントが根拠にしている主張のうち、見つからないものがあります。",
    "component_equation": "コンポーネントが参照している式のうち、見つからないものがあります。",
    "claim_without_chunk": "出典チャンクに結び付いていない主張があります。",
    "unit_claim": "学ぶ単位が参照している主張のうち、見つからないものがあります。",
    "unit_component": "学ぶ単位が参照しているコンポーネントのうち、見つからないものがあります。",
}

#: ``chunk_id`` を持たないのが**正常**な claim の由来（V-9）。式から合成された claim は
#: 本文のチャンクから切り出されていないので、出典チャンク未着地を破断に数えない。
_CHUNKLESS_CLAIM_ORIGINS: frozenset[str] = frozenset({"equation_synthesis"})

#: グラフのどの層を検査するか（``debug`` 層は fallback / inferred の置き場なので対象外）。
CHECKED_GRAPH_LAYERS = ("main", "equation_detail")

#: ``from_label`` に出す本文の長さ（表示用の切り詰め。件数ではないので数値は外に出ない）。
_LABEL_SNIPPET_MAX = 80

#: 「裸の内部 ID」を表示ラベルに出さないための判定（UUID / agent 側 ID 形）。
#: ``core.learner_context_common`` の同種の判定は学習者向け射影の正本だが、本モジュールは
#: 教員向け運用情報であり依存を増やしたくないため、ここでは最小限の局所判定に留める。
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_INTERNAL_ID_RE = re.compile(
    r"^(?:eq|ev|eq_op|theory_op|comp|span|step|synth_claim|claim|node|blk|sec)[_\-]?\w*\d[\w\-.]*$",
    re.IGNORECASE,
)


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
        checked_at: 検査時刻（ISO 文字列）。省略時は現在時刻。

    Returns:
        ``{"status", "checked_at", "facts", "details"}``。``facts`` に数字は入らない（T-3）。
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

    has_material = bool(claims or components or equations or units or graph_nodes)

    # ① グラフノード → claim
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

    if not has_material:
        return unchecked_result(FACT_NO_MATERIAL, checked_at=checked_at)

    facts = [KIND_FACTS[kind] for kind in DETAIL_KINDS if details.get(kind)]
    if not facts:
        facts = [FACT_OK]
    return {
        "status": STATUS_BROKEN if details else STATUS_OK,
        "checked_at": checked_at or _now_iso(),
        "facts": facts,
        "details": details,
    }


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


def _rows(session: Any, sql: str, document_id: str) -> list[Any]:
    return list(
        session.execute(sa_text(sql), {"document_id": document_id}).mappings().all()
    )


def _graph_nodes(session: Any, document_id: str) -> list[Any]:
    row = session.execute(sa_text(_GRAPH_SQL), {"document_id": document_id}).fetchone()
    if not row:
        return []
    graph = row[0]
    if not isinstance(graph, Mapping):
        return []
    nodes = graph.get("nodes")
    return list(nodes) if isinstance(nodes, list) else []


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
        return build_reference_health(
            claims=_rows(session, _CLAIMS_SQL, doc_id),
            components=_rows(session, _COMPONENTS_SQL, doc_id),
            equations=_rows(session, _EQUATIONS_SQL, doc_id),
            units=_rows(session, _UNITS_SQL, doc_id),
            graph_nodes=_graph_nodes(session, doc_id),
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
    out = dict(health)
    out["source"] = SOURCE_RECORDED
    out["run_id"] = _text(row[1])
    return out
