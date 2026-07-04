/* 分野の地図 — 詳細パネルとチャット遷移 (Issue C: C-1/C-2)
 *
 * atlas-overlay.js (Issue B) が発火する "atlas:nodeselect" イベントを購読し、
 * 詳細パネル (#atlas-panel-body) に検証行・承認行・アクション行を差し込む。
 *
 * 守るべき設計原則 (仕様書 §1.2 / issue C):
 * - 検証行・承認行は台帳由来の事実のみ。評価語(重要・要注意 等)を使わない
 * - 霧領域では投入の指示・催促をしない(事実の定型文はフィクスチャ側が持つ)
 * - ↗ アクションは「オーバーレイを閉じ、対話へプロンプト送出」。プロンプトには
 *   構造化ペイロード {node_id, level, skeleton_version, action} を必ず添付する
 *   (自由文のみに依存しない)
 *
 * 接続点:
 * - C-2: window.sendPrompt(text, payload) — app.js が payload.atlas_context を
 *   チャット API へ引き渡す
 * - D-1: [修正を報告] は CustomEvent "atlas:reportrequest" を発火するのみ
 *   (フォームの中身と送信は issue D)
 */
(() => {
  "use strict";

  // ボタン表示制御に使うアクション定義 (§6.2)。
  // learn / evid はノードの learn / evid フラグで表示制御 (霧・行間では非表示)。
  // mind / report は常時表示。
  const ACTIONS = [
    { key: "learn", label: "ここから学ぶ ↗", always: false },
    { key: "evid", label: "根拠を見る ↗", always: false },
    { key: "mind", label: "気になる ↗", always: true },
    { key: "report", label: "修正を報告", always: true },
  ];

  const state = {
    current: null, // { node_id, level, skeleton_version }
  };

  function nodeInfo(id) {
    const data = window.AtlasOverlay && window.AtlasOverlay.data;
    return (data && data.nodes && data.nodes[id]) || { label: id };
  }

  // -------------------------------------------------------------------
  // C-1: 詳細パネル描画
  // -------------------------------------------------------------------

  function renderPanel(detail) {
    const body = document.getElementById("atlas-panel-body");
    if (!body) return;
    state.current = detail;
    const info = nodeInfo(detail.node_id);

    body.replaceChildren();

    // 検証行・承認行: 台帳由来の事実のみ (評価語なし)。データはフィクスチャ →
    // 実データ (E系) 移行後も同じフィールドで供給される。
    const verify = document.createElement("p");
    verify.className = "atlas-panel-verify";
    verify.textContent = info.verify || "台帳の記帳情報がありません。";
    body.appendChild(verify);

    const endorse = document.createElement("p");
    endorse.className = "atlas-panel-endorse";
    endorse.textContent = info.endorse || "—";
    body.appendChild(endorse);

    // アクション行 (§6.2 の表示規則)
    const row = document.createElement("div");
    row.className = "atlas-panel-actions";
    ACTIONS.forEach((act) => {
      if (!act.always && !info[act.key]) return; // learn/evid: 霧・行間では非表示
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "atlas-act-btn";
      btn.setAttribute("data-act", act.key);
      btn.textContent = act.label;
      btn.addEventListener("click", () => runAction(act.key));
      row.appendChild(btn);
    });
    body.appendChild(row);
  }

  // -------------------------------------------------------------------
  // C-2: チャット遷移 (↗ アクション)
  // -------------------------------------------------------------------

  // 学習パス生成 (C-3) の入力素材: 現レベルの地図データから、対象ノードに
  // 繋がるノード列 (概念グラフ依存の投影) と並置候補を抽出する。
  // ここは抽出のみで順序・意味付けの判断はサーバ側 (core/atlas_path.py) が行う。
  function contextForNode(nodeId, level) {
    const data = window.AtlasOverlay && window.AtlasOverlay.data;
    if (!data) return { related: [], juxtapose: [] };
    const levelData = (data.levels || {})[String(level)] || {};
    const pick = (id) => {
      const info = nodeInfo(id);
      return {
        node_id: id,
        label: info.label || id,
        status: info.status || "",
        pill: info.pill || "",
      };
    };

    let orderedIds = [];
    if (level === 3) {
      orderedIds = (levelData.chain || []).slice();
    } else if (level === 2) {
      // エッジを無向 BFS して対象からの近さ順に並べる (概念マップの依存投影)
      const adj = {};
      (levelData.edges || []).forEach(([a, b]) => {
        (adj[a] = adj[a] || []).push(b);
        (adj[b] = adj[b] || []).push(a);
      });
      const seen = { [nodeId]: true };
      const queue = [nodeId];
      while (queue.length) {
        const cur = queue.shift();
        orderedIds.push(cur);
        (adj[cur] || []).forEach((nx) => {
          if (!seen[nx]) { seen[nx] = true; queue.push(nx); }
        });
      }
    } else {
      // L1: 同じ領域の代表概念 + リーダー線で繋がるノード
      const nodes = levelData.nodes || [];
      const self = nodes.find((n) => n.id === nodeId);
      const region = self && self.region;
      orderedIds = nodes.filter((n) => !region || n.region === region).map((n) => n.id);
      (levelData.leaders || []).forEach(([a, b]) => {
        if (a === nodeId && orderedIds.indexOf(b) < 0) orderedIds.push(b);
        if (b === nodeId && orderedIds.indexOf(a) < 0) orderedIds.push(a);
      });
    }
    const related = orderedIds.filter((id) => id !== nodeId).map(pick);

    // 並置候補: リーダー線 (L1) で対象と繋がる相手を優先し、
    // 無ければ related の中の contested ノード。接続の言語化はしない (§8)。
    const juxtapose = [];
    const l1 = (data.levels || {})["1"] || {};
    (l1.leaders || []).forEach(([a, b]) => {
      if (a === nodeId) juxtapose.push(pick(b));
      if (b === nodeId) juxtapose.push(pick(a));
    });
    if (juxtapose.length === 0) {
      const contested = related.find((r) => r.status === "contested");
      if (contested) juxtapose.push(contested);
    }
    return { related, juxtapose };
  }

  // ↗ の共通機構: オーバーレイを閉じ、対話パネルへプロンプトを送出する。
  // 構造化ペイロード {node_id, level, skeleton_version, action} を必ず添付する。
  function sendToChat(text, atlasContext) {
    if (typeof window.sendPrompt !== "function") {
      console.warn("[atlas] 対話パネルが初期化されていません (sendPrompt なし)");
      return;
    }
    if (window.AtlasOverlay) window.AtlasOverlay.close();
    window.sendPrompt(text, { atlas_context: atlasContext });
  }

  function runAction(actionKey) {
    const cur = state.current;
    if (!cur) return;
    const info = nodeInfo(cur.node_id);
    const label = info.label || cur.node_id;
    const base = {
      node_id: cur.node_id,
      level: cur.level,
      skeleton_version: cur.skeleton_version,
      action: actionKey,
      node_label: label,
      node_status: info.status || "",
      node_pill: info.pill || "",
    };

    if (actionKey === "learn") {
      // C-3: 学習パス提案カードの入力素材 (related / juxtapose) を添付する
      const ctx = contextForNode(cur.node_id, cur.level);
      sendToChat(
        "「" + label + "」から学習パスを組んで、いまの文脈に繋げて",
        Object.assign({}, base, { related: ctx.related, juxtapose: ctx.juxtapose })
      );
    } else if (actionKey === "evid") {
      sendToChat("「" + label + "」の根拠（原文の該当箇所・式・承認の履歴）を見せて", base);
    } else if (actionKey === "mind") {
      // 既存 tension 記録経路 (帰属つき) へ — サーバ側で kind='tension' として記録
      sendToChat("「" + label + "」について、うまく言えないが引っかかる。違和感として記録して", base);
    } else if (actionKey === "report") {
      // 修正報告のフォームと送信は issue D。ここでは接続点イベントの発火のみ。
      document.dispatchEvent(new CustomEvent("atlas:reportrequest", { detail: base }));
    }
  }

  document.addEventListener("atlas:nodeselect", (e) => renderPanel(e.detail));

  // テスト用の公開 (挙動は変えない)
  window.AtlasPanel = { contextForNode };
})();
