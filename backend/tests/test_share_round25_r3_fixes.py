"""第 25 周・面 A 第 3 周の是正（IK-0921 / IK-0922 / IK-0898 / IK-0927 / IK-0929）。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "src"))

ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"


# ── IK-0921: Copilot の語表の正本は登録簿 ─────────────────────────────────────
class TestCopilotKeywordsInRegistry:
    def test_intent_has_no_separate_keyword_table(self):
        src = (ROOT / "backend" / "core" / "admin_assistant" / "intent.py").read_text(encoding="utf-8")
        assert "_KEYWORDS: dict" not in src
        assert 'getattr(cap, "keywords"' in src

    def test_library_capabilities_have_keywords(self):
        from core.admin_assistant import capabilities as caps

        for cap in caps.all_capabilities():
            if cap.id.startswith("library."):
                assert cap.keywords, cap.id

    def test_library_question_resolves_to_library(self):
        from core.admin_assistant import intent

        got = intent.resolve_capability(
            "これを確定したら学生の画面には何が出る？", {"tab": "knowledge-library"}
        )
        assert got == "library.identity_candidates"

    def test_atlas_freeze_still_resolves_on_atlas_tab(self):
        from core.admin_assistant import intent

        assert intent.resolve_capability("地図の骨格を凍結したい", {"tab": "atlas"}) == "atlas.freeze_skeleton"

    def test_kb_search_prefers_open_tab(self):
        from core.admin_assistant import knowledge as kb
        from core.admin_assistant import capabilities as caps

        allowed = caps.all_capabilities()
        hits = kb.search("確定 学生", allowed, limit=3, screen="knowledge-library")
        assert hits and hits[0]["screen"] == "knowledge-library"

    def test_library_howto_lives_in_library_doc(self):
        from core.admin_assistant import capabilities as caps

        for cap in caps.all_capabilities():
            if cap.screen == "knowledge-library":
                assert cap.howto_doc.startswith("admin_operations/library.md#"), cap.id

    def test_what_students_see_section(self):
        doc = (ROOT / "docs" / "admin_operations" / "library.md").read_text(encoding="utf-8")
        assert "{#what-students-see}" in doc
        assert "(#what-students-see)" in doc


# ── IK-0922: 記号の使用例に合成主張を出さない ─────────────────────────────────
class TestSymbolMentionsExcludeSynthesized:
    def test_predicate_shared(self):
        from core.schema import CLAIM_ORIGIN_EQUATION_SYNTHESIS, NON_SOURCE_TEXT_CLAIM_ORIGINS

        assert CLAIM_ORIGIN_EQUATION_SYNTHESIS in NON_SOURCE_TEXT_CLAIM_ORIGINS

    def test_query_filters_origin(self):
        from core import symbol_lookup

        captured = {}

        class _Rows:
            def mappings(self):
                return self

            def fetchall(self):
                return [{"document_id": "d", "text": "The pressure $P$ is fixed by the penalty."}]

        class _Session:
            def execute(self, stmt, params):
                captured["sql"] = str(stmt)
                captured["params"] = params
                return _Rows()

        out = symbol_lookup._claim_mentions(
            _Session(), ["11111111-1111-1111-1111-111111111111"], symbol="P"
        )
        assert "non_source_origins" in captured["sql"]
        assert captured["params"]["non_source_origins"] == ["equation_synthesis"]
        assert out


# ── IK-0898: 区切りの無い TeX を見出しの切り詰めの前に区切る ───────────────────
class TestClaimLabelDelimitsBareTex:
    def test_bare_tex_is_delimited(self):
        from core.deliberation.labels import claim_label

        label = claim_label({"text": "The structure network \\mathcal{N}_\\mathrm{TOV} maps radial coordinate to profiles."})
        assert "$\\mathcal{N}_\\mathrm{TOV}$" in label.text

    def test_full_text_keeps_symbol(self):
        from core.deliberation.labels import claim_label

        label = claim_label({"text": "Two interacting networks --- one representing the EOS P(\\rho) and one representing the structure, are trained jointly."})
        assert "$P(\\rho)$" in label.full_text

    def test_plain_claim_unchanged(self):
        from core.deliberation.labels import claim_label

        assert claim_label({"text": "A plain claim without math."}).text == "A plain claim without math."


# ── IK-0927: 地図との対応の導出の条件と、手動の入口 ───────────────────────────
class TestAtlasLinkManual:
    def _skeleton(self):
        concept = SimpleNamespace(id="c_tov", label="TOV 方程式")
        region = SimpleNamespace(id="r1", label="コンパクト星", concepts=[concept])
        return SimpleNamespace(version="3", regions=[region])

    def test_search_concept_nodes(self):
        from core.library import atlas_links

        hits = atlas_links.search_concept_nodes(self._skeleton(), "tov")
        assert hits == [{"node_id": "c_tov", "label": "TOV 方程式", "region_label": "コンパクト星"}]
        assert atlas_links.search_concept_nodes(self._skeleton(), "銀河") == []

    def test_facts_constants(self):
        from core.library import atlas_links

        assert "凍結していないエントリ" in atlas_links.FACT_UNFROZEN_ENTRIES_LEXICAL_ONLY
        assert "手で対応を付けられます" in atlas_links.FACT_MANUAL_LINK_ENTRY

    def test_ui_has_manual_entry_and_anchor(self):
        js = ADMIN_JS.read_text(encoding="utf-8")
        assert 'data-ui-anchor="knowledge-library.atlas-link-manual"' in js
        assert "/atlas-links/nodes?domain_key=" in js
        from core.help_kb.admin_ui_anchors import ADMIN_UI_ANCHORS

        assert "knowledge-library.atlas-link-manual" in ADMIN_UI_ANCHORS


@pytest.fixture
def client_and_teacher():
    pytest.importorskip("fastapi")
    sys.path.insert(0, str(ROOT / "backend" / "api"))
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import _create_token, ROLE_TEACHER

    client = TestClient(app)
    teacher = _create_token("22222222-2222-2222-2222-222222222222", "tea", "tea@x", ROLE_TEACHER)
    return client, {"Authorization": "Bearer " + teacher}


class TestAtlasLinkManualApi:
    def _patch(self, monkeypatch, skeleton):
        import routes.library as route_mod

        class _S:
            def close(self):
                pass

        monkeypatch.setattr(route_mod, "get_session", lambda: _S())
        monkeypatch.setattr(route_mod.atlas_store, "load_frozen_skeleton", lambda s, d: skeleton)
        calls = []
        monkeypatch.setattr(
            route_mod.library_registry, "create_node_link",
            lambda **kw: calls.append(kw) or {"id": "l1", "status": "candidate", **{k: kw[k] for k in ("domain_key", "node_id")}},
        )
        return calls

    def test_unknown_node_is_422(self, client_and_teacher, monkeypatch):
        client, headers = client_and_teacher
        calls = self._patch(monkeypatch, TestAtlasLinkManual()._skeleton())
        r = client.post(
            "/api/admin/library/entries/33333333-3333-3333-3333-333333333333/atlas-links",
            json={"domain_key": "astrophysics", "node_id": "nope"}, headers=headers,
        )
        assert r.status_code == 422
        assert calls == []

    def test_manual_link_is_candidate_with_manual_justification(self, client_and_teacher, monkeypatch):
        client, headers = client_and_teacher
        calls = self._patch(monkeypatch, TestAtlasLinkManual()._skeleton())
        r = client.post(
            "/api/admin/library/entries/33333333-3333-3333-3333-333333333333/atlas-links",
            json={"domain_key": "astrophysics", "node_id": "c_tov"}, headers=headers,
        )
        assert r.status_code == 201
        assert calls[0]["mapping_justification"] == "manual_curation"

    def test_node_search(self, client_and_teacher, monkeypatch):
        client, headers = client_and_teacher
        self._patch(monkeypatch, TestAtlasLinkManual()._skeleton())
        r = client.get("/api/admin/library/atlas-links/nodes", params={"domain_key": "astrophysics", "q": "TOV"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["nodes"][0]["label"] == "TOV 方程式"


# ── IK-0929: コース管理の分野キー ────────────────────────────────────────────
class TestCourseAtlasBindingText:
    def test_coverage_label_has_no_ratio(self):
        from core.label_vocab import atlas_binding_coverage_label

        assert atlas_binding_coverage_label(0, 20) == "トピックの対応なし"
        assert atlas_binding_coverage_label(5, 20) == "一部のトピックに対応あり"
        assert atlas_binding_coverage_label(20, 20) == "すべてのトピックに対応あり"

    def test_js_uses_server_labels(self):
        js = ADMIN_JS.read_text(encoding="utf-8")
        body = js.split("function courseAtlasBindingText")[1].split("\n  function ")[0]
        assert "atlas_domain_label" in body
        assert "atlas_binding_coverage_label" in body
        assert "c.atlas_cartridge_id +" not in body
        assert "atlas_topic_count" not in body
