/* 分野の地図 — 全画面オーバーレイ (Issue B: B-1/B-2/B-3)
 *
 * フィクスチャ (atlas-fixture.js, 仕様書 §12) 駆動。実データ接続 (E系) 後は
 * 同じ形のデータを AtlasOverlay.open(data) に渡すだけで差し替えられる。
 *
 * 守るべき設計原則 (§1.2 / §14):
 * - 霧・隣接の光に理由テキストや推薦文言を付けない
 * - 踏破率の %・スコア・バッジを一切表示しない (足跡の描画に留める)
 * - モデル知識由来 (霧・骨格) とコーパス由来 (灯り) を視覚的に混ぜない
 * - 状態は色 + 形状 (破線 / 二重円 / ハッチ) で二重符号化 (色覚対応)
 *
 * 接続点 (C-1 / C-2 / D-1):
 * - ノード選択で document に CustomEvent "atlas:nodeselect" を発火する。
 *   detail = { node_id, level, skeleton_version }
 * - 詳細パネルのプレースホルダ: #atlas-panel-body (C-1 が中身を差し込む)
 */
(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";

  // ノード数上限 (§13)。超過分は先頭から採用し、残りは描画しない (代表選出は E系)
  const LIMITS = { l1Regions: 7, l1ConceptsPerRegion: 6, l2Nodes: 20, l3Nodes: 12 };

  // §5 の視覚言語 (仕様の定数。テーマ変数ではない)
  const C = {
    verifiedFill: "#E1F5EE", verifiedStroke: "#0F6E56",
    contestedFill: "#FAEEDA", contestedStroke: "#854F0B",
    assumedStroke: "#BA7517",
    nowRing: "#378ADD", nowFill: "#E6F1FB", nowCore: "#185FA5",
    glowRing: "#F5A623",
    fogStroke: "#888780",
    edge: "#B4B2A9", arrow: "#5F5E5A",
    footprint: "#378ADD",
  };

  const PILL_STYLES = {
    verified: { bg: "#E1F5EE", fg: "#085041", dashed: false },
    contested: { bg: "#FAEEDA", fg: "#633806", dashed: false },
    assumed: { bg: "transparent", fg: "#854F0B", dashed: true },
    gap: { bg: "transparent", fg: "#854F0B", dashed: true },
    unvisited: { bg: "var(--color-background-tertiary)", fg: "var(--color-text-secondary)", dashed: false },
    now: { bg: "#E6F1FB", fg: "#0C447C", dashed: false },
    fog: { bg: "var(--color-background-tertiary)", fg: "var(--color-text-secondary)", dashed: false },
    link: { bg: "var(--color-background-tertiary)", fg: "var(--color-text-secondary)", dashed: false },
  };

  const state = {
    data: null,
    level: 1,
    selected: null,
    built: false,
    lastFocus: null,
    // C-2: 再オープン時に直前の選択・レベルを復元する (戻ってこられる感覚)。
    // 保持はセッション内のみ (メモリ)。骨格改版時の id 永続性問題を避けるため、
    // data が差し替わったら破棄する (未決事項の決定: セッション内のみ)。
    reopen: false,
    // F-2 の内部計測用: どの導線から開かれ、いつ開いたか (atlas:opened / atlas:closed)
    openSource: "",
    openedAt: 0,
  };

  // -------------------------------------------------------------------
  // DOM helpers
  // -------------------------------------------------------------------

  const el = (tag, attrs, text) => {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  };

  const svgEl = (tag, attrs, text) => {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  };

  const nodeInfo = (id) => (state.data.nodes && state.data.nodes[id]) || { label: id, status: "unvisited" };
  const nodeStatus = (id) => nodeInfo(id).status || "unvisited";

  // -------------------------------------------------------------------
  // オーバーレイ骨組み (B-1)
  // -------------------------------------------------------------------

  function buildShell() {
    const ov = el("div", { class: "atlas-ov", role: "dialog", "aria-modal": "true", "aria-label": "分野の地図", hidden: "" });
    ov.id = "atlas-overlay";

    const sheet = el("div", { class: "atlas-sheet" });
    ov.appendChild(sheet);

    // sr-only の説明見出し (§13)
    sheet.appendChild(el("h2", { class: "atlas-sr-only" },
      "分野の地図オーバーレイ: ズーム3段階（分野・コース・導出）とノード詳細"));

    // ヘッダー行
    const head = el("div", { class: "atlas-head" });
    head.appendChild(el("span", { class: "atlas-title" }, "分野の地図"));
    head.appendChild(el("span", { class: "atlas-spacer" }));
    const seg = el("div", { class: "atlas-seg", role: "tablist", "aria-label": "ズームレベル" });
    [["1", "分野"], ["2", "コース"], ["3", "導出"]].forEach(([lv, label]) => {
      const b = el("button", { type: "button", class: "atlas-seg-btn", role: "tab", "data-level": lv, "aria-selected": "false" }, label);
      b.addEventListener("click", () => showLevel(Number(lv)));
      seg.appendChild(b);
    });
    seg.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const next = state.level + (e.key === "ArrowRight" ? 1 : -1);
      if (next >= 1 && next <= 3) {
        showLevel(next);
        seg.querySelector(`[data-level="${next}"]`).focus();
      }
    });
    head.appendChild(seg);
    const close = el("button", { type: "button", class: "atlas-close", "aria-label": "閉じる" }, "✕");
    close.addEventListener("click", closeOverlay);
    head.appendChild(close);
    sheet.appendChild(head);

    // パンくず
    sheet.appendChild(el("p", { class: "atlas-crumb", id: "atlas-crumb" }));
    sheet.appendChild(el("p", { class: "atlas-context", id: "atlas-context" }));

    // マップキャンバス
    sheet.appendChild(el("div", { class: "atlas-canvas", id: "atlas-canvas" }));

    // 詳細パネル (プレースホルダ。中身とアクションは C-1/C-2/D-1)
    const panel = el("div", { class: "atlas-panel", id: "atlas-detail-panel" });
    const panelHead = el("div", { class: "atlas-panel-head" });
    panelHead.appendChild(el("span", { class: "atlas-panel-name", id: "atlas-panel-name" }));
    panelHead.appendChild(el("span", { class: "atlas-panel-pill", id: "atlas-panel-pill" }));
    panel.appendChild(panelHead);
    panel.appendChild(el("div", { class: "atlas-panel-body", id: "atlas-panel-body" }));
    sheet.appendChild(panel);

    // 凡例 (常時表示・4項目)
    sheet.appendChild(buildLegend());

    // フッター: 骨格の版数 (出所の常時明示 §13)
    sheet.appendChild(el("p", { class: "atlas-foot", id: "atlas-foot" }));

    // 個人知識ネットワーク (Phase P-1): 「わたしの地図」トグル + 凡例 + 「まだ地図にない」トレイ。
    // PersonalMap が無い環境 (未読み込み等) では従来どおり何も差し込まれない。
    if (window.PersonalMap) window.PersonalMap.mountControls(sheet);

    ov.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); closeOverlay(); }
    });
    ov.addEventListener("mousedown", (e) => { if (e.target === ov) closeOverlay(); });

    document.body.appendChild(ov);
    return ov;
  }

  function legendItem(svgChildren, label, viewW) {
    const item = el("span", { class: "atlas-legend-item" });
    const svg = svgEl("svg", { width: viewW || 14, height: 14, viewBox: `0 0 ${viewW || 14} 14`, "aria-hidden": "true" });
    svgChildren.forEach((c) => svg.appendChild(c));
    item.appendChild(svg);
    item.appendChild(document.createTextNode(label));
    return item;
  }

  function buildLegend() {
    const legend = el("div", { class: "atlas-legend" });
    legend.appendChild(legendItem([
      svgEl("circle", { cx: 7, cy: 7, r: 5.5, fill: C.verifiedFill, stroke: C.verifiedStroke, "stroke-width": 1 }),
    ], "実験で確認"));
    legend.appendChild(legendItem([
      svgEl("circle", { cx: 7, cy: 7, r: 5.5, fill: C.contestedFill, stroke: C.contestedStroke, "stroke-width": 1 }),
    ], "解釈が分かれる"));
    legend.appendChild(legendItem([
      svgEl("circle", { cx: 7, cy: 7, r: 5.5, fill: "none", stroke: C.assumedStroke, "stroke-width": 1.5, "stroke-dasharray": "3 2" }),
    ], "暗黙の前提・行間"));
    legend.appendChild(legendItem([
      svgEl("rect", { x: 1, y: 1, width: 16, height: 11, rx: 2.5, fill: "none", stroke: C.fogStroke, "stroke-width": 0.75, "stroke-dasharray": "3 2" }),
      svgEl("line", { x1: 4, y1: 10, x2: 9, y2: 3, stroke: C.fogStroke, "stroke-width": 0.75, opacity: 0.6 }),
      svgEl("line", { x1: 8, y1: 10, x2: 13, y2: 3, stroke: C.fogStroke, "stroke-width": 0.75, opacity: 0.6 }),
    ], "論文未投入の地形", 18));
    return legend;
  }

  // -------------------------------------------------------------------
  // ノード描画 (§5 の視覚言語)
  // -------------------------------------------------------------------

  function makeNodeGroup(id, level) {
    const info = nodeInfo(id);
    const g = svgEl("g", {
      class: "atlas-node",
      tabindex: "0",
      role: "button",
      "data-node": id,
      "aria-label": `${info.label}（${info.pill || nodeStatus(id)}）`,
    });
    const activate = (e) => { e.preventDefault(); selectNode(id); };
    g.addEventListener("click", activate);
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") activate(e);
    });
    return g;
  }

  function appendCircleNode(g, x, y, id) {
    const status = nodeStatus(id);
    const glow = !!nodeInfo(id).glow;
    if (status === "now") {
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 17, fill: "none", stroke: C.nowRing, "stroke-width": 1.5 }));
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 10, fill: C.nowFill, stroke: C.nowRing, "stroke-width": 1 }));
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 3.5, fill: C.nowCore }));
      return;
    }
    if (glow) {
      // 隣接の光: 外輪のみ。理由ラベルは付けない (§1.2)
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 16, fill: "none", stroke: C.glowRing, "stroke-width": 1.25 }));
    }
    if (status === "verified") {
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 10, fill: C.verifiedFill, stroke: C.verifiedStroke, "stroke-width": 1 }));
    } else if (status === "contested") {
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 10, fill: C.contestedFill, stroke: C.contestedStroke, "stroke-width": 1 }));
    } else if (status === "assumed") {
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 10, fill: "none", stroke: C.assumedStroke, "stroke-width": 1.5, "stroke-dasharray": "4 3" }));
    } else {
      // unvisited (個人層の訪問状態) — グレー減衰
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 10, fill: "var(--color-background-tertiary)", stroke: C.fogStroke, "stroke-width": 1 }));
    }
  }

  function appendNodeLabel(g, node, level) {
    const info = nodeInfo(node.id);
    const label = node.label || info.label;
    const status = nodeStatus(node.id);
    if (status === "now") {
      g.appendChild(svgEl("text", { class: "atlas-th", x: node.x, y: node.y - 32, "text-anchor": "middle" }, label));
      g.appendChild(svgEl("text", { class: "atlas-ts", x: node.x, y: node.y + 36, "text-anchor": "middle" }, "いまここ"));
      return;
    }
    if (node.labelPos === "top") {
      g.appendChild(svgEl("text", { class: "atlas-ts", x: node.x, y: node.y - 22, "text-anchor": "middle" }, label));
    } else if (node.labelPos === "left") {
      g.appendChild(svgEl("text", { class: "atlas-ts", x: node.x - 18, y: node.y + 4, "text-anchor": "end" }, label));
    } else {
      g.appendChild(svgEl("text", { class: "atlas-ts", x: node.x, y: node.y + 26, "text-anchor": "middle" }, label));
    }
  }

  // -------------------------------------------------------------------
  // L1: 分野レベル (B-2)
  // -------------------------------------------------------------------

  function renderL1(levelData) {
    const [vw, vh] = levelData.viewBox;
    const svg = svgEl("svg", { width: "100%", viewBox: `0 0 ${vw} ${vh}`, role: "img" });
    svg.appendChild(svgEl("title", null, levelData.svgTitle || "分野レベル"));
    svg.appendChild(svgEl("desc", null, levelData.svgDesc || ""));

    // 45° ハッチ (霧 = モデル知識由来の地形。灯りと視覚的に混ぜない)
    const defs = svgEl("defs");
    const pattern = svgEl("pattern", {
      id: "atlas-fog-hatch", width: 8, height: 8,
      patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)",
    });
    pattern.appendChild(svgEl("line", { x1: 0, y1: 0, x2: 0, y2: 8, stroke: C.fogStroke, "stroke-width": 0.5, opacity: 0.3 }));
    defs.appendChild(pattern);
    svg.appendChild(defs);

    const regions = (levelData.regions || []).slice(0, LIMITS.l1Regions);
    if ((levelData.regions || []).length > LIMITS.l1Regions) {
      console.warn("[atlas] L1 領域が上限を超過。先頭 " + LIMITS.l1Regions + " 件のみ描画");
    }

    // 領域 (灯り / 霧)
    for (const region of regions) {
      if (region.kind === "fog") {
        const g = makeNodeGroup(region.id, 1);
        g.appendChild(svgEl("rect", {
          x: region.x, y: region.y, width: region.w, height: region.h, rx: 12,
          fill: "url(#atlas-fog-hatch)", stroke: C.fogStroke, "stroke-width": 0.5, "stroke-dasharray": "5 4",
        }));
        // 領域名 + 匿名ドットのみ。概念ラベル・誘導文言は置かない (§1.2)
        g.appendChild(svgEl("text", { class: "atlas-ts", x: region.x + 20, y: region.y + 26 }, region.label));
        for (const [dx, dy] of region.dots || []) {
          g.appendChild(svgEl("circle", { cx: dx, cy: dy, r: 3, fill: C.fogStroke, opacity: 0.4 }));
        }
        svg.appendChild(g);
      } else {
        svg.appendChild(svgEl("rect", {
          class: "atlas-box", x: region.x, y: region.y, width: region.w, height: region.h, rx: 14, "stroke-width": 0.5,
        }));
        svg.appendChild(svgEl("text", { class: "atlas-th", x: region.x + 20, y: region.y + 26 }, region.label));
      }
    }

    // 代表概念ノード (領域ごとの上限ガード)
    const perRegion = {};
    const nodes = [];
    for (const node of levelData.nodes || []) {
      const key = node.region || "";
      perRegion[key] = (perRegion[key] || 0) + 1;
      if (perRegion[key] > LIMITS.l1ConceptsPerRegion) {
        console.warn("[atlas] L1 領域 '" + key + "' の代表概念が上限を超過。省略: " + node.id);
        continue;
      }
      nodes.push(node);
    }
    const pos = {};
    nodes.forEach((n) => { pos[n.id] = n; });

    // 足跡: 訪問済みノードを結ぶ青点線ポリライン (数値化しない)
    const fp = (levelData.footprints || []).map((id) => pos[id]).filter(Boolean);
    if (fp.length > 1) {
      svg.appendChild(svgEl("polyline", {
        points: fp.map((n) => `${n.x},${n.y}`).join(" "),
        fill: "none", stroke: C.footprint, "stroke-width": 1, "stroke-dasharray": "3 4", opacity: 0.55,
      }));
    }

    // 領域間リーダー線 (破線)
    for (const [fromId, toId] of levelData.leaders || []) {
      const a = pos[fromId], b = pos[toId];
      if (!a || !b) continue;
      const line = trimmedLine(a, b, 17, 12);
      svg.appendChild(svgEl("line", {
        x1: line.x1, y1: line.y1, x2: line.x2, y2: line.y2,
        stroke: C.fogStroke, "stroke-width": 0.75, "stroke-dasharray": "4 4",
      }));
    }

    for (const node of nodes) {
      const g = makeNodeGroup(node.id, 1);
      appendCircleNode(g, node.x, node.y, node.id);
      appendNodeLabel(g, node, 1);
      svg.appendChild(g);
    }
    return svg;
  }

  // -------------------------------------------------------------------
  // L2: コースレベル (B-3)
  // -------------------------------------------------------------------

  function nodeRadius(id) {
    if (nodeStatus(id) === "now") return 17;
    if (nodeInfo(id).glow) return 16;
    return 10;
  }

  function trimmedLine(a, b, ra, rb) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const pa = (ra + 2) / len, pb = (rb + 2) / len;
    return {
      x1: a.x + dx * pa, y1: a.y + dy * pa,
      x2: b.x - dx * pb, y2: b.y - dy * pb,
    };
  }

  function renderL2(levelData) {
    const [vw, vh] = levelData.viewBox;
    const svg = svgEl("svg", { width: "100%", viewBox: `0 0 ${vw} ${vh}`, role: "img" });
    svg.appendChild(svgEl("title", null, levelData.svgTitle || "コースレベル"));
    svg.appendChild(svgEl("desc", null, levelData.svgDesc || ""));

    const nodes = (levelData.nodes || []).slice(0, LIMITS.l2Nodes);
    if ((levelData.nodes || []).length > LIMITS.l2Nodes) {
      console.warn("[atlas] L2 ノードが上限を超過。先頭 " + LIMITS.l2Nodes + " 件のみ描画");
    }
    const pos = {};
    nodes.forEach((n) => { pos[n.id] = n; });

    // 実線エッジ
    for (const [fromId, toId] of levelData.edges || []) {
      const a = pos[fromId], b = pos[toId];
      if (!a || !b) continue;
      const line = trimmedLine(a, b, nodeRadius(fromId), nodeRadius(toId));
      svg.appendChild(svgEl("line", {
        x1: line.x1, y1: line.y1, x2: line.x2, y2: line.y2,
        stroke: C.edge, "stroke-width": 0.75,
      }));
    }

    for (const node of nodes) {
      const g = makeNodeGroup(node.id, 2);
      appendCircleNode(g, node.x, node.y, node.id);
      appendNodeLabel(g, node, 2);
      svg.appendChild(g);
    }
    return svg;
  }

  // -------------------------------------------------------------------
  // L3: 導出レベル (B-3)
  // -------------------------------------------------------------------

  function renderL3(levelData) {
    const [vw, vh] = levelData.viewBox;
    const svg = svgEl("svg", { width: "100%", viewBox: `0 0 ${vw} ${vh}`, role: "img" });
    svg.appendChild(svgEl("title", null, levelData.svgTitle || "導出レベル"));
    svg.appendChild(svgEl("desc", null, levelData.svgDesc || ""));

    const defs = svgEl("defs");
    const marker = svgEl("marker", {
      id: "atlas-arrow", viewBox: "0 0 10 10", refX: 8, refY: 5,
      markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse",
    });
    marker.appendChild(svgEl("path", {
      d: "M2 1L8 5L2 9", fill: "none", stroke: C.arrow,
      "stroke-width": 1.5, "stroke-linecap": "round", "stroke-linejoin": "round",
    }));
    defs.appendChild(marker);
    svg.appendChild(defs);

    const chain = (levelData.chain || []).slice(0, LIMITS.l3Nodes);
    if ((levelData.chain || []).length > LIMITS.l3Nodes) {
      console.warn("[atlas] L3 ステップが上限を超過。先頭 " + LIMITS.l3Nodes + " 件のみ描画");
    }

    const boxW = 240, boxH = 44, stepY = 72, x = (vw - boxW) / 2, y0 = 40;
    chain.forEach((id, i) => {
      const y = y0 + i * stepY;
      const status = nodeStatus(id);
      const info = nodeInfo(id);
      const g = makeNodeGroup(id, 3);
      if (status === "gap") {
        // 行間ステップ: 破線オレンジ矩形 + ラベル「（行間）」
        g.appendChild(svgEl("rect", {
          x, y, width: boxW, height: boxH, rx: 8,
          fill: "none", stroke: C.assumedStroke, "stroke-width": 1.5, "stroke-dasharray": "5 4",
        }));
      } else if (status === "verified") {
        g.appendChild(svgEl("rect", {
          x, y, width: boxW, height: boxH, rx: 8,
          fill: C.verifiedFill, stroke: C.verifiedStroke, "stroke-width": 1,
        }));
      } else {
        // 終端の接続ノードなど
        g.appendChild(svgEl("rect", {
          class: "atlas-box", x, y, width: boxW, height: boxH, rx: 8, "stroke-width": 0.5,
        }));
      }
      const label = info.label + (status === "gap" ? "（行間）" : "");
      g.appendChild(svgEl("text", {
        class: "atlas-th", x: vw / 2, y: y + boxH / 2,
        "text-anchor": "middle", "dominant-baseline": "central",
      }, label));
      svg.appendChild(g);
      if (i < chain.length - 1) {
        svg.appendChild(svgEl("line", {
          x1: vw / 2, y1: y + boxH, x2: vw / 2, y2: y + stepY - 4,
          stroke: C.arrow, "stroke-width": 1.25, "marker-end": "url(#atlas-arrow)",
        }));
      }
    });
    return svg;
  }

  // -------------------------------------------------------------------
  // レベル切替・選択
  // -------------------------------------------------------------------

  const RENDERERS = { 1: renderL1, 2: renderL2, 3: renderL3 };

  function showLevel(level) {
    state.level = level;
    const ov = document.getElementById("atlas-overlay");
    ov.querySelectorAll(".atlas-seg-btn").forEach((b) => {
      const on = Number(b.getAttribute("data-level")) === level;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    ov.querySelector("#atlas-crumb").textContent = (state.data.crumbs || {})[String(level)] || "";
    const canvas = ov.querySelector("#atlas-canvas");
    canvas.replaceChildren(RENDERERS[level](state.data.levels[String(level)] || { viewBox: [680, 370] }));
    const initial = (state.data.initial_selection || {})[String(level)];
    if (initial) selectNode(initial, { silent: false });
    // 個人知識ネットワーク (Phase P-1): L1 かつ「わたしの地図」ON のときだけ本人の痕跡ドットを
    // 重ね描く。判定・描画は PersonalMap 側の責務 (§9)。
    if (window.PersonalMap) window.PersonalMap.onLevelRendered(level, canvas);
  }

  function selectNode(id, opts) {
    state.selected = id;
    const ov = document.getElementById("atlas-overlay");
    ov.querySelectorAll(".atlas-node").forEach((g) => {
      g.classList.toggle("sel", g.getAttribute("data-node") === id);
    });
    const info = nodeInfo(id);
    ov.querySelector("#atlas-panel-name").textContent = info.label;
    const pill = ov.querySelector("#atlas-panel-pill");
    pill.textContent = info.pill || "";
    const style = PILL_STYLES[nodeStatus(id)] || PILL_STYLES.unvisited;
    pill.style.background = style.bg;
    pill.style.color = style.fg;
    pill.style.border = style.dashed ? "1px dashed " + C.assumedStroke : "none";
    if (!(opts && opts.silent)) {
      // C-1 (詳細パネル) / C-2 (チャット遷移) / D-1 (修正報告) の接続点
      document.dispatchEvent(new CustomEvent("atlas:nodeselect", {
        detail: {
          node_id: id,
          level: state.level,
          skeleton_version: state.data.skeleton_version || "",
        },
      }));
    }
  }

  // -------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------

  // 導線3 (寄り道復帰): 戻った位置を一時ハイライトする (§3-4)。誘導文言は付けない。
  function pulseNode(id) {
    const ov = document.getElementById("atlas-overlay");
    if (!ov) return;
    ov.querySelectorAll('.atlas-node[data-node="' + id + '"]').forEach((g) => {
      g.classList.add("atlas-pulse");
      setTimeout(() => g.classList.remove("atlas-pulse"), 4000);
    });
  }

  // 導線4 (初回ログイン): 自動表示が初回のみであることの注記 (§16-6 の決定:
  // オプトアウトは設定項目ではなく、一度きりの自動表示 + この注記で成立させる)
  function setIntroNote(show) {
    const ov = document.getElementById("atlas-overlay");
    if (!ov) return;
    const existing = ov.querySelector(".atlas-intro-note");
    if (existing) existing.remove();
    if (!show) return;
    const note = el("p", { class: "atlas-intro-note" },
      "分野の地図です。この自動表示は初回のみ — 次回からは左下のミニマップや上部の「地図」からいつでも開けます。");
    const foot = ov.querySelector("#atlas-foot");
    foot.parentNode.insertBefore(note, foot);
  }

  // opts (Issue F): { level, focus, highlight, intro, source }
  // - level / focus 指定時はセッション内復元 (reopen) より優先する
  // - source は導線名 (minimap / topic_complete / ...) — atlas:opened / atlas:closed
  //   イベントに載せ、内部計測 (atlas-cues.js) が拾う。ユーザーには見せない
  function openOverlay(data, opts) {
    opts = opts || {};
    // data === null は「骨格なし」の明示 (API 404)。undefined のときだけフィクスチャへ
    // フォールバックする (骨格なしカートリッジで地図を出さない — issue F 受け入れ条件6)
    const nextData = data === undefined ? window.ATLAS_FIXTURE : data;
    if (!nextData) {
      console.warn("[atlas] 骨格データがありません (地図機能を出さない)");
      return;
    }
    // データ (骨格の版) が差し替わったら復元状態を破棄する
    if (nextData !== state.data) {
      state.reopen = false;
      state.selected = null;
      state.level = 1;
    }
    state.data = nextData;
    state.lastFocus = document.activeElement;
    let ov = document.getElementById("atlas-overlay");
    if (!ov) ov = buildShell();
    ov.querySelector("#atlas-foot").textContent =
      "骨格 " + (state.data.skeleton_version || "—") + " / " + (state.data.provenance || "AI生成");
    ov.removeAttribute("hidden");
    // C-2: 再オープン時は直前のレベル・選択を復元する (初回は L1 + 初期選択)。
    // 導線からの明示オープン (opts.level / opts.focus) は復元より優先する。
    let restoreLevel = state.reopen ? state.level : 1;
    let restoreSel = state.reopen ? state.selected : null;
    if (opts.level || opts.focus) {
      restoreLevel = opts.level || 1;
      restoreSel = opts.focus && state.data.nodes && state.data.nodes[opts.focus]
        ? opts.focus
        : null;
    }
    showLevel(restoreLevel);
    if (restoreSel) selectNode(restoreSel);
    const contextId = restoreSel || state.selected ||
      ((state.data.initial_selection || {})[String(restoreLevel)] || "");
    const contextEl = ov.querySelector("#atlas-context");
    if (contextEl) {
      contextEl.textContent = contextId && state.data.nodes && state.data.nodes[contextId]
        ? "現在地: 「" + (state.data.nodes[contextId].label || contextId) + "」。ノードを選ぶと、記帳された根拠と学習への入口を確認できます。"
        : "ノードを選ぶと、その位置に記帳された根拠と学習への入口を確認できます。";
    }
    if (opts.highlight) pulseNode(restoreSel || state.selected);
    setIntroNote(!!opts.intro);
    state.openSource = opts.source || "";
    state.openedAt = Date.now();
    document.dispatchEvent(new CustomEvent("atlas:opened", {
      detail: { source: state.openSource },
    }));
    const seg = ov.querySelector('.atlas-seg-btn[data-level="' + restoreLevel + '"]');
    if (seg) seg.focus();
  }

  function closeOverlay() {
    const ov = document.getElementById("atlas-overlay");
    if (!ov || ov.hasAttribute("hidden")) return;
    ov.setAttribute("hidden", "");
    state.reopen = true; // 次回オープンで選択・レベルを復元 (セッション内のみ)
    document.dispatchEvent(new CustomEvent("atlas:closed", {
      detail: {
        source: state.openSource,
        duration_ms: state.openedAt ? Date.now() - state.openedAt : 0,
      },
    }));
    if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
    // 個人知識ネットワーク (Phase P-1): マーカーの小ポップ等を後始末する。
    if (window.PersonalMap) window.PersonalMap.onOverlayClosed();
  }

  window.AtlasOverlay = {
    open: openOverlay,
    close: closeOverlay,
    showLevel,
    selectNode,
    get selected() { return state.selected; },
    get level() { return state.level; },
    // C-1/C-3: 詳細パネル (atlas-panel.js) が台帳行・学習パス素材を参照する
    get data() { return state.data; },
  };

  // エントリポイント: トップバーの「地図」ボタン (ミニマップと並ぶ常設導線)
  // データソースは atlas-data.js が解決する (fixture ⇄ Atlas API の設定切替。Issue E-3)
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("atlas-btn");
    if (btn) btn.addEventListener("click", async () => {
      const data = window.AtlasData ? await window.AtlasData.load() : undefined;
      if (window.AtlasData && !data) {
        // 骨格なしカートリッジでは地図機能ごと出さない
        btn.style.display = "none";
        return;
      }
      // 「ただ開くだけ」の呼び出し: level/focus を渡さず openOverlay の C-2 復元
      // (state.reopen ? 直前のレベル・選択 : 初回は L1) に委ねる。
      openOverlay(data, { source: "topbar" });
    });
  });
})();
