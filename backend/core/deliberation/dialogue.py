"""面③「対話的検討」— grounding 構築 + 1ターン実行（設計書 §5・§7）。

- grounding（面①内訳 + 面②位置づけ）は :func:`build_grounding` が
  ``decomposition.build`` / ``positioning.build`` を再利用して組み立てる。
  cross_corpus レンズ・shared_part の exemplar_images は per-user 権限判定が
  必要なため（W5）、本モジュールではフィルタしない — 呼び出し側（route 層）が
  ``routes/deliberation.py`` の既存ゲート（``_apply_cross_corpus_gate`` /
  ``_apply_exemplar_image_gate``、overview と同じもの）を適用してから
  :func:`grounding_to_text` へ渡すこと。
- **同一性候補の供給（N2, Phase W-β 追補）**: ``build_grounding`` は document-scoped
  要素（instance）についてのみ、同分野（document の cartridge_id = library の
  domain_key）の凍結済み ``library_entries`` 上位k件（既定5・
  ``DELIBERATION_IDENTITY_CANDIDATES_TOP_K``）を非LLM・決定論的に収集し
  ``grounding["identity_candidates"]`` に載せる（``core.library.search.search_frozen_entries``
  の再利用、apparatus retrieval と同型）。供給は実在する id・name・aliases の事実の一覧のみで、
  LLM に同一視を促す文言は加えない（KN-3）。0件・domain_key 未解決のときは
  :func:`grounding_to_text` が「identity 種別の注釈は追加しないでください」という
  捏造ガードの一文に切り替える — LLM が実在しない ``shared_part_id`` を作文する経路を
  構造的に塞ぐ（``annotations._commit_identity`` の実在検証と二重の防御）。
- :func:`run_turn` が1ターンを実行する（W6: 1応答=1 LLM コール）。grounding は
  **セッションの最初の user メッセージにのみ**注入する（設計書 §5）。
- 候補注釈の構造化抽出は応答と同じ1コールの structured output で同時に取る
  （``_DialogueTurnOutput``）。LLM 呼び出し自体が失敗した場合は
  ``core.llm_worker.repair.run_with_repair`` を使わず（同期パスを重くしない・W6）、
  注釈なし・応答本文のみの縮退（``degraded=True``）にする。個々の候補注釈の
  W3 検証（evidence/reason 必須）は ``core.deliberation.annotations`` に委譲する。

figure 要素は vision（画像 + caption + 近傍本文）。画像バイト列は MinIO
``figure-images`` バケットから読む（``core.storage`` 経由。FastAPI 非依存）。

本モジュールは FastAPI にも ``routes``/``services`` にも依存しない（開発ルール2）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from core.config import get_settings
from core.document_pipeline.persistence import get_latest_analysis_run
from core.library.schema import ENTRY_TYPE_APPARATUS, ENTRY_TYPE_THEORY_COMPONENT
from core.library.search import search_frozen_entries
from core.llm import generate_conversation_turn
from core.llm_usage import usage_context
from core.llm_worker.client import resolve_model as _resolve_model_key
from core.llm_worker.cost_gate import CostGate, today_str
from core.postgres import get_session
from core.storage import get_storage_client
from core.deliberation import context_lens, decomposition, positioning
from core.deliberation.schema import (
    CONTEXT_ROLE_STATUS_UNIDENTIFIED,
    CONTEXT_STATUS_CANDIDATE,
    CONTEXT_STATUS_CONFIRMED,
    CONTEXT_STATUS_SOURCE_BACKED,
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    ElementRef,
    SCOPE_DOCUMENT,
)

logger = logging.getLogger(__name__)

_MODEL_SETTING_KEY = "deliberation_llm_model"

_FEATURE_CHAT = "deliberation:chat"
_FEATURE_VISION = "deliberation:vision"

_DEGRADED_REPLY = (
    "AI 応答を生成できませんでした。内訳と位置づけは overview を参照してください。"
)

# grounding 整形時に省く冗長な入れ子構造（プロンプト肥大化を避ける。生の値は
# overview API で別途参照可能なので情報は失われない）。
_GROUNDING_SKIP_FIELD_KEYS = ("frozen_content", "graph_node", "apparatus_candidates")

# N2 捏造ガード: identity 種別の指示は無条件に出さない。「同分野の既存の共通部品」一覧
# （実在する library_entries の id）が grounding に供給されている場合のみ、その一覧の id に
# 限定して identity を許可する（KN-3: LLM に同一視を促しすぎない・供給は事実の一覧のみ）。
# 供給0件時は grounding 側の一文（_IDENTITY_GUARD_NO_CANDIDATES）で identity を明示禁止する。
_INSTRUCTION_HEADER = (
    "あなたは大学院生の学習支援システムの教員向け機能「要素検討ワークスペース」の対話補助です。"
    "以下は教員が深く検討している1つの要素の内訳と位置づけです。これを踏まえて教員の質問・"
    "コメントに答えてください。断定は避け「〜の可能性がある」「〜と考えられる」のような"
    "仮説的な言い回しにしてください。もし対話の中で注釈として記録する価値がある解釈・"
    "意味づけ・内訳の補足などがあれば、annotations に候補として追加してください"
    "（0件でも構いません）。annotations の各項目には必ず"
    "kind（'meaning'|'decomposition'|'positioning_note'|'interpretation'|'identity'|"
    "'standardization' のいずれか）、body（自由文の説明。identity/standardization の場合は"
    "JSON文字列で {\"shared_part_id\":...,\"local_expression\":{...}} または "
    "{\"standardization_status\":...} を含める）、evidence（本文からの逐語引用の配列。"
    "空配列は不可）、reason（そう判断した理由）、confidence（0.0〜1.0）を含めてください。"
    "根拠（evidence・reason）を示せない項目は annotations に含めないでください。"
    "kind='identity' は特別で、後述の「同分野の既存の共通部品」一覧が示されている場合に"
    "限り使用できます（一覧が無いときは identity を使わないでください）。"
    "また、[文脈: 中心要素]・[文脈: 上位構造]・[文脈: 下位構造] が示されている場合は、"
    "回答の中でそのどの関係・根拠に基づいたかを読み手が確認できる形式で述べ、"
    "AI候補（ステータスが「AI候補」の関係）を確定した事実であるかのように述べないでください。"
)

# 供給あり: 「該当がある場合のみ」を強調し、shared_part_id を一覧内の実在 id に限定する
# （§6-1 の推奨案）。同一視を無理に作らせない。
_IDENTITY_GUARD_WITH_CANDIDATES = (
    "上の一覧はこの分野の凍結済み共通部品ライブラリの事実の一覧です。"
    "検討中の要素が一覧のいずれかと本当に同一のものを指していると判断できる場合に限り、"
    "kind='identity' の注釈を候補として追加して構いません。その場合 body の shared_part_id は"
    "必ず上の一覧に記載された id をそのまま使ってください（一覧に無い id を作らないこと）。"
    "該当が無ければ identity の注釈は追加しないでください。同一視を無理に見つける必要はありません。"
)

# 供給なし（0件・domain 未解決・domain スコープ要素）: identity の生成を明示的に禁止する。
# LLM が実在しない shared_part_id を作文する経路を構造的に塞ぐ（捏造ガード）。
_IDENTITY_GUARD_NO_CANDIDATES = (
    "この分野の共通部品ライブラリに参照できる既存エントリが供給されていないため、"
    "kind='identity' の注釈は追加しないでください。"
)


def resolve_model() -> str:
    """DELIBERATION_LLM_MODEL があればそれを、無ければ fast tier のモデルを使う。"""
    return _resolve_model_key(_MODEL_SETTING_KEY)


# ---------------------------------------------------------------------------
# コスト上限（§11: session/day の2段。他機能とは独立の in-memory カウンタ）
# ---------------------------------------------------------------------------

_cost_gate = CostGate()


def check_and_count_llm_call(session_id: str, user_id: str | None) -> bool:
    """1セッション・1ユーザー1日あたりの LLM コール上限内なら True を返し消費する。

    上限超過なら False（呼び出し側は HTTP 429 + 事実文 detail にマッピングする。
    数値レンジ・残数は返さない）。
    """
    settings = get_settings()
    per_session = int(getattr(settings, "deliberation_max_calls_per_session", 8))
    per_day = int(getattr(settings, "deliberation_max_calls_per_day", 40))
    daily_key = (today_str(), user_id or "")
    ok = _cost_gate.check_and_count(
        session_limit=per_session,
        session_key=session_id,
        daily_limit=per_day,
        daily_key=daily_key,
    )
    if not ok:
        logger.info("deliberation dialogue turn skipped: session/day cap reached")
    return ok


# ---------------------------------------------------------------------------
# grounding 構築（純粋部と DB 読み出し部を分離）
# ---------------------------------------------------------------------------

# 同一性候補の検索クエリに使う内訳フィールド（要素型横断の既知キーのみ・決定論的）。
_IDENTITY_QUERY_FIELD_KEYS = (
    "name",
    "summary",
    "text",
    "normalized_text",
    "caption_text",
    "plain_text",
    "latex",
)

# 要素型 → library entry_type の絞り込み（未登録の要素型は None = 絞り込みなし）。
# 図は装置エントリ、概念系インスタンスは theory_component エントリが同一性リンクの
# 主対象（entry_type 語彙の正本は core.library.schema）。
_IDENTITY_ENTRY_TYPE_BY_ELEMENT: dict[str, str | None] = {
    ELEMENT_FIGURE: ENTRY_TYPE_APPARATUS,
    ELEMENT_THEORY_COMPONENT: ENTRY_TYPE_THEORY_COMPONENT,
    ELEMENT_THEORY_CLAIM: ENTRY_TYPE_THEORY_COMPONENT,
    ELEMENT_EQUATION: ENTRY_TYPE_THEORY_COMPONENT,
}


def document_domain_key(document_id: str) -> str:
    """document → 最新解析 run の cartridge_id（= library domain_key と同一名前空間）。

    解析 run が無い・cartridge 未指定の document は空文字（→ 候補供給なしに縮退）。
    手動リンク作成 UI の候補検索エンドポイント（routes/deliberation.py）も
    同じ解決規約を共有する（フロントに domain 知識を持たせない）。
    """
    if not str(document_id or "").strip():
        return ""
    try:
        run = get_latest_analysis_run(document_id=document_id)
    except Exception:  # noqa: BLE001 — domain 解決は best-effort（対話自体を止めない）
        logger.warning(
            "deliberation dialogue: domain_key resolution failed for document %s",
            document_id, exc_info=True,
        )
        return ""
    return str((run or {}).get("cartridge_id") or "")


def identity_query_text(breakdown: dict[str, Any]) -> str:
    """内訳から同一性候補検索用のクエリテキストを組み立てる（非LLM・決定論的）。"""
    parts: list[str] = [str(breakdown.get("label") or "")]
    fields = breakdown.get("fields") or {}
    for key in _IDENTITY_QUERY_FIELD_KEYS:
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " ".join(p for p in parts if p).strip()


def identity_entry_type_for_element(element_type: str) -> str | None:
    """要素型 → library entry_type の絞り込み（未登録の要素型は None = 絞り込みなし）。"""
    return _IDENTITY_ENTRY_TYPE_BY_ELEMENT.get(element_type)


def collect_identity_candidates(ref: ElementRef, breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    """同分野の凍結済み library_entries 上位k件を同一性候補の材料として返す（N2）。

    - 対象は document-scoped のインスタンス要素のみ（identity link の source 制約と同じ）。
    - domain_key は対象要素の document → 最新解析 run の cartridge_id から決定論的に解決
      （cartridge_id = library domain_key の同一名前空間。apparatus retrieval と同じ規約）。
    - 検索は ``core.library.search.search_frozen_entries``（凍結版のみ・失敗時空リスト）を
      再利用。件数は ``DELIBERATION_IDENTITY_CANDIDATES_TOP_K``（既定5）。
    - 返すのは実在エントリの事実（id・名称・aliases）のみ。summary 等の本文は供給しない
      （LLM に同一視の判断材料を与えすぎない・KN-3）。
    """
    if ref.scope != SCOPE_DOCUMENT:
        return []
    domain_key = document_domain_key(ref.document_id or "")
    if not domain_key:
        return []
    query_text = identity_query_text(breakdown)
    if not query_text:
        return []
    settings = get_settings()
    top_k = int(getattr(settings, "deliberation_identity_candidates_top_k", 5))
    if top_k <= 0:
        return []
    try:
        hits = search_frozen_entries(
            domain_key=domain_key,
            query_text=query_text,
            entry_type=identity_entry_type_for_element(ref.element_type),
            top_k=top_k,
        )
    except Exception:  # noqa: BLE001 — search は例外を投げない契約だが防御的に（非致命）
        logger.warning(
            "deliberation dialogue: identity candidate retrieval failed for %s:%s",
            ref.element_type, ref.element_id, exc_info=True,
        )
        hits = []
    candidates: list[dict[str, Any]] = []
    for hit in hits:
        entry_id = str(hit.get("entry_id") or "")
        if not entry_id:
            continue
        candidates.append(
            {
                "shared_part_id": entry_id,
                "name": str(hit.get("name") or ""),
                "aliases": [str(a) for a in (hit.get("aliases") or []) if str(a or "").strip()],
            }
        )
    return candidates


def build_grounding(ref: ElementRef) -> dict[str, Any]:
    """面①内訳 + 面②位置づけ + 面③コンテキスト + 同一性候補材料を束ねた grounding 素材を
    返す（フィルタ前）。

    positioning 全体が失敗しても grounding 自体は内訳だけで返す（overview と同じ
    fail-soft。§4 冒頭のレンズ単位 fail-soft は positioning.py 内で既に効いている）。
    identity_candidates も同様に fail-soft（失敗・0件は空リスト → 捏造ガード文へ縮退）。
    ``context``（Issue #498 の要素中心コンテキストビュー）は ``context_lens.build`` が
    内部で fail-soft な契約を持つが、本モジュールでも念のため例外を握って None に縮退する
    （overview 側の三本目の try/except と同じ防御・W6）。
    """
    breakdown = decomposition.build(ref)
    try:
        positioning_payload: dict[str, Any] = {"available": True, "lenses": positioning.build(ref)}
    except Exception:  # noqa: BLE001
        logger.warning(
            "deliberation dialogue: positioning failed for %s:%s", ref.element_type, ref.element_id,
            exc_info=True,
        )
        positioning_payload = {
            "available": False,
            "note": "位置づけレンズの取得に失敗したため内訳のみ返す",
        }
    try:
        context = context_lens.build(ref)
    except Exception:  # noqa: BLE001
        logger.warning(
            "deliberation dialogue: context lens failed for %s:%s", ref.element_type, ref.element_id,
            exc_info=True,
        )
        context = None
    identity_candidates = collect_identity_candidates(ref, breakdown)
    return {
        "breakdown": breakdown,
        "positioning": positioning_payload,
        "context": context,
        "identity_candidates": identity_candidates,
    }


def _format_field_value(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (list, tuple)):
        joined = "、".join(str(v) for v in value if str(v or "").strip())
        return joined or None
    return str(value)


# 面③コンテキスト（Issue #498）の根拠状態 → 読み手向けラベル（W8: 数値は一切出さない）。
_CONTEXT_STATUS_LABELS: dict[str, str] = {
    CONTEXT_STATUS_SOURCE_BACKED: "原文根拠",
    CONTEXT_STATUS_CANDIDATE: "AI候補",
    CONTEXT_STATUS_CONFIRMED: "教員確認済み",
}


def _context_status_label(status: Any) -> str:
    return _CONTEXT_STATUS_LABELS.get(str(status or ""), str(status or ""))


def grounding_to_text(grounding: dict[str, Any]) -> str:
    """grounding dict を LLM prompt 用のテキストへ整形する（純粋関数・テスト容易）。"""
    breakdown = grounding.get("breakdown") or {}
    positioning_payload = grounding.get("positioning") or {}

    lines: list[str] = []
    lines.append(f"[要素] {breakdown.get('label', '')}（{breakdown.get('element_type', '')}）")

    fields = breakdown.get("fields") or {}
    for key, value in fields.items():
        if key in _GROUNDING_SKIP_FIELD_KEYS:
            continue
        formatted = _format_field_value(value)
        if formatted is None:
            continue
        lines.append(f"- {key}: {formatted}")

    for note in breakdown.get("notes") or []:
        lines.append(f"(注記) {note}")

    if positioning_payload.get("available"):
        lenses = positioning_payload.get("lenses") or {}
        for lens_name, lens in lenses.items():
            if not isinstance(lens, dict):
                continue
            items = lens.get("items") or []
            if not items:
                continue
            lines.append(f"[位置づけ:{lens_name}]")
            for item in items:
                lines.append(f"- {item.get('label', '')}: {item.get('value', '')}")
    else:
        note = positioning_payload.get("note")
        if note:
            lines.append(f"(注記) {note}")

    # 面③ 要素中心コンテキストビュー（Issue #498）。中心要素の文脈上の役割 + 上位構造
    # （Why）/ 下位構造（How）を事実文で列挙する。空レーンは黙って省く（他セクションと
    # 同じ「該当が無ければ書かない」方針）。数値（confidence 等）は一切出さない（W8）。
    context = grounding.get("context")
    if isinstance(context, dict):
        focus = context.get("focus") or {}
        role_status = focus.get("contextual_role_status")
        lines.append(f"[文脈: 中心要素] {focus.get('label', '')}")
        if role_status == CONTEXT_ROLE_STATUS_UNIDENTIFIED:
            lines.append("- 上位構造との関係は未同定")
        elif focus.get("contextual_role"):
            lines.append(
                f"- この文脈での役割: {focus.get('contextual_role')}"
                f"（{_context_status_label(role_status)}）"
            )

        upper_items = [i for i in (context.get("upper") or []) if isinstance(i, dict)]
        if upper_items:
            lines.append("[文脈: 上位構造]")
            for item in upper_items:
                lines.append(
                    f"- {item.get('relation_label', '')}: {item.get('label', '')}"
                    f"（{_context_status_label(item.get('relation_status'))}）"
                )

        lower_items = [i for i in (context.get("lower") or []) if isinstance(i, dict)]
        if lower_items:
            lines.append("[文脈: 下位構造]")
            for item in lower_items:
                lines.append(
                    f"- {item.get('relation_label', '')}: {item.get('label', '')}"
                    f"（{_context_status_label(item.get('relation_status'))}）"
                )

        for note in context.get("notes") or []:
            lines.append(f"(注記) {note}")

    # N2: 同一性候補の材料（実在エントリの事実の一覧）または捏造ガード。
    # 供給ありは「該当がある場合のみ・一覧内の id のみ」、供給なしは identity 明示禁止。
    identity_candidates = [
        c for c in (grounding.get("identity_candidates") or []) if isinstance(c, dict)
    ]
    if identity_candidates:
        lines.append("[同分野の既存の共通部品（凍結済みライブラリ）]")
        for candidate in identity_candidates:
            aliases = [str(a) for a in (candidate.get("aliases") or []) if str(a or "").strip()]
            alias_text = f"（別名: {'、'.join(aliases)}）" if aliases else ""
            lines.append(
                f"- id={candidate.get('shared_part_id', '')}: {candidate.get('name', '')}{alias_text}"
            )
        lines.append(f"(指示) {_IDENTITY_GUARD_WITH_CANDIDATES}")
    else:
        lines.append(f"(指示) {_IDENTITY_GUARD_NO_CANDIDATES}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# figure の画像バイト列取得（vision セッション用）
# ---------------------------------------------------------------------------

_FIGURE_IMAGES_BUCKET = "figure-images"


def figure_image_bytes(figure_id: str) -> bytes | None:
    """document_figures.minio_key から図画像バイト列を読む（best-effort）。

    取得できなければ ``None``（呼び出し側はテキストのみで対話を続行する）。
    """
    session = get_session()
    try:
        row = session.execute(
            sa_text("SELECT minio_key FROM document_figures WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": figure_id},
        ).fetchone()
    finally:
        session.close()
    minio_key = str(row[0]) if row and row[0] else ""
    if not minio_key:
        return None
    try:
        return get_storage_client().get_object(_FIGURE_IMAGES_BUCKET, minio_key)
    except Exception:  # noqa: BLE001
        logger.warning("deliberation dialogue: failed to load figure image %s", figure_id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# LLM 構造化出力スキーマ
# ---------------------------------------------------------------------------


class _AnnotationCandidateOut(BaseModel):
    kind: str = ""
    body: str = ""
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0


class _DialogueTurnOutput(BaseModel):
    reply: str = ""
    annotations: list[_AnnotationCandidateOut] = Field(default_factory=list)


@dataclass
class DialogueTurnResult:
    reply: str
    annotations: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False


# ---------------------------------------------------------------------------
# メッセージ組み立て（純粋関数・テスト容易）
# ---------------------------------------------------------------------------


def build_llm_messages(
    prior_messages: list[dict[str, str]],
    user_content: str,
    grounding_text: str,
) -> list[dict[str, str]]:
    """会話履歴 + 新規ユーザー発話から LLM 送信用メッセージ列を組み立てる。

    grounding_text は**最初の user メッセージにのみ**注入する（設計書 §5）。
    """
    turns = list(prior_messages) + [{"role": "user", "content": user_content}]
    messages: list[dict[str, str]] = []
    first_user_injected = False
    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if not first_user_injected and role == "user":
            first_user_injected = True
            if grounding_text:
                content = _INSTRUCTION_HEADER + "\n\n" + grounding_text + "\n\n---\n\n" + content
        messages.append({"role": role, "content": content})
    return messages


# ---------------------------------------------------------------------------
# 1ターン実行
# ---------------------------------------------------------------------------


def run_turn(
    ref: ElementRef,
    *,
    prior_messages: list[dict[str, str]],
    user_content: str,
    grounding_text: str,
    images: list[bytes] | None = None,
    model: str | None = None,
    user_id: str | None = None,
) -> DialogueTurnResult:
    """1ターンを実行する（W6: 1応答=1 LLM コール）。

    LLM 呼び出しが失敗した場合（API エラー・構造化出力パース失敗等）は
    ``run_with_repair`` を使わず、注釈なし・応答本文のみの縮退（``degraded=True``）で
    返す（同期パスを重くしない）。
    """
    llm_messages = build_llm_messages(prior_messages, user_content, grounding_text)
    resolved_model = model or resolve_model()
    feature = _FEATURE_VISION if images else _FEATURE_CHAT
    document_id = ref.document_id if ref.scope == SCOPE_DOCUMENT else None

    with usage_context(feature, user_id=user_id, document_id=document_id):
        try:
            parsed = generate_conversation_turn(
                llm_messages, _DialogueTurnOutput, images=images, model=resolved_model,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "deliberation dialogue: LLM turn failed for %s:%s", ref.element_type, ref.element_id,
                exc_info=True,
            )
            return DialogueTurnResult(reply=_DEGRADED_REPLY, annotations=[], degraded=True)

    raw_annotations = [a.model_dump() for a in (parsed.annotations or [])]
    reply = (parsed.reply or "").strip() or _DEGRADED_REPLY
    return DialogueTurnResult(reply=reply, annotations=raw_annotations, degraded=False)


__all__ = [
    "DialogueTurnResult",
    "build_grounding",
    "collect_identity_candidates",
    "document_domain_key",
    "identity_query_text",
    "identity_entry_type_for_element",
    "grounding_to_text",
    "build_llm_messages",
    "run_turn",
    "figure_image_bytes",
    "resolve_model",
    "check_and_count_llm_call",
]
