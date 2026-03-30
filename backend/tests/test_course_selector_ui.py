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

    def test_change_handler_calls_enroll_course(self):
        """enroll: プレフィックスの場合に enrollCourse を呼び出すこと。"""
        handler_match = re.search(
            r'addEventListener\("change".*?\n(.*?)\}\);',
            self.js,
            re.DOTALL,
        )
        assert handler_match
        body = handler_match.group(1)
        assert "enrollCourse" in body

    def test_change_handler_calls_switch_course(self):
        """マイコース選択時に switchCourse を呼び出すこと。"""
        handler_match = re.search(
            r'addEventListener\("change".*?\n(.*?)\}\);',
            self.js,
            re.DOTALL,
        )
        assert handler_match
        body = handler_match.group(1)
        assert "switchCourse" in body

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
