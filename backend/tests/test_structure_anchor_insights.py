"""教員向け anchor インサイト（stage / doubt_type 単位の k-匿名集約）のテスト。

対象:
  - ``backend/core/structure_anchor/insights.py``（純関数 + 読み取り SQL）
  - ``GET /api/admin/courses/{course_id}/anchor-insights``（``api/routes/admin.py``）

正本: ``docs/features/structure-anchored-questions.md`` §7 Stage 3 / §8-5。

観点:
  - k-匿名（k=3・distinct 学習者数・n<3 セル非表示・レンジ表示のみ）
  - 本人未確定（``llm_candidate``）・棄却（``dismissed``）・取り除かれた往復
    （``superseded``）を集計に入れない
  - 生件数・user_id・質問原文・confidence・anchor_id を返さない（再帰キー検査）
  - ルートの権限 fail-closed（owner / editor / SYSTEM_ADMIN 以外は 404 で、
    集約処理を呼ばない）

ハーネスは ``test_object_scope_authorization.py``（route 関数を直接呼び、
モジュール属性を monkeypatch する流儀）を踏襲する。実 DB へは接続しない。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from api.routes import admin as admin_module  # noqa: E402
from core.privacy import K_ANONYMITY  # noqa: E402
from core.structure_anchor import insights  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

_ADMIN_SRC = (ROOT / "backend" / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
_INSIGHTS_SRC = (
    ROOT / "backend" / "core" / "structure_anchor" / "insights.py"
).read_text(encoding="utf-8")


def _entries(*specs) -> list[tuple[str, str, str, str]]:
    """``(user_id, anchor_type, stage_key, doubt_type)`` の列を組み立てる。"""
    return [tuple(s) for s in specs]


def _cell(cells, anchor_type, doubt_type, stage=""):
    for c in cells:
        if (
            c["anchor_type"] == anchor_type
            and c["doubt_type"] == doubt_type
            and c.get("stage", "") == stage
        ):
            return c
    return None


# ===========================================================================
# 1. 純関数 aggregate_cells — k-匿名 / レンジ / 除外
# ===========================================================================


class TestAggregateCells:
    def test_below_k_cell_is_not_returned(self):
        """k 未満（2人）のセルは結果に現れず、suppressed で存在だけを正直に示す。"""
        result = insights.aggregate_cells(_entries(
            ("u1", "stage", "elimination", "justification_gap"),
            ("u2", "stage", "elimination", "justification_gap"),
        ))
        assert result["cells"] == []
        assert result["suppressed"] is True

    def test_at_k_cell_is_returned_with_range_only(self):
        result = insights.aggregate_cells(_entries(
            ("u1", "stage", "elimination", "justification_gap"),
            ("u2", "stage", "elimination", "justification_gap"),
            ("u3", "stage", "elimination", "justification_gap"),
        ))
        assert result["suppressed"] is False
        cell = _cell(result["cells"], "stage", "justification_gap", "elimination")
        assert cell is not None
        assert cell["count_range"] == "3-5"
        # 生件数は返さない
        assert "count" not in cell
        assert K_ANONYMITY == 3

    def test_count_ranges_use_the_privacy_buckets(self):
        for n, expected in ((3, "3-5"), (5, "3-5"), (6, "6-10"), (10, "6-10"), (11, "11+")):
            result = insights.aggregate_cells(_entries(
                *[(f"u{i}", "claim", "", "definition") for i in range(n)]
            ))
            cell = _cell(result["cells"], "claim", "definition")
            assert cell is not None and cell["count_range"] == expected, n

    def test_same_user_repeat_does_not_inflate_a_cell(self):
        """同一学習者の連打でセルを k に到達させない（distinct 学習者数で数える）。"""
        result = insights.aggregate_cells(_entries(
            ("u1", "claim", "", "definition"),
            ("u1", "claim", "", "definition"),
            ("u1", "claim", "", "definition"),
            ("u2", "claim", "", "definition"),
        ))
        assert result["cells"] == []
        assert result["suppressed"] is True

    def test_stages_are_separate_cells_and_carry_japanese_labels(self):
        result = insights.aggregate_cells(_entries(
            *[(f"u{i}", "stage", "elimination", "scope") for i in range(3)],
            *[(f"v{i}", "stage", "theory_basis", "scope") for i in range(3)],
        ))
        elim = _cell(result["cells"], "stage", "scope", "elimination")
        basis = _cell(result["cells"], "stage", "scope", "theory_basis")
        assert elim is not None and basis is not None
        assert elim["stage_label"] == "消去"
        assert basis["stage_label"] == "理論の土台"
        assert elim["anchor_type_label"] == "理論構成の段階"
        assert elim["doubt_type_label"] == "どこまで成り立つのか"

    def test_non_stage_cells_carry_no_stage_key(self):
        result = insights.aggregate_cells(_entries(
            *[(f"u{i}", "equation", "", "definition") for i in range(3)]
        ))
        cell = result["cells"][0]
        assert "stage" not in cell and "stage_label" not in cell

    def test_doubt_types_are_separate_cells(self):
        """同じ段階でも疑いの型が違えば別セル（片方だけ k を満たす場合は片方だけ出る）。"""
        result = insights.aggregate_cells(_entries(
            *[(f"u{i}", "stage", "equation_system", "justification_gap") for i in range(3)],
            ("w1", "stage", "equation_system", "premise"),
            ("w2", "stage", "equation_system", "premise"),
        ))
        assert len(result["cells"]) == 1
        assert result["cells"][0]["doubt_type"] == "justification_gap"
        assert result["suppressed"] is False

    def test_ordering_is_lexicographic_not_a_ranking(self):
        """多い順に並べ替えない（P7: ランキング化しない）。"""
        result = insights.aggregate_cells(_entries(
            *[(f"a{i}", "stage", "elimination", "scope") for i in range(11)],
            *[(f"b{i}", "claim", "", "definition") for i in range(3)],
        ))
        assert [c["anchor_type"] for c in result["cells"]] == ["claim", "stage"]

    def test_empty_input_is_not_reported_as_suppressed(self):
        """データがそもそも無い場合は suppressed=False（伏せた集計は存在しない）。"""
        assert insights.aggregate_cells([]) == {"cells": [], "suppressed": False}


# ===========================================================================
# 2. 読み取り SQL — 対象行の絞り込み
# ===========================================================================


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows=None, raises=None):
        self.rows = list(rows or [])
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        if self.raises is not None:
            raise self.raises
        return _FakeResult(self.rows)

    def close(self):
        self.closed = True


def _row(user_id, anchor, *, kind_ok=True):
    return (user_id, {"text": "これはどういう意味ですか", "structure_anchor": anchor})


def _anchor(**kwargs):
    base = {
        "anchor_type": "stage",
        "anchor_id": "elimination",
        "anchor_label": "消去",
        "doubt_type": "justification_gap",
        "attribution_source": "confirmed",
        "status": "active",
        "confidence": 0.81,
        "evidence_quote": "なぜ消えるのか",
    }
    base.update(kwargs)
    return base


class TestCollectAnchorEntries:
    def test_sql_filters_owned_confirmed_active_and_non_superseded(self):
        session = _FakeSession(rows=[_row("u1", _anchor())])
        entries = insights.collect_anchor_entries(session, "course-1")
        sql, params = session.calls[0]
        assert "kind = 'question'" in sql
        assert "status <> 'superseded'" in sql
        assert "attribution_source" in sql and "= ANY(:sources)" in sql
        assert "<> 'dismissed'" in sql
        assert params["sources"] == ["learner_selected", "confirmed"]
        assert params["cid"] == "course-1"
        assert entries == [("u1", "stage", "elimination", "justification_gap")]

    def test_llm_candidate_is_excluded_by_the_sources_bind(self):
        """本人未確定の帰属（llm_candidate）は bind 値に含まれない（P1）。"""
        session = _FakeSession(rows=[])
        insights.collect_anchor_entries(session, "course-1")
        assert "llm_candidate" not in session.calls[0][1]["sources"]

    def test_json_string_payload_is_parsed(self):
        import json as _json

        session = _FakeSession(rows=[("u1", _json.dumps({"structure_anchor": _anchor()}))])
        assert insights.collect_anchor_entries(session, "c") == [
            ("u1", "stage", "elimination", "justification_gap"),
        ]

    def test_unknown_anchor_type_is_skipped(self):
        session = _FakeSession(rows=[_row("u1", _anchor(anchor_type="mystery"))])
        assert insights.collect_anchor_entries(session, "c") == []

    def test_unknown_stage_id_degrades_to_no_stage(self):
        """stage 語彙に無い anchor_id は stage として扱わない（domain-independent 維持）。"""
        session = _FakeSession(rows=[_row("u1", _anchor(anchor_id="higgs_sector"))])
        assert insights.collect_anchor_entries(session, "c") == [
            ("u1", "stage", "", "justification_gap"),
        ]

    def test_unknown_doubt_type_falls_back_to_unclassified(self):
        session = _FakeSession(rows=[_row("u1", _anchor(doubt_type="???"))])
        assert insights.collect_anchor_entries(session, "c")[0][3] == "unclassified"

    def test_missing_structure_anchor_is_skipped(self):
        session = _FakeSession(rows=[("u1", {"text": "質問"})])
        assert insights.collect_anchor_entries(session, "c") == []

    def test_anchor_id_is_dropped_for_non_stage_types(self):
        """stage 以外は anchor_id を持ち出さない（個別要素まで割らない断面）。"""
        session = _FakeSession(rows=[
            _row("u1", _anchor(anchor_type="claim", anchor_id="claim-9f3")),
        ])
        entries = insights.collect_anchor_entries(session, "c")
        assert entries == [("u1", "claim", "", "justification_gap")]
        assert not any("claim-9f3" in str(v) for e in entries for v in e)


# ===========================================================================
# 3. aggregate_anchor_insights — DTO の形と数値非漏洩
# ===========================================================================


def _iter_keys_and_values(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}", k, v
            yield from _iter_keys_and_values(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_keys_and_values(v, f"{path}[{i}]")


_FORBIDDEN_KEYS = {
    "count", "counts", "n", "total", "user_id", "user_ids", "users",
    "learner_ids", "text", "question", "evidence_quote", "confidence",
    "anchor_id", "anchor_label", "trace_id", "score",
}


class TestAggregateAnchorInsights:
    def test_dto_shape(self):
        session = _FakeSession(rows=[_row(f"u{i}", _anchor()) for i in range(4)])
        result = insights.aggregate_anchor_insights(session, "course-1")
        assert set(result) == {"course_id", "cells", "suppressed", "note"}
        assert result["course_id"] == "course-1"
        assert result["suppressed"] is False
        assert result["cells"][0]["count_range"] == "3-5"
        assert "評価利用は禁止" in result["note"]

    def test_suppressed_note_is_a_fact_not_an_error(self):
        session = _FakeSession(rows=[_row(f"u{i}", _anchor()) for i in range(2)])
        result = insights.aggregate_anchor_insights(session, "course-1")
        assert result == {
            "course_id": "course-1",
            "cells": [],
            "suppressed": True,
            "note": insights.SUPPRESSED_NOTE,
        }

    def test_blank_course_id_returns_empty_without_touching_the_db(self):
        session = _FakeSession(rows=[_row("u1", _anchor())])
        result = insights.aggregate_anchor_insights(session, "  ")
        assert result["cells"] == [] and result["course_id"] == ""
        assert session.calls == []

    def test_db_failure_degrades_to_an_empty_aggregate(self):
        session = _FakeSession(raises=RuntimeError("boom"))
        result = insights.aggregate_anchor_insights(session, "course-1")
        assert result["cells"] == [] and result["suppressed"] is False

    def test_response_never_carries_raw_numbers_or_identifiers(self):
        session = _FakeSession(rows=[
            *[_row(f"u{i}", _anchor()) for i in range(6)],
            *[_row(f"v{i}", _anchor(anchor_type="claim", anchor_id="claim-9f3")) for i in range(4)],
        ])
        result = insights.aggregate_anchor_insights(session, "course-1")
        assert result["cells"], "前提: セルが出ていること"
        for path, key, value in _iter_keys_and_values(result):
            assert key not in _FORBIDDEN_KEYS, f"{path} が禁止キー {key} を含む"
            assert not isinstance(value, (int, float)) or isinstance(value, bool), (
                f"{path} に生の数値 {value!r} が載っている（表示はレンジ・ラベルのみ）"
            )
        blob = repr(result)
        assert "claim-9f3" not in blob and "0.81" not in blob
        assert "これはどういう意味ですか" not in blob


# ===========================================================================
# 4. ガードレール（core 規約・責務の分離）
# ===========================================================================


class TestGuardrails:
    def test_core_module_does_not_import_fastapi(self):
        assert "fastapi" not in _INSIGHTS_SRC.lower()

    def test_core_module_has_no_write_sql(self):
        upper = _INSIGHTS_SRC.upper()
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER "):
            assert verb not in upper, verb

    def test_k_anonymity_comes_from_the_privacy_module(self):
        """k=3 をリテラルで再定義しない（正本は core/privacy.py）。"""
        assert "from core.privacy import" in _INSIGHTS_SRC
        assert "K_ANONYMITY = " not in _INSIGHTS_SRC

    def test_stage_labels_come_from_element_vocab(self):
        """theory stage の訳語表を新規に作らない（正本は core/element_vocab.py）。"""
        assert "from core.element_vocab import theory_stage_label" in _INSIGHTS_SRC
        assert "理論の土台" not in _INSIGHTS_SRC

    def test_tension_is_not_mixed_in(self):
        """responsibility 分離: tension の合流は naive_signal.py の担当。"""
        assert "kind = 'question'" in _INSIGHTS_SRC
        # SQL / 定数の kind リテラルとして 'tension' が現れない（散文の言及は別）
        assert "'tension'" not in _INSIGHTS_SRC


# ===========================================================================
# 5. ルート — 権限 fail-closed
# ===========================================================================

OWNER = "11111111-1111-1111-1111-111111111111"
EDITOR = "22222222-2222-2222-2222-222222222222"
VIEWER = "33333333-3333-3333-3333-333333333333"
OTHER_TEACHER = "44444444-4444-4444-4444-444444444444"
ADMIN = "55555555-5555-5555-5555-555555555555"

COURSE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
UNKNOWN_COURSE_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

_COURSE_DATA = {"title": "コース", "topics": [], "sources": []}


def _user(user_id: str, role: str = "TEACHER") -> dict:
    return {"id": user_id, "username": "u", "email": "u@example.com", "role": role}


@pytest.fixture(autouse=True)
def _patch_permission_sources(monkeypatch):
    def _fake_get_editable_course_data(user_id: str, course_id: str):
        if course_id != COURSE_ID:
            return None
        return dict(_COURSE_DATA) if user_id in (OWNER, EDITOR) else None

    def _fake_fetch_course_data_row(course_id: str):
        return dict(_COURSE_DATA) if course_id == COURSE_ID else None

    monkeypatch.setattr(
        admin_module, "get_editable_course_data", _fake_get_editable_course_data,
    )
    monkeypatch.setattr(
        admin_module, "_fetch_course_data_row", _fake_fetch_course_data_row,
    )


class TestAnchorInsightsRoute:
    @pytest.mark.parametrize(
        "user", [_user(OWNER), _user(EDITOR), _user(ADMIN, role="SYSTEM_ADMIN")],
    )
    def test_allowed_users_receive_the_aggregate(self, monkeypatch, user):
        session = _FakeSession(rows=[_row(f"u{i}", _anchor()) for i in range(3)])
        monkeypatch.setattr(admin_module, "_pg_session", lambda: session)

        result = admin_module.get_anchor_insights(COURSE_ID, current_user=user)

        assert result["course_id"] == COURSE_ID
        assert result["cells"][0]["stage"] == "elimination"
        assert session.closed is True

    @pytest.mark.parametrize(
        "user_id,course_id",
        [
            (OTHER_TEACHER, COURSE_ID),
            (VIEWER, COURSE_ID),
            (OWNER, UNKNOWN_COURSE_ID),
        ],
    )
    def test_denied_users_get_404_and_no_db_session_is_opened(
        self, monkeypatch, user_id, course_id,
    ):
        def _boom():
            raise AssertionError("認可失敗時に DB セッションを開いてはならない")

        monkeypatch.setattr(admin_module, "_pg_session", _boom)

        with pytest.raises(HTTPException) as exc:
            admin_module.get_anchor_insights(course_id, current_user=_user(user_id))
        assert exc.value.status_code == 404

    def test_detail_identical_for_missing_and_forbidden(self, monkeypatch):
        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession())
        with pytest.raises(HTTPException) as missing:
            admin_module.get_anchor_insights(UNKNOWN_COURSE_ID, current_user=_user(OWNER))
        with pytest.raises(HTTPException) as forbidden:
            admin_module.get_anchor_insights(COURSE_ID, current_user=_user(OTHER_TEACHER))
        assert missing.value.detail == forbidden.value.detail

    def test_gate_precedes_the_aggregate_call(self):
        src = extract_function_source(_ADMIN_SRC, "get_anchor_insights")
        assert src.index("_require_editable_course_or_404") < src.index(
            "aggregate_anchor_insights(session, course_id)",
        )

    def test_route_is_registered_under_courses(self):
        assert '"/courses/{course_id}/anchor-insights"' in _ADMIN_SRC

    def test_route_is_read_only_and_records_no_audit_event(self):
        src = extract_function_source(_ADMIN_SRC, "get_anchor_insights")
        assert "record_review_event" not in src
        assert "AUDIT_ENTITY" not in src
