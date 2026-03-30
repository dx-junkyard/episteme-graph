"""Issue #52: 学習画面のコース選択・切り替えUIのテスト。

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

    def test_course_selector_container_exists(self):
        """コースセレクター コンテナ (id=course-selector) が存在すること。"""
        assert 'id="course-selector"' in self.html

    def test_course_selector_button_exists(self):
        """コースセレクターボタン (id=course-selector-btn) が存在すること。"""
        assert 'id="course-selector-btn"' in self.html

    def test_course_name_span_exists(self):
        """コース名表示要素 (id=course-name) が存在すること。"""
        assert 'id="course-name"' in self.html

    def test_course_selector_menu_exists(self):
        """ドロップダウンメニュー (id=course-selector-menu) が存在すること。"""
        assert 'id="course-selector-menu"' in self.html

    def test_course_selector_arrow_exists(self):
        """ドロップダウン矢印が存在すること。"""
        assert "course-selector-arrow" in self.html

    def test_course_selector_in_topbar(self):
        """コースセレクターがトップバー内に配置されていること。"""
        topbar_match = re.search(
            r'class="topbar-l">(.*?)</div>',
            self.html,
            re.DOTALL,
        )
        assert topbar_match, "topbar-l should exist"
        assert "course-selector" in topbar_match.group(1)

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

    def test_render_course_menu_exists(self):
        """renderCourseMenu 関数が定義されていること。"""
        assert "function renderCourseMenu" in self.js

    def test_switch_course_exists(self):
        """switchCourse 関数が定義されていること。"""
        assert "async function switchCourse" in self.js

    def test_enroll_course_exists(self):
        """enrollCourse 関数が定義されていること。"""
        assert "async function enrollCourse" in self.js

    def test_init_course_dropdown_exists(self):
        """initCourseDropdown 関数が定義されていること。"""
        assert "function initCourseDropdown" in self.js

    def test_close_course_menu_exists(self):
        """closeCourseMenu 関数が定義されていること。"""
        assert "function closeCourseMenu" in self.js

    def test_show_no_course_state_exists(self):
        """showNoCourseState 関数が定義されていること。"""
        assert "function showNoCourseState" in self.js

    def test_set_chat_enabled_exists(self):
        """setChatEnabled 関数が定義されていること。"""
        assert "function setChatEnabled" in self.js

    def test_switch_course_clears_state(self):
        """switchCourse がチャット履歴とトピックIDをクリアすること。"""
        # switchCourse 内で state をクリアしている箇所を検証
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

    def test_no_course_state_shows_message(self):
        """showNoCourseState が「受講可能なコースはありません」メッセージを表示すること。"""
        assert "現在受講可能なコースはありません" in self.js

    def test_course_menu_has_own_courses_section(self):
        """renderCourseMenu にマイコースセクションが定義されていること。"""
        assert "マイコース" in self.js

    def test_course_menu_has_enrollable_section(self):
        """renderCourseMenu に受講可能なコースセクションが定義されていること。"""
        assert "受講可能なコース" in self.js

    def test_course_menu_binds_switch_click(self):
        """renderCourseMenu がコース切り替えクリックをバインドすること。"""
        assert 'data-course-id' in self.js

    def test_course_menu_binds_enroll_click(self):
        """renderCourseMenu が受講登録クリックをバインドすること。"""
        assert 'data-enroll-id' in self.js

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

    def test_courses_split_by_enrollable(self):
        """initCourseSelector でコースを is_enrollable で分離すること。"""
        assert "is_enrollable" in self.js

    def test_dropdown_toggle_on_click(self):
        """ドロップダウンがクリックでトグルされること。"""
        assert 'selector.classList.toggle("open")' in self.js

    def test_dropdown_closes_on_outside_click(self):
        """外部クリックでドロップダウンが閉じること。"""
        assert "closeCourseMenu" in self.js
        assert 'document.addEventListener("click"' in self.js


# ---------------------------------------------------------------------------
# CSS tests
# ---------------------------------------------------------------------------


class TestStylesCSSCourseSelector:
    """styles.css にコースセレクターのスタイルが定義されていることを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_css(self):
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_course_selector_style_exists(self):
        """.course-selector スタイルが定義されていること。"""
        assert ".course-selector" in self.css

    def test_course_selector_btn_style_exists(self):
        """.course-selector-btn スタイルが定義されていること。"""
        assert ".course-selector-btn" in self.css

    def test_course_selector_menu_style_exists(self):
        """.course-selector-menu スタイルが定義されていること。"""
        assert ".course-selector-menu" in self.css

    def test_course_menu_item_style_exists(self):
        """.course-menu-item スタイルが定義されていること。"""
        assert ".course-menu-item" in self.css

    def test_course_menu_active_style_exists(self):
        """.course-menu-item.active スタイルが定義されていること。"""
        assert ".course-menu-item.active" in self.css

    def test_course_menu_open_state_style(self):
        """.course-selector.open 状態でメニューが表示されるスタイルがあること。"""
        assert ".course-selector.open .course-selector-menu" in self.css

    def test_no_course_message_style_exists(self):
        """.no-course-message スタイルが定義されていること。"""
        assert ".no-course-message" in self.css

    def test_enroll_btn_style_exists(self):
        """.enroll-btn スタイルが定義されていること。"""
        assert ".enroll-btn" in self.css

    def test_menu_divider_style_exists(self):
        """.course-menu-divider スタイルが定義されていること。"""
        assert ".course-menu-divider" in self.css
