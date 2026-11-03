"""学習画面がサーバの事実文を素通しする UI 契約（ペルソナ通し受講 第 8 周・IK-0405〜0407）。

``frontend/public/{index.html,js/app.js,css/styles.css}`` を静的に検査して次を固定する:

1. IK-0405 受講登録: enroll 応答の ``notice``（正本 ``label_vocab.COURSE_ENROLLED_NOTICE``）を
   トップバーの事実文に textContent で1回だけ描く。器は既定 hidden・警告色にしない・
   タイマーで消さず次のコース切替で消す。
2. IK-0406 記号の照会: ``available: false`` のときサーバの ``facts`` を描き、空のときだけ
   従来の固定文へ縮退する。
3. IK-0407 同名コース: 題名が重複する選択肢にだけ description 由来の2行目を添える
   （数値を描かない・description が空なら何も足さない）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_block(name: str) -> str:
    """``app.js`` の関数1つ分の本文（次の同インデント ``}`` まで）。"""
    src = _read(APP_JS)
    pattern = re.compile(r"^([ \t]*)(?:async +)?function %s\(" % re.escape(name), re.MULTILINE)
    match = pattern.search(src)
    assert match, f"{name} が app.js に見つからない"
    indent = match.group(1)
    rest = src[match.end():]
    end = re.search(r"^%s\}" % re.escape(indent), rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


class TestEnrollNotice:
    def test_carrier_exists_hidden_by_default(self):
        html = _read(INDEX_HTML)
        assert 'id="course-enroll-notice"' in html
        tag = html[html.index('id="course-enroll-notice"'):]
        tag = tag[: tag.index(">") + 1]
        assert "hidden" in tag
        # 事実の表示であって操作要素ではない（data-ui-anchor を付けない規律）。
        assert "data-ui-anchor" not in tag

    def test_css_keeps_hidden_effective_and_is_not_warning_colored(self):
        css = _read(STYLES_CSS)
        assert "#course-enroll-notice[hidden] { display: none !important; }" in css
        block = css[css.index(".course-enroll-notice {"):]
        block = block[: block.index("}")]
        assert "var(--color-text-secondary)" in block
        for warn in ("danger", "warning", "#e53935"):
            assert warn not in block

    def test_enroll_renders_server_notice_after_switch(self):
        js = _function_block("enrollCourse")
        assert "showEnrollNotice(" in js
        assert "data.notice" in js
        assert "data.enrolled" in js
        # 切替の後に出す（切替の冒頭で前の事実文を消すため）。
        assert js.index("await switchCourse(data.id)") < js.index("showEnrollNotice(")

    def test_notice_uses_text_content_and_no_timer(self):
        js = _function_block("showEnrollNotice")
        assert "textContent" in js
        assert "innerHTML" not in js
        assert "setTimeout" not in js
        assert "setInterval" not in js

    def test_notice_wording_not_baked_into_frontend(self):
        """文言の正本はサーバ（label_vocab）。フロントに同じ文を焼き込まない。"""
        from core import label_vocab

        notice = label_vocab.COURSE_ENROLLED_NOTICE
        assert notice
        assert notice not in _read(APP_JS)
        assert notice not in _read(INDEX_HTML)

    def test_switch_course_clears_notice(self):
        js = _function_block("switchCourse")
        assert 'showEnrollNotice("")' in js


class TestSymbolLookupUnavailableFacts:
    def test_unavailable_branch_prefers_server_facts(self):
        js = _function_block("openSymbolLookup")
        branch = js[js.index("if (!data.available)"):]
        branch = branch[: branch.index("return;")]
        assert "data.facts" in branch
        assert "serverFacts.length" in branch
        # facts が空のときだけの縮退として固定文を残す。
        assert "この論文には、この記号の記述が見つかりませんでした。" in branch
        assert branch.index("serverFacts.length") < branch.index("見つかりませんでした")

    def test_available_branch_still_renders_facts(self):
        js = _function_block("renderSymbolLookupPopover")
        assert "facts.forEach" in js


class TestDuplicateCourseTitles:
    def test_option_label_uses_secondary_line_only_for_duplicates(self):
        js = _function_block("courseOptionLabel")
        assert "dupTitles[title]" in js
        assert "courseSecondaryLine(c)" in js

    def test_secondary_line_comes_from_description_only(self):
        js = _function_block("courseSecondaryLine")
        assert "description" in js
        # 推測で埋めない（DTO に無い材料を使わない・数値を描かない）。
        for forbidden in ("updated_at", "created_at", "件", "%", "score"):
            assert forbidden not in js

    def test_render_course_select_uses_option_label(self):
        js = _function_block("renderCourseSelect")
        assert "duplicatedCourseTitles(" in js
        assert js.count("courseOptionLabel(c, dupTitles)") == 2
        assert "escHtml(courseOptionLabel(" in js
