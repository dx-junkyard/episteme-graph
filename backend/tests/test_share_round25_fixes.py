"""第 25 周・面 A（共有・転用の通し）第 1 周の是正（IK-0861〜IK-0871）。

DB へ行かない単体・静的検査で、是正の振る舞いを固定する:

- IK-0861 C層の説明の権限は部品の document 単位・学生側は受講ゲート + 共有範囲
- IK-0862 式の表示名は正本へ委譲・数字の無い TeX 由来の式 ID も遮断・方針語の注意は出さない
- IK-0863 「共通部品として」が無い理由の事実文
- IK-0864 空の状態の文言はサーバの facts 1 つ・導出結果の事実文を画面が描く
- IK-0865 Copilot の概念レジストリ 4 項目・KB 節に内部語を書かない
- IK-0866 ライブラリ・共有の表示語（同梱の初期データ・凍結版・権限語）
- IK-0867 別名の編集欄を外す（正本はラベル区画）
- IK-0868 関係の相手は名前で検索して選ぶ
- IK-0869 / IK-0870 記号の照会（式だけの定義・登録はあるが定義なし・但し書き・字面の注記）
- IK-0871 上位レーンの同語反復
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
for path in (str(BACKEND), str(BACKEND / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)

APP_JS = (REPO / "frontend/public/js/app.js").read_text(encoding="utf-8")
ADMIN_JS = (REPO / "frontend/public/js/admin.js").read_text(encoding="utf-8")
STUDIO_JS = (REPO / "frontend/public/js/admin-lecture-studio.js").read_text(encoding="utf-8")
ADMIN_HTML = (REPO / "frontend/public/admin.html").read_text(encoding="utf-8")

COMP = "33333333-3333-4333-8333-333333333333"
DOC = "11111111-1111-4111-8111-111111111111"


# ===========================================================================
# IK-0861
# ===========================================================================


class TestExplanationPermissionIsDocumentScoped:
    def _component(self, course_id=""):
        from api.routes.theory_components import TheoryComponentOut

        return TheoryComponentOut.model_construct(
            id=COMP, course_id=course_id, source_scope={"document_id": DOC}
        )

    def test_pipeline_component_without_course_is_viewable_via_document(self):
        from api.routes import theory_components as tc

        with patch.object(tc, "_ensure_document_viewable", return_value=[{}]) as doc_gate, patch.object(
            tc, "_ensure_viewable"
        ) as course_gate:
            tc._ensure_component_viewable(self._component(), {"id": "u1", "role": "TEACHER"})
        doc_gate.assert_called_once()
        course_gate.assert_not_called()

    def test_document_denied_and_no_course_is_404(self):
        from api.routes import theory_components as tc

        with patch.object(
            tc, "_ensure_document_viewable", side_effect=HTTPException(status_code=404, detail="x")
        ):
            with pytest.raises(HTTPException) as exc:
                tc._ensure_component_viewable(self._component(), {"id": "u1", "role": "TEACHER"})
        assert exc.value.status_code == 404

    def test_document_denied_falls_back_to_course(self):
        from api.routes import theory_components as tc

        with patch.object(
            tc, "_ensure_document_viewable", side_effect=HTTPException(status_code=404, detail="x")
        ), patch.object(tc, "_ensure_viewable") as course_gate:
            tc._ensure_component_viewable(self._component("c1"), {"id": "u1", "role": "TEACHER"})
        course_gate.assert_called_once_with("c1", {"id": "u1", "role": "TEACHER"})

    def test_explanation_routes_do_not_gate_on_component_course_id(self):
        """一覧・追加・承認・承認一覧が部品の course_id だけでゲートしない（構造検査）。"""
        import inspect

        from api.routes import theory_components as tc

        for fn in (tc.list_component_explanations, tc.create_component_explanation):
            source = inspect.getsource(fn)
            assert "_ensure_component_viewable(component, current_user)" in source
            assert "_ensure_viewable(component.course_id" not in source
        for fn in (tc.endorse_explanation, tc.list_endorsements):
            source = inspect.getsource(fn)
            assert "_ensure_explanation_viewable(" in source
            assert "_ensure_viewable(course_id, current_user)" not in source

    def test_create_records_authoring_course_as_provenance(self):
        from api.routes.theory_components import ExplanationCreateRequest

        assert ExplanationCreateRequest(course_id="c9").course_id == "c9"
        assert 'course_id: lsState.courseId || ""' in STUDIO_JS


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _CaptureSession:
    def __init__(self, rows=()):
        self.rows = rows
        self.sql = ""
        self.params = {}

    def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = dict(params or {})
        return _Rows(self.rows)

    def close(self):
        pass


class TestLearnerExplanationScope:
    def _call(self, *, resolved, rows=()):
        from api.routes import learning as learning_mod

        session = _CaptureSession(rows)
        with patch.object(learning_mod, "get_accessible_course_data", return_value={"sources": []}), patch.object(
            learning_mod, "_course_document_ids", return_value=[DOC]
        ), patch.object(learning_mod, "resolve_course_component_id", return_value=resolved), patch.object(
            learning_mod, "_pg_session", return_value=session
        ):
            result = learning_mod.get_component_explanations_for_learner("c1", "comp_003", {"id": "s1"})
        return result, session

    def test_agent_id_is_resolved_to_uuid_before_query(self):
        result, session = self._call(resolved=COMP)
        assert session.params["cid"] == COMP
        assert "e.shared = TRUE" in session.sql
        assert "e.course_id = :course_id" in session.sql
        assert result["component_id"] == COMP

    def test_empty_has_note_fact(self):
        from api.routes import learning as learning_mod

        result, _ = self._call(resolved=COMP)
        assert result["explanations"] == []
        assert result["note"] == learning_mod.COMPONENT_EXPLANATIONS_EMPTY_NOTE

    def test_component_not_in_course_is_404_with_fact(self):
        from api.routes import learning as learning_mod

        with pytest.raises(HTTPException) as exc:
            self._call(resolved=None)
        assert exc.value.status_code == 404
        assert exc.value.detail == learning_mod.COMPONENT_EXPLANATIONS_NOT_IN_COURSE

    def test_gate_is_enrollment_not_teacher_view(self):
        import inspect

        from api.routes import learning as learning_mod

        source = inspect.getsource(learning_mod.get_component_explanations_for_learner)
        assert "get_accessible_course_data(" in source
        assert "get_viewable_course_data(" not in source

    def test_app_js_separates_404_from_empty(self):
        assert "res.status === 404" in APP_JS
        assert "(data && data.note)" in APP_JS


# ===========================================================================
# IK-0862
# ===========================================================================


class TestEquationLabelAndIdMask:
    def test_tex_label_equation_ids_are_internal(self):
        from core.display_projection import contains_internal_id, mask_internal_ids

        assert contains_internal_id("see eq_eosnet and eq_total_loss")
        assert "eq_eosnet" not in mask_internal_ids("eq_eosnet で定義")
        # 論文の式番号は可読なので遮断しない。
        assert not contains_internal_id("eq_12")
        assert not contains_internal_id("eq_2_7")
        # 下付きの一部（T_eq_x）は当たらない。
        assert not contains_internal_id("T_eq_x")

    def test_policy_word_cautions_are_dropped(self):
        from core import component_context

        items = component_context._build_cautions(
            {"cautions": [{"text": "式抽出は semantic_hint_only 扱い"}, {"text": "低エネルギー近似に限る"}]}
        )
        assert [i["text"] for i in items] == ["低エネルギー近似に限る"]


# ===========================================================================
# IK-0863
# ===========================================================================


class TestSharedPartNote:
    def test_note_is_closed_world_and_rendered(self):
        from core import component_context

        note = component_context.SHARED_PART_ABSENT_NOTE
        assert "このコーパスの中には" in note
        assert "候補" not in note
        assert "data.shared_part_note" in APP_JS

    def test_student_manual_has_sections(self):
        manual = (REPO / "docs/manual/student/02-student.md").read_text(encoding="utf-8")
        assert "{#material-element-shared-part}" in manual
        assert "{#material-element-explanations}" in manual


# ===========================================================================
# IK-0864
# ===========================================================================


class TestRegistryEmptyStates:
    def _identity(self, include_dismissed):
        from api.routes import library as library_route

        with patch.object(library_route.library_store, "list_entries", return_value=[]), patch.object(
            library_route._identity_links, "list_for_shared_parts", return_value={}
        ), patch.object(library_route, "_document_access_checker", return_value=lambda _d: True):
            return library_route.list_identity_candidates(
                domain_key=None, include_dismissed=include_dismissed, current_user={"id": "u1"}
            )

    def test_empty_fact_differs_with_dismissed(self):
        from api.routes import library as library_route

        plain = self._identity(False)["facts"]
        dismissed = self._identity(True)["facts"]
        assert plain[0] == library_route.IDENTITY_CANDIDATES_EMPTY
        assert dismissed[0] == library_route.IDENTITY_CANDIDATES_EMPTY_WITH_DISMISSED
        assert library_route.IDENTITY_CANDIDATES_ORIGIN_FACT in plain

    def test_derive_result_facts_are_rendered(self):
        assert 'id="library-atlas-derive-facts"' in ADMIN_HTML
        seg = ADMIN_JS[ADMIN_JS.index("function deriveLibraryAtlasLinks"):]
        seg = seg[: seg.index("\n  }\n")]
        assert "library-atlas-derive-facts" in seg
        assert "data.facts" in seg


# ===========================================================================
# IK-0865
# ===========================================================================


class TestCopilotRegistryCapabilities:
    IDS = ("library.identity_candidates", "library.labels", "library.relations", "library.atlas_links")

    def test_capabilities_are_guidance_only_with_kb(self):
        from core.admin_assistant import capabilities, knowledge

        kb = knowledge.get_knowledge_base() if hasattr(knowledge, "get_knowledge_base") else None
        for cap_id in self.IDS:
            cap = capabilities.get_capability(cap_id)
            assert cap is not None, cap_id
            assert cap.kind == capabilities.KIND_GUIDANCE_ONLY
            assert cap.howto_doc.startswith("admin_operations/library.md#")
            for st in cap.locate_steps:
                base = st.anchor_id.split(":")[0]
                assert base + ":" in ADMIN_JS or base + ": function" in ADMIN_JS, base
            if kb is not None:
                section = kb.section_for_howto(cap.howto_doc)
                assert section and section.get("body"), cap_id

    def test_kb_has_no_internal_words(self):
        body = (REPO / "docs/admin_operations/library.md").read_text(encoding="utf-8")
        body = body.split("---", 2)[-1]  # front-matter（role: TEACHER）は語彙ではないので除く
        for word in ("楽観ロック", "expected_revision", "409", "retired", "draft", "TEACHER", "(L層", "knowledge-library"):
            assert word not in body, word


# ===========================================================================
# IK-0866
# ===========================================================================


class TestLibraryDisplayVocabulary:
    def test_display_entries_maps_internal_values(self):
        from api.routes import library as library_route

        class _S:
            def execute(self, *a, **k):
                raise RuntimeError("no db")

            def rollback(self):
                pass

            def close(self):
                pass

        with patch.object(library_route, "get_session", return_value=_S()):
            out = library_route._display_entries(
                [
                    {"id": "e1", "created_by": "bundled_import", "updated_by": "bundled_import",
                     "domain_key": "unassigned", "latest_version_no": 0, "review_status": "confirmed"},
                    {"id": "e2", "updated_by": "44444444-4444-4444-8444-444444444444",
                     "domain_key": "unassigned", "latest_version_no": 2, "review_status": "candidate"},
                ]
            )
        assert out[0]["updated_by_label"] == library_route.LIBRARY_ACTOR_BUNDLED
        assert out[0]["version_line"] == library_route.LIBRARY_VERSION_NONE
        assert out[0]["domain_label"] == library_route.LIBRARY_DOMAIN_UNASSIGNED_LABEL
        assert out[0]["review_status_note"] == library_route.LIBRARY_REVIEW_CONFIRMED_NOTE
        assert out[1]["version_line"] == "凍結版 2"
        assert "44444444" not in out[1]["updated_by_label"]
        assert "review_status_note" not in out[1]

    def test_admin_js_does_not_show_revision_or_raw_actor(self):
        assert "' / revision ' + entry.revision" not in ADMIN_JS
        assert "escHtml(e.updated_by) :" not in ADMIN_JS
        assert "e.updated_by_label" in ADMIN_JS
        assert "entry.review_status_note" in ADMIN_JS

    def test_share_permission_words_are_japanese(self):
        from api.schemas import DocumentGroupPermissionOut

        out = DocumentGroupPermissionOut(document_id="d", group_id="g", permission="viewer")
        assert out.permission_label == "閲覧・引用できる"
        assert 'viewer (閲覧・引用)' not in ADMIN_JS
        assert "p.permission_label || p.permission" in ADMIN_JS


# ===========================================================================
# IK-0867 / IK-0868
# ===========================================================================


class TestLibraryEditingSurfaces:
    def test_aliases_field_removed_from_detail_form(self):
        assert 'id="lib-detail-aliases"' not in ADMIN_JS
        seg = ADMIN_JS[ADMIN_JS.index("function saveLibraryEntryEdits"):]
        seg = seg[: seg.index("\n  }\n")]
        assert "aliases:" not in seg

    def test_relation_counterpart_is_searched_by_name(self):
        assert "相手のエントリ ID" not in ADMIN_JS
        assert "function searchLibraryRelationCounterparts" in ADMIN_JS
        assert 'id="library-relation-query"' in ADMIN_JS

    def test_search_not_found_fact(self):
        from api.routes import library as library_route

        with patch.object(library_route.library_store, "list_entries", return_value=[]):
            out = library_route.list_entries(q="状態方程式", current_user={"id": "u1"})
        assert out["facts"] == [library_route.LIBRARY_SEARCH_NOT_FOUND.format(query="状態方程式")]


# ===========================================================================
# IK-0869 / IK-0870
# ===========================================================================


from tests.test_symbol_lookup_core import FakeSession, _chunks, _documents  # noqa: E402


class TestSymbolLookupDefinitions:
    def _symbol(self, **over):
        row = {
            "document_id": DOC,
            "agent_symbol_id": "sym_F",
            "canonical_symbol": "F_{EOS}",
            "notation_variants": [],
            "kind": "function",
            "unit": "",
            "scope": "document",
            "definition_status": "defined",
            "defining_equation_ids": ["eq_eos"],
            "source_evidence_ids": [],
            "definition_evidence_texts": [],
        }
        row.update(over)
        return row

    def test_equation_only_definition_is_not_dropped(self):
        from core.symbol_lookup import FACT_NO_DEFINITION, lookup_symbol_definition

        session = FakeSession(
            symbols=[self._symbol()],
            equations=[{"document_id": DOC, "agent_equation_id": "eq_eos", "block_id": "b2",
                        "page": 1, "label": "3", "latex": r"F_{EOS} = \frac{P}{\rho}"}],
            chunks=_chunks(),
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="F_{EOS}", document_ids=[DOC], equation_id="eq_eos")
        assert result["definition"]["text"] == "式 (3)で定義されています。"
        assert result["definition"]["latex"].startswith("F_{EOS}")
        assert FACT_NO_DEFINITION not in result["facts"]

    def test_registered_without_definition_uses_formula_fallback(self):
        from core.symbol_lookup import FACT_NO_DEFINITION, lookup_symbol_definition

        chunks = _chunks()
        chunks[0]["formulas"] = [{"latex": r"r = \frac{a}{b}", "label": ""}]
        chunks[1]["formulas"] = []
        session = FakeSession(
            symbols=[self._symbol(canonical_symbol="r", defining_equation_ids=[])],
            chunks=chunks,
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="r", document_ids=[DOC])
        assert FACT_NO_DEFINITION not in result["facts"]
        assert any("左辺に現れます" in f and "定義の文は見つかりませんでした" in f for f in result["facts"])
        assert result["equations"] and result["equations"][0]["name"] == "番号の無い式"

    def test_fallback_note_only_mentions_pdf_when_known_pdf(self):
        from core import symbol_lookup

        class _S:
            def __init__(self, kind):
                self.kind = kind

            def execute(self, *a, **k):
                kind = self.kind

                class _R:
                    def mappings(self):
                        return self

                    def fetchall(self):
                        return [{"id": DOC, "source_kind": kind, "source_path": ""}]

                return _R()

        assert symbol_lookup._fallback_note(_S("pdf"), DOC) == symbol_lookup.FACT_FORMULA_FALLBACK_NOTE
        assert symbol_lookup._fallback_note(_S("tex_archive"), DOC) == symbol_lookup.FACT_FORMULA_FALLBACK_NOTE_TEX
        assert "PDF" not in symbol_lookup.FACT_FORMULA_FALLBACK_NOTE_TEX

    def test_other_documents_fact_warns_meaning_may_differ(self):
        from core import symbol_lookup

        assert "同じ字形" in symbol_lookup.FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS
        assert "意味は論文ごとに異なることがあります" in symbol_lookup.FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS

    def test_popover_renders_definition_latex_and_equations(self):
        seg = APP_JS[APP_JS.index("function renderSymbolLookupPopover"):]
        seg = seg[: seg.index("\n  }\n")]
        assert "definition.latex" in seg
        assert "data.equations" in seg


# ===========================================================================
# IK-0871
# ===========================================================================


class TestThesisLaneWording:
    def test_relation_label_has_no_counterpart_type(self):
        from core.deliberation import context_lens

        assert context_lens.RELATION_LABELS["supports_thesis"] == "を支持する"

    def test_central_thesis_label_names_the_content(self):
        from core.deliberation import context_lens

        label = context_lens._central_thesis_label({"central_thesis": {"text": "中性子星の半径は状態方程式で決まる"}})
        assert label.startswith("中心命題「") and "状態方程式" in label
        # 第 2 周（IK-0871 の残り）: 本文も問いも無いときも種類名だけにしない。
        assert context_lens._central_thesis_label({}) == context_lens.CENTRAL_THESIS_WITHOUT_TEXT_LABEL
        assert context_lens._central_thesis_label({"headline_claim": "見出し"}) == "見出し"


class TestSymbolPowerNotMergedIntoSubscript:
    """IK-0869（ハーネス是正者の所見）: 画面が上付きを添字に結合して送っても当たる。"""

    def test_fallback_keys(self):
        from core.symbol_lookup import symbol_key_fallbacks

        assert symbol_key_fallbacks("r_2") == ["r"]
        assert symbol_key_fallbacks("c_s2") == ["c_s"]
        assert symbol_key_fallbacks("c_s^2") == ["c_s"]
        assert symbol_key_fallbacks("V_{cb}") == []

    def test_exact_match_wins_then_fallback(self):
        from core.symbol_lookup import lookup_symbol_definition

        session = FakeSession(
            symbols=[{
                "document_id": DOC, "agent_symbol_id": "sym_r", "canonical_symbol": "r",
                "notation_variants": [], "kind": "variable", "unit": "", "scope": "document",
                "definition_status": "defined", "defining_equation_ids": [], "source_evidence_ids": [],
                "definition_evidence_texts": ["r is the radial coordinate."],
            }],
            chunks=_chunks(),
            documents=_documents(),
        )
        result = lookup_symbol_definition(session, symbol="r_2", document_ids=[DOC])
        assert "radial coordinate" in (result.get("definition") or {}).get("text", "")

    def test_client_drops_superscript_rows(self):
        seg = APP_JS[APP_JS.index("function symbolTextFromToken"):]
        seg = seg[: seg.index("\n  }\n")]
        assert "top > -2.8" in seg
