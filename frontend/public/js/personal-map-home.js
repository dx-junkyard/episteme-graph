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
 * ビュー構成（提案書 §3.2 の A/C。B「このコースでの地図」は既存 personal-map.js が
 * 担うため本パネルには含めない。提案書 §3.2 D の月別グルーピングタブはオーナー裁定に
 * より非搭載）:
 *   いまの地図   — 直近の本人確定痕跡を現在地にした局所ビュー（デフォルト）
 *   問いからの旅 — 問い/引っかかりの新しい順カード列（上限20件・「すべて見る」は作らない）
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
    { key: "nearby", label: "いまここの周り" },
    { key: "now", label: "いまの地図" },
    { key: "journeys", label: "問いからの旅" },
  ];

  // 近傍関係ビュー（案E。正本: docs/features/personal_map_nearby_design.md）。
  // サーバが中心として解決できるアンカー種別（設計書 §3.2 の表）。ここに無い種別の
  // 痕跡は中心の選択肢に出さない（確実に available:false になる中心を押させない）。
  // "topic" は範囲モード（トピックアンカーの事実ベース粗表示。点ビューではなく
  // range_documents を返す。中心移動で点ビューへ遷移できる）。
  const NEARBY_ANCHOR_TYPES = ["component", "claim", "equation", "derivation_step", "stage", "topic"];

  const NEARBY_MODES = [
    { key: "near", label: "近く（前後1階層）" },
    { key: "root", label: "土台までの道筋" },
  ];

  // 縦軸の意味の説明（mode ごとに1行）。図の外に置き、図の中に凡例文を描かない。
  const NEARBY_AXIS_NOTE = {
    near: "上から下へ、前提 → いまここ → それに依存するもの の順に並んでいます。",
    root: "上から下へ、土台から順に前提が積み上がって「いまここ」に至ります。",
    // 範囲モード（topic アンカー）: 1点ではなく、そのトピックの教材が触れている理論構成を
    // 論文単位で並べる。縦の意味は root と同じ「土台から積み上がる」向きのまま。
    range: "上から下へ、土台から順に前提が積み上がっています。",
  };

  const state = {
    deps: {},
    overlayEl: null,
    contentEl: null,
    journeyAreaEl: null,
    tabsEl: null,
    activeTab: "nearby",
    // 近傍関係ビュー（案E）。中心は本人の痕跡ノード、mode は near/root。
    // centerComponentId は「中心の移動」（同じ document の main 層内のみ）。
    // nearbyCache はキー（node_id|mode|component_id）ごとの取得結果。ポーリングしない。
    nearbyCenterNodeId: null,
    nearbyMode: "near",
    nearbyCenterComponentId: "",
    nearbyCache: {},
    nearbyLoadingKey: null,
    // 「名前のある霧」（いまの地図タブ・現在地の隣にある骨格概念を名前だけ淡く見せる）。
    // キーは現在地ノードの id。ポーリングしない・タブ描画時に未取得なら1回だけ取りにいく。
    fogCache: {},
    fogLoadingId: null,
    cache: null, // { promise, data } — open() のたびに1回だけ fetch。invalidate() で破棄
    lastFetchFailed: false, // 「読み込み中」と「取得失敗」の表示を区別するためだけの一時フラグ
    lastFocus: null,
    // G3-P1: 「いまの地図」タブのコース絞り込み（既定=すべて。クライアント側フィルタのみ・
    // 再 fetch しない。地図オーバーレイに依存しない導線として本パネルに持たせる）。
    courseFilter: "",
    // N17: 訂正操作の一時フィードバック（personal-map.js の showTransientNote と同じ様式）。
    noteEl: null,
    noteTimer: null,
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

  // 「名前のある霧」（いまの地図タブ限定の好奇心装置）。現在地の骨格上の隣接概念を
  // 名前だけ淡く見せる。失敗・対象なしはすべて null に丸め、呼び出し側は静かに何も
  // 描かない（推薦・件数を伴わない装飾情報のため、journey/nearby のような
  // `_fetch_error` 区別は設けない — 霧が出ないこと自体が正しい挙動になる）。
  function fetchAtlasNeighbors(nodeId) {
    const t = token();
    if (!t || !nodeId) return Promise.resolve(null);
    return fetch(
      API_BASE + "/me/personal-network/atlas-neighbors?node_id=" + encodeURIComponent(nodeId),
      { headers: { Authorization: "Bearer " + t } }
    )
      .then((res) => {
        if (!res.ok) return null;
        return res.json();
      })
      .catch(() => null);
  }

  // 明示操作起点の1回（タブ描画時）に限る。キー（nodeId）単位で重複抑制するため
  // ポーリングにはならない。
  function requestFog(nodeId) {
    if (!nodeId) return;
    if (state.fogCache[nodeId] || state.fogLoadingId === nodeId) return;
    state.fogLoadingId = nodeId;
    fetchAtlasNeighbors(nodeId).then((dto) => {
      state.fogLoadingId = null;
      state.fogCache[nodeId] = dto || { available: false };
      if (state.activeTab === "now") renderPanel();
    });
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
    // T3: 「いまの地図」「問いからの旅」から「いまここの周り」への橋。サーバが中心として
    // 解決できるアンカーを持つノードにのみ出す（canBeNearbyCenter / nearbyCenters と同じ述語）。
    const nbCenterId = nearbyCenterIdForNode(node, data);
    if (nbCenterId) {
      html +=
        '<button type="button" class="pm-home-journey-btn" data-pm-home-nearby-jump="' +
        esc(nbCenterId) +
        '">この場所の周りを見る</button>';
    }
    // N17: 訂正操作「地図には反映しない」（提案書 §6）を最上位パネルからも使えるようにする。
    // 対象は tension/question のみ（reconstruction には出さない — コースビュー
    // personal-map.js の showPopup と同じ対象範囲）。node.id は interest_traces.id。
    if (node.node_kind === "tension" || node.node_kind === "question") {
      html +=
        '<button type="button" class="personal-map-exclude-btn" data-pm-home-map-exclude="' +
        esc(node.id) +
        '">地図には反映しない</button>';
    }
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
  // ビュー: いまここの周り（近傍関係ビュー。正本 docs/features/personal_map_nearby_design.md）
  //
  // 見せるのは2つの関係だけ（PMN-1: 位置に意味の無い配置をしない）:
  //   縦 = 依存の向き（上=これが前提にしていること / 下=これに依存していること）
  //   枠線 = 確かめられているか（実線=検証の記帳あり / 破線=記帳なし）
  // 座標・順位・距離を描かない。数値・件数・進捗を描かない（PMN-4）。
  // -------------------------------------------------------------------

  function nearbyKey(nodeId, mode, componentId) {
    return String(nodeId || "") + "|" + String(mode || "") + "|" + String(componentId || "");
  }

  // 明示操作（タブを開く / 中心を選ぶ / モード切替 / 中心移動）でのみ fetch する。
  // 404 は「その中心は本人のものではない」= fail-closed で null。その他の失敗は
  // `_fetch_error` で「空」と区別する（journey と同じ流儀）。
  function fetchNearby(nodeId, mode, componentId) {
    const t = token();
    if (!t || !nodeId) return Promise.resolve(null);
    let url =
      API_BASE +
      "/me/personal-network/nearby?node_id=" +
      encodeURIComponent(nodeId) +
      "&mode=" +
      encodeURIComponent(mode || "near");
    if (componentId) url += "&center_component_id=" + encodeURIComponent(componentId);
    return fetch(url, { headers: { Authorization: "Bearer " + t } })
      .then((res) => {
        if (res.status === 404) return null;
        if (!res.ok) return { _fetch_error: true };
        return res.json();
      })
      .catch(() => ({ _fetch_error: true }));
  }

  function requestNearby() {
    const nodeId = state.nearbyCenterNodeId;
    if (!nodeId) return;
    const key = nearbyKey(nodeId, state.nearbyMode, state.nearbyCenterComponentId);
    if (state.nearbyCache[key] || state.nearbyLoadingKey === key) return;
    state.nearbyLoadingKey = key;
    fetchNearby(nodeId, state.nearbyMode, state.nearbyCenterComponentId).then((dto) => {
      state.nearbyLoadingKey = null;
      state.nearbyCache[key] = dto || { _not_found: true };
      if (state.activeTab === "nearby") renderPanel();
    });
  }

  // サーバが中心として解決できるアンカーを持つか（T3: 「いまの地図」「問いからの旅」の
  // ノード行に「この場所の周りを見る」を出すかどうかの判定と、中心の選択肢の絞り込みが
  // 同じ述語を共有する — 二重実装しない）。
  function canBeNearbyCenter(node) {
    return !!(
      node &&
      node.anchor &&
      node.anchor.anchor_id &&
      NEARBY_ANCHOR_TYPES.indexOf(node.anchor.anchor_type) !== -1
    );
  }

  // 中心の選択肢: 本人の痕跡をアンカー単位に束ね、サーバが解決できる種別だけ残す。
  // 追加 fetch はしない（/api/me/personal-network の結果から導出するだけ）。
  function nearbyCenters(data) {
    const nodes = nodesByRecency(data).filter(canBeNearbyCenter);
    const seen = {};
    const out = [];
    nodes.forEach((n) => {
      const key = n.anchor.anchor_type + "|" + n.anchor.anchor_id;
      if (seen[key]) return;
      seen[key] = true;
      out.push({
        nodeId: n.id,
        // 範囲モード判定（topic アンカーかどうか）に使う。中心解決そのものはサーバ側が
        // 担う（点/範囲どちらの DTO が返るかもサーバ判断）— ここは UI の分岐用メタ情報。
        anchorType: n.anchor.anchor_type,
        anchorId: n.anchor.anchor_id,
        label: n.anchor.anchor_label || n.label || "",
        kinds: nodes
          .filter((m) => m.anchor.anchor_type + "|" + m.anchor.anchor_id === key)
          .map((m) => m.node_kind),
      });
    });
    return out;
  }

  // ノード自身の anchor が属する中心（nearbyCenters の代表 nodeId）を引く。中心の
  // dedupe（同じアンカーを共有する複数ノードのうち先頭だけが centers に載る）と食い違うと
  // renderNearby() 側の「未知の中心なら先頭に戻す」フォールバックに巻き取られてしまうため、
  // ジャンプ先には必ず centers 側の代表 nodeId を使う。
  function nearbyCenterIdForNode(node, data) {
    if (!canBeNearbyCenter(node)) return null;
    const centers = nearbyCenters(data);
    for (let i = 0; i < centers.length; i++) {
      if (
        centers[i].anchorType === node.anchor.anchor_type &&
        centers[i].anchorId === node.anchor.anchor_id
      ) {
        return centers[i].nodeId;
      }
    }
    return null;
  }

  function renderNearbyCenters(centers) {
    let html = '<div class="pm-home-nb-centers-head">自分の記録がある場所</div>';
    html += '<div class="pm-home-nb-centers">';
    centers.forEach((c) => {
      const on = c.nodeId === state.nearbyCenterNodeId;
      let marks = "";
      const usedKinds = {};
      c.kinds.forEach((k) => {
        if (usedKinds[k]) return;
        usedKinds[k] = true;
        marks += '<i class="personal-map-legend-swatch personal-map-dot-' + esc(k) + '"></i>';
      });
      html +=
        '<button type="button" class="pm-home-nb-center-chip' +
        (on ? " active" : "") +
        '" aria-pressed="' + on + '" data-pm-home-nearby-center="' + esc(c.nodeId) + '">' +
        marks + esc(c.label) + "</button>";
    });
    html += "</div>";
    return html;
  }

  function renderNearbyModes() {
    let html = '<div class="pm-home-nb-modes" role="group" aria-label="表示の範囲">';
    NEARBY_MODES.forEach((m) => {
      const on = state.nearbyMode === m.key;
      html +=
        '<button type="button" class="pm-home-nb-mode' +
        (on ? " active" : "") +
        '" aria-pressed="' + on + '" data-pm-home-nearby-mode="' + m.key + '">' +
        esc(m.label) + "</button>";
    });
    html += "</div>";
    return html;
  }

  // 枠線のクラス（PMN-3: 記帳の有無を主語にする。verification が無いノードは
  // 「検証済み」に見せない = 主張しない中立の見た目にする）。
  function nearbyNodeClass(node) {
    let cls = "pm-home-nb-node";
    if (node.is_center) cls += " is-center";
    if (node.mine && node.mine.length) cls += " has-mine";
    if (!node.verification) cls += " no-ledger";
    else if (node.verification.status === "untested" || node.verification.status === "unknown") {
      cls += " unverified";
    }
    return cls;
  }

  const NB = { w: 700, chipH: 38, rowH: 76, laneX: 84, pad: 12 };

  // extra は範囲ビューでのみ渡す claim_excerpt（切り詰め済み）。渡された場合はラベル単体より
  // 広い幅を許容する。点ビュー（renderNearbyGraph）は第2引数を渡さないため挙動は不変。
  function nearbyChipWidth(label, extra) {
    const base = Math.max(150, Math.min(300, 30 + String(label || "").length * 13));
    if (!extra) return base;
    return Math.max(base, Math.min(420, 40 + String(extra).length * 9));
  }

  // SVG <text> は CSS の text-overflow が効かず自動省略されないため、表示幅に収まる
  // 文字数へフロント側で切り詰める（全角文字主体の表示を想定した目安 45〜50 字程度）。
  // 範囲ビューのチップでのみ使う（点ビューは claim_excerpt を描画しない。理由は
  // renderNearbyGraph 側のコメント参照）。
  const NBR_EXCERPT_MAX_CHARS = 48;
  function nearbyTruncateExcerpt(text, maxChars) {
    const s = String(text || "").trim();
    const limit = maxChars || NBR_EXCERPT_MAX_CHARS;
    if (!s || s.length <= limit) return s;
    return s.slice(0, Math.max(1, limit - 1)) + "…";
  }

  function nearbyRows(dto) {
    const rows = [];
    if (dto.mode === "root") {
      (dto.root_path || []).forEach((n, i) => {
        rows.push({ label: i === 0 ? "土台" : "", nodes: [n] });
      });
    } else if ((dto.upstream || []).length) {
      rows.push({ label: "これが前提にしていること", nodes: dto.upstream });
    }
    rows.push({ label: "いまここ", nodes: [dto.center] });
    if ((dto.downstream || []).length) {
      rows.push({ label: "これに依存していること", nodes: dto.downstream });
    }
    return rows;
  }

  function renderNearbyGraph(dto) {
    const rows = nearbyRows(dto);
    const pos = {};
    let chips = "";
    let lanes = "";

    rows.forEach((row, ri) => {
      const y = NB.pad + 24 + ri * NB.rowH;
      if (row.label) {
        lanes +=
          '<line x1="' + (NB.laneX + 6) + '" y1="' + (y - 26) + '" x2="' + (NB.w - 10) +
          '" y2="' + (y - 26) + '" class="pm-home-nb-lane-line"/>' +
          '<text x="' + (NB.laneX - 6) + '" y="' + (y - 22) +
          '" class="pm-home-nb-lane-label" text-anchor="end">' + esc(row.label) + "</text>";
      }
      const widths = row.nodes.map((n) => nearbyChipWidth(n.label));
      let total = 0;
      widths.forEach((w) => { total += w + 14; });
      total -= 14;
      let x = NB.laneX + 8 + Math.max(0, (NB.w - NB.laneX - 20 - total) / 2);
      row.nodes.forEach((n, i) => {
        const w = widths[i];
        const cx = x + w / 2;
        pos[n.component_id] = { x: cx, top: y - NB.chipH / 2, bottom: y + NB.chipH / 2 };
        // 点ビューの横レーンチップは横に並ぶため個々の幅が狭い（laneX 起点で複数チップを
        // 同じ行に詰める）。claim_excerpt を足すと折り返し・重なりが起きるため、DTO に
        // 値が来ていても描画しない（excerpt は範囲ビュー = renderNearbyRangeGraph のみ）。
        chips +=
          '<g class="pm-home-nb-chip" tabindex="0" role="button" ' +
          'data-pm-home-nearby-move="' + esc(n.component_id) + '">' +
          "<title>" + esc(n.label) + "</title>" +
          '<rect x="' + x + '" y="' + (y - NB.chipH / 2) + '" width="' + w + '" height="' + NB.chipH +
          '" rx="9" class="' + nearbyNodeClass(n) + '"/>' +
          '<text x="' + (x + 12) + '" y="' + (y + (n.verification ? -2 : 5)) +
          '" class="pm-home-nb-node-label">' + esc(n.label) + "</text>";
        if (n.verification) {
          chips +=
            '<text x="' + (x + 12) + '" y="' + (y + 12) +
            '" class="pm-home-nb-node-verif">' + esc(n.verification.label) + "</text>";
        }
        (n.mine || []).forEach((m, k) => {
          const mx = x + w - 13 - k * 13;
          chips += nearbyMarker(m.kind, mx, y - NB.chipH / 2 + 12);
        });
        chips += "</g>";
        x += w + 14;
      });
    });

    let links = "";
    (dto.edges || []).forEach((e) => {
      const a = pos[e.from];
      const b = pos[e.to];
      if (!a || !b || a.bottom >= b.top) return;
      links +=
        '<path class="pm-home-nb-link" d="M ' + a.x + " " + a.bottom +
        " C " + a.x + " " + (a.bottom + 20) + ", " + b.x + " " + (b.top - 20) +
        ", " + b.x + " " + b.top + '"/>';
    });

    const h = NB.pad * 2 + 24 + rows.length * NB.rowH - 30;
    return (
      '<div class="pm-home-nb-graph"><svg viewBox="0 0 ' + NB.w + " " + h +
      '" role="img" aria-label="いまここの前後の関係">' + lanes + links + chips + "</svg></div>"
    );
  }

  // 記号は atlas.css の .personal-map-dot-* と同じ形・色を共有する（コースビューと統一）。
  function nearbyMarker(kind, x, y) {
    const r = 4.4;
    const cls = "personal-map-dot personal-map-dot-" + kind;
    if (kind === "question") {
      return '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" class="' + cls + '"/>';
    }
    if (kind === "reconstruction") {
      return (
        '<rect x="' + (x - r) + '" y="' + (y - r) + '" width="' + r * 2 + '" height="' + r * 2 +
        '" class="' + cls + '"/>'
      );
    }
    return (
      '<polygon points="' + x + "," + (y - r) + " " + (x + r) + "," + (y + r) + " " +
      (x - r) + "," + (y + r) + '" class="' + cls + '"/>'
    );
  }

  function renderNearbyLegend(dto, isRange) {
    // 台帳の記帳が1件も無いときは検証の区別ごと出さない（PMN-7 fail-closed）。
    let html = '<div class="pm-home-nb-legend">';
    if (dto.ledger_available) {
      html +=
        '<span class="pm-home-nb-lg"><i class="pm-home-nb-sw solid"></i>検証の記帳がある</span>' +
        '<span class="pm-home-nb-lg"><i class="pm-home-nb-sw dash"></i>このコーパスの中では検証記録がない</span>';
    }
    if (isRange && !dto.range_fallback) {
      // 範囲モード固有の対比（数値・件数は出さない。PMN-4）。フォールバック
      // （トピック⇄claim の対応が未記録）では touched が構造的に存在しないため、
      // 「触れている場所」の凡例ごと出さない（凡例が事実より多くを主張しない）。
      html +=
        '<span class="pm-home-nb-lg"><i class="pm-home-nb-sw range-touched"></i>この話題が触れている場所</span>' +
        '<span class="pm-home-nb-lg"><i class="pm-home-nb-sw range-untouched"></i>この論文のその他の理論構成</span>';
    }
    html +=
      '<span class="pm-home-nb-lg"><i class="personal-map-legend-swatch personal-map-dot-question"></i>問い</span>' +
      '<span class="pm-home-nb-lg"><i class="personal-map-legend-swatch personal-map-dot-tension"></i>引っかかり</span>' +
      '<span class="pm-home-nb-lg"><i class="personal-map-legend-swatch personal-map-dot-reconstruction"></i>再構成</span>';
    html += "</div>";
    return html;
  }

  // head は見出しのみ差し替え可能（事実文そのものはサーバが正本。ここでは組み立てない）。
  function renderNearbyFacts(dto, head) {
    const facts = dto.facts || [];
    if (!facts.length) return "";
    let html =
      '<div class="pm-home-nb-facts"><div class="pm-home-nb-facts-head">' +
      esc(head || "この場所について") + "</div>";
    facts.forEach((f) => { html += '<p class="pm-home-nb-fact">' + esc(f) + "</p>"; });
    html += "</div>";
    return html;
  }

  // 中心ノードにある本人の記録は、既存のノード行（旅・訂正操作つき）をそのまま再利用する。
  function renderNearbyMine(dto, data) {
    const mine = (dto.center && dto.center.mine) || [];
    if (!mine.length) return "";
    const byId = {};
    ((data && data.nodes) || []).forEach((n) => { byId[n.id] = n; });
    let html = '<div class="pm-home-related-heading">『' + esc(dto.center.label) + "』にある自分の記録</div>";
    mine.forEach((m) => {
      const node = byId[m.trace_id];
      if (node) { html += nodeRowHtml(node, data); return; }
      html +=
        '<div class="pm-home-node-row"><div class="pm-home-node-top">' +
        '<span class="pm-home-node-kind">' + esc(m.kind_label || kindLabel(m.kind)) + "</span>" +
        '<span class="pm-home-node-label">' + esc(m.text || "") + "</span></div></div>";
    });
    return html;
  }

  // 範囲モード（topic アンカー）用のグラフ: 1論文=1グラフ・1ノード=1行（レーンラベル・
  // 「いまここ」ラベルは無い — 中心の概念が無いため）。並び順は DTO の nodes 順そのまま
  // （サーバが土台から積み上がる順で並べている前提。位置に意味を持たせるのはサーバ側の
  // 決定論導出のみで、フロントは並び替えない = PMN-1）。
  // chipH は行内に描く文字行数（label / claim_excerpt / verification のうち存在するもの）
  // に応じて可変（1行=24 / 2行=34（従来値） / 3行=46）。行間の余白 gap は固定 20px。
  const NBR = { w: 700, chipH: 34, pad: 10, gap: 20 };

  function nearbyRangeChipHeight(lineCount) {
    if (lineCount <= 1) return 24;
    if (lineCount === 2) return 34;
    return 46;
  }

  // 各行の baseline y 座標（チップ中心 cy・高さ chipH から算出）。行の積み順は常に
  // label → claim_excerpt → verification（存在する行だけを詰めて描く）。
  function nearbyRangeLineYs(cy, lineCount) {
    if (lineCount <= 1) return [cy + 4];
    if (lineCount === 2) return [cy - 1, cy + 11];
    return [cy - 11, cy + 2, cy + 15];
  }

  function renderNearbyRangeGraph(doc) {
    const nodes = (doc && doc.nodes) || [];
    if (!nodes.length) return "";
    const pos = {};
    let chips = "";
    let top = NBR.pad; // 次に描くチップの上端
    nodes.forEach((n) => {
      // claim_excerpt: その論文がその理論構成で言っていることの逐語（最大80字、サーバ側で
      // 切り詰め済み）。SVG text は自動省略されないため、表示幅に収まる文字数へさらに
      // 切り詰める（範囲ビューのチップのみ — 点ビューは横に狭いため描画しない）。
      const excerpt = n.claim_excerpt ? nearbyTruncateExcerpt(n.claim_excerpt) : "";
      const lineCount = 1 + (excerpt ? 1 : 0) + (n.verification ? 1 : 0);
      const chipH = nearbyRangeChipHeight(lineCount);
      const w = nearbyChipWidth(n.label, excerpt);
      const x = (NBR.w - w) / 2;
      const cy = top + chipH / 2;
      pos[n.component_id] = { x: x + w / 2, top: cy - chipH / 2, bottom: cy + chipH / 2 };
      let cls = nearbyNodeClass(n);
      if (!n.touched) cls += " untouched";
      // title はホバーで全文を読めるよう label + excerpt 全文（切り詰め前）を併記する。
      const titleText = n.claim_excerpt ? n.label + "：" + n.claim_excerpt : n.label;
      chips +=
        '<g class="pm-home-nb-chip" tabindex="0" role="button" ' +
        'data-pm-home-nearby-move="' + esc(n.component_id) + '">' +
        "<title>" + esc(titleText) + "</title>" +
        '<rect x="' + x + '" y="' + (cy - chipH / 2) + '" width="' + w + '" height="' + chipH +
        '" rx="8" class="' + cls + '"/>';
      const lineYs = nearbyRangeLineYs(cy, lineCount);
      let li = 0;
      chips +=
        '<text x="' + (x + 10) + '" y="' + lineYs[li++] +
        '" class="pm-home-nb-node-label">' + esc(n.label) + "</text>";
      if (excerpt) {
        chips +=
          '<text x="' + (x + 10) + '" y="' + lineYs[li++] +
          '" class="pm-home-nb-node-excerpt">' + esc(excerpt) + "</text>";
      }
      if (n.verification) {
        chips +=
          '<text x="' + (x + 10) + '" y="' + lineYs[li++] +
          '" class="pm-home-nb-node-verif">' + esc(n.verification.label) + "</text>";
      }
      (n.mine || []).forEach((m, k) => {
        const mx = x + w - 12 - k * 13;
        chips += nearbyMarker(m.kind, mx, cy - chipH / 2 + 11);
      });
      chips += "</g>";
      top = cy + chipH / 2 + NBR.gap;
    });

    let links = "";
    (doc.edges || []).forEach((e) => {
      const a = pos[e.from];
      const b = pos[e.to];
      if (!a || !b || a.bottom >= b.top) return;
      links +=
        '<path class="pm-home-nb-link" d="M ' + a.x + " " + a.bottom +
        " C " + a.x + " " + (a.bottom + 16) + ", " + b.x + " " + (b.top - 16) +
        ", " + b.x + " " + b.top + '"/>';
    });

    const h = top - NBR.gap + NBR.pad;
    return (
      '<div class="pm-home-nb-graph pm-home-nb-range-graph"><svg viewBox="0 0 ' + NBR.w + " " + h +
      '" role="img" aria-label="この話題が触れている理論構成">' + links + chips + "</svg></div>"
    );
  }

  // 選択中の中心（topic アンカー）に紐づく本人の記録。中心ノードという概念が無い範囲
  // モードでは、代わりに「このトピックでの自分の記録」として anchor_id 一致で束ねる。
  function renderNearbyRangeMine(data, anchorId) {
    const nodes = ((data && data.nodes) || []).filter(
      (n) => n.anchor && n.anchor.anchor_type === "topic" && n.anchor.anchor_id === anchorId
    );
    if (!nodes.length) return "";
    nodes.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    let html = '<div class="pm-home-related-heading">このトピックでの自分の記録</div>';
    nodes.forEach((n) => { html += nodeRowHtml(n, data); });
    return html;
  }

  function renderNearbyRange(dto, data, anchorId) {
    let html = '<p class="pm-home-nb-axis">' + esc(NEARBY_AXIS_NOTE.range || "") + "</p>";
    // フォールバック（トピック⇄claim の対応が未記録）のときは「触れている」と
    // 主張しない見出しに切り替える（事実文との整合。出所の正直さ）。
    html +=
      '<div class="pm-home-nb-range-head">' +
      (dto.range_fallback ? "コースのソース論文の理論構成" : "この話題が触れている範囲") +
      "</div>";
    const docs = dto.range_documents || [];
    docs.forEach((doc) => {
      if (doc.title) {
        html += '<div class="pm-home-nb-doc-head">' + esc(doc.title) + "</div>";
      }
      html += renderNearbyRangeGraph(doc);
    });
    html += renderNearbyLegend(dto, true);
    html += renderNearbyFacts(dto);
    html += renderNearbyRangeMine(data, anchorId);
    return html;
  }

  function renderNearby(data) {
    const centers = nearbyCenters(data);
    if (!centers.length) {
      return (
        '<p class="pm-home-empty">' +
        "まだ論文の理論構成に結びついた記録がありません。教材のどこについての記録か決めると、ここに現れます。" +
        "</p>"
      );
    }
    const known = {};
    centers.forEach((c) => { known[c.nodeId] = true; });
    if (!state.nearbyCenterNodeId || !known[state.nearbyCenterNodeId]) {
      state.nearbyCenterNodeId = centers[0].nodeId;
      state.nearbyCenterComponentId = "";
    }
    let currentCenter = null;
    centers.forEach((c) => {
      if (c.nodeId === state.nearbyCenterNodeId) currentCenter = c;
    });
    // 範囲モード（topic アンカー）は中心移動前は near/root の区別が無い（そもそも「中心」
    // という概念が無い事実ベースの粗表示）ため、モード切替を出さない。中心移動後（点
    // ビューへ遷移した後）は通常どおり出す（設計書 §5「中心移動」）。
    const hideModes =
      !!currentCenter && currentCenter.anchorType === "topic" && !state.nearbyCenterComponentId;

    let html = renderNearbyCenters(centers);
    if (!hideModes) html += renderNearbyModes();
    const key = nearbyKey(state.nearbyCenterNodeId, state.nearbyMode, state.nearbyCenterComponentId);
    const dto = state.nearbyCache[key];
    if (!dto) {
      html += loadingHtml();
      return html;
    }
    if (dto._fetch_error) return html + failHtml();
    if (dto._not_found || dto.available === false) {
      html +=
        '<p class="pm-home-empty">' +
        esc((dto && dto.notice) || "この記録は、まだ論文の理論構成に結びついていません。") +
        "</p>";
      // 行き止まりにしない: サーバが添えた出口案内の事実文をそのまま描く（文言はサーバ正本）。
      html += renderNearbyFacts(dto, "この記録について");
      return html;
    }
    if (dto.mode === "range") {
      html += renderNearbyRange(dto, data, currentCenter ? currentCenter.anchorId : "");
      return html;
    }
    html += '<p class="pm-home-nb-axis">' + esc(NEARBY_AXIS_NOTE[dto.mode] || "") + "</p>";
    html += renderNearbyGraph(dto);
    html += renderNearbyLegend(dto);
    html += renderNearbyFacts(dto);
    html += renderNearbyMine(dto, data);
    return html;
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

  // 「名前のある霧」— 現在地の直後にだけ描く。available:false・neighbors 空・未取得は
  // 何も描かない（fail-closed。エラー文言も出さない）。件数・矢印・推薦文言は付けない。
  function renderFog(current) {
    const dto = state.fogCache[(current && current.id) || ""];
    if (!dto || dto.available !== true) return "";
    const neighbors = dto.neighbors || [];
    if (!neighbors.length) return "";
    const here = dto.here || {};
    const hereLabel = here.label || "";
    const hereRegion = here.region_label || "";

    let html = '<div class="pm-home-fog">';
    html += '<div class="pm-home-fog-head">この場所の隣にあるもの</div>';
    html +=
      '<div class="pm-home-fog-here">いまの場所：『' +
      esc(hereLabel) +
      "』" +
      (hereRegion ? "（" + esc(hereRegion) + "）" : "") +
      "</div>";

    // edge 群 → sibling 群の順（それ以外の relation 値は sibling 群に合流させ、
    // 情報を落とさない）。
    const edges = neighbors.filter((n) => n.relation === "edge");
    const siblings = neighbors.filter((n) => n.relation !== "edge");
    const ordered = edges.concat(siblings);

    html += '<div class="pm-home-fog-chips">';
    ordered.forEach((n) => {
      html += '<span class="pm-home-fog-chip">' + esc(n.label || "");
      if (n.region_label && n.region_label !== hereRegion) {
        html += '<span class="pm-home-fog-region">・' + esc(n.region_label) + "</span>";
      }
      html += "</span>";
    });
    html += "</div></div>";
    return html;
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
    html += renderFog(current);

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
    // T4: nodeRowHtml() を再利用する（「地図には反映しない」「この場所の周りを見る」が
    // 旅タブでも「いまの地図」タブと一貫して出るようにする。自前の HTML 組み立てはやめる）。
    let html = '<div class="pm-home-journeys-list">';
    shown.forEach((n) => { html += nodeRowHtml(n, data); });
    html += "</div>";
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
    if (!data) {
      // 対象なし（404）なら何も出さない（fail-closed。エラーバナーは出さない）
      area.hidden = true;
      area.innerHTML = "";
      return;
    }
    if (!Array.isArray(data.steps) || !data.steps.length) {
      // T5: steps が空でも無言にしない。サーバが notice/facts を添えていれば、
      // nearby の「対象なし」分岐と同じ見出し・同じ描画部品（renderNearbyFacts）で
      // 「この記録について」を表示する。notice が無い旧型応答は従来どおり非表示
      // （後方互換）。
      if (!data.notice) {
        area.hidden = true;
        area.innerHTML = "";
        return;
      }
      let emptyHtml = '<button type="button" class="pm-home-journey-close" aria-label="閉じる">×</button>';
      emptyHtml += '<div class="pm-home-journey-heading">旅の経路</div>';
      emptyHtml += '<p class="pm-home-empty">' + esc(data.notice) + "</p>";
      emptyHtml += renderNearbyFacts({ facts: data.facts || [] }, "この記録について");
      area.innerHTML = emptyHtml;
      area.hidden = false;
      if (typeof area.scrollIntoView === "function") area.scrollIntoView({ block: "nearest" });
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
  // N17: 本人による訂正「地図には反映しない」（提案書 §6。コースビュー
  // personal-map.js の requestMapExclude と同じ API・同じフィードバック文言）。
  // 痕跡行そのものは削除しない（P4）— 地図の導出から外れるだけの状態遷移で、
  // dismiss（候補の当落判定）とは独立。操作後はキャッシュを破棄して再取得・再描画する。
  // -------------------------------------------------------------------

  function showTransientNote(text) {
    const el = state.noteEl;
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    if (state.noteTimer) clearTimeout(state.noteTimer);
    state.noteTimer = setTimeout(() => {
      el.hidden = true;
      el.textContent = "";
    }, 4000);
  }

  function requestMapExclude(traceId) {
    const t = token();
    if (!t || !traceId) return;
    fetch(API_BASE + "/learning/traces/" + encodeURIComponent(traceId) + "/map-exclude", {
      method: "POST",
      headers: { Authorization: "Bearer " + t },
    })
      .then((res) => {
        if (!res.ok) throw new Error("map-exclude " + res.status);
        return res.json();
      })
      .then(() => {
        // 除外したノードを起点にした旅カードが残ると stale になるため破棄する
        closeJourneyArea();
        // 再取得・再描画（PN-2: 導出はサーバ状態が正。除外済みノードは次の導出から消える）
        state.cache = null;
        renderPanel(); // キャッシュ破棄直後は「読み込み中」表示
        loadNetwork().then(() => renderPanel());
        showTransientNote("地図には反映しません（問いの軌跡には残ります）");
      })
      .catch(() => {}); // fail-closed: 失敗時は何も出さない（エラーバナーは出さない）
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
    if (state.activeTab === "nearby") return renderNearby(data);
    if (state.activeTab === "journeys") return renderJourneys(data);
    return renderNow(data);
  }

  function renderPanel() {
    if (state.tabsEl) state.tabsEl.innerHTML = renderTabsBar();
    if (state.contentEl) state.contentEl.innerHTML = renderTabContent();
    // 近傍関係ビューは中心が決まってからサーバへ取りにいく（描画後に1回だけ。
    // requestNearby がキー単位で重複を抑えるのでポーリングにはならない）。
    if (state.activeTab === "nearby" && cachedData()) requestNearby();
    // 「名前のある霧」も同じ流儀: いまの地図タブ表示中・現在地が決まっていて未取得なら
    // 1回だけ取りにいく（コースフィルタで current が変われば別 nodeId として取得する）。
    if (state.activeTab === "now") {
      const data = cachedData();
      if (data) {
        const nodes = filteredNodes(data);
        if (nodes.length) requestFog(nodes[0].id);
      }
    }
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

    // N17: 「地図には反映しない」（tension/question のみ。nodeRowHtml が付与する）
    const excludeBtn = e.target.closest("[data-pm-home-map-exclude]");
    if (excludeBtn) {
      requestMapExclude(excludeBtn.getAttribute("data-pm-home-map-exclude"));
      return;
    }

    // G7-J: 旅の行き止まりから「問いからの旅」タブ（ノード一覧）へ戻り、別の問いを選び直せる。
    // T6: switchTab() は同タブでは早期 return するため、旅タブを表示中に押されたときは
    // タブ切替の代わりに起点リスト先頭へフォーカスを移す（何も起きないノーオペを解消する）。
    const journeyRestart = e.target.closest("[data-pm-home-journey-restart]");
    if (journeyRestart) {
      closeJourneyArea();
      if (state.activeTab === "journeys") {
        const list = state.contentEl && state.contentEl.querySelector(".pm-home-journeys-list");
        if (list && typeof list.scrollIntoView === "function") list.scrollIntoView({ block: "nearest" });
      } else {
        switchTab("journeys");
      }
      return;
    }

    // 近傍関係ビュー: 中心の選択 / モード切替 / 中心の移動（いずれも明示操作でのみ取得）
    const nbCenter = e.target.closest("[data-pm-home-nearby-center]");
    if (nbCenter) {
      state.nearbyCenterNodeId = nbCenter.getAttribute("data-pm-home-nearby-center");
      state.nearbyCenterComponentId = "";
      renderPanel();
      return;
    }
    const nbMode = e.target.closest("[data-pm-home-nearby-mode]");
    if (nbMode) {
      state.nearbyMode = nbMode.getAttribute("data-pm-home-nearby-mode");
      renderPanel();
      return;
    }
    const nbMove = e.target.closest("[data-pm-home-nearby-move]");
    if (nbMove) {
      const target = nbMove.getAttribute("data-pm-home-nearby-move");
      state.nearbyCenterComponentId = target || "";
      renderPanel();
      return;
    }

    // T3: 「いまの地図」「問いからの旅」のノード行から「いまここの周り」へ橋渡す。
    // switchTab() は同タブでは早期 return するため使わず、常に中心を選び直して再描画する。
    const nbJump = e.target.closest("[data-pm-home-nearby-jump]");
    if (nbJump) {
      closeJourneyArea();
      state.nearbyCenterNodeId = nbJump.getAttribute("data-pm-home-nearby-jump");
      state.nearbyCenterComponentId = "";
      state.activeTab = "nearby";
      renderPanel();
      return;
    }

    const tabBtn = e.target.closest("[data-pm-home-tab]");
    if (tabBtn) {
      switchTab(tabBtn.getAttribute("data-pm-home-tab"));
    }
  }

  // G3-P1: コース絞り込み select の変更（クライアント側フィルタのみ・再 fetch しない）。
  // 近傍関係ビューのグラフチップは SVG の <g>（<button> ではない）ため、Enter/Space が
  // click になる保証がない。personal-map.js のマーカーと同じ流儀でキー操作を補う。
  function onOverlayKeydown(e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    const chip = e.target.closest && e.target.closest("[data-pm-home-nearby-move]");
    if (!chip) return;
    e.preventDefault();
    state.nearbyCenterComponentId = chip.getAttribute("data-pm-home-nearby-move") || "";
    renderPanel();
  }

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

    // N17: 訂正操作の一時フィードバック（数秒で自動的に隠れる。スタイルは
    // personal-map.js と同じ .personal-map-transient-note を再利用する）
    const transientNote = document.createElement("div");
    transientNote.className = "personal-map-transient-note";
    transientNote.hidden = true;
    panel.appendChild(transientNote);

    const journeyArea = document.createElement("div");
    journeyArea.className = "pm-home-journey-area";
    journeyArea.hidden = true;
    panel.appendChild(journeyArea);

    overlay.appendChild(panel);
    overlay.addEventListener("click", onOverlayClick);
    overlay.addEventListener("change", onOverlayChange);
    overlay.addEventListener("keydown", onOverlayKeydown);
    document.body.appendChild(overlay);

    state.overlayEl = overlay;
    state.tabsEl = tabs;
    state.contentEl = content;
    state.noteEl = transientNote;
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
    // 開くたびに先頭タブ（いまここの周り）へ戻す。前回の選択を持ち越さない（PN-2 と同族:
    // 表示状態を保存しない）。中心の選択・モードも初期化して、明示操作から取り直す。
    state.activeTab = TABS[0].key;
    state.nearbyCenterNodeId = null;
    state.nearbyCenterComponentId = "";
    state.nearbyMode = "near";
    closeJourneyArea();
    state.overlayEl.hidden = false;
    renderPanel(); // キャッシュ未取得時は「読み込み中」表示（取得失敗と区別する）
    loadNetwork().then(() => renderPanel());
  }

  function close() {
    if (!state.overlayEl) return;
    state.overlayEl.hidden = true;
    closeJourneyArea();
    // N17: 一時フィードバックも破棄する（次回 open 時に古い文言を残さない）
    if (state.noteTimer) clearTimeout(state.noteTimer);
    if (state.noteEl) {
      state.noteEl.hidden = true;
      state.noteEl.textContent = "";
    }
    if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
  }

  function invalidate() {
    state.cache = null;
    state.activeTab = "nearby";
    state.courseFilter = "";
    // PN-1（本人のみ可視）: ログアウト・別ユーザーのログインを跨いで前ユーザーの
    // 取得結果が残らないよう、コース横断の派生キャッシュも合わせて破棄する。
    state.nearbyCache = {};
    state.fogCache = {};
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
