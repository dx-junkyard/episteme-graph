/*
 * ガイダンス層（G層）— 「次にやること」バッジ + Next Steps パネル。
 *
 * ES5 / IIFE。window.AdminNextSteps を公開。admin.js の initApp() から
 * AdminNextSteps.init({apiFetch, state}) を呼んで起動する。
 *
 * 設計原則（docs/features/guidance_layer_design.md）:
 *   - G1 完了フラグを持たない: 表示内容はサーバ側 GET /api/admin/assistant/next-steps の
 *     応答をそのまま描画するだけ（フロントは状態を持たない）。
 *   - G3 Capability Registry が単一の真実源: 権限判定はサーバ側（P1 と同型）。
 *     フロントは応答された steps/hidden を表示するだけで独自のロール判定はしない。
 *   - G4 押し付けない: バッジは件数を示すだけで、パネルを自動で開かない。ポーリングしない。
 *     再取得は admin.js 側の画面イベント（タブ切替・アップロード完了等）からの
 *     明示的な refresh() 呼び出しでのみ行う。
 *   - G8 道案内は誘導まで:「案内する」は既存 window.AdminAssistant.runLocatePlan() を
 *     呼ぶだけ。spotlight 機構をここで二重実装しない。
 *   - 取得失敗時は fail-closed（バッジ数字なし・cue も出さない。捏造しない）。
 *   - 識別子/CSS は admin-next-steps- プレフィックス（admin-assistant / atlas / doubt と非衝突）。
 */
(function () {
  "use strict";

  // 注入される依存（疎結合）。未注入なら window グローバルへフォールバック。
  var deps = { apiFetch: null, state: null };

  var toggleBtn = null, countEl = null, panelEl = null, bodyEl = null;
  var initialized = false;
  var isOpen = false;
  var lastData = null; // 直近の GET 応答 {steps, hidden, truncated, assistant_cue_pending}
  var hiddenExpanded = false;
  var cueShown = false;
  var cueBubbleEl = null;

  var SEVERITY_LABELS = { required: "● 必要", recommended: "○ 推奨", optional: "・任意" };
  var SEVERITY_ORDER = ["required", "recommended", "optional"];

  // -------------------------------------------------------------------------
  // ユーティリティ
  // -------------------------------------------------------------------------

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
  // バッジ + パネル DOM
  // -------------------------------------------------------------------------

  function buildBadge() {
    toggleBtn = document.getElementById("admin-next-steps-toggle");
    if (!toggleBtn) return;
    countEl = document.createElement("span");
    countEl.className = "admin-next-steps-count";
    toggleBtn.appendChild(countEl);
    toggleBtn.addEventListener("click", togglePanel);
  }

  function buildPanel() {
    panelEl = document.createElement("div");
    panelEl.className = "admin-next-steps-panel";
    panelEl.setAttribute("data-ui-anchor", "header.next-steps");
    panelEl.setAttribute("hidden", "hidden");
    panelEl.innerHTML =
      '<div class="admin-next-steps-head">' +
        '<span class="admin-next-steps-title">📋 次にやること</span>' +
        '<button type="button" class="admin-next-steps-close" title="閉じる">×</button>' +
      "</div>" +
      '<div class="admin-next-steps-body"><div class="admin-next-steps-empty">読み込み中...</div></div>';
    document.body.appendChild(panelEl);

    bodyEl = panelEl.querySelector(".admin-next-steps-body");
    panelEl.querySelector(".admin-next-steps-close").addEventListener("click", closePanel);

    // パネル外クリックで閉じる（G4: 自動で開くことはしないが、開いた後は普通のドロップダウンとして扱う）。
    document.addEventListener("click", function (e) {
      if (!isOpen || !panelEl) return;
      if (panelEl.contains(e.target)) return;
      if (toggleBtn && toggleBtn.contains(e.target)) return;
      closePanel();
    });
  }

  function openPanel() {
    if (!panelEl) return;
    panelEl.removeAttribute("hidden");
    isOpen = true;
  }
  function closePanel() {
    if (!panelEl) return;
    panelEl.setAttribute("hidden", "hidden");
    isOpen = false;
  }
  function togglePanel() {
    if (isOpen) closePanel(); else openPanel();
  }

  // -------------------------------------------------------------------------
  // 描画
  // -------------------------------------------------------------------------

  function render(data) {
    lastData = data || { steps: [], hidden: [], truncated: false };
    renderBadge(lastData);
    renderPanel(lastData);
  }

  function renderBadge(data) {
    if (!countEl) return;
    var steps = data.steps || [];
    if (!steps.length) {
      countEl.textContent = "";
      countEl.className = "admin-next-steps-count";
      return;
    }
    var requiredN = 0, i;
    for (i = 0; i < steps.length; i++) {
      if (steps[i].severity === "required") requiredN++;
    }
    countEl.textContent = " (" + steps.length + ")";
    countEl.className = "admin-next-steps-count" + (requiredN > 0 ? " required" : "");
  }

  function groupBySeverity(steps) {
    var groups = { required: [], recommended: [], optional: [] };
    (steps || []).forEach(function (s) {
      if (groups[s.severity]) groups[s.severity].push(s);
      else groups.optional.push(s);
    });
    return groups;
  }

  function renderItemHtml(step, isHiddenItem) {
    var html = '<div class="admin-next-steps-item" data-step-key="' + escHtml(step.step_key) + '">';
    html += '<div class="admin-next-steps-item-title">' + escHtml(step.title) + "</div>";
    if (step.reason) {
      html += '<div class="admin-next-steps-item-reason">' + escHtml(step.reason) + "</div>";
    }
    html += '<div class="admin-next-steps-item-actions">';
    if (isHiddenItem) {
      html += '<button type="button" class="admin-next-steps-act" data-act="restore">元に戻す</button>';
    } else {
      if (step.locate_plan) {
        html += '<button type="button" class="admin-next-steps-act primary" data-act="locate">案内する</button>';
      }
      if (step.dismissible !== false) {
        html += '<button type="button" class="admin-next-steps-act" data-act="dismiss">今はしない</button>';
      }
    }
    html += "</div></div>";
    return html;
  }

  function renderPanel(data) {
    if (!bodyEl) return;
    var steps = data.steps || [];
    var hidden = data.hidden || [];
    var html = "";

    if (!steps.length) {
      html += '<div class="admin-next-steps-empty">今やるべきことはありません。</div>';
    } else {
      var groups = groupBySeverity(steps);
      SEVERITY_ORDER.forEach(function (sev) {
        var items = groups[sev];
        if (!items.length) return;
        html += '<div class="admin-next-steps-group">';
        html += '<div class="admin-next-steps-group-label">' + SEVERITY_LABELS[sev] + "</div>";
        items.forEach(function (step) { html += renderItemHtml(step, false); });
        html += "</div>";
      });
      if (data.truncated) {
        html += '<div class="admin-next-steps-truncated-note">表示件数の上限に達しています。対応が終わると次の項目が表示されます。</div>';
      }
    }

    html += '<div class="admin-next-steps-foot">';
    html += '<button type="button" class="admin-next-steps-hidden-toggle"' + (hidden.length ? "" : " disabled") + ">非表示にした項目 (" + hidden.length + ") " + (hiddenExpanded ? "▾" : "▸") + "</button>";
    html += '<div class="admin-next-steps-hidden-list"' + (hiddenExpanded ? "" : " hidden") + ">";
    if (hiddenExpanded) {
      hidden.forEach(function (step) { html += renderItemHtml(step, true); });
    }
    html += "</div>";
    html += '<button type="button" class="admin-next-steps-assistant-link">🤖 操作アシスタントに質問する</button>';
    html += "</div>";

    bodyEl.innerHTML = html;
    wireItemButtons(bodyEl);

    var hiddenToggle = bodyEl.querySelector(".admin-next-steps-hidden-toggle");
    var hiddenList = bodyEl.querySelector(".admin-next-steps-hidden-list");
    if (hiddenToggle && !hiddenToggle.disabled) {
      hiddenToggle.addEventListener("click", function () {
        hiddenExpanded = !hiddenExpanded;
        renderPanel(lastData);
      });
    }

    var assistantLink = bodyEl.querySelector(".admin-next-steps-assistant-link");
    if (assistantLink) {
      assistantLink.addEventListener("click", function () {
        closePanel();
        if (window.AdminAssistant && typeof window.AdminAssistant.open === "function") {
          window.AdminAssistant.open();
        }
      });
    }
  }

  function findStep(stepKey) {
    if (!lastData) return null;
    var lists = [lastData.steps || [], lastData.hidden || []], i, j;
    for (i = 0; i < lists.length; i++) {
      for (j = 0; j < lists[i].length; j++) {
        if (lists[i][j].step_key === stepKey) return lists[i][j];
      }
    }
    return null;
  }

  function wireItemButtons(root) {
    var items = root.querySelectorAll(".admin-next-steps-item");
    items.forEach(function (itemEl) {
      var stepKey = itemEl.getAttribute("data-step-key");

      var locateBtn = itemEl.querySelector('[data-act="locate"]');
      if (locateBtn) {
        locateBtn.addEventListener("click", function () {
          var step = findStep(stepKey);
          closePanel();
          if (step && step.locate_plan && window.AdminAssistant && window.AdminAssistant.runLocatePlan) {
            window.AdminAssistant.runLocatePlan(step.locate_plan);
          }
        });
      }

      var dismissBtn = itemEl.querySelector('[data-act="dismiss"]');
      if (dismissBtn) {
        dismissBtn.addEventListener("click", function () { dismissStep(stepKey); });
      }

      var restoreBtn = itemEl.querySelector('[data-act="restore"]');
      if (restoreBtn) {
        restoreBtn.addEventListener("click", function () { restoreStep(stepKey); });
      }
    });
  }

  // -------------------------------------------------------------------------
  // dismiss / restore（G5: 却下は保持。行削除しない — サーバ側の責務）
  // -------------------------------------------------------------------------

  function dismissStep(stepKey) {
    if (!stepKey) return;
    apiFetch("/admin/assistant/next-steps/" + encodeURIComponent(stepKey) + "/dismiss", { method: "POST" })
      .then(function () { refresh(); })
      .catch(function () { /* 失敗時は何もしない。次回 refresh で再表示される（捏造しない） */ });
  }

  function restoreStep(stepKey) {
    if (!stepKey) return;
    apiFetch("/admin/assistant/next-steps/" + encodeURIComponent(stepKey) + "/restore", { method: "POST" })
      .then(function () { refresh(); })
      .catch(function () {});
  }

  // -------------------------------------------------------------------------
  // 初回ログイン一度きりの cue（§8）
  // -------------------------------------------------------------------------

  function showCueBubble(anchorEl, text) {
    if (!cueBubbleEl) {
      cueBubbleEl = document.createElement("div");
      cueBubbleEl.className = "admin-next-steps-cue-bubble";
      document.body.appendChild(cueBubbleEl);
    }
    cueBubbleEl.textContent = text || "";
    var r = anchorEl.getBoundingClientRect();
    // アンカーが画面下端に近い（Admin Copilot の右下フローティングボタン等）場合は
    // 吹き出しを上に出す。左右も画面半分より右側のアンカーなら右端揃えにする
    // （固定 left 揃えだと右下のフローティングボタン基準で画面外にはみ出すため）。
    var estimatedHeight = 40;
    var showAbove = r.bottom + 6 + estimatedHeight > window.innerHeight;
    var alignRight = r.left > window.innerWidth / 2;
    cueBubbleEl.style.top = showAbove
      ? (window.scrollY + r.top - estimatedHeight - 6) + "px"
      : (window.scrollY + r.bottom + 6) + "px";
    if (alignRight) {
      cueBubbleEl.style.left = "auto";
      cueBubbleEl.style.right = (window.innerWidth - window.scrollX - r.right) + "px";
    } else {
      cueBubbleEl.style.right = "auto";
      cueBubbleEl.style.left = (window.scrollX + r.left) + "px";
    }
    cueBubbleEl.style.display = "block";
    setTimeout(function () { if (cueBubbleEl) cueBubbleEl.style.display = "none"; }, 3600);
  }

  function maybeShowFirstLoginCue(data) {
    if (cueShown) return;
    // fail-closed: フィールド欠落・false ならここで何もしない（捏造しない）。
    if (!data || data.assistant_cue_pending !== true) return;
    var copilotBtn = document.getElementById("admin-copilot-toggle");
    if (!copilotBtn) return;
    cueShown = true;
    copilotBtn.classList.add("admin-next-steps-pulse");
    showCueBubble(copilotBtn, "操作に迷ったらここで質問できます");
    setTimeout(function () {
      if (copilotBtn) copilotBtn.classList.remove("admin-next-steps-pulse");
    }, 3000);
    // 表示後に一度きりフラグを永続化（assistant_step_dismissals の cue:first_login 行で代用）。
    apiFetch("/admin/assistant/next-steps/cue:first_login/dismiss", { method: "POST" }).catch(function () {});
  }

  // -------------------------------------------------------------------------
  // 再取得（G4: ポーリング禁止。呼び出し元は画面イベント発生時のみ呼ぶこと）
  // -------------------------------------------------------------------------

  function refresh() {
    // setupRoleBasedUI() may activate a role-specific default tab before
    // AdminNextSteps.init() has injected apiFetch.  Treat that early refresh as
    // a no-op; the normal initApp() path explicitly refreshes immediately after
    // init.  Without this guard SYSTEM_ADMIN initialization aborts before tabs
    // are wired, leaving the whole grouped navigation inert.
    if (!initialized) return;
    apiFetch("/admin/assistant/next-steps")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        render(data);
        maybeShowFirstLoginCue(data);
      })
      .catch(function () {
        // fail-closed: 取得失敗時はバッジ・パネルとも空表示に留め、cue も出さない（捏造しない）。
        render({ steps: [], hidden: [], truncated: false });
      });
  }

  // -------------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------------

  function init(options) {
    if (initialized) return;
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    deps.state = options.state || null;

    buildBadge();
    if (!toggleBtn) return; // admin.html にバッジが無い画面では何もしない
    buildPanel();
    initialized = true;
  }

  window.AdminNextSteps = {
    init: init,
    refresh: refresh
  };
})();
