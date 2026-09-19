/*
 * 外部 AI 転送の常設事実文 — 対話の入力欄のそばに1行だけ置く。
 *
 * ES5 / IIFE。window.DisclosureNote を公開。学習画面は app.js、管理画面は
 * admin.js の初期化から DisclosureNote.init({apiFetch}) を呼んで起動する。
 *
 * 設計原則（docs/features/disclosure_axes_design.md, DA1〜DA6）:
 *   - DA2 宛先は provider だけ。**文言をここに焼き込まない** — GET /api/disclosure が
 *     返す data_kinds[].note をそのまま描く（provider はサーバが実行時の設定から
 *     差し込む）。モデル名・トークン数・金額は描かない。
 *   - DA3 認証済みなら誰でも読める API なので、ロール分岐を持たない。
 *   - DA5 告知であって同意ではない。ボタン・チェックボックス・モーダルを作らない
 *     （閉じる操作も持たない = 常設）。
 *   - fail-soft: 取得に失敗したら**何も描かない**（推測で文言を書かない・対話は妨げない）。
 *     ポーリングもしない（ログイン後1回だけ取得）。
 *   - 事実の段落であって操作要素ではないため data-ui-anchor は付けない。
 *
 * 使い方:
 *   静的な担体 <div data-disclosure-note="learning_chat"></div> は init() が自動で埋める。
 *   動的に組み立てるパネル（Copilot・W層・グラフレビュー等）は、描画のあとに
 *   DisclosureNote.mount(container, "course_materials") を呼ぶ。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null };
  var catalog = null;   // {data_kind: item} — 取得できたときだけ埋まる
  var loading = null;   // 進行中の Promise（多重取得の抑止）

  // fail-soft の要: 注入前に呼ばれても同期例外を投げない（rejected Promise を返す）。
  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    if (typeof fn !== "function") {
      return Promise.reject(new Error("DisclosureNote: apiFetch is not injected"));
    }
    try {
      return Promise.resolve(fn(path, opts));
    } catch (err) {
      return Promise.reject(err);
    }
  }

  // -------------------------------------------------------------------------
  // 取得（ログイン後1回・失敗は静かに諦める）
  // -------------------------------------------------------------------------

  function load() {
    if (catalog) return Promise.resolve(catalog);
    if (loading) return loading;
    loading = apiFetch("/disclosure")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        var map = {};
        var list = (data && data.data_kinds) || [];
        for (var i = 0; i < list.length; i++) {
          if (list[i] && list[i].data_kind) map[list[i].data_kind] = list[i];
        }
        catalog = map;
        return catalog;
      })
      .catch(function () {
        catalog = null;   // 事実文を出さない（捏造しない）
        return null;
      })
      .then(function (result) {
        loading = null;
        return result;
      });
    return loading;
  }

  /** ログアウト時に前のユーザーの取得結果を残さない（他モジュールの invalidate と同族）。 */
  function invalidate() {
    catalog = null;
    loading = null;
  }

  // -------------------------------------------------------------------------
  // 事実文
  // -------------------------------------------------------------------------

  /** データ種別の1行事実文（未取得・未登録なら空文字）。 */
  function factLine(dataKind) {
    if (!catalog) return "";
    var item = catalog[dataKind];
    if (!item) return "";
    return String(item.note || "");
  }

  /**
   * containerEl の中に事実文の段落を差し込む（既にあれば差し替える）。
   * 事実文が空（未取得・未登録）のときは何もしない。
   */
  function mount(containerEl, dataKind) {
    if (!containerEl || !dataKind) return;
    var pending;
    try {
      pending = load();
    } catch (err) {
      return;   // fail-soft: 事実文が描かれないだけで、呼び出し元は止めない。
    }
    if (!pending || typeof pending.then !== "function") return;
    pending.then(function () {
      var text = factLine(dataKind);
      if (!text) return;
      var selector = '.disclosure-note[data-disclosure-kind="' + dataKind + '"]';
      var existing = containerEl.querySelector(selector);
      if (existing) {
        existing.textContent = text;
        return;
      }
      var p = document.createElement("p");
      p.className = "disclosure-note";
      p.setAttribute("data-disclosure-kind", dataKind);
      p.textContent = text;
      containerEl.insertBefore(p, containerEl.firstChild);
    }).catch(function () {
      // fail-soft: 描画時の例外も握り潰す（事実文が出ないだけ）。
    });
  }

  /** 静的な担体（<div data-disclosure-note="...">）を全部埋める。 */
  function mountAll(rootEl) {
    var root = rootEl || document;
    var slots = root.querySelectorAll ? root.querySelectorAll("[data-disclosure-note]") : [];
    for (var i = 0; i < slots.length; i++) {
      mount(slots[i], slots[i].getAttribute("data-disclosure-note"));
    }
  }

  // -------------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------------

  function init(options) {
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    load().then(function () { mountAll(); }).catch(function () { /* fail-soft */ });
  }

  window.DisclosureNote = {
    init: init,
    load: load,
    factLine: factLine,
    mount: mount,
    mountAll: mountAll,
    invalidate: invalidate
  };
})();
