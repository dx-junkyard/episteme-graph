/*
 * 分野の適合（カートリッジの形の宣言 × この論文） — 概念レジストリ P3-7。
 *
 * ES5 / IIFE。window.AdminCartridgeFit を公開。再解析モーダル（admin.js の
 * openReanalyzeOptionsModal）から AdminCartridgeFit.mount(container, opts) を
 * 呼んで起動する（admin.js 側の追記は「区画の器」1行と mount 呼び出しだけ）。
 *
 * 設計原則（docs/features/concept_registry_design.md §8 / KR5・KR6・KR8）:
 *   - **数値を描かない**（一致件数・スコア・cosine を出さない）。描くのは
 *     サーバが返した事実文と、主題語の**名前の列挙**だけ。
 *   - 文言はサーバ（core/cartridge_shape.py）が正本。ここに焼き込まない
 *     （見出しと「読み込み中」だけがフロント側の文字列）。
 *   - fail-soft: 取得に失敗したら**何も描かない**（再解析の操作を妨げない・
 *     推測で埋めない）。ポーリングしない（分野を選び直したときだけ 1 回引く）。
 *   - アップロード時（解析前）は材料が無いのでサーバが「まだ解析されていない」
 *     の 1 行を返す。ここで先回りして別の文言を作らない。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null };

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    if (typeof fn !== "function") {
      return Promise.reject(new Error("AdminCartridgeFit: apiFetch is not injected"));
    }
    try {
      return Promise.resolve(fn(path, opts));
    } catch (err) {
      return Promise.reject(err);
    }
  }

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function init(options) {
    if (options && typeof options.apiFetch === "function") deps.apiFetch = options.apiFetch;
  }

  function render(container, data) {
    if (!container) return;
    var facts = (data && data.facts) || [];
    if (!facts.length) { container.innerHTML = ""; return; }
    var html = '<div style="font-weight:600;margin-bottom:2px">分野の適合</div>';
    for (var i = 0; i < facts.length; i++) {
      html += '<div style="margin-bottom:2px">' + esc(facts[i]) + "</div>";
    }
    container.innerHTML = html;
  }

  /*
   * container: 事実文を描く器（admin.js 側が data-ui-anchor を付けて用意する）
   * opts: {documentId, cartridgeId, watchSelector}
   *   - cartridgeId が空（「指定しない」）なら区画ごと空にする（分野を選んで
   *     いないのに適合を語らない）。
   *   - watchSelector を渡すと、その select の change でもう一度引き直す
   *     （select は後から差し込まれるので、モーダルへの委譲で拾う）。
   */
  function mount(container, opts) {
    if (!container) return;
    var settings = opts || {};
    var documentId = String(settings.documentId || "");
    if (!documentId) { container.innerHTML = ""; return; }

    function load(cartridgeId) {
      var key = String(cartridgeId || "");
      if (!key) { container.innerHTML = ""; return; }
      container.innerHTML = '<div style="font-weight:600;margin-bottom:2px">分野の適合</div>' +
        "<div>読み込み中…</div>";
      apiFetch(
        "/admin/cartridges/" + encodeURIComponent(key) +
        "/fit?document_id=" + encodeURIComponent(documentId)
      )
        .then(function (res) {
          if (!res || !res.ok) throw new Error("status");
          return res.json();
        })
        .then(function (data) { render(container, data); })
        .catch(function () { container.innerHTML = ""; });
    }

    load(settings.cartridgeId);

    var root = settings.watchRoot || container.closest("#reanalyze-options-modal") || document;
    var selector = settings.watchSelector || "#reanalyze-domain-select";
    if (root && root.addEventListener) {
      root.addEventListener("change", function (e) {
        var target = e.target;
        if (!target || !target.matches || !target.matches(selector)) return;
        load(target.value || "");
      });
    }
  }

  window.AdminCartridgeFit = { init: init, mount: mount };
})();
