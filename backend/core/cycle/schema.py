"""理解サイクル（Understanding Cycle, UCサイクル）Phase 1 の語彙定数（正本）。

正本は ``docs/features/understanding_cycle_design.md``（UC1〜UC10）。FastAPI には
依存しない（core/ 規約）。migration 不要 — ``interest_traces`` に kind を2つ
（``intention`` / ``anchor_mark``）追加するだけで、既存テーブルの CHECK なし TEXT 列に
相乗りする（020_interest_trace.sql 確認済み）。

- ``intention``: OPEN（初回動機・持ち越し問い再回答）・LEAVE（持ち越し問い選択）の痕跡。
  ``role`` は ``INTENTION_ROLES`` の3値。carryover は本人×コースにつき常に active 最大
  1件で、新しい carryover を書いたら旧行を ``superseded`` に遷移させる（UC6）。
- ``anchor_mark``: ANCHOR（軽量4ボタン）の痕跡。既存 ``structure_anchor`` 経路A
  （``attribution_source='learner_selected'``・同期・非LLM）へ相乗りし、
  ``payload.quick_label`` / ``payload.revisit`` だけを追加する（設計書 §4.2）。

いずれも本人専用メモであり、監査記帳（``theory_review_events``）は行わない
（指揮官裁定）。数値（confidence / load_score / score）をキー名に使わない（UC9）。
"""

from __future__ import annotations

KIND_INTENTION = "intention"
KIND_ANCHOR_MARK = "anchor_mark"

# intention.payload.role（設計書 §4.1）。
ROLE_OPENING_MOTIVE = "opening_motive"
ROLE_CARRYOVER_QUESTION = "carryover_question"
ROLE_REVISIT_ANSWER = "revisit_answer"

INTENTION_ROLES = (
    ROLE_OPENING_MOTIVE,
    ROLE_CARRYOVER_QUESTION,
    ROLE_REVISIT_ANSWER,
)

# 軽量アンカー4ボタン（設計書 §4.2）。既存 structure_anchor の doubt_type 語彙への
# マッピングと、日本語ラベル・「あとで戻る」だけが持つ revisit フラグを1箇所に集約する。
QUICK_LABELS: dict[str, dict[str, object]] = {
    "curious": {
        "doubt_type": "unclassified",
        "label": "気になる",
        "revisit": False,
    },
    "not_yet": {
        "doubt_type": "justification_gap",
        "label": "まだ分からない",
        "revisit": False,
    },
    "return_later": {
        "doubt_type": "unclassified",
        "label": "あとで戻る",
        "revisit": True,
    },
    "connects": {
        "doubt_type": "connection",
        "label": "何かとつながりそう",
        "revisit": False,
    },
}

QUICK_LABEL_KEYS = tuple(QUICK_LABELS.keys())
