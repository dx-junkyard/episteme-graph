"""論文ディスカバリー層（arXiv 分野購読）のフロント静的ガードレール。

正本: `docs/features/paper_discovery_design.md`（不変条項 PD1〜PD8、§4.4 UI）。

固定するのは以下:

- モジュールが ES5 で書かれ、`window.PaperDiscovery`（init / openModal）を公開すること
  （開発ルール5。admin 系 JS の共通規約）。
- 管理UI 3点セットの第3点 — 5つの `data-ui-anchor` 担体が実在すること
  （`ADMIN_UI_ANCHORS` 登録とマニュアル節は別担当。ここでは担体だけを固定する）。
- PD1: 取り込み前に「何が起きるか」の事実文（LLM を使う・候補として保存される）を出すこと。
- PD2: 受理後は既存アップロードと同一の合流点（`handleUploadAccepted`）へ渡すこと。
- PD3: キーフレーズの供給元ラベルが4語彙とも実装され、外したチップが削除ではなく
  打ち消し表示で保持されること。
- PD4: 数値スコア（confidence / score / 類似度）を描画しないこと。
- PD6: 検索条件と `closed_world_note` を一覧の上に常時表示し、空一覧を「該当なし」と
  偽らないこと。
- PD8: ポーリング（setInterval）・自動表示をしないこと。
- 許可ドメイン未設定時の事実文と取り込みボタンの無効化（UF1 継承の補助表示）。

すべて静的解析（部分文字列・正規表現）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend" / "public"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
DISCOVERY_JS = FRONTEND_DIR / "js" / "admin-paper-discovery.js"

ANCHORS = (
    "materials.arxiv-discovery",
    "materials.arxiv-discovery-modal",
    "materials.arxiv-discovery-search",
    "materials.arxiv-discovery-ingest",
    "materials.arxiv-discovery-subscribe",
)

# 属性直書き（`data-ui-anchor="X"`）と setAttribute（`"data-ui-anchor", "X"`）の両形。
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
    # 次の同階層 function 定義の手前までを本体とみなす（2 スペースインデント）。
    return after.split("\n  function ")[0]


# ---------------------------------------------------------------------------
# ① モジュールの存在と公開 API
# ---------------------------------------------------------------------------


class TestModuleWiring:
    def test_module_file_exists(self):
        assert DISCOVERY_JS.exists(), "admin-paper-discovery.js が見つかりません"

    def test_public_api_surface(self):
        src = _read(DISCOVERY_JS)
        assert "window.PaperDiscovery = {" in src
        start = src.index("window.PaperDiscovery = {")
        block = src[start : src.index("\n  };", start)]
        for method in ("init:", "openModal:"):
            assert method in block, f"{method} が公開 API に無い"

    def test_admin_html_loads_the_script(self):
        src = _read(ADMIN_HTML)
        assert "js/admin-paper-discovery.js" in src

    def test_script_loads_before_admin_js(self):
        """DI 注入元（admin.js）より前に読み込む（他の DI モジュールと同じ慣例）。"""
        src = _read(ADMIN_HTML)
        assert src.index("js/admin-paper-discovery.js") < src.index("js/admin.js")

    def test_admin_js_injects_dependencies(self):
        src = _read(ADMIN_JS)
        assert "window.PaperDiscovery.init({" in src
        # 受理後の合流点を DI で渡す（モジュール側に第2の実装を作らない）。
        assert "onUploadAccepted: handleUploadAccepted" in src

    def test_admin_js_opens_modal_from_entry_button(self):
        src = _read(ADMIN_JS)
        assert 'getElementById("paper-discovery-link")' in src
        assert "window.PaperDiscovery.openModal()" in src

    def test_entry_button_lives_in_admin_html(self):
        """入口はアップロードゾーン内の静的 HTML（URLから取得の隣）。"""
        src = _read(ADMIN_HTML)
        assert 'id="paper-discovery-link"' in src
        assert 'data-ui-anchor="materials.arxiv-discovery"' in src
        # 「URLから取得」の隣に置く（新しいタブを作らない）。
        assert src.index('id="url-upload-link"') < src.index('id="paper-discovery-link"')


# ---------------------------------------------------------------------------
# ② UI アンカー担体（管理UI 3点セットの第3点）
# ---------------------------------------------------------------------------


class TestUiAnchors:
    def setup_method(self):
        self.blob = _read(ADMIN_HTML) + "\n" + _read(DISCOVERY_JS)
        self.anchors = _ANCHOR_RE.findall(self.blob)

    def test_all_five_anchors_have_a_carrier(self):
        missing = [a for a in ANCHORS if a not in self.anchors]
        assert missing == [], f"data-ui-anchor 担体が無いアンカー: {missing}"

    def test_each_anchor_used_once(self):
        """1属性1ID 規約: 同じ論理IDを複数の担体に付けない。"""
        for anchor in ANCHORS:
            count = self.anchors.count(anchor)
            assert count == 1, f"{anchor} の担体が {count} 箇所"

    def test_modal_anchor_is_on_the_overlay(self):
        src = _read(DISCOVERY_JS)
        assert 'overlay.id = "paper-discovery-modal";' in src
        assert (
            'overlay.setAttribute("data-ui-anchor", "materials.arxiv-discovery-modal");'
            in src
        )


# ---------------------------------------------------------------------------
# ③ ES5（開発ルール5）
# ---------------------------------------------------------------------------


class TestEs5:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_no_arrow_functions(self):
        assert "=>" not in self.src

    def test_no_const_or_let(self):
        assert re.search(r"(^|[^\w.$])const\s+\w", self.src) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", self.src) is None

    def test_no_template_literals_or_class(self):
        assert "`" not in self.src
        assert re.search(r"(^|[^\w.$])class\s+\w", self.src) is None

    def test_no_promise_finally(self):
        """後処理は .then / .catch の中で行う（admin 系の共通流儀）。"""
        assert ".finally(" not in self.src


# ---------------------------------------------------------------------------
# ④ PD1: 取り込み確認の事実文と明示承認
# ---------------------------------------------------------------------------


class TestIngestConfirmation:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_ingest_notice_states_llm_usage_and_candidate_status(self):
        assert "解析には LLM を使用します" in self.src
        assert "解析パイプラインを実行します" in self.src
        assert "公開するまで学習者には表示されません" in self.src

    def test_summary_shows_selected_count_before_running(self):
        body = _extract_function(self.src, "renderIngestSummary")
        assert "ids.length" in body
        assert "INGEST_NOTICE_TAIL" in body

    def test_ingest_button_disabled_without_selection(self):
        body = _extract_function(self.src, "renderIngestSummary")
        assert "button.disabled" in body
        assert "!ids.length" in body

    def test_ingest_posts_selected_items_only(self):
        body = _extract_function(self.src, "runIngest")
        assert '"/admin/discovery/ingest"' in body
        assert '"POST"' in body
        assert "selectedIds()" in body

    def test_ingest_refuses_while_button_disabled(self):
        """無効化されたボタンからの実行経路を塞ぐ（fail-closed の補助）。"""
        body = _extract_function(self.src, "runIngest")
        assert "button.disabled" in body


# ---------------------------------------------------------------------------
# ⑤ PD2: 受理後は既存アップロード経路へ合流
# ---------------------------------------------------------------------------


class TestAcceptedPathIsShared:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_accepted_items_go_to_injected_handler(self):
        body = _extract_function(self.src, "handleIngestResult")
        assert "deps.onUploadAccepted(" in body

    def test_no_own_polling_or_material_list_reimplementation(self):
        body = _extract_function(self.src, "handleIngestResult")
        assert "setInterval" not in body
        assert "/admin/tasks/" not in body

    def test_reuses_existing_upload_options(self):
        """analyze_images / models は既存アップロード欄と同じ入力から引き継ぐ。"""
        body = _extract_function(self.src, "uploadOptions")
        assert "upload-analyze-images" in body
        assert "getUploadModels" in body

    def test_failed_items_surface_server_detail(self):
        body = _extract_function(self.src, "handleIngestResult")
        assert "failure.detail" in body


# ---------------------------------------------------------------------------
# ⑥ PD3: キーフレーズの供給元表示と「外す＝保持」
# ---------------------------------------------------------------------------


class TestKeyphraseChips:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_all_four_source_labels_exist(self):
        for label in (
            "分野の地図の概念から",
            "カートリッジ語彙から",
            "承認済み理論部品から",
            "手動",
        ):
            assert label in self.src, f"供給元ラベルが無い: {label}"

    def test_source_label_is_shown_as_tooltip(self):
        body = _extract_function(self.src, "renderKeyphraseChips")
        assert "SOURCE_LABELS[entry.source]" in body
        assert 'title="' in body

    def test_removing_a_chip_flips_enabled_instead_of_deleting(self):
        body = _extract_function(self.src, "renderKeyphraseChips")
        assert "enabled = !" in body
        assert "splice" not in body, "キーフレーズは削除せず enabled で保持する（PD3/P4）"

    def test_disabled_chip_is_struck_through(self):
        body = _extract_function(self.src, "chipStyle")
        assert "line-through" in body

    def test_candidates_are_fetched_from_the_dedicated_endpoint(self):
        body = _extract_function(self.src, "loadKeyphraseCandidates")
        assert '"/keyphrase-candidates"' in body

    def test_search_sends_only_enabled_keyphrases(self):
        body = _extract_function(self.src, "runSearch")
        assert "enabledKeyphrases()" in body

    def test_subscription_save_keeps_disabled_entries(self):
        """保存時も外したチップを落とさない（enabled:false のまま送る）。"""
        body = _extract_function(self.src, "saveSubscription")
        assert "keyphrases: state.keyphrases" in body
        assert '"PUT"' in body


# ---------------------------------------------------------------------------
# ⑦ PD6: 検索条件の常時表示と閉世界の正直さ
# ---------------------------------------------------------------------------


class TestClosedWorldHonesty:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_query_note_container_exists(self):
        assert 'id="pd-query-note"' in self.src

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

    def test_zero_condition_search_states_why_the_list_is_empty(self):
        """条件ゼロのとき query="" が返る（サーバは arXiv を呼ばない）。

        「まだ検索していません」と取り違えず、条件未指定であることを書く。
        """
        body = _extract_function(self.src, "renderQueryNote")
        assert "state.searched" in body
        assert "カテゴリまたはキーフレーズを指定してください" in body

    def test_empty_result_is_not_called_absence_of_papers(self):
        assert "この検索条件では候補が見つかりませんでした。" in self.src
        assert "条件を変えると別の論文が見つかることがあります。" in self.src
        for banned in ("この分野の論文はありません", "該当する論文はありません"):
            assert banned not in self.src, f"閉世界を外れた断定文: {banned}"

    def test_ingested_judgement_scope_is_stated(self):
        """source_url が無い既存行は判定できない事実を隠さない（PD6）。"""
        assert "URL経由で取り込まれた論文のみ判定できます" in self.src


# ---------------------------------------------------------------------------
# ⑧ PD4: 数値スコアを見せない
# ---------------------------------------------------------------------------


class TestNoNumericScores:
    def setup_method(self):
        self.src = _strip_comments(_read(DISCOVERY_JS))

    def test_no_score_or_confidence_fields_are_read(self):
        for banned in (
            ".confidence",
            ".score",
            ".similarity",
            "confidence_label",
            "類似度",
            "一致度",
        ):
            assert banned not in self.src, f"数値スコアの痕跡: {banned}"

    def test_matched_keyphrases_are_shown_as_names_only(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "matched_keyphrases" in body
        assert "一致: " in body


# ---------------------------------------------------------------------------
# ⑨ PD8: 押し付けない（ポーリング・自動表示なし）
# ---------------------------------------------------------------------------


class TestNoPushing:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_no_polling(self):
        assert "setInterval" not in self.src
        assert "setTimeout" not in self.src

    def test_fetches_only_when_modal_opens(self):
        body = _extract_function(self.src, "openModal")
        assert "loadSubscriptions();" in body
        assert "checkAllowedDomains();" in body

    def test_admin_js_does_not_auto_open_the_modal(self):
        """入口ボタンのクリック以外から openModal を呼ばない。"""
        src = _read(ADMIN_JS)
        occurrences = src.count("PaperDiscovery.openModal()")
        assert occurrences == 1, f"openModal 呼び出しが {occurrences} 箇所"


# ---------------------------------------------------------------------------
# ⑩ 許可ドメイン未設定時の案内（UF1 継承の補助表示）
# ---------------------------------------------------------------------------


class TestAllowedDomainNotice:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_blocked_notice_text(self):
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
        """許可リストを確認できないときも黙って通さず事実を書く。"""
        body = _extract_function(self.src, "renderIngestSummary")
        assert "DOMAIN_UNKNOWN_NOTICE" in body
        assert "許可ドメインを確認できませんでした" in self.src

    def test_server_detail_is_surfaced_not_replaced(self):
        """422（件数上限・許可ドメイン未設定）はサーバの事実文をそのまま見せる。"""
        body = _extract_function(self.src, "runIngest")
        assert "detailText(err," in body
        assert "function detailText" in self.src


# ---------------------------------------------------------------------------
# ⑪ 候補一覧の状態別表示（取り込み済み / 見送り済み）
# ---------------------------------------------------------------------------


class TestCandidateStates:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_ingested_is_labelled_and_not_selectable(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "取り込み済み" in body
        # チェックボックスは status === "new" のときだけ描く。
        assert 'status === "new"' in body

    def test_dismissed_is_hidden_unless_toggled(self):
        body = _extract_function(self.src, "visibleCandidates")
        assert 'status === "dismissed"' in body
        assert "state.showDismissed" in body

    def test_dismissed_toggle_exists(self):
        assert 'id="pd-show-dismissed"' in self.src
        assert "見送り済みを表示" in self.src

    def test_dismiss_and_restore_use_state_transitions(self):
        body = _extract_function(self.src, "setDismissed")
        assert '"/admin/discovery/dismiss"' in body
        assert '"/admin/discovery/restore"' in body
        assert '"DELETE"' not in body, "見送りは行削除ではなく状態遷移（PD5/P4）"

    def test_restore_button_exists(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "戻す" in body

    def test_summary_is_collapsible(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "<details" in body
        assert "要旨" in body
