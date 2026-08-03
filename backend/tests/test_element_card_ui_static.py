"""統一パーツカード（docs/architecture/admin_ux_issues_2026-08-01.md §3.2 / §3.3 Phase 1）
に対する静的ガードレール。

守るもの:
1. ES5（開発ルール5）で `window.ElementCard` を公開し、公開 API の形を保つこと。
2. 入力契約が context_lens の DTO 形に固定され、**カードが独自の取得をしない**こと
   （fetch / XMLHttpRequest / apiFetch を持たない）。
3. readonly バリアントで `relation_status === "candidate"` の近傍を描画しないこと
   （サーバ側 core/element_context.py の除去に対する二重ガード）。
4. confidence の生数値を出さないこと（W8）。カードは confidence を一切読まない。
5. admin.html / index.html の読み込み順（element-vocab.js より後・利用側より前）。
6. `data-ui-anchor` の**値**をハードコードしないこと（opts 経由のみ。§2.2 の
   双方向網羅テストの担体は呼び出し側画面が持つ）。
7. 種別語彙は element-vocab.js へ委譲し、独自の種別辞書を持たないこと（P1）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "frontend" / "public" / "js"
CARD_JS = JS_DIR / "element-card.js"
VOCAB_JS = JS_DIR / "element-vocab.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_lines(src: str) -> str:
    """行コメント・ブロックコメントを落としたコード部分だけを返す。"""
    without_block = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        line for line in without_block.splitlines() if not line.strip().startswith("//")
    )


# ---------------------------------------------------------------------------
# 1. ファイルの体裁と公開 API
# ---------------------------------------------------------------------------


class TestCardSourceFile:
    def test_file_exists(self):
        assert CARD_JS.exists()

    def test_is_es5(self):
        """開発ルール5: アロー関数・const/let・テンプレートリテラル禁止。"""
        src = _read(CARD_JS)
        assert "=>" not in src
        assert "`" not in src
        assert not re.search(r"(^|[^\w.$])const\s", src)
        assert not re.search(r"(^|[^\w.$])let\s", src)
        assert '"use strict";' in src

    def test_publishes_window_element_card(self):
        src = _read(CARD_JS)
        assert "global.ElementCard = {" in src
        for key in ("render: render", "bind: bind", "mount: mount"):
            assert key in src, key
        assert "VARIANT_EDITABLE: VARIANT_EDITABLE" in src
        assert "VARIANT_READONLY: VARIANT_READONLY" in src

    def test_render_returns_html_string_separately_from_bind(self):
        """render（HTML 文字列生成）と bind（イベント）が分離していること。
        呼び出し側が innerHTML 合成に混ぜられるための契約。"""
        src = _read(CARD_JS)
        assert "function render(dto, opts) {" in src
        assert "function bind(containerEl, dto, opts) {" in src
        assert "function mount(containerEl, dto, opts) {" in src
        # render は DOM を作らない（文字列連結のみ）。
        render_body = src[src.index("function render(dto, opts) {"):src.index("function rootElement(")]
        for forbidden in ("createElement", "innerHTML", "addEventListener"):
            assert forbidden not in render_body, forbidden

    def test_points_at_the_design_doc(self):
        src = _read(CARD_JS)
        assert "admin_ux_issues_2026-08-01.md" in src

    def test_declares_both_variants(self):
        src = _read(CARD_JS)
        assert 'var VARIANT_EDITABLE = "editable";' in src
        assert 'var VARIANT_READONLY = "readonly";' in src


# ---------------------------------------------------------------------------
# 2. カードは独自の取得をしない
# ---------------------------------------------------------------------------


class TestNoOwnFetch:
    def test_no_network_access(self):
        """入力 DTO は呼び出し側が渡す（§3.3 Phase 1「カードは独自の取得をしない」）。"""
        code = _code_lines(_read(CARD_JS))
        for forbidden in ("fetch(", "XMLHttpRequest", "apiFetch", "/api/"):
            assert forbidden not in code, forbidden

    def test_no_direct_katex_dependency(self):
        """数式レンダラは opts.renderMath で注入する（katex を直接触らない）。"""
        code = _code_lines(_read(CARD_JS))
        assert "katex" not in code
        assert "typeof opts.renderMath" in _read(CARD_JS)


# ---------------------------------------------------------------------------
# 3. readonly バリアントの二重ガード
# ---------------------------------------------------------------------------


class TestReadonlyGuards:
    def test_candidate_items_are_dropped_for_readonly(self):
        """readonly では relation_status === "candidate" の ITEM を描画しない。"""
        src = _read(CARD_JS)
        start = src.index("function visibleItems(dto, lane, ctx) {")
        body = src[start:src.index("\n  }\n", start)]
        assert "ctx.readonly" in body
        assert "STATUS_CANDIDATE" in body
        assert "continue;" in body
        assert 'var STATUS_CANDIDATE = "candidate";' in src

    def test_render_and_bind_share_the_visible_item_helper(self):
        """data 属性のインデックスとハンドラ引数がずれないよう、render と bind が
        同じ可視 ITEM 導出（visibleItems）を通ること。"""
        src = _read(CARD_JS)
        lane_start = src.index("function laneHtml(dto, lane, ctx) {")
        assert "visibleItems(dto, lane, ctx)" in src[lane_start:lane_start + 400]
        bind_start = src.index("function bind(containerEl, dto, opts) {")
        bind_body = src[bind_start:src.index("function mount(", bind_start)]
        assert "visibleItems(dto, LANE_UPPER, ctx)" in bind_body
        assert "visibleItems(dto, LANE_LOWER, ctx)" in bind_body

    def test_actions_and_review_notes_are_editable_only(self):
        """操作行・要確認事項は編集可バリアントのみ（§3.2 差分表）。"""
        src = _read(CARD_JS)
        for fn in ("function actionsHtml(ctx) {", "function reviewHtml(ctx) {"):
            start = src.index(fn)
            body = src[start:src.index("\n  }\n", start)]
            assert "if (!ctx.editable) return \"\";" in body, fn

    def test_variant_fails_closed_to_readonly(self):
        """variant 未指定・未知の値は readonly（候補も操作も出さない側）に倒す。"""
        src = _read(CARD_JS)
        start = src.index("function context(opts) {")
        body = src[start:src.index("\n  }\n", start)]
        assert "opts.variant === VARIANT_EDITABLE ? VARIANT_EDITABLE : VARIANT_READONLY" in body

    def test_candidate_role_is_hidden_for_readonly(self):
        """readonly では candidate な contextual_role も出さない（承認済みのみ）。"""
        src = _read(CARD_JS)
        start = src.index("function roleHtml(focus, ctx) {")
        body = src[start:src.index("\n  }\n", start)]
        assert "ctx.readonly" in body
        assert "STATUS_CANDIDATE" in body


# ---------------------------------------------------------------------------
# 4. 数値を見せない（W8）
# ---------------------------------------------------------------------------


class TestNoNumbersShown:
    def test_confidence_is_never_read(self):
        """confidence フィールドへのアクセス自体を持たない（コメント言及のみ可）。"""
        code = _code_lines(_read(CARD_JS))
        assert not re.search(r"\.confidence\b", code)
        assert '"confidence"' not in code

    def test_status_labels_come_from_the_vocabulary(self):
        """段階ラベルは element-vocab.js の statusLabel（正本）から取る。"""
        src = _read(CARD_JS)
        assert "vocab.statusLabel" in src
        # カード内にラベル文字列の独自辞書を作らない。
        for stale in ("出典に裏付け", "教員確定", "AI候補"):
            assert stale not in src, stale

    def test_provenance_id_list_is_not_rendered(self):
        """focus.provenance は内部 ID 列（theory_claims:<uuid> 等）なので描かない。"""
        code = _code_lines(_read(CARD_JS))
        assert "focus.provenance" not in code
        assert "provenance" not in code


# ---------------------------------------------------------------------------
# 5. 読み込み順
# ---------------------------------------------------------------------------


class TestScriptLoadOrder:
    def _positions(self, html: str, names: list[str]) -> dict:
        out = {}
        for name in names:
            m = re.search(r'<script src="/js/' + re.escape(name) + r'(\?[^"]*)?"', html)
            assert m, "script タグが無い: " + name
            out[name] = m.start()
        return out

    def test_admin_html_order(self):
        html = _read(ADMIN_HTML)
        pos = self._positions(
            html,
            [
                "element-vocab.js",
                "element-card.js",
                "deliberation.js",
                "admin-lecture-studio.js",
                "admin.js",
            ],
        )
        assert pos["element-vocab.js"] < pos["element-card.js"]
        for consumer in ("deliberation.js", "admin-lecture-studio.js", "admin.js"):
            assert pos["element-card.js"] < pos[consumer], consumer

    def test_index_html_order(self):
        html = _read(INDEX_HTML)
        pos = self._positions(html, ["element-vocab.js", "element-card.js", "app.js"])
        assert pos["element-vocab.js"] < pos["element-card.js"] < pos["app.js"]


# ---------------------------------------------------------------------------
# 6. anchor をハードコードしない
# ---------------------------------------------------------------------------


class TestNoHardcodedAnchors:
    def test_no_literal_anchor_values(self):
        """data-ui-anchor の**値**を直書きしない（opts.deliberateAnchor /
        extraActions[].anchor 経由のみ）。属性名の出力自体は許す。"""
        src = _read(CARD_JS)
        hits = re.findall(r'data-ui-anchor="[^"\'+]', src)
        assert hits == [], hits

    def test_anchor_comes_from_opts(self):
        src = _read(CARD_JS)
        assert "ctx.deliberateAnchor" in src
        assert "textOf(action.anchor)" in src

    def test_anchor_ids_of_known_screens_are_absent(self):
        """既存 admin アンカーID（materials. / lecture-studio. 接頭）を含まない。"""
        src = _read(CARD_JS)
        assert "lecture-studio." not in src
        assert "materials." not in src


# ---------------------------------------------------------------------------
# 7. 語彙の委譲（P1）
# ---------------------------------------------------------------------------


class TestVocabularyDelegation:
    def test_delegates_element_type_labels(self):
        src = _read(CARD_JS)
        assert "global.ElementVocab" in src
        assert "vocab.elementTypeLabel" in src

    def test_no_own_type_dictionary(self):
        """種別 → 日本語表示名のインライン辞書を持たない。"""
        pattern = re.compile(
            r"(theory_component|theory_claim|shared_part|derivation|figure|equation)\s*:\s*\"([^\"]*)\""
        )
        hits = [
            key + ': "' + value + '"'
            for key, value in pattern.findall(_read(CARD_JS))
            if not value.isascii()
        ]
        assert hits == [], hits

    def test_vocab_source_exposes_status_label(self):
        src = _read(VOCAB_JS)
        assert "statusLabel: statusLabel" in src


# ---------------------------------------------------------------------------
# 8. CSS
# ---------------------------------------------------------------------------


class TestCardStyles:
    def test_core_classes_exist(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".element-card {",
            ".element-card-head {",
            ".element-card-kind {",
            ".element-card-meta {",
            ".element-card-body {",
            ".element-card-lanes {",
            ".element-card-item {",
            ".element-card-actions {",
            ".element-card-status {",
        ):
            assert selector in css, selector

    def test_uses_theme_variables_only(self):
        """新規カード CSS は既存の CSS 変数だけで色を書く（ダーク/ライト追従）。"""
        css = _read(STYLES_CSS)
        start = css.index("/* ── 統一パーツカード（element-card.js")
        block = css[start:]
        hex_colors = re.findall(r":\s*#[0-9a-fA-F]{3,8}", block)
        assert hex_colors == [], hex_colors


class TestFollowUpOptions:
    """後続候補3件（admin_ux_issues_2026-08-01.md §0 末尾）: hideHead/hideBody・
    ITEM の evidence_refs 折りたたみ・roleLabel 上書き。"""

    def test_hide_head_and_hide_body_options(self):
        """外殻カードが種別チップ・タイトル・要約を既に表示している画面
        （原稿スタジオ根拠リンク）向けの重複回避オプション。"""
        src = _read(CARD_JS)
        assert "hideHead: !!opts.hideHead" in src
        assert "hideBody: !!opts.hideBody" in src
        assert '(ctx.hideHead ? "" : headHtml(focus, ctx))' in src
        assert '(ctx.hideBody ? "" : bodyHtml(focus, ctx))' in src

    def test_hide_intrinsic_is_independent_of_hide_body(self):
        """「これは何か」（focus.intrinsic / placement）は hideBody とは別のスイッチ。
        外殻が旧 intrinsic_summary を出しているだけの画面（根拠リンク外殻）でも
        構造化ブロックは展開部に出したいため（設計書 §6 S3）。"""
        src = _read(CARD_JS)
        assert "hideIntrinsic: !!opts.hideIntrinsic" in src
        assert '(ctx.hideIntrinsic ? "" : intrinsicHtml(focus, ctx))' in src

    def test_role_label_override(self):
        """document スコープの画面が「この論文での役割」等の正確な主語を使えること。
        既定文言（この文脈での役割）は不変。"""
        src = _read(CARD_JS)
        assert "roleLabel: textOf(opts.roleLabel)" in src
        assert "ctx.roleLabel || ROLE_LABEL" in src
        assert 'var ROLE_LABEL = "この文脈での役割"' in src

    def test_item_evidence_refs_rendered_editable_only(self):
        """ITEM の根拠参照（内部 ID 列）は editable のみ・折りたたみで描く。
        readonly（学習者）には出さない。"""
        src = _read(CARD_JS)
        start = src.index("function itemRefsHtml")
        block = src[start:src.index("\n  }", start)]
        assert 'if (!ctx.editable) return "";' in block
        assert "evidence_refs" in block
        assert "element-card-item-refs" in block
        assert "<details" in block

    def test_evidence_pane_uses_the_options(self):
        """原稿スタジオ根拠リンクの呼び出し側が3オプションを実際に使っていること。"""
        ls = _read(JS_DIR / "admin-lecture-studio.js")
        assert "hideHead: true" in ls
        assert "hideBody: true" in ls
        assert 'roleLabel: "この論文での役割"' in ls


# ---------------------------------------------------------------------------
# 9. 要素文脈の提示再設計（element_context_presentation_redesign.md Phase 1）
# ---------------------------------------------------------------------------


class TestZoneRendering:
    """DTO v2: ITEM が `group` を持つとき4区画ゾーンで描く（§2.2 / §4.3 / §6）。
    group が無い旧 DTO は従来の upper/lower 2レーンのまま（後方互換）。"""

    def test_zone_order_is_the_four_sections(self):
        src = _read(CARD_JS)
        assert 'var ZONE_ORDER = ["positioning", "composition", "forward", "related"];' in src

    def test_zone_and_group_headings_come_from_the_vocabulary(self):
        """区画・小見出しの文言は element-vocab.js が正本（独自辞書を持たない）。"""
        src = _read(CARD_JS)
        code = _code_lines(src)
        assert 'vocabCall("zoneForGroup", group)' in src
        assert 'vocabCall("zoneHeading", zone)' in src
        assert 'vocabCall("groupHeading", group)' in src
        for stale in ("この論文での位置づけ", "この要素を組み立てているもの", "定義する記号"):
            assert stale not in code, stale

    def test_falls_back_to_legacy_lanes_without_groups(self):
        src = _read(CARD_JS)
        block = src[src.index("function lanesHtml(dto, ctx) {"):]
        block = block[: block.index("\n  }")]
        assert "hasGroupedItems(entries) || derivations.length" in block
        assert "return legacyLanesHtml(dto, ctx);" in block
        assert "function legacyLanesHtml(dto, ctx) {" in src

    def test_empty_zone_is_not_drawn(self):
        src = _read(CARD_JS)
        start = src.index("function zoneHtml(zone, bucket, ctx, leadHtml) {")
        block = src[start : src.index("\n  }", start)]
        assert 'if (!inner) return ""' in block

    def test_group_display_limit_with_a_more_details(self):
        """group ごと5件まで + 「ほかN件」の折りたたみ（情報は落とさない）。"""
        src = _read(CARD_JS)
        assert "var GROUP_ITEM_LIMIT = 5;" in src
        start = src.index("function groupHtml(group, entries, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert "entries.slice(0, GROUP_ITEM_LIMIT)" in block
        assert "entries.slice(GROUP_ITEM_LIMIT)" in block
        assert "element-card-group-more" in block

    def test_operation_group_is_editable_only_and_collapsed(self):
        """式の詳細層（traceability）は教員のみ・既定折りたたみ（CP3）。
        readonly では visibleItems の段階で落とす（fail-closed の二重ガード）。"""
        src = _read(CARD_JS)
        start = src.index("function operationGroupHtml(entries, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert 'if (!ctx.editable || !entries.length) return "";' in block
        assert "<details" in block
        visible = src[src.index("function visibleItems(dto, lane, ctx) {") :]
        visible = visible[: visible.index("\n  }")]
        assert "ctx.readonly && textOf(item.group) === GROUP_OPERATION" in visible

    def test_every_item_row_carries_a_lookup_key(self):
        """ゾーン描画では ITEM が元のレーン順に並ばないため、呼び出し側（教材内
        ジャンプ）が行→ITEM を引けるよう data-element-card-itemref を全行に付ける。"""
        src = _read(CARD_JS)
        start = src.index("function itemHtml(item, lane, index, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert 'data-element-card-itemref="' in block
        assert "refAttr" in block


class TestItemSublabelAndQualifier:
    def test_sublabel_is_rendered_for_both_variants(self):
        """sublabel は「区別材料」なので readonly（学習者）でも描く。
        editable 限定なのは evidence_refs / reviewNotes だけ（§6 の差分表）。"""
        src = _read(CARD_JS)
        start = src.index("function itemInnerHtml(item, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert "textOf(item.sublabel)" in block
        assert "element-card-item-sub" in block
        assert "ctx.editable" not in block, "sublabel を編集権限で出し分けない"

    def test_qualifier_chip_uses_the_vocabulary_and_is_skipped_when_unknown(self):
        src = _read(CARD_JS)
        start = src.index("function itemInnerHtml(item, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert "vocabQualifierLabel(item.element_type, item.qualifier)" in block
        assert "element-card-item-qualifier" in block
        # 未知キーは ElementVocab が "" を返す → チップを出さない（三項の false 側）。
        assert 'qualifier\n        ? ' in block or "(qualifier" in block

    def test_label_source_is_never_rendered(self):
        """label_source（来歴）は教員向けの内部情報。カードは読まない。"""
        code = _code_lines(_read(CARD_JS))
        assert "label_source" not in code


class TestMathGateIsInternal:
    """設計書 §6 S3-5: TeX 描画ガードを element-card.js 内部に置き、
    admin / W層 / 学習の3通りの壊れ方を1実装で終わらせる。"""

    def test_gate_function_exists(self):
        src = _read(CARD_JS)
        assert "function looksLikeRenderableTex(text, symbolMode) {" in src
        assert "opens === closes" in src

    def test_render_math_is_only_called_through_the_gate(self):
        """ctx.renderMath の呼び出し箇所は renderMathGated の1つだけ。"""
        src = _read(CARD_JS)
        assert src.count("ctx.renderMath(") == 1
        start = src.index("function renderMathGated(ctx, expr, display, symbolMode) {")
        block = src[start : src.index("\n  }", start)]
        assert "looksLikeRenderableTex(expr, symbolMode)" in block
        assert "try {" in block  # レンダラの例外で画面を壊さない

    def test_symbol_labels_go_through_the_gate_then_fall_back_to_plain_text(self):
        src = _read(CARD_JS)
        assert "renderMathGated(ctx, label, false, true) || ctx.esc(label)" not in src
        start = src.index("function itemInnerHtml(item, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert "ELEMENT_TYPE_SYMBOL" in block
        assert "renderMathGated(ctx, label, false, true)" in block
        assert "if (!labelHtml) labelHtml = ctx.esc(label);" in block


class TestIntrinsicBlock:
    """①これは何か（focus.intrinsic）+ ②位置づけ（focus.placement）。"""

    def test_rows_are_omitted_when_the_value_is_missing(self):
        src = _read(CARD_JS)
        start = src.index("function intrinsicHtml(focus, ctx) {")
        block = src[start : src.index("\n  }", start)]
        for guard in (
            "if (role) rows.push(",
            "if (reading) rows.push(",
            "if (section) rows.push(",
        ):
            assert guard in block, guard
        assert 'if (!rows.length && !factsHtml) return "";' in block

    def test_source_language_note_is_shown_for_free_text(self):
        """英語自由文は訳さず原文のまま完全表示し、出所だけ注記する（§2.4 / Q3）。"""
        src = _read(CARD_JS)
        assert 'var SOURCE_LANGUAGE_NOTE = "（論文の原文）";' in src
        assert "intrinsic.summary_is_source_language" in src

    def test_body_is_not_duplicated_with_the_intrinsic_summary(self):
        """CP1: focus.label / intrinsic.summary / intrinsic_summary の同一文字列を
        二度描かない。"""
        src = _read(CARD_JS)
        start = src.index("function bodyHtml(focus, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert "textOf(focus.intrinsic.summary) === summary" in block
        head = src[src.index("function headHtml(focus, ctx) {") :]
        head = head[: head.index("\n  }")]
        assert "textOf(focus.headline) || textOf(focus.label)" in head
        assert "title === textOf(focus.intrinsic.summary)" in head

    def test_confidence_is_not_read_from_the_new_blocks(self):
        code = _code_lines(_read(CARD_JS))
        assert "confidence" not in code


class TestDerivationStoryCards:
    """§4.4 / §7: 「入力 →[操作]→ 出力 + この式の役割」のストーリーカード。"""

    def test_story_card_parts(self):
        src = _read(CARD_JS)
        for fn in (
            "function derivationHeadHtml(d, ctx) {",
            "function derivationFlowHtml(d, ctx) {",
            "function derivationMetaHtml(d, ctx) {",
            "function derivationIdentifiersHtml(d, ctx) {",
            "function derivationActionsHtml(d, index, ctx) {",
        ):
            assert fn in src, fn
        assert 'var DERIVATION_ROLE_LABEL = "この導出でのこの要素の役割";' in src
        assert 'var DERIVATION_ELIMINATED_LABEL = "消える記号";' in src
        assert 'var DERIVATION_RETAINED_LABEL = "残る記号";' in src

    def test_direction_is_not_asserted_when_unknown(self):
        """CP2/CP10: 向きが同定できない側は「（同定されていません）」の事実文。"""
        src = _read(CARD_JS)
        assert 'var DERIVATION_UNKNOWN = "（同定されていません）";' in src
        start = src.index("function derivationFlowHtml(d, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert "if (!left) left = DERIVATION_UNKNOWN;" in block
        assert "if (!right) right = DERIVATION_UNKNOWN;" in block

    def test_no_duplicate_rows_when_stories_are_drawn(self):
        """ストーリーカードを描くときは derivation_in / derivation_out の ITEM 行を
        重複描画しない。"""
        src = _read(CARD_JS)
        start = src.index("function zoneBuckets(entries, skipDerivationGroups) {")
        block = src[start : src.index("\n  }", start)]
        assert "group === GROUP_DERIVATION_IN || group === GROUP_DERIVATION_OUT" in block
        zones = src[src.index("function zonesHtml(ctx, entries, derivations) {") :]
        zones = zones[: zones.index("\n  }")]
        assert "zoneBuckets(entries, !!stories)" in zones

    def test_identifiers_are_editable_only(self):
        """内部 ID（derivation_id / evidence_refs）は教員の折りたたみ内のみ（EC3′）。"""
        src = _read(CARD_JS)
        start = src.index("function derivationIdentifiersHtml(d, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert 'if (!ctx.editable) return "";' in block
        assert "d.derivation_id" in block
        assert "<details" in block

    def test_candidate_derivations_are_hidden_for_readonly(self):
        src = _read(CARD_JS)
        start = src.index("function derivationsOf(dto, ctx) {")
        block = src[start : src.index("\n  }", start)]
        assert "ctx.readonly && textOf(d.relation_status) === STATUS_CANDIDATE" in block
        # focus 配下 / トップレベルのどちらで来ても受ける。
        assert "asArray(dto.derivations)" in block
        assert "asArray(focusOf(dto).derivations)" in block

    def test_actions_are_bound_through_the_same_derivation_list(self):
        src = _read(CARD_JS)
        bind = src[src.index("function bind(containerEl, dto, opts) {") :]
        bind = bind[: bind.index("function mount(")]
        assert "derivationsOf(dto, ctx)" in bind
        assert "[data-element-card-derivation]" in bind
        assert "derivationRef(derivation)" in bind


class TestNewCardStyles:
    def test_new_classes_exist(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".element-card-intrinsic {",
            ".element-card-def-row {",
            ".element-card-zone {",
            ".element-card-zone-title {",
            ".element-card-group-title {",
            ".element-card-item-sub {",
            ".element-card-item-qualifier {",
            ".element-card-derivation {",
            ".element-card-derivation-flow {",
        ):
            assert selector in css, selector


class TestZoneVocabulary:
    """フロント専用の表示写像（zoneForGroup / zoneHeading / groupHeading /
    qualifierLabel）を固定する。訳語の Python ⇄ JS パリティは
    test_element_vocab_mirror.py（MIRRORED_TABLES）が別途守る — ここはサーバ正本を
    持たない**描画側の区画写像**だけを見る。"""

    _ZONES = {
        "stage": "positioning",
        "thesis": "positioning",
        "claim": "positioning",
        "section": "positioning",
        "symbol_defined": "composition",
        "symbol_used": "composition",
        "equation_up": "composition",
        "derivation_in": "composition",
        "claim_required": "composition",
        "evidence": "composition",
        "derivation_out": "forward",
        "equation_down": "forward",
        "operation": "forward",
    }

    def _table(self, name: str) -> dict:
        src = _read(VOCAB_JS)
        marker = "var " + name + " = {"
        assert marker in src, name
        start = src.index(marker)
        block = src[start + len(marker): src.index("\n  };", start)]
        out = {}
        for quoted, bare, value in re.findall(
            r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"", block
        ):
            out[quoted or bare] = value
        return out

    def _zone_table(self) -> dict:
        src = _read(VOCAB_JS)
        start = src.index("var GROUP_ZONES = {")
        block = src[start: src.index("\n  };", start)]
        consts = {
            "ZONE_POSITIONING": "positioning",
            "ZONE_COMPOSITION": "composition",
            "ZONE_FORWARD": "forward",
            "ZONE_RELATED": "related",
        }
        out = {}
        for key, const in re.findall(r"([a-z_]+)\s*:\s*(ZONE_[A-Z_]+)", block):
            out[key] = consts[const]
        return out

    def test_group_to_zone_mapping_is_fixed(self):
        assert self._zone_table() == self._ZONES

    def test_zone_headings_are_fixed(self):
        assert self._table("ZONE_HEADINGS") == {
            "positioning": "この論文での位置づけ",
            "composition": "この要素を組み立てているもの",
            "forward": "この要素が支えているもの／ここから先",
            "related": "関連",
        }

    def test_group_headings_are_fixed(self):
        assert self._table("GROUP_HEADINGS") == {
            "symbol_defined": "定義する記号",
            "symbol_used": "使う記号",
            "equation_up": "もとになる式",
            "equation_down": "次の式",
            "derivation_in": "この要素を導く導出",
            "derivation_out": "導出",
            "claim_required": "前提となる主張",
            "evidence": "本文の根拠",
            "stage": "理論の段階",
            "section": "掲載",
            "thesis": "中心命題",
            "claim": "支える主張",
            "operation": "式の詳細層",
        }

    def test_unknown_group_falls_back_to_related(self):
        src = _read(VOCAB_JS)
        start = src.index("function zoneForGroup(group) {")
        block = src[start: src.index("\n  }", start)]
        assert "return ZONE_RELATED;" in block

    def test_qualifier_label_delegates_per_element_type(self):
        """qualifier の訳は既存の統制語彙表へ委譲する（新しい訳語表を作らない）。"""
        src = _read(VOCAB_JS)
        start = src.index("function qualifierLabel(elementType, key) {")
        block = src[start: src.index("\n  }", start)]
        assert 'if (type === "derivation") return chainTypeLabel(raw);' in block
        assert 'if (type === "stage") return theoryStageLabel(raw);' in block
        assert 'if (type === "symbol") return definitionStatusLabel(raw);' in block
        assert 'if (type === "equation") return equationRoleLabel(raw);' in block
        assert "QUALIFIER_EQUATION_DETAIL_LABEL" in block
        assert 'return "";' in block  # 未知種別は fail-closed

    def test_new_functions_are_published(self):
        src = _read(VOCAB_JS)
        published = src[src.index("global.ElementVocab = {"):]
        for name in ("zoneForGroup", "zoneHeading", "groupHeading", "qualifierLabel"):
            assert name + ": " + name in published, name


# ---------------------------------------------------------------------------
# 10. 実行時の振る舞い（node が使える環境のみ）
# ---------------------------------------------------------------------------

_CARD_HARNESS = r"""
const fs = require("fs");
const path = require("path");
var window = {};
const dir = process.argv[2];
eval(fs.readFileSync(path.join(dir, "element-vocab.js"), "utf8"));
eval(fs.readFileSync(path.join(dir, "element-card.js"), "utf8"));
const Card = window.ElementCard;
function esc(s){ return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function fakeMath(expr, display){ return '<span class="fake-katex">'+esc(expr)+'</span>'; }

const v2 = {
  focus: {
    element_type: "equation", element_id: "eq_a", label: "delta を定義する式",
    headline: "delta を定義する式",
    intrinsic: {role_key: "definition", summary: "Definition of the density contrast",
      summary_is_source_language: true,
      symbols: [{symbol: "\\delta(t,x)", meaning: "密度コントラスト", defined_here: true}],
      conditions: ["rho > 0"], facts: ["この式は定義であり、前段の式を持ちません。"]},
    placement: {section_label: "2.1 Density contrast",
      stage: {key: "theory_basis", description: "観測量を密度ゆらぎとして定義する"}},
    derivations: [{derivation_id: "derivation_1", label: "フーリエ変換による書き換え",
      chain_type: "equation_chain", operation_text: "フーリエ変換", focus_role: "input",
      inputs: [], outputs: [{element_type:"equation", element_id:"eq_b", label:"次の式", navigable:true}],
      eliminated_symbols: [], retained_symbols: ["delta"], relation_status: "source_backed",
      evidence_refs: ["ev_1"], navigable: true}]
  },
  upper: [
    {element_id: null, element_type: "stage", label: "理論の土台", sublabel: "観測量を密度ゆらぎとして定義する",
     qualifier: "theory_basis", group: "stage", relation_label: "の理論段階に属する",
     relation_status: "source_backed", navigable: false},
    {element_id: "d1", element_type: "derivation", label: "フーリエ変換による書き換え",
     group: "derivation_out", relation_label: "の導出に入力として使われる",
     relation_status: "source_backed", navigable: true},
    {element_id: "op1", element_type: "theory_component", label: "Define eq_b",
     qualifier: "equation_detail", group: "operation", relation_label: "の操作ステップで使われる",
     relation_status: "partially_source_backed", navigable: false}
  ],
  lower: [
    {element_id: "sym1", element_type: "symbol", label: "\\delta(t,x)", sublabel: "密度コントラスト",
     qualifier: "defined", group: "symbol_defined", relation_label: "で記号を定義する",
     relation_status: "source_backed", navigable: false, evidence_refs: ["ev_9"]},
    {element_id: "ev1", element_type: "evidence", label: "「We define …」", sublabel: "2.1 節",
     group: "evidence", relation_label: "を根拠にする", relation_status: "source_backed",
     navigable: false, evidence_refs: ["ev_1"]}
  ],
  notes: ["debug 層のノード 3 件は表示していません。"]
};

const legacy = {
  focus: {element_type: "theory_claim", label: "焦点の主張", intrinsic_summary: "本文"},
  upper: [{id: "c1", element_type: "theory_claim", label: "上位主張", relation_label: "を支持する",
           relation_status: "source_backed", navigable: true}],
  lower: []
};

const editable = Card.render(v2, {variant:"editable", escapeHtml: esc, renderMath: fakeMath,
  onCenter: function(){}, onDeliberate: function(){}});
const readonly = Card.render(v2, {variant:"readonly", escapeHtml: esc, renderMath: fakeMath,
  onCenter: function(){}});
const legacyHtml = Card.render(legacy, {variant:"readonly", escapeHtml: esc});

function zoneOrder(html){
  const out = [];
  const re = /element-card-zone element-card-zone-([a-z]+)/g;
  let m; while ((m = re.exec(html))) out.push(m[1]);
  return out;
}

process.stdout.write(JSON.stringify({
  zoneOrder: zoneOrder(editable),
  zoneHeadings: editable.indexOf("この論文での位置づけ") >= 0 &&
                editable.indexOf("この要素を組み立てているもの") >= 0,
  groupHeadings: editable.indexOf("定義する記号") >= 0 && editable.indexOf("本文の根拠") >= 0,
  intrinsicRendered: editable.indexOf("Definition of the density contrast") >= 0 &&
                     editable.indexOf("（論文の原文）") >= 0 &&
                     editable.indexOf("（この式で定義）") >= 0 &&
                     editable.indexOf("この式は定義であり、前段の式を持ちません。") >= 0,
  stageDescription: editable.indexOf("観測量を密度ゆらぎとして定義する") >= 0,
  readonlySublabel: readonly.indexOf("element-card-item-sub") >= 0 &&
                    readonly.indexOf("密度コントラスト") >= 0,
  readonlyNoEvidenceRefs: readonly.indexOf("element-card-item-refs") < 0 &&
                          readonly.indexOf("ev_9") < 0 && readonly.indexOf("ev_1") < 0,
  readonlyNoOperationLayer: readonly.indexOf("式の詳細層") < 0 &&
                            readonly.indexOf("Define eq_b") < 0,
  editableOperationLayerCollapsed: editable.indexOf("式の詳細層（traceability・1件）") >= 0,
  derivationStory: editable.indexOf("element-card-derivation-flow") >= 0 &&
                   editable.indexOf("この要素 ─[フーリエ変換]→ 次の式") >= 0,
  derivationNotDuplicated:
    (editable.match(/フーリエ変換による書き換え/g) || []).length === 1,
  derivationIdentifiersEditableOnly:
    editable.indexOf("derivation_1") >= 0 && readonly.indexOf("derivation_1") < 0,
  symbolRenderedThroughGate: editable.indexOf('fake-katex">\\delta(t,x)') >= 0,
  noRawStatusKeys: editable.indexOf(">source_backed<") < 0,
  legacyFallsBackToLanes: legacyHtml.indexOf("element-card-lane-upper") >= 0 &&
                          legacyHtml.indexOf("element-card-zone") < 0,
  everyRowHasLookupKey:
    (editable.match(/data-element-card-itemref=/g) || []).length,
  itemrefLanes: (editable.match(/data-element-card-itemref="(upper|lower):\d+"/g) || []).join(",")
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_zone_rendering_behaviour_node(tmp_path):
    """DTO v2 の実描画。ゾーン順序・group 小見出し・sublabel・導出ストーリー・
    式の詳細層の editable 限定・旧 DTO の 2レーン縮退をまとめて確認する。"""
    harness = tmp_path / "card.js"
    harness.write_text(_CARD_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(harness), str(JS_DIR)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["zoneOrder"] == ["positioning", "composition", "forward"], out["zoneOrder"]
    assert out["zoneHeadings"]
    assert out["groupHeadings"]
    assert out["intrinsicRendered"]
    assert out["stageDescription"], "node.description を落とさない（RC4）"
    assert out["readonlySublabel"], "readonly でも sublabel は描く"
    assert out["readonlyNoEvidenceRefs"], "evidence_refs は editable のみ"
    assert out["readonlyNoOperationLayer"], "式の詳細層は学習者に出さない（CP3）"
    assert out["editableOperationLayerCollapsed"]
    assert out["derivationStory"]
    assert out["derivationNotDuplicated"], "ストーリー表示時に ITEM 行を重複させない"
    assert out["derivationIdentifiersEditableOnly"], "内部 ID は教員の折りたたみ内のみ"
    assert out["symbolRenderedThroughGate"]
    assert out["noRawStatusKeys"]
    assert out["legacyFallsBackToLanes"], "group が無い旧 DTO は従来のレーン描画"
    # stage / operation / symbol_defined / evidence の4行（derivation_out の ITEM は
    # ストーリーカードに置き換わるので描かれない）。添字は元レーンのまま残る。
    assert out["everyRowHasLookupKey"] == 4, out["everyRowHasLookupKey"]
    assert out["itemrefLanes"] == (
        'data-element-card-itemref="upper:0",'
        'data-element-card-itemref="lower:0",'
        'data-element-card-itemref="lower:1",'
        'data-element-card-itemref="upper:2"'
    ), out["itemrefLanes"]
