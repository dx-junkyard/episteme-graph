"""Tests for 承認・共有レイヤー(C層) — migration 021 endorsement & sharing.

A層(生成パイプライン)には手を入れず、その上に承認・共有を積む層のテスト。
既存テストと同様、SQL/ソースの静的検証 + 純粋関数のユニットテストで構成する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

MAIN = ROOT / "backend" / "api" / "main.py"
MIGRATION = ROOT / "backend" / "db" / "021_endorsement_sharing.sql"
ROUTES = ROOT / "backend" / "api" / "routes" / "theory_components.py"
LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
CANDIDATES = ROOT / "backend" / "core" / "component_candidates.py"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
AGENTS = ROOT / "src" / "episteme_graph" / "agents"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestMigration021:
    def test_reference_sql_defines_tables_and_view(self):
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

    def test_main_applies_migration_021(self):
        source = _read(MAIN)
        assert "Migration 021" in source
        assert "component_explanations" in source
        assert "component_endorsements" in source
        assert "component_citations" in source
        assert "Migrations (002-" in source  # 以降の migration 追加 (023...) でも壊れないよう prefix で確認


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
        source = _read(ROUTES)
        # 承認・共有の state 変更は theory_review_events に監査記録を残す
        # (entity_type を 'endorsement' / 'explanation' / 'citation' に拡張)。
        assert "_record_review_event(" in source
        for entity in ('"endorsement"', '"explanation"', '"citation"'):
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

    def test_llm_failure_returns_empty(self, monkeypatch):
        import core.component_candidates as cc

        def _boom(**kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(cc, "generate_text_with_structured_output", _boom)
        result = cc.generate_component_candidates("q", "a", existing_claims=[])
        assert result.components == []


class TestFrontend:
    def test_admin_js_has_endorsement_modal(self):
        source = _read(ADMIN_JS)
        assert "lsOpenEndorsementModal" in source
        assert 'data-theory-action="endorse"' in source
        assert "/admin/theory-components/candidates/from-query" in source
        assert "/admin/explanations/" in source

    def test_app_js_shows_explanation_versions(self):
        source = _read(APP_JS)
        assert "showComponentExplanations" in source
        assert "/components/" in source


class TestALayerUntouched:
    """A層(生成パイプライン)に C層 の差分が入っていないこと。"""

    def test_agents_have_no_endorsement_or_citation_code(self):
        if not AGENTS.exists():
            pytest.skip("agents package not present in this checkout")
        offenders = []
        for path in AGENTS.rglob("*.py"):
            text = _read(path)
            if "component_endorsements" in text or "component_citations" in text:
                offenders.append(str(path))
        assert not offenders, f"A層に C層 の差分が混入: {offenders}"
