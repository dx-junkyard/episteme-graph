"""Structured learning support orchestration.

This module keeps learning-path detours explicit: prerequisite reviews,
detail explanations, return-to-path actions, and check-question prompts are
represented as structured state instead of only prose in chat text.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


# 本文に埋め込まれたクリック候補マーカー。フロント側でのパース（\x00 センチネル方式）を
# 廃し、ここ（サーバ側）の1か所で構造化アクションへ正規化する。
_ACTION_BUTTON_RE = re.compile(r"\[ACTION_BUTTON:\s*([^\]\n]{1,120})\]")
_DRILLDOWN_RE = re.compile(r"\[([^\]\n]{2,80}?について(?:詳しく)?(?:聞く|教えて|教えてください|知りたい))\]")


@dataclass
class LearningSupportOrigin:
    course_id: str
    topic_id: str
    topic_title: str
    chapter_title: str = ""


@dataclass
class LearningSupportAction:
    type: str
    label: str
    message: str = ""
    target_topic_id: str | None = None


@dataclass
class LearningSupportResult:
    answer: str
    mode: str = "normal"
    status_label: str = ""
    origin: LearningSupportOrigin | None = None
    next_actions: list[LearningSupportAction] = field(default_factory=list)

    def model_dump(self) -> dict:
        return {
            "answer": self.answer,
            "support_mode": self.mode,
            "status_label": self.status_label,
            "origin": asdict(self.origin) if self.origin else None,
            "next_actions": [asdict(action) for action in self.next_actions],
        }


class LearningSupportAgent:
    """Builds structured UX state for learning support detours."""

    def __init__(self, course_id: str, course_data: dict):
        self.course_id = course_id
        self.course_data = course_data or {}

    def origin_for_topic(self, topic_id: str, topic_info: dict | None) -> LearningSupportOrigin:
        topic = topic_info or {}
        chapter_title = ""
        chapter_index = topic.get("chapter_index")
        chapters = self.course_data.get("chapters") or []
        if isinstance(chapter_index, int) and 0 <= chapter_index < len(chapters):
            chapter = chapters[chapter_index] or {}
            chapter_title = str(chapter.get("title") or "")
        return LearningSupportOrigin(
            course_id=self.course_id,
            topic_id=topic_id,
            topic_title=str(topic.get("title") or topic_id),
            chapter_title=chapter_title,
        )

    def return_to_path_result(self, origin: dict | None) -> LearningSupportResult:
        topic_title = str((origin or {}).get("topic_title") or "元の学習トピック")
        return LearningSupportResult(
            answer=f"元の学習パス「{topic_title}」に戻ります。続きに進む前に、必要なら確認問題で理解を確認します。",
            mode="return_to_learning_path",
        )

    def with_learning_actions(
        self,
        *,
        answer: str,
        mode: str,
        origin: LearningSupportOrigin,
        include_continue: bool = True,
        continue_label: str = "前提知識をもう少し確認する",
        continue_message: str = "前提知識をもう少し確認する",
        extra_actions: list[LearningSupportAction] | None = None,
        status_label: str = "詳細説明中",
    ) -> LearningSupportResult:
        """detour（寄り道）状態の構造化レスポンスを組み立てる。

        どの入口由来でも先頭は必ず「学習パスに戻る」（復帰導線）にし、続いて任意の
        ``extra_actions``（本文由来のドリルダウン等）、必要なら継続アクション、最後に
        質問アクションを並べる。
        """
        actions = [
            LearningSupportAction(
                type="return_to_learning_path",
                label="学習パスに戻る",
                message="学習パスに戻る",
                target_topic_id=origin.topic_id,
            )
        ]
        if extra_actions:
            for action in extra_actions:
                actions.append(action)
        if include_continue:
            actions.append(
                LearningSupportAction(
                    type="continue_detail",
                    label=continue_label,
                    message=continue_message,
                )
            )
        actions.append(
            LearningSupportAction(
                type="ask_question",
                label="質問する",
                message="質問したいことがあります",
            )
        )
        return LearningSupportResult(
            answer=answer,
            mode=mode,
            status_label=status_label,
            origin=origin,
            next_actions=actions,
        )

    def advice_actions(
        self,
        origin: LearningSupportOrigin,
        topic_title: str,
        first_concept: str | None = None,
    ) -> list[LearningSupportAction]:
        """学習相談・トピック導入（パス上）の前進アクション。

        detour ではないので「学習パスに戻る」は付けず、前提確認への分岐と本トピックの
        解説開始の2つを型付きで返す。
        """
        concept_label = (first_concept or topic_title or "").strip() or "最初の概念"
        return [
            LearningSupportAction(
                type="review_prerequisite",
                label="前提知識を確認する",
                message="このコースに必要な前提知識を確認する",
                target_topic_id=origin.topic_id,
            ),
            LearningSupportAction(
                type="start_topic",
                label=f"「{concept_label}」の説明に進む",
                message=f"{concept_label}について説明してください",
                target_topic_id=origin.topic_id,
            ),
        ]

    def prerequisite_choice_actions(
        self,
        first_prerequisite: str,
    ) -> list[LearningSupportAction]:
        """前提知識チェックの介入時に提示する「はい/いいえ」選択肢（型付き）。

        ``with_learning_actions`` の ``extra_actions`` として渡される前提なので、
        先頭の「学習パスに戻る」はここには含めない。
        """
        actions = [
            LearningSupportAction(
                type="continue_detail",
                label="はい、理解しています",
                message="はい、理解しています",
            )
        ]
        first = (first_prerequisite or "").strip()
        if first:
            actions.append(
                LearningSupportAction(
                    type="drilldown",
                    label=f"いいえ、「{first}」から教えてほしい",
                    message=f"{first}について教えてください",
                )
            )
        return actions

    @staticmethod
    def is_prerequisite_request(message: str) -> bool:
        msg = (message or "").strip()
        return "前提知識" in msg and ("確認" in msg or "復習" in msg or "必要" in msg)


def extract_inline_actions(text: str) -> tuple[str, list[LearningSupportAction]]:
    """LLM 本文中のクリック候補マーカーを構造化アクションへ変換し、本文から除去する。

    対象は ``[ACTION_BUTTON: 〇〇]`` と ``[〇〇について(詳しく)?聞く/教えて]`` の2形式。
    本文側のフラグメント解析はここ（サーバ側・決定論的）に一元化し、フロントは
    ``next_actions`` のみを描画する（型付き送信）。日本語ラベルを再び intent 分類へ
    通さないよう、抽出したアクションは ``type="drilldown"`` を付与する。

    Returns
    -------
    (clean_text, actions)
        マーカーを除去した本文と、抽出した ``LearningSupportAction`` のリスト。
    """
    actions: list[LearningSupportAction] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        label = (label or "").strip()
        if not label or label in seen:
            return
        seen.add(label)
        actions.append(
            LearningSupportAction(type="drilldown", label=label, message=label)
        )

    def _sub(match: re.Match) -> str:
        _add(match.group(1))
        return ""

    clean = _ACTION_BUTTON_RE.sub(_sub, text or "")
    clean = _DRILLDOWN_RE.sub(_sub, clean)
    # マーカー除去で生じた空行を畳む。
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, actions

