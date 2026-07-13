"""LLM 使用量推計（U層 Task D）API のガードレール + ハンドラ単体テスト。

DB・TestClient 不要の流儀（``test_image_library_guardrails.py`` の route 走査パターンを
踏襲）。外部サービス（PostgreSQL / OpenAI）への実接続は行わない。

観点:
  1. ``routes/llm_usage.py`` の全ルートが GET のみ（DELETE/POST/PATCH 不在, U6）
  2. ``/metrics`` は ``_require_system_admin``、``/estimate/...`` は ``_require_teacher`` に依存
  3. estimate ハンドラのレスポンス形状（レンジのみ・cost/usd/点推定フィールド不在, U5）
  4. metrics: 不正 ``group_by`` → HTTPException 400（``collect_metrics`` の ValueError 変換）
  5. ``core.llm_usage.document_estimate.estimate_document_run`` の決定性・
     analyze_images オプションによるステージ増減・上限キャップの note
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SRC = ROOT / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


def _collect_forbidden_keys(obj, path: str = "") -> list[str]:
    """レスポンスを再帰的に走査し、cost/usd/点推定を匂わせるキー名を収集する（U5）。"""
    offending: list[str] = []
    forbidden_substrings = ("cost", "usd", "price")
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_lower = str(key).lower()
            if any(sub in key_lower for sub in forbidden_substrings):
                offending.append(f"{path}.{key}" if path else str(key))
            # "tokens" 単体（点推定）は禁止だが "tokens_range" は許可
            if key_lower == "tokens" or key_lower.endswith("_tokens"):
                offending.append(f"{path}.{key}" if path else str(key))
            offending.extend(_collect_forbidden_keys(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            offending.extend(_collect_forbidden_keys(item, f"{path}[{i}]"))
    return offending


# ===========================================================================
# 1. ルート走査（メソッド・依存関数）
# ===========================================================================


@_skip_no_fastapi
class TestRouteRegistration:
    def test_all_routes_are_get_only(self):
        import routes.llm_usage as llm_usage_routes

        for route in llm_usage_routes.router.routes:
            methods = getattr(route, "methods", set()) or set()
            assert methods == {"GET"}, f"{route.path} has non-GET methods: {methods}"

    def test_no_delete_route_in_source(self):
        src = (BACKEND / "api" / "routes" / "llm_usage.py").read_text(encoding="utf-8")
        assert "@router.delete" not in src
        assert "@router.post" not in src
        assert "@router.patch" not in src
        assert "@router.put" not in src

    def test_metrics_route_requires_system_admin(self):
        import routes.llm_usage as llm_usage_routes

        route = next(r for r in llm_usage_routes.router.routes if r.path.endswith("/metrics"))
        dependant = route.dependant
        dep_names = {dep.call.__name__ for dep in dependant.dependencies if dep.call}
        assert "_require_system_admin" in dep_names
        assert "_require_teacher" not in dep_names

    def test_estimate_route_requires_teacher(self):
        import routes.llm_usage as llm_usage_routes

        route = next(r for r in llm_usage_routes.router.routes if "/estimate/" in r.path)
        dependant = route.dependant
        dep_names = {dep.call.__name__ for dep in dependant.dependencies if dep.call}
        assert "_require_teacher" in dep_names
        assert "_require_system_admin" not in dep_names


# ===========================================================================
# 2. /metrics ハンドラ（monkeypatch collect_metrics / get_session）
# ===========================================================================


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@_skip_no_fastapi
class TestMetricsHandler:
    def test_metrics_happy_path_delegates_to_collect_metrics(self, monkeypatch):
        import routes.llm_usage as llm_usage_routes

        fake_session = _FakeSession()
        captured = {}

        def fake_get_session():
            return fake_session

        def fake_collect_metrics(session, *, date_from, date_to, group_by):
            captured["session"] = session
            captured["date_from"] = date_from
            captured["date_to"] = date_to
            captured["group_by"] = group_by
            return {
                "from": date_from,
                "to": date_to,
                "rows": [],
                "dropped_events": 0,
                "price_table_loaded": False,
            }

        monkeypatch.setattr(llm_usage_routes, "get_session", fake_get_session)
        monkeypatch.setattr(llm_usage_routes, "collect_metrics", fake_collect_metrics)

        result = llm_usage_routes.get_llm_usage_metrics(
            date_from="2026-07-01",
            date_to="2026-07-11",
            group_by="feature,model",
            current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
        )

        assert result["from"] == "2026-07-01"
        assert result["to"] == "2026-07-11"
        assert captured["group_by"] == ["feature", "model"]
        assert fake_session.closed is True

    def test_metrics_defaults_group_by_to_feature(self, monkeypatch):
        import routes.llm_usage as llm_usage_routes

        captured = {}

        def fake_get_session():
            return _FakeSession()

        def fake_collect_metrics(session, *, date_from, date_to, group_by):
            captured["group_by"] = group_by
            return {"from": date_from, "to": date_to, "rows": [], "dropped_events": 0,
                     "price_table_loaded": False}

        monkeypatch.setattr(llm_usage_routes, "get_session", fake_get_session)
        monkeypatch.setattr(llm_usage_routes, "collect_metrics", fake_collect_metrics)

        llm_usage_routes.get_llm_usage_metrics(
            date_from=None, date_to=None, group_by="feature",
            current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
        )
        assert captured["group_by"] == ["feature"]

    def test_metrics_defaults_date_range_when_unspecified(self, monkeypatch):
        import routes.llm_usage as llm_usage_routes

        captured = {}

        def fake_get_session():
            return _FakeSession()

        def fake_collect_metrics(session, *, date_from, date_to, group_by):
            captured["date_from"] = date_from
            captured["date_to"] = date_to
            return {"from": date_from, "to": date_to, "rows": [], "dropped_events": 0,
                     "price_table_loaded": False}

        monkeypatch.setattr(llm_usage_routes, "get_session", fake_get_session)
        monkeypatch.setattr(llm_usage_routes, "collect_metrics", fake_collect_metrics)

        llm_usage_routes.get_llm_usage_metrics(
            date_from=None, date_to=None, group_by="feature",
            current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
        )
        # 捏造しない: 未指定時も実際に使った値をそのまま返す（None のままにしない）
        assert captured["date_from"] is not None
        assert captured["date_to"] is not None

    def test_invalid_group_by_becomes_http_400(self, monkeypatch):
        import routes.llm_usage as llm_usage_routes
        from fastapi import HTTPException

        def fake_get_session():
            return _FakeSession()

        def fake_collect_metrics(session, *, date_from, date_to, group_by):
            raise ValueError(f"invalid group_by fields: {group_by!r}")

        monkeypatch.setattr(llm_usage_routes, "get_session", fake_get_session)
        monkeypatch.setattr(llm_usage_routes, "collect_metrics", fake_collect_metrics)

        with pytest.raises(HTTPException) as exc_info:
            llm_usage_routes.get_llm_usage_metrics(
                date_from="2026-07-01", date_to="2026-07-11", group_by="bogus_field",
                current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
            )
        assert exc_info.value.status_code == 400

    def test_metrics_closes_session_even_on_value_error(self, monkeypatch):
        import routes.llm_usage as llm_usage_routes
        from fastapi import HTTPException

        fake_session = _FakeSession()

        def fake_get_session():
            return fake_session

        def fake_collect_metrics(session, *, date_from, date_to, group_by):
            raise ValueError("bad group_by")

        monkeypatch.setattr(llm_usage_routes, "get_session", fake_get_session)
        monkeypatch.setattr(llm_usage_routes, "collect_metrics", fake_collect_metrics)

        with pytest.raises(HTTPException):
            llm_usage_routes.get_llm_usage_metrics(
                date_from="2026-07-01", date_to="2026-07-11", group_by="bogus",
                current_user={"id": "admin-1", "role": "SYSTEM_ADMIN"},
            )
        assert fake_session.closed is True


# ===========================================================================
# 3. /estimate/documents/{document_id} ハンドラ
# ===========================================================================


@_skip_no_fastapi
class TestEstimateHandler:
    def test_estimate_happy_path_shape_has_no_point_estimate_or_cost(self, monkeypatch):
        import routes.llm_usage as llm_usage_routes

        def fake_ensure_viewable(document_id, current_user):
            return [{"document_id": "canonical-doc-1", "material_id": "mat-1"}]

        def fake_get_session():
            return _FakeSession()

        def fake_estimate_document_run(session, document_id, *, analyze_images=False):
            assert document_id == "canonical-doc-1"  # canonical id を使う
            return {
                "document_id": document_id,
                "total_tokens_range": [1000, 2000],
                "stages": [
                    {"stage": "paper_skeleton", "tokens_range": [500, 900]},
                    {"stage": "claim_qualification", "tokens_range": [500, 1100]},
                ],
                "usage_source": "estimated_heuristic",
            }

        monkeypatch.setattr(llm_usage_routes, "_ensure_document_viewable", fake_ensure_viewable)
        monkeypatch.setattr(llm_usage_routes, "get_session", fake_get_session)
        monkeypatch.setattr(llm_usage_routes, "estimate_document_run", fake_estimate_document_run)

        result = llm_usage_routes.get_document_usage_estimate(
            document_id="mat-1", analyze_images=False,
            current_user={"id": "teacher-1", "role": "TEACHER"},
        )

        low, high = result["total_tokens_range"]
        assert isinstance(low, int) and isinstance(high, int)
        assert low <= high
        for stage in result["stages"]:
            s_low, s_high = stage["tokens_range"]
            assert s_low <= s_high

        offending = _collect_forbidden_keys(result)
        assert offending == [], f"forbidden point-estimate/cost keys found: {offending}"

    def test_estimate_document_not_found_is_404(self, monkeypatch):
        import routes.llm_usage as llm_usage_routes
        from fastapi import HTTPException

        monkeypatch.setattr(
            llm_usage_routes, "_ensure_document_viewable",
            lambda document_id, current_user: [{"document_id": "doc-x"}],
        )
        monkeypatch.setattr(llm_usage_routes, "get_session", lambda: _FakeSession())
        monkeypatch.setattr(
            llm_usage_routes, "estimate_document_run",
            lambda session, document_id, *, analyze_images=False: None,
        )

        with pytest.raises(HTTPException) as exc_info:
            llm_usage_routes.get_document_usage_estimate(
                document_id="doc-x", analyze_images=False,
                current_user={"id": "teacher-1", "role": "TEACHER"},
            )
        assert exc_info.value.status_code == 404

    def test_estimate_propagates_viewable_gate_404(self, monkeypatch):
        """権限ゲート自体が 404 を投げた場合、そのまま伝播する（fail-closed）。"""
        import routes.llm_usage as llm_usage_routes
        from fastapi import HTTPException

        def fake_ensure_viewable(document_id, current_user):
            raise HTTPException(status_code=404, detail="Document not found")

        monkeypatch.setattr(llm_usage_routes, "_ensure_document_viewable", fake_ensure_viewable)

        with pytest.raises(HTTPException) as exc_info:
            llm_usage_routes.get_document_usage_estimate(
                document_id="doc-nope", analyze_images=False,
                current_user={"id": "teacher-1", "role": "TEACHER"},
            )
        assert exc_info.value.status_code == 404

    def test_estimate_closes_session(self, monkeypatch):
        import routes.llm_usage as llm_usage_routes

        fake_session = _FakeSession()
        monkeypatch.setattr(
            llm_usage_routes, "_ensure_document_viewable",
            lambda document_id, current_user: [{"document_id": "doc-1"}],
        )
        monkeypatch.setattr(llm_usage_routes, "get_session", lambda: fake_session)
        monkeypatch.setattr(
            llm_usage_routes, "estimate_document_run",
            lambda session, document_id, *, analyze_images=False: {
                "document_id": document_id, "total_tokens_range": [0, 0],
                "stages": [], "usage_source": "estimated_heuristic",
            },
        )

        llm_usage_routes.get_document_usage_estimate(
            document_id="doc-1", analyze_images=False,
            current_user={"id": "teacher-1", "role": "TEACHER"},
        )
        assert fake_session.closed is True


# ===========================================================================
# 4. core.llm_usage.document_estimate — 決定論的見積り
# ===========================================================================


class _FakeQueryResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeEstimateSession:
    """SQL テキストの内容でどのテーブル向けクエリかを判別して固定行を返す fake session。"""

    def __init__(self, chunk_row, figure_row):
        self._chunk_row = chunk_row
        self._figure_row = figure_row
        self.queries: list[str] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.queries.append(sql)
        if "FROM chunks" in sql:
            return _FakeQueryResult(self._chunk_row)
        if "FROM document_figures" in sql:
            return _FakeQueryResult(self._figure_row)
        raise AssertionError(f"unexpected query in estimate_document_run: {sql}")


class TestDocumentEstimateDeterminism:
    def _make_session(self, chunk_count=10, total_chars=50_000, figure_count=8):
        return _FakeEstimateSession(
            chunk_row=(chunk_count, total_chars), figure_row=(figure_count,)
        )

    def test_document_not_found_returns_none(self):
        from core.llm_usage import document_estimate

        session = self._make_session(chunk_count=0, total_chars=0, figure_count=0)
        result = document_estimate.estimate_document_run(session, "doc-missing")
        assert result is None

    def test_same_input_produces_same_output(self):
        """乱数・時刻非依存: 同一入力には常に同一出力（テスト可能性）。"""
        from core.llm_usage import document_estimate

        session_a = self._make_session()
        session_b = self._make_session()

        result_a = document_estimate.estimate_document_run(session_a, "doc-1", analyze_images=True)
        result_b = document_estimate.estimate_document_run(session_b, "doc-1", analyze_images=True)

        assert result_a == result_b

    def test_analyze_images_false_has_no_apparatus_stage(self):
        from core.llm_usage import document_estimate

        session = self._make_session(figure_count=8)
        result = document_estimate.estimate_document_run(session, "doc-1", analyze_images=False)

        stage_names = [s["stage"] for s in result["stages"]]
        assert document_estimate.APPARATUS_STAGE_KEY not in stage_names
        assert result["usage_source"] == "estimated_heuristic"

    def test_analyze_images_true_adds_apparatus_stage(self):
        from core.llm_usage import document_estimate

        session = self._make_session(figure_count=8)
        result = document_estimate.estimate_document_run(session, "doc-1", analyze_images=True)

        stage_names = [s["stage"] for s in result["stages"]]
        assert document_estimate.APPARATUS_STAGE_KEY in stage_names

        apparatus_stage = next(
            s for s in result["stages"] if s["stage"] == document_estimate.APPARATUS_STAGE_KEY
        )
        assert "figures=8" in apparatus_stage["note"]
        assert "capped_at" not in apparatus_stage["note"]

    def test_analyze_images_true_caps_at_apparatus_max_images(self, monkeypatch):
        from core.llm_usage import document_estimate

        class _FakeSettings:
            apparatus_max_images_per_document = 20

        monkeypatch.setattr(document_estimate, "get_settings", lambda: _FakeSettings())

        session = self._make_session(figure_count=50)
        result = document_estimate.estimate_document_run(session, "doc-1", analyze_images=True)

        apparatus_stage = next(
            s for s in result["stages"] if s["stage"] == document_estimate.APPARATUS_STAGE_KEY
        )
        assert "figures=50" in apparatus_stage["note"]
        assert "capped_at=APPARATUS_MAX_IMAGES_PER_DOCUMENT" in apparatus_stage["note"]

    def test_all_stage_profiles_present_and_ranges_valid(self):
        from core.llm_usage import document_estimate

        session = self._make_session()
        result = document_estimate.estimate_document_run(session, "doc-1", analyze_images=False)

        stage_names = {s["stage"] for s in result["stages"]}
        assert stage_names == set(document_estimate.STAGE_INPUT_PROFILES.keys())

        total_low, total_high = result["total_tokens_range"]
        assert total_low <= total_high
        summed_low = sum(s["tokens_range"][0] for s in result["stages"])
        summed_high = sum(s["tokens_range"][1] for s in result["stages"])
        assert [total_low, total_high] == [summed_low, summed_high]

        for stage in result["stages"]:
            low, high = stage["tokens_range"]
            assert isinstance(low, int) and isinstance(high, int)
            assert low <= high
            assert low >= 0

    def test_larger_document_yields_larger_or_equal_token_range(self):
        """総文字数が増えるほど見積りトークン量が単調非減少であること（健全性チェック）。"""
        from core.llm_usage import document_estimate

        small = self._make_session(total_chars=1_000)
        big = self._make_session(total_chars=100_000)

        small_result = document_estimate.estimate_document_run(small, "doc-1")
        big_result = document_estimate.estimate_document_run(big, "doc-1")

        assert big_result["total_tokens_range"][1] > small_result["total_tokens_range"][1]

    def test_response_has_no_point_estimate_or_cost_fields(self):
        from core.llm_usage import document_estimate

        session = self._make_session()
        result = document_estimate.estimate_document_run(session, "doc-1", analyze_images=True)

        offending = _collect_forbidden_keys(result)
        assert offending == [], f"forbidden point-estimate/cost keys found: {offending}"
