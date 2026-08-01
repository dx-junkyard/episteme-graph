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
  除去する（``component_context.strip_confidence`` を共有）。
- **裸の内部 ID を出さない**: ITEM の ``evidence_refs``（evidence_id / step_id 等の
  内部参照）と ``focus.provenance``（``theory_claims:<uuid>`` 等）は落とし、さらに
  **ラベルそのものが内部 ID 形の場合は一般ラベルへ置換する**（W層はラベル解決に
  失敗した項目に内部 ID をそのまま入れる — 図の DB UUID・``ev_0001``・
  ``synth_claim_0001`` 等。W層を変更しない（LE6）ため学習者向け側で遮る）。
- **書き込みを行わない**: A層（``src/episteme_graph/agents/``）・W層のコードは
  読むだけで変更しない。本モジュールに書き込み経路は無い。
- 本モジュールは FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.component_context import strip_confidence
from core.deliberation import context_lens as context_lens_mod
from core.deliberation.refs import document_run_artifacts, equation_records
from core.deliberation.schema import (
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

# 各レーンの表示上限（``component_context._GRAPH_LANE_MAX`` と同じ値）。W層 ``_cap_lane``
# が candidate 込みで既に 20 件へ切ったあとに本フィルタが走るため、実際の表示件数は
# 20 件以下になり得る（W層を変更しない（LE6）ことを優先した既知の挙動。設計書 §4）。
_LANE_MAX = 20

# 学習者が「旅の続き」として実際に再フェッチできる element_type。
# theory_claim / equation は本 API（``claim`` / ``equation``）、theory_component は
# 学習者向け component 文脈 API（``/components/{id}/context``）で開ける。それ以外
# （figure / section / thesis / derivation / symbol / evidence / stage / part）は
# 学習者向けの文脈取得口が無いため ``navigable`` を立てない（W層の
# ``_NAVIGABLE_ELEMENT_TYPES`` は教員向けの可否なのでそのままでは契約が成立しない）。
_LEARNER_NAVIGABLE_ELEMENT_TYPES = (
    ELEMENT_THEORY_CLAIM,
    ELEMENT_EQUATION,
    ELEMENT_THEORY_COMPONENT,
)

# W層設計書 §16 で evidence / derivation が教員向けには navigable になった
# （``context_lens._NAVIGABLE_ELEMENT_TYPES`` に追加）。**学習者の旅の対象は
# claim / equation / component のまま**なので、ホワイトリスト方式に加えて明示的な
# 拒否リストとしても書いておく（W層が語彙を増やしたときに学習者側へ黙って波及しない
# ための二重の fail-closed。学習者向けには対応する文脈取得 API が存在しないため、
# navigable を立てると押しても何も起きない導線になる）。
_LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES = (
    ELEMENT_EVIDENCE,
    ELEMENT_DERIVATION,
)

# context_lens が投影を返せない / 例外だった場合の事実文（available:false 時の note）。
NOTE_NO_CONTEXT = "この要素の文脈情報は現在表示できません。"

PROVENANCE_COURSE_FREEZE = "course_freeze"

# ── 内部 ID ラベルの遮断（LE4）──────────────────────────────────────────────
# W層 context_lens はラベル（caption / 本文 / 記号）を引けなかった項目に内部 ID を
# そのまま label として入れる（``_build_claim`` の図 DB UUID・evidence_id・
# subclaim の agent 側 ID、``_build_equation`` の ``synth_claim_0001``、
# thesis の ``support:<section>:<idx>`` など）。W層は変更しない（LE6）ので、
# 学習者向け射影の時点で「裸の内部 ID 形」を検出し一般ラベルへ置換する。
_UUID_LABEL_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_INTERNAL_ID_LABEL_RES = (
    re.compile(r"^ev(?:idence)?_[0-9]", re.IGNORECASE),   # evidence_registry: ev_0001
    re.compile(r"^synth_", re.IGNORECASE),               # 合成 claim: synth_claim_0001
    re.compile(r"^claim_", re.IGNORECASE),                # claim 生ID: claim_span_001 / claim_0004
    re.compile(r"^span_[0-9]", re.IGNORECASE),           # rhetorical_role: span_001
    re.compile(r"^support:"),                             # thesis support node: support:<section>:<idx>
    re.compile(r"^node_", re.IGNORECASE),                 # graph node id
)

# ``eq_2_7`` 形は論文の式番号由来で学習者にも可読なため v1 では置換しない（設計書 §4 の裁定）。
_EQUATION_NUMBER_LABEL_RE = re.compile(r"^eq[_\-.]?[0-9]", re.IGNORECASE)

# element_type 別の一般ラベル（内部 ID を出す代わりの事実文。関係語
# （``relation_label``）は保持するので「図 / を根拠とする」の形で意味は残る）。
_GENERIC_ITEM_LABELS = {
    ELEMENT_THEORY_CLAIM: "関連する主張",
    ELEMENT_THEORY_COMPONENT: "関連する論理要素",
    ELEMENT_EQUATION: "関連する数式",
    "figure": "図",
    "evidence": "本文の根拠箇所",
    "section": "掲載セクション",
    "thesis": "中心命題",
    "derivation": "導出の流れ",
    "symbol": "記号",
    "stage": "理論の段階",
    "part": "構成部品",
}
_GENERIC_ITEM_LABEL_FALLBACK = "関連する要素"

# ``focus.contextual_role`` は上位項目のラベルから合成される（W層
# ``_derive_contextual_role``）ため、内部 ID がそのまま役割文に混ざり得る
# （「synth_claim_0001を定量化する」等）。含まれていたら role をキーごと落とす
# （candidate / unidentified と同じ「推測で穴埋めしない」縮退）。
_ROLE_INTERNAL_TOKEN_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|synth_[A-Za-z0-9_]*[0-9]"
    r"|claim_span_[0-9]"
    r"|claim_[0-9]{3,}"
    r"|ev(?:idence)?_[0-9]{3,}"
    r"|span_[0-9]{3,}"
    r"|support:",
    re.IGNORECASE,
)

# W層 ``context_lens._degenerate_result`` の目印（要素の読み取りに失敗した fail-soft 形）。
# この文言は context_lens.py 内で degenerate 専用に1箇所しか使われていない。
_DEGENERATE_LENS_NOTE = "要素が見つからないか読み取りに失敗したため、文脈は表示できません"


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _normalized_document_ids(course_document_ids: set[str] | None) -> list[str]:
    return sorted({str(d) for d in (course_document_ids or set()) if str(d or "").strip()})


def _json_list(value: Any) -> list:
    return value if isinstance(value, list) else []


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
    断つ（``component_context._resolve_component_row`` と同型の fail-closed）。

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

    conditions = ["source_scope->'legacy_ids' ? :raw_id"]
    params: dict[str, Any] = {"raw_id": str(element_id), "doc_ids": document_ids}
    if _is_uuid(element_id):
        conditions.append("id = CAST(:uuid_id AS uuid)")
        params["uuid_id"] = str(element_id)
    where_clause = " OR ".join(conditions)

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
# ---------------------------------------------------------------------------


def _is_internal_id_label(label: str, element_type: str, element_id: Any) -> bool:
    """label が「裸の内部 ID」かどうか（LE4 のラベル規則）。

    ``eq_2_7`` のような equation の式番号は論文由来で学習者にも可読なため対象外
    （設計書 §4 の裁定）。それ以外で UUID / evidence・claim・span の生ID形 /
    ``support:`` 接頭 / label が ITEM の id 生値そのもの のいずれかなら内部 ID とみなす。
    """
    text = str(label or "").strip()
    if not text:
        return False
    if element_type == ELEMENT_EQUATION and _EQUATION_NUMBER_LABEL_RE.match(text):
        return False
    if _UUID_LABEL_RE.match(text):
        return True
    if any(pattern.match(text) for pattern in _INTERNAL_ID_LABEL_RES):
        return True
    raw_id = str(element_id or "").strip()
    return bool(raw_id) and text == raw_id


def _generic_item_label(element_type: str) -> str:
    return _GENERIC_ITEM_LABELS.get(str(element_type or ""), _GENERIC_ITEM_LABEL_FALLBACK)


def _project_item(item: dict) -> dict:
    """ITEM を学習者向けに射影する（``component_context._project_context_item`` と同形）。

    ``evidence_refs``（evidence_id / step_id 等の内部参照）と ``relation``
    （内部語彙キー）は落とし、読み手向けの ``relation_label`` のみ残す。
    ``label`` が裸の内部 ID 形なら element_type 別の一般ラベルへ置換する（LE4。
    関係情報は保持するので項目自体は落とさない = 情報を落とさない）。
    ``navigable`` は W層の値（教員向けの可否）を信用せず、学習者が実際に再フェッチ
    できる型かで作り直す（さらに evidence / derivation は明示的に拒否する —
    ``_LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES``）。
    """
    element_type = str(item.get("element_type") or "")
    element_id = item.get("element_id")
    label = str(item.get("label") or "")
    if _is_internal_id_label(label, element_type, element_id):
        label = _generic_item_label(element_type)
    navigable = (
        bool(element_id)
        and element_type in _LEARNER_NAVIGABLE_ELEMENT_TYPES
        and element_type not in _LEARNER_FORCED_NON_NAVIGABLE_ELEMENT_TYPES
    )
    return {
        "id": element_id,
        "element_type": item.get("element_type"),
        "label": label,
        "relation_label": item.get("relation_label"),
        "relation_status": item.get("relation_status"),
        "navigable": navigable,
    }


def _visible_items(items: Any) -> list[dict]:
    """candidate を除外して射影し、レーン上限で切る。"""
    projected = [
        _project_item(item)
        for item in _json_list(items)
        if isinstance(item, dict) and item.get("relation_status") != CONTEXT_STATUS_CANDIDATE
    ]
    return projected[:_LANE_MAX]


def _project_focus(focus: Any, element_type: str, element_id: str) -> dict:
    """focus を学習者向けに射影する。

    ``contextual_role`` は ``contextual_role_status`` が source_backed / confirmed の
    ときだけ残す（candidate = AI 候補 / unidentified = 未同定は、役割を語らずキー自体を
    落とす — 推測で穴埋めしない）。役割文に内部 ID が混ざっている場合も同様に落とす
    （W層は上位項目のラベルから役割文を合成するため、ラベル解決に失敗した項目が
    そのまま「この論文での役割」に現れる）。``provenance``（内部 ID 列）は落とす。
    """
    focus = focus if isinstance(focus, dict) else {}
    result: dict[str, Any] = {
        "element_type": element_type,
        "element_id": element_id,
        "document_id": focus.get("document_id"),
        "label": str(focus.get("label") or ""),
        "intrinsic_summary": str(focus.get("intrinsic_summary") or ""),
    }
    role = str(focus.get("contextual_role") or "").strip()
    if role and _ROLE_INTERNAL_TOKEN_RE.search(role):
        # 上位項目のラベルが内部 ID だったため役割文に ID が混ざっている
        # （「synth_claim_0001を定量化する」等）。役割を語らずキーごと落とす。
        role = ""
    role_status = str(focus.get("contextual_role_status") or "")
    if role and role_status in _LEARNER_VISIBLE_STATUSES:
        result["contextual_role"] = role
        result["contextual_role_status"] = role_status
    generic = focus.get("generic")
    if isinstance(generic, dict):
        result["generic"] = generic
    return result


def _notes(value: Any) -> list[str]:
    return [str(n) for n in _json_list(value) if str(n or "").strip()]


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
        "focus": _project_focus(lens.get("focus"), element_type, resolved_id),
        "upper": _visible_items(lens.get("upper")),
        "lower": _visible_items(lens.get("lower")),
        "notes": _notes(lens.get("notes")),
        "provenance": PROVENANCE_COURSE_FREEZE,
    }
    return strip_confidence(result)
