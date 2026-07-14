"""レクチャー表示ソース／ボタン活性の回帰テスト。

方針転換（トピック教材ベースのレクチャー）:
受講レクチャーの表示は、非レクチャー時の教材表示（``get_topic_material`` =
``topics[].student_material`` 最優先）に揃える。トピックが授業用の教材本文
（student_material）または読み上げ原稿（spoken_script）を持つなら、それを1トピック分の
レクチャー教材として使い（`_lecture_uses_topic_material` が真）、音声は
``topic_lecture_audio_cache`` から解決する。実チャンク教材しか無いトピックだけが
PDF 由来チャンク経路へフォールバックする。

`routes/lecture.py` は fastapi 経由の重い依存チェーンを持つため、対象の純関数
`_lecture_uses_topic_material` はソースから直接 exec して単体テストし、呼び出し側
（get_lecture_sequence / get_topic_audio_status）の配線はソース文字列で検証する。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LECTURE_PY = ROOT / "backend" / "api" / "routes" / "lecture.py"


def _read() -> str:
    return LECTURE_PY.read_text(encoding="utf-8")


def _extract_func(name: str) -> str:
    source = _read()
    start = source.index(f"def {name}")
    end = source.index("\ndef ", start + 10)
    return source[start:end]


def _load_predicate():
    """依存モジュール（fastapi 等）を経由せず、対象の純関数だけを抽出して実行する。

    `_lecture_uses_topic_material` は `_topic_student_material` / `_topic_spoken_script`
    に依存するため、それらも併せて名前空間に注入する。
    """
    ns: dict = {}
    for fn_name in ("_topic_student_material", "_topic_spoken_script", "_lecture_uses_topic_material"):
        exec(_extract_func(fn_name), ns)  # noqa: S102 - テスト専用の純関数抽出
    return ns["_lecture_uses_topic_material"]


class TestLectureUsesTopicMaterial:
    def setup_method(self):
        self.fn = _load_predicate()

    def test_true_when_topic_has_student_material(self):
        topic = {"student_material": {"source_text": "学生向け教材テキスト"}}
        assert self.fn(topic) is True

    def test_true_when_topic_has_content(self):
        # content/summary もトピック教材として扱う（非レクチャー表示と一致させる）。
        assert self.fn({"content": "自動生成された本文..."}) is True
        assert self.fn({"summary": "自動生成された要約"}) is True

    def test_true_when_topic_has_spoken_script(self):
        assert self.fn({"spoken_script": "先生が話す自然文"}) is True

    def test_false_when_topic_has_no_authored_material(self):
        # 授業用教材が無い（＝PDF由来チャンクにフォールバックする）トピック。
        assert self.fn({"material_chunk_ids": ["chunk-1"]}) is False
        assert self.fn({}) is False


class TestLectureSequenceUsesTopicMaterialFirst:
    def test_topic_material_prioritized_over_chunks(self):
        body = _extract_func("get_lecture_sequence")
        # トピック教材を持つならトピック経路を優先する（非レクチャー表示と一致）。
        assert "if _lecture_uses_topic_material(topic_info):" in body
        assert "_build_topic_draft_segment(course_id, topic_id, topic_info, course_data)" in body
        idx_guard = body.index("if _lecture_uses_topic_material(topic_info):")
        idx_build = body.index("_build_topic_draft_segment(course_id, topic_id, topic_info, course_data)")
        idx_use = body.index("if topic_segment:")
        assert idx_guard < idx_build < idx_use
        # 旧「チャンク優先」ゲートが残っていないこと。
        assert "_topic_has_linkable_material" not in body


class TestAudioStatusUsesTopicReadiness:
    def test_gate_uses_topic_material_predicate(self):
        body = _extract_func("get_topic_audio_status")
        assert "if _lecture_uses_topic_material(topic_info):" in body
        assert "_compute_topic_audio_readiness(" in body
        # 旧「チャンク優先」ゲートが残っていないこと。
        assert "_topic_has_linkable_material" not in body
