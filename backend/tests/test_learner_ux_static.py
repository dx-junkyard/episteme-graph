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
        css = _read(STYLES_CSS)
        assert ".lecture-toggle-hint" in css
        assert ".lecture-toggle-wrap" in css


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
