"""授業用ドラフトの材料の是正（IK-0459〜IK-0465。砂場コース 1bad7d9f の再生成プロンプト 20 本）。

- IK-0459 式として読めない候補（arXiv の見出し行・軸ラベル・天体名・空の式・本文の文・
  置換文字・割れた断片）を、参照一覧・付録・要件・「重要な数式」の行から外し、理由を残す
- IK-0460 原文より先まで復元した LaTeX に原文と注記を並べ、復元式を配るトピックには
  必ず注意点を付ける（部品の注意書きが無くても）
- IK-0461 根拠の有無の注記は、いまの根拠候補から決める（食い違う注記を持ち越さない）
- IK-0462 確認問題の要件をトピックの重要概念で埋めない
- IK-0463 主張の本文を 55 字で切らない（件数で予算を守る・同じ本文は重複除去）
- IK-0464 原文抜粋を所属・キャプション・語の途中から始めない
- IK-0465 要旨の block を共有する章立ての単位が同じ主張の並びにならない
"""

from __future__ import annotations

import json

import pytest

from core import course_content_builder as ccb
from core import label_vocab
from core.knowledge_objects import learning_units as ko_learning_units


# ---------------------------------------------------------------------------
# IK-0459
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        ({"equation_id": "eq_blk_001_0012", "raw_text": "arXiv:2606.02318v1  [astro-ph.HE]  1 Jun 2026"},
         "document_header"),
        ({"equation_id": "eq_blk_002_0014", "raw_text": "50\n100\nm1 [M⊙]"}, "axis_label"),
        ({"equation_id": "eq_O7", "raw_text": "HD 217086 (O7)"}, "no_relation"),
        ({"equation_id": "eq_30", "raw_text": ",\n(30)"}, "empty_body"),
        ({"equation_id": "eq_blk_003_0102", "raw_text": "�\nαK + 3"}, "replacement_char"),
        ({"equation_id": "eq_blk_005_0198",
          "raw_text": "s = 2 and λMG = 3, µ starts deviating from unity around z = 20."}, "prose"),
        ({"equation_id": "eq_blk_009_0319", "raw_text": "22h58m00s\n57m40s\n20s\n00s"}, "axis_ticks"),
        ({"equation_id": "eq_blk_008_0337", "raw_text": "s = 1."}, "axis_ticks"),
    ],
)
def test_junk_equation_candidates_are_named(record, reason):
    assert ccb.is_junk_equation_candidate(record) == reason
    assert reason in ccb.JUNK_EQUATION_REASONS
    assert reason in label_vocab.JUNK_EQUATION_REASON_LABELS


@pytest.mark.parametrize(
    "record",
    [
        {"equation_id": "eq_27", "raw_text": "𝑃B + 𝑃turb + 𝑃Tg = 𝑃Te + 𝑃rad,\n(27)"},
        {"equation_id": "eq_eqcand_inline_blk_1_2_ab", "raw_text": "J=3–2"},
        {"equation_id": "eq_eqcand_inline_blk_1_3_cd", "raw_text": "w0"},
        {"equation_id": "eq_6", "raw_text": ",\n(6)", "latex": r"S = \int d^4x\,\sqrt{-g}\,\mathcal{L}"},
        {"equation_id": "eq_n", "latex": "N = 15"},
        {"equation_id": "eq_q", "raw_text": "3.1. High-Z SFR: Z > 0.1 Z⊙", "latex": r"Z > 0.1\,Z_\odot"},
    ],
)
def test_real_equations_and_inline_candidates_are_kept(record):
    assert ccb.is_junk_equation_candidate(record) is None


def test_equation_split_over_consecutive_blocks_is_one_fragment_run():
    records = [
        {"equation_id": "eq_blk_003_0100", "raw_text": "dαB\nd ln a = c2"},
        {"equation_id": "eq_blk_003_0102", "raw_text": "�\nαK + 3"},
        {"equation_id": "eq_blk_003_0103", "raw_text": "2α2\nB"},
        {"equation_id": "eq_blk_003_0104", "raw_text": "�\n+ α2"},
        {"equation_id": "eq_blk_003_0105", "raw_text": "B\n2 −αB"},
        {"equation_id": "eq_blk_003_0073", "raw_text": "L2 = G2,\n(7a)", "latex": r"\mathcal{L}_2 = G_2"},
    ]
    reasons = ccb.junk_equation_candidates(records)
    assert set(reasons) == {
        "eq_blk_003_0100", "eq_blk_003_0102", "eq_blk_003_0103", "eq_blk_003_0104", "eq_blk_003_0105",
    }
    assert reasons["eq_blk_003_0100"] == "split_fragment"


def _junk_topic() -> dict:
    items = [
        {"equation_id": "eq_blk_001_0012", "raw_text": "arXiv:2606.02318v1  [astro-ph.HE]  1 Jun 2026"},
        {"equation_id": "eq_blk_002_0014", "raw_text": "50\n100\nm1 [M⊙]"},
        {"equation_id": "eq_eqcand_inline_blk_001_0011_281_89ec", "latex": r"t \ge t_{\min}",
         "raw_text": "t ≥tmin"},
    ]
    return {
        "id": "t5",
        "content": "概要\nx\n\n重要な数式\n- eq_blk_001_0012: \n- eq_blk_002_0014: \n- eq_eqcand_inline_blk_x_1: ",
        "content_blocks": [{"type": "equations", "items": items}],
        "linked_equation_ids": [i["equation_id"] for i in items],
        "evidence_links": [
            {"kind": "equation", "target_id": i["equation_id"]} for i in items
        ] + [{"kind": "equation", "target_id": "eq_blk_004_0001",
              "plain_text": "HD 217086 (O7)"}],
    }


def test_junk_equations_leave_prompt_but_are_noted():
    topic = _junk_topic()
    evidence = ccb._topic_evidence_for_prompt(topic)
    ids = {r["id"] for r in evidence["available_references"] if r["kind"] == "equation"}
    assert ids == {"eq_eqcand_inline_blk_001_0011_281_89ec"}
    assert evidence["linked_equation_ids"] == ["eq_eqcand_inline_blk_001_0011_281_89ec"]
    assert evidence["excluded_equations_note"].startswith(label_vocab.EXCLUDED_EQUATION_CANDIDATES_NOTE)
    assert "論文の見出し行" in evidence["excluded_equations_note"]
    # 「重要な数式」の行は外した式も、内部 ID だけで本体の無い行も残さない
    assert "eq_blk_001_0012" not in evidence["content"]
    assert "eqcand" not in evidence["content"]
    assert "重要な数式" not in evidence["content"]


def test_junk_equations_are_not_appended_as_key_equations():
    topic = _junk_topic()
    result = {"student_material": {"source_text": "本文"}}
    ccb._ensure_required_equations_in_material(result, topic)
    text = result["student_material"]["source_text"]
    assert "eq_blk_001_0012" not in text and "eq_blk_002_0014" not in text


def test_excluded_candidates_are_recorded_with_reasons():
    record = ccb.excluded_equation_candidates_record([_junk_topic()])
    assert {"topic_id": "t5", "equation_id": "eq_blk_001_0012", "reason": "document_header"} in record
    assert {r["equation_id"] for r in record} >= {"eq_blk_002_0014", "eq_blk_004_0001"}


# ---------------------------------------------------------------------------
# IK-0460
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"reconstructed": True, "raw_text": "Mmax = 2", "latex": r"M_{\max} = 2.06^{+0.07}_{-0.09}"}, True),
        ({"reconstructed": True, "raw_text": ",\n(6)", "latex": r"S = \int d^4x\,\sqrt{-g}"}, True),
        ({"reconstructed": True, "raw_text": "S =", "latex": r"S = \int d^4x"}, True),
        ({"reconstructed": True, "raw_text": "d\n({ΘGW})) ∝",
          "latex": r"p(t_d) \propto [unknown function of t_d]"}, True),
        ({"reconstructed": True, "raw_text": "R 1.4 = 12.85 +0.03 -0.06 km, (5)",
          "latex": r"R_{1.4} = 12.85^{+0.03}_{-0.06}\,\mathrm{km}"}, False),
        ({"raw_text": "Mmax = 2", "latex": r"M_{\max} = 2.06"}, False),
    ],
)
def test_reconstruction_beyond_raw(item, expected):
    assert ccb.reconstruction_beyond_raw(item) is expected


def _reconstructed_topic() -> dict:
    return {
        "content_blocks": [{"type": "equations", "items": [
            {"equation_id": "eq_mmax", "reconstructed": True, "raw_text": "Mmax = 2",
             "latex": r"M_{\max} = 2.06^{+0.07}_{-0.09}"},
        ]}],
        "evidence_links": [{"kind": "equation", "target_id": "eq_mmax"}],
        "linked_equation_ids": ["eq_mmax"],
    }


def test_reconstructed_equation_is_presented_with_raw_text_and_note():
    evidence = ccb._topic_evidence_for_prompt(_reconstructed_topic())
    ref = evidence["available_references"][0]
    assert ref["raw_text"] == "Mmax = 2"
    assert ref["reconstruction_note"] == label_vocab.RECONSTRUCTED_BEYOND_RAW_NOTE
    item = evidence["content_blocks"][0]["items"][0]
    assert item["reconstruction_note"] == label_vocab.RECONSTRUCTED_BEYOND_RAW_NOTE
    assert evidence["reconstructed_equation_ids"] == ["eq_mmax"]


def test_reconstruction_caution_fires_without_component_cautions():
    result = {"cautions": ["別の注意"]}
    ccb._ensure_reconstruction_caution(result, _reconstructed_topic())
    assert label_vocab.RECONSTRUCTED_TOPIC_CAUTION in result["cautions"]


def test_reconstruction_caution_not_added_when_only_junk_is_reconstructed():
    topic = {"content_blocks": [{"type": "equations", "items": [
        {"equation_id": "eq_blk_001_0012", "reconstructed": True,
         "raw_text": "arXiv:2606.02318v1 [astro-ph.HE] 1 Jun 2026"},
    ]}]}
    result = {"cautions": []}
    ccb._ensure_reconstruction_caution(result, topic)
    assert result["cautions"] == []


def test_prompt_components_do_not_carry_document_uuid():
    topic = {"content_blocks": [{"type": "components", "items": [
        {"component_id": "comp_001", "label": "DTD basis", "document_id": "7954db7d-528d-488a-8fa2-6e6697ba70e4"},
    ]}]}
    evidence = ccb._topic_evidence_for_prompt(topic)
    assert "7954db7d" not in json.dumps(evidence, ensure_ascii=False)


# ---------------------------------------------------------------------------
# IK-0461
# ---------------------------------------------------------------------------


def _grounded_topic() -> dict:
    return {
        "draft_source": "course_content_generation",
        "evidence_links": [
            {"kind": "claim", "target_id": "claim_a", "summary": "Three sub-populations are found."},
            {"kind": "figure", "target_id": "fig-1", "caption": "Figure 6."},
        ],
        "student_material": {"source_text": (
            "本文。\n\n> 注: このトピックの根拠は要約文だけです。図・式・主張は紐づいておらず、値は根拠に含まれていません。"
        )},
    }


def test_grounding_facts_follow_current_references():
    facts = ccb._topic_evidence_for_prompt(_grounded_topic())["grounding_facts"]
    assert label_vocab.GROUNDING_KIND_SUPPLIED_FACT.format(kind="主張") in facts
    assert label_vocab.GROUNDING_KIND_SUPPLIED_FACT.format(kind="図") in facts
    assert label_vocab.GROUNDING_KIND_NOT_SUPPLIED_FACT.format(kind="式") in facts


def test_stale_grounding_note_is_not_carried_into_current_draft():
    draft = ccb._topic_existing_draft(_grounded_topic())
    assert "紐づいておらず" not in draft["student_material"]["source_text"]
    assert "本文。" in draft["student_material"]["source_text"]


def test_generated_note_contradicting_references_is_stripped():
    topic = _grounded_topic()
    result = {
        "student_material": {"source_text": "本文。\n> 注: 図・式・主張は紐づいておらず。\n> 注: 数式は根拠候補に含まれていません。"},
        "cautions": ["主張はこのトピックの根拠に含まれていない。", "境界の選び方に注意。"],
    }
    ccb._strip_contradicted_draft_notes(result, topic)
    text = result["student_material"]["source_text"]
    assert "紐づいておらず" not in text
    # 式は本当に供給されていないので、その注記は残す
    assert "数式は根拠候補に含まれていません" in text
    assert result["cautions"] == ["境界の選び方に注意。"]


# ---------------------------------------------------------------------------
# IK-0462
# ---------------------------------------------------------------------------


def test_requirements_are_not_padded_with_key_concepts():
    topic = {"key_concepts": ["連星ブラックホール（BBH）", "形成チャネル"],
             "learning_objectives": ["DTD を説明できる"]}
    item = {"question": "DTD とは何の分布ですか。", "answer_requirements": ["形成から合体までの時間"]}
    ccb._fill_check_question_detail(item, topic)
    assert item["answer_requirements"] == ["形成から合体までの時間"]


def test_padded_requirements_in_generated_draft_are_removed():
    topic = {
        "draft_source": "course_content_generation",
        "key_concepts": ["連星ブラックホール（BBH）"],
        "check_questions": [{"question": "q", "answer_requirements": ["形成から合体までの時間", "連星ブラックホール（BBH）"]}],
    }
    draft = ccb._topic_existing_draft(topic)
    assert draft["check_questions"][0]["answer_requirements"] == ["形成から合体までの時間"]


# ---------------------------------------------------------------------------
# IK-0463
# ---------------------------------------------------------------------------


def test_claim_text_is_not_cut_to_sibling_identical_prefix():
    base = "The delay-time distribution characterizes the time delay between "
    topic = {"evidence_links": [
        {"kind": "claim", "target_id": "c1", "summary": base + "formation and merger of binaries."},
        {"kind": "claim", "target_id": "c2", "summary": base + "the star formation and the merger."},
    ]}
    refs = ccb._topic_evidence_for_prompt(topic)["available_references"]
    texts = [r["text"] for r in refs if r["kind"] == "claim"]
    assert len(texts) == 2 and texts[0] != texts[1]
    assert all("…" not in t for t in texts)


def test_duplicate_claim_text_keeps_the_longer():
    topic = {"evidence_links": [
        {"kind": "claim", "target_id": "c1", "summary": "The field is ordered."},
        {"kind": "claim", "target_id": "c2", "summary": "The field is ordered"},
        {"kind": "claim", "target_id": "c3", "summary": "The field is ordered near the head."},
    ]}
    refs = [r for r in ccb._topic_evidence_for_prompt(topic)["available_references"] if r["kind"] == "claim"]
    assert [r["id"] for r in refs] == ["c3"]


def test_claim_count_is_capped_and_noted():
    topic = {"evidence_links": [
        {"kind": "claim", "target_id": f"c{i}", "summary": f"Distinct claim number {i} about the field."}
        for i in range(ccb.CLAIM_REFERENCE_LIMIT + 3)
    ]}
    evidence = ccb._topic_evidence_for_prompt(topic)
    claims = [r for r in evidence["available_references"] if r["kind"] == "claim"]
    assert len(claims) == ccb.CLAIM_REFERENCE_LIMIT
    assert evidence["omitted_claims_note"] == ccb.OMITTED_CLAIMS_NOTE


def test_budget_drops_claims_instead_of_cutting_their_text():
    long = "x" * 200
    topic = {"evidence_links": [
        {"kind": "claim", "target_id": f"c{i}", "summary": f"claim {i} {long}"} for i in range(10)
    ]}
    text = ccb._evidence_prompt_json(topic, 2500)
    payload = json.loads(text)
    claims = [r for r in payload["available_references"] if r["kind"] == "claim"]
    assert claims and all(len(r["text"]) > 150 for r in claims)
    assert payload.get("omitted_claims_note") == ccb.OMITTED_CLAIMS_NOTE


# ---------------------------------------------------------------------------
# IK-0464
# ---------------------------------------------------------------------------


def test_excerpt_skips_affiliation_and_caption_chunks():
    chunks = [
        {"text": "GWTC-4 of LIGO Shaunak P 1 1Department of Astronomy, Tata Institute of Fundamental Research"},
        {"text": "mJy/beam Figure 3. Same as Figure 2 but using equal length."},
        {"text": "resentative seed. The radius is measured as 12.85 km."},
    ]
    assert ccb.topic_source_excerpt(chunks).startswith("The radius is measured")


def test_excerpt_trims_header_to_abstract():
    chunks = [{"text": "Title A. Author 1 1Department of Physics, University of X ABSTRACT We present a study."}]
    assert ccb.topic_source_excerpt(chunks).startswith("ABSTRACT We present")


def test_excerpt_prefers_chunk_of_first_block_ref():
    chunks = [
        {"text": "ABSTRACT We present the abstract.", "block_ids": ["blk_001_0005"], "document_id": "d"},
        {"text": "The field bends near the head.", "block_ids": ["blk_006_0150"], "document_id": "d"},
    ]
    excerpt = ccb.topic_source_excerpt(chunks, [("d", "blk_006_0150"), ("d", "blk_001_0005")])
    assert excerpt.startswith("The field bends")


# ---------------------------------------------------------------------------
# IK-0465
# ---------------------------------------------------------------------------


class _Scope:
    def __init__(self, claims, evidence):
        self._claims = claims
        self._evidence = evidence

    def iter_kind(self, kind, doc):
        return list((self._claims if kind == "claims" else {}).items())

    def get(self, kind, doc, key):
        table = {"claims": self._claims, "evidence": self._evidence}.get(kind, {})
        return table.get(key)


def test_section_units_sharing_the_abstract_block_get_their_own_claims_first():
    evidence = {f"ev_{b}_{i}": {"source": {"block_id": b}} for b in ("abs", "sec") for i in range(8)}
    claims = {}
    for b in ("abs", "sec"):
        for i in range(8):
            claims[f"claim_{b}_{i}"] = {"source_evidence_ids": [f"ev_{b}_{i}"]}
    unit = {"unit_kind": ko_learning_units.KIND_SECTION_BLOCK, "document_id": "d",
            "source_block_ids": ["abs", "sec"], "section_ids": []}
    other = {"unit_kind": ko_learning_units.KIND_SECTION_BLOCK, "document_id": "d",
             "source_block_ids": ["abs"], "section_ids": []}
    share = ccb.section_block_share({"u1": unit, "u2": other})
    assert share[("d", "abs")] == 2
    picked, _eqs, blocks = ccb._section_unit_refs([unit], _Scope(claims, evidence), share)
    assert [cid for _d, cid in picked] == [f"claim_sec_{i}" for i in range(8)]
    assert blocks[0] == ("d", "sec")
