"""第 25 周 面 B 第 7 波（IK-1048 / IK-1049 / IK-1028 残り / IK-1021 doubt 側 / IK-1013）の固定。

- IK-1048: 観測からの支持線（support_paths）も、負荷・反実仮想（IK-1030）と同じ判定の正本
  （``dependency.is_claim_sequence_edge`` + ``claim_sequence_node_ids``）で主張の出現順の辺を数えない。
  先に「同じ台帳行の支持線を辺あり / なしで並べる」読み取りで仮説を確かめる。ゼミ前ブリーフ・
  反証条件の候補の入力も同じ除外を通す。
- IK-1049: 音声・casual でも利用者が地図で選んだ場所の事実を渡す（route 側は
  ``test_assistant_context_learning_route.py::TestModeMatrix``）。ここでは解決器の純関数。
- IK-1028 残り: 確定済みの前提のカードに結ばれた論文の題名の事実文。
- IK-1021 doubt 側: 後継の決まらない ⚓ は凍結時の部品名と事実文を 200 で返す。
- IK-1013: 開幕の問いが生の "unknown" のときは問いとして出さず事実文 1 行。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), str(_BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.doubt as doubt  # noqa: E402
import routes.learning as learning_routes  # noqa: E402
from core import label_vocab  # noqa: E402
from core.assistant_context.resolvers import learning as resolvers_learning  # noqa: E402
from core.assistant_context.schema import ScreenContext  # noqa: E402
from core.discuss import opening  # noqa: E402
from core.doubt import population  # noqa: E402
from core.doubt import support_paths as sp  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_ATLAS_JS = (_ROOT / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
_APP_JS = (_ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
_DISCUSS_JS = (_ROOT / "frontend" / "public" / "js" / "discuss.js").read_text(encoding="utf-8")
_ADMIN_HTML = (_ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
_INDEX_HTML = (_ROOT / "frontend" / "public" / "index.html").read_text(encoding="utf-8")
_SEMINAR_SRC = (_BACKEND / "core" / "doubt" / "seminar_brief.py").read_text(encoding="utf-8")
_FALSIFY_SRC = (
    _BACKEND / "core" / "doubt" / "falsification_conditions" / "input_builder.py"
).read_text(encoding="utf-8")


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


# 論文 1 本。序論の背景文 2 つ（claim_bg1 / claim_bg2）は主張の出現順の並び（claim チェーン）の
# ノード s1 / s2 に、式どうしの依存の主張 claim_eq は式の手順 e1 に、観測の主張 claim_obs は
# 式チェーンの o1 に結ばれている。出現順の辺 s1→e1 / s2→e1 が「観測から支える経路」に数えられると
# 3 本の独立した線になり、外すと o1→e1 の 1 本だけが残る（run の「複数の独立した支持線」の形）。
_SEQ = {"s1", "s2"}
_GRAPH = {
    "nodes": [
        {"component_id": "s1", "graph_layer": "equation_detail", "linked_claim_ids": ["claim_bg1"],
         "label": "背景文 1"},
        {"component_id": "s2", "graph_layer": "equation_detail", "linked_claim_ids": ["claim_bg2"],
         "label": "背景文 2"},
        {"component_id": "o1", "graph_layer": "equation_detail", "linked_claim_ids": ["claim_obs"],
         "label": "観測の手順"},
        {"component_id": "e1", "graph_layer": "equation_detail", "linked_claim_ids": ["claim_eq"],
         "label": "式の手順"},
    ],
    "edges": [
        {"source": "s1", "target": "e1", "source_backing_status": "source_backed"},
        {"source": "s2", "target": "e1", "source_backing_status": "source_backed"},
        {"source": "o1", "target": "e1", "source_backing_status": "source_backed"},
    ],
}
_OBS_TARGETS = [
    {"claim_id": "claim_bg1", "label": "Dust polarization studies …", "identified_via": "claim_type"},
    {"claim_id": "claim_bg2", "label": "Overall, Cep B exhibits …", "identified_via": "claim_type"},
    {"claim_id": "claim_obs", "label": "観測された強度", "identified_via": "dsl"},
]


class _GraphSession:
    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM theory_component_graphs" in sql:
            if "AS document_id" in sql:
                return _Rows([("doc-a", json.dumps(_GRAPH))])
            return _Rows([(json.dumps(_GRAPH),)])
        if "FROM epistemic_ledger" in sql:
            return _Rows([("claim_eq", "claim"), ("claim_obs", "claim")])
        return _Rows([])

    def rollback(self):
        pass


def _resolver(_session, _doc, _payload):
    return set(_SEQ)


def _patch(monkeypatch):
    monkeypatch.setattr(sp, "observation_claim_targets", lambda *_a, **_k: list(_OBS_TARGETS))
    monkeypatch.setattr(population, "claim_sequence_ids_for_payload", _resolver)


class TestIk1048SupportLines:
    def test_read_compares_the_same_rows_with_and_without_sequence_edges(self, monkeypatch):
        """仮説の確かめ（読み取り 1 本・書き込みなし）: 出現順の辺を数えると「複数」、外すと「単一」。"""
        _patch(monkeypatch)
        rows = sp.compare_support_lines_with_and_without(_GraphSession(), document_id="doc-a")
        by_target = {r["target_id"]: r for r in rows}
        assert by_target["claim_eq"]["with_sequence_edges"] == "several"
        assert by_target["claim_eq"]["without_sequence_edges"] == "single"
        # 数値（経路数・容量）は出さない（SL4）
        assert set(by_target["claim_eq"]) == {
            "target_type", "target_id", "with_sequence_edges", "without_sequence_edges",
        }
        assert sp.compare_support_lines_with_and_without(_GraphSession(), document_id="") == []

    def test_default_context_excludes_sequence_edges_and_background_roots(self, monkeypatch):
        _patch(monkeypatch)
        lines = sp.compute_support_lines(_GraphSession(), "claim", "claim_eq", document_ids=["doc-a"])
        assert lines["level"] == "single"
        roots = [r["claim_id"] for r in lines["observation_roots"]]
        assert roots == ["claim_obs"]  # 序論の背景文は支持根に並ばない
        assert "背景文" not in lines["fact_line"]

    def test_explicit_false_keeps_the_old_reading_for_the_comparison_only(self, monkeypatch):
        _patch(monkeypatch)
        ctx = sp.build_support_context(
            _GraphSession(), document_ids=["doc-a"], exclude_claim_sequence_edges=False,
        )
        assert sp.compute_support_lines_from_context(ctx, "claim", "claim_eq")["level"] == "several"

    def test_one_judgement_for_dependency_and_adjacency(self):
        src = (_BACKEND / "core" / "doubt" / "support_paths.py").read_text(encoding="utf-8")
        assert "excluded_node_ids=frozenset(graph.claim_sequence_node_ids)" in src
        assert "is_claim_sequence_edge(source, target, excluded_node_ids)" in src
        assert "exclude_claim_sequence_edges: bool = True" in src

    def test_seminar_brief_and_falsification_input_pass_the_exclusion(self):
        assert "build_dependency_graph(session, document_ids=[document_id], exclude_claim_sequence_edges=True)" in _SEMINAR_SRC
        assert "build_dependency_graph(session, document_ids=[document_id], exclude_claim_sequence_edges=True)" in _FALSIFY_SRC


def _ctx(view):
    return ScreenContext(screen="learning", selection={}, view=view, visible_entities=[])


class TestIk1049SelectedAtlasNode:
    _VIEW = {"mode": "chat", "atlas_node_label": "中性子星", "atlas_node_id": "n_ns",
             "atlas_node_ledger_status": "unrecorded"}

    def test_selected_atlas_node_facts_without_view_mode(self):
        sources = {"atlas_node": {"node_id": "n_ns", "verify_line": "検証: 台帳に記帳なし。",
                                  "papers": [{"title": "EOS paper", "provenance_label": "AIによる推定（未確認）"}],
                                  "papers_resolved": True}}
        facts = resolvers_learning.resolve_selected_atlas_node(_ctx(self._VIEW), sources)
        text = "\n".join(facts)
        assert "分野の地図で「中性子星」を開きました" in text
        assert "AIによる推定（未確認）" in text
        assert "を見ています" not in text  # 表示モードの事実は含まない

    def test_no_selection_no_facts(self):
        assert resolvers_learning.resolve_selected_atlas_node(_ctx({"mode": "chat"}), {}) == []

    def test_not_a_registered_kind(self):
        from core.assistant_context import registered_kinds

        assert "atlas_node" not in registered_kinds("learning")

    def test_route_passes_the_selection_only_for_casual(self):
        src = (_BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert "selected_atlas_node=bool(_is_casual and _cycle_mode != \"elicit\")" in src
        assert "if wanted is None or include_atlas_node:" in src


class _AssumptionSession:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement, params=None):
        if "SELECT 1 FROM assumption_nodes" in str(statement):
            return _Rows([])
        return _Rows(self.rows)

    def close(self):
        pass


def _assumption_row(aid, status, document_ids):
    return (aid, "前提の文", "manual", status, "", {}, "", "理由", "course-1",
            json.dumps(document_ids), "2026-10-28", "2026-10-28")


class TestIk1028LinkedDocumentTitle:
    def test_confirmed_card_names_the_linked_paper_in_scope_only(self, monkeypatch):
        monkeypatch.setattr(doubt, "_course_scope_document_ids", lambda *_a, **_k: ["doc-a"])
        monkeypatch.setattr(doubt, "_pg_session", lambda: _AssumptionSession([
            _assumption_row("a1", "confirmed", ["doc-a"]),
            _assumption_row("a2", "confirmed", ["doc-x"]),
        ]))
        monkeypatch.setattr(doubt, "has_naive_signal", lambda *_a, **_k: False)
        monkeypatch.setattr(doubt, "_document_title_entry", lambda _s, d: {"document_title": f"題名-{d}"})
        out = doubt.list_assumptions("course-1", status="confirmed", current_user={"id": "u1", "role": "teacher"})
        a1, a2 = out["items"]
        assert a1["ledger_document_line"].startswith("結ばれた論文: 『題名-doc-a』")
        assert "ledger_document_line" not in a2  # 範囲外の論文の名前は出さない

    def test_card_renders_the_server_line(self):
        assert "a.ledger_document_line" in _ATLAS_JS
        assert "doubt-atlas.js?v=round25-doubt-" in _ADMIN_HTML  # 第 8 波で doubt8・第 9 波で doubt9 に上がる


class TestIk1021SupersededPayload:
    def test_payload_has_frozen_label_and_line(self):
        out = learning_routes._superseded_component_payload(
            {"status": "superseded", "frozen_label": "EOS network map"}, "置き換わりました", explanations=True,
        )
        assert out["available"] is False
        assert out["frozen_label"] == "EOS network map"
        assert out["frozen_label_line"] == label_vocab.frozen_component_superseded_line("EOS network map")
        assert out["explanations"] == []
        assert "id" not in out

    def test_unnamed_falls_back_to_fact_without_name(self):
        out = learning_routes._superseded_component_payload({"status": "superseded"}, "n")
        assert "frozen_label" not in out
        assert out["frozen_label_line"] == label_vocab.FROZEN_COMPONENT_SUPERSEDED_LINE_UNNAMED

    def test_popup_heading_uses_the_frozen_label(self):
        assert "data.frozen_label_line" in _APP_JS
        assert "-doubt7" in _INDEX_HTML


class TestIk1013UnfilledQuestion:
    def test_unknown_is_not_a_question(self):
        out = opening.project_thesis(
            {"central_question": "unknown", "central_thesis": {"text": "主張の文"}}, {}, {}, None,
        )
        assert out["central_question"] == ""
        assert out["central_question_note"] == opening.CENTRAL_QUESTION_UNSETTLED_NOTE

    def test_real_question_has_no_note(self):
        out = opening.project_thesis(
            {"central_question": "How does X depend on Y?", "central_thesis": {"text": "主張"}}, {}, {}, None,
        )
        assert out["central_question"] == "How does X depend on Y?"
        assert "central_question_note" not in out

    def test_unfilled_tokens(self):
        assert opening.is_unfilled_value("Unknown.")
        assert opening.is_unfilled_value("不明")
        assert not opening.is_unfilled_value("")
        assert not opening.is_unfilled_value("How does X depend on Y?")

    def test_front_renders_the_server_note(self):
        assert "thesis.central_question_note" in _DISCUSS_JS
