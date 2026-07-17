"""intent 分類 + capability 解決（設計 §3 / §5 / §6）。

方針:
  - **ヒューリスティックを一次分類**にする（決定論的・非LLM・テスト可能・コスト0）。
    where 型発話 → locate / 変更動詞 + 対象 → action / それ以外 → guidance。
  - LLM は任意の一段リファイン（`allow_llm=True` のときのみ 1 コール）。LLM が
    許可外 / 未知の capability を返しても drop する（P1 fail-closed）。LLM 失敗時は
    ヒューリスティック結果へ縮退する（P6）。
  - 権限判定の最終責任は route 側（capabilities.can_access）。ここでは capability を
    **全 registry から**解決し、route が role で弾く（権限外は honest な拒否文言にできる）。
"""

from __future__ import annotations

import re
from typing import Optional

from core.admin_assistant import capabilities as caps
from core.admin_assistant.schema import (
    INTENT_ACTION,
    INTENT_CLARIFY,
    INTENT_GUIDANCE,
    INTENT_LOCATE,
    INTENT_STATUS_QUERY,
    INTENTS,
    IntentResult,
    LLMIntentResponse,
)

# where 型（道案内）の合図。
_WHERE_MARKERS = (
    "どこ", "何処", "どうやって行", "見つから", "見つけ", "たどり着",
    "場所", "どのタブ", "どの画面", "where",
)

# 変更動詞（action の合図）。
_ACTION_MARKERS = (
    "して", "したい", "してほしい", "してくれ", "変更", "書き換え", "書き直",
    "リライト", "修正", "直して", "整え", "公開", "非公開", "削除", "消して",
    "作成", "作って", "生成", "凍結", "差し替え", "更新", "登録",
)

# 状態照会（教材・コースの処理状態を尋ねる発話）の合図。
# 「状況」単体は system.view_stats（利用状況）の keyword と重なるため、
# ここでは「進捗・完了・失敗」等の状態照会に固有の複合語のみを合図にする。
_STATUS_QUERY_MARKERS = (
    "どうなって", "進捗", "終わった", "終わりました", "完了した", "完了しました",
    "失敗した", "解析状況", "処理状況", "状態を教えて", "ステータス",
)

# capability ごとのキーワード（keyword マッチで解決）。
_KEYWORDS: dict[str, tuple] = {
    "materials.upload": ("アップロード", "取り込", "教材を追加", "pdf", "tex", "ファイル"),
    "materials.set_visibility": ("教材", "開示", "公開範囲", "可視性", "visibility", "限定", "共有範囲"),
    "materials.delete": ("教材", "削除", "消"),
    "course_builder.open": ("コース", "設計", "構築", "ビルダー", "コースを作"),
    "lecture_studio.rewrite_chunk_script": (
        "原稿", "スクリプト", "書き換え", "書き直", "リライト", "修正", "チャンク", "ナレーション", "台本",
    ),
    "course.set_visibility": ("コース", "開示", "公開範囲", "可視性", "限定", "共有範囲"),
    "course.publish": ("コース", "公開", "学生に", "受講", "出す", "リリース"),
    "course.delete": ("コース", "削除", "消"),
    "atlas.generate_skeleton": ("地図", "骨格", "生成", "スケルトン", "atlas", "スケルトン生成"),
    "atlas.freeze_skeleton": ("地図", "骨格", "凍結", "確定", "freeze"),
    "users.create_student": ("学生", "受講者", "アカウント", "作成", "登録"),
    "users.create_teacher": ("教員", "先生", "教師", "アカウント", "作成"),
    "system.view_stats": ("統計", "stats", "状況", "件数"),
    "system.view_error_logs": ("エラー", "ログ", "error", "障害"),
    # N13/N31: 図分類レビュー・stumbles・schema-proposals（いずれも guidance_only）。
    # 「図」「提案」単体は他語（地図・検証提案 等）に部分一致するため複合語のみを使う。
    "materials.review_figures": ("図・画像", "図の分類", "画像の分類", "図分類"),
    "stumbles.view": ("つまづき", "つまずき", "未回答", "答えられなかった"),
    "schema_proposals.review": ("スキーマ提案", "スキーマ拡張", "shadow testing", "シャドーテスト"),
}

# selection のキー → target_type（曖昧な削除/可視性の絞り込みに使う）。
_SELECTION_TARGET_HINTS = {
    "course_id": "course",
    "chunk_id": "chunk",
    "material_id": "material",
    "cartridge_id": "cartridge",
}

_VISIBILITY_PATTERNS = (
    ("public", ("public", "全体に公開", "全体公開", "全員", "一般公開", "全体")),
    ("group", ("group", "グループ", "限定公開", "限定", "グループ限定")),
    ("private", ("private", "非公開", "自分だけ", "自分のみ", "プライベート")),
)


def has_where_marker(message: str) -> bool:
    msg = message or ""
    return any(m in msg for m in _WHERE_MARKERS)


def has_action_marker(message: str) -> bool:
    msg = message or ""
    return any(m in msg for m in _ACTION_MARKERS)


def has_status_query_marker(message: str) -> bool:
    msg = message or ""
    return any(m in msg for m in _STATUS_QUERY_MARKERS)


def _selection_target_types(screen_context: Optional[dict]) -> set:
    selection = (screen_context or {}).get("selection") or {}
    return {t for key, t in _SELECTION_TARGET_HINTS.items() if selection.get(key)}


def resolve_capability(
    message: str,
    screen_context: Optional[dict] = None,
    pool: Optional[list] = None,
) -> Optional[str]:
    """message から最も可能性の高い capability id を解決する（全 registry 対象）。

    キーワード重なりでスコアし、現在タブ・選択中対象で tie-break する。
    """
    msg = (message or "")
    pool = pool if pool is not None else caps.all_capabilities()
    active_tab = (screen_context or {}).get("tab") or ""
    sel_types = _selection_target_types(screen_context)

    best_id = None
    best_score = 0.0
    for cap in pool:
        kws = _KEYWORDS.get(cap.id, ())
        hits = sum(1 for kw in kws if kw and kw.lower() in msg.lower())
        if hits <= 0:
            continue
        score = float(hits)
        if cap.screen == active_tab:
            score += 1.5
        if cap.target_type and cap.target_type in sel_types:
            score += 1.5
        if score > best_score:
            best_score = score
            best_id = cap.id
    return best_id


def parse_visibility(message: str) -> Optional[str]:
    msg = (message or "").lower()
    for value, markers in _VISIBILITY_PATTERNS:
        if any(m.lower() in msg for m in markers):
            return value
    return None


def heuristic_classify(
    message: str,
    role: str,
    screen_context: Optional[dict] = None,
) -> IntentResult:
    cap_id = resolve_capability(message, screen_context) or ""
    cap = caps.get_capability(cap_id) if cap_id else None
    is_where = has_where_marker(message)

    # 状態照会は capability 解決より先に判定する（教材/コースの進捗確認は
    # 特定の capability に紐づかない読み取り専用の照会のため）。where 型
    # （「教材アップロードはどこ？」等）は道案内を優先する。
    if has_status_query_marker(message) and not is_where:
        return IntentResult(intent=INTENT_STATUS_QUERY, answer="", capability_id="", source="heuristic")

    if is_where and cap is not None:
        intent = INTENT_LOCATE
    elif cap is not None and cap.is_action() and has_action_marker(message):
        intent = INTENT_ACTION
    elif cap is not None:
        intent = INTENT_GUIDANCE
    elif is_where:
        intent = INTENT_CLARIFY
    else:
        intent = INTENT_GUIDANCE  # 一般的な「何ができる?」→ KB 検索へ

    return IntentResult(intent=intent, answer="", capability_id=cap_id, source="heuristic")


def _refine_with_llm(
    message: str,
    history: list,
    screen_context: Optional[dict],
    role: str,
    allowed: list,
    model: Optional[str],
) -> Optional[IntentResult]:
    """1 LLM コールで intent + capability を精緻化する。失敗時 None。"""
    try:
        from core.llm import generate_text_with_structured_output
    except Exception:
        return None

    allowed_json = [c.summary_dict() for c in allowed]
    sys_prompt = (
        "あなたは学習支援システムの管理画面アシスタントのルータです。"
        "ユーザーの発話を intent に分類し、可能なら操作カタログから 1 つの capability を選びます。\n"
        "intent: guidance(操作の説明) / locate(どこで操作するかの道案内) / action(操作の代行) / "
        "clarify(聞き返し) / status_query(教材・コースの処理状態の照会)。\n"
        "capability_id は必ず与えられた許可リストの id から選ぶ（無ければ空文字）。"
        "リストに無い操作を発明しない。断定しない。"
    )
    ctx_lines = [
        f"現在のタブ: {(screen_context or {}).get('tab', '')}",
        f"選択中: {(screen_context or {}).get('selection', {})}",
        "許可された操作カタログ(JSON): " + _compact_json(allowed_json),
        f"ユーザー発話: {message}",
    ]
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "\n".join(ctx_lines)},
    ]
    try:
        parsed: LLMIntentResponse = generate_text_with_structured_output(
            messages, LLMIntentResponse, model=model or None
        )
    except Exception:
        return None

    intent = parsed.intent if parsed.intent in INTENTS else INTENT_CLARIFY
    cap_id = parsed.capability_id or ""
    # fail-closed: LLM が許可外/未知の capability を返したら drop。
    if cap_id and cap_id not in {c.id for c in allowed}:
        cap_id = ""
    if parsed.needs_clarification:
        intent = INTENT_CLARIFY
    return IntentResult(
        intent=intent,
        answer=(parsed.answer or "").strip(),
        capability_id=cap_id,
        source="llm",
    )


def classify(
    message: str,
    role: str,
    *,
    history: Optional[list] = None,
    screen_context: Optional[dict] = None,
    allow_llm: bool = False,
    model: Optional[str] = None,
) -> IntentResult:
    """intent 分類のエントリポイント。

    ヒューリスティックを基準に、`allow_llm` なら LLM で精緻化する（P6: 失敗は縮退）。
    権限（role）による最終判定は route 側で行う。
    """
    base = heuristic_classify(message, role, screen_context)
    if not allow_llm:
        return base

    allowed = caps.capabilities_for(role)
    refined = _refine_with_llm(message, history or [], screen_context, role, allowed, model)
    if refined is None:
        return base

    # LLM が capability を落とした場合、ヒューリスティックの解決を温存（許可内のときのみ）。
    if not refined.capability_id and base.capability_id:
        if base.capability_id in {c.id for c in allowed}:
            refined.capability_id = base.capability_id
    return refined


def _compact_json(obj) -> str:
    import json

    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "[]"
