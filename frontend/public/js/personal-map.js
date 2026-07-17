/* 個人知識ネットワーク（Phase P-1 / P-2）— 「わたしの地図」UI + 旅カード
 *
 * 設計の正本: docs/features/personal_knowledge_network_design.md §9（フロント「わたしの地図」）・
 * §6（旅の経路探索）。参照する不変条項（同 §0）: PN-1（本人のみ可視）/
 * PN-2（導出のみ・保存しない）/ PN-3（本人確定のみノード化。サーバ側
 * core/personal_graph/derive.py が担保）/ PN-4（数値を見せない。件数・%・順位付けは
 * 一切描かない）/ PN-5（非LLM・決定論・自動で開かない。旅も明示操作＝「ここから
 * 旅に出る」ボタンでのみ開く）/ PN-6（同一性リンクは confirmed のみ辿る。サーバ側
 * journey.py が担保）/ PN-7（fail-closed。コース文脈・トークン・骨格が無ければ
 * 機能ごと隠す）。
 *
 * atlas-overlay.js の L1 描画の上に、本人の痕跡（tension/question/reconstruction、
 * および bridge 辺を張った tension の variant）を kind 別の小さな図形で重ねるだけの薄い層。
 * 既存の描画（状態ドット・いまここ・足あと・霧）は一切変更しない。atlas-minimap.js には触れない。
 *
 * 旅カード（Phase P-2, §6/§9）: マーカー小ポップ・「まだ地図にない」トレイの各項目に
 * 「ここから旅に出る」ボタンを追加し、journey API の結果を常に最新1枚のカードとして
 * オーバーレイ内（トレイの近く）に表示する。fetch はボタン押下時のみ・キャッシュしない。
 * 関連関数（fetchJourney / requestJourney / renderJourneyCard / closeJourneyCard 等）は
 * すべて内部実装であり、公開契約（下記6メソッド）には含めない。
 *
 * コース横断の橋（Phase P-2「コース横断の橋」/ 提案書 §4.1・§5）: 旅カードの応答に
 * `cross_course_hint`（コース内 journey API が付与）が含まれる場合、本人が選んだときだけ
 * `/api/me/personal-network/journey` を叩いてコース横断版の旅へ差し替える（自動では
 * 開かない。提案書「本人が選んだ場合のみ」）。
 *
 * 本人による訂正（提案書 §6）: マーカー小ポップに「地図には反映しない」
 * （tension/question のみ・POST /api/learning/traces/{id}/map-exclude）、問いの軌跡の
 * 除外済み項目（`data-map-excluded="1"`。付与元は app.js）に「地図に戻す」
 * （POST .../map-restore）を追加する。どちらも痕跡行自体は削除せず（P4）、地図の導出から
 * 外れる/戻るだけ。
 *
 * 公開契約 window.PersonalMap（呼び出し側は app.js。名前・引数は固定）:
 *   init(deps)                          — deps.openTrajectory(traceId) を登録
 *   mountControls(shellEl)              — atlas-overlay buildShell 完了時に一度だけ呼ばれる
 *   onLevelRendered(level, canvasEl)    — atlas-overlay showLevel の描画後に毎回呼ばれる
 *   onOverlayClosed()                   — atlas-overlay closeOverlay 時（旅カードも破棄する）
 *   annotateTrajectoryList(containerEl) — 問いの軌跡一覧に「地図で見る」を後付け
 *   invalidate()                        — コース切替時にキャッシュ・トグル状態を破棄（旅カードも破棄する）
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
    journeyCardEl: null, // Phase P-2: 旅カード（常に最新1枚）
    journeySeq: 0, // 旅カード fetch の連番 (closeJourneyCard で進める。後着の古い応答の破棄に使う)
    noteEl: null, // Phase P-3: 「地図には反映しません」等の一時メッセージ
    noteTimer: null,
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

      const actions = document.createElement("div");
      actions.className = "personal-map-journey-popup-actions";

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
        actions.appendChild(link);
      }
      // Phase P-2: ノードから旅に出る（明示操作のみ・PN-5）
      actions.appendChild(buildJourneyButton(node.id));
      // Phase P-3: 「地図には反映しない」（対象は tension/question のみ。
      // reconstruction ノードには出さない — 提案書 §6）
      if (node.node_kind === "tension" || node.node_kind === "question") {
        actions.appendChild(buildMapExcludeButton(node.id));
      }
      item.appendChild(actions);
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
    // Phase P-2: トグル OFF（コース切替による強制 OFF を含む）では旅カードも破棄する
    if (!state.enabled) closeJourneyCard();
    // Phase P-3: トグル OFF では一時メッセージも消す
    if (!state.enabled && state.noteEl) {
      if (state.noteTimer) clearTimeout(state.noteTimer);
      state.noteEl.hidden = true;
      state.noteEl.innerHTML = "";
    }
  }

  function renderTray(data) {
    const tray = state.trayEl;
    if (!tray) return;
    tray.innerHTML = "";
    if (!state.enabled || !data) { tray.hidden = true; return; } // トグル OFF 時は領域ごと非表示のまま
    const entries = unanchoredEntries(data);

    const heading = document.createElement("div");
    heading.className = "personal-map-tray-heading";
    heading.textContent = "まだ地図にない";
    tray.appendChild(heading);

    if (!entries.length) {
      // 0件でも見出しは残す（異常演出にしない・数値や進捗は出さない）
      const empty = document.createElement("div");
      empty.className = "personal-map-tray-empty";
      empty.textContent = "まだ記録がありません。学習の中で問いを残すと、ここに現れます。";
      tray.appendChild(empty);
      tray.hidden = false;
      return;
    }

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

      // Phase P-2: 「まだ地図にない」ノードからも旅に出られる（アンカーが無くても
      // 旅の起点にはなれる — journey.py 側で topic 粒度等へ縮退する）
      const actionsRow = document.createElement("div");
      actionsRow.className = "personal-map-journey-tray-actions";
      actionsRow.appendChild(buildJourneyButton(node.id));
      list.appendChild(actionsRow);
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
      // コース切替後に届いた遅延応答は破棄する（別コースの UI に描画しない）。
      // invalidate() はキャッシュ・トグル状態は消すが進行中の Promise 自体は
      // キャンセルできないため、応答到着時に要求開始時の courseId と現在の
      // courseId を突き合わせる（admin-lecture-studio.js のコース切替と同じ流儀）。
      if (courseId !== state.courseId) return;
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
    // N17: 最上位パネル（personal-map-home.js）の「わたしの地図」とラベルが重複していた
    // ため、オーバーレイ内トグルは「自分の記録を重ねる」に改名（機能は不変 — atlas の
    // 公共骨格の上に本人の痕跡ドットを重畳表示するトグル）。
    label.appendChild(document.createTextNode("自分の記録を重ねる"));
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

    // Phase P-3: 一時メッセージ（「地図には反映しません」等。数秒で自動的に隠れる）
    const note = document.createElement("div");
    note.className = "personal-map-transient-note";
    note.hidden = true;
    wrap.appendChild(note);

    // Phase P-2: 旅カード（常に最新1枚・トレイの近くに常設。§6/§9）
    const journeyCard = document.createElement("div");
    journeyCard.className = "personal-map-journey-card";
    journeyCard.hidden = true;
    wrap.appendChild(journeyCard);

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
    state.noteEl = note;
    state.journeyCardEl = journeyCard;
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

    // Phase P-3: 「地図に戻す」チップ（`data-map-excluded="1"` のみ。付与元は app.js の
    // 一覧描画）。除外済みトレースは地図ノードとして返らないため network fetch には
    // 依存しない（§6。行削除ではなく状態遷移で保持されているだけ）。
    items.forEach((itemEl) => {
      if (itemEl.getAttribute("data-map-excluded") !== "1") return;
      if (itemEl.querySelector(".personal-map-restore-chip")) return; // 二重付与防止
      const excludedTraceId = itemEl.getAttribute("data-trace-id");
      if (!excludedTraceId) return;
      itemEl.appendChild(buildRestoreChip(excludedTraceId, itemEl));
    });

    loadNetwork(courseId).then((data) => {
      // コース切替後に届いた遅延応答は破棄する（別コースの軌跡一覧に誤って
      // 「地図で見る」導線を付けない。onToggleChange と同じ courseId 突き合わせ）。
      if (courseId !== currentCourseId()) return;
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
  // 旅の経路探索（Journey, Phase P-2, §6/§9）
  //
  // 明示操作（「ここから旅に出る」ボタン）でのみ journey API を fetch する。
  // キャッシュせず、ポーリングもしない（PN-5）。旅カードは常に最新1枚（atlas cues
  // の流儀 F-2 と同じ）で、新しい旅を開くと前のカードを即座に置き換える。
  // 404・失敗は静かに何も出さない（fail-closed。カードを空のまま隠す）。
  // -------------------------------------------------------------------

  // 404（対象なし）は従来どおり fail-closed で null に丸めるが、それ以外の非 ok /
  // 通信例外は `_fetch_error` で区別する（renderJourneyCard が「空」と「取得できな
  // かった」を分けて表示するため。G3）。
  function fetchJourney(courseId, nodeId) {
    const t = token();
    if (!t) return Promise.resolve(null);
    return fetch(
      API_BASE +
        "/learning/courses/" +
        encodeURIComponent(courseId) +
        "/personal-network/journey?node_id=" +
        encodeURIComponent(nodeId),
      { headers: { Authorization: "Bearer " + t } }
    )
      .then((res) => {
        if (res.status === 404) return null;
        if (!res.ok) return { _fetch_error: true };
        return res.json();
      })
      .catch(() => ({ _fetch_error: true }));
  }

  // コース横断版の旅（Phase P-2「コース横断の橋」）。本人が旅カードの静かな一行
  // 「以前の学習につながる道を見る」を選んだときだけ呼ぶ（自動では開かない）。
  // 正本API `/api/me/personal-network/journey` を使う（コース所有ではなく本人スコープ）。
  function fetchCrossCourseJourney(nodeId) {
    const t = token();
    if (!t) return Promise.resolve(null);
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

  // ref.kind === "atlas_node" のときだけリンク化する（§9）。既存の
  // AtlasOverlay.showLevel / selectNode をそのまま使い、フォーカス演出を独自実装しない。
  function focusAtlasNodeFromJourney(atlasNodeId) {
    if (!atlasNodeId || !window.AtlasOverlay) return;
    window.AtlasOverlay.showLevel(1);
    window.AtlasOverlay.selectNode(atlasNodeId);
  }

  function closeJourneyCard() {
    // 進行中の旅 fetch を無効化する（応答到着時にこの連番との一致を確認し、
    // 閉じた後／別の旅要求に上書きされた後に届く古い応答を破棄する。到着順の逆転対策）。
    state.journeySeq += 1;
    if (state.journeyCardEl) {
      state.journeyCardEl.hidden = true;
      state.journeyCardEl.innerHTML = "";
    }
  }

  function renderJourneyCard(data) {
    const card = state.journeyCardEl;
    if (!card) return;
    card.innerHTML = "";
    if (data && data._fetch_error) {
      // G3: 404/空（fail-closed）とは区別し、通信エラーで読み込めなかった事実だけを出す。
      const errClose = document.createElement("button");
      errClose.type = "button";
      errClose.className = "personal-map-journey-close";
      errClose.setAttribute("aria-label", "閉じる");
      errClose.textContent = "×";
      errClose.addEventListener("click", closeJourneyCard);
      card.appendChild(errClose);

      const errHeading = document.createElement("div");
      errHeading.className = "personal-map-journey-heading";
      errHeading.textContent = "旅の経路";
      card.appendChild(errHeading);

      const errText = document.createElement("div");
      errText.className = "personal-map-journey-fact";
      errText.textContent = "旅の経路を読み込めませんでした。";
      card.appendChild(errText);

      card.hidden = false;
      return;
    }
    if (!data || !Array.isArray(data.steps) || !data.steps.length) {
      // steps が空/対象なし（404）なら何も出さない（fail-closed。エラーバナーは出さない）
      card.hidden = true;
      return;
    }

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "personal-map-journey-close";
    closeBtn.setAttribute("aria-label", "閉じる");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", closeJourneyCard);
    card.appendChild(closeBtn);

    const heading = document.createElement("div");
    heading.className = "personal-map-journey-heading";
    heading.textContent = "旅の経路";
    card.appendChild(heading);

    // 番号なしリスト（PN-4: step 数を匂わせない）
    const list = document.createElement("div");
    list.className = "personal-map-journey-list";
    data.steps.forEach((step) => {
      const item = document.createElement("div");
      item.className = "personal-map-journey-item";

      const factText = document.createElement("span");
      factText.className = "personal-map-journey-fact";
      factText.textContent = step.fact || "";
      item.appendChild(factText);

      const ref = step.ref || null;
      if (ref && ref.kind === "atlas_node" && ref.id) {
        // atlas_node のみリンク化する（§9）
        const link = document.createElement("button");
        link.type = "button";
        link.className = "personal-map-journey-ref-link";
        link.textContent = ref.label || factText.textContent;
        link.addEventListener("click", () => focusAtlasNodeFromJourney(ref.id));
        item.appendChild(link);
      } else if (ref && ref.label) {
        // atlas_node 以外はテキスト併記のみ（リンク化しない）
        const label = document.createElement("span");
        label.className = "personal-map-journey-ref-label";
        label.textContent = ref.label;
        item.appendChild(label);
      }

      list.appendChild(item);
    });
    card.appendChild(list);

    if (data.frontier_note) {
      // 経路が途切れた事実をそのまま出す（警告色にしない。D層「空欄は発見」と同族・§6）
      const frontier = document.createElement("div");
      frontier.className = "personal-map-journey-frontier";
      frontier.textContent = data.frontier_note;
      card.appendChild(frontier);

      // G7-J: 行き止まりで終わらせず、別の問いから旅をやり直せる導線を出す（事実文のみ）。
      const restartBtn = document.createElement("button");
      restartBtn.type = "button";
      restartBtn.className = "personal-map-journey-btn";
      restartBtn.textContent = "別の問いから旅に出る";
      restartBtn.addEventListener("click", () => {
        closeJourneyCard();
        if (state.trayEl && !state.trayEl.hidden && typeof state.trayEl.scrollIntoView === "function") {
          state.trayEl.scrollIntoView({ block: "nearest" });
        }
      });
      card.appendChild(restartBtn);
    }

    if (data.truncated) {
      const truncated = document.createElement("div");
      truncated.className = "personal-map-journey-truncated";
      truncated.textContent = "（途中まで）";
      card.appendChild(truncated);
    }

    // Phase P-2「コース横断の橋」: 静かな一行 + 本人が選んだときだけコース横断版へ
    // 差し替える（提案書 §4.1・§5.2「本人が選んだ場合のみ」。自動では開かない）。
    if (data.cross_course_hint && data.cross_course_hint.fact) {
      const hint = document.createElement("div");
      hint.className = "personal-map-journey-cross-hint";
      const hintText = document.createElement("span");
      hintText.textContent = data.cross_course_hint.fact;
      hint.appendChild(hintText);
      const hintBtn = document.createElement("button");
      hintBtn.type = "button";
      hintBtn.className = "personal-map-journey-cross-btn";
      hintBtn.textContent = "以前の学習につながる道を見る";
      hintBtn.addEventListener("click", () => {
        const crossNodeId = data.cross_course_hint && data.cross_course_hint.node_id;
        if (!crossNodeId) return;
        // requestJourney と同じ courseId 突き合わせ + 連番ガード（コース切替・連打対策）。
        const courseId = state.courseId;
        state.journeySeq += 1;
        const seq = state.journeySeq;
        fetchCrossCourseJourney(crossNodeId).then((crossData) => {
          if (courseId !== state.courseId) return;
          if (seq !== state.journeySeq) return;
          renderJourneyCard(crossData);
        });
      });
      hint.appendChild(hintBtn);
      card.appendChild(hint);
    }

    card.hidden = false;
    if (typeof card.scrollIntoView === "function") card.scrollIntoView({ block: "nearest" });
  }

  function requestJourney(nodeId) {
    const courseId = state.courseId;
    if (!courseId || !nodeId) return;
    closePopup();
    // 旅カードは常に最新1枚 — 新しい旅を開くと前のカードは即座に置き換わる
    closeJourneyCard();
    const seq = state.journeySeq;
    fetchJourney(courseId, nodeId).then((data) => {
      // コース切替後に届いた遅延応答は破棄する（別コースの旅カードに描画しない。
      // onToggleChange と同じ courseId 突き合わせ）。
      if (courseId !== state.courseId) return;
      // 連打で新しい旅要求が発行済みなら、後着の古い応答は破棄する（到着順の逆転対策）。
      if (seq !== state.journeySeq) return;
      renderJourneyCard(data);
    });
  }

  function buildJourneyButton(nodeId) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "personal-map-journey-btn";
    btn.textContent = "ここから旅に出る";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      requestJourney(nodeId);
    });
    return btn;
  }

  // -------------------------------------------------------------------
  // 本人による訂正: 「地図には反映しない」／「地図に戻す」（Phase P-3, 提案書 §6）
  //
  // 痕跡行そのものは削除しない（P4）。地図の導出（core/personal_graph/derive.py）から
  // 外れる/戻るだけの状態遷移で、tension/anchor の dismiss（候補の当落判定）とは独立
  // — status には触れない。
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

  // 除外/復帰の成否に関わらずキャッシュを破棄し、ON中なら現在レベルを再描画する
  // （state.lastCanvas/lastLevel を使う。onToggleChange と同じ再取得パターン）。
  function refreshAfterMapExclusionChange() {
    const courseId = state.courseId;
    delete state.networkCache[courseId];
    if (!state.enabled) return;
    loadNetwork(courseId).then((data) => {
      // コース切替後に届いた遅延応答は破棄する（別コースの UI に描画しない。
      // onToggleChange と同じ courseId 突き合わせ）。
      if (courseId !== state.courseId) return;
      if (!data) return;
      renderDotsLayer(state.lastCanvas);
      renderTray(data);
    });
  }

  function requestMapExclude(traceId) {
    const t = token();
    if (!t || !traceId) return;
    closePopup();
    fetch(API_BASE + "/learning/traces/" + encodeURIComponent(traceId) + "/map-exclude", {
      method: "POST",
      headers: { Authorization: "Bearer " + t },
    })
      .then((res) => {
        if (!res.ok) throw new Error("map-exclude " + res.status);
        return res.json();
      })
      .then(() => {
        refreshAfterMapExclusionChange();
        showTransientNote("地図には反映しません（問いの軌跡には残ります）");
      })
      .catch(() => {}); // fail-closed: 失敗時は何も出さない（エラーバナーは出さない）
  }

  function requestMapRestore(traceId, itemEl) {
    const t = token();
    if (!t || !traceId) return;
    fetch(API_BASE + "/learning/traces/" + encodeURIComponent(traceId) + "/map-restore", {
      method: "POST",
      headers: { Authorization: "Bearer " + t },
    })
      .then((res) => {
        if (!res.ok) throw new Error("map-restore " + res.status);
        return res.json();
      })
      .then(() => {
        if (itemEl) {
          itemEl.setAttribute("data-map-excluded", "0");
          const chip = itemEl.querySelector(".personal-map-restore-chip");
          if (chip) chip.remove();
        }
        refreshAfterMapExclusionChange();
        showTransientNote("地図に戻しました");
      })
      .catch(() => {}); // fail-closed: 失敗時は何も出さない
  }

  function buildMapExcludeButton(traceId) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "personal-map-exclude-btn";
    btn.textContent = "地図には反映しない";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      requestMapExclude(traceId);
    });
    return btn;
  }

  function buildRestoreChip(traceId, itemEl) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "personal-map-restore-chip";
    chip.textContent = "地図に戻す";
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      requestMapRestore(traceId, itemEl);
    });
    return chip;
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
    // Phase P-2: オーバーレイを閉じたら旅カードも破棄する（再オープン時に古い旅を
    // 引き継がない。トグル状態が ON のままでも旅カードだけは毎回明示操作からやり直す）。
    closeJourneyCard();
  }

  function invalidate() {
    state.networkCache = {};
    state.enabled = false;
    state.courseId = "";
    if (state.toggleInputEl) state.toggleInputEl.checked = false;
    closePopup();
    closeJourneyCard();
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
