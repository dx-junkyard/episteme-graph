"""レクチャーボタン有効化バグの回帰テスト。

不具合: `_topic_student_material`/`_topic_spoken_script` は content/summary に
フォールバックするため、コースビルダーが生成するほぼ全トピックで真になる。これを
「ドラフト専用トピック（音声キャッシュ不可）」の判定にそのまま使うと、実チャンク教材
（`lecture_audio_cache` にキャッシュ可能）を持つトピックまで `get_topic_audio_status`
が has_audio=False を返し、管理画面で音声を生成してもレクチャーボタンが有効化されない。

`routes/lecture.py` は fastapi/neo4j 経由の重い依存チェーンを持つため（services.py
経由で neo4j 等）、対象の純関数 `_topic_has_linkable_material` はソースから直接
exec して単体テストする。呼び出し側2箇所（get_topic_audio_status /
get_lecture_sequence）は既存の `test_endorsement_sharing.py` と同様にソース文字列で
配線を検証する。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LECTURE_PY = ROOT / "backend" / "api" / "routes" / "lecture.py"


def _read() -> str:
    return LECTURE_PY.read_text(encoding="utf-8")


def _load_topic_has_linkable_material():
    """依存モジュール（fastapi 等）を経由せず、対象関数だけをソースから抽出して実行する。"""
    source = _read()
    start = source.index("def _topic_has_linkable_material")
    end = source.index("\ndef ", start + 10)
    func_src = source[start:end]
    ns: dict = {}
    exec(func_src, ns)  # noqa: S102 - テスト専用の純関数抽出
    return ns["_topic_has_linkable_material"]


class TestTopicHasLinkableMaterial:
    def setup_method(self):
        self.fn = _load_topic_has_linkable_material()

    def test_true_when_topic_has_material_chunk_ids(self):
        topic = {"material_chunk_ids": ["chunk-1"]}
        assert self.fn(topic, {"sources": []}) is True

    def test_true_when_course_has_material_sources(self):
        topic = {"material_chunk_ids": []}
        course_data = {"sources": [{"material_id": "mat-1"}]}
        assert self.fn(topic, course_data) is True

    def test_false_when_no_chunks_and_no_course_material(self):
        topic = {"material_chunk_ids": []}
        assert self.fn(topic, {"sources": []}) is False

    def test_content_and_summary_alone_do_not_count_as_linkable(self):
        """バグの核心: content/summary はほぼ全トピックに設定されるが、それだけでは
        「実チャンク教材あり」と判定してはならない。"""
        topic = {
            "material_chunk_ids": [],
            "content": "自動生成された本文...",
            "summary": "自動生成された要約",
            "student_material": {"source_text": "学生向け教材テキスト"},
        }
        assert self.fn(topic, {"sources": []}) is False

    def test_sources_without_material_id_do_not_count(self):
        topic = {"material_chunk_ids": []}
        course_data = {"sources": [{"title": "参考文献のみ"}]}
        assert self.fn(topic, course_data) is False

    def test_missing_sources_key_is_safe(self):
        assert self.fn({}, {}) is False

    def test_non_dict_course_data_is_safe(self):
        assert self.fn({"material_chunk_ids": ["c1"]}, None) is True
        assert self.fn({}, None) is False


class TestAudioStatusGateWiring:
    def test_gate_checks_linkable_material_before_draft_fallback(self):
        source = _read()
        body = source.split("def get_topic_audio_status")[1].split("\ndef ")[0]
        assert "if not _topic_has_linkable_material(topic_info, course_data) and (" in body
        assert "_topic_student_material(topic_info) or _topic_spoken_script(topic_info)" in body

    def test_docstring_still_reflects_the_narrowed_condition(self):
        source = _read()
        body = source.split("def get_topic_audio_status")[1].split("\ndef ")[0]
        assert "実チャンク教材" in body


class TestLectureSequenceGateWiring:
    def test_draft_segment_only_built_without_linkable_material(self):
        source = _read()
        body = source.split("def get_lecture_sequence")[1].split("\ndef ")[0]
        assert "topic_segment = None" in body
        assert "if not _topic_has_linkable_material(topic_info, course_data):" in body
        idx_guard = body.index("if not _topic_has_linkable_material")
        idx_build = body.index("_build_topic_draft_segment(topic_id, topic_info, course_data)")
        idx_use = body.index("if topic_segment:")
        # ガード → ドラフト構築 → 利用、の順で並んでいること
        assert idx_guard < idx_build < idx_use
