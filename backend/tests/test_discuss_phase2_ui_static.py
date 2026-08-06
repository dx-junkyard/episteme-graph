"""discuss モード（「論文と話す」, docs/features/discussion_mode_design.md §6.3 Phase 2）の
フロントエンド実装（frontend/public/js/discuss.js, app.js, index.html, styles.css）に対する
静的ガードレール。test_discuss_ui_static.py（Phase 1）と同じ流儀で、ソース文字列の
部分一致・関数本体の抽出検査のみを行う（実サーバ・実DOM不使用）。

検証観点:
1. discuss.js: 開幕画面3区画（この論文の主張／理論のバックボーン／最初の一手）
   の見出し文言が存在する。
2. discuss.js: 開幕画面は available:false / 非 ok 応答 / documents 空のとき教材区画を
   上書きしない（fail-closed。Phase 1 のプレースホルダのまま）。
3. discuss.js: 着地画面（consolidation）の3区画見出し・スキップボタン・15分無活動
   タイマー定数が存在する。
4. discuss.js に数値スコア・件数・網羅率を学習者に見せる表示が無い。
5. app.js: 分岐チップ（深掘り／横展開）は discuss モード限定でのみ描画される。
6. app.js: sendMessage 成功パスから notifyActivity() を呼ぶ配線がある。
7. app.js: selectTopic の discuss→通常トピック遷移直後に着地画面トリガー②が発火する。
8. index.html: discuss.js の script タグと着地モーダルのルート要素が存在する。
9. discuss.js 全体に「寄り道」「疑え」という語が一切含まれない。
10. app.js: switchCourse がコース切替時に旧コースの discuss 内部状態を破棄する
    （Discuss.reset() 呼び出し）。無活動タイムアウトが別コース画面で誤発火するのを防ぐ
    （2026-07-25 追記）。
11. discuss.js: maybeShowLanding は発火時点のアプリ現在コースと discuss セッションの
    コースが一致しない場合は何もしない防御ガードを持つ（DI 未注入時は後方互換で従来
    どおり動作する）。app.js が initApp から Discuss.init に getActiveCourseId を注入する
    （2026-07-25 追記）。
12. discuss.js: 着地モーダル本文に「接続は『わたしの地図』の既存導線で行える」旨の
    事実文注記が存在する（設計 §9.1 軌道修正 #5、2026-07-25 追記）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_function_body(src: str, signature: str) -> str:
    """`signature`（例: "function foo(...) {"）から対応する閉じ `}` までを、
    素朴な波括弧カウントで抽出する（test_discuss_ui_static.py と同じ流儀）。"""
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


class TestOpeningScreenSections:
    """設計 §3.3: 開幕画面3区画。すべて非LLM・A層成果の読み出しのみ。"""

    def test_three_section_headings_present(self):
        js = _read(DISCUSS_JS)
        assert "この論文の主張" in js
        # 旧見出し（比喩的で学習者に伝わらない）に戻さない（§9.5）
        assert "賭けているもの" not in js
        assert "理論のバックボーン" in js
        assert "最初の一手" in js

    def test_fragile_points_are_split_by_subject(self):
        """投影の是正（discuss_opening_authoring_design.md §2/§3）: 旧「最も脆い一手」は
        論文についての言明とシステム（解析）についての言明を1区画に混ぜていた。
        主語ごとの2区画に分け、混合見出しには戻さない。詳細は
        test_discuss_opening_projection.py（TestFragileSubjectSeparationInUi）。"""
        js = _read(DISCUSS_JS)
        assert "この論文が確かめていないこと" in js
        assert "まだ確認できていないところ" in js
        assert "最も脆い一手" not in js

    def test_free_input_note_present(self):
        js = _read(DISCUSS_JS)
        assert "自由に入力してもかまいません。" in js

    def test_opening_note_previews_dialogue_flow(self):
        """discuss_dialogue_alignment_design.md §6: 開幕ノートは対話の進行の型
        （まず理解の突き合わせ → それから一緒に検討）を予告する。序盤の言い直し・
        確認の往復を学習者が冗長と感じないための静的文言。数値は含めない（DM6）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function buildOpeningHtml(data) {")
        note_start = block.index('discuss-opening-note')
        note = block[note_start:block.index("</div>'", note_start)]
        assert "突き合わせ" in note
        assert "一緒に検討" in note
        # 命令形・煽り・数値は入れない（D層禁止語彙 / DM6）。
        for banned in ("疑え", "崩壊させよ", "%", "件"):
            assert banned not in note

    def test_backbone_node_click_starts_conversation(self):
        """ノードクリック = そこから対話開始（sendPrompt 経由・事実ベースの定型文）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBackboneSection(doc) {")
        assert "data-discuss-ask" in block
        assert "位置づけと根拠を教えてください" in _extract_function_body(js, "function askText(label) {")

    def test_action_starts_above_the_fold(self):
        """行動の起点（最初の一手）を開幕画面の最上部に置く。以前は最下部にあり、
        初見の学習者が最初に取れる操作がスクロール外だった。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function buildOpeningHtml(data) {")
        idx_first_move = block.index("renderFirstMoveSection()")
        idx_docs = block.index("docs.forEach(")
        assert idx_first_move < idx_docs

    def test_each_section_has_plain_language_subline(self):
        """A層の語彙（バックボーン・支持構造）を学習者の言葉へ橋渡しする1行を持つ。"""
        js = _read(DISCUSS_JS)
        assert "discuss-section-sub" in js
        assert "この論文がいちばん言いたいことです。" in js
        assert "この論文は、この順に組み立てられています。" in js
        assert "ボタンを押すと、その話題から質問が始まります。" in js

    def test_central_claim_is_readable_block_not_a_chip(self):
        """中心命題（claim の label＝論文原文の1文）はチップに詰めず、
        引用ブロックとして読ませる（既定3行クランプ + 明示操作で全文）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function claimBlockHtml(item) {")
        assert "discuss-claim-text" in block
        assert "この主張について聞く" in block
        assert "data-discuss-expand" in block
        css = _read(STYLES_CSS)
        clamp_rule = css.split(".discuss-claim-text {")[1].split("}")[0]
        assert "line-clamp: 3" in clamp_rule

    def test_claim_text_is_not_summarized_or_translated_client_side(self):
        """開幕画面は非LLM・既存成果の投影のみ（DM8）。原文を勝手に要約・和訳しない。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function claimBlockHtml(item) {")
        assert "esc(label)" in block
        for forbidden in ("summar", "translate", "slice(0,"):
            assert forbidden not in block

    def test_backbone_renders_stage_label_once_as_a_flow(self):
        """main ノードの label は stage 名そのもの（CLAUDE.md #308）なので、
        stage_label と label を両方描くと同じ文字が二重に出る。日本語 stage_label を
        優先し、矢印つなぎの流れとして描く。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBackboneSection(doc) {")
        assert "n.stage_label) || (n && n.label)" in block
        assert "discuss-backbone-stage" not in block
        assert "discuss-backbone-flow" in block
        assert "discuss-backbone-arrow" in block

    def test_backbone_review_dashed_border_has_a_fact_legend(self):
        """点線の意味を凡例1行で説明する（見た目の差だけでは学習者に読めない）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBackboneSection(doc) {")
        assert "点線は、根拠がまだ確認されていない段階です。" in block
        # 凡例は該当ノードがあるときだけ出す
        assert "if (hasReview) {" in block

    def test_support_sections_collapse_into_one_details(self):
        """支持構造は区画ごとに details を開かず1つに畳む（閉じた状態で A層の
        分類語が縦積みになるのを避ける）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderSupportDetails(sections) {")
        assert block.count("<details") == 1
        assert "支持構造をくわしく見る" in block

    def test_review_status_gets_subtle_visual_difference_not_alarm_color(self):
        """review_status/source_backing_status が source_backed でないノードは
        点線枠などの控えめな差にとどめ、警告色・強調色にしない（設計 §3.3）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBackboneSection(doc) {")
        assert "source_backed" in block
        assert "discuss-backbone-node--review" in block
        css = _read(STYLES_CSS)
        review_rule = css.split(".discuss-backbone-node--review {")[1].split("}")[0]
        assert "dashed" in review_rule
        for alarm_color in ("red", "#f00", "warning"):
            assert alarm_color not in review_rule.lower()


class TestOpeningScreenFailClosed:
    """開幕画面は API 失敗・available:false・documents 空のとき教材区画を
    上書きしない（Phase 1 プレースホルダのまま fail-closed に縮退する）。"""

    def test_render_opening_checks_ok_before_replacing(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function renderOpening(containerEl, courseId) {")
        assert "if (!res.ok) return;" in block

    def test_render_opening_checks_available_flag(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function renderOpening(containerEl, courseId) {")
        assert "if (!data || !data.available) return;" in block

    def test_render_opening_checks_empty_documents(self):
        """出せる中身が何も無ければ描画しない（Phase 1 プレースホルダのまま）。
        Phase 0b 以降は「教員が書いた course_focus だけがある」場合も中身として数える
        （サーバから来た実データであり、捏造ではない）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function buildOpeningHtml(data) {")
        assert "if (!docs.length && !focus) return" in block
        opening_block = _extract_function_body(js, "async function renderOpening(containerEl, courseId) {")
        assert "if (!html) return;" in opening_block

    def test_render_opening_never_renders_fixture_or_fake_data(self):
        js = _read(DISCUSS_JS)
        forbidden = ("ATLAS_FIXTURE", "fixture", "mock", "dummy")
        for word in forbidden:
            assert word not in js, f"discuss.js にフィクスチャ/偽データ由来の語 {word!r} が含まれている"

    def test_app_js_calls_render_opening_only_after_placeholder(self):
        """app.js 側: プレースホルダを描画してから renderOpening を呼ぶ順序
        （取得できなければプレースホルダのまま残る）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderMaterialRegion() {")
        idx_placeholder = block.index("renderDiscussPlaceholder()")
        idx_opening = block.index("window.Discuss.renderOpening(body, state.courseId)")
        assert idx_placeholder < idx_opening


class TestLandingScreen:
    """設計 §3.5: consolidation。討議終了時の軽量モーダル。スキップ可・既定オン。"""

    def test_three_section_headings_present(self):
        js = _read(DISCUSS_JS)
        assert "今日話した内容を地図に置く" in js
        assert "理解の確認" in js
        assert "このトピックで続きを学ぶ" in js

    def test_empty_candidates_fact_sentence(self):
        js = _read(DISCUSS_JS)
        assert "今回の対話からの候補はありません" in js
        assert "わたしの地図" in js

    def test_recon_probe_section_hidden_when_no_item(self):
        """再構成プローブは「あれば」1問。無ければ区画ごと非表示。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(
            js, "function buildLandingBodyHtml(tensionItems, anchorItems, reconItem) {"
        )
        assert "if (reconItem) {" in block

    def test_skip_button_present_and_always_available(self):
        js = _read(DISCUSS_JS)
        assert "discuss-landing-skip-btn" in js
        assert "スキップ" in js
        assert "スキップしても、ここまでの記録は残ります" in js

    def test_confirm_dismiss_actions_for_tension_and_anchor_cards(self):
        js = _read(DISCUSS_JS)
        assert "data-discuss-tension-confirm" in js
        assert "data-discuss-tension-dismiss" in js
        assert "data-discuss-anchor-confirm" in js
        assert "data-discuss-anchor-dismiss" in js

    def test_maybe_show_landing_suppresses_when_no_turns_or_recently_shown(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function maybeShowLanding(courseId, reason) {")
        assert "if (turnCount <= 0) return;" in block
        assert "SUPPRESS_MS" in block

    def test_inactivity_timeout_constant_is_fifteen_minutes(self):
        js = _read(DISCUSS_JS)
        assert "var INACTIVITY_MS = 15 * 60 * 1000;" in js

    def test_notify_activity_arms_timer_without_polling(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function notifyActivity() {")
        assert "armInactivityTimer" in block
        # ポーリング禁止（setInterval は使わない。setTimeout の張り直しのみ）
        assert "setInterval" not in js

    def test_continue_learning_button_is_informational_not_automatic(self):
        """「このトピックで続きを学ぶ」は情報的ボタンで自動遷移しない。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function bindLandingContentEvents(root, reconItem) {")
        assert "discuss-landing-continue-btn" in block
        assert "discussReturnToSequential" in block


class TestNoNumericScoresOrHypeInDiscussJs:
    """DM6: 数値・件数・網羅率を学習者に見せない。煽り文句・ゲーミフィケーションもしない。"""

    FORBIDDEN_SUBSTRINGS = ("confidence", "スコア", "達成率", "踏破", "ランキング", "%")

    def test_no_forbidden_numeric_or_hype_tokens(self):
        js = _read(DISCUSS_JS)
        for token in self.FORBIDDEN_SUBSTRINGS:
            assert token not in js, f"discuss.js に禁止トークン {token!r} が含まれている"


class TestBranchChipsDiscussOnly:
    """設計 §3.4: 分岐チップ（深掘り／横展開）は discuss モードの応答直後にのみ出す。"""

    def test_branch_chips_defined_in_discuss_js(self):
        js = _read(DISCUSS_JS)
        assert "🔎 深掘り" in js
        assert "🧭 横展開" in js
        assert "discuss-branch-chips" in js

    def test_app_js_gates_branch_chips_behind_discuss_mode(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderAiContent(text, msg)")
        idx = block.index("window.Discuss.renderBranchChips()")
        # 直前の行が isDiscussMode() ガードであること
        preceding = block[:idx]
        guard_idx = preceding.rindex("if (isDiscussMode()")
        assert idx - guard_idx < 120

    def test_branch_chips_use_generic_suggest_btn_wiring(self):
        """discuss.js は自前のチャット区画バインディングを増やさず、既存の
        .suggest-btn 汎用配線（app.js の renderChat 内）に相乗りする。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBranchChips() {")
        assert "suggest-btn" in block
        assert "data-suggest=" in block


class TestActivityAndTriggerWiring:
    """討議終了トリガー3つの配線: ① 明示終了 ② discuss→通常トピック切替 ③ 無活動タイムアウト。"""

    def test_trigger_one_explicit_end_button_wired(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initDiscussUI() {")
        assert "discuss-end-btn" in block
        assert "maybeShowLanding(state.courseId, \"explicit\")" in block

    def test_trigger_two_topic_switch_hook_in_select_topic(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function selectTopic(topicId, opts) {")
        assert "_discussLeaving" in block
        assert 'maybeShowLanding(_discussLeavingCourseId, "topic_switch")' in block
        # 遷移自体はブロックしない: フックはトピック切替が完了した後（関数末尾）に呼ぶ
        idx_set = block.index("state.currentTopicId = topicId;")
        idx_hook = block.index('maybeShowLanding(_discussLeavingCourseId, "topic_switch")')
        assert idx_set < idx_hook

    def test_trigger_three_notify_activity_on_send_success(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function sendMessage(text, actionPayload) {")
        assert "window.Discuss.notifyActivity()" in block
        idx_ok = block.index("if (res.ok) {")
        idx_notify = block.index("window.Discuss.notifyActivity()")
        assert idx_ok < idx_notify

    def test_end_button_present_in_html(self):
        html = _read(INDEX_HTML)
        assert 'id="discuss-end-btn"' in html
        assert "議論を終える" in html


class TestCourseSwitchResetsDiscussState:
    """コース切替（switchCourse）で旧コースの discuss 内部状態（無活動タイマー・
    往復回数・ctx.courseId）を持ち越さない。持ち越すと、旧コースの discuss セッションの
    無活動タイムアウト（15分）が切替後の別コース画面上で着地モーダルとして誤発火する。"""

    def test_switch_course_calls_discuss_reset(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function switchCourse(courseId) {")
        assert "window.Discuss.reset()" in block

    def test_discuss_init_di_wired_from_app_js(self):
        """app.js が initApp から Discuss.init に現在コースを読める getter を注入する
        （着地判定のコース一致ガードの防御の二重化に使う）。"""
        js = _read(APP_JS)
        assert "window.Discuss.init(" in js
        assert "getActiveCourseId" in js

    def test_maybe_show_landing_has_active_course_guard(self):
        """防御の二重化: 発火時点のアプリの現在コースと discuss セッションのコースが
        一致しなければ何もしない（DI 未注入時は後方互換で常にチェックをスキップする）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function maybeShowLanding(courseId, reason) {")
        assert "getActiveCourseId" in block
        assert "activeCourseId !== ctx.courseId" in block
        # ガードは landing_shown メトリクス送信・往復回数リセットより前に判定する
        # （不一致なら landing_shown も送らない）。
        idx_guard = block.index("activeCourseId !== ctx.courseId")
        idx_metric = block.index('sendDiscussMetric("landing_shown"')
        assert idx_guard < idx_metric

    def test_discuss_reset_clears_stale_course_context(self):
        """reset() 自体も ctx.courseId を破棄する（reset 経路が単独でも安全なように）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "reset: function () {")
        assert 'ctx.courseId = "";' in block

    def test_discuss_reset_clears_landing_suppression_window(self):
        """reset() は抑制窓 lastShownAt も落とす。残すと切替後の新しいコースの着地画面が
        最初の SUPPRESS_MS（10分）だけ「直近に出した」と誤判定されて出なくなる。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "reset: function () {")
        assert "lastShownAt = 0;" in block

    def test_switch_course_resets_discuss_scope_to_default(self):
        """スコープ選択（course_sources / all_visible）もコースを跨いで持ち越さない。
        「閲覧できる周辺資料まで」は前のコースの文脈で選ばれた設定なので、別コースでは
        既定に戻す（画面表示と実検索範囲を一致させる, DM1）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function switchCourse(courseId) {")
        assert 'state.discussScope = "course_sources";' in block


class TestSeedPromptIsPostedAsAssistantTurn:
    """[B-F2] 開幕の「議論のきっかけ」（教員が承認した立場を求める問い）は、学習者の
    発話として送らずアシスタントの発話としてチャット欄へ置く。

    学習者メッセージとして送ると AI は発話タイプ別ルール1（質問には即答・出し惜しみ
    禁止）でその問いに自分で答えきってしまい、係留（学習者が先に立場を述べ、AI が
    revoice で言い直す）が起動しない（docs/features/discuss_dialogue_alignment_design.md
    §3-1）。注入は LLM を呼ばない（DM8）。
    """

    def test_app_js_exposes_post_seed_prompt(self):
        js = _read(APP_JS)
        assert "window.discussPostSeedPrompt = function (text) {" in js

    def test_post_seed_prompt_injects_assistant_role_and_focuses_input(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "window.discussPostSeedPrompt = function (text) {")
        # discuss モード限定（fail-closed）
        assert "if (!isDiscussMode()" in block
        # assistant ロールで積む（user として送らない）
        assert 'role: "assistant"' in block
        assert "discuss_prompt: true" in block
        # 書き直し・以降削除の truncate 境界計算と整合させるため id を焼き込む
        assert "genMsgId()" in block
        # 学習者の入力を促す
        assert "renderChat();" in block
        assert "input.focus()" in block
        # LLM を呼ばない（送信経路を通さない）
        assert "sendMessage" not in block
        assert "apiFetch" not in block

    def test_post_seed_prompt_replaces_an_unanswered_injected_turn(self):
        """別のきっかけを続けて押しても問いだけが積み上がらない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "window.discussPostSeedPrompt = function (text) {")
        assert "last.discuss_prompt" in block
        assert "msgs.pop()" in block

    def test_injected_turn_does_not_get_branch_chips(self):
        """立場の表明を求めているターンに「深掘り／横展開」を並べない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderAiContent(text, msg)")
        idx = block.index("window.Discuss.renderBranchChips()")
        guard = block[:idx]
        assert "msg.discuss_prompt" in guard[guard.rindex("if (isDiscussMode()"):]

    def test_injected_turn_is_visually_marked_with_a_fact_sentence(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderChat() {")
        assert "msg.discuss_prompt" in block
        assert "discuss-prompt-hint" in block
        css = _read(STYLES_CSS)
        assert ".mg.ai.discuss-prompt {" in css
        assert ".discuss-prompt-hint {" in css


class TestOpeningChipsCarryStructureAnchors:
    """[F-5] DM3 / 設計 §3.4: 開幕チップ・バックボーンノード起点の問いは、元要素の
    id を明示アンカー（structure_anchor 経路A = learner_selected）として添える。

    element_type の語彙は既存の方法A（app.js の materialAnchorElementType と同じ規約）
    に揃える — 数式のみ専用語彙 "formula" があり、claim / stage には無いので backend の
    既定フォールバック（"concept"）に委ねる（backend/core は変更しない）。
    """

    def test_element_type_mapping_follows_the_existing_convention(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function anchorElementType(kind) {")
        assert 'kind === "equation" ? "formula" : "concept"' in block

    def test_anchor_attrs_are_omitted_without_a_real_element_id(self):
        """agent が合成した文・定型文（最初の一手・中心命題の合成文）は id を持たない。
        存在しない要素 id を捏造してアンカーにしない。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function anchorAttrs(ref) {")
        assert 'if (!ref || !ref.id) return ""' in block
        ref_block = _extract_function_body(js, "function itemRef(item, kind) {")
        assert "if (!item || !item.id) return null;" in ref_block

    def test_backbone_nodes_anchor_on_node_id(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBackboneSection(doc) {")
        assert "anchorAttrs(" in block
        assert "n.node_id" in block

    def test_claim_and_equation_chips_anchor_on_their_ids(self):
        js = _read(DISCUSS_JS)
        claim_block = _extract_function_body(js, "function claimBlockHtml(item) {")
        assert 'anchorAttrs(itemRef(item, "claim"))' in claim_block
        thesis_block = _extract_function_body(js, "function renderThesisSection(doc) {")
        assert 'itemRef(e, "equation")' in thesis_block

    def test_bind_opening_events_sends_the_anchor_fields(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function bindOpeningEvents(containerEl) {")
        for field in ("element_id:", "element_type:", "element_label:"):
            assert field in block, field
        # アンカーが無いチップは従来どおり自由文のみで送る
        assert "if (!elId) { window.sendPrompt(text); return; }" in block


class TestVoiceDisabledInDiscussMode:
    """[F-2] DM1 / 設計 §6.4: 音声版 discuss は Phase 3 の非スコープ。音声経路は
    intent_mode:"casual" 固定で送るため、discuss 中に使うと画面のスコープ表示と実検索
    範囲が食い違い（DM1 違反）、痕跡も casual として記録されて観測基盤の
    entry_mode='discuss' 集計から漏れる。開始を塞ぐ（fail-closed）。
    """

    def test_start_voice_mode_refuses_in_discuss(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function startVoiceMode() {")
        assert "if (isDiscussMode()) {" in block
        idx_guard = block.index("if (isDiscussMode()) {")
        idx_media = block.index("getUserMedia")
        assert idx_guard < idx_media

    def test_update_voice_availability_disables_button_and_stops_active_session(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function updateVoiceAvailability() {")
        assert "btn.disabled = blocked;" in block
        # 既にハンズフリー中に discuss へ切り替えたらセッションを終了する
        assert "stopVoiceMode()" in block
        assert "voiceState.active" in block

    def test_voice_segment_never_sends_casual_while_in_discuss(self):
        """文字起こし中にモードが切り替わっても casual として送らない（fail-closed）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function handleVoiceSegment() {")
        idx_guard = block.index("if (isDiscussMode()) {")
        idx_send = block.index('intent_mode: "casual"')
        assert idx_guard < idx_send

    def test_availability_is_refreshed_from_the_single_layout_entry_point(self):
        """モード切替・トピック遷移・会話描画のすべてが applyDiscussLayout を通る。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function applyDiscussLayout(")
        assert "updateVoiceAvailability();" in block

    def test_fact_sentence_is_shown_near_the_composer(self):
        js = _read(APP_JS)
        html = _read(INDEX_HTML)
        assert "音声会話は「順番に学ぶ」モードで利用できます。" in js
        assert 'id="discuss-voice-note"' in html
        # 既定は非表示（discuss 中だけ出す）
        idx = html.index('id="discuss-voice-note"')
        assert "hidden" in html[idx:idx + 160]
        css = _read(STYLES_CSS)
        assert ".discuss-voice-note {" in css
        assert ".voice-mode-btn:disabled {" in css


class TestLandingConnectNote:
    """設計 §9.1 軌道修正 #5: 着地モーダルは connect 機能そのものは持たず、
    「わたしの地図」の既存導線で行える旨を事実文で注記する。"""

    def test_connect_fact_sentence_present(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(
            js, "function buildLandingBodyHtml(tensionItems, anchorItems, reconItem) {"
        )
        assert "接続" in block
        assert "わたしの地図" in block


class TestIndexHtmlWiring:
    def test_discuss_js_script_tag_present(self):
        html = _read(INDEX_HTML)
        assert '<script src="/js/discuss.js"></script>' in html
        # reconstruction.js より後に読み込む（既存の並び順を踏襲）
        idx_recon = html.index('<script src="/js/reconstruction.js"></script>')
        idx_discuss = html.index('<script src="/js/discuss.js"></script>')
        assert idx_recon < idx_discuss

    def test_landing_modal_root_present_and_hidden_by_default(self):
        html = _read(INDEX_HTML)
        assert 'id="discuss-landing-region"' in html
        block = html[html.index('id="discuss-landing-region"') - 80: html.index('id="discuss-landing-region"') + 120]
        assert "hidden" in block


class TestStylesCssPhase2:
    def test_phase2_css_classes_defined(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".discuss-opening",
            ".discuss-section",
            ".discuss-chip",
            ".discuss-backbone-list",
            ".discuss-backbone-node",
            ".discuss-backbone-flow",
            ".discuss-backbone-arrow",
            ".discuss-section-sub",
            ".discuss-claim",
            ".discuss-claim-text",
            ".discuss-support-group",
            ".discuss-branch-chips",
            ".discuss-end-btn",
            ".discuss-landing-overlay",
            ".discuss-landing-panel",
            ".discuss-landing-card",
            ".discuss-landing-skip-btn",
        ):
            assert selector in css, f"missing CSS selector: {selector}"


class TestNoDetourOrShameVocabInDiscussJs:
    """discuss.js 全体に「寄り道」「疑え」という語が一切含まれない。"""

    def test_no_detour_word(self):
        js = _read(DISCUSS_JS)
        assert "寄り道" not in js

    def test_no_doubt_imperative_word(self):
        js = _read(DISCUSS_JS)
        assert "疑え" not in js
