/*
 * discuss 観測基盤（Observation Layer for Phase 3 Gate）管理 UI。
 * 正本: docs/features/discuss_observation_design.md §4（可視化）/ §5（ダンプ）。
 *
 * ES5 / IIFE。window.AdminDiscussObservation を公開。admin.js の initApp() から
 * AdminDiscussObservation.init({apiFetch, escHtml, onTabActivate, state}) を呼んで起動する
 * （admin-manual-editor.js / admin-llm-usage.js と同型の DI 注入パターン）。
 *
 * バックエンド API（別チーム実装、SYSTEM_ADMIN のみ）:
 *   GET /api/admin/discuss/observation-status
 *     → {generated_at, usage:{tag:{count,distinct_users,first_at,last_at}},
 *        traces:{total, grounding_recorded, grounding_distribution:{...}, recorded_since},
 *        ui_events:{total, by_event:{...}, first_at, last_at},
 *        criteria:[{key,label,current,target,met}], ready_for_analysis}
 *   GET /api/admin/discuss/observation-dump?format=tar.gz|zip
 *     → バイナリダウンロード（Content-Disposition 付き）
 *
 * 設計上の不変条項（本 UI での担保）:
 *   - DO3: 学習者に数値を見せない（このUI自体が SYSTEM_ADMIN 専用。学習者向け画面には出さない）。
 *   - DO5: 「参考目安」は表示のみで自動ゲートにしない（達成しても何も自動実行しない旨を明記する）。
 *   - ポーリングしない（定期タイマーでの自動再取得は行わない。取得は手動ボタンのみ）。
 *   - SYSTEM_ADMIN 以外にはタブ自体を表示しない（サーバ側 fail-closed に加えた表示側の隠蔽）。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null, escHtml: null, onTabActivate: null, state: null };
  var initialized = false;

  var USAGE_TAGS = [
    { key: "learning:chat_discuss", label: "discuss" },
    { key: "learning:chat_casual", label: "casual" },
    { key: "learning:chat", label: "通常チャット" }
  ];

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

  function _fmtNum(n) {
    return escHtml(String(n == null ? 0 : n));
  }

  function _fmtSpan(first, last) {
    return escHtml((first || "-") + " 〜 " + (last || "-"));
  }

  function setDumpStatus(text, isError) {
    var el = document.getElementById("ado-dump-status");
    if (!el) return;
    el.textContent = text || "";
    el.style.color = isError ? "var(--color-text-danger)" : "var(--color-text-secondary)";
  }

  // ---------------------------------------------------------------------
  // 描画
  // ---------------------------------------------------------------------

  function renderStatus(data) {
    data = data || {};
    var noteEl = document.getElementById("ado-generated-at");
    if (noteEl) noteEl.textContent = "生成時刻: " + (data.generated_at || "-");

    var body = document.getElementById("ado-body");
    if (!body) return;

    var usage = data.usage || {};
    var traces = data.traces || {};
    var grounding = traces.grounding_distribution || {};
    var uiEvents = data.ui_events || {};
    var byEvent = uiEvents.by_event || {};
    var criteria = Array.isArray(data.criteria) ? data.criteria : [];

    var html = "";

    // 1) LLM 利用量（3タグ別）
    html += '<h4 class="ado-subhead">LLM利用量（チャットタグ別）</h4>';
    html += '<div style="overflow-x:auto"><table class="admin-table"><thead><tr>' +
      '<th>タグ</th><th>件数</th><th>ユーザー数</th><th>期間</th>' +
      '</tr></thead><tbody>';
    USAGE_TAGS.forEach(function (t) {
      var u = usage[t.key] || {};
      html += "<tr><td>" + escHtml(t.label) + " <span style=\"color:var(--color-text-tertiary)\">(" +
        escHtml(t.key) + ")</span></td><td>" + _fmtNum(u.count) + "</td><td>" +
        _fmtNum(u.distinct_users) + "</td><td>" + _fmtSpan(u.first_at, u.last_at) + "</td></tr>";
    });
    html += "</tbody></table></div>";

    // 2) discuss 痕跡（grounding 分布）
    html += '<h4 class="ado-subhead">discuss 痕跡（会話ターン）</h4>';
    html += '<div class="ado-note">記録開始日: ' + escHtml(traces.recorded_since || "-") +
      '（それ以前の痕跡には grounding の記録がありません。grounding 分布は「記録済み件数」を分母にしています）</div>';
    html += '<div style="overflow-x:auto"><table class="admin-table"><thead><tr>' +
      '<th>discuss 総数</th><th>grounding 記録済み</th><th>教材由来</th><th>別の資料</th><th>AI生成</th>' +
      '</tr></thead><tbody><tr>' +
      "<td>" + _fmtNum(traces.total) + "</td>" +
      "<td>" + _fmtNum(traces.grounding_recorded) + "</td>" +
      "<td>" + _fmtNum(grounding.course_material) + "</td>" +
      "<td>" + _fmtNum(grounding.other_material) + "</td>" +
      "<td>" + _fmtNum(grounding.model_generated) + "</td>" +
      "</tr></tbody></table></div>";

    // 3) UI イベント別件数
    html += '<h4 class="ado-subhead">UI イベント（開幕・着地・分岐チップ等）</h4>';
    html += '<div class="ado-note">期間: ' + _fmtSpan(uiEvents.first_at, uiEvents.last_at) + '</div>';
    var eventKeys = Object.keys(byEvent);
    if (!eventKeys.length) {
      html += '<div class="ado-note">記録された UI イベントはまだありません。</div>';
    } else {
      html += '<div style="overflow-x:auto"><table class="admin-table"><thead><tr><th>イベント</th><th>件数</th></tr></thead><tbody>';
      eventKeys.forEach(function (k) {
        html += "<tr><td>" + escHtml(k) + "</td><td>" + _fmtNum(byEvent[k]) + "</td></tr>";
      });
      html += "</tbody></table></div>";
    }

    // 4) 分析開始の参考目安（DO5: 自動ゲートではない）
    html += '<h4 class="ado-subhead">分析開始の参考目安</h4>';
    if (criteria.length) {
      html += '<div style="overflow-x:auto"><table class="admin-table"><thead><tr>' +
        '<th>項目</th><th>現状</th><th>目安</th><th>達成</th></tr></thead><tbody>';
      criteria.forEach(function (c) {
        c = c || {};
        var current = (c.current === null || c.current === undefined) ? "-" : String(c.current);
        var target = (c.target === null || c.target === undefined) ? "-" : String(c.target);
        html += "<tr><td>" + escHtml(c.label || c.key || "") + "</td><td>" + escHtml(current) +
          "</td><td>" + escHtml(target) + "</td><td class=\"" +
          (c.met ? "ado-met" : "ado-unmet") + "\">" + (c.met ? "○" : "×") + "</td></tr>";
      });
      html += "</tbody></table></div>";
    } else {
      html += '<div class="ado-note">目安データがありません。</div>';
    }
    html += '<p class="ado-note">この一覧は参考目安であり、自動的に何かが起きることはありません。' +
      '分析に入るかどうかの判断は運用者が行います。</p>';

    body.innerHTML = html;
  }

  function loadStatus() {
    var body = document.getElementById("ado-body");
    if (body) body.innerHTML = '<div style="color:var(--color-text-tertiary)">読み込み中...</div>';
    apiFetch("/admin/discuss/observation-status")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(renderStatus)
      .catch(function () {
        if (body) body.innerHTML = '<div style="color:var(--color-text-danger)">読み込みに失敗しました</div>';
      });
  }

  // ---------------------------------------------------------------------
  // ダンプのダウンロード（認証付き: fetch + blob + <a download>、
  // admin-lecture-studio.js のエクスポートダウンロードと同じ方式）
  // ---------------------------------------------------------------------

  function doDownload(format) {
    var btnId = format === "zip" ? "ado-dump-zip-btn" : "ado-dump-targz-btn";
    var btn = document.getElementById(btnId);
    if (btn) btn.disabled = true;
    setDumpStatus("ダンプを生成しています...", false);

    apiFetch("/admin/discuss/observation-dump?format=" + encodeURIComponent(format))
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        var cd = res.headers.get("Content-Disposition") || "";
        var match = cd.match(/filename="?([^";\n]+)"?/);
        var filename = match ? match[1] : ("discuss-observation." + format);
        return res.blob().then(function (blob) {
          var url = URL.createObjectURL(blob);
          var a = document.createElement("a");
          a.href = url;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
        });
      })
      .then(function () {
        setDumpStatus("ダウンロードを開始しました。", false);
      })
      .catch(function () {
        setDumpStatus("ダンプの取得に失敗しました。", true);
      })
      .then(function () {
        if (btn) btn.disabled = false;
      });
  }

  // ---------------------------------------------------------------------
  // タブ構築
  // ---------------------------------------------------------------------

  function buildTab() {
    var panel = document.getElementById("tab-discuss-observation");
    if (!panel || panel.getAttribute("data-ado-built") === "1") return;
    panel.setAttribute("data-ado-built", "1");

    panel.innerHTML =
      '<div class="admin-section">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">' +
          '<h3 class="admin-section-title" style="margin-bottom:0">discuss 観測状況</h3>' +
          '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
            '<button id="ado-refresh-btn" class="admin-action-btn" data-ui-anchor="discuss-observation.refresh">取得</button>' +
            '<button id="ado-dump-targz-btn" class="admin-action-btn" data-ui-anchor="discuss-observation.dump-targz">ダンプ (tar.gz)</button>' +
            '<button id="ado-dump-zip-btn" class="admin-action-btn" data-ui-anchor="discuss-observation.dump-zip">ダンプ (zip)</button>' +
          '</div>' +
        '</div>' +
        '<p class="ado-note">' +
          'discuss モード（「論文と話す」）の Phase 3（v2）着手判断のための観測基盤です。' +
          'ここでの表示は参考目安であり、自動的に何かが起きることはありません。' +
          '自動更新（ポーリング）は行わないため、最新の状況は「取得」ボタンで手動更新してください。' +
        '</p>' +
        '<div id="ado-generated-at" class="ado-note"></div>' +
        '<div id="ado-dump-status" class="ado-note"></div>' +
        '<div id="ado-body">' +
          '<div style="color:var(--color-text-tertiary)">読み込み中...</div>' +
        '</div>' +
      '</div>';

    var refreshBtn = document.getElementById("ado-refresh-btn");
    if (refreshBtn) refreshBtn.addEventListener("click", loadStatus);
    var targzBtn = document.getElementById("ado-dump-targz-btn");
    if (targzBtn) targzBtn.addEventListener("click", function () { doDownload("tar.gz"); });
    var zipBtn = document.getElementById("ado-dump-zip-btn");
    if (zipBtn) zipBtn.addEventListener("click", function () { doDownload("zip"); });
  }

  // ---------------------------------------------------------------------
  // 公開 API
  // ---------------------------------------------------------------------

  function init(options) {
    if (initialized) return;
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    deps.escHtml = options.escHtml || null;
    deps.onTabActivate = options.onTabActivate || null;
    deps.state = options.state || null;

    // SYSTEM_ADMIN 以外にはタブ自体を作らない（サーバ側 403 に加えた表示側の fail-closed）。
    if (deps.state && deps.state.role === "SYSTEM_ADMIN") {
      buildTab();
      if (deps.onTabActivate) deps.onTabActivate("discuss-observation", loadStatus);
    }
    initialized = true;
  }

  window.AdminDiscussObservation = { init: init };
})();
