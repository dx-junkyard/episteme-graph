"""痕跡 kind 登録簿 — ``interest_traces.kind`` の語彙と露出宣言の**単一の真実源**。

正本設計書: ``docs/features/trace_registry_sovereignty_ledger_design.md``（Part A）。

不変条項:

- **TR1 登録簿が kind の単一の真実源** — kind の語彙・各 kind の露出宣言
  （問いの軌跡に出るか / 教員向け k-匿名集約の対象か / わたしの地図の導出対象か）は
  本モジュールに一元宣言する。**新しい kind は露出3宣言
  （``learner_trajectory`` / ``teacher_dashboard`` / ``personal_map``）なしには
  追加できない**（:class:`TraceKindSpec` の必須フィールドで構造強制）。
- **TR2 消費面はガードレールで登録簿に一致させる** — 主要消費者
  （:data:`CONSUMERS`）のソースが宣言と食い違えば
  ``backend/tests/test_trace_registry_guardrails.py`` が落ちる。
  新しい kind を足したら、B方式（denylist）の2消費者
  （``get_interest_traces`` / ``aggregate_interest_dashboard``）の除外式を
  更新しないとテストが通らない状態を作る。
- **TR3 情報を落とさない（P4継承）** — 書き込み経路が現存しない語彙
  （kind ``detour``、status ``revisited`` / ``abstracted``）は削除せず
  ``dead=True`` マークで登録簿に保持する（既存行は存在しうる）。

本モジュールは純宣言（FastAPI / sqlalchemy / LLM 非依存）。status 語彙の正本は
``api/services.py::_TRACE_STATUSES`` のまま（各 spec の ``statuses`` はその部分集合で
あることをガードレールが固定する）。日本語 status ラベルの正本は
``core/label_vocab.py::TRACE_STATUS_LABELS``。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ALL_TRACE_KINDS",
    "CONSUMERS",
    "DASHBOARD_EXCLUDED_KINDS",
    "PERSONAL_MAP_KINDS",
    "TRACE_KINDS",
    "TRAJECTORY_EXCLUDED_KINDS",
    "TraceKindSpec",
]


@dataclass(frozen=True)
class TraceKindSpec:
    """1つの kind の宣言（TR1: 露出3宣言は必須フィールド — 省略して追加できない）。"""

    kind: str
    label: str                       # 日本語系統ラベル（台帳表示の正本）
    writers: tuple[str, ...]         # 書き込み経路の記述（ドキュメンテーション）
    statuses: frozenset[str]         # この kind で使われる status 語彙（_TRACE_STATUSES の部分集合）
    learner_trajectory: bool         # 問いの軌跡（get_interest_traces）に出るか
    teacher_dashboard: bool          # aggregate_interest_dashboard（k-匿名集約）の対象になり得るか
    personal_map: bool               # わたしの地図（personal_graph 導出）の対象か
    dead: bool = False               # 書き込み経路が現存しない語彙（TR3: 削除せず保持）


# 9 kind の宣言（2026-08-15 時点の実態調査の確定値 + 構造の降下路の
# ``backstage_question``。既存 kind の露出の意味論は変えない）。宣言順は台帳
# 「わたしの記録」の系統表示順の正本でもある（dict の挿入順）。
TRACE_KINDS: dict[str, TraceKindSpec] = {
    "question": TraceKindSpec(
        kind="question",
        label="問い",
        writers=(
            "backend/api/routes/learning.py の学習チャット3経路（通常チャット / "
            "選択テキスト質問 / 要素タップ質問。services.record_interest_trace 経由）",
        ),
        statuses=frozenset({"open", "resolved", "superseded", "revisited"}),
        learner_trajectory=True,
        teacher_dashboard=True,
        personal_map=True,
    ),
    "backstage_question": TraceKindSpec(
        kind="backstage_question",
        label="楽屋の質問",
        writers=(
            "backend/api/routes/learning.py の学習チャット"
            "（LearningChatRequest.backstage フラグ経路。"
            "services.record_interest_trace 経由。構造の降下路 "
            "docs/features/structure_descent_design.md §4）",
        ),
        statuses=frozenset({"open", "resolved", "superseded"}),
        # SD4: 楽屋は集計に入らない。記録は本人にだけ残る（台帳・問いの軌跡には出る）。
        # 教員向け k-匿名集約・わたしの地図の対象にしない。tension worker は
        # payload_flag 方式のため kind では自動除外されず、送信側（learning.py）が
        # backstage のとき tension_hint を立てないことで除外する（§6 精査記録②）。
        learner_trajectory=True,
        teacher_dashboard=False,
        personal_map=False,
    ),
    "misconception": TraceKindSpec(
        kind="misconception",
        label="誤解の記録",
        writers=(
            "backend/api/routes/learning.py の学習チャット3経路"
            "（誤解検出時。services.record_interest_trace 経由）",
        ),
        statuses=frozenset({"open", "resolved", "superseded"}),
        learner_trajectory=True,
        teacher_dashboard=True,
        personal_map=False,
    ),
    "tension": TraceKindSpec(
        kind="tension",
        label="引っかかり",
        writers=(
            "backend/core/tension/worker.py::_insert_candidate"
            "（record_interest_trace **非経由**の意図的直INSERT — "
            "analyzed_at の同時セットが必要なため。設計書 §2.4）",
            "backend/api/routes/learning.py の atlas「気になる」",
            "services.record_learner_articulated_tension（discuss 着地の"
            "「今日の理解を自分の言葉で」）",
        ),
        statuses=frozenset({
            "candidate", "open", "articulated", "connected",
            "abstracted", "dismissed", "superseded",
        }),
        learner_trajectory=True,
        teacher_dashboard=True,
        personal_map=True,
    ),
    "raw": TraceKindSpec(
        kind="raw",
        label="その他の記録",
        writers=(
            "backend/api/routes/learning.py の atlas 学習パス三択",
            "services.record_interest_trace の未知 kind 縮退先"
            "（best-effort 記録の意図的挙動。設計書 §2.4）",
        ),
        statuses=frozenset({"open", "resolved", "dismissed", "articulated"}),
        learner_trajectory=True,
        teacher_dashboard=True,
        personal_map=False,
    ),
    "detour": TraceKindSpec(
        kind="detour",
        label="寄り道",
        # 書き込み経路が現存しない（集計 FILTER 句と表示ラベルのみが参照）。
        # 既存行は存在しうるため削除しない（TR3）。
        writers=(),
        statuses=frozenset({"open", "revisited"}),
        learner_trajectory=True,
        teacher_dashboard=True,
        personal_map=False,
        dead=True,
    ),
    "help_usage": TraceKindSpec(
        kind="help_usage",
        label="使い方の質問",
        writers=(
            "backend/api/routes/learning.py の3経路"
            "（typed action / pre-route / 意図分類経由の HELP ハンドラ）",
            "backend/api/routes/admin_assistant.py の3経路"
            "（support_action / ui-anchor-events / guidance 無ヒット記録）",
        ),
        statuses=frozenset({"open", "superseded"}),
        learner_trajectory=False,
        teacher_dashboard=False,
        personal_map=False,
    ),
    "intention": TraceKindSpec(
        kind="intention",
        label="学習の意図",
        writers=("services.record_cycle_intention（leave_note 含む）",),
        statuses=frozenset({"open", "superseded", "dismissed"}),
        learner_trajectory=False,
        teacher_dashboard=False,
        personal_map=False,
    ),
    "anchor_mark": TraceKindSpec(
        kind="anchor_mark",
        label="軽量アンカー",
        writers=("services.record_cycle_anchor_mark",),
        statuses=frozenset({"open"}),
        learner_trajectory=False,
        teacher_dashboard=False,
        personal_map=False,
    ),
}


# 導出 frozenset（消費者・ガードレールが参照する正本）。
ALL_TRACE_KINDS: frozenset[str] = frozenset(TRACE_KINDS)
TRAJECTORY_EXCLUDED_KINDS: frozenset[str] = frozenset(
    spec.kind for spec in TRACE_KINDS.values() if not spec.learner_trajectory
)
DASHBOARD_EXCLUDED_KINDS: frozenset[str] = frozenset(
    spec.kind for spec in TRACE_KINDS.values() if not spec.teacher_dashboard
)
PERSONAL_MAP_KINDS: frozenset[str] = frozenset(
    spec.kind for spec in TRACE_KINDS.values() if spec.personal_map
)


# 主要消費者の宣言表（ドキュメンテーション兼テスト駆動データ。TR2）。
#
# mode:
# - "allowlist": SQL / Python の kind 許可リスト（新 kind を自動除外 — 安全）。
#   ``kinds`` の全要素がソースにクォート付きリテラルで現れることをガードレールが検査する。
# - "denylist": SQL の kind 除外リスト（新 kind のたびに手追記が必要 — 唯一の漏れ穴。
#   ``kinds`` は導出 frozenset を直接参照し、全要素の出現を検査する）。
# - "payload_flag": kind 条件なしの payload フラグ方式（``flag`` キーの逐語出現を検査）。
#
# ``function`` が None のときはモジュール全体、指定時はその関数本体
# （``tests/guardrail_helpers.extract_function_source``）を検査対象にする。
CONSUMERS: dict[str, dict] = {
    "get_interest_traces": {
        "module": "backend/api/services.py",
        "function": "get_interest_traces",
        "mode": "denylist",
        "kinds": TRAJECTORY_EXCLUDED_KINDS,
    },
    "aggregate_interest_dashboard": {
        "module": "backend/api/services.py",
        "function": "aggregate_interest_dashboard",
        "mode": "denylist",
        "kinds": DASHBOARD_EXCLUDED_KINDS,
    },
    "tension_digest": {
        "module": "backend/api/services.py",
        "function": "get_tension_digest",
        "mode": "allowlist",
        "kinds": frozenset({"tension"}),
    },
    "anchor_digest": {
        "module": "backend/api/services.py",
        "function": "get_anchor_digest",
        "mode": "allowlist",
        "kinds": frozenset({"question"}),
    },
    "personal_graph_queries": {
        "module": "backend/core/personal_graph/queries.py",
        "function": None,
        "mode": "allowlist",
        "kinds": frozenset({"tension", "question"}),
    },
    "structure_anchor_worker": {
        "module": "backend/core/structure_anchor/worker.py",
        "function": "_fetch_pending_questions",
        "mode": "allowlist",
        "kinds": frozenset({"question"}),
    },
    "tension_worker": {
        "module": "backend/core/tension/worker.py",
        "function": None,
        "mode": "payload_flag",
        "flag": "tension_hint",
        "kinds": frozenset(),
    },
    "naive_signal": {
        "module": "backend/core/doubt/naive_signal.py",
        "function": None,
        "mode": "allowlist",
        "kinds": frozenset({"question", "misconception", "tension"}),
    },
    "stumble": {
        "module": "backend/core/reconstruction/stumble.py",
        "function": None,
        "mode": "allowlist",
        "kinds": frozenset({"question"}),
    },
    "next_steps_help_gaps": {
        "module": "backend/core/admin_assistant/next_steps.py",
        "function": "_eval_manual_help_gaps_pending",
        "mode": "allowlist",
        "kinds": frozenset({"help_usage"}),
    },
    "bridges": {
        "module": "backend/core/personal_graph/bridges.py",
        "function": None,
        "mode": "allowlist",
        "kinds": frozenset({"tension"}),
    },
    "cycle_queries": {
        "module": "backend/core/cycle/queries.py",
        "function": None,
        "mode": "allowlist",
        "kinds": frozenset({"intention", "anchor_mark", "tension", "question"}),
    },
}
