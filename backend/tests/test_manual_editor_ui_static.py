"""利用者マニュアル KB（help_kb）draft/freeze 管理 UI の静的ガードレール。

対象（manual_help_kb_design.md Phase 3 ②・フロントのみ、バックエンドは別実装）:
  - SYSTEM_ADMIN 向け「マニュアル編集」タブが admin.html / admin.js /
    admin-manual-editor.js に実在し、既定で非表示（display:none）であること。
  - 6本の help-kb API（drafts一覧 / 単一取得 / 保存 / seed / freeze /
    serving-source切替 / 版一覧）への fetch 呼び出しが存在すること。
  - draft 保存の 409（衝突）は自動マージせず、事実文 + 明示の再読込ボタンを
    提示すること。
  - freeze は事実文の確認モーダルを経由し、422 の violations をそのまま
    列挙すること（要約・捏造しない）。
  - ポーリング禁止（setInterval 不使用）。
  - admin-manual-editor.js は新規ファイルとして ES5 準拠（アロー関数・
    const/let・テンプレートリテラル禁止）。

すべて静的解析（正規表現・部分文字列検索）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
MANUAL_EDITOR_JS = FRONTEND_DIR / "js" / "admin-manual-editor.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    marker = "function " + name
    assert marker in src, f"{name} が見つかりません"
    after = src.split(marker, 1)[1]
    # 次の "  function " 定義（同じインデント階層）の手前までを本体とみなす。
    return after.split("\n  function ")[0]


# ---------------------------------------------------------------------------
# admin.html: タブ・パネル・script タグの実在
# ---------------------------------------------------------------------------


class TestManualEditorHtml:
    def setup_method(self):
        self.html = _read(ADMIN_HTML)

    def test_tab_button_exists_hidden_by_default(self):
        assert 'data-tab="manual-editor"' in self.html
        assert 'id="tab-btn-manual-editor"' in self.html
        m = re.search(r"<button[^>]*id=\"tab-btn-manual-editor\"[^>]*>", self.html)
        assert m, "tab-btn-manual-editor ボタンが見つかりません"
        assert 'style="display:none"' in m.group(0)

    def test_panel_container_exists(self):
        assert 'id="tab-manual-editor"' in self.html

    def test_script_tag_registered_before_admin_js(self):
        assert "/js/admin-manual-editor.js" in self.html
        idx_mke = self.html.index("/js/admin-manual-editor.js")
        idx_admin = self.html.index("/js/admin.js")
        assert idx_mke < idx_admin, "admin.js より前に読み込まれる必要があります（DI 注入のため）"

    def test_script_tag_loaded_in_admin_assistant_family_region(self):
        # admin-assistant.js 系のモジュール群と同じ末尾のスクリプト読み込みブロックに
        # 配置されていること（読み込み順の慣習に合わせる）。
        idx_assistant = self.html.index("/js/admin-assistant.js")
        idx_mke = self.html.index("/js/admin-manual-editor.js")
        idx_admin = self.html.index("/js/admin.js")
        assert idx_assistant < idx_mke < idx_admin


# ---------------------------------------------------------------------------
# admin.js: 役割別可視性・DI 注入（変更は最小限であること）
# ---------------------------------------------------------------------------


class TestManualEditorAdminJsWiring:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_tab_shown_for_system_admin_only(self):
        body = _extract_function(self.js, "setupRoleBasedUI")
        assert "tab-btn-manual-editor" in body

    def test_init_called_with_di(self):
        m = re.search(r"window\.ManualKbEditor\.init\(\{(.*?)\}\)", self.js, re.S)
        assert m, "ManualKbEditor.init 呼び出しが見つかりません"
        body = m.group(1)
        assert "apiFetch" in body
        assert "escHtml" in body
        assert "onTabActivate" in body
        assert "state" in body

    def test_admin_js_change_is_minimal_hook_only(self):
        """admin.js への変更は setupRoleBasedUI のタブ表示行 + initApp の init 呼び出しに
        限定する（本体ロジックは admin-manual-editor.js 側に閉じる）。"""
        assert self.js.count("ManualKbEditor") <= 3  # init(...) 呼び出し + コメント程度


# ---------------------------------------------------------------------------
# admin-manual-editor.js: API 配線・409衝突・freeze確認・ポーリング禁止・ES5
# ---------------------------------------------------------------------------


class TestManualEditorModule:
    def setup_method(self):
        assert MANUAL_EDITOR_JS.exists(), "admin-manual-editor.js が存在しません"
        self.js = _read(MANUAL_EDITOR_JS)

    def test_exposes_window_api(self):
        assert "window.ManualKbEditor = {" in self.js
        assert "init: init" in self.js

    def test_init_gates_on_system_admin_role(self):
        body = _extract_function(self.js, "init")
        assert 'deps.state.role === "SYSTEM_ADMIN"' in body

    def test_calls_all_six_api_paths(self):
        assert '"/admin/help-kb/drafts"' in self.js
        assert "_draftPath" in self.js
        assert '"/admin/help-kb/drafts/"' in self.js
        assert '"/admin/help-kb/drafts/seed"' in self.js
        assert '"/admin/help-kb/freeze"' in self.js
        assert '"/admin/help-kb/serving-source"' in self.js
        assert '"/admin/help-kb/versions"' in self.js

    def test_save_uses_put_with_expected_revision(self):
        body = _extract_function(self.js, "doSave")
        assert 'method: "PUT"' in body
        assert "expected_revision" in body
        assert "currentRevision" in body

    def test_conflict_409_does_not_auto_merge(self):
        """409 は自動マージせず、事実文 + 明示の再読込ボタンのみ提示する。"""
        body = _extract_function(self.js, "doSave")
        assert "err.status === 409" in body
        assert "showConflictNote" in body
        assert "最新を読み込み直してください" in body
        # 自動マージ（サーバ内容とローカル内容を結合するような処理）が無いこと。
        assert "merge" not in body.lower()

        reload_body = self.js
        assert 'id="mke-reload-btn"' in reload_body
        assert "mke-reload-btn" in _extract_function(self.js, "buildTab")

    def test_seed_action_present(self):
        body = _extract_function(self.js, "doSeed")
        assert '"/admin/help-kb/drafts/seed"' in body
        assert 'method: "POST"' in body

    def test_freeze_uses_fact_based_confirm_dialog(self):
        body = _extract_function(self.js, "doFreeze")
        assert "_confirmDanger" in body
        assert "配信ソースを DB に切り替えます" in body
        assert "リポジトリの docs 更新は配信に反映されません" in body

    def test_freeze_confirm_helper_uses_shared_or_native_confirm(self):
        body = _extract_function(self.js, "_confirmDanger")
        assert "window.AdminDangerConfirm" in body
        assert "window.confirm" in body

    def test_freeze_violations_rendered_verbatim_not_summarized(self):
        """422 の violations はサーバ応答をそのまま列挙する（要約・捏造しない）。"""
        body = _extract_function(self.js, "renderViolations")
        assert "violations" in body
        assert "JSON.stringify" in body  # オブジェクト形の violation もそのまま出す
        assert "forEach" in body

        freeze_body = _extract_function(self.js, "doFreeze")
        assert "renderViolations" in freeze_body
        assert "err.status === 422" in freeze_body
        assert "err.body.detail.violations" in freeze_body

    def test_switch_source_also_uses_fact_based_confirm(self):
        body = _extract_function(self.js, "doSwitchSource")
        assert "_confirmDanger" in body
        assert '"/admin/help-kb/serving-source"' in body
        assert "source: target" in body

    def test_no_polling(self):
        assert "setInterval" not in self.js

    def test_no_cost_or_numeric_score_wording(self):
        # G4/W8 系の不変条項に倣い、数値スコア的な表現を出さない。
        assert "confidence" not in self.js.lower()

    def test_module_is_es5(self):
        assert "=>" not in self.js
        assert re.search(r"\bconst\s", self.js) is None
        assert re.search(r"\blet\s", self.js) is None
        assert "`" not in self.js
        assert '"use strict"' in self.js
