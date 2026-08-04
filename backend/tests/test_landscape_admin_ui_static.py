"""知識ランドスケープ（Knowledge Landscape）の教員フロントエンド静的ガードレール。

正本: docs/features/knowledge_landscape_design.md
  §0  LS3 AI配置は inferred 止まり・確定は教員の明示操作のみ / LS5 数値（weight・
      confidence）を見せない / LS8 骨格版・生成日の事実行 / LS10 配置不能は信号
  §4.1 教員 UX（教材管理 → ⋯ メニュー → 位置づけモーダル）
  §9.1 admin API 契約（GET placements / PATCH placements/{id} / POST propose）
  §10.3 教員フロント（admin.js 内・新規ファイルなし）+ 3点セット（anchor / 表 / マニュアル）

学習者側（`landscape-layer.js` / `app.js` 出典タブ）は `test_landscape_ui_static.py` が
担当する。バックエンド（`/api/admin/landscape/...`）は並行実装中のため、ここでは
admin フロント + アンカー3点セットの静的契約のみを検証する
（`test_figure_studio_ui_static.py` と同じ流儀。API は呼ばない）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
TEACHER_MANUAL = ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"

for _p in (str(BACKEND),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402

LANDSCAPE_ANCHOR_IDS = (
    "materials.row-landscape",
    "materials.landscape-modal",
    "materials.landscape-propose",
)


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    # 他テストが合成ツリー・DB 状態で温めたモジュールキャッシュを持ち込まない／持ち出さない。
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _landscape_segment() -> str:
    """admin.js に追加した知識ランドスケープ節（配置レビュー）だけを取り出す。"""
    src = _read(ADMIN_JS)
    start = src.index("// ── 知識ランドスケープ（配置レビュー, migration 065）")
    end = src.index("// ── File Upload ─", start)
    return src[start:end]


def _fn_block(src: str, signature: str) -> str:
    start = src.index(signature)
    return src[start : src.index("\n  }", start)]


# ===========================================================================
# 1. 教材一覧の ⋯ メニュー項目（§4.1-2 / §10.3）
# ===========================================================================


class TestMaterialsRowMenuItem:
    def test_menu_item_has_class_anchor_and_label(self):
        src = _read(ADMIN_JS)
        assert 'class="ls-menu-item admin-landscape-doc-btn"' in src
        assert 'data-ui-anchor="materials.row-landscape"' in src
        assert "位置づけ（分野マップ）…</button>" in src

    def test_menu_item_requires_document_id(self):
        """document_id の無い教材（解析未到達）には出さない（既存の共有/版と同条件）。"""
        src = _read(ADMIN_JS)
        start = src.index("var landscapeBtn = m.document_id")
        block = src[start : src.index("\n      //", start)]
        assert ': ""' in block, "document_id が無いときは空文字に縮退すること"

    def test_menu_item_is_placed_in_the_more_panel_after_version(self):
        src = _read(ADMIN_JS)
        panel = src[src.index('<div class="ls-menu material-more-panel" hidden>') :]
        panel = panel[: panel.index("</div>")]
        for name in ("versionBtn", "landscapeBtn", "estimateBtn"):
            assert name in panel, name
        assert panel.index("versionBtn") < panel.index("landscapeBtn") < panel.index("estimateBtn")

    def test_menu_item_is_bound_after_render(self):
        src = _read(ADMIN_JS)
        assert 'tbody.querySelectorAll(".admin-landscape-doc-btn").forEach(function (btn) {' in src
        block = src[src.index('tbody.querySelectorAll(".admin-landscape-doc-btn")') :][:600]
        assert 'openLandscapeModal(this.getAttribute("data-document-id"), this.getAttribute("data-title"));' in block


# ===========================================================================
# 2. モーダル定型（§10.3）
# ===========================================================================


class TestLandscapeModalContract:
    def test_modal_functions_exist(self):
        seg = _landscape_segment()
        for signature in (
            "function openLandscapeModal(documentId, title) {",
            "function loadLandscapePlacements(documentId) {",
            "function renderLandscapePlacements(data) {",
        ):
            assert signature in seg, signature

    def test_overlay_carries_id_anchor_and_standard_css(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function openLandscapeModal(documentId, title) {")
        assert 'overlay.id = "landscape-modal";' in block
        assert 'overlay.setAttribute("data-ui-anchor", "materials.landscape-modal");' in block
        assert "z-index:9999" in block
        assert "document.body.appendChild(overlay);" in block

    def test_title_names_the_document(self):
        seg = _landscape_segment()
        assert "位置づけ（分野マップ）: ' + escHtml(title || \"\")" in seg

    def test_background_click_and_close_button_remove_the_overlay(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function openLandscapeModal(documentId, title) {")
        assert 'overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });' in block
        assert 'document.getElementById("landscape-modal-close")' in block

    def test_loader_is_kicked_from_open(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function openLandscapeModal(documentId, title) {")
        assert "loadLandscapePlacements(documentId);" in block

    def test_no_polling(self):
        seg = _landscape_segment()
        assert "setInterval" not in seg
        assert "setTimeout" not in seg


# ===========================================================================
# 3. API 結線（§9.1 の path / method / DTO キー）
# ===========================================================================


class TestApiWiring:
    def test_get_placements_path(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function loadLandscapePlacements(documentId) {")
        assert 'apiFetch("/admin/landscape/documents/" + encodeURIComponent(documentId) + "/placements")' in block

    def test_patch_targets_the_placement_endpoint(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeUpdateStatus(placementId, status, btn) {")
        assert '"/admin/landscape/placements/" + encodeURIComponent(placementId)' in block
        assert 'method: "PATCH"' in block
        assert "JSON.stringify({ status: status })" in block

    def test_patch_offers_exactly_the_three_review_transitions(self):
        seg = _landscape_segment()
        block = seg[seg.index("var LANDSCAPE_STATUS_ACTIONS = [") :]
        block = block[: block.index("\n  ];")]
        assert '["confirmed", "確認"]' in block
        assert '["rejected", "却下"]' in block
        assert '["review_required", "再検討"]' in block
        # superseded への手動遷移は API 契約に無い（§8 store.update_status）。
        assert "superseded" not in block
        assert "inferred" not in block

    def test_status_change_reloads_instead_of_mutating_locally(self):
        """LS3: 状態の正本はサーバ。成功後は一覧を読み直す（楽観更新しない）。"""
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeUpdateStatus(placementId, status, btn) {")
        assert "loadLandscapePlacements(_landscapeModalState.documentId);" in block
        assert "placement.status =" not in block
        assert "p.status =" not in block

    def test_propose_posts_to_the_documents_scoped_endpoint(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeProposeAgain() {")
        assert '"/admin/landscape/documents/" + encodeURIComponent(_landscapeModalState.documentId) + "/placements/propose"' in block
        assert 'method: "POST"' in block

    def test_no_delete_request_against_landscape_endpoints(self):
        """LS3/P4: 配置は行削除しない（削除 API は契約に存在しない）。"""
        src = _read(ADMIN_JS)
        for m in re.finditer(r'method:\s*"DELETE"', src):
            window = src[max(0, m.start() - 600) : m.end() + 600]
            assert "landscape" not in window, "landscape エンドポイントへの DELETE 要求がある"
        seg = _landscape_segment()
        assert "DELETE" not in seg

    def test_dto_keys_match_the_admin_contract(self):
        seg = _landscape_segment()
        for key in (
            "data.placements",
            "data.unplaced_domains",
            "p.domain_key",
            "p.domain_name",
            "p.node_label",
            "p.perspective_label",
            "p.weight_label",
            "p.reason",
            "p.review_note",
        ):
            assert key in seg.replace("(p && p.", "(p.").replace("placement.", "p."), key
        assert "placement.evidence" in seg or "p.evidence" in seg


# ===========================================================================
# 4. 事実文・出所の正直さ（LS1 / LS5 / LS8 / LS10）
# ===========================================================================


class TestFactStatements:
    def test_empty_state_is_a_fact_sentence_naming_the_propose_button(self):
        seg = _landscape_segment()
        assert 'var LANDSCAPE_EMPTY_NOTICE = "配置候補はまだありません。[AIで再提案] で生成できます。";' in seg

    def test_unplaced_domains_are_reported_as_facts(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeUnplacedHtml(unplaced) {")
        assert 'escHtml("この論文は「" + name + "」の地図に配置できませんでした: " + reason)' in block

    def test_footer_states_the_origin_and_skeleton_version(self):
        seg = _landscape_segment()
        assert "AIによる推定（未確認）" in seg
        block = _fn_block(seg, "function _landscapeFooterHtml(data, groups, placements) {")
        assert "LANDSCAPE_CORPUS_NOTICE" in block
        assert '"最終生成: " + formatDateTime(generated)' in block
        assert "使用した骨格" in block

    def test_skeleton_version_is_never_invented(self):
        """版が取れないときは版の行を出さない（推測しない）。"""
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeFrozenVersion(data, group) {")
        assert 'return "";' in block

    def test_status_labels_prefer_the_server_label(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeStatusChipHtml(placement) {")
        assert "placement.status_label" in block
        assert "LANDSCAPE_STATUS_FALLBACK_LABELS[status]" in block

    def test_status_chip_colors_distinguish_the_four_states(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeStatusChipHtml(placement) {")
        assert 'status === "confirmed"' in block and "--color-background-success" in block
        assert 'status === "review_required"' in block and "--color-text-warning" in block
        assert 'status === "rejected"' in block and "text-decoration:line-through" in block

    def test_errors_are_shown_as_fact_sentences_without_alert(self):
        seg = _landscape_segment()
        assert "alert(" not in seg
        assert "function _landscapeNotice(message, isDanger) {" in seg
        assert "var(--color-text-danger)" in seg

    def test_server_detail_is_used_for_429_and_other_failures(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeErrorDetail(res, fallback) {")
        assert "bodyJson && bodyJson.detail" in block
        propose = _fn_block(seg, "function _landscapeProposeAgain() {")
        assert "_landscapeErrorDetail(res," in propose

    def test_skip_and_limit_texts_carry_no_numbers(self):
        """LS5: 上限値・スコアの数値を UI 文言に書かない。"""
        seg = _landscape_segment()
        block = seg[seg.index("var LANDSCAPE_SKIP_TEXT = {") :]
        block = block[: block.index("\n  };")]
        assert re.search(r"\d", block) is None, "skip 事実文に数値が含まれている"
        assert "daily_call_limit_reached" in block
        assert "no_frozen_skeleton" in block
        assert "no_source_material" in block


class TestNoNumericExposure:
    """LS5: weight / confidence の生数値を描画しない（*_label のみ）。"""

    def test_no_raw_weight_field_is_rendered(self):
        seg = _landscape_segment()
        assert re.search(r"\.weight\b", seg) is None, "生 weight を参照している"
        assert re.search(r"\[\s*[\"']weight[\"']\s*\]", seg) is None

    def test_no_confidence_field_is_referenced(self):
        seg = _landscape_segment()
        assert re.search(r"\.confidence\b", seg) is None, "生 confidence を参照している"
        assert re.search(r"\[\s*[\"']confidence[\"']\s*\]", seg) is None


# ===========================================================================
# 5. 再提案ボタン（§10.3: 実行中 disabled / 429 は事実文）
# ===========================================================================


class TestProposeButton:
    def test_button_carries_class_and_anchor(self):
        seg = _landscape_segment()
        assert 'class="admin-action-btn admin-landscape-propose-btn"' in seg
        assert 'data-ui-anchor="materials.landscape-propose"' in seg
        assert ">AIで再提案</button>" in seg

    def test_button_is_disabled_while_in_flight(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeProposeAgain() {")
        assert "if (_landscapeModalState.proposing) return;" in block
        assert "_landscapeModalState.proposing = true;" in block
        assert "btn.disabled = true;" in block
        assert 'btn.textContent = "生成中...";' in block

    def test_button_is_restored_on_both_success_and_failure(self):
        seg = _landscape_segment()
        reset = _fn_block(seg, "function _landscapeResetProposeButton() {")
        assert "_landscapeModalState.proposing = false;" in reset
        assert "btn.disabled = false;" in reset
        assert 'btn.textContent = "AIで再提案";' in reset
        block = _fn_block(seg, "function _landscapeProposeAgain() {")
        assert block.count("_landscapeResetProposeButton();") >= 2

    def test_success_reloads_the_list(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeProposeAgain() {")
        assert "loadLandscapePlacements(_landscapeModalState.documentId);" in block

    def test_stage_payload_skip_vocabulary_is_surfaced(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeProposeResultText(payload) {")
        assert "payload.skipped_by_limit" in block
        assert "payload.skipped_reason" in block
        assert "LANDSCAPE_SKIP_TEXT[reason]" in block

    def test_status_buttons_are_serialized_against_double_writes(self):
        seg = _landscape_segment()
        block = _fn_block(seg, "function _landscapeUpdateStatus(placementId, status, btn) {")
        assert "if (_landscapeModalState.busy) return;" in block
        assert "_landscapeModalState.busy = true;" in block
        assert "_landscapeModalState.busy = false;" in block
        assert "btn.disabled = true;" in block


# ===========================================================================
# 6. ES5 準拠（開発ルール5: admin.js は ES5）
# ===========================================================================


class TestLandscapeSegmentIsEs5:
    def test_no_arrow_functions(self):
        assert "=>" not in _landscape_segment()

    def test_no_const_or_let(self):
        seg = _landscape_segment()
        assert re.search(r"(^|[^\w.$])const\s+\w", seg) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", seg) is None

    def test_no_template_literals_or_class(self):
        seg = _landscape_segment()
        assert "`" not in seg
        assert re.search(r"(^|[^\w.$])class\s+\w", seg) is None

    def test_no_promise_finally(self):
        assert ".finally(" not in _landscape_segment()


# ===========================================================================
# 7. 3点セット（anchor 表 + マニュアル節）
# ===========================================================================


class TestAnchorThreePieceSet:
    def test_three_anchor_ids_are_registered(self):
        for anchor_id in LANDSCAPE_ANCHOR_IDS:
            assert anchor_id in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS, anchor_id
            assert anchor_id in admin_anchors_mod.ADMIN_UI_ANCHORS, anchor_id

    def test_anchor_values_point_at_the_materials_manual(self):
        expected = {
            "materials.row-landscape": "teacher/11-admin-materials.md#landscape-open",
            "materials.landscape-modal": "teacher/11-admin-materials.md#landscape-modal",
            "materials.landscape-propose": "teacher/11-admin-materials.md#landscape-propose",
        }
        for anchor_id, ref in expected.items():
            assert admin_anchors_mod.ADMIN_UI_ANCHORS[anchor_id] == ref

    def test_anchors_resolve_for_teacher_role(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor_id in LANDSCAPE_ANCHOR_IDS:
            assert anchor_id in resolved, anchor_id
            section = resolved[anchor_id]
            assert section["title"], anchor_id
            assert section["body"], anchor_id

    def test_anchors_are_carried_by_admin_js(self):
        """双方向網羅（test_admin_help_inspect_ui_static.py）と同じく素の ID 出現で判定する。

        モーダル overlay は属性直書きではなく setAttribute で付けるため、
        属性文字列だけを探すと担体を見落とす。
        """
        src = _read(ADMIN_JS)
        for anchor_id in LANDSCAPE_ANCHOR_IDS:
            assert anchor_id in src, anchor_id
        assert 'data-ui-anchor="materials.row-landscape"' in src
        assert 'data-ui-anchor="materials.landscape-propose"' in src
        assert 'overlay.setAttribute("data-ui-anchor", "materials.landscape-modal");' in src

    def test_manual_sections_have_explicit_anchors(self):
        doc = _read(TEACHER_MANUAL)
        assert "## 位置づけ（分野マップ） {#landscape}" in doc
        assert "### 位置づけ（分野マップ）… {#landscape-open}" in doc
        assert "### 位置づけモーダル {#landscape-modal}" in doc
        assert "### AIで再提案 {#landscape-propose}" in doc

    def test_manual_documents_the_status_vocabulary_and_actions(self):
        doc = _read(TEACHER_MANUAL)
        for term in ("教員確認済み", "AIによる推定（未確認）", "確認待ち（AI推定）", "却下"):
            assert term in doc, term
        assert "**[確認]**" in doc
        assert "**[却下]**" in doc
        assert "**[再検討]**" in doc

    def test_manual_states_the_learner_visibility_rule(self):
        doc = _read(TEACHER_MANUAL)
        assert "**[却下]した配置は学習者側からすぐに見えなくなります。**" in doc

    def test_manual_has_the_disabled_button_line_for_propose(self):
        doc = _read(TEACHER_MANUAL)
        section = doc[doc.index("### AIで再提案 {#landscape-propose}") :]
        section = section[: section.index("\n## ")]
        assert "**ボタンが無効（グレーアウト）になっている場合**" in section

    def test_manual_lists_the_menu_item_in_the_more_menu(self):
        doc = _read(TEACHER_MANUAL)
        assert "[位置づけ（分野マップ）…](#landscape-open)" in doc
