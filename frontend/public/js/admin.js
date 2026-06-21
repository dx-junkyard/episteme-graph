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
    currentSessionStatus: "draft",
    currentSessionPublishedCourseId: null,
    importedFromCourseId: null,
    availableMaterials: [],
    availableSessions: [],
    selectedMaterialIds: [],
    materialPipelineStatus: {},
    materialPipelineTimers: {},
    exportContext: null,
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

  var materialPipelineStageGroups = [
    {
      label: "文書構造を読む",
      stages: [
        ["document_structure", "DocumentStructureAgent"],
        ["paper_skeleton", "PaperSkeletonAgent"],
      ],
    },
    {
      label: "論述・主張を抽出する",
      stages: [
        ["rhetorical_role", "RhetoricalRoleAgent"],
        ["claim_qualification", "ClaimQualificationAgent"],
        ["equation_semantics", "EquationSemanticsAgent"],
      ],
    },
    {
      label: "根拠・派生関係を整理する",
      stages: [
        ["evidence_registry", "EvidenceRegistryBuilder"],
        ["claim_object_builder", "ClaimObjectBuilder"],
        ["symbol_registry", "SymbolRegistryBuilder"],
        ["derivation_chain", "DerivationChainAgent"],
        ["figure_table_semantics", "FigureTableSemanticsAgent"],
      ],
    },
    {
      label: "理論コンポーネントを組み立てる",
      stages: [
        ["thesis_reconstruction", "ThesisReconstructionAgent"],
        ["dsl_linking", "DSLLinkingAgent"],
        ["component_assembly", "ComponentAssemblyAgent"],
        ["component_graph", "ComponentGraphAgent"],
        ["narrative_annotator", "NarrativeAnnotator"],
      ],
    },
    {
      label: "コース化・出力を準備する",
      stages: [
        ["course_mapping", "CourseMappingAgent"],
        ["blueprint", "BlueprintAgent"],
        ["export_validation", "ExportValidationGate"],
      ],
    },
  ];
  var materialPipelineStages = materialPipelineStageGroups.reduce(function (stages, group) {
    return stages.concat(group.stages);
  }, []);

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
      html += '<td><div class="materials-action-cell">' +
        materialPipelineMenuHtml(m) +
        '<button class="admin-pdf-reupload-btn' + pdfBtnClass + '" data-material-id="' + escHtml(m.material_id) + '" title="' + escHtml(pdfBtnTitle) + '">' + pdfBtnLabel + '</button>' +
        resumeBtn +
        '<button class="admin-delete-btn" data-material-id="' + escHtml(m.material_id) + '" data-material-title="' + escHtml(m.title) + '" style="background:none;border:1px solid var(--color-text-danger);color:var(--color-text-danger);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:12px">削除</button>' +
        '</div></td>';
      html += "</tr>";
    });
    tbody.innerHTML = html;
    materials.forEach(function (m) {
      var mid = m.material_id || "";
      if (mid) loadMaterialPipelineStatus(mid);
    });
    bindMaterialPipelineMenus(tbody);

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

  function materialPipelineMenuHtml(material) {
    var mid = escHtml(material.material_id || "");
    var disabled = material.document_id ? "" : " disabled";
    var html =
      '<div class="material-pipeline-menu ls-action-menu" data-material-id="' + mid + '">' +
        '<button class="admin-action-btn ls-menu-trigger material-pipeline-trigger" type="button" data-material-id="' + mid + '"' + disabled + '>' +
          '<span class="ls-step-mark"></span><span>パイプラインを実行 ▼</span>' +
        '</button>' +
        '<div class="ls-menu material-pipeline-panel" hidden>' +
          '<button class="ls-menu-item material-pipeline-item" type="button" data-stage=""><span class="ls-step-mark"></span><span>パイプライン全実行</span></button>' +
          '<div class="ls-menu-group-label">個別ステージを実行</div>';
    materialPipelineStageGroups.forEach(function (group) {
      html += '<div class="ls-menu-stage-group">' +
        '<div class="ls-menu-group-label ls-menu-stage-heading">' + escHtml(group.label) + '</div>';
      group.stages.forEach(function (entry) {
        html += '<button class="ls-menu-item ls-menu-item-indent material-pipeline-item" type="button" data-stage="' + escHtml(entry[0]) + '">' +
          '<span class="ls-step-mark"></span><span>' + escHtml(entry[1]) + '</span></button>';
      });
      html += '</div>';
    });
    html +=
          '<div class="ls-menu-divider"></div>' +
          '<button class="ls-menu-item material-revision-item" type="button" data-document-id="' + escHtml(material.document_id || "") + '"' + disabled + '><span class="ls-step-mark"></span><span>反復改善（採用版を保ったまま）</span></button>' +
          '<button class="ls-menu-item material-export-item" type="button"><span class="ls-step-mark"></span><span>外部レビュー用に書き出し</span></button>' +
        '</div>' +
      '</div>';
    return html;
  }

  function bindMaterialPipelineMenus(root) {
    root.querySelectorAll(".material-pipeline-trigger").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var menu = this.closest(".material-pipeline-menu");
        var panel = menu && menu.querySelector(".material-pipeline-panel");
        if (!panel) return;
        var willOpen = panel.hidden;
        document.querySelectorAll(".ls-menu").forEach(function (m) { m.hidden = true; });
        panel.hidden = !willOpen;
      });
    });
    root.querySelectorAll(".material-pipeline-panel").forEach(function (panel) {
      panel.addEventListener("click", function (e) { e.stopPropagation(); });
    });
    root.querySelectorAll(".material-pipeline-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var menu = this.closest(".material-pipeline-menu");
        if (!menu) return;
        var panel = menu.querySelector(".material-pipeline-panel");
        if (panel) panel.hidden = true;
        if (!confirm("既存の実行結果を上書きします。本当に実行しますか？")) return;
        runMaterialPipeline(menu.getAttribute("data-material-id"), this.getAttribute("data-stage") || "");
      });
    });
    root.querySelectorAll(".material-revision-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var menu = this.closest(".material-pipeline-menu");
        var docId = this.getAttribute("data-document-id");
        if (menu) {
          var panel = menu.querySelector(".material-pipeline-panel");
          if (panel) panel.hidden = true;
        }
        if (!docId) { alert("この教材にはまだドキュメントが紐づいていません。"); return; }
        if (window.EGRevisions) window.EGRevisions.open(docId);
      });
    });
    root.querySelectorAll(".material-export-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var menu = this.closest(".material-pipeline-menu");
        if (!menu) return;
        var mid = menu.getAttribute("data-material-id");
        var status = state.materialPipelineStatus[mid] || {};
        state.exportContext = { scope: "document", documentId: status.document_id || "", materialId: mid };
        lsOpenExportModal(state.exportContext);
        var panel = menu.querySelector(".material-pipeline-panel");
        if (panel) panel.hidden = true;
      });
    });
  }

  function closeMaterialPipelineMenus() {
    document.querySelectorAll(".material-pipeline-panel").forEach(function (panel) {
      panel.hidden = true;
    });
  }

  function initMaterialPipelineOutsideClick() {
    if (state.materialPipelineOutsideClickBound) return;
    state.materialPipelineOutsideClickBound = true;
    document.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest(".material-pipeline-menu")) return;
      closeMaterialPipelineMenus();
    }, true);
  }

  function loadMaterialPipelineStatus(materialId) {
    apiFetch("/admin/materials/" + encodeURIComponent(materialId) + "/document-pipeline/status")
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (status) {
        if (!status) return;
        state.materialPipelineStatus[materialId] = status;
        updateMaterialPipelineMarks(materialId);
        if (status.active_task_id) {
          pollMaterialPipelineTask(materialId, status.active_task_id);
        }
      })
      .catch(function () {});
  }

  function pipelineVisual(status) {
    if (status === "completed") return "done";
    if (status === "running" || status === "pending" || status === "processing") return "running";
    if (status === "failed") return "error";
    return "pending";
  }

  function applyPipelineVisual(el, visual) {
    if (!el) return;
    ["done", "next", "running", "error", "pending"].forEach(function (name) {
      el.classList.toggle("ls-menu-item-" + name, visual === name);
    });
  }

  function updateMaterialPipelineMarks(materialId) {
    var status = state.materialPipelineStatus[materialId] || {};
    document.querySelectorAll('.material-pipeline-menu[data-material-id="' + CSS.escape(materialId) + '"]').forEach(function (menu) {
      applyPipelineVisual(menu.querySelector(".material-pipeline-trigger"), pipelineVisual(status.status));
      menu.querySelectorAll(".material-pipeline-item").forEach(function (btn) {
        var stage = btn.getAttribute("data-stage") || "";
        var visual = stage ? pipelineVisual((status.stages || {})[stage]) : pipelineVisual(status.status);
        applyPipelineVisual(btn, visual);
      });
      var exportBtn = menu.querySelector(".material-export-item");
      applyPipelineVisual(exportBtn, pipelineVisual(status.status));
      if (exportBtn) exportBtn.disabled = !status.document_id;
    });
  }

  function runMaterialPipeline(materialId, stage) {
    if (!materialId) return;
    var label = stage ? (materialPipelineStages.find(function (s) { return s[0] === stage; }) || ["", stage])[1] : "パイプライン全実行";
    showUploadStatus(label + "を開始しています...", "info");
    apiFetch("/admin/materials/" + encodeURIComponent(materialId) + "/document-pipeline/run", {
      method: "POST",
      body: JSON.stringify({ start_stage: stage || "" }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (body) {
            throw new Error((body && body.detail) || label + "を開始できませんでした");
          }, function () {
            throw new Error(label + "を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        state.materialPipelineStatus[materialId] = Object.assign({}, state.materialPipelineStatus[materialId] || {}, {
          status: "running",
          active_task_id: data.task_id,
          active_target_stage: "",
          active_start_stage: stage || "",
        });
        if (stage) {
          state.materialPipelineStatus[materialId].stages = state.materialPipelineStatus[materialId].stages || {};
          state.materialPipelineStatus[materialId].stages[stage] = "running";
        }
        updateMaterialPipelineMarks(materialId);
        showUploadStatus(label + "を開始しました。進捗を確認しています...", "info");
        pollMaterialPipelineTask(materialId, data.task_id);
      })
      .catch(function (err) {
        showUploadStatus((err && err.message) || label + "を開始できませんでした", "error");
        loadMaterialPipelineStatus(materialId);
      });
  }

  function pollMaterialPipelineTask(materialId, taskId) {
    if (!taskId) return;
    if (state.materialPipelineTimers[materialId]) return;
    var retryCount = 0;
    var timer = setInterval(function () {
      apiFetch("/admin/tasks/" + encodeURIComponent(taskId))
        .then(function (res) {
          if (!res.ok) throw new Error("Status check failed");
          return res.json();
        })
        .then(function (task) {
          retryCount = 0;
          var rd = task.result_data || {};
          var status = state.materialPipelineStatus[materialId] || {};
          status.status = task.status === "failed" ? "failed" : task.status === "completed" ? "completed" : "running";
          status.current_stage = rd.stage || status.current_stage || "";
          status.document_id = rd.document_id || status.document_id || "";
          status.stages = status.stages || {};
          if (rd.target_stage) status.stages[rd.target_stage] = status.status === "completed" ? "completed" : status.status === "failed" ? "failed" : "running";
          if (rd.start_stage) status.stages[rd.start_stage] = status.status === "completed" ? "completed" : status.status === "failed" ? "failed" : "running";
          if (status.current_stage && status.stages[status.current_stage] !== "completed") {
            status.stages[status.current_stage] = status.status === "failed" ? "failed" : "running";
          }
          state.materialPipelineStatus[materialId] = status;
          updateMaterialPipelineMarks(materialId);

          if (task.status === "completed" || task.status === "failed") {
            clearInterval(timer);
            delete state.materialPipelineTimers[materialId];
            showUploadStatus(
              (rd.label || "パイプライン") + (task.status === "completed" ? "が完了しました" : "に失敗しました: " + (task.error_message || "不明なエラー")),
              task.status === "completed" ? "success" : "error"
            );
            loadMaterialPipelineStatus(materialId);
            loadMaterials();
          } else {
            showUploadStatus((rd.label || "パイプライン") + "中... (" + (rd.progress || 0) + "%)", "info");
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= 5) {
            clearInterval(timer);
            delete state.materialPipelineTimers[materialId];
            showUploadStatus("パイプラインの進捗確認に失敗しました。更新ボタンで状況を確認してください。", "error");
            loadMaterialPipelineStatus(materialId);
          }
        });
    }, 3000);
    state.materialPipelineTimers[materialId] = timer;
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
    var lowerName = file.name.toLowerCase();
    if (!(lowerName.endsWith(".pdf") || lowerName.endsWith(".tar.gz") || lowerName.endsWith(".tgz"))) {
      showUploadStatus("PDF または TeX .tar.gz ファイルのみアップロードできます。", "error");
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
        state.availableSessions = sessions || [];
        renderSessionBar(sessions);
        // 初期表示は常に新規作成状態（前回セッションは自動復元しない）
        renderCourseChat();
        renderCoursePreview();
      })
      .catch(function () {
        state.availableSessions = [];
        var bar = document.getElementById("cb-session-bar");
        if (bar) bar.style.display = "none";
      });
  }

  function renderSessionBar(sessions) {
    var bar = document.getElementById("cb-session-bar");
    if (!bar) return;

    var selectHtml = '<select id="session-select" style="flex:1;padding:4px 6px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-primary);font-size:12px">';
    selectHtml += '<option value="">― 過去のセッションを選択 ―</option>';
    (sessions || []).forEach(function (s) {
      var label = s.display_name || s.title || s.session_id;
      if (s.status === "published") label += " [登録済]";
      selectHtml += '<option value="' + escHtml(s.session_id) + '">' + escHtml(label) + "</option>";
    });
    selectHtml += "</select>";

    bar.innerHTML =
      '<div style="display:flex;gap:8px;align-items:center;padding:6px 12px;border-bottom:1px solid var(--color-border)">' +
      selectHtml +
      '<button id="new-session-btn" style="padding:4px 10px;font-size:12px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-primary);cursor:pointer;white-space:nowrap">+ 新規</button>' +
      '<button id="import-course-btn" style="padding:4px 10px;font-size:12px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-info);cursor:pointer;white-space:nowrap">既存コースを読込</button>' +
      "</div>";

    // 現在選択中のセッションがあれば反映（なければ空欄のまま）
    var sel = document.getElementById("session-select");
    if (sel && state.currentSessionId) {
      sel.value = state.currentSessionId;
    }

    sel.addEventListener("change", function () {
      if (this.value) {
        selectSession(this.value);
      } else {
        // 未選択に戻した場合は新規作成状態にリセット
        resetToNewSession();
      }
    });
    document.getElementById("new-session-btn").addEventListener("click", function () {
      resetToNewSession();
      sel.value = "";
    });
    document.getElementById("import-course-btn").addEventListener("click", function () {
      openImportCourseModal();
    });
  }

  function resetToNewSession() {
    state.currentSessionId = null;
    state.currentSessionStatus = "draft";
    state.currentSessionPublishedCourseId = null;
    state.chatHistory = [];
    state.chatMessages = [];
    state.courseDraft = null;
    state.importedFromCourseId = null;
    renderCourseChat();
    renderCoursePreview();
  }

  function createNewSession() {
    // 後方互換: importCourse等から呼ばれた場合のみ即時作成
    return _createSessionNow("", null);
  }

  function _createSessionNow(title, sourceFileName) {
    return apiFetch("/admin/course-builder/sessions", {
      method: "POST",
      body: JSON.stringify({ title: title || "", source_file_name: sourceFileName || null }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.currentSessionId = data.session_id;
        state.currentSessionStatus = data.status || "draft";
        state.currentSessionPublishedCourseId = data.published_course_id || null;
        reloadSessionBar(data.session_id);
        return data;
      });
  }

  function reloadSessionBar(selectId) {
    apiFetch("/admin/course-builder/sessions")
      .then(function (res) { return res.json(); })
      .then(function (sessions) {
        state.availableSessions = sessions || [];
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
        state.currentSessionStatus = data.status || "draft";
        state.currentSessionPublishedCourseId = data.published_course_id || null;
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

  function _getSrcFileNameFromSelection() {
    if (!state.selectedMaterialIds || state.selectedMaterialIds.length === 0) return null;
    for (var i = 0; i < state.availableMaterials.length; i++) {
      var m = state.availableMaterials[i];
      if ((m.material_id || m.id) === state.selectedMaterialIds[0]) {
        var fn = m.filename || m.title || "";
        return fn.replace(/\.pdf$/i, "").replace(/\s+/g, "_") || null;
      }
    }
    return null;
  }

  function sendCourseChat(text) {
    if (!text || state.sending) return;

    // 遅延セッション作成: 初回送信時にセッションを作成する
    if (!state.currentSessionId) {
      var srcFileName = _getSrcFileNameFromSelection();
      _createSessionNow("", srcFileName)
        .then(function () { sendCourseChat(text); })
        .catch(function () { sendCourseChat(text); });
      return;
    }

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

    // 登録済みセッションは承認ボタンを無効化
    var approveBtn = document.getElementById("cb-approve-btn");
    if (approveBtn) {
      var isPublished = state.currentSessionStatus === "published" || !!state.currentSessionPublishedCourseId;
      if (isPublished) {
        approveBtn.disabled = true;
        approveBtn.textContent = "登録済み";
        approveBtn.style.background = "var(--color-bg-tertiary)";
        approveBtn.style.color = "var(--color-text-tertiary)";
        approveBtn.style.cursor = "not-allowed";
        approveBtn.style.opacity = "0.7";
      } else {
        approveBtn.disabled = false;
        approveBtn.textContent = "承認してコースを登録";
        approveBtn.style.background = "";
        approveBtn.style.color = "";
        approveBtn.style.cursor = "";
        approveBtn.style.opacity = "";
      }
    }
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

        var newCourseId = data.id;

        // セッションの status を published に更新
        state.currentSessionStatus = "published";
        state.currentSessionPublishedCourseId = newCourseId;
        renderCoursePreview();
        if (state.currentSessionId) {
          apiFetch("/admin/course-builder/sessions/" + state.currentSessionId, {
            method: "PUT",
            body: JSON.stringify({
              status: "published",
              published_course_id: newCourseId,
              title: draft.title || "",
            }),
          }).then(function () {
            reloadSessionBar(state.currentSessionId);
          }).catch(function () {});
        }

        var successMsg = {
          role: "assistant",
          content: "コース「" + (data.title || draft.title) + "」が正常に登録されました。（ID: " + newCourseId + "）\n\n「コース管理」タブからグループ単位で受講可／編集可の権限を設定できます。\n\nコース内容を自動生成中...",
        };
        state.chatMessages.push(successMsg);
        renderCourseChat();

        apiFetch("/admin/courses/" + newCourseId + "/course-content/generate", {
          method: "POST",
          body: "{}",
        })
          .then(function (genRes) {
            if (genRes.ok) {
              successMsg.content = "コース「" + (data.title || draft.title) + "」が正常に登録されました。（ID: " + newCourseId + "）\n\n「コース管理」タブからグループ単位で受講可／編集可の権限を設定できます。\n\nコース内容の自動生成を開始しました。完了後は原稿スタジオで確認できます。";
            } else {
              successMsg.content = "コース「" + (data.title || draft.title) + "」が正常に登録されました。（ID: " + newCourseId + "）\n\n「コース管理」タブからグループ単位で受講可／編集可の権限を設定できます。\n\n（コース内容の自動生成を開始できませんでした。原稿スタジオから手動で実行してください。）";
            }
            renderCourseChat();
          })
          .catch(function () {
            successMsg.content = "コース「" + (data.title || draft.title) + "」が正常に登録されました。（ID: " + newCourseId + "）\n\n「コース管理」タブからグループ単位で受講可／編集可の権限を設定できます。\n\n（コース内容の自動生成を開始できませんでした。原稿スタジオから手動で実行してください。）";
            renderCourseChat();
          });
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
          body: JSON.stringify({ title: "再編集: " + courseTitle, source_file_name: null }),
        })
          .then(function (res) { return res.json(); })
          .then(function (sessionData) {
            var sessionId = sessionData.session_id;
            state.currentSessionStatus = sessionData.status || "draft";
            state.currentSessionPublishedCourseId = sessionData.published_course_id || null;

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
    courseDraftView: "preview",
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
    // Issue #306: 表示するグラフ層 ("main" | "equation_detail" | "all")。
    graphLayerFilter: "main",
    selectedScope: null,
    selectedTheoryComponentId: null,
    analysisStatus: null,
    pipelineTask: null,
    theoryLoading: false,
    claimsLoading: false,
    graphLoading: false,
    // Issue #232: 左ペインタブ
    leftTab: "course",
    rightPaneVisible: true,
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

  function lsSelectedCourseTitle() {
    var select = document.getElementById("ls-course-select");
    if (!select || !lsState.courseId) return "";
    var opt = select.options[select.selectedIndex];
    return opt ? opt.textContent : "";
  }

  function lsUpdateCourseShell() {
    var selected = Boolean(lsState.courseId);
    var select = document.getElementById("ls-course-select");
    var label = document.getElementById("ls-course-select-label");
    var current = document.getElementById("ls-course-current");
    var resetBtn = document.getElementById("ls-course-reset-btn");
    var empty = document.getElementById("ls-empty-course");
    var workarea = document.getElementById("ls-studio-workarea");
    if (select) select.hidden = selected;
    if (label) label.hidden = selected;
    if (resetBtn) resetBtn.hidden = !selected;
    if (current) {
      current.hidden = !selected;
      current.textContent = selected ? "コース: " + (lsSelectedCourseTitle() || lsState.courseId) : "コース: -";
    }
    if (empty) empty.hidden = selected;
    if (workarea) workarea.hidden = !selected;
    lsUpdateRightPaneToggle();
    lsUpdateAssistantOpenButton();
  }

  function lsUpdateAssistantOpenButton() {
    var btn = document.getElementById("ls-ai-assistant-btn");
    var promptEl = document.getElementById("ls-rewrite-prompt");
    if (!btn) return;
    btn.disabled = !lsState.courseId || !promptEl || promptEl.disabled;
  }

  function lsOpenAssistantModal() {
    var modal = document.getElementById("ls-assistant-modal");
    var promptEl = document.getElementById("ls-rewrite-prompt");
    if (!modal || !promptEl || promptEl.disabled) return;
    lsUpdateAssistantContext();
    modal.hidden = false;
    setTimeout(function () { promptEl.focus(); }, 0);
  }

  function lsCloseAssistantModal() {
    var modal = document.getElementById("ls-assistant-modal");
    if (modal) modal.hidden = true;
  }

  function lsUpdateRightPaneToggle() {
    var btn = document.getElementById("ls-right-pane-toggle");
    if (!btn) return;
    btn.disabled = !lsState.courseId || !document.getElementById("ls-right-pane");
    btn.textContent = lsState.rightPaneVisible ? "右ペインを隠す" : "右ペインを表示";
  }

  function lsApplyRightPaneVisibility() {
    var splitEl = document.querySelector("#ls-workspace .ls-split");
    if (splitEl) splitEl.classList.toggle("ls-right-pane-collapsed", !lsState.rightPaneVisible);
    lsUpdateRightPaneToggle();
  }

  function lsSetRightPaneVisible(visible) {
    lsState.rightPaneVisible = visible !== false;
    lsApplyRightPaneVisibility();
  }

  function lsIsTheoryGraphView(view) {
    return view === "claims" || view === "theory" || view === "graph";
  }

  function lsTopWorkViewsForCurrentMode() {
    if (lsState.leftTab === "course") return ["edit"];
    if (lsState.leftTab === "document") return ["edit", "structure", "graph"];
    return [];
  }

  function lsNormalizeViewForCurrentMode(view) {
    if (lsState.leftTab === "course") return "edit";
    if (lsState.leftTab === "components") return "theory";
    if (view === "audio") return "edit";
    if (!view) return "edit";
    return view;
  }

  function lsUpdateWorkTabActive() {
    var view = lsNormalizeViewForCurrentMode(lsState.view || "edit");
    lsState.view = view;
    var isCourseTopic = lsState.selectedScope && lsState.selectedScope.type === "course_topic";
    if (isCourseTopic) {
      view = "edit";
      lsState.view = view;
    }
    var topView = lsIsTheoryGraphView(view) ? "graph" : view;
    var topTabs = document.getElementById("ls-work-tabs");
    var topViews = lsTopWorkViewsForCurrentMode();
    if (topTabs) {
      topTabs.hidden = topViews.length === 0;
      topTabs.querySelectorAll(".ls-work-tab").forEach(function (b) {
        var tabView = b.getAttribute("data-ls-view");
        b.hidden = topViews.indexOf(tabView) === -1;
        b.classList.toggle("active", tabView === topView);
      });
    }
    var theoryTabs = document.getElementById("ls-theory-graph-tabs");
    if (theoryTabs) {
      theoryTabs.hidden = lsState.leftTab !== "document" || !lsIsTheoryGraphView(view);
      theoryTabs.querySelectorAll(".ls-work-tab").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-ls-view") === view);
      });
    }
  }

  function initLectureStudio() {
    var courseSelect = document.getElementById("ls-course-select");
    var audioAllBtn = document.getElementById("ls-audio-all-btn");
    var settingsBtn = document.getElementById("ls-settings-btn");
    var courseContentBtn = document.getElementById("ls-course-content-btn");
    var moreMenuBtn = document.getElementById("ls-more-menu-btn");
    var saveBtn = document.getElementById("ls-save-btn");
    var rewriteBtn = document.getElementById("ls-rewrite-btn");
    var resetCourseBtn = document.getElementById("ls-course-reset-btn");
    var assistantBtn = document.getElementById("ls-ai-assistant-btn");
    var assistantCloseBtn = document.getElementById("ls-assistant-close");
    var assistantModal = document.getElementById("ls-assistant-modal");
    var rightPaneToggle = document.getElementById("ls-right-pane-toggle");

    lsBindMenu("ls-more-menu-btn", "ls-more-menu");

    courseSelect.addEventListener("change", function () {
      var courseId = this.value;
      if (courseId) {
        lsState.courseId = courseId;
        // ボタンはチャンク読み込み完了後に lsRenderChunkList で制御するため
        // ここでは一旦無効化して読み込みを待つ
        if (audioAllBtn) audioAllBtn.disabled = true;
        settingsBtn.disabled = false;
        if (courseContentBtn) courseContentBtn.disabled = false;
        moreMenuBtn.disabled = false;
        lsState.rightPaneVisible = true;
        lsUpdateCourseShell();
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
        if (audioAllBtn) audioAllBtn.disabled = true;
        settingsBtn.disabled = true;
        if (courseContentBtn) courseContentBtn.disabled = true;
        moreMenuBtn.disabled = true;
        lsRenderChunkList();
        lsRenderLeftPanel();
        lsClearEditor();
        lsCloseAssistantModal();
        lsUpdateCourseShell();
      }
    });

    if (resetCourseBtn) resetCourseBtn.addEventListener("click", function () {
      courseSelect.value = "";
      courseSelect.dispatchEvent(new Event("change"));
    });

    if (assistantBtn) assistantBtn.addEventListener("click", function () {
      lsOpenAssistantModal();
    });

    if (assistantCloseBtn) assistantCloseBtn.addEventListener("click", function () {
      lsCloseAssistantModal();
    });

    if (assistantModal) assistantModal.addEventListener("click", function (e) {
      if (e.target === assistantModal) lsCloseAssistantModal();
    });

    if (rightPaneToggle) rightPaneToggle.addEventListener("click", function () {
      lsSetRightPaneVisible(!lsState.rightPaneVisible);
    });

    if (audioAllBtn) audioAllBtn.addEventListener("click", function () {
      if (!lsState.courseId || lsState.generating) return;
      if (!lsIsCourseContentComplete()) return;
      lsCloseMenus();
      lsBatchAudio();
    });

    settingsBtn.addEventListener("click", function () {
      if (!lsState.courseId) return;
      lsCloseMenus();
      lsOpenSettingsModal();
    });

    if (courseContentBtn) courseContentBtn.addEventListener("click", function () {
      if (!lsState.courseId) return;
      lsCloseMenus();
      lsConfirmCourseContentGeneration();
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
      if (!btn || btn.hidden) return;
      lsState.view = btn.getAttribute("data-ls-view") || "edit";
      lsUpdateWorkTabActive();
      lsRenderWorkspace();
    });

    document.getElementById("ls-theory-graph-tabs").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-ls-view]");
      if (!btn || btn.hidden) return;
      lsState.view = btn.getAttribute("data-ls-view") || "claims";
      lsUpdateWorkTabActive();
      lsRenderWorkspace();
    });

    // Load courses on tab activation
    onTabActivate("lecture-studio", function () {
      lsLoadCourses();
    });

    lsUpdateWorkTabActive();
    lsUpdateCourseShell();
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

  function lsOpenExportModal(context) {
    state.exportContext = context || null;
    var overlay = document.getElementById("export-modal-overlay");
    document.getElementById("export-status").textContent = "";
    var courseOpt = document.querySelector('input[name="export-scope"][value="course"]');
    var docOpt = document.querySelector('input[name="export-scope"][value="document"]');
    if (courseOpt && docOpt) {
      if (state.exportContext && state.exportContext.scope === "document") {
        docOpt.checked = true;
        courseOpt.disabled = true;
      } else {
        courseOpt.disabled = false;
      }
    }
    overlay.style.display = "flex";
  }

  function lsCloseExportModal() {
    state.exportContext = null;
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
    if (state.exportContext && state.exportContext.scope === "document") {
      scopeVal = "document";
      scopeId = state.exportContext.documentId || state.exportContext.materialId || "";
      endpoint = "/documents/" + scopeId + "/export-bundle";
    } else if (scopeVal === "course") {
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
        lsUpdateCourseShell();
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

  function lsResetWorkspaceForNavTab(tab) {
    lsState.selectedChunkId = null;
    lsState.selectedScope = null;
    lsState.selectedTheoryComponentId = null;
    lsState.displayView = "preview";
    if (tab === "course") {
      lsState.view = "edit";
      lsClearEditor("コースのトピックを選択すると授業用ドラフトが表示されます", "トピックを選択してください");
    } else if (tab === "document") {
      lsState.view = "edit";
      lsClearEditor("チャンクを選択すると編集ワークベンチが表示されます", "チャンクを選択してください");
    } else if (tab === "components") {
      lsState.view = "theory";
      lsClearEditor("コンポーネントを選択すると詳細が表示されます", "コンポーネントを選択してください");
    }
    lsUpdateAssistantContext();
  }

  function lsSwitchNavTab(tab) {
    var changed = lsState.leftTab !== tab;
    lsState.leftTab = tab;
    if (changed) lsResetWorkspaceForNavTab(tab);
    document.querySelectorAll(".ls-nav-tab").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-ls-nav") === tab);
    });
    var courseList = document.getElementById("ls-course-list");
    var chunkList = document.getElementById("ls-chunk-list");
    var componentsList = document.getElementById("ls-components-list");
    if (courseList) courseList.hidden = tab !== "course";
    if (chunkList) chunkList.hidden = tab !== "document";
    if (componentsList) componentsList.hidden = tab !== "components";
    lsUpdateWorkTabActive();
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
        lsUpdateCourseControls();
        if (lsState.leftTab === "course") lsRenderCourseStructure();
      })
      .catch(function () {
        lsState.courseStructure = null;
        lsUpdateCourseControls();
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
        var ci = parseInt(item.getAttribute("data-chapter") || "0", 10);
        var ti = parseInt(item.getAttribute("data-topic") || "0", 10);
        var chapter = (lsState.courseStructure && lsState.courseStructure.chapters || [])[ci];
        var topic = chapter && (chapter.topics || [])[ti];
        if (topic) {
          lsState.selectedScope = { type: "course_topic", chapterIndex: ci, topicIndex: ti, topicId: topic.id || "" };
          lsState.selectedChunkId = null;
          lsState.selectedTheoryComponentId = null;
          lsState.view = "edit";
          lsRenderWorkspace();
        }
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
          var targetStage = rd.start_stage || rd.target_stage || "";
          var label = targetStage ? lsAgentStageLabels[targetStage] || targetStage : "パイプライン全実行";
          lsState.pipelineTask = targetStage
            ? { step: "document_pipeline", stage: targetStage, status: "running" }
            : { step: "document_pipeline", status: "running" };
          lsSetCourseTaskBusy(true);
          lsShowProgress(label + "が進行中です... (進捗: " + (rd.progress || 0) + "%)", "info");
          lsPollGenericCourseTask(task.task_id, label, "document_pipeline", targetStage);
        } else if (task.task_type === "course_content_generation") {
          lsState.pipelineTask = { step: "course_content", status: "running" };
          lsSetCourseTaskBusy(true);
          lsShowProgress("コース内容生成が進行中です... (進捗: " + (rd.progress || 0) + "%)", "info");
          lsPollCourseContentTask(task.task_id);
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
    var audioAllBtn = document.getElementById("ls-audio-all-btn");
    var settingsBtn = document.getElementById("ls-settings-btn");

    if (!lsState.chunks || lsState.chunks.length === 0) {
      listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">' +
        '教材のチャンクが見つかりません。<br>' +
        '教材がコースに紐づけられているか、PDF解析が完了しているかを確認してください。</div>';
      if (audioAllBtn) audioAllBtn.disabled = true;
      if (settingsBtn) settingsBtn.disabled = !lsState.courseId;
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
    lsState.selectedTheoryComponentId = null;
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
    lsState.selectedTheoryComponentId = null;
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
    lsState.selectedTheoryComponentId = null;
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

  function lsGetSelectedCourseTopic() {
    if (!lsState.selectedScope || lsState.selectedScope.type !== "course_topic") return null;
    var chapter = (lsState.courseStructure && lsState.courseStructure.chapters || [])[lsState.selectedScope.chapterIndex];
    if (!chapter) return null;
    return (chapter.topics || [])[lsState.selectedScope.topicIndex] || null;
  }

  function lsScopeHasDocumentContext(scope) {
    if (!scope) return false;
    return scope.type === "paper" || scope.type === "section" || scope.type === "chunk";
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
    lsApplyRightPaneVisibility();
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
    var formulasEl = document.getElementById("ls-formulas");
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
    if (formulasEl) formulasEl.hidden = true;

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

  function lsRenderSelectedCourseTopic(topic) {
    lsEnsureWorkspace();
    lsState.view = "edit";
    lsUpdateWorkTabActive();
    var metaEl = document.getElementById("ls-chunk-meta");
    if (metaEl) {
      var confidence = topic.content_confidence && topic.content_confidence !== "none"
        ? " / " + topic.content_confidence
        : "";
      metaEl.textContent = "コーストピック: " + (topic.title || "無題") + confidence;
    }

    var sourceEl = document.getElementById("ls-source-text");
    var displayEl = document.getElementById("ls-display-text");
    var spokenEl = document.getElementById("ls-spoken-text");
    var leftTitle = document.getElementById("ls-left-title");
    var rightTitle = document.getElementById("ls-right-title");
    if (leftTitle) leftTitle.textContent = "授業用ドラフト";
    if (rightTitle) rightTitle.textContent = "根拠リンク";
    if (sourceEl) {
      sourceEl.innerHTML = lsCourseDraftHtml(topic);
      sourceEl.hidden = false;
      lsBindCourseDraftControls(topic);
    }
    if (displayEl) {
      displayEl.value = lsTopicStudentMaterialSource(topic);
      displayEl.disabled = true;
      displayEl.hidden = true;
    }
    if (spokenEl) {
      spokenEl.value = topic.spoken_script || topic.content || topic.summary || "";
      spokenEl.disabled = true;
      spokenEl.hidden = true;
    }

    document.getElementById("ls-display-tabs").hidden = true;
    document.getElementById("ls-evidence-tabs").hidden = true;
    document.getElementById("ls-left-pane").hidden = false;
    document.getElementById("ls-right-pane").hidden = false;
    var splitEl = document.querySelector("#ls-workspace .ls-split");
    if (splitEl) splitEl.hidden = false;
    document.getElementById("ls-structure-panel").hidden = true;
    document.getElementById("ls-theory-panel").hidden = true;
    document.getElementById("ls-claims-panel").hidden = true;
    document.getElementById("ls-graph-panel").hidden = true;
    document.getElementById("ls-sync-row").hidden = true;
    document.getElementById("ls-pdf-view").hidden = true;
    document.getElementById("ls-source-text").hidden = false;
    document.getElementById("ls-display-preview").hidden = false;

    var preview = document.getElementById("ls-display-preview");
    if (preview) preview.innerHTML = lsCourseEvidenceHtml(topic);
    var saveBtn = document.getElementById("ls-save-btn");
    if (saveBtn) saveBtn.disabled = false;
    var rewriteBtn = document.getElementById("ls-rewrite-btn");
    if (rewriteBtn) rewriteBtn.disabled = true;
    lsApplyRightPaneVisibility();
    lsUpdateAssistantContext();
  }

  function lsTopicCoverageStatus(topic) {
    if (topic.coverage && topic.coverage.status) return topic.coverage.status;
    if (topic.content_source === "source_excerpt" && topic.content_confidence === "none") return "missing";
    if (topic.content_confidence === "none") return "weak";
    if (topic.content_confidence) return "sufficient";
    return (topic.linked_component_ids && topic.linked_component_ids.length) ? "sufficient" : "weak";
  }

  function lsTopicCoverageMessage(topic) {
    if (topic.coverage && topic.coverage.message) return topic.coverage.message;
    var status = lsTopicCoverageStatus(topic);
    if (status === "missing") {
      return "このトピックに対応するClaimまたはコンポーネントの対応付けが弱いため、本文説明を自動生成するには確認が必要です。";
    }
    if (status === "weak") return "根拠候補はありますが、授業用ドラフトとして使う前に教員確認が必要です。";
    return "授業用ドラフトを支える根拠候補があります。";
  }

  function lsTopicStudentMaterialSource(topic) {
    var material = topic.student_material || {};
    if (material.source_text) return material.source_text;
    var lines = [];
    if (topic.title) lines.push("## " + topic.title);
    if (topic.summary) lines.push("", topic.summary);
    var formulas = lsTopicFormulas(topic);
    formulas.forEach(function (formula) {
      if (formula.id) lines.push("", "![[" + "equation:" + formula.id + "]]");
    });
    return lines.join("\n").trim();
  }

  function lsListText(value) {
    if (Array.isArray(value)) return value.map(function (item) {
      return typeof item === "object" && item ? (item.question || item.text || "") : item;
    }).filter(Boolean).join("\n");
    if (typeof value === "string") return value;
    return "";
  }

  function lsSplitLines(value) {
    return String(value || "").split(/\r?\n/).map(function (line) {
      return line.trim();
    }).filter(Boolean);
  }

  function lsNormalizeCheckQuestions(value) {
    var items = Array.isArray(value) ? value : (value ? [value] : []);
    return items.map(function (item) {
      if (typeof item === "string") {
        return { question: item, model_answer: "", answer_requirements: [], explanation: "" };
      }
      item = item || {};
      return {
        question: item.question || item.text || "",
        model_answer: item.model_answer || item.answer || "",
        answer_requirements: Array.isArray(item.answer_requirements || item.required_elements)
          ? (item.answer_requirements || item.required_elements)
          : lsSplitLines(item.answer_requirements || item.required_elements || ""),
        explanation: item.explanation || item.rationale || "",
      };
    }).filter(function (item) { return String(item.question || "").trim(); });
  }

  function lsPrimaryCheckQuestion(topic) {
    var questions = lsNormalizeCheckQuestions(topic.check_questions || topic.assessment_prompts || []);
    return questions[0] || { question: "", model_answer: "", answer_requirements: [], explanation: "" };
  }

  function lsCollectCheckQuestions() {
    var question = ((document.getElementById("ls-course-check-question") || {}).value || "").trim();
    if (!question) return [];
    return [{
      question: question,
      model_answer: ((document.getElementById("ls-course-check-model-answer") || {}).value || "").trim(),
      answer_requirements: lsSplitLines((document.getElementById("ls-course-check-requirements") || {}).value),
      explanation: ((document.getElementById("ls-course-check-explanation") || {}).value || "").trim(),
    }];
  }

  function lsCourseDraftHtml(topic) {
    var materialText = lsTopicStudentMaterialSource(topic);
    var isPreview = lsState.courseDraftView !== "edit";
    var previewHidden = isPreview ? "" : " hidden";
    var editHidden = isPreview ? " hidden" : "";
    var spokenText = topic.spoken_script || topic.content || "";
    var cautionsText = lsListText(topic.cautions);
    var checkQuestion = lsPrimaryCheckQuestion(topic);
    var requirementsText = lsListText(checkQuestion.answer_requirements);
    var conceptsText = lsListText(topic.key_concepts);
    var status = lsTopicCoverageStatus(topic);
    return '' +
      '<div class="ls-course-draft" data-topic-id="' + escHtml(topic.id || "") + '">' +
        '<section class="ls-course-draft-section">' +
          '<div class="ls-course-draft-label">トピック</div>' +
          '<div class="ls-course-topic-title">' + escHtml(topic.title || "無題") + '</div>' +
          '<textarea id="ls-course-key-concepts" class="ls-course-small-textarea"' + editHidden + ' placeholder="重要な概念を1行ずつ入力">' + escHtml(conceptsText) + '</textarea>' +
          '<div class="ls-course-field-preview"' + previewHidden + '>' + lsRenderCourseListPreview(conceptsText, topic, "重要な概念はまだ入力されていません。") + '</div>' +
        '</section>' +
        '<section class="ls-course-draft-section">' +
          '<div class="ls-course-draft-label">教材</div>' +
          '<textarea id="ls-course-material-text" class="ls-course-material-textarea"' + editHidden + ' placeholder="例: $w = v_B \\\\cdot v_D$&#10;&#10;![[equation:eq_001]]">' + escHtml(materialText) + '</textarea>' +
          '<div id="ls-course-material-preview" class="ls-course-material-preview"' + previewHidden + '>' + lsRenderCourseMaterialPreview(materialText, topic) + '</div>' +
        '</section>' +
        '<section class="ls-course-draft-section">' +
          '<div class="ls-course-draft-label">本文説明</div>' +
          '<textarea id="ls-course-spoken-script" class="ls-course-script-textarea"' + editHidden + ' placeholder="教員が話せる自然文。音声読み上げ対象です。">' + escHtml(spokenText) + '</textarea>' +
          '<div id="ls-course-spoken-preview" class="ls-course-field-preview"' + previewHidden + '>' + lsRenderCourseMaterialPreview(spokenText, topic) + '</div>' +
        '</section>' +
        '<section class="ls-course-draft-section">' +
          '<div class="ls-course-draft-label">注意点</div>' +
          '<textarea id="ls-course-cautions" class="ls-course-small-textarea"' + editHidden + ' placeholder="誤解しやすい点や適用条件を1行ずつ入力">' + escHtml(cautionsText) + '</textarea>' +
          '<div id="ls-course-cautions-preview" class="ls-course-field-preview"' + previewHidden + '>' + lsRenderCourseListPreview(cautionsText, topic, "注意点はまだ入力されていません。") + '</div>' +
        '</section>' +
        '<section class="ls-course-draft-section">' +
          '<div class="ls-course-draft-label">確認問題</div>' +
          '<textarea id="ls-course-check-question" class="ls-course-small-textarea"' + editHidden + ' placeholder="確認問題">' + escHtml(checkQuestion.question || "") + '</textarea>' +
          '<textarea id="ls-course-check-model-answer" class="ls-course-small-textarea"' + editHidden + ' placeholder="模範解答">' + escHtml(checkQuestion.model_answer || "") + '</textarea>' +
          '<textarea id="ls-course-check-requirements" class="ls-course-small-textarea"' + editHidden + ' placeholder="回答に含めるべき要素を1行ずつ入力">' + escHtml(requirementsText) + '</textarea>' +
          '<textarea id="ls-course-check-explanation" class="ls-course-small-textarea"' + editHidden + ' placeholder="難しい問題の場合、なぜそうなるかの解説">' + escHtml(checkQuestion.explanation || "") + '</textarea>' +
          '<div id="ls-course-check-questions-preview" class="ls-course-field-preview"' + previewHidden + '>' + lsRenderCheckQuestionPreview(checkQuestion, topic) + '</div>' +
        '</section>' +
        '<section class="ls-course-draft-section">' +
          '<div class="ls-course-draft-label">根拠リンク</div>' +
          '<div class="ls-coverage-pill ls-coverage-' + escHtml(status) + '">' + escHtml(status) + '</div>' +
          '<p class="ls-course-muted">' + escHtml(lsTopicCoverageMessage(topic)) + '</p>' +
          '<div class="ls-course-evidence-chips">' + lsCourseEvidenceChipsHtml(topic) + '</div>' +
        '</section>' +
        '<div class="ls-course-draft-toolbar">' +
          '<div class="ls-course-help">数式は $...$ / $$...$$、埋め込みは ![[equation:id]]・![[figure:id]]・![[source:id]]・![[claim:id]]・![[component:id]] で記述できます。</div>' +
          '<div class="ls-course-draft-tabs">' +
            '<button type="button" class="ls-mini-tab' + (isPreview ? " active" : "") + '" data-course-draft-view="preview">プレビュー</button>' +
            '<button type="button" class="ls-mini-tab' + (!isPreview ? " active" : "") + '" data-course-draft-view="edit">編集</button>' +
          '</div>' +
        '</div>' +
      '</div>';
  }

  function lsBindCourseDraftControls(topic) {
    var root = document.querySelector(".ls-course-draft");
    if (!root) return;
    root.querySelectorAll("[data-course-draft-view]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        lsState.courseDraftView = btn.getAttribute("data-course-draft-view") || "preview";
        lsRenderSelectedCourseTopic(topic);
      });
    });
    var materialEl = document.getElementById("ls-course-material-text");
    function updateTopic() {
      topic.key_concepts = lsSplitLines((document.getElementById("ls-course-key-concepts") || {}).value);
      topic.student_material = {
        source_format: "eg-markdown-v1",
        source_text: (materialEl && materialEl.value) || "",
      };
      topic.spoken_script = (document.getElementById("ls-course-spoken-script") || {}).value || "";
      topic.cautions = lsSplitLines((document.getElementById("ls-course-cautions") || {}).value);
      topic.check_questions = lsCollectCheckQuestions();
    }
    root.querySelectorAll("textarea").forEach(function (area) {
      area.addEventListener("input", updateTopic);
    });
    root.querySelectorAll("[data-evidence-ref]").forEach(function (link) {
      link.addEventListener("click", function () {
        lsFocusEvidence(link.getAttribute("data-evidence-ref"));
      });
    });
  }

  function lsNormalizeEvidenceId(id) {
    var value = String(id || "").trim();
    value = value.replace(/^\[\[/, "").replace(/\]\]$/, "");
    value = value.replace(/^\[\[/, "").replace(/\]\]$/, "");
    return value.trim();
  }

  function lsCourseEvidenceKey(kind, id) {
    return kind + ":" + lsNormalizeEvidenceId(id);
  }

  function lsCourseComponentById(componentId) {
    var normalizedId = lsNormalizeEvidenceId(componentId);
    var components = (lsState.courseComponents && lsState.courseComponents.components) || [];
    return components.find(function (component) {
      return lsNormalizeEvidenceId(component.id) === normalizedId ||
        lsNormalizeEvidenceId(component.component_id) === normalizedId;
    }) || null;
  }

  function lsTopicComponentById(topic, componentId) {
    var normalizedId = lsNormalizeEvidenceId(componentId);
    var found = null;
    ((topic && topic.content_blocks) || []).forEach(function (block) {
      if (found || !block || block.type !== "components") return;
      (block.items || []).forEach(function (item) {
        if (found || !item) return;
        if (lsNormalizeEvidenceId(item.component_id) === normalizedId) found = item;
      });
    });
    return found;
  }

  function lsShortSummary(text, limit) {
    text = String(text || "").replace(/\s+/g, " ").trim();
    limit = limit || 180;
    if (!text) return "";
    return text.length > limit ? text.slice(0, limit).trim() + "..." : text;
  }

  function lsTopicEvidenceItems(topic) {
    var items = [];
    (topic.evidence_links || []).forEach(function (link) {
      if (!link) return;
      var kind = link.kind || "source";
      var id = link.target_id || link.id || "";
      items.push({
        key: lsCourseEvidenceKey(kind, id),
        kind: kind,
        id: id,
        title: link.summary || id || kind,
        summary: link.summary || "",
        role: link.support_role || "",
        confidence: link.confidence || "",
      });
    });
    (topic.linked_component_ids || []).forEach(function (id) {
      var blockComponent = lsTopicComponentById(topic, id);
      var component = lsCourseComponentById(id);
      var title = (blockComponent && (blockComponent.label || blockComponent.component_id)) ||
        (component && (component.name || component.label || component.id));
      var summary = (blockComponent && (blockComponent.teaching_takeaway || blockComponent.summary || "")) ||
        (component && (component.summary || component.teacher_notes || ""));
      items.push({
        key: lsCourseEvidenceKey("component", id),
        kind: "component",
        id: lsNormalizeEvidenceId(id),
        title: title || id,
        summary: summary || "このトピックに関連付けられた論理コンポーネントです。要約はまだ生成されていません。",
        role: "support",
        confidence: topic.content_confidence || "",
      });
    });
    lsTopicFormulas(topic).forEach(function (formula) {
      items.push({
        key: lsCourseEvidenceKey("equation", formula.id),
        kind: "equation",
        id: lsNormalizeEvidenceId(formula.id),
        title: formula.label || lsNormalizeEvidenceId(formula.id),
        summary: formula.plain_text || formula.latex || "",
        latex: formula.latex || "",
        role: "equation",
        confidence: topic.content_confidence || "",
      });
    });
    if (topic.source_excerpt) {
      items.push({
        key: lsCourseEvidenceKey("source", topic.linked_chunk_ids && topic.linked_chunk_ids[0] || "excerpt"),
        kind: "source",
        id: topic.linked_chunk_ids && topic.linked_chunk_ids[0] || "excerpt",
        title: "原文抜粋",
        summary: topic.source_excerpt,
        role: "source_span",
        confidence: topic.content_confidence || "",
      });
    }
    var seen = {};
    return items.filter(function (item) {
      if (seen[item.key]) return false;
      seen[item.key] = true;
      return true;
    });
  }

  function lsEvidenceItemByRef(topic, kind, id) {
    var key = lsCourseEvidenceKey(kind, id);
    var normalizedId = lsNormalizeEvidenceId(id);
    return lsTopicEvidenceItems(topic).find(function (item) {
      return item.key === key || (item.kind === kind && lsNormalizeEvidenceId(item.id) === normalizedId);
    }) || null;
  }

  function lsCourseEvidenceChipsHtml(topic) {
    var items = lsTopicEvidenceItems(topic);
    if (!items.length) return '<span class="ls-course-muted">根拠リンク候補はまだありません。</span>';
    return items.map(function (item) {
      return '<button type="button" class="ls-evidence-chip" data-evidence-ref="' + escHtml(item.key) + '">' +
        escHtml(item.kind) + ': ' + escHtml(item.title) +
      '</button>';
    }).join("");
  }

  function lsCourseEvidenceHtml(topic) {
    var items = lsTopicEvidenceItems(topic);
    if (!items.length) {
      return '<div class="ls-course-evidence-empty">このトピックに対応する根拠リンク候補はまだありません。</div>';
    }
    return '<div class="ls-course-evidence-list">' + items.map(function (item) {
      return '<article class="ls-course-evidence-card" data-evidence-key="' + escHtml(item.key) + '">' +
        '<div class="ls-course-evidence-head">' +
          '<span class="ls-evidence-kind">' + escHtml(item.kind) + '</span>' +
          '<strong>' + escHtml(item.title) + '</strong>' +
        '</div>' +
        (item.kind === "equation" && item.latex
          ? '<div class="ls-course-evidence-formula">' + lsRenderKatex(item.latex, true) + '</div>'
          : (item.summary ? '<div class="ls-course-evidence-summary">' + lsRenderTextWithFormulas(item.summary, lsTopicFormulas(topic)) + '</div>' : '')) +
        '<div class="ls-course-evidence-meta">' +
          (item.role ? '<span>' + escHtml(item.role) + '</span>' : '') +
          (item.confidence ? '<span>' + escHtml(item.confidence) + '</span>' : '') +
        '</div>' +
      '</article>';
    }).join("") + '</div>';
  }

  function lsFocusEvidence(key) {
    var preview = document.getElementById("ls-display-preview");
    if (!preview || !key) return;
    preview.querySelectorAll(".ls-course-evidence-card").forEach(function (card) {
      card.classList.toggle("active", card.getAttribute("data-evidence-key") === key);
    });
    var target = preview.querySelector('[data-evidence-key="' + CSS.escape(key) + '"]');
    if (target) target.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function lsRenderCourseListPreview(text, topic, emptyMessage) {
    var items = lsSplitLines(text);
    if (!items.length) return '<div class="ls-course-muted">' + escHtml(emptyMessage || "未入力です。") + '</div>';
    return '<ul class="ls-course-preview-list">' + items.map(function (item) {
      return '<li>' + lsRenderCourseMaterialPreview(item, topic) + '</li>';
    }).join("") + '</ul>';
  }

  function lsRenderCheckQuestionPreview(item, topic) {
    item = item || {};
    if (!item.question) return '<div class="ls-course-muted">確認問題はまだ入力されていません。</div>';
    var html = '<div class="ls-course-check-preview">';
    html += '<div><strong>問題</strong><br>' + lsRenderCourseMaterialPreview(item.question, topic) + '</div>';
    if (item.model_answer) {
      html += '<div><strong>模範解答</strong><br>' + lsRenderCourseMaterialPreview(item.model_answer, topic) + '</div>';
    }
    if (item.answer_requirements && item.answer_requirements.length) {
      html += '<div><strong>回答に必要な要素</strong>' +
        '<ul class="ls-course-preview-list">' + item.answer_requirements.map(function (req) {
          return '<li>' + escHtml(req) + '</li>';
        }).join("") + '</ul></div>';
    }
    if (item.explanation) {
      html += '<div><strong>解説</strong><br>' + lsRenderCourseMaterialPreview(item.explanation, topic) + '</div>';
    }
    html += '</div>';
    return html;
  }

  function lsRenderCourseMaterialPreview(text, topic) {
    var formulas = lsTopicFormulas(topic);
    var formulaById = {};
    formulas.forEach(function (formula, idx) {
      var normalizedId = lsNormalizeEvidenceId(formula.id || ("FORMULA_" + idx));
      formulaById[String(formula.id)] = formula;
      formulaById[normalizedId] = formula;
      formulaById["[[" + normalizedId + "]]"] = formula;
      formulaById["FORMULA_" + idx] = formula;
      formulaById["[[" + "FORMULA_" + idx + "]]"] = formula;
    });
    var embedBlocks = [];
    var mathBlocks = [];
    function preserveMath(expr, display) {
      var idx = mathBlocks.length;
      mathBlocks.push({ expr: expr, display: display });
      return "@@EG_COURSE_MATH_" + idx + "@@";
    }
    function preserveEmbed(kind, id, inline) {
      var idx = embedBlocks.length;
      embedBlocks.push({ kind: kind, id: id, inline: inline });
      return "@@EG_COURSE_EMBED_" + idx + "@@";
    }
    var preserved = lsNormalizePreviewLineBreaks(text || "");
    preserved = preserved.replace(/!\[\[equation:\s*\[\[([^\]]+)\]\]\s*\]\]/g, "![[equation:$1]]");
    preserved = preserved.replace(/\[\[equation:\s*\[\[([^\]]+)\]\]\s*\]\]/g, "[[equation:$1]]");
    preserved = preserved.replace(/!\[\[([a-z_]+):([^\]]+)\]\]/g, function (_m, kind, id) {
      return preserveEmbed(kind, id, false);
    });
    preserved = preserved.replace(/\[\[([a-z_]+):([^\]]+)\]\]/g, function (_m, kind, id) {
      return preserveEmbed(kind, id, true);
    });
    preserved = preserved.replace(/\[\[([^\[\]:]+)\]\]/g, function (m, id) {
      var formula = formulaById[m] || formulaById[id] || formulaById[lsNormalizeEvidenceId(id)];
      if (!formula) return m;
      return preserveMath(formula.latex || formula.summary || id, true);
    });
    preserved = preserved.replace(/\\\[([\s\S]+?)\\\]/g, function (_m, expr) {
      return preserveMath(expr, true);
    });
    preserved = preserved.replace(/\$\$([\s\S]+?)\$\$/g, function (_m, expr) {
      return preserveMath(expr, true);
    });
    preserved = preserved.replace(/\\\(([\s\S]+?)\\\)/g, function (_m, expr) {
      return preserveMath(expr, false);
    });
    preserved = preserved.replace(/\$([^\$\n]+?)\$/g, function (_m, expr) {
      return preserveMath(expr, false);
    });
    var html = escHtml(preserved);
    html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.split("\n\n").map(function (p) {
      return "<p>" + p.replace(/\n/g, "<br>") + "</p>";
    }).join("");
    html = html.replace(/@@EG_COURSE_MATH_(\d+)@@/g, function (_m, idx) {
      var block = mathBlocks[parseInt(idx, 10)];
      return block ? lsRenderKatex(block.expr, block.display) : "";
    });
    html = html.replace(/@@EG_COURSE_EMBED_(\d+)@@/g, function (_m, idx) {
      var embed = embedBlocks[parseInt(idx, 10)];
      if (!embed) return "";
      var embedId = lsNormalizeEvidenceId(embed.id);
      var key = lsCourseEvidenceKey(embed.kind, embedId);
      var evidenceItem = lsEvidenceItemByRef(topic, embed.kind, embedId);
      if (embed.kind === "equation") {
        var formula = formulaById[String(embed.id)] || formulaById[embedId] || (evidenceItem && evidenceItem.latex ? evidenceItem : null);
        if (formula && (formula.latex || formula.summary)) {
          var latex = formula.latex || formula.summary || "";
          return '<button type="button" class="ls-material-embed ls-material-formula-only" data-evidence-ref="' + escHtml(key) + '">' +
            lsRenderKatex(latex, true) +
          '</button>';
        }
        return '<button type="button" class="ls-material-embed ls-material-missing" data-evidence-ref="' + escHtml(key) + '">' +
          '<span class="ls-material-embed-kind">未解決の数式</span>' +
          '<strong>' + escHtml(embedId || "数式") + '</strong>' +
          '<span class="ls-material-embed-summary">この数式IDに対応するLaTeXを取得できませんでした。</span>' +
        '</button>';
      }
      if (embed.kind === "source" && (embedId === "summary" || embedId === "topic_summary")) {
        return '<button type="button" class="ls-material-embed ls-material-evidence-card ls-material-source" data-evidence-ref="' + escHtml(key) + '">' +
          '<span class="ls-material-embed-kind">概要</span>' +
          '<strong>トピック概要</strong>' +
          '<span class="ls-material-embed-summary">' + escHtml(lsShortSummary(topic.summary || "", 260) || "このトピックの概要はまだ生成されていません。") + '</span>' +
        '</button>';
      }
      if (embed.kind === "source" && topic.source_excerpt) {
        return '<button type="button" class="ls-material-embed ls-material-evidence-card ls-material-source" data-evidence-ref="' + escHtml(key) + '">' +
          '<span class="ls-material-embed-kind">原文抜粋</span>' +
          '<strong>参照抜粋</strong>' +
          '<span class="ls-material-embed-summary">' + escHtml(lsShortSummary(topic.source_excerpt, 260)) + '</span>' +
        '</button>';
      }
      if (evidenceItem) {
        return '<button type="button" class="ls-material-embed ls-material-evidence-card" data-evidence-ref="' + escHtml(key) + '">' +
          '<span class="ls-material-embed-kind">' + escHtml(evidenceItem.kind) + '</span>' +
          '<strong>' + escHtml(evidenceItem.title || evidenceItem.id) + '</strong>' +
          '<span class="ls-material-embed-summary">' + escHtml(lsShortSummary(evidenceItem.summary, 260) || "この教材要素に紐づく根拠リンクです。右ペインで詳細を確認できます。") + '</span>' +
          '<span class="ls-material-embed-meta">' + escHtml([evidenceItem.role, evidenceItem.confidence].filter(Boolean).join(" / ")) + '</span>' +
        '</button>';
      }
      return '<button type="button" class="ls-material-embed ls-material-evidence-card ls-material-missing" data-evidence-ref="' + escHtml(key) + '">' +
        '<span class="ls-material-embed-kind">未解決</span>' +
        '<strong>' + escHtml(embed.kind + ":" + embed.id) + '</strong>' +
        '<span class="ls-material-embed-summary">このIDに対応する根拠サマリを取得できませんでした。根拠リンクの対応付けを確認してください。</span>' +
      '</button>';
    });
    return html || '<div class="ls-course-muted">教材プレビューは空です。</div>';
  }

  function lsRenderWorkspace() {
    var chunk = lsGetSelectedChunk();
    var metaEl = document.getElementById("ls-chunk-meta");
    lsUpdateWorkTabActive();
    var currentView = lsState.view || "edit";
    if (lsState.selectedScope && lsState.selectedScope.type === "course_topic" && !lsIsTheoryGraphView(currentView)) {
      var selectedTopic = lsGetSelectedCourseTopic();
      if (selectedTopic) {
        lsRenderSelectedCourseTopic(selectedTopic);
        lsUpdateAssistantContext();
        return;
      }
    }
    if (lsState.selectedScope && lsState.selectedScope.type === "component" && currentView === "theory") {
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
      if (!lsScopeHasDocumentContext(lsState.selectedScope)) {
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
    var formulasEl = document.getElementById("ls-formulas");
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
      if (formulasEl) formulasEl.hidden = true;
      document.getElementById("ls-display-preview").hidden = true;
      lsRenderTheoryPanel(chunk);
    } else if (isClaims) {
      document.getElementById("ls-display-tabs").hidden = true;
      spokenEl.hidden = true;
      displayEl.hidden = true;
      if (formulasEl) formulasEl.hidden = true;
      document.getElementById("ls-display-preview").hidden = true;
      lsRenderClaimsPanel(chunk);
    } else if (isGraph) {
      document.getElementById("ls-display-tabs").hidden = true;
      spokenEl.hidden = true;
      displayEl.hidden = true;
      if (formulasEl) formulasEl.hidden = true;
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
      if (formulasEl) formulasEl.hidden = true;
      spokenEl.hidden = false;
      spokenEl.disabled = lsState.syncSpoken;
    } else {
      document.getElementById("ls-right-title").textContent = lsState.displayView === "formulas" ? "数式一覧" : "表示テキスト";
      document.getElementById("ls-display-tabs").hidden = false;
      spokenEl.hidden = true;
      displayEl.hidden = lsState.displayView !== "script";
      document.getElementById("ls-display-preview").hidden = lsState.displayView !== "preview";
      if (formulasEl) {
        formulasEl.hidden = lsState.displayView !== "formulas";
        if (lsState.displayView === "formulas") lsRenderFormulas(chunk.formulas || []);
      }
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
      if (formulasEl) formulasEl.hidden = true;
    }
    if (isStructure) lsRenderStructure(chunk);
    lsApplyRightPaneVisibility();
    lsUpdateAssistantContext();
  }

  function lsUpdateAssistantContext() {
    var label = document.getElementById("ls-assistant-label");
    var promptEl = document.getElementById("ls-rewrite-prompt");
    var btn = document.getElementById("ls-rewrite-btn");
    if (!label || !promptEl || !btn) return;
    if (lsState.selectedScope && lsState.selectedScope.type === "course_topic") {
      label.textContent = "授業用ドラフトへの提案:";
      promptEl.placeholder = "例: 根拠リンクを踏まえて教材欄に数式を追加して";
      promptEl.disabled = false;
      btn.textContent = "AIで提案";
      btn.disabled = false;
      lsUpdateAssistantOpenButton();
      return;
    }
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
    btn.disabled = !lsState.courseId || !lsState.selectedChunkId;
    promptEl.disabled = btn.disabled;
    lsUpdateAssistantOpenButton();
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

    var view = lsGraphForCurrentLayer(graph);
    var html =
      lsGraphNarrativeSummaryHtml(graph) +
      '<div class="ls-component-graph-shell">' +
        '<div class="ls-component-graph-main">' +
          lsGraphLayerToolbarHtml(nodes) +
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
          lsGraphEmptyDetailHtml(view.nodes.length, view.edges.length) +
        '</aside>' +
      '</div>' +
      lsGraphValidationHtml(validations);
    container.innerHTML = html;

    lsBindGraphLayerToolbar(documentId);

    if (!window.vis || !window.vis.Network) {
      lsRenderGraphFallback(container, view);
      return;
    }

    lsInitComponentGraphNetwork(view);
  }

  // Issue #360: NarrativeAnnotator の graph_summary をグラフ上部に表示する。
  // 注釈は LLM 提案 (provisional) なのでラベルで明示する。
  function lsGraphNarrativeSummaryHtml(graph) {
    var narrative = (graph && graph.narrative) || {};
    var summary = String(narrative.graph_summary || "").trim();
    if (!summary) return "";
    return '<div class="ls-graph-narrative-summary">' +
      '<div class="ls-graph-narrative-title">この論文のグラフの読み方' +
      '<span class="ls-graph-narrative-badge">AI提案</span></div>' +
      '<p>' + escHtml(summary) + '</p>' +
    '</div>';
  }

  // Issue #306: グラフは main（上位理論構成）と equation_detail（式単位）の
  // 2層を持つ。デフォルトは main を優先表示し、トグルで詳細層を展開できる。
  function lsGraphLayerOptions(nodes) {
    var counts = { main: 0, equation_detail: 0, debug: 0, other: 0 };
    (nodes || []).forEach(function (node) {
      var layer = String((node && node.graph_layer) || "main").toLowerCase();
      if (counts[layer] === undefined) counts.other += 1;
      else counts[layer] += 1;
    });
    // Only a layered graph (with equation_detail / debug nodes) needs a toggle.
    if (!counts.equation_detail && !counts.debug && !counts.other) return [];
    var options = [{ value: "main", label: "主グラフ", count: counts.main }];
    if (counts.equation_detail) {
      options.push({ value: "equation_detail", label: "式の詳細", count: counts.equation_detail });
    }
    options.push({
      value: "all",
      label: "すべて（デバッグ）",
      count: counts.main + counts.equation_detail + counts.debug + counts.other,
    });
    return options;
  }

  function lsGraphLayerToolbarHtml(nodes) {
    var options = lsGraphLayerOptions(nodes);
    if (options.length <= 1) return "";
    var current = lsState.graphLayerFilter || "main";
    var buttons = options.map(function (opt) {
      var active = opt.value === current ? " is-active" : "";
      return '<button type="button" class="ls-graph-layer-btn' + active + '" data-graph-layer="' +
        escHtml(opt.value) + '">' + escHtml(opt.label) +
        ' <span class="ls-graph-layer-count">' + escHtml(String(opt.count)) + '</span></button>';
    }).join("");
    return '<div class="ls-graph-layer-toolbar" role="group" aria-label="グラフ層の切り替え">' +
      buttons + '</div>';
  }

  function lsBindGraphLayerToolbar(documentId) {
    var buttons = document.querySelectorAll(".ls-graph-layer-btn");
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.addEventListener("click", function () {
        var layer = btn.getAttribute("data-graph-layer") || "main";
        if (layer === lsState.graphLayerFilter) return;
        lsState.graphLayerFilter = layer;
        lsRenderGraphPanel(documentId);
      });
    });
  }

  // Return a graph filtered to the currently selected layer. Edges are kept
  // only when both endpoints remain visible.
  function lsGraphForCurrentLayer(graph) {
    var filter = lsState.graphLayerFilter || "main";
    var nodes = graph.nodes || [];
    var edges = graph.edges || [];
    if (filter === "all") return graph;
    var visible = nodes.filter(function (node) {
      var layer = String((node && node.graph_layer) || "main").toLowerCase();
      if (filter === "main") return layer === "main";
      // equation_detail view shows the detailed trace plus its debug steps.
      return layer === "equation_detail" || layer === "debug";
    });
    // Never show an empty canvas: fall back to all nodes if the filter is empty.
    if (!visible.length) visible = nodes;
    var visibleIds = {};
    visible.forEach(function (node) { visibleIds[lsGraphNodeId(node)] = true; });
    var visibleEdges = edges.filter(function (edge) {
      var source = edge.source_component_id || edge.source || edge.from;
      var target = edge.target_component_id || edge.target || edge.to;
      return visibleIds[source] && visibleIds[target];
    });
    return Object.assign({}, graph, { nodes: visible, edges: visibleEdges });
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
      var backing = String((node && node.source_backing_status) || "").toLowerCase();
      var visNode = {
        id: id,
        label: lsGraphNodeDisplayLabel(node, id),
        x: pos.x,
        y: pos.y,
        group: group,
        title: lsGraphNodeTooltip(node),
      };
      if (lsGraphNodeDashed(node, group) || backing === "review_required") {
        visNode.shapeProperties = { borderDashes: [6, 5] };
      }
      if (backing === "partially_source_backed") {
        visNode.borderWidth = 1;
      }
      if (lsGraphNodeFaded(node, backing)) {
        visNode.opacity = 0.55;
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
    var nodeById = {};
    (graph.nodes || []).forEach(function (n) {
      var id = lsGraphNodeId(n);
      if (id) nodeById[id] = n;
    });
    var connected = (graph.edges || []).filter(function (edge) {
      var source = edge.source_component_id || edge.source || edge.from;
      var target = edge.target_component_id || edge.target || edge.to;
      return source === nodeId || target === nodeId;
    });
    var backing = String(node.source_backing_status || "").toLowerCase();

    // Header: badge + title
    var html =
      '<div class="ls-graph-detail-badge ' + escHtml(lsGraphNodeGroup(node)) + '">' +
      escHtml(node.component_type_text || node.component_type || node.typeName || "component") + '</div>' +
      '<h4>' + escHtml((lsGraphNodeDisplayLabel(node, nodeId) || nodeId || "無題").replace(/\n/g, " — ")) + '</h4>';

    // 1. このノードの意味
    if (node.description) {
      html += '<div class="ls-graph-detail-section"><b>このノードの意味</b><p>' + escHtml(node.description) + '</p></div>';
    }

    // 1a. 論文の議論での役割（NarrativeAnnotator, issue #360。LLM 提案）
    var graphNarrative = (graph && graph.narrative) || {};
    var nodeNarrative = (graphNarrative.node_narratives || {})[nodeId];
    if (nodeNarrative && String(nodeNarrative.narrative_role || "").trim()) {
      html += '<div class="ls-graph-detail-section"><b>議論での役割 <span class="ls-graph-narrative-badge">AI提案</span></b>' +
        '<p>' + escHtml(nodeNarrative.narrative_role) + '</p></div>';
    }

    // 1b. 抽出メモ（review_reason — step.reason からの抽出/検証メモ）
    var reviewReason = String(node.review_reason || "").trim();
    if (reviewReason) {
      html += '<div class="ls-graph-detail-section"><b>抽出メモ</b><p class="ls-graph-detail-memo">' + escHtml(reviewReason) + '</p></div>';
    }

    // 2. 入力
    var inputParts = "";
    if ((node.input_equation_ids || []).length) {
      inputParts += '<div class="ls-graph-detail-link"><span>式</span> ' + escHtml((node.input_equation_ids).join(", ")) + '</div>';
    }
    if ((node.input_claim_ids || []).length) {
      inputParts += '<div class="ls-graph-detail-link"><span>claim</span> ' + escHtml((node.input_claim_ids).join(", ")) + '</div>';
    }
    if ((node.required_claim_ids || []).length) {
      inputParts += '<div class="ls-graph-detail-link"><span>前提claim</span> ' + escHtml((node.required_claim_ids).join(", ")) + '</div>';
    }
    if (inputParts) {
      html += '<div class="ls-graph-detail-section"><b>入力</b>' + inputParts + '</div>';
    }

    // 3. 出力
    var outputParts = "";
    if ((node.output_equation_ids || []).length) {
      outputParts += '<div class="ls-graph-detail-link"><span>式</span> ' + escHtml((node.output_equation_ids).join(", ")) + '</div>';
    }
    if ((node.output_claim_ids || []).length) {
      outputParts += '<div class="ls-graph-detail-link"><span>claim</span> ' + escHtml((node.output_claim_ids).join(", ")) + '</div>';
    }
    if (outputParts) {
      html += '<div class="ls-graph-detail-section"><b>出力</b>' + outputParts + '</div>';
    }

    // 4. 接続（接続先ノードのラベルと接続理由を表示）
    html += '<div class="ls-graph-detail-section"><b>接続</b>';
    if (!connected.length) {
      html += '<p class="ls-theory-muted">接続エッジはありません。</p>';
    } else {
      html += '<ul class="ls-graph-detail-edges">';
      connected.forEach(function (edge) {
        var source = edge.source_component_id || edge.source || edge.from;
        var target = edge.target_component_id || edge.target || edge.to;
        var otherId = source === nodeId ? target : source;
        var otherNode = nodeById[otherId];
        var otherLabel = otherNode ? ((lsGraphNodeDisplayLabel(otherNode, otherId) || otherId).replace(/\n/g, " ")) : otherId;
        var arrow = source === nodeId ? "→" : "←";
        var relation = edge.relation || edge.edge_type || edge.type || "RELATED_TO";
        var edgeLabel = lsGraphEdgeLabel(relation);
        html += '<li><span>' + escHtml(edgeLabel) + '</span>' + escHtml(arrow + " " + otherLabel);
        var evidence = edge.evidence || {};
        var reason = String(evidence.reason || "").trim();
        if (reason) {
          html += '<div class="ls-graph-detail-edge-reason">' + escHtml(reason) + '</div>';
        }
        // 遷移の説明（NarrativeAnnotator, issue #360。LLM 提案）
        var edgeNarrative = (graphNarrative.edge_narratives || {})[edge.edge_id];
        var transition = edgeNarrative ? String(edgeNarrative.transition_text || "").trim() : "";
        if (transition) {
          html += '<div class="ls-graph-detail-edge-narrative">' + escHtml(transition) + ' <span class="ls-graph-narrative-badge">AI提案</span></div>';
        }
        html += '</li>';
      });
      html += '</ul>';
    }
    html += '</div>';

    // 5. 根拠
    var links = lsGraphSourceLinksHtml(node);
    if (links) {
      html += '<div class="ls-graph-detail-section"><b>根拠</b>' + links + '</div>';
    }

    // 6. 要確認事項
    var hasReview = backing || (node.review_reasons || []).length;
    if (hasReview) {
      html += '<div class="ls-graph-detail-section"><b>要確認事項</b>';
      if (backing) {
        html += '<p class="ls-graph-backing ls-graph-backing-' + escHtml(backing) + '">' +
          escHtml(lsGraphSourceBackingLabel(backing)) + '</p>';
      }
      if ((node.review_reasons || []).length) {
        html += '<ul class="ls-graph-detail-reasons">';
        node.review_reasons.forEach(function (reason) {
          html += '<li>' + escHtml(lsGraphReviewReasonLabel(reason)) + '</li>';
        });
        html += '</ul>';
      }
      html += '</div>';
    }

    // 7. システム情報（折りたたみ）
    html += '<div class="ls-graph-detail-section ls-graph-detail-system">' +
      '<details><summary>システム情報</summary>' +
      '<div class="ls-graph-detail-link"><b>ID</b> <code>' + escHtml(nodeId || "") + '</code></div>' +
      '<div class="ls-graph-detail-link"><b>レイヤー</b> ' + escHtml(lsGraphLayerLabel(node.graph_layer)) + '</div>' +
      '<div class="ls-graph-detail-link"><b>ステータス</b> ' + escHtml(node.review_status || node.origin || "paper") + '</div>';
    var linkage = lsGraphLayerLinkageHtml(node);
    if (linkage) html += linkage;
    html += '</details></div>';

    detail.innerHTML = html;
  }

  function lsRenderGraphFallback(container, graph) {
    var nodes = graph.nodes || [];
    var edges = graph.edges || [];
    var nodeById = {};
    nodes.forEach(function (node) { nodeById[lsGraphNodeId(node)] = node; });
    var html = '<div class="ls-empty-state">ネットワーク描画ライブラリを読み込めませんでした。テキスト表示に切り替えます。</div><div class="ls-graph-flow">';
    nodes.forEach(function (node) {
      html += '<div class="ls-graph-node">' + escHtml(lsGraphMainStageLabel(node) || node.label || lsGraphNodeId(node)) + '<div class="ls-theory-muted">' + escHtml(node.origin || "paper") + ' / ' + escHtml(node.component_type || "") + '</div></div>';
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

  function lsGraphSourceLinksHtml(node) {
    var rows = [
      ["equation", node.linked_equation_ids],
      ["derivation", node.linked_derivation_ids],
      ["claim", node.linked_claim_ids],
      ["evidence", node.linked_evidence_ids],
    ];
    var html = "";
    rows.forEach(function (row) {
      var ids = row[1] || [];
      if (!ids.length) return;
      html += '<div class="ls-graph-detail-link"><span>' + escHtml(row[0]) + '</span> ' +
        escHtml(ids.join(", ")) + '</div>';
    });
    return html;
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
    var layer = String((node && node.graph_layer) || "").toLowerCase();
    var maturity = String((node && node.maturity_source) || "").toLowerCase();
    return layer === "debug" || maturity === "deterministic_fallback" ||
      /uniform-coordinate|単一粒子/.test(label) || (group === "method" && /unpredictability/.test(label));
  }

  function lsGraphNodeFaded(node, backing) {
    var layer = String((node && node.graph_layer) || "").toLowerCase();
    var maturity = String((node && node.maturity_source) || "").toLowerCase();
    return backing === "inferred" || layer === "debug" || maturity === "deterministic_fallback";
  }

  function lsGraphNodeIsFallback(node) {
    var maturity = String((node && node.maturity_source) || "").toLowerCase();
    var backing = String((node && node.source_backing_status) || "").toLowerCase();
    return maturity === "deterministic_fallback" || backing === "inferred";
  }

  // Issue #337: Japanese operation verb mapping for graph node display.
  var LS_OPERATION_VERB_JA = {
    "Apply definition": "定義を適用",
    "Apply criterion": "基準を適用",
    "Apply constraint": "制約を適用",
    "Apply equation": "式を適用",
    "Apply measurement": "測定を適用",
    "Apply independence": "独立性を適用",
    "Infer conclusion": "結論を導出",
    "Infer intermediate claim": "中間主張を導出",
    "Derive result": "結果を導出",
    "Derive consistency relation": "整合関係を導出",
    "Compare": "比較・検証",
    "Flag limitation": "制約を確認",
    "Eliminate parameter": "パラメータを消去",
    "Solve linear system": "連立方程式を求解",
    "State assumption": "仮定を提示",
    "Branch on condition": "条件分岐",
    "Introduce observable": "観測量を導入",
    "Define": "定義",
    "Construct": "構成",
    "Linearize": "線形化",
    "Normalize": "正規化",
    "Approximate": "近似",
    "Substitute": "代入",
    "Eliminate": "消去",
    "Derive": "導出",
    "Constrain": "制約",
    "Diagnose": "診断",
    "Solve": "求解",
    "Infer": "推論",
    "Apply": "適用",
    "Flag": "確認",
    "State": "提示",
    "Introduce": "導入",
  };

  // Issue #337: Japanese stage labels for main graph nodes.
  var LS_STAGE_LABELS_JA = {
    "Theory basis": "理論の前提",
    "Observation model": "観測モデル",
    "Observable construction": "観測量の構成",
    "Equation system": "方程式系",
    "Elimination": "消去",
    "Consistency relation": "整合条件",
    "Diagnostic / application": "診断・応用",
  };

  function lsGraphOperationLabelJa(verb) {
    var label = String(verb || "").trim();
    if (LS_OPERATION_VERB_JA[label]) return LS_OPERATION_VERB_JA[label];
    if (LS_STAGE_LABELS_JA[label]) return LS_STAGE_LABELS_JA[label];
    var parts = label.split(" ");
    if (parts.length >= 2 && LS_OPERATION_VERB_JA[parts[0]]) {
      return LS_OPERATION_VERB_JA[parts[0]] + ": " + parts.slice(1).join(" ");
    }
    return label;
  }

  // Prefix a warning icon for fallback / inferred nodes so they are not mistaken
  // for confirmed, source-backed theory operations (issue #302).
  // Issue #337: prefer visual_label (short), translate to Japanese.
  function lsGraphNodeDisplayLabel(node, id) {
    var visualLabel = String((node && node.visual_label) || "").trim();
    var raw;
    if (visualLabel) {
      raw = lsGraphOperationLabelJa(visualLabel);
    } else {
      raw = lsGraphFullDisplayLabel(node) || id;
    }
    var label = lsWrapGraphLabel(raw);
    return lsGraphNodeIsFallback(node) ? "⚠ " + label : label;
  }

  function lsGraphLayerLabel(layer) {
    var labels = {
      main: "主グラフ（上位理論構成）",
      equation_detail: "式の詳細",
      debug: "要確認 / 推論層",
    };
    return labels[String(layer || "main").toLowerCase()] || layer || "主グラフ";
  }

  function lsGraphLayerLinkageHtml(node) {
    var html = "";
    if (node.representative_component_id) {
      html += '<div class="ls-graph-detail-link"><span>代表Component</span> ' +
        escHtml(node.representative_component_id) + '</div>';
    }
    var linkedComponents = node.linked_component_ids || [];
    if (linkedComponents.length) {
      html += '<div class="ls-graph-detail-link"><span>関連Component</span> ' +
        escHtml(String(linkedComponents.length)) + ' 件</div>';
    }
    var members = node.member_component_ids || [];
    if (members.length) {
      html += '<div class="ls-graph-detail-link"><span>式ステップ</span> ' +
        escHtml(String(members.length)) + ' 件を集約</div>';
    }
    if (node.parent_component_id) {
      html += '<div class="ls-graph-detail-link"><span>所属</span> ' +
        escHtml(node.parent_component_id) + '</div>';
    }
    return html;
  }

  function lsGraphSourceBackingLabel(status) {
    var labels = {
      source_backed: "出典あり",
      partially_source_backed: "部分的に出典あり",
      inferred: "推論",
      review_required: "要確認",
    };
    return labels[String(status || "").toLowerCase()] || status || "";
  }

  function lsGraphReviewReasonLabel(reason) {
    var labels = {
      missing_atomic_claim: "atomicなclaim未紐付け",
      missing_evidence_link: "evidence未紐付け",
      missing_equation_link: "equation未紐付け",
      missing_derivation_link: "derivation未紐付け",
      equation_needs_math_review: "数式の確認が必要",
      edge_not_source_backed: "edgeに出典がない",
      fallback_or_inferred_node: "fallback / 推論ノード",
      source_span_missing: "原文spanがない",
    };
    return labels[String(reason || "")] || reason || "";
  }

  // Issue #319: main-layer TheoryOperationNode labels must be short stage names
  // only. Backend re-normalizes stored graphs, but guard client-side too so a
  // stale graph never shows "Theory basis: Eq. (2.7) ..." as a visual label.
  var LS_MAIN_STAGE_LABELS = [
    "Theory basis",
    "Observation model",
    "Observable construction",
    "Equation system",
    "Elimination",
    "Consistency relation",
    "Diagnostic / application",
  ];

  function lsGraphMainStageLabel(node) {
    var label = String((node && (node.label || node.name)) || "");
    var layer = String((node && node.graph_layer) || "main").toLowerCase();
    var ctype = String((node && (node.component_type || node.type)) || "");
    if (layer !== "main" || ctype !== "TheoryOperationNode") return label;
    // "<Stage>: <long text>" -> "<Stage>".
    var colon = label.indexOf(":");
    if (colon >= 0) {
      var head = label.slice(0, colon).trim();
      for (var i = 0; i < LS_MAIN_STAGE_LABELS.length; i++) {
        if (head.toLowerCase() === LS_MAIN_STAGE_LABELS[i].toLowerCase()) return LS_MAIN_STAGE_LABELS[i];
      }
    }
    for (var j = 0; j < LS_MAIN_STAGE_LABELS.length; j++) {
      if (label.toLowerCase() === LS_MAIN_STAGE_LABELS[j].toLowerCase()) return LS_MAIN_STAGE_LABELS[j];
    }
    return label;
  }

  function lsGraphFullDisplayLabel(node) {
    if (!node) return "";
    var explicit = String(node.display_label || "").trim();
    if (explicit) return explicit;
    var stage = lsGraphMainStageLabel(node);
    var object = String(node.theory_object || "").trim();
    if (!object) return stage || node.label || node.name || "";
    if (!stage || object.toLowerCase() === String(stage).toLowerCase()) return object;
    return stage + ": " + object;
  }

  function lsGraphNodeTooltip(node) {
    var parts = [];
    var type = node.component_type_text || node.component_type || node.review_status || "";
    if (type) parts.push(type);
    var backing = lsGraphSourceBackingLabel(node.source_backing_status);
    if (backing) parts.push(backing);
    var reasons = (node.review_reasons || []).map(lsGraphReviewReasonLabel);
    if (reasons.length) parts.push(reasons.join(" / "));
    return parts.join("\n");
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
    if (relation === "DIAGNOSES" || relation === "COMPARES") return 6;
    if (relation === "DERIVES" || relation === "ELIMINATES_BIAS" || relation === "ELIMINATES" || relation === "SOLVES") return 5;
    if (relation === "FEEDS_EQUATION_SYSTEM" || relation === "LINEARIZES" || relation === "DEFINES" || relation === "CONSTRUCTS" || relation === "NORMALIZES") return 4;
    if (relation === "REQUIRES") return 5;
    if (relation === "PRODUCES_FOR" || relation === "ENABLES" || relation === "SUPPORTS" || relation === "SUBSTITUTES" || relation === "APPROXIMATES") return 4;
    if (relation === "UNCERTAIN_DUE_TO") return 3;
    if (relation === "QUALIFIES" || relation === "CHECKED_BY" || relation === "CONSTRAINS") return 2;
    if (relation === "TRANSFORMS" || relation === "FEEDS" || relation === "REQUIRES_REVIEW") return 1;
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
      FEEDS_EQUATION_SYSTEM: "式系へ",
      LINEARIZES: "線形化",
      ELIMINATES_BIAS: "バイアス消去",
      DERIVES: "導出",
      CONSTRAINS: "制約",
      DIAGNOSES: "診断",
      REQUIRES_REVIEW: "要確認",
      CONSTRUCTS: "構成",
      NORMALIZES: "正規化",
      SOLVES: "求解",
      SUBSTITUTES: "代入",
      ELIMINATES: "消去",
      APPROXIMATES: "近似",
      TRANSFORMS: "変換",
      COMPARES: "比較",
      FEEDS: "入力",
    };
    return labels[key] || key;
  }

  function lsGraphEdgeDashed(edge) {
    var status = String((edge && edge.support_status) || "").toLowerCase();
    var relation = String((edge && (edge.relation || edge.edge_type || edge.type)) || "").toLowerCase();
    var review = String((edge && edge.review_status) || "").toLowerCase();
    var backing = String((edge && edge.source_backing_status) || "").toLowerCase();
    if (review === "review_required") return true;
    if (backing === "review_required" || backing === "inferred") return true;
    return /llm|related|qualifies|conflicts|inhibits|uncertain/.test(status + " " + relation);
  }

  function lsGraphEdgeColor(edge) {
    var relation = String((edge && (edge.relation || edge.edge_type || edge.type)) || "").toUpperCase();
    if (relation === "DIAGNOSES" || relation === "COMPARES") return "#7c3aed";
    if (relation === "DERIVES" || relation === "ELIMINATES_BIAS" || relation === "ELIMINATES" || relation === "SOLVES") return "#2563eb";
    if (relation === "FEEDS_EQUATION_SYSTEM" || relation === "LINEARIZES" || relation === "CONSTRUCTS" || relation === "NORMALIZES" || relation === "SUBSTITUTES" || relation === "APPROXIMATES") return "#0891b2";
    if (relation === "UNCERTAIN_DUE_TO") return "#f97316";
    if (relation === "CONFLICTS_WITH" || relation === "INHIBITS") return "#ef4444";
    if (relation === "CHECKED_BY" || relation === "QUALIFIES" || relation === "CONSTRAINS") return "#84cc16";
    if (relation === "DEFINES") return "#22c55e";
    if (relation === "RELATED_TO") return "#f97316";
    if (relation === "TRANSFORMS" || relation === "FEEDS" || relation === "REQUIRES_REVIEW") return "#94a3b8";
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
    var selectedTopic = lsGetSelectedCourseTopic();
    var preview = document.getElementById("ls-display-preview");
    if (!preview || (!chunk && !selectedTopic)) return;
    var text = lsNormalizePreviewLineBreaks(document.getElementById("ls-display-text").value || "");
    var formulasSource = chunk ? (chunk.formulas || []) : lsTopicFormulas(selectedTopic);
    var formulaById = {};
    var usedFormulas = new Set();
    formulasSource.forEach(function (f, idx) {
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
    var extractedHtml = lsRenderUnplacedExtractedFormulas(formulasSource, usedFormulas);
    if (extractedHtml) preview.insertAdjacentHTML("beforeend", extractedHtml);
  }

  function lsRenderTextWithFormulas(text, formulas) {
    var formulaById = {};
    (formulas || []).forEach(function (f, idx) {
      var fallbackId = "FORMULA_" + idx;
      var id = f.id || fallbackId;
      var bareId = String(id).replace(/^\[\[/, "").replace(/\]\]$/, "");
      formulaById[id] = f;
      formulaById[bareId] = f;
      formulaById[("[[" + bareId + "]]")] = f;
      formulaById[fallbackId] = f;
      formulaById[("[[" + fallbackId + "]]")] = f;
    });
    var mathBlocks = [];
    function preserveMath(expr, display) {
      var idx = mathBlocks.length;
      mathBlocks.push({ expr: expr, display: display });
      return "@@EG_TOPIC_MATH_" + idx + "@@";
    }
    var preserved = lsNormalizePreviewLineBreaks(text || "");
    preserved = preserved.replace(/\[\[([^\[\]]+)\]\]/g, function (m, id) {
      var formula = formulaById[m] || formulaById[id];
      if (!formula) return m;
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
    var html = escHtml(preserved).split("\n\n").map(function (p) {
      return "<p>" + p.replace(/\n/g, "<br>") + "</p>";
    }).join("");
    return html.replace(/@@EG_TOPIC_MATH_(\d+)@@/g, function (_m, idx) {
      var block = mathBlocks[parseInt(idx, 10)];
      return block ? lsRenderKatex(block.expr, block.display) : "";
    });
  }

  function lsTopicFormulas(topic) {
    var formulas = [];
    ((topic && topic.content_blocks) || []).forEach(function (block) {
      if (!block || block.type !== "equations") return;
      (block.items || []).forEach(function (item) {
        if (!item || !item.latex) return;
        formulas.push({
          id: item.equation_id || ("TOPIC_FORMULA_" + formulas.length),
          label: item.label || "",
          latex: item.latex,
          plain_text: item.plain_text || "",
          is_display: true,
        });
      });
    });
    return formulas;
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
    var formula = lsNormalizeKatexFormula(expr, display);
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

  function lsNormalizeKatexFormula(expr, display) {
    var formula = String(expr || "").trim();
    if (!formula) return "";
    formula = formula.replace(/\\(?:nonumber|notag)\b/g, "").trim();
    var hasEnv = /\\begin\{[^{}]+\}/.test(formula);
    var hasAlignment = /(^|[^\\])&/.test(formula) || /\\\\/.test(formula);
    if (display && hasAlignment && !hasEnv) {
      formula = "\\begin{aligned} " + formula + " \\end{aligned}";
    }
    return formula;
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

  function lsClearEditor(message, meta) {
    if (lsState.pdfObjectUrl) {
      URL.revokeObjectURL(lsState.pdfObjectUrl);
      lsState.pdfObjectUrl = null;
    }
    lsState.pdfUrl = null;
    document.getElementById("ls-workspace").innerHTML = '<div class="ls-empty-state">' + escHtml(message || "チャンクを選択すると編集ワークベンチが表示されます") + '</div>';
    document.getElementById("ls-chunk-meta").textContent = meta || "チャンクを選択してください";
    document.getElementById("ls-rewrite-prompt").disabled = true;
    document.getElementById("ls-rewrite-btn").disabled = true;
    document.getElementById("ls-save-btn").disabled = true;
    var formulasEl = document.getElementById("ls-formulas");
    if (formulasEl) formulasEl.innerHTML = "";
    lsUpdateWorkTabActive();
    lsHideActionStatus();
    lsUpdateAssistantOpenButton();
    lsUpdateRightPaneToggle();
  }

  function lsRenderFormulas(formulas) {
    var el = document.getElementById("ls-formulas");
    if (!el) return;
    if (!formulas || formulas.length === 0) {
      el.innerHTML = '<div class="ls-empty-state">この表示対象の数式一覧はありません。</div>';
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

  function lsIsCourseContentComplete() {
    var status = lsState.courseStructure && lsState.courseStructure.course_content_status;
    return Boolean(status && status.status === "completed");
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

    var moreMenuBtn = document.getElementById("ls-more-menu-btn");
    if (moreMenuBtn) {
      moreMenuBtn.disabled = !hasCourse;
      moreMenuBtn.classList.toggle("ls-menu-trigger-busy", busy);
    }

    lsSetMenuItemState("ls-audio-all-btn", ready && hasChunks && lsIsCourseContentComplete(), lsPipelineStepVisual("audio", state));

    var settingsBtn = document.getElementById("ls-settings-btn");
    var courseContentBtn = document.getElementById("ls-course-content-btn");
    if (settingsBtn) settingsBtn.disabled = !hasCourse;
    if (courseContentBtn) courseContentBtn.disabled = !ready;
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
    symbol_registry: "SymbolRegistryBuilder",
    derivation_chain: "DerivationChainAgent",
    figure_table_semantics: "FigureTableSemanticsAgent",
    thesis_reconstruction: "ThesisReconstructionAgent",
    dsl_linking: "DSLLinkingAgent",
    component_assembly: "ComponentAssemblyAgent",
    component_graph: "ComponentGraphAgent",
    narrative_annotator: "NarrativeAnnotator",
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
      body: JSON.stringify({ start_stage: targetStage }),
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

  function lsConfirmCourseContentGeneration() {
    var existing = document.getElementById("ls-course-content-confirm");
    if (existing) existing.remove();
    var overlay = document.createElement("div");
    overlay.id = "ls-course-content-confirm";
    overlay.className = "ls-settings-modal";
    overlay.innerHTML =
      '<div class="ls-settings-dialog" style="max-width:420px">' +
        '<div class="ls-settings-head">' +
          '<h3>コース内容生成</h3>' +
          '<button id="ls-course-content-confirm-close" class="lecture-chat-close" type="button">&times;</button>' +
        '</div>' +
        '<p style="font-size:13px;line-height:1.7;color:var(--color-text-secondary);margin:0 0 16px">コース内容を再作成します。既に作成済の内容は上書きされます。</p>' +
        '<div class="ls-settings-actions">' +
          '<button id="ls-course-content-cancel" class="admin-action-btn" type="button">キャンセル</button>' +
          '<button id="ls-course-content-run" class="admin-action-btn" type="button" style="background:var(--color-text-success);color:#fff">実行</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    function close() { overlay.remove(); }
    document.getElementById("ls-course-content-confirm-close").addEventListener("click", close);
    document.getElementById("ls-course-content-cancel").addEventListener("click", close);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });
    document.getElementById("ls-course-content-run").addEventListener("click", function () {
      close();
      lsGenerateCourseContent();
    });
  }

  function lsGenerateCourseContent() {
    lsState.pipelineTask = { step: "course_content", status: "running" };
    lsSetCourseTaskBusy(true);
    lsShowProgress("コース内容生成を開始しています...", "info");

    apiFetch("/admin/courses/" + lsState.courseId + "/course-content/generate", {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (errBody) {
            throw new Error((errBody && errBody.detail) || "コース内容生成を開始できませんでした");
          }, function () {
            throw new Error("コース内容生成を開始できませんでした");
          });
        }
        return res.json();
      })
      .then(function (data) {
        lsShowProgress("コース内容生成中... (0%)", "info");
        lsPollCourseContentTask(data.task_id);
      })
      .catch(function (err) {
        lsState.pipelineTask = { step: "course_content", status: "failed" };
        lsShowProgress("コース内容生成に失敗しました: " + (err.message || "不明なエラー"), "error");
        lsSetCourseTaskBusy(false);
      });
  }

  function lsPollCourseContentTask(taskId) {
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
          if (task.status === "completed") {
            clearInterval(timer);
            lsState.pipelineTask = null;
            lsShowProgress("コース内容生成が完了しました", "success");
            lsSetCourseTaskBusy(false);
            if (lsState.courseId) {
              lsLoadScripts(lsState.courseId);
              lsLoadCourseStructure(lsState.courseId);
            }
          } else if (task.status === "failed") {
            clearInterval(timer);
            lsState.pipelineTask = { step: "course_content", status: "failed" };
            lsShowProgress("コース内容生成に失敗しました: " + (task.error_message || "不明なエラー"), "error");
            lsSetCourseTaskBusy(false);
            if (lsState.courseId) lsLoadCourseStructure(lsState.courseId);
          } else {
            lsShowProgress("コース内容生成中... (" + progress + "%)", "info");
          }
        })
        .catch(function () {
          retryCount++;
          if (retryCount >= maxRetries) {
            clearInterval(timer);
            lsShowProgress("コース内容生成の進捗確認に失敗しました。", "error");
            lsState.pipelineTask = { step: "course_content", status: "failed" };
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
    if (lsState.selectedScope && lsState.selectedScope.type === "course_topic") {
      lsSaveCourseTopicDraft();
      return;
    }
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

  function lsSaveCourseTopicDraft() {
    var topic = lsGetSelectedCourseTopic();
    if (!lsState.courseId || !topic) return;
    var materialEl = document.getElementById("ls-course-material-text");
    var payload = {
      chapter_index: lsState.selectedScope.chapterIndex,
      topic_index: lsState.selectedScope.topicIndex,
      key_concepts: lsSplitLines((document.getElementById("ls-course-key-concepts") || {}).value),
      student_material: {
        source_format: "eg-markdown-v1",
        source_text: materialEl ? materialEl.value : lsTopicStudentMaterialSource(topic),
      },
      spoken_script: (document.getElementById("ls-course-spoken-script") || {}).value || "",
      cautions: lsSplitLines((document.getElementById("ls-course-cautions") || {}).value),
      check_questions: lsCollectCheckQuestions(),
    };
    document.getElementById("ls-save-btn").disabled = true;
    lsShowActionStatus("保存中...", "info");
    apiFetch("/admin/courses/" + encodeURIComponent(lsState.courseId) +
      "/lecture-studio/course-topics/" + encodeURIComponent(topic.id || lsState.selectedScope.topicId || ""), {
      method: "PUT",
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Save failed");
        return res.json();
      })
      .then(function () {
        topic.key_concepts = payload.key_concepts;
        topic.student_material = payload.student_material;
        topic.spoken_script = payload.spoken_script;
        topic.cautions = payload.cautions;
        topic.check_questions = payload.check_questions;
        topic.status = "generated";
        lsShowActionStatus("授業用ドラフトを保存しました", "success");
        lsRenderCourseStructure();
      })
      .catch(function () {
        lsShowActionStatus("保存に失敗しました", "error");
      })
      .finally(function () {
        document.getElementById("ls-save-btn").disabled = false;
      });
  }

  function lsRewriteScript() {
    if (lsState.selectedScope && lsState.selectedScope.type === "course_topic") {
      lsRewriteCourseTopicDraft();
      return;
    }
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

  function lsRewriteCourseTopicDraft() {
    var topic = lsGetSelectedCourseTopic();
    if (!lsState.courseId || !topic) return;
    var prompt = document.getElementById("ls-rewrite-prompt").value.trim();
    if (!prompt) {
      lsShowActionStatus("指示を入力してください", "error");
      return;
    }
    var payload = {
      prompt: prompt,
      chapter_index: lsState.selectedScope.chapterIndex,
      topic_index: lsState.selectedScope.topicIndex,
      key_concepts: lsSplitLines((document.getElementById("ls-course-key-concepts") || {}).value),
      student_material: {
        source_format: "eg-markdown-v1",
        source_text: (document.getElementById("ls-course-material-text") || {}).value || lsTopicStudentMaterialSource(topic),
      },
      spoken_script: (document.getElementById("ls-course-spoken-script") || {}).value || "",
      cautions: lsSplitLines((document.getElementById("ls-course-cautions") || {}).value),
      check_questions: lsCollectCheckQuestions(),
    };
    document.getElementById("ls-rewrite-btn").disabled = true;
    lsShowActionStatus("AIで提案中...", "info");
    apiFetch("/admin/courses/" + encodeURIComponent(lsState.courseId) +
      "/lecture-studio/course-topics/" + encodeURIComponent(topic.id || lsState.selectedScope.topicId || "") + "/draft/rewrite", {
      method: "POST",
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Rewrite failed");
        return res.json();
      })
      .then(function (data) {
        topic.key_concepts = data.key_concepts || [];
        topic.student_material = data.student_material || { source_format: "eg-markdown-v1", source_text: "" };
        topic.spoken_script = data.spoken_script || "";
        topic.cautions = data.cautions || [];
        topic.check_questions = lsNormalizeCheckQuestions(data.check_questions || []);
        lsShowActionStatus("提案を反映しました。保存すると確定します。", "success");
        lsRenderSelectedCourseTopic(topic);
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

    initMaterialPipelineOutsideClick();

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

  // ── 反復改善 (Iterative improvement / Revisions) UI (#408) ───────────
  // 採用版 (active run) を保持したまま改善候補を生成・検証・採否する管理UI。
  // latest run（処理状況）と active run（採用成果物）を明確に区別して表示する。
  var EGRevisions = (function () {
    var current = { documentId: null, revisionId: null, report: null, filter: "all" };

    var STAGE_LABELS = {
      queued: "待機中", audit: "原論文を監査中", proposal: "修正候補を生成中",
      candidate_assembly: "候補を組み立て中", validation: "候補を検証中",
      report: "差分レポートを作成中", completed: "完了 — 採否を確認してください",
      failed: "失敗"
    };
    var OUTCOME_LABELS = {
      no_audit_targets: "監査の結果、修正対象は見つかりませんでした。",
      proposals_failed: "修正対象はありましたが、採用可能な修正候補を生成できませんでした（改善案の生成に失敗）。",
      candidate_invalid: "修正候補を生成しましたが、検証に通らず採用できません。",
      changes_proposed: "修正候補を生成しました。採否を確認してください。"
    };

    function el(id) { return document.getElementById(id); }
    function stopPolling() { if (current.pollTimer) { clearTimeout(current.pollTimer); current.pollTimer = null; } }
    function close() { stopPolling(); var m = el("eg-rev-modal"); if (m) m.remove(); }
    function docPath() { return "/admin/documents/" + encodeURIComponent(current.documentId) + "/revisions"; }

    function open(documentId) {
      current.documentId = documentId;
      current.revisionId = null;
      current.report = null;
      current.filter = "all";
      renderModal();
      loadList();
    }

    function renderModal() {
      close();
      var overlay = document.createElement("div");
      overlay.id = "eg-rev-modal";
      overlay.className = "eg-rev-overlay";
      overlay.innerHTML =
        '<div class="eg-rev-box">' +
          '<div class="eg-rev-header">' +
            '<h3>反復改善パイプライン</h3>' +
            '<button id="eg-rev-close" class="eg-rev-close" type="button">×</button>' +
          '</div>' +
          '<div class="eg-rev-active" id="eg-rev-active">読み込み中…</div>' +
          '<div class="eg-rev-toolbar">' +
            '<button id="eg-rev-start" class="admin-action-btn" type="button">改善処理を開始</button>' +
            '<span class="eg-rev-hint">採用済み成果物は変更されません。</span>' +
          '</div>' +
          '<div class="eg-rev-list" id="eg-rev-list"></div>' +
          '<div class="eg-rev-detail" id="eg-rev-detail"></div>' +
        '</div>';
      document.body.appendChild(overlay);
      el("eg-rev-close").addEventListener("click", close);
      el("eg-rev-start").addEventListener("click", startRevision);
    }

    function loadList() {
      apiFetch(docPath())
        .then(function (r) { return r.json(); })
        .then(renderList)
        .catch(function () { if (el("eg-rev-list")) el("eg-rev-list").textContent = "一覧の取得に失敗しました。"; });
    }

    function renderList(lineage) {
      var act = el("eg-rev-active");
      if (act) {
        act.innerHTML =
          '採用版 (active run): <code>' + escHtml(lineage.active_run_id || "なし（既存データへfallback）") + '</code>' +
          ' <span class="eg-rev-sep">|</span> 最新run (処理状況): <code>' + escHtml(lineage.latest_run_id || "-") + '</code>';
      }
      var runs = (lineage.runs || []).filter(function (r) { return r.run_type === "revision"; });
      var listEl = el("eg-rev-list");
      if (!listEl) return;
      if (!runs.length) { listEl.innerHTML = '<p class="eg-rev-empty">まだ改善runはありません。</p>'; return; }
      var html = '<table class="eg-rev-table"><thead><tr><th>revision</th><th>状態</th><th>base</th><th></th></tr></thead><tbody>';
      runs.forEach(function (r) {
        html += '<tr><td><code>' + escHtml((r.id || "").slice(0, 8)) + '</code>' +
            (r.is_active ? ' <span class="eg-rev-badge eg-rev-badge-active">採用中</span>' : '') + '</td>' +
          '<td><span class="eg-rev-status">' + escHtml(r.revision_status || r.status || "") + '</span></td>' +
          '<td><code>' + escHtml((r.base_run_id || "").slice(0, 8)) + '</code></td>' +
          '<td><button class="eg-rev-open admin-action-btn" type="button" data-rev="' + escHtml(r.id) + '">詳細</button></td></tr>';
      });
      html += '</tbody></table>';
      listEl.innerHTML = html;
      listEl.querySelectorAll(".eg-rev-open").forEach(function (b) {
        b.addEventListener("click", function () { openDetail(this.getAttribute("data-rev")); });
      });
    }

    function startRevision() {
      var btn = el("eg-rev-start"); if (btn) btn.disabled = true;
      apiFetch(docPath(), { method: "POST", body: "{}" })
        .then(function (r) {
          if (r.status === 409) throw new Error("採用版（active run）がありません。先に解析を完了してください。");
          return r.json();
        })
        .then(function (data) {
          if (btn) btn.disabled = false;
          loadList();
          if (data.revision_run_id) openDetail(data.revision_run_id);
        })
        .catch(function (e) { if (btn) btn.disabled = false; alert("開始に失敗: " + (e.message || e)); });
    }

    function openDetail(revId) {
      stopPolling();
      current.revisionId = revId;
      current.report = null;
      apiFetch(docPath() + "/" + encodeURIComponent(revId))
        .then(function (r) { return r.json(); })
        .then(function (detail) {
          renderDetail(detail);
          if (detail.has_report) loadReport();
          restoreRunState(detail);   // #414-4: restore running/failed/completed on reload
        })
        .catch(function () { alert("詳細の取得に失敗しました。"); });
    }

    // Restore the run's UI state after a modal reopen / browser reload (#414-4):
    // running -> resume polling the same task; failed -> show stage + error;
    // completed -> the report (loaded above) already shows the outcome.
    function restoreRunState(detail) {
      var status = String(detail.status || "").toLowerCase();
      var task = detail.latest_task || null;
      current.taskId = (task && task.task_id) || null;
      if (status === "running") {
        setRunning(true, STAGE_LABELS[detail.current_stage] || "処理中…");
        renderRunProgress({ current_stage: detail.current_stage, task: task });
        pollRunStatus();
      } else if (status === "failed") {
        setRunning(false);
        renderRunFailure({
          current_stage: detail.current_stage,
          error_message: detail.error_message,
          task: task
        });
      }
    }

    function decisionsHtml(decisions) {
      if (!decisions || !decisions.length) return "";
      var rows = decisions.map(function (d) {
        var meta = d.metadata || {};
        return '<li>' + escHtml(d.old_status) + ' → <strong>' + escHtml(d.new_status) + '</strong>' +
          (meta.comment ? ' — ' + escHtml(meta.comment) : '') + '</li>';
      }).join("");
      return '<div class="eg-rev-decisions"><h5>監査履歴</h5><ul>' + rows + '</ul></div>';
    }

    function renderDetail(detail) {
      var d = el("eg-rev-detail"); if (!d) return;
      var html = '<div class="eg-rev-detail-head"><h4>revision <code>' +
          escHtml((detail.revision_run_id || "").slice(0, 8)) + '</code></h4>' +
        '<div>状態: <strong>' + escHtml(detail.revision_status || detail.status || "") + '</strong>' +
        ' / checkpoints: ' + (detail.checkpoint_count || 0) + '</div></div>';
      html += '<details class="eg-rev-ops"><summary>（デバッグ用）revision operations JSON を直接指定</summary>' +
        '<p class="eg-rev-hint">通常は空のままにします。監査結果から修正候補が自動生成されます。' +
        'JSON を入力した場合のみ、その operations が使われます（サーバー側で検証）。</p>' +
        '<textarea id="eg-rev-ops" class="eg-rev-ops-text" placeholder="[]"></textarea></details>';
      html += '<div class="eg-rev-actions">' +
        '<button id="eg-rev-run" class="admin-action-btn" type="button">監査＋候補生成</button>' +
        '<button id="eg-rev-accept" class="admin-action-btn"' + (detail.has_report ? '' : ' disabled') + ' type="button">採用</button>' +
        '<button id="eg-rev-reject" class="admin-action-btn" type="button">却下</button>' +
        '<button id="eg-rev-revise" class="admin-action-btn" type="button">再修正</button>' +
        '<label class="eg-rev-confirm"><input type="checkbox" id="eg-rev-confirm-protected"> 保護項目の変更を明示承認</label>' +
        '<input id="eg-rev-comment" class="eg-rev-comment" placeholder="決定コメント">' +
        '</div>';
      html += '<div class="eg-rev-progress" id="eg-rev-progress"></div>';
      html += '<div class="eg-rev-report" id="eg-rev-report"></div>';
      html += decisionsHtml(detail.decisions);
      d.innerHTML = html;
      el("eg-rev-run").addEventListener("click", runRevision);
      el("eg-rev-accept").addEventListener("click", acceptRevision);
      el("eg-rev-reject").addEventListener("click", rejectRevision);
      el("eg-rev-revise").addEventListener("click", reviseRevision);
    }

    function setProgress(html) { var p = el("eg-rev-progress"); if (p) p.innerHTML = html || ""; }

    function setRunning(on, label) {
      current.running = !!on;
      var btn = el("eg-rev-run");
      if (btn) { btn.disabled = !!on; btn.textContent = on ? (label || "処理中…") : "監査＋候補生成"; }
      // Prevent accept/revise while a worker is active (#412 P1-4: avoid double run).
      ["eg-rev-accept", "eg-rev-reject", "eg-rev-revise"].forEach(function (id) {
        var b = el(id); if (b && on) b.disabled = true;
      });
    }

    function renderRunProgress(st) {
      var rd = (st.task && st.task.result_data) || {};
      var stageKey = rd.stage || st.current_stage || "queued";
      var label = STAGE_LABELS[stageKey] || stageKey;
      // Prefer the real "completed / total" count (e.g. 27 / 199) over a bare %
      // when the worker has published per-checkpoint progress (#414-3).
      var detail = "";
      if (typeof rd.total_count === "number" && rd.total_count > 0) {
        detail = " " + (rd.completed_count || 0) + " / " + rd.total_count;
      } else if (typeof rd.progress === "number" && rd.progress > 0) {
        detail = " " + rd.progress + "%";
      }
      setProgress('<div class="eg-rev-info">' + escHtml(label) + escHtml(detail) + ' …</div>');
    }

    function renderRunFailure(st) {
      var stage = STAGE_LABELS[st.current_stage] || st.current_stage || "";
      var msg = st.error_message || "サーバーエラー";
      var rd = (st.task && st.task.result_data) || {};
      var extra = "";
      if (rd.rejected_operations && rd.rejected_operations.length) {
        extra = '<div>修正候補が検証で却下されました（' + rd.rejected_operations.length + ' 件）。</div>';
      }
      setProgress('<div class="eg-rev-warn">処理に失敗しました（' + escHtml(stage) + '）: ' +
        escHtml(msg) + extra + '</div>');
    }

    function pollRunStatus() {
      if (!current.revisionId) return;
      var url = docPath() + "/" + encodeURIComponent(current.revisionId) + "/run-status" +
        (current.taskId ? ("?task_id=" + encodeURIComponent(current.taskId)) : "");
      apiFetch(url)
        .then(function (r) { if (!r.ok) throw new Error("status " + r.status); return r.json(); })
        .then(function (st) {
          current.pollFails = 0;
          var status = String(st.status || "").toLowerCase();
          if (status === "completed") {
            setRunning(false);
            setProgress('<div class="eg-rev-done">' + escHtml(STAGE_LABELS.completed) + '</div>');
            openDetail(current.revisionId);   // reload detail + report
            return;
          }
          if (status === "failed") {
            setRunning(false);
            renderRunFailure(st);
            return;
          }
          renderRunProgress(st);
          current.pollTimer = setTimeout(pollRunStatus, 3000);
        })
        .catch(function () {
          // Network/timeout is NOT a processing failure — keep checking (#412 P1-4).
          current.pollFails = (current.pollFails || 0) + 1;
          setProgress('<div class="eg-rev-info">接続が切れたため状態を確認中…（再試行 ' +
            current.pollFails + '）</div>');
          current.pollTimer = setTimeout(pollRunStatus, 5000);
        });
    }

    function runRevision() {
      var body = {};
      var opsEl = el("eg-rev-ops");
      if (opsEl && opsEl.value.trim()) {
        try { body.operations = JSON.parse(opsEl.value); }
        catch (e) { alert("operations の JSON が不正です。"); return; }
      }
      stopPolling();
      current.taskId = null;
      setRunning(true, STAGE_LABELS.audit + " …");
      setProgress('<div class="eg-rev-info">' + escHtml(STAGE_LABELS.queued) + ' …</div>');
      apiFetch(docPath() + "/" + encodeURIComponent(current.revisionId) + "/run",
               { method: "POST", body: JSON.stringify(body) })
        .then(function (r) {
          if (r.status === 409) { setRunning(false); setProgress('<div class="eg-rev-warn">この改善runは既に実行中です。</div>'); return null; }
          if (!r.ok) { return r.text().then(function (t) { throw new Error("server " + r.status + ": " + t); }); }
          return r.json();
        })
        .then(function (data) {
          if (!data) return;
          current.taskId = data.task_id || null;
          pollRunStatus();   // 202 accepted → poll task/revision status
        })
        .catch(function (e) {
          // Could not even start the run → a real failure, not a timeout.
          setRunning(false);
          setProgress('<div class="eg-rev-warn">開始に失敗しました: ' + escHtml(String(e.message || e)) + '</div>');
        });
    }

    function loadReport() {
      apiFetch(docPath() + "/" + encodeURIComponent(current.revisionId) + "/report")
        .then(function (r) { if (!r.ok) throw new Error("no report"); return r.json(); })
        .then(function (report) { current.report = report; renderReport(); })
        .catch(function () {});
    }

    function metricsTable(before, after) {
      before = before || {}; after = after || {};
      var keys = [
        ["hard_error_count", "hard error"], ["warning_count", "warning"],
        ["review_required_count", "review required"], ["unresolved_reference_count", "未解決参照"],
        ["source_backed_rate", "source-backed率"], ["low_confidence_equation_count", "低conf式"],
        ["main_claim_coverage", "main claim coverage"], ["component_granularity_violation_count", "粒度違反"]
      ];
      var rows = keys.map(function (k) {
        var b = before[k[0]], a = after[k[0]];
        var cls = (typeof a === "number" && typeof b === "number" && a !== b) ? " class=\"eg-rev-changed\"" : "";
        return '<tr' + cls + '><td>' + escHtml(k[1]) + '</td><td>' + escHtml(String(b)) + '</td><td>' + escHtml(String(a)) + '</td></tr>';
      }).join("");
      return '<table class="eg-rev-metrics"><thead><tr><th>指標</th><th>before</th><th>after</th></tr></thead><tbody>' + rows + '</tbody></table>';
    }

    var FILTERS = [["all", "すべて"], ["claim", "claim"], ["equation", "equation"], ["component", "component"], ["graph_edge", "graph"]];

    function filterBar() {
      return '<div class="eg-rev-filter">' + FILTERS.map(function (f) {
        return '<button class="eg-rev-filter-btn' + (current.filter === f[0] ? ' active' : '') +
          '" type="button" data-filter="' + f[0] + '">' + escHtml(f[1]) + '</button>';
      }).join("") + '</div>';
    }

    function changeMatchesFilter(c) {
      if (current.filter === "all") return true;
      if (current.filter === "graph_edge") return c.entity_type === "graph_edge" || c.entity_type === "graph_node";
      return c.entity_type === current.filter;
    }

    function changesHtml(report) {
      var changes = (report.entity_changes || []).filter(changeMatchesFilter);
      if (!changes.length) return '<p class="eg-rev-empty">該当する変更はありません。</p>';
      return '<ul class="eg-rev-changes">' + changes.map(function (c) {
        var protectedTag = c.protected ? ' <span class="eg-rev-badge eg-rev-badge-protected">保護項目</span>' : '';
        var src = (c.source_locations || []).map(function (s) {
          return escHtml(s.section_id || s.chunk_id || JSON.stringify(s));
        }).join(", ");
        return '<li class="eg-rev-change">' +
          '<span class="eg-rev-ctype">' + escHtml(c.change_type) + '</span> ' +
          '<code>' + escHtml(c.entity_type) + ':' + escHtml(c.entity_id || "") + '</code>' + protectedTag +
          (c.reason ? '<div class="eg-rev-reason">理由: ' + escHtml(c.reason) + '</div>' : '') +
          (c.checkpoint_ids && c.checkpoint_ids.length ? '<div class="eg-rev-trace">checkpoint: ' + escHtml(c.checkpoint_ids.join(", ")) + '</div>' : '') +
          (c.evidence_refs && c.evidence_refs.length ? '<div class="eg-rev-trace">evidence: ' + escHtml(c.evidence_refs.join(", ")) + '</div>' : '') +
          (c.source_chunk_ids && c.source_chunk_ids.length ? '<div class="eg-rev-trace">source chunk: ' + escHtml(c.source_chunk_ids.join(", ")) + '</div>' : '') +
          (src ? '<div class="eg-rev-trace">原文: ' + src + '</div>' : '') +
          '</li>';
      }).join("") + '</ul>';
    }

    function stageSummaryHtml(report) {
      var ss = report.stage_summary; if (!ss) return "";
      var a = ss.audit || {}, g = ss.candidate_generation || {}, e = ss.candidate_evaluation || {};
      var outcomeMsg = OUTCOME_LABELS[ss.outcome] || "";
      var reasons = (g.rejection_reasons || []).map(function (r) {
        return '<li>' + escHtml(r.reason) + ': ' + (r.count || 0) + '</li>';
      }).join("");
      return '<div class="eg-rev-stages">' +
        (outcomeMsg ? '<div class="eg-rev-outcome">' + escHtml(outcomeMsg) + '</div>' : '') +
        '<h5>監査結果</h5><ul class="eg-rev-stagelist">' +
          '<li>チェック: ' + (a.checkpoints_total || 0) + '</li>' +
          '<li>修正対象: ' + (a.revision_target_count || 0) + '</li>' +
          '<li>人手確認: ' + (a.manual_review_count || 0) + '</li></ul>' +
        '<h5>候補生成</h5><ul class="eg-rev-stagelist">' +
          '<li>採用可能な提案: ' + (g.accepted_count || 0) + '</li>' +
          '<li>拒否された提案: ' + (g.rejected_count || 0) + '</li>' +
          (reasons ? '<li>主な拒否理由:<ul>' + reasons + '</ul></li>' : '') + '</ul>' +
        '<h5>候補評価</h5><ul class="eg-rev-stagelist">' +
          '<li>適用変更: ' + (e.applied_change_count || 0) + '</li>' +
          '<li>candidate invalid: <strong>' + (e.candidate_invalid ? "true" : "false") + '</strong></li>' +
          '<li>新規未解決参照: ' + (e.new_unknown_reference_count || 0) + '</li></ul>' +
        '</div>';
    }

    var EXCLUDE_LABELS = {
      excluded_invalid: "不正な操作", excluded_dependency: "依存により除外",
      requires_confirmation: "要承認"
    };

    // #415: per-operation outcome — adoptable / partially_adoptable / blocked.
    function excludedOpsHtml(report) {
      var status = report.candidate_status || (report.summary || {}).candidate_status || "adoptable";
      var os = report.operation_summary || {};
      var excluded = report.excluded_operations || [];
      if (status === "adoptable" && !excluded.length) {
        return '<div class="eg-rev-outcome">すべての変更を採用できます（' +
          (os.applied_count || 0) + ' 件）。</div>';
      }
      var head;
      if (status === "partially_adoptable") {
        head = '<div class="eg-rev-outcome">問題のある変更を除外すれば採用できます。</div>';
      } else if (status === "blocked") {
        head = '<div class="eg-rev-warn">除外後も hard error または未解決参照が残るため採用できません（blocked）。</div>';
      } else {
        head = "";
      }
      var counts = '<ul class="eg-rev-stagelist">' +
        '<li>採用可能な変更: ' + (os.applied_count || 0) + '</li>' +
        '<li>除外（不正）: ' + (os.excluded_invalid_count || 0) + '</li>' +
        '<li>除外（依存）: ' + (os.excluded_dependency_count || 0) + '</li>' +
        '<li>この候補が新規に生じさせる hard error: ' +
          ((report.summary || {}).hard_error_count || 0) + '</li>' +
        '<li>元データから引き継ぐ hard error: ' +
          ((report.summary || {}).carried_hard_error_count || 0) + '</li></ul>';
      var rows = excluded.map(function (x) {
        return '<li class="eg-rev-change"><span class="eg-rev-ctype">' +
          escHtml(EXCLUDE_LABELS[x.status] || x.status) + '</span> ' +
          '<code>' + escHtml(x.target_type || "") + ':' + escHtml(x.target_id || "") + '</code>' +
          (x.operation_id ? ' <span class="eg-rev-trace">(' + escHtml(x.operation_id) + ')</span>' : '') +
          '<div class="eg-rev-reason">理由: ' + escHtml(x.reason || "") + '</div></li>';
      }).join("");
      return '<div class="eg-rev-stages">' + head + counts +
        (rows ? '<h5>除外される変更</h5><ul class="eg-rev-changes">' + rows + '</ul>' : '') + '</div>';
    }

    function renderReport() {
      var rep = el("eg-rev-report"); if (!rep || !current.report) return;
      var report = current.report;
      var s = report.summary || {};
      var status = report.candidate_status || s.candidate_status || (s.acceptable ? "adoptable" : "blocked");
      var partial = status === "partially_adoptable";
      var acc = el("eg-rev-accept");
      if (acc) {
        acc.disabled = !s.acceptable;
        // #415: partial adoption needs an explicit, differently-labelled action.
        acc.textContent = partial ? "問題のある変更を除外して採用" : "採用";
      }
      var warn = "";
      if (status === "blocked") warn = '<div class="eg-rev-warn">候補は blocked です。具体的な hard error と除外対象を確認してください。</div>';
      else if (s.protected_change_count > 0) warn = '<div class="eg-rev-warn">教師承認/手動編集済み項目への変更が ' + s.protected_change_count + ' 件あります。採用には明示承認が必要です。</div>';
      var html = warn +
        '<div class="eg-rev-rec">推奨: <strong>' + escHtml(report.recommendation) + '</strong>' +
          '（参考情報。自動採用には使われません）</div>' +
        excludedOpsHtml(report) +
        stageSummaryHtml(report) +
        '<h5>品質指標 (before / after)</h5>' + metricsTable(report.quality_before, report.quality_after) +
        '<h5>解消: ' + (s.resolved_issue_count || 0) + ' / 新規: ' + (s.introduced_issue_count || 0) +
          ' / 変更: ' + (s.entity_change_count || 0) + '</h5>' +
        filterBar() +
        '<div id="eg-rev-changes-box">' + changesHtml(report) + '</div>';
      rep.innerHTML = html;
      rep.querySelectorAll(".eg-rev-filter-btn").forEach(function (b) {
        b.addEventListener("click", function () {
          current.filter = this.getAttribute("data-filter");
          renderReport();
        });
      });
    }

    function decisionBody() {
      var comment = (el("eg-rev-comment") && el("eg-rev-comment").value) || "";
      var confirm = !!(el("eg-rev-confirm-protected") && el("eg-rev-confirm-protected").checked);
      // Adopt the reduced operation set only when the candidate is partial; the
      // differently-labelled button is the user's explicit partial-accept action.
      var s = (current.report && current.report.summary) || {};
      var status = (current.report && current.report.candidate_status) || s.candidate_status || "";
      var acceptPartial = status === "partially_adoptable";
      return JSON.stringify({ comment: comment, confirm_protected: confirm,
                              accept_partial: acceptPartial });
    }

    function decisionResponse(r, fallback) {
      if (r.ok) return r.json();
      return r.json()
        .catch(function () { return {}; })
        .then(function (j) {
          throw new Error(j.detail || fallback || ("server " + r.status));
        });
    }

    function acceptRevision() {
      apiFetch(docPath() + "/" + encodeURIComponent(current.revisionId) + "/accept",
               { method: "POST", body: decisionBody() })
        .then(function (r) {
          if (r.status === 409) throw new Error("競合: 採用版が更新されています。最新の差分で再確認してください。");
          return decisionResponse(r, "採用できません");
        })
        .then(function () { alert("採用しました。採用版を切り替えました。"); loadList(); openDetail(current.revisionId); })
        .catch(function (e) { alert(e.message || "採用に失敗しました。"); });
    }

    function rejectRevision() {
      apiFetch(docPath() + "/" + encodeURIComponent(current.revisionId) + "/reject",
               { method: "POST", body: decisionBody() })
        .then(function (r) { return decisionResponse(r, "却下に失敗しました。"); })
        .then(function () { alert("却下しました。採用版は変更されません。"); loadList(); openDetail(current.revisionId); })
        .catch(function (e) { alert(e.message || "却下に失敗しました。"); });
    }

    function reviseRevision() {
      apiFetch(docPath() + "/" + encodeURIComponent(current.revisionId) + "/revise",
               { method: "POST", body: decisionBody() })
        .then(function (r) { return decisionResponse(r, "再修正の作成に失敗しました。"); })
        .then(function (data) { loadList(); if (data.revision_run_id) openDetail(data.revision_run_id); })
        .catch(function (e) { alert(e.message || "再修正の作成に失敗しました。"); });
    }

    return { open: open };
  })();
  window.EGRevisions = EGRevisions;

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
