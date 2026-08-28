/**
 * 論文レーダー（教材起点の類似論文探索と比較分析）— 教材管理タブの行メニュー
 * 「📡 近い論文を探す…」から開くモーダル。
 *
 * 正本: docs/features/paper_radar_design.md（不変条項 PR1〜PR8。PD1〜PD8 を継承）。
 *
 *   PR1 起点は教材1件・候補は読み時導出（このモジュールも候補を保存・キャッシュしない）
 *   PR2 距離は段階ラベルのみ。サーバが付けた distance_label をそのまま描き、
 *       クライアントに閾値表・数値→ラベルの変換表を持たない。ラベルの無い候補は
 *       「距離を判定できませんでした」の区画へ正直に分ける（最遠帯に化けさせない）
 *   PR3 取り込みは既存の弁のみ（/admin/discovery/ingest と /ingest-batch）
 *   PR4 比較文は AI の推定・非保存。出所ラベルとサーバ固定の注意書き（caveat）を
 *       そのまま出す（クライアントで書き換えない・独自の注意書きを発明しない）
 *   PR5 教員の明示操作のみ（検索も比較もボタン押下時だけ。ポーリング・自動検索なし）
 *   PR7 閉世界の正直さ（検索条件と closed_world_note を候補一覧の上に常時出す）
 *
 * ES5 で書く（開発ルール5。admin.js / admin-paper-discovery.js と同じ流儀）。
 * DI は init(deps)。deps = { apiFetch, escHtml, onUploadAccepted }。
 */
(function () {
  "use strict";

  var deps = null;

  // PD3: 供給元の日本語ラベル（キーフレーズチップの title 属性に出す）。
  var SOURCE_LABELS = {
    skeleton: "分野の地図の概念から",
    cartridge: "カートリッジ語彙から",
    component: "承認済み理論部品から",
    manual: "手動"
  };

  // 距離の選択肢。value はサーバの語彙（RADAR_DISTANCES）、label は選択肢の説明文。
  // ここにあるのは「教員が何を選ぶか」の文言であって、候補に付く帯ラベルではない。
  // 帯ラベル（distance_label）はサーバの label_vocab が正本で、クライアントは
  // 受け取った文字列をそのまま描くだけ（PR2）。
  var DISTANCE_OPTIONS = [
    { value: "near", label: "近い（テーマも近い）" },
    { value: "mid", label: "中間" },
    { value: "far", label: "同じ分野の別テーマ" }
  ];

  // seed のカテゴリがどこから来たかを必ず1行で言う（判定不能を偽装しない — PD6）。
  var CATEGORY_SOURCE_NOTICES = {
    arxiv: "arXiv のカテゴリから取得",
    arxiv_inferred:
      "ファイル名から推定した arXiv 情報です（教材の出所としては未登録）。",
    subscription: "分野購読の条件から取得",
    manual: "カテゴリを入力してください"
  };
  var NO_ARXIV_NOTICE =
    "この教材は arXiv 由来として登録されていないため、カテゴリを指定してください。";

  // arXiv 出所の後付け登録（3段階）。推定はあくまで推定として言い、
  // タイトルが一致しないときは教員に両方を見せてから確認を取る（偽装しない）。
  var PROV_INFERRED_HEAD = "ファイル名から arXiv-";
  var PROV_TITLE_MATCH_TAIL = " と推定し、タイトルが一致しました。";
  var PROV_NOT_FETCHED_TAIL =
    " と推定しましたが、arXiv から論文情報を取得できませんでした。";
  var PROV_MISMATCH_NOTICE =
    " と推定しましたが、タイトルが一致しませんでした。内容を確認してください。";
  var PROV_ARXIV_TITLE_HEAD = "arXiv の論文: ";
  var PROV_DOCUMENT_TITLE_HEAD = "この教材のタイトル: ";
  var PROV_REGISTER_LABEL = "この論文として登録する";
  var PROV_REGISTERED_NOTICE = "この教材の出所として登録しました。";
  var PROV_REGISTER_FAILED_NOTICE = "出所として登録できませんでした。";
  var PROV_CONFIRM_TAIL =
    "この教材の出所として登録しますか？（タイトルは一致していません）";

  // PD1: 取り込み前に必ず出す事実文（何が起きるかを省略しない）。
  // 分野購読モーダルと同一の文言・同一の境界（相互 import できないので同型に書く）。
  var INGEST_NOTICE_TAIL =
    "件の論文を取得し、解析パイプラインを実行します。解析には LLM を使用します。" +
    "解析結果は候補として保存され、公開するまで学習者には表示されません。";
  var SYNC_INGEST_MAX = 5;
  var BATCH_INGEST_MAX = 50;
  var BATCH_NOTICE_TAIL =
    "件をキューに登録します。サーバが順に取得・解析します（1件ずつ・間隔をあけて実行）。" +
    "進捗は教材一覧と分野購読モーダルの取り込みキュー欄で確認できます。解析には LLM を使用します。" +
    "解析結果は候補として保存され、公開するまで学習者には表示されません。";
  var BATCH_LIMIT_NOTICE_HEAD = "一度にキューへ登録できるのは ";
  var BATCH_LIMIT_NOTICE_TAIL = " 件までです。選択を減らしてください。";
  var BATCH_QUEUED_NOTICE_TAIL =
    "件をキューに登録しました。進捗は教材一覧で確認できます。";

  // UF1 継承: 許可ドメイン未設定は fail-closed の表示（強制はサーバ側）。
  var DOMAIN_BLOCKED_NOTICE =
    "取得先ドメインが許可されていません。システム管理者が「AIモデル」タブで設定できます。";
  var DOMAIN_UNKNOWN_NOTICE =
    "許可ドメインを確認できませんでした。取り込みはサーバ側で拒否される場合があります。";

  // PD6 / PR7: 空一覧を「この論文に近い論文が無い」と読ませない。
  var EMPTY_RESULT_NOTICE =
    "この検索条件では候補が見つかりませんでした。条件を変えると別の論文が見つかることがあります。";
  var NOT_SEARCHED_NOTICE =
    "距離と条件を確認して「この条件で検索」を押してください。";
  var SOURCE_URL_NOTICE =
    "「取り込み済み」はURL経由で取り込まれた論文のみ判定できます。";

  // PR2: 帯の見出し。帯ラベルそのものはサーバの文字列を差し込むだけ。
  var DISTANCE_CHIP_HEAD = "距離: ";
  var OTHER_BAND_HEAD = "他の距離の候補 ";
  var UNMEASURED_HEAD = "距離を判定できませんでした";
  var BANDING_UNAVAILABLE_NOTICE =
    "距離の判定はできませんでした。候補を新着順のまま表示しています。";

  // PR4: 比較分析。見出しに「AI 推定」と明記し、caveat はサーバの文字列をそのまま出す。
  var COMPARE_MAX = 10;
  var COMPARE_HEAD = "起点論文との違い（AI 推定）";
  var COMPARE_COMMON_HEAD = "共通点: ";
  var COMPARE_QUOTE_HEAD = "引用: ";
  var COMPARE_EMPTY_NOTICE = "比較できる違いは返されませんでした。";
  var COMPARE_NONE_SELECTED_NOTICE =
    "比較する論文を選択してください（チェックボックスで選びます）。";
  var COMPARE_LIMIT_NOTICE_HEAD = "一度に比較できるのは ";
  var COMPARE_LIMIT_NOTICE_TAIL = " 件までです。選択を減らしてください。";

  var state = {
    open: false,
    documentId: "",
    title: "",
    seed: null,
    seedError: "",
    // arXiv 出所の後付け登録。推定は自動で1回だけ試し（タイトル一致時のみ）、
    // 一致しないときは教員の明示確認を経てからでないと登録しない。
    provenance: null,
    provenanceAutoAttempted: false,
    provenanceNotice: "",
    distance: "near",
    categories: [],
    categoriesSource: "",
    keyphrases: [],
    candidates: [],
    query: "",
    closedWorldNote: "",
    total: null,
    banding: null,
    searched: false,
    searching: false,
    // 教員が開いた帯は開いたまま保つ（比較結果の描き直しで畳まない）。
    openBands: {},
    selected: {},
    ingesting: false,
    domainAllowed: null,
    // 比較結果はレスポンス限り（DB に保存しない — PR4）。モーダルを閉じれば消える。
    comparing: false,
    compareById: {},
    compareNotes: []
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
    var node = el("pr-notice");
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

  function bindAll(root, selector, handler) {
    var nodes = root.querySelectorAll(selector);
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].addEventListener("click", function () {
        handler(this);
      });
    }
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

  function seedDomainKey() {
    return (state.seed && state.seed.domain_key) || "";
  }

  // ── モーダルの骨組み ──────────────────────────────────────────────────
  function close() {
    var overlay = el("paper-radar-modal");
    if (overlay) overlay.remove();
    state.open = false;
  }

  function distanceRadiosHtml() {
    var html = "";
    for (var i = 0; i < DISTANCE_OPTIONS.length; i++) {
      var option = DISTANCE_OPTIONS[i];
      html +=
        '<label style="font-size:12px;color:var(--color-text-primary);display:inline-flex;align-items:center;gap:4px">' +
        '<input type="radio" name="pr-distance-choice" class="pr-distance-choice" value="' +
        esc(option.value) +
        '"' +
        (option.value === state.distance ? " checked" : "") +
        ">" +
        esc(option.label) +
        "</label>";
    }
    return html;
  }

  function modalHtml() {
    return (
      // overflow-y:auto は固定区画（seed・検索条件・フッター）の合計が 88vh を超える
      // 低い画面でのフォールバック。通常は #pr-results の内部スクロールだけが効く。
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:640px;max-width:860px;width:88vw;max-height:88vh;display:flex;flex-direction:column;overflow-y:auto">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">近い論文を探す</h3>' +
          '<button type="button" id="pr-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        "</div>" +
        '<div id="pr-notice" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:8px"></div>' +

        // ① seed ヘッダ（起点の論文が何かを常に見せる）
        '<div id="pr-seed" style="border:1px solid var(--color-border-tertiary);border-radius:6px;padding:8px 10px;margin-bottom:10px"></div>' +

        // ② 距離セレクタ + ③ 検索条件
        '<div style="border:1px solid var(--color-border-tertiary);border-radius:6px;padding:10px;margin-bottom:10px">' +
          '<div id="pr-distance" data-ui-anchor="materials.radar-distance" style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:8px">' +
            '<span style="font-size:12px;color:var(--color-text-secondary)">距離</span>' +
            distanceRadiosHtml() +
            '<span style="font-size:11.5px;color:var(--color-text-tertiary)">距離は次の検索から適用されます。</span>' +
          "</div>" +

          '<div style="margin-bottom:8px">' +
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">arXiv カテゴリ</div>' +
            '<div id="pr-categories" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px"></div>' +
            '<div id="pr-category-source" style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:4px"></div>' +
            '<div style="display:flex;gap:6px">' +
              '<input type="text" id="pr-category-input" placeholder="astro-ph.CO" ' +
                'style="padding:3px 7px;font-size:12px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary);min-width:170px">' +
              '<button type="button" id="pr-category-add" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">追加</button>' +
            "</div>" +
          "</div>" +

          // キーフレーズは「近い」でのみ効く条件（mid / far はカテゴリだけで網を張る）。
          '<div id="pr-keyphrase-section" style="margin-bottom:8px">' +
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">キーフレーズ<span style="color:var(--color-text-tertiary);font-size:11px">（クリックで外す/戻す。外したものも条件として保持されます）</span></div>' +
            '<div id="pr-keyphrases" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px"></div>' +
            '<div style="display:flex;gap:6px">' +
              '<input type="text" id="pr-keyphrase-input" placeholder="キーフレーズを追加" ' +
                'style="padding:3px 7px;font-size:12px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary);min-width:220px">' +
              '<button type="button" id="pr-keyphrase-add" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">追加</button>' +
            "</div>" +
          "</div>" +

          '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
            '<button type="button" id="pr-search-btn" data-ui-anchor="materials.radar-search" class="admin-action-btn">この条件で検索</button>' +
            '<span style="font-size:11.5px;color:var(--color-text-tertiary)">この画面から分野購読の条件は変更されません。</span>' +
          "</div>" +
        "</div>" +

        // ④ 候補一覧（PR7: 検索条件と閉世界注記を常に上に出す）
        '<div id="pr-query-note" style="font-size:11.5px;color:var(--color-text-tertiary);padding-bottom:4px"></div>' +
        '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px">' +
          '<button type="button" id="pr-compare-btn" data-ui-anchor="materials.radar-compare" class="admin-action-btn" style="font-size:11.5px;padding:1px 8px" disabled>違いを分析</button>' +
          '<span id="pr-compare-note" style="font-size:11.5px;color:var(--color-text-tertiary)"></span>' +
        "</div>" +
        '<div style="font-size:11.5px;color:var(--color-text-tertiary);border-bottom:1px solid var(--color-border-tertiary);padding-bottom:6px;margin-bottom:6px">' +
          esc(SOURCE_URL_NOTICE) +
        "</div>" +
        '<div id="pr-results" style="overflow-y:auto;flex:1;min-height:160px"></div>' +

        // ⑤ 取り込み確認
        '<div style="border-top:1px solid var(--color-border-tertiary);margin-top:10px;padding-top:10px">' +
          '<div id="pr-ingest-summary" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px"></div>' +
          '<div id="pr-ingest-result" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px"></div>' +
          '<div style="display:flex;justify-content:flex-end;gap:8px">' +
            '<button type="button" id="pr-cancel" class="admin-action-btn" style="background:var(--color-bg-tertiary);color:var(--color-text)">閉じる</button>' +
            '<button type="button" id="pr-ingest-btn" data-ui-anchor="materials.radar-ingest" class="admin-action-btn" disabled>選択した論文を取り込む</button>' +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function openModal(documentId, title) {
    if (!deps) return;
    if (!documentId) return;
    close();
    state.open = true;
    state.documentId = String(documentId);
    state.title = title || "";
    state.seed = null;
    state.seedError = "";
    state.provenance = null;
    state.provenanceAutoAttempted = false;
    state.provenanceNotice = "";
    state.distance = "near";
    state.categories = [];
    state.categoriesSource = "";
    state.keyphrases = [];
    state.candidates = [];
    state.query = "";
    state.closedWorldNote = "";
    state.total = null;
    state.banding = null;
    state.searched = false;
    state.searching = false;
    state.openBands = {};
    state.selected = {};
    state.ingesting = false;
    state.domainAllowed = null;
    state.comparing = false;
    state.compareById = {};
    state.compareNotes = [];

    var overlay = document.createElement("div");
    overlay.id = "paper-radar-modal";
    overlay.setAttribute("data-ui-anchor", "materials.radar-modal");
    overlay.style.cssText =
      "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML = modalHtml();
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    el("pr-close").addEventListener("click", close);
    el("pr-cancel").addEventListener("click", close);
    el("pr-search-btn").addEventListener("click", runSearch);
    el("pr-compare-btn").addEventListener("click", runCompare);
    el("pr-ingest-btn").addEventListener("click", runIngest);
    el("pr-category-add").addEventListener("click", addCategoryFromInput);
    el("pr-keyphrase-add").addEventListener("click", addKeyphraseFromInput);
    bindEnter("pr-category-input", addCategoryFromInput);
    bindEnter("pr-keyphrase-input", addKeyphraseFromInput);
    bindDistanceChoices();

    renderSeed();
    renderCategoryChips();
    renderKeyphraseChips();
    renderKeyphraseSection();
    renderQueryNote();
    renderCandidates();
    renderCompareControl();
    renderIngestSummary();

    // PR5 / PD8: 開いたときに走るのは seed の解決と許可ドメイン確認だけ。
    // 検索・比較は教員がボタンを押したときにしか実行しない。
    loadSeed();
    checkAllowedDomains();
  }

  // 距離の切り替えでは検索し直さない（PR5: 明示操作のみ）。
  function bindDistanceChoices() {
    var container = el("pr-distance");
    if (!container) return;
    var nodes = container.querySelectorAll(".pr-distance-choice");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].addEventListener("change", function () {
        if (!this.checked) return;
        state.distance = this.value;
        renderKeyphraseSection();
      });
    }
  }

  // ── seed（起点論文）────────────────────────────────────────────────────
  function loadSeed() {
    var documentId = state.documentId;
    api(
      "/admin/discovery/radar/seed?document_ref=" + encodeURIComponent(documentId)
    )
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        if (state.documentId !== documentId) return; // 別教材に切り替え済みなら破棄
        applySeed((data && data.seed) || null);
      })
      .catch(function (err) {
        if (state.documentId !== documentId) return;
        state.seed = null;
        state.seedError = detailText(err, "起点の論文を読み込めませんでした。");
        renderSeed();
        setNotice(state.seedError, true);
      });
  }

  function applySeed(seed) {
    state.seed = seed;
    state.seedError = "";
    state.provenance = (seed && seed.provenance) || null;
    state.categories = seed && seed.categories ? seed.categories.slice(0) : [];
    state.categoriesSource = (seed && seed.categories_source) || "";
    state.keyphrases = [];
    var candidates = (seed && seed.keyphrase_candidates) || [];
    for (var i = 0; i < candidates.length; i++) {
      var entry = candidates[i] || {};
      if (!entry.text) continue;
      state.keyphrases.push({
        text: entry.text,
        source: entry.source || "manual",
        enabled: entry.enabled !== false
      });
    }
    renderSeed();
    renderCategoryChips();
    renderKeyphraseChips();
    renderKeyphraseSection();
    renderQueryNote();
  }

  function renderSeed() {
    var node = el("pr-seed");
    if (!node) return;
    var seed = state.seed;
    var title = (seed && seed.title) || state.title || "";
    var html =
      '<div style="font-size:13px;color:var(--color-text-primary)">起点の論文: ' +
      esc(title) +
      "</div>";

    if (state.seedError) {
      html +=
        '<div id="pr-seed-error" style="font-size:11.5px;color:var(--color-text-danger, #e53935);margin-top:3px">' +
        esc(state.seedError) +
        "</div>";
      node.innerHTML = html;
      return;
    }

    if (seed && seed.arxiv_id) {
      html +=
        '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:3px">arXiv: ' +
        esc(seed.arxiv_id) +
        (seed.abs_url
          ? ' <a href="' +
            esc(seed.abs_url) +
            '" target="_blank" rel="noopener noreferrer" style="color:var(--color-accent, #2563eb)">arXiv で開く</a>'
          : "") +
        "</div>";
    } else if (seed && provenanceStatus() === "inferred") {
      // 推定は推定として下の区画で言う（未登録を「登録済み」に見せない）。
      html += "";
    } else if (seed) {
      // PD6: 判定不能を偽装しない（arXiv 由来でない教材はカテゴリを手で決める）。
      html +=
        '<div id="pr-seed-no-arxiv" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(NO_ARXIV_NOTICE) +
        "</div>";
    } else {
      html +=
        '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:3px">起点の論文を読み込んでいます...</div>';
    }

    html += provenanceHtml();
    node.innerHTML = html;
    // 動的描画のたびにイベントを付け直す（このモジュールの既存の流儀）。
    bindProvenanceActions(node);
    maybeAutoRegisterProvenance();
  }

  // ── arXiv 出所の後付け登録 ─────────────────────────────────────────────
  function provenanceStatus() {
    return (state.provenance && state.provenance.status) || "";
  }

  function provenanceFactHtml(text) {
    return (
      '<div class="pr-provenance-fact" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
      esc(text) +
      "</div>"
    );
  }

  // 推定・並置・登録ボタンをまとめた区画。registered / none のときは何も足さない
  // （従来表示のまま）。can_register が false のときだけ登録導線を隠す
  // （キーがサーバから来ない場合は表示し、403 は事実文で degrade する）。
  function provenanceHtml() {
    var prov = state.provenance;
    var html = "";
    if (prov && prov.status === "inferred") {
      var arxivId = String(prov.arxiv_id || "");
      if (!prov.fetched) {
        html += provenanceFactHtml(
          PROV_INFERRED_HEAD + arxivId + PROV_NOT_FETCHED_TAIL
        );
      } else if (prov.title_match) {
        html += provenanceFactHtml(
          PROV_INFERRED_HEAD + arxivId + PROV_TITLE_MATCH_TAIL
        );
      } else {
        html += provenanceFactHtml(
          PROV_INFERRED_HEAD + arxivId + PROV_MISMATCH_NOTICE
        );
        html +=
          '<div class="pr-provenance-titles" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
          esc(PROV_ARXIV_TITLE_HEAD + String(prov.arxiv_title || "")) +
          "</div>" +
          '<div class="pr-provenance-titles" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:1px">' +
          esc(PROV_DOCUMENT_TITLE_HEAD + String(prov.document_title || "")) +
          "</div>";
        if (prov.can_register !== false) {
          html +=
            '<div style="margin-top:4px">' +
            '<button type="button" id="pr-provenance-register" data-ui-anchor="materials.radar-provenance" ' +
            'class="admin-action-btn" style="font-size:11.5px;padding:1px 8px">' +
            esc(PROV_REGISTER_LABEL) +
            "</button></div>";
        }
      }
    }
    if (state.provenanceNotice) {
      html +=
        '<div id="pr-provenance-notice" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(state.provenanceNotice) +
        "</div>";
    }
    return html;
  }

  function bindProvenanceActions(root) {
    var button = root.querySelector("#pr-provenance-register");
    if (!button) return;
    button.addEventListener("click", function () {
      var prov = state.provenance || {};
      var message =
        PROV_ARXIV_TITLE_HEAD +
        String(prov.arxiv_title || "") +
        "\n" +
        PROV_DOCUMENT_TITLE_HEAD +
        String(prov.document_title || "") +
        "\n\n" +
        PROV_CONFIRM_TAIL;
      if (!window.confirm(message)) return;
      // 手動確定後は自動登録を二度と試みない。
      state.provenanceAutoAttempted = true;
      registerProvenance(true);
    });
  }

  // タイトルが一致した推定だけ、1回に限り自動登録する（PR5: 教員の作業を
  // 増やさないための例外。一致しない場合は必ず明示確認を経る）。
  function maybeAutoRegisterProvenance() {
    var prov = state.provenance;
    if (state.provenanceAutoAttempted) return;
    if (!prov || prov.status !== "inferred") return;
    if (!prov.fetched || !prov.title_match) return;
    if (prov.can_register === false) return;
    state.provenanceAutoAttempted = true;
    registerProvenance(false);
  }

  function registerProvenance(confirmed) {
    var prov = state.provenance;
    if (!prov || !prov.arxiv_id) return;
    var documentId = state.documentId;
    api("/admin/discovery/radar/provenance", {
      method: "POST",
      body: JSON.stringify({
        document_ref: documentId,
        arxiv_id: prov.arxiv_id,
        confirm: !!confirmed
      })
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        if (state.documentId !== documentId) return;
        state.provenanceNotice = PROV_REGISTERED_NOTICE;
        if (data && data.seed) {
          // 登録の副作用で、POST の往復中に教員が編集した条件チップ
          // （カテゴリ・キーフレーズ）を巻き戻さない。出所の表示だけ
          // registered に更新する。
          state.seed = data.seed;
          if (data.seed.provenance) state.provenance = data.seed.provenance;
          if (data.seed.categories_source) {
            state.categoriesSource = data.seed.categories_source;
          }
        }
        renderSeed();
        renderCategoryChips();
      })
      .catch(function (err) {
        if (state.documentId !== documentId) return;
        // 403 / 409 / 422 はサーバの事実文をそのまま出すだけで、検索操作は妨げない。
        state.provenanceNotice = detailText(err, PROV_REGISTER_FAILED_NOTICE);
        renderSeed();
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

  function renderCategoryChips() {
    var node = el("pr-categories");
    var source = el("pr-category-source");
    if (source) {
      source.textContent = state.categoriesSource
        ? CATEGORY_SOURCE_NOTICES[state.categoriesSource] ||
          CATEGORY_SOURCE_NOTICES.manual
        : "";
    }
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
        '<button type="button" class="pr-category-remove" data-value="' +
        esc(state.categories[i]) +
        '" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;padding:0;font-size:12px">&times;</button>' +
        "</span>";
    }
    node.innerHTML = html;
    bindAll(node, ".pr-category-remove", function (button) {
      var index = indexOfText(state.categories, button.getAttribute("data-value"));
      if (index >= 0) state.categories.splice(index, 1);
      renderCategoryChips();
    });
  }

  function renderKeyphraseChips() {
    var node = el("pr-keyphrases");
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
        '<button type="button" class="pr-keyphrase-chip" data-value="' +
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
    bindAll(node, ".pr-keyphrase-chip", function (button) {
      var index = indexOfText(state.keyphrases, button.getAttribute("data-value"));
      if (index >= 0) {
        state.keyphrases[index].enabled = !state.keyphrases[index].enabled;
      }
      renderKeyphraseChips();
    });
  }

  // 「近い」以外はカテゴリだけで網を張る（キーフレーズで絞ると「近い」に寄るため）。
  // 区画ごと隠して、効かない条件を操作させない。
  function renderKeyphraseSection() {
    var section = el("pr-keyphrase-section");
    if (!section) return;
    section.style.display = state.distance === "near" ? "" : "none";
  }

  function addCategoryFromInput() {
    var input = el("pr-category-input");
    var value = input ? input.value.trim() : "";
    if (!value) return;
    if (indexOfText(state.categories, value) < 0) state.categories.push(value);
    if (input) input.value = "";
    renderCategoryChips();
  }

  function addKeyphraseFromInput() {
    var input = el("pr-keyphrase-input");
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
    if (!state.documentId) return;

    state.searching = true;
    setNotice("arXiv を検索しています...");
    var button = el("pr-search-btn");
    if (button) button.disabled = true;

    var payload = {
      document_ref: state.documentId,
      distance: state.distance,
      categories: state.categories,
      // 「近い」以外はキーフレーズを条件に使わない（UI の非表示と挙動を一致させる）。
      keyphrases: state.distance === "near" ? enabledKeyphrases() : []
    };

    api("/admin/discovery/radar/search", {
      method: "POST",
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.searching = false;
        if (button) button.disabled = false;
        if (data && data.seed) applySeedMeta(data.seed);
        state.candidates = (data && data.candidates) || [];
        state.query = (data && data.query) || "";
        state.closedWorldNote = (data && data.closed_world_note) || "";
        state.total = data && typeof data.total === "number" ? data.total : null;
        state.banding = (data && data.banding) || null;
        state.searched = true;
        state.openBands = {};
        state.selected = {};
        // 前の検索の比較結果を新しい候補に持ち越さない（PR4: 結果は一時的な注釈）。
        state.compareById = {};
        state.compareNotes = [];
        setNotice("");
        renderQueryNote();
        renderCandidates();
        renderCompareControl();
        renderIngestSummary();
      })
      .catch(function (err) {
        state.searching = false;
        if (button) button.disabled = false;
        // 404（教材不可視）/ 422（距離語彙外）/ 502（arXiv 失敗）はサーバの事実文をそのまま。
        setNotice(detailText(err, "検索に失敗しました。"), true);
      });
  }

  // 検索レスポンスの seed は表示メタだけ更新する（教員が編集した条件は上書きしない）。
  // provenance は検索経路だと arXiv 再取得を省くため fetched=false に劣化しうる。
  // 手元の情報が減る方向の上書きはせず、registered 化（登録済みになった）か
  // 手元が空のときだけ差し替える。
  function applySeedMeta(seed) {
    if (!seed) return;
    state.seed = seed;
    var incoming = seed.provenance;
    if (incoming && (incoming.status === "registered" || !state.provenance)) {
      state.provenance = incoming;
    }
    renderSeed();
  }

  // PR7: 何をどう検索した結果なのかを一覧の上に常時出す。
  function renderQueryNote() {
    var node = el("pr-query-note");
    if (!node) return;
    var parts = [];
    if (state.query) {
      parts.push("検索条件: " + state.query);
    } else if (state.searched) {
      parts.push(
        "検索条件: 指定されていません（カテゴリまたはキーフレーズを指定してください）。"
      );
    } else {
      parts.push("検索条件: まだ検索していません。");
    }
    if (state.total !== null) parts.push("該当件数: " + String(state.total));
    if (state.closedWorldNote) parts.push(state.closedWorldNote);
    // PR2: 帯分けができなかったことを黙らない（サーバの note を優先して出す）。
    if (state.banding && state.banding.available === false) {
      parts.push(
        state.banding.note ? String(state.banding.note) : BANDING_UNAVAILABLE_NOTICE
      );
    }
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
      '<div class="pr-candidate" data-arxiv-id="' +
      esc(arxivId) +
      '" style="border-bottom:1px solid var(--color-border-tertiary);padding:8px 0">' +
      '<div style="display:flex;gap:8px;align-items:flex-start">';

    if (status === "new") {
      html +=
        '<input type="checkbox" class="pr-select" data-arxiv-id="' +
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
      "</div>";

    // PR2: サーバが確定した帯ラベルをそのまま出す（閾値判定・数値描画をしない）。
    if (candidate && candidate.distance_label) {
      html +=
        '<span class="pr-distance-label" style="display:inline-block;margin-top:3px;border:1px solid var(--color-border);border-radius:10px;padding:0 7px;font-size:11px;color:var(--color-text-secondary)">' +
        esc(DISTANCE_CHIP_HEAD + candidate.distance_label) +
        "</span>";
    }

    html +=
      '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
      esc(meta.join(" ・ ")) +
      "</div>";

    if (matched.length) {
      // なぜ候補なのかを1行で言う（ブラックボックスのおすすめにしない・数値は出さない）。
      html +=
        '<div class="pr-matched" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:2px">一致: ' +
        esc(matched.join(" ・ ")) +
        "</div>";
    }

    if (status === "ingested") {
      html +=
        '<div class="pr-status-label" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:3px">取り込み済み</div>';
    } else if (candidate && candidate.abs_url) {
      html +=
        '<div style="margin-top:4px">' +
        '<a href="' +
        esc(candidate.abs_url) +
        '" target="_blank" rel="noopener noreferrer" style="font-size:11px;color:var(--color-accent, #2563eb)">arXiv で開く</a>' +
        "</div>";
    }

    html += compareBlockHtml(arxivId);

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

  // PR4: 比較結果は候補カード内の一時的な注釈。出所ラベルとサーバの caveat を
  // そのまま添える（クライアントで注意書きを作らない・断定に書き換えない）。
  function compareBlockHtml(arxivId) {
    var item = state.compareById[arxivId];
    if (!item) return "";
    var html =
      '<div class="pr-compare-block" style="margin-top:6px;border-left:2px solid var(--color-border);padding-left:8px">' +
      '<div style="font-size:11.5px;color:var(--color-text-secondary)">' +
      esc(COMPARE_HEAD) +
      "</div>";

    if (item.common_ground) {
      html +=
        '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(COMPARE_COMMON_HEAD + item.common_ground) +
        "</div>";
    }

    var differences = item.differences || [];
    if (differences.length) {
      html += '<ul style="margin:4px 0 0 16px;padding:0">';
      for (var i = 0; i < differences.length; i++) {
        var diff = differences[i] || {};
        html +=
          '<li style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">' +
          esc(diff.statement || "");
        if (diff.evidence_quote) {
          html +=
            '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:1px">' +
            esc(COMPARE_QUOTE_HEAD + '"' + diff.evidence_quote + '"') +
            "</div>";
        }
        html += "</li>";
      }
      html += "</ul>";
    } else if (!item.common_ground) {
      html +=
        '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:3px">' +
        esc(COMPARE_EMPTY_NOTICE) +
        "</div>";
    }

    if (item.caveat) {
      html +=
        '<div class="pr-compare-caveat" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:4px">' +
        esc(item.caveat) +
        "</div>";
    }

    return html + "</div>";
  }

  // PR2: 帯（distance_label）ごとに区切る。どの帯がどのラベルかはサーバの語彙が
  // 正本なので、クライアントはラベル文字列をキーにして素直にまとめるだけにする。
  // ラベルの無い候補は最遠帯へ混ぜず、専用の区画に分ける。
  function groupCandidatesByBand(candidates) {
    var groups = [];
    var index = {};
    var unmeasured = [];
    for (var i = 0; i < candidates.length; i++) {
      var candidate = candidates[i] || {};
      var label = candidate.distance_label;
      if (!label) {
        unmeasured.push(candidate);
        continue;
      }
      var key = String(label);
      if (index[key] === undefined) {
        index[key] = groups.length;
        groups.push({ label: key, items: [] });
      }
      groups[index[key]].items.push(candidate);
    }
    return { groups: groups, unmeasured: unmeasured };
  }

  // 既定で開く帯は、サーバが選択距離に対応する帯を宣言していればその帯、
  // 宣言が無ければ「候補が最も多い帯」。距離キー → 帯ラベルの対応表を
  // クライアントに持たないための決め方で（帯ラベルの正本はサーバの語彙）、
  // どちらにしても他の帯は折りたたみで必ず残す（候補を捨てない — PR7）。
  function primaryGroupIndex(groups) {
    var declared = state.banding && state.banding.primary_label;
    if (declared) {
      for (var d = 0; d < groups.length; d++) {
        if (groups[d].label === String(declared)) return d;
      }
    }
    var best = -1;
    var bestCount = -1;
    for (var i = 0; i < groups.length; i++) {
      if (groups[i].items.length > bestCount) {
        bestCount = groups[i].items.length;
        best = i;
      }
    }
    return best;
  }

  // 既定は「選択距離に対応する（または最大の）帯だけ開く」。教員が別の帯を開いたら
  // その状態を覚えて、比較結果の描き直しで畳まないようにする。
  function bandIsOpen(label, isPrimary) {
    if (Object.prototype.hasOwnProperty.call(state.openBands, label)) {
      return !!state.openBands[label];
    }
    return !!isPrimary;
  }

  function bandSectionHtml(group, open, isPrimary) {
    var summary = isPrimary
      ? DISTANCE_CHIP_HEAD + group.label + "（" + String(group.items.length) + "件）"
      : OTHER_BAND_HEAD + group.label + "（" + String(group.items.length) + "件）";
    var html =
      '<details class="pr-band" data-band-label="' +
      esc(group.label) +
      '"' +
      (open ? " open" : "") +
      ' style="margin-bottom:6px">' +
      '<summary style="font-size:12px;color:var(--color-text-secondary);cursor:pointer">' +
      esc(summary) +
      "</summary>";
    for (var i = 0; i < group.items.length; i++) {
      html += candidateCardHtml(group.items[i]);
    }
    return html + "</details>";
  }

  function renderCandidates() {
    var node = el("pr-results");
    if (!node) return;
    if (!state.candidates.length) {
      node.innerHTML =
        '<div id="pr-empty-note" style="font-size:12.5px;color:var(--color-text-tertiary);padding:8px 0">' +
        esc(state.searched ? EMPTY_RESULT_NOTICE : NOT_SEARCHED_NOTICE) +
        "</div>";
      return;
    }

    var html = "";
    if (state.banding && state.banding.available === false) {
      // 帯分けができなかったときは帯を作らず、新着順のまま一覧にする（PR2）。
      for (var i = 0; i < state.candidates.length; i++) {
        html += candidateCardHtml(state.candidates[i]);
      }
    } else {
      var grouped = groupCandidatesByBand(state.candidates);
      var primary = primaryGroupIndex(grouped.groups);
      for (var g = 0; g < grouped.groups.length; g++) {
        html += bandSectionHtml(
          grouped.groups[g],
          bandIsOpen(grouped.groups[g].label, g === primary),
          g === primary
        );
      }
      if (grouped.unmeasured.length) {
        html +=
          '<details class="pr-band pr-band-unmeasured" data-band-label="' +
          esc(UNMEASURED_HEAD) +
          '"' +
          (bandIsOpen(UNMEASURED_HEAD, false) ? " open" : "") +
          ' style="margin-bottom:6px">' +
          '<summary style="font-size:12px;color:var(--color-text-secondary);cursor:pointer">' +
          esc(UNMEASURED_HEAD + "（" + String(grouped.unmeasured.length) + "件）") +
          "</summary>";
        for (var u = 0; u < grouped.unmeasured.length; u++) {
          html += candidateCardHtml(grouped.unmeasured[u]);
        }
        html += "</details>";
      }
    }

    node.innerHTML = html;
    bindBandToggles(node);
    bindAll(node, ".pr-select", function (input) {
      var arxivId = input.getAttribute("data-arxiv-id");
      if (input.checked) state.selected[arxivId] = true;
      else delete state.selected[arxivId];
      renderCompareControl();
      renderIngestSummary();
    });
  }

  function bindBandToggles(root) {
    var nodes = root.querySelectorAll(".pr-band");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].addEventListener("toggle", function () {
        var label = this.getAttribute("data-band-label");
        if (label) state.openBands[label] = this.open;
      });
    }
  }

  function findCandidate(arxivId) {
    for (var i = 0; i < state.candidates.length; i++) {
      if (state.candidates[i] && state.candidates[i].arxiv_id === arxivId) {
        return state.candidates[i];
      }
    }
    return null;
  }

  // ── 比較分析（PR5: ボタンを押したときだけ実行する）──────────────────────
  function selectedIds() {
    var out = [];
    for (var key in state.selected) {
      if (Object.prototype.hasOwnProperty.call(state.selected, key) && state.selected[key]) {
        out.push(key);
      }
    }
    return out;
  }

  function renderCompareControl() {
    var button = el("pr-compare-btn");
    var note = el("pr-compare-note");
    var ids = selectedIds();
    var lines = [];
    if (!ids.length) {
      lines.push(COMPARE_NONE_SELECTED_NOTICE);
    } else if (ids.length > COMPARE_MAX) {
      lines.push(
        COMPARE_LIMIT_NOTICE_HEAD + String(COMPARE_MAX) + COMPARE_LIMIT_NOTICE_TAIL
      );
    }
    for (var i = 0; i < state.compareNotes.length; i++) {
      lines.push(String(state.compareNotes[i]));
    }
    if (note) note.textContent = lines.join(" ");
    if (!button) return;
    button.disabled =
      state.comparing || !ids.length || ids.length > COMPARE_MAX;
  }

  function runCompare() {
    if (state.comparing) return;
    var button = el("pr-compare-btn");
    if (button && button.disabled) return;
    var ids = selectedIds();
    if (!ids.length || ids.length > COMPARE_MAX) return;

    state.comparing = true;
    state.compareNotes = [];
    renderCompareControl();
    setNotice("違いを分析しています...");

    api("/admin/discovery/radar/compare", {
      method: "POST",
      body: JSON.stringify({ document_ref: state.documentId, arxiv_ids: ids })
    })
      .then(function (res) {
        if (!res.ok) return rejectWithBody(res);
        return res.json();
      })
      .then(function (data) {
        state.comparing = false;
        applyCompareResult(data || {});
      })
      .catch(function (err) {
        state.comparing = false;
        // 429（日次上限）/ 502（LLM 失敗）はサーバの事実文をそのまま見せる。
        setNotice(detailText(err, "比較分析を実行できませんでした。"), true);
        renderCompareControl();
      });
  }

  function applyCompareResult(data) {
    var items = data.items || [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      if (item.arxiv_id) state.compareById[item.arxiv_id] = item;
    }
    var notes = [];
    var skipped = data.skipped || [];
    for (var s = 0; s < skipped.length; s++) {
      var skip = skipped[s] || {};
      // サーバの detail を独自文で上書きしない（黙って落とさない）。
      notes.push(
        (skip.arxiv_id || "") + ": " + (skip.detail || "比較できませんでした。")
      );
    }
    var serverNotes = data.notes || [];
    for (var n = 0; n < serverNotes.length; n++) {
      notes.push(String(serverNotes[n]));
    }
    state.compareNotes = notes;
    setNotice("");
    renderCandidates();
    renderCompareControl();
  }

  // ── 取り込み（PR3: 既存の弁のみ。境界も事実文も分野購読モーダルと同一）────
  function usesBatchIngest(count) {
    return count > SYNC_INGEST_MAX;
  }

  function renderIngestSummary() {
    var summary = el("pr-ingest-summary");
    var button = el("pr-ingest-btn");
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
  }

  function runIngest() {
    if (state.ingesting) return;
    var button = el("pr-ingest-btn");
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
      // キュー行の表示に使うタイトルだけ添える（候補は保存しない — PR1）。
      if (batch && candidate && candidate.title) entry.title = candidate.title;
      items.push(entry);
    }
    var payload = uploadOptions();
    payload.items = items;
    // 監査の帰属は seed の分野（引けなければサーバ側の既定に落ちる）。
    if (seedDomainKey()) payload.domain_key = seedDomainKey();

    state.ingesting = true;
    if (button) button.disabled = true;
    var result = el("pr-ingest-result");
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
        renderCompareControl();
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

    var result = el("pr-ingest-result");
    if (result) result.textContent = lines.join(" ／ ");
    renderCandidates();
  }

  // キュー投入は「取り込み完了」ではない。候補行の status をローカルで
  // "ingested" に書き換えない（PD6: 起きていないことを起きたように見せない）。
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
      lines.push(
        (skip.arxiv_id || "") + ": " + (skip.detail || "キューに登録できませんでした。")
      );
    }
    if (data.notice) lines.push(String(data.notice));
    if (!lines.length) lines.push("キューに登録された論文はありませんでした。");

    var result = el("pr-ingest-result");
    if (result) result.textContent = lines.join(" ／ ");
    renderCandidates();
  }

  window.PaperRadar = {
    init: function (injected) {
      deps = injected || null;
    },
    openModal: openModal,
    close: close
  };
})();
