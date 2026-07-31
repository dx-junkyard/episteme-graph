"""教材図スタジオ API（実パス ``/api/admin/...``）。

設計書 `docs/features/teaching_figure_studio_design.md` §8。話（テキスト）や数式だけでは
わかりづらい箇所に、教員が AI との対話で SVG の説明図を作り、既存の ``![[figure:id]]``
記法に相乗りして教材へ埋め込むための層。

不変条項（FG1〜FG9）のうち本ルータが担う部分:

- **FG2 確定は人間**: 図の保存・採用は教員の明示操作のみ。turn API は DB を変更しない
  （生成物は返すだけで、保存するのは ``POST /teaching-figures``）。提案の accepted /
  dismissed 遷移も教員操作のみ。
- **FG3 サニタイズ必須**: 保存経路（POST / PATCH）は必ず
  ``core.teaching_figures.sanitizer.sanitize_svg`` を通す。拒否は除去ではなく 422
  （fail-closed）。SVG 配信レスポンスには ``nosniff`` + CSP sandbox を付ける
  （``SVG_RESPONSE_HEADERS``。学習者向け配信 ``routes/learning.py`` も同じ定数を使う）。
- **FG6 コスト上限**: 対話 1 ターン / 提案生成はそれぞれ独立した CostGate（day-only）。
  超過は 429 + 事実文（数値を出さない）。
- **FG7 情報を落とさない**: 行削除 API を持たない（``draft → adopted → retired`` の
  status 遷移のみ）。retire 時もトピック側の参照登録は消さない（§7.4）。
- **FG8 数値を見せない**: 提案の confidence は段階ラベルのみ（生値は返さない）。

権限（§8「権限の非対称に注意」）: 全エンドポイントが ``_require_teacher``。**書き込み系**
（turn / 保存 / PATCH / 提案 generate・accept・dismiss）は **コース所有者 / SYSTEM_ADMIN**
ゲート（``_shared.course_data_for_owner``）— トピック本文の保存
（``save_lecture_studio_course_topic``）が所有者限定であるため、図の生成・保存・採用も
同じ水準に揃える（editor 教員が図だけ作れて本文に反映できない中途半端を作らない）。
**読み取り系**（一覧 GET / 画像 GET / 提案一覧 GET）は editor 共有教員にも開く
（``_course_data_for_studio_editable``、§7.3 — studio プレビューの figures_index が
画像 URL を指すため、owner 限定にすると editor のプレビューが 403 で壊れる）。

採用時の参照登録（§7.1b、本設計の要石）は ``_register_figure_in_topic`` が
``topic.linked_figure_ids`` と ``topic.evidence_links`` の両方へ書き、図の保存と
**同一トランザクション**で ``learning_courses.data`` を更新する。これにより
①AI 書き換えで本文 embed が消えても配信ゲート（``_course_references_figure``）が壊れない
②retired / 未配信時に生 UUID が露出せず evidence_items 経由の読み取りカードへ落ちる。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from dependencies import _require_teacher
from services import record_review_event
from core import llm_policy
from core.config import get_settings
from core.course_content_builder import build_topic_evidence_items
from core.course_data import (
    course_title as _course_title,
    find_course_topic,
    iter_all_topics,
)
from core.llm_worker.cost_gate import CostGate, today_str
from core.llm_worker.history import window_history
from core.postgres import get_session as _pg_session
from core.schema import AUDIT_ENTITY_TEACHING_FIGURE
from core.storage import get_storage_client
from core.teaching_figures.generator import run_figure_turn
from core.teaching_figures.sanitizer import SvgRejected, sanitize_svg
from core.teaching_figures.schema import (
    FIGURE_IMAGES_BUCKET,
    FIGURE_KIND_LABELS,
    FIGURE_KINDS,
    SVG_CONTENT_TYPE,
    minio_key_for,
    new_figure_id,
)
from core.teaching_figures.signals import topic_gap_signals
from core.teaching_figures.store import (
    TeachingFigureNotFoundError,
    create_suggestions,
    create_teaching_figure,
    get_suggestion,
    get_teaching_figure,
    list_suggestions,
    list_teaching_figures,
    set_suggestion_status,
    update_teaching_figure,
)
from core.teaching_figures.suggest import generate_suggestions

from routes.lecture_studio._shared import (
    _course_data_for_studio_editable,
    course_data_for_owner,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Teaching Figure Studio"])


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
#
# バケット名・content_type・MinIO キー規約・id 採番は
# ``core.teaching_figures.schema`` が正本（``FIGURE_IMAGES_BUCKET`` /
# ``SVG_CONTENT_TYPE`` / ``minio_key_for`` / ``new_figure_id``）。ここでは再定義しない。

#: SVG 配信時のレスポンスヘッダ（FG3 第3防御。§7.2）。
#: blob → ``<img>`` 経路では SVG 内 script は元々実行されないが、API 応答や blob URL を
#: トップレベル文書として開かれた場合にも実行コンテキストを封じる（JWT が localStorage に
#: ある現行構成では必須）。学習者向け配信（``routes/learning.py``）と教員向け配信の
#: **両方**がこの同じ定数を使う（定義を二重化しない）。
SVG_RESPONSE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}

STATUS_DRAFT = "draft"
STATUS_ADOPTED = "adopted"
STATUS_RETIRED = "retired"
_FIGURE_STATUSES = (STATUS_DRAFT, STATUS_ADOPTED, STATUS_RETIRED)

#: 種別未指定のときの既定。具体的な種別（concept_map 等）を勝手に付けると分類を捏造する
#: ことになるため、正直な ``other`` に寄せる（FG5 の精神）。
_FIGURE_KIND_FALLBACK = "other"

SUGGESTION_STATUS_CANDIDATE = "candidate"
SUGGESTION_STATUS_ACCEPTED = "accepted"
SUGGESTION_STATUS_DISMISSED = "dismissed"

# grounding の上限（FG6: 同期パスを重くしない・プロンプト膨張を防ぐ）
_GROUNDING_MATERIAL_CHARS = 4000
_GROUNDING_MAX_EQUATIONS = 8
_GROUNDING_MAX_CLAIMS = 8


def figure_image_response(image_bytes: bytes, content_type: str | None) -> Response:
    """図画像バイト列を配信する ``Response`` を組み立てる（SVG のみ追加ヘッダ付き）。

    ``content_type`` が空なら ``image/png``（抽出図は常に PNG で ``document_figures`` に
    content_type 列が無い）。学習者向け・教員向けの両配信がこの1箇所を通ることで、
    SVG セキュリティヘッダの付け忘れを構造的に防ぐ。
    """
    media_type = (content_type or "").strip() or "image/png"
    headers = dict(SVG_RESPONSE_HEADERS) if media_type == SVG_CONTENT_TYPE else None
    return Response(content=image_bytes, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# コスト上限（FG6。原稿スタジオ rewrite の ``consume_lecture_rewrite_quota`` と同型の
# day-only ゲート。in-memory・プロセスローカルである制約は既存踏襲）
# ---------------------------------------------------------------------------

_figure_studio_cost_gate = CostGate()
_figure_suggest_cost_gate = CostGate()


def consume_figure_studio_quota(user_id: str) -> None:
    """図スタジオ対話の日次上限を1消費する。超過は 429 + 事実文（数値非表示）。"""
    cap = int(getattr(get_settings(), "figure_studio_max_calls_per_day", 60) or 0)
    ok = _figure_studio_cost_gate.check_and_count(daily_limit=cap, daily_key=(today_str(), user_id))
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="本日の図の生成回数の上限に達しました。明日以降に再度お試しください。",
        )


def consume_figure_suggest_quota(user_id: str) -> None:
    """図の提案生成の日次上限を1消費する。超過は 429 + 事実文（数値非表示）。"""
    cap = int(getattr(get_settings(), "figure_suggest_max_calls_per_day", 20) or 0)
    ok = _figure_suggest_cost_gate.check_and_count(daily_limit=cap, daily_key=(today_str(), user_id))
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="本日の図の提案生成の上限に達しました。明日以降に再度お試しください。",
        )


def _max_svg_bytes() -> int:
    return int(getattr(get_settings(), "teaching_figure_max_svg_bytes", 200_000) or 200_000)


def _max_suggestions() -> int:
    return int(getattr(get_settings(), "teaching_figure_max_suggestions", 4) or 4)


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------


def _validated_model(model: str | None) -> str | None:
    """M層 scene ``figure_studio`` に対するモデル指定を fail-closed 検証する（3行パターン）。"""
    requested = str(model or "").strip() or None
    if requested:
        reason = llm_policy.validate_model_for_scene(llm_policy.SCENE_FIGURE_STUDIO, requested)
        if reason:
            raise HTTPException(status_code=422, detail=reason)
    return requested


def _validated_figure_kind(value: object) -> str:
    """図種別を語彙（``FIGURE_KINDS``）で検証する。未指定は ``other`` に落とす。"""
    kind = str(value or "").strip() or _FIGURE_KIND_FALLBACK
    if kind not in FIGURE_KINDS:
        raise HTTPException(status_code=422, detail=f"図の種別が不正です: {kind}")
    return kind


def _find_topic(course_data: dict, topic_id: str) -> dict | None:
    """トピックを ``id`` で探す（フラット ``topics[]`` + 章ネスト ``chapters[].topics[]``）。

    ``find_course_topic`` はフラット形のみを見るため、章ネスト形の取りこぼしを
    ``iter_all_topics`` で補う。返るのは ``course_data`` 内の dict への**参照**なので、
    呼び出し側が in-place で書き換えれば ``course_data`` に反映される。
    """
    topic = find_course_topic(course_data, topic_id)
    if topic is not None:
        return topic
    for candidate in iter_all_topics(course_data):
        if str(candidate.get("id") or "") == str(topic_id):
            return candidate
    return None


def _register_figure_in_topic(topic: dict, figure_id: str, caption: str) -> dict:
    """採用時の参照登録（§7.1b）。``linked_figure_ids`` と ``evidence_links`` に追記する。

    冪等（同じ figure_id を二重登録しない）。``evidence_links`` の行は設計書指定の
    ``extra`` に加えて**トップレベルにも** ``figure_id`` / ``caption`` を持たせる —
    既存の読み手（``build_topic_evidence_items`` / ``_topic_figures_for_prompt`` /
    ``_required_figure_items``）はトップレベルを読むため、``extra`` だけでは
    evidence_items 投影・rewrite プロンプトの図列挙に載らない。
    """
    figure_id = str(figure_id)
    caption = str(caption or "")

    linked_raw = topic.get("linked_figure_ids")
    linked = [str(v) for v in linked_raw if str(v or "").strip()] if isinstance(linked_raw, list) else []
    if figure_id not in linked:
        linked.append(figure_id)
    topic["linked_figure_ids"] = linked

    links_raw = topic.get("evidence_links")
    links = list(links_raw) if isinstance(links_raw, list) else []
    existing = next(
        (
            link
            for link in links
            if isinstance(link, dict)
            and link.get("kind") == "figure"
            and str(link.get("target_id") or link.get("figure_id") or "") == figure_id
        ),
        None,
    )
    if existing is not None:
        if caption:
            existing["caption"] = caption
        extra = existing.get("extra") if isinstance(existing.get("extra"), dict) else {}
        extra.update({"figure_id": figure_id, "caption": existing.get("caption") or "", "teaching": True})
        existing["extra"] = extra
        existing.setdefault("figure_id", figure_id)
        link = existing
    else:
        link = {
            "kind": "figure",
            "target_id": figure_id,
            "figure_id": figure_id,
            "caption": caption,
            "extra": {"figure_id": figure_id, "caption": caption, "teaching": True},
        }
        links.append(link)
    topic["evidence_links"] = links
    return link


def _save_course_data(session, course_id: str, course_data: dict) -> None:
    """``learning_courses.data`` を更新する（commit は呼び出し側）。

    トピック音声キャッシュ（``topic_lecture_audio_cache``）はここでは削除しない —
    採用時に書き換わるのは構造フィールド（``linked_figure_ids`` / ``evidence_links``）
    だけで、表示スライドと読み上げ原稿（``student_material`` / ``spoken_script``）は
    変わらないため（§7.5: 本文への embed 挿入とその保存は既存の
    ``save_lecture_studio_course_topic`` が担い、そこで既存挙動どおり無効化される）。
    """
    session.execute(
        sa_text(
            "UPDATE learning_courses SET data = CAST(:data AS jsonb), updated_at = now() "
            "WHERE id = :course_id"
        ),
        {"course_id": course_id, "data": json.dumps(course_data, ensure_ascii=False)},
    )


def _teaching_figure_image_url(course_id: str, figure_id: str) -> str:
    return f"/api/admin/courses/{course_id}/teaching-figures/{figure_id}/image"


def _figure_summary(course_id: str, row: dict) -> dict:
    """一覧・保存応答用の図サマリ（``svg_source`` / ``revisions`` は返さない）。"""
    figure_id = str(row.get("id") or "")
    return {
        "id": figure_id,
        "title": row.get("title") or "",
        "caption": row.get("caption") or "",
        "figure_kind": row.get("figure_kind") or "",
        "figure_kind_label": FIGURE_KIND_LABELS.get(row.get("figure_kind") or "", ""),
        "status": row.get("status") or "",
        "topic_id": row.get("topic_id") or "",
        "created_at": row.get("created_at") or "",
        "image_url": _teaching_figure_image_url(course_id, figure_id),
    }


def _suggestion_response(row: dict) -> dict:
    """提案1件を API 応答用に整形する（FG8: confidence の生値は返さない）。

    ``store`` が既に段階ラベル（``confidence_label``）へ変換している前提だが、
    生値キーが混ざっていても漏らさないよう、ここでも明示的に落とす（二重防御）。
    """
    figure_kind = str(row.get("figure_kind") or "")
    return {
        "id": str(row.get("id") or ""),
        "anchor_excerpt": row.get("anchor_excerpt") or "",
        "gap_reason": row.get("gap_reason") or "",
        "figure_kind": figure_kind,
        "figure_kind_label": row.get("figure_kind_label") or FIGURE_KIND_LABELS.get(figure_kind, ""),
        "figure_brief": row.get("figure_brief") or "",
        "confidence_label": row.get("confidence_label") or "",
        "status": row.get("status") or "",
        "signal_basis": row.get("signal_basis") or "",
        "created_at": row.get("created_at") or "",
    }


def _topic_material_text(topic: dict) -> str:
    material = topic.get("student_material")
    if isinstance(material, dict):
        text = str(material.get("source_text") or "").strip()
        if text:
            return text
    return str(topic.get("content") or topic.get("summary") or "").strip()


def _build_grounding(
    course_data: dict,
    topic: dict,
    *,
    suggestion: dict | None = None,
) -> dict:
    """図生成の grounding を決定論的に組み立てる（§4.2「描いてよいのは grounding に
    現れる関係のみ」の材料。LLM は呼ばない）。

    材料はトピック本文の抜粋 + ``content_blocks`` 由来の数式 + claim テキスト +
    提案の ``figure_brief``。``build_topic_evidence_items`` を使うことで、原稿スタジオ
    右ペイン「根拠リンク」や学習画面の evidence_items と**同じ規則**の投影になる
    （このトピックで公開してよい参照だけが入る）。
    """
    equations: list[dict] = []
    claims: list[dict] = []
    try:
        for item in build_topic_evidence_items(topic):
            kind = item.get("kind")
            if kind == "equation" and len(equations) < _GROUNDING_MAX_EQUATIONS:
                equations.append({
                    "id": item.get("id") or "",
                    "label": item.get("title") or "",
                    "latex": item.get("latex") or "",
                    "plain_text": item.get("plain_text") or "",
                })
            elif kind == "claim" and len(claims) < _GROUNDING_MAX_CLAIMS:
                claims.append({
                    "id": item.get("id") or "",
                    "text": item.get("summary") or item.get("title") or "",
                })
    except Exception:  # noqa: BLE001 — grounding の欠落で生成自体を止めない（fail-soft）
        logger.warning("teaching figure grounding: evidence projection failed", exc_info=True)

    grounding: dict[str, Any] = {
        "course_title": _course_title(course_data),
        "topic_id": str(topic.get("id") or ""),
        "topic_title": str(topic.get("title") or ""),
        "key_concepts": [str(c) for c in (topic.get("key_concepts") or []) if str(c).strip()][:12],
        "material_excerpt": _topic_material_text(topic)[:_GROUNDING_MATERIAL_CHARS],
        "equations": equations,
        "claims": claims,
    }
    if suggestion:
        grounding["figure_brief"] = suggestion.get("figure_brief") or ""
        grounding["anchor_excerpt"] = suggestion.get("anchor_excerpt") or ""
        grounding["gap_reason"] = suggestion.get("gap_reason") or ""
        grounding["figure_kind"] = suggestion.get("figure_kind") or ""
    return grounding


def _grounding_to_text(grounding: dict) -> str:
    """grounding dict をプロンプト用のテキストブロックへ決定論的に整形する。

    ``run_figure_turn(grounding=...)`` は文字列を受ける（``prompt.build_turn_content`` が
    ``[参考資料]`` セクションとして差し込む）ため、構造 → テキストの変換は route の責務。
    W層 ``dialogue.build_grounding`` / ``grounding_to_text`` と同じ二段構え（構造は
    テストしやすく、テキスト化は1箇所）。
    """
    lines: list[str] = []
    if grounding.get("course_title"):
        lines.append(f"コース: {grounding['course_title']}")
    if grounding.get("topic_title"):
        lines.append(f"トピック: {grounding['topic_title']}")
    if grounding.get("key_concepts"):
        lines.append("重要概念: " + " / ".join(grounding["key_concepts"]))
    if grounding.get("figure_brief"):
        lines.append(f"作図の狙い（教員が採択した提案）: {grounding['figure_brief']}")
    if grounding.get("gap_reason"):
        lines.append(f"わかりづらさの理由: {grounding['gap_reason']}")
    if grounding.get("anchor_excerpt"):
        lines.append(f"対象箇所（本文からの引用）: {grounding['anchor_excerpt']}")
    equations = grounding.get("equations") or []
    if equations:
        lines.append("関係する数式:")
        for eq in equations:
            body = eq.get("latex") or eq.get("plain_text") or ""
            label = eq.get("label") or eq.get("id") or ""
            lines.append(f"- {label}: {body}".rstrip(": "))
    claims = grounding.get("claims") or []
    if claims:
        lines.append("関係する主張（claim）:")
        for claim in claims:
            text = str(claim.get("text") or "").strip()
            if text:
                lines.append(f"- {text}")
    if grounding.get("material_excerpt"):
        lines.append("教材本文:")
        lines.append(str(grounding["material_excerpt"]))
    return "\n".join(lines).strip()


def _load_suggestion(session, course_id: str, topic_id: str, suggestion_id: str) -> dict | None:
    """提案1件を（course_id / topic_id スコープ内で）引く。スコープ外は None（fail-closed）。"""
    if not suggestion_id or not topic_id:
        return None
    rows = list_suggestions(
        session,
        course_id,
        topic_id,
        statuses=(SUGGESTION_STATUS_CANDIDATE, SUGGESTION_STATUS_ACCEPTED),
    )
    return next((row for row in rows if str(row.get("id") or "") == str(suggestion_id)), None)


# ---------------------------------------------------------------------------
# 対話（R4）: 1ターン = 1 LLM コール・DB 非変更
# ---------------------------------------------------------------------------


class FigureStudioTurnRequest(BaseModel):
    """図スタジオ対話の1ターン。履歴はクライアント持ち（会話は永続化しない・§4.2）。"""

    instruction: str = ""
    topic_id: str = ""
    history: list[dict] = Field(default_factory=list)
    current_svg: str = ""
    suggestion_id: str | None = None
    model: str | None = None


@router.post("/courses/{course_id}/figure-studio/turn")
def figure_studio_turn(
    course_id: str,
    body: FigureStudioTurnRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員の指示で図（SVG）を生成・修正する1ターン（DB を変更しない）。

    LLM 失敗・サニタイズ失敗は 500 にせず ``degraded=true`` の 200 で返す
    （チャット型 AI の共通規約。プレビューは前回版のまま保つ）。
    """
    course_data = course_data_for_owner(course_id, current_user)

    instruction = str(body.instruction or "").strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="指示を入力してください。")

    topic_id = str(body.topic_id or "").strip()
    topic = _find_topic(course_data, topic_id) if topic_id else None
    if topic_id and topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    requested_model = _validated_model(body.model)

    suggestion = None
    if body.suggestion_id:
        session = _pg_session()
        try:
            suggestion = _load_suggestion(session, course_id, topic_id, str(body.suggestion_id))
        except Exception:  # noqa: BLE001 — 提案が引けなくても対話自体は続行（fail-soft）
            logger.warning("teaching figure turn: suggestion lookup failed", exc_info=True)
        finally:
            session.close()

    grounding = _build_grounding(course_data, topic or {}, suggestion=suggestion)

    # クライアント由来の履歴はここで正規化・ウィンドウ化する（境界での入力防御。
    # 設計書 §4.2 の 12件/6000字。current_message で「送信直前に push された現在発話」の
    # 二重化も除去する）。
    history = window_history(
        body.history,
        max_messages=12,
        max_chars=6000,
        current_message=instruction,
    )

    consume_figure_studio_quota(current_user["id"])
    result = run_figure_turn(
        history=history,
        user_instruction=instruction,
        current_svg=str(body.current_svg or ""),
        grounding=_grounding_to_text(grounding),
        model=requested_model,
        user_id=current_user.get("id") or "",
        course_id=course_id,
        max_svg_bytes=_max_svg_bytes(),
    )
    return {
        "reply": getattr(result, "reply", "") or "",
        "svg_source": getattr(result, "svg_source", "") or "",
        "title": getattr(result, "title", "") or "",
        "caption": getattr(result, "caption", "") or "",
        "figure_kind": getattr(result, "figure_kind", "") or "",
        "degraded": bool(getattr(result, "degraded", False)),
    }


# ---------------------------------------------------------------------------
# 保存・採用（R5）
# ---------------------------------------------------------------------------


class TeachingFigureCreateRequest(BaseModel):
    """図の保存。``adopt=true`` で採用（配信対象 + §7.1b のトピック側登録）。"""

    svg_source: str
    title: str = ""
    caption: str = ""
    figure_kind: str = ""
    topic_id: str = ""
    suggestion_id: str | None = None
    adopt: bool = True


@router.post("/courses/{course_id}/teaching-figures")
def create_course_teaching_figure(
    course_id: str,
    body: TeachingFigureCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """SVG をサニタイズして保存し、``adopt=true`` なら採用まで行う。

    処理順（DB 側は1トランザクション。MinIO アップロードのみ外部 I/O）:
    サニタイズ（拒否は 422）→ id 採番 + MinIO アップロード → 行作成 →
    採用時はトピック側登録 + ``learning_courses.data`` 更新 → 提案の accepted 遷移 →
    commit → 監査記帳。id を先に採番するのは MinIO キー（``teaching/{course_id}/{id}.svg``）が
    id 依存のため（アップロードが失敗すれば行は作られない = 参照先の無い行を残さない）。
    """
    course_data = course_data_for_owner(course_id, current_user)

    figure_kind = _validated_figure_kind(body.figure_kind)
    try:
        sanitized = sanitize_svg(body.svg_source or "", max_bytes=_max_svg_bytes())
    except SvgRejected as exc:
        raise HTTPException(status_code=422, detail=str(getattr(exc, "reason", None) or exc)) from exc

    topic_id = str(body.topic_id or "").strip()
    topic = None
    if body.adopt:
        if not topic_id:
            raise HTTPException(status_code=422, detail="採用するトピックを指定してください。")
        topic = _find_topic(course_data, topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")

    status = STATUS_ADOPTED if body.adopt else STATUS_DRAFT
    caption = str(body.caption or "")
    figure_id = new_figure_id()
    minio_key = minio_key_for(course_id, figure_id)
    session = _pg_session()
    try:
        get_storage_client().upload_bytes(
            FIGURE_IMAGES_BUCKET,
            minio_key,
            sanitized.svg.encode("utf-8"),
            content_type=SVG_CONTENT_TYPE,
        )
        row = create_teaching_figure(
            session,
            figure_id=figure_id,
            course_id=course_id,
            topic_id=topic_id or None,
            created_by=current_user.get("id"),
            title=str(body.title or ""),
            caption=caption,
            figure_kind=figure_kind,
            svg_source=sanitized.svg,
            minio_key=minio_key,
            content_type=SVG_CONTENT_TYPE,
            status=status,
            source_suggestion_id=str(body.suggestion_id) if body.suggestion_id else None,
        )

        evidence_link: dict | None = None
        linked_figure_ids: list[str] = []
        if topic is not None:
            evidence_link = _register_figure_in_topic(topic, figure_id, caption)
            linked_figure_ids = list(topic.get("linked_figure_ids") or [])
            _save_course_data(session, course_id, course_data)

        if body.suggestion_id:
            set_suggestion_status(session, str(body.suggestion_id), SUGGESTION_STATUS_ACCEPTED)

        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except ValueError as exc:
        # store 側の語彙検証（figure_kind / status）に落ちた場合は 422（fail-closed）。
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        logger.exception("Failed to save teaching figure for course %s", course_id)
        raise HTTPException(status_code=500, detail="Failed to save teaching figure")
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_TEACHING_FIGURE,
        figure_id,
        "",
        status,
        current_user.get("id"),
        {
            "action": "teaching_figure.create",
            "course_id": course_id,
            "topic_id": topic_id,
            "figure_kind": figure_kind,
            "adopted": bool(body.adopt),
            "suggestion_id": str(body.suggestion_id) if body.suggestion_id else "",
            # 「AI 生成物である」ことは検証できない（保存 API は教員クライアント由来の
            # svg_source を受ける）。提出原文とサニタイズ後の両方の指紋を残す（§4.1）。
            "svg_sha256": getattr(sanitized, "sha256", "") or "",
            "source_svg_sha256": getattr(sanitized, "source_sha256", "") or "",
        },
    )
    return {
        "figure_id": figure_id,
        "figure": _figure_summary(course_id, row),
        "evidence_link": evidence_link,
        "linked_figure_ids": linked_figure_ids,
    }


class TeachingFigurePatchRequest(BaseModel):
    """title / caption / SVG の差し替えと status 遷移（行削除はしない・FG7）。"""

    title: str | None = None
    caption: str | None = None
    svg_source: str | None = None
    status: str | None = None


@router.patch("/courses/{course_id}/teaching-figures/{figure_id}")
def update_course_teaching_figure(
    course_id: str,
    figure_id: str,
    body: TeachingFigurePatchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """図の修正・状態遷移。

    - ``svg_source`` 差し替え: 再サニタイズ（拒否 422）+ MinIO 再アップロード
      （旧版の ``revisions`` append は store 側の責務）。
    - ``status``: ``draft → adopted`` は §7.1b のトピック側登録も実行。
      ``adopted → retired`` はトピック側登録を**削除しない**（§7.4: 生 UUID 露出を防ぎ
      「配信対象ではありません」カードへ落とすため）。``retired → adopted`` の復帰可。
    """
    course_data = course_data_for_owner(course_id, current_user)

    new_status = str(body.status or "").strip() or None
    if new_status is not None and new_status not in _FIGURE_STATUSES:
        raise HTTPException(status_code=422, detail=f"図の状態が不正です: {new_status}")

    sanitized = None
    if body.svg_source is not None:
        try:
            sanitized = sanitize_svg(body.svg_source or "", max_bytes=_max_svg_bytes())
        except SvgRejected as exc:
            raise HTTPException(status_code=422, detail=str(getattr(exc, "reason", None) or exc)) from exc

    session = _pg_session()
    try:
        existing = get_teaching_figure(session, figure_id)
        # 他コースの図・不在はどちらも 404（存在の有無を権限で区別しない・fail-closed）。
        if not existing or str(existing.get("course_id") or "") != str(course_id):
            raise HTTPException(status_code=404, detail="Teaching figure not found")
        old_status = str(existing.get("status") or "")

        fields: dict[str, Any] = {"updated_by": current_user.get("id")}
        if body.title is not None:
            fields["title"] = str(body.title)
        if body.caption is not None:
            fields["caption"] = str(body.caption)
        if sanitized is not None:
            # 差し替えは同じキーへ上書きする（MinIO は配信スナップショット・正本は DB の
            # svg_source）。キー未設定の古い行は規約どおりのキーを付け直す。
            minio_key = str(existing.get("minio_key") or "") or minio_key_for(course_id, figure_id)
            get_storage_client().upload_bytes(
                FIGURE_IMAGES_BUCKET,
                minio_key,
                sanitized.svg.encode("utf-8"),
                content_type=SVG_CONTENT_TYPE,
            )
            fields["svg_source"] = sanitized.svg
            fields["minio_key"] = minio_key
        if new_status is not None:
            fields["status"] = new_status

        try:
            row = update_teaching_figure(session, figure_id, **fields)
        except TeachingFigureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Teaching figure not found") from exc
        except ValueError as exc:
            # store 側の語彙検証（figure_kind / status）に落ちた場合は 422（fail-closed）。
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        evidence_link: dict | None = None
        linked_figure_ids: list[str] = []
        topic_registered = False
        if new_status == STATUS_ADOPTED:
            topic_id = str(existing.get("topic_id") or "").strip()
            topic = _find_topic(course_data, topic_id) if topic_id else None
            if topic is not None:
                evidence_link = _register_figure_in_topic(
                    topic, figure_id, str(row.get("caption") or existing.get("caption") or "")
                )
                linked_figure_ids = list(topic.get("linked_figure_ids") or [])
                _save_course_data(session, course_id, course_data)
                topic_registered = True

        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to update teaching figure %s", figure_id)
        raise HTTPException(status_code=500, detail="Failed to update teaching figure")
    finally:
        session.close()

    metadata: dict[str, Any] = {
        "action": "teaching_figure.update",
        "course_id": course_id,
        "svg_replaced": sanitized is not None,
        "topic_registered": topic_registered,
    }
    if sanitized is not None:
        metadata["svg_sha256"] = getattr(sanitized, "sha256", "") or ""
        metadata["source_svg_sha256"] = getattr(sanitized, "source_sha256", "") or ""
    if row.get("revisions_truncated"):
        # FG7: 旧版の間引きが起きたことを黙って落とさない（store が立てるフラグ）。
        metadata["revisions_truncated"] = True
    record_review_event(
        AUDIT_ENTITY_TEACHING_FIGURE,
        figure_id,
        old_status,
        new_status or old_status,
        current_user.get("id"),
        metadata,
    )
    return {
        "figure_id": figure_id,
        "figure": _figure_summary(course_id, row),
        "evidence_link": evidence_link,
        "linked_figure_ids": linked_figure_ids,
        "topic_registered": topic_registered,
        # 旧版の間引きが起きたことを UI にも正直に伝える（FG7）。
        "revisions_truncated": bool(row.get("revisions_truncated")),
    }


@router.get("/courses/{course_id}/teaching-figures")
def list_course_teaching_figures(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """このコースの生成図一覧（draft を含む。挿入タブ・スタジオのストック表示用）。

    読み取り系は editor 共有教員にも開く（§7.3「コース編集権限」。書き込み系は
    トピック保存の権限非対称に合わせて owner のみ — §8）。
    """
    _course_data_for_studio_editable(course_id, current_user)
    session = _pg_session()
    try:
        rows = list_teaching_figures(session, course_id)
    finally:
        session.close()
    return {
        "course_id": course_id,
        "figures": [_figure_summary(course_id, row) for row in rows],
    }


@router.get("/courses/{course_id}/teaching-figures/{figure_id}/image")
def get_course_teaching_figure_image(
    course_id: str,
    figure_id: str,
    current_user: dict = Depends(_require_teacher),
) -> Response:
    """教員向け図画像配信（draft も見える — 教員のプレビュー用。§7.3）。

    正本は DB の ``svg_source`` なので、MinIO 取得に失敗しても DB の SVG で配信する
    （スナップショットがずれても教員のプレビューを壊さない）。SVG には
    ``nosniff`` + CSP sandbox を付ける（FG3 第3防御）。

    読み取り系は editor 共有教員にも開く（§7.3。studio プレビューの figures_index が
    この URL を指すため、owner 限定にすると editor のプレビューで画像が 403 になる）。
    """
    _course_data_for_studio_editable(course_id, current_user)
    session = _pg_session()
    try:
        row = get_teaching_figure(session, figure_id)
    finally:
        session.close()
    if not row or str(row.get("course_id") or "") != str(course_id):
        raise HTTPException(status_code=404, detail="Teaching figure not found")

    content_type = str(row.get("content_type") or SVG_CONTENT_TYPE)
    minio_key = str(row.get("minio_key") or "")
    if minio_key:
        try:
            return figure_image_response(
                get_storage_client().get_object(FIGURE_IMAGES_BUCKET, minio_key), content_type
            )
        except Exception:  # noqa: BLE001 — 正本は DB 側の svg_source
            logger.warning(
                "teaching figure image: MinIO fetch failed course=%s figure=%s",
                course_id, figure_id, exc_info=True,
            )
    svg_source = str(row.get("svg_source") or "")
    if not svg_source:
        raise HTTPException(status_code=404, detail="Teaching figure image not found")
    return figure_image_response(svg_source.encode("utf-8"), content_type)


# ---------------------------------------------------------------------------
# ギャップ検出・図の提案（R1/R2）
# ---------------------------------------------------------------------------


class FigureSuggestionGenerateRequest(BaseModel):
    model: str | None = None


@router.post("/courses/{course_id}/topics/{topic_id}/figure-suggestions/generate")
def generate_course_figure_suggestions(
    course_id: str,
    topic_id: str,
    body: FigureSuggestionGenerateRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """このトピックの「図で補うとよい箇所」候補を生成する（単発 LLM・candidate のみ）。

    学習者信号（``topic_gap_signals``）は既存の k-匿名集約の**読み出しだけ**を使い、
    LLM へ渡すのはレンジ・段階ラベルの事実文のみ（FG8）。既存 candidate の
    ``superseded`` 遷移は store（``create_suggestions``）の責務。
    """
    course_data = course_data_for_owner(course_id, current_user)
    topic = _find_topic(course_data, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    requested_model = _validated_model((body.model if body else None))

    session = _pg_session()
    try:
        # 学習者信号は「トピックに紐づく claim / anchor」に絞った k-匿名集約の読み出し。
        # document_id はコース sources の 1件目（core 側が解決に使う provenance）。
        signal_lines = topic_gap_signals(
            session,
            course_id=course_id,
            topic_id=topic_id,
            document_id=_primary_course_document_id(course_data),
            topic=topic,
        )
    except Exception:  # noqa: BLE001 — 信号が引けなくても本文のみで提案は動く（§5.1）
        logger.warning("figure suggestions: learner signal read failed", exc_info=True)
        signal_lines = []
    finally:
        session.close()

    consume_figure_suggest_quota(current_user["id"])
    # 生成（LLM）は読み取り専用（DB 書き込みは下の create_suggestions が行う）。信号読み出しと
    # セッションを分けるのは、信号側で例外が起きた場合に aborted transaction を引き継がない
    # ようにするため。
    session = _pg_session()
    try:
        result = generate_suggestions(
            session,
            course_id=course_id,
            topic_id=topic_id,
            topic=topic,
            signal_lines=signal_lines,
            model=requested_model,
            user_id=current_user.get("id"),
            max_items=_max_suggestions(),
        )
    finally:
        session.close()
    rows = [
        {**row, "created_by": current_user.get("id")}
        for row in (getattr(result, "suggestions", None) or [])
        if isinstance(row, dict)
    ]
    dropped = int(getattr(result, "dropped", 0) or 0)

    session = _pg_session()
    try:
        created = create_suggestions(session, rows) if rows else 0
        session.commit()
        suggestions = list_suggestions(
            session,
            course_id,
            topic_id,
            statuses=(SUGGESTION_STATUS_CANDIDATE, SUGGESTION_STATUS_ACCEPTED),
        )
    except Exception:
        session.rollback()
        logger.exception("Failed to persist figure suggestions for course %s", course_id)
        raise HTTPException(status_code=500, detail="Failed to save figure suggestions")
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_TEACHING_FIGURE,
        f"{course_id}:{topic_id}",
        "",
        "suggestions_generated",
        current_user.get("id"),
        {
            "action": "teaching_figure.suggestions_generate",
            "course_id": course_id,
            "topic_id": topic_id,
            "created": int(created or 0),
            "dropped": dropped,
            "used_learner_signals": bool(signal_lines),
        },
    )
    return {
        "course_id": course_id,
        "topic_id": topic_id,
        "suggestions": [_suggestion_response(row) for row in suggestions],
        "dropped": dropped,
    }


def _primary_course_document_id(course_data: dict) -> str:
    """コース sources の先頭 document_id（無ければ空文字）。

    ``core.teaching_figures.signals`` が claim / anchor をトピックへ対応づけるための
    provenance ヒント。解決できなくても提案は本文のみで動く（§5.1）。
    """
    # ここでの解決は routes/lecture.py の正本（services.list_course_source_document_ids）に
    # 委譲する（material_id → documents.id の SQL 解決を二重実装しない）。
    from routes.lecture import _course_document_ids

    try:
        ids = _course_document_ids(course_data)
    except Exception:  # noqa: BLE001
        logger.warning("figure suggestions: course document resolution failed", exc_info=True)
        return ""
    return str(ids[0]) if ids else ""


@router.get("/courses/{course_id}/topics/{topic_id}/figure-suggestions")
def get_course_figure_suggestions(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """candidate + accepted の提案一覧（段階ラベルのみ・FG8）。

    読み取り系は editor 共有教員にも開く（§7.3。生成・確定は owner のみ）。
    """
    _course_data_for_studio_editable(course_id, current_user)
    session = _pg_session()
    try:
        rows = list_suggestions(
            session,
            course_id,
            topic_id,
            statuses=(SUGGESTION_STATUS_CANDIDATE, SUGGESTION_STATUS_ACCEPTED),
        )
    finally:
        session.close()
    return {
        "course_id": course_id,
        "topic_id": topic_id,
        "suggestions": [_suggestion_response(row) for row in rows],
    }


def _decide_suggestion(course_id: str, suggestion_id: str, status: str, current_user: dict) -> dict:
    course_data_for_owner(course_id, current_user)
    session = _pg_session()
    try:
        # 遷移前に現在の状態を読む（監査に実際の old_status を残すため。P5）。
        # 他コースの提案・不在はどちらも 404 で、書き込み自体を行わない（fail-closed）。
        existing = get_suggestion(session, suggestion_id)
        if not existing or str(existing.get("course_id") or "") != str(course_id):
            raise HTTPException(status_code=404, detail="Figure suggestion not found")
        old_status = str(existing.get("status") or "")
        # 判断済みの提案は書き換えない（``set_suggestion_status`` の遷移元は candidate のみ）。
        # store の契約は「対象が無い・既に candidate でない場合は None（呼び出し側が
        # 404/409 にマッピング）」なので、存在は確認できている以上 404 ではなく 409 +
        # 事実文を返す（W層 annotations の already-decided 409 と同型）。
        if old_status != SUGGESTION_STATUS_CANDIDATE:
            raise HTTPException(
                status_code=409,
                detail="この提案はすでに判断済みです（採択または却下されています）。",
            )
        row = set_suggestion_status(session, suggestion_id, status)
        if not row:
            # 読み取りと更新の間に他の操作が確定させた場合（レース）。
            raise HTTPException(
                status_code=409,
                detail="この提案はすでに判断済みです（採択または却下されています）。",
            )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        logger.exception("Failed to update figure suggestion %s", suggestion_id)
        raise HTTPException(status_code=500, detail="Failed to update figure suggestion")
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_TEACHING_FIGURE,
        str(suggestion_id),
        old_status,
        status,
        current_user.get("id"),
        {
            "action": f"teaching_figure.suggestion_{status}",
            "course_id": course_id,
            "topic_id": row.get("topic_id") or "",
        },
    )
    return {"suggestion": _suggestion_response(row)}


@router.post("/courses/{course_id}/figure-suggestions/{suggestion_id}/accept")
def accept_course_figure_suggestion(
    course_id: str,
    suggestion_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """提案を採択済みにする（教員操作のみ・FG2。行削除はしない）。"""
    return _decide_suggestion(course_id, suggestion_id, SUGGESTION_STATUS_ACCEPTED, current_user)


@router.post("/courses/{course_id}/figure-suggestions/{suggestion_id}/dismiss")
def dismiss_course_figure_suggestion(
    course_id: str,
    suggestion_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """提案を却下する（``dismissed`` 遷移で保持・FG7）。"""
    return _decide_suggestion(course_id, suggestion_id, SUGGESTION_STATUS_DISMISSED, current_user)
