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
    # 本体（plain_text）を持つ式は埋め込まれる。
    assert "![[equation:eq_from_id]]" in source_text
    # 未解決の数式 fix: 本体の無い裸ID（linked_equation_ids だけ）には埋め込みを出さない
    # （出すと必ず「未解決の数式」になるため）。説明バレットは残す。
    assert "![[equation:eq_link_only]]" not in source_text
    assert "eq_link_only" in source_text


def test_equation_has_renderable_body():
    from core.course_content_builder import _equation_has_renderable_body

    assert _equation_has_renderable_body({"latex": "a=b"})
    assert _equation_has_renderable_body({"plain_text": "aはbに等しい"})
    assert _equation_has_renderable_body({"raw_text": "F2 = a+b"})
    assert _equation_has_renderable_body({"normalized_latex": "x=y"})
    # 本体が無い（IDとラベルだけ）の式は埋め込み不可と判定される。
    assert not _equation_has_renderable_body({"equation_id": "eq_x", "label": "eq:x"})
    assert not _equation_has_renderable_body({})
    assert not _equation_has_renderable_body(None)


def test_content_blocks_carry_raw_text_for_latexless_equations():
    """未解決の数式 fix: equation items must keep a readable fallback (plain/raw)
    even when LaTeX is absent, and the description should use the reading."""
    from core.course_content_builder import (
        _content_blocks,
        _equation_material_description,
    )

    equations = [
        {"equation_id": "eq_F2", "label": "eq:F2", "raw_text": "F2 = a + b", "latex": None},
        {"equation_id": "eq_g", "label": "g", "latex": "g = h", "plain_text": "gはhに等しい"},
    ]
    blocks = _content_blocks("", [], [], equations, [])
    eq_block = next(b for b in blocks if b["type"] == "equations")
    by_id = {item["equation_id"]: item for item in eq_block["items"]}
    # LaTeX-less equation keeps a raw_text fallback rather than being dropped.
    assert by_id["eq_F2"]["raw_text"] == "F2 = a + b"
    assert by_id["eq_F2"]["latex"] in (None, "")
    assert by_id["eq_g"]["latex"] == "g = h"

    # Description falls back to the reading instead of the generic sentence.
    assert _equation_material_description({"plain_text": "F2はaとbの和"}) == "F2はaとbの和"


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


def test_topic_evidence_links_surfaces_component_equation_claim_and_source():
    from core.course_content_builder import _topic_evidence_links

    components = [
        {
            "component_id": "comp_001",
            "summary": "摂動カーネルの役割を述べるコンポーネント",
            "linked_claim_ids": ["clm_1", "clm_missing"],
            "linked_evidence_ids": ["ev_1", "ev_missing"],
        },
    ]
    equations = [{"equation_id": "eq_kernel", "plain_text": "F2 と F3 の定義"}]
    claims = {"clm_1": {"claim_id": "clm_1", "normalized_text": "カーネル係数は時間依存である"}}
    evidence = {
        "ev_1": {
            "evidence_id": "ev_1",
            "evidence_text": "We find that the kernel coefficients are time dependent.",
            "evidence_role": "source_quote",
        }
    }

    links = _topic_evidence_links(components, equations, claims, evidence, "high")
    by_key = {(l["kind"], l["target_id"]): l for l in links}

    assert ("component", "comp_001") in by_key
    assert ("equation", "eq_kernel") in by_key
    # claims.json / evidence_registry に実体があるものだけが根拠化される。
    assert ("claim", "clm_1") in by_key
    assert ("claim", "clm_missing") not in by_key
    assert ("source", "ev_1") in by_key
    assert ("source", "ev_missing") not in by_key
    assert by_key[("claim", "clm_1")]["summary"] == "カーネル係数は時間依存である"
    assert by_key[("source", "ev_1")]["summary"] == "We find that the kernel coefficients are time dependent."
    assert by_key[("source", "ev_1")]["support_role"] == "source_quote"
    assert by_key[("component", "comp_001")]["confidence"] == "high"


def test_collect_structured_content_flattens_nested_equation_latex():
    """equation_semantics 生成物のネストされた source_extraction.latex を
    トップレベル latex/plain_text に補完し、根拠リンク本文が空にならないこと。"""
    from core.course_content_builder import _collect_structured_content, _topic_evidence_links

    artifacts_by_doc = {
        "doc-1": {
            "equation_semantics": {
                "equations": [
                    {
                        "equation_id": "eq_eq_functions1",
                        "source_extraction": {
                            "latex": r"\alpha(k_1,k_2) := 1 + \frac{k_1\cdot k_2}{k_1^2}",
                            "plain_text": "alpha = 1 + ...",
                            "needs_math_review": False,
                            "extraction_status": "complete",
                        },
                        "reconstruction": {"status": "none"},
                    },
                ],
            },
        },
    }

    bundle = _collect_structured_content(artifacts_by_doc)
    eq = bundle["equations"]["eq_eq_functions1"]
    assert eq["latex"].startswith(r"\alpha(k_1,k_2)")
    assert eq["plain_text"] == "alpha = 1 + ..."

    links = _topic_evidence_links([], [eq], {}, {}, "high")
    by_key = {(l["kind"], l["target_id"]): l for l in links}
    # 描画用 latex が根拠リンクに載る（summary は意味要約用で、ここでは空でも可）。
    assert by_key[("equation", "eq_eq_functions1")]["latex"].startswith(r"\alpha(k_1,k_2)")


def test_topic_evidence_links_equation_uses_semantic_summary_and_carries_latex():
    """数式の根拠リンクは summary に生 LaTeX を入れず意味要約を使い、
    描画用に latex / label を別フィールドで持つこと。"""
    from core.course_content_builder import _topic_evidence_links

    equations = [{
        "equation_id": "eq_eq_functions1",
        "label": "eq:functions1",
        "latex": r"\alpha(k_1,k_2) := 1 + \frac{k_1\cdot k_2}{k_1^2}",
        "plain_text": "alpha = ...",
        "semantics": {"summary": "モード結合の基底関数を定義する。"},
    }]

    links = _topic_evidence_links([], equations, {}, {}, "high")
    eq_link = next(l for l in links if l["kind"] == "equation" and l["target_id"] == "eq_eq_functions1")

    assert eq_link["summary"] == "モード結合の基底関数を定義する。"
    assert not eq_link["summary"].startswith("\\")  # 生 LaTeX ではない
    assert eq_link["latex"].startswith(r"\alpha(k_1,k_2)")
    assert eq_link["label"] == "eq:functions1"


def test_equation_display_math_respects_needs_math_review_and_reconstruction():
    from core.course_content_builder import _equation_display_math

    # needs_math_review な PDF 由来数式は表示用にしない（None）。
    latex, plain = _equation_display_math({
        "source_extraction": {"latex": "garbled", "plain_text": "garbled", "needs_math_review": True},
        "reconstruction": {"status": "none"},
    })
    assert latex is None and plain is None

    # reconstruction があればそれを優先する。
    latex, plain = _equation_display_math({
        "source_extraction": {"latex": "raw", "plain_text": "raw", "needs_math_review": True},
        "reconstruction": {"status": "reconstructed", "latex": "E=mc^2", "plain_text": "E equals m c squared"},
    })
    assert latex == "E=mc^2" and plain == "E equals m c squared"

    # prose 再構成（latex_is_prose）は監査専用なので表示しない。
    latex, plain = _equation_display_math({
        "source_extraction": {"latex": "raw", "plain_text": "raw", "needs_math_review": True},
        "reconstruction": {"status": "reconstructed", "latex": "これは説明文です", "review_reason": ["latex_is_prose"]},
    })
    assert latex is None and plain is None


def test_fill_equation_display_math_respects_existing_top_level():
    from core.course_content_builder import _fill_equation_display_math

    # 既にトップレベルへフラット化済みの値は尊重する。
    eq = {"latex": "kept", "source_extraction": {"latex": "other", "needs_math_review": False}}
    _fill_equation_display_math(eq)
    assert eq["latex"] == "kept"


def test_topic_evidence_for_prompt_derives_available_references_from_links():
    from core.course_content_builder import _topic_evidence_for_prompt

    topic = {
        "evidence_links": [
            {"kind": "component", "target_id": "comp_001", "summary": "..."},
            {"kind": "equation", "target_id": "eq_kernel", "summary": "..."},
            {"kind": "claim", "target_id": "clm_1", "summary": "..."},
            {"kind": "source", "target_id": "ev_1", "summary": "..."},
        ],
        "source_excerpt": "原文の抜粋テキスト",
    }

    refs = _topic_evidence_for_prompt(topic)["available_references"]

    assert {"kind": "component", "id": "comp_001"} in refs
    assert {"kind": "equation", "id": "eq_kernel"} in refs
    assert {"kind": "claim", "id": "clm_1"} in refs
    # 特定の source span も解決可能な参照として提示される。
    assert {"kind": "source", "id": "ev_1"} in refs
    # source_excerpt がある場合は汎用の topic_summary 参照も含まれる。
    assert {"kind": "source", "id": "topic_summary"} in refs


def test_topic_evidence_for_prompt_omits_source_reference_without_excerpt():
    from core.course_content_builder import _topic_evidence_for_prompt

    topic = {"evidence_links": [{"kind": "component", "target_id": "comp_001", "summary": "..."}]}
    refs = _topic_evidence_for_prompt(topic)["available_references"]

    assert {"kind": "component", "id": "comp_001"} in refs
    assert all(ref["kind"] != "source" for ref in refs)


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
