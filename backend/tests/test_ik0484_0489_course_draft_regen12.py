"""授業用ドラフトの材料の是正（IK-0484〜IK-0489。第 13 波 a の再生成プロンプト 20 本の残り）。

- IK-0484 前の生成が水増しした要件（全問の末尾に同じ重要概念。言い回しが変わった重要概念）を
  「現在の下書き」と保存する確認問題の両方から外す（答えに根ざす要件は残す）
- IK-0485 本文中の値の条件（``z < 2`` / ``wa = 0``）と、原文が切れ端で LaTeX に穴のある復元式を
  式の一覧から外す。原文そのままの LaTeX には復元の注記を付けず、原文と違う LaTeX には
  項目自身に ``reconstructed`` を付ける
- IK-0486 本体の空の「重要な数式」の行・本体の長い式の空の text・本体の無い式の参照
- IK-0487 原文抜粋・原文の証拠を表・凡例・図の説明の続き・数式の断片・内部参照から始めず、
  同じ本文の原文参照を1つにし、予算で短くしても同じ文にしない
- IK-0488 「現在の下書き」から、いまの参照一覧に無い埋め込みを外す（上限では埋め込んだ主張を優先）
- IK-0489 予算の間引きで grounding_facts / reconstructed_equation_ids / linked_* を落とさない
"""

from __future__ import annotations

import json

import pytest

from core import course_content_builder as ccb
from core import label_vocab


# ---------------------------------------------------------------------------
# IK-0484
# ---------------------------------------------------------------------------


def _padded_questions() -> list[dict]:
    pad = "M_PISN（対不安定性に関係する質量スケール）"
    return [
        {"question": "境界の例を2つ挙げてください。",
         "model_answer": r"境目（$M_{\rm PISN}$ や $q = 0.75$）は前提である。",
         "answer_requirements": ["境界が前提であること", pad]},
        {"question": "最小遅延時間はどう変わりますか。", "model_answer": "約 1 Gyr 大きくなる。",
         "answer_requirements": ["約 1 Gyr 大きくなること", pad]},
        {"question": "d ≳ 7 は何を意味しますか。", "model_answer": "最小遅延時間付近に集中する。",
         "answer_requirements": ["最小遅延時間付近への集中", pad]},
    ]


def test_requirement_repeated_across_questions_is_padding_unless_grounded():
    out = ccb.strip_padded_requirements(_padded_questions(), [])
    # 1問目は模範解答に M_PISN があるので残す（答えに根ざす）
    assert out[0]["answer_requirements"] == ["境界が前提であること", "M_PISN（対不安定性に関係する質量スケール）"]
    assert out[1]["answer_requirements"] == ["約 1 Gyr 大きくなること"]
    assert out[2]["answer_requirements"] == ["最小遅延時間付近への集中"]


def test_requirement_matching_reworded_key_concept_head_is_padding():
    questions = [{"question": "2つのネットワークの入出力は？", "model_answer": "EOS ネットワークは密度を入力に圧力を返す。",
                  "answer_requirements": ["EOS ネットワーク: 密度→圧力",
                                          "二重ネットワーク（星の構造を出すネットワークと EOS 側のネットワーク）"]}]
    out = ccb.strip_padded_requirements(questions, ["二重ネットワーク（EOS ネットワークと構造ネットワーク）"])
    assert out[0]["answer_requirements"] == ["EOS ネットワーク: 密度→圧力"]


def test_existing_draft_drops_padding_from_previous_key_concepts():
    topic = {
        "draft_source": "course_content_generation",
        "key_concepts": ["M_PISN（対不安定型超新星の質量閾値）"],
        "check_questions": _padded_questions(),
    }
    draft = ccb._topic_existing_draft(topic)
    assert [q["answer_requirements"] for q in draft["check_questions"]][1:] == [
        ["約 1 Gyr 大きくなること"], ["最小遅延時間付近への集中"],
    ]


def test_generated_check_questions_do_not_keep_copied_padding():
    result = {"check_questions": _padded_questions()}
    ccb._ensure_check_question_details(result, {"key_concepts": []})
    assert result["check_questions"][1]["answer_requirements"] == ["約 1 Gyr 大きくなること"]


def test_distinct_requirements_are_kept():
    questions = [{"question": "q", "model_answer": "a", "answer_requirements": ["頭部で弓状に湾曲", "尾で背骨に沿う"]}]
    assert ccb.strip_padded_requirements(questions, ["フィラメント"])[0]["answer_requirements"] == [
        "頭部で弓状に湾曲", "尾で背骨に沿う",
    ]


# ---------------------------------------------------------------------------
# IK-0485
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["z < 2", "wa = 0", "z ≈0", "s = 1."])
def test_short_value_conditions_are_excluded(raw):
    assert ccb.is_junk_equation_candidate({"equation_id": "eq_eqcand_inline_x", "raw_text": raw}) == "axis_ticks"


@pytest.mark.parametrize("raw", ["J=3–2", "w(a) = w0", "Pclump = PB", "m1 > MPISN", "w0"])
def test_symbolic_relations_are_kept(raw):
    assert ccb.is_junk_equation_candidate({"equation_id": "eq_eqcand_inline_x", "raw_text": raw}) is None


def test_fragment_with_unknown_holes_is_excluded():
    record = {"equation_id": "eq_blk_003_0057", "raw_text": "d\n({ΘGW})) ∝", "reconstructed": True,
              "latex": r"p(t_d) \propto \text{[unknown function of }t_d\text{]}"}
    assert ccb.is_junk_equation_candidate(record) == "split_fragment"
    # 穴があっても原文が式として読める長さなら残す（注記で知らせる — IK-0460）
    whole = {**record, "raw_text": "p(td | ΘGW) ∝ f(td, ΘGW) for tmin < td"}
    assert ccb.is_junk_equation_candidate(whole) is None


def test_latex_identical_to_raw_is_not_marked_reconstruction_beyond_raw():
    item = {"equation_id": "eq_n", "reconstructed": True, "raw_text": "N = 15", "latex": "N = 15"}
    assert ccb.reconstruction_beyond_raw(item) is False
    topic = {"content_blocks": [{"type": "equations", "items": [item]}],
             "evidence_links": [{"kind": "equation", "target_id": "eq_n"}]}
    evidence = ccb._topic_evidence_for_prompt(topic)
    ref = evidence["available_references"][0]
    assert "reconstruction_note" not in ref and "reconstructed" not in ref
    assert "reconstructed_equation_ids" not in evidence or evidence["reconstructed_equation_ids"] == []


def test_reconstructed_latex_differing_from_raw_is_flagged_on_the_reference():
    item = {"equation_id": "eq_7", "reconstructed": True, "raw_text": "M max = 2.062 +0.065 -0.086 M ⊙ (7)",
            "latex": r"M_{\max} = 2.062^{+0.065}_{-0.086}\, M_{\odot}"}
    topic = {"content_blocks": [{"type": "equations", "items": [item]}],
             "evidence_links": [{"kind": "equation", "target_id": "eq_7"}]}
    evidence = ccb._topic_evidence_for_prompt(topic)
    assert evidence["available_references"][0]["reconstructed"] is True
    assert evidence["reconstructed_equation_ids"] == ["eq_7"]


def test_raw_only_item_with_reconstruction_status_is_not_listed_as_reconstructed():
    item = {"equation_id": "eq_27", "reconstructed": True, "raw_text": "PB + Pturb = PTe + Prad, (27)"}
    topic = {"content_blocks": [{"type": "equations", "items": [item]}],
             "evidence_links": [{"kind": "equation", "target_id": "eq_27"}]}
    assert ccb.topic_reconstructed_equation_ids(topic) == []


# ---------------------------------------------------------------------------
# IK-0486
# ---------------------------------------------------------------------------


def test_compose_content_writes_raw_body_and_skips_bodyless_rows():
    content = ccb._compose_topic_content(
        "概要文", [], [],
        [{"equation_id": "eq_27", "label": "27", "raw_text": "PB + Pturb = PTe,\n(27)"},
         {"equation_id": "eq_x", "label": "30"}],
        [],
    )
    assert "- 27: PB + Pturb = PTe, (27)" in content
    assert "- 30" not in content


def test_compose_content_omits_heading_when_no_equation_has_a_body():
    content = ccb._compose_topic_content("概要文", [], [], [{"equation_id": "eq_x", "label": "30"}], [])
    assert "重要な数式" not in content


def test_prompt_content_fills_or_drops_empty_equation_rows():
    topic = {
        "content": "概要\nx\n\n重要な数式\n- 27: \n- 28: ",
        "content_blocks": [{"type": "equations", "items": [
            {"equation_id": "eq_27", "label": "27", "raw_text": "PB + Pturb = PTe + Prad,\n(27)"},
        ]}],
        "evidence_links": [{"kind": "equation", "target_id": "eq_27"}],
    }
    content = ccb._topic_evidence_for_prompt(topic)["content"]
    assert "- 27: PB + Pturb = PTe + Prad, (27)" in content
    assert "- 28:" not in content


def test_long_equation_reference_gets_plain_text_instead_of_empty():
    latex = r"L_{\mathrm{TOV}} = \frac{1}{N_c}\sum_{i=1}^{N_c}" + r"\left\| x \right\|^2" * 12
    topic = {"content_blocks": [{"type": "equations", "items": [
        {"equation_id": "eq_4", "latex": latex, "plain_text": "L_TOV = (1/N_c) sum ||dN/dr - f||^2"},
    ]}], "evidence_links": [{"kind": "equation", "target_id": "eq_4"}]}
    ref = ccb._topic_evidence_for_prompt(topic)["available_references"][0]
    assert ref["text"].startswith("L_TOV = (1/N_c)")


def test_equation_reference_without_any_body_is_not_offered():
    topic = {"evidence_links": [{"kind": "equation", "target_id": "eq_blk_003_0044"}]}
    refs = ccb._topic_evidence_for_prompt(topic)["available_references"]
    assert refs == []


# ---------------------------------------------------------------------------
# IK-0487
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Column Density (x 1022) Profile 1 Profile 2 Profile 3 Profile 4 Profile 5 Averaged Profile", "table_or_legend"),
        ("Intercore Separation (pc) 1 22:57:38.05 +62:35:43.83 0.041 0.031 7.0 ± 3.8 112.0 ± 7.0 125.29", "table_or_legend"),
        ("Same as Figure 2 but using equal length for all the B-field segments.", "figure_caption"),
    ],
)
def test_excerpt_rejects_tables_legends_and_caption_continuations(text, reason):
    assert ccb.source_excerpt_rejection_reason(text) == reason


def test_prose_with_numbers_is_not_a_table():
    text = "The field strength is 181 ± 9 μG, and the ratio of turbulent to ordered components is 0.33 ± 0.01 in the cloud."
    assert ccb.source_excerpt_rejection_reason(text) is None


def test_excerpt_does_not_start_mid_equation_or_carry_placeholders():
    chunks = [
        {"text": "4𝜋𝜌𝜎𝑣 𝜎𝜃 [[FORMULA_0]] ≈9.3 [[FORMULA_1]] where Q=0.5 is suggested by Ostriker et al. (2001) "
                 "in the case of small dispersion. The resulting field is strong."},
    ]
    excerpt = ccb.topic_source_excerpt(chunks)
    assert excerpt.startswith("The resulting field is strong")
    chunks = [{"text": "1. Sources above the mass gap. • [[eq_eqcand_inline_blk_004_0083_2_217317c8]] or "
                       "[[FORMULA_3]] denote the groups."}]
    excerpt = ccb.topic_source_excerpt(chunks)
    assert "[[" not in excerpt and "（数式）" in excerpt


def test_excerpt_prefers_chunk_without_placeholder_head():
    chunks = [
        {"text": "B. Modelling We assume [[FORMULA_0]] for the fluid."},
        {"text": "The dark energy fluid has a sound speed that we vary."},
    ]
    assert ccb.topic_source_excerpt(chunks).startswith("The dark energy fluid")


def test_section_heading_start_is_kept():
    assert ccb.clean_excerpt_text("1. INTRODUCTION The redshift distribution").startswith("1. INTRODUCTION")


def test_source_evidence_text_is_cleaned_and_deduplicated():
    same = "Four-phase training.-Naive joint optimization is unstable: neither network provides a gradient."
    topic = {
        "source_excerpt": same,
        "evidence_links": [
            {"kind": "source", "target_id": "ev_0042", "summary": same},
            {"kind": "source", "target_id": "ev_0043", "summary": same},
            {"kind": "source", "target_id": "ev_0010", "summary": "et al. 2025a; L. S. Collaboration & V. Kalogera 2023. We infer the BBH population."},
        ],
    }
    refs = [r for r in ccb._topic_evidence_for_prompt(topic)["available_references"] if r["kind"] == "source"]
    assert [r["id"] for r in refs] == ["ev_0042", "ev_0010"]
    assert refs[1]["text"].startswith("We infer")


def test_embedded_duplicate_source_is_the_one_kept():
    same = "The field bends near the head of the filament."
    topic = {
        "source_excerpt": same,
        "evidence_links": [{"kind": "source", "target_id": "ev_1", "summary": same}],
        "student_material": {"source_text": "本文\n![[source:excerpt]]"},
    }
    refs = [r for r in ccb._topic_evidence_for_prompt(topic)["available_references"] if r["kind"] == "source"]
    assert [r["id"] for r in refs] == ["excerpt"]


def test_budget_shortening_keeps_source_texts_distinct():
    base = "Four-phase training.-Naive joint optimization is unstable because neither network provides "
    topic = {"evidence_links": [
        {"kind": "source", "target_id": "ev_a", "summary": base + "a meaningful gradient signal at the start."},
        {"kind": "source", "target_id": "ev_b", "summary": base + "stable targets until the structure network is pretrained."},
    ] + [{"kind": "component", "target_id": f"comp_{i}", "label": "x" * 110} for i in range(30)],
        "summary": "y" * 3000}
    payload = json.loads(ccb._evidence_prompt_json(topic, 5000))
    texts = [r["text"] for r in payload["available_references"] if r["kind"] == "source"]
    assert len(texts) == 2 and texts[0] != texts[1]


# ---------------------------------------------------------------------------
# IK-0488
# ---------------------------------------------------------------------------


def test_stale_embeds_are_stripped_from_current_draft():
    topic = {
        "draft_source": "course_content_generation",
        "evidence_links": [{"kind": "claim", "target_id": "claim_a", "summary": "Three groups are found."}],
        "student_material": {"source_text": (
            "導入。\n\n![[source:topic_summary]]\n\n前半 ![[claim:claim_gone]] 後半。\n\n![[claim:claim_a]]"
        )},
    }
    text = ccb._topic_existing_draft(topic)["student_material"]["source_text"]
    assert "topic_summary" not in text and "claim_gone" not in text
    assert "![[claim:claim_a]]" in text
    assert "前半" in text and "後半。" in text


def test_claim_cap_keeps_claims_embedded_in_draft():
    links = [{"kind": "claim", "target_id": f"c{i}", "summary": f"Distinct claim number {i} about the field."}
             for i in range(ccb.CLAIM_REFERENCE_LIMIT + 3)]
    last = f"c{ccb.CLAIM_REFERENCE_LIMIT + 2}"
    topic = {"evidence_links": links, "student_material": {"source_text": f"![[claim:{last}]]"}}
    refs = [r["id"] for r in ccb._topic_evidence_for_prompt(topic)["available_references"] if r["kind"] == "claim"]
    assert last in refs and len(refs) == ccb.CLAIM_REFERENCE_LIMIT
    draft = ccb._topic_existing_draft(topic)
    assert f"![[claim:{last}]]" in draft["student_material"]["source_text"]


# ---------------------------------------------------------------------------
# IK-0489
# ---------------------------------------------------------------------------


def test_budget_keeps_grounding_facts_and_linked_ids():
    items = [{"equation_id": f"eq_{i}", "latex": f"x_{i} = y_{i} + z_{i}" + " + w" * 40,
              "semantic_kind": "k" * 300} for i in range(12)]
    items[0].update({"reconstructed": True, "raw_text": "x0 =", "latex": r"x_0 = \int f"})
    topic = {
        "summary": "s" * 1500,
        "content": "c" * 1500,
        "source_excerpt": "The text of the excerpt. " * 40,
        "content_blocks": [{"type": "equations", "items": items},
                           {"type": "components", "items": [{"component_id": f"comp_{i}", "label": "l" * 200,
                                                             "summary": "m" * 400} for i in range(8)]}],
        "evidence_links": [{"kind": "equation", "target_id": f"eq_{i}"} for i in range(12)]
        + [{"kind": "claim", "target_id": f"c{i}", "summary": f"claim {i} " + "z" * 150} for i in range(6)],
        "linked_equation_ids": [f"eq_{i}" for i in range(12)],
        "linked_claim_ids": [f"c{i}" for i in range(6)],
        "linked_component_ids": [f"comp_{i}" for i in range(8)],
        "teaching_takeaways": ["t" * 200] * 6,
    }
    payload = json.loads(ccb._evidence_prompt_json(topic, 8000))
    assert payload.get(ccb.PROMPT_JSON_OMITTED_KEY)
    for key in ("grounding_facts", "reconstructed_equation_ids", "linked_equation_ids",
                "linked_claim_ids", "linked_component_ids"):
        assert payload.get(key), key
    assert payload["reconstructed_equation_ids"] == ["eq_0"]
    assert label_vocab.GROUNDING_KIND_SUPPLIED_FACT.format(kind="主張") in payload["grounding_facts"]
