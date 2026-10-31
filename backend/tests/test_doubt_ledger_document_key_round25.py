"""第 25 周 面 B 第 2 周 — 台帳の行キーに論文を含める（IK-0899）と、その上に乗る是正のガードレール。

- IK-0899: 台帳 epistemic_ledger の行キーは (document_id, target_type, target_id)（migration 092）。
  グラフのノード id・式の agent ID は論文の中でしか一意でないので、読み手は document_id を
  必須のキーワード引数で受け、分からなければ閉じる。既存の衝突行は自動で振り分けず、人の記帳が
  入ったものに印（attribution_check_needed）を立てて教員に確かめさせる。
- IK-0900: 対象単位の書き込みルートはすべて対象の教材の編集権限を見る（AST で固定）。台帳 GET は
  閲覧ゲート + ``can_edit``。疑義と検証提案は閲覧できる教員なら残せる。
- IK-0901: 台帳対象の表示名の正本は ``core/doubt/target_labels.py``（document 必須）。
- IK-0902: 支持の根は対象へ届いた経路の根だけ・支持線ゼロでは出さない。
- IK-0903: 観測の選択肢は開いているグラフの論文に絞る・範囲外の観測は倒さず warnings。
- IK-0904: 式・部品の台帳には疑義の入口の理由と、支える主張の一覧を出す。
- IK-0905: 記帳面は画面の語（欄名・記帳者名・重複・無変更の事実文）。
- IK-0906: 部品の説明ポップアップは説明ゼロでも検証の一行を出す。
- IK-0907: 地図を開いた学習者の往復には、地図に出るもの・出ないものの事実を渡す。
- IK-0858: TeX の label キー（eq:mod_dcf）を印字番号として「式 (eq:mod_dcf)」にしない。
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "api"))

from fastapi import HTTPException  # noqa: E402

from api.routes import doubt  # noqa: E402
from core.doubt import ledger_targets as lt  # noqa: E402
from core.doubt import target_labels  # noqa: E402

_DOUBT_SRC = (_BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
_MIGRATION = _BACKEND / "db" / "092_epistemic_ledger_document_key.sql"
_DOC_A = "aaaaaaaa-0000-0000-0000-000000000001"
_DOC_B = "bbbbbbbb-0000-0000-0000-000000000002"


class _Result:
    def __init__(self, rows):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Session:
    """(predicate, rows) の先勝ちディスパッチ。SQL とパラメタを記録する。"""

    def __init__(self, handlers=None):
        self.handlers = list(handlers or [])
        self.calls: list[tuple[str, dict]] = []
        self.committed = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        for predicate, rows in self.handlers:
            if predicate(sql, dict(params or {})):
                return _Result(rows)
        return _Result([])

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# IK-0899: migration 092 と行キーの正本
# ---------------------------------------------------------------------------


class TestMigration092:
    def test_new_key_includes_document(self):
        sql = _MIGRATION.read_text(encoding="utf-8")
        assert "UNIQUE NULLS NOT DISTINCT (document_id, target_type, target_id)" in sql
        assert "epistemic_ledger_document_target_key" in sql

    def test_old_key_is_dropped_only_once_with_the_attribution_mark(self):
        sql = _MIGRATION.read_text(encoding="utf-8")
        # 印を立てる UPDATE は旧キーの LOOP の中だけ（新キーへ移った後は再実行されない）。
        loop = sql[sql.index("FOR old_key IN"):sql.index("END LOOP")]
        assert "SET attribution_check_needed = TRUE" in loop
        assert "DROP CONSTRAINT" in loop
        code = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        assert code.count("attribution_check_needed = TRUE") == 1

    def test_migration_does_not_delete_or_split_rows(self):
        sql = _MIGRATION.read_text(encoding="utf-8")
        code = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        assert "DELETE" not in code.upper()
        assert "INSERT" not in code.upper()

    def test_no_writer_uses_the_old_conflict_target(self):
        offenders = []
        for path in list((_BACKEND / "core").rglob("*.py")) + list((_BACKEND / "api").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "ON CONFLICT (target_id, target_type)" in text or "ON CONFLICT(target_id, target_type)" in text:
                offenders.append(str(path.relative_to(_BACKEND)))
        assert offenders == []


class TestRowFilter:
    def test_scoped_targets_need_a_document(self):
        with pytest.raises(lt.LedgerDocumentRequired):
            lt.ledger_row_filter(target_type="component", target_id="theory_op_0001", document_id=None)
        with pytest.raises(lt.LedgerDocumentRequired):
            lt.ledger_row_filter(target_type="equation", target_id="eq_dcf", document_id="not-a-uuid")

    def test_global_ids_keep_the_old_meaning(self):
        where, params = lt.ledger_row_filter(target_type="claim", target_id="c1", document_id=_DOC_A)
        assert where == "target_id = :tid AND target_type = :ttype"
        where, _ = lt.ledger_row_filter(
            target_type="component", target_id="cccccccc-0000-0000-0000-000000000003", document_id=None,
        )
        assert "document_id" not in where

    def test_two_documents_with_the_same_node_get_different_keys(self):
        _, a = lt.ledger_row_filter(target_type="component", target_id="theory_op_0001", document_id=_DOC_A)
        _, b = lt.ledger_row_filter(target_type="component", target_id="theory_op_0001", document_id=_DOC_B)
        assert a["doc"] != b["doc"]

    @pytest.mark.parametrize("fn, name", [
        (doubt._fetch_ledger_row, "document_id"),
        (doubt.learner_ledger_line, "document_id"),
        (lt.resolve_component_ledger_target, "document_id"),
    ])
    def test_readers_take_document_as_a_required_keyword(self, fn, name):
        param = inspect.signature(fn).parameters[name]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    def test_personal_map_and_labels_require_document(self):
        from core.doubt.open_assumptions import target_label
        from core.personal_graph.queries import fetch_component_ledger_statuses

        for fn in (target_label, fetch_component_ledger_statuses):
            param = inspect.signature(fn).parameters["document_id"]
            assert param.kind is inspect.Parameter.KEYWORD_ONLY
            assert param.default is inspect.Parameter.empty

    def test_fetch_row_closes_with_422_without_document(self):
        with pytest.raises(HTTPException) as exc:
            doubt._fetch_ledger_row(_Session(), "component", "theory_op_0001", document_id=None)
        assert exc.value.status_code == 422

    def test_learner_line_does_not_guess_a_document(self):
        session = _Session()
        assert doubt.learner_ledger_line(session, "equation", "eq_dcf", document_id=None) is None
        assert session.calls == []

    def test_worker_target_ref_carries_the_document(self):
        refs = lt.ledger_target_refs([("component", "theory_op_0001", _DOC_A)])
        target_type, target_id = refs[0]
        assert (target_type, target_id) == ("component", "theory_op_0001")
        assert refs[0].document_id == _DOC_A


# ---------------------------------------------------------------------------
# IK-0900: 書き込みルートのゲート（AST）
# ---------------------------------------------------------------------------


def _route_functions():
    tree = ast.parse(_DOUBT_SRC)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                    and isinstance(deco.func.value, ast.Name) and deco.func.value.id == "admin_router"
                    and deco.args and isinstance(deco.args[0], ast.Constant)):
                yield node, deco.func.attr, str(deco.args[0].value)


def _calls(node: ast.FunctionDef) -> set[str]:
    return {
        n.func.id for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


class TestWriteGates:
    def test_every_ledger_write_route_checks_edit_permission(self):
        missing = []
        for fn, method, path in _route_functions():
            if not path.startswith("/ledger/{target_type}/{target_id}") or method == "get":
                continue
            calls = _calls(fn)
            if not ({"_require_editable_ledger_target", "_decide_simulation_candidate"} & calls):
                missing.append(fn.name)
            if "document_id" not in [a.arg for a in fn.args.args + fn.args.kwonlyargs]:
                missing.append(f"{fn.name}:document_id")
        assert missing == []

    def test_assumption_and_challenge_routes_are_gated(self):
        by_name = {fn.name: _calls(fn) for fn, _m, _p in _route_functions()}
        assert "_require_editable_assumption" in by_name["confirm_assumption"]
        assert "_require_editable_assumption" in by_name["dismiss_assumption"]
        assert "_ledger_target_access" in by_name["create_challenge"]
        assert "_require_viewable_challenge_target" in by_name["create_verification_proposal"]
        assert "_ledger_target_access" in by_name["get_ledger_entry"]

    def test_scoped_target_without_document_is_422_before_any_session(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", lambda: (_ for _ in ()).throw(AssertionError("no db")))
        with pytest.raises(HTTPException) as exc:
            doubt._require_editable_ledger_target("component", "theory_op_0001", {"id": "u1"})
        assert exc.value.status_code == 422

    def test_scoped_target_absent_from_the_document_is_404(self, monkeypatch):
        monkeypatch.setattr(doubt, "_pg_session", lambda: _Session())
        monkeypatch.setattr(doubt, "_require_editable_document_or_404", lambda d, u: None)
        with pytest.raises(HTTPException) as exc:
            doubt._require_editable_ledger_target(
                "component", "theory_op_0001", {"id": "u1"}, document_id=_DOC_A,
            )
        assert exc.value.status_code == 404

    def test_view_only_teacher_reads_with_can_edit_false(self, monkeypatch):
        class _Access:
            found = True
            can_view = True
            can_edit = False

        monkeypatch.setattr(doubt, "_pg_session", lambda: _Session())
        monkeypatch.setattr(doubt, "resolve_document_access", lambda uid, ref: _Access())
        out = doubt._ledger_target_access(
            "component", "theory_op_0001", {"id": "u1", "role": "teacher"}, document_id=_DOC_A,
        )
        assert out == {"document_id": _DOC_A, "can_edit": False}

    def test_unviewable_target_is_404(self, monkeypatch):
        class _Access:
            found = True
            can_view = False
            can_edit = False

        monkeypatch.setattr(doubt, "_pg_session", lambda: _Session())
        monkeypatch.setattr(doubt, "resolve_document_access", lambda uid, ref: _Access())
        with pytest.raises(HTTPException) as exc:
            doubt._ledger_target_access(
                "component", "theory_op_0001", {"id": "u1"}, document_id=_DOC_A,
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# GET の新しいキー（IK-0899 / 0900 / 0904 / 0905）
# ---------------------------------------------------------------------------


def _ledger_row(**over):
    row = {
        "id": "led-1", "target_id": "theory_op_0001", "target_type": "component",
        "document_id": _DOC_A, "course_id": "", "verification_status": "untested",
        "verification_scopes": [{"scope_id": "s1", "domain": "低温", "recorded_by": "user-1",
                                 "evidence_quote": "q", "reason": "r"}],
        "scope_candidates": [], "consensus_explicit": {}, "consensus_behavioral": 0,
        "load_score": None, "created_at": "", "updated_at": "", "falsification_conditions": [],
        "falsification_candidates": [], "evidence_lines": [], "attribution_check_needed": True,
    }
    row.update(over)
    return tuple(row.values())


class TestLedgerEntryKeys:
    def _get(self, monkeypatch, *, can_edit, row=None):
        session = _Session([
            (lambda sql, p: "FROM epistemic_ledger" in sql and "SELECT id::text, target_id" in sql,
             [row or _ledger_row()]),
        ])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(
            doubt, "_ledger_target_access",
            lambda *a, **k: {"document_id": _DOC_A, "can_edit": can_edit},
        )
        monkeypatch.setattr(doubt, "compute_support_lines", lambda *a, **k: None)
        monkeypatch.setattr(doubt, "ledger_target_label", lambda *a, **k: "理論の土台")
        monkeypatch.setattr(doubt, "_user_display_names", lambda s, ids: {"user-1": "浦川"})
        monkeypatch.setattr(
            doubt, "supporting_claims_for_target",
            lambda *a, **k: [{"claim_id": "c1", "label": "主張の本文"}],
        )
        return doubt.get_ledger_entry(
            "component", "theory_op_0001", document_id=_DOC_A, current_user={"id": "u1"},
        ), session

    def test_row_is_fetched_with_the_open_document(self, monkeypatch):
        _out, session = self._get(monkeypatch, can_edit=True)
        select = [p for sql, p in session.calls if "SELECT id::text, target_id" in sql]
        assert select and select[0]["doc"] == _DOC_A

    def test_read_only_teacher_gets_the_fact(self, monkeypatch):
        out, _ = self._get(monkeypatch, can_edit=False)
        assert out["can_edit"] is False
        assert out["can_edit_note"] == doubt.LEDGER_READ_ONLY_NOTE

    def test_attribution_check_note_and_recorder_name(self, monkeypatch):
        out, _ = self._get(monkeypatch, can_edit=True)
        assert out["attribution_note"] == lt.LEDGER_ATTRIBUTION_CHECK_NOTE
        assert out["verification_scopes"][0]["recorded_by_label"] == "浦川"
        assert out["target_label"] == "理論の土台"

    def test_component_ledger_points_to_supporting_claims(self, monkeypatch):
        out, _ = self._get(monkeypatch, can_edit=True)
        assert out["challenge_allowed"] is False
        assert out["challenge_note"] == doubt.CHALLENGE_TARGETS_NOTE
        assert out["supporting_claims"] == [{"claim_id": "c1", "label": "主張の本文"}]


class TestRecordSurfaceFacts:
    def test_scope_422_uses_screen_words(self, monkeypatch):
        # IK-0999（第 5 波）: 権限の検査が入力の検査より先なので、編集権限を通した上で 422 を見る。
        monkeypatch.setattr(doubt, "_require_editable_ledger_target", lambda *a, **k: _DOC_A)
        monkeypatch.setattr(doubt, "_pg_session", lambda: (_ for _ in ()).throw(AssertionError("no db")))
        with pytest.raises(HTTPException) as exc:
            doubt.add_ledger_scope(
                "claim", "c1", doubt.ScopeCreateRequest(reason="r", evidence_quote="q"),
                current_user={"id": "u1"},
            )
        detail = exc.value.detail
        for word in ("成立条件", "領域", "精度", "系"):
            assert word in detail
        for api_name in ("condition", "domain", "precision", "system"):
            assert api_name not in detail

    def test_duplicate_scope_is_accepted_with_a_fact(self, monkeypatch):
        row = _ledger_row(target_type="claim", target_id="c1")
        session = _Session([(lambda sql, p: "SELECT id::text, target_id" in sql, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(doubt, "_require_editable_ledger_target", lambda *a, **k: _DOC_A)
        monkeypatch.setattr(doubt, "_record_doubt_event", lambda *a, **k: None)
        out = doubt.add_ledger_scope(
            "claim", "c1",
            doubt.ScopeCreateRequest(domain="低温", evidence_quote="q", reason="別の教員の判断"),
            current_user={"id": "u2"},
        )
        assert out["duplicate"] is True
        assert out["note"] == doubt.SCOPE_DUPLICATE_NOTE

    @pytest.mark.parametrize("new_status, changed", [("untested", False), ("refuted", True)])
    def test_verification_status_says_whether_it_changed(self, monkeypatch, new_status, changed):
        row = _ledger_row(target_type="claim", target_id="c1")
        session = _Session([(lambda sql, p: "SELECT id::text, target_id" in sql, [row])])
        monkeypatch.setattr(doubt, "_pg_session", lambda: session)
        monkeypatch.setattr(doubt, "_require_editable_ledger_target", lambda *a, **k: _DOC_A)
        monkeypatch.setattr(doubt, "_record_doubt_event", lambda *a, **k: None)
        out = doubt.put_verification_status(
            "claim", "c1", doubt.VerificationStatusRequest(verification_status=new_status, reason="r"),
            current_user={"id": "u1"},
        )
        assert out["changed"] is changed
        assert out["note"] == (
            doubt.VERIFICATION_STATUS_CHANGED_NOTE if changed else doubt.VERIFICATION_STATUS_UNCHANGED_NOTE
        )
        update = [p for sql, p in session.calls if sql.strip().startswith("UPDATE epistemic_ledger")]
        assert update and update[0]["lid"] == "led-1"


# ---------------------------------------------------------------------------
# IK-0901: 表示名の正本
# ---------------------------------------------------------------------------


class TestTargetLabels:
    def test_node_and_equation_names_come_from_the_paper(self, monkeypatch):
        class _Readable:
            def __init__(self, session, document_id):
                self.equation_records = {"eq_dcf": {"label": "12"}}
                self.node_labels = {"theory_op_0001": "理論の土台: 主張の本文"}

        import core.doubt.seminar_brief as sb

        monkeypatch.setattr(sb, "_ReadableLabels", _Readable)
        labels = target_labels.LedgerTargetLabels(_Session())
        assert labels.label("equation", "eq_dcf", document_id=_DOC_A) == "式 (12)"
        assert labels.label("component", "theory_op_0001", document_id=_DOC_A) == "理論の土台: 主張の本文"

    def test_unreadable_targets_never_show_the_internal_id(self):
        labels = target_labels.LedgerTargetLabels(_Session())
        assert labels.label("component", "theory_op_0001", document_id=None) == target_labels.UNNAMED_TARGET_LABEL
        assert labels.label("claim", "c1", document_id=None) == target_labels.UNNAMED_TARGET_LABEL

    def test_learner_list_folds_same_statements_and_puts_unnamed_last(self):
        from core.doubt.seminar_brief import MERGED_INDISTINCT_NOTE

        items = [
            {"statement": target_labels.UNNAMED_TARGET_LABEL, "target_type": "equation"},
            {"statement": "前提 A", "target_type": "component"},
            {"statement": "前提 A", "target_type": "component"},
        ]
        out = doubt._fold_learner_open_assumptions(items)
        assert [i["statement"] for i in out] == ["前提 A", target_labels.UNNAMED_TARGET_LABEL]
        assert out[0]["merged_note"] == MERGED_INDISTINCT_NOTE


# ---------------------------------------------------------------------------
# IK-0902: 支持の根
# ---------------------------------------------------------------------------


class TestSupportRoots:
    def _ctx(self):
        from core.doubt.dependency import DependencyGraph
        from core.doubt.support_paths import SupportPathContext

        graph = DependencyGraph(
            node_ids={"r1", "r2", "t1", "t2", "t3"},
            node_labels={n: n for n in ("r1", "r2", "t1", "t2", "t3")},
            claim_refs={"obsA": {"r1"}, "obsB": {"r2"}},
        )
        return SupportPathContext(
            graph=graph,
            obs_targets=[
                {"claim_id": "obsA", "label": "観測A", "identified_via": "claim_type"},
                {"claim_id": "obsB", "label": "観測B", "identified_via": "claim_type"},
            ],
            adjacency={"r1": {"t1"}, "r2": {"t2"}},
        )

    def test_roots_are_only_the_ones_that_reach_the_target(self):
        from core.doubt.support_paths import compute_support_lines_from_context

        ctx = self._ctx()
        t1 = compute_support_lines_from_context(ctx, "component", "t1")
        t2 = compute_support_lines_from_context(ctx, "component", "t2")
        assert [r["claim_id"] for r in t1["observation_roots"]] == ["obsA"]
        assert [r["claim_id"] for r in t2["observation_roots"]] == ["obsB"]

    def test_no_support_line_means_no_roots(self):
        from core.doubt.support_paths import compute_support_lines_from_context

        out = compute_support_lines_from_context(self._ctx(), "component", "t3")
        assert out["level"] == "none"
        assert out["observation_roots"] == []


# ---------------------------------------------------------------------------
# IK-0903: 観測の選択肢の範囲
# ---------------------------------------------------------------------------


class TestObservationTargetsScope:
    def test_document_outside_the_course_is_404(self, monkeypatch):
        monkeypatch.setattr(doubt, "_course_scope_document_ids", lambda c, u: [_DOC_A])
        with pytest.raises(HTTPException) as exc:
            doubt.get_observation_targets("course-1", document_id=_DOC_B, current_user={"id": "u1"})
        assert exc.value.status_code == 404

    def test_open_document_narrows_the_choices(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(doubt, "_course_scope_document_ids", lambda c, u: [_DOC_A, _DOC_B])
        monkeypatch.setattr(doubt, "_pg_session", lambda: _Session())

        def _targets(_s, *, document_ids):
            seen["ids"] = list(document_ids)
            return []

        monkeypatch.setattr(doubt, "observation_claim_targets", _targets)
        doubt.get_observation_targets("course-1", document_id=_DOC_B, current_user={"id": "u1"})
        assert seen["ids"] == [_DOC_B]


# ---------------------------------------------------------------------------
# IK-0904: 支える主張
# ---------------------------------------------------------------------------


class TestSupportingClaims:
    def test_component_node_claims_resolve_agent_ids_to_live_uuids(self):
        """IK-0930: グラフの主張参照が局所 ID（claim_span_*）でも live の UUID へ解決する。

        UUID 参照・局所 ID 参照のどちらも同じ論文の live 行に当たれば採り、どの行にも
        当たらない参照（claim_span_9）は落とす（推測で結ばない）。
        """
        claim = "dddddddd-0000-0000-0000-000000000004"
        claim2 = "dddddddd-0000-0000-0000-000000000005"
        graph = {"nodes": [{
            "component_id": "theory_op_0001",
            "linked_claim_ids": [f"claim:{claim}", "claim_span_1", "claim_span_9"],
        }]}
        session = _Session([
            (lambda sql, p: "FROM theory_component_graphs" in sql, [(graph,)]),
            (lambda sql, p: "FROM theory_claims_live" in sql and ":doc" in sql, [
                (claim, "synth_claim_0001", "最初の主張", {}),
                (claim2, "claim_span_1", "二つめの主張", {}),
            ]),
        ])
        out = lt.supporting_claims_for_target(
            session, document_id=_DOC_A, target_type="component", target_id="theory_op_0001",
        )
        assert [c["claim_id"] for c in out] == [claim, claim2]
        assert [c["label"] for c in out] == ["最初の主張", "二つめの主張"]

    def test_without_document_nothing_is_read(self):
        session = _Session()
        assert lt.supporting_claims_for_target(
            session, document_id=None, target_type="component", target_id="theory_op_0001",
        ) == []
        assert session.calls == []


# ---------------------------------------------------------------------------
# IK-0907: 地図の画面の事実
# ---------------------------------------------------------------------------


class TestPersonalMapScreenFact:
    def test_view_fact_only_when_the_map_was_opened(self):
        from core.assistant_context.resolvers.learning import resolve_view
        from core.assistant_context.schema import (
            LEARNING_PERSONAL_MAP_SCREEN_FACT,
            normalize_screen_context,
        )

        opened = normalize_screen_context(
            {"screen": "learning", "selection": {}, "view": {"mode": "chat", "personal_map_seen": True}}
        )
        closed = normalize_screen_context({"screen": "learning", "selection": {}, "view": {"mode": "chat"}})
        assert any(LEARNING_PERSONAL_MAP_SCREEN_FACT in f for f in resolve_view(opened, {}))
        assert not any(LEARNING_PERSONAL_MAP_SCREEN_FACT in f for f in resolve_view(closed, {}))


# ---------------------------------------------------------------------------
# IK-0858: TeX の label キーは印字番号ではない
# ---------------------------------------------------------------------------


class TestTexLabelIsNotPrinted:
    def test_equation_label_does_not_show_tex_keys(self):
        from core.graph_paper_layer.schema import equation_display_label
        from core.theory_modules.schema import equation_display_name

        assert "eq:mod_dcf" not in equation_display_label({"label": "eq:mod_dcf"})
        assert "eq:mod_dcf" not in equation_display_name({"label": "eq:mod_dcf"})
        assert equation_display_label({"label": "12"}) == "式 (12)"


# ---------------------------------------------------------------------------
# フロント（静的）
# ---------------------------------------------------------------------------


class TestFrontStatic:
    def test_doubt_atlas_builds_every_ledger_path_through_one_helper(self):
        js = (_REPO / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
        assert js.count('"/admin/doubt/ledger/"') == 1  # ledgerPath の中だけ
        assert 'data-doubt-document' in js
        assert "p.document_id" in js and "item.document_id" in js
        assert "recorded_by_label" in js and "s.recorded_by ||" not in js
        assert "can_edit === false" in js and "attribution_note" in js
        assert "supporting_claims" in js
        assert "observation-targets\" +" in js and "cfState.documentId" in js

    def test_studio_passes_the_open_document(self):
        js = (_REPO / "frontend" / "public" / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
        assert 'renderNodeLedgerSection(detail, "component", nodeId, lsGraphActiveDocumentId()' in js

    def test_component_popup_shows_the_ledger_line_without_explanations(self):
        js = (_REPO / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
        start = js.index("async function showComponentExplanations(")
        body = js[start: js.index("\n  }\n", start)]
        empty = body.index("if (!items.length) {")
        assert body.index('appendLearnerLedgerLine(body, "component", componentId)') > empty
        assert body.index('appendLearnerLedgerLine(body, "component", componentId)') < body.index("return;", empty)

    def test_learner_ledger_fetch_carries_the_focus_document(self):
        js = (_REPO / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
        start = js.index("async function fetchLearnerLedgerFactLine(")
        body = js[start: js.index("\n  }\n", start)]
        assert "focusQuerySuffix(" in body
        assert "personal_map_seen" in js
