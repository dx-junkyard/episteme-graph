/* コーパス回遊層（学習者フロント）— 「論文の海」
 *
 * 設計の正本: docs/features/corpus_roaming_design.md
 *   §4.2（Phase A UI）/ §5（Phase B: コース無し論文議論）/ §6（Phase C: 地図の端）/
 *   §7（Phase D: 関心信号）。参照する不変条項（同 §2）:
 *     CR2 既存のコース学習を壊さない — 回遊はサイドバーに並置される別の入口であり、
 *         既存入口の置き換え・自動遷移をしない。atlas-overlay / landscape-layer の
 *         コース前提コードには触らない（このモジュールは自前の描画を持つ）。
 *     CR3 数値を見せない — weight / confidence / 件数 / 類似度を描かない。配置には
 *         サーバが返す出所ラベル（source_label）をそのまま添える。
 *     CR4 閉世界の正直さ — 端の文言（fact_line）はサーバが唯一の正本。ここでは
 *         組み立てず、受け取った文字列をそのまま表示する。
 *     CR5 好奇心の文法 — バッジ・督促・自動表示をしない。ポーリングしない。
 *         「この先を知りたい」を押しても、見えている内容は変わらない。
 *     CR6 学習者を監視しない — 端への関心は明示タップのときだけ送る。
 *     CR7 学習者起点で外部 API を呼ばない — 呼ぶのは自サーバの読み取り API だけ。
 *     CR8 情報を落とさない — 取り消しは withdraw API（状態遷移）で行う。
 *
 * 公開契約 window.CorpusSea（呼び出し側は app.js。名前・引数は固定）:
 *   init(deps)   — 一度だけ。deps は将来の DI 用（現状は未使用）
 *   open()       — サイドバーの「論文の海」ボタンから。自動では絶対に呼ばない（CR5）
 *   close()      — オーバーレイを閉じる
 *   invalidate() — コース切替・ログアウト時。取得結果・議論の下書きを破棄する
 *
 * 描画資産についての決定（設計 §11 の「コース非依存化がどこまで素直か」への答え）:
 *   atlas-overlay.js は単一の #atlas-overlay を持ち、詳細パネル（atlas-panel.js）と
 *   landscape-layer.js が window.AtlasContext.courseId と学習者のコース状態に結び
 *   ついている。回遊のためにそこへ手を入れるとコースの地図の挙動が変わりうるため
 *   （CR2）、本モジュールは L1 骨格（GET /api/atlas?cartridge=）だけを読み、自前の
 *   簡易 SVG で「領域 + 論文ドット」を描く。atlas-data.js を経由しないのは、
 *   AtlasData.load() が window.AtlasContext.courseId を優先するため、コース選択中は
 *   分野を指定しても**コースの地図**が返ってしまうから（既存挙動は変更しない）。
 */
(function () {
  "use strict";

  const API = "/api";

  // 1つの概念ノードに重ねる論文マーカーの上限。溢れた分は「…」に畳む（件数は
  // 出さない — CR3。全件は右の論文リストで読める）。
  const MAX_MARKERS_PER_NODE = 4;
  const MARKER_STEP = 14;
  const CONCEPT_OFFSET = [16, -14];

  // 回答の出所（grounding）と根拠の格（tier）の表示語彙。app.js の GROUNDING_META /
  // TIER_META と同じ語彙を使う（学習者から見て同じものが同じ名前で見える必要がある）。
  // モジュールを自己完結させるため、discuss.js の DISCUSS_TOPIC_ID と同様に意図的な複製。
  const GROUNDING_META = {
    course_material: { label: "教材から回答", cls: "grounding-course", icon: "📘" },
    other_material: { label: "別の資料から回答", cls: "grounding-other", icon: "📄" },
    model_generated: { label: "AIの一般知識（出典なし）", cls: "grounding-model", icon: "💭" },
  };
  const TIER_META = {
    approved: { label: "承認済み", cls: "tier-approved" },
    source: { label: "原典", cls: "tier-source" },
    out_of_source: { label: "参考", cls: "tier-out" },
  };

  // document 直付け議論のスコープ。値はサーバ契約のまま（course_sources = その論文のみ）。
  // ラベルだけを document 文脈の事実に合わせる（設計 §5.2）。
  const SCOPE_OPTIONS = [
    { value: "course_sources", label: "この論文のみ" },
    { value: "all_visible", label: "閲覧できる周辺資料まで" },
  ];

  const state = {
    deps: {},
    overlayEl: null,
    lastFocus: null,
    view: "map", // "map" | "discuss"
    domainsLoaded: false,
    domains: [],
    domainKey: "",
    landscape: null,
    skeleton: null,
    documents: [],
    selectedDocumentId: "",
    loading: false,
    // 端への関心（Phase D）。この画面で押した分の trace_id を持つだけで、
    // 押した結果によって地図・論文リスト・端の文言は一切変わらない（CR5）。
    interest: {},
    discuss: {
      documentId: "",
      title: "",
      scope: "course_sources",
      messages: [],
      sending: false,
      editingMessageId: "",
      historyLoaded: false,
    },
  };

  // -------------------------------------------------------------------
  // 取得（すべて読み取り専用。ポーリングしない — CR5 / CR9）
  // -------------------------------------------------------------------

  function token() {
    try {
      return localStorage.getItem("eg_token") || null;
    } catch (e) {
      return null;
    }
  }

  function apiFetch(path, opts) {
    opts = opts || {};
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    const t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    return fetch(API + path, Object.assign({}, opts, { headers: headers }));
  }

  // 取得失敗・404 はすべて null に丸める。呼び出し側はその区画ごと出さない
  // （fail-closed。代替データ・作り話で埋めない）。
  async function getJson(path) {
    try {
      const res = await apiFetch(path);
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      return null;
    }
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // -------------------------------------------------------------------
  // オーバーレイの骨組み
  // -------------------------------------------------------------------

  function ensureOverlay() {
    if (state.overlayEl) return state.overlayEl;
    const ov = document.createElement("div");
    ov.className = "corpus-sea-overlay";
    ov.id = "corpus-sea-overlay";
    ov.hidden = true;
    ov.innerHTML =
      '<div class="corpus-sea-sheet" role="dialog" aria-modal="true" aria-labelledby="corpus-sea-title">' +
        '<div class="corpus-sea-head">' +
          '<span class="corpus-sea-title" id="corpus-sea-title">🌊 論文の海</span>' +
          '<div class="corpus-sea-domain-picker" id="corpus-sea-domains"></div>' +
          '<button type="button" class="corpus-sea-close" id="corpus-sea-close" title="閉じる" aria-label="閉じる">×</button>' +
        '</div>' +
        '<div class="corpus-sea-note" id="corpus-sea-note"></div>' +
        '<div class="corpus-sea-body" id="corpus-sea-map-view">' +
          '<div class="corpus-sea-mapcol">' +
            '<div class="corpus-sea-map" id="corpus-sea-map"></div>' +
            '<div class="corpus-sea-edges" id="corpus-sea-edges"></div>' +
          '</div>' +
          '<div class="corpus-sea-side">' +
            '<div class="corpus-sea-papers" id="corpus-sea-papers"></div>' +
            '<div class="corpus-sea-detail" id="corpus-sea-detail"></div>' +
          '</div>' +
        '</div>' +
        '<div class="corpus-sea-discuss" id="corpus-sea-discuss-view" hidden>' +
          '<div class="corpus-sea-discuss-bar" id="corpus-sea-discuss-bar"></div>' +
          '<div class="corpus-sea-opening" id="corpus-sea-opening"></div>' +
          '<div class="corpus-sea-chat" id="corpus-sea-chat"></div>' +
          '<div class="corpus-sea-composer">' +
            '<input type="text" id="corpus-sea-input" placeholder="この論文について考えを書く…（Enterで送信）">' +
            '<button type="button" class="corpus-sea-send-btn" id="corpus-sea-send">送信</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(ov);
    state.overlayEl = ov;
    bindShell(ov);
    return ov;
  }

  function bindShell(ov) {
    ov.querySelector("#corpus-sea-close").addEventListener("click", close);
    ov.addEventListener("mousedown", function (e) {
      if (e.target === ov) close(); // シート外クリックで閉じる
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && state.overlayEl && !state.overlayEl.hidden) close();
    });
    const input = ov.querySelector("#corpus-sea-input");
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") submitDiscussInput();
    });
    ov.querySelector("#corpus-sea-send").addEventListener("click", submitDiscussInput);
  }

  function el(id) {
    return state.overlayEl ? state.overlayEl.querySelector("#" + id) : null;
  }

  function setView(view) {
    state.view = view;
    const mapView = el("corpus-sea-map-view");
    const discussView = el("corpus-sea-discuss-view");
    if (mapView) mapView.hidden = view !== "map";
    if (discussView) discussView.hidden = view !== "discuss";
    const picker = el("corpus-sea-domains");
    if (picker) picker.hidden = view !== "map";
  }

  // -------------------------------------------------------------------
  // Phase A: ドメイン選択
  // -------------------------------------------------------------------

  async function loadDomains() {
    const data = await getJson("/learning/corpus/domains");
    if (!data) {
      // fail-closed: 取得できなかったことを「無い」と言い換えない（CR4）。
      // 何も出さず、次に開いたときに取り直せるよう未取得のままにする。
      state.domains = [];
      state.domainsLoaded = false;
      renderNote("");
      renderDomains();
      return;
    }
    const list = Array.isArray(data.domains) ? data.domains : [];
    state.domains = list;
    state.domainsLoaded = true;
    if (!list.length) {
      renderNote("いま閲覧できる分野の地図はありません。");
      renderDomains();
      return;
    }
    renderNote("");
    renderDomains();
    // 1件だけなら選ぶ手間を置かない（画面を1操作分短くするだけで、内容は変わらない）。
    if (list.length === 1) await selectDomain(domainKeyOf(list[0]));
  }

  function domainKeyOf(entry) {
    return (entry && (entry.domain_key || "")) || "";
  }

  // 表示名はサーバの返す文字列をそのまま使う（label / domain_name のどちらでも
  // 受けられるようにしておき、無ければ domain_key を出す＝作らない）。
  function domainLabelOf(entry) {
    if (!entry) return "";
    return entry.label || entry.domain_name || entry.domain_key || "";
  }

  // 「閲覧できる論文が配置されているか」の bool。件数ではない（CR3）。
  function domainHasPapers(entry) {
    if (!entry) return false;
    if (typeof entry.has_placements === "boolean") return entry.has_placements;
    if (typeof entry.has_visible_papers === "boolean") return entry.has_visible_papers;
    return false;
  }

  function renderDomains() {
    const box = el("corpus-sea-domains");
    if (!box) return;
    if (!state.domains.length) {
      box.innerHTML = "";
      return;
    }
    let html = "";
    state.domains.forEach(function (d) {
      const key = domainKeyOf(d);
      const active = key === state.domainKey ? " active" : "";
      const papers = domainHasPapers(d)
        ? '<span class="corpus-sea-domain-dot" title="閲覧できる論文が置かれています"></span>'
        : "";
      html += '<button type="button" class="corpus-sea-domain-btn' + active + '" data-corpus-domain="' +
        esc(key) + '">' + esc(domainLabelOf(d)) + papers + "</button>";
    });
    box.innerHTML = html;
    box.querySelectorAll("[data-corpus-domain]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        selectDomain(this.getAttribute("data-corpus-domain"));
      });
    });
  }

  function renderNote(text) {
    const note = el("corpus-sea-note");
    if (!note) return;
    note.textContent = text || "";
    note.hidden = !text;
  }

  async function selectDomain(domainKey) {
    if (!domainKey) return;
    state.domainKey = domainKey;
    state.selectedDocumentId = "";
    state.landscape = null;
    state.skeleton = null;
    state.documents = [];
    state.loading = true;
    renderDomains();
    renderMap();
    renderPapers();
    renderDetail();
    renderEdges();

    const q = "?domain_key=" + encodeURIComponent(domainKey);
    const [landscape, skeleton, documents] = await Promise.all([
      getJson("/learning/corpus/landscape" + q),
      // 骨格そのものは既存の atlas API から明示カートリッジで取る（座標の二重管理をしない）。
      getJson("/atlas?cartridge=" + encodeURIComponent(domainKey)),
      getJson("/learning/corpus/documents" + q),
    ]);
    if (state.domainKey !== domainKey) return; // 遅延応答ガード（別分野へ切替済み）
    state.loading = false;
    state.landscape = landscape;
    state.skeleton = skeleton;
    state.documents = (documents && Array.isArray(documents.documents)) ? documents.documents : [];
    renderMap();
    renderPapers();
    renderDetail();
    renderEdges();
  }

  // -------------------------------------------------------------------
  // Phase A: 地図（簡易描画）
  // -------------------------------------------------------------------

  function level1() {
    const s = state.skeleton;
    return (s && s.levels && s.levels["1"]) || null;
  }

  // 配置先アンカーの座標を L1 データから解決する（landscape-layer.js と同じ規則。
  // 概念ノードはノード脇、領域は箱の右上寄り）。描かれていない位置は null で省く。
  function anchorPoint(nodeId, posById, regionById) {
    const node = posById[nodeId];
    if (node) return [node.x + CONCEPT_OFFSET[0], node.y + CONCEPT_OFFSET[1]];
    const region = regionById[nodeId];
    if (region) return [region.x + region.w - 20, region.y + 22];
    return null;
  }

  function placements() {
    const l = state.landscape;
    return (l && Array.isArray(l.placements)) ? l.placements : [];
  }

  function svgEsc(s) {
    return esc(s);
  }

  function renderMap() {
    const box = el("corpus-sea-map");
    if (!box) return;
    const lvl = level1();
    if (!lvl) {
      // fail-closed: 骨格が無い / 取得に失敗したら地図領域ごと出さない。
      box.innerHTML = state.loading
        ? '<div class="corpus-sea-loading">読み込み中…</div>'
        : "";
      return;
    }
    const vb = Array.isArray(lvl.viewBox) ? lvl.viewBox : [680, 370];
    const posById = {};
    (lvl.nodes || []).forEach(function (n) { posById[n.id] = n; });
    const regionById = {};
    (lvl.regions || []).forEach(function (r) { regionById[r.id] = r; });

    let svg = '<svg viewBox="0 0 ' + vb[0] + " " + vb[1] + '" class="corpus-sea-svg" role="img" ' +
      'aria-label="この分野の地図と、閲覧できる論文の位置">';
    // 領域
    (lvl.regions || []).forEach(function (r) {
      const cls = "corpus-sea-region" + (r.kind === "fog" ? " fog" : "");
      svg += '<g class="' + cls + '">';
      svg += '<rect x="' + r.x + '" y="' + r.y + '" width="' + r.w + '" height="' + r.h +
        '" rx="10"></rect>';
      svg += '<text x="' + (r.x + 10) + '" y="' + (r.y + 16) + '">' + svgEsc(r.label || r.id) + "</text>";
      svg += "</g>";
    });
    // 概念ノード
    (lvl.nodes || []).forEach(function (n) {
      const meta = (state.skeleton.nodes || {})[n.id] || {};
      svg += '<g class="corpus-sea-node">';
      svg += '<circle cx="' + n.x + '" cy="' + n.y + '" r="6"></circle>';
      svg += '<text x="' + n.x + '" y="' + (n.y + 18) + '" text-anchor="middle">' +
        svgEsc(n.label || meta.label || n.id) + "</text>";
      svg += "</g>";
    });
    // 論文マーカー（アンカーごとに束ねる）
    const groups = {};
    placements().forEach(function (p) {
      const key = p.anchor_node_id || "";
      if (!key) return;
      (groups[key] = groups[key] || []).push(p);
    });
    Object.keys(groups).forEach(function (nodeId) {
      const point = anchorPoint(nodeId, posById, regionById);
      if (!point) return;
      const entries = groups[nodeId];
      const shown = entries.slice(0, MAX_MARKERS_PER_NODE);
      shown.forEach(function (p, i) {
        svg += '<g class="corpus-sea-marker" data-corpus-doc="' + esc(p.document_id) + '" tabindex="0" ' +
          'role="button" aria-label="' + esc(p.document_title || "") + '">' +
          '<circle cx="' + (point[0] + i * MARKER_STEP) + '" cy="' + point[1] + '" r="9"></circle>' +
          '<text x="' + (point[0] + i * MARKER_STEP) + '" y="' + (point[1] + 3.5) +
          '" text-anchor="middle">📄</text></g>';
      });
      if (entries.length > MAX_MARKERS_PER_NODE) {
        // 溢れた分は数を出さず「…」に畳む（全件は右の論文リストにある）。
        svg += '<text class="corpus-sea-marker-more" x="' +
          (point[0] + MAX_MARKERS_PER_NODE * MARKER_STEP) + '" y="' + (point[1] + 4) +
          '" text-anchor="middle">…</text>';
      }
    });
    svg += "</svg>";
    box.innerHTML = svg;
    box.querySelectorAll("[data-corpus-doc]").forEach(function (g) {
      const open = function () { selectDocument(g.getAttribute("data-corpus-doc")); };
      g.addEventListener("click", open);
      g.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
    });
  }

  // -------------------------------------------------------------------
  // Phase A: 論文リストと詳細パネル
  // -------------------------------------------------------------------

  function renderPapers() {
    const box = el("corpus-sea-papers");
    if (!box) return;
    if (state.loading) {
      box.innerHTML = '<div class="corpus-sea-loading">読み込み中…</div>';
      return;
    }
    if (!state.domainKey) {
      box.innerHTML = "";
      return;
    }
    if (!state.documents.length) {
      box.innerHTML = '<div class="corpus-sea-empty">この分野で閲覧できる論文はまだありません。</div>';
      return;
    }
    let html = '<div class="corpus-sea-sec-hd">閲覧できる論文（新しい順）</div>';
    state.documents.forEach(function (d) {
      const active = d.document_id === state.selectedDocumentId ? " active" : "";
      const meta = [];
      if (Array.isArray(d.authors) && d.authors.length) meta.push(d.authors.join(", "));
      if (d.year) meta.push(String(d.year));
      html += '<button type="button" class="corpus-sea-paper' + active + '" data-corpus-doc-row="' +
        esc(d.document_id) + '">';
      html += '<span class="corpus-sea-paper-title">' + esc(d.title || "") + "</span>";
      if (meta.length) html += '<span class="corpus-sea-paper-meta">' + esc(meta.join(" · ")) + "</span>";
      if (d.placed === false) {
        html += '<span class="corpus-sea-paper-note">この論文は、現在の分野の地図には置かれていません。</span>';
      }
      html += "</button>";
    });
    box.innerHTML = html;
    box.querySelectorAll("[data-corpus-doc-row]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        selectDocument(this.getAttribute("data-corpus-doc-row"));
      });
    });
  }

  function findDocument(docId) {
    for (let i = 0; i < state.documents.length; i++) {
      if (state.documents[i].document_id === docId) return state.documents[i];
    }
    return null;
  }

  function selectDocument(docId) {
    state.selectedDocumentId = docId || "";
    renderPapers();
    renderDetail();
  }

  function renderDetail() {
    const box = el("corpus-sea-detail");
    if (!box) return;
    const docId = state.selectedDocumentId;
    if (!docId) {
      box.innerHTML = "";
      return;
    }
    const doc = findDocument(docId);
    const mine = placements().filter(function (p) { return p.document_id === docId; });
    const title = (doc && doc.title) || (mine[0] && mine[0].document_title) || "";
    let html = '<div class="corpus-sea-detail-card">';
    html += '<div class="corpus-sea-detail-title">' + esc(title) + "</div>";
    const meta = [];
    if (doc && Array.isArray(doc.authors) && doc.authors.length) meta.push(doc.authors.join(", "));
    if (doc && doc.year) meta.push(String(doc.year));
    if (meta.length) html += '<div class="corpus-sea-detail-meta">' + esc(meta.join(" · ")) + "</div>";
    if (mine.length) {
      html += '<div class="corpus-sea-sec-hd">この論文の位置づけ</div>';
      mine.forEach(function (p) {
        html += '<div class="corpus-sea-placement">';
        html += '<span class="corpus-sea-placement-node">' + esc(p.node_label || p.anchor_node_id || "") + "</span>";
        const persp = p.perspective_label || p.perspective || "";
        if (persp) html += '<span class="corpus-sea-placement-persp">' + esc(persp) + "</span>";
        // 出所ラベルはサーバの文字列をそのまま出す（CR3。ここで作らない・数値にしない）。
        if (p.source_label) {
          html += '<span class="corpus-sea-placement-source">' + esc(p.source_label) + "</span>";
        }
        html += "</div>";
      });
    } else {
      html += '<div class="corpus-sea-detail-fact">この論文は、現在の分野の地図には置かれていません。</div>';
    }
    if (!doc || doc.can_discuss !== false) {
      html += '<button type="button" class="corpus-sea-discuss-btn" id="corpus-sea-open-discuss">この論文と議論する</button>';
    }
    html += "</div>";
    box.innerHTML = html;
    const btn = box.querySelector("#corpus-sea-open-discuss");
    if (btn) {
      btn.addEventListener("click", function () { openDiscuss(docId, title); });
    }
  }

  // -------------------------------------------------------------------
  // Phase C/D: 地図の端（縁・外）と関心タップ
  // -------------------------------------------------------------------

  function interestKey(ring, regionId) {
    return state.domainKey + "|" + ring + "|" + (regionId || "");
  }

  function frontierButtonHtml(ring, regionId) {
    const key = interestKey(ring, regionId);
    const traceId = state.interest[key] || "";
    const label = traceId ? "気になるに追加済み（取り消す）" : "この先を知りたい";
    return '<button type="button" class="corpus-sea-frontier-btn' + (traceId ? " on" : "") +
      '" data-corpus-ring="' + esc(ring) + '" data-corpus-region="' + esc(regionId || "") + '">' +
      esc(label) + "</button>";
  }

  function renderEdges() {
    const box = el("corpus-sea-edges");
    if (!box) return;
    const l = state.landscape;
    if (!l) {
      box.innerHTML = "";
      return;
    }
    const fringe = Array.isArray(l.fringe) ? l.fringe : [];
    const outer = l.outer || null;
    if (!fringe.length && !outer) {
      box.innerHTML = "";
      return;
    }
    let html = "";
    fringe.forEach(function (f) {
      html += '<div class="corpus-sea-edge fringe">';
      if (f.region_label || f.region_id) {
        html += '<div class="corpus-sea-edge-region">' + esc(f.region_label || f.region_id) + "</div>";
      }
      // fact_line はサーバが正本（CR4）。ここで文言を作らない・言い換えない。
      html += '<div class="corpus-sea-edge-fact">' + esc(f.fact_line || "") + "</div>";
      const titles = Array.isArray(f.paper_titles) ? f.paper_titles : [];
      if (titles.length) {
        html += '<ul class="corpus-sea-edge-papers">';
        titles.forEach(function (t) { html += "<li>" + esc(t) + "</li>"; });
        html += "</ul>";
      }
      html += frontierButtonHtml("fringe", f.region_id || "");
      html += "</div>";
    });
    if (outer) {
      html += '<div class="corpus-sea-edge outer">';
      html += '<div class="corpus-sea-edge-fact">' + esc(outer.fact_line || "") + "</div>";
      html += frontierButtonHtml("outer", "");
      html += "</div>";
    }
    box.innerHTML = html;
    box.querySelectorAll("[data-corpus-ring]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        toggleFrontierInterest(this.getAttribute("data-corpus-ring"),
          this.getAttribute("data-corpus-region"), this);
      });
    });
  }

  // 明示タップのときだけ記録する（CR6）。押した結果で提示内容は変えない（CR5）—
  // 変わるのはこのボタン自身のラベル（取り消せることを示す）だけ。
  async function toggleFrontierInterest(ring, regionId, btn) {
    if (!state.domainKey || !ring) return;
    const key = interestKey(ring, regionId);
    const traceId = state.interest[key] || "";
    btn.disabled = true;
    try {
      if (traceId) {
        const res = await apiFetch(
          "/learning/corpus/frontier-interest/" + encodeURIComponent(traceId) + "/withdraw",
          { method: "POST" }
        );
        if (res.ok) delete state.interest[key];
      } else {
        const body = { domain_key: state.domainKey, ring: ring };
        if (regionId) body.region_id = regionId;
        const res = await apiFetch("/learning/corpus/frontier-interest", {
          method: "POST", body: JSON.stringify(body),
        });
        if (res.ok) {
          const data = await res.json();
          if (data && data.trace_id) state.interest[key] = data.trace_id;
        }
      }
    } catch (e) {
      // 失敗は静かに元のまま（作り話の成功表示をしない）
    }
    btn.disabled = false;
    renderEdges();
  }

  // -------------------------------------------------------------------
  // Phase B: document 直付け discuss
  // -------------------------------------------------------------------

  function docPath(suffix) {
    return "/learning/documents/" + encodeURIComponent(state.discuss.documentId) + suffix;
  }

  function renderDiscussBar() {
    const bar = el("corpus-sea-discuss-bar");
    if (!bar) return;
    let html = '<button type="button" class="corpus-sea-back" id="corpus-sea-back">← 論文の海へ戻る</button>';
    // モードバー（コース版の .mode-bar.discuss と同じ中立表示。コース外であることを添える）
    html += '<span class="corpus-sea-mode">🗣 論文と議論中（コース外）</span>';
    html += '<span class="corpus-sea-doc-title">' + esc(state.discuss.title || "") + "</span>";
    html += '<span class="corpus-sea-scope-label">範囲:</span>';
    html += '<div class="corpus-sea-scope-toggle">';
    SCOPE_OPTIONS.forEach(function (opt) {
      const active = state.discuss.scope === opt.value ? " active" : "";
      html += '<button type="button" class="corpus-sea-scope-opt' + active +
        '" data-corpus-scope="' + esc(opt.value) + '">' + esc(opt.label) + "</button>";
    });
    html += "</div>";
    bar.innerHTML = html;
    bar.querySelector("#corpus-sea-back").addEventListener("click", backToMap);
    bar.querySelectorAll("[data-corpus-scope]").forEach(function (b) {
      b.addEventListener("click", function () {
        state.discuss.scope = this.getAttribute("data-corpus-scope") || "course_sources";
        renderDiscussBar();
      });
    });
  }

  // 議論ビューを離れる。着地画面・digest はコース文脈だけの機構なので呼ばない
  // （設計 §5.4 の縮退の明示）。discuss.js の document 文脈だけを解除する。
  function leaveDiscussView() {
    const openingEl = el("corpus-sea-opening");
    if (openingEl) openingEl.setAttribute("data-discuss-active", "false");
    if (window.Discuss && window.Discuss.exitDocumentContext) window.Discuss.exitDocumentContext();
  }

  function backToMap() {
    leaveDiscussView();
    setView("map");
  }

  async function openDiscuss(docId, title) {
    if (!docId) return;
    state.discuss = {
      documentId: docId,
      title: title || "",
      scope: "course_sources",
      messages: [],
      sending: false,
      editingMessageId: "",
      historyLoaded: false,
    };
    setView("discuss");
    renderDiscussBar();
    renderChat();
    const openingEl = el("corpus-sea-opening");
    if (openingEl) {
      openingEl.innerHTML = '<div class="corpus-sea-loading">読み込み中…</div>';
      // 議論中であることの合図（discuss.js の遅延応答ガードが読む）。
      openingEl.setAttribute("data-discuss-active", "true");
      // 既存の discuss 開幕画面をそのまま再利用する（設計 §5.3。DTO はコース版と同形）。
      // 第2引数に文脈オブジェクトを渡すと discuss.js が document 直付けの取得口へ切り替える。
      if (window.Discuss && window.Discuss.renderOpening) {
        const target = openingEl;
        window.Discuss.renderOpening(target, {
          documentId: docId,
          onAsk: function (text) { sendDiscussMessage(text); },
          onSeed: function (text) { postSeedPrompt(text); },
        }).then(function () {
          if (target.querySelector(".discuss-opening")) return;
          // fail-closed: 開幕素材が無ければ事実文だけを残す（作り話で埋めない）。
          target.innerHTML = '<div class="corpus-sea-empty">' +
            'この論文の開幕素材はまだ用意されていません。そのまま議論を始められます。</div>';
        });
      } else {
        openingEl.innerHTML = "";
      }
    }
    const hist = await getJson(docPath("/discuss/history"));
    if (state.discuss.documentId !== docId) return; // 遅延応答ガード
    state.discuss.messages = (hist && Array.isArray(hist.history)) ? hist.history : [];
    state.discuss.historyLoaded = true;
    renderChat();
    const input = el("corpus-sea-input");
    if (input) input.focus();
  }

  // 開幕の「議論のきっかけ」（立場を求める問い）は LLM を呼ばず、アシスタントの問いとして
  // 会話欄に置いて学習者の応答を待つ（app.js の discussPostSeedPrompt と同型）。
  function postSeedPrompt(text) {
    if (!text || state.discuss.sending) return;
    const msgs = state.discuss.messages;
    const last = msgs.length ? msgs[msgs.length - 1] : null;
    if (last && last.role === "assistant" && last.discuss_prompt) msgs.pop();
    msgs.push({ role: "assistant", content: text, id: genMsgId(), discuss_prompt: true });
    renderChat();
    const input = el("corpus-sea-input");
    if (input) input.focus();
  }

  function genMsgId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "m-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function groundingBadge(g) {
    const m = GROUNDING_META[g];
    if (!m) return "";
    return '<span class="grounding-badge ' + m.cls + '">' + m.icon + " " + esc(m.label) + "</span>";
  }

  function tierBadge(tier) {
    const m = TIER_META[tier] || TIER_META.out_of_source;
    return '<span class="tier-badge ' + m.cls + '">' + esc(m.label) + "</span>";
  }

  // 本文は素通し（改行のみ <br>）。教材本文の埋め込み記法・数式解決はコース教材の
  // 文脈でしか成立しないため、ここでは行わない（縮退を正直に受ける）。
  function bodyHtml(text) {
    return esc(text).replace(/\n/g, "<br>");
  }

  function renderChat() {
    const box = el("corpus-sea-chat");
    if (!box) return;
    let html = "";
    if (!state.discuss.messages.length && !state.discuss.sending) {
      html += '<div class="corpus-sea-chat-hint">この論文について、あなたの読みを書いてください。' +
        '回答の根拠（教材由来か、AIの一般知識か）は各回答に表示されます。</div>';
    }
    state.discuss.messages.forEach(function (msg) {
      const idAttr = msg.id ? ' id="corpus-msg-' + esc(msg.id) + '"' : "";
      if (msg.role === "user") {
        // 会話バブルはコース版と同じクラス（.mg.usr / .mg.ai）を使い、見た目を揃える。
        html += '<div class="mg usr"' + idAttr + ">" + bodyHtml(msg.content) +
          '<span class="corpus-sea-msg-ops">' +
          '<button type="button" class="corpus-sea-msg-op" data-corpus-edit="' + esc(msg.id || "") + '" title="書き直す">✏️</button>' +
          '<button type="button" class="corpus-sea-msg-op" data-corpus-del="' + esc(msg.id || "") + '" title="ここから削除">🗑</button>' +
          "</span></div>";
        return;
      }
      const seedCls = msg.discuss_prompt ? " discuss-prompt" : "";
      html += '<div class="mg ai' + seedCls + '"' + idAttr + ">" + bodyHtml(msg.content);
      if (msg.discuss_prompt) {
        html += '<div class="discuss-prompt-hint">この問いに、あなたの考えを書いてください。</div>';
      }
      if (msg.content_grounding || msg.overall_tier) {
        html += '<div class="answer-tier-bar">';
        if (msg.content_grounding) html += groundingBadge(msg.content_grounding);
        if (msg.overall_tier) {
          html += '<span class="answer-tier-label">この回答の根拠の格</span>' + tierBadge(msg.overall_tier);
        }
        html += "</div>";
      }
      html += "</div>";
    });
    if (state.discuss.sending) {
      html += '<div class="mg ai"><div class="typing"><span></span><span></span><span></span></div></div>';
    }
    box.innerHTML = html;
    box.querySelectorAll("[data-corpus-edit]").forEach(function (b) {
      b.addEventListener("click", function () { startEdit(this.getAttribute("data-corpus-edit")); });
    });
    box.querySelectorAll("[data-corpus-del]").forEach(function (b) {
      b.addEventListener("click", function () { deleteFrom(this.getAttribute("data-corpus-del")); });
    });
    box.scrollTop = box.scrollHeight;
  }

  function findMessageIndex(msgId) {
    for (let i = 0; i < state.discuss.messages.length; i++) {
      if (state.discuss.messages[i].id === msgId) return i;
    }
    return -1;
  }

  // 書き直し: 本文を入力欄へ戻し、送信でその往復以降を差し替える（コース版と同じ
  // truncate セマンティクス。実体はサーバの replace_message_id）。
  function startEdit(msgId) {
    if (!msgId || state.discuss.sending) return;
    const idx = findMessageIndex(msgId);
    if (idx === -1) return;
    const input = el("corpus-sea-input");
    if (input) {
      input.value = state.discuss.messages[idx].content || "";
      input.focus();
    }
    state.discuss.editingMessageId = msgId;
  }

  async function deleteFrom(msgId) {
    if (!msgId || state.discuss.sending) return;
    if (!confirm("このメッセージ以降のやりとりを削除します。よろしいですか？")) return;
    try {
      const res = await apiFetch(docPath("/discuss/messages/" + encodeURIComponent(msgId)), {
        method: "DELETE",
      });
      if (!res.ok) return;
      const idx = findMessageIndex(msgId);
      if (idx !== -1) state.discuss.messages = state.discuss.messages.slice(0, idx);
      if (state.discuss.editingMessageId === msgId) state.discuss.editingMessageId = "";
      renderChat();
    } catch (e) {
      /* 失敗時は表示を変えない */
    }
  }

  function submitDiscussInput() {
    const input = el("corpus-sea-input");
    if (!input) return;
    const text = (input.value || "").trim();
    if (!text) return;
    input.value = "";
    sendDiscussMessage(text);
  }

  async function sendDiscussMessage(text) {
    if (!text || state.discuss.sending || !state.discuss.documentId) return;
    const replaceId = state.discuss.editingMessageId || "";
    state.discuss.editingMessageId = "";
    if (replaceId) {
      const ri = findMessageIndex(replaceId);
      if (ri !== -1) state.discuss.messages = state.discuss.messages.slice(0, ri);
    }
    const userMsgId = genMsgId();
    state.discuss.messages.push({ role: "user", content: text, id: userMsgId });
    state.discuss.sending = true;
    renderChat();
    const docId = state.discuss.documentId;
    const body = {
      message: text,
      message_id: userMsgId,
      history: state.discuss.messages.slice(0, -1),
      discuss_scope: state.discuss.scope || "course_sources",
    };
    if (replaceId) body.replace_message_id = replaceId;
    try {
      const res = await apiFetch(docPath("/discuss/chat"), {
        method: "POST", body: JSON.stringify(body),
      });
      if (state.discuss.documentId !== docId) return; // 別の論文へ移動済み
      if (res.ok) {
        const data = await res.json();
        state.discuss.messages.push({
          role: "assistant",
          content: data.answer,
          id: genMsgId(),
          overall_tier: data.overall_tier || null,
          content_grounding: data.content_grounding || null,
        });
      } else {
        state.discuss.messages.push({
          role: "assistant", id: genMsgId(),
          content: "この論文との議論をいま開けませんでした。もう一度お試しください。",
        });
      }
    } catch (e) {
      state.discuss.messages.push({
        role: "assistant", id: genMsgId(), content: "サーバーに接続できません。",
      });
    }
    state.discuss.sending = false;
    renderChat();
  }

  // -------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------

  function init(deps) {
    state.deps = deps || {};
  }

  function open() {
    if (!token()) return; // fail-closed: 未ログインでは開かない
    ensureOverlay();
    state.lastFocus = document.activeElement;
    setView("map");
    state.overlayEl.hidden = false;
    if (!state.domainsLoaded) {
      loadDomains();
    } else {
      renderDomains();
    }
  }

  function close() {
    if (!state.overlayEl) return;
    if (state.view === "discuss") leaveDiscussView();
    state.overlayEl.hidden = true;
    if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
  }

  function invalidate() {
    if (state.view === "discuss") leaveDiscussView();
    state.domainsLoaded = false;
    state.domains = [];
    state.domainKey = "";
    state.landscape = null;
    state.skeleton = null;
    state.documents = [];
    state.selectedDocumentId = "";
    state.interest = {};
    state.discuss = {
      documentId: "", title: "", scope: "course_sources",
      messages: [], sending: false, editingMessageId: "", historyLoaded: false,
    };
    if (state.overlayEl) state.overlayEl.hidden = true;
  }

  window.CorpusSea = {
    init: init,
    open: open,
    close: close,
    invalidate: invalidate,
  };
})();
