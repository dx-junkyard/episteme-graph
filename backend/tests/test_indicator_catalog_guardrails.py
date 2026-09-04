"""制度指標カタログのガードレール（``docs/features/indicator_governance_design.md`` §7）。

構造的に守るもの:

- core の純粋性（``core/indicator_catalog.py`` が FastAPI / sqlalchemy / LLM を掴まない）
- 全 spec の ``route`` が**実際に登録されている API パス**であること（綴り間違いの検出）
- **IG4 網羅**: 既知の集約経路がすべてカタログに現れること（カタログに無い集約 API を
  新設できない）
- ``trace_registry.CONSUMERS`` の教員露出消費者がカタログの ``consumer`` と一致すること
- ``GET /api/indicators`` が ``_get_current_user`` を使い ``_require_teacher`` を使わない
  こと（IG1: 観察される側の学習者も定義を読める）
- 全 ``label`` がマニュアル（student / system_admin）に逐語で現れること
- フロントがカタログ項目から数値を読まないこと
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    collect_route_pairs,
)

from core import indicator_catalog as ic  # noqa: E402

_CATALOG_SRC_PATH = BACKEND / "core" / "indicator_catalog.py"
_ROUTE_SRC_PATH = BACKEND / "api" / "routes" / "indicators.py"
_MAIN_SRC_PATH = BACKEND / "api" / "main.py"
_JS_PATH = ROOT / "frontend" / "public" / "js" / "admin-indicators.js"
_DESIGN_DOC = ROOT / "docs" / "features" / "indicator_governance_design.md"
_STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "01-specification.md"
_SYSADMIN_MANUAL = ROOT / "docs" / "manual" / "system_admin" / "13-admin-llm-usage.md"
_TEACHER_MANUAL = ROOT / "docs" / "manual" / "teacher" / "21-admin-interest-dashboard.md"

_CATALOG_SRC = _CATALOG_SRC_PATH.read_text(encoding="utf-8")
_ROUTE_SRC = _ROUTE_SRC_PATH.read_text(encoding="utf-8")
_MAIN_SRC = _MAIN_SRC_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. core の純粋性
# ---------------------------------------------------------------------------


class TestCorePurity:
    def test_catalog_module_does_not_import_web_db_or_llm(self):
        assert_module_tree_does_not_import(
            [_CATALOG_SRC_PATH],
            ["fastapi", "sqlalchemy", "core.llm", "core.postgres", "openai"],
        )

    def test_catalog_module_holds_no_values(self):
        """カタログは定義だけを持つ（DB を引く・集計する関数を持たない）。"""
        assert_source_forbids(
            _CATALOG_SRC,
            ["get_session", "sa_text", "SELECT ", "collect_metrics("],
            context="core/indicator_catalog.py（IG1: 値を持たない）",
        )

    def test_catalog_module_is_importable_without_fastapi(self):
        """subprocess で単独 import できる（推移的な純粋性）。"""
        import subprocess

        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); "
             "from core import indicator_catalog as ic; ic.validate_catalog(); "
             "assert 'fastapi' not in sys.modules; "
             "assert 'sqlalchemy' not in sys.modules; print('ok')" % str(BACKEND)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# 2. route が実在すること（綴り間違いの検出）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registered_paths() -> set[str]:
    pytest.importorskip("fastapi")
    from api.main import app

    return {path for path, _method in collect_route_pairs(app)}


class TestRoutesExist:
    def test_every_indicator_route_is_a_registered_path(self, registered_paths):
        missing = sorted(
            f"{spec.id} -> {spec.route}"
            for spec in ic.all_indicators()
            if spec.route not in registered_paths
        )
        assert missing == [], (
            f"カタログの route が実際の API パスとして登録されていません: {missing}"
            "（ルーターの prefix 込みの実パスで書くこと）"
        )

    def test_every_indicator_route_literal_segment_appears_in_a_route_module(self):
        """パスの最後の**リテラル**セグメントがルーターのソースに逐語で現れること。

        ``registered_paths`` との突合（上のテスト）が本命だが、パス変数を除いた
        リテラル部分がどのルーターにも無い＝カタログだけが知っている綴り、という
        状態を追加で塞ぐ。
        """
        blob = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((BACKEND / "api" / "routes").glob("*.py"))
        )
        missing = []
        for spec in ic.all_indicators():
            literals = [
                seg for seg in spec.route.strip("/").split("/")
                if seg and not seg.startswith("{")
            ]
            assert literals, spec.id
            if literals[-1] not in blob:
                missing.append(f"{spec.id} -> {literals[-1]}")
        assert missing == [], f"ルーターのソースに現れない route セグメント: {missing}"

    def test_indicator_ids_are_referenced_from_the_metric_responses(self):
        """``indicator_id`` を足した経路は、実在する id を名乗ること。"""
        blob = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((BACKEND / "api" / "routes").glob("*.py"))
        )
        used = set(re.findall(r'"indicator_id":\s*"([a-z0-9-]+)"', blob))
        used |= set(re.findall(r'\["indicator_id"\]\s*=\s*"([a-z0-9-]+)"', blob))
        assert used, "indicator_id を名乗る計器レスポンスが1つも無い"
        unknown = sorted(i for i in used if i not in ic.INDICATORS)
        assert unknown == [], f"カタログに無い indicator_id: {unknown}"


# ---------------------------------------------------------------------------
# 3. IG4 — 既知の集約経路の網羅
# ---------------------------------------------------------------------------


#: 2026-09-04 時点の「教員・管理者・本人に集約や記録を見せる」経路の実測一覧。
#: **ここに1本足したらカタログにも1件足す**（逆も同じ）。
_KNOWN_AGGREGATE_ROUTES = (
    "/api/admin/llm-usage/metrics",
    "/api/admin/llm-usage/forecast",
    "/api/admin/llm-usage/estimate/documents/{document_id}",
    "/api/admin/doubt/metrics",
    "/api/admin/discuss/observation-status",
    "/api/admin/interest-dashboard",
    "/api/admin/courses/{course_id}/bridge-insights",
    "/api/admin/courses/{course_id}/anchor-insights",
    "/api/admin/discovery/frontier-interest",
    "/api/admin/doubt/courses/{course_id}/naive-signals",
    "/api/admin/reconstruction/items/review-queue",
    "/api/admin/documents/{document_id}/claims/stumble-summary",
    "/api/admin/assistant/next-steps",
    "/api/admin/users/{user_id}/activity",
    "/api/me/records",
)


class TestCoverage:
    def test_every_known_aggregate_route_maps_to_an_indicator(self):
        missing = sorted(
            route for route in _KNOWN_AGGREGATE_ROUTES
            if not ic.indicators_for_route(route)
        )
        assert missing == [], (
            f"カタログに登録されていない集約経路: {missing}"
            "（IG4 — 集約 API を足したら core/indicator_catalog.py にも1件足す）"
        )

    def test_no_indicator_points_at_an_unknown_route(self):
        extra = sorted(
            f"{spec.id} -> {spec.route}"
            for spec in ic.all_indicators()
            if spec.route not in _KNOWN_AGGREGATE_ROUTES
        )
        assert extra == [], (
            f"既知の集約経路一覧に無い route がカタログにあります: {extra}"
            "（本テストの _KNOWN_AGGREGATE_ROUTES も更新すること）"
        )

    def test_known_aggregate_routes_are_all_registered(self, registered_paths):
        missing = sorted(r for r in _KNOWN_AGGREGATE_ROUTES if r not in registered_paths)
        assert missing == [], f"実在しない経路が一覧に残っています: {missing}"


#: ``trace_registry.CONSUMERS`` のうち**教員・管理者に露出する**消費者と、対応する計器。
#: 学習者本人向けの消費者（問いの軌跡・digest・わたしの地図）は集約ではないので入れない。
_TEACHER_EXPOSED_CONSUMERS = {
    "aggregate_interest_dashboard": "interest-dashboard",
    "next_steps_help_gaps": "help-gaps-pending",
    "naive_signal": "naive-signals",
    "stumble": "claims-stumble-summary",
    "bridges": "bridge-insights",
    "frontier_interest_aggregate": "frontier-interest",
}


class TestTraceRegistryAlignment:
    def test_declared_consumers_exist_in_the_registry(self):
        from core import trace_registry

        unknown = sorted(k for k in _TEACHER_EXPOSED_CONSUMERS if k not in trace_registry.CONSUMERS)
        assert unknown == [], f"trace_registry.CONSUMERS に無い消費者: {unknown}"

    def test_every_teacher_exposed_consumer_has_an_indicator(self):
        from core import trace_registry

        for consumer_key, indicator_id in _TEACHER_EXPOSED_CONSUMERS.items():
            spec = ic.get_indicator(indicator_id)
            assert spec is not None, f"{indicator_id} がカタログに無い"
            entry = trace_registry.CONSUMERS[consumer_key]
            module = str(entry["module"]).replace("backend/", "", 1)
            assert spec.consumer.startswith(module), (
                f"{indicator_id}: consumer={spec.consumer!r} が "
                f"trace_registry の module={module!r} と一致しない"
            )
            fn = entry.get("function")
            if fn:
                assert spec.consumer.endswith(f"::{fn}"), (
                    f"{indicator_id}: consumer={spec.consumer!r} が "
                    f"trace_registry の function={fn!r} と一致しない"
                )

    def test_route_modules_using_the_k_anonymity_helpers_are_covered(self):
        """k-匿名ヘルパをルート層で直接使うなら、その経路は計器として宣言されていること。"""
        covered_files = set()
        for spec in ic.all_indicators():
            # consumer が routes/*.py を指すものと、route を持つルーターの両方を拾う。
            for path in sorted((BACKEND / "api" / "routes").glob("*.py")):
                tail = "/" + spec.route.rstrip("/").rsplit("/", 1)[-1]
                if f'"{tail}"' in path.read_text(encoding="utf-8"):
                    covered_files.add(path.name)

        offenders = []
        for path in sorted((BACKEND / "api" / "routes").glob("*.py")):
            src = path.read_text(encoding="utf-8")
            if "bucket_count_range" in src or "meets_k_anonymity" in src:
                if path.name not in covered_files:
                    offenders.append(path.name)
        assert offenders == [], (
            f"k-匿名ヘルパを使うが計器として宣言されていないルーター: {offenders}"
        )


# ---------------------------------------------------------------------------
# 4. IG1 — 定義は全当事者に公開する（教員ゲートを掛けない）
# ---------------------------------------------------------------------------


class TestPublicAccess:
    def test_router_uses_current_user_not_require_teacher(self):
        """IG1: 定義は全当事者が読める — ロールゲートの依存を使わない。

        （説明のために docstring / コメントで ``_require_teacher`` の名に触れることは
        あるので、**依存注入と import の形**で検査する。）
        """
        assert "Depends(_get_current_user)" in _ROUTE_SRC
        code_lines = [
            line for line in _ROUTE_SRC.splitlines()
            if not line.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        for term in ("Depends(_require_teacher)", "Depends(_require_system_admin)"):
            assert term not in code, (
                f"routes/indicators.py が {term} を使っています"
                "（IG1: 観察される側の学習者も定義を読めなければならない）"
            )
        import_lines = [ln for ln in code_lines if ln.startswith(("from ", "import "))]
        for line in import_lines:
            assert "_require_teacher" not in line and "_require_system_admin" not in line

    def test_router_is_read_only(self):
        for verb in (".post(", ".put(", ".patch(", ".delete("):
            assert f"@router{verb}" not in _ROUTE_SRC, (
                f"カタログはコードが正本 — API から書き換えられない（{verb} を作らない）"
            )

    def test_router_is_registered_in_main(self):
        assert "from routes import indicators as indicators_routes" in _MAIN_SRC
        assert "app.include_router(indicators_routes.router)" in _MAIN_SRC

    def test_route_prefix_is_not_under_admin(self):
        assert 'APIRouter(prefix="/api/indicators"' in _ROUTE_SRC
        assert "/api/admin/indicators" not in _ROUTE_SRC


# ---------------------------------------------------------------------------
# 5. マニュアル・設計書との突合
# ---------------------------------------------------------------------------


class TestDocumentation:
    def test_design_doc_exists_and_declares_the_invariants(self):
        doc = _DESIGN_DOC.read_text(encoding="utf-8")
        assert "実装済み" in doc[:1500]
        for tag in ("IG1", "IG2", "IG3", "IG4", "IG5"):
            assert tag in doc, f"設計書に {tag} が無い"

    def test_every_label_appears_verbatim_in_a_manual(self):
        student = _STUDENT_MANUAL.read_text(encoding="utf-8")
        sysadmin = _SYSADMIN_MANUAL.read_text(encoding="utf-8")
        missing = sorted(
            spec.label for spec in ic.all_indicators()
            if spec.label not in student and spec.label not in sysadmin
        )
        assert missing == [], (
            f"マニュアルに逐語で現れない計器名: {missing}"
            "（student/01-specification.md または system_admin/13-admin-llm-usage.md に書く）"
        )

    def test_learner_facing_indicators_are_listed_for_learners(self):
        """学習者の活動が入りうる計器は、学生向けマニュアルに名前で載せる。"""
        student = _STUDENT_MANUAL.read_text(encoding="utf-8")
        learner_facing = (
            "interest-dashboard", "bridge-insights", "anchor-insights",
            "naive-signals", "frontier-interest", "reconstruction-review-queue",
            "claims-stumble-summary", "help-gaps-pending",
            "discuss-observation-status", "llm-usage-metrics", "my-records",
        )
        missing = sorted(
            ic.INDICATORS[i].label for i in learner_facing
            if ic.INDICATORS[i].label not in student
        )
        assert missing == [], f"学生向けマニュアルに無い計器名: {missing}"

    def test_manual_sections_have_explicit_anchors(self):
        assert "{#institutional-indicators}" in _STUDENT_MANUAL.read_text(encoding="utf-8")
        assert "{#indicator-catalog}" in _SYSADMIN_MANUAL.read_text(encoding="utf-8")
        assert "{#indicator-fact}" in _TEACHER_MANUAL.read_text(encoding="utf-8")

    def test_student_manual_section_has_no_denylisted_terms(self):
        from core.help_kb.validator import STUDENT_DENYLIST

        text = _STUDENT_MANUAL.read_text(encoding="utf-8")
        offenders = sorted(term for term in STUDENT_DENYLIST if term in text)
        assert offenders == [], f"学生向けマニュアルの禁止語彙: {offenders}"

    def test_student_manual_states_the_non_uses(self):
        text = _STUDENT_MANUAL.read_text(encoding="utf-8")
        section = text[text.index("{#institutional-indicators}"):]
        for phrase in ("成績評価には使いません", "ランキング", "自動的な判定", "k-匿名"):
            assert phrase in section, f"学生向け節に「{phrase}」が無い"

    def test_design_docs_referenced_by_specs_exist(self):
        missing = sorted(
            f"{spec.id} -> {spec.design_doc}"
            for spec in ic.all_indicators()
            if not (ROOT / spec.design_doc).exists()
        )
        assert missing == [], f"存在しない設計書を指している spec: {missing}"

    def test_api_doc_mentions_the_router(self):
        doc = (ROOT / "docs" / "backend" / "api.md").read_text(encoding="utf-8")
        assert "routes/indicators.py" in doc
        assert "/api/indicators" in doc


# ---------------------------------------------------------------------------
# 6. フロント — カタログから数値を描かない
# ---------------------------------------------------------------------------


class TestFrontend:
    def test_js_exists_and_exposes_the_module(self):
        js = _JS_PATH.read_text(encoding="utf-8")
        assert "window.AdminIndicators" in js
        assert "factLine" in js and "mount" in js

    def test_js_never_reads_numeric_fields_from_catalog_items(self):
        js = _JS_PATH.read_text(encoding="utf-8")
        forbidden = (
            ".count", ".total", ".value", ".score", ".weight",
            ".confidence", ".cohort_size", ".rows", ".tokens",
        )
        offenders = sorted(term for term in forbidden if term in js)
        assert offenders == [], (
            f"カタログ項目から数値らしいフィールドを読んでいます: {offenders}"
            "（IG1: カタログは定義だけ — 数値を描く経路を作らない）"
        )

    def test_js_states_the_non_uses_in_the_fact_line(self):
        js = _JS_PATH.read_text(encoding="utf-8")
        assert "個人の比較・成績・自動判定には使いません" in js

    def test_js_fails_soft(self):
        js = _JS_PATH.read_text(encoding="utf-8")
        assert ".catch(" in js
        # ポーリングしない（定期タイマーで再取得しない）。
        assert "setInterval" not in js

    def test_js_is_registered_in_the_anchor_coverage_sources(self):
        src = (BACKEND / "tests" / "test_admin_help_inspect_ui_static.py").read_text(
            encoding="utf-8"
        )
        block = src[src.index("_ADMIN_FRONTEND_SOURCES"):]
        block = block[: block.index("]")]
        assert "admin-indicators.js" in block

    def test_admin_html_loads_the_module_before_its_consumers(self):
        html = (ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
        assert "/js/admin-indicators.js" in html
        assert html.index("/js/admin-indicators.js") < html.index("/js/admin-llm-usage.js")
        assert html.index("/js/admin-indicators.js") < html.index("/js/admin.js?")
