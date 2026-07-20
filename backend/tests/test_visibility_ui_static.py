"""ビジョン×UXギャップ調査 G1-1/G5-2/G5-3/G5-4 の管理画面フロント静的ガードレール。

対象:
  - G1-1: コース/教材の公開・開示範囲 UI（renderCourseManagementTable の状態列・
    クイック公開トグル、openPermissionModal / openDocumentShareModal の
    「開示範囲」セクション、Admin Copilot の UI アンカーがテーブル全体
    フォールバックではなく実コントロールを返すこと）
  - G5-2: コース管理タブが is_published / atlas binding を捨てずに描画すること
  - G5-3: 不可逆・破壊的操作の確認 UI が openDangerConfirmModal（明示ボタン
    クリックを要求する2段確認）に統一されていること（削除系のみ引き続き
    名前入力必須の openDeleteConfirmModal を使う）
  - G5-4: パイプラインメニューの表示ラベルが教員向け日本語になっていること、
    「共有設定」（グループ共有・開示範囲）と「版の管理」（V層バージョン）の
    ラベルが区別されていること、動的「スキーマ管理」タブが「スキーマ提案」と
    紛れない名称に変わっていること

すべて静的解析（正規表現・部分文字列検索）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
STYLES_CSS = FRONTEND_DIR / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# G5-3: 危険操作確認モーダルの統一
# ---------------------------------------------------------------------------


class TestDangerConfirmModal:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_open_danger_confirm_modal_defined(self):
        assert "function openDangerConfirmModal" in self.js

    def test_danger_confirm_modal_requires_explicit_button_click(self):
        """window.confirm() の Enter キー一発ではなく、専用ボタンのクリックを要求する
        （2段確認）。cancel/exec 両ボタンを持つこと。"""
        body = self.js.split("function openDangerConfirmModal")[1].split("\n  function ")[0]
        assert 'id="danger-confirm-cancel-btn"' in body
        assert 'id="danger-confirm-exec-btn"' in body
        assert "if (typeof onConfirm === \"function\") onConfirm();" in body

    @staticmethod
    def _assert_migrated(js: str, old_confirm_snippet: str, label: str):
        assert old_confirm_snippet not in js, f"{label}: 素の confirm() がまだ残っています"

    def test_pipeline_single_stage_retry_migrated(self):
        self._assert_migrated(
            self.js,
            'if (!confirm("ステージ「" + stage + "」を再実行します。既存の結果は上書きされます。よろしいですか？")) return;',
            "パイプライン単一ステージ再実行",
        )

    def test_pipeline_menu_item_migrated(self):
        self._assert_migrated(
            self.js,
            'if (!confirm("既存の実行結果を上書きします。本当に実行しますか？")) return;',
            "パイプラインメニュー実行",
        )

    def test_library_freeze_migrated(self):
        self._assert_migrated(
            self.js,
            'if (!confirm("凍結版はパイプラインの解析に使われます。取り消せません（修正は次版で行います）。よろしいですか？")) return;',
            "ライブラリエントリ凍結",
        )

    def test_library_retire_migrated(self):
        self._assert_migrated(
            self.js,
            'if (!confirm("このエントリを廃止します。retrieval対象から外れますが、履歴・provenanceは保持されます。よろしいですか？")) return;',
            "ライブラリエントリ廃止",
        )

    def test_atlas_skeleton_freeze_migrated(self):
        self._assert_migrated(self.js, "if (!confirm(check)) return;", "分野の地図骨格の凍結")

    def test_group_delete_migrated(self):
        self._assert_migrated(
            self.js,
            'if (!confirm("グループ「" + g.name + "」を削除します。よろしいですか？")) return;',
            "グループ削除",
        )

    def test_migrated_call_sites_use_danger_modal(self):
        """移行した6箇所すべてが openDangerConfirmModal を呼んでいること
        （+ クイック公開トグル・開示範囲の public 確認の呼び出しも含め、
        定義1件 + 呼び出し6件以上が存在すること）。"""
        calls = re.findall(r"openDangerConfirmModal\(", self.js)
        assert len(calls) >= 7  # 定義1 + 移行済み呼び出し6以上

    def test_delete_still_uses_name_confirmation_modal(self):
        """削除系（名前入力必須）は引き続き openDeleteConfirmModal を使う
        （重大度に対して簡素すぎる/複雑すぎる、の逆転を作らない）。"""
        assert "function openDeleteConfirmModal" in self.js
        assert 'openDeleteConfirmModal("material"' in self.js
        assert 'openDeleteConfirmModal(kind, id, name)' in self.js or "function openDeleteConfirmModal(kind, id, name)" in self.js


# ---------------------------------------------------------------------------
# G1-1: コースの公開・開示範囲 UI
# ---------------------------------------------------------------------------


class TestCourseVisibilityUi:
    def setup_method(self):
        self.js = _read(ADMIN_JS)
        self.html = _read(ADMIN_HTML)

    def test_course_management_table_has_state_column(self):
        assert "<th>公開・地図</th>" in self.html

    def test_course_management_colspan_updated(self):
        """新しい列を足したので読み込み中/コースなし行の colspan もずれていないこと。"""
        assert 'colspan="5"' in self.html
        # cm-tbody 用の JS 生成メッセージも同様に更新されていること
        cm_section = self.js.split("function loadCourseManagement")[1].split("function renderCourseManagementTable")[0]
        assert 'colspan="5"' in cm_section

    def test_render_course_table_shows_visibility_badge(self):
        assert "function courseVisibilityBadgeHtml" in self.js
        assert "function courseVisibilityLabel" in self.js
        assert "courseVisibilityBadgeHtml(c)" in self.js

    def test_render_course_table_shows_atlas_binding_text(self):
        """G5-2: is_published だけでなく atlas binding 状態も描画すること。"""
        assert "function courseAtlasBindingText" in self.js
        body = self.js.split("function courseAtlasBindingText")[1].split("\n  function ")[0]
        assert "atlas_cartridge_id" in body
        assert "地図: " in body

    def test_quick_publish_toggle_button_rendered(self):
        assert "cm-quick-publish-btn" in self.js
        assert "公開する" in self.js
        assert "公開を止める" in self.js

    def test_quick_publish_toggle_calls_visibility_api(self):
        body = self.js.split("cm-quick-publish-btn\").forEach")[1].split("\n  }")[0]
        assert '/admin/courses/" + encodeURIComponent(courseId) + "/visibility"' in body
        assert 'method: "PUT"' in body

    def test_permission_modal_has_visibility_section(self):
        """openPermissionModal に開示範囲セクションが統合されていること
        （モーダルを増やさない）。"""
        assert 'id="cm-visibility-select"' in self.js
        assert 'id="cm-visibility-group-select"' in self.js
        assert 'id="cm-visibility-apply-btn"' in self.js
        assert "function applyCourseVisibility" in self.js
        assert "function initCourseVisibilitySection" in self.js

    def test_apply_course_visibility_calls_api(self):
        body = self.js.split("function applyCourseVisibility")[1].split("\n  function ")[0]
        assert '/admin/courses/" + encodeURIComponent(courseId) + "/visibility"' in body
        assert 'method: "PUT"' in body

    def test_public_apply_requires_danger_confirm(self):
        """開示範囲を public にする適用は、公開状態のクイック操作と同様に
        確認モーダルを経由すること。"""
        body = self.js.split("function applyCourseVisibility")[1].split("\n  function ")[0]
        assert "openDangerConfirmModal" in body


# ---------------------------------------------------------------------------
# G1-1: 教材の公開・開示範囲 UI
# ---------------------------------------------------------------------------


class TestMaterialVisibilityUi:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_share_button_passes_material_id_and_visibility(self):
        body = self.js.split("var shareBtn = m.document_id")[1].split("var versionBtn")[0]
        assert "data-material-id=" in body
        assert "data-visibility=" in body
        assert "data-group-id=" in body

    def test_open_document_share_modal_accepts_visibility_args(self):
        assert re.search(
            r"function openDocumentShareModal\(documentId,\s*title,\s*materialId,\s*visibility,\s*groupId\)",
            self.js,
        )

    def test_document_share_modal_has_visibility_section(self):
        assert 'id="doc-visibility-select"' in self.js
        assert 'id="doc-visibility-group-select"' in self.js
        assert 'id="doc-visibility-apply-btn"' in self.js
        assert "function applyMaterialVisibility" in self.js

    def test_apply_material_visibility_calls_api(self):
        body = self.js.split("function applyMaterialVisibility")[1].split("\n  function ")[0]
        assert '/admin/materials/" + encodeURIComponent(_docShareState.materialId) + "/visibility"' in body
        assert 'method: "PUT"' in body

    def test_apply_material_visibility_requires_material_id(self):
        body = self.js.split("function applyMaterialVisibility")[1].split("\n  function ")[0]
        assert "_docShareState.materialId" in body


# ---------------------------------------------------------------------------
# G1-6: Admin Copilot の UI アンカーが実コントロールを返す
# ---------------------------------------------------------------------------


class TestAssistantAnchorsResolveRealControls:
    def setup_method(self):
        self.js = _read(ADMIN_JS)
        self.hooks_body = self.js.split("function registerAssistantHooks")[1].split("\n  // ── 反復改善")[0]

    def test_course_visibility_control_not_table_fallback_only(self):
        """course_visibility_control がテーブル全体 (#cm-tbody) だけを返す
        フォールバックになっていないこと（G1-6 の指摘）。"""
        m = re.search(r"course_visibility_control:\s*function\s*\(\)\s*\{(.*?)\n\s*\},", self.hooks_body, re.S)
        assert m, "course_visibility_control アンカーが見つかりません"
        body = m.group(1)
        assert "cm-visibility-select" in body

    def test_publish_button_not_table_fallback_only(self):
        m = re.search(r"publish_button:\s*function\s*\(\)\s*\{(.*?)\n\s*\},", self.hooks_body, re.S)
        assert m, "publish_button アンカーが見つかりません"
        body = m.group(1)
        assert "cm-visibility-apply-btn" in body or "cm-quick-publish-btn" in body

    def test_material_visibility_control_points_to_real_select(self):
        assert 'material_visibility_control: function () { return document.getElementById("doc-visibility-select"); }' in self.hooks_body

    def test_row_anchors_track_last_selected_id(self):
        """course_row / material_row が『直近に選ばれた行』を記憶し、
        パラメータなしアンカー（visibility_control 等）がそれを使って
        実コントロールへフォールバックできること。"""
        assert "_cmLastAnchoredCourseId = id;" in self.hooks_body
        assert "_matLastAnchoredMaterialId = id;" in self.hooks_body


# ---------------------------------------------------------------------------
# G5-4: 命名・ラベルの整理
# ---------------------------------------------------------------------------


class TestLabelDisambiguation:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_pipeline_stage_labels_are_japanese(self):
        """パイプラインメニューの表示ラベルが内部 agent 名のままになっていないこと。
        内部ステージキー（entry[0]）は変えない。"""
        assert "DocumentStructureAgent" not in self.js
        assert "PaperSkeletonAgent" not in self.js
        assert "ComponentGraphAgent" not in self.js
        stages_block = self.js.split("var materialPipelineStageGroups = [")[1].split("\n  var materialPipelineStages")[0]
        assert '["document_structure", "文書構造の復元"]' in stages_block
        assert '["component_graph", "理論操作グラフの構築"]' in stages_block

    def test_share_vs_version_button_labels_disambiguated(self):
        """グループ共有（共有設定）と V層バージョン管理（版の管理）が別ラベルで
        区別されていること。"""
        assert ">共有設定</button>" in self.js
        assert ">版の管理</button>" in self.js
        # 旧来の紛らわしい短いラベルが復活していないこと
        assert ">共有</button>" not in self.js
        assert ">共有版</button>" not in self.js

    def test_schema_evolution_tab_renamed(self):
        """動的追加の『スキーマ管理』タブが、静的な『スキーマ提案』タブと
        紛れない名称に変わっていること。"""
        assert 'schemaTab.textContent = "スキーマ管理";' not in self.js
        assert "DSL進化分析" in self.js

    def test_schema_proposals_tab_label_unchanged(self):
        """紛れの原因だった側（静的『スキーマ提案』タブ）は変更しない。"""
        html = _read(ADMIN_HTML)
        assert 'data-tab="schema-proposals" role="menuitem">スキーマ提案</button>' in html


# ---------------------------------------------------------------------------
# CSS: 開示範囲バッジ
# ---------------------------------------------------------------------------


class TestVisibilityBadgeCss:
    def test_badge_classes_defined(self):
        css = _read(STYLES_CSS)
        assert ".status-visibility-public" in css
        assert ".status-visibility-group" in css
        assert ".status-visibility-private" in css
