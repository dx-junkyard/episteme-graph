"""コーパスを補う論文（Corpus Complement）のフロント静的ガードレール。

正本: `docs/features/corpus_complement_design.md`（不変条項 CC1〜CC8、§4 UI、§7 ui_static、§8）。

固定するのは以下:

- 管理UI 3点セット: 2つの `data-ui-anchor` 担体（`materials.arxiv-discovery-complement` /
  `materials.arxiv-discovery-foundation`）が1回ずつ実在し、`ADMIN_UI_ANCHORS` に登録され、
  マニュアル節（`docs/manual/teacher/11-admin-materials.md`）が anchor 付きで存在すること。
- 新しいモーダル・新しいタブを作らず、既存の `admin-paper-discovery.js`（ES5）に足すこと。
- CC4/CC6: サーバが付けたキーの有無をそのまま描くこと。クライアント側で閾値比較・並べ替え・
  件数の描画をしないこと。〈推定〉の出所タグを常に添えること。
- CC5: SL1 の denylist 語彙（「この分野では未検証」「誰も検証していない」「世界初」「未踏」）が
  JS・マニュアル節に無いこと。閉世界の事実文はサーバの値をそのまま出すこと。
- CC6: 断定語（「おすすめ」「必読」「重要な論文」）が JS・マニュアル節に無いこと。
- CC7: 取り込みは既存経路のまま（`state.mode` で `/ingest` の分岐を作らない）。
- CC8: レンズ縮退の事実文を出す欄があり、検索そのものを止めないこと。
- PD8 継承: ポーリング（`setInterval` / `setTimeout`）をしないこと。

すべて静的解析（部分文字列・正規表現）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend" / "public"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
DISCOVERY_JS = FRONTEND_DIR / "js" / "admin-paper-discovery.js"
MANUAL = ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"

ANCHORS = (
    "materials.arxiv-discovery-complement",
    "materials.arxiv-discovery-foundation",
)

MANUAL_ANCHORS = (
    "arxiv-discovery-complement",
    "arxiv-discovery-foundation",
)

ENDPOINTS = (
    "/admin/discovery/complement/search",
    "/admin/discovery/complement/foundation",
)

# CC5: SL1 の閉世界語彙 denylist（台帳はコーパスの射影であって分野の射影ではない）。
CLOSED_WORLD_DENYLIST = (
    "この分野では未検証",
    "誰も検証していない",
    "世界初",
    "未踏",
)

# CC6: 候補を「良い論文」と断定する語を使わない。
JUDGEMENT_DENYLIST = ("おすすめ", "必読", "重要な論文")

_ANCHOR_RE = re.compile(r'data-ui-anchor(?:="|",\s*")([^"]+)"')


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


def _extract_function(src: str, name: str) -> str:
    marker = "function " + name + "("
    assert marker in src, f"{name} が見つかりません"
    after = src.split(marker, 1)[1]
    return after.split("\n  function ")[0]


def _manual_section(anchor: str) -> str:
    """`### 見出し {#anchor}` から次の `### ` 直前までを返す。"""
    src = _read(MANUAL)
    marker = "{#" + anchor + "}"
    assert marker in src, f"マニュアル節 {anchor} が見つかりません"
    after = src.split(marker, 1)[1]
    return after.split("\n### ", 1)[0]


# ---------------------------------------------------------------------------
# ① 3点セット: アンカー担体・登録・マニュアル節
# ---------------------------------------------------------------------------


class TestUiAnchors:
    def setup_method(self):
        self.blob = _read(ADMIN_HTML) + "\n" + _read(DISCOVERY_JS)
        self.anchors = _ANCHOR_RE.findall(self.blob)

    def test_both_anchors_have_a_carrier(self):
        missing = [a for a in ANCHORS if a not in self.anchors]
        assert missing == [], f"data-ui-anchor 担体が無いアンカー: {missing}"

    def test_each_anchor_used_once(self):
        """1属性1ID 規約: 同じ論理IDを複数の担体に付けない。"""
        for anchor in ANCHORS:
            count = self.anchors.count(anchor)
            assert count == 1, f"{anchor} の担体が {count} 箇所"

    def test_anchors_are_registered_in_the_admin_anchor_table(self):
        backend_dir = str(ROOT / "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from core.help_kb.admin_ui_anchors import (  # noqa: PLC0415
            ADMIN_UI_ANCHORS,
            KNOWN_ADMIN_UI_ANCHOR_IDS,
        )

        for anchor in ANCHORS:
            assert anchor in KNOWN_ADMIN_UI_ANCHOR_IDS, anchor
            assert anchor in ADMIN_UI_ANCHORS, anchor
            target = ADMIN_UI_ANCHORS[anchor]
            # 教員向けの節だけを指す（student/ 参照は構造的禁止）。
            assert target.startswith("teacher/11-admin-materials.md#"), target

    def test_manual_sections_exist_with_explicit_anchors(self):
        src = _read(MANUAL)
        for anchor in MANUAL_ANCHORS:
            assert "{#" + anchor + "}" in src, f"マニュアル節 {anchor} が無い"

    def test_manual_sections_document_the_disabled_state(self):
        """無効化され得る要素は「ボタンが無効になっている場合」を必ず持つ。"""
        for anchor in MANUAL_ANCHORS:
            body = _manual_section(anchor)
            assert "ボタンが無効になっている場合" in body, anchor

    def test_manual_sections_state_no_llm_is_used(self):
        for anchor in MANUAL_ANCHORS:
            body = _manual_section(anchor)
            assert "AI（LLM）は使いません" in body, anchor

    def test_modal_section_points_at_the_two_new_ways(self):
        """モーダル節から2つの探し方へ導線がある（区画説明の追記）。"""
        body = _manual_section("arxiv-discovery-modal")
        for anchor in MANUAL_ANCHORS:
            assert "(#" + anchor + ")" in body, anchor


# ---------------------------------------------------------------------------
# ② 既存モジュールへの追加（新しいモーダル・新しいタブを作らない）
# ---------------------------------------------------------------------------


class TestModuleWiring:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_buttons_live_in_the_existing_modal(self):
        assert 'id="pd-complement-btn"' in self.src
        assert 'id="pd-foundation-btn"' in self.src
        # 「この条件で検索」の隣 / 「引用グラフから探す」の隣（§4.1）。
        assert self.src.index('id="pd-search-btn"') < self.src.index(
            'id="pd-complement-btn"'
        )
        assert self.src.index('id="pd-citation-btn"') < self.src.index(
            'id="pd-foundation-btn"'
        )

    def test_no_new_modal_element(self):
        """モーダルは既存の1枚だけ（第2のオーバーレイを作らない）。"""
        assert self.src.count("overlay.id = ") == 1

    def test_buttons_are_bound_to_the_two_handlers(self):
        assert (
            'el("pd-complement-btn").addEventListener("click", runComplementSearch);'
            in self.src
        )
        assert (
            'el("pd-foundation-btn").addEventListener("click", runFoundationSearch);'
            in self.src
        )

    def test_endpoints_are_posted(self):
        complement = _extract_function(self.src, "runComplementSearch")
        assert '"' + ENDPOINTS[0] + '"' in complement
        assert '"POST"' in complement
        foundation = _extract_function(self.src, "runFoundationSearch")
        assert '"' + ENDPOINTS[1] + '"' in foundation
        assert '"POST"' in foundation

    def test_complement_search_sends_the_same_conditions_without_order(self):
        """検索条件は通常検索と同じ。並び順はこの経路では常に補完優先（order を送らない）。"""
        body = _extract_function(self.src, "runComplementSearch")
        assert "domain_key: state.domainKey" in body
        assert "categories: state.categories" in body
        assert "keyphrases: enabledKeyphrases()" in body
        assert "order:" not in body

    def test_foundation_sends_only_the_domain_key(self):
        body = _extract_function(self.src, "runFoundationSearch")
        assert "JSON.stringify({ domain_key: state.domainKey })" in body

    def test_foundation_shares_the_citation_optin_gate(self):
        """第2の判定を作らず、`renderCitationControl` で有効/無効を共有する（§4.1）。"""
        body = _extract_function(self.src, "renderCitationControl")
        assert 'el("pd-foundation-btn")' in body
        assert "state.citationEnabled !== true" in body
        assert 'id="pd-foundation-btn"' in self.src
        assert 'class="admin-action-btn" disabled>基盤論文を探す' in self.src

    def test_new_modes_are_registered(self):
        assert 'state.mode = "complement";' in self.src
        assert 'state.mode = "foundation";' in self.src


# ---------------------------------------------------------------------------
# ③ ES5（開発ルール5）と PD8（ポーリング禁止）
# ---------------------------------------------------------------------------


class TestEs5AndNoPolling:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_no_arrow_functions_or_template_literals(self):
        assert "=>" not in self.src
        assert "`" not in self.src

    def test_no_const_or_let_or_class(self):
        assert re.search(r"(^|[^\w.$])const\s+\w", self.src) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", self.src) is None
        assert re.search(r"(^|[^\w.$])class\s+\w", self.src) is None

    def test_no_promise_finally(self):
        assert ".finally(" not in self.src

    def test_no_polling(self):
        assert "setInterval" not in self.src
        assert "setTimeout" not in self.src


# ---------------------------------------------------------------------------
# ④ CC4/CC6: キーの有無をそのまま描く（閾値判定・件数・点数を持たない）
# ---------------------------------------------------------------------------


class TestComplementRendering:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)
        self.stripped = _strip_comments(self.src)
        self.lines_body = _extract_function(self.stripped, "complementLines")
        self.card_body = _extract_function(self.stripped, "candidateCardHtml")

    def test_fixed_heads_are_module_constants(self):
        assert (
            'var COMPLEMENT_FILLS_HEAD = "地図の薄い領域に着地: ";' in self.src
        ), "レンズA の見出し語が固定されていない"
        assert (
            'var COMPLEMENT_SKIES_HEAD = "検証記録の無い前提に近い: ";' in self.src
        ), "レンズB の見出し語が固定されていない"
        # レンズC は既存の「引用元: 」を再利用する（第2の語彙を作らない）。
        assert 'var CITATION_DERIVED_HEAD = "引用元: ";' in self.src
        assert "CITATION_DERIVED_HEAD" in self.lines_body

    def test_estimate_tag_is_always_attached(self):
        assert 'var COMPLEMENT_ESTIMATE_TAG = "〈推定〉";' in self.src
        assert "COMPLEMENT_ESTIMATE_TAG" in self.card_body

    def test_block_is_rendered_only_when_the_server_attached_keys(self):
        assert "candidate.complement" in self.lines_body
        assert "candidate.cited_by" in self.lines_body
        # 行が1つも作れなければブロックごと出さない。
        assert "if (complementRows.length) {" in self.card_body

    def test_no_client_side_threshold_comparison(self):
        """クライアントに閾値・数値比較を持たない（判定はサーバ）。"""
        for banned in (">=", "<=", "0.45", "0.36", "0.30", "0.55", "threshold"):
            assert banned not in self.lines_body, f"クライアント側の閾値判定: {banned}"

    def test_no_client_side_reordering(self):
        """補完ありを先頭にするのはサーバ（`order_complement_first`）。"""
        for banned in (".sort(", ".reverse("):
            assert banned not in self.stripped, f"クライアント側の並べ替え: {banned}"

    def test_no_counts_are_embedded_into_rendered_text(self):
        """件数（fills / skies / cited_by の本数）を文字列に埋め込まない（CC4）。"""
        for banned in (
            ".length +",
            "+ fills.length",
            "+ skies.length",
            "+ citedBy.length",
            "String(fills.length)",
            "String(citedBy.length)",
        ):
            assert banned not in self.lines_body, f"件数の描画: {banned}"

    def test_no_numeric_scores(self):
        for banned in (
            ".confidence",
            ".score",
            ".similarity",
            ".cosine",
            "citationCount",
            "citation_count",
            "類似度",
            "一致度",
        ):
            assert banned not in self.stripped, f"数値スコアの痕跡: {banned}"

    def test_skeleton_version_comes_from_the_server(self):
        """骨格の版はサーバ値をそのまま出す（クライアントで推定しない）。"""
        body = _extract_function(self.stripped, "complementSkeletonVersion")
        assert "complement.skeleton_version" in body
        assert "COMPLEMENT_SKELETON_HEAD" in self.stripped

    def test_closed_world_note_is_passed_through(self):
        """レンズB の閉世界の事実文はサーバの値をそのまま出す（実装側で作らない）。"""
        assert "sky.closed_world_note" in self.lines_body
        assert "このコーパスの中では検証記録がありません" not in self.stripped


# ---------------------------------------------------------------------------
# ⑤ CC8: レンズの縮退を黙らない（検索そのものは成立させる）
# ---------------------------------------------------------------------------


class TestLensDegradation:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_complement_note_container_exists(self):
        assert 'id="pd-complement-note"' in self.src

    def test_both_lenses_notes_are_rendered_as_is(self):
        body = _extract_function(self.src, "renderComplementNote")
        assert "lenses.coverage" in body
        assert "lenses.skies" in body
        assert "String(coverage.note)" in body
        assert "String(skies.note)" in body

    def test_ranking_note_is_still_rendered(self):
        """ranking.available:false を黙って落とさない（PD6 継承）。"""
        body = _extract_function(self.src, "applyComplementResult")
        assert "state.ranking = (data && data.ranking) || null;" in body
        assert "renderRankingNote();" in body

    def test_query_note_states_the_fixed_order_and_map_version(self):
        body = _extract_function(self.src, "renderQueryNote")
        assert "COMPLEMENT_ORDER_NOTE" in body
        assert "COMPLEMENT_SKELETON_HEAD" in body
        assert (
            'var COMPLEMENT_ORDER_NOTE = "並び順: 補完の根拠がある候補を先に（関連度順）";'
            in self.src
        )

    def test_foundation_query_note_states_provenance_and_pending_seeds(self):
        body = _extract_function(self.src, "renderQueryNote")
        assert "FOUNDATION_MODE_LABEL" in body
        assert "FOUNDATION_SEEDS_HEAD" in body
        assert "state.foundationPending" in body
        assert (
            'var FOUNDATION_MODE_LABEL = "候補の出所: 取り込み済み論文の参照リスト";'
            in self.src
        )

    def test_foundation_degrades_like_the_citation_route(self):
        body = _extract_function(self.src, "applyFoundationResult")
        assert "data.enabled === false || data.available === false" in body
        assert "state.candidates = [];" in body

    def test_empty_list_is_not_called_no_results(self):
        body = _extract_function(self.src, "renderCandidates")
        assert "FOUNDATION_EMPTY_NOTICE" in body


# ---------------------------------------------------------------------------
# ⑥ CC7: 取り込みは既存の弁のまま（mode で経路を分岐しない）
# ---------------------------------------------------------------------------


class TestIngestPathIsUnchanged:
    def setup_method(self):
        self.src = _strip_comments(_read(DISCOVERY_JS))

    def test_ingest_does_not_branch_on_mode(self):
        for name in ("runIngest", "renderIngestSummary", "selectedIds"):
            body = _extract_function(self.src, name)
            assert "state.mode" not in body, f"{name} が mode で分岐している"

    def test_ingest_endpoints_are_the_existing_two(self):
        body = _extract_function(self.src, "runIngest")
        assert '"/admin/discovery/ingest"' in body
        assert '"/admin/discovery/ingest-batch"' in body
        assert "complement" not in body


# ---------------------------------------------------------------------------
# ⑦ CC5 / CC6: 語彙の denylist（JS・マニュアル節）
# ---------------------------------------------------------------------------


class TestVocabulary:
    def setup_method(self):
        # 禁止語の検査は実コードを対象にする（既存コメントの説明文は対象外）。
        self.js = _strip_comments(_read(DISCOVERY_JS))
        self.manual_sections = "\n".join(_manual_section(a) for a in MANUAL_ANCHORS)

    def test_closed_world_denylist_absent_from_js(self):
        for banned in CLOSED_WORLD_DENYLIST:
            assert banned not in self.js, f"閉世界語彙の逸脱: {banned}"

    def test_closed_world_denylist_absent_from_manual_sections(self):
        for banned in CLOSED_WORLD_DENYLIST:
            assert banned not in self.manual_sections, f"閉世界語彙の逸脱: {banned}"

    def test_judgement_words_absent_from_js(self):
        for banned in JUDGEMENT_DENYLIST:
            assert banned not in self.js, f"断定語: {banned}"

    def test_judgement_words_absent_from_manual_sections(self):
        for banned in JUDGEMENT_DENYLIST:
            assert banned not in self.manual_sections, f"断定語: {banned}"

    def test_manual_states_learner_signals_are_not_used(self):
        """CC1: 補完の根拠に学習者の記録を混ぜないことを教員向けに明示する。"""
        body = _manual_section("arxiv-discovery-complement")
        assert "学習者の記録" in body
