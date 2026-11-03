"""第 25 周 第 10 波（面 B・doubt）の是正のガードレール。

- IK-1120: 合意リストの支持線は行の対象の document を範囲にする（台帳と同じ — IK-0852）。
  「到達可能性」は覆る条件の量で、検証提案の到達可能性とは欄名で言い分ける。
- IK-1121: 検証提案の完了に帰属・日時・根拠（completed_by / completed_at / completion_note・
  migration 095）を残し、台帳のカードと下流（合意リスト・学習者の一覧・開幕の「確かめていないこと」）
  に段階の語で届ける。完了しても検証状態は変えない（事実文）。
- IK-1122: 復帰位置の既定値 0 を構造帰属が「見ていた区画」と読まない。
- 頭脳 STILL: 学習者の台帳の一行・前提一覧の札は「検証の記録」の語。
"""

from __future__ import annotations

import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), str(_BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.doubt as doubt  # noqa: E402
import routes.learning as learning_routes  # noqa: E402
from core import label_vocab  # noqa: E402
from core.discuss import opening  # noqa: E402
from core.doubt import open_assumptions as oa  # noqa: E402
from core.learning_experience import build_position_anchor  # noqa: E402
from core.structure_anchor.worker import reported_segment_id  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_ATLAS_JS = (_ROOT / "frontend" / "public" / "js" / "doubt-atlas.js").read_text(encoding="utf-8")
_APP_JS = (_ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
_ADMIN_HTML = (_ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
_MIGRATION_095 = (_BACKEND / "db" / "095_verification_proposal_completion.sql").read_text(encoding="utf-8")

_DOC_A = "1771872a-0000-4000-8000-000000000000"
_DOC_B = "2771872a-0000-4000-8000-000000000000"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _NoExclusion:
    excluded = False

    def excludes(self, *_a):
        return False


class _OpenSession:
    """2 論文に 1 行ずつ。提案は A の行に完了 1・B の行に未完了 1。"""

    def execute(self, statement, params=None):
        sql = str(statement)
        params = dict(params or {})
        if "FROM epistemic_ledger" in sql and "verification_scopes" in sql:
            return _Rows([
                ("cl-a", "claim", "untested", [], 3, 9.0, [], _DOC_A),
                ("cl-b", "claim", "untested", [], 3, 9.0, [], _DOC_B),
            ])
        if "FROM verification_proposals" in sql and "COUNT(*)" in sql:
            assert "FILTER (WHERE vp.status = 'completed')" in sql
            return _Rows([(0, 1)] if params.get("tid") == "cl-a" else [(1, 0)])
        return _Rows([])

    def rollback(self):
        pass


@pytest.fixture
def compiled(monkeypatch):
    built: list[list[str]] = []

    def _build(_session, *, document_ids):
        built.append(list(document_ids))
        return {"docs": list(document_ids)}

    def _lines(ctx, _t, _id):
        return {"level": "single" if len(ctx["docs"]) == 1 else "several"}

    monkeypatch.setattr(oa, "load_percentiles", lambda *_a, **_k: (1.0, 2.0, 3.0))
    monkeypatch.setattr(oa, "build_support_context", _build)
    monkeypatch.setattr(oa, "compute_support_lines_from_context", _lines)
    monkeypatch.setattr(oa, "has_naive_signal", lambda *_a, **_k: False)
    import core.doubt.target_labels as tl

    class _Labels:
        def __init__(self, *_a):
            pass

        def label(self, *_a, **_k):
            return "対象"

    monkeypatch.setattr(tl, "LedgerTargetLabels", _Labels)
    items = oa.compile_open_assumptions(
        _OpenSession(), "c1", document_ids=[_DOC_A, _DOC_B], exclusion=_NoExclusion(),
    )
    return built, {i["target_id"]: i for i in items}


class TestIk1120SupportLineScope:
    def test_support_context_is_built_per_row_document(self, compiled):
        built, items = compiled
        # コースの 2 本を範囲にせず、行の論文ごとに 1 度ずつ（台帳と同じ範囲）
        assert sorted(built) == [[_DOC_A], [_DOC_B]]
        assert items["cl-a"]["support_line_level"] == "single"
        assert items["cl-b"]["support_line_level"] == "single"

    def test_reachability_field_names_are_split(self):
        labels = label_vocab.LEDGER_FIELD_LABELS
        assert labels["falsification_reachability"] == "覆る条件の到達可能性"
        assert labels["proposal_reachability"] == "検証提案の到達可能性"
        assert 'fieldLabel(fs, "falsification_reachability", "覆る条件の到達可能性")' in _ATLAS_JS
        assert "検証提案の到達可能性: " in _ATLAS_JS
        assert "'<span class=\"doubt-badge\">到達可能性: '" not in _ATLAS_JS

    def test_list_carries_scope_note(self):
        src = inspect.getsource(doubt.get_open_assumptions)
        assert '"support_scope_note": OPEN_ASSUMPTIONS_SUPPORT_SCOPE_NOTE' in src
        assert "data-doubt-support-scope-note" in _ATLAS_JS
        assert not any(ch.isdigit() for ch in label_vocab.OPEN_ASSUMPTIONS_SUPPORT_SCOPE_NOTE)


class TestIk1121ProposalCompletion:
    def test_list_distinguishes_completed_proposals(self, compiled):
        _built, items = compiled
        assert items["cl-a"]["verification_proposal_state"] == "completed"
        assert items["cl-a"]["verification_proposal_label"] == "検証提案あり（完了）"
        assert items["cl-a"]["has_verification_proposal"] is True
        assert items["cl-b"]["verification_proposal_label"] == "検証提案あり"
        assert label_vocab.verification_proposal_presence(1, 1) == "mixed"
        assert label_vocab.verification_proposal_presence(0, 0) == ""

    def test_completed_line_has_name_and_jst_time(self):
        at = datetime(2026, 10, 31, 5, 40, tzinfo=timezone.utc)
        assert label_vocab.proposal_completed_line("岡村 千尋", at) == "完了: 岡村 千尋（2026-10-31 14:40）"
        assert label_vocab.proposal_completed_line("", None) == "完了: 名前の登録がありません（日時の記録なし）"
        assert label_vocab.proposal_completion_note_line("  ") == "確かめたことの記帳なし"
        assert label_vocab.proposal_completion_note_line("X 線で確認") == "確かめたこと: X 線で確認"

    def test_patch_completed_records_actor_time_and_note(self, monkeypatch):
        calls = []

        class _S:
            def execute(self, stmt, params=None):
                calls.append((str(stmt), dict(params or {})))
                if "SELECT status FROM verification_proposals" in str(stmt):
                    return _Rows([("in_progress",)])
                return _Rows([])

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(doubt, "_require_viewable_proposal_target", lambda *a, **k: None)
        monkeypatch.setattr(doubt, "_pg_session", lambda: _S())
        monkeypatch.setattr(doubt, "_record_doubt_event", lambda *a, **k: None)
        body = doubt.ProposalPatchRequest(status="completed", completion_note="  観測\x1b[0m で確認  ")
        result = doubt.patch_verification_proposal("p1", body, current_user={"id": "u-1"})
        assert result["status"] == "completed"
        sql, params = next(c for c in calls if c[0].startswith("UPDATE verification_proposals"))
        assert "completed_by = CAST(:completed_by AS uuid)" in sql
        assert "completed_at = now()" in sql
        assert params["completed_by"] == "u-1"
        assert params["completion_note"] == "観測 で確認"
        # 検証状態（台帳）は変えない（SL2 — 人が別の操作で記帳する）
        assert not any("epistemic_ledger" in c[0] for c in calls)

    def test_patch_non_completed_does_not_touch_completion_columns(self, monkeypatch):
        calls = []

        class _S:
            def execute(self, stmt, params=None):
                calls.append(str(stmt))
                if "SELECT status FROM verification_proposals" in str(stmt):
                    return _Rows([("proposed",)])
                return _Rows([])

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(doubt, "_require_viewable_proposal_target", lambda *a, **k: None)
        monkeypatch.setattr(doubt, "_pg_session", lambda: _S())
        monkeypatch.setattr(doubt, "_record_doubt_event", lambda *a, **k: None)
        doubt.patch_verification_proposal(
            "p1", doubt.ProposalPatchRequest(status="in_progress", completion_note="x"),
            current_user={"id": "u-1"},
        )
        assert not any("completed_by" in c for c in calls)

    def test_ledger_proposals_carry_completion_fields(self, monkeypatch):
        at = datetime(2026, 10, 31, 5, 40, tzinfo=timezone.utc)

        class _S:
            def execute(self, stmt, params=None):
                assert "LEFT JOIN users cu ON cu.id = vp.completed_by" in str(stmt)
                return _Rows([
                    ("p1", "ch1", "提案", "外部", "next_generation", "completed", "提案者", at, "完了者", at, ""),
                    ("p2", "ch1", "提案2", "", "unassessed", "in_progress", "提案者", at, "", None, ""),
                ])

        out = doubt._proposals_by_challenge(_S(), ["ch1"])["ch1"]
        assert out[0]["status_line"] == "完了: 完了者（2026-10-31 14:40）"
        assert out[0]["completion_note_line"] == "確かめたことの記帳なし"
        assert out[0]["status_note"] == label_vocab.PROPOSAL_COMPLETED_STATUS_UNCHANGED_NOTE
        assert "status_line" not in out[1]

    def test_migration_095_is_idempotent_and_keeps_rows(self):
        assert "ADD COLUMN IF NOT EXISTS completed_by UUID REFERENCES users(id) ON DELETE SET NULL" in _MIGRATION_095
        assert "ADD COLUMN IF NOT EXISTS completed_at" in _MIGRATION_095
        assert "ADD COLUMN IF NOT EXISTS completion_note" in _MIGRATION_095
        assert "vp.completed_at IS NULL" in _MIGRATION_095
        assert "DELETE" not in _MIGRATION_095.upper().replace("ON DELETE SET NULL", "")

    def test_cards_and_downstream_render_server_words(self):
        assert "p.status_line" in _ATLAS_JS and "p.completion_note_line" in _ATLAS_JS
        assert "data-doubt-proposal-note" in _ATLAS_JS
        assert "patchBody.completion_note = note" in _ATLAS_JS
        assert 'escHtml(item.verification_proposal_label || "検証提案あり")' in _ATLAS_JS
        assert 'item.verification_proposal_label || "検証提案あり"' in _APP_JS
        assert "doubt-atlas.js?v=round25-doubt-20261031-doubt1" in _ADMIN_HTML

    def test_opening_unverified_line_carries_proposal_word(self):
        line = opening._assumption_fact_line({
            "scope_count_is_zero": True, "has_verification_proposal": True,
            "verification_proposal_label": "検証提案あり（完了）",
        })
        assert "検証提案あり（完了）です。" in line
        plain = opening._assumption_fact_line({"scope_count_is_zero": True})
        assert "検証提案" not in plain


class TestIk1122ReturnPositionNotQuestionAnchor:
    def test_position_anchor_records_whether_segment_was_reported(self):
        assert "segment_reported" not in build_position_anchor("t1", 0, 0)
        assert build_position_anchor("t1", 0, 0, segment_reported=False)["segment_reported"] is False
        src = inspect.getsource(learning_routes)
        assert '_seg_reported = _anchor.get("segment_id") is not None' in src
        assert "build_position_anchor(topic_id, _seg, _scroll, segment_reported=_seg_reported)" in src

    @pytest.mark.parametrize("pos, expected", [
        ({"segment_id": 0, "segment_reported": False}, None),
        ({"segment_id": 0, "segment_reported": True}, 0),
        ({"segment_id": 3, "segment_reported": True}, 3),
        ({"segment_id": 0}, None),          # 旧い痕跡: 0 は既定値と区別できない
        ({"segment_id": 2}, 2),             # 1 以上はレクチャー中の報告でしか生じない
        ({}, None),
        ({"segment_id": True}, None),
        (None, None),
    ])
    def test_worker_binds_segment_only_when_reported(self, pos, expected):
        assert reported_segment_id(pos) == expected

    def test_worker_uses_the_helper(self):
        import core.structure_anchor.worker as worker

        src = inspect.getsource(worker.run_anchor_mining)
        assert "reported_segment_id(pos)" in src

    def test_app_reports_segment_only_in_lecture(self):
        start = _APP_JS.index("currentAnchor: function ()")
        body = _APP_JS[start:start + 900]
        assert "anchor.segment_id = lectureState.currentSegmentIndex || 0;" in body
        assert "segment_id: seg" not in body


class TestLearnerVerificationWording:
    def test_learner_fact_line_uses_record_words(self):
        lines = [
            doubt._learner_fact_line("claim", "unknown", []),
            doubt._learner_fact_line("claim", "indirectly_supported", []),
            doubt._learner_fact_line("claim", "refuted", []),
            doubt._learner_fact_line("claim", "untested", [{"condition": "低温"}]),
            doubt._learner_fact_line("claim", "directly_verified", [{"condition": "低温"}]),
        ]
        assert lines[0] == "この内容の検証の記録はまだありません。"
        for line in lines:
            assert "検証スコープ" not in line
            assert "記帳" not in line and "台帳" not in line

    def test_learner_list_badges_match_heading(self):
        assert 'meta.push("検証の記録なし")' in _APP_JS
        assert 'meta.push("記録された条件あり")' in _APP_JS
        assert "検証スコープの記録" not in _APP_JS


class TestIk1057AggregateCutNote:
    def test_single_line_through_aggregate_node_says_so(self):
        from core.doubt import support_paths as sp

        src = inspect.getsource(sp.compute_support_lines_from_context)
        assert 'graph.node_layers.get(m["node_id"]) or "main") == "main"' in src
        note = sp.SUPPORT_LINE_AGGREGATE_NOTE_TEMPLATE.format(labels="『診断・応用』")
        assert note == "（『診断・応用』は理論の段をまとめたノードで、この支持線はその段の集約を通ります）"
        for word in label_vocab.ATLAS_LEARNER_WORDING_DENYLIST:
            assert word not in note
