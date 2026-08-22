"""確認問題に合格したときも講評を出し、そこから深掘りできることの静的ガードレール。

背景: `/check` は**合否に関わらず** feedback / model_answer / explanation を返すのに、
フロントは合格時に overlay を即 remove して次トピックへ遷移していたため、サーバーが
作った講評が一度も表示されずに捨てられていた（合格すると何のフィードバックも出ない）。

設計出所: docs/features/learning.md §2.3「確認問題の採点結果（合格時も講評を出す）」 +
docs/manual/student/02-student.md §「確認問題の3つの選択肢」{#check-options}。

test_check_options_ui_static.py と同じ方式（Path 読み + 素の assert + 波括弧カウントに
よる関数・ブロック本体の抽出）で受け入れ条件を固定する。

受け入れ条件との対応:
1. 合格分岐は自動遷移しない — 講評があれば applyCheckPassState で提示し、
   即前進（selectTopic / 完了カード）は「講評も解答例も解説も空」のときだけ
2. applyCheckPassState は講評を一等地に出し、解答例・解説は <details> に畳む。
   主ボタンを data-advance（次へ進む / 確認を終える）に切り替え、確認後に事実でなくなる
   スキップ導線を取り除く
3. 合格結果は state.lastCheckPass に保持し、議論から開き直したときに講評を再提示する。
   トピック単位なので selectTopic で破棄する
4. 合格後の深掘りは壁打ち（check_scaffold）にしない — 答えは本人が組み立て終えている
   ので、解答を伏せる拘束は目的を失う。持ち出す見出しも合否で分ける
5. サーバーは合否に関わらず講評を返す（合格時に落とす分岐を作らない）
6. 合格表示に数値・スコア・祝祭演出を出さない（既存の不変条項）
7. マニュアルに合格後の画面と「合格前だけ答えを伏せる」事実が書かれている
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
FEATURE_DOC = ROOT / "docs" / "features" / "learning.md"

PASS_STATE_SIG = "function applyCheckPassState(pass) {"
PASSED_BRANCH_SIG = "if (data.passed) {"
OPEN_MODAL_SIG = "function openCheckModal() {"
DISCUSS_HANDLER_SIG = (
    'document.getElementById("check-discuss").addEventListener("click", async function () {'
)

# 数値・進捗・ゲーミフィケーション語彙（test_learner_ux_static.py と同一集合）+ 祝祭演出。
FORBIDDEN_WORDS = (
    "踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア",
    "おめでとう", "🎉", "祝", "レベルアップ", "正解率", "点数", "何点",
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


class TestPassedBranchDoesNotDiscardFeedback:
    """合格しても講評を捨てない（即遷移は講評が空のときだけ）。"""

    def test_passed_branch_renders_grading_before_any_advance(self):
        body = _extract_body(_read(APP_JS), PASSED_BRANCH_SIG)
        assert "applyCheckPassState(state.lastCheckPass)" in body
        # 前進（selectTopic / 完了カード）は講評が空のときのフォールバックなので、
        # 提示より後ろ（else 側）に来る。
        assert body.index("applyCheckPassState") < body.index("await selectTopic")
        assert body.index("applyCheckPassState") < body.index("showCourseCompletionCard")

    def test_passed_branch_gates_on_all_three_grading_fields(self):
        """講評・解答例・解説のいずれかがあれば提示する（feedback だけを見ない）。"""
        body = _extract_body(_read(APP_JS), PASSED_BRANCH_SIG)
        assert "data.feedback" in body
        assert "data.model_answer" in body
        assert "data.explanation" in body
        assert "if (passFeedback || passModelAnswer || passExplanation) {" in body

    def test_passed_branch_stores_pass_state_with_topic_id(self):
        body = _extract_body(_read(APP_JS), PASSED_BRANCH_SIG)
        assert "state.lastCheckPass = {" in body
        assert "topicId: state.currentTopicId," in body
        assert "passed: true," in body


class TestApplyCheckPassState:
    """合格表示の中身と、前進が明示操作になっていること。"""

    def test_feedback_is_rendered_with_pass_class(self):
        body = _extract_body(_read(APP_JS), PASS_STATE_SIG)
        assert 'feedbackEl.className = "check-feedback pass";' in body
        assert 'escHtml(pass.feedback || "")' in body

    def test_model_answer_and_explanation_are_collapsed(self):
        """一等地は講評だけ。解答例・解説は本人が開くまで畳む。"""
        body = _extract_body(_read(APP_JS), PASS_STATE_SIG)
        assert '<details class="check-reveal">' in body
        assert "解答例と解説を読む" in body
        # 講評は details の外（先に出る）。
        assert body.index('escHtml(pass.feedback') < body.index("check-reveal") or (
            body.index("reveal =") < body.index('escHtml(pass.feedback')
        )

    def test_primary_button_switches_to_explicit_advance(self):
        body = _extract_body(_read(APP_JS), PASS_STATE_SIG)
        assert 'submitBtn.setAttribute("data-advance", "true");' in body
        # 最終トピックで「次へ」と言わない。
        assert '"次へ進む"' in body
        assert '"確認を終える"' in body
        assert "getNextTopic()" in body

    def test_submitted_answer_is_kept_read_only(self):
        body = _extract_body(_read(APP_JS), PASS_STATE_SIG)
        assert "answerEl.value = pass.answer" in body
        assert "answerEl.readOnly = true;" in body

    def test_skip_exit_removed_after_confirmation(self):
        """確認したあとに「今回は確認せず進む」は事実でなくなる。"""
        body = _extract_body(_read(APP_JS), PASS_STATE_SIG)
        assert 'document.getElementById("check-skip")' in body
        assert "skipBtn.remove();" in body

    def test_no_forbidden_or_celebratory_wording(self):
        body = _extract_body(_read(APP_JS), PASS_STATE_SIG)
        hits = [w for w in FORBIDDEN_WORDS if w in body]
        assert not hits, f"禁止語彙が見つかりました: {hits}"


class TestPassStateLifetime:
    """合格時の講評はトピック単位。開き直しでは復元し、トピック切替では破棄する。"""

    def test_state_declares_last_check_pass_default_null(self):
        assert "lastCheckPass: null," in _read(APP_JS)

    def test_open_check_modal_restores_pass_state_for_same_topic(self):
        body = _extract_body(_read(APP_JS), OPEN_MODAL_SIG)
        assert "state.lastCheckPass.topicId === state.currentTopicId" in body
        assert "applyCheckPassState(state.lastCheckPass)" in body
        # 冒頭のクリア（古い指摘の持ち越し禁止）より後に復元する。
        assert body.index("state.lastCheckGrading = null;") < body.index(
            "applyCheckPassState(state.lastCheckPass)"
        )

    def test_pass_state_reset_on_topic_switch(self):
        body = _extract_body(_read(APP_JS), "async function selectTopic(")
        assert "state.lastCheckPass = null;" in body


class TestDeepDiveAfterPass:
    """合格後の深掘りは壁打ちにしない（拘束の目的が失われている）。"""

    def test_discuss_handler_branches_on_passed(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert "var passedCheck = !!(grading && grading.passed);" in body
        assert "if (passedCheck) {" in body

    def test_pass_path_sends_without_scaffold(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        pass_block = body[body.index("if (passedCheck) {"):body.index("} else {")]
        assert "state.checkScaffoldActive = false;" in pass_block
        assert "check_scaffold" not in pass_block
        # 不合格側は壁打ちのまま（既存契約）。
        assert "check_scaffold: true" in body

    def test_carried_heading_differs_by_verdict(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert '"講評: "' in body
        assert '"指摘された点: "' in body

    def test_return_chip_still_offered_after_pass_discussion(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert body.index("await sendMessage") < body.index("showCheckReturnChip()")


class TestServerReturnsFeedbackRegardlessOfVerdict:
    """採点結果の講評を合格時に落とす分岐を作らない（フロントが出せる前提）。"""

    def test_response_schema_carries_feedback_fields(self):
        for field in ("feedback", "model_answer", "explanation", "answer_requirements"):
            assert field in LearningCheckQuestionResponse.model_fields

    def test_route_builds_feedback_outside_any_passed_branch(self):
        body = _python_def_source(_read(LEARNING_PY), "def check_topic_understanding(")
        assert 'feedback = str(parsed.get("feedback") or "")' in body
        # 講評の組み立ては合否判定より後、かつ if 分岐の中ではない（インデント2階層）。
        assert '\n    feedback = str(parsed.get("feedback")' in body
        assert "        feedback=feedback," in body

    def test_prompt_requires_feedback_for_both_verdicts(self):
        """採点プロンプトが合格時の講評を明示的に要求する（空講評で即遷移に戻らない）。"""
        body = _python_def_source(_read(LEARNING_PY), "def check_topic_understanding(")
        assert "feedback は合否に関わらず必ず書いてください" in body
        assert "passed=true のときは" in body
        # 数値・評価の言い切りは書かせない（既存の不変条項）。
        assert "点数・正解率・達成度のような数値" in body


class TestCssHooks:
    def test_pass_and_reveal_classes_exist(self):
        css = _read(STYLES_CSS)
        for cls in (".check-feedback.pass", ".check-reveal"):
            assert cls in css, f"styles.css に {cls} がありません"


class TestDocumentation:
    def test_manual_describes_passed_screen(self):
        section = _manual_section()
        assert "講評" in section
        assert "次へ進む" in section
        assert "確認を終える" in section
        assert "解答例と解説を読む" in section

    def test_manual_states_scaffold_applies_before_passing_only(self):
        """「答えを伏せる」のは合格前だけ、という事実を書く（合格後は通常のチャット）。"""
        section = _manual_section()
        assert "まだ合格していない" in section
        assert "合格したあとに議論を始めた場合は、この制限はかかりません" in section

    def test_manual_has_no_forbidden_words(self):
        section = _manual_section()
        hits = [w for w in FORBIDDEN_WORDS if w in section]
        assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_feature_doc_records_the_change(self):
        doc = _read(FEATURE_DOC)
        assert "確認問題の採点結果（合格時も講評を出す）" in doc
        assert "applyCheckPassState" in doc
