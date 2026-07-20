"""レクチャー表示ソース／ボタン活性の回帰テスト。

方針転換（トピック教材ベースのレクチャー）:
受講レクチャーの表示は、非レクチャー時の教材表示（``get_topic_material`` =
``topics[].student_material`` 最優先）に揃える。トピックが授業用の教材本文
（student_material）または読み上げ原稿（spoken_script）を持つなら、それを1トピック分の
レクチャー教材として使い（`lecture_uses_topic_material` が真）、音声は
``topic_lecture_audio_cache`` から解決する。実チャンク教材しか無いトピックだけが
PDF 由来チャンク経路へフォールバックする。

N18（2026-07）: 述語 `lecture_uses_topic_material` の正本は routes/lecture.py から
core/lecture.py へ移設された（状態投影 core/status/projector.py と readiness 判定を
共有するため。core から routes を import しない）。述語は core から直接 import して
単体テストし、呼び出し側（get_lecture_sequence / get_topic_audio_status）の配線は
ソース文字列で検証する。
"""

from __future__ import annotations

from pathlib import Path

from core.lecture import lecture_uses_topic_material

ROOT = Path(__file__).resolve().parents[2]
LECTURE_PY = ROOT / "backend" / "api" / "routes" / "lecture.py"
CORE_LECTURE_PY = ROOT / "backend" / "core" / "lecture.py"


def _read() -> str:
    return LECTURE_PY.read_text(encoding="utf-8")


def _extract_func(name: str) -> str:
    source = _read()
    start = source.index(f"def {name}")
    end = source.index("\ndef ", start + 10)
    return source[start:end]


class TestLectureUsesTopicMaterial:
    def setup_method(self):
        self.fn = lecture_uses_topic_material

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


class TestPredicateCanonicalLocation:
    """述語・スライド分割の正本が core/lecture.py にあり、routes 側に再実装が無いこと。"""

    def test_core_defines_predicate_and_builder(self):
        core_src = CORE_LECTURE_PY.read_text(encoding="utf-8")
        assert "def lecture_uses_topic_material(" in core_src
        assert "def build_topic_slides(" in core_src
        assert "def compute_topic_audio_readiness(" in core_src
        assert "def compute_course_audio_readiness(" in core_src

    def test_routes_does_not_redefine_predicate(self):
        src = _read()
        assert "def _lecture_uses_topic_material(" not in src
        assert "def _build_topic_slides(" not in src
        assert "def _compute_topic_audio_readiness(" not in src

    def test_core_does_not_import_routes(self):
        core_src = CORE_LECTURE_PY.read_text(encoding="utf-8")
        assert "from routes" not in core_src
        assert "import routes" not in core_src


class TestLectureSequenceUsesTopicMaterialFirst:
    def test_topic_material_prioritized_over_chunks(self):
        body = _extract_func("get_lecture_sequence")
        # トピック教材を持つならトピック経路を優先する（非レクチャー表示と一致）。
        assert "if lecture_uses_topic_material(topic_info):" in body
        assert "_build_topic_draft_segment(course_id, topic_id, topic_info, course_data)" in body
        idx_guard = body.index("if lecture_uses_topic_material(topic_info):")
        idx_build = body.index("_build_topic_draft_segment(course_id, topic_id, topic_info, course_data)")
        idx_use = body.index("if topic_segment:")
        assert idx_guard < idx_build < idx_use
        # 旧「チャンク優先」ゲートが残っていないこと。
        assert "_topic_has_linkable_material" not in body


class TestAudioStatusUsesTopicReadiness:
    def test_gate_uses_topic_material_predicate(self):
        body = _extract_func("get_topic_audio_status")
        assert "if lecture_uses_topic_material(topic_info):" in body
        # readiness 判定は core の正本（compute_topic_audio_readiness）を呼ぶこと。
        assert "compute_topic_audio_readiness(" in body
        # 旧「チャンク優先」ゲートが残っていないこと。
        assert "_topic_has_linkable_material" not in body
