"""理解サイクル（Understanding Cycle, UCサイクル）Phase 1 の語彙定数（正本）。

正本は ``docs/features/understanding_cycle_design.md``（UC1〜UC10）。FastAPI には
依存しない（core/ 規約）。migration 不要 — ``interest_traces`` に kind を2つ
（``intention`` / ``anchor_mark``）追加するだけで、既存テーブルの CHECK なし TEXT 列に
相乗りする（020_interest_trace.sql 確認済み）。

- ``intention``: OPEN（初回動機・持ち越し問い再回答）・LEAVE（持ち越し問い選択）の痕跡。
  ``role`` は ``INTENTION_ROLES`` の4値（帰還の扉の ``leave_note`` を含む）。carryover は本人×コースにつき常に active 最大
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

# intention.payload.role（設計書 §4.1 + 帰還の扉 §2.1）。
ROLE_OPENING_MOTIVE = "opening_motive"
ROLE_CARRYOVER_QUESTION = "carryover_question"
ROLE_REVISIT_ANSWER = "revisit_answer"
# 帰還の扉（return_door_design.md §2.1）: LEAVE の「未来の自分への書き置き」。
# carryover と同じ「本人×コースにつき active 最大1件・新規記録時に旧行 superseded」規約。
ROLE_LEAVE_NOTE = "leave_note"

INTENTION_ROLES = (
    ROLE_OPENING_MOTIVE,
    ROLE_CARRYOVER_QUESTION,
    ROLE_REVISIT_ANSWER,
    ROLE_LEAVE_NOTE,
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


# ---------------------------------------------------------------------------
# 帰還の扉が空のときの事実文（IK-0418）
# ---------------------------------------------------------------------------
# 空の扉は ``empty: true`` だけを返していた。何が無いのか・どうすれば残せるかを
# 事実で言う。画面は RD3（書かなければ何も出ない）どおり描かない。
EMPTY_DOOR_FACT = (
    "このコースには、次の自分への書き置き・持ち越した問い・確定した引っかかりが"
    "まだ残っていません。"
)
EMPTY_DOOR_HINT = (
    "論文との議論を終えるときに「次の自分への書き置き」か「持ち越す問い」を残すと、"
    "次に開いたときここに出ます。議論の最初に書いた「開いた動機」はここには出ません。"
)

# ---------------------------------------------------------------------------
# 「今日のあなたの言葉」から外す相づち・了解（IK-0417）
# ---------------------------------------------------------------------------
#: 比較の前に落とす文字（句読点・空白・記号）。
FILLER_STRIP_CHARS = frozenset(" 　、。，．,.!！・…〜~\n\t")
#: 句読点を落とした完全一致で相づちとみなす発話。
FILLER_UTTERANCES = frozenset({
    "はい", "うん", "ええ", "了解", "了解です", "了解しました", "わかりました",
    "分かりました", "わかった", "分かった", "理解しました", "理解しています",
    "はい理解しています", "はい理解しました", "はいわかりました", "はい分かりました",
    "ありがとう", "ありがとうございます", "ありがとうございました", "ok", "OK", "オッケー",
    "大丈夫です", "はい大丈夫です",
})
#: 問いを含まない短い発話で、この語で始まるものも相づちとみなす。
FILLER_PREFIXES = ("はい", "うん", "了解", "わかりました", "分かりました")
#: 上の前方一致を使う最大長（句読点を落とした文字数）。長い発話は中身があるので残す。
FILLER_SHORT_MAX_CHARS = 10
