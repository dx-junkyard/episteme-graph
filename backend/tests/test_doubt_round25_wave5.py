"""第 25 周 面 B 第 5 波（IK-0998〜IK-1005）の是正の固定。

- IK-0999: 台帳の書き込みルートは権限（不在と同一の 404）を入力の検査（422）より先に見る。
- IK-1001: 依存の広がりは論文単位で計算する（同じ行を 2 範囲で計算して比べる読み取りで仮説を確かめる）。
- IK-1000 / 0998 / 1002 / 1003: 既定のコース・取り下げ済みの疑義・欄の出所・支える主張の印。
- IK-1004: 逆質問の直後の「X ってどういう意味」は前提の説明に取らない。
- IK-1005: 使い方の回答の直後の送り直し・地図の札の問い（画面文脈あり）は RAG へ。
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), str(_BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.doubt as doubt  # noqa: E402
import routes.learning as learning_mod  # noqa: E402
from core import label_vocab  # noqa: E402
from core.doubt import load_calculator as lc  # noqa: E402
from schemas import LearningChatRequest, ScreenContextPayload  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_DOUBT_SRC = (_BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
_ATLAS_JS = (_ROOT / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
_ADMIN_HTML = (_ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")


def _deny(*_a, **_k):
    raise HTTPException(status_code=404, detail="not found")


def _no_db():
    raise AssertionError("権限の拒否より先に DB を開いた")


# ---------------------------------------------------------------------------
# IK-0999
# ---------------------------------------------------------------------------


class TestIk0999GateBeforeValidation:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setattr(doubt, "_require_editable_ledger_target", _deny)
        monkeypatch.setattr(doubt, "_ledger_target_access", _deny)
        monkeypatch.setattr(doubt, "_require_viewable_challenge_target", _deny)
        monkeypatch.setattr(doubt, "_require_viewable_proposal_target", _deny)
        monkeypatch.setattr(doubt, "_require_editable_assumption", _deny)
        monkeypatch.setattr(doubt, "_require_editable_course_or_404", _deny)
        monkeypatch.setattr(doubt, "_pg_session", _no_db)

    def _status(self, fn, *args):
        with pytest.raises(HTTPException) as exc:
            fn(*args, current_user={"id": "u1", "role": "TEACHER"})
        return exc.value.status_code

    def test_incomplete_scope_without_rights_is_404(self):
        # 第 5 周 te-02 の 1 手（根拠なしの body）。権限の無い教員には 422 ではなく 404。
        assert self._status(
            doubt.add_ledger_scope, "component", "theory_op_0002", doubt.ScopeCreateRequest()
        ) == 404

    def test_other_ledger_writes_are_404_before_422(self):
        cases = [
            (doubt.put_verification_status, "claim", "c1",
             doubt.VerificationStatusRequest(verification_status="bogus")),
            (doubt.add_falsification_condition, "claim", "c1",
             doubt.FalsificationConditionCreateRequest(statement="", kind="bogus", reason="")),
            (doubt.add_evidence_line, "claim", "c1", doubt.EvidenceLineCreateRequest(line_kind="bogus")),
            (doubt.create_challenge, "claim", "c1", doubt.ChallengeCreateRequest(challenge_type="bogus", reason="")),
            (doubt.create_verification_proposal, "ch1", doubt.ProposalCreateRequest(proposal="")),
            (doubt.patch_verification_proposal, "p1", doubt.ProposalPatchRequest(status="bogus")),
            (doubt.confirm_assumption, "a1", doubt.AssumptionConfirmRequest(reason="")),
            (doubt.create_assumption_manual,
             doubt.AssumptionCreateRequest(statement="", reason="", course_id="course-x")),
        ]
        for fn, *args in cases:
            assert self._status(fn, *args) == 404, fn.__name__

    def test_gate_precedes_every_422_in_ledger_write_routes(self):
        """台帳の書き込みルートでは、権限の呼び出しが本文の 422 より前（ソースの順序で固定）。"""
        tree = ast.parse(_DOUBT_SRC)
        gates = (
            "_require_editable_ledger_target", "_ledger_target_access",
            "_require_viewable_challenge_target", "_require_viewable_proposal_target",
            "_require_editable_assumption", "_require_editable_course_or_404",
            "_require_editable_document_or_404",
        )
        checked = 0
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if not any(d.startswith(("admin_router.post", "admin_router.patch", "admin_router.put")) for d in decorators):
                continue
            if not any(("/ledger/" in d or "/challenges" in d or "/proposals" in d or "/assumptions" in d)
                       for d in decorators):
                continue
            src = ast.get_source_segment(_DOUBT_SRC, node) or ""
            body = src[src.index('"""', src.index(")") ) if '"""' in src else 0:]
            gate_pos = min((body.find(g + "(") for g in gates if body.find(g + "(") >= 0), default=-1)
            if gate_pos < 0:
                continue
            checked += 1
            # 本文の 422 で、パス（target_type）の検査より後のもの。
            body_422 = [m.start() for m in re.finditer(r"status_code=422", body)]
            path_checks = ("invalid target_type", "_require_ledger_target_type")
            for pos in body_422:
                window = body[max(0, pos - 200): pos + 120]
                if any(p in window for p in path_checks):
                    continue
                assert gate_pos < pos, node.name
        assert checked >= 10


# ---------------------------------------------------------------------------
# IK-1001
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _graph(edges):
    nodes = sorted({n for e in edges for n in e})
    return {
        "nodes": [{"component_id": n, "graph_layer": "main"} for n in nodes],
        "edges": [{"source": a, "target": b} for a, b in edges],
    }


class _GraphSession:
    """theory_component_graphs と epistemic_ledger の読みだけを返す（書き込みは記録する）。"""

    def __init__(self):
        # 2 本の論文が同じ局所 ID（theory_op_0002）を持つ。論文 A では下流が無く、論文 B では
        # 下流が 3 つある。コースの論文を 1 枚に混ぜると A の行に B の下流が繋がる。
        self.graphs = {
            "doc-a": _graph([("theory_op_0001", "theory_op_0002")]),
            "doc-b": _graph([("theory_op_0002", "theory_op_0003"), ("theory_op_0003", "theory_op_0004"),
                             ("theory_op_0004", "theory_op_0005")]),
        }
        self.ledger = {
            "doc-a": [("lid-a2", "theory_op_0002", "component")],
            "doc-b": [("lid-b2", "theory_op_0002", "component")],
        }
        self.updates = []
        self.committed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "FROM theory_component_graphs" in sql:
            ids = params.get("doc_ids") or []
            return _Rows([(d, json.dumps(self.graphs[d])) for d in ids if d in self.graphs])
        if "FROM epistemic_ledger" in sql:
            ids = params.get("doc_ids") or []
            return _Rows([row for d in ids for row in self.ledger.get(d, [])])
        if "UPDATE epistemic_ledger" in sql:
            self.updates.append((params["lid"], params["score"]))
            return _Rows([])
        return _Rows([])

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class TestIk1001LoadScope:
    def test_same_row_differs_between_document_and_course_scope(self):
        """仮説の確かめ: 同じ行を論文 1 本とコース全体の 2 範囲で計算すると値が違う。"""
        session = _GraphSession()
        rows = lc.compare_load_scopes(session, document_id="doc-a", scope_document_ids=["doc-a", "doc-b"])
        assert rows == [{
            "target_type": "component", "target_id": "theory_op_0002",
            "document_scope": 1.0, "wide_scope": 4.0,
        }]

    def test_recompute_is_per_document_whatever_the_range(self, monkeypatch):
        """解析完了時（論文 1 本）とコースの［再計算］（論文の集合）で同じ行が同じ値になる。"""
        pipeline = _GraphSession()
        monkeypatch.setattr(lc, "get_session", lambda: pipeline)
        lc.recompute_load_scores(document_ids=["doc-a"])
        course = _GraphSession()
        monkeypatch.setattr(lc, "get_session", lambda: course)
        out = lc.recompute_load_scores(document_ids=["doc-a", "doc-b"])
        assert out == {"updated": 2, "documents": 2}
        assert dict(pipeline.updates)["lid-a2"] == dict(course.updates)["lid-a2"] == 1.0
        assert dict(course.updates)["lid-b2"] == 4.0
        assert course.committed

    def test_read_threshold_still_uses_the_course_population(self):
        assert "load_percentiles(session, document_ids=document_ids, exclusion=exclusion)" in _DOUBT_SRC


# ---------------------------------------------------------------------------
# IK-1000 / IK-0998 / IK-1002 / IK-1003
# ---------------------------------------------------------------------------


class TestScreenFacts:
    def test_default_course_prefers_owner(self):
        src = _ATLAS_JS[_ATLAS_JS.index("function loadCourses"):]
        src = src[: src.index("function reloadAll")]
        assert 'c.role === "owner"' in src
        assert "c.role_label" in src
        assert src.index("ownedId") < src.index("select.selectedIndex = 1")

    def test_withdrawn_challenges_are_folded_not_dropped(self):
        assert 'data-ui-anchor="doubt-atlas.withdrawn-challenges"' in _ATLAS_JS
        assert "withdrawnChallengesHtml(entry, challenges.filter" in _ATLAS_JS
        assert '"withdrawn_challenges_heading": WITHDRAWN_CHALLENGES_HEADING' in _DOUBT_SRC
        assert '"note": CHALLENGE_WITHDRAWN_NOTE' in _DOUBT_SRC
        # 取り下げ済みの区画に操作ボタンを置かない（再開しない）。
        fold = _ATLAS_JS[_ATLAS_JS.index("function withdrawnChallengesHtml"):]
        fold = fold[: fold.index("\n  }\n")]
        assert "<button" not in fold

    def test_field_labels_come_from_label_vocab(self):
        assert '"field_labels": dict(LEDGER_FIELD_LABELS)' in _DOUBT_SRC
        assert _DOUBT_SRC.count('"field_sources_note": LEDGER_FIELD_SOURCES_NOTE') == 2
        assert "LEDGER_STATUS_UNRECORDED_WITH_SCOPES_NOTE" in _DOUBT_SRC
        for key in ("verification", "support_line", "load", "behavioral"):
            assert key in label_vocab.LEDGER_FIELD_LABELS
        assert 'fieldLabel(fs, "support_line"' in _ATLAS_JS

    def test_new_notes_are_numberless(self):
        notes = [
            label_vocab.LEDGER_FIELD_SOURCES_NOTE, label_vocab.LEDGER_STATUS_UNRECORDED_WITH_SCOPES_NOTE,
            label_vocab.SUPPORTING_CLAIMS_CHALLENGED_NOTE, label_vocab.WITHDRAWN_CHALLENGES_HEADING,
            label_vocab.CHALLENGE_WITHDRAWN_NOTE, *label_vocab.LEDGER_FIELD_LABELS.values(),
            *label_vocab.SUPPORTING_CLAIM_CHALLENGE_STATE_LABELS.values(),
        ]
        for note in notes:
            assert not re.search(r"\d", note), note

    def test_supporting_claim_state_precedence(self):
        state = doubt.supporting_claim_challenge_state
        assert state([]) is None
        assert state([("withdrawn", ["in_progress"])]) is None
        assert state([("open", [])]) == "challenged"
        assert state([("led_to_verification", ["proposed"])]) == "proposed"
        assert state([("led_to_verification", ["withdrawn"])]) == "challenged"
        assert state([("open", []), ("led_to_verification", ["in_progress"])]) == "in_progress"

    def test_supporting_claim_rows_show_the_mark(self):
        assert "c.challenge_state_label" in _ATLAS_JS
        assert "entry.supporting_challenge_note" in _ATLAS_JS

    def test_cache_bust(self):
        # 第 6 波で -doubt6 に上げた（IK-1028）。キャッシュ破りは第 5 波以降の番号であればよい。
        # 第 7 波で 20261028-doubt7 に上げた（IK-1028 残り）。日付も進むので番号だけを見る。
        assert re.search(r"doubt-atlas\.js\?v=round25-doubt-202610\d\d-doubt(?:[5-9]|1\d)\b", _ADMIN_HTML)


# ---------------------------------------------------------------------------
# IK-1004
# ---------------------------------------------------------------------------

_TOPIC = "主結果"
_PREREQ = "DCF法による磁場強度の推定"


def _gate_history():
    gate = (
        f"「{_TOPIC}」を理解するには、まず以下の前提知識を押さえる必要があります：\n\n"
        f"**{_PREREQ}**\n\nこの前提知識を理解していますか？\n"
        f"理解している場合は、その前提で「{_TOPIC}」の説明に進みます。"
        f"理解できていない場合は、まず「{_PREREQ}」から説明します。"
    )
    return [{"role": "user", "content": "主結果を教えて"}, {"role": "assistant", "content": gate}]


def _followup(message):
    topic = {"id": "t1", "title": _TOPIC, "prerequisites": [{"name": _PREREQ}]}
    course = {"id": "c1", "topics": [topic], "chapters": [], "sources": []}
    return learning_mod._prerequisite_followup(message, _gate_history(), topic, _TOPIC, course)


class TestIk1004FollowupScope:
    def test_question_about_other_subject_is_not_prereq_explanation(self):
        assert _followup("「未訪問」ってどういう意味ですか")[0] != "explain"
        assert _followup("地図の未訪問ってどういう意味？")[0] != "explain"
        assert _followup("What does unvisited mean?")[0] != "explain"

    def test_bare_negative_and_prereq_mentions_still_explain(self):
        assert _followup("いいえ")[0] == "explain"
        assert _followup("わからないので説明して")[0] == "explain"
        assert _followup("それってどういう意味？")[0] == "explain"
        assert _followup("DCF法って何ですか")[0] == "explain"


# ---------------------------------------------------------------------------
# IK-1005
# ---------------------------------------------------------------------------


def _req(message, history=None, view=None):
    payload = {"message": message, "history": history or []}
    if view is not None:
        payload["screen_context"] = ScreenContextPayload(screen="learning", view=view)
    return LearningChatRequest(**payload)


class TestIk1005ResendExit:
    def test_resend_after_usage_help_goes_to_rag(self):
        q = "地図の札はどう見ればいいですか"
        help_answer = "マニュアル本文。\n\n" + learning_mod._USAGE_HELP_FOOTER
        hist = [{"role": "user", "content": q}, {"role": "assistant", "content": help_answer}]
        assert learning_mod._is_resend_after_usage_help(_req(q + "。", hist)) is True
        assert learning_mod._is_resend_after_usage_help(_req("別の問い", hist)) is False
        assert learning_mod._is_resend_after_usage_help(_req(q)) is False
        other = [{"role": "user", "content": q}, {"role": "assistant", "content": "通常の回答"}]
        assert learning_mod._is_resend_after_usage_help(_req(q, other)) is False

    def test_screen_fact_question_needs_the_atlas_node_on_screen(self):
        view = {"atlas_node_label": "重力波", "atlas_node_status": "unvisited"}
        assert learning_mod._is_screen_fact_question(_req("この札はどういう意味？", view=view), course_id="c1")
        assert not learning_mod._is_screen_fact_question(_req("この札はどういう意味？"), course_id="c1")
        assert not learning_mod._is_screen_fact_question(_req("ボタンの使い方は？", view=view), course_id="c1")

    def test_both_routes_use_the_same_predicate(self):
        src = (_BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert src.count("_usage_help_goes_to_rag(body, course_id=course_id)") == 2
