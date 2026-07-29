/*
 * Admin Copilot — 管理画面 統合 AI アシスタント（横断ユーティリティ層, migration 034）。
 *
 * ES5 / IIFE。window.AdminAssistant を公開。admin.js の initApp() から
 * AdminAssistant.init({apiFetch, state, activateTabView}) を呼んで起動する。
 *
 * 設計原則（docs/features/admin_assistant_design.md §2 / §9）:
 *   - 全タブ横断の常設フローティングパネル。3 モード（説明 / 道案内 / 代行）を単一チャットで。
 *   - 道案内は画面遷移＋点灯のみ（P8）。値入力・送信・保存は本人が行う。
 *   - 代行は可逆なら即適用+戻す、不可逆は確認カード（P2）。戻すは ActionStack。
 *   - 画面状態は各画面の registerScreenContext から取得（DOM スクレイプしない, §8）。
 *   - 点灯先は各画面の registerUiAnchors が論理ID→DOM を解決（バックエンドは DOM 非依存）。
 *   - 識別子/CSS は admin-assistant- プレフィックス（Field Atlas / Doubt Atlas と非衝突）。
 */
(function () {
  "use strict";

  var API = "/api";

  // 注入される依存（疎結合）。未注入なら window グローバルへフォールバック。
  var deps = { apiFetch: null, state: null, activateTabView: null };

  // 画面が登録するフック。
  var screenContextProviders = {}; // tab -> function(): {selection, visible_entities}
  var uiAnchorMaps = {};           // tab -> { anchor_id: function(param?): Element }
  var undoHandlers = {};           // tab -> { snapshot: fn, restore: fn }  （L1 ローカル Undo 用）

  // UI / 会話状態。
  var panelEl = null, bodyEl = null, inputEl = null, undoBtn = null, toggleBtn = null;
  var hintEl = null;
  var initialized = false;
  var busy = false;
  var history = [];        // [{role, content}]
  var actionStack = [];    // [{kind:'server'|'local', label, reversible, action_id?, undo?}]
  var sessionId = "as_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

  // -------------------------------------------------------------------------
  // ユーティリティ
  // -------------------------------------------------------------------------

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    return fn(path, opts);
  }

  function activateTabView(tab) {
    var fn = deps.activateTabView || window.activateTabView;
    if (typeof fn === "function") fn(tab);
  }

  function currentRole() {
    var st = deps.state || window.state || {};
    return st.role || "";
  }

  function escHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function renderMarkdown(text) {
    var html = escHtml(text);
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.split("\n\n").map(function (p) { return "<p>" + p + "</p>"; }).join("");
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  function activeTab() {
    var on = document.querySelector(".admin-tab.on");
    return on ? (on.dataset ? on.dataset.tab : on.getAttribute("data-tab")) : "";
  }

  function collectScreenContext() {
    var tab = activeTab();
    var ctx = { tab: tab, selection: {}, visible_entities: [] };
    var provider = screenContextProviders[tab];
    if (typeof provider === "function") {
      try {
        var s = provider() || {};
        if (s.selection) ctx.selection = s.selection;
        if (s.visible_entities) ctx.visible_entities = s.visible_entities;
      } catch (e) { /* 画面提供が壊れても説明モードは動かす */ }
    }
    return ctx;
  }

  // -------------------------------------------------------------------------
  // パネル DOM
  // -------------------------------------------------------------------------

  // 常設フローティング起動ボタン（右下）。以前この位置は通知ベル（🔔）が占めていたが、
  // 「AI アシスタントへのアクセスを良くする」ため位置を入れ替え、通知はトップバーへ移した。
  function buildToggleButton() {
    toggleBtn = document.createElement("button");
    toggleBtn.id = "admin-copilot-toggle";
    toggleBtn.type = "button";
    toggleBtn.className = "admin-assistant-fab";
    toggleBtn.setAttribute("data-ui-anchor", "header.copilot");
    toggleBtn.title = "管理画面の操作を手伝う AI アシスタント";
    toggleBtn.innerHTML = "🤖";
    toggleBtn.addEventListener("click", togglePanel);
    document.body.appendChild(toggleBtn);
  }

  function buildPanel() {
    panelEl = document.createElement("div");
    panelEl.className = "admin-assistant-panel";
    panelEl.setAttribute("hidden", "hidden");
    panelEl.innerHTML =
      '<div class="admin-assistant-head">' +
        '<span class="admin-assistant-title">🤖 操作アシスタント</span>' +
        '<button type="button" class="admin-assistant-close" title="閉じる">×</button>' +
      "</div>" +
      '<div class="admin-assistant-body"></div>' +
      '<div class="admin-assistant-foot">' +
        '<button type="button" class="admin-assistant-undo" disabled title="直前の操作を戻す">↩ 戻す</button>' +
        '<div class="admin-assistant-inputrow">' +
          '<textarea class="admin-assistant-input" rows="1" placeholder="操作の説明・道案内・代行を頼めます"></textarea>' +
          '<button type="button" class="admin-assistant-send">送信</button>' +
        "</div>" +
      "</div>";
    document.body.appendChild(panelEl);

    bodyEl = panelEl.querySelector(".admin-assistant-body");
    inputEl = panelEl.querySelector(".admin-assistant-input");
    undoBtn = panelEl.querySelector(".admin-assistant-undo");
    var sendBtn = panelEl.querySelector(".admin-assistant-send");
    var closeBtn = panelEl.querySelector(".admin-assistant-close");

    sendBtn.addEventListener("click", onSend);
    closeBtn.addEventListener("click", closePanel);
    undoBtn.addEventListener("click", onUndo);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        onSend();
      }
    });
  }

  function openPanel() {
    if (!panelEl) return;
    panelEl.removeAttribute("hidden");
    if (bodyEl && !bodyEl.childNodes.length) greet();
    if (inputEl) inputEl.focus();
  }
  function closePanel() { if (panelEl) panelEl.setAttribute("hidden", "hidden"); }
  function togglePanel() {
    if (!panelEl) return;
    if (panelEl.hasAttribute("hidden")) openPanel(); else closePanel();
  }

  // N12（実行可否の事前開示）: 挨拶文の「代行できます」例示は、サーバが executable=true と
  // 申告した（＝代行ハンドラ実装済みの）操作に限定する。未実装の action は
  // 「道案内のみ対応」と明示し、どれが実際に動くかを事前に区別できるようにする。
  function buildGreeting(capabilities) {
    var base =
      "こんにちは。管理画面の操作をお手伝いします。\n\n" +
      "・**説明**: 「コースを公開するには？」\n" +
      "・**道案内**: 「教材ってどこからアップロードするの？」\n";
    var executable = [];
    var guideOnly = [];
    var i, c;
    if (capabilities && capabilities.length) {
      for (i = 0; i < capabilities.length; i++) {
        c = capabilities[i];
        if (!c || c.kind !== "action") continue;
        if (c.executable) executable.push(c.title);
        else guideOnly.push(c.title);
      }
    }
    if (executable.length) {
      base += "・**代行**（実行まで対応）: " + executable.join("・") + "\n";
      if (guideOnly.length) {
        base += "・**道案内のみ対応**: " + guideOnly.join("・") + "\n";
      }
    } else {
      // capability 一覧が取れないときは、実装済みと確認されている例だけを出す（捏造しない, P4）。
      base += "・**代行**: 「このコースを公開して」\n";
    }
    base += "\nあなたの権限でできる操作だけをご案内します。";
    return base;
  }

  function greet() {
    // サーバの capability 一覧（executable フラグ付き）で挨拶を組み立てる。
    // 取得失敗時は静的文面へ縮退（fail-closed。代行例示は実装済みのもののみ）。
    apiFetch("/admin/assistant/capabilities").then(function (res) {
      return res.ok ? res.json() : null;
    }).then(function (rows) {
      if (bodyEl && !bodyEl.childNodes.length) appendAiMsg(buildGreeting(rows));
    }).catch(function () {
      if (bodyEl && !bodyEl.childNodes.length) appendAiMsg(buildGreeting(null));
    });
  }

  // -------------------------------------------------------------------------
  // メッセージ描画
  // -------------------------------------------------------------------------

  function appendUserMsg(text) {
    var el = document.createElement("div");
    el.className = "admin-assistant-msg user";
    el.textContent = text;
    bodyEl.appendChild(el);
    scrollBottom();
  }

  function appendAiMsg(text) {
    var el = document.createElement("div");
    el.className = "admin-assistant-msg ai";
    el.innerHTML = renderMarkdown(text);
    bodyEl.appendChild(el);
    scrollBottom();
    return el;
  }

  function appendCitations(node, citations) {
    if (!citations || !citations.length) return;
    var box = document.createElement("div");
    box.className = "admin-assistant-cites";
    box.textContent = "根拠: " + citations.map(function (c) { return c.doc || ""; }).join(" / ");
    node.appendChild(box);
  }

  function appendButton(node, label, cls, handler) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "admin-assistant-act " + (cls || "");
    b.textContent = label;
    b.addEventListener("click", handler);
    node.appendChild(b);
    return b;
  }

  var typingEl = null;
  function showTyping() {
    hideTyping();
    typingEl = document.createElement("div");
    typingEl.className = "admin-assistant-msg ai";
    typingEl.innerHTML = '<div class="admin-assistant-typing"><span></span><span></span><span></span></div>';
    bodyEl.appendChild(typingEl);
    scrollBottom();
  }
  function hideTyping() { if (typingEl && typingEl.parentNode) typingEl.parentNode.removeChild(typingEl); typingEl = null; }

  function scrollBottom() { if (bodyEl) bodyEl.scrollTop = bodyEl.scrollHeight; }

  // -------------------------------------------------------------------------
  // 送信 → /chat
  // -------------------------------------------------------------------------

  function onSend() {
    if (busy || !inputEl) return;
    var message = (inputEl.value || "").trim();
    if (!message) return;
    inputEl.value = "";
    appendUserMsg(message);
    history.push({ role: "user", content: message });
    busy = true;
    showTyping();

    var payload = {
      message: message,
      history: history.slice(-10),
      session_id: sessionId,
      screen_context: collectScreenContext()
    };
    // admin「？使い方」インスペクト・モード中は typed usage_help として送る
    // （ホバー中のアンカーIDを添えて、意図分類LLMを経由しない一次経路に載せる）。
    if (window.AdminHelpInspect && window.AdminHelpInspect.isActive()) {
      payload.support_action = "usage_help";
      payload.ui_anchor = window.AdminHelpInspect.hoveredAnchorId();
    }

    apiFetch("/admin/assistant/chat", {
      method: "POST",
      body: JSON.stringify(payload)
    }).then(function (res) { return res.json(); })
      .then(function (data) { hideTyping(); handleChatResponse(data); })
      .catch(function () { hideTyping(); appendAiMsg("すみません、うまく処理できませんでした。もう一度お試しください。"); })
      .then(function () { busy = false; });
  }

  function handleChatResponse(data) {
    if (!data || !data.answer) { appendAiMsg("うまく応答できませんでした。"); return; }
    var node = appendAiMsg(data.answer);
    history.push({ role: "assistant", content: data.answer });
    appendCitations(node, data.citations);

    if (data.intent === "locate" && data.locate_plan && data.locate_plan.steps && data.locate_plan.steps.length) {
      var plan = data.locate_plan;
      appendButton(node, "この場所へ案内", "primary", function () { runLocatePlan(plan); });
    } else if (data.intent === "action" && data.action_plan) {
      renderActionControls(node, data.action_plan);
    } else if (data.next_actions && data.next_actions.length) {
      var nav = null, i;
      for (i = 0; i < data.next_actions.length; i++) {
        if (data.next_actions[i].type === "navigate") { nav = data.next_actions[i]; break; }
      }
      if (nav && nav.screen) {
        appendButton(node, "そのタブを開く", "", function () { activateTabView(nav.screen); });
      }
    }
  }

  function renderActionControls(node, plan) {
    if (!plan.supported) {
      // 段階登録（P1）: 代行未対応。案内へ誘導。
      return;
    }
    if (plan.confirm_required) {
      var card = document.createElement("div");
      card.className = "admin-assistant-confirm";
      card.innerHTML = '<div class="admin-assistant-confirm-note">この操作は取り消せません。実行してよろしいですか？</div>';
      appendButton(card, "実行する", "danger", function () { executeAction(plan, true, card); });
      appendButton(card, "やめる", "", function () { if (card.parentNode) card.parentNode.removeChild(card); });
      node.appendChild(card);
    } else {
      appendButton(node, "実行する", "primary", function (e) {
        if (e && e.target) e.target.setAttribute("disabled", "disabled");
        executeAction(plan, false, null);
      });
    }
  }

  // -------------------------------------------------------------------------
  // 代行実行 → /actions
  // -------------------------------------------------------------------------

  function executeAction(plan, confirm, card) {
    if (busy) return;
    busy = true;
    showTyping();
    apiFetch("/admin/assistant/actions", {
      method: "POST",
      body: JSON.stringify({
        capability_id: plan.capability_id,
        target: plan.target || {},
        args: plan.args || {},
        session_id: sessionId,
        confirm: !!confirm
      })
    }).then(function (res) {
      return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
    }).then(function (r) {
      hideTyping();
      if (card && card.parentNode) card.parentNode.removeChild(card);
      var data = r.data || {};
      if (!r.ok) {
        appendAiMsg("実行できませんでした: " + (data.detail || r.status));
        return;
      }
      if (data.status === "confirm_pending") {
        appendAiMsg(data.message || "この操作には確認が必要です。");
        return;
      }
      appendAiMsg(data.message || "実行しました。");
      pushAction({
        kind: "server",
        label: plan.title || plan.capability_id,
        reversible: !!data.reversible,
        action_id: data.action_id
      });
      // G層: 代行（例: course.publish の公開）成功で Next Steps の対象が変わり得るため再取得する。
      // 会話1コールに留める（P6 と同型）ため next-steps 自体には LLM を呼ばない。
      if (window.AdminNextSteps) window.AdminNextSteps.refresh();
    }).catch(function () {
      hideTyping();
      appendAiMsg("実行中にエラーが発生しました。");
    }).then(function () { busy = false; });
  }

  // -------------------------------------------------------------------------
  // 戻す（ActionStack: L1 ローカル / L2 サーバ revert を一元管理）
  // -------------------------------------------------------------------------

  function pushAction(entry) {
    actionStack.push(entry);
    refreshUndo();
  }

  function refreshUndo() {
    if (!undoBtn) return;
    var top = actionStack.length ? actionStack[actionStack.length - 1] : null;
    if (!top) {
      undoBtn.setAttribute("disabled", "disabled");
      undoBtn.title = "戻せる操作はありません";
    } else if (!top.reversible) {
      undoBtn.removeAttribute("disabled");
      undoBtn.title = "この操作は取り消せません";
    } else {
      undoBtn.removeAttribute("disabled");
      undoBtn.title = "「" + top.label + "」を戻す";
    }
  }

  function onUndo() {
    if (!actionStack.length) return;
    var top = actionStack[actionStack.length - 1];
    if (!top.reversible) {
      appendAiMsg("「" + top.label + "」は取り消せない操作です。");
      return;
    }
    actionStack.pop();
    if (top.kind === "local" && typeof top.undo === "function") {
      try { top.undo(); appendAiMsg("「" + top.label + "」を戻しました。"); }
      catch (e) { appendAiMsg("戻す処理に失敗しました。"); }
      refreshUndo();
      return;
    }
    // L2: サーバ revert
    busy = true;
    apiFetch("/admin/assistant/actions/" + encodeURIComponent(top.action_id) + "/revert", {
      method: "POST"
    }).then(function (res) {
      return res.json().then(function (data) { return { ok: res.ok, data: data }; });
    }).then(function (r) {
      if (r.ok) appendAiMsg((r.data && r.data.message) || "操作を取り消しました。");
      else { appendAiMsg("取り消せませんでした: " + ((r.data && r.data.detail) || "")); actionStack.push(top); }
    }).catch(function () {
      appendAiMsg("取り消し中にエラーが発生しました。"); actionStack.push(top);
    }).then(function () { busy = false; refreshUndo(); });
  }

  // G2 是正: サーバ側 assistant_actions 履歴（GET /admin/assistant/actions）から
  // actionStack を再構成する。従来 actionStack はメモリ内のみで、ページを再読み込み
  // すると「戻す」が常に空になっていた（P3「before スナップショットで取り消し可能」の
  // 約束がセッション内でしか成立していなかった）。まだ取り消し可能（reversible かつ
  // status='applied'）な行だけを、古い順に積み直す（最新のものが「戻す」の対象になる）。
  // 取得に失敗しても静かに空スタックのまま（fail-closed。ローカル L1 Undo はセッション
  // 限りのまま変更しない）。
  function loadServerActionHistory() {
    apiFetch("/admin/assistant/actions").then(function (res) {
      return res.ok ? res.json() : [];
    }).then(function (rows) {
      if (!rows || !rows.length) return;
      var revertible = [];
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        if (r && r.reversible && r.status === "applied") revertible.push(r);
      }
      // サーバは新しい順に返すため、古い順に積み直す（最新が一番上=次の Undo 対象）。
      revertible.reverse();
      for (var j = 0; j < revertible.length; j++) {
        actionStack.push({
          kind: "server",
          // AssistantActionSummary には人間向けタイトルが無いため capability_id を使う
          // （plan.title || plan.capability_id という既存のフォールバック方針と同じ, P4）。
          label: revertible[j].capability_id,
          reversible: true,
          action_id: revertible[j].action_id
        });
      }
      refreshUndo();
    }).catch(function () { /* fail-closed: 履歴なしのまま続行 */ });
  }

  // -------------------------------------------------------------------------
  // 道案内（runLocatePlan + スポットライト） — 変更を伴わない（P8）
  // -------------------------------------------------------------------------

  function resolveAnchor(screen, anchorId) {
    var map = uiAnchorMaps[screen];
    if (!map) return null;
    var fn = map[anchorId];
    if (typeof fn === "function") { try { return fn(); } catch (e) { return null; } }
    // テンプレート差し込み: "course_row:c_123" → base "course_row" を param 付きで解決。
    var idx = anchorId.indexOf(":");
    if (idx > 0) {
      var base = anchorId.slice(0, idx);
      var param = anchorId.slice(idx + 1);
      var bfn = map[base];
      if (typeof bfn === "function") { try { return bfn(param); } catch (e2) { return null; } }
    }
    return null;
  }

  function spotlight(el) {
    if (!el) return;
    el.classList.add("admin-assistant-spotlight");
    setTimeout(function () { el.classList.remove("admin-assistant-spotlight"); }, 4000);
  }

  function showHint(el, text) {
    if (!hintEl) {
      hintEl = document.createElement("div");
      hintEl.className = "admin-assistant-hintbubble";
      document.body.appendChild(hintEl);
    }
    hintEl.textContent = text || "";
    hintEl.style.display = text ? "block" : "none";
    if (el && text) {
      var r = el.getBoundingClientRect();
      hintEl.style.top = (window.scrollY + r.top - 8) + "px";
      hintEl.style.left = (window.scrollX + r.left) + "px";
    }
    clearTimeout(showHint._t);
    showHint._t = setTimeout(function () { if (hintEl) hintEl.style.display = "none"; }, 4200);
  }

  function runLocatePlan(plan) {
    if (!plan || !plan.steps || !plan.steps.length) return;
    var i = 0;
    function step() {
      if (i >= plan.steps.length) return;
      var s = plan.steps[i++];
      activateTabView(s.screen);
      // タブ切替直後の DOM 反映を待つ。
      setTimeout(function () {
        var el = resolveAnchor(s.screen, s.anchor_id);
        if (!el) {
          // 点灯先が現時点で無い → 捏造しない（P4）。ここまでで案内を止める。
          appendAiMsg("この先は、この画面での操作（" + (s.hint || "選択") + "）のあとにご案内します。");
          return;
        }
        try { el.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (e) { el.scrollIntoView(); }
        spotlight(el);
        showHint(el, s.hint);
        // 次のステップへ（入力・送信はしない, P8）。
        setTimeout(step, 1300);
      }, 220);
    }
    openPanel();
    step();
  }

  // -------------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------------

  function init(options) {
    if (initialized) return;
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    deps.state = options.state || null;
    deps.activateTabView = options.activateTabView || null;

    buildPanel();
    buildToggleButton();
    loadServerActionHistory();

    initialized = true;
  }

  window.AdminAssistant = {
    init: init,
    registerScreenContext: function (tab, fn) { screenContextProviders[tab] = fn; },
    registerUiAnchors: function (tab, map) {
      var cur = uiAnchorMaps[tab] || (uiAnchorMaps[tab] = {});
      for (var k in map) { if (map.hasOwnProperty(k)) cur[k] = map[k]; }
    },
    registerUndoHandler: function (tab, handlers) { undoHandlers[tab] = handlers; },
    runLocatePlan: runLocatePlan,
    resolveAnchor: resolveAnchor,
    open: openPanel,
    close: closePanel,
    toggle: togglePanel,
    say: function (t) { appendAiMsg(t); },
    // L1 ローカル Undo をスタックへ積む（画面が使う）。
    pushLocalUndo: function (label, undoFn) { pushAction({ kind: "local", label: label, reversible: true, undo: undoFn }); }
  };
})();
