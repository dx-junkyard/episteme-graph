/* 分野の地図 — データソース切替 (Issue E-3)
 *
 * オーバーレイ (atlas-overlay.js) はフィクスチャと同形の JSON で駆動される。
 * このモジュールは「フィクスチャ (issue B/C) → Atlas API (issue E)」の
 * 差し替えを設定切替だけで行うための取得口。
 *
 * 切替方法 (優先順):
 *   1. window.ATLAS_DATA_SOURCE = "api" | "fixture"
 *   2. localStorage.setItem("atlas_data_source", "api")
 *   3. 既定: "fixture"
 *
 * 状態判定ロジックはサーバ側 (core/atlas_state.py)。ここでは判定を複製せず、
 * 取得と互換フォールバック (API 不通時はフィクスチャ) のみを行う。
 */
(function () {
  "use strict";

  const STORAGE_KEY = "atlas_data_source";
  const DEFAULT_CARTRIDGE = "particle_physics";

  function source() {
    if (window.ATLAS_DATA_SOURCE) return window.ATLAS_DATA_SOURCE;
    try {
      return localStorage.getItem(STORAGE_KEY) || "fixture";
    } catch (e) {
      return "fixture";
    }
  }

  async function fetchFromApi(cartridgeId, opts) {
    const headers = {};
    try {
      const token = localStorage.getItem("eg_token");
      if (token) headers["Authorization"] = "Bearer " + token;
    } catch (e) { /* localStorage 不可の環境ではそのまま */ }
    let url = "/api/atlas?cartridge=" + encodeURIComponent(cartridgeId || DEFAULT_CARTRIDGE);
    if (opts && opts.focus) url += "&focus=" + encodeURIComponent(opts.focus);
    const res = await fetch(url, { headers: headers });
    if (res.status === 404) return null; // 骨格なし → 地図機能を出さない (issue F 受け入れ条件6)
    if (!res.ok) throw new Error("atlas api " + res.status);
    return res.json();
  }

  // opts.focus: 初期選択したいノード id (導線カード用。API の focus パラメータへ渡す)
  async function load(cartridgeId, opts) {
    if (source() !== "api") return window.ATLAS_FIXTURE;
    try {
      return await fetchFromApi(cartridgeId, opts);
    } catch (err) {
      console.warn("[atlas] API 取得に失敗。フィクスチャで表示します:", err);
      return window.ATLAS_FIXTURE;
    }
  }

  window.AtlasData = { load: load, source: source };
})();
