"""カテゴリギャップ候補の教員フロントエンド（レビュー UI）静的ガードレール。

正本: docs/features/category_gap_candidates_design.md
  §2   LS1 / AB1 「言い表せなかった主題」の事実文（エラー色・警告アイコン・欠陥語彙禁止）/
       LS5 件数バッジ・カバー率を出さない（支持論文はタイトル列挙）/
       LS7 配置層から骨格を書き換えない（書くのは常に教員の PUT draft）/
       LS9 レビュー画面を開いた瞬間に LLM を呼ばない（読み取りは非LLM・ポーリングなし）
  §5.4 レビュー UI（修正報告セクション内の第2グループ・ボタン3つ・満杯の事実文・
       却下の理由必須と注記・公開前チェックのゲート文言）
  §5.5 骨格への反映（from-frozen 複製 → 決定論 patch プレビュー → 既存 PUT draft →
       mark-incorporated の刻印）+ 凍結完了の事実文

バックエンド（`core/atlas_gaps/` / `routes/atlas_gaps.py` / freeze ゲート）は
`test_atlas_gaps_{guardrails,store,patching,api}.py` が担当する。本ファイルは
admin フロント（`frontend/public/js/admin.js`）+ アンカー3点セットの静的契約のみを
検証する（`test_landscape_admin_ui_static.py` と同じ流儀。API は呼ばない）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ATLAS_MANUAL = ROOT / "docs" / "manual" / "teacher" / "17-admin-atlas.md"
MATERIALS_MANUAL = ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

for _p in (str(BACKEND),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402

GAP_ANCHOR_IDS = (
    "atlas.gap-candidates",
    "atlas.gap-dismissed-filter",
    "atlas.gap-accept",
    "atlas.gap-dismiss",
    "atlas.gap-restore",
    "atlas.gap-incorporate",
    "atlas.gap-draft-from-frozen",
)


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _gap_segment() -> str:
    """admin.js に追加したカテゴリギャップ候補のレビュー節だけを取り出す。"""
    src = _read(ADMIN_JS)
    start = src.index("// ── 論文の解析から見つかった候補（カテゴリギャップ候補, migration 066）")
    end = src.index("    function addDomainOption(key, label) {", start)
    return src[start:end]


def _fn_block(src: str, signature: str) -> str:
    start = src.index(signature)
    return src[start : src.index("\n    }", start)]


def _strip_comment_lines(src: str) -> str:
    """行頭コメント（`// …`）を落とす。

    実装コメントは設計書の条項（「カバー率を出さない」等）を引用するため、文言の
    禁止語彙検査に混ぜると自分の禁止条項の記述で落ちる。検査対象は利用者に出る
    文字列とコードだけにする。
    """
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )


# ===========================================================================
# 1. セクションの定型（§5.4: 修正報告セクション内の第2グループ・専用タブなし）
# ===========================================================================


class TestSectionContract:
    def test_group_is_appended_into_the_reports_section(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function buildGapsGroup() {")
        assert 'document.getElementById("atlas-reports-section")' in block
        assert 'group.id = "atlas-gaps-group";' in block
        assert "section.appendChild(group);" in block

    def test_group_is_not_a_new_tab(self):
        """専用タブ・専用ナビ項目を新設しない（§5.4 / 合意事項5）。"""
        seg = _gap_segment()
        assert "onTabActivate" not in seg
        assert "data-tab" not in seg
        assert "data-atlas-section" not in seg

    def test_heading_and_intro_literals(self):
        seg = _gap_segment()
        assert 'var GAP_GROUP_TITLE = "論文の解析から見つかった候補";' in seg
        assert 'var GAP_GROUP_INTRO = "複数の論文が、この地図にまだ無い項目に触れています。";' in seg

    def test_group_carries_the_section_anchor(self):
        seg = _gap_segment()
        assert 'group.setAttribute("data-ui-anchor", "atlas.gap-candidates");' in seg

    def test_missing_skeleton_hides_the_group(self):
        """骨格なし（404）は領域ごと非表示（fail-closed。空の枠を出さない）。"""
        seg = _gap_segment()
        block = _fn_block(seg, "function loadGapCandidates() {")
        assert "if (res.status === 404) return null;" in block
        assert 'gapsGroupEl.style.display = "none";' in block

    def test_no_polling(self):
        seg = _gap_segment()
        assert "setInterval" not in seg
        assert "setTimeout" not in seg

    def test_reloaded_from_load_state_only(self):
        """再取得はタブ表示（loadState）と各操作の成功後だけ（LS9）。"""
        src = _read(ADMIN_JS)
        load_state = _fn_block(src, "    function loadState() {")
        assert "loadGapCandidates();" in load_state


# ===========================================================================
# 2. 候補カード（§5.4: ラベル編集 / 層バッジ / 支持論文 / 出所 / 版チップ）
# ===========================================================================


class TestCandidateCard:
    def test_label_is_inline_editable_and_sent_as_proposed_label(self):
        seg = _gap_segment()
        assert 'class="atlas-gap-label-input"' in seg
        label_reader = _fn_block(seg, "function _gapLabelFromCard(card) {")
        assert '.querySelector(".atlas-gap-label-input")' in label_reader
        preview = _fn_block(seg, "function gapIncorporate(clusterKey, label) {")
        assert "proposed_label: label" in preview

    def test_edited_label_survives_the_requeue_after_a_decision(self):
        """判断のたびにキューを読み直すため、編集途中の名前を持ち越す。"""
        seg = _gap_segment()
        assert "var gapLabelEdits = {};" in seg
        card = _fn_block(seg, "function _gapCandidateCardHtml(candidate, draftExists, retired) {")
        assert "gapLabelEdits.hasOwnProperty(clusterKey)" in card
        apply_block = _fn_block(seg, "function gapApplyIncorporation(clusterKey, preview) {")
        assert "delete gapLabelEdits[clusterKey]" in apply_block

    def test_layer_badge_names_the_parent_region(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function _gapLayerBadgeText(candidate) {")
        assert '"概念（親: " + parent + "）"' in block
        assert "candidate.parent_region_label" in block
        assert '"領域"' in block

    def test_supporting_documents_are_listed_by_title_and_expand_evidence(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function _gapDocumentsHtml(candidate) {")
        assert "candidate.documents" in block
        assert "d.title" in block
        assert "d.reason" in block
        assert "d.evidence_quote" in block
        assert "<details" in block  # 行を開くと理由と逐語が出る

    def test_origin_label_is_explicit(self):
        seg = _gap_segment()
        assert 'var GAP_ORIGIN_LABEL = "AIによる検出（未確認）";' in seg
        card = _fn_block(seg, "function _gapCandidateCardHtml(candidate, draftExists, retired) {")
        assert "GAP_ORIGIN_LABEL" in card

    def test_version_mismatch_chip_literal(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function _gapVersionChipHtml(candidate) {")
        assert 'escHtml("この候補は版 " + version + " の地図に対するものです")' in block
        assert "candidate.version_mismatch" in block
        # 版が引けなければチップを出さない（推測した版を書かない）
        assert 'if (!version) return "";' in block

    def test_dismissed_rows_offer_restore(self):
        seg = _gap_segment()
        card = _fn_block(seg, "function _gapCandidateCardHtml(candidate, draftExists, retired) {")
        assert 'data-gap-action="restore"' in card
        assert ">見送りから戻す</button>" in card

    def test_dismissed_filter_refetches_with_include_dismissed(self):
        seg = _gap_segment()
        assert 'id="atlas-gaps-show-dismissed"' in seg
        assert "見送り済みも表示" in seg
        load = _fn_block(seg, "function loadGapCandidates() {")
        assert 'gapIncludeDismissed ? "?include_dismissed=true" : ""' in load


# ===========================================================================
# 3. ボタン3つと取り込みフロー（§5.4 / §5.5）
# ===========================================================================


class TestDecisionButtons:
    def test_decide_endpoint_and_actions(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function gapDecide(clusterKey, action, note) {")
        assert 'gapsPath() + "/decide"' in block
        assert 'method: "POST"' in block
        assert "cluster_key: clusterKey" in block
        assert "review_note: note" in block
        card = _fn_block(seg, "function _gapCandidateCardHtml(candidate, draftExists, retired) {")
        for action in ("accept", "dismiss", "incorporate"):
            assert 'data-gap-action="%s"' % action in card

    def test_accept_note_states_that_the_draft_is_unchanged(self):
        seg = _gap_segment()
        assert (
            'var GAP_ACCEPT_NOTE = "[採用] は「カテゴリとして妥当」という判断だけを記録します。'
            '次版の下書きはこの操作では変わりません。";'
        ) in seg

    def test_dismiss_requires_a_reason_before_sending(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function handleGapAction(clusterKey, action, card) {")
        assert 'var note = prompt("却下の理由（必須）") || "";' in block
        assert "if (!note.trim()) { setGapsStatus(GAP_DISMISS_REASON_REQUIRED, true); return; }" in block
        # 空理由のまま decide を呼ばない（gapDecide は理由チェックの後ろだけ）
        idx_guard = block.index("if (!note.trim())")
        idx_call = block.index('gapDecide(clusterKey, "dismiss", note);')
        assert idx_guard < idx_call

    def test_dismiss_note_explains_persistence_and_restore(self):
        seg = _gap_segment()
        assert (
            'var GAP_DISMISS_NOTE = "この分野で同じ名前の候補は今後表示されません。'
            '「見送り済み」フィルタから戻せます。";'
        ) in seg

    def test_incorporate_is_gated_on_accepted_and_draft_and_capacity(self):
        seg = _gap_segment()
        card = _fn_block(seg, "function _gapCandidateCardHtml(candidate, draftExists, retired) {")
        assert "(!accepted || !draftExists || atCapacity || retired ? \" disabled\" : \"\")" in card

    def test_capacity_fact_sentence_without_gauge(self):
        seg = _gap_segment()
        assert (
            'var GAP_AT_CAPACITY_TEXT = "この領域の概念は上限（6件）に達しています。'
            '追加するには次版で既存概念の整理が必要です。";'
        ) in seg
        blocked = _fn_block(seg, "function _gapBlockedText(candidate, accepted, draftExists, retired) {")
        assert "candidate.parent_region_at_capacity" in blocked
        # ゲージ・空きスロットの表示を作らない
        code = _strip_comment_lines(seg)
        for banned in ("空きスロット", "progress", "gauge"):
            assert banned not in code, banned

    def test_from_frozen_confirm_is_a_fact_sentence(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function gapCreateDraftFromFrozen() {")
        assert (
            'confirm("現行版 " + version + " を複製して下書きを作ります。'
            '下書きは学習者には表示されません")'
        ) in block
        assert '"/atlas/skeleton/draft/from-frozen"' in block
        assert 'method: "POST"' in block


class TestIncorporationThreeSteps:
    """§5.5: プレビュー（読み取り専用）→ 教員の既存 PUT draft → 刻印。"""

    def test_step1_preview_endpoint(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function gapIncorporate(clusterKey, label) {")
        assert 'gapsPath() + "/incorporate-preview"' in block
        assert 'method: "POST"' in block

    def test_step2_confirm_shows_node_id_label_and_validation(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function openGapIncorporateConfirm(clusterKey, preview) {")
        assert "preview.node_id" in block
        assert "preview.proposed_label" in block
        assert "preview.parent_region_id" in block
        assert "validation.errors" in block
        assert "validation.warnings" in block
        assert "openDangerConfirmModal(" in block
        # patch が組めなかったときは確認を出さずに事実文で止める
        assert "if (!preview.patched_draft) {" in block

    def test_step3_reuses_the_existing_put_draft_path(self):
        """骨格を書くのは常に教員の PUT（LS7）。gap 専用の保存経路を作らない。"""
        seg = _gap_segment()
        block = _fn_block(seg, "function gapApplyIncorporation(clusterKey, preview) {")
        assert "applyAssistProposal(preview.patched_draft)" in block
        # gap 側から骨格 draft へ直接 PUT / DELETE しない
        assert 'method: "PUT"' not in seg
        assert 'method: "DELETE"' not in seg
        assert '"/atlas/skeleton/draft"' not in seg

    def test_step3_marks_incorporated_after_the_put(self):
        seg = _gap_segment()
        block = _fn_block(seg, "function gapApplyIncorporation(clusterKey, preview) {")
        assert 'gapsPath() + "/mark-incorporated"' in block
        assert "draft_node_id: preview.node_id" in block
        idx_put = block.index("applyAssistProposal(preview.patched_draft)")
        idx_mark = block.index('gapsPath() + "/mark-incorporated"')
        assert idx_put < idx_mark, "mark-incorporated は PUT 成功後に呼ぶ契約"
        assert "loadGapCandidates();" in block

    def test_existing_put_draft_path_keeps_the_optimistic_lock_flow(self):
        """既存 saveDraft の 409 事実文 + 再読込に委ねる（クライアントに再実装しない）。"""
        src = _read(ADMIN_JS)
        save = _fn_block(src, "    function saveDraft() {")
        assert "revision: draftRevision" in save
        assert "if (res.status === 409) {" in save
        assert "loadState();" in save
        apply_block = _fn_block(src, "    function applyAssistProposal(resultSkeleton) {")
        assert "return saveDraft();" in apply_block


# ===========================================================================
# 4. 公開前チェック（freeze）の追随（§5.4 / §5.5）
# ===========================================================================


class TestFreezeGateWiring:
    def _freeze_handler(self) -> str:
        src = _read(ADMIN_JS)
        start = src.index('document.getElementById("atlas-freeze").addEventListener("click", function () {')
        return src[start : src.index("\n    });", start)]

    def test_pending_labels_are_listed_without_counts(self):
        block = self._freeze_handler()
        assert "detail.pending_labels" in block
        assert "GAP_FREEZE_PENDING_TEXT" in block
        assert '(detail.pending_labels || []).join("、")' in block
        assert 'scrollToAtlasSection("atlas-reports-section");' in block
        seg = _gap_segment()
        assert (
            'var GAP_FREEZE_PENDING_TEXT = "採用済みでまだ次版に反映されていない候補が残っています";'
        ) in seg

    def test_pending_labels_message_carries_no_number(self):
        seg = _gap_segment()
        start = seg.index("var GAP_FREEZE_PENDING_TEXT")
        literal = seg[start : seg.index("\n", start)]
        assert re.search(r"\d", literal.split("=", 1)[1]) is None

    def test_freeze_impact_facts_are_passed_through(self):
        block = self._freeze_handler()
        assert "impact.facts" in block
        # 事実文はそのまま差し込む（言い換え・件数の付加をしない）
        assert "checkLines.push(fact)" in block

    def test_freeze_success_states_the_reanalysis_fact(self):
        block = self._freeze_handler()
        assert "GAP_FREEZE_REANALYSIS_TEXT" in block
        seg = _gap_segment()
        assert 'var GAP_FREEZE_REANALYSIS_TEXT = "既存論文の配置は再解析するまで変わりません";' in seg


# ===========================================================================
# 5. 教材管理 landscape モーダルの案内行（§4.1 裁定）
# ===========================================================================


class TestLandscapeModalNotice:
    def test_notice_is_one_fact_line_gated_on_the_server_flag(self):
        src = _read(ADMIN_JS)
        assert (
            'var LANDSCAPE_GAP_SIGNAL_NOTICE = "この分野の地図への候補として記録されています'
            '（分野の地図タブで確認できます）";'
        ) in src
        block = _fn_block(src, "  function _landscapeUnplacedHtml(unplaced) {").replace(
            "\n  }", ""
        )
        assert "item.gap_signals_recorded" in block
        assert "LANDSCAPE_GAP_SIGNAL_NOTICE" in block

    def test_modal_has_no_review_or_map_edit_controls(self):
        """1論文の画面でレビューさせない・「地図を直す」ボタンを置かない（§4.1）。"""
        src = _read(ADMIN_JS)
        start = src.index("// ── 知識ランドスケープ（配置レビュー, migration 065）")
        seg = _strip_comment_lines(src[start : src.index("// ── File Upload ─", start)])
        assert "gap-candidates" not in seg
        assert "data-gap-action" not in seg
        assert "地図を直す" not in seg


# ===========================================================================
# 6. 事実文の温度（LS1 / AB1 / LS5）
# ===========================================================================


class TestNeutralPresentation:
    #: 候補は不備ではなく発見。欠陥語彙・督促語彙・集計数値の語彙を使わない。
    BANNED = (
        "カバー率",
        "網羅率",
        "踏破率",
        "地図の穴",
        "埋めましょう",
        "埋めてください",
        "不足",
        "未整備",
        "欠落",
        "早急",
        "至急",
        "してください、",
    )

    def test_segment_has_no_defect_or_urging_vocabulary(self):
        code = _strip_comment_lines(_gap_segment())
        for term in self.BANNED:
            assert term not in code, term

    def test_candidate_card_uses_no_error_or_warning_styling(self):
        """AB1: エラー色・警告アイコンを候補カードに使わない。"""
        seg = _gap_segment()
        card = _fn_block(seg, "function _gapCandidateCardHtml(candidate, draftExists, retired) {")
        for token in (
            "--color-text-danger",
            "--color-background-warning",
            "--color-text-warning",
            "⚠",
            "❗",
            "admin-status-error",
        ):
            assert token not in card, token
        for helper in (
            "function _gapLayerBadgeText(candidate) {",
            "function _gapVersionChipHtml(candidate) {",
            "function _gapDocumentsHtml(candidate) {",
        ):
            block = _fn_block(seg, helper)
            assert "--color-text-danger" not in block, helper
            assert "⚠" not in block, helper

    def test_no_count_badges_or_raw_confidence(self):
        """LS5: 件数バッジ・生 confidence を描画しない（支持論文はタイトル列挙）。"""
        seg = _gap_segment()
        assert "documents.length" not in seg
        assert re.search(r"\.length\s*\+\s*\"件", seg) is None
        assert re.search(r"\bd\.confidence\b", seg) is None
        assert "confidence_label" not in seg
        assert re.search(r"\.weight\b", seg) is None

    def test_empty_queue_is_a_neutral_fact(self):
        seg = _gap_segment()
        assert 'var GAP_EMPTY_TEXT = "いまレビューする候補はありません。";' in seg

    def test_errors_are_status_lines_not_alerts(self):
        seg = _gap_segment()
        assert "alert(" not in seg
        assert "function setGapsStatus(text, isError) {" in seg


# ===========================================================================
# 7. ES5 準拠（開発ルール5: admin.js は ES5）
# ===========================================================================


class TestGapSegmentIsEs5:
    def test_no_arrow_functions(self):
        assert "=>" not in _gap_segment()

    def test_no_const_or_let(self):
        seg = _gap_segment()
        assert re.search(r"(^|[^\w.$])const\s+\w", seg) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", seg) is None

    def test_no_template_literals_or_class(self):
        seg = _gap_segment()
        assert "`" not in seg
        assert re.search(r"(^|[^\w.$])class\s+\w", seg) is None

    def test_no_promise_finally(self):
        assert ".finally(" not in _gap_segment()


# ===========================================================================
# 8. 3点セット（anchor 表 + マニュアル節）
# ===========================================================================


class TestAnchorThreePieceSet:
    def test_all_gap_anchor_ids_are_registered(self):
        for anchor_id in GAP_ANCHOR_IDS:
            assert anchor_id in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS, anchor_id
            assert anchor_id in admin_anchors_mod.ADMIN_UI_ANCHORS, anchor_id

    def test_anchor_values_point_at_the_atlas_manual(self):
        expected = {
            "atlas.gap-candidates": "teacher/17-admin-atlas.md#gap-candidates",
            "atlas.gap-dismissed-filter": "teacher/17-admin-atlas.md#gap-dismissed-filter",
            "atlas.gap-accept": "teacher/17-admin-atlas.md#gap-accept",
            "atlas.gap-dismiss": "teacher/17-admin-atlas.md#gap-dismiss",
            "atlas.gap-restore": "teacher/17-admin-atlas.md#gap-restore",
            "atlas.gap-incorporate": "teacher/17-admin-atlas.md#gap-incorporate",
            "atlas.gap-draft-from-frozen": "teacher/17-admin-atlas.md#gap-draft-from-frozen",
        }
        for anchor_id, ref in expected.items():
            assert admin_anchors_mod.ADMIN_UI_ANCHORS[anchor_id] == ref

    def test_anchors_resolve_for_teacher_role(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor_id in GAP_ANCHOR_IDS:
            assert anchor_id in resolved, anchor_id
            assert resolved[anchor_id]["title"], anchor_id
            assert resolved[anchor_id]["body"], anchor_id

    def test_anchors_are_carried_by_admin_js(self):
        src = _read(ADMIN_JS)
        assert 'group.setAttribute("data-ui-anchor", "atlas.gap-candidates");' in src
        for anchor_id in GAP_ANCHOR_IDS:
            if anchor_id == "atlas.gap-candidates":
                continue
            assert 'data-ui-anchor="%s"' % anchor_id in src, anchor_id

    def test_manual_sections_have_explicit_anchors(self):
        doc = _read(ATLAS_MANUAL)
        assert "## 論文の解析から見つかった候補 {#gap-candidates}" in doc
        for heading in (
            "### 見送り済みも表示 {#gap-dismissed-filter}",
            "### 採用 {#gap-accept}",
            "### 却下… {#gap-dismiss}",
            "### 見送りから戻す {#gap-restore}",
            "### 次版の下書きに取り込む… {#gap-incorporate}",
            "### 現在の版から次版の下書きを作る {#gap-draft-from-frozen}",
        ):
            assert heading in doc, heading

    def test_manual_documents_the_repetition_threshold_without_numbers_of_documents(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("## 論文の解析から見つかった候補 {#gap-candidates}") :]
        section = section[: section.index("\n## ")]
        assert "複数の論文" in section
        assert "1本の論文" in section
        assert "AIによる検出（未確認）" in section

    def test_manual_has_disabled_button_lines_for_both_gated_buttons(self):
        doc = _read(ATLAS_MANUAL)
        for anchor in ("{#gap-incorporate}", "{#gap-draft-from-frozen}"):
            section = doc[doc.index(anchor) :]
            section = section[: section.index("\n## ")] if "\n## " in section else section
            section = section.split("\n### ")[0]
            assert "**ボタンが無効（グレーアウト）になっている場合**" in section, anchor

    def test_manual_states_the_dismiss_reason_requirement(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("### 却下… {#gap-dismiss}") :].split("\n### ")[0]
        assert "理由の入力は必須" in section
        assert "却下には理由が必要です" in section

    def test_manual_freeze_section_documents_the_gate(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("### 公開前チェック（凍結） {#freeze}") :].split("\n## ")[0]
        assert "採用済みでまだ次版に反映されていない候補" in section
        assert "既存論文の配置は再解析するまで変わりません" in section

    def test_manual_gap_sections_have_no_defect_vocabulary(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("## 論文の解析から見つかった候補 {#gap-candidates}") :]
        section = section[: section.index("\n## ")]
        for term in ("カバー率", "網羅率", "地図の穴", "埋めましょう", "埋めてください", "未整備"):
            assert term not in section, term

    def test_materials_manual_documents_the_notice_line(self):
        doc = _read(MATERIALS_MANUAL)
        assert "### 地図への候補として記録されています {#landscape-gap-signal}" in doc
        section = doc[doc.index("{#landscape-gap-signal}") :].split("\n## ")[0]
        assert "この分野の地図への候補として記録されています" in section
        assert "17-admin-atlas.md#gap-candidates" in section
        # モーダル内でレビューさせない旨（1論文の画面に「地図を直す」を置かない）
        assert "表示だけ" in section

    def test_student_manual_documents_the_unplaced_fact_line(self):
        doc = _read(STUDENT_MANUAL)
        section = doc[doc.index("{#paper-placement}") :]
        assert "どの領域にも配置されていません" in section
        assert "地図がまだ言い表せて" in section
        # 学生向け denylist（管理系の語彙）を持ち込まない
        for term in ("/api/admin", "ADMIN_PASSWORD"):
            assert term not in section, term
