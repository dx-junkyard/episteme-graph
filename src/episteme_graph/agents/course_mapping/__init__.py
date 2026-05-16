"""CourseMappingAgent.

Component と Course topic を接続し、course_info.json に linked_component_ids /
learning_objectives / prerequisite_concepts / blackbox_policy を付与する。

BlueprintAgent への入力として使える教材構造を生成する。
"""
from .agent import CourseMappingAgent
from .schema import (
    CourseTopicMapping,
    CourseMappingResult,
)

__all__ = [
    "CourseMappingAgent",
    "CourseTopicMapping",
    "CourseMappingResult",
]
