/* 分野の地図 — 「見晴らしの瞬間」の導線 (Issue F-2)
 *
 * 地図は中央に常設しない。学習の節目 (トピック完了・章末・寄り道復帰) に
 * カード「地図で現在地を見る」をそっと提示し、開くのは常に本人。
 * 例外は初回ログイン時の一度きりの自動表示のみ (フラグはサーバに永続化し、
 * 再ログイン・別端末でも繰り返さない)。
 *
 * 未決事項の決定 (issue F):
 * - 抑制ルール: 直近10分以内にオーバーレイを開いていたら、トピック完了・章末の
 *   カードは出さない。寄り道復帰 (導線3) は明示アクション直後なので抑制しない。
 *   提示カードは常に最新1枚のみ。
 * - 初回自動表示のオプトアウト (§16-6): 設定項目は作らない。自動表示は構造上
 *   一度きりで、オーバーレイ内の注記 (atlas-overlay.js の intro note) が
 *   「初回のみ」であることを明示する。
 *
 * 内部計測 (数値をユーザーに見せない): 導線別の shown / opened / dwell /
 * learn_reached を POST /api/learning/atlas/cues/events に記録する。
 * Stage 2 ゲート判断の材料であり、表示には使わない (§1.2-4)。
 */
(() => {
  "use strict";

  const SUPPRESS_MS = 10 * 60 * 1000; // 抑制ルール: 直近10分

  const state = {
    lastOverlayOpenAt: 0,
    openCue: "",   // いま開いているオーバーレイの導線名 (learn_reached の帰属用)
    card: null,    // 提示中の導線カード (常に最新1枚のみ)
  };

  function authHeaders() {
    const headers = { "Content-Type": "application/json" };
    try {
      const token = localStorage.getItem("eg_token");
      if (token) headers["Authorization"] = "Bearer " + token;
    } catch (e) { /* localStorage 不可の環境ではそのまま */ }
    return headers;
  }

  // 内部計測は best-effort。失敗しても学習フローには一切影響させない
  async function postEvent(cue, event, payload) {
    try {
      await fetch("/api/learning/atlas/cues/events", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ cue: cue, event: event, payload: payload || {} }),
      });
    } catch (e) { /* noop */ }
  }

  function skeletonMissing() {
    return !!(window.AtlasMinimap && window.AtlasMinimap.available === false);
  }

  // 完了トピック名 → 地図ノードの決定論的な突き合わせ (ラベル一致 → 包含一致)。
  // 見つからなければ null を返し、オーバーレイは「いまここ」中心で開く。
  function resolveFocusNode(data, label) {
    if (!data || !data.nodes || !label) return null;
    const entries = Object.entries(data.nodes);
    let hit = entries.find(([, n]) => n.label === label);
    if (!hit) {
      hit = entries.find(([, n]) =>
        n.status !== "fog" && n.label &&
        (label.indexOf(n.label) >= 0 || n.label.indexOf(label) >= 0));
    }
    return hit ? hit[0] : null;
  }

  function removeCard() {
    if (state.card) {
      state.card.remove();
      state.card = null;
    }
  }

  async function openFromCue(kind, opts) {
    const data = window.AtlasData ? await window.AtlasData.load() : undefined;
    if (window.AtlasData && !data) { removeCard(); return; } // 骨格なし
    const focus = resolveFocusNode(data, opts.focusLabel || "");
    removeCard();
    if (window.AtlasOverlay) {
      window.AtlasOverlay.open(data, {
        level: 1,
        focus: focus || undefined,
        highlight: !!opts.highlight,
        source: kind,
      });
    }
  }

  // 導線カードの提示。kind: topic_complete | chapter_end | detour_return
  // opts: { container, text, focusLabel, highlight, force }
  // 提示に留める — 自動では開かない。学習フローの操作は一切ブロックしない。
  function showCue(kind, opts) {
    opts = opts || {};
    if (skeletonMissing()) return;           // 骨格なしカートリッジでは導線を出さない
    if (!opts.container) return;
    // 抑制: 直近でオーバーレイを開いた直後は出さない (寄り道復帰は対象外)
    if (kind !== "detour_return" &&
        state.lastOverlayOpenAt &&
        Date.now() - state.lastOverlayOpenAt < SUPPRESS_MS) {
      return;
    }
    removeCard(); // 常に最新1枚のみ

    const card = document.createElement("div");
    card.className = "atlas-cue-card";
    card.setAttribute("data-cue", kind);

    const text = document.createElement("span");
    text.className = "atlas-cue-text";
    text.textContent = opts.text || "";
    card.appendChild(text);

    const open = document.createElement("button");
    open.type = "button";
    open.className = "atlas-cue-open";
    open.textContent = "地図で現在地を見る";
    open.addEventListener("click", () => { openFromCue(kind, opts); });
    card.appendChild(open);

    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "atlas-cue-dismiss";
    dismiss.setAttribute("aria-label", "この案内を閉じる");
    dismiss.textContent = "✕";
    dismiss.addEventListener("click", removeCard);
    card.appendChild(dismiss);

    opts.container.appendChild(card);
    if (typeof opts.container.scrollTop === "number") {
      opts.container.scrollTop = opts.container.scrollHeight;
    }
    state.card = card;
    postEvent(kind, "shown", { focus_label: opts.focusLabel || "" });
  }

  // 導線4: 初回ログイン時の一度きりの自動表示 (L1 俯瞰位置)。
  // フラグはサーバ側 (atlas_cue_events の first_login/opened 行) に永続化する。
  // フラグ確認に失敗したときは自動表示しない (押し付けない側に倒す)。
  async function maybeAutoOpenFirstLogin() {
    try {
      if (skeletonMissing()) return;
      const res = await fetch("/api/learning/atlas/cues/state", { headers: authHeaders() });
      if (!res.ok) return;
      const st = await res.json();
      if (st.first_login_seen) return;
      const data = window.AtlasData ? await window.AtlasData.load() : undefined;
      if (!data) return; // 骨格なし → 自動表示もしない
      // 開く前にフラグを永続化する (以後どの端末でも自動表示しない)
      await postEvent("first_login", "opened", {});
      if (window.AtlasOverlay) {
        window.AtlasOverlay.open(data, { level: 1, intro: true, source: "first_login" });
      }
    } catch (e) { /* 初回導線は best-effort */ }
  }

  // ── 内部計測の購読 ──────────────────────────────────────────────
  document.addEventListener("atlas:opened", (e) => {
    state.lastOverlayOpenAt = Date.now();
    const source = (e.detail && e.detail.source) || "";
    state.openCue = source;
    removeCard(); // 地図を開いたら提示中のカードは役目を終える
    // first_login の opened はフラグ永続化のため maybeAutoOpenFirstLogin が先に記録済み
    if (source && source !== "first_login") postEvent(source, "opened", {});
  });

  document.addEventListener("atlas:closed", (e) => {
    const d = e.detail || {};
    if (d.source) postEvent(d.source, "dwell", { duration_ms: d.duration_ms || 0 });
    state.openCue = "";
  });

  document.addEventListener("atlas:learnaction", (e) => {
    if (state.openCue) {
      postEvent(state.openCue, "learn_reached", {
        node_id: (e.detail && e.detail.node_id) || "",
      });
    }
  });

  window.AtlasCues = { showCue, maybeAutoOpenFirstLogin };
})();
