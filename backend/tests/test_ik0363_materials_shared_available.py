"""IK-0363: G層 ``materials.none`` は「使える教材」（公開・共有を含む）で判定する。

所有ゼロでも公開・グループ共有された教材からコースを作れる教員に、
「登録されている教材がまだありません」とだけ案内しない。所有ゼロ・利用可能ありは
``materials.shared_available``（recommended・道案内はコース構築）が案内する。

fake session（実 DB なし）で評価器の分岐と SQL の可視性条件だけを固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant import next_steps as ns  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    extract_function_source,
)

_SRC = (BACKEND / "core" / "admin_assistant" / "next_steps.py").read_text(encoding="utf-8")
_UID = "u-teacher"


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def fetchone(self):
        return self._row


class _FakeSession:
    def __init__(self, *, owns_any: bool, usable_any: bool):
        self.row = {"owns_any": owns_any, "usable_any": usable_any}
        self.queries: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.queries.append((str(stmt), params or {}))
        return _FakeResult(self.row)


class TestCatalog:
    def test_shared_available_rule_is_registered(self):
        rule = ns.RULE_CATALOG[ns.RULE_MATERIALS_SHARED_AVAILABLE]
        assert rule["severity"] == ns.SEVERITY_RECOMMENDED
        # material.no_course と同じ道案内（コース構築タブ）を再利用する（G3）
        assert rule["capability_id"] == "course_builder.open"
        assert rule["capability_id"] == ns.RULE_CATALOG[ns.RULE_MATERIAL_NO_COURSE]["capability_id"]
        cap = caps.get_capability("course_builder.open")
        assert cap is not None
        assert cap.kind == "guidance_only"
        assert caps.can_access("course_builder.open", "TEACHER")

    def test_evaluator_registered(self):
        assert ns._RULE_EVALUATORS[ns.RULE_MATERIALS_SHARED_AVAILABLE] is ns._eval_materials_shared_available

    def test_materials_none_keeps_its_catalog_entry(self):
        rule = ns.RULE_CATALOG[ns.RULE_MATERIALS_NONE]
        assert rule["severity"] == ns.SEVERITY_REQUIRED
        assert rule["capability_id"] == "materials.upload"


class TestVisibilitySql:
    """判定集合は教材一覧（list_materials）と同じ可視性。所有だけを数えない。"""

    def test_usable_sql_covers_public_group_and_document_share(self):
        sql = ns._USABLE_MATERIAL_SQL
        assert "d.uploaded_by = CAST(:uid AS uuid)" in sql
        assert "'public'" in sql
        assert "'group'" in sql
        assert "group_members" in sql
        assert "object_group_permissions" in sql
        assert "dgp.object_type = 'document'" in sql
        assert "'viewer', 'editor'" in sql
        assert "d.filename IS NOT NULL" in sql

    def test_materials_none_uses_usable_set(self):
        src = extract_function_source(_SRC, "_eval_materials_none")
        assert "_material_availability" in src
        assert "usable_any" in src
        # 旧実装（所有の count だけ）に戻っていない
        assert "SELECT count(*) FROM documents WHERE uploaded_by" not in src

    def test_core_does_not_import_api_services(self):
        assert_source_does_not_import(_SRC, ["api.services", "services", "fastapi"],
                                      context="next_steps.py")


class TestMaterialsNone:
    def test_fires_when_nothing_is_usable(self):
        out = ns._eval_materials_none(_FakeSession(owns_any=False, usable_any=False), _UID)
        assert len(out) == 1
        step, _ = out[0]
        assert step.rule_id == ns.RULE_MATERIALS_NONE
        assert step.step_key == "materials.none:global"
        assert "まだありません" in step.reason

    def test_silent_when_shared_materials_are_usable(self):
        """IK-0363 の再現条件: 所有ゼロ・公開教材あり → 「教材なし」と言わない。"""
        out = ns._eval_materials_none(_FakeSession(owns_any=False, usable_any=True), _UID)
        assert out == []

    def test_silent_when_owner(self):
        assert ns._eval_materials_none(_FakeSession(owns_any=True, usable_any=True), _UID) == []

    def test_owned_counts_as_usable_even_if_usable_flag_is_false(self):
        # 所有しているなら使える（filename 未設定の登録途中でも「教材なし」とは言わない）
        assert ns._eval_materials_none(_FakeSession(owns_any=True, usable_any=False), _UID) == []

    def test_query_is_parameterized_with_uid(self):
        session = _FakeSession(owns_any=False, usable_any=False)
        ns._eval_materials_none(session, _UID)
        assert session.queries and session.queries[0][1] == {"uid": _UID}


class TestSharedAvailable:
    def test_fires_when_owned_zero_and_usable(self):
        out = ns._eval_materials_shared_available(
            _FakeSession(owns_any=False, usable_any=True), _UID
        )
        assert len(out) == 1
        step, sort_ts = out[0]
        assert step.rule_id == ns.RULE_MATERIALS_SHARED_AVAILABLE
        assert step.severity == ns.SEVERITY_RECOMMENDED
        assert step.capability_id == "course_builder.open"
        assert step.step_key == "materials.shared_available:global"
        assert "共有されている教材からコースを作れます。" in step.reason
        assert step.target == {}
        assert sort_ts == ""
        anchors = [s["anchor_id"] for s in step.locate_plan["steps"]]
        assert "cb_material_select" in anchors

    def test_silent_for_owner(self):
        assert ns._eval_materials_shared_available(
            _FakeSession(owns_any=True, usable_any=True), _UID
        ) == []

    def test_silent_when_nothing_usable(self):
        assert ns._eval_materials_shared_available(
            _FakeSession(owns_any=False, usable_any=False), _UID
        ) == []

    def test_reason_has_no_numbers(self):
        step, _ = ns._eval_materials_shared_available(
            _FakeSession(owns_any=False, usable_any=True), _UID
        )[0]
        assert not any(ch.isdigit() for ch in step.reason + step.title)

    def test_exactly_one_of_the_two_rules_fires(self):
        """所有 × 可視 の 4 状態で、二つのルールが同時に点灯しない。"""
        for owns in (False, True):
            for usable in (False, True):
                session = _FakeSession(owns_any=owns, usable_any=usable)
                none_out = ns._eval_materials_none(session, _UID)
                shared_out = ns._eval_materials_shared_available(session, _UID)
                assert not (none_out and shared_out)
                if owns:
                    assert not none_out and not shared_out


class TestComputeNextSteps:
    def test_teacher_with_only_shared_materials_is_guided_to_course_builder(self, monkeypatch):
        """compute_next_steps 経由: 他ルールは空にして、2 ルールの出方だけを見る。"""
        for rule_id in list(ns._RULE_EVALUATORS):
            if rule_id not in (ns.RULE_MATERIALS_NONE, ns.RULE_MATERIALS_SHARED_AVAILABLE):
                monkeypatch.setitem(ns._RULE_EVALUATORS, rule_id, lambda _s, _u: [])
        monkeypatch.setattr(ns, "fetch_dismissed_keys", lambda _s, _u: set())
        session = _FakeSession(owns_any=False, usable_any=True)
        result = ns.compute_next_steps(session, {"id": _UID, "role": "TEACHER"})
        rule_ids = [s["rule_id"] for s in result["steps"]]
        assert rule_ids == [ns.RULE_MATERIALS_SHARED_AVAILABLE]


class TestSharedAvailableStopsAfterCourse:
    """第 5 周の発見: 共有教材からコースを作って公開した教員の「次にやること」に、
    「共有されている教材からコースを作成する」が残り続けた。所有コースがあれば出さない。"""

    def test_not_emitted_when_teacher_owns_a_course(self):
        import core.admin_assistant.next_steps as ns

        class _Session(_FakeSession):
            def execute(self, stmt, params=None):
                text = str(getattr(stmt, "text", stmt))
                if "learning_courses" in text:
                    return _FakeResult({"owns_course": True})
                return super().execute(stmt, params)

        assert ns._eval_materials_shared_available(_Session(owns_any=False, usable_any=True), _UID) == []

    def test_emitted_when_teacher_owns_no_course(self):
        import core.admin_assistant.next_steps as ns

        out = ns._eval_materials_shared_available(_FakeSession(owns_any=False, usable_any=True), _UID)
        assert [step.rule_id for step, _ in out] == [ns.RULE_MATERIALS_SHARED_AVAILABLE]
