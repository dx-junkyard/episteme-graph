"""主権台帳v1「わたしの記録」— 本人の学習痕跡の一望と持ち出し（Part B）。

正本設計書: ``docs/features/trace_registry_sovereignty_ledger_design.md`` §3。
系統（kind）の語彙・宣言順・露出宣言の正本は ``core/trace_registry.py``（TR1）、
status の日本語ラベルの正本は ``core/label_vocab.py::TRACE_STATUS_LABELS``。

不変条項:

- **TR4 台帳は読み取り専用・本人のみ** — 本モジュールは ``interest_traces`` を
  読むだけで、書き込み・削除・封印の経路を持たない（封印は P4 例外の専用設計書を
  経る v2）。API 層（``routes/my_records.py``）も GET のみ。
- **TR5 来歴は誠実に** — 現在記録されていない事実（集約への実際の包含来歴）を
  推定で表示しない。「集約への包含の来歴は現在記録されていません」と事実文で言う。
  偽のボタン・先取りの約束を出さない。
- **TR6 数値を見せない（UC9継承）** — 台帳は本人の行の列挙であり数値集計ではない。
  件数バッジ・進捗率・スコアのフィールドを作らない。
- **TR7 台帳の表示はステアリングに使わない（UC5継承）** — 台帳 DTO を提示内容・
  提示順・対話方針の入力にしない（本モジュールを学習チャット・digest・worker から
  import しない）。

DB 読みは ``core.postgres.get_session`` + try/finally（``core/personal_graph/queries.py``
の流儀）。FastAPI / routes / services / core.llm は import しない。
``build_ledger_overview`` / ``build_ledger_export`` は純関数（fake rows でテスト可能）。
"""

from __future__ import annotations

import json

from sqlalchemy import text as sa_text

from core.label_vocab import TRACE_STATUS_LABELS
from core.postgres import get_session as _pg_session
from core.privacy import K_ANONYMITY
from core.trace_registry import TRACE_KINDS

# ---------------------------------------------------------------------------
# 公表状態の事実文（TR5: 実際の包含来歴は記録されていないため、登録簿の露出宣言 +
# 行の状態から決定論的に導出できる事実だけを言う）。
# ---------------------------------------------------------------------------

#: status='candidate' の行（AI 候補。本人が確定するまで本人の痕跡ではない — P1）。
PUBLICITY_CANDIDATE = "AIの候補です。あなたが確定するまで、あなたの痕跡になりません。"
#: 教員向け k-匿名集約の対象になり得る kind の行。閾値の正本は core/privacy.py
#: （k=3 をリテラルで再定義しない）。
PUBLICITY_DASHBOARD = (
    f"教員向けには{K_ANONYMITY}人以上の匿名集計にのみ含まれることがあります。"
)
#: 上記いずれでもない行（本人専用メモ等）。
PUBLICITY_PRIVATE = "あなた以外には表示されません。"

#: 全体注記（TR5）。包含来歴の記録基盤は提案1 v2（手渡しチャネル）の専用設計書で
#: 確定する — ここで先取りしない。
PROVENANCE_NOTE = (
    "集約への包含の来歴は現在記録されていません。手渡しの仕組み（実装予定）と同時に、"
    "どの集約に含まれたかを記録する仕組みを追加します。"
)

#: 持ち出し JSON の注記。
EXPORT_NOTE = "このファイルはあなたの学習痕跡の完全な持ち出しです。"

#: 未知 status の表示ラベル（素通しにしない — 語彙の正本にない値をそのまま UI 語彙に
#: 昇格させない。行自体は落とさず保持する。P4）。
UNKNOWN_STATUS_LABEL = "その他"

#: 台帳一覧の既定上限（超過は truncated=True で正直に返す。持ち出しは常に全件、
#: の意味論は呼び出し側が limit を十分大きく取ることで担保する — v1 は一覧と同じ
#: 取得経路を共有する）。
DEFAULT_LEDGER_LIMIT = 500


def _payload_dict(raw) -> dict:
    """JSONB 列を dict に正規化する（personal_graph/queries.py と同じ防御）。"""
    if isinstance(raw, dict):
        return raw
    if raw:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _to_iso(value) -> str:
    """timestamptz 列を ISO 文字列へ正規化する（NULL は空文字）。"""
    return value.isoformat() if value else ""


# ---------------------------------------------------------------------------
# DB 読み（本人スコープ・全行）
# ---------------------------------------------------------------------------


def fetch_ledger_rows(
    user_id: str, limit: int = DEFAULT_LEDGER_LIMIT
) -> tuple[list[dict], bool]:
    """本人の interest_traces 全行を新しい順に読む（P4 の一望）。

    kind 条件・status 条件・payload 条件を**一切付けない** — dismissed / superseded /
    candidate も含めて本人の全痕跡を返す。``limit + 1`` 行取得して超過を判定し、
    ``(rows, truncated)`` を返す（TR6: 総件数は数えない・返さない）。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, kind, status, course_id, topic_id, payload, created_at
                FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid)
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"uid": user_id, "lim": int(limit) + 1},
        ).fetchall()
    finally:
        session.close()
    truncated = len(rows) > limit
    return [
        {
            "id": str(r[0]),
            "kind": r[1] or "",
            "status": r[2] or "",
            "course_id": str(r[3]) if r[3] is not None else "",
            "topic_id": r[4],
            "payload": _payload_dict(r[5]),
            "created_at": _to_iso(r[6]),
        }
        for r in rows[:limit]
    ], truncated


def fetch_course_labels(rows: list[dict]) -> dict[str, str]:
    """rows に現れる course_id のタイトルを引く。

    正本は ``core.personal_graph.queries.fetch_course_titles``（遅延 import。
    削除済みコース・タイトル未設定はキー自体が無い — 痕跡の行は残る）。
    """
    from core.personal_graph.queries import fetch_course_titles

    course_ids = sorted({r.get("course_id") for r in rows if r.get("course_id")})
    if not course_ids:
        return {}
    return fetch_course_titles(course_ids)


# ---------------------------------------------------------------------------
# 純関数: 台帳 overview（系統グルーピング + 公表状態の事実文）
# ---------------------------------------------------------------------------


def _system_publicity_note(spec) -> str:
    """系統ごとの露出事実文（登録簿由来・静的。TR5）。"""
    surfaces: list[str] = []
    if spec.learner_trajectory:
        surfaces.append("「問いの軌跡」")
    if spec.personal_map:
        surfaces.append("「わたしの地図」")
    parts: list[str] = []
    if surfaces:
        parts.append("と".join(surfaces) + "に表示されます。")
    if spec.teacher_dashboard:
        parts.append(PUBLICITY_DASHBOARD)
    if not parts:
        return PUBLICITY_PRIVATE
    return "".join(parts)


def _row_publicity(status: str, spec) -> str:
    """行の公表状態の事実文（優先順: candidate → 教員集約対象 → 本人のみ）。

    未登録 kind（spec が None）の行は、教員向け集約が denylist 方式である事実に
    合わせて集約対象側の文言に倒す（TR5: 実態より安心側に偽らない）。
    """
    if status == "candidate":
        return PUBLICITY_CANDIDATE
    if spec is None or spec.teacher_dashboard:
        return PUBLICITY_DASHBOARD
    return PUBLICITY_PRIVATE


def _project_item(row: dict, spec, course_labels: dict) -> dict:
    """1行を台帳表示用に射影する。

    payload は ``text`` / ``context_label`` のみに射影し、confidence / load_score /
    score / weight 等の数値キーを一切出さない（W8 同型。ネスト payload を
    そのまま返さない）。
    """
    payload = row.get("payload") or {}
    status = row.get("status") or ""
    course_id = row.get("course_id") or ""
    return {
        "id": row.get("id") or "",
        "kind": row.get("kind") or "",
        "kind_label": spec.label if spec is not None else (row.get("kind") or ""),
        "status": status,
        "status_label": TRACE_STATUS_LABELS.get(status, UNKNOWN_STATUS_LABEL),
        "text": payload.get("text") or "",
        "context_label": payload.get("context_label") or "",
        "created_at": row.get("created_at") or "",
        "course_id": course_id,
        "course_label": course_labels.get(course_id, ""),
        "flags": {
            "map_excluded": bool(payload.get("map_excluded")),
            "superseded": status == "superseded",
            "candidate": status == "candidate",
        },
        "publicity": _row_publicity(status, spec),
    }


def build_ledger_overview(
    rows: list[dict], *, course_labels: dict, truncated: bool
) -> dict:
    """台帳 overview DTO を組み立てる（純関数・DB 非接触）。

    系統は ``trace_registry.TRACE_KINDS`` の宣言順。登録簿に無い kind の行が
    実在した場合も落とさず、末尾に kind 名そのままの系統として保持する（P4）。
    件数フィールドは作らない（TR6）。
    """
    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        by_kind.setdefault(row.get("kind") or "", []).append(row)

    systems: list[dict] = []
    for kind, spec in TRACE_KINDS.items():
        items = [
            _project_item(row, spec, course_labels) for row in by_kind.pop(kind, [])
        ]
        systems.append({
            "kind": kind,
            "label": spec.label,
            "dead": spec.dead,
            "publicity_note": _system_publicity_note(spec),
            "items": items,
        })
    # 登録簿に無い kind（実行時の異常系でのみ生じうる）— 情報を落とさない（P4）。
    for kind, kind_rows in by_kind.items():
        systems.append({
            "kind": kind,
            "label": kind,
            "dead": False,
            "publicity_note": PUBLICITY_DASHBOARD,
            "items": [_project_item(row, None, course_labels) for row in kind_rows],
        })

    return {
        "systems": systems,
        "truncated": bool(truncated),
        "provenance_note": PROVENANCE_NOTE,
    }


# ---------------------------------------------------------------------------
# 純関数: 持ち出し（export）
# ---------------------------------------------------------------------------


def build_ledger_export(rows: list[dict], *, exported_at: str) -> dict:
    """持ち出し JSON を組み立てる（純関数・DB 非接触）。

    records は payload **全文・無加工**（本人の手元に落とすデータなので数値キーも
    削らない — 完全な持ち出し）。``user_id`` はキーとして含めない（ファイル単体が
    識別子を持ち歩かないため）。スキーマ安定性: ``schema_version`` を持ち、将来の
    kind 追加は records に新 kind の行が増えるだけで既存キーは変えない。
    """
    return {
        "schema_version": 1,
        "exported_at": exported_at,
        "note": EXPORT_NOTE,
        "records": [
            {
                "id": row.get("id") or "",
                "kind": row.get("kind") or "",
                "status": row.get("status") or "",
                "course_id": row.get("course_id") or "",
                "topic_id": row.get("topic_id"),
                "text": (row.get("payload") or {}).get("text") or "",
                "payload": row.get("payload") or {},
                "created_at": row.get("created_at") or "",
            }
            for row in rows
        ],
    }
