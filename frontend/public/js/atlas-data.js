/* 分野の地図 — データソース切替 (Issue E-3 / 個人層 binding の整備)
 *
 * オーバーレイ (atlas-overlay.js) はフィクスチャと同形の JSON で駆動される。
 * このモジュールは「フィクスチャ (issue B/C) → Atlas API (issue E)」の
 * 差し替えを設定切替だけで行うための取得口。
 *
 * 切替方法 (優先順):
 *   1. window.ATLAS_DATA_SOURCE = "api" | "fixture"
 *   2. localStorage.setItem("atlas_data_source", "api")
 *   3. サーバ設定 GET /api/atlas/runtime-config (既定 "api"。gap4)
 *   4. 取得不能時のフォールバック: "api" (本番でモック地図を全ユーザーに出さない側に倒す)
 *
 * gap3: 現在のコース文脈 (window.AtlasContext) があれば course/topic で取得し、
 * サーバがコースからカートリッジと focus を解決する。無ければ従来の cartridge 指定。
 *
 * 状態判定ロジックはサーバ側 (core/atlas_state.py)。ここでは判定を複製せず、
 * 取得と互換フォールバック (API 不通時はフィクスチャ) のみを行う。
 *
 * fail-closed (G層 #9): コース文脈もカートリッジ明示指定も無い場合はフォールバックせず
 * 取得自体を行わない (null = 地図領域ごと非表示)。既定カートリッジへのフォールバックは
 * 無関係な分野の地図を学習者に見せてしまう事故経路だったため廃止した。
 */
(function () {
  "use strict";

  const STORAGE_KEY = "atlas_data_source";

  let _runtimeSource = null; // runtime-config のキャッシュ (1回だけ取得)

  function ctx() {
    return window.AtlasContext || {};
  }

  // 明示指定 (window / localStorage)。無ければ null。
  function explicitSource() {
    if (window.ATLAS_DATA_SOURCE) return window.ATLAS_DATA_SOURCE;
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      if (v) return v;
    } catch (e) { /* localStorage 不可の環境ではそのまま */ }
    return null;
  }

  // サーバ設定を1回だけ取得しキャッシュする。到達不能なら "api" に倒す (gap4)。
  async function resolveSource() {
    const explicit = explicitSource();
    if (explicit) return explicit;
    if (_runtimeSource) return _runtimeSource;
    try {
      const res = await fetch("/api/atlas/runtime-config");
      if (res.ok) {
        const cfg = await res.json();
        _runtimeSource = (cfg && cfg.data_source) || "api";
        return _runtimeSource;
      }
    } catch (e) { /* noop */ }
    _runtimeSource = "api"; // 設定取得不能 → 本番安全側 (モック地図を出さない)
    return _runtimeSource;
  }

  // 後方互換の同期版。明示指定 > runtime キャッシュ > "fixture"。
  function source() {
    return explicitSource() || _runtimeSource || "fixture";
  }

  async function fetchFromApi(cartridgeId, opts) {
    const headers = {};
    try {
      const token = localStorage.getItem("eg_token");
      if (token) headers["Authorization"] = "Bearer " + token;
    } catch (e) { /* localStorage 不可の環境ではそのまま */ }

    const c = ctx();
    let url;
    if (c.courseId) {
      // gap3: コース文脈があればサーバがカートリッジ・focus を解決する
      url = "/api/atlas?course=" + encodeURIComponent(c.courseId);
      const topicId = (opts && opts.focusTopicId) || c.topicId;
      if (topicId) url += "&topic=" + encodeURIComponent(topicId);
    } else {
      // fail-closed: 明示指定 (引数 / window.AtlasContext.cartridgeId) が無ければ
      // 取得せず null を返す (地図領域ごと非表示)。既定カートリッジへは倒さない。
      const cid = cartridgeId || c.cartridgeId;
      if (!cid) return null;
      url = "/api/atlas?cartridge=" + encodeURIComponent(cid);
    }
    if (opts && opts.focus) url += "&focus=" + encodeURIComponent(opts.focus);

    const res = await fetch(url, { headers: headers });
    if (res.status === 404) return null; // 骨格なし → 地図機能を出さない (issue F 受け入れ条件6)
    if (!res.ok) throw new Error("atlas api " + res.status);
    return res.json();
  }

  // opts.focus: 初期選択したいノード id / opts.focusTopicId: トピック id (サーバで focus 解決)
  async function load(cartridgeId, opts) {
    if ((await resolveSource()) !== "api") return window.ATLAS_FIXTURE;
    try {
      return await fetchFromApi(cartridgeId, opts);
    } catch (err) {
      // fail-closed: source=api での取得・パース失敗時にフィクスチャへ退避しない
      // (本番でモック地図を出さない gap4 の意図)。骨格なし (404) と同じ null を返し、
      // 地図領域ごと非表示にする。フィクスチャは明示指定 (fixture) のときのみ。
      console.warn("[atlas] API 取得に失敗。地図を非表示にします:", err);
      return null;
    }
  }

  window.AtlasData = { load: load, source: source };
})();
