/* 個人知識ネットワーク（Phase P-3）— 最上位「わたしの地図」パネル。
 *
 * 設計の正本: /Users/Shared/issues/episteme_graph_personal_knowledge_network_ux_proposal.md
 * （§2 UX原則・§3.1/3.2 情報設計・§4 画面フロー・§7.1 プライバシー・§8 避けるべきUX）と、
 * その実装フェーズ表 §9 Phase P-3。参照する不変条項（同族の docs/features/
 * personal_knowledge_network_design.md §0 PN-1〜PN-7 を継承）:
 *   PN-1 本人のみ可視 / PN-2 導出のみ・保存しない（毎回サーバ状態から導出。完了フラグを
 *   持たない） / PN-3 本人確定のみ（サーバ側 core/personal_graph/derive.py が担保） /
 *   PN-4 数値を見せない（件数・割合・順位付けの類は一切描かない） /
 *   PN-5 非LLM・決定論・自動で開かない（明示操作＝ボタン押下でのみ fetch する） /
 *   PN-6 同一性リンクは confirmed のみ（サーバ側 journey.py が担保） /
 *   PN-7 fail-closed（取得失敗時は「いまは表示できません」とだけ出す。コンソールエラーに
 *   しない）。
 *
 * personal-map.js（コース単位「わたしの地図」atlas トグル）とは独立の軽量 DOM
 * （#personal-map-home-overlay）。atlas-overlay.js の canvas/レベル描画には一切触れない。
 * データソースは本人スコープの正本 API `/api/me/personal-network`（コース横断）であり、
 * コース単位の `/api/learning/courses/{id}/personal-network` は使わない
 * （提案書 §5.3 — 正本APIは本人主体、course_id は所有境界ではなく provenance）。
 *
 * ビュー構成（提案書 §3.2 の A/C/D。B「このコースでの地図」は既存 personal-map.js が
 * 担うため本パネルには含めない）:
 *   いまの地図   — 直近の本人確定痕跡を現在地にした局所ビュー（デフォルト）
 *   問いからの旅 — 問い/引っかかりの新しい順カード列（上限20件・「すべて見る」は作らない）
 *   振り返り     — 月別グルーピング（件数・進捗率は出さない。PN-4）
 *
 * 公開契約 window.PersonalMapHome（呼び出し側は app.js。名前・引数は固定）:
 *   init(deps)   — deps.openTrajectory(traceId) を登録（任意。ノード詳細から既存の
 *                  問いの軌跡ビューへ遷移する導線に使う。未登録でも動作する）
 *   open()       — パネルを開く（fetch は開くたびに1回・in-memory キャッシュ済みなら再利用）
 *   close()      — パネルを閉じる（旅の表示も破棄する）
 *   invalidate() — ログアウト・コース切替時にキャッシュを破棄する（次回 open() で再取得）
 */
(() => {
  "use strict";

  const API_BASE = "/api";
  const OVERLAY_ID = "personal-map-home-overlay";

  // この地図の常設注記（提案書 §7.1 の文言そのまま）。
  const PRIVACY_NOTE = "この地図はあなたにだけ表示されます。成績評価には使用されません。";

  // kind 表示名（PN-4: 個数・割合は一切添えない）。
  const KIND_META = {
    tension: { label: "引っかかり" },
    question: { label: "問い" },
    reconstruction: { label: "再構成" },
  };

  // 旅の step.ref.kind ごとの種別ラベル（提案書 §2.5 の区別をそのまま踏襲）。
  const REF_KIND_LABEL = {
    graph_node: "理論構成",
    shared_part: "共通部品（専門家確認済み）",
    document: "教材",
    atlas_node: "地図",
    personal_node: "あなたの痕跡",
  };

  const TABS = [
    { key: "now", label: "いまの地図" },
    { key: "journeys", label: "問いからの旅" },
    { key: "reflect", label: "振り返り" },
  ];

  const state = {
    deps: {},
    overlayEl: null,
    contentEl: null,
    journeyAreaEl: null,
    tabsEl: null,
    activeTab: "now",
    cache: null, // { promise, data } — open() のたびに1回だけ fetch。invalidate() で破棄
    lastFetchFailed: false, // 「読み込み中」と「取得失敗」の表示を区別するためだけの一時フラグ
    lastFocus: null,
    // G3-P1: 「いまの地図」タブのコース絞り込み（既定=すべて。クライアント側フィルタのみ・
    // 再 fetch しない。地図オーバーレイに依存しない導線として本パネルに持たせる）。
    courseFilter: "",
  };

  // -------------------------------------------------------------------
  // 認証・取得（personal-map.js と同じ様式。fail-closed）
  // -------------------------------------------------------------------

  function token() {
    try {
      return localStorage.getItem("eg_token") || null;
    } catch (e) {
      return null;
    }
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function fmtMonth(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.getFullYear() + "年" + (d.getMonth() + 1) + "月";
  }

  // 本人スコープの個人ネットワーク（コース横断・正本API）。open() のたびに1回だけ fetch し
  // in-memory キャッシュする（PN-2: サーバに保存させない・ポーリングしない）。
  // 401/失敗はすべて null に丸め、呼び出し側が静かに諦められるようにする（PN-7）。
  function loadNetwork() {
    if (state.cache) return state.cache.promise;
    const t = token();
    if (!t) return Promise.resolve(null);

    state.lastFetchFailed = false;
    const entry = { promise: null, data: null };
    entry.promise = fetch(API_BASE + "/me/personal-network", {
      headers: { Authorization: "Bearer " + t },
    })
      .then((res) => {
        if (!res.ok) throw new Error("personal-network " + res.status);
        return res.json();
      })
      .then((data) => {
        entry.data = data;
        return data;
      })
      .catch(() => {
        state.cache = null; // fail-closed: 次回 open() で再試行できるようにする
        state.lastFetchFailed = true;
        return null;
      });
    state.cache = entry;
    return entry.promise;
  }

  function cachedData() {
    return state.cache ? state.cache.data : null;
  }

  // 旅の経路探索（コース横断・正本API）。明示操作（「ここから旅に出る」ボタン）でのみ
  // fetch する。キャッシュしない（旅は毎回明示操作からやり直す。PN-5）。
  // 404（対象なし）は従来どおり fail-closed で null に丸めるが、それ以外の非 ok /
  // 通信例外は `_fetch_error` で区別する（renderJourneyArea が「空」と「取得できな
  // かった」を分けて表示するため。G3）。
  function fetchJourney(nodeId) {
    const t = token();
    if (!t || !nodeId) return Promise.resolve(null);
    return fetch(
      API_BASE + "/me/personal-network/journey?node_id=" + encodeURIComponent(nodeId),
      { headers: { Authorization: "Bearer " + t } }
    )
      .then((res) => {
        if (res.status === 404) return null;
        if (!res.ok) return { _fetch_error: true };
        return res.json();
      })
      .catch(() => ({ _fetch_error: true }));
  }

  // -------------------------------------------------------------------
  // 表示ヘルパ
  // -------------------------------------------------------------------

  function kindLabel(kind) {
    return (KIND_META[kind] || {}).label || kind || "記録";
  }

  function courseTitleOf(data, courseId) {
    if (!courseId) return "以前の学習";
    const c = data && data.courses && data.courses[courseId];
    return (c && c.title) || "以前の学習";
  }

  // created_at 新しい順（PN-2: サーバ状態からの導出結果をそのまま並べるだけ）。
  function nodesByRecency(data) {
    const nodes = (data && data.nodes) || [];
    return nodes.slice().sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  }

  // G3-P1: ノードの provenance（course_id）に現れるコース一覧を出現順で返す（candidate は
  // 数えない・件数は出さない。フィルタの選択肢を作るためだけの導出）。
  function distinctCourseIds(data) {
    const seen = {};
    const order = [];
    ((data && data.nodes) || []).forEach((n) => {
      const cid = n.course_id || "";
      if (!cid || seen[cid]) return;
      seen[cid] = true;
      order.push(cid);
    });
    return order;
  }

  // 「いまの地図」タブのみのコース絞り込み（クライアント側フィルタ・再 fetch しない）。
  function filteredNodes(data) {
    const nodes = nodesByRecency(data);
    if (!state.courseFilter) return nodes;
    return nodes.filter((n) => (n.course_id || "") === state.courseFilter);
  }

  // 複数コースの痕跡があるときだけ意味を持つため、コースが1つ以下なら何も描かない。
  function renderCourseFilter(data) {
    const ids = distinctCourseIds(data);
    if (ids.length < 2) return "";
    let html = '<div class="pm-home-course-filter">';
    html += '<label class="pm-home-course-filter-label">コース</label>';
    html += '<select class="pm-home-course-filter-select" data-pm-home-course-filter="1">';
    html += '<option value=""' + (state.courseFilter === "" ? " selected" : "") + '>すべて</option>';
    ids.forEach((cid) => {
      const sel = state.courseFilter === cid ? " selected" : "";
      html += '<option value="' + esc(cid) + '"' + sel + '>' + esc(courseTitleOf(data, cid)) + '</option>';
    });
    html += '</select></div>';
    return html;
  }

  function renderFacts(facts) {
    if (!facts || !facts.length) return "";
    let html = '<ul class="pm-home-facts">';
    facts.forEach((f) => { html += "<li>" + esc(f) + "</li>"; });
    html += "</ul>";
    return html;
  }

  function nodeRowHtml(node, data) {
    let html = '<div class="pm-home-node-row">';
    html += '<div class="pm-home-node-top">';
    html += '<span class="pm-home-node-kind">' + esc(kindLabel(node.node_kind)) + "</span>";
    html += '<span class="pm-home-node-label">' + esc(node.label || "") + "</span>";
    html += "</div>";
    html += '<div class="pm-home-node-ctx">' + esc(courseTitleOf(data, node.course_id)) + "</div>";
    html += renderFacts(node.facts);
    html += '<div class="pm-home-node-actions">';
    html +=
      '<button type="button" class="pm-home-journey-btn" data-pm-home-journey="' +
      esc(node.id) +
      '">ここから旅に出る</button>';
    html += "</div>";
    html += "</div>";
    return html;
  }

  function failHtml() {
    return '<p class="pm-home-fail">いまは表示できません。</p>';
  }

  function loadingHtml() {
    return '<p class="pm-home-loading">読み込み中…</p>';
  }

  // -------------------------------------------------------------------
  // ビュー A: いまの地図（デフォルト）
  // -------------------------------------------------------------------

  function sameAnchorGroupNodes(current, data, byId) {
    const groups = (data && data.anchor_groups) || [];
    const group = groups.find((g) => Array.isArray(g.node_ids) && g.node_ids.indexOf(current.id) !== -1);
    if (!group) return [];
    const out = [];
    group.node_ids.forEach((id) => {
      if (id === current.id) return;
      const n = byId[id];
      if (n) out.push(n);
    });
    return out;
  }

  function renderNow(data) {
    // G3-P1: コース絞り込み（地図オーバーレイに依存しない、コース単位の分岐を持つ導線）。
    const filterHtml = renderCourseFilter(data);
    const nodes = filteredNodes(data);
    if (!nodes.length) {
      const emptyMsg = state.courseFilter
        ? "このコースに紐づく痕跡はまだありません。"
        : "まだ痕跡がありません。学習の中で問いを残すと、ここに現れます。";
      return filterHtml + '<p class="pm-home-empty">' + esc(emptyMsg) + "</p>";
    }
    const byId = {};
    nodes.forEach((n) => { byId[n.id] = n; });
    const current = nodes[0];

    let html = filterHtml;
    html += '<div class="pm-home-current">';
    html += '<div class="pm-home-current-heading">現在地</div>';
    html += nodeRowHtml(current, data);
    html += "</div>";

    // ①同じ anchor_group の他ノード（少数・anchor_groups が自然に絞る）
    const groupNodes = sameAnchorGroupNodes(current, data, byId);
    // ②直近の他ノード数件（上限5・少数に限定。§2.2）
    const groupIds = {};
    groupNodes.forEach((n) => { groupIds[n.id] = true; });
    const recentNodes = [];
    for (let i = 1; i < nodes.length && recentNodes.length < 5; i++) {
      const n = nodes[i];
      if (n.id === current.id || groupIds[n.id]) continue;
      recentNodes.push(n);
    }

    if (groupNodes.length || recentNodes.length) {
      html += '<div class="pm-home-related">';
      html += '<div class="pm-home-related-heading">ここへつながるもの</div>';
      if (groupNodes.length) {
        html += '<div class="pm-home-related-sub">同じ場所につながる記録</div>';
        groupNodes.forEach((n) => { html += nodeRowHtml(n, data); });
      }
      if (recentNodes.length) {
        html += '<div class="pm-home-related-sub">最近の記録</div>';
        recentNodes.forEach((n) => { html += nodeRowHtml(n, data); });
      }
      html += "</div>";
    }
    return html;
  }

  // -------------------------------------------------------------------
  // ビュー C: 問いからの旅
  // -------------------------------------------------------------------

  function renderJourneys(data) {
    const nodes = nodesByRecency(data).filter(
      (n) => n.node_kind === "question" || n.node_kind === "tension"
    );
    if (!nodes.length) {
      return '<p class="pm-home-empty">まだ問いの記録がありません。学習の中で問いを残すと、ここに現れます。</p>';
    }
    // 表示上限20件（「すべて見る」を基本導線にしない。§4.5）
    const shown = nodes.slice(0, 20);
    let html = '<div class="pm-home-journeys-list">';
    shown.forEach((n) => {
      html += '<div class="pm-home-journey-card-item">';
      html += '<div class="pm-home-node-top">';
      html += '<span class="pm-home-node-kind">' + esc(kindLabel(n.node_kind)) + "</span>";
      html += '<span class="pm-home-node-label">' + esc(n.label || "") + "</span>";
      html += "</div>";
      html +=
        '<div class="pm-home-node-ctx">' +
        esc(courseTitleOf(data, n.course_id)) +
        " · " +
        esc(fmtMonth(n.created_at)) +
        "</div>";
      html += '<div class="pm-home-node-actions">';
      html +=
        '<button type="button" class="pm-home-journey-btn" data-pm-home-journey="' +
        esc(n.id) +
        '">ここから旅に出る</button>';
      html += "</div>";
      html += "</div>";
    });
    html += "</div>";
    return html;
  }

  // -------------------------------------------------------------------
  // ビュー D: 振り返り（月別グルーピング。件数・進捗率は出さない。PN-4）
  // -------------------------------------------------------------------

  function renderReflect(data) {
    const nodes = nodesByRecency(data);
    if (!nodes.length) {
      return '<p class="pm-home-empty">まだ振り返れる記録がありません。</p>';
    }
    const order = [];
    const byMonth = {};
    nodes.forEach((n) => {
      const label = fmtMonth(n.created_at) || "時期不明";
      if (!byMonth[label]) {
        byMonth[label] = [];
        order.push(label);
      }
      byMonth[label].push(n);
    });
    let html = "";
    order.forEach((label) => {
      html += '<div class="pm-home-reflect-month">';
      html += '<div class="pm-home-reflect-month-heading">' + esc(label) + "</div>";
      byMonth[label].forEach((n) => {
        html += '<div class="pm-home-reflect-item">';
        html +=
          '<div class="pm-home-reflect-item-title">' +
          esc(kindLabel(n.node_kind)) +
          "『" +
          esc(n.label || "") +
          "』（" +
          esc(courseTitleOf(data, n.course_id)) +
          "）</div>";
        html += renderFacts(n.facts);
        html += "</div>";
      });
      html += "</div>";
    });
    return html;
  }

  // -------------------------------------------------------------------
  // 旅の経路表示（常に最新1枚。A/C 両ビュー共通のカードとして下部に表示する）
  // -------------------------------------------------------------------

  function closeJourneyArea() {
    if (!state.journeyAreaEl) return;
    state.journeyAreaEl.hidden = true;
    state.journeyAreaEl.innerHTML = "";
  }

  function renderJourneyArea(data) {
    const area = state.journeyAreaEl;
    if (!area) return;
    if (data && data._fetch_error) {
      // G3: 404/空（fail-closed）とは区別し、通信エラーで読み込めなかった事実だけを出す。
      area.innerHTML =
        '<button type="button" class="pm-home-journey-close" aria-label="閉じる">×</button>' +
        '<div class="pm-home-journey-heading">旅の経路</div>' +
        '<div class="pm-home-journey-fact">旅の経路を読み込めませんでした。</div>';
      area.hidden = false;
      return;
    }
    if (!data || !Array.isArray(data.steps) || !data.steps.length) {
      // steps が空/対象なし（404）なら何も出さない（fail-closed。エラーバナーは出さない）
      area.hidden = true;
      area.innerHTML = "";
      return;
    }
    let html = '<button type="button" class="pm-home-journey-close" aria-label="閉じる">×</button>';
    html += '<div class="pm-home-journey-heading">旅の経路</div>';
    html += '<div class="pm-home-journey-list">';
    data.steps.forEach((step) => {
      html += '<div class="pm-home-journey-item">';
      html += '<span class="pm-home-journey-fact">' + esc(step.fact || "") + "</span>";
      const ref = step.ref || null;
      const refLabel = ref && REF_KIND_LABEL[ref.kind];
      if (refLabel) {
        html += '<span class="pm-home-journey-ref-kind">' + esc(refLabel) + "</span>";
      }
      html += "</div>";
    });
    html += "</div>";
    if (data.frontier_note) {
      // 経路が途切れた事実をそのまま出す（警告色にしない。通常文体で表示する）
      html += '<div class="pm-home-journey-frontier">' + esc(data.frontier_note) + "</div>";
      // G7-J: 行き止まりで終わらせず、別の問いから旅をやり直せる導線を出す（事実文のみ）。
      html +=
        '<button type="button" class="pm-home-journey-btn pm-home-journey-restart" ' +
        'data-pm-home-journey-restart="1">別の問いから旅に出る</button>';
    }
    if (data.truncated) {
      html += '<div class="pm-home-journey-truncated">（途中まで）</div>';
    }
    area.innerHTML = html;
    area.hidden = false;
    if (typeof area.scrollIntoView === "function") area.scrollIntoView({ block: "nearest" });
  }

  function requestJourney(nodeId) {
    if (!nodeId) return;
    // 旅カードは常に最新1枚 — 新しい旅を開くと前のカードは即座に置き換わる
    closeJourneyArea();
    fetchJourney(nodeId).then((data) => renderJourneyArea(data));
  }

  // -------------------------------------------------------------------
  // パネル全体の描画
  // -------------------------------------------------------------------

  function renderTabsBar() {
    let html = "";
    TABS.forEach((tab) => {
      const on = state.activeTab === tab.key;
      html +=
        '<button type="button" class="pm-home-tab' +
        (on ? " active" : "") +
        '" aria-pressed="' +
        on +
        '" data-pm-home-tab="' +
        tab.key +
        '">' +
        esc(tab.label) +
        "</button>";
    });
    return html;
  }

  function renderTabContent() {
    const data = cachedData();
    if (!data) {
      // 取得前（読み込み中）と取得失敗を区別する（後者のみ「いまは表示できません」。PN-7）。
      return state.lastFetchFailed ? failHtml() : loadingHtml();
    }
    if (state.activeTab === "journeys") return renderJourneys(data);
    if (state.activeTab === "reflect") return renderReflect(data);
    return renderNow(data);
  }

  function renderPanel() {
    if (state.tabsEl) state.tabsEl.innerHTML = renderTabsBar();
    if (state.contentEl) state.contentEl.innerHTML = renderTabContent();
  }

  function switchTab(key) {
    if (state.activeTab === key) return;
    state.activeTab = key;
    closeJourneyArea();
    renderPanel();
  }

  // -------------------------------------------------------------------
  // オーバーレイの構築（一度だけ。atlas-overlay とは独立の軽量 DOM）
  // -------------------------------------------------------------------

  function onOverlayClick(e) {
    const closeBtn = e.target.closest(".pm-home-close-btn");
    if (closeBtn) { close(); return; }

    const journeyClose = e.target.closest(".pm-home-journey-close");
    if (journeyClose) { closeJourneyArea(); return; }

    const journeyBtn = e.target.closest("[data-pm-home-journey]");
    if (journeyBtn) {
      requestJourney(journeyBtn.getAttribute("data-pm-home-journey"));
      return;
    }

    // G7-J: 旅の行き止まりから「問いからの旅」タブ（ノード一覧）へ戻り、別の問いを選び直せる。
    const journeyRestart = e.target.closest("[data-pm-home-journey-restart]");
    if (journeyRestart) {
      closeJourneyArea();
      switchTab("journeys");
      return;
    }

    const tabBtn = e.target.closest("[data-pm-home-tab]");
    if (tabBtn) {
      switchTab(tabBtn.getAttribute("data-pm-home-tab"));
    }
  }

  // G3-P1: コース絞り込み select の変更（クライアント側フィルタのみ・再 fetch しない）。
  function onOverlayChange(e) {
    const sel = e.target.closest("[data-pm-home-course-filter]");
    if (!sel) return;
    state.courseFilter = sel.value || "";
    renderPanel();
  }

  function ensureOverlay() {
    if (state.overlayEl) return state.overlayEl;

    const overlay = document.createElement("div");
    overlay.id = OVERLAY_ID;
    overlay.className = "pm-home-overlay";
    overlay.hidden = true;
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "わたしの地図");

    const panel = document.createElement("div");
    panel.className = "pm-home-panel";

    const header = document.createElement("div");
    header.className = "pm-home-header";
    header.innerHTML =
      '<span class="pm-home-title">わたしの地図</span>' +
      '<button type="button" class="pm-home-close-btn" aria-label="閉じる">×</button>';
    panel.appendChild(header);

    const note = document.createElement("div");
    note.className = "pm-home-note";
    note.textContent = PRIVACY_NOTE;
    panel.appendChild(note);

    const tabs = document.createElement("div");
    tabs.className = "pm-home-tabs";
    panel.appendChild(tabs);

    const content = document.createElement("div");
    content.className = "pm-home-content";
    panel.appendChild(content);

    const journeyArea = document.createElement("div");
    journeyArea.className = "pm-home-journey-area";
    journeyArea.hidden = true;
    panel.appendChild(journeyArea);

    overlay.appendChild(panel);
    overlay.addEventListener("click", onOverlayClick);
    overlay.addEventListener("change", onOverlayChange);
    document.body.appendChild(overlay);

    state.overlayEl = overlay;
    state.tabsEl = tabs;
    state.contentEl = content;
    state.journeyAreaEl = journeyArea;
    return overlay;
  }

  // -------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------

  function init(deps) {
    state.deps = deps || {};
  }

  function open() {
    const t = token();
    if (!t) return; // fail-closed: 未ログインでは開かない
    ensureOverlay();
    state.lastFocus = document.activeElement;
    state.activeTab = "now";
    closeJourneyArea();
    state.overlayEl.hidden = false;
    renderPanel(); // キャッシュ未取得時は「読み込み中」表示（取得失敗と区別する）
    loadNetwork().then(() => renderPanel());
  }

  function close() {
    if (!state.overlayEl) return;
    state.overlayEl.hidden = true;
    closeJourneyArea();
    if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
  }

  function invalidate() {
    state.cache = null;
    state.activeTab = "now";
    state.courseFilter = "";
    closeJourneyArea();
    if (state.overlayEl && !state.overlayEl.hidden) renderPanel();
  }

  window.PersonalMapHome = {
    init,
    open,
    close,
    invalidate,
  };
})();
