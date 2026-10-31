"""第 25 周 面 C（分野の地図の運用）第 10 波の是正（IK-1118 / IK-1119 / H54 / te-01・パネルの論文名）。

- IK-1118: 関係を名前で言う**全ての出口**が ``label_vocab`` の句（``edge_relation_phrase`` /
  ``edge_direction_phrase``）を通る — L2 の「ここには描いていないもの」・反映の知らせ・採用の結果文・
  凍結を止めている採用の一覧・JSON Patch の after。同じ型の 4 回目なので、「A — B」を組み立てる
  f-string が句の関数を呼ばない関数に現れないことを AST で固定する（面を足していく直し方をやめる）。
- IK-1119（オーナー判断＝推奨どおり）: L2 の語は「これまでの版の改訂で加わった関係」。版の事実は
  右パネルの関係の行にだけ「（版 X で加わった）」で添える（地図の上の印・NEW・告知は作らない）。
- H54: 次版の下書き（無ければ公開中の版）に入った採用の見送りは 409 の事実文。カードは［見送り…］を出さない。
- te-01: 凍結で消えるノードが無いとき「対応は要らない」の事実文。
- 右パネル: その場所に置かれたコースの論文の名前（出所ラベル付き・件数なし）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas_correspondence  # noqa: E402
from core import atlas_review_display as display  # noqa: E402
from core import label_vocab  # noqa: E402

JS = ROOT / "frontend" / "public" / "js"

_PHRASE_FUNCS = ("edge_relation_phrase", "edge_direction_phrase", "edge_change_label")


def _edge(a, b, kind="depends"):
    return SimpleNamespace(from_id=a, to_id=b, kind=kind)


# ---------------------------------------------------------------------------
# IK-1118: 全ての出口が句を通る
# ---------------------------------------------------------------------------


class TestDirectionOnEverySurface:
    def test_undrawn_line_keeps_direction(self):
        note = label_vocab.atlas_l2_omitted_note(
            [label_vocab.ATLAS_L2_HINT_COURSE_TOPICS],
            [("depends", "惑星形成", "星形成"), ("adjacent", "宇宙線", "宇宙天気")],
        )
        assert "「惑星形成」が「星形成」に依存する" in note
        assert "「宇宙線」と「宇宙天気」（隣接）" in note
        assert "惑星形成 — 星形成" not in note
        assert not re.search(r"[0-9０-９]", note)

    def test_undrawn_line_without_kind_stays_as_fact(self):
        line = label_vocab.atlas_l2_undrawn_relations_line([("A", "B")])
        assert "A — B" in line

    def test_incorporated_result_says_direction(self):
        line = display.incorporated_edge_result_line("depends", "初期宇宙・インフレーション", "初期宇宙の物理")
        assert line == "「初期宇宙・インフレーション」が「初期宇宙の物理」に依存する関係を次版の下書きに追加しました。"
        assert "（隣接）" in display.incorporated_edge_result_line("adjacent", "A", "B")
        assert display.incorporated_edge_result_line("", "A", "B") == "「A — B」を次版の下書きに追加しました。"

    def test_accept_result_says_direction(self):
        line = display.edge_decide_result_line("accept", "A", "B", "depends")
        assert line.startswith("「A」が「B」に依存する関係として採用しました。")
        assert display.edge_decide_result_line("accept", "A", "B") == (
            "「A」と「B」を関係として採用しました。次版の下書きへの反映は別の操作です。"
        )
        assert display.edge_decide_result_line("dismiss", "A", "B", "depends").startswith("「A」と「B」の関係の候補")

    def test_patch_after_uses_phrase(self):
        from core.atlas_edges import patching

        draft = {
            "version": "2026.8",
            "regions": [
                {"id": "r", "label": "R", "concepts": [
                    {"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}
            ],
            "edges": [],
        }
        built = patching.build_edge_patch(draft, from_id="a", to_id="b", kind="depends")
        assert built["patch"][0]["after"] == "「A」が「B」に依存する"

    def test_route_sources_route_every_naming_exit_through_phrase(self):
        edges_src = (BACKEND / "api" / "routes" / "atlas_edges.py").read_text(encoding="utf-8")
        assert "incorporated_edge_result_line(" in edges_src
        assert "INCORPORATED_EDGE_RESULT.format(" not in edges_src
        atlas_src = (BACKEND / "api" / "routes" / "atlas.py").read_text(encoding="utf-8")
        assert "atlas_lifecycle.edge_change_label(" in atlas_src
        view_src = (BACKEND / "api" / "routes" / "atlas_view.py").read_text(encoding="utf-8")
        assert "concept_edge_kind.get((a, b), \"\")" in view_src

    def test_no_dash_join_outside_phrase_functions(self):
        """「A — B」を f-string で組む関数は、同じ関数の中で句の関数を通す（無ければ fallback ではない）。"""
        files = (
            list((BACKEND / "api" / "routes").glob("atlas*.py"))
            + list((BACKEND / "core").glob("atlas*.py"))
            + list((BACKEND / "core" / "atlas_edges").glob("*.py"))
            + list((BACKEND / "core" / "atlas_gaps").glob("*.py"))
            + [BACKEND / "core" / "label_vocab.py"]
        )
        offenders: list[str] = []
        for path in files:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                fn_src = ast.get_source_segment(source, fn) or ""
                for node in ast.walk(fn):
                    if not isinstance(node, ast.JoinedStr):
                        continue
                    consts = "".join(
                        v.value for v in node.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    )
                    n_fmt = sum(isinstance(v, ast.FormattedValue) for v in node.values)
                    if " — " in consts and n_fmt >= 2 and not any(
                        f"{name}(" in fn_src for name in _PHRASE_FUNCS
                    ):
                        offenders.append(f"{path.name}:{fn.name}:{node.lineno}")
        assert offenders == []


# ---------------------------------------------------------------------------
# IK-1119: 範囲の語とパネルの版の事実
# ---------------------------------------------------------------------------


class TestAddedRelationVersions:
    def _view(self):
        from routes import atlas_view

        return atlas_view

    def test_hint_word_states_range(self):
        assert label_vocab.ATLAS_L2_HINT_ADDED_RELATIONS.startswith("これまでの版の改訂")
        assert label_vocab.ATLAS_L2_UNDRAWN_RELATIONS_PREFIX.startswith("これまでの版の改訂")

    def test_versions_follow_the_same_rule_as_pairs(self):
        v = self._view()
        h1 = SimpleNamespace(version="2026.2", edges=[_edge("x", "y")])
        h2 = SimpleNamespace(version="2026.3", edges=[_edge("x", "y"), _edge("p", "q")])
        h3 = SimpleNamespace(version="2026.4", edges=[_edge("x", "y"), _edge("p", "q"), _edge("m", "n")])
        assert v.added_relation_pairs([h1, h2, h3], "2026.4") == [("m", "n"), ("p", "q")]
        got = v.added_relation_versions([h1, h2, h3], "2026.4")
        assert got == {frozenset(("m", "n")): "2026.4", frozenset(("p", "q")): "2026.3"}
        # 最初の版から在る関係には版を言わない
        assert frozenset(("x", "y")) not in got

    def test_panel_line_appends_version_only_when_known(self):
        line = label_vocab.atlas_panel_relations_line(
            [("depends", "A", "B", "2026.7"), ("adjacent", "A", "C", "")]
        )
        assert "「A」が「B」に依存する（版 2026.7 で加わった）" in line
        assert "「A」と「C」（隣接）。" in line or "「A」と「C」（隣接）、" in line
        # 3 要素の旧形も読める
        assert label_vocab.atlas_panel_relations_line([("depends", "A", "B")]) == (
            "地図の関係: 「A」が「B」に依存する。"
        )

    def test_no_badge_on_the_map(self):
        overlay = (JS / "atlas-overlay.js").read_text(encoding="utf-8")
        assert "で加わった" not in overlay
        assert "NEW" not in overlay


# ---------------------------------------------------------------------------
# H54: 反映済みの採用は見送れない
# ---------------------------------------------------------------------------


class _FakeSession:
    def rollback(self):
        pass

    def commit(self):
        pass

    def close(self):
        pass


class TestDismissLock:
    def _edges(self):
        from routes import atlas_edges

        return atlas_edges

    def _patch(self, monkeypatch, mod, *, draft_edges, frozen_edges, status="accepted"):
        monkeypatch.setattr(mod, "_session", lambda: _FakeSession())
        decision = {"status": status, "decided_by": "u1", "edge_kind": "depends"}
        monkeypatch.setattr(mod.edge_store, "get_decision", lambda _s, _k: decision)
        called = {}

        def _decide(*_a, **_k):
            called["decide"] = True
            return {"decision": decision}

        monkeypatch.setattr(mod.edge_store, "decide", _decide)
        draft = (
            {"skeleton": SimpleNamespace(edges=[_edge(*e) for e in draft_edges]), "revision": 1}
            if draft_edges is not None
            else None
        )
        monkeypatch.setattr(mod.atlas_store, "load_draft", lambda _s, _c: draft)
        frozen = SimpleNamespace(version="2026.7", edges=[_edge(*e) for e in frozen_edges])
        monkeypatch.setattr(mod.atlas_store, "load_learner_skeleton", lambda _c, _s=None: frozen)
        return called

    def _body(self, mod, action="dismiss"):
        return mod.DecideEdgeCandidateRequest(
            edge_key="edge|astro|a|b", action=action, review_note="理由"
        )

    def test_in_draft_is_409(self, monkeypatch):
        from fastapi import HTTPException

        mod = self._edges()
        called = self._patch(monkeypatch, mod, draft_edges=[("a", "b")], frozen_edges=[])
        with pytest.raises(HTTPException) as exc:
            mod.decide_atlas_edge_candidate("astro", self._body(mod), {"id": "u1"})
        assert exc.value.status_code == 409
        assert exc.value.detail == display.EDGE_DISMISS_LOCKED_IN_DRAFT
        assert "decide" not in called

    def test_in_frozen_without_draft_is_409(self, monkeypatch):
        from fastapi import HTTPException

        mod = self._edges()
        self._patch(monkeypatch, mod, draft_edges=None, frozen_edges=[("b", "a")])
        with pytest.raises(HTTPException) as exc:
            mod.decide_atlas_edge_candidate("astro", self._body(mod), {"id": "u1"})
        assert exc.value.status_code == 409
        assert "版 2026.7" in exc.value.detail

    def test_removed_from_draft_can_be_dismissed(self, monkeypatch):
        mod = self._edges()
        called = self._patch(monkeypatch, mod, draft_edges=[], frozen_edges=[("a", "b")])
        monkeypatch.setattr(mod, "_record_edge_event", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_project_own_decision", lambda d, _u: d)
        monkeypatch.setattr(mod, "_decide_result_line", lambda *a, **k: "")
        mod.decide_atlas_edge_candidate("astro", self._body(mod), {"id": "u1"})
        assert called.get("decide")

    def test_card_flags_and_ui_hides_dismiss(self):
        mod = self._edges()
        cand = {
            "from_id": "a", "to_id": "b",
            "decision": {"status": "accepted", "edge_kind": "depends"},
        }
        out = mod._annotate_candidate(cand, {"a": "A", "b": "B"}, {("a", "b")})
        assert out["dismiss_locked"] is True
        assert out["dismiss_locked_note"] == display.EDGE_DISMISS_LOCKED_IN_DRAFT
        admin = (JS / "admin.js").read_text(encoding="utf-8")
        assert "candidate && candidate.dismiss_locked" in admin
        assert "item.dismiss_locked ?" in admin


# ---------------------------------------------------------------------------
# te-01 / 右パネルの論文名
# ---------------------------------------------------------------------------


class TestFreezeAndPanelFacts:
    def test_no_correspondence_needed_fact(self):
        frozen = SimpleNamespace(version="2026.7")
        assert atlas_correspondence.no_correspondence_needed_fact(
            {"candidates": [], "unmatched_removed": [], "already_declared": []}, frozen
        ) == atlas_correspondence.FACT_NO_CORRESPONDENCE_NEEDED
        assert atlas_correspondence.no_correspondence_needed_fact(
            {"candidates": [{"from_id": "x"}]}, frozen
        ) == ""
        assert atlas_correspondence.no_correspondence_needed_fact({}, None) == ""

    def test_papers_line_names_with_provenance_no_count(self):
        line = label_vocab.atlas_panel_papers_line(
            [("論文甲", "教員確認済み"), ("論文乙", "AIによる推定（未確認）")]
        )
        assert line == (
            "ここに置かれているこのコースの論文: 『論文甲』（教員確認済み）、『論文乙』（AIによる推定（未確認））。"
        )
        many = label_vocab.atlas_panel_papers_line([(f"論文{chr(0x3042 + i)}", "") for i in range(6)])
        assert many.endswith("、ほか。")
        assert not re.search(r"[0-9０-９]", many)
        assert label_vocab.atlas_panel_papers_line([]) == ""

    def test_papers_by_concept_uses_provenance(self, monkeypatch):
        from core import document_refs
        from routes import atlas_view

        monkeypatch.setattr(
            document_refs, "resolve_paper_titles", lambda _s, ids, **_k: {i: f"題{i}" for i in ids}
        )
        rows = [
            {"domain_key": "astro", "node_id": "c1", "document_id": "d2", "status": "inferred"},
            {"domain_key": "astro", "node_id": "c1", "document_id": "d1", "status": "confirmed"},
            {"domain_key": "other", "node_id": "c1", "document_id": "d3", "status": "confirmed"},
            {"domain_key": "astro", "node_id": "old", "document_id": "d4", "status": "confirmed"},
        ]
        got = atlas_view.course_papers_by_concept(
            None, rows, domain_key="astro", concept_ids={"c1", "c2"},
            resolve=lambda nid: "c2" if nid == "old" else "",
        )
        assert got["c1"] == [("題d1", "教員確認済み"), ("題d2", "AIによる推定（未確認）")]
        assert got["c2"] == [("題d4", "教員確認済み")]

    def test_panel_js_renders_papers(self):
        panel = (JS / "atlas-panel.js").read_text(encoding="utf-8")
        assert "info.papers" in panel and "atlas-panel-papers" in panel
