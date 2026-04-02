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
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-tertiary)">教材がまだアップロードされていません</td></tr>';
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
      html += "</tr>";
    });
    tbody.innerHTML = html;
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
      }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Chat failed");
        return res.json();
      })
      .then(function (data) {
        state.chatMessages.push({ role: "assistant", content: data.answer });
        state.chatHistory.push({ role: "user", content: text });
        state.chatHistory.push({ role: "assistant", content: data.answer });

        if (data.course_draft) {
          state.courseDraft = data.course_draft;
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

    // Build sources
    (draft.sources || []).forEach(function (s) {
      if (typeof s === "string") {
        courseData.sources.push({ title: s });
      } else {
        courseData.sources.push({
          title: s.title || "",
          subtitle: s.subtitle || "",
          license: s.license || "",
          used_section: s.used_section || "",
          arxiv_id: s.arxiv_id || "",
          material_id: s.material_id || "",
        });
      }
    });

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

        // A2: 「学生に公開する」ボタンを表示
        var approveArea = document.getElementById("cb-approve-area");
        if (approveArea && !document.getElementById("cb-publish-btn")) {
          var publishBtn = document.createElement("button");
          publishBtn.id = "cb-publish-btn";
          publishBtn.textContent = "学生に公開する";
          publishBtn.style.cssText = "margin-left:8px;padding:8px 16px;background:var(--color-text-info);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px";
          publishBtn.addEventListener("click", function () {
            publishCourse(data.id, publishBtn);
          });
          approveArea.appendChild(publishBtn);
        }

        // Show success message in chat
        state.chatMessages.push({
          role: "assistant",
          content: "コース「" + (data.title || draft.title) + "」が正常に登録されました。（ID: " + data.id + "）\n\n「学生に公開する」ボタンで学生が受講できるようになります。",
        });
        renderCourseChat();
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "承認してコースを登録";
        showUploadStatus("コースの登録に失敗しました。", "error");
      });
  }

  // A2: コースを学生に公開する
  function publishCourse(courseId, btn) {
    btn.disabled = true;
    btn.textContent = "公開中...";
    apiFetch("/admin/courses/" + courseId + "/publish", { method: "PUT" })
      .then(function (res) {
        if (!res.ok) throw new Error("Publish failed");
        return res.json();
      })
      .then(function () {
        btn.textContent = "公開済み ✓";
        btn.style.background = "var(--color-text-success)";
        state.chatMessages.push({
          role: "assistant",
          content: "コースを学生に公開しました。学習画面から「受講開始」ボタンで受講できるようになります。",
        });
        renderCourseChat();
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "学生に公開する";
      });
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
          if (c.is_published) {
            statusBadge = '<span style="font-size:10px;background:var(--color-text-success);color:#fff;padding:1px 6px;border-radius:3px;margin-left:6px">公開中</span>';
          } else if (c.is_template) {
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
            '<div class="import-course-item" data-course-id="' + escHtml(c.id) + '" style="padding:10px 12px;border:1px solid var(--color-border-secondary);border-radius:6px;margin-bottom:8px;cursor:pointer;transition:background 0.15s">' +
              '<div style="display:flex;justify-content:space-between;align-items:center">' +
                '<div>' +
                  '<div style="font-size:14px;color:var(--color-text-primary);font-weight:500">' + escHtml(c.title) + statusBadge + '</div>' +
                  '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:2px">ID: ' + escHtml(c.id) + (updatedAt ? ' | 更新: ' + updatedAt : '') + '</div>' +
                '</div>' +
                '<span style="font-size:12px;color:var(--color-text-info)">選択 &rarr;</span>' +
              '</div>' +
            '</div>';
        });
        listEl.innerHTML = html;

        // Add click handlers
        listEl.querySelectorAll(".import-course-item").forEach(function (item) {
          item.addEventListener("mouseenter", function () {
            this.style.background = "var(--color-background-tertiary)";
          });
          item.addEventListener("mouseleave", function () {
            this.style.background = "";
          });
          item.addEventListener("click", function () {
            var courseId = this.getAttribute("data-course-id");
            importCourse(courseId);
            overlay.remove();
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
          opt.textContent = c.title + (c.is_published ? " [公開中]" : "");
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
          opt.textContent = c.title + (c.is_published ? " [公開中]" : "");
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
  };

  function initLectureStudio() {
    var courseSelect = document.getElementById("ls-course-select");
    var generateAllBtn = document.getElementById("ls-generate-all-btn");
    var audioAllBtn = document.getElementById("ls-audio-all-btn");
    var saveBtn = document.getElementById("ls-save-btn");
    var rewriteBtn = document.getElementById("ls-rewrite-btn");

    courseSelect.addEventListener("change", function () {
      var courseId = this.value;
      if (courseId) {
        lsState.courseId = courseId;
        generateAllBtn.disabled = false;
        audioAllBtn.disabled = false;
        lsLoadScripts(courseId);
      } else {
        lsState.courseId = null;
        lsState.chunks = [];
        lsState.selectedChunkId = null;
        generateAllBtn.disabled = true;
        audioAllBtn.disabled = true;
        lsRenderChunkList();
        lsClearEditor();
      }
    });

    generateAllBtn.addEventListener("click", function () {
      if (!lsState.courseId || lsState.generating) return;
      lsBatchGenerate();
    });

    audioAllBtn.addEventListener("click", function () {
      if (!lsState.courseId || lsState.generating) return;
      lsBatchAudio();
    });

    saveBtn.addEventListener("click", function () {
      lsSaveScript();
    });

    rewriteBtn.addEventListener("click", function () {
      lsRewriteScript();
    });

    // Load courses on tab activation
    onTabActivate("lecture-studio", function () {
      lsLoadCourses();
    });

    lsLoadCourses();
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
          opt.textContent = c.title + (c.is_published ? " [公開中]" : "");
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
        lsRenderChunkList();
        lsClearEditor();
      })
      .catch(function () {
        listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">読み込みに失敗しました</div>';
      });
  }

  function lsRenderChunkList() {
    var listEl = document.getElementById("ls-chunk-list");
    if (!lsState.chunks || lsState.chunks.length === 0) {
      listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">チャンクがありません</div>';
      return;
    }

    var html = "";
    lsState.chunks.forEach(function (c, i) {
      var active = c.chunk_id === lsState.selectedChunkId ? " active" : "";
      var preview = (c.text || "").substring(0, 40).replace(/\n/g, " ");
      if (c.text && c.text.length > 40) preview += "...";
      html +=
        '<div class="ls-chunk-item' + active + '" data-chunk-id="' + escHtml(c.chunk_id) + '">' +
          '<span class="ls-chunk-status ' + escHtml(c.status) + '"></span>' +
          '<span class="ls-chunk-label">#' + (c.chunk_index || i) + " " + escHtml(preview) + '</span>' +
        '</div>';
    });
    listEl.innerHTML = html;

    // Bind click handlers
    listEl.querySelectorAll(".ls-chunk-item").forEach(function (item) {
      item.addEventListener("click", function () {
        var chunkId = this.getAttribute("data-chunk-id");
        lsSelectChunk(chunkId);
      });
    });
  }

  function lsSelectChunk(chunkId) {
    lsState.selectedChunkId = chunkId;
    var chunk = null;
    for (var i = 0; i < lsState.chunks.length; i++) {
      if (lsState.chunks[i].chunk_id === chunkId) {
        chunk = lsState.chunks[i];
        break;
      }
    }
    if (!chunk) return;

    // Update active class
    document.querySelectorAll(".ls-chunk-item").forEach(function (el) {
      el.classList.remove("active");
      if (el.getAttribute("data-chunk-id") === chunkId) el.classList.add("active");
    });

    // Fill editor
    var sourceEl = document.getElementById("ls-source-text");
    sourceEl.textContent = chunk.text || "(テキストなし)";

    var spokenEl = document.getElementById("ls-spoken-text");
    spokenEl.value = chunk.spoken_text || "";
    spokenEl.disabled = false;

    document.getElementById("ls-rewrite-prompt").disabled = false;
    document.getElementById("ls-rewrite-btn").disabled = false;
    document.getElementById("ls-save-btn").disabled = false;

    // Show formulas
    lsRenderFormulas(chunk.formulas || []);
  }

  function lsClearEditor() {
    document.getElementById("ls-source-text").innerHTML = '<span style="color:var(--color-text-tertiary)">チャンクを選択すると表示されます</span>';
    var spokenEl = document.getElementById("ls-spoken-text");
    spokenEl.value = "";
    spokenEl.disabled = true;
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
      html +=
        '<div class="ls-formula-item">' +
          '<span class="ls-formula-latex">' + escHtml(f.latex || f.id || "") + '</span><br>' +
          '<span class="ls-formula-spoken">' + escHtml(f.spoken || "") + '</span>' +
        '</div>';
    });
    el.innerHTML = html;
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

  function lsBatchGenerate() {
    lsState.generating = true;
    document.getElementById("ls-generate-all-btn").disabled = true;
    lsShowProgress("スクリプトを生成中...", "info");

    apiFetch("/admin/courses/" + lsState.courseId + "/lecture-scripts/generate", {
      method: "POST",
      body: JSON.stringify({ override: false }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Generation failed");
        return res.json();
      })
      .then(function (data) {
        lsState.chunks = data.chunks || [];
        lsRenderChunkList();
        lsShowProgress(
          "生成完了: " + data.generated + "件生成 / " + data.skipped + "件スキップ (全" + data.total_chunks + "件)",
          "success"
        );
      })
      .catch(function (err) {
        lsShowProgress("生成に失敗しました: " + (err.message || "不明なエラー"), "error");
      })
      .finally(function () {
        lsState.generating = false;
        document.getElementById("ls-generate-all-btn").disabled = false;
      });
  }

  function lsBatchAudio() {
    lsState.generating = true;
    document.getElementById("ls-audio-all-btn").disabled = true;
    lsShowProgress("音声を生成中... (数分かかる場合があります)", "info");

    apiFetch("/admin/courses/" + lsState.courseId + "/lecture-audio/generate", {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Audio generation failed");
        return res.json();
      })
      .then(function (data) {
        lsShowProgress(
          "音声生成完了: " + data.generated + "件生成 / " + data.skipped + "件スキップ" +
          (data.errors > 0 ? " / " + data.errors + "件エラー" : "") +
          " (全" + data.total_chunks + "件)",
          data.errors > 0 ? "error" : "success"
        );
        // Reload scripts to update statuses
        lsLoadScripts(lsState.courseId);
      })
      .catch(function (err) {
        lsShowProgress("音声生成に失敗しました: " + (err.message || "不明なエラー"), "error");
      })
      .finally(function () {
        lsState.generating = false;
        document.getElementById("ls-audio-all-btn").disabled = false;
      });
  }

  function lsSaveScript() {
    if (!lsState.selectedChunkId) return;

    var spokenText = document.getElementById("ls-spoken-text").value;
    document.getElementById("ls-save-btn").disabled = true;
    lsShowActionStatus("保存中...", "info");

    apiFetch("/admin/chunks/" + lsState.selectedChunkId + "/lecture-script", {
      method: "PUT",
      body: JSON.stringify({ spoken_text: spokenText, formulas: [] }),
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
    lsShowActionStatus("AIで書き換え中...", "info");

    apiFetch("/admin/chunks/" + lsState.selectedChunkId + "/lecture-script/rewrite", {
      method: "POST",
      body: JSON.stringify({ prompt: prompt }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Rewrite failed");
        return res.json();
      })
      .then(function (data) {
        // Update editor
        document.getElementById("ls-spoken-text").value = data.spoken_text;
        lsRenderFormulas(data.formulas || []);
        lsShowActionStatus("書き換え完了", "success");
        // Update local state
        for (var i = 0; i < lsState.chunks.length; i++) {
          if (lsState.chunks[i].chunk_id === lsState.selectedChunkId) {
            lsState.chunks[i].spoken_text = data.spoken_text;
            lsState.chunks[i].formulas = data.formulas || [];
            lsState.chunks[i].status = "edited";
            break;
          }
        }
        lsRenderChunkList();
      })
      .catch(function () {
        lsShowActionStatus("書き換えに失敗しました", "error");
      })
      .finally(function () {
        document.getElementById("ls-rewrite-btn").disabled = false;
      });
  }

  // ── Init ───────────────────────────────────────────────────────────
  function initApp() {
    // Role-based access control
    if (!setupRoleBasedUI()) return;

    var usernameEl = document.getElementById("admin-username");
    if (usernameEl) usernameEl.textContent = state.username || "";

    initTabs();
    initUpload();
    initCourseBuilder();
    initLectureStudio();
    initStumbles();
    initSchemaProposals();
    initSchemaEvolution();
    initUserManagement();
    initLogout();
    loadMaterials();

    document.getElementById("refresh-materials").addEventListener("click", loadMaterials);
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
