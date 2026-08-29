"""論文レーダー（教材起点の類似論文探索と比較分析）のフロント静的ガードレール。

正本: `docs/features/paper_radar_design.md`（不変条項 PR1〜PR8、§4 UI）。

固定するのは以下:

- モジュールが ES5 で書かれ、`window.PaperRadar`（init / openModal）を公開し、
  DI 注入元（admin.js）より前に読み込まれること（開発ルール5・admin 系 JS の共通規約）。
- 管理UI 3点セットの第3点 — モーダル系6つの `data-ui-anchor` 担体（radar-modal /
  radar-distance / radar-search / radar-compare / radar-ingest / radar-provenance）と、
  admin.js 側の行メニュー担体（materials.row-radar）が実在すること。
- arXiv 出所の後付け登録: 推定を推定として言い、自動登録はタイトル一致時に1回だけ、
  一致しないときは両タイトルを並置したうえで教員の明示確認を経ること。
  `can_register === false` のときは登録導線を出さないこと。
- PR1: 起点は教材1件（`document_ref` を渡す）で、候補を保存・キャッシュしないこと。
- PR2: 距離帯はサーバの `distance_label` を素通しで描くこと。クライアントに
  閾値（0.45 / 0.30 等）も「距離キー → 帯ラベル」の変換表も持たないこと。
  ラベルの無い候補は最遠帯に混ぜず「距離を判定できませんでした」区画へ分けること。
- PR3: 取り込みは既存の弁のみ（5件以下＝`/ingest` / 6件以上＝`/ingest-batch`）で、
  境界定数・確認事実文・許可ドメイン未設定の案内が分野購読モーダルと同一であること。
- PR4: 比較文は「AI 推定」と明示し、`caveat` はサーバの文字列をそのまま出すこと
  （クライアントで注意書きを発明しない）。逐語引用を必ず添えること。
- PR5: 検索も比較も明示操作のみ（ポーリング・自動検索・自動オープンをしない）。
- PR7: 検索条件と `closed_world_note` を候補一覧の上に常時表示し、他の帯の候補を
  折りたたみで保持する（候補を捨てない）。空一覧を「近い論文が無い」と偽らない。

すべて静的解析（部分文字列・正規表現）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend" / "public"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
RADAR_JS = FRONTEND_DIR / "js" / "admin-paper-radar.js"

# モーダル側（admin-paper-radar.js）が担う5件。
MODAL_ANCHORS = (
    "materials.radar-modal",
    "materials.radar-distance",
    "materials.radar-search",
    "materials.radar-compare",
    "materials.radar-ingest",
    "materials.radar-provenance",
)
# 行メニュー側（admin.js）が担う1件。合計6件。
ROW_ANCHOR = "materials.row-radar"

_ANCHOR_RE = re.compile(r'data-ui-anchor(?:="|",\s*")([^"]+)"')


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """コメントを落としたソース（禁止語の検査は実コードだけを対象にする）。"""
    without_block = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


def _extract_function(src: str, name: str) -> str:
    marker = "function " + name + "("
    assert marker in src, f"{name} が見つかりません"
    after = src.split(marker, 1)[1]
    return after.split("\n  function ")[0]


# ---------------------------------------------------------------------------
# ① モジュールの存在と公開 API
# ---------------------------------------------------------------------------


class TestModuleWiring:
    def test_module_file_exists(self):
        assert RADAR_JS.exists(), "admin-paper-radar.js が見つかりません"

    def test_public_api_surface(self):
        src = _read(RADAR_JS)
        assert "window.PaperRadar = {" in src
        start = src.index("window.PaperRadar = {")
        block = src[start : src.index("\n  };", start)]
        for method in ("init:", "openModal:", "close:"):
            assert method in block, f"{method} が公開 API に無い"

    def test_admin_html_loads_the_script(self):
        assert "js/admin-paper-radar.js" in _read(ADMIN_HTML)

    def test_script_loads_before_admin_js(self):
        """DI 注入元（admin.js）より前に読み込む（他の DI モジュールと同じ慣例）。"""
        src = _read(ADMIN_HTML)
        assert src.index("js/admin-paper-radar.js") < src.index("js/admin.js")

    def test_admin_js_injects_dependencies(self):
        src = _read(ADMIN_JS)
        assert "window.PaperRadar.init({" in src
        # 受理後の合流点を DI で渡す（モジュール側に第2の実装を作らない — PD2）。
        block = src[src.index("window.PaperRadar.init({") :][:400]
        assert "onUploadAccepted: handleUploadAccepted" in block
        assert "apiFetch: apiFetch" in block
        assert "escHtml: escHtml" in block

    def test_admin_js_opens_modal_from_row_menu_button(self):
        src = _read(ADMIN_JS)
        assert '.admin-radar-doc-btn' in src
        assert "window.PaperRadar.openModal(" in src

    def test_row_button_requires_document_id(self):
        """document_id の無い行にはレーダー入口を出さない（landscape と同じガード）。"""
        src = _read(ADMIN_JS)
        start = src.index("var radarBtn = ")
        block = src[start : start + 700]
        assert "m.document_id" in block
        assert 'class="ls-menu-item admin-radar-doc-btn"' in block
        assert "近い論文を探す" in block

    def test_logical_anchor_for_locate_is_registered(self):
        """Copilot 道案内の論理ID（data-ui-anchor とは別系統）。"""
        src = _read(ADMIN_JS)
        assert "paper_radar_row_menu:" in src
        start = src.index("paper_radar_row_menu:")
        block = src[start : start + 300]
        assert "material-more-trigger" in block


# ---------------------------------------------------------------------------
# ② UI アンカー担体（管理UI 3点セットの第3点）
# ---------------------------------------------------------------------------


class TestUiAnchors:
    def setup_method(self):
        self.radar_anchors = _ANCHOR_RE.findall(_read(RADAR_JS))
        self.admin_anchors = _ANCHOR_RE.findall(_read(ADMIN_JS))

    def test_modal_anchors_have_a_carrier(self):
        missing = [a for a in MODAL_ANCHORS if a not in self.radar_anchors]
        assert missing == [], f"data-ui-anchor 担体が無いアンカー: {missing}"

    def test_row_anchor_has_a_carrier(self):
        assert ROW_ANCHOR in self.admin_anchors

    def test_each_anchor_used_once(self):
        """1属性1ID 規約: 同じ論理IDを複数の担体に付けない。"""
        blob = self.radar_anchors + self.admin_anchors
        for anchor in MODAL_ANCHORS + (ROW_ANCHOR,):
            count = blob.count(anchor)
            assert count == 1, f"{anchor} の担体が {count} 箇所"

    def test_modal_anchor_is_on_the_overlay(self):
        src = _read(RADAR_JS)
        assert 'overlay.id = "paper-radar-modal";' in src
        assert (
            'overlay.setAttribute("data-ui-anchor", "materials.radar-modal");' in src
        )

    def test_radar_ids_do_not_collide_with_discovery(self):
        """要素 id は pr- プレフィックス（pd- と衝突させない）。"""
        src = _read(RADAR_JS)
        assert 'id="pd-' not in src
        assert 'getElementById("pd-' not in src


# ---------------------------------------------------------------------------
# ③ ES5（開発ルール5）
# ---------------------------------------------------------------------------


class TestEs5:
    def setup_method(self):
        self.src = _read(RADAR_JS)

    def test_no_arrow_functions(self):
        assert "=>" not in self.src

    def test_no_const_or_let(self):
        assert re.search(r"(^|[^\w.$])const\s+\w", self.src) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", self.src) is None

    def test_no_template_literals_or_class(self):
        assert "`" not in self.src
        assert re.search(r"(^|[^\w.$])class\s+\w", self.src) is None

    def test_no_promise_finally(self):
        assert ".finally(" not in self.src


# ---------------------------------------------------------------------------
# ④ PR1 / PR8: 起点は教材1件・document_ref を渡す
# ---------------------------------------------------------------------------


class TestSeedScoping:
    def setup_method(self):
        self.src = _read(RADAR_JS)

    def test_seed_is_fetched_for_the_opened_document(self):
        body = _extract_function(self.src, "loadSeed")
        assert '"/admin/discovery/radar/seed?document_ref="' in body
        assert "encodeURIComponent(documentId)" in body

    def test_search_posts_document_ref_and_distance(self):
        body = _extract_function(self.src, "runSearch")
        assert '"/admin/discovery/radar/search"' in body
        assert "document_ref: state.documentId" in body
        assert "distance: state.distance" in body

    def test_open_modal_requires_a_document_id(self):
        body = _extract_function(self.src, "openModal")
        assert "if (!documentId) return;" in body

    def test_no_learner_endpoints(self):
        """PR8: 学習者向け API を叩かない（教員専用の層）。"""
        assert "/learning/" not in _strip_comments(self.src)

    def test_missing_arxiv_id_is_stated_not_faked(self):
        assert (
            "この教材は arXiv 由来として登録されていないため、カテゴリを指定してください。"
            in self.src
        )

    def test_category_source_is_always_labelled(self):
        for label in ("arXiv のカテゴリから取得", "分野購読の条件から取得", "カテゴリを入力してください"):
            assert label in self.src, f"カテゴリ供給元ラベルが無い: {label}"


# ---------------------------------------------------------------------------
# ⑤ PR2: 距離帯は素通し・閾値表を持たない
# ---------------------------------------------------------------------------


class TestDistanceBands:
    def setup_method(self):
        self.src = _read(RADAR_JS)
        self.code = _strip_comments(self.src)

    def test_distance_label_is_rendered_verbatim(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "candidate.distance_label" in body
        # ラベルを組み立て直さない（サーバ文字列 + 見出しの連結だけ）。
        assert "distance_label =" not in body

    def test_no_threshold_literals(self):
        for banned in ("0.45", "0.30", "cosine", "similarity"):
            assert banned not in self.code, f"閾値・生値の痕跡: {banned}"

    def test_no_distance_key_to_band_label_table(self):
        """near / mid / far → 帯ラベルの変換表をクライアントに持たない。"""
        for key in ("near", "mid", "far"):
            pattern = r'["\']?' + key + r'["\']?\s*:\s*"(近い|中間|遠い)"'
            assert re.search(pattern, self.code) is None, f"帯ラベル変換表がある: {key}"

    def test_unmeasured_candidates_get_their_own_section(self):
        assert "距離を判定できませんでした" in self.src
        body = _extract_function(self.src, "groupCandidatesByBand")
        # ラベルが無ければ帯に混ぜず unmeasured へ寄せる（最遠帯に化けさせない）。
        assert "unmeasured.push(candidate)" in body

    def test_other_bands_are_kept_folded(self):
        body = _extract_function(self.src, "bandSectionHtml")
        assert "<details" in body
        assert "OTHER_BAND_HEAD" in body
        assert "他の距離の候補 " in self.src

    def test_open_bands_are_remembered_across_rerenders(self):
        """比較結果の描き直しで、教員が開いた帯を畳まない（候補を隠さない）。"""
        body = _extract_function(self.src, "bandIsOpen")
        assert "state.openBands" in body
        assert "bindBandToggles(node);" in _extract_function(self.src, "renderCandidates")

    def test_banding_unavailable_is_stated(self):
        body = _extract_function(self.src, "renderQueryNote")
        assert "state.banding" in body
        assert "available === false" in body

    def test_banding_unavailable_falls_back_to_flat_list(self):
        body = _extract_function(self.src, "renderCandidates")
        assert "available === false" in body

    def test_no_numeric_scores_are_read(self):
        for banned in (".confidence", ".score", "confidence_label", "類似度", "一致度"):
            assert banned not in self.code, f"数値スコアの痕跡: {banned}"


# ---------------------------------------------------------------------------
# ⑥ PR4: 比較分析は AI 推定・caveat はサーバの文字列
# ---------------------------------------------------------------------------


class TestCompareAnalysis:
    def setup_method(self):
        self.src = _read(RADAR_JS)

    def test_compare_posts_selected_ids_only(self):
        body = _extract_function(self.src, "runCompare")
        assert '"/admin/discovery/radar/compare"' in body
        assert "arxiv_ids: ids" in body
        assert "document_ref: state.documentId" in body

    def test_compare_is_capped_at_ten(self):
        assert re.search(r"var COMPARE_MAX = 10;", self.src)
        body = _extract_function(self.src, "renderCompareControl")
        assert "ids.length > COMPARE_MAX" in body
        assert "button.disabled" in body

    def test_compare_button_disabled_without_selection_and_states_reason(self):
        body = _extract_function(self.src, "renderCompareControl")
        assert "!ids.length" in body
        assert "COMPARE_NONE_SELECTED_NOTICE" in body
        assert "比較する論文を選択してください" in self.src
        assert "一度に比較できるのは " in self.src

    def test_compare_block_is_labelled_as_ai_estimate(self):
        assert "起点論文との違い（AI 推定）" in self.src

    def test_caveat_is_rendered_from_the_server_value(self):
        body = _extract_function(self.src, "compareBlockHtml")
        assert "item.caveat" in body
        # クライアント側に独自の注意書き定数を持たない（サーバ固定文の正本を偽らない）。
        assert "本文は確認されていません" not in _strip_comments(self.src)

    def test_differences_carry_verbatim_quotes(self):
        body = _extract_function(self.src, "compareBlockHtml")
        assert "diff.evidence_quote" in body
        assert "diff.statement" in body

    def test_skipped_and_notes_are_surfaced(self):
        body = _extract_function(self.src, "applyCompareResult")
        assert "data.skipped" in body
        assert "data.notes" in body
        assert "skip.detail" in body

    def test_compare_errors_show_server_detail(self):
        body = _extract_function(self.src, "runCompare")
        assert "detailText(err," in body
        assert "function detailText" in self.src

    def test_compare_results_are_not_persisted(self):
        """結果はレスポンス限り（localStorage 等へ保存しない — PR4）。"""
        code = _strip_comments(self.src)
        assert "localStorage" not in code
        assert "sessionStorage" not in code

    # ── 重なり / 違いの2区画化 ────────────────────────────────────────────

    def test_compare_sections_have_fixed_heads(self):
        """「同じ内容」と「別の知識」を同じ箇条書きに混ぜない（主語を分ける）。"""
        assert "≒ 重なっていそうな要素 — 別の表現・文脈で同じ内容" in self.src
        assert "✚ 異なっていそうな要素 — 関連するが別の知識" in self.src

    def test_compare_overlaps_are_rendered_when_returned(self):
        body = _extract_function(self.src, "compareBlockHtml")
        assert "item.overlaps || []" in body
        assert "overlap.statement" in body
        assert "overlap.evidence_quote" in body
        # component_label は空文字で来ることがあるので有無を必ず見る。
        assert "overlap.component_label" in body

    def test_compare_falls_back_to_common_ground_without_overlaps(self):
        """overlaps を返さない旧レスポンスでも共通点を落とさない（後方互換）。"""
        body = _extract_function(self.src, "compareBlockHtml")
        assert "} else if (item.common_ground) {" in body
        assert "COMPARE_COMMON_HEAD" in body

    def test_compare_empty_notice_needs_both_sections_empty(self):
        body = _extract_function(self.src, "compareBlockHtml")
        assert "!overlaps.length && !item.common_ground" in body
        assert "比較できる違いは返されませんでした。" in self.src

    def test_compare_quotes_are_collapsed_not_dropped(self):
        """逐語引用は畳むが省かない（PR4: 引用を必ず添える）。"""
        body = _extract_function(self.src, "compareQuoteHtml")
        assert "<details" in body
        assert "COMPARE_QUOTE_SUMMARY" in body
        assert "根拠（要旨からの逐語引用）" in self.src
        # 常時表示の「引用: 」前置きは details に置き換えた（二重表示にしない）。
        assert "COMPARE_QUOTE_HEAD" not in self.src

    def test_compare_difference_aspect_is_shown_as_a_chip(self):
        body = _extract_function(self.src, "compareBlockHtml")
        assert "diff.aspect" in body
        assert "compareItemChipHtml(" in body


# ---------------------------------------------------------------------------
# ⑥-b 重なり・差分表示（landing / overlap_components / new_facets）
#     いずれもサーバが付けたラベルの素通し。数値・閾値をクライアントに持たない。
# ---------------------------------------------------------------------------


class TestOverlapAndFacetDisplay:
    def setup_method(self):
        self.src = _read(RADAR_JS)
        self.code = _strip_comments(self.src)

    def test_legend_declares_symbols_and_estimation(self):
        """記号の意味と「推定である」ことを一覧の先頭で1回だけ宣言する。"""
        assert "≒ 重なり = 既存の部品・語彙と同じ内容を扱っていそう" in self.src
        assert "✚ 新しい面 = 起点論文が触れていない主題に近そう" in self.src
        assert (
            "いずれもタイトル・要旨からの推定です（取り込み後の解析・教員確認で確定します）"
            in self.src
        )

    def test_legend_is_rendered_once_before_the_bands(self):
        body = _extract_function(self.src, "renderCandidates")
        assert "var html = relationLegendHtml();" in body
        assert 'id="pr-relation-legend"' in self.src

    def test_legend_is_gated_by_actual_signals(self):
        """関係の注記が1件も無い検索では凡例ごと出さない。"""
        legend = _extract_function(self.src, "relationLegendHtml")
        assert "if (!hasRelationSignals()) return" in legend
        body = _extract_function(self.src, "hasRelationSignals")
        assert "candidate.landing" in body
        assert "candidate.overlap_components" in body
        assert "candidate.new_facets" in body

    def test_landing_line_uses_server_labels_only(self):
        body = _extract_function(self.src, "landingLineHtml")
        for key in (
            "landing.node_label",
            "landing.region_label",
            "landing.nearness_label",
            "landing.skeleton_version",
        ):
            assert key in body, f"着地行がサーバの {key} を読んでいない"
        assert "取り込むと: " in self.src
        assert "の近くに落ちそうです" in self.src
        # 骨格の版を伏せない（VA8: 閉世界の言明は版を明示する）。
        assert "骨格 版" in self.src

    def test_landing_line_is_optional(self):
        body = _extract_function(self.src, "landingLineHtml")
        assert 'if (!landing || !landing.node_label) return "";' in body

    def test_chips_state_overlap_and_new_facet_in_fixed_words(self):
        assert "≒ 部品「" in self.src
        assert "」と重なりそう" in self.src
        assert "✚ 「" in self.src
        assert "」の近く（起点論文は未言及）" in self.src
        assert "〈推定〉" in self.src

    def test_chip_counts_are_capped_and_remainder_is_kept(self):
        """表示上限で落とした分は「ほか」で存在だけ残す（件数は出さない）。"""
        assert re.search(r"var OVERLAP_CHIP_MAX = 3;", self.src)
        assert re.search(r"var FACET_CHIP_MAX = 2;", self.src)
        body = _extract_function(self.src, "relationChipsHtml")
        assert "OVERLAP_CHIP_MAX" in body
        assert "FACET_CHIP_MAX" in body
        assert "CHIP_MORE_LABEL" in body
        assert "ほか" in self.src

    def test_chips_are_optional(self):
        body = _extract_function(self.src, "relationChipsHtml")
        assert "(candidate && candidate.overlap_components) || []" in body
        assert "(candidate && candidate.new_facets) || []" in body
        assert 'if (!overlaps.length && !facets.length) return "";' in body

    def test_unmeasured_fact_is_gated_by_relation_context(self):
        """下地ごと無いときは注記を出さない（全候補に同じ行を並べない）。"""
        body = _extract_function(self.src, "relationUnmeasuredHtml")
        assert "state.relationContext" in body
        assert "context.available" in body
        assert '!context.available) return "";' in body
        # 着地が測れた候補には出さない（重複した注記を並べない）。
        assert "candidate.landing" in body
        assert (
            "着地・重なりの近さは測定できませんでした（このまま取り込みできます）"
            in self.src
        )

    def test_relation_context_comes_from_the_search_response(self):
        body = _extract_function(self.src, "runSearch")
        assert "data.relation_context" in body
        opened = _extract_function(self.src, "openModal")
        assert "state.relationContext = null;" in opened

    def test_card_renders_landing_then_chips(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "landingLineHtml(candidate)" in body
        assert "relationUnmeasuredHtml(candidate)" in body
        assert "relationChipsHtml(candidate)" in body
        assert body.index("landingLineHtml") < body.index("relationChipsHtml")

    def test_relation_text_is_escaped(self):
        for name in (
            "relationChipHtml",
            "landingLineHtml",
            "relationUnmeasuredHtml",
            "relationChipsHtml",
            "relationLegendHtml",
        ):
            body = _extract_function(self.src, name)
            assert "esc(" in body, f"{name} が esc() を通していない"

    def test_no_client_side_relation_scoring(self):
        """近さはサーバが決める。クライアントで計算・描画しない（PR2 / VA2）。"""
        for banned in (
            "Math.sqrt",
            "cosine",
            "similarity",
            "dot_product",
            "overlap_score",
            "重なり度",
            "スコア",
        ):
            assert banned not in self.code, f"数値算出の痕跡: {banned}"


# ---------------------------------------------------------------------------
# ⑦ PR3: 取り込みは既存の弁のみ
# ---------------------------------------------------------------------------


class TestIngestReusesExistingValve:
    def setup_method(self):
        self.src = _read(RADAR_JS)

    def test_boundary_constants_match_discovery(self):
        assert re.search(r"var SYNC_INGEST_MAX = 5;", self.src)
        assert re.search(r"var BATCH_INGEST_MAX = 50;", self.src)

    def test_boundary_predicate_is_strictly_greater_than_five(self):
        body = _extract_function(self.src, "usesBatchIngest")
        assert "count > SYNC_INGEST_MAX" in body

    def test_run_ingest_selects_existing_endpoints_only(self):
        body = _extract_function(self.src, "runIngest")
        assert '"/admin/discovery/ingest-batch"' in body
        assert '"/admin/discovery/ingest"' in body
        # レーダー専用の取り込みエンドポイントを作らない（PR3）。
        assert "/radar/ingest" not in self.src

    def test_ingest_notice_states_llm_usage_and_candidate_status(self):
        assert "解析には LLM を使用します" in self.src
        assert "解析パイプラインを実行します" in self.src
        assert "公開するまで学習者には表示されません" in self.src

    def test_ingest_button_disabled_without_selection(self):
        body = _extract_function(self.src, "renderIngestSummary")
        assert "button.disabled" in body
        assert "!ids.length" in body

    def test_domain_key_comes_from_the_seed(self):
        body = _extract_function(self.src, "runIngest")
        assert "payload.domain_key = seedDomainKey()" in body

    def test_accepted_items_go_to_injected_handler(self):
        body = _extract_function(self.src, "handleIngestResult")
        assert "deps.onUploadAccepted(" in body

    def test_queue_result_does_not_fake_ingested_status(self):
        body = _extract_function(self.src, "handleBatchResult")
        assert 'status = "ingested"' not in body

    def test_reuses_existing_upload_options(self):
        body = _extract_function(self.src, "uploadOptions")
        assert "upload-analyze-images" in body
        assert "getUploadModels" in body


# ---------------------------------------------------------------------------
# ⑧ 許可ドメイン未設定時の案内（UF1 継承の補助表示）
# ---------------------------------------------------------------------------


class TestAllowedDomainNotice:
    def setup_method(self):
        self.src = _read(RADAR_JS)

    def test_blocked_notice_text_matches_discovery(self):
        assert (
            "取得先ドメインが許可されていません。システム管理者が「AIモデル」タブで設定できます。"
            in self.src
        )

    def test_checks_existing_allowlist_endpoint(self):
        body = _extract_function(self.src, "checkAllowedDomains")
        assert '"/admin/url-fetch-domains"' in body
        assert "arxiv.org" in body

    def test_blocked_state_disables_ingest_button(self):
        body = _extract_function(self.src, "renderIngestSummary")
        assert "state.domainAllowed === false" in body
        assert "DOMAIN_BLOCKED_NOTICE" in body

    def test_unknown_state_is_stated_not_silently_allowed(self):
        body = _extract_function(self.src, "renderIngestSummary")
        assert "DOMAIN_UNKNOWN_NOTICE" in body
        assert "許可ドメインを確認できませんでした" in self.src


# ---------------------------------------------------------------------------
# ⑨ PR5: 明示操作のみ（ポーリング・自動検索なし）
# ---------------------------------------------------------------------------


class TestNoPushing:
    def setup_method(self):
        self.src = _read(RADAR_JS)

    def test_no_polling_or_timers(self):
        assert "setInterval" not in self.src
        assert "setTimeout" not in self.src

    def test_open_modal_does_not_search_or_compare(self):
        body = _extract_function(self.src, "openModal")
        assert "loadSeed();" in body
        assert "checkAllowedDomains();" in body
        assert "runSearch()" not in body
        assert "runCompare()" not in body

    def test_distance_change_does_not_trigger_search(self):
        body = _extract_function(self.src, "bindDistanceChoices")
        assert "state.distance = this.value" in body
        assert "runSearch" not in body

    def test_admin_js_opens_modal_only_from_the_row_button(self):
        src = _read(ADMIN_JS)
        assert src.count("PaperRadar.openModal(") == 1


# ---------------------------------------------------------------------------
# ⑩ PR7: 閉世界の正直さ
# ---------------------------------------------------------------------------


class TestClosedWorldHonesty:
    def setup_method(self):
        self.src = _read(RADAR_JS)

    def test_query_note_container_exists(self):
        assert 'id="pr-query-note"' in self.src

    def test_query_note_renders_query_and_closed_world_note(self):
        body = _extract_function(self.src, "renderQueryNote")
        assert "state.query" in body
        assert "state.closedWorldNote" in body
        assert "検索条件" in body

    def test_search_stores_closed_world_note_from_response(self):
        body = _extract_function(self.src, "runSearch")
        assert "closed_world_note" in body
        assert "data.query" in body

    def test_query_note_is_rendered_on_open_and_after_search(self):
        assert "renderQueryNote();" in _extract_function(self.src, "openModal")
        assert "renderQueryNote();" in _extract_function(self.src, "runSearch")

    def test_empty_result_is_not_called_absence_of_papers(self):
        assert "この検索条件では候補が見つかりませんでした。" in self.src
        assert "条件を変えると別の論文が見つかることがあります。" in self.src
        for banned in ("近い論文はありません", "該当する論文はありません", "類似論文はありません"):
            assert banned not in self.src, f"閉世界を外れた断定文: {banned}"

    def test_ingested_judgement_scope_is_stated(self):
        assert "URL経由で取り込まれた論文のみ判定できます" in self.src

    def test_subscription_is_not_written_from_this_screen(self):
        """PR1: この画面から分野購読を書き換えない（保存ボタンを置かない）。"""
        code = _strip_comments(self.src)
        assert "/admin/discovery/subscriptions" not in code
        assert '"PUT"' not in code
        assert "この画面から分野購読の条件は変更されません。" in self.src

    def test_dismissals_are_not_touched(self):
        """見送りは分野購読の概念。レーダーからは書かない・読まない（§5.1）。"""
        code = _strip_comments(self.src)
        assert "/admin/discovery/dismiss" not in code
        assert "/admin/discovery/restore" not in code


# ---------------------------------------------------------------------------
# ⑪ 候補一覧の状態別表示
# ---------------------------------------------------------------------------


class TestCandidateStates:
    def setup_method(self):
        self.src = _read(RADAR_JS)

    def test_ingested_is_labelled_and_not_selectable(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "取り込み済み" in body
        assert 'status === "new"' in body

    def test_summary_is_collapsible(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "<details" in body
        assert "要旨" in body

    def test_matched_keyphrases_are_shown_as_names_only(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "matched_keyphrases" in body
        assert "一致: " in body

    def test_keyphrase_chips_keep_disabled_entries(self):
        body = _extract_function(self.src, "renderKeyphraseChips")
        assert "enabled = !" in body
        assert "splice" not in body, "キーフレーズは削除せず enabled で保持する（PD3/P4）"

    def test_keyphrase_section_is_hidden_outside_near(self):
        body = _extract_function(self.src, "renderKeyphraseSection")
        assert 'state.distance === "near"' in body

    def test_search_sends_keyphrases_only_for_near(self):
        body = _extract_function(self.src, "runSearch")
        assert 'state.distance === "near" ? enabledKeyphrases() : []' in body


# ---------------------------------------------------------------------------
# ⑫ arXiv 出所の後付け登録（3段階: 推定表示 / 自動登録 / 明示確認）
# ---------------------------------------------------------------------------


class TestArxivProvenanceRegistration:
    def setup_method(self):
        self.src = _read(RADAR_JS)
        self.code = _strip_comments(self.src)

    def test_seed_provenance_is_kept_in_state(self):
        assert "provenance: null," in self.code
        assert "provenanceAutoAttempted: false," in self.code
        body = _extract_function(self.src, "applySeed")
        assert "state.provenance = (seed && seed.provenance) || null;" in body

    def test_open_modal_resets_provenance_state(self):
        body = _extract_function(self.src, "openModal")
        assert "state.provenance = null;" in body
        assert "state.provenanceAutoAttempted = false;" in body

    def test_inferred_category_source_is_labelled_as_estimate(self):
        """推定プリフィルを「arXiv 由来として登録済み」に見せない。"""
        assert "arxiv_inferred:" in self.code
        assert (
            "ファイル名から推定した arXiv 情報です（教材の出所としては未登録）。"
            in self.src
        )

    def test_inferred_facts_are_stated(self):
        assert "ファイル名から arXiv-" in self.src
        assert " と推定し、タイトルが一致しました。" in self.src
        assert " と推定しましたが、arXiv から論文情報を取得できませんでした。" in self.src

    def test_auto_registration_is_attempted_only_once(self):
        body = _extract_function(self.src, "maybeAutoRegisterProvenance")
        assert "if (state.provenanceAutoAttempted) return;" in body
        assert "state.provenanceAutoAttempted = true;" in body
        # 一致していない推定を黙って登録しない。
        assert "prov.title_match" in body
        assert "prov.fetched" in body
        assert 'prov.status !== "inferred"' in body

    def test_auto_registration_runs_from_render_seed(self):
        body = _extract_function(self.src, "renderSeed")
        assert "maybeAutoRegisterProvenance();" in body
        assert "bindProvenanceActions(node);" in body

    def test_cannot_register_hides_the_control(self):
        """can_register === false のときだけ導線を隠す（未定義なら出す）。"""
        body = _extract_function(self.src, "provenanceHtml")
        assert "prov.can_register !== false" in body
        gate = _extract_function(self.src, "maybeAutoRegisterProvenance")
        assert "prov.can_register === false" in gate

    def test_mismatch_shows_both_titles_escaped(self):
        body = _extract_function(self.src, "provenanceHtml")
        assert "prov.arxiv_title" in body
        assert "prov.document_title" in body
        assert "esc(PROV_ARXIV_TITLE_HEAD" in body
        assert "esc(PROV_DOCUMENT_TITLE_HEAD" in body
        assert "arXiv の論文: " in self.src
        assert "この教材のタイトル: " in self.src

    def test_mismatch_requires_explicit_confirmation(self):
        body = _extract_function(self.src, "bindProvenanceActions")
        assert "window.confirm(" in body
        assert "PROV_ARXIV_TITLE_HEAD" in body
        assert "PROV_DOCUMENT_TITLE_HEAD" in body
        assert "registerProvenance(true);" in body

    def test_register_button_carries_anchor_and_label(self):
        assert 'id="pr-provenance-register"' in self.src
        assert 'data-ui-anchor="materials.radar-provenance"' in self.src
        assert "この論文として登録する" in self.src

    def test_register_posts_confirm_flag_to_the_provenance_endpoint(self):
        body = _extract_function(self.src, "registerProvenance")
        assert '"/admin/discovery/radar/provenance"' in body
        assert 'method: "POST"' in body
        assert "document_ref: documentId" in body
        assert "arxiv_id: prov.arxiv_id" in body
        assert "confirm: !!confirmed" in body

    def test_register_applies_returned_provenance_without_reverting_edits(self):
        # 登録成功は出所表示（provenance / categories_source）だけを更新する。
        # applySeed で seed 全体を差し替えると、POST の往復中に教員が編集した
        # 条件チップ（カテゴリ・キーフレーズ）が巻き戻るため禁止。
        body = _extract_function(self.src, "registerProvenance")
        assert "applySeed(" not in body
        assert "state.provenance = data.seed.provenance" in body
        assert "state.categoriesSource = data.seed.categories_source" in body
        assert "この教材の出所として登録しました。" in self.src

    def test_search_response_does_not_degrade_provenance(self):
        # 検索経路の seed は arXiv 再取得を省くため provenance が fetched=false に
        # 劣化しうる。手元の情報が減る方向の上書きをしないこと。
        body = _extract_function(self.src, "applySeedMeta")
        assert 'incoming.status === "registered" || !state.provenance' in body

    def test_register_failure_shows_server_detail_without_blocking_search(self):
        body = _extract_function(self.src, "registerProvenance")
        assert "detailText(err," in body
        # 失敗時に検索・比較を止めない（notice を出すだけ）。
        assert "state.searching" not in body
        assert "disabled" not in body

    def test_register_result_is_scoped_to_the_opened_document(self):
        body = _extract_function(self.src, "registerProvenance")
        assert "if (state.documentId !== documentId) return;" in body


# ---------------------------------------------------------------------------
# ⑬ キャッシュバスター（配信更新の取りこぼし防止）
# ---------------------------------------------------------------------------


class TestCacheBuster:
    def test_admin_html_bumps_the_radar_cache_buster(self):
        src = _read(ADMIN_HTML)
        assert "js/admin-paper-radar.js?v=paper-radar-20260829-2" in src
