"""Tests for 承認・共有レイヤー(C層) — migration 021 endorsement & sharing.

A層(生成パイプライン)には手を入れず、その上に承認・共有を積む層のテスト。
既存テストと同様、SQL/ソースの静的検証 + 純粋関数のユニットテストで構成する。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from tests.guardrail_helpers import assert_module_tree_forbids, extract_function_source  # noqa: E402

MIGRATION = ROOT / "backend" / "db" / "021_endorsement_sharing.sql"
ROUTES = ROOT / "backend" / "api" / "routes" / "theory_components.py"
LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
CANDIDATES = ROOT / "backend" / "core" / "component_candidates.py"
# 原稿スタジオ (Lecture Script Studio) は Tier 3-17b で admin.js から分離済み。
# lsOpenEndorsementModal はこちらに存在する。
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
AGENTS = ROOT / "src" / "episteme_graph" / "agents"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestMigration021:
    def test_reference_sql_defines_tables_and_view(self):
        """正本は backend/db/021_endorsement_sharing.sql（main.py のインライン DDL ではない）。"""
        sql = _read(MIGRATION)
        assert "CREATE TABLE IF NOT EXISTS component_explanations" in sql
        assert "CREATE TABLE IF NOT EXISTS component_endorsements" in sql
        assert "CREATE TABLE IF NOT EXISTS component_citations" in sql
        assert "CREATE OR REPLACE VIEW component_explanation_endorsement_summary" in sql

    def test_endorsement_is_explanation_scoped(self):
        """承認は説明バージョン単位(explanation_id への FK)。"""
        sql = _read(MIGRATION)
        assert "explanation_id UUID NOT NULL REFERENCES component_explanations(id)" in sql
        # 二重カウント防止の一意制約
        assert "UNIQUE(explanation_id, endorser_id)" in sql
        # revoke は履歴を残す(行削除しない)
        assert "revoked        BOOLEAN NOT NULL DEFAULT FALSE" in sql

    def test_standard_explanation_is_unique_per_component(self):
        sql = _read(MIGRATION)
        assert "uq_component_explanations_standard" in sql
        assert "WHERE kind = 'standard'" in sql


class TestRoutes:
    def test_endorsement_and_sharing_routes_exist(self):
        source = _read(ROUTES)
        assert '"/theory-components/{component_id}/explanations"' in source
        assert '"/explanations/{explanation_id}"' in source
        assert '"/explanations/{explanation_id}/endorse"' in source
        assert '"/explanations/{explanation_id}/endorsements"' in source
        assert '"/explanations/{explanation_id}/cite"' in source
        assert '"/theory-components/candidates/from-query"' in source
        assert '"/courses/{course_id}/sharing-dashboard"' in source

    def test_routes_require_teacher(self):
        source = _read(ROUTES)
        # C層エンドポイントは _require_teacher 依存を使う
        assert source.count("Depends(_require_teacher)") >= 10

    def test_audit_entity_types_extended(self):
        """承認・共有の state 変更は theory_review_events に監査記録を残す。

        entity_type は core/schema.py の AUDIT_ENTITY_* カタログ定数を使う
        （entity_type を 'endorsement' / 'explanation' / 'citation' に拡張, 提案7）。
        """
        from core.schema import AUDIT_ENTITY_CITATION, AUDIT_ENTITY_ENDORSEMENT, AUDIT_ENTITY_EXPLANATION

        assert (AUDIT_ENTITY_ENDORSEMENT, AUDIT_ENTITY_EXPLANATION, AUDIT_ENTITY_CITATION) == (
            "endorsement", "explanation", "citation",
        )
        source = _read(ROUTES)
        assert "_record_review_event(" in source
        for entity in ("AUDIT_ENTITY_ENDORSEMENT", "AUDIT_ENTITY_EXPLANATION", "AUDIT_ENTITY_CITATION"):
            assert entity in source

    def test_claim_linking_stays_candidate_until_teacher_confirms(self):
        source = _read(ROUTES)
        # AI 候補の claim 紐づけは confirmed=False(教員が確定するまで候補)
        assert '"confirmed": False' in source

    def test_learner_explanations_endpoint_returns_only_approved(self):
        source = _read(LEARNING)
        assert '"/courses/{course_id}/components/{component_id}/explanations"' in source
        assert "review_status = 'teacher_approved'" in source


class TestEndorsementLabel:
    def _label(self, **summary):
        import routes.theory_components as tc
        return tc._endorsement_label(summary)

    def test_zero_is_unendorsed(self):
        assert self._label(endorser_count=0) == "未承認"

    def test_provisional_only(self):
        assert self._label(endorser_count=1, provisional_count=1) == "暫定的に1名が承認"

    def test_strong_and_breadth_are_reflected(self):
        label = self._label(endorser_count=3, strong_count=1, provisional_count=0, expertise_breadth=2)
        assert "3名" in label
        assert "強い支持" in label
        assert "専門2分野" in label

    def test_label_is_not_a_numeric_score(self):
        """承認の重みは学習者への評価点にしない(段階ラベルのみ)。"""
        label = self._label(endorser_count=2, strong_count=0, provisional_count=0, expertise_breadth=1)
        assert label.strip() not in {"1.0", "2.0", "2", "100"}
        assert "名" in label


class TestCandidateGeneration:
    def test_sanitizes_component_type_and_claim_ids(self, monkeypatch):
        import core.component_candidates as cc

        fake = cc.CandidateGenerationResult(
            components=[
                cc.CandidateComponent(
                    name="Something",
                    component_type="not_a_real_type",
                    summary="s",
                    backing_claims=[
                        cc.CandidateClaimLink(claim_id="valid-1", reason="r", confidence=0.9),
                        cc.CandidateClaimLink(claim_id="hallucinated", reason="r", confidence=0.9),
                    ],
                ),
                cc.CandidateComponent(name="", component_type="concept"),  # 名前なしは捨てる
            ]
        )
        monkeypatch.setattr(cc, "generate_text_with_structured_output", lambda **kwargs: fake)

        result = cc.generate_component_candidates(
            "q", "a", existing_claims=[{"id": "valid-1", "text": "claim text"}]
        )
        assert len(result.components) == 1
        comp = result.components[0]
        # 未知の component_type は 'concept' に正規化される
        assert comp.component_type == "concept"
        # 存在しない claim_id は除去される
        assert [b.claim_id for b in comp.backing_claims] == ["valid-1"]

    def test_llm_failure_raises_candidate_generation_error(self, monkeypatch):
        """LLM 呼び出し自体の失敗は「0件」と区別するため専用例外を送出する(§4 B2)。

        真の0件(LLM 正常応答・候補なし)は引き続き空配列を返す
        (backend/tests/test_component_candidates_failure.py 参照)。
        """
        import core.component_candidates as cc

        def _boom(**kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(cc, "generate_text_with_structured_output", _boom)
        with pytest.raises(cc.CandidateGenerationError):
            cc.generate_component_candidates("q", "a", existing_claims=[])


class TestFrontend:
    def test_admin_js_has_endorsement_modal(self):
        source = _read(ADMIN_LS_JS)
        assert "lsOpenEndorsementModal" in source
        assert 'data-theory-action="endorse"' in source
        assert "/admin/theory-components/candidates/from-query" in source
        assert "/admin/explanations/" in source

    def test_app_js_shows_explanation_versions(self):
        source = _read(APP_JS)
        assert "showComponentExplanations" in source
        assert "/components/" in source


def _js_fn(src: str, name: str, next_name: str) -> str:
    """admin-lecture-studio.js から `function name` 〜 `function next_name` 直前を切り出す
    （test_deliberation_ui_static.py 等と同じ簡易抽出方式）。"""
    start = src.index("function " + name)
    end = src.index("function " + next_name, start)
    return src[start:end]


class TestClaimLinkConfirmationUI:
    """N3: claim 紐づけの確定/却下操作。

    「claim 紐づけの最終確定は必ず教員」(設計原則2) の実行手段。AI 候補
    (confirmed=false) を教員が確定/却下でき、却下は行削除ではなく status 保持(P4)。
    """

    def test_confirm_reject_reset_buttons_rendered(self):
        src = _read(ADMIN_LS_JS)
        assert 'data-claim-action="confirm"' in src
        assert 'data-claim-action="reject"' in src
        # 確定・却下とも可逆（候補に戻す）
        assert 'data-claim-action="reset"' in src

    def test_state_change_goes_through_patch_endpoint(self):
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsSetBackingClaimState", "lsEndorseLevelLabel")
        assert '"/admin/explanations/" + expId' in body
        assert '"PATCH"' in body
        assert "backing_claims" in body

    def test_reject_preserves_entry_p4(self):
        """却下は status='rejected' で保持し、配列から要素を除去しない（P4）。"""
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsSetBackingClaimState", "lsEndorseLevelLabel")
        assert '"rejected"' in body
        assert ".splice(" not in body
        assert ".filter(" not in body
        assert ".map(" in body

    def test_backing_claim_state_vocabulary(self):
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsBackingClaimState", "lsRenderBackingClaims")
        for st in ('"confirmed"', '"rejected"', '"candidate"'):
            assert st in body

    def test_backend_audits_backing_claims_update(self):
        """PATCH /explanations/{id} は backing_claims 変更を theory_review_events に監査記録する
        （設計原則3: 状態変更は監査）。"""
        source = _read(ROUTES)
        body = extract_function_source(source, "patch_explanation")
        assert "backing_claims_update" in body
        assert "_record_review_event(" in body


class TestEndorserListUI:
    """N30: 承認者個別一覧（名前 + 専門タグ + 段階ラベル。数値スコアは出さない）。"""

    def test_endorsers_details_block_rendered(self):
        src = _read(ADMIN_LS_JS)
        assert "data-exp-endorsers" in src
        assert "承認者一覧" in src

    def test_fetches_individual_endorsements_api(self):
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsLoadEndorsers", "lsOpenCiteSelector")
        assert '"/admin/explanations/" + expId + "/endorsements"' in body

    def test_shows_stage_labels_not_numeric_scores(self):
        src = _read(ADMIN_LS_JS)
        label_fn = _js_fn(src, "lsEndorseLevelLabel", "lsLoadEndorsers")
        for label in ("強い承認", "暫定", "承認"):
            assert label in label_fn
        # 個別一覧の描画は集計の数値フィールドを表示しない（段階ラベルのみ）
        body = _js_fn(src, "lsLoadEndorsers", "lsOpenCiteSelector")
        for numeric_field in ("endorser_count", "strong_count", "provisional_count", "expertise_breadth"):
            assert numeric_field not in body

    def test_endorser_rendering_is_escaped(self):
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsLoadEndorsers", "lsOpenCiteSelector")
        assert "escHtml(name" in body
        assert "escHtml(tag" in body


class TestCiteCourseSelector:
    """N35: 引用操作は生のコースID手入力（window.prompt）ではなくコース選択にする。"""

    def test_no_window_prompt_left(self):
        src = _read(ADMIN_LS_JS)
        assert "window.prompt(" not in src

    def test_courses_are_loaded_from_admin_courses_api(self):
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsOpenCiteSelector", "lsEndorseExplanation")
        assert '"/admin/courses"' in body
        assert "data-cite-select" in body

    def test_options_limited_to_editable_courses(self):
        """引用API(_ensure_editable)が編集権限を要求するため owner/editor に絞る。"""
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsOpenCiteSelector", "lsEndorseExplanation")
        assert '"owner"' in body
        assert '"editor"' in body

    def test_source_course_excluded(self):
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsOpenCiteSelector", "lsEndorseExplanation")
        assert "component.course_id" in body

    def test_cite_call_still_uses_cite_endpoint(self):
        src = _read(ADMIN_LS_JS)
        body = _js_fn(src, "lsOpenCiteSelector", "lsEndorseExplanation")
        assert '"/cite"' in body


class TestES5ComplianceCLayerUI:
    """開発ルール5: admin 系 JS は ES5。C層 UI 領域の追加分をリグレッションガードする。"""

    def test_c_layer_region_is_es5(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("=== 承認・共有レイヤー(C層) UI")
        end = src.index("function lsInsertTheoryChip", start)
        region = src[start:end]
        assert "=>" not in region
        assert re.search(r"\b(const|let)\s", region) is None
        assert "`" not in region


class TestALayerUntouched:
    """A層(生成パイプライン)に C層 の差分が入っていないこと。"""

    def test_agents_have_no_endorsement_or_citation_code(self):
        if not AGENTS.exists():
            pytest.skip("agents package not present in this checkout")
        assert_module_tree_forbids(AGENTS, ["component_endorsements", "component_citations"])
