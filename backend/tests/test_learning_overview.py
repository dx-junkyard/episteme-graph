"""Issue #60: 学習開始時オーバービュー提示機能のテスト。

- greeting_response_node が Standard モードを使用すること
- トピックメタ情報（概念・前提知識）がプロンプトに埋め込まれること
- _generate_greeting_response にメタ情報が渡されプロンプトに反映されること
- フォールバック時にもメタ情報が含まれること
"""

from __future__ import annotations

from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1. greeting_response_node オーバービューテスト
# ---------------------------------------------------------------------------


class TestGreetingResponseOverview:
    """greeting_response_node がオーバービュー形式で応答すること。"""

    def test_uses_standard_mode(self):
        """Standard モードの LLM パラメータを使用すること。"""
        from core.graphs.student_graph import greeting_response_node

        captured_params = {}

        def mock_get_llm_params(mode):
            from core.llm import get_llm_params as real_get
            captured_params["mode"] = mode
            return {"model": "test-model", "reasoning_effort": "medium"}

        with patch("core.graphs.student_graph.get_llm_params", side_effect=mock_get_llm_params):
            with patch("core.graphs.student_graph.generate_text", return_value="テスト"):
                greeting_response_node({"question": "学習を始めたい"})

        assert captured_params["mode"] == "standard"

    def test_prompt_includes_concepts(self):
        """プロンプトにトピック概念が含まれること。"""
        from core.graphs.student_graph import greeting_response_node

        with patch("core.graphs.student_graph.generate_text", return_value="テスト") as mock_gen:
            greeting_response_node({
                "question": "学習を始めたい",
                "course_title": "量子力学入門",
                "topic_title": "波動関数",
                "topic_concepts": ["シュレーディンガー方程式", "確率解釈"],
                "topic_prerequisites": [],
            })
            prompt = mock_gen.call_args[1]["messages"][0]["content"]
            assert "シュレーディンガー方程式" in prompt
            assert "確率解釈" in prompt

    def test_prompt_includes_prerequisites(self):
        """プロンプトに前提知識が含まれること。"""
        from core.graphs.student_graph import greeting_response_node

        with patch("core.graphs.student_graph.generate_text", return_value="テスト") as mock_gen:
            greeting_response_node({
                "question": "学習を始めたい",
                "course_title": "量子力学入門",
                "topic_title": "摂動論",
                "topic_concepts": [],
                "topic_prerequisites": ["波動関数の基礎", "線形代数"],
            })
            prompt = mock_gen.call_args[1]["messages"][0]["content"]
            assert "波動関数の基礎" in prompt
            assert "線形代数" in prompt

    def test_prompt_contains_overview_structure(self):
        """プロンプトにオーバービューの4構成要素が含まれること。"""
        from core.graphs.student_graph import greeting_response_node

        with patch("core.graphs.student_graph.generate_text", return_value="テスト") as mock_gen:
            greeting_response_node({
                "question": "こんにちは",
                "course_title": "量子力学入門",
                "topic_title": "波動関数",
            })
            prompt = mock_gen.call_args[1]["messages"][0]["content"]
            assert "歓迎と目標" in prompt
            assert "構成要素" in prompt
            assert "前提知識の確認" in prompt
            assert "ネクストアクション" in prompt

    def test_prompt_no_detailed_explanation_note(self):
        """プロンプトに具体的解説を行わない注意書きがあること。"""
        from core.graphs.student_graph import greeting_response_node

        with patch("core.graphs.student_graph.generate_text", return_value="テスト") as mock_gen:
            greeting_response_node({
                "question": "こんにちは",
                "course_title": "量子力学入門",
                "topic_title": "波動関数",
            })
            prompt = mock_gen.call_args[1]["messages"][0]["content"]
            assert "具体的な解説" in prompt

    def test_no_concepts_no_prereqs_still_works(self):
        """概念・前提知識が空でも正常に動作すること。"""
        from core.graphs.student_graph import greeting_response_node

        with patch("core.graphs.student_graph.generate_text", return_value="テスト") as mock_gen:
            result = greeting_response_node({
                "question": "こんにちは",
                "topic_concepts": [],
                "topic_prerequisites": [],
            })
            assert result["raw_answer"] == "テスト"
            prompt = mock_gen.call_args[1]["messages"][0]["content"]
            # メタ情報ブロックが空でもプロンプト構造は維持
            assert "歓迎と目標" in prompt

    def test_fallback_includes_topic_title(self):
        """フォールバック時にトピック名が含まれること。"""
        from core.graphs.student_graph import greeting_response_node

        with patch("core.graphs.student_graph.generate_text", side_effect=Exception("LLM error")):
            result = greeting_response_node({
                "question": "学習を始めたい",
                "topic_title": "摂動論",
            })
            assert "摂動論" in result["raw_answer"]
            assert "前提知識" in result["raw_answer"]


# ---------------------------------------------------------------------------
# 2. _generate_greeting_response 相当ロジックのテスト
# ---------------------------------------------------------------------------
# learning.py は FastAPI + DB 依存が多いため直接 import できない。
# 既存テスト (test_greeting_meta_dialogue.py) と同様に、
# 対象ロジックを複製してユニットテストする。


def _build_overview_prompt(
    course_title: str,
    topic_title: str,
    *,
    topic_info: dict | None = None,
    course_data: dict | None = None,
) -> str:
    """learning.py の _generate_greeting_response 内のプロンプト構築ロジックを複製。"""
    prerequisites: list[str] = []
    concepts: list[str] = []

    if topic_info:
        for p in topic_info.get("prerequisites", []):
            name = p.get("name", p) if isinstance(p, dict) else str(p)
            if name:
                prerequisites.append(name)

    if course_data:
        for c in course_data.get("concepts", []):
            name = c.get("name", c) if isinstance(c, dict) else str(c)
            if name:
                concepts.append(name)

    concepts_block = ""
    if concepts:
        concepts_block = "■ このトピックで習得すべき主要概念:\n" + "\n".join(
            f"  - {c}" for c in concepts
        ) + "\n\n"

    prereqs_block = ""
    if prerequisites:
        prereqs_block = "■ このトピックに必要な前提知識:\n" + "\n".join(
            f"  - {p}" for p in prerequisites
        ) + "\n\n"

    return (
        f"あなたは「{course_title}」の学習をサポートするAIチューターです。\n"
        f"学生が新しく「{topic_title}」の学習を開始しようとしています。\n\n"
        f"{concepts_block}"
        f"{prereqs_block}"
        "以下の構成で、学習の導入となる最初のメッセージを作成してください:\n"
        "1. 【歓迎と目標】このトピックで学ぶことの全体像と、最終的な学習目標を簡潔に説明する。\n"
        "2. 【構成要素】習得すべき主要な概念をリストアップする。\n"
        "3. 【前提知識の確認】このトピックを学ぶために必要な前提知識を提示する。\n"
        "4. 【ネクストアクション】「まずは前提知識の復習から始めますか？ それとも最初の概念の説明に進みますか？」と、学生に次の行動を選ばせる質問で締めくくる。\n\n"
        "※注意: ここでは具体的な解説（数式展開など）はまだ行わないこと。"
    )


def _build_fallback(
    course_title: str,
    topic_title: str,
    concepts: list[str],
    prerequisites: list[str],
) -> str:
    """learning.py のフォールバックロジックを複製。"""
    return (
        f"「{course_title}」の学習サポートへようこそ！\n\n"
        f"これから「{topic_title}」の学習を始めます。\n\n"
        + (f"**習得すべき主要概念:** {', '.join(concepts)}\n\n" if concepts else "")
        + (f"**必要な前提知識:** {', '.join(prerequisites)}\n\n" if prerequisites else "")
        + "まずは前提知識の復習から始めますか？ それとも最初の概念の説明に進みますか？"
    )


class TestOverviewPromptBuilder:
    """オーバービュープロンプト構築ロジックのテスト（learning.py 相当）。"""

    def test_prompt_includes_topic_prerequisites(self):
        """topic_info の prerequisites がプロンプトに含まれること。"""
        topic_info = {
            "prerequisites": [
                {"name": "波動関数の基礎", "status": "not_started"},
                {"name": "線形代数", "status": "mastered"},
            ],
        }
        prompt = _build_overview_prompt(
            "量子力学入門", "摂動論", topic_info=topic_info,
        )
        assert "波動関数の基礎" in prompt
        assert "線形代数" in prompt

    def test_prompt_includes_course_concepts(self):
        """course_data の concepts がプロンプトに含まれること。"""
        course_data = {
            "concepts": [
                {"name": "波動関数", "status": "future"},
                {"name": "ハミルトニアン", "status": "future"},
            ],
        }
        prompt = _build_overview_prompt(
            "量子力学入門", "波動関数", course_data=course_data,
        )
        assert "波動関数" in prompt
        assert "ハミルトニアン" in prompt

    def test_overview_structure_in_prompt(self):
        """プロンプトにオーバービューの4構成要素が含まれること。"""
        prompt = _build_overview_prompt("量子力学入門", "波動関数")
        assert "歓迎と目標" in prompt
        assert "構成要素" in prompt
        assert "前提知識の確認" in prompt
        assert "ネクストアクション" in prompt

    def test_string_prerequisites_handled(self):
        """前提知識が文字列リスト形式でも処理できること。"""
        topic_info = {
            "prerequisites": ["波動関数の基礎", "線形代数"],
        }
        prompt = _build_overview_prompt(
            "量子力学入門", "摂動論", topic_info=topic_info,
        )
        assert "波動関数の基礎" in prompt
        assert "線形代数" in prompt

    def test_no_metadata_generates_valid_prompt(self):
        """メタ情報なしでも有効なプロンプトが生成されること。"""
        prompt = _build_overview_prompt("コースA", "トピックB")
        assert "コースA" in prompt
        assert "トピックB" in prompt
        assert "歓迎と目標" in prompt

    def test_empty_prerequisites_no_block(self):
        """前提知識が空の場合、前提知識ブロックが含まれないこと。"""
        topic_info = {"prerequisites": []}
        prompt = _build_overview_prompt(
            "コースA", "トピックB", topic_info=topic_info,
        )
        assert "■ このトピックに必要な前提知識" not in prompt

    def test_empty_concepts_no_block(self):
        """概念が空の場合、概念ブロックが含まれないこと。"""
        course_data = {"concepts": []}
        prompt = _build_overview_prompt(
            "コースA", "トピックB", course_data=course_data,
        )
        assert "■ このトピックで習得すべき主要概念" not in prompt


class TestOverviewFallback:
    """フォールバックメッセージにメタ情報が含まれること。"""

    def test_fallback_includes_all_metadata(self):
        result = _build_fallback(
            "量子力学入門", "摂動論",
            concepts=["ハミルトニアン"],
            prerequisites=["波動関数の基礎"],
        )
        assert "量子力学入門" in result
        assert "摂動論" in result
        assert "ハミルトニアン" in result
        assert "波動関数の基礎" in result
        assert "前提知識の復習" in result

    def test_fallback_no_metadata(self):
        result = _build_fallback("コースA", "トピックB", [], [])
        assert "コースA" in result
        assert "トピックB" in result
        assert "前提知識の復習" in result

    def test_fallback_concepts_only(self):
        result = _build_fallback(
            "コースA", "トピックB",
            concepts=["概念X", "概念Y"],
            prerequisites=[],
        )
        assert "概念X" in result
        assert "概念Y" in result
        assert "必要な前提知識" not in result


# ---------------------------------------------------------------------------
# 3. StudentState メタ情報フィールドテスト
# ---------------------------------------------------------------------------


class TestStudentStateTopicMetadata:
    """StudentState にトピックメタ情報フィールドが定義されていること。"""

    def test_topic_concepts_field_exists(self):
        from core.graphs.state import StudentState
        annotations = StudentState.__annotations__
        assert "topic_concepts" in annotations

    def test_topic_prerequisites_field_exists(self):
        from core.graphs.state import StudentState
        annotations = StudentState.__annotations__
        assert "topic_prerequisites" in annotations

    def test_state_accepts_metadata(self):
        """メタ情報を含む State を作成できること。"""
        from core.graphs.state import StudentState
        state: StudentState = {
            "question": "こんにちは",
            "topic_concepts": ["波動関数", "確率解釈"],
            "topic_prerequisites": ["線形代数"],
        }
        assert state["topic_concepts"] == ["波動関数", "確率解釈"]
        assert state["topic_prerequisites"] == ["線形代数"]
