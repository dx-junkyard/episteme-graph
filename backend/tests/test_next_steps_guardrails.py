"""ガイダンス層（G層）Next Steps — fail-closed・却下保持・ガードレールの検証。

構成（backend/tests/test_admin_assistant.py に倣う）:
  - Group A: 純粋ロジック + 構造テスト（DB 不要・FastAPI 不要）。
  - Group B: TestClient（実 DB なし。DB 未接続時の fail-closed 縮退と
    dismiss/restore が next_steps ストア関数を呼ぶことだけを検証する）。
    FastAPI 未導入の環境（CI 素の Python）では skip。

観点（設計 §10）:
  - 全ルールの capability_id が registry に存在し required_role が TEACHER 以下。
  - 権限外ロール（STUDENT）で compute_next_steps を呼んでも項目が出ない（G3 fail-closed）。
    このとき DB には一切アクセスしない（session=None でも例外にならないことで確認する）。
  - dismiss / restore が行削除しない（G5/P4）— ソースに DELETE 文が無いことを構造的に検証。
  - core/admin_assistant/next_steps.py が FastAPI / LLM クライアントを import しない（G2/G7）。
  - reason / title テンプレートに禁止語彙（煽り・督促）を含まない（G6）。
  - 返却件数上限 10 件と truncated の整合（fake データでの純ロジックテスト）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import help_kb  # noqa: E402
from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant import next_steps as next_steps_mod  # noqa: E402
from core.admin_assistant.schema import ROLE_TEACHER  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

_NEXT_STEPS_SRC = (BACKEND / "core" / "admin_assistant" / "next_steps.py").read_text(encoding="utf-8")
_ROUTE_SRC = (BACKEND / "api" / "routes" / "admin_assistant.py").read_text(encoding="utf-8")

_FORBIDDEN_WORDS = ("！", "今すぐ", "急いで", "必ず", "早く", "至急")


# ===========================================================================
# Group A-1: ルールカタログ（G3 fail-closed の前提）
# ===========================================================================


class TestRuleCatalog:
    def test_all_rule_capabilities_exist_in_registry(self):
        for rule_id, rule in next_steps_mod.RULE_CATALOG.items():
            cap = caps.get_capability(rule["capability_id"])
            assert cap is not None, f"{rule_id} が参照する capability {rule['capability_id']} が未登録"

    # manual_help_kb_design.md §4-1 の供給側計器2ルールは意図的に SYSTEM_ADMIN 専用
    # （capability registry / 操作KB という運用基盤そのものの計器のため）。それ以外の
    # ルールは従来どおり TEACHER が満たせる required_role であることを検証する。
    _SYSTEM_ADMIN_ONLY_RULES = (
        next_steps_mod.RULE_ASSISTANT_KB_UNDOCUMENTED,
        next_steps_mod.RULE_MANUAL_TODO_UNRESOLVED,
    )

    def test_all_rule_capabilities_reachable_by_teacher(self):
        """TEACHER 以下（= TEACHER が満たせる）required_role であること
        （意図的な SYSTEM_ADMIN 専用ルールを除く）。"""
        for rule_id, rule in next_steps_mod.RULE_CATALOG.items():
            if rule_id in self._SYSTEM_ADMIN_ONLY_RULES:
                continue
            assert caps.can_access(rule["capability_id"], ROLE_TEACHER), (
                f"{rule_id} の capability {rule['capability_id']} は TEACHER からアクセスできない"
            )

    def test_system_admin_only_rules_still_require_at_least_system_admin(self):
        """意図的な除外リストが、うっかり STUDENT まで開いてしまう抜け穴になっていないこと。"""
        for rule_id in self._SYSTEM_ADMIN_ONLY_RULES:
            rule = next_steps_mod.RULE_CATALOG[rule_id]
            assert caps.can_access(rule["capability_id"], "SYSTEM_ADMIN") is True
            assert caps.can_access(rule["capability_id"], ROLE_TEACHER) is False
            assert caps.can_access(rule["capability_id"], "STUDENT") is False

    def test_all_severities_are_known(self):
        for rule_id, rule in next_steps_mod.RULE_CATALOG.items():
            assert rule["severity"] in next_steps_mod.SEVERITIES, f"{rule_id} の severity が不正"

    def test_new_capabilities_registered(self):
        """設計 §3.2 の新規 capability 2 件が登録されていること。"""
        assert caps.get_capability("course.atlas_binding") is not None
        assert caps.get_capability("lecture_studio.generate_audio") is not None

    def test_new_capabilities_have_locate_steps(self):
        for cap_id in ("course.atlas_binding", "lecture_studio.generate_audio"):
            cap = caps.get_capability(cap_id)
            assert cap.locate_steps, f"{cap_id} に locate_steps が無い"

    def test_all_rules_have_registered_evaluators(self):
        """RULE_CATALOG と _RULE_EVALUATORS の対応漏れを構造的に防ぐ（追随の仕組み化）。"""
        assert set(next_steps_mod.RULE_CATALOG.keys()) == set(
            next_steps_mod._RULE_EVALUATORS.keys()
        )


# ===========================================================================
# Group A-1b: figure.unreviewed_modes（N13, #496 追随）
# ===========================================================================


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, stmt, params=None):
        self.queries.append((str(stmt), params))
        return _FakeResult(self.rows)


class TestFigureUnreviewedModesRule:
    def test_rule_registered_as_recommended_guidance(self):
        rule = next_steps_mod.RULE_CATALOG[next_steps_mod.RULE_FIGURE_UNREVIEWED_MODES]
        assert rule["severity"] == next_steps_mod.SEVERITY_RECOMMENDED
        assert rule["capability_id"] == "materials.review_figures"
        cap = caps.get_capability("materials.review_figures")
        assert cap is not None
        assert cap.kind == "guidance_only"  # 道案内のみ（図モーダルへ, G8）

    def test_inventory_unvisited_rule_intentionally_absent(self):
        """survey §5-5 の見送り推奨: 「検出要素を見たか」は押し付けになるため
        ルール化しない（G4）。"""
        assert "material.inventory_unvisited" not in next_steps_mod.RULE_CATALOG

    def test_evaluator_sql_targets_pending_ai_suggestions_of_own_documents(self):
        """未レビュー = mode_review_status='pending'、AI 分類済み = suggested_mode<>'unknown'、
        対象は本人所有（uploaded_by）のみ。"""
        src = extract_function_source(_NEXT_STEPS_SRC, "_eval_figure_unreviewed_modes")
        assert "mode_review_status = 'pending'" in src
        assert "suggested_mode <> 'unknown'" in src
        assert "uploaded_by = CAST(:uid AS uuid)" in src
        assert "document_figures" in src
        assert "f.document_id = d.id::text" in src

    def test_evaluator_builds_factual_step(self):
        session = _FakeSession([
            {
                "id": "d-uuid-1",
                "source_path": "mat_1",
                "title": "レーザー分光の基礎",
                "created_at": "2026-07-01T00:00:00+00:00",
                "pending_count": 3,
            },
        ])
        out = next_steps_mod._eval_figure_unreviewed_modes(session, "u1")
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.rule_id == next_steps_mod.RULE_FIGURE_UNREVIEWED_MODES
        assert step.severity == next_steps_mod.SEVERITY_RECOMMENDED
        assert step.step_key == "figure.unreviewed_modes:d-uuid-1"
        assert step.target == {"material_id": "d-uuid-1"}
        assert "3 件" in step.reason
        assert "レーザー分光の基礎" in step.title
        # locate の行アンカーは data-material-id（= source_path）で解決される
        anchors = [s["anchor_id"] for s in step.locate_plan["steps"]]
        assert "material_row:mat_1" in anchors
        assert "material_figures_button" in anchors

    def test_evaluator_returns_empty_when_no_pending_rows(self):
        out = next_steps_mod._eval_figure_unreviewed_modes(_FakeSession([]), "u1")
        assert out == []


# ===========================================================================
# Group A-2: fail-closed（STUDENT には何も出さない）
# ===========================================================================


class TestFailClosed:
    def test_student_gets_nothing_without_touching_db(self):
        """STUDENT はどのルールの capability にも到達できないため、DB に触れずに空を返す。

        session に None を渡し、万一 DB アクセスを試みれば AttributeError で即座に
        検出できるようにする（G3: 評価すらしないことの確認）。
        """
        result = next_steps_mod.compute_next_steps(None, {"id": "u-student", "role": "STUDENT"})
        assert result == {"steps": [], "hidden": [], "truncated": False}

    def test_missing_user_id_returns_empty(self):
        result = next_steps_mod.compute_next_steps(None, {"role": "TEACHER"})
        assert result == {"steps": [], "hidden": [], "truncated": False}

    def test_empty_user_returns_empty(self):
        result = next_steps_mod.compute_next_steps(None, {})
        assert result == {"steps": [], "hidden": [], "truncated": False}

    def test_unknown_role_gets_nothing(self):
        result = next_steps_mod.compute_next_steps(None, {"id": "u1", "role": "BOGUS"})
        assert result == {"steps": [], "hidden": [], "truncated": False}


# ===========================================================================
# Group A-3: 却下は保持する（G5/P4）— ソース構造の検証
# ===========================================================================


class TestDismissalPersistence:
    def test_no_delete_statement_in_next_steps_module(self):
        assert "DELETE FROM assistant_step_dismissals" not in _NEXT_STEPS_SRC
        assert "DELETE  FROM assistant_step_dismissals" not in _NEXT_STEPS_SRC

    def test_restore_uses_revoked_update_not_delete(self):
        assert "def restore_step" in _NEXT_STEPS_SRC
        body = extract_function_source(_NEXT_STEPS_SRC, "restore_step")
        assert "DELETE" not in body.upper().replace("REVOKED", "")
        assert "revoked = TRUE" in body or "revoked=TRUE" in body

    def test_dismiss_upserts_without_delete(self):
        assert "def dismiss_step" in _NEXT_STEPS_SRC
        body = extract_function_source(_NEXT_STEPS_SRC, "dismiss_step")
        assert "ON CONFLICT" in body
        assert "DELETE" not in body.upper()

    def test_routes_dismiss_restore_do_not_delete_rows(self):
        for fn_name in ("dismiss_next_step", "restore_next_step"):
            assert f"def {fn_name}" in _ROUTE_SRC
            body = extract_function_source(_ROUTE_SRC, fn_name)
            assert "DELETE" not in body.upper()

    def test_cue_reuses_dismissal_table_key(self):
        """§8: 初回ログイン cue は専用テーブルを増やさず assistant_step_dismissals を流用する。"""
        assert next_steps_mod.CUE_FIRST_LOGIN_KEY == "cue:first_login"
        assert "def is_cue_pending" in _NEXT_STEPS_SRC


# ===========================================================================
# Group A-4: ガードレール（構造テスト, G2/G7）
# ===========================================================================


class TestGuardrails:
    def test_next_steps_does_not_import_fastapi(self):
        assert_source_does_not_import(_NEXT_STEPS_SRC, ["fastapi"], context="next_steps.py")

    def test_next_steps_does_not_import_llm_client(self):
        assert_source_does_not_import(_NEXT_STEPS_SRC, ["openai"], context="next_steps.py")
        assert_source_forbids(_NEXT_STEPS_SRC, ["core.llm", "llm_client"], context="next_steps.py")

    def test_next_steps_uses_sqlalchemy_text_only(self):
        """SQLAlchemy セッション（sqlalchemy.text 等）の利用は可（タスク条件）。"""
        assert "from sqlalchemy import text as sa_text" in _NEXT_STEPS_SRC

    def test_route_registers_next_steps_endpoints(self):
        assert '"/next-steps"' in _ROUTE_SRC
        assert '"/next-steps/{step_key}/dismiss"' in _ROUTE_SRC
        assert '"/next-steps/{step_key}/restore"' in _ROUTE_SRC

    def test_audit_uses_next_step_entity_type(self):
        assert "'next_step'" in _ROUTE_SRC

    def test_only_documents_and_learning_courses_read_by_uploaded_by_or_user_id(self):
        """本人所有のみを対象にする（uploaded_by / user_id）。"""
        assert "uploaded_by = CAST(:uid AS uuid)" in _NEXT_STEPS_SRC
        assert "user_id = CAST(:uid AS uuid)" in _NEXT_STEPS_SRC


# ===========================================================================
# Group A-5: reason / title に禁止語彙が無い（G6）
# ===========================================================================


class TestReasonWording:
    def test_no_forbidden_vocabulary_in_source_literals(self):
        assert_source_forbids(_NEXT_STEPS_SRC, _FORBIDDEN_WORDS, context="next_steps.py")

    def test_reasons_are_factual_statements_not_commands(self):
        """reason の f-string テンプレートに命令調の「〜してください」を含めない。"""
        assert "してください" not in _NEXT_STEPS_SRC


# ===========================================================================
# Group A-6: finalize_next_steps の純ロジック（DB 非依存, fake データ）
# ===========================================================================


def _fake_step(key: str, severity: str) -> next_steps_mod.NextStep:
    return next_steps_mod.NextStep(
        step_key=key,
        rule_id="test.rule",
        severity=severity,
        title=f"title-{key}",
        reason=f"reason-{key}",
        capability_id="materials.upload",
        locate_plan={"capability_id": "materials.upload", "steps": []},
        target={},
    )


class TestFinalizeNextSteps:
    def test_orders_by_severity_then_oldest_first(self):
        entries = [
            (_fake_step("a", next_steps_mod.SEVERITY_OPTIONAL), "2020-01-03"),
            (_fake_step("b", next_steps_mod.SEVERITY_REQUIRED), "2020-01-02"),
            (_fake_step("c", next_steps_mod.SEVERITY_REQUIRED), "2020-01-01"),
            (_fake_step("d", next_steps_mod.SEVERITY_RECOMMENDED), "2020-01-01"),
        ]
        result = next_steps_mod.finalize_next_steps(entries, set())
        keys = [s["step_key"] for s in result["steps"]]
        assert keys == ["c", "b", "d", "a"]
        assert result["truncated"] is False

    def test_truncates_at_max_steps(self):
        entries = [
            (_fake_step(f"k{i}", next_steps_mod.SEVERITY_OPTIONAL), f"2020-01-{i:02d}")
            for i in range(1, next_steps_mod.MAX_STEPS + 3)
        ]
        result = next_steps_mod.finalize_next_steps(entries, set())
        assert len(result["steps"]) == next_steps_mod.MAX_STEPS
        assert result["truncated"] is True

    def test_no_truncation_when_within_limit(self):
        entries = [
            (_fake_step(f"k{i}", next_steps_mod.SEVERITY_REQUIRED), f"2020-01-{i:02d}")
            for i in range(1, next_steps_mod.MAX_STEPS + 1)
        ]
        result = next_steps_mod.finalize_next_steps(entries, set())
        assert len(result["steps"]) == next_steps_mod.MAX_STEPS
        assert result["truncated"] is False

    def test_dismissed_moved_to_hidden_not_dropped(self):
        """G5/P4: 却下は捨てず hidden に分離する。"""
        entries = [
            (_fake_step("a", next_steps_mod.SEVERITY_REQUIRED), "t"),
            (_fake_step("b", next_steps_mod.SEVERITY_REQUIRED), "t"),
        ]
        result = next_steps_mod.finalize_next_steps(entries, {"a"})
        assert [s["step_key"] for s in result["steps"]] == ["b"]
        assert [s["step_key"] for s in result["hidden"]] == ["a"]

    def test_step_to_dict_has_expected_keys(self):
        step = _fake_step("x", next_steps_mod.SEVERITY_RECOMMENDED)
        d = step.to_dict()
        for key in (
            "step_key", "rule_id", "severity", "title", "reason",
            "capability_id", "locate_plan", "target", "dismissible",
        ):
            assert key in d


# ===========================================================================
# Group A-7: コース⇄地図 binding 判定（course.no_atlas_binding のロジック）
# ===========================================================================


class TestCourseAtlasBindingLogic:
    def test_needs_binding_when_nothing_set(self):
        assert next_steps_mod._course_needs_atlas_binding({}) is True

    def test_no_binding_needed_when_cartridge_present(self):
        """設計 §3.1 の条件は AND: cartridge_id さえあれば（途中でも）対象外。"""
        data = {"cartridge_id": "particle_physics", "topics": []}
        assert next_steps_mod._course_needs_atlas_binding(data) is False

    def test_no_binding_needed_when_topic_node_bound_without_cartridge(self):
        data = {"topics": [{"id": "t1", "atlas_node_id": "n1"}]}
        # cartridge_id が無くても、topics 側に atlas_node_id があれば対象外
        assert next_steps_mod._course_needs_atlas_binding(data) is False

    def test_finds_atlas_node_nested_in_chapters(self):
        data = {"chapters": [{"topics": [{"atlas_node_id": "n1"}]}]}
        assert next_steps_mod._course_has_atlas_node(data) is True


# ===========================================================================
# Group A-7b: atlas_binding_lifecycle_design.md §5 の新ルール2件 + pending 抑制
# ===========================================================================


class _StubSkeleton:
    """concept_ids()/region_ids() だけを持つ最小スタブ（stale ルール専用）。"""

    def __init__(self, concept_ids=(), region_ids=()):
        self._concept_ids = concept_ids
        self._region_ids = region_ids

    def concept_ids(self):
        return self._concept_ids

    def region_ids(self):
        return self._region_ids


class TestNewAtlasBindingRulesRegistered:
    def test_ready_rule_registered_as_recommended_reusing_capability(self):
        rule = next_steps_mod.RULE_CATALOG[next_steps_mod.RULE_COURSE_ATLAS_BINDING_READY]
        assert rule["severity"] == next_steps_mod.SEVERITY_RECOMMENDED
        assert rule["capability_id"] == "course.atlas_binding"

    def test_stale_rule_registered_as_recommended_reusing_capability(self):
        rule = next_steps_mod.RULE_CATALOG[next_steps_mod.RULE_COURSE_ATLAS_BINDING_STALE]
        assert rule["severity"] == next_steps_mod.SEVERITY_RECOMMENDED
        assert rule["capability_id"] == "course.atlas_binding"


class TestCourseAtlasBindingReadyRule:
    def test_produces_step_when_pending_frozen_and_active(self, monkeypatch):
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "load_learner_skeleton", lambda dk, session: object()
        )
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "domain_lifecycle", lambda session, dk: "active"
        )
        session = _FakeSession([
            {
                "id": "course1",
                "title": "コースA",
                "data": {"atlas_binding_pending": "modified_gravity", "topics": []},
                "created_at": "2026-07-01T00:00:00+00:00",
            },
        ])
        out = next_steps_mod._eval_course_atlas_binding_ready(session, "u1")
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.rule_id == next_steps_mod.RULE_COURSE_ATLAS_BINDING_READY
        assert step.severity == next_steps_mod.SEVERITY_RECOMMENDED
        assert step.step_key == "course.atlas_binding_ready:course1"
        assert step.target == {"course_id": "course1"}
        assert "modified_gravity" in step.reason
        assert "コースA" in step.reason

    def test_no_step_when_skeleton_not_yet_frozen(self, monkeypatch):
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "load_learner_skeleton", lambda dk, session: None
        )
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "domain_lifecycle", lambda session, dk: "active"
        )
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {"atlas_binding_pending": "modified_gravity", "topics": []},
                "created_at": "t",
            },
        ])
        out = next_steps_mod._eval_course_atlas_binding_ready(session, "u1")
        assert out == []

    def test_no_step_when_pending_domain_retired(self, monkeypatch):
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "load_learner_skeleton", lambda dk, session: object()
        )
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "domain_lifecycle", lambda session, dk: "retired"
        )
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {"atlas_binding_pending": "modified_gravity", "topics": []},
                "created_at": "t",
            },
        ])
        out = next_steps_mod._eval_course_atlas_binding_ready(session, "u1")
        assert out == []

    def test_no_step_when_not_pending(self, monkeypatch):
        def _boom(dk, session):
            raise AssertionError("skeleton lookup should be skipped when not pending")

        monkeypatch.setattr(next_steps_mod.atlas_store, "load_learner_skeleton", _boom)
        session = _FakeSession([
            {"id": "course1", "title": "コースA", "data": {"topics": []}, "created_at": "t"},
        ])
        out = next_steps_mod._eval_course_atlas_binding_ready(session, "u1")
        assert out == []

    def test_no_step_when_already_bound(self, monkeypatch):
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "load_learner_skeleton", lambda dk, session: object()
        )
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "domain_lifecycle", lambda session, dk: "active"
        )
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {
                    "atlas_binding_pending": "modified_gravity",
                    "cartridge_id": "modified_gravity",
                    "topics": [],
                },
                "created_at": "t",
            },
        ])
        out = next_steps_mod._eval_course_atlas_binding_ready(session, "u1")
        assert out == []


class TestCourseAtlasBindingStaleRule:
    def test_detects_node_ids_missing_from_current_frozen_skeleton(self, monkeypatch):
        skeleton = _StubSkeleton(concept_ids=("c1", "c2"), region_ids=("r1",))
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "load_learner_skeleton", lambda dk, session: skeleton
        )
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {
                    "cartridge_id": "dom",
                    "topics": [
                        {"id": "t1", "atlas_node_id": "c1"},
                        {"id": "t2", "atlas_node_id": "c9"},  # 現行凍結版に存在しない
                    ],
                },
                "created_at": "t",
            },
        ])
        out = next_steps_mod._eval_course_atlas_binding_stale(session, "u1")
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.rule_id == next_steps_mod.RULE_COURSE_ATLAS_BINDING_STALE
        assert step.step_key == "course.atlas_binding_stale:course1"
        assert "1 件" in step.reason
        assert "コースA" in step.reason
        # 版数・数値スコア（confidence 等）は出さない（事実としての件数のみ, G6）
        assert "revision" not in step.reason
        assert "version" not in step.reason

    def test_no_step_when_all_node_ids_known(self, monkeypatch):
        skeleton = _StubSkeleton(concept_ids=("c1", "c2"), region_ids=("r1",))
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "load_learner_skeleton", lambda dk, session: skeleton
        )
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {"cartridge_id": "dom", "topics": [{"id": "t1", "atlas_node_id": "c1"}]},
                "created_at": "t",
            },
        ])
        out = next_steps_mod._eval_course_atlas_binding_stale(session, "u1")
        assert out == []

    def test_no_step_when_no_cartridge_id(self, monkeypatch):
        def _boom(dk, session):
            raise AssertionError("skeleton lookup should be skipped without cartridge_id")

        monkeypatch.setattr(next_steps_mod.atlas_store, "load_learner_skeleton", _boom)
        session = _FakeSession([
            {"id": "course1", "title": "コースA", "data": {"topics": []}, "created_at": "t"},
        ])
        out = next_steps_mod._eval_course_atlas_binding_stale(session, "u1")
        assert out == []

    def test_no_step_when_skeleton_unavailable_fail_closed(self, monkeypatch):
        """骨格が読めない（未凍結・DB 不通等）場合は stale と断定しない（fail-closed）。"""
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "load_learner_skeleton", lambda dk, session: None
        )
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {"cartridge_id": "dom", "topics": [{"id": "t1", "atlas_node_id": "c9"}]},
                "created_at": "t",
            },
        ])
        out = next_steps_mod._eval_course_atlas_binding_stale(session, "u1")
        assert out == []

    def test_checks_nested_chapter_topics_too(self, monkeypatch):
        skeleton = _StubSkeleton(concept_ids=("c1",), region_ids=())
        monkeypatch.setattr(
            next_steps_mod.atlas_store, "load_learner_skeleton", lambda dk, session: skeleton
        )
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {
                    "cartridge_id": "dom",
                    "chapters": [{"topics": [{"id": "t1", "atlas_node_id": "c9"}]}],
                    "topics": [],
                },
                "created_at": "t",
            },
        ])
        out = next_steps_mod._eval_course_atlas_binding_stale(session, "u1")
        assert len(out) == 1

    def test_skeleton_lookup_cached_per_domain_not_per_course(self, monkeypatch):
        """同一ドメインを参照する複数コースがあっても load_learner_skeleton は
        ドメイン単位で1回だけ呼ばれる（N+1 回避）。"""
        calls: list[str] = []

        def _tracked(dk, session):
            calls.append(dk)
            return _StubSkeleton(concept_ids=("c1",), region_ids=())

        monkeypatch.setattr(next_steps_mod.atlas_store, "load_learner_skeleton", _tracked)
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {"cartridge_id": "dom", "topics": [{"id": "t1", "atlas_node_id": "c9"}]},
                "created_at": "t",
            },
            {
                "id": "course2", "title": "コースB",
                "data": {"cartridge_id": "dom", "topics": [{"id": "t2", "atlas_node_id": "c9"}]},
                "created_at": "t2",
            },
        ])
        next_steps_mod._eval_course_atlas_binding_stale(session, "u1")
        assert calls == ["dom"]


# ===========================================================================
# Group A-8: help_kb G層ルール3本（manual_help_kb_design.md §4-1）
# ===========================================================================


class TestHelpKbRulesRegistered:
    """需要側/供給側の両面計器3ルールが RULE_CATALOG + capability registry に
    正しく配線され、G3 fail-closed（ロール階層）を満たすこと。"""

    def test_three_rules_present_with_expected_severity_and_capability(self):
        expected = {
            next_steps_mod.RULE_MANUAL_HELP_GAPS_PENDING: (
                next_steps_mod.SEVERITY_RECOMMENDED, "manual_help.view_gaps",
            ),
            next_steps_mod.RULE_ASSISTANT_KB_UNDOCUMENTED: (
                next_steps_mod.SEVERITY_OPTIONAL, "assistant_kb.view_undocumented",
            ),
            next_steps_mod.RULE_MANUAL_TODO_UNRESOLVED: (
                next_steps_mod.SEVERITY_OPTIONAL, "manual_kb.view_todos",
            ),
        }
        for rule_id, (severity, capability_id) in expected.items():
            rule = next_steps_mod.RULE_CATALOG[rule_id]
            assert rule["severity"] == severity
            assert rule["capability_id"] == capability_id
            cap = caps.get_capability(capability_id)
            assert cap is not None, f"{capability_id} が未登録"
            assert cap.kind == "guidance_only"

    def test_help_gaps_pending_capability_is_teacher_reachable(self):
        cap = caps.get_capability("manual_help.view_gaps")
        assert cap.required_role == "TEACHER"
        assert caps.can_access("manual_help.view_gaps", "TEACHER") is True
        assert caps.can_access("manual_help.view_gaps", "STUDENT") is False

    def test_supply_side_capabilities_are_system_admin_only(self):
        """G3 fail-closed: 供給側2ルールは SYSTEM_ADMIN のみ到達可能（TEACHER 不可）。"""
        for capability_id in ("assistant_kb.view_undocumented", "manual_kb.view_todos"):
            cap = caps.get_capability(capability_id)
            assert cap.required_role == "SYSTEM_ADMIN"
            assert caps.can_access(capability_id, "SYSTEM_ADMIN") is True
            assert caps.can_access(capability_id, "TEACHER") is False
            assert caps.can_access(capability_id, "STUDENT") is False

    def test_system_admin_rules_not_evaluated_for_teacher_role(self):
        """compute_next_steps の active_rules フィルタで、TEACHER には供給側2ルールの
        評価関数自体が呼ばれない（G3: 評価すらしない）ことを、DB 例外の有無で確認する。

        session=None を渡し、もし誤って評価されればどのルールも session.execute で
        即座に AttributeError になる。TEACHER には required の materials.none 等は
        評価されるため、DB 到達不能な None セッションでは例外送出そのものが
        「評価された」ことの証拠になる — ここでは代わりに active_rules を直接検証する。
        """
        role = "TEACHER"
        active_rule_ids = {
            rule_id for rule_id, rule in next_steps_mod.RULE_CATALOG.items()
            if caps.can_access(rule["capability_id"], role)
        }
        assert next_steps_mod.RULE_ASSISTANT_KB_UNDOCUMENTED not in active_rule_ids
        assert next_steps_mod.RULE_MANUAL_TODO_UNRESOLVED not in active_rule_ids
        assert next_steps_mod.RULE_MANUAL_HELP_GAPS_PENDING in active_rule_ids

        admin_active_rule_ids = {
            rule_id for rule_id, rule in next_steps_mod.RULE_CATALOG.items()
            if caps.can_access(rule["capability_id"], "SYSTEM_ADMIN")
        }
        assert next_steps_mod.RULE_ASSISTANT_KB_UNDOCUMENTED in admin_active_rule_ids
        assert next_steps_mod.RULE_MANUAL_TODO_UNRESOLVED in admin_active_rule_ids

    def test_new_rules_have_registered_evaluators(self):
        for rule_id in (
            next_steps_mod.RULE_MANUAL_HELP_GAPS_PENDING,
            next_steps_mod.RULE_ASSISTANT_KB_UNDOCUMENTED,
            next_steps_mod.RULE_MANUAL_TODO_UNRESOLVED,
        ):
            assert rule_id in next_steps_mod._RULE_EVALUATORS


class TestHelpGapsPendingUsesPrivacyModule:
    """k=3 をリテラル再定義しない（core/privacy.py 正本を使う）。"""

    def test_next_steps_imports_privacy_module(self):
        assert "from core import privacy" in _NEXT_STEPS_SRC

    def test_evaluator_source_calls_privacy_helpers_not_literal_k(self):
        src = extract_function_source(_NEXT_STEPS_SRC, "_eval_manual_help_gaps_pending")
        assert "privacy.meets_k_anonymity" in src
        assert "privacy.bucket_count_range" in src
        # K_ANONYMITY をリテラルの 3 として再定義していないこと（正本は core/privacy.py のみ）
        assert "K_ANONYMITY = 3" not in _NEXT_STEPS_SRC
        assert "K_ANONYMITY=3" not in _NEXT_STEPS_SRC

    def test_query_excludes_superseded_traces(self):
        """機能3（メッセージ書き直し/削除）で superseded になった痕跡を集計に含めない。"""
        src = extract_function_source(_NEXT_STEPS_SRC, "_eval_manual_help_gaps_pending")
        assert "status <> 'superseded'" in src

    def test_query_does_not_select_verbatim_question_text(self):
        """P3: 質問逐語・ユーザー特定情報を集計クエリで読まない（payload->>'text' 不使用）。"""
        src = extract_function_source(_NEXT_STEPS_SRC, "_eval_manual_help_gaps_pending")
        assert "payload->>'text'" not in src
        assert "user_id" not in src


class TestManualHelpGapsPendingEvaluator:
    def test_bucket_below_k_anonymity_is_not_shown(self):
        session = _FakeSession([
            {"bucket_key": "manual/student/02-student.md#voice-mode", "cnt": 2,
             "last_seen": "2026-07-20T00:00:00+00:00"},
        ])
        out = next_steps_mod._eval_manual_help_gaps_pending(session, "u1")
        assert out == []

    def test_bucket_at_k_anonymity_is_shown_with_range_and_no_verbatim(self):
        session = _FakeSession([
            {"bucket_key": "manual/student/02-student.md#voice-mode", "cnt": 3,
             "last_seen": "2026-07-20T00:00:00+00:00"},
        ])
        out = next_steps_mod._eval_manual_help_gaps_pending(session, "u1")
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.rule_id == next_steps_mod.RULE_MANUAL_HELP_GAPS_PENDING
        assert step.severity == next_steps_mod.SEVERITY_RECOMMENDED
        assert step.step_key == "manual.help_gaps_pending:manual/student/02-student.md#voice-mode"
        # 表示ラベルは節タイトル優先（索引から解決できないときのみ anchor パスへ縮退）。
        # 引用の正本は target.anchor に保持される。
        label = help_kb.section_title_for_citation(
            "manual/student/02-student.md#voice-mode"
        ) or "student/02-student.md#voice-mode"
        assert f"『{label}』" in step.reason
        assert step.target == {"anchor": "manual/student/02-student.md#voice-mode"}
        assert "3-5" in step.reason
        assert "未整備" in step.reason

    def test_higher_count_buckets_into_wider_range(self):
        session = _FakeSession([
            {"bucket_key": "manual/student/02-student.md#voice-mode", "cnt": 12,
             "last_seen": "t"},
        ])
        out = next_steps_mod._eval_manual_help_gaps_pending(session, "u1")
        assert len(out) == 1
        assert "11+" in out[0][0].reason

    def test_no_hit_bucket_uses_dedicated_key_and_wording(self):
        session = _FakeSession([
            {"bucket_key": "no_hit", "cnt": 5, "last_seen": "t"},
        ])
        out = next_steps_mod._eval_manual_help_gaps_pending(session, "u1")
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.step_key == "manual.help_gaps_pending:no_hit"
        assert "一致する説明が見つからない" in step.reason
        assert step.target == {"anchor": None}

    def test_no_rows_returns_empty(self):
        out = next_steps_mod._eval_manual_help_gaps_pending(_FakeSession([]), "u1")
        assert out == []


class TestAssistantKbUndocumentedEvaluator:
    def test_no_undocumented_capabilities_returns_empty(self, monkeypatch):
        class _Cap:
            def __init__(self, title, howto_doc):
                self.title = title
                self.howto_doc = howto_doc

        monkeypatch.setattr(
            next_steps_mod.caps, "all_capabilities",
            lambda: [_Cap("A", "materials.md#a"), _Cap("B", "materials.md#b")],
        )
        monkeypatch.setattr(
            next_steps_mod.admin_kb, "section_for_howto",
            lambda howto: {"body": "説明あり"},
        )
        out = next_steps_mod._eval_assistant_kb_undocumented(None, "u1")
        assert out == []

    def test_missing_or_empty_kb_section_is_flagged(self, monkeypatch):
        class _Cap:
            def __init__(self, cap_id, title, howto_doc):
                self.id = cap_id
                self.title = title
                self.howto_doc = howto_doc

        caps_list = [
            _Cap("a.one", "機能A", "materials.md#a"),   # documented
            _Cap("b.two", "機能B", "materials.md#b"),   # section missing
            _Cap("c.three", "機能C", ""),                 # no howto_doc at all
        ]
        monkeypatch.setattr(next_steps_mod.caps, "all_capabilities", lambda: caps_list)

        def _section_for_howto(howto):
            if howto == "materials.md#a":
                return {"body": "説明あり"}
            if howto == "materials.md#b":
                return {"body": ""}  # 空節 = 未整備
            return None

        monkeypatch.setattr(next_steps_mod.admin_kb, "section_for_howto", _section_for_howto)
        out = next_steps_mod._eval_assistant_kb_undocumented(None, "u1")
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.rule_id == next_steps_mod.RULE_ASSISTANT_KB_UNDOCUMENTED
        assert step.severity == next_steps_mod.SEVERITY_OPTIONAL
        assert step.step_key == "assistant_kb.undocumented:global"
        assert "2 件" in step.reason
        assert "機能B" in step.reason
        assert "機能C" in step.reason
        # 数値スコア(confidence 等)や督促語彙を出さない事実文であること
        assert "confidence" not in step.reason


class TestManualTodoUnresolvedEvaluator:
    def test_no_excluded_sections_returns_empty(self, monkeypatch):
        monkeypatch.setattr(next_steps_mod.help_manual, "excluded_sections", lambda: [])
        out = next_steps_mod._eval_manual_todo_unresolved(None, "u1")
        assert out == []

    def test_excluded_sections_present_produces_step(self, monkeypatch):
        monkeypatch.setattr(
            next_steps_mod.help_manual, "excluded_sections",
            lambda: [
                {"file": "02-student.md", "anchor": "todo-one", "title": "T1"},
                {"file": "03-teacher.md", "anchor": "todo-two", "title": "T2"},
            ],
        )
        out = next_steps_mod._eval_manual_todo_unresolved(None, "u1")
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.rule_id == next_steps_mod.RULE_MANUAL_TODO_UNRESOLVED
        assert step.severity == next_steps_mod.SEVERITY_OPTIONAL
        assert step.step_key == "manual.todo_unresolved:global"
        assert "2 件" in step.reason
        assert "TODO" in step.reason


class TestNoAtlasBindingPendingSuppression:
    """atlas_binding_lifecycle_design.md §5 最終項: atlas_binding_pending が非空の
    コースには course.no_atlas_binding を出さない（二重督促の回避）。"""

    def test_pending_course_is_suppressed(self):
        session = _FakeSession([
            {
                "id": "course1", "title": "コースA",
                "data": {"atlas_binding_pending": "modified_gravity", "topics": []},
                "created_at": "t",
            },
        ])
        out = next_steps_mod._eval_course_no_atlas_binding(session, "u1")
        assert out == []

    def test_non_pending_course_still_flagged(self):
        session = _FakeSession([
            {"id": "course2", "title": "コースB", "data": {"topics": []}, "created_at": "t"},
        ])
        out = next_steps_mod._eval_course_no_atlas_binding(session, "u1")
        assert len(out) == 1
        assert out[0][0].step_key == "course.no_atlas_binding:course2"


# ===========================================================================
# Group B: API 結合テスト（TestClient。実 DB なしでの fail-closed 縮退を検証）
# ===========================================================================

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_TEACHER = "11111111-1111-1111-1111-111111111111"


def _headers(role: str, sub: str = _UID_TEACHER):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


@pytest.fixture
def api():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI 未導入")
    for p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from fastapi.testclient import TestClient
    from api.main import app
    import routes.admin_assistant as route_mod

    client = TestClient(app)
    return client, route_mod


@pytestmark_api
class TestNextStepsAPI:
    def test_student_forbidden(self, api):
        client, _route_mod = api
        r = client.get("/api/admin/assistant/next-steps", headers=_headers("STUDENT"))
        assert r.status_code == 403

    def test_get_returns_expected_keys_even_without_live_db(self, api):
        """DB 未接続環境でも捏造せず fail-closed に縮退する（S5 と同型）。"""
        client, _route_mod = api
        r = client.get("/api/admin/assistant/next-steps", headers=_headers("TEACHER"))
        assert r.status_code == 200
        data = r.json()
        for key in ("steps", "hidden", "truncated", "assistant_cue_pending"):
            assert key in data
        assert isinstance(data["steps"], list)
        assert isinstance(data["hidden"], list)

    def test_dismiss_calls_store_and_returns_status(self, api, monkeypatch):
        client, route_mod = api
        calls = []
        monkeypatch.setattr(
            route_mod.next_steps_mod, "dismiss_step",
            lambda session, uid, key: calls.append(("dismiss", uid, key)),
        )
        monkeypatch.setattr(route_mod, "_record_next_step_event", lambda *a, **k: None)
        r = client.post(
            "/api/admin/assistant/next-steps/materials.none:global/dismiss",
            headers=_headers("TEACHER"),
        )
        assert r.status_code == 200
        assert r.json() == {"status": "dismissed", "step_key": "materials.none:global"}
        assert calls == [("dismiss", _UID_TEACHER, "materials.none:global")]

    def test_restore_calls_store_and_returns_status(self, api, monkeypatch):
        client, route_mod = api
        calls = []
        monkeypatch.setattr(
            route_mod.next_steps_mod, "restore_step",
            lambda session, uid, key: calls.append(("restore", uid, key)) or True,
        )
        monkeypatch.setattr(route_mod, "_record_next_step_event", lambda *a, **k: None)
        r = client.post(
            "/api/admin/assistant/next-steps/materials.none:global/restore",
            headers=_headers("TEACHER"),
        )
        assert r.status_code == 200
        assert r.json() == {"status": "restored", "step_key": "materials.none:global"}
        assert calls == [("restore", _UID_TEACHER, "materials.none:global")]

    def test_dismiss_forbidden_for_student(self, api):
        client, _route_mod = api
        r = client.post(
            "/api/admin/assistant/next-steps/materials.none:global/dismiss",
            headers=_headers("STUDENT"),
        )
        assert r.status_code == 403


# ===========================================================================
# Group A-6: migration 039 の配線 + フロント統合（構造テスト。DB / FastAPI 不要）
# ===========================================================================

import re  # noqa: E402

_MIGRATION_SQL = (BACKEND / "db" / "039_assistant_step_dismissals.sql").read_text(encoding="utf-8")
_NEXT_STEPS_JS = (ROOT / "frontend" / "public" / "js" / "admin-next-steps.js").read_text(encoding="utf-8")
_ADMIN_HTML = (ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
_ADMIN_JS_MAIN = (ROOT / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")


class TestMigrationWiring:
    """migration 039 が正本 SQL・ORM の二点で一致していること
    （main.py 側との比較は 2026-07 の migration 正本一本化により廃止）。
    """

    def test_sql_reference_defines_table(self):
        assert "CREATE TABLE IF NOT EXISTS assistant_step_dismissals" in _MIGRATION_SQL
        assert "UNIQUE (user_id, step_key)" in _MIGRATION_SQL
        assert "revoked" in _MIGRATION_SQL

    def test_sql_reference_defines_expected_index(self):
        assert "idx_assistant_step_dismissals_user" in _MIGRATION_SQL

    def test_orm_model_exists(self):
        from core import models

        assert hasattr(models, "AssistantStepDismissal")
        assert models.AssistantStepDismissal.__tablename__ == "assistant_step_dismissals"


class TestFrontendIntegration:
    """G層フロントの構造的不変条項（test_admin_assistant.py の frontend 検証と同方式）。"""

    def test_admin_html_includes_badge_and_script(self):
        assert "admin-next-steps-toggle" in _ADMIN_HTML
        assert "admin-next-steps.js" in _ADMIN_HTML

    def test_next_steps_js_is_es5(self):
        # 管理画面 JS の規約（ES5）: アロー関数・const/let・テンプレートリテラルを使わない。
        assert "=>" not in _NEXT_STEPS_JS
        assert re.search(r"\bconst\s", _NEXT_STEPS_JS) is None
        assert re.search(r"\blet\s", _NEXT_STEPS_JS) is None
        assert "`" not in _NEXT_STEPS_JS

    def test_no_polling(self):
        # G4: ポーリング禁止（再取得は画面イベントからの refresh() のみ）。
        assert "setInterval" not in _NEXT_STEPS_JS

    def test_locate_delegates_to_admin_assistant(self):
        # G8: 道案内は AdminAssistant.runLocatePlan に委譲し spotlight を二重実装しない。
        assert "runLocatePlan" in _NEXT_STEPS_JS
        assert "admin-assistant-spotlight" not in _NEXT_STEPS_JS

    def test_admin_js_wires_next_steps(self):
        assert "AdminNextSteps.init" in _ADMIN_JS_MAIN
        assert "AdminNextSteps.refresh" in _ADMIN_JS_MAIN

    def test_atlas_data_has_no_default_cartridge_fallback(self):
        # Phase 0（設計 §9）: 既定カートリッジへのフォールバック経路が復活していないこと。
        atlas_data = (ROOT / "frontend" / "public" / "js" / "atlas-data.js").read_text(encoding="utf-8")
        assert "DEFAULT_CARTRIDGE" not in atlas_data
        assert "particle_physics" not in atlas_data
