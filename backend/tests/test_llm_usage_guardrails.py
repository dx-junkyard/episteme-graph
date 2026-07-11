"""LLM トークン使用量推計（U層, Usage Metering）ガードレールの自動テスト。

設計の正本は docs/features/llm_usage_metering_design.md §10「ガードレール」に列挙された
9 項目 + 追加1項目（migration DDL の二重管理整合）を構造的に守る（test_doubt_guardrails.py /
test_image_library_guardrails.py の流儀 — ソースコードを read_text / ast で読み込む構造的検査
+ 非DB・非LLM のユニットテストを踏襲する。実 DB・実 LLM・TestClient は使わない）。

1. recorder は例外を漏らさない（U2） — record() / flush_now() が壊れた session factory でも例外を出さない
2. core/llm_usage が FastAPI を import しない（U4・開発ルール2）。core/llm.py・core/tts.py も同様
3. 削除 API 不在（U6） — routes/llm_usage.py に DELETE/POST/PATCH/PUT ハンドラが無い
4. 権限 fail-closed — /metrics は _require_system_admin、/estimate は _require_teacher
5. reported / estimated の分離（U1） — metrics のレスポンス row は2バケット構造
6. estimate API はレンジのみ（U5） — cost_usd・点推定フィールドが無い
7. 価格ハードコード禁止（U7） — pricing.py に価格表の dict リテラルが無い
8. 学習者 API 非漏洩（U5） — routes/learning.py にトークン/使用量系語彙が出現しない
9. estimator の決定性 — 同一入力で常に同一出力・乱数や時刻に非依存
10. （追加）migration DDL の二重管理整合 — 043_llm_usage_events.sql と main.py の 043 ブロックが一致

外部サービス（PostgreSQL / OpenAI / Gemini）への実接続は行わない。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SRC = ROOT / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CORE_LLM_USAGE_DIR = BACKEND / "core" / "llm_usage"
_ROUTE_LLM_USAGE_SRC = (BACKEND / "api" / "routes" / "llm_usage.py").read_text(encoding="utf-8")
_DOCUMENT_ESTIMATE_SRC = (_CORE_LLM_USAGE_DIR / "document_estimate.py").read_text(encoding="utf-8")
_PRICING_SRC = (_CORE_LLM_USAGE_DIR / "pricing.py").read_text(encoding="utf-8")
_ESTIMATOR_SRC = (_CORE_LLM_USAGE_DIR / "estimator.py").read_text(encoding="utf-8")
_METRICS_SRC = (_CORE_LLM_USAGE_DIR / "metrics.py").read_text(encoding="utf-8")
_LLM_SRC = (BACKEND / "core" / "llm.py").read_text(encoding="utf-8")
_TTS_SRC = (BACKEND / "core" / "tts.py").read_text(encoding="utf-8")
_LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_MIGRATION_SQL_SRC = (BACKEND / "db" / "043_llm_usage_events.sql").read_text(encoding="utf-8")
_MAIN_SRC = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


# ===========================================================================
# 1. recorder は例外を漏らさない（U2）
# ===========================================================================


class TestRecorderNeverRaises:
    """core.llm_usage.recorder.record() / flush_now() は DB 接続が完全に壊れていても
    例外を外に漏らさない（U2: 計測失敗が LLM 呼び出し本体を壊してはならない）。"""

    @pytest.fixture(autouse=True)
    def _clean_recorder_state(self):
        import core.llm_usage.recorder as recorder

        recorder.reset_for_tests()
        yield
        recorder.reset_for_tests()

    def test_record_survives_session_factory_that_always_raises(self, monkeypatch):
        import core.llm_usage.recorder as recorder
        from core.llm_usage.schema import UsageEvent

        class _AlwaysRaisingFactory:
            def __call__(self):
                raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(recorder, "_session_factory", _AlwaysRaisingFactory())

        event = UsageEvent(
            provider="openai",
            model="gpt-4o",
            operation="chat",
            feature="unattributed",
            usage_source="reported",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )

        # どちらも例外を投げてはならない（例外が伝播すれば呼び出し元の LLM フローを壊す）。
        recorder.record(event)
        result = recorder.flush_now()
        assert result == 0  # 書けなかったことは正直に 0 として返す（U8 の裏側）

        recorder.reset_for_tests()

    def test_record_survives_completely_broken_event(self, monkeypatch):
        """metadata に JSON化できないオブジェクトを積んでも record() 自体は落ちない。"""
        import core.llm_usage.recorder as recorder
        from core.llm_usage.schema import UsageEvent

        class _Unserializable:
            pass

        class _AlwaysRaisingFactory:
            def __call__(self):
                raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(recorder, "_session_factory", _AlwaysRaisingFactory())

        event = UsageEvent(
            provider="openai",
            model="gpt-4o",
            operation="chat",
            feature="unattributed",
            usage_source="estimated_heuristic",
            metadata={"bad": _Unserializable()},
        )
        recorder.record(event)
        recorder.flush_now()


# ===========================================================================
# 2. core/llm_usage が FastAPI を import しない（U4・開発ルール2）
# ===========================================================================


class TestNoFastAPIImportInCore:
    def test_no_fastapi_import_in_core_llm_usage(self):
        for path in _CORE_LLM_USAGE_DIR.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "import fastapi" not in src, f"{path} imports fastapi"
            assert "from fastapi" not in src, f"{path} imports fastapi"

    def test_no_fastapi_import_in_llm_py(self):
        assert "import fastapi" not in _LLM_SRC
        assert "from fastapi" not in _LLM_SRC

    def test_no_fastapi_import_in_tts_py(self):
        assert "import fastapi" not in _TTS_SRC
        assert "from fastapi" not in _TTS_SRC


# ===========================================================================
# 3. 削除 API 不在（U6）
# ===========================================================================


class TestNoDeleteOrMutatingAPI:
    def test_no_mutating_route_decorators_in_source(self):
        assert "@router.delete" not in _ROUTE_LLM_USAGE_SRC
        assert "@router.post" not in _ROUTE_LLM_USAGE_SRC
        assert "@router.patch" not in _ROUTE_LLM_USAGE_SRC
        assert "@router.put" not in _ROUTE_LLM_USAGE_SRC

    @_skip_no_fastapi
    def test_all_registered_routes_are_get_only(self):
        import routes.llm_usage as llm_usage_routes

        for route in llm_usage_routes.router.routes:
            methods = getattr(route, "methods", set()) or set()
            assert methods == {"GET"}, f"{route.path} has non-GET methods: {methods}"


# ===========================================================================
# 4. 権限 fail-closed
# ===========================================================================


@_skip_no_fastapi
class TestPermissionFailClosed:
    def test_metrics_route_requires_system_admin(self):
        import routes.llm_usage as llm_usage_routes

        route = next(r for r in llm_usage_routes.router.routes if r.path.endswith("/metrics"))
        dependant = route.dependant
        dep_names = {dep.call.__name__ for dep in dependant.dependencies if dep.call}
        assert "_require_system_admin" in dep_names, (
            f"/metrics route missing _require_system_admin dependency: {dep_names}"
        )

    def test_estimate_route_requires_teacher(self):
        import routes.llm_usage as llm_usage_routes

        route = next(r for r in llm_usage_routes.router.routes if "/estimate/" in r.path)
        dependant = route.dependant
        dep_names = {dep.call.__name__ for dep in dependant.dependencies if dep.call}
        assert "_require_teacher" in dep_names, (
            f"/estimate route missing _require_teacher dependency: {dep_names}"
        )


# ===========================================================================
# 5. reported / estimated の分離（U1）
# ===========================================================================


class _FakeMetricsQueryResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeMetricsSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt, params=None):
        return _FakeMetricsQueryResult(self._rows)


class TestReportedEstimatedSeparation:
    def test_metrics_source_builds_two_separate_buckets(self):
        """metrics.py のソース検査: aggregated entry が reported/estimated の
        2つの独立した dict として構築されていること（合算バケットが無いこと）。"""
        assert '"reported": dict(_EMPTY_BUCKET)' in _METRICS_SRC
        assert '"estimated": dict(_EMPTY_BUCKET)' in _METRICS_SRC

    def test_collect_metrics_row_has_separate_reported_and_estimated_dicts(self, monkeypatch):
        import core.llm_usage.metrics as metrics

        monkeypatch.setattr(metrics.pricing, "load_price_table", lambda: None)
        monkeypatch.setattr(metrics, "dropped_count", lambda: 0)

        rows = [
            ("feat-a", "gpt-4o", "reported", 1000, 200, 1200, 100, 5),
            ("feat-a", "gpt-4o", "estimated_heuristic", 50, 10, 60, 0, 1),
        ]
        session = _FakeMetricsSession(rows)
        result = metrics.collect_metrics(
            session, date_from="2026-07-01", date_to="2026-07-12", group_by=["feature"]
        )

        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert isinstance(row["reported"], dict)
        assert isinstance(row["estimated"], dict)
        assert row["reported"] is not row["estimated"]
        assert row["reported"]["prompt_tokens"] == 1000
        assert row["estimated"]["prompt_tokens"] == 50

        # 合算した単一のトークン数フィールドが row 直下に存在しない
        assert "prompt_tokens" not in row
        assert "completion_tokens" not in row
        assert "total_tokens" not in row
        assert set(row.keys()) == {"key", "reported", "estimated", "cost_usd"}


# ===========================================================================
# 6. estimate API はレンジのみ（U5）
# ===========================================================================


def _cost_or_usd_key_construction_patterns_found(src: str) -> list[str]:
    """docstring 上の言及ではなく、実際のキー構築・代入・属性アクセス文脈のみを検出する。"""
    patterns = [
        r'["\']cost_usd["\']\s*:',
        r'\bcost_usd\s*=',
        r'\.cost_usd\b',
        r'["\']usd["\']',
        r'["\']cost["\']\s*:',
    ]
    found = []
    for pat in patterns:
        if re.search(pat, src):
            found.append(pat)
    return found


class _FakeEstimateQueryResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeEstimateSession:
    def __init__(self, chunk_row, figure_row):
        self._chunk_row = chunk_row
        self._figure_row = figure_row

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM chunks" in sql:
            return _FakeEstimateQueryResult(self._chunk_row)
        if "FROM document_figures" in sql:
            return _FakeEstimateQueryResult(self._figure_row)
        raise AssertionError(f"unexpected query: {sql}")


def _find_point_estimate_or_cost_keys(obj, path: str = "") -> list[str]:
    """レスポンスを再帰的に走査し、点推定トークン数・cost/usd を匂わせるキーを収集する。"""
    offending: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = str(key).lower()
            cur_path = f"{path}.{key}" if path else str(key)
            if "cost" in key_lower or "usd" in key_lower:
                offending.append(cur_path)
            if key_lower == "tokens" or key_lower.endswith("_tokens"):
                offending.append(cur_path)
            offending.extend(_find_point_estimate_or_cost_keys(value, cur_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            offending.extend(_find_point_estimate_or_cost_keys(item, f"{path}[{i}]"))
    return offending


def _check_range_shapes(obj, path: str = "") -> list[str]:
    """``*_range`` で終わるキーの値が [low, high] の int ペア（low<=high）であることを検査する。"""
    problems: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = str(key).lower()
            cur_path = f"{path}.{key}" if path else str(key)
            if key_lower.endswith("_range"):
                valid = (
                    isinstance(value, list)
                    and len(value) == 2
                    and all(isinstance(x, int) and not isinstance(x, bool) for x in value)
                    and value[0] <= value[1]
                )
                if not valid:
                    problems.append(f"{cur_path} is not a valid [low, high] int range: {value!r}")
            problems.extend(_check_range_shapes(value, cur_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            problems.extend(_check_range_shapes(item, f"{path}[{i}]"))
    return problems


class TestEstimateAPIRangeOnly:
    def test_no_cost_or_usd_key_construction_in_routes_source(self):
        found = _cost_or_usd_key_construction_patterns_found(_ROUTE_LLM_USAGE_SRC)
        assert found == [], f"routes/llm_usage.py has cost/usd key-construction patterns: {found}"

    def test_no_cost_or_usd_key_construction_in_document_estimate_source(self):
        found = _cost_or_usd_key_construction_patterns_found(_DOCUMENT_ESTIMATE_SRC)
        assert found == [], (
            f"core/llm_usage/document_estimate.py has cost/usd key-construction patterns: {found}"
        )

    def test_estimate_document_run_response_has_no_point_estimate_or_cost(self):
        from core.llm_usage import document_estimate

        session = _FakeEstimateSession(chunk_row=(10, 50_000), figure_row=(8,))
        result = document_estimate.estimate_document_run(session, "doc-1", analyze_images=True)

        offending = _find_point_estimate_or_cost_keys(result)
        assert offending == [], f"forbidden point-estimate/cost keys found: {offending}"

        problems = _check_range_shapes(result)
        assert problems == [], f"invalid range shapes found: {problems}"

        # トップレベルの total_tokens_range 自体もレンジ形式であることの直接確認
        low, high = result["total_tokens_range"]
        assert isinstance(low, int) and isinstance(high, int)
        assert low <= high


# ===========================================================================
# 7. 価格ハードコード禁止（U7）
# ===========================================================================


# estimator.py / document_estimate.py の係数定数は価格ではないため明示的に許可リストへ入れる
# （設計書 §4.2/§4.3、docs/features/llm_usage_metering_design.md 参照）。
_ALLOWED_NON_PRICE_MODULE_LEVEL_NAMES = {
    "CJK_TOKENS_PER_CHAR",
    "OTHER_CHARS_PER_TOKEN",
    "PER_MESSAGE_OVERHEAD",
    "PRIMING_TOKENS",
    "FLAT_IMAGE_TOKENS",
    "ESTIMATE_MARGIN",
    "STAGE_INPUT_PROFILES",
    "APPARATUS_TEXT_OVERHEAD_TOKENS_PER_IMAGE",
    "USAGE_SOURCE",
    "APPARATUS_STAGE_KEY",
    # 語彙の正本（schema.py）。価格ではない。
    "OPERATIONS",
    "USAGE_SOURCES",
    "UNATTRIBUTED",
    "KNOWN_FEATURES",
    # metrics.py の非価格な内部構造（group_by ホワイトリスト・ゼロ初期化テンプレート）
    "_GROUP_BY_SQL",
    "_EMPTY_BUCKET",
}


def _module_level_dict_assignments(src: str):
    """module top-level の Assign/AnnAssign のうち、値が dict リテラルのものを列挙する。"""
    tree = ast.parse(src)
    results = []
    for node in tree.body:
        value = None
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        if isinstance(value, ast.Dict):
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            results.append((names, value, node.lineno))
    return results


class TestNoHardcodedPricing:
    def test_pricing_module_has_no_module_level_dict_literal(self):
        """pricing.py は価格表を LLM_PRICE_TABLE_PATH の JSON からのみ読む
        （モジュールレベルの dict リテラルとして価格表を持ってはならない, U7）。"""
        for names, _value, lineno in _module_level_dict_assignments(_PRICING_SRC):
            pytest.fail(
                f"pricing.py has a module-level dict literal {names} at line {lineno}; "
                "price tables must come only from LLM_PRICE_TABLE_PATH JSON (U7)"
            )

    def test_no_module_level_price_dict_outside_allowlist(self):
        """core/llm_usage/*.py と core/llm.py の全モジュールレベル dict リテラルのうち、
        許可リスト外かつ『モデル名 -> {単価キー: 数値}』形の二重ネスト構造（価格表の形）が
        無いことを確認する。"""
        paths = list(_CORE_LLM_USAGE_DIR.glob("*.py")) + [BACKEND / "core" / "llm.py"]
        for path in paths:
            src = path.read_text(encoding="utf-8")
            for names, value, lineno in _module_level_dict_assignments(src):
                if any(n in _ALLOWED_NON_PRICE_MODULE_LEVEL_NAMES for n in names):
                    continue
                for inner in value.values:
                    if isinstance(inner, ast.Dict):
                        for leaf in inner.values:
                            if isinstance(leaf, ast.Constant) and isinstance(
                                leaf.value, (int, float)
                            ) and not isinstance(leaf.value, bool):
                                pytest.fail(
                                    f"{path}: possible hardcoded price table at {names} "
                                    f"(line {lineno}) — nested numeric dict resembles a "
                                    "per-model USD rate table"
                                )

    def test_no_per_million_style_price_constant_naming(self):
        paths = list(_CORE_LLM_USAGE_DIR.glob("*.py")) + [BACKEND / "core" / "llm.py"]
        for path in paths:
            src = path.read_text(encoding="utf-8")
            assert "per_million" not in src.lower(), f"{path} has a per_million-style constant"

    def test_allowlisted_coefficients_are_present_and_documented(self):
        """許可リストの係数定数が実際に estimator.py / document_estimate.py に存在すること
        （許可リストが空振りでないことの確認）。"""
        for const in (
            "CJK_TOKENS_PER_CHAR",
            "OTHER_CHARS_PER_TOKEN",
            "FLAT_IMAGE_TOKENS",
            "ESTIMATE_MARGIN",
            "PER_MESSAGE_OVERHEAD",
            "PRIMING_TOKENS",
        ):
            assert const in _ESTIMATOR_SRC, f"{const} missing from estimator.py"
        assert "STAGE_INPUT_PROFILES" in _DOCUMENT_ESTIMATE_SRC

    def test_pricing_only_reads_from_configured_json_path(self):
        assert "settings.llm_price_table_path" in _PRICING_SRC
        assert "json.load" in _PRICING_SRC


# ===========================================================================
# 8. 学習者 API 非漏洩（U5）
# ===========================================================================


class TestLearnerAPINoUsageLeak:
    _FORBIDDEN_VOCAB = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "usage_source",
        "cost_usd",
        "collect_metrics",
    )

    def test_forbidden_usage_vocabulary_absent_from_learning_routes(self):
        offending = [w for w in self._FORBIDDEN_VOCAB if w in _LEARNING_SRC]
        assert offending == [], (
            f"routes/learning.py contains forbidden usage-metering vocabulary: {offending}"
        )

    def test_usage_context_attribution_wiring_is_not_accidentally_excluded(self):
        """帰属配線（usage_context）自体は許可された語彙であり、検査対象から除外されている
        ことを確認する（誤って attribution 配線ごと削らせない安全弁）。"""
        assert "usage_context" in _LEARNING_SRC
        assert "from core.llm_usage.context import usage_context" in _LEARNING_SRC
        # 上記の帰属配線用語彙は禁止語彙リストに含まれていないことの確認
        assert "usage_context" not in TestLearnerAPINoUsageLeak._FORBIDDEN_VOCAB


# ===========================================================================
# 9. estimator の決定性
# ===========================================================================


class TestEstimatorDeterminism:
    def test_estimate_text_tokens_is_deterministic(self):
        from core.llm_usage import estimator

        text = "これはテストです。This is a test with mixed 日本語 and English content."
        first = estimator.estimate_text_tokens(text, model="gpt-4o")
        second = estimator.estimate_text_tokens(text, model="gpt-4o")
        assert first == second

    def test_estimate_messages_tokens_is_deterministic(self):
        from core.llm_usage import estimator

        messages = [
            {"role": "user", "content": "こんにちは、質問があります。"},
            {"role": "assistant", "content": "はい、どうぞ。"},
        ]
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        first = estimator.estimate_messages_tokens(messages, model="gpt-4o", schema=schema)
        second = estimator.estimate_messages_tokens(messages, model="gpt-4o", schema=schema)
        assert first == second

    def test_estimate_vision_tokens_is_deterministic(self):
        from core.llm_usage import estimator

        dims = [(1024, 768), None, (2048, 2048)]
        first = estimator.estimate_vision_tokens(dims)
        second = estimator.estimate_vision_tokens(dims)
        assert first == second

    def test_estimator_source_has_no_random_or_wallclock_dependency(self):
        assert "import random" not in _ESTIMATOR_SRC
        assert "from random" not in _ESTIMATOR_SRC
        assert "datetime.now" not in _ESTIMATOR_SRC
        assert "time.time" not in _ESTIMATOR_SRC
        assert "time.monotonic" not in _ESTIMATOR_SRC
        assert "import time" not in _ESTIMATOR_SRC
        assert "import datetime" not in _ESTIMATOR_SRC


# ===========================================================================
# 10.（追加）migration DDL の二重管理整合
# ===========================================================================


def _normalize_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _extract_balanced_parens_block(text: str, start_pattern: str) -> str:
    """``start_pattern`` にマッチする箇所から、開き丸括弧に対応する閉じ丸括弧までを返す。"""
    match = re.search(start_pattern, text)
    assert match, f"pattern not found: {start_pattern}"
    paren_start = text.index("(", match.start())
    depth = 0
    for i in range(paren_start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[match.start(): i + 1]
    raise AssertionError(f"unbalanced parentheses for pattern: {start_pattern}")


def _extract_main_py_llm_usage_sql_texts() -> list[str]:
    """main.py の migration 043 ブロック内の sa_text(...) 呼び出しから
    llm_usage_events / llm_usage_daily に言及する SQL 文字列を AST 経由で抽出する。

    Python の隣接文字列リテラル連結は ast.parse の時点で単一の Constant に畳み込まれるため、
    複数行にわたる文字列連結表記も正しく1本の文字列として取得できる。
    """
    tree = ast.parse(_MAIN_SRC)
    texts: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != "sa_text" or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if "llm_usage_events" in arg.value or "llm_usage_daily" in arg.value:
                texts.append(arg.value)
    return texts


class TestMigrationDualManagementConsistency:
    """backend/db/043_llm_usage_events.sql と backend/api/main.py の migration 043 ブロックは
    独立に保守されるため、内容が乖離していないことを構造的に検査する
    （test_doubt_guardrails.py の migration 検査の流儀を踏襲。完全一致までは要求しない現実的な比較）。
    """

    def test_main_py_contains_llm_usage_migration_block(self):
        assert "Migration 043" in _MAIN_SRC
        assert "llm_usage_events" in _MAIN_SRC

    def test_create_table_statement_matches_after_whitespace_normalization(self):
        sql_table = _extract_balanced_parens_block(
            _MIGRATION_SQL_SRC, r"CREATE TABLE IF NOT EXISTS llm_usage_events \("
        )
        main_texts = _extract_main_py_llm_usage_sql_texts()
        main_combined = "\n".join(main_texts)
        main_table = _extract_balanced_parens_block(
            main_combined, r"CREATE TABLE IF NOT EXISTS llm_usage_events \("
        )
        assert _normalize_whitespace(sql_table) == _normalize_whitespace(main_table), (
            "CREATE TABLE llm_usage_events differs between "
            "db/043_llm_usage_events.sql and api/main.py's migration 043 block"
        )

    def test_all_four_index_names_present_in_both(self):
        index_names = (
            "idx_llm_usage_events_occurred",
            "idx_llm_usage_events_feature",
            "idx_llm_usage_events_model",
            "idx_llm_usage_events_document",
        )
        main_combined = "\n".join(_extract_main_py_llm_usage_sql_texts())
        for idx_name in index_names:
            assert idx_name in _MIGRATION_SQL_SRC, f"{idx_name} missing from 043_llm_usage_events.sql"
            assert idx_name in main_combined, f"{idx_name} missing from main.py migration 043 block"

    def test_llm_usage_daily_view_present_and_matches_in_both(self):
        assert "CREATE OR REPLACE VIEW llm_usage_daily AS" in _MIGRATION_SQL_SRC
        main_combined = "\n".join(_extract_main_py_llm_usage_sql_texts())
        assert "CREATE OR REPLACE VIEW llm_usage_daily AS" in main_combined

        sql_view_match = re.search(
            r"CREATE OR REPLACE VIEW llm_usage_daily AS[\s\S]*?(?:;|\Z)", _MIGRATION_SQL_SRC
        )
        main_view_match = re.search(
            r"CREATE OR REPLACE VIEW llm_usage_daily AS[\s\S]*", main_combined
        )
        assert sql_view_match and main_view_match
        sql_view = _normalize_whitespace(sql_view_match.group(0).rstrip(";"))
        main_view = _normalize_whitespace(main_view_match.group(0))
        assert sql_view == main_view, (
            "llm_usage_daily view definition differs between "
            "db/043_llm_usage_events.sql and api/main.py's migration 043 block"
        )

    def test_check_constraint_vocabulary_matches_in_both(self):
        """operation IN (...) / usage_source IN (...) の許可値集合が両方で一致すること。"""
        main_combined = "\n".join(_extract_main_py_llm_usage_sql_texts())

        def _vocab(text: str, column: str) -> set[str]:
            match = re.search(rf"{column} TEXT NOT NULL(?: DEFAULT '[^']*')? CHECK \({column} IN\s*\(([^)]*)\)\)", text)
            assert match, f"{column} CHECK constraint not found"
            return {v.strip().strip("'") for v in match.group(1).split(",")}

        sql_operation_vocab = _vocab(_MIGRATION_SQL_SRC, "operation")
        main_operation_vocab = _vocab(main_combined, "operation")
        assert sql_operation_vocab == main_operation_vocab

        sql_usage_source_vocab = _vocab(_MIGRATION_SQL_SRC, "usage_source")
        main_usage_source_vocab = _vocab(main_combined, "usage_source")
        assert sql_usage_source_vocab == main_usage_source_vocab

        # 語彙の正本（schema.py）ともずれていないこと
        from core.llm_usage.schema import OPERATIONS, USAGE_SOURCES

        assert sql_operation_vocab == set(OPERATIONS)
        assert sql_usage_source_vocab == set(USAGE_SOURCES)


class TestOrmModelMatchesMigration:
    """core/models.py の LlmUsageEvent は migration 043 の DDL と独立に保守される
    3つ目の定義（SQL ファイル / main.py ブロック / ORM）。カラム集合が DDL と
    乖離していないことを検査する。
    """

    def test_orm_columns_match_sql_table_columns(self):
        from core.models import LlmUsageEvent

        table_block = _extract_balanced_parens_block(
            _MIGRATION_SQL_SRC, r"CREATE TABLE IF NOT EXISTS llm_usage_events \("
        )
        sql_columns = set()
        for line in table_block.splitlines():
            stripped = line.strip().rstrip(",")
            match = re.match(r'^"?([a-z_]+)"?\s+(UUID|TIMESTAMPTZ|TEXT|INTEGER|BIGINT|BOOLEAN|JSONB)\b', stripped)
            if match:
                sql_columns.add(match.group(1))

        # ORM 側は Column("metadata", ...) のように DB カラム名を別名で持てるため
        # 属性名でなく実カラム名で比較する
        orm_columns = {column.name for column in LlmUsageEvent.__table__.columns}

        assert sql_columns, "no columns parsed from migration SQL (parser broke?)"
        assert orm_columns == sql_columns, (
            "LlmUsageEvent ORM columns diverged from db/043_llm_usage_events.sql: "
            f"orm_only={sorted(orm_columns - sql_columns)}, "
            f"sql_only={sorted(sql_columns - orm_columns)}"
        )

    def test_orm_tablename(self):
        from core.models import LlmUsageEvent

        assert LlmUsageEvent.__tablename__ == "llm_usage_events"
