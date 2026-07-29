/*
 * 管理画面「？使い方」インスペクト・モード。
 *
 * 学習画面（index.html / app.js）の使い方インスペクト・モード
 * （docs/features/learning_ui_inspect_hover_design.md §4/§5/§8、app.js:3331-3520）を
 * ES5 / IIFE で管理画面向けに移植したもの。window.AdminHelpInspect を公開する。
 *
 * 設計原則（学習側と同一）:
 *   - アンカー表（GET /admin/assistant/help/ui-anchors）は init() 時に一度だけ取得し、
 *     メモリにキャッシュする。ホバーごとの API コールはしない（非LLM・fail-closed）。
 *   - ホバー対象は data-ui-anchor 属性を持つ要素のみ（IH9 相当）。
 *   - 未整備アンカーは固定事実文のみ表示し、1秒滞留したときだけ no_hit を
 *     セッション内で一度だけ記録する（ベストエフォート・失敗しても UX を止めない）。
 *   - クリックはインスペクト解除のみ（トグルボタン・バナー自身は除く）。Esc でも解除。
 *
 * 公開 API:
 *   window.AdminHelpInspect.init({apiFetch})
 *   window.AdminHelpInspect.toggle()
 *   window.AdminHelpInspect.isActive()          -> boolean
 *   window.AdminHelpInspect.hoveredAnchorId()   -> string|null
 *
 * init() は admin.js（並行実装）が認証済み apiFetch を注入して呼び出す。
 * DOM イベントの配線自体は DOMContentLoaded 時点で（init 呼び出しを待たずに）済ませるが、
 * アンカー表のネットワーク取得は init() 呼び出しの中でのみ行う（それ以前にホバーしても
 * 「説明未整備」表示になるだけで、余計な fetch は起きない）。
 */
(function () {
  "use strict";

  var _state = {
    active: false,
    anchors: null,           // { anchor_id: {title, body} } 取得前は null
    anchorsFetched: false,   // 1回だけフェッチする（失敗時も再フェッチしない）
    hoveredAnchorId: null,   // 最後にツールチップ表示したアンカーID（代表1件）
    reportedNoHitIds: {},    // セッション内 no_hit dedup（anchor 単位で1回まで）
    noHitTimer: null
  };

  var _deps = { apiFetch: null };

  var TOOLTIP_GAP = 8;
  var TOOLTIP_MARGIN = 12;
  var NO_HIT_DWELL_MS = 1000;

  function escHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function apiFetch(path, opts) {
    var fn = _deps.apiFetch || window.apiFetch;
    if (typeof fn !== "function") return Promise.reject(new Error("apiFetch not available"));
    return fn(path, opts);
  }

  // 同じ DOM 要素を複数の文書が参照している場合、data-ui-anchor に空白区切りで
  // 複数の anchor id を書けるようにする（例: "header.notifications versioning.inbox"）。
  // アンカー表に登録済みの最初の id を採用し、無ければ先頭を代表として扱う。
  function splitAnchorIds(raw) {
    return (raw || "").split(/\s+/).filter(function (s) { return s.length > 0; });
  }

  function resolveAnchorId(rawAttr) {
    var ids = splitAnchorIds(rawAttr);
    if (!ids.length) return null;
    var table = _state.anchors || {};
    var i;
    for (i = 0; i < ids.length; i++) {
      if (Object.prototype.hasOwnProperty.call(table, ids[i])) return ids[i];
    }
    return ids[0];
  }

  // アンカー表は init() 時に一度だけ取得する（ホバーごとの API コールはしない）。
  // 失敗時は空オブジェクト（fail-closed でツールチップは常に「未整備」扱いになる）。
  function fetchUiAnchorsOnce() {
    if (_state.anchorsFetched) return;
    _state.anchorsFetched = true;
    apiFetch("/admin/assistant/help/ui-anchors").then(function (res) {
      return res && res.ok ? res.json() : null;
    }).then(function (data) {
      _state.anchors = (data && data.anchors) || {};
    }).catch(function () {
      _state.anchors = {};
    });
  }

  function hideTooltip() {
    if (_state.noHitTimer) { clearTimeout(_state.noHitTimer); _state.noHitTimer = null; }
    var tip = document.getElementById("admin-inspect-tooltip");
    if (tip) { tip.hidden = true; tip.innerHTML = ""; }
  }

  // ツールチップより手前に居座る固定要素（topbar・インスペクトバナー）の下端を返す。
  function safeTopBound() {
    var bound = 0;
    var fixedEls = [document.querySelector(".topbar"), document.getElementById("admin-inspect-banner")];
    var i, el, rect;
    for (i = 0; i < fixedEls.length; i++) {
      el = fixedEls[i];
      if (!el || el.hidden) continue;
      rect = el.getBoundingClientRect();
      if (rect.height > 0) bound = Math.max(bound, rect.bottom);
    }
    return bound;
  }

  function positionTooltip(tip, anchorEl) {
    var r = anchorEl.getBoundingClientRect();
    var tw = Math.min(320, window.innerWidth - 24);
    tip.style.width = tw + "px";
    var left = Math.max(12, Math.min(r.left, window.innerWidth - tw - 12));
    tip.style.left = left + "px";

    // 自然な高さを実測する（前回のクランプが残っていると測り違える）。
    tip.style.maxHeight = "none";
    var th = tip.offsetHeight;

    var safeTop = safeTopBound() + TOOLTIP_MARGIN;
    var safeBottom = window.innerHeight - TOOLTIP_MARGIN;
    var spaceBelow = safeBottom - (r.bottom + TOOLTIP_GAP);
    var spaceAbove = (r.top - TOOLTIP_GAP) - safeTop;

    if (th <= spaceBelow || spaceBelow >= spaceAbove) {
      tip.style.bottom = "auto";
      tip.style.top = Math.max(safeTop, r.bottom + TOOLTIP_GAP) + "px";
      tip.style.maxHeight = Math.max(80, spaceBelow) + "px";
    } else {
      tip.style.top = "auto";
      tip.style.bottom = (window.innerHeight - r.top + TOOLTIP_GAP) + "px";
      tip.style.maxHeight = Math.max(80, spaceAbove) + "px";
    }
  }

  function reportNoHit(anchorId) {
    if (_state.reportedNoHitIds[anchorId]) return;
    _state.reportedNoHitIds[anchorId] = true;
    apiFetch("/admin/assistant/help/ui-anchor-events", {
      method: "POST",
      body: JSON.stringify({ anchor_id: anchorId, kind: "no_hit" })
    }).catch(function () { /* fail-open: 記録できなくても UX は止めない */ });
  }

  // UI 論理アンカーへのホバー中に出すツールチップ。既に配信済みのアンカー表データを
  // 見せるだけで、LLM・チャット API は一切呼ばない。
  function showTooltip(anchorEl, anchorId) {
    hideTooltip();
    _state.hoveredAnchorId = anchorId;
    var tip = document.getElementById("admin-inspect-tooltip");
    if (!tip) return;
    var table = _state.anchors || {};
    var entry = table[anchorId] || null;
    if (entry) {
      tip.innerHTML =
        '<div class="admin-inspect-tooltip-source">📖 使い方</div>' +
        '<div class="admin-inspect-tooltip-title">' + escHtml(entry.title || "") + "</div>" +
        '<div class="admin-inspect-tooltip-body">' + escHtml(entry.body || "") + "</div>";
    } else {
      // 未整備は固定事実文のみ。生成で埋めない。
      tip.innerHTML =
        '<div class="admin-inspect-tooltip-source">📖 使い方</div>' +
        '<div class="admin-inspect-tooltip-body">この部分の説明はまだ用意されていません</div>';
      _state.noHitTimer = setTimeout(function () {
        reportNoHit(anchorId);
      }, NO_HIT_DWELL_MS);
    }
    tip.hidden = false;
    positionTooltip(tip, anchorEl);
  }

  function setActive(active) {
    if (_state.active === active) return;
    _state.active = active;
    document.body.classList.toggle("admin-inspect-mode", active);
    var banner = document.getElementById("admin-inspect-banner");
    if (banner) banner.hidden = !active;
    var toggleBtn = document.getElementById("admin-help-usage-btn");
    if (toggleBtn) toggleBtn.classList.toggle("active", active);
    hideTooltip();
    _state.hoveredAnchorId = null;
  }

  function toggle() {
    setActive(!_state.active);
  }

  function bindEvents() {
    // IH4 相当: インスペクト中のクリックは解除のみ。capture フェーズで先取りし、
    // 下の操作（送信・削除等）へ伝播させない。トグルボタン自身とバナー内だけは
    // 通常のクリック動作を許可する。
    document.addEventListener("click", function (e) {
      if (!_state.active) return;
      if (e.target.closest("#admin-help-usage-btn, #admin-inspect-banner")) return;
      e.preventDefault();
      e.stopPropagation();
      setActive(false);
    }, true);

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && _state.active) setActive(false);
    });

    // ホバー対象は data-ui-anchor 登録済み要素のみ。
    document.addEventListener("mouseover", function (e) {
      if (!_state.active) return;
      var el = e.target.closest("[data-ui-anchor]");
      if (!el) return;
      var anchorId = resolveAnchorId(el.getAttribute("data-ui-anchor"));
      if (!anchorId) return;
      showTooltip(el, anchorId);
    });
    document.addEventListener("mouseout", function (e) {
      if (!_state.active) return;
      var el = e.target.closest("[data-ui-anchor]");
      if (!el) return;
      if (e.relatedTarget && el.contains(e.relatedTarget)) return;
      hideTooltip();
    });

    var toggleBtn = document.getElementById("admin-help-usage-btn");
    if (toggleBtn) {
      toggleBtn.addEventListener("click", function () {
        toggle();
      });
    }
  }

  function onReady() {
    bindEvents();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }

  window.AdminHelpInspect = {
    init: function (opts) {
      opts = opts || {};
      _deps.apiFetch = opts.apiFetch || null;
      // アンカー表のネットワーク取得は init（認証済み apiFetch 注入後）でのみ行う。
      fetchUiAnchorsOnce();
    },
    toggle: toggle,
    isActive: function () { return _state.active; },
    hoveredAnchorId: function () { return _state.hoveredAnchorId; }
  };
})();
