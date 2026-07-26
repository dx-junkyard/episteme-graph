"""学習者側UX修正（vision_ux_gap_survey_2026-07.md 由来）の静的ガードレール。

対応する問題ID:
  T1 (G1-2)  initApp 二重定義の解消
  T2 (G1-4)  コース完走時の完了体験
  T4 (G4-T)  tension/anchor ダイジェストの気づき導線（進捗タブのドット）
  T5 (G3)    fail-closed の「空」と「壊れ」の区別（tension/anchor digest・
             reconstruction・旅カード）
  T6 (G4-L)  レクチャー無効理由の視認性
  T7 (G3-P1) わたしの地図（コーススコープ）の地図非依存化（コースフィルタ）
  T8 (G7-J)  旅カードの行き止まり解消

T3 (G1-5 / G4-D, 受講登録の確認 + description 表示) は既存の
test_course_selector_ui.py に統合済み（初期実装から存在する enrollCourse 系
テストの隣に置くのが自然なため）。

既存の test_personal_map_ui_guardrails.py / test_personal_map_home_ui_static.py と
同様、フロントエンドソースの静的検証で受け入れ条件を固定する（実ブラウザ・実APIには
依存しない — このリポジトリの実行環境に Node.js が無いため）。
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
APP_JS = FRONTEND_DIR / "js" / "app.js"
PERSONAL_MAP_JS = FRONTEND_DIR / "js" / "personal-map.js"
PERSONAL_MAP_HOME_JS = FRONTEND_DIR / "js" / "personal-map-home.js"
RECONSTRUCTION_JS = FRONTEND_DIR / "js" / "reconstruction.js"
INDEX_HTML = FRONTEND_DIR / "index.html"
STYLES_CSS = FRONTEND_DIR / "css" / "styles.css"

FORBIDDEN_WORDS = ("踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    """app.js/personal-map*.js の2-spaceインデント関数本体を切り出す。

    既存テスト群 (test_personal_map_ui_guardrails.py 等) と同じ様式:
    `  function <name>(...) {` から最初の `\n  }\n` まで（非貪欲）。
    async 関数にも対応するため `function` の前の修飾語は任意とする。
    """
    match = re.search(
        r"\n  (?:async )?function " + re.escape(name) + r"\(.*?\n  \}\n",
        src,
        re.S,
    )
    assert match, f"{name} 関数が見つかりません"
    return match.group(0)


# ---------------------------------------------------------------------------
# T1 (G1-2): initApp 二重定義の解消
# ---------------------------------------------------------------------------


class TestInitAppSingleDefinition:
    def test_init_app_defined_exactly_once(self):
        """app.js の initApp 定義は1つだけであること（後勝ちのデッドコードを残さない）。"""
        src = _read(APP_JS)
        defs = re.findall(r"\basync function initApp\s*\(", src)
        assert len(defs) == 1, f"initApp の定義が {len(defs)} 個あります（1個であるべき）"

    def test_init_app_wires_groups_and_invitation_badge(self):
        """実行される initApp が initGroups() / loadInvitationBadge() の両方を呼ぶこと
        （旧デッドコード側だけが持っていた配線を生きている側へ統合する）。"""
        body = _extract_function(_read(APP_JS), "initApp")
        assert "initGroups()" in body
        assert "loadInvitationBadge()" in body
        # 既存のレクチャー/地図関連の配線も引き続き残っていること（後勝ち側の退行防止）。
        assert "initLectureMode()" in body
        assert "initSplitHandle()" in body
        assert "initCourseSelector()" in body

    def test_groups_button_wired_to_open_groups_modal(self):
        """#groups-btn クリックで openGroupsModal が動く配線が生きていること。"""
        src = _read(APP_JS)
        init_groups_body = _extract_function(src, "initGroups")
        assert "groups-btn" in init_groups_body
        assert "openGroupsModal" in init_groups_body


# ---------------------------------------------------------------------------
# T2 (G1-4): コース完走時の完了体験
# ---------------------------------------------------------------------------


class TestCourseCompletionCard:
    def test_show_course_completion_card_exists(self):
        src = _read(APP_JS)
        assert "function showCourseCompletionCard" in src

    def test_completion_card_has_no_forbidden_vocabulary(self):
        """数値・スコア・踏破率・祝祭的ゲーミフィケーション演出を出さない（設計不変条項）。"""
        body = _extract_function(_read(APP_JS), "showCourseCompletionCard")
        for word in FORBIDDEN_WORDS:
            assert word not in body, f"禁止語彙 {word!r} が完了カードに含まれています"
        for word in ("おめでとう", "🎉", "祝", "レベルアップ"):
            assert word not in body, f"祝祭的な演出語彙 {word!r} が完了カードに含まれています"

    def test_completion_card_offers_other_courses_and_my_map_links(self):
        body = _extract_function(_read(APP_JS), "showCourseCompletionCard")
        assert "他のコースを見る" in body
        assert "わたしの地図を見る" in body
        assert "PersonalMapHome.open()" in body

    def test_next_topic_button_shows_complete_label_at_final_topic(self):
        """最終トピック（next が無い）でもボタンを隠さず「確認して完了」を出す。"""
        body = _extract_function(_read(APP_JS), "updateNextTopicBtn")
        assert '確認して完了' in body
        # 最終トピックでも btn.style.display = "none" にしない
        # （currentTopicId が無いときの非表示条件とは独立して判定していること）。
        assert 'if (!state.currentTopicId)' in body

    def test_open_check_modal_no_longer_blocks_on_missing_next(self):
        """openCheckModal が「次が無ければ即 return」で最終トピックをブロックしないこと。"""
        body = _extract_function(_read(APP_JS), "openCheckModal")
        assert "if (!next) return" not in body

    def test_submit_check_answer_shows_completion_card_when_no_next(self):
        """合格時・直接前進時ともに、次トピックが無ければ完了カードを出すこと。"""
        body = _extract_function(_read(APP_JS), "submitCheckAnswer")
        assert body.count("showCourseCompletionCard(") >= 2

    def test_lecture_complete_routes_to_check_modal_even_at_final_topic(self):
        """レクチャー完了バナーは最終トピックでも「確認して完了」で同じ確認フローへ合流する。"""
        body = _extract_function(_read(APP_JS), "onLectureComplete")
        assert "確認して完了" in body
        assert "openCheckModal" in body


# ---------------------------------------------------------------------------
# レビュー指摘1 (P1): 完了カードのサーバー状態 gating
#   - 途中を飛ばしても「全トピックを学習しました」と断定しない
#   - courseCompleted は /check レスポンスの course_completed（サーバー正本）でのみ真
# ---------------------------------------------------------------------------


class TestCourseCompletionCardServerGating:
    def test_show_course_completion_card_takes_course_completed_param(self):
        src = _read(APP_JS)
        assert re.search(
            r"function showCourseCompletionCard\(\s*completedTopic,\s*courseCompleted\s*\)",
            src,
        ), "showCourseCompletionCard は第2引数 courseCompleted を取ること"

    def test_completion_card_branches_on_course_completed(self):
        """courseCompleted が falsy のときは断定文言を出さず、事実文のみの縮退版にする。"""
        body = _extract_function(_read(APP_JS), "showCourseCompletionCard")
        assert "courseCompleted" in body
        assert "まだ確認を終えていないトピックがあります" in body
        assert "全トピックを学習しました" in body
        # 縮退版でも件数・割合は出さない（踏破率を数値にしない原則）。
        for word in FORBIDDEN_WORDS:
            assert word not in body

    def test_submit_check_answer_stores_and_uses_server_course_completed(self):
        """合格経路: data.course_completed===true のときだけ true を渡し、応答受信時に
        state.lastCheckCourseCompleted へ現況を保存する（data-advance 経路が参照する）。"""
        body = _extract_function(_read(APP_JS), "submitCheckAnswer")
        assert "state.lastCheckCourseCompleted = !!data.course_completed" in body
        assert "showCourseCompletionCard(completedTopic, data.course_completed === true)" in body

    def test_submit_check_answer_direct_advance_uses_last_check_flag(self):
        """不合格→「理解したので次へ」の data-advance 経路（サーバー応答なし）は、
        直前の check 応答が保存した state.lastCheckCourseCompleted を渡す。"""
        body = _extract_function(_read(APP_JS), "submitCheckAnswer")
        assert "showCourseCompletionCard(directCompleted, state.lastCheckCourseCompleted)" in body

    def test_last_check_course_completed_reset_on_topic_switch(self):
        """トピック切替（selectTopic）で古いコース/トピックの完了現況を持ち越さない。"""
        body = _extract_function(_read(APP_JS), "selectTopic")
        assert "state.lastCheckCourseCompleted = false" in body

    def test_last_check_course_completed_reset_on_course_switch(self):
        src = _read(APP_JS)
        switch_match = re.search(
            r"async function switchCourse.*?\n(.*?)await loadAndRenderCourse",
            src,
            re.DOTALL,
        )
        assert switch_match, "switchCourse function body should exist"
        assert "state.lastCheckCourseCompleted = false" in switch_match.group(1)

    def test_sidebar_shows_confirmed_marker_from_progress(self):
        """リロード後の復元: progress.completed_topic_ids に含まれるトピックへ
        控えめな確認済みマークを付ける。"""
        body = _extract_function(_read(APP_JS), "renderSidebar")
        assert "completed_topic_ids" in body
        assert "ni-done" in body
        assert "確認済み" in body

    def test_sidebar_shows_course_completed_fact_line_without_numbers(self):
        """progress.course_completed が true なら一行の事実文を出す（数値は出さない）。"""
        body = _extract_function(_read(APP_JS), "renderSidebar")
        assert "course_completed" in body
        assert "全トピックの確認を終えています" in body
        for word in FORBIDDEN_WORDS:
            assert word not in body

    def test_css_defines_sidebar_completion_markers(self):
        css = _read(STYLES_CSS)
        assert ".ni-done" in css
        assert ".course-tree-done" in css


# ---------------------------------------------------------------------------
# レビュー指摘3 (P2): コース切替時の digest/state 汚染防止
# ---------------------------------------------------------------------------


class TestCourseSwitchDigestReset:
    def test_switch_course_resets_digest_and_trace_state(self):
        """switchCourse がコースA由来の候補カード・気づきドットをコースBへ残さないよう、
        interestTraces/tensionDigest/anchorDigest/tensionDeferred/anchorDeferred を
        state 宣言時の初期値と同じ形へ戻すこと。"""
        src = _read(APP_JS)
        switch_match = re.search(
            r"async function switchCourse.*?\n(.*?)await loadAndRenderCourse",
            src,
            re.DOTALL,
        )
        assert switch_match
        body = switch_match.group(1)
        assert "state.interestTraces = null" in body
        assert "state.tensionDigest = null" in body
        assert "state.tensionDeferred = {}" in body
        assert "state.anchorDigest = null" in body
        assert "state.anchorDeferred = {}" in body

    def test_load_interest_traces_guards_stale_course_response(self):
        body = _extract_function(_read(APP_JS), "loadInterestTraces")
        assert "var cid = state.courseId" in body or "const cid = state.courseId" in body
        assert "state.courseId !== cid" in body
        # 応答反映（renderProgressTab/updateProgressTabDot）より前にガードがあること。
        guard_idx = body.index("state.courseId !== cid")
        render_idx = body.index("renderProgressTab()")
        assert guard_idx < render_idx

    def test_load_tension_digest_guards_stale_course_response(self):
        body = _extract_function(_read(APP_JS), "loadTensionDigest")
        assert "var cid = state.courseId" in body or "const cid = state.courseId" in body
        assert "state.courseId !== cid" in body

    def test_load_anchor_digest_guards_stale_course_response(self):
        body = _extract_function(_read(APP_JS), "loadAnchorDigest")
        assert "var cid = state.courseId" in body or "const cid = state.courseId" in body
        assert "state.courseId !== cid" in body


# ---------------------------------------------------------------------------
# T4 (G4-T): tension/anchor ダイジェストの気づき導線
# ---------------------------------------------------------------------------


class TestProgressTabDigestDot:
    def test_update_progress_tab_dot_checks_digests(self):
        body = _extract_function(_read(APP_JS), "updateProgressTabDot")
        assert "state.tensionDigest" in body
        assert "state.anchorDigest" in body
        assert "lx-tab-dot-hint" in body

    def test_render_progress_tab_refreshes_dot(self):
        """renderProgressTab の呼び出し経路すべてでドットが再評価されること
        （dismiss/defer/確定 いずれも renderProgressTab 経由で同期する）。"""
        body = _extract_function(_read(APP_JS), "renderProgressTab")
        assert "updateProgressTabDot()" in body

    def test_css_hint_dot_distinct_from_revisit_dot(self):
        css = _read(STYLES_CSS)
        assert ".lx-tab-dot-hint" in css
        # 既存の再訪推奨ドットとは別の色変数を使っていること（視覚的に区別できる）。
        hint_rule = re.search(r"\.lx-tab-dot-hint\s*\{[^}]*\}", css)
        assert hint_rule
        assert "--lx-revisit" not in hint_rule.group(0)


# ---------------------------------------------------------------------------
# T5 (G3): fail-closed の「空」と「壊れ」の区別
# ---------------------------------------------------------------------------


class TestDigestEmptyVsErrorStates:
    def test_load_tension_digest_marks_error_distinctly(self):
        body = _extract_function(_read(APP_JS), "loadTensionDigest")
        assert "_error" in body

    def test_load_anchor_digest_marks_error_distinctly(self):
        body = _extract_function(_read(APP_JS), "loadAnchorDigest")
        assert "_error" in body

    def test_render_tension_digest_card_distinguishes_empty_and_error(self):
        body = _extract_function(_read(APP_JS), "renderTensionDigestCard")
        assert "digest._error" in body
        assert "取得できませんでした" in body
        assert "まだありません" in body

    def test_render_anchor_digest_card_distinguishes_empty_and_error(self):
        body = _extract_function(_read(APP_JS), "renderAnchorDigestCard")
        assert "digest._error" in body
        assert "取得できませんでした" in body
        assert "まだありません" in body

    def test_reconstruction_empty_state_explains_reason(self):
        """再構成: 「まだありません」に加え、なぜ無いかの理由説明を足す。"""
        src = _read(RECONSTRUCTION_JS)
        assert "教員が承認した主張" in src

    def test_personal_map_journey_card_distinguishes_fetch_error(self):
        """旅カード（コースビュー）: 404/空（fail-closed）とは別に、通信エラーを
        _fetch_error として区別し、専用の文言を出す。"""
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "renderJourneyCard")
        assert "_fetch_error" in fn_src
        assert "読み込めませんでした" in fn_src
        fetch_fn = _extract_function(src, "fetchJourney")
        assert "_fetch_error" in fetch_fn
        assert "404" in fetch_fn

    def test_personal_map_home_journey_area_distinguishes_fetch_error(self):
        """旅カード（わたしの地図トップ）でも同様に区別する。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        fn_src = _extract_function(src, "renderJourneyArea")
        assert "_fetch_error" in fn_src
        assert "読み込めませんでした" in fn_src
        fetch_fn = _extract_function(src, "fetchJourney")
        assert "_fetch_error" in fetch_fn
        assert "404" in fetch_fn


# ---------------------------------------------------------------------------
# T6 (G4-L): レクチャー無効理由の視認性
# ---------------------------------------------------------------------------


class TestLectureToggleHint:
    def test_index_html_has_lecture_toggle_hint_element(self):
        html = _read(INDEX_HTML)
        assert 'id="lecture-toggle-hint"' in html
        assert "hidden" in re.search(r'<span[^>]*id="lecture-toggle-hint"[^>]*>', html).group(0)

    def test_update_lecture_toggle_availability_sets_hint_text(self):
        body = _extract_function(_read(APP_JS), "updateLectureToggleAvailability")
        assert "lecture-toggle-hint" in body
        assert "音声未生成" in body

    def test_css_defines_lecture_toggle_hint_style(self):
        """学習UI再編 Phase 1（§3.2）で lecture-toggle-hint は教材ヘッダの
        .material-format-toggle 内へ移設された（旧 .lecture-toggle-wrap の縦積み
        レイアウトは廃止）。"""
        css = _read(STYLES_CSS)
        assert ".lecture-toggle-hint" in css
        assert ".material-format-toggle" in css


# ---------------------------------------------------------------------------
# T7 (G3-P1): わたしの地図（コーススコープ）の地図非依存化 — コースフィルタ
# ---------------------------------------------------------------------------


class TestPersonalMapHomeCourseFilter:
    def test_course_filter_state_and_helpers_present(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "courseFilter" in src
        assert "function distinctCourseIds" in src
        assert "function filteredNodes" in src
        assert "function renderCourseFilter" in src

    def test_course_filter_only_applies_to_now_tab(self):
        """コースフィルタは「いまの地図」タブ (renderNow) にのみ影響し、他タブの
        描画関数はフィルタ済みノードを使わない（提案どおりスコープを絞る）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        now_body = _extract_function(src, "renderNow")
        assert "filteredNodes(" in now_body
        journeys_body = _extract_function(src, "renderJourneys")
        assert "filteredNodes(" not in journeys_body
        reflect_body = _extract_function(src, "renderReflect")
        assert "filteredNodes(" not in reflect_body

    def test_course_filter_does_not_count_candidates(self):
        """PN-3/PN-4: フィルタ選択肢の導出は candidate を数えない・件数を出さない
        （distinctCourseIds はコース ID の重複排除だけを行う）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        fn_src = _extract_function(src, "distinctCourseIds")
        for word in FORBIDDEN_WORDS:
            assert word not in fn_src
        filter_fn = _extract_function(src, "renderCourseFilter")
        for word in FORBIDDEN_WORDS:
            assert word not in filter_fn
        # 件数・%表示を匂わせる終端パターンが無いこと（personal-map系テストの慣例）。
        forbidden_patterns = ['件"', "件<", "件、", "件）", "件)", '%"', "%<"]
        hits = [p for p in forbidden_patterns if p in filter_fn]
        assert not hits, f"数値表示の可能性がある文言が見つかりました: {hits}"

    def test_course_filter_change_wired_without_polling(self):
        """select の change イベントで再 fetch せず、クライアント側フィルタのみで
        再描画すること（ポーリング・追加 fetch 禁止）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        change_fn = _extract_function(src, "onOverlayChange")
        assert "data-pm-home-course-filter" in change_fn
        assert "fetch(" not in change_fn
        assert "setInterval" not in src

    def test_course_filter_reset_on_invalidate(self):
        body = _extract_function(_read(PERSONAL_MAP_HOME_JS), "invalidate")
        assert 'state.courseFilter = ""' in body

    def test_css_defines_course_filter_style(self):
        css = _read(STYLES_CSS)
        assert ".pm-home-course-filter" in css


# ---------------------------------------------------------------------------
# T8 (G7-J): 旅カードの行き止まり解消
# ---------------------------------------------------------------------------


class TestJourneyDeadEndRestart:
    def test_personal_map_journey_card_offers_restart_on_frontier(self):
        """旅がfrontier_note終端になったとき「別の問いから旅に出る」導線を出す。"""
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "renderJourneyCard")
        assert "別の問いから旅に出る" in fn_src
        # frontier_note の分岐内に配置されていること（行き止まり以外では出さない）。
        frontier_idx = fn_src.index("frontier_note")
        restart_idx = fn_src.index("別の問いから旅に出る")
        assert restart_idx > frontier_idx

    def test_personal_map_home_journey_area_offers_restart_on_frontier(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        fn_src = _extract_function(src, "renderJourneyArea")
        assert "別の問いから旅に出る" in fn_src
        frontier_idx = fn_src.index("frontier_note")
        restart_idx = fn_src.index("別の問いから旅に出る")
        assert restart_idx > frontier_idx

    def test_personal_map_home_restart_switches_to_journeys_tab(self):
        """わたしの地図トップの行き止まりからは「問いからの旅」タブ（ノード一覧）へ
        戻れること（別の問いを選び直せる）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        click_fn = _extract_function(src, "onOverlayClick")
        assert "data-pm-home-journey-restart" in click_fn
        assert 'switchTab("journeys")' in click_fn

    def test_restart_actions_have_no_forbidden_vocabulary(self):
        for path in (PERSONAL_MAP_JS, PERSONAL_MAP_HOME_JS):
            src = _read(path)
            for word in FORBIDDEN_WORDS:
                assert word not in src, f"{path.name}: 禁止語彙 {word!r} が見つかりました"


# ---------------------------------------------------------------------------
# N15 (vision_ux_gap_survey_2026-07-17 §5-7): 出典タブへの D層検証状態の一行併記
# ---------------------------------------------------------------------------


class TestSourcesTabLedgerLine:
    """出典タブの根拠カードに台帳（epistemic_ledger）の一行事実文を併記する（D3-6）。

    - component ポップアップと同じ文言・様式（fetchLearnerLedgerFactLine /
      makeLedgerLineDiv を共用）
    - 台帳未記帳 (404)・API失敗時は何も出さない fail-closed
    - 数値スコアを学習者に見せない
    """

    def test_render_sources_tab_triggers_ledger_annotation(self):
        src = _read(APP_JS)
        fn_src = _extract_function(src, "renderSourcesTab")
        assert "annotateSourceCardsWithLedger" in fn_src
        assert "data-lx-ledger-chunk" in fn_src

    def test_annotation_uses_shared_ledger_helpers(self):
        """component ポップアップ経路と同じ取得・整形ヘルパーを使う（文言・様式の同一性）。"""
        src = _read(APP_JS)
        card_fn = _extract_function(src, "annotateOneSourceCardLedger")
        assert "fetchLearnerLedgerFactLine" in card_fn
        assert "makeLedgerLineDiv" in card_fn
        popup_fn = _extract_function(src, "appendLearnerLedgerLine")
        assert "fetchLearnerLedgerFactLine" in popup_fn
        assert "makeLedgerLineDiv" in popup_fn

    def test_annotation_is_fail_closed(self):
        """台帳未記帳・失敗時はエラー文言を出さず何も描かない（fail-closed）。"""
        src = _read(APP_JS)
        card_fn = _extract_function(src, "annotateOneSourceCardLedger")
        assert "fail-closed" in card_fn
        # エラーの可視化・警告文言を出さない（catch は無言）
        assert "エラー" not in card_fn
        fetch_fn = _extract_function(src, "fetchLearnerLedgerFactLine")
        assert "return null" in fetch_fn or "= null" in fetch_fn

    def test_ledger_fact_line_has_no_numeric_leak(self):
        """一行事実文の描画は fact_line / scopes 由来テキストのみ（数値スコア非表示）。"""
        src = _read(APP_JS)
        fetch_fn = _extract_function(src, "fetchLearnerLedgerFactLine")
        assert "fact_line" in fetch_fn
        for banned in ("load_score", "confidence", "toFixed"):
            assert banned not in fetch_fn

    def test_non_pipeline_formula_ids_are_excluded(self):
        """旧フォーマット変換の [[FORMULA_N]] / TOPIC_FORMULA_N は台帳対象にしない。"""
        src = _read(APP_JS)
        card_fn = _extract_function(src, "annotateOneSourceCardLedger")
        assert "FORMULA_" in card_fn
        assert "TOPIC_FORMULA_" in card_fn


# ---------------------------------------------------------------------------
# N15 拡張: 出典タブの claim 併記（chunk→claim ID の学習者向け読み取り API）
# ---------------------------------------------------------------------------


class TestSourcesTabClaimLedgerLine:
    """equation に加え claim の台帳検証状態も出典カードへ一行併記する。

    - チャンク→claim ID の解決は学習者向け読み取り専用 API
      (`/learning/courses/{course_id}/chunks/{chunk_id}/claim-refs`) を使う
      （equation 経路の source-chunk API とは独立）
    - 台帳の取得・様式は equation と同じ共有ヘルパー
      (fetchLearnerLedgerFactLine / makeLedgerLineDiv) を使う
    - API 失敗・0件・台帳未記帳は何も出さない（fail-closed。equation 側の
      失敗が claim 側を巻き込まない、その逆も同様）
    - 1カードの上限3行は equation と claim の合算で維持する
    """

    def test_card_fetches_claim_refs_from_dedicated_endpoint(self):
        src = _read(APP_JS)
        card_fn = _extract_function(src, "annotateOneSourceCardLedger")
        assert "claim-refs" in card_fn

    def test_claim_ledger_line_uses_shared_helpers(self):
        src = _read(APP_JS)
        card_fn = _extract_function(src, "annotateOneSourceCardLedger")
        # "claim" は fetchLearnerLedgerFactLine の targetType として渡される
        assert '"claim"' in card_fn or "kind" in card_fn
        assert "fetchLearnerLedgerFactLine" in card_fn
        assert "makeLedgerLineDiv" in card_fn

    def test_equation_and_claim_share_the_three_line_cap(self):
        """equation と claim を合算した配列に対して 1回だけ 3行上限を適用する。"""
        src = _read(APP_JS)
        card_fn = _extract_function(src, "annotateOneSourceCardLedger")
        assert "count < 3" in card_fn
        # equation・claim それぞれ独立に 3件ループする旧実装には戻さない
        assert card_fn.count("< 3") == 1

    def test_claim_fetch_is_independently_fail_closed(self):
        """claim-refs 取得の失敗は equation 側の結果に影響しない（個別 try/catch）。"""
        src = _read(APP_JS)
        card_fn = _extract_function(src, "annotateOneSourceCardLedger")
        assert card_fn.count("fail-closed") >= 1
        assert "エラー" not in card_fn


# ---------------------------------------------------------------------------
# N32 (同 §5-7): ロックトピックの視覚と挙動の齟齬 — 事実文トースト
# ---------------------------------------------------------------------------


class TestLockedTopicFactToast:
    def test_locked_topic_click_shows_fact_and_still_navigates(self):
        """ロック表示トピックのクリックで事実文を出すが、遷移は従来どおり許可する。"""
        src = _read(APP_JS)
        fn_src = _extract_function(src, "renderSidebar")
        assert "maybeShowLockedTopicFact" in fn_src
        # 事実文表示の後も selectTopic を呼ぶ（遷移をブロックしない）
        toast_idx = fn_src.index("maybeShowLockedTopicFact")
        select_idx = fn_src.index("selectTopic", toast_idx)
        assert select_idx > toast_idx

    def test_fact_line_is_factual_and_conditional(self):
        """「前のトピックの確認が未完了です」は事実であるときだけ表示する。"""
        src = _read(APP_JS)
        fn_src = _extract_function(src, "maybeShowLockedTopicFact")
        assert "前のトピックの確認が未完了です" in fn_src
        # 前のトピックが確認済みなら出さない（completed_topic_ids を参照する）
        assert "completed_topic_ids" in fn_src
        # 数値・煽り語彙を出さない
        for banned in ("%", "スコア", "急いで", "ロックされています"):
            assert banned not in fn_src

    def test_fact_toast_styles_exist(self):
        css = _read(STYLES_CSS)
        assert ".fact-toast" in css
