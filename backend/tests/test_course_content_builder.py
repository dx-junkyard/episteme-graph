"""Tests for course content builder helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


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


def test_content_blocks_components_projection_includes_rich_fields():
    """component_evidence_redesign.md Phase 1: components 投影は裸IDではなく
    接続の説明文（narrative_role / preconditions・inputs・outputs・cautions の
    text 付き投影 / dependencies の reason 付き投影 / 数式の役割分類 / claims）を運ぶ。"""
    from core.course_content_builder import _content_blocks

    components = [
        {
            "component_id": "comp_1",
            "label": "ラベル1",
            "summary": "要約1",
            "teaching_takeaway": "要点1",
            "document_id": "doc-1",
            "narrative_role": "この段階は中心主張の前提を与える。",
            "preconditions": [
                {"text": "前提A", "claim_ids": ["clm_1"], "equation_ids": []},
                {"text": "", "claim_ids": ["ignored"]},  # text 空は落ちる
            ],
            "inputs": [{"text": "入力A", "claim_ids": [], "equation_ids": ["eq_1"]}],
            "outputs": [{"text": "出力A"}],
            "cautions": [{"text": "注意A"}],
            "dependencies": [
                {"dependency_type": "requires", "component_refs": ["comp_2"], "reason": "comp_2 の結果を使う"},
                {"dependency_type": "x", "component_refs": [], "reason": ""},  # 両方空は落ちる
            ],
            "input_equation_ids": ["eq_1"],
            "intermediate_equation_ids": ["eq_2"],
            "output_equation_ids": ["eq_3"],
            "constraint_equation_ids": [],
            "definition_equation_ids": [],
            "linked_equation_ids": ["eq_1", "eq_4"],
            "linked_claim_ids": ["clm_1", "clm_2"],
        },
    ]

    blocks = _content_blocks("", [], components, [], [])
    comp_block = next(b for b in blocks if b["type"] == "components")
    item = comp_block["items"][0]

    assert item["narrative_role"] == "この段階は中心主張の前提を与える。"
    assert item["document_id"] == "doc-1"
    assert item["preconditions"] == [{"text": "前提A", "claim_ids": ["clm_1"], "equation_ids": []}]
    assert item["inputs"] == [{"text": "入力A", "claim_ids": [], "equation_ids": ["eq_1"]}]
    assert item["outputs"] == [{"text": "出力A", "claim_ids": [], "equation_ids": []}]
    assert item["cautions"] == [{"text": "注意A", "claim_ids": [], "equation_ids": []}]
    assert item["dependencies"] == [{"type": "requires", "targets": ["comp_2"], "reason": "comp_2 の結果を使う"}]
    # 役割別リストで初出優先 dedupe。linked_equation_ids のみにある eq_4 は role="linked"。
    assert item["equations"] == [
        {"id": "eq_1", "role": "input"},
        {"id": "eq_2", "role": "intermediate"},
        {"id": "eq_3", "role": "output"},
        {"id": "eq_4", "role": "linked"},
    ]
    assert item["claims"] == ["clm_1", "clm_2"]
    # 既存4フィールドは維持（フロント互換）。
    assert item["component_id"] == "comp_1"
    assert item["label"] == "ラベル1"
    assert item["summary"] == "要約1"
    assert item["teaching_takeaway"] == "要点1"


def test_content_blocks_components_projection_defaults_when_fields_absent():
    """narrative_annotator artifact が無い / ComponentRecord に新フィールドが
    無い（古い成果物）場合でも空文字・空リストへ縮退し、壊れない。"""
    from core.course_content_builder import _content_blocks

    components = [{"component_id": "comp_old", "label": "旧ラベル"}]
    blocks = _content_blocks("", [], components, [], [])
    item = next(b for b in blocks if b["type"] == "components")["items"][0]

    assert item["narrative_role"] == ""
    assert item["document_id"] == ""
    assert item["preconditions"] == []
    assert item["inputs"] == []
    assert item["outputs"] == []
    assert item["cautions"] == []
    assert item["dependencies"] == []
    assert item["equations"] == []
    assert item["claims"] == []


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


def test_topic_evidence_links_component_carries_label():
    """component の根拠リンクは label を持つ（build_topic_evidence_items が
    title に summary を流用せず label を使えるようにする, Phase 1 §3）。"""
    from core.course_content_builder import _topic_evidence_links

    components = [{"component_id": "comp_1", "label": "ラベルA", "summary": "要約"}]
    links = _topic_evidence_links(components, [], {}, {}, "high")
    by_key = {(l["kind"], l["target_id"]): l for l in links}

    assert by_key[("component", "comp_1")]["label"] == "ラベルA"


def test_topic_evidence_links_component_without_label_has_no_label_key():
    from core.course_content_builder import _topic_evidence_links

    components = [{"component_id": "comp_1", "summary": "要約"}]
    links = _topic_evidence_links(components, [], {}, {}, "high")
    by_key = {(l["kind"], l["target_id"]): l for l in links}

    assert "label" not in by_key[("component", "comp_1")]


def test_collect_structured_content_joins_narrative_role_from_annotator():
    """narrative_annotator の node_narratives（component_id は component_assembly と
    共有する名前空間）から narrative_role を components 投影へ join する
    （component_evidence_redesign.md Phase 1 §1）。narrative の生 confidence は
    投影に混ぜない。"""
    from core.course_content_builder import _collect_structured_content

    artifacts_by_doc = {
        "doc-1": {
            "component_assembly": {
                "components": [
                    {"component_id": "comp_1", "label": "L1", "confidence": 0.4},
                    {"component_id": "comp_2", "label": "L2"},
                ],
            },
            "narrative_annotator": {
                "node_narratives": [
                    {"component_id": "comp_1", "narrative_role": "導入段階", "reason": "x", "confidence": 0.9},
                    {"component_id": "comp_missing", "narrative_role": "存在しないコンポーネント"},
                ],
            },
        },
    }

    bundle = _collect_structured_content(artifacts_by_doc)
    comp1 = bundle["components"]["comp_1"]
    comp2 = bundle["components"]["comp_2"]

    assert comp1["narrative_role"] == "導入段階"
    assert "narrative_role" not in comp2
    # narrative 由来の生 confidence (0.9) が component 自身の confidence (0.4) を
    # 上書きしない（投影に narrative の数値を混ぜない）。
    assert comp1["confidence"] == 0.4


def test_collect_structured_content_narrative_artifact_missing_or_malformed_is_skipped():
    """narrative_annotator artifact が欠落 / dict でない / node_narratives の要素が
    dict でない場合も静かにスキップされ、例外を投げない（防御的）。"""
    from core.course_content_builder import _collect_structured_content

    # 1) narrative_annotator キー自体が無い。
    bundle = _collect_structured_content({
        "doc-1": {"component_assembly": {"components": [{"component_id": "comp_1", "label": "L1"}]}},
    })
    assert "narrative_role" not in bundle["components"]["comp_1"]

    # 2) narrative_annotator が dict でない（壊れた artifact）。
    bundle = _collect_structured_content({
        "doc-1": {
            "component_assembly": {"components": [{"component_id": "comp_1", "label": "L1"}]},
            "narrative_annotator": "not-a-dict",
        },
    })
    assert "narrative_role" not in bundle["components"]["comp_1"]

    # 3) node_narratives の要素が dict でない。
    bundle = _collect_structured_content({
        "doc-1": {
            "component_assembly": {"components": [{"component_id": "comp_1", "label": "L1"}]},
            "narrative_annotator": {"node_narratives": ["not-a-dict", None, 42]},
        },
    })
    assert "narrative_role" not in bundle["components"]["comp_1"]

    # 4) narrative_role が空文字（LLM が空を返した）は付与しない。
    bundle = _collect_structured_content({
        "doc-1": {
            "component_assembly": {"components": [{"component_id": "comp_1", "label": "L1"}]},
            "narrative_annotator": {"node_narratives": [{"component_id": "comp_1", "narrative_role": ""}]},
        },
    })
    assert "narrative_role" not in bundle["components"]["comp_1"]


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


def test_topic_evidence_links_equation_quote_source_carries_full_latex():
    """equation_quote の source 根拠リンクは evidence_text（生 TeX）を切り詰めずに
    latex フィールドで全文保持し、summary 経由の 220 字切り詰めで TeX を壊さないこと
    （arXiv TeX アーカイブ由来の \\begin{aligned}...\\end{aligned} が "\\end{a..." に
    壊れていた退行の再発防止）。"""
    from core.course_content_builder import _topic_evidence_links

    # 220 字を超える現実的な aligned 数式（arXiv-2508.06322 の sum rule 相当）。
    long_latex = (
        r"\begin{aligned} \frac{R_{\Lambda_c}}{R_{\Lambda_c}^{SM}} = "
        r"a^{\rm HQ} \frac{R_{D}}{R_{D}^{SM}} + b^{\rm HQ} \frac{R_{D^*}}{R_{D^*}^{SM}} "
        r"\,,~~~ \text{with}~~ a^{\rm HQ} = \frac{1}{4},~ b^{\rm HQ} = \frac{3}{4} \,. "
        r"\end{aligned}"
    )
    assert len(long_latex) > 220
    components = [{"component_id": "comp_1", "linked_evidence_ids": ["ev_eq_1"]}]
    evidence = {
        "ev_eq_1": {
            "evidence_id": "ev_eq_1",
            "evidence_text": long_latex,
            "evidence_role": "equation_quote",
        }
    }

    links = _topic_evidence_links(components, [], {}, evidence, "exact_title")
    src = next(l for l in links if l["kind"] == "source" and l["target_id"] == "ev_eq_1")

    assert src["latex"] == long_latex  # 全文・無切り詰め
    assert src["latex"].endswith(r"\end{aligned}")
    assert "..." not in src.get("summary", "")
    assert not src.get("summary", "").startswith("\\begin")  # 生 TeX を summary に出さない
    assert src["support_role"] == "equation_quote"


def test_topic_evidence_links_tex_like_source_quote_is_promoted_to_latex():
    """role が equation_quote でなくても本文が生 TeX なら latex に昇格し、
    途中切り詰めの対象にしないこと（他論文・他経路への汎用ガード）。"""
    from core.course_content_builder import _topic_evidence_links

    tex_body = r"\frac{d\Gamma}{dq^2} = \frac{G_F^2 |V_{cb}|^2}{192\pi^3 m_b^3} \sum_i |H_i|^2" * 4
    components = [{"component_id": "comp_1", "linked_evidence_ids": ["ev_2"]}]
    evidence = {
        "ev_2": {
            "evidence_id": "ev_2",
            "evidence_text": tex_body,
            "evidence_role": "source_quote",
        }
    }

    links = _topic_evidence_links(components, [], {}, evidence, "high")
    src = next(l for l in links if l["kind"] == "source" and l["target_id"] == "ev_2")

    assert src["latex"] == tex_body
    assert "..." not in src.get("summary", "")


def test_topic_evidence_links_prose_source_quote_still_truncated_as_text():
    """散文の source 引用は従来どおり 220 字で要約されること（TeX ガードの誤爆なし）。
    散文中に数式コマンドが少し混ざっても TeX 扱いしない。"""
    from core.course_content_builder import _topic_evidence_links

    prose = (
        "The coefficients \\alpha_R and \\beta_R are independent of NP effects. "
        "The sum rule provides a powerful tool for cross-checking the consistency "
        "of experimental data and testing the validity of SM predictions. "
    ) * 3
    components = [{"component_id": "comp_1", "linked_evidence_ids": ["ev_3"]}]
    evidence = {
        "ev_3": {
            "evidence_id": "ev_3",
            "evidence_text": prose,
            "evidence_role": "source_quote",
        }
    }

    links = _topic_evidence_links(components, [], {}, evidence, "high")
    src = next(l for l in links if l["kind"] == "source" and l["target_id"] == "ev_3")

    assert "latex" not in src
    assert len(src["summary"]) <= 223  # 220 + "..."


def test_looks_like_tex_math_heuristic():
    from core.course_content_builder import _looks_like_tex_math

    # 数式環境があれば即 TeX。
    assert _looks_like_tex_math(r"\begin{aligned} x &= y \end{aligned}")
    assert _looks_like_tex_math(r"\begin{align*} a &= b \\ c &= d \end{align*}")
    assert _looks_like_tex_math(r"\begin{pmatrix} a & b \end{pmatrix}")
    # 環境なしでもコマンド密度が高ければ TeX。
    assert _looks_like_tex_math(r"\frac{R_{\Lambda_c}}{R_{\Lambda_c}^{SM}} - \alpha_R \frac{R_D}{R_D^{SM}}")
    # 散文は False（数式コマンドが少し混ざっても）。
    assert not _looks_like_tex_math("The sum rule provides a powerful cross-check of the data.")
    assert not _looks_like_tex_math(
        "We denote the ratio by \\alpha_R in the following discussion, "
        "which is independent of new physics contributions to the decay rates."
    )
    assert not _looks_like_tex_math("")


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


# ---------------------------------------------------------------------------
# 回帰テスト: build_course_content はトピック draft 再生成後にトピック音声
# キャッシュ (topic_lecture_audio_cache) を無効化しなければならない。
#
# build_course_content は全トピックの student_material/spoken_script を
# 無条件で上書きする (_generate_course_topic_drafts) が、個別トピック編集経路
# (routes/lecture_studio/topics.py::save_lecture_studio_course_topic) にはある
# DELETE FROM topic_lecture_audio_cache がここには無かった。表示スライドと
# 音声スライドの食い違いを防ぐため、コース単位で無効化されることを確認する。
# ---------------------------------------------------------------------------


def test_invalidate_topic_lecture_audio_cache_issues_course_scoped_delete():
    from core.course_content_builder import _invalidate_topic_lecture_audio_cache

    mock_session = MagicMock()
    _invalidate_topic_lecture_audio_cache(mock_session, "course-1")

    mock_session.execute.assert_called_once()
    call = mock_session.execute.call_args
    assert "DELETE FROM topic_lecture_audio_cache" in str(call.args[0])
    assert call.args[1] == {"course_id": "course-1"}
    # commit はしない。呼び出し元 (build_course_content -> _save_course) の
    # コミットに相乗りする既存パターンに従う。
    mock_session.commit.assert_not_called()


def test_build_course_content_deletes_topic_lecture_audio_cache_after_regenerating_drafts():
    """build_course_content 経由でもトピック音声キャッシュが無効化されることを確認する

    （individual トピック編集の test_lecture_topic_audio.py::
    TestTopicAudioInvalidatedOnEdit と対になる回帰テスト）。
    """
    from core.course_content_builder import build_course_content

    course_data = {
        "sources": [{"material_id": "m1"}],
        "topics": [{"id": "t1", "title": "Topic 1"}],
    }

    def _execute_side_effect(query, params=None):
        sql = str(query)
        result = MagicMock()
        if "SELECT data, user_id" in sql:
            result.fetchone.return_value = (course_data, "user-1")
        elif "document_id::text" in sql:
            result.fetchall.return_value = [("doc-1",)]
        elif "chunk_index ASC" in sql:
            result.fetchall.return_value = []
        elif "FROM document_figures" in sql:
            result.fetchall.return_value = []
        return result

    mock_session = MagicMock()
    mock_session.execute.side_effect = _execute_side_effect

    fake_artifacts = {
        "doc-1": {
            "stage_outputs": {
                "_artifacts": {
                    "course_mapping": {"topics": [{"title": "Topic 1", "description": "desc"}]},
                },
            },
        },
    }

    with patch("core.course_content_builder._pg_session", return_value=mock_session), \
         patch("core.document_pipeline.persistence.resolve_artifact_runs", return_value=fake_artifacts), \
         patch("core.course_content_builder.generate_text_with_structured_output") as mock_generate:
        mock_generate.return_value = {
            "key_concepts": ["k"],
            "student_material": {"source_format": "eg-markdown-v1", "source_text": "text"},
            "spoken_script": "script",
            "cautions": [],
            "check_questions": [],
        }

        result = build_course_content("user-1", "course-1")

    assert result["status"] == "completed"

    execute_calls = mock_session.execute.call_args_list
    delete_indices = [
        i for i, c in enumerate(execute_calls)
        if "DELETE FROM topic_lecture_audio_cache" in str(c.args[0])
    ]
    assert len(delete_indices) == 1
    assert execute_calls[delete_indices[0]].args[1] == {"course_id": "course-1"}

    # DELETE はトピック draft 再生成の後、コース保存 (UPDATE ... title = :title)
    # より前に実行される（_save_course と同一トランザクションでまとめて確定するため）。
    save_indices = [i for i, c in enumerate(execute_calls) if "title = :title" in str(c.args[0])]
    assert save_indices and delete_indices[0] < save_indices[0]


# ---------------------------------------------------------------------------
# Phase 4 §7.1 (hierarchical_context_explanation_design.md):
# 図のコース流通 — evidence 供給（非LLM・決定論）
# ---------------------------------------------------------------------------


def test_collect_structured_content_builds_figure_claim_links():
    """figure_table_semantics artifact の FigureRecord.linked_claim_ids から
    claim_id → 図 の逆引き索引 (figure_claim_links) を構築すること。

    図⇄claim リンクの正本は FigureRecord.linked_claim_ids の一箇所
    （figure_concept_linking_design）で、claim 側には figure_ids が populate
    されないため、ここで明示的に逆方向へたどる。
    """
    from core.course_content_builder import _collect_structured_content

    artifacts_by_doc = {
        "doc-1": {
            "figure_table_semantics": {
                "figures": [
                    {
                        "figure_id": "fig_2",
                        "caption": "Figure 2. Apparatus overview.",
                        "linked_claim_ids": ["clm_1", "clm_2"],
                    },
                    {
                        "figure_id": "fig_5",
                        "caption": "Figure 5. Unrelated figure.",
                        "linked_claim_ids": [],
                    },
                ],
            },
        },
    }

    bundle = _collect_structured_content(artifacts_by_doc)
    links = bundle["figure_claim_links"]

    assert set(links.keys()) == {"clm_1", "clm_2"}
    assert links["clm_1"] == [
        {"document_id": "doc-1", "figure_key": "fig_2", "caption": "Figure 2. Apparatus overview."}
    ]
    assert links["clm_2"] == links["clm_1"]


def test_load_document_figures_index_builds_dual_key_lookup():
    """document_figures を figure_id (UUID) キーと document_id::figure_key キーの
    両方で索引し、component 経由（figure_id 既知）と claim 逆引き経由
    （document_id+figure_key しか分からない）の両方の解決経路を賄うこと。"""
    from core.course_content_builder import _load_document_figures_index

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [
        ("fig-uuid-1", "doc-1", "fig_2", "Figure 2. Apparatus overview."),
    ]

    index = _load_document_figures_index(mock_session, ["doc-1"])

    assert index["fig-uuid-1"]["figure_key"] == "fig_2"
    assert index["fig-uuid-1"]["document_id"] == "doc-1"
    assert index["fig-uuid-1"]["caption"] == "Figure 2. Apparatus overview."
    assert index["doc-1::fig_2"] is index["fig-uuid-1"]

    call = mock_session.execute.call_args
    assert "FROM document_figures" in str(call.args[0])
    assert call.args[1] == {"did_0": "doc-1"}


def test_load_document_figures_index_empty_document_ids_skips_query():
    from core.course_content_builder import _load_document_figures_index

    mock_session = MagicMock()
    assert _load_document_figures_index(mock_session, []) == {}
    mock_session.execute.assert_not_called()


def test_resolve_figure_ref_prefers_figure_id_then_falls_back_to_composite_key():
    from core.course_content_builder import _resolve_figure_ref

    entry = {"figure_id": "fig-uuid-1", "figure_key": "fig_2", "document_id": "doc-1", "caption": "c"}
    index = {"fig-uuid-1": entry, "doc-1::fig_2": entry}

    assert _resolve_figure_ref(index, figure_id="fig-uuid-1") is entry
    assert _resolve_figure_ref(index, document_id="doc-1", figure_key="fig_2") is entry
    # figure_id が索引に無ければ document_id+figure_key へフォールバックする。
    assert _resolve_figure_ref(index, figure_id="missing", document_id="doc-1", figure_key="fig_2") is entry
    # どちらの経路でも解決できなければ None（id を発明しない）。
    assert _resolve_figure_ref(index, figure_id="missing", document_id="doc-2", figure_key="fig_2") is None
    assert _resolve_figure_ref(index) is None


def test_topic_evidence_links_surfaces_figure_via_component_source_scope():
    """経路1: 装置候補コンポーネントの source_scope.figure_id/figure_key から
    図を根拠化する（apparatus_components.py が付与する source_scope）。"""
    from core.course_content_builder import _topic_evidence_links

    components = [
        {
            "component_id": "comp_apparatus_1",
            "document_id": "doc-1",
            "summary": "測定装置の構成コンポーネント",
            "source_scope": {
                "source": "apparatus_semantics",
                "document_id": "doc-1",
                "figure_id": "fig-uuid-1",
                "figure_key": "fig_2",
            },
        },
    ]
    figures_index = {
        "fig-uuid-1": {
            "figure_id": "fig-uuid-1",
            "figure_key": "fig_2",
            "document_id": "doc-1",
            "caption": "Figure 2. Schematic of the detector setup.",
        },
    }

    links = _topic_evidence_links(components, [], {}, {}, "high", figures_index=figures_index)
    figure_links = [l for l in links if l["kind"] == "figure"]

    assert len(figure_links) == 1
    fig_link = figure_links[0]
    assert fig_link["target_id"] == "fig-uuid-1"
    assert fig_link["figure_id"] == "fig-uuid-1"
    assert fig_link["figure_key"] == "fig_2"
    assert fig_link["document_id"] == "doc-1"
    assert fig_link["caption"] == "Figure 2. Schematic of the detector setup."


def test_topic_evidence_links_surfaces_figure_via_claim_reverse_lookup():
    """経路2: component の linked_claim_ids → FigureRecord.linked_claim_ids の
    逆引き (figure_claim_links) を辿って、claim を支持する図を根拠化する。"""
    from core.course_content_builder import _topic_evidence_links

    components = [
        {
            "component_id": "comp_1",
            "document_id": "doc-1",
            "summary": "測定結果を説明するコンポーネント",
            "linked_claim_ids": ["clm_1"],
        },
    ]
    claims = {"clm_1": {"claim_id": "clm_1", "normalized_text": "測定値は理論予測と一致する"}}
    figure_claim_links = {
        "clm_1": [{"document_id": "doc-1", "figure_key": "fig_3", "caption": "Figure 3. Result comparison."}],
    }
    figures_index = {
        "doc-1::fig_3": {
            "figure_id": "fig-uuid-3",
            "figure_key": "fig_3",
            "document_id": "doc-1",
            "caption": "Figure 3. Result comparison.",
        },
        "fig-uuid-3": {
            "figure_id": "fig-uuid-3",
            "figure_key": "fig_3",
            "document_id": "doc-1",
            "caption": "Figure 3. Result comparison.",
        },
    }

    links = _topic_evidence_links(
        components, [], claims, {}, "high",
        figures_index=figures_index,
        figure_claim_links=figure_claim_links,
    )
    figure_links = [l for l in links if l["kind"] == "figure"]

    assert len(figure_links) == 1
    assert figure_links[0]["target_id"] == "fig-uuid-3"
    assert figure_links[0]["figure_key"] == "fig_3"
    # claim 自体も従来どおり根拠化される（figure 追加が既存 kind を壊さない）。
    assert ("claim", "clm_1") in {(l["kind"], l["target_id"]) for l in links}


def test_topic_evidence_links_figure_skipped_when_unresolvable():
    """figures_index で解決できない figure_id/figure_key は根拠化しない
    （document_figures.id を発明しない。P4 の正直な縮退）。"""
    from core.course_content_builder import _topic_evidence_links

    components = [
        {
            "component_id": "comp_apparatus_1",
            "document_id": "doc-1",
            "linked_claim_ids": ["clm_1"],
            "source_scope": {"figure_id": "fig-uuid-unknown", "figure_key": "fig_9"},
        },
    ]
    claims = {"clm_1": {"claim_id": "clm_1", "normalized_text": "..."}}
    figure_claim_links = {
        "clm_1": [{"document_id": "doc-1", "figure_key": "fig_unresolved", "caption": "..."}],
    }

    links = _topic_evidence_links(
        components, [], claims, {}, "high",
        figures_index={},
        figure_claim_links=figure_claim_links,
    )

    assert not [l for l in links if l["kind"] == "figure"]


def test_topic_evidence_links_figure_deduplicates_across_routes():
    """同じ図が経路1（component）と経路2（claim 逆引き）の両方から到達しても
    根拠リンクは1件にまとまること。"""
    from core.course_content_builder import _topic_evidence_links

    components = [
        {
            "component_id": "comp_apparatus_1",
            "document_id": "doc-1",
            "linked_claim_ids": ["clm_1"],
            "source_scope": {"figure_id": "fig-uuid-1", "figure_key": "fig_2"},
        },
    ]
    claims = {"clm_1": {"claim_id": "clm_1", "normalized_text": "..."}}
    figure_claim_links = {
        "clm_1": [{"document_id": "doc-1", "figure_key": "fig_2", "caption": "Figure 2."}],
    }
    figures_index = {
        "fig-uuid-1": {
            "figure_id": "fig-uuid-1",
            "figure_key": "fig_2",
            "document_id": "doc-1",
            "caption": "Figure 2.",
        },
        "doc-1::fig_2": {
            "figure_id": "fig-uuid-1",
            "figure_key": "fig_2",
            "document_id": "doc-1",
            "caption": "Figure 2.",
        },
    }

    links = _topic_evidence_links(
        components, [], claims, {}, "high",
        figures_index=figures_index,
        figure_claim_links=figure_claim_links,
    )

    assert len([l for l in links if l["kind"] == "figure"]) == 1


def test_topic_evidence_for_prompt_includes_figure_reference():
    """kind='figure' の evidence_links も available_references に流れ、
    `![[figure:id]]` が発明でなく供給された id で埋め込めること。"""
    from core.course_content_builder import _topic_evidence_for_prompt

    topic = {
        "evidence_links": [
            {"kind": "component", "target_id": "comp_001", "summary": "..."},
            {
                "kind": "figure",
                "target_id": "fig-uuid-1",
                "summary": "Figure 2.",
                "figure_id": "fig-uuid-1",
                "figure_key": "fig_2",
                "document_id": "doc-1",
                "caption": "Figure 2. Apparatus overview.",
            },
        ],
    }

    refs = _topic_evidence_for_prompt(topic)["available_references"]
    assert {"kind": "figure", "id": "fig-uuid-1"} in refs


def test_course_content_draft_prompt_documents_figure_embed_syntax():
    """Phase 4 §7.1 の申し送り事項: `_COURSE_CONTENT_DRAFT_PROMPT` の埋め込み記法一覧に
    `![[figure:id]]` を明記し、供給された figure の id のみ使用可能であることを明示する
    （evidence には figure が既に流れているため、LLM へ記法自体を教える）。"""
    from core.course_content_builder import _COURSE_CONTENT_DRAFT_PROMPT

    assert "![[figure:id]]" in _COURSE_CONTENT_DRAFT_PROMPT
    # available_references にある figure の id のみ使用可、という制約が明文化されている
    figure_lines = [
        line for line in _COURSE_CONTENT_DRAFT_PROMPT.splitlines()
        if "![[figure:id]]" in line
    ]
    assert any("available_references" in line for line in figure_lines)


# ---------------------------------------------------------------------------
# バグB回帰: FigureRecord.figure_id（ピリオド保持, 例 'fig_3.3'）と
# document_figures.figure_key（アンダースコア正規化, 例 'fig_3_3'）の表記ゆれを
# normalize_figure_join_key で突合すること。
# ---------------------------------------------------------------------------


def test_resolve_figure_ref_normalizes_period_vs_underscore_figure_key():
    """_resolve_figure_ref は figure_key のピリオド⇄アンダースコア表記ゆれを
    normalize_figure_join_key で吸収してから索引を引くこと（バグB本体の修正）。"""
    from core.course_content_builder import _resolve_figure_ref

    entry = {"figure_id": "fig-uuid-33", "figure_key": "fig_3_3", "document_id": "doc-1", "caption": "c"}
    index = {"fig-uuid-33": entry, "doc-1::fig_3_3": entry}

    # figure_key 側がピリオド保持表記（FigureRecord.figure_id そのまま）でも一致する。
    assert _resolve_figure_ref(index, document_id="doc-1", figure_key="fig_3.3") is entry
    # 索引側と同じアンダースコア表記でも従来どおり一致する。
    assert _resolve_figure_ref(index, document_id="doc-1", figure_key="fig_3_3") is entry
    # 正規化後も一致しなければ None のまま（id を発明しない）。
    assert _resolve_figure_ref(index, document_id="doc-1", figure_key="fig_9.9") is None


def test_load_document_figures_index_normalizes_figure_key_for_composite_key():
    """document_figures.figure_key は _normalize_figure_key により既に正規化済みの
    はずだが、索引構築側でも normalize_figure_join_key を通して冪等に保つこと。"""
    from core.course_content_builder import _load_document_figures_index

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [
        ("fig-uuid-33", "doc-1", "fig_3.3", "Figure 3.3. Calibration setup."),
    ]

    index = _load_document_figures_index(mock_session, ["doc-1"])

    assert index["doc-1::fig_3_3"] is index["fig-uuid-33"]
    # ピリオド保持のままの複合キーは登録されない（正規化後の表記だけを持つ）。
    assert "doc-1::fig_3.3" not in index


def test_topic_evidence_links_resolves_figure_via_claim_reverse_lookup_with_period_label():
    """バグB回帰（本丸）: figure_table_semantics の FigureRecord.figure_id が
    章番号付きラベル 'fig_3.3'（ピリオド保持）で、document_figures.figure_key が
    'fig_3_3'（アンダースコア正規化）の組でも、経路2（claim 逆引き）が
    kind='figure' の evidence link を生成すること。修正前は素朴な文字列 join
    のため100%解決に失敗していた。"""
    from core.course_content_builder import _collect_structured_content, _topic_evidence_links

    artifacts_by_doc = {
        "doc-1": {
            "figure_table_semantics": {
                "figures": [
                    {
                        "figure_id": "fig_3.3",
                        "caption": "Figure 3.3. Calibration setup.",
                        "linked_claim_ids": ["clm_1"],
                    },
                ],
            },
        },
    }
    bundle = _collect_structured_content(artifacts_by_doc)

    components = [
        {
            "component_id": "comp_1",
            "document_id": "doc-1",
            "linked_claim_ids": ["clm_1"],
        },
    ]
    claims = {"clm_1": {"claim_id": "clm_1", "normalized_text": "測定値は較正済みである"}}
    # _load_document_figures_index が実際に生成する形の索引（figure_key は既に
    # 正規化済み表記 'fig_3_3' で複合キーが作られる）。
    figures_index = {
        "fig-uuid-33": {
            "figure_id": "fig-uuid-33",
            "figure_key": "fig_3_3",
            "document_id": "doc-1",
            "caption": "Figure 3.3. Calibration setup.",
        },
        "doc-1::fig_3_3": {
            "figure_id": "fig-uuid-33",
            "figure_key": "fig_3_3",
            "document_id": "doc-1",
            "caption": "Figure 3.3. Calibration setup.",
        },
    }

    links = _topic_evidence_links(
        components, [], claims, {}, "high",
        figures_index=figures_index,
        figure_claim_links=bundle["figure_claim_links"],
    )
    figure_links = [link for link in links if link["kind"] == "figure"]

    assert len(figure_links) == 1
    assert figure_links[0]["target_id"] == "fig-uuid-33"
    assert figure_links[0]["figure_key"] == "fig_3_3"


# ---------------------------------------------------------------------------
# 図の決定論注入（数式の _ensure_required_equations_in_material と同格,
# hierarchical_context_explanation_design.md Phase 4 §7.2）
# ---------------------------------------------------------------------------


def test_ensure_required_figures_appends_missing_embed():
    """evidence_links に kind='figure' があり本文に embed が無ければ末尾に追記される。"""
    from core.course_content_builder import _ensure_required_figures_in_material

    result = {
        "student_material": {"source_format": "eg-markdown-v1", "source_text": "導入の説明文"},
        "spoken_script": "読み上げ原稿はそのまま",
    }
    topic = {
        "evidence_links": [
            {"kind": "figure", "target_id": "fig-uuid-1", "caption": "Figure 1. Apparatus overview."},
        ],
    }

    _ensure_required_figures_in_material(result, topic)
    source_text = result["student_material"]["source_text"]

    assert source_text.startswith("導入の説明文")
    assert "![[figure:fig-uuid-1]]" in source_text
    # spoken_script は変更しない(v1 設計: 図は読み上げない)。
    assert result["spoken_script"] == "読み上げ原稿はそのまま"


def test_ensure_required_figures_does_not_duplicate_existing_embed():
    """既に LLM が書いた embed は重複させない。"""
    from core.course_content_builder import _ensure_required_figures_in_material

    source_text = "導入\n![[figure:fig-uuid-1]]\nつづき"
    result = {"student_material": {"source_format": "eg-markdown-v1", "source_text": source_text}}
    topic = {
        "evidence_links": [
            {"kind": "figure", "target_id": "fig-uuid-1", "caption": "Figure 1."},
        ],
    }

    _ensure_required_figures_in_material(result, topic)

    assert result["student_material"]["source_text"].count("![[figure:fig-uuid-1]]") == 1


def test_ensure_required_figures_noop_without_figure_evidence():
    """図 evidence が無ければ本文は不変(equation 等の他 kind は無視される)。"""
    from core.course_content_builder import _ensure_required_figures_in_material

    result = {"student_material": {"source_format": "eg-markdown-v1", "source_text": "本文のみ"}}
    topic = {"evidence_links": [{"kind": "equation", "target_id": "eq_1"}]}

    _ensure_required_figures_in_material(result, topic)

    assert result["student_material"]["source_text"] == "本文のみ"


def test_ensure_required_figures_deduplicates_repeated_evidence_links():
    """同じ figure が evidence_links に複数回現れても embed は1回だけ追加する。"""
    from core.course_content_builder import _ensure_required_figures_in_material

    result = {"student_material": {"source_format": "eg-markdown-v1", "source_text": ""}}
    topic = {
        "evidence_links": [
            {"kind": "figure", "target_id": "fig-uuid-1", "caption": "Figure 1."},
            {"kind": "figure", "target_id": "fig-uuid-1", "caption": "Figure 1."},
            {"kind": "figure", "target_id": "fig-uuid-2", "caption": "Figure 2."},
        ],
    }

    _ensure_required_figures_in_material(result, topic)
    source_text = result["student_material"]["source_text"]

    assert source_text.count("![[figure:fig-uuid-1]]") == 1
    assert source_text.count("![[figure:fig-uuid-2]]") == 1


def test_generate_single_topic_draft_wires_figure_injection_after_equations():
    """_generate_single_topic_draft は equations と同格で figures も注入する
    （LLM が空応答/未embed でも、根拠にある図は決定論側で必ず本文に出る）。"""
    from core.course_content_builder import _generate_single_topic_draft

    topic = {
        "id": "t1",
        "title": "セクション1",
        "evidence_links": [
            {"kind": "figure", "target_id": "fig-uuid-1", "caption": "Figure 1. Apparatus overview."},
        ],
    }

    with patch(
        "core.course_content_builder.generate_text_with_structured_output",
        return_value={
            "key_concepts": ["概念A"],
            "student_material": {"source_format": "eg-markdown-v1", "source_text": "LLM本文"},
            "spoken_script": "LLM読み上げ",
            "cautions": [],
            "check_questions": [],
        },
    ):
        result = _generate_single_topic_draft(
            course_context={},
            topics=[topic],
            topic=topic,
            index=0,
            model="gpt-4o",
            reasoning_effort=None,
        )

    assert "![[figure:fig-uuid-1]]" in result["student_material"]["source_text"]
    assert result["spoken_script"] == "LLM読み上げ"
