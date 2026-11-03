"""第 25 周 面 B 第 4 波（IK-0968 / 0969 / 0970 / 0972 / 0853）の是正の固定。

- IK-0970: 段の閾値は表示の母集団（導出の手順を外した後）で取る。除外前後で分布を並べ、
  除外後の母集団では残った行が上の段に届くことを見る。
- IK-0968: 台帳 GET が疑義ごとに検証提案を返す（読み時・ラベルつき・UUID の提案者を出さない）。
- IK-0969 / 0972 / 0853: 固定文はサーバ側（core/doubt/scope.py）にあり、数値を含まない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), str(_BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.doubt.load_calculator import load_percentiles, percentiles_from_scores
from core.doubt.schema import load_level_for_score

_ROOT = Path(__file__).resolve().parents[1]
_DOUBT_ROUTES = (_ROOT / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
_OPENING_SRC = (_ROOT / "core" / "discuss" / "opening.py").read_text(encoding="utf-8")


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _LedgerSession:
    """epistemic_ledger の行（target_type, target_id, document_id, load_score）だけを返す。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def execute(self, statement, params=None):
        self.calls += 1
        return _Rows(self.rows)


class _FakeExclusion:
    def __init__(self, excluded_ids):
        self.excluded_ids = set(excluded_ids)
        self.excluded = False

    def excludes(self, target_type, target_id, document_id):
        hit = target_type == "component" and target_id in self.excluded_ids
        if hit:
            self.excluded = True
        return hit


def _population():
    # 導出の手順（claim チェーン）のノードが分布の上側を占める形（第 3 周の地図の埋まり方）。
    chain = [("component", f"chain_{i}", "d1", 100.0 + i) for i in range(20)]
    kept = [("claim", f"claim_{i}", "d1", float(i + 1)) for i in range(10)]
    return chain, kept


class TestIk0970ThresholdPopulation:
    def test_percentiles_from_scores_matches_linear_interpolation(self):
        assert percentiles_from_scores([]) == (0.0, 0.0, 0.0)
        p50, p90, p99 = percentiles_from_scores([1, 2, 3, 4, 5])
        assert p50 == 3.0
        assert abs(p90 - 4.6) < 1e-9
        assert abs(p99 - 4.96) < 1e-9

    def test_before_and_after_exclusion_distribution(self):
        chain, kept = _population()
        rows = chain + kept
        before = percentiles_from_scores([r[3] for r in rows])
        session = _LedgerSession(rows)
        exclusion = _FakeExclusion({r[1] for r in chain})
        after = load_percentiles(session, document_ids=["d1"], exclusion=exclusion)
        # 除外前の閾値では、残る主張はどれも高の段に届かない（症状の再現）。
        before_levels = {load_level_for_score(r[3], *before) for r in kept}
        assert before_levels.isdisjoint({"high", "highest"})
        # 除外後の母集団で取ると、残った主張の上側が高の段に入る。
        after_levels = {load_level_for_score(r[3], *after) for r in kept}
        assert after_levels & {"high", "highest"}
        # 閾値の計算で外しただけでは「一覧から外した」印を立てない。
        assert exclusion.excluded is False

    def test_without_exclusion_keeps_sql_path(self):
        session = _LedgerSession([(1.0, 2.0, 3.0)])
        assert load_percentiles(session, document_ids=["d1"]) == (1.0, 2.0, 3.0)

    def test_all_display_surfaces_pass_exclusion(self):
        assert "exclusion=ClaimChainExclusion(session)" in _DOUBT_ROUTES  # 台帳 GET の負荷段
        assert "load_percentiles(session, document_ids=document_ids, exclusion=exclusion)" in _DOUBT_ROUTES


class TestIk0968ProposalsInLedger:
    def test_ledger_get_attaches_proposals_per_challenge(self):
        assert '"proposals": proposals_by_challenge.get(str(c[0]), [])' in _DOUBT_ROUTES
        assert '"status_label": CHALLENGE_STATUS_LABELS.get(' in _DOUBT_ROUTES

    def test_proposals_helper_returns_labels_not_uuid(self):
        src = _DOUBT_ROUTES[_DOUBT_ROUTES.index("def _proposals_by_challenge"):]
        src = src[: src.index("\nclass ")]
        for key in ('"reachability_label"', '"status_label"', '"proposer_name"', '"external_check"'):
            assert key in src
        assert '"proposer_id"' not in src


class TestFixedNotes:
    def test_notes_are_server_side_and_numberless(self):
        from core.doubt import scope

        notes = [
            scope.FALSIFICATION_REFRESH_SCOPE_NOTE,
            scope.OPENING_UNVERIFIED_EMPTY_NOTE,
            scope.ASSUMPTION_MINING_NOTES["running"],
            scope.ASSUMPTION_MINING_NOTES["finished_none"],
            scope.ASSUMPTION_MINING_NOTES["failed"],
        ]
        for note in notes:
            assert note and not re.search(r"\d", note)

    def test_opening_uses_section_specific_empty_note(self):
        assert "OPENING_UNVERIFIED_EMPTY_NOTE" in _OPENING_SRC
        assert 'result["paper_unverified_empty_note"] = SECTION_EMPTY_NOTE' not in _OPENING_SRC

    def test_ledger_get_returns_refresh_scope_note(self):
        assert _DOUBT_ROUTES.count('"falsification_refresh_note": FALSIFICATION_REFRESH_SCOPE_NOTE') == 2


class TestIk0853MiningState:
    def test_mining_note_distinguishes_states(self, monkeypatch):
        from api.routes import doubt as routes
        from core.doubt.assumption_mining import worker

        monkeypatch.setattr(worker, "_mining_states", {})
        assert routes._mining_note_key(True, "c1") == "found"
        assert routes._mining_note_key(False, "c1") == "none_in_scope"
        worker._set_mining_state("c1", worker.MINING_STATE_RUNNING)
        assert routes._mining_note_key(False, "c1") == "running"
        worker._set_mining_state("c1", worker.MINING_STATE_FINISHED)
        assert routes._mining_note_key(False, "c1") == "finished_none"
        worker._set_mining_state("c1", worker.MINING_STATE_FAILED)
        assert routes._mining_note_key(False, "c1") == "failed"
        # 候補があれば状態より候補の事実が先
        assert routes._mining_note_key(True, "c1") == "found"


class TestIk0907AtlasNodeView:
    def _ctx(self, view):
        from core.assistant_context.schema import normalize_screen_context

        return normalize_screen_context({"screen": "learning", "selection": {}, "view": view})

    def test_atlas_node_facts_from_enum(self):
        from core.assistant_context.resolvers.learning import resolve_view
        from core.label_vocab import ATLAS_PILL_NOTES

        ctx = self._ctx({"mode": "chat", "atlas_node_label": "暗黒エネルギー", "atlas_node_status": "assumed"})
        facts = resolve_view(ctx, {})
        joined = "\n".join(facts)
        assert "「暗黒エネルギー」" in joined
        # 第 9 波（IK-1059 残り）: 札の説明は学習者の語に読み替えて渡す。
        from core.label_vocab import learner_atlas_wording

        assert learner_atlas_wording(ATLAS_PILL_NOTES["assumed"]) in joined

    def test_no_label_no_fact_and_internal_id_dropped(self):
        from core.assistant_context.resolvers.learning import resolve_view

        assert not any("分野の地図" in f for f in resolve_view(self._ctx({"atlas_node_status": "assumed"}), {}))
        ctx = self._ctx({"atlas_node_label": "theory_op_12", "atlas_node_status": "assumed"})
        assert not any("分野の地図" in f for f in resolve_view(ctx, {}))


class TestIk0971LearnerUnrecorded:
    def test_unrecorded_line_is_exists_false_with_fact(self, monkeypatch):
        from api.routes import doubt as routes

        monkeypatch.setattr(routes, "ledger_target_label", lambda *a, **kw: "式 (3)")
        line = routes._learner_unrecorded_line(object(), "equation", "eq_3", document_id="d1")
        assert line["exists"] is False
        assert line["fact_line"] == "この内容の検証の記録はまだありません。"  # IK-1059 学習者の語
        assert line["target_label"] == "式 (3)"
        assert "recorded_by" not in str(line)

    def test_route_returns_200_shape_instead_of_404(self):
        src = _DOUBT_ROUTES[_DOUBT_ROUTES.index("def get_learner_ledger_line"):]
        src = src[: src.index("\ndef _learner_unrecorded_line")]
        assert "return _learner_unrecorded_line(" in src
        assert '{"exists": True, **line}' in src


class TestIk0974MaterialRole:
    def _row(self, is_owner, is_editor, owner_name):
        return tuple([None] * 13) + (is_owner, is_editor, owner_name)

    def test_roles_and_edit_request(self):
        from api.routes import admin

        teacher = {"id": "u1", "role": "teacher"}
        own = admin._material_role_fields(self._row(True, False, "浦川"), teacher)
        assert own == {"role": "owner", "role_label": "所有"}
        ed = admin._material_role_fields(self._row(False, True, "浦川"), teacher)
        assert ed["role"] == "editor" and "edit_request_note" not in ed
        view = admin._material_role_fields(self._row(False, False, "浦川"), teacher)
        assert view["role"] == "viewer" and view["role_label"] == "閲覧のみ"
        assert "浦川" in view["edit_request_note"]
        anon = admin._material_role_fields(self._row(False, False, ""), teacher)
        assert anon["edit_request_note"] and "さん" not in anon["edit_request_note"]
        admin_user = {"id": "u9", "role": admin.ROLE_SYSTEM_ADMIN}
        assert admin._material_role_fields(self._row(False, False, "x"), admin_user)["role"] == "editor"

    def test_list_route_is_still_registered(self):
        src = (_ROOT / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
        assert '@router.get("/materials", response_model=list[MaterialOut])\ndef list_materials(' in src
