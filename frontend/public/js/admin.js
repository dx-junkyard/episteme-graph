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
    materialFilter: "all",   // all | no_course | recent | completed
    materialSort: "uploaded", // uploaded | analyzed | title | volume
    materialSearch: "",
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

  // Close every open group dropdown.
  function closeAllTabMenus() {
    document.querySelectorAll(".admin-tab-group.open").forEach(function (g) {
      g.classList.remove("open");
      var trigger = g.querySelector(".admin-tab-group-trigger");
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    });
  }

  // Mark the group that owns the active tab, and surface the current sub-tab
  // label on its trigger so the selection stays visible when the menu is closed.
  function _syncActiveGroup(tabName) {
    document.querySelectorAll(".admin-tab-group").forEach(function (g) {
      var activeBtn = g.querySelector('.admin-tab[data-tab="' + tabName + '"]');
      g.classList.toggle("active-group", !!activeBtn);
      var cur = g.querySelector(".admin-tab-group-current");
      if (cur) cur.textContent = activeBtn ? activeBtn.textContent : "";
    });
  }

  // Core activation: toggle .on on the tab button, show its panel, sync group.
  // Shared by the click handler and the public activateTabView().
  function _applyActiveTab(tabName) {
    document.querySelectorAll(".admin-tab").forEach(function (b) {
      b.classList.toggle("on", b.dataset.tab === tabName);
    });
    document.querySelectorAll(".admin-panel").forEach(function (p) { p.classList.remove("vis"); });
    var target = document.getElementById("tab-" + tabName);
    if (target) target.classList.add("vis");
    _syncActiveGroup(tabName);
  }

  // Hide a group's trigger when it has no visible tabs (e.g. SYSTEM_ADMIN hides
  // all コンテンツ tabs). Call after role-based visibility changes.
  function refreshTabGroupVisibility() {
    document.querySelectorAll(".admin-tab-group").forEach(function (g) {
      var tabs = g.querySelectorAll(".admin-tab");
      var anyVisible = false;
      tabs.forEach(function (b) { if (b.style.display !== "none") anyVisible = true; });
      g.style.display = anyVisible ? "" : "none";
    });
  }

  function initTabs() {
    document.getElementById("adminTabs").addEventListener("click", function (e) {
      // Group trigger → toggle its dropdown (accordion: only one open at a time)
      var trigger = e.target.closest(".admin-tab-group-trigger");
      if (trigger) {
        var group = trigger.parentElement;
        var wasOpen = group.classList.contains("open");
        closeAllTabMenus();
        if (!wasOpen) {
          group.classList.add("open");
          trigger.setAttribute("aria-expanded", "true");
        }
        return;
      }

      // Tab menu item → activate the tab and close the dropdown
      var btn = e.target.closest(".admin-tab");
      if (!btn || !btn.dataset.tab) return;
      if (btn.style.display === "none") return;
      _applyActiveTab(btn.dataset.tab);
      closeAllTabMenus();

      // Fire tab activation callbacks
      var cbs = _tabActivateCallbacks[btn.dataset.tab];
      if (cbs) {
        cbs.forEach(function (fn) { fn(); });
      }

      // G層: タブ切替のたびに Next Steps を再取得する（ポーリングはしない）。
      if (window.AdminNextSteps) window.AdminNextSteps.refresh();
    });

    // Click outside any group closes the open dropdown
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".admin-tab-group")) closeAllTabMenus();
    });

    // Reflect the initially-active tab (HTML default, or role-based default) on its group
    var initiallyOn = document.querySelector(".admin-tab.on");
    if (initiallyOn && initiallyOn.dataset.tab) _syncActiveGroup(initiallyOn.dataset.tab);
  }

  function activateTabView(tabName) {
    _applyActiveTab(tabName);
    closeAllTabMenus();
    // G層: タブ切替のたびに Next Steps を再取得する（ポーリングはしない）。
    if (window.AdminNextSteps) window.AdminNextSteps.refresh();
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
      var pipelineStatus = state.materialPipelineStatus[m.material_id] || {};
      var isDegraded = pipelineStatus.is_degraded || false;
      var degradedStages = pipelineStatus.degraded_stages || [];
      var availableFeatures = pipelineStatus.available_features || [];
      var retryStage = pipelineStatus.retry_suggestion || "";

      var statusClass = "status-" + m.status;
      if (isDegraded) statusClass += " status-degraded";
      var statusLabel = {
        uploaded: "アップロード済み",
        processing: "処理中...",
        completed: isDegraded ? "完了（一部機能制限）" : "完了",
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
        statusLabel = "失敗: " + m.analysis_stage + " (チャンク・RAGチャットは使用可能)";
      }

      var uploadedAt = m.uploaded_at || "";
      if (uploadedAt) {
        try {
          var dt = new Date(uploadedAt);
          uploadedAt = dt.getFullYear() + "/" + (dt.getMonth() + 1) + "/" + dt.getDate() + " " +
            dt.getHours() + ":" + String(dt.getMinutes()).padStart(2, "0");
        } catch (e) { /* keep original */ }
      }

      html += '<tr data-material-id="' + escHtml(m.material_id) + '">';
      html += "<td>" + escHtml(m.filename) + "</td>";
      html += "<td>" + escHtml(m.title) + "</td>";
      html += '<td><span class="admin-status ' + statusClass + '" title="' +
        (isDegraded ? "利用可能な機能: " + availableFeatures.join(", ") + "\n機能制限中のステージ: " + degradedStages.join(", ") : "") +
        '">' + statusLabel + "</span>";
      if (isDegraded) {
        html += '<div class="admin-degraded-hint" style="font-size:11px;color:var(--color-text-warning,#c85a00);margin-top:2px;">' +
          '⚠ 一部の高度な機能は制限されています。RAGチャットは利用可能です。' +
          (retryStage ? ' <a href="#" class="admin-retry-stage-link" data-material-id="' + escHtml(m.material_id) + '" data-stage="' + escHtml(retryStage) + '" style="text-decoration:underline;cursor:pointer;">ステージ再実行</a>' : '') +
          '</div>';
      }
      html += "</td>";
      html += "<td>" + escHtml(uploadedAt) + "</td>";
      var hasPdf = m.has_pdf === true;
      var chunkCount = typeof m.chunk_count === "number" ? m.chunk_count : null;
      // status=failed のみ PDF 再登録を促す。縮退(completed)はチャンクが存在するため不要
      var pdfRegistrationFailed = m.status === "failed" && chunkCount === 0;
      var pdfBtnLabel = pdfRegistrationFailed ? "失敗 (PDF再登録)" : (hasPdf ? "登録済" : "PDF再登録");
      var pdfBtnClass = pdfRegistrationFailed ? " admin-pdf-reupload-btn-failed" : "";
      var pdfBtnTitle = pdfRegistrationFailed
        ? "チャンク作成に失敗しました。PDFを再登録してください"
        : "PDFのみ再登録";
      var resumeBtn = "";
      if (m.status === "failed" && m.document_id) {
        resumeBtn = '<button class="admin-resume-analysis-btn" data-document-id="' + escHtml(m.document_id) + '" data-filename="' + escHtml(m.filename || m.title || "教材") + '" title="保存済みPDFから解析を再開">解析再開</button>';
      }
      // 機能1: 解析成果をグループへ共有（document_id が必要）
      var shareBtn = m.document_id
        ? '<button class="admin-share-doc-btn" data-document-id="' + escHtml(m.document_id) + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" title="解析成果をグループへ共有" style="background:none;border:1px solid var(--color-text-info);color:var(--color-text-info);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:12px">共有</button>'
        : "";
      // V層: 共有版の発行・履歴・削除予約（document_id が必要）
      var versionBtn = m.document_id
        ? '<button class="admin-version-doc-btn" data-document-id="' + escHtml(m.document_id) + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" title="共有バージョンを管理" style="background:none;border:1px solid var(--color-text-info);color:var(--color-text-info);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:12px">共有版</button>'
        : "";
      // 画像読み取りパイプライン（migration 041）: 抽出された図・画像を表示（document_id が必要）
      var figuresBtn = m.document_id
        ? '<button class="admin-figures-btn" data-document-id="' + escHtml(m.document_id) + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" title="抽出された図・画像を表示" style="background:none;border:1px solid var(--color-text-info);color:var(--color-text-info);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:12px">図・画像</button>'
        : "";
      html += '<td><div class="materials-action-cell">' +
        materialPipelineMenuHtml(m) +
        '<button class="admin-pdf-reupload-btn' + pdfBtnClass + '" data-material-id="' + escHtml(m.material_id) + '" title="' + escHtml(pdfBtnTitle) + '">' + pdfBtnLabel + '</button>' +
        resumeBtn +
        figuresBtn +
        shareBtn +
        versionBtn +
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

    // 機能1: 解析成果のグループ共有ボタン
    tbody.querySelectorAll(".admin-share-doc-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openDocumentShareModal(this.getAttribute("data-document-id"), this.getAttribute("data-title"));
      });
    });

    // 画像読み取りパイプライン（migration 041）: 図・画像一覧モーダル
    tbody.querySelectorAll(".admin-figures-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openFiguresModal(this.getAttribute("data-document-id"), this.getAttribute("data-title"));
      });
    });

    // V層: 共有バージョン管理ボタン（発行 / 版履歴 / 削除予約）
    tbody.querySelectorAll(".admin-version-doc-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (window.Versioning) {
          window.Versioning.openModal("document", this.getAttribute("data-document-id"), this.getAttribute("data-title"));
        }
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
        openReanalyzeOptionsModal(docId, filename, btn);
      });
    });

    tbody.querySelectorAll(".admin-retry-stage-link").forEach(function (link) {
      link.addEventListener("click", function (e) {
        e.preventDefault();
        var materialId = this.getAttribute("data-material-id");
        var stage = this.getAttribute("data-stage");
        if (!materialId || !stage) return;
        if (!confirm("ステージ「" + stage + "」を再実行します。既存の結果は上書きされます。よろしいですか？")) return;
        runMaterialPipeline(materialId, stage);
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
        if (window.LectureStudio) { window.LectureStudio.openExportModal(state.exportContext); }
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

  // ── 画像読み取りパイプライン（migration 041）— 再解析オプション ─────────
  // 「解析再開」ボタンのフローにもアップロード時と同じチェックボックスを出す。
  function openReanalyzeOptionsModal(docId, filename, triggerBtn) {
    var existing = document.getElementById("reanalyze-options-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "reanalyze-options-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:360px;max-width:440px">' +
        '<h3 style="margin:0 0 12px;font-size:15px;color:var(--color-text-primary)">解析を再開</h3>' +
        '<p style="font-size:12.5px;color:var(--color-text-secondary);margin:0 0 12px">「' + escHtml(filename) + '」の解析を再開します。</p>' +
        '<label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--color-text-secondary);margin-bottom:16px">' +
          '<input type="checkbox" id="reanalyze-analyze-images">' +
          '図面・画像を解析する（装置図の同定に vision AI を使用）' +
        '</label>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button id="reanalyze-cancel-btn" class="admin-action-btn">キャンセル</button>' +
          '<button id="reanalyze-confirm-btn" class="admin-action-btn" style="background:var(--color-text-success);color:#fff">再開する</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("reanalyze-cancel-btn").addEventListener("click", function () { overlay.remove(); });
    document.getElementById("reanalyze-confirm-btn").addEventListener("click", function () {
      var analyzeImages = document.getElementById("reanalyze-analyze-images").checked;
      overlay.remove();
      performReanalyze(docId, filename, triggerBtn, analyzeImages);
    });
  }

  function performReanalyze(docId, filename, btn, analyzeImages) {
    if (btn) { btn.disabled = true; btn.textContent = "再開中..."; }
    apiFetch("/admin/documents/" + docId + "/reanalyze", {
      method: "POST",
      body: JSON.stringify({ analyze_images: !!analyzeImages }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (btn) btn.textContent = "処理中";
        if (data.task_id) startTaskPolling(data.task_id, filename);
        loadMaterials();
      })
      .catch(function () {
        if (btn) { btn.disabled = false; btn.textContent = "解析再開"; }
        showUploadStatus("解析の再開に失敗しました。", "error");
      });
  }

  // ── 図・画像（document_figures, migration 041） ─────────────────────
  var _figuresModalState = { documentId: null, title: "", figures: [], objectUrls: [] };

  function _figuresRevokeObjectUrls() {
    _figuresModalState.objectUrls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (e) { /* noop */ } });
    _figuresModalState.objectUrls = [];
  }

  function openFiguresModal(documentId, title) {
    _figuresRevokeObjectUrls();
    _figuresModalState.documentId = documentId;
    _figuresModalState.title = title || "";
    _figuresModalState.figures = [];

    var existing = document.getElementById("figures-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "figures-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:520px;max-width:760px;max-height:82vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">図・画像 — ' + escHtml(title || "") + '</h3>' +
          '<button id="figures-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          'PDFから抽出された図です。装置候補はAIによる推定であり、常に教員の確認が必要です（確定はライブラリへの昇格操作でのみ行われます）。' +
        '</p>' +
        '<div id="figures-modal-body" style="overflow-y:auto;flex:1">' +
          '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) { _figuresRevokeObjectUrls(); overlay.remove(); } });
    document.getElementById("figures-modal-close").addEventListener("click", function () { _figuresRevokeObjectUrls(); overlay.remove(); });

    loadFigures(documentId);
  }

  function loadFigures(documentId) {
    apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/figures")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        _figuresModalState.figures = (data && data.figures) || [];
        renderFiguresList();
      })
      .catch(function () {
        var body = document.getElementById("figures-modal-body");
        if (body) body.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">図の読み込みに失敗しました</div>';
      });
  }

  function figureExtractionBadge(method) {
    if (method === "embedded") {
      return '<span class="admin-status" style="background:var(--color-background-info);color:var(--color-text-info)">埋込画像</span>';
    }
    if (method === "region_render") {
      return '<span class="admin-status" style="background:var(--color-background-tertiary);color:var(--color-text-secondary)">領域推定</span>';
    }
    return "";
  }

  // match_status バッジ。confidence の生数値は出さず、段階バッジ + 固定の「要確認」注記のみ（review_status を確定表示しない）。
  function figureMatchBadge(matchStatus) {
    var label = { matched: "一致候補", novel: "新規候補", unknown: "不明" }[matchStatus] || "不明";
    var bg = "var(--color-background-tertiary)", fg = "var(--color-text-tertiary)";
    if (matchStatus === "matched") { bg = "var(--color-background-success)"; fg = "var(--color-text-success)"; }
    else if (matchStatus === "novel") { bg = "var(--color-background-info)"; fg = "var(--color-text-info)"; }
    return '<span class="admin-status" style="background:' + bg + ';color:' + fg + '">' + label + '</span>' +
      '<span style="font-size:11px;color:var(--color-text-tertiary);margin-left:4px">（要確認）</span>';
  }

  function renderFiguresList() {
    var body = document.getElementById("figures-modal-body");
    if (!body) return;
    var figures = _figuresModalState.figures;
    if (!figures.length) {
      body.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">抽出された図はありません</div>';
      return;
    }
    var html = "";
    figures.forEach(function (fig, idx) {
      html += '<div class="figure-card" data-figure-id="' + escHtml(fig.id) + '" style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--color-border-tertiary)">' +
        '<div style="width:120px;height:120px;flex:0 0 120px;background:var(--color-background-secondary);border-radius:4px;display:flex;align-items:center;justify-content:center;overflow:hidden">' +
          '<img class="figure-thumb" data-figure-idx="' + idx + '" style="max-width:100%;max-height:100%;display:none" alt="">' +
          '<span class="figure-thumb-placeholder" style="font-size:11px;color:var(--color-text-tertiary)">読込中...</span>' +
        '</div>' +
        '<div style="flex:1;min-width:0">' +
          '<div style="font-size:13px;font-weight:600;color:var(--color-text-primary)">' + escHtml(fig.figure_label || fig.figure_key || "図") +
            (fig.page ? ' <span style="font-weight:400;color:var(--color-text-tertiary);font-size:11px">p.' + escHtml(fig.page) + '</span>' : "") +
          '</div>' +
          '<div style="font-size:12px;color:var(--color-text-secondary);margin:4px 0">' + escHtml(fig.caption_text || "(captionなし)") + '</div>' +
          '<div style="margin-bottom:6px">' + figureExtractionBadge(fig.extraction_method) +
            ' <button class="admin-action-btn figure-deliberate-btn" type="button" data-figure-id="' + escHtml(fig.id) + '" style="font-size:11px;padding:2px 8px;margin-left:4px">深く検討</button>' +
          '</div>' +
          _renderApparatusCandidates(fig) +
        '</div>' +
      '</div>';
    });
    body.innerHTML = html;

    // 画像はサムネイルごとに認証付きで遅延取得する（apiFetchRaw → blob → objectURL。PDFプレビューと同じ流儀）。
    figures.forEach(function (fig, idx) { loadFigureThumbnail(fig, idx); });

    body.querySelectorAll(".figure-promote-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var figureId = this.getAttribute("data-figure-id");
        var fig = figures.filter(function (f) { return f.id === figureId; })[0];
        var candIdx = parseInt(this.getAttribute("data-candidate-idx"), 10);
        var candidate = fig && fig.apparatus_candidates && fig.apparatus_candidates[candIdx];
        openLibraryEntryModal({
          documentId: _figuresModalState.documentId,
          figureId: figureId,
          candidate: candidate || null,
        });
      });
    });

    // Phase 2（装置図理解機能の拡張）— 図上でパーツ・図中ラベルの位置を確認するオーバーレイ。
    body.querySelectorAll(".figure-overlay-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var figureId = this.getAttribute("data-figure-id");
        var fig = figures.filter(function (f) { return f.id === figureId; })[0];
        var candIdx = parseInt(this.getAttribute("data-candidate-idx"), 10);
        if (fig) openApparatusOverlayModal(fig, candIdx);
      });
    });

    // W層（要素検討ワークスペース, Phase 0）— 図要素の「深く検討」導線。
    body.querySelectorAll(".figure-deliberate-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var figureId = this.getAttribute("data-figure-id");
        var fig = figures.filter(function (f) { return f.id === figureId; })[0];
        if (window.Deliberation) {
          window.Deliberation.openElement("figure", figureId, {
            documentId: _figuresModalState.documentId,
            title: (fig && (fig.figure_label || fig.figure_key)) || "",
          });
        }
      });
    });
  }

  function _renderApparatusCandidates(fig) {
    var candidates = fig.apparatus_candidates || [];
    if (!candidates.length) return "";
    var html = '<div style="display:flex;flex-direction:column;gap:6px;margin-top:4px">';
    candidates.forEach(function (c, i) {
      var parts = (c.parts || []).map(function (p) {
        var namePart = p.expanded_name ? (p.name + " = " + p.expanded_name) : p.name;
        return escHtml(namePart) + (p.role ? "（" + escHtml(p.role) + "）" : "");
      }).join("、");
      // 「図で確認」導線: 図の bbox があり、パーツの一部に bbox がある、または
      // 図中ラベルが取得できている場合のみオーバーレイ表示が可能。
      var hasPartBbox = (c.parts || []).some(function (p) { return !!p.bbox; });
      var canOverlay = !!fig.bbox && (hasPartBbox || (fig.inner_labels && fig.inner_labels.length > 0));
      html += '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:var(--color-background-secondary);border-radius:4px;padding:6px 8px">' +
        '<span style="font-size:12.5px;font-weight:600;color:var(--color-text-primary)">' + escHtml(c.apparatus_name_candidate || "(未同定)") + '</span>' +
        figureMatchBadge(c.match_status) +
        (parts ? '<span style="font-size:11.5px;color:var(--color-text-tertiary)">パーツ: ' + parts + '</span>' : "") +
        (canOverlay
          ? '<button class="admin-action-btn figure-overlay-btn" type="button" data-figure-id="' + escHtml(fig.id) + '" data-candidate-idx="' + i + '" style="font-size:11.5px;padding:2px 8px">図で確認</button>'
          : "") +
        '<span style="flex:1"></span>' +
        '<button class="admin-action-btn figure-promote-btn" data-figure-id="' + escHtml(fig.id) + '" data-candidate-idx="' + i + '" style="font-size:11.5px;padding:2px 8px">ライブラリへ昇格</button>' +
      '</div>';
    });
    html += '</div>';
    return html;
  }

  function loadFigureThumbnail(fig, idx) {
    var img = document.querySelector('.figure-thumb[data-figure-idx="' + idx + '"]');
    if (!img) return;
    // image_url が返っていればそれを使う（"/api" プレフィックス付きでも剥がして apiFetchRaw と整合させる）。
    // 無ければ契約上のパスを組み立てる。
    var path = fig.image_url || ("/admin/documents/" + encodeURIComponent(_figuresModalState.documentId) + "/figures/" + encodeURIComponent(fig.id) + "/image");
    if (path.indexOf("/api/") === 0) path = path.substring(4);
    apiFetchRaw(path, { _noJson: true })
      .then(function (res) {
        if (!res.ok) throw new Error("image load failed");
        return res.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        _figuresModalState.objectUrls.push(url);
        img.src = url;
        img.style.display = "";
        var placeholder = img.parentNode.querySelector(".figure-thumb-placeholder");
        if (placeholder) placeholder.remove();
      })
      .catch(function () {
        var placeholder = img.parentNode.querySelector(".figure-thumb-placeholder");
        if (placeholder) placeholder.textContent = "表示できません";
      });
  }

  // ── 装置候補オーバーレイ（Phase 2, 装置図理解機能の拡張・レビューUX） ─────────
  // figures-modal の上に重ねる第2モーダル。装置候補は常に review_required 系の
  // candidate であり、ここでは位置の確認のみを行う（確定操作は既存のライブラリ
  // 昇格導線に委ねる。candidate-only 原則）。
  var _apparatusOverlayState = { objectUrls: [], requestId: 0 };

  function _apparatusOverlayRevokeObjectUrls() {
    _apparatusOverlayState.objectUrls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (e) { /* noop */ } });
    _apparatusOverlayState.objectUrls = [];
  }

  function _apparatusOverlayClose(overlay) {
    // Invalidate an image request that may still be resolving. Without this,
    // a slower previous request can paint its image into a newly opened modal.
    _apparatusOverlayState.requestId += 1;
    _apparatusOverlayRevokeObjectUrls();
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
  }

  // ページ座標系(pt)の bbox を、図領域 fb（同じくページ座標系）に対する相対 % に変換する。
  // region_render / embedded どちらの抽出方式でも同じ変換でよい（正規化のため）。
  function _figureRelativeRect(fb, b) {
    if (!fb || !b || fb.length < 4 || b.length < 4) return null;
    var w = fb[2] - fb[0];
    var h = fb[3] - fb[1];
    if (w <= 0 || h <= 0) return null;
    var clamp = function (v) { return Math.max(0, Math.min(100, v)); };
    var left = clamp((b[0] - fb[0]) / w * 100);
    var top = clamp((b[1] - fb[1]) / h * 100);
    var right = clamp((b[2] - fb[0]) / w * 100);
    var bottom = clamp((b[3] - fb[1]) / h * 100);
    var width = right - left;
    var height = bottom - top;
    if (width <= 0 || height <= 0) return null;
    return { left: left, top: top, width: width, height: height };
  }

  function _apparatusOverlayClearSelection(wrap) {
    wrap.querySelectorAll(".apparatus-overlay-box").forEach(function (b) {
      var isUnmatched = b.className.indexOf("apparatus-overlay-box-unmatched") !== -1;
      b.style.borderColor = isUnmatched ? "var(--color-text-tertiary)" : "var(--color-text-info)";
      b.style.borderWidth = "2px";
    });
  }

  function _apparatusOverlaySelectPart(wrap, box, p) {
    _apparatusOverlayClearSelection(wrap);
    box.style.borderColor = "var(--color-text-danger)";
    box.style.borderWidth = "3px";
    var detail = document.getElementById("apparatus-overlay-detail");
    if (!detail) return;
    var roleText = p.role ? escHtml(p.role) : "本文根拠なし（画像からの推定のみ）";
    var evidenceText = p.evidence_quote ? escHtml(p.evidence_quote) : "本文根拠なし（画像からの推定のみ）";
    var html = '<div style="font-weight:600;color:var(--color-text-primary);margin-bottom:2px">' +
      escHtml(p.name || "") + (p.expanded_name ? " = " + escHtml(p.expanded_name) : "") + '</div>';
    if (p.label_ref) {
      html += '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:2px">図中ラベル: ' + escHtml(p.label_ref) + '</div>';
    }
    html += '<div style="margin-bottom:2px"><span style="color:var(--color-text-tertiary)">役割: </span>' + roleText + '</div>';
    html += '<div style="margin-bottom:2px"><span style="color:var(--color-text-tertiary)">根拠: </span>' + evidenceText + '</div>';
    if (p.reason) {
      html += '<div style="font-size:11.5px;color:var(--color-text-tertiary)">推定理由: ' + escHtml(p.reason) + '</div>';
    }
    detail.innerHTML = html;
  }

  function _apparatusOverlaySelectLabel(wrap, box, text) {
    _apparatusOverlayClearSelection(wrap);
    box.style.borderColor = "var(--color-text-danger)";
    box.style.borderWidth = "3px";
    var detail = document.getElementById("apparatus-overlay-detail");
    if (!detail) return;
    detail.innerHTML = '<div>未対応の図中ラベル: ' + escHtml(text || "") + '</div>';
  }

  function _renderApparatusOverlayBoxes(figBbox, parts, innerLabels) {
    var wrap = document.getElementById("apparatus-overlay-imgwrap");
    if (!wrap) return;
    wrap.querySelectorAll(".apparatus-overlay-box").forEach(function (b) { b.remove(); });

    // label_ref に一致する（完全一致・大文字小文字無視）図中ラベルは part 側の
    // ボックスでカバーされるため、未対応ラベルの薄色ボックスからは除外する。
    var referencedLabels = {};
    (parts || []).forEach(function (p) {
      if (p.label_ref) referencedLabels[String(p.label_ref).toLowerCase()] = true;
    });

    (parts || []).forEach(function (p) {
      if (!p.bbox) return;
      var rect = _figureRelativeRect(figBbox, p.bbox);
      if (!rect) return;
      var box = document.createElement("div");
      box.className = "apparatus-overlay-box";
      box.title = p.name || "";
      box.style.cssText = "position:absolute;box-sizing:border-box;cursor:pointer;" +
        "left:" + rect.left + "%;top:" + rect.top + "%;width:" + rect.width + "%;height:" + rect.height + "%;" +
        "border:2px solid var(--color-text-info);background:rgba(55,138,221,0.08)";
      box.addEventListener("click", function (e) {
        e.stopPropagation();
        _apparatusOverlaySelectPart(wrap, box, p);
      });
      wrap.appendChild(box);
    });

    (innerLabels || []).forEach(function (lbl) {
      if (!lbl || !lbl.bbox) return;
      var text = lbl.text || "";
      if (text && referencedLabels[String(text).toLowerCase()]) return;
      var rect = _figureRelativeRect(figBbox, lbl.bbox);
      if (!rect) return;
      var box = document.createElement("div");
      box.className = "apparatus-overlay-box apparatus-overlay-box-unmatched";
      box.title = text;
      box.style.cssText = "position:absolute;box-sizing:border-box;cursor:pointer;" +
        "left:" + rect.left + "%;top:" + rect.top + "%;width:" + rect.width + "%;height:" + rect.height + "%;" +
        "border:2px dashed var(--color-text-tertiary);background:rgba(174,174,178,0.08)";
      box.addEventListener("click", function (e) {
        e.stopPropagation();
        _apparatusOverlaySelectLabel(wrap, box, text);
      });
      wrap.appendChild(box);
    });
  }

  function _apparatusOverlayPartsTableHtml(parts) {
    if (!parts.length) {
      return '<tr><td colspan="5" style="padding:6px;color:var(--color-text-tertiary);font-size:12px">パーツ候補はありません</td></tr>';
    }
    return parts.map(function (p) {
      return '<tr style="border-bottom:1px solid var(--color-border-tertiary)">' +
        '<td style="padding:4px 6px;font-size:12px;color:var(--color-text-secondary)">' + escHtml(p.label_ref || "—") + '</td>' +
        '<td style="padding:4px 6px;font-size:12px;color:var(--color-text-primary)">' + escHtml(p.name || "") + '</td>' +
        '<td style="padding:4px 6px;font-size:12px;color:var(--color-text-primary)">' + escHtml(p.expanded_name || "") + '</td>' +
        '<td style="padding:4px 6px;font-size:12px;color:var(--color-text-secondary)">' + escHtml(p.role || "本文根拠なし（画像からの推定のみ）") + '</td>' +
        '<td style="padding:4px 6px;font-size:11.5px;color:var(--color-text-tertiary)">' + escHtml(p.evidence_quote || "本文根拠なし（画像からの推定のみ）") + '</td>' +
      '</tr>';
    }).join("");
  }

  // fig: figures 一覧中の1件。candidateIdx: fig.apparatus_candidates のインデックス。
  function openApparatusOverlayModal(fig, candidateIdx) {
    _apparatusOverlayRevokeObjectUrls();
    var requestId = ++_apparatusOverlayState.requestId;

    var candidate = (fig.apparatus_candidates && fig.apparatus_candidates[candidateIdx]) || null;
    var parts = (candidate && candidate.parts) || [];
    var figBbox = fig.bbox || null;
    var innerLabels = fig.inner_labels || [];
    var figLabel = fig.figure_label || fig.figure_key || "図";
    var nameCandidate = (candidate && candidate.apparatus_name_candidate) || "(未同定)";

    var existing = document.getElementById("apparatus-overlay-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "apparatus-overlay-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;z-index:10000";

    var imgWrapHtml =
      '<div id="apparatus-overlay-imgwrap" style="position:relative;background:var(--color-background-secondary);border-radius:4px;overflow:hidden;min-height:220px;display:flex;align-items:center;justify-content:center">' +
        '<img id="apparatus-overlay-img" style="display:none;width:100%;height:auto" alt="">' +
        '<span id="apparatus-overlay-img-placeholder" style="font-size:12px;color:var(--color-text-tertiary)">読込中...</span>' +
      '</div>';

    var bodyHtml;
    if (figBbox) {
      bodyHtml = imgWrapHtml;
    } else {
      bodyHtml = imgWrapHtml +
        '<p style="font-size:11.5px;color:var(--color-text-warning);margin:8px 0">位置情報なし（bbox 未取得） — 図内の位置と対応付けできないため、一覧表示のみです。</p>' +
        '<div style="overflow-x:auto">' +
          '<table style="width:100%;border-collapse:collapse">' +
            '<thead><tr style="text-align:left;border-bottom:1px solid var(--color-border-tertiary)">' +
              '<th style="padding:4px 6px;font-size:11.5px;color:var(--color-text-tertiary)">ラベル</th>' +
              '<th style="padding:4px 6px;font-size:11.5px;color:var(--color-text-tertiary)">名称</th>' +
              '<th style="padding:4px 6px;font-size:11.5px;color:var(--color-text-tertiary)">展開名</th>' +
              '<th style="padding:4px 6px;font-size:11.5px;color:var(--color-text-tertiary)">役割</th>' +
              '<th style="padding:4px 6px;font-size:11.5px;color:var(--color-text-tertiary)">根拠</th>' +
            '</tr></thead>' +
            '<tbody>' + _apparatusOverlayPartsTableHtml(parts) + '</tbody>' +
          '</table>' +
        '</div>';
    }

    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:20px;min-width:560px;max-width:820px;max-height:88vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">' +
          '<h3 style="margin:0;font-size:15px;color:var(--color-text-primary)">' + escHtml(figLabel) + ' — ' + escHtml(nameCandidate) + '</h3>' +
          '<button id="apparatus-overlay-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 10px">装置候補（レビュー待ち） — AIによる推定であり、確定はライブラリへの昇格操作でのみ行われます。</p>' +
        '<div style="overflow-y:auto;flex:1">' + bodyHtml + '</div>' +
        '<div id="apparatus-overlay-detail" style="margin-top:10px;border-top:1px solid var(--color-border-tertiary);padding-top:8px;font-size:12.5px;color:var(--color-text-secondary);min-height:20px">クリックしてパーツや図中ラベルの詳細を確認できます。</div>' +
      '</div>';

    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) _apparatusOverlayClose(overlay); });
    overlay.querySelector("#apparatus-overlay-close").addEventListener("click", function () { _apparatusOverlayClose(overlay); });

    // 画像は figures-modal のサムネイルと同じ流儀（apiFetchRaw → blob → objectURL）で取得する。
    var path = fig.image_url || "";
    if (path.indexOf("/api/") === 0) path = path.substring(4);
    apiFetchRaw(path, { _noJson: true })
      .then(function (res) {
        if (!res.ok) throw new Error("image load failed");
        return res.blob();
      })
      .then(function (blob) {
        if (requestId !== _apparatusOverlayState.requestId || !overlay.parentNode) return;
        var url = URL.createObjectURL(blob);
        _apparatusOverlayState.objectUrls.push(url);
        var img = overlay.querySelector("#apparatus-overlay-img");
        if (!img) return;
        var wrap = overlay.querySelector("#apparatus-overlay-imgwrap");
        if (wrap) {
          // The positioning container must match the rendered image box.
          // The loading min-height/flex layout would offset percentage bboxes.
          wrap.style.minHeight = "0";
          wrap.style.display = "block";
        }
        img.style.display = "block";
        var placeholder = overlay.querySelector("#apparatus-overlay-img-placeholder");
        if (placeholder) placeholder.remove();
        if (figBbox) {
          img.addEventListener("load", function () {
            if (requestId !== _apparatusOverlayState.requestId || !overlay.parentNode) return;
            _renderApparatusOverlayBoxes(figBbox, parts, innerLabels);
          });
        }
        img.src = url;
      })
      .catch(function () {
        if (requestId !== _apparatusOverlayState.requestId || !overlay.parentNode) return;
        var placeholder = overlay.querySelector("#apparatus-overlay-img-placeholder");
        if (placeholder) placeholder.textContent = "画像を表示できません";
      });
  }

  // ── ナレッジライブラリ（L層, migration 042）— 昇格 / 新規作成モーダル ──
  // ライブラリへの書き込みは常に人間の操作のみ（LLMが自動昇格する経路は作らない）。
  var _libEntryModalCtx = { documentId: null, figureId: null, candidate: null };
  var _libSimilarDebounceTimer = null;

  function _libraryPartsRowHtml(name, role) {
    return '<div class="lib-part-row" style="display:flex;gap:6px;margin-bottom:4px">' +
      '<input type="text" class="lib-part-name" placeholder="パーツ名" value="' + escHtml(name || "") + '" style="flex:1;padding:3px 6px;font-size:12.5px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary)">' +
      '<input type="text" class="lib-part-role" placeholder="役割" value="' + escHtml(role || "") + '" style="flex:1;padding:3px 6px;font-size:12.5px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary)">' +
      '<button type="button" class="lib-part-remove" style="background:none;border:none;color:var(--color-text-danger);cursor:pointer;font-size:13px">&times;</button>' +
    '</div>';
  }

  function _libraryReadPartsRows(containerId) {
    var rows = document.querySelectorAll("#" + containerId + " .lib-part-row");
    var out = [];
    rows.forEach(function (row) {
      var name = row.querySelector(".lib-part-name").value.trim();
      var role = row.querySelector(".lib-part-role").value.trim();
      if (name) out.push({ name: name, role: role });
    });
    return out;
  }

  function _apparatusBodyFromCandidate(candidate) {
    var parts = (candidate && candidate.parts) || [];
    return {
      typical_parts: parts.map(function (p) { return { name: p.name || "", role: p.role || "" }; }),
      visual_cues: "",
      typical_configurations: "",
      measurement_targets: "",
    };
  }

  // opts: { documentId, figureId, candidate } — 図からの昇格（figureId 付き）と
  // 白紙からの新規作成（すべて null）を同じフォームで扱う（§6-4 の3経路のうち a と c）。
  function openLibraryEntryModal(opts) {
    opts = opts || {};
    _libEntryModalCtx = {
      documentId: opts.documentId || null,
      figureId: opts.figureId || null,
      candidate: opts.candidate || null,
    };

    var existing = document.getElementById("library-entry-modal");
    if (existing) existing.remove();

    var isPromote = !!(opts.candidate || opts.figureId);
    var defaultName = (opts.candidate && opts.candidate.apparatus_name_candidate) || "";
    var defaultBody = _apparatusBodyFromCandidate(opts.candidate);
    var canOfferImage = !!(opts.figureId && opts.documentId);

    var overlay = document.createElement("div");
    overlay.id = "library-entry-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:10000";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:460px;max-width:600px;max-height:86vh;overflow-y:auto">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">' + (isPromote ? "ライブラリへ昇格" : "ライブラリに新規エントリを作成") + '</h3>' +
          '<button id="library-entry-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        (isPromote ? '<p style="font-size:12px;color:var(--color-text-warning);background:#fff6e6;border:1px solid var(--color-text-warning);border-radius:4px;padding:6px 8px;margin:0 0 12px">AIの候補をそのまま登録せず、自分の言葉で書き直すことを推奨します。</p>' : '') +
        '<div id="library-entry-status" class="upload-status" style="display:none;margin-bottom:10px"></div>' +

        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">分野 (domain_key)</label>' +
        '<input type="text" id="lib-entry-domain" placeholder="例: particle_physics" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box">' +

        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">種別</label>' +
        '<select id="lib-entry-type" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px">' +
          '<option value="apparatus" selected>装置 (apparatus)</option>' +
          '<option value="theory_component">理論コンポーネント</option>' +
        '</select>' +

        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">名称</label>' +
        '<input type="text" id="lib-entry-name" value="' + escHtml(defaultName) + '" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box">' +

        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">別名 (カンマ区切り・任意)</label>' +
        '<input type="text" id="lib-entry-aliases" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box">' +

        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">要約</label>' +
        '<textarea id="lib-entry-summary" rows="2" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box"></textarea>' +

        '<div id="lib-entry-body-apparatus">' +
          '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">見た目の特徴 (visual_cues)</label>' +
          '<textarea id="lib-entry-visual-cues" rows="2" placeholder="例: 円筒形で放射状にフランジ" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box"></textarea>' +

          '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:4px">典型的な構成パーツ</label>' +
          '<div id="lib-entry-parts-list" style="margin-bottom:6px">' +
            (defaultBody.typical_parts.length ? defaultBody.typical_parts.map(function (p) { return _libraryPartsRowHtml(p.name, p.role); }).join("") : _libraryPartsRowHtml("", "")) +
          '</div>' +
          '<button type="button" id="lib-entry-parts-add" class="admin-action-btn" style="font-size:11.5px;padding:2px 8px;margin-bottom:10px">+ パーツを追加</button>' +

          '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">典型的な構成・用途 (任意)</label>' +
          '<textarea id="lib-entry-configs" rows="2" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box"></textarea>' +

          '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">計測対象 (任意)</label>' +
          '<textarea id="lib-entry-measurement" rows="2" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box"></textarea>' +
        '</div>' +

        '<div id="lib-entry-body-theory" style="display:none">' +
          '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">body (JSON、任意)</label>' +
          '<textarea id="lib-entry-body-json" rows="4" placeholder="{}" style="width:100%;padding:5px 8px;font-size:12px;font-family:monospace;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box"></textarea>' +
        '</div>' +

        (canOfferImage
          ? ('<label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--color-text-secondary);margin-bottom:4px">' +
              '<input type="checkbox" id="lib-entry-include-image"> 例示画像を含める（元教材の所有者のみ有効）' +
            '</label>' +
            '<div id="lib-entry-image-warning" style="display:none;font-size:11.5px;color:var(--color-text-warning);background:#fff6e6;border:1px solid var(--color-text-warning);border-radius:4px;padding:5px 7px;margin-bottom:10px">この画像はライブラリを閲覧できる全教員に表示されます。元教材の所有者以外がこの操作を行うと拒否されます。</div>')
          : "") +

        '<div style="border-top:1px solid var(--color-border-tertiary);padding-top:10px;margin-top:4px">' +
          '<label style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:4px">' +
            '<input type="radio" name="lib-entry-merge-target" value="" checked> 新規作成' +
          '</label>' +
          '<div id="lib-entry-similar-area" style="margin-bottom:8px;padding-left:22px"></div>' +
        '</div>' +

        '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px">' +
          '<button id="lib-entry-cancel" class="admin-action-btn">キャンセル</button>' +
          '<button id="lib-entry-submit" class="admin-action-btn" style="background:var(--color-text-success);color:#fff">登録する</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    if (opts.candidate && opts.candidate.reason) {
      document.getElementById("lib-entry-summary").value = opts.candidate.reason;
    }

    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("library-entry-close").addEventListener("click", function () { overlay.remove(); });
    document.getElementById("lib-entry-cancel").addEventListener("click", function () { overlay.remove(); });

    document.getElementById("lib-entry-type").addEventListener("change", function () {
      var isApparatus = this.value === "apparatus";
      document.getElementById("lib-entry-body-apparatus").style.display = isApparatus ? "" : "none";
      document.getElementById("lib-entry-body-theory").style.display = isApparatus ? "none" : "";
    });

    document.getElementById("lib-entry-parts-list").addEventListener("click", function (e) {
      var btn = e.target.closest(".lib-part-remove");
      if (!btn) return;
      var row = btn.closest(".lib-part-row");
      if (row) row.remove();
    });
    document.getElementById("lib-entry-parts-add").addEventListener("click", function () {
      document.getElementById("lib-entry-parts-list").insertAdjacentHTML("beforeend", _libraryPartsRowHtml("", ""));
    });

    document.getElementById("lib-entry-name").addEventListener("input", _libraryTriggerSimilarSearch);
    document.getElementById("lib-entry-domain").addEventListener("input", _libraryTriggerSimilarSearch);

    if (canOfferImage) {
      document.getElementById("lib-entry-include-image").addEventListener("change", function () {
        document.getElementById("lib-entry-image-warning").style.display = this.checked ? "" : "none";
      });
    }

    document.getElementById("lib-entry-submit").addEventListener("click", submitLibraryEntry);
  }

  function _libraryTriggerSimilarSearch() {
    if (_libSimilarDebounceTimer) clearTimeout(_libSimilarDebounceTimer);
    _libSimilarDebounceTimer = setTimeout(_libraryRunSimilarSearch, 400);
  }

  function _libraryRunSimilarSearch() {
    var domainEl = document.getElementById("lib-entry-domain");
    var nameEl = document.getElementById("lib-entry-name");
    var area = document.getElementById("lib-entry-similar-area");
    if (!domainEl || !nameEl || !area) return;
    var domainKey = domainEl.value.trim();
    var entryType = document.getElementById("lib-entry-type").value;
    var name = nameEl.value.trim();
    if (!domainKey || name.length < 2) { area.innerHTML = ""; return; }

    apiFetch("/admin/library/entries/similar", {
      method: "POST",
      body: JSON.stringify({ domain_key: domainKey, entry_type: entryType, text: name, top_k: 5 }),
    })
      .then(function (res) { return res.ok ? res.json() : { entries: [] }; })
      .then(function (data) { _renderLibrarySimilarResults((data && data.entries) || []); })
      .catch(function () { /* 類似検索の失敗は致命的ではないので無視する */ });
  }

  function _renderLibrarySimilarResults(entries) {
    var area = document.getElementById("lib-entry-similar-area");
    if (!area) return;
    if (!entries.length) {
      area.innerHTML = '<div style="font-size:11.5px;color:var(--color-text-tertiary)">類似する既存エントリは見つかりませんでした</div>';
      return;
    }
    var html = '<div style="font-size:11.5px;color:var(--color-text-secondary);margin-bottom:4px">類似する既存エントリが見つかりました。統合する場合は選択してください:</div>';
    entries.forEach(function (e) {
      html += '<label style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:2px">' +
        '<input type="radio" name="lib-entry-merge-target" value="' + escHtml(e.id) + '">' +
        escHtml(e.name) + (e.summary ? ' <span style="color:var(--color-text-tertiary)">— ' + escHtml(e.summary.slice(0, 40)) + '</span>' : '') +
      '</label>';
    });
    area.innerHTML = html;
  }

  function _libraryEntryStatus(msg, kind) {
    var el = document.getElementById("library-entry-status");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (kind || "info");
  }

  function submitLibraryEntry() {
    var domainKey = document.getElementById("lib-entry-domain").value.trim();
    var entryType = document.getElementById("lib-entry-type").value;
    var name = document.getElementById("lib-entry-name").value.trim();
    var aliasesRaw = document.getElementById("lib-entry-aliases").value.trim();
    var summary = document.getElementById("lib-entry-summary").value.trim();

    if (!domainKey) { _libraryEntryStatus("分野 (domain_key) を入力してください", "error"); return; }
    if (!name) { _libraryEntryStatus("名称を入力してください", "error"); return; }

    var aliases = aliasesRaw ? aliasesRaw.split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];

    var body;
    if (entryType === "apparatus") {
      body = {
        visual_cues: document.getElementById("lib-entry-visual-cues").value.trim(),
        typical_parts: _libraryReadPartsRows("lib-entry-parts-list"),
        typical_configurations: document.getElementById("lib-entry-configs").value.trim(),
        measurement_targets: document.getElementById("lib-entry-measurement").value.trim(),
      };
    } else {
      var raw = document.getElementById("lib-entry-body-json").value.trim();
      try {
        body = raw ? JSON.parse(raw) : {};
      } catch (e) {
        _libraryEntryStatus("body の JSON が不正です", "error");
        return;
      }
    }

    var includeImageEl = document.getElementById("lib-entry-include-image");
    var includeImage = !!(includeImageEl && includeImageEl.checked);
    var mergeTargetEl = document.querySelector('input[name="lib-entry-merge-target"]:checked');
    var mergeTargetId = mergeTargetEl && mergeTargetEl.value ? mergeTargetEl.value : null;

    var submitBtn = document.getElementById("lib-entry-submit");
    submitBtn.disabled = true;
    _libraryEntryStatus(mergeTargetId ? "統合しています..." : "登録しています...", "info");

    if (mergeTargetId) {
      _mergeIntoLibraryEntry(mergeTargetId, domainKey);
      return;
    }

    var payload = {
      domain_key: domainKey,
      entry_type: entryType,
      name: name,
      aliases: aliases,
      summary: summary,
      body: body,
      source_component_ids: [],
      source_document_ids: _libEntryModalCtx.documentId ? [_libEntryModalCtx.documentId] : [],
      exemplar_images: (includeImage && _libEntryModalCtx.figureId)
        ? [{ figure_id: _libEntryModalCtx.figureId, source_document_id: _libEntryModalCtx.documentId }]
        : [],
    };

    apiFetch("/admin/library/entries", { method: "POST", body: JSON.stringify(payload) })
      .then(function (res) {
        if (res.status === 403) throw { forbidden: true };
        if (!res.ok) return res.json().then(function (d) { throw { message: (d && d.detail) || "登録に失敗しました" }; });
        return res.json();
      })
      .then(function () {
        _libraryEntryStatus("ライブラリに登録しました", "success");
        setTimeout(function () {
          var modal = document.getElementById("library-entry-modal");
          if (modal) modal.remove();
          _refreshLibraryAfterChange(domainKey);
        }, 700);
      })
      .catch(function (err) {
        submitBtn.disabled = false;
        if (err && err.forbidden) {
          _libraryEntryStatus("教材の所有者のみが画像を含められます", "error");
        } else {
          _libraryEntryStatus((err && err.message) || "登録に失敗しました", "error");
        }
      });
  }

  // 既存エントリへの統合 = provenance 追記のみ（本文はそのユーザーが編集した内容では上書きしない）。
  function _mergeIntoLibraryEntry(entryId, domainKey) {
    var submitBtn = document.getElementById("lib-entry-submit");
    apiFetch("/admin/library/entries/" + encodeURIComponent(entryId))
      .then(function (res) {
        if (!res.ok) throw { message: "統合先エントリの取得に失敗しました" };
        return res.json();
      })
      .then(function (entry) {
        var existingDocIds = (entry.source_document_ids || []).slice();
        if (_libEntryModalCtx.documentId && existingDocIds.indexOf(_libEntryModalCtx.documentId) === -1) {
          existingDocIds.push(_libEntryModalCtx.documentId);
        }
        return apiFetch("/admin/library/entries/" + encodeURIComponent(entryId), {
          method: "PUT",
          body: JSON.stringify({
            expected_revision: entry.revision,
            source_document_ids: existingDocIds,
          }),
        });
      })
      .then(function (res) {
        if (res.status === 409) throw { conflict: true };
        if (!res.ok) return res.json().then(function (d) { throw { message: (d && d.detail) || "統合に失敗しました" }; });
        return res.json();
      })
      .then(function () {
        _libraryEntryStatus("既存エントリに統合しました（provenance を追記）", "success");
        setTimeout(function () {
          var modal = document.getElementById("library-entry-modal");
          if (modal) modal.remove();
          _refreshLibraryAfterChange(domainKey);
        }, 700);
      })
      .catch(function (err) {
        if (submitBtn) submitBtn.disabled = false;
        if (err && err.conflict) {
          _libraryEntryStatus("他の教員が更新しました。再読み込みしてください", "error");
        } else {
          _libraryEntryStatus((err && err.message) || "統合に失敗しました", "error");
        }
      });
  }

  function _refreshLibraryAfterChange(domainKey) {
    var panel = document.getElementById("tab-knowledge-library");
    if (!panel || !panel.classList.contains("vis")) return;
    loadLibraryDomains();
    if (domainKey) loadLibraryEntries();
  }

  // ── ナレッジライブラリタブ（一覧・編集・凍結・廃止・版履歴） ────────
  var _libraryTabState = { selectedDomain: "", entries: [] };
  var _libraryEntriesDebounceTimer = null;

  function initKnowledgeLibraryTab() {
    onTabActivate("knowledge-library", function () {
      loadLibraryDomains();
      loadLibraryEntries();
    });

    var newBtn = document.getElementById("library-new-entry-btn");
    if (newBtn) newBtn.addEventListener("click", function () {
      openLibraryEntryModal({ documentId: null, figureId: null, candidate: null });
    });

    var qEl = document.getElementById("library-search-q");
    if (qEl) qEl.addEventListener("input", function () {
      if (_libraryEntriesDebounceTimer) clearTimeout(_libraryEntriesDebounceTimer);
      _libraryEntriesDebounceTimer = setTimeout(function () { loadLibraryEntries(); }, 400);
    });
    var typeEl = document.getElementById("library-filter-type");
    if (typeEl) typeEl.addEventListener("change", function () { loadLibraryEntries(); });
    var retiredEl = document.getElementById("library-include-retired");
    if (retiredEl) retiredEl.addEventListener("change", function () { loadLibraryEntries(); });
  }

  function loadLibraryDomains() {
    var listEl = document.getElementById("library-domains-list");
    if (!listEl) return;
    apiFetch("/admin/library/domains")
      .then(function (res) { return res.ok ? res.json() : { domains: [] }; })
      .then(function (data) { renderLibraryDomainsList((data && data.domains) || []); })
      .catch(function () {
        listEl.innerHTML = '<div style="padding:8px;color:var(--color-text-danger);font-size:12.5px">分野の読み込みに失敗しました</div>';
      });
  }

  // ".admin-action-btn" に選択状態を示す専用CSSが無いため、選択中は背景色をインラインで
  // 切り替える（既存クラスの追加なしで視覚的な区別を出す）。
  function _libraryDomainButtonStyle(active) {
    var base = "text-align:left;display:flex;justify-content:space-between;gap:6px;width:100%";
    return active
      ? base + ";background:var(--color-background-info);color:var(--color-text-info);border-color:var(--color-border-info)"
      : base;
  }

  function renderLibraryDomainsList(domains) {
    var listEl = document.getElementById("library-domains-list");
    if (!listEl) return;
    var allActive = !_libraryTabState.selectedDomain;
    var html = '<button type="button" class="library-domain-item admin-action-btn" data-domain-key="" style="' + _libraryDomainButtonStyle(allActive) + '">すべての分野</button>';
    domains.forEach(function (d) {
      var active = _libraryTabState.selectedDomain === d.domain_key;
      html += '<button type="button" class="library-domain-item admin-action-btn" data-domain-key="' + escHtml(d.domain_key) + '" style="' + _libraryDomainButtonStyle(active) + '">' +
        '<span>' + escHtml(d.domain_key) + '</span>' +
        '<span style="color:var(--color-text-tertiary);font-size:11px">' + (d.entry_count || 0) + '件 / 凍結' + (d.frozen_count || 0) + '</span>' +
      '</button>';
    });
    listEl.innerHTML = html;
    listEl.querySelectorAll(".library-domain-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        _libraryTabState.selectedDomain = this.getAttribute("data-domain-key") || "";
        listEl.querySelectorAll(".library-domain-item").forEach(function (b) {
          b.setAttribute("style", _libraryDomainButtonStyle(false));
        });
        this.setAttribute("style", _libraryDomainButtonStyle(true));
        loadLibraryEntries();
      });
    });
  }

  function loadLibraryEntries() {
    var listEl = document.getElementById("library-entries-list");
    if (!listEl) return;
    listEl.innerHTML = '<div style="padding:8px;color:var(--color-text-tertiary);font-size:12.5px">読み込み中...</div>';
    var params = [];
    if (_libraryTabState.selectedDomain) params.push("domain_key=" + encodeURIComponent(_libraryTabState.selectedDomain));
    var typeEl = document.getElementById("library-filter-type");
    if (typeEl && typeEl.value) params.push("entry_type=" + encodeURIComponent(typeEl.value));
    var qEl = document.getElementById("library-search-q");
    if (qEl && qEl.value.trim()) params.push("q=" + encodeURIComponent(qEl.value.trim()));
    var retiredEl = document.getElementById("library-include-retired");
    if (retiredEl && retiredEl.checked) params.push("include_retired=true");
    var qs = params.length ? ("?" + params.join("&")) : "";

    apiFetch("/admin/library/entries" + qs)
      .then(function (res) { return res.ok ? res.json() : { entries: [] }; })
      .then(function (data) {
        _libraryTabState.entries = (data && data.entries) || [];
        renderLibraryEntriesList();
      })
      .catch(function () {
        listEl.innerHTML = '<div style="padding:8px;color:var(--color-text-danger);font-size:12.5px">読み込みに失敗しました</div>';
      });
  }

  function renderLibraryEntriesList() {
    var listEl = document.getElementById("library-entries-list");
    if (!listEl) return;
    var entries = _libraryTabState.entries;
    if (!entries.length) {
      listEl.innerHTML = '<div style="padding:8px;color:var(--color-text-tertiary);font-size:12.5px">エントリがありません</div>';
      return;
    }
    var html = "";
    entries.forEach(function (e) {
      var retired = e.status === "retired";
      html += '<button type="button" class="library-entry-item" data-entry-id="' + escHtml(e.id) + '" style="text-align:left;border:1px solid var(--color-border-tertiary);border-radius:4px;padding:6px 8px;background:' + (retired ? "var(--color-background-tertiary)" : "var(--color-background-primary)") + ';opacity:' + (retired ? "0.6" : "1") + ';cursor:pointer">' +
        '<div style="font-size:12.5px;font-weight:600;color:var(--color-text-primary)">' + escHtml(e.name) +
          (retired ? ' <span style="font-size:10.5px;color:var(--color-text-tertiary)">(廃止)</span>' : '') +
        '</div>' +
        '<div style="font-size:11px;color:var(--color-text-tertiary)">' + (e.entry_type === "apparatus" ? "装置" : "理論コンポーネント") +
          ' ・ 版 ' + (e.latest_version_no || 0) +
          (e.updated_by ? ' ・ 更新: ' + escHtml(e.updated_by) : '') +
        '</div>' +
      '</button>';
    });
    listEl.innerHTML = html;
    listEl.querySelectorAll(".library-entry-item").forEach(function (btn) {
      btn.addEventListener("click", function () { selectLibraryEntry(this.getAttribute("data-entry-id")); });
    });
  }

  function selectLibraryEntry(entryId) {
    var body = document.getElementById("library-detail-body");
    if (body) body.innerHTML = '<div style="color:var(--color-text-tertiary);font-size:12.5px">読み込み中...</div>';
    apiFetch("/admin/library/entries/" + encodeURIComponent(entryId))
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (entry) { renderLibraryDetail(entry); })
      .catch(function () {
        if (body) body.innerHTML = '<div style="color:var(--color-text-danger);font-size:12.5px">エントリの読み込みに失敗しました</div>';
      });
  }

  function renderLibraryDetail(entry) {
    var body = document.getElementById("library-detail-body");
    if (!body) return;
    var isApparatus = entry.entry_type === "apparatus";
    var b = entry.body || {};
    var parts = b.typical_parts || [];
    var aliasesStr = (entry.aliases || []).join(", ");
    var isRetired = entry.status === "retired";

    var html =
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:4px">' +
        '<div style="font-size:11px;color:var(--color-text-tertiary)">' + escHtml(entry.domain_key) + ' ・ ' + (isApparatus ? "装置" : "理論コンポーネント") + (isRetired ? ' ・ <span style="color:var(--color-text-danger)">廃止済み</span>' : '') + '</div>' +
        '<div style="font-size:11px;color:var(--color-text-tertiary)">版 ' + (entry.latest_version_no || 0) + ' / revision ' + entry.revision + '</div>' +
      '</div>' +
      '<div id="library-detail-status" class="upload-status" style="display:none;margin-bottom:8px"></div>' +

      '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">名称</label>' +
      '<input type="text" id="lib-detail-name" value="' + escHtml(entry.name) + '" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:8px;box-sizing:border-box">' +

      '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">別名 (カンマ区切り)</label>' +
      '<input type="text" id="lib-detail-aliases" value="' + escHtml(aliasesStr) + '" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:8px;box-sizing:border-box">' +

      '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">要約</label>' +
      '<textarea id="lib-detail-summary" rows="2" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:8px;box-sizing:border-box">' + escHtml(entry.summary || "") + '</textarea>';

    if (isApparatus) {
      html +=
        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">見た目の特徴 (visual_cues)</label>' +
        '<textarea id="lib-detail-visual-cues" rows="2" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:8px;box-sizing:border-box">' + escHtml(b.visual_cues || "") + '</textarea>' +

        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:4px">典型的な構成パーツ</label>' +
        '<div id="lib-detail-parts-list" style="margin-bottom:6px">' +
          (parts.length ? parts.map(function (p) { return _libraryPartsRowHtml(p.name, p.role); }).join("") : _libraryPartsRowHtml("", "")) +
        '</div>' +
        '<button type="button" id="lib-detail-parts-add" class="admin-action-btn" style="font-size:11.5px;padding:2px 8px;margin-bottom:8px">+ パーツを追加</button>' +

        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">典型的な構成・用途</label>' +
        '<textarea id="lib-detail-configs" rows="2" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:8px;box-sizing:border-box">' + escHtml(b.typical_configurations || "") + '</textarea>' +

        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">計測対象</label>' +
        '<textarea id="lib-detail-measurement" rows="2" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:8px;box-sizing:border-box">' + escHtml(b.measurement_targets || "") + '</textarea>';
    } else {
      html +=
        '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">body (JSON)</label>' +
        '<textarea id="lib-detail-body-json" rows="6" style="width:100%;padding:5px 8px;font-size:12px;font-family:monospace;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:8px;box-sizing:border-box">' + escHtml(JSON.stringify(b, null, 2)) + '</textarea>';
    }

    // exemplar_images は含有承認済みの画像のみを指すが、v1 は件数テキスト表示に留める（画像自体は figures API 経由でのみ配信）。
    var exemplarCount = (entry.exemplar_images || []).length;
    html +=
      '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:8px">例示画像 ' + exemplarCount + ' 件（含有承認済み）</div>' +

      '<div style="font-size:11.5px;color:var(--color-text-secondary);margin-bottom:8px">' +
        '<div>provenance — 由来教材: ' + ((entry.source_document_ids || []).length ? (entry.source_document_ids || []).map(escHtml).join(", ") : "なし") + '</div>' +
        '<div>由来コンポーネント: ' + ((entry.source_component_ids || []).length ? (entry.source_component_ids || []).map(escHtml).join(", ") : "なし") + '</div>' +
      '</div>' +

      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">' +
        '<button id="lib-detail-save" class="admin-action-btn" style="background:var(--color-text-success);color:#fff">保存</button>' +
        (isRetired
          ? '<button id="lib-detail-restore" class="admin-action-btn">復元</button>'
          : ('<button id="lib-detail-freeze" class="admin-action-btn">凍結（版発行）</button>' +
             '<button id="lib-detail-retire" class="admin-action-btn" style="background:var(--color-text-danger);color:#fff">廃止</button>')) +
      '</div>' +

      '<div style="font-size:12px;font-weight:600;margin-bottom:4px">版履歴</div>' +
      '<div id="lib-detail-versions" style="font-size:11.5px;color:var(--color-text-tertiary)">読み込み中...</div>';

    body.innerHTML = html;

    if (isApparatus) {
      document.getElementById("lib-detail-parts-list").addEventListener("click", function (e) {
        var btn = e.target.closest(".lib-part-remove");
        if (!btn) return;
        var row = btn.closest(".lib-part-row");
        if (row) row.remove();
      });
      document.getElementById("lib-detail-parts-add").addEventListener("click", function () {
        document.getElementById("lib-detail-parts-list").insertAdjacentHTML("beforeend", _libraryPartsRowHtml("", ""));
      });
    }

    document.getElementById("lib-detail-save").addEventListener("click", function () { saveLibraryEntryEdits(entry); });
    var freezeBtn = document.getElementById("lib-detail-freeze");
    if (freezeBtn) freezeBtn.addEventListener("click", function () { freezeLibraryEntry(entry.id); });
    var retireBtn = document.getElementById("lib-detail-retire");
    if (retireBtn) retireBtn.addEventListener("click", function () { retireLibraryEntry(entry.id); });
    var restoreBtn = document.getElementById("lib-detail-restore");
    if (restoreBtn) restoreBtn.addEventListener("click", function () { restoreLibraryEntry(entry.id); });

    loadLibraryVersions(entry.id);
  }

  function _libraryDetailStatus(msg, kind) {
    var el = document.getElementById("library-detail-status");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (kind || "info");
  }

  function saveLibraryEntryEdits(entry) {
    var name = document.getElementById("lib-detail-name").value.trim();
    var aliasesRaw = document.getElementById("lib-detail-aliases").value.trim();
    var summary = document.getElementById("lib-detail-summary").value.trim();
    var aliases = aliasesRaw ? aliasesRaw.split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];

    var payload = { expected_revision: entry.revision, name: name, aliases: aliases, summary: summary };

    if (entry.entry_type === "apparatus") {
      payload.body = {
        visual_cues: document.getElementById("lib-detail-visual-cues").value.trim(),
        typical_parts: _libraryReadPartsRows("lib-detail-parts-list"),
        typical_configurations: document.getElementById("lib-detail-configs").value.trim(),
        measurement_targets: document.getElementById("lib-detail-measurement").value.trim(),
      };
    } else {
      var raw = document.getElementById("lib-detail-body-json").value.trim();
      try {
        payload.body = raw ? JSON.parse(raw) : {};
      } catch (e) {
        _libraryDetailStatus("body の JSON が不正です", "error");
        return;
      }
    }

    _libraryDetailStatus("保存しています...", "info");
    apiFetch("/admin/library/entries/" + encodeURIComponent(entry.id), { method: "PUT", body: JSON.stringify(payload) })
      .then(function (res) {
        if (res.status === 409) throw { conflict: true };
        if (!res.ok) return res.json().then(function (d) { throw { message: (d && d.detail) || "保存に失敗しました" }; });
        return res.json();
      })
      .then(function (updated) {
        _libraryDetailStatus("保存しました", "success");
        renderLibraryDetail(updated);
        loadLibraryEntries();
      })
      .catch(function (err) {
        if (err && err.conflict) {
          _libraryDetailStatus("他の教員が更新しました。再読み込みしてください", "error");
        } else {
          _libraryDetailStatus((err && err.message) || "保存に失敗しました", "error");
        }
      });
  }

  function freezeLibraryEntry(entryId) {
    var note = prompt("凍結版のchangelogメモ（任意）");
    if (note === null) return; // cancelled
    if (!confirm("凍結版はパイプラインの解析に使われます。取り消せません（修正は次版で行います）。よろしいですか？")) return;
    _libraryDetailStatus("凍結しています...", "info");
    apiFetch("/admin/library/entries/" + encodeURIComponent(entryId) + "/freeze", { method: "POST", body: JSON.stringify({ note: note || "" }) })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error((d && d.detail) || "凍結に失敗しました"); });
        return res.json();
      })
      .then(function () {
        _libraryDetailStatus("凍結しました", "success");
        selectLibraryEntry(entryId);
        loadLibraryEntries();
      })
      .catch(function (err) { _libraryDetailStatus(err.message || "凍結に失敗しました", "error"); });
  }

  function retireLibraryEntry(entryId) {
    if (!confirm("このエントリを廃止します。retrieval対象から外れますが、履歴・provenanceは保持されます。よろしいですか？")) return;
    apiFetch("/admin/library/entries/" + encodeURIComponent(entryId) + "/retire", { method: "POST" })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error((d && d.detail) || "廃止に失敗しました"); });
        return res.json();
      })
      .then(function () {
        selectLibraryEntry(entryId);
        loadLibraryEntries();
      })
      .catch(function (err) { _libraryDetailStatus(err.message || "廃止に失敗しました", "error"); });
  }

  function restoreLibraryEntry(entryId) {
    apiFetch("/admin/library/entries/" + encodeURIComponent(entryId) + "/restore", { method: "POST" })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error((d && d.detail) || "復元に失敗しました"); });
        return res.json();
      })
      .then(function () {
        selectLibraryEntry(entryId);
        loadLibraryEntries();
      })
      .catch(function (err) { _libraryDetailStatus(err.message || "復元に失敗しました", "error"); });
  }

  function loadLibraryVersions(entryId) {
    var el = document.getElementById("lib-detail-versions");
    if (!el) return;
    apiFetch("/admin/library/entries/" + encodeURIComponent(entryId) + "/versions")
      .then(function (res) { return res.ok ? res.json() : { versions: [] }; })
      .then(function (data) {
        var versions = (data && data.versions) || [];
        if (!el) return;
        if (!versions.length) { el.innerHTML = "まだ凍結版はありません"; return; }
        var html = "";
        versions.forEach(function (v) {
          html += '<div style="padding:4px 0;border-bottom:1px solid var(--color-border-tertiary)">' +
            '版 ' + escHtml(v.version_no) + (v.note ? " — " + escHtml(v.note) : "") +
            '<span style="color:var(--color-text-tertiary)"> ・ ' + escHtml(v.published_by || "") + ' ・ ' + formatDateTime(v.created_at) + '</span>' +
          '</div>';
        });
        el.innerHTML = html;
      })
      .catch(function () { if (el) el.textContent = "版履歴の読み込みに失敗しました"; });
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
    // 画像読み取りパイプライン（migration 041）: チェックボックスの明示オプトインのみで vision LLM を呼ぶ
    var analyzeImagesEl = document.getElementById("upload-analyze-images");
    formData.append("analyze_images", (analyzeImagesEl && analyzeImagesEl.checked) ? "true" : "false");

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
        showMaterialNextStepCard();
        loadMaterials();
        loadMaterialsForSelection();
        if (window.AdminNextSteps) window.AdminNextSteps.refresh();
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
            // G層: 解析完了で material.no_course 等の対象になり得るため再取得する。
            if (window.AdminNextSteps) window.AdminNextSteps.refresh();
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

  // G層 §7: 教材アップロード成功直後のインラインカード（自動遷移はしない・案内のみ）。
  function showMaterialNextStepCard() {
    var statusEl = document.getElementById("upload-status");
    if (!statusEl || !statusEl.parentNode) return;
    var existing = document.getElementById("admin-next-steps-inline-card-materials");
    if (existing) existing.parentNode.removeChild(existing);

    var card = document.createElement("div");
    card.id = "admin-next-steps-inline-card-materials";
    card.className = "admin-next-steps-inline-card";
    card.innerHTML =
      "<span>解析が完了したら『コース構築』でこの教材からコースを作成できます</span>" +
      '<button type="button" class="admin-next-steps-inline-act">案内する</button>';
    var actBtn = card.querySelector(".admin-next-steps-inline-act");
    if (actBtn) {
      actBtn.addEventListener("click", function () {
        if (window.AdminAssistant && window.AdminAssistant.runLocatePlan) {
          window.AdminAssistant.runLocatePlan({
            steps: [{ screen: "course-builder", anchor_id: "cb_material_select", hint: "コースの素材となる教材を選びます" }]
          });
        }
      });
    }
    statusEl.parentNode.insertBefore(card, statusEl.nextSibling);
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
    // 機能2: 教材選択ツールバー（検索・並び替え・絞り込み）を配線
    initMaterialToolbar();
    // Issue #72: 利用可能な教材一覧をロード
    loadMaterialsForSelection();
  }

  // ── Material Selection (Issue #72 / 機能2 コース作成UX) ─────────────
  function loadMaterialsForSelection() {
    // include=summary で主要コンポーネント名・章立て・コース作成済みかを併せて取得
    apiFetch("/admin/materials?include=summary")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var materials = Array.isArray(data) ? data : (data.materials || []);
        // 全ステータスを保持（未解析/解析中もカードで状態を明示する）
        state.availableMaterials = materials;
        renderMaterialCards();
      })
      .catch(function () {
        var listEl = document.getElementById("cb-material-select-list");
        if (listEl) listEl.innerHTML = '<span class="cb-mat-empty">教材の読み込みに失敗しました</span>';
      });
  }

  // 教材選択ツールバー（検索・並び替え・絞り込み）の初期化。DOM は静的なので一度だけ配線する。
  function initMaterialToolbar() {
    var search = document.getElementById("cb-material-search");
    var sort = document.getElementById("cb-material-sort");
    var filters = document.getElementById("cb-material-filters");
    if (search) {
      search.addEventListener("input", function () {
        state.materialSearch = this.value || "";
        renderMaterialCards();
      });
    }
    if (sort) {
      sort.addEventListener("change", function () {
        state.materialSort = this.value;
        renderMaterialCards();
      });
    }
    if (filters) {
      filters.querySelectorAll(".cb-mat-chip").forEach(function (chip) {
        chip.addEventListener("click", function () {
          state.materialFilter = this.getAttribute("data-filter") || "all";
          filters.querySelectorAll(".cb-mat-chip").forEach(function (c) { c.classList.remove("active"); });
          this.classList.add("active");
          renderMaterialCards();
        });
      });
    }
  }

  function cbMaterialSelectable(m) { return m && m.status === "completed"; }

  function cbDocTypeLabel(t) {
    return { textbook: "教科書", lecture_note: "講義ノート", paper: "論文", report: "レポート" }[t] || t || "";
  }

  function cbStatusBadge(m) {
    switch (m.status) {
      case "completed": return { text: "解析完了", cls: "ok" };
      case "processing":
        var p = (m.analysis_progress != null) ? " " + m.analysis_progress + "%" : "";
        return { text: "解析中" + p, cls: "proc" };
      case "failed": return { text: "解析失敗", cls: "fail" };
      default: return { text: "未解析", cls: "idle" };
    }
  }

  function cbFindMaterial(mid) {
    var list = state.availableMaterials || [];
    for (var i = 0; i < list.length; i++) {
      if ((list[i].material_id || list[i].id) === mid) return list[i];
    }
    return null;
  }

  function cbFilteredMaterials() {
    var list = (state.availableMaterials || []).slice();
    var q = (state.materialSearch || "").toLowerCase().trim();
    if (q) {
      list = list.filter(function (m) {
        return ((m.title || "") + " " + (m.filename || "")).toLowerCase().indexOf(q) !== -1;
      });
    }
    if (state.materialFilter === "no_course") {
      list = list.filter(function (m) { return !m.has_course; });
    } else if (state.materialFilter === "recent") {
      list = list.filter(function (m) { return !!m.analyzed_at; });
    } else if (state.materialFilter === "completed") {
      list = list.filter(function (m) { return m.status === "completed"; });
    }
    // 「直近の生成」タブは解析完了時刻順を優先する
    var sort = (state.materialFilter === "recent") ? "analyzed" : state.materialSort;
    list.sort(function (a, b) {
      if (sort === "title") return (a.title || a.filename || "").localeCompare(b.title || b.filename || "");
      if (sort === "volume") return (b.chunk_count || 0) - (a.chunk_count || 0);
      if (sort === "analyzed") return (b.analyzed_at || "").localeCompare(a.analyzed_at || "");
      return (b.uploaded_at || "").localeCompare(a.uploaded_at || "");
    });
    return list;
  }

  function cbUpdateMaterialCount() {
    var el = document.getElementById("cb-material-count");
    if (!el) return;
    var total = (state.availableMaterials || []).length;
    var shown = cbFilteredMaterials().length;
    var sel = (state.selectedMaterialIds || []).length;
    var txt = "表示 " + shown + " / 全 " + total;
    if (sel > 0) txt += "（選択 " + sel + "）";
    el.textContent = txt;
  }

  function renderMaterialCards() {
    var listEl = document.getElementById("cb-material-select-list");
    if (!listEl) return;

    var all = state.availableMaterials || [];
    if (all.length === 0) {
      listEl.innerHTML = '<span class="cb-mat-empty">利用可能な教材がありません</span>';
      cbUpdateMaterialCount();
      return;
    }

    var list = cbFilteredMaterials();
    if (list.length === 0) {
      listEl.innerHTML = '<span class="cb-mat-empty">条件に一致する教材がありません</span>';
      cbUpdateMaterialCount();
      return;
    }

    var html = "";
    list.forEach(function (m) {
      var mid = m.material_id || m.id || "";
      var midE = escHtml(mid);
      var selected = state.selectedMaterialIds.indexOf(mid) !== -1;
      var selectable = cbMaterialSelectable(m);
      var st = cbStatusBadge(m);
      var title = escHtml(m.title || m.filename || "不明な教材");

      var metaParts = [];
      if (m.authors && m.authors.length) {
        metaParts.push(escHtml(m.authors.slice(0, 2).join(", ")) + (m.authors.length > 2 ? " 他" : ""));
      }
      if (m.year) metaParts.push(String(m.year));
      if (m.doc_type) metaParts.push(escHtml(cbDocTypeLabel(m.doc_type)));

      var previewNames = (m.top_components && m.top_components.length) ? m.top_components : (m.top_concepts || []);
      var hasDetail = (m.section_outline && m.section_outline.length) || (previewNames && previewNames.length);

      html += '<div class="cb-mat-card' + (selected ? " selected" : "") + (selectable ? "" : " disabled") + '" data-mid="' + midE + '">';
      html += '<div class="cb-mat-card-row">';
      html += '<span class="cb-mat-check">' + (selected ? "✓" : "") + '</span>';
      html += '<div class="cb-mat-body">';
      html += '<div class="cb-mat-title">' + title + '</div>';
      if (metaParts.length) html += '<div class="cb-mat-meta">' + metaParts.join(" · ") + '</div>';
      html += '<div class="cb-mat-badges">';
      html += '<span class="cb-mat-badge st-' + st.cls + '">' + escHtml(st.text) + '</span>';
      if (m.chunk_count) html += '<span class="cb-mat-badge">' + m.chunk_count + ' チャンク</span>';
      if (m.component_count) html += '<span class="cb-mat-badge">コンポーネント ' + m.component_count + '</span>';
      if (!m.has_course) html += '<span class="cb-mat-badge nocourse">コース未作成</span>';
      html += '</div>';
      if (previewNames && previewNames.length) {
        html += '<div class="cb-mat-preview-line">主要: ' + escHtml(previewNames.slice(0, 3).join(" / ")) + (previewNames.length > 3 ? " …" : "") + '</div>';
      }
      if (!selectable) {
        html += '<div class="cb-mat-note">解析が完了すると選択できます</div>';
      }
      html += '</div>';
      if (hasDetail) html += '<button class="cb-mat-detail-btn" data-mid="' + midE + '" type="button">詳細</button>';
      html += '</div>';
      html += '<div class="cb-mat-detail" style="display:none"></div>';
      html += '</div>';
    });
    listEl.innerHTML = html;
    cbUpdateMaterialCount();
    wireMaterialCards(listEl);
  }

  function wireMaterialCards(listEl) {
    listEl.querySelectorAll(".cb-mat-card").forEach(function (card) {
      card.addEventListener("click", function () {
        var mid = card.getAttribute("data-mid");
        var m = cbFindMaterial(mid);
        if (!m || !cbMaterialSelectable(m)) return;
        toggleMaterialSelection(mid, card);
      });
    });
    listEl.querySelectorAll(".cb-mat-detail-btn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation(); // カードの選択トグルを発火させない
        toggleMaterialDetail(btn.getAttribute("data-mid"), btn.parentElement.parentElement);
      });
    });
  }

  function toggleMaterialSelection(mid, card) {
    var idx = state.selectedMaterialIds.indexOf(mid);
    var selected;
    if (idx === -1) { state.selectedMaterialIds.push(mid); selected = true; }
    else { state.selectedMaterialIds.splice(idx, 1); selected = false; }
    if (card) {
      if (selected) card.classList.add("selected"); else card.classList.remove("selected");
      var chk = card.querySelector(".cb-mat-check");
      if (chk) chk.textContent = selected ? "✓" : "";
    }
    cbUpdateMaterialCount();
  }

  function toggleMaterialDetail(mid, card) {
    if (!card) return;
    var panel = card.querySelector(".cb-mat-detail");
    if (!panel) return;
    if (!panel.style.display || panel.style.display === "none") {
      panel.innerHTML = renderMaterialDetailHtml(cbFindMaterial(mid));
      panel.style.display = "block";
    } else {
      panel.style.display = "none";
    }
  }

  function renderMaterialDetailHtml(m) {
    if (!m) return "";
    var html = "";
    var comps = m.top_components || [];
    if (comps.length) {
      html += '<div class="cb-mat-detail-sec"><div class="cb-mat-detail-h">主要コンポーネント</div><ul>';
      comps.forEach(function (n) { html += "<li>" + escHtml(n) + "</li>"; });
      html += "</ul></div>";
    }
    var outline = m.section_outline || [];
    if (outline.length) {
      html += '<div class="cb-mat-detail-sec"><div class="cb-mat-detail-h">章立て</div><ul>';
      outline.forEach(function (s) { html += "<li>" + escHtml(s) + "</li>"; });
      html += "</ul></div>";
    }
    if (!comps.length && m.top_concepts && m.top_concepts.length) {
      html += '<div class="cb-mat-detail-sec"><div class="cb-mat-detail-h">主要概念</div><ul>';
      m.top_concepts.forEach(function (s) { html += "<li>" + escHtml(s) + "</li>"; });
      html += "</ul></div>";
    }
    return html || '<span class="cb-mat-empty">追加情報はありません</span>';
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

        // G層: コース登録完了で material.no_course が消え course.* 系が現れ得るため再取得する。
        if (window.AdminNextSteps) window.AdminNextSteps.refresh();

        // S2: コース登録直後に分野の地図への配置を提案する (教員が承認するまで確定しない)
        var cbAtlasArea = document.getElementById("cb-atlas-binding-area");
        if (cbAtlasArea) {
          cbAtlasArea.style.display = "";
          atlasBindingPropose(
            newCourseId,
            document.getElementById("cb-atlas-binding-body"),
            document.getElementById("cb-atlas-binding-status")
          );
        }

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

  // ── 分野の地図 — 骨格レビュー・凍結 (Issue A-3) ─────────────────────
  function initAtlas() {
    var select = document.getElementById("atlas-cartridge-select");
    if (!select) return;
    var statusEl = document.getElementById("atlas-status");
    var frozenInfoEl = document.getElementById("atlas-frozen-info");
    var draftArea = document.getElementById("atlas-draft-area");
    var editor = document.getElementById("atlas-draft-editor");
    var validationEl = document.getElementById("atlas-validation");
    var generateBtn = document.getElementById("atlas-generate");
    var overviewEl = document.getElementById("atlas-overview");
    var navReportCountEl = document.getElementById("atlas-nav-report-count");
    var navCourseCountEl = document.getElementById("atlas-nav-course-count");
    // JSON編集 / プレビュー タブ (skeleton editor upgrade P1/P2)
    var jsonView = document.getElementById("atlas-view-json");
    var previewView = document.getElementById("atlas-view-preview");
    var tabJson = document.getElementById("atlas-tab-json");
    var tabPreview = document.getElementById("atlas-tab-preview");
    var assistToggle = document.getElementById("atlas-assist-toggle");
    var assistPanelEl = document.getElementById("atlas-assist-panel");
    var hasDraft = false;
    var cartridgesLoaded = false;
    // migration 027: 楽観ロック。保存/生成は state 取得時の revision を添えて送る
    var draftRevision = null;
    // P2: 直近の検証 issue (region_id/concept_id 付き) をプレビューのハイライトに使う
    var currentIssues = { errorIssues: [], warningIssues: [] };
    // P5: プレビュー上でクリックしたノード (AI agent のスコープヒント)
    var selectionHint = null;
    var activeView = "preview";
    var latestAtlasState = null;
    var latestReportsData = null;
    var latestAtlasCourses = [];
    // 新分野 (カートリッジファイル無し) の生成メタデータ: domain_key → {name, description}
    var pendingDomainMeta = {};

    function setStatus(text, isError) {
      statusEl.textContent = text || "";
      statusEl.style.color = isError ? "var(--color-text-danger, #e53935)" : "var(--color-text-secondary)";
    }

    function basePath() {
      return "/admin/cartridges/" + encodeURIComponent(select.value) + "/atlas/skeleton";
    }

    function scrollToAtlasSection(sectionId) {
      var section = document.getElementById(sectionId);
      if (!section) return;
      document.querySelectorAll("[data-atlas-section]").forEach(function (btn) {
        btn.classList.toggle("on", btn.getAttribute("data-atlas-section") === sectionId);
      });
      try { section.scrollIntoView({ behavior: "smooth", block: "start" }); }
      catch (e) { section.scrollIntoView(); }
    }

    document.querySelectorAll("[data-atlas-section]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        scrollToAtlasSection(btn.getAttribute("data-atlas-section"));
      });
    });

    function setWorkflowStep(currentId, doneIds) {
      ["atlas-step-create", "atlas-step-review", "atlas-step-impact", "atlas-step-publish"].forEach(function (id) {
        var step = document.getElementById(id);
        if (!step) return;
        step.classList.toggle("current", id === currentId);
        step.classList.toggle("done", doneIds.indexOf(id) >= 0);
      });
    }

    function renderAtlasOverview() {
      if (!overviewEl) return;
      if (!select.value) {
        overviewEl.innerHTML = '<div class="atlas-admin-empty">分野を選ぶと、現在の状態と次にすることが表示されます。</div>';
        if (navReportCountEl) navReportCountEl.textContent = "";
        if (navCourseCountEl) navCourseCountEl.textContent = "";
        setWorkflowStep("atlas-step-create", []);
        return;
      }

      var frozen = latestAtlasState && latestAtlasState.frozen ? latestAtlasState.frozen.skeleton : null;
      var draft = latestAtlasState && latestAtlasState.draft ? latestAtlasState.draft : null;
      var validation = draft && draft.validation ? draft.validation : { errors: [], warnings: [] };
      var reports = latestReportsData && latestReportsData.reports ? latestReportsData.reports : [];
      var pending = reports.filter(function (r) { return r.status === "pending"; }).length;
      var acceptedOpen = reports.filter(function (r) {
        return r.status === "accepted" && !r.incorporated_at && !r.applied_version;
      }).length;
      var incorporated = reports.filter(function (r) {
        return r.status === "accepted" && r.incorporated_at && !r.applied_version;
      }).length;
      var relevantCourses = latestAtlasCourses.filter(function (c) {
        return !c.atlas_cartridge_id || c.atlas_cartridge_id === select.value;
      });
      var courseNeedsReview = relevantCourses.filter(function (c) {
        var total = Number(c.topic_count || 0);
        var bound = Number(c.atlas_topic_count || 0);
        return !c.atlas_cartridge_id || total === 0 || bound < total;
      });

      var publishedText = frozen ? ("版 " + escHtml(frozen.version || "—")) : "未公開";
      var draftText = draft
        ? ("編集中" + ((validation.errors || []).length ? "・エラー " + validation.errors.length + "件" : "・確認可能") )
        : "なし";
      var reportText = "未確認 " + pending + "件" +
        (acceptedOpen ? "・次版で未対応 " + acceptedOpen + "件" : "") +
        (incorporated ? "・公開待ち " + incorporated + "件" : "");
      var courseText = courseNeedsReview.length ? "要確認 " + courseNeedsReview.length + "件" : "確認事項なし";

      var action = { text: "現在、急いで対応する項目はありません。", label: "マップを見る", section: "atlas-map-section" };
      if (!frozen && !draft) {
        action = { text: "学習者に表示する最初の分野マップを作成してください。", label: "最初の地図を作る", generate: true };
        setWorkflowStep("atlas-step-create", []);
      } else if (pending > 0) {
        action = { text: "未確認の修正報告が " + pending + "件あります。改版の要否を判断してください。", label: "修正報告を確認", section: "atlas-reports-section" };
        setWorkflowStep("atlas-step-impact", ["atlas-step-create"]);
      } else if (draft) {
        action = { text: "編集中の次版があります。地図の見え方と検証結果を確認してください。", label: "次版の編集を続ける", section: "atlas-map-section" };
        setWorkflowStep("atlas-step-review", ["atlas-step-create"]);
      } else if (courseNeedsReview.length > 0) {
        action = { text: "地図配置を確認するコースが " + courseNeedsReview.length + "件あります。", label: "コース配置を確認", section: "atlas-binding-section" };
        setWorkflowStep("atlas-step-impact", ["atlas-step-create", "atlas-step-review"]);
      } else {
        setWorkflowStep("atlas-step-publish", ["atlas-step-create", "atlas-step-review", "atlas-step-impact"]);
      }

      overviewEl.innerHTML =
        '<div class="atlas-admin-summary-grid">' +
          '<div class="atlas-admin-summary-card"><span class="atlas-admin-summary-label">学習者に公開中</span><span class="atlas-admin-summary-value">' + publishedText + '</span></div>' +
          '<div class="atlas-admin-summary-card"><span class="atlas-admin-summary-label">編集中の次版</span><span class="atlas-admin-summary-value">' + escHtml(draftText) + '</span></div>' +
          '<div class="atlas-admin-summary-card"><span class="atlas-admin-summary-label">修正報告</span><span class="atlas-admin-summary-value">' + escHtml(reportText) + '</span></div>' +
          '<div class="atlas-admin-summary-card"><span class="atlas-admin-summary-label">コース配置</span><span class="atlas-admin-summary-value">' + escHtml(courseText) + '</span></div>' +
        '</div>' +
        '<div class="atlas-admin-next"><div class="atlas-admin-next-copy"><small>次にすること</small>' + escHtml(action.text) + '</div>' +
          '<button type="button" id="atlas-overview-action" class="admin-action-btn atlas-admin-primary">' + escHtml(action.label) + '</button></div>';
      var overviewAction = document.getElementById("atlas-overview-action");
      if (overviewAction) overviewAction.addEventListener("click", function () {
        if (action.generate && generateBtn) generateBtn.click();
        else scrollToAtlasSection(action.section || "atlas-map-section");
      });
      if (navReportCountEl) navReportCountEl.textContent = pending ? String(pending) : "";
      if (navCourseCountEl) navCourseCountEl.textContent = courseNeedsReview.length ? String(courseNeedsReview.length) : "";
    }

    function renderValidation(validation) {
      if (!validation) {
        validationEl.innerHTML = "";
        currentIssues = { errorIssues: [], warningIssues: [] };
        return;
      }
      // P2: 構造化 issue (region_id/concept_id/edge) を保持。無ければメッセージ文字列から補う
      currentIssues = {
        errorIssues: validation.error_issues || (validation.errors || []).map(function (m) { return { message: m }; }),
        warningIssues: validation.warning_issues || (validation.warnings || []).map(function (m) { return { message: m }; }),
      };
      var html = "";
      (validation.errors || []).forEach(function (e) {
        html += "<div style='color:var(--color-text-danger,#e53935)'>エラー: " + escHtml(e) + "</div>";
      });
      (validation.warnings || []).forEach(function (w) {
        html += "<div style='color:var(--color-text-secondary)'>警告: " + escHtml(w) + "</div>";
      });
      validationEl.innerHTML = html;
      // プレビュー表示中なら最新の issue でハイライトを描き直す
      if (activeView === "preview") renderPreview();
    }

    // 現在の editor JSON をパースして骨格 dict (atlas_skeleton 中身) を返す。失敗時 null。
    function parseSkeletonFromEditor() {
      try {
        var parsed = JSON.parse(editor.value);
        return parsed && parsed.atlas_skeleton ? parsed.atlas_skeleton : parsed;
      } catch (e) {
        return null;
      }
    }

    function renderPreview() {
      if (!previewView || !window.AtlasDraftPreview) return;
      var skeleton = parseSkeletonFromEditor();
      if (!skeleton) {
        previewView.innerHTML = "<div style='color:var(--color-text-danger,#e53935);font-size:12px;padding:10px'>" +
          "JSON の構文エラーのためプレビューできません。「JSON編集」タブで修正してください。</div>";
        return;
      }
      window.AtlasDraftPreview.render(previewView, skeleton, {
        errorIssues: currentIssues.errorIssues,
        warningIssues: currentIssues.warningIssues,
        selectedId: selectionHint ? (selectionHint.concept_id || selectionHint.region_id) : null,
        onSelect: function (detail) {
          selectionHint = detail;
          if (window.AtlasAssistPanel && window.AtlasAssistPanel.setSelectionHint) {
            window.AtlasAssistPanel.setSelectionHint(detail);
          }
        },
      });
    }

    function switchView(view) {
      activeView = view;
      var isPreview = view === "preview";
      if (jsonView) jsonView.style.display = isPreview ? "none" : "";
      if (previewView) previewView.style.display = isPreview ? "" : "none";
      if (tabJson) { tabJson.classList.toggle("on", !isPreview); tabJson.setAttribute("aria-selected", isPreview ? "false" : "true"); }
      if (tabPreview) { tabPreview.classList.toggle("on", isPreview); tabPreview.setAttribute("aria-selected", isPreview ? "true" : "false"); }
      if (isPreview) renderPreview();
    }

    if (tabJson) tabJson.addEventListener("click", function () { switchView("json"); });
    if (tabPreview) tabPreview.addEventListener("click", function () { switchView("preview"); });
    // §3.1: JSON編集タブでの編集は blur 時に (構文が通れば) プレビューへ即時反映する
    if (editor) editor.addEventListener("blur", function () { if (activeView === "preview") renderPreview(); });

    function renderState(data) {
      latestAtlasState = data || null;
      hasDraft = !!(data && data.draft);
      var frozen = data && data.frozen ? data.frozen.skeleton : null;
      if (frozen) {
        var reviewers = (frozen.reviewed_by || []).join(", ");
        var infoHtml =
          "学習者に公開中: 版 <b>" + escHtml(frozen.version) + "</b>" +
          " ／ 作成方法: " + escHtml(frozen.generated_by || "—") +
          " ／ 確認者: " + escHtml(reviewers || "—") +
          "（公開済みの版は直接変更せず、次版として編集します）";
        // changelog と修正報告者への帰属 credits の表示 (Issue D-3, 受け入れ条件3)
        var entries = frozen.changelog || [];
        if (entries.length) {
          infoHtml += "<div style='margin-top:6px'>これまでの変更:</div><ul style='margin:2px 0 0 18px;padding:0'>";
          for (var ci = entries.length - 1; ci >= 0; ci--) {
            var entry = entries[ci];
            infoHtml += "<li>版 " + escHtml(entry.version) + " — " + escHtml(entry.note || "");
            if ((entry.credits || []).length) {
              infoHtml += "（修正報告の帰属: " + escHtml(entry.credits.join(", ")) + "）";
            }
            infoHtml += "</li>";
          }
          infoHtml += "</ul>";
        }
        frozenInfoEl.innerHTML = infoHtml;
      } else {
        frozenInfoEl.textContent = "公開中の分野マップはまだありません（学習者には地図が表示されません）。";
      }
      if (hasDraft) {
        draftArea.style.display = "";
        editor.value = JSON.stringify({ atlas_skeleton: data.draft.skeleton }, null, 2);
        renderValidation(data.draft.validation);
        generateBtn.textContent = "次版の対応案を作り直す（現在の編集内容を置換）";
        draftRevision = data.draft.revision || null;
        switchView("preview");
      } else {
        draftArea.style.display = "none";
        editor.value = "";
        renderValidation(null);
        generateBtn.textContent = frozen ? "新しい版の編集を始める" : "最初の地図を作る";
        draftRevision = null;
      }
      // draft を読み直したら AI agent は古い revision に基づくため会話を無効化する
      if (window.AtlasAssistPanel && window.AtlasAssistPanel.notifyDraftChanged) {
        window.AtlasAssistPanel.notifyDraftChanged(draftRevision);
      }
      renderAtlasOverview();
    }

    function loadState() {
      if (!select.value) {
        latestAtlasState = null;
        latestReportsData = null;
        frozenInfoEl.textContent = "";
        draftArea.style.display = "none";
        renderReports(null);
        setStatus("カートリッジを選択してください");
        renderAtlasOverview();
        return;
      }
      setStatus("読み込み中...");
      apiFetch(basePath())
        .then(function (res) {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then(function (data) { renderState(data); setStatus(""); })
        .catch(function (err) { setStatus("読み込みに失敗しました: " + err.message, true); });
      loadReports();
    }

    // ── 修正報告のレビューキュー (Issue D-2 / D-3) ────────────────────
    var reportsListEl = document.getElementById("atlas-reports-list");
    var reportsHintEl = document.getElementById("atlas-reports-hint");
    var reportsStatusEl = document.getElementById("atlas-reports-status");
    var reportsFilterEl = document.getElementById("atlas-report-status-filter");

    var REPORT_STATUS_LABELS = {
      pending: "レビュー待ち",
      accepted: "採用済み",
      declined: "見送り",
      merged: "重複統合",
    };

    function setReportsStatus(text, isError) {
      if (!reportsStatusEl) return;
      reportsStatusEl.textContent = text || "";
      reportsStatusEl.style.color = isError ? "var(--color-text-danger, #e53935)" : "var(--color-text-secondary)";
    }

    function reportsPath() {
      return "/admin/cartridges/" + encodeURIComponent(select.value) + "/atlas/reports";
    }

    function renderReports(data) {
      if (!reportsListEl) return;
      if (!data) {
        latestReportsData = null;
        reportsListEl.innerHTML = "";
        reportsHintEl.innerHTML = "";
        renderAtlasOverview();
        return;
      }
      latestReportsData = data;
      // 改版検討ヒント (issue A の閾値と接続。表示のみ・自動改版はしない)
      var hints = data.revision_hint_targets || [];
      if (hints.length) {
        reportsHintEl.innerHTML =
          "<span style='color:var(--color-text-danger,#e53935)'>改版検討: </span>" +
          "同一対象への未クローズ報告が閾値（" + escHtml(String(data.revision_trigger_threshold)) + "件）に達しています — " +
          escHtml(hints.join(", "));
      } else {
        reportsHintEl.innerHTML = "";
      }
      var allReports = data.reports || [];
      var selectedStatus = reportsFilterEl ? reportsFilterEl.value : "pending";
      var reports = selectedStatus
        ? allReports.filter(function (r) { return r.status === selectedStatus; })
        : allReports;
      renderAtlasOverview();
      if (!reports.length) {
        reportsListEl.innerHTML = "<div style='color:var(--color-text-tertiary)'>該当する報告はありません。</div>";
        return;
      }
      var html = "";
      reports.forEach(function (r) {
        var target = r.node_id ? ("ノード " + r.node_id) : ("領域 " + r.region_id);
        if (r.node_label) target += "（" + r.node_label + "）";
        html += "<div class='admin-list-item' data-report-id='" + escHtml(r.id) + "' style='border:1px solid var(--color-border);border-radius:6px;padding:8px 10px;margin-bottom:8px'>";
        html += "<div style='display:flex;gap:8px;flex-wrap:wrap;align-items:center'>";
        html += "<b>" + escHtml(target) + "</b>";
        html += "<span>レベル" + escHtml(String(r.level)) + "</span>";
        html += "<span>骨格 " + escHtml(r.skeleton_version) + "</span>";
        if (r.version_mismatch) {
          // 旧版への報告の識別 (受け入れ条件5)
          html += "<span style='color:var(--color-text-danger,#e53935);border:1px solid currentColor;border-radius:4px;padding:0 6px;font-size:11px'>旧版（現行 " + escHtml(data.current_version || "—") + "）</span>";
        }
        var statusLabel = REPORT_STATUS_LABELS[r.status] || r.status;
        if (r.status === "accepted" && r.applied_version) statusLabel = "公開版に反映済み";
        else if (r.status === "accepted" && r.incorporated_at) statusLabel = "次版で対応済み";
        else if (r.status === "accepted") statusLabel = "採用・次版で対応待ち";
        html += "<span class='atlas-admin-report-badge " + escHtml(r.status) + "'>" + escHtml(statusLabel) + "</span>";
        if (r.open_count_for_target > 1) {
          html += "<span style='color:var(--color-text-secondary)'>同一対象の未クローズ報告: " + escHtml(String(r.open_count_for_target)) + "件</span>";
        }
        html += "</div>";
        html += "<div style='margin:6px 0;white-space:pre-wrap'>" + escHtml(r.report_text) + "</div>";
        html += "<div style='color:var(--color-text-tertiary);font-size:11.5px'>報告者: " + escHtml(r.reporter_name || r.reporter_id) +
          " ／ " + escHtml((r.created_at || "").slice(0, 16).replace("T", " ")) + "</div>";
        if (r.resolution_note) {
          html += "<div style='color:var(--color-text-secondary);font-size:12px;margin-top:4px'>処理メモ: " + escHtml(r.resolution_note) + "</div>";
        }
        if (r.incorporation_note) {
          html += "<div style='color:var(--color-text-secondary);font-size:12px;margin-top:2px'>次版での対応: " + escHtml(r.incorporation_note) + "</div>";
        }
        if (r.applied_version) {
          html += "<div style='color:var(--color-text-secondary);font-size:12px;margin-top:2px'>版 " + escHtml(r.applied_version) + " に反映済み</div>";
        }
        if (r.status === "pending") {
          html += "<div style='display:flex;gap:6px;margin-top:6px;flex-wrap:wrap'>" +
            "<button class='admin-action-btn' data-report-action='accept'>採用（次版へ）</button>" +
            "<button class='admin-action-btn' data-report-action='decline'>見送り（理由つき）</button>" +
            "<button class='admin-action-btn' data-report-action='merge'>重複統合</button>" +
            "</div>";
        } else if (r.status === "accepted" && !r.incorporated_at && !r.applied_version) {
          html += "<div style='display:flex;gap:6px;margin-top:6px;flex-wrap:wrap'>" +
            "<button class='admin-action-btn' data-report-action='incorporate'>次版で対応済みにする</button>" +
            "</div>";
        }
        html += "</div>";
      });
      reportsListEl.innerHTML = html;
    }

    function loadReports() {
      if (!select.value || !reportsListEl) { renderReports(null); return; }
      setReportsStatus("読み込み中...");
      // Always load all statuses once.  The dashboard counts and the filtered
      // review list must be projections of the same report set.
      apiFetch(reportsPath())
        .then(function (res) {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then(function (data) { renderReports(data); setReportsStatus(""); })
        .catch(function (err) {
          renderReports(null);
          setReportsStatus("報告の読み込みに失敗しました: " + err.message, true);
        });
    }

    function resolveReport(reportId, action) {
      if (action === "incorporate") {
        var incorporationNote = prompt("次版で行った対応のメモ（任意）") || "";
        if (!confirm("この報告を、編集中の次版で対応済みとして記録しますか？")) return;
        setReportsStatus("次版での対応を記録中...");
        apiFetch(reportsPath() + "/" + encodeURIComponent(reportId) + "/incorporate", {
          method: "POST",
          body: JSON.stringify({ note: incorporationNote }),
        })
          .then(function (res) {
            return res.json().then(function (body) {
              if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "HTTP " + res.status);
              return body;
            });
          })
          .then(function () { setReportsStatus("次版で対応済みとして記録しました"); loadReports(); })
          .catch(function (err) { setReportsStatus("記録に失敗しました: " + err.message, true); });
        return;
      }
      var note = "";
      var mergeInto = "";
      if (action === "decline") {
        note = prompt("見送りの理由（必須・報告者に通知されます）") || "";
        if (!note.trim()) { setReportsStatus("見送りには理由が必要です", true); return; }
      } else if (action === "merge") {
        mergeInto = prompt("統合先の報告ID（同じ内容の報告の id）") || "";
        if (!mergeInto.trim()) { setReportsStatus("統合先の報告IDが必要です", true); return; }
        note = prompt("統合メモ（任意）") || "";
      } else if (action === "accept") {
        if (!confirm("この報告を採用します。次版で対応した後に、対応済みとして記録してください。よろしいですか？")) return;
        note = prompt("処理メモ（任意）") || "";
      }
      setReportsStatus("処理中...");
      apiFetch(reportsPath() + "/" + encodeURIComponent(reportId) + "/resolve", {
        method: "POST",
        body: JSON.stringify({ action: action, note: note, merge_into: mergeInto }),
      })
        .then(function (res) {
          return res.json().then(function (body) {
            if (!res.ok) {
              var detail = body.detail;
              throw new Error(typeof detail === "string" ? detail : "HTTP " + res.status);
            }
            return body;
          });
        })
        .then(function () { setReportsStatus("処理しました（報告者に通知されます）"); loadReports(); })
        .catch(function (err) { setReportsStatus("処理に失敗しました: " + err.message, true); });
    }

    if (reportsListEl) {
      reportsListEl.addEventListener("click", function (e) {
        var btn = e.target.closest ? e.target.closest("[data-report-action]") : null;
        if (!btn) return;
        var item = btn.closest("[data-report-id]");
        if (!item) return;
        resolveReport(item.getAttribute("data-report-id"), btn.getAttribute("data-report-action"));
      });
      document.getElementById("atlas-reports-refresh").addEventListener("click", loadReports);
      reportsFilterEl.addEventListener("change", function () {
        if (latestReportsData) renderReports(latestReportsData);
        else loadReports();
      });
    }

    function addDomainOption(key, label) {
      for (var i = 0; i < select.options.length; i++) {
        if (select.options[i].value === key) return;
      }
      var opt = document.createElement("option");
      opt.value = key;
      opt.textContent = label || key;
      select.appendChild(opt);
    }

    function loadCartridges() {
      if (cartridgesLoaded) { loadState(); return; }
      // migration 027: 一覧はカートリッジ (名前) + DB 骨格の domain の合成
      var names = {};
      apiFetch("/admin/cartridges")
        .then(function (res) { return res.json(); })
        .then(function (items) {
          (items || []).forEach(function (c) {
            names[c.cartridge_id] = c.name;
          });
          return apiFetch("/admin/atlas/domains");
        })
        .then(function (res) { return res.ok ? res.json() : { domains: [] }; })
        .then(function (data) {
          var keys = {};
          (data.domains || []).forEach(function (d) {
            keys[d.domain_key] = true;
            // migration 028: DB 永続化された domain_meta の名前をラベルに使う
            // (カートリッジファイルの無い新分野。names には出てこない)
            if (d.domain_name && !names[d.domain_key]) {
              names[d.domain_key] = d.domain_name;
            }
          });
          Object.keys(names).forEach(function (k) { keys[k] = true; });
          var sorted = Object.keys(keys).sort();
          sorted.forEach(function (k) {
            addDomainOption(k, names[k] ? names[k] + " (" + k + ")" : k);
          });
          cartridgesLoaded = true;
          if (sorted.length === 1) {
            select.value = sorted[0];
            loadState();
          }
        })
        .catch(function () { setStatus("分野一覧の取得に失敗しました", true); });
    }

    function loadAtlasCourses() {
      apiFetch("/admin/courses")
        .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
        .then(function (courses) { latestAtlasCourses = courses || []; renderAtlasOverview(); })
        .catch(function () { latestAtlasCourses = []; renderAtlasOverview(); });
    }

    function runGenerate(force) {
      setStatus("次版の対応案を作成中...");
      var payload = { force: !!force };
      if (pendingDomainMeta[select.value]) {
        payload.domain = pendingDomainMeta[select.value];
      }
      apiFetch(basePath() + "/generate", {
        method: "POST",
        body: JSON.stringify(payload),
      })
        .then(function (res) {
          return res.json().then(function (body) {
            if (!res.ok) {
              var detail = body.detail;
              if (res.status === 409 && detail && detail.current_revision != null) {
                throw new Error("他の編集とぶつかりました。再読込してください");
              }
              throw new Error(
                typeof detail === "string" ? detail : (detail && detail.message) || ("HTTP " + res.status)
              );
            }
            return body;
          });
        })
        .then(function () { setStatus("次版を作成しました。地図プレビューを確認してください"); loadState(); })
        .catch(function (err) { setStatus("生成に失敗しました: " + err.message, true); });
    }

    generateBtn.addEventListener("click", function () {
      if (!select.value) { setStatus("分野を選択してください", true); return; }
      if (hasDraft && !confirm("現在の次版を置き換えて、対応案を作り直しますか？")) return;
      runGenerate(hasDraft);
    });

    // 新分野の追加 (migration 027: カートリッジファイル不要。骨格はDB管理)
    var addDomainToggle = document.getElementById("atlas-add-domain-toggle");
    var addDomainForm = document.getElementById("atlas-add-domain-form");
    if (addDomainToggle && addDomainForm) {
      addDomainToggle.addEventListener("click", function () {
        addDomainForm.style.display = addDomainForm.style.display === "none" ? "" : "none";
      });
      document.getElementById("atlas-add-domain").addEventListener("click", function () {
        var key = document.getElementById("atlas-new-domain-key").value.trim();
        var name = document.getElementById("atlas-new-domain-name").value.trim();
        var desc = document.getElementById("atlas-new-domain-desc").value.trim();
        if (!/^[a-z][a-z0-9_]*$/.test(key)) {
          setStatus("管理IDは英小文字・数字・アンダースコア（先頭は英字）で入力してください", true);
          return;
        }
        if (!name) { setStatus("分野名を入力してください", true); return; }
        pendingDomainMeta[key] = { name: name, description: desc };
        addDomainOption(key, name + " (" + key + ")");
        select.value = key;
        addDomainForm.style.display = "none";
        runGenerate(false);
      });
    }

    // draft 保存 (AIアシスト適用からも再利用するため関数化)。成功時に Promise を解決する。
    function saveDraft() {
      var parsed;
      try {
        parsed = JSON.parse(editor.value);
      } catch (e) {
        setStatus("JSON の構文エラー: " + e.message, true);
        return Promise.reject(e);
      }
      setStatus("保存中...");
      return apiFetch(basePath() + "/draft", {
        method: "PUT",
        body: JSON.stringify({ skeleton: parsed, revision: draftRevision }),
      })
        .then(function (res) {
          return res.json().then(function (body) {
            if (!res.ok) {
              var detail = body.detail;
              if (res.status === 409) {
                // 楽観ロック衝突: 他の教員の保存が先行した。最新を読み直す
                loadState();
                throw new Error("他の教員が次版を更新しました。最新の内容を読み込み直したため、編集をやり直してください");
              }
              if (detail && detail.errors) {
                renderValidation({
                  errors: detail.errors, warnings: [],
                  error_issues: detail.error_issues, warning_issues: [],
                });
                throw new Error(detail.message || "バリデーションエラー");
              }
              throw new Error(typeof detail === "string" ? detail : "HTTP " + res.status);
            }
            return body;
          });
        })
        .then(function (body) {
          renderValidation(body.draft.validation);
          draftRevision = body.draft.revision || null;
          setStatus("次版を保存しました");
          // 4.1: draft が更新されたので AI agent の古い revision の提案を無効化する
          if (window.AtlasAssistPanel && window.AtlasAssistPanel.notifyDraftChanged) {
            window.AtlasAssistPanel.notifyDraftChanged(draftRevision);
          }
          return body;
        })
        .catch(function (err) { setStatus("保存に失敗しました: " + err.message, true); throw err; });
    }

    document.getElementById("atlas-save-draft").addEventListener("click", function () {
      saveDraft().catch(function () {});
    });

    // AIアシスト提案の適用: 提案後の骨格 dict を editor へ反映し、保存フローに乗せる
    // (AIが直接 draft を確定させない — 教員の「適用」で PUT を経由する。§4.3)
    function applyAssistProposal(resultSkeleton) {
      if (!resultSkeleton) return Promise.reject(new Error("提案結果が空です"));
      editor.value = JSON.stringify({ atlas_skeleton: resultSkeleton }, null, 2);
      if (activeView === "preview") renderPreview();
      return saveDraft();
    }

    // AI agent トグル (skeleton editor upgrade P3/P4)
    if (assistToggle && assistPanelEl) {
      assistToggle.addEventListener("click", function () {
        if (!select.value) { setStatus("分野を選択してください", true); return; }
        var visible = assistPanelEl.style.display !== "none";
        if (visible) { assistPanelEl.style.display = "none"; return; }
        assistPanelEl.style.display = "";
        if (window.AtlasAssistPanel) {
          window.AtlasAssistPanel.open({
            panelEl: assistPanelEl,
            cartridgeId: select.value,
            apiFetch: apiFetch,
            basePath: basePath,
            getRevision: function () { return draftRevision; },
            getSkeleton: parseSkeletonFromEditor,
            getSelectionHint: function () { return selectionHint; },
            onApply: applyAssistProposal,
            // §4.4: 解釈した対象をプレビュー上でハイライトし、チャットと連動させる
            onHighlight: function (detail) {
              selectionHint = detail;
              switchView("preview");
            },
          });
        }
      });
    }

    document.getElementById("atlas-freeze").addEventListener("click", function () {
      var version = document.getElementById("atlas-freeze-version").value.trim();
      var note = document.getElementById("atlas-freeze-note").value.trim();
      if (!version) { setStatus("版数を入力してください (例: 2026.1)", true); return; }
      if (currentIssues.errorIssues.length) {
        setStatus("検証エラーが " + currentIssues.errorIssues.length + "件あります。修正してから公開してください", true);
        switchView("preview");
        return;
      }
      var allReports = latestReportsData && latestReportsData.reports ? latestReportsData.reports : [];
      var pendingCount = allReports.filter(function (r) { return r.status === "pending"; }).length;
      var acceptedOpenCount = allReports.filter(function (r) {
        return r.status === "accepted" && !r.incorporated_at && !r.applied_version;
      }).length;
      if (acceptedOpenCount) {
        setStatus("採用済みで次版に未対応の修正報告が " + acceptedOpenCount + "件あります。対応後に公開してください", true);
        scrollToAtlasSection("atlas-reports-section");
        return;
      }
      var warningCount = currentIssues.warningIssues.length;
      var check = "公開前チェック\n\n" +
        "公開版: " + version + "\n" +
        "検証エラー: 0件\n" +
        "検証警告: " + warningCount + "件\n" +
        "未確認の修正報告: " + pendingCount + "件\n\n" +
        "この次版を学習者向けに公開しますか？";
      if (!confirm(check)) return;
      setStatus("学習者向けに公開中...");
      apiFetch(basePath() + "/freeze", {
        method: "POST",
        body: JSON.stringify({ version: version, note: note }),
      })
        .then(function (res) {
          return res.json().then(function (body) {
            if (!res.ok) {
              var detail = body.detail;
              throw new Error(typeof detail === "string" ? detail : "HTTP " + res.status);
            }
            return body;
          });
        })
        .then(function () { setStatus("版 " + version + " を学習者向けに公開しました"); loadState(); loadReports(); })
        .catch(function (err) { setStatus("公開に失敗しました: " + err.message, true); });
    });

    select.addEventListener("change", function () {
      // 分野を切り替えたら選択ヒント・ビュー・AI agent をリセットする
      selectionHint = null;
      switchView("preview");
      if (assistPanelEl) assistPanelEl.style.display = "none";
      if (window.AtlasAssistPanel && window.AtlasAssistPanel.reset) window.AtlasAssistPanel.reset();
      loadState();
    });
    document.getElementById("atlas-refresh").addEventListener("click", loadState);
    onTabActivate("atlas", function () { loadCartridges(); loadAtlasCourses(); });
    document.addEventListener("atlas:binding-saved", loadAtlasCourses);
    initAtlasBinding();
  }

  // ── 学習マップ編集 — コース ⇄ 地図バインディング (S2) ─────────────────
  //
  // 提案は決定論的な突合 (サーバ側 match_topic_to_concept)。教員が確認して
  // 「この対応で反映」するまで確定しない。コースビルダーの登録直後にも同じ
  // エディタを cb-atlas-binding-area へ描画する。

  function atlasBindingRenderEditor(bodyEl, statusEl, courseId, data, onSaved) {
    function setStatus(text, isError) {
      if (!statusEl) return;
      statusEl.textContent = text || "";
      statusEl.style.color = isError ? "var(--color-text-danger, #e53935)" : "var(--color-text-secondary)";
    }
    var proposals = data.proposals || [];
    if (!proposals.length) {
      bodyEl.innerHTML = "<div style='color:var(--color-text-tertiary)'>凍結済みの骨格がありません。先に「分野の地図」で骨格を生成・凍結してください。</div>";
      return;
    }
    var byKey = {};
    proposals.forEach(function (p) { byKey[p.domain_key] = p; });
    var initial = data.recommended || data.current_cartridge_id || proposals[0].domain_key;

    var html = "<div style='display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px'>";
    html += "<label style='font-size:12.5px'>分野:</label>";
    html += "<select data-role='ab-domain' style='padding:4px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)'>";
    html += "<option value=''>（バインドしない — 地図を出さない/導出に任せる）</option>";
    proposals.forEach(function (p) {
      var sel = p.domain_key === initial ? " selected" : "";
      html += "<option value='" + escHtml(p.domain_key) + "'" + sel + ">" +
        escHtml(p.domain_key) + "（トピック一致 " + p.matched + "/" + p.topic_count + "）</option>";
    });
    html += "</select>";
    if (data.recommended) {
      html += "<span style='font-size:12px;color:var(--color-text-tertiary)'>提案: " + escHtml(data.recommended) + "</span>";
    }
    html += "</div>";
    html += "<div data-role='ab-table'></div>";
    html += "<div style='margin-top:8px'><button data-role='ab-save' class='admin-action-btn'>この対応で反映</button></div>";
    bodyEl.innerHTML = html;

    var domainSelect = bodyEl.querySelector("[data-role='ab-domain']");
    var tableEl = bodyEl.querySelector("[data-role='ab-table']");

    function renderTable() {
      var p = byKey[domainSelect.value];
      if (!p) {
        tableEl.innerHTML = "<div style='color:var(--color-text-tertiary);font-size:12px'>分野をバインドしない場合、トピックの対応付けは保存時にすべて解除されます。</div>";
        return;
      }
      var t = "<table style='width:100%;border-collapse:collapse;font-size:12.5px'>";
      t += "<tr><th style='text-align:left;padding:4px;border-bottom:1px solid var(--color-border)'>トピック</th>" +
        "<th style='text-align:left;padding:4px;border-bottom:1px solid var(--color-border)'>地図上の概念（提案を編集可）</th></tr>";
      (p.bindings || []).forEach(function (b) {
        t += "<tr><td style='padding:4px;border-bottom:1px solid var(--color-border)'>" + escHtml(b.topic_title) + "</td>";
        t += "<td style='padding:4px;border-bottom:1px solid var(--color-border)'>";
        t += "<select data-role='ab-topic' data-topic-id='" + escHtml(b.topic_id) + "' style='padding:2px 6px;font-size:12.5px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)'>";
        var selectedNode = b.atlas_node_id || b.current || "";
        t += "<option value=''" + (selectedNode ? "" : " selected") + ">（対応なし）</option>";
        (p.concepts || []).forEach(function (c) {
          var sel = c.id === selectedNode ? " selected" : "";
          t += "<option value='" + escHtml(c.id) + "'" + sel + ">" +
            escHtml(c.region_label + " › " + c.label) + "</option>";
        });
        t += "</select>";
        if (b.atlas_node_id) {
          t += " <span style='font-size:11.5px;color:var(--color-text-tertiary)'>提案: " + escHtml(b.node_label || b.atlas_node_id) + "</span>";
        }
        t += "</td></tr>";
      });
      t += "</table>";
      tableEl.innerHTML = t;
    }
    domainSelect.addEventListener("change", renderTable);
    renderTable();

    bodyEl.querySelector("[data-role='ab-save']").addEventListener("click", function () {
      var bindings = [];
      var selects = tableEl.querySelectorAll("[data-role='ab-topic']");
      for (var i = 0; i < selects.length; i++) {
        bindings.push({
          topic_id: selects[i].getAttribute("data-topic-id"),
          atlas_node_id: selects[i].value,
        });
      }
      setStatus("保存中...");
      apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/atlas-binding", {
        method: "PUT",
        body: JSON.stringify({ cartridge_id: domainSelect.value, topic_bindings: bindings }),
      })
        .then(function (res) {
          return res.json().then(function (body) {
            if (!res.ok) {
              var detail = body.detail;
              throw new Error(typeof detail === "string" ? detail : "HTTP " + res.status);
            }
            return body;
          });
        })
        .then(function (body) {
          if (body.cartridge_id) {
            setStatus("保存しました（分野: " + body.cartridge_id + " ／ トピック対応 " + body.bindings_applied + " 件）");
          } else {
            setStatus("バインドを解除しました");
          }
          // G層: course.no_atlas_binding が解消され得るため再取得する。
          if (window.AdminNextSteps) window.AdminNextSteps.refresh();
          document.dispatchEvent(new CustomEvent("atlas:binding-saved"));
          if (onSaved) onSaved(body);
        })
        .catch(function (err) { setStatus("保存に失敗しました: " + err.message, true); });
    });
  }

  function atlasBindingPropose(courseId, bodyEl, statusEl, onSaved) {
    if (!courseId || !bodyEl) return;
    if (statusEl) {
      statusEl.textContent = "トピックの対応案を作成中...";
      statusEl.style.color = "var(--color-text-secondary)";
    }
    apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/atlas-binding/propose", { method: "POST" })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) {
            var detail = body.detail;
            throw new Error(typeof detail === "string" ? detail : "HTTP " + res.status);
          }
          return body;
        });
      })
      .then(function (data) {
        if (statusEl) statusEl.textContent = "";
        atlasBindingRenderEditor(bodyEl, statusEl, courseId, data, onSaved);
      })
      .catch(function (err) {
        if (statusEl) {
          statusEl.textContent = "提案の取得に失敗しました: " + err.message;
          statusEl.style.color = "var(--color-text-danger, #e53935)";
        }
      });
  }

  function initAtlasBinding() {
    var select = document.getElementById("atlas-binding-course-select");
    var proposeBtn = document.getElementById("atlas-binding-propose");
    if (!select || !proposeBtn) return;
    var bodyEl = document.getElementById("atlas-binding-body");
    var statusEl = document.getElementById("atlas-binding-status");
    var coursesLoaded = false;

    function loadCourses() {
      if (coursesLoaded) return;
      apiFetch("/admin/courses")
        .then(function (res) { return res.json(); })
        .then(function (courses) {
          (courses || []).forEach(function (c) {
            var opt = document.createElement("option");
            opt.value = c.id;
            opt.textContent = c.title + " (" + c.id + ")";
            select.appendChild(opt);
          });
          coursesLoaded = true;
        })
        .catch(function () {});
    }

    proposeBtn.addEventListener("click", function () {
      if (!select.value) {
        statusEl.textContent = "コースを選択してください";
        statusEl.style.color = "var(--color-text-danger, #e53935)";
        return;
      }
      atlasBindingPropose(select.value, bodyEl, statusEl);
    });
    onTabActivate("atlas", loadCourses);
  }

  // ── 学習者体験レイヤー(B層) Stage 4 — 関心集約ダッシュボード（実集計）──────
  function initInterestDashboard() {
    var select = document.getElementById("interest-dashboard-course-select");
    var refreshBtn = document.getElementById("refresh-interest-dashboard");
    if (!select) return;

    // コース一覧を読み込んでセレクタに反映。
    apiFetch("/learning/courses")
      .then(function (res) { return res.json(); })
      .then(function (courses) {
        (courses || []).forEach(function (c) {
          var opt = document.createElement("option");
          opt.value = c.id;
          opt.textContent = c.title;
          select.appendChild(opt);
        });
      })
      .catch(function () {});

    select.addEventListener("change", loadInterestDashboard);
    if (refreshBtn) refreshBtn.addEventListener("click", loadInterestDashboard);
  }

  function loadInterestDashboard() {
    var body = document.getElementById("interest-dashboard-body");
    var select = document.getElementById("interest-dashboard-course-select");
    if (!body) return;
    var courseId = select ? select.value : "";
    if (!courseId) {
      body.innerHTML = '<div style="color:var(--color-text-tertiary)">コースを選択してください</div>';
      return;
    }
    body.innerHTML = '<div style="color:var(--color-text-tertiary)">読み込み中…</div>';
    apiFetch("/admin/interest-dashboard?course_id=" + encodeURIComponent(courseId))
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if ((data.cohort_size || 0) === 0 && (!data.hotspots || data.hotspots.length === 0)) {
          body.innerHTML = '<div style="color:var(--color-text-tertiary)">このコースにはまだ関心痕跡が記録されていません。</div>';
          return;
        }
        var hotspots = data.hotspots || [];
        var maxCount = hotspots.reduce(function (m, h) { return Math.max(m, h.interest_count || 0); }, 1);
        var html = "";
        html += '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:8px">対象クラス規模: ' +
          escHtml(String(data.cohort_size || "-")) + ' 名（個人は特定しません）</div>';

        html += '<h4 style="margin:8px 0 6px;font-size:13px">関心が集まっている領域</h4>';
        hotspots.forEach(function (h) {
          var pct = Math.round((h.interest_count || 0) / maxCount * 100);
          html += '<div class="id-hotspot">';
          html += '<div class="id-topic">' + escHtml(h.topic_title || "") +
            ' <span style="color:var(--color-text-tertiary);font-size:11px">未消化 ' +
            Math.round((h.unfinished_ratio || 0) * 100) + '%' +
            (h.learners ? ' ・ ' + escHtml(String(h.learners)) + '名' : '') + '</span></div>';
          html += '<div class="id-bar" style="width:' + pct + 'px;min-width:20px"></div>';
          html += '<div class="id-count">' + escHtml(String(h.interest_count || 0)) + '</div>';
          html += '</div>';
        });

        var us = data.unfinished_summary || {};
        html += '<h4 style="margin:14px 0 6px;font-size:13px">未消化の総量</h4>';
        html += '<div style="display:flex;gap:16px;font-size:13px">';
        html += '<div>未解決の問い: <strong>' + escHtml(String(us.open_questions || 0)) + '</strong></div>';
        html += '<div>反復した寄り道: <strong>' + escHtml(String(us.repeated_detours || 0)) + '</strong></div>';
        html += '<div>反復する誤答: <strong>' + escHtml(String(us.recurring_misconceptions || 0)) + '</strong></div>';
        html += '</div>';

        // 構造帰属ヒートマップ（Structure-Anchored Questions Stage 3）。
        // 集計対象は本人が確定した帰属のみ・n<3 のセルはサーバ側で非表示（k-匿名化）。
        // 教材改善のための可視化であり、学習者の評価には使わないこと。
        var anchors = data.anchor_heatmap || [];
        if (anchors.length > 0) {
          var maxAnchor = anchors.reduce(function (m, a) { return Math.max(m, a.count || 0); }, 1);
          html += '<h4 style="margin:14px 0 6px;font-size:13px">疑問が集中している構造（どこに・どう引っかかったか）</h4>';
          anchors.forEach(function (a) {
            var where = a.stage
              ? a.stage + '（' + (a.anchor_type_label || "") + '）'
              : (a.anchor_type_label || a.anchor_type || "");
            var pct = Math.round((a.count || 0) / maxAnchor * 100);
            html += '<div class="id-hotspot">';
            html += '<div class="id-topic">' + escHtml(a.topic_title || "") +
              ' — ' + escHtml(where) +
              ' <span style="color:var(--color-text-tertiary);font-size:11px">' +
              escHtml(a.doubt_type_label || "") +
              (a.learners ? ' ・ ' + escHtml(String(a.learners)) + '名' : '') + '</span></div>';
            html += '<div class="id-bar" style="width:' + pct + 'px;min-width:20px"></div>';
            html += '<div class="id-count">' + escHtml(String(a.count || 0)) + '</div>';
            html += '</div>';
          });
          html += '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:4px">' +
            '本人が確定した帰属のみ・3名未満のセルは表示されません（教材改善用。評価には使いません）</div>';
        }

        body.innerHTML = html;
      })
      .catch(function () {
        body.innerHTML = '<div style="color:var(--color-text-danger)">読み込みに失敗しました</div>';
      });
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
    // 動的タブはグループのプルダウンメニューへ入れる（無ければタブバー直下にフォールバック）
    var opsMenu = document.getElementById("admin-tab-menu-ops") || tabsEl;
    var knowledgeMenu = document.getElementById("admin-tab-menu-knowledge") || tabsEl;

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
      studentTab.setAttribute("role", "menuitem");
      studentTab.textContent = "学生管理";
      opsMenu.appendChild(studentTab);
    }

    // Show teacher management tab for SYSTEM_ADMIN only
    if (state.role === "SYSTEM_ADMIN") {
      var teacherTab = document.createElement("button");
      teacherTab.className = "admin-tab";
      teacherTab.dataset.tab = "teachers";
      teacherTab.setAttribute("role", "menuitem");
      teacherTab.textContent = "教員管理";
      opsMenu.appendChild(teacherTab);

      var ssTabBtn = document.getElementById("tab-btn-system-stats");
      if (ssTabBtn) ssTabBtn.style.display = "";
    }

    // Show schema evolution tab for TEACHER/SYSTEM_ADMIN
    if (state.role === "TEACHER" || state.role === "SYSTEM_ADMIN") {
      var schemaTab = document.createElement("button");
      schemaTab.className = "admin-tab";
      schemaTab.dataset.tab = "schema";
      schemaTab.setAttribute("role", "menuitem");
      schemaTab.textContent = "スキーマ管理";
      knowledgeMenu.appendChild(schemaTab);
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

    // 役割ごとの表示調整が終わったら、タブが1つも無いグループの見出しを隠す
    refreshTabGroupVisibility();

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

    // 学習マップ編集への導線（既存の「分野の地図」タブの学習マップ編集セクションを開く）。
    var atlasBindingOpenBtn = document.getElementById("cm-atlas-binding-open");
    if (atlasBindingOpenBtn) {
      atlasBindingOpenBtn.addEventListener("click", function () {
        activateTabView("atlas");
        var section = document.getElementById("atlas-binding-section");
        if (section) {
          try { section.scrollIntoView({ behavior: "smooth", block: "start" }); }
          catch (e) { section.scrollIntoView(); }
        }
      });
    }
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
        actionHtml = '<button class="cm-manage-btn admin-action-btn" data-course-id="' + escHtml(c.id) + '" data-course-title="' + escHtml(c.title) + '">共有設定</button>' +
          ' <button class="cm-version-btn admin-action-btn" data-course-id="' + escHtml(c.id) + '" data-course-title="' + escHtml(c.title) + '">共有版</button>';
      } else {
        actionHtml = '<span style="font-size:11px;color:var(--color-text-tertiary)">所有者のみ変更可</span>';
      }
      html += '<tr data-course-id="' + escHtml(c.id) + '">' +
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
    // V層: コースの共有バージョン管理
    tbody.querySelectorAll(".cm-version-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (window.Versioning) {
          window.Versioning.openModal("course", this.getAttribute("data-course-id"), this.getAttribute("data-course-title"));
        }
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

  // ── 機能1: ドキュメント（解析成果）のグループ共有（migration 035）─────────
  // 教材管理タブの「共有」ボタンから開く。コース共有モーダルと同型。
  var _docShareState = { documentId: null, title: "", perms: [], groups: [] };

  function openDocumentShareModal(documentId, title) {
    _docShareState.documentId = documentId;
    _docShareState.title = title || "";

    var existing = document.getElementById("doc-share-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "doc-share-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:24px;min-width:480px;max-width:640px;max-height:80vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">解析成果のグループ共有</h3>' +
          '<button id="doc-share-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 12px">教材「' + escHtml(title || "") + '」の解析成果（理論コンポーネント・グラフ・Claim）を指定グループへ共有します。viewer=閲覧・引用／editor=編集・再解析。</p>' +
        '<div id="doc-share-status" class="upload-status" style="display:none;margin-bottom:12px"></div>' +
        '<h4 style="font-size:13px;margin:0 0 8px 0;color:var(--color-text-secondary)">現在の共有設定</h4>' +
        '<div id="doc-share-current" style="margin-bottom:16px;overflow-y:auto;flex:1"></div>' +
        '<hr style="border:none;border-top:1px solid var(--color-border);margin:4px 0 12px 0">' +
        '<h4 style="font-size:13px;margin:0 0 8px 0;color:var(--color-text-secondary)">グループを追加</h4>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
          '<select id="doc-share-group" style="flex:1;padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary)"></select>' +
          '<select id="doc-share-role" style="padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary)">' +
            '<option value="viewer">viewer (閲覧・引用)</option>' +
            '<option value="editor">editor (編集・再解析)</option>' +
          '</select>' +
          '<button id="doc-share-add" class="admin-action-btn" style="background:var(--color-text-info);color:#fff">追加</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("doc-share-close").addEventListener("click", function () { overlay.remove(); });
    document.getElementById("doc-share-add").addEventListener("click", addDocumentShare);

    loadDocumentShareModal();
  }

  function setDocShareStatus(msg, kind) {
    var el = document.getElementById("doc-share-status");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (kind || "info");
  }

  function loadDocumentShareModal() {
    var docId = _docShareState.documentId;
    Promise.all([
      apiFetch("/admin/documents/" + encodeURIComponent(docId) + "/groups").then(function (r) { return r.json(); }),
      apiFetch("/groups").then(function (r) { return r.json(); }),
    ]).then(function (results) {
      _docShareState.perms = results[0] || [];
      _docShareState.groups = results[1] || [];
      renderDocShareCurrent();
      renderDocShareDropdown();
    }).catch(function () { setDocShareStatus("共有設定の取得に失敗しました", "error"); });
  }

  function renderDocShareCurrent() {
    var el = document.getElementById("doc-share-current");
    if (!el) return;
    if (!_docShareState.perms.length) {
      el.innerHTML = '<p style="color:var(--color-text-tertiary);font-size:12px;margin:0">まだ共有グループは設定されていません。</p>';
      return;
    }
    var html = '<table class="admin-table"><thead><tr><th>グループ</th><th>権限</th><th>操作</th></tr></thead><tbody>';
    _docShareState.perms.forEach(function (p) {
      html += '<tr><td>' + escHtml(p.group_name || p.group_id) + '</td><td>' + escHtml(p.permission) + '</td>' +
        '<td><button class="doc-share-remove admin-action-btn" data-gid="' + escHtml(p.group_id) + '" style="background:var(--color-text-danger);color:#fff">削除</button></td></tr>';
    });
    html += "</tbody></table>";
    el.innerHTML = html;
    el.querySelectorAll(".doc-share-remove").forEach(function (btn) {
      btn.addEventListener("click", function () { removeDocumentShare(this.getAttribute("data-gid")); });
    });
  }

  function renderDocShareDropdown() {
    var sel = document.getElementById("doc-share-group");
    if (!sel) return;
    var assigned = {};
    _docShareState.perms.forEach(function (p) { assigned[p.group_id] = true; });
    var html = '<option value="">グループを選択...</option>';
    _docShareState.groups.forEach(function (g) {
      var suffix = assigned[g.id] ? " (設定済み)" : "";
      html += '<option value="' + escHtml(g.id) + '">' + escHtml(g.name) + suffix + '</option>';
    });
    sel.innerHTML = html;
  }

  function addDocumentShare() {
    var groupSel = document.getElementById("doc-share-group");
    var roleSel = document.getElementById("doc-share-role");
    if (!groupSel.value) { setDocShareStatus("グループを選択してください", "error"); return; }
    setDocShareStatus("追加中...", "info");
    apiFetch("/admin/documents/" + encodeURIComponent(_docShareState.documentId) + "/groups", {
      method: "POST",
      body: JSON.stringify({ group_id: groupSel.value, permission: roleSel.value }),
    }).then(function (res) {
      if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "追加に失敗しました"); });
      return res.json();
    }).then(function () {
      setDocShareStatus("共有しました", "success");
      loadDocumentShareModal();
    }).catch(function (e) { setDocShareStatus(e.message || "追加に失敗しました", "error"); });
  }

  function removeDocumentShare(groupId) {
    setDocShareStatus("削除中...", "info");
    apiFetch("/admin/documents/" + encodeURIComponent(_docShareState.documentId) + "/groups/" + encodeURIComponent(groupId), {
      method: "DELETE",
    }).then(function (res) {
      if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "削除に失敗しました"); });
      return res.json();
    }).then(function () {
      setDocShareStatus("削除しました", "success");
      loadDocumentShareModal();
    }).catch(function (e) { setDocShareStatus(e.message || "削除に失敗しました", "error"); });
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
      if (window.LectureStudio) {
        window.LectureStudio.init({
          apiFetch: apiFetch,
          apiFetchRaw: apiFetchRaw,
          escHtml: escHtml,
          onTabActivate: onTabActivate,
          state: state,
        });
      }
      // W層（要素検討ワークスペース, Phase 0）— 「深く検討」統合パネルの起動。
      if (window.Deliberation) {
        window.Deliberation.init({ apiFetch: apiFetch, escHtml: escHtml });
      }
    }
    initStumbles();
    initAtlas();
    // D層 — 前提の地図タブ（doubt-atlas.js が本体。「分野の地図」とは別機能）
    if (window.DoubtAtlas) {
      onTabActivate("doubt-atlas", function () { window.DoubtAtlas.initTab(); });
    }
    initInterestDashboard();
    initSchemaProposals();
    initSchemaEvolution();
    initUserManagement();
    initGroups();
    initSystemStats();
    initKnowledgeLibraryTab();
    initLogout();

    // 横断ユーティリティ層（Admin Copilot）— 統合 AI アシスタント。
    // 依存を注入して疎結合に起動し、各画面の状態・点灯先フックを登録する。
    if (window.AdminAssistant) {
      window.AdminAssistant.init({
        apiFetch: apiFetch,
        state: state,
        activateTabView: activateTabView,
      });
      registerAssistantHooks();
    }

    // G層（ガイダンス層）— 状態導出型「次にやること」バッジ。AdminAssistant のすぐ後に起動する
    // （案内先の runLocatePlan / open が既に登録済みであることを前提にするため）。
    if (window.AdminNextSteps) {
      window.AdminNextSteps.init({
        apiFetch: apiFetch,
        state: state,
      });
      window.AdminNextSteps.refresh();
    }

    // V層（共有物のバージョン管理）: 依存注入 + 通知インボックス起動。
    if (window.Versioning) {
      window.Versioning.init({ apiFetch: apiFetch, state: state });
      window.Versioning.initInbox();
    }

    if (state.role !== "SYSTEM_ADMIN") {
      loadMaterials();
      document.getElementById("refresh-materials").addEventListener("click", loadMaterials);
    }
  }

  // 各画面の構造化状態（screen context）と道案内の点灯先（UI anchors）を Copilot に登録する。
  // DOM スクレイプではなく、画面が自分の状態・要素を提供する（設計 §9.2 / §9.5）。
  function registerAssistantHooks() {
    var AA = window.AdminAssistant;
    if (!AA) return;

    // --- UI アンカー（道案内の点灯先。論理ID → 実 DOM） ---
    // material_row / course_row は行単位解決: "material_row:m_123" のように param 付きで
    // 呼ばれた場合はその行を返し、param が無ければ従来どおり tbody 全体にフォールバックする。
    AA.registerUiAnchors("materials", {
      upload_dropzone: function () { return document.getElementById("upload-zone"); },
      material_row: function (id) {
        return id
          ? document.querySelector('#materials-tbody tr[data-material-id="' + id + '"]') || document.getElementById("materials-tbody")
          : document.getElementById("materials-tbody");
      },
      material_visibility_control: function () { return document.getElementById("materials-tbody"); }
    });
    AA.registerUiAnchors("course-builder", {
      cb_material_select: function () { return document.getElementById("cb-material-select"); },
      cb_chat_input: function () { return document.getElementById("cb-chat-input"); }
    });
    AA.registerUiAnchors("lecture-studio", {
      chunk_list: function () { return document.getElementById("ls-chunk-list"); },
      assistant_open_button: function () { return document.getElementById("ls-ai-assistant-btn"); },
      "ls-rewrite-prompt": function () { return document.getElementById("ls-rewrite-prompt"); },
      ls_course_select: function () { return document.getElementById("ls-course-select"); },
      ls_audio_generate: function () { return document.getElementById("ls-audio-all-btn"); }
    });
    AA.registerUiAnchors("course-management", {
      course_list: function () { return document.getElementById("cm-table"); },
      course_row: function (id) {
        return id
          ? document.querySelector('#cm-tbody tr[data-course-id="' + id + '"]') || document.getElementById("cm-tbody")
          : document.getElementById("cm-tbody");
      },
      course_visibility_control: function () { return document.getElementById("cm-tbody"); },
      publish_button: function () { return document.getElementById("cm-table"); },
      atlas_binding_button: function () { return document.getElementById("cm-atlas-binding-open"); }
    });
    AA.registerUiAnchors("atlas", {
      atlas_generate_button: function () { return document.getElementById("atlas-generate"); }
    });
    AA.registerUiAnchors("groups", {
      create_student_form: function () { return document.getElementById("tab-groups"); },
      create_teacher_form: function () { return document.getElementById("tab-groups"); }
    });

    // --- 画面コンテキスト（現在の選択・可視要素） ---
    AA.registerScreenContext("lecture-studio", function () {
      return window.LectureStudio ? window.LectureStudio.getScreenContext() : {
        selection: { chunk_id: null, course_id: null },
        visible_entities: []
      };
    });
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
        // W層「深く検討」導線: entity_id はこのレポート内では claim/component は
        // パイプライン内部の artifact id であり theory_claims/theory_components の DB id と
        // 一致する保証がない（accept 時に再生成されるため）。equation のみ独立テーブルを
        // 持たず artifact 上の equation_id をそのまま使う設計（decomposition.py）のため、
        // ここでは equation の変更にだけ導線を出す（claim/component/graph_edge は見送り）。
        var deliberateTag = (c.entity_type === "equation" && c.entity_id)
          ? ' <button class="admin-action-btn eg-rev-deliberate-btn" type="button" data-equation-id="' + escHtml(c.entity_id) + '" style="font-size:10.5px;padding:1px 6px;margin-left:6px">深く検討</button>'
          : '';
        var src = (c.source_locations || []).map(function (s) {
          return escHtml(s.section_id || s.chunk_id || JSON.stringify(s));
        }).join(", ");
        return '<li class="eg-rev-change">' +
          '<span class="eg-rev-ctype">' + escHtml(c.change_type) + '</span> ' +
          '<code>' + escHtml(c.entity_type) + ':' + escHtml(c.entity_id || "") + '</code>' + protectedTag + deliberateTag +
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
      // W層「深く検討」導線（equation のみ。上の changesHtml のコメント参照）。
      rep.querySelectorAll(".eg-rev-deliberate-btn").forEach(function (b) {
        b.addEventListener("click", function (e) {
          e.stopPropagation();
          var equationId = this.getAttribute("data-equation-id");
          if (window.Deliberation) {
            window.Deliberation.openElement("equation", equationId, {
              documentId: current.documentId,
              title: "数式 " + equationId,
            });
          }
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
