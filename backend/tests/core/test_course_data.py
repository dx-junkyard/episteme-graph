"""Tier 3-18: ``core/course_data.py`` のユニットテスト。

- 各アクセサの None/非dict/空/フラット/ネスト混在ケース
- ``find_course_topic`` の nested 互換 (id 一致優先 → chapter_index/topic_index フォールバック)
- ``validate_course_data`` が完全形フィクスチャを受理すること
- extra="allow" ラウンドトリップで未知キーが model_dump で保持されること
- FastAPI 非 import（core/ 共通ルール）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.guardrail_helpers import assert_source_does_not_import  # noqa: E402

from core import course_data as cd  # noqa: E402


# ---------------------------------------------------------------------------
# フィクスチャ（tests/test_course_data_isolation.py の COURSE_DATA_A 相当の完全形）
# ---------------------------------------------------------------------------

FULL_COURSE_DATA = {
    "id": "course-aaa",
    "title": "コースA",
    "description": "説明文",
    "chapters": [{"title": "第1章", "status": "locked", "progress_pct": 0}],
    "topics": [
        {
            "id": "topic-A-1",
            "title": "コースAトピック1",
            "chapter_index": 0,
            "status": "locked",
            "prerequisites": [{"name": "前提1", "status": "mastered"}],
            "misconceptions": [{"label": "L", "wrong": "誤", "correct": "正"}],
            "summary": "要約",
            "content": "本文",
            "content_blocks": [{"type": "text", "body": "x"}],
            "learning_objectives": ["目標1"],
            "prerequisite_concepts": ["前提概念1"],
            "blackbox_policy": {},
            "assessment_prompts": ["問い1"],
            "expected_misconceptions": ["誤解1"],
            "linked_component_ids": ["comp-1"],
            "linked_equation_ids": ["eq-1"],
            "linked_figure_ids": ["fig-uuid-1"],
            "source_evidence_ids": ["ev-1"],
            "teaching_takeaways": ["要点1"],
            "material_chunk_ids": ["chunk-1"],
            "source_excerpt": "抜粋",
            "key_concepts": ["概念1"],
            "student_material": {"source_format": "eg-markdown-v1", "source_text": "教材"},
            "spoken_script": "台本",
            "cautions": ["注意1"],
            "check_questions": [{"question": "問題1"}],
            "linked_claim_ids": ["claim-1"],
            "evidence_links": [{"chunk_id": "c1"}],
            "content_source": "course_content_generation",
            "content_confidence": "high",
            "draft_source": "course_content_generation",
            "coverage": {"ratio": 0.5},
            "linked_chunk_ids": ["chunk-1"],
            "atlas_node_id": "node-1",
        }
    ],
    "concepts": [{"name": "概念A1", "status": "future", "children": [], "expanded": False}],
    "sources": [
        {
            "title": "教材A",
            "subtitle": "",
            "license": "",
            "used_section": "",
            "material_id": "mat-A-001",
            "document_id": "doc-A-001",
        }
    ],
    "referenced_sections": [{"source": "s", "section": "1", "title": "t", "note": ""}],
    "cartridge_id": "particle_physics",
    "lecture_studio_settings": {
        "narration_persona": "default",
        "response_persona": "default",
        "lecture_language": "ja",
        "scripts_need_regeneration": False,
    },
    "course_content_status": {"status": "completed", "extra_free_form_key": 123},
    "domain": "素粒子物理学",
    "goal": "到達目標",
    "target_audience": "大学院生",
    "prerequisites": ["前提知識A"],
}


# ---------------------------------------------------------------------------
# course_topics
# ---------------------------------------------------------------------------


class TestCourseTopics:
    def test_none_input(self):
        assert cd.course_topics(None) == []

    def test_non_dict_input(self):
        assert cd.course_topics("not a dict") == []
        assert cd.course_topics(["list", "not", "dict"]) == []

    def test_missing_key(self):
        assert cd.course_topics({}) == []

    def test_non_list_value(self):
        # topics キーはあるが list でない (壊れたデータ) → 空リストへ縮退
        assert cd.course_topics({"topics": {"not": "a list"}}) == []
        assert cd.course_topics({"topics": None}) == []

    def test_flat_topics(self):
        data = {"topics": [{"id": "t1"}, {"id": "t2"}]}
        assert cd.course_topics(data) == [{"id": "t1"}, {"id": "t2"}]

    def test_does_not_include_nested_chapter_topics(self):
        data = {"chapters": [{"topics": [{"id": "nested"}]}], "topics": [{"id": "flat"}]}
        assert cd.course_topics(data) == [{"id": "flat"}]

    def test_filters_non_dict_elements(self):
        """非 dict 要素は除外する（``course_sources`` / ``iter_all_topics`` の章側と同じ）。

        回帰: フラット側だけが素通しで、``None`` / 数値 / 文字列がそのまま
        呼び出し側へ流れ、``topic.get(...)`` を呼ぶ各層で AttributeError になった。
        トピックは位置ではなく topic_id / title で参照するので位置保存は不要。
        """
        data = {"topics": [None, 3, "junk", {"id": "t1"}, [], {"id": "t2"}]}
        assert cd.course_topics(data) == [{"id": "t1"}, {"id": "t2"}]

    def test_declared_return_type_holds_for_broken_data(self):
        data = {"topics": [None, "junk"]}
        assert all(isinstance(t, dict) for t in cd.course_topics(data))


# ---------------------------------------------------------------------------
# iter_all_topics
# ---------------------------------------------------------------------------


class TestIterAllTopics:
    def test_none_input(self):
        assert cd.iter_all_topics(None) == []

    def test_empty(self):
        assert cd.iter_all_topics({}) == []

    def test_flat_only(self):
        data = {"topics": [{"id": "t1"}, {"id": "t2"}]}
        assert cd.iter_all_topics(data) == [{"id": "t1"}, {"id": "t2"}]

    def test_nested_only(self):
        data = {"chapters": [{"topics": [{"id": "n1"}]}, {"topics": [{"id": "n2"}]}]}
        assert cd.iter_all_topics(data) == [{"id": "n1"}, {"id": "n2"}]

    def test_flat_and_nested_mixed(self):
        # 両方見て、ネスト分 → フラット分の順で返す（順序自体は呼び出し側は依存しない設計）
        data = {
            "chapters": [{"topics": [{"id": "n1"}]}],
            "topics": [{"id": "f1"}],
        }
        result = cd.iter_all_topics(data)
        assert {"id": "n1"} in result
        assert {"id": "f1"} in result
        assert len(result) == 2

    def test_flat_non_dict_elements_are_filtered_too(self):
        """章ネスト側とフラット側で非 dict の扱いを揃える。"""
        data = {"chapters": [{"topics": [None, {"id": "nested"}]}], "topics": ["junk", {"id": "flat"}]}
        assert cd.iter_all_topics(data) == [{"id": "nested"}, {"id": "flat"}]

    def test_defensive_against_non_dict_chapter_and_topic(self):
        data = {"chapters": ["not a dict", {"topics": ["not a dict", {"id": "ok"}]}], "topics": None}
        assert cd.iter_all_topics(data) == [{"id": "ok"}]


# ---------------------------------------------------------------------------
# course_chapters
# ---------------------------------------------------------------------------


class TestCourseChapters:
    def test_none_and_non_dict(self):
        assert cd.course_chapters(None) == []
        assert cd.course_chapters("x") == []

    def test_missing_or_non_list(self):
        assert cd.course_chapters({}) == []
        assert cd.course_chapters({"chapters": "not a list"}) == []

    def test_does_not_filter_non_dict_elements(self):
        # chapter_index はこのリストの位置に対応するため、非 dict 要素があっても
        # 除外してはならない（除外すると以降の要素の添字がずれる）。
        data = {"chapters": [{"title": "第1章"}, "not a dict", None]}
        assert cd.course_chapters(data) == [{"title": "第1章"}, "not a dict", None]


# ---------------------------------------------------------------------------
# course_sources / course_source_material_ids
# ---------------------------------------------------------------------------


class TestCourseSources:
    def test_none_and_non_dict(self):
        assert cd.course_sources(None) == []
        assert cd.course_sources("x") == []

    def test_missing_or_non_list(self):
        assert cd.course_sources({}) == []
        assert cd.course_sources({"sources": "not a list"}) == []

    def test_filters_non_dict_elements(self):
        data = {"sources": [{"material_id": "m1"}, "not a dict", None]}
        assert cd.course_sources(data) == [{"material_id": "m1"}]


class TestCourseSourceMaterialIds:
    def test_none_input(self):
        assert cd.course_source_material_ids(None) == []

    def test_empty(self):
        assert cd.course_source_material_ids({"sources": []}) == []

    def test_skips_falsy_material_id(self):
        data = {"sources": [{"material_id": ""}, {"material_id": None}, {"title": "no id key"}]}
        assert cd.course_source_material_ids(data) == []

    def test_order_preserving_no_dedup(self):
        data = {"sources": [{"material_id": "m1"}, {"material_id": "m2"}, {"material_id": "m1"}]}
        # 重複除去はしない（呼び出し側の責務）— 3件のまま返る
        assert cd.course_source_material_ids(data) == ["m1", "m2", "m1"]

    def test_casts_to_str(self):
        data = {"sources": [{"material_id": 123}]}
        assert cd.course_source_material_ids(data) == ["123"]


# ---------------------------------------------------------------------------
# course_cartridge_id / course_title / lecture_studio_settings
# ---------------------------------------------------------------------------


class TestCourseCartridgeId:
    def test_none_and_non_dict(self):
        assert cd.course_cartridge_id(None) == ""
        assert cd.course_cartridge_id(123) == ""

    def test_missing(self):
        assert cd.course_cartridge_id({}) == ""

    def test_present_stripped(self):
        assert cd.course_cartridge_id({"cartridge_id": "  particle_physics  "}) == "particle_physics"


class TestCourseTitle:
    def test_none_and_non_dict_returns_default(self):
        assert cd.course_title(None) == ""
        assert cd.course_title(None, default="fallback") == "fallback"

    def test_missing_returns_default(self):
        assert cd.course_title({}, default="course-id-123") == "course-id-123"

    def test_present(self):
        assert cd.course_title({"title": "コースA"}) == "コースA"

    def test_empty_string_falls_back_to_default(self):
        # "" は falsy なので `data.get("title") or default` は default を返す
        # (元コードの `course_data.get("title") or course_title` パターンと同じ挙動)
        assert cd.course_title({"title": ""}, default="course-id-123") == "course-id-123"


class TestLectureStudioSettings:
    def test_none_and_non_dict(self):
        assert cd.lecture_studio_settings(None) == {}
        assert cd.lecture_studio_settings("x") == {}

    def test_missing(self):
        assert cd.lecture_studio_settings({}) == {}

    def test_non_dict_value(self):
        assert cd.lecture_studio_settings({"lecture_studio_settings": ["not", "dict"]}) == {}

    def test_present(self):
        settings = {"narration_persona": "p1", "lecture_language": "en"}
        assert cd.lecture_studio_settings({"lecture_studio_settings": settings}) == settings


# ---------------------------------------------------------------------------
# find_course_topic
# ---------------------------------------------------------------------------


class TestFindCourseTopic:
    def test_none_input(self):
        assert cd.find_course_topic(None, "t1") is None

    def test_find_by_id(self):
        data = {"topics": [{"id": "t1", "title": "Topic 1"}, {"id": "t2"}]}
        assert cd.find_course_topic(data, "t1") == {"id": "t1", "title": "Topic 1"}

    def test_find_by_id_str_coercion(self):
        # id が int で格納されていても str 比較で一致する（元実装と同じ挙動）
        data = {"topics": [{"id": 1, "title": "Topic 1"}]}
        assert cd.find_course_topic(data, "1") == {"id": 1, "title": "Topic 1"}

    def test_not_found_by_id_falls_back_to_chapter_topic_index(self):
        data = {"topics": [{"id": "other", "chapter_index": 0, "topic_index": 2}]}
        found = cd.find_course_topic(data, "missing-id", chapter_index=0, topic_index=2)
        assert found == {"id": "other", "chapter_index": 0, "topic_index": 2}

    def test_topic_index_fallback_uses_enumerate_index_when_absent(self):
        # topic_index キーが無い topic は enumerate の添字で照合する
        data = {"topics": [{"id": "a", "chapter_index": 0}, {"id": "b", "chapter_index": 0}]}
        found = cd.find_course_topic(data, "missing-id", chapter_index=0, topic_index=1)
        assert found == {"id": "b", "chapter_index": 0}

    def test_no_match_returns_none(self):
        data = {"topics": [{"id": "t1"}]}
        assert cd.find_course_topic(data, "missing") is None

    def test_does_not_search_nested_chapter_topics(self):
        # find_course_topic はフラット topics[] のみを見る（元 lecture_studio 実装と同じ）
        data = {"chapters": [{"topics": [{"id": "nested"}]}], "topics": []}
        assert cd.find_course_topic(data, "nested") is None


# ---------------------------------------------------------------------------
# course_atlas_binding_facts（projector.py からの移設・後方互換）
# ---------------------------------------------------------------------------


class TestCourseAtlasBindingFacts:
    def test_none_and_non_dict(self):
        assert cd.course_atlas_binding_facts(None) == (False, False)

    def test_no_cartridge_no_atlas_node(self):
        assert cd.course_atlas_binding_facts({"topics": [{"id": "t1"}]}) == (False, False)

    def test_cartridge_only(self):
        assert cd.course_atlas_binding_facts({"cartridge_id": "particle_physics"}) == (True, False)

    def test_atlas_node_flat_topics(self):
        data = {"topics": [{"id": "t1", "atlas_node_id": "n1"}]}
        assert cd.course_atlas_binding_facts(data) == (False, True)

    def test_atlas_node_nested_chapter_topics(self):
        data = {"chapters": [{"topics": [{"id": "t1", "atlas_node_id": "n1"}]}]}
        assert cd.course_atlas_binding_facts(data) == (False, True)

    def test_both(self):
        data = {
            "cartridge_id": "particle_physics",
            "topics": [{"id": "t1", "atlas_node_id": "n1"}],
        }
        assert cd.course_atlas_binding_facts(data) == (True, True)

    def test_reexported_from_projector_is_identical_function(self):
        """core/status/projector.py は本モジュールから再エクスポートしているだけであること。"""
        from core.status import projector

        assert projector.course_atlas_binding_facts is cd.course_atlas_binding_facts


# ---------------------------------------------------------------------------
# validate_course_data / CourseData モデル
# ---------------------------------------------------------------------------


class TestValidateCourseData:
    def test_accepts_full_fixture(self):
        course = cd.validate_course_data(FULL_COURSE_DATA)
        assert course.id == "course-aaa"
        assert course.title == "コースA"
        assert len(course.topics) == 1
        assert course.topics[0].id == "topic-A-1"
        assert course.topics[0].atlas_node_id == "node-1"
        # Phase 4 §7.1 (hierarchical_context_explanation_design.md): 図のコース流通。
        assert course.topics[0].linked_figure_ids == ["fig-uuid-1"]
        assert course.sources[0].material_id == "mat-A-001"
        assert course.sources[0].document_id == "doc-A-001"
        assert course.lecture_studio_settings.lecture_language == "ja"
        assert course.domain == "素粒子物理学"

    def test_accepts_empty_dict(self):
        course = cd.validate_course_data({})
        assert course.topics == []
        assert course.sources == []
        assert course.title is None

    def test_topic_linked_figure_ids_defaults_to_empty_list(self):
        """linked_figure_ids が無いトピック（既存コースデータ・移行前）でも
        空リストへ安全に縮退し、validate_course_data を壊さないこと。"""
        course = cd.validate_course_data({"topics": [{"id": "t1", "title": "T1"}]})
        assert course.topics[0].linked_figure_ids == []

    def test_extra_allow_roundtrip_preserves_unknown_keys(self):
        """extra="allow" ラウンドトリップで未知キーが model_dump で保存されること。"""
        data = {
            "title": "コースX",
            "totally_unknown_top_level_key": {"nested": [1, 2, 3]},
            "topics": [
                {
                    "id": "t1",
                    "unknown_topic_key": "should survive",
                }
            ],
            "sources": [{"material_id": "m1", "unknown_source_key": 42}],
        }
        course = cd.validate_course_data(data)
        dumped = course.model_dump()
        assert dumped["totally_unknown_top_level_key"] == {"nested": [1, 2, 3]}
        assert dumped["topics"][0]["unknown_topic_key"] == "should survive"
        assert dumped["sources"][0]["unknown_source_key"] == 42

    def test_nested_chapter_topics_defensive_field_accepted(self):
        """CourseChapter.topics（防御的ネスト形）も extra="allow" 経由で拒否されない。"""
        data = {"chapters": [{"title": "第1章", "topics": [{"id": "nested-1"}]}]}
        course = cd.validate_course_data(data)
        assert course.chapters[0].topics[0].id == "nested-1"


# ---------------------------------------------------------------------------
# core/ 共通ルール: FastAPI を import しない
# ---------------------------------------------------------------------------


def test_course_data_module_does_not_import_fastapi():
    src = Path(cd.__file__).read_text(encoding="utf-8")
    assert_source_does_not_import(src, ["fastapi"], context="core/course_data.py")
