"""第 25 周 面 C（分野の地図の運用）第 7 波の是正（IK-1044〜1047 + IK-0942 / IK-1018 / IK-0664 の残り）。

- IK-1044: 関係（辺）の区画と「採用済み・未反映」の区画にも［現在の版から次版の下書きを作る］
  （既存 ``draft/from-frozen`` を呼ぶだけ）。関係カードの案内文は画面にある導線を指す。
- IK-1045: 学習者の L2 の手がかりは、現行凍結版で過去の改訂で加わった関係の両端（直近 → 古い順）。
- IK-1046: 配置の理由の注記は、略称の正式名（頭文字の連なり）が引用にあれば「現れない」と言わない。
- IK-1047: 反映の確認モーダルに依存の向きの一文（``direction_line``）。
- IK-0942 残り: 論文の海も「地図 版 X」で版を言う。
- IK-1018 残り: 学習者の地図の文に「台帳」「記帳」を出さない（読み時の投影）。
- IK-0664: 論文の呼び方は題名を正本・無ければ arXiv 番号。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas_state as st  # noqa: E402
from core import document_refs  # noqa: E402
from core import label_vocab  # noqa: E402
from core.landscape import schema as ls_schema  # noqa: E402

JS = ROOT / "frontend" / "public" / "js"
ADMIN_JS = JS / "admin.js"
ROUTES = BACKEND / "api" / "routes"
PUBLIC = ROOT / "frontend" / "public"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _edge(a, b):
    return SimpleNamespace(from_id=a, to_id=b)


# ---------------------------------------------------------------------------
# IK-1044 / IK-1047（admin.js）
# ---------------------------------------------------------------------------


class TestEdgeDraftCreateEntrance:
    def test_edge_sections_offer_draft_from_frozen(self):
        src = _read(ADMIN_JS)
        assert "function _edgeDraftCreateHtml(" in src
        # 候補の一覧（候補 0 件の経路も含む）と「採用済み・未反映」の区画の両方に出す
        assert "_reviewListNotesHtml(data) + _edgeDraftCreateHtml(draftExists, retired)" in src
        assert '_edgeDraftCreateHtml(draftExists, retired, "atlas-edge-draft-create-orphaned")' in src
        # 既存の from-frozen を呼ぶだけ（新しい書き込み経路を作らない）
        assert src.count("/atlas/skeleton/draft/from-frozen") == 1
        assert "gapCreateDraftFromFrozen(setEdgesStatus)" in src
        assert 'data-ui-anchor="atlas.edge-draft-from-frozen"' in src

    def test_card_text_points_at_existing_button(self):
        src = _read(ADMIN_JS)
        m = re.search(r'var EDGE_NO_DRAFT_TEXT = "([^"]+)";', src)
        assert m and "［現在の版から次版の下書きを作る］" in m.group(1)

    def test_anchor_and_manual_three_piece_set(self):
        from core.help_kb import admin_ui_anchors

        assert "atlas.edge-draft-from-frozen" in admin_ui_anchors.KNOWN_ADMIN_UI_ANCHOR_IDS
        manual = _read(ROOT / "docs" / "manual" / "teacher" / "17-admin-atlas.md")
        assert "{#edge-draft-from-frozen}" in manual

    def test_incorporate_confirm_lists_direction_line(self):
        src = _read(ADMIN_JS)
        body = src[src.index("function openEdgeIncorporateConfirm"):]
        body = body[: body.index("\n    function ", 10)]
        assert "preview.direction_line" in body

    def test_admin_js_cache_buster(self):
        html = _read(PUBLIC / "admin.html")
        assert "-atlas7" in html.split("/js/admin.js?v=")[1].split('"')[0]


# ---------------------------------------------------------------------------
# IK-1045
# ---------------------------------------------------------------------------


class TestAddedRelationsAcrossVersions:
    def test_relations_added_in_earlier_revisions_stay_newest_first(self):
        from routes import atlas_view as v

        h1 = SimpleNamespace(version="2026.2", edges=[_edge("base1", "base2")])
        h2 = SimpleNamespace(version="2026.3", edges=[_edge("base1", "base2"), _edge("a", "b"), _edge("c", "d")])
        h3 = SimpleNamespace(
            version="2026.4",
            edges=[_edge("base1", "base2"), _edge("a", "b"), _edge("d", "c"), _edge("e", "f")],
        )
        got = v.added_relation_pairs([h1, h2, h3], "2026.4")
        # 2026.4 で加わった e–f が先、2026.3 で確定した a–b / c–d も残る。最初の版から在る関係は含めない。
        assert got == [("e", "f"), ("a", "b"), ("d", "c")]

    def test_hint_word_matches_selection(self):
        assert "改訂" in label_vocab.ATLAS_L2_HINT_ADDED_RELATIONS
        assert not re.search(r"[0-9０-９]", label_vocab.atlas_l2_omitted_note(label_vocab.ATLAS_L2_HINTS))


# ---------------------------------------------------------------------------
# IK-1046
# ---------------------------------------------------------------------------


class TestAcronymExpansion:
    def test_expanded_acronym_is_not_reported(self):
        quote = "the Tolman-Oppenheimer-Volkoff equations give the maximum mass"
        assert ls_schema.acronym_expanded_in("TOV", [quote])
        assert ls_schema.acronym_expanded_in("LMC", ["the Large Magellanic Cloud"])
        assert ls_schema.acronym_expanded_in("EoS", ["an equation of state for dense matter"])
        assert not ls_schema.acronym_expanded_in("BAO", [quote])
        assert ls_schema.reason_beyond_quote_note("TOV 方程式で最大質量を論じる", [quote]) == ""

    def test_unexpanded_term_still_reported(self):
        note = ls_schema.reason_beyond_quote_note("BAO の測定に基づく", ["distance measurements"])
        assert "「BAO」" in note


# ---------------------------------------------------------------------------
# IK-0942 残り（論文の海）
# ---------------------------------------------------------------------------


class TestSeaVersionLabel:
    def test_corpus_landscape_carries_version_label(self):
        src = _read(BACKEND / "core" / "corpus_view.py")
        assert '"skeleton_version_label": label_vocab.atlas_version_label(skeleton_version)' in src
        assert label_vocab.atlas_version_label("2026.4") == "地図 版 2026.4"
        assert label_vocab.atlas_version_label("") == ""

    def test_sea_renders_server_label(self):
        js = _read(JS / "corpus-sea.js")
        assert "function versionLineHtml()" in js and "skeleton_version_label" in js
        assert "densityLegendHtml() + versionLineHtml()" in js
        html = _read(PUBLIC / "index.html")
        assert "-atlas7" in html.split("/js/corpus-sea.js?v=")[1].split('"')[0]


# ---------------------------------------------------------------------------
# IK-1018 残り（台帳・記帳）
# ---------------------------------------------------------------------------


class TestLearnerWording:
    def test_known_lines_are_reworded(self):
        for line in (
            st.FOG_VERIFY_LINE,
            st.SEED_VERIFY_LINE,
            st.VERIFIED_WITHOUT_EVIDENCE_LINE,
            label_vocab.ATLAS_PILL_UNRECORDED,
            label_vocab.ATLAS_PILL_SEED_VERIFIED,
            label_vocab.ATLAS_PILL_NOTES["assumed"],
            label_vocab.ATLAS_PILL_NOTES["unrecorded"],
            "検証: 記帳された直接検証なし。原文2本に記帳あり（式(3)）。",
            "検証: 台帳に記帳がありません。",
        ):
            out = label_vocab.learner_atlas_wording(line)
            for term in label_vocab.ATLAS_LEARNER_WORDING_DENYLIST:
                assert term not in out, (line, out)
        assert label_vocab.learner_atlas_wording(st.SEED_VERIFY_LINE).startswith("検証: 検証の記録なし")
        assert label_vocab.learner_atlas_wording("承認: 2名の教員が承認") == "承認: 2名の教員が承認"

    def test_route_projects_nested_learner_text(self):
        from routes import atlas_view as v

        payload = {
            "nodes": {"n": {"pill": "台帳に記帳なし", "verify": "検証: 台帳に記帳なし。", "label": "台帳"}},
            "nodes_list": [{"panel": {"verify": "検証: 台帳に記帳がありません。", "pill_note": "記帳"}}],
        }
        v._apply_learner_wording(payload)
        assert payload["nodes"]["n"]["pill"] == "検証の記録なし"
        assert "台帳" not in payload["nodes"]["n"]["verify"]
        # 名前（label）は書き換えない
        assert payload["nodes"]["n"]["label"] == "台帳"
        assert "台帳" not in payload["nodes_list"][0]["panel"]["verify"]
        src = _read(ROUTES / "atlas_view.py")
        assert "_apply_learner_wording(payload[key])" in src
        assert "return _apply_learner_wording({" in src

    def test_frontend_learner_texts(self):
        panel = _read(JS / "atlas-panel.js")
        overlay = _read(JS / "atlas-overlay.js")
        assert '"検証の記録がありません。"' in panel
        assert "記帳された根拠" not in overlay
        html = _read(PUBLIC / "index.html")
        assert "-atlas7" in html.split("/js/atlas-panel.js?v=")[1].split('"')[0]
        assert "-atlas7" in html.split("/js/atlas-overlay.js?v=")[1].split('"')[0]


# ---------------------------------------------------------------------------
# IK-0664（論文の呼び方）
# ---------------------------------------------------------------------------


class TestPaperTitles:
    def test_title_first_then_sibling_then_cache_then_number(self):
        entries = [
            {"document_id": "d1", "title": "Readable Title", "source_url": "", "filename": ""},
            {"document_id": "d2", "title": "2605.26810v1", "source_url": "", "filename": "2605.26810v1.pdf"},
            {"document_id": "d3", "title": "2606.02318v1", "source_url": "", "filename": ""},
            {"document_id": "d4", "title": "2606.00411v1", "source_url": "", "filename": ""},
        ]
        siblings = [
            {"document_id": "s9", "title": "Neutron star masses", "source_url": "https://arxiv.org/abs/2605.26810v2", "filename": ""},
            {"document_id": "s1", "title": "2605.26810", "source_url": "", "filename": ""},
        ]
        got = document_refs.choose_paper_titles(
            entries, siblings=siblings, cached_titles={"2606.02318": "Cached arXiv title"}
        )
        assert got == {
            "d1": "Readable Title",
            "d2": "Neutron star masses",
            "d3": "Cached arXiv title",
            "d4": "2606.00411v1",  # 題名が無ければ番号のまま（捏造しない）
        }

    def test_learner_landscape_resolves_titles_with_visible_siblings(self):
        src = _read(ROUTES / "landscape.py")
        assert "document_refs.resolve_paper_titles(" in src
        assert "services.list_visible_document_ids(viewer_id)" in src
        assert 'viewer_id=current_user["id"]' in src

    def test_registered_sources_treat_arxiv_number_as_placeholder(self):
        src = _read(ROUTES / "learning.py")
        block = src[src.index("def _with_resolved_source_titles("):]
        block = block[: block.index("\n@router", 0)]
        assert "title_is_arxiv_number(stored)" in block
        assert "(not stored or stored == material_id)" in block
