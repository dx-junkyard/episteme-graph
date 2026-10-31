"""第 25 周 第 6 波 面 A（share）の是正 — IK-1012 / 1020 / 1021 / 1022 / 1023 / 1024 / 1025 / 1026・
IK-0953（一覧の印）・IK-0954（区画注記）。

DB も LLM も使わない（純関数・fake session・ソースの契約）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

ADMIN_JS = (ROOT / "frontend/public/js/admin.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "frontend/public/js/app.js").read_text(encoding="utf-8")
STUDIO_JS = (ROOT / "frontend/public/js/admin-lecture-studio.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "frontend/public/admin.html").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend/public/index.html").read_text(encoding="utf-8")
LIBRARY_MANUAL = (ROOT / "docs/manual/teacher/19-admin-knowledge-library.md").read_text(encoding="utf-8")
STUDIO_MANUAL = (ROOT / "docs/manual/teacher/14-admin-lecture-studio.md").read_text(encoding="utf-8")

AUTHOR = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


# ===========================================================================
# IK-1022 — 記号の照合キーが TeX の間隔命令を残す
# ===========================================================================


class TestSymbolSpacing:
    def test_symbol_lookup_key_drops_spacing_commands(self):
        from core.symbol_lookup import symbol_key

        assert symbol_key(r"F_{\!EOS}") == symbol_key("F_EOS")
        assert symbol_key(r"a\,b") == symbol_key("ab")
        assert symbol_key(r"\quad x") == "x"
        # 間隔命令ではない語の命令は残す（前方一致で食わない）。
        assert "quadrant" in symbol_key(r"\quadrant")

    def test_theory_module_key_is_the_canonical_rule(self):
        from core.theory_modules.schema import clean_symbol_name, symbol_match_key

        assert symbol_match_key(r"F_{\!EOS}") == symbol_match_key("F_EOS")
        for cmd in (r"\!", r"\,", r"\;", r"\:", r"\quad", r"\qquad"):
            assert symbol_match_key(f"F_{{{cmd} EOS}}") == symbol_match_key("F_EOS"), cmd
        assert "\\!" not in clean_symbol_name(r"F_{\!EOS}")

    def test_symbol_lookup_delegates_to_the_canonical_regex(self):
        from core import symbol_lookup

        assert "strip_tex_spacing(" in inspect.getsource(symbol_lookup.symbol_key)


# ===========================================================================
# IK-1012 — 版をまたぐ同一性（出典の重なりで 1 対 1 に決まるときだけ付け替え）
# ===========================================================================


class TestSupersededMatch:
    def test_mutual_unique_best_with_enough_overlap(self):
        from core.knowledge_objects.remap import match_superseded_components as m

        pairs = m({"o1": {"a", "b"}, "o2": {"x", "y"}}, {"n1": {"a", "b", "c"}, "n2": {"x", "y"}})
        assert pairs == [("o1", "n1"), ("o2", "n2")]

    def test_ties_are_not_resolved_by_guessing(self):
        from core.knowledge_objects.remap import match_superseded_components as m

        assert m({"o1": {"a"}, "o2": {"a"}}, {"n1": {"a"}}) == []
        assert m({"o1": {"a", "b"}}, {"n1": {"a"}, "n2": {"b"}}) == []

    def test_weak_overlap_needs_degree_one_on_both_sides(self):
        from core.knowledge_objects.remap import match_superseded_components as m

        # Jaccard 0.25・相手は唯一 → 決まる。
        assert m({"o1": {"a"}}, {"n1": {"a", "b", "c", "d"}}) == [("o1", "n1")]
        # Jaccard が低く、旧側に別の重なりの相手がある → 結ばない。
        olds = {"o1": {"a", "b", "c", "d", "e"}}
        news = {"n1": {"a", "x", "y", "z"}, "n2": {"b", "c", "q", "r", "s", "t", "u"}}
        assert m(olds, news) == []

    def test_empty_sources_never_match(self):
        from core.knowledge_objects.remap import match_superseded_components as m

        assert m({"o1": set()}, {"n1": {"a"}}) == []
        assert m({}, {"n1": {"a"}}) == []

    def test_sync_reports_superseded_and_inserted_ids(self):
        from core.knowledge_objects.sync import sync_live_rows
        from tests.knowledge_object_fakes import FakeKnowledgeSession

        session = FakeKnowledgeSession(live_rows=[{
            "id": "uuid-old", "stable_key": "k1:old", "agent_id": "comp_003",
            "review_status": "teacher_review_required", "created_by": None,
        }])
        result = sync_live_rows(
            session, table="theory_components", document_id="doc-1", run_id="run-2",
            incoming=[{"agent_id": "comp_003", "stable_key": "k1:new", "values": {"name": "New"}}],
            content_columns=("name",), preserved_columns=("review_status",),
            agent_id_column="agent_component_id",
        )
        assert result.superseded_ids == ["uuid-old"]
        assert len(result.inserted_ids) == 1

    def test_persist_components_reanchors_in_the_same_transaction(self):
        from core.document_pipeline import persistence

        src = inspect.getsource(persistence.persist_components)
        assert "_reanchor_superseded_components(" in src
        assert "superseded_ids" in src and "inserted_ids" in src
        wrapper = inspect.getsource(persistence._reanchor_superseded_components)
        assert "begin_nested" in wrapper  # 失敗しても同期そのものは止めない

    def test_reanchor_records_remap_kind_and_moves_human_records(self):
        from core.knowledge_objects import remap

        src = inspect.getsource(remap._reanchor_component_records)
        for table in ("component_explanations", "element_identity_links", "epistemic_ledger",
                      "element_annotations", "deliberation_sessions"):
            assert table in src, table
        assert "DELETE" not in src
        assert remap.REMAP_KIND_SUPERSEDED_COMPONENT == "superseded_component"
        assert "REMAP_KIND_SUPERSEDED_COMPONENT" in inspect.getsource(remap.reanchor_superseded_components)

    def test_stranded_fact_lists_titles_without_ids(self):
        from core.knowledge_objects.remap import STRANDED_RECORD_NOTE, stranded_explanations_fact

        fact = stranded_explanations_fact([
            {"component_name": "TOV network map", "title": "構造ネットワーク", "kind": "personal"},
            {"component_name": "Loss", "title": "", "kind": "standard"},
        ])
        assert "「TOV network map」への説明「構造ネットワーク」" in fact
        assert "「Loss」の標準の説明" in fact
        assert STRANDED_RECORD_NOTE in fact
        assert stranded_explanations_fact([]) == ""

    def test_teacher_list_says_stranded_explanations(self):
        from routes import theory_components as tc

        assert "_stranded_explanations_fact(" in inspect.getsource(tc.list_component_explanations)


# ===========================================================================
# IK-1021 — 凍結したコースの ⚓ は凍結時の部品を指す
# ===========================================================================


def _course(topic_items, links=None):
    return {"topics": [{
        "id": "t1",
        "content_blocks": [{"type": "components", "items": topic_items}],
        "evidence_links": links or [],
    }]}


class TestFrozenComponentRefs:
    def test_stamp_adds_db_id_from_the_live_index(self):
        from core.frozen_component_refs import stamp_component_db_ids

        topics = _course(
            [{"component_id": "comp_003", "document_id": "doc-1", "label": "TOV network map"}],
            [{"kind": "component", "target_id": "comp_003", "label": "TOV network map"}],
        )["topics"]
        n = stamp_component_db_ids(topics, {("doc-1", "comp_003"): "uuid-a"})
        assert n == 2
        assert topics[0]["content_blocks"][0]["items"][0]["db_id"] == "uuid-a"
        assert topics[0]["evidence_links"][0]["db_id"] == "uuid-a"
        # 本文の参照と component_id は変えない。
        assert topics[0]["content_blocks"][0]["items"][0]["component_id"] == "comp_003"

    def test_link_with_ambiguous_document_is_not_stamped(self):
        from core.frozen_component_refs import stamp_component_db_ids

        topics = _course(
            [{"component_id": "comp_003", "document_id": "doc-1"},
             {"component_id": "comp_003", "document_id": "doc-2"}],
            [{"kind": "component", "target_id": "comp_003"}],
        )["topics"]
        stamp_component_db_ids(topics, {("doc-1", "comp_003"): "a", ("doc-2", "comp_003"): "b"})
        assert "db_id" not in topics[0]["evidence_links"][0]

    def test_frozen_ref_reads_db_id_and_label(self):
        from core.frozen_component_refs import frozen_component_ref

        course = _course([{"component_id": "comp_003", "document_id": "doc-1",
                           "label": "TOV network map", "db_id": "uuid-a"}])
        ref = frozen_component_ref(course, "comp_003", "t1")
        assert ref == {"db_id": "uuid-a", "label": "TOV network map", "document_id": "doc-1"}
        assert frozen_component_ref(course, "comp_999", "t1") is None

    def test_resolution_follows_the_remap_record(self, monkeypatch):
        from core import frozen_component_refs as fcr
        from core.knowledge_objects import remap

        monkeypatch.setattr(remap, "successor_component_id", lambda s, cid: "uuid-new")
        out = fcr.resolve_frozen_component(None, component_id="comp_003",
                                           frozen={"db_id": "uuid-old"}, document_ids=["doc-1"])
        assert out == {"status": "remapped", "id": "uuid-new"}
        monkeypatch.setattr(remap, "successor_component_id", lambda s, cid: None)
        # 第 7 波（IK-1051）: 記録が無ければ読み時の付け替えを試し、決まらない理由を添える。
        monkeypatch.setattr(remap, "read_time_superseded_successor", lambda s, cid: (None, "ambiguous"))
        out = fcr.resolve_frozen_component(None, component_id="comp_003",
                                           frozen={"db_id": "uuid-old"}, document_ids=["doc-1"])
        assert out == {"status": "superseded", "id": "uuid-old", "reason": "ambiguous"}
        monkeypatch.setattr(remap, "successor_component_id", lambda s, cid: cid)
        out = fcr.resolve_frozen_component(None, component_id="comp_003",
                                           frozen={"db_id": "uuid-old"}, document_ids=["doc-1"])
        assert out == {"status": "live", "id": "uuid-old"}

    def test_old_freeze_uses_the_frozen_label(self, monkeypatch):
        from core import frozen_component_refs as fcr
        from core.knowledge_objects import remap

        monkeypatch.setattr(remap, "superseded_component_by_agent_id",
                            lambda s, **kw: ("uuid-old", "superseded"))
        monkeypatch.setattr(remap, "successor_component_id", lambda s, cid: None)
        monkeypatch.setattr(remap, "read_time_superseded_successor", lambda s, cid: (None, "not_found"))
        out = fcr.resolve_frozen_component(None, component_id="comp_003",
                                           frozen={"db_id": "", "label": "TOV network map"},
                                           document_ids=["doc-1"])
        assert out == {"status": "superseded", "id": "uuid-old", "reason": "not_found"}
        # 手がかり（db_id も名前も）が無ければ従来の解決に任せる。
        assert fcr.resolve_frozen_component(None, component_id="comp_003",
                                            frozen={"db_id": "", "label": ""},
                                            document_ids=["doc-1"]) is None

    def test_learner_routes_resolve_through_the_frozen_ref(self):
        from routes import learning

        # IK-1075（第 9 波）: 部品の文脈 API と画面文脈は共通の解決点
        # resolve_learner_component_context を通り、凍結の ⚓ の後継解決はそこにある。
        assert "resolve_learner_component_context(" in inspect.getsource(learning.get_course_component_context)
        assert "_resolve_frozen_component_ref(" in inspect.getsource(learning.resolve_learner_component_context)
        assert "_resolve_frozen_component_ref(" in inspect.getsource(
            learning.get_component_explanations_for_learner
        )
        # 第 7 波（IK-1051）: 0 件と「決まらない」を言い分ける文の選択は _superseded_learner_note。
        assert "_superseded_learner_note(" in inspect.getsource(learning.resolve_learner_component_context)
        assert "SUPERSEDED_COMPONENT_LEARNER_NOTE" in inspect.getsource(learning._superseded_learner_note)

    def test_freeze_stamps_db_ids(self):
        from core import course_content_builder as ccb

        src = inspect.getsource(ccb)
        assert "_load_component_uuid_index(session, document_ids)" in src
        assert "_stamp_component_db_ids(enriched_topics, component_uuid_index)" in src

    def test_app_draws_the_server_facts(self):
        start = APP_JS.index("function renderComponentContextPanel")
        body = APP_JS[start:start + 3000]
        assert "data.available === false" in body
        assert "data.version_note" in body


# ===========================================================================
# IK-1023 — 教員の説明の「著者以外の査読者」
# ===========================================================================


def _explanation_row(*, kind="personal", author=AUTHOR, review="teacher_approved", shared=True,
                     non_author_reviewed=False):
    return (
        "exp-1", "comp-1", "course-1", kind, author, "", "t", "b", [], "", review, shared,
        None, None, 0, 0, 0, 0, 0, False, non_author_reviewed,
    )


class TestPersonalSecondReviewer:
    def _out(self, viewer, *, reviewable=True, reviewers=None, **kw):
        from routes import theory_components as tc

        return tc._row_to_explanation_out(
            _explanation_row(**kw), viewer, standard_reviewable=reviewable,
            personal_reviewers=reviewers or [],
        )

    def test_document_editor_can_second_review_a_shared_self_reviewed_explanation(self):
        out = self._out({"id": OTHER, "role": "teacher"})
        assert out.second_review_available is True
        assert out.can_review is True
        assert out.can_edit is False
        from core import explanation_governance as xgov

        assert out.edit_note == xgov.EXPLANATION_AUTHOR_ONLY_NOTE

    def test_not_offered_when_conditions_fail(self):
        assert not self._out({"id": OTHER, "role": "teacher"}, reviewable=False).second_review_available
        assert not self._out({"id": OTHER, "role": "teacher"}, shared=False).second_review_available
        assert not self._out({"id": OTHER, "role": "teacher"}, review="teacher_review_required").second_review_available
        assert not self._out({"id": OTHER, "role": "teacher"}, non_author_reviewed=True).second_review_available
        assert not self._out({"id": OTHER, "role": "teacher"}, reviewers=[OTHER]).second_review_available
        assert not self._out({"id": AUTHOR, "role": "teacher"}).second_review_available

    def test_patch_allows_only_the_review_to_non_authors(self):
        from routes import theory_components as tc

        src = inspect.getsource(tc.patch_explanation)
        assert "EXPLANATION_PERSONAL_SECOND_REVIEW_ONLY" in src
        assert "personal_second_review" in src
        assert "_can_review_standard(" in src

    def test_citation_says_whether_it_reaches_the_citing_course(self):
        from routes import theory_components as tc
        from core import explanation_governance as xgov

        assert xgov.citation_delivery_fact(review_status="teacher_approved", delivered=True) == xgov.CITATION_DELIVERED_FACT
        assert xgov.citation_delivery_fact(review_status="teacher_approved", delivered=False) == xgov.CITATION_NOT_YET_DELIVERED_FACT
        assert xgov.citation_delivery_fact(review_status="draft", delivered=True) == xgov.CITATION_NOT_APPROVED_FACT
        src = inspect.getsource(tc.cite_explanation) + inspect.getsource(tc._citation_response)  # 第 11 波（IK-1154）で応答を分けた
        assert "outcome_fact" in src and "_explanation_delivered_to_course(" in src
        assert "LEARNER_EXPLANATION_SCOPE_SQL" in inspect.getsource(tc._explanation_delivered_to_course)

    def test_card_hides_share_from_second_reviewers(self):
        card = STUDIO_JS[STUDIO_JS.index("function lsRenderExplanationCard"):]
        card = card[:card.index("function lsBindExplanationActions")]
        assert 'exp.kind !== "standard" && exp.can_edit' in card

    def test_manual_names_the_reviewer(self):
        assert "著者以外の、その論文を編集できる教員" in STUDIO_MANUAL
        assert "引用しただけでは引用先のコースの学生に届きません" in STUDIO_MANUAL


# ===========================================================================
# IK-1024 — ラベルの見送りを見える記録に
# ===========================================================================


class _LabelSession:
    def __init__(self, row):
        self.row = row
        self.sql: list[str] = []

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        row = self.row

        class _R:
            def fetchone(self_inner):
                return row

        return _R()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestLabelRestore:
    def test_restore_requires_a_reason(self):
        import pytest
        from core.library import registry

        with pytest.raises(ValueError):
            registry.restore_label("33333333-3333-3333-3333-333333333333", actor_id=AUTHOR, review_note=" ")

    def test_restore_is_a_status_transition_only_from_dismissed(self):
        from core.library import registry

        src = inspect.getsource(registry.restore_label)
        assert "UPDATE library_entry_labels" in src
        assert "AND status = :dismissed" in src
        assert "DELETE" not in src
        from core.library import schema

        assert schema.AUDIT_ACTION_LABEL_RESTORE in schema.REGISTRY_AUDIT_ACTIONS

    def test_routes_return_facts_and_decision_lines(self):
        from routes import library

        assert "LABEL_DISMISSED_FACT" in inspect.getsource(library.dismiss_entry_label)
        assert "restore_label(" in inspect.getsource(library.restore_entry_label)
        assert "_attach_label_decision_lines(" in inspect.getsource(library.list_entry_labels)
        line = library.LABEL_DISMISSED_LINE.format(reason="OCR のノイズ", actor="岡村", date="2026-10-27")
        assert "OCR のノイズ" in line and "岡村" in line

    def test_admin_shows_dismissed_rows_with_restore(self):
        body = ADMIN_JS[ADMIN_JS.index("function loadLibraryEntryLabels"):]
        body = body[:body.index("function loadLibraryEntryRelations")]
        assert "include_dismissed=true" in body
        assert 'data-ui-anchor="knowledge-library.labels-dismissed"' in body
        assert 'data-ui-anchor="knowledge-library.label-restore"' in body
        assert "/restore" in body
        assert "l.decision_line" in body

    def test_manual_sections(self):
        assert "{#labels-dismissed}" in LIBRARY_MANUAL
        assert "{#label-restore}" in LIBRARY_MANUAL


# ===========================================================================
# IK-1025 — 旅の出口案内は実行できる操作だけ
# ===========================================================================


class TestJourneyExit:
    def test_selected_text_exit_does_not_send_to_the_card(self):
        from core.personal_graph import nearby
        from core.personal_graph.schema import PersonalAnchor, PersonalNode

        node = PersonalNode(
            id="n1", node_kind="question", label="q",
            anchor=PersonalAnchor(anchor_type="segment", anchor_id="seg_3"),
            topic_id="t1", created_at="", facts=[], source={},
        )
        fact = nearby.sharpen_fact_for(node)
        assert "式か主張の文だけを選び直して質問" in fact
        assert "帰属カードで主張や式を選んで確定" not in fact
        assert "テキストを選んで質問" not in fact


# ===========================================================================
# IK-1026 / IK-1020 / IK-0953 — 対の確認の問い・片側の置き換わり・判断の印
# ===========================================================================


class TestPairQuestions:
    def test_pair_question_names_the_partner_with_version_and_title(self):
        from core.deliberation import identity_links as il

        q = il.pair_confirm_question(
            instance_type_label="部品", instance_label="TOV residual loss at collocation points",
            instance_side=il.instance_side_label("TeX 版", "PINN"),
            partners=[("部品", "TOV residual constraint", il.instance_side_label("PDF 版", "PINN"))],
            shared_part_name="TOV residual loss at collocation points",
        )
        assert "部品「TOV residual loss at collocation points」（TeX 版『PINN』）" in q
        assert "部品「TOV residual constraint」（PDF 版『PINN』）" in q
        assert "共通部品「TOV residual loss at collocation points」は、同じもの" not in q

    def test_without_partners_falls_back(self):
        from core.deliberation import identity_links as il

        q = il.pair_confirm_question(instance_type_label="部品", instance_label="A", instance_side="",
                                     partners=[], shared_part_name="X")
        assert q == il.confirm_question("部品", "A", "X")

    def test_shared_part_listing_attaches_pair_and_replaced_facts(self):
        from routes import deliberation as d
        from core.deliberation import identity_links as il

        rows = [
            {"shared_part_id": "e1", "status": "candidate", "instance_element_type": "theory_component",
             "instance_type_label": "部品", "instance_label": "A", "instance_document_title": "P",
             "instance_document_format_label": "TeX 版", "shared_part_name": "A", "reason": "r"},
            {"shared_part_id": "e1", "status": "candidate", "instance_element_type": "theory_component",
             "instance_type_label": "部品", "instance_label": "B", "instance_document_title": "P",
             "instance_document_format_label": "PDF 版", "shared_part_name": "A", "reason": "r"},
        ]
        d._attach_pair_confirm_questions(rows)
        assert "（PDF 版『P』）" in rows[0]["confirm_question"]
        replaced = [
            {"shared_part_id": "e2", "status": "candidate", "instance_element_type": "theory_component",
             "instance_type_label": "部品", "instance_label": "B", "shared_part_name": "X",
             "reason": "別の論文の記述と本文の意味が近いと測定されました"},
            {"shared_part_id": "e2", "status": "candidate", "instance_element_type": "theory_component",
             "instance_type_label": "部品", "instance_label": "", "shared_part_name": "X"},
        ]
        d._attach_pair_confirm_questions(replaced)
        assert replaced[0]["reason"] == il.PARTNER_REPLACED_REASON
        assert "解析のやり直しで置き換わりました" in replaced[0]["confirm_question"]

    def test_library_queue_judges_stale_rule_on_all_links_and_drops_internal_keys(self):
        from routes import library

        src = inspect.getsource(library.list_identity_candidates)
        assert "visible + unresolved" in src
        assert "partner_replaced_note" in src
        items = [
            library._identity_candidate_item(
                {"id": "l1", "instance_element_type": "theory_component", "instance_element_id": "c1",
                 "local_expression": {"name": "A"}, "status": "candidate", "reason": "別の論文の記述と…"},
                entry={"name": "A"}, document_id="d1", display={"title": "P", "format_label": "TeX 版"},
                component=None,
            ),
            library._identity_candidate_item(
                {"id": "l2", "instance_element_type": "theory_component", "instance_element_id": "c2",
                 "local_expression": {"name": "B"}, "status": "confirmed",
                 "decided_by": None, "decided_at": "2026-10-27T03:00:00"},
                entry={"name": "A"}, document_id="d2", display={"title": "P", "format_label": "PDF 版"},
                component=None,
            ),
        ]
        library._attach_identity_pair_facts(items, entry={"name": "A"}, unresolved=False)
        assert "部品「B」（PDF 版『P』）" in items[0]["confirm_question"]
        assert items[1]["decision_line"].startswith("〔確定〕 確定: ")
        assert "2026-10-27" in items[1]["decision_line"]
        for it in items:
            assert not [k for k in it if k.startswith("_")]

    def test_partner_replaced_reason_replaces_the_stored_proxy_text(self):
        from routes import library
        from core.deliberation import identity_links as il

        items = [library._identity_candidate_item(
            {"id": "l1", "instance_element_type": "theory_component", "instance_element_id": "c1",
             "local_expression": {"name": "A"}, "status": "candidate",
             "reason": "別の論文の記述と本文の意味が近いと測定されました"},
            entry={"name": "A"}, document_id="d1", display={"title": "P"}, component=None,
        )]
        library._attach_identity_pair_facts(items, entry={"name": "A"}, unresolved=True)
        assert items[0]["reason"] == il.PARTNER_REPLACED_REASON
        assert "別の論文" not in items[0]["reason"]

    def test_decision_outcome_fact_and_admin_rendering(self):
        from core.deliberation import identity_links as il

        line = il.decision_line("confirmed", "岡村 千尋", "2026-10-27T01:02:03")
        assert line == "〔確定〕 確定: 岡村 千尋・2026-10-27"
        assert "凍結した版" in il.decision_outcome_fact("confirmed", line)
        assert il.decision_line("candidate", "x", None) == ""
        assert "link.decision_line" in ADMIN_JS
        assert "data.outcome_fact" in ADMIN_JS[ADMIN_JS.index("function _libraryDecideIdentityLink"):][:1500]
        assert "entry.partner_replaced_note" in ADMIN_JS


# ===========================================================================
# IK-0954 — 区画注記が凍結の条件を言う
# ===========================================================================


class TestBoundPartsNote:
    def test_section_note_states_the_freeze_condition(self):
        assert "確定した結び付きは、この共通部品に凍結した版があるとき学生の「共通部品として」に出ます" in ADMIN_JS
        assert "確定した結び付きは学生の「共通部品として」に出ます。\"" not in ADMIN_JS


class TestCacheBusters:
    def test_share6_versions(self):
        assert "-share6" in ADMIN_HTML.split("/js/admin.js?v=")[1].split('"')[0]
        assert "-share6" in ADMIN_HTML.split("/js/admin-lecture-studio.js?v=")[1].split('"')[0]
        assert "-share6" in INDEX_HTML.split("/js/app.js?v=")[1].split('"')[0]
