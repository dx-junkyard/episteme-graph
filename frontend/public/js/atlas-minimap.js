/* 分野の地図 — 常設ミニマップ (Issue F-1)
 *
 * 学習パスパネル (左サイドバー) 下部の切手大表示。「地平線の存在を常に感じさせる」
 * ためのもので、情報を詰めない。表示するのは次の3つ**だけ** (受け入れ条件1):
 *   - 「いまここ」の位置 (青の点。二重円の簡略形)
 *   - 訪問済み・状態ノードの簡略ドット (色は §5 準拠、ラベルなし)
 *   - 霧領域のハッチ簡略表現 (灯り/霧の割合が面積の印象で伝わる)
 * 数値 (%・件数)・ラベル・凡例は一切描かない (§1.2-4)。
 *
 * 縮約アルゴリズム (issue F 未決事項の決定): L1 をそのまま縮小する。
 * 領域ボックス・霧ハッチはそのまま、代表概念ノードは無ラベルのドットへ簡略化。
 *
 * データ: GET /api/atlas (L1) の応答から縮約描画 (専用エンドポイントなし)。
 * 更新: トピック遷移 (app.js が refresh() を呼ぶ) とオーバーレイ閉時
 * ("atlas:closed" 購読) のみ。常時ポーリングしない。
 * 表示条件: 骨格を同梱したカートリッジのみ (AtlasData.load() が null → 領域ごと非表示)。
 */
(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";

  // §5 の視覚言語 (atlas-overlay.js と同じ仕様定数の縮約サブセット)
  const C = {
    verifiedFill: "#E1F5EE", verifiedStroke: "#0F6E56",
    contestedFill: "#FAEEDA", contestedStroke: "#854F0B",
    assumedStroke: "#BA7517",
    nowRing: "#378ADD", nowCore: "#185FA5", nowFill: "#E6F1FB",
    fogStroke: "#888780",
  };

  const state = {
    data: null,       // 直近取得の地図データ (levels["1"] を縮約描画)
    available: null,  // null = 未判定, false = 骨格なし (領域ごと非表示)
    loading: false,
  };

  const svgEl = (tag, attrs) => {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };

  // -------------------------------------------------------------------
  // 縮約描画: L1 をそのまま縮小 (ラベル・数値なし)
  // -------------------------------------------------------------------

  function dotForNode(id, x, y) {
    const info = (state.data.nodes || {})[id] || {};
    const status = info.status || "unvisited";
    const g = document.createDocumentFragment();
    if (status === "now") {
      // いまここ: 二重円の簡略形
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 16, fill: "none", stroke: C.nowRing, "stroke-width": 3 }));
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 8, fill: C.nowCore }));
      return g;
    }
    if (status === "verified") {
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: C.verifiedFill, stroke: C.verifiedStroke, "stroke-width": 2 }));
    } else if (status === "contested") {
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: C.contestedFill, stroke: C.contestedStroke, "stroke-width": 2 }));
    } else if (status === "assumed") {
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: "none", stroke: C.assumedStroke, "stroke-width": 2.5, "stroke-dasharray": "4 3" }));
    } else {
      // unvisited ほか — グレー減衰ドット
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: 6, fill: "var(--color-background-tertiary)", stroke: C.fogStroke, "stroke-width": 1.5 }));
    }
    return g;
  }

  function renderSvg() {
    const l1 = ((state.data || {}).levels || {})["1"];
    if (!l1) return null;
    const [vw, vh] = l1.viewBox || [680, 370];
    const svg = svgEl("svg", {
      viewBox: `0 0 ${vw} ${vh}`,
      width: "100%",
      "aria-hidden": "true", // ボタン側の aria-label が説明する (装飾扱い)
      focusable: "false",
    });

    const defs = svgEl("defs");
    const pattern = svgEl("pattern", {
      id: "atlas-mini-fog-hatch", width: 10, height: 10,
      patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)",
    });
    pattern.appendChild(svgEl("line", { x1: 0, y1: 0, x2: 0, y2: 10, stroke: C.fogStroke, "stroke-width": 1.5, opacity: 0.35 }));
    defs.appendChild(pattern);
    svg.appendChild(defs);

    // 領域: 灯り = 淡い枠ボックス / 霧 = ハッチ + 破線枠 (面積の印象で割合を伝える)
    for (const region of l1.regions || []) {
      if (region.kind === "fog") {
        svg.appendChild(svgEl("rect", {
          x: region.x, y: region.y, width: region.w, height: region.h, rx: 10,
          fill: "url(#atlas-mini-fog-hatch)", stroke: C.fogStroke,
          "stroke-width": 1, "stroke-dasharray": "6 5", opacity: 0.9,
        }));
      } else {
        svg.appendChild(svgEl("rect", {
          x: region.x, y: region.y, width: region.w, height: region.h, rx: 12,
          fill: "var(--color-background-secondary, #f5f5f7)",
          stroke: "var(--color-border, #d2d2d7)", "stroke-width": 1,
        }));
      }
    }

    // ノードの簡略ドット (ラベルなし)。「いまここ」は最後に描いて最前面へ
    let nowNode = null;
    for (const node of l1.nodes || []) {
      const info = (state.data.nodes || {})[node.id] || {};
      if ((info.status || "") === "now") { nowNode = node; continue; }
      svg.appendChild(dotForNode(node.id, node.x, node.y));
    }
    if (nowNode) svg.appendChild(dotForNode(nowNode.id, nowNode.x, nowNode.y));
    return svg;
  }

  function renderInto(host) {
    host.replaceChildren();
    if (!state.data) return;
    const svg = renderSvg();
    if (!svg) return;

    const btn = document.createElement("div");
    btn.className = "atlas-minimap";
    btn.id = "atlas-minimap";
    btn.setAttribute("role", "button");
    btn.setAttribute("tabindex", "0");
    btn.setAttribute("aria-label", "分野の地図を開く。現在地と全体の広がりの縮図");
    btn.setAttribute("title", "分野の地図を開く");
    btn.appendChild(svg);

    const activate = async (e) => {
      e.preventDefault();
      // クリックは常にオーバーレイを開くだけ (ノード直接選択はしない — 非スコープ)。
      // L1・「いまここ」中心で開く (受け入れ条件2)
      const data = window.AtlasData ? await window.AtlasData.load() : undefined;
      if (window.AtlasData && !data) { setAvailable(false); return; }
      if (data) state.data = data;
      if (window.AtlasOverlay) window.AtlasOverlay.open(data, { level: 1, source: "minimap" });
    };
    btn.addEventListener("click", activate);
    btn.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") activate(e);
    });
    host.appendChild(btn);
  }

  function setAvailable(v) {
    state.available = v;
    if (v === false) {
      state.data = null;
      const host = document.getElementById("atlas-minimap-slot");
      if (host) host.replaceChildren();
      const topBtn = document.getElementById("atlas-btn");
      if (topBtn) topBtn.style.display = "none";
    }
  }

  // -------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------

  // sidebar の再描画 (innerHTML 差し替え) 後に呼ばれる。スロットを末尾に確保し、
  // 手元のデータで即描画する (データが無ければ refresh を仕掛ける)。
  function mount(sidebarEl) {
    if (!sidebarEl || state.available === false) return;
    let slot = document.getElementById("atlas-minimap-slot");
    if (!slot || slot.parentNode !== sidebarEl) {
      slot = document.createElement("div");
      slot.id = "atlas-minimap-slot";
      sidebarEl.appendChild(slot);
    }
    if (state.data) {
      renderInto(slot);
    } else {
      refresh();
    }
  }

  // 再取得して描画。呼び出し契機はトピック遷移・オーバーレイ閉時・初回 mount のみ
  // (常時ポーリングしない)。多重呼び出しは合流させる。
  async function refresh() {
    if (state.available === false || state.loading) return;
    if (!window.AtlasData) return;
    state.loading = true;
    try {
      const data = await window.AtlasData.load();
      if (!data) { setAvailable(false); return; }
      state.available = true;
      state.data = data;
      const slot = document.getElementById("atlas-minimap-slot");
      if (slot) renderInto(slot);
    } catch (err) {
      console.warn("[atlas] ミニマップの更新に失敗:", err);
    } finally {
      state.loading = false;
    }
  }

  document.addEventListener("atlas:closed", () => { refresh(); });

  window.AtlasMinimap = {
    mount,
    refresh,
    // atlas-cues.js が骨格の有無 (受け入れ条件6) を確認するための読み取り口
    get available() { return state.available; },
  };
})();
