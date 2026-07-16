/*
 * U層（LLM トークン使用量推計, migration 043）— 管理 UI（ビジョン×UXギャップ調査 G2-U）。
 *
 * ES5 / IIFE。window.AdminLlmUsage を公開。admin.js の initApp() から
 * AdminLlmUsage.init({apiFetch, escHtml, onTabActivate, state}) を呼んで起動する
 * （admin-next-steps.js と同型の DI 注入パターン）。
 *
 * 2つの独立した窓口を持つ:
 *   1. SYSTEM_ADMIN 向け「LLM使用量」タブ — GET /api/admin/llm-usage/metrics。
 *      feature/model 別に reported（実測）/ estimated（推計）を分離表示する（U1）。
 *      dropped_events は 0 件でも必ず表示する（U8: 隠さない）。cost_usd は
 *      price_table_loaded=true のときのみ表示する（U7: 価格のハードコード禁止・
 *      価格表が無ければ費用を捏造しない）。
 *   2. TEACHER 向け「解析コスト見積り」ポップオーバー（教材行のボタンから起動）—
 *      GET /api/admin/llm-usage/estimate/documents/{id}。レンジのみ表示し、
 *      点推定・金額は一切出さない（U5）。
 *
 * 識別子/CSS は llm-usage- プレフィックス（Field Atlas / Doubt Atlas と非衝突）。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null, escHtml: null, onTabActivate: null, state: null };
  var initialized = false;
  var currentGroupBy = "feature,model";

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    return fn(path, opts);
  }

  function escHtml(value) {
    if (deps.escHtml) return deps.escHtml(value);
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // -------------------------------------------------------------------------
  // 1. SYSTEM_ADMIN 向け「LLM使用量」タブ
  // -------------------------------------------------------------------------

  function buildTab() {
    var panel = document.getElementById("tab-llm-usage");
    if (!panel || panel.getAttribute("data-llm-usage-built") === "1") return;
    panel.setAttribute("data-llm-usage-built", "1");
    panel.innerHTML =
      '<div class="admin-section">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">' +
          '<h3 class="admin-section-title" style="margin-bottom:0">LLM使用量</h3>' +
          '<div style="display:flex;gap:8px;align-items:center">' +
            '<select id="llm-usage-groupby" style="padding:4px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
              '<option value="feature,model">feature &times; model</option>' +
              '<option value="feature">feature</option>' +
              '<option value="model">model</option>' +
              '<option value="provider">provider</option>' +
              '<option value="operation">operation</option>' +
              '<option value="day">day</option>' +
            '</select>' +
            '<button id="llm-usage-refresh" class="admin-action-btn">更新</button>' +
          '</div>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin-bottom:10px">' +
          '実測(reported)と推計(estimated)は常に分けて表示します（合算した単一数値は作りません）。' +
          '価格表が設定されている場合のみ費用(概算)の列に金額が入ります。' +
        '</p>' +
        '<div id="llm-usage-range-note" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:4px"></div>' +
        '<div id="llm-usage-price-note" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:4px"></div>' +
        '<div id="llm-usage-dropped-note" style="font-size:12px;margin-bottom:10px"></div>' +
        '<div id="llm-usage-metrics-body">' +
          '<div style="color:var(--color-text-tertiary)">読み込み中...</div>' +
        '</div>' +
      '</div>';

    var groupSel = document.getElementById("llm-usage-groupby");
    if (groupSel) {
      groupSel.value = currentGroupBy;
      groupSel.addEventListener("change", function () {
        currentGroupBy = groupSel.value;
        loadMetrics();
      });
    }
    var refreshBtn = document.getElementById("llm-usage-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", loadMetrics);
  }

  function loadMetrics() {
    var body = document.getElementById("llm-usage-metrics-body");
    if (!body) return;
    body.innerHTML = '<div style="color:var(--color-text-tertiary)">読み込み中...</div>';
    apiFetch("/admin/llm-usage/metrics?group_by=" + encodeURIComponent(currentGroupBy))
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(renderMetrics)
      .catch(function () {
        body.innerHTML = '<div style="color:var(--color-text-danger)">読み込みに失敗しました</div>';
      });
  }

  function _fmtNum(n) {
    return escHtml(String(n || 0));
  }

  function renderMetrics(data) {
    var rangeNote = document.getElementById("llm-usage-range-note");
    if (rangeNote) {
      rangeNote.textContent = "集計期間: " + (data.from || "-") + " 〜 " + (data.to || "-");
    }
    var priceNote = document.getElementById("llm-usage-price-note");
    if (priceNote) {
      priceNote.textContent = data.price_table_loaded
        ? "価格表: 設定あり（費用(概算)列に金額が入ります）"
        : "価格表: 未設定（費用は表示されません。金額を推定表示することはありません）";
    }
    var droppedNote = document.getElementById("llm-usage-dropped-note");
    if (droppedNote) {
      // U8: dropped_events は 0 件でも必ず表示する（隠さない）。
      var dropped = data.dropped_events || 0;
      droppedNote.textContent = "記録バッファから溢れて記録できなかったイベント: " + dropped + "件";
      droppedNote.style.color = dropped > 0 ? "var(--color-text-danger)" : "var(--color-text-tertiary)";
    }

    var body = document.getElementById("llm-usage-metrics-body");
    if (!body) return;
    var rows = data.rows || [];
    if (!rows.length) {
      body.innerHTML = '<div style="color:var(--color-text-tertiary)">対象期間に記録されたLLM使用量はありません。</div>';
      return;
    }

    var html = '<div style="overflow-x:auto"><table class="admin-table llm-usage-table"><thead>' +
      '<tr>' +
        '<th rowspan="2">内訳</th>' +
        '<th colspan="4" style="text-align:center">実測 (reported)</th>' +
        '<th colspan="4" style="text-align:center">推計 (estimated)</th>' +
        '<th rowspan="2">費用(概算)</th>' +
      '</tr>' +
      '<tr>' +
        '<th>入力</th><th>出力</th><th>合計</th><th>回数</th>' +
        '<th>入力</th><th>出力</th><th>合計</th><th>回数</th>' +
      '</tr>' +
      '</thead><tbody>';

    rows.forEach(function (row) {
      var key = row.key || {};
      var keyParts = [];
      Object.keys(key).forEach(function (k) {
        keyParts.push(k + ": " + (key[k] === null || key[k] === undefined || key[k] === "" ? "(未帰属)" : key[k]));
      });
      var keyLabel = keyParts.length ? keyParts.join(" / ") : "(全体)";
      var rep = row.reported || {};
      var est = row.estimated || {};
      var cost = row.cost_usd;
      var costCell = (cost === null || cost === undefined) ? "-" : ("$" + Number(cost).toFixed(6));
      html += "<tr>" +
        "<td>" + escHtml(keyLabel) + "</td>" +
        "<td>" + _fmtNum(rep.prompt_tokens) + "</td>" +
        "<td>" + _fmtNum(rep.completion_tokens) + "</td>" +
        "<td>" + _fmtNum(rep.total_tokens) + "</td>" +
        "<td>" + _fmtNum(rep.calls) + "</td>" +
        "<td>" + _fmtNum(est.prompt_tokens) + "</td>" +
        "<td>" + _fmtNum(est.completion_tokens) + "</td>" +
        "<td>" + _fmtNum(est.total_tokens) + "</td>" +
        "<td>" + _fmtNum(est.calls) + "</td>" +
        "<td>" + escHtml(costCell) + "</td>" +
        "</tr>";
    });
    html += "</tbody></table></div>";
    body.innerHTML = html;
  }

  // -------------------------------------------------------------------------
  // 2. TEACHER 向け「解析コスト見積り」ポップオーバー（レンジのみ・金額なし, U5）
  // -------------------------------------------------------------------------

  var estimatePopoverEl = null;

  function closeEstimatePopover() {
    if (estimatePopoverEl && estimatePopoverEl.parentNode) {
      estimatePopoverEl.parentNode.removeChild(estimatePopoverEl);
    }
    estimatePopoverEl = null;
    document.removeEventListener("click", _onDocClickCloseEstimate, true);
  }

  function _onDocClickCloseEstimate(e) {
    if (estimatePopoverEl && !estimatePopoverEl.contains(e.target)) {
      closeEstimatePopover();
    }
  }

  function _positionPopover(pop, anchorEl) {
    var r = anchorEl.getBoundingClientRect();
    pop.style.position = "fixed";
    pop.style.top = (r.bottom + 6) + "px";
    var left = r.left;
    var maxLeft = window.innerWidth - 300;
    if (left > maxLeft) left = Math.max(8, maxLeft);
    pop.style.left = left + "px";
  }

  function openEstimatePopover(materialId, anchorEl) {
    if (!materialId || !anchorEl) return;
    closeEstimatePopover();

    var pop = document.createElement("div");
    pop.className = "llm-usage-estimate-popover";
    pop.innerHTML = '<div style="font-size:12px;color:var(--color-text-tertiary)">見積り中...</div>';
    document.body.appendChild(pop);
    estimatePopoverEl = pop;
    _positionPopover(pop, anchorEl);
    setTimeout(function () { document.addEventListener("click", _onDocClickCloseEstimate, true); }, 0);

    apiFetch("/admin/llm-usage/estimate/documents/" + encodeURIComponent(materialId))
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (estimatePopoverEl !== pop) return; // 別のポップオーバーに切り替わっていたら何もしない
        renderEstimatePopover(pop, data);
        _positionPopover(pop, anchorEl);
      })
      .catch(function () {
        if (estimatePopoverEl !== pop) return;
        pop.innerHTML = '<div style="font-size:12px;color:var(--color-text-danger)">見積りの取得に失敗しました</div>';
      });
  }

  function renderEstimatePopover(pop, data) {
    var stages = data.stages || [];
    var total = data.total_tokens_range || [0, 0];
    var html = '<div class="llm-usage-estimate-title">解析コスト見積り（トークン量のレンジのみ）</div>';
    html += '<div class="llm-usage-estimate-total">合計目安: ' +
      escHtml(String(total[0])) + ' 〜 ' + escHtml(String(total[1])) + ' トークン</div>';
    if (stages.length) {
      html += '<ul class="llm-usage-estimate-list">';
      stages.forEach(function (s) {
        var range = s.tokens_range || [0, 0];
        html += '<li>' + escHtml(s.stage || "") + ': ' +
          escHtml(String(range[0])) + ' 〜 ' + escHtml(String(range[1])) +
          (s.note ? ' <span class="llm-usage-estimate-note">(' + escHtml(s.note) + ')</span>' : '') +
          '</li>';
      });
      html += '</ul>';
    }
    html += '<div class="llm-usage-estimate-footnote">' +
      '推計値です（粗い係数によるレンジ・実測ではありません）。金額は表示しません。' +
      '</div>';
    pop.innerHTML = html;
  }

  // -------------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------------

  function init(options) {
    if (initialized) return;
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    deps.escHtml = options.escHtml || null;
    deps.onTabActivate = options.onTabActivate || null;
    deps.state = options.state || null;

    if (deps.state && deps.state.role === "SYSTEM_ADMIN") {
      buildTab();
      if (deps.onTabActivate) deps.onTabActivate("llm-usage", loadMetrics);
    }
    initialized = true;
  }

  window.AdminLlmUsage = {
    init: init,
    openEstimatePopover: openEstimatePopover
  };
})();
