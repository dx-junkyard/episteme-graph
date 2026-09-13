"""コースビルダーの前提知識 半順序チェック API（学ぶ単位の一級化 Phase 2 / P2-4）。

実パス: ``POST /api/admin/course-builder/prerequisite-check``
（admin 系子ルーターとして main.py から ``prefix="/api/admin"`` で直接登録される —
routes/seminar_brief.py の admin_router と同型）。

正本: ``docs/features/learning_units_design.md`` §6.4。

- **DB を読まない・書かない**（下書き（course_draft）の chapters/topics だけを入力に取る）。
- **LLM を呼ばない**（検査は ``core/course_prerequisites.py`` の純関数, LU3）。
- 返すのは事実文だけ（件数・スコアを出さない, LU5）。学習者の痕跡は一切入力にしない（LU6）。
- コースビルダーの下書きプレビューに**事実の段落**として描くための API で、操作要素では
  ないのでフロントに ``data-ui-anchor`` は付けない（``admin-indicators.js`` の規律）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from core.course_prerequisites import analyze_prerequisite_order
from dependencies import _require_teacher

# main.py で prefix="/api/admin" を付けて直接登録される admin 系子ルーター（prefix なし）
router = APIRouter(tags=["CoursePrerequisites"])


class PrerequisiteCheckTopic(BaseModel):
    """course_draft の chapters[].topics[] 要素（素の文字列でも来る）。"""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    prerequisites: list[dict | str] = Field(default_factory=list)


class PrerequisiteCheckChapter(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str | None = None
    topics: list[PrerequisiteCheckTopic | str] = Field(default_factory=list)


class PrerequisiteCheckRequest(BaseModel):
    """コースビルダーの下書き（course_draft）。章ネスト形とフラット形の両方を受ける。"""

    model_config = ConfigDict(extra="allow")

    chapters: list[PrerequisiteCheckChapter | str] = Field(default_factory=list)
    topics: list[PrerequisiteCheckTopic | str] = Field(default_factory=list)


def _topic_dict(topic: PrerequisiteCheckTopic | str, topic_index: int, chapter_index: int) -> dict:
    """admin.js の ``approveCourse()`` と**同じ規則**で下書きの topic を組み立てる
    （``id = "t{通し番号}"`` / ``chapter_index = 章の位置``）。登録後の topic id と一致させる
    ことで、プレビューで見た事実と登録後のコースの構造が食い違わないようにする。
    """
    if isinstance(topic, str):
        title = topic.strip()
        prerequisites: list = []
    else:
        title = (topic.title or "").strip()
        prerequisites = list(topic.prerequisites or [])
    return {
        "id": f"t{topic_index}",
        "title": title,
        "chapter_index": chapter_index,
        "prerequisites": prerequisites,
    }


def _draft_topics(body: PrerequisiteCheckRequest) -> list[dict]:
    topics: list[dict] = []
    index = 0
    chapter_index = 0
    for chapter in body.chapters or []:
        if isinstance(chapter, str):
            chapter_index += 1
            continue
        for topic in chapter.topics or []:
            topics.append(_topic_dict(topic, index, chapter_index))
            index += 1
        chapter_index += 1
    for topic in body.topics or []:
        topics.append(_topic_dict(topic, index, chapter_index))
        index += 1
    return [t for t in topics if t["title"] or t["prerequisites"]]


@router.post("/course-builder/prerequisite-check")
def check_course_draft_prerequisites(
    body: PrerequisiteCheckRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """下書きの前提知識の並び（循環・冗長・未解決・前方参照）を事実文で返す。

    ``available`` は「検査が成立したか」— トピックが1件も無い下書きでは ``False`` で
    ``facts`` は空にする（何も無いことを問題として書かない）。検査が成立して問題が
    見つからなければ ``available: true`` / ``facts: []`` を返す。
    """
    topics = _draft_topics(body)
    if not topics:
        return {"available": False, "facts": []}
    report = analyze_prerequisite_order(topics)
    return {"available": True, "facts": report.to_facts()}
