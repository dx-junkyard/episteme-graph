"""学習者向け claim / equation 文脈 API の core ロジック（非LLM・読み取り専用）。

設計書: ``docs/features/learner_element_context_design.md``。

``core/component_context.py`` が component について実現した「コーススコープ・
fail-closed のオンデマンド文脈 API」を、``claim`` と ``equation`` へ広げる。
component の文脈 DTO が instance / shared_part / graph の三層構造を持つのに対し、
本モジュールは **W層の要素中心コンテキストレンズ
（``core/deliberation/context_lens.py``）の投影を学習者向けにフィルタして返すだけ**
に責務を絞る（claim / equation には ComponentRecord のような固有の rich 投影が
無く、上位・下位の関係そのものが提示価値の中心だからである）。

不変条項（設計書 §2）:

- **fail-closed**: 要素の ``document_id`` がコースの document 集合
  （呼び出し側が渡す ``course_document_ids``、通常は
  ``routes/lecture.py::_course_document_ids`` の戻り値）に含まれない場合は
  SQL の時点で除外し ``None`` を返す（コース外文書の要素は解決自体が失敗する）。
- **candidate を学習者に出さない**: ``upper`` / ``lower`` のうち
  ``relation_status == "candidate"``（AI が提案した未確定の関係）は除外し、
  ``focus.contextual_role`` も source_backed / confirmed のときだけ残す
  （``component_context._build_graph`` の graph 射影と同じ原則）。
- **数値を見せない（W8）**: 最終レスポンスを再帰走査して ``"confidence"`` キーを
  除去する（``core/learner_context_common.py::strip_confidence`` を component 文脈
  API と共有）。
- **裸の内部 ID を出さない**: ITEM の ``evidence_refs``（evidence_id / step_id 等の
  内部参照）と ``focus.provenance``（``theory_claims:<uuid>`` 等）は落とし、さらに
  **ラベルそのものが内部 ID 形の場合は一般ラベルへ置換する**（W層はラベル解決に
  失敗した項目に内部 ID をそのまま入れる — 図の DB UUID・``ev_0001``・
  ``synth_claim_0001`` 等）。
- **書き込みを行わない**: A層（``src/episteme_graph/agents/``）・W層のコードは
  読むだけで変更しない。本モジュールに書き込み経路は無い。
- **共通プリミティブは ``core/learner_context_common.py`` が正本**: 数値除去・
  コース document スコープの SQL 強制（``scoped_id_match_sql``）・ITEM の射影と
  遮断層（``project_item`` / ``visible_lane_items``）は component 文脈 API
  （``core/component_context.py``）と共有する。本モジュールは旧名で再エクスポート
  するだけなので、遮断が片方の API にしか実装されない状態には戻らない。
- 本モジュールは FastAPI を import しない（開発ルール2 / core/ 共通ルール）。

ITEM v2 / focus v2（``docs/features/element_context_presentation_redesign.md`` §4）:

W層 ``context_lens`` が ``sublabel`` / ``qualifier`` / ``group`` / ``unresolved`` /
``label_source`` と、focus の ``headline`` / ``intrinsic`` / ``placement`` /
``contextual_role_source`` / ``review_notes`` / ``derivations`` を載せるようになった。
本モジュールは**権限フィルタ**として次を行う（LE6′「可読性の正本は W層、射影側は
権限フィルタのみ」。遮断層は W層の改修後も**二重防御として維持**する）:

- 透過: ``sublabel`` / ``qualifier`` / ``group`` / ``unresolved`` / ``headline`` /
  ``intrinsic`` / ``placement`` / ``contextual_role_source`` / ``derivations``
- 落とす: ``label_source``（来歴は教員のみ）/ ``review_notes``（要確認は教員のみ）/
  ``evidence_refs``・``relation``（内部参照・内部語彙）/ ``qualifier ==
  "equation_detail"`` の ITEM **全体**（traceability 層は学習者に見せない・CP3）/
  ``derivations[].derivation_id``・``evidence_refs``
- 遮断（EC3′ の「最後の砦」）: 内部 ID / 生 TeX を含む表示文字列は空にする。
  ただし**記号（symbol）の label は TeX でも遮断しない** — 記号は式の再掲ではなく
  読解の部品で、レンダリング可否はフロントの ``looksLikeRenderableTex`` ゲートが
  判断する（§5.4）。一般ラベルへ置換したときは ``unresolved`` を立て、
  **``sublabel`` は保持する**（「関連する数式」が2件並んで区別不能になる RC6 の再発防止）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core import learner_context_common
from core.learner_context_common import (  # noqa: F401  (旧名の再エクスポート)
    ITEM_GROUP_FALLBACK,
    ITEM_GROUPS,
    PROVENANCE_COURSE_FREEZE,
    QUALIFIER_EQUATION_DETAIL,
    contains_internal_id as _contains_internal_id,
    equation_focus_label as _equation_focus_label,
    generic_item_label as _generic_item_label,
    is_internal_id_label as _is_internal_id_label,
    is_uuid as _is_uuid,
    json_list as _json_list,
    learner_navigable as _learner_navigable,
    normalized_document_ids as _normalized_document_ids,
    normalized_group as _normalized_group,
    project_item as _project_item,
    safe_text as _safe_text,
    scoped_id_match_sql,
    strip_confidence,
)
from core.text_excerpt import excerpt, looks_like_tex_math
from core.deliberation import context_lens as context_lens_mod
from core.deliberation.refs import document_run_artifacts, equation_records
from core.deliberation.schema import (  # noqa: F401  (ELEMENT_* は語彙表と再エクスポート)
    CONTEXT_ROLE_STATUS_UNIDENTIFIED,
    CONTEXT_STATUS_CANDIDATE,
    CONTEXT_STATUS_CONFIRMED,
    CONTEXT_STATUS_SOURCE_BACKED,
    ELEMENT_DERIVATION,
    ELEMENT_EQUATION,
    ELEMENT_EVIDENCE,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    SCOPE_DOCUMENT,
    ElementRef,
)

logger = logging.getLogger(__name__)

# ── 公開 element_type 語彙（API パスに現れる値）─────────────────────────────
# 学習者向け API は W層の内部語彙（``theory_claim``）ではなく短い ``claim`` を使う。
# それ以外の値は呼び出し側が 404 にする（学習者 API の 404 統一方針）。
ELEMENT_TYPE_CLAIM = "claim"
ELEMENT_TYPE_EQUATION = "equation"
SUPPORTED_ELEMENT_TYPES = (ELEMENT_TYPE_CLAIM, ELEMENT_TYPE_EQUATION)

# 公開語彙 → W層内部語彙。
_INTERNAL_ELEMENT_TYPES = {
    ELEMENT_TYPE_CLAIM: ELEMENT_THEORY_CLAIM,
    ELEMENT_TYPE_EQUATION: ELEMENT_EQUATION,
}

# 学習者に出してよい関係状態（candidate は教員確定前の AI 候補なので出さない）。
_LEARNER_VISIBLE_STATUSES = (CONTEXT_STATUS_SOURCE_BACKED, CONTEXT_STATUS_CONFIRMED)

# 各レーンの表示上限（正本は ``learner_context_common.LANE_MAX``。旧名を再エクスポート
# して既存の参照面を維持する）。W層 ``_cap_lane`` が candidate 込みで既に 20 件へ切った
# あとに本フィルタが走るため、実際の表示件数は 20 件以下になり得る（W層を変更しない
# （LE6）ことを優先した既知の挙動。設計書 §4）。
_LANE_MAX = learner_context_common.LANE_MAX

# 学習者が「旅の続き」として実際に再フェッチできる element_type / 明示的な拒否リスト
# （正本は ``learner_context_common``。旧名の再エクスポート）。
_LEARNER_NAVIGABLE_ELEMENT_TYPES = learner_context_common.LEARNER_NAVIGABLE_ELEMENT_TYPES
_LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES = (
    learner_context_common.LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES
)

# W層 ITEM の表示専用 element_type（``deliberation/schema.py`` の解決対象語彙には無い）。
_ITEM_TYPE_SYMBOL = learner_context_common.ITEM_TYPE_SYMBOL

# context_lens が投影を返せない / 例外だった場合の事実文（available:false 時の note）。
NOTE_NO_CONTEXT = "この要素の文脈情報は現在表示できません。"

# ``focus.contextual_role`` の内部 ID 検出（``_project_focus`` が role をキーごと落とす
# 判定に使う。正本は ``learner_context_common``）。
_ROLE_INTERNAL_TOKEN_RE = learner_context_common.ROLE_INTERNAL_TOKEN_RE

# W層 ``context_lens._degenerate_result`` の目印（要素の読み取りに失敗した fail-soft 形）。
# この文言は context_lens.py 内で degenerate 専用に1箇所しか使われていない。
_DEGENERATE_LENS_NOTE = "要素が見つからないか読み取りに失敗したため、文脈は表示できません"


# ---------------------------------------------------------------------------
# 要素解決（コース document スコープを SQL / 走査対象で強制する）
# ---------------------------------------------------------------------------

# 曖昧判定に必要なのは「2 行以上あるか」だけなので、候補は少数で足りる
# （legacy_ids が大量一致するケースで巨大な結果集合を作らない）。
_CLAIM_CANDIDATE_LIMIT = 3


def _resolve_claim(element_id: str, course_document_ids: set[str]) -> tuple[str, str] | None:
    """claim を ``(db_uuid, document_id)`` に解決する（コース document 集合内に限定）。

    ``document_id = ANY(:doc_ids)`` を WHERE 句に直接含めることで（後付けの Python
    フィルタではなく）、agent 側 ID がコース外文書の同名 claim に誤って一致する余地を
    断つ。WHERE 断片の組み立ては
    ``learner_context_common.scoped_id_match_sql``（component 側の
    ``_resolve_component_row`` と共有する正本）に委ね、本関数は claim 固有の
    **曖昧一致の扱い**だけを持つ。

    **agent 側 ID の曖昧一致は解決しない**（fail-closed）。``claim_span_001`` のような
    agent 側 ID は span_id が block ごとに振り直されるため文書内でも文書間でも反復し
    （``claim_object_builder/builder.py::_make_claim_id`` の legacy_ids はカウンタ接尾辞
    を持たない）、複数文書をソースに持つコースでは**別論文の別 claim** に一致し得る。
    W層 ``core/deliberation/refs.py::_resolve_by_legacy_id`` が「単一 document 必須」で
    同じ問題を断っているのと整合させ、本モジュールでは

    - UUID（``theory_claims.id``）完全一致 … 一意なので即決
    - legacy_ids 一致が **ちょうど1行** … 解決
    - legacy_ids 一致が複数行（別 document にまたがる / 同一 document 内で複数） … 曖昧
      なので ``None``（呼び出し側が 404 → フロントは「文脈情報はまだありません。」へ縮退）

    とする。「たまたま最初に返った行」を出典に裏付けられた文脈として見せない。
    """
    document_ids = _normalized_document_ids(course_document_ids)
    if not document_ids:
        return None

    where_clause, params = scoped_id_match_sql(element_id, document_ids)

    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT id::text AS id, document_id, (id::text = :raw_id) AS id_match
                FROM theory_claims
                WHERE document_id = ANY(:doc_ids) AND ({where_clause})
                ORDER BY (id::text = :raw_id) DESC, document_id ASC, created_at ASC, id::text ASC
                LIMIT {_CLAIM_CANDIDATE_LIMIT}
                """
            ),
            params,
        ).mappings().fetchall()
    finally:
        session.close()
    candidates = [dict(r) for r in (rows or [])]
    if not candidates:
        return None
    first = candidates[0]
    if not first.get("id_match") and len(candidates) > 1:
        # agent 側 ID が複数行に一致した = どの claim を指すか決められない（fail-closed）。
        logger.info(
            "element_context: ambiguous agent claim id %r matched %d rows in course scope",
            str(element_id),
            len(candidates),
        )
        return None
    return str(first["id"]), str(first["document_id"] or "")


def _resolve_equation(element_id: str, course_document_ids: set[str]) -> tuple[str, str] | None:
    """equation を ``(equation_id, document_id)`` に解決する。

    equation は独立テーブルを持たない（W層設計 §2）ため、コースの document 集合を
    順に走査し ``equation_semantics`` artifact の ``equations[].equation_id`` に一致する
    最初の document を採用する（``core/deliberation/refs.py::_resolve_equation`` と
    同じ存在確認）。走査対象がコース document 集合そのものなので、コース外文書の
    equation は原理的に解決されない（fail-closed）。1 document の artifact 読み取りが
    失敗しても他 document の走査は続ける（fail-soft）。

    ``document_run_artifacts``（巨大 JSONB の SELECT）は document ごとに1回だけ読み、
    ``equation_records(doc, artifacts=...)`` に渡して再取得を省く。
    """
    raw_id = str(element_id)
    for document_id in _normalized_document_ids(course_document_ids):
        try:
            records = equation_records(
                document_id, artifacts=document_run_artifacts(document_id)
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "element_context: equation records read failed for document %s",
                document_id,
                exc_info=True,
            )
            continue
        for record in records:
            if isinstance(record, dict) and str(record.get("equation_id") or "") == raw_id:
                return raw_id, document_id
    return None


def _resolve_element(
    element_type: str, element_id: str, course_document_ids: set[str]
) -> tuple[str, str] | None:
    if element_type == ELEMENT_TYPE_CLAIM:
        return _resolve_claim(element_id, course_document_ids)
    if element_type == ELEMENT_TYPE_EQUATION:
        return _resolve_equation(element_id, course_document_ids)
    return None


# ---------------------------------------------------------------------------
# 学習者向けフィルタ（candidate 除外 / role 抑止 / 内部 ID 除去）
#
# 遮断層（``_is_internal_id_label`` / ``_generic_item_label`` /
# ``_contains_internal_id`` / ``_safe_text`` / ``_normalized_group`` /
# ``_equation_focus_label`` / ``_learner_navigable`` / ``_project_item``）の正本は
# ``core/learner_context_common.py`` にあり、本モジュールは旧名で再エクスポート
# している（import 節参照）。component 文脈 API も同じ関数を通るため、遮断が
# 片方の API にだけ実装される状態には戻らない。
# ---------------------------------------------------------------------------


def _equation_explanatory_fields(record: Any) -> dict:
    """equations.json のレコードから「役割 / 意味の要約 / 記号の意味」を取り出す。

    ホバー（``equation_hover_content_design.md`` §3.1）と**同じ材料**を返す（EC5）。
    語彙キーのまま返し、日本語表示名への変換はフロント（``element-vocab.js``）に
    委ねる。意味が解決できていない記号は落とす（推測で埋めない）。数値
    （confidence）は載せない。
    """
    if not isinstance(record, dict):
        return {}
    semantics = record.get("semantics") if isinstance(record.get("semantics"), dict) else {}
    out: dict[str, Any] = {}
    role = str(record.get("role_in_argument") or semantics.get("role_in_argument") or "").strip()
    if role:
        out["role_in_argument"] = role
    semantic_kind = str(record.get("semantic_kind") or semantics.get("summary") or "").strip()
    if semantic_kind:
        out["semantic_kind"] = excerpt(semantic_kind, 120)
    symbols: list[dict] = []
    seen: set[str] = set()
    raw_symbols = _json_list(record.get("symbols")) + _json_list(semantics.get("defined_symbols"))
    for raw in raw_symbols:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip()
        meaning = str(raw.get("meaning") or "").strip()
        if not symbol or not meaning or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append({"symbol": symbol, "meaning": excerpt(meaning, 60)})
        if len(symbols) >= 6:
            break
    if symbols:
        out["symbols"] = symbols
    return out


def _equation_record_in_course(element_id: str, document_id: str) -> dict | None:
    """解決済みの数式レコードを1件読む（説明材料の取り出し用・fail-soft）。"""
    try:
        records = equation_records(document_id, artifacts=document_run_artifacts(document_id))
    except Exception:  # noqa: BLE001
        logger.warning(
            "element_context: equation record read failed for document %s", document_id, exc_info=True
        )
        return None
    for record in records:
        if isinstance(record, dict) and str(record.get("equation_id") or "") == str(element_id):
            return record
    return None


def _visible_items(items: Any) -> list[dict]:
    """candidate（LE2）と式の詳細層（CP3）を除外して射影し、レーン上限で切る。

    実体は component 文脈 API と共有する
    ``learner_context_common.visible_lane_items``（ITEM v2 のキー集合で射影する）。
    """
    return learner_context_common.visible_lane_items(items)


def _project_intrinsic(value: Any) -> dict:
    """focus.intrinsic（①これは何か）を射影する（§4.2）。

    統制語彙キー（``kind_key`` / ``role_key`` / ``definition_status``）はそのまま渡し、
    訳はフロント（``element-vocab.js``）に委ねる（CP4）。自由文は内部 ID / 生 TeX を
    遮断する。**記号そのもの（``symbols[].symbol``）だけは TeX でも通す**（§5.4）。
    値が無いキーは載せない（推測で穴埋めしない）。
    """
    data = value if isinstance(value, dict) else {}
    out: dict[str, Any] = {}

    for key in ("kind_key", "role_key"):
        raw = str(data.get(key) or "").strip()
        if raw:
            out[key] = raw

    summary = _safe_text(data.get("summary"))
    if summary:
        out["summary"] = summary
        # 「（論文の原文）」注記の出し分け（表示側の責務。値そのものは訳さない）。
        out["summary_is_source_language"] = bool(data.get("summary_is_source_language"))

    reading = _safe_text(data.get("reading"))
    if reading:
        out["reading"] = reading

    symbols: list[dict] = []
    for raw in _json_list(data.get("symbols")):
        if not isinstance(raw, dict):
            continue
        symbol = _safe_text(raw.get("symbol"), allow_tex=True)
        if not symbol:
            continue
        entry: dict[str, Any] = {"symbol": symbol}
        meaning = _safe_text(raw.get("meaning"))
        if meaning:
            entry["meaning"] = meaning
        if raw.get("defined_here") is not None:
            entry["defined_here"] = bool(raw.get("defined_here"))
        definition_status = str(raw.get("definition_status") or "").strip()
        if definition_status:
            entry["definition_status"] = definition_status
        symbols.append(entry)
    if symbols:
        out["symbols"] = symbols

    for key in ("conditions", "facts"):
        values = [_safe_text(v) for v in _json_list(data.get(key))]
        values = [v for v in values if v]
        if values:
            out[key] = values

    return out


def _project_placement(value: Any) -> dict:
    """focus.placement（②この論文での位置づけ）を射影する（§4.2）。"""
    data = value if isinstance(value, dict) else {}
    out: dict[str, Any] = {}

    section_label = _safe_text(data.get("section_label"))
    if section_label:
        out["section_label"] = section_label

    stage = data.get("stage") if isinstance(data.get("stage"), dict) else {}
    stage_key = str(stage.get("key") or "").strip()
    stage_description = _safe_text(stage.get("description"))
    if stage_key or stage_description:
        projected_stage: dict[str, Any] = {}
        if stage_key:
            projected_stage["key"] = stage_key
        if stage_description:
            projected_stage["description"] = stage_description
        out["stage"] = projected_stage

    thesis_role = _safe_text(data.get("thesis_role"))
    if thesis_role:
        out["thesis_role"] = thesis_role

    return out


def _project_mini_ref(value: Any) -> dict | None:
    """derivations[].inputs / outputs の MINI 参照を射影する。

    ``navigable`` は学習者ホワイトリストで再計算し、TeX ラベル・内部 ID ラベルは
    element_type 別の一般ラベルへ置換する（項目自体は落とさない）。
    """
    data = value if isinstance(value, dict) else {}
    element_type = str(data.get("element_type") or "")
    element_id = data.get("element_id")
    label = str(data.get("label") or "")
    if not element_type and not str(element_id or "").strip() and not label:
        return None
    is_symbol = element_type == _ITEM_TYPE_SYMBOL  # 記号は TeX でも遮断しない（§5.4）
    if (not is_symbol and looks_like_tex_math(label)) or _is_internal_id_label(
        label, element_type, element_id
    ):
        label = _generic_item_label(element_type)
    return {
        "element_type": element_type,
        "element_id": element_id,
        "label": label,
        "navigable": _learner_navigable(element_type, element_id),
    }


def _project_derivation(value: Any) -> dict | None:
    """focus.derivations[] のストーリーカード1件を射影する（§4.4）。

    candidate（AI 候補の関係）は出さない（LE2）。``derivation_id`` / ``evidence_refs``
    は内部参照なので落とす。``chain_type`` / ``focus_role`` は統制語彙キーのまま渡す。
    """
    data = value if isinstance(value, dict) else {}
    if not data:
        return None
    relation_status = data.get("relation_status")
    if relation_status == CONTEXT_STATUS_CANDIDATE:
        return None

    label = _safe_text(data.get("label")) or _generic_item_label(ELEMENT_DERIVATION)
    out: dict[str, Any] = {
        "label": label,
        "sublabel": _safe_text(data.get("sublabel")),
        "chain_type": str(data.get("chain_type") or "").strip(),
        "operation_text": _safe_text(data.get("operation_text")),
        "focus_role": str(data.get("focus_role") or "").strip(),
        "inputs": [],
        "outputs": [],
        "reason": _safe_text(data.get("reason")),
        "relation_status": relation_status,
        # 導出には学習者向けの文脈取得 API が無い（旅の対象は claim / equation /
        # component のみ）。W層の値に関わらず倒す。
        "navigable": False,
    }
    for key in ("inputs", "outputs"):
        refs = []
        for raw in _json_list(data.get(key)):
            projected = _project_mini_ref(raw)
            if projected is not None:
                refs.append(projected)
        out[key] = refs
    for key in ("eliminated_symbols", "retained_symbols"):
        # 記号は TeX 形のまま渡す（レンダリング可否はフロントのゲート。§5.4）。
        symbols = [_safe_text(v, allow_tex=True) for v in _json_list(data.get(key))]
        symbols = [s for s in symbols if s]
        if symbols:
            out[key] = symbols
    return out


def _project_derivations(value: Any) -> list[dict]:
    projected: list[dict] = []
    for raw in _json_list(value):
        entry = _project_derivation(raw)
        if entry is None:
            continue
        projected.append(entry)
        if len(projected) >= _LANE_MAX:
            break
    return projected


def _project_focus(
    focus: Any, element_type: str, element_id: str, *, equation_record: Any = None
) -> dict:
    """focus（v2）を学習者向けに射影する。

    ``contextual_role`` は ``contextual_role_source``（§4.4 の自己記述優先の来歴）が
    ``unidentified`` のときだけ落とす。committed / self_described / structural は
    残す（従来は candidate 判定で落としていたため、AI 候補ではない自己記述の役割まで
    空欄になっていた）。来歴を持たない旧形の投影では従来どおり
    ``contextual_role_status`` で判定する（後方互換の fail-soft）。役割文に内部 ID が
    混ざっている場合はどちらの経路でもキーごと落とす。

    ``provenance``（内部 ID 列）と ``review_notes``（要確認事項 = 教員のみ）は落とす。
    """
    focus = focus if isinstance(focus, dict) else {}
    label = str(focus.get("label") or "")
    intrinsic_summary = str(focus.get("intrinsic_summary") or "")
    if element_type == ELEMENT_TYPE_EQUATION:
        # EC1/EC2: 旧 W層は latex（80字で切り詰め済み）を label / intrinsic_summary の
        # 両方に入れていた。壊れた TeX は KaTeX で赤いエラーになるだけなので落とし、
        # 式番号（あれば）と、式を*読むための*材料に置き換える。
        label = _equation_focus_label(label, element_id)
    else:
        label = _safe_text(label)
    intrinsic_summary = _safe_text(intrinsic_summary)

    headline = _safe_text(focus.get("headline")) or label

    result: dict[str, Any] = {
        "element_type": element_type,
        "element_id": element_id,
        "document_id": focus.get("document_id"),
        "label": label,
        "headline": headline,
        "intrinsic_summary": intrinsic_summary,
    }
    if element_type == ELEMENT_TYPE_EQUATION:
        # 役割 / 意味の要約 / 記号の意味（ホバーと同じ材料。表示名の変換はフロント）。
        result.update(_equation_explanatory_fields(equation_record))

    intrinsic = _project_intrinsic(focus.get("intrinsic"))
    if intrinsic:
        result["intrinsic"] = intrinsic
    placement = _project_placement(focus.get("placement"))
    if placement:
        result["placement"] = placement
    derivations = _project_derivations(focus.get("derivations"))
    if derivations:
        result["derivations"] = derivations

    role = str(focus.get("contextual_role") or "").strip()
    if role and (_ROLE_INTERNAL_TOKEN_RE.search(role) or _contains_internal_id(role)):
        # 上位項目のラベルが内部 ID だったため役割文に ID が混ざっている
        # （「synth_claim_0001を定量化する」等）。役割を語らずキーごと落とす。
        role = ""
    if role and looks_like_tex_math(role):
        role = ""
    role_source = str(focus.get("contextual_role_source") or "").strip()
    role_status = str(focus.get("contextual_role_status") or "")
    if role_source:
        role_visible = role_source != CONTEXT_ROLE_STATUS_UNIDENTIFIED
    else:  # 旧形の投影（来歴キーが無い）は従来の status 判定へ縮退する。
        role_visible = role_status in _LEARNER_VISIBLE_STATUSES
    if role and role_visible:
        result["contextual_role"] = role
        if role_status:
            result["contextual_role_status"] = role_status
        if role_source:
            result["contextual_role_source"] = role_source

    generic = focus.get("generic")
    if isinstance(generic, dict):
        result["generic"] = generic
    return result


def _notes(value: Any) -> list[str]:
    return [str(n) for n in _json_list(value) if str(n or "").strip()]


def _learner_notes(value: Any) -> list[str]:
    """学習者に出す notes（事実文）。内部 ID / 生 TeX を含む行だけ落とす（最後の砦）。"""
    return [note for note in (_safe_text(n) for n in _notes(value)) if note]


def _is_degenerate_lens(lens: dict, resolved_id: str) -> bool:
    """W層 ``context_lens._degenerate_result`` そのものかどうか（LE8 の縮退判定）。

    ``context_lens.build()`` は builder が ``None`` を返した / 例外だった場合も
    **dict**（``_degenerate_result``）を返す。これは投影ではなく「読めなかった」という
    fail-soft 形なので、``available: true`` + ``focus.label = "<uuid>"`` で学習者に
    出してはならない。W層は変更しない（LE6）ため、ここで形状から検出する。

    判定は保守的に **4条件すべて**を要求する（正常だが疎な投影 — 上位・下位が空でも
    ラベルや本文が引けている投影 — を誤って縮退させない）:

    1. ``notes`` に degenerate 専用の文言が含まれる（``context_lens.py`` 内で
       この文言を使うのは ``_degenerate_result`` の1箇所だけ）
    2. ``focus.label`` が解決済み element_id の生値そのもの
    3. ``focus.intrinsic_summary`` が空
    4. ``upper`` / ``lower`` がともに空
    """
    focus = lens.get("focus")
    focus = focus if isinstance(focus, dict) else {}
    if _DEGENERATE_LENS_NOTE not in _notes(lens.get("notes")):
        return False
    if str(focus.get("label") or "") != str(resolved_id):
        return False
    if str(focus.get("intrinsic_summary") or "").strip():
        return False
    return not _json_list(lens.get("upper")) and not _json_list(lens.get("lower"))


# ---------------------------------------------------------------------------
# 公開インターフェース
# ---------------------------------------------------------------------------


def build_element_context(
    element_type: str, element_id: str, course_document_ids: set[str]
) -> dict | None:
    """学習者向け claim / equation 文脈 DTO を組み立てる。

    - 未対応の ``element_type``、コースの document 集合内で要素が解決できない、または
      agent 側 ID が複数行に一致して曖昧な場合は ``None``（呼び出し側は 404）。
    - 要素は解決できたが W層 context lens が投影を返せない / 例外 / 内部の
      fail-soft 形（``_degenerate_result``）だった場合は
      ``{"available": False, "note": <事実文>}``（fail-soft。呼び出し側は 200 で返す）。
    - 成功時は ``{"available": True, "element_type", "element_id", "focus",
      "upper", "lower", "notes", "provenance"}``。
    """
    if element_type not in SUPPORTED_ELEMENT_TYPES:
        return None

    resolved = _resolve_element(element_type, element_id, course_document_ids)
    if resolved is None:
        return None
    resolved_id, document_id = resolved

    try:
        ref = ElementRef(
            scope=SCOPE_DOCUMENT,
            element_type=_INTERNAL_ELEMENT_TYPES[element_type],
            element_id=resolved_id,
            document_id=document_id,
        )
        ref.validate()
        lens = context_lens_mod.build(ref)
    except Exception:  # noqa: BLE001
        logger.warning(
            "element_context: context_lens build failed for %s:%s",
            element_type,
            resolved_id,
            exc_info=True,
        )
        lens = None
    if not isinstance(lens, dict) or _is_degenerate_lens(lens, resolved_id):
        return {"available": False, "note": NOTE_NO_CONTEXT}

    result = {
        "available": True,
        "element_type": element_type,
        "element_id": resolved_id,
        "focus": _project_focus(
            lens.get("focus"),
            element_type,
            resolved_id,
            equation_record=(
                _equation_record_in_course(resolved_id, document_id)
                if element_type == ELEMENT_TYPE_EQUATION
                else None
            ),
        ),
        "upper": _visible_items(lens.get("upper")),
        "lower": _visible_items(lens.get("lower")),
        "notes": _learner_notes(lens.get("notes")),
        "provenance": PROVENANCE_COURSE_FREEZE,
    }
    return strip_confidence(result)
