"""主権台帳v1（core/trace_ledger.py）の純関数テスト — fake rows・DB 非接続。

対象仕様: docs/features/trace_registry_sovereignty_ledger_design.md §3.2。
build_ledger_overview / build_ledger_export を dict のリストだけで検証する
（core/personal_graph の derive 系テストと同型）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.trace_ledger import (  # noqa: E402
    PROVENANCE_NOTE,
    PUBLICITY_CANDIDATE,
    PUBLICITY_DASHBOARD,
    PUBLICITY_PRIVATE,
    build_ledger_export,
    build_ledger_overview,
)
from core.trace_registry import TRACE_KINDS  # noqa: E402


def _row(**overrides) -> dict:
    row = {
        "id": "t-1",
        "kind": "question",
        "status": "open",
        "course_id": "c-1",
        "topic_id": "topic-1",
        "payload": {"text": "なぜ線形化できるのか", "context_label": "トピック1"},
        "created_at": "2026-08-15T10:00:00+00:00",
    }
    row.update(overrides)
    return row


def _overview(rows, *, course_labels=None, truncated=False) -> dict:
    return build_ledger_overview(
        rows, course_labels=course_labels or {}, truncated=truncated
    )


def _all_items(overview) -> list[dict]:
    return [item for system in overview["systems"] for item in system["items"]]


# ===========================================================================
# 系統グルーピング
# ===========================================================================


class TestSystemGrouping:
    def test_systems_follow_registry_declaration_order(self):
        overview = _overview([])
        assert [s["kind"] for s in overview["systems"]] == list(TRACE_KINDS)

    def test_labels_and_dead_come_from_registry(self):
        overview = _overview([])
        by_kind = {s["kind"]: s for s in overview["systems"]}
        assert by_kind["question"]["label"] == "問い"
        assert by_kind["tension"]["label"] == "引っかかり"
        assert by_kind["detour"]["dead"] is True
        assert all(not s["dead"] for k, s in by_kind.items() if k != "detour")

    def test_rows_are_grouped_into_their_kind(self):
        rows = [
            _row(id="t-1", kind="question"),
            _row(id="t-2", kind="tension", status="articulated"),
            _row(id="t-3", kind="intention", status="open"),
        ]
        overview = _overview(rows)
        by_kind = {s["kind"]: s for s in overview["systems"]}
        assert [i["id"] for i in by_kind["question"]["items"]] == ["t-1"]
        assert [i["id"] for i in by_kind["tension"]["items"]] == ["t-2"]
        assert [i["id"] for i in by_kind["intention"]["items"]] == ["t-3"]

    def test_unregistered_kind_rows_are_kept_not_dropped(self):
        """P4: 登録簿に無い kind の行も落とさず末尾の系統として保持する。"""
        overview = _overview([_row(id="t-x", kind="legacy_kind")])
        tail = overview["systems"][-1]
        assert tail["kind"] == "legacy_kind"
        assert [i["id"] for i in tail["items"]] == ["t-x"]


# ===========================================================================
# 行の保持（P4）と射影
# ===========================================================================


class TestRowRetentionAndProjection:
    def test_candidate_superseded_dismissed_rows_are_all_kept(self):
        rows = [
            _row(id="t-1", kind="tension", status="candidate"),
            _row(id="t-2", kind="tension", status="dismissed"),
            _row(id="t-3", kind="question", status="superseded"),
        ]
        items = _all_items(_overview(rows))
        assert {i["id"] for i in items} == {"t-1", "t-2", "t-3"}

    def test_status_labels_come_from_label_vocab(self):
        rows = [
            _row(id="t-1", status="open"),
            _row(id="t-2", status="superseded"),
            _row(id="t-3", kind="tension", status="dismissed"),
        ]
        by_id = {i["id"]: i for i in _all_items(_overview(rows))}
        assert by_id["t-1"]["status_label"] == "未解決"
        assert by_id["t-2"]["status_label"] == "書き直しで差し替え"
        assert by_id["t-3"]["status_label"] == "見送り（保持）"

    def test_unknown_status_is_labeled_sonota_not_passed_through(self):
        items = _all_items(_overview([_row(status="weird_status")]))
        assert items[0]["status_label"] == "その他"

    def test_flags_reflect_row_state(self):
        rows = [
            _row(id="t-1", kind="tension", status="candidate"),
            _row(id="t-2", status="superseded"),
            _row(id="t-3", payload={"text": "x", "map_excluded": True}),
        ]
        by_id = {i["id"]: i for i in _all_items(_overview(rows))}
        assert by_id["t-1"]["flags"] == {
            "map_excluded": False, "superseded": False, "candidate": True,
        }
        assert by_id["t-2"]["flags"]["superseded"] is True
        assert by_id["t-3"]["flags"]["map_excluded"] is True

    def test_course_label_resolution(self):
        overview = _overview(
            [_row(course_id="c-1"), _row(id="t-2", course_id="c-gone")],
            course_labels={"c-1": "素粒子物理入門"},
        )
        by_id = {i["id"]: i for i in _all_items(overview)}
        assert by_id["t-1"]["course_label"] == "素粒子物理入門"
        # 削除済みコースはタイトルが引けないだけで行は残る（P4）。
        assert by_id["t-2"]["course_label"] == ""
        assert by_id["t-2"]["course_id"] == "c-gone"


# ===========================================================================
# 公表状態の事実文（3種の分岐）
# ===========================================================================


class TestPublicity:
    def test_candidate_row_gets_candidate_fact(self):
        items = _all_items(_overview([_row(kind="tension", status="candidate")]))
        assert items[0]["publicity"] == PUBLICITY_CANDIDATE
        assert "あなたが確定するまで" in PUBLICITY_CANDIDATE

    def test_dashboard_kind_row_gets_aggregation_fact(self):
        items = _all_items(_overview([_row(kind="question", status="open")]))
        assert items[0]["publicity"] == PUBLICITY_DASHBOARD
        assert "匿名集計" in PUBLICITY_DASHBOARD

    def test_private_kind_row_gets_private_fact(self):
        for kind in ("help_usage", "intention", "anchor_mark"):
            items = _all_items(_overview([_row(kind=kind, status="open")]))
            assert items[0]["publicity"] == PUBLICITY_PRIVATE, kind
        assert PUBLICITY_PRIVATE == "あなた以外には表示されません。"

    def test_candidate_takes_priority_over_dashboard_kind(self):
        """tension は dashboard 対象 kind だが、candidate 行は候補の事実文が優先。"""
        items = _all_items(_overview([_row(kind="tension", status="candidate")]))
        assert items[0]["publicity"] == PUBLICITY_CANDIDATE

    def test_system_publicity_notes_are_static_facts(self):
        overview = _overview([])
        by_kind = {s["kind"]: s for s in overview["systems"]}
        assert "わたしの地図" in by_kind["question"]["publicity_note"]
        assert "匿名集計" in by_kind["question"]["publicity_note"]
        assert by_kind["intention"]["publicity_note"] == PUBLICITY_PRIVATE
        assert by_kind["anchor_mark"]["publicity_note"] == PUBLICITY_PRIVATE


# ===========================================================================
# 数値キー非漏洩（W8 同型）・件数フィールド禁止（TR6）・来歴（TR5）
# ===========================================================================


class TestNoNumericLeakage:
    def test_numeric_payload_keys_never_reach_the_overview(self):
        rows = [
            _row(payload={
                "text": "問いの本文",
                "context_label": "文脈",
                "confidence": 0.92,
                "load_score": 7,
                "score": 3,
                "weight": 0.5,
                "nested": {"confidence": 0.8},
            }),
        ]
        dumped = json.dumps(_overview(rows), ensure_ascii=False)
        for forbidden in ("confidence", "load_score", '"score"', '"weight"', "0.92"):
            assert forbidden not in dumped

    def test_overview_has_no_count_fields(self):
        overview = _overview([_row(), _row(id="t-2")])
        dumped = json.dumps(overview, ensure_ascii=False)
        for forbidden in ("count", "total", "progress"):
            assert forbidden not in dumped

    def test_truncated_flag_is_passed_through(self):
        assert _overview([], truncated=True)["truncated"] is True
        assert _overview([], truncated=False)["truncated"] is False

    def test_provenance_note_is_the_honest_fact(self):
        overview = _overview([])
        assert overview["provenance_note"] == PROVENANCE_NOTE
        assert "現在記録されていません" in PROVENANCE_NOTE


# ===========================================================================
# 持ち出し（export）
# ===========================================================================


class TestExport:
    def test_schema_version_and_note(self):
        export = build_ledger_export([], exported_at="2026-08-15T10:00:00+00:00")
        assert export["schema_version"] == 1
        assert export["exported_at"] == "2026-08-15T10:00:00+00:00"
        assert "持ち出し" in export["note"]

    def test_records_carry_full_unmodified_payload(self):
        payload = {"text": "本文", "confidence": 0.9, "structure_anchor": {"a": 1}}
        export = build_ledger_export(
            [_row(payload=payload)], exported_at="2026-08-15T10:00:00+00:00"
        )
        record = export["records"][0]
        assert record["payload"] == payload  # 全文・無加工（数値キーも削らない）
        assert record["text"] == "本文"
        assert record["kind"] == "question"
        assert record["status"] == "open"
        assert record["course_id"] == "c-1"
        assert record["topic_id"] == "topic-1"
        assert record["created_at"] == "2026-08-15T10:00:00+00:00"

    def test_export_contains_no_user_id_key(self):
        export = build_ledger_export(
            [_row()], exported_at="2026-08-15T10:00:00+00:00"
        )
        assert "user_id" not in json.dumps(export, ensure_ascii=False)

    def test_all_rows_are_exported_including_dismissed_and_superseded(self):
        rows = [
            _row(id="t-1", kind="tension", status="dismissed"),
            _row(id="t-2", status="superseded"),
            _row(id="t-3", kind="anchor_mark", status="open"),
        ]
        export = build_ledger_export(rows, exported_at="2026-08-15T10:00:00+00:00")
        assert [r["id"] for r in export["records"]] == ["t-1", "t-2", "t-3"]
