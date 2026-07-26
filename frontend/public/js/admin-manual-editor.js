/*
 * 利用者マニュアル KB（help_kb）draft/freeze 管理 UI（manual_help_kb_design.md Phase 3 ②）。
 *
 * ES5 / IIFE。window.ManualKbEditor を公開。admin.js の initApp() から
 * ManualKbEditor.init({apiFetch, escHtml, onTabActivate, state}) を呼んで起動する
 * （admin-assistant.js / admin-llm-usage.js と同型の DI 注入パターン）。
 *
 * バックエンド API（別チーム実装、SYSTEM_ADMIN のみ）:
 *   GET  /api/admin/help-kb/drafts                        — {state, drafts:[...]}
 *   GET  /api/admin/help-kb/drafts/{audience}/{file}       — {audience, file, content, revision}
 *   PUT  /api/admin/help-kb/drafts/{audience}/{file}       — body {content, expected_revision}
 *   POST /api/admin/help-kb/drafts/seed                    — 配信中スナップショットから draft を冪等シード
 *   POST /api/admin/help-kb/freeze                         — {version_no,...} / 422 {detail:{violations}}
 *   POST /api/admin/help-kb/serving-source                 — body {source: "files"|"db"}
 *   GET  /api/admin/help-kb/versions                       — 版メタ一覧
 *
 * 設計上の不変条項（本 UI での担保）:
 *   - draft の同時編集衝突（409）は自動マージしない。事実文 + 明示の再読込ボタンのみ提示する。
 *   - freeze / 配信ソース切替は事実文の確認モーダル（window.AdminDangerConfirm、無ければ
 *     window.confirm フォールバック）を必ず経由する。捏造・要約はせず、検証違反(violations)は
 *     サーバ応答をそのまま列挙する。
 *   - ポーリングしない（定期タイマーでの自動再取得は行わない）。数値スコア表示なし。
 *   - SYSTEM_ADMIN 以外にはタブ自体を表示しない（サーバ側 fail-closed に加えた表示側の隠蔽）。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null, escHtml: null, onTabActivate: null, state: null };
  var initialized = false;

  var AUDIENCE_LABELS = {
    student: "学生 (student)",
    teacher: "教員 (teacher)",
    system_admin: "管理者 (system_admin)"
  };
  var AUDIENCE_ORDER = ["student", "teacher", "system_admin"];

  // 直近ロード結果（選択ハイライトの再描画に使う）。
  var servingSource = null; // "files" | "db"
  var activeVersionNo = null;
  var lastDrafts = [];
  var versionsCache = [];
  var currentAudience = null;
  var currentFile = null;
  var currentRevision = null;

  // ---------------------------------------------------------------------
  // ユーティリティ
  // ---------------------------------------------------------------------

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

  function _draftPath(audience, file) {
    return "/admin/help-kb/drafts/" + encodeURIComponent(audience) + "/" + encodeURIComponent(file);
  }

  // GET/POST/PUT 共通: レスポンス JSON を返し、非 2xx は {status, body} を投げる。
  function _fetchJson(path, opts) {
    return apiFetch(path, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (!res.ok) {
          var err = new Error("http " + res.status);
          err.status = res.status;
          err.body = body;
          throw err;
        }
        return body;
      });
    });
  }

  function _confirmDanger(opts, onConfirm) {
    if (window.AdminDangerConfirm && typeof window.AdminDangerConfirm.open === "function") {
      window.AdminDangerConfirm.open(opts, onConfirm);
      return;
    }
    if (window.confirm((opts.title ? opts.title + "\n\n" : "") + opts.message)) {
      onConfirm();
    }
  }

  function setStatus(text, kind) {
    var el = document.getElementById("mke-status");
    if (!el) return;
    if (!text) { el.innerHTML = ""; return; }
    var color = kind === "error"
      ? "var(--color-text-danger)"
      : (kind === "success" ? "var(--color-text-success)" : "var(--color-text-secondary)");
    el.innerHTML = '<div style="color:' + color + '">' + escHtml(text) + "</div>";
  }

  // 凍結検証違反(violations)はサーバ応答をそのまま列挙する（要約・捏造しない）。
  function renderViolations(violations) {
    var el = document.getElementById("mke-status");
    if (!el) return;
    var html = '<div style="color:var(--color-text-danger)">' +
      "凍結検証に失敗しました。以下の指摘を解消してから再度お試しください。</div>" +
      '<ul style="margin:6px 0 0 18px;color:var(--color-text-danger)">';
    (violations || []).forEach(function (v) {
      var text = typeof v === "string" ? v : JSON.stringify(v);
      html += "<li>" + escHtml(text) + "</li>";
    });
    html += "</ul>";
    el.innerHTML = html;
  }

  function _guessNextVersionNo() {
    var max = 0;
    (versionsCache || []).forEach(function (v) {
      var n = v && v.version_no;
      if (typeof n === "number" && n > max) max = n;
    });
    if (!max && typeof activeVersionNo === "number") max = activeVersionNo;
    return max + 1;
  }

  // ---------------------------------------------------------------------
  // 描画
  // ---------------------------------------------------------------------

  function renderServingBadge() {
    var el = document.getElementById("mke-serving-badge");
    if (el) {
      var label = servingSource === "db"
        ? "配信中: DB凍結版 v" + (activeVersionNo != null ? activeVersionNo : "?")
        : "配信中: ファイル同梱版";
      el.innerHTML = '<span class="mke-serving-badge">' + escHtml(label) + "</span>";
    }
    var toFiles = document.getElementById("mke-switch-files-btn");
    var toDb = document.getElementById("mke-switch-db-btn");
    if (toFiles) toFiles.style.display = (servingSource === "db") ? "" : "none";
    if (toDb) toDb.style.display = (servingSource !== "db" && activeVersionNo != null) ? "" : "none";
  }

  function renderDraftList(drafts) {
    lastDrafts = drafts || [];
    var el = document.getElementById("mke-draft-list");
    if (!el) return;
    if (!lastDrafts.length) {
      el.innerHTML = '<div style="color:var(--color-text-tertiary);font-size:12px">' +
        "draft がありません。「ファイルから取込」で配信中スナップショットから作成してください。</div>";
      return;
    }
    var byAudience = {};
    lastDrafts.forEach(function (d) {
      var a = d.audience || "unknown";
      if (!byAudience[a]) byAudience[a] = [];
      byAudience[a].push(d);
    });
    var order = AUDIENCE_ORDER.slice();
    Object.keys(byAudience).forEach(function (a) {
      if (order.indexOf(a) === -1) order.push(a);
    });

    var html = "";
    order.forEach(function (a) {
      var files = byAudience[a];
      if (!files || !files.length) return;
      html += '<div class="mke-audience-group" style="margin-bottom:10px">' +
        '<div style="font-size:12px;font-weight:600;color:var(--color-text-secondary);margin-bottom:4px">' +
        escHtml(AUDIENCE_LABELS[a] || a) + "</div>";
      files.forEach(function (f) {
        var isActive = (currentAudience === a && currentFile === f.file);
        html += '<button type="button" class="mke-file-btn" data-audience="' + escHtml(a) +
          '" data-file="' + escHtml(f.file) + '" style="display:block;width:100%;text-align:left;' +
          "padding:5px 8px;margin-bottom:2px;border-radius:4px;cursor:pointer;font-size:12px;" +
          "border:1px solid " + (isActive ? "var(--color-text-link, #2f6fed)" : "var(--color-border)") + ";" +
          "background:" + (isActive ? "var(--color-bg-tertiary)" : "var(--color-bg-secondary)") + ";" +
          'color:var(--color-text-primary)">' +
          escHtml(f.file) +
          '<span style="float:right;color:var(--color-text-tertiary)">rev.' + escHtml(String(f.revision)) + "</span>" +
          "</button>";
      });
      html += "</div>";
    });
    el.innerHTML = html;
    el.querySelectorAll(".mke-file-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        selectFile(this.getAttribute("data-audience"), this.getAttribute("data-file"));
      });
    });
  }

  function renderVersions(versions) {
    versionsCache = versions || [];
    var el = document.getElementById("mke-versions-list");
    if (!el) return;
    if (!versionsCache.length) {
      el.innerHTML = '<div style="color:var(--color-text-tertiary)">まだ凍結版はありません。</div>';
      return;
    }
    var sorted = versionsCache.slice().sort(function (a, b) {
      return (b.version_no || 0) - (a.version_no || 0);
    });
    var html = "";
    sorted.forEach(function (v) {
      var label = "v" + (v.version_no != null ? v.version_no : "?");
      var when = v.created_at || v.frozen_at || v.updated_at || "";
      var isActive = servingSource === "db" && activeVersionNo != null && v.version_no === activeVersionNo;
      html += '<div style="padding:4px 0;border-bottom:1px solid var(--color-border);' +
        'display:flex;justify-content:space-between;gap:8px;font-size:12px">' +
        "<span>" + escHtml(label) +
        (isActive ? ' <span style="color:var(--color-text-success)">（配信中）</span>' : "") +
        "</span><span style=\"color:var(--color-text-tertiary)\">" + escHtml(String(when)) + "</span></div>";
    });
    el.innerHTML = html;
  }

  function updateEditorRevisionLabel() {
    var el = document.getElementById("mke-editor-revision");
    if (el) el.textContent = "revision: " + (currentRevision != null ? currentRevision : "-");
  }

  function showConflictNote() {
    var note = document.getElementById("mke-conflict-note");
    if (note) note.style.display = "";
    var saveBtn = document.getElementById("mke-save-btn");
    if (saveBtn) saveBtn.disabled = true;
  }

  function hideConflictNote() {
    var note = document.getElementById("mke-conflict-note");
    if (note) note.style.display = "none";
    var saveBtn = document.getElementById("mke-save-btn");
    if (saveBtn) saveBtn.disabled = false;
  }

  // ---------------------------------------------------------------------
  // データ取得
  // ---------------------------------------------------------------------

  function loadAll() {
    apiFetch("/admin/help-kb/drafts").then(function (res) {
      if (!res.ok) throw new Error("status " + res.status);
      return res.json();
    }).then(function (data) {
      var st = data.state || {};
      servingSource = st.serving_source || "files";
      activeVersionNo = (typeof st.active_version_no === "number") ? st.active_version_no : null;
      renderServingBadge();
      renderDraftList(data.drafts || []);
    }).catch(function () {
      setStatus("draft 一覧の読み込みに失敗しました", "error");
    });

    apiFetch("/admin/help-kb/versions").then(function (res) {
      if (!res.ok) throw new Error("status " + res.status);
      return res.json();
    }).then(function (data) {
      var versions = Array.isArray(data) ? data : (data.versions || []);
      renderVersions(versions);
      renderServingBadge();
    }).catch(function () {
      var el = document.getElementById("mke-versions-list");
      if (el) el.innerHTML = '<div style="color:var(--color-text-danger)">版履歴の読み込みに失敗しました</div>';
    });
  }

  function selectFile(audience, file) {
    loadDraftFile(audience, file, {});
  }

  function loadDraftFile(audience, file, opts) {
    opts = opts || {};
    var emptyEl = document.getElementById("mke-editor-empty");
    var areaEl = document.getElementById("mke-editor-area");
    var saveStatus = document.getElementById("mke-save-status");
    hideConflictNote();
    if (emptyEl) emptyEl.style.display = "none";
    if (areaEl) areaEl.style.display = "";
    if (saveStatus) { saveStatus.textContent = "読み込み中..."; saveStatus.style.color = ""; }

    _fetchJson(_draftPath(audience, file)).then(function (data) {
      currentAudience = data.audience || audience;
      currentFile = data.file || file;
      currentRevision = (typeof data.revision !== "undefined") ? data.revision : null;
      var textarea = document.getElementById("mke-editor-textarea");
      if (textarea) textarea.value = data.content || "";
      updateEditorRevisionLabel();
      var nameEl = document.getElementById("mke-editor-filename");
      if (nameEl) nameEl.textContent = (AUDIENCE_LABELS[currentAudience] || currentAudience) + " / " + currentFile;
      if (saveStatus) {
        saveStatus.textContent = opts.statusText || "";
        saveStatus.style.color = "var(--color-text-success)";
      }
      renderDraftList(lastDrafts);
    }).catch(function () {
      if (saveStatus) {
        saveStatus.textContent = "読み込みに失敗しました";
        saveStatus.style.color = "var(--color-text-danger)";
      }
    });
  }

  // ---------------------------------------------------------------------
  // 操作
  // ---------------------------------------------------------------------

  function doSave() {
    if (!currentAudience || !currentFile) return;
    var textarea = document.getElementById("mke-editor-textarea");
    if (!textarea) return;
    var saveBtn = document.getElementById("mke-save-btn");
    var saveStatus = document.getElementById("mke-save-status");
    if (saveBtn) saveBtn.disabled = true;
    if (saveStatus) { saveStatus.textContent = "保存中..."; saveStatus.style.color = ""; }

    _fetchJson(_draftPath(currentAudience, currentFile), {
      method: "PUT",
      body: JSON.stringify({ content: textarea.value, expected_revision: currentRevision })
    }).then(function (data) {
      hideConflictNote();
      if (data && typeof data.revision !== "undefined" && data.revision !== null) {
        currentRevision = data.revision;
        updateEditorRevisionLabel();
        if (saveStatus) { saveStatus.textContent = "保存しました"; saveStatus.style.color = "var(--color-text-success)"; }
        loadAll();
      } else {
        loadDraftFile(currentAudience, currentFile, { statusText: "保存しました" });
      }
    }).catch(function (err) {
      if (saveBtn) saveBtn.disabled = false;
      if (err && err.status === 409) {
        // 409 = 衝突。自動マージはしない。事実文 + 明示の再読込ボタンのみ提示する。
        showConflictNote();
        if (saveStatus) {
          saveStatus.textContent = "他の編集と衝突しました。最新を読み込み直してください。";
          saveStatus.style.color = "var(--color-text-danger)";
        }
      } else {
        if (saveStatus) { saveStatus.textContent = "保存に失敗しました"; saveStatus.style.color = "var(--color-text-danger)"; }
      }
    });
  }

  function doSeed() {
    var btn = document.getElementById("mke-seed-btn");
    if (btn) btn.disabled = true;
    setStatus("配信中スナップショットから draft を取り込んでいます...", "");
    _fetchJson("/admin/help-kb/drafts/seed", { method: "POST" }).then(function () {
      setStatus("draft を取り込みました（既存ファイルの draft は上書きしません。冪等シードです）。", "success");
      loadAll();
    }).catch(function () {
      setStatus("取込に失敗しました", "error");
    }).then(function () {
      if (btn) btn.disabled = false;
    });
  }

  function doFreeze() {
    var nextGuess = _guessNextVersionNo();
    var msg = "現在の draft を凍結版 v" + nextGuess + " として作成し、配信ソースを DB に切り替えます。" +
      "以後、リポジトリの docs 更新は配信に反映されません（ファイル配信に戻すまで）。";
    _confirmDanger({
      title: "凍結して配信",
      message: msg,
      confirmLabel: "凍結して配信する"
    }, function () {
      setStatus("凍結処理を実行しています...", "");
      _fetchJson("/admin/help-kb/freeze", { method: "POST" }).then(function (data) {
        var vno = data && (typeof data.version_no !== "undefined") ? data.version_no : nextGuess;
        setStatus("凍結版 v" + vno + " を作成し、配信ソースを DB に切り替えました。", "success");
        loadAll();
      }).catch(function (err) {
        if (err && err.status === 422 && err.body && err.body.detail && err.body.detail.violations) {
          renderViolations(err.body.detail.violations);
        } else {
          setStatus("凍結に失敗しました", "error");
        }
      });
    });
  }

  function doSwitchSource(target) {
    var msg;
    if (target === "files") {
      msg = "配信ソースをファイル同梱版に切り替えます。以後、DBの凍結版ではなくリポジトリ同梱の docs " +
        "から配信されます（既存の凍結版データ自体は削除されません）。";
    } else {
      msg = "配信ソースをDB凍結版（現在の active version）に切り替えます。以後、リポジトリの docs " +
        "更新は配信に反映されません（ファイル配信に戻すまで）。";
    }
    _confirmDanger({
      title: "配信ソースの切替",
      message: msg,
      confirmLabel: "切り替える"
    }, function () {
      setStatus("配信ソースを切り替えています...", "");
      _fetchJson("/admin/help-kb/serving-source", {
        method: "POST",
        body: JSON.stringify({ source: target })
      }).then(function () {
        setStatus("配信ソースを切り替えました。", "success");
        loadAll();
      }).catch(function () {
        setStatus("切替に失敗しました", "error");
      });
    });
  }

  // ---------------------------------------------------------------------
  // タブ構築
  // ---------------------------------------------------------------------

  function buildTab() {
    var panel = document.getElementById("tab-manual-editor");
    if (!panel || panel.getAttribute("data-mke-built") === "1") return;
    panel.setAttribute("data-mke-built", "1");

    panel.innerHTML =
      '<div class="admin-section">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px">' +
          '<h3 class="admin-section-title" style="margin-bottom:0">マニュアル編集（help_kb draft/freeze）</h3>' +
          '<button id="mke-refresh-btn" class="admin-action-btn">更新</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin-bottom:10px">' +
          'ここでの編集は draft（下書き）です。「凍結して配信」を実行するまで、学生・教員向けの' +
          'ヘルプ応答には反映されません。draft は audience（student / teacher / system_admin）ごとに' +
          '独立しています。' +
        '</p>' +
        '<div id="mke-serving-badge" style="margin-bottom:10px"></div>' +
        '<div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">' +
          '<button id="mke-seed-btn" class="admin-action-btn">ファイルから取込</button>' +
          '<button id="mke-freeze-btn" class="admin-action-btn">凍結して配信</button>' +
          '<button id="mke-switch-files-btn" class="admin-action-btn" style="display:none">ファイル配信に戻す</button>' +
          '<button id="mke-switch-db-btn" class="admin-action-btn" style="display:none">DB配信に切替</button>' +
        '</div>' +
        '<div id="mke-status" style="font-size:12px;margin-bottom:10px"></div>' +
        '<div style="display:flex;gap:16px;flex-wrap:wrap">' +
          '<div style="flex:0 0 260px;min-width:220px">' +
            '<div id="mke-draft-list"></div>' +
            '<h4 style="margin:16px 0 6px;font-size:13px">版履歴</h4>' +
            '<div id="mke-versions-list" style="font-size:12px;color:var(--color-text-secondary)"></div>' +
          '</div>' +
          '<div style="flex:1;min-width:320px">' +
            '<div id="mke-editor-empty" style="color:var(--color-text-tertiary);font-size:13px">' +
              '左の一覧からファイルを選んでください。' +
            '</div>' +
            '<div id="mke-editor-area" style="display:none">' +
              '<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;gap:8px;flex-wrap:wrap">' +
                '<strong id="mke-editor-filename" style="font-size:13px"></strong>' +
                '<span id="mke-editor-revision" style="font-size:11px;color:var(--color-text-tertiary)"></span>' +
              '</div>' +
              '<textarea id="mke-editor-textarea" class="mke-editor-textarea" spellcheck="false"></textarea>' +
              '<div id="mke-conflict-note" class="mke-conflict-note" style="display:none">' +
                '他の編集と衝突しました。最新を読み込み直してください。' +
                '<button id="mke-reload-btn" class="admin-action-btn" style="margin-left:8px">再読込</button>' +
              '</div>' +
              '<div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
                '<button id="mke-save-btn" class="admin-action-btn">保存</button>' +
                '<span id="mke-save-status" style="font-size:12px"></span>' +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';

    var refreshBtn = document.getElementById("mke-refresh-btn");
    if (refreshBtn) refreshBtn.addEventListener("click", loadAll);
    var seedBtn = document.getElementById("mke-seed-btn");
    if (seedBtn) seedBtn.addEventListener("click", doSeed);
    var freezeBtn = document.getElementById("mke-freeze-btn");
    if (freezeBtn) freezeBtn.addEventListener("click", doFreeze);
    var toFilesBtn = document.getElementById("mke-switch-files-btn");
    if (toFilesBtn) toFilesBtn.addEventListener("click", function () { doSwitchSource("files"); });
    var toDbBtn = document.getElementById("mke-switch-db-btn");
    if (toDbBtn) toDbBtn.addEventListener("click", function () { doSwitchSource("db"); });
    var saveBtn = document.getElementById("mke-save-btn");
    if (saveBtn) saveBtn.addEventListener("click", doSave);
    var reloadBtn = document.getElementById("mke-reload-btn");
    if (reloadBtn) reloadBtn.addEventListener("click", function () {
      if (currentAudience && currentFile) loadDraftFile(currentAudience, currentFile);
    });
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
      if (deps.onTabActivate) deps.onTabActivate("manual-editor", loadAll);
    }
    initialized = true;
  }

  window.ManualKbEditor = { init: init };
})();
