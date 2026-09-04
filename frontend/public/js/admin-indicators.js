/*
 * 制度指標カタログ — 計器のそばに「これは何のための計器か」を1行で置く。
 *
 * ES5 / IIFE。window.AdminIndicators を公開。admin.js の initApp() から
 * AdminIndicators.init({apiFetch}) を呼んで起動する。
 *
 * 設計原則（docs/features/indicator_governance_design.md, IG1〜IG5）:
 *   - IG1 公開するのは**定義だけ**。GET /api/indicators は値を返さないので、
 *     ここでも数値・件数・レンジを描画しない（label / purpose の文字列のみ）。
 *   - IG2 非利用（ランキング・成績・推薦・自動ゲート）を必ず併記する。
 *     文言はサーバの固定文と同じ趣旨を1行に畳んだもので、計器ごとに変えない。
 *   - fail-soft: カタログの取得に失敗したら**何も描画しない**（捏造しない・
 *     計器本体の表示は妨げない）。ポーリングもしない（ログイン後1回だけ取得）。
 *   - 事実の段落であって操作要素ではないため data-ui-anchor は付けない
 *     （ボタン・リンクを足すときはアンカー3点セットを揃えること）。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null };
  var catalog = null;      // {id: item} — 取得できたときだけ埋まる
  var loading = null;      // 進行中の Promise（多重取得の抑止）

  // IG2: 全計器に共通の非利用宣言（サーバの CATALOG_NOTE と同趣旨の1行版）。
  var NON_USE_LINE = "個人の比較・成績・自動判定には使いません。";

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    return fn(path, opts);
  }

  function escHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // -------------------------------------------------------------------------
  // 取得（ログイン後1回・失敗は静かに諦める）
  // -------------------------------------------------------------------------

  function load() {
    if (catalog) return Promise.resolve(catalog);
    if (loading) return loading;
    loading = apiFetch("/indicators")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        var map = {};
        var list = (data && data.indicators) || [];
        for (var i = 0; i < list.length; i++) {
          if (list[i] && list[i].id) map[list[i].id] = list[i];
        }
        catalog = map;
        return catalog;
      })
      .catch(function () {
        // fail-soft: カタログが読めないときは事実文を出さない（推測で書かない）。
        catalog = null;
        return null;
      })
      .then(function (result) {
        loading = null;
        return result;
      });
    return loading;
  }

  // -------------------------------------------------------------------------
  // 事実文
  // -------------------------------------------------------------------------

  /** カタログに載っている計器の1行事実文。未取得・未登録なら空文字。 */
  function factLine(indicatorId) {
    if (!catalog) return "";
    var item = catalog[indicatorId];
    if (!item) return "";
    return "計器: " + item.label + " — " + item.purpose + NON_USE_LINE;
  }

  /**
   * containerEl の中に事実文の段落を差し込む（既にあれば差し替える）。
   * 事実文が空（カタログ未取得・未登録）のときは何もしない。
   */
  function mount(containerEl, indicatorId) {
    if (!containerEl) return;
    load().then(function () {
      var text = factLine(indicatorId);
      if (!text) return;
      var existing = containerEl.querySelector(
        '.indicator-fact[data-indicator-id="' + indicatorId + '"]'
      );
      if (existing) {
        existing.textContent = text;
        return;
      }
      var p = document.createElement("p");
      p.className = "indicator-fact";
      p.setAttribute("data-indicator-id", indicatorId);
      p.textContent = text;
      containerEl.insertBefore(p, containerEl.firstChild);
    });
  }

  // -------------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------------

  function init(options) {
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    load();
  }

  window.AdminIndicators = {
    init: init,
    load: load,
    factLine: factLine,
    mount: mount
  };
})();
