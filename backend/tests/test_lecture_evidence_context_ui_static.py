"""原稿スタジオ「根拠リンク」ペイン改善（Phase 0 重複解消・双方向化 / Phase 1 上位下位
コンテキストの遅延ロード / Phase 2 論理要素単位の構造アウトライン / 統一パーツカードへの
収斂 = admin_ux_issues_2026-08-01.md §3.3 Phase 3）に対する静的ガードレール。

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
  ステイル応答破棄）する。source はフェッチしない。
- §3.3 Phase 3: 取得した文脈 DTO の描画は統一パーツカード（`element-card.js` /
  `window.ElementCard`）へ委譲する。独自のレーン HTML（旧 `ls-evidence-context-lane` 系）を
  復活させないこと。ITEM 行の操作（「この根拠リンク内で見る」/「深く検討」）は
  `itemActions`、カード自身の「深く検討」は `opts.onDeliberate`（解決済み element_id が
  取れたときだけ渡す fail-closed）で載せる。
- Phase 2: 論理要素（component）ごとのグループにカードを束ね（first-wins・「その他の根拠」
  への回収・component が無いトピックはフラット表示へフォールバック）、グループバーは
  カードヘッダとは別コントロールとして折りたたみだけを担当する。カードの
  `data-evidence-key` 等の DOM 契約は Phase 0/1 のまま維持する。
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
        # §3.3 Phase 3: レーン・裏付けバッジの CSS は統一パーツカード側が正本。
        # 原稿スタジオ側は外殻カードに馴染ませる調整だけを持つ。
        assert ".ls-evidence-context-card.element-card" in css
        assert ".ls-evidence-context-lane" not in css
        assert ".ls-context-status" not in css
        assert ".element-card-status-source_backed" in css
        assert ".element-card-status-candidate" in css


# ---------------------------------------------------------------------------
# Phase 1: 上位/下位コンテキストの遅延ロード
# ---------------------------------------------------------------------------


class TestContextLazyLoad:
    def test_kind_to_element_type_map_excludes_source(self):
        """source は文脈フェッチ対象にしない（kind='source' の根拠カードには evidence_id
        でない ID — トピック概要・原文抜粋 — も混ざるため。W層設計書 §16 で evidence が
        解決対象になっても、この forward 写像には足さない）。

        判定対象はオブジェクトリテラル本体のみ（コメント行は逆写像の説明で "source" に
        言及するため、`};` までで区切る）。
        """
        src = _read()
        block = _block(
            src,
            "var LS_EVIDENCE_CONTEXT_ELEMENT_TYPES = {",
            "};",
        )
        assert "component: \"theory_component\"" in block
        assert "claim: \"theory_claim\"" in block
        assert "equation: \"equation\"" in block
        assert "figure: \"figure\"" in block
        assert "source" not in block

    def test_reverse_map_defined_for_local_jump(self):
        src = _read()
        block = _block(
            src, "var LS_EVIDENCE_CONTEXT_KINDS = {", "function lsEvidenceContextElementType(kind) {"
        )
        assert "theory_component:" in block
        assert "theory_claim:" in block
        # §16: W層が返す evidence ITEM は同一トピックの出典カード（kind='source'、
        # target_id = evidence_id）と突合できる。
        assert "evidence: \"source\"," in block

    def test_relation_status_labels_are_japanese(self):
        """段階ラベルの正本は element-vocab.js（ElementVocab.statusLabel）で、描画は
        統一パーツカードが行う（admin_ux_issues_2026-08-01.md §3.3 Phase 0/3）。
        原稿スタジオ側は独自のラベル辞書・バッジ関数を一切持たない。"""
        src = _read()
        assert "LS_CONTEXT_STATUS_LABELS" not in src
        assert "function lsContextStatusLabel" not in src
        assert "function lsContextStatusBadgeHtml" not in src
        vocab = (ROOT / "frontend" / "public" / "js" / "element-vocab.js").read_text(
            encoding="utf-8"
        )
        for label in ("出典に裏付け", "教員確定", "AI候補"):
            assert label in vocab, label

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

    def test_context_is_rendered_by_the_unified_element_card(self):
        """§3.3 Phase 3: 取得した DTO はそのまま ElementCard へ渡す（カードは独自の
        取得をしない・呼び出し側は独自のレーン HTML を組まない）。"""
        src = _read()
        block = _block(
            src,
            "function lsRenderEvidenceContext(topic, item, container, data) {",
            "// 同一トピックの根拠リンクに対応アイテムがあるか。",
        )
        assert "window.ElementCard.mount(" in block
        assert "lsEvidenceContextCardOpts(topic, item, data.focus || {})" in block
        # カード未読み込みは事実文へ縮退する（独自 HTML の二重実装を持たない）
        assert "if (!window.ElementCard) {" in block
        assert "lsEvidenceContextFactHtml(" in block

    def test_bespoke_lane_renderers_are_gone(self):
        """独自レーン HTML（旧 ls-evidence-context-lane 系）を復活させないこと。"""
        src = _read()
        for name in (
            "function lsEvidenceContextLaneHtml",
            "function lsEvidenceContextItemHtml",
            "function lsBindEvidenceContextActions",
            "function lsRevealEvidenceDeliberateButton",
        ):
            assert name not in src, name
        # コメント（撤去の申し送り）は残るのでコード行だけを見る。
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )
        for marker in (
            "ls-evidence-context-lane",
            "ls-evidence-context-item",
            "ls-evidence-context-role",
            "ls-evidence-context-notes",
            "data-context-jump",
            "data-context-deliberate",
        ):
            assert marker not in code, marker

    def test_card_opts_keep_lane_titles_and_variant(self):
        src = _read()
        block = _block(
            src,
            "function lsEvidenceContextCardOpts(topic, item, focus) {",
            "function lsRenderEvidenceContext(",
        )
        assert "VARIANT_EDITABLE" in block
        assert "上位（この要素が支えるもの）" in block
        assert "下位（この要素を支えるもの）" in block
        assert "renderMath: lsRenderKatex" in block

    def test_local_match_uses_id_then_prefix_heuristic(self):
        src = _read()
        block = _block(
            src,
            "function lsEvidenceContextLocalMatch(topic, ctxItem) {",
            "// 右ペイン根拠カードの配線",
        )
        assert "lsEvidenceItemByRef(topic, kind, ctxItem.element_id)" in block
        assert "indexOf(label) === 0" in block

    def test_item_actions_prefer_pane_jump_then_deliberation(self):
        """ITEM 行の操作は itemActions に載る。ローカル対応があれば「この根拠リンク内で
        見る」、無ければ（かつ W層で開けるなら）「深く検討」。両者は排他。"""
        src = _read()
        block = _block(
            src,
            "function lsEvidenceContextCardOpts(topic, item, focus) {",
            "function lsRenderEvidenceContext(",
        )
        assert "itemActions:" in block
        assert "この根拠リンク内で見る" in block
        assert "lsFocusEvidence(match.key)" in block
        # 「深く検討」は対応が無い ITEM のみ（排他条件）
        assert "if (matchOf(ctxItem)) return false;" in block
        assert "ctxItem.navigable && ctxItem.element_id && window.Deliberation" in block
        assert 'anchor: "lecture-studio.component-deliberate"' in block

    def test_deliberation_open_uses_document_and_title_options(self):
        src = _read()
        block = _block(
            src,
            "function lsEvidenceContextCardOpts(topic, item, focus) {",
            "function lsRenderEvidenceContext(",
        )
        assert "window.Deliberation.openElement(" in block
        assert "documentId:" in block
        assert "title:" in block

    def test_card_deliberate_action_is_fail_closed_on_resolution(self):
        """カード自身の「深く検討」は解決済み element_id が取れたときだけ渡す。
        外殻カードに hidden ボタンを置く旧方式（lsRevealEvidenceDeliberateButton）は撤去。"""
        src = _read()
        html_block = _block(
            src, "function lsCourseEvidenceHtml(topic) {", "function lsFocusEvidence(key) {"
        )
        assert "data-evidence-deliberate=" not in html_block
        block = _block(
            src,
            "function lsEvidenceContextCardOpts(topic, item, focus) {",
            "function lsRenderEvidenceContext(",
        )
        assert "if (!elementType || !elementId) return opts;" in block
        assert "opts.onDeliberate = function () {" in block
        assert 'opts.deliberateAnchor = "lecture-studio.component-deliberate";' in block


# ---------------------------------------------------------------------------
# Phase 2: 論理要素単位の構造アウトライン（グループ化）
# ---------------------------------------------------------------------------


class TestEvidenceOutlineGrouping:
    def _group_block(self) -> str:
        src = _read()
        return _block(
            src,
            "function lsGroupCourseEvidenceItems(topic, items) {",
            "var LS_EVIDENCE_GROUP_KIND_ORDER",
        )

    def test_grouping_helper_exists_and_is_used_by_renderer(self):
        src = _read()
        assert "function lsGroupCourseEvidenceItems(topic, items) {" in src
        block = _block(src, "function lsCourseEvidenceHtml(topic) {", "function lsCourseEvidenceCardHtml(")
        assert "lsGroupCourseEvidenceItems(topic, items)" in block
        assert "lsEvidenceGroupHtml(topic, group)" in block

    def test_flat_fallback_when_no_component_items(self):
        """component アイテムが無いトピックはグループバーなしの従来表示に落ちる。"""
        group = self._group_block()
        assert 'if (item.kind === "component") componentItems.push(item);' in group
        assert "if (!componentItems.length) return null;" in group
        render = _block(
            _read(), "function lsCourseEvidenceHtml(topic) {", "function lsCourseEvidenceCardHtml("
        )
        # null（= component 無し）のときはフラットなカードリストを返す
        assert "if (!groups) {" in render
        assert "'<div class=\"ls-course-evidence-list\">'" in render

    def test_children_are_claims_and_equations_only(self):
        group = self._group_block()
        assert 'if (item.kind !== "claim" && item.kind !== "equation") return;' in group
        assert 'item.kind === "claim" ? refs.claims[norm] : refs.equations[norm]' in group

    def test_first_wins_prevents_duplicate_placement(self):
        """同一 claim/equation は最初の component グループにのみ置く（重複表示禁止）。"""
        group = self._group_block()
        assert "if (placed[item.key]) return;" in group
        assert "placed[item.key] = true;" in group

    def test_unassigned_items_go_to_others_group(self):
        group = self._group_block()
        assert "if (!placed[item.key]) others.push(item);" in group
        assert "groups.push({ component: null, children: others });" in group
        html = _block(
            _read(), "function lsEvidenceGroupHtml(topic, group) {", "function lsExpandEvidenceGroupFor("
        )
        assert '"その他の根拠"' in html

    def test_child_refs_read_rich_projection_then_fallback(self):
        """rich 投影（claims / equations[].id）を主経路に、旧データは components API へ。"""
        block = _block(
            _read(),
            "function lsComponentChildRefs(topic, componentId) {",
            "// 根拠アイテム列 → グループ列。",
        )
        assert "lsTopicComponentById(topic, componentId)" in block
        assert "(rich.claims || [])" in block
        assert "(rich.equations || [])" in block
        assert "lsCourseComponentById(componentId)" in block
        assert '"evidence_claims", "linked_claim_ids", "claim_ids"' in block

    def test_group_bar_is_a_separate_control_with_anchor(self):
        html = _block(
            _read(), "function lsEvidenceGroupHtml(topic, group) {", "function lsExpandEvidenceGroupFor("
        )
        assert 'class="ls-evidence-group-bar"' in html
        assert "data-evidence-group-toggle=" in html
        assert 'data-ui-anchor="lecture-studio.evidence-group"' in html
        assert 'aria-expanded="true"' in html  # 既定は展開
        # グループバーはカードヘッダ（文脈トグル）と責務を混ぜない
        assert "data-evidence-toggle=" not in html

    def test_group_bar_shows_kind_counts_only(self):
        src = _read()
        counts = _block(
            src,
            "function lsEvidenceGroupCountsLabel(children) {",
            "function lsEvidenceGroupHtml(",
        )
        assert "counts[item.kind]" in counts
        assert "lsEvidenceKindLabel(kind)" in counts
        html = _block(src, "function lsEvidenceGroupHtml(topic, group) {", "function lsExpandEvidenceGroupFor(")
        assert "紐づく主張・数式はありません" in html

    def test_cards_keep_evidence_key_contract_inside_groups(self):
        """グループの中身も Phase 0/1 と同じカード関数で描く（DOM 契約の維持）。"""
        src = _read()
        card = _block(src, "function lsCourseEvidenceCardHtml(topic, item) {", "// ── Phase 2:")
        assert re.search(
            r'<article class="ls-course-evidence-card" data-evidence-key="\'\s*\+\s*escHtml\(item\.key\)',
            card,
        )
        assert "data-evidence-toggle=" in card
        assert "data-evidence-context=" in card
        assert "data-evidence-draft=" in card
        html = _block(src, "function lsEvidenceGroupHtml(topic, group) {", "function lsExpandEvidenceGroupFor(")
        assert "lsCourseEvidenceCardHtml(topic, child)" in html
        assert "lsCourseEvidenceCardHtml(topic, group.component)" in html
        assert 'class="ls-evidence-group-children"' in html

    def test_focus_expands_collapsed_group_before_scrolling(self):
        src = _read()
        focus = _block(src, "function lsFocusEvidence(key) {", "// 右ペインの根拠カード → 左ドラフト")
        assert "lsExpandEvidenceGroupFor(target)" in focus
        idx_expand = focus.index("lsExpandEvidenceGroupFor(target)")
        idx_scroll = focus.index("scrollIntoView")
        assert idx_expand < idx_scroll
        expand = _block(src, "function lsExpandEvidenceGroupFor(target) {", "function lsFocusEvidence(key) {")
        assert 'closest(".ls-evidence-group")' in expand
        assert 'classList.contains("collapsed")' in expand
        assert 'setAttribute("aria-expanded", "true")' in expand
        assert "body.hidden = false" in expand

    def test_group_toggle_is_bound(self):
        block = _block(
            _read(),
            "function lsBindCourseEvidenceCards(topic, container) {",
            "\n  function lsRenderCourseListPreview(",
        )
        assert "[data-evidence-group-toggle]" in block
        assert 'classList.toggle("collapsed")' in block
        assert 'setAttribute("aria-expanded", collapsed ? "false" : "true")' in block
        assert "body.hidden = collapsed" in block

    def test_group_styles_defined(self):
        css = STYLES_CSS.read_text(encoding="utf-8")
        assert ".ls-evidence-group-bar" in css
        assert ".ls-evidence-group-children" in css
        assert ".ls-evidence-group.collapsed .ls-evidence-group-caret" in css

    def test_group_anchor_registered_with_carrier(self):
        """新アンカーが admin_ui_anchors の表と JS の担体の両方に存在すること。"""
        anchors_py = (
            ROOT / "backend" / "core" / "help_kb" / "admin_ui_anchors.py"
        ).read_text(encoding="utf-8")
        assert '"lecture-studio.evidence-group",' in anchors_py  # KNOWN 側
        assert (
            '"lecture-studio.evidence-group": "teacher/14-admin-lecture-studio.md#evidence-group"'
            in anchors_py
        )
        assert 'data-ui-anchor="lecture-studio.evidence-group"' in _read()
        manual = (
            ROOT / "docs" / "manual" / "teacher" / "14-admin-lecture-studio.md"
        ).read_text(encoding="utf-8")
        assert "{#evidence-group}" in manual


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
        """段階ラベル・バッジは統一パーツカードが element-vocab.js 経由で描き、
        マップに無い値は何も描かない（生の内部語彙・数値を出さない）。"""
        card = (ROOT / "frontend" / "public" / "js" / "element-card.js").read_text(
            encoding="utf-8"
        )
        assert "vocabStatusLabel" in card
        assert "confidence" not in "\n".join(
            line for line in card.splitlines() if not line.strip().startswith(("//", "*", "/*"))
        )


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
