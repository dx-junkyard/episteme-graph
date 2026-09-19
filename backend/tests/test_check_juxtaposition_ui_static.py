"""確認問題の画面が「並置 → 開示 → 本人の自己確認」であることの静的ガードレール。

是正 F1（`docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md` §4 第1波 #1、
出所は `six_lenses_2026-09-10/01_learner.md` 提案2 / `05_ai.md` 提案1）。
本ファイルは旧 `test_check_pass_feedback_ui_static.py`（合格時も講評を出す、という
合否前提の契約）を置き換えたもの。「講評を捨てない」という受け入れ条件は
「並置と開示を捨てない」に読み替えて維持している。

受け入れ条件との対応:
1. 応答の合否分岐が無い — `/check` の応答は必ず並置として提示され、自動遷移しない
2. 並置の一等地は事実文。要件・観点・解答例・解説を落とさず、解答例と解説は畳む
3. 先へ進むかどうかは本人の3択（R層 SELF-CHECK と同型の問いかけ）だけが決める。
   `verdict_wrong` は進行を止めない
4. 並置結果はトピック単位に保持し、議論から開き直したら再提示する
5. 開示後の深掘りは壁打ち（check_scaffold）にしない
6. 合否・採点・点数の語彙を UI 文言に出さない（既存の不変条項の拡張）
7. マニュアルに「AI は合否をつけない・進むかは自分で選ぶ」が書かれている
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from api.schemas import LearningCheckQuestionResponse  # noqa: E402

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"
LEARNING_PY = ROOT / "backend" / "api" / "routes" / "learning.py"

REVIEW_STATE_SIG = "function applyCheckReviewState(review) {"
SELFCHECK_HTML_SIG = "function checkSelfCheckHtml(review) {"
SELFCHECK_SUBMIT_SIG = "async function submitCheckSelfCheck(value) {"
SUBMIT_SIG = "async function submitCheckAnswer() {"
OPEN_MODAL_SIG = "function openCheckModal() {"
DISCUSS_HANDLER_SIG = (
    'document.getElementById("check-discuss").addEventListener("click", async function () {'
)

# 数値・進捗・ゲーミフィケーション語彙（test_learner_ux_static.py と同一集合）+ 祝祭演出
# + 是正 F1 で追加した合否・採点語彙。
FORBIDDEN_WORDS = (
    "踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア",
    "おめでとう", "🎉", "祝", "レベルアップ", "正解率", "点数", "何点",
    "合格", "不合格", "正解", "採点",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_body(src: str, signature: str) -> str:
    """`signature` から対応する閉じ `}` までを波括弧カウントで抽出する。"""
    start = src.index(signature)
    brace_start = src.index("{", start)
    depth = 0
    i = brace_start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unterminated body for: " + signature)


def _python_def_source(src: str, signature: str) -> str:
    start = src.index(signature)
    rest = src[start + len(signature):]
    ends = [rest.find(marker) for marker in ("\ndef ", "\nclass ", "\n@")]
    ends = [e for e in ends if e != -1]
    return src[start:start + len(signature) + (min(ends) if ends else len(rest))]


def _manual_section() -> str:
    md = _read(STUDENT_MANUAL)
    start = md.index("{#check-options}")
    rel_end = md[start:].find("\n### ")
    return md[start:start + rel_end] if rel_end != -1 else md[start:]


class TestSubmitHasNoVerdictBranch:
    """応答に合否が無いので、合否分岐も自動遷移も存在しない。"""

    def test_no_passed_branch_anywhere_in_app_js(self):
        src = _read(APP_JS)
        assert "data.passed" not in src
        assert "lastCheckPass" not in src
        assert "applyCheckPassState" not in src

    def test_submit_always_renders_the_juxtaposition(self):
        body = _extract_body(_read(APP_JS), SUBMIT_SIG)
        assert "applyCheckReviewState(state.lastCheckReview)" in body
        # 応答受信の分岐で selectTopic（自動遷移）へ落ちる経路は無い。
        response_block = body[body.index("var data = await res.json();"):]
        assert "selectTopic" not in response_block

    def test_submit_stores_all_projection_fields(self):
        body = _extract_body(_read(APP_JS), SUBMIT_SIG)
        for key in ("statements", "observations", "answer_requirements", "model_answer",
                    "explanation", "degraded"):
            assert key in body, f"{key} を捨てている"
        assert "topicId: state.currentTopicId," in body


class TestReviewRendering:
    """並置の一等地は事実文。要件・観点は落とさず、解答例・解説は畳む。"""

    def test_advisory_note_is_shown_first(self):
        body = _extract_body(_read(APP_JS), REVIEW_STATE_SIG)
        assert "CHECK_ADVISORY_NOTE" in body
        note = _read(APP_JS)
        assert 'CHECK_ADVISORY_NOTE = "AI が並べた観点です（合否の判定ではありません）。"' in note

    def test_statements_and_observations_are_rendered(self):
        body = _extract_body(_read(APP_JS), REVIEW_STATE_SIG)
        assert "review.statements" in body
        assert "checkObservationsHtml(review.observations)" in body
        assert "review.answer_requirements" in body

    def test_model_answer_and_explanation_are_collapsed(self):
        body = _extract_body(_read(APP_JS), REVIEW_STATE_SIG)
        assert '<details class="check-reveal">' in body
        assert "解答例と解説を読む" in body

    def test_submitted_answer_stays_editable_for_revision(self):
        """再回答（REVISE）できるよう readOnly にしない。"""
        body = _extract_body(_read(APP_JS), REVIEW_STATE_SIG)
        assert "answerEl.value = review.answer" in body
        assert "answerEl.readOnly = false;" in body

    def test_primary_button_is_revise_until_self_check(self):
        body = _extract_body(_read(APP_JS), REVIEW_STATE_SIG)
        assert '"もう一度答える"' in body
        assert 'submitBtn.removeAttribute("data-advance");' in body

    def test_primary_button_advances_only_after_completion(self):
        body = _extract_body(_read(APP_JS), REVIEW_STATE_SIG)
        assert "if (review.topicCompleted) {" in body
        assert 'submitBtn.setAttribute("data-advance", "true");' in body
        assert '"次へ進む"' in body
        assert '"確認を終える"' in body

    def test_skip_exit_removed_only_after_completion(self):
        """確認を終えたあとに「今回は確認せず進む」は事実でなくなる。"""
        body = _extract_body(_read(APP_JS), REVIEW_STATE_SIG)
        skip_block = body[body.index('document.getElementById("check-skip")') - 200:]
        assert "review.topicCompleted" in skip_block
        assert "skipBtn.remove();" in body

    def test_observation_status_labels_are_hypothetical(self):
        src = _read(APP_JS)
        assert 'covered: "触れられているようです"' in src
        assert 'not_mentioned: "見当たらないようです"' in src
        assert 'unclear: "読み取れませんでした"' in src


class TestSelfCheckIsTheOnlyGate:
    """先へ進む確定は本人の3択だけ（R層 SELF-CHECK と同型）。"""

    def test_three_choices_match_the_reconstruction_wording(self):
        body = _extract_body(_read(APP_JS), SELFCHECK_HTML_SIG)
        assert "あなたの見立てはどうでしたか？" in body
        assert 'data-sc="agreed">合っていた' in body
        assert 'data-sc="disagreed">違っていた' in body
        assert 'data-sc="verdict_wrong">観点がおかしい' in body

    def test_records_are_not_presented_as_correctness(self):
        body = _extract_body(_read(APP_JS), SELFCHECK_HTML_SIG)
        assert "正誤の記録ではありません" in body

    def test_verdict_wrong_keeps_the_three_choices_available(self):
        body = _extract_body(_read(APP_JS), SELFCHECK_HTML_SIG)
        assert 'review.selfCheck === "verdict_wrong"' in body
        assert "いつでも選べます" in body

    def test_self_check_posts_to_the_dedicated_route(self):
        body = _extract_body(_read(APP_JS), SELFCHECK_SUBMIT_SIG)
        assert '"/check/self-check"' in body
        assert 'JSON.stringify({ self_check: value })' in body

    def test_self_check_updates_server_completion_state(self):
        body = _extract_body(_read(APP_JS), SELFCHECK_SUBMIT_SIG)
        assert "state.lastCheckCourseCompleted = !!data.course_completed" in body
        assert "review.topicCompleted = !!data.topic_completed" in body

    def test_self_check_failure_reenables_the_buttons(self):
        body = _extract_body(_read(APP_JS), SELFCHECK_SUBMIT_SIG)
        assert "catch (err)" in body
        assert "b.disabled = false" in body


class TestSelfCheckVocabularyMirror:
    """3択の文言はサーバ側の正本（core/check_review.py）と逐語で一致させる。

    JS 側に表を持つのは既定（フロントは core を import できない）だが、黙って
    分裂しないようミラーを固定する（doubt / element_vocab のミラーテストと同型）。
    """

    def test_question_and_labels_match_the_server_vocabulary(self):
        from core import check_review

        body = _extract_body(_read(APP_JS), SELFCHECK_HTML_SIG)
        assert check_review.SELF_CHECK_QUESTION in body
        for value, label in check_review.SELF_CHECK_LABELS.items():
            assert f'data-sc="{value}">{label}' in body

    def test_degraded_statement_matches_the_server_fixed_line(self):
        from core import check_review

        # マニュアルは行折り返しがあるので改行・行頭空白を畳んで比較する。
        section = "".join(line.strip() for line in _manual_section().splitlines())
        assert check_review.DEGRADED_STATEMENT in section

    def test_observation_status_labels_cover_every_server_status(self):
        from core import check_review

        src = _read(APP_JS)
        table = src[src.index("CHECK_SELFCHECK_STATUS_LABELS = {"):]
        table = table[:table.index("};")]
        for status in check_review.OBSERVATION_STATUSES:
            assert f"{status}:" in table


class TestReviewLifetime:
    """並置はトピック単位。開き直しでは復元し、トピック切替では破棄する。"""

    def test_state_declares_last_check_review_default_null(self):
        assert "lastCheckReview: null," in _read(APP_JS)

    def test_open_check_modal_restores_review_for_same_topic(self):
        body = _extract_body(_read(APP_JS), OPEN_MODAL_SIG)
        assert "state.lastCheckReview.topicId === state.currentTopicId" in body
        assert "applyCheckReviewState(state.lastCheckReview)" in body

    def test_open_check_modal_drops_other_topic_review(self):
        body = _extract_body(_read(APP_JS), OPEN_MODAL_SIG)
        assert "state.lastCheckReview.topicId !== state.currentTopicId" in body
        assert "state.lastCheckReview = null;" in body

    def test_review_reset_on_topic_switch(self):
        body = _extract_body(_read(APP_JS), "async function selectTopic(")
        assert "state.lastCheckReview = null;" in body


class TestDeepDiveAfterReveal:
    """解答例が開示されたあとの深掘りは壁打ちにしない（拘束の目的が失われている）。"""

    def test_discuss_handler_branches_on_reveal_not_on_a_verdict(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert "var revealedCheck = !!(review && review.topicId === state.currentTopicId);" in body
        assert "if (revealedCheck) {" in body

    def test_revealed_path_sends_without_scaffold(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        revealed_block = body[body.index("if (revealedCheck) {"):body.index("} else {")]
        assert "state.checkScaffoldActive = false;" in revealed_block
        assert "check_scaffold" not in revealed_block
        # 未提出側は壁打ちのまま（既存契約）。
        assert "check_scaffold: true" in body

    def test_carried_material_keeps_the_statements(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert '"並べて見えたこと: "' in body
        assert "review.statements.join" in body

    def test_return_chip_still_offered_after_discussion(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert body.index("await sendMessage") < body.index("showCheckReturnChip()")


class TestNoVerdictVocabularyInTheUi:
    def test_check_ui_bodies_have_no_forbidden_words(self):
        js = _read(APP_JS)
        bodies = "\n".join(
            _extract_body(js, sig) for sig in (
                REVIEW_STATE_SIG, SELFCHECK_HTML_SIG, SELFCHECK_SUBMIT_SIG,
                SUBMIT_SIG, OPEN_MODAL_SIG, DISCUSS_HANDLER_SIG,
                "function showCourseCompletionCard(completedTopic, courseCompleted, opts) {",
            )
        )
        hits = [w for w in FORBIDDEN_WORDS if w in bodies]
        assert not hits, f"禁止語彙が見つかりました: {hits}"


class TestServerContract:
    def test_response_schema_carries_the_projection_fields(self):
        for field in ("statements", "observations", "covered", "not_mentioned",
                      "model_answer", "explanation", "answer_requirements",
                      "advisory", "degraded", "self_check_required"):
            assert field in LearningCheckQuestionResponse.model_fields
        assert "passed" not in LearningCheckQuestionResponse.model_fields

    def test_route_builds_the_projection_outside_any_verdict_branch(self):
        body = _python_def_source(_read(LEARNING_PY), "def check_topic_understanding(")
        assert "\n    observations = check_review.parsed_observations(" in body
        assert "\n    statements = check_review.build_statements(" in body
        assert "        statements=statements," in body


class TestCssHooks:
    def test_juxtaposition_and_selfcheck_classes_exist(self):
        css = _read(STYLES_CSS)
        for cls in (".check-feedback.advisory", ".check-reveal", ".check-advisory",
                    ".check-statement", ".check-observations", ".check-selfcheck",
                    ".check-sc"):
            assert cls in css, f"styles.css に {cls} がありません"

    def test_pass_class_is_gone(self):
        """合否の色分け（.check-feedback.pass）は残さない。"""
        assert ".check-feedback.pass" not in _read(STYLES_CSS)


class TestDocumentation:
    def test_manual_states_the_ai_gives_no_verdict(self):
        section = _manual_section()
        assert "AI は合否をつけません" in section
        assert "あなたの見立てはどうでしたか？" in section
        assert "もう一度答える" in section
        assert "解答例と解説を読む" in section

    def test_manual_describes_the_three_choices(self):
        section = _manual_section()
        for label in ("合っていた", "違っていた", "観点がおかしい"):
            assert label in section
        assert "正誤の記録ではなく" in section
        assert "先へ進めなくなることはなく" in section

    def test_manual_describes_the_degraded_line(self):
        section = _manual_section()
        assert "AI の観点提示ができませんでした" in section

    def test_manual_states_scaffold_applies_before_submitting_only(self):
        section = _manual_section()
        assert "まだ回答を送っていない状態" in section
        assert "この制限はかかりません" in section

    def test_manual_course_completion_says_the_learner_records_it(self):
        md = _read(STUDENT_MANUAL)
        section = md[md.index("{#course-completion}"):]
        assert "AI が合否を決めて" in section
        assert "確認済みとして記録されます" in section
