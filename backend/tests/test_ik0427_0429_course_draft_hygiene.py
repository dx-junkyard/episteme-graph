"""IK-0427 / IK-0428 / IK-0429: 授業用ドラフト生成の材料と後処理の衛生。

観測（ペルソナ通し受講・第 9 周のコース内容の再生成）:

- IK-0427: プロンプトの「現在の下書き」末尾の「この節で使う数式 / この節で参照する図」に、
  いまの根拠候補では別の式を指す ``eq_3`` 等へ別論文の式の説明（暗黒エネルギー論文の圧力
  摂動・Cep B の磁場比）と連星ブラックホール論文の図が付いたまま渡っていた。付録は生成の
  たびにいまのトピックから付け直す部分なのに、前の生成（旧実装）の付録を持ち越していた。
- IK-0428: 確認問題の要件に、問いと無関係な「数式 eq_blk_003_0055 の意味または役割に
  触れる」が毎回足され（内部 ID）、テンプレートの問いの模範解答に英文の要約が入った。
- IK-0429: 図の座標軸の目盛り（``22h58m00s`` / ``62°42'00"``）・式の断片（``J=3``）・
  同じ式の重複が「重要な数式」として渡っていた。
"""
from __future__ import annotations

import json

import pytest

import core.course_content_builder as ccb
import tests.test_ik0377_course_content_document_scope as scope_fixtures


DOC_A = scope_fixtures.DOC_A
DOC_B = scope_fixtures.DOC_B
LATEX_A = scope_fixtures.LATEX_A
LATEX_B = scope_fixtures.LATEX_B


def _two_doc_artifacts_with_eq_3() -> dict:
    """両論文が ``eq_3`` を持つ（A = Cep B の偏光率 / B = 中性子星の損失関数）。"""
    raw = json.dumps(scope_fixtures._two_doc_artifacts())
    return json.loads(raw.replace('"eq_1"', '"eq_3"'))


def _doc_b_topic() -> dict:
    bundle = ccb._collect_structured_content(_two_doc_artifacts_with_eq_3())
    chunks = scope_fixtures._chunks()
    for rows in chunks.values():
        for row in rows:
            row["formulas"] = [{**f, "id": "eq_3"} for f in row["formulas"]]
    return ccb._enrich_topics(
        [{"id": "t-eos", "title": "中性子星の状態方程式"}], bundle, chunks, scope_fixtures._figures_index()
    )[0]


STALE_APPENDIX = (
    "## 本文\n\n状態方程式の逆問題を扱う。\n\n"
    "### この節で使う数式\n"
    "- 3: delta P = (P'/rho') delta rho + delta P_s\n"
    "![[equation:eq_3]]\n\n"
    "### この節で参照する図\n"
    "- Figure 2. Comparison of the evolution of the normalized SFR\n"
    "![[figure:edf93c98-27ad-434b-ba77-8bc85aa270ab]]"
)


# ---------------------------------------------------------------------------
# IK-0427
# ---------------------------------------------------------------------------


def test_doc_b_topic_appendix_uses_doc_b_equation_even_when_doc_a_shares_eq_3():
    topic = _doc_b_topic()
    items = ccb._required_equation_items(topic)
    assert [item["equation_id"] for item in items] == ["eq_3"]
    assert items[0]["latex"] == LATEX_B

    result = {"student_material": {"source_text": "本文"}}
    ccb._ensure_required_equations_in_material(result, topic)
    text = result["student_material"]["source_text"]
    assert "![[equation:eq_3]]" in text
    assert "Cep B" not in text and LATEX_A not in text


def test_existing_draft_for_prompt_drops_stale_appendix_and_legacy_requirements():
    topic = _doc_b_topic()
    topic["student_material"] = {"source_format": "eg-markdown-v1", "source_text": STALE_APPENDIX}
    topic["check_questions"] = [{
        "question": "逆問題とは何か",
        "model_answer": "観測から EOS を推定する",
        "answer_requirements": ["EOS を推定する", "数式 3 の意味または役割に触れる",
                                "数式 eq_blk_003_0055 の意味または役割に触れる"],
        "explanation": "",
    }]

    draft = ccb._topic_existing_draft(topic)

    assert draft["student_material"]["source_text"] == "## 本文\n\n状態方程式の逆問題を扱う。"
    assert draft["check_questions"][0]["answer_requirements"] == ["EOS を推定する"]
    # 保存しているトピック自体は書き換えない（プロンプト用の写しだけ）。
    assert topic["student_material"]["source_text"] == STALE_APPENDIX
    dumped = ccb._prompt_json(draft, 6000)
    assert "delta P_s" not in dumped and "edf93c98" not in dumped


def test_generated_draft_does_not_carry_over_stale_appendix(monkeypatch):
    """モデルが前の付録をそのまま写しても、いまのトピックの付録に付け直される。"""
    topic = _doc_b_topic()
    topic["linked_figure_ids"] = []
    topic["evidence_links"] = [link for link in topic.get("evidence_links") or [] if link.get("kind") != "figure"]

    monkeypatch.setattr(ccb, "structured_call", lambda *a, **k: {
        "key_concepts": ["状態方程式"],
        "student_material": {"source_text": STALE_APPENDIX},
        "check_questions": [{"question": "逆問題とは何か", "model_answer": "観測から推定する"}],
    })

    result = ccb._generate_single_topic_draft(
        course_context={}, topics=[topic], topic=topic, index=0, reasoning_effort=None
    )
    text = result["student_material"]["source_text"]

    assert "delta P_s" not in text  # 別論文の式の説明を持ち越さない
    assert "edf93c98" not in text  # 別論文の図を持ち越さない
    assert "Figure 2." not in text
    assert text.count(ccb.GENERATED_EQUATIONS_HEADING) == 1
    assert "![[equation:eq_3]]" in text
    assert text.startswith("## 本文\n\n状態方程式の逆問題を扱う。")


def test_appendix_regeneration_is_idempotent():
    topic = _doc_b_topic()
    result = {"student_material": {"source_text": "本文"}}
    ccb._ensure_required_equations_in_material(result, topic)
    ccb._ensure_required_figures_in_material(result, topic)
    once = result["student_material"]["source_text"]
    ccb._ensure_required_equations_in_material(result, topic)
    ccb._ensure_required_figures_in_material(result, topic)
    assert result["student_material"]["source_text"] == once


def test_model_written_section_with_prose_is_not_stripped():
    text = "本文\n\n### この節で使う数式\n損失関数は次の形をとる。\n![[equation:eq_3]]"
    assert ccb.strip_generated_reference_appendix(text) == text


# ---------------------------------------------------------------------------
# IK-0428
# ---------------------------------------------------------------------------


def _topic_with_equations(*items: dict, summary: str = "") -> dict:
    return {
        "summary": summary,
        "content_blocks": [{"type": "equations", "items": list(items)}],
        "linked_equation_ids": [item["equation_id"] for item in items],
    }


def test_unrelated_question_gets_no_equation_requirement_and_no_internal_id():
    topic = _topic_with_equations(
        {"equation_id": "eq_blk_003_0055", "label": None, "latex": r"p_t(t_d)"},
        {"equation_id": "eq_blk_003_0056", "label": None, "latex": r"t_d^{\min}"},
    )
    item = {"question": "なぜ星形成率が必要なのですか。", "answer_requirements": ["DTD との組み合わせ"]}

    ccb._fill_check_question_detail(item, topic)

    assert item["answer_requirements"] == ["DTD との組み合わせ"]
    assert not any("eq_" in req for req in item["answer_requirements"])


def test_question_citing_printed_number_gets_printed_label_requirement():
    topic = _topic_with_equations({"equation_id": "eq_12", "label": "12", "latex": r"R_{1.4} = 12.85"})
    item = {"question": "式 (12) が示す半径の値の意味を説明してください。"}

    ccb._fill_check_question_detail(item, topic)

    assert "式 (12) の意味または役割に触れる" in item["answer_requirements"]
    assert not any("eq_12" in req for req in item["answer_requirements"])


def test_question_quoting_equation_body_without_number_uses_generic_requirement():
    topic = _topic_with_equations({"equation_id": "eq_blk_1", "label": None, "latex": r"p(t) \propto t^{-d}"})
    item = {"question": "p(t) \\propto t^{-d} の d は何を決めますか。"}

    ccb._fill_check_question_detail(item, topic)

    assert ccb.QUESTION_EQUATION_REQUIREMENT in item["answer_requirements"]


def test_requirements_and_model_answer_never_carry_internal_ids():
    topic = _topic_with_equations({"equation_id": "eq_3", "label": "3", "latex": r"L = x"})
    item = {
        "question": "損失の構成を説明してください。",
        "model_answer": "comp_004 の説明のとおり、損失は ![[equation:eq_3]] で与えられる。",
        "answer_requirements": ["comp_004 に触れる", "数式 eq_3 の意味または役割に触れる", "重み付き和"],
    }

    ccb._fill_check_question_detail(item, topic)

    assert item["answer_requirements"] == ["重み付き和"]
    assert "comp_004" not in item["model_answer"] and "![[" not in item["model_answer"]
    assert "損失は" in item["model_answer"]


def test_english_summary_is_not_used_as_model_answer():
    topic = {"summary": "Corrects population inference for detection selection effects using GWTC-4 injections."}
    item = {"question": "学習損失と4段階の交互最適化で扱った中心概念と数式の役割を説明してください。"}

    ccb._fill_check_question_detail(item, topic)

    assert "Corrects" not in item["model_answer"]
    assert item["model_answer"]


# ---------------------------------------------------------------------------
# IK-0429
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["22h58m00s\n57m40s\n20s\n00s", "62°42'00\"", "40'00\"", "J=3", "s = 1", "20"])
def test_axis_ticks_and_short_fragments_are_unpresentable(text):
    assert ccb.is_unpresentable_equation_text(text)


@pytest.mark.parametrize("text", ["N = 15", r"M_{\max} = 2.062", r"p(t) \propto t^{-d}", ""])
def test_real_equations_are_presentable(text):
    assert not ccb.is_unpresentable_equation_text(text)


def _noisy_topic() -> dict:
    items = [
        {"equation_id": "eq_tick_1", "raw_text": "22h58m00s\n57m40s\n20s\n00s"},
        {"equation_id": "eq_tick_2", "raw_text": "62°42'00\""},
        {"equation_id": "eq_frag", "raw_text": "J=3"},
        {"equation_id": "eq_action_1", "latex": r"S = \int d^4x\,\sqrt{-g}\left(\sum_{i=2}^{5}\mathcal{L}_i\right)"},
        {"equation_id": "eq_action_2", "latex": r"S = \int d^4x\,\sqrt{-g}\,\bigg(\sum_{i=2}^{5} \mathcal{L}_i\bigg)"},
        {"equation_id": "eq_n_1", "latex": "N = 15", "plain_text": "N = 15"},
        {"equation_id": "eq_n_2", "latex": "N = 15", "plain_text": "N equals 15"},
    ]
    return {
        "content": "概要\nx\n\n重要な数式\n" + "\n".join(f"- {i['equation_id']}: " for i in items),
        "content_blocks": [{"type": "equations", "items": items}],
        "linked_equation_ids": [i["equation_id"] for i in items],
        "evidence_links": [{"kind": "equation", "target_id": i["equation_id"]} for i in items],
    }


def test_prompt_drops_ticks_fragments_and_duplicates_but_keeps_stored_blocks():
    topic = _noisy_topic()
    stored_before = json.dumps(topic["content_blocks"], sort_keys=True)

    payload = ccb._topic_evidence_for_prompt(topic)

    kept = ["eq_action_1", "eq_n_1"]
    assert [r["id"] for r in payload["available_references"] if r["kind"] == "equation"] == kept
    assert [i["equation_id"] for i in payload["content_blocks"][0]["items"]] == kept
    assert payload["linked_equation_ids"] == kept
    for dropped in ("eq_tick_1", "eq_tick_2", "eq_frag", "eq_action_2", "eq_n_2"):
        assert f"- {dropped}:" not in payload["content"]
    assert "- eq_n_1:" in payload["content"]
    # 保存する content_blocks（学習画面の位置引き）は変えない。
    assert json.dumps(topic["content_blocks"], sort_keys=True) == stored_before


def test_appendix_does_not_list_ticks_or_duplicates():
    topic = _noisy_topic()
    result = {"student_material": {"source_text": "本文"}}
    ccb._ensure_required_equations_in_material(result, topic)
    text = result["student_material"]["source_text"]
    assert "22h58m00s" not in text and "J=3" not in text
    assert "eq_action_2" not in text and "eq_n_2" not in text
    assert "![[equation:eq_action_1]]" in text
