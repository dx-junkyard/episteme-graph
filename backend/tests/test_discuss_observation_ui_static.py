"""discuss 観測基盤（Observation Layer for Phase 3 Gate, docs/features/discuss_observation_design.md）
の フロントエンド実装に対する静的ガードレール。

対象（フロントのみ・バックエンドは別チーム実装 backend/core/discuss/observation.py +
backend/api/routes/discuss_observation.py）:
  - frontend/public/js/app.js / discuss.js — §3 のイベント送信13種の配線
  - frontend/public/js/admin-discuss-observation.js — §4 の管理 UI（新規, ES5）
  - frontend/public/admin.html — タブ・パネル・script タグの実在

すべて静的解析（正規表現・部分文字列検索・波括弧カウントによる関数本体抽出）のみ。
実サーバ・実DOM・ブラウザは使わない（他の *_ui_static.py と同じ流儀）。

検証観点:
1. app.js: sendDiscussMetric ヘルパーが fire-and-forget（.catch 存在・await しない・
   結果を DOM に反映する配線が無い）。discussion_entered / scope_switched / 分岐チップ
   （branch_deep_clicked / branch_wide_clicked）の配線がある。
2. discuss.js: 自前の sendDiscussMetric ヘルパーを持つ（app.js と同型・fire-and-forget）。
   opening_shown / opening_starter_clicked / opening_backbone_clicked /
   landing_shown（payload.reason）/ landing_confirmed（payload.kind）/ landing_dismissed
   （payload.kind）/ landing_skipped / landing_probe_clicked / landing_continue_clicked
   の配線がある。
3. 13種のイベント語彙すべてが app.js + discuss.js のどこかで送信されている。
4. payload.reason は explicit|topic_switch|timeout の3値。
5. 学習者向け UI（app.js / discuss.js）に discuss/metric-events のレスポンス
   （{recorded:n}）を DOM へ表示する配線が無い（DO3）。
6. admin-discuss-observation.js: SYSTEM_ADMIN ガード・「参考目安」注記文言・
   ダウンロード2形式（tar.gz / zip）・ポーリング禁止（setInterval 不使用）・ES5準拠。
7. admin.html: タブボタン・パネル・script タグが既定非表示で実在する。
8. admin.js への変更は最小限のフック（表示切替 + init 呼び出し）に留まる。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ADMIN_DISCUSS_OBS_JS = ROOT / "frontend" / "public" / "js" / "admin-discuss-observation.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"


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


ALL_EVENTS = [
    "discussion_entered",
    "opening_shown",
    "opening_starter_clicked",
    "opening_backbone_clicked",
    "branch_deep_clicked",
    "branch_wide_clicked",
    "scope_switched",
    "landing_shown",
    "landing_confirmed",
    "landing_dismissed",
    "landing_skipped",
    "landing_probe_clicked",
    "landing_continue_clicked",
]


class TestEventVocabularyCoverage:
    """13種のイベント語彙すべてが、どこかから送信されていること。"""

    def test_all_thirteen_events_are_sent_somewhere(self):
        combined = _read(APP_JS) + "\n" + _read(DISCUSS_JS)
        for event in ALL_EVENTS:
            assert '"' + event + '"' in combined, f"イベント {event!r} の送信箇所が見つからない"

    def test_exactly_thirteen_events_defined_in_design(self):
        # 語彙の変化（typo・増減）を検知するための固定長チェック。
        assert len(ALL_EVENTS) == 13


class TestAppJsMetricHelper:
    """app.js: sendDiscussMetric は fire-and-forget（DO6）。学習者に recorded を見せない（DO3）。"""

    def test_helper_defined(self):
        js = _read(APP_JS)
        assert "function sendDiscussMetric(event, payload)" in js

    def test_helper_is_fire_and_forget(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function sendDiscussMetric(event, payload) {")
        assert ".catch(function ()" in block
        assert "await apiFetch" not in block
        assert "return apiFetch" not in block  # 呼び出し元へ Promise を返して待たせない

    def test_helper_posts_to_metric_events_endpoint(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function sendDiscussMetric(event, payload) {")
        assert '"/learning/discuss/metric-events"' in block
        assert 'method: "POST"' in block
        assert "course_id: state.courseId" in block

    def test_discussion_entered_wired_in_enter_discuss_mode(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function enterDiscussMode() {")
        assert 'sendDiscussMetric("discussion_entered", {})' in block
        # 遷移そのもの (selectTopic) は既存どおり残っていること。
        assert "selectTopic(DISCUSS_TOPIC_ID)" in block

    def test_scope_switched_wired_in_init_discuss_ui(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initDiscussUI() {")
        assert 'sendDiscussMetric("scope_switched"' in block
        # 既存のスコープ保存の一行は変更しない（他テストの回帰対象と同一文字列）。
        assert 'state.discussScope = this.getAttribute("data-discuss-scope") || "course_sources";' in block

    def test_branch_chip_clicks_wired_in_generic_suggest_btn_binder(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderChat() {")
        assert "data-discuss-branch" in block
        assert 'sendDiscussMetric("branch_deep_clicked", {})' in block
        assert 'sendDiscussMetric("branch_wide_clicked", {})' in block

    def test_no_recorded_count_shown_to_learner(self):
        """DO3: レスポンス {recorded:n} を DOM に反映する配線が無い（ヘルパー本体は
        レスポンスの中身を読まない。設計コメント中の "recorded" 文言自体は許容する）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function sendDiscussMetric(event, payload) {")
        assert ".json()" not in block
        assert "recorded" not in block


class TestDiscussJsMetricHelper:
    """discuss.js: 自前の sendDiscussMetric ヘルパー（app.js と同仕様・fire-and-forget）。"""

    def test_helper_defined_and_self_contained(self):
        js = _read(DISCUSS_JS)
        assert "function sendDiscussMetric(event, payload)" in js
        block = _extract_function_body(js, "function sendDiscussMetric(event, payload) {")
        assert ".catch(function ()" in block
        assert '"/learning/discuss/metric-events"' in block
        assert "ctx.courseId" in block

    def test_no_recorded_count_shown_to_learner(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function sendDiscussMetric(event, payload) {")
        assert ".json()" not in block
        assert "recorded" not in block

    def test_opening_shown_wired_after_successful_render(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function renderOpening(containerEl, courseId) {")
        assert "notifyOpeningShown(courseId)" in block
        notify_block = _extract_function_body(js, "function notifyOpeningShown(courseId) {")
        assert 'sendDiscussMetric("opening_shown", {})' in notify_block

    def test_opening_shown_deduped_per_course(self):
        """renderOpening は renderChat 経由で何度も呼ばれるため、同一コースでの
        再描画は重複計上しない（設計は禁止していないが、素朴な実装だと過剰計測になる）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function notifyOpeningShown(courseId) {")
        assert "openingShownCourseId" in block

    def test_opening_starter_and_backbone_clicks_distinguished(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function bindOpeningEvents(containerEl) {")
        assert 'sendDiscussMetric("opening_backbone_clicked", {})' in block
        assert 'sendDiscussMetric("opening_starter_clicked", {})' in block
        assert "discuss-backbone-node" in block

    def test_landing_shown_carries_reason_payload(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function maybeShowLanding(courseId, reason) {")
        assert 'sendDiscussMetric("landing_shown", { reason: reason || "" })' in block

    def test_landing_shown_only_fires_after_suppression_checks(self):
        """うるさくしない: 抑制窓・0往復ガードの後で発火する（毎回は出さない）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function maybeShowLanding(courseId, reason) {")
        idx_guard = block.index("if (turnCount <= 0) return;")
        idx_shown = block.index('sendDiscussMetric("landing_shown"')
        assert idx_guard < idx_shown

    def test_landing_confirmed_wired_for_tension_and_anchor(self):
        js = _read(DISCUSS_JS)
        tension_block = _extract_function_body(js, "function openTensionInlineConfirm(traceId) {")
        assert 'sendDiscussMetric("landing_confirmed", { kind: "tension" })' in tension_block
        anchor_block = _extract_function_body(js, "async function confirmAnchorCard(traceId) {")
        assert 'sendDiscussMetric("landing_confirmed", { kind: "anchor" })' in anchor_block

    def test_landing_dismissed_wired_for_tension_and_anchor(self):
        js = _read(DISCUSS_JS)
        tension_block = _extract_function_body(js, "async function dismissTensionCard(traceId) {")
        assert 'sendDiscussMetric("landing_dismissed", { kind: "tension" })' in tension_block
        anchor_block = _extract_function_body(js, "async function dismissAnchorCard(traceId) {")
        assert 'sendDiscussMetric("landing_dismissed", { kind: "anchor" })' in anchor_block

    def test_landing_skipped_wired_on_explicit_skip_paths_only(self):
        """スキップ相当（背景クリック・Escape・スキップボタン）でのみ発火し、
        「挑戦する」「このトピックで続きを学ぶ」経由の close では発火しない。"""
        js = _read(DISCUSS_JS)
        skip_block = _extract_function_body(js, "function skipLanding() {")
        assert 'sendDiscussMetric("landing_skipped", {})' in skip_block

        root_block = _extract_function_body(js, "function bindLandingRootOnce(root) {")
        assert "skipLanding()" in root_block
        assert "closeLanding()" not in root_block  # 背景クリック・Escape は skipLanding 経由のみ

        content_block = _extract_function_body(js, "function bindLandingContentEvents(root, reconItem) {")
        assert "landing_skipped" not in content_block

    def test_landing_probe_and_continue_wired(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function bindLandingContentEvents(root, reconItem) {")
        assert 'sendDiscussMetric("landing_probe_clicked", {})' in block
        assert 'sendDiscussMetric("landing_continue_clicked", {})' in block


class TestLandingReasonPayloadValues:
    """payload.reason はバックエンド契約どおり explicit|topic_switch|timeout の3値。"""

    def test_three_reason_values_present_at_call_sites(self):
        app_js = _read(APP_JS)
        discuss_js = _read(DISCUSS_JS)
        assert 'maybeShowLanding(state.courseId, "explicit")' in app_js
        assert 'maybeShowLanding(_discussLeavingCourseId, "topic_switch")' in app_js
        assert 'maybeShowLanding(ctx.courseId, "timeout")' in discuss_js


class TestAdminDiscussObservationModule:
    def setup_method(self):
        assert ADMIN_DISCUSS_OBS_JS.exists(), "admin-discuss-observation.js が存在しません"
        self.js = _read(ADMIN_DISCUSS_OBS_JS)

    def test_exposes_window_api(self):
        assert "window.AdminDiscussObservation = {" in self.js
        assert "init: init" in self.js

    def test_init_gates_on_system_admin_role(self):
        block = _extract_function_body(self.js, "function init(options) {")
        assert 'deps.state.role === "SYSTEM_ADMIN"' in block

    def test_calls_status_and_dump_endpoints(self):
        assert '"/admin/discuss/observation-status"' in self.js
        assert '"/admin/discuss/observation-dump?format="' in self.js

    def test_two_dump_formats_available(self):
        assert 'doDownload("tar.gz")' in self.js
        assert 'doDownload("zip")' in self.js
        assert "ado-dump-targz-btn" in self.js
        assert "ado-dump-zip-btn" in self.js

    def test_download_uses_authenticated_fetch_blob_pattern(self):
        block = _extract_function_body(self.js, "function doDownload(format) {")
        assert "apiFetch(" in block
        assert "res.blob()" in block
        assert "URL.createObjectURL(blob)" in block
        assert "Content-Disposition" in block
        assert "a.download" in block

    def test_reference_target_gate_wording_present(self):
        """DO5: 参考目安は自動ゲートにしない旨を事実文で明記する。"""
        assert "参考目安" in self.js
        assert "自動的に何かが起きることはありません" in self.js

    def test_no_polling(self):
        assert "setInterval" not in self.js

    def test_no_numeric_score_wording(self):
        assert "confidence" not in self.js.lower()

    def test_module_is_es5(self):
        assert "=>" not in self.js
        assert re.search(r"\bconst\s", self.js) is None
        assert re.search(r"\blet\s", self.js) is None
        assert "`" not in self.js
        assert '"use strict"' in self.js

    def test_no_recorded_count_shown_to_learner_facing_text(self):
        # このモジュールは SYSTEM_ADMIN 専用 UI なので件数表示そのものは許容されるが、
        # 学習者向けの文言・要素IDが紛れ込んでいないことだけ確認する。
        assert "material-body" not in self.js
        assert "chat-area" not in self.js


class TestAdminHtmlWiring:
    def setup_method(self):
        self.html = _read(ADMIN_HTML)

    def test_tab_button_exists_hidden_by_default(self):
        assert 'data-tab="discuss-observation"' in self.html
        m = re.search(r"<button[^>]*id=\"tab-btn-discuss-observation\"[^>]*>", self.html)
        assert m, "tab-btn-discuss-observation ボタンが見つかりません"
        assert 'style="display:none"' in m.group(0)

    def test_panel_container_exists(self):
        assert 'id="tab-discuss-observation"' in self.html

    def test_script_tag_registered_before_admin_js(self):
        assert "/js/admin-discuss-observation.js" in self.html
        idx_ado = self.html.index("/js/admin-discuss-observation.js")
        idx_admin = self.html.index("/js/admin.js")
        assert idx_ado < idx_admin, "admin.js より前に読み込まれる必要があります（DI 注入のため）"


class TestAdminJsWiringIsMinimal:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_tab_shown_for_system_admin_only(self):
        block = _extract_function_body(self.js, "function setupRoleBasedUI() {")
        assert "tab-btn-discuss-observation" in block

    def test_init_called_with_di(self):
        m = re.search(r"window\.AdminDiscussObservation\.init\(\{(.*?)\}\)", self.js, re.S)
        assert m, "AdminDiscussObservation.init 呼び出しが見つかりません"
        body = m.group(1)
        assert "apiFetch" in body
        assert "escHtml" in body
        assert "onTabActivate" in body
        assert "state" in body

    def test_admin_js_change_is_minimal_hook_only(self):
        assert self.js.count("AdminDiscussObservation") <= 3
