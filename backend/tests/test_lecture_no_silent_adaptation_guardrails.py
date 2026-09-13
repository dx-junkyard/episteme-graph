"""レクチャーの沈黙適応の撤去（是正 F3）のガードレール。

設計根拠: ``docs/architecture/six_lenses_2026-09-10/01_learner.md`` §2 提案1
（統合は ``docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md`` §4 第1波 #3）。
vision §3.6（沈黙適応をしない・UC5）/ §6 原則8（出所の正直さ）/ 原則12（押し付けない）。

構造的に守る不変条項:

1. ``build_lecture_sequence`` は入力チャンクと同数のセグメントを返す
   （``skip`` によるチャンク落としをしない）。
2. ``spoken_text`` を「習得済み」要約文へ置換しない（内容改変ゼロ）。
3. ``get_user_mastered_concepts`` は ``learning_chat_history`` を読まない
   （接触の痕跡＝質問した事実を能力の推定に使わない）。
4. API DTO に省略件数（``skipped_segments`` / ``summary_segments``）が無い。
5. 画面の「短く聴く」トグルは既定 OFF・コース単位 localStorage・
   畳んだ位置に「前に触れた箇所（開く）」の1行を残す（消さずに畳む）。
6. マニュアルが「自動的に短縮・省略」と告知していない。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
CORE_LECTURE = BACKEND / "core" / "lecture.py"
ROUTE_LECTURE = BACKEND / "api" / "routes" / "lecture.py"
SCHEMAS = BACKEND / "api" / "schemas.py"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_forbids,
    extract_function_source,
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_js_function(src: str, name: str) -> str:
    """``function <name>(`` から対応する閉じ ``}`` までを素朴な波括弧カウントで抽出する
    （test_discuss_ui_static.py / test_learning_ui_phase2_static.py と同じ流儀）。"""
    start = src.index("function " + name + "(")
    brace_start = src.index("{", start)
    depth = 0
    for i in range(brace_start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces for JS function {name}")


# ---------------------------------------------------------------------------
# 1/2. サーバは内容を落とさない・書き換えない
# ---------------------------------------------------------------------------


class TestSequenceKeepsEveryChunk:
    @staticmethod
    def _course_data() -> dict:
        return {
            "topics": [{
                "id": "topic-1",
                "title": "応用数学",
                "prerequisites": [
                    {"name": "線形代数", "status": "mastered"},
                    {"name": "微分積分", "status": "mastered"},
                ],
            }],
            "concepts": [{"name": "線形代数", "status": "mastered"}],
        }

    def test_segment_count_equals_chunk_count_for_any_mastery(self):
        """習得済み概念をどう与えてもセグメント数は入力チャンク数と一致する。"""
        from core.lecture import build_lecture_sequence

        rng = random.Random(20260910)
        chunks = []
        for i in range(12):
            # 短い定義文（旧 skip 条件）と長文を混ぜ、境界長も混ぜる。
            if i % 3 == 0:
                text = "線形代数の基本定義です。"
            elif i % 3 == 1:
                text = "線形代数と微分積分の基礎を復習します。" * rng.randint(2, 6)
            else:
                text = "応用数学の新しいトピックの解説。" * rng.randint(1, 40)
            chunks.append({
                "id": f"c{i}", "chunk_index": i, "text": text, "spoken_text": text,
            })

        for mastered in (
            None,
            set(),
            {"線形代数"},
            {"線形代数", "微分積分"},
            {"線形代数", "微分積分", "量子力学"},
        ):
            result = build_lecture_sequence(
                "topic-1", self._course_data(), chunks, mastered_concepts=mastered,
            )
            assert len(result) == len(chunks), f"mastered={mastered}"
            assert [s["chunk_id"] for s in result] == [c["id"] for c in chunks]
            assert all(s["segment_mode"] == "full" for s in result)

    def test_spoken_text_is_never_replaced(self):
        """読み上げは常に元のまま（「習得済み」要約への置換をしない）。"""
        from core.lecture import build_lecture_sequence

        text = "線形代数と微分積分の基礎を復習します。応用分野でも活用されます。" * 4
        chunks = [{"id": "c1", "chunk_index": 0, "text": text, "spoken_text": text}]
        result = build_lecture_sequence(
            "topic-1", self._course_data(), chunks,
            mastered_concepts={"線形代数", "微分積分"},
        )
        assert result[0]["spoken_text"] == text
        assert "習得済み" not in result[0]["spoken_text"]
        assert "要約:" not in result[0]["spoken_text"]

    def test_source_has_no_skip_or_summary_branch(self):
        src = extract_function_source(_read(CORE_LECTURE), "build_lecture_sequence")
        assert_source_forbids(
            src,
            [
                '== "skip"',
                '== "summary"',
                "既に習得済みの内容です",
                "continue",  # チャンクを落とす経路そのものを持たない
            ],
            context="core/lecture.py::build_lecture_sequence",
        )

    def test_previously_touched_is_note_only_bool(self):
        from core.lecture import _is_previously_touched

        out = _is_previously_touched("線形代数の定義。", {"線形代数"}, {"線形代数"})
        assert isinstance(out, bool)


# ---------------------------------------------------------------------------
# 3. 接触の痕跡を能力推定に使わない
# ---------------------------------------------------------------------------


class TestMasteryHasNoChatHistorySource:
    def test_function_does_not_read_chat_history(self):
        src = extract_function_source(_read(CORE_LECTURE), "get_user_mastered_concepts")
        assert_source_forbids(
            src,
            ["FROM learning_chat_history", "studied_topic_ids"],
            context="core/lecture.py::get_user_mastered_concepts",
        )

    def test_lecture_module_does_not_query_chat_history_at_all(self):
        # モジュール全体でも同表を引かない（別関数への移設で戻ることを防ぐ）。
        # 「読まない」と明記した docstring は許す（SQL としての参照だけを禁じる）。
        assert_source_forbids(
            _read(CORE_LECTURE),
            ["FROM learning_chat_history", "studied_topic_ids"],
            context="core/lecture.py",
        )


# ---------------------------------------------------------------------------
# 4. DTO に省略件数が無い
# ---------------------------------------------------------------------------


class TestDtoHasNoOmissionCounts:
    def test_schema_fields(self):
        from schemas import LectureSegment, LectureSequenceResponse

        resp_fields = set(LectureSequenceResponse.model_fields.keys())
        assert "skipped_segments" not in resp_fields
        assert "summary_segments" not in resp_fields
        assert "previously_touched" in LectureSegment.model_fields

    def test_route_does_not_compute_counts(self):
        assert_source_forbids(
            _read(ROUTE_LECTURE),
            ["skipped_segments", "summary_segments", "skipped_count", "summary_count"],
            context="api/routes/lecture.py",
        )

    def test_schemas_module_has_no_count_fields(self):
        # フィールド宣言としての再侵入を禁じる（撤去を明記した docstring は許す）。
        assert_source_forbids(
            _read(SCHEMAS),
            ["skipped_segments:", "summary_segments:"],
            context="api/schemas.py",
        )


# ---------------------------------------------------------------------------
# 5. 画面の「短く聴く」トグル（本人の明示操作・既定 OFF・消さずに畳む）
# ---------------------------------------------------------------------------


class TestCondensedToggleUiContract:
    def test_button_exists_with_anchor_and_default_off(self):
        html = _read(INDEX_HTML)
        assert 'id="lecture-condensed"' in html
        assert 'data-ui-anchor="material.lecture-condensed"' in html
        # 既定 OFF は aria-pressed="false" で表現する。
        idx = html.index('id="lecture-condensed"')
        tag = html[html.rindex("<button", 0, idx): html.index(">", idx) + 1]
        assert 'aria-pressed="false"' in tag
        assert "短く聴く" in html

    def test_anchor_is_registered_and_mapped(self):
        from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS, UI_ANCHORS

        assert "material.lecture-condensed" in KNOWN_UI_ANCHOR_IDS
        assert UI_ANCHORS["material.lecture-condensed"] == (
            "student/02-student.md#lecture-condensed"
        )

    def test_storage_key_is_course_scoped_and_default_off(self):
        js = _read(APP_JS)
        assert '"eg_lecture_condensed:"' in js
        src = _extract_js_function(js, "isLectureCondensedOn")
        # 明示的に "1" のときだけ ON（キー未設定は OFF）。
        assert '=== "1"' in src
        assert "return false" in src

    def test_no_server_side_learner_preference(self):
        """学習者設定をサーバに保存しない（localStorage のみ）。"""
        js = _read(APP_JS)
        src = _extract_js_function(js, "toggleLectureCondensed")
        assert_source_forbids(
            src, ["apiFetch", "fetch("], context="app.js::toggleLectureCondensed",
        )

    def test_folded_slide_keeps_open_row(self):
        js = _read(APP_JS)
        # 畳んだ位置に残す1行の文言（消さずに畳む）。
        assert "前に触れた箇所（開く）" in js
        assert 'id="lecture-folded-open"' in js
        assert "openFoldedLectureSlide" in js

    def test_fold_requires_toggle_and_server_note(self):
        js = _read(APP_JS)
        src = _extract_js_function(js, "isLectureSlideFolded")
        assert "isLectureCondensedOn()" in src
        assert "previously_touched" in src
        assert "openedFoldedSlides" in src

    def test_folded_slide_skips_audio_via_auto_advance(self):
        js = _read(APP_JS)
        src = _extract_js_function(js, "startPlayback")
        assert "isLectureSlideFolded" in src
        assert "autoAdvance()" in src


# ---------------------------------------------------------------------------
# 6. マニュアルは実挙動を告知する
# ---------------------------------------------------------------------------


class TestManualTellsTheTruth:
    def test_no_auto_omission_claim(self):
        md = _read(MANUAL)
        assert "自動的に短縮・省略" not in md

    def test_condensed_section_exists(self):
        md = _read(MANUAL)
        assert "{#lecture-condensed}" in md
        assert "短く聴く" in md
        assert "前に触れた箇所（開く）" in md
