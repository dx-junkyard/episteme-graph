/**
 * 論文ディスカバリー（arXiv 分野購読）— 教材管理タブの「arXivから探す」モーダル。
 *
 * 正本: docs/features/paper_discovery_design.md（不変条項 PD1〜PD8）。
 *
 *   PD1 発見は自動・取り込みは教員の明示承認のみ（選択 → 確認 → 実行の3段を崩さない）
 *   PD2 取得・解析は既存経路へ完全合流（受理後は handleUploadAccepted へ渡すだけ）
 *   PD3 検索語彙は分野語彙から供給し、出所を明示する（チップのツールチップ）。
 *       外したチップは削除ではなく enabled:false で保持する（打ち消し表示）
 *   PD4 数値スコアを見せない（類似度・一致度の生値を描画しない）
 *   PD5 候補は保存せず読み時導出（このモジュールも候補をキャッシュ・永続化しない）
 *   PD6 閉世界の正直さ（検索条件と closed_world_note を一覧の上に常時出す）
 *   PD8 押し付けない（自動表示・ポーリングをしない。開いたときだけ fetch する）
 *
 * ES5 で書く（開発ルール5。admin.js / admin-release-review.js と同じ流儀）。
 * DI は init(deps)。
 */
(function () {
  "use strict";

  var deps = null;

  // PD3: 供給元の日本語ラベル（チップの title 属性に出す）。
  var SOURCE_LABELS = {
    skeleton: "分野の地図の概念から",
    cartridge: "カートリッジ語彙から",
    component: "承認済み理論部品から",
    manual: "手動"
  };

  // PD1: 取り込み前に必ず出す事実文（何が起きるかを省略しない）。
  var INGEST_NOTICE_TAIL =
    "件の論文を取得し、解析パイプラインを実行します。解析には LLM を使用します。" +
    "解析結果は候補として保存され、公開するまで学習者には表示されません。";
  // UF1 継承: 許可ドメイン未設定は fail-closed の表示（強制はサーバ側）。
  var DOMAIN_BLOCKED_NOTICE =
    "取得先ドメインが許可されていません。システム管理者が「AIモデル」タブで設定できます。";
  var DOMAIN_UNKNOWN_NOTICE =
    "許可ドメインを確認できませんでした。取り込みはサーバ側で拒否される場合があります。";
  // PD6: 空一覧を「この分野に論文が無い」と読ませない。
  var EMPTY_RESULT_NOTICE =
    "この検索条件では候補が見つかりませんでした。条件を変えると別の論文が見つかることがあります。";
  var SOURCE_URL_NOTICE =
    "「取り込み済み」はURL経由で取り込まれた論文のみ判定できます。";
  var KEYPHRASE_NEW_NOTICE_TAIL =
    "件の新しいキーフレーズ候補が分野語彙から供給されました（外れた状態です。使うにはクリックしてください）。";

  // ── Phase 2（バッチ取り込み + 事前見積り）─────────────────────────────
  // 少数（5件以下）は従来どおり同期取り込み、6件以上はサーバのキューへ登録する。
  // 経路が違えば起きることも違うので、確認画面の事実文も切り替える（PD1）。
  var SYNC_INGEST_MAX = 5;
  var BATCH_INGEST_MAX = 50;
  var BATCH_NOTICE_TAIL =
    "件をキューに登録します。サーバが順に取得・解析します（1件ずつ・間隔をあけて実行）。" +
    "進捗はこのモーダルの取り込みキュー欄と教材一覧で確認できます。解析には LLM を使用します。" +
    "解析結果は候補として保存され、公開するまで学習者には表示されません。";
  // 上限はサーバ側でも 422 で強制される。ここでの検査は先回りの案内にすぎない。
  var BATCH_LIMIT_NOTICE_HEAD = "一度にキューへ登録できるのは ";
  var BATCH_LIMIT_NOTICE_TAIL = " 件までです。選択を減らしてください。";
  // 取り込みキューの状態語彙（サーバの status をそのまま日本語ラベルにするだけ）。
  var QUEUE_STATUS_LABELS = {
    queued: "待機中",
    fetching: "取得中",
    accepted: "受理済み",
    failed: "失敗"
  };
  var QUEUE_EMPTY_NOTICE = "キューに項目はありません。";
  var QUEUE_LOAD_ERROR = "取り込みキューを読み込めませんでした。";
  var BATCH_QUEUED_NOTICE_TAIL =
    "件をキューに登録しました。進捗は下の「取り込みキュー」で確認できます。";
  var ESTIMATE_LINE_HEAD = "1論文あたりの解析トークンの目安: ";

  var state = {
    open: false,
    domainKey: "",
    subscriptions: [],
    categories: [],
    keyphrases: [],
    authors: [],
    candidates: [],
    query: "",
    closedWorldNote: "",
    total: null,
    searched: false,
    selected: {},
    showDismissed: false,
    searching: false,
    saving: false,
    ingesting: false,
    domainAllowed: null,
    // Phase 2: 取り込みキューは手動更新のみ（PD8。ポーリングしない）。
    queue: [],
    queueLoading: false,
    queueError: "",
    // 事前見積りはモーダルを開いている間 1 回だけ取得してキャッシュする。
    estimate: null,
    estimateRequested: false
  };

  // ── 小道具 ────────────────────────────────────────────────────────────
  function esc(text) {
    if (deps && deps.escHtml) return deps.escHtml(text == null ? "" : text);
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function api(path, options) {
    if (deps && deps.apiFetch) return deps.apiFetch(path, options);
    return Promise.reject(new Error("apiFetch is not injected"));
  }

  function el(id) {
    return document.getElementById(id);
  }

  // サーバの detail（事実文）をそのまま見せる。取れないときだけ既定文へ縮退する。
  function detailText(err, fallback) {
    if (!err) return fallback;
    var detail = err.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && detail.length && typeof detail[0] === "object" && detail[0].msg) {
      return String(detail[0].msg);
    }
    if (err.message && err.message !== "Unauthorized") return String(err.message);
    return fallback;
  }

  function rejectWithBody(res) {
    return res
      .json()
      .catch(function () {
        return {};
      })
      .then(function (body) {
        throw body || {};
      });
  }

  function setNotice(message, isError) {
    var node = el("pd-notice");
    if (!node) return;
    node.textContent = message || "";
    node.style.color = isError
      ? "var(--color-text-danger, #e53935)"
      : "var(--color-text-secondary)";
  }

  function indexOfText(list, text) {
    for (var i = 0; i < list.length; i++) {
      var entry = list[i];
      var value = typeof entry === "string" ? entry : entry && entry.text;
      if (value === text) return i;
    }
    return -1;
  }

  function enabledKeyphrases() {
    var out = [];
    for (var i = 0; i < state.keyphrases.length; i++) {
      if (state.keyphrases[i] && state.keyphrases[i].enabled) {
        out.push(state.keyphrases[i].text);
      }
    }
    return out;
  }

  // 既存アップロード欄と同じ入力から解析オプションを引き継ぐ（第2の設定 UI を作らない）。
  function uploadOptions() {
    var checkbox = el("upload-analyze-images");
    var analyzeImages = !!(checkbox && checkbox.checked);
    var payload = { analyze_images: analyzeImages };
    if (window.AdminLlmModels && window.AdminLlmModels.getUploadModels) {
      var models = window.AdminLlmModels.getUploadModels(analyzeImages);
      if (models) payload.models = models;
    }
    return payload;
  }

  // ── モーダルの骨組み ──────────────────────────────────────────────────
  function close() {
    var overlay = el("paper-discovery-modal");
    if (overlay) overlay.remove();
    state.open = false;
  }

  function modalHtml() {
    return (
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:640px;max-width:840px;width:88vw;max-height:88vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">arXivから探す</h3>' +
          '<button type="button" id="pd-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        "</div>" +
        '<div id="pd-notice" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:8px"></div>' +

        // ① 検索・購読パネル
        '<div style="border:1px solid var(--color-border-tertiary);border-radius:6px;padding:10px;margin-bottom:10px">' +
          '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">' +
            '<label for="pd-domain" style="font-size:12px;color:var(--color-text-secondary)">分野</label>' +
            '<input type="text" id="pd-domain" list="pd-domain-options" placeholder="astrophysics" ' +
              'style="padding:4px 7px;font-size:12.5px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary);min-width:200px">' +
            '<datalist id="pd-domain-options"></datalist>' +
            '<span id="pd-domain-meta" style="font-size:11.5px;color:var(--color-text-tertiary)"></span>' +
          "</div>" +

          '<div style="margin-bottom:8px">' +
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">arXiv カテゴリ</div>' +
            '<div id="pd-categories" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px"></div>' +
            '<div style="display:flex;gap:6px">' +
              '<input type="text" id="pd-category-input" placeholder="astro-ph.CO" ' +
                'style="padding:3px 7px;font-size:12px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary);min-width:170px">' +
              '<button type="button" id="pd-category-add" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">追加</button>' +
            "</div>" +
          "</div>" +

          '<div style="margin-bottom:8px">' +
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">キーフレーズ<span style="color:var(--color-text-tertiary);font-size:11px">（クリックで外す/戻す。外したものも条件として保持されます）</span></div>' +
            '<div id="pd-keyphrases" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px"></div>' +
            '<div id="pd-keyphrase-note" style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:4px"></div>' +
            '<div style="display:flex;gap:6px">' +
              '<input type="text" id="pd-keyphrase-input" placeholder="キーフレーズを追加" ' +
                'style="padding:3px 7px;font-size:12px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary);min-width:220px">' +
              '<button type="button" id="pd-keyphrase-add" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">追加</button>' +
            "</div>" +
          "</div>" +

          '<div style="margin-bottom:8px">' +
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">著者フォロー（任意）</div>' +
            '<div id="pd-authors" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px"></div>' +
            '<div style="display:flex;gap:6px">' +
              '<input type="text" id="pd-author-input" placeholder="著者名" ' +
                'style="padding:3px 7px;font-size:12px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary);min-width:200px">' +
              '<button type="button" id="pd-author-add" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">追加</button>' +
            "</div>" +
          "</div>" +

          '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
            '<button type="button" id="pd-search-btn" data-ui-anchor="materials.arxiv-discovery-search" class="admin-action-btn">この条件で検索</button>' +
            '<button type="button" id="pd-subscribe-btn" data-ui-anchor="materials.arxiv-discovery-subscribe" class="admin-action-btn">この条件を保存</button>' +
            '<span id="pd-subscribe-note" style="font-size:11.5px;color:var(--color-text-tertiary)"></span>' +
          "</div>" +
        "</div>" +

        // ② 候補一覧（PD6: 検索条件を常に上に出す）
        '<div id="pd-query-note" style="font-size:11.5px;color:var(--color-text-tertiary);border-bottom:1px solid var(--color-border-tertiary);padding-bottom:6px;margin-bottom:6px"></div>' +
        '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px">' +
          '<label style="font-size:11.5px;color:var(--color-text-secondary);display:flex;align-items:center;gap:4px">' +
            '<input type="checkbox" id="pd-show-dismissed">見送り済みを表示' +
          "</label>" +
          '<span style="font-size:11.5px;color:var(--color-text-tertiary)">' + esc(SOURCE_URL_NOTICE) + "</span>" +
        "</div>" +
        '<div id="pd-results" style="overflow-y:auto;flex:1;min-height:160px"></div>' +

        // ④ 取り込みキュー（Phase 2。開いたとき・登録直後・[更新] のときだけ読む）
        '<details id="pd-queue" data-ui-anchor="materials.arxiv-discovery-queue" style="margin-top:8px;border-top:1px solid var(--color-border-tertiary);padding-top:8px">' +
          '<summary style="font-size:12px;color:var(--color-text-secondary);cursor:pointer">取り込みキュー</summary>' +
          '<div style="display:flex;align-items:center;gap:8px;margin:6px 0">' +
            '<button type="button" id="pd-queue-refresh" data-ui-anchor="materials.arxiv-discovery-queue-refresh" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">更新</button>' +
            '<span style="font-size:11.5px;color:var(--color-text-tertiary)">自動では更新されません。</span>' +
          "</div>" +
          '<div id="pd-queue-list" style="max-height:180px;overflow-y:auto"></div>' +
        "</details>" +

        // ③ 取り込み確認
        '<div style="border-top:1px solid var(--color-border-tertiary);margin-top:10px;padding-top:10px">' +
          '<div id="pd-ingest-summary" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px"></div>' +
          '<div id="pd-ingest-estimate" style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:6px"></div>' +
          '<div id="pd-ingest-result" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px"></div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px">' +
            '<button type="button" id="pd-cancel" class="admin-action-btn" style="background:var(--color-bg-tertiary);color:var(--color-text)">閉じる</button>' +
            '<button type="button" id="pd-ingest-btn" data-ui-anchor="materials.arxiv-discovery-ingest" class="admin-action-btn" disabled>選択した論文を取り込む</button>' +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function openModal() {
    if (!deps) return;
    close();
    state.open = true;
    state.candidates = [];
    state.selected = {};
    state.query = "";
    state.closedWorldNote = "";
    state.total = null;
    state.searched = false;
    // 再オープン時は描き直したチェックボックス（未チェック）と状態を一致させる。
    state.showDismissed = false;
    state.searching = false;
    state.saving = false;
    state.ingesting = false;
    state.domainAllowed = null;
    state.queue = [];
    state.queueLoading = false;
    state.queueError = "";
    state.estimate = null;
    state.estimateRequested = false;

    var overlay = document.createElement("div");
    overlay.id = "paper-discovery-modal";
    overlay.setAttribute("data-ui-anchor", "materials.arxiv-discovery-modal");
    overlay.style.cssText =
      "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML = modalHtml();
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    el("pd-close").addEventListener("click", close);
    el("pd-cancel").addEventListener("click", close);
    el("pd-search-btn").addEventListener("click", runSearch);
    el("pd-subscribe-btn").addEventListener("click", saveSubscription);
    el("pd-ingest-btn").addEventListener("click", runIngest);
    el("pd-category-add").addEventListener("click", addCategoryFromInput);
    el("pd-keyphrase-add").addEventListener("click", addKeyphraseFromInput);
    el("pd-author-add").addEventListener("click", addAuthorFromInput);
    bindEnter("pd-category-input", addCategoryFromInput);
    bindEnter("pd-keyphrase-input", addKeyphraseFromInput);
    bindEnter("pd-author-input", addAuthorFromInput);
    el("pd-domain").addEventListener("change", function () {
      selectDomain(this.value.trim());
    });
    el("pd-show-dismissed").addEventListener("change", function () {
      state.showDismissed = !!this.checked;
      renderCandidates();
    });
    el("pd-queue-refresh").addEventListener("click", function () {
      loadQueue();
    });

    renderChips();
    renderQueryNote();
    renderCandidates();
    renderIngestSummary();
    renderQueue();

    // PD8: 開いたときだけ取得する（ポーリング・自動更新をしない）。
    loadSubscriptions();
    loadDomainOptions();
    checkAllowedDomains();
    loadQueue();
  }

  function bindEnter(inputId, handler) {
    var input = el(inputId);
    if (!input) return;
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        handler();
      }
    });
  }

  // ── 購読条件のロード ──────────────────────────────────────────────────
  function loadSubscriptions() {
    api("/admin/discovery/subscriptions")
      .then(function (res) {
        return res.ok ? res.json() : { subscriptions: [] };
      })
      .then(function (data) {
        state.subscriptions = (data && data.subscriptions) || [];
        fillDomainDatalist();
        if (!state.domainKey && state.subscriptions.length) {
          var input = el("pd-domain");
          if (input) input.value = state.subscriptions[0].domain_key || "";
          selectDomain(state.subscriptions[0].domain_key || "");
        }
      })
      .catch(function () {
        setNotice("購読条件を読み込めませんでした。分野を入力して検索できます。");
      });
  }

  // 分野の候補は既存 API から供給する（自由入力は妨げない — fail-soft）。
  function loadDomainOptions() {
    api("/admin/cartridges")
      .then(function (res) {
        return res.ok ? res.json() : [];
      })
      .then(function (items) {
        var list = items || [];
        for (var i = 0; i < list.length; i++) {
          addDomainOption(list[i] && list[i].cartridge_id, list[i] && list[i].name);
        }
        return api("/admin/library/domains");
      })
      .then(function (res) {
        return res && res.ok ? res.json() : { domains: [] };
      })
      .then(function (data) {
        var domains = (data && data.domains) || [];
        for (var i = 0; i < domains.length; i++) {
          addDomainOption(domains[i] && domains[i].domain_key, null);
        }
      })
      .catch(function () {
        /* 候補提示の失敗は致命的ではない（自由入力で検索できる） */
      });
  }

  function fillDomainDatalist() {
    for (var i = 0; i < state.subscriptions.length; i++) {
      addDomainOption(state.subscriptions[i] && state.subscriptions[i].domain_key, null);
    }
  }

  function addDomainOption(key, label) {
    if (!key) return;
    var datalist = el("pd-domain-options");
    if (!datalist) return;
    var existing = datalist.querySelectorAll("option");
    for (var i = 0; i < existing.length; i++) {
      if (existing[i].value === key) return;
    }
    var option = document.createElement("option");
    option.value = key;
    if (label && label !== key) option.label = label + " (" + key + ")";
    datalist.appendChild(option);
  }

  function findSubscription(domainKey) {
    for (var i = 0; i < state.subscriptions.length; i++) {
      if (state.subscriptions[i] && state.subscriptions[i].domain_key === domainKey) {
        return state.subscriptions[i];
      }
    }
    return null;
  }

  function selectDomain(domainKey) {
    state.domainKey = domainKey || "";
    state.candidates = [];
    state.selected = {};
    state.query = "";
    state.closedWorldNote = "";
    state.total = null;
    state.searched = false;
    setNotice("");

    var subscription = findSubscription(state.domainKey);
    state.categories = subscription ? (subscription.arxiv_categories || []).slice(0) : [];
    state.keyphrases = [];
    if (subscription) {
      var stored = subscription.keyphrases || [];
      for (var i = 0; i < stored.length; i++) {
        var entry = stored[i] || {};
        state.keyphrases.push({
          text: entry.text || "",
          source: entry.source || "manual",
          enabled: entry.enabled !== false
        });
      }
    }
    state.authors = subscription ? (subscription.followed_authors || []).slice(0) : [];

    var meta = el("pd-domain-meta");
    if (meta) {
      meta.textContent = subscription
        ? subscription.last_checked_at
          ? "前回の検索: " + String(subscription.last_checked_at)
          : "保存済みの購読条件を読み込みました。"
        : "この分野の購読条件はまだ保存されていません。";
    }

    renderChips();
    renderQueryNote();
    renderCandidates();
    renderIngestSummary();
    if (state.domainKey) loadKeyphraseCandidates(!subscription);
  }

  // PD3: 分野語彙からの供給。既存の購読条件は書き換えず、新しい候補は
  // 「外れた状態」で足す（教員の操作なしに検索条件が広がらないようにする）。
  function loadKeyphraseCandidates(isNewSubscription) {
    var domainKey = state.domainKey;
    api(
      "/admin/discovery/subscriptions/" +
        encodeURIComponent(domainKey) +
        "/keyphrase-candidates"
    )
      .then(function (res) {
        return res.ok ? res.json() : { candidates: [] };
      })
      .then(function (data) {
        if (state.domainKey !== domainKey) return; // 分野を切り替え済みなら破棄
        var candidates = (data && data.candidates) || [];
        var added = 0;
        for (var i = 0; i < candidates.length; i++) {
          var candidate = candidates[i] || {};
          if (!candidate.text) continue;
          if (indexOfText(state.keyphrases, candidate.text) >= 0) continue;
          state.keyphrases.push({
            text: candidate.text,
            source: candidate.source || "manual",
            enabled: !!isNewSubscription
          });
          added++;
        }
        var note = el("pd-keyphrase-note");
        if (note) {
          if (added && !isNewSubscription) {
            note.textContent = String(added) + KEYPHRASE_NEW_NOTICE_TAIL;
          } else if (added) {
            note.textContent =
              "分野の語彙から " + String(added) + "件のキーフレーズを供給しました。";
          } else {
            note.textContent = "";
          }
        }
        renderChips();
      })
      .catch(function () {
        /* 候補供給の失敗は致命的ではない（手動で足せる） */
      });
  }

  // ── チップ描画 ────────────────────────────────────────────────────────
  function chipStyle(enabled) {
    var base =
      "border:1px solid var(--color-border);border-radius:12px;padding:1px 9px;font-size:11.5px;cursor:pointer;background:var(--color-bg-tertiary);color:var(--color-text-primary)";
    if (enabled) return base;
    // PD3: 外したチップは消さず打ち消し表示で残す。
    return base + ";text-decoration:line-through;opacity:0.55";
  }

  function renderChips() {
    renderCategoryChips();
    renderKeyphraseChips();
    renderAuthorChips();
  }

  function renderCategoryChips() {
    var node = el("pd-categories");
    if (!node) return;
    if (!state.categories.length) {
      node.innerHTML =
        '<span style="font-size:11.5px;color:var(--color-text-tertiary)">カテゴリが未指定です。</span>';
      return;
    }
    var html = "";
    for (var i = 0; i < state.categories.length; i++) {
      html +=
        '<span style="' +
        chipStyle(true) +
        ';display:inline-flex;align-items:center;gap:5px;cursor:default">' +
        esc(state.categories[i]) +
        '<button type="button" class="pd-category-remove" data-value="' +
        esc(state.categories[i]) +
        '" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;padding:0;font-size:12px">&times;</button>' +
        "</span>";
    }
    node.innerHTML = html;
    bindAll(node, ".pd-category-remove", function (button) {
      var index = indexOfText(state.categories, button.getAttribute("data-value"));
      if (index >= 0) state.categories.splice(index, 1);
      renderCategoryChips();
    });
  }

  function renderKeyphraseChips() {
    var node = el("pd-keyphrases");
    if (!node) return;
    if (!state.keyphrases.length) {
      node.innerHTML =
        '<span style="font-size:11.5px;color:var(--color-text-tertiary)">キーフレーズはまだありません。</span>';
      return;
    }
    var html = "";
    for (var i = 0; i < state.keyphrases.length; i++) {
      var entry = state.keyphrases[i] || {};
      var sourceLabel = SOURCE_LABELS[entry.source] || SOURCE_LABELS.manual;
      html +=
        '<button type="button" class="pd-keyphrase-chip" data-value="' +
        esc(entry.text) +
        '" title="' +
        esc(sourceLabel) +
        '" style="' +
        chipStyle(entry.enabled) +
        '">' +
        esc(entry.text) +
        "</button>";
    }
    node.innerHTML = html;
    bindAll(node, ".pd-keyphrase-chip", function (button) {
      var index = indexOfText(state.keyphrases, button.getAttribute("data-value"));
      if (index >= 0) {
        state.keyphrases[index].enabled = !state.keyphrases[index].enabled;
      }
      renderKeyphraseChips();
    });
  }

  function renderAuthorChips() {
    var node = el("pd-authors");
    if (!node) return;
    if (!state.authors.length) {
      node.innerHTML =
        '<span style="font-size:11.5px;color:var(--color-text-tertiary)">著者フォローは未設定です。</span>';
      return;
    }
    var html = "";
    for (var i = 0; i < state.authors.length; i++) {
      html +=
        '<span style="' +
        chipStyle(true) +
        ';display:inline-flex;align-items:center;gap:5px;cursor:default">' +
        esc(state.authors[i]) +
        '<button type="button" class="pd-author-remove" data-value="' +
        esc(state.authors[i]) +
        '" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;padding:0;font-size:12px">&times;</button>' +
        "</span>";
    }
    node.innerHTML = html;
    bindAll(node, ".pd-author-remove", function (button) {
      var index = indexOfText(state.authors, button.getAttribute("data-value"));
      if (index >= 0) state.authors.splice(index, 1);
      renderAuthorChips();
    });
  }

  function bindAll(root, selector, handler) {
    var nodes = root.querySelectorAll(selector);
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].addEventListener("click", function () {
        handler(this);
      });
    }
  }

  function addCategoryFromInput() {
    var input = el("pd-category-input");
    var value = input ? input.value.trim() : "";
    if (!value) return;
    if (indexOfText(state.categories, value) < 0) state.categories.push(value);
    if (input) input.value = "";
    renderCategoryChips();
  }

  function addKeyphraseFromInput() {
    var input = el("pd-keyphrase-input");
    var value = input ? input.value.trim() : "";
    if (!value) return;
    var index = indexOfText(state.keyphrases, value);
    if (index >= 0) {
      state.keyphrases[index].enabled = true;
    } else {
      state.keyphrases.push({ text: value, source: "manual", enabled: true });
    }
    if (input) input.value = "";
    renderKeyphraseChips();
  }

  function addAuthorFromInput() {
    var input = el("pd-author-input");
    var value = input ? input.value.trim() : "";
    if (!value) return;
    if (indexOfText(state.authors, value) < 0) state.authors.push(value);
    if (input) input.value = "";
    renderAuthorChips();
  }

  // ── 許可ドメインの確認（UF1 継承の補助表示）────────────────────────────
  function checkAllowedDomains() {
    api("/admin/url-fetch-domains")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        var domains = (data && data.domains) || [];
        var allowed = false;
        for (var i = 0; i < domains.length; i++) {
          var name = String((domains[i] && domains[i].domain) || "").toLowerCase();
          // ドット境界で照合する（"myarxiv.org" を許可扱いにしない）。
          if (
            name === "arxiv.org" ||
            name.lastIndexOf(".arxiv.org") === name.length - ".arxiv.org".length
          ) {
            allowed = true;
          }
        }
        state.domainAllowed = allowed;
        renderIngestSummary();
      })
      .catch(function () {
        state.domainAllowed = null;
        renderIngestSummary();
      });
  }

  // ── 検索 ──────────────────────────────────────────────────────────────
  function runSearch() {
    if (state.searching) return;
    var input = el("pd-domain");
    var domainKey = input ? input.value.trim() : state.domainKey;
    if (!domainKey) {
      setNotice("分野を入力してください。", true);
      return;
    }
    if (domainKey !== state.domainKey) selectDomain(domainKey);

    state.searching = true;
    setNotice("arXiv を検索しています...");
    var button = el("pd-search-btn");
    if (button) button.disabled = true;

    api("/admin/discovery/search", {
      method: "POST",
      body: JSON.stringify({
        domain_key: state.domainKey,
        categories: state.categories,
        keyphrases: enabledKeyphrases()
      })
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.searching = false;
        if (button) button.disabled = false;
        state.candidates = (data && data.candidates) || [];
        state.query = (data && data.query) || "";
        state.closedWorldNote = (data && data.closed_world_note) || "";
        state.total = data && typeof data.total === "number" ? data.total : null;
        state.searched = true;
        state.selected = {};
        setNotice("");
        renderQueryNote();
        renderCandidates();
        renderIngestSummary();
      })
      .catch(function (err) {
        state.searching = false;
        if (button) button.disabled = false;
        setNotice(detailText(err, "検索に失敗しました。"), true);
      });
  }

  // PD6: 何をどう検索した結果なのかを一覧の上に常時出す。
  function renderQueryNote() {
    var node = el("pd-query-note");
    if (!node) return;
    var parts = [];
    if (state.query) {
      parts.push("検索条件: " + state.query);
    } else if (state.searched) {
      // 条件ゼロのときサーバは arXiv を呼ばず query="" を返す。空の理由を正直に書く。
      parts.push("検索条件: 指定されていません（カテゴリまたはキーフレーズを指定してください）。");
    } else {
      parts.push("検索条件: まだ検索していません。");
    }
    if (state.total !== null) parts.push("該当件数: " + String(state.total));
    if (state.closedWorldNote) parts.push(state.closedWorldNote);
    node.textContent = parts.join(" ／ ");
  }

  // ── 候補一覧 ──────────────────────────────────────────────────────────
  function candidateCardHtml(candidate) {
    var arxivId = (candidate && candidate.arxiv_id) || "";
    var status = (candidate && candidate.status) || "new";
    var authors = (candidate && candidate.authors) || [];
    var categories = (candidate && candidate.categories) || [];
    var matched = (candidate && candidate.matched_keyphrases) || [];
    var meta = [];
    if (authors.length) meta.push(authors.join(", "));
    if (candidate && candidate.published) meta.push(String(candidate.published));
    if (categories.length) meta.push(categories.join(" / "));

    var html =
      '<div class="pd-candidate" data-arxiv-id="' +
      esc(arxivId) +
      '" style="border-bottom:1px solid var(--color-border-tertiary);padding:8px 0">' +
      '<div style="display:flex;gap:8px;align-items:flex-start">';

    if (status === "new") {
      html +=
        '<input type="checkbox" class="pd-select" data-arxiv-id="' +
        esc(arxivId) +
        '"' +
        (state.selected[arxivId] ? " checked" : "") +
        ' style="margin-top:3px">';
    } else {
      html += '<span style="width:13px"></span>';
    }

    html +=
      '<div style="flex:1;min-width:0">' +
      '<div style="font-size:13px;color:var(--color-text-primary)">' +
      esc((candidate && candidate.title) || arxivId) +
      "</div>" +
      '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
      esc(meta.join(" ・ ")) +
      "</div>";

    if (matched.length) {
      // なぜ候補なのかを1行で言う（ブラックボックスのおすすめにしない・数値は出さない）。
      html +=
        '<div class="pd-matched" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:2px">一致: ' +
        esc(matched.join(" ・ ")) +
        "</div>";
    }

    if (status === "ingested") {
      html +=
        '<div class="pd-status-label" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">取り込み済み</div>';
    } else if (status === "dismissed") {
      html +=
        '<div style="margin-top:4px;display:flex;gap:6px;align-items:center">' +
        '<span style="font-size:11.5px;color:var(--color-text-tertiary)">見送り済み</span>' +
        '<button type="button" class="pd-restore-btn admin-action-btn" data-arxiv-id="' +
        esc(arxivId) +
        '" style="font-size:11px;padding:1px 7px">戻す</button>' +
        "</div>";
    } else {
      html +=
        '<div style="margin-top:4px;display:flex;gap:6px">' +
        '<button type="button" class="pd-dismiss-btn admin-action-btn" data-arxiv-id="' +
        esc(arxivId) +
        '" style="font-size:11px;padding:1px 7px">見送る</button>' +
        (candidate && candidate.abs_url
          ? '<a href="' +
            esc(candidate.abs_url) +
            '" target="_blank" rel="noopener noreferrer" style="font-size:11px;color:var(--color-accent, #2563eb)">arXiv で開く</a>'
          : "") +
        "</div>";
    }

    if (candidate && candidate.summary) {
      html +=
        '<details style="margin-top:4px"><summary style="font-size:11.5px;color:var(--color-text-secondary);cursor:pointer">要旨</summary>' +
        '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:3px;white-space:pre-wrap">' +
        esc(candidate.summary) +
        "</div></details>";
    }

    html += "</div></div></div>";
    return html;
  }

  function visibleCandidates() {
    var out = [];
    for (var i = 0; i < state.candidates.length; i++) {
      var candidate = state.candidates[i] || {};
      if (candidate.status === "dismissed" && !state.showDismissed) continue;
      out.push(candidate);
    }
    return out;
  }

  function renderCandidates() {
    var node = el("pd-results");
    if (!node) return;
    var visible = visibleCandidates();
    if (!visible.length) {
      node.innerHTML =
        '<div id="pd-empty-note" style="font-size:12.5px;color:var(--color-text-tertiary);padding:8px 0">' +
        esc(state.searched ? EMPTY_RESULT_NOTICE : "分野と条件を指定して「この条件で検索」を押してください。") +
        "</div>";
      return;
    }
    var html = "";
    for (var i = 0; i < visible.length; i++) {
      html += candidateCardHtml(visible[i]);
    }
    node.innerHTML = html;

    bindAll(node, ".pd-select", function (input) {
      var arxivId = input.getAttribute("data-arxiv-id");
      if (input.checked) state.selected[arxivId] = true;
      else delete state.selected[arxivId];
      renderIngestSummary();
    });
    bindAll(node, ".pd-dismiss-btn", function (button) {
      setDismissed(button.getAttribute("data-arxiv-id"), true, button);
    });
    bindAll(node, ".pd-restore-btn", function (button) {
      setDismissed(button.getAttribute("data-arxiv-id"), false, button);
    });
  }

  function findCandidate(arxivId) {
    for (var i = 0; i < state.candidates.length; i++) {
      if (state.candidates[i] && state.candidates[i].arxiv_id === arxivId) {
        return state.candidates[i];
      }
    }
    return null;
  }

  // PD5/P4: 見送りは行削除ではなく状態遷移。復帰も同じ経路で行う。
  function setDismissed(arxivId, dismissed, button) {
    if (!arxivId || !state.domainKey) return;
    if (button) button.disabled = true;
    api(dismissed ? "/admin/discovery/dismiss" : "/admin/discovery/restore", {
      method: "POST",
      body: JSON.stringify({ domain_key: state.domainKey, arxiv_id: arxivId })
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json().catch(function () {
          return {};
        });
      })
      .then(function () {
        var candidate = findCandidate(arxivId);
        if (candidate) candidate.status = dismissed ? "dismissed" : "new";
        if (dismissed) delete state.selected[arxivId];
        setNotice("");
        renderCandidates();
        renderIngestSummary();
      })
      .catch(function (err) {
        if (button) button.disabled = false;
        setNotice(
          detailText(err, dismissed ? "見送りを記録できませんでした。" : "見送りを戻せませんでした。"),
          true
        );
      });
  }

  // ── 購読条件の保存 ────────────────────────────────────────────────────
  function saveSubscription() {
    if (state.saving) return;
    var input = el("pd-domain");
    var domainKey = input ? input.value.trim() : state.domainKey;
    if (!domainKey) {
      setNotice("分野を入力してください。", true);
      return;
    }
    if (domainKey !== state.domainKey) state.domainKey = domainKey;

    state.saving = true;
    var button = el("pd-subscribe-btn");
    if (button) button.disabled = true;
    var note = el("pd-subscribe-note");
    if (note) note.textContent = "保存しています...";

    api("/admin/discovery/subscriptions/" + encodeURIComponent(state.domainKey), {
      method: "PUT",
      body: JSON.stringify({
        arxiv_categories: state.categories,
        keyphrases: state.keyphrases,
        followed_authors: state.authors
      })
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.saving = false;
        if (button) button.disabled = false;
        var saved = data && data.subscription;
        if (saved) {
          var existing = findSubscription(saved.domain_key);
          if (existing) {
            state.subscriptions[
              state.subscriptions.indexOf(existing)
            ] = saved;
          } else {
            state.subscriptions.push(saved);
            addDomainOption(saved.domain_key, null);
          }
        }
        if (note) note.textContent = "この分野の購読条件を保存しました。";
      })
      .catch(function (err) {
        state.saving = false;
        if (button) button.disabled = false;
        if (note) note.textContent = "";
        setNotice(detailText(err, "購読条件を保存できませんでした。"), true);
      });
  }

  // ── 取り込み ──────────────────────────────────────────────────────────
  function selectedIds() {
    var out = [];
    for (var key in state.selected) {
      if (Object.prototype.hasOwnProperty.call(state.selected, key) && state.selected[key]) {
        out.push(key);
      }
    }
    return out;
  }

  // 5件以下は同期取り込み、6件以上はキュー登録（Phase 2）。境界を1箇所に閉じる。
  function usesBatchIngest(count) {
    return count > SYNC_INGEST_MAX;
  }

  // PD1: 選択件数と「何が起きるか」を必ず出してから実行させる。
  // 経路（同期 / キュー）で起きることが違うので事実文も切り替える。
  function renderIngestSummary() {
    var summary = el("pd-ingest-summary");
    var button = el("pd-ingest-btn");
    if (!summary || !button) return;
    var ids = selectedIds();
    var overLimit = ids.length > BATCH_INGEST_MAX;
    var lines = [];
    if (overLimit) {
      lines.push(
        String(ids.length) +
          "件が選択されています。" +
          BATCH_LIMIT_NOTICE_HEAD +
          String(BATCH_INGEST_MAX) +
          BATCH_LIMIT_NOTICE_TAIL
      );
    } else if (usesBatchIngest(ids.length)) {
      lines.push(String(ids.length) + BATCH_NOTICE_TAIL);
    } else if (ids.length) {
      lines.push(String(ids.length) + INGEST_NOTICE_TAIL);
    } else {
      lines.push("取り込む論文を選択してください。");
    }
    if (state.domainAllowed === false) {
      lines.push(DOMAIN_BLOCKED_NOTICE);
    } else if (state.domainAllowed === null) {
      lines.push(DOMAIN_UNKNOWN_NOTICE);
    }
    summary.textContent = lines.join(" ");
    button.disabled =
      state.ingesting || !ids.length || overLimit || state.domainAllowed === false;

    // 見積りは「取り込むつもりがある」ときだけ取りに行く（1回だけ・キャッシュ）。
    if (ids.length) ensureEstimate();
    renderEstimateLine();
  }

  // ── 事前見積り（U層のレンジ表示の流儀。金額なし・reported/estimated 非合算）──
  function ensureEstimate() {
    if (state.estimateRequested) return;
    state.estimateRequested = true;
    api("/admin/discovery/ingest-estimate")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        state.estimate = data || null;
        renderEstimateLine();
      })
      .catch(function () {
        // fail-soft: 見積りが取れないときは行ごと出さない（取り込みは止めない）。
        state.estimate = null;
        renderEstimateLine();
      });
  }

  // U1: reported（実測）と estimated（推計）を合算せず、あるものだけ並べる。
  function estimateBucketTexts(perDocument) {
    var buckets = [
      { key: "reported", label: "実測(reported) " },
      { key: "estimated", label: "推計(estimated) " }
    ];
    var out = [];
    for (var i = 0; i < buckets.length; i++) {
      var bucket = perDocument ? perDocument[buckets[i].key] : null;
      var range = bucket && bucket.total_tokens_range;
      if (!range || range.length !== 2 || range[0] == null || range[1] == null) continue;
      out.push(
        buckets[i].label + String(range[0]) + " 〜 " + String(range[1]) + " トークン"
      );
    }
    return out;
  }

  function renderEstimateLine() {
    var node = el("pd-ingest-estimate");
    if (!node) return;
    var count = selectedIds().length;
    var data = state.estimate;
    if (!count || !data) {
      node.textContent = "";
      return;
    }
    if (data.available === false) {
      // 実績がまだ無いことをサーバの事実文のまま出す（推測で埋めない）。
      node.textContent = data.note ? String(data.note) : "";
      return;
    }
    var texts = estimateBucketTexts(data.per_document);
    if (!texts.length) {
      node.textContent = "";
      return;
    }
    var line = ESTIMATE_LINE_HEAD + texts.join(" ／ ") + " × " + String(count) + "件";
    if (data.basis_note) line += "（" + String(data.basis_note) + "）";
    node.textContent = line;
  }

  function runIngest() {
    if (state.ingesting) return;
    var button = el("pd-ingest-btn");
    if (button && button.disabled) return;
    var ids = selectedIds();
    if (!ids.length) return;
    if (ids.length > BATCH_INGEST_MAX) {
      // サーバも 422 で拒否する。ここでは先回りして事実文を出すだけ。
      setNotice(
        BATCH_LIMIT_NOTICE_HEAD + String(BATCH_INGEST_MAX) + BATCH_LIMIT_NOTICE_TAIL,
        true
      );
      return;
    }
    var batch = usesBatchIngest(ids.length);

    var items = [];
    for (var i = 0; i < ids.length; i++) {
      var candidate = findCandidate(ids[i]);
      var entry = { arxiv_id: ids[i] };
      // キュー行の表示に使うタイトルだけ添える（候補は保存しない — PD5）。
      if (batch && candidate && candidate.title) entry.title = candidate.title;
      items.push(entry);
    }
    var payload = uploadOptions();
    payload.items = items;
    if (batch && state.domainKey) payload.domain_key = state.domainKey;

    state.ingesting = true;
    if (button) button.disabled = true;
    var result = el("pd-ingest-result");
    if (result) result.textContent = batch ? "キューに登録しています..." : "取得しています...";
    setNotice("");

    api(batch ? "/admin/discovery/ingest-batch" : "/admin/discovery/ingest", {
      method: "POST",
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.ingesting = false;
        if (batch) handleBatchResult(data || {});
        else handleIngestResult(data || {});
        renderIngestSummary();
      })
      .catch(function (err) {
        state.ingesting = false;
        if (result) result.textContent = "";
        // 422（件数上限・許可ドメイン未設定）はサーバの事実文をそのまま見せる。
        setNotice(detailText(err, "取り込みに失敗しました。"), true);
        renderIngestSummary();
      });
  }

  function handleIngestResult(data) {
    var accepted = data.accepted || [];
    var failed = data.failed || [];
    var lines = [];

    for (var i = 0; i < accepted.length; i++) {
      var item = accepted[i] || {};
      var arxivId = item.arxiv_id || "";
      // PD2: 受理後は既存アップロードと同じ合流点へ渡す（第2のポーリングを作らない）。
      if (deps && deps.onUploadAccepted) {
        deps.onUploadAccepted(item, item.filename || item.title || arxivId);
      }
      var candidate = findCandidate(arxivId);
      // 直前に取り込んだ事実の反映（サーバ側の判定は次回検索で読み時導出される）。
      if (candidate) candidate.status = "ingested";
      delete state.selected[arxivId];
    }

    if (accepted.length) {
      lines.push(
        String(accepted.length) +
          "件の取り込みを開始しました。進捗は教材一覧で確認できます。"
      );
    }
    for (var j = 0; j < failed.length; j++) {
      var failure = failed[j] || {};
      // サーバの detail を独自文で上書きしない。
      lines.push(
        (failure.arxiv_id || "") + ": " + (failure.detail || "取り込みに失敗しました。")
      );
    }
    if (!lines.length) lines.push("取り込まれた論文はありませんでした。");

    var result = el("pd-ingest-result");
    if (result) result.textContent = lines.join(" ／ ");
    renderCandidates();
  }

  // キュー投入は「取り込み完了」ではない。候補行の status をローカルで
  // "ingested" に書き換えない（PD6: 起きていないことを起きたように見せない）。
  // 二重登録を防ぐためにチェックだけ外す。
  function handleBatchResult(data) {
    var queued = data.queued || [];
    var skipped = data.skipped || [];
    var lines = [];

    for (var i = 0; i < queued.length; i++) {
      var item = queued[i] || {};
      if (item.arxiv_id) delete state.selected[item.arxiv_id];
    }

    if (queued.length) {
      lines.push(String(queued.length) + BATCH_QUEUED_NOTICE_TAIL);
    }
    for (var j = 0; j < skipped.length; j++) {
      var skip = skipped[j] || {};
      // サーバの detail を独自文で上書きしない。
      lines.push(
        (skip.arxiv_id || "") + ": " + (skip.detail || "キューに登録できませんでした。")
      );
    }
    // notice（許可ドメイン未設定など）は存在すれば必ず出す。
    if (data.notice) lines.push(String(data.notice));
    if (!lines.length) lines.push("キューに登録された論文はありませんでした。");

    var result = el("pd-ingest-result");
    if (result) result.textContent = lines.join(" ／ ");
    renderCandidates();

    var details = el("pd-queue");
    if (details) details.open = true;
    loadQueue();
  }

  // ── 取り込みキュー（PD8: 開いたとき・登録直後・[更新] のときだけ読む）─────
  function loadQueue() {
    if (state.queueLoading) return;
    state.queueLoading = true;
    state.queueError = "";
    renderQueue();

    // 分野で絞らない（開いたときと [更新] で中身が変わらないようにする。
    // 行ごとに domain_key を出すので、どの分野の項目かは行で分かる）。
    api("/admin/discovery/ingest-queue")
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.queueLoading = false;
        state.queue = (data && data.items) || [];
        renderQueue();
      })
      .catch(function (err) {
        state.queueLoading = false;
        state.queueError = detailText(err, QUEUE_LOAD_ERROR);
        renderQueue();
      });
  }

  function queueRowHtml(item) {
    var status = (item && item.status) || "";
    var label = QUEUE_STATUS_LABELS[status] || status;
    var title = (item && item.title) || (item && item.arxiv_id) || "";
    var meta = [];
    if (item && item.arxiv_id) meta.push(item.arxiv_id);
    if (item && item.domain_key) meta.push(item.domain_key);
    var html =
      '<div class="pd-queue-row" style="border-bottom:1px solid var(--color-border-tertiary);padding:6px 0">' +
      '<div style="font-size:12.5px;color:var(--color-text-primary)">' +
      esc(title) +
      "</div>" +
      '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
      esc(label) +
      (meta.length ? " ・ " + esc(meta.join(" ・ ")) : "") +
      "</div>";

    if (status === "failed") {
      html +=
        '<div style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc((item && item.detail) || "取り込みに失敗しました。") +
        "</div>" +
        '<div style="margin-top:4px">' +
        '<button type="button" class="pd-queue-retry admin-action-btn" data-item-id="' +
        esc((item && item.item_id) || "") +
        '" style="font-size:11px;padding:1px 7px">再試行</button>' +
        "</div>";
    } else if (item && item.detail) {
      html +=
        '<div style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(item.detail) +
        "</div>";
    }

    return html + "</div>";
  }

  function renderQueue() {
    var node = el("pd-queue-list");
    if (!node) return;
    if (state.queueLoading) {
      node.innerHTML =
        '<div style="font-size:11.5px;color:var(--color-text-tertiary)">読み込んでいます...</div>';
      return;
    }
    if (state.queueError) {
      node.innerHTML =
        '<div style="font-size:11.5px;color:var(--color-text-danger, #e53935)">' +
        esc(state.queueError) +
        "</div>";
      return;
    }
    if (!state.queue.length) {
      node.innerHTML =
        '<div id="pd-queue-empty" style="font-size:11.5px;color:var(--color-text-tertiary)">' +
        esc(QUEUE_EMPTY_NOTICE) +
        "</div>";
      return;
    }
    var html = "";
    for (var i = 0; i < state.queue.length; i++) {
      html += queueRowHtml(state.queue[i] || {});
    }
    node.innerHTML = html;
    bindAll(node, ".pd-queue-retry", function (button) {
      retryQueueItem(button.getAttribute("data-item-id"), button);
    });
  }

  // P4/PD1: 失敗行は消さず保持し、リトライは教員の明示操作のみ。
  function retryQueueItem(itemId, button) {
    if (!itemId) return;
    if (button) button.disabled = true;
    api("/admin/discovery/ingest-queue/" + encodeURIComponent(itemId) + "/retry", {
      method: "POST"
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        var updated = data && data.item;
        if (updated) {
          for (var i = 0; i < state.queue.length; i++) {
            if (state.queue[i] && state.queue[i].item_id === updated.item_id) {
              state.queue[i] = updated;
            }
          }
        }
        setNotice("");
        renderQueue();
      })
      .catch(function (err) {
        if (button) button.disabled = false;
        // 422（failed 以外）はサーバの事実文をそのまま見せる。
        setNotice(detailText(err, "再試行できませんでした。"), true);
      });
  }

  window.PaperDiscovery = {
    init: function (injected) {
      deps = injected || null;
    },
    openModal: openModal,
    close: close
  };
})();
