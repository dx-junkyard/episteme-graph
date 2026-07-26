"""着地画面（「今日の議論を振り返る」）の改善2点のテスト。

1. 帰属候補カードが帰属（anchor_label / doubt_type_label）を提示すること
   — 従来は API が返す帰属情報を捨てて質問文だけを表示し、ボタン文言も
   「この理解で残す」で操作の意味（帰属の確定）とズレていた。app.js の
   ``renderAnchorDigestCard`` と同じ問いかけの形に揃える。
2. 「今日の理解を自分の言葉で」— 候補生成（非同期 LLM）を待たずに本人の記述を
   ``status='articulated'`` の tension として残す直接経路
   （``services.record_learner_articulated_tension`` +
   ``POST /api/learning/courses/{course_id}/discuss/reflection``）。

既存の discuss テストと同型: ソース静的検査 + ``record_interest_trace`` /
``_record_tension_event`` を差し替えた純粋ユニットテスト（DB・実サーバ不使用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from tests.guardrail_helpers import extract_function_source  # noqa: E402

SERVICES = ROOT / "backend" / "api" / "services.py"
LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _js_function(src: str, signature: str) -> str:
    """JS 関数を素朴な波括弧カウントで切り出す（test_discuss_phase2_ui_static.py と同じ流儀）。"""
    start = src.index(signature)
    depth = 0
    i = src.index("{", start)
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unterminated function body for: " + signature)


# ---------------------------------------------------------------------------
# services.record_learner_articulated_tension（本人の言葉 → tension 1行）
# ---------------------------------------------------------------------------


class _Recorder:
    """record_interest_trace の差し替え。呼び出し引数を丸ごと控える。"""

    def __init__(self, trace_id: str | None = "trace-1"):
        self.trace_id = trace_id
        self.calls: list[dict] = []

    def __call__(self, user_id, course_id, topic_id, kind, text,
                 context_label="", extra_payload=None, status="open"):
        self.calls.append({
            "user_id": user_id, "course_id": course_id, "topic_id": topic_id,
            "kind": kind, "text": text, "context_label": context_label,
            "extra_payload": dict(extra_payload or {}), "status": status,
        })
        return self.trace_id


@pytest.fixture()
def services_mod(monkeypatch):
    from api import services

    return services


@pytest.fixture()
def recorder(monkeypatch, services_mod):
    rec = _Recorder()
    audits: list[tuple] = []
    monkeypatch.setattr(services_mod, "record_interest_trace", rec)
    monkeypatch.setattr(
        services_mod, "_record_tension_event",
        lambda *args, **kwargs: audits.append((args, kwargs)),
    )
    rec.audits = audits  # type: ignore[attr-defined]
    return rec


class TestRecordLearnerArticulatedTension:
    def test_records_owned_tension_row(self, services_mod, recorder):
        out = services_mod.record_learner_articulated_tension(
            "u1", "c1", "_discussion", "  感度は共振の幅の内側だけ効くと理解した。 ",
            context_label="論文との議論", origin="discuss_landing",
        )
        assert out == {"trace_id": "trace-1", "status": "articulated"}
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["kind"] == "tension"
        # 候補（candidate）ではなく本人が引き受けた状態で入る
        assert call["status"] == "articulated"
        assert call["text"] == "感度は共振の幅の内側だけ効くと理解した。"
        assert call["context_label"] == "論文との議論"
        assert call["extra_payload"]["learner_text"] == "感度は共振の幅の内側だけ効くと理解した。"
        assert call["extra_payload"]["self_articulated"] is True
        assert call["extra_payload"]["origin"] == "discuss_landing"

    def test_status_is_owned_by_learner_so_it_becomes_a_map_node(self):
        """articulated は本人が引き受けた状態の語彙に含まれる（個人ネットワークに載る条件）。"""
        from core.tension.schema import TENSION_OWNED_STATUSES

        assert "articulated" in TENSION_OWNED_STATUSES

    def test_empty_text_records_nothing(self, services_mod, recorder):
        assert services_mod.record_learner_articulated_tension("u1", "c1", "_discussion", "   ") is None
        assert services_mod.record_learner_articulated_tension("u1", "c1", "_discussion", "") is None
        assert recorder.calls == []

    def test_text_is_capped(self, services_mod, recorder):
        services_mod.record_learner_articulated_tension("u1", "c1", "_discussion", "あ" * 3000)
        assert len(recorder.calls[0]["text"]) == 1000

    def test_state_change_is_audited(self, services_mod, recorder):
        services_mod.record_learner_articulated_tension(
            "u1", "c1", "_discussion", "わかったこと", origin="discuss_landing",
        )
        assert len(recorder.audits) == 1  # type: ignore[attr-defined]
        args = recorder.audits[0][0]  # type: ignore[attr-defined]
        # (trace_id, old_status, new_status, user_id, metadata)
        assert args[0] == "trace-1"
        assert args[1] == ""  # 候補を経由していないので旧状態は空
        assert args[2] == "articulated"
        assert args[3] == "u1"

    def test_no_trace_id_means_no_audit(self, services_mod, monkeypatch):
        audits: list = []
        monkeypatch.setattr(services_mod, "record_interest_trace", _Recorder(trace_id=None))
        monkeypatch.setattr(
            services_mod, "_record_tension_event", lambda *a, **k: audits.append(a),
        )
        assert services_mod.record_learner_articulated_tension("u1", "c1", "_discussion", "x") is None
        assert audits == []


# ---------------------------------------------------------------------------
# ルート（POST /courses/{course_id}/discuss/reflection）
# ---------------------------------------------------------------------------


class TestReflectionRoute:
    def _body(self) -> str:
        return extract_function_source(_read(LEARNING), "record_discuss_reflection")

    def test_route_registered(self):
        source = _read(LEARNING)
        assert '@router.post("/courses/{course_id}/discuss/reflection", status_code=201)' in source

    def test_empty_text_is_422(self):
        body = self._body()
        assert "status_code=422" in body

    def test_course_access_gate_before_write(self):
        body = self._body()
        assert "get_course_data(current_user[\"id\"], course_id)" in body
        assert body.index("get_course_data") < body.index("record_learner_articulated_tension")
        assert "status_code=404" in body

    def test_uses_reserved_discussion_topic(self):
        body = self._body()
        assert "DISCUSSION_TOPIC_ID" in body
        assert "DISCUSSION_TOPIC_LABEL" in body
        assert 'origin="discuss_landing"' in body

    def test_no_llm_call_in_route(self):
        """着地は非LLM（DM8）。要約・言い換えを AI にさせない（P1）。"""
        body = self._body()
        for token in ("generate_", "core.llm", "openai", "resolve_model"):
            assert token not in body.lower()


# ---------------------------------------------------------------------------
# UI（discuss.js）
# ---------------------------------------------------------------------------


class TestAnchorCardShowsAttribution:
    def _card(self) -> str:
        return _js_function(_read(DISCUSS_JS), "function anchorCardHtml(item) {")

    def test_card_asks_about_attribution_not_understanding(self):
        card = self._card()
        assert "この疑問は" in card
        assert "anchor_label" in card
        assert "doubt_type_label" in card
        # 質問文の echo だけを見せるカードには戻さない
        assert "この理解で残す" not in _read(DISCUSS_JS)

    def test_question_text_is_still_shown_as_the_quote(self):
        assert "question_text" in self._card()

    def test_unclassified_falls_back_without_an_empty_label(self):
        card = self._card()
        assert 'item.doubt_type === "unclassified"' in card
        assert "この疑問はどこへの引っかかりでしたか?" in card

    def test_doubt_type_correction_chips_present(self):
        js = _read(DISCUSS_JS)
        assert "ANCHOR_DOUBT_OPTIONS" in js
        assert "data-discuss-anchor-correct" in js
        assert "data-discuss-anchor-doubt" in js
        # 提案どおりの様相はチップに出さない（訂正肢のみ）
        assert "if (o.doubt_type === item.doubt_type) return;" in self._card()

    def test_correction_chip_confirms_with_that_doubt_type(self):
        js = _read(DISCUSS_JS)
        confirm = _js_function(js, "async function confirmAnchorCard(traceId, doubtType) {")
        assert "doubt_type: doubtType" in confirm


class TestReflectionSection:
    def test_section_is_rendered_first_and_always(self):
        js = _read(DISCUSS_JS)
        body = _js_function(js, "function buildLandingBodyHtml(tensionItems, anchorItems, reconItem) {")
        assert "reflectionSectionHtml()" in body
        assert body.index("reflectionSectionHtml()") < body.index("今日話した内容を地図に置く")
        # 候補の有無で出し分けない（if の内側に入れない）
        assert "html += reflectionSectionHtml();" in body

    def test_section_copy_states_it_is_private(self):
        section = _js_function(_read(DISCUSS_JS), "function reflectionSectionHtml() {")
        assert "今日の理解を自分の言葉で" in section
        assert "あなたにだけ表示されます" in section

    def test_save_posts_to_reflection_endpoint(self):
        save = _js_function(_read(DISCUSS_JS), "async function saveReflection() {")
        assert "/discuss/reflection" in save
        assert '"POST"' in save

    def test_save_failure_keeps_the_learner_text(self):
        """保存失敗で本人が書いた文章を消さない（P4 の趣旨）。"""
        save = _js_function(_read(DISCUSS_JS), "async function saveReflection() {")
        assert "discuss-landing-reflect-error" in save
        assert "入力はそのまま残しています" in save

    def test_metric_event_is_separate_from_candidate_confirm(self):
        js = _read(DISCUSS_JS)
        assert 'sendDiscussMetric("landing_reflection_saved"' in js
        from core.discuss import observation

        assert "landing_reflection_saved" in observation.METRIC_EVENT_VOCAB
