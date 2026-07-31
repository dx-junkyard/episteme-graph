"""原稿スタジオ「根拠リンク」ペイン改善（Phase 0 重複解消・双方向化 / Phase 1 上位下位
コンテキストの遅延ロード）に対する静的ガードレール。

対象は `frontend/public/js/admin-lecture-studio.js` と `frontend/public/css/styles.css`
のみ（バックエンドの `/context` エンドポイントは並行作業の別担当なので、本テストは
フロント側の契約だけを固定する）。

固定する契約:
- Phase 0-1: claim/source 系の title が短い抜粋（`title_is_excerpt`）になり、カードは
  `title_is_excerpt` または `title === summary` のとき太字ヘッダを描かない（旧保存データ
  にも効く後方互換ガード）。一方でチップは従来どおり title を使う（title を空にしない）。
- Phase 0-2: 右カードヘッダが開閉トグルとして配線され、展開時に左ドラフトの
  `[data-evidence-ref]` へスクロール + ハイライトする（双方向化）。
- Phase 1: 展開時に W層 context lens を遅延フェッチ（初回のみ・キャッシュ・コース切替の
  ステイル応答破棄）し、上位/下位レーンと裏付けラベルを描く。source はフェッチしない。
- confidence 等の生数値を描画しないこと（W8）。
- 追加領域が ES5 準拠（開発ルール5: アロー関数・const/let・テンプレートリテラル禁止）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read() -> str:
    return ADMIN_LS_JS.read_text(encoding="utf-8")


def _block(src: str, start_marker: str, end_marker: str) -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


# ---------------------------------------------------------------------------
# Phase 0-1: 重複表示（title と summary の同一文2度描画）の解消
# ---------------------------------------------------------------------------


class TestDuplicateTitleGuard:
    def test_source_items_get_short_excerpt_title_with_flag(self):
        """claim/source 系は title を短い抜粋にし、title_is_excerpt を立てる。"""
        src = _read()
        block = _block(src, "function lsTopicEvidenceItems(topic) {", "function lsEvidenceItemByRef(")
        assert "srcTitleIsExcerpt" in block
        assert "lsShortSummary(srcSummary, 60)" in block
        assert "title_is_excerpt: srcTitleIsExcerpt" in block

    def test_card_title_guard_helper_checks_flag_and_equality(self):
        """title_is_excerpt と title === summary の両方で太字ヘッダを抑止する。"""
        src = _read()
        block = _block(src, "function lsEvidenceCardShowsTitle(item) {", "function lsCourseEvidenceHtml(topic) {")
        assert "item.title_is_excerpt" in block
        assert "title === summary" in block

    def test_card_head_renders_title_only_through_guard(self):
        """カードの <strong> 描画が必ずガード関数の分岐下にあること。"""
        src = _read()
        block = _block(src, "function lsCourseEvidenceHtml(topic) {", "function lsFocusEvidence(key) {")
        assert "lsEvidenceCardShowsTitle(item) ? '<strong>' + escHtml(item.title)" in block
        # ガードを通らない素の <strong>title 描画が残っていないこと
        assert block.count("<strong>") == 1

    def test_chips_still_use_title(self):
        """チップは title を使い続ける（title を空にしない設計の担保）。"""
        src = _read()
        block = _block(src, "function lsCourseEvidenceChipsHtml(topic) {", "function lsEvidenceCardShowsTitle(")
        assert "escHtml(item.title)" in block


# ---------------------------------------------------------------------------
# Phase 0-2: 右カード → 左ドラフトの逆リンク（双方向化）
# ---------------------------------------------------------------------------


class TestCardToDraftWiring:
    def test_card_head_is_a_toggle_button_with_anchor(self):
        src = _read()
        block = _block(src, "function lsCourseEvidenceHtml(topic) {", "function lsFocusEvidence(key) {")
        assert 'button type="button" class="ls-course-evidence-head" data-evidence-toggle=' in block
        assert 'data-ui-anchor="lecture-studio.evidence-context"' in block
        assert 'aria-expanded="false"' in block

    def test_draft_focus_helper_targets_evidence_ref_and_highlights(self):
        src = _read()
        block = _block(src, "function lsFocusDraftEvidence(key) {", "// ── 根拠リンクカードの上位/下位コンテキスト")
        assert 'document.getElementById("ls-source-text")' in block
        assert "[data-evidence-ref=" in block
        assert "ls-draft-evidence-focus" in block
        assert "scrollIntoView" in block

    def test_card_binder_toggles_and_focuses_draft(self):
        src = _read()
        block = _block(src, "function lsBindCourseEvidenceCards(topic, container) {", "\n  function lsRenderCourseListPreview(")
        assert '[data-evidence-toggle]' in block
        assert 'classList.contains("expanded")' in block
        assert "lsLoadEvidenceContext(topic, itemsByKey[key], body)" in block
        assert "lsFocusDraftEvidence(key)" in block
        assert "[data-evidence-draft]" in block

    def test_right_pane_render_binds_cards(self):
        src = _read()
        block = _block(src, "function lsRenderRightPaneForTopic(topic) {", "function lsLoadStumbleSummary(")
        assert "preview.innerHTML = lsCourseEvidenceHtml(topic);" in block
        assert "lsBindCourseEvidenceCards(topic, preview);" in block

    def test_draft_highlight_class_styled(self):
        css = STYLES_CSS.read_text(encoding="utf-8")
        assert ".ls-draft-evidence-focus" in css
        assert "button.ls-course-evidence-head" in css
        assert ".ls-evidence-context-lane" in css
        assert ".ls-context-status-source_backed" in css
        assert ".ls-context-status-candidate" in css


# ---------------------------------------------------------------------------
# Phase 1: 上位/下位コンテキストの遅延ロード
# ---------------------------------------------------------------------------


class TestContextLazyLoad:
    def test_kind_to_element_type_map_excludes_source(self):
        """source は文脈フェッチ対象にしない（原文抜粋は「要素」ではない）。"""
        src = _read()
        block = _block(
            src,
            "var LS_EVIDENCE_CONTEXT_ELEMENT_TYPES = {",
            "var LS_EVIDENCE_CONTEXT_KINDS = {",
        )
        assert "component: \"theory_component\"" in block
        assert "claim: \"theory_claim\"" in block
        assert "equation: \"equation\"" in block
        assert "figure: \"figure\"" in block
        assert "source" not in block

    def test_reverse_map_defined_for_local_jump(self):
        src = _read()
        block = _block(src, "var LS_EVIDENCE_CONTEXT_KINDS = {", "var LS_CONTEXT_STATUS_LABELS = {")
        assert "theory_component:" in block
        assert "theory_claim:" in block

    def test_relation_status_labels_are_japanese(self):
        src = _read()
        block = _block(src, "var LS_CONTEXT_STATUS_LABELS = {", "function lsEvidenceContextElementType(kind) {")
        assert "出典に裏付け" in block
        assert "教員確定" in block
        assert "AI候補" in block

    def test_fetch_hits_context_endpoint_with_document_id(self):
        src = _read()
        block = _block(src, "function lsLoadEvidenceContext(topic, item, container) {", "function lsEvidenceContextStillVisible(")
        assert 'apiFetch("/admin/deliberation/elements/"' in block
        assert '"/context?document_id="' in block
        assert "encodeURIComponent(documentId)" in block

    def test_fetch_is_cached_and_stale_guarded(self):
        src = _read()
        block = _block(src, "function lsLoadEvidenceContext(topic, item, container) {", "function lsEvidenceContextStillVisible(")
        assert "lsState.evidenceContextByKey[cacheKey]" in block
        assert "courseId !== lsState.courseId" in block
        assert "lsEvidenceContextStillVisible(container)" in block

    def test_cache_declared_in_state_and_cleared_on_course_switch(self):
        src = _read()
        assert "evidenceContextByKey: {}," in src
        assert src.count("lsState.evidenceContextByKey = {};") >= 2

    def test_source_kind_degrades_without_fetch(self):
        src = _read()
        block = _block(src, "function lsLoadEvidenceContext(topic, item, container) {", "function lsEvidenceContextStillVisible(")
        # element_type が引けない kind（= source）はフェッチせず事実文で縮退する
        assert "if (!elementType) {" in block
        idx_guard = block.index("if (!elementType) {")
        idx_fetch = block.index("apiFetch(")
        assert idx_guard < idx_fetch

    def test_fetch_failure_degrades_with_factual_text(self):
        src = _read()
        block = _block(src, "function lsLoadEvidenceContext(topic, item, container) {", "function lsEvidenceContextStillVisible(")
        assert "コンテキストを取得できませんでした。" in block
        assert ".catch(function () {" in block

    def test_lanes_render_upper_and_lower_with_status_badge(self):
        src = _read()
        block = _block(src, "function lsRenderEvidenceContext(topic, item, container, data) {", "function lsEvidenceContextLocalMatch(")
        assert "上位（この要素が支えるもの）" in block
        assert "下位（この要素を支えるもの）" in block
        assert "lsContextStatusBadgeHtml(ctxItem.relation_status)" in block
        assert "relation_label" in block
        assert "一般には: " in block

    def test_unidentified_role_is_not_rendered(self):
        src = _read()
        block = _block(src, "function lsRenderEvidenceContext(topic, item, container, data) {", "function lsEvidenceContextLaneHtml(")
        assert 'roleStatus !== "unidentified"' in block

    def test_local_match_uses_id_then_prefix_heuristic(self):
        src = _read()
        block = _block(src, "function lsEvidenceContextLocalMatch(topic, ctxItem) {", "function lsBindEvidenceContextActions(")
        assert "lsEvidenceItemByRef(topic, kind, ctxItem.element_id)" in block
        assert "indexOf(label) === 0" in block

    def test_item_navigation_prefers_pane_jump_then_deliberation(self):
        src = _read()
        block = _block(src, "function lsEvidenceContextItemHtml(topic, ctxItem) {", "function lsEvidenceContextLocalMatch(")
        assert "data-context-jump=" in block
        assert "ctxItem.navigable && ctxItem.element_id && window.Deliberation" in block
        assert 'data-ui-anchor="lecture-studio.component-deliberate"' in block

    def test_deliberation_open_uses_document_and_title_options(self):
        src = _read()
        block = _block(src, "function lsBindEvidenceContextActions(container) {", "function lsRevealEvidenceDeliberateButton(")
        assert "window.Deliberation.openElement(" in block
        assert "documentId:" in block
        assert "title:" in block

    def test_footer_deliberate_button_hidden_until_focus_resolved(self):
        src = _read()
        html_block = _block(src, "function lsCourseEvidenceHtml(topic) {", "function lsFocusEvidence(key) {")
        assert "data-evidence-deliberate=" in html_block
        assert "hidden>深く検討" in html_block
        reveal = _block(src, "function lsRevealEvidenceDeliberateButton(container, item, focus) {", "// 右ペイン根拠カードの配線")
        assert "focus.element_id" in reveal
        assert "btn.hidden = true" in reveal
        assert "!window.Deliberation" in reveal


# ---------------------------------------------------------------------------
# W8: 数値（confidence 等）を描画しない
# ---------------------------------------------------------------------------


class TestNoRawNumbers:
    def test_context_rendering_never_prints_confidence(self):
        """文脈描画コード（コメント除く）が confidence を一切参照しないこと。"""
        src = _read()
        region = _block(
            src,
            "// ── 根拠リンクカードの上位/下位コンテキスト",
            "\n  function lsRenderCourseListPreview(",
        )
        code = "\n".join(
            line for line in region.splitlines() if not line.strip().startswith("//")
        )
        assert "confidence" not in code

    def test_context_status_badge_only_emits_stage_labels(self):
        src = _read()
        block = _block(src, "function lsContextStatusBadgeHtml(status) {", "function lsEvidenceContextFactHtml(")
        assert "LS_CONTEXT_STATUS_LABELS[status]" in block
        # マップに無い値は何も描かない（生の内部語彙・数値を出さない）
        assert 'if (!label) return "";' in block


# ---------------------------------------------------------------------------
# ES5 準拠（開発ルール5）
# ---------------------------------------------------------------------------


class TestEs5Guard:
    def _regions(self):
        src = _read()
        return [
            _block(src, "function lsEvidenceCardShowsTitle(item) {", "\n  function lsFocusEvidence(key) {"),
            _block(src, "function lsFocusDraftEvidence(key) {", "\n  function lsRenderCourseListPreview("),
        ]

    def test_no_arrow_functions(self):
        for region in self._regions():
            assert "=>" not in region

    def test_no_const_or_let(self):
        for region in self._regions():
            assert re.search(r"\bconst\s+\w", region) is None
            assert re.search(r"\blet\s+\w", region) is None

    def test_no_template_literals(self):
        for region in self._regions():
            assert "`" not in region

    def test_uses_var_or_function(self):
        for region in self._regions():
            assert "var " in region or "function " in region
