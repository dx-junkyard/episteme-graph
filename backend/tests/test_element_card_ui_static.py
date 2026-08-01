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

import re
from pathlib import Path

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
