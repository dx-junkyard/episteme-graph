"""学習チャットの入口統合 Phase 1 — フロント（様相チップ・訂正）の静的ガードレール。

設計正本: ``docs/features/learning_chat_entry_unification_design.md``（§5 / §6 / §7 / §8）。
実装契約: 指揮者が固定した §2 の ``stance`` DTO（``{stance, source, label}``）。

対象（フロントのみ。サーバ側 = learning.py / schemas.py / observation.py は別テスト）:
  - ``frontend/public/js/app.js`` — ``renderStanceLine`` / ``correctStance`` /
    ``sendMessage`` の応答保持（``stance`` / ``reply_to_id``）
  - ``frontend/public/css/styles.css`` — ``.stance-line`` まわり
  - ``backend/core/help_kb/ui_anchors.py`` + ``docs/manual/student/02-student.md`` —
    学習側 UI アンカー ``chat.stance-chip`` の3点セット

すべて静的解析（部分文字列検索・波括弧カウントによる関数本体抽出）のみで、実サーバ・
実DOM・ブラウザを使わない（test_discuss_ui_static.py / test_understanding_cycle_ui_static.py
と同じ流儀）。

検証観点:
1. 既定は無表示（LC6/§6）: 推定（``source === "inferred"``）かつ ``stance !== "tutor"``
   のときだけ 1 行を描く。discuss 中は描かない。
2. 訂正は既存の書き直し経路（``_replace_message_id``）+ 明示 typed action
   （``support_action: "ask_question"``）。新しい API パスを作らない（§5）。
3. 表示ラベルはサーバの ``stance.label`` をそのまま描く。JS に
   ``core/label_vocab.py`` の ``LEARNING_STANCE_LABELS`` の表をミラーしない。
4. 観測（§7）: 訂正で ``stance_corrected`` を fire-and-forget（payload は ``{stance:"tutor"}``）。
5. LC7 数値非表示 / DM5 「寄り道」語彙を様相の文言に使わない。
6. UI アンカー3点セット（KNOWN 登録・UI_ANCHORS マップ・マニュアル節の実在）。
7. 音声ループ（``updateVoiceAvailability`` / ``handleVoiceSegment``）と
   ``sendWith`` / ``sendCurrent`` は無改変（様相に触らない）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb.ui_anchors import (  # noqa: E402
    KNOWN_UI_ANCHOR_IDS,
    UI_ANCHORS,
    resolve_ui_anchors,
)
from core.label_vocab import LEARNING_STANCE_LABELS  # noqa: E402

ANCHOR_ID = "chat.stance-chip"
MANUAL_REF = "student/02-student.md#stance-chip"
# 訂正チップの文言（設計 §6）。ラベル表のミラー検査ではこの固定文言だけを除外する
# （「ふつうの質問として」は LEARNING_STANCE_LABELS["tutor"] の前方一致になるため）。
CORRECTION_LABEL = "ふつうの質問として聞き直す"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _extract_function_body(src: str, signature: str) -> str:
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
    raise AssertionError("unterminated function body for: " + signature)


def _stance_blocks() -> str:
    """本層が新規に足した JS ブロック（描画 + 訂正）だけを連結して返す。"""
    js = _read(APP_JS)
    return (
        _extract_function_body(js, "function renderStanceLine(msg) {")
        + "\n"
        + _extract_function_body(js, "function correctStance(replyToId, stance) {")
    )


# ===========================================================================
# 1. 既定は無表示（静音）— tutor / 明示には何も描かない
# ===========================================================================


class TestStanceChipVisibility:
    def test_stance_chip_not_shown_for_tutor(self):
        block = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function renderStanceLine(msg) {")
        )
        # 推定のときだけ・tutor 以外のときだけ描く（早期 return）。
        assert 'st.source !== "inferred"' in block
        assert 'st.stance === "tutor"' in block
        assert 'return ""' in block

    def test_stance_chip_not_shown_in_discuss_mode(self):
        block = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function renderStanceLine(msg) {")
        )
        assert "isDiscussMode()" in block

    def test_render_hook_is_called_from_ai_content(self):
        js = _read(APP_JS)
        assert "html += renderStanceLine(msg);" in js
        # 出所（tier / grounding）の直後に置く（根拠の提示は様相に依らない）。
        assert js.index('var bar = \'<div class="answer-tier-bar">\'') < js.index(
            "html += renderStanceLine(msg);"
        )

    def test_line_carries_ui_anchor_and_fixed_sentence(self):
        block = _extract_function_body(
            _read(APP_JS), "function renderStanceLine(msg) {"
        )
        assert 'data-ui-anchor="' + ANCHOR_ID + '"' in block
        assert '"答えました。"' in block or "答えました。" in block
        assert CORRECTION_LABEL in block

    def test_response_keeps_stance_and_reply_to_id(self):
        js = _read(APP_JS)
        assert "stance: data.stance || null," in js
        assert "reply_to_id: userMsgId," in js


# ===========================================================================
# 2. 訂正は既存の書き直し経路（新エンドポイントを作らない）
# ===========================================================================


class TestCorrectionRouting:
    def test_correction_chip_uses_replace_message_id(self):
        block = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function correctStance(replyToId, stance) {")
        )
        assert "_replace_message_id: replyToId," in block
        assert 'support_action: "ask_question",' in block
        assert 'intent_mode: Session.inDetour() ? "explore" : "on_path",' in block
        # 新しい API パスを作らない（送信は sendMessage 経由のみ）。
        assert "apiFetch(" not in block
        assert "fetch(" not in block

    def test_no_new_stance_endpoint_anywhere(self):
        js = _strip_js_comments(_read(APP_JS))
        for path in re.findall(r'apiFetch\(\s*"([^"]+)"', js):
            assert "stance" not in path, f"様相専用の新パスを作っています: {path}"

    def test_correction_is_bound_by_data_attribute(self):
        js = _read(APP_JS)
        assert '"[data-stance-correct]"' in js
        assert 'this.getAttribute("data-stance-correct")' in js
        assert 'this.getAttribute("data-reply-to")' in js

    def test_correction_ignored_while_sending_and_clears_edit_state(self):
        block = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function correctStance(replyToId, stance) {")
        )
        assert "if (state.sending || !replyToId) return;" in block
        assert "cancelEditMessage()" in block

    def test_correction_is_not_sticky(self):
        """様相をクライアント状態として持ち越さない（§6: sticky にしない）。"""
        block = _strip_js_comments(_stance_blocks())
        for banned in ("localStorage", "sessionStorage", "state.stance"):
            assert banned not in block, f"{banned} に様相を持ち越しています"

    def test_send_with_and_send_current_untouched(self):
        js = _read(APP_JS)
        for sig in ("function sendWith(mode) {", "function sendCurrent() {"):
            if sig == "function sendCurrent() {":
                # 1 行関数なので本体抽出せず該当行で見る。
                line = [ln for ln in js.splitlines() if "function sendCurrent()" in ln][0]
                assert "stance" not in line
                continue
            assert "stance" not in _extract_function_body(js, sig)


# ===========================================================================
# 3. 表示ラベルはサーバ由来（JS に表を持たない）
# ===========================================================================


class TestLabelMirrorDiscipline:
    def test_stance_labels_come_from_label_vocab(self):
        """JS に LEARNING_STANCE_LABELS の値を焼き込まない（表を持たない）。

        唯一の例外は訂正チップの固定文言（「ふつうの質問として聞き直す」）で、これは
        様相→ラベルの対応表ではなくボタンの文言。検査前にその文言だけを取り除く。
        """
        js = _read(APP_JS).replace(CORRECTION_LABEL, "")
        for key, label in LEARNING_STANCE_LABELS.items():
            assert label not in js, f"様相ラベル「{label}」（{key}）を JS に焼き込んでいます"

    def test_label_is_read_from_response(self):
        block = _extract_function_body(
            _read(APP_JS), "function renderStanceLine(msg) {"
        )
        assert "st.label" in block

    def test_no_stance_enum_table_in_js(self):
        """本層のブロックで様相キーを列挙しない（分岐は source と tutor だけ）。

        ``discuss`` は既存の intent_mode 語彙として app.js 全域にあるため、検査対象は
        本層が新規に足したブロック（描画 + 訂正）に限る。
        """
        block = _strip_js_comments(_stance_blocks())
        for key in LEARNING_STANCE_LABELS:
            if key == "tutor":
                continue
            assert key not in block, f"様相キー「{key}」を JS に持ち込んでいます"


# ===========================================================================
# 4. 観測イベント（§7）
# ===========================================================================


class TestMetric:
    def test_stance_corrected_metric_sent(self):
        block = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function correctStance(replyToId, stance) {")
        )
        assert 'sendDiscussMetric("stance_corrected"' in block
        assert '{ stance: stance || "tutor" }' in block

    def test_metric_payload_has_no_free_text(self):
        block = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function correctStance(replyToId, stance) {")
        )
        call = block[block.index('sendDiscussMetric("stance_corrected"'):]
        call = call[: call.index(");") + 2]
        for banned in ("text", "message", "content"):
            assert banned not in call, f"観測 payload に本文（{banned}）を載せています"


# ===========================================================================
# 5. 語彙・数値の禁止（DM5 / LC7）
# ===========================================================================


class TestWording:
    def test_no_detour_wording_in_stance_ui(self):
        block = _stance_blocks()
        assert "寄り道" not in block, "様相の文言に「寄り道」を使っています（DM5）"

    def test_no_numbers_or_confidence(self):
        block = _strip_js_comments(_stance_blocks())
        for banned in ("confidence", "score", "スコア", "一致度", "％", "%", "件"):
            assert banned not in block, f"数値表現「{banned}」が含まれています（LC7）"

    def test_no_pressure_wording(self):
        block = _stance_blocks()
        for banned in ("してください", "しましょう", "おすすめ", "推奨", "警告", "注意"):
            assert banned not in block, f"督促・警告の語彙「{banned}」が含まれています"


# ===========================================================================
# 6. 学習者ヘルプアンカー 3点セット（正本 = core/help_kb/ui_anchors.py）
# ===========================================================================


class TestStanceAnchor:
    def test_stance_anchor_registered(self):
        assert ANCHOR_ID in KNOWN_UI_ANCHOR_IDS
        assert UI_ANCHORS.get(ANCHOR_ID) == MANUAL_REF

    def test_manual_section_exists_with_explicit_anchor(self):
        md = _read(STUDENT_MANUAL)
        assert "{#stance-chip}" in md
        assert CORRECTION_LABEL in md
        # 事実文の中身: 入口は1つ / 根拠の示し方は調子で変わらない / 記録は消えない
        assert "1 往復" in md or "１往復" in md

    def test_resolve_ui_anchors_returns_non_empty_body(self):
        resolved = resolve_ui_anchors()
        assert ANCHOR_ID in resolved, "マニュアル節が解決できません"
        assert resolved[ANCHOR_ID]["body"].strip()
        assert resolved[ANCHOR_ID]["manual_anchor"].startswith("student/")

    def test_frontend_carrier_exists_and_is_registered(self):
        js = _read(APP_JS)
        used = set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', js))
        assert ANCHOR_ID in used
        assert not (used - set(KNOWN_UI_ANCHOR_IDS)), (
            f"KNOWN_UI_ANCHOR_IDS に無いアンカーID: {used - set(KNOWN_UI_ANCHOR_IDS)}"
        )


# ===========================================================================
# 7. 音声ループは無改変（§6）
# ===========================================================================


class TestVoiceLoopUntouched:
    def test_voice_functions_do_not_mention_stance(self):
        js = _read(APP_JS)
        for sig in (
            "function updateVoiceAvailability() {",
            "async function handleVoiceSegment() {",
        ):
            body = _extract_function_body(js, sig)
            assert "stance" not in body, f"{sig} が様相に触れています（無改変が契約）"


# ===========================================================================
# 8. CSS（控えめなトーン・警告色にしない）
# ===========================================================================


class TestStyles:
    def test_stance_line_styles_exist(self):
        css = _read(STYLES_CSS)
        for selector in (".stance-line", ".stance-fact", ".stance-correct-btn"):
            assert selector in css, f"styles.css に {selector} のスタイルがありません"

    def test_stance_line_is_not_a_warning_color(self):
        css = _read(STYLES_CSS)
        block = css[css.index(".stance-line {"):]
        block = block[: block.index(".stance-correct-btn")]
        for banned in ("--color-text-warning", "--color-background-warning", "red", "#f00"):
            assert banned not in block, f"警告色（{banned}）を使っています"
