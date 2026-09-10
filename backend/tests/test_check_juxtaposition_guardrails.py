"""是正 F1（確認問題の合否を AI の確定から外す）の構造的ガードレール。

正本: `docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md` §4 第1波 #1
（出所は `six_lenses_2026-09-10/01_learner.md` 提案2 / `05_ai.md` 提案1）。

固定する不変条項:
  1. `/check` の経路は LLM 出力から合否（`passed`）を読まない・DTO に持たない
  2. 文字数による判定（`>= 40` のようなフォールバック）を作らない
  3. この経路から `record_student_stumble_event`（AI 判定を学習者の属性として
     回答逐語つきで書く経路）を呼ばない
  4. 完了の書き込み（`record_topic_check_pass`）は self-check 経路だけが行う
  5. `core/check_review.py` は FastAPI / LLM を import しない（開発ルール2）
  6. プロンプトが採点を指示せず、判定語彙を明示的に禁止している
  7. 自己確認の語彙は R層と共有し、`verdict_wrong` は完了に使わない
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
)

LEARNING_PY = BACKEND / "api" / "routes" / "learning.py"
CHECK_REVIEW_PY = BACKEND / "core" / "check_review.py"
SCHEMAS_PY = BACKEND / "api" / "schemas.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _route_source(fn_name: str) -> str:
    """`def {fn_name}` から次の `@router` デコレータ直前までを切り出す。"""
    src = _read(LEARNING_PY)
    start = src.index(f"def {fn_name}")
    tail = src[start + 1:]
    rel_end = tail.find("\n@router")
    end = start + 1 + rel_end if rel_end != -1 else len(src)
    return src[start:end]


class TestNoVerdictFromTheLLM:
    def test_check_route_never_reads_a_passed_flag(self):
        assert_source_forbids(
            _route_source("check_topic_understanding"),
            ['parsed.get("passed")', "passed =", "passed=", '"passed"'],
            context="check_topic_understanding",
        )

    def test_check_route_has_no_length_based_fallback(self):
        body = _route_source("check_topic_understanding")
        assert ">= 40" not in body
        assert "len((body.answer" not in body

    def test_response_dto_has_no_passed_field(self):
        from schemas import LearningCheckQuestionResponse

        assert "passed" not in LearningCheckQuestionResponse.model_fields
        for field in ("advisory", "degraded", "statements", "observations", "self_check_required"):
            assert field in LearningCheckQuestionResponse.model_fields

    def test_dto_definition_block_mentions_no_verdict(self):
        src = _read(SCHEMAS_PY)
        block = src[src.index("class LearningCheckObservation"):src.index("class LearningCheckSelfCheckRequest")]
        assert "passed" not in block


class TestNoLearnerVerdictRecords:
    def test_check_route_records_no_stumble_event(self):
        assert "record_student_stumble_event" not in _route_source("check_topic_understanding")

    def test_learning_routes_have_no_stumble_writer(self):
        assert "record_student_stumble_event" not in _read(LEARNING_PY)

    def test_self_check_route_does_not_store_the_learner_verbatim(self):
        body = _route_source("self_check_topic_understanding")
        assert "body.answer" not in body
        assert "record_student_stumble_event" not in body


class TestCompletionIsWrittenOnlyByTheLearner:
    def test_check_route_does_not_write_completion(self):
        assert "record_topic_check_pass(" not in _route_source("check_topic_understanding")

    def test_self_check_route_writes_completion_for_advancing_values_only(self):
        body = _route_source("self_check_topic_understanding")
        assert "record_topic_check_pass(" in body
        assert "check_review.SELF_CHECK_ADVANCING" in body

    def test_record_topic_check_pass_has_exactly_one_call_site_in_learning_routes(self):
        assert _read(LEARNING_PY).count("record_topic_check_pass(") == 1

    def test_invalid_self_check_value_is_422(self):
        body = _route_source("self_check_topic_understanding")
        assert "check_review.SELF_CHECK_VALUES" in body
        assert "status_code=422" in body


class TestCoreModuleIsPure:
    def test_core_does_not_import_fastapi_or_llm(self):
        assert_source_does_not_import(
            _read(CHECK_REVIEW_PY),
            ["fastapi", "sqlalchemy", "core.llm", "openai"],
            context="core/check_review.py",
        )

    def test_core_has_no_sql(self):
        assert_source_forbids(
            _read(CHECK_REVIEW_PY),
            ["SELECT ", "INSERT ", "UPDATE ", "DELETE FROM"],
            context="core/check_review.py",
        )


class TestPromptContract:
    def test_prompt_does_not_ask_for_grading(self):
        body = _route_source("check_topic_understanding")
        assert "採点はしません" in body
        assert "採点する大学教員" not in body
        assert '"passed"' not in body

    def test_prompt_forbids_verdict_and_numeric_wording(self):
        body = _route_source("check_topic_understanding")
        assert "禁止: 合格・不合格・正解・採点という語" in body
        assert "点数・正解率・達成度のような数値" in body

    def test_prompt_keeps_the_closed_world_of_requirements(self):
        body = _route_source("check_topic_understanding")
        assert "リストに無い要素を作らないでください" in body

    def test_prompt_does_not_instruct_the_learner_to_advance(self):
        body = _route_source("check_topic_understanding")
        assert "受講者本人が決めます" in body


class TestUsageAndModelWiringPreserved:
    def test_usage_feature_and_model_override_are_unchanged(self):
        body = _route_source("check_topic_understanding")
        assert 'usage_context("learning:understanding_check"' in body
        assert "get_course_live_llm_models(course_id)" in body
        assert "llm_policy.SCENE_LEARNING_CHAT" in body

    def test_single_llm_call(self):
        body = _route_source("check_topic_understanding")
        # 呼び出しは共通骨格（core/llm_worker/single_shot.py::json_call）経由の1地点だけ。
        # override 経路と既定経路の差は渡す kwargs（model / reasoning_effort）だけで、
        # LLM 呼び出し自体を分岐で複製しない。
        assert body.count("json_call(") == 1
        assert body.count("call=generate_text") == 1
        assert body.count("generate_text(") == 0, "素の LLM 呼び出し地点を増やさない"
        # 修復再呼び出し（repair_prompt）は使わない = 1リクエスト1コールのまま。
        assert "repair_prompt" not in body

    def test_self_check_route_calls_no_llm(self):
        body = _route_source("self_check_topic_understanding")
        assert "generate_text" not in body
        assert "usage_context" not in body
