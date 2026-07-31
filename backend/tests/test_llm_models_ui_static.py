"""LLM モデル選択（M層, migration 061）フロントエンド UI の静的ガードレール。

対象（llm_model_selection_design.md §6、フロントのみ。バックエンドは実装済み・別テスト）:
  - 教材管理タブに解析モデルの1行サマリ（`llm-model-summary`）+ 変更パネル
    （`llm-model-panel`）が admin.html に実在すること。
  - 運用タブ「AIモデル」（SYSTEM_ADMIN のみ）が admin.html / admin.js /
    admin-llm-models.js に実在し、既定で非表示（display:none）であること。
  - admin-llm-models.js は新規ファイルとして ES5 準拠（アロー関数・const/let・
    テンプレートリテラル禁止）。
  - アップロード時に models（JSON文字列）を formData へ追加するコードが admin.js に
    存在すること。
  - UI 文字列に tier 語（fast/standard/deep/analysis の内部呼称）・金額（$/円/USD）・
    「選択してください」プレースホルダが現れないこと（M2/M3/M8）。
  - 運用タブでのシステム既定変更は confirm を経由すること。
  - Phase 4（ステージ別指定, §6.1「▸ ステージ別に指定する（詳細）」・
    §6.6「▸ ステージ別の指定（N件）」）: 教材管理パネルの詳細折りたたみは既定で閉じており、
    `GET /pipeline-stages` を叩き、vision ステージは vision カタログを使い、選択は
    pipeline:<stage> キーとして run-only で models に乗る。運用タブのステージ別セクションは
    0件のとき非表示で、変更は confirm 経由の既存 doOpsSave/doOpsReset を再利用する。

すべて静的解析（正規表現・部分文字列検索）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
LLM_MODELS_JS = FRONTEND_DIR / "js" / "admin-llm-models.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    marker = "function " + name
    assert marker in src, f"{name} が見つかりません"
    after = src.split(marker, 1)[1]
    return after.split("\n  function ")[0]


# ---------------------------------------------------------------------------
# admin.html: タブ・パネル・script タグの実在
# ---------------------------------------------------------------------------


class TestLlmModelsHtml:
    def setup_method(self):
        self.html = _read(ADMIN_HTML)

    def test_ops_tab_button_exists_hidden_by_default(self):
        assert 'data-tab="llm-models"' in self.html
        assert 'id="tab-btn-llm-models"' in self.html
        m = re.search(r'<button[^>]*id="tab-btn-llm-models"[^>]*>', self.html)
        assert m, "tab-btn-llm-models ボタンが見つかりません"
        assert 'style="display:none"' in m.group(0)

    def test_ops_panel_container_exists(self):
        assert 'id="tab-llm-models"' in self.html

    def test_script_tag_registered_before_admin_js(self):
        assert "/js/admin-llm-models.js" in self.html
        idx_llm = self.html.index("/js/admin-llm-models.js")
        idx_admin = self.html.index("/js/admin.js")
        assert idx_llm < idx_admin

    def test_materials_summary_row_exists(self):
        assert 'id="llm-model-summary"' in self.html
        assert 'id="llm-model-summary-value"' in self.html
        assert 'id="llm-model-change-btn"' in self.html
        assert 'id="llm-model-panel"' in self.html

    def test_materials_summary_placed_after_analyze_images_checkbox(self):
        idx_checkbox = self.html.index('id="upload-analyze-images"')
        idx_summary = self.html.index('id="llm-model-summary"')
        assert idx_checkbox < idx_summary

    def test_materials_summary_has_no_placeholder_text(self):
        """M2: 未選択のプレースホルダを作らない。「選択してください」を使わない。

        （他機能のプルダウンで同文言を使う箇所があるため、教材アップロード区画の
        解析モデル・サマリ行の近傍だけを対象に検査する。）
        """
        idx = self.html.index('id="llm-model-summary"')
        nearby = self.html[idx : idx + 800]
        assert "選択してください" not in nearby


# ---------------------------------------------------------------------------
# admin.js: 役割別可視性・DI 注入・アップロード/再解析への配線
# ---------------------------------------------------------------------------


class TestLlmModelsAdminJsWiring:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_ops_tab_shown_for_system_admin_only(self):
        body = _extract_function(self.js, "setupRoleBasedUI")
        assert "tab-btn-llm-models" in body

    def test_init_called_with_di(self):
        m = re.search(r"window\.AdminLlmModels\.init\(\{(.*?)\}\)", self.js, re.S)
        assert m, "AdminLlmModels.init 呼び出しが見つかりません"
        body = m.group(1)
        assert "apiFetch" in body
        assert "escHtml" in body
        assert "onTabActivate" in body
        assert "state" in body

    def test_materials_panel_initialized_on_upload_screen(self):
        assert "window.AdminLlmModels.initMaterialsPanel()" in self.js

    def test_upload_formdata_includes_models(self):
        body = _extract_function(self.js, "uploadFile")
        assert "getUploadModels" in body
        assert 'formData.append("models"' in body

    def test_reanalyze_modal_uses_reanalyze_panel(self):
        body = _extract_function(self.js, "openReanalyzeOptionsModal")
        assert "initReanalyzePanel" in body
        assert "getReanalyzeModels" in body
        assert 'id="reanalyze-llm-model-row"' in body

    def test_perform_reanalyze_forwards_models(self):
        body = _extract_function(self.js, "performReanalyze")
        assert "body.models = models" in body


# ---------------------------------------------------------------------------
# admin-llm-models.js: 選択肢構築・カタログ未設定時の fail-closed・ES5・文言規約
# ---------------------------------------------------------------------------


class TestLlmModelsModule:
    def setup_method(self):
        assert LLM_MODELS_JS.exists(), "admin-llm-models.js が存在しません"
        self.js = _read(LLM_MODELS_JS)

    def test_exposes_window_api(self):
        assert "window.AdminLlmModels = {" in self.js
        assert "init: init" in self.js
        assert "initMaterialsPanel: initMaterialsPanel" in self.js
        assert "getUploadModels: getUploadModels" in self.js
        assert "getReanalyzeModels: getReanalyzeModels" in self.js
        assert "initOpsTab: initOpsTab" in self.js

    def test_calls_catalog_endpoint(self):
        assert '"/admin/llm-models/catalog?scene=' in self.js

    def test_calls_my_policies_endpoints(self):
        assert '"/admin/llm-models/my-policies/"' in self.js
        assert 'method: "PUT"' in self.js
        assert 'method: "DELETE"' in self.js

    def test_calls_system_policies_endpoints(self):
        assert '"/admin/llm-models/policies"' in self.js
        assert '"/admin/llm-models/policies/"' in self.js

    def test_catalog_unavailable_disables_change_and_shows_fact(self):
        """M4: カタログ未設定/取得失敗時は現在値のみ・[変更]を無効化・架空候補を並べない。"""
        body = _extract_function(self.js, "renderMaterialsSummary")
        assert "btn.disabled = !mtCatalogAvailable" in body
        note_body = _extract_function(self.js, "_unavailableNoteHtml")
        assert "選択肢が未設定です" in note_body

    def test_upload_models_excludes_out_of_catalog_values(self):
        """422 回避: カタログ options に無い実効モデルはキーごと省略する。"""
        body = _extract_function(self.js, "getUploadModels")
        assert "_validModelForScene" in body
        assert "mtCatalogAvailable" in body

    def test_reanalyze_models_returns_null_when_unchanged(self):
        """M6: 前回と同一なら null を返し、前回継承（options 自動継承）に任せる。"""
        body = _extract_function(self.js, "getReanalyzeModels")
        assert "_modelsEqual" in body
        assert "return null" in body

    def test_ops_change_requires_confirm(self):
        body = _extract_function(self.js, "doOpsSave")
        assert "window.confirm" in body
        assert "全ユーザーの既定に影響します" in body

    def test_ops_reset_handles_404_honestly(self):
        body = _extract_function(self.js, "doOpsReset")
        assert "err.status === 404" in body
        assert "システム既定は未設定です" in body

    def test_no_tier_names_in_ui_strings(self):
        """M3: fast/standard/deep/analysis という内部 tier 名を表示文字列に出さない。

        コード冒頭の設計コメント（不変条項の説明文）はドキュメントであり表示文字列では
        ないため、`/* ... */` ブロックコメントを除去してから検査する。
        """
        without_block_comments = re.sub(r"/\*.*?\*/", "", self.js, flags=re.S)
        for tier_word in ("fast", "standard", "deep", "analysis"):
            assert tier_word not in without_block_comments.lower(), (
                f"tier 語 {tier_word!r} が表示文字列に現れています"
            )

    def test_no_cost_or_currency_wording(self):
        """M8: 金額(USD/円/$)を出さない。コスト目安は低/中/高のみ。

        設計コメント（不変条項の説明文）はドキュメントであり表示文字列ではないため、
        `/* ... */` ブロックコメントを除去してから検査する。
        """
        without_block_comments = re.sub(r"/\*.*?\*/", "", self.js, flags=re.S)
        assert "$" not in without_block_comments
        assert "円" not in without_block_comments
        assert "USD" not in without_block_comments
        assert "cost_usd" not in without_block_comments

    def test_no_placeholder_select_text(self):
        assert "選択してください" not in self.js

    def test_vision_select_gated_by_analyze_images_checkbox(self):
        """図の解析（vision）選択は analyze_images チェック時のみ表示する。"""
        body = _extract_function(self.js, "renderMaterialsPanel")
        assert "upload-analyze-images" in body
        assert "showVision" in body

    def test_stage_level_detail_panel_implemented(self):
        """§6.1 Phase 4: 「ステージ別に指定する（詳細）」折りたたみが実装されていること。"""
        assert "ステージ別に指定する（詳細）" in self.js

    def test_module_is_es5(self):
        assert "=>" not in self.js
        assert re.search(r"\bconst\s", self.js) is None
        assert re.search(r"\blet\s", self.js) is None
        assert "`" not in self.js
        assert '"use strict"' in self.js


# ---------------------------------------------------------------------------
# Phase 3（設計書 §6.2〜6.5）: 汎用モデルチップ + 各画面への配線
# ---------------------------------------------------------------------------


class TestLlmModelChipComponent:
    """AdminLlmModels.createModelChip（設計書 §6.2〜6.5 共通部品）。"""

    def setup_method(self):
        self.js = _read(LLM_MODELS_JS)

    def test_create_model_chip_exposed(self):
        assert "createModelChip: createModelChip" in self.js
        assert "invalidateSceneCatalogCache: invalidateSceneCatalogCache" in self.js

    def test_create_model_chip_supports_initial_model_and_reset(self):
        body = _extract_function(self.js, "createModelChip")
        assert "opts.initialModel" in body
        assert 'val === "__reset__"' not in body  # 実装は data-model="" を reset として扱う
        assert 'var val = this.getAttribute("data-model") || null;' in body

    def test_chip_menu_shows_cost_hint_and_default_tag_not_currency(self):
        body = _extract_function(self.js, "_chipMenuItemsHtml")
        assert "コスト目安" in body
        assert "$" not in body and "円" not in body and "USD" not in body

    def test_module_is_es5_after_chip_addition(self):
        assert "=>" not in self.js
        assert re.search(r"\bconst\s", self.js) is None
        assert re.search(r"\blet\s", self.js) is None
        assert "`" not in self.js


class TestCourseBuilderModelChipWiring:
    """設計書 §6.2: コース構築チャットのモデルチップ。"""

    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_chip_mounted_in_course_builder(self):
        assert 'sceneKey: "course_builder"' in self.js
        assert "cb-chat-model-chip" in self.js

    def test_html_mount_point_exists(self):
        html = _read(ADMIN_HTML)
        assert 'id="cb-chat-model-chip"' in html

    def test_chat_request_forwards_selected_model(self):
        body = _extract_function(self.js, "sendCourseChat")
        assert "_cbCurrentModel()" in body

    def test_divider_role_rendered_but_not_persisted_to_history(self):
        body = _extract_function(self.js, "renderCourseChat")
        assert 'msg.role === "divider"' in body
        # 区切り事実文は表示専用（chatMessages）で、保存対象の chatHistory には積まない。
        assert 'chatHistory.push({ role: "divider"' not in self.js

    def test_session_switch_syncs_chip_without_reannouncing(self):
        # セッション切替時は setModel で追従するだけで onChange（= 区切り線挿入）を発火しない。
        assert "_cbSyncModelChipToSession" in self.js
        body = _extract_function(self.js, "_cbSyncModelChipToSession")
        assert "_cbModelChip.setModel(" in body


class TestLectureStudioModelChipWiring:
    """設計書 §6.3: 原稿スタジオ「AIアシスタント」モーダルのモデルチップ。"""

    def setup_method(self):
        self.js = _read(FRONTEND_DIR / "js" / "admin-lecture-studio.js")

    def test_chip_mounted_for_rewrite_modal(self):
        assert 'sceneKey: "lecture_studio"' in self.js
        assert "ls-rewrite-model-chip" in self.js

    def test_html_mount_point_exists(self):
        html = _read(ADMIN_HTML)
        assert 'id="ls-rewrite-model-chip"' in html

    def test_chunk_rewrite_forwards_model(self):
        body = _extract_function(self.js, "lsRewriteScript")
        assert "_lsRewriteModel()" in body

    def test_topic_draft_rewrite_forwards_model(self):
        body = _extract_function(self.js, "lsRewriteCourseTopicDraft")
        assert "_lsRewriteModel()" in body

    def test_module_stays_es5(self):
        assert "=>" not in self.js
        assert re.search(r"\bconst\s", self.js) is None
        assert re.search(r"\blet\s", self.js) is None


class TestAtlasModelChipWiring:
    """設計書 §6.5: 分野の地図の骨格生成ボタン隣のモデルチップ。"""

    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_chip_mounted_next_to_generate_button(self):
        assert 'sceneKey: "atlas"' in self.js
        assert "atlas-generate-model-chip" in self.js

    def test_html_mount_point_exists(self):
        html = _read(ADMIN_HTML)
        assert 'id="atlas-generate-model-chip"' in html

    def test_generate_payload_includes_model_when_selected(self):
        body = _extract_function(self.js, "initAtlas")
        assert "_atlasModelChip" in body
        assert "payload.model = atlasModel" in body


class TestDeliberationModelChipWiring:
    """設計書 §6.5: 要素検討ワークスペースの対話パネル用モデルチップ。"""

    def setup_method(self):
        self.js = _read(FRONTEND_DIR / "js" / "deliberation.js")

    def test_chip_mounted_in_chat_header(self):
        assert "deliberation-model-chip" in self.js

    def test_figure_elements_use_vision_scene(self):
        assert (
            'var chipScene = elementType === "figure" ? "deliberation:vision" : "deliberation";'
            in self.js
        )

    def test_message_body_forwards_selected_model(self):
        body = _extract_function(self.js, "_sendChatMessage")
        assert "chatState.selectedModel" in body
        assert "messageBody.model = chatState.selectedModel" in body

    def test_module_stays_es5(self):
        assert "=>" not in self.js
        assert re.search(r"\bconst\s", self.js) is None
        assert re.search(r"\blet\s", self.js) is None


class TestCourseSettingsModelChipWiring:
    """設計書 §6.4: コース管理タブ「AIモデル」— 学習チャットのコース単位上書き。"""

    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_llm_model_button_owner_only(self):
        # renderCourseManagementTable 内: 所有者の actionHtml 組み立て（"所有者のみ変更可" より前）
        # にのみボタン変数が現れ、非所有者向けの分岐（"所有者のみ変更可" 以降）には現れない。
        body = _extract_function(self.js, "renderCourseManagementTable")
        assert "cm-llm-model-btn" in body
        before, sep, after = body.partition("所有者のみ変更可")
        assert sep, '"所有者のみ変更可" の非所有者分岐が見つかりません'
        assert "llmModelBtn" in before
        assert "llmModelBtn" not in after.split("stateHtml", 1)[0]

    def test_modal_uses_learning_chat_scene(self):
        body = _extract_function(self.js, "openCourseModelModal")
        assert 'sceneKey: "learning_chat"' in body

    def test_save_put_includes_llm_models_and_confirm(self):
        body = _extract_function(self.js, "openCourseModelModal")
        assert "llm_models: { learning_chat: toSave || null }" in body
        assert "window.confirm(message)" in body
        assert '"/learning/courses/"' in body

    def test_reads_saved_override_from_admin_courses_list(self):
        """§6.4: 現在の保存値は GET /api/admin/courses の llm_models フィールドから読む
        （未保存ならカタログの実効モデルに追従。捏造しない）。"""
        body = _extract_function(self.js, "openCourseModelModal")
        assert "course.llm_models" in body
        assert "initialModel: currentOverride" in body


class TestNoTierCostOrPlaceholderInNewChipWiring:
    """新規追加したチップ導線にも M2/M3/M8 の禁止語彙が無いことを確認する（部分一致検査）。"""

    def _scoped(self, src: str, marker: str, span: int = 2000) -> str:
        idx = src.index(marker)
        return src[idx : idx + span]

    def test_course_builder_chip_area_has_no_placeholder(self):
        js = _read(ADMIN_JS)
        scoped = self._scoped(js, "_initCourseBuilderModelChip")
        assert "選択してください" not in scoped

    def test_course_settings_modal_has_no_currency_wording(self):
        js = _read(ADMIN_JS)
        scoped = self._scoped(js, "function openCourseModelModal")
        assert "$" not in scoped and "USD" not in scoped and "円" not in scoped
        assert "選択してください" not in scoped


class TestAppJsUnchangedForModelSelection:
    """M9: 学生向け app.js にはモデル選択・モデル名 UI を追加しない（Phase 3 は変更禁止）。"""

    def test_app_js_has_no_llm_model_ui(self):
        app_js = _read(FRONTEND_DIR / "js" / "app.js")
        assert "llm-model" not in app_js
        assert "AdminLlmModels" not in app_js
        assert "createModelChip" not in app_js
        assert "llm_models" not in app_js


class TestInitOrderingRegression:
    """回帰ガード: 教材管理が「読み込み中」のまま止まった初期化順バグ（2026-07-28）。

    initMaterialsPanel() はモジュール内 fetch の実体（deps.apiFetch）を使うため、
    admin.js の initApp では必ず AdminLlmModels.init({...}) を先に呼ばなければならない。
    また deps 未注入で叩かれても同期 TypeError で initApp を巻き込まないよう、
    モジュール側の apiFetch ヘルパーは非関数を rejected Promise に変換する。
    """

    def test_init_is_called_before_init_materials_panel(self):
        src = _read(ADMIN_JS)
        init_idx = src.find("AdminLlmModels.init(")
        panel_idx = src.find("AdminLlmModels.initMaterialsPanel(")
        assert init_idx != -1, "AdminLlmModels.init の呼び出しが admin.js にありません"
        assert panel_idx != -1, "AdminLlmModels.initMaterialsPanel の呼び出しが admin.js にありません"
        assert init_idx < panel_idx, (
            "AdminLlmModels.init(deps) は initMaterialsPanel() より前に呼ぶこと"
            "（deps.apiFetch 未注入のまま fetch すると教材管理の初期化ごと落ちる）"
        )

    def test_module_apifetch_guards_missing_function(self):
        src = _read(LLM_MODELS_JS)
        helper = _extract_function(src, "apiFetch")
        assert "Promise.reject" in helper, (
            "AdminLlmModels の apiFetch ヘルパーは fn 不在時に Promise.reject を返すこと"
            "（同期 throw は呼び出し元の initApp を巻き込む）"
        )


# ---------------------------------------------------------------------------
# Phase 4（設計書 §6.1・§6.6）: ステージ別指定（詳細）+ 運用タブのステージ別セクション
# ---------------------------------------------------------------------------


class TestMaterialsStageDetailPanel:
    """§6.1「▸ ステージ別に指定する（詳細）」: 教材管理の変更パネル内の折りたたみ。"""

    def setup_method(self):
        self.js = _read(LLM_MODELS_JS)

    def test_default_closed(self):
        """M2: 既定で閉じている（mtStagesOpen の初期値 false + hidden 属性の描画）。"""
        assert re.search(r"var\s+mtStagesOpen\s*=\s*false\s*;", self.js)
        body = _extract_function(self.js, "renderMaterialsPanel")
        assert '"llm-model-stages-body"' in body
        assert "mtStagesOpen ?" in body

    def test_toggle_is_wired(self):
        body = _extract_function(self.js, "renderMaterialsPanel")
        assert "toggleMaterialsStages" in body
        toggle_body = _extract_function(self.js, "toggleMaterialsStages")
        assert "mtStagesOpen = !mtStagesOpen" in toggle_body

    def test_calls_pipeline_stages_endpoint(self):
        assert '"/admin/llm-models/pipeline-stages"' in self.js

    def test_stage_fetch_failure_shows_fact_not_crash(self):
        body = _extract_function(self.js, "_loadMaterialsStages")
        assert "取得できませんでした" in body
        assert ".catch(" in body

    def test_vision_stage_uses_vision_catalog(self):
        """vision:true の行は mtCatalog.vision（pipeline.vision の options）を使う。"""
        body = _extract_function(self.js, "_renderMaterialsStagesBody")
        assert "stage.vision ? mtCatalog.vision : mtCatalog.pipeline" in body

    def test_inherit_option_is_default_and_shows_effective_model(self):
        body = _extract_function(self.js, "_renderMaterialsStagesBody")
        assert '<option value="">継承（' in body
        assert "stage.effective.model" in body

    def test_changed_rows_become_run_only_overrides(self):
        """変更した行だけ mtStagesOverrides に積み、getUploadModels が run-only で乗せる。"""
        render_body = _extract_function(self.js, "_renderMaterialsStagesBody")
        assert "mtStagesOverrides[f] = this.value" in render_body
        assert "delete mtStagesOverrides[f]" in render_body

        upload_body = _extract_function(self.js, "getUploadModels")
        assert "for (var feature in mtStagesOverrides)" in upload_body
        assert "models[feature] = mtStagesOverrides[feature]" in upload_body

    def test_stage_overrides_not_persisted_as_user_policy(self):
        """run-only: applyMaterialsSelection（本人の既定を保存する経路）は
        mtStagesOverrides を putMyPolicy に渡さない。"""
        body = _extract_function(self.js, "applyMaterialsSelection")
        assert "mtStagesOverrides" not in body

    def test_reset_clears_stage_overrides(self):
        body = _extract_function(self.js, "resetMaterialsSelection")
        assert "mtStagesOverrides = {}" in body


class TestReanalyzeStageFactLine:
    """§6.1-B: 再解析モーダルは編集 UI を付けず、前回のステージ別指定件数だけ事実文で示す。"""

    def setup_method(self):
        self.js = _read(LLM_MODELS_JS)

    def test_shows_stage_count_fact_when_present(self):
        body = _extract_function(self.js, "initReanalyzePanel")
        assert "_stageOverrideCount" in body
        assert "ステージ別の指定があります" in body

    def test_no_edit_ui_for_stage_overrides_in_reanalyze_panel(self):
        """再解析パネル本体（_renderReanalyzePanelBody）にステージ別 select は追加しない。"""
        body = _extract_function(self.js, "_renderReanalyzePanelBody")
        assert "llm-model-stage-select" not in body

    def test_get_reanalyze_models_forwards_stage_keys(self):
        """orchestrator は body.models を丸ごと置き換えるため、前回のステージ別指定を
        黙って落とさないよう getReanalyzeModels が引き継ぐこと。"""
        body = _extract_function(self.js, "getReanalyzeModels")
        assert 'key.indexOf("pipeline:") === 0' in body
        assert "models[key] = prev[key]" in body


class TestOpsTabStageSection:
    """§6.6「▸ ステージ別の指定（N件）」: 運用タブの feature 単位 system 行セクション。"""

    def setup_method(self):
        self.js = _read(LLM_MODELS_JS)

    def test_hidden_when_zero_rows(self):
        body = _extract_function(self.js, "renderOpsStageSection")
        assert "container.hidden = true" in body
        assert "_featureLevelPolicies" in body

    def test_uses_is_feature_level_flag(self):
        body = _extract_function(self.js, "_featureLevelPolicies")
        assert "is_feature_level" in body

    def test_change_and_reset_reuse_existing_confirm_flow(self):
        """スタブを増やさず、既存の openOpsChangeRow / doOpsReset（confirm 経由の doOpsSave）
        をステージ別行にもそのまま流用する。"""
        body = _extract_function(self.js, "_renderOpsStageBody")
        assert "openOpsChangeRow(this.getAttribute" in body
        assert "doOpsReset(this.getAttribute" in body

    def test_scene_meta_falls_back_to_policy_row_for_feature_keys(self):
        body = _extract_function(self.js, "_sceneMeta")
        assert "_policyRow" in body

    def test_add_new_stage_override_excludes_already_specified(self):
        body = _extract_function(self.js, "_renderOpsStageAddBody")
        assert "existing[s.feature]" in body

    def test_add_flow_reuses_do_ops_save(self):
        body = _extract_function(self.js, "_renderOpsStageAddBody")
        assert "doOpsSave(feature" in body


class TestDisabledChangeButtonShowsReason:
    """回帰ガード: [変更] を無効化するときは理由を必ず併記する（2026-07-28）。

    カタログ未設定時に「無効化された [変更] ボタン」だけが出て理由がどこにも
    表示されず、原因が画面から分からない状態になっていた。M4 の fail-closed は
    「架空の候補を並べない」であって「理由を伏せる」ことではない。
    """

    def test_summary_note_element_exists(self):
        html = _read(ADMIN_HTML)
        assert 'id="llm-model-summary-note"' in html, (
            "解析モデル行に理由表示用の llm-model-summary-note が必要"
        )

    def test_summary_render_sets_note_when_catalog_unavailable(self):
        src = _read(LLM_MODELS_JS)
        render = _extract_function(src, "renderMaterialsSummary")
        assert "_setMaterialsSummaryNote" in render, (
            "renderMaterialsSummary は catalog 不在時に理由の事実文を出すこと"
        )
        assert "選択肢が未設定です" in render

    def test_summary_error_path_also_sets_note(self):
        src = _read(LLM_MODELS_JS)
        render = _extract_function(src, "renderMaterialsSummaryError")
        assert "_setMaterialsSummaryNote" in render


class TestReadOnlySceneRow:
    """J5: 読み取り専用の場面（音声会話）は運用タブで表示のみにする。

    サーバは ``read_only`` / ``read_only_reason`` を返し、PUT は 422 で拒否する。
    フロントは「変更できません」＋理由の事実文を出し、[変更] / [既定に戻す] を出さない
    （settable-but-inert な UI を作らない）。
    """

    def test_ops_table_respects_read_only_flag(self):
        src = _read(LLM_MODELS_JS)
        render = _extract_function(src, "renderOpsTable")
        assert "scene.read_only" in render, "renderOpsTable は read_only を見て分岐すること"
        assert "変更できません" in render
        assert "read_only_reason" in render

    def test_read_only_row_has_no_change_or_reset_button(self):
        src = _read(LLM_MODELS_JS)
        render = _extract_function(src, "renderOpsTable")
        # read_only 分岐（actionsHtml の if 側）にボタンの生成が含まれないこと
        read_only_branch = render.split("if (scene.read_only)", 1)[1].split("} else {", 1)[0]
        assert "llm-models-ops-change-btn" not in read_only_branch
        assert "llm-models-ops-reset-btn" not in read_only_branch

    def test_change_row_shows_fact_sentence_for_read_only_scene(self):
        src = _read(LLM_MODELS_JS)
        change_row = _extract_function(src, "openOpsChangeRow")
        assert "scene.read_only" in change_row, (
            "openOpsChangeRow は read_only の場面で選択肢を描かないこと"
        )

    def test_no_tier_names_in_read_only_wording(self):
        src = _read(LLM_MODELS_JS)
        render = _extract_function(src, "renderOpsTable")
        branch = render.split("if (scene.read_only)", 1)[1].split("} else {", 1)[0]
        for tier in ("fast", "standard", "deep", "analysis"):
            assert tier not in branch.lower()
