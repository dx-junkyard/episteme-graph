"""LangGraph ベースのワークフロー定義。

StudentGraph: 学生向けリアルタイム対話パイプライン
"""

from core.graphs.student_graph import build_student_graph

__all__ = ["build_student_graph"]
