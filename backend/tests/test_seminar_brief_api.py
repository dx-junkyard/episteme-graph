"""ゼミ前ブリーフ（Seminar Brief, 提案1 v1）のテスト — 実 DB・実 LLM・TestClient 不使用。

正本: ``docs/features/seminar_brief_mirroring_design.md`` §1（SB1〜SB4）。
``test_my_records_api.py`` / ``test_discuss_observation.py`` と同じ流儀
（ソース静的検査 + fake セッション/monkeypatch による純関数実行）で以下を検証する:

- ルート存在（GET のみ・prefix="/api/admin" 登録・削除/書込メソッドなし）
- 権限2段ゲート（``_require_teacher`` + ``_ensure_document_viewable``）の配線
- document / course 対応が解決できないときの ``{available: false}`` 正直縮退
- 生数値キー（dependent_count / n_items / load_score / confidence）の非漏洩（再帰検査, SB2）
- 第4区画の空欄予約固定文（SB3）
- 晴れ間固定文が「このコーパスの中では」を含む肯定形であること（SL1）+ denylist 不在
- SB4: 学習者個人・学習者別件数のクエリ経路が無い（claim つまづきは stumble 集約の再利用のみ）
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

from core.doubt import seminar_brief as sb  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
)

_CORE_SRC = (BACKEND / "core" / "doubt" / "seminar_brief.py").read_text(encoding="utf-8")
_ROUTE_SRC = (BACKEND / "api" / "routes" / "seminar_brief.py").read_text(encoding="utf-8")
_MAIN_SRC = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
_OPEN_ASSUMPTIONS_SRC = (BACKEND / "core" / "doubt" / "open_assumptions.py").read_text(encoding="utf-8")

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)

# SB2: ブリーフに漏れてはならない生数値キー
_FORBIDDEN_NUMERIC_KEYS = ("dependent_count", "n_items", "load_score", "confidence")

# SL1: 閉世界 denylist（分野レベルの不在言明）
_SL1_DENYLIST = ("この分野では未検証", "誰も検証していない", "世界初", "未踏")


# ---------------------------------------------------------------------------
# fake セッション（SQL 文字列の特徴で分岐する最小実装）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """documents / theory_component_graphs / epistemic_ledger だけ答える fake。

    それ以外の SQL（target_label の theory_claims 参照等）は例外を投げる —
    呼び出し側の fail-soft（try/except で id へ縮退）が効くことも同時に確かめる。
    """

    def __init__(self, *, document_row=("doc-1",), course_rows=(("course-1",),),
                 ledger_rows=(("t-1", "claim"),)):
        self.document_row = document_row
        self.course_rows = course_rows
        self.ledger_rows = ledger_rows

    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM documents" in sql:
            return _FakeResult([self.document_row] if self.document_row else [])
        if "FROM theory_component_graphs" in sql:
            return _FakeResult(list(self.course_rows))
        if "FROM epistemic_ledger" in sql:
            return _FakeResult(list(self.ledger_rows))
        raise RuntimeError(f"unexpected SQL in fake session: {sql[:80]}")


def _assumption_item(**overrides) -> dict:
    """compile_open_assumptions の1件（生数値キーの混入も模擬）。"""
    item = {
        "target_id": "t-1",
        "target_type": "claim",
        "statement": "検出器の応答は線形である",
        "verification_status": "untested",
        "scope_coverage": "none",
        "scope_count_is_zero": True,
        "load_level": "high",
        "dependent_count": 12,          # 生数値（投影で落ちること）
        "load_score": 3.25,             # 生数値（投影で落ちること）
        "challenge_count_label": "少数",
        "challenge_types": ["definitional"],
        "has_verification_proposal": False,
        "has_naive_signal": True,
        "has_falsification_condition": False,
        "falsification_not_formulable": False,
        "reachability_summary": "reachable",
        "support_line_level": "single",
    }
    item.update(overrides)
    return item


def _patched_brief(monkeypatch, *, session=None, items=None) -> dict:
    """投影元を monkeypatch した build_seminar_brief の実行結果。"""
    session = session or _FakeSession()
    items = items if items is not None else [_assumption_item()]

    seen: dict = {}

    def fake_compile(sess, course_id, include_challenger_names=False, document_id=""):
        seen["course_id"] = course_id
        seen["include_challenger_names"] = include_challenger_names
        seen["document_id"] = document_id
        return list(items)

    monkeypatch.setattr(sb, "compile_open_assumptions", fake_compile)
    monkeypatch.setattr(
        sb, "get_stumble_summary",
        lambda document_id, claim_ids=None: {
            "document_id": document_id,
            "claims": [{
                "claim_id": "t-1",
                "claim_type": "measurement",
                "label": "検出器の応答は線形である",
                "n_items": 4,  # 生数値（転記されないこと）
                "axes": {
                    "error_rate": "高",
                    "symbol_descent": "3-5",
                    "verdict_self_check_divergence": "まだデータなし",
                    "faq": {"questions": "3-5", "has_data": True},
                },
                "has_data": True,
            }],
            "k_anonymity": 3,
        },
    )
    monkeypatch.setattr(
        sb, "build_support_context",
        lambda sess, *, course_id="", document_id="": object(),
    )
    monkeypatch.setattr(
        sb, "compute_support_lines_from_context",
        lambda ctx, ttype, tid: {
            "level": "single",
            "fact_line": "この対象は単一の支持線に立っています。『観測モデル』が同時に崩れると、観測からの支持が途切れます。",
            "cut_members": [{"node_id": "n-1", "label": "観測モデル"}],
            "observation_roots": [],
        },
    )
    brief = sb.build_seminar_brief(session, "doc-1")
    brief["_seen"] = seen
    return brief


def _walk(value):
    """dict/list を再帰的に平坦化して (key, value) を列挙する。"""
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


# ===========================================================================
# ルーター形状（GET のみ・登録・権限2段ゲート）
# ===========================================================================


class TestRouterShape:
    def test_single_get_endpoint_only(self):
        assert '@admin_router.get("/documents/{document_ref}/seminar-brief")' in _ROUTE_SRC
        assert _ROUTE_SRC.count("@admin_router.") == 1  # GET 1本のみ（読み時合成・書き込みなし）
        for verb in (".post(", ".put(", ".patch(", ".delete("):
            assert f"@admin_router{verb}" not in _ROUTE_SRC

    def test_main_registers_with_admin_prefix(self):
        assert "from routes import seminar_brief as seminar_brief_routes" in _MAIN_SRC
        assert (
            'app.include_router(seminar_brief_routes.admin_router, prefix="/api/admin")'
            in _MAIN_SRC
        )

    def test_two_stage_permission_gate_is_wired(self):
        """権限2段ゲート: TEACHER ロール + document 閲覧権（404 fail-closed）。"""
        assert "Depends(_require_teacher)" in _ROUTE_SRC
        assert "_ensure_document_viewable(document_ref, current_user)" in _ROUTE_SRC
        assert "from routes.theory_components import _ensure_document_viewable" in _ROUTE_SRC

    @_skip_no_fastapi
    def test_registered_route_is_get_only_and_teacher_gated(self):
        import routes.seminar_brief as seminar_brief_routes

        routes_ = [
            r for r in seminar_brief_routes.admin_router.routes
            if getattr(r, "path", "").endswith("/seminar-brief")
        ]
        assert len(routes_) == 1
        route = routes_[0]
        assert (getattr(route, "methods", set()) or set()) == {"GET"}
        dep_names = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
        assert "_require_teacher" in dep_names

    @_skip_no_fastapi
    def test_route_function_calls_viewable_gate_before_building(self, monkeypatch):
        import routes.seminar_brief as seminar_brief_routes

        calls: list[str] = []
        monkeypatch.setattr(
            seminar_brief_routes, "_ensure_document_viewable",
            lambda ref, user: calls.append(f"gate:{ref}"),
        )
        monkeypatch.setattr(
            seminar_brief_routes, "build_seminar_brief",
            lambda session, ref: calls.append(f"build:{ref}") or {"available": True},
        )

        class _S:
            def close(self):
                calls.append("close")

        monkeypatch.setattr(seminar_brief_routes, "_pg_session", lambda: _S())
        result = seminar_brief_routes.get_seminar_brief(
            "doc-1", current_user={"id": "u-1", "role": "instructor"}
        )
        assert result == {"available": True}
        assert calls == ["gate:doc-1", "build:doc-1", "close"]


# ===========================================================================
# 正直縮退（available: false）
# ===========================================================================


class TestAvailabilityDegrade:
    def test_unresolved_document_degrades_honestly(self):
        session = _FakeSession(document_row=None)
        brief = sb.build_seminar_brief(session, "no-such-doc")
        assert brief["available"] is False
        assert brief["reason"]

    def test_document_without_course_mapping_degrades_honestly(self):
        session = _FakeSession(course_rows=[("",)])
        brief = sb.build_seminar_brief(session, "doc-1")
        assert brief["available"] is False
        assert brief["document_id"] == "doc-1"
        assert brief["reason"]

    def test_empty_ref_degrades_honestly(self):
        brief = sb.build_seminar_brief(_FakeSession(), "")
        assert brief["available"] is False


# ===========================================================================
# course_id 導出の決定論化（ORDER BY）
# ===========================================================================


class TestCourseDerivationIsDeterministic:
    class _RecordingSession:
        """_derive_course_id が発行する SQL を記録する fake。"""

        def __init__(self, rows):
            self.rows = rows
            self.sql: list[str] = []

        def execute(self, statement, params=None):
            self.sql.append(str(statement))
            return _FakeResult(list(self.rows))

    def test_course_derivation_sql_orders_by_created_at_desc(self):
        """複数コース対応時に返す course_id が実行ごとに揺れないこと（決定論化）。"""
        session = self._RecordingSession([("course-newer",), ("course-older",)])
        course_id = sb._derive_course_id(session, "doc-1")
        assert course_id == "course-newer"  # 並び先頭（=最新）を採用
        assert len(session.sql) == 1
        sql = session.sql[0]
        assert "FROM theory_component_graphs" in sql
        assert "ORDER BY created_at DESC" in sql
        # 同時刻タイブレークも安定させる
        assert "ORDER BY created_at DESC, course_id" in sql

    def test_course_derivation_skips_empty_rows(self):
        session = self._RecordingSession([("",), (None,), ("course-1",)])
        assert sb._derive_course_id(session, "doc-1") == "course-1"


# ===========================================================================
# 4区画の合成と SB2（数値非漏洩）
# ===========================================================================


class TestBriefComposition:
    def test_compile_is_document_scoped_without_challenger_names(self, monkeypatch):
        brief = _patched_brief(monkeypatch)
        seen = brief["_seen"]
        assert seen["course_id"] == "course-1"
        assert seen["include_challenger_names"] is False  # ブリーフに疑義者名を載せない
        assert seen["document_id"] == "doc-1"

    def test_fragile_assumptions_carry_graded_labels_and_sl_keys(self, monkeypatch):
        brief = _patched_brief(monkeypatch)
        item = brief["fragile_assumptions"][0]
        assert item["load_level"] == "high"
        assert item["load_level_label"] == "高"  # LOAD_LEVEL_LABELS 由来
        assert item["challenge_count_label"] == "少数"
        # SL 4キー（賭け金の台帳の段階表示）
        for key in (
            "has_falsification_condition", "falsification_not_formulable",
            "reachability_summary", "support_line_level",
        ):
            assert key in item
        # claim つまづき補助は段階ラベルのみ（stumble の k-匿名集約の再利用）
        assert item["stumble"]["axes"]["error_rate"] == "高"
        assert item["stumble"]["axes"]["faq"]["questions"] == "3-5"

    def test_single_support_lines_use_fact_line_verbatim(self, monkeypatch):
        brief = _patched_brief(monkeypatch)
        lines = brief["single_support_lines"]
        assert lines, "level=single の対象は区画②に載る"
        assert lines[0]["fact_line"].startswith("この対象は単一の支持線に立っています。")
        assert lines[0]["statement"]

    def test_clear_skies_use_fixed_closed_world_fact_line(self, monkeypatch):
        brief = _patched_brief(monkeypatch)
        skies = brief["clear_skies"]
        assert skies, "untested × スコープ空欄の対象は区画③に載る"
        for sky in skies:
            assert sky["fact_line"] == sb.FACT_LINE_NO_VERIFICATION_RECORD
            assert "このコーパスの中では" in sky["fact_line"]

    def test_verified_or_scoped_items_do_not_enter_clear_skies(self, monkeypatch):
        items = [
            _assumption_item(target_id="t-1", verification_status="unknown"),
            _assumption_item(target_id="t-2", scope_count_is_zero=False),
        ]
        brief = _patched_brief(monkeypatch, items=items)
        assert brief["clear_skies"] == []

    def test_fourth_section_is_reserved_with_fixed_note(self, monkeypatch):
        """SB3: 第4区画は空欄予約（警告色・催促文にしない固定の事実文）。"""
        brief = _patched_brief(monkeypatch)
        assert brief["learner_handoff"] == {
            "reserved": True,
            "note": "（この区画は、学習者からの手渡しの仕組みの実装後に使われます）",
        }

    def test_no_raw_numeric_keys_anywhere(self, monkeypatch):
        """SB2: dependent_count / n_items / load_score / confidence の再帰非漏洩。"""
        brief = _patched_brief(monkeypatch)
        brief.pop("_seen")
        offending = [k for k, _ in _walk(brief) if k in _FORBIDDEN_NUMERIC_KEYS]
        assert offending == [], f"生数値キーがブリーフに漏洩: {offending}"

    def test_fragile_assumptions_capped_at_eight(self, monkeypatch):
        items = [_assumption_item(target_id=f"t-{i}") for i in range(12)]
        brief = _patched_brief(monkeypatch, items=items)
        assert len(brief["fragile_assumptions"]) == 8


# ===========================================================================
# SL1 閉世界語彙 / SB4 / 構造ガードレール
# ===========================================================================


class TestStructuralGuardrails:
    def test_fixed_phrase_present_and_denylist_absent(self):
        assert "このコーパスの中では" in sb.FACT_LINE_NO_VERIFICATION_RECORD
        assert_source_forbids(_CORE_SRC, _SL1_DENYLIST, context="core/doubt/seminar_brief.py")
        assert_source_forbids(_ROUTE_SRC, _SL1_DENYLIST, context="routes/seminar_brief.py")

    def test_sb4_no_learner_level_query_paths(self):
        """SB4: 学習者個人・学習者別件数のクエリを書かない（stumble の k-匿名集約の再利用のみ）。"""
        assert_source_forbids(
            _CORE_SRC, ("interest_traces", "learning_states", "DISTINCT user_id", "user_id"),
            context="core/doubt/seminar_brief.py",
        )
        assert "get_stumble_summary" in _CORE_SRC  # claim つまづきは既存集約の経由のみ

    def test_core_module_does_not_import_fastapi_or_llm(self):
        assert_source_does_not_import(
            _CORE_SRC, ("fastapi", "core.llm", "openai"),
            context="core/doubt/seminar_brief.py",
        )

    def test_core_module_is_read_only(self):
        assert_source_forbids(
            _CORE_SRC, ("INSERT INTO", "UPDATE ", "DELETE FROM"),
            context="core/doubt/seminar_brief.py",
        )

    def test_open_assumptions_gained_optional_document_scope(self):
        """§3 精査④: optional document_id（既定 ""・percentile は course 全体のまま）。"""
        assert 'document_id: str = ""' in _OPEN_ASSUMPTIONS_SRC
        assert "AND document_id = :doc" in _OPEN_ASSUMPTIONS_SRC
        # 支持線の共有文脈にも document_id を透過する
        assert (
            "build_support_context(session, course_id=course_id, document_id=document_id)"
            in _OPEN_ASSUMPTIONS_SRC
        )
        # percentile は course 全体（document で絞らない — 「高」の意味を保つ）
        assert "load_percentiles(session, course_id)" in _OPEN_ASSUMPTIONS_SRC
