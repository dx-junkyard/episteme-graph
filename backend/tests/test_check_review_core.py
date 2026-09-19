"""`core/check_review.py`（確認問題の並置・非LLM・純関数）のテスト。

是正 F1（`docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md` §4 第1波 #1、
出所は `six_lenses_2026-09-10/01_learner.md` 提案2 / `05_ai.md` 提案1）:
確認問題の合否を AI の確定から外し、要件との並置 + 本人の自己確認に分解する。

ここで固定するのは
  - 語彙に合否が無いこと（status は covered / not_mentioned / unclear の3値）
  - 閉世界照合（出題の answer_requirements に無い要件を作らない）
  - 判定・採点語彙の混入を落とすこと
  - degraded が判定を生まないこと
  - 事実文が推量形で、点数・順位・煽り語彙を含まないこと
"""

from __future__ import annotations

from core import check_review


class TestVocabulary:
    def test_statuses_have_no_verdict_value(self):
        assert check_review.OBSERVATION_STATUSES == ("covered", "not_mentioned", "unclear")
        for name in dir(check_review):
            assert "passed" not in name.lower()

    def test_self_check_values_are_shared_with_reconstruction(self):
        from core.reconstruction.schema import SELF_CHECK_VALUES

        assert check_review.SELF_CHECK_VALUES is SELF_CHECK_VALUES
        assert check_review.SELF_CHECK_VALUES == ("agreed", "disagreed", "verdict_wrong")

    def test_only_two_values_advance_completion(self):
        assert check_review.SELF_CHECK_ADVANCING == ("agreed", "disagreed")
        assert "verdict_wrong" not in check_review.SELF_CHECK_ADVANCING

    def test_self_check_question_matches_reconstruction_wording(self):
        assert check_review.SELF_CHECK_QUESTION == "あなたの見立てはどうでしたか？"
        assert set(check_review.SELF_CHECK_LABELS) == set(check_review.SELF_CHECK_VALUES)


class TestSanitizeStatement:
    def test_plain_statement_is_kept(self):
        assert check_review.sanitize_statement("  …への言及は見当たらないようです。  ") == (
            "…への言及は見当たらないようです。"
        )

    def test_verdict_wording_is_dropped(self):
        for text in ("合格です", "不合格でした", "正解しています", "採点すると十分です", "80点"):
            assert check_review.sanitize_statement(text) == ""

    def test_empty_and_none_are_empty(self):
        assert check_review.sanitize_statement(None) == ""
        assert check_review.sanitize_statement("   ") == ""


class TestNormalizeObservations:
    REQS = ["定義を述べる", "関係を述べる"]

    def test_non_list_is_empty(self):
        assert check_review.normalize_observations(None, self.REQS) == []
        assert check_review.normalize_observations({"a": 1}, self.REQS) == []

    def test_requirement_outside_closed_world_is_emptied_but_statement_kept(self):
        out = check_review.normalize_observations(
            [{"requirement": "捏造された要件", "status": "covered", "statement": "触れているようです"}],
            self.REQS,
        )
        assert out == [
            {"requirement": "", "status": "covered", "statement": "触れているようです"},
        ]

    def test_requirement_matched_case_insensitively(self):
        out = check_review.normalize_observations(
            [{"requirement": " 定義を述べる ", "status": "not_mentioned", "statement": "見当たらないようです"}],
            self.REQS,
        )
        assert out[0]["requirement"] == "定義を述べる"

    def test_unknown_status_falls_back_to_unclear_not_to_a_verdict(self):
        out = check_review.normalize_observations(
            [{"requirement": "定義を述べる", "status": "failed", "statement": "…"}],
            self.REQS,
        )
        assert out[0]["status"] == "unclear"

    def test_statement_with_verdict_wording_is_dropped_but_item_survives(self):
        out = check_review.normalize_observations(
            [{"requirement": "定義を述べる", "status": "covered", "statement": "正解です"}],
            self.REQS,
        )
        assert out == [{"requirement": "定義を述べる", "status": "covered", "statement": ""}]

    def test_empty_item_is_skipped(self):
        out = check_review.normalize_observations(
            [{"requirement": "リスト外", "status": "covered", "statement": "採点済み"}],
            self.REQS,
        )
        assert out == []

    def test_limit_is_three_by_default(self):
        raw = [
            {"requirement": "定義を述べる", "status": "covered", "statement": f"文{i}"}
            for i in range(10)
        ]
        assert len(check_review.normalize_observations(raw, self.REQS)) == check_review.MAX_OBSERVATIONS
        assert check_review.MAX_OBSERVATIONS == 3

    def test_free_requirements_allowed_when_question_has_none(self):
        out = check_review.normalize_observations(
            [{"requirement": "自由記述の観点", "status": "covered", "statement": "…"}], [],
        )
        assert out[0]["requirement"] == "自由記述の観点"

    def test_parsed_observations_reads_the_observations_key(self):
        out = check_review.parsed_observations(
            {"observations": [{"requirement": "定義を述べる", "status": "covered"}]}, self.REQS,
        )
        assert out[0]["requirement"] == "定義を述べる"
        assert check_review.parsed_observations({}, self.REQS) == []
        assert check_review.parsed_observations(None, self.REQS) == []


class TestSplitObservations:
    def test_split_keeps_order_and_ignores_unlabeled(self):
        covered, not_mentioned = check_review.split_observations([
            {"requirement": "A", "status": "covered", "statement": ""},
            {"requirement": "B", "status": "not_mentioned", "statement": ""},
            {"requirement": "", "status": "covered", "statement": "要件名なし"},
            {"requirement": "C", "status": "unclear", "statement": ""},
        ])
        assert covered == ["A"]
        assert not_mentioned == ["B"]


class TestBuildStatements:
    FORBIDDEN = (
        "合格", "不合格", "正解", "採点", "点数", "正解率", "達成率", "スコア", "ランキング",
        "おめでとう", "🎉",
    )

    def test_degraded_returns_only_the_fixed_line(self):
        statements = check_review.build_statements("回答本文", [], degraded=True)
        assert statements == [check_review.DEGRADED_STATEMENT]
        assert "見比べてください" in check_review.DEGRADED_STATEMENT

    def test_learner_text_is_quoted_first(self):
        statements = check_review.build_statements("私の答え", [], degraded=False)
        assert statements[0] == "あなたは「私の答え」と述べました。"

    def test_long_answer_is_excerpted(self):
        statements = check_review.build_statements("あ" * 500, [], degraded=False)
        assert statements[0].endswith("…」と述べました。")

    def test_covered_and_not_mentioned_lines_are_hypothetical(self):
        statements = check_review.build_statements("答え", [
            {"requirement": "定義", "status": "covered", "statement": ""},
            {"requirement": "関係", "status": "not_mentioned", "statement": ""},
        ])
        joined = "\n".join(statements)
        assert "「定義」への言及が、あなたの回答にあるようです。" in joined
        assert "「関係」への言及は、あなたの回答には見当たらないようです。" in joined

    def test_no_observations_falls_back_to_side_by_side_invitation(self):
        statements = check_review.build_statements("答え", [])
        assert any("並べて" in s for s in statements)

    def test_last_line_hands_the_decision_to_the_learner(self):
        statements = check_review.build_statements("答え", [])
        assert statements[-1] == "解答例と解説を読んだうえで、次に進むかどうかはご自身で決めてください。"

    def test_no_forbidden_wording_in_any_branch(self):
        cases = [
            check_review.build_statements("答え", [], degraded=True),
            check_review.build_statements("答え", []),
            check_review.build_statements("答え", [
                {"requirement": "定義", "status": "covered", "statement": ""},
                {"requirement": "関係", "status": "not_mentioned", "statement": ""},
            ]),
        ]
        for statements in cases:
            joined = "\n".join(statements)
            hits = [w for w in self.FORBIDDEN if w in joined]
            assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_pure_function_does_not_mutate_input(self):
        observations = [{"requirement": "定義", "status": "covered", "statement": "x"}]
        snapshot = [dict(o) for o in observations]
        check_review.build_statements("答え", observations)
        assert observations == snapshot
