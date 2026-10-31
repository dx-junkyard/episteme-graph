"""第 25 周 面 C（分野の地図の運用）第 9 波の是正（IK-1072 / IK-1073 + 頭脳 NEW + IK-1056 の固定）。

- IK-1072: 有向の関係（依存）の向きの句は ``label_vocab.edge_direction_phrase`` が正本。反映の確認・カード
  （``atlas_review_display.edge_direction_line``）・凍結の影響（``atlas_lifecycle.edge_change_label``）・
  学生の L2（``levels["2"].directed_edges`` + ``relations_note`` → 矢じり）・右パネル（``relations``）が通す。
  「この版で加わった」印（NEW バッジ）は作らない。
- IK-1073: いまここが無いとき L1 / L2 に事実文 ``now_note``（推定でいまここを付けない）。
- 頭脳 NEW: 反映後のカードの向きの文は現在形 / 索引の再構築で何も変わらなければ書き直さない
  （「最後に作成」が進まない）。
- IK-1056: 承認の記録が無い 2 概念は状態が違っても同じ文。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas_lifecycle  # noqa: E402
from core import atlas_review_display as display  # noqa: E402
from core import atlas_state as st  # noqa: E402
from core import label_vocab  # noqa: E402

JS = ROOT / "frontend" / "public" / "js"


# ---------------------------------------------------------------------------
# IK-1072: 向きの句の正本と、それを通る各面
# ---------------------------------------------------------------------------


class TestDirectionPhraseCanonical:
    def test_phrase_only_for_directed_kinds(self):
        assert label_vocab.edge_direction_phrase("depends", "A", "B") == "「A」が「B」に依存する"
        assert label_vocab.edge_direction_phrase("adjacent", "A", "B") == ""
        assert label_vocab.edge_direction_phrase("related", "A", "B") == ""
        assert label_vocab.edge_direction_phrase(None, "A", "B") == ""
        assert label_vocab.edge_kind_is_directed("depends")
        assert not label_vocab.edge_kind_is_directed("related")

    def test_relation_phrase_says_kind_for_undirected(self):
        assert label_vocab.edge_relation_phrase("adjacent", "A", "B") == "「A」と「B」（隣接）"
        assert label_vocab.edge_relation_phrase("depends", "A", "B") == "「A」が「B」に依存する"
        assert label_vocab.edge_relation_phrase("bogus", "A", "B") == ""

    def test_review_card_line_uses_the_same_phrase_and_tense(self):
        phrase = label_vocab.edge_direction_phrase("depends", "宇宙膨張", "一般相対論")
        pending = display.edge_direction_line("depends", "宇宙膨張", "一般相対論")
        in_draft = display.edge_direction_line("depends", "宇宙膨張", "一般相対論", in_draft=True)
        assert pending.startswith(phrase) and pending.endswith("記載されます。")
        assert in_draft.startswith(phrase) and in_draft.endswith("記載されています。")
        assert "記載されます" not in in_draft
        assert "向きはありません" in display.edge_direction_line("adjacent", "A", "B", in_draft=True)
        assert "記載されています" in display.edge_direction_line("adjacent", "A", "B", in_draft=True)

    def test_freeze_impact_label_carries_direction(self):
        label = atlas_lifecycle.edge_change_label("宇宙膨張", "一般相対論", "depends")
        assert label == "宇宙膨張 — 一般相対論（依存: 「宇宙膨張」が「一般相対論」に依存する）"
        assert atlas_lifecycle.edge_change_label("A", "B", "adjacent") == "A — B（隣接）"
        assert atlas_lifecycle.edge_change_label("A", "B", "") == "A — B"

    def test_annotate_candidate_in_draft_is_present_tense(self):
        from api.routes import atlas_edges

        candidate = {
            "from_id": "a",
            "to_id": "b",
            "from_label": "A",
            "to_label": "B",
            "decision": {"status": "accepted", "edge_kind": "depends"},
        }
        out = atlas_edges._annotate_candidate(dict(candidate), {}, {("a", "b")})
        assert out["direction_line"].endswith("記載されています。")
        assert out["in_draft"] is True
        out2 = atlas_edges._annotate_candidate(dict(candidate), {}, set())
        assert out2["direction_line"].endswith("記載されます。")

    def test_l2_directed_line_and_panel_line_have_no_numbers(self):
        line = label_vocab.atlas_l2_directed_relations_line(
            [("depends", "宇宙膨張", "一般相対論"), ("related", "X", "Y")]
        )
        assert "「宇宙膨張」が「一般相対論」に依存する" in line
        assert "X" not in line
        assert label_vocab.atlas_l2_directed_relations_line([("related", "X", "Y")]) == ""
        many = [("depends", f"A{i}", "B") for i in range(10)]
        assert label_vocab.atlas_l2_directed_relations_line(many).endswith("、ほか。")
        panel = label_vocab.atlas_panel_relations_line(
            [("depends", "宇宙膨張", "一般相対論"), ("adjacent", "宇宙膨張", "暗黒物質")]
        )
        assert panel.startswith(label_vocab.ATLAS_PANEL_RELATIONS_PREFIX)
        assert "「宇宙膨張」と「暗黒物質」（隣接）" in panel
        assert label_vocab.atlas_panel_relations_line([]) == ""

    def test_no_new_badge_wording(self):
        for text in (
            label_vocab.ATLAS_L2_DIRECTED_RELATIONS_PREFIX,
            label_vocab.ATLAS_PANEL_RELATIONS_PREFIX,
            label_vocab.ATLAS_NOW_ABSENT_NOTE,
        ):
            for banned in ("NEW", "新着", "この版で加わった"):
                assert banned not in text


# ---------------------------------------------------------------------------
# GET /api/atlas: L2 の向き・右パネルの関係・いまここの不在
# ---------------------------------------------------------------------------


@pytest.fixture
def depends_cartridge(tmp_path, monkeypatch):
    """bundled 骨格の bc_sumrule — vcb を依存に書き換えた砂場のカートリッジ。"""
    import shutil

    from core import cartridges as cartridges_module

    src = cartridges_module._cartridges_root() / "particle_physics"
    dst = tmp_path / "particle_physics"
    shutil.copytree(src, dst)
    yaml_path = dst / "atlas" / "skeleton.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    old = "  - from: bc_sumrule\n    to: vcb\n    kind: related\n"
    assert old in text
    yaml_path.write_text(
        text.replace(old, "  - from: bc_sumrule\n    to: vcb\n    kind: depends\n"), encoding="utf-8"
    )
    monkeypatch.setattr(cartridges_module, "_cartridges_root", lambda: tmp_path)
    cartridges_module.clear_cache()
    yield tmp_path
    cartridges_module.clear_cache()


def _route(traces):
    from tests.api.test_atlas_view_api import FakeResult, _SIGNATURE, _cache_rows

    rows = _cache_rows()
    meta = json.dumps({"signature": _SIGNATURE})

    def route(sql, params):
        if "atlas_overlay_dirty" in sql and sql.startswith("SELECT"):
            return FakeResult()
        if "entry_type = 'meta'" in sql:
            return FakeResult([(meta,)])
        if "FROM atlas_overlay_cache" in sql:
            return FakeResult(rows)
        if "FROM interest_traces" in sql:
            return FakeResult(traces)
        if "document_analysis_runs" in sql:
            return FakeResult([("", "", "", "", "")])
        return FakeResult()

    return route


class TestAtlasViewDirectionAndNowNote:
    def _get(self, monkeypatch, traces):
        from tests.api import test_atlas_view_api as base

        factory_cls = base.FakeSession

        class Factory:
            def __call__(self):
                return factory_cls(_route(traces))

        import core.postgres as postgres_module

        monkeypatch.setattr(postgres_module, "get_session", Factory())
        from fastapi.testclient import TestClient

        from api.main import app

        res = TestClient(app).get("/api/atlas?cartridge=particle_physics", headers=base._headers())
        assert res.status_code == 200
        return res.json()

    def test_l2_directed_edges_and_relation_note(self, monkeypatch, depends_cartridge):
        from tests.api.test_atlas_view_api import _traces_rows

        data = self._get(monkeypatch, _traces_rows())
        l2 = data["levels"]["2"]
        assert ["bc_sumrule", "vcb"] in l2["edges"]
        assert l2["directed_edges"] == [["bc_sumrule", "vcb"]]
        labels = {n["id"]: n["label"] for n in data["nodes_list"]}
        phrase = label_vocab.edge_direction_phrase("depends", labels["bc_sumrule"], labels["vcb"])
        assert phrase in l2["relations_note"]
        # 右パネル: 選んだ概念に結ばれた確定済みの関係（両端の概念に同じ句）
        assert phrase in data["nodes"]["vcb"]["relations"]
        panel = next(n for n in data["nodes_list"] if n["id"] == "bc_sumrule")["panel"]
        assert phrase in panel["relations"]
        # 足跡あり → いまここの不在の文は出ない
        assert "now_note" not in data["levels"]["1"]
        assert "now_note" not in l2

    def test_no_footprints_says_now_is_absent(self, monkeypatch, depends_cartridge):
        data = self._get(monkeypatch, [])
        assert data["me"]["now"] is None
        assert data["levels"]["1"]["now_note"] == label_vocab.ATLAS_NOW_ABSENT_NOTE
        assert data["levels"]["2"]["now_note"] == label_vocab.ATLAS_NOW_ABSENT_NOTE
        # いまここを推定で付けない
        assert all(n.get("status") != "now" for n in data["nodes"].values())


class TestFrontendWiring:
    def test_overlay_draws_arrowheads_from_server_pairs_and_notes(self):
        src = (JS / "atlas-overlay.js").read_text(encoding="utf-8")
        assert "levelData.directed_edges" in src
        assert "atlas-l2-arrow" in src
        assert "levelData.now_note" in src and "levelData.relations_note" in src
        # 種別の解釈をフロントでしない
        assert '"depends"' not in src

    def test_panel_shows_server_relations_line(self):
        src = (JS / "atlas-panel.js").read_text(encoding="utf-8")
        assert "info.relations" in src
        assert "atlas-panel-relations" in src

    def test_cache_busters_bumped(self):
        html = (ROOT / "frontend" / "public" / "index.html").read_text(encoding="utf-8")
        assert "-atlas9" in html.split("/js/atlas-overlay.js?v=")[1].split('"')[0]
        assert "-atlas9" in html.split("/js/atlas-panel.js?v=")[1].split('"')[0]


# ---------------------------------------------------------------------------
# 頭脳 NEW: 索引の再構築で何も変わらなければ書き直さない
# ---------------------------------------------------------------------------


class TestVectorRefreshUnchangedDoesNotRewrite:
    def test_no_targets_and_same_rows_skips_replace(self, monkeypatch):
        import core.atlas_store as atlas_store
        from core.atlas_vectors import builder, store

        class _C:
            def __init__(self, cid, label):
                self.id, self.label = cid, label

        class _R:
            def __init__(self):
                self.id, self.label, self.concepts = "r1", "領域", (_C("c1", "概念"),)

        class _S:
            version = "v1"
            regions = (_R(),)

        monkeypatch.setattr(atlas_store, "load_frozen_skeleton", lambda s, d: _S())

        def _hashes(session, domain, specs):
            for spec in specs:
                spec["source_text"] = "t-" + spec["node_id"]
                spec["source_hash"] = "h-" + spec["node_id"]

        monkeypatch.setattr(builder, "_attach_source_hashes", _hashes)
        monkeypatch.setattr(store, "stored_hashes", lambda s, d, v: {"r1": "h-r1", "c1": "h-c1"})
        monkeypatch.setattr(store, "stored_vectors", lambda s, d, v: {"r1": [1.0], "c1": [1.0]})

        def _boom(*a, **k):  # pragma: no cover — 呼ばれてはいけない
            raise AssertionError("unchanged refresh must not rewrite rows")

        monkeypatch.setattr(store, "replace_domain_embeddings", _boom)
        monkeypatch.setattr(builder, "embed_texts", _boom)

        class _Sess:
            def commit(self):  # pragma: no cover
                raise AssertionError("no commit")

        got = builder.build_anchor_embeddings("d1", session=_Sess())
        assert got["status"] == "completed"
        assert got["embedded"] == 0 and got["embedded_node_ids"] == []
        assert got["unchanged"] is True


# ---------------------------------------------------------------------------
# IK-1056: 状態の違う 2 概念で承認の記録が無いときは同じ文
# ---------------------------------------------------------------------------


class TestEndorseNoneLineIsStatusIndependent:
    def test_same_line_for_different_statuses(self):
        signals = st.ConceptSignals(key="c1")
        lines = {
            st.endorse_line_for(status, signals)
            for status in (st.STATUS_VERIFIED, st.STATUS_UNKNOWN, st.STATUS_ASSUMED)
        }
        assert lines == {label_vocab.ATLAS_ENDORSE_NONE_LINE}
        # キャッシュに残る旧い文も読み時に同じ文へ
        assert st.learner_endorse_line(label_vocab.ATLAS_ENDORSE_LEGACY_NONE_LINE) == (
            label_vocab.ATLAS_ENDORSE_NONE_LINE
        )
