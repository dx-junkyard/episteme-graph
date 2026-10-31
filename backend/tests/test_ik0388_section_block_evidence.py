"""章立ての単位（section_block）と数式プレースホルダーのテスト（IK-0388 / IK-0389）。

IK-0388: ``topic.units`` が section_block の単位だけのトピックに、根拠（主張・式・図・
出典チャンク）が1つも付かなかった。section_block の単位は component を束ねず
（``agent_payload`` は ``block_type`` だけ）、``linked_claim_ids`` は DB UUID で
artifact の claim 名前空間と突合できないため。章（``section_ids``）と出典 block
（``source_block_ids``）の実所在から主張・式・図を引くことを固定する。

IK-0389: 配信する本文に、``content_blocks`` の式から引けない ``[[FORMULA_N]]`` を
残さない（学習画面 app.js と同じ引き方で判定し、引けないものだけ事実の文に置き換える）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import core.course_content_builder as ccb
from core import course_units, label_vocab
from core.course_data import UNIT_SOURCE_TEACHER_SELECTED

DOC = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


# ---------------------------------------------------------------------------
# フィクスチャ（手組みの scoped bundle = _collect_structured_content と同じ形）
# ---------------------------------------------------------------------------


def _artifacts():
    return {
        "claim_object_builder": {
            "claims": [
                {
                    "claim_id": "c_setup",
                    "text": "The filament is observed in dust polarization.",
                    "source_evidence_ids": ["ev_1"],
                    "section_id": "sec_2",
                    "equation_ids": ["eq_1"],
                },
                {
                    "claim_id": "c_data",
                    "text": "Column density is derived from Herschel maps.",
                    "source_evidence_ids": ["ev_2"],
                    "section_id": "sec_2",
                },
                {
                    "claim_id": "c_result",
                    "text": "The mass-to-flux ratio is supercritical.",
                    "source_evidence_ids": ["ev_3"],
                    "section_id": "sec_4",
                },
                {
                    # atomic 子を持つ親は子で代表させて落とす。
                    "claim_id": "c_parent",
                    "text": "Parent claim",
                    "source_evidence_ids": ["ev_1"],
                    "section_id": "sec_2",
                    "subclaim_ids": ["c_setup"],
                },
            ]
        },
        "evidence_registry": {
            "records": [
                {"evidence_id": "ev_1", "evidence_text": "…", "source": {"block_id": "b_10", "section_id": "sec_2"}},
                {"evidence_id": "ev_2", "evidence_text": "…", "source": {"block_id": "b_11", "section_id": "sec_2"}},
                {"evidence_id": "ev_3", "evidence_text": "…", "source": {"block_id": "b_40", "section_id": "sec_4"}},
            ]
        },
        "equation_semantics": {
            "equations": [
                {"equation_id": "eq_1", "latex": r"N = \int n\,dl",
                 "source_location": {"block_id": "b_12", "section_id": "sec_2"}},
                {"equation_id": "eq_9", "latex": r"\lambda = M/\Phi",
                 "source_location": {"block_id": "b_41", "section_id": "sec_4"}},
            ]
        },
        "figure_table_semantics": {
            "figures": [
                {"figure_id": "fig_1", "caption": "Polarization map", "linked_claim_ids": ["c_setup"]},
            ]
        },
    }


def _bundle(documents=(DOC,)):
    return ccb._collect_structured_content({doc: _artifacts() for doc in documents})


def _section_unit(stable_key="k1:sec2", *, document_id=DOC, sections=("sec_2",), blocks=("b_10",)):
    return {
        "unit_id": "uuid-" + stable_key,
        "document_id": document_id,
        "stable_key": stable_key,
        "unit_kind": "section_block",
        "label": "問題設定と観測データ",
        "summary": "",
        # DB UUID — artifact の claim 名前空間とは突合できない（従来の失敗の原因）。
        "linked_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],
        "linked_equation_ids": [],
        "linked_component_ids": [],
        "linked_figure_ids": [],
        "agent_payload": {"block_type": "problem_setup"},
        "order_index": 0,
        "section_ids": list(sections),
        "source_block_ids": list(blocks),
    }


def _topic(*keys):
    return {
        "id": "t1",
        "title": "問題設定と観測データ",
        "units": [
            {"kind": "section_block", "stable_key": k, "unit_id": "uuid-" + k,
             "label": "問題設定と観測データ", "source": UNIT_SOURCE_TEACHER_SELECTED}
            for k in keys
        ],
    }


def _chunks():
    return {
        "m1": [
            {"id": "chunk-a", "document_id": DOC, "text": "Section 2 text", "block_ids": ["b_10", "b_11", "b_12"],
             "formulas": [{"id": "[[FORMULA_0]]", "latex": r"N = \int n\,dl"}]},
            {"id": "chunk-b", "document_id": DOC, "text": "Section 4 text", "block_ids": ["b_40", "b_41"],
             "formulas": []},
        ]
    }


def _figures_index():
    item = {"figure_id": "f-uuid-1", "document_id": DOC, "figure_key": "fig_1",
            "caption": "Polarization map"}
    # ``_load_document_figures_index`` と同じキー規則（UUID と document::正規化キー）。
    return {"f-uuid-1": item, f"{DOC}::fig_1": item}


# ---------------------------------------------------------------------------
# IK-0388
# ---------------------------------------------------------------------------


class TestSectionBlockUnitsCarryEvidence:
    def _enrich(self, units, topic=None, *, bundle=None):
        return ccb._enrich_topics(
            [topic or _topic(*units.keys())], bundle or _bundle(), _chunks(), _figures_index(), units
        )[0]

    def test_section_claims_equations_and_figures_are_bound(self):
        units = {"k1:sec2": _section_unit()}
        topic = self._enrich(units)
        assert topic["linked_claim_ids"][:2] == ["c_setup", "c_data"]
        assert "c_parent" not in topic["linked_claim_ids"]
        assert "c_result" not in topic["linked_claim_ids"]  # 別の章
        assert topic["linked_equation_ids"] == ["eq_1"]
        kinds = {(link["kind"], link["target_id"]) for link in topic["evidence_links"]}
        assert ("claim", "c_setup") in kinds and ("claim", "c_data") in kinds
        assert ("equation", "eq_1") in kinds
        assert ("figure", "f-uuid-1") in kinds
        assert topic["material_chunk_ids"] == ["chunk-a"]
        assert topic["content_source"] == ccb.UNIT_SELECTION_CONTENT_SOURCE

    def test_block_match_ranks_before_section_match(self):
        units = {"k1:sec2": _section_unit(blocks=("b_11",))}
        topic = self._enrich(units)
        assert topic["linked_claim_ids"][0] == "c_data"

    def test_other_document_is_never_used(self):
        """同じ ID の主張・式が別論文にあっても、単位の論文の中でだけ引く（IK-0377）。"""
        units = {"k1:sec2": _section_unit(document_id=DOC)}
        topic = self._enrich(units, bundle=_bundle(documents=(DOC, OTHER)))
        for link in topic["evidence_links"]:
            if link["kind"] == "figure":
                assert link.get("document_id") in ("", DOC)
        refs = ccb._section_unit_refs([_section_unit(document_id=DOC)], ccb._BundleScope(_bundle((DOC, OTHER))))
        for refs_list in refs:
            assert all(doc == DOC for doc, _ in refs_list)

    def test_unit_without_document_binds_nothing(self):
        units = {"k1:sec2": _section_unit(document_id="")}
        topic = self._enrich(units)
        assert topic["linked_claim_ids"] == []
        assert topic["evidence_links"] == []

    def test_non_section_units_are_not_widened(self):
        row = _section_unit()
        row["unit_kind"] = "thesis_support"
        refs = ccb._section_unit_refs([row], ccb._BundleScope(_bundle()))
        assert refs == ([], [], [])

    def test_claim_limit(self):
        artifacts = _artifacts()
        artifacts["claim_object_builder"]["claims"] = [
            {"claim_id": f"c_{i}", "text": "x", "source_evidence_ids": [], "section_id": "sec_2"}
            for i in range(20)
        ]
        bundle = ccb._collect_structured_content({DOC: artifacts})
        claims, _eqs, _blocks = ccb._section_unit_refs([_section_unit()], ccb._BundleScope(bundle))
        assert len(claims) == ccb._SECTION_UNIT_CLAIM_LIMIT


class TestUnitRowsCarrySectionsAndBlocks:
    def test_loader_reads_section_ids_and_source_block_ids(self):
        row = (
            "u-1", DOC, "k1:a", "section_block", "章", "", [], [], [], [], {}, 0,
            ["sec_2"], ["b_10", "b_10"],
        )
        session = MagicMock()
        session.execute.return_value.fetchall.return_value = [row]
        units = course_units.load_units_for_documents(session, [DOC])
        assert course_units.unit_section_ids(units["k1:a"]) == ["sec_2"]
        assert course_units.unit_source_block_ids(units["k1:a"]) == ["b_10"]
        sql = str(session.execute.call_args.args[0])
        assert "section_ids" in sql and "source_block_ids" in sql

    def test_legacy_twelve_column_rows_still_load(self):
        row = ("u-1", DOC, "k1:a", "section_block", "章", "", [], [], [], [], {}, 0)
        session = MagicMock()
        session.execute.return_value.fetchall.return_value = [row]
        units = course_units.load_units_for_documents(session, [DOC])
        assert units["k1:a"]["section_ids"] == []
        assert units["k1:a"]["source_block_ids"] == []


# ---------------------------------------------------------------------------
# IK-0389
# ---------------------------------------------------------------------------


class TestUnresolvedFormulaPlaceholders:
    def test_placeholders_without_formulas_are_replaced(self):
        topic = {
            "id": "t1",
            "content_blocks": [],
            "student_material": {"source_format": "eg-markdown-v1",
                                 "source_text": "主結果は [[FORMULA_0]] [[FORMULA_1]] [[FORMULA_2]] である。"},
            "spoken_script": "式 [[FORMULA_0]] を見てください。",
        }
        assert ccb._sanitize_topic_formula_placeholders(topic) is True
        text = topic["student_material"]["source_text"]
        assert "[[FORMULA_" not in text
        assert label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT in text
        assert "[[FORMULA_" not in topic["spoken_script"]
        assert topic["grounding_note"] == label_vocab.UNRESOLVED_FORMULA_GROUNDING_NOTE
        assert topic["coverage"]["status"] == "weak"

    def test_resolvable_placeholders_are_kept(self):
        topic = {
            "content_blocks": [
                {"type": "equations", "items": [{"equation_id": "eq_1", "latex": "a=b"}]}
            ],
            "student_material": {"source_text": "[[FORMULA_0]] と [[FORMULA_1]] と ![[equation:eq_1]]"},
        }
        ccb._sanitize_topic_formula_placeholders(topic)
        text = topic["student_material"]["source_text"]
        assert "[[FORMULA_0]]" in text          # 位置 0 で引ける
        assert "[[FORMULA_1]]" not in text      # 引けない
        assert "![[equation:eq_1]]" in text     # 埋め込み記法には触れない

    def test_existing_coverage_is_not_overwritten(self):
        topic = {
            "student_material": {"source_text": "[[FORMULA_3]]"},
            "coverage": {"status": "missing", "message": "x"},
            "grounding_note": "既存",
        }
        ccb._sanitize_topic_formula_placeholders(topic)
        assert topic["coverage"] == {"status": "missing", "message": "x"}
        assert topic["grounding_note"] == "既存"

    def test_clean_material_is_untouched(self):
        topic = {"student_material": {"source_text": "数式なし"}}
        assert ccb._sanitize_topic_formula_placeholders(topic) is False
        assert "grounding_note" not in topic

    def test_draft_generation_never_delivers_unresolved_placeholders(self, monkeypatch):
        """生成モデルが引けないプレースホルダーを書いても、配信本文には残らない。"""

        def fake_draft(**_kwargs):
            return {
                "key_concepts": ["λ"],
                "student_material": {"source_format": "eg-markdown-v1",
                                     "source_text": "主結果: [[FORMULA_0]] [[FORMULA_1]]"},
                "spoken_script": "[[FORMULA_2]]",
                "cautions": [],
                "check_questions": [],
            }

        monkeypatch.setattr(ccb, "_generate_single_topic_draft", fake_draft)
        topics = [{"id": "t1", "title": "主結果", "content_blocks": []}]
        ccb._generate_course_topic_drafts({"title": "c"}, topics)
        assert "[[FORMULA_" not in topics[0]["student_material"]["source_text"]
        assert "[[FORMULA_" not in topics[0]["spoken_script"]

    def test_fallback_material_is_also_sanitized(self, monkeypatch):
        def boom(**_kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(ccb, "_generate_single_topic_draft", boom)
        topics = [{"id": "t1", "title": "主結果", "summary": "要約 [[FORMULA_4]]", "content_blocks": []}]
        ccb._generate_course_topic_drafts({"title": "c"}, topics)
        assert "[[FORMULA_" not in topics[0]["student_material"]["source_text"]

    def test_replacement_text_has_no_numbers_or_ids(self):
        for line in (label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT, label_vocab.UNRESOLVED_FORMULA_GROUNDING_NOTE):
            assert not any(ch.isdigit() for ch in line)
            assert "FORMULA" not in line


def test_formula_with_empty_latex_counts_as_unresolved():
    """latex も plain_text も空の数式は画面に描けないので、その [[FORMULA_N]] は事実文に置き換える（第 9 周）。"""
    from core import label_vocab
    from core.course_content_builder import replace_unresolved_formula_placeholders

    text, changed = replace_unresolved_formula_placeholders(
        "13CO の [[FORMULA_0]] 分子線", [{"id": "eq_inline_x", "latex": ""}]
    )
    assert changed and label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT in text and "[[FORMULA_0]]" not in text
    text2, changed2 = replace_unresolved_formula_placeholders("式 [[FORMULA_0]]", [{"id": "eq_x", "latex": "J=3-2"}])
    assert not changed2 and "[[FORMULA_0]]" in text2
