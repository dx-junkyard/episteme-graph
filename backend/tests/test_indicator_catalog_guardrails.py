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
    iter_app_routes,
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


#
# 検査の形（2026-09-05 に手書き一覧から自動走査へ置換）:
#
#   旧実装は「既知の集約経路」15本を**手で**書いた一覧とカタログの双方向一致だけを
#   見ていた。どちらにも書かなければ緑のまま通るため、実際には未収録の集約 GET が
#   10本あった（`unanswered-queries` / `open-assumptions` / `ledger-summary` /
#   `assumption-atlas` / `seminar-brief` / `sharing-dashboard` / `materials-stats` /
#   `landscape/overview` / `forecast/documents/{id}` / `ingest-estimate`）。
#   IG4 が「足したら気付く」ための条項である以上、**母集合はアプリ側から取る**。
#
#   母集合 = FastAPI に登録済みの **GET** で、依存に `_require_teacher` /
#   `_require_system_admin` を持つもの（＝教員・管理者に何かを見せる経路）。
#   各経路は次のいずれかでなければならない:
#
#     (a) カタログの `route` に載っている（＝計器として宣言されている）
#     (b) 下の `_NON_INDICATOR_ROUTES` に**分類理由つき**で載っている
#
#   分類理由は自由文ではなく下の定数語彙から選ぶ（「集約ではない」と言い切れる
#   類型だけを置く）。新しい GET を足すと、どちらかへ書くまでテストが落ちる。
#

#: 分類理由の語彙（自由文を書かせない — 類型で説明できないものは計器として宣言する）。
_R_OBJECT = "単一オブジェクトの取得・一覧（保存されている物をそのまま返す。集約値ではない）"
_R_SETTING = "設定・語彙・カタログの読み出し（観察ではなく構成の参照）"
_R_ASSET = "ファイル・画像・音声の配信（バイナリ）"
_R_WORKSPACE = "人が確定するための候補・下書き・レビュー対象そのものの提示（AI 候補を含む）"
_R_STATE = "処理・オブジェクトの状態表示（進捗の有無・最終実行時刻。人の活動を数えない）"
_R_OPS_LOG = "運用ログ・イベント時系列の読み出し（自分の所有物 or 管理者の運用面）"
_R_EXPORT = "宣言済みの計器と同一 source の生データ持ち出し（集約値ではない）"
_R_ACCOUNT = "認可済みのアカウント一覧（アカウント運用。学習データではない）"

_NON_INDICATOR_REASONS = (
    _R_OBJECT, _R_SETTING, _R_ASSET, _R_WORKSPACE,
    _R_STATE, _R_OPS_LOG, _R_EXPORT, _R_ACCOUNT,
)

#: 計器ではない教員・管理者向け GET（パスは実パス。**プレフィックスの一括許可はしない** —
#: 新しい経路を1本足したら必ずここかカタログに1行増える）。
_NON_INDICATOR_ROUTES: dict[str, str] = {
    # -- Admin Copilot / 操作支援 -----------------------------------------
    "/api/admin/assistant/actions": _R_OPS_LOG,
    "/api/admin/assistant/capabilities": _R_SETTING,
    "/api/admin/assistant/help/ui-anchors": _R_SETTING,
    # -- 分野の地図（骨格・候補・別名） ------------------------------------
    "/api/admin/atlas/domains": _R_OBJECT,
    "/api/admin/cartridges": _R_SETTING,
    "/api/admin/cartridges/{cartridge_id}": _R_SETTING,
    "/api/admin/cartridges/{cartridge_id}/atlas/aliases": _R_OBJECT,
    "/api/admin/cartridges/{cartridge_id}/atlas/edge-candidates": _R_WORKSPACE,
    "/api/admin/cartridges/{cartridge_id}/atlas/freeze-impact": _R_WORKSPACE,
    "/api/admin/cartridges/{cartridge_id}/atlas/gap-candidates": _R_WORKSPACE,
    "/api/admin/cartridges/{cartridge_id}/atlas/reports": _R_WORKSPACE,
    "/api/admin/cartridges/{cartridge_id}/atlas/skeleton": _R_OBJECT,
    "/api/admin/cartridges/{cartridge_id}/atlas/vectors/status": _R_STATE,
    "/api/admin/cartridges/{cartridge_id}/component-types": _R_SETTING,
    "/api/admin/cartridges/{cartridge_id}/maturity-levels": _R_SETTING,
    "/api/admin/cartridges/{cartridge_id}/ontology": _R_SETTING,
    "/api/admin/cartridges/{cartridge_id}/relation-types": _R_SETTING,
    "/api/admin/cartridges/{cartridge_id}/support-statuses": _R_SETTING,
    # -- コース・原稿スタジオ ----------------------------------------------
    "/api/admin/chunks/{chunk_id}/lecture-audio": _R_ASSET,
    "/api/admin/course-builder/sessions": _R_OBJECT,
    "/api/admin/course-builder/sessions/{session_id}": _R_OBJECT,
    "/api/admin/courses": _R_OBJECT,
    "/api/admin/courses/{course_id}/draft-format": _R_SETTING,
    "/api/admin/courses/{course_id}/groups": _R_OBJECT,
    "/api/admin/courses/{course_id}/lecture-scripts": _R_OBJECT,
    "/api/admin/courses/{course_id}/lecture-studio/components": _R_OBJECT,
    "/api/admin/courses/{course_id}/lecture-studio/course-structure": _R_OBJECT,
    "/api/admin/courses/{course_id}/lecture-studio/document-structure": _R_OBJECT,
    "/api/admin/courses/{course_id}/lecture-studio/settings": _R_SETTING,
    "/api/admin/courses/{course_id}/tasks/active": _R_STATE,
    "/api/admin/courses/{course_id}/teaching-figures": _R_OBJECT,
    "/api/admin/courses/{course_id}/teaching-figures/{figure_id}/image": _R_ASSET,
    "/api/admin/courses/{course_id}/theory-components": _R_OBJECT,
    "/api/admin/courses/{course_id}/topics/{topic_id}/figure-suggestions": _R_WORKSPACE,
    # -- W層（要素検討ワークスペース） --------------------------------------
    "/api/admin/deliberation/documents/{document_id}/elements": _R_OBJECT,
    "/api/admin/deliberation/elements/{element_type}/{element_id}/annotations": _R_WORKSPACE,
    "/api/admin/deliberation/elements/{element_type}/{element_id}/context": _R_OBJECT,
    "/api/admin/deliberation/elements/{element_type}/{element_id}/identity-links": _R_WORKSPACE,
    "/api/admin/deliberation/elements/{element_type}/{element_id}/overview": _R_OBJECT,
    "/api/admin/deliberation/elements/{element_type}/{element_id}/shared-part-candidates": _R_WORKSPACE,
    "/api/admin/deliberation/sessions/{session_id}": _R_OBJECT,
    "/api/admin/deliberation/shared-parts/{shared_part_id}/identity-links": _R_WORKSPACE,
    # -- 論文ディスカバリー -------------------------------------------------
    "/api/admin/discovery/ingest-queue": _R_STATE,
    "/api/admin/discovery/radar/seed": _R_OBJECT,
    "/api/admin/discovery/subscriptions": _R_SETTING,
    "/api/admin/discovery/subscriptions/{domain_key}/keyphrase-candidates": _R_WORKSPACE,
    # -- discuss 観測基盤 ---------------------------------------------------
    # 観測ダンプは値の集約ではなく、`discuss-observation-status` と同じ
    # `discuss_metric_events` の**生行**（本文非含有・仮名化）の持ち出し。
    # 定義は計器 discuss-observation-status 側に書いてある。
    "/api/admin/discuss/observation-dump": _R_EXPORT,
    # -- 教材の解析成果 -----------------------------------------------------
    "/api/admin/documents/{document_id}/chunks/{chunk_id}/claims": _R_OBJECT,
    "/api/admin/documents/{document_id}/component-graph": _R_OBJECT,
    "/api/admin/documents/{document_id}/element-explanations": _R_WORKSPACE,
    "/api/admin/documents/{document_id}/figures": _R_OBJECT,
    "/api/admin/documents/{document_id}/figures/{figure_id}/image": _R_ASSET,
    "/api/admin/documents/{document_id}/groups": _R_OBJECT,
    "/api/admin/documents/{document_id}/paper-layer": _R_OBJECT,
    "/api/admin/documents/{document_id}/revisions": _R_OBJECT,
    "/api/admin/documents/{document_id}/revisions/{revision_id}": _R_OBJECT,
    "/api/admin/documents/{document_id}/revisions/{revision_id}/report": _R_OBJECT,
    "/api/admin/documents/{document_id}/revisions/{revision_id}/run-status": _R_STATE,
    "/api/admin/documents/{document_id}/sections/{section_id}/components": _R_OBJECT,
    "/api/admin/documents/{document_id}/structure": _R_OBJECT,
    # -- D層（台帳・疑義・反実仮想） ----------------------------------------
    # 反実仮想セッションは「自分が作った / 明示的に共有された」保存済みオブジェクトの
    # 一覧であって、集団の集計ではない。
    "/api/admin/doubt/counterfactual/sessions": _R_OBJECT,
    "/api/admin/doubt/courses/{course_id}/assumptions": _R_WORKSPACE,
    "/api/admin/doubt/courses/{course_id}/observation-targets": _R_OBJECT,
    "/api/admin/doubt/ledger/{target_type}/{target_id}": _R_OBJECT,
    "/api/admin/doubt/targets/{target_type}/{target_id}/challenges": _R_OBJECT,
    # -- 運用・KB ------------------------------------------------------------
    "/api/admin/error-logs": _R_OPS_LOG,
    "/api/admin/explanations/{explanation_id}/endorsements": _R_OBJECT,
    "/api/admin/help-kb/drafts": _R_OBJECT,
    "/api/admin/help-kb/drafts/{audience}/{file}": _R_OBJECT,
    "/api/admin/help-kb/versions": _R_OBJECT,
    # -- 配置層（個別レビュー。集約は landscape-overview が計器） -------------
    "/api/admin/landscape/courses/{course_id}/placements": _R_WORKSPACE,
    "/api/admin/landscape/documents/{document_ref}/placements": _R_WORKSPACE,
    # -- L層ライブラリ -------------------------------------------------------
    "/api/admin/library/domains": _R_SETTING,
    "/api/admin/library/entries": _R_OBJECT,
    "/api/admin/library/entries/{entry_id}": _R_OBJECT,
    "/api/admin/library/entries/{entry_id}/versions": _R_OBJECT,
    # -- M層（モデル選択） ---------------------------------------------------
    "/api/admin/llm-models/catalog": _R_SETTING,
    "/api/admin/llm-models/pipeline-stages": _R_SETTING,
    "/api/admin/llm-models/policies": _R_SETTING,
    # -- 教材管理 -------------------------------------------------------------
    "/api/admin/materials": _R_OBJECT,
    "/api/admin/materials/{material_id}": _R_OBJECT,
    "/api/admin/materials/{material_id}/document-pipeline/status": _R_STATE,
    "/api/admin/materials/{material_id}/pdf": _R_ASSET,
    "/api/admin/notifications": _R_OBJECT,
    "/api/admin/reextraction-jobs": _R_STATE,
    "/api/admin/schema-proposals": _R_WORKSPACE,
    "/api/admin/schema/predicates": _R_SETTING,
    "/api/admin/schema/types": _R_SETTING,
    # -- V層（共有物のバージョン） -------------------------------------------
    "/api/admin/shared/notifications": _R_OBJECT,
    "/api/admin/shared/releases/{release_id}": _R_OBJECT,
    "/api/admin/shared/subscription/me": _R_OBJECT,
    "/api/admin/shared/{object_type}/{object_id}/releases": _R_OBJECT,
    "/api/admin/shared/{object_type}/{object_id}/version-state": _R_STATE,
    # -- 状態投影（G層と同じ更新規律） ---------------------------------------
    "/api/admin/status/courses/{course_id}": _R_STATE,
    "/api/admin/status/events": _R_OPS_LOG,
    "/api/admin/status/materials/{material_id}": _R_STATE,
    "/api/admin/status/overview": _R_STATE,
    "/api/admin/tasks/{task_id}": _R_STATE,
    "/api/admin/theory-components/{component_id}/explanations": _R_OBJECT,
    "/api/admin/url-fetch-domains": _R_SETTING,
    # -- AL層（アカウント運用。個票は account-activity が計器） ---------------
    "/api/admin/users": _R_ACCOUNT,
}

#: 2026-09-05 の棚卸しで「計器」と判定した経路。**allowlist へ移してはならない**
#: （移すと網羅テストは緑のままカタログから消せてしまうため、名指しで固定する）。
_TRIAGED_AS_INDICATORS = (
    "/api/admin/courses/{course_id}/unanswered-queries",
    "/api/admin/courses/{course_id}/sharing-dashboard",
    "/api/admin/doubt/courses/{course_id}/ledger-summary",
    "/api/admin/doubt/courses/{course_id}/open-assumptions",
    "/api/admin/doubt/courses/{course_id}/assumption-atlas",
    "/api/admin/documents/{document_ref}/seminar-brief",
    "/api/admin/system/materials-stats",
    "/api/admin/landscape/overview",
    "/api/admin/llm-usage/forecast/documents/{document_id}",
    "/api/admin/discovery/ingest-estimate",
)

#: カタログにあるがロールゲート GET ではない経路（＝自動走査の母集合に入らないもの）。
#: 本人だけが自分の記録を読む経路は `_get_current_user` で足りるため、ここに宣言する。
_NON_ROLE_GATED_INDICATOR_ROUTES = {
    "/api/me/records": "本人のみが自分の記録を読む（ロールゲートではなく本人ゲート）",
}


def _dependency_names(route) -> set[str]:
    """1ルートの依存ツリーに現れる依存関数名（推移的）。"""
    names: set[str] = set()
    root = getattr(route, "dependant", None)
    stack = [root] if root is not None else []
    while stack:
        current = stack.pop()
        call = getattr(current, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        stack.extend(getattr(current, "dependencies", None) or [])
    return names


@pytest.fixture(scope="module")
def role_gated_get_paths() -> set[str]:
    """教員・管理者向け GET の実測母集合（アプリの登録内容から取る）。"""
    pytest.importorskip("fastapi")
    from api.main import app

    paths: set[str] = set()
    for route in iter_app_routes(app):
        if "GET" not in (getattr(route, "methods", None) or set()):
            continue
        if {"_require_teacher", "_require_system_admin"} & _dependency_names(route):
            paths.add(getattr(route, "path", ""))
    return paths


class TestCoverage:
    def test_the_scan_finds_a_realistic_population(self, role_gated_get_paths):
        """走査そのものが空振りしていないこと（依存の取り方が変わったら気付く）。"""
        assert len(role_gated_get_paths) > 100, (
            "教員・管理者向け GET の母集合が小さすぎます — 依存の走査が壊れている可能性"
            f"（検出 {len(role_gated_get_paths)} 本）"
        )
        assert "/api/admin/interest-dashboard" in role_gated_get_paths

    def test_every_role_gated_get_is_an_indicator_or_explicitly_excluded(
        self, role_gated_get_paths
    ):
        """IG4 の本体 — 教員・管理者に見せる GET は、計器か、分類理由つきの除外か。"""
        catalog_routes = {spec.route for spec in ic.all_indicators()}
        untriaged = sorted(
            path for path in role_gated_get_paths
            if path not in catalog_routes and path not in _NON_INDICATOR_ROUTES
        )
        assert untriaged == [], (
            "教員・管理者向けの GET が計器としても除外としても宣言されていません: "
            f"{untriaged}\n"
            "（IG4 — 集約を返すなら core/indicator_catalog.py に IndicatorSpec を足す。"
            "集約でないなら本テストの _NON_INDICATOR_ROUTES に分類理由つきで足す）"
        )

    def test_excluded_routes_carry_a_reason_from_the_fixed_vocabulary(self):
        """除外理由は自由文でなく類型（「なんとなく集約ではない」を書かせない）。"""
        bad = sorted(
            f"{path} -> {reason!r}"
            for path, reason in _NON_INDICATOR_ROUTES.items()
            if reason not in _NON_INDICATOR_REASONS
        )
        assert bad == [], f"分類語彙に無い除外理由: {bad}"

    def test_excluded_routes_are_registered_and_not_also_indicators(
        self, role_gated_get_paths
    ):
        """除外一覧に幽霊（実在しない経路）・二重登録を残さない。"""
        catalog_routes = {spec.route for spec in ic.all_indicators()}
        stale = sorted(p for p in _NON_INDICATOR_ROUTES if p not in role_gated_get_paths)
        assert stale == [], f"実在しない（or ロールゲートでない）除外経路: {stale}"
        both = sorted(set(_NON_INDICATOR_ROUTES) & catalog_routes)
        assert both == [], f"計器と除外の二重登録: {both}"

    def test_triaged_aggregates_stay_in_the_catalog(self):
        """2026-09-05 に計器と判定した経路を、後から除外一覧へ逃がせない。"""
        moved = sorted(r for r in _TRIAGED_AS_INDICATORS if r in _NON_INDICATOR_ROUTES)
        assert moved == [], (
            f"計器と判定済みの経路が除外一覧へ移されています: {moved}"
        )
        missing = sorted(r for r in _TRIAGED_AS_INDICATORS if not ic.indicators_for_route(r))
        assert missing == [], f"計器と判定済みなのにカタログに無い経路: {missing}"

    def test_catalog_routes_outside_the_scan_are_declared(self, role_gated_get_paths):
        """ロールゲート GET でないカタログ経路は、理由つきで宣言されていること。"""
        undeclared = sorted(
            f"{spec.id} -> {spec.route}"
            for spec in ic.all_indicators()
            if spec.route not in role_gated_get_paths
            and spec.route not in _NON_ROLE_GATED_INDICATOR_ROUTES
        )
        assert undeclared == [], (
            f"母集合にもホワイトリストにも無いカタログ経路: {undeclared}"
        )

    def test_all_indicator_routes_are_registered(self, registered_paths):
        missing = sorted(
            spec.route for spec in ic.all_indicators()
            if spec.route not in registered_paths
        )
        assert missing == [], f"実在しない経路を指す計器: {missing}"


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

    def test_modules_using_the_k_anonymity_gate_are_declared_as_consumers(self):
        """k-匿名ゲートを使う実装モジュールは、カタログの ``consumer`` に現れること。

        旧実装は ``api/routes/*.py`` の中に ``bucket_count_range`` を探していたが、
        k-匿名ゲートはすべて ``api/services.py`` と ``core/**`` にあり、ルート層には
        1件も無いため**常に空振り**していた（対象0件でも緑）。実際の所在を走査し、
        非空であることも同時に固定する。
        """
        gate_helpers = (
            "bucket_count_range", "meets_k_anonymity", "count_range_gated", "gate_label",
            "K_ANONYMITY",
        )
        scanned: list[Path] = [BACKEND / "api" / "services.py"]
        scanned += sorted((BACKEND / "core").rglob("*.py"))

        # 語彙の中継（k の正本を各層の名前へ再輸出するだけで、計器そのものではない）。
        relay_modules = {"core/privacy.py", "core/doubt/schema.py"}

        users: list[str] = []
        for path in scanned:
            rel = str(path.relative_to(BACKEND))
            if rel in relay_modules:
                continue
            src = path.read_text(encoding="utf-8")
            if "core.privacy" not in src and "core import privacy" not in src:
                continue
            if not any(helper in src for helper in gate_helpers):
                continue
            users.append(rel)

        assert users, (
            "k-匿名ゲートの利用箇所が1件も見つかりません — 走査対象が壊れています"
            "（この検査は以前 routes/ だけを見ていて常に空振りしていた）"
        )

        consumer_modules = {
            spec.consumer.split("::", 1)[0] for spec in ic.all_indicators()
        }
        offenders = sorted(m for m in users if m not in consumer_modules)
        assert offenders == [], (
            f"k-匿名ゲートを使うが計器の consumer として現れないモジュール: {offenders}"
            "（集約を返すなら IndicatorSpec を足す。語彙の中継なら relay_modules に足す）"
        )

    def test_route_layer_does_not_gate_k_anonymity_on_its_own(self):
        """ルート層で k-匿名ゲートを組み直さない（正本は core/privacy.py）。

        ルート層に閾値判定が現れたら、それは宣言されていない集約が生まれた合図。
        """
        offenders = []
        for path in sorted((BACKEND / "api" / "routes").glob("*.py")):
            src = path.read_text(encoding="utf-8")
            if "bucket_count_range" in src or "meets_k_anonymity" in src:
                offenders.append(path.name)
        assert offenders == [], (
            f"ルート層が k-匿名ゲートを直接呼んでいます: {offenders}"
            "（集計は core/ 側に置き、カタログの consumer として宣言すること）"
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
        """全 label が読み手のいるマニュアルに逐語で現れること。

        宛先別に3面ある: 学習者の活動が入るものは student、教員が値を読むものは
        teacher、システム管理者だけのものは system_admin。どれか1つに現れればよい
        （学習者向けの必須集合は次のテストが別に固定する）。
        """
        blobs = [
            _STUDENT_MANUAL.read_text(encoding="utf-8"),
            _SYSADMIN_MANUAL.read_text(encoding="utf-8"),
            _TEACHER_MANUAL.read_text(encoding="utf-8"),
        ]
        missing = sorted(
            spec.label for spec in ic.all_indicators()
            if not any(spec.label in blob for blob in blobs)
        )
        assert missing == [], (
            f"マニュアルに逐語で現れない計器名: {missing}"
            "（student/01-specification.md / teacher/21-admin-interest-dashboard.md /"
            " system_admin/13-admin-llm-usage.md のいずれかに書く）"
        )

    def test_teacher_readable_indicators_are_listed_for_teachers(self):
        """教員が**値**を読める計器は、教員向けマニュアルに名前で載せる。"""
        teacher = _TEACHER_MANUAL.read_text(encoding="utf-8")
        missing = sorted(
            spec.label for spec in ic.all_indicators()
            if spec.values_audience == ic.AUDIENCE_TEACHER and spec.label not in teacher
        )
        assert missing == [], f"教員向けマニュアルに無い計器名: {missing}"

    def test_learner_facing_indicators_are_listed_for_learners(self):
        """学習者の活動が入りうる計器は、学生向けマニュアルに名前で載せる。"""
        student = _STUDENT_MANUAL.read_text(encoding="utf-8")
        learner_facing = (
            "interest-dashboard", "bridge-insights", "anchor-insights",
            "naive-signals", "frontier-interest", "reconstruction-review-queue",
            "claims-stumble-summary", "help-gaps-pending",
            "discuss-observation-status", "llm-usage-metrics", "my-records",
            # 集約ではない個票。学習者の氏名と質問の原文が教員に見えるため、
            # 学生向けマニュアルへの明記は他のどれよりも必要（IG1 の趣旨）。
            "unanswered-queries",
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
