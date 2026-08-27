"""discuss 観測基盤（Observation Layer for Phase 3 Gate）— 集計・目安判定・ダンプ構築・仮名化。

正本: ``docs/features/discuss_observation_design.md``。discuss モード（「論文と話す」）の
Phase 3（v2）着手判断を「実測ゲート」で行えるようにするための計器一式。

不変条項（DO1〜DO6、設計書 §1）:
- DO1 本文を持ち出さない: 観測イベント・ダンプに学習者の発話本文・逐語・paraphrase・
  evidence_quote を一切含めない（このモジュールの射影関数はすべてホワイトリスト方式で
  組み立てる。``interest_traces.payload`` には verbatim な ``text`` キーが含まれるが、
  本モジュールのどの射影関数もそれを参照しない）。
- DO2 仮名化: ダンプ内の user_id は HMAC-SHA256（鍵は settings.jwt_secret + ドメイン
  分離文字列）による安定仮名。復元はできない。
- DO3 学習者に数値を見せない: 計測は内部専用（可視化は SYSTEM_ADMIN のみ）。
- DO4 削除 API を作らない: ``discuss_metric_events`` は append-only。
- DO5 参考目安は自動ゲートにしない: 判定結果（met/ready_for_analysis）を返すだけで、
  閾値到達で何かが自動的に起きることはない。
- DO6 計測失敗で UX を止めない: フロントは fire-and-forget 前提（本モジュールの責務外）。

設計方針（``core/discuss/opening.py`` / ``core/component_context.py`` と同じ流儀）:
- 純粋関数（fake rows・fake dict → 射影/集計/判定）と DB 読み出しを分離する。
  DB 読み出し関数は ``_query_*`` / ``_fetch_*`` の prefix を持ち、都度セッションを開いて
  ``finally`` で閉じる。
- 本モジュールは FastAPI を import しない。
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from core.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 仮名化（DO2）
# ---------------------------------------------------------------------------

# HMAC 鍵のドメイン分離文字列。settings.jwt_secret を JWT 署名と同じ値のまま使い回すのを
# 避けるため、鍵にこの文字列を連結してから HMAC を計算する（他用途への転用を防ぐ）。
_PSEUDONYM_DOMAIN = "discuss-observation-v1"


def pseudonymize_user_id(user_id: str) -> str:
    """user_id を HMAC-SHA256 で安定的・非可逆に仮名化する（DO2）。

    同じ user_id は常に同じ仮名になる（ダンプ間で同一ユーザーの追跡は可能）が、
    仮名から元の user_id を復元することはできない。鍵は ``settings.jwt_secret`` に
    ドメイン分離文字列を連結したもの（``os.environ`` 直書き禁止・開発ルール1）。
    """
    key = f"{get_settings().jwt_secret}:{_PSEUDONYM_DOMAIN}".encode("utf-8")
    message = str(user_id or "").encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# 小さな純粋ヘルパー
# ---------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    """datetime（または既に文字列）を ISO8601 文字列にする。None はそのまま None。"""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _truthy(value: Any) -> bool:
    """Postgres の TEXT 由来の 'true'/'false' 文字列と Python の bool の両方を受理する。

    ``payload->>'x'`` は常に TEXT で返るため、テスト（fake dict は Python の bool を
    そのまま積むことが多い）と実 DB 読み出しの両方に対応する。
    """
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"


def _span_days(first_at: str | None, last_at: str | None) -> int:
    """観測期間の日数（observation_span_days）。片方でも欠ければ 0（誠実に「まだ測れない」）。"""
    if not first_at or not last_at:
        return 0
    try:
        first = datetime.fromisoformat(str(first_at))
        last = datetime.fromisoformat(str(last_at))
    except ValueError:
        return 0
    return max(0, (last - first).days)


# ---------------------------------------------------------------------------
# §4: 分析開始の参考目安（名前付き定数のカタログ。DO5: 自動ゲートにしない）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationCriterion:
    key: str
    label: str
    target: int
    rationale: str


# 設計書 §4 の5基準（順序も設計書の表と同じ）。ここに書かれた target と rationale が正本。
OBSERVATION_CRITERIA: tuple[ObservationCriterion, ...] = (
    ObservationCriterion(
        key="distinct_discuss_users",
        label="discuss を使った学習者の実人数",
        target=5,
        rationale="1〜2人の癖ではなく傾向として読める最低人数",
    ),
    ObservationCriterion(
        key="discuss_turns",
        label="discuss チャットの往復数",
        target=200,
        rationale="grounding 分布・モード間比較が%で意味を持つ量",
    ),
    ObservationCriterion(
        key="observation_span_days",
        label="観測期間（日数）",
        target=14,
        rationale="曜日・課題周期のバイアス緩和",
    ),
    ObservationCriterion(
        key="landing_shown",
        label="着地画面の表示回数",
        target=30,
        rationale="着地率（confirm/skip）を率として読める量",
    ),
    ObservationCriterion(
        key="grounding_recorded_turns",
        label="grounding が記録された discuss 往復数",
        target=100,
        rationale="§2-1 の計器設置後データの十分量",
    ),
)


def compute_criteria(
    usage: Mapping[str, Mapping[str, Any]],
    traces: Mapping[str, Any],
    ui_events: Mapping[str, Any],
) -> tuple[list[dict], bool]:
    """OBSERVATION_CRITERIA の各基準について現在値・達成可否を計算する（純粋関数）。

    ``usage`` は ``{"learning:chat_discuss": {"count","distinct_users","first_at","last_at"}, ...}``、
    ``traces`` は ``{"grounding_recorded": int, ...}``、``ui_events`` は
    ``{"by_event": {"landing_shown": int, ...}, ...}`` の形（``build_observation_status`` が
    渡す形と同じ）。fake dict でそのままテストできる。
    """
    discuss_usage = usage.get("learning:chat_discuss", {}) if usage else {}
    by_event = (ui_events or {}).get("by_event") or {}
    current_by_key = {
        "distinct_discuss_users": int(discuss_usage.get("distinct_users") or 0),
        "discuss_turns": int(discuss_usage.get("count") or 0),
        "observation_span_days": _span_days(discuss_usage.get("first_at"), discuss_usage.get("last_at")),
        "landing_shown": int(by_event.get("landing_shown") or 0),
        "grounding_recorded_turns": int((traces or {}).get("grounding_recorded") or 0),
    }
    criteria: list[dict] = []
    for c in OBSERVATION_CRITERIA:
        current = current_by_key.get(c.key, 0)
        criteria.append(
            {
                "key": c.key,
                "label": c.label,
                "current": current,
                "target": c.target,
                "met": current >= c.target,
            }
        )
    ready_for_analysis = all(item["met"] for item in criteria)
    return criteria, ready_for_analysis


# ---------------------------------------------------------------------------
# §4: DB 読み出し（観測状況）
# ---------------------------------------------------------------------------

# discuss / casual / 通常チャットの3タグ（learning.py の _chat_feature 分岐と同じ文字列）。
CHAT_FEATURES: tuple[str, ...] = ("learning:chat_discuss", "learning:chat_casual", "learning:chat")


def _query_usage_by_feature(session) -> dict[str, dict[str, Any]]:
    """3チャットタグ別の件数・distinct users・期間（DB読み）。"""
    from sqlalchemy import text as sa_text

    rows = session.execute(
        sa_text(
            """
            SELECT feature, COUNT(*) AS calls, COUNT(DISTINCT user_id) AS distinct_users,
                   MIN(occurred_at) AS first_at, MAX(occurred_at) AS last_at
            FROM llm_usage_events
            WHERE feature = ANY(:features)
            GROUP BY feature
            """
        ),
        {"features": list(CHAT_FEATURES)},
    ).mappings().fetchall()

    by_feature: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_feature[str(r["feature"])] = {
            "count": int(r["calls"] or 0),
            "distinct_users": int(r["distinct_users"] or 0),
            "first_at": _iso(r["first_at"]),
            "last_at": _iso(r["last_at"]),
        }
    # 0件のタグも0埋めで返す（存在しないキーで呼び出し側が誤読しないように、U1と同じ誠実さ）。
    for feature in CHAT_FEATURES:
        by_feature.setdefault(
            feature, {"count": 0, "distinct_users": 0, "first_at": None, "last_at": None}
        )
    return by_feature


def _query_traces_summary(session) -> dict[str, Any]:
    """discuss 痕跡総数・grounding 記録済み件数と分布・記録開始日（DB読み）。"""
    from sqlalchemy import text as sa_text

    totals = session.execute(
        sa_text(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE (payload -> 'content_grounding') IS NOT NULL) AS grounding_recorded,
                MIN(created_at) FILTER (WHERE (payload -> 'content_grounding') IS NOT NULL) AS recorded_since
            FROM interest_traces
            WHERE payload ->> 'entry_mode' = 'discuss'
            """
        )
    ).mappings().fetchone()

    dist_rows = session.execute(
        sa_text(
            """
            SELECT COALESCE(payload->>'content_grounding', 'unknown') AS grounding, COUNT(*) AS n
            FROM interest_traces
            WHERE payload ->> 'entry_mode' = 'discuss' AND (payload -> 'content_grounding') IS NOT NULL
            GROUP BY 1
            """
        )
    ).mappings().fetchall()

    return {
        "total": int((totals or {}).get("total") or 0),
        "grounding_recorded": int((totals or {}).get("grounding_recorded") or 0),
        "grounding_distribution": {str(r["grounding"]): int(r["n"] or 0) for r in dist_rows},
        "recorded_since": _iso((totals or {}).get("recorded_since")),
    }


def _query_ui_events_summary(session) -> dict[str, Any]:
    """discuss_metric_events のイベント別件数・期間（DB読み）。"""
    from sqlalchemy import text as sa_text

    totals = session.execute(
        sa_text(
            "SELECT COUNT(*) AS total, MIN(created_at) AS first_at, MAX(created_at) AS last_at "
            "FROM discuss_metric_events"
        )
    ).mappings().fetchone()

    by_event_rows = session.execute(
        sa_text("SELECT event, COUNT(*) AS n FROM discuss_metric_events GROUP BY event")
    ).mappings().fetchall()

    return {
        "total": int((totals or {}).get("total") or 0),
        "by_event": {str(r["event"]): int(r["n"] or 0) for r in by_event_rows},
        "first_at": _iso((totals or {}).get("first_at")),
        "last_at": _iso((totals or {}).get("last_at")),
    }


def build_observation_status() -> dict:
    """discuss 観測状況（設計書 §4）。SYSTEM_ADMIN 向けの蓄積量集計 + 参考目安判定。

    参考目安（``criteria`` / ``ready_for_analysis``）は表示専用（DO5）— 到達状況を
    返すだけで、これを見て自動的に何かが切り替わることはない。
    """
    from core.postgres import get_session

    session = get_session()
    try:
        usage = _query_usage_by_feature(session)
        traces = _query_traces_summary(session)
        ui_events = _query_ui_events_summary(session)
    finally:
        session.close()

    criteria, ready_for_analysis = compute_criteria(usage, traces, ui_events)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "usage": usage,
        "traces": traces,
        "ui_events": ui_events,
        "criteria": criteria,
        "ready_for_analysis": ready_for_analysis,
    }


# ---------------------------------------------------------------------------
# §2-2: UI イベント取込（学習者本人記録・DO1 のホワイトリスト方式）
# ---------------------------------------------------------------------------

# サーバ側ホワイトリスト（設計書 §2-2）。フロント送信ポイントは discuss.js / app.js。
METRIC_EVENT_VOCAB: frozenset[str] = frozenset(
    {
        "discussion_entered",
        "opening_shown",
        "opening_starter_clicked",
        "opening_backbone_clicked",
        "branch_deep_clicked",
        "branch_wide_clicked",
        "scope_switched",
        "landing_shown",
        "landing_confirmed",
        "landing_dismissed",
        "landing_skipped",
        "landing_probe_clicked",
        "landing_continue_clicked",
        # 着地画面「今日の理解を自分の言葉で」の保存（候補の confirm とは別の導線なので
        # landing_confirmed には合算しない）。
        "landing_reflection_saved",
        # 理解サイクル（UCサイクル, docs/features/understanding_cycle_design.md §10）:
        # OPEN 動機・ELICIT予想・DIFF並置閲覧・LEAVE持ち越し・REVISIT再回答・ANCHOR軽量
        # アンカーの内部計測（DO1〜DO6 継承。本文非含有・数値非表示・削除APIなし）。
        "cycle_motive_saved",
        "cycle_prediction_saved",
        "cycle_diff_viewed",
        "cycle_carryover_saved",
        "cycle_revisit_answered",
        "cycle_anchor_quick",
        # コーパス回遊 Phase B（docs/features/corpus_roaming_design.md §5.5）:
        # コース無し論文議論（document 直付け discuss）をコース discuss と分離集計する
        # ための2語彙（DO1〜DO6 継承 — 本文非含有・payload 最小・学習者に数値非表示・
        # 削除 API なし）。course_id 欄にはセンチネル ``_doc:{document_id}`` が入る。
        # **記録はサーバ側**（opening / chat の各ルート）が best-effort で行うため、
        # フロントからは送らない（二重計上の防止）。
        "document_discuss_opened",
        "document_discuss_turn",
    }
)

# payload キー + 値ホワイトリスト（設計書 §2-2）。キーだけでなく値も列挙型で検証する
# （直接 API 経由で reason 等に任意の自由文を入れてダンプへ持ち出す穴を塞ぐ）。
_METRIC_EVENT_PAYLOAD_VALUE_VOCAB: dict[str, frozenset[str]] = {
    "scope": frozenset({"course_sources", "all_visible"}),
    "reason": frozenset({"explicit", "topic_switch", "timeout"}),
    "kind": frozenset({"tension", "anchor"}),
}
_METRIC_EVENT_PAYLOAD_KEYS: frozenset[str] = frozenset(_METRIC_EVENT_PAYLOAD_VALUE_VOCAB.keys())


def sanitize_event_payload(payload: dict | None) -> dict:
    """payload をホワイトリスト（scope/reason/kind）だけに絞り、値も許容 enum のみ通す
    （DO1: 本文混入の構造的防止）。

    キーが未知、値が文字列以外、または値が許容 enum に含まれない場合は、その
    キーを**黙って捨てる**（422 にはしない — DO6: 計測失敗で UX を止めない。イベント
    本体の記録は継続する）。
    """
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in _METRIC_EVENT_PAYLOAD_VALUE_VOCAB:
            continue
        if not isinstance(value, str):
            continue
        if value not in _METRIC_EVENT_PAYLOAD_VALUE_VOCAB[key]:
            continue
        out[key] = value
    return out


def insert_metric_events(user_id: str, events: list[dict]) -> int:
    """discuss_metric_events へ複数行を1トランザクションで追記する（DO4: append-only）。

    ``events`` の各要素は ``{"event","course_id","payload"}`` の形で、event 語彙の検証は
    呼び出し側（ルート層）が担う（本関数は payload のホワイトリスト絞り込みのみ再度行う —
    DO1 の多重防御）。失敗時は rollback して例外を再送出する（呼び出し側で 500 に変換する。
    ``atlas.py::record_atlas_cue_event`` と同じ規約）。
    """
    if not events:
        return 0

    from sqlalchemy import text as sa_text

    from core.postgres import get_session

    session = get_session()
    try:
        for ev in events:
            session.execute(
                sa_text(
                    """
                    INSERT INTO discuss_metric_events (user_id, course_id, event, payload)
                    VALUES (CAST(:uid AS uuid), :cid, :event, CAST(:payload AS jsonb))
                    """
                ),
                {
                    "uid": user_id,
                    "cid": str(ev.get("course_id") or ""),
                    "event": ev.get("event"),
                    "payload": json.dumps(sanitize_event_payload(ev.get("payload")), ensure_ascii=False),
                },
            )
        session.commit()
        return len(events)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# §5: 観測ダンプ — 行の射影（純粋関数。DO1: 本文キーを一切参照しない / DO2: 仮名化）
# ---------------------------------------------------------------------------

DUMP_FORMATS: tuple[str, ...] = ("tar.gz", "zip")
_MAX_DUMP_ROWS = 200_000
SCHEMA_VERSION = "discuss-observation-dump/1"


def project_llm_usage_row(row: Mapping[str, Any]) -> dict:
    """``llm_usage_events`` の1行 → ダンプ用の射影（本文非含有・user 仮名化）。"""
    return {
        "created_at": _iso(row.get("occurred_at")),
        "feature": row.get("feature") or "",
        "operation": row.get("operation") or "",
        "model": row.get("model") or "",
        "usage_source": row.get("usage_source") or "",
        "prompt_tokens": row.get("prompt_tokens"),
        "completion_tokens": row.get("completion_tokens"),
        "total_tokens": row.get("total_tokens"),
        "cached_tokens": row.get("cached_tokens"),
        "reasoning_tokens": row.get("reasoning_tokens"),
        "user_pseudonym": pseudonymize_user_id(str(row.get("user_id") or "")),
        "course_id": row.get("course_id") or "",
    }


def project_trace_row(row: Mapping[str, Any]) -> dict:
    """``interest_traces``（discuss 由来）の1行 → ダンプ用の射影。

    DO1: 本文キー（``text``/``paraphrase``/``evidence_quote`` 等）は一切参照しない
    （ホワイトリスト方式 — ここに書かれていないキーは絶対にダンプへ入らない）。
    """
    return {
        "created_at": _iso(row.get("created_at")),
        "user_pseudonym": pseudonymize_user_id(str(row.get("user_id") or "")),
        "course_id": row.get("course_id") or "",
        "kind": row.get("kind") or "",
        "status": row.get("status") or "",
        "overall_tier": row.get("overall_tier"),
        "content_grounding": row.get("content_grounding"),
        "discuss_scope": row.get("discuss_scope"),
        "tension_hint": _truthy(row.get("tension_hint")),
        "structure_anchor_present": _truthy(row.get("structure_anchor_present")),
        "map_excluded": _truthy(row.get("map_excluded")),
    }


def project_ui_event_row(row: Mapping[str, Any]) -> dict:
    """``discuss_metric_events`` の1行 → ダンプ用の射影（全列。payload は取込時に
    既にホワイトリスト済み — DO1 の多重防御はここでは不要）。
    """
    payload = row.get("payload")
    return {
        "created_at": _iso(row.get("created_at")),
        "user_pseudonym": pseudonymize_user_id(str(row.get("user_id") or "")),
        "course_id": row.get("course_id") or "",
        "event": row.get("event") or "",
        "payload": payload if isinstance(payload, dict) else {},
    }


# ---------------------------------------------------------------------------
# §5: 観測ダンプ — DB 読み出し（各ファイル 200,000 行キャップ）
# ---------------------------------------------------------------------------


def _fetch_llm_usage_chat_rows(session) -> tuple[list[dict], bool]:
    from sqlalchemy import text as sa_text

    rows = session.execute(
        sa_text(
            """
            SELECT occurred_at, feature, operation, model, usage_source,
                   prompt_tokens, completion_tokens, total_tokens, cached_tokens, reasoning_tokens,
                   user_id::text AS user_id, course_id
            FROM llm_usage_events
            WHERE feature = ANY(:features)
            ORDER BY occurred_at
            LIMIT :limit
            """
        ),
        {"features": list(CHAT_FEATURES), "limit": _MAX_DUMP_ROWS + 1},
    ).mappings().fetchall()
    truncated = len(rows) > _MAX_DUMP_ROWS
    projected = [project_llm_usage_row(dict(r)) for r in rows[:_MAX_DUMP_ROWS]]
    return projected, truncated


def _fetch_discuss_trace_rows(session) -> tuple[list[dict], bool]:
    from sqlalchemy import text as sa_text

    rows = session.execute(
        sa_text(
            """
            SELECT created_at, user_id::text AS user_id, course_id, kind, status,
                   payload->>'overall_tier' AS overall_tier,
                   payload->>'content_grounding' AS content_grounding,
                   payload->>'discuss_scope' AS discuss_scope,
                   COALESCE(payload->>'tension_hint', 'false') AS tension_hint,
                   (payload -> 'structure_anchor') IS NOT NULL AS structure_anchor_present,
                   COALESCE(payload->>'map_excluded', 'false') AS map_excluded
            FROM interest_traces
            WHERE payload ->> 'entry_mode' = 'discuss'
            ORDER BY created_at
            LIMIT :limit
            """
        ),
        {"limit": _MAX_DUMP_ROWS + 1},
    ).mappings().fetchall()
    truncated = len(rows) > _MAX_DUMP_ROWS
    projected = [project_trace_row(dict(r)) for r in rows[:_MAX_DUMP_ROWS]]
    return projected, truncated


def _fetch_discuss_ui_event_rows(session) -> tuple[list[dict], bool]:
    from sqlalchemy import text as sa_text

    rows = session.execute(
        sa_text(
            """
            SELECT created_at, user_id::text AS user_id, course_id, event, payload
            FROM discuss_metric_events
            ORDER BY created_at
            LIMIT :limit
            """
        ),
        {"limit": _MAX_DUMP_ROWS + 1},
    ).mappings().fetchall()
    truncated = len(rows) > _MAX_DUMP_ROWS
    projected = [project_ui_event_row(dict(r)) for r in rows[:_MAX_DUMP_ROWS]]
    return projected, truncated


# ---------------------------------------------------------------------------
# §5: daily_summary.jsonl — 日別集約（継続送信近似=課題#4の誠実な近似指標）
# ---------------------------------------------------------------------------

# 「継続送信」とみなす間隔（同一ユーザーの discuss LLM イベントが前回からこの時間以内に
# 続いたら followup とみなす）。生成プロンプトへの応答率の直接測定ではない（課題#4）。
_FOLLOWUP_WINDOW = timedelta(minutes=30)


def count_followup_turns_by_day(user_events: Iterable[tuple[str, datetime]]) -> dict[str, int]:
    """同一ユーザーの discuss ターンが前回から30分以内に続いた回数を日別に数える（純粋関数）。

    ``user_events`` は ``(user_id, occurred_at)`` のイテラブル（順不同でよい。ユーザー単位で
    内部ソートする）。「継続送信近似」であり、生成プロンプトへ実際に応答したかどうかの
    直接測定ではない（README.md にも明記する、課題#4）。
    """
    by_user: dict[str, list[datetime]] = {}
    for user_id, occurred_at in user_events:
        if occurred_at is None:
            continue
        by_user.setdefault(str(user_id), []).append(occurred_at)

    counts: dict[str, int] = {}
    for _user_id, timestamps in by_user.items():
        ordered = sorted(timestamps)
        for prev, cur in zip(ordered, ordered[1:]):
            if cur - prev <= _FOLLOWUP_WINDOW:
                day = cur.date().isoformat()
                counts[day] = counts.get(day, 0) + 1
    return counts


def build_daily_summary_rows(
    usage_by_day: Mapping[str, Mapping[str, int]],
    grounding_by_day: Mapping[str, Mapping[str, int]],
    landing_by_day: Mapping[str, int],
    followup_by_day: Mapping[str, int],
) -> list[dict]:
    """4つの日別集計を1つの daily_summary 行リストへマージする（純粋関数）。"""
    days = sorted(
        set(usage_by_day or {}) | set(grounding_by_day or {}) | set(landing_by_day or {}) | set(followup_by_day or {})
    )
    rows: list[dict] = []
    for day in days:
        usage = (usage_by_day or {}).get(day, {})
        rows.append(
            {
                "day": day,
                "discuss_turns": int(usage.get("discuss_turns") or 0),
                "distinct_users": int(usage.get("distinct_users") or 0),
                "grounding_distribution": dict((grounding_by_day or {}).get(day, {})),
                "landing_events": int((landing_by_day or {}).get(day) or 0),
                "followup_turns_approx": int((followup_by_day or {}).get(day) or 0),
            }
        )
    return rows


def _fetch_daily_usage(session) -> dict[str, dict[str, int]]:
    from sqlalchemy import text as sa_text

    rows = session.execute(
        sa_text(
            """
            SELECT date_trunc('day', occurred_at)::date AS day,
                   COUNT(*) AS turns, COUNT(DISTINCT user_id) AS distinct_users
            FROM llm_usage_events
            WHERE feature = 'learning:chat_discuss'
            GROUP BY 1
            """
        )
    ).mappings().fetchall()
    return {
        str(r["day"]): {"discuss_turns": int(r["turns"] or 0), "distinct_users": int(r["distinct_users"] or 0)}
        for r in rows
    }


def _fetch_daily_grounding(session) -> dict[str, dict[str, int]]:
    from sqlalchemy import text as sa_text

    rows = session.execute(
        sa_text(
            """
            SELECT date_trunc('day', created_at)::date AS day,
                   COALESCE(payload->>'content_grounding', 'unknown') AS grounding,
                   COUNT(*) AS n
            FROM interest_traces
            WHERE payload ->> 'entry_mode' = 'discuss' AND (payload -> 'content_grounding') IS NOT NULL
            GROUP BY 1, 2
            """
        )
    ).mappings().fetchall()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        day = str(r["day"])
        out.setdefault(day, {})[str(r["grounding"])] = int(r["n"] or 0)
    return out


def _fetch_daily_landing(session) -> dict[str, int]:
    from sqlalchemy import text as sa_text

    rows = session.execute(
        sa_text(
            """
            SELECT date_trunc('day', created_at)::date AS day, COUNT(*) AS n
            FROM discuss_metric_events
            WHERE event LIKE 'landing_%'
            GROUP BY 1
            """
        )
    ).mappings().fetchall()
    return {str(r["day"]): int(r["n"] or 0) for r in rows}


def _fetch_discuss_usage_events_for_followup(session) -> list[tuple[str, datetime]]:
    """継続送信近似の元データ。行数 = discuss ターン総数（日別集計の入力なので、
    出力される daily_summary 自体の行数＝日数はずっと小さい）。念のため defensive な上限を置く。
    """
    from sqlalchemy import text as sa_text

    rows = session.execute(
        sa_text(
            """
            SELECT user_id::text AS user_id, occurred_at
            FROM llm_usage_events
            WHERE feature = 'learning:chat_discuss'
            ORDER BY user_id, occurred_at
            LIMIT :limit
            """
        ),
        {"limit": _MAX_DUMP_ROWS * 2},
    ).fetchall()
    return [(str(r[0]), r[1]) for r in rows]


def _build_daily_summary(session) -> list[dict]:
    usage_by_day = _fetch_daily_usage(session)
    grounding_by_day = _fetch_daily_grounding(session)
    landing_by_day = _fetch_daily_landing(session)
    followup_events = _fetch_discuss_usage_events_for_followup(session)
    followup_by_day = count_followup_turns_by_day(followup_events)
    return build_daily_summary_rows(usage_by_day, grounding_by_day, landing_by_day, followup_by_day)


# ---------------------------------------------------------------------------
# §5: manifest.json — 期間（全行の created_at から算出。純粋関数）
# ---------------------------------------------------------------------------


def _period_from_rows(row_lists: Iterable[list[dict]], *, key: str = "created_at") -> dict[str, str | None]:
    """複数のダンプ行リストから、全体の期間（``key`` 列の最小・最大）を算出する。

    既定 ``key="created_at"`` は ``project_*_row`` 済み行（ISO8601 文字列 or None）を
    想定する。``daily_summary`` 行だけは ``created_at`` を持たず ``day``（YYYY-MM-DD）を
    持つため、呼び出し側が ``key="day"`` を渡す。対象行が1件も無ければ
    ``{"from": None, "to": None}``（設計書 §5「期間」。データが無い場合を誠実に null で
    表す）。ISO8601 文字列は同一タイムゾーン表記（本モジュールの ``_iso`` はすべて UTC
    オフセット付き）なので辞書順比較がそのまま時系列順になる。
    """
    values = [row.get(key) for rows in row_lists for row in rows if row.get(key)]
    if not values:
        return {"from": None, "to": None}
    return {"from": min(values), "to": max(values)}


# ---------------------------------------------------------------------------
# §5: README.md（分析観点・近似指標の注意）
# ---------------------------------------------------------------------------

_README_TEMPLATE = """# discuss 観測ダンプ

discuss モード（「論文と話す」）の Phase 3（v2）着手判断のための分析用ダンプです。
学習者の発話本文・逐語・paraphrase・evidence_quote は一切含みません（DO1）。
user は HMAC-SHA256 による安定した仮名（`user_pseudonym`）に置き換えてあります（DO2）。
同一ユーザーはダンプ内・ダンプ間で同じ仮名になりますが、仮名から元のユーザーを
復元することはできません。

## ファイル一覧

### manifest.json

生成時刻・スキーマ版・期間（`period.from`〜`period.to`。対象行が無ければ両方 null）・
仮名化方式・各ファイルの行数と truncated（200,000行キャップに達したか）を記録します。

### llm_usage_chat.jsonl

discuss / casual / 通常チャットの3タグ（`learning:chat_discuss` / `learning:chat_casual` /
`learning:chat`）の LLM 使用量イベントです。

| 列 | 内容 |
|---|---|
| created_at | イベント発生時刻（ISO8601） |
| feature | チャットタグ（3値のいずれか） |
| operation | LLM 呼び出し種別 |
| model | 使用モデル |
| usage_source | reported / estimated_tokenizer / estimated_heuristic |
| prompt_tokens / completion_tokens / total_tokens / cached_tokens / reasoning_tokens | トークン数（実測 or 推計） |
| user_pseudonym | 仮名化された user_id |
| course_id | コースID |

分析観点の例: `feature` 別のターン数推移、`model` 別のトークン消費、discuss と casual /
通常チャットの利用比較。

### discuss_traces.jsonl

`entry_mode='discuss'` の interest_traces（学習チャットの往復ごとの痕跡）です。

| 列 | 内容 |
|---|---|
| created_at | 往復発生時刻 |
| user_pseudonym | 仮名化された user_id |
| course_id | コースID |
| kind | question / misconception 等（interest_traces.kind） |
| status | interest_traces.status |
| overall_tier | 回答全体の tier（教員承認状況の集約） |
| content_grounding | course_material / other_material / model_generated |
| discuss_scope | course_sources / all_visible（discuss 以外は null） |
| tension_hint | 違和感ヒントが立ったか（bool） |
| structure_anchor_present | 構造帰属が付与されたか（bool。中身は含まない） |
| map_excluded | 個人知識ネットワークから除外指定されているか（bool） |

分析観点の例: `content_grounding` の分布推移（教材根拠 vs モデル生成の比率）、
`overall_tier` と `content_grounding` の関係、`discuss_scope` の選択傾向。

### discuss_ui_events.jsonl

discuss の UI 操作イベント（開幕画面・着地画面・分岐チップ等）です。全列を含みます。
`payload` は取込時点で `scope` / `reason` / `kind` のみに絞り込み済みです（DO1）。

| 列 | 内容 |
|---|---|
| created_at | イベント発生時刻 |
| user_pseudonym | 仮名化された user_id |
| course_id | コースID |
| event | イベント種別（13語彙のいずれか） |
| payload | scope/reason/kind のみを含む dict |

分析観点の例: `opening_shown` に対する `opening_starter_clicked` / `opening_backbone_clicked`
の比率（開幕画面の実効性）、`landing_shown` に対する `landing_confirmed` /
`landing_dismissed` / `landing_skipped` の比率（着地率）。

### daily_summary.jsonl

日別集約です。

| 列 | 内容 |
|---|---|
| day | 日付（YYYY-MM-DD） |
| discuss_turns | discuss チャットの往復数 |
| distinct_users | discuss チャットを使った実人数 |
| grounding_distribution | content_grounding 別の件数（記録済み分のみ） |
| landing_events | 着地画面関連イベント（`landing_*`）の件数 |
| followup_turns_approx | 継続送信の近似（下記「近似指標の注意」参照） |

## 近似指標の注意（課題#4）

`followup_turns_approx` は「学習者が生成プロンプト（応答末尾の言い換え/予測/自己説明の
誘い、または why/how/what-if 問い返し）に実際に応じたか」を LLM 判定なしに直接測定した
ものでは **ありません**。同一ユーザーの discuss チャットイベントが前回から30分以内に
続いた回数を数えただけの近似です。誠実さのため、この列を「生成プロンプト応答率」その
ものとして扱わないでください。

## 429（コスト上限超過）について

CostGate は in-memory・プロセスローカルであり、拒否された呼び出しは `llm_usage_events`
に記録されません。本ダンプには 429 の発生状況は含まれません（既知の限界）。
"""


def _render_readme() -> str:
    return _README_TEMPLATE


# ---------------------------------------------------------------------------
# §5: アーカイブ構築（メモリ内。tar.gz / zip）
# ---------------------------------------------------------------------------


def _jsonl_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    lines = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)
    return (lines + "\n").encode("utf-8")


def _build_archive(fmt: str, *, manifest: dict, readme: str, jsonl_files: dict[str, list[dict]]) -> bytes:
    """マニフェスト・README・4つの jsonl をメモリ内でアーカイブする（tar.gz / zip）。"""
    entries: dict[str, bytes] = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        "README.md": readme.encode("utf-8"),
    }
    name_map = {
        "llm_usage_chat": "llm_usage_chat.jsonl",
        "discuss_traces": "discuss_traces.jsonl",
        "discuss_ui_events": "discuss_ui_events.jsonl",
        "daily_summary": "daily_summary.jsonl",
    }
    for key, rows in jsonl_files.items():
        entries[name_map[key]] = _jsonl_bytes(rows)

    buffer = io.BytesIO()
    if fmt == "zip":
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
    else:  # "tar.gz"
        with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
            for name, data in entries.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def build_observation_dump(fmt: str) -> tuple[bytes, str]:
    """discuss 観測ダンプを構築する（設計書 §5）。本文非含有・user 仮名化（DO1/DO2）。

    戻り値は ``(bytes, filename)``。``fmt`` は ``"tar.gz"`` か ``"zip"``
    （``DUMP_FORMATS`` の要素以外は ``ValueError``。ルート層が事前に 422 で弾く前提の
    防御的チェック）。
    """
    if fmt not in DUMP_FORMATS:
        raise ValueError(f"unsupported dump format: {fmt!r}")

    from core.postgres import get_session

    session = get_session()
    try:
        llm_usage_rows, llm_usage_truncated = _fetch_llm_usage_chat_rows(session)
        trace_rows, trace_truncated = _fetch_discuss_trace_rows(session)
        ui_event_rows, ui_event_truncated = _fetch_discuss_ui_event_rows(session)
        daily_summary_rows = _build_daily_summary(session)
    finally:
        session.close()

    generated_at = datetime.now(timezone.utc)
    file_rows = {
        "llm_usage_chat": (llm_usage_rows, llm_usage_truncated),
        "discuss_traces": (trace_rows, trace_truncated),
        "discuss_ui_events": (ui_event_rows, ui_event_truncated),
        # daily_summary は日別集約のため行数は高々「観測日数」— 200,000行キャップの
        # 対象3ファイルとは性質が異なる（truncated は常に false で正直に記録する）。
        "daily_summary": (daily_summary_rows, False),
    }
    manifest = {
        "generated_at": generated_at.isoformat(),
        "schema_version": SCHEMA_VERSION,
        # 全体の期間（設計書 §5「期間」。created_at を持つ3ファイル横断。データが
        # 無ければ両方 null — 自動アラートにしない、DO5 と同じ誠実さ）。
        "period": _period_from_rows([llm_usage_rows, trace_rows, ui_event_rows]),
        "pseudonymization": f"hmac-sha256(jwt_secret + '{_PSEUDONYM_DOMAIN}')",
        "files": {
            name: {
                "rows": len(rows),
                "truncated": truncated,
                # daily_summary は created_at を持たず day（YYYY-MM-DD）を持つ。
                "period": _period_from_rows([rows], key="day" if name == "daily_summary" else "created_at"),
            }
            for name, (rows, truncated) in file_rows.items()
        },
        "truncated": any(truncated for _rows, truncated in file_rows.values()),
    }

    archive_bytes = _build_archive(
        fmt,
        manifest=manifest,
        readme=_render_readme(),
        jsonl_files={name: rows for name, (rows, _truncated) in file_rows.items()},
    )
    ext = fmt  # "tar.gz" or "zip" — どちらも拡張子としてそのまま使える
    filename = f"discuss-observation-{generated_at.strftime('%Y%m%dT%H%M%SZ')}.{ext}"
    return archive_bytes, filename
