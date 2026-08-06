/* 知識ランドスケープ（学習者側）— 「論文の位置」レイヤー + 出典タブ用データ供給
 *
 * 設計の正本: docs/features/knowledge_landscape_design.md §4.2（受講者 UX）・
 * §9.2（学習者 API 契約）・§10.1（本ファイルの契約）。参照する不変条項（同 §0）:
 *   LS1 地図は正解ではなく投影 — 1論文が複数領域・複数観点に載るのが既定。断定しない
 *   LS4 evidence-based — reason と原文逐語 quote を必ず併記する
 *   LS5 数値を見せない — weight / confidence の生値は API にもUIにも出さない。
 *       表示はサーバが返す段階ラベル（weight_label）と出所ラベル（provenance_label）のみ
 *   LS6 fail-closed — 取得失敗・データ無しは機能ごと非表示（フィクスチャに退避しない）
 *   LS7 地図の安定性 — 骨格の座標は一切動かさない。既存ノード・エッジ・ミニマップに触らない
 *   LS8 コーパスの正直さ — 論文件数・骨格版・AI推定を含むことを事実文で明示する
 *   LS9 同期パスに LLM を入れない — 読み取り API のみ・ポーリングしない
 *
 * atlas-overlay.js の L1 描画の上に 📄 マーカーを1レイヤー重ねるだけの薄い層で、
 * personal-map.js と同じ3フック契約（mountControls / onLevelRendered / onOverlayClosed）で
 * 呼ばれる。既存の描画（領域・概念ノード・状態ドット・霧・足あと）は一切変更せず、
 * atlas-minimap.js には触れない（LS7）。
 *
 * 公開契約 window.LandscapeLayer（呼び出し側は atlas-overlay.js と app.js。名前・引数は固定）:
 *   init(deps)                       — 予約（現状 deps は未使用。personal-map.js と揃えた形）
 *   mountControls(sheetEl)           — atlas-overlay buildShell 完了時に一度だけ呼ばれる
 *   onLevelRendered(level, canvasEl) — atlas-overlay showLevel の描画後に毎回呼ばれる
 *   onOverlayClosed()                — atlas-overlay closeOverlay 時
 *   getData(courseId)                — 出典タブ（app.js）が同一 fetch を共有するための取得口
 *   invalidate()                     — コース切替時にキャッシュ・トグル状態を破棄
 */
(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const API_BASE = "/api";

  // 同一ノードに載る論文が多いときの見せ方（§10.1）。4件までは横に並べ、
  // 5件以上は1個のマーカー + 「+N」に集約する（地図を潰さない）。
  const MAX_MARKERS_PER_NODE = 4;
  // 概念ノード中心からのオフセット（ノード外輪 r=17 を避ける）
  const CONCEPT_OFFSET = [16, -14];
  const MARKER_STEP = 14;

  const state = {
    deps: null,
    enabled: false,
    courseId: "",
    cache: {}, // courseId -> { promise: Promise<data|null>, data: object|null }
    controlsEl: null,
    toggleInputEl: null,
    factEl: null,
    popupEl: null,
    lastCanvas: null,
    lastLevel: 1,
  };

  // -------------------------------------------------------------------
  // 認証・取得（personal-map.js と同じ様式。ポーリングしない・LS9）
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

  // コース単位で一度だけ fetch し in-memory キャッシュする。非 ok・例外はすべて
  // null に丸め、呼び出し側が静かに諦められるようにする（LS6 fail-closed。
  // フィクスチャへ退避しない — window.ATLAS_FIXTURE は参照しない）。
  function loadLandscape(courseId) {
    if (!courseId) return Promise.resolve(null);
    const cached = state.cache[courseId];
    if (cached) return cached.promise;
    const t = token();
    if (!t) return Promise.resolve(null);

    const entry = { promise: null, data: null };
    entry.promise = fetch(
      API_BASE + "/learning/courses/" + encodeURIComponent(courseId) + "/landscape",
      { headers: { Authorization: "Bearer " + t } }
    )
      .then((res) => {
        if (!res.ok) return null; // 404（骨格なし・配置なし）も含めて非表示へ縮退
        return res.json();
      })
      .then((data) => {
        entry.data = data || null;
        return entry.data;
      })
      .catch(() => {
        // 失敗はキャッシュに残さず、次回呼び出しで再試行できるようにする
        delete state.cache[courseId];
        return null;
      });
    state.cache[courseId] = entry;
    return entry.promise;
  }

  function cachedData(courseId) {
    const entry = state.cache[courseId];
    return entry ? entry.data : null;
  }

  // 学習者に見せられる配置が1件でもあるか（無ければトグルごと出さない・LS6）
  function hasPlacements(data) {
    if (!data || !Array.isArray(data.documents)) return false;
    for (let i = 0; i < data.documents.length; i++) {
      const ps = data.documents[i] && data.documents[i].placements;
      if (Array.isArray(ps) && ps.length) return true;
    }
    return false;
  }

  // -------------------------------------------------------------------
  // コーパスの正直さ（LS8）— 件数・骨格版・AI推定の有無を事実文で出す
  // -------------------------------------------------------------------

  function skeletonVersionText(data) {
    const domains = (data && data.domains) || [];
    let picked = null;
    for (let i = 0; i < domains.length; i++) {
      if (domains[i] && domains[i].is_course_map) { picked = domains[i]; break; }
    }
    if (!picked && domains.length) picked = domains[0];
    return (picked && picked.frozen_version) ? String(picked.frozen_version) : "";
  }

  function includesInferred(data) {
    const docs = (data && data.documents) || [];
    for (let i = 0; i < docs.length; i++) {
      const ps = (docs[i] && docs[i].placements) || [];
      for (let j = 0; j < ps.length; j++) {
        if (ps[j] && ps[j].status !== "confirmed") return true;
      }
    }
    return false;
  }

  function corpusFactText(data) {
    const corpus = (data && data.corpus) || {};
    const n = typeof corpus.source_document_count === "number" ? corpus.source_document_count : 0;
    const version = skeletonVersionText(data);
    const notes = [];
    if (version) notes.push("骨格版 " + version);
    if (includesInferred(data)) notes.push("AI推定を含む");
    let text = "登録済み論文 " + n + " 件の解析から生成";
    if (notes.length) text += "（" + notes.join("・") + "）";
    return text;
  }

  // -------------------------------------------------------------------
  // 配置のノード別グルーピング
  // -------------------------------------------------------------------

  // node_id -> [{ title, node_label, node_kind, placements: [...] }]（論文単位）
  function groupByNode(data) {
    const groups = {};
    ((data && data.documents) || []).forEach((doc) => {
      const byNode = {};
      ((doc && doc.placements) || []).forEach((p) => {
        if (!p || !p.node_id) return;
        if (!byNode[p.node_id]) byNode[p.node_id] = [];
        byNode[p.node_id].push(p);
      });
      Object.keys(byNode).forEach((nodeId) => {
        const first = byNode[nodeId][0];
        if (!groups[nodeId]) groups[nodeId] = [];
        groups[nodeId].push({
          title: (doc && doc.title) || "無題の論文",
          node_label: first.node_label || "",
          node_kind: first.node_kind || "region",
          placements: byNode[nodeId],
        });
      });
    });
    return groups;
  }

  // -------------------------------------------------------------------
  // 図形描画（L1 canvas への重ね書き。既存要素は触らない・LS7）
  // -------------------------------------------------------------------

  function svgEl(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function makeMarker(x, y, badgeText, ariaLabel) {
    const g = svgEl("g", {
      class: "landscape-marker",
      tabindex: "0",
      role: "button",
      "aria-label": ariaLabel || "この位置に置かれた論文を見る",
    });
    g.appendChild(svgEl("circle", { cx: x, cy: y, r: 7.5, class: "landscape-marker-bg" }));
    g.appendChild(svgEl("text", {
      x: x, y: y + 3.5, class: "landscape-marker-icon", "text-anchor": "middle",
    }, "📄"));
    if (badgeText) {
      g.appendChild(svgEl("text", { x: x + 9, y: y - 6, class: "landscape-marker-badge" }, badgeText));
    }
    return g;
  }

  // 配置先アンカーの座標を L1 データから解決する。concept は AtlasOverlay の
  // levels["1"].nodes、region は levels["1"].regions の箱の右上寄りに置く。
  function anchorPoint(nodeId, posById, regionById) {
    const node = posById[nodeId];
    if (node) return [node.x + CONCEPT_OFFSET[0], node.y + CONCEPT_OFFSET[1]];
    const region = regionById[nodeId];
    if (region) return [region.x + region.w - 20, region.y + 22];
    return null; // このズームでは描かれていない位置（省いて良い）
  }

  function renderMarkerLayer(canvasEl) {
    if (!canvasEl) return;
    const svg = canvasEl.querySelector("svg");
    if (!svg) return;
    const existing = svg.querySelector(".landscape-layer");
    if (existing) existing.remove();
    if (!state.enabled) return;
    // 座標は L1 のもの（AtlasOverlay.data.levels["1"]）。L2/L3 では描かない。
    if (state.lastLevel !== 1) return;

    const data = cachedData(state.courseId);
    if (!data) return;
    const overlayData = window.AtlasOverlay && window.AtlasOverlay.data;
    const levelData = overlayData && overlayData.levels && overlayData.levels["1"];
    if (!levelData) return;

    const posById = {};
    (levelData.nodes || []).forEach((n) => { posById[n.id] = n; });
    const regionById = {};
    (levelData.regions || []).forEach((r) => { regionById[r.id] = r; });

    const groups = groupByNode(data);
    const nodeIds = Object.keys(groups);
    if (!nodeIds.length) return;

    const layer = svgEl("g", { class: "landscape-layer" });
    nodeIds.forEach((nodeId) => {
      const point = anchorPoint(nodeId, posById, regionById);
      if (!point) return;
      const entries = groups[nodeId];
      const label = (entries[0] && entries[0].node_label) || nodeId;
      if (entries.length > MAX_MARKERS_PER_NODE) {
        // 5件以上は1個に集約し「+N」で件数だけ示す（重ねて潰さない）
        const g = makeMarker(point[0], point[1], "+" + entries.length,
          label + " に置かれた論文を見る");
        bindOpen(g, entries);
        layer.appendChild(g);
        return;
      }
      entries.forEach((entry, i) => {
        const g = makeMarker(point[0] + i * MARKER_STEP, point[1], "",
          entry.title + " の位置づけを見る");
        bindOpen(g, [entry]);
        layer.appendChild(g);
      });
    });
    svg.appendChild(layer);
  }

  function bindOpen(g, entries) {
    const open = (evt) => {
      evt.preventDefault();
      evt.stopPropagation();
      showPopup(entries, evt);
    };
    g.addEventListener("click", open);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") open(e); });
  }

  // -------------------------------------------------------------------
  // マーカークリックのポップオーバー（app.js の src-popup と同型の
  // 外側クリック / Esc クローズ。要素 id は landscape-popup）
  // -------------------------------------------------------------------

  function onDocMouseDownClosePopup(e) {
    if (state.popupEl && !state.popupEl.contains(e.target)) closePopup();
  }

  function onDocKeyDownClosePopup(e) {
    if (e.key === "Escape") closePopup();
  }

  function closePopup() {
    if (state.popupEl) {
      state.popupEl.remove();
      state.popupEl = null;
    }
    document.removeEventListener("mousedown", onDocMouseDownClosePopup, true);
    document.removeEventListener("keydown", onDocKeyDownClosePopup, true);
  }

  function positionPopup(popup, evt) {
    const x = (evt && typeof evt.clientX === "number") ? evt.clientX : window.innerWidth / 2;
    const y = (evt && typeof evt.clientY === "number") ? evt.clientY : window.innerHeight / 2;
    const rect = popup.getBoundingClientRect();
    const left = Math.min(Math.max(8, x + 10), Math.max(8, window.innerWidth - rect.width - 8));
    const top = Math.min(Math.max(8, y + 10), Math.max(8, window.innerHeight - rect.height - 8));
    popup.style.left = left + "px";
    popup.style.top = top + "px";
  }

  function appendPlacementRow(container, placement) {
    const row = document.createElement("div");
    row.className = "landscape-popup-row";

    // 観点・関連の段階・出所はすべてサーバが返す**ラベル**のみ（LS5: 生数値を出さない）
    const meta = document.createElement("div");
    meta.className = "landscape-popup-meta";
    const bits = [];
    if (placement.perspective_label) bits.push(placement.perspective_label);
    if (placement.weight_label) bits.push(placement.weight_label);
    meta.textContent = bits.join("・");
    row.appendChild(meta);

    if (placement.provenance_label) {
      const prov = document.createElement("span");
      prov.className = "landscape-provenance";
      prov.textContent = placement.provenance_label;
      row.appendChild(prov);
    }

    if (placement.reason) {
      const reason = document.createElement("div");
      reason.className = "landscape-popup-reason";
      reason.textContent = placement.reason;
      row.appendChild(reason);
    }

    // LS4: 原文逐語 quote を必ず辿れるようにする
    ((placement.evidence) || []).forEach((ev) => {
      if (!ev || !ev.quote) return;
      const quote = document.createElement("div");
      quote.className = "landscape-popup-quote";
      quote.textContent = "「" + ev.quote + "」";
      row.appendChild(quote);
    });

    container.appendChild(row);
  }

  function showPopup(entries, evt) {
    closePopup();
    const popup = document.createElement("div");
    popup.className = "landscape-popup";
    popup.id = "landscape-popup";
    popup.setAttribute("role", "dialog");

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "landscape-popup-close";
    closeBtn.setAttribute("aria-label", "閉じる");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", closePopup);
    popup.appendChild(closeBtn);

    const head = document.createElement("div");
    head.className = "landscape-popup-head";
    head.textContent = (entries[0] && entries[0].node_label) || "この位置の論文";
    popup.appendChild(head);

    entries.forEach((entry) => {
      const docBox = document.createElement("div");
      docBox.className = "landscape-popup-doc";
      const title = document.createElement("div");
      title.className = "landscape-popup-title";
      title.textContent = entry.title;
      docBox.appendChild(title);
      (entry.placements || []).forEach((p) => appendPlacementRow(docBox, p));
      popup.appendChild(docBox);
    });

    // LS8: どのコーパス・どの骨格版から出た表示かを常に添える
    const fact = document.createElement("div");
    fact.className = "landscape-popup-fact";
    fact.textContent = corpusFactText(cachedData(state.courseId));
    popup.appendChild(fact);

    document.body.appendChild(popup);
    positionPopup(popup, evt);
    state.popupEl = popup;
    setTimeout(() => {
      document.addEventListener("mousedown", onDocMouseDownClosePopup, true);
      document.addEventListener("keydown", onDocKeyDownClosePopup, true);
    }, 0);
  }

  // -------------------------------------------------------------------
  // トグル（オーバーレイシェル内に常設。データがある時だけ見せる）
  // -------------------------------------------------------------------

  function updateFactVisibility() {
    if (!state.factEl) return;
    if (!state.enabled) {
      state.factEl.hidden = true;
      state.factEl.textContent = "";
      return;
    }
    const data = cachedData(state.courseId);
    if (!data) { state.factEl.hidden = true; return; }
    state.factEl.textContent = corpusFactText(data);
    state.factEl.hidden = false;
  }

  function onToggleChange(checked) {
    if (!checked) {
      state.enabled = false;
      closePopup();
      updateFactVisibility();
      renderMarkerLayer(state.lastCanvas);
      return;
    }
    const courseId = state.courseId;
    if (!courseId || !token()) {
      // fail-closed: コース文脈・トークンが無ければ ON にしない
      if (state.toggleInputEl) state.toggleInputEl.checked = false;
      return;
    }
    state.enabled = true;
    loadLandscape(courseId).then((data) => {
      // コース切替後に届いた遅延応答は破棄する（別コースの地図に描画しない）
      if (courseId !== state.courseId) return;
      if (!hasPlacements(data)) {
        state.enabled = false;
        if (state.toggleInputEl) state.toggleInputEl.checked = false;
        updateFactVisibility();
        renderMarkerLayer(state.lastCanvas);
        return;
      }
      renderMarkerLayer(state.lastCanvas);
      updateFactVisibility();
    });
  }

  function buildControls(sheetEl) {
    const wrap = document.createElement("div");
    wrap.className = "landscape-controls";
    wrap.hidden = true; // 可視性は refreshContext が「配置データの有無」で判定する

    const label = document.createElement("label");
    label.className = "landscape-toggle";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.addEventListener("change", () => onToggleChange(checkbox.checked));
    label.appendChild(checkbox);
    label.appendChild(document.createTextNode("論文の位置"));
    wrap.appendChild(label);

    // LS8: コーパス事実行（トグル ON のときだけ・数値は件数と版のみ）
    const fact = document.createElement("div");
    fact.className = "landscape-fact";
    fact.hidden = true;
    wrap.appendChild(fact);

    // 個人知識ネットワークのコントロール（あれば）の後ろ、無ければフッター手前。
    const personal = sheetEl.querySelector(".personal-map-controls");
    const foot = sheetEl.querySelector("#atlas-foot");
    if (personal && personal.parentNode) {
      personal.parentNode.insertBefore(wrap, personal.nextSibling);
    } else if (foot && foot.parentNode) {
      foot.parentNode.insertBefore(wrap, foot);
    } else {
      sheetEl.appendChild(wrap);
    }

    state.controlsEl = wrap;
    state.toggleInputEl = checkbox;
    state.factEl = fact;
  }

  // 現在のコース文脈から可視性を再判定する（LS6 fail-closed）。showLevel は
  // レベル切替・再オープンのたびに呼ばれるため、buildShell が一度きりでも
  // コース切替に追随できる（personal-map.js の refreshContext と同じ流儀）。
  function refreshContext() {
    const courseId = currentCourseId();
    if (courseId !== state.courseId) {
      state.enabled = false;
      if (state.toggleInputEl) state.toggleInputEl.checked = false;
      closePopup();
    }
    state.courseId = courseId;
    if (!state.controlsEl) return;
    if (!courseId || !token()) {
      state.controlsEl.hidden = true;
      updateFactVisibility();
      return;
    }
    // 初回オープン時に遅延 fetch し、配置が1件でもあるときだけトグルを出す
    loadLandscape(courseId).then((data) => {
      if (courseId !== state.courseId) return;
      if (!state.controlsEl) return;
      state.controlsEl.hidden = !hasPlacements(data);
      updateFactVisibility();
    });
  }

  // -------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------

  function init(deps) {
    state.deps = deps || {};
  }

  function mountControls(sheetEl) {
    if (!sheetEl || state.controlsEl) return; // 二重マウント防止
    buildControls(sheetEl);
    refreshContext();
  }

  function onLevelRendered(level, canvasEl) {
    state.lastLevel = level;
    state.lastCanvas = canvasEl;
    refreshContext();
    if (level === 1) {
      renderMarkerLayer(canvasEl);
      if (state.enabled) updateFactVisibility();
    }
  }

  function onOverlayClosed() {
    closePopup();
  }

  // 出典タブ（app.js「分野の中の位置づけ」）が同一 fetch を共有するための取得口。
  // 常に Promise<data|null> を返す（失敗・データ無しは null = セクションごと非表示）。
  function getData(courseId) {
    return loadLandscape(courseId || currentCourseId());
  }

  function invalidate() {
    state.cache = {};
    state.enabled = false;
    state.courseId = "";
    if (state.toggleInputEl) state.toggleInputEl.checked = false;
    if (state.controlsEl) state.controlsEl.hidden = true;
    closePopup();
    updateFactVisibility();
    renderMarkerLayer(state.lastCanvas);
  }

  window.LandscapeLayer = {
    init,
    mountControls,
    onLevelRendered,
    onOverlayClosed,
    getData,
    invalidate,
  };
})();
