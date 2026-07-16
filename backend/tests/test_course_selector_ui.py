"""Issue #54: 学習画面のコース選択プルダウン (<select> + <optgroup>) UIのテスト。

index.html の DOM 要素と app.js のコース切り替えロジックが
正しく実装されていることを検証する。
外部 API は一切呼び出さない。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Frontend DOM / JS tests (静的HTML/JS解析)
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
INDEX_HTML = FRONTEND_DIR / "index.html"
APP_JS = FRONTEND_DIR / "js" / "app.js"
STYLES_CSS = FRONTEND_DIR / "css" / "styles.css"


@pytest.fixture(autouse=True)
def _no_backend_settings():
    """フロントエンドテストではバックエンド設定のオーバーライドは不要。"""
    yield


# ---------------------------------------------------------------------------
# index.html DOM tests
# ---------------------------------------------------------------------------


class TestIndexHTMLCourseSelector:
    """index.html にコース選択UIに必要な DOM 要素が存在することを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_course_select_element_exists(self):
        """<select id="course-select"> が存在すること。"""
        assert 'id="course-select"' in self.html

    def test_course_select_is_select_tag(self):
        """コースセレクターが <select> タグであること。"""
        assert re.search(r"<select[^>]*id=\"course-select\"", self.html)

    def test_course_select_has_default_option(self):
        """デフォルトの option が存在すること。"""
        assert re.search(r"<option[^>]*>読み込み中", self.html)

    def test_course_select_in_topbar(self):
        """コースセレクターがトップバー内に配置されていること。"""
        topbar_match = re.search(
            r'class="topbar-l">(.*?)</div>',
            self.html,
            re.DOTALL,
        )
        assert topbar_match, "topbar-l should exist"
        assert "course-select" in topbar_match.group(1)

    def test_no_custom_dropdown_remnants(self):
        """旧カスタムドロップダウンの要素が残っていないこと。"""
        assert 'id="course-selector-btn"' not in self.html
        assert 'id="course-selector-menu"' not in self.html
        assert "course-selector-arrow" not in self.html

    def test_chat_input_exists(self):
        """チャット入力欄 (id=chat-input) が存在すること。"""
        assert 'id="chat-input"' in self.html

    def test_send_button_exists(self):
        """送信ボタン (id=send-btn) が存在すること。"""
        assert 'id="send-btn"' in self.html

    def test_chat_clear_button_exists(self):
        """質疑応答履歴削除ボタン (id=chat-clear-btn) が存在すること。"""
        assert 'id="chat-clear-btn"' in self.html
        assert "質疑応答履歴を削除" in self.html

    def test_chat_clear_button_is_in_right_panel(self):
        """履歴削除ボタンが右サイドパネル内に配置されていること。"""
        right_panel = re.search(r'<div class="rp">(.*?)</div>\s*</div>\s*<script', self.html, re.DOTALL)
        assert right_panel
        assert "rp-danger-zone" in right_panel.group(1)
        assert "chat-clear-btn" in right_panel.group(1)


# ---------------------------------------------------------------------------
# app.js logic tests
# ---------------------------------------------------------------------------


class TestAppJSCourseSelector:
    """app.js にコース選択・切り替えに必要な関数と処理が存在することを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_init_course_selector_exists(self):
        """initCourseSelector 関数が定義されていること。"""
        assert "async function initCourseSelector" in self.js

    def test_render_course_select_exists(self):
        """renderCourseSelect 関数が定義されていること。"""
        assert "function renderCourseSelect" in self.js

    def test_switch_course_exists(self):
        """switchCourse 関数が定義されていること。"""
        assert "async function switchCourse" in self.js

    def test_enroll_course_exists(self):
        """enrollCourse 関数が定義されていること。"""
        assert "async function enrollCourse" in self.js

    def test_init_course_select_handler_exists(self):
        """initCourseSelectHandler 関数が定義されていること。"""
        assert "function initCourseSelectHandler" in self.js

    def test_show_no_course_state_exists(self):
        """showNoCourseState 関数が定義されていること。"""
        assert "function showNoCourseState" in self.js

    def test_set_chat_enabled_exists(self):
        """setChatEnabled 関数が定義されていること。"""
        assert "function setChatEnabled" in self.js

    def test_clear_chat_history_function_exists(self):
        """質疑応答履歴削除関数が定義されていること。"""
        assert "async function clearChatHistory" in self.js
        assert '"DELETE"' in self.js

    def test_clear_chat_button_handler_exists(self):
        """履歴削除ボタンのクリックで clearChatHistory が呼ばれること。"""
        assert "chat-clear-btn" in self.js
        assert "clearBtn.addEventListener(\"click\", clearChatHistory)" in self.js

    def test_clear_chat_history_resets_local_messages(self):
        """削除成功後にローカルのチャット履歴を空にすること。"""
        assert "state.chatMessages = []" in self.js
        assert "質疑応答履歴を削除" in self.js

    def test_chat_clear_button_has_danger_zone_css(self):
        """履歴削除ボタンが薄赤色の危険操作スタイルを持つこと。"""
        css = STYLES_CSS.read_text(encoding="utf-8")
        assert ".rp-danger-zone" in css
        assert ".chat-clear-btn" in css
        assert "#fff5f5" in css

    def test_no_old_custom_dropdown_functions(self):
        """旧カスタムドロップダウンの関数が残っていないこと。"""
        assert "function renderCourseMenu" not in self.js
        assert "function initCourseDropdown" not in self.js
        assert "function closeCourseMenu" not in self.js

    def test_optgroup_my_courses(self):
        """renderCourseSelect で「マイコース」optgroup が生成されること。"""
        assert '<optgroup label="マイコース">' in self.js

    def test_optgroup_enrollable_courses(self):
        """renderCourseSelect で「新しく受講可能なコース」optgroup が生成されること。"""
        assert '<optgroup label="新しく受講可能なコース">' in self.js

    def test_enroll_prefix_in_value(self):
        """受講可能コースの value に "enroll:" プレフィックスが付与されること。"""
        assert 'value="enroll:' in self.js

    def test_change_handler_detects_enroll_prefix(self):
        """change イベントで "enroll:" プレフィックスを判定していること。"""
        assert 'val.indexOf("enroll:") === 0' in self.js

    def _init_course_select_handler_body(self):
        """initCourseSelectHandler 関数全体を切り出す（change ハンドラは内部に
        openEnrollConfirm のネストしたコールバックを持つため、単純な
        `\\}\\);` 終端の非貪欲マッチでは enrollCourse 呼び出し前に打ち切れてしまう。
        関数全体の境界（2-space インデントの閉じ括弧）で切り出す。"""
        match = re.search(
            r"function initCourseSelectHandler\(\).*?\n  \}\n",
            self.js,
            re.DOTALL,
        )
        assert match, "initCourseSelectHandler 関数が見つかりません"
        return match.group(0)

    def test_change_handler_calls_enroll_course(self):
        """enroll: プレフィックスの場合、確認モーダルを経て enrollCourse を呼び出すこと
        (G1-5: 確認なし即時 enroll をやめる)。"""
        body = self._init_course_select_handler_body()
        assert "enrollCourse" in body

    def test_change_handler_calls_switch_course(self):
        """マイコース選択時に switchCourse を呼び出すこと。"""
        body = self._init_course_select_handler_body()
        assert "switchCourse" in body

    def test_change_handler_confirms_before_enroll(self):
        """G1-5: enroll: 選択時に確認モーダル (openEnrollConfirm) を経由すること
        （確認なしの即時 enrollCourse 呼び出しに戻さない）。"""
        body = self._init_course_select_handler_body()
        assert "openEnrollConfirm" in body
        # enrollCourse の呼び出しは openEnrollConfirm のコールバック内（confirmed 分岐）にある。
        assert body.index("openEnrollConfirm") < body.index("enrollCourse")

    def test_change_handler_resets_select_on_cancel(self):
        """G1-5: キャンセル時は select を元の値に戻すこと。"""
        body = self._init_course_select_handler_body()
        assert "select.value = prevVal" in body

    def test_enroll_confirm_shows_title_and_description(self):
        """openEnrollConfirm がコースのタイトルと description を提示すること。"""
        match = re.search(
            r"function openEnrollConfirm\(.*?\n  \}\n",
            self.js,
            re.DOTALL,
        )
        assert match, "openEnrollConfirm 関数が見つかりません"
        body = match.group(0)
        assert "course.title" in body
        assert "course.description" in body
        assert "キャンセル" in body

    def test_switch_course_clears_state(self):
        """switchCourse がチャット履歴とトピックIDをクリアすること。"""
        switch_match = re.search(
            r"async function switchCourse.*?\n(.*?)await loadAndRenderCourse",
            self.js,
            re.DOTALL,
        )
        assert switch_match, "switchCourse function body should exist"
        body = switch_match.group(1)
        assert "state.currentTopicId = null" in body
        assert "state.chatMessages = []" in body
        assert "state.course = null" in body

    def test_switch_course_saves_to_localstorage(self):
        """switchCourse が localStorage にコースIDを保存すること。"""
        switch_match = re.search(
            r"async function switchCourse.*?\n(.*?)await loadAndRenderCourse",
            self.js,
            re.DOTALL,
        )
        assert switch_match
        assert 'localStorage.setItem("eg_course"' in switch_match.group(1)

    def test_switch_course_rerenders_ui(self):
        """switchCourse がサイドバー・チャット・右パネルを再描画すること。"""
        switch_match = re.search(
            r"async function switchCourse.*?\n(.*?)await loadAndRenderCourse",
            self.js,
            re.DOTALL,
        )
        assert switch_match
        body = switch_match.group(1)
        assert "renderSidebar()" in body
        assert "renderChat()" in body
        assert "renderRightPanel()" in body

    def test_no_course_state_disables_chat(self):
        """showNoCourseState がチャットを無効化すること。"""
        no_course_match = re.search(
            r"function showNoCourseState.*?\n(.*?)\n  \}",
            self.js,
            re.DOTALL,
        )
        assert no_course_match
        body = no_course_match.group(1)
        assert "setChatEnabled(false)" in body

    def test_no_course_state_disables_select(self):
        """showNoCourseState が select を disabled にすること。"""
        no_course_match = re.search(
            r"function showNoCourseState.*?\n(.*?)\n  \}",
            self.js,
            re.DOTALL,
        )
        assert no_course_match
        body = no_course_match.group(1)
        assert "select.disabled = true" in body

    def test_no_course_state_shows_message(self):
        """showNoCourseState が「受講可能なコースはありません」メッセージを表示すること。"""
        assert "現在受講可能なコースはありません" in self.js

    def test_courses_split_by_enrollable(self):
        """initCourseSelector でコースを is_enrollable で分離すること。"""
        assert "is_enrollable" in self.js

    def test_init_app_calls_init_course_selector(self):
        """initApp が initCourseSelector を呼び出すこと。"""
        init_app_match = re.search(
            r"async function initApp.*?\n(.*?)\n  \}",
            self.js,
            re.DOTALL,
        )
        assert init_app_match
        assert "initCourseSelector" in init_app_match.group(1)

    def test_enroll_refreshes_course_list(self):
        """enrollCourse がコース一覧を再取得すること。"""
        enroll_match = re.search(
            r"async function enrollCourse.*?\n(.*?)\n  \}",
            self.js,
            re.DOTALL,
        )
        assert enroll_match
        body = enroll_match.group(1)
        assert "loadCourses()" in body
        assert "switchCourse" in body

    def _enroll_course_body(self):
        match = re.search(
            r"async function enrollCourse.*?\n(.*?)\n  \}",
            self.js,
            re.DOTALL,
        )
        assert match, "enrollCourse 関数が見つかりません"
        return match.group(1)

    def _open_enroll_confirm_body(self):
        match = re.search(
            r"function openEnrollConfirm\(.*?\n  \}\n",
            self.js,
            re.DOTALL,
        )
        assert match, "openEnrollConfirm 関数が見つかりません"
        return match.group(0)

    # -----------------------------------------------------------------
    # レビュー指摘4 (P2): 受講登録失敗後に再試行できない
    # -----------------------------------------------------------------

    def test_enroll_course_throws_on_non_ok_response(self):
        """enrollCourse は非2xxを握りつぶさず throw すること
        （呼び出し元が select を復元し再試行できるようにするため）。"""
        body = self._enroll_course_body()
        assert "if (!res.ok)" in body
        assert "throw new Error" in body
        # 旧実装の「catch (_) { /* ignore */ }」型の握りつぶしに戻さない。
        assert "/* ignore */" not in body

    def test_enroll_course_does_not_swallow_exceptions(self):
        """enrollCourse 自体が例外を catch して握りつぶさないこと
        （通信例外はそのまま呼び出し元へ伝播させる）。"""
        body = self._enroll_course_body()
        assert "catch (_) { /* ignore */ }" not in body
        assert "catch (_) {}" not in body

    def test_enroll_course_returns_true_on_success(self):
        body = self._enroll_course_body()
        assert "return true;" in body

    def test_open_enroll_confirm_has_error_display_area(self):
        """openEnrollConfirm が失敗表示用の領域を持つこと（再試行可能な状態を保つ）。"""
        body = self._open_enroll_confirm_body()
        assert "enroll-confirm-error" in body

    def test_open_enroll_confirm_disables_buttons_while_busy(self):
        """通信中はキャンセル/OK 両ボタンを disabled にし、二重送信を防ぐこと。"""
        body = self._open_enroll_confirm_body()
        assert "cancelBtn.disabled = busy" in body or "cancelBtn.disabled = true" in body
        assert "okBtn.disabled = busy" in body or "okBtn.disabled = true" in body
        assert "受講手続き中" in body

    def test_open_enroll_confirm_reenables_buttons_on_failure(self):
        """失敗時にボタンを再有効化し、再試行できる状態に戻すこと。"""
        body = self._open_enroll_confirm_body()
        assert "setBusy(false)" in body
        assert "受講登録に失敗しました" in body

    def test_open_enroll_confirm_awaits_callback_before_closing(self):
        """OK 押下で即座に overlay を閉じず、callback(confirmed) の完了を待ってから
        閉じる/エラー表示を切り替えること。"""
        body = self._open_enroll_confirm_body()
        assert "await callback(true)" in body

    def test_change_handler_restores_select_on_enroll_failure(self):
        """change ハンドラは enrollCourse 失敗時にも select を prevVal へ戻すこと。"""
        body = self._init_course_select_handler_body()
        assert "catch (err)" in body
        # 失敗時の復元は confirmed 分岐（try/catch）内にあり、単にキャンセル分岐だけの
        # 話ではないことを確認する。
        confirmed_branch = body[body.index("if (confirmed)"):body.index("} else {")]
        assert "select.value = prevVal" in confirmed_branch


# ---------------------------------------------------------------------------
# CSS tests
# ---------------------------------------------------------------------------


class TestStylesCSSCourseSelector:
    """styles.css にコースセレクターのスタイルが定義されていることを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_css(self):
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_course_select_style_exists(self):
        """.course-select スタイルが定義されていること。"""
        assert ".course-select" in self.css

    def test_course_select_hover_style_exists(self):
        """.course-select:hover スタイルが定義されていること。"""
        assert ".course-select:hover" in self.css

    def test_course_select_focus_style_exists(self):
        """.course-select:focus スタイルが定義されていること。"""
        assert ".course-select:focus" in self.css

    def test_no_old_custom_dropdown_styles(self):
        """旧カスタムドロップダウンのスタイルが残っていないこと。"""
        assert ".course-selector-btn" not in self.css
        assert ".course-selector-menu" not in self.css
        assert ".course-menu-item" not in self.css
        assert ".course-menu-divider" not in self.css

    def test_no_course_message_style_exists(self):
        """.no-course-message スタイルが定義されていること。"""
        assert ".no-course-message" in self.css

    def test_enroll_confirm_error_style_exists(self):
        """レビュー指摘4: 受講登録失敗表示用の .enroll-confirm-error が定義されていること。"""
        assert ".enroll-confirm-error" in self.css
