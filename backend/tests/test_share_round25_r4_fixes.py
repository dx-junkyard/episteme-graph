"""第 25 周・面 A 第 4 周の是正（IK-0953 / 0955 / 0956 / 0957 / 0958 / 0960 / 0961 / 0917 / 0924 / 0925）。

- IK-0953: 確定した同一性リンクを理由必須で取り消す（confirmed → revoked・行は残す・監査）。
  作成者・確定者・取り消した人を表示名で返し、作成者 = 確定者なら事実文。
- IK-0917: 種別が違う対には一段強い確認の事実文と、両側を名指しする問い。
- IK-0956: 同じ題名の論文の版の手がかり（形式とファイル名）。
- IK-0957: 候補づくりの規則の版を artifact に残し、空状態が「まだ探していない」を言い分ける。
- IK-0958: Copilot の生リンクを文字に落とし、学生に見えるか・取り消しの節を引ける。
- IK-0960 / IK-0925: 標準の説明の根拠と、本人の有効な支持のときだけの［支持を取り消す］。
- IK-0961: 記号の吹き出しの学生向けの言い方。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import identity_links  # noqa: E402

JS = ROOT / "frontend" / "public" / "js"
_LINK = "55555555-5555-5555-5555-555555555555"
_DOC = "11111111-1111-1111-1111-111111111111"
_USER_A = "22222222-2222-2222-2222-222222222222"
_USER_B = "66666666-6666-6666-6666-666666666666"


# ── IK-0953 ─────────────────────────────────────────────────────────────────


class TestRevokeCore:
    def test_revoke_requires_actor_and_reason(self):
        with pytest.raises(ValueError):
            identity_links.revoke(_LINK, revoked_by="", reason="x")
        with pytest.raises(ValueError):
            identity_links.revoke(_LINK, revoked_by=_USER_A, reason="   ")

    def test_revoke_sql_only_from_confirmed_and_keeps_decider(self):
        src = (BACKEND / "core" / "deliberation" / "identity_links.py").read_text(encoding="utf-8")
        start = src.index("def revoke(")
        block = src[start:src.index("\ndef ", start + 10)]
        assert "status = :current_status" in block
        assert "decided_by =" not in block  # 確定者は上書きしない
        assert "DELETE" not in block

    def test_migration_adds_revoked_without_delete(self):
        sql = (BACKEND / "db" / "093_identity_link_revoke.sql").read_text(encoding="utf-8")
        assert "'revoked'" in sql
        assert "revoke_reason" in sql and "revoked_by" in sql and "revoked_at" in sql
        assert "DELETE" not in sql.upper().replace("DELETE 文", "")

    def test_provenance_label_for_revoked(self):
        assert identity_links.provenance_label({"status": "revoked"}) == identity_links.PROVENANCE_REVOKED


class TestDescribeActors:
    def test_self_confirmed_note(self, monkeypatch):
        from core import atlas_review_display

        monkeypatch.setattr(
            atlas_review_display, "fetch_actor_labels",
            lambda _s, ids: {_USER_A: "山田"},
        )
        out = identity_links.describe_actors(
            {"status": "confirmed", "created_by": _USER_A, "decided_by": _USER_A},
            session=object(),
        )
        assert out["created_by_label"] == "山田"
        assert out["decided_by_label"] == "山田"
        assert out["revoked_by_label"] == ""
        assert out["self_confirmed_note"] == identity_links.SELF_CONFIRMED_NOTE

    def test_unknown_actor_is_named_not_uuid(self, monkeypatch):
        from core import atlas_review_display

        monkeypatch.setattr(atlas_review_display, "fetch_actor_labels", lambda _s, ids: {})
        out = identity_links.describe_actors(
            {"status": "confirmed", "created_by": _USER_A, "decided_by": _USER_B},
            session=object(),
        )
        assert _USER_B not in repr(out)
        assert "self_confirmed_note" not in out


class TestRevokeRoute:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from api.main import app
        from dependencies import _create_token, ROLE_TEACHER

        token = _create_token(_USER_A, "tea", "tea@x", ROLE_TEACHER)
        return TestClient(app), {"Authorization": "Bearer " + token}

    def _patch(self, monkeypatch, status="confirmed"):
        import routes.deliberation as route

        existing = {"id": _LINK, "status": status, "instance_document_id": _DOC, "decided_by": _USER_B}
        monkeypatch.setattr(route.identity_links, "get_by_id", lambda _id: dict(existing))
        monkeypatch.setattr(route, "_ensure_document_editable", lambda *a, **k: None)
        recorded = []
        monkeypatch.setattr(route, "record_review_event", lambda *a, **k: recorded.append(a))
        monkeypatch.setattr(
            route.identity_links, "revoke",
            lambda link_id, revoked_by, reason: {**existing, "status": "revoked", "revoke_reason": reason},
        )
        monkeypatch.setattr(route, "_identity_link_response", lambda link: dict(link))
        return recorded

    def test_empty_reason_is_422(self, client, monkeypatch):
        c, h = client
        self._patch(monkeypatch)
        res = c.post(f"/api/admin/deliberation/identity-links/{_LINK}/revoke", json={"reason": " "}, headers=h)
        assert res.status_code == 422

    def test_candidate_cannot_be_revoked(self, client, monkeypatch):
        c, h = client
        self._patch(monkeypatch, status="candidate")
        res = c.post(f"/api/admin/deliberation/identity-links/{_LINK}/revoke", json={"reason": "粒度が違う"}, headers=h)
        assert res.status_code == 409

    def test_revoke_records_audit(self, client, monkeypatch):
        c, h = client
        recorded = self._patch(monkeypatch)
        res = c.post(f"/api/admin/deliberation/identity-links/{_LINK}/revoke", json={"reason": "粒度が違う"}, headers=h)
        assert res.status_code == 200
        assert res.json()["status"] == "revoked"
        assert recorded and recorded[0][3] == "revoked"
        assert recorded[0][5]["action"] == "identity_link.revoked"
        assert recorded[0][5]["confirmed_by"] == _USER_B


class TestRevokeUi:
    def test_deliberation_row_has_revoke_only_for_confirmed(self):
        src = (JS / "deliberation.js").read_text(encoding="utf-8")
        assert 'data-identity-action="revoke"' in src
        assert 'link.status === "confirmed"' in src
        assert 'data-ui-anchor="deliberation.identity-link-revoke"' in src
        assert "revoke_note" in src and "decided_by_label" in src

    def test_revoke_prompts_reason(self):
        src = (JS / "deliberation.js").read_text(encoding="utf-8")
        start = src.index("function _decideIdentityLink(")
        block = src[start:start + 2500]
        assert 'action === "revoke"' in block
        assert "window.prompt(" in block
        assert "reason" in block


# ── IK-0917 / IK-0956 ───────────────────────────────────────────────────────


class TestGranularityAndVersion:
    def test_granularity_note_only_when_kinds_differ(self):
        assert identity_links.granularity_note("theory", "theory") == ""
        note = identity_links.granularity_note("theory", "quantity")
        assert note and "種別が違います" in note
        assert identity_links.granularity_note("", "theory") == ""

    def test_confirm_question_names_both_sides(self):
        q = identity_links.confirm_question("部品", "TOV network map", "TOV 方程式")
        assert "TOV network map" in q and "TOV 方程式" in q and q.endswith("。")

    def test_version_hint(self):
        assert identity_links.document_version_hint("arXiv-2605.31198v1.tar.gz") == "TeX 版・arXiv-2605.31198v1.tar.gz"
        assert identity_links.document_version_hint("paper.pdf").startswith("PDF 版")
        assert identity_links.document_version_hint(None, None) == ""
        assert identity_links.document_format_label("x", "https://arxiv.org/pdf/1") == "PDF 版"

    def test_mismatch_confirm_is_stronger_in_ui(self):
        src = (JS / "deliberation.js").read_text(encoding="utf-8")
        assert "data-identity-granularity-mismatch" in src
        assert "window.confirm(" in src

    def test_learner_other_documents_disambiguate_same_title(self, monkeypatch):
        from core import component_context

        monkeypatch.setattr(
            component_context.identity_links_mod, "list_for_shared_part",
            lambda _sp: [
                {"status": "confirmed", "instance_document_id": "d1"},
                {"status": "confirmed", "instance_document_id": "d2"},
            ],
        )
        monkeypatch.setattr(component_context, "_document_titles", lambda ids: {"d1": "同じ題名", "d2": "同じ題名"})
        monkeypatch.setattr(component_context, "_document_format_labels", lambda ids: {"d1": "TeX 版", "d2": "PDF 版"})
        items, _ = component_context._other_confirmed_documents("sp", "d0", {"d1", "d2"})
        assert [i["title"] for i in items] == ["同じ題名（TeX 版）", "同じ題名（PDF 版）"]


# ── IK-0955 ─────────────────────────────────────────────────────────────────


class TestEntryReviewButtonsByState:
    def test_buttons_follow_state(self):
        src = (JS / "admin.js").read_text(encoding="utf-8")
        start = src.index("function _libraryEntryReviewButtonsHtml(")
        block = src[start:src.index("\n  }\n", start)]
        assert 'reviewStatus === "candidate"' in block
        assert 'reviewStatus === "dismissed"' in block
        assert "候補に戻す" in block
        # 確定済みには何も並べない
        assert block.rstrip().endswith('return "";')


# ── IK-0957 ─────────────────────────────────────────────────────────────────


class TestIdentityRulesVersion:
    def test_unscanned_documents(self):
        from core.library import identity_candidates as ic

        resolved = {
            "a": {"status": "completed", "stage_outputs": {"_artifacts": {"identity_candidates": {"rules_version": ic.IDENTITY_RULES_VERSION}}}},
            "b": {"status": "completed", "stage_outputs": {"_artifacts": {"identity_candidates": {}}}},
            "c": {"status": "completed", "stage_outputs": {}},
            "d": {"status": "running", "stage_outputs": {}},
        }
        assert ic.documents_not_scanned_by_current_rules(resolved) == ["b", "c"]

    def test_result_records_rules_version(self):
        from core.library import identity_candidates as ic

        out = ic._result("doc", "", 0, 0, 0, 0, 0, [])
        assert out["rules_version"] == ic.IDENTITY_RULES_VERSION

    def test_route_facts(self, monkeypatch):
        from api.routes import library as route
        from core.library import identity_candidates as ic

        monkeypatch.setattr(route.services, "list_visible_document_ids", lambda _u: {"a", "b"})

        class _S:
            def close(self):
                pass

        monkeypatch.setattr(route, "get_session", lambda: _S())
        import core.document_pipeline.persistence as persistence

        monkeypatch.setattr(
            persistence, "resolve_artifact_runs",
            lambda _s, ids, policy="adopted": {
                "a": {"status": "completed", "stage_outputs": {"_artifacts": {"identity_candidates": {"rules_version": ic.IDENTITY_RULES_VERSION}}}},
                "b": {"status": "completed", "stage_outputs": {}},
            },
        )
        monkeypatch.setattr(route, "_document_titles", lambda ids: {"b": "PINN の論文"})
        facts = route._identity_rules_scan_facts("u")
        assert len(facts) == 1 and "『PINN の論文』" in facts[0]
        assert "パイプラインを実行" in facts[0]

        monkeypatch.setattr(
            persistence, "resolve_artifact_runs",
            lambda _s, ids, policy="adopted": {
                "a": {"status": "completed", "stage_outputs": {"_artifacts": {"identity_candidates": {"rules_version": ic.IDENTITY_RULES_VERSION}}}},
            },
        )
        assert route._identity_rules_scan_facts("u") == [ic.IDENTITY_RULES_ALL_SCANNED_FACT]


# ── IK-0958 ─────────────────────────────────────────────────────────────────


class TestCopilot:
    def test_render_markdown_drops_link_syntax(self):
        src = (JS / "admin-assistant.js").read_text(encoding="utf-8")
        start = src.index("function renderMarkdown(")
        block = src[start:start + 800]
        assert "\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\)" in block

    def test_student_visibility_and_revoke_questions_resolve(self):
        from core.admin_assistant import intent

        got = intent.resolve_capability(
            "凍結していなくても学生に見える？確定の取り消しはどこから？", {"tab": "knowledge-library"}
        )
        assert got == "library.what_students_see"
        assert intent.resolve_capability("確定を取り消すには？", {"tab": "knowledge-library"}) == "library.revoke_identity_link"

    def test_what_students_see_answers_both_points(self):
        doc = (ROOT / "docs" / "admin_operations" / "library.md").read_text(encoding="utf-8")
        start = doc.index("{#what-students-see}")
        section = doc[start:doc.index("\n## ", start)]
        assert "凍結した版" in section
        assert "確定を取り消す" in section
        assert "{#revoke-identity-link}" in doc


# ── IK-0960 / IK-0925 / IK-0924 ─────────────────────────────────────────────


class TestExplanationCard:
    def test_revoke_support_only_when_my_support(self):
        src = (JS / "admin-lecture-studio.js").read_text(encoding="utf-8")
        start = src.index("function lsRenderExplanationCard(")
        block = src[start:start + 5000]
        assert "exp.my_support" in block
        assert "lsRenderStandardEvidence(exp)" in block

    def test_evidence_open_guarded(self):
        src = (JS / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert 'data-ui-anchor="lecture-studio.explanation-evidence-open"' in src
        assert 'openElement("theory_claim"' in src

    def test_modal_heading_uses_two_axes(self):
        src = (JS / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert "説明バージョンと承認" not in src
        assert "説明と、その支持・学生への公開" in src

    def test_out_model_fields(self):
        from api.routes.theory_components import ExplanationOut

        fields = ExplanationOut.model_fields
        for key in ("my_support", "evidence_claims", "evidence_document_id", "evidence_note"):
            assert key in fields


# ── IK-0961 ─────────────────────────────────────────────────────────────────


class TestSymbolWording:
    def test_no_internal_wording(self):
        from core import symbol_lookup

        assert "位置を特定できない" not in symbol_lookup.FACT_POSITION_UNKNOWN
        assert "この式の中だけ" not in symbol_lookup.LEARNER_SYMBOL_SCOPE_LABELS.values()
        from core.element_vocab import SYMBOL_SCOPE_LABELS

        assert set(symbol_lookup.LEARNER_SYMBOL_SCOPE_LABELS) == set(SYMBOL_SCOPE_LABELS)
