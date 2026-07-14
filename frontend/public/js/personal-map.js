/* 個人知識ネットワーク（Phase P-1）— 「わたしの地図」UI
 *
 * 設計の正本: docs/features/personal_knowledge_network_design.md §9（フロント「わたしの地図」）。
 * 参照する不変条項（同 §0）: PN-1（本人のみ可視）/ PN-2（導出のみ・保存しない）/
 * PN-3（本人確定のみノード化。サーバ側 core/personal_graph/derive.py が担保）/
 * PN-4（数値を見せない。件数・%・スコアは一切描かない）/ PN-5（非LLM・決定論・自動で開かない）/
 * PN-7（fail-closed。コース文脈・トークン・骨格が無ければ機能ごと隠す）。
 *
 * atlas-overlay.js の L1 描画の上に、本人の痕跡（tension/question/reconstruction、
 * および bridge 辺を張った tension の variant）を kind 別の小さな図形で重ねるだけの薄い層。
 * 既存の描画（状態ドット・いまここ・足あと・霧）は一切変更しない。atlas-minimap.js には触れない。
 *
 * 公開契約 window.PersonalMap（呼び出し側は app.js。名前・引数は固定）:
 *   init(deps)                          — deps.openTrajectory(traceId) を登録
 *   mountControls(shellEl)              — atlas-overlay buildShell 完了時に一度だけ呼ばれる
 *   onLevelRendered(level, canvasEl)    — atlas-overlay showLevel の描画後に毎回呼ばれる
 *   onOverlayClosed()                   — atlas-overlay closeOverlay 時
 *   annotateTrajectoryList(containerEl) — 問いの軌跡一覧に「地図で見る」を後付け
 *   invalidate()                        — コース切替時にキャッシュ・トグル状態を破棄
 */
(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const API_BASE = "/api";

  // kind 別の表示名（PN-4: 個数・割合は一切添えない）。
  const KIND_META = {
    question: { label: "問い" },
    tension: { label: "引っかかり" },
    reconstruction: { label: "再構成" },
    bridge: { label: "橋" }, // tension のうち bridge 辺の from になっているものの variant
  };
  const KIND_DISPLAY_ORDER = ["question", "tension", "reconstruction", "bridge"];
  // atlas ノード中心からの固定オフセット（kind ごとに位置を固定し、地図全体で視覚言語を揃える）
  const MARKER_OFFSETS = {
    tension: [-9, -9],
    bridge: [9, -9],
    question: [9, 9],
    reconstruction: [-9, 9],
  };

  const state = {
    deps: null,
    enabled: false,
    courseId: "",
    networkCache: {}, // courseId -> { promise: Promise<data|null>, data: object|null }
    controlsEl: null,
    toggleInputEl: null,
    legendEl: null,
    trayEl: null,
    popupEl: null,
    lastCanvas: null,
    lastLevel: 1,
  };

  // -------------------------------------------------------------------
  // 認証・取得（reconstruction.js / atlas-cues.js と同じ様式）
  // -------------------------------------------------------------------

  function token() {
    try {
      return localStorage.getItem("eg_token") || null;
    } catch (e) {
      return null; // localStorage 不可の環境ではそのまま非表示扱いにする
    }
  }

  function currentCourseId() {
    return (window.AtlasContext && window.AtlasContext.courseId) || "";
  }

  // コース単位で一度だけ fetch し in-memory キャッシュする（PN-2: サーバに保存させない・
  // ポーリングしない）。401/404/失敗はすべて null に丸め、呼び出し側が静かに諦められるようにする。
  function loadNetwork(courseId) {
    if (!courseId) return Promise.resolve(null);
    const cached = state.networkCache[courseId];
    if (cached) return cached.promise;
    const t = token();
    if (!t) return Promise.resolve(null);

    const entry = { promise: null, data: null };
    entry.promise = fetch(
      API_BASE + "/learning/courses/" + encodeURIComponent(courseId) + "/personal-network",
      { headers: { Authorization: "Bearer " + t } }
    )
      .then((res) => {
        if (!res.ok) throw new Error("personal-network " + res.status);
        return res.json();
      })
      .then((data) => {
        entry.data = data;
        return data;
      })
      .catch(() => {
        // fail-closed: 失敗時はキャッシュを残さず、次回呼び出しで再試行できるようにする
        delete state.networkCache[courseId];
        return null;
      });
    state.networkCache[courseId] = entry;
    return entry.promise;
  }

  function cachedData(courseId) {
    const entry = state.networkCache[courseId];
    return entry ? entry.data : null;
  }

  // -------------------------------------------------------------------
  // kind 判定（bridge variant の解決）
  // -------------------------------------------------------------------

  function bridgeFromIds(data) {
    const s = new Set();
    (data.edges || []).forEach((e) => {
      if (e.edge_kind === "bridge") s.add(e.from_node_id);
    });
    return s;
  }

  function displayKind(node, bridgeIds) {
    if (node.node_kind === "tension" && bridgeIds.has(node.id)) return "bridge";
    return node.node_kind;
  }

  function groupByAtlasNode(data) {
    const bridgeIds = bridgeFromIds(data);
    const groups = {}; // atlas_node_id -> { kinds: string[], nodes: [] }
    (data.nodes || []).forEach((n) => {
      const atlasId = n.anchor && n.anchor.atlas_node_id;
      if (!atlasId) return;
      const kind = displayKind(n, bridgeIds);
      if (!groups[atlasId]) groups[atlasId] = { kinds: [], nodes: [] };
      if (groups[atlasId].kinds.indexOf(kind) === -1) groups[atlasId].kinds.push(kind);
      groups[atlasId].nodes.push({ node: n, kind });
    });
    return groups;
  }

  // 「まだ地図にない」対象: atlas_node_id を持たないノード（P4: 捨てない・一覧で保持する）
  function unanchoredEntries(data) {
    const bridgeIds = bridgeFromIds(data);
    return (data.nodes || [])
      .filter((n) => !(n.anchor && n.anchor.atlas_node_id))
      .map((n) => ({ node: n, kind: displayKind(n, bridgeIds) }));
  }

  // -------------------------------------------------------------------
  // 図形描画（L1 canvas への重ね書き）
  // -------------------------------------------------------------------

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
    return node;
  }

  function makeMarkerShape(kind, x, y) {
    const r = 4.5;
    const cls = "personal-map-dot personal-map-dot-" + kind;
    if (kind === "question") {
      return svgEl("circle", { cx: x, cy: y, r: r, class: cls });
    }
    if (kind === "reconstruction") {
      return svgEl("rect", { x: x - r, y: y - r, width: r * 2, height: r * 2, class: cls });
    }
    if (kind === "bridge") {
      const d = r + 1;
      const pts = [[x, y - d], [x + d, y], [x, y + d], [x - d, y]].map((p) => p.join(",")).join(" ");
      return svgEl("polygon", { points: pts, class: cls });
    }
    // tension（既定・三角形）
    const pts = [[x, y - r], [x + r, y + r], [x - r, y + r]].map((p) => p.join(",")).join(" ");
    return svgEl("polygon", { points: pts, class: cls });
  }

  function renderDotsLayer(canvasEl) {
    if (!canvasEl) return;
    const svg = canvasEl.querySelector("svg");
    if (!svg) return;
    const existing = svg.querySelector(".personal-map-layer");
    if (existing) existing.remove();
    if (!state.enabled) return;
    // 座標は L1 のもの（AtlasOverlay.data.levels["1"]）なので、L2/L3 表示中に
    // トグルが ON になっても描かない（レベルが 1 に戻った時点で onLevelRendered が描く）。
    if (state.lastLevel !== 1) return;

    const data = cachedData(state.courseId);
    if (!data) return;
    const overlayData = window.AtlasOverlay && window.AtlasOverlay.data;
    const levelData = overlayData && overlayData.levels && overlayData.levels["1"];
    if (!levelData) return;

    const posById = {};
    (levelData.nodes || []).forEach((n) => { posById[n.id] = n; });

    const groups = groupByAtlasNode(data);
    const atlasIds = Object.keys(groups);
    if (!atlasIds.length) return;

    const layer = svgEl("g", { class: "personal-map-layer" });
    atlasIds.forEach((atlasId) => {
      const pos = posById[atlasId];
      if (!pos) return; // このズームでは描かれていない位置（省いて良い。件数化しない）
      const group = groups[atlasId];
      const g = svgEl("g", {
        class: "personal-map-marker-group",
        tabindex: "0",
        role: "button",
        "aria-label": "この位置の自分の記録を見る",
      });
      KIND_DISPLAY_ORDER.forEach((kind) => {
        if (group.kinds.indexOf(kind) === -1) return; // 同一 atlas ノードでも kind につき1個のみ
        const off = MARKER_OFFSETS[kind];
        g.appendChild(makeMarkerShape(kind, pos.x + off[0], pos.y + off[1]));
      });
      const open = (evt) => { evt.preventDefault(); evt.stopPropagation(); showPopup(group.nodes, evt); };
      g.addEventListener("click", open);
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") open(e); });
      layer.appendChild(g);
    });
    svg.appendChild(layer);
  }

  // -------------------------------------------------------------------
  // マーカークリック時の小ポップ（本人の痕跡一覧・数値なし）
  // -------------------------------------------------------------------

  function closePopup() {
    if (state.popupEl) {
      state.popupEl.remove();
      state.popupEl = null;
    }
    document.removeEventListener("click", onDocClickClosePopup, true);
  }

  function onDocClickClosePopup(e) {
    if (state.popupEl && !state.popupEl.contains(e.target)) closePopup();
  }

  function positionPopup(popup, evt) {
    const x = (evt && typeof evt.clientX === "number") ? evt.clientX : window.innerWidth / 2;
    const y = (evt && typeof evt.clientY === "number") ? evt.clientY : window.innerHeight / 2;
    const rect = popup.getBoundingClientRect();
    const left = Math.min(Math.max(8, x + 8), window.innerWidth - rect.width - 8);
    const top = Math.min(Math.max(8, y + 8), window.innerHeight - rect.height - 8);
    popup.style.left = left + "px";
    popup.style.top = top + "px";
  }

  function showPopup(entries, evt) {
    closePopup();
    const popup = document.createElement("div");
    popup.className = "personal-map-popup";
    popup.setAttribute("role", "dialog");

    const list = document.createElement("div");
    list.className = "personal-map-popup-list";
    entries.forEach(({ node, kind }) => {
      const item = document.createElement("div");
      item.className = "personal-map-popup-item";

      const kindLabel = document.createElement("span");
      kindLabel.className = "personal-map-popup-kind";
      kindLabel.textContent = (KIND_META[kind] || {}).label || node.node_kind;
      item.appendChild(kindLabel);

      const text = document.createElement("span");
      text.className = "personal-map-popup-text";
      text.textContent = node.label || "";
      item.appendChild(text);

      // init 未呼び出し（openTrajectory 未登録）なら導線を出さない
      if (state.deps && typeof state.deps.openTrajectory === "function") {
        const link = document.createElement("button");
        link.type = "button";
        link.className = "personal-map-popup-link";
        link.textContent = "軌跡で見る";
        link.addEventListener("click", () => {
          closePopup();
          state.deps.openTrajectory(node.id);
          if (window.AtlasOverlay) window.AtlasOverlay.close();
        });
        item.appendChild(link);
      }
      list.appendChild(item);
    });
    popup.appendChild(list);

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "personal-map-popup-close";
    closeBtn.setAttribute("aria-label", "閉じる");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", closePopup);
    popup.appendChild(closeBtn);

    document.body.appendChild(popup);
    positionPopup(popup, evt);
    state.popupEl = popup;
    setTimeout(() => document.addEventListener("click", onDocClickClosePopup, true), 0);
  }

  // -------------------------------------------------------------------
  // トグル・凡例・「まだ地図にない」トレイ（オーバーレイシェル内に常設）
  // -------------------------------------------------------------------

  function updateLegendTrayVisibility() {
    if (state.legendEl) state.legendEl.hidden = !state.enabled;
    if (state.trayEl && !state.enabled) {
      state.trayEl.hidden = true;
      state.trayEl.innerHTML = "";
    }
  }

  function renderTray(data) {
    const tray = state.trayEl;
    if (!tray) return;
    tray.innerHTML = "";
    if (!state.enabled || !data) { tray.hidden = true; return; }
    const entries = unanchoredEntries(data);
    if (!entries.length) { tray.hidden = true; return; } // 空なら見出しごと非表示（異常演出にしない）

    const heading = document.createElement("div");
    heading.className = "personal-map-tray-heading";
    heading.textContent = "まだ地図にない";
    tray.appendChild(heading);

    const list = document.createElement("div");
    list.className = "personal-map-tray-list";
    entries.forEach(({ node, kind }) => {
      const row = document.createElement("div");
      row.className = "personal-map-tray-item";
      const kindLabel = document.createElement("span");
      kindLabel.className = "personal-map-tray-kind";
      kindLabel.textContent = (KIND_META[kind] || {}).label || node.node_kind;
      const text = document.createElement("span");
      text.className = "personal-map-tray-text";
      text.textContent = node.label || "";
      row.appendChild(kindLabel);
      row.appendChild(text);
      list.appendChild(row);
    });
    tray.appendChild(list);
    tray.hidden = false;
  }

  function onToggleChange(checked) {
    if (!checked) {
      state.enabled = false;
      closePopup();
      updateLegendTrayVisibility();
      renderDotsLayer(state.lastCanvas);
      return;
    }
    const courseId = state.courseId;
    if (!courseId || !token()) {
      // fail-closed: コース文脈・トークンが無ければ ON にしない
      if (state.toggleInputEl) state.toggleInputEl.checked = false;
      return;
    }
    state.enabled = true;
    updateLegendTrayVisibility();
    loadNetwork(courseId).then((data) => {
      if (!data) {
        // 401/404/失敗時は静かに OFF へ戻す（エラーバナーは出さない）
        state.enabled = false;
        if (state.toggleInputEl) state.toggleInputEl.checked = false;
        updateLegendTrayVisibility();
        renderDotsLayer(state.lastCanvas);
        return;
      }
      renderDotsLayer(state.lastCanvas);
      renderTray(data);
      updateLegendTrayVisibility();
    });
  }

  function buildControls(shellEl) {
    const wrap = document.createElement("div");
    wrap.className = "personal-map-controls";
    wrap.hidden = true; // 可視性は refreshContext がコース文脈・トークンの有無で判定する

    const row = document.createElement("div");
    row.className = "personal-map-toggle-row";
    const label = document.createElement("label");
    label.className = "personal-map-toggle";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.addEventListener("change", () => onToggleChange(checkbox.checked));
    label.appendChild(checkbox);
    label.appendChild(document.createTextNode("わたしの地図"));
    row.appendChild(label);
    wrap.appendChild(row);

    const legend = document.createElement("div");
    legend.className = "personal-map-legend";
    legend.hidden = true; // トグル ON のときのみ提示
    KIND_DISPLAY_ORDER.forEach((kind) => {
      const item = document.createElement("span");
      item.className = "personal-map-legend-item";
      const swatch = document.createElement("span");
      swatch.className = "personal-map-legend-swatch personal-map-dot-" + kind;
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(KIND_META[kind].label));
      legend.appendChild(item);
    });
    wrap.appendChild(legend);

    const tray = document.createElement("div");
    tray.className = "personal-map-tray";
    tray.hidden = true;
    wrap.appendChild(tray);

    // 凡例（atlas-legend）の直後に差し込む。無ければフッター手前・最悪シェル末尾。
    const atlasLegend = shellEl.querySelector(".atlas-legend");
    const foot = shellEl.querySelector("#atlas-foot");
    if (atlasLegend && atlasLegend.parentNode) {
      atlasLegend.parentNode.insertBefore(wrap, atlasLegend.nextSibling);
    } else if (foot && foot.parentNode) {
      foot.parentNode.insertBefore(wrap, foot);
    } else {
      shellEl.appendChild(wrap);
    }

    state.controlsEl = wrap;
    state.toggleInputEl = checkbox;
    state.legendEl = legend;
    state.trayEl = tray;
  }

  // 現在のコース文脈・トークンの有無からトグルの可視性を再判定する（PN-1/PN-7 fail-closed）。
  // showLevel はレベル切替・再オープンのたびに呼ばれるため、ここで毎回再評価すれば
  // 「buildShell は一度きり」でも文脈の変化（コース切替）に追随できる。
  function refreshContext() {
    const courseId = currentCourseId();
    if (courseId !== state.courseId) {
      // コースが変わっていたら取り違え防止のためトグルを落とす
      state.enabled = false;
      if (state.toggleInputEl) state.toggleInputEl.checked = false;
      closePopup();
    }
    state.courseId = courseId;
    if (state.controlsEl) {
      state.controlsEl.hidden = !(courseId && token());
    }
    updateLegendTrayVisibility();
  }

  // -------------------------------------------------------------------
  // 問いの軌跡ビューとの相互リンク
  // -------------------------------------------------------------------

  function openAtlasFocusedOn(atlasNodeId) {
    if (!window.AtlasData || !window.AtlasOverlay) return;
    Promise.resolve(window.AtlasData.load()).then((data) => {
      if (!data) return; // fail-closed: 骨格なし・取得失敗なら何もしない
      // refreshContext のコース取り違え検知（courseId 不一致→トグル解除）が
      // 直後の open で誤発火しないよう、先に現在コースへ同期してから ON にする。
      state.courseId = currentCourseId();
      state.enabled = true;
      if (state.toggleInputEl) state.toggleInputEl.checked = true;
      window.AtlasOverlay.open(data, { focus: atlasNodeId, source: "personal-map" });
    });
  }

  function annotateTrajectoryList(containerEl) {
    if (!containerEl) return;
    const courseId = currentCourseId();
    if (!courseId || !token()) return; // コース文脈なし/未ログインなら何もしない
    const items = containerEl.querySelectorAll("[data-trace-id]");
    if (!items.length) return;
    loadNetwork(courseId).then((data) => {
      if (!data) return; // fail-closed
      const byId = {};
      (data.nodes || []).forEach((n) => { byId[n.id] = n; });
      items.forEach((itemEl) => {
        const traceId = itemEl.getAttribute("data-trace-id");
        const node = traceId && byId[traceId];
        if (!node || !(node.anchor && node.anchor.atlas_node_id)) return;
        if (itemEl.querySelector(".personal-map-locate-btn")) return; // 二重付与防止
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "personal-map-locate-btn";
        btn.textContent = "地図で見る";
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          openAtlasFocusedOn(node.anchor.atlas_node_id);
        });
        itemEl.appendChild(btn);
      });
    });
  }

  // -------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------

  function init(deps) {
    state.deps = deps || {};
  }

  function mountControls(shellEl) {
    if (!shellEl || state.controlsEl) return; // 二重マウント防止
    buildControls(shellEl);
    refreshContext();
  }

  function onLevelRendered(level, canvasEl) {
    state.lastLevel = level;
    state.lastCanvas = canvasEl;
    refreshContext();
    if (level === 1) {
      renderDotsLayer(canvasEl);
      // 「地図で見る」経由（onToggleChange を通らない ON）でもトレイ・凡例を揃える
      if (state.enabled) {
        renderTray(cachedData(state.courseId));
        updateLegendTrayVisibility();
      }
    }
  }

  function onOverlayClosed() {
    closePopup();
  }

  function invalidate() {
    state.networkCache = {};
    state.enabled = false;
    state.courseId = "";
    if (state.toggleInputEl) state.toggleInputEl.checked = false;
    closePopup();
    updateLegendTrayVisibility();
    renderDotsLayer(state.lastCanvas);
  }

  window.PersonalMap = {
    init,
    mountControls,
    onLevelRendered,
    onOverlayClosed,
    annotateTrajectoryList,
    invalidate,
  };
})();
