"""第 25 周・面 A（共有・転用の通し）第 2 周の是正（IK-0890〜IK-0898 + IK-0866 / IK-0871 の残り）。

DB へ行かない単体・静的検査で、是正の振る舞いを固定する:

- IK-0890 共通部品の検索の分野は段順の 1 関数（run → コース → 配置）・複数 / 未定は選ばせる
- IK-0891 説明の course_id は出所（NULL 許容・FK は ON DELETE SET NULL）・失敗は事実文
- IK-0892 学生の説明は部品の文脈と同じ焦点論文で解決・曖昧は「特定できない」
- IK-0893 記号の照会の第 3 の供給源（主張本文の言及）・左辺の定義の形
- IK-0894 解決できない式も式本文を同梱して数式で描く
- IK-0895 復元の印の判定の正本（原文の LaTeX と一致する復元は印なし）
- IK-0896 旅の出口案内を帰属の種類で分ける
- IK-0897 ラベル・関係の追加が結果（新規 / 既存 / 見送り済み）を言う
- IK-0898 見出しが $…$ の数式区間を保つ
- IK-0866 導出の確認文に分野の英字キーを出さない / IK-0871 本文の無い中心命題の項目
"""

from __future__ import annotations

import inspect
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

from tests.test_symbol_lookup_core import FakeSession, _Result, _chunks, _documents  # noqa: E402

APP_JS = (REPO / "frontend/public/js/app.js").read_text(encoding="utf-8")
ADMIN_JS = (REPO / "frontend/public/js/admin.js").read_text(encoding="utf-8")
STUDIO_JS = (REPO / "frontend/public/js/admin-lecture-studio.js").read_text(encoding="utf-8")
DELIB_JS = (REPO / "frontend/public/js/deliberation.js").read_text(encoding="utf-8")
CARD_JS = (REPO / "frontend/public/js/element-card.js").read_text(encoding="utf-8")
MIGRATION = (BACKEND / "db" / "091_component_explanations_course_optional.sql").read_text(encoding="utf-8")

DOC = "11111111-1111-4111-8111-111111111111"
COMP = "33333333-3333-4333-8333-333333333333"


# ===========================================================================
# IK-0890
# ===========================================================================


class TestDocumentDomainResolution:
    def test_run_cartridge_wins(self):
        from core.deliberation import dialogue

        with patch.object(dialogue, "document_domain_key", return_value="astrophysics"):
            keys, source = dialogue.resolve_document_domains(DOC)
        assert keys == ["astrophysics"]
        assert source == dialogue.DOMAIN_SOURCE_RUN

    def test_course_domains_when_run_has_none(self):
        from core.deliberation import dialogue
        from core.paper_discovery import corpus

        class _S:
            def close(self):
                pass

        with patch.object(dialogue, "document_domain_key", return_value=""), patch.object(
            dialogue, "get_session", return_value=_S()
        ), patch.object(corpus, "document_domain_keys", return_value=["astrophysics", "gravity"]):
            keys, source = dialogue.resolve_document_domains(DOC)
        assert keys == ["astrophysics", "gravity"]
        assert source == dialogue.DOMAIN_SOURCE_COURSE

    def test_identity_candidates_use_the_same_resolver(self):
        from core.deliberation import dialogue

        source = inspect.getsource(dialogue.collect_identity_candidates)
        assert "resolve_document_domains(" in source

    def test_route_asks_to_choose_instead_of_internal_word(self):
        from api.routes import deliberation as route

        source = inspect.getsource(route.list_shared_part_candidates_for_element)
        assert "resolve_document_domains(" in source
        assert "カートリッジ" not in source
        assert "カートリッジ" not in route.SHARED_PART_DOMAIN_NOTE_UNDECIDED
        assert "分野を選ぶ" in route.SHARED_PART_DOMAIN_NOTE_UNDECIDED

    def test_frontend_renders_domain_select_and_sends_choice(self):
        assert "data-identity-domain-select" in DELIB_JS
        assert '"domain_key=" + encodeURIComponent(selectedDomain)' in DELIB_JS
        assert "d.domain_label" in DELIB_JS


# ===========================================================================
# IK-0891
# ===========================================================================


class TestExplanationCourseOptional:
    def test_migration_drops_not_null_and_sets_null_on_delete(self):
        assert "ALTER COLUMN course_id DROP NOT NULL" in MIGRATION
        assert "ON DELETE SET NULL" in MIGRATION
        assert "DELETE FROM" not in MIGRATION.upper().replace("ON DELETE", "")

    def test_create_and_standard_insert_null_not_empty_string(self):
        from api.routes import theory_components as tc

        create_src = inspect.getsource(tc.create_component_explanation)
        assert "authored_course_id.strip() or None" in create_src
        std_src = inspect.getsource(tc._ensure_standard_explanation)
        assert 'str(component.course_id or "").strip() or None' in std_src
        assert "return False" in std_src

    def test_failure_messages_are_japanese_facts(self):
        from api.routes import theory_components as tc

        assert 'detail="Failed to create explanation"' not in inspect.getsource(tc.create_component_explanation)
        assert tc.EXPLANATION_CREATE_FAILED_FACT.startswith("説明を保存できませんでした")
        assert "X-Explanation-Fact" in inspect.getsource(tc.list_component_explanations)

    def test_annotation_commit_accepts_pipeline_component(self):
        from core.deliberation import annotations

        source = inspect.getsource(annotations._commit_interpretation)
        assert "コースに紐づいていないため" not in source

    def test_studio_renders_detail_and_fact_header(self):
        assert "function lsResponseJsonOrDetail(res)" in STUDIO_JS
        assert 'res.headers.get("X-Explanation-Fact")' in STUDIO_JS
        assert '.then(lsResponseJsonOrDetail)' in STUDIO_JS


# ===========================================================================
# IK-0892
# ===========================================================================


class TestLearnerExplanationFocus:
    def _call(self, *, resolved, ambiguous=False, **kwargs):
        from api.routes import learning as learning_mod

        class _Session:
            def execute(self, *a, **k):
                class _R:
                    def fetchall(self_inner):
                        return []

                return _R()

            def close(self):
                pass

        with patch.object(learning_mod, "get_accessible_course_data", return_value={"sources": []}), patch.object(
            learning_mod, "_course_document_ids", return_value=[DOC]
        ), patch.object(learning_mod, "resolve_course_component_id", return_value=resolved) as resolver, patch.object(
            learning_mod, "component_matches_multiple_documents", return_value=ambiguous
        ), patch.object(learning_mod, "_pg_session", return_value=_Session()), patch.object(
            learning_mod, "_learner_focus"
        ) as focus:
            focus.return_value.document_ids = (DOC,)
            result = learning_mod.get_component_explanations_for_learner(
                "c1", "comp_001", {"id": "s1"}, **kwargs
            )
        return result, resolver, focus

    def test_topic_focus_is_passed_to_resolver(self):
        _result, resolver, focus = self._call(resolved=COMP, topic_id="t1")
        assert focus.called
        assert resolver.call_args.kwargs["preferred_document_ids"] == (DOC,)

    def test_ambiguous_is_not_reported_as_absent(self):
        from api.routes import learning as learning_mod

        with pytest.raises(HTTPException) as exc:
            self._call(resolved=None, ambiguous=True, topic_id="t1")
        assert exc.value.detail == learning_mod.COMPONENT_EXPLANATIONS_AMBIGUOUS
        assert "見つかりませんでした" not in learning_mod.COMPONENT_EXPLANATIONS_AMBIGUOUS

    def test_app_js_sends_focus_suffix(self):
        assert '"/explanations" +\n        // IK-0892' in APP_JS
        assert 'focusQuerySuffix("?"));' in APP_JS


# ===========================================================================
# IK-0893
# ===========================================================================


class _ClaimSession(FakeSession):
    def __init__(self, *, claims=(), **kwargs):
        super().__init__(**kwargs)
        self.claims = [dict(c) for c in claims]

    def execute(self, statement, params=None):
        sql = str(statement)
        if "theory_claims_live" in sql:
            docs = set((params or {}).get("doc_ids") or [])
            return _Result([c for c in self.claims if c["document_id"] in docs])
        return super().execute(statement, params)

    def rollback(self):
        pass


class TestSymbolClaimMentions:
    def test_lhs_definition_form(self):
        from core.symbol_lookup import _lhs_is_definition_form

        assert _lhs_is_definition_form("P")
        assert _lhs_is_definition_form(r"P(\varepsilon)")
        assert not _lhs_is_definition_form("P(R_*)")
        assert not _lhs_is_definition_form(r"P(\rho_\ast)")

    def test_boundary_value_is_not_a_definition(self):
        from core.symbol_lookup import lookup_symbol_definition

        chunks = _chunks()
        chunks[0]["formulas"] = [{"latex": "P(R_*) = 0", "label": ""}]
        chunks[1]["formulas"] = []
        session = _ClaimSession(chunks=chunks, documents=_documents())
        result = lookup_symbol_definition(session, symbol="P", document_ids=[DOC])
        assert not any("左辺に現れます" in f for f in result.get("facts") or [])

    def test_unregistered_symbol_quotes_claim_usage(self):
        from core.symbol_lookup import FACT_CLAIM_MENTIONS, lookup_symbol_definition

        session = _ClaimSession(
            chunks=_chunks(),
            documents=_documents(),
            claims=[
                {"document_id": DOC, "text": "A neural network represents the pressure P as a function of energy density."},
                {"document_id": DOC, "text": "Pressure appears in the TOV equations."},
            ],
        )
        result = lookup_symbol_definition(session, symbol="P", document_ids=[DOC])
        assert result["available"] is True
        assert FACT_CLAIM_MENTIONS in result["facts"]
        assert result["definition"] is None
        assert [m["text"] for m in result["mentions"]] == [
            "A neural network represents the pressure P as a function of energy density."
        ]

    def test_popover_renders_mentions_with_math(self):
        assert "(data && data.mentions)" in APP_JS
        assert "renderTextWithInlineMath(mention.text)" in APP_JS


# ===========================================================================
# IK-0894 / IK-0895
# ===========================================================================


class TestEquationLatexAndReconstruction:
    def test_component_equations_carry_latex(self):
        from core import component_context

        record = {"equation_id": "eq_a", "source_extraction": {"latex": r"P = \frac{a}{b}"}}
        items = component_context._build_equations(
            {"linked_equation_ids": ["eq_a"]}, {"eq_a": record}
        )
        assert items[0]["latex"] == r"P = \frac{a}{b}"

    def test_unresolved_branch_renders_math(self):
        assert "eq.latex ? renderMaterialKatex(eq.latex, true)" in APP_JS
        assert 'renderTextWithInlineMath(eq.label || "")' in APP_JS

    def test_reconstruction_matching_source_is_not_marked(self):
        from core.learner_context_common import equation_latex_is_reconstructed

        same = {
            "source_extraction": {"latex": r"P = \frac{a}{b}"},
            "reconstruction": {"status": "reconstructed", "latex": r"P=\frac{a}{b}"},
        }
        differs = {
            "source_extraction": {"latex": r"P = a / b"},
            "reconstruction": {"status": "reconstructed", "latex": r"P=\frac{a}{b}"},
        }
        assert equation_latex_is_reconstructed(same) is False
        assert equation_latex_is_reconstructed(differs) is True
        assert equation_latex_is_reconstructed({"reconstructed": True}) is True

    def test_course_builder_delegates_to_the_canonical_judgment(self):
        from core import course_content_builder as ccb

        assert "equation_latex_is_reconstructed" in inspect.getsource(ccb._equation_is_reconstructed)


# ===========================================================================
# IK-0896
# ===========================================================================


class TestSharpenFactByAnchor:
    def test_selected_text_anchor_does_not_suggest_selecting_again(self):
        from core.personal_graph import nearby
        from core.personal_graph.schema import PersonalAnchor, PersonalNode

        node = PersonalNode(
            id="n1", node_kind="question", label="q",
            anchor=PersonalAnchor(anchor_type="segment", anchor_id="seg_3"),
            topic_id="t1", created_at="", facts=[], source={},
        )
        fact = nearby.sharpen_fact_for(node)
        assert fact == nearby.FACT_SELECTED_SHARPEN
        assert "テキストを選んで質問" not in fact
        assert nearby.sharpen_fact_for(None) == nearby.FACT_RANGE_SHARPEN


# ===========================================================================
# IK-0897
# ===========================================================================


class _RegistrySession:
    def __init__(self, prior_status):
        self.prior_status = prior_status

    def execute(self, statement, params=None):
        sql = str(statement)

        class _R:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        if "SELECT status FROM library_entries" in sql:
            return _R(("active",))
        if "SELECT status FROM library_entry_labels" in sql:
            return _R((self.prior_status,) if self.prior_status else None)
        if "INSERT INTO library_entry_labels" in sql:
            return _R(None if self.prior_status == "dismissed" else "row")
        return _R("row")


class TestLabelOutcome:
    @pytest.mark.parametrize(
        "prior, outcome", [("", "created"), ("confirmed", "existing"), ("dismissed", "dismissed")]
    )
    def test_add_label_reports_outcome(self, prior, outcome):
        from core.library import registry

        label_row = {"id": "l1", "status": prior or "confirmed"}
        with patch.object(registry, "_label_row_to_dict", return_value=dict(label_row)):
            result = registry.add_label(
                "44444444-4444-4444-8444-444444444444", kind="alternate", label="TOV map",
                actor_id="55555555-5555-4555-8555-555555555555", session=_RegistrySession(prior),
            )
        assert result["outcome"] == outcome

    def test_route_facts_cover_every_outcome(self):
        from api.routes import library

        for kind in ("alternate", "hidden"):
            for outcome in ("created", "existing", "dismissed"):
                assert library.LABEL_OUTCOME_FACTS[(kind, outcome)]
        assert set(library.RELATION_OUTCOME_FACTS) == {"created", "existing", "dismissed"}

    def test_admin_js_renders_outcome_facts(self):
        assert '_libraryShowOutcomeFacts("library-labels-list", data && data.facts)' in ADMIN_JS
        assert '_libraryShowOutcomeFacts("library-relations-list", data && data.facts)' in ADMIN_JS


# ===========================================================================
# IK-0898 / IK-0866 / IK-0871
# ===========================================================================


class TestLensLabelsKeepMath:
    def test_synthesized_claim_is_japanese_with_math(self):
        from core.deliberation import labels

        label = labels.claim_label({"text": r"An equation of this paper defines $\mathcal{N}_\mathrm{TOV}$."})
        assert label.text == r"この論文の式が $\mathcal{N}_\mathrm{TOV}$ を定めます"
        label = labels.claim_label({"text": "In equation (12), $P$ depends on $\\varepsilon$, $r$."})
        assert label.text == "式 (12)では $P$ が $\\varepsilon$、$r$ に依存します"

    def test_prose_with_math_is_not_dropped_for_learners(self):
        from core.learner_context_common import project_item

        item = project_item(
            {"element_type": "theory_claim", "element_id": COMP,
             "label": r"この論文の式が $\mathcal{N}_\mathrm{TOV}$ を定めます"}
        )
        assert r"$\mathcal{N}_\mathrm{TOV}$" in item["label"]

    def test_text_excerpt_detector(self):
        from core.text_excerpt import is_prose_with_delimited_math

        assert is_prose_with_delimited_math(r"defines $\mathcal{N}$ here")
        assert not is_prose_with_delimited_math(r"\mathcal{N}_\mathrm{TOV} = \frac{a}{b}")

    def test_card_renders_delimited_math(self):
        assert "function textWithDelimitedMathHtml(ctx, text)" in CARD_JS
        assert "textWithDelimitedMathHtml(ctx, label)" in CARD_JS

    def test_derive_confirm_uses_domain_label(self):
        assert '"分野「" + domainKey + "」' not in ADMIN_JS
        assert "_libraryTabState.domainLabels" in ADMIN_JS

    def test_central_thesis_without_text_is_not_bare_kind_name(self):
        from core.deliberation import context_lens

        assert context_lens._central_thesis_label({}) == context_lens.CENTRAL_THESIS_WITHOUT_TEXT_LABEL
        assert context_lens._central_thesis_label({"central_question": "中性子星の状態方程式は何か"}).startswith(
            "問い「"
        )
        assert context_lens._central_thesis_label({}) != "中心命題"
