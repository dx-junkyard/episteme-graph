"""痕跡 kind 登録簿（core/trace_registry.py）のガードレール。

正本: docs/features/trace_registry_sovereignty_ledger_design.md §2.3。

- TR1: 登録簿が kind の単一の真実源（services._INTEREST_KINDS は登録簿からの導出、
  cycle 語彙定数は登録簿に存在、statuses は _TRACE_STATUSES の部分集合、
  9 kind 全登録・dead は detour のみ）
- TR2: 消費面が登録簿と一致する（B方式 denylist の2消費者に除外集合の**全要素**が
  現れる / A方式 allowlist の各消費者に宣言 kind のリテラルが全て現れる /
  payload_flag 方式のフラグ逐語）
- 純宣言モジュール（FastAPI / sqlalchemy / core.llm 非 import）
- Part B（trace_ledger / my_records）の読み取り専用・本人のみ（TR4）
- §2.4 の是正（superseded フィルタ）が剥がれていない
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import trace_registry  # noqa: E402
from core.trace_registry import (  # noqa: E402
    ALL_TRACE_KINDS,
    CONSUMERS,
    DASHBOARD_EXCLUDED_KINDS,
    TRACE_KINDS,
    TRAJECTORY_EXCLUDED_KINDS,
)
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

_TRACE_REGISTRY_SRC = (BACKEND / "core" / "trace_registry.py").read_text(encoding="utf-8")
_SERVICES_SRC = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
_TRACE_LEDGER_SRC = (BACKEND / "core" / "trace_ledger.py").read_text(encoding="utf-8")
_MY_RECORDS_SRC = (BACKEND / "api" / "routes" / "my_records.py").read_text(encoding="utf-8")
_STRUCTURE_ANCHOR_WORKER_SRC = (BACKEND / "core" / "structure_anchor" / "worker.py").read_text(
    encoding="utf-8"
)
_LEARNING_ROUTE_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")


# ===========================================================================
# 1. 語彙の二重管理を同値で固定（TR1）
# ===========================================================================


class TestVocabularySingleSource:
    def test_services_interest_kinds_equals_registry(self):
        from api import services

        assert services._INTEREST_KINDS == trace_registry.ALL_TRACE_KINDS

    def test_cycle_schema_kinds_are_registered(self):
        from core.cycle.schema import KIND_ANCHOR_MARK, KIND_INTENTION

        assert KIND_INTENTION in TRACE_KINDS
        assert KIND_ANCHOR_MARK in TRACE_KINDS

    def test_all_nine_kinds_are_registered(self):
        assert ALL_TRACE_KINDS == frozenset({
            "raw", "question", "detour", "misconception", "tension",
            "help_usage", "intention", "anchor_mark", "backstage_question",
        })
        # 露出3宣言は dataclass 必須フィールドで構文的にも強制されるが、
        # dead 語彙（detour）も含めて9件存在することをここで固定する（TR3）。
        assert len(TRACE_KINDS) == 9

    def test_only_detour_is_dead(self):
        dead = {kind for kind, spec in TRACE_KINDS.items() if spec.dead}
        assert dead == {"detour"}

    def test_kind_field_matches_dict_key(self):
        for kind, spec in TRACE_KINDS.items():
            assert spec.kind == kind

    def test_statuses_are_subsets_of_trace_statuses(self):
        from api import services

        allowed = set(services._TRACE_STATUSES)
        for kind, spec in TRACE_KINDS.items():
            assert spec.statuses <= allowed, (
                f"kind={kind} の statuses に _TRACE_STATUSES 外の語彙がある: "
                f"{sorted(spec.statuses - allowed)}"
            )


# ===========================================================================
# 2. B方式（denylist）の2消費者 — 除外集合の全要素が除外式として現れる（TR2）
# ===========================================================================


class TestDenylistConsumers:
    def test_get_interest_traces_excludes_every_trajectory_excluded_kind(self):
        body = extract_function_source(_SERVICES_SRC, "get_interest_traces")
        assert TRAJECTORY_EXCLUDED_KINDS, "除外集合が空になっている（登録簿の破損）"
        for kind in sorted(TRAJECTORY_EXCLUDED_KINDS):
            assert f"'{kind}'" in body, (
                f"get_interest_traces が kind='{kind}' を明示除外していない"
                "（新しい kind を登録簿に足したら除外式も更新すること）"
            )

    def test_aggregate_interest_dashboard_excludes_every_dashboard_excluded_kind(self):
        body = extract_function_source(_SERVICES_SRC, "aggregate_interest_dashboard")
        assert DASHBOARD_EXCLUDED_KINDS, "除外集合が空になっている（登録簿の破損）"
        for kind in sorted(DASHBOARD_EXCLUDED_KINDS):
            assert f"'{kind}'" in body, (
                f"aggregate_interest_dashboard が kind='{kind}' を明示除外していない"
                "（新しい kind を登録簿に足したら除外式も更新すること）"
            )


# ===========================================================================
# 3. CONSUMERS 宣言表とソースの一致（TR2 — 登録簿駆動）
# ===========================================================================


def _consumer_source(entry: dict) -> str:
    src = (ROOT / entry["module"]).read_text(encoding="utf-8")
    if entry.get("function"):
        return extract_function_source(src, entry["function"])
    return src


class TestConsumersMatchRegistry:
    @pytest.mark.parametrize(
        "name",
        sorted(n for n, e in CONSUMERS.items() if e["mode"] == "allowlist"),
    )
    def test_allowlist_consumer_mentions_every_declared_kind(self, name):
        entry = CONSUMERS[name]
        assert entry["kinds"], f"{name}: allowlist なのに宣言 kind が空"
        assert entry["kinds"] <= ALL_TRACE_KINDS, f"{name}: 未登録 kind を宣言している"
        body = _consumer_source(entry)
        for kind in sorted(entry["kinds"]):
            assert f"'{kind}'" in body or f'"{kind}"' in body, (
                f"{name}（{entry['module']}）に宣言 kind '{kind}' のリテラルが現れない"
                "（消費者を変更したら CONSUMERS の宣言も更新すること）"
            )

    @pytest.mark.parametrize(
        "name",
        sorted(n for n, e in CONSUMERS.items() if e["mode"] == "payload_flag"),
    )
    def test_payload_flag_consumer_mentions_the_flag(self, name):
        entry = CONSUMERS[name]
        assert entry["kinds"] == frozenset(), f"{name}: payload_flag 方式は kind 条件なし"
        body = _consumer_source(entry)
        assert entry["flag"] in body, (
            f"{name}（{entry['module']}）に payload フラグ '{entry['flag']}' が現れない"
        )

    def test_denylist_consumers_reference_derived_frozensets(self):
        """denylist の2消費者の宣言 kinds が導出 frozenset そのものであること
        （リテラル再宣言による黙った分裂の防止）。"""
        assert CONSUMERS["get_interest_traces"]["kinds"] is TRAJECTORY_EXCLUDED_KINDS
        assert (
            CONSUMERS["aggregate_interest_dashboard"]["kinds"] is DASHBOARD_EXCLUDED_KINDS
        )

    def test_consumer_modules_exist(self):
        for name, entry in CONSUMERS.items():
            assert (ROOT / entry["module"]).exists(), (
                f"{name}: CONSUMERS が存在しないモジュールを指している: {entry['module']}"
            )


# ===========================================================================
# 4. 純宣言モジュール（FastAPI / sqlalchemy / core.llm 非依存）
# ===========================================================================


class TestRegistryIsPureDeclaration:
    def test_registry_has_no_framework_dependency(self):
        assert_source_does_not_import(
            _TRACE_REGISTRY_SRC,
            ["fastapi", "sqlalchemy", "core.llm", "core.postgres", "openai"],
            context="core/trace_registry.py",
        )

    def test_registry_has_no_db_session(self):
        assert "get_session" not in _TRACE_REGISTRY_SRC


# ===========================================================================
# 5. Part B（台帳・API）の読み取り専用・本人のみ（TR4）
# ===========================================================================


class TestLedgerAndRouteAreReadOnly:
    def test_trace_ledger_does_not_import_fastapi(self):
        assert_source_does_not_import(
            _TRACE_LEDGER_SRC,
            ["fastapi", "core.llm", "openai"],
            context="core/trace_ledger.py",
        )

    def test_trace_ledger_has_no_write_sql(self):
        assert_source_forbids(
            _TRACE_LEDGER_SRC,
            ["INSERT INTO", "UPDATE interest_traces", "DELETE FROM"],
            context="core/trace_ledger.py",
        )

    def test_my_records_route_has_no_write_methods_and_no_user_id_param(self):
        assert_source_forbids(
            _MY_RECORDS_SRC,
            [
                "@me_router.post",
                "@me_router.put",
                "@me_router.patch",
                "@me_router.delete",
                "{user_id}",
            ],
            context="api/routes/my_records.py",
        )


# ===========================================================================
# 6. §2.4 の是正 — superseded フィルタが剥がれていない
# ===========================================================================


class TestSupersededFilterFix:
    def test_fetch_pending_questions_excludes_superseded(self):
        body = extract_function_source(
            _STRUCTURE_ANCHOR_WORKER_SRC, "_fetch_pending_questions"
        )
        assert "status <> 'superseded'" in body, (
            "structure_anchor/worker.py::_fetch_pending_questions から superseded "
            "除外が消えている（書き直しで消した問いを帰属解析してしまう。設計書 §2.4）"
        )

    def test_anchor_digest_lazy_trigger_excludes_superseded(self):
        body = extract_function_source(_LEARNING_ROUTE_SRC, "get_anchor_digest_route")
        assert "status <> 'superseded'" in body, (
            "routes/learning.py の anchor 遅延起動クエリから superseded 除外が"
            "消えている（設計書 §2.4）"
        )
