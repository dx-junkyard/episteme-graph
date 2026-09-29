"""IK-0377: コース肉付けは論文内ローカル ID を必ず (document, ID) で解決する。

観測（ペルソナ通し受講）: 2本の論文（Cep B フィラメント / 中性子星の状態方程式）から
作ったコースで、状態方程式の章のトピックの授業用ドラフト作成プロンプトに Cep B の
``eq_1``〜``eq_7``（偏光率・ΔV_NT・N(H2)/W …）が Cep B の節見出し・原文抜粋付きで
並んだ。``equation_id`` / ``component_id`` / ``claim_id`` / ``evidence_id`` /
``block_id`` は document の中でしか一意でないのに、``_collect_structured_content`` が
素の ID で平たい索引へ集め、トピックの肉付けがそれを引いていたため。

ここで固定すること:
1. 両論文が ``eq_1`` / ``comp_001`` / ``clm_1`` / ``ev_1`` / ``b_0001`` を持っていても、
   document B に束ねたトピックは B の式本文・節見出し・主張・原文・出典チャンクだけを持つ
   （artifact の読み順 A→B / B→A のどちらでも）。
2. プロンプト材料（``_topic_evidence_for_prompt``）にも A の式が混ざらない。
3. document が決まらない単位のトピックには別論文の式を付けず、事実文を残す。
4. 出典チャンク・チャンク由来の式・図の逆引きも document で閉じる。
"""
from __future__ import annotations

import pytest

import core.course_content_builder as ccb
from core.course_data import UNIT_SOURCE_TEACHER_SELECTED


DOC_A = "11111111-1111-1111-1111-111111111111"  # Cep B フィラメント論文
DOC_B = "22222222-2222-2222-2222-222222222222"  # 中性子星 EOS 論文

LATEX_A = r"p = \frac{\sqrt{Q^2+U^2}}{I}"
LATEX_B = r"\mathcal{L} = \sum_i (R_i - R_{1.4})^2"
SECTION_A = "Magnetic Fields in the Cep B Filament"
SECTION_B = "Neutron Star Equation of State"


def _artifacts(*, doc: str, latex: str, section_title: str, claim_text: str,
               evidence_text: str, mapping_title: str, figure_claims=None) -> dict:
    artifacts = {
        "document_structure": {
            "sections": [{"section_id": "sec_1", "title": section_title}],
        },
        "course_mapping": {
            "topics": [{
                "title": mapping_title,
                "description": f"{mapping_title} の説明",
                "linked_component_ids": ["comp_001"],
            }],
        },
        "component_assembly": {
            "components": [{
                "component_id": "comp_001",
                "label": f"{mapping_title} の構成要素",
                "summary": f"{mapping_title} を支える要素",
                "linked_equation_ids": ["eq_1"],
                "linked_claim_ids": ["clm_1"],
                "linked_evidence_ids": ["ev_1"],
            }],
        },
        "equation_semantics": {
            "equations": [{
                "equation_id": "eq_1",
                "label": f"{doc[:4]} の式",
                "source_extraction": {
                    "latex": latex,
                    "source_location": {"section_id": "sec_1", "block_id": "b_0001"},
                },
            }],
        },
        "claim_object_builder": {
            "claims": [{
                "claim_id": "clm_1",
                "normalized_text": claim_text,
                "source_evidence_ids": ["ev_1"],
            }],
        },
        "evidence_registry": {
            "records": [{
                "evidence_id": "ev_1",
                "evidence_text": evidence_text,
                "evidence_role": "source_quote",
                "source": {"page": 1, "section_id": "sec_1", "block_id": "b_0001"},
            }],
        },
    }
    if figure_claims:
        artifacts["figure_table_semantics"] = {
            "figures": [{"figure_id": "fig_1", "caption": "Figure 1.", "linked_claim_ids": list(figure_claims)}],
        }
    return artifacts


def _two_doc_artifacts(order: tuple[str, str] = (DOC_A, DOC_B)) -> dict:
    by_doc = {
        DOC_A: _artifacts(
            doc=DOC_A, latex=LATEX_A, section_title=SECTION_A,
            claim_text="Cep B の磁場はフィラメントに垂直である",
            evidence_text="The polarization fraction decreases toward the filament.",
            mapping_title="Cep B の磁場構造",
            figure_claims=["clm_1"],
        ),
        DOC_B: _artifacts(
            doc=DOC_B, latex=LATEX_B, section_title=SECTION_B,
            claim_text="状態方程式は高密度で軟化する",
            evidence_text="The loss is minimized over the TOV residual.",
            mapping_title="中性子星の状態方程式",
        ),
    }
    return {doc: by_doc[doc] for doc in order}


def _chunks() -> dict[str, list[dict]]:
    def chunk(chunk_id, doc, material, text, formula_latex):
        return {
            "id": chunk_id,
            "material_id": material,
            "chunk_index": 0,
            "text": text,
            "formulas": [{"id": "eq_1", "latex": formula_latex}],
            "chapter": None,
            "section": None,
            "document_id": doc,
            "block_ids": ["b_0001"],
        }

    return {
        "m-a": [chunk("chunk-a", DOC_A, "m-a", "Cep B の本文段落", LATEX_A)],
        "m-b": [chunk("chunk-b", DOC_B, "m-b", "EOS 論文の本文段落", LATEX_B)],
    }


def _unit_row(doc: str, key: str = "k1:eos") -> dict:
    return {
        "unit_id": f"uuid-{key}",
        "document_id": doc,
        "stable_key": key,
        "unit_kind": "parent_component",
        "label": "状態方程式の単位",
        "summary": "",
        "linked_claim_ids": [],
        "linked_equation_ids": ["eq_1"],
        "linked_component_ids": ["db-uuid-ignored"],
        "linked_figure_ids": [],
        "agent_payload": {"linked_component_agent_ids": ["comp_001"]},
        "order_index": 0,
    }


def _unit_topic(key: str = "k1:eos") -> dict:
    return {
        "id": "t-eos",
        "title": "高密度物質",
        "units": [{
            "kind": "parent_component",
            "stable_key": key,
            "unit_id": f"uuid-{key}",
            "label": "状態方程式の単位",
            "source": UNIT_SOURCE_TEACHER_SELECTED,
        }],
    }


def _equation_items(topic: dict) -> list[dict]:
    for block in topic.get("content_blocks") or []:
        if block.get("type") == "equations":
            return list(block.get("items") or [])
    return []


def _links(topic: dict, kind: str) -> list[dict]:
    return [link for link in topic.get("evidence_links") or [] if link.get("kind") == kind]


def _assert_only_doc_b(topic: dict) -> None:
    items = _equation_items(topic)
    assert items, "doc B の式が付いていること"
    assert all(item.get("latex") == LATEX_B for item in items)
    assert all(item.get("latex") != LATEX_A for item in items)
    assert items[0].get("section_label") == SECTION_B

    eq_links = _links(topic, "equation")
    assert [link.get("latex") for link in eq_links] == [LATEX_B]
    assert eq_links[0].get("section_label") == SECTION_B

    assert [link.get("summary") for link in _links(topic, "claim")] == ["状態方程式は高密度で軟化する"]
    assert [link.get("summary") for link in _links(topic, "source")] == [
        "The loss is minimized over the TOV residual."
    ]
    # 出典チャンク・原文抜粋も B のもの（両論文が b_0001 を持つ）。
    assert topic["material_chunk_ids"] == ["chunk-b"]
    assert "EOS" in topic["source_excerpt"]
    # 図の逆引き（A の図は A の clm_1 を指す）が B の同名 claim に付かない。
    assert _links(topic, "figure") == []

    prompt = ccb._topic_evidence_for_prompt(topic)
    dumped = repr(prompt)
    assert LATEX_A.replace("\\", "\\\\") not in dumped and LATEX_A not in dumped
    assert SECTION_A not in dumped
    assert "Cep B" not in dumped


@pytest.mark.parametrize("order", [(DOC_A, DOC_B), (DOC_B, DOC_A)])
def test_unit_bound_topic_gets_its_own_documents_eq_1(order):
    bundle = ccb._collect_structured_content(_two_doc_artifacts(order))
    units = {"k1:eos": _unit_row(DOC_B)}

    topic = ccb._enrich_topics([_unit_topic()], bundle, _chunks(), None, units)[0]

    assert topic["linked_component_ids"] == ["comp_001"]
    assert topic["linked_equation_ids"] == ["eq_1"]
    _assert_only_doc_b(topic)


@pytest.mark.parametrize("order", [(DOC_A, DOC_B), (DOC_B, DOC_A)])
def test_mapping_bound_topic_resolves_component_in_the_mapping_document(order):
    bundle = ccb._collect_structured_content(_two_doc_artifacts(order))

    topic = ccb._enrich_topics(
        [{"id": "t-eos", "title": "中性子星の状態方程式"}], bundle, _chunks()
    )[0]

    assert topic["content_confidence"] == "exact_title"
    _assert_only_doc_b(topic)


def _figures_index() -> dict:
    index = {}
    for doc, fig_uuid in ((DOC_A, "fig-uuid-a"), (DOC_B, "fig-uuid-b")):
        item = {"figure_id": fig_uuid, "figure_key": "fig_1", "document_id": doc, "caption": "Figure 1."}
        index[fig_uuid] = item
        index[f"{doc}::fig_1"] = item
    return index


def test_doc_a_topic_still_gets_doc_a_equation_and_figure():
    bundle = ccb._collect_structured_content(_two_doc_artifacts())

    topic = ccb._enrich_topics(
        [{"id": "t-cep", "title": "Cep B の磁場構造"}], bundle, _chunks(), _figures_index()
    )[0]

    assert [item.get("latex") for item in _equation_items(topic)] == [LATEX_A]
    assert topic["material_chunk_ids"] == ["chunk-a"]
    # A の図は A の clm_1 を指しているので A のトピックには付く。
    assert [link["target_id"] for link in _links(topic, "figure")] == ["fig-uuid-a"]


def test_doc_b_topic_does_not_get_doc_a_figure_through_same_claim_id():
    bundle = ccb._collect_structured_content(_two_doc_artifacts())

    topic = ccb._enrich_topics(
        [{"id": "t-eos", "title": "中性子星の状態方程式"}], bundle, _chunks(), _figures_index()
    )[0]

    assert _links(topic, "figure") == []


def test_unit_without_document_gets_no_cross_document_equations():
    bundle = ccb._collect_structured_content(_two_doc_artifacts())
    units = {"k1:eos": _unit_row("")}

    topic = ccb._enrich_topics([_unit_topic()], bundle, _chunks(), None, units)[0]

    assert topic["linked_component_ids"] == []
    assert _equation_items(topic) == []
    assert topic["linked_equation_ids"] == []
    assert _links(topic, "equation") == []
    assert _links(topic, "claim") == []
    assert topic["material_chunk_ids"] == []
    assert topic["source_excerpt"] == ""
    assert topic["grounding_note"] == ccb.UNSCOPED_UNITS_GROUNDING_NOTE
    assert topic["coverage"]["status"] == "missing"


def test_unit_whose_document_has_no_artifacts_gets_nothing():
    bundle = ccb._collect_structured_content({DOC_A: _two_doc_artifacts()[DOC_A]})
    units = {"k1:eos": _unit_row(DOC_B)}

    topic = ccb._enrich_topics([_unit_topic()], bundle, _chunks(), None, units)[0]

    assert _equation_items(topic) == []
    assert topic["material_chunk_ids"] == []


def test_scoped_bundle_keeps_both_documents_and_counts_them():
    bundle = ccb._collect_structured_content(_two_doc_artifacts())
    scope = ccb._BundleScope(bundle)

    assert scope.get("equations", DOC_A, "eq_1")["source_extraction"]["latex"] == LATEX_A
    assert scope.get("equations", DOC_B, "eq_1")["source_extraction"]["latex"] == LATEX_B
    # document が空の参照は解決しない（別論文へ落ちない）。
    assert scope.get("equations", "", "eq_1") is None
    assert scope.get("claims", DOC_B, "clm_1")["normalized_text"] == "状態方程式は高密度で軟化する"
    assert scope.count("equations") == 2
    assert scope.count("components") == 2
    assert scope.figure_links(DOC_B, "clm_1") == []
    assert len(scope.figure_links(DOC_A, "clm_1")) == 1


def test_source_chunks_do_not_borrow_same_block_from_another_document():
    """B の根拠 block b_0001 が B のチャンクに無いとき、A の同名 block を借りない。"""
    chunks = [{"id": "chunk-a", "document_id": DOC_A, "block_ids": ["b_0001"], "text": "A"},
              {"id": "chunk-b", "document_id": DOC_B, "block_ids": ["b_0002"], "text": "B"}]
    index = ccb._chunk_block_index(chunks)

    assert ccb._topic_source_chunks(chunks, [(DOC_B, "b_0001")], index) == []
    # 参照側の document がチャンクのどれとも一致しない（material_id 形など）ときだけ救済。
    assert [c["id"] for c in ccb._topic_source_chunks(chunks, [("arxiv-x", "b_0001")], index)] == ["chunk-a"]


def test_chunk_formulas_are_allowed_per_document():
    chunks = [
        {"id": "chunk-a", "document_id": DOC_A, "formulas": [{"id": "eq_1", "latex": LATEX_A}]},
        {"id": "chunk-b", "document_id": DOC_B, "formulas": [{"id": "eq_1", "latex": LATEX_B}]},
    ]

    out = ccb._relevant_chunk_formulas(chunks, {"eq_1"}, allowed_by_document={DOC_B: {"eq_1"}})

    assert [f["latex"] for f in out] == [LATEX_B]
