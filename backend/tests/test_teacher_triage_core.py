"""負荷順トリアージ core（``core/teacher_triage.py``, 教員支援 Phase 4 §2）の単体テスト。

正本: ``docs/features/teacher_triage_instruments_design.md`` §2 / §5 精査①②③。

検証観点:
- 台帳バッチ読み: ``epistemic_ledger`` は ANY(:ids) の1クエリ、``load_percentiles`` は
  キューにつき1回（行ごとに呼ばない — routes/doubt.py のアンチパターンを再現しない）。
- agent ID 解決経路: 説明キューの claim / component は agent 側 ID → DB UUID の
  索引で解決してから台帳を引く。equation は素通し、figure / document は導出不能。
- NULL / 未解決 / course 混在の末尾配置と正直な縮退ラベル「影響度を導出できない候補」。
- 段階ラベルが ``core/doubt/schema.py``（LOAD_LEVEL_LABELS = 低/中/高/最高位）由来で
  あること（TT2: 独自辞書を作らない）。
- 生値（load_score）非漏洩: 付与されるのは段階キーとラベルの2キーのみ。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import teacher_triage  # noqa: E402
from core.doubt import schema as doubt_schema  # noqa: E402


# ---------------------------------------------------------------------------
# インメモリフェイクセッション（epistemic_ledger / load_percentiles の SQL 面のみ模倣）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeLedgerSession:
    def __init__(self, ledger_rows, percentiles=(10.0, 50.0, 100.0)):
        self.ledger_rows = ledger_rows
        self.percentiles = percentiles
        self.ledger_queries = 0
        self.percentile_queries = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        if "FROM epistemic_ledger WHERE target_id = ANY(:ids)" in sql:
            self.ledger_queries += 1
            ids = set(params["ids"])
            rows = [
                (r["target_type"], r["target_id"], r["load_score"], r["course_id"])
                for r in self.ledger_rows
                if r["target_id"] in ids
            ]
            return _FakeResult(rows)
        if "percentile_cont" in sql:
            self.percentile_queries += 1
            return _FakeResult([self.percentiles])
        raise AssertionError(f"unhandled SQL in FakeLedgerSession: {sql!r}")

    def close(self):
        pass


def _row(target_id, load_score, course_id="course-1", target_type="claim"):
    return {
        "target_type": target_type,
        "target_id": target_id,
        "load_score": load_score,
        "course_id": course_id,
    }


# ---------------------------------------------------------------------------
# 台帳バッチ読み（§5 精査②）
# ---------------------------------------------------------------------------


class TestLoadLevelsForTargets:
    def test_ledger_is_read_in_one_batch_and_percentiles_once(self):
        """行ごとに percentile を引かない（routes/doubt.py の行ごと呼びは真似ない）。"""
        fake = FakeLedgerSession([
            _row("c1", 120.0), _row("c2", 60.0), _row("c3", 20.0), _row("c4", 5.0),
        ])
        targets = [("claim", "c1"), ("claim", "c2"), ("claim", "c3"), ("claim", "c4")]
        levels = teacher_triage.load_levels_for_targets(fake, targets)
        assert len(levels) == 4
        assert fake.ledger_queries == 1
        assert fake.percentile_queries == 1

    def test_levels_match_doubt_schema_percentile_mapping(self):
        """段階の決め方は D層の load_level_for_score そのもの（第2実装を作らない）。"""
        p50, p90, p99 = 10.0, 50.0, 100.0
        fake = FakeLedgerSession(
            [_row("c1", 120.0), _row("c2", 60.0), _row("c3", 20.0), _row("c4", 5.0)],
            percentiles=(p50, p90, p99),
        )
        levels = teacher_triage.load_levels_for_targets(
            fake, [("claim", c) for c in ("c1", "c2", "c3", "c4")]
        )
        assert levels[("claim", "c1")] == "highest"
        assert levels[("claim", "c2")] == "high"
        assert levels[("claim", "c3")] == "medium"
        assert levels[("claim", "c4")] == "low"
        for cid, score in (("c1", 120.0), ("c2", 60.0), ("c3", 20.0), ("c4", 5.0)):
            assert levels[("claim", cid)] == doubt_schema.load_level_for_score(
                score, p50, p90, p99
            )

    def test_null_load_score_is_underivable(self):
        fake = FakeLedgerSession([_row("c1", None), _row("c2", 60.0)])
        levels = teacher_triage.load_levels_for_targets(
            fake, [("claim", "c1"), ("claim", "c2")]
        )
        assert levels[("claim", "c1")] == ""
        assert levels[("claim", "c2")] == "high"

    def test_missing_ledger_row_is_underivable(self):
        fake = FakeLedgerSession([_row("c1", 60.0)])
        levels = teacher_triage.load_levels_for_targets(
            fake, [("claim", "c1"), ("claim", "no-ledger-row")]
        )
        assert levels[("claim", "no-ledger-row")] == ""

    def test_mixed_course_ids_degrade_all_without_percentile_query(self):
        """course 混在は percentile の基準が定まらない → 全件導出不能（正直な縮退）。"""
        fake = FakeLedgerSession([
            _row("c1", 60.0, course_id="course-1"),
            _row("c2", 60.0, course_id="course-2"),
        ])
        levels = teacher_triage.load_levels_for_targets(
            fake, [("claim", "c1"), ("claim", "c2")]
        )
        assert levels == {("claim", "c1"): "", ("claim", "c2"): ""}
        assert fake.percentile_queries == 0

    def test_absent_course_ids_degrade_all(self):
        fake = FakeLedgerSession([_row("c1", 60.0, course_id="")])
        levels = teacher_triage.load_levels_for_targets(fake, [("claim", "c1")])
        assert levels == {("claim", "c1"): ""}
        assert fake.percentile_queries == 0

    def test_empty_targets_do_not_query(self):
        fake = FakeLedgerSession([])
        assert teacher_triage.load_levels_for_targets(fake, []) == {}
        assert teacher_triage.load_levels_for_targets(fake, [None, None]) == {}
        assert fake.ledger_queries == 0

    def test_target_type_must_match_the_ledger_row(self):
        """同じ target_id でも target_type が違えば別物（UNIQUE(target_id, target_type)）。"""
        fake = FakeLedgerSession([_row("x1", 60.0, target_type="claim")])
        levels = teacher_triage.load_levels_for_targets(
            fake, [("component", "x1"), ("claim", "x1")]
        )
        assert levels[("claim", "x1")] == "high"
        assert levels[("component", "x1")] == ""

    def test_no_raw_scores_in_return_value(self):
        """生値非漏洩（TT2）: 返るのは段階キー文字列のみ。"""
        fake = FakeLedgerSession([_row("c1", 123.456)])
        levels = teacher_triage.load_levels_for_targets(fake, [("claim", "c1")])
        allowed = set(doubt_schema.LOAD_LEVELS) | {""}
        for value in levels.values():
            assert isinstance(value, str)
            assert value in allowed


# ---------------------------------------------------------------------------
# agent ID 解決経路（§5 精査③）
# ---------------------------------------------------------------------------


class TestExplanationTargetForRow:
    CLAIM_LOOKUP = {"claim_span_1": "11111111-1111-1111-1111-111111111111"}
    COMPONENT_LOOKUP = {"comp_agent_1": "22222222-2222-2222-2222-222222222222"}

    def _target(self, element_type, element_id):
        return teacher_triage.explanation_target_for_row(
            {"element_type": element_type, "element_id": element_id},
            self.CLAIM_LOOKUP,
            self.COMPONENT_LOOKUP,
        )

    def test_claim_agent_id_resolves_to_db_uuid(self):
        assert self._target("theory_claim", "claim_span_1") == (
            "claim", "11111111-1111-1111-1111-111111111111",
        )

    def test_unresolved_claim_agent_id_is_underivable(self):
        assert self._target("theory_claim", "claim_unknown") is None

    def test_component_agent_id_resolves_to_db_uuid(self):
        assert self._target("theory_component", "comp_agent_1") == (
            "component", "22222222-2222-2222-2222-222222222222",
        )

    def test_unresolved_component_agent_id_is_underivable(self):
        assert self._target("theory_component", "comp_unknown") is None

    def test_equation_id_passes_through(self):
        assert self._target("equation", "eq_2_7") == ("equation", "eq_2_7")

    def test_figure_and_document_scopes_are_underivable(self):
        assert self._target("figure", "fig-1") is None
        assert self._target("document", "doc-1") is None

    def test_empty_element_id_is_underivable(self):
        assert self._target("equation", "") is None
        assert self._target("theory_claim", None) is None


# ---------------------------------------------------------------------------
# 付与と並べ替え（末尾配置・doubt/schema 由来ラベル・生値非漏洩）
# ---------------------------------------------------------------------------


class TestAnnotateAndSortByLoad:
    def _items(self):
        return [
            {"id": "a"},   # low
            {"id": "b"},   # highest
            {"id": "c"},   # 導出不能
            {"id": "d"},   # high
            {"id": "e"},   # medium
            {"id": "f"},   # 導出不能（2件目 — 安定性確認用）
        ]

    LEVELS = {
        ("claim", "a"): "low",
        ("claim", "b"): "highest",
        ("claim", "d"): "high",
        ("claim", "e"): "medium",
    }

    @staticmethod
    def _target(item):
        if item["id"] in ("c", "f"):
            return None
        return ("claim", item["id"])

    def test_descending_order_with_underivable_at_the_end(self):
        result = teacher_triage.annotate_and_sort_by_load(
            self._items(), self.LEVELS, target_for_item=self._target
        )
        assert [it["id"] for it in result] == ["b", "d", "e", "a", "c", "f"]

    def test_underivable_items_keep_original_relative_order(self):
        """安定ソート: 同段階（ここでは導出不能）内は従来順を保持する。"""
        result = teacher_triage.annotate_and_sort_by_load(
            self._items(), self.LEVELS, target_for_item=self._target
        )
        tail = [it["id"] for it in result if it["load_level"] == ""]
        assert tail == ["c", "f"]

    def test_labels_come_from_doubt_schema(self):
        """段階ラベルの正本は core/doubt/schema.py（低/中/高/最高位。TT2）。"""
        assert teacher_triage.LOAD_LEVEL_LABELS is doubt_schema.LOAD_LEVEL_LABELS
        result = teacher_triage.annotate_and_sort_by_load(
            self._items(), self.LEVELS, target_for_item=self._target
        )
        by_id = {it["id"]: it for it in result}
        assert by_id["b"]["load_level_label"] == doubt_schema.LOAD_LEVEL_LABELS["highest"] == "最高位"
        assert by_id["d"]["load_level_label"] == "高"
        assert by_id["e"]["load_level_label"] == "中"
        assert by_id["a"]["load_level_label"] == "低"

    def test_underivable_items_get_the_honest_degradation_label(self):
        result = teacher_triage.annotate_and_sort_by_load(
            self._items(), self.LEVELS, target_for_item=self._target
        )
        by_id = {it["id"]: it for it in result}
        assert by_id["c"]["load_level_label"] == "影響度を導出できない候補"
        assert by_id["c"]["load_level"] == ""
        assert teacher_triage.LOAD_UNDERIVABLE_LABEL == "影響度を導出できない候補"

    def test_only_level_and_label_keys_are_added_and_no_raw_values(self):
        """生値非漏洩（TT2）: 付与キーは2つだけ・値は文字列のみ（float を運ばない）。"""
        items = self._items()
        before_keys = [set(it.keys()) for it in items]
        result = teacher_triage.annotate_and_sort_by_load(
            items, self.LEVELS, target_for_item=self._target
        )
        for original_keys, item in zip(before_keys, sorted(result, key=lambda it: it["id"])):
            added = set(item.keys()) - original_keys
            assert added == {"load_level", "load_level_label"}
            assert not any(isinstance(v, float) for v in item.values())
            assert "load_score" not in item


# ---------------------------------------------------------------------------
# 監査 metadata（TT3）
# ---------------------------------------------------------------------------


class TestSortMetadata:
    def test_sort_order_is_recorded_when_declared(self):
        meta = teacher_triage.sort_metadata({"action": "x"}, "load")
        assert meta["sort_order"] == "load"

    def test_unspecified_sort_order_is_not_fabricated(self):
        """未指定を default と偽装しない（設計書 §2）。"""
        assert "sort_order" not in teacher_triage.sort_metadata({"action": "x"}, None)
        assert "sort_order" not in teacher_triage.sort_metadata({"action": "x"}, "")

    def test_sort_vocabulary_is_fixed(self):
        assert teacher_triage.SORT_ORDERS == ("default", "load")


# ---------------------------------------------------------------------------
# モジュール規律（core 非 FastAPI）
# ---------------------------------------------------------------------------


class TestModuleDiscipline:
    def test_core_module_does_not_import_fastapi_or_llm(self):
        src = (BACKEND / "core" / "teacher_triage.py").read_text(encoding="utf-8")
        for forbidden in ("fastapi", "openai", "core.llm ", "from core.llm import"):
            assert forbidden not in src, f"core/teacher_triage.py must not import {forbidden!r}"

    def test_no_local_label_table_redefinition(self):
        """LOAD_LEVEL_LABELS（低/中/高/最高位）を再定義しない（doubt/schema が正本）。"""
        src = (BACKEND / "core" / "teacher_triage.py").read_text(encoding="utf-8")
        assert '"最高位"' not in src
        assert "from core.doubt.schema import" in src
