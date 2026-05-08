/* ===================================================================
   Episteme Graph — Admin UI Application Logic
   =================================================================== */

(function () {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────
  var state = {
    token: localStorage.getItem("eg_token") || null,
    username: localStorage.getItem("eg_username") || null,
    role: null,
    chatHistory: [],
    chatMessages: [],
    courseDraft: null,
    sending: false,
    currentSessionId: null,
    importedFromCourseId: null,
    availableMaterials: [],
    selectedMaterialIds: [],
    errorLogs: [],
    selectedErrorLogIds: new Set(),
    lastSelectedErrorLogIndex: null,
  };

  var API = "/api";

  function parseJwtPayload(token) {
    try {
      var parts = token.split(".");
      if (parts.length !== 3) return null;
      var payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(payload));
    } catch (e) { return null; }
  }

  if (state.token) {
    var decoded = parseJwtPayload(state.token);
    if (decoded) state.role = decoded.role || "STUDENT";
  }

  // ── API helpers ────────────────────────────────────────────────────
  function apiFetch(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (!opts._noJson) headers["Content-Type"] = "application/json";
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    opts.headers = headers;
    return fetch(API + path, opts).then(function (res) {
      if (res.status === 401) {
        state.token = null;
        localStorage.removeItem("eg_token");
        renderAuth();
        throw new Error("Unauthorized");
      }
      return res;
    });
  }

  function apiFetchRaw(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    opts.headers = headers;
    return fetch(API + path, opts).then(function (res) {
      if (res.status === 401) {
        state.token = null;
        localStorage.removeItem("eg_token");
        renderAuth();
        throw new Error("Unauthorized");
      }
      return res;
    });
  }

  // ── Auth ───────────────────────────────────────────────────────────
  function renderAuth() {
    var overlay = document.getElementById("auth-overlay");
    if (state.token) {
      if (overlay) overlay.remove();
      return;
    }
    if (overlay) return;

    overlay = document.createElement("div");
    overlay.id = "auth-overlay";
    overlay.className = "auth-overlay";
    overlay.innerHTML =
      '<div class="auth-box">' +
        '<h2>Episteme Graph — 管理画面</h2>' +
        '<form id="auth-form">' +
          '<input id="auth-user" type="text" placeholder="ユーザー名" required autocomplete="username">' +
          '<input id="auth-pass" type="password" placeholder="パスワード" required autocomplete="current-password">' +
          '<button type="submit" id="auth-btn">ログイン</button>' +
        '</form>' +
        '<div class="auth-toggle" id="auth-toggle">' +
          'アカウントがない場合 <a id="auth-switch">新規登録</a>' +
        '</div>' +
        '<div class="auth-error" id="auth-error"></div>' +
      '</div>';
    document.body.appendChild(overlay);

    var isLogin = true;
    document.getElementById("auth-switch").addEventListener("click", function handleSwitch() {
      isLogin = !isLogin;
      document.getElementById("auth-btn").textContent = isLogin ? "ログイン" : "登録";
      document.getElementById("auth-toggle").innerHTML = isLogin
        ? 'アカウントがない場合 <a id="auth-switch">新規登録</a>'
        : '既にアカウントがある場合 <a id="auth-switch">ログイン</a>';
      document.getElementById("auth-switch").addEventListener("click", handleSwitch);
    });

    document.getElementById("auth-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var username = document.getElementById("auth-user").value.trim();
      var password = document.getElementById("auth-pass").value;
      var errEl = document.getElementById("auth-error");
      errEl.textContent = "";

      var endpoint = isLogin ? "/auth/login" : "/auth/register";
      var payload = isLogin
        ? { username: username, password: password }
        : { username: username, password: password, email: username + "@learning.local" };

      fetch(API + endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (res) {
          if (!res.ok) return res.json().catch(function () { return {}; }).then(function (d) { throw d; });
          return res.json();
        })
        .then(function (data) {
          state.token = data.access_token;
          state.username = username;
          var decoded = parseJwtPayload(data.access_token);
          state.role = decoded ? (decoded.role || "STUDENT") : "STUDENT";
          localStorage.setItem("eg_token", data.access_token);
          localStorage.setItem("eg_username", username);
          overlay.remove();
          initApp();
        })
        .catch(function (err) {
          errEl.textContent = err.detail || "認証に失敗しました";
        });
    });
  }

  // ── Utilities ──────────────────────────────────────────────────────
  function escHtml(s) {
    if (s === null || s === undefined || s === "") return "";
    s = String(s);
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function formatDateTime(value) {
    if (!value) return "";
    try {
      var d = new Date(value);
      return d.getFullYear() + "/" + (d.getMonth() + 1) + "/" + d.getDate() + " " +
        String(d.getHours()).padStart(2, "0") + ":" +
        String(d.getMinutes()).padStart(2, "0") + ":" +
        String(d.getSeconds()).padStart(2, "0");
    } catch (e) {
      return value;
    }
  }

  // ── Tab Switching ──────────────────────────────────────────────────
  // Callbacks fired when a tab becomes active (keyed by data-tab value)
  var _tabActivateCallbacks = {};

  function onTabActivate(tabName, fn) {
    if (!_tabActivateCallbacks[tabName]) _tabActivateCallbacks[tabName] = [];
    _tabActivateCallbacks[tabName].push(fn);
  }

  function initTabs() {
    document.getElementById("adminTabs").addEventListener("click", function (e) {
      var btn = e.target.closest(".admin-tab");
      if (!btn || !btn.dataset.tab) return;
      if (btn.style.display === "none") return;
      this.querySelectorAll(".admin-tab").forEach(function (b) { b.classList.remove("on"); });
      btn.classList.add("on");
      document.querySelectorAll(".admin-panel").forEach(function (p) { p.classList.remove("vis"); });
      var target = document.getElementById("tab-" + btn.dataset.tab);
      if (target) target.classList.add("vis");

      // Fire tab activation callbacks
      var cbs = _tabActivateCallbacks[btn.dataset.tab];
      if (cbs) {
        cbs.forEach(function (fn) { fn(); });
      }
    });
  }

  function activateTabView(tabName) {
    document.querySelectorAll(".admin-tab").forEach(function (b) {
      b.classList.toggle("on", b.dataset.tab === tabName);
    });
    document.querySelectorAll(".admin-panel").forEach(function (p) { p.classList.remove("vis"); });
    var target = document.getElementById("tab-" + tabName);
    if (target) target.classList.add("vis");
  }

  // ── Materials Management ──────────────────────────────────────────
  function loadMaterials() {
    apiFetch("/admin/materials")
      .then(function (res) { return res.json(); })
      .then(function (materials) {
        renderMaterials(materials);
      })
      .catch(function () {
        var tbody = document.getElementById("materials-tbody");
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-danger)">読み込みに失敗しました</td></tr>';
      });
  }

  function renderMaterials(materials) {
    var tbody = document.getElementById("materials-tbody");
    if (!materials || materials.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--color-text-tertiary)">教材がまだアップロードされていません</td></tr>';
      return;
    }

    var html = "";
    materials.forEach(function (m) {
      var statusClass = "status-" + m.status;
      var statusLabel = {
        uploaded: "アップロード済み",
        processing: "処理中...",
        completed: "完了",
        failed: "失敗",
      }[m.status] || m.status;
      if (m.status === "processing" && m.analysis_stage) {
        var progressText = "";
        if (typeof m.analysis_progress === "number") {
          progressText = " " + m.analysis_progress + "%";
        } else if (typeof m.analysis_processed === "number" && typeof m.analysis_total === "number") {
          progressText = " " + m.analysis_processed + "/" + m.analysis_total;
        }
        statusLabel = "処理中: " + m.analysis_stage + progressText;
      }
      if (m.status === "failed" && m.analysis_stage) {
        statusLabel = "失敗: " + m.analysis_stage;
      }

      var uploadedAt = m.uploaded_at || "";
      if (uploadedAt) {
        try {
          var dt = new Date(uploadedAt);
          uploadedAt = dt.getFullYear() + "/" + (dt.getMonth() + 1) + "/" + dt.getDate() + " " +
            dt.getHours() + ":" + String(dt.getMinutes()).padStart(2, "0");
        } catch (e) { /* keep original */ }
      }

      html += "<tr>";
      html += "<td>" + escHtml(m.filename) + "</td>";
      html += "<td>" + escHtml(m.title) + "</td>";
      html += '<td><span class="admin-status ' + statusClass + '">' + statusLabel + "</span></td>";
      html += "<td>" + escHtml(uploadedAt) + "</td>";
      var hasPdf = m.has_pdf === true;
      var chunkCount = typeof m.chunk_count === "number" ? m.chunk_count : null;
      var pdfRegistrationFailed = m.status === "failed" || (m.status === "completed" && chunkCount === 0);
      var pdfBtnLabel = pdfRegistrationFailed ? "失敗 (PDF再登録)" : (hasPdf ? "登録済" : "PDF再登録");
      var pdfBtnClass = pdfRegistrationFailed ? " admin-pdf-reupload-btn-failed" : "";
      var pdfBtnTitle = pdfRegistrationFailed
        ? "教材処理に失敗、またはチャンクが作成されませんでした。PDFを再登録してください"
        : "PDFのみ再登録";
      var resumeBtn = "";
      if ((m.status === "processing" || m.status === "failed") && m.document_id) {
        resumeBtn = '<button class="admin-resume-analysis-btn" data-document-id="' + escHtml(m.document_id) + '" data-filename="' + escHtml(m.filename || m.title || "教材") + '" title="保存済みPDFから解析を再開">解析再開</button>';
      }
      html += '<td style="display:flex;gap:6px">' +
        '<button class="admin-pdf-reupload-btn' + pdfBtnClass + '" data-material-id="' + escHtml(m.material_id) + '" title="' + escHtml(pdfBtnTitle) + '">' + pdfBtnLabel + '</button>' +
        resumeBtn +
        '<button class="admin-delete-btn" data-material-id="' + escHtml(m.material_id) + '" data-material-title="' + escHtml(m.title) + '" style="background:none;border:1px solid var(--color-text-danger);color:var(--color-text-danger);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:12px">削除</button>' +
        '</td>';
      html += "</tr>";
    });
    tbody.innerHTML = html;

    // Attach delete handlers
    tbody.querySelectorAll(".admin-delete-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mid = this.getAttribute("data-material-id");
        var title = this.getAttribute("data-material-title");
        openDeleteConfirmModal("material", mid, title);
      });
    });

    // Attach PDF re-upload handlers
    tbody.querySelectorAll(".admin-pdf-reupload-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mid = this.getAttribute("data-material-id");
        var input = document.createElement("input");
        input.type = "file";
        input.accept = ".pdf,application/pdf";
        input.onchange = function () {
          var file = input.files[0];
          if (!file) return;
          var formData = new FormData();
          formData.append("file", file);
          btn.disabled = true;
          btn.textContent = "登録中...";
          apiFetch("/admin/materials/" + mid + "/pdf", { method: "PUT", body: formData, _noJson: true })
            .then(function (res) {
              if (res.status === 409) {
                return res.json().then(function (body) {
                  throw { mismatch: true, message: body.detail || "PDFが一致しません" };
                });
              }
              if (!res.ok) throw { message: "status " + res.status };
              btn.textContent = "完了";
              btn.classList.remove("admin-pdf-reupload-btn-failed");
              setTimeout(function () { btn.textContent = "PDF再登録"; btn.disabled = false; }, 2000);
            })
            .catch(function (err) {
              btn.textContent = "失敗";
              btn.classList.add("admin-pdf-reupload-btn-failed");
              btn.disabled = false;
              if (err && err.mismatch) {
                alert("⚠ " + err.message);
              } else {
                alert("PDF再登録に失敗しました: " + (err.message || err));
              }
            });
        };
        input.click();
      });
    });

    tbody.querySelectorAll(".admin-resume-analysis-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var docId = this.getAttribute("data-document-id");
        var filename = this.getAttribute("data-filename") || "教材";
        if (!docId) return;
        btn.disabled = true;
        btn.textContent = "再開中...";
        apiFetch("/admin/documents/" + docId + "/reanalyze", { method: "POST" })
          .then(function (res) {
            if (!res.ok) throw new Error("status " + res.status);
            return res.json();
          })
          .then(function (data) {
            btn.textContent = "処理中";
            if (data.task_id) startTaskPolling(data.task_id, filename);
            loadMaterials();
          })
          .catch(function () {
            btn.disabled = false;
            btn.textContent = "解析再開";
            showUploadStatus("解析の再開に失敗しました。", "error");
          });
      });
    });
  }

  // ── Delete Confirmation Modal ──────────────────────────────────────
  function openDeleteConfirmModal(kind, id, name) {
    // kind: "material" | "course"
    var existing = document.getElementById("delete-confirm-modal");
    if (existing) existing.remove();

    var kindLabel = kind === "material" ? "教材" : "コース";
    var warningText = kind === "material"
      ? "この教材を削除すると、紐づくコースも同時に削除されます。"
      : "このコースを削除すると、関連する学習履歴も削除されます。";

    var overlay = document.createElement("div");
    overlay.id = "delete-confirm-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";

    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border-secondary);border-radius:8px;padding:24px;min-width:400px;max-width:500px">' +
        '<h3 style="margin:0 0 12px;font-size:16px;color:var(--color-text-danger)">' + kindLabel + 'の削除</h3>' +
        '<p style="font-size:13px;color:var(--color-text-primary);margin:0 0 8px">' +
          '以下の' + kindLabel + 'を削除しようとしています:' +
        '</p>' +
        '<p style="font-size:14px;font-weight:600;color:var(--color-text-primary);margin:0 0 12px;padding:8px;background:var(--color-bg-secondary);border-radius:4px">' +
          escHtml(name) +
        '</p>' +
        '<p style="font-size:12px;color:var(--color-text-danger);margin:0 0 12px">' + warningText + '</p>' +
        '<p style="font-size:13px;color:var(--color-text-secondary);margin:0 0 8px">' +
          '削除を確認するには、' + kindLabel + '名を正確に入力してください:' +
        '</p>' +
        '<input type="text" id="delete-confirm-input" placeholder="' + escHtml(name) + '" style="width:100%;padding:8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary);box-sizing:border-box;margin-bottom:16px">' +
        '<div id="delete-confirm-error" style="display:none;color:var(--color-text-danger);font-size:12px;margin-bottom:8px"></div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button id="delete-cancel-btn" style="padding:6px 16px;border:1px solid var(--color-border);border-radius:4px;background:none;color:var(--color-text-secondary);cursor:pointer;font-size:13px">キャンセル</button>' +
          '<button id="delete-exec-btn" style="padding:6px 16px;border:none;border-radius:4px;background:var(--color-text-danger);color:#fff;cursor:pointer;font-size:13px" disabled>削除する</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    var input = document.getElementById("delete-confirm-input");
    var execBtn = document.getElementById("delete-exec-btn");
    var errorEl = document.getElementById("delete-confirm-error");

    // Enable button only when name matches
    input.addEventListener("input", function () {
      execBtn.disabled = (input.value !== name);
    });

    // Cancel
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    document.getElementById("delete-cancel-btn").addEventListener("click", function () {
      overlay.remove();
    });

    // Execute delete
    execBtn.addEventListener("click", function () {
      execBtn.disabled = true;
      execBtn.textContent = "削除中...";
      errorEl.style.display = "none";

      var endpoint = kind === "material"
        ? "/admin/materials/" + id
        : "/admin/courses/" + id;

      apiFetch(endpoint, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm_name: input.value }),
      })
        .then(function (res) {
          if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "削除に失敗しました"); });
          return res.json();
        })
        .then(function (data) {
          overlay.remove();
          if (kind === "material") {
            var msg = "教材「" + name + "」を削除しました。";
            if (data.deleted_courses && data.deleted_courses.length > 0) {
              msg += " 紐づく " + data.deleted_courses.length + " 件のコースも削除されました。";
            }
            showUploadStatus(msg, "success");
            loadMaterials();
          } else {
            showUploadStatus("コース「" + name + "」を削除しました。", "success");
          }
        })
        .catch(function (err) {
          execBtn.disabled = false;
          execBtn.textContent = "削除する";
          errorEl.textContent = err.message || "削除に失敗しました";
          errorEl.style.display = "block";
        });
    });

    // Focus input
    input.focus();
  }

  // ── File Upload ────────────────────────────────────────────────────
  function initUpload() {
    var zone = document.getElementById("upload-zone");
    var fileInput = document.getElementById("file-input");

    // Drag & drop
    zone.addEventListener("dragover", function (e) {
      e.preventDefault();
      zone.classList.add("drag-over");
    });
    zone.addEventListener("dragleave", function () {
      zone.classList.remove("drag-over");
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.classList.remove("drag-over");
      var files = e.dataTransfer.files;
      if (files.length > 0) uploadFile(files[0]);
    });

    // File input
    fileInput.addEventListener("change", function () {
      if (this.files.length > 0) uploadFile(this.files[0]);
      this.value = "";
    });
  }

  // ── Task Polling State ──────────────────────────────────────────
  var _activePollingTimers = {};

  function uploadFile(file) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      showUploadStatus("PDFファイルのみアップロードできます。", "error");
      return;
    }

    showUploadStatus(escHtml(file.name) + " をアップロード中...", "info");
    disableUploadUI(true);

    var formData = new FormData();
    formData.append("file", file);

    apiFetchRaw("/admin/materials/upload", {
      method: "POST",
      body: formData,
    })
      .then(function (res) {
        if (res.status === 413) throw { detail: "ファイルサイズが大きすぎます（上限: 50MB）" };
        if (!res.ok) return res.json().then(function (d) { throw d; });
        return res.json();
      })
      .then(function (data) {
        showUploadStatus(
          escHtml(file.name) + " のアップロードが完了しました。グラフ構築中..." +
          '<span class="typing" style="display:inline-flex;margin-left:8px"><span></span><span></span><span></span></span>',
          "info"
        );
        loadMaterials();
        loadMaterialsForSelection();
        if (data.task_id) {
          startTaskPolling(data.task_id, file.name);
        } else {
          disableUploadUI(false);
        }
      })
      .catch(function (err) {
        showUploadStatus("アップロードに失敗しました: " + (err.detail || "不明なエラー"), "error");
        disableUploadUI(false);
      });
  }

  function disableUploadUI(disabled) {
    var zone = document.getElementById("upload-zone");
    var fileInput = document.getElementById("file-input");
    if (zone) {
      zone.style.opacity = disabled ? "0.5" : "1";
      zone.style.pointerEvents = disabled ? "none" : "auto";
    }
    if (fileInput) fileInput.disabled = disabled;
  }

  function startTaskPolling(taskId, filename) {
    var retryCount = 0;
    var maxRetries = 3;
    var intervalMs = 3000;

    function poll() {
      apiFetch("/admin/tasks/" + taskId)
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0; // reset on success

          if (task.status === "completed") {
            stopTaskPolling(taskId);
            showUploadStatus(
              escHtml(filename) + " のグラフ構築が完了しました。",
              "success"
            );
            disableUploadUI(false);
            loadMaterials();
            loadMaterialsForSelection();
          } else if (task.status === "failed") {
            stopTaskPolling(taskId);
            var errMsg = task.error_message || "不明なエラー";
            showUploadStatus(
              escHtml(filename) + " の処理に失敗しました: " + escHtml(errMsg),
              "error"
            );
            disableUploadUI(false);
            loadMaterials();
          }
          // pending/processing: continue polling
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            stopTaskPolling(taskId);
            showUploadStatus(
              escHtml(filename) + " の進捗確認に失敗しました。「更新」ボタンで教材一覧を確認してください。",
              "error"
            );
            disableUploadUI(false);
            loadMaterials();
          }
          // else: retry on next interval
        });
    }

    _activePollingTimers[taskId] = setInterval(poll, intervalMs);
    // Initial poll immediately
    poll();
  }

  function stopTaskPolling(taskId) {
    if (_activePollingTimers[taskId]) {
      clearInterval(_activePollingTimers[taskId]);
      delete _activePollingTimers[taskId];
    }
  }

  function showUploadStatus(message, type) {
    var el = document.getElementById("upload-status");
    el.innerHTML = message;
    el.className = "upload-status upload-status-" + type;
  }

  // ── Course Builder Chat ────────────────────────────────────────────
  function initCourseBuilder() {
    var input = document.getElementById("cb-chat-input");
    var btn = document.getElementById("cb-send-btn");

    btn.addEventListener("click", function () {
      sendCourseChat(input.value.trim());
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        sendCourseChat(input.value.trim());
      }
    });

    document.getElementById("cb-approve-btn").addEventListener("click", function () {
      approveCourse();
    });

    // A1: セッション一覧をロード
    loadSessions();
    // Issue #72: 利用可能な教材一覧をロード
    loadMaterialsForSelection();
  }

  // ── Material Selection (Issue #72) ────────────────────────────────
  function loadMaterialsForSelection() {
    apiFetch("/admin/materials")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var materials = Array.isArray(data) ? data : (data.materials || []);
        state.availableMaterials = materials.filter(function (m) {
          return m.status === "completed";
        });
        renderMaterialCheckboxes();
      })
      .catch(function () {
        var listEl = document.getElementById("cb-material-select-list");
        if (listEl) listEl.innerHTML = '<span style="color:var(--color-text-tertiary);font-size:12px">教材の読み込みに失敗しました</span>';
      });
  }

  function renderMaterialCheckboxes() {
    var listEl = document.getElementById("cb-material-select-list");
    if (!listEl) return;

    if (!state.availableMaterials || state.availableMaterials.length === 0) {
      listEl.innerHTML = '<span style="color:var(--color-text-tertiary);font-size:12px">利用可能な教材がありません</span>';
      return;
    }

    var html = "";
    state.availableMaterials.forEach(function (m) {
      var mid = escHtml(m.material_id || m.id || "");
      var title = escHtml(m.title || m.filename || "不明な教材");
      var checked = state.selectedMaterialIds.indexOf(m.material_id || m.id || "") !== -1;
      html += '<label class="cb-material-checkbox' + (checked ? " selected" : "") + '" data-mid="' + mid + '">';
      html += '<input type="checkbox" value="' + mid + '"' + (checked ? " checked" : "") + '>';
      html += title;
      html += "</label>";
    });
    listEl.innerHTML = html;

    listEl.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
      cb.addEventListener("change", function () {
        var mid = this.value;
        var label = this.parentElement;
        if (this.checked) {
          if (state.selectedMaterialIds.indexOf(mid) === -1) {
            state.selectedMaterialIds.push(mid);
          }
          label.classList.add("selected");
        } else {
          state.selectedMaterialIds = state.selectedMaterialIds.filter(function (id) { return id !== mid; });
          label.classList.remove("selected");
        }
      });
    });
  }

  // ── Session Management ─────────────────────────────────────────────
  function loadSessions() {
    apiFetch("/admin/course-builder/sessions")
      .then(function (res) { return res.json(); })
      .then(function (sessions) {
        if (!sessions || sessions.length === 0) {
          createNewSession();
        } else {
          renderSessionBar(sessions);
          selectSession(sessions[0].session_id);
        }
      })
      .catch(function () {
        var bar = document.getElementById("cb-session-bar");
        if (bar) bar.style.display = "none";
      });
  }

  function renderSessionBar(sessions) {
    var bar = document.getElementById("cb-session-bar");
    if (!bar) return;

    var selectHtml = '<select id="session-select" style="flex:1;padding:4px 6px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-primary);font-size:12px">';
    sessions.forEach(function (s) {
      selectHtml += '<option value="' + escHtml(s.session_id) + '">' + escHtml(s.title) + "</option>";
    });
    selectHtml += "</select>";

    bar.innerHTML =
      '<div style="display:flex;gap:8px;align-items:center;padding:6px 12px;border-bottom:1px solid var(--color-border)">' +
      selectHtml +
      '<button id="new-session-btn" style="padding:4px 10px;font-size:12px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-primary);cursor:pointer;white-space:nowrap">+ 新規</button>' +
      '<button id="import-course-btn" style="padding:4px 10px;font-size:12px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-info);cursor:pointer;white-space:nowrap">既存コースを読込</button>' +
      "</div>";

    if (state.currentSessionId) {
      var sel = document.getElementById("session-select");
      if (sel) sel.value = state.currentSessionId;
    }

    document.getElementById("session-select").addEventListener("change", function () {
      selectSession(this.value);
    });
    document.getElementById("new-session-btn").addEventListener("click", function () {
      createNewSession();
    });
    document.getElementById("import-course-btn").addEventListener("click", function () {
      openImportCourseModal();
    });
  }

  function createNewSession() {
    apiFetch("/admin/course-builder/sessions", {
      method: "POST",
      body: JSON.stringify({ title: "新しいセッション" }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.currentSessionId = data.session_id;
        state.chatHistory = [];
        state.chatMessages = [];
        state.courseDraft = null;
        state.importedFromCourseId = null;
        renderCourseChat();
        renderCoursePreview();
        reloadSessionBar(data.session_id);
      })
      .catch(function () {
        // セッション作成失敗時は session_id なしで動作継続
      });
  }

  function reloadSessionBar(selectId) {
    apiFetch("/admin/course-builder/sessions")
      .then(function (res) { return res.json(); })
      .then(function (sessions) {
        renderSessionBar(sessions);
        if (selectId) {
          var sel = document.getElementById("session-select");
          if (sel) sel.value = selectId;
          state.currentSessionId = selectId;
        }
      })
      .catch(function () {});
  }

  function selectSession(sessionId) {
    apiFetch("/admin/course-builder/sessions/" + sessionId)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.currentSessionId = data.session_id;
        state.chatHistory = data.history || [];
        state.chatMessages = data.history || [];
        state.courseDraft = data.course_draft || null;
        state.importedFromCourseId = null;
        renderCourseChat();
        renderCoursePreview();
        var sel = document.getElementById("session-select");
        if (sel) sel.value = sessionId;
      })
      .catch(function () {});
  }

  function sendCourseChat(text) {
    if (!text || state.sending) return;

    state.chatMessages.push({ role: "user", content: text });
    state.sending = true;
    renderCourseChat();

    document.getElementById("cb-chat-input").value = "";

    // Issue #50: インポートされたコースの場合、AIに現在の構成を伝えるためにコンテキストを付与
    var chatHistory = state.chatHistory;
    if (state.courseDraft && chatHistory.length <= 1) {
      var draftContext = {
        role: "user",
        content: "【現在のコース構成（JSON）】\n```json\n" + JSON.stringify(state.courseDraft, null, 2) + "\n```\nこの構成をベースに再編集を行います。",
      };
      var draftAck = {
        role: "assistant",
        content: "現在のコース構成を確認しました。どのように変更・アップデートしますか？",
      };
      chatHistory = [draftContext, draftAck].concat(chatHistory);
    }

    apiFetch("/admin/course-builder/chat", {
      method: "POST",
      body: JSON.stringify({
        message: text,
        history: chatHistory,
        session_id: state.currentSessionId || null,
        selected_material_ids: state.selectedMaterialIds,
      }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Chat failed");
        return res.json();
      })
      .then(function (data) {
        var parsed = extractCourseDraftFromAnswer(data.answer);
        var assistantAnswer = parsed.answer;
        var courseDraft = data.course_draft || parsed.courseDraft;

        state.chatMessages.push({ role: "assistant", content: assistantAnswer });
        state.chatHistory.push({ role: "user", content: text });
        state.chatHistory.push({ role: "assistant", content: assistantAnswer });

        if (courseDraft) {
          state.courseDraft = courseDraft;
          renderCoursePreview();
        }
      })
      .catch(function () {
        state.chatMessages.push({ role: "assistant", content: "エラーが発生しました。もう一度お試しください。" });
      })
      .finally(function () {
        state.sending = false;
        renderCourseChat();
      });
  }

  function extractCourseDraftFromAnswer(answer) {
    var text = answer || "";
    var markerRe = /(?:-{3,}\s*)?COURSE_DRAFT_JSON(?:\s*-{3,})?\s*:?\s*/i;
    var marker = markerRe.exec(text);
    if (!marker) {
      return { answer: text, courseDraft: null };
    }

    var cleanAnswer = text.slice(0, marker.index).trim();
    var jsonText = text.slice(marker.index + marker[0].length).trim();

    if (jsonText.indexOf("```") === 0) {
      var firstNewline = jsonText.indexOf("\n");
      jsonText = firstNewline >= 0 ? jsonText.slice(firstNewline + 1) : jsonText.slice(3);
      var fenceEnd = jsonText.indexOf("```");
      if (fenceEnd >= 0) jsonText = jsonText.slice(0, fenceEnd);
    }

    jsonText = jsonText.trim();
    if (jsonText.slice(0, 4).toLowerCase() === "json") {
      jsonText = jsonText.slice(4).trim();
    }

    try {
      return { answer: cleanAnswer, courseDraft: JSON.parse(jsonText) };
    } catch (err) {
      return { answer: cleanAnswer, courseDraft: null };
    }
  }

  function renderCourseChat() {
    var area = document.getElementById("cb-chat-area");
    var html = "";

    state.chatMessages.forEach(function (msg) {
      if (msg.role === "user") {
        html += '<div class="mg usr">' + escHtml(msg.content) + "</div>";
      } else {
        html += '<div class="mg ai">' + renderSimpleMarkdown(msg.content) + "</div>";
      }
    });

    if (state.sending) {
      html += '<div class="mg ai"><div class="typing"><span></span><span></span><span></span></div></div>';
    }

    area.innerHTML = html;
    area.scrollTop = area.scrollHeight;
  }

  function renderSimpleMarkdown(text) {
    var html = escHtml(text);
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.split("\n\n").map(function (p) { return "<p>" + p + "</p>"; }).join("");
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  // ── Course Preview ─────────────────────────────────────────────────
  function renderCoursePreview() {
    var area = document.getElementById("cb-preview-area");
    var approveArea = document.getElementById("cb-approve-area");
    var draft = state.courseDraft;

    if (!draft) {
      area.innerHTML = '<div style="color:var(--color-text-tertiary);font-size:13px;padding:20px">AIとの対話でコース構成が生成されると、ここにプレビューが表示されます。</div>';
      approveArea.style.display = "none";
      return;
    }

    var html = "";

    // Title
    html += '<div class="cb-draft-title">' + escHtml(draft.title || "無題のコース") + "</div>";

    // Metadata
    if (draft.target_audience) {
      html += '<div class="cb-draft-meta"><span class="cb-meta-label">対象:</span> ' + escHtml(draft.target_audience) + "</div>";
    }
    if (draft.goal) {
      html += '<div class="cb-draft-meta"><span class="cb-meta-label">到達目標:</span> ' + escHtml(draft.goal) + "</div>";
    }
    if (draft.prerequisites && draft.prerequisites.length > 0) {
      html += '<div class="cb-draft-meta"><span class="cb-meta-label">前提知識:</span> ' + draft.prerequisites.map(escHtml).join(", ") + "</div>";
    }

    // Chapters tree
    if (draft.chapters && draft.chapters.length > 0) {
      html += '<div class="cb-tree-section"><div class="cb-tree-header">章構成</div>';
      draft.chapters.forEach(function (ch, ci) {
        html += '<div class="cb-tree-chapter">';
        html += '<span class="cb-tree-num">' + (ci + 1) + "</span>";
        html += '<span class="cb-tree-name">' + escHtml(ch.title) + "</span>";
        html += "</div>";

        // Topics
        if (ch.topics && ch.topics.length > 0) {
          ch.topics.forEach(function (t) {
            html += '<div class="cb-tree-topic">';
            html += escHtml(t.title || t);
            html += "</div>";
          });
        }
      });
      html += "</div>";
    }

    // Concepts
    if (draft.concepts && draft.concepts.length > 0) {
      html += '<div class="cb-tree-section"><div class="cb-tree-header">概念マップ</div>';
      draft.concepts.forEach(function (c) {
        var name = typeof c === "string" ? c : c.name;
        html += '<div class="cb-tree-concept">' + escHtml(name) + "</div>";
      });
      html += "</div>";
    }

    // Sources
    if (draft.sources && draft.sources.length > 0) {
      html += '<div class="cb-tree-section"><div class="cb-tree-header">使用教材</div>';
      draft.sources.forEach(function (s) {
        var title = typeof s === "string" ? s : s.title;
        html += '<div class="cb-tree-source">' + escHtml(title) + "</div>";
      });
      html += "</div>";
    }

    area.innerHTML = html;
    approveArea.style.display = "block";
  }

  // ── Course Approval ────────────────────────────────────────────────
  function approveCourse() {
    if (!state.courseDraft) return;

    var draft = state.courseDraft;
    var btn = document.getElementById("cb-approve-btn");
    btn.disabled = true;
    btn.textContent = "登録中...";

    // Convert draft to CourseCreateRequest format
    var courseData = {
      title: draft.title || "新規コース",
      chapters: (draft.chapters || []).map(function (ch) {
        return { title: ch.title || ch, status: "locked", progress_pct: 0 };
      }),
      topics: [],
      concepts: [],
      sources: [],
    };

    // Build topics from chapters (A3: prerequisites を draft から伝達)
    var topicIndex = 0;
    (draft.chapters || []).forEach(function (ch, ci) {
      (ch.topics || []).forEach(function (t) {
        var topicTitle = typeof t === "string" ? t : (t.title || t);
        var prereqs = [];
        if (t && t.prerequisites && Array.isArray(t.prerequisites)) {
          t.prerequisites.forEach(function (p) {
            var name = typeof p === "string" ? p : (p && p.name ? p.name : "");
            if (name) prereqs.push({ name: name, status: "not_started" });
          });
        }
        courseData.topics.push({
          id: "t" + topicIndex,
          title: topicTitle,
          chapter_index: ci,
          status: topicIndex === 0 ? "in_progress" : "locked",
          prerequisites: prereqs,
          misconceptions: [],
        });
        topicIndex++;
      });
    });

    // Build concepts
    (draft.concepts || []).forEach(function (c) {
      var name = typeof c === "string" ? c : c.name;
      courseData.concepts.push({
        name: name,
        status: "future",
        children: (c && c.children) ? c.children : [],
        expanded: false,
      });
    });

    // Build sources from UI selection to guarantee material_id binding
    courseData.sources = [];
    if (state.selectedMaterialIds && state.selectedMaterialIds.length > 0) {
      state.selectedMaterialIds.forEach(function (mid) {
        var mat = null;
        for (var i = 0; i < state.availableMaterials.length; i++) {
          var m = state.availableMaterials[i];
          if ((m.material_id || m.id) === mid) {
            mat = m;
            break;
          }
        }
        courseData.sources.push({
          title: mat ? (mat.title || mat.filename) : "",
          subtitle: "",
          license: "",
          used_section: "",
          material_id: mid,
        });
      });
    } else {
      // Fallback to draft sources if no UI selection
      (draft.sources || []).forEach(function (s) {
        if (typeof s === "string") {
          courseData.sources.push({ title: s, subtitle: "", license: "", used_section: "", material_id: "" });
        } else {
          courseData.sources.push({
            title: s.title || "",
            subtitle: s.subtitle || "",
            license: s.license || "",
            used_section: s.used_section || "",
            material_id: s.material_id || "",
          });
        }
      });
    }

    // A2: is_template: true を付与してコースを登録
    apiFetch("/learning/courses", {
      method: "POST",
      body: JSON.stringify(Object.assign({}, courseData, { is_template: true })),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Course creation failed");
        return res.json();
      })
      .then(function (data) {
        btn.textContent = "登録完了!";
        btn.style.background = "var(--color-text-success)";

        // Show success message in chat
        state.chatMessages.push({
          role: "assistant",
          content: "コース「" + (data.title || draft.title) + "」が正常に登録されました。（ID: " + data.id + "）\n\n「コース管理」タブからグループ単位で受講可／編集可の権限を設定できます。",
        });
        renderCourseChat();

        // コース本文はバックエンド側で、既存のAgent成果物とアウトラインを対応付けて作成する。
        // 教材解析のAgentパイプラインは教材アップロード後または原稿スタジオから明示実行する。
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "承認してコースを登録";
        showUploadStatus("コースの登録に失敗しました。", "error");
      });
  }

  // ── Auto Pipeline (Issue #139) ─────────────────────────────────────
  // コース登録直後に原稿→音声と、DSL→Claim→Component→Graphの解析チェーンを自動実行する。
  function kickAutoPipeline(courseId, courseTitle) {
    var pipelineMsg = {
      role: "assistant",
      content: "コース登録完了。原稿・音声生成と解析パイプラインを開始しました。（進捗: 0%）",
    };
    state.chatMessages.push(pipelineMsg);
    renderCourseChat();

    function setPipelineStatus(text) {
      pipelineMsg.content = text;
      renderCourseChat();
    }

    apiFetch("/admin/courses/" + courseId + "/lecture-scripts/generate", {
      method: "POST",
      body: JSON.stringify({ override: false, auto_audio: true }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            var msg = (errBody && errBody.detail) || "原稿生成を開始できませんでした";
            throw new Error(msg);
          }, function () {
            throw new Error("原稿生成を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        var scriptTaskId = data.task_id;
        var totalChunks = data.total_chunks || 0;
        setPipelineStatus(
          "コース「" + courseTitle + "」の原稿生成を開始しました。（0 / " + totalChunks + " チャンク）"
        );
        _pollPipelineTask(courseId, scriptTaskId, "script", totalChunks, setPipelineStatus);
      })
      .catch(function (err) {
        setPipelineStatus(
          "コース登録は完了しましたが、原稿生成の開始に失敗しました: " +
          (err.message || "不明なエラー") +
          "\n\n「Lecture Studio」タブから手動で実行してください。"
        );
      });

    apiFetch("/admin/courses/" + courseId + "/document-pipeline/run", {
      method: "POST",
      body: JSON.stringify({}),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            throw new Error((errBody && errBody.detail) || "Agentパイプラインを開始できませんでした");
          }, function () {
            throw new Error("Agentパイプラインを開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        _pollAnalysisPipelineTask(data.task_id, setPipelineStatus);
      })
      .catch(function (err) {
        setPipelineStatus(
          "コース登録は完了しましたが、Agentパイプラインの開始に失敗しました: " +
          (err.message || "不明なエラー") +
          "\n\n原稿スタジオから手動でパイプライン全実行を実行してください。"
        );
      });
  }

  function _pollAnalysisPipelineTask(taskId, setStatus) {
    var retryCount = 0;
    var maxRetries = 5;
    var intervalMs = 3000;
    var labels = { document_pipeline: "Agent Pipeline", course_content: "コース本文生成", completed: "完了" };
    function poll() {
      apiFetch("/admin/tasks/" + taskId)
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0;
          var rd = task.result_data || {};
          var stage = rd.stage || "structure";
          var progress = rd.progress || 0;
          if (task.status === "completed") {
            clearInterval(timer);
            setStatus("Agentパイプラインが完了しました。構造化情報をコース本文へ反映済みです。");
          } else if (task.status === "failed") {
            clearInterval(timer);
            setStatus("Agentパイプラインに失敗しました: " + (task.error_message || "不明なエラー"));
          } else {
            setStatus("Agentパイプライン実行中: " + (rd.label || labels[stage] || stage) + " — " + progress + "%");
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            clearInterval(timer);
            setStatus("解析パイプラインの進捗確認に失敗しました。システム統計で最新状態を確認してください。");
          }
        });
    }
    var timer = setInterval(poll, intervalMs);
    poll();
  }

  function _pollPipelineTask(courseId, taskId, phase, totalChunks, setStatus) {
    var retryCount = 0;
    var maxRetries = 5;
    var intervalMs = 3000;
    var phaseLabel = phase === "audio" ? "音声生成" : "原稿生成";

    function poll() {
      apiFetch("/admin/tasks/" + taskId)
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0;
          var rd = task.result_data || {};
          var progress = rd.progress || 0;
          var generated = rd.generated || 0;
          var skipped = rd.skipped || 0;
          var errors = rd.errors || 0;
          var processed = generated + skipped + errors;

          if (task.status === "completed") {
            clearInterval(timer);
            if (phase === "script" && rd.next_task_id) {
              // チェイン先の音声タスクに移行
              setStatus(
                "原稿生成完了（" + generated + "件生成 / " + skipped + "件スキップ）。音声生成を開始しました。（進捗: 0%）"
              );
              _pollPipelineTask(courseId, rd.next_task_id, "audio", totalChunks, setStatus);
            } else {
              setStatus(
                "自動生成が完了しました。" + phaseLabel + ": " + generated + "件生成 / " +
                skipped + "件スキップ" + (errors > 0 ? " / " + errors + "件エラー" : "") +
                "\n\nLecture Studio タブから内容を確認できます。"
              );
            }
          } else if (task.status === "failed") {
            clearInterval(timer);
            setStatus(
              phaseLabel + "に失敗しました: " + (task.error_message || "不明なエラー") +
              "\n\nLecture Studio タブから手動で再実行してください。"
            );
          } else {
            setStatus(
              phaseLabel + "中... (" + processed + " / " + totalChunks + " — " + progress + "%)"
            );
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            clearInterval(timer);
            setStatus(
              phaseLabel + "の進捗確認に失敗しました。Lecture Studio タブで最新の状態を確認してください。"
            );
          }
        });
    }

    var timer = setInterval(poll, intervalMs);
    poll();
  }

  // ── Course Import (Issue #50) ───────────────────────────────────────
  function openImportCourseModal() {
    // Remove existing modal if any
    var existing = document.getElementById("import-course-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "import-course-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";

    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border-secondary);border-radius:8px;padding:24px;min-width:400px;max-width:600px;max-height:70vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">既存コースを読み込む</h3>' +
          '<button id="import-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 12px">登録済みのコースを選択して、Course Builderで再編集できます。</p>' +
        '<div id="import-course-list" style="overflow-y:auto;flex:1">' +
          '<p style="color:var(--color-text-tertiary);font-size:13px">読み込み中...</p>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    // Close on overlay click
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    document.getElementById("import-modal-close").addEventListener("click", function () {
      overlay.remove();
    });

    // Fetch teacher's courses
    apiFetch("/admin/courses")
      .then(function (res) { return res.json(); })
      .then(function (courses) {
        var listEl = document.getElementById("import-course-list");
        if (!courses || courses.length === 0) {
          listEl.innerHTML = '<p style="color:var(--color-text-tertiary);font-size:13px">登録済みのコースがありません。</p>';
          return;
        }
        var html = "";
        courses.forEach(function (c) {
          var statusBadge = "";
          if (c.is_template) {
            statusBadge = '<span style="font-size:10px;background:var(--color-text-info);color:#fff;padding:1px 6px;border-radius:3px;margin-left:6px">テンプレート</span>';
          }
          var updatedAt = "";
          if (c.updated_at) {
            try {
              var dt = new Date(c.updated_at);
              updatedAt = dt.getFullYear() + "/" + (dt.getMonth() + 1) + "/" + dt.getDate();
            } catch (e) { updatedAt = ""; }
          }
          html +=
            '<div class="import-course-item" data-course-id="' + escHtml(c.id) + '" style="padding:10px 12px;border:1px solid var(--color-border-secondary);border-radius:6px;margin-bottom:8px;transition:background 0.15s">' +
              '<div style="display:flex;justify-content:space-between;align-items:center">' +
                '<div class="import-course-select" style="flex:1;cursor:pointer">' +
                  '<div style="font-size:14px;color:var(--color-text-primary);font-weight:500">' + escHtml(c.title) + statusBadge + '</div>' +
                  '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:2px">ID: ' + escHtml(c.id) + (updatedAt ? ' | 更新: ' + updatedAt : '') + '</div>' +
                '</div>' +
                '<div style="display:flex;gap:8px;align-items:center">' +
                  '<span class="import-course-select" style="font-size:12px;color:var(--color-text-info);cursor:pointer">選択 &rarr;</span>' +
                  '<button class="course-delete-btn" data-course-id="' + escHtml(c.id) + '" data-course-title="' + escHtml(c.title) + '" style="background:none;border:1px solid var(--color-text-danger);color:var(--color-text-danger);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:11px">削除</button>' +
                '</div>' +
              '</div>' +
            '</div>';
        });
        listEl.innerHTML = html;

        // Add click handlers for import
        listEl.querySelectorAll(".import-course-select").forEach(function (el) {
          var item = el.closest(".import-course-item");
          el.addEventListener("click", function () {
            var courseId = item.getAttribute("data-course-id");
            importCourse(courseId);
            overlay.remove();
          });
        });
        listEl.querySelectorAll(".import-course-item").forEach(function (item) {
          item.addEventListener("mouseenter", function () {
            this.style.background = "var(--color-background-tertiary)";
          });
          item.addEventListener("mouseleave", function () {
            this.style.background = "";
          });
        });
        // Add click handlers for course delete
        listEl.querySelectorAll(".course-delete-btn").forEach(function (btn) {
          btn.addEventListener("click", function (e) {
            e.stopPropagation();
            var cid = this.getAttribute("data-course-id");
            var title = this.getAttribute("data-course-title");
            overlay.remove();
            openDeleteConfirmModal("course", cid, title);
          });
        });
      })
      .catch(function () {
        var listEl = document.getElementById("import-course-list");
        if (listEl) listEl.innerHTML = '<p style="color:var(--color-text-danger);font-size:13px">コース一覧の取得に失敗しました。</p>';
      });
  }

  function importCourse(courseId) {
    // 1. Fetch course data in draft format
    apiFetch("/admin/courses/" + courseId + "/draft-format")
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load course");
        return res.json();
      })
      .then(function (data) {
        var draft = data.course_draft;
        var courseTitle = data.course_title || "不明なコース";

        // 2. Create a new session for the import
        return apiFetch("/admin/course-builder/sessions", {
          method: "POST",
          body: JSON.stringify({ title: "再編集: " + courseTitle }),
        })
          .then(function (res) { return res.json(); })
          .then(function (sessionData) {
            var sessionId = sessionData.session_id;

            // 3. Set state with imported data
            var initMessage = "コース「" + courseTitle + "」のデータを読み込みました。どのように変更・アップデートしますか？\n\n現在のコース構成はプレビューに表示されています。例えば以下のような指示ができます：\n- 「第3章に新しいトピックを追加して」\n- 「この概念をもう少し細かく分割して」\n- 「前提知識の順序を見直して」";
            state.currentSessionId = sessionId;
            state.chatHistory = [
              { role: "assistant", content: initMessage },
            ];
            state.chatMessages = [
              { role: "assistant", content: initMessage },
            ];
            state.courseDraft = draft;
            state.importedFromCourseId = courseId;

            // 4. Save session with initial state
            return apiFetch("/admin/course-builder/sessions/" + sessionId, {
              method: "PUT",
              body: JSON.stringify({
                title: "再編集: " + courseTitle,
                history: state.chatHistory,
                course_draft: draft,
              }),
            });
          });
      })
      .then(function () {
        renderCourseChat();
        renderCoursePreview();
        reloadSessionBar(state.currentSessionId);
      })
      .catch(function () {
        state.chatMessages.push({
          role: "assistant",
          content: "コースの読み込みに失敗しました。もう一度お試しください。",
        });
        renderCourseChat();
      });
  }

  // ── Stumbles (Unanswered Queries) ──────────────────────────────────
  function initStumbles() {
    var select = document.getElementById("stumbles-course-select");
    var refreshBtn = document.getElementById("refresh-stumbles");

    apiFetch("/learning/courses")
      .then(function (res) { return res.json(); })
      .then(function (courses) {
        courses.forEach(function (c) {
          var opt = document.createElement("option");
          opt.value = c.id;
          opt.textContent = c.title;
          select.appendChild(opt);
        });
      })
      .catch(function () {});

    function loadStumbles() {
      var courseId = select.value;
      var tbody = document.getElementById("stumbles-tbody");
      if (!courseId) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-tertiary)">コースを選択してください</td></tr>';
        return;
      }
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-tertiary)">読み込み中...</td></tr>';
      apiFetch("/admin/courses/" + courseId + "/unanswered-queries")
        .then(function (res) { return res.json(); })
        .then(function (rows) {
          if (!rows || rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-tertiary)">つまづきデータはまだありません</td></tr>';
            return;
          }
          var html = "";
          rows.forEach(function (r) {
            var dt = "";
            if (r.asked_at) {
              try {
                var d = new Date(r.asked_at);
                dt = d.getFullYear() + "/" + (d.getMonth() + 1) + "/" + d.getDate() + " " +
                  d.getHours() + ":" + String(d.getMinutes()).padStart(2, "0");
              } catch (e) { dt = r.asked_at; }
            }
            html += "<tr>";
            html += "<td style='white-space:nowrap'>" + escHtml(dt) + "</td>";
            html += "<td>" + escHtml(r.student_name) + "</td>";
            html += "<td>" + escHtml(r.topic_id) + "</td>";
            html += "<td style='max-width:400px;word-break:break-word'>" + escHtml(r.question) + "</td>";
            html += "</tr>";
          });
          tbody.innerHTML = html;
        })
        .catch(function () {
          tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-danger)">読み込みに失敗しました</td></tr>';
        });
    }

    select.addEventListener("change", loadStumbles);
    refreshBtn.addEventListener("click", loadStumbles);
  }

  // ── Error Log Analysis ─────────────────────────────────────────────
  function initErrorAnalysis() {
    if (state.role !== "SYSTEM_ADMIN") return;
    var keywordEl = document.getElementById("error-log-keyword");
    var minutesEl = document.getElementById("error-log-minutes");
    var includeInfoEl = document.getElementById("error-log-include-info");
    var refreshBtn = document.getElementById("error-log-refresh");
    if (!keywordEl || !minutesEl || !includeInfoEl || !refreshBtn) return;

    function load() {
      loadErrorLogs();
    }

    refreshBtn.addEventListener("click", load);
    minutesEl.addEventListener("change", load);
    includeInfoEl.addEventListener("change", load);
    keywordEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter") load();
    });
    document.getElementById("error-log-list").addEventListener("click", function (e) {
      var checkbox = e.target.closest(".error-log-select-row");
      if (!checkbox) return;
      toggleErrorLogSelection(Number(checkbox.dataset.logId), checkbox.checked, e.shiftKey);
    });
    document.getElementById("error-log-select-all").addEventListener("click", selectAllErrorLogs);
    document.getElementById("error-log-clear-selection").addEventListener("click", clearErrorLogSelection);
    document.getElementById("error-log-copy-selected").addEventListener("click", copySelectedErrorLogs);
    document.getElementById("error-log-copy-visible").addEventListener("click", copyVisibleErrorLogs);
    onTabActivate("error-analysis", load);
    load();
  }

  function setErrorLogStatus(message, kind) {
    var el = document.getElementById("error-log-status");
    if (!el) return;
    if (!message) {
      el.style.display = "none";
      el.textContent = "";
      return;
    }
    el.textContent = message;
    el.className = "upload-status upload-status-" + (kind || "info");
    el.style.display = "block";
  }

  function loadErrorLogs() {
    var listEl = document.getElementById("error-log-list");
    var keywordEl = document.getElementById("error-log-keyword");
    var minutesEl = document.getElementById("error-log-minutes");
    var includeInfoEl = document.getElementById("error-log-include-info");
    if (!listEl || !keywordEl || !minutesEl || !includeInfoEl) return;

    setErrorLogStatus("", "");
    listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>';
    var qs = "?keyword=" + encodeURIComponent(keywordEl.value.trim()) +
      "&minutes=" + encodeURIComponent(minutesEl.value || "1440") +
      "&limit=1000" +
      "&include_info=" + encodeURIComponent(includeInfoEl.checked ? "true" : "false");
    apiFetch("/admin/error-logs" + qs)
      .then(function (res) {
        if (!res.ok) return res.json().catch(function () { return {}; }).then(function (d) { throw d; });
        return res.json();
      })
      .then(function (data) {
        renderErrorLogs(data.items || []);
      })
      .catch(function (err) {
        listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">読み込みに失敗しました</div>';
        setErrorLogStatus((err && err.detail) || "エラーログの取得に失敗しました", "error");
      });
  }

  function renderErrorLogs(rows) {
    var listEl = document.getElementById("error-log-list");
    if (!listEl) return;
    state.errorLogs = rows || [];
    state.selectedErrorLogIds.clear();
    state.lastSelectedErrorLogIndex = null;
    updateErrorLogSelectionUi();
    if (!rows || rows.length === 0) {
      listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">条件に一致するエラーはありません</div>';
      return;
    }

    var html = "";
    rows.forEach(function (row, idx) {
      var logId = String(idx);
      var meta = [
        ["session_id", "セッション"],
        ["user_id", "ユーザー"],
        ["material_id", "教材"],
        ["course_id", "コース"],
      ];
      var chips = "";
      meta.forEach(function (m) {
        if (row[m[0]]) {
          chips += '<span class="error-log-chip">' + escHtml(m[1]) + ': ' + escHtml(row[m[0]]) + '</span>';
        }
      });
      if (!chips) chips = '<span class="error-log-chip muted">ID情報なし</span>';

      html += '<div class="error-log-item" data-log-id="' + escHtml(logId) + '">' +
        '<div class="error-log-item-head">' +
          '<label class="error-log-check">' +
            '<input class="error-log-select-row" type="checkbox" data-log-id="' + escHtml(logId) + '">' +
          '</label>' +
          '<div class="error-log-main">' +
            '<div class="error-log-time">' + escHtml(formatDateTime(row.timestamp)) + ' / ' + escHtml(row.level) + ' / ' + escHtml(row.logger) + '</div>' +
            '<div class="error-log-path">' + escHtml(row.method || "") + ' ' + escHtml(row.path || "") + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="error-log-chips">' + chips + '</div>' +
        '<pre class="error-log-message">' + escHtml(row.message || "") + '</pre>';
      if (row.traceback) {
        html += '<details class="error-log-trace"><summary>Traceback</summary><pre>' + escHtml(row.traceback) + '</pre></details>';
      }
      html += '</div>';
    });
    listEl.innerHTML = html;
  }

  function serializeErrorLog(row) {
    return [
      "timestamp: " + (row.timestamp || ""),
      "level: " + (row.level || ""),
      "logger: " + (row.logger || ""),
      "method: " + (row.method || ""),
      "path: " + (row.path || ""),
      "session_id: " + (row.session_id || ""),
      "user_id: " + (row.user_id || ""),
      "material_id: " + (row.material_id || ""),
      "course_id: " + (row.course_id || ""),
      "message: " + (row.message || ""),
      row.traceback ? "traceback:\n" + row.traceback : "",
    ].filter(Boolean).join("\n");
  }

  function updateErrorLogSelectionUi() {
    var countEl = document.getElementById("error-log-selection-count");
    var copySelectedBtn = document.getElementById("error-log-copy-selected");
    var selectedCount = state.selectedErrorLogIds.size;
    if (countEl) countEl.textContent = selectedCount + "件選択中";
    if (copySelectedBtn) copySelectedBtn.disabled = selectedCount === 0;

    document.querySelectorAll(".error-log-item").forEach(function (item) {
      var id = Number(item.dataset.logId);
      var selected = state.selectedErrorLogIds.has(id);
      item.classList.toggle("selected", selected);
      var checkbox = item.querySelector(".error-log-select-row");
      if (checkbox) checkbox.checked = selected;
    });
  }

  function toggleErrorLogSelection(index, checked, shiftKey) {
    if (!state.errorLogs || !state.errorLogs[index]) return;
    if (shiftKey && state.lastSelectedErrorLogIndex !== null) {
      var start = Math.min(state.lastSelectedErrorLogIndex, index);
      var end = Math.max(state.lastSelectedErrorLogIndex, index);
      for (var i = start; i <= end; i += 1) {
        if (state.errorLogs[i]) {
          if (checked) state.selectedErrorLogIds.add(i);
          else state.selectedErrorLogIds.delete(i);
        }
      }
    } else if (checked) {
      state.selectedErrorLogIds.add(index);
    } else {
      state.selectedErrorLogIds.delete(index);
    }
    state.lastSelectedErrorLogIndex = index;
    updateErrorLogSelectionUi();
  }

  function selectAllErrorLogs() {
    (state.errorLogs || []).forEach(function (_, idx) {
      state.selectedErrorLogIds.add(idx);
    });
    state.lastSelectedErrorLogIndex = null;
    updateErrorLogSelectionUi();
  }

  function clearErrorLogSelection() {
    state.selectedErrorLogIds.clear();
    state.lastSelectedErrorLogIndex = null;
    updateErrorLogSelectionUi();
  }

  function getSelectedErrorLogs() {
    return Array.from(state.selectedErrorLogIds)
      .sort(function (a, b) { return a - b; })
      .map(function (idx) { return state.errorLogs[idx]; })
      .filter(Boolean);
  }

  function getErrorLogCopyFormat() {
    var el = document.getElementById("error-log-copy-format");
    return el ? el.value : "ai-markdown";
  }

  function getErrorLogExportContext(rows) {
    var keywordEl = document.getElementById("error-log-keyword");
    var minutesEl = document.getElementById("error-log-minutes");
    var includeInfoEl = document.getElementById("error-log-include-info");
    return {
      exported_at: new Date().toISOString(),
      total_logs: rows.length,
      keyword: keywordEl ? keywordEl.value.trim() : "",
      minutes: minutesEl ? minutesEl.value || "1440" : "1440",
      include_info: includeInfoEl ? !!includeInfoEl.checked : false,
    };
  }

  function serializeErrorLogs(rows, format) {
    var ctx = getErrorLogExportContext(rows);
    if (format === "json") {
      return JSON.stringify({
        exported_at: ctx.exported_at,
        total_logs: ctx.total_logs,
        filters: {
          keyword: ctx.keyword,
          minutes: Number(ctx.minutes),
          include_info: ctx.include_info,
        },
        logs: rows,
      }, null, 2);
    }
    if (format === "plain") {
      return rows.map(function (row, idx) {
        return "===== Log " + (idx + 1) + " =====\n" + serializeErrorLog(row);
      }).join("\n\n");
    }
    var lines = [
      "# Error Log Analysis Input",
      "",
      "## Context",
      "- Exported at: " + ctx.exported_at,
      "- Total logs: " + ctx.total_logs,
      "- Includes INFO: " + String(ctx.include_info),
      "- Time window minutes: " + ctx.minutes,
      "- Keyword: " + (ctx.keyword || "(none)"),
      "",
      "## Logs",
    ];
    rows.forEach(function (row, idx) {
      lines.push(
        "",
        "### Log " + (idx + 1),
        "````text",
        serializeErrorLog(row),
        "````"
      );
    });
    return lines.join("\n");
  }

  function copyErrorLogText(text, successMessage) {
    function ok() {
      setErrorLogStatus(successMessage, "success");
    }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try {
        document.execCommand("copy");
        ok();
      } catch (e) {
        setErrorLogStatus("コピーに失敗しました", "error");
      }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok).catch(fallback);
    } else {
      fallback();
    }
  }

  function copyErrorLogs(rows, label) {
    if (!rows || rows.length === 0) {
      setErrorLogStatus("コピー対象のログがありません", "error");
      return;
    }
    var format = getErrorLogCopyFormat();
    var text = serializeErrorLogs(rows, format);
    copyErrorLogText(text, label + " " + rows.length + "件をコピーしました");
  }

  function copySelectedErrorLogs() {
    copyErrorLogs(getSelectedErrorLogs(), "選択したログ");
  }

  function copyVisibleErrorLogs() {
    copyErrorLogs(state.errorLogs || [], "表示中のログ");
  }

  // ── Logout ─────────────────────────────────────────────────────────
  function initLogout() {
    document.getElementById("logout-btn").addEventListener("click", function () {
      state.token = null;
      state.username = null;
      localStorage.removeItem("eg_token");
      localStorage.removeItem("eg_username");
      renderAuth();
    });
  }

  // ── User Management ────────────────────────────────────────────────
  function initUserManagement() {
    // 学生管理タブ (TEACHER)
    var studentForm = document.getElementById("student-form");
    if (studentForm) {
      studentForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var username = document.getElementById("student-username").value.trim();
        var email = document.getElementById("student-email").value.trim();
        var password = document.getElementById("student-password").value;
        var msgEl = document.getElementById("student-msg");
        if (!username || !password) return;
        msgEl.textContent = "作成中...";
        msgEl.className = "upload-status upload-status-info";
        apiFetch("/admin/users/student", {
          method: "POST",
          body: JSON.stringify({ username: username, email: email || username + "@learning.local", password: password }),
        })
          .then(function (res) {
            if (res.status === 409) throw { detail: "そのユーザー名は既に使用されています" };
            if (!res.ok) return res.json().then(function (d) { throw d; });
            return res.json();
          })
          .then(function (data) {
            msgEl.textContent = "学生「" + escHtml(data.username) + "」を作成しました。";
            msgEl.className = "upload-status upload-status-success";
            studentForm.reset();
          })
          .catch(function (err) {
            msgEl.textContent = "作成に失敗しました: " + (err.detail || "不明なエラー");
            msgEl.className = "upload-status upload-status-error";
          });
      });
    }

    // 教員管理タブ (SYSTEM_ADMIN)
    var teacherForm = document.getElementById("teacher-form");
    if (teacherForm) {
      teacherForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var username = document.getElementById("teacher-username").value.trim();
        var email = document.getElementById("teacher-email").value.trim();
        var password = document.getElementById("teacher-password").value;
        var msgEl = document.getElementById("teacher-msg");
        if (!username || !password) return;
        msgEl.textContent = "作成中...";
        msgEl.className = "upload-status upload-status-info";
        apiFetch("/admin/users/teacher", {
          method: "POST",
          body: JSON.stringify({ username: username, email: email || username + "@learning.local", password: password }),
        })
          .then(function (res) {
            if (res.status === 409) throw { detail: "そのユーザー名は既に使用されています" };
            if (!res.ok) return res.json().then(function (d) { throw d; });
            return res.json();
          })
          .then(function (data) {
            msgEl.textContent = "教員「" + escHtml(data.username) + "」を作成しました。";
            msgEl.className = "upload-status upload-status-success";
            teacherForm.reset();
          })
          .catch(function (err) {
            msgEl.textContent = "作成に失敗しました: " + (err.detail || "不明なエラー");
            msgEl.className = "upload-status upload-status-error";
          });
      });
    }
  }

  // ── Role-based UI setup ───────────────────────────────────────────
  function setupRoleBasedUI() {
    // Redirect STUDENT to learning page
    if (state.role === "STUDENT") {
      window.location.href = "/";
      return false;
    }

    var tabsEl = document.getElementById("adminTabs");

    if (state.role === "SYSTEM_ADMIN") {
      ["materials", "course-builder", "course-management", "lecture-studio"].forEach(function (tabName) {
        var tabBtn = tabsEl.querySelector('.admin-tab[data-tab="' + tabName + '"]');
        var panel = document.getElementById("tab-" + tabName);
        if (tabBtn) tabBtn.style.display = "none";
        if (panel) panel.classList.remove("vis");
      });

      var errorTabBtn = document.getElementById("tab-btn-error-analysis");
      if (errorTabBtn) errorTabBtn.style.display = "";
      activateTabView("error-analysis");
    }

    // Show student management tab for TEACHER
    if (state.role === "TEACHER" || state.role === "SYSTEM_ADMIN") {
      var studentTab = document.createElement("button");
      studentTab.className = "admin-tab";
      studentTab.dataset.tab = "students";
      studentTab.textContent = "学生管理";
      tabsEl.appendChild(studentTab);
    }

    // Show teacher management tab for SYSTEM_ADMIN only
    if (state.role === "SYSTEM_ADMIN") {
      var teacherTab = document.createElement("button");
      teacherTab.className = "admin-tab";
      teacherTab.dataset.tab = "teachers";
      teacherTab.textContent = "教員管理";
      tabsEl.appendChild(teacherTab);

      var ssTabBtn = document.getElementById("tab-btn-system-stats");
      if (ssTabBtn) ssTabBtn.style.display = "";
    }

    // Show schema evolution tab for TEACHER/SYSTEM_ADMIN
    if (state.role === "TEACHER" || state.role === "SYSTEM_ADMIN") {
      var schemaTab = document.createElement("button");
      schemaTab.className = "admin-tab";
      schemaTab.dataset.tab = "schema";
      schemaTab.textContent = "スキーマ管理";
      tabsEl.appendChild(schemaTab);
    }

    // Create schema management panel
    var schemaPanel = document.createElement("div");
    schemaPanel.className = "admin-panel";
    schemaPanel.id = "tab-schema";
    schemaPanel.innerHTML =
      '<div class="admin-section">' +
        '<h3 class="admin-section-title">DSLスキーマ自己進化</h3>' +
        '<p style="color:var(--color-text-secondary);margin-bottom:12px">' +
          '学生のつまづきデータを分析し、不足している概念カテゴリや関係性の拡張をAIが提案します。' +
        '</p>' +
        '<div style="display:flex;gap:8px;margin-bottom:16px">' +
          '<button id="schema-analyze-btn" class="admin-action-btn">AIメタ分析を実行</button>' +
          '<button id="schema-refresh-btn" class="admin-action-btn" style="background:var(--color-bg-tertiary);color:var(--color-text)">提案を更新</button>' +
        '</div>' +
        '<div id="schema-analyze-msg" class="upload-status" style="display:none"></div>' +
        '<h4 style="margin:16px 0 8px">提案一覧</h4>' +
        '<div id="schema-proposals-list" style="margin-bottom:24px">' +
          '<p style="color:var(--color-text-tertiary)">読み込み中...</p>' +
        '</div>' +
        '<h4 style="margin:16px 0 8px">再抽出ジョブ</h4>' +
        '<div id="schema-jobs-list">' +
          '<p style="color:var(--color-text-tertiary)">読み込み中...</p>' +
        '</div>' +
      '</div>' +
      '<div class="admin-section" style="margin-top:24px">' +
        '<h3 class="admin-section-title">現在のスキーマ定義</h3>' +
        '<div style="display:flex;gap:24px;flex-wrap:wrap">' +
          '<div style="flex:1;min-width:300px">' +
            '<h4>概念カテゴリ (OntologyType)</h4>' +
            '<div id="schema-types-list"><p style="color:var(--color-text-tertiary)">読み込み中...</p></div>' +
          '</div>' +
          '<div style="flex:1;min-width:300px">' +
            '<h4>関係性タイプ (CorePredicate)</h4>' +
            '<div id="schema-preds-list"><p style="color:var(--color-text-tertiary)">読み込み中...</p></div>' +
          '</div>' +
        '</div>' +
      '</div>';
    tabsEl.parentElement.appendChild(schemaPanel);

    // Create student management panel
    var studentPanel = document.createElement("div");
    studentPanel.className = "admin-panel";
    studentPanel.id = "tab-students";
    studentPanel.innerHTML =
      '<div class="admin-section">' +
        '<h3 class="admin-section-title">学生アカウント追加</h3>' +
        '<form id="student-form" class="admin-user-form">' +
          '<div class="admin-form-row">' +
            '<label>ユーザー名 <input id="student-username" type="text" required placeholder="student_name"></label>' +
          '</div>' +
          '<div class="admin-form-row">' +
            '<label>メールアドレス <input id="student-email" type="email" placeholder="student@example.com"></label>' +
          '</div>' +
          '<div class="admin-form-row">' +
            '<label>パスワード <input id="student-password" type="password" required placeholder="パスワード"></label>' +
          '</div>' +
          '<button type="submit" class="admin-action-btn">学生を作成</button>' +
        '</form>' +
        '<div id="student-msg" class="upload-status"></div>' +
      '</div>';
    tabsEl.parentElement.appendChild(studentPanel);

    // Create teacher management panel (SYSTEM_ADMIN only)
    if (state.role === "SYSTEM_ADMIN") {
      var teacherPanel = document.createElement("div");
      teacherPanel.className = "admin-panel";
      teacherPanel.id = "tab-teachers";
      teacherPanel.innerHTML =
        '<div class="admin-section">' +
          '<h3 class="admin-section-title">教員アカウント追加</h3>' +
          '<form id="teacher-form" class="admin-user-form">' +
            '<div class="admin-form-row">' +
              '<label>ユーザー名 <input id="teacher-username" type="text" required placeholder="teacher_name"></label>' +
            '</div>' +
            '<div class="admin-form-row">' +
              '<label>メールアドレス <input id="teacher-email" type="email" placeholder="teacher@example.com"></label>' +
            '</div>' +
            '<div class="admin-form-row">' +
              '<label>パスワード <input id="teacher-password" type="password" required placeholder="パスワード"></label>' +
            '</div>' +
            '<button type="submit" class="admin-action-btn">教員を作成</button>' +
          '</form>' +
          '<div id="teacher-msg" class="upload-status"></div>' +
        '</div>';
      tabsEl.parentElement.appendChild(teacherPanel);
    }

    return true;
  }

  // ── Schema Proposals (Shadow Testing — Issue #45) ──────────────────
  var _currentSimProposalId = null;

  function initSchemaProposals() {
    loadSchemaProposalsList();
    initApproveActions();

    // Refresh proposals list when the tab is activated
    onTabActivate("schema-proposals", function () {
      loadSchemaProposalsList();
    });
  }

  function loadSchemaProposalsList() {
    var container = document.getElementById("sp-proposals-list");
    if (!container) return;

    apiFetch("/admin/schema-proposals")
      .then(function (res) { return res.json(); })
      .then(function (proposals) {
        if (!proposals || proposals.length === 0) {
          container.innerHTML = '<p style="color:var(--color-text-tertiary)">提案はまだありません。「スキーマ管理」タブからAIメタ分析を実行してください。</p>';
          return;
        }
        var html = "";
        proposals.forEach(function (p) {
          var statusBadge = p.status === "pending"
            ? '<span style="color:#f59e0b;font-weight:bold">保留中</span>'
            : p.status === "approved"
              ? '<span style="color:#22c55e;font-weight:bold">承認済</span>'
              : '<span style="color:#ef4444;font-weight:bold">却下</span>';

          html += '<div class="sp-proposal-card" style="border:1px solid var(--color-border);border-radius:8px;padding:12px;margin-bottom:12px">';
          html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">';
          html += '<strong style="flex:1">' + escHtml(p.summary) + '</strong>' + statusBadge;
          html += '</div>';
          html += '<p style="font-size:0.85em;color:var(--color-text-secondary);margin-bottom:8px">' + escHtml(p.reasoning) + '</p>';

          if (p.items && p.items.length > 0) {
            html += '<div style="margin-bottom:8px">';
            html += '<span style="font-size:0.8em;font-weight:bold;color:var(--color-text-secondary)">提案アイテム:</span>';
            html += '<ul style="margin:4px 0 0;padding-left:20px;font-size:0.9em">';
            p.items.forEach(function (item) {
              var typeLabel = item.item_type === "ontology_type"
                ? '<span style="background:#3b82f6;color:#fff;padding:1px 6px;border-radius:3px;font-size:0.75em">概念</span>'
                : '<span style="background:#8b5cf6;color:#fff;padding:1px 6px;border-radius:3px;font-size:0.75em">関係</span>';
              html += '<li>' + typeLabel + ' <code>' + escHtml(item.key) + '</code>: ' + escHtml(item.description) + '</li>';
            });
            html += '</ul></div>';
          }

          html += '<div style="font-size:0.8em;color:var(--color-text-tertiary);margin-bottom:8px">分析クエリ数: ' + p.source_query_count + ' | 作成: ' + escHtml(p.created_at ? p.created_at.substring(0, 16) : "") + '</div>';

          if (p.status === "pending") {
            html += '<div style="display:flex;gap:8px">';
            html += '<button class="admin-action-btn sp-simulate-btn" data-id="' + escHtml(p.proposal_id) + '" style="font-size:0.85em;padding:4px 12px">シミュレーションを実行</button>';
            html += '</div>';
          }

          html += '</div>';
        });
        container.innerHTML = html;

        // Bind simulate buttons
        container.querySelectorAll(".sp-simulate-btn").forEach(function (btn) {
          btn.addEventListener("click", function () {
            runSimulation(btn.dataset.id, btn);
          });
        });
      })
      .catch(function () {
        container.innerHTML = '<p style="color:var(--color-text-danger)">読み込みに失敗しました</p>';
      });
  }

  function runSimulation(proposalId, btn) {
    btn.disabled = true;
    btn.textContent = "シミュレーション中...";
    _currentSimProposalId = proposalId;

    apiFetch("/admin/schema-proposals/" + proposalId + "/simulate", { method: "POST" })
      .then(function (res) {
        if (!res.ok) throw new Error("Simulation failed");
        return res.json();
      })
      .then(function (data) {
        btn.textContent = "シミュレーション完了";
        btn.style.background = "var(--color-text-success)";
        btn.style.color = "#fff";
        renderSimulationResult(data);
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "シミュレーションを実行";
        var msgEl = document.getElementById("sp-approve-msg");
        if (msgEl) {
          msgEl.style.display = "block";
          msgEl.textContent = "シミュレーションに失敗しました。ドキュメントが不足している可能性があります。";
          msgEl.className = "upload-status upload-status-error";
        }
      });
  }

  function renderSimulationResult(data) {
    var area = document.getElementById("sp-simulation-area");
    var summaryEl = document.getElementById("sp-simulation-summary");
    var detailsEl = document.getElementById("sp-simulation-details");
    var actionsEl = document.getElementById("sp-approve-actions");

    area.style.display = "block";

    // Summary stats
    var stats = data.stats || {};
    var summaryHtml = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px">';
    summaryHtml += renderStatCard("Target", stats.target_doc_count || 0, "#3b82f6");
    summaryHtml += renderStatCard("Similar", stats.similar_doc_count || 0, "#8b5cf6");
    summaryHtml += renderStatCard("Control", stats.control_doc_count || 0, "#6b7280");
    summaryHtml += '</div>';
    summaryHtml += '<div style="display:flex;gap:16px;flex-wrap:wrap">';
    summaryHtml += renderStatCard("追加コンセプト", stats.total_added_concepts || 0, "#22c55e");
    summaryHtml += renderStatCard("削除コンセプト", stats.total_removed_concepts || 0, "#ef4444");
    summaryHtml += renderStatCard("再分類ノード", stats.total_reclassified_nodes || 0, "#f59e0b");
    summaryHtml += '</div>';
    summaryEl.innerHTML = summaryHtml;

    // Detail diff per category
    var detailHtml = "";
    var categories = [
      { key: "target", label: "Target ドキュメント", color: "#3b82f6", desc: "提案トリガーとなった未回答クエリに紐づくドキュメント" },
      { key: "similar", label: "Similar ドキュメント", color: "#8b5cf6", desc: "Targetと同系統のドキュメント" },
      { key: "control", label: "Control ドキュメント", color: "#6b7280", desc: "ベースライン（関連性が低いドキュメント）" },
    ];

    var results = data.results || {};
    categories.forEach(function (cat) {
      var docs = results[cat.key] || [];
      detailHtml += '<div style="margin-top:16px">';
      detailHtml += '<h4 style="color:' + cat.color + ';margin-bottom:4px">' + cat.label + ' (' + docs.length + '件)</h4>';
      detailHtml += '<p style="font-size:0.8em;color:var(--color-text-tertiary);margin-bottom:8px">' + cat.desc + '</p>';

      if (docs.length === 0) {
        detailHtml += '<p style="color:var(--color-text-tertiary);font-size:0.9em">対象ドキュメントなし</p>';
      } else {
        docs.forEach(function (doc) {
          detailHtml += renderDocDiff(doc, cat.color);
        });
      }
      detailHtml += '</div>';
    });

    detailsEl.innerHTML = detailHtml;
    actionsEl.style.display = "block";

    // Scroll to simulation area
    area.scrollIntoView({ behavior: "smooth" });
  }

  function renderStatCard(label, value, color) {
    return '<div style="background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:6px;padding:8px 16px;text-align:center;min-width:100px">' +
      '<div style="font-size:1.5em;font-weight:bold;color:' + color + '">' + value + '</div>' +
      '<div style="font-size:0.8em;color:var(--color-text-secondary)">' + escHtml(label) + '</div>' +
    '</div>';
  }

  function renderDocDiff(doc, borderColor) {
    var html = '<div style="border-left:3px solid ' + borderColor + ';padding:8px 12px;margin-bottom:8px;background:var(--color-bg-secondary);border-radius:0 6px 6px 0">';
    html += '<div style="font-weight:bold;margin-bottom:4px">' + escHtml(doc.title || doc.doc_id) + '</div>';

    if (doc.summary) {
      html += '<p style="font-size:0.85em;color:var(--color-text-secondary);margin-bottom:8px">' + escHtml(doc.summary) + '</p>';
    }

    // Added concepts
    if (doc.added_concepts && doc.added_concepts.length > 0) {
      html += '<div style="margin-bottom:4px"><span style="color:#22c55e;font-size:0.85em;font-weight:bold">+ 追加コンセプト:</span>';
      html += '<ul style="margin:2px 0 0;padding-left:20px;font-size:0.85em">';
      doc.added_concepts.forEach(function (c) {
        html += '<li style="color:#22c55e">' + escHtml(c.name || JSON.stringify(c)) + (c.type ? ' <code style="font-size:0.8em">' + escHtml(c.type) + '</code>' : '') + '</li>';
      });
      html += '</ul></div>';
    }

    // Removed concepts
    if (doc.removed_concepts && doc.removed_concepts.length > 0) {
      html += '<div style="margin-bottom:4px"><span style="color:#ef4444;font-size:0.85em;font-weight:bold">- 削除コンセプト:</span>';
      html += '<ul style="margin:2px 0 0;padding-left:20px;font-size:0.85em">';
      doc.removed_concepts.forEach(function (c) {
        html += '<li style="color:#ef4444">' + escHtml(c.name || JSON.stringify(c)) + '</li>';
      });
      html += '</ul></div>';
    }

    // Added relations
    if (doc.added_relations && doc.added_relations.length > 0) {
      html += '<div style="margin-bottom:4px"><span style="color:#22c55e;font-size:0.85em;font-weight:bold">+ 追加リレーション:</span>';
      html += '<ul style="margin:2px 0 0;padding-left:20px;font-size:0.85em">';
      doc.added_relations.forEach(function (r) {
        html += '<li style="color:#22c55e">' + escHtml(r.source) + ' <code>' + escHtml(r.predicate) + '</code> ' + escHtml(r.target) + '</li>';
      });
      html += '</ul></div>';
    }

    // Removed relations
    if (doc.removed_relations && doc.removed_relations.length > 0) {
      html += '<div style="margin-bottom:4px"><span style="color:#ef4444;font-size:0.85em;font-weight:bold">- 削除リレーション:</span>';
      html += '<ul style="margin:2px 0 0;padding-left:20px;font-size:0.85em">';
      doc.removed_relations.forEach(function (r) {
        html += '<li style="color:#ef4444">' + escHtml(r.source) + ' <code>' + escHtml(r.predicate) + '</code> ' + escHtml(r.target) + '</li>';
      });
      html += '</ul></div>';
    }

    // Reclassified nodes
    if (doc.reclassified_nodes && doc.reclassified_nodes.length > 0) {
      html += '<div style="margin-bottom:4px"><span style="color:#f59e0b;font-size:0.85em;font-weight:bold">~ 再分類ノード:</span>';
      html += '<ul style="margin:2px 0 0;padding-left:20px;font-size:0.85em">';
      doc.reclassified_nodes.forEach(function (n) {
        html += '<li style="color:#f59e0b">' + escHtml(n.name) + ': <code>' + escHtml(n.old_type) + '</code> → <code>' + escHtml(n.new_type) + '</code></li>';
      });
      html += '</ul></div>';
    }

    if ((!doc.added_concepts || doc.added_concepts.length === 0) &&
        (!doc.removed_concepts || doc.removed_concepts.length === 0) &&
        (!doc.added_relations || doc.added_relations.length === 0) &&
        (!doc.removed_relations || doc.removed_relations.length === 0) &&
        (!doc.reclassified_nodes || doc.reclassified_nodes.length === 0)) {
      html += '<p style="color:var(--color-text-tertiary);font-size:0.85em">変化なし</p>';
    }

    html += '</div>';
    return html;
  }

  function initApproveActions() {
    var fullBtn = document.getElementById("sp-approve-full");
    var canaryBtn = document.getElementById("sp-approve-canary");
    var canaryOpts = document.getElementById("sp-canary-options");
    var canaryConfirm = document.getElementById("sp-canary-confirm");

    if (!fullBtn) return;

    fullBtn.addEventListener("click", function () {
      if (!_currentSimProposalId) return;
      approveWithScope(_currentSimProposalId, "full", []);
    });

    canaryBtn.addEventListener("click", function () {
      canaryOpts.style.display = canaryOpts.style.display === "none" ? "block" : "none";
      loadCanaryCourses();
    });

    canaryConfirm.addEventListener("click", function () {
      if (!_currentSimProposalId) return;
      var select = document.getElementById("sp-canary-course-select");
      var selectedIds = [];
      for (var i = 0; i < select.options.length; i++) {
        if (select.options[i].selected) selectedIds.push(select.options[i].value);
      }
      if (selectedIds.length === 0) {
        var msgEl = document.getElementById("sp-approve-msg");
        msgEl.style.display = "block";
        msgEl.textContent = "適用するコースを選択してください。";
        msgEl.className = "upload-status upload-status-error";
        return;
      }
      approveWithScope(_currentSimProposalId, "canary", selectedIds);
    });
  }

  function loadCanaryCourses() {
    var select = document.getElementById("sp-canary-course-select");
    if (!select || select.options.length > 0) return;

    apiFetch("/learning/courses")
      .then(function (res) { return res.json(); })
      .then(function (courses) {
        courses.forEach(function (c) {
          var opt = document.createElement("option");
          opt.value = c.id;
          opt.textContent = c.title;
          select.appendChild(opt);
        });
      })
      .catch(function () {});
  }

  function approveWithScope(proposalId, scope, courseIds) {
    var msgEl = document.getElementById("sp-approve-msg");
    msgEl.style.display = "block";
    msgEl.textContent = "承認処理中...";
    msgEl.className = "upload-status upload-status-info";

    var fullBtn = document.getElementById("sp-approve-full");
    var canaryBtn = document.getElementById("sp-approve-canary");
    fullBtn.disabled = true;
    canaryBtn.disabled = true;

    apiFetch("/admin/schema-proposals/" + proposalId + "/approve-with-scope", {
      method: "PUT",
      body: JSON.stringify({ scope: scope, course_ids: courseIds }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Approve failed");
        return res.json();
      })
      .then(function () {
        var scopeLabel = scope === "full" ? "システム全体" : "カナリアリリース";
        msgEl.textContent = "スキーマ提案を承認しました（" + scopeLabel + "）。再抽出ジョブが開始されます。";
        msgEl.className = "upload-status upload-status-success";
        loadSchemaProposalsList();
      })
      .catch(function () {
        fullBtn.disabled = false;
        canaryBtn.disabled = false;
        msgEl.textContent = "承認に失敗しました。";
        msgEl.className = "upload-status upload-status-error";
      });
  }

  // ── Schema Evolution ───────────────────────────────────────────────
  function initSchemaEvolution() {
    var analyzeBtn = document.getElementById("schema-analyze-btn");
    var refreshBtn = document.getElementById("schema-refresh-btn");
    if (!analyzeBtn) return;

    analyzeBtn.addEventListener("click", function () {
      var msgEl = document.getElementById("schema-analyze-msg");
      msgEl.style.display = "block";
      msgEl.textContent = "AIがつまづきデータを分析中...";
      msgEl.className = "upload-status upload-status-info";
      analyzeBtn.disabled = true;

      apiFetch("/admin/schema-proposals/analyze", { method: "POST" })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          analyzeBtn.disabled = false;
          if (data.message) {
            msgEl.textContent = data.message;
            msgEl.className = "upload-status upload-status-info";
          } else {
            msgEl.textContent = "提案が生成されました: " + escHtml(data.summary || "");
            msgEl.className = "upload-status upload-status-success";
            loadSchemaProposals();
          }
        })
        .catch(function () {
          analyzeBtn.disabled = false;
          msgEl.textContent = "分析に失敗しました";
          msgEl.className = "upload-status upload-status-error";
        });
    });

    refreshBtn.addEventListener("click", function () {
      loadSchemaProposals();
      loadSchemaJobs();
      loadSchemaDefinitions();
    });

    loadSchemaProposals();
    loadSchemaJobs();
    loadSchemaDefinitions();
  }

  function loadSchemaProposals() {
    var container = document.getElementById("schema-proposals-list");
    apiFetch("/admin/schema-proposals")
      .then(function (res) { return res.json(); })
      .then(function (proposals) {
        if (!proposals || proposals.length === 0) {
          container.innerHTML = '<p style="color:var(--color-text-tertiary)">提案はまだありません。「AIメタ分析を実行」ボタンで生成できます。</p>';
          return;
        }
        var html = "";
        proposals.forEach(function (p) {
          var statusBadge = p.status === "pending"
            ? '<span style="color:#f59e0b;font-weight:bold">保留中</span>'
            : p.status === "approved"
              ? '<span style="color:#22c55e;font-weight:bold">承認済</span>'
              : '<span style="color:#ef4444;font-weight:bold">却下</span>';
          html += '<div style="border:1px solid var(--color-border);border-radius:8px;padding:12px;margin-bottom:8px">';
          html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">';
          html += '<strong>' + escHtml(p.summary) + '</strong>' + statusBadge;
          html += '</div>';
          html += '<p style="font-size:0.85em;color:var(--color-text-secondary);margin-bottom:8px">' + escHtml(p.reasoning) + '</p>';
          html += '<div style="font-size:0.8em;color:var(--color-text-tertiary)">分析クエリ数: ' + p.source_query_count + '</div>';
          if (p.items && p.items.length > 0) {
            html += '<ul style="margin:8px 0 0;padding-left:20px;font-size:0.9em">';
            p.items.forEach(function (item) {
              var typeLabel = item.item_type === "ontology_type" ? "[概念]" : "[関係]";
              html += '<li>' + typeLabel + ' <code>' + escHtml(item.key) + '</code>: ' + escHtml(item.description) + '</li>';
            });
            html += '</ul>';
          }
          if (p.status === "pending") {
            html += '<div style="margin-top:8px;display:flex;gap:8px">';
            html += '<button class="admin-action-btn schema-approve-btn" data-id="' + escHtml(p.proposal_id) + '" style="font-size:0.85em;padding:4px 12px">承認して適用</button>';
            html += '<button class="admin-action-btn schema-reject-btn" data-id="' + escHtml(p.proposal_id) + '" style="font-size:0.85em;padding:4px 12px;background:var(--color-bg-tertiary);color:var(--color-text)">却下</button>';
            html += '</div>';
          }
          html += '</div>';
        });
        container.innerHTML = html;

        // Bind approve/reject buttons
        container.querySelectorAll(".schema-approve-btn").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var pid = btn.dataset.id;
            btn.disabled = true;
            btn.textContent = "処理中...";
            apiFetch("/admin/schema-proposals/" + pid + "/approve", { method: "PUT" })
              .then(function (res) { return res.json(); })
              .then(function () {
                loadSchemaProposals();
                loadSchemaJobs();
                loadSchemaDefinitions();
              })
              .catch(function () { btn.disabled = false; btn.textContent = "承認して適用"; });
          });
        });
        container.querySelectorAll(".schema-reject-btn").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var pid = btn.dataset.id;
            btn.disabled = true;
            apiFetch("/admin/schema-proposals/" + pid + "/reject", { method: "PUT" })
              .then(function () { loadSchemaProposals(); })
              .catch(function () { btn.disabled = false; });
          });
        });
      })
      .catch(function () {
        container.innerHTML = '<p style="color:var(--color-text-danger)">読み込みに失敗しました</p>';
      });
  }

  function loadSchemaJobs() {
    var container = document.getElementById("schema-jobs-list");
    apiFetch("/admin/reextraction-jobs")
      .then(function (res) { return res.json(); })
      .then(function (jobs) {
        if (!jobs || jobs.length === 0) {
          container.innerHTML = '<p style="color:var(--color-text-tertiary)">再抽出ジョブはありません</p>';
          return;
        }
        var html = '<table style="width:100%;font-size:0.9em"><thead><tr><th>ID</th><th>ステータス</th><th>進捗</th><th>開始</th></tr></thead><tbody>';
        jobs.forEach(function (j) {
          var statusLabel = j.status === "running"
            ? '<span style="color:#3b82f6">実行中</span>'
            : j.status === "completed"
              ? '<span style="color:#22c55e">完了</span>'
              : j.status === "failed"
                ? '<span style="color:#ef4444">失敗</span>'
                : '<span style="color:var(--color-text-tertiary)">待機中</span>';
          html += '<tr>';
          html += '<td><code>' + escHtml(j.job_id) + '</code></td>';
          html += '<td>' + statusLabel + '</td>';
          html += '<td>' + j.processed_docs + '/' + j.total_docs + '</td>';
          html += '<td>' + escHtml(j.created_at ? j.created_at.substring(0, 16) : "") + '</td>';
          html += '</tr>';
        });
        html += '</tbody></table>';
        container.innerHTML = html;
      })
      .catch(function () {
        container.innerHTML = '<p style="color:var(--color-text-danger)">読み込みに失敗しました</p>';
      });
  }

  function loadSchemaDefinitions() {
    // Load ontology types
    apiFetch("/admin/schema/types")
      .then(function (res) { return res.json(); })
      .then(function (types) {
        var container = document.getElementById("schema-types-list");
        if (!types || types.length === 0) {
          container.innerHTML = '<p style="color:var(--color-text-tertiary)">なし</p>';
          return;
        }
        var html = '<ul style="list-style:none;padding:0;font-size:0.9em">';
        types.forEach(function (t) {
          var badge = t.is_builtin ? '' : ' <span style="color:#f59e0b;font-size:0.8em">[拡張]</span>';
          html += '<li style="padding:4px 0;border-bottom:1px solid var(--color-border)">';
          html += '<strong>' + escHtml(t.label) + '</strong>' + badge;
          if (t.description) html += '<br><span style="font-size:0.85em;color:var(--color-text-secondary)">' + escHtml(t.description) + '</span>';
          html += '</li>';
        });
        html += '</ul>';
        container.innerHTML = html;
      })
      .catch(function () {
        document.getElementById("schema-types-list").innerHTML = '<p style="color:var(--color-text-danger)">読み込み失敗</p>';
      });

    // Load predicates
    apiFetch("/admin/schema/predicates")
      .then(function (res) { return res.json(); })
      .then(function (preds) {
        var container = document.getElementById("schema-preds-list");
        if (!preds || preds.length === 0) {
          container.innerHTML = '<p style="color:var(--color-text-tertiary)">なし</p>';
          return;
        }
        var html = '<ul style="list-style:none;padding:0;font-size:0.9em">';
        preds.forEach(function (p) {
          var badge = p.is_builtin ? '' : ' <span style="color:#f59e0b;font-size:0.8em">[拡張]</span>';
          html += '<li style="padding:4px 0;border-bottom:1px solid var(--color-border)">';
          html += '<strong>' + escHtml(p.label) + '</strong>' + badge;
          if (p.description) html += '<br><span style="font-size:0.85em;color:var(--color-text-secondary)">' + escHtml(p.description) + '</span>';
          html += '</li>';
        });
        html += '</ul>';
        container.innerHTML = html;
      })
      .catch(function () {
        document.getElementById("schema-preds-list").innerHTML = '<p style="color:var(--color-text-danger)">読み込み失敗</p>';
      });
  }

  // ── Lecture Script Studio (Issue #70) ───────────────────────────────
  var lsState = {
    courseId: null,
    chunks: [],
    selectedChunkId: null,
    generating: false,
    view: "edit",
    evidenceView: "pdf",
    displayView: "preview",
    syncSpoken: true,
    pdfObjectUrl: null,
    pdfUrl: null,
    settings: {
      narration_persona: "",
      response_persona: "",
    },
    componentsByChunk: {},
    claimsByChunk: {},
    claimMetaByChunk: {},
    componentsBySection: {},
    graphByDocument: {},
    selectedScope: null,
    selectedTheoryComponentId: null,
    analysisStatus: null,
    pipelineTask: null,
    theoryLoading: false,
    claimsLoading: false,
    graphLoading: false,
    // Issue #232: 左ペインタブ
    leftTab: "course",
    courseStructure: null,
    courseComponents: null,
  };

  var lsPersonaOptions = [
    { id: "", label: "標準" },
    { id: "general_friendly", label: "サイエンス・コミュニケーター（一般 × フレンドリー）" },
    { id: "general_formal", label: "科学ジャーナリスト（一般 × フォーマル）" },
    { id: "expert_friendly", label: "研究室の共同研究者（専門 × フレンドリー）" },
    { id: "expert_formal", label: "学会発表／査読者（専門 × フォーマル）" },
  ];

  function lsBindMenu(triggerId, menuId) {
    var trigger = document.getElementById(triggerId);
    var menu = document.getElementById(menuId);
    if (!trigger || !menu) return;
    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      var willOpen = menu.hidden;
      document.querySelectorAll(".ls-menu").forEach(function (m) { m.hidden = true; });
      menu.hidden = !willOpen;
    });
    menu.addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", function () {
      menu.hidden = true;
    });
  }

  function lsCloseMenus() {
    document.querySelectorAll(".ls-menu").forEach(function (menu) {
      menu.hidden = true;
    });
  }

  function lsIsTheoryGraphView(view) {
    return view === "claims" || view === "theory" || view === "graph";
  }

  function lsUpdateWorkTabActive() {
    var view = lsState.view || "edit";
    var topView = lsIsTheoryGraphView(view) ? "claims" : view;
    document.querySelectorAll("#ls-work-tabs .ls-work-tab").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-ls-view") === topView);
    });
    var theoryTabs = document.getElementById("ls-theory-graph-tabs");
    if (theoryTabs) {
      theoryTabs.hidden = !lsIsTheoryGraphView(view);
      theoryTabs.querySelectorAll(".ls-work-tab").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-ls-view") === view);
      });
    }
  }

  function initLectureStudio() {
    var courseSelect = document.getElementById("ls-course-select");
    var pipelineFullBtn = document.getElementById("ls-pipeline-full-btn");
    var audioAllBtn = document.getElementById("ls-audio-all-btn");
    var settingsBtn = document.getElementById("ls-settings-btn");
    var exportBtn = document.getElementById("ls-export-btn");
    var pipelineMenuBtn = document.getElementById("ls-pipeline-menu-btn");
    var moreMenuBtn = document.getElementById("ls-more-menu-btn");
    var saveBtn = document.getElementById("ls-save-btn");
    var rewriteBtn = document.getElementById("ls-rewrite-btn");

    lsBindMenu("ls-pipeline-menu-btn", "ls-pipeline-menu");
    lsBindMenu("ls-more-menu-btn", "ls-more-menu");

    courseSelect.addEventListener("change", function () {
      var courseId = this.value;
      if (courseId) {
        lsState.courseId = courseId;
        // ボタンはチャンク読み込み完了後に lsRenderChunkList で制御するため
        // ここでは一旦無効化して読み込みを待つ
        pipelineFullBtn.disabled = true;
        audioAllBtn.disabled = true;
        settingsBtn.disabled = false;
        exportBtn.disabled = false;
        pipelineMenuBtn.disabled = true;
        moreMenuBtn.disabled = false;
        lsLoadSettings(courseId);
        lsLoadScripts(courseId);
      } else {
        lsState.courseId = null;
        lsState.chunks = [];
        lsState.selectedChunkId = null;
        lsState.componentsByChunk = {};
        lsState.claimsByChunk = {};
        lsState.claimMetaByChunk = {};
        lsState.componentsBySection = {};
        lsState.graphByDocument = {};
        lsState.selectedScope = null;
        lsState.selectedTheoryComponentId = null;
        lsState.analysisStatus = null;
        lsState.pipelineTask = null;
        lsState.settings = { narration_persona: "", response_persona: "" };
        lsState.courseStructure = null;
        lsState.courseComponents = null;
        pipelineFullBtn.disabled = true;
        audioAllBtn.disabled = true;
        settingsBtn.disabled = true;
        exportBtn.disabled = true;
        pipelineMenuBtn.disabled = true;
        moreMenuBtn.disabled = true;
        lsRenderChunkList();
        lsRenderLeftPanel();
        lsClearEditor();
      }
    });

    pipelineFullBtn.addEventListener("click", function () {
      if (!lsState.courseId || lsState.generating) return;
      lsCloseMenus();
      lsRunDocumentPipeline();
    });

    audioAllBtn.addEventListener("click", function () {
      if (!lsState.courseId || lsState.generating) return;
      lsCloseMenus();
      lsBatchAudio();
    });

    document.querySelectorAll(".ls-agent-stage-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!lsState.courseId || lsState.generating) return;
        lsCloseMenus();
        lsRunDocumentPipeline(btn.getAttribute("data-stage") || "");
      });
    });

    settingsBtn.addEventListener("click", function () {
      if (!lsState.courseId) return;
      lsCloseMenus();
      lsOpenSettingsModal();
    });

    exportBtn.addEventListener("click", function () {
      if (!lsState.courseId) return;
      lsCloseMenus();
      lsOpenExportModal();
    });

    document.getElementById("export-cancel-btn").addEventListener("click", function () {
      lsCloseExportModal();
    });

    document.getElementById("export-modal-overlay").addEventListener("click", function (e) {
      if (e.target === this) lsCloseExportModal();
    });

    document.getElementById("export-run-btn").addEventListener("click", function () {
      lsRunExport();
    });

    saveBtn.addEventListener("click", function () {
      lsSaveScript();
    });

    rewriteBtn.addEventListener("click", function () {
      lsRewriteScript();
    });

    // Issue #232: 左ペインタブ切り替え
    document.querySelectorAll(".ls-nav-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tab = btn.getAttribute("data-ls-nav");
        if (tab) lsSwitchNavTab(tab);
      });
    });

    document.getElementById("ls-work-tabs").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-ls-view]");
      if (!btn) return;
      lsState.view = btn.getAttribute("data-ls-view") || "edit";
      lsUpdateWorkTabActive();
      lsRenderWorkspace();
    });

    document.getElementById("ls-theory-graph-tabs").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-ls-view]");
      if (!btn) return;
      lsState.view = btn.getAttribute("data-ls-view") || "claims";
      lsUpdateWorkTabActive();
      lsRenderWorkspace();
    });

    // Load courses on tab activation
    onTabActivate("lecture-studio", function () {
      lsLoadCourses();
    });

    lsLoadCourses();
  }

  function lsLoadSettings(courseId) {
    apiFetch("/admin/courses/" + courseId + "/lecture-studio/settings")
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load settings");
        return res.json();
      })
      .then(function (settings) {
        if (lsState.courseId !== courseId) return;
        lsState.settings = {
          narration_persona: settings.narration_persona || "",
          response_persona: settings.response_persona || "",
        };
      })
      .catch(function () {
        lsState.settings = { narration_persona: "", response_persona: "" };
      });
  }

  function lsPersonaSelectHtml(id, selected) {
    var html = '<select id="' + escHtml(id) + '" class="ls-settings-select">';
    lsPersonaOptions.forEach(function (opt) {
      html += '<option value="' + escHtml(opt.id) + '"' + (opt.id === selected ? " selected" : "") + '>' + escHtml(opt.label) + '</option>';
    });
    html += '</select>';
    return html;
  }

  function lsOpenExportModal() {
    var overlay = document.getElementById("export-modal-overlay");
    document.getElementById("export-status").textContent = "";
    overlay.style.display = "flex";
  }

  function lsCloseExportModal() {
    document.getElementById("export-modal-overlay").style.display = "none";
  }

  function lsRunExport() {
    var scope = document.querySelector('input[name="export-scope"]:checked');
    var scopeVal = scope ? scope.value : "course";
    var includeSnippets = document.getElementById("export-opt-snippets").checked;
    var includeReview = document.getElementById("export-opt-review").checked;
    var includeNdjson = document.getElementById("export-opt-ndjson").checked;
    var includeDebug = document.getElementById("export-opt-debug").checked;

    var endpoint, scopeId;
    if (scopeVal === "course") {
      scopeId = lsState.courseId;
      endpoint = "/courses/" + scopeId + "/export-bundle";
    } else {
      var sel = lsState.selectedScope;
      scopeId = sel && sel.documentId ? sel.documentId : lsState.courseId;
      endpoint = scopeId && scopeVal === "document"
        ? "/documents/" + scopeId + "/export-bundle"
        : "/courses/" + lsState.courseId + "/export-bundle";
    }

    var statusEl = document.getElementById("export-status");
    var runBtn = document.getElementById("export-run-btn");
    statusEl.textContent = "生成中...";
    runBtn.disabled = true;

    var body = JSON.stringify({
      scope: scopeVal,
      include_source_snippets: includeSnippets,
      include_review_fields: includeReview,
      include_ndjson: includeNdjson,
      include_debug_data: includeDebug,
      include_llm_raw_outputs: false,
    });

    apiFetch(endpoint, { method: "POST", body: body })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (d) { throw new Error(d.detail || "エラーが発生しました"); });
        }
        var cd = res.headers.get("Content-Disposition") || "";
        var match = cd.match(/filename="?([^";\n]+)"?/);
        var filename = match ? match[1] : "episteme_export.zip";
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
        statusEl.textContent = "ダウンロードを開始しました";
        runBtn.disabled = false;
      })
      .catch(function (err) {
        statusEl.textContent = "エラー: " + (err.message || "不明なエラー");
        runBtn.disabled = false;
      });
  }

  function lsOpenSettingsModal() {
    var existing = document.getElementById("ls-settings-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "ls-settings-modal";
    overlay.className = "ls-settings-modal";
    overlay.innerHTML =
      '<div class="ls-settings-dialog">' +
        '<div class="ls-settings-head">' +
          '<h3>原稿スタジオ設定</h3>' +
          '<button id="ls-settings-close" class="lecture-chat-close" type="button">&times;</button>' +
        '</div>' +
        '<label class="ls-settings-field">' +
          '<span>読み上げテキストの解説モード</span>' +
          lsPersonaSelectHtml("ls-narration-persona", lsState.settings.narration_persona || "") +
        '</label>' +
        '<label class="ls-settings-field">' +
          '<span>質問への応答の解説モード</span>' +
          lsPersonaSelectHtml("ls-response-persona", lsState.settings.response_persona || "") +
        '</label>' +
        '<div id="ls-settings-status" class="upload-status" style="display:none"></div>' +
        '<div class="ls-settings-actions">' +
          '<button id="ls-settings-cancel" class="admin-action-btn" type="button">キャンセル</button>' +
          '<button id="ls-settings-save" class="admin-action-btn" type="button" style="background:var(--color-text-success);color:#fff">保存</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    function close() { overlay.remove(); }
    document.getElementById("ls-settings-close").addEventListener("click", close);
    document.getElementById("ls-settings-cancel").addEventListener("click", close);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });
    document.getElementById("ls-settings-save").addEventListener("click", function () {
      lsSaveSettings(overlay);
    });
  }

  function lsSaveSettings(modal) {
    var statusEl = document.getElementById("ls-settings-status");
    var saveBtn = document.getElementById("ls-settings-save");
    var settings = {
      narration_persona: document.getElementById("ls-narration-persona").value,
      response_persona: document.getElementById("ls-response-persona").value,
    };
    statusEl.textContent = "保存中...";
    statusEl.className = "upload-status upload-status-info";
    statusEl.style.display = "block";
    saveBtn.disabled = true;

    apiFetch("/admin/courses/" + lsState.courseId + "/lecture-studio/settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Save failed");
        return res.json();
      })
      .then(function (saved) {
        lsState.settings = {
          narration_persona: saved.narration_persona || "",
          response_persona: saved.response_persona || "",
        };
        modal.remove();
        lsShowProgress("原稿スタジオ設定を保存しました", "success");
      })
      .catch(function () {
        statusEl.textContent = "保存に失敗しました";
        statusEl.className = "upload-status upload-status-error";
        saveBtn.disabled = false;
      });
  }

  function lsLoadCourses() {
    var select = document.getElementById("ls-course-select");
    var currentVal = select.value;
    // Keep first placeholder option, remove the rest
    while (select.options.length > 1) select.remove(1);

    apiFetch("/admin/courses")
      .then(function (res) { return res.json(); })
      .then(function (courses) {
        courses.forEach(function (c) {
          var opt = document.createElement("option");
          opt.value = c.id;
          opt.textContent = c.title;
          select.appendChild(opt);
        });
        if (currentVal) select.value = currentVal;
      })
      .catch(function () {});
  }

  function lsLoadScripts(courseId) {
    var listEl = document.getElementById("ls-chunk-list");
    listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>';

    apiFetch("/admin/courses/" + courseId + "/lecture-scripts")
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load scripts");
        return res.json();
      })
      .then(function (chunks) {
        lsState.chunks = chunks;
        lsState.selectedChunkId = null;
        lsState.componentsByChunk = {};
        lsState.claimsByChunk = {};
        lsState.claimMetaByChunk = {};
        lsState.componentsBySection = {};
        lsState.graphByDocument = {};
        lsState.selectedScope = null;
        lsState.selectedTheoryComponentId = null;
        lsState.analysisStatus = null;
        lsState.pipelineTask = null;
        lsRenderChunkList();
        lsRenderLeftPanel();
        lsClearEditor();
        lsRefreshAnalysisStatus(courseId);
        // Issue #139: 進行中タスクがあればポーリング状態に復帰
        lsCheckActiveTask(courseId);
        // Issue #232: コース構造とコンポーネントを非同期ロード
        lsLoadCourseStructure(courseId);
        lsLoadCourseComponents(courseId);
      })
      .catch(function () {
        listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">読み込みに失敗しました</div>';
      });
  }

  // ── Issue #232: 左ペインタブ管理 ───────────────────────────────────────

  function lsSwitchNavTab(tab) {
    lsState.leftTab = tab;
    document.querySelectorAll(".ls-nav-tab").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-ls-nav") === tab);
    });
    var courseList = document.getElementById("ls-course-list");
    var chunkList = document.getElementById("ls-chunk-list");
    var componentsList = document.getElementById("ls-components-list");
    if (courseList) courseList.hidden = tab !== "course";
    if (chunkList) chunkList.hidden = tab !== "document";
    if (componentsList) componentsList.hidden = tab !== "components";
    if (tab === "course") lsRenderCourseStructure();
    if (tab === "document") lsRenderChunkList();
    if (tab === "components") lsRenderComponentsTab();
  }

  function lsRenderLeftPanel() {
    lsSwitchNavTab(lsState.leftTab || "course");
  }

  function lsLoadCourseStructure(courseId) {
    var el = document.getElementById("ls-course-list");
    if (!el) return;
    if (lsState.leftTab === "course") {
      el.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>';
    }
    apiFetch("/admin/courses/" + courseId + "/lecture-studio/course-structure")
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data || lsState.courseId !== courseId) return;
        lsState.courseStructure = data;
        if (lsState.leftTab === "course") lsRenderCourseStructure();
      })
      .catch(function () {
        lsState.courseStructure = null;
        if (lsState.leftTab === "course") lsRenderCourseStructure();
      });
  }

  function lsLoadCourseComponents(courseId) {
    var el = document.getElementById("ls-components-list");
    if (!el) return;
    if (lsState.leftTab === "components") {
      el.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>';
    }
    apiFetch("/admin/courses/" + courseId + "/lecture-studio/components")
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data || lsState.courseId !== courseId) return;
        lsState.courseComponents = data;
        if (lsState.leftTab === "components") lsRenderComponentsTab();
      })
      .catch(function () {
        lsState.courseComponents = null;
        if (lsState.leftTab === "components") lsRenderComponentsTab();
      });
  }

  function lsRenderCourseStructure() {
    var el = document.getElementById("ls-course-list");
    if (!el) return;
    if (!lsState.courseId) {
      el.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">コースを選択してください</div>';
      return;
    }
    if (!lsState.courseStructure) {
      el.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>';
      return;
    }
    var s = lsState.courseStructure;
    var statusLabel = { draft: "未着手", partial: "一部生成", generated: "生成済み", audio_generated: "音声生成済み", no_chunks: "チャンクなし" };
    var html = '<div class="ls-course-header">' +
      '<div class="ls-doc-title">' + escHtml(s.title || "コース") + '</div>' +
      '<div class="ls-doc-meta">' + escHtml(statusLabel[s.course_status] || s.course_status || "") +
      (s.total_chunks > 0 ? ' (' + (s.generated_chunks || 0) + '/' + s.total_chunks + ')' : '') + '</div>' +
      '</div>';

    if (!s.chapters || !s.chapters.length) {
      html += '<div style="padding:12px 16px;color:var(--color-text-tertiary);font-size:12px">章構造がありません。<br>コースビルダーでコース設計を完了してください。</div>';
      el.innerHTML = html;
      return;
    }

    s.chapters.forEach(function (chapter, ci) {
      html += '<div class="ls-course-chapter">' +
        '<span class="ls-doc-title">第' + (ci + 1) + '章: ' + escHtml(chapter.title || "") + '</span>' +
        '</div>';
      (chapter.topics || []).forEach(function (topic, ti) {
        var statusCls = "ls-topic-status-" + (topic.status || "draft");
        html += '<div class="ls-course-topic" data-chapter="' + ci + '" data-topic="' + ti + '">' +
          '<span class="ls-topic-dot ' + statusCls + '"></span>' +
          '<span class="ls-topic-label">' + (ci + 1) + '.' + (ti + 1) + ' ' + escHtml(topic.title || "") + '</span>' +
          '</div>';
      });
    });

    el.innerHTML = html;

    el.querySelectorAll(".ls-course-topic").forEach(function (item) {
      item.addEventListener("click", function () {
        el.querySelectorAll(".ls-course-topic").forEach(function (t) { t.classList.remove("active"); });
        item.classList.add("active");
      });
    });
  }

  function lsRenderComponentsTab() {
    var el = document.getElementById("ls-components-list");
    if (!el) return;
    if (!lsState.courseId) {
      el.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">コースを選択してください</div>';
      return;
    }
    var data = lsState.courseComponents;
    if (!data) {
      el.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>';
      return;
    }
    var analysisLabel = {
      not_started: "未解析",
      pending: "解析待ち",
      running: "解析中",
      completed: "解析済み",
      failed: "解析失敗",
    };
    var html = '<div class="ls-course-header">' +
      '<div class="ls-doc-title">理論コンポーネント</div>' +
      '<div class="ls-doc-meta">' + escHtml(analysisLabel[data.analysis_status] || data.analysis_status || "") +
      ' / ' + (data.components ? data.components.length : 0) + '件</div>' +
      '</div>';

    if (data.analysis_status !== "completed" && (!data.components || !data.components.length)) {
      html += '<div style="padding:12px 16px;color:var(--color-text-tertiary);font-size:12px">' +
        (data.analysis_status === "running" ? "Agent解析実行中です..." :
         data.analysis_status === "pending" ? "Agent解析待ちです..." :
         "Agent解析が未完了です。<br>パイプラインを実行してコンポーネントを生成してください。") +
        '</div>';
      el.innerHTML = html;
      return;
    }

    var statusLabel = { teacher_reviewed: "承認済み", rejected: "却下", draft: "下書き", candidate: "候補" };
    var reviewLabel = { teacher_review_required: "要確認", approved: "承認済み", rejected: "却下" };

    (data.components || []).forEach(function (c) {
      var depEdges = (data.all_edges || []).filter(function (e) {
        return e.target === c.id || e.from === c.id;
      });
      var depCount = depEdges.length;
      html +=
        '<div class="ls-component-item" data-component-id="' + escHtml(c.id) + '">' +
          '<div class="ls-component-head">' +
            '<span class="ls-component-name">' + escHtml(c.name || "無題") + '</span>' +
            '<span class="ls-theory-badge">' + escHtml(statusLabel[c.status] || c.status || "") + '</span>' +
          '</div>' +
          '<div class="ls-component-type">' + escHtml(c.component_type || "") + '</div>' +
          '<div class="ls-component-summary">' + escHtml((c.summary || "").substring(0, 80) + (c.summary && c.summary.length > 80 ? "..." : "")) + '</div>' +
          '<div class="ls-component-meta">' +
            'inputs: ' + (c.inputs ? c.inputs.length : 0) +
            ' / outputs: ' + (c.outputs ? c.outputs.length : 0) +
            ' / precond: ' + (c.preconditions ? c.preconditions.length : 0) +
            (depCount > 0 ? ' / 依存: ' + depCount : '') +
          '</div>' +
          '<div class="ls-component-meta">' +
            escHtml(reviewLabel[c.review_status] || c.review_status || "") +
            ' / ' + escHtml(c.maturity_level || "") +
          '</div>' +
        '</div>';
    });

    if (data.all_edges && data.all_edges.length) {
      html += '<div class="ls-course-header" style="margin-top:8px">' +
        '<div class="ls-doc-title">依存関係</div>' +
        '<div class="ls-doc-meta">' + data.all_edges.length + '件</div>' +
        '</div>';
      (data.all_edges || []).forEach(function (edge) {
        var rel = edge.relation_type || edge.edge_type || edge.type || "depends_on";
        var src = edge.source_name || edge.from || (edge.source || "").substring(0, 8);
        var tgt = edge.target_name || edge.to || (edge.target || "").substring(0, 8);
        html += '<div class="ls-component-edge">' +
          '<span class="ls-theory-ref">' + escHtml(rel) + '</span>' +
          ' ' + escHtml(src) + ' → ' + escHtml(tgt) +
          '</div>';
      });
    }

    el.innerHTML = html;

    el.querySelectorAll(".ls-component-item").forEach(function (item) {
      item.addEventListener("click", function () {
        var componentId = item.getAttribute("data-component-id");
        var component = (lsState.courseComponents && lsState.courseComponents.components || []).find(function (c) { return c.id === componentId; });
        if (component) lsSelectCourseComponent(component);
      });
    });
  }

  // ────────────────────────────────────────────────────────────────────────────

  function lsRefreshAnalysisStatus(courseId) {
    if (!courseId) return;
    apiFetch("/admin/courses/" + courseId + "/analysis-status")
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load analysis status");
        return res.json();
      })
      .then(function (status) {
        if (lsState.courseId !== courseId) return;
        lsState.analysisStatus = status || null;
        lsUpdateCourseControls();
      })
      .catch(function () {
        lsState.analysisStatus = null;
        lsUpdateCourseControls();
      });
  }

  // Issue #139: コースに進行中のタスクがあれば、ボタンを無効化しポーリングを復帰させる
  function lsCheckActiveTask(courseId) {
    apiFetch("/admin/courses/" + courseId + "/tasks/active")
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (task) {
        if (!task || !task.task_id) return;
        // コース選択が切り替わっていたら無視
        if (lsState.courseId !== courseId) return;

        var rd = task.result_data || {};
        var totalChunks = rd.total_chunks || 0;

        if (task.task_type === "audio_generation") {
          lsState.pipelineTask = { step: "audio", status: "running" };
          lsSetCourseTaskBusy(true);
          lsShowProgress(
            "音声生成が進行中です... (進捗: " + (rd.progress || 0) + "%)",
            "info"
          );
          _lsPollAudioTask(task.task_id, totalChunks);
        } else if (task.task_type === "script_generation") {
          lsState.pipelineTask = { step: "script", status: "running" };
          lsSetCourseTaskBusy(true);
          lsShowProgress(
            "スクリプト生成が進行中です... (進捗: " + (rd.progress || 0) + "%)",
            "info"
          );
          _lsPollGenerateTask(task.task_id, totalChunks);
        } else if (task.task_type === "structure_reanalysis") {
          lsState.pipelineTask = { step: "structure", status: "running" };
          lsSetCourseTaskBusy(true);
          lsShowProgress(
            "構造の再解析が進行中です... (進捗: " + (rd.progress || 0) + "%)",
            "info"
          );
          _lsPollStructureTask(task.task_id, rd.total_materials || 0);
        } else if (task.task_type === "document_pipeline") {
          var targetStage = rd.target_stage || "";
          var label = targetStage ? lsAgentStageLabels[targetStage] || targetStage : "パイプライン全実行";
          lsState.pipelineTask = targetStage
            ? { step: "document_pipeline", stage: targetStage, status: "running" }
            : { step: "document_pipeline", status: "running" };
          lsSetCourseTaskBusy(true);
          lsShowProgress(label + "が進行中です... (進捗: " + (rd.progress || 0) + "%)", "info");
          lsPollGenericCourseTask(task.task_id, label, "document_pipeline", targetStage);
        } else if (
          task.task_type === "claim_extraction" ||
          task.task_type === "component_assembly" ||
          task.task_type === "component_graph_update" ||
          task.task_type === "analysis_pipeline"
        ) {
          lsSetCourseTaskBusy(true);
          var label = task.task_type === "claim_extraction" ? "構成要素の抽出" :
            task.task_type === "component_assembly" ? "論理要素の抽出" :
            task.task_type === "component_graph_update" ? "グラフ更新" : "解析パイプライン";
          var step = task.task_type === "claim_extraction" ? "claims" :
            task.task_type === "component_assembly" ? "components" :
            task.task_type === "component_graph_update" ? "graph" : "structure";
          lsState.pipelineTask = { step: step, status: "running" };
          lsShowProgress(label + "が進行中です... (進捗: " + (rd.progress || 0) + "%)", "info");
          lsPollGenericCourseTask(task.task_id, label, step);
        }
      })
      .catch(function () { /* ignore */ });
  }

  function lsCurrentDocumentId() {
    var chunk = lsGetSelectedChunk();
    if (chunk && (chunk.document_id || chunk.material_id)) return chunk.document_id || chunk.material_id;
    if (lsState.chunks && lsState.chunks.length) return lsState.chunks[0].document_id || lsState.chunks[0].material_id || "";
    return "";
  }

  function lsSectionIdForChunk(chunk) {
    if (chunk && chunk.section_id) return chunk.section_id;
    var docId = (chunk && (chunk.document_id || chunk.material_id)) || "document";
    if (chunk && chunk.page_start) return docId + ":page_" + chunk.page_start;
    var idx = chunk && chunk.chunk_index ? Number(chunk.chunk_index) : 0;
    return docId + ":section_" + (Math.floor(idx / 4) + 1);
  }

  function lsDocumentStructure() {
    var docs = {};
    (lsState.chunks || []).forEach(function (chunk, i) {
      var docId = chunk.document_id || chunk.material_id || "document";
      if (!docs[docId]) docs[docId] = { id: docId, label: chunk.material_id || "論文", sections: {} };
      var sid = lsSectionIdForChunk(chunk);
      if (!docs[docId].sections[sid]) {
        docs[docId].sections[sid] = {
          id: sid,
          label: chunk.section_title || (chunk.page_start ? "p." + chunk.page_start : "section " + (Object.keys(docs[docId].sections).length + 1)),
          level: chunk.section_level || 0,
          order: chunk.section_order || Object.keys(docs[docId].sections).length + 1,
          chunks: [],
        };
      }
      docs[docId].sections[sid].chunks.push(Object.assign({ _position: i }, chunk));
    });
    return docs;
  }

  function lsSectionState(section) {
    var key = section.id;
    var components = lsState.componentsBySection[key] || [];
    var warnings = components.filter(function (c) {
      return c.validation_warnings && c.validation_warnings.length;
    }).length;
    if (!components.length) return "論理要素: 0";
    return "論理要素: " + components.length + (warnings ? " / 警告: " + warnings : "");
  }

  function lsClaimState(chunkId) {
    var claims = lsState.claimsByChunk[chunkId] || [];
    if (!claims.length) return "";
    var unreviewed = claims.filter(function (claim) {
      return claim.review_status !== "teacher_approved";
    }).length;
    return ' <span class="ls-chunk-theory-state">主張: ' + claims.length + " / 未レビュー: " + unreviewed + "</span>";
  }

  function lsRenderChunkList() {
    var listEl = document.getElementById("ls-chunk-list");
    var pipelineFullBtn = document.getElementById("ls-pipeline-full-btn");
    var audioAllBtn = document.getElementById("ls-audio-all-btn");
    var settingsBtn = document.getElementById("ls-settings-btn");

    if (!lsState.chunks || lsState.chunks.length === 0) {
      listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">' +
        '教材のチャンクが見つかりません。<br>' +
        '教材がコースに紐づけられているか、PDF解析が完了しているかを確認してください。</div>';
      if (pipelineFullBtn) pipelineFullBtn.disabled = true;
      if (audioAllBtn) audioAllBtn.disabled = true;
      if (settingsBtn) settingsBtn.disabled = !lsState.courseId;
      document.querySelectorAll(".ls-agent-stage-btn").forEach(function (btn) { btn.disabled = true; });
      lsUpdateCourseControls();
      return;
    }

    lsUpdateCourseControls();
    var html = "";
    var docs = lsDocumentStructure();
    Object.keys(docs).forEach(function (docId) {
      var docActive = lsState.selectedScope && lsState.selectedScope.type === "paper" && lsState.selectedScope.documentId === docId ? " active" : "";
      var graph = lsState.graphByDocument[docId];
      var graphState = graph && graph.validation_results ? "グラフ警告: " + graph.validation_results.length : "グラフ警告: -";
      html += '<div class="ls-doc-node' + docActive + '" data-doc-id="' + escHtml(docId) + '">' +
        '<span class="ls-doc-title">' + escHtml(docs[docId].label || "論文") + '</span>' +
        '<span class="ls-doc-meta">' + escHtml(graphState) + '</span>' +
        '</div>';
      Object.keys(docs[docId].sections).forEach(function (sid) {
        var section = docs[docId].sections[sid];
        var sectionActive = lsState.selectedScope && lsState.selectedScope.type === "section" && lsState.selectedScope.sectionId === sid ? " active" : "";
        html += '<div class="ls-section-node' + sectionActive + '" data-section-id="' + escHtml(sid) + '" data-doc-id="' + escHtml(docId) + '">' +
          '<span class="ls-doc-title">' + escHtml(section.label) + '</span>' +
          '<span class="ls-doc-meta">' + escHtml(lsSectionState(section)) + '</span>' +
          '</div>';
        section.chunks.forEach(function (c, i) {
          var active = c.chunk_id === lsState.selectedChunkId && (!lsState.selectedScope || lsState.selectedScope.type === "chunk") ? " active" : "";
          var preview = (c.text || "").substring(0, 40).replace(/\n/g, " ");
          if (c.text && c.text.length > 40) preview += "...";
          var theoryState = lsChunkTheoryState(c.chunk_id) + lsClaimState(c.chunk_id);
          html +=
            '<div class="ls-chunk-item ls-chunk-child' + active + '" data-chunk-id="' + escHtml(c.chunk_id) + '">' +
              '<span class="ls-chunk-status ' + escHtml(c.status) + '"></span>' +
              '<span class="ls-chunk-label">chunk ' + (c.chunk_index || i) + " " + escHtml(preview) + theoryState + '</span>' +
            '</div>';
        });
      });
    });
    listEl.innerHTML = html;

    // Bind click handlers
    listEl.querySelectorAll(".ls-doc-node").forEach(function (item) {
      item.addEventListener("click", function () {
        lsSelectPaper(this.getAttribute("data-doc-id"));
      });
    });
    listEl.querySelectorAll(".ls-section-node").forEach(function (item) {
      item.addEventListener("click", function () {
        lsSelectSection(this.getAttribute("data-doc-id"), this.getAttribute("data-section-id"));
      });
    });
    listEl.querySelectorAll(".ls-chunk-item").forEach(function (item) {
      item.addEventListener("click", function () {
        var chunkId = this.getAttribute("data-chunk-id");
        lsSelectChunk(chunkId);
      });
    });
  }

  function lsSelectChunk(chunkId) {
    lsState.selectedChunkId = chunkId;
    lsState.selectedScope = { type: "chunk", chunkId: chunkId };
    var chunk = lsGetSelectedChunk();
    if (!chunk) return;

    // Update active class
    document.querySelectorAll(".ls-chunk-item").forEach(function (el) {
      el.classList.remove("active");
      if (el.getAttribute("data-chunk-id") === chunkId) el.classList.add("active");
    });

    lsState.displayView = "preview";
    lsRenderWorkspace();
    lsLoadTheoryComponentsForChunk(chunkId);
    lsLoadClaimsForChunk(chunk);

    document.getElementById("ls-rewrite-prompt").disabled = false;
    document.getElementById("ls-rewrite-btn").disabled = false;
    document.getElementById("ls-save-btn").disabled = false;

    // Show formulas
    lsRenderFormulas(chunk.formulas || []);
  }

  function lsSelectSection(documentId, sectionId) {
    var first = null;
    (lsState.chunks || []).some(function (chunk) {
      if ((chunk.document_id || chunk.material_id) === documentId && lsSectionIdForChunk(chunk) === sectionId) {
        first = chunk;
        return true;
      }
      return false;
    });
    if (first) lsState.selectedChunkId = first.chunk_id;
    lsState.selectedScope = { type: "section", documentId: documentId, sectionId: sectionId };
    lsState.view = "theory";
    lsUpdateWorkTabActive();
    lsLoadSectionComponents(documentId, sectionId);
    lsRenderChunkList();
    lsRenderWorkspace();
  }

  function lsSelectPaper(documentId) {
    var first = (lsState.chunks || []).find(function (chunk) {
      return (chunk.document_id || chunk.material_id) === documentId;
    });
    if (first) lsState.selectedChunkId = first.chunk_id;
    lsState.selectedScope = { type: "paper", documentId: documentId };
    lsState.view = "graph";
    lsUpdateWorkTabActive();
    lsLoadComponentGraph(documentId, false);
    lsRenderChunkList();
    lsRenderWorkspace();
  }

  function lsGetSelectedCourseComponent() {
    var componentId = lsState.selectedTheoryComponentId;
    if (!componentId || !lsState.courseComponents || !lsState.courseComponents.components) return null;
    return lsState.courseComponents.components.find(function (component) {
      return component.id === componentId;
    }) || null;
  }

  function lsSelectCourseComponent(component) {
    if (!component) return;
    lsState.selectedTheoryComponentId = component.id;
    lsState.selectedScope = { type: "component", componentId: component.id };
    lsState.view = "theory";
    lsUpdateWorkTabActive();
    document.querySelectorAll(".ls-component-item").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-component-id") === component.id);
    });
    lsRenderWorkspace();
  }

  function lsGetSelectedChunk() {
    for (var i = 0; i < lsState.chunks.length; i++) {
      if (lsState.chunks[i].chunk_id === lsState.selectedChunkId) return lsState.chunks[i];
    }
    return null;
  }

  function lsEnsureWorkspace() {
    var workspace = document.getElementById("ls-workspace");
    if (document.getElementById("ls-display-text")) return workspace;
    var tpl = document.getElementById("ls-workspace-template");
    workspace.innerHTML = "";
    workspace.appendChild(tpl.content.cloneNode(true));

    document.getElementById("ls-evidence-tabs").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-ls-evidence]");
      if (!btn) return;
      lsState.evidenceView = btn.getAttribute("data-ls-evidence") || "pdf";
      lsRenderWorkspace();
    });
    document.getElementById("ls-display-tabs").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-ls-display]");
      if (!btn) return;
      lsState.displayView = btn.getAttribute("data-ls-display") || "preview";
      lsRenderWorkspace();
    });
    document.getElementById("ls-sync-spoken").addEventListener("change", function () {
      lsState.syncSpoken = this.checked;
      if (this.checked) {
        document.getElementById("ls-spoken-text").value = document.getElementById("ls-display-text").value;
      }
      lsRenderWorkspace();
    });
    document.getElementById("ls-display-text").addEventListener("input", function () {
      var chunk = lsGetSelectedChunk();
      if (chunk) {
        chunk.display_text = this.value;
        chunk.text = this.value;
      }
      if (lsState.syncSpoken) {
        document.getElementById("ls-spoken-text").value = this.value;
        if (chunk) chunk.spoken_text = this.value;
      }
      lsRenderDisplayPreview();
    });
    document.getElementById("ls-spoken-text").addEventListener("input", function () {
      var chunk = lsGetSelectedChunk();
      if (chunk) chunk.spoken_text = this.value;
    });
    var extractBtn = document.getElementById("ls-extract-theory-btn");
    if (extractBtn) {
      extractBtn.addEventListener("click", function () {
        var chunk = lsGetSelectedChunk();
        if (!chunk || lsState.theoryLoading) return;
        if (lsState.selectedScope && lsState.selectedScope.type === "section") {
          lsAssembleSectionComponents(lsState.selectedScope.documentId, lsState.selectedScope.sectionId);
        } else {
          lsExtractTheoryComponents(chunk.chunk_id);
        }
      });
    }
    var claimsBtn = document.getElementById("ls-extract-claims-btn");
    if (claimsBtn) {
      claimsBtn.addEventListener("click", function () {
        var chunk = lsGetSelectedChunk();
        if (!chunk || lsState.claimsLoading) return;
        lsExtractClaims(chunk);
      });
    }
    var graphBtn = document.getElementById("ls-refresh-graph-btn");
    if (graphBtn) {
      graphBtn.addEventListener("click", function () {
        var docId = lsCurrentDocumentId();
        if (!docId || lsState.graphLoading) return;
        lsLoadComponentGraph(docId, true);
      });
    }
    return workspace;
  }

  function lsBindTheoryCardActions(container) {
    if (!container) return;
    container.querySelectorAll("[data-theory-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var card = this.closest(".ls-theory-card");
        var component = lsFindTheoryComponent(card.getAttribute("data-component-id")) || lsGetSelectedCourseComponent();
        var action = this.getAttribute("data-theory-action");
        if (!component) return;
        if (action === "open") lsOpenTheoryDetail(component);
        if (action === "insert") lsInsertTheoryChip(component);
        if (action === "approve") lsSaveTheoryComponent(component, { status: "teacher_reviewed" });
        if (action === "reject") lsRejectTheoryComponent(component);
      });
    });
  }

  function lsRenderSelectedCourseComponent(component) {
    lsEnsureWorkspace();
    var metaEl = document.getElementById("ls-chunk-meta");
    if (metaEl) metaEl.textContent = "論理コンポーネント: " + (component.name || "無題");

    var sourceEl = document.getElementById("ls-source-text");
    var displayEl = document.getElementById("ls-display-text");
    var spokenEl = document.getElementById("ls-spoken-text");
    if (sourceEl) sourceEl.textContent = "";
    if (displayEl) {
      displayEl.value = "";
      displayEl.disabled = true;
      displayEl.hidden = true;
    }
    if (spokenEl) {
      spokenEl.value = "";
      spokenEl.disabled = true;
      spokenEl.hidden = true;
    }

    document.getElementById("ls-display-tabs").hidden = true;
    document.getElementById("ls-evidence-tabs").hidden = true;
    document.getElementById("ls-left-pane").hidden = true;
    document.getElementById("ls-right-pane").hidden = true;
    var splitEl = document.querySelector("#ls-workspace .ls-split");
    if (splitEl) splitEl.hidden = true;
    document.getElementById("ls-structure-panel").hidden = true;
    document.getElementById("ls-theory-panel").hidden = false;
    document.getElementById("ls-claims-panel").hidden = true;
    document.getElementById("ls-graph-panel").hidden = true;
    document.getElementById("ls-sync-row").hidden = true;
    document.getElementById("ls-display-preview").hidden = true;
    document.getElementById("ls-pdf-view").hidden = true;

    var extractBtn = document.getElementById("ls-extract-theory-btn");
    if (extractBtn) {
      extractBtn.textContent = "論理要素候補を抽出";
      extractBtn.disabled = true;
    }
    var container = document.getElementById("ls-theory-components");
    if (!container) return;
    container.innerHTML =
      '<div class="ls-theory-current">選択中コンポーネント</div>' +
      lsTheoryCardHtml(component);
    lsBindTheoryCardActions(container);
  }

  function lsRenderWorkspace() {
    var chunk = lsGetSelectedChunk();
    var metaEl = document.getElementById("ls-chunk-meta");
    lsUpdateWorkTabActive();
    if (lsState.selectedScope && lsState.selectedScope.type === "component") {
      var selectedComponent = lsGetSelectedCourseComponent();
      if (selectedComponent) {
        lsRenderSelectedCourseComponent(selectedComponent);
        lsUpdateAssistantContext();
        return;
      }
    }
    if (!chunk && lsState.view === "graph" && lsState.chunks && lsState.chunks.length) {
      chunk = lsState.chunks[0];
      lsState.selectedChunkId = chunk.chunk_id;
      if (!lsState.selectedScope) {
        lsState.selectedScope = {
          type: "paper",
          documentId: chunk.document_id || chunk.material_id || "",
        };
      }
    }
    if (!chunk) {
      document.getElementById("ls-workspace").innerHTML = '<div class="ls-empty-state">チャンクを選択すると編集ワークベンチが表示されます</div>';
      metaEl.textContent = "チャンクを選択してください";
      lsUpdateAssistantContext();
      return;
    }

    lsEnsureWorkspace();
    metaEl.textContent = "チャンク #" + (chunk.chunk_index || 0) +
      (chunk.page_start ? " / PDF p." + chunk.page_start : "") +
      (chunk.material_id ? " / " + chunk.material_id : "");

    var sourceEl = document.getElementById("ls-source-text");
    var displayEl = document.getElementById("ls-display-text");
    var spokenEl = document.getElementById("ls-spoken-text");
    sourceEl.textContent = chunk.raw_text || chunk.text || "(抽出テキストなし)";
    if (displayEl.value !== (chunk.display_text || chunk.text || "")) displayEl.value = chunk.display_text || chunk.text || "";
    if (spokenEl.value !== (chunk.spoken_text || displayEl.value || "")) spokenEl.value = chunk.spoken_text || displayEl.value || "";
    displayEl.disabled = false;
    spokenEl.disabled = false;

    var isStructure = lsState.view === "structure";
    var isAudio = lsState.view === "audio";
    var isCompare = lsState.view === "compare";
    var isTheory = lsState.view === "theory";
    var isClaims = lsState.view === "claims";
    var isGraph = lsState.view === "graph";
    var splitEl = document.querySelector("#ls-workspace .ls-split");
    if (splitEl) splitEl.hidden = isStructure || isTheory || isClaims || isGraph;
    document.getElementById("ls-structure-panel").hidden = !isStructure;
    document.getElementById("ls-theory-panel").hidden = !isTheory;
    document.getElementById("ls-claims-panel").hidden = !isClaims;
    document.getElementById("ls-graph-panel").hidden = !isGraph;
    document.getElementById("ls-sync-row").hidden = !isAudio;
    document.getElementById("ls-sync-spoken").checked = lsState.syncSpoken;

    document.getElementById("ls-left-pane").hidden = isStructure || isAudio || isTheory || isClaims || isGraph;
    document.getElementById("ls-right-pane").hidden = isStructure || isTheory || isClaims || isGraph;

    document.getElementById("ls-evidence-tabs").querySelectorAll(".ls-mini-tab").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-ls-evidence") === lsState.evidenceView);
    });
    document.getElementById("ls-display-tabs").querySelectorAll(".ls-mini-tab").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-ls-display") === lsState.displayView);
    });

    if (isTheory) {
      document.getElementById("ls-display-tabs").hidden = true;
      spokenEl.hidden = true;
      displayEl.hidden = true;
      document.getElementById("ls-display-preview").hidden = true;
      lsRenderTheoryPanel(chunk);
    } else if (isClaims) {
      document.getElementById("ls-display-tabs").hidden = true;
      spokenEl.hidden = true;
      displayEl.hidden = true;
      document.getElementById("ls-display-preview").hidden = true;
      lsRenderClaimsPanel(chunk);
    } else if (isGraph) {
      document.getElementById("ls-display-tabs").hidden = true;
      spokenEl.hidden = true;
      displayEl.hidden = true;
      document.getElementById("ls-display-preview").hidden = true;
      var graphDocumentId = lsCurrentDocumentId();
      if (graphDocumentId && !lsState.graphByDocument[graphDocumentId] && !lsState.graphLoading) {
        lsLoadComponentGraph(graphDocumentId, false);
      }
      lsRenderGraphPanel(graphDocumentId);
    } else if (isAudio) {
      document.getElementById("ls-right-title").textContent = "読み上げテキスト";
      document.getElementById("ls-display-tabs").hidden = true;
      document.getElementById("ls-display-preview").hidden = true;
      displayEl.hidden = true;
      spokenEl.hidden = false;
      spokenEl.disabled = lsState.syncSpoken;
    } else {
      document.getElementById("ls-right-title").textContent = "表示テキスト";
      document.getElementById("ls-display-tabs").hidden = false;
      spokenEl.hidden = true;
      displayEl.hidden = lsState.displayView !== "script";
      document.getElementById("ls-display-preview").hidden = lsState.displayView !== "preview";
      lsRenderDisplayPreview();
    }

    sourceEl.hidden = lsState.evidenceView !== "extract";
    document.getElementById("ls-pdf-view").hidden = lsState.evidenceView !== "pdf";
    if (!isStructure && !isAudio && !isTheory && !isClaims && !isGraph && lsState.evidenceView === "pdf") lsLoadPdfForChunk(chunk);
    if (isCompare) {
      lsState.displayView = "preview";
      document.getElementById("ls-display-tabs").querySelectorAll(".ls-mini-tab").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-ls-display") === "preview");
      });
      displayEl.hidden = true;
      document.getElementById("ls-display-preview").hidden = false;
    }
    if (isStructure) lsRenderStructure(chunk);
    lsUpdateAssistantContext();
  }

  function lsUpdateAssistantContext() {
    var label = document.getElementById("ls-assistant-label");
    var promptEl = document.getElementById("ls-rewrite-prompt");
    var btn = document.getElementById("ls-rewrite-btn");
    if (!label || !promptEl || !btn) return;
    var view = lsState.view || "edit";
    var config = {
      compare: {
        label: "表示テキスト・数式への提案:",
        placeholder: "例: 原典と照合して、数式プレースホルダーと表示文を整えて",
        button: "表示・数式を提案",
      },
      edit: {
        label: "表示テキスト・数式への提案:",
        placeholder: "例: 前提知識の説明を1文追加し、数式をプレースホルダー化して",
        button: "表示・数式を書き換え",
      },
      structure: {
        label: "構造への提案:",
        placeholder: "例: このチャンクのDSL・変数・関係の見落としを指摘して",
        button: "構造を提案",
      },
      theory: {
        label: "論理要素への提案:",
        placeholder: "例: 成立条件、制約、禁止条件、依存概念を一般知識も使って補って",
        button: "論理要素を提案",
      },
      claims: {
        label: "主張への提案:",
        placeholder: "例: 原文に基づく主張、仮定、式、注意条件を見直して",
        button: "主張を提案",
      },
      graph: {
        label: "構造グラフへの提案:",
        placeholder: "例: 論理要素間の接続不足や未レビュー箇所を指摘して",
        button: "グラフを提案",
      },
      audio: {
        label: "読み上げ文言への提案:",
        placeholder: "例: 音声で自然に聞こえるように数式と専門語を読み下して",
        button: "読み上げ文を提案",
      },
    }[view] || {
      label: "AIへの指示:",
      placeholder: "改善指示を入力してください",
      button: "AIで提案",
    };
    label.textContent = config.label;
    promptEl.placeholder = config.placeholder;
    btn.textContent = config.button;
  }

  function lsChunkTheoryState(chunkId) {
    var components = lsState.componentsByChunk[chunkId] || [];
    if (!components.length) return "";
    var warnings = components.filter(function (c) {
      return c.validation_warnings && c.validation_warnings.length;
    }).length;
    var reviewed = components.filter(function (c) { return c.status === "teacher_reviewed"; }).length;
    var blackbox = components.filter(function (c) {
      return c.blackbox_policy && c.blackbox_policy.default_level === "io_only";
    }).length;
    var label = warnings ? " 警告 " + warnings :
      reviewed ? " 承認済み " + components.length + " 論理要素" :
      blackbox ? " IOのみで利用可" :
      " レビュー待ち";
    return '<span class="ls-chunk-theory-state">' + escHtml(label) + '</span>';
  }

  function lsFindTheoryComponent(componentId) {
    var keys = Object.keys(lsState.componentsByChunk || {});
    for (var i = 0; i < keys.length; i++) {
      var list = lsState.componentsByChunk[keys[i]] || [];
      for (var j = 0; j < list.length; j++) {
        if (String(list[j].id) === String(componentId)) return list[j];
      }
    }
    keys = Object.keys(lsState.componentsBySection || {});
    for (i = 0; i < keys.length; i++) {
      list = lsState.componentsBySection[keys[i]] || [];
      for (j = 0; j < list.length; j++) {
        if (String(list[j].id) === String(componentId)) return list[j];
      }
    }
    return null;
  }

  function lsSetTheoryStatus(message, type) {
    var el = document.getElementById("ls-theory-status");
    if (!el) return;
    el.textContent = message || "";
    el.className = "ls-theory-status " + (type ? "upload-status-" + type : "");
  }

  function lsSetPanelStatus(id, message, type) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = message || "";
    el.className = "ls-theory-status " + (type ? "upload-status-" + type : "");
  }

  function lsClaimCardHtml(claim) {
    var scope = claim.source_scope || {};
    var source = (scope.page_start || (scope.pages && scope.pages[0])) ? "p." + (scope.page_start || scope.pages[0]) : (scope.chunk_id || "");
    var review = claim.review_status === "teacher_approved" ? "承認済み" : claim.review_status || "teacher_review_required";
    var concepts = (claim.concepts || []).map(function (concept) {
      var normalized = concept.normalized ? ' → ' + concept.normalized : "";
      return '<span class="ls-theory-ref">' + escHtml(concept.raw || concept.name || "") + normalized + ': ' + escHtml(concept.concept_type || "Concept") + '</span>';
    }).join(" ");
    var scopeText = [
      scope.level || "",
      scope.section_title || scope.section_id || "",
      source,
    ].filter(Boolean).join(" / ");
    return '' +
      '<div class="ls-theory-card" data-claim-id="' + escHtml(claim.claim_id) + '">' +
        '<div class="ls-theory-card-head">' +
          '<div><strong>' + escHtml(claim.claim_id || "claim") + '</strong><div class="ls-theory-type">type: ' + escHtml(claim.claim_type || "") + '</div></div>' +
          '<span class="ls-theory-badge">' + escHtml(review) + '</span>' +
        '</div>' +
        '<div class="ls-theory-summary">' + escHtml(claim.text || "") + '</div>' +
        (claim.normalized_text && claim.normalized_text !== claim.text ? '<div class="ls-theory-section"><b>normalized</b><div class="ls-theory-muted">' + escHtml(claim.normalized_text) + '</div></div>' : '') +
        '<div class="ls-theory-section"><b>concepts</b><div>' + (concepts || '<span class="ls-theory-source-warn">未抽出</span>') + '</div></div>' +
        (claim.equation && Object.keys(claim.equation).length ? '<div class="ls-theory-section"><b>equation</b><div class="ls-theory-muted">' + escHtml((claim.equation.label || claim.equation.equation_id || "") + " " + (claim.equation.latex || "")) + '</div></div>' : '') +
        '<div class="ls-theory-section"><b>support</b> ' + escHtml(claim.support_status || "source_backed") + '</div>' +
        '<div class="ls-theory-section"><b>source_scope</b> <span class="ls-theory-ref">' + escHtml(scopeText) + '</span></div>' +
        '<div class="ls-theory-section"><b>evidence</b><div class="ls-theory-muted">' + escHtml(claim.evidence_text || "") + '</div></div>' +
      '</div>';
  }

  function lsRenderClaimsPanel(chunk) {
    var container = document.getElementById("ls-claims-list");
    var btn = document.getElementById("ls-extract-claims-btn");
    if (!container) return;
    if (btn) btn.disabled = !chunk || lsState.claimsLoading;
    if (!chunk) {
      container.innerHTML = '<div class="ls-empty-state">チャンクを選択すると主張一覧が表示されます。</div>';
      return;
    }
    var claims = lsState.claimsByChunk[chunk.chunk_id] || [];
    var meta = lsState.claimMetaByChunk[chunk.chunk_id] || {};
    if (!claims.length) {
      var reason = meta.skip_reason || "";
      var role = meta.chunk_role || "";
      container.innerHTML = '<div class="ls-empty-state">' +
        (reason ? 'このチャンクは ' + escHtml(role || "metadata") + ' のため、主張抽出対象外です。' : '主張はまだありません。主張を抽出してください。') +
        '</div>';
      return;
    }
    container.innerHTML =
      '<div class="ls-theory-current">Chunk #' + escHtml(chunk.chunk_index || "") + ' / 主張ビュー</div>' +
      claims.map(lsClaimCardHtml).join("");
  }

  function lsLoadClaimsForChunk(chunk) {
    var docId = chunk && (chunk.document_id || chunk.material_id);
    if (!chunk || !docId || !chunk.chunk_id) return;
    apiFetch("/admin/documents/" + encodeURIComponent(docId) + "/chunks/" + encodeURIComponent(chunk.chunk_id) + "/claims")
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load claims");
        return res.json();
      })
      .then(function (claims) {
        lsState.claimsByChunk[chunk.chunk_id] = claims || [];
        if (lsState.view === "claims") lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function () {});
  }

  function lsExtractClaims(chunk) {
    var docId = chunk && (chunk.document_id || chunk.material_id);
    if (!chunk || !docId) return;
    lsState.claimsLoading = true;
    lsSetPanelStatus("ls-claims-status", "抽出中...", "info");
    lsRenderClaimsPanel(chunk);
    apiFetch("/admin/documents/" + encodeURIComponent(docId) + "/chunks/" + encodeURIComponent(chunk.chunk_id) + "/claims/extract", {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (body) {
            throw new Error(lsApiErrorMessage(body, "主張抽出に失敗しました"));
          }, function () {
            throw new Error("主張抽出に失敗しました");
          });
        }
        return res.json();
      })
      .then(function (data) {
        lsState.claimsByChunk[chunk.chunk_id] = data.claims || [];
        lsState.claimMetaByChunk[chunk.chunk_id] = { chunk_role: data.chunk_role || "", skip_reason: data.skip_reason || "" };
        lsSetPanelStatus("ls-claims-status", (data.claims && data.claims.length) ? "抽出しました" : "主張は見つかりませんでした", "success");
        lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function (err) {
        lsSetPanelStatus("ls-claims-status", err.message || "抽出に失敗しました", "error");
      })
      .finally(function () {
        lsState.claimsLoading = false;
        lsRenderClaimsPanel(lsGetSelectedChunk());
      });
  }

  function lsTheoryItemsHtml(items) {
    if (!items || !items.length) return '<div class="ls-theory-muted">未設定</div>';
    var html = '<ul class="ls-theory-items">';
    items.forEach(function (item) {
      var evidenceClaims = item.evidence_claims || [];
      var hasSource = evidenceClaims.length || (item.source_refs && item.source_refs.length);
      html += '<li>' + escHtml(item.name || item.label || item.condition || "") +
        (hasSource ? ' <span class="ls-theory-source-ok">根拠あり</span>' : ' <span class="ls-theory-source-warn">要根拠</span>') +
        (evidenceClaims.length ? '<div>' + evidenceClaims.map(function (id) { return '<span class="ls-theory-ref">' + escHtml(id) + '</span>'; }).join(" ") + '</div>' : '') +
        '</li>';
    });
    html += '</ul>';
    return html;
  }

  function lsTheorySourceHtml(component) {
    var refs = component.source_chunks || [];
    if (!refs.length) return '<div class="ls-theory-warning">このコンポーネントには出典チャンクがありません。</div>';
    return refs.map(function (ref) {
      var page = ref.page_start ? " / p." + ref.page_start : "";
      return '<span class="ls-theory-ref">chunk #' + escHtml(ref.chunk_id || "") + escHtml(page) + '</span>';
    }).join(" ");
  }

  function lsTheoryCanApprove(component) {
    if (!component || !component.name || !component.source_chunks || !component.source_chunks.length) return false;
    if (!component.inputs || !component.inputs.length || !component.outputs || !component.outputs.length) return false;
    var fields = ["inputs", "outputs", "preconditions", "constraints", "invalid_conditions"];
    for (var i = 0; i < fields.length; i++) {
      var items = component[fields[i]] || [];
      for (var j = 0; j < items.length; j++) {
        if (items[j].needs_source) return false;
        if ((!items[j].source_refs || !items[j].source_refs.length) && (!items[j].evidence_claims || !items[j].evidence_claims.length)) return false;
      }
    }
    return true;
  }

  function lsTheoryCardHtml(c) {
    var statusLabel = c.status === "teacher_reviewed" ? "承認済み" : c.status === "rejected" ? "却下" : c.status === "draft" ? "下書き" : "候補";
    var warning = c.validation_warnings && c.validation_warnings.length ? " ⚠" : "";
    var level = c.blackbox_policy && c.blackbox_policy.default_level ? c.blackbox_policy.default_level : "summary";
    var evidence = (c.evidence_claims || []).map(function (id) { return '<span class="ls-theory-ref">' + escHtml(id) + '</span>'; }).join(" ");
    var duplicates = (c.duplicate_candidates || []).map(function (d) {
      return '<li>' + escHtml(d.possible_duplicate_of || "") + ' <span class="ls-theory-badge">score ' + escHtml(String(d.score || "")) + '</span><div class="ls-theory-muted">' + escHtml((d.reasons || []).join(" / ")) + '</div></li>';
    }).join("");
    var flow = (c.internal_flow || []).map(function (f) {
      return '<li>' + escHtml((f.from || "") + " → " + (f.to || "")) + '</li>';
    }).join("");
    var approveDisabled = lsTheoryCanApprove(c) ? "" : " disabled";
    return '' +
      '<div class="ls-theory-card" data-component-id="' + escHtml(c.id) + '">' +
        '<div class="ls-theory-card-head">' +
          '<div><strong>' + escHtml(c.name || "無題の理論") + '</strong><div class="ls-theory-type">type: ' + escHtml(c.component_type || "theory") + ' / origin: ' + escHtml(c.origin || "paper") + '</div></div>' +
          '<span class="ls-theory-badge">' + escHtml(statusLabel) + warning + '</span>' +
        '</div>' +
        '<div class="ls-theory-summary">' + escHtml(c.summary || "") + '</div>' +
        '<div class="ls-theory-section"><b>maturity</b> ' + escHtml(c.maturity_level || "") + ' / ' + escHtml(c.maturity_source || "") + '</div>' +
        '<div class="ls-theory-section"><b>review</b> ' + escHtml(c.review_status || "") + '</div>' +
        '<div class="ls-theory-section"><b>入力</b>' + lsTheoryItemsHtml(c.inputs) + '</div>' +
        '<div class="ls-theory-section"><b>出力</b>' + lsTheoryItemsHtml(c.outputs) + '</div>' +
        '<div class="ls-theory-section"><b>成立条件</b>' + lsTheoryItemsHtml(c.preconditions) + '</div>' +
        '<div class="ls-theory-section"><b>注意条件</b>' + lsTheoryItemsHtml(c.cautions || c.invalid_conditions) + '</div>' +
        '<div class="ls-theory-section"><b>internal_flow</b>' + (flow ? '<ul class="ls-theory-items">' + flow + '</ul>' : '<div class="ls-theory-muted">未設定</div>') + '</div>' +
        '<div class="ls-theory-section"><b>evidence_claims</b><div>' + (evidence || '<span class="ls-theory-source-warn">未設定</span>') + '</div></div>' +
        '<div class="ls-theory-section"><b>duplicate candidates</b>' + (duplicates ? '<ul class="ls-theory-items">' + duplicates + '</ul>' : '<div class="ls-theory-muted">候補なし</div>') + '</div>' +
        '<div class="ls-theory-section"><b>出典</b><div>' + lsTheorySourceHtml(c) + '</div></div>' +
        '<div class="ls-theory-section"><b>表示</b> ' + escHtml(level) + '</div>' +
        '<div class="ls-theory-actions">' +
          '<button class="admin-action-btn" data-theory-action="open">開く</button>' +
          '<button class="admin-action-btn" data-theory-action="insert">原稿に挿入</button>' +
          '<button class="admin-action-btn" data-theory-action="approve"' + approveDisabled + '>承認</button>' +
          '<button class="admin-action-btn" data-theory-action="reject">却下</button>' +
        '</div>' +
      '</div>';
  }

  function lsRenderTheoryPanel(chunk) {
    var container = document.getElementById("ls-theory-components");
    var extractBtn = document.getElementById("ls-extract-theory-btn");
    if (!container) return;
    if (extractBtn) extractBtn.disabled = !chunk || lsState.theoryLoading;
    if (lsState.selectedScope && lsState.selectedScope.type === "section") {
      var sectionComponents = lsState.componentsBySection[lsState.selectedScope.sectionId] || [];
      if (extractBtn) extractBtn.textContent = "節の論理要素を抽出";
      if (!sectionComponents.length) {
        container.innerHTML = '<div class="ls-empty-state">Section ' + escHtml(lsState.selectedScope.sectionId) + '<br><br>論理要素候補はまだありません。</div>';
        return;
      }
      container.innerHTML =
        '<div class="ls-theory-current">セクション論理要素ビュー: ' + escHtml(lsState.selectedScope.sectionId) + '</div>' +
        sectionComponents.map(lsTheoryCardHtml).join("");
      container.querySelectorAll("[data-theory-action]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var card = this.closest(".ls-theory-card");
          var component = lsFindTheoryComponent(card.getAttribute("data-component-id"));
          var action = this.getAttribute("data-theory-action");
          if (!component) return;
          if (action === "open") lsOpenTheoryDetail(component);
          if (action === "insert") lsInsertTheoryChip(component);
          if (action === "approve") lsSaveTheoryComponent(component, { status: "teacher_reviewed" });
          if (action === "reject") lsRejectTheoryComponent(component);
        });
      });
      return;
    }
    if (extractBtn) extractBtn.textContent = "論理要素候補を抽出";
    if (!chunk) {
      container.innerHTML = '<div class="ls-empty-state">チャンクを選択すると、論理要素候補が表示されます。</div>';
      return;
    }
    var components = lsState.componentsByChunk[chunk.chunk_id] || [];
    if (!components.length) {
      container.innerHTML =
        '<div class="ls-empty-state">選択中チャンク: #' + escHtml(chunk.chunk_index || "") +
        (chunk.page_start ? " / PDF p." + escHtml(chunk.page_start) : "") + '<br><br>' +
        'このチャンクには論理要素がまだありません。<br>' +
        '「論理要素候補を抽出」を押してください。</div>';
      return;
    }
    container.innerHTML =
      '<div class="ls-theory-current">選択中チャンク: #' + escHtml(chunk.chunk_index || "") +
      (chunk.page_start ? " / PDF p." + escHtml(chunk.page_start) : "") + '</div>' +
      components.map(lsTheoryCardHtml).join("");
    container.querySelectorAll("[data-theory-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var card = this.closest(".ls-theory-card");
        var component = lsFindTheoryComponent(card.getAttribute("data-component-id"));
        var action = this.getAttribute("data-theory-action");
        if (!component) return;
        if (action === "open") lsOpenTheoryDetail(component);
        if (action === "insert") lsInsertTheoryChip(component);
        if (action === "approve") lsSaveTheoryComponent(component, { status: "teacher_reviewed" });
        if (action === "reject") lsRejectTheoryComponent(component);
      });
    });
  }

  function lsLoadTheoryComponentsForChunk(chunkId) {
    if (!lsState.courseId || !chunkId) return;
    apiFetch("/admin/courses/" + lsState.courseId + "/theory-components?chunk_id=" + encodeURIComponent(chunkId))
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load theory components");
        return res.json();
      })
      .then(function (components) {
        lsState.componentsByChunk[chunkId] = components || [];
        if (lsState.view === "theory") lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function () {});
  }

  function lsLoadSectionComponents(documentId, sectionId) {
    if (!documentId || !sectionId) return;
    apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/sections/" + encodeURIComponent(sectionId) + "/components")
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load section components");
        return res.json();
      })
      .then(function (components) {
        lsState.componentsBySection[sectionId] = components || [];
        (components || []).forEach(function (component) {
          lsUpdateTheoryInState(component);
        });
        if (lsState.view === "theory") lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function () {});
  }

  function lsAssembleSectionComponents(documentId, sectionId) {
    if (!documentId || !sectionId) return;
    lsState.theoryLoading = true;
    lsSetTheoryStatus("組み立て中...", "info");
    lsRenderTheoryPanel(lsGetSelectedChunk());
    apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/sections/" + encodeURIComponent(sectionId) + "/components/assemble", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (body) {
            throw new Error(lsApiErrorMessage(body, "節の論理要素抽出に失敗しました"));
          }, function () {
            throw new Error("節の論理要素抽出に失敗しました");
          });
        }
        return res.json();
      })
      .then(function (data) {
        lsState.componentsBySection[sectionId] = data.components || [];
        (data.components || []).forEach(function (component) {
          lsUpdateTheoryInState(component);
        });
        lsSetTheoryStatus((data.components && data.components.length) ? "組み立てました" : "候補は見つかりませんでした", "success");
        lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function (err) {
        lsSetTheoryStatus(err.message || "組み立てに失敗しました", "error");
      })
      .finally(function () {
        lsState.theoryLoading = false;
        lsRenderTheoryPanel(lsGetSelectedChunk());
      });
  }

  function lsRenderGraphPanel(documentId) {
    var container = document.getElementById("ls-component-graph");
    if (!container) return;
    if (!documentId) {
      container.innerHTML = '<div class="ls-empty-state">論文を選択すると論理グラフが表示されます。</div>';
      return;
    }
    var graph = lsState.graphByDocument[documentId];
    if (!graph) {
      container.innerHTML = '<div class="ls-empty-state">グラフを読み込み中、または未作成です。</div>';
      return;
    }
    var nodes = graph.nodes || [];
    var edges = graph.edges || [];
    var validations = graph.validation_results || [];
    if (!nodes.length) {
      container.innerHTML = '<div class="ls-empty-state">論理要素がまだありません。節ビューで論理要素を抽出してください。</div>';
      return;
    }

    var html =
      '<div class="ls-component-graph-shell">' +
        '<div class="ls-component-graph-main">' +
          '<div id="ls-component-network" class="ls-component-network" tabindex="0"></div>' +
          '<div class="ls-component-legend">' +
            '<div class="ls-component-legend-title">論理コンポーネント</div>' +
            '<div><span class="ls-legend-swatch ls-legend-assumption"></span>前提・定義</div>' +
            '<div><span class="ls-legend-swatch ls-legend-method"></span>手法・構成</div>' +
            '<div><span class="ls-legend-swatch ls-legend-relation"></span>推論・関係</div>' +
            '<div><span class="ls-legend-swatch ls-legend-diagnostic"></span>検証・制約</div>' +
            '<div><span class="ls-legend-swatch ls-legend-conclusion"></span>結論・出力</div>' +
            '<div><span class="ls-legend-swatch ls-legend-uncertainty"></span>注意・不確実性</div>' +
          '</div>' +
          '<button id="ls-component-graph-fit" class="ls-component-graph-fit" type="button" title="全体を表示">⤢</button>' +
        '</div>' +
        '<aside id="ls-component-graph-detail" class="ls-component-graph-detail">' +
          lsGraphEmptyDetailHtml(nodes.length, edges.length) +
        '</aside>' +
      '</div>' +
      lsGraphValidationHtml(validations);
    container.innerHTML = html;

    if (!window.vis || !window.vis.Network) {
      lsRenderGraphFallback(container, graph);
      return;
    }

    lsInitComponentGraphNetwork(graph);
  }

  function lsGraphEmptyDetailHtml(nodeCount, edgeCount) {
    return '<div class="ls-graph-detail-empty">' +
      '<div class="ls-graph-detail-icon">⌖</div>' +
      '<p>ノードを選択すると、論理ステップの詳細が表示されます。</p>' +
      '<div class="ls-graph-detail-counts">' +
        '<span>' + escHtml(String(nodeCount)) + ' nodes</span>' +
        '<span>' + escHtml(String(edgeCount)) + ' edges</span>' +
      '</div>' +
    '</div>';
  }

  function lsGraphValidationHtml(validations) {
    var html = '<div class="ls-theory-section ls-graph-validation"><b>検証結果</b>';
    if (!validations || !validations.length) {
      html += '<div class="ls-theory-source-ok">警告はありません</div>';
    } else {
      html += '<ul class="ls-theory-items">';
      validations.forEach(function (v) {
        html += '<li><span class="ls-theory-source-warn">' + escHtml(v.severity || "warning") + '</span> ' + escHtml(v.message || "") + '</li>';
      });
      html += '</ul>';
    }
    return html + '</div>';
  }

  function lsInitComponentGraphNetwork(graph) {
    var networkEl = document.getElementById("ls-component-network");
    if (!networkEl) return;

    var nodes = graph.nodes || [];
    var edges = graph.edges || [];
    var graphEdges = lsGraphDisplayEdges(edges);
    var layoutPositions = lsGraphLayoutPositions(nodes, graphEdges);
    var nodeById = {};
    nodes.forEach(function (node) {
      var id = lsGraphNodeId(node);
      if (id) nodeById[id] = node;
    });

    var visNodes = new window.vis.DataSet(nodes.map(function (node, index) {
      var id = lsGraphNodeId(node) || ("node-" + index);
      var group = lsGraphNodeGroup(node);
      var pos = layoutPositions[id] || { x: 0, y: index * 160 };
      var visNode = {
        id: id,
        label: lsWrapGraphLabel(node.label || node.name || id),
        x: pos.x,
        y: pos.y,
        group: group,
        title: node.component_type_text || node.component_type || node.review_status || "",
      };
      if (lsGraphNodeDashed(node, group)) {
        visNode.shapeProperties = { borderDashes: [6, 5] };
      }
      return visNode;
    }));

    var visEdges = new window.vis.DataSet(graphEdges.map(function (edge, index) {
      var from = edge.source_component_id || edge.source || edge.from;
      var to = edge.target_component_id || edge.target || edge.to;
      var relation = edge.relation || edge.edge_type || edge.type || "RELATED_TO";
      return {
        id: edge.edge_id || ("edge-" + index),
        from: from,
        to: to,
        label: lsGraphEdgeLabel(relation),
        arrows: "to",
        dashes: lsGraphEdgeDashed(edge),
        width: Math.max(1, Math.min(4, Number(edge.confidence || 0.7) * 4)),
        color: { color: lsGraphEdgeColor(edge) },
      };
    }).filter(function (edge) {
      return edge.from && edge.to;
    }));

    var network = new window.vis.Network(networkEl, { nodes: visNodes, edges: visEdges }, {
      layout: {
        hierarchical: false,
      },
      physics: false,
      nodes: {
        shape: "box",
        margin: { top: 11, right: 14, bottom: 11, left: 14 },
        borderWidth: 2,
        borderWidthSelected: 3,
        font: { size: 13, face: "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif", multi: true },
        shadow: { enabled: true, color: "rgba(15, 23, 42, 0.08)", size: 10, x: 0, y: 4 },
      },
      edges: {
        font: { size: 12, align: "middle", color: "#64748b", strokeWidth: 5, strokeColor: "#ffffff" },
        smooth: { type: "cubicBezier", forceDirection: "vertical", roundness: 0.42 },
      },
      groups: {
        assumption: { color: { background: "#f0fdf4", border: "#22c55e", highlight: { background: "#dcfce7", border: "#16a34a" } } },
        method: { color: { background: "#eff6ff", border: "#3b82f6", highlight: { background: "#dbeafe", border: "#2563eb" } } },
        relation: { color: { background: "#faf5ff", border: "#a855f7", highlight: { background: "#f3e8ff", border: "#9333ea" } } },
        diagnostic: { color: { background: "#f7fee7", border: "#84cc16", highlight: { background: "#ecfccb", border: "#65a30d" } } },
        conclusion: { color: { background: "#fef2f2", border: "#ef4444", highlight: { background: "#fee2e2", border: "#dc2626" } } },
        uncertainty: { color: { background: "#fff7ed", border: "#f97316", highlight: { background: "#ffedd5", border: "#ea580c" } }, shape: "ellipse" },
      },
      interaction: { hover: true, tooltipDelay: 180 },
    });

    var fitBtn = document.getElementById("ls-component-graph-fit");
    if (fitBtn) {
      fitBtn.addEventListener("click", function () {
        network.fit({ animation: { duration: 450, easingFunction: "easeInOutQuad" } });
      });
    }
    network.once("afterDrawing", function () {
      network.fit({ animation: false });
    });
    network.on("selectNode", function (params) {
      var node = nodeById[params.nodes[0]];
      if (node) lsRenderGraphNodeDetail(node, graph);
    });
    network.on("deselectNode", function () {
      var detail = document.getElementById("ls-component-graph-detail");
      if (detail) detail.innerHTML = lsGraphEmptyDetailHtml(nodes.length, edges.length);
    });
  }

  function lsRenderGraphNodeDetail(node, graph) {
    var detail = document.getElementById("ls-component-graph-detail");
    if (!detail) return;
    var nodeId = lsGraphNodeId(node);
    var connected = (graph.edges || []).filter(function (edge) {
      var source = edge.source_component_id || edge.source || edge.from;
      var target = edge.target_component_id || edge.target || edge.to;
      return source === nodeId || target === nodeId;
    });
    var html =
      '<div class="ls-graph-detail-badge ' + escHtml(lsGraphNodeGroup(node)) + '">' + escHtml(node.component_type_text || node.component_type || node.typeName || "component") + '</div>' +
      '<h4>' + escHtml(node.label || node.name || nodeId || "無題") + '</h4>' +
      '<p class="ls-graph-detail-meta">' + escHtml(node.review_status || node.origin || "paper") + '</p>';
    if (node.summary) {
      html += '<div class="ls-graph-detail-section"><b>Summary</b><p>' + escHtml(node.summary) + '</p></div>';
    }
    html += '<div class="ls-graph-detail-section"><b>Connections</b>';
    if (!connected.length) {
      html += '<p class="ls-theory-muted">接続エッジはありません。</p>';
    } else {
      html += '<ul class="ls-graph-detail-edges">';
      connected.forEach(function (edge) {
        var source = edge.source_component_id || edge.source || edge.from;
        var target = edge.target_component_id || edge.target || edge.to;
        var direction = source === nodeId ? "→ " + target : "← " + source;
        html += '<li><span>' + escHtml(edge.relation || edge.edge_type || edge.type || "RELATED_TO") + '</span>' + escHtml(direction) + '</li>';
      });
      html += '</ul>';
    }
    html += '</div><div class="ls-graph-detail-section"><b>System ID</b><code>' + escHtml(nodeId || "") + '</code></div>';
    detail.innerHTML = html;
  }

  function lsRenderGraphFallback(container, graph) {
    var nodes = graph.nodes || [];
    var edges = graph.edges || [];
    var nodeById = {};
    nodes.forEach(function (node) { nodeById[lsGraphNodeId(node)] = node; });
    var html = '<div class="ls-empty-state">ネットワーク描画ライブラリを読み込めませんでした。テキスト表示に切り替えます。</div><div class="ls-graph-flow">';
    nodes.forEach(function (node) {
      html += '<div class="ls-graph-node">' + escHtml(node.label || lsGraphNodeId(node)) + '<div class="ls-theory-muted">' + escHtml(node.origin || "paper") + ' / ' + escHtml(node.component_type || "") + '</div></div>';
    });
    edges.forEach(function (edge) {
      var sourceId = edge.source_component_id || edge.source || edge.from;
      var targetId = edge.target_component_id || edge.target || edge.to;
      var source = nodeById[sourceId] || {};
      var target = nodeById[targetId] || {};
      html += '<details class="ls-graph-edge"><summary><span class="ls-graph-node-inline">' + escHtml(source.label || sourceId || "") + '</span>' +
        '<span class="ls-graph-edge-label"> -- ' + escHtml(edge.relation || edge.edge_type || edge.type || "RELATED_TO") + ' → </span>' +
        '<span class="ls-graph-node-inline">' + escHtml(target.label || targetId || "") + '</span></summary></details>';
    });
    html += '</div>' + lsGraphValidationHtml(graph.validation_results || []);
    container.innerHTML = html;
  }

  function lsGraphNodeId(node) {
    return node && (node.component_id || node.id || node.node_id);
  }

  function lsWrapGraphLabel(label) {
    var text = String(label || "");
    text = text
      .replace(/^Completeness condition$/i, "完全性条件\n(Completeness)")
      .replace(/^Reality criterion$/i, "実在性の基準\n(Reality criterion)")
      .replace(/^No-real-change after separation$/i, "分離後の無影響\n(No-real-change)")
      .replace(/^Remote alternative-measurement assignment$/i, "遠隔の代替測定\n(Remote assignment)")
      .replace(/^Reality attribution for noncommuting observables$/i, "非可換量の同時実在\n(ルートB)")
      .replace(/^Alternative-to-incompleteness inference$/i, "波動関数の不完全性\n(最終結論)")
      .replace(/^Argument uncertainty from criterion scope and degraded equations$/i, "論証の不確実性")
      .replace(/^Uniform-coordinate unpredictability from interval probability$/i, "単一粒子座標の\n予測不可能性")
      .replace(/^Incompleteness diagnosis from many-to-one wave-function assignment$/i, "不完全性の診断\n(ルートA)");
    if (text.indexOf("\n") >= 0 || text.length <= 18) return text;
    if (!/\s/.test(text)) {
      var chunks = [];
      for (var i = 0; i < text.length; i += 14) chunks.push(text.slice(i, i + 14));
      return chunks.join("\n");
    }
    return text.split(/\s+/).reduce(function (lines, word) {
      var current = lines[lines.length - 1] || "";
      if ((current + " " + word).trim().length > 18) lines.push(word);
      else lines[lines.length - 1] = (current + " " + word).trim();
      return lines;
    }, [""]).join("\n");
  }

  function lsGraphNodeGroup(node) {
    var type = String((node && (node.component_type_text || node.component_type || node.type)) || "").toLowerCase();
    var label = String((node && (node.label || node.name)) || "").toLowerCase();
    var haystack = type + " " + label;
    if (/uncertain|risk|caution|注意|不確実/.test(haystack)) return "uncertainty";
    if (/assumption|premise|definition|axiom|criterion|no-real-change|completeness|前提|定義|基準|無影響/.test(haystack)) return "assumption";
    if (/method|setup|experiment|procedure|assignment|unpredictability|手法|構成|実験|代替測定|予測不可能/.test(haystack)) return "method";
    if (/diagnostic|constraint|law|check|diagnosis|検証|制約|診断/.test(haystack)) return "diagnostic";
    if (/conclusion|result|output|claim|incompleteness inference|final|結論|出力|主張|最終/.test(haystack)) return "conclusion";
    return "relation";
  }

  function lsGraphNodeLevel(node, fallbackIndex) {
    var group = lsGraphNodeGroup(node);
    var label = String((node && (node.label || node.name)) || "").toLowerCase();
    if (/uniform-coordinate|単一粒子/.test(label)) return 0;
    if (group === "assumption") return 0;
    if (group === "method" || group === "uncertainty") return 1;
    if (group === "relation" || group === "diagnostic") return 2;
    if (group === "conclusion") return 3;
    return Math.min(3, Math.max(0, Number(fallbackIndex || 0)));
  }

  function lsGraphNodeDashed(node, group) {
    var label = String((node && (node.label || node.name)) || "").toLowerCase();
    return /uniform-coordinate|単一粒子/.test(label) || (group === "method" && /unpredictability/.test(label));
  }

  function lsGraphDisplayEdges(edges) {
    var byPair = {};
    (edges || []).forEach(function (edge, index) {
      var source = edge.source_component_id || edge.source || edge.from;
      var target = edge.target_component_id || edge.target || edge.to;
      if (!source || !target) return;
      var key = source + "->" + target;
      var current = byPair[key];
      if (!current || lsGraphRelationPriority(edge) > lsGraphRelationPriority(current)) {
        byPair[key] = Object.assign({}, edge, { edge_id: edge.edge_id || ("edge-" + index) });
      }
    });
    return Object.keys(byPair).map(function (key) { return byPair[key]; });
  }

  function lsGraphLayoutPositions(nodes, edges) {
    var nodeById = {};
    var levels = {};
    (nodes || []).forEach(function (node, index) {
      var id = lsGraphNodeId(node) || ("node-" + index);
      nodeById[id] = node;
      levels[id] = lsGraphNodeLevel(node, index);
    });

    for (var pass = 0; pass < 12; pass += 1) {
      var changed = false;
      (edges || []).forEach(function (edge) {
        var relation = String(edge.relation || edge.edge_type || edge.type || "").toUpperCase();
        if (relation === "UNCERTAIN_DUE_TO" || relation === "RELATED_TO") return;
        var source = edge.source_component_id || edge.source || edge.from;
        var target = edge.target_component_id || edge.target || edge.to;
        if (!nodeById[source] || !nodeById[target]) return;
        var nextLevel = Math.min(4, (levels[source] || 0) + 1);
        if ((levels[target] || 0) < nextLevel) {
          levels[target] = nextLevel;
          changed = true;
        }
      });
      if (!changed) break;
    }

    (nodes || []).forEach(function (node, index) {
      var id = lsGraphNodeId(node) || ("node-" + index);
      var group = lsGraphNodeGroup(node);
      if (group === "uncertainty") levels[id] = Math.min(2, Math.max(1, levels[id] || 1));
      if (group === "conclusion") levels[id] = Math.max(4, levels[id] || 4);
      if (lsGraphNodeDashed(node, group)) levels[id] = 0;
    });

    var buckets = {};
    (nodes || []).forEach(function (node, index) {
      var id = lsGraphNodeId(node) || ("node-" + index);
      var level = levels[id] || 0;
      if (!buckets[level]) buckets[level] = [];
      buckets[level].push(node);
    });

    var positions = {};
    Object.keys(buckets).forEach(function (levelKey) {
      var level = Number(levelKey);
      var bucket = buckets[levelKey].sort(function (a, b) {
        return lsGraphNodeSortKey(a) - lsGraphNodeSortKey(b);
      });
      var spacing = bucket.length > 3 ? 280 : 340;
      var totalWidth = (bucket.length - 1) * spacing;
      bucket.forEach(function (node, index) {
        var id = lsGraphNodeId(node);
        positions[id] = {
          x: (index * spacing) - (totalWidth / 2),
          y: level * 185,
        };
      });
    });
    return positions;
  }

  function lsGraphNodeSortKey(node) {
    var label = String((node && (node.label || node.name)) || "").toLowerCase();
    if (/completeness|完全性/.test(label)) return 10;
    if (/reality criterion|実在.*基準/.test(label)) return 20;
    if (/no-disturbance|no-real-change|分離/.test(label)) return 30;
    if (/uniform|coordinate|単一粒子|座標/.test(label)) return 40;
    if (/remote|遠隔/.test(label)) return 50;
    if (/criterion-of-reality|scope|limitation|uncertain|射程|不確実/.test(label)) return 60;
    if (/wave function|diagnos|診断/.test(label)) return 70;
    if (/disjunctive|incompleteness|結論/.test(label)) return 80;
    return Number(node && node.display_order) || 99;
  }

  function lsGraphRelationPriority(edge) {
    var relation = String((edge && (edge.relation || edge.edge_type || edge.type)) || "").toUpperCase();
    if (relation === "REQUIRES") return 5;
    if (relation === "PRODUCES_FOR" || relation === "ENABLES" || relation === "SUPPORTS") return 4;
    if (relation === "UNCERTAIN_DUE_TO") return 3;
    if (relation === "QUALIFIES" || relation === "CHECKED_BY") return 2;
    return 1;
  }

  function lsGraphEdgeLabel(relation) {
    var key = String(relation || "").toUpperCase();
    var labels = {
      REQUIRES: "依存",
      PRODUCES_FOR: "生成",
      ENABLES: "支持",
      SUPPORTS: "支持",
      UNCERTAIN_DUE_TO: "不確実性",
      RELATED_TO: "関連",
      QUALIFIES: "限定",
      CHECKED_BY: "検証",
      CONFLICTS_WITH: "矛盾",
      INHIBITS: "抑制",
      DEFINES: "定義",
    };
    return labels[key] || key;
  }

  function lsGraphEdgeDashed(edge) {
    var status = String((edge && edge.support_status) || "").toLowerCase();
    var relation = String((edge && (edge.relation || edge.edge_type || edge.type)) || "").toLowerCase();
    return /llm|related|qualifies|conflicts|inhibits|uncertain/.test(status + " " + relation);
  }

  function lsGraphEdgeColor(edge) {
    var relation = String((edge && (edge.relation || edge.edge_type || edge.type)) || "").toUpperCase();
    if (relation === "UNCERTAIN_DUE_TO") return "#f97316";
    if (relation === "CONFLICTS_WITH" || relation === "INHIBITS") return "#ef4444";
    if (relation === "CHECKED_BY" || relation === "QUALIFIES") return "#84cc16";
    if (relation === "DEFINES") return "#22c55e";
    if (relation === "RELATED_TO") return "#f97316";
    return "#94a3b8";
  }

  function lsLoadComponentGraph(documentId, forceRender) {
    if (!documentId) return;
    lsState.graphLoading = true;
    lsSetPanelStatus("ls-graph-status", "読み込み中...", "info");
    apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/component-graph")
      .then(function (res) {
        if (!res.ok) throw new Error("Graphの読み込みに失敗しました");
        return res.json();
      })
      .then(function (graph) {
        lsState.graphByDocument[documentId] = graph;
        lsSetPanelStatus("ls-graph-status", "更新しました", "success");
        if (forceRender || lsState.view === "graph") lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function (err) {
        lsSetPanelStatus("ls-graph-status", err.message || "読み込みに失敗しました", "error");
      })
      .finally(function () {
        lsState.graphLoading = false;
      });
  }

  function lsExtractTheoryComponents(chunkId) {
    lsState.theoryLoading = true;
    lsSetTheoryStatus("抽出中...", "info");
    lsRenderTheoryPanel(lsGetSelectedChunk());
    apiFetch("/admin/chunks/" + chunkId + "/theory-components/extract", {
      method: "POST",
      body: JSON.stringify({ force: true, use_llm: true }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("論理要素候補の抽出に失敗しました");
        return res.json();
      })
      .then(function (data) {
        lsState.componentsByChunk[chunkId] = data.components || [];
        lsSetTheoryStatus((data.components && data.components.length) ? "抽出しました" : "候補は見つかりませんでした", "success");
        lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function (err) {
        lsSetTheoryStatus(err.message || "抽出に失敗しました", "error");
      })
      .finally(function () {
        lsState.theoryLoading = false;
        lsRenderTheoryPanel(lsGetSelectedChunk());
      });
  }

  function lsUpdateTheoryInState(component) {
    var chunkId = component.primary_chunk_id || lsState.selectedChunkId;
    if (!chunkId) return;
    var list = lsState.componentsByChunk[chunkId] || [];
    var found = false;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === component.id) {
        list[i] = component;
        found = true;
        break;
      }
    }
    if (!found) list.unshift(component);
    lsState.componentsByChunk[chunkId] = list;
  }

  function lsSaveTheoryComponent(component, patch) {
    var payload = Object.assign({}, component, patch || {});
    delete payload.id;
    delete payload.course_id;
    delete payload.primary_chunk_id;
    delete payload.validation_warnings;
    delete payload.created_at;
    delete payload.updated_at;
    lsSetTheoryStatus("保存中...", "info");
    apiFetch("/admin/theory-components/" + component.id, {
      method: "PUT",
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (body) {
            throw new Error(body && body.detail ? JSON.stringify(body.detail) : "保存に失敗しました");
          }, function () {
            throw new Error("保存に失敗しました");
          });
        }
        return res.json();
      })
      .then(function (saved) {
        lsUpdateTheoryInState(saved);
        lsSetTheoryStatus(saved.status === "teacher_reviewed" ? "承認しました" : "保存しました", "success");
        lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function (err) {
        lsSetTheoryStatus(err.message || "保存に失敗しました", "error");
      });
  }

  function lsPersistTheoryComponent(component) {
    var payload = lsNormalizeTheoryRefs(Object.assign({}, component));
    delete payload.id;
    delete payload.course_id;
    delete payload.primary_chunk_id;
    delete payload.validation_warnings;
    delete payload.created_at;
    delete payload.updated_at;
    return apiFetch("/admin/theory-components/" + component.id, {
      method: "PUT",
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (body) {
            throw new Error(body && body.detail ? JSON.stringify(body.detail) : "保存に失敗しました");
          }, function () {
            throw new Error("保存に失敗しました");
          });
        }
        return res.json();
      });
  }

  function lsNormalizeTheoryRefs(component) {
    var chunk = lsGetSelectedChunk();
    var chunkId = (chunk && chunk.chunk_id) || lsState.selectedChunkId || "";
    function normalizeRef(ref) {
      ref = Object.assign({}, ref || {});
      if (!ref.chunk_id && chunkId) ref.chunk_id = chunkId;
      if (ref.page_start === undefined && chunk && chunk.page_start) ref.page_start = chunk.page_start;
      if (ref.page_end === undefined && chunk && chunk.page_end) ref.page_end = chunk.page_end;
      return ref;
    }
    function normalizeItems(items) {
      return (items || []).map(function (item) {
        item = Object.assign({}, item || {});
        if (item.source_refs && item.source_refs.length) {
          item.source_refs = item.source_refs.map(normalizeRef);
        }
        return item;
      });
    }
    component.source_chunks = (component.source_chunks || []).map(normalizeRef);
    ["inputs", "outputs", "preconditions", "constraints", "invalid_conditions", "dependencies"].forEach(function (field) {
      component[field] = normalizeItems(component[field]);
    });
    return component;
  }

  function lsRejectTheoryComponent(component) {
    lsSetTheoryStatus("却下中...", "info");
    apiFetch("/admin/theory-components/" + component.id + "/reject", {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("却下に失敗しました");
        return res.json();
      })
      .then(function (saved) {
        lsUpdateTheoryInState(saved);
        lsSetTheoryStatus("却下しました", "success");
        lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function (err) {
        lsSetTheoryStatus(err.message || "却下に失敗しました", "error");
      });
  }

  function lsInsertTheoryChip(component) {
    var displayEl = document.getElementById("ls-display-text");
    if (!displayEl) return;
    var chip = "[[THEORY:" + component.id + "|" + component.name + "]]";
    var start = displayEl.selectionStart || 0;
    var end = displayEl.selectionEnd || 0;
    var value = displayEl.value || "";
    displayEl.value = value.slice(0, start) + chip + value.slice(end);
    displayEl.selectionStart = displayEl.selectionEnd = start + chip.length;
    displayEl.dispatchEvent(new Event("input"));
    lsState.displayView = "script";
    lsSetTheoryStatus("原稿に挿入しました", "success");
  }

  function lsOpenTheoryDetail(component) {
    var existing = document.getElementById("ls-theory-detail-modal");
    if (existing) existing.remove();
    var policy = component.blackbox_policy || { default_level: "summary", expand_if_unlearned: true };
    var overlay = document.createElement("div");
    overlay.id = "ls-theory-detail-modal";
    overlay.className = "ls-settings-modal";
    overlay.innerHTML =
      '<div class="ls-theory-dialog">' +
        '<div class="ls-settings-head">' +
          '<h3>論理要素</h3>' +
          '<button id="ls-theory-close" class="lecture-chat-close" type="button">&times;</button>' +
        '</div>' +
        '<label class="ls-settings-field"><span>name</span><input id="ls-theory-name" class="ls-settings-select" value="' + escHtml(component.name || "") + '"></label>' +
        '<label class="ls-settings-field"><span>summary</span><textarea id="ls-theory-summary-edit" class="ls-theory-jsonarea">' + escHtml(component.summary || "") + '</textarea></label>' +
        '<label class="ls-settings-field"><span>teacher_notes</span><textarea id="ls-theory-notes-edit" class="ls-theory-jsonarea">' + escHtml(component.teacher_notes || "") + '</textarea></label>' +
        '<div class="ls-settings-field"><span>表示レベル</span><div class="ls-theory-levels">' +
          lsTheoryLevelButton("io_only", "入出力だけ", policy.default_level) +
          lsTheoryLevelButton("summary", "要点", policy.default_level) +
          lsTheoryLevelButton("derivation", "導出", policy.default_level) +
          lsTheoryLevelButton("source", "原典", policy.default_level) +
        '</div></div>' +
        lsTheoryJsonField("inputs", component.inputs) +
        lsTheoryJsonField("outputs", component.outputs) +
        lsTheoryJsonField("preconditions", component.preconditions) +
        lsTheoryJsonField("constraints", component.constraints) +
        lsTheoryJsonField("invalid_conditions", component.invalid_conditions) +
        lsTheoryJsonField("dependencies", component.dependencies) +
        lsTheoryJsonField("source_chunks", component.source_chunks) +
        '<div id="ls-theory-detail-status" class="upload-status" style="display:none"></div>' +
        '<div class="ls-settings-actions">' +
          '<button id="ls-theory-save-draft" class="admin-action-btn" type="button">下書き保存</button>' +
          '<button id="ls-theory-save-candidate" class="admin-action-btn" type="button">候補保存</button>' +
          '<button id="ls-theory-approve-detail" class="admin-action-btn" type="button">承認</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    function close() { overlay.remove(); }
    document.getElementById("ls-theory-close").addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    overlay.querySelectorAll("[data-theory-level]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        overlay.querySelectorAll("[data-theory-level]").forEach(function (b) { b.classList.remove("active"); });
        this.classList.add("active");
      });
    });
    document.getElementById("ls-theory-save-draft").addEventListener("click", function () {
      lsSaveTheoryFromDetail(component, "draft", overlay);
    });
    document.getElementById("ls-theory-save-candidate").addEventListener("click", function () {
      lsSaveTheoryFromDetail(component, "candidate", overlay);
    });
    document.getElementById("ls-theory-approve-detail").addEventListener("click", function () {
      lsSaveTheoryFromDetail(component, "teacher_reviewed", overlay);
    });
  }

  function lsTheoryLevelButton(level, label, current) {
    return '<button class="ls-mini-tab' + (level === current ? " active" : "") + '" type="button" data-theory-level="' + escHtml(level) + '">' + escHtml(label) + '</button>';
  }

  function lsTheoryJsonField(name, value) {
    return '<label class="ls-settings-field"><span>' + escHtml(name) + '</span>' +
      '<textarea class="ls-theory-jsonarea" data-theory-json="' + escHtml(name) + '">' +
      escHtml(JSON.stringify(value || [], null, 2)) + '</textarea></label>';
  }

  function lsSaveTheoryFromDetail(component, status, overlay) {
    var detailStatus = document.getElementById("ls-theory-detail-status");
    function show(msg, type) {
      detailStatus.textContent = msg;
      detailStatus.className = "upload-status upload-status-" + type;
      detailStatus.style.display = "block";
    }
    var payload = Object.assign({}, component);
    payload.name = document.getElementById("ls-theory-name").value.trim();
    payload.summary = document.getElementById("ls-theory-summary-edit").value;
    payload.teacher_notes = document.getElementById("ls-theory-notes-edit").value;
    payload.status = status;
    var activeLevel = overlay.querySelector("[data-theory-level].active");
    payload.blackbox_policy = {
      default_level: activeLevel ? activeLevel.getAttribute("data-theory-level") : "summary",
      expand_if_unlearned: true,
    };
    var failed = false;
    overlay.querySelectorAll("[data-theory-json]").forEach(function (area) {
      if (failed) return;
      try {
        payload[area.getAttribute("data-theory-json")] = JSON.parse(area.value || "[]");
      } catch (e) {
        failed = true;
        show(area.getAttribute("data-theory-json") + " のJSON形式を確認してください", "error");
      }
    });
    if (failed) return;
    show("保存中...", "info");
    lsSaveTheoryComponent(component, payload);
    overlay.remove();
  }

  function lsRenderDisplayPreview() {
    var chunk = lsGetSelectedChunk();
    var preview = document.getElementById("ls-display-preview");
    if (!preview || !chunk) return;
    var text = lsNormalizePreviewLineBreaks(document.getElementById("ls-display-text").value || "");
    var formulaById = {};
    var usedFormulas = new Set();
    (chunk.formulas || []).forEach(function (f, idx) {
      var fallbackId = "FORMULA_" + idx;
      var legacyMathId = "LS_MATH_" + idx;
      var id = f.id || fallbackId;
      var bareId = String(id).replace(/^\[\[/, "").replace(/\]\]$/, "");
      formulaById[id] = f;
      formulaById[bareId] = f;
      formulaById[("[[" + bareId + "]]")] = f;
      formulaById[fallbackId] = f;
      formulaById[("[[" + fallbackId + "]]")] = f;
      formulaById[legacyMathId] = f;
    });
    var mathBlocks = [];
    var theoryChips = [];
    var preserved = text;
    function preserveMath(expr, display) {
      var idx = mathBlocks.length;
      mathBlocks.push({ expr: expr, display: display });
      return "@@EG_PREVIEW_MATH_" + idx + "@@";
    }
    preserved = preserved.replace(/\[\[THEORY:([^|\]]+)\|([^\]]+)\]\]/g, function (_m, id, label) {
      var idx = theoryChips.length;
      theoryChips.push({ id: id, label: label });
      return "@@EG_PREVIEW_THEORY_" + idx + "@@";
    });
    preserved = preserved.replace(/\[\[([^\[\]]+)\]\]/g, function (m, id) {
      var formula = formulaById[m] || formulaById[id];
      if (!formula) return m;
      usedFormulas.add(formula);
      return preserveMath(formula.latex || formula.id || m, formula.is_display === true);
    });
    preserved = preserved.replace(/\bLS_MATH_(\d+)\b/g, function (m) {
      var formula = formulaById[m];
      if (!formula) return m;
      usedFormulas.add(formula);
      return preserveMath(formula.latex || formula.id || m, formula.is_display === true);
    });
    preserved = preserved.replace(/\$\$([\s\S]+?)\$\$/g, function (_m, expr) {
      return preserveMath(expr, true);
    });
    preserved = preserved.replace(/\\\[([\s\S]+?)\\\]/g, function (_m, expr) {
      return preserveMath(expr, true);
    });
    preserved = preserved.replace(/\\\(([\s\S]+?)\\\)/g, function (_m, expr) {
      return preserveMath(expr, false);
    });
    preserved = preserved.replace(/\$([^\$\n]+?)\$/g, function (_m, expr) {
      return preserveMath(expr, false);
    });
    var html = escHtml(preserved);
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/@@EG_PREVIEW_THEORY_(\d+)@@/g, function (_m, idx) {
      var chip = theoryChips[parseInt(idx, 10)] || {};
      var component = lsFindTheoryComponent(chip.id);
      var marker = "?";
      if (component && component.status === "teacher_reviewed") marker = "✓";
      if (component && component.validation_warnings && component.validation_warnings.length) marker = "⚠";
      if (component && component.blackbox_policy && component.blackbox_policy.default_level === "io_only") marker = "🔒";
      return '<span class="ls-theory-chip" data-component-id="' + escHtml(chip.id) + '">' + escHtml(chip.label) + ' ' + marker + '</span>';
    });
    preview.innerHTML = html.split("\n\n").map(function (p) { return "<p>" + p.replace(/\n/g, "<br>") + "</p>"; }).join("");
    preview.innerHTML = preview.innerHTML.replace(/@@EG_PREVIEW_MATH_(\d+)@@/g, function (_m, idx) {
      var block = mathBlocks[parseInt(idx, 10)];
      return lsRenderKatex(block.expr, block.display);
    });
    var extractedHtml = lsRenderUnplacedExtractedFormulas(chunk.formulas || [], usedFormulas);
    if (extractedHtml) preview.insertAdjacentHTML("beforeend", extractedHtml);
  }

  function lsNormalizePreviewLineBreaks(text) {
    return String(text || "").replace(/([A-Za-z0-9,;:)\]])\n(?=[A-Za-z0-9([“"'])/g, "$1 ");
  }

  function lsRenderUnplacedExtractedFormulas(formulas, usedFormulas) {
    var items = [];
    (formulas || []).forEach(function (f) {
      if (!f || usedFormulas.has(f)) return;
      if (!f.source_image && !f.needs_math_review && !f.source_location) return;
      var expr = f.latex || f.id || "";
      if (!expr) return;
      items.push(
        '<div class="ls-extracted-formula-in-preview">' +
          (f.label ? '<div class="ls-extracted-formula-label">(' + escHtml(f.label) + ')</div>' : '') +
          lsRenderKatex(expr, true) +
        '</div>'
      );
    });
    if (!items.length) return "";
    return '<div class="ls-extracted-formulas-block">' + items.join("") + '</div>';
  }

  function lsRenderKatex(expr, display) {
    var formula = String(expr || "").trim();
    if (!formula) return "";
    var cls = display ? "lecture-formula-block visible" : "lecture-formula visible";
    if (window.katex) {
      try {
        var rendered = window.katex.renderToString(formula, {
          displayMode: !!display,
          throwOnError: false,
          strict: "ignore",
          trust: false,
        });
        return '<span class="' + cls + '">' + rendered + '</span>';
      } catch (e) {
        // Fall through to escaped fallback.
      }
    }
    return '<span class="' + cls + '"><code class="ls-formula-chip">' + escHtml(formula) + '</code></span>';
  }

  function lsLoadPdfForChunk(chunk) {
    var pdfView = document.getElementById("ls-pdf-view");
    if (!chunk.pdf_url) {
      pdfView.innerHTML = '<div class="ls-empty-state">このチャンクにPDF参照がありません</div>';
      return;
    }
    var page = chunk.page_start ? "#page=" + encodeURIComponent(chunk.page_start) : "";
    // ページ情報があるチャンクは同一チャンクIDなら再描画スキップ
    if (chunk.page_start && pdfView.getAttribute("data-pdf-chunk") === chunk.chunk_id) return;
    pdfView.setAttribute("data-pdf-chunk", chunk.chunk_id);
    // 同じPDFが既に読み込み済みならBlobを再利用してiframeだけ差し替え
    if (lsState.pdfObjectUrl && lsState.pdfUrl === chunk.pdf_url) {
      pdfView.innerHTML = '<iframe class="ls-pdf-frame" src="' + escHtml(lsState.pdfObjectUrl + page) + '"></iframe>';
      return;
    }
    pdfView.innerHTML = '<div class="ls-empty-state">PDFを読み込み中...</div>';
    apiFetchRaw(chunk.pdf_url, { _noJson: true })
      .then(function (res) {
        if (!res.ok) throw new Error("PDF load failed");
        return res.blob();
      })
      .then(function (blob) {
        if (lsState.pdfObjectUrl) URL.revokeObjectURL(lsState.pdfObjectUrl);
        lsState.pdfObjectUrl = URL.createObjectURL(blob);
        lsState.pdfUrl = chunk.pdf_url;
        pdfView.innerHTML = '<iframe class="ls-pdf-frame" src="' + escHtml(lsState.pdfObjectUrl + page) + '"></iframe>';
      })
      .catch(function () {
        pdfView.innerHTML = '<div class="ls-empty-state">PDFを表示できませんでした</div>';
      });
  }

  function lsRenderStructure(chunk) {
    var dsl = chunk.smiles_dsl || "";
    var variables = chunk.variables ? JSON.stringify(chunk.variables, null, 2) : "";
    var ancestors = chunk.ancestors && chunk.ancestors.length ? JSON.stringify(chunk.ancestors, null, 2) : "";
    document.getElementById("ls-dsl-view").textContent =
      dsl || variables || ancestors
        ? [dsl, variables && "variables:\n" + variables, ancestors && "ancestors:\n" + ancestors].filter(Boolean).join("\n\n")
        : "このチャンクにDSL情報はまだ登録されていません。PDF全体の構造解析結果が空、またはチャンク別DSLが未生成です。";
    var graphEl = document.getElementById("ls-graph-elements");
    var elements = chunk.graph_elements || [];
    var html = "";
    if (chunk.neo4j_node_id) {
      html += '<div class="ls-graph-row"><span class="ls-graph-badge">chunk</span><strong>' + escHtml(chunk.neo4j_node_id) + '</strong></div>';
    }
    if (!elements.length) {
      html += '<div class="ls-empty-state">このチャンクに対応するグラフ要素は見つかりませんでした。PDF全体の構造解析結果が空、または本文との紐付けが未生成です。</div>';
    } else {
      elements.forEach(function (g) {
        html += '<div class="ls-graph-row"><span class="ls-graph-badge">' + escHtml(g.type || "") + '</span><strong>' + escHtml(g.label || g.id || "") + '</strong>';
        if (g.status) html += '<div class="ls-graph-status">' + escHtml(g.status) + '</div>';
        if (g.description) html += '<div class="ls-graph-desc">' + escHtml(g.description) + '</div>';
        html += '</div>';
      });
    }
    graphEl.innerHTML = html;
  }

  function lsClearEditor() {
    if (lsState.pdfObjectUrl) {
      URL.revokeObjectURL(lsState.pdfObjectUrl);
      lsState.pdfObjectUrl = null;
    }
    lsState.pdfUrl = null;
    document.getElementById("ls-workspace").innerHTML = '<div class="ls-empty-state">チャンクを選択すると編集ワークベンチが表示されます</div>';
    document.getElementById("ls-chunk-meta").textContent = "チャンクを選択してください";
    document.getElementById("ls-rewrite-prompt").disabled = true;
    document.getElementById("ls-rewrite-btn").disabled = true;
    document.getElementById("ls-save-btn").disabled = true;
    document.getElementById("ls-formulas").innerHTML = "";
    lsHideActionStatus();
  }

  function lsRenderFormulas(formulas) {
    var el = document.getElementById("ls-formulas");
    if (!formulas || formulas.length === 0) {
      el.innerHTML = "";
      return;
    }
    var html = '<div style="font-size:11px;font-weight:600;color:var(--color-text-secondary);margin-bottom:6px">数式一覧</div>';
    formulas.forEach(function (f) {
      var label = f.label || f.equation_label || "";
      var sourceImage = lsFormulaSourceImageHtml(f);
      var review = (f.needs_math_review || (f.review_reason && f.review_reason.length))
        ? '<div class="ls-formula-review">要確認: ' + escHtml((f.review_reason || []).slice(0, 2).join(", ") || "復元式") + '</div>'
        : "";
      html +=
        '<div class="ls-formula-item">' +
          sourceImage +
          '<div class="ls-formula-rendered">' + lsRenderKatex(f.latex || f.id || "", f.is_display === true) + '</div>' +
          (label ? '<span class="ls-theory-badge">(' + escHtml(label) + ')</span><br>' : '') +
          '<span class="ls-formula-latex">' + escHtml(f.latex || f.id || "") + '</span><br>' +
          '<span class="ls-formula-spoken">' + escHtml(f.spoken || "") + '</span>' +
          review +
        '</div>';
    });
    el.innerHTML = html;
  }

  function lsFormulaSourceImageHtml(f) {
    var img = f && f.source_image;
    if (!img || !img.data_base64) return "";
    var mime = img.mime_type || "image/png";
    var page = img.page || (f.source_location && f.source_location.page) || "";
    var bbox = img.bbox || (f.source_location && f.source_location.bbox) || [];
    var meta = page ? "p." + page : "";
    if (bbox && bbox.length === 4) {
      meta += (meta ? " " : "") + "bbox " + bbox.map(function (v) { return Math.round(Number(v) || 0); }).join(",");
    }
    return '<div class="ls-formula-source-image-wrap">' +
      '<img class="ls-formula-source-image" src="data:' + escHtml(mime) + ';base64,' + escHtml(img.data_base64) + '" alt="PDFから切り出した数式画像">' +
      (meta ? '<div class="ls-formula-source-meta">' + escHtml(meta) + '</div>' : '') +
      '</div>';
  }

  function lsShowProgress(msg, type) {
    var el = document.getElementById("ls-progress");
    el.innerHTML = msg;
    el.className = "upload-status upload-status-" + (type || "info");
    el.style.display = "block";
  }

  function lsHideProgress() {
    var el = document.getElementById("ls-progress");
    el.style.display = "none";
  }

  function lsShowActionStatus(msg, type) {
    var el = document.getElementById("ls-action-status");
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (type || "info");
    el.style.display = "block";
  }

  function lsHideActionStatus() {
    var el = document.getElementById("ls-action-status");
    el.style.display = "none";
  }

  function lsApiErrorMessage(body, fallback) {
    var detail = body && body.detail;
    if (!detail) return fallback;
    if (typeof detail === "string") return detail;
    if (detail.code === "claim_extraction_failed") {
      return "主張抽出に失敗しました。LLM応答が有効な主張JSONとして解釈できませんでした。再実行してください。";
    }
    if (detail.code === "component_assembly_failed") {
      return "論理要素の抽出に失敗しました。意味のある論理要素候補を生成できなかったため、保存しませんでした。";
    }
    return detail.message || fallback;
  }

  function lsHasUsableDsl() {
    var chunks = lsState.chunks || [];
    if (!chunks.length) return false;
    return chunks.some(function (chunk) {
      return Boolean((chunk.smiles_dsl && String(chunk.smiles_dsl).trim()) ||
        (chunk.graph_elements && chunk.graph_elements.length));
    });
  }

  function lsHasScripts() {
    var chunks = lsState.chunks || [];
    if (!chunks.length) return false;
    return chunks.every(function (chunk) {
      return Boolean(String(chunk.spoken_text || chunk.display_text || "").trim());
    });
  }

  function lsHasAudio() {
    var chunks = lsState.chunks || [];
    if (!chunks.length) return false;
    return chunks.every(function (chunk) {
      return chunk.status === "audio_ready";
    });
  }

  function lsCoursePipelineState() {
    var status = lsState.analysisStatus || {};
    var claimsDone = Boolean(status.claims && status.claims.complete);
    var componentsDone = Boolean(status.components && status.components.complete);
    var graphDone = Boolean(status.graph && status.graph.complete);
    var claimsError = Boolean(status.claims && status.claims.failed);
    var componentsError = Boolean(status.components && status.components.failed);
    var graphError = Boolean(status.graph && status.graph.failed);
    var structureDone = lsHasUsableDsl() || claimsDone || componentsDone || graphDone;
    var scriptsDone = lsHasScripts();
    var audioDone = lsHasAudio();
    var next = "";
    if (!structureDone) next = "structure";
    else if (!claimsDone) next = "claims";
    else if (!componentsDone) next = "components";
    else if (!graphDone) next = "graph";
    else if (!scriptsDone) next = "script";
    else if (!audioDone) next = "audio";
    else next = "";
    return {
      structureDone: structureDone,
      claimsDone: claimsDone,
      componentsDone: componentsDone,
      graphDone: graphDone,
      scriptsDone: scriptsDone,
      audioDone: audioDone,
      claimsError: claimsError,
      componentsError: componentsError,
      graphError: graphError,
      next: next,
    };
  }

  function lsPipelineStepVisual(step, state) {
    var task = lsState.pipelineTask || {};
    if (task.step === step && task.status === "running") return "running";
    if (task.step === step && task.status === "failed") return "error";
    if (step === "claims" && state.claimsError) return "error";
    if (step === "components" && state.componentsError) return "error";
    if (step === "graph" && state.graphError) return "error";
    if (step === "structure" && state.structureDone) return "done";
    if (step === "claims" && state.claimsDone) return "done";
    if (step === "components" && state.componentsDone) return "done";
    if (step === "graph" && state.graphDone) return "done";
    if (step === "script" && state.scriptsDone) return "done";
    if (step === "audio" && state.audioDone) return "done";
    if (state.next === step) return "next";
    return "pending";
  }

  function lsSetMenuItemState(id, enabled, visual) {
    var btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = !enabled;
    ["done", "next", "running", "error", "pending"].forEach(function (name) {
      btn.classList.toggle("ls-menu-item-" + name, visual === name);
    });
  }

  function lsSetAgentStageItemState(enabled) {
    var task = lsState.pipelineTask || {};
    document.querySelectorAll(".ls-agent-stage-btn").forEach(function (btn) {
      var stage = btn.getAttribute("data-stage") || "";
      var visual = "pending";
      if (task.stage === stage && task.status === "running") visual = "running";
      if (task.stage === stage && task.status === "failed") visual = "error";
      btn.disabled = !enabled;
      ["done", "next", "running", "error", "pending"].forEach(function (name) {
        btn.classList.toggle("ls-menu-item-" + name, visual === name);
      });
    });
  }

  function lsUpdateCourseControls() {
    var hasCourse = Boolean(lsState.courseId);
    var hasChunks = Boolean(lsState.chunks && lsState.chunks.length);
    var busy = Boolean(lsState.generating);
    var ready = hasCourse && !busy;
    var state = lsCoursePipelineState();

    var pipelineMenuBtn = document.getElementById("ls-pipeline-menu-btn");
    var moreMenuBtn = document.getElementById("ls-more-menu-btn");
    if (pipelineMenuBtn) {
      pipelineMenuBtn.disabled = !hasCourse;
      pipelineMenuBtn.classList.toggle("ls-menu-trigger-busy", busy);
    }
    if (moreMenuBtn) moreMenuBtn.disabled = !hasCourse;

    lsSetMenuItemState("ls-pipeline-full-btn", ready, lsState.pipelineTask && lsState.pipelineTask.step === "document_pipeline" ? lsState.pipelineTask.status === "failed" ? "error" : "running" : "pending");
    lsSetAgentStageItemState(ready);
    lsSetMenuItemState("ls-audio-all-btn", ready && hasChunks && state.scriptsDone, lsPipelineStepVisual("audio", state));

    var exportBtn = document.getElementById("ls-export-btn");
    var settingsBtn = document.getElementById("ls-settings-btn");
    if (exportBtn) exportBtn.disabled = !hasCourse;
    if (settingsBtn) settingsBtn.disabled = !hasCourse;
  }

  function lsSetCourseTaskBusy(isBusy) {
    lsState.generating = isBusy;
    lsUpdateCourseControls();
  }

  var lsAgentStageLabels = {
    document_structure: "DocumentStructureAgent",
    paper_skeleton: "PaperSkeletonAgent",
    rhetorical_role: "RhetoricalRoleAgent",
    claim_qualification: "ClaimQualificationAgent",
    equation_semantics: "EquationSemanticsAgent",
    evidence_registry: "EvidenceRegistryBuilder",
    claim_object_builder: "ClaimObjectBuilder",
    derivation_chain: "DerivationChainAgent",
    figure_table_semantics: "FigureTableSemanticsAgent",
    thesis_reconstruction: "ThesisReconstructionAgent",
    dsl_linking: "DSLLinkingAgent",
    component_assembly: "ComponentAssemblyAgent",
    component_graph: "ComponentGraphAgent",
    course_mapping: "CourseMappingAgent",
    blueprint: "BlueprintAgent",
    export_validation: "ExportValidationGate",
  };

  function lsRunDocumentPipeline(stage) {
    var targetStage = stage || "";
    var label = targetStage ? lsAgentStageLabels[targetStage] || targetStage : "パイプライン全実行";
    lsState.pipelineTask = targetStage
      ? { step: "document_pipeline", stage: targetStage, status: "running" }
      : { step: "document_pipeline", status: "running" };
    lsSetCourseTaskBusy(true);
    lsShowProgress(label + "を開始しています...", "info");
    apiFetch("/admin/courses/" + lsState.courseId + "/document-pipeline/run", {
      method: "POST",
      body: JSON.stringify({ target_stage: targetStage }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            throw new Error((errBody && errBody.detail) || label + "を開始できませんでした");
          }, function () {
            throw new Error(label + "を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        lsPollGenericCourseTask(data.task_id, label, "document_pipeline", targetStage);
      })
      .catch(function (err) {
        lsState.pipelineTask = targetStage
          ? { step: "document_pipeline", stage: targetStage, status: "failed" }
          : { step: "document_pipeline", status: "failed" };
        lsShowProgress(label + "に失敗しました: " + (err.message || "不明なエラー"), "error");
        lsSetCourseTaskBusy(false);
      });
  }

  function lsRunCourseStep(kind, endpoint, label, body) {
    var stepMap = { structure: "structure", claims: "claims", components: "components", graph: "graph" };
    lsState.pipelineTask = { step: stepMap[kind] || kind, status: "running" };
    lsSetCourseTaskBusy(true);
    lsShowProgress(label + "を開始しています...", "info");
    apiFetch(endpoint, {
      method: "POST",
      body: body || "{}",
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            throw new Error((errBody && errBody.detail) || label + "を開始できませんでした");
          }, function () {
            throw new Error(label + "を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        lsPollGenericCourseTask(data.task_id, label, stepMap[kind] || kind);
      })
      .catch(function (err) {
        lsState.pipelineTask = { step: stepMap[kind] || kind, status: "failed" };
        lsShowProgress(label + "に失敗しました: " + (err.message || "不明なエラー"), "error");
        lsSetCourseTaskBusy(false);
      });
  }

  function lsRunCourseStepWithRetryConfirm(kind, endpoint, label) {
    lsShowProgress(label + "の状態を確認しています...", "info");
    apiFetch("/admin/courses/" + lsState.courseId + "/analysis-status")
      .then(function (res) {
        if (!res.ok) throw new Error("解析状態を確認できませんでした");
        return res.json();
      })
      .then(function (status) {
        var step = status[kind] || {};
        var force = false;
        if (step.complete) {
          var ok = window.confirm(label + "は解析済です。解析済のデータも含めてすべて再度実行しますか？");
          if (!ok) {
            lsHideProgress();
            return;
          }
          force = true;
        }
        lsRunCourseStep(kind, endpoint, label, JSON.stringify({ force: force }));
      })
      .catch(function (err) {
        lsShowProgress((err && err.message) || "解析状態を確認できませんでした", "error");
      });
  }

  function lsPollGenericCourseTask(taskId, label, step, targetStage) {
    var retryCount = 0;
    var maxRetries = 5;
    var intervalMs = 3000;
    function poll() {
      apiFetch("/admin/tasks/" + taskId)
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0;
          var rd = task.result_data || {};
          var progress = rd.progress || 0;
          var generated = rd.generated || 0;
          var skipped = rd.skipped || 0;
          var failed = rd.failed || 0;
          var total = rd.total_chunks || rd.total_sections || rd.total_documents || 0;
          if (task.status === "completed") {
            clearInterval(timer);
            var doneMessage = label + "が完了しました: " + generated + "件処理 / " + skipped + "件スキップ";
            if (failed) doneMessage += " / " + failed + "件エラー（該当箇所はスキップ）";
            lsShowProgress(doneMessage, failed ? "warning" : "success");
            lsState.pipelineTask = failed
              ? { step: step || "", stage: targetStage || "", status: "failed" }
              : null;
            lsSetCourseTaskBusy(false);
            lsLoadScripts(lsState.courseId);
          } else if (task.status === "failed") {
            clearInterval(timer);
            lsState.pipelineTask = { step: step || "", stage: targetStage || "", status: "failed" };
            lsShowProgress(label + "に失敗しました: " + (task.error_message || "不明なエラー"), "error");
            lsSetCourseTaskBusy(false);
          } else {
            if (targetStage) lsState.pipelineTask = { step: step || "", stage: targetStage, status: "running" };
            var progressMessage = label + "中... (" + generated + " / " + total + " — " + progress + "%)";
            if (failed) progressMessage += " / エラー " + failed + "件はスキップ";
            lsShowProgress(progressMessage, "info");
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            clearInterval(timer);
            lsShowProgress(label + "の進捗確認に失敗しました。", "error");
            lsState.pipelineTask = { step: step || "", stage: targetStage || "", status: "failed" };
            lsSetCourseTaskBusy(false);
          }
        });
    }
    var timer = setInterval(poll, intervalMs);
    poll();
  }

  function lsReanalyzeStructure() {
    lsState.pipelineTask = { step: "structure", status: "running" };
    lsSetCourseTaskBusy(true);
    lsShowProgress("構造の再解析を開始しています...", "info");

    apiFetch("/admin/courses/" + lsState.courseId + "/structure/reanalyze", {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            var msg = (errBody && errBody.detail) || "構造の再解析を開始できませんでした";
            throw new Error(msg);
          }, function () {
            throw new Error("構造の再解析を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        _lsPollStructureTask(data.task_id, data.total_materials || 0);
      })
      .catch(function (err) {
        lsState.pipelineTask = { step: "structure", status: "failed" };
        lsShowProgress("構造の再解析に失敗しました: " + (err.message || "不明なエラー"), "error");
        lsSetCourseTaskBusy(false);
      });
  }

  function _lsPollStructureTask(taskId, totalMaterials) {
    var retryCount = 0;
    var maxRetries = 5;
    var intervalMs = 3000;

    function poll() {
      apiFetch("/admin/tasks/" + taskId)
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0;
          var rd = task.result_data || {};
          var processed = rd.processed_materials || 0;
          var updated = rd.updated_chunks || 0;
          var errors = rd.errors || 0;
          var progress = rd.progress || 0;
          var total = totalMaterials || rd.total_materials || 0;

          if (task.status === "completed") {
            clearInterval(timer);
            lsState.pipelineTask = null;
            lsShowProgress(
              "構造の再解析完了: " + processed + "件の教材 / " + updated + "件のチャンクを更新",
              "success"
            );
            lsSetCourseTaskBusy(false);
            lsLoadScripts(lsState.courseId);
          } else if (task.status === "failed") {
            clearInterval(timer);
            lsState.pipelineTask = { step: "structure", status: "failed" };
            lsShowProgress(
              "構造の再解析に失敗しました: " + (task.error_message || (errors + "件のエラー")),
              "error"
            );
            lsSetCourseTaskBusy(false);
            lsLoadScripts(lsState.courseId);
          } else {
            lsShowProgress(
              "構造の再解析中... (" + processed + " / " + total + "教材、更新チャンク " + updated + "件 — " + progress + "%)",
              "info"
            );
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            clearInterval(timer);
            lsShowProgress("進捗確認に失敗しました。ページをリロードして状況を確認してください。", "error");
            lsState.pipelineTask = { step: "structure", status: "failed" };
            lsSetCourseTaskBusy(false);
          }
        });
    }

    var timer = setInterval(poll, intervalMs);
    poll();
  }

  function lsBatchGenerate() {
    lsState.pipelineTask = { step: "script", status: "running" };
    lsSetCourseTaskBusy(true);
    lsShowProgress("スクリプト生成を開始しています...", "info");

    apiFetch("/admin/courses/" + lsState.courseId + "/lecture-scripts/generate", {
      method: "POST",
      body: JSON.stringify({ override: false }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            var msg = (errBody && errBody.detail) || "スクリプト生成を開始できませんでした";
            throw new Error(msg);
          }, function () {
            throw new Error("スクリプト生成を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        var taskId = data.task_id;
        var totalChunks = data.total_chunks || 0;
        lsShowProgress("スクリプト生成中... (0 / " + totalChunks + ")", "info");
        _lsPollGenerateTask(taskId, totalChunks);
      })
      .catch(function (err) {
        lsState.pipelineTask = { step: "script", status: "failed" };
        lsShowProgress("生成に失敗しました: " + (err.message || "不明なエラー"), "error");
        lsSetCourseTaskBusy(false);
      });
  }

  function _lsPollGenerateTask(taskId, totalChunks) {
    var retryCount = 0;
    var maxRetries = 5;
    var intervalMs = 3000;

    function poll() {
      apiFetch("/admin/tasks/" + taskId)
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0;

          var rd = task.result_data || {};
          var generated = rd.generated || 0;
          var skipped = rd.skipped || 0;
          var progress = rd.progress || 0;
          var processed = generated + skipped;

          if (task.status === "completed") {
            clearInterval(timer);
            // Issue #139: 自動パイプラインの場合は音声タスクへチェイン
            if (rd.next_task_id) {
              lsState.pipelineTask = { step: "audio", status: "running" };
              lsShowProgress(
                "原稿生成完了 (" + generated + "件生成 / " + skipped + "件スキップ)。続いて音声生成を開始します...",
                "info"
              );
              // チャンクリストを更新（spoken_text が埋まった状態を反映）
              apiFetch("/admin/courses/" + lsState.courseId + "/lecture-scripts")
                .then(function (res) { return res.ok ? res.json() : []; })
                .then(function (chunks) {
                  if (lsState.courseId) {
                    lsState.chunks = chunks;
                    lsRenderChunkList();
                    // ボタンは音声生成中のため再度無効化
                    lsSetCourseTaskBusy(true);
                  }
                })
                .catch(function () {});
              _lsPollAudioTask(rd.next_task_id, totalChunks);
              return;
            }
            lsShowProgress(
              "生成完了: " + generated + "件生成 / " + skipped + "件スキップ (全" + totalChunks + "件)",
              "success"
            );
            lsState.pipelineTask = null;
            lsSetCourseTaskBusy(false);
            lsLoadScripts(lsState.courseId);
          } else if (task.status === "failed") {
            clearInterval(timer);
            lsState.pipelineTask = { step: "script", status: "failed" };
            lsShowProgress("生成に失敗しました: " + (task.error_message || "不明なエラー"), "error");
            lsSetCourseTaskBusy(false);
          } else {
            // pending / processing
            lsShowProgress(
              "スクリプト生成中... (" + processed + " / " + totalChunks + " — " + progress + "%)",
              "info"
            );
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            clearInterval(timer);
            lsShowProgress("進捗確認に失敗しました。ページをリロードして状況を確認してください。", "error");
            lsState.pipelineTask = { step: "script", status: "failed" };
            lsSetCourseTaskBusy(false);
          }
        });
    }

    var timer = setInterval(poll, intervalMs);
    poll();
  }

  function lsBatchAudio() {
    lsState.pipelineTask = { step: "audio", status: "running" };
    lsSetCourseTaskBusy(true);
    lsShowProgress("音声生成を開始しています...", "info");

    apiFetch("/admin/courses/" + lsState.courseId + "/lecture-audio/generate", {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            var msg = (errBody && errBody.detail) || "音声生成を開始できませんでした";
            throw new Error(msg);
          }, function () {
            throw new Error("音声生成を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        var taskId = data.task_id;
        var totalChunks = data.total_chunks || 0;
        lsShowProgress("音声生成中... (0 / " + totalChunks + ")", "info");
        _lsPollAudioTask(taskId, totalChunks);
      })
      .catch(function (err) {
        lsState.pipelineTask = { step: "audio", status: "failed" };
        lsShowProgress("音声生成に失敗しました: " + (err.message || "不明なエラー"), "error");
        lsSetCourseTaskBusy(false);
      });
  }

  function _lsPollAudioTask(taskId, totalChunks) {
    var retryCount = 0;
    var maxRetries = 5;
    var intervalMs = 3000;

    function poll() {
      apiFetch("/admin/tasks/" + taskId)
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0;

          var rd = task.result_data || {};
          var generated = rd.generated || 0;
          var skipped = rd.skipped || 0;
          var errors = rd.errors || 0;
          var progress = rd.progress || 0;
          var processed = generated + skipped + errors;

          if (task.status === "completed") {
            clearInterval(timer);
            lsState.pipelineTask = errors > 0 ? { step: "audio", status: "failed" } : null;
            lsShowProgress(
              "音声生成完了: " + generated + "件生成 / " + skipped + "件スキップ" +
              (errors > 0 ? " / " + errors + "件エラー" : "") +
              " (全" + totalChunks + "件)",
              errors > 0 ? "error" : "success"
            );
            lsSetCourseTaskBusy(false);
            lsLoadScripts(lsState.courseId);
          } else if (task.status === "failed") {
            clearInterval(timer);
            lsState.pipelineTask = { step: "audio", status: "failed" };
            lsShowProgress("音声生成に失敗しました: " + (task.error_message || "不明なエラー"), "error");
            lsSetCourseTaskBusy(false);
          } else {
            // pending / processing
            lsShowProgress(
              "音声生成中... (" + processed + " / " + totalChunks + " — " + progress + "%)",
              "info"
            );
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            clearInterval(timer);
            lsShowProgress("進捗確認に失敗しました。ページをリロードして状況を確認してください。", "error");
            lsState.pipelineTask = { step: "audio", status: "failed" };
            lsSetCourseTaskBusy(false);
          }
        });
    }

    var timer = setInterval(poll, intervalMs);
    poll();
  }

  function lsSaveScript() {
    if (!lsState.selectedChunkId) return;

    var displayEl = document.getElementById("ls-display-text");
    var spokenEl = document.getElementById("ls-spoken-text");
    var chunk = lsGetSelectedChunk();
    var displayText = displayEl ? displayEl.value : "";
    var spokenText = spokenEl ? spokenEl.value : displayText;
    document.getElementById("ls-save-btn").disabled = true;
    lsShowActionStatus("保存中...", "info");

    apiFetch("/admin/chunks/" + lsState.selectedChunkId + "/lecture-script", {
      method: "PUT",
      body: JSON.stringify({ display_text: displayText, spoken_text: spokenText, formulas: (chunk && chunk.formulas) || [] }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Save failed");
        return res.json();
      })
      .then(function () {
        lsShowActionStatus("保存しました", "success");
        // Update local state
        for (var i = 0; i < lsState.chunks.length; i++) {
          if (lsState.chunks[i].chunk_id === lsState.selectedChunkId) {
            lsState.chunks[i].display_text = displayText;
            lsState.chunks[i].text = displayText;
            lsState.chunks[i].spoken_text = spokenText;
            lsState.chunks[i].status = "edited";
            break;
          }
        }
        lsRenderChunkList();
      })
      .catch(function () {
        lsShowActionStatus("保存に失敗しました", "error");
      })
      .finally(function () {
        document.getElementById("ls-save-btn").disabled = false;
      });
  }

  function lsRewriteScript() {
    if (!lsState.selectedChunkId) return;

    var prompt = document.getElementById("ls-rewrite-prompt").value.trim();
    if (!prompt) {
      lsShowActionStatus("指示を入力してください", "error");
      return;
    }

    document.getElementById("ls-rewrite-btn").disabled = true;
    lsShowActionStatus("AIで提案中...", "info");
    var view = lsState.view || "edit";
    var theoryComponents = [];
    if (view === "theory") {
      theoryComponents = lsState.componentsByChunk[lsState.selectedChunkId] || [];
      if (!theoryComponents.length) {
        lsShowActionStatus("論理要素候補を先に抽出してください", "error");
        document.getElementById("ls-rewrite-btn").disabled = false;
        return;
      }
    }

    apiFetch("/admin/chunks/" + lsState.selectedChunkId + "/lecture-script/rewrite", {
      method: "POST",
      body: JSON.stringify({
        prompt: prompt,
        narration_persona: lsState.settings.narration_persona || "",
        studio_view: view,
        theory_components: theoryComponents,
      }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Rewrite failed");
        return res.json();
      })
      .then(function (data) {
        if (view === "theory") {
          lsApplyTheorySuggestions(data.theory_components || []);
          return;
        }
        // Update editor
        document.getElementById("ls-display-text").value = data.display_text || data.spoken_text;
        document.getElementById("ls-spoken-text").value = data.spoken_text;
        lsRenderDisplayPreview();
        lsRenderFormulas(data.formulas || []);
        lsShowActionStatus(view === "audio" ? "読み上げ文を更新しました" : "提案を反映しました", "success");
        // Update local state
        for (var i = 0; i < lsState.chunks.length; i++) {
          if (lsState.chunks[i].chunk_id === lsState.selectedChunkId) {
            lsState.chunks[i].display_text = data.display_text || data.spoken_text;
            lsState.chunks[i].text = data.display_text || data.spoken_text;
            lsState.chunks[i].spoken_text = data.spoken_text;
            lsState.chunks[i].formulas = data.formulas || [];
            lsState.chunks[i].status = "edited";
            break;
          }
        }
        lsRenderChunkList();
      })
      .catch(function () {
        lsShowActionStatus("AI提案に失敗しました", "error");
      })
      .finally(function () {
        document.getElementById("ls-rewrite-btn").disabled = false;
      });
  }

  function lsApplyTheorySuggestions(components) {
    if (!components || !components.length) {
      lsShowActionStatus("論理要素の提案はありませんでした", "error");
      return;
    }
    var byId = {};
    (lsState.componentsByChunk[lsState.selectedChunkId] || []).forEach(function (c) {
      byId[c.id] = c;
    });
    var saves = components.map(function (suggested) {
      var current = byId[suggested.id] || suggested;
      var merged = lsMergeTheorySuggestion(current, suggested);
      merged.id = current.id || suggested.id;
      merged.course_id = current.course_id || suggested.course_id || lsState.courseId;
      merged.primary_chunk_id = current.primary_chunk_id || suggested.primary_chunk_id || lsState.selectedChunkId;
      merged.status = merged.status || "candidate";
      lsUpdateTheoryInState(merged);
      return lsPersistTheoryComponent(merged);
    });
    Promise.all(saves)
      .then(function (savedList) {
        savedList.forEach(function (saved) {
          if (saved) lsUpdateTheoryInState(saved);
        });
        lsShowActionStatus("論理要素の提案を反映しました", "success");
        lsRenderWorkspace();
        lsRenderChunkList();
      })
      .catch(function () {
        lsShowActionStatus("論理要素提案の保存に失敗しました", "error");
      });
  }

  function lsMergeTheorySuggestion(current, suggested) {
    var merged = Object.assign({}, current, suggested);
    // inputs / outputs are structural data derived from DSL. Never accept LLM edits.
    merged.inputs = current.inputs || [];
    merged.outputs = current.outputs || [];
    if ((!suggested.source_chunks || !suggested.source_chunks.length) && current.source_chunks) {
      merged.source_chunks = current.source_chunks;
    }
    return merged;
  }

  // ── Init ───────────────────────────────────────────────────────────
  // ── Groups Management (Issue #121) ─────────────────────────────────
  var _groupsState = { list: [], selectedId: null };

  function initGroups() {
    var refreshBtn = document.getElementById("groups-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", loadGroups);

    var createForm = document.getElementById("groups-create-form");
    if (createForm) {
      createForm.addEventListener("submit", function (e) {
        e.preventDefault();
        createGroup();
      });
    }

    var joinForm = document.getElementById("groups-join-form");
    if (joinForm) {
      joinForm.addEventListener("submit", function (e) {
        e.preventDefault();
        joinByCode();
      });
    }

    loadGroups();
    loadMyInvitations();
  }

  function setGroupsStatus(msg, kind) {
    var el = document.getElementById("groups-status");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (kind || "info");
  }

  function loadGroups() {
    apiFetch("/groups")
      .then(function (res) { return res.json(); })
      .then(function (list) {
        _groupsState.list = list || [];
        renderGroupsList();
        // 選択解除されていたら先頭を選ぶ
        if (_groupsState.list.length && !_groupsState.selectedId) {
          selectGroup(_groupsState.list[0].id);
        } else if (_groupsState.selectedId) {
          selectGroup(_groupsState.selectedId);
        }
      })
      .catch(function (e) { setGroupsStatus("グループ一覧の取得に失敗しました", "error"); });
  }

  function renderGroupsList() {
    var el = document.getElementById("groups-list");
    if (!_groupsState.list.length) {
      el.innerHTML = '<div style="padding:12px;color:var(--color-text-tertiary);font-size:13px">まだグループに参加していません</div>';
      return;
    }
    var html = "";
    _groupsState.list.forEach(function (g) {
      var badge = g.my_role === "admin" ? '<span style="color:var(--color-text-success);font-size:11px;margin-left:4px">(admin)</span>' : "";
      var isSelected = g.id === _groupsState.selectedId;
      var cardStyle = isSelected
        ? "border:2px solid var(--color-text-success);border-radius:8px;overflow:hidden;box-shadow:0 4px 12px rgba(0,0,0,0.12);margin-bottom:0"
        : "border:1px solid var(--color-border);border-radius:6px;overflow:hidden;opacity:0.8";
      var headerBg = isSelected ? "var(--color-bg-tertiary)" : "var(--color-bg-secondary)";
      var bodyDisplay = isSelected ? "block" : "none";
      var toggleIcon = isSelected ? "▲" : "▼";
      var accentBar = isSelected
        ? '<div style="height:3px;background:var(--color-text-success)"></div>'
        : "";
      html +=
        '<div style="' + cardStyle + '">' +
          accentBar +
          '<div class="groups-item" data-gid="' + escHtml(g.id) + '" style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;cursor:pointer;background:' + headerBg + '">' +
            '<div>' +
              '<div style="font-size:13px;font-weight:' + (isSelected ? "600" : "500") + '">' + escHtml(g.name) + badge + "</div>" +
              '<div style="font-size:11px;color:var(--color-text-tertiary)">メンバー ' + (g.member_count || 0) + "人</div>" +
            "</div>" +
            '<span style="font-size:10px;color:' + (isSelected ? "var(--color-text-success)" : "var(--color-text-tertiary)") + '">' + toggleIcon + "</span>" +
          "</div>" +
          '<div id="groups-body-' + escHtml(g.id) + '" style="display:' + bodyDisplay + ";padding:16px;border-top:1px solid var(--color-border)\">" +
            (isSelected ? '<div style="font-size:13px;color:var(--color-text-tertiary)">読み込み中...</div>' : "") +
          "</div>" +
        "</div>";
    });
    el.innerHTML = html;
    var items = el.querySelectorAll(".groups-item");
    for (var i = 0; i < items.length; i++) {
      items[i].addEventListener("click", function () {
        selectGroup(this.getAttribute("data-gid"));
      });
    }
  }

  function selectGroup(groupId) {
    _groupsState.selectedId = groupId;
    renderGroupsList();
    apiFetch("/groups/" + encodeURIComponent(groupId))
      .then(function (res) {
        if (!res.ok) throw new Error("failed");
        return res.json();
      })
      .then(renderGroupDetail)
      .catch(function () {
        var body = document.getElementById("groups-body-" + groupId);
        if (body) body.innerHTML = '<p style="color:var(--color-text-tertiary)">取得に失敗しました</p>';
      });
  }

  function renderGroupDetail(g) {
    var bodyEl = document.getElementById("groups-body-" + g.id);
    if (!bodyEl) return;

    var isAdmin = g.my_role === "admin";
    var members = (g.members || []).map(function (m) {
      var actions = "";
      if (isAdmin && m.role !== "admin") {
        actions = '<button class="admin-action-btn groups-remove-btn" data-uid="' + escHtml(m.user_id) + '" style="font-size:11px">除名</button>';
      } else if (!isAdmin && m.user_id === _meUserId()) {
        actions = '<button class="admin-action-btn groups-leave-btn" style="font-size:11px">退会</button>';
      }
      return '<tr><td>' + escHtml(m.username) + '</td><td>' + escHtml(m.email || "") + '</td><td>' + escHtml(m.role) + '</td><td>' + actions + '</td></tr>';
    }).join("");

    var inviteCodeBlock = "";
    if (isAdmin && g.invite_code) {
      inviteCodeBlock =
        '<div style="margin:8px 0">' +
        '<strong>招待コード:</strong> <code style="font-size:13px;padding:2px 6px;background:var(--color-bg-tertiary);border-radius:3px">' + escHtml(g.invite_code) + '</code>' +
        ' <button id="groups-rotate-btn" class="admin-action-btn" style="font-size:11px;margin-left:8px">再発行</button>' +
        "</div>";
    }

    var inviteByUser = "";
    if (isAdmin) {
      inviteByUser =
        '<div style="margin-top:16px;padding:12px;background:var(--color-bg-secondary);border-radius:4px">' +
        '<h4 style="font-size:13px;margin:0 0 8px 0">ユーザーを直接招待</h4>' +
        '<div style="display:flex;gap:8px">' +
        '<input type="text" id="groups-invite-username" placeholder="ユーザー名" style="flex:1;padding:4px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
        '<button id="groups-invite-btn" class="admin-action-btn">招待</button>' +
        "</div></div>";
    }

    var dangerZone = "";
    if (isAdmin) {
      dangerZone =
        '<div style="margin-top:24px">' +
        '<button id="groups-delete-btn" class="admin-action-btn" style="background:#dc2626;color:#fff">グループを削除</button>' +
        "</div>";
    }

    bodyEl.innerHTML =
      '<p style="color:var(--color-text-secondary);font-size:13px;margin:0 0 8px 0">' + escHtml(g.description || "") + "</p>" +
      inviteCodeBlock +
      '<h4 style="font-size:13px;margin:16px 0 8px 0">メンバー (' + (g.members || []).length + ")</h4>" +
      '<table class="admin-table"><thead><tr><th>ユーザー名</th><th>メール</th><th>ロール</th><th></th></tr></thead><tbody>' +
      members + "</tbody></table>" +
      inviteByUser +
      dangerZone;

    if (isAdmin) {
      var rot = bodyEl.querySelector("#groups-rotate-btn");
      if (rot) rot.addEventListener("click", function () { rotateInviteCode(g.id); });
      var invBtn = bodyEl.querySelector("#groups-invite-btn");
      if (invBtn) invBtn.addEventListener("click", function () {
        var u = bodyEl.querySelector("#groups-invite-username").value.trim();
        if (!u) return;
        inviteUser(g.id, u);
      });
      var del = bodyEl.querySelector("#groups-delete-btn");
      if (del) del.addEventListener("click", function () {
        if (!confirm("グループ「" + g.name + "」を削除します。よろしいですか？")) return;
        deleteGroup(g.id);
      });
      var removeBtns = bodyEl.querySelectorAll(".groups-remove-btn");
      for (var i = 0; i < removeBtns.length; i++) {
        removeBtns[i].addEventListener("click", function () {
          removeMember(g.id, this.getAttribute("data-uid"));
        });
      }
    } else {
      var leave = bodyEl.querySelector(".groups-leave-btn");
      if (leave) leave.addEventListener("click", function () {
        if (!confirm("グループを退会しますか？")) return;
        removeMember(g.id, _meUserId());
      });
    }
  }

  function _meUserId() {
    var decoded = parseJwtPayload(state.token);
    return decoded ? (decoded.sub || "") : "";
  }

  function createGroup() {
    var name = document.getElementById("groups-new-name").value.trim();
    var desc = document.getElementById("groups-new-desc").value.trim();
    if (!name) { setGroupsStatus("グループ名を入力してください", "error"); return; }
    apiFetch("/groups", {
      method: "POST",
      body: JSON.stringify({ name: name, description: desc }),
    })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw d; });
        return res.json();
      })
      .then(function (g) {
        setGroupsStatus("グループ「" + g.name + "」を作成しました。招待コード: " + g.invite_code, "success");
        document.getElementById("groups-new-name").value = "";
        document.getElementById("groups-new-desc").value = "";
        _groupsState.selectedId = g.id;
        loadGroups();
      })
      .catch(function (e) { setGroupsStatus("作成失敗: " + (e.detail || "不明なエラー"), "error"); });
  }

  function joinByCode() {
    var code = document.getElementById("groups-join-code").value.trim();
    if (!code) return;
    apiFetch("/groups/join-by-code", {
      method: "POST",
      body: JSON.stringify({ invite_code: code }),
    })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw d; });
        return res.json();
      })
      .then(function (g) {
        setGroupsStatus("グループ「" + g.name + "」に参加しました。", "success");
        document.getElementById("groups-join-code").value = "";
        _groupsState.selectedId = g.id;
        loadGroups();
      })
      .catch(function (e) { setGroupsStatus("参加失敗: " + (e.detail || "不明なエラー"), "error"); });
  }

  function rotateInviteCode(gid) {
    apiFetch("/groups/" + encodeURIComponent(gid) + "/invite-code/rotate", { method: "POST" })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        setGroupsStatus("招待コードを再発行しました: " + data.invite_code, "success");
        selectGroup(gid);
      });
  }

  function inviteUser(gid, username) {
    apiFetch("/groups/" + encodeURIComponent(gid) + "/members", {
      method: "POST",
      body: JSON.stringify({ username: username }),
    })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw d; });
        return res.json();
      })
      .then(function () {
        setGroupsStatus("ユーザー「" + username + "」を招待しました。", "success");
        var invInput = document.getElementById("groups-invite-username");
        if (invInput) invInput.value = "";
        selectGroup(gid);
      })
      .catch(function (e) { setGroupsStatus("招待失敗: " + (e.detail || "不明なエラー"), "error"); });
  }

  function removeMember(gid, uid) {
    apiFetch("/groups/" + encodeURIComponent(gid) + "/members/" + encodeURIComponent(uid), { method: "DELETE" })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw d; });
        setGroupsStatus("メンバーを削除しました。", "success");
        loadGroups();
      })
      .catch(function (e) { setGroupsStatus("削除失敗: " + (e.detail || "不明なエラー"), "error"); });
  }

  function deleteGroup(gid) {
    apiFetch("/groups/" + encodeURIComponent(gid), { method: "DELETE" })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw d; });
        setGroupsStatus("グループを削除しました。", "success");
        _groupsState.selectedId = null;
        loadGroups();
      })
      .catch(function (e) { setGroupsStatus("削除失敗: " + (e.detail || "不明なエラー"), "error"); });
  }

  function loadMyInvitations() {
    apiFetch("/me/invitations")
      .then(function (res) { return res.json(); })
      .then(function (list) {
        var el = document.getElementById("groups-my-invitations");
        if (!list || !list.length) { el.innerHTML = ""; return; }
        var html = '<div style="padding:12px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:4px;margin-bottom:12px">' +
          '<h4 style="font-size:13px;margin:0 0 8px 0">未承諾の招待 (' + list.length + ")</h4>";
        list.forEach(function (inv) {
          html += '<div style="display:flex;align-items:center;gap:8px;padding:4px 0">' +
            '<span style="flex:1;font-size:13px">グループ「' + escHtml(inv.group_name) + '」 (招待者: ' + escHtml(inv.inviter_username) + ")</span>" +
            '<button class="admin-action-btn groups-inv-accept" data-id="' + escHtml(inv.id) + '" style="font-size:11px;background:var(--color-text-success);color:#fff">承諾</button>' +
            '<button class="admin-action-btn groups-inv-decline" data-id="' + escHtml(inv.id) + '" style="font-size:11px">辞退</button>' +
            "</div>";
        });
        html += "</div>";
        el.innerHTML = html;
        var accepts = el.querySelectorAll(".groups-inv-accept");
        for (var i = 0; i < accepts.length; i++) {
          accepts[i].addEventListener("click", function () { respondInvitation(this.getAttribute("data-id"), "accept"); });
        }
        var declines = el.querySelectorAll(".groups-inv-decline");
        for (var j = 0; j < declines.length; j++) {
          declines[j].addEventListener("click", function () { respondInvitation(this.getAttribute("data-id"), "decline"); });
        }
      });
  }

  function respondInvitation(invId, action) {
    apiFetch("/me/invitations/" + encodeURIComponent(invId) + "/" + action, { method: "POST" })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw d; });
        setGroupsStatus("招待を" + (action === "accept" ? "承諾" : "辞退") + "しました。", "success");
        loadMyInvitations();
        loadGroups();
      })
      .catch(function (e) { setGroupsStatus("操作失敗: " + (e.detail || "不明なエラー"), "error"); });
  }

  function parseJwtPayload(token) {
    try {
      var parts = token.split(".");
      if (parts.length !== 3) return null;
      var payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(payload));
    } catch (e) { return null; }
  }

  // ── Course Management (Issue #125) ────────────────────────────────
  var _cmState = {
    courses: [],
    groups: [],
    currentCourseId: null,
    currentCourseTitle: "",
    perms: [],
  };

  function initCourseManagement() {
    var refreshBtn = document.getElementById("cm-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", loadCourseManagement);
    onTabActivate("course-management", loadCourseManagement);
  }

  function setCmStatus(msg, kind) {
    var el = document.getElementById("cm-status");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (kind || "info");
  }

  function loadCourseManagement() {
    var tbody = document.getElementById("cm-tbody");
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-tertiary)">読み込み中...</td></tr>';

    Promise.all([
      apiFetch("/admin/courses").then(function (res) { return res.json(); }),
      apiFetch("/groups").then(function (res) { return res.json(); }),
    ])
      .then(function (results) {
        _cmState.courses = results[0] || [];
        _cmState.groups = results[1] || [];
        // グループ権限マッピングをコース毎に取得
        if (!_cmState.courses.length) {
          tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-tertiary)">コースがありません</td></tr>';
          return;
        }
        return Promise.all(_cmState.courses.map(function (c) {
          return apiFetch("/admin/courses/" + encodeURIComponent(c.id) + "/groups")
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (perms) { c._perms = perms || []; return c; });
        })).then(renderCourseManagementTable);
      })
      .catch(function () {
        setCmStatus("コース一覧の取得に失敗しました", "error");
      });
  }

  function renderCourseManagementTable() {
    var tbody = document.getElementById("cm-tbody");
    if (!tbody) return;
    if (!_cmState.courses.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-tertiary)">コースがありません</td></tr>';
      return;
    }
    var html = "";
    _cmState.courses.forEach(function (c) {
      var roleBadge;
      if (c.role === "owner") {
        roleBadge = '<span style="font-size:10px;background:var(--color-text-success);color:#fff;padding:1px 6px;border-radius:3px">所有</span>';
      } else if (c.role === "viewer") {
        roleBadge = '<span style="font-size:10px;background:var(--color-text-tertiary);color:#fff;padding:1px 6px;border-radius:3px">viewer</span>';
      } else {
        roleBadge = '<span style="font-size:10px;background:var(--color-text-info);color:#fff;padding:1px 6px;border-radius:3px">editor</span>';
      }
      var perms = c._perms || [];
      var permsHtml;
      if (!perms.length) {
        permsHtml = '<span style="color:var(--color-text-tertiary);font-size:12px">未設定</span>';
      } else {
        permsHtml = perms.map(function (p) {
          var color = p.permission === "editor" ? "var(--color-text-info)" : "var(--color-text-success)";
          return '<span style="display:inline-block;margin:0 4px 4px 0;padding:2px 8px;font-size:11px;background:' + color + ';color:#fff;border-radius:3px">' +
            escHtml(p.group_name || p.group_id) + ' (' + escHtml(p.permission) + ')</span>';
        }).join("");
      }
      var actionHtml;
      if (c.role === "owner") {
        actionHtml = '<button class="cm-manage-btn admin-action-btn" data-course-id="' + escHtml(c.id) + '" data-course-title="' + escHtml(c.title) + '">共有設定</button>';
      } else {
        actionHtml = '<span style="font-size:11px;color:var(--color-text-tertiary)">所有者のみ変更可</span>';
      }
      html += '<tr>' +
        '<td>' + escHtml(c.title) + '</td>' +
        '<td>' + roleBadge + '</td>' +
        '<td>' + permsHtml + '</td>' +
        '<td>' + actionHtml + '</td>' +
        '</tr>';
    });
    tbody.innerHTML = html;
    tbody.querySelectorAll(".cm-manage-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openPermissionModal(this.getAttribute("data-course-id"), this.getAttribute("data-course-title"));
      });
    });
  }

  function openPermissionModal(courseId, courseTitle) {
    _cmState.currentCourseId = courseId;
    _cmState.currentCourseTitle = courseTitle;

    var existing = document.getElementById("cm-perm-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "cm-perm-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";

    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border-secondary);border-radius:8px;padding:24px;min-width:480px;max-width:640px;max-height:80vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">グループ共有設定</h3>' +
          '<button id="cm-perm-close-btn" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 12px">コース:「' + escHtml(courseTitle) + '」の共有グループと権限を管理します。</p>' +
        '<div id="cm-perm-status-inline" class="upload-status" style="display:none;margin-bottom:12px"></div>' +
        '<h4 style="font-size:13px;margin:0 0 8px 0;color:var(--color-text-secondary)">現在の共有設定</h4>' +
        '<div id="cm-perm-current" style="margin-bottom:16px;overflow-y:auto;flex:1"></div>' +
        '<hr style="border:none;border-top:1px solid var(--color-border);margin:4px 0 12px 0">' +
        '<h4 style="font-size:13px;margin:0 0 8px 0;color:var(--color-text-secondary)">グループを追加</h4>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
          '<select id="cm-perm-group-select" style="flex:1;padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)"></select>' +
          '<select id="cm-perm-role-select" style="padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
            '<option value="viewer">viewer (受講可)</option>' +
            '<option value="editor">editor (編集可)</option>' +
          '</select>' +
          '<button id="cm-perm-add-btn" class="admin-action-btn" style="background:var(--color-text-success);color:#fff">追加</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    document.getElementById("cm-perm-close-btn").addEventListener("click", function () {
      overlay.remove();
    });
    document.getElementById("cm-perm-add-btn").addEventListener("click", addPermissionMapping);

    loadPermissionModal();
  }

  function setPermStatus(msg, kind) {
    var el = document.getElementById("cm-perm-status-inline");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (kind || "info");
  }

  function loadPermissionModal() {
    var courseId = _cmState.currentCourseId;
    apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/groups")
      .then(function (res) { return res.json(); })
      .then(function (perms) {
        _cmState.perms = perms || [];
        renderPermissionCurrent();
        renderGroupDropdown();
      })
      .catch(function () { setPermStatus("共有設定の取得に失敗しました", "error"); });
  }

  function renderPermissionCurrent() {
    var el = document.getElementById("cm-perm-current");
    if (!el) return;
    if (!_cmState.perms.length) {
      el.innerHTML = '<p style="color:var(--color-text-tertiary);font-size:12px;margin:0">まだ共有グループは設定されていません。</p>';
      return;
    }
    var html = '<table class="admin-table"><thead><tr><th>グループ</th><th>権限</th><th>操作</th></tr></thead><tbody>';
    _cmState.perms.forEach(function (p) {
      html += '<tr>' +
        '<td>' + escHtml(p.group_name || p.group_id) + '</td>' +
        '<td>' + escHtml(p.permission) + '</td>' +
        '<td><button class="cm-perm-remove-btn admin-action-btn" data-gid="' + escHtml(p.group_id) + '" style="background:var(--color-text-danger);color:#fff">削除</button></td>' +
        '</tr>';
    });
    html += "</tbody></table>";
    el.innerHTML = html;
    el.querySelectorAll(".cm-perm-remove-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        removePermissionMapping(this.getAttribute("data-gid"));
      });
    });
  }

  function renderGroupDropdown() {
    var sel = document.getElementById("cm-perm-group-select");
    if (!sel) return;
    var assigned = {};
    _cmState.perms.forEach(function (p) { assigned[p.group_id] = true; });
    var html = '<option value="">グループを選択...</option>';
    _cmState.groups.forEach(function (g) {
      var suffix = assigned[g.id] ? " (設定済み)" : "";
      html += '<option value="' + escHtml(g.id) + '">' + escHtml(g.name) + suffix + '</option>';
    });
    sel.innerHTML = html;
  }

  function addPermissionMapping() {
    var groupSel = document.getElementById("cm-perm-group-select");
    var roleSel = document.getElementById("cm-perm-role-select");
    if (!groupSel.value) {
      setPermStatus("グループを選択してください", "error");
      return;
    }
    var courseId = _cmState.currentCourseId;
    setPermStatus("追加中...", "info");
    apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/groups", {
      method: "POST",
      body: JSON.stringify({ group_id: groupSel.value, permission: roleSel.value }),
    })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "追加に失敗しました"); });
        return res.json();
      })
      .then(function () {
        setPermStatus("追加しました", "success");
        loadPermissionModal();
        loadCourseManagement();
      })
      .catch(function (e) {
        setPermStatus(e.message || "追加に失敗しました", "error");
      });
  }

  function removePermissionMapping(groupId) {
    var courseId = _cmState.currentCourseId;
    setPermStatus("削除中...", "info");
    apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/groups/" + encodeURIComponent(groupId), {
      method: "DELETE",
    })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "削除に失敗しました"); });
        return res.json();
      })
      .then(function () {
        setPermStatus("削除しました", "success");
        loadPermissionModal();
        loadCourseManagement();
      })
      .catch(function (e) {
        setPermStatus(e.message || "削除に失敗しました", "error");
      });
  }

  // ── System Statistics (Issue #144, SYSTEM_ADMIN only) ──────────────
  var _ssAllStats = [];
  var _ssSortKey = "created_at";
  var _ssSortAsc = false;
  var _ssRunningTasks = {};

  function initSystemStats() {
    if (state.role !== "SYSTEM_ADMIN") return;

    onTabActivate("system-stats", loadSystemStats);

    document.getElementById("ss-refresh").addEventListener("click", loadSystemStats);
    document.getElementById("ss-teacher-filter").addEventListener("change", renderSystemStats);

    document.getElementById("ss-table").addEventListener("click", function (e) {
      var actionBtn = e.target.closest(".ss-generate-btn");
      if (actionBtn) {
        ssStartGeneration(
          actionBtn.getAttribute("data-course-id"),
          actionBtn.getAttribute("data-kind")
        );
        return;
      }

      var th = e.target.closest(".ss-sortable");
      if (!th) return;
      var key = th.dataset.sort;
      if (_ssSortKey === key) {
        _ssSortAsc = !_ssSortAsc;
      } else {
        _ssSortKey = key;
        _ssSortAsc = key === "title" || key === "uploaded_by";
      }
      renderSystemStats();
    });
  }

  function loadSystemStats() {
    var tbody = document.getElementById("ss-tbody");
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--color-text-tertiary)">読み込み中...</td></tr>';

    apiFetch("/admin/system/materials-stats")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        _ssAllStats = data;
        rebuildTeacherFilter(data);
        renderSystemStats();
      })
      .catch(function () {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--color-text-danger)">読み込みに失敗しました</td></tr>';
      });
  }

  function rebuildTeacherFilter(stats) {
    var sel = document.getElementById("ss-teacher-filter");
    var current = sel.value;
    var teachers = {};
    stats.forEach(function (s) { if (s.uploaded_by) teachers[s.uploaded_by] = true; });
    sel.innerHTML = '<option value="">全教員</option>';
    Object.keys(teachers).sort().forEach(function (name) {
      var opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      if (name === current) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  function renderSystemStats() {
    var filter = document.getElementById("ss-teacher-filter").value;
    var rows = _ssAllStats.filter(function (s) {
      return !filter || s.uploaded_by === filter;
    });

    rows.sort(function (a, b) {
      var av = a[_ssSortKey];
      var bv = b[_ssSortKey];
      if (typeof av === "string") av = av.toLowerCase();
      if (typeof bv === "string") bv = bv.toLowerCase();
      if (av < bv) return _ssSortAsc ? -1 : 1;
      if (av > bv) return _ssSortAsc ? 1 : -1;
      return 0;
    });

    // Update sort indicators
    document.querySelectorAll("#ss-table .ss-sortable").forEach(function (th) {
      var key = th.dataset.sort;
      th.innerHTML = th.innerHTML.replace(/[△▽]/g, "").trim();
      if (key === _ssSortKey) {
        th.innerHTML += " " + (_ssSortAsc ? "&#9651;" : "&#9661;");
      } else {
        th.innerHTML += " &#9651;";
      }
    });

    var tbody = document.getElementById("ss-tbody");
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--color-text-tertiary)">教材がありません</td></tr>';
      return;
    }

    var html = "";
    rows.forEach(function (s) {
      var createdAt = s.created_at ? new Date(s.created_at).toLocaleString("ja-JP") : "";
      html += "<tr>" +
        "<td>" + escHtml(s.title || "") + "</td>" +
        "<td>" + escHtml(s.uploaded_by || "") + "</td>" +
        "<td style='font-size:11px'>" + escHtml(createdAt) + "</td>" +
        "<td style='text-align:center'>" + (s.chunk_count || 0) + "</td>" +
        "<td>" + ssAnalysisCell(s) + "</td>" +
        "<td>" + ssGenerationCell(s, "script") + "</td>" +
        "<td>" + ssGenerationCell(s, "audio") + "</td>" +
        "<td style='text-align:center'>" + s.enrolled_students + "</td>" +
        "<td style='text-align:center'>" + s.chat_count + "</td>" +
        "</tr>";
    });
    tbody.innerHTML = html;
  }

  function ssGenerationCell(row, kind) {
    var pct = kind === "audio"
      ? row.audio_progress
      : kind === "structure"
        ? row.structure_progress
        : row.script_progress;
    var courseId = row.course_id || "";
    var runningKind = _ssRunningTasks[courseId] || ssTaskKind(row.active_task_type);
    var html = ssProgressBar(pct, runningKind === kind);
    var chunkCount = row.chunk_count || 0;

    if (runningKind === kind) {
      return html + '<div style="font-size:11px;color:var(--color-text-info);margin-top:4px">実行中...</div>';
    }
    if (runningKind) {
      return html;
    }
    if (chunkCount <= 0) {
      return html + '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:4px">チャンクなし</div>';
    }
    if (kind === "structure" && Math.round(row.structure_progress || 0) < 100) {
      return html + ssGenerateButton(
        row.course_id,
        "structure",
        Math.round(row.structure_progress || 0) === 0 ? "構造解析" : "構造再解析"
      );
    }
    if (kind === "script" && Math.round(row.script_progress || 0) < 100) {
      return html + ssGenerateButton(
        row.course_id,
        "script",
        Math.round(row.script_progress || 0) === 0 ? "原稿生成" : "原稿再実行"
      );
    }
    if (
      kind === "audio" &&
      Math.round(row.audio_progress || 0) < 100 &&
      Math.round(row.script_progress || 0) >= 100
    ) {
      return html + ssGenerateButton(
        row.course_id,
        "audio",
        Math.round(row.audio_progress || 0) === 0 ? "音声生成" : "音声再実行"
      );
    }
    return html;
  }

  function ssAnalysisCell(row) {
    var runningKind = _ssRunningTasks[row.course_id || ""] || ssTaskKind(row.active_task_type);
    var html = '<div class="ss-analysis-steps">' +
      ssMiniStep("DSL", row.structure_progress, runningKind === "structure" || runningKind === "analysis") +
      ssMiniStep("Claim", row.claim_progress, runningKind === "claims" || runningKind === "analysis") +
      ssMiniStep("Component", row.component_progress, runningKind === "components" || runningKind === "analysis") +
      ssMiniStep("Graph", row.graph_progress, runningKind === "graph" || runningKind === "analysis") +
      '</div>';
    if (runningKind) return html + '<div style="font-size:11px;color:var(--color-text-info);margin-top:4px">実行中...</div>';
    if ((row.chunk_count || 0) <= 0) {
      return html + '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:4px">チャンクなし</div>';
    }
    return html +
      '<div class="ss-analysis-actions">' +
        ssGenerateButton(row.course_id, "structure", "DSL") +
        ssGenerateButton(row.course_id, "claims", "Claim") +
        ssGenerateButton(row.course_id, "components", "Component") +
        ssGenerateButton(row.course_id, "graph", "Graph") +
        ssGenerateButton(row.course_id, "analysis", "全解析") +
      '</div>';
  }

  function ssMiniStep(label, pct, isRunning) {
    var p = Math.round(pct || 0);
    var cls = p >= 100 ? "done" : isRunning ? "running" : p > 0 ? "partial" : "";
    return '<div class="ss-analysis-step ' + cls + '">' +
      '<span>' + escHtml(label) + '</span>' +
      '<div><i style="width:' + p + '%"></i></div>' +
      '</div>';
  }

  function ssTaskKind(taskType) {
    if (taskType === "structure_reanalysis") return "structure";
    if (taskType === "claim_extraction") return "claims";
    if (taskType === "component_assembly") return "components";
    if (taskType === "component_graph_update") return "graph";
    if (taskType === "analysis_pipeline") return "analysis";
    if (taskType === "script_generation") return "script";
    if (taskType === "audio_generation") return "audio";
    return "";
  }

  function ssGenerateButton(courseId, kind, label) {
    return '<button class="admin-action-btn ss-generate-btn" data-kind="' + escHtml(kind) +
      '" data-course-id="' + escHtml(courseId || "") +
      '" style="margin-top:6px;padding:3px 8px;font-size:11px">' + label + '</button>';
  }

  function ssSetStatus(msg, type) {
    var el = document.getElementById("ss-status");
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (type || "info");
    el.style.display = "block";
  }

  function ssStartGeneration(courseId, kind) {
    if (!courseId || _ssRunningTasks[courseId]) return;

    var endpoint;
    var body;
    var label;
    if (kind === "structure") {
      endpoint = "/admin/courses/" + encodeURIComponent(courseId) + "/structure/reanalyze";
      body = "{}";
      label = "DSL解析";
    } else if (kind === "claims") {
      endpoint = "/admin/courses/" + encodeURIComponent(courseId) + "/claims/extract-all";
      body = "{}";
      label = "構成要素抽出";
    } else if (kind === "components") {
      endpoint = "/admin/courses/" + encodeURIComponent(courseId) + "/components/assemble-all";
      body = JSON.stringify({ force: false });
      label = "論理要素抽出";
    } else if (kind === "graph") {
      endpoint = "/admin/courses/" + encodeURIComponent(courseId) + "/component-graph/update";
      body = "{}";
      label = "グラフ更新";
    } else if (kind === "analysis") {
      endpoint = "/admin/courses/" + encodeURIComponent(courseId) + "/analysis/run-all";
      body = "{}";
      label = "全解析";
    } else if (kind === "audio") {
      endpoint = "/admin/courses/" + encodeURIComponent(courseId) + "/lecture-audio/generate";
      body = "{}";
      label = "音声生成";
    } else {
      endpoint = "/admin/courses/" + encodeURIComponent(courseId) + "/lecture-scripts/generate";
      body = JSON.stringify({ override: false });
      label = "原稿生成";
    }

    _ssRunningTasks[courseId] = kind;
    renderSystemStats();
    ssSetStatus(label + "を開始しています...", "info");

    apiFetch(endpoint, {
      method: "POST",
      body: body,
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            throw new Error((errBody && errBody.detail) || label + "を開始できませんでした");
          }, function () {
            throw new Error(label + "を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        ssSetStatus(label + "を開始しました。進捗を確認しています...", "info");
        ssPollTask(courseId, data.task_id, label);
      })
      .catch(function (err) {
        delete _ssRunningTasks[courseId];
        renderSystemStats();
        ssSetStatus((err && err.message) || label + "を開始できませんでした", "error");
      });
  }

  function ssPollTask(courseId, taskId, label) {
    var retryCount = 0;
    var maxRetries = 5;
    var intervalMs = 3000;

    function poll() {
      apiFetch("/admin/tasks/" + taskId)
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0;
          var rd = task.result_data || {};
          var progress = rd.progress || 0;
          var generated = rd.generated || 0;
          var skipped = rd.skipped || 0;
          var errors = rd.errors || 0;
          var updatedChunks = rd.updated_chunks || 0;
          var processedMaterials = rd.processed_materials || 0;

          if (task.status === "completed") {
            clearInterval(timer);
            var completedKind = _ssRunningTasks[courseId];
            delete _ssRunningTasks[courseId];
            if (completedKind === "structure") {
              ssSetStatus(
                label + "が完了しました: " + processedMaterials + "件の教材 / " +
                  updatedChunks + "件のチャンクを更新" +
                  (errors > 0 ? " / " + errors + "件エラー" : ""),
                errors > 0 ? "error" : "success"
              );
            } else {
              ssSetStatus(
                label + "が完了しました: " + generated + "件生成 / " + skipped + "件スキップ" +
                (errors > 0 ? " / " + errors + "件エラー" : ""),
                errors > 0 ? "error" : "success"
              );
            }
            loadSystemStats();
          } else if (task.status === "failed") {
            clearInterval(timer);
            delete _ssRunningTasks[courseId];
            renderSystemStats();
            ssSetStatus(label + "に失敗しました: " + (task.error_message || "不明なエラー"), "error");
          } else {
            if (_ssRunningTasks[courseId] === "structure") {
              ssSetStatus(label + "中... (" + progress + "%、更新チャンク " + updatedChunks + "件)", "info");
            } else {
              ssSetStatus(label + "中... (" + progress + "%)", "info");
            }
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            clearInterval(timer);
            delete _ssRunningTasks[courseId];
            renderSystemStats();
            ssSetStatus("進捗確認に失敗しました。更新ボタンで状況を確認してください。", "error");
          }
        });
    }

    var timer = setInterval(poll, intervalMs);
    poll();
  }

  function ssProgressBar(pct, isRunning) {
    var p = Math.round(pct || 0);
    var color;
    var label;
    if (p >= 100) {
      color = "var(--color-text-success)";
      label = "完了";
    } else if (isRunning) {
      color = "var(--color-text-info)";
      label = "処理中";
    } else if (p > 0) {
      color = "var(--color-text-info)";
      label = "未完了";
    } else {
      color = "var(--color-border)";
      label = "未着手";
    }
    return '<div style="display:flex;align-items:center;gap:6px">' +
      '<div style="flex:1;height:6px;background:var(--color-bg-tertiary);border-radius:3px;min-width:60px">' +
        '<div style="height:100%;width:' + p + '%;background:' + color + ';border-radius:3px;transition:width .3s"></div>' +
      '</div>' +
      '<span style="font-size:11px;white-space:nowrap;color:' + color + '">' + label + ' ' + p + '%</span>' +
    '</div>';
  }

  function initApp() {
    // Role-based access control
    if (!setupRoleBasedUI()) return;

    var usernameEl = document.getElementById("admin-username");
    if (usernameEl) usernameEl.textContent = state.username || "";

    initTabs();
    initErrorAnalysis();
    if (state.role !== "SYSTEM_ADMIN") {
      initUpload();
      initCourseBuilder();
      initCourseManagement();
      initLectureStudio();
    }
    initStumbles();
    initSchemaProposals();
    initSchemaEvolution();
    initUserManagement();
    initGroups();
    initSystemStats();
    initLogout();

    if (state.role !== "SYSTEM_ADMIN") {
      loadMaterials();
      document.getElementById("refresh-materials").addEventListener("click", loadMaterials);
    }
  }

  // Boot
  document.addEventListener("DOMContentLoaded", function () {
    // Check role before showing auth or app
    if (state.token && state.role === "STUDENT") {
      window.location.href = "/";
      return;
    }
    renderAuth();
    if (state.token) initApp();
  });
})();
