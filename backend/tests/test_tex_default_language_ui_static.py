"""管理UI: URLから取得モーダルの「取得する形式」と「生成する言語」（静的検査）。

正本: ``docs/features/paper_radar_design.md`` §15.9 /
``docs/features/url_material_upload_design.md`` の追補 /
``docs/features/discuss_opening_authoring_design.md`` §14。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
RADAR_JS = ROOT / "frontend" / "public" / "js" / "admin-paper-radar.js"
DISCOVERY_JS = ROOT / "frontend" / "public" / "js" / "admin-paper-discovery.js"
MANUAL = ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _js_function(js: str, name: str) -> str:
    start = js.index("function " + name + "(")
    nxt = js.find("\n  function ", start + 1)
    return js[start: nxt if nxt != -1 else len(js)]


def _notice(js: str, var: str) -> str:
    m = re.search(var + r"\s*=\s*\n?\s*\"([^\"]+)\";", js)
    assert m, var
    return m.group(1)


class TestUrlUploadFormatSwitch:
    def test_anchor_carrier_exists_in_the_modal(self):
        js = _read(ADMIN_JS)
        assert 'data-ui-anchor="materials.url-upload-format"' in js
        assert 'id="url-upload-format-row"' in js

    def test_default_is_tex_and_reset_on_open(self):
        js = _read(ADMIN_JS)
        assert 'var URL_DEFAULT_SOURCE_FORMAT = "tex";' in js
        assert "_urlUploadFormat = URL_DEFAULT_SOURCE_FORMAT;" in _js_function(js, "openUrlUploadModal")

    def test_notice_matches_the_arxiv_modals_verbatim(self):
        js = _read(ADMIN_JS)
        url_notice = _notice(js, "URL_FORMAT_NOTICE")
        assert url_notice == _notice(_read(RADAR_JS), "FORMAT_NOTICE")
        assert url_notice == _notice(_read(DISCOVERY_JS), "FORMAT_NOTICE")

    def test_format_is_sent_only_for_arxiv_urls(self):
        body = _js_function(_read(ADMIN_JS), "submitUrlUpload")
        assert "if (_isArxivPaperUrl(url)) payload.source_format = _urlUploadFormat;" in body

    def test_client_does_not_build_arxiv_urls(self):
        """書き換え（/abs/ → /src/ 等）はサーバが行う。クライアントは URL を組み立てない。"""
        js = _read(ADMIN_JS)
        assert "arxiv.org/src/" not in js
        assert '"https://arxiv.org/pdf/"' not in js

    def test_row_is_hidden_until_an_arxiv_url_is_typed(self):
        js = _read(ADMIN_JS)
        assert 'id="url-upload-format-row" data-ui-anchor="materials.url-upload-format" hidden' in js
        assert 'input.addEventListener("input", _urlUploadSyncFormatRow);' in js


class TestGenerationLanguage:
    def test_upload_zone_has_the_select_with_unspecified_default(self):
        html = _read(ADMIN_HTML)
        assert 'data-ui-anchor="materials.upload-language"' in html
        assert 'id="upload-language-select"' in html
        assert '<option value="" selected>指定しない</option>' in html
        assert '<option value="ja">日本語</option>' in html
        assert '<option value="en">英語</option>' in html

    def test_upload_and_url_send_language_only_when_chosen(self):
        js = _read(ADMIN_JS)
        assert 'if (uploadLanguage) formData.append("language", uploadLanguage);' in _js_function(js, "uploadFile")
        assert "if (urlLanguage) payload.language = urlLanguage;" in _js_function(js, "submitUrlUpload")

    def test_reanalyze_modal_has_the_row_and_inherits_by_default(self):
        js = _read(ADMIN_JS)
        assert 'id="reanalyze-language-row" data-ui-anchor="materials.upload-language"' in js
        assert '<option value="">前回と同じ</option>' in js
        assert "if (language !== null && language !== undefined) body.language = language;" in _js_function(js, "performReanalyze")
        assert "<option value=\"none\">指定しない</option>" in js

    def test_cache_buster_was_bumped(self):
        assert "/js/admin.js?v=tex-default-language-20261001-1" in _read(ADMIN_HTML)


class TestManualSections:
    def test_both_sections_exist(self):
        text = _read(MANUAL)
        assert "{#url-upload-format}" in text
        assert "{#upload-language}" in text

    def test_manual_states_the_call_budget(self):
        text = _read(MANUAL)
        section = text.split("{#url-upload-format}", 1)[1].split("\n### ", 1)[0]
        assert "最大2回" in section
        assert "arXiv からアクセスを制限されています" in section
