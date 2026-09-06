/* 推定の糸（Relation Threads）— 分野の地図 L2 に重ねる「確定前の関係」レイヤー
 *
 * 設計の正本: docs/features/atlas_relation_edges_design.md §6（学習者向け: 推定の糸）。
 * 親: docs/architecture/field_map_display_principles_2026-08-29.md（原則①′
 * 「地形は人間・関係は離散の辺」）。参照する不変条項（同 §2）:
 *   RE1 主張は離散の辺のみ・地形不変 — node の位置・骨格の地形に一切触れない
 *   RE2 出所必須 — 糸は必ず点線 + 「AIによる推定（未確認）」+ 骨格版を伴う。
 *       凍結された実線の骨格辺と視覚的に区別できない描画をしない
 *   RE4 数値非表示 — cosine・共起件数を出さない（v1 は段階ラベルすら描かない）
 *   RE6 候補は読み時導出 — 追加フェッチをしない。GET /api/atlas の
 *       レスポンス（AtlasOverlay.data.threads）をそのまま読むだけ
 *   RE7 ヘアボール防止 — 上限・除外はサーバ側の導出で担保済み。ここでは
 *       「現在の L2 に描かれていないノードを端点に持つ糸」を落とすだけ
 *   RE8 教員の判断は学習者表示に反映される — 見送られた辺はサーバが返さない
 *
 * landscape-layer.js / personal-map.js と同じ3フック契約で atlas-overlay.js から
 * 呼ばれる薄い層。既存の描画（領域・概念ノード・実線エッジ・霧・足あと）は一切
 * 変更せず、atlas-minimap.js には触れない（RE1）。
 *
 * 公開契約 window.AtlasThreadsLayer（呼び出し側は atlas-overlay.js。名前・引数は固定）:
 *   mountControls(sheetEl)           — atlas-overlay buildShell 完了時に一度だけ呼ばれる
 *   onLevelRendered(level, canvasEl) — atlas-overlay showLevel の描画後に毎回呼ばれる
 *   onOverlayClosed()                — atlas-overlay closeOverlay 時
 *
 * 既定オフ。トグル状態はメモリのみで持ち、localStorage に保存しない
 * （「既定オフ」が毎回の既定であること自体が RE2 の一部）。
 */
(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const LAYER_CLASS = "threads-layer";
  // 糸を描くズームレベル（v1 は L2 のコース地図のみ。L1/L3 は非スコープ）
  const THREAD_LEVEL = 2;
  // 実線の骨格辺（#B4B2A9 / 直線）と混ざらないための視覚言語（RE2）。
  // 色は atlas-overlay.js の霧のトーン（C.fogStroke）に合わせ、破線で描く。
  const THREAD_STROKE = "#888780";
  const THREAD_DASH = "2 5";
  const THREAD_WIDTH = 0.75;
  const THREAD_OPACITY = 0.75;
  // 端点のノード円を避けるための余白（実際の半径は描画済みの circle から読む）
  const NODE_GAP = 3;
  // 出所ラベルの固定文（RE2。サーバの段階ラベルは v1 では描かない = RE4）
  const PROVENANCE_TEXT = "AIによる推定（未確認）";

  const state = {
    enabled: false,
    controlsEl: null,
    toggleInputEl: null,
    factEl: null,
    lastCanvas: null,
    lastLevel: 1,
  };

  // -------------------------------------------------------------------
  // データ（追加フェッチなし・RE6）
  // -------------------------------------------------------------------

  // GET /api/atlas の optional キー threads。キーなし・available:false・items 空は
  // すべて「糸なし」に丸める（fail-closed）。
  function threadsData() {
    try {
      const data = window.AtlasOverlay && window.AtlasOverlay.data;
      const threads = data && data.threads;
      if (!threads || threads.available !== true) return null;
      const items = threads.items;
      if (!Array.isArray(items) || !items.length) return null;
      return threads;
    } catch (e) {
      return null;
    }
  }

  function factText(threads) {
    const version = (threads && threads.skeleton_version) ? String(threads.skeleton_version) : "";
    // RE2: 出所と骨格版を必ず併記する。件数・近さの数値は出さない（RE4）
    return version ? PROVENANCE_TEXT + "・骨格 版" + version : PROVENANCE_TEXT;
  }

  // -------------------------------------------------------------------
  // 座標解決（描画済みの L2 ノードから読む）
  // -------------------------------------------------------------------

  // atlas-overlay.js の makeNodeGroup は各ノードを
  // <g class="atlas-node" data-node="{id}"><circle cx cy r .../></g> で描く。
  // レイアウト計算を二重実装せず、描かれた circle の cx/cy/r をそのまま読む
  // （ノード数上限で描画されなかった概念は見つからない = そのまま糸を落とす・RE7）。
  function nodePoint(svg, nodeId) {
    if (!svg || !nodeId) return null;
    const g = svg.querySelector('.atlas-node[data-node="' + cssEscape(nodeId) + '"]');
    if (!g) return null;
    const circles = g.querySelectorAll("circle");
    if (!circles.length) return null;
    let x = null;
    let y = null;
    let r = 0;
    for (let i = 0; i < circles.length; i++) {
      const cx = parseFloat(circles[i].getAttribute("cx"));
      const cy = parseFloat(circles[i].getAttribute("cy"));
      const cr = parseFloat(circles[i].getAttribute("r"));
      if (isNaN(cx) || isNaN(cy)) continue;
      if (x === null) { x = cx; y = cy; }
      if (!isNaN(cr) && cr > r) r = cr;
    }
    if (x === null) return null;
    return { x: x, y: y, r: r || 10 };
  }

  // querySelector に安全に埋め込むための最小エスケープ（id は骨格由来の英数字・
  // ハイフン・アンダースコアが想定だが、想定外の文字でも例外にしない）
  function cssEscape(value) {
    const s = String(value);
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(s);
    return s.replace(/["\\\]]/g, "\\$&");
  }

  // 端点の円に食い込まないよう両端を詰める（atlas-overlay の trimmedLine と同型）
  function trimmed(a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (!len) return null;
    const pa = (a.r + NODE_GAP) / len;
    const pb = (b.r + NODE_GAP) / len;
    if (pa + pb >= 1) return null; // ノードが重なるほど近い場合は描かない
    return {
      x1: a.x + dx * pa, y1: a.y + dy * pa,
      x2: b.x - dx * pb, y2: b.y - dy * pb,
    };
  }

  // -------------------------------------------------------------------
  // 描画（既存要素は触らない・RE1）
  // -------------------------------------------------------------------

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
    return node;
  }

  function removeLayer(canvasEl) {
    const canvas = canvasEl || state.lastCanvas;
    if (!canvas) return;
    const svg = canvas.querySelector("svg");
    if (!svg) return;
    const existing = svg.querySelector("." + LAYER_CLASS);
    if (existing) existing.remove();
  }

  function renderThreads(canvasEl) {
    removeLayer(canvasEl);
    if (!state.enabled) return;
    if (state.lastLevel !== THREAD_LEVEL) return;
    const canvas = canvasEl || state.lastCanvas;
    if (!canvas) return;
    const svg = canvas.querySelector("svg");
    if (!svg) return;
    const threads = threadsData();
    if (!threads) return;

    const layer = svgEl("g", { class: LAYER_CLASS, "aria-hidden": "true" });
    let drawn = 0;
    threads.items.forEach((item) => {
      if (!item) return;
      const a = nodePoint(svg, item.from);
      const b = nodePoint(svg, item.to);
      if (!a || !b) return; // このズームに描かれていない端点は静かに省く（RE7）
      const line = trimmed(a, b);
      if (!line) return;
      const el = svgEl("line", {
        class: "threads-line",
        x1: line.x1, y1: line.y1, x2: line.x2, y2: line.y2,
        stroke: THREAD_STROKE,
        "stroke-width": THREAD_WIDTH,
        "stroke-dasharray": THREAD_DASH,
        "stroke-linecap": "round",
        opacity: THREAD_OPACITY,
      });
      layer.appendChild(el);
      drawn += 1;
    });
    if (!drawn) return;
    svg.appendChild(layer);
  }

  // -------------------------------------------------------------------
  // コントロール（L2 かつ糸データがあるときだけ見せる）
  // -------------------------------------------------------------------

  function updateFact() {
    if (!state.factEl) return;
    const threads = threadsData();
    if (!state.enabled || !threads) {
      state.factEl.hidden = true;
      state.factEl.textContent = "";
      return;
    }
    state.factEl.textContent = factText(threads);
    state.factEl.hidden = false;
  }

  // fail-closed: 糸データが無い / L2 以外ではコントロールごと隠し、チェックを戻す
  function refreshControls() {
    if (!state.controlsEl) return;
    const visible = state.lastLevel === THREAD_LEVEL && !!threadsData();
    state.controlsEl.hidden = !visible;
    if (!visible && state.enabled) {
      state.enabled = false;
      if (state.toggleInputEl) state.toggleInputEl.checked = false;
    }
    updateFact();
  }

  function onToggleChange(checked) {
    if (checked && !threadsData()) {
      // データが無ければ ON にしない（fail-closed）
      state.enabled = false;
      if (state.toggleInputEl) state.toggleInputEl.checked = false;
      updateFact();
      removeLayer(state.lastCanvas);
      return;
    }
    state.enabled = !!checked;
    updateFact();
    renderThreads(state.lastCanvas);
  }

  function buildControls(sheetEl) {
    const wrap = document.createElement("div");
    wrap.className = "threads-controls";
    wrap.hidden = true; // 可視性は refreshControls が判定する（fail-closed）

    // 静的マークアップのみ（外部データを差し込まない）。data-ui-anchor は
    // 「？ 使い方」インスペクトのホバー担体（core/help_kb/ui_anchors.py が正本）。
    // RE2: 出所ラベル + 骨格版の事実行はトグル ON のときだけ見せる。
    wrap.innerHTML =
      '<label class="threads-toggle" data-ui-anchor="atlas.relation-threads">' +
      '<input type="checkbox" class="threads-toggle-input">推定の糸</label>' +
      '<div class="threads-fact" hidden></div>';
    const checkbox = wrap.querySelector(".threads-toggle-input");
    const fact = wrap.querySelector(".threads-fact");
    checkbox.checked = false; // 既定オフ（状態はメモリのみ・保存先を持たない）
    checkbox.addEventListener("change", () => {
      try {
        onToggleChange(checkbox.checked);
      } catch (e) {
        failClosed();
      }
    });

    // 「論文の位置」（知識ランドスケープ）の後ろ、無ければフッター手前。
    const landscape = sheetEl.querySelector(".landscape-controls");
    const foot = sheetEl.querySelector("#atlas-foot");
    if (landscape && landscape.parentNode) {
      landscape.parentNode.insertBefore(wrap, landscape.nextSibling);
    } else if (foot && foot.parentNode) {
      foot.parentNode.insertBefore(wrap, foot);
    } else {
      sheetEl.appendChild(wrap);
    }

    state.controlsEl = wrap;
    state.toggleInputEl = checkbox;
    state.factEl = fact;
  }

  // どこで失敗しても「線を消してコントロールを隠す」に落とす（fail-closed）
  function failClosed() {
    state.enabled = false;
    try {
      if (state.toggleInputEl) state.toggleInputEl.checked = false;
      if (state.controlsEl) state.controlsEl.hidden = true;
      if (state.factEl) { state.factEl.hidden = true; state.factEl.textContent = ""; }
      removeLayer(state.lastCanvas);
    } catch (e) { /* これ以上できることはない */ }
  }

  // -------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------

  function mountControls(sheetEl) {
    if (!sheetEl || state.controlsEl) return; // 二重マウント防止
    try {
      buildControls(sheetEl);
      refreshControls();
    } catch (e) {
      failClosed();
    }
  }

  function onLevelRendered(level, canvasEl) {
    try {
      state.lastLevel = level;
      state.lastCanvas = canvasEl;
      refreshControls();
      renderThreads(canvasEl);
    } catch (e) {
      failClosed();
    }
  }

  function onOverlayClosed() {
    try {
      removeLayer(state.lastCanvas);
      state.enabled = false;
      if (state.toggleInputEl) state.toggleInputEl.checked = false;
      updateFact();
    } catch (e) {
      failClosed();
    }
  }

  window.AtlasThreadsLayer = {
    mountControls,
    onLevelRendered,
    onOverlayClosed,
  };
})();
