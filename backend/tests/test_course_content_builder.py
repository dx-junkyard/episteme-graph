"""Tests for course content builder helpers."""

from __future__ import annotations


def test_course_json_dumps_strips_nul_characters():
    from core.course_content_builder import _json_dumps

    dumped = _json_dumps({
        "title": "test",
        "topics": [
            {
                "spoken_script": "before\x00after",
                "content_blocks": [{"latex": r"$R_{\\" + "\x00" + "Lambda}$"}],
                "literal_escape": r"bad\u0000escape",
            }
        ],
    })

    assert "\x00" not in dumped
    assert "\\u0000" not in dumped
    assert "beforeafter" in dumped


def test_ensure_required_equations_adds_missing_embeds():
    from core.course_content_builder import _ensure_required_equations_in_material

    result = {
        "student_material": {
            "source_format": "eg-markdown-v1",
            "source_text": "導入\n![[equation:eq_existing]]",
        },
    }
    topic = {
        "linked_equation_ids": ["eq_existing", "eq_from_id", "eq_link_only"],
        "content_blocks": [
            {
                "type": "equations",
                "items": [
                    {
                        "equation_id": "eq_existing",
                        "label": "既存式",
                        "plain_text": "既に本文で使っている式",
                    },
                    {
                        "id": "eq_from_id",
                        "label": "式A",
                        "plain_text": r"\begin{aligned} a &= b \end{aligned}",
                        "summary": "物理量の関係を定義する式",
                    },
                ],
            },
        ],
    }

    _ensure_required_equations_in_material(result, topic)
    source_text = result["student_material"]["source_text"]

    assert source_text.count("![[equation:eq_existing]]") == 1
    assert "### この節で使う数式" in source_text
    assert "- 式A: 物理量の関係を定義する式" in source_text
    assert r"\begin{aligned}" not in source_text
    assert "![[equation:eq_from_id]]" in source_text
    assert "![[equation:eq_link_only]]" in source_text


def test_detailed_check_questions_fills_legacy_question_fields():
    from core.course_content_builder import _detailed_check_questions

    topic = {
        "title": "摂動カーネル",
        "summary": "摂動カーネルは観測量と理論パラメータの対応を整理する。",
        "learning_objectives": ["カーネルの役割を説明できる"],
        "key_concepts": ["物質密度揺らぎ"],
        "linked_equation_ids": ["eq_kernel"],
        "content_blocks": [
            {
                "type": "equations",
                "items": [{"equation_id": "eq_kernel", "label": "eq:kernel"}],
            },
        ],
    }

    questions = _detailed_check_questions(["この節の中心を説明してください。"], topic)

    assert questions == [
        {
            "question": "この節の中心を説明してください。",
            "model_answer": "摂動カーネルは観測量と理論パラメータの対応を整理する。",
            "answer_requirements": [
                "物質密度揺らぎ",
                "カーネルの役割を説明できる",
                "数式 eq:kernel の意味または役割に触れる",
            ],
            "explanation": (
                "根拠となる数式を単独で読むのではなく、各記号が何を表し、"
                "その式が次の議論にどのように使われるかを確認する。"
            ),
        },
    ]


def test_normalized_legacy_check_question_is_filled_with_topic_context():
    from core.course_content_builder import _ensure_check_question_details, _normalize_topic_draft_response

    result = _normalize_topic_draft_response({
        "student_material": {"source_text": "本文"},
        "check_questions": ["問い"],
    })
    topic = {
        "summary": "この節では式を使って観測量を定義する。",
        "linked_equation_ids": ["eq_observable"],
        "content_blocks": [
            {
                "type": "equations",
                "items": [{"equation_id": "eq_observable", "label": "eq:observable"}],
            },
        ],
    }

    assert result["check_questions"][0]["model_answer"] == ""

    _ensure_check_question_details(result, topic)

    assert result["check_questions"][0]["model_answer"] == "この節では式を使って観測量を定義する。"
    assert "数式 eq:observable の意味または役割に触れる" in result["check_questions"][0]["answer_requirements"]


def test_detailed_check_questions_preserves_and_fills_partial_dict():
    from core.course_content_builder import _detailed_check_questions

    topic = {"learning_objectives": ["定義を確認する"]}

    questions = _detailed_check_questions(
        [{"question": "定義は何か", "answer_requirements": ["既存要素"]}],
        topic,
    )

    assert questions[0]["question"] == "定義は何か"
    assert questions[0]["answer_requirements"] == ["既存要素", "定義を確認する"]
    assert questions[0]["model_answer"] == "定義を確認する"
    assert questions[0]["explanation"] == "用語の暗記ではなく、前提、中心概念、結論のつながりを確認する。"


def test_fallback_topic_draft_creates_detailed_check_question():
    from core.course_content_builder import _apply_deterministic_topic_draft_fallback

    topic = {
        "title": "銀河分布",
        "summary": "銀河分布の歪度と尖度を用いてモデルを評価する。",
        "assessment_prompts": ["なぜ高次統計量を見るのか。"],
        "linked_equation_ids": ["eq_sigma"],
    }

    _apply_deterministic_topic_draft_fallback(topic)

    assert topic["check_questions"][0]["question"] == "なぜ高次統計量を見るのか。"
    assert topic["check_questions"][0]["model_answer"] == "銀河分布の歪度と尖度を用いてモデルを評価する。"
    assert topic["check_questions"][0]["answer_requirements"]
    assert topic["check_questions"][0]["explanation"]
