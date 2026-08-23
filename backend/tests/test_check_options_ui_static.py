"""確認問題モーダルの3つの選択肢（回答する / AIと議論 / 確認せず進む）の静的ガードレール。

設計出所: docs/features/learning.md の確認問題フロー + docs/manual/student/02-student.md
§「確認問題の3つの選択肢」{#check-options}。

test_descent_ui_static.py / test_discuss_ui_static.py と同じ方式（Path 読み + 素の
assert + 波括弧カウントによる関数・ハンドラ本体の抽出）で、フロントエンドソースの静的
検証により受け入れ条件を固定する（実ブラウザ・実 API には依存しない）。

受け入れ条件との対応:
1. 3ボタン（#check-submit / #check-discuss / #check-skip）と2つの data-ui-anchor 値、
   「確認問題に戻る」チップ（#check-return-chip）が app.js に存在する
2. スキップは**既存の自由（モーダルを閉じて別トピックを選ぶ）の明示化**にすぎないので、
   ハンドラは overlay 除去 → selectTopic のみ。apiFetch（サーバ記録）と
   showAtlasCueAfterAdvance（「〜を確認しました」の地図 cue = 確認していないのに
   出せば偽事実）を呼ばないことを構造的に固定する
3. 議論は回答下書き（空なら「（まだ回答していません）」）と直前の採点結果を合成して
   sendMessage で自動送信する（P4: 書きかけと指摘を捨てない）
4. openCheckModal は冒頭で state.lastCheckGrading をクリアする（古い指摘の持ち越し禁止）
5. チップは追加前に removeCheckReturnChip を呼び常に最新1枚。ポーリング（setInterval /
   setTimeout での再掲）をしない
6. showCourseCompletionCard の opts.skipped 分岐（タイトル文言と「回答しました」行の抑止）
7. スキップ/議論まわりの新規文言に督促・煽り語彙が無い
8. UI アンカー4点セット（check.discuss / check.skip: KNOWN 登録・UI_ANCHORS 値・
   マニュアル節 {#check-options} 実在・CSS クラス実在）
9. 議論は**壁打ちモード**（check_scaffold）で送る。AI は確認問題の解答そのもの・
   模範解答を出さず、回答に必要な構成要素（定義・事実・関係）だけを説明し、組み立ては
   学習者に委ねる。フラグは開幕1通で終わらず、追撃の「答えを教えて」でも直答に
   戻らないよう state.checkScaffoldActive で継続注入し、確認問題に戻ったとき・
   トピックを移ったときにリセットする
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from api.schemas import LearningChatRequest  # noqa: E402
from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS, UI_ANCHORS  # noqa: E402

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"
LEARNING_PY = ROOT / "backend" / "api" / "routes" / "learning.py"

ANCHOR_IDS = ("check.discuss", "check.skip")
MANUAL_REF = "student/02-student.md#check-options"

# ハンドラ・関数本体の抽出対象シグネチャ。
SKIP_HANDLER_SIG = (
    'document.getElementById("check-skip").addEventListener("click", async function () {'
)
DISCUSS_HANDLER_SIG = (
    'document.getElementById("check-discuss").addEventListener("click", async function () {'
)
OPEN_MODAL_SIG = "function openCheckModal() {"
SHOW_CHIP_SIG = "function showCheckReturnChip() {"
REMOVE_CHIP_SIG = "function removeCheckReturnChip() {"
COMPLETION_CARD_SIG = (
    "function showCourseCompletionCard(completedTopic, courseCompleted, opts) {"
)

# 督促・煽り語彙（スキップは「押し付けないための逃げ道」なので、選ばなかったことを
# 責める文言・回答を強制する文言を出さない）。
PUSHY_WORDS = ("理解が浅", "サボ", "遅れて", "推奨しません", "必ず回答")

# 数値・進捗・ゲーミフィケーション語彙（test_descent_ui_static.py と同一集合）。
FORBIDDEN_WORDS = ("踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_body(src: str, signature: str) -> str:
    """`signature` から対応する閉じ `}` までを波括弧カウントで抽出する
    （test_descent_ui_static.py と同じ流儀）。"""
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
    """Python の `def name(` から、次のトップレベル定義（`def ` / `class ` / `@`）の
    直前までを切り出す（learning.py はモジュール直下に関数を並べる）。"""
    start = src.index(signature)
    rest = src[start + len(signature):]
    ends = [rest.find(marker) for marker in ("\ndef ", "\nclass ", "\n@")]
    ends = [e for e in ends if e != -1]
    return src[start:start + len(signature) + (min(ends) if ends else len(rest))]


def _new_wording_bodies() -> str:
    """スキップ・議論・チップ・完了カードの本体（新規文言の担体）をまとめて返す。"""
    js = _read(APP_JS)
    return "\n".join(
        _extract_body(js, sig)
        for sig in (
            SKIP_HANDLER_SIG,
            DISCUSS_HANDLER_SIG,
            SHOW_CHIP_SIG,
            REMOVE_CHIP_SIG,
            COMPLETION_CARD_SIG,
        )
    )


class TestModalElements:
    """3つの選択肢とチップの DOM 要素・アンカー属性が存在する。"""

    def test_three_options_and_chip_ids_exist(self):
        js = _read(APP_JS)
        for element_id in ("check-submit", "check-discuss", "check-skip", "check-return-chip"):
            assert element_id in js, f"app.js に #{element_id} がありません"

    def test_data_ui_anchor_attributes_on_both_buttons(self):
        body = _extract_body(_read(APP_JS), OPEN_MODAL_SIG)
        assert 'data-ui-anchor="check.discuss"' in body
        assert 'data-ui-anchor="check.skip"' in body

    def test_skip_label_depends_on_next_topic(self):
        """次トピック有無でラベルを切り替える（最終トピックで「次へ」と言わない）。"""
        body = _extract_body(_read(APP_JS), OPEN_MODAL_SIG)
        assert "hasNextTopic" in body
        assert "今回は確認せず次へ進む" in body
        assert "今回は確認せず終える" in body

    def test_skip_uses_weak_link_style_class(self):
        """スキップは弱いリンク風（check-skip-link）で、主ボタン扱いにしない。"""
        body = _extract_body(_read(APP_JS), OPEN_MODAL_SIG)
        assert 'id="check-skip" class="check-skip-link"' in body


class TestSkipHandler:
    """スキップは既存の自由の明示化 — サーバ記録も地図 cue も作らない。"""

    def test_skip_does_not_call_api(self):
        """apiFetch を呼ばない（確認せず進んだ事実をサーバに記録しない）。"""
        body = _extract_body(_read(APP_JS), SKIP_HANDLER_SIG)
        assert "apiFetch" not in body
        assert "fetch(" not in body

    def test_skip_does_not_show_atlas_cue(self):
        """showAtlasCueAfterAdvance（「〜を確認しました」）を呼ばない — 確認して
        いないのに出せば偽の事実になる。"""
        body = _extract_body(_read(APP_JS), SKIP_HANDLER_SIG)
        assert "showAtlasCueAfterAdvance" not in body
        assert "AtlasCues" not in body

    def test_skip_advances_via_select_topic(self):
        body = _extract_body(_read(APP_JS), SKIP_HANDLER_SIG)
        assert "overlay.remove()" in body
        assert "selectTopic(skipNext.id)" in body

    def test_skip_on_last_topic_marks_completion_card_as_skipped(self):
        body = _extract_body(_read(APP_JS), SKIP_HANDLER_SIG)
        assert "showCourseCompletionCard(" in body
        assert "{ skipped: true }" in body


class TestDiscussHandler:
    """議論は下書きと採点の指摘を捨てず、そのまま自動送信する（P4）。"""

    def test_discuss_auto_sends_via_send_message(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert "await sendMessage(lines.join" in body

    def test_discuss_carries_draft_with_fallback(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert "check-answer" in body
        assert "（まだ回答していません）" in body

    def test_discuss_carries_grading_feedback_and_requirements(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert "state.lastCheckGrading" in body
        assert "grading.feedback" in body
        assert "grading.answer_requirements" in body

    def test_discuss_shows_return_chip_after_sending(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert body.index("await sendMessage") < body.index("showCheckReturnChip()")


class TestGradingNotCarriedOver:
    """開き直しは前回の採点結果を持ち越さない（古い指摘で議論メッセージを作らない）。"""

    def test_open_check_modal_clears_last_grading_before_wiring(self):
        body = _extract_body(_read(APP_JS), OPEN_MODAL_SIG)
        assert "state.lastCheckGrading = null;" in body
        assert body.index("state.lastCheckGrading = null;") < body.index("check-discuss")


class TestReturnChip:
    """チップは常に最新1枚・消えたら再掲しない。"""

    def test_show_chip_removes_previous_first(self):
        body = _extract_body(_read(APP_JS), SHOW_CHIP_SIG)
        assert body.index("removeCheckReturnChip()") < body.index("createElement")

    def test_chip_click_reopens_modal(self):
        body = _extract_body(_read(APP_JS), SHOW_CHIP_SIG)
        assert "openCheckModal()" in body

    def test_chip_removed_on_topic_change(self):
        """トピックを移ったら「確認問題に戻る」は事実でなくなる（selectTopic 内で除去）。"""
        js = _read(APP_JS)
        select_topic_body = _extract_body(js, "async function selectTopic(")
        assert "removeCheckReturnChip()" in select_topic_body

    def test_no_polling_for_chip(self):
        """setInterval / setTimeout による再掲をしない。"""
        body = "\n".join(
            _extract_body(_read(APP_JS), sig) for sig in (SHOW_CHIP_SIG, REMOVE_CHIP_SIG)
        )
        assert "setInterval" not in body
        assert "setTimeout" not in body

    def test_chip_text_via_textcontent(self):
        body = _extract_body(_read(APP_JS), SHOW_CHIP_SIG)
        assert 'chip.textContent = "確認問題に戻る"' in body
        assert "innerHTML" not in body


class TestCompletionCardSkippedBranch:
    """スキップして最後まで来た場合は「回答した」と言わない。"""

    def test_skipped_title_wording(self):
        body = _extract_body(_read(APP_JS), COMPLETION_CARD_SIG)
        assert "opts.skipped" in body
        assert "最後のトピックまで進みました" in body

    def test_answered_line_suppressed_when_skipped(self):
        body = _extract_body(_read(APP_JS), COMPLETION_CARD_SIG)
        assert "completedTopic && !opts.skipped" in body
        assert body.index("completedTopic && !opts.skipped") < body.index(
            "」の確認問題に回答しました。"
        )

    def test_opts_defaults_to_empty_object(self):
        """既存の2引数呼び出し（合格経路）を壊さない。"""
        body = _extract_body(_read(APP_JS), COMPLETION_CARD_SIG)
        assert "opts = opts || {};" in body


class TestNoPushyVocabulary:
    """スキップという逃げ道を、選んだことを責める文言で塞がない。"""

    def test_new_app_wording_has_no_pushy_words(self):
        body = _new_wording_bodies()
        hits = [w for w in PUSHY_WORDS if w in body]
        assert not hits, f"督促・煽り語彙が見つかりました: {hits}"

    def test_new_app_wording_has_no_forbidden_words(self):
        body = _new_wording_bodies()
        hits = [w for w in FORBIDDEN_WORDS if w in body]
        assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_manual_section_has_no_pushy_words(self):
        section = _manual_section()
        hits = [w for w in PUSHY_WORDS + FORBIDDEN_WORDS if w in section]
        assert not hits, f"禁止語彙が見つかりました: {hits}"


def _manual_section() -> str:
    """マニュアルの {#check-options} 節本文（次の ### 見出しまで）を切り出す。"""
    md = _read(STUDENT_MANUAL)
    start = md.index("{#check-options}")
    rel_end = md[start:].find("\n### ")
    return md[start:start + rel_end] if rel_end != -1 else md[start:]


class TestLearnerHelpAnchor:
    """UI アンカー4点セット（正本 = core/help_kb/ui_anchors.py）。"""

    def test_anchors_registered_in_known_ids(self):
        for anchor_id in ANCHOR_IDS:
            assert anchor_id in KNOWN_UI_ANCHOR_IDS

    def test_anchors_mapped_to_the_same_student_manual_section(self):
        """3選択肢を1節で並べて説明しているので、2つのアンカーは同じ節を指す。"""
        for anchor_id in ANCHOR_IDS:
            assert UI_ANCHORS.get(anchor_id) == MANUAL_REF

    def test_manual_section_exists_with_explicit_anchor(self):
        section = _manual_section()
        # 3つの選択肢がすべて説明されている。
        assert "回答する" in section
        assert "AIと議論して理解を深める" in section
        assert "今回は確認せず次へ進む" in section
        assert "今回は確認せず終える" in section
        assert "確認問題に戻る" in section

    def test_manual_states_skip_is_not_completion(self):
        """スキップが完了扱いにならない事実と、記録・評価されない事実を明記する。"""
        section = _manual_section()
        assert "「確認済み」にはならず" in section
        assert "評価に使われたりすることはありません" in section
        assert "いつでも確認できます" in section

    def test_css_rules_exist(self):
        css = _read(STYLES_CSS)
        for cls in (".check-skip-link", ".check-return-chip"):
            assert cls in css, f"styles.css に {cls} がありません"


# --- 壁打ちモード（check_scaffold） -----------------------------------------
# 契約フレーズ: プロンプト拘束の骨子。文面を整えるのは自由だが、この語が落ちたら
# 「解答を出さない / 要素は説明する / 組み立ては学習者 / 壁打ち / 問いかけ」という
# 設計意図のどれかが失われている。
SCAFFOLD_CONTRACT_PHRASES = (
    "解答そのもの",
    "模範解答",
    "構成要素",
    "組み立て",
    "壁打ち",
    "問いかけ",
)


class TestCheckScaffoldFrontend:
    """議論は壁打ちモードで送り、追撃でも直答に戻らない。"""

    def test_discuss_handler_sends_check_scaffold_flag(self):
        body = _extract_body(_read(APP_JS), DISCUSS_HANDLER_SIG)
        assert "check_scaffold: true" in body
        assert "state.checkScaffoldActive = true;" in body

    def test_state_declares_check_scaffold_active_default_false(self):
        js = _read(APP_JS)
        assert "checkScaffoldActive: false," in js

    def test_send_message_reinjects_flag_while_active(self):
        """壁打ちは開幕1通で終わらない — sendMessage が継続注入する。"""
        body = _extract_body(_read(APP_JS), "async function sendMessage(text, actionPayload) {")
        assert "state.checkScaffoldActive" in body
        assert "payload.check_scaffold = true;" in body
        # 明示指定（分岐チップ等）を上書きしない。
        assert "payload.check_scaffold === undefined" in body

    def test_reset_on_topic_change(self):
        body = _extract_body(_read(APP_JS), "async function selectTopic(")
        assert "state.checkScaffoldActive = false;" in body

    def test_reset_when_check_modal_reopened(self):
        body = _extract_body(_read(APP_JS), OPEN_MODAL_SIG)
        assert "state.checkScaffoldActive = false;" in body


class TestCheckScaffoldBackend:
    """system プロンプトの拘束と、その注入地点を固定する。"""

    def test_request_field_defaults_to_false(self):
        assert "check_scaffold" in LearningChatRequest.model_fields
        assert LearningChatRequest(message="x").check_scaffold is False

    def test_instruction_contains_contract_phrases(self):
        src = _read(LEARNING_PY)
        assert "_CHECK_SCAFFOLD_INSTRUCTION = " in src
        instruction = src[src.index("_CHECK_SCAFFOLD_INSTRUCTION = "):]
        instruction = instruction[:instruction.index('"""', instruction.index('"""') + 3)]
        missing = [p for p in SCAFFOLD_CONTRACT_PHRASES if p not in instruction]
        assert not missing, f"契約フレーズが欠けています: {missing}"

    def test_learning_chat_injects_instruction(self):
        body = _python_def_source(_read(LEARNING_PY), "def learning_chat(")
        assert "if body.check_scaffold:" in body
        assert '_system_prompt += "\\n\\n" + _CHECK_SCAFFOLD_INSTRUCTION' in body

    def test_learning_chat_neutralizes_qa_scaffold_frame(self):
        """「以下の質問に答えてください」の足場が直答を再誘導しないよう中立化する
        （DA1/DA2 と同型の問題）。"""
        body = _python_def_source(_read(LEARNING_PY), "def learning_chat(")
        idx = body.index("_scaffold_user_instruction")
        frame = body[idx - 400:]
        assert "壁打ちモードの規則に従って" in frame
        assert "答えの組み立ては学習者に委ね" in frame


class TestCheckScaffoldManual:
    """マニュアルは「答えを直接教えない」事実を書く（督促・煽り語彙は既存テストが検査）。"""

    def test_manual_states_ai_does_not_give_the_answer(self):
        section = _manual_section()
        assert "答えを" in section and "直接教えません" in section
        assert "組み立て" in section
        assert "壁打ち" in section
