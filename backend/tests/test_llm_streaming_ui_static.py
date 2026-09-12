"""LLM 応答のストリーミング Phase 3-a — フロント（逐次バブル・停止）の静的ガードレール。

設計正本: ``docs/features/llm_response_streaming_design.md``（§2.1 / §2.2 / §5 / §9 の
ui_static 項）。対象はフロントだけ（サーバ側 = core/llm.py / routes/learning.py は
``test_llm_streaming_{core,api,guardrails}.py``）。

  - ``frontend/public/js/app.js`` — ``fetchClientFeaturesOnce`` / ``shouldStreamChatTurn`` /
    ``runChatStream`` / ``runStreamingChatTurn`` / ``rollbackStreamedTurn`` /
    ``applyChatTurnResponse`` / ``sendMessage``
  - ``frontend/public/css/styles.css`` — ``.mg.ai.streaming`` と 1 画面レイアウトの不変

すべて静的解析（部分文字列検索・波括弧カウントによる関数本体抽出）のみで、実サーバ・
実DOM・ブラウザを使わない（test_learning_stance_ui_static.py と同じ流儀）。

検証観点:
1. §5.1 ストリーム中に ``renderChat()`` を呼ばない（delta ごとの innerHTML 全再構築禁止）。
2. §5.1 delta の適用は ``textContent``（``innerHTML`` を使わない＝エスケープ不要）。
3. §5.1 / O-1 停止したら当該 user メッセージを ``state.chatMessages`` から取り除き、
   本文を入力欄へ戻す（片肺の往復を次の ``history`` に混ぜない）。
4. §3.4 / ST9 機能フラグ ``GET /learning/client-features`` をログイン後 1 回だけ取得し、
   失敗は ``false``（fail-to-current）。
5. §2.1 ``EventSource`` を使わない（Authorization ヘッダが乗らないため）。
   ``AbortController`` で停止できる。
6. ST8 学習者向けの文言に数値（トークン・秒・残回数）が出ない。
7. §5.4 音声・casual・書き直し・typed action はストリーム経路に入らない。
8. ST7 ``final`` の適用が従来の JSON 経路と同じ 1 本の関数を通る。
9. §5.3 1 画面レイアウトを壊さない（``.ca`` の ``min-height`` / 下段 ``flex: 0 0 auto``）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"

# ストリーム経路を構成する JS 関数（本体を切り出して字面検査する対象）。
STREAM_FUNCTIONS = (
    "async function runChatStream(path, bodyText) {",
    "async function runStreamingChatTurn(streamPath, requestBody, userMsgId, text) {",
    "function openStreamingBubble() {",
    "function restoreTypingIndicator() {",
    "function closeStreamingBubble() {",
    "function showStreamingStanceLine(stance) {",
    "function autoScrollChatIfAtBottom() {",
    "function parseSseFrame(frame) {",
    "function setSendButtonStopMode(on) {",
    "function abortChatStream() {",
    "function rollbackStreamedTurn(userMsgId, text, notice) {",
    "function showStreamNotice(message) {",
    "async function fetchClientFeaturesOnce() {",
    "function shouldStreamChatTurn(payload, replaceMessageId) {",
)


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


def _stream_blocks() -> str:
    """本層が新規に足したストリーム関連の JS だけを連結して返す。"""
    js = _read(APP_JS)
    return "\n".join(_extract_function_body(js, sig) for sig in STREAM_FUNCTIONS)


def _css_rule(css: str, selector: str) -> str:
    idx = css.index(selector + " {")
    end = css.index("}", idx)
    return css[idx:end]


# ===========================================================================
# 0. 前提: 各関数が実在する
# ===========================================================================


class TestStreamFunctionsExist:
    def test_all_stream_functions_present(self):
        js = _read(APP_JS)
        for sig in STREAM_FUNCTIONS:
            assert sig in js, f"ストリーム経路の関数がありません: {sig}"

    def test_shared_response_application_block_exists(self):
        """応答の適用（履歴 push・出典・チップ）は sendMessage の `if (data)` 1 本。"""
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function sendMessage(text, actionPayload) {")
        )
        assert "if (data) {\n        respData = data;" in body
        assert body.count("state.chatMessages.push({\n          role: \"assistant\",") == 1


# ===========================================================================
# 1. ストリーム中に renderChat() を呼ばない（§5.1）
# ===========================================================================


class TestNoRerenderWhileStreaming:
    def test_stream_functions_never_call_render_chat(self):
        block = _strip_js_comments(_stream_blocks())
        assert "renderChat(" not in block, (
            "ストリーム中に renderChat() を呼んでいます（delta ごとに ca.innerHTML を"
            "全再構築してしまう。§5.1）"
        )

    def test_final_triggers_exactly_one_render_from_send_message(self):
        """全再描画は送信直後（typing）と往復の末尾の 2 箇所だけ。

        delta ごとの再描画を足していないこと＝ストリームの成功・停止・失敗が
        すべて末尾の 1 回に合流していることを、回数で固定する。
        """
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function sendMessage(text, actionPayload) {")
        )
        assert body.count("renderChat();") == 2
        # 末尾の 1 回は try/catch を抜けたあと（成功・停止・失敗の合流点）。
        assert body.rindex("renderChat();") > body.index("} catch (err) {")

    def test_streaming_turn_does_not_apply_the_response_itself(self):
        """ストリーム側は「完成した応答を1つ返す」だけ（適用は sendMessage の共通ブロック）。"""
        body = _strip_js_comments(
            _extract_function_body(
                _read(APP_JS),
                "async function runStreamingChatTurn(streamPath, requestBody, userMsgId, text) {",
            )
        )
        assert "state.chatMessages.push({\n      role: \"assistant\"," not in body
        assert "renderChat(" not in body


# ===========================================================================
# 2. delta の適用は textContent（innerHTML を使わない）
# ===========================================================================


class TestDeltaIsPlainText:
    def test_delta_uses_text_content(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function runChatStream(path, bodyText) {")
        )
        assert "_streamState.bubble.textContent += ev.data.t;" in body

    def test_stream_functions_never_insert_html(self):
        block = _strip_js_comments(_stream_blocks())
        for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML"):
            assert banned not in block, f"ストリーム経路で {banned} を使っています（§5.1）"

    def test_streaming_bubble_class_is_plain(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function openStreamingBubble() {")
        )
        assert '"mg ai streaming"' in body

    def test_no_partial_markdown_rendering(self):
        """Phase 3-a はプレーンテキスト表示のみ（部分マークダウン・KaTeX を掛けない）。"""
        block = _strip_js_comments(_stream_blocks())
        for banned in ("renderAiContent(", "renderMathInElement(", "renderMirrorBlock("):
            assert banned not in block, f"ストリーム中に {banned} を掛けています（§5.2）"


# ===========================================================================
# 3. 停止したら往復ごと巻き戻す（O-1 / ST1）
# ===========================================================================


class TestStopRollsBackTurn:
    def test_rollback_removes_user_message_and_restores_input(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function rollbackStreamedTurn(userMsgId, text, notice) {")
        )
        # 当該 user メッセージ以降をクライアント履歴から取り除く。
        assert "_findMessageIndexById(userMsgId)" in body
        assert "state.chatMessages = state.chatMessages.slice(0, idx);" in body
        # 本文は入力欄へ戻す（書き直し startEditMessage と同じ作法）。
        assert 'document.getElementById("chat-input")' in body
        assert "input.value = text" in body

    def test_aborted_turn_is_not_applied(self):
        body = _strip_js_comments(
            _extract_function_body(
                _read(APP_JS),
                "async function runStreamingChatTurn(streamPath, requestBody, userMsgId, text) {",
            )
        )
        # aborted 分岐は rollback を呼び、応答の適用も JSON 再送もしない。
        m = re.search(r'if \(result\.status === "aborted"\) \{(.*?)\n    \}', body, flags=re.S)
        assert m, "aborted 分岐がありません"
        branch = m.group(1)
        assert "rollbackStreamedTurn(" in branch
        assert "data: null" in branch  # 応答として何も返さない＝適用ブロックを通らない
        assert "retryWithJson: false" in branch

    def test_send_message_skips_json_retry_when_aborted(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function sendMessage(text, actionPayload) {")
        )
        assert "if (needJsonRequest && !streamAborted) {" in body

    def test_abort_uses_abort_controller(self):
        js = _read(APP_JS)
        assert "new AbortController()" in js
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function abortChatStream() {")
        )
        assert "_streamState.controller.abort()" in body
        assert "_streamState.aborted = true;" in body


# ===========================================================================
# 4. 停止ボタンは送信ボタンの差し替え（新しい帯を作らない, §5.3）
# ===========================================================================


class TestStopButtonIsASwap:
    def test_stop_mode_swaps_the_existing_send_button(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function setSendButtonStopMode(on) {")
        )
        assert 'document.getElementById("send-btn")' in body
        assert '"停止"' in body
        # 新しいボタン・帯を DOM に足さない。
        assert "createElement(" not in body
        assert "appendChild(" not in body

    def test_no_new_stop_button_in_markup(self):
        html = _read(INDEX_HTML)
        assert "stop-btn" not in html
        assert 'id="chat-stop' not in html

    def test_send_button_click_aborts_while_streaming(self):
        """停止ハンドラは送信ハンドラ（sendCurrent）より前に登録し、ストリーム中だけ横取りする。

        既存の ``btn.addEventListener("click", sendCurrent)`` と ``sendCurrent`` 自体は
        無改変（= Enter キーの挙動も従来どおり）であることを併せて固定する。
        """
        js = _strip_js_comments(_read(APP_JS))
        assert "e.stopImmediatePropagation();" in js
        assert "abortChatStream();" in js
        assert 'btn.addEventListener("click", sendCurrent);' in js
        assert js.index("abortChatStream();\n    });") < js.index(
            'btn.addEventListener("click", sendCurrent);'
        )
        assert (
            'function sendCurrent() { sendWith(Session.inDetour() ? "explore" : "on_path"); }' in js
        )


# ===========================================================================
# 5. 機能フラグ（client-features）とフォールバック（§3.4 / ST9）
# ===========================================================================


class TestClientFeatureFlag:
    def test_client_features_is_fetched_once_after_login(self):
        js = _read(APP_JS)
        assert 'apiFetch("/learning/client-features")' in js
        assert "fetchClientFeaturesOnce();" in js
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function fetchClientFeaturesOnce() {")
        )
        assert "if (_streamState.clientFeaturesFetched) return;" in body
        assert "_streamState.clientFeaturesFetched = true;" in body

    def test_flag_defaults_to_false(self):
        js = _read(APP_JS)
        assert "clientFeatures: { chat_streaming: false }," in js

    def test_fetch_failure_leaves_flag_false(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function fetchClientFeaturesOnce() {")
        )
        assert "if (!res.ok) return;" in body
        assert "catch" in body

    def test_unsupported_falls_back_to_json_once_and_disables(self):
        body = _strip_js_comments(
            _extract_function_body(
                _read(APP_JS),
                "async function runStreamingChatTurn(streamPath, requestBody, userMsgId, text) {",
            )
        )
        assert "state.clientFeatures = { chat_streaming: false };" in body
        assert "return { retryWithJson: true, aborted: false, data: null };" in body

    def test_404_and_network_failure_are_unsupported(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function runChatStream(path, bodyText) {")
        )
        # 404 = フラグ off、401 = 失効（従来経路の apiFetch にログアウト処理を任せる）
        assert 'if (res.status === 404 || res.status === 401) return { status: "unsupported" };' in body
        # start 前の fetch 失敗も unsupported（従来経路へ退避）。
        assert body.count('{ status: "unsupported" }') >= 2


# ===========================================================================
# 6. EventSource を使わない / SSE 自前パース（§2.1）
# ===========================================================================


class TestTransport:
    def test_event_source_is_not_used(self):
        js = _strip_js_comments(_read(APP_JS))
        assert "EventSource" not in js, (
            "EventSource は Authorization ヘッダを付けられない（§2.1）。"
            "fetch + ReadableStream で自前パースすること"
        )

    def test_stream_request_carries_authorization_header(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function runChatStream(path, bodyText) {")
        )
        assert 'headers["Authorization"] = "Bearer " + state.token;' in body
        assert "signal: controller.signal" in body
        # トークンをクエリに載せない（個人・機微データを URL に置かない）。
        assert "token=" not in body

    def test_sse_frames_are_parsed_by_event_and_data(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function parseSseFrame(frame) {")
        )
        assert '"event:"' in body
        assert '"data:"' in body
        assert "JSON.parse(" in body

    def test_reader_loop_reads_the_body_stream(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function runChatStream(path, bodyText) {")
        )
        assert "res.body.getReader()" in body
        assert "new TextDecoder()" in body
        for name in ('"start"', '"delta"', '"final"', '"error"'):
            assert "ev.name === " + name in body


# ===========================================================================
# 7. 学習者に数値を見せない（ST8）
# ===========================================================================


class TestNoNumbersForLearner:
    def test_notices_have_no_digits(self):
        js = _read(APP_JS)
        for name in ("STREAM_STOP_NOTICE", "STREAM_ERROR_NOTICE"):
            m = re.search(r'const ' + name + r' = "([^"]*)";', js)
            assert m, f"{name} の定義が見つかりません"
            text = m.group(1)
            assert not re.search(r"[0-9０-９]", text), f"{name} に数値が入っています: {text}"

    def test_stop_notice_says_the_turn_is_not_recorded(self):
        js = _read(APP_JS)
        assert "途中で止めました。この応答は記録に残していません。" in js
        assert "応答を受け取れませんでした。" in js

    def test_stream_block_has_no_metering_vocabulary(self):
        block = _strip_js_comments(_stream_blocks())
        for banned in ("トークン", "残り回数", "残回数", "生成速度", "経過秒", "文字/秒"):
            assert banned not in block, f"学習者向けに計量値の語彙が出ています: {banned}"

    def test_stream_block_renders_no_counters(self):
        """delta の本文以外（件数・進捗率・所要時間）を描かない。"""
        block = _strip_js_comments(_stream_blocks())
        for banned in ("Date.now()", "performance.now()", "%"):
            assert banned not in block, f"進捗・時間の表示につながる {banned} を使っています"


# ===========================================================================
# 8. ストリーム対象の限定（§5.1 / §5.4）
# ===========================================================================


class TestStreamGating:
    def test_gate_excludes_voice_casual_rewrite_and_typed_action(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function shouldStreamChatTurn(payload, replaceMessageId) {")
        )
        assert 'state.clientFeatures.chat_streaming !== true' in body
        assert "if (replaceMessageId) return false;" in body
        assert "voiceState.active" in body
        assert 'payload.intent_mode === "casual"' in body
        assert "payload.support_action" in body
        assert "payload.ui_anchor" in body

    def test_voice_and_casual_path_untouched(self):
        """音声ループは従来どおり完成テキスト → TTS（Phase 3-a 非対象, §5.4）。"""
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function handleVoiceSegment() {")
        )
        assert "speakVoiceAnswer(data.answer)" in body
        assert "runChatStream(" not in body
        assert "/chat/stream" not in body

    def test_stream_path_is_built_from_the_existing_chat_path(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function sendMessage(text, actionPayload) {")
        )
        assert 'chatPath + "/stream"' in body
        # 非ストリーム経路の POST 先・ボディは従来のまま（ST7）。
        assert 'state.currentTopicId + "/chat"' in body
        assert "body: requestBody," in body


# ===========================================================================
# 9. final は従来経路と同じ 1 本の適用関数を通る（ST7）
# ===========================================================================


class TestSharedApplyPath:
    def test_both_paths_converge_on_one_data_variable(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "async function sendMessage(text, actionPayload) {")
        )
        assert "data = streamed.data;" in body       # ストリーム経路
        assert "data = await res.json();" in body    # 従来の JSON 経路
        assert body.count("if (data) {") == 1        # 適用は 1 箇所に合流

    def test_apply_keeps_existing_response_fields(self):
        body = _extract_function_body(
            _read(APP_JS), "async function sendMessage(text, actionPayload) {"
        )
        for field in (
            "content: data.answer,",
            "sources: data.sources || [],",
            "overall_tier: data.overall_tier || null,",
            "content_grounding: data.content_grounding || null,",
            "anchor_confirm: data.anchor_confirm || null,",
            "mirror: data.mirror || null,",
            "stance: data.stance || null,",
            "reply_to_id: userMsgId,",
        ):
            assert field in body, f"応答の保持フィールドが落ちています: {field}"

    def test_stance_line_rules_match_render_stance_line(self):
        """start の先出し 1 行は renderStanceLine と同じ規則（推定・非 tutor・非 discuss）。"""
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function showStreamingStanceLine(stance) {")
        )
        assert 'stance.source !== "inferred"' in body
        assert 'stance.stance === "tutor"' in body
        assert "isDiscussMode()" in body
        # final 後の renderChat と二重にならないよう、先出し要素は必ず除去する。
        close = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function closeStreamingBubble() {")
        )
        assert "_streamState.stanceEl" in close and ".remove()" in close


# ===========================================================================
# 10. CSS / 1 画面レイアウト（§5.3）
# ===========================================================================


class TestStylesAndLayout:
    def test_streaming_bubble_is_pre_wrap(self):
        rule = _css_rule(_read(STYLES_CSS), ".mg.ai.streaming")
        assert "white-space: pre-wrap" in rule

    def test_stream_notice_is_not_a_warning(self):
        rule = _css_rule(_read(STYLES_CSS), ".stream-notice")
        assert "color: var(--color-text-tertiary)" in rule
        assert "background-warning" not in rule

    def test_chat_area_min_height_unchanged(self):
        rule = _css_rule(_read(STYLES_CSS), ".ca")
        assert "flex: 1 1 0" in rule
        assert "min-height: 120px" in rule

    def test_lower_region_still_never_shrinks(self):
        css = _read(STYLES_CSS)
        for selector in (".mode-bar", ".ia"):
            rule = _css_rule(css, selector)
            assert "flex: 0 0 auto" in rule, f"{selector} が押し出され得る形になっています"

    def test_auto_scroll_only_when_at_bottom(self):
        body = _strip_js_comments(
            _extract_function_body(_read(APP_JS), "function autoScrollChatIfAtBottom() {")
        )
        assert "ca.scrollHeight - ca.scrollTop - ca.clientHeight" in body
        assert "STREAM_AUTOSCROLL_SLACK_PX" in body
        assert "if (atBottom) ca.scrollTop = ca.scrollHeight;" in body
        # 読み返し中に引き戻さない（無条件スクロール・scrollIntoView をしない）。
        assert "scrollIntoView" not in body
