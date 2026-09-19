"""G層ルール ``material.explanations_skipped`` / ``material.ingest_incomplete``。

2026-09-19 の実測（12 論文）で見えた 2 つの「見えない欠落」を教員に届ける:

- 8/11 本で ``contextual_explanation`` が日次上限に達し、``stage_outputs`` に
  ``skipped_by_limit: true`` を残したまま run は completed。説明は 1 件も無い。
- 11/12 本で ``document_completeness`` が ``complete=false``。artifact 止まりで
  教材一覧にも To-Do にも出ない。

fake session（実 DB なし）で評価器の分岐だけを固定する:
  1. 上限で止まった教材に出る / 説明が 1 件でもあれば出ない（G1: 実施すれば自動消滅）
  2. 完全性が false の教材に出る / true なら出ない（G1）
  3. 事実文に件数・督促語彙が無い（G6）・道案内は既存 capability の再利用（G3）
  4. 対象は本人所有（uploaded_by）の教材だけ・成果物 run は「採用（adopted）」で解決する
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import coverage_facts  # noqa: E402
from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant import next_steps as ns  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

_SRC = (BACKEND / "core" / "admin_assistant" / "next_steps.py").read_text(encoding="utf-8")

_UID = "u-teacher"
_FORBIDDEN_WORDS = ("！", "今すぐ", "急いで", "必ず", "早く", "至急", "してください")


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeSession:
    """SQL の中身で応答を出し分ける最小のフェイク（実 DB には接続しない）。"""

    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.queries.append((str(stmt), params or {}))
        return _FakeResult(self.rows)


def _material(**over):
    row = {
        "id": "d-uuid-1",
        "source_path": "mat_1",
        "title": "重力波の波形モデル",
        "created_at": "2026-09-01T00:00:00+00:00",
    }
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# 1. カタログ登録（G3）
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_rules_are_registered_as_guidance(self):
        # explanations_skipped は recommended、ingest_incomplete は optional
        # （PDF 経路はほぼ全教材が complete=false になり、recommended だと上限 10 件を
        # 埋め尽くす — 2026-09-19 再現性レビュー §7）。
        expected = {
            ns.RULE_MATERIAL_EXPLANATIONS_SKIPPED: ns.SEVERITY_RECOMMENDED,
            ns.RULE_MATERIAL_INGEST_INCOMPLETE: ns.SEVERITY_OPTIONAL,
        }
        for rule_id, severity in expected.items():
            rule = ns.RULE_CATALOG[rule_id]
            assert rule["severity"] == severity
            cap = caps.get_capability(rule["capability_id"])
            assert cap is not None
            assert cap.kind == "guidance_only"  # 代行しない（G8: 誘導まで）
            assert caps.can_access(rule["capability_id"], "TEACHER")

    def test_rules_have_evaluators(self):
        assert set(ns.RULE_CATALOG) == set(ns._RULE_EVALUATORS)


# ---------------------------------------------------------------------------
# 2. material.explanations_skipped
# ---------------------------------------------------------------------------


class TestExplanationsSkipped:
    def test_points_at_the_adopted_run_and_the_owner(self):
        src = extract_function_source(_SRC, "_eval_material_explanations_skipped")
        assert "skipped_by_limit" in src
        assert "element_explanations" in src
        assert "uploaded_by = CAST(:uid AS uuid)" in ns._ADOPTED_RUN_SQL
        # 採用 run（active → 直近 completed）で見る。走行中の run を混ぜない（P0-8）。
        assert "active_analysis_run_id" in ns._ADOPTED_RUN_SQL
        assert "r2.status = 'completed'" in ns._ADOPTED_RUN_SQL

    def test_disappears_when_an_explanation_exists(self):
        """SQL 側の NOT EXISTS で消える（完了フラグを持たない, G1）。"""
        src = extract_function_source(_SRC, "_eval_material_explanations_skipped")
        assert "NOT EXISTS" in src
        assert "e.role IS NULL" in src  # 議論のきっかけを説明と数えない

    def test_builds_a_factual_step(self):
        session = _FakeSession([_material()])
        out = ns._eval_material_explanations_skipped(session, _UID)
        assert len(out) == 1
        step, sort_ts = out[0]
        assert step.rule_id == ns.RULE_MATERIAL_EXPLANATIONS_SKIPPED
        assert step.step_key == "material.explanations_skipped:d-uuid-1"
        assert step.target == {"material_id": "d-uuid-1"}
        assert sort_ts == "2026-09-01T00:00:00+00:00"
        assert "重力波の波形モデル" in step.reason
        assert "上限" in step.reason
        # 道案内は教材の行まで（data-material-id = source_path で解決）
        anchors = [s["anchor_id"] for s in step.locate_plan["steps"]]
        assert "material_row:mat_1" in anchors

    def test_reason_has_no_numbers_and_no_pressure(self):
        step, _ = ns._eval_material_explanations_skipped(_FakeSession([_material()]), _UID)[0]
        assert not any(ch.isdigit() for ch in step.reason)
        for word in _FORBIDDEN_WORDS:
            assert word not in step.reason

    def test_empty_when_nothing_matches(self):
        assert ns._eval_material_explanations_skipped(_FakeSession([]), _UID) == []


# ---------------------------------------------------------------------------
# 3. material.ingest_incomplete
# ---------------------------------------------------------------------------


def _incomplete(*reasons, **over):
    row = _material(**over)
    row["payload"] = {"complete": False, "review_reasons": list(reasons)}
    return row


class TestIngestIncomplete:
    def test_reads_the_completeness_artifact_of_the_adopted_run(self):
        src = extract_function_source(_SRC, "_eval_material_ingest_incomplete")
        assert "document_analysis_artifacts" in src
        assert "'false'" in src
        assert ns._COMPLETENESS_STAGE == "document_completeness"

    def test_builds_a_factual_step_with_the_reason_vocabulary(self):
        session = _FakeSession([_incomplete("structure_page_coverage_low")])
        out = ns._eval_material_ingest_incomplete(session, _UID)
        assert len(out) == 1
        step, _sort_ts = out[0]
        assert step.rule_id == ns.RULE_MATERIAL_INGEST_INCOMPLETE
        assert step.step_key == "material.ingest_incomplete:d-uuid-1"
        assert step.target == {"material_id": "d-uuid-1"}
        assert (
            coverage_facts.COMPLETENESS_REASON_FACTS["structure_page_coverage_low"]
            in step.reason
        )
        anchors = [s["anchor_id"] for s in step.locate_plan["steps"]]
        assert "material_row:mat_1" in anchors

    def test_many_reasons_are_summarised_without_a_count(self):
        session = _FakeSession([
            _incomplete(
                "structure_page_coverage_low",
                "equation_label_discontinuity",
                "terminal_section_missing",
                "tail_truncation_suspected",
            )
        ])
        step, _ = ns._eval_material_ingest_incomplete(session, _UID)[0]
        assert "ほかにも記録があります。" in step.reason
        assert not any(ch.isdigit() for ch in step.reason)

    def test_unknown_reason_still_speaks(self):
        session = _FakeSession([_incomplete("brand_new_reason")])
        step, _ = ns._eval_material_ingest_incomplete(session, _UID)[0]
        assert coverage_facts.completeness_fact("brand_new_reason") in step.reason

    def test_reason_has_no_pressure_vocabulary(self):
        session = _FakeSession([_incomplete("ingest_incomplete")])
        step, _ = ns._eval_material_ingest_incomplete(session, _UID)[0]
        for word in _FORBIDDEN_WORDS:
            assert word not in step.reason

    def test_empty_when_nothing_matches(self):
        assert ns._eval_material_ingest_incomplete(_FakeSession([]), _UID) == []


# ---------------------------------------------------------------------------
# 4. 上限と truncated（既存の規律を壊さない）
# ---------------------------------------------------------------------------


class TestLimits:
    def test_many_materials_are_truncated_honestly(self):
        rows = [
            _incomplete("ingest_incomplete", id=f"d-{i}", source_path=f"mat_{i}",
                        created_at=f"2026-09-{i:02d}T00:00:00+00:00")
            for i in range(1, ns.MAX_STEPS + 4)
        ]
        entries = ns._eval_material_ingest_incomplete(_FakeSession(rows), _UID)
        result = ns.finalize_next_steps(entries, set())
        assert len(result["steps"]) == ns.MAX_STEPS
        assert result["truncated"] is True

    def test_dismissal_keeps_the_item_instead_of_dropping_it(self):
        entries = ns._eval_material_ingest_incomplete(
            _FakeSession([_incomplete("ingest_incomplete")]), _UID
        )
        key = entries[0][0].step_key
        result = ns.finalize_next_steps(entries, {key})
        assert result["steps"] == []
        assert [s["step_key"] for s in result["hidden"]] == [key]
