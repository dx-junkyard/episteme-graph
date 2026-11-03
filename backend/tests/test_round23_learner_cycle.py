"""第 23 波・第 1 波の回帰テスト — 理解サイクルまわりの「事実と表示の一致」。

机上レビュー ``uxsim/runs/round23/desk_review.md`` の F1 / F3 で、コードの行で確かめた欠陥:

- F1 持ち越しの問い: 候補から選ぶと 80 字で切った表示用の文（印の種類の接頭辞付き）が
  本文として保存されていた（RD1 逐語に反する）。
- F1 保存の偽表示: 持ち越し・開いた動機・予想・予想との違いの 4 か所が、保存 API の
  成否を見ずに「記録しました」「残しました」と出していた。
- F1 欄外の印: 「問いの軌跡」の一覧を流用していたため、教材に付けた印（anchor_mark）が
  一度も出なかった。
- F3 精読モード: ON が効くのは動機未記録の初回開幕だけで、押しても画面に何も出なかった。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from core.cycle.derive import build_landing_candidates, build_margin_marks  # noqa: E402
from core.trace_registry import CONSUMERS, MARGIN_MARK_KINDS, TRACE_KINDS  # noqa: E402

DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
CYCLE_ROUTE = ROOT / "backend" / "api" / "routes" / "cycle.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(src: str, header: str) -> str:
    start = src.index(header)
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"関数の終わりが見つからない: {header}")


# ---------------------------------------------------------------------------
# F1 持ち越しの問い — 保存するのは本文そのもの
# ---------------------------------------------------------------------------


class TestCarryoverKeepsTheWholeText:
    LONG = "あ" * 120

    def test_tension_candidate_carries_the_full_text(self):
        rows = [{
            "id": "t1", "kind": "tension", "status": "articulated",
            "payload": {"learner_text": self.LONG},
        }]
        (c,) = build_landing_candidates(rows)
        assert c["text"] == self.LONG
        assert c["label"].endswith("…") and len(c["label"]) < len(self.LONG)

    def test_anchor_mark_text_has_no_quick_label_prefix(self):
        rows = [{
            "id": "a1", "kind": "anchor_mark", "status": "open",
            "payload": {
                "text": "共振の幅の内側だけで感度が効く理由",
                "quick_label": "not_yet",
                "structure_anchor": {"status": "active"},
            },
        }]
        (c,) = build_landing_candidates(rows)
        assert c["label"].startswith("まだ分からない: ")
        assert c["text"] == "共振の幅の内側だけで感度が効く理由"

    def test_discuss_js_saves_text_not_label(self):
        js = _read(DISCUSS_JS)
        assert "data-cycle-leave-label" not in js
        assert 'data-cycle-leave-text="' in js
        assert 'this.getAttribute("data-cycle-leave-text")' in js


# ---------------------------------------------------------------------------
# F1 保存の成否で事実文を変える（共通関数 1 つ）
# ---------------------------------------------------------------------------


class TestSavesReportTheirOutcome:
    def test_common_failure_function_exists(self):
        js = _read(DISCUSS_JS)
        assert "function showSaveFailed(host, btn) {" in js
        assert 'var SAVE_FAILED_FACT = "保存できませんでした。入力はそのまま残しています。";' in js

    def test_carryover_checks_the_result_before_saying_done(self):
        body = _function_body(_read(DISCUSS_JS), "async function saveCycleCarryover(")
        assert "if (!saved) {" in body
        assert body.index("showSaveFailed(") < body.index("次回の開幕で待っています。")

    def test_motive_prediction_and_reflection_check_the_result(self):
        js = _read(DISCUSS_JS)
        bind = _function_body(js, "function bindOpeningEvents(containerEl) {")
        assert "if (!motiveSaved) {" in bind
        assert bind.index("if (!motiveSaved) {") < bind.index("記録しました。")
        assert js.count("showSaveFailed(") >= 6  # 定義 + 5 か所（動機・予想・違い・持ち越し・書き置き）
        diff = _function_body(js, "function renderCycleDiff(docs, predictionText) {")
        assert "res && res.ok" in diff
        assert diff.index("showSaveFailed(") < diff.index("残しました。")

    def test_leave_note_uses_the_same_function(self):
        body = _function_body(_read(DISCUSS_JS), "async function saveLeaveNote() {")
        assert "showSaveFailed(box, btn);" in body


# ---------------------------------------------------------------------------
# F1 欄外の印 — 登録簿の margin_marks 宣言が母集合
# ---------------------------------------------------------------------------


class TestMarginMarks:
    def test_registry_declares_margin_mark_kinds(self):
        assert MARGIN_MARK_KINDS == frozenset({"question", "tension", "anchor_mark"})
        # 問いの軌跡からは除外のまま（印は軌跡の一覧に出さない）。
        assert TRACE_KINDS["anchor_mark"].learner_trajectory is False
        assert CONSUMERS["cycle_margin_marks"]["kinds"] is MARGIN_MARK_KINDS

    def test_anchor_mark_is_included_with_its_kind_label(self):
        rows = [{
            "id": "a1", "kind": "anchor_mark", "status": "open",
            "payload": {
                "text": "この近似の成り立つ範囲",
                "quick_label": "not_yet",
                "structure_anchor": {
                    "status": "active", "anchor_label": "この近似の成り立つ範囲",
                },
            },
        }]
        (m,) = build_margin_marks(rows)["marks"]
        assert m == {
            "kind": "anchor_mark",
            "text": "この近似の成り立つ範囲",
            "anchor_label": "",  # 本文と同じ抜き書きは重ねない
            "mark_label": "まだ分からない",
        }

    def test_unconfirmed_and_excluded_rows_are_dropped(self):
        rows = [
            {"id": "q1", "kind": "question", "status": "open",
             "payload": {"text": "候補のまま",
                         "structure_anchor": {"attribution_source": "llm_candidate"}}},
            {"id": "t1", "kind": "tension", "status": "candidate",
             "payload": {"text": "AIの候補"}},
            {"id": "t2", "kind": "tension", "status": "articulated",
             "payload": {"text": "引き受けた", "map_excluded": True}},
            {"id": "a1", "kind": "anchor_mark", "status": "open",
             "payload": {"text": "取り下げた印", "structure_anchor": {"status": "dismissed"}}},
            {"id": "x1", "kind": "intention", "status": "open", "payload": {"text": "動機"}},
            {"id": "q2", "kind": "question", "status": "open",
             "payload": {"text": "確定した問い",
                         "structure_anchor": {"attribution_source": "confirmed",
                                              "anchor_label": "式 (3)"}}},
        ]
        marks = build_margin_marks(rows)["marks"]
        assert [m["text"] for m in marks] == ["確定した問い"]
        assert marks[0]["anchor_label"] == "式 (3)"

    def test_limit_and_no_numbers(self):
        rows = [{"id": f"t{i}", "kind": "tension", "status": "open",
                 "payload": {"text": f"問い{i}"}} for i in range(30)]
        dto = build_margin_marks(rows)
        assert len(dto["marks"]) == 12
        assert set(dto) == {"marks"}

    def test_route_reads_the_registry_set(self):
        src = _read(CYCLE_ROUTE)
        assert '@learning_router.get("/courses/{course_id}/cycle/margin-marks")' in src
        assert "fetch_margin_mark_traces(current_user[\"id\"], course_id, MARGIN_MARK_KINDS)" in src


# ---------------------------------------------------------------------------
# F3 精読モード
# ---------------------------------------------------------------------------


class TestPrecisionReading:
    def test_on_shows_the_predict_area_after_the_first_opening(self):
        js = _read(DISCUSS_JS)
        section = _function_body(js, "function renderCycleOpeningSection(data) {")
        assert "if (isPrecisionReadingOn()) return renderCyclePredictOnlyBlock();" in section
        # OFF のときは従来どおり何も描かない（UC1）。
        assert section.rstrip("}").rstrip().endswith('return "";')

    def test_on_opens_the_area_without_a_click_once_per_context(self):
        bind = _function_body(_read(DISCUSS_JS), "function bindOpeningEvents(containerEl) {")
        assert "[data-cycle-predict-autoopen]" in bind
        assert "predictAutoOpenedFor !== openingCache.courseId" in bind
        assert "openCyclePredictArea();" in bind

    def test_carryover_opening_offers_the_predict_button_when_on(self):
        body = _function_body(_read(DISCUSS_JS), "function renderCycleCarryoverBlock(carryover) {")
        assert "isPrecisionReadingOn()" in body
        assert "data-cycle-predict-open" in body

    def test_toggle_reports_its_state_right_away(self):
        js = _read(APP_JS)
        assert 'id="cycle-precision-fact"' in _read(INDEX_HTML)
        assert re.search(r'const PRECISION_FACT_ON =\s*"精読モード: ON。', js)
        assert re.search(r'const PRECISION_FACT_OFF =\s*"精読モード: OFF。', js)
        assert "next ? PRECISION_FACT_ON : PRECISION_FACT_OFF" in js

    def test_diff_column_names_its_provenance(self):
        js = _read(DISCUSS_JS)
        assert "論文の骨格（解析で再構成した文）" in js
        assert "'<div class=\"cycle-diff-hd\">論文の骨格</div>'" not in js


# ---------------------------------------------------------------------------
# F2 取り消しの表示
# ---------------------------------------------------------------------------


class TestWithdrawalIsReported:
    SERVICES = ROOT / "backend" / "api" / "services.py"
    LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"

    def test_truncate_records_the_reason(self):
        src = _read(self.SERVICES)
        body = src.split("def truncate_chat_and_supersede")[1].split("\ndef ")[0]
        assert 'reason: str = "rewrite"' in body
        assert "jsonb_build_object('superseded_reason'" in body
        assert "TRACE_SUPERSEDED_REASONS" in body

    def test_delete_routes_pass_delete_and_return_the_fact(self):
        src = _read(self.LEARNING)
        assert src.count('reason="delete"') == 2
        assert src.count('"fact": label_vocab.CHAT_TURN_WITHDRAWN_FACT') == 2

    def test_chat_shows_the_server_fact_after_delete(self):
        body = _function_body(_read(APP_JS), "async function deleteMessageFrom(msgId) {")
        assert "appendSystemNote(deleted.fact)" in body
        assert "取り消しました" not in body  # 文言は JS に焼き込まない

    def test_unknown_reason_is_rejected(self):
        import pytest

        import services

        with pytest.raises(ValueError):
            services.truncate_chat_and_supersede("u", "c", "t", "m", reason="other")


class TestMarginMarksRoute:
    def test_404_when_course_not_accessible(self, monkeypatch):
        import pytest
        from fastapi import HTTPException

        from api.routes import cycle

        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: None)
        with pytest.raises(HTTPException) as exc_info:
            cycle.get_cycle_margin_marks_route("course-x", current_user={"id": "u1"})
        assert exc_info.value.status_code == 404

    def test_reads_with_the_registry_kinds_and_fails_open(self, monkeypatch):
        from api.routes import cycle

        seen = {}
        monkeypatch.setattr(cycle, "get_accessible_course_data", lambda uid, cid: {"topics": []})

        def _fetch(uid, cid, kinds):
            seen["kinds"] = kinds
            return [{"id": "a", "kind": "anchor_mark", "status": "open",
                     "payload": {"text": "印", "quick_label": "curious",
                                 "structure_anchor": {"status": "active"}}}]

        monkeypatch.setattr(cycle, "fetch_margin_mark_traces", _fetch)
        out = cycle.get_cycle_margin_marks_route("c1", current_user={"id": "u1"})
        assert seen["kinds"] is MARGIN_MARK_KINDS
        assert out["marks"][0]["mark_label"] == "気になる"

        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(cycle, "fetch_margin_mark_traces", _boom)
        assert cycle.get_cycle_margin_marks_route("c1", current_user={"id": "u1"}) == {"marks": []}

    def test_route_is_get_only(self):
        from api.routes import cycle

        routes = [r for r in cycle.learning_router.routes
                  if getattr(r, "path", "").endswith("/cycle/margin-marks")]
        assert routes and all(set(r.methods) == {"GET"} for r in routes)
