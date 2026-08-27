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

Phase 2（バッチ取り込み + 事前見積り、§5）で追加した固定:

- 取り込みの2経路（5件以下＝同期 `/ingest` / 6件以上＝キュー `/ingest-batch`）と、
  経路ごとに切り替わる確認事実文。クライアント側の上限先回り検査（上限の強制はサーバ）。
- キュー投入を「取り込み完了」と偽らないこと（候補行の status をローカルで
  `ingested` に書き換えない — PD6）。
- 事前見積り行が fail-soft（取得失敗で行ごと出さない・取り込みを止めない）であり、
  U1 のとおり reported / estimated を合算せず、金額を出さないこと。
- 取り込みキュー欄が手動更新のみ（ポーリング禁止 — PD8）で、失敗行を保持して
  明示操作でのみ再試行できること（P4 / PD1）。

Phase 3（関連度順の並べ替え + 引用グラフからの候補、§6）で追加した固定:

- 並び順は検索リクエストのパラメータであり、クライアント側で候補を並べ替えないこと。
- `relevance_label` は**サーバが確定した文字列をそのまま描く**こと（クライアント側の
  閾値表・数値→ラベル変換を持たない — PD4）。
- `ranking.available:false` を黙って新着順に落とさず note を出すこと（PD6）。
- 引用グラフ供給は明示操作（ボタン）でのみ実行し、`citation_source_enabled:false` の
  ときは無効化 + 事実文を出すこと（強制はサーバ側 — UI は補助）。
- 引用グラフ一覧は出所（`derived_from` / `seeds` / `closed_world_note`）を常時明示し、
  通常検索の一覧と混ざらないこと（PD6）。

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
    # Phase 2（取り込みキュー欄）
    "materials.arxiv-discovery-queue",
    "materials.arxiv-discovery-queue-refresh",
    # Phase 3（並び順トグル / 引用グラフからの候補）
    "materials.arxiv-discovery-order",
    "materials.arxiv-discovery-citation-search",
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


# ---------------------------------------------------------------------------
# ⑫ Phase 2: 取り込みの2経路（同期 / キュー）
# ---------------------------------------------------------------------------


class TestBatchIngestRouting:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_boundary_constants_are_declared_once(self):
        """境界（5件）と上限（50件）はリテラルを散らさず定数で持つ。"""
        assert re.search(r"var SYNC_INGEST_MAX = 5;", self.src)
        assert re.search(r"var BATCH_INGEST_MAX = 50;", self.src)

    def test_boundary_predicate_is_strictly_greater_than_five(self):
        """5件以下は同期、6件以上がキュー（境界の判定を1箇所に閉じる）。"""
        body = _extract_function(self.src, "usesBatchIngest")
        assert "count > SYNC_INGEST_MAX" in body

    def test_run_ingest_selects_endpoint_by_batch_flag(self):
        body = _extract_function(self.src, "runIngest")
        assert "usesBatchIngest(ids.length)" in body
        assert '"/admin/discovery/ingest-batch"' in body
        assert '"/admin/discovery/ingest"' in body

    def test_batch_notice_states_queueing_not_completion(self):
        """キュー経路の事実文（何が起きるか）を省略しない（PD1）。"""
        assert "件をキューに登録します。" in self.src
        assert "サーバが順に取得・解析します（1件ずつ・間隔をあけて実行）。" in self.src
        assert "進捗はこのモーダルの取り込みキュー欄と教材一覧で確認できます。" in self.src
        # 同期経路と同じ「LLM を使う」「候補として保存」の告知も落とさない。
        assert "解析には LLM を使用します" in self.src
        assert "公開するまで学習者には表示されません" in self.src

    def test_summary_switches_notice_by_route(self):
        body = _extract_function(self.src, "renderIngestSummary")
        assert "usesBatchIngest(ids.length)" in body
        assert "BATCH_NOTICE_TAIL" in body
        assert "INGEST_NOTICE_TAIL" in body

    def test_client_side_limit_check_precedes_the_request(self):
        """上限の強制はサーバ（422）。クライアントは先回りの案内のみ。"""
        summary = _extract_function(self.src, "renderIngestSummary")
        assert "ids.length > BATCH_INGEST_MAX" in summary
        assert "overLimit" in summary
        run = _extract_function(self.src, "runIngest")
        assert "ids.length > BATCH_INGEST_MAX" in run
        assert "BATCH_LIMIT_NOTICE_HEAD" in run

    def test_batch_payload_carries_domain_and_titles(self):
        body = _extract_function(self.src, "runIngest")
        assert "payload.domain_key" in body
        assert "entry.title" in body

    def test_batch_reuses_existing_upload_options(self):
        """analyze_images / models はバッチでも既存アップロード欄から引き継ぐ。"""
        body = _extract_function(self.src, "runIngest")
        assert "uploadOptions()" in body


# ---------------------------------------------------------------------------
# ⑬ Phase 2: キュー投入を「完了」と偽らない（PD6）
# ---------------------------------------------------------------------------


class TestBatchResultHonesty:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)
        self.body = _extract_function(self.src, "handleBatchResult")

    def test_does_not_fake_ingested_status_locally(self):
        """キュー登録は取り込み完了ではない。候補行の status を書き換えない。"""
        assert "ingested" not in self.body, "キュー投入で取り込み済みに見せている"
        assert "candidate.status" not in self.body

    def test_does_not_push_queued_items_into_the_upload_pipeline(self):
        """受理は非同期。ここで handleUploadAccepted を呼ばない（教材一覧の偽装防止）。"""
        assert "onUploadAccepted" not in self.body

    def test_reports_queued_count_and_skipped_details(self):
        assert "data.queued" in self.body
        assert "data.skipped" in self.body
        assert "skip.detail" in self.body, "skipped の detail をサーバ文のまま出す"

    def test_notice_is_always_surfaced_when_present(self):
        assert "data.notice" in self.body

    def test_refreshes_the_queue_once_after_registering(self):
        assert "loadQueue();" in self.body
        assert "setInterval" not in self.body


# ---------------------------------------------------------------------------
# ⑭ Phase 2: 事前見積り（U層のレンジ表示の流儀）
# ---------------------------------------------------------------------------


class TestIngestEstimate:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_uses_the_dedicated_endpoint_once(self):
        body = _extract_function(self.src, "ensureEstimate")
        assert '"/admin/discovery/ingest-estimate"' in body
        # 1 回だけ取得してキャッシュする（開くたびにリセット）。
        assert "state.estimateRequested" in body

    def test_fetch_failure_is_fail_soft(self):
        """見積りが取れなくても取り込みを止めない（行ごと出さないだけ）。"""
        body = _extract_function(self.src, "ensureEstimate")
        assert ".catch(" in body
        assert "state.estimate = null;" in body
        render = _extract_function(self.src, "renderEstimateLine")
        assert 'node.textContent = "";' in render

    def test_unavailable_is_stated_with_server_note(self):
        body = _extract_function(self.src, "renderEstimateLine")
        assert "data.available === false" in body
        assert "data.note" in body

    def test_reported_and_estimated_are_not_summed(self):
        """U1: 実測と推計を合算した単一数値を作らない。"""
        body = _extract_function(self.src, "estimateBucketTexts")
        assert '"reported"' in body
        assert '"estimated"' in body
        assert "total_tokens_range" in body
        for banned in ("+ range[1]", "range[0] + range", "合計"):
            assert banned not in body, f"レンジを合成している痕跡: {banned}"

    def test_line_is_per_document_times_selection(self):
        body = _extract_function(self.src, "renderEstimateLine")
        assert "ESTIMATE_LINE_HEAD" in body
        assert "data.per_document" in body
        assert '" × "' in body
        assert "basis_note" in body
        assert "1論文あたりの解析トークンの目安: " in self.src

    def test_no_currency_is_displayed(self):
        src = _strip_comments(self.src)
        for banned in ("cost_usd", "cost", "USD", "$", "円"):
            assert banned not in src, f"金額表示の痕跡: {banned}"

    def test_estimate_is_requested_only_when_something_is_selected(self):
        body = _extract_function(self.src, "renderIngestSummary")
        assert "if (ids.length) ensureEstimate();" in body


# ---------------------------------------------------------------------------
# ⑮ Phase 2: 取り込みキュー欄（手動更新のみ・失敗は保持）
# ---------------------------------------------------------------------------


class TestIngestQueuePane:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_queue_pane_is_a_collapsible_with_an_anchor(self):
        assert 'id="pd-queue"' in self.src
        assert '<details id="pd-queue" data-ui-anchor="materials.arxiv-discovery-queue"' in self.src
        assert 'id="pd-queue-list"' in self.src

    def test_manual_refresh_button_exists(self):
        assert 'id="pd-queue-refresh"' in self.src
        assert 'data-ui-anchor="materials.arxiv-discovery-queue-refresh"' in self.src
        assert "自動では更新されません。" in self.src

    def test_queue_is_fetched_only_on_open_refresh_and_after_batch(self):
        """PD8: ポーリングしない。読むのは3つの明示契機だけ。"""
        assert self.src.count("loadQueue();") == 3
        assert "loadQueue();" in _extract_function(self.src, "openModal")
        assert "loadQueue();" in _extract_function(self.src, "handleBatchResult")
        body = _extract_function(self.src, "loadQueue")
        assert '"/admin/discovery/ingest-queue"' in body
        assert "setInterval" not in body

    def test_all_four_status_labels_exist(self):
        for status, label in (
            ("queued", "待機中"),
            ("fetching", "取得中"),
            ("accepted", "受理済み"),
            ("failed", "失敗"),
        ):
            assert status + ": \"" + label + '"' in self.src, f"status ラベルが無い: {status}"

    def test_empty_queue_is_stated(self):
        assert "キューに項目はありません。" in self.src

    def test_failed_rows_keep_detail_and_offer_retry(self):
        body = _extract_function(self.src, "queueRowHtml")
        assert 'status === "failed"' in body
        assert "item.detail" in body
        assert "pd-queue-retry" in body
        assert "再試行" in body

    def test_retry_is_an_explicit_post_and_surfaces_422_detail(self):
        body = _extract_function(self.src, "retryQueueItem")
        assert '"/retry"' in body
        assert '"POST"' in body
        assert "detailText(err," in body
        # 行削除しない（P4）。
        assert "splice" not in body
        assert '"DELETE"' not in body

    def test_queue_load_failure_is_stated_not_silently_empty(self):
        body = _extract_function(self.src, "renderQueue")
        assert "state.queueError" in body
        assert "取り込みキューを読み込めませんでした。" in self.src


# ---------------------------------------------------------------------------
# ⑯ Phase 3: 並び順トグル（サーバ側の並べ替えパラメータ）
# ---------------------------------------------------------------------------


class TestOrderToggle:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_order_control_exists_with_anchor(self):
        assert 'id="pd-order"' in self.src
        assert 'data-ui-anchor="materials.arxiv-discovery-order"' in self.src

    def test_both_orders_are_offered_with_date_as_default(self):
        assert 'var ORDER_LABELS = { date: "新着順", relevance: "関連度順" };' in self.src
        assert '<option value="date">' in self.src
        assert '<option value="relevance">' in self.src
        # 既定は新着順（先に描く option / 初期 state の両方）。
        assert self.src.index('<option value="date">') < self.src.index(
            '<option value="relevance">'
        )
        assert 'order: "date",' in self.src

    def test_search_request_carries_the_order(self):
        body = _extract_function(self.src, "runSearch")
        assert "order: state.order" in body

    def test_changing_the_order_does_not_search_automatically(self):
        """PD8: 並び順を変えただけで勝手に fetch しない（次の検索から効く）。"""
        body = _extract_function(self.src, "openModal")
        handler = body.split('el("pd-order").addEventListener("change"', 1)
        assert len(handler) == 2, "並び順の change ハンドラが無い"
        assert "runSearch" not in handler[1].split("});", 1)[0]
        assert "並び順は次の検索から適用されます。" in self.src

    def test_client_never_reorders_the_candidate_list(self):
        """並べ替えはサーバの責務。返却順をそのまま描く。"""
        assert ".sort(" not in self.src, "クライアント側で候補を並べ替えている"
        render = _extract_function(self.src, "renderCandidates")
        assert "sort" not in render
        visible = _extract_function(self.src, "visibleCandidates")
        assert "sort" not in visible

    def test_applied_order_comes_from_the_response(self):
        """要求した並び順ではなく、サーバが実際に適用した並び順を表示に使う。"""
        body = _extract_function(self.src, "runSearch")
        assert "state.appliedOrder" in body
        assert 'data.order === "relevance"' in body
        note = _extract_function(self.src, "renderQueryNote")
        assert "state.appliedOrder" in note
        assert "並び順: " in note


# ---------------------------------------------------------------------------
# ⑰ Phase 3: relevance_label はサーバ文字列の素通し（PD4）
# ---------------------------------------------------------------------------


class TestRelevanceLabel:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)
        self.stripped = _strip_comments(self.src)

    def test_label_is_rendered_verbatim_from_the_candidate(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "candidate.relevance_label" in body
        assert "esc(candidate.relevance_label)" in body

    def test_label_is_only_rendered_when_present(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "if (candidate && candidate.relevance_label)" in body

    def test_no_client_side_threshold_table_or_conversion(self):
        """クライアントに数値→ラベルの変換表を持たない（サーバが確定した表示）。"""
        for banned in (
            "RELEVANCE_LABELS",
            "relevance_score",
            "relevanceScore",
            "state.relevance >",
            "toFixed",
            "Math.round",
            "parseFloat",
        ):
            assert banned not in self.stripped, f"クライアント側の数値処理: {banned}"

    def test_label_text_is_not_hardcoded_in_the_module(self):
        """「関連: 高」のような表示文字列を実装側で作らない（サーバの文字列を出す）。"""
        for banned in ("関連: 高", "関連: 中", "関連: 低"):
            assert banned not in self.stripped, f"ラベル文字列の実装側での生成: {banned}"

    def test_still_no_numeric_scores_anywhere(self):
        for banned in (".confidence", ".score", ".similarity", "類似度", "一致度"):
            assert banned not in self.stripped, f"数値スコアの痕跡: {banned}"


# ---------------------------------------------------------------------------
# ⑱ Phase 3: ranking.available:false を黙らない（PD6）
# ---------------------------------------------------------------------------


class TestRankingNote:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_ranking_note_container_sits_next_to_the_query_note(self):
        assert 'id="pd-ranking-note"' in self.src
        assert self.src.index('id="pd-query-note"') < self.src.index(
            'id="pd-ranking-note"'
        )

    def test_search_stores_ranking_from_the_response(self):
        body = _extract_function(self.src, "runSearch")
        assert "state.ranking = (data && data.ranking) || null;" in body

    def test_unavailable_ranking_shows_the_server_note(self):
        body = _extract_function(self.src, "renderRankingNote")
        assert "ranking.available === false" in body
        assert "ranking.note" in body

    def test_fallback_notice_states_the_list_is_still_date_ordered(self):
        """note が取れなくても「関連度順で並んでいる」と誤認させない。"""
        assert (
            "関連度順の並べ替えは利用できませんでした。新着順のまま表示しています。"
            in self.src
        )
        body = _extract_function(self.src, "renderRankingNote")
        assert "RANKING_UNAVAILABLE_NOTICE" in body

    def test_ranking_note_is_rendered_on_open_and_after_search(self):
        assert "renderRankingNote();" in _extract_function(self.src, "openModal")
        assert "renderRankingNote();" in _extract_function(self.src, "runSearch")


# ---------------------------------------------------------------------------
# ⑲ Phase 3: 引用グラフからの候補（明示操作・出所の明示）
# ---------------------------------------------------------------------------


class TestCitationSearch:
    def setup_method(self):
        self.src = _read(DISCOVERY_JS)

    def test_button_exists_with_anchor_and_starts_disabled(self):
        assert 'id="pd-citation-btn"' in self.src
        assert 'data-ui-anchor="materials.arxiv-discovery-citation-search"' in self.src
        assert "引用グラフから探す" in self.src
        # 初期描画は無効（サーバの宣言を読むまで開けない — fail-closed の補助）。
        marker = self.src.index('id="pd-citation-btn"')
        assert "disabled" in self.src[marker : marker + 260]

    def test_enabled_only_when_server_declares_it(self):
        body = _extract_function(self.src, "renderCitationControl")
        assert "state.citationEnabled !== true" in body
        load = _extract_function(self.src, "loadSubscriptions")
        assert "data.citation_source_enabled" in load

    def test_disabled_state_states_the_fact(self):
        assert "引用グラフによる候補供給は有効化されていません（サーバ設定）。" in self.src
        body = _extract_function(self.src, "renderCitationControl")
        assert "CITATION_DISABLED_NOTICE" in body
        # 可否を確認できないときも黙って開けない。
        assert "CITATION_UNKNOWN_NOTICE" in body
        assert "引用グラフによる候補供給の利用可否を確認できませんでした。" in self.src

    def test_posts_to_the_dedicated_endpoint_with_domain_key(self):
        body = _extract_function(self.src, "runCitationSearch")
        assert '"/admin/discovery/citation-search"' in body
        assert '"POST"' in body
        assert "domain_key: state.domainKey" in body

    def test_runs_only_on_explicit_click(self):
        """PD8: 自動実行しない（openModal / 検索完了から呼ばない）。"""
        code = _strip_comments(self.src)
        assert code.count("runCitationSearch") == 2, (
            "runCitationSearch の出現は 関数定義 と クリックバインド の2箇所だけであるべき"
        )
        assert (
            'el("pd-citation-btn").addEventListener("click", runCitationSearch);'
            in self.src
        )
        # 起動時に呼ぶ経路が無い（`runCitationSearch()` は関数定義の1箇所だけ）。
        assert self.src.count("runCitationSearch()") == 1
        assert "function runCitationSearch() {" in self.src
        assert "runCitationSearch" not in _extract_function(self.src, "runSearch")
        assert (
            _extract_function(self.src, "openModal").count("runCitationSearch") == 1
        ), "openModal からの runCitationSearch はクリックバインドの1回だけ"
        assert "setInterval" not in self.src

    def test_refuses_while_button_disabled(self):
        body = _extract_function(self.src, "runCitationSearch")
        assert "button.disabled" in body

    def test_failure_surfaces_server_detail(self):
        """502（外部 API 不達）等はサーバの事実文をそのまま見せる。"""
        body = _extract_function(self.src, "runCitationSearch")
        assert "detailText(err," in body
        assert "rejectWithBody(res)" in body

    def test_disabled_or_unavailable_response_shows_the_note_and_no_candidates(self):
        body = _extract_function(self.src, "applyCitationResult")
        assert "data.enabled === false || data.available === false" in body
        assert "state.candidates = [];" in body
        assert "data.note" in body

    def test_results_reuse_the_shared_candidate_rendering(self):
        """取り込み経路を分岐させない（同じカード・同じチェックボックス）。"""
        body = _extract_function(self.src, "applyCitationResult")
        assert "state.candidates =" in body
        assert "renderCandidates();" in body
        assert "renderIngestSummary();" in body
        # 専用の取り込み経路を作らない。
        assert "/admin/discovery/ingest" not in body

    def test_derived_from_is_rendered_per_candidate(self):
        body = _extract_function(self.src, "candidateCardHtml")
        assert "candidate.derived_from" in body
        assert "CITATION_DERIVED_HEAD" in body
        assert "origin.title || origin.arxiv_id" in body
        assert 'var CITATION_DERIVED_HEAD = "引用元: ";' in self.src

    def test_condition_row_switches_to_seeds_and_closed_world_note(self):
        body = _extract_function(self.src, "renderQueryNote")
        assert 'state.mode === "citation"' in body
        assert "CITATION_MODE_LABEL" in body
        assert "CITATION_SEEDS_HEAD" in body
        assert "state.closedWorldNote" in body
        assert "citationSeedTitles()" in body
        assert 'var CITATION_SEEDS_HEAD = "シード: ";' in self.src
        assert 'var CITATION_MODE_LABEL = "候補の出所: 引用グラフ";' in self.src

    def test_seed_titles_fall_back_to_ids_without_inventing_text(self):
        body = _extract_function(self.src, "citationSeedTitles")
        assert "seed.title || seed.arxiv_id" in body

    def test_citation_and_search_results_do_not_mix(self):
        """通常検索は出所を search に戻し、引用グラフのシード・注記を持ち越さない。"""
        search = _extract_function(self.src, "runSearch")
        assert 'state.mode = "search";' in search
        assert "state.citationSeeds = [];" in search
        citation = _extract_function(self.src, "applyCitationResult")
        assert 'state.mode = "citation";' in citation
        # 引用グラフ一覧は並べ替えの対象外（前回の ranking を持ち越さない）。
        assert "state.ranking = null;" in citation

    def test_empty_citation_list_is_not_called_absence_of_papers(self):
        body = _extract_function(self.src, "renderCandidates")
        assert 'state.mode === "citation"' in body
        assert "CITATION_EMPTY_NOTICE" in body
        assert "state.citationNote" in body
        assert (
            "引用グラフからは候補が見つかりませんでした。"
            "取り込み済みの論文が増えると候補が変わることがあります。" in self.src
        )
