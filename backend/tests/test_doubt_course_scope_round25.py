"""第 25 周 面 B（D層の教員側 ⇄ 学生の検証状態）の是正のガードレール（2026-10-23）。

- IK-0852: D層のコース範囲の正本は「コースの教材から求めた document 集合」。core は
  ``document_ids`` を必須キーワード引数で受け、空集合は SQL を出さない。行の course_id 列で絞らない。
- IK-0857: ``/courses/{course_id}/...`` の admin ルートはすべて ``_course_scope_document_ids``
  （= 編集権限ゲート + 教材の document 集合）を通す（AST で固定）。
- IK-0855: component の台帳対象の解決は ``core/doubt/ledger_targets.py`` の 1 関数。
- IK-0858: ゼミ前ブリーフの一点吊りは切断点で束ねる。
- IK-0859: コースビルダーは毎回、最新の構成案を先頭の疑似ターンに入れる。
- IK-0860: 部品の文脈のグラフ要約は id を表示名に写してから語境界で切る。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from core.doubt import scope as doubt_scope
from core.doubt.ledger_targets import pick_graph_node_for_component
from core.component_context import readable_graph_summary

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_DOUBT_ROUTES = _BACKEND / "api" / "routes" / "doubt.py"
_DOUBT_DIR = _BACKEND / "core" / "doubt"


class _NoSqlSession:
    """SQL を 1 本でも出したら落ちる session（fail-closed の確認用）。"""

    def execute(self, *_a, **_k):  # pragma: no cover - 呼ばれたら失敗
        raise AssertionError("範囲が空なら SQL を出してはならない")

    def rollback(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# IK-0852: 範囲の正本
# ---------------------------------------------------------------------------


class TestScopePrimitive:
    def test_normalize_dedupes_strips_and_sorts(self):
        assert doubt_scope.normalize_document_ids(["b", " a ", "", None, "b"]) == ["a", "b"]
        assert doubt_scope.normalize_document_ids(None) == []
        assert doubt_scope.normalize_document_ids("x") == ["x"]

    def test_clause_is_none_for_empty_scope(self):
        assert doubt_scope.document_scope_clause([]) is None
        assert doubt_scope.document_scope_clause(None) is None

    def test_clause_binds_ids_with_any(self):
        where, params = doubt_scope.document_scope_clause(["d2", "d1"])
        assert where == "document_id::text = ANY(:doc_ids)"
        assert params == {"doc_ids": ["d1", "d2"]}


class TestCoreFunctionsRequireDocumentIds:
    """core の範囲関数は ``document_ids`` を必須キーワード引数で受ける（既定値なし）。"""

    @pytest.mark.parametrize(
        "module,name",
        [
            ("core.doubt.dependency", "build_dependency_graph"),
            ("core.doubt.observation_targets", "observation_claim_targets"),
            ("core.doubt.support_paths", "build_support_context"),
            ("core.doubt.support_paths", "compute_support_lines"),
            ("core.doubt.open_assumptions", "compile_open_assumptions"),
            ("core.doubt.load_calculator", "recompute_load_scores"),
            ("core.doubt.load_calculator", "load_percentiles"),
            ("core.doubt.assumption_mining.corpus_audit", "run_corpus_audit"),
            ("core.doubt.assumption_mining.detector", "detect_gap_clusters"),
            ("core.doubt.counterfactual", "compute_counterfactual"),
        ],
    )
    def test_document_ids_is_required_keyword_only(self, module, name):
        import importlib

        fn = getattr(importlib.import_module(module), name)
        param = inspect.signature(fn).parameters["document_ids"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    def test_no_course_id_column_filter_remains_in_scope_readers(self):
        """D層の読み出しに「course_id = :course」の範囲絞りを戻さない（出所の列は残してよい）。"""
        files = [
            _DOUBT_DIR / "dependency.py",
            _DOUBT_DIR / "observation_targets.py",
            _DOUBT_DIR / "support_paths.py",
            _DOUBT_DIR / "open_assumptions.py",
            _DOUBT_DIR / "load_calculator.py",
            _DOUBT_DIR / "assumption_mining" / "corpus_audit.py",
            _DOUBT_DIR / "assumption_mining" / "detector.py",
            _DOUBT_DIR / "scope_candidates" / "worker.py",
            _DOUBT_DIR / "falsification_conditions" / "worker.py",
        ]
        for path in files:
            src = path.read_text(encoding="utf-8")
            assert 'filters.append("course_id = :course")' not in src, path.name
            assert "WHERE course_id = :course" not in src, path.name
        routes_src = _DOUBT_ROUTES.read_text(encoding="utf-8")
        assert "WHERE course_id = :course" not in routes_src
        assert "WHERE l.course_id = :course" not in routes_src

    def test_empty_scope_issues_no_sql(self):
        from core.doubt.dependency import build_dependency_graph
        from core.doubt.observation_targets import observation_claim_targets
        from core.doubt.open_assumptions import compile_open_assumptions
        from core.doubt.load_calculator import load_percentiles, recompute_load_scores
        from core.doubt.assumption_mining.corpus_audit import run_corpus_audit
        from core.doubt.assumption_mining.detector import detect_gap_clusters

        session = _NoSqlSession()
        assert build_dependency_graph(session, document_ids=[]).node_ids == set()
        assert observation_claim_targets(session, document_ids=[]) == []
        assert compile_open_assumptions(session, "c", document_ids=[]) == []
        assert load_percentiles(session, document_ids=[]) == (0.0, 0.0, 0.0)
        assert detect_gap_clusters(session, document_ids=[]) == []
        # DB に触れずに理由を返す（get_session を呼ばない）
        assert recompute_load_scores(document_ids=[]) == {"updated": 0, "documents": 0}
        audit = run_corpus_audit(document_ids=[])
        assert audit["skipped"] is True and audit["reason"] == "no_documents"


# ---------------------------------------------------------------------------
# IK-0857: コース単位ルートのゲート（AST）
# ---------------------------------------------------------------------------


def _course_routes() -> list[ast.FunctionDef]:
    tree = ast.parse(_DOUBT_ROUTES.read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                continue
            owner = deco.func.value
            if not (isinstance(owner, ast.Name) and owner.id == "admin_router"):
                continue
            if deco.args and isinstance(deco.args[0], ast.Constant) and str(
                deco.args[0].value
            ).startswith("/courses/{course_id}/"):
                out.append(node)
    return out


class TestCourseRouteGate:
    def test_route_inventory_is_not_empty(self):
        assert len(_course_routes()) >= 10

    def test_every_course_route_passes_the_gate_first(self):
        """全コース単位 admin ルートの最初の文（docstring の後）がゲートを通す。"""
        offenders = []
        for fn in _course_routes():
            body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            names = {
                n.func.id for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            gated = bool(names & {"_course_scope_document_ids", "_require_editable_course_or_404"})
            # ゲートは session を開く・集約を呼ぶより前（先頭の文の中）にある
            first = ast.dump(body[0]) if body else ""
            first_is_gate = "_course_scope_document_ids" in first or "_require_editable_course_or_404" in first
            if not (gated and first_is_gate):
                offenders.append(fn.name)
        assert not offenders, f"ゲートを先頭で通さないコース単位ルート: {offenders}"

    def test_scope_helper_gates_then_resolves_course_sources(self):
        src = _DOUBT_ROUTES.read_text(encoding="utf-8")
        start = src.index("def _course_scope_document_ids(")
        block = src[start: src.index("\n\n\n", start)]
        assert block.index("_require_editable_course_or_404") < block.index("list_course_source_document_ids")

    def test_learner_ledger_line_intersects_course_materials(self):
        src = _DOUBT_ROUTES.read_text(encoding="utf-8")
        start = src.index("def get_learner_ledger_line(")
        block = src[start: src.index("@learning_router", start + 10)]
        assert "list_course_source_document_ids(course)" in block
        assert "target_documents & course_documents" in block


# ---------------------------------------------------------------------------
# IK-0855: 部品 → 台帳対象
# ---------------------------------------------------------------------------


class TestComponentLedgerTarget:
    def test_main_node_with_claim_overlap_wins(self):
        nodes = [
            {"component_id": "eq_op_1", "graph_layer": "equation_detail", "linked_claim_ids": ["c1", "c2"]},
            {"component_id": "theory_op_1", "graph_layer": "main", "linked_claim_ids": ["c1"]},
            {"component_id": "theory_op_2", "graph_layer": "main", "linked_claim_ids": ["c9"]},
        ]
        node = pick_graph_node_for_component(nodes, {"c1"}, set())
        assert node["component_id"] == "theory_op_1"

    def test_no_overlap_resolves_nothing(self):
        nodes = [{"component_id": "theory_op_1", "graph_layer": "main", "linked_claim_ids": ["x"]}]
        assert pick_graph_node_for_component(nodes, {"c1"}, set()) is None

    def test_debug_layer_is_never_used(self):
        nodes = [{"component_id": "dbg", "graph_layer": "debug", "linked_claim_ids": ["c1"]}]
        assert pick_graph_node_for_component(nodes, {"c1"}, set()) is None

    def test_equation_overlap_counts_and_claim_prefix_is_normalized(self):
        nodes = [
            {"component_id": "a", "graph_layer": "main", "linked_claim_ids": ["claim:c1"]},
            {"component_id": "b", "graph_layer": "main", "linked_equation_ids": ["eq_3"],
             "linked_claim_ids": ["c1"]},
        ]
        node = pick_graph_node_for_component(nodes, {"c1"}, {"eq_3"})
        assert node["component_id"] == "b"

    def test_learner_projection_routes_component_through_the_resolver(self):
        src = _DOUBT_ROUTES.read_text(encoding="utf-8")
        start = src.index("def learner_ledger_line(")
        block = src[start: src.index("@learning_router", start)]
        assert "resolve_component_ledger_target(session, target_id, document_id=document_id)" in block
        assert '"target_note"' in block

    def test_app_appends_target_note_to_ledger_line(self):
        app = (_REPO / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
        assert "data.target_note" in app


# ---------------------------------------------------------------------------
# IK-0858: 一点吊りを切断点で束ねる
# ---------------------------------------------------------------------------


class TestSingleSupportGrouping:
    def test_same_cut_targets_collapse_into_one_row(self):
        from core.doubt.seminar_brief import SINGLE_SUPPORT_GROUP_FACT, _group_single_support_by_cut

        items = [
            {"target_id": f"t{i}", "target_type": "claim", "statement": f"主張{i}",
             "fact_line": "この対象は単一の支持線に立っています。『X』が同時に崩れると…",
             "cut_key": ("n1",), "cut_labels": "観測モデル"}
            for i in range(5)
        ] + [
            {"target_id": "u", "target_type": "claim", "statement": "別の主張",
             "fact_line": "原文", "cut_key": ("n2",), "cut_labels": "式 (3)"},
        ]
        out = _group_single_support_by_cut(items)
        assert len(out) == 2
        head = out[0]
        assert head["fact_line"] == SINGLE_SUPPORT_GROUP_FACT.format(cut="観測モデル")
        assert head["statement"].endswith("ほか")  # 件数は書かない
        assert head["statement"] == "「主張0」、「主張1」、「主張2」 ほか"
        assert "件" not in head["statement"] and "件" not in head["fact_line"]
        # 1 対象だけの切断点は文面を変えない
        assert out[1]["fact_line"] == "原文"
        for item in out:
            assert "cut_key" not in item and "cut_labels" not in item


# ---------------------------------------------------------------------------
# IK-0859 / IK-0860 / IK-0854 / IK-0853
# ---------------------------------------------------------------------------


class TestCourseBuilderDraftEveryTurn:
    def test_draft_pseudo_turn_is_injected_on_every_send(self):
        admin = (_REPO / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
        start = admin.index("function sendCourseChat(text) {")
        block = admin[start: start + 4000]
        assert "chatHistory.length <= 1" not in block
        assert "if (state.courseDraft) {" in block
        assert "【現在のコース構成（JSON）】" in block


class TestGraphSummaryProjection:
    def test_node_ids_become_labels_before_truncation(self):
        text = "まず theory_op_0001 が定義を与え、ついで theory_op_0002 と eq_op_0172 がつながる。" * 6
        labels = {"theory_op_0001": "理論の基礎", "theory_op_0002": "方程式系"}
        out = readable_graph_summary(text, labels)
        assert "theory_op_0001" not in out and "「理論の基礎」" in out
        assert len(out) <= 201
        # 切れた ID の断片（数字の無い theory_op）を作らない
        import re

        assert not re.search(r"theory_op(?![_0-9])", out)

    def test_empty_summary(self):
        assert readable_graph_summary("", {}) == ""


class TestCounterfactualNamesAndScope:
    def test_warning_uses_names_not_ids(self):
        from core.doubt import counterfactual as cf

        assert "グラフ上に対応するノードが見つからなかった前提: " + ", " not in inspect.getsource(cf)
        assert cf.UNRESOLVED_WARNING_PREFIX.startswith("グラフ上で見つからなかったため")

    def test_shared_scope_labels_cover_visibility_vocabulary(self):
        from core.label_vocab import SHARED_SCOPE_LABELS

        assert set(SHARED_SCOPE_LABELS) == {"private", "group", "public"}

    def test_doubt_atlas_reads_warnings_and_scope_label(self):
        js = (_REPO / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
        assert "result.warnings" in js
        assert "shared_scope_label" in js
        assert "（共有範囲: private）" not in js
        assert '" 件";' not in js.split("function updateCfStatus", 1)[1].split("function ", 1)[0]


class TestEmptyReasonsAreServerFacts:
    def test_batch_buttons_read_the_response(self):
        js = (_REPO / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
        assert "function runBatch(path)" in js
        assert "data.note" in js
        assert "doubt-batch-status" in js
        assert "data.empty_note" in js

    def test_learner_list_separates_failure_from_empty(self):
        app = (_REPO / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
        start = app.index("async function loadLearnerOpenAssumptions()")
        block = app[start: start + 2000]
        assert "一覧を取得できませんでした。" in block
        assert "data.empty_note" in block

    def test_notes_carry_no_digits(self):
        for name in (
            "SCOPE_NO_DOCUMENTS_NOTE", "ATLAS_EMPTY_NOTE", "OPEN_ASSUMPTIONS_EMPTY_NOTE",
            "LEARNER_OPEN_ASSUMPTIONS_EMPTY_NOTE", "SECTION_EMPTY_NOTE", "BATCH_SCHEDULED_NOTE",
            "LOAD_RECOMPUTED_NOTE", "STUDIO_LEDGER_HINT",
        ):
            assert not any(ch.isdigit() for ch in getattr(doubt_scope, name)), name
        for text in doubt_scope.CORPUS_AUDIT_NOTES.values():
            assert not any(ch.isdigit() for ch in text)
