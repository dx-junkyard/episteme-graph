"""IK-0438〜IK-0443: 授業用ドラフト生成プロンプトの根拠候補・下書き・コース概要の衛生。

観測（ペルソナ通し受講・第 9 周のコース内容の再生成。mailbox req 00229〜00248）:

- IK-0438: ``available_references`` が ID だけで、主張の本文・図のキャプション・原文の中身が
  読めない。原文抜粋に「FIG. 1.」があるのに図は供給されていない（t19）。
- IK-0439: 文字数予算の間引きが ``content_blocks`` 末尾の equations ブロックを丸ごと落とし、
  式が ID だけになる（t7 / t11）。
- IK-0440: 前の生成の注意点「このトピックには図・式・主張が紐づいていません」が、根拠の
  付いた再生成に持ち越される（t0 / t3 / t13 / t15）。builder の事実文も残る。
- IK-0441: 別トピックと同じ要約（t4 = t2）。
- IK-0442: 軸ラベルの掲載節（「B-field strength (mG)」）、参照一覧に無い依存先 ID（``comp_003__r1``）。
- IK-0443: 部品の注意書き「Residual equation is reconstructed.」が学習者向けの注意点に届かない。
"""
from __future__ import annotations

import copy
import json

import core.course_content_builder as ccb
import tests.test_ik0377_course_content_document_scope as scope_fixtures
from core import label_vocab

DOC_B = scope_fixtures.DOC_B


def _artifacts_with_component_details() -> dict:
    artifacts = scope_fixtures._two_doc_artifacts()
    doc_b = artifacts[DOC_B]
    comps = doc_b["component_assembly"]["components"]
    comps[0]["cautions"] = [
        {"text": "Residual equation is reconstructed.", "equation_ids": ["eq_1"]},
        {"text": "Constraint is soft, not exact.", "claim_ids": ["clm_1"]},
    ]
    comps[0]["assumptions"] = ["15 seeds aggregated"]
    comps[0]["dependencies"] = [
        {"dependency_type": "depends_on", "component_refs": ["comp_002", "comp_003__r1"],
         "reason": "Acts on structure-network outputs."},
    ]
    comps.append({
        "component_id": "comp_002",
        "label": "Structure network",
        "summary": "Maps density to pressure.",
    })
    eq = doc_b["equation_semantics"]["equations"][0]
    eq["reconstruction"] = {"status": "reconstructed", "latex": scope_fixtures.LATEX_B}
    doc_b["document_structure"]["sections"].append({"section_id": "sec_2", "title": "B-field strength (mG)"})
    doc_b["equation_semantics"]["equations"].append({
        "equation_id": "eq_axis",
        "label": "15",
        "source_extraction": {
            "latex": r"P_{Te} = 2 n_e k_B T_e",
            "source_location": {"section_id": "sec_2", "block_id": "b_0002"},
        },
    })
    comps[0]["linked_equation_ids"] = ["eq_1", "eq_axis"]
    return artifacts


def _doc_b_topic() -> dict:
    bundle = ccb._collect_structured_content(_artifacts_with_component_details())
    return ccb._enrich_topics(
        [{"id": "t-eos", "title": "中性子星の状態方程式"}],
        bundle, scope_fixtures._chunks(), scope_fixtures._figures_index(),
    )[0]


# ---------------------------------------------------------------------------
# IK-0438 参照一覧に中身の短い本文
# ---------------------------------------------------------------------------


def test_available_references_carry_short_text_per_kind():
    topic = {
        "evidence_links": [
            {"kind": "claim", "target_id": "claim_span_003_sub01", "summary": "磁場は頭部で弓状に曲がる。" * 20},
            {"kind": "figure", "target_id": "fig-1", "summary": "", "caption": "Figure 2. Mass-radius diagram."},
            {"kind": "source", "target_id": "ev_0125", "summary": "The hierarchical likelihood is defined."},
            {"kind": "equation", "target_id": "eq_short"},
            {"kind": "equation", "target_id": "eq_long"},
        ],
        "content_blocks": [{"type": "equations", "items": [
            {"equation_id": "eq_short", "latex": r"p(t) \propto t^{-d}"},
            {"equation_id": "eq_long", "latex": r"\int " + "x " * 150},
        ]}],
        "source_excerpt": "ABSTRACT We present a detailed study of the Cep B molecular cloud." * 5,
    }
    refs = {(r["kind"], r["id"]): r for r in ccb._topic_evidence_for_prompt(topic)["available_references"]}

    claim_text = refs[("claim", "claim_span_003_sub01")]["text"]
    # IK-0463: 主張の本文は兄弟を区別できる長さ（CLAIM_REFERENCE_TEXT_LIMIT）まで載せる。
    assert claim_text.startswith("磁場は頭部で") and len(claim_text) <= ccb.CLAIM_REFERENCE_TEXT_LIMIT + 1
    assert refs[("figure", "fig-1")]["text"] == "Figure 2. Mass-radius diagram."
    assert refs[("source", "ev_0125")]["text"] == "The hierarchical likelihood is defined."
    assert refs[("equation", "eq_short")]["text"] == r"p(t) \propto t^{-d}"
    # 200 字を超える式の本体は途中で切らない（載せない）
    assert "text" not in refs[("equation", "eq_long")]
    summary_text = refs[("source", "excerpt")]["text"]  # IK-0455: 配信と同じ id
    assert summary_text.startswith("ABSTRACT") and len(summary_text) <= ccb.REFERENCE_TEXT_LIMIT + 1


def test_figure_mentioned_in_excerpt_but_not_provided_gets_fact_note():
    topic = {"source_excerpt": "K-essence-like FIG. 1. Solutions for µ from Equation 9 ..."}
    evidence = ccb._topic_evidence_for_prompt(topic)
    assert evidence["figures_note"] == ccb.SOURCE_FIGURE_NOT_PROVIDED_NOTE

    topic["evidence_links"] = [{"kind": "figure", "target_id": "fig-1", "caption": "FIG. 1."}]
    assert "figures_note" not in json.loads(ccb._prompt_json(ccb._topic_evidence_for_prompt(topic), 8000))


def test_integrated_topic_references_have_claim_text():
    topic = _doc_b_topic()
    refs = {(r["kind"], r["id"]): r for r in ccb._topic_evidence_for_prompt(topic)["available_references"]}
    assert refs[("claim", "clm_1")]["text"] == "状態方程式は高密度で軟化する"


# ---------------------------------------------------------------------------
# IK-0439 予算の優先順位（式を ID だけにしない）
# ---------------------------------------------------------------------------


def _t7_like_topic() -> dict:
    equations = [
        {"equation_id": f"eq_blk_{i}", "latex": rf"q_{i} > \bar{{q}}_{i} + \sum_k w_k x_k^{{{i}}}",
         "semantic_kind": "Defines the split " * 6, "section_label": "Hierarchical Bayesian Analysis"}
        for i in range(6)
    ]
    components = [
        {"component_id": f"comp_{i}", "label": f"Component {i}", "summary": "Long summary " * 60,
         "teaching_takeaway": "Takeaway " * 40,
         "preconditions": [{"text": "Precondition text " * 10, "claim_ids": ["c"]} for _ in range(6)]}
        for i in range(3)
    ]
    return {
        "summary": "Infers BBH population hyperparameters from the event catalog.",
        "content": "概要\n" + "重複した本文 " * 400,
        "content_blocks": [
            {"type": "summary", "text": "Infers BBH population hyperparameters."},
            {"type": "components", "items": components},
            {"type": "equations", "items": equations},
        ],
        "source_excerpt": "GWTC-4 of LIGO-Virgo-KAGRA " * 60,
        "linked_equation_ids": [e["equation_id"] for e in equations],
        "linked_claim_ids": [f"claim_{i}" for i in range(20)],
        "teaching_takeaways": ["Takeaway " * 40] * 3,
        "evidence_links": (
            [{"kind": "claim", "target_id": f"claim_{i}", "summary": "The claim body sentence. " * 8}
             for i in range(20)]
            + [{"kind": "equation", "target_id": e["equation_id"]} for e in equations]
        ),
    }


def test_budget_keeps_all_linked_equations_with_bodies():
    topic = _t7_like_topic()
    text = ccb._evidence_prompt_json(topic, 8000)
    assert len(text) <= 8000
    parsed = json.loads(text)
    blocks = [b for b in parsed["content_blocks"] if b.get("type") == "equations"]
    assert blocks, "equations ブロックが丸ごと落ちている"
    kept = {item["equation_id"]: item for item in blocks[0]["items"]}
    assert set(kept) == {f"eq_blk_{i}" for i in range(6)}
    assert all(item.get("latex") for item in kept.values())
    # 重複する材料（content / teaching_takeaways）が式より先に落ちる
    assert "content" not in parsed and "teaching_takeaways" not in parsed
    # 参照一覧の式にも本体が載る（200 字以内）
    eq_refs = [r for r in parsed["available_references"] if r["kind"] == "equation"]
    assert eq_refs and all(r.get("text") for r in eq_refs)


def test_budget_drops_unreferenced_chunk_equations_before_linked_ones():
    topic = _t7_like_topic()
    extra = [{"equation_id": f"fallback_{i}", "latex": rf"y_{i} = " + "a + " * 30} for i in range(4)]
    topic["content_blocks"][2]["items"].extend(extra)
    parsed = json.loads(ccb._evidence_prompt_json(topic, 6000))
    blocks = [b for b in parsed.get("content_blocks", []) if b.get("type") == "equations"]
    ids = {item["equation_id"] for item in blocks[0]["items"]} if blocks else set()
    assert {f"eq_blk_{i}" for i in range(6)} <= ids
    assert not any(i.startswith("fallback_") for i in ids)


def test_draft_embedded_equation_is_priority():
    topic = {"student_material": {"source_text": "本文 ![[equation:eq_x]] ほか"}, "content_blocks": []}
    assert "eq_x" in ccb._priority_equation_ids(topic)


# ---------------------------------------------------------------------------
# IK-0440 前の生成の注記を持ち越さない
# ---------------------------------------------------------------------------


def test_existing_draft_drops_builder_notes_and_stale_generated_cautions():
    topic = _doc_b_topic()
    topic.update({
        "draft_source": "course_content_generation",
        "student_material": {"source_text": "## 本文\n\n説明。\n\n> 注: " + ccb.UNLINKED_TOPIC_GROUNDING_NOTE},
        "cautions": ["このトピックには図・式・主張が紐づいておらず、説明は要約文のみです。",
                     ccb.UNLINKED_TOPIC_GROUNDING_NOTE],
    })
    # 記録の無い旧い下書き（draft_reference_key なし）→ 生成された注意点は渡さない
    draft = ccb._topic_existing_draft(topic)
    assert draft["cautions"] == []
    assert ccb.UNLINKED_TOPIC_GROUNDING_NOTE not in draft["student_material"]["source_text"]
    assert draft["student_material"]["source_text"].startswith("## 本文")

    # 同じ根拠で作った下書き → モデルが書いた注意点は残し、builder の定数だけ外す
    topic["draft_reference_key"] = ccb.current_reference_key(topic)
    draft = ccb._topic_existing_draft(topic)
    assert draft["cautions"] == ["このトピックには図・式・主張が紐づいておらず、説明は要約文のみです。"]


def test_teacher_written_cautions_survive_changed_references():
    topic = {"draft_source": "", "cautions": ["教員の注意書き"], "evidence_links": [
        {"kind": "claim", "target_id": "c1", "summary": "x"}]}
    assert ccb._topic_existing_draft(topic)["cautions"] == ["教員の注意書き"]


def test_enrich_clears_stale_builder_grounding_note_when_topic_is_now_linked():
    bundle = ccb._collect_structured_content(_artifacts_with_component_details())
    raw = {
        "id": "t-eos", "title": "中性子星の状態方程式",
        "grounding_note": ccb.UNLINKED_TOPIC_GROUNDING_NOTE,
        "coverage": {"status": "missing", "message": ccb.UNLINKED_TOPIC_GROUNDING_NOTE},
    }
    topic = ccb._enrich_topics([raw], bundle, scope_fixtures._chunks(), scope_fixtures._figures_index())[0]
    assert topic["grounding_note"] == ""
    assert topic["coverage"] == {}
    # 教員の書いた文は触らない
    raw2 = dict(raw, grounding_note="教員のメモ")
    topic2 = ccb._enrich_topics([raw2], bundle, scope_fixtures._chunks(), scope_fixtures._figures_index())[0]
    assert topic2["grounding_note"] == "教員のメモ"


def test_generation_records_reference_key(monkeypatch):
    topic = _doc_b_topic()
    monkeypatch.setattr(ccb, "_generate_single_topic_draft", lambda **kw: {
        "key_concepts": ["a"], "student_material": {"source_text": "x"},
        "spoken_script": "", "cautions": [], "check_questions": [],
    })
    ccb._generate_course_topic_drafts({"title": "c"}, [topic])
    assert topic["draft_reference_key"] == ccb.current_reference_key(topic)
    assert "draft_reference_key" in ccb.BUILDER_OWNED_TOPIC_KEYS


# ---------------------------------------------------------------------------
# IK-0441 / IK-0442 コース概要・掲載節・依存先
# ---------------------------------------------------------------------------


def test_duplicate_summaries_are_flagged_and_passed_to_prompt():
    topics = [
        {"id": "t2", "title": "磁場強度の推定", "summary": "The estimate adopts Q = 0.5."},
        {"id": "t3", "title": "主結果", "summary": "Other."},
        {"id": "t4", "title": "確かめられていない点", "summary": "The estimate adopts  Q = 0.5."},
    ]
    ccb._flag_duplicate_topic_summaries(topics)
    assert topics[0]["coverage"]["duplicate_summary_of"] == ["t4"]
    assert topics[2]["coverage"]["duplicate_summary_of"] == ["t2"]
    assert topics[2]["coverage"]["message"] == label_vocab.DUPLICATE_TOPIC_SUMMARY_NOTE
    assert "coverage" not in topics[1]
    context = ccb._topic_context_for_prompt(topics[2], topics)
    assert context["summary_shared_with"] == ["磁場強度の推定"]
    assert "summary_shared_with" in ccb._COURSE_CONTENT_DRAFT_PROMPT


def test_axis_label_is_not_a_section_label():
    assert ccb.section_title_rejection_reason("B-field strength (mG)") == "axis_label"
    assert ccb.section_title_rejection_reason("Velocity (km/s)") == "axis_label"
    assert ccb.section_title_rejection_reason("Standard Model (SM)") is None
    assert ccb.section_title_rejection_reason("Intercore Separation") is None
    topic = _doc_b_topic()
    items = {i["equation_id"]: i for b in topic["content_blocks"] if b["type"] == "equations" for i in b["items"]}
    assert "section_label" not in items["eq_axis"]
    assert items["eq_1"]["section_label"] == scope_fixtures.SECTION_B


def test_dependency_targets_resolved_or_dropped_in_prompt():
    topic = _doc_b_topic()
    comp = next(b for b in topic["content_blocks"] if b["type"] == "components")["items"][0]
    dep = comp["dependencies"][0]
    assert dep["targets"] == ["comp_002", "comp_003__r1"]  # 保存形は変えない
    assert dep["target_labels"] == ["Structure network", ""]

    evidence = ccb._topic_evidence_for_prompt(topic)
    prompt_comp = next(b for b in evidence["content_blocks"] if b["type"] == "components")["items"][0]
    prompt_dep = prompt_comp["dependencies"][0]
    assert "comp_003__r1" not in json.dumps(evidence)
    assert prompt_dep.get("target_names") == ["Structure network"]
    assert prompt_dep["reason"] == "Acts on structure-network outputs."


# ---------------------------------------------------------------------------
# IK-0443 部品の注意書き・前提を学習者の注意点へ
# ---------------------------------------------------------------------------


def test_component_cautions_and_assumptions_listed_in_prompt():
    topic = _doc_b_topic()
    rows = ccb._topic_evidence_for_prompt(topic)["component_cautions"]
    texts = {r["text"]: r for r in rows}
    assert texts["Residual equation is reconstructed."]["reconstructed_equation"] is True
    assert "reconstructed_equation" not in texts["Constraint is soft, not exact."]
    assert texts["15 seeds aggregated"]["kind"] == "assumption"


def test_reconstruction_caution_appended_when_model_drops_it(monkeypatch):
    topic = _doc_b_topic()
    monkeypatch.setattr(ccb, "structured_call", lambda *a, **k: {
        "key_concepts": ["EOS"], "student_material": {"source_text": "本文"},
        "cautions": ["損失の重みは本文で確認する"], "check_questions": [],
    })
    result = ccb._generate_single_topic_draft(
        course_context={}, topics=[topic], topic=topic, index=0, reasoning_effort=None
    )
    assert label_vocab.RECONSTRUCTED_TOPIC_CAUTION in result["cautions"]

    monkeypatch.setattr(ccb, "structured_call", lambda *a, **k: {
        "key_concepts": ["EOS"], "student_material": {"source_text": "本文"},
        "cautions": ["残差の式は AI が復元した式です"], "check_questions": [],
    })
    result = ccb._generate_single_topic_draft(
        course_context={}, topics=[topic], topic=topic, index=0, reasoning_effort=None
    )
    assert label_vocab.RECONSTRUCTED_TOPIC_CAUTION not in result["cautions"]


def test_no_reconstruction_caution_without_reconstructed_equation(monkeypatch):
    artifacts = _artifacts_with_component_details()
    artifacts[DOC_B]["equation_semantics"]["equations"][0].pop("reconstruction")
    bundle = ccb._collect_structured_content(artifacts)
    topic = ccb._enrich_topics(
        [{"id": "t-eos", "title": "中性子星の状態方程式"}],
        bundle, scope_fixtures._chunks(), scope_fixtures._figures_index(),
    )[0]
    result = {"cautions": []}
    ccb._ensure_reconstruction_caution(result, copy.deepcopy(topic))
    assert result["cautions"] == []


def test_generated_notes_include_new_constants():
    assert label_vocab.RECONSTRUCTED_TOPIC_CAUTION in ccb.GENERATED_DRAFT_NOTES
    assert label_vocab.DUPLICATE_TOPIC_SUMMARY_NOTE in ccb.GENERATED_DRAFT_NOTES
    for note in ccb.GENERATED_DRAFT_NOTES:
        assert not any(ch.isdigit() for ch in note)
