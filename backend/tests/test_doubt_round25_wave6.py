"""第 25 周 面 B 第 6 波（IK-1030 / IK-1001 残り / IK-1027 / IK-1028 / IK-1031 / IK-0853 残り③）の固定。

- IK-1030: 依存の広がり（load）と反実仮想の依存グラフは、主張を出現順に並べた claim チェーンの辺を
  数えない（オーナー判断・推奨どおり）。判定の正本は一覧の母集合（IK-0933）と同じ
  ``claim_sequence_node_ids``。先に「同じ台帳行を辺あり / なしで並べる」読み取りで仮説を確かめる。
- IK-1027: 台帳への書き込みを断る文。閲覧できる相手は 403 + 画面と同じ理由文、閲覧もできない・
  不在は日本語の同一 404（存在の有無を言わない）。
- IK-1028: 確定済みの前提のカードに台帳を開く手・出所と段階の日本語の札。
- IK-1031: 地図で開いた場所の画面文脈に、詳細パネルの検証・承認の行と置かれた論文の題名を足す。
- IK-0853 残り③: 確定済みの表示ではマイニングの候補の文を出さない。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), str(_BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.doubt as doubt  # noqa: E402
from core import label_vocab  # noqa: E402
from core.assistant_context.resolvers import learning as resolvers_learning  # noqa: E402
from core.assistant_context.schema import ScreenContext  # noqa: E402
from core.doubt import dependency  # noqa: E402
from core.doubt import load_calculator as lc  # noqa: E402
from core.doubt import population  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_ATLAS_JS = (_ROOT / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
_APP_JS = (_ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
_ADMIN_HTML = (_ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
_INDEX_HTML = (_ROOT / "frontend" / "public" / "index.html").read_text(encoding="utf-8")
_CF_SRC = (_BACKEND / "core" / "doubt" / "counterfactual.py").read_text(encoding="utf-8")
_LC_SRC = (_BACKEND / "core" / "doubt" / "load_calculator.py").read_text(encoding="utf-8")
_LEARNING_SRC = (_BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


# 論文 1 本: 序論の主張 claim_intro が claim チェーンの先頭 s1 に、式の主張 claim_eq が式の手順 e1 に
# 結ばれている。claim チェーン s1 → s2 → s3 → s4（出現順の並び）と式チェーン e1 → e2。
_SEQ = {"s1", "s2", "s3", "s4"}
_GRAPH = {
    "nodes": [
        {"component_id": "s1", "graph_layer": "equation_detail", "required_claim_ids": ["claim_intro"]},
        {"component_id": "s2", "graph_layer": "equation_detail"},
        {"component_id": "s3", "graph_layer": "equation_detail"},
        {"component_id": "s4", "graph_layer": "equation_detail"},
        {"component_id": "e1", "graph_layer": "equation_detail", "linked_claim_ids": ["claim_eq"]},
        {"component_id": "e2", "graph_layer": "equation_detail"},
    ],
    "edges": [
        {"source": "s1", "target": "s2"},
        {"source": "s2", "target": "s3"},
        {"source": "s3", "target": "s4"},
        {"source": "e1", "target": "e2"},
    ],
}
_LEDGER_ROWS = [("lid-intro", "claim_intro", "claim"), ("lid-eq", "claim_eq", "claim")]


class _GraphSession:
    def __init__(self):
        self.updates = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "FROM theory_component_graphs" in sql:
            return _Rows([("doc-a", json.dumps(_GRAPH))])
        if "FROM epistemic_ledger" in sql:
            return _Rows(list(_LEDGER_ROWS))
        if "UPDATE epistemic_ledger" in sql:
            self.updates.append((params["lid"], params["score"]))
        return _Rows([])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _resolver(_session, _doc, _payload):
    return set(_SEQ)


# ---------------------------------------------------------------------------
# IK-1030: 読み取り 1 本（仮説の確かめ）→ 是正
# ---------------------------------------------------------------------------


class TestIk1030ClaimSequenceEdges:
    def test_read_compares_same_rows_with_and_without_sequence_edges(self):
        """仮説の確かめ（純関数）: 出現順の先頭の主張だけが、辺を外すと縮む。"""
        session = _GraphSession()
        with_graph = dependency.build_dependency_graph(session, document_ids=["doc-a"])
        without_graph = dependency.build_dependency_graph(
            session, document_ids=["doc-a"], exclude_claim_sequence_edges=True,
            claim_sequence_resolver=_resolver,
        )
        rows = lc.compare_scores_with_and_without(with_graph, without_graph, _LEDGER_ROWS, {})
        assert rows == [
            {"target_type": "claim", "target_id": "claim_intro",
             "with_sequence_edges": 4.0, "without_sequence_edges": 1.0},
            {"target_type": "claim", "target_id": "claim_eq",
             "with_sequence_edges": 2.0, "without_sequence_edges": 2.0},
        ]
        assert without_graph.claim_sequence_edges_excluded is True
        assert without_graph.excluded_edge_count == 3
        # ノードと主張の参照は残す（台帳行の種は消えない）
        assert {"s1", "s2", "s3", "s4"} <= without_graph.node_ids
        assert without_graph.claim_refs["claim_intro"] == {"s1"}

    def test_session_read_uses_the_canonical_resolver(self, monkeypatch):
        monkeypatch.setattr(population, "claim_sequence_ids_for_payload", _resolver)
        rows = lc.compare_claim_sequence_edges(_GraphSession(), document_id="doc-a")
        by_target = {r["target_id"]: r for r in rows}
        assert by_target["claim_intro"]["with_sequence_edges"] > by_target["claim_intro"]["without_sequence_edges"]
        assert lc.compare_claim_sequence_edges(_GraphSession(), document_id="") == []

    def test_recompute_saves_the_value_without_sequence_edges(self, monkeypatch):
        monkeypatch.setattr(population, "claim_sequence_ids_for_payload", _resolver)
        session = _GraphSession()
        monkeypatch.setattr(lc, "get_session", lambda: session)
        out = lc.recompute_load_scores(document_ids=["doc-a"])
        assert out == {"updated": 2, "documents": 1}
        assert dict(session.updates) == {"lid-intro": 1.0, "lid-eq": 2.0}

    def test_default_graph_is_unchanged_for_other_consumers(self):
        """支持線・ゼミ前ブリーフ・反証条件の入力は従来どおり（既定は外さない）。"""
        graph = dependency.build_dependency_graph(_GraphSession(), document_ids=["doc-a"])
        assert graph.claim_sequence_edges_excluded is False
        assert graph.downstream("s1") == {"s2", "s3", "s4"}

    def test_resolver_failure_keeps_all_edges(self):
        def _boom(*_a):
            raise RuntimeError("x")

        graph = dependency.build_dependency_graph(
            _GraphSession(), document_ids=["doc-a"], exclude_claim_sequence_edges=True,
            claim_sequence_resolver=_boom,
        )
        assert graph.downstream("s1") == {"s2", "s3", "s4"}

    def test_load_and_counterfactual_pass_the_exclusion(self):
        assert "build_dependency_graph(session, document_ids=document_ids, exclude_claim_sequence_edges=True)" in _CF_SRC
        assert "build_dependency_graph(\n                session, document_ids=[document_id], exclude_claim_sequence_edges=True," in _LC_SRC
        # 伝播の規則（compute_propagation）は変えない — 区分の規則の本文は残る
        assert "def compute_propagation(graph: DependencyGraph, seeds: set[str]) -> CounterfactualResult:" in _CF_SRC

    def test_canonical_resolver_uses_theory_module_marker(self, monkeypatch):
        """判定の正本は claim_sequence_node_ids（derivation_chain の chain_type）。"""
        import core.document_pipeline.persistence as persistence

        artifacts = {"derivation_chain": {"chains": [
            {"derivation_id": "derivation_claim_0001", "chain_type": "claim_chain"},
            {"derivation_id": "derivation_eq_0001", "chain_type": "equation_chain"},
        ]}}
        monkeypatch.setattr(persistence, "document_run_artifacts", lambda *_a, **_k: artifacts)
        payload = {"nodes": [
            {"component_id": "s1", "graph_layer": "equation_detail",
             "linked_derivation_ids": ["derivation_claim_0001", "derivation_claim_0001:step_001"]},
            {"component_id": "e1", "graph_layer": "equation_detail",
             "linked_derivation_ids": ["derivation_eq_0001"]},
            {"component_id": "m1", "graph_layer": "main"},
        ]}
        session = SimpleNamespace()
        assert population.claim_sequence_ids_for_payload(session, "doc-a", payload) == {"s1"}
        assert population.claim_sequence_ids_for_payload(session, "", payload) == set()


# ---------------------------------------------------------------------------
# IK-1027: 書き込みを断る文
# ---------------------------------------------------------------------------


class _NoopSession:
    def close(self):
        pass


class TestIk1027WriteDenial:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", lambda: _NoopSession())
        monkeypatch.setattr(doubt, "_target_document_ids", lambda *a, **k: ["doc-a"])
        monkeypatch.setattr(doubt, "target_requires_document", lambda *a, **k: False)

        def _deny(*_a, **_k):
            raise HTTPException(status_code=404, detail="not found")

        monkeypatch.setattr(doubt, "_require_editable_document_or_404", _deny)

    def test_viewer_gets_403_with_the_screen_sentence(self, monkeypatch):
        monkeypatch.setattr(
            doubt, "resolve_document_access",
            lambda *_a, **_k: SimpleNamespace(found=True, can_view=True, can_edit=False),
        )
        with pytest.raises(HTTPException) as exc:
            doubt._require_editable_ledger_target("claim", "c1", {"id": "u1", "role": "teacher"})
        assert exc.value.status_code == 403
        assert exc.value.detail == label_vocab.LEDGER_READ_ONLY_NOTE
        # 画面の理由文（台帳 GET の can_edit_note）と同じ正本
        from core.doubt.scope import LEDGER_READ_ONLY_NOTE

        assert exc.value.detail is LEDGER_READ_ONLY_NOTE

    def test_non_viewer_gets_the_same_japanese_404(self, monkeypatch):
        monkeypatch.setattr(
            doubt, "resolve_document_access",
            lambda *_a, **_k: SimpleNamespace(found=True, can_view=False, can_edit=False),
        )
        with pytest.raises(HTTPException) as exc:
            doubt._require_editable_ledger_target("claim", "c1", {"id": "u1", "role": "teacher"})
        assert exc.value.status_code == 404
        assert exc.value.detail == label_vocab.LEDGER_WRITE_TARGET_NOT_FOUND_NOTE

    def test_missing_target_gets_the_same_404(self, monkeypatch):
        monkeypatch.setattr(doubt, "_target_document_ids", lambda *a, **k: [])
        with pytest.raises(HTTPException) as exc:
            doubt._require_editable_ledger_target("claim", "c1", {"id": "u1", "role": "teacher"})
        assert exc.value.status_code == 404
        assert exc.value.detail == label_vocab.LEDGER_WRITE_TARGET_NOT_FOUND_NOTE

    def test_no_english_detail_left(self):
        src = (_BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
        assert "Ledger target not found" not in src
        assert "見つからないか" in label_vocab.LEDGER_WRITE_TARGET_NOT_FOUND_NOTE
        assert "ありません" not in label_vocab.LEDGER_WRITE_TARGET_NOT_FOUND_NOTE


# ---------------------------------------------------------------------------
# IK-1028 / IK-0853 残り③
# ---------------------------------------------------------------------------


class _AssumptionSession:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT 1 FROM assumption_nodes" in sql:
            return _Rows([])
        return _Rows(self.rows)

    def close(self):
        pass


def _assumption_row(aid, status, document_ids, origin="manual"):
    return (aid, "前提の文", origin, status, "", {}, "", "理由", "course-1",
            json.dumps(document_ids), "2026-10-27", "2026-10-27")


class TestIk1028ConfirmedAssumptionCard:
    def _call(self, monkeypatch, rows, status):
        monkeypatch.setattr(doubt, "_course_scope_document_ids", lambda *_a, **_k: ["doc-b", "doc-a"])
        monkeypatch.setattr(doubt, "_pg_session", lambda: _AssumptionSession(rows))
        monkeypatch.setattr(doubt, "has_naive_signal", lambda *_a, **_k: False)
        return doubt.list_assumptions("course-1", status=status, current_user={"id": "u1", "role": "teacher"})

    def test_confirmed_card_has_ledger_document_and_japanese_badges(self, monkeypatch):
        out = self._call(monkeypatch, [
            _assumption_row("a1", "confirmed", ["doc-x", "doc-a"]),
            _assumption_row("a2", "confirmed", []),
        ], "confirmed")
        a1, a2 = out["items"]
        assert a1["ledger_document_id"] == "doc-a"  # コースの範囲の論文を優先
        assert a1["origin_label"] == "教員が手で記帳" and a1["status_label"] == "確定済み"
        assert "ledger_document_id" not in a2
        assert a2["ledger_note"] == label_vocab.ASSUMPTION_NOT_LINKED_TO_DOCUMENT_NOTE
        # IK-0853 残り③: 確定済みの表示では候補の文を出さない
        assert out["mining_note"] is None

    def test_candidate_view_keeps_mining_note_and_no_ledger_hand(self, monkeypatch):
        out = self._call(monkeypatch, [_assumption_row("a3", "candidate", ["doc-a"], origin="mined_gap")], "candidate")
        assert out["mining_note"]
        item = out["items"][0]
        assert "ledger_document_id" not in item and "ledger_note" not in item
        assert item["status_label"] == "候補（未確定）"

    def test_label_vocab_covers_migration_030_check(self):
        sql = (_BACKEND / "db" / "030_assumption_nodes.sql").read_text(encoding="utf-8") \
            if (_BACKEND / "db" / "030_assumption_nodes.sql").exists() else ""
        if sql:
            for origin in ("mined_gap", "mined_corpus", "naive_aggregate", "manual"):
                assert origin in sql
        assert set(label_vocab.ASSUMPTION_ORIGIN_LABELS) == {"mined_gap", "mined_corpus", "naive_aggregate", "manual"}
        assert set(label_vocab.ASSUMPTION_STATUS_LABELS) == {"candidate", "confirmed", "operationalized", "dismissed"}
        assert label_vocab.assumption_origin_label("weird") == label_vocab.ASSUMPTION_LABEL_UNKNOWN

    def test_card_renders_labels_and_ledger_button(self):
        src = _ATLAS_JS[_ATLAS_JS.index("function loadAssumptions"):]
        src = src[: src.index("function bindAssumptionButtons")]
        assert "escHtml(a.origin)" not in src and "escHtml(a.status)" not in src
        assert "a.origin_label" in src and "a.status_label" in src
        assert 'data-ui-anchor="doubt-atlas.assumption-open-ledger"' in src
        assert "a.ledger_note" in src
        bind = _ATLAS_JS[_ATLAS_JS.index("function bindAssumptionButtons"):]
        assert 'renderNodeLedgerSection(detail, "assumption", id, doc)' in bind
        # 第 7 波で -doubt7 に上げた（以降の波の番号であればよい）。
        assert re.search(r"doubt-atlas\.js\?v=round25-doubt-202610\d\d-doubt(?:[6-9]|1\d)\b", _ADMIN_HTML)


# ---------------------------------------------------------------------------
# IK-1031
# ---------------------------------------------------------------------------


def _ctx(view):
    return ScreenContext(screen="learning", selection={}, view=view, visible_entities=[])


class TestIk1031AtlasNodePanelFacts:
    _VIEW = {"atlas_node_label": "中性子星", "atlas_node_id": "n_ns", "atlas_node_ledger_status": "unrecorded"}

    def test_panel_lines_and_papers_become_facts(self):
        sources = {"atlas_node": {
            "node_id": "n_ns",
            "verify_line": "検証: 台帳に記帳なし。",
            "endorse_line": "承認: 承認記録なし",
            "papers": [
                {"title": "Neutron star radius", "provenance_label": "教員確認済み"},
                {"title": "EOS paper", "provenance_label": "AIによる推定（未確認）"},
            ],
            "papers_resolved": True,
        }}
        facts = resolvers_learning.resolve_view(_ctx(self._VIEW), sources)
        text = "\n".join(facts)
        assert "その場所の詳細パネルに出ている行: 検証: 台帳に記帳なし。" in text
        assert "承認: 承認記録なし" in text
        assert "『Neutron star radius』（教員確認済み）" in text
        assert "AIによる推定（未確認）" in text  # 出所ラベルを剥がさない
        assert "分野の中の位置づけ" in text

    def test_no_papers_is_said_only_when_resolved(self):
        base = {"node_id": "n_ns", "papers": []}
        facts = resolvers_learning.resolve_view(_ctx(self._VIEW), {"atlas_node": {**base, "papers_resolved": True}})
        assert any("置かれているものはありません" in f for f in facts)
        facts = resolvers_learning.resolve_view(_ctx(self._VIEW), {"atlas_node": {**base, "papers_resolved": False}})
        assert not any("置かれているもの" in f for f in facts)

    def test_mismatched_node_is_ignored(self):
        facts = resolvers_learning.resolve_view(
            _ctx(self._VIEW), {"atlas_node": {"node_id": "other", "verify_line": "検証: x。", "papers_resolved": True}}
        )
        assert not any("詳細パネル" in f for f in facts)

    def test_screen_sends_reference_only(self):
        assert "view.atlas_node_id = String(state.atlasLastNode.node_id)" in _APP_JS
        assert "node_id: String(nodeId)" in _APP_JS
        assert "-doubt6" in _INDEX_HTML

    def test_route_resolves_inside_scope_without_new_llm_calls(self):
        helper = _LEARNING_SRC[_LEARNING_SRC.index("def _screen_atlas_node_sources"):]
        helper = helper[: helper.index("\ndef _learning_screen_sources")]
        assert "learner_landscape_for_documents(course_data, sorted(grounding_document_ids))" in helper
        assert "generate_text" not in helper and "llm" not in helper.lower().replace("fail", "")
        assert "_SCREEN_ATLAS_NODE_MAX_PAPERS" in helper
