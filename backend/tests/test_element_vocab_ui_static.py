"""要素種別語彙の1本化（docs/architecture/admin_ux_issues_2026-08-01.md §3.3 Phase 0）
に対する静的ガードレール。

背景（§3.1(1)）: 要素種別の表示名がフロント3ファイル・6辞書に分裂し、同じ
theory_component が「論理要素」/「コンポーネント」、同じ theory_claim が「主張」/「claim」、
figure が「図」/「図・画像」と画面によって別名になっていた（P1「種別語彙は1つ」の違反）。

このテストが守るもの:
1. 正本 frontend/public/js/element-vocab.js が存在し ES5（開発ルール5）であること。
2. 正本が canonical 訳語（論理要素 / 主張 / 数式 / 図 / 出典 / 根拠箇所 / 導出 / 共通部品）を
   持ち、未知キーは fail-soft でキー文字列を返すこと。
3. admin.html / index.html で、正本を参照する全スクリプトより**前**に読み込まれること。
4. 参照側3ファイル（admin-lecture-studio.js / app.js / deliberation.js）が独自の種別辞書を
   再定義していないこと（旧辞書リテラルの再出現禁止）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "frontend" / "public" / "js"
VOCAB_JS = JS_DIR / "element-vocab.js"
ADMIN_LS_JS = JS_DIR / "admin-lecture-studio.js"
APP_JS = JS_DIR / "app.js"
DELIBERATION_JS = JS_DIR / "deliberation.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"

# 参照側（正本へ委譲する側）。admin.js は種別辞書を持たないが、将来の再発を防ぐため
# 併せて走査する。
CONSUMER_JS = [ADMIN_LS_JS, APP_JS, DELIBERATION_JS, JS_DIR / "admin.js"]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. 正本の存在と ES5 準拠
# ---------------------------------------------------------------------------


class TestVocabSourceFile:
    def test_file_exists(self):
        assert VOCAB_JS.exists()

    def test_publishes_window_element_vocab(self):
        src = _read(VOCAB_JS)
        assert "global.ElementVocab = {" in src
        assert "kindLabel: kindLabel" in src
        assert "elementTypeLabel: elementTypeLabel" in src
        assert "statusLabel: statusLabel" in src

    def test_is_es5(self):
        """開発ルール5: アロー関数・const/let・テンプレートリテラル禁止。"""
        src = _read(VOCAB_JS)
        assert "=>" not in src
        assert "`" not in src
        assert not re.search(r"(^|[^\w.$])const\s", src)
        assert not re.search(r"(^|[^\w.$])let\s", src)
        assert '"use strict";' in src

    def test_has_no_dom_or_network_dependency(self):
        """語彙表なので DOM / fetch には触らない（どのページからも読める）。"""
        src = _read(VOCAB_JS)
        for forbidden in ("document.", "fetch(", "XMLHttpRequest", "localStorage"):
            assert forbidden not in src

    def test_points_at_the_design_doc(self):
        src = _read(VOCAB_JS)
        assert "admin_ux_issues_2026-08-01.md" in src
        assert "独自辞書" in src


# ---------------------------------------------------------------------------
# 2. canonical 訳語
# ---------------------------------------------------------------------------


class TestCanonicalLabels:
    def _map(self, name: str) -> dict:
        src = _read(VOCAB_JS)
        start = src.index("var " + name + " = {")
        end = src.index("};", start)
        block = src[start:end]
        return dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block))

    def test_kind_labels(self):
        """根拠リンク系 kind の canonical 表示名（§3.1(1) / §3.4(a)）。"""
        assert self._map("KIND_LABELS") == {
            "component": "論理要素",
            "claim": "主張",
            "equation": "数式",
            "figure": "図",
            "source": "出典",
            "evidence": "根拠箇所",
            "derivation": "導出",
            "shared_part": "共通部品",
        }

    def test_element_type_labels(self):
        """backend element_type / context_lens ITEM 語彙の canonical 表示名。"""
        assert self._map("ELEMENT_TYPE_LABELS") == {
            "theory_component": "論理要素",
            "theory_claim": "主張",
            "equation": "数式",
            "figure": "図",
            "shared_part": "共通部品",
            "evidence": "根拠箇所",
            "derivation": "導出",
            "section": "節",
            "thesis": "中心命題",
            "symbol": "記号",
            "stage": "理論ステージ",
            "part": "パーツ",
        }

    def test_unknown_keys_fall_back_to_the_key_itself(self):
        """未知キーはキー文字列をそのまま返す（fail-soft・情報を落とさない）。"""
        src = _read(VOCAB_JS)
        start = src.index("function lookup(table, key) {")
        end = src.index("\n  }\n", start)
        body = src[start:end]
        assert "hasOwnProperty.call(table, raw)" in body
        assert "return raw;" in body

    def test_no_stale_translations_remain(self):
        """統一で退場した訳語が辞書の値として残っていない（説明コメントでの言及は可）。"""
        src = _read(VOCAB_JS)
        for stale in ("コンポーネント", "図・画像", "claim"):
            assert ': "' + stale + '"' not in src


class TestStatusLabels:
    """裏付け状態の段階ラベル（旧 admin-lecture-studio.js の LS_CONTEXT_STATUS_LABELS）。

    統一パーツカード（element-card.js, §3.3 Phase 1）と原稿スタジオの根拠リンクが
    同じ文言を引くため、正本を element-vocab.js に置いた。**表示文字列は変えない**。
    """

    def test_status_labels_are_the_existing_wording(self):
        src = _read(VOCAB_JS)
        start = src.index("var STATUS_LABELS = {")
        block = src[start:src.index("};", start)]
        assert dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block)) == {
            "source_backed": "出典に裏付け",
            "confirmed": "教員確定",
            "candidate": "AI候補",
        }

    def test_unknown_status_returns_empty_string(self):
        """未知の状態値は "" を返す（呼び出し側がバッジ自体を出さない判断に使う。
        内部語彙をそのまま画面に出さない — 旧 lsContextStatusBadgeHtml の挙動）。"""
        src = _read(VOCAB_JS)
        start = src.index("function statusLabel(status) {")
        body = src[start:src.index("\n  }\n", start)]
        assert "hasOwnProperty.call(STATUS_LABELS, raw)" in body
        assert 'return "";' in body

    def test_unidentified_is_not_a_badge(self):
        """未同定はバッジにしない（レーンの事実文で語る）。"""
        src = _read(VOCAB_JS)
        start = src.index("var STATUS_LABELS = {")
        assert "unidentified" not in src[start:src.index("};", start)]

    def test_lecture_studio_has_no_status_label_dictionary(self):
        """原稿スタジオは段階ラベルの辞書もバッジ関数も持たない。

        §3.3 Phase 0 では委譲関数（lsContextStatusLabel）を置いていたが、Phase 3 で
        文脈描画を統一パーツカードへ寄せた結果、原稿スタジオ側から状態ラベルを描く
        経路そのものが無くなった（カードが ElementVocab.statusLabel を引く）。"""
        src = _read(ADMIN_LS_JS)
        assert "LS_CONTEXT_STATUS_LABELS" not in src
        assert "function lsContextStatusLabel" not in src
        assert "function lsContextStatusBadgeHtml" not in src

    def test_element_card_delegates_status_labels(self):
        """統一パーツカードは正本（ElementVocab.statusLabel）だけを引く。"""
        src = _read(JS_DIR / "element-card.js")
        start = src.index("function vocabStatusLabel(status) {")
        body = src[start : src.index("\n  }\n", start)]
        assert "vocab.statusLabel(status)" in body
        # カード内に独自の状態ラベル辞書を持たない
        assert "STATUS_LABELS = {" not in src


# ---------------------------------------------------------------------------
# 3. 読み込み順（参照側より前）
# ---------------------------------------------------------------------------


class TestScriptLoadOrder:
    def _positions(self, html: str, names: list[str]) -> dict:
        out = {}
        for name in names:
            m = re.search(r'<script src="/js/' + re.escape(name) + r'(\?[^"]*)?"', html)
            assert m, "script タグが無い: " + name
            out[name] = m.start()
        return out

    def test_admin_html_loads_vocab_before_consumers(self):
        html = _read(ADMIN_HTML)
        pos = self._positions(
            html,
            ["element-vocab.js", "deliberation.js", "admin-lecture-studio.js", "admin.js"],
        )
        for consumer in ("deliberation.js", "admin-lecture-studio.js", "admin.js"):
            assert pos["element-vocab.js"] < pos[consumer], consumer

    def test_index_html_loads_vocab_before_app(self):
        html = _read(INDEX_HTML)
        pos = self._positions(html, ["element-vocab.js", "app.js"])
        assert pos["element-vocab.js"] < pos["app.js"]

    def test_existing_admin_load_order_conventions_preserved(self):
        """既存規約（CLAUDE.md 開発ルール5）を壊していないこと:
        admin-lecture-studio.js は doubt-atlas.js より後・admin.js より前、
        admin-llm-models.js は admin.js より前。"""
        html = _read(ADMIN_HTML)
        pos = self._positions(
            html,
            ["doubt-atlas.js", "admin-lecture-studio.js", "admin-llm-models.js", "admin.js"],
        )
        assert pos["doubt-atlas.js"] < pos["admin-lecture-studio.js"] < pos["admin.js"]
        assert pos["admin-llm-models.js"] < pos["admin.js"]


# ---------------------------------------------------------------------------
# 4. 参照側が独自辞書を持たない
# ---------------------------------------------------------------------------


class TestConsumersDelegate:
    # §3.1(1) の6辞書のうち、種別 → 表示名の辞書として撤去したもの。
    RETIRED_DICTS = [
        "LS_EVIDENCE_KIND_LABELS",
        "MATERIAL_EVIDENCE_KIND_LABELS",
        "ELEMENT_TYPE_LABELS",
        "INVENTORY_TYPE_CHIP_LABELS",
        "INVENTORY_TYPE_BADGE_LABELS",
    ]

    def test_retired_dictionaries_are_not_redefined(self):
        for path in CONSUMER_JS:
            if not path.exists():
                continue
            src = _read(path)
            for name in self.RETIRED_DICTS:
                assert name not in src, path.name + " が " + name + " を再定義している"

    def test_each_consumer_delegates_to_element_vocab(self):
        for path in (ADMIN_LS_JS, APP_JS, DELIBERATION_JS):
            src = _read(path)
            assert "window.ElementVocab" in src, path.name

    def test_no_inline_type_label_literals_reappear(self):
        """種別 → **日本語表示名** のインライン辞書リテラル（theory_component: "論理要素" 等）を
        参照側に書き戻さない。値が ASCII のみの写像（kind ⇄ element_type の変換表など）は
        表示名ではないので対象外。"""
        pattern = re.compile(
            r"(theory_component|theory_claim|shared_part|derivation)\s*:\s*\"([^\"]*)\""
        )
        for path in CONSUMER_JS:
            if not path.exists():
                continue
            hits = [
                key + ': "' + value + '"'
                for key, value in pattern.findall(_read(path))
                if not value.isascii()
            ]
            assert hits == [], path.name + " に種別表示名の辞書リテラルが再出現: " + str(hits)

    def test_graph_detail_ref_rows_use_the_vocabulary(self):
        """理論グラフ詳細ペインの行ラベル（旧: "式" / "claim" / "evidence" /
        "derivation" / "関連要素" の引数直書き）が正本経由になっていること（§3.1(1) 2行目）。

        §3.3 Phase 2 で詳細ペインは統一パーツカードへ移行したため、行ラベルの直書きは
        `element_type` の受け渡し（表示名は ElementCard → element-vocab.js が引く）に
        置き換わっている。旧 `lsGraphRefRowHtml` 系の HTML 組み立ては撤去済み。"""
        src = _read(ADMIN_LS_JS)
        # 旧実装（行ラベル直書きの HTML 組み立て）が復活していないこと。
        assert "function lsGraphRefRowHtml" not in src
        assert "function lsGraphRefChipHtml" not in src
        assert 'lsGraphRefRowHtml(resolver, "claim"' not in src
        assert 'lsGraphRefRowHtml(resolver, "式"' not in src
        assert 'lsGraphRefRowHtml(resolver, "関連要素"' not in src
        # kind → backend element_type の写像を経由し、表示名は正本が引く。
        assert "var LS_GRAPH_REF_ELEMENT_TYPES = {" in src
        block = src[src.index("var LS_GRAPH_REF_ELEMENT_TYPES = {"):]
        block = block[: block.index("};") + 2]
        for key, element_type in (
            ("component", "theory_component"),
            ("claim", "theory_claim"),
            ("evidence", "evidence"),
            ("derivation", "derivation"),
        ):
            assert key + ': "' + element_type + '"' in block, key
        assert "function lsElementTypeLabel(elementType) {" in src
