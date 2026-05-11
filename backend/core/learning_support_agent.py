"""Structured learning support orchestration.

This module keeps learning-path detours explicit: prerequisite reviews,
detail explanations, return-to-path actions, and check-question prompts are
represented as structured state instead of only prose in chat text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


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
    ) -> LearningSupportResult:
        actions = [
            LearningSupportAction(
                type="return_to_learning_path",
                label="学習パスに戻る",
                message="学習パスに戻る",
                target_topic_id=origin.topic_id,
            )
        ]
        if include_continue:
            actions.append(
                LearningSupportAction(
                    type="continue_detail",
                    label="前提知識をもう少し確認する",
                    message="前提知識をもう少し確認する",
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
            status_label="詳細説明中",
            origin=origin,
            next_actions=actions,
        )

    @staticmethod
    def is_prerequisite_request(message: str) -> bool:
        msg = (message or "").strip()
        return "前提知識" in msg and ("確認" in msg or "復習" in msg or "必要" in msg)

