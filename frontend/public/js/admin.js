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
    materialsList: [],
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
        '<div class="auth-error" id="auth-error"></div>' +
      '</div>';
    document.body.appendChild(overlay);

    document.getElementById("auth-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var username = document.getElementById("auth-user").value.trim();
      var password = document.getElementById("auth-pass").value;
      var errEl = document.getElementById("auth-error");
      errEl.textContent = "";

      var endpoint = "/auth/login";
      var payload = { username: username, password: password };

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

  // 表示ラベルは教員向けの日本語にする（内部ステージキーは agent 実装と結び付いているため変更しない）。
  var materialPipelineStageGroups = [
    {
      label: "文書構造を読む",
      stages: [
        ["document_structure", "文書構造の復元"],
        ["paper_skeleton", "論文アウトラインの推定"],
      ],
    },
    {
      label: "論述・主張を抽出する",
      stages: [
        ["rhetorical_role", "論述の役割分類"],
        ["claim_qualification", "主張の抽出・分類"],
        ["equation_semantics", "数式の意味付け"],
      ],
    },
    {
      label: "根拠・派生関係を整理する",
      stages: [
        ["evidence_registry", "根拠の一元管理"],
        ["claim_object_builder", "主張オブジェクトの組み立て"],
        ["symbol_registry", "数式記号の整理"],
        ["derivation_chain", "導出関係の構築"],
        ["figure_table_semantics", "図表の意味復元"],
        ["apparatus_semantics", "図の装置・パーツ解析"],
      ],
    },
    {
      label: "理論コンポーネントを組み立てる",
      stages: [
        ["thesis_reconstruction", "中心命題の再構成"],
        ["dsl_linking", "概念グラフへの接続"],
        ["component_assembly", "理論コンポーネントの組み立て"],
        ["component_graph", "理論操作グラフの構築"],
        ["narrative_annotator", "説明注釈の付与"],
      ],
    },
    {
      label: "コース化・出力を準備する",
      stages: [
        ["course_mapping", "コース項目への対応付け"],
        ["blueprint", "コース設計案の生成"],
        ["export_validation", "整合性の最終チェック"],
      ],
    },
  ];
  var materialPipelineStages = materialPipelineStageGroups.reduce(function (stages, group) {
    return stages.concat(group.stages);
  }, []);

  function renderMaterials(materials) {
    var tbody = document.getElementById("materials-tbody");
    // 解析再開モーダルが直近の analysis_options を復元できるよう、一覧取得のたびにキャッシュする。
    state.materialsList = materials || [];
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
      // N36: 開示範囲（Public/Group/Private）をコース側（G5-2 の courseVisibilityBadgeHtml）と
      // 対称のバッジ様式でインライン表示する（変更は従来どおり「共有設定」モーダルから）。
      html += "<td>" + escHtml(m.title) +
        '<div style="margin-top:2px">' + courseVisibilityBadgeHtml({ visibility: m.visibility || "private" }) + '</div></td>';
      var hasPdf = m.has_pdf === true;
      var chunkCount = typeof m.chunk_count === "number" ? m.chunk_count : null;
      // status=failed のみ PDF 再登録を促す。縮退(completed)はチャンクが存在するため不要
      var pdfRegistrationFailed = m.status === "failed" && chunkCount === 0;
      var pdfBtnLabel = pdfRegistrationFailed ? "失敗 (PDF再登録)" : (hasPdf ? "PDF再登録（登録済）" : "PDF再登録");
      var pdfBtnClass = pdfRegistrationFailed ? " admin-pdf-reupload-btn-failed" : "";
      var pdfBtnTitle = pdfRegistrationFailed
        ? "チャンク作成に失敗しました。PDFを再登録してください"
        : "PDFのみ再登録";
      var canResume = m.status === "failed" && !!m.document_id;

      html += '<td><span class="admin-status ' + statusClass + '" title="' +
        (isDegraded ? "利用可能な機能: " + availableFeatures.join(", ") + "\n機能制限中のステージ: " + degradedStages.join(", ") : "") +
        '">' + statusLabel + "</span>";
      if (isDegraded) {
        html += '<div class="admin-degraded-hint" style="font-size:11px;color:var(--color-text-warning,#c85a00);margin-top:2px;">' +
          '⚠ 一部の高度な機能は制限されています。RAGチャットは利用可能です。' +
          (retryStage ? ' <a href="#" class="admin-retry-stage-link" data-ui-anchor="materials.row-retry-stage" data-material-id="' + escHtml(m.material_id) + '" data-stage="' + escHtml(retryStage) + '" style="text-decoration:underline;cursor:pointer;">ステージ再実行</a>' : '') +
          '</div>';
      }
      // 異常時の導線は「⋯」メニューに畳まない（admin_ux_issues_2026-08-01.md §2.3-3）。
      // 解析失敗・PDF再登録要の行では、ステータス列にも同じ操作をインラインリンクで出す
      // （クリック挙動はメニュー項目と同一ハンドラ。data-ui-anchor はメニュー項目側が担体）。
      var failedLinks = "";
      if (canResume) {
        failedLinks += '<button type="button" class="admin-inline-link admin-resume-analysis-inline" data-document-id="' + escHtml(m.document_id) + '" data-filename="' + escHtml(m.filename || m.title || "教材") + '">解析を再開</button>';
      }
      if (pdfRegistrationFailed) {
        if (failedLinks) failedLinks += " / ";
        failedLinks += '<button type="button" class="admin-inline-link admin-pdf-reupload-inline" data-material-id="' + escHtml(m.material_id) + '" data-reset-label="PDFを再登録">PDFを再登録</button>';
      }
      if (failedLinks) {
        html += '<div class="admin-failed-hint">' + failedLinks + '</div>';
      }
      html += "</td>";
      html += "<td>" + escHtml(uploadedAt) + "</td>";
      var resumeBtn = canResume
        ? '<button class="ls-menu-item admin-resume-analysis-btn" type="button" data-ui-anchor="materials.row-resume-analysis" data-document-id="' + escHtml(m.document_id) + '" data-filename="' + escHtml(m.filename || m.title || "教材") + '" title="保存済みPDFから解析を再開">解析再開</button>'
        : "";
      // 機能1: 解析成果のグループ共有 + 開示範囲（G1-1）（document_id が必要）
      var shareBtn = m.document_id
        ? '<button class="ls-menu-item admin-share-doc-btn" type="button" data-ui-anchor="materials.row-share" data-document-id="' + escHtml(m.document_id) + '" data-material-id="' + escHtml(m.material_id || "") + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" data-visibility="' + escHtml(m.visibility || "private") + '" data-group-id="' + escHtml(m.group_id || "") + '" title="グループ共有・開示範囲（公開/グループ限定/非公開）を設定します">共有設定…</button>'
        : "";
      // V層: 共有版（リリース）の発行・履歴・削除予約（document_id が必要。上の「共有設定」とは別機能）
      var versionBtn = m.document_id
        ? '<button class="ls-menu-item admin-version-doc-btn" type="button" data-ui-anchor="materials.row-version" data-document-id="' + escHtml(m.document_id) + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" title="解析成果を版（リリース）として発行・履歴管理します（共有設定とは別機能）">版の管理…</button>'
        : "";
      // 知識ランドスケープ（migration 065）: この論文が分野マップのどこに位置づくかの
      // 配置候補（AI推定）の確認・却下・再検討（document_id が必要）
      var landscapeBtn = m.document_id
        ? '<button class="ls-menu-item admin-landscape-doc-btn" type="button" data-ui-anchor="materials.row-landscape" data-document-id="' + escHtml(m.document_id) + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" title="この論文が分野マップ（基準地図）のどこに位置づくかのAI候補を確認・却下・再検討します">位置づけ（分野マップ）…</button>'
        : "";
      // ゼミ前ブリーフ（seminar_brief_mirroring_design.md §1）: 輪講の前にこの論文の
      // 「賭け金」（脆い前提・一点吊りの支持線・晴れ間）を10分で把握する read-only
      // 合成ビュー（新テーブル・新LLMゼロ、SB1。document_id が必要）
      var seminarBriefBtn = m.document_id
        ? '<button class="ls-menu-item admin-seminar-brief-btn" type="button" data-ui-anchor="materials.row-seminar-brief" data-document-id="' + escHtml(m.document_id) + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" title="輪講の前に、この論文の脆い前提・一点吊りの支持線・晴れ間を確認します（読み取り専用）">ゼミ前ブリーフ…</button>'
        : "";
      // 画像読み取りパイプライン（migration 041）: 抽出された図・画像を表示（document_id が必要）
      var figuresBtn = m.document_id
        ? '<button class="ls-menu-item admin-figures-btn" type="button" data-ui-anchor="materials.row-figures" data-document-id="' + escHtml(m.document_id) + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" title="抽出された図・画像を表示">図・画像</button>'
        : "";
      // 要素インベントリ（W層の教材単位の統合入口）: この教材からパイプラインが検出した
      // theory_component / theory_claim / equation / figure の全件を一覧表示する
      // （document_id が必要。図・画像ボタンと同条件・その隣に並べる）。
      var inventoryBtn = m.document_id
        ? '<button class="ls-menu-item admin-inventory-btn" type="button" data-ui-anchor="materials.row-inventory" data-document-id="' + escHtml(m.document_id) + '" data-title="' + escHtml(m.title || m.filename || "教材") + '" title="この教材からパイプラインが検出した要素の一覧を表示">検出要素</button>'
        : "";
      // U層（LLM使用量推計, migration 043）: 解析前の事前トークン見積り（TEACHER・レンジのみ・金額なし, G2-U）
      var estimateBtn = m.material_id
        ? '<button class="ls-menu-item admin-estimate-btn" type="button" data-ui-anchor="materials.row-estimate" data-material-id="' + escHtml(m.material_id) + '" title="解析パイプラインが使うトークン量の目安をレンジで表示します（金額は表示されません）">解析コスト見積り…</button>'
        : "";
      var pdfBtn = '<button class="ls-menu-item admin-pdf-reupload-btn' + pdfBtnClass + '" type="button" data-ui-anchor="materials.row-pdf-reupload" data-material-id="' + escHtml(m.material_id) + '" title="' + escHtml(pdfBtnTitle) + '">' + pdfBtnLabel + '</button>';
      var deleteBtn = '<button class="ls-menu-item ls-menu-item-danger admin-delete-btn" type="button" data-ui-anchor="materials.row-delete" data-material-id="' + escHtml(m.material_id) + '" data-material-title="' + escHtml(m.title) + '" title="この教材と紐づく解析成果・コースを削除します">削除…</button>';
      // §2.3: 行に出しっぱなしにするのは「パイプラインを実行 ▼」と「⋯」の2つだけ。
      html += '<td><div class="materials-action-cell">' +
        materialPipelineMenuHtml(m) +
        '<div class="material-more-menu ls-action-menu" data-material-id="' + escHtml(m.material_id) + '">' +
          '<button class="admin-action-btn ls-menu-trigger material-more-trigger" type="button" data-ui-anchor="materials.row-more-menu" title="この教材のその他の操作" aria-label="その他の操作">⋯</button>' +
          '<div class="ls-menu material-more-panel" hidden>' +
            figuresBtn +
            inventoryBtn +
            shareBtn +
            versionBtn +
            landscapeBtn +
            seminarBriefBtn +
            estimateBtn +
            pdfBtn +
            resumeBtn +
            '<div class="ls-menu-divider"></div>' +
            deleteBtn +
          '</div>' +
        '</div>' +
        '</div></td>';
      html += "</tr>";
    });
    tbody.innerHTML = html;
    materials.forEach(function (m) {
      var mid = m.material_id || "";
      if (mid) loadMaterialPipelineStatus(mid);
    });
    bindMaterialPipelineMenus(tbody);
    bindMaterialMoreMenus(tbody);

    // Attach delete handlers
    tbody.querySelectorAll(".admin-delete-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mid = this.getAttribute("data-material-id");
        var title = this.getAttribute("data-material-title");
        openDeleteConfirmModal("material", mid, title);
      });
    });

    // 機能1: 解析成果のグループ共有 + 開示範囲ボタン
    tbody.querySelectorAll(".admin-share-doc-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openDocumentShareModal(
          this.getAttribute("data-document-id"),
          this.getAttribute("data-title"),
          this.getAttribute("data-material-id"),
          this.getAttribute("data-visibility"),
          this.getAttribute("data-group-id")
        );
      });
    });

    // 画像読み取りパイプライン（migration 041）: 図・画像一覧モーダル
    tbody.querySelectorAll(".admin-figures-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openFiguresModal(this.getAttribute("data-document-id"), this.getAttribute("data-title"));
      });
    });

    // 要素インベントリ（検出要素の一覧）
    tbody.querySelectorAll(".admin-inventory-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (window.Deliberation) {
          window.Deliberation.openInventory(this.getAttribute("data-document-id"), {
            title: this.getAttribute("data-title")
          });
        }
      });
    });

    // 知識ランドスケープ（migration 065）: 位置づけ（分野マップ）レビューモーダル
    tbody.querySelectorAll(".admin-landscape-doc-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openLandscapeModal(this.getAttribute("data-document-id"), this.getAttribute("data-title"));
      });
    });

    // ゼミ前ブリーフ（seminar_brief_mirroring_design.md §1）: read-only 合成ビューのモーダル
    tbody.querySelectorAll(".admin-seminar-brief-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openSeminarBriefModal(this.getAttribute("data-document-id"), this.getAttribute("data-title"));
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

    // U層（G2-U）: 解析コスト見積り（レンジのみ・金額なし）ポップオーバー
    tbody.querySelectorAll(".admin-estimate-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (window.AdminLlmUsage) {
          window.AdminLlmUsage.openEstimatePopover(this.getAttribute("data-material-id"), this);
        }
      });
    });

    // Attach PDF re-upload handlers
    // 「⋯」メニュー項目（.admin-pdf-reupload-btn）と、失敗行のステータス列インライン導線
    // （.admin-pdf-reupload-inline, §2.3-3）の両方に同一の処理を張る。
    tbody.querySelectorAll(".admin-pdf-reupload-btn, .admin-pdf-reupload-inline").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mid = this.getAttribute("data-material-id");
        var resetLabel = this.getAttribute("data-reset-label") || "PDF再登録";
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
              setTimeout(function () { btn.textContent = resetLabel; btn.disabled = false; }, 2000);
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

    // 「⋯」メニュー項目と、失敗行のステータス列インライン導線（§2.3-3）の両方。
    tbody.querySelectorAll(".admin-resume-analysis-btn, .admin-resume-analysis-inline").forEach(function (btn) {
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
        openDangerConfirmModal({
          title: "ステージの再実行",
          message: "ステージ「" + stage + "」を再実行します。既存の結果は上書きされます。よろしいですか？",
          confirmLabel: "再実行する",
        }, function () {
          runMaterialPipeline(materialId, stage);
        });
      });
    });
  }

  function materialPipelineMenuHtml(material) {
    var mid = escHtml(material.material_id || "");
    var disabled = material.document_id ? "" : " disabled";
    var html =
      '<div class="material-pipeline-menu ls-action-menu" data-ui-anchor="materials.row-pipeline-run" data-material-id="' + mid + '">' +
        '<button class="admin-action-btn ls-menu-trigger material-pipeline-trigger" data-ui-anchor="materials.row-pipeline-run" type="button" data-material-id="' + mid + '"' + disabled + '>' +
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
        var materialId = menu.getAttribute("data-material-id");
        var stage = this.getAttribute("data-stage") || "";
        openDangerConfirmModal({
          title: "パイプラインの再実行",
          message: "既存の実行結果を上書きします。本当に実行しますか？",
          confirmLabel: "実行する",
        }, function () {
          runMaterialPipeline(materialId, stage);
        });
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

  // §2.3: 教材行の「⋯」メニュー（図・画像 / 検出要素 / 共有設定 / 版の管理 /
  // 位置づけ（分野マップ）/ 解析コスト見積り / PDF再登録 / 解析再開 / 区切り線 /
  // 削除）。開閉と外側クリックの
  // 挙動はパイプラインメニューと共通（closeMaterialPipelineMenus / 下の capture リスナ）。
  function bindMaterialMoreMenus(root) {
    root.querySelectorAll(".material-more-trigger").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var menu = this.closest(".material-more-menu");
        var panel = menu && menu.querySelector(".material-more-panel");
        if (!panel) return;
        var willOpen = panel.hidden;
        document.querySelectorAll(".ls-menu").forEach(function (m) { m.hidden = true; });
        panel.hidden = !willOpen;
      });
    });
    root.querySelectorAll(".material-more-panel").forEach(function (panel) {
      panel.addEventListener("click", function (e) {
        e.stopPropagation();
        var item = e.target && e.target.closest ? e.target.closest(".ls-menu-item") : null;
        if (!item) return;
        // PDF再登録は項目ラベル上で進捗（登録中... / 完了 / 失敗）を返すため開いたままにする。
        if (item.classList.contains("admin-pdf-reupload-btn")) return;
        panel.hidden = true;
      });
    });
  }

  function closeMaterialPipelineMenus() {
    // 行のドロップダウン（パイプライン実行 / ⋯）をまとめて閉じる。
    document.querySelectorAll(".material-pipeline-panel, .material-more-panel").forEach(function (panel) {
      panel.hidden = true;
    });
  }

  function initMaterialPipelineOutsideClick() {
    if (state.materialPipelineOutsideClickBound) return;
    state.materialPipelineOutsideClickBound = true;
    document.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest(".material-pipeline-menu")) return;
      if (e.target && e.target.closest && e.target.closest(".material-more-menu")) return;
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
    overlay.setAttribute("data-ui-anchor", "header.delete-confirm");
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
          '<button id="delete-exec-btn" data-ui-anchor="header.delete-confirm" style="padding:6px 16px;border:none;border-radius:4px;background:var(--color-text-danger);color:#fff;cursor:pointer;font-size:13px" disabled>削除する</button>' +
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

  // ── Danger Confirmation Modal (G5-3) ────────────────────────────────
  // 不可逆・破壊的だが「名前入力」までは不要な操作向けの共通確認モーダル。
  // ブラウザ標準の confirm() は反射的に押し抜けてしまいがちなため、
  // 明示のボタンクリックを要求する「2段確認」に統一する（削除系は
  // openDeleteConfirmModal の名前入力必須モーダルを引き続き使う）。
  function openDangerConfirmModal(opts, onConfirm) {
    opts = opts || {};
    var existing = document.getElementById("danger-confirm-modal");
    if (existing) existing.remove();

    var messages = Array.isArray(opts.message) ? opts.message : [opts.message];
    var messagesHtml = messages.filter(function (m) { return !!m; }).map(function (m) {
      return '<p style="font-size:13px;color:var(--color-text-primary);margin:0 0 8px;white-space:pre-wrap">' + escHtml(m) + '</p>';
    }).join("");

    var overlay = document.createElement("div");
    overlay.id = "danger-confirm-modal";
    overlay.setAttribute("data-ui-anchor", "header.danger-confirm");
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";

    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border-secondary);border-radius:8px;padding:24px;min-width:380px;max-width:480px">' +
        '<h3 style="margin:0 0 12px;font-size:16px;color:var(--color-text-danger)">' + escHtml(opts.title || "確認") + '</h3>' +
        messagesHtml +
        '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">' +
          '<button id="danger-confirm-cancel-btn" style="padding:6px 16px;border:1px solid var(--color-border);border-radius:4px;background:none;color:var(--color-text-secondary);cursor:pointer;font-size:13px">' + escHtml(opts.cancelLabel || "キャンセル") + '</button>' +
          '<button id="danger-confirm-exec-btn" style="padding:6px 16px;border:none;border-radius:4px;background:var(--color-text-danger);color:#fff;cursor:pointer;font-size:13px">' + escHtml(opts.confirmLabel || "実行する") + '</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    document.getElementById("danger-confirm-cancel-btn").addEventListener("click", function () {
      overlay.remove();
    });
    document.getElementById("danger-confirm-exec-btn").addEventListener("click", function () {
      overlay.remove();
      if (typeof onConfirm === "function") onConfirm();
    });
  }

  // 他モジュール（versioning.js 等）から同じ2段確認モーダルを再利用できるように公開する。
  // 生の window.confirm() をモジュールごとに個別実装しないための共通導線。
  window.AdminDangerConfirm = { open: openDangerConfirmModal };

  // ── 画像読み取りパイプライン（migration 041）— 再解析オプション ─────────
  // モーダルは document_id しか受け取れないため、直近の /admin/materials 一覧
  // キャッシュ（state.materialsList）から material を逆引きする。
  function _findMaterialByDocumentId(docId) {
    var list = state.materialsList || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].document_id === docId) return list[i];
    }
    return null;
  }

  // 「解析再開」ボタンのフローにもアップロード時と同じチェックボックスを出す。
  function openReanalyzeOptionsModal(docId, filename, triggerBtn) {
    var existing = document.getElementById("reanalyze-options-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "reanalyze-options-modal";
    overlay.setAttribute("data-ui-anchor", "materials.reanalyze-modal");
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:360px;max-width:440px">' +
        '<h3 style="margin:0 0 12px;font-size:15px;color:var(--color-text-primary)">解析を再開</h3>' +
        '<p style="font-size:12.5px;color:var(--color-text-secondary);margin:0 0 12px">「' + escHtml(filename) + '」の解析を再開します。</p>' +
        '<label style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--color-text-secondary);margin-bottom:8px">' +
          '<input type="checkbox" id="reanalyze-analyze-images">' +
          '図面・画像を解析する（装置図の同定に vision AI を使用）' +
        '</label>' +
        '<div id="reanalyze-llm-model-row" style="margin-bottom:16px"></div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button id="reanalyze-cancel-btn" class="admin-action-btn">キャンセル</button>' +
          '<button id="reanalyze-confirm-btn" class="admin-action-btn" style="background:var(--color-text-success);color:#fff">再開する</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    // 前回の解析オプション（analysis_options）があればチェック状態を復元する（防御的）。
    var lastMaterial = _findMaterialByDocumentId(docId);
    var lastOpts = lastMaterial && lastMaterial.analysis_options;
    var analyzeImagesCheckbox = document.getElementById("reanalyze-analyze-images");
    if (analyzeImagesCheckbox) analyzeImagesCheckbox.checked = !!(lastOpts && lastOpts.analyze_images);

    // M層（LLM モデル選択, migration 061）: 前回値を表示し「変更」で選び直せるようにする。
    // docId は静かな計器（コスト見通しの一行, teacher_triage_instruments_design.md §3.1）
    // の document 版 forecast 用。
    if (window.AdminLlmModels) {
      var llmModelRow = document.getElementById("reanalyze-llm-model-row");
      if (llmModelRow) window.AdminLlmModels.initReanalyzePanel(llmModelRow, lastOpts, docId);
    }

    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("reanalyze-cancel-btn").addEventListener("click", function () { overlay.remove(); });
    document.getElementById("reanalyze-confirm-btn").addEventListener("click", function () {
      var analyzeImages = document.getElementById("reanalyze-analyze-images").checked;
      var llmModels = window.AdminLlmModels ? window.AdminLlmModels.getReanalyzeModels(lastOpts, analyzeImages) : null;
      // N6残: 前回 run が analyze_images=true で、今回チェックを外して実行する場合は
      // 未レビューの AI 図分類・装置候補が失われることを明示確認してから実行する
      // （明示 OFF はユーザーの意思なのでブロックはしないが、警告なしには通さない）。
      var prevAnalyzeImages = !!(lastOpts && lastOpts.analyze_images);
      if (prevAnalyzeImages && !analyzeImages) {
        var confirmBtn = document.getElementById("reanalyze-confirm-btn");
        if (confirmBtn) confirmBtn.disabled = true; // 件数取得中の二重クリック防止
        _confirmExplicitImagesOff(docId, function () {
          overlay.remove();
          performReanalyze(docId, filename, triggerBtn, analyzeImages, llmModels);
        }, function () {
          if (confirmBtn) confirmBtn.disabled = false; // キャンセル時は選び直せる
        });
        return;
      }
      overlay.remove();
      performReanalyze(docId, filename, triggerBtn, analyzeImages, llmModels);
    });
  }

  // N6残: 明示 OFF 再解析の確認。未レビュー件数 n の取得を試み、取得失敗時も
  // 件数なしの汎用警告文で必ず確認を出す（警告自体を落とさない）。
  function _confirmExplicitImagesOff(docId, onConfirm, onCancel) {
    function openConfirm(unreviewedCount) {
      var messages = [
        "前回の解析では図・画像の解析が有効でした。図・画像の解析を無効にして再解析すると、未レビューのAI図分類・装置候補が失われます。",
      ];
      if (typeof unreviewedCount === "number" && unreviewedCount > 0) {
        messages.push("未レビューのAI分類 " + unreviewedCount + " 件が対象です。");
      }
      messages.push("よろしいですか？");
      openDangerConfirmModal({
        title: "図・画像の解析を無効にして再解析",
        message: messages,
        confirmLabel: "再解析する",
      }, onConfirm);
      // openDangerConfirmModal はキャンセルコールバックを持たないため、
      // キャンセル系（キャンセルボタン・背景クリック）はモーダル消滅の監視で拾う。
      var dangerModal = document.getElementById("danger-confirm-modal");
      if (dangerModal && typeof onCancel === "function") {
        var cancelBtn = dangerModal.querySelector("#danger-confirm-cancel-btn");
        if (cancelBtn) cancelBtn.addEventListener("click", onCancel);
        dangerModal.addEventListener("click", function (e) {
          if (e.target === dangerModal) onCancel();
        });
      }
    }
    apiFetch("/admin/documents/" + encodeURIComponent(docId) + "/figures")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        var figures = (data && data.figures) || [];
        // 未レビュー = AI 分類（suggested_mode）または装置候補があり、教員レビュー
        // （reviewed_mode / mode_review_status='reviewed'）が済んでいない図。
        var count = 0;
        figures.forEach(function (f) {
          var hasAiOutput = (f.suggested_mode && f.suggested_mode !== "unknown") ||
            ((f.apparatus_candidates || []).length > 0);
          var isReviewed = f.mode_review_status === "reviewed" || !!f.reviewed_mode;
          if (hasAiOutput && !isReviewed) count++;
        });
        openConfirm(count);
      })
      .catch(function () {
        // 件数が取れなくても警告は必ず出す（fail-closed 側に倒す）。
        openConfirm(null);
      });
  }

  function performReanalyze(docId, filename, btn, analyzeImages, models) {
    if (btn) { btn.disabled = true; btn.textContent = "再開中..."; }
    var body = { analyze_images: !!analyzeImages };
    // M層（LLM モデル選択, migration 061）: 未指定（null）なら前回 run の options から自動継承される。
    if (models) body.models = models;
    apiFetch("/admin/documents/" + docId + "/reanalyze", {
      method: "POST",
      body: JSON.stringify(body),
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
  // viewerIsOwner: 図一覧APIレスポンスの viewer_is_owner（元教材の所有者か）。
  // ライブラリ昇格モーダルの例示画像チェック（fail-closed）に使う。
  var _figuresModalState = { documentId: null, title: "", figures: [], objectUrls: [], viewerIsOwner: false };

  function _figuresRevokeObjectUrls() {
    _figuresModalState.objectUrls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (e) { /* noop */ } });
    _figuresModalState.objectUrls = [];
  }

  function openFiguresModal(documentId, title) {
    _figuresRevokeObjectUrls();
    _figuresModalState.documentId = documentId;
    _figuresModalState.title = title || "";
    _figuresModalState.figures = [];
    _figuresModalState.viewerIsOwner = false;

    var existing = document.getElementById("figures-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "figures-modal";
    overlay.setAttribute("data-ui-anchor", "materials.figures-modal");
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
        // fail-closed: レスポンスに無ければ所有者ではない扱い。
        _figuresModalState.viewerIsOwner = !!(data && data.viewer_is_owner);
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
            ' <button class="admin-action-btn figure-deliberate-btn" type="button" data-ui-anchor="materials.figure-deliberate" data-figure-id="' + escHtml(fig.id) + '" style="font-size:11px;padding:2px 8px;margin-left:4px">深く検討</button>' +
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
          viewerIsOwner: _figuresModalState.viewerIsOwner,
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
          ? '<button class="admin-action-btn figure-overlay-btn" type="button" data-ui-anchor="materials.figure-overlay" data-figure-id="' + escHtml(fig.id) + '" data-candidate-idx="' + i + '" style="font-size:11.5px;padding:2px 8px">図で確認</button>'
          : "") +
        '<span style="flex:1"></span>' +
        '<button class="admin-action-btn figure-promote-btn" data-ui-anchor="materials.figure-promote" data-figure-id="' + escHtml(fig.id) + '" data-candidate-idx="' + i + '" style="font-size:11.5px;padding:2px 8px">ライブラリへ昇格</button>' +
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
    // N28: 装置候補の connections も転記対象にする（昇格で接続情報を落とさない P4）。
    // confidence は AI 内部値のため転記しない（body は教員の共同財。reason は根拠として残す）。
    var connections = ((candidate && candidate.connections) || []).map(function (c) {
      return {
        from_part: c.from_part || "",
        to_part: c.to_part || "",
        relation: c.relation || "",
        reason: c.reason || "",
      };
    });
    return {
      typical_parts: parts.map(function (p) { return { name: p.name || "", role: p.role || "" }; }),
      visual_cues: "",
      typical_configurations: "",
      measurement_targets: "",
      connections: connections,
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
    // N28: connections は v1 では編集フォームを持たないため、submit 時に転記できるよう
    // モーダルコンテキストに保持する（新規作成時のみ。統合は provenance 追記のみ）。
    _libEntryModalCtx.connections = defaultBody.connections || [];
    // fail-closed: viewer_is_owner が真であることも必須にする（未取得・false ならチェックボックス自体を出さない）。
    var canOfferImage = !!(opts.figureId && opts.documentId && opts.viewerIsOwner);

    var overlay = document.createElement("div");
    overlay.id = "library-entry-modal";
    overlay.setAttribute("data-ui-anchor", "materials.library-entry-modal");
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
        // N27: 既存カートリッジ・既存ライブラリ分野を datalist で提示する（自由入力も可能な複合方式）。
        // フリーテキストのみだと typo の domain_key が検索・retrieval から孤立するため。
        '<input type="text" id="lib-entry-domain" list="lib-entry-domain-options" placeholder="例: particle_physics" style="width:100%;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box">' +
        '<datalist id="lib-entry-domain-options"></datalist>' +

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

          // N28: 候補の接続情報の転記予告（編集 UI は v1 なし。統合＝provenance 追記では転記されない）。
          (defaultBody.connections.length
            ? ('<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:10px">接続 ' + defaultBody.connections.length + ' 本を候補から転記します（新規作成時のみ。統合では転記されません）: ' +
                defaultBody.connections.map(function (c) {
                  return escHtml((c.from_part || "?") + " → " + (c.to_part || "?") + (c.relation ? "（" + c.relation + "）" : ""));
                }).join("、") +
              '</div>')
            : '') +
        '</div>' +

        '<div id="lib-entry-body-theory" style="display:none">' +
          '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">body (JSON、任意)</label>' +
          '<textarea id="lib-entry-body-json" rows="4" placeholder="{}" style="width:100%;padding:5px 8px;font-size:12px;font-family:monospace;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary);margin-bottom:10px;box-sizing:border-box"></textarea>' +
        '</div>' +

        (canOfferImage
          ? ('<label data-ui-anchor="materials.library-entry-merge-target" style="display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--color-text-secondary);margin-bottom:4px">' +
              '<input type="checkbox" id="lib-entry-include-image"> 例示画像を含める（元教材の所有者のみ有効）' +
            '</label>' +
            '<div id="lib-entry-image-warning" style="display:none;font-size:11.5px;color:var(--color-text-warning);background:#fff6e6;border:1px solid var(--color-text-warning);border-radius:4px;padding:5px 7px;margin-bottom:10px">この画像はライブラリを閲覧できる全教員に表示されます。元教材の所有者以外がこの操作を行うと拒否されます。</div>' +
            '<div id="lib-entry-image-merge-note" style="display:none;font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:10px">統合では例示画像は引き継がれません（新規作成時のみ）</div>')
          : "") +

        '<div data-ui-anchor="materials.library-entry-merge-target" style="border-top:1px solid var(--color-border-tertiary);padding-top:10px;margin-top:4px">' +
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

    // N27: datalist に既存カートリッジ + 既存ライブラリ分野を提示する（fail-soft）。
    _libraryPopulateDomainOptions();

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
      // 種別が変わると類似候補（統合先）の母集団も変わるため再検索する。
      _libraryTriggerSimilarSearch();
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

    // 統合先ラジオ（新規作成 + _renderLibrarySimilarResults が動的追加する候補）は
    // イベント委譲で拾う。統合を選ぶと例示画像は引き継がれないため、無効化+チェック
    // 解除してその旨を注記する（サイレント破棄をやめ正直に伝える。§6-4 修正7）。
    overlay.addEventListener("change", function (e) {
      if (e.target && e.target.name === "lib-entry-merge-target") {
        _libraryUpdateImageCheckboxForMergeTarget();
      }
    });

    document.getElementById("lib-entry-submit").addEventListener("click", submitLibraryEntry);
  }

  function _libraryUpdateImageCheckboxForMergeTarget() {
    var includeImageEl = document.getElementById("lib-entry-include-image");
    if (!includeImageEl) return; // canOfferImage が false のときはチェックボックス自体が無い
    var mergeTargetEl = document.querySelector('input[name="lib-entry-merge-target"]:checked');
    var mergeTargetId = mergeTargetEl && mergeTargetEl.value ? mergeTargetEl.value : null;
    var noteEl = document.getElementById("lib-entry-image-merge-note");
    var warningEl = document.getElementById("lib-entry-image-warning");
    if (mergeTargetId) {
      includeImageEl.checked = false;
      includeImageEl.disabled = true;
      if (warningEl) warningEl.style.display = "none";
      if (noteEl) noteEl.style.display = "";
    } else {
      includeImageEl.disabled = false;
      if (noteEl) noteEl.style.display = "none";
    }
  }

  // N27: domain_key のフリーテキスト typo によるエントリの検索孤立を防ぐため、
  // 既存カートリッジ一覧 + 既存ライブラリ分野を datalist 候補として提示する。
  // 候補提示は補助機能（fail-soft）— 取得に失敗しても自由入力での登録は妨げない。
  function _libraryPopulateDomainOptions() {
    var datalist = document.getElementById("lib-entry-domain-options");
    if (!datalist) return;
    var seen = {};
    function addOption(key, label) {
      if (!key || seen[key]) return;
      seen[key] = true;
      var opt = document.createElement("option");
      opt.value = key;
      if (label && label !== key) opt.label = label + " (" + key + ")";
      datalist.appendChild(opt);
    }
    apiFetch("/admin/cartridges")
      .then(function (res) { return res.ok ? res.json() : []; })
      .then(function (items) {
        (items || []).forEach(function (c) { addOption(c.cartridge_id, c.name); });
        return apiFetch("/admin/library/domains");
      })
      .then(function (res) { return res.ok ? res.json() : { domains: [] }; })
      .then(function (data) {
        (((data && data.domains) || [])).forEach(function (d) { addOption(d.domain_key, null); });
      })
      .catch(function () { /* 候補提示の失敗は致命的ではない（自由入力で登録できる） */ });
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
    } else {
      var html = '<div style="font-size:11.5px;color:var(--color-text-secondary);margin-bottom:4px">類似する既存エントリが見つかりました。統合する場合は選択してください:</div>';
      entries.forEach(function (e) {
        html += '<label style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:2px">' +
          '<input type="radio" name="lib-entry-merge-target" value="' + escHtml(e.id) + '">' +
          escHtml(e.name) + (e.summary ? ' <span style="color:var(--color-text-tertiary)">— ' + escHtml(e.summary.slice(0, 40)) + '</span>' : '') +
        '</label>';
      });
      area.innerHTML = html;
    }
    // 再検索で選択中だった統合先候補が消えることがある（change イベントは発火しない）。
    // 例示画像チェックボックスの有効/無効を現在の選択状況に合わせて再同期しておく。
    _libraryUpdateImageCheckboxForMergeTarget();
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
        // N28: 装置候補から転記した接続情報（編集 UI は v1 なし。昇格で落とさない P4）。
        connections: _libEntryModalCtx.connections || [],
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
    var html = '<button type="button" class="library-domain-item admin-action-btn" data-ui-anchor="knowledge-library.domains-list" data-domain-key="" style="' + _libraryDomainButtonStyle(allActive) + '">すべての分野</button>';
    domains.forEach(function (d) {
      var active = _libraryTabState.selectedDomain === d.domain_key;
      html += '<button type="button" class="library-domain-item admin-action-btn" data-ui-anchor="knowledge-library.domains-list" data-domain-key="' + escHtml(d.domain_key) + '" style="' + _libraryDomainButtonStyle(active) + '">' +
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
      html += '<button type="button" class="library-entry-item" data-ui-anchor="knowledge-library.entries-list" data-entry-id="' + escHtml(e.id) + '" style="text-align:left;border:1px solid var(--color-border-tertiary);border-radius:4px;padding:6px 8px;background:' + (retired ? "var(--color-background-tertiary)" : "var(--color-background-primary)") + ';opacity:' + (retired ? "0.6" : "1") + ';cursor:pointer">' +
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

  // N1（Phase S 標準化判定の UI 到達経路）: migration 050 の5語彙を日本語ラベル+色分けで表示する。
  // 値の変更経路は「深く検討」モーダルの標準化候補を教員が commit したときのみ
  // （LLM が library_entries に直接書く経路は無い。KN-3 / L層ガードレール）。数値スコアは出さない。
  // 正本は backend/core/library/schema.py::STANDARDIZATION_STATUS_LABELS。
  // deliberation.js / app.js の同語彙表とバイト一致させる
  // （固定は backend/tests/test_library_vocab_mirror.py）。旧ラベルが括弧で補って
  // いた語釈（標準＝教科書級 / 分野標準＝その分野内）はバッジの title に移した（P4）。
  var _libraryStandardizationLabels = {
    standard: "標準",
    field_standard: "分野標準",
    emerging_common: "共通化しつつある",
    novel: "新規",
    unknown: "未評価",
  };
  var _libraryStandardizationStyles = {
    standard: "background:var(--color-text-success);color:#fff",
    field_standard: "background:var(--color-text-info);color:#fff",
    emerging_common: "background:#7b5cb8;color:#fff",
    novel: "background:var(--color-text-warning);color:#fff",
    unknown: "background:var(--color-background-tertiary,#eeeef0);color:var(--color-text-secondary)",
  };
  function _libraryStandardizationBadgeHtml(status) {
    var key = _libraryStandardizationLabels.hasOwnProperty(status) ? status : "unknown";
    return '<span class="admin-status" title="標準化度（三角測量による判定。確定は教員の commit のみ）。標準＝教科書級の定着 / 分野標準＝その分野内での定着 / 共通化しつつある＝コーパス内で反復するが外部の標準ではない" style="' +
      _libraryStandardizationStyles[key] + '">標準化度: ' + escHtml(_libraryStandardizationLabels[key]) + '</span>';
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

      // N1: Phase S 標準化判定バッジ + shared_part の「深く検討」入口。
      // 評価・確定の実体は深く検討モーダル側（標準化度を評価ボタン → 候補注釈 → 教員 commit）。
      '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:8px">' +
        _libraryStandardizationBadgeHtml(entry.standardization_status) +
        (window.Deliberation && window.Deliberation.openElement
          ? '<button type="button" id="lib-detail-deliberate" data-ui-anchor="knowledge-library.detail-deliberate" class="admin-action-btn" style="font-size:11.5px;padding:2px 8px" title="この共通部品の内訳・標準化度の評価・同一性リンクを検討します">深く検討</button>'
          : '') +
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

      // N28: connections は昇格時に装置候補から転記された接続情報。v1 は読み取り専用表示
      // （編集 UI なし・保存時に body へそのまま引き継ぐ。落とさない P4）。
      var detailConnections = b.connections || [];
      if (detailConnections.length) {
        html +=
          '<label style="font-size:12px;color:var(--color-text-secondary);display:block;margin-bottom:2px">接続 (connections・候補から転記)</label>' +
          '<div style="font-size:11.5px;color:var(--color-text-secondary);margin-bottom:8px">' +
            detailConnections.map(function (c) {
              return '<div>' + escHtml((c.from_part || "?") + " → " + (c.to_part || "?")) +
                (c.relation ? '（' + escHtml(c.relation) + '）' : '') +
                (c.reason ? ' <span style="color:var(--color-text-tertiary)">— ' + escHtml(c.reason) + '</span>' : '') +
              '</div>';
            }).join("") +
          '</div>';
      }
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

      // N29: retired は読み取り専用（サーバ側も 409 でブロックする）。保存は無効化し、
      // 復元が先であることを事実文で示す（凍結・廃止ボタンは retired では従来から非表示）。
      (isRetired
        ? '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:6px">retired のエントリは編集できません。復元してから編集してください。</div>'
        : '') +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">' +
        '<button id="lib-detail-save" data-ui-anchor="knowledge-library.detail-save" class="admin-action-btn" style="background:var(--color-text-success);color:#fff"' +
          (isRetired ? ' disabled title="retired のエントリは編集できません。復元してから編集してください"' : '') + '>保存</button>' +
        (isRetired
          ? '<button id="lib-detail-restore" data-ui-anchor="knowledge-library.detail-restore" class="admin-action-btn">復元</button>'
          : ('<button id="lib-detail-freeze" data-ui-anchor="knowledge-library.detail-freeze" class="admin-action-btn">凍結（版発行）</button>' +
             '<button id="lib-detail-retire" data-ui-anchor="knowledge-library.detail-retire" class="admin-action-btn" style="background:var(--color-text-danger);color:#fff">廃止</button>')) +
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

    // N1: shared_part の深く検討モーダルを開く（標準化度の評価・候補確定・同一性リンクの入口）。
    var deliberateBtn = document.getElementById("lib-detail-deliberate");
    if (deliberateBtn) deliberateBtn.addEventListener("click", function () {
      if (window.Deliberation) window.Deliberation.openElement("shared_part", entry.id, { title: entry.name });
    });

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
        // N28: connections は編集 UI を持たない転記情報。フォーム値から body を再構成する
        // 際に落とさない（P4）。
        connections: (entry.body && entry.body.connections) || [],
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
        if (res.status === 409) {
          // 409 は2種類: retired 読み取り専用（detail は事実文の文字列, N29）と
          // 楽観ロック衝突（detail は {message, current_revision} の dict）。
          return res.json().then(function (d) {
            var detail = d && d.detail;
            if (typeof detail === "string" && detail) throw { message: detail };
            throw { conflict: true };
          }, function () { throw { conflict: true }; });
        }
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
    openDangerConfirmModal({
      title: "ライブラリエントリの凍結",
      message: ["凍結版はパイプラインの解析に使われます。取り消せません（修正は次版で行います）。", "よろしいですか？"],
      confirmLabel: "凍結する",
    }, function () {
      _libraryDetailStatus("凍結しています...", "info");
      apiFetch("/admin/library/entries/" + encodeURIComponent(entryId) + "/freeze", { method: "POST", body: JSON.stringify({ note: note || "" }) })
        .then(function (res) {
          if (!res.ok) return res.json().then(function (d) { throw new Error((d && d.detail) || "凍結に失敗しました"); });
          return res.json();
        })
        .then(function (data) {
          if (data && data.embedding_status === "failed") {
            _libraryDetailStatus("凍結しましたが、検索用インデックスの生成に失敗しました。再度凍結すると再試行されます", "error");
          } else {
            _libraryDetailStatus("凍結しました", "success");
          }
          selectLibraryEntry(entryId);
          loadLibraryEntries();
        })
        .catch(function (err) { _libraryDetailStatus(err.message || "凍結に失敗しました", "error"); });
    });
  }

  function retireLibraryEntry(entryId) {
    openDangerConfirmModal({
      title: "ライブラリエントリの廃止",
      message: "このエントリを廃止します。retrieval対象から外れますが、履歴・provenanceは保持されます。よろしいですか？",
      confirmLabel: "廃止する",
    }, function () {
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
    });
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

  // ── 知識ランドスケープ（配置レビュー, migration 065） ─────────────────
  // 正本: docs/features/knowledge_landscape_design.md §4.1（教員UX）/ §9.1（admin API）/ §10.3。
  //   LS1 地図は正解ではなく投影（1論文が複数領域×複数観点に置かれるのが既定）
  //   LS3 AI配置は inferred 止まり。confirmed / rejected にできるのは教員の明示操作のみ
  //   LS5 weight / confidence の生数値を出さない（*_label のみを描画する）
  //   LS8 使用骨格の版・生成日を事実行で明示する
  //   LS10 配置不能（unplaced）は失敗ではなく信号として事実文で提示する
  var _landscapeModalState = { documentId: null, title: "", proposing: false, busy: false };

  // 配置候補ゼロ（かつ unplaced 申告もゼロ）のときの事実文。
  var LANDSCAPE_EMPTY_NOTICE = "配置候補はまだありません。[AIで再提案] で生成できます。";
  // LS8: コーパス・出所の正直さ。数値スコアではなく出所と可視範囲を事実として書く。
  var LANDSCAPE_CORPUS_NOTICE = "この一覧は解析パイプラインが生成した配置候補（AI推定）と、教員の確認結果です。" +
    "確認していない候補も学習者には「AIによる推定（未確認）」として表示され、却下すると表示されなくなります。";
  // ステージ payload（§7.2 の skip 語彙）を事実文へ。数値・上限値は書かない。
  var LANDSCAPE_SKIP_TEXT = {
    no_frozen_skeleton: "凍結済みの分野マップ（骨格）がまだありません。「分野の地図」タブで骨格を凍結すると配置候補を生成できます。",
    no_source_material: "この教材には配置の判断に使える解析結果（中心命題・主張）がまだありません。解析の完了後に再度お試しください。",
    daily_call_limit_reached: "本日の配置候補の生成上限に達しました。翌日以降に再度お試しください。",
    llm_call_failed: "配置候補の生成に失敗しました。しばらく待ってから再度お試しください。"
  };
  // サーバの status_label が取れないときのフォールバック（fail-soft・断定しない語）。
  var LANDSCAPE_STATUS_FALLBACK_LABELS = {
    confirmed: "教員確認済み",
    inferred: "AIによる推定（未確認）",
    review_required: "確認待ち（AI推定）",
    rejected: "却下",
    superseded: "旧候補"
  };
  var LANDSCAPE_STATUS_ACTIONS = [
    ["confirmed", "確認"],
    ["rejected", "却下"],
    ["review_required", "再検討"]
  ];

  function openLandscapeModal(documentId, title) {
    _landscapeModalState.documentId = documentId;
    _landscapeModalState.title = title || "";
    _landscapeModalState.proposing = false;
    _landscapeModalState.busy = false;

    var existing = document.getElementById("landscape-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "landscape-modal";
    overlay.setAttribute("data-ui-anchor", "materials.landscape-modal");
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:560px;max-width:720px;max-height:82vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">位置づけ（分野マップ）: ' + escHtml(title || "") + '</h3>' +
          '<button id="landscape-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px">' +
          '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0">' +
            'この論文が分野マップ（基準地図）のどこに位置づくかの候補です。1つの論文が複数の領域・複数の観点に置かれることがあります。' +
          '</p>' +
          '<button id="landscape-modal-propose" class="admin-action-btn admin-landscape-propose-btn" type="button" data-ui-anchor="materials.landscape-propose" title="AIに配置候補を作り直させます（教員が確認・却下した配置は上書きされません）" style="flex:0 0 auto;font-size:12px">AIで再提案</button>' +
        '</div>' +
        '<div id="landscape-modal-notice" style="font-size:12px;color:var(--color-text-secondary);margin:0 0 8px"></div>' +
        '<div id="landscape-modal-body" style="overflow-y:auto;flex:1">' +
          '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("landscape-modal-close").addEventListener("click", function () { overlay.remove(); });
    document.getElementById("landscape-modal-propose").addEventListener("click", _landscapeProposeAgain);

    loadLandscapePlacements(documentId);
  }

  function loadLandscapePlacements(documentId) {
    apiFetch("/admin/landscape/documents/" + encodeURIComponent(documentId) + "/placements")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        renderLandscapePlacements(data || {});
      })
      .catch(function () {
        var body = document.getElementById("landscape-modal-body");
        if (body) body.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">配置の読み込みに失敗しました</div>';
      });
  }

  function _landscapeNotice(message, isDanger) {
    var el = document.getElementById("landscape-modal-notice");
    if (!el) return;
    el.style.color = isDanger ? "var(--color-text-danger)" : "var(--color-text-secondary)";
    el.textContent = message || "";
  }

  // サーバの detail（事実文）をそのまま使う。取れないときだけ既定文へ縮退する。
  function _landscapeErrorDetail(res, fallback) {
    return res.json()
      .then(function (bodyJson) {
        var detail = bodyJson && bodyJson.detail;
        if (typeof detail === "string" && detail) return detail;
        if (detail && typeof detail.message === "string" && detail.message) return detail.message;
        return fallback;
      })
      .catch(function () { return fallback; });
  }

  function _landscapeStatusChipHtml(placement) {
    var status = (placement && placement.status) || "";
    var label = (placement && placement.status_label) || LANDSCAPE_STATUS_FALLBACK_LABELS[status] || status || "不明";
    var bg = "var(--color-background-tertiary)";
    var fg = "var(--color-text-secondary)";
    var extra = "";
    if (status === "confirmed") { bg = "var(--color-background-success)"; fg = "var(--color-text-success)"; }
    else if (status === "review_required") { bg = "var(--color-background-warning, #fff3cd)"; fg = "var(--color-text-warning)"; }
    else if (status === "rejected") { fg = "var(--color-text-tertiary)"; extra = ";text-decoration:line-through"; }
    return '<span class="admin-status landscape-status-chip" data-landscape-status="' + escHtml(status) +
      '" style="background:' + bg + ';color:' + fg + extra + '">' + escHtml(label) + '</span>';
  }

  function _landscapeEvidenceHtml(placement) {
    var evidence = (placement && placement.evidence) || [];
    if (!evidence.length) {
      return '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:4px">原文の引用はありません</div>';
    }
    var inner = "";
    evidence.forEach(function (ev) {
      var quote = (ev && ev.quote) || "";
      if (!quote) return;
      inner += '<div style="font-size:12px;color:var(--color-text-secondary);border-left:2px solid var(--color-border-tertiary);padding:2px 0 2px 8px;margin:4px 0">' +
        escHtml(quote) +
        (ev && ev.claim_id ? ' <code style="font-size:10px;color:var(--color-text-tertiary)">' + escHtml(ev.claim_id) + '</code>' : "") +
      '</div>';
    });
    if (!inner) {
      return '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:4px">原文の引用はありません</div>';
    }
    return '<details style="margin-top:4px">' +
      '<summary style="font-size:11px;color:var(--color-text-tertiary);cursor:pointer">原文の引用を見る</summary>' +
      inner +
    '</details>';
  }

  function _landscapeActionsHtml(placement) {
    var pid = (placement && placement.id) || "";
    var status = (placement && placement.status) || "";
    var html = '<div style="display:flex;gap:6px;margin-top:6px">';
    LANDSCAPE_STATUS_ACTIONS.forEach(function (pair) {
      var isCurrent = pair[0] === status;
      html += '<button class="admin-action-btn admin-landscape-status-btn" type="button" data-placement-id="' + escHtml(pid) +
        '" data-status="' + escHtml(pair[0]) + '" style="font-size:11px;padding:2px 8px"' +
        (isCurrent ? " disabled" : "") + ">" + pair[1] + "</button>";
    });
    html += '</div>';
    return html;
  }

  function _landscapeGroupByDomain(placements) {
    var order = [];
    var groups = {};
    placements.forEach(function (p) {
      var key = (p && p.domain_key) || "";
      if (!groups[key]) {
        groups[key] = { domain_key: key, domain_name: (p && p.domain_name) || key, rows: [] };
        order.push(key);
      }
      groups[key].rows.push(p);
    });
    return order.map(function (key) { return groups[key]; });
  }

  // LS8: 使用した骨格版。envelope の domains[] があればそれを、無ければ行の
  // skeleton_version を使い、どちらも無ければ版の行を出さない（推測しない）。
  function _landscapeFrozenVersion(data, group) {
    var domains = (data && data.domains) || [];
    var i;
    for (i = 0; i < domains.length; i++) {
      if (domains[i] && domains[i].domain_key === group.domain_key) {
        return domains[i].frozen_version || domains[i].skeleton_version || "";
      }
    }
    for (i = 0; i < group.rows.length; i++) {
      if (group.rows[i] && group.rows[i].skeleton_version) return group.rows[i].skeleton_version;
    }
    return "";
  }

  function _landscapeLatestTimestamp(data, placements) {
    if (data && data.last_run_at) return data.last_run_at;
    if (data && data.generated_at) return data.generated_at;
    var latest = "";
    placements.forEach(function (p) {
      var created = (p && p.created_at) || "";
      if (created && created > latest) latest = created;
    });
    return latest;
  }

  function _landscapeFooterHtml(data, groups, placements) {
    var facts = [];
    var generated = _landscapeLatestTimestamp(data, placements);
    if (generated) facts.push("最終生成: " + formatDateTime(generated));
    var versions = [];
    groups.forEach(function (group) {
      var version = _landscapeFrozenVersion(data, group);
      if (version) versions.push(group.domain_name + " 骨格版 " + version);
    });
    if (versions.length) facts.push("使用した骨格: " + versions.join(" ・ "));
    return '<div style="margin-top:12px;padding-top:8px;border-top:1px solid var(--color-border-tertiary);font-size:11px;color:var(--color-text-tertiary)">' +
      escHtml(LANDSCAPE_CORPUS_NOTICE) +
      (facts.length ? '<div style="margin-top:4px">' + escHtml(facts.join(" ・ ")) + '</div>' : "") +
    '</div>';
  }

  // カテゴリギャップ候補（migration 066, §4.1 裁定）: 記録された事実の一行だけを添える。
  // このモーダルではレビューさせない（「地図を直す」ボタンを1論文の画面に置かない）。
  var LANDSCAPE_GAP_SIGNAL_NOTICE = "この分野の地図への候補として記録されています（分野の地図タブで確認できます）";

  // LS10: どの領域にも置けなかったドメインは失敗ではなく信号。事実文で並べる。
  function _landscapeUnplacedHtml(unplaced) {
    if (!unplaced.length) return "";
    var html = '<div style="margin-top:12px">' +
      '<div style="font-size:12px;font-weight:600;color:var(--color-text-secondary);margin-bottom:4px">配置できなかった分野</div>';
    unplaced.forEach(function (item) {
      var name = (item && (item.domain_name || item.domain_key)) || "";
      var reason = (item && item.reason) || "";
      html += '<div class="landscape-unplaced-row" style="font-size:12px;color:var(--color-text-secondary);padding:3px 0">' +
        escHtml("この論文は「" + name + "」の地図に配置できませんでした: " + reason) +
        (item && item.gap_signals_recorded
          ? '<div class="landscape-gap-signal-row" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' +
              escHtml(LANDSCAPE_GAP_SIGNAL_NOTICE) +
            '</div>'
          : "") +
      '</div>';
    });
    html += '</div>';
    return html;
  }

  function renderLandscapePlacements(data) {
    var body = document.getElementById("landscape-modal-body");
    if (!body) return;
    var placements = (data && data.placements) || [];
    var unplaced = (data && data.unplaced_domains) || [];
    var groups = _landscapeGroupByDomain(placements);
    var html = "";

    if (!placements.length && !unplaced.length) {
      html += '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">' + escHtml(LANDSCAPE_EMPTY_NOTICE) + '</div>';
    }

    groups.forEach(function (group) {
      var version = _landscapeFrozenVersion(data, group);
      html += '<div class="landscape-domain-group" data-domain-key="' + escHtml(group.domain_key) + '" style="margin-bottom:14px">' +
        '<div style="font-size:13px;font-weight:600;color:var(--color-text-primary)">' + escHtml(group.domain_name) + '</div>' +
        (version ? '<div style="font-size:11px;color:var(--color-text-tertiary);margin-bottom:4px">骨格版 ' + escHtml(version) + '</div>' : '<div style="margin-bottom:4px"></div>');
      group.rows.forEach(function (p) {
        var meta = [];
        if (p && p.perspective_label) meta.push(p.perspective_label);
        if (p && p.weight_label) meta.push(p.weight_label);
        html += '<div class="landscape-placement-row" data-placement-id="' + escHtml((p && p.id) || "") + '" style="padding:8px 0;border-bottom:1px solid var(--color-border-tertiary)">' +
          '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
            '<span style="font-size:13px;color:var(--color-text-primary)">' + escHtml((p && p.node_label) || (p && p.node_id) || "") + '</span>' +
            (meta.length ? '<span style="font-size:11px;color:var(--color-text-tertiary)">' + escHtml(meta.join(" ・ ")) + '</span>' : "") +
            _landscapeStatusChipHtml(p) +
          '</div>' +
          ((p && p.reason) ? '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:4px">' + escHtml(p.reason) + '</div>' : "") +
          _landscapeEvidenceHtml(p) +
          ((p && p.review_note) ? '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:4px">メモ: ' + escHtml(p.review_note) + '</div>' : "") +
          _landscapeActionsHtml(p) +
        '</div>';
      });
      html += '</div>';
    });

    html += _landscapeUnplacedHtml(unplaced);
    html += _landscapeFooterHtml(data, groups, placements);
    body.innerHTML = html;

    body.querySelectorAll(".admin-landscape-status-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        _landscapeUpdateStatus(this.getAttribute("data-placement-id"), this.getAttribute("data-status"), this);
      });
    });
  }

  // LS3: 確定は教員の明示操作のみ。楽観更新はせず、成功後に一覧を読み直す。
  function _landscapeUpdateStatus(placementId, status, btn) {
    if (!placementId || !status) return;
    if (_landscapeModalState.busy) return;
    _landscapeModalState.busy = true;
    if (btn) btn.disabled = true;
    _landscapeNotice("");
    apiFetch("/admin/landscape/placements/" + encodeURIComponent(placementId), {
      method: "PATCH",
      body: JSON.stringify({ status: status })
    })
      .then(function (res) {
        if (!res.ok) {
          return _landscapeErrorDetail(res, "この配置の状態を変更できませんでした。").then(function (msg) {
            throw new Error(msg);
          });
        }
        return null;
      })
      .then(function () {
        _landscapeModalState.busy = false;
        loadLandscapePlacements(_landscapeModalState.documentId);
      })
      .catch(function (err) {
        _landscapeModalState.busy = false;
        if (btn) btn.disabled = false;
        _landscapeNotice((err && err.message) || "この配置の状態を変更できませんでした。", true);
      });
  }

  function _landscapeResetProposeButton() {
    _landscapeModalState.proposing = false;
    var btn = document.getElementById("landscape-modal-propose");
    if (btn) {
      btn.disabled = false;
      btn.textContent = "AIで再提案";
    }
  }

  function _landscapeProposeResultText(payload) {
    if (payload && payload.skipped_by_limit) return LANDSCAPE_SKIP_TEXT.daily_call_limit_reached;
    var reason = (payload && payload.skipped_reason) || "";
    if (reason && LANDSCAPE_SKIP_TEXT[reason]) return LANDSCAPE_SKIP_TEXT[reason];
    if (reason) return "配置候補は生成されませんでした（" + reason + "）。";
    if (payload && payload.created === 0) return "新しい配置候補はありませんでした。";
    return "配置候補を更新しました。";
  }

  function _landscapeProposeAgain() {
    if (_landscapeModalState.proposing) return;
    if (!_landscapeModalState.documentId) return;
    _landscapeModalState.proposing = true;
    var btn = document.getElementById("landscape-modal-propose");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "生成中...";
    }
    _landscapeNotice("配置候補を生成しています。しばらくお待ちください。");
    apiFetch("/admin/landscape/documents/" + encodeURIComponent(_landscapeModalState.documentId) + "/placements/propose", {
      method: "POST",
      body: JSON.stringify({})
    })
      .then(function (res) {
        if (!res.ok) {
          // 429（日次上限）等はサーバの事実文をそのまま出す（上限値をUIに書かない）。
          return _landscapeErrorDetail(res, LANDSCAPE_SKIP_TEXT.llm_call_failed).then(function (msg) {
            throw new Error(msg);
          });
        }
        return res.json().catch(function () { return {}; });
      })
      .then(function (payload) {
        _landscapeResetProposeButton();
        _landscapeNotice(_landscapeProposeResultText(payload || {}));
        loadLandscapePlacements(_landscapeModalState.documentId);
      })
      .catch(function (err) {
        _landscapeResetProposeButton();
        _landscapeNotice((err && err.message) || LANDSCAPE_SKIP_TEXT.llm_call_failed, true);
      });
  }

  // ── ゼミ前ブリーフ（seminar_brief_mirroring_design.md §1） ─────────────────
  // 正本: docs/features/seminar_brief_mirroring_design.md
  //   SB1 新テーブル・新LLMゼロ（既存投影の読み時合成 — このモーダルは読むだけ。書き込みAPIなし）
  //   SB2 数値を見せない（件数・人数の生値なし。段階ラベル・事実文のみ描画する）
  //   SB3 第4区画「学習者からの問い」は v1 空欄で予約（空欄は発見の流儀 — 警告色・催促文にしない）
  //   SB4 学習者個人・学習者別件数への導線を作らない
  // 描画は createElement / textContent 基調（本文変数を innerHTML に渡さない）。

  // 第4区画の note がサーバから取れないときの固定文
  // （正本: core/doubt/seminar_brief.py::LEARNER_HANDOFF_RESERVED_NOTE の逐語）。
  var SEMINAR_BRIEF_RESERVED_NOTE = "（この区画は、学習者からの手渡しの仕組みの実装後に使われます）";
  // 支持線の段階ラベル。正本は backend/core/doubt/schema.py::SUPPORT_LEVEL_BADGE_LABELS
  // （test_seminar_brief_ui_static.py が逐語ミラーを固定。SL4: 経路数・カットのサイズは出さない）。
  var SEMINAR_SUPPORT_LEVEL_LABELS = {
    none: "支持記録なし",
    single: "単一の支持線",
    several: "複数の支持線"
  };
  // 検証状態のラベル。正本は backend/core/label_vocab.py::VERIFICATION_STATUS_LABELS_LEDGER
  // （doubt-atlas.js::STATUS_LABELS と同じ台帳宛先の文言。逐語ミラーをテストで固定）。
  var SEMINAR_VERIFICATION_STATUS_LABELS = {
    directly_verified: "直接検証の記帳あり",
    indirectly_supported: "間接的な支持あり",
    untested: "未検証",
    refuted: "反証の記帳あり",
    unknown: "検証情報なし"
  };
  // つまづき4軸の見出し（原稿スタジオの lsStumbleAxis と同じ語彙。k-匿名の段階ラベルのみ）。
  var SEMINAR_STUMBLE_AXIS_LABELS = [
    ["error_rate", "誤り率"],
    ["symbol_descent", "記号降下"],
    ["verdict_self_check_divergence", "判定の乖離"],
    ["faq", "質問・誤解"]
  ];

  function openSeminarBriefModal(documentId, title) {
    var existing = document.getElementById("seminar-brief-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "seminar-brief-modal";
    overlay.setAttribute("data-ui-anchor", "materials.seminar-brief-modal");
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    // モーダルの骨組みは静的文字列のみ（教材タイトル・本文はあとから textContent で入れる）。
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:560px;max-width:720px;max-height:82vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 id="seminar-brief-modal-title" style="margin:0;font-size:16px;color:var(--color-text-primary)"></h3>' +
          '<button id="seminar-brief-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 8px">' +
          '輪講の前に、この論文の「何が崩れたら危ういか」を確認するための読み取り専用ビューです。' +
        '</p>' +
        '<div id="seminar-brief-modal-body" style="overflow-y:auto;flex:1">' +
          '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    document.getElementById("seminar-brief-modal-title").textContent = "ゼミ前ブリーフ: " + (title || "");

    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("seminar-brief-modal-close").addEventListener("click", function () { overlay.remove(); });

    loadSeminarBrief(documentId);
  }

  function loadSeminarBrief(documentId) {
    apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/seminar-brief")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        renderSeminarBrief(data || {});
      })
      .catch(function () {
        var body = document.getElementById("seminar-brief-modal-body");
        if (!body) return;
        body.textContent = "";
        body.appendChild(_sbEl("div", "padding:16px;color:var(--color-text-danger);font-size:13px", "ブリーフの読み込みに失敗しました"));
      });
  }

  // createElement + textContent の小さなヘルパー（本文変数を innerHTML に渡さない）。
  function _sbEl(tag, cssText, text) {
    var el = document.createElement(tag);
    if (cssText) el.style.cssText = cssText;
    if (text) el.textContent = text;
    return el;
  }

  // 区画の枠。行が空なら null（＝区画ごと静かに省略する。空を告げる置き文を書かない）。
  function _sbSection(heading, rowEls) {
    if (!rowEls || !rowEls.length) return null;
    var section = _sbEl("div", "margin-bottom:16px");
    section.className = "seminar-brief-section";
    section.appendChild(_sbEl("div", "font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:6px", heading));
    rowEls.forEach(function (el) { section.appendChild(el); });
    return section;
  }

  // 脆い前提1件の段階ラベル群（SB2: 描画するのはサーバの段階ラベル・レンジ文字列のみ。
  // 文字列以外の値（数値等）は種類を問わず描画しない構造ガード）。
  function _sbFragileMeta(item) {
    var facts = [];
    if (typeof item.load_level_label === "string" && item.load_level_label) {
      facts.push("下流への影響: " + item.load_level_label);
    }
    if (typeof item.verification_status === "string" && SEMINAR_VERIFICATION_STATUS_LABELS[item.verification_status]) {
      facts.push("検証: " + SEMINAR_VERIFICATION_STATUS_LABELS[item.verification_status]);
    }
    if (typeof item.challenge_count_label === "string" && item.challenge_count_label) {
      facts.push("疑義: " + item.challenge_count_label);
    }
    if (typeof item.support_line_level === "string" && SEMINAR_SUPPORT_LEVEL_LABELS[item.support_line_level]) {
      facts.push("支持: " + SEMINAR_SUPPORT_LEVEL_LABELS[item.support_line_level]);
    }
    return facts;
  }

  // 脆い前提（claim）に添える claim つまづきの段階ラベル行（k-匿名通過分のみ = has_data の
  // ときだけ。少人数ゼミでは出ないのがふつうの状態 — 出ないことを警告にしない, SB4）。
  function _sbStumbleFacts(stumble) {
    if (!stumble || !stumble.has_data) return [];
    var axes = stumble.axes || {};
    var parts = [];
    SEMINAR_STUMBLE_AXIS_LABELS.forEach(function (pair) {
      var value = axes[pair[0]];
      if (pair[0] === "faq") value = value && value.questions;
      if (typeof value === "string" && value) parts.push(pair[1] + ": " + value);
    });
    if (!parts.length) return [];
    return ["つまづき — " + parts.join(" ・ ")];
  }

  function _sbStatementRow(statement, facts) {
    var row = _sbEl("div", "padding:6px 0;border-bottom:1px solid var(--color-border-tertiary)");
    row.className = "seminar-brief-row";
    row.appendChild(_sbEl("div", "font-size:13px;color:var(--color-text-primary)", statement || ""));
    (facts || []).forEach(function (fact) {
      row.appendChild(_sbEl("div", "font-size:11.5px;color:var(--color-text-secondary);margin-top:2px", fact));
    });
    return row;
  }

  function renderSeminarBrief(data) {
    var body = document.getElementById("seminar-brief-modal-body");
    if (!body) return;
    body.textContent = "";

    // 合成できない document は理由の事実文のみ（警告色にしない）。
    if (!data.available) {
      body.appendChild(_sbEl("div", "padding:16px;color:var(--color-text-secondary);font-size:13px",
        data.reason || "この教材ではゼミ前ブリーフを合成できません。"));
      return;
    }

    // 1. 脆い前提（未検証 × 下流影響の段階ラベル。並びはサーバが決める）
    var fragileRows = [];
    (data.fragile_assumptions || []).forEach(function (item) {
      if (!item || !item.statement) return;
      fragileRows.push(_sbStatementRow(item.statement, _sbFragileMeta(item).concat(_sbStumbleFacts(item.stumble))));
    });
    var fragileSection = _sbSection("脆い前提", fragileRows);
    if (fragileSection) body.appendChild(fragileSection);

    // 2. 一点吊りの支持線（SL層 support_paths level=single の事実文）
    var singleRows = [];
    (data.single_support_lines || []).forEach(function (item) {
      if (!item || !item.statement) return;
      singleRows.push(_sbStatementRow(item.statement, item.fact_line ? [item.fact_line] : []));
    });
    var singleSection = _sbSection("一点吊りの支持線", singleRows);
    if (singleSection) body.appendChild(singleSection);

    // 3. 晴れ間（SL1 閉世界語彙の事実文。サーバの文をそのまま出す — 言い換えない）
    var clearingRows = [];
    (data.clear_skies || []).forEach(function (item) {
      if (!item || !item.statement) return;
      clearingRows.push(_sbStatementRow(item.statement, item.fact_line ? [item.fact_line] : []));
    });
    var clearingSection = _sbSection("晴れ間", clearingRows);
    if (clearingSection) body.appendChild(clearingSection);

    // 4. 学習者からの問い — v1 は空欄予約（SB3）。空でもこの区画だけは常に出し、
    //    淡色の note のみを添える（警告色・催促文にしない）。
    var handed = data.learner_handoff || {};
    var handedSection = _sbEl("div", "margin-bottom:16px");
    handedSection.className = "seminar-brief-section seminar-brief-handed";
    handedSection.appendChild(_sbEl("div", "font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:6px", "学習者からの問い"));
    handedSection.appendChild(_sbEl("div", "font-size:12px;color:var(--color-text-tertiary)", handed.note || SEMINAR_BRIEF_RESERVED_NOTE));
    body.appendChild(handedSection);
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
    var analyzeImagesChecked = !!(analyzeImagesEl && analyzeImagesEl.checked);
    formData.append("analyze_images", analyzeImagesChecked ? "true" : "false");
    // M層（LLM モデル選択, migration 061）: 選択済みモデル（カタログ収載分のみ）を run options へ渡す。
    if (window.AdminLlmModels) {
      var uploadModels = window.AdminLlmModels.getUploadModels(analyzeImagesChecked);
      if (uploadModels) formData.append("models", JSON.stringify(uploadModels));
    }

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
        handleUploadAccepted(data, file.name);
      })
      .catch(function (err) {
        showUploadStatus("アップロードに失敗しました: " + (err.detail || "不明なエラー"), "error");
        disableUploadUI(false);
      });
  }

  // 受理（202）後の共通経路。ファイル選択とURL指定の両方がここへ合流する
  // （状態表示・タスクポーリング・一覧更新を二重に実装しない）。
  function handleUploadAccepted(data, displayName) {
    showUploadStatus(
      escHtml(displayName) + " のアップロードが完了しました。グラフ構築中..." +
      '<span class="typing" style="display:inline-flex;margin-left:8px"><span></span><span></span><span></span></span>',
      "info"
    );
    showMaterialNextStepCard();
    loadMaterials();
    loadMaterialsForSelection();
    if (window.AdminNextSteps) window.AdminNextSteps.refresh();
    if (data && data.task_id) {
      startTaskPolling(data.task_id, displayName);
    } else {
      disableUploadUI(false);
    }
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

  // ── URL指定による教材取得 ──────────────────────────────────────────
  //
  // 取得できるのは管理者が許可したドメインのみ（サーバ側で強制する）。この画面は
  // 許可リストを取得して事実文で提示し、空なら取得ボタンを理由付きで無効化する。
  // 受理後は既存のファイルアップロードと同じ経路（handleUploadAccepted）へ合流する。
  var _urlUploadSubmitting = false;

  function initUrlUpload() {
    var link = document.getElementById("url-upload-link");
    if (!link) return;
    link.addEventListener("click", function (e) {
      e.preventDefault();
      openUrlUploadModal();
    });
  }

  // サーバの detail（事実文）をそのまま見せる。取れないときだけ既定文へ縮退する。
  function _urlFetchDetailText(err) {
    if (!err) return "不明なエラー";
    var detail = err.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && detail.length && typeof detail[0] === "object" && detail[0].msg) return String(detail[0].msg);
    if (err.message && err.message !== "Unauthorized") return String(err.message);
    return "不明なエラー";
  }

  function closeUrlUploadModal() {
    var overlay = document.getElementById("url-upload-modal");
    if (overlay) overlay.remove();
  }

  function openUrlUploadModal() {
    closeUrlUploadModal();
    _urlUploadSubmitting = false;

    var overlay = document.createElement("div");
    overlay.id = "url-upload-modal";
    overlay.setAttribute("data-ui-anchor", "materials.url-upload-modal");
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:460px;max-width:560px;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">URLから教材を取得</h3>' +
          '<button type="button" id="url-upload-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<label for="url-upload-input" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:4px">教材のURL</label>' +
        '<input type="text" id="url-upload-input" placeholder="https://arxiv.org/pdf/2401.00001" ' +
          'style="padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary)">' +
        '<div id="url-upload-domains-note" style="font-size:12px;color:var(--color-text-tertiary);margin-top:8px">許可ドメインを確認しています。</div>' +
        '<div id="url-upload-status" class="upload-status" style="display:none;margin-top:10px"></div>' +
        '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">' +
          '<button type="button" id="url-upload-cancel" class="admin-action-btn" style="background:var(--color-bg-tertiary);color:var(--color-text)">キャンセル</button>' +
          '<button type="button" id="url-upload-submit" data-ui-anchor="materials.url-upload-submit" class="admin-action-btn" disabled>アップロード</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) closeUrlUploadModal(); });
    document.getElementById("url-upload-close").addEventListener("click", closeUrlUploadModal);
    document.getElementById("url-upload-cancel").addEventListener("click", closeUrlUploadModal);
    document.getElementById("url-upload-submit").addEventListener("click", submitUrlUpload);
    var input = document.getElementById("url-upload-input");
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        submitUrlUpload();
      }
    });
    input.focus();

    loadUrlUploadDomains();
  }

  // 無効化したボタンには理由の事実文を必ず添える。
  function _urlUploadSetSubmitEnabled(enabled, reason) {
    var btn = document.getElementById("url-upload-submit");
    if (btn) btn.disabled = !enabled;
    var note = document.getElementById("url-upload-domains-note");
    if (note && reason) note.textContent = reason;
  }

  function _urlUploadStatus(message, kind) {
    var el = document.getElementById("url-upload-status");
    if (!el) return;
    if (!message) {
      el.style.display = "none";
      el.textContent = "";
      return;
    }
    el.style.display = "";
    el.className = "upload-status upload-status-" + (kind || "info");
    el.textContent = message;
  }

  function loadUrlUploadDomains() {
    apiFetch("/admin/url-fetch-domains")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        var domains = (data && data.domains) || [];
        var names = [];
        for (var i = 0; i < domains.length; i++) {
          if (domains[i] && domains[i].domain) names.push(domains[i].domain);
        }
        if (!names.length) {
          _urlUploadSetSubmitEnabled(false, "URLからの取得は、管理者が取得先ドメインを許可すると利用できます。");
          return;
        }
        _urlUploadSetSubmitEnabled(true, "取得できるのは管理者が許可したドメインのみです: " + names.join(", "));
      })
      .catch(function () {
        _urlUploadSetSubmitEnabled(false, "許可ドメインを取得できませんでした。時間をおいて開き直してください。");
      });
  }

  function submitUrlUpload() {
    if (_urlUploadSubmitting) return;
    var btn = document.getElementById("url-upload-submit");
    if (btn && btn.disabled) return;
    var input = document.getElementById("url-upload-input");
    var url = input ? input.value.trim() : "";
    if (!url) {
      _urlUploadStatus("URLを入力してください。", "error");
      return;
    }

    var payload = { url: url };
    // 画像読み取りパイプライン（migration 041）: ファイル選択時と同じチェックボックスを引き継ぐ。
    var analyzeImagesEl = document.getElementById("upload-analyze-images");
    var analyzeImagesChecked = !!(analyzeImagesEl && analyzeImagesEl.checked);
    payload.analyze_images = analyzeImagesChecked;
    // M層（LLM モデル選択, migration 061）: ファイル選択時と同じ選択を run options へ渡す。
    if (window.AdminLlmModels) {
      var uploadModels = window.AdminLlmModels.getUploadModels(analyzeImagesChecked);
      if (uploadModels) payload.models = uploadModels;
    }

    _urlUploadSubmitting = true;
    if (btn) btn.disabled = true;
    _urlUploadStatus("取得しています...", "info");
    disableUploadUI(true);

    apiFetch("/admin/materials/upload-from-url", {
      method: "POST",
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; }).then(function (d) { throw d; });
        }
        return res.json();
      })
      .then(function (data) {
        _urlUploadSubmitting = false;
        closeUrlUploadModal();
        handleUploadAccepted(data, (data && (data.filename || data.title)) || url);
      })
      .catch(function (err) {
        _urlUploadSubmitting = false;
        if (btn) btn.disabled = false;
        disableUploadUI(false);
        _urlUploadStatus("取得に失敗しました: " + _urlFetchDetailText(err), "error");
      });
  }

  // ── URL取得の許可ドメイン（運用「AIモデル」タブ = SYSTEM_ADMIN 限定）──────
  //
  // 教材管理タブは SYSTEM_ADMIN では非表示（setupRoleBasedUI）なので、この区画は
  // 運用グループの `#tab-llm-models` パネル末尾に動的生成する。admin-llm-models.js の
  // buildOpsTab() は data-llm-models-built ガードで初回1回だけ innerHTML を書くため、
  // initOpsTab の後に append すれば消えない。冪等（既に居れば作り直さない）。
  var _urlFetchDomainsBusy = false;

  function ensureUrlFetchDomainsSection() {
    var panel = document.getElementById("tab-llm-models");
    if (!panel) return;
    var section = document.getElementById("url-fetch-domains-section");
    if (section && section.parentNode === panel) {
      loadUrlFetchDomains();
      return;
    }
    if (section && section.parentNode) section.parentNode.removeChild(section);

    section = document.createElement("div");
    section.className = "admin-section";
    section.id = "url-fetch-domains-section";
    section.setAttribute("data-ui-anchor", "llm-models.url-fetch-domains");
    section.innerHTML =
      '<h3 class="admin-section-title">URL取得の許可ドメイン</h3>' +
      '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
        "ここに登録したドメインからのみ、教員は URL を指定して教材を取得できます。" +
      "</p>" +
      '<div id="url-fetch-domains-status" class="upload-status" style="display:none;margin-bottom:10px"></div>' +
      '<div id="url-fetch-domains-list" style="margin-bottom:12px">' +
        '<p style="color:var(--color-text-tertiary);font-size:13px;margin:0">読み込み中...</p>' +
      "</div>" +
      '<div style="display:flex;gap:8px;align-items:center">' +
        '<input type="text" id="url-fetch-domain-input" placeholder="arxiv.org" ' +
          'style="flex:0 1 260px;padding:5px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-primary);color:var(--color-text-primary)">' +
        '<button type="button" id="url-fetch-domain-add" data-ui-anchor="llm-models.url-fetch-domain-add" class="admin-action-btn">追加</button>' +
      "</div>";
    panel.appendChild(section);

    var addBtn = document.getElementById("url-fetch-domain-add");
    if (addBtn) addBtn.addEventListener("click", urlFetchDomainAdd);
    var input = document.getElementById("url-fetch-domain-input");
    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          urlFetchDomainAdd();
        }
      });
    }
    var list = document.getElementById("url-fetch-domains-list");
    if (list) {
      list.addEventListener("click", function (e) {
        var btn = e.target && e.target.closest ? e.target.closest("[data-url-domain-remove]") : null;
        if (!btn || btn.disabled) return;
        urlFetchDomainRemove(btn.getAttribute("data-url-domain-remove"));
      });
    }
    loadUrlFetchDomains();
  }

  function _urlFetchDomainsStatus(message, kind) {
    var el = document.getElementById("url-fetch-domains-status");
    if (!el) return;
    if (!message) {
      el.style.display = "none";
      el.textContent = "";
      return;
    }
    el.style.display = "";
    el.className = "upload-status upload-status-" + (kind || "info");
    el.textContent = message;
  }

  function loadUrlFetchDomains() {
    var list = document.getElementById("url-fetch-domains-list");
    if (!list) return;
    apiFetch("/admin/url-fetch-domains")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        renderUrlFetchDomains((data && data.domains) || []);
      })
      .catch(function () {
        list.innerHTML = '<p style="color:var(--color-text-danger);font-size:13px;margin:0">許可ドメインの読み込みに失敗しました。</p>';
      });
  }

  function renderUrlFetchDomains(domains) {
    var list = document.getElementById("url-fetch-domains-list");
    if (!list) return;
    if (!domains.length) {
      list.innerHTML =
        '<p style="color:var(--color-text-tertiary);font-size:13px;margin:0">' +
        "許可ドメインは登録されていません。登録するまで教員はURLからの取得を利用できません。" +
        "</p>";
      return;
    }
    var rows = "";
    for (var i = 0; i < domains.length; i++) {
      var d = domains[i] || {};
      rows +=
        "<tr>" +
          "<td>" + escHtml(d.domain) + "</td>" +
          "<td>" + escHtml(formatDateTime(d.created_at)) + "</td>" +
          '<td><button type="button" class="admin-action-btn" data-ui-anchor="llm-models.url-fetch-domain-remove" ' +
            'data-url-domain-remove="' + escHtml(d.domain) + '">削除</button></td>' +
        "</tr>";
    }
    list.innerHTML =
      '<table class="admin-table">' +
        "<thead><tr><th>ドメイン</th><th>登録日</th><th>操作</th></tr></thead>" +
        "<tbody>" + rows + "</tbody>" +
      "</table>";
  }

  function urlFetchDomainAdd() {
    if (_urlFetchDomainsBusy) return;
    var input = document.getElementById("url-fetch-domain-input");
    var domain = input ? input.value.trim() : "";
    if (!domain) {
      _urlFetchDomainsStatus("追加するドメインを入力してください。", "error");
      return;
    }
    _urlFetchDomainsBusy = true;
    _urlFetchDomainsStatus("追加しています...", "info");
    apiFetch("/admin/url-fetch-domains", {
      method: "POST",
      body: JSON.stringify({ domain: domain }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; }).then(function (d) { throw d; });
        }
        return res.json().catch(function () { return {}; });
      })
      .then(function () {
        _urlFetchDomainsBusy = false;
        if (input) input.value = "";
        _urlFetchDomainsStatus("ドメイン " + domain + " を許可リストに追加しました。", "success");
        loadUrlFetchDomains();
      })
      .catch(function (err) {
        _urlFetchDomainsBusy = false;
        _urlFetchDomainsStatus("追加に失敗しました: " + _urlFetchDetailText(err), "error");
      });
  }

  function urlFetchDomainRemove(domain) {
    if (_urlFetchDomainsBusy || !domain) return;
    if (!confirm("ドメイン " + domain + " を許可リストから削除します。以後このドメインからのURL取得はできなくなります。")) return;
    _urlFetchDomainsBusy = true;
    _urlFetchDomainsStatus("削除しています...", "info");
    apiFetch("/admin/url-fetch-domains/" + encodeURIComponent(domain), { method: "DELETE" })
      .then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; }).then(function (d) { throw d; });
        }
        return res.json().catch(function () { return {}; });
      })
      .then(function () {
        _urlFetchDomainsBusy = false;
        _urlFetchDomainsStatus("ドメイン " + domain + " を許可リストから削除しました。", "success");
        loadUrlFetchDomains();
      })
      .catch(function (err) {
        _urlFetchDomainsBusy = false;
        _urlFetchDomainsStatus("削除に失敗しました: " + _urlFetchDetailText(err), "error");
      });
  }

  // ── Course Builder Chat ────────────────────────────────────────────
  //
  // M層 Phase 3（llm_model_selection_design.md §6.2）: チャット右上のモデルチップ。
  // 選択は court_builder_sessions の DB スキーマ（history/course_draft/status 等の
  // 固定列のみ）に相乗りできる汎用フィールドが無いため、サーバへは永続化しない
  // （セッション更新 API に model 用の列を追加していない — 正直に記録する）。
  // 代わりにセッションID単位の in-memory map で「同一ページ滞在中のセッション切替」
  // だけ選択を復元する（ページ再読込・別端末では復元されない）。
  var _cbModelChip = null;
  var _cbModelBySession = {};
  var _cbLastAnnouncedModel = null;

  function _cbSessionKey() {
    return state.currentSessionId || "__new__";
  }

  function _cbCurrentModel() {
    return _cbModelBySession[_cbSessionKey()] || null;
  }

  // セッション切替時にチップの表示だけを追従させる（onChange は発火させない=通知しない）。
  function _cbSyncModelChipToSession() {
    _cbLastAnnouncedModel = null;
    if (_cbModelChip) _cbModelChip.setModel(_cbModelBySession[_cbSessionKey()] || null);
  }

  function _initCourseBuilderModelChip() {
    var mount = document.getElementById("cb-chat-model-chip");
    if (!mount || !window.AdminLlmModels) return;
    _cbModelChip = window.AdminLlmModels.createModelChip({
      sceneKey: "course_builder",
      mountEl: mount,
      compact: true,
      anchorId: "course-builder.model-chip",
      onChange: function (model) {
        _cbModelBySession[_cbSessionKey()] = model;
        var label = model || (_cbModelChip ? _cbModelChip.getModel() : null);
        // 既に対話が始まっている場合のみ、以降の応答がこのモデルになることを
        // 区切り線として履歴表示に追記する（chatHistory には積まず表示専用）。
        if (label && label !== _cbLastAnnouncedModel && state.chatMessages && state.chatMessages.length > 0) {
          state.chatMessages.push({ role: "divider", content: "ここから " + label + " で応答します" });
          renderCourseChat();
        }
        _cbLastAnnouncedModel = label;
      }
    });
  }

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
    // M層 Phase 3（§6.2）: モデルチップ
    _initCourseBuilderModelChip();
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

      html += '<div class="cb-mat-card' + (selected ? " selected" : "") + (selectable ? "" : " disabled") + '" data-ui-anchor="course-builder.material-card" data-mid="' + midE + '">';
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

    var selectHtml = '<select id="session-select" data-ui-anchor="course-builder.session-select" style="flex:1;padding:4px 6px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-primary);font-size:12px">';
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
      '<button id="new-session-btn" data-ui-anchor="course-builder.new-session-btn" style="padding:4px 10px;font-size:12px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-primary);cursor:pointer;white-space:nowrap">+ 新規</button>' +
      '<button id="import-course-btn" data-ui-anchor="course-builder.import-course-btn" style="padding:4px 10px;font-size:12px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:4px;color:var(--color-text-info);cursor:pointer;white-space:nowrap">既存コースを読込</button>' +
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
    _cbSyncModelChipToSession();
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
        // "__new__" キーに積んでいた選択を、実際のセッションIDへ引き継ぐ。
        if (_cbModelBySession.hasOwnProperty("__new__")) {
          _cbModelBySession[data.session_id] = _cbModelBySession["__new__"];
          delete _cbModelBySession.__new__;
        }
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
        _cbSyncModelChipToSession();
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
        // M層 Phase 3（§6.2）: この実行だけのモデル上書き。未選択（null）なら
        // サーバ側の既定解決順序（ユーザー既定 → システム既定 → env → tier）に委ねる。
        model: _cbCurrentModel(),
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

        // session_saved の正直化（設計書 §6）: 保存に失敗していても回答自体は表示済みの
        // うえで、警告のみ画面表示する（chatHistory には積まず表示専用）。
        if (data.session_saved === false) {
          state.chatMessages.push({
            role: "warning",
            content: "セッションの保存に失敗しました。会話履歴が保存されていない可能性があります。",
          });
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
      } else if (msg.role === "warning") {
        html += '<div class="mg ai" style="color:var(--color-text-warning);font-size:12px;">⚠ ' + escHtml(msg.content) + "</div>";
      } else if (msg.role === "divider") {
        // M層 Phase 3（§6.2）: モデル変更の区切り事実文。chatHistory には積まず表示専用。
        html += '<div class="cb-model-divider" style="text-align:center;font-size:11px;' +
          'color:var(--color-text-tertiary);margin:8px 0">── ' + escHtml(msg.content) + " ──</div>";
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

        // リリース前の確認（release_review_flow_design.md §2 の入口1）: 登録直後に
        // 「AI が作った地図を提示 → 次へ（＝承認）→ 公開」のウィザードを開く。
        // ウィザードが読み込まれていない環境では従来のインラインパネルへ縮退する（RR7）。
        if (window.AdminReleaseReview) {
          window.AdminReleaseReview.open(newCourseId, draft.title || "", { autoOpened: true });
        } else {
          // S2: コース登録直後に分野の地図への配置を提案する (教員が承認するまで確定しない)
          var cbAtlasArea = document.getElementById("cb-atlas-binding-area");
          if (cbAtlasArea) {
            cbAtlasArea.style.display = "";
            atlasBindingPropose(
              newCourseId,
              document.getElementById("cb-atlas-binding-body"),
              document.getElementById("cb-atlas-binding-status"),
              null,
              { courseTitle: draft.title || "", courseDescription: draft.description || "" }
            );
          }
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
  // initAtlas の外から「この分野にフォーカスしてタブを開く」ために使う関数。
  // initAtlas 実行前（タブ未初期化）は null のまま。
  var atlasAdminFocusDomain = null;

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
    // ドメインライフサイクル（migration 057）: retire/restore ボタンと読み取り専用の注記
    var retireBtn = document.getElementById("atlas-domain-retire");
    var restoreBtn = document.getElementById("atlas-domain-restore");
    var retiredNoteEl = document.getElementById("atlas-domain-retired-note");
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
    // ドメインライフサイクル（migration 057）: domain_key → "active" | "retired"
    // meta 行の無いドメイン（同梱カートリッジ等）は active とみなす（未登録キーは既定 active）。
    var domainLifecycles = {};
    // タブ外（コースビルダー導線等）から特定分野へフォーカスして開くための予約キー。
    var pendingFocusKey = null;
    // M層 Phase 3（§6.5）: 骨格生成モデルのチップ（生成ボタンの隣）。
    var _atlasModelChip = null;
    var atlasModelChipMount = document.getElementById("atlas-generate-model-chip");
    if (atlasModelChipMount && window.AdminLlmModels) {
      _atlasModelChip = window.AdminLlmModels.createModelChip({
        sceneKey: "atlas",
        mountEl: atlasModelChipMount,
        compact: true,
        onChange: function () {}
      });
    }

    // retired 中は生成・保存・凍結を無効化し、retire/restore ボタンの表示を切り替える。
    function updateLifecycleUI() {
      var isRetired = !!select.value && domainLifecycles[select.value] === "retired";
      if (retireBtn) retireBtn.style.display = (select.value && !isRetired) ? "" : "none";
      if (restoreBtn) restoreBtn.style.display = isRetired ? "" : "none";
      if (retiredNoteEl) retiredNoteEl.style.display = isRetired ? "" : "none";
      if (generateBtn) generateBtn.disabled = isRetired;
      var saveDraftBtn = document.getElementById("atlas-save-draft");
      if (saveDraftBtn) saveDraftBtn.disabled = isRetired;
      var freezeBtnEl = document.getElementById("atlas-freeze");
      if (freezeBtnEl) freezeBtnEl.disabled = isRetired;
    }

    // select の <option> 表示ラベルに廃止済み表示を反映する（一覧の再取得はせず表示のみ更新）。
    function refreshDomainOptionLabel(key, retired) {
      for (var i = 0; i < select.options.length; i++) {
        if (select.options[i].value === key) {
          var label = select.options[i].textContent.replace(/(?:\s*（廃止済み）)+$/, "");
          select.options[i].textContent = retired ? (label + "（廃止済み）") : label;
          return;
        }
      }
    }

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
      updateLifecycleUI();
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
        updateLifecycleUI();
        loadGapCandidates();
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
      // カテゴリギャップ候補（migration 066）。ポーリングはせず、この読み込みと
      // 各操作の成功後だけ取り直す。
      loadGapCandidates();
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
          html += "<div data-ui-anchor='atlas.report-resolve' style='display:flex;gap:6px;margin-top:6px;flex-wrap:wrap'>" +
            "<button class='admin-action-btn' data-report-action='accept'>採用（次版へ）</button>" +
            "<button class='admin-action-btn' data-report-action='decline'>見送り（理由つき）</button>" +
            "<button class='admin-action-btn' data-report-action='merge'>重複統合</button>" +
            "</div>";
        } else if (r.status === "accepted" && !r.incorporated_at && !r.applied_version) {
          html += "<div data-ui-anchor='atlas.report-incorporate' style='display:flex;gap:6px;margin-top:6px;flex-wrap:wrap'>" +
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

    // ── 論文の解析から見つかった候補（カテゴリギャップ候補, migration 066） ─────
    // 正本: docs/features/category_gap_candidates_design.md
    //   §5.4 レビュー UI（修正報告セクション内の第2グループ。専用タブを作らない）
    //   §5.5 骨格への反映は additive-only。**骨格を書くのは常に教員の PUT draft**
    //        （preview → 既存の draft 保存経路 → mark-incorporated の3手）
    //   LS1 / AB1 「この地図では言い表せなかった主題」の事実文。エラー色・警告
    //        アイコン・欠陥語彙・督促を使わない（候補は不備ではなく発見）
    //   LS5  件数バッジ・カバー率を出さない（支持論文はタイトルの列挙で示す）
    //   G1 / PN-2 キューはサーバの読み時導出。完了フラグをクライアントに持たない
    //   LS9  読み取り・patch 生成とも非LLM。ポーリングしない（タブ表示時と操作成功後のみ）
    var GAP_GROUP_TITLE = "論文の解析から見つかった候補";
    var GAP_GROUP_INTRO = "複数の論文が、この地図にまだ無い項目に触れています。";
    var GAP_ORIGIN_LABEL = "AIによる検出（未確認）";
    var GAP_ACCEPT_NOTE = "[採用] は「カテゴリとして妥当」という判断だけを記録します。次版の下書きはこの操作では変わりません。";
    var GAP_DISMISS_NOTE = "この分野で同じ名前の候補は今後表示されません。「見送り済み」フィルタから戻せます。";
    var GAP_AT_CAPACITY_TEXT = "この領域の概念は上限（6件）に達しています。追加するには次版で既存概念の整理が必要です。";
    var GAP_NO_DRAFT_TEXT = "次版の下書きがまだありません。「現在の版から次版の下書きを作る」を実行すると取り込めます。";
    var GAP_NOT_ACCEPTED_TEXT = "[採用] を押すと、次版の下書きに取り込めます。";
    var GAP_RETIRED_TEXT = "この分野は廃止済みです。「復帰する」で戻すと操作できます。";
    var GAP_EMPTY_TEXT = "いまレビューする候補はありません。";
    var GAP_DISMISS_REASON_REQUIRED = "却下には理由が必要です";
    var GAP_FREEZE_PENDING_TEXT = "採用済みでまだ次版に反映されていない候補が残っています";
    var GAP_FREEZE_REANALYSIS_TEXT = "既存論文の配置は再解析するまで変わりません";

    var gapIncludeDismissed = false;
    var gapBusy = false;
    var latestGapData = null;
    // 教員がその場で書き換えた提案ラベル（cluster_key → 表記）。判断のたびに一覧を
    // 読み直すため、編集途中の名前が消えないようクライアント側で保持する。
    var gapLabelEdits = {};
    var gapsGroupEl = null;
    var gapsListEl = null;
    var gapsStatusEl = null;

    function gapsPath() {
      return "/admin/cartridges/" + encodeURIComponent(select.value) + "/atlas/gap-candidates";
    }

    function setGapsStatus(text, isError) {
      if (!gapsStatusEl) return;
      gapsStatusEl.textContent = text || "";
      gapsStatusEl.style.color = isError ? "var(--color-text-danger, #e53935)" : "var(--color-text-secondary)";
    }

    // 修正報告セクション内の第2グループとして生成する（admin.html は変更しない。
    // 「下書きを破棄」ボタンと同じ後付けパターン）。
    function buildGapsGroup() {
      var section = document.getElementById("atlas-reports-section");
      if (!section || document.getElementById("atlas-gaps-group")) return;
      var group = document.createElement("div");
      group.id = "atlas-gaps-group";
      group.setAttribute("data-ui-anchor", "atlas.gap-candidates");
      group.style.cssText = "margin-top:18px;padding-top:14px;border-top:1px solid var(--color-border)";
      group.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:6px">' +
          '<h4 style="font-size:13px;margin:0">' + escHtml(GAP_GROUP_TITLE) + '</h4>' +
          '<label data-ui-anchor="atlas.gap-dismissed-filter" style="font-size:12px;color:var(--color-text-secondary);display:flex;align-items:center;gap:4px">' +
            '<input type="checkbox" id="atlas-gaps-show-dismissed"> 見送り済みも表示' +
          '</label>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 8px">' + escHtml(GAP_GROUP_INTRO) + '</p>' +
        '<div id="atlas-gaps-list" style="font-size:12.5px"></div>' +
        '<div id="atlas-gaps-status" style="font-size:12.5px;color:var(--color-text-secondary);margin-top:8px"></div>';
      section.appendChild(group);
      gapsGroupEl = group;
      gapsListEl = document.getElementById("atlas-gaps-list");
      gapsStatusEl = document.getElementById("atlas-gaps-status");
      group.style.display = "none";

      document.getElementById("atlas-gaps-show-dismissed").addEventListener("change", function () {
        gapIncludeDismissed = !!this.checked;
        loadGapCandidates();
      });
      gapsListEl.addEventListener("input", function (e) {
        var input = e.target;
        if (!input || !input.classList || !input.classList.contains("atlas-gap-label-input")) return;
        var owner = input.closest ? input.closest("[data-cluster-key]") : null;
        if (owner) gapLabelEdits[owner.getAttribute("data-cluster-key")] = input.value;
      });
      gapsListEl.addEventListener("click", function (e) {
        var btn = e.target.closest ? e.target.closest("[data-gap-action]") : null;
        if (!btn || btn.disabled) return;
        var action = btn.getAttribute("data-gap-action");
        if (action === "draft-from-frozen") { gapCreateDraftFromFrozen(); return; }
        var card = btn.closest("[data-cluster-key]");
        if (!card) return;
        handleGapAction(card.getAttribute("data-cluster-key"), action, card);
      });
    }

    // 骨格が無い分野（404）はグループごと非表示にする（fail-closed。空の枠を出さない）。
    function loadGapCandidates() {
      if (!gapsGroupEl) return;
      if (!select.value) {
        latestGapData = null;
        gapsGroupEl.style.display = "none";
        return;
      }
      apiFetch(gapsPath() + (gapIncludeDismissed ? "?include_dismissed=true" : ""))
        .then(function (res) {
          if (res.status === 404) return null;
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then(function (data) {
          if (!data) {
            latestGapData = null;
            gapsGroupEl.style.display = "none";
            return;
          }
          gapsGroupEl.style.display = "";
          renderGapCandidates(data);
          setGapsStatus("");
        })
        .catch(function (err) {
          latestGapData = null;
          gapsGroupEl.style.display = "";
          if (gapsListEl) gapsListEl.innerHTML = "";
          setGapsStatus("候補の読み込みに失敗しました: " + err.message, true);
        });
    }

    function _gapLayerBadgeText(candidate) {
      var layer = (candidate && candidate.layer) || "";
      if (layer === "concept") {
        var parent = (candidate && (candidate.parent_region_label || candidate.parent_region_id)) || "";
        return "概念（親: " + parent + "）";
      }
      return (candidate && candidate.layer_label) || "領域";
    }

    // 旧版の地図に対する信号を含む候補の事実チップ（件数・エラー色を付けない）。
    function _gapVersionChipHtml(candidate) {
      if (!candidate || !candidate.version_mismatch) return "";
      var docs = candidate.documents || [];
      var version = "";
      for (var i = 0; i < docs.length; i++) {
        if (docs[i] && docs[i].version_mismatch && docs[i].skeleton_version) {
          version = docs[i].skeleton_version;
          break;
        }
      }
      if (!version) return "";
      return '<span class="atlas-gap-version-chip" style="border:1px solid var(--color-border);border-radius:4px;padding:0 6px;font-size:11px;color:var(--color-text-secondary)">' +
        escHtml("この候補は版 " + version + " の地図に対するものです") + '</span>';
    }

    // LS5: 支持論文は件数バッジではなくタイトルの列挙。行を開くと理由と原文の逐語が出る。
    function _gapDocumentsHtml(candidate) {
      var docs = (candidate && candidate.documents) || [];
      if (!docs.length) return "";
      var html = '<div class="atlas-gap-documents" style="margin-top:6px">' +
        '<div style="font-size:11.5px;color:var(--color-text-tertiary)">この項目に触れている論文</div>';
      docs.forEach(function (d) {
        html += '<details class="atlas-gap-document" style="margin:2px 0">' +
          '<summary style="font-size:12px;color:var(--color-text-secondary);cursor:pointer">' +
            escHtml((d && d.title) || "無題の論文") +
          '</summary>';
        if (d && d.reason) {
          html += '<div style="font-size:12px;color:var(--color-text-secondary);padding:2px 0 0 12px">' + escHtml(d.reason) + '</div>';
        }
        if (d && d.evidence_quote) {
          html += '<div style="font-size:12px;color:var(--color-text-secondary);border-left:2px solid var(--color-border-tertiary);padding:2px 0 2px 8px;margin:4px 0 4px 12px">' +
            escHtml(d.evidence_quote) + '</div>';
        }
        html += '</details>';
      });
      html += '</div>';
      return html;
    }

    // 取り込みボタンが押せない理由の事実文（ゲージ・空きスロット・督促は出さない）。
    function _gapBlockedText(candidate, accepted, draftExists, retired) {
      if (retired) return GAP_RETIRED_TEXT;
      if (candidate && candidate.parent_region_at_capacity) return GAP_AT_CAPACITY_TEXT;
      if (!accepted) return GAP_NOT_ACCEPTED_TEXT;
      if (!draftExists) return GAP_NO_DRAFT_TEXT;
      return "";
    }

    function _gapCandidateCardHtml(candidate, draftExists, retired) {
      var clusterKey = (candidate && candidate.cluster_key) || "";
      var decision = (candidate && candidate.decision) || null;
      var status = (decision && decision.status) || "candidate";
      var accepted = status === "accepted";
      var suppressed = status === "dismissed" || status === "merged";
      var atCapacity = !!(candidate && candidate.parent_region_at_capacity);
      var html = '<div class="atlas-gap-card" data-cluster-key="' + escHtml(clusterKey) +
        '" style="border:1px solid var(--color-border);border-radius:6px;padding:8px 10px;margin-bottom:8px">';
      var labelValue = gapLabelEdits.hasOwnProperty(clusterKey)
        ? gapLabelEdits[clusterKey]
        : ((candidate && candidate.proposed_label) || "");
      html += '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
        '<input type="text" class="atlas-gap-label-input" value="' + escHtml(labelValue) +
          '" style="font-size:13px;padding:3px 6px;border:1px solid var(--color-border);border-radius:4px;min-width:220px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
        '<span class="atlas-gap-layer" style="font-size:11.5px;color:var(--color-text-secondary)">' + escHtml(_gapLayerBadgeText(candidate)) + '</span>' +
        '<span class="atlas-gap-origin" style="font-size:11px;color:var(--color-text-tertiary)">' + escHtml(GAP_ORIGIN_LABEL) + '</span>' +
        (decision && decision.status_label
          ? '<span class="atlas-gap-decision" style="font-size:11px;color:var(--color-text-secondary);border:1px solid var(--color-border);border-radius:4px;padding:0 6px">' + escHtml(decision.status_label) + '</span>'
          : "") +
        _gapVersionChipHtml(candidate) +
      '</div>';
      html += _gapDocumentsHtml(candidate);
      if (decision && decision.review_note) {
        html += '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:4px">判断のメモ: ' + escHtml(decision.review_note) + '</div>';
      }

      html += '<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">';
      if (suppressed) {
        html += '<button type="button" class="admin-action-btn" data-gap-action="restore" data-ui-anchor="atlas.gap-restore">見送りから戻す</button>';
      } else {
        html += '<button type="button" class="admin-action-btn" data-gap-action="accept" data-ui-anchor="atlas.gap-accept"' +
          (accepted || retired ? " disabled" : "") + '>採用</button>';
        html += '<button type="button" class="admin-action-btn" data-gap-action="dismiss" data-ui-anchor="atlas.gap-dismiss"' +
          (retired ? " disabled" : "") + '>却下…</button>';
        if (accepted && !draftExists) {
          html += '<button type="button" class="admin-action-btn" data-gap-action="draft-from-frozen" data-ui-anchor="atlas.gap-draft-from-frozen"' +
            (retired ? " disabled" : "") + '>現在の版から次版の下書きを作る</button>';
        } else {
          html += '<button type="button" class="admin-action-btn" data-gap-action="incorporate" data-ui-anchor="atlas.gap-incorporate"' +
            (!accepted || !draftExists || atCapacity || retired ? " disabled" : "") + '>次版の下書きに取り込む…</button>';
        }
      }
      html += '</div>';

      if (!suppressed) {
        html += '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:4px">' + escHtml(GAP_ACCEPT_NOTE) + '</div>';
        html += '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:2px">' + escHtml(GAP_DISMISS_NOTE) + '</div>';
        var blocked = _gapBlockedText(candidate, accepted, draftExists, retired);
        if (blocked) {
          html += '<div class="atlas-gap-blocked" style="font-size:11.5px;color:var(--color-text-secondary);margin-top:2px">' + escHtml(blocked) + '</div>';
        }
      }
      html += '</div>';
      return html;
    }

    function renderGapCandidates(data) {
      latestGapData = data || null;
      if (!gapsListEl) return;
      var candidates = (data && data.candidates) || [];
      var draftExists = !!(data && data.draft_exists);
      var retired = !!select.value && domainLifecycles[select.value] === "retired";
      if (!candidates.length) {
        gapsListEl.innerHTML = '<div style="color:var(--color-text-tertiary)">' + escHtml(GAP_EMPTY_TEXT) + '</div>';
        return;
      }
      var html = "";
      candidates.forEach(function (candidate) {
        html += _gapCandidateCardHtml(candidate, draftExists, retired);
      });
      gapsListEl.innerHTML = html;
    }

    // インライン編集した提案ラベル（空欄なら候補の表記をサーバ側で使う）。
    function _gapLabelFromCard(card) {
      var input = card ? card.querySelector(".atlas-gap-label-input") : null;
      return input ? input.value.trim() : "";
    }

    function handleGapAction(clusterKey, action, card) {
      if (!clusterKey || gapBusy) return;
      if (action === "accept" || action === "restore") {
        gapDecide(clusterKey, action, "");
        return;
      }
      if (action === "dismiss") {
        // 見送りは理由必須（空は送信しない。サーバ側も 422 で拒否する）。
        var note = prompt("却下の理由（必須）") || "";
        if (!note.trim()) { setGapsStatus(GAP_DISMISS_REASON_REQUIRED, true); return; }
        gapDecide(clusterKey, "dismiss", note);
        return;
      }
      if (action === "incorporate") gapIncorporate(clusterKey, _gapLabelFromCard(card));
    }

    // サーバの detail（事実文）をそのまま使う。取れないときだけ HTTP 状態へ縮退する。
    function _gapResponse(res) {
      return res.json().then(function (body) {
        if (!res.ok) {
          var detail = body && body.detail;
          throw new Error(
            typeof detail === "string" ? detail : ((detail && detail.message) || ("HTTP " + res.status))
          );
        }
        return body;
      });
    }

    function gapDecide(clusterKey, action, note) {
      gapBusy = true;
      setGapsStatus("処理中...");
      apiFetch(gapsPath() + "/decide", {
        method: "POST",
        body: JSON.stringify({ cluster_key: clusterKey, action: action, review_note: note || "" }),
      })
        .then(_gapResponse)
        .then(function () { gapBusy = false; setGapsStatus(""); loadGapCandidates(); })
        .catch(function (err) { gapBusy = false; setGapsStatus("処理に失敗しました: " + err.message, true); });
    }

    // 次版の下書きを現行凍結版から複製する（決定論・LLM 不使用）。
    function gapCreateDraftFromFrozen() {
      if (!select.value || gapBusy) return;
      var version = (latestGapData && latestGapData.skeleton_version) || "";
      if (!confirm("現行版 " + version + " を複製して下書きを作ります。下書きは学習者には表示されません")) return;
      gapBusy = true;
      setGapsStatus("次版の下書きを作成中...");
      apiFetch("/admin/cartridges/" + encodeURIComponent(select.value) + "/atlas/skeleton/draft/from-frozen", {
        method: "POST",
      })
        .then(_gapResponse)
        .then(function () {
          gapBusy = false;
          setGapsStatus("次版の下書きを作りました");
          loadState();
        })
        .catch(function (err) {
          gapBusy = false;
          setGapsStatus("下書きを作れませんでした: " + err.message, true);
        });
    }

    // 取り込みの3手 (1) 読み取り専用の patch プレビュー
    function gapIncorporate(clusterKey, label) {
      if (gapBusy) return;
      gapBusy = true;
      setGapsStatus("取り込む内容を確認中...");
      apiFetch(gapsPath() + "/incorporate-preview", {
        method: "POST",
        body: JSON.stringify({ cluster_key: clusterKey, proposed_label: label || "" }),
      })
        .then(_gapResponse)
        .then(function (preview) {
          gapBusy = false;
          setGapsStatus("");
          openGapIncorporateConfirm(clusterKey, preview || {});
        })
        .catch(function (err) {
          gapBusy = false;
          setGapsStatus("取り込みを準備できませんでした: " + err.message, true);
        });
    }

    function _gapIssueText(issue) {
      if (!issue) return "";
      if (typeof issue === "string") return issue;
      return issue.message || "";
    }

    // 取り込みの3手 (2) 事実文の確認（追加される node の id・名前・親領域 + 検証結果）
    function openGapIncorporateConfirm(clusterKey, preview) {
      var validation = preview.validation || {};
      var lines = [];
      if (preview.summary) lines.push(preview.summary);
      if (preview.node_id) lines.push("追加する項目のid: " + preview.node_id);
      if (preview.proposed_label) lines.push("名前: " + preview.proposed_label);
      if (preview.layer === "concept" && preview.parent_region_id) {
        lines.push("親領域: " + preview.parent_region_id);
      }
      (validation.errors || []).forEach(function (issue) {
        var text = _gapIssueText(issue);
        if (text) lines.push("検証: " + text);
      });
      (validation.warnings || []).forEach(function (issue) {
        var text = _gapIssueText(issue);
        if (text) lines.push("検証: " + text);
      });
      if (!preview.patched_draft) {
        setGapsStatus("この項目は下書きに追加できませんでした: " + (lines.join(" / ") || "内容を確認してください"), true);
        return;
      }
      lines.push("この内容を次版の下書きに保存します。学習者には表示されません。");
      openDangerConfirmModal({
        title: "次版の下書きに取り込む",
        message: lines,
        confirmLabel: "下書きに追加する",
      }, function () { gapApplyIncorporation(clusterKey, preview); });
    }

    // 取り込みの3手 (3) 教員の既存 PUT draft（applyAssistProposal → saveDraft。楽観ロック
    // 409 は saveDraft 側の事実文 + 再読込に委ねる）→ 成功後に mark-incorporated で刻印。
    // **骨格を書くのは常にこの PUT** — gap 側の API は下書きを書かない（LS7）。
    function gapApplyIncorporation(clusterKey, preview) {
      gapBusy = true;
      setGapsStatus("次版の下書きに保存中...");
      applyAssistProposal(preview.patched_draft)
        .then(function () {
          return apiFetch(gapsPath() + "/mark-incorporated", {
            method: "POST",
            body: JSON.stringify({ cluster_key: clusterKey, draft_node_id: preview.node_id || "" }),
          }).then(_gapResponse);
        })
        .then(function () {
          gapBusy = false;
          // 取り込み済みの候補は次の導出で消えるので、編集中の名前も持ち越さない。
          if (gapLabelEdits.hasOwnProperty(clusterKey)) delete gapLabelEdits[clusterKey];
          setGapsStatus("次版の下書きに追加しました");
          loadGapCandidates();
        })
        .catch(function (err) {
          gapBusy = false;
          setGapsStatus("取り込みに失敗しました: " + err.message, true);
          loadGapCandidates();
        });
    }

    buildGapsGroup();

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
            // migration 057: ドメインライフサイクル。meta 行の無いキーは既定 active。
            domainLifecycles[d.domain_key] = d.lifecycle || "active";
          });
          Object.keys(names).forEach(function (k) { keys[k] = true; });
          var sorted = Object.keys(keys).sort();
          sorted.forEach(function (k) {
            var label = names[k] ? names[k] + " (" + k + ")" : k;
            if (domainLifecycles[k] === "retired") label += "（廃止済み）";
            addDomainOption(k, label);
          });
          cartridgesLoaded = true;
          if (pendingFocusKey && keys[pendingFocusKey]) {
            select.value = pendingFocusKey;
            pendingFocusKey = null;
            loadState();
          } else if (sorted.length === 1) {
            pendingFocusKey = null;
            select.value = sorted[0];
            loadState();
          } else {
            pendingFocusKey = null;
            updateLifecycleUI();
          }
        })
        .catch(function () { setStatus("分野一覧の取得に失敗しました", true); });
    }

    // タブ外（コースビルダーの「このコースから新しい分野マップを作る」導線等）から
    // 特定分野へフォーカスして「分野の地図」タブを開くための入口。
    // 一覧は毎回取り直す（生成直後で新分野がまだ選択肢に無い可能性があるため）。
    function focusDomain(domainKey) {
      if (!domainKey) return;
      pendingFocusKey = domainKey;
      cartridgesLoaded = false;
      for (var i = select.options.length - 1; i >= 0; i--) {
        if (select.options[i].value !== "") select.remove(i);
      }
      select.value = "";
      loadCartridges();
      loadAtlasCourses();
    }
    atlasAdminFocusDomain = focusDomain;

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
      // M層 Phase 3（§6.5）: 骨格生成モデルのこの実行だけの上書き。未選択なら省略し、
      // サーバ側の既定解決順序に委ねる。
      var atlasModel = _atlasModelChip ? _atlasModelChip.getSelected() : null;
      if (atlasModel) payload.model = atlasModel;
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

    // ドメインライフサイクル（migration 057）: retire / restore。削除ではなく状態遷移のみ（AB3）。
    if (retireBtn) {
      retireBtn.addEventListener("click", function () {
        if (!select.value) return;
        if (!confirm("廃止すると新しいコースのバインド候補・照合対象から外れます。バインド済みコースの表示は維持されます。よろしいですか？")) return;
        var key = select.value;
        retireBtn.disabled = true;
        apiFetch("/admin/cartridges/" + encodeURIComponent(key) + "/atlas/retire", {
          method: "POST",
          body: JSON.stringify({ note: "" }),
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
          .then(function () {
            domainLifecycles[key] = "retired";
            refreshDomainOptionLabel(key, true);
            retireBtn.disabled = false;
            setStatus("この分野を廃止しました");
            loadState();
          })
          .catch(function (err) {
            retireBtn.disabled = false;
            setStatus("廃止に失敗しました: " + err.message, true);
          });
      });
    }
    if (restoreBtn) {
      restoreBtn.addEventListener("click", function () {
        if (!select.value) return;
        var key = select.value;
        restoreBtn.disabled = true;
        apiFetch("/admin/cartridges/" + encodeURIComponent(key) + "/atlas/restore", { method: "POST" })
          .then(function (res) {
            return res.json().then(function (body) {
              if (!res.ok) {
                var detail = body.detail;
                throw new Error(typeof detail === "string" ? detail : "HTTP " + res.status);
              }
              return body;
            });
          })
          .then(function () {
            domainLifecycles[key] = "active";
            refreshDomainOptionLabel(key, false);
            restoreBtn.disabled = false;
            setStatus("この分野を復帰しました");
            loadState();
          })
          .catch(function (err) {
            restoreBtn.disabled = false;
            setStatus("復帰に失敗しました: " + err.message, true);
          });
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

    var saveDraftBtnEl = document.getElementById("atlas-save-draft");
    saveDraftBtnEl.addEventListener("click", function () {
      saveDraft().catch(function () {});
    });

    // 下書きを破棄（レビュー2回目 修正1）: draft は作業コピーなので、retired（廃止済み）
    // ドメインでも破棄は許可する（後始末の手段）。admin.html には触れず JS 側で
    // 「次版を保存」ボタンの隣に生成する。draft が無いときは atlas-draft-area 自体が
    // display:none になるため、この場所に置けば自然に非表示になる。
    var discardDraftBtn = document.createElement("button");
    discardDraftBtn.type = "button";
    discardDraftBtn.id = "atlas-discard-draft";
    discardDraftBtn.setAttribute("data-ui-anchor", "atlas.discard-draft");
    discardDraftBtn.className = "admin-action-btn";
    discardDraftBtn.textContent = "下書きを破棄";
    saveDraftBtnEl.parentNode.insertBefore(discardDraftBtn, saveDraftBtnEl.nextSibling);
    discardDraftBtn.addEventListener("click", function () {
      if (!select.value) return;
      if (!confirm("現在の下書きを破棄します。凍結済みの版と学習者の表示には影響しません。よろしいですか？")) return;
      var key = select.value;
      discardDraftBtn.disabled = true;
      apiFetch("/admin/cartridges/" + encodeURIComponent(key) + "/atlas/skeleton/draft", { method: "DELETE" })
        .then(function (res) {
          if (res.status === 404) {
            discardDraftBtn.disabled = false;
            setStatus("破棄する下書きがありません", true);
            return null;
          }
          return res.json().then(function (body) {
            if (!res.ok) {
              var detail = body.detail;
              throw new Error(typeof detail === "string" ? detail : "HTTP " + res.status);
            }
            return body;
          });
        })
        .then(function (body) {
          discardDraftBtn.disabled = false;
          if (!body) return;
          setStatus("下書きを破棄しました");
          loadState();
        })
        .catch(function (err) {
          discardDraftBtn.disabled = false;
          setStatus("破棄に失敗しました: " + err.message, true);
        });
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
      var checkLines = [
        "公開版: " + version,
        "検証エラー: 0件",
        "検証警告: " + warningCount + "件",
        "未確認の修正報告: " + pendingCount + "件",
        "この次版を学習者向けに公開しますか？取り消せません（修正は次版で行います）。",
      ];

      function openFreezeChecklist() {
        openDangerConfirmModal({
          title: "分野の地図骨格を公開前チェック",
          message: checkLines,
          confirmLabel: "公開する",
        }, function () {
          setStatus("学習者向けに公開中...");
          apiFetch(basePath() + "/freeze", {
            method: "POST",
            body: JSON.stringify({ version: version, note: note }),
          })
            .then(function (res) {
              return res.json().then(function (body) {
                if (!res.ok) {
                  var detail = body.detail;
                  // カテゴリギャップ候補の公開前ゲート（migration 066）: 採用済みで
                  // まだ次版に反映されていない候補はラベルの列挙で示す（件数は出さない）。
                  if (detail && detail.pending_labels) {
                    scrollToAtlasSection("atlas-reports-section");
                    var labels = (detail.pending_labels || []).join("、");
                    throw new Error(
                      (detail.message || GAP_FREEZE_PENDING_TEXT) + (labels ? ": " + labels : "")
                    );
                  }
                  throw new Error(typeof detail === "string" ? detail : "HTTP " + res.status);
                }
                return body;
              });
            })
            .then(function () {
              // 追加した項目に既存の論文が並ぶのは再解析のときであることを事実で添える。
              setStatus("版 " + version + " を学習者向けに公開しました。" + GAP_FREEZE_REANALYSIS_TEXT);
              loadState();
              loadReports();
            })
            .catch(function (err) { setStatus("公開に失敗しました: " + err.message, true); });
        });
      }

      // §3.2: 凍結前に影響プレビューを取得し、影響があれば事実文で確認する（best-effort。
      // 404/失敗時は従来どおり凍結チェックリストへ進む — プレビューが無くても凍結は止めない）。
      apiFetch("/admin/cartridges/" + encodeURIComponent(select.value) + "/atlas/freeze-impact")
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (impact) {
          var removedCount = impact ? (impact.removed_node_ids || []).length : 0;
          var affectedCourses = impact ? (impact.affected_courses || []) : [];
          // サーバが添える事実文（facts[]）はそのまま公開前チェックへ差し込む。
          // 件数を足したり言い換えたりしない（category_gap_candidates_design.md §5.5）。
          var facts = impact ? (impact.facts || []) : [];
          if (facts.length) {
            var question = checkLines.pop();
            facts.forEach(function (fact) { if (fact) checkLines.push(fact); });
            checkLines.push(question);
          }
          if (removedCount > 0 || affectedCourses.length > 0) {
            var titles = affectedCourses.map(function (c) { return c.title; });
            var shown = titles.slice(0, 5).join("、") + (titles.length > 5 ? " …" : "");
            var proceed = confirm(
              "この凍結で " + removedCount + " 概念が削除され、" + affectedCourses.length +
              " コースの対応が外れます" + (shown ? ": " + shown : "") + "。" +
              (facts.length ? facts.join("\n") + "\n" : "") + "凍結しますか？"
            );
            if (!proceed) return;
          }
          openFreezeChecklist();
        })
        .catch(function () { openFreezeChecklist(); });
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

  function atlasBindingRenderEditor(bodyEl, statusEl, courseId, data, onSaved, options) {
    options = options || {};
    function setStatus(text, isError) {
      if (!statusEl) return;
      statusEl.textContent = text || "";
      statusEl.style.color = isError ? "var(--color-text-danger, #e53935)" : "var(--color-text-secondary)";
    }
    var proposals = data.proposals || [];
    var byKey = {};
    proposals.forEach(function (p) { byKey[p.domain_key] = p; });
    // §2.1 (atlas_binding_lifecycle_design.md): 0一致でも先頭ドメインへ fallback しない。
    // 該当なしは「（バインドしない）」が既定になる。
    var initial = data.recommended || data.current_cartridge_id || "";
    var noMatch = !data.recommended && !data.current_cartridge_id;
    var domainsChecked = (data.domains_checked != null) ? data.domains_checked : proposals.length;
    var retiredSkipped = data.retired_skipped || 0;
    // 現行バインドが候補一覧に無い場合（廃止済み分野など）、保存すると無言で解除されて
    // しまうため、明示の事実文で伝える。current_retired はバックエンド propose 応答の
    // 追加フィールド（未定義でも動作する）。
    var currentKey = data.current_cartridge_id || "";
    var currentMissing = !!(currentKey && !byKey[currentKey]);

    var html = "";

    // §2.3: 凍結待ちの仮予約（意思の記録。地図表示には影響しない）
    if (data.atlas_binding_pending) {
      html += "<div data-role='ab-pending-note' style='background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:6px;padding:8px 10px;margin-bottom:10px;font-size:12.5px'>" +
        "新しい分野『" + escHtml(data.atlas_binding_pending) + "』の骨格の凍結待ちです。凍結後、『次にやること』に割り当ての再開が表示されます。" +
        " <button type='button' data-role='ab-pending-cancel' class='admin-action-btn' style='margin-left:6px'>取り消す</button>" +
        "</div>";
    }

    // §2.1/2.2: 該当なしの事実文（煽らない・エラー表示にしない）+ 既定の説明
    if (noMatch) {
      html += "<div data-role='ab-nomatch-note' style='color:var(--color-text-secondary);font-size:12.5px;margin-bottom:8px'>" +
        "このコースのトピックに対応する分野マップは見つかりませんでした（" + escHtml(String(domainsChecked)) + "分野を照合）。" +
        (retiredSkipped > 0 ? "<br><small style='color:var(--color-text-tertiary)'>（廃止済み " + escHtml(String(retiredSkipped)) + " 分野は照合対象外）</small>" : "") +
        "<br>今はバインドしなくても、『次にやること』からいつでも再開できます。" +
        "</div>";
    } else if (!proposals.length) {
      html += "<div style='color:var(--color-text-tertiary);font-size:12.5px;margin-bottom:8px'>凍結済みの骨格がありません。下記から新しい分野マップを作るか、「分野の地図」タブで骨格を生成・凍結してください。</div>";
    }

    // 現行バインドが候補に無い（廃止済み等）場合の事実文。保存しない限り現状維持であることを
    // 明示し、無言の解除を防ぐ（AB1: 事実文のみ・警告色にしない）。
    if (currentMissing) {
      html += "<div data-role='ab-current-missing-note' style='color:var(--color-text-secondary);font-size:12.5px;margin-bottom:8px'>" +
        (data.current_retired === true
          ? "現在バインドされている分野『" + escHtml(currentKey) + "』は廃止済みのため、候補には表示されません。"
          : "現在バインドされている分野『" + escHtml(currentKey) + "』は候補にありません。") +
        "<br>保存しない限り現在のバインドは維持されます（学習者の地図表示は変わりません）。" +
        "解除するには「（バインドしない）」を選んで保存してください。" +
        "</div>";
    }

    html += "<div style='display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px'>";
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
    html += "<div style='margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;align-items:center'>" +
      // リリース前の確認ウィザード（release_review_flow_design.md §3.2）は、この保存
      // ボタンをそのまま「次へ」として使う（保存ペイロードを再実装しない）。ラベルと
      // 追加アンカーだけ options で差し替える。既定は従来どおり。
      "<button data-role='ab-save' data-ui-anchor='" + escHtml(options.saveAnchor || "atlas.binding-save") +
        "' class='admin-action-btn'>" + escHtml(options.saveLabel || "この対応で反映") + "</button>" +
      "<button type='button' data-role='ab-new-domain-toggle' data-ui-anchor='atlas.binding-new-domain' class='admin-action-btn'>このコースから新しい分野マップを作る</button>" +
      "</div>";

    // §2.3: コース起点の新分野作成（インラインのミニフォーム。既定は非表示）
    html += "<div data-role='ab-new-domain-form' style='display:none;margin-top:10px;border:1px solid var(--color-border);border-radius:6px;padding:10px'>";
    html += "<div style='font-size:12px;color:var(--color-text-secondary);margin-bottom:6px'>新しい分野マップの下書きを作成します。作成後は「分野の地図」タブでレビュー・凍結してください。</div>";
    html += "<label style='font-size:12px;display:block;margin-bottom:2px'>管理ID（domain_key。英小文字・数字・アンダースコア）</label>";
    html += "<input type='text' data-role='ab-new-domain-key' placeholder='例: modified_gravity' style='width:100%;padding:4px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary);margin-bottom:6px;box-sizing:border-box'>";
    html += "<label style='font-size:12px;display:block;margin-bottom:2px'>分野名</label>";
    html += "<input type='text' data-role='ab-new-domain-name' value='" + escHtml(options.courseTitle || "") + "' style='width:100%;padding:4px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary);margin-bottom:6px;box-sizing:border-box'>";
    html += "<label style='font-size:12px;display:block;margin-bottom:2px'>説明（任意）</label>";
    html += "<textarea data-role='ab-new-domain-desc' rows='2' style='width:100%;padding:4px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary);margin-bottom:6px;box-sizing:border-box'>" + escHtml(options.courseDescription || "") + "</textarea>";
    html += "<div style='display:flex;gap:8px;align-items:center;flex-wrap:wrap'>" +
      "<button type='button' data-role='ab-new-domain-submit' data-ui-anchor='atlas.binding-new-domain' class='admin-action-btn'>下書きを生成</button>" +
      "<span data-role='ab-new-domain-status' style='font-size:12px;color:var(--color-text-secondary)'></span>" +
      "</div>";
    html += "</div>";
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

    // §2.3: 凍結待ちの仮予約の取り消し
    var pendingCancelBtn = bodyEl.querySelector("[data-role='ab-pending-cancel']");
    if (pendingCancelBtn) {
      pendingCancelBtn.addEventListener("click", function () {
        pendingCancelBtn.disabled = true;
        apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/atlas-binding/pending", { method: "DELETE" })
          .then(function (res) {
            if (!res.ok) {
              return res.json().then(function (body) {
                throw new Error(typeof body.detail === "string" ? body.detail : "HTTP " + res.status);
              });
            }
            return res.json().catch(function () { return {}; });
          })
          .then(function () {
            var note = bodyEl.querySelector("[data-role='ab-pending-note']");
            if (note && note.parentNode) note.parentNode.removeChild(note);
            if (window.AdminNextSteps) window.AdminNextSteps.refresh();
          })
          .catch(function (err) {
            pendingCancelBtn.disabled = false;
            setStatus("仮予約の取り消しに失敗しました: " + err.message, true);
          });
      });
    }

    bodyEl.querySelector("[data-role='ab-save']").addEventListener("click", function () {
      var bindings = [];
      var selects = tableEl.querySelectorAll("[data-role='ab-topic']");
      var anyBound = false;
      for (var i = 0; i < selects.length; i++) {
        var v = selects[i].value;
        if (v) anyBound = true;
        bindings.push({
          topic_id: selects[i].getAttribute("data-topic-id"),
          atlas_node_id: v,
        });
      }
      // §2.4: 0一致のまま明示バインドを保存しようとしたときだけ確認を挟む（禁止はしない）
      if (domainSelect.value && !anyBound) {
        var okToSave = window.confirm(
          "この地図には現在このコースの対応（足がかり）がありません。学習者には現在地が表示されない地図が出ます。反映しますか？"
        );
        if (!okToSave) return;
      }
      // 現行バインドが候補に無い（廃止済み等）まま「（バインドしない）」で保存しようとした
      // ときは、無言の解除にならないよう明示確認を挟む（上記0一致確認とは独立の分岐）。
      if (currentMissing && !domainSelect.value) {
        var okToUnbind = window.confirm(
          "現在のバインド（分野『" + currentKey + "』）とトピックの対応をすべて解除します。よろしいですか？"
        );
        if (!okToUnbind) return;
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
          // §2.3: バインド保存が成功すると仮予約はサーバ側で自動クリアされるため表示も消す
          var pendingNote = bodyEl.querySelector("[data-role='ab-pending-note']");
          if (pendingNote && pendingNote.parentNode) pendingNote.parentNode.removeChild(pendingNote);
          // G層: course.no_atlas_binding が解消され得るため再取得する。
          if (window.AdminNextSteps) window.AdminNextSteps.refresh();
          document.dispatchEvent(new CustomEvent("atlas:binding-saved"));
          if (onSaved) onSaved(body);
        })
        .catch(function (err) { setStatus("保存に失敗しました: " + err.message, true); });
    });

    // §2.3: このコースから新しい分野マップを作る（凍結待ち仮予約 → 骨格 draft 生成）
    var newDomainToggle = bodyEl.querySelector("[data-role='ab-new-domain-toggle']");
    var newDomainForm = bodyEl.querySelector("[data-role='ab-new-domain-form']");
    if (newDomainToggle && newDomainForm) {
      newDomainToggle.addEventListener("click", function () {
        newDomainForm.style.display = newDomainForm.style.display === "none" ? "" : "none";
      });
      var newDomainSubmit = bodyEl.querySelector("[data-role='ab-new-domain-submit']");
      var newDomainStatusEl = bodyEl.querySelector("[data-role='ab-new-domain-status']");
      function setNewDomainStatus(text, isError) {
        if (!newDomainStatusEl) return;
        newDomainStatusEl.textContent = text || "";
        newDomainStatusEl.style.color = isError ? "var(--color-text-danger, #e53935)" : "var(--color-text-secondary)";
      }
      newDomainSubmit.addEventListener("click", function () {
        var keyInput = bodyEl.querySelector("[data-role='ab-new-domain-key']");
        var nameInput = bodyEl.querySelector("[data-role='ab-new-domain-name']");
        var descInput = bodyEl.querySelector("[data-role='ab-new-domain-desc']");
        var key = (keyInput.value || "").trim();
        var name = (nameInput.value || "").trim();
        var desc = (descInput.value || "").trim();
        if (!/^[a-z0-9_]+$/.test(key)) {
          setNewDomainStatus("管理IDは英小文字・数字・アンダースコアで入力してください", true);
          return;
        }
        if (!name) { setNewDomainStatus("分野名を入力してください", true); return; }
        newDomainSubmit.disabled = true;
        setNewDomainStatus("仮予約を保存中...");
        apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/atlas-binding/pending", {
          method: "PUT",
          body: JSON.stringify({ domain_key: key }),
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
          .then(function () {
            // ①成功: 仮予約が保存された。以降②が失敗しても仮予約は残る。
            if (window.AdminNextSteps) window.AdminNextSteps.refresh();
            setNewDomainStatus("骨格の下書きを生成中...");
            return apiFetch("/admin/cartridges/" + encodeURIComponent(key) + "/atlas/skeleton/generate", {
              method: "POST",
              body: JSON.stringify({ domain: { name: name, description: desc } }),
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
              .then(function () {
                newDomainSubmit.disabled = false;
                setNewDomainStatus("");
                newDomainForm.innerHTML =
                  "<div style='font-size:12.5px;color:var(--color-text-secondary)'>下書きを生成しました。『分野の地図』タブでレビュー・凍結してください。</div>" +
                  "<button type='button' data-role='ab-new-domain-open-atlas' class='admin-action-btn' style='margin-top:6px'>分野の地図タブを開く</button>";
                var openAtlasBtn = newDomainForm.querySelector("[data-role='ab-new-domain-open-atlas']");
                if (openAtlasBtn) {
                  openAtlasBtn.addEventListener("click", function () {
                    if (typeof activateTabView === "function") activateTabView("atlas");
                    // タブの実クリックハンドラ (onTabActivate) は activateTabView からは
                    // 発火しないため、新分野へフォーカスして一覧・状態を明示的に読み込む。
                    // initAtlas 未実行環境（タブ未初期化）へのフォールバックはタブの実クリック。
                    if (atlasAdminFocusDomain) {
                      atlasAdminFocusDomain(key);
                    } else {
                      var tabBtn = document.querySelector('.admin-tab[data-tab="atlas"]');
                      if (tabBtn) tabBtn.click();
                    }
                  });
                }
              })
              .catch(function (err) {
                newDomainSubmit.disabled = false;
                setNewDomainStatus(
                  "下書きの生成に失敗しました（仮予約は保存済み。分野の地図タブから再生成できます）: " + err.message,
                  true
                );
              });
          })
          .catch(function (err) {
            newDomainSubmit.disabled = false;
            setNewDomainStatus("仮予約の保存に失敗しました: " + err.message, true);
          });
      });
    }
  }

  function atlasBindingPropose(courseId, bodyEl, statusEl, onSaved, options) {
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
        atlasBindingRenderEditor(bodyEl, statusEl, courseId, data, onSaved, options);
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
    // コース起点の新分野作成フォームに prefill するための title/description。
    // コースビルダー承認直後の導線 (atlasBindingPropose の呼び出し箇所を参照) と同じ
    // options 形にして atlasBindingRenderEditor へ渡す。
    var courseMeta = {};

    function loadCourses() {
      if (coursesLoaded) return;
      apiFetch("/admin/courses")
        .then(function (res) { return res.json(); })
        .then(function (courses) {
          (courses || []).forEach(function (c) {
            var opt = document.createElement("option");
            opt.value = c.id;
            var label = c.title + " (" + c.id + ")";
            // atlas-binding の propose/save/pending は所有者または SYSTEM_ADMIN のみ許可
            // （バックエンド側ゲート）。viewer/editor のコースは選んでも 403 になるため、
            // 選択肢の時点で操作不可を明示する（SYSTEM_ADMIN は全コース操作可なので除外しない）。
            if (c.role !== "owner" && state.role !== "SYSTEM_ADMIN") {
              opt.disabled = true;
              label += "（所有者のみ操作できます）";
            }
            opt.textContent = label;
            select.appendChild(opt);
            courseMeta[c.id] = { title: c.title || "", description: c.description || "" };
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
      var meta = courseMeta[select.value] || {};
      atlasBindingPropose(select.value, bodyEl, statusEl, null, {
        courseTitle: meta.title || "",
        courseDescription: meta.description || "",
      });
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
    // 知識ネットワークビジョン Phase B — 橋の候補は独立セクションなので、関心集約本体の
    // 早期 return（コース未選択・データなし）に巻き込まれないよう常に呼ぶ。
    loadBridgeInsights(courseId);
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

  // ── 知識ネットワークビジョン Phase B — 橋の候補（教員向け唯一の窓口, G2-B）────
  // GET /api/admin/courses/{course_id}/bridge-insights をそのまま事実文で提示する。
  // 個人特定・数値スコア・ランキング表現は作らない（k-匿名集約はサーバ側 core/privacy.py が正本）。
  function loadBridgeInsights(courseId) {
    var body = document.getElementById("bridge-insights-body");
    if (!body) return;
    if (!courseId) {
      body.innerHTML = '<div style="color:var(--color-text-tertiary)">コースを選択してください</div>';
      return;
    }
    body.innerHTML = '<div style="color:var(--color-text-tertiary)">読み込み中…</div>';
    apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/bridge-insights")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(function (data) {
        var bridges = data.bridges || [];
        if (!bridges.length) {
          body.innerHTML = '<div style="color:var(--color-text-tertiary)">まだ表示できる集約はありません（3名以上の確定痕跡が必要です）。</div>';
          return;
        }
        var html = '<ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.8">';
        bridges.forEach(function (b) {
          var kindLabel = b.ref_type === "graph_edge" ? "構造グラフの関係" : "コンポーネント";
          var name = b.label ? escHtml(b.label) : (kindLabel + "（" + escHtml(b.ref_id || "") + "）");
          html += "<li>" + name +
            ' <span style="color:var(--color-text-tertiary);font-size:11px">（' +
            escHtml(b.learner_count_range || "") + '名の学習者が結びつけました）</span></li>';
        });
        html += "</ul>";
        html += '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:8px">' +
          escHtml(data.note || "") + "</div>";
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
            '<input class="error-log-select-row" data-ui-anchor="error-analysis.select-row" type="checkbox" data-log-id="' + escHtml(logId) + '">' +
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
  // トークンを捨ててログイン画面に戻す共通処理。ヘッダーのログアウトボタンと、
  // 自分のパスワードを再設定した直後（トークンが即失効する）の両方から呼ぶ。
  function performLogout() {
    state.token = null;
    state.username = null;
    localStorage.removeItem("eg_token");
    localStorage.removeItem("eg_username");
    renderAuth();
  }

  function initLogout() {
    document.getElementById("logout-btn").addEventListener("click", function () {
      performLogout();
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

    // アカウントライフサイクル（一覧・停止・再開・パスワード再設定・利用状況・削除予約・移管）
    alInitAccountLifecycle();
  }

  // ── アカウントライフサイクル管理（一覧・停止・再開・パスワード再設定・利用状況・削除予約・移管） ──
  // 正本: docs/features/account_lifecycle_management_design.md §9。
  //   AL2 停止は認証の拒否のみ（所有権・共有・受講状態には触らない。文言でもそう書く）
  //   AL7 学生の個票（パスワード再設定・利用状況・削除予約）は SYSTEM_ADMIN のみ描画する
  //   AL10 自分自身と Administrator は停止・削除できない（サーバ側 422。UI では理由付きで無効化）
  // 一覧の再取得はタブを開いたとき・操作が成功したとき・「一覧を更新」を押したときだけ
  // （ポーリングしない）。段階の付いた評価値・煽り文言は出さない。
  var AL_SCOPES = {
    students: {
      role: "learner",
      label: "学生",
      listId: "al-students-list",
      msgId: "al-students-msg",
      searchId: "al-students-search",
      refreshId: "al-students-refresh"
    },
    teachers: {
      role: "instructor",
      label: "教員",
      listId: "al-teachers-list",
      msgId: "al-teachers-msg",
      searchId: "al-teachers-search",
      refreshId: "al-teachers-refresh"
    }
  };

  // data-ui-anchor（マニュアル節参照）は論理IDを素の文字列で持つ。
  // students の reset / activity / delete は SYSTEM_ADMIN 限定のため system_admin/ 節を指す。
  var AL_ANCHORS = {
    students: {
      list: "students.user-list",
      suspend: "students.user-suspend",
      reset: "students.user-reset",
      activity: "students.user-activity",
      remove: "students.user-delete",
      transfer: ""
    },
    teachers: {
      list: "teachers.user-list",
      suspend: "teachers.user-suspend",
      reset: "teachers.user-reset",
      activity: "teachers.user-activity",
      remove: "teachers.user-delete",
      transfer: "teachers.user-transfer"
    }
  };

  var AL_STATUS_LABELS = {
    active: "有効",
    suspended: "停止中",
    pending_deletion: "削除予定",
    deleted: "削除済み"
  };

  var AL_AUTH_EVENT_LABELS = {
    login_success: "ログイン成功",
    login_failed: "ログイン失敗",
    login_rejected_suspended: "停止中のログイン試行",
    token_rejected_suspended: "停止中のアクセス",
    token_rejected_stale: "失効トークンのアクセス",
    password_reset: "パスワード再設定"
  };

  // U1: 実測と推計を混ぜた単一数値を出さない（種別を分けて並べる）。
  // activity API は reported / estimated の2バケットで返す（推計の内訳は合算済み）。
  var AL_USAGE_SOURCE_LABELS = {
    reported: "実測（プロバイダの報告値）",
    estimated: "推計（トークン数の換算値）"
  };
  var AL_USAGE_BUCKETS = ["reported", "estimated"];

  var AL_MIN_PASSWORD_LENGTH = 8;

  function alAccountSectionHtml(scope) {
    var cfg = AL_SCOPES[scope];
    return '<div class="admin-section" style="margin-top:24px">' +
      '<h3 class="admin-section-title">' + cfg.label + 'アカウント一覧</h3>' +
      '<p style="color:var(--color-text-secondary);font-size:12.5px;margin:0 0 10px">' +
        '停止すると、そのアカウントはログインできなくなります。教材・コース・グループの所有と共有、' +
        '受講状態は停止しても変わりません。' +
      '</p>' +
      '<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">' +
        '<input id="' + cfg.searchId + '" type="text" placeholder="ユーザー名・メールで絞り込む" ' +
          'style="flex:1;max-width:280px;padding:6px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
        '<button id="' + cfg.refreshId + '" class="admin-action-btn" type="button">一覧を更新</button>' +
      '</div>' +
      '<div id="' + cfg.listId + '" data-ui-anchor="' + AL_ANCHORS[scope].list + '">' +
        '<p style="color:var(--color-text-tertiary)">読み込み中...</p>' +
      '</div>' +
      '<div id="' + cfg.msgId + '" class="upload-status"></div>' +
    '</div>';
  }

  function alSetMessage(scope, text, kind) {
    var el = document.getElementById(AL_SCOPES[scope].msgId);
    if (!el) return;
    el.textContent = text || "";
    el.className = "upload-status" + (kind ? " upload-status-" + kind : "");
  }

  function alErrorDetail(err) {
    if (!err) return "不明なエラー";
    return err.detail || err.message || "不明なエラー";
  }

  function alExpectOk(res) {
    if (!res.ok) {
      return res.json().catch(function () { return {}; }).then(function (d) { throw d; });
    }
    return res.json().catch(function () { return {}; });
  }

  function alStatusChip(status) {
    var label = AL_STATUS_LABELS[status] || status || "";
    var color = "var(--color-text-secondary)";
    if (status === "suspended") color = "var(--color-text-warning)";
    if (status === "pending_deletion" || status === "deleted") color = "var(--color-text-danger)";
    return '<span class="al-status-chip" data-al-status="' + escHtml(status || "") + '" ' +
      'style="font-size:11.5px;padding:1px 6px;border:1px solid ' + color + ';border-radius:10px;color:' + color + '">' +
      escHtml(label) + '</span>';
  }

  function alActionBtn(action, anchor, label, uid, uname) {
    return '<button type="button" class="admin-action-btn al-action-btn"' +
      ' data-ui-anchor="' + anchor + '"' +
      ' data-al-action="' + escHtml(action) + '"' +
      ' data-al-user-id="' + escHtml(uid) + '"' +
      ' data-al-username="' + escHtml(uname) + '"' +
      ' style="padding:3px 8px;font-size:11.5px">' + escHtml(label) + '</button>';
  }

  function alDisabledBtn(anchor, label, reason) {
    return '<button type="button" class="admin-action-btn al-action-btn" disabled' +
      ' data-ui-anchor="' + anchor + '" title="' + escHtml(reason) + '"' +
      ' style="padding:3px 8px;font-size:11.5px;opacity:.5;cursor:not-allowed">' + escHtml(label) + '</button>';
  }

  function alUserRowHtml(scope, u) {
    var a = AL_ANCHORS[scope];
    var isSystemAdmin = state.role === "SYSTEM_ADMIN";
    var status = u.status || "active";
    var uid = u.id || "";
    var uname = u.username || "";
    // AL10: 自分自身と bootstrap Administrator は停止・削除できない。
    var locked = (uid && uid === _meUserId()) || uname === "Administrator";
    var btns = [];

    if (status !== "deleted") {
      if (status === "active") {
        if (locked) {
          btns.push(alDisabledBtn(a.suspend, "停止…",
            "自分自身のアカウントと Administrator は停止できません。別のシステム管理者に依頼してください。"));
        } else {
          btns.push(alActionBtn("suspend", a.suspend, "停止…", uid, uname));
        }
      } else if (status === "suspended") {
        btns.push(alActionBtn("restore", a.suspend, "再開", uid, uname));
      }

      // AL7: 個票・パスワード再設定・削除予約は SYSTEM_ADMIN のみ。
      if (isSystemAdmin) {
        btns.push(alActionBtn("reset", a.reset, "パスワード再設定…", uid, uname));
        btns.push(alActionBtn("activity", a.activity, "利用状況…", uid, uname));
        if (status === "pending_deletion") {
          btns.push(alActionBtn("cancel-deletion", a.remove, "削除予約を取消", uid, uname));
        } else if (status === "suspended" && !locked) {
          btns.push(alActionBtn("deletion", a.remove, "削除予約…", uid, uname));
        } else {
          btns.push(alDisabledBtn(a.remove, "削除予約…", locked
            ? "自分自身のアカウントと Administrator は削除できません。"
            : "削除予約は、先に停止したアカウントにだけできます。「停止…」を実行してください。"));
        }
        if (a.transfer) {
          btns.push(alActionBtn("transfer", a.transfer, "移管…", uid, uname));
        }
      }
    }

    return '<tr data-al-user-row="' + escHtml(uid) + '">' +
      '<td>' + escHtml(uname) + '</td>' +
      '<td style="font-size:12px;color:var(--color-text-secondary)">' + escHtml(u.email || "") + '</td>' +
      '<td>' + alStatusChip(status) + '</td>' +
      '<td style="font-size:12px">' + escHtml(u.last_login_at ? formatDateTime(u.last_login_at) : "—") + '</td>' +
      '<td style="font-size:12px">' + escHtml(u.last_seen_at ? formatDateTime(u.last_seen_at) : "—") + '</td>' +
      '<td><div style="display:flex;gap:4px;flex-wrap:wrap">' + btns.join("") + '</div></td>' +
    '</tr>';
  }

  function alRenderUsers(scope, users) {
    var el = document.getElementById(AL_SCOPES[scope].listId);
    if (!el) return;
    if (!users || users.length === 0) {
      el.innerHTML = '<p style="color:var(--color-text-tertiary)">該当するアカウントはありません。</p>';
      return;
    }
    var rows = "";
    for (var i = 0; i < users.length; i++) rows += alUserRowHtml(scope, users[i]);
    el.innerHTML =
      '<table class="admin-table"><thead><tr>' +
        '<th>ユーザー名</th><th>メール</th><th>状態</th><th>最終ログイン</th><th>最終アクセス</th><th>操作</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>' +
      '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:6px 0 0">' +
        '「最終アクセス」は書き込みを抑えるため数分間隔で更新される目安の値です。' +
      '</p>';
  }

  function alLoadUsers(scope) {
    var cfg = AL_SCOPES[scope];
    var listEl = document.getElementById(cfg.listId);
    if (!listEl) return;
    var searchEl = document.getElementById(cfg.searchId);
    var q = searchEl ? searchEl.value.trim() : "";
    var path = "/admin/users?role=" + encodeURIComponent(cfg.role) + "&limit=200";
    if (q) path += "&q=" + encodeURIComponent(q);
    listEl.innerHTML = '<p style="color:var(--color-text-tertiary)">読み込み中...</p>';
    apiFetch(path)
      .then(alExpectOk)
      .then(function (data) {
        alRenderUsers(scope, (data && data.users) || []);
      })
      .catch(function (err) {
        listEl.innerHTML = '<p style="color:var(--color-text-danger);font-size:12.5px">' +
          '一覧を取得できませんでした: ' + escHtml(alErrorDetail(err)) + '</p>';
      });
  }

  function alSuspendUser(scope, uid, uname) {
    var reason = prompt("停止の理由（必須・監査記録に残ります）");
    if (reason === null) return;
    reason = reason.trim();
    if (!reason) {
      alSetMessage(scope, "停止の理由を入力してください。理由なしでは停止できません。", "error");
      return;
    }
    alSetMessage(scope, "「" + uname + "」を停止しています...", "info");
    apiFetch("/admin/users/" + encodeURIComponent(uid) + "/suspend", {
      method: "POST",
      body: JSON.stringify({ reason: reason })
    })
      .then(alExpectOk)
      .then(function () {
        alSetMessage(scope, "「" + uname + "」を停止しました。ログインできなくなります。" +
          "教材・コース・グループの所有と共有、受講状態は変わりません。", "success");
        alLoadUsers(scope);
      })
      .catch(function (err) {
        alSetMessage(scope, "停止できませんでした: " + alErrorDetail(err), "error");
      });
  }

  function alRestoreUser(scope, uid, uname) {
    alSetMessage(scope, "「" + uname + "」を再開しています...", "info");
    apiFetch("/admin/users/" + encodeURIComponent(uid) + "/restore", { method: "POST" })
      .then(alExpectOk)
      .then(function () {
        alSetMessage(scope, "「" + uname + "」を再開しました。ログインできる状態に戻りました。", "success");
        alLoadUsers(scope);
      })
      .catch(function (err) {
        alSetMessage(scope, "再開できませんでした: " + alErrorDetail(err), "error");
      });
  }

  function alOpenPasswordResetModal(scope, uid, uname) {
    var existing = document.getElementById("al-reset-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "al-reset-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:380px;max-width:460px">' +
        '<h3 style="margin:0 0 12px;font-size:15px;color:var(--color-text-primary)">パスワードの再設定</h3>' +
        '<p style="font-size:12.5px;color:var(--color-text-primary);margin:0 0 8px">「' + escHtml(uname) + '」の新しいパスワードを設定します。</p>' +
        '<p style="font-size:12px;color:var(--color-text-secondary);margin:0 0 12px">' +
          '再設定すると、このアカウントで発行済みのログイン状態はすべて無効になり、次回から新しいパスワードが必要になります。' +
          '新しいパスワードは本人に個別に伝えてください（画面には再表示されません）。' +
        '</p>' +
        '<input type="password" id="al-reset-pw" autocomplete="new-password" placeholder="新しいパスワード（' + AL_MIN_PASSWORD_LENGTH + '文字以上）" ' +
          'style="width:100%;padding:8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary);box-sizing:border-box;margin-bottom:8px">' +
        '<input type="password" id="al-reset-pw2" autocomplete="new-password" placeholder="確認のためもう一度入力" ' +
          'style="width:100%;padding:8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary);box-sizing:border-box;margin-bottom:10px">' +
        '<div id="al-reset-error" style="display:none;color:var(--color-text-danger);font-size:12px;margin-bottom:8px"></div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button type="button" id="al-reset-cancel" class="admin-action-btn">キャンセル</button>' +
          '<button type="button" id="al-reset-exec" class="admin-action-btn" data-ui-anchor="' + AL_ANCHORS[scope].reset + '" ' +
            'style="background:var(--color-text-danger);color:#fff" disabled>再設定する</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    var pw = document.getElementById("al-reset-pw");
    var pw2 = document.getElementById("al-reset-pw2");
    var execBtn = document.getElementById("al-reset-exec");
    var errEl = document.getElementById("al-reset-error");

    function validate() {
      var v1 = pw.value;
      var v2 = pw2.value;
      var msg = "";
      if (v1.length > 0 && v1.length < AL_MIN_PASSWORD_LENGTH) {
        msg = "パスワードは" + AL_MIN_PASSWORD_LENGTH + "文字以上で入力してください。";
      } else if (v2.length > 0 && v1 !== v2) {
        msg = "確認用のパスワードが一致しません。";
      }
      errEl.textContent = msg;
      errEl.style.display = msg ? "block" : "none";
      execBtn.disabled = !(v1.length >= AL_MIN_PASSWORD_LENGTH && v1 === v2);
    }
    pw.addEventListener("input", validate);
    pw2.addEventListener("input", validate);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    document.getElementById("al-reset-cancel").addEventListener("click", function () { overlay.remove(); });

    execBtn.addEventListener("click", function () {
      execBtn.disabled = true;
      execBtn.textContent = "再設定中...";
      apiFetch("/admin/users/" + encodeURIComponent(uid) + "/password-reset", {
        method: "POST",
        body: JSON.stringify({ new_password: pw.value })
      })
        .then(alExpectOk)
        .then(function (data) {
          overlay.remove();
          if (data && data.self_reset) {
            // 自分自身のパスワードを再設定した場合は、いま使っているログイン状態も無効になる。
            alert("パスワードを再設定しました。いま使っているログイン状態は無効になりました。新しいパスワードで再ログインしてください。");
            performLogout();
            return;
          }
          alSetMessage(scope, "「" + uname + "」のパスワードを再設定しました。" +
            "このアカウントの発行済みログイン状態は無効になりました。", "success");
          alLoadUsers(scope);
        })
        .catch(function (err) {
          execBtn.disabled = false;
          execBtn.textContent = "再設定する";
          errEl.textContent = "再設定できませんでした: " + alErrorDetail(err);
          errEl.style.display = "block";
        });
    });

    pw.focus();
  }

  function alTokenText(value) {
    if (value === null || value === undefined || value === "") return "—";
    return String(value);
  }

  // activity API の llm_usage は reported / estimated を別オブジェクトで返す
  // （U1: 合算した単一数値を作らない）。表示もバケットごとの行に分けたまま並べる。
  function alUsageBucketRows(usage) {
    var rows = [];
    for (var i = 0; i < AL_USAGE_BUCKETS.length; i++) {
      var name = AL_USAGE_BUCKETS[i];
      var bucket = (usage && usage[name]) || null;
      if (!bucket) continue;
      rows.push({
        usage_source: name,
        prompt_tokens: bucket.prompt_tokens,
        completion_tokens: bucket.completion_tokens,
        total_tokens: bucket.total_tokens,
        calls: bucket.calls
      });
    }
    return rows;
  }

  function alUsageHasRecords(rows) {
    for (var i = 0; i < rows.length; i++) {
      if (Number(rows[i].calls || 0) > 0 || Number(rows[i].total_tokens || 0) > 0) return true;
    }
    return false;
  }

  function alUsageSummaryHtml(usage) {
    var html = '<h4 style="font-size:13px;margin:16px 0 6px">AI（LLM）の利用実績</h4>';
    if (!usage || !usage.available) {
      html += '<p style="font-size:12.5px;color:var(--color-text-secondary);margin:0">' +
        '利用実績を取得できませんでした。（記録がないという意味ではありません）</p>';
      return html;
    }
    var days = usage.window_days || 30;
    html += '<p style="font-size:12px;color:var(--color-text-secondary);margin:0 0 8px">' +
      '直近' + days + '日間の記録です。実測と推計は別の行として表示します（足し合わせた数値は出しません）。' +
      'バックグラウンドのバッチ処理は利用者に結び付かない「未帰属」として記録されるため、ここには出ません。' +
      '</p>';
    var rows = alUsageBucketRows(usage);
    if (!alUsageHasRecords(rows)) {
      html += '<p style="font-size:12.5px;color:var(--color-text-secondary);margin:0">この期間の記録はありません。</p>';
    } else {
      html += '<table class="admin-table"><thead><tr>' +
        '<th>種別</th><th>入力トークン</th><th>出力トークン</th><th>合計トークン</th><th>呼び出し回数</th>' +
        '</tr></thead><tbody>';
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        html += '<tr><td>' + escHtml(AL_USAGE_SOURCE_LABELS[r.usage_source] || r.usage_source) + '</td>' +
          '<td style="font-size:12px">' + escHtml(alTokenText(r.prompt_tokens)) + '</td>' +
          '<td style="font-size:12px">' + escHtml(alTokenText(r.completion_tokens)) + '</td>' +
          '<td style="font-size:12px">' + escHtml(alTokenText(r.total_tokens)) + '</td>' +
          '<td style="font-size:12px">' + escHtml(alTokenText(r.calls)) + '</td></tr>';
      }
      html += '</tbody></table>';
    }
    var features = usage.top_features || [];
    if (features.length) {
      html += '<h5 style="font-size:12.5px;margin:12px 0 4px">よく使われた機能</h5>' +
        '<table class="admin-table"><thead><tr><th>機能</th><th>実測（合計トークン）</th><th>推計（合計トークン）</th></tr></thead><tbody>';
      for (var j = 0; j < features.length; j++) {
        var f = features[j] || {};
        var rep = f.reported || {};
        var est = f.estimated || {};
        html += '<tr><td style="font-size:12px">' + escHtml(f.feature || "（未帰属）") + '</td>' +
          '<td style="font-size:12px">' + escHtml(alTokenText(rep.total_tokens)) + '</td>' +
          '<td style="font-size:12px">' + escHtml(alTokenText(est.total_tokens)) + '</td></tr>';
      }
      html += '</tbody></table>';
    }
    return html;
  }

  function alActivityHtml(data) {
    var user = (data && data.user) || {};
    var events = (data && data.auth_events) || [];
    var html = '<p style="font-size:12.5px;color:var(--color-text-secondary);margin:0 0 6px">' +
      '状態: ' + alStatusChip(user.status || "active") +
      ' ／ 最終ログイン: ' + escHtml(user.last_login_at ? formatDateTime(user.last_login_at) : "記録がありません") +
      ' ／ 最終アクセス: ' + escHtml(user.last_seen_at ? formatDateTime(user.last_seen_at) : "記録がありません") +
      '</p>';
    // 個票だけが持つ情報（一覧には含まれない）: 停止の理由と削除予定日。
    if (user.status_reason) {
      html += '<p style="font-size:12.5px;color:var(--color-text-secondary);margin:0 0 6px">' +
        '停止・削除予約の理由: ' + escHtml(user.status_reason) + '</p>';
    }
    if (user.purge_after) {
      html += '<p style="font-size:12.5px;color:var(--color-text-secondary);margin:0 0 6px">' +
        '削除予定: ' + escHtml(formatDateTime(user.purge_after)) +
        '（それまでは「削除予約を取消」で元に戻せます）</p>';
    }
    html += '<h4 style="font-size:13px;margin:0 0 6px">ログインの記録</h4>';
    if (!events.length) {
      html += '<p style="font-size:12.5px;color:var(--color-text-secondary);margin:0">記録はまだありません。</p>';
    } else {
      html += '<table class="admin-table"><thead><tr><th>日時</th><th>できごと</th><th>IPアドレス</th><th>ブラウザ</th></tr></thead><tbody>';
      for (var i = 0; i < events.length; i++) {
        var e = events[i] || {};
        html += '<tr><td style="font-size:12px;white-space:nowrap">' + escHtml(formatDateTime(e.created_at)) + '</td>' +
          '<td style="font-size:12px">' + escHtml(AL_AUTH_EVENT_LABELS[e.event] || e.event || "") + '</td>' +
          '<td style="font-size:12px">' + escHtml(e.ip_address || "—") + '</td>' +
          '<td style="font-size:11.5px;max-width:260px;word-break:break-all">' + escHtml(e.user_agent || "—") + '</td></tr>';
      }
      html += '</tbody></table>';
    }
    html += alUsageSummaryHtml(data && data.llm_usage);
    html += '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:12px 0 0">' +
      'この記録はアカウントの運用（不正利用・休眠アカウントの確認）のために表示しています。学習の評価には使いません。' +
      '</p>';
    return html;
  }

  function alOpenActivityModal(scope, uid, uname) {
    var existing = document.getElementById("al-activity-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "al-activity-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:520px;max-width:760px;max-height:80vh;overflow:auto">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:15px;color:var(--color-text-primary)">「' + escHtml(uname) + '」の利用状況</h3>' +
          '<button type="button" id="al-activity-close" class="admin-action-btn" data-ui-anchor="' + AL_ANCHORS[scope].activity + '">閉じる</button>' +
        '</div>' +
        '<div id="al-activity-body"><p style="color:var(--color-text-tertiary)">読み込み中...</p></div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    document.getElementById("al-activity-close").addEventListener("click", function () { overlay.remove(); });

    apiFetch("/admin/users/" + encodeURIComponent(uid) + "/activity")
      .then(alExpectOk)
      .then(function (data) {
        var body = document.getElementById("al-activity-body");
        if (body) body.innerHTML = alActivityHtml(data);
      })
      .catch(function (err) {
        var body = document.getElementById("al-activity-body");
        if (body) {
          body.innerHTML = '<p style="color:var(--color-text-danger);font-size:12.5px">' +
            '利用状況を取得できませんでした: ' + escHtml(alErrorDetail(err)) + '</p>';
        }
      });
  }

  // 削除予約は取消可能だが行き先が破壊的なので、コース削除と同型の「対象名入力」確認にする。
  function alOpenDeletionModal(scope, uid, uname) {
    var existing = document.getElementById("al-deletion-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "al-deletion-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border-secondary);border-radius:8px;padding:24px;min-width:420px;max-width:520px">' +
        '<h3 style="margin:0 0 12px;font-size:16px;color:var(--color-text-danger)">アカウントの削除予約</h3>' +
        '<p style="font-size:13px;color:var(--color-text-primary);margin:0 0 8px">以下のアカウントの削除を予約します:</p>' +
        '<p style="font-size:14px;font-weight:600;color:var(--color-text-primary);margin:0 0 12px;padding:8px;background:var(--color-bg-secondary);border-radius:4px">' +
          escHtml(uname) +
        '</p>' +
        '<p style="font-size:12px;color:var(--color-text-secondary);margin:0 0 6px">' +
          '予約してもすぐには消えません。既定では14日の猶予期間があり、その間は「削除予約を取消」で元に戻せます。' +
        '</p>' +
        '<p style="font-size:12px;color:var(--color-text-secondary);margin:0 0 10px">' +
          '教材・コース・グループを所有しているアカウントは、猶予期間が過ぎても削除されません。先に「移管…」で引き継ぎ先を決めてください。' +
        '</p>' +
        '<label style="display:block;font-size:12.5px;color:var(--color-text-secondary);margin-bottom:10px">猶予日数（空欄のままだと既定の14日）' +
          '<input type="number" id="al-deletion-grace" min="1" max="365" placeholder="14" ' +
            'style="width:100px;margin-left:8px;padding:6px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
        '</label>' +
        '<p style="font-size:13px;color:var(--color-text-secondary);margin:0 0 8px">確認のため、ユーザー名を正確に入力してください:</p>' +
        '<input type="text" id="al-deletion-confirm-input" placeholder="' + escHtml(uname) + '" ' +
          'style="width:100%;padding:8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary);box-sizing:border-box;margin-bottom:12px">' +
        '<div id="al-deletion-error" style="display:none;color:var(--color-text-danger);font-size:12px;margin-bottom:8px"></div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button type="button" id="al-deletion-cancel" class="admin-action-btn">キャンセル</button>' +
          '<button type="button" id="al-deletion-exec" class="admin-action-btn" data-ui-anchor="' + AL_ANCHORS[scope].remove + '" ' +
            'style="background:var(--color-text-danger);color:#fff" disabled>削除を予約する</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    var input = document.getElementById("al-deletion-confirm-input");
    var execBtn = document.getElementById("al-deletion-exec");
    var errEl = document.getElementById("al-deletion-error");

    input.addEventListener("input", function () {
      execBtn.disabled = (input.value !== uname);
    });
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    document.getElementById("al-deletion-cancel").addEventListener("click", function () { overlay.remove(); });

    execBtn.addEventListener("click", function () {
      execBtn.disabled = true;
      execBtn.textContent = "予約中...";
      var graceEl = document.getElementById("al-deletion-grace");
      var body = {};
      var graceRaw = graceEl ? graceEl.value.trim() : "";
      if (graceRaw) body.grace_days = parseInt(graceRaw, 10);
      apiFetch("/admin/users/" + encodeURIComponent(uid) + "/deletion", {
        method: "POST",
        body: JSON.stringify(body)
      })
        .then(alExpectOk)
        .then(function (data) {
          overlay.remove();
          var when = (data && data.purge_after) ? formatDateTime(data.purge_after) : "";
          alSetMessage(scope, "「" + uname + "」の削除を予約しました。" +
            (when ? "削除予定は " + when + " です。" : "") +
            "それまでは「削除予約を取消」で元に戻せます。", "success");
          alLoadUsers(scope);
        })
        .catch(function (err) {
          execBtn.disabled = false;
          execBtn.textContent = "削除を予約する";
          errEl.textContent = "削除を予約できませんでした: " + alErrorDetail(err);
          errEl.style.display = "block";
        });
    });

    input.focus();
  }

  function alCancelDeletion(scope, uid, uname) {
    alSetMessage(scope, "「" + uname + "」の削除予約を取り消しています...", "info");
    apiFetch("/admin/users/" + encodeURIComponent(uid) + "/deletion", { method: "DELETE" })
      .then(alExpectOk)
      .then(function () {
        alSetMessage(scope, "「" + uname + "」の削除予約を取り消しました。停止中の状態に戻りました。", "success");
        alLoadUsers(scope);
      })
      .catch(function (err) {
        alSetMessage(scope, "削除予約を取り消せませんでした: " + alErrorDetail(err), "error");
      });
  }

  function alOpenTransferModal(scope, uid, uname) {
    var existing = document.getElementById("al-transfer-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "al-transfer-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:420px;max-width:520px">' +
        '<h3 style="margin:0 0 12px;font-size:15px;color:var(--color-text-primary)">所有物の移管</h3>' +
        '<p style="font-size:12.5px;color:var(--color-text-primary);margin:0 0 8px">' +
          '「' + escHtml(uname) + '」が所有している教材・コース・グループを、別の教員または管理者に引き継ぎます。' +
        '</p>' +
        '<p style="font-size:12px;color:var(--color-text-secondary);margin:0 0 10px">' +
          '受講者の学習状態・共有設定・発行済みの版はそのまま残ります。発行済みの版に記録された発行者名は、発行時点の事実として変わりません。' +
        '</p>' +
        '<label style="display:block;font-size:12.5px;color:var(--color-text-secondary);margin-bottom:12px">引き継ぎ先' +
          '<select id="al-transfer-target" style="width:100%;margin-top:4px;padding:7px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
            '<option value="">読み込み中...</option>' +
          '</select>' +
        '</label>' +
        '<div id="al-transfer-error" style="display:none;color:var(--color-text-danger);font-size:12px;margin-bottom:8px"></div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button type="button" id="al-transfer-cancel" class="admin-action-btn">キャンセル</button>' +
          '<button type="button" id="al-transfer-exec" class="admin-action-btn" data-ui-anchor="' + AL_ANCHORS[scope].transfer + '" ' +
            'style="background:var(--color-text-danger);color:#fff" disabled>移管する</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    var select = document.getElementById("al-transfer-target");
    var execBtn = document.getElementById("al-transfer-exec");
    var errEl = document.getElementById("al-transfer-error");

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });
    document.getElementById("al-transfer-cancel").addEventListener("click", function () { overlay.remove(); });

    // 移管先は「有効な TEACHER 以上」= instructor + admin（設計 §5）。教員が1人だけの
    // 環境では管理者が唯一の正当な引き取り先になるため、admin を候補から外さない
    // （外すと AL9 により削除予約が永久に完了せず、通知が出続ける）。
    // 学生で limit を食い潰さないよう、ロールごとに取得してマージする。
    Promise.all([
      apiFetch("/admin/users?role=instructor&status=active&limit=200").then(alExpectOk),
      apiFetch("/admin/users?role=admin&status=active&limit=200").then(alExpectOk)
    ])
      .then(function (results) {
        var users = [];
        for (var r = 0; r < results.length; r++) {
          var chunk = (results[r] && results[r].users) || [];
          for (var k = 0; k < chunk.length; k++) users.push(chunk[k]);
        }
        var opts = '<option value="">選択してください</option>';
        var seen = {};
        for (var i = 0; i < users.length; i++) {
          if (!users[i] || !users[i].id || users[i].id === uid) continue;
          if (seen[users[i].id]) continue;
          seen[users[i].id] = true;
          // レスポンスの role はアプリ語彙（TEACHER / SYSTEM_ADMIN）。
          var suffix = users[i].role === "SYSTEM_ADMIN" ? "（管理者）" : "";
          opts += '<option value="' + escHtml(users[i].id) + '">' +
            escHtml(users[i].username || "") + suffix + '</option>';
        }
        select.innerHTML = opts;
      })
      .catch(function (err) {
        select.innerHTML = '<option value="">取得できませんでした</option>';
        errEl.textContent = "引き継ぎ先の候補を取得できませんでした: " + alErrorDetail(err);
        errEl.style.display = "block";
      });

    select.addEventListener("change", function () {
      execBtn.disabled = !select.value;
    });

    execBtn.addEventListener("click", function () {
      var targetId = select.value;
      if (!targetId) return;
      var targetName = select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : "";
      openDangerConfirmModal({
        title: "所有物の移管",
        message: [
          "「" + uname + "」が所有する教材・コース・グループの所有者が「" + targetName + "」に変わります。",
          "移管後、元の所有者はこれらを自分の教材・コースとして操作できなくなります。"
        ],
        confirmLabel: "移管する"
      }, function () {
        overlay.remove();
        alSetMessage(scope, "「" + uname + "」の所有物を移管しています...", "info");
        apiFetch("/admin/users/" + encodeURIComponent(uid) + "/transfer-ownership", {
          method: "POST",
          body: JSON.stringify({ to_user_id: targetId })
        })
          .then(alExpectOk)
          .then(function (data) {
            var t = (data && data.transferred) || {};
            alSetMessage(scope, "「" + targetName + "」に移管しました（教材 " + (t.documents || 0) +
              " 件・コース " + (t.courses || 0) + " 件・グループ " + (t.groups || 0) + " 件）。", "success");
            alLoadUsers(scope);
          })
          .catch(function (err) {
            alSetMessage(scope, "移管できませんでした: " + alErrorDetail(err), "error");
          });
      });
    });
  }

  function alHandleAction(scope, action, uid, uname) {
    if (!uid) return;
    if (action === "suspend") { alSuspendUser(scope, uid, uname); return; }
    if (action === "restore") { alRestoreUser(scope, uid, uname); return; }
    if (action === "reset") { alOpenPasswordResetModal(scope, uid, uname); return; }
    if (action === "activity") { alOpenActivityModal(scope, uid, uname); return; }
    if (action === "deletion") { alOpenDeletionModal(scope, uid, uname); return; }
    if (action === "cancel-deletion") { alCancelDeletion(scope, uid, uname); return; }
    if (action === "transfer") { alOpenTransferModal(scope, uid, uname); return; }
  }

  // Copilot 道案内（G8）: 一覧の行に描かれた操作ボタンの代表1件を返す。
  // 一覧が未読込・その操作が非活性のときは null（案内はそこで止まる = P8 fail-closed）。
  function _alRowActionAnchor(scope, action) {
    var listEl = document.getElementById(AL_SCOPES[scope].listId);
    if (!listEl) return null;
    return listEl.querySelector('[data-al-action="' + action + '"]');
  }

  function alInitAccountLifecycle() {
    Object.keys(AL_SCOPES).forEach(function (scope) {
      var cfg = AL_SCOPES[scope];
      var listEl = document.getElementById(cfg.listId);
      // 教員管理パネルは SYSTEM_ADMIN 以外では生成されない（存在しなければ何もしない）。
      if (!listEl) return;
      listEl.addEventListener("click", function (e) {
        var btn = e.target && e.target.closest ? e.target.closest("[data-al-action]") : null;
        if (!btn || btn.disabled) return;
        alHandleAction(
          scope,
          btn.getAttribute("data-al-action"),
          btn.getAttribute("data-al-user-id"),
          btn.getAttribute("data-al-username")
        );
      });
      var refreshBtn = document.getElementById(cfg.refreshId);
      if (refreshBtn) refreshBtn.addEventListener("click", function () { alLoadUsers(scope); });
      var searchEl = document.getElementById(cfg.searchId);
      if (searchEl) {
        searchEl.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); alLoadUsers(scope); }
        });
      }
      // 取得はタブを開いたときだけ（ポーリングしない）。
      onTabActivate(scope, function () { alLoadUsers(scope); });
    });
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

      // U層（LLM使用量推計, migration 043）— SYSTEM_ADMIN のみメトリクス閲覧可（G2-U）。
      var llmUsageTabBtn = document.getElementById("tab-btn-llm-usage");
      if (llmUsageTabBtn) llmUsageTabBtn.style.display = "";

      // 利用者マニュアル KB（help_kb）draft/freeze 管理 — SYSTEM_ADMIN のみ（manual_help_kb_design.md Phase 3 ②）。
      var manualEditorTabBtn = document.getElementById("tab-btn-manual-editor");
      if (manualEditorTabBtn) manualEditorTabBtn.style.display = "";

      // discuss 観測基盤（Observation Layer）— SYSTEM_ADMIN のみ（discuss_observation_design.md §4）。
      var discussObservationTabBtn = document.getElementById("tab-btn-discuss-observation");
      if (discussObservationTabBtn) discussObservationTabBtn.style.display = "";

      // M層（LLM モデル選択, migration 061）— SYSTEM_ADMIN のみ運用タブでシステム既定を管理。
      var llmModelsTabBtn = document.getElementById("tab-btn-llm-models");
      if (llmModelsTabBtn) llmModelsTabBtn.style.display = "";
    }

    // Show schema evolution tab for TEACHER/SYSTEM_ADMIN
    if (state.role === "TEACHER" || state.role === "SYSTEM_ADMIN") {
      var schemaTab = document.createElement("button");
      schemaTab.className = "admin-tab";
      schemaTab.dataset.tab = "schema";
      schemaTab.setAttribute("role", "menuitem");
      schemaTab.textContent = "DSL進化分析";
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
          '<button id="schema-analyze-btn" data-ui-anchor="schema.analyze-btn" class="admin-action-btn">AIメタ分析を実行</button>' +
          '<button id="schema-refresh-btn" data-ui-anchor="schema.refresh-btn" class="admin-action-btn" style="background:var(--color-bg-tertiary);color:var(--color-text)">提案を更新</button>' +
        '</div>' +
        '<div id="schema-analyze-msg" class="upload-status" style="display:none"></div>' +
        '<h4 style="margin:16px 0 8px">提案一覧</h4>' +
        '<div id="schema-proposals-list" data-ui-anchor="schema.proposals-list" style="margin-bottom:24px">' +
          '<p style="color:var(--color-text-tertiary)">読み込み中...</p>' +
        '</div>' +
        '<h4 style="margin:16px 0 8px">再抽出ジョブ</h4>' +
        '<div id="schema-jobs-list" data-ui-anchor="schema.jobs-list">' +
          '<p style="color:var(--color-text-tertiary)">読み込み中...</p>' +
        '</div>' +
      '</div>' +
      '<div class="admin-section" style="margin-top:24px">' +
        '<h3 class="admin-section-title">現在のスキーマ定義</h3>' +
        '<div style="display:flex;gap:24px;flex-wrap:wrap">' +
          '<div style="flex:1;min-width:300px">' +
            '<h4>概念カテゴリ (OntologyType)</h4>' +
            '<div id="schema-types-list" data-ui-anchor="schema.types-list"><p style="color:var(--color-text-tertiary)">読み込み中...</p></div>' +
          '</div>' +
          '<div style="flex:1;min-width:300px">' +
            '<h4>関係性タイプ (CorePredicate)</h4>' +
            '<div id="schema-preds-list" data-ui-anchor="schema.preds-list"><p style="color:var(--color-text-tertiary)">読み込み中...</p></div>' +
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
        '<form id="student-form" data-ui-anchor="students.student-form" class="admin-user-form">' +
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
      '</div>' +
      alAccountSectionHtml("students");
    tabsEl.parentElement.appendChild(studentPanel);

    // Create teacher management panel (SYSTEM_ADMIN only)
    if (state.role === "SYSTEM_ADMIN") {
      var teacherPanel = document.createElement("div");
      teacherPanel.className = "admin-panel";
      teacherPanel.id = "tab-teachers";
      teacherPanel.innerHTML =
        '<div class="admin-section">' +
          '<h3 class="admin-section-title">教員アカウント追加</h3>' +
          '<form id="teacher-form" data-ui-anchor="teachers.teacher-form" class="admin-user-form">' +
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
        '</div>' +
        alAccountSectionHtml("teachers");
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
          container.innerHTML = '<p style="color:var(--color-text-tertiary)">提案はまだありません。「DSL進化分析」タブからAIメタ分析を実行してください。</p>';
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
            html += '<button class="admin-action-btn sp-simulate-btn" data-ui-anchor="schema-proposals.simulate-btn" data-id="' + escHtml(p.proposal_id) + '" style="font-size:0.85em;padding:4px 12px">シミュレーションを実行</button>';
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
      openDangerConfirmModal({
        title: "スキーマ提案をシステム全体へ適用",
        message: "スキーマ提案をシステム全体に適用し、全教材の再抽出ジョブを開始します。この操作は取り消せません。",
        confirmLabel: "適用する",
      }, function () {
        approveWithScope(_currentSimProposalId, "full", []);
      });
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
            html += '<button class="admin-action-btn schema-approve-btn" data-ui-anchor="schema.approve-btn" data-id="' + escHtml(p.proposal_id) + '" style="font-size:0.85em;padding:4px 12px">承認して適用</button>';
            html += '<button class="admin-action-btn schema-reject-btn" data-ui-anchor="schema.reject-btn" data-id="' + escHtml(p.proposal_id) + '" style="font-size:0.85em;padding:4px 12px;background:var(--color-bg-tertiary);color:var(--color-text)">却下</button>';
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
          '<div class="groups-item" data-ui-anchor="groups.list" data-gid="' + escHtml(g.id) + '" style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;cursor:pointer;background:' + headerBg + '">' +
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
        actions = '<button class="admin-action-btn groups-remove-btn" data-ui-anchor="groups.remove-btn" data-uid="' + escHtml(m.user_id) + '" style="font-size:11px">除名</button>';
      } else if (!isAdmin && m.user_id === _meUserId()) {
        actions = '<button class="admin-action-btn groups-leave-btn" data-ui-anchor="groups.leave-btn" style="font-size:11px">退会</button>';
      }
      return '<tr><td>' + escHtml(m.username) + '</td><td>' + escHtml(m.email || "") + '</td><td>' + escHtml(m.role) + '</td><td>' + actions + '</td></tr>';
    }).join("");

    var inviteCodeBlock = "";
    if (isAdmin && g.invite_code) {
      inviteCodeBlock =
        '<div style="margin:8px 0">' +
        '<strong>招待コード:</strong> <code style="font-size:13px;padding:2px 6px;background:var(--color-bg-tertiary);border-radius:3px">' + escHtml(g.invite_code) + '</code>' +
        ' <button id="groups-rotate-btn" data-ui-anchor="groups.rotate-btn" class="admin-action-btn" style="font-size:11px;margin-left:8px">再発行</button>' +
        "</div>";
    }

    var inviteByUser = "";
    if (isAdmin) {
      inviteByUser =
        '<div style="margin-top:16px;padding:12px;background:var(--color-bg-secondary);border-radius:4px">' +
        '<h4 style="font-size:13px;margin:0 0 8px 0">ユーザーを直接招待</h4>' +
        '<div style="display:flex;gap:8px">' +
        '<input type="text" id="groups-invite-username" placeholder="ユーザー名" style="flex:1;padding:4px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
        '<button id="groups-invite-btn" data-ui-anchor="groups.invite-btn" class="admin-action-btn">招待</button>' +
        "</div></div>";
    }

    var dangerZone = "";
    if (isAdmin) {
      dangerZone =
        '<div style="margin-top:24px">' +
        '<button id="groups-delete-btn" data-ui-anchor="groups.delete-btn" class="admin-action-btn" style="background:#dc2626;color:#fff">グループを削除</button>' +
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
        openDangerConfirmModal({
          title: "グループの削除",
          message: "グループ「" + g.name + "」を削除します。よろしいですか？",
          confirmLabel: "削除する",
        }, function () {
          deleteGroup(g.id);
        });
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
            '<button class="admin-action-btn groups-inv-accept" data-ui-anchor="groups.my-invitations" data-id="' + escHtml(inv.id) + '" style="font-size:11px;background:var(--color-text-success);color:#fff">承諾</button>' +
            '<button class="admin-action-btn groups-inv-decline" data-ui-anchor="groups.my-invitations" data-id="' + escHtml(inv.id) + '" style="font-size:11px">辞退</button>' +
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
  // Admin Copilot の道案内（course_row → course_visibility_control / publish_button）が
  // どのコース行を指しているかを覚えておくための最小限の状態（G1-6 のテーブル全体
  // フォールバックを解消するため。行単位の実コントロールを返せるようにする）。
  var _cmLastAnchoredCourseId = null;

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
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--color-text-tertiary)">読み込み中...</td></tr>';

    Promise.all([
      apiFetch("/admin/courses").then(function (res) { return res.json(); }),
      apiFetch("/groups").then(function (res) { return res.json(); }),
    ])
      .then(function (results) {
        _cmState.courses = results[0] || [];
        _cmState.groups = results[1] || [];
        // グループ権限マッピングをコース毎に取得
        if (!_cmState.courses.length) {
          tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--color-text-tertiary)">コースがありません</td></tr>';
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

  // G1-1/G5-2: 開示範囲バッジ・地図バインディング状態の表示テキスト（事実文のみ・煽り表示なし）。
  function courseVisibilityLabel(v) {
    if (v === "public") return "公開中";
    if (v === "group") return "グループ限定";
    return "非公開";
  }
  function courseVisibilityBadgeClass(v) {
    if (v === "public") return "status-visibility-public";
    if (v === "group") return "status-visibility-group";
    return "status-visibility-private";
  }
  function courseVisibilityBadgeHtml(c) {
    var v = c.visibility || "private";
    return '<span class="admin-status ' + courseVisibilityBadgeClass(v) + '">' + courseVisibilityLabel(v) + '</span>';
  }
  function courseAtlasBindingText(c) {
    if (c.atlas_cartridge_id) {
      return "地図: " + c.atlas_cartridge_id + "（" + (c.atlas_topic_count || 0) + "/" + (c.topic_count || 0) + " トピック）";
    }
    return "地図: 未設定";
  }

  function renderCourseManagementTable() {
    var tbody = document.getElementById("cm-tbody");
    if (!tbody) return;
    if (!_cmState.courses.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--color-text-tertiary)">コースがありません</td></tr>';
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
      var stateHtml;
      // C層(承認・共有レイヤー) — 説明バージョン・承認・引用の状況を見る共有ダッシュボード。
      // グループ共有（V層「版の管理」）とは別機能。閲覧可能な行なら owner/editor/viewer 問わず開ける
      // （sharing-dashboard API は _ensure_viewable のみを要求する）。
      var sharingDashboardBtn = '<button class="cm-sharing-dashboard-btn admin-action-btn" data-ui-anchor="course-management.sharing-dashboard-btn" data-course-id="' +
        escHtml(c.id) + '" data-course-title="' + escHtml(c.title) +
        '" style="font-size:11px;padding:2px 8px" title="説明バージョン・承認・引用の状況を表示します">共有ダッシュボード</button>';
      if (c.role === "owner") {
        // M層 Phase 3（§6.4）: 学習チャットのモデル設定（コース単位の上書き）。学生には出さない（M9）。
        var llmModelBtn = '<button class="cm-llm-model-btn admin-action-btn" data-ui-anchor="course-management.llm-model-btn" data-course-id="' + escHtml(c.id) +
          '" data-course-title="' + escHtml(c.title) + '" style="font-size:11px;padding:2px 8px" ' +
          'title="このコースの受講チャットが使うモデルを設定します">AIモデル</button>';
        // Phase 0b（discuss_opening_authoring_design.md §2 最下段）: discuss 開幕画面の
        // 「このコースで議論したいこと」。教員の任意入力のみ（AI 生成なし・空欄可）。
        var focusBtn = '<button class="cm-focus-btn admin-action-btn" data-ui-anchor="course-management.focus-btn" data-course-id="' + escHtml(c.id) +
          '" data-course-title="' + escHtml(c.title) + '" style="font-size:11px;padding:2px 8px" ' +
          'title="「論文と議論する」の開幕画面に出す、このコースで議論したいことを入力します">議論テーマ</button>';
        // リリース前の確認（release_review_flow_design.md §2）: AI が作った地図を提示し、
        // 「次へ」で承認、そのまま公開まで進めるウィザード。所有者のみ。
        var releaseReviewBtn = '<button class="cm-release-review-btn admin-action-btn" data-ui-anchor="course-management.release-review-btn" data-course-id="' + escHtml(c.id) +
          '" data-course-title="' + escHtml(c.title) + '" style="font-size:11px;padding:2px 8px" ' +
          'title="AIが作成した学習マップ・論文の位置づけを確認して公開まで進めます">確認して公開</button>';
        actionHtml = releaseReviewBtn + ' <button class="cm-manage-btn admin-action-btn" data-ui-anchor="course-management.manage-btn" data-course-id="' + escHtml(c.id) + '" data-course-title="' + escHtml(c.title) + '" title="開示範囲・グループ共有を設定します">共有設定</button>' +
          ' <button class="cm-version-btn admin-action-btn" data-ui-anchor="course-management.version-btn" data-course-id="' + escHtml(c.id) + '" data-course-title="' + escHtml(c.title) + '" title="コースを版（リリース）として発行・履歴管理します（共有設定とは別機能）">版の管理</button>' +
          ' ' + sharingDashboardBtn + ' ' + llmModelBtn + ' ' + focusBtn;
        var isPublic = c.visibility === "public";
        var quickToggle = '<button class="cm-quick-publish-btn admin-action-btn" data-ui-anchor="course-management.quick-publish-btn" data-course-id="' + escHtml(c.id) + '" data-action="' + (isPublic ? "unpublish" : "publish") + '" style="font-size:11px;padding:2px 8px;margin-top:4px">' +
          (isPublic ? "公開を止める" : "公開する") + '</button>';
        stateHtml = '<div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">' +
          courseVisibilityBadgeHtml(c) +
          '<span style="font-size:11px;color:var(--color-text-tertiary)">' + escHtml(courseAtlasBindingText(c)) + '</span>' +
          quickToggle +
          '</div>';
      } else {
        // N10残: 非所有者（editor/viewer）にも「版の管理」を表示する。開くと読み取り専用
        // モーダル（版履歴・現在版・自分のピン状態は見え、発行・削除予約は versioning.js が
        // version-state の is_owner/can_publish フラグで理由付き無効化する。fail-closed）。
        actionHtml = '<span style="font-size:11px;color:var(--color-text-tertiary)">所有者のみ変更可</span> ' +
          '<button class="cm-version-btn admin-action-btn" data-ui-anchor="course-management.version-btn" data-course-id="' + escHtml(c.id) + '" data-course-title="' + escHtml(c.title) + '" title="共有版の履歴と現在見ている版を確認します（発行・削除予約は所有者のみ）">版の管理</button> ' +
          sharingDashboardBtn;
        stateHtml = '<div style="display:flex;flex-direction:column;gap:3px;align-items:flex-start">' +
          courseVisibilityBadgeHtml(c) +
          '<span style="font-size:11px;color:var(--color-text-tertiary)">' + escHtml(courseAtlasBindingText(c)) + '</span>' +
          '</div>';
      }
      html += '<tr data-course-id="' + escHtml(c.id) + '">' +
        '<td>' + escHtml(c.title) + '</td>' +
        '<td>' + roleBadge + '</td>' +
        '<td>' + permsHtml + '</td>' +
        '<td>' + stateHtml + '</td>' +
        '<td>' + actionHtml + '</td>' +
        '</tr>';
    });
    tbody.innerHTML = html;
    tbody.querySelectorAll(".cm-manage-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openPermissionModal(this.getAttribute("data-course-id"), this.getAttribute("data-course-title"));
      });
    });
    // リリース前の確認ウィザード（release_review_flow_design.md §2 の入口2）
    tbody.querySelectorAll(".cm-release-review-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!window.AdminReleaseReview) return;
        window.AdminReleaseReview.open(
          this.getAttribute("data-course-id"),
          this.getAttribute("data-course-title"),
          {}
        );
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
    // C層(承認・共有レイヤー) — 共有ダッシュボード（G2-C）
    tbody.querySelectorAll(".cm-sharing-dashboard-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openSharingDashboardModal(this.getAttribute("data-course-id"), this.getAttribute("data-course-title"));
      });
    });
    // M層 Phase 3（§6.4）— 学習チャットのモデル設定
    tbody.querySelectorAll(".cm-llm-model-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openCourseModelModal(this.getAttribute("data-course-id"), this.getAttribute("data-course-title"));
      });
    });
    // Phase 0b — discuss 開幕画面の「このコースで議論したいこと」（教員の任意入力）
    tbody.querySelectorAll(".cm-focus-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openCourseFocusModal(this.getAttribute("data-course-id"), this.getAttribute("data-course-title"));
      });
    });
    // G1-1: 行内の「公開する/公開を止める」クイック操作（visibility=public ⇔ private の切替）。
    // 細かいグループ指定が必要な場合は「共有設定」モーダルの開示範囲セクションを使う。
    tbody.querySelectorAll(".cm-quick-publish-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var courseId = this.getAttribute("data-course-id");
        var action = this.getAttribute("data-action");
        var nextVisibility = action === "publish" ? "public" : "private";
        openDangerConfirmModal({
          title: action === "publish" ? "コースを公開" : "コースの公開を止める",
          message: action === "publish"
            ? "このコースを公開します。学生が受講できるようになります。"
            : "このコースの公開を止めます（非公開に切り替えます）。学生の新規受講はできなくなります。",
          confirmLabel: action === "publish" ? "公開する" : "公開を止める",
        }, function () {
          setCmStatus(action === "publish" ? "公開しています..." : "非公開にしています...", "info");
          apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/visibility", {
            method: "PUT",
            body: JSON.stringify({ visibility: nextVisibility }),
          }).then(function (res) {
            if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "更新に失敗しました"); });
            return res.json();
          }).then(function () {
            setCmStatus(action === "publish" ? "公開しました" : "非公開にしました", "success");
            loadCourseManagement();
            if (window.AdminNextSteps) window.AdminNextSteps.refresh();
          }).catch(function (e) {
            setCmStatus(e.message || "更新に失敗しました", "error");
          });
        });
      });
    });
  }

  // ── C層(承認・共有レイヤー) — 共有ダッシュボード（G2-C）────────────────────
  // GET /admin/courses/{course_id}/sharing-dashboard をそのまま提示する。
  // 承認は API が返す段階ラベル(endorsement_label)を使う（数値の捏造・独自スコア化はしない）。
  function openSharingDashboardModal(courseId, courseTitle) {
    var existing = document.getElementById("sharing-dashboard-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "sharing-dashboard-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:520px;max-width:760px;max-height:82vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">共有ダッシュボード — ' + escHtml(courseTitle || "") + '</h3>' +
          '<button id="sharing-dashboard-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          'このコースの理論コンポーネントに紐づく説明バージョンごとの、共有・承認・引用の状況です（個人ごとの追跡ではなく集計です）。' +
        '</p>' +
        '<div id="sharing-dashboard-modal-body" style="overflow-y:auto;flex:1">' +
          '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("sharing-dashboard-modal-close").addEventListener("click", function () { overlay.remove(); });

    apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/sharing-dashboard")
      .then(function (res) {
        if (!res.ok) throw new Error("status " + res.status);
        return res.json();
      })
      .then(renderSharingDashboardModal)
      .catch(function () {
        var body = document.getElementById("sharing-dashboard-modal-body");
        if (body) body.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">読み込みに失敗しました</div>';
      });
  }

  function renderSharingDashboardModal(data) {
    var body = document.getElementById("sharing-dashboard-modal-body");
    if (!body) return;
    var totals = data.totals || {};
    var explanations = data.explanations || [];
    var html = '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px;margin-bottom:14px;padding:10px;background:var(--color-background-tertiary);border-radius:6px">' +
      '<div>説明バージョン: <strong>' + escHtml(String(totals.explanation_count || 0)) + '</strong></div>' +
      '<div>共有中: <strong>' + escHtml(String(totals.shared_count || 0)) + '</strong></div>' +
      '<div>承認あり: <strong>' + escHtml(String(totals.endorsed_count || 0)) + '</strong></div>' +
      '<div>引用合計: <strong>' + escHtml(String(totals.citation_total || 0)) + '</strong></div>' +
      '</div>';

    if (!explanations.length) {
      html += '<div style="color:var(--color-text-tertiary);font-size:13px">このコースにはまだ説明バージョンがありません。</div>';
      body.innerHTML = html;
      return;
    }

    html += '<table class="admin-table"><thead><tr>' +
      '<th>説明</th><th>種別</th><th>作成者</th><th>共有</th><th>承認</th><th>引用</th>' +
      '</tr></thead><tbody>';
    explanations.forEach(function (e) {
      var kindLabel = e.kind === "standard" ? "標準（AI由来の説明）" : "教員独自の解釈";
      var sharedLabel = e.shared
        ? '<span class="admin-status status-visibility-public">共有中</span>'
        : '<span class="admin-status status-visibility-private">未共有</span>';
      html += "<tr>" +
        "<td>" + escHtml(e.title || "(無題)") + "</td>" +
        "<td>" + escHtml(kindLabel) + "</td>" +
        "<td>" + escHtml(e.author_name || "-") + "</td>" +
        "<td>" + sharedLabel + "</td>" +
        "<td>" + escHtml(e.endorsement_label || "未承認") + "</td>" +
        "<td>" + escHtml(String(e.citation_count || 0)) + "</td>" +
        "</tr>";
    });
    html += "</tbody></table>";
    body.innerHTML = html;
  }

  // ── M層 Phase 3（§6.4）— コース単位の学習チャットモデル設定 ─────────────────
  // 学生には出さない（M9）。保存先は learning_courses.data.llm_models.learning_chat
  // （PUT /api/learning/courses/{id} body.llm_models、core/course_data.py 正本）。
  // 現在の保存値は GET /api/admin/courses（loadCourseManagement で取得済みの
  // _cmState.courses[].llm_models）から読む — 未保存なら catalog の実効モデルに
  // 追従して表示する（捏造しない）。
  function openCourseModelModal(courseId, courseTitle) {
    var existing = document.getElementById("course-model-modal");
    if (existing) existing.remove();

    var course = null;
    for (var i = 0; i < _cmState.courses.length; i++) {
      if (_cmState.courses[i].id === courseId) { course = _cmState.courses[i]; break; }
    }
    var savedModels = (course && course.llm_models) || {};
    var currentOverride = savedModels.learning_chat || null;

    var overlay = document.createElement("div");
    overlay.id = "course-model-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:420px;max-width:520px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">AIモデル設定 — ' + escHtml(courseTitle || "") + '</h3>' +
          '<button id="course-model-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">受講者には表示されません。このコースの受講チャットが使うモデルの設定です。</p>' +
        '<div style="font-size:13px;margin-bottom:6px">学習チャットのモデル: <span id="course-model-chip-mount" style="display:inline-block;vertical-align:middle"></span></div>' +
        '<p id="course-model-current-note" style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          (currentOverride
            ? "現在このコースの設定として保存されています。"
            : "現在このコース独自の設定はありません（表示は既定値です）。") +
        '</p>' +
        '<div id="course-model-status" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:10px"></div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button id="course-model-cancel" class="admin-action-btn">閉じる</button>' +
          '<button id="course-model-save" data-ui-anchor="course-management.course-model-save" class="admin-action-btn">保存</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("course-model-modal-close").addEventListener("click", function () { overlay.remove(); });
    document.getElementById("course-model-cancel").addEventListener("click", function () { overlay.remove(); });

    // pendingModel: null は「未変更（保存済みの値のまま）」ではなく「既定に戻す」を意味する
    // ―― chip の初期状態は currentOverride で復元しているため、ユーザーが一度も触らなければ
    // save 時に currentOverride をそのまま送る。
    var touched = false;
    var pendingModel = currentOverride;
    var modelChip = null;
    var mount = document.getElementById("course-model-chip-mount");
    if (mount && window.AdminLlmModels) {
      modelChip = window.AdminLlmModels.createModelChip({
        sceneKey: "learning_chat",
        mountEl: mount,
        compact: true,
        initialModel: currentOverride,
        onChange: function (model) {
          touched = true;
          pendingModel = model;
        }
      });
    }

    document.getElementById("course-model-save").addEventListener("click", function () {
      var statusEl = document.getElementById("course-model-status");
      var toSave = touched ? pendingModel : currentOverride;
      var fallbackLabel = modelChip ? modelChip.getModel() : "既定";
      var message = toSave
        ? "このコースの受講チャットは " + toSave + " で応答するようになります。"
        : "このコースの受講チャットの設定を解除します（既定 " + (fallbackLabel || "既定") + " に戻ります）。";
      if (!window.confirm(message)) return;
      statusEl.textContent = "保存しています...";
      apiFetch("/learning/courses/" + encodeURIComponent(courseId), {
        method: "PUT",
        body: JSON.stringify({ llm_models: { learning_chat: toSave || null } }),
      })
        .then(function (res) {
          if (!res.ok) {
            return res.json().then(function (d) { throw new Error((d && d.detail) || "保存に失敗しました"); });
          }
          return res.json();
        })
        .then(function () {
          statusEl.textContent = "保存しました。";
          if (course) {
            course.llm_models = course.llm_models || {};
            if (toSave) course.llm_models.learning_chat = toSave;
            else delete course.llm_models.learning_chat;
          }
          setTimeout(function () { overlay.remove(); }, 500);
        })
        .catch(function (err) {
          statusEl.textContent = (err && err.message) || "保存に失敗しました";
        });
    });
  }

  // ── Phase 0b — discuss 開幕画面「このコースで議論したいこと」 ─────────────────
  // discuss_opening_authoring_design.md §2 最下段 / §10 Phase 0b。教員の任意入力だけで
  // 成立させる（AI 生成・候補提示は一切しない）。保存先は learning_courses.data.course_focus
  // （PUT /api/learning/courses/{id} body.course_focus、正本アクセサは core/course_data.py::
  // course_focus）。現在値は GET /api/admin/courses（_cmState.courses[].course_focus）から
  // 読む。空欄で保存すると設定解除（開幕画面から区画ごと消える）。
  var COURSE_FOCUS_MAX_CHARS = 600;

  function openCourseFocusModal(courseId, courseTitle) {
    var existing = document.getElementById("course-focus-modal");
    if (existing) existing.remove();

    var course = null;
    for (var i = 0; i < _cmState.courses.length; i++) {
      if (_cmState.courses[i].id === courseId) { course = _cmState.courses[i]; break; }
    }
    var currentFocus = (course && course.course_focus) || "";

    var overlay = document.createElement("div");
    overlay.id = "course-focus-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      // モーダル全体に「議論テーマ」の節を係留する（入力欄にホバーしても説明が出る。
      // 「保存」ボタンだけは自分の節を持つので closest() でそちらが勝つ）。
      '<div data-ui-anchor="course-management.focus-btn" style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:460px;max-width:560px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">議論テーマ — ' + escHtml(courseTitle || "") + '</h3>' +
          '<button id="course-focus-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          '「論文と議論する」の開幕画面の先頭に、担当教員が書いたものとして表示されます。' +
          'AI は生成しません。空欄のままでも構いません（その場合は区画ごと表示されません）。</p>' +
        '<textarea id="course-focus-input" rows="4" maxlength="' + COURSE_FOCUS_MAX_CHARS + '" ' +
          'placeholder="例: この論文の感度の限界がどこから来るのかを、前提に戻って議論してください。" ' +
          'style="width:100%;box-sizing:border-box;font-family:inherit;font-size:13px;line-height:1.7;padding:8px;' +
          'border:1px solid var(--color-border-secondary);border-radius:6px;background:var(--color-background-secondary);' +
          'color:var(--color-text-primary)"></textarea>' +
        '<p style="font-size:11px;color:var(--color-text-tertiary);margin:6px 0 10px">' + COURSE_FOCUS_MAX_CHARS + '文字以内。</p>' +
        '<div id="course-focus-status" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:10px"></div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
          '<button id="course-focus-cancel" class="admin-action-btn">閉じる</button>' +
          '<button id="course-focus-save" class="admin-action-btn" data-ui-anchor="course-management.course-focus-save">保存</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("course-focus-modal-close").addEventListener("click", function () { overlay.remove(); });
    document.getElementById("course-focus-cancel").addEventListener("click", function () { overlay.remove(); });

    var input = document.getElementById("course-focus-input");
    if (input) { input.value = currentFocus; input.focus(); }

    document.getElementById("course-focus-save").addEventListener("click", function () {
      var statusEl = document.getElementById("course-focus-status");
      var text = input ? (input.value || "").trim() : "";
      if (text.length > COURSE_FOCUS_MAX_CHARS) {
        statusEl.textContent = COURSE_FOCUS_MAX_CHARS + "文字以内で入力してください。";
        return;
      }
      // 事実文の確認（削除・公開ではないので破壊的確認ゲートは使わない。ただし
      // 空欄保存＝学習者の画面から区画が消えることは明示する）。
      if (!text && currentFocus) {
        if (!window.confirm("入力を空にして保存すると、開幕画面からこの区画が表示されなくなります。")) return;
      }
      statusEl.textContent = "保存しています...";
      apiFetch("/learning/courses/" + encodeURIComponent(courseId), {
        method: "PUT",
        body: JSON.stringify({ course_focus: text }),
      })
        .then(function (res) {
          if (!res.ok) {
            return res.json().then(function (d) { throw new Error((d && d.detail) || "保存に失敗しました"); });
          }
          return res.json();
        })
        .then(function () {
          statusEl.textContent = "保存しました。";
          if (course) course.course_focus = text;
          setTimeout(function () { overlay.remove(); }, 500);
        })
        .catch(function (err) {
          statusEl.textContent = (err && err.message) || "保存に失敗しました";
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
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">コースの共有・開示範囲</h3>' +
          '<button id="cm-perm-close-btn" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 12px">コース:「' + escHtml(courseTitle) + '」の設定です。</p>' +
        '<h4 style="font-size:13px;margin:0 0 8px 0;color:var(--color-text-secondary)">開示範囲</h4>' +
        '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px">誰がこのコースを見て受講できるかを設定します。</p>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap">' +
          '<select id="cm-visibility-select" style="padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)">' +
            '<option value="private">非公開（自分のみ）</option>' +
            '<option value="group">グループ限定</option>' +
            '<option value="public">公開（誰でも受講可）</option>' +
          '</select>' +
          '<select id="cm-visibility-group-select" style="display:none;padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-bg-secondary);color:var(--color-text-primary)"></select>' +
          '<button id="cm-visibility-apply-btn" data-ui-anchor="course-management.visibility-apply" class="admin-action-btn">適用</button>' +
        '</div>' +
        '<div id="cm-visibility-status" class="upload-status" style="display:none;margin-bottom:12px"></div>' +
        '<hr style="border:none;border-top:1px solid var(--color-border);margin:4px 0 12px 0">' +
        '<h4 style="font-size:13px;margin:0 0 8px 0;color:var(--color-text-secondary)">解析成果・受講のグループ共有</h4>' +
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
          '<button id="cm-perm-add-btn" data-ui-anchor="course-management.perm-add" class="admin-action-btn" style="background:var(--color-text-success);color:#fff">追加</button>' +
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

    initCourseVisibilitySection();
    loadPermissionModal();
  }

  // G1-1: 「共有設定」モーダルに統合した開示範囲セクション（public/group/private）。
  function initCourseVisibilitySection() {
    var course = null;
    for (var i = 0; i < _cmState.courses.length; i++) {
      if (_cmState.courses[i].id === _cmState.currentCourseId) { course = _cmState.courses[i]; break; }
    }
    var visSelect = document.getElementById("cm-visibility-select");
    var groupSelect = document.getElementById("cm-visibility-group-select");
    if (!visSelect || !groupSelect) return;

    visSelect.value = (course && course.visibility) || "private";
    var groupHtml = '<option value="">グループを選択...</option>';
    _cmState.groups.forEach(function (g) {
      groupHtml += '<option value="' + escHtml(g.id) + '">' + escHtml(g.name) + '</option>';
    });
    groupSelect.innerHTML = groupHtml;
    if (course && course.visibility === "group" && course.group_id) groupSelect.value = course.group_id;
    toggleCmVisibilityGroupSelect();

    visSelect.addEventListener("change", toggleCmVisibilityGroupSelect);
    document.getElementById("cm-visibility-apply-btn").addEventListener("click", applyCourseVisibility);
  }

  function toggleCmVisibilityGroupSelect() {
    var visSelect = document.getElementById("cm-visibility-select");
    var groupSelect = document.getElementById("cm-visibility-group-select");
    if (!visSelect || !groupSelect) return;
    groupSelect.style.display = visSelect.value === "group" ? "" : "none";
  }

  function setCmVisibilityStatus(msg, kind) {
    var el = document.getElementById("cm-visibility-status");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (kind || "info");
  }

  function applyCourseVisibility() {
    var visSelect = document.getElementById("cm-visibility-select");
    var groupSelect = document.getElementById("cm-visibility-group-select");
    if (!visSelect) return;
    var visibility = visSelect.value;
    var groupId = groupSelect ? groupSelect.value : "";
    if (visibility === "group" && !groupId) {
      setCmVisibilityStatus("グループを選択してください", "error");
      return;
    }
    var courseId = _cmState.currentCourseId;

    function doApply() {
      setCmVisibilityStatus("更新しています...", "info");
      apiFetch("/admin/courses/" + encodeURIComponent(courseId) + "/visibility", {
        method: "PUT",
        body: JSON.stringify({ visibility: visibility, group_id: visibility === "group" ? groupId : null }),
      }).then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "更新に失敗しました"); });
        return res.json();
      }).then(function () {
        setCmVisibilityStatus("更新しました", "success");
        loadCourseManagement();
        if (window.AdminNextSteps) window.AdminNextSteps.refresh();
      }).catch(function (e) {
        setCmVisibilityStatus(e.message || "更新に失敗しました", "error");
      });
    }

    if (visibility === "public") {
      openDangerConfirmModal({
        title: "コースを公開",
        message: "このコースを公開します。学生が受講できるようになります。",
        confirmLabel: "公開する",
      }, doApply);
    } else {
      doApply();
    }
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
  // 教材管理タブの「共有設定」ボタンから開く。コース共有モーダルと同型。
  // G1-1: 開示範囲（public/group/private）セクションも同じモーダルに統合する
  // （モーダル乱立を避けるため、別モーダルを新設しない）。
  var _docShareState = { documentId: null, materialId: null, title: "", visibility: "private", groupId: "", perms: [], groups: [] };
  // Admin Copilot の道案内（material_row → material_visibility_control）が
  // どの教材行を指しているかを覚えておくための最小限の状態（course 側の
  // _cmLastAnchoredCourseId と同型）。
  var _matLastAnchoredMaterialId = null;

  // §2.3 の2層化で行操作の多くが「⋯」メニュー内へ移ったため、道案内（locate）の
  // 点灯先を解決するときは、対象項目を含むメニューを開いてから返す（P8: 誘導まで。
  // 値入力・実行は本人）。行 id 指定があればその行、無ければ先頭行にフォールバック。
  function _matRowActionAnchor(id, selector) {
    if (id) _matLastAnchoredMaterialId = id;
    var mid = id || _matLastAnchoredMaterialId;
    var el = (mid && document.querySelector('#materials-tbody tr[data-material-id="' + mid + '"] ' + selector))
      || document.querySelector('#materials-tbody ' + selector);
    if (el && el.closest) {
      var panel = el.closest(".ls-menu");
      if (panel && panel.hidden) panel.hidden = false;
    }
    return el;
  }

  function openDocumentShareModal(documentId, title, materialId, visibility, groupId) {
    _docShareState.documentId = documentId;
    _docShareState.materialId = materialId || "";
    _docShareState.title = title || "";
    _docShareState.visibility = visibility || "private";
    _docShareState.groupId = groupId || "";

    var existing = document.getElementById("doc-share-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "doc-share-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:24px;min-width:480px;max-width:640px;max-height:80vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">教材の共有・開示範囲</h3>' +
          '<button id="doc-share-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 12px">教材「' + escHtml(title || "") + '」の設定です。</p>' +
        '<h4 style="font-size:13px;margin:0 0 8px 0;color:var(--color-text-secondary)">開示範囲</h4>' +
        '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px">誰がこの教材（と解析成果）を見られるかを設定します。</p>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap">' +
          '<select id="doc-visibility-select" style="padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary)">' +
            '<option value="private">非公開（自分のみ）</option>' +
            '<option value="group">グループ限定</option>' +
            '<option value="public">公開（誰でも閲覧可）</option>' +
          '</select>' +
          '<select id="doc-visibility-group-select" style="display:none;padding:6px 8px;font-size:13px;border:1px solid var(--color-border);border-radius:4px;background:var(--color-background-secondary);color:var(--color-text-primary)"></select>' +
          '<button id="doc-visibility-apply-btn" class="admin-action-btn">適用</button>' +
        '</div>' +
        '<div id="doc-visibility-status" class="upload-status" style="display:none;margin-bottom:12px"></div>' +
        '<hr style="border:none;border-top:1px solid var(--color-border);margin:4px 0 12px 0">' +
        '<h4 style="font-size:13px;margin:0 0 8px 0;color:var(--color-text-secondary)">解析成果のグループ共有</h4>' +
        '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px">解析成果（理論コンポーネント・グラフ・Claim）を指定グループへ個別に共有します。viewer=閲覧・引用／editor=編集・再解析。</p>' +
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

    var visSelect = document.getElementById("doc-visibility-select");
    visSelect.value = _docShareState.visibility;
    toggleDocVisibilityGroupSelect();
    visSelect.addEventListener("change", toggleDocVisibilityGroupSelect);
    document.getElementById("doc-visibility-apply-btn").addEventListener("click", applyMaterialVisibility);

    loadDocumentShareModal();
  }

  function toggleDocVisibilityGroupSelect() {
    var visSelect = document.getElementById("doc-visibility-select");
    var groupSelect = document.getElementById("doc-visibility-group-select");
    if (!visSelect || !groupSelect) return;
    groupSelect.style.display = visSelect.value === "group" ? "" : "none";
  }

  function setDocVisibilityStatus(msg, kind) {
    var el = document.getElementById("doc-visibility-status");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.style.display = "block";
    el.textContent = msg;
    el.className = "upload-status upload-status-" + (kind || "info");
  }

  function renderDocVisibilityGroupOptions() {
    var sel = document.getElementById("doc-visibility-group-select");
    if (!sel) return;
    var html = '<option value="">グループを選択...</option>';
    _docShareState.groups.forEach(function (g) {
      html += '<option value="' + escHtml(g.id) + '">' + escHtml(g.name) + '</option>';
    });
    sel.innerHTML = html;
    if (_docShareState.visibility === "group" && _docShareState.groupId) sel.value = _docShareState.groupId;
  }

  function applyMaterialVisibility() {
    var visSelect = document.getElementById("doc-visibility-select");
    var groupSelect = document.getElementById("doc-visibility-group-select");
    if (!visSelect) return;
    var visibility = visSelect.value;
    var groupId = groupSelect ? groupSelect.value : "";
    if (visibility === "group" && !groupId) {
      setDocVisibilityStatus("グループを選択してください", "error");
      return;
    }
    if (!_docShareState.materialId) {
      setDocVisibilityStatus("この教材の識別子が取得できませんでした", "error");
      return;
    }

    function doApply() {
      setDocVisibilityStatus("更新しています...", "info");
      apiFetch("/admin/materials/" + encodeURIComponent(_docShareState.materialId) + "/visibility", {
        method: "PUT",
        body: JSON.stringify({ visibility: visibility, group_id: visibility === "group" ? groupId : null }),
      }).then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || "更新に失敗しました"); });
        return res.json();
      }).then(function () {
        _docShareState.visibility = visibility;
        _docShareState.groupId = visibility === "group" ? groupId : "";
        setDocVisibilityStatus("更新しました", "success");
        loadMaterials();
      }).catch(function (e) { setDocVisibilityStatus(e.message || "更新に失敗しました", "error"); });
    }

    if (visibility === "public") {
      openDangerConfirmModal({
        title: "教材を公開",
        message: "この教材（と解析成果）を公開します。誰でも閲覧できるようになります。",
        confirmLabel: "公開する",
      }, doApply);
    } else {
      doApply();
    }
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
      renderDocVisibilityGroupOptions();
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
    var anchor = kind === "script" ? "system-stats.generate-script"
      : kind === "audio" ? "system-stats.generate-audio"
      : "system-stats.run-analysis";
    return '<button class="admin-action-btn ss-generate-btn" data-ui-anchor="' + anchor + '" data-kind="' + escHtml(kind) +
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

    // M層（LLM モデル選択, migration 061）— 依存注入は initMaterialsPanel() より前に
    // 行うこと。モジュール内の fetch 実体は deps.apiFetch であり、注入前に叩くと
    // 教材管理の初期化ごと落ちる（解析モデル/教材一覧が「読み込み中」のまま止まる）。
    if (window.AdminLlmModels) {
      window.AdminLlmModels.init({
        apiFetch: apiFetch,
        escHtml: escHtml,
        onTabActivate: onTabActivate,
        state: state,
        activateTabView: activateTabView,
      });
      // URL取得の許可ドメイン（SYSTEM_ADMIN 限定の運用タブに相乗り）。AdminLlmModels.init が
      // 登録した initOpsTab の直後に走らせる（コールバックは登録順に実行される）ため、
      // buildOpsTab() の innerHTML 書き込みに消されない。
      onTabActivate("llm-models", ensureUrlFetchDomainsSection);
    }

    if (state.role !== "SYSTEM_ADMIN") {
      initUpload();
      initUrlUpload();
      // M層 — 教材アップロード区画の解析モデル1行サマリ（init 済みが前提）。
      if (window.AdminLlmModels) window.AdminLlmModels.initMaterialsPanel();
      initCourseBuilder();
      initCourseManagement();
      // リリース前の確認ウィザード（release_review_flow_design.md §3.2）— DI 注入して
      // 疎結合に起動する。ステップ1は既存の atlas-binding UI をそのまま埋め込む。
      if (window.AdminReleaseReview) {
        window.AdminReleaseReview.init({
          apiFetch: apiFetch,
          escHtml: escHtml,
          atlasBindingPropose: atlasBindingPropose,
          refreshNextSteps: function () {
            if (window.AdminNextSteps) window.AdminNextSteps.refresh();
          },
          onPublished: function () {
            loadCourseManagement();
          },
        });
      }
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
        window.Deliberation.init({ apiFetch: apiFetch, apiFetchRaw: apiFetchRaw, escHtml: escHtml });
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

    // U層（LLM トークン使用量推計, migration 043）— SYSTEM_ADMIN メトリクスタブ +
    // TEACHER 向け教材見積りポップオーバー。DI 注入して疎結合に起動する（G2-U）。
    if (window.AdminLlmUsage) {
      window.AdminLlmUsage.init({
        apiFetch: apiFetch,
        escHtml: escHtml,
        onTabActivate: onTabActivate,
        state: state,
      });
    }

    // 利用者マニュアル KB（help_kb, migration 058/059）— draft 編集 + 凍結配信の管理 UI。
    // DI 注入して疎結合に起動する（admin-llm-usage.js と同型）。
    if (window.ManualKbEditor) {
      window.ManualKbEditor.init({
        apiFetch: apiFetch,
        escHtml: escHtml,
        onTabActivate: onTabActivate,
        state: state,
      });
    }

    // discuss 観測基盤（Observation Layer, docs/features/discuss_observation_design.md §4）—
    // Phase 3 着手判断のための観測状況ダッシュボード + ダンプ取得。DI 注入して疎結合に起動する
    // （admin-manual-editor.js と同型）。
    if (window.AdminDiscussObservation) {
      window.AdminDiscussObservation.init({
        apiFetch: apiFetch,
        escHtml: escHtml,
        onTabActivate: onTabActivate,
        state: state,
      });
    }

    // M層の AdminLlmModels.init はこの関数の先頭側（initUpload より前）で実施済み —
    // initMaterialsPanel が deps.apiFetch を必要とするため、ここには置かない。

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

    // admin「？使い方」インスペクト・モード（学習画面のインスペクトと同型）。
    // [data-ui-anchor] 要素へのホバーでマニュアル節ツールチップを表示する。
    if (window.AdminHelpInspect) {
      window.AdminHelpInspect.init({ apiFetch: apiFetch });
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
    // 呼ばれた場合はその行を返し（同時に「直近に選ばれた行」として記憶する）、param が
    // 無ければ従来どおり tbody 全体にフォールバックする。
    // G1-1/G1-6: material_visibility_control / course_visibility_control / publish_button は
    // かつてテーブル全体（tbody・table 要素）を返すだけで実コントロールを指していなかった。
    // 開示範囲セクション・クイック公開トグルを実装したので、開いているモーダルの実コントロール
    // → 直近に選んだ行のクイックボタン、の順に解決する（見つからなければ null=P8 fail-closed。
    // 「この先はこの画面での操作のあとに案内します」に自然に縮退する）。
    AA.registerUiAnchors("materials", {
      upload_dropzone: function () { return document.getElementById("upload-zone"); },
      material_row: function (id) {
        if (id) _matLastAnchoredMaterialId = id;
        return id
          ? document.querySelector('#materials-tbody tr[data-material-id="' + id + '"]') || document.getElementById("materials-tbody")
          : document.getElementById("materials-tbody");
      },
      material_visibility_control: function () { return document.getElementById("doc-visibility-select"); },
      // U層（G2-U）: 教材行の解析コスト見積り導線。行 id 指定があればその行、無ければ先頭行。
      material_estimate_button: function (id) {
        return _matRowActionAnchor(id, ".admin-estimate-btn");
      },
      // V層（G6）: 教材行の「版の管理」ボタン（共有版の発行・削除予約）。
      shared_version_button: function (id) {
        return _matRowActionAnchor(id, ".admin-version-doc-btn");
      },
      // 図分類レビュー（#496 / N13）: 教材行の「図・画像」ボタン（図モーダルを開く）。
      material_figures_button: function (id) {
        return _matRowActionAnchor(id, ".admin-figures-btn");
      },
      // W層/開幕素材レビュー: 教材行の「検出要素」ボタン（要素インベントリを開く）。
      material_inventory_button: function (id) {
        return _matRowActionAnchor(id, ".admin-inventory-btn");
      },
      // ゼミ前ブリーフ: 教材行の「ゼミ前ブリーフ…」ボタン（⋯メニュー内）。
      seminar_brief_button: function (id) {
        return _matRowActionAnchor(id, ".admin-seminar-brief-btn");
      },
      // 開幕素材レビュー（discuss の「議論のきっかけ」）: 要素インベントリ内の
      // 「説明レビュー」ボタン。インベントリを開くまでは DOM に存在しないので、
      // 未解決なら道案内はそこで止まる（P8 fail-closed）。
      explanation_review_button: function () {
        return document.getElementById("deliberation-explanation-review-open");
      }
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
      ls_audio_generate: function () { return document.getElementById("ls-audio-all-btn"); },
      // R層（G6）: 再構成レビューキュー直行ボタン（未レビュー件数バッジ付き）。
      recon_review_button: function () { return document.querySelector("[data-ls-recon-review]"); },
      // W層（G6）: 要素検討（深く検討）モーダルを開くボタン。claim/component/figure/equation の
      // いずれかで最初に見つかったもの（画面上に複数あり得るため代表 1 件でよい）。
      deliberation_open_button: function () {
        return document.querySelector(".ls-claim-deliberate-btn")
          || document.querySelector('[data-theory-action="deliberate"]')
          || document.querySelector(".figure-deliberate-btn")
          || document.querySelector(".eg-rev-deliberate-btn");
      },
      // W層（G6）: 深く検討モーダル内の同一性リンク確定/却下ボタン（候補があるときのみ出現）。
      identity_link_confirm_button: function () { return document.querySelector("[data-identity-action]"); }
    });
    AA.registerUiAnchors("course-management", {
      course_list: function () { return document.getElementById("cm-table"); },
      course_row: function (id) {
        if (id) _cmLastAnchoredCourseId = id;
        return id
          ? document.querySelector('#cm-tbody tr[data-course-id="' + id + '"]') || document.getElementById("cm-tbody")
          : document.getElementById("cm-tbody");
      },
      course_visibility_control: function () {
        var modalSelect = document.getElementById("cm-visibility-select");
        if (modalSelect) return modalSelect;
        return _cmLastAnchoredCourseId
          ? document.querySelector('#cm-tbody tr[data-course-id="' + _cmLastAnchoredCourseId + '"] .cm-quick-publish-btn')
          : null;
      },
      publish_button: function () {
        var applyBtn = document.getElementById("cm-visibility-apply-btn");
        if (applyBtn) return applyBtn;
        return _cmLastAnchoredCourseId
          ? document.querySelector('#cm-tbody tr[data-course-id="' + _cmLastAnchoredCourseId + '"] .cm-quick-publish-btn')
          : null;
      },
      atlas_binding_button: function () { return document.getElementById("cm-atlas-binding-open"); },
      // C層（G2-C）: コース行の共有ダッシュボードボタン。行 id 指定があればその行、無ければ先頭行。
      sharing_dashboard_button: function (id) {
        if (id) _cmLastAnchoredCourseId = id;
        var cid = id || _cmLastAnchoredCourseId;
        return (cid && document.querySelector('#cm-tbody tr[data-course-id="' + cid + '"] .cm-sharing-dashboard-btn'))
          || document.querySelector('#cm-tbody .cm-sharing-dashboard-btn');
      },
      // V層（G6）: コース行の「版の管理」ボタン（共有版の発行・削除予約）。
      shared_version_button: function (id) {
        if (id) _cmLastAnchoredCourseId = id;
        var cid = id || _cmLastAnchoredCourseId;
        return (cid && document.querySelector('#cm-tbody tr[data-course-id="' + cid + '"] .cm-version-btn'))
          || document.querySelector('#cm-tbody .cm-version-btn');
      }
    });
    AA.registerUiAnchors("atlas", {
      atlas_generate_button: function () { return document.getElementById("atlas-generate"); }
    });
    // 2026-07-29 是正: 学生・教員アカウント作成フォームは #tab-groups ではなく
    // 独立タブ #tab-students / #tab-teachers 側にある（setupRoleBasedUI が動的生成）。
    // 従来ここで "groups" screen に登録していたため道案内が実在しないフォームを指していた。
    AA.registerUiAnchors("groups", {
      groups_create_form: function () { return document.getElementById("groups-create-form"); }
    });
    // アカウントライフサイクル（account_lifecycle_management_design.md §9）: 一覧と行操作。
    // 行ボタンは一覧を描画するまで DOM に無いので、未解決なら道案内はそこで止まる
    // （P8 fail-closed。学生タブの reset/activity/delete は SYSTEM_ADMIN のときだけ描画される）。
    AA.registerUiAnchors("students", {
      create_student_form: function () { return document.getElementById("student-form"); },
      user_list: function () { return document.getElementById("al-students-list"); },
      user_suspend_button: function () { return _alRowActionAnchor("students", "suspend"); },
      user_restore_button: function () { return _alRowActionAnchor("students", "restore"); },
      user_reset_button: function () { return _alRowActionAnchor("students", "reset"); },
      user_activity_button: function () { return _alRowActionAnchor("students", "activity"); },
      user_delete_button: function () { return _alRowActionAnchor("students", "deletion"); }
    });
    AA.registerUiAnchors("teachers", {
      create_teacher_form: function () { return document.getElementById("teacher-form"); },
      user_list: function () { return document.getElementById("al-teachers-list"); },
      user_suspend_button: function () { return _alRowActionAnchor("teachers", "suspend"); },
      user_restore_button: function () { return _alRowActionAnchor("teachers", "restore"); },
      user_reset_button: function () { return _alRowActionAnchor("teachers", "reset"); },
      user_activity_button: function () { return _alRowActionAnchor("teachers", "activity"); },
      user_delete_button: function () { return _alRowActionAnchor("teachers", "deletion"); },
      user_transfer_button: function () { return _alRowActionAnchor("teachers", "transfer"); }
    });
    // 知識ネットワークビジョン Phase B（G2-B）: 橋の候補セクション。
    AA.registerUiAnchors("interest-dashboard", {
      interest_dashboard_course_select: function () { return document.getElementById("interest-dashboard-course-select"); },
      bridge_insights_section: function () { return document.getElementById("bridge-insights-section"); }
    });
    // U層（G2-U）: LLM使用量タブの主要コンテナ（SYSTEM_ADMIN 向けメトリクス）。
    AA.registerUiAnchors("llm-usage", {
      llm_usage_metrics: function () { return document.getElementById("llm-usage-metrics-body") || document.getElementById("tab-llm-usage"); }
    });
    // D層（G6）: 前提の地図タブの検証状態記帳フォーム・疑義の取り下げ/検証提案化ボタン。
    // ノード詳細ペインに動的に描画されるため data 属性で解決する（doubt-atlas.js は非改変）。
    AA.registerUiAnchors("doubt-atlas", {
      doubt_verification_form_button: function () { return document.querySelector("[data-doubt-vstatus-form]"); },
      doubt_challenge_withdraw_button: function () { return document.querySelector("[data-doubt-challenge-withdraw]"); },
      doubt_challenge_proposal_button: function () { return document.querySelector("[data-doubt-challenge-proposal]"); }
    });
    // L層（G6）: ナレッジライブラリタブの分野・エントリ一覧、凍結ボタン。
    AA.registerUiAnchors("knowledge-library", {
      library_domain_list: function () { return document.getElementById("library-domains-list"); },
      library_entry_freeze_button: function () { return document.getElementById("lib-detail-freeze"); }
    });
    // N31: つまづきデータタブ（コース選択 → 未回答クエリ一覧）。
    AA.registerUiAnchors("stumbles", {
      stumbles_course_select: function () { return document.getElementById("stumbles-course-select"); },
      stumbles_table: function () { return document.getElementById("stumbles-table"); }
    });
    // N31: スキーマ提案タブ（提案一覧。承認はシミュレーション結果画面の確認ゲート経由）。
    AA.registerUiAnchors("schema-proposals", {
      sp_proposals_list: function () { return document.getElementById("sp-proposals-list"); }
    });
    // 2026-07-29 是正: DSL進化分析タブ（このタブ自身の承認・却下には確認ダイアログが無い）。
    AA.registerUiAnchors("schema", {
      schema_analyze_button: function () { return document.getElementById("schema-analyze-btn"); }
    });
    // 2026-07-29 是正: マニュアル編集タブ（help_kb draft/freeze、SYSTEM_ADMIN のみ）。
    AA.registerUiAnchors("manual-editor", {
      manual_editor_panel: function () { return document.getElementById("mke-draft-list") || document.getElementById("tab-manual-editor"); }
    });
    // 2026-07-29 是正: discuss 観測タブ（Observation Layer、SYSTEM_ADMIN のみ）。
    AA.registerUiAnchors("discuss-observation", {
      discuss_observation_panel: function () { return document.getElementById("ado-body") || document.getElementById("tab-discuss-observation"); }
    });
    // 2026-07-29 是正: AIモデルタブ（M層、システム既定の変更、SYSTEM_ADMIN のみ）。
    AA.registerUiAnchors("llm-models", {
      llm_models_ops_table: function () { return document.getElementById("llm-models-ops-table") || document.getElementById("tab-llm-models"); }
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
      overlay.setAttribute("data-ui-anchor", "materials.revision-modal");
      overlay.innerHTML =
        '<div class="eg-rev-box">' +
          '<div class="eg-rev-header">' +
            '<h3>反復改善パイプライン</h3>' +
            '<button id="eg-rev-close" class="eg-rev-close" type="button">×</button>' +
          '</div>' +
          '<div class="eg-rev-active" id="eg-rev-active">読み込み中…</div>' +
          '<div class="eg-rev-toolbar">' +
            '<button id="eg-rev-start" data-ui-anchor="materials.revision-start" class="admin-action-btn" type="button">改善処理を開始</button>' +
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
      html += '<div class="eg-rev-actions" data-ui-anchor="materials.revision-decision">' +
        '<button id="eg-rev-run" data-ui-anchor="materials.revision-decision" class="admin-action-btn" type="button">監査＋候補生成</button>' +
        '<button id="eg-rev-accept" data-ui-anchor="materials.revision-decision" class="admin-action-btn"' + (detail.has_report ? '' : ' disabled') + ' type="button">採用</button>' +
        '<button id="eg-rev-reject" data-ui-anchor="materials.revision-decision" class="admin-action-btn" type="button">却下</button>' +
        '<button id="eg-rev-revise" data-ui-anchor="materials.revision-decision" class="admin-action-btn" type="button">再修正</button>' +
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
