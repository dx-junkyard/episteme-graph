/*
 * Lecture Script Studio — 原稿スタジオ（Issue #70）。
 * admin.js（13,066行）から分離（アーキテクチャ整理 Tier 3-17b）。
 *
 * ES5 / IIFE。window.LectureStudio を公開。admin.js の initApp() から
 * LectureStudio.init({apiFetch, apiFetchRaw, escHtml, onTabActivate, state}) を呼んで起動する。
 * 内部ロジックは admin.js からの逐語移設（関数名・DOM ID・挙動は変更していない）。
 *
 * 依存の注入パターンは admin-assistant.js と同型（deps オブジェクト + window フォールバック）。
 * admin.js 側の呼び出し箇所:
 *   - initApp(): window.LectureStudio.init({...})
 *   - 教材管理タブのエクスポートボタン: window.LectureStudio.openExportModal(state.exportContext)
 *   - registerAssistantHooks(): window.LectureStudio.getScreenContext()（Admin Copilot 画面コンテキスト）
 */
(function () {
  "use strict";

  // 注入される依存（疎結合）。未注入なら window グローバルへフォールバック
  // （admin-assistant.js 等と同型。実運用では admin.js の initApp() が必ず実値を注入するため
  // window フォールバックは防御的なものに留まる）。
  var deps = { apiFetch: null, apiFetchRaw: null, escHtml: null, onTabActivate: null, state: null };
  var initialized = false;

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    return fn(path, opts);
  }

  function apiFetchRaw(path, opts) {
    var fn = deps.apiFetchRaw || window.apiFetchRaw;
    return fn(path, opts);
  }

  function escHtml(s) {
    var fn = deps.escHtml || window.escHtml;
    return fn(s);
  }

  function onTabActivate(tabName, fn) {
    var f = deps.onTabActivate || window.onTabActivate;
    return f(tabName, fn);
  }

  // admin.js 側の共有 state オブジェクト（state.exportContext の読み書きに使う）。
  // init() で注入される（同一オブジェクト参照を保持するので admin.js 側からも変更が見える）。
  var state = null;

  // ============================================================================
  // ↓↓↓ ここから admin.js L5029-L11321 の逐語移設（ロジック・DOM ID・関数名は無変更） ↓↓↓
  // ============================================================================

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
      lecture_language: "ja",
    },
    // レクチャースライド同期 (migration 040 Phase 3): スライド試聴中の Audio ハンドル
    slideAudioPlayer: null,
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
    // 音声準備確認フロー (Issue #491) §2-2: コース選択後の非同期ロード完了フラグ。
    // lecture-scripts（scriptsLoaded）とコース構造（structureLoaded）の両方が現在の
    // コース分だけ揃うまで音声生成を有効化しない（それまでは「読み込み中」扱い）。
    scriptsLoaded: false,
    structureLoaded: false,
    // 再構成ループ (R層): コーストピック右ペインの表示 ("evidence" | "stumble")
    rightPaneMode: "evidence",
    stumbleByDocument: {},
    // 再構成ループ (R層, G4-R): レビューキュー直行ボタンの件数バッジ。
    // null = 未取得（ボタンは件数無しで表示）。教員自身の作業件数であり、
    // 学習者統計の k-匿名レンジ表示原則とは別物（内部運用カウント）。
    reconReviewCount: null,
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
        // 音声準備確認フロー (Issue #491) §2-2: コース切替の開始時に前コースの
        // chunks・構造・分析状態・音声準備状態（ロード完了フラグ）をクリアし、
        // 音声言語モーダルを必ず閉じる（前コースの状態で音声生成が有効になったり、
        // モーダルが漏れ出すのを防ぐ。以降の非同期ロードが揃うまで readiness=読み込み中）。
        lsState.chunks = [];
        lsState.courseStructure = null;
        lsState.courseComponents = null;
        lsState.analysisStatus = null;
        lsState.pipelineTask = null;
        lsState.scriptsLoaded = false;
        lsState.structureLoaded = false;
        lsState.reconReviewCount = null;
        lsState.stumbleByDocument = {};
        lsCloseAudioLangModal();
        // ボタンはチャンク読み込み完了後に lsRenderChunkList で制御するため
        // ここでは一旦無効化して読み込みを待つ
        if (audioAllBtn) audioAllBtn.disabled = true;
        settingsBtn.disabled = false;
        if (courseContentBtn) courseContentBtn.disabled = false;
        moreMenuBtn.disabled = false;
        lsState.rightPaneVisible = true;
        lsUpdateCourseShell();
        lsUpdateCourseControls();
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
        lsState.settings = { narration_persona: "", response_persona: "", lecture_language: "ja" };
        lsState.courseStructure = null;
        lsState.courseComponents = null;
        // 音声準備確認フロー (Issue #491): コース未選択に戻したらロード完了フラグを
        // リセットし、音声言語モーダルを閉じる（§2-1）。
        lsState.scriptsLoaded = false;
        lsState.structureLoaded = false;
        lsState.reconReviewCount = null;
        lsState.stumbleByDocument = {};
        lsCloseAudioLangModal();
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
      // 音声準備確認フロー (Issue #491) §3-1: モーダルを開けるのは音声生成が
      // 有効な（全条件を満たす）ときだけ。fail-closed。
      if (!lsCanGenerateAudio()) return;
      lsCloseMenus();
      lsOpenAudioLangModal();
    });

    lsInitAudioLangModal();

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
          // レクチャースライド同期 + 音声言語切替 (migration 040): バックエンドが未対応でも
          // 既定 "ja" にフォールバックする（Phase 4 実装待ちでも動作するよう防御的に）。
          lecture_language: settings.lecture_language || "ja",
        };
      })
      .catch(function () {
        lsState.settings = { narration_persona: "", response_persona: "", lecture_language: "ja" };
      });
  }

  // レクチャースライド同期 + 音声言語切替 (migration 040): コースの現在の読み上げ言語。
  function lsCourseLectureLanguage() {
    return (lsState.settings && lsState.settings.lecture_language) || "ja";
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
          lecture_language: saved.lecture_language || lsCourseLectureLanguage(),
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

  // レクチャースライド同期 + 音声言語切替 (migration 040 Phase 3/4): 音声一括生成の言語選択モーダル。
  // #ls-audio-all-btn は即実行せずこのモーダルを開き、[生成を開始] で lsBatchAudio(language) を呼ぶ。
  function lsAudioStatusForLanguage(language) {
    var chunks = lsState.chunks || [];
    var total = 0;
    var ready = 0;
    var sawField = false;
    chunks.forEach(function (c) {
      if (typeof c.slide_count !== "number") return;
      sawField = true;
      total += c.slide_count;
      var chunkLang = c.spoken_language || "ja";
      if (chunkLang === language && typeof c.audio_ready_slides === "number") {
        ready += c.audio_ready_slides;
      }
    });
    return sawField ? { total: total, ready: ready } : null;
  }

  function lsAudioLangLabel(language) {
    return language === "en" ? "English" : "日本語";
  }

  function lsRenderAudioLangModalStatus(language) {
    var statusEl = document.getElementById("ls-audio-lang-status");
    if (!statusEl) return;
    var stat = lsAudioStatusForLanguage(language);
    if (!stat) {
      // フィールド欠落時（Phase 4 バックエンド未反映時）は表示を省略する（防御的）。
      statusEl.textContent = "";
      return;
    }
    statusEl.textContent = "現状: " + lsAudioLangLabel(language) + "音声 " + stat.ready + "/" + stat.total + " スライド生成済み";
  }

  function lsUpdateAudioLangWarning(selectedLanguage) {
    var warnEl = document.getElementById("ls-audio-lang-warning");
    if (!warnEl) return;
    var currentLanguage = lsCourseLectureLanguage();
    if (selectedLanguage !== currentLanguage) {
      warnEl.textContent = "⚠ 読み上げ原稿を " + lsAudioLangLabel(selectedLanguage) + " で再生成し、音声を作り直します。" +
        "既存の " + lsAudioLangLabel(currentLanguage) + " 音声は使われなくなります。";
      warnEl.style.display = "block";
    } else {
      warnEl.style.display = "none";
    }
  }

  function lsSelectedAudioLangValue() {
    var checked = document.querySelector('input[name="ls-audio-lang"]:checked');
    return checked ? checked.value : lsCourseLectureLanguage();
  }

  function lsOpenAudioLangModal() {
    // 音声準備確認フロー (Issue #491) §3-1: モーダルを開けるのは音声生成が有効なとき
    // だけ（fail-closed）。ボタンの click 経路以外からの誤呼び出しでも準備未完了なら開かない。
    if (!lsCanGenerateAudio()) return;
    var modal = document.getElementById("ls-audio-lang-modal");
    if (!modal) {
      // モーダルDOMが無い場合でも音声生成自体は現在の設定言語でブロックしない（fail-safe）。
      lsBatchAudio(lsCourseLectureLanguage());
      return;
    }
    var currentLanguage = lsCourseLectureLanguage();
    var jaRadio = document.getElementById("ls-audio-lang-ja");
    var enRadio = document.getElementById("ls-audio-lang-en");
    if (jaRadio) jaRadio.checked = currentLanguage !== "en";
    if (enRadio) enRadio.checked = currentLanguage === "en";
    lsRenderAudioLangSummary();
    lsRenderAudioLangModalStatus(currentLanguage);
    lsUpdateAudioLangWarning(currentLanguage);
    var startBtn = document.getElementById("ls-audio-lang-start");
    if (startBtn) startBtn.disabled = false;
    modal.hidden = false;
  }

  // 音声準備確認フロー (Issue #491) §3-1: モーダル上部に選択中コース名と対象スライド数を表示する。
  function lsRenderAudioLangSummary() {
    var el = document.getElementById("ls-audio-lang-summary");
    if (!el) return;
    var title = lsSelectedCourseTitle() || lsState.courseId || "";
    var totalSlides = 0;
    (lsState.chunks || []).forEach(function (c) {
      if (typeof c.slide_count === "number") totalSlides += c.slide_count;
    });
    var html = "コース: <strong>" + escHtml(title) + "</strong>";
    if (totalSlides > 0) {
      html += "<br>対象スライド数: <strong>" + totalSlides + "</strong> 枚";
    }
    el.innerHTML = html;
  }

  function lsCloseAudioLangModal() {
    var modal = document.getElementById("ls-audio-lang-modal");
    if (modal) modal.hidden = true;
  }

  function lsInitAudioLangModal() {
    var modal = document.getElementById("ls-audio-lang-modal");
    if (!modal || modal.getAttribute("data-ls-bound") === "1") return;
    modal.setAttribute("data-ls-bound", "1");
    var closeBtn = document.getElementById("ls-audio-lang-close");
    var cancelBtn = document.getElementById("ls-audio-lang-cancel");
    var startBtn = document.getElementById("ls-audio-lang-start");
    if (closeBtn) closeBtn.addEventListener("click", lsCloseAudioLangModal);
    if (cancelBtn) cancelBtn.addEventListener("click", lsCloseAudioLangModal);
    modal.addEventListener("click", function (e) {
      if (e.target === modal) lsCloseAudioLangModal();
    });
    ["ls-audio-lang-ja", "ls-audio-lang-en"].forEach(function (id) {
      var radio = document.getElementById(id);
      if (!radio) return;
      radio.addEventListener("change", function () {
        var lang = this.value;
        lsRenderAudioLangModalStatus(lang);
        lsUpdateAudioLangWarning(lang);
      });
    });
    if (startBtn) startBtn.addEventListener("click", function () {
      var lang = lsSelectedAudioLangValue();
      lsCloseAudioLangModal();
      lsBatchAudio(lang);
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
        // 音声準備確認フロー (Issue #491) §2-2: 選択が切り替わっていたら遅れて届いた
        // 応答は破棄する（前コースの chunks で音声生成が有効化されるのを防ぐ）。
        if (lsState.courseId !== courseId) return;
        lsState.chunks = chunks;
        lsState.scriptsLoaded = true;
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
        // 再構成ループ (R層, G4-R): レビューキュー直行ボタンの件数バッジを更新
        lsRefreshReconReviewBadge(courseId);
      })
      .catch(function () {
        // 音声準備確認フロー (Issue #491): 読み込み失敗も「読み込み完了（結果は空）」
        // として扱い、音声生成を fail-closed で無効のままにする。scripts の失敗時は
        // 後続の lsLoadCourseStructure が走らないため、両フラグをここで確定させて
        // readiness が「読み込み中」のまま固まらないようにする。
        if (lsState.courseId !== courseId) return;
        lsState.scriptsLoaded = true;
        lsState.structureLoaded = true;
        listEl.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">読み込みに失敗しました</div>';
        lsUpdateCourseControls();
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
        // 音声準備確認フロー (Issue #491) §2-2: 遅れて届いた応答は破棄。courseId が
        // 一致する場合はデータの有無に関わらず「構造ロード完了」とし、chunks 側と
        // 合わせて readiness を確定させる（構造無し＝コース内容未完了として無効化）。
        if (lsState.courseId !== courseId) return;
        lsState.structureLoaded = true;
        lsState.courseStructure = data || null;
        lsUpdateCourseControls();
        if (lsState.leftTab === "course") lsRenderCourseStructure();
      })
      .catch(function () {
        if (lsState.courseId !== courseId) return;
        lsState.structureLoaded = true;
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
      lsReconReviewBadgeHtml() +
      '</div>';

    if (!s.chapters || !s.chapters.length) {
      html += '<div style="padding:12px 16px;color:var(--color-text-tertiary);font-size:12px">章構造がありません。<br>コースビルダーでコース設計を完了してください。</div>';
      el.innerHTML = html;
      lsBindReconReviewButtons();
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
    lsBindReconReviewButtons();

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
      lsReconReviewBadgeHtml() +
      '</div>';

    if (data.analysis_status !== "completed" && (!data.components || !data.components.length)) {
      html += '<div style="padding:12px 16px;color:var(--color-text-tertiary);font-size:12px">' +
        (data.analysis_status === "running" ? "Agent解析実行中です..." :
         data.analysis_status === "pending" ? "Agent解析待ちです..." :
         "Agent解析が未完了です。<br>パイプラインを実行してコンポーネントを生成してください。") +
        '</div>';
      el.innerHTML = html;
      lsBindReconReviewButtons();
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
    lsBindReconReviewButtons();

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
          // レクチャースライド同期 (migration 040 Phase 3): 表示/読み上げのスライド分割数が
          // 一致しないチャンクに ⚠ を添える（既存の status 表示は壊さない）。
          var slideMismatchBadge = lsChunkSlideMismatch(c)
            ? '<span class="ls-slide-mismatch-badge" title="表示テキストと読み上げテキストのスライド区切り数が一致していません">⚠</span>'
            : "";
          html +=
            '<div class="ls-chunk-item ls-chunk-child' + active + '" data-chunk-id="' + escHtml(c.chunk_id) + '">' +
              '<span class="ls-chunk-status ' + escHtml(c.status) + '"></span>' +
              '<span class="ls-chunk-label">chunk ' + (c.chunk_index || i) + " " + escHtml(preview) + theoryState + slideMismatchBadge + '</span>' +
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
    lsStopSlideAudioPlayback();
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
    lsStopSlideAudioPlayback();
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
    lsStopSlideAudioPlayback();
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
      if (lsState.displayView === "slides" && btn.getAttribute("data-ls-display") !== "slides") {
        lsStopSlideAudioPlayback();
      }
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
    // レクチャースライド同期 (migration 040 Phase 3): どちらの textarea に最後に
    // フォーカスしていたかを覚えておく（「スライド区切りを挿入」の挿入先判定に使う）。
    document.getElementById("ls-display-text").addEventListener("focus", function () {
      lsSlideToolsLastFocus = "ls-display-text";
    });
    document.getElementById("ls-spoken-text").addEventListener("focus", function () {
      lsSlideToolsLastFocus = "ls-spoken-text";
    });
    // 整合インジケータ・スライドタブのライブ更新（描画は debounce 300ms）。
    document.getElementById("ls-display-text").addEventListener("input", function () {
      lsUpdateSlideIndicatorsDebounced();
    });
    document.getElementById("ls-spoken-text").addEventListener("input", function () {
      lsUpdateSlideIndicatorsDebounced();
    });
    var insertSlideMarkerBtn = document.getElementById("ls-insert-slide-marker-btn");
    if (insertSlideMarkerBtn) {
      insertSlideMarkerBtn.addEventListener("click", function () {
        lsInsertSlideMarker();
      });
    }
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
        if (action === "endorse") lsOpenEndorsementModal(component);
        if (action === "deliberate") {
          if (window.Deliberation) {
            window.Deliberation.openElement("theory_component", component.id, {
              documentId: lsTheoryElementDocumentId(component),
              title: component.name || "",
            });
          }
        }
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
    // 再構成ループ (R層): コーストピックでは根拠リンク⇄つまづきトグルを出す
    var stumbleTabs = document.getElementById("ls-stumble-tabs");
    if (stumbleTabs) stumbleTabs.hidden = false;
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

    lsRenderRightPaneForTopic(topic);
    var saveBtn = document.getElementById("ls-save-btn");
    if (saveBtn) saveBtn.disabled = false;
    var rewriteBtn = document.getElementById("ls-rewrite-btn");
    if (rewriteBtn) rewriteBtn.disabled = true;
    lsApplyRightPaneVisibility();
    lsUpdateAssistantContext();
  }

  // 再構成ループ (R層): 右ペインを現在のモード（根拠リンク / つまづき）で描画する。
  function lsRenderRightPaneForTopic(topic) {
    var rightTitle = document.getElementById("ls-right-title");
    var preview = document.getElementById("ls-display-preview");
    var tabs = document.getElementById("ls-stumble-tabs");
    if (tabs) {
      tabs.querySelectorAll("[data-ls-rightmode]").forEach(function (btn) {
        var active = btn.getAttribute("data-ls-rightmode") === lsState.rightPaneMode;
        btn.classList.toggle("active", active);
        btn.onclick = function () {
          lsState.rightPaneMode = btn.getAttribute("data-ls-rightmode") || "evidence";
          lsRenderRightPaneForTopic(topic);
        };
      });
    }
    if (!preview) return;
    if (lsState.rightPaneMode === "stumble") {
      if (rightTitle) rightTitle.textContent = "つまづき";
      preview.innerHTML = '<div class="ls-course-muted">つまづきの集計を読み込み中…</div>';
      lsLoadStumbleSummary(topic, preview);
    } else {
      if (rightTitle) rightTitle.textContent = "根拠リンク";
      preview.innerHTML = lsCourseEvidenceHtml(topic);
    }
  }

  // つまづきサマリーを document 単位で取得（キャッシュ）し、トピックの claim に絞って描画。
  function lsLoadStumbleSummary(topic, container) {
    var documentId = lsCurrentDocumentId() || lsCoursePrimaryDocumentId();
    if (!documentId) {
      container.innerHTML = '<div class="ls-course-muted">このコースに紐づく解析済みドキュメントが見つかりません。</div>';
      return;
    }
    var cached = lsState.stumbleByDocument[documentId];
    if (cached) { lsRenderStumbleSummary(topic, container, cached); return; }
    apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/claims/stumble-summary")
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data) {
          container.innerHTML = '<div class="ls-course-muted">つまづき集計を取得できませんでした。</div>';
          return;
        }
        lsState.stumbleByDocument[documentId] = data;
        lsRenderStumbleSummary(topic, container, data);
      })
      .catch(function () {
        container.innerHTML = '<div class="ls-course-muted">つまづき集計の取得に失敗しました。</div>';
      });
  }

  // コースの主ドキュメント id（sources[].material_id）を返す（chunk 由来が取れない場合の保険）。
  function lsCoursePrimaryDocumentId() {
    var course = lsState.courseStructure || {};
    var sources = course.sources || (course.data && course.data.sources) || [];
    for (var i = 0; i < sources.length; i++) {
      if (sources[i] && sources[i].material_id) return sources[i].material_id;
    }
    return "";
  }

  // トピックに紐づく claim id（evidence_links kind='claim'）の集合を返す。
  function lsStumbleClaimIdsForTopic(topic) {
    var ids = {};
    (topic.evidence_links || []).forEach(function (link) {
      if (link && link.kind === "claim") {
        var id = link.target_id || link.id || "";
        if (id) ids[id] = true;
      }
    });
    return ids;
  }

  function lsRenderStumbleSummary(topic, container, data) {
    var claims = (data && data.claims) || [];
    var topicIds = lsStumbleClaimIdsForTopic(topic);
    var hasFilter = Object.keys(topicIds).length > 0;
    var filtered = hasFilter
      ? claims.filter(function (c) { return topicIds[c.claim_id]; })
      : claims;
    if (!filtered.length) {
      container.innerHTML = '<div class="ls-course-muted">' +
        (hasFilter
          ? 'このトピックの claim には、まだ再構成の記録がありません。'
          : 'このドキュメントには、まだ再構成の記録がありません。') +
        '<br>学習者が再構成に挑戦すると、ここに難所が集計されます（k=3 未満は「まだデータなし」）。</div>';
      return;
    }
    var html = '<div class="ls-stumble-list">';
    filtered.forEach(function (c) {
      var axes = c.axes || {};
      var faq = axes.faq || {};
      html += '<article class="ls-stumble-card" data-claim-id="' + escHtml(c.claim_id) + '">' +
        '<div class="ls-stumble-label">' + escHtml(c.label || c.claim_type || c.claim_id) + '</div>' +
        '<div class="ls-stumble-axes">' +
          lsStumbleAxis("誤り率", axes.error_rate) +
          lsStumbleAxis("記号降下", axes.symbol_descent) +
          lsStumbleAxis("判定の乖離", axes.verdict_self_check_divergence) +
          lsStumbleAxis("質問・誤解", faq.questions) +
        '</div>' +
        '<div class="ls-stumble-actions">' +
          '<button type="button" class="ls-stumble-link" data-stumble-review="' + escHtml(c.claim_id) + '">この claim の問いを確認</button>' +
          '<button type="button" class="ls-stumble-link" data-stumble-draft="' + escHtml(c.claim_id) + '">ドラフトの該当箇所へ</button>' +
        '</div>' +
        '<div class="ls-stumble-review" id="ls-stumble-review-' + escHtml(c.claim_id) + '" hidden></div>' +
      '</article>';
    });
    html += '</div>';
    html += '<div class="ls-course-muted ls-stumble-note">段階ラベルとレンジのみを表示します（k=3 の匿名化。個人は特定されません）。</div>';
    container.innerHTML = html;

    container.querySelectorAll("[data-stumble-review]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        lsToggleReviewQueueForClaim(this.getAttribute("data-stumble-review"));
      });
    });
    container.querySelectorAll("[data-stumble-draft]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        // 既存の根拠リンク⇄ドラフト対応を再利用（claim の evidence key へフォーカス）。
        lsFocusEvidence(lsCourseEvidenceKey("claim", this.getAttribute("data-stumble-draft")));
      });
    });
  }

  function lsStumbleAxis(label, value) {
    var v = value || "まだデータなし";
    var cls = "ls-stumble-axis";
    if (v === "高") cls += " ls-stumble-high";
    else if (v === "中") cls += " ls-stumble-mid";
    else if (v === "まだデータなし") cls += " ls-stumble-nodata";
    return '<span class="' + cls + '"><span class="ls-stumble-axis-label">' + escHtml(label) +
      '</span><span class="ls-stumble-axis-value">' + escHtml(v) + '</span></span>';
  }

  // review キュー（該当 claim の item のみ）をインラインで開閉する。
  function lsToggleReviewQueueForClaim(claimId) {
    var box = document.getElementById("ls-stumble-review-" + claimId);
    if (!box) return;
    if (!box.hidden) { box.hidden = true; box.innerHTML = ""; return; }
    box.hidden = false;
    box.innerHTML = '<div class="ls-course-muted">問いを読み込み中…</div>';
    var documentId = lsCurrentDocumentId() || lsCoursePrimaryDocumentId();
    apiFetch("/admin/reconstruction/items/review-queue?document_id=" + encodeURIComponent(documentId))
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        var items = ((data && data.items) || []).filter(function (it) { return it.claim_id === claimId; });
        if (!items.length) {
          box.innerHTML = '<div class="ls-course-muted">この claim に紐づく再構成課題はありません。</div>';
          return;
        }
        box.innerHTML = items.map(function (it) {
          var sig = it.signals || {};
          return '<div class="ls-stumble-item">' +
            '<div class="ls-stumble-item-tier">' + escHtml(it.rank_tier || "") + ' · ' + escHtml(it.elicit_mode || "") + '</div>' +
            '<div class="ls-stumble-item-prompt">' + escHtml(it.prompt || "") + '</div>' +
            '<div class="ls-stumble-item-sig">回答 ' + escHtml(sig.responses || "-") +
              ' / 誤り ' + escHtml(sig.mismatch_level || "-") +
              (sig.verdict_dissent ? ' / 判定への異議あり' : '') + '</div>' +
            '<div class="ls-stumble-item-actions">' +
              '<button type="button" class="ls-stumble-mini" data-item-status="retired" data-item-id="' + escHtml(it.item_id) + '">配信停止（retire）</button>' +
              '<button type="button" class="ls-stumble-mini" data-item-status="confirmed" data-item-id="' + escHtml(it.item_id) + '">追認（confirm）</button>' +
            '</div>' +
          '</div>';
        }).join("");
        box.querySelectorAll("[data-item-id]").forEach(function (b) {
          b.addEventListener("click", function () {
            lsPatchReconItem(this.getAttribute("data-item-id"), this.getAttribute("data-item-status"), box, claimId);
          });
        });
      })
      .catch(function () { box.innerHTML = '<div class="ls-course-muted">読み込みに失敗しました。</div>'; });
  }

  // 既存 PATCH API（教員向け status 遷移。削除 API は無い・retire のみ）の共通呼び出し。
  // claim 別インライン確認パネル（lsToggleReviewQueueForClaim）とレビューキュー直行
  // モーダル（G4-R, lsOpenReconReviewModal）の双方から再利用する。
  function lsPatchReconItemStatus(itemId, status) {
    return apiFetch("/admin/reconstruction/items/" + encodeURIComponent(itemId), {
      method: "PATCH",
      body: JSON.stringify({ status: status }),
    }).then(function (res) { return res.ok ? res.json() : null; });
  }

  function lsPatchReconItem(itemId, status, box, claimId) {
    lsPatchReconItemStatus(itemId, status)
      .then(function () {
        // review キューを再取得して反映
        box.hidden = true;
        lsToggleReviewQueueForClaim(claimId);
      })
      .catch(function () {});
  }

  // ── G4-R: R層レビューキューへの直行導線 ──────────────────────────────
  // 原稿スタジオは「コース→トピック→右ペイン切替→claim カード展開」の5段階の奥に
  // レビューキューが埋もれている（到達前にバッジも無い）。コース選択直後のコース/
  // コンポーネントビュー付近に件数バッジ付きボタンを出し、押下でレビューキューを
  // フラットに一覧するモーダルを開き、その場で confirm/retire まで行えるようにする。
  // 既存の5段階の経路（lsToggleReviewQueueForClaim 経由）はそのまま残す。

  function lsReconReviewBadgeHtml() {
    var label = "再構成の確認";
    if (typeof lsState.reconReviewCount === "number") {
      label += "（要レビュー " + lsState.reconReviewCount + "件）";
    }
    return '<button type="button" class="ls-recon-review-btn" data-ls-recon-review="1">' +
      escHtml(label) + '</button>';
  }

  function lsUpdateReconReviewBadgeText() {
    var label = "再構成の確認";
    if (typeof lsState.reconReviewCount === "number") {
      label += "（要レビュー " + lsState.reconReviewCount + "件）";
    }
    document.querySelectorAll("[data-ls-recon-review]").forEach(function (btn) {
      btn.textContent = label;
    });
  }

  function lsBindReconReviewButtons() {
    document.querySelectorAll("[data-ls-recon-review]").forEach(function (btn) {
      btn.onclick = function () { lsOpenReconReviewModal(); };
    });
  }

  // 件数バッジの母数: status='auto'（教員未確認）かつ rank_tier が付いた（人数 k=3
  // 以上で疑わしさランクが算出された）item のみを「教員が今日やること」として数える。
  // 「情報不足」（k未満）は教員の作業対象ではないため数えない。これは学習者統計の
  // k-匿名レンジ表示（3-5/6-10/11+）とは別物の内部運用カウントであり、生数値のまま表示してよい
  // （教員自身の作業件数。学習者個別データは一切含まない）。
  function lsRefreshReconReviewBadge(courseId) {
    var documentId = lsCurrentDocumentId() || lsCoursePrimaryDocumentId();
    if (!documentId) {
      lsState.reconReviewCount = null;
      lsUpdateReconReviewBadgeText();
      return;
    }
    apiFetch("/admin/reconstruction/items/review-queue?document_id=" + encodeURIComponent(documentId))
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (courseId && lsState.courseId !== courseId) return;
        var items = (data && data.items) || [];
        lsState.reconReviewCount = items.filter(function (it) {
          return it.status === "auto" && it.rank_tier && it.rank_tier !== "情報不足";
        }).length;
        lsUpdateReconReviewBadgeText();
      })
      .catch(function () {
        lsState.reconReviewCount = null;
        lsUpdateReconReviewBadgeText();
      });
  }

  function lsOpenReconReviewModal() {
    var existing = document.getElementById("ls-recon-review-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "ls-recon-review-modal";
    overlay.className = "ls-settings-modal";
    overlay.innerHTML =
      '<div class="ls-settings-dialog ls-recon-review-dialog">' +
        '<div class="ls-settings-head">' +
          '<h3>再構成の確認</h3>' +
          '<button id="ls-recon-review-close" class="lecture-chat-close" type="button">&times;</button>' +
        '</div>' +
        '<p class="ls-course-muted">疑わしさランク順の一覧です。教員は事後の監査役であり、' +
          'AI が自動生成した問いをその場で追認（confirm）・配信停止（retire）できます。</p>' +
        '<div id="ls-recon-review-list"><div class="ls-course-muted">読み込み中…</div></div>' +
      '</div>';
    document.body.appendChild(overlay);

    function close() { overlay.remove(); }
    document.getElementById("ls-recon-review-close").addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });

    lsLoadReconReviewModalList();
  }

  function lsLoadReconReviewModalList() {
    var list = document.getElementById("ls-recon-review-list");
    if (!list) return;
    var documentId = lsCurrentDocumentId() || lsCoursePrimaryDocumentId();
    var qs = documentId ? "?document_id=" + encodeURIComponent(documentId) : "";
    apiFetch("/admin/reconstruction/items/review-queue" + qs)
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        lsRenderReconReviewModalList((data && data.items) || []);
      })
      .catch(function () {
        list.innerHTML = '<div class="ls-course-muted">読み込みに失敗しました。</div>';
      });
  }

  function lsRenderReconReviewModalList(items) {
    var list = document.getElementById("ls-recon-review-list");
    if (!list) return;
    if (!items.length) {
      list.innerHTML = '<div class="ls-course-muted">現在レビュー対象の再構成課題はありません。</div>';
      return;
    }
    list.innerHTML = items.map(function (it) {
      var sig = it.signals || {};
      return '<div class="ls-stumble-item" data-recon-item="' + escHtml(it.item_id) + '">' +
        '<div class="ls-stumble-item-tier">' + escHtml(it.rank_tier || "") + ' · ' + escHtml(it.elicit_mode || "") +
          (it.status ? ' · ' + escHtml(it.status) : '') + '</div>' +
        '<div class="ls-stumble-item-prompt">' + escHtml(it.prompt || "") + '</div>' +
        '<div class="ls-stumble-item-sig">回答 ' + escHtml(sig.responses || "-") +
          ' / 誤り ' + escHtml(sig.mismatch_level || "-") +
          (sig.verdict_dissent ? ' / 判定への異議あり' : '') + '</div>' +
        '<div class="ls-stumble-item-actions">' +
          '<button type="button" class="ls-stumble-mini" data-recon-item-status="retired" data-recon-item-id="' + escHtml(it.item_id) + '">配信停止（retire）</button>' +
          '<button type="button" class="ls-stumble-mini" data-recon-item-status="confirmed" data-recon-item-id="' + escHtml(it.item_id) + '">追認（confirm）</button>' +
        '</div>' +
      '</div>';
    }).join("");
    list.querySelectorAll("[data-recon-item-id]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        lsPatchReconItemInModal(btn.getAttribute("data-recon-item-id"), btn.getAttribute("data-recon-item-status"));
      });
    });
  }

  function lsPatchReconItemInModal(itemId, status) {
    lsPatchReconItemStatus(itemId, status)
      .then(function () {
        lsLoadReconReviewModalList();
        lsRefreshReconReviewBadge();
        // claim 別つまづきパネルのキャッシュも古くなるため破棄する（次回開いたときに再取得）
        lsState.stumbleByDocument = {};
      })
      .catch(function () {});
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
        // レクチャースライド同期 (migration 040 Phase 3): 教材/本文説明にも同じ
        // 区切り挿入ボタンと整合インジケータを付ける（編集タブでのみ表示）。
        '<div class="ls-course-slide-tools-row"' + editHidden + '>' +
          '<button type="button" id="ls-course-insert-slide-marker-btn" class="ls-mini-tab" ' +
            'title="最後にフォーカスしていた入力欄（教材 or 本文説明）のカーソル位置に区切りを挿入します。もう一方には自動挿入されないため、対応する位置には手動で追加してください。">+ スライド区切りを挿入</button>' +
          '<span id="ls-course-slides-indicator" class="ls-slides-indicator ls-slides-indicator-inline"></span>' +
        '</div>' +
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

    // レクチャースライド同期 (migration 040 Phase 3): コーストピックドラフトの
    // 「スライド区切りを挿入」ボタン + 整合インジケータ。
    var spokenScriptEl = document.getElementById("ls-course-spoken-script");
    var courseSlideLastFocus = "ls-course-material-text";
    if (materialEl) materialEl.addEventListener("focus", function () { courseSlideLastFocus = "ls-course-material-text"; });
    if (spokenScriptEl) spokenScriptEl.addEventListener("focus", function () { courseSlideLastFocus = "ls-course-spoken-script"; });

    // サーバへの分割問い合わせは非同期になるため、連続入力時に古いレスポンスが
    // 新しい入力内容を上書きしないよう世代を振って捨てる（lsUpdateSlideIndicatorsNow と同型）。
    var courseSlideRequestSeq = 0;
    function updateCourseSlideIndicator() {
      var indicatorEl = document.getElementById("ls-course-slides-indicator");
      if (!indicatorEl) return;
      var dText = materialEl ? materialEl.value : lsTopicStudentMaterialSource(topic);
      var sText = spokenScriptEl ? spokenScriptEl.value : (topic.spoken_script || topic.content || "");
      var seq = ++courseSlideRequestSeq;
      lsFetchSplitSlides(dText, sText, []).then(function (split) {
        if (seq !== courseSlideRequestSeq) return; // 古いレスポンスは無視
        lsRenderSlideIndicatorEl(indicatorEl, split);
      });
    }
    var courseSlideIndicatorTimer = null;
    function scheduleCourseSlideIndicatorUpdate() {
      if (courseSlideIndicatorTimer) clearTimeout(courseSlideIndicatorTimer);
      courseSlideIndicatorTimer = setTimeout(function () {
        courseSlideIndicatorTimer = null;
        updateCourseSlideIndicator();
      }, 300);
    }
    if (materialEl) materialEl.addEventListener("input", scheduleCourseSlideIndicatorUpdate);
    if (spokenScriptEl) spokenScriptEl.addEventListener("input", scheduleCourseSlideIndicatorUpdate);
    updateCourseSlideIndicator();

    var courseInsertMarkerBtn = document.getElementById("ls-course-insert-slide-marker-btn");
    if (courseInsertMarkerBtn) {
      courseInsertMarkerBtn.addEventListener("click", function () {
        var target = document.getElementById(courseSlideLastFocus) || materialEl;
        lsInsertSlideMarkerIntoTextarea(target);
        updateTopic();
        updateCourseSlideIndicator();
      });
    }
  }

  function lsNormalizeEvidenceId(id) {
    var value = String(id || "").trim();
    value = value.replace(/^\[\[/, "").replace(/\]\]$/, "");
    value = value.replace(/^\[\[/, "").replace(/\]\]$/, "");
    value = value.trim();
    // Collapse a duplicated "eq_" prefix from the legacy double-prefix bug
    // ("eq_eq_F2" → "eq_F2") so legacy and corrected ids resolve to the same key.
    value = value.replace(/^(?:eq_){2,}/i, "eq_");
    return value;
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
    // 数式は content_blocks 由来の formula（latex を持つ）で本文をレンダリングする。
    // evidence_links 由来の数式アイテムにも latex / label を引き当てて、根拠リンク
    // カード・チップが生 LaTeX 文字列ではなく数式・ラベルとして表示されるようにする。
    var formulaByNorm = {};
    lsTopicFormulas(topic).forEach(function (f) {
      formulaByNorm[lsNormalizeEvidenceId(f.id)] = f;
    });
    (topic.evidence_links || []).forEach(function (link) {
      if (!link) return;
      var kind = link.kind || "source";
      var id = link.target_id || link.id || "";
      if (kind === "equation") {
        var f = formulaByNorm[lsNormalizeEvidenceId(id)];
        var eqLatex = link.latex || (f && f.latex) || "";
        var eqLabel = link.label || (f && f.label) || lsNormalizeEvidenceId(id);
        items.push({
          key: lsCourseEvidenceKey(kind, id),
          kind: kind,
          id: id,
          // タイトルに生 LaTeX を出さない。ラベル（無ければ正規化ID）を使う。
          title: eqLabel,
          summary: link.summary || "",
          latex: eqLatex,
          plain_text: link.plain_text || (f && f.plain_text) || "",
          role: link.support_role || "equation",
          confidence: link.confidence || "",
        });
        return;
      }
      // equation 以外（source の equation_quote 等）でも latex を持てる。
      // 旧データ互換: summary に生 TeX が入っている場合は latex に移し、
      // タイトル・本文に生 LaTeX 文字列を出さず数式として描画する。
      var srcLatex = link.latex || "";
      var srcSummary = link.summary || "";
      if (!srcLatex && lsLooksLikeTexMath(srcSummary)) {
        srcLatex = srcSummary;
        srcSummary = "";
      }
      items.push({
        key: lsCourseEvidenceKey(kind, id),
        kind: kind,
        id: id,
        title: srcLatex
          ? (link.label || (link.support_role === "equation_quote" ? "数式引用" : "数式"))
          : (srcSummary || id || kind),
        summary: srcSummary,
        latex: srcLatex,
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
    var items = lsTopicEvidenceItems(topic);
    var exact = items.find(function (item) {
      return item.key === key || (item.kind === kind && lsNormalizeEvidenceId(item.id) === normalizedId);
    });
    if (exact) return exact;
    // kind フォールバック: 本文の埋め込みが kind を取り違えていても（例: 実体は
    // component なのに `claim:comp_001` と書かれている場合）、同一IDの根拠アイテムが
    // あれば解決する。これにより誤った kind 接頭辞による「未解決」表示を防ぐ。
    return items.find(function (item) {
      return lsNormalizeEvidenceId(item.id) === normalizedId;
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
        (item.latex
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
        var formula = formulaById[String(embed.id)] || formulaById[embedId] ||
          (evidenceItem && (evidenceItem.latex || evidenceItem.plain_text || evidenceItem.raw_text) ? evidenceItem : null);
        var body = lsEquationEmbedBody(formula);
        if (body) {
          return '<button type="button" class="ls-material-embed ls-material-formula-only" data-evidence-ref="' + escHtml(key) + '">' +
            body +
          '</button>';
        }
        return '<button type="button" class="ls-material-embed ls-material-missing" data-evidence-ref="' + escHtml(key) + '">' +
          '<span class="ls-material-embed-kind">未解決の数式</span>' +
          '<strong>' + escHtml(embedId || "数式") + '</strong>' +
          '<span class="ls-material-embed-summary">この数式IDに対応する数式本文を取得できませんでした。</span>' +
        '</button>';
      }
      if (embed.kind === "source" && (embedId === "summary" || embedId === "topic_summary")) {
        return '<button type="button" class="ls-material-embed ls-material-evidence-card ls-material-source" data-evidence-ref="' + escHtml(key) + '">' +
          '<span class="ls-material-embed-kind">概要</span>' +
          '<strong>トピック概要</strong>' +
          '<span class="ls-material-embed-summary">' + escHtml(lsShortSummary(topic.summary || "", 260) || "このトピックの概要はまだ生成されていません。") + '</span>' +
        '</button>';
      }
      // 特定の根拠アイテム（source span / claim / component 等）が解決できる場合は、
      // 汎用のトピック原文抜粋フォールバックより優先して、そのアイテム自身を表示する。
      if (evidenceItem) {
        // 数式（equation_quote の source 等）は生 LaTeX 文字列を切り詰めて出さず、
        // KaTeX で数式として描画する。
        var embedBody = evidenceItem.latex
          ? '<span class="ls-material-embed-summary ls-material-embed-formula">' + lsRenderKatex(evidenceItem.latex, true) + '</span>'
          : '<span class="ls-material-embed-summary">' + escHtml(lsShortSummary(evidenceItem.summary, 260) || "この教材要素に紐づく根拠リンクです。右ペインで詳細を確認できます。") + '</span>';
        return '<button type="button" class="ls-material-embed ls-material-evidence-card" data-evidence-ref="' + escHtml(key) + '">' +
          '<span class="ls-material-embed-kind">' + escHtml(evidenceItem.kind) + '</span>' +
          '<strong>' + escHtml(evidenceItem.title || evidenceItem.id) + '</strong>' +
          embedBody +
          '<span class="ls-material-embed-meta">' + escHtml([evidenceItem.role, evidenceItem.confidence].filter(Boolean).join(" / ")) + '</span>' +
        '</button>';
      }
      if (embed.kind === "source" && topic.source_excerpt) {
        return '<button type="button" class="ls-material-embed ls-material-evidence-card ls-material-source" data-evidence-ref="' + escHtml(key) + '">' +
          '<span class="ls-material-embed-kind">原文抜粋</span>' +
          '<strong>参照抜粋</strong>' +
          '<span class="ls-material-embed-summary">' + escHtml(lsShortSummary(topic.source_excerpt, 260)) + '</span>' +
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
    // レクチャースライド同期 (migration 040 Phase 3)
    var slidesEl = document.getElementById("ls-slides-panel");
    var slideToolsRow = document.getElementById("ls-slide-tools-row");
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
    // 再構成ループ (R層): つまづきトグルはコーストピック専用。チャンク編集ビューでは隠す。
    var stumbleTabsEl = document.getElementById("ls-stumble-tabs");
    if (stumbleTabsEl) stumbleTabsEl.hidden = true;

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
      if (slidesEl) slidesEl.hidden = true;
      if (slideToolsRow) slideToolsRow.hidden = true;
      document.getElementById("ls-display-preview").hidden = true;
      lsRenderTheoryPanel(chunk);
    } else if (isClaims) {
      document.getElementById("ls-display-tabs").hidden = true;
      spokenEl.hidden = true;
      displayEl.hidden = true;
      if (formulasEl) formulasEl.hidden = true;
      if (slidesEl) slidesEl.hidden = true;
      if (slideToolsRow) slideToolsRow.hidden = true;
      document.getElementById("ls-display-preview").hidden = true;
      lsRenderClaimsPanel(chunk);
    } else if (isGraph) {
      document.getElementById("ls-display-tabs").hidden = true;
      spokenEl.hidden = true;
      displayEl.hidden = true;
      if (formulasEl) formulasEl.hidden = true;
      if (slidesEl) slidesEl.hidden = true;
      if (slideToolsRow) slideToolsRow.hidden = true;
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
      if (slidesEl) slidesEl.hidden = true;
      if (slideToolsRow) slideToolsRow.hidden = false;
      spokenEl.hidden = false;
      spokenEl.disabled = lsState.syncSpoken;
    } else {
      document.getElementById("ls-right-title").textContent = lsState.displayView === "formulas" ? "数式一覧" :
        lsState.displayView === "slides" ? "スライド" : "表示テキスト";
      document.getElementById("ls-display-tabs").hidden = false;
      spokenEl.hidden = true;
      displayEl.hidden = lsState.displayView !== "script";
      document.getElementById("ls-display-preview").hidden = lsState.displayView !== "preview";
      if (formulasEl) {
        formulasEl.hidden = lsState.displayView !== "formulas";
        if (lsState.displayView === "formulas") lsRenderFormulas(chunk.formulas || []);
      }
      if (slidesEl) {
        // 描画自体は末尾の lsUpdateSlideIndicatorsNow() が担う（分割問い合わせが
        // 非同期になったため、ここで二重にリクエストしない）。
        slidesEl.hidden = lsState.displayView !== "slides";
      }
      if (slideToolsRow) slideToolsRow.hidden = lsState.displayView !== "script";
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
      if (slidesEl) slidesEl.hidden = true;
      if (slideToolsRow) slideToolsRow.hidden = true;
    }
    if (isStructure) lsRenderStructure(chunk);
    lsApplyRightPaneVisibility();
    lsUpdateSlideIndicatorsNow();
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
        '<div class="ls-theory-actions">' +
          '<button class="admin-action-btn ls-claim-deliberate-btn" type="button" data-claim-id="' + escHtml(claim.claim_id) + '" data-document-id="' + escHtml(claim.document_id || "") + '">深く検討</button>' +
        '</div>' +
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
    // W層（要素検討ワークスペース, Phase 0）— theory_claim 要素の「深く検討」導線。
    container.querySelectorAll(".ls-claim-deliberate-btn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var claimId = this.getAttribute("data-claim-id");
        var documentId = this.getAttribute("data-document-id");
        if (window.Deliberation) {
          window.Deliberation.openElement("theory_claim", claimId, {
            documentId: documentId,
            title: claimId,
          });
        }
      });
    });
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
          '<button class="admin-action-btn" data-theory-action="endorse">説明・承認の共有</button>' +
          '<button class="admin-action-btn" data-theory-action="deliberate">深く検討</button>' +
        '</div>' +
      '</div>';
  }

  // W層（要素検討ワークスペース, Phase 0）— theory_component 要素の「深く検討」導線で
  // 使う document_id の解決。TheoryComponentOut.source_scope.document_id
  // （backend/api/schemas.py の TheorySourceScope）は chunk 由来・section 由来・
  // 「選択中コンポーネント」経路のいずれでも共通して埋まっているフィールドなので、
  // 呼び出し元の表示スコープ（チャンク/セクション/単体）に依存せずここから直接読む。
  function lsTheoryElementDocumentId(component) {
    var scope = (component && component.source_scope) || {};
    return scope.document_id || "";
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
          if (action === "endorse") lsOpenEndorsementModal(component);
          if (action === "deliberate") {
            if (window.Deliberation) {
              window.Deliberation.openElement("theory_component", component.id, {
                documentId: lsTheoryElementDocumentId(component),
                title: component.name || "",
              });
            }
          }
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
        if (action === "endorse") lsOpenEndorsementModal(component);
        if (action === "deliberate") {
          if (window.Deliberation) {
            window.Deliberation.openElement("theory_component", component.id, {
              documentId: lsTheoryElementDocumentId(component),
              title: component.name || "",
            });
          }
        }
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
    var readingPath = lsGraphComputeReadingPath(view);
    var html =
      lsGraphNarrativeSummaryHtml(graph) +
      lsGraphReadingPathBannerHtml(view, readingPath) +
      '<div class="ls-component-graph-shell">' +
        '<div class="ls-component-graph-main">' +
          lsGraphLayerToolbarHtml(nodes) +
          lsGraphReadingPathToggleHtml(readingPath.length) +
          // D層 (D3-4): 反実仮想モードのトグル（可逆・非破壊。doubt-atlas.js が本体）
          (window.DoubtAtlas ? window.DoubtAtlas.counterfactualToolbarHtml() : "") +
          '<div id="ls-component-network" class="ls-component-network" tabindex="0"></div>' +
          '<div class="ls-component-legend">' +
            '<div class="ls-component-legend-title">論理コンポーネント</div>' +
            '<div><span class="ls-legend-swatch ls-legend-assumption"></span>前提・定義</div>' +
            '<div><span class="ls-legend-swatch ls-legend-method"></span>手法・構成</div>' +
            '<div><span class="ls-legend-swatch ls-legend-relation"></span>推論・関係</div>' +
            '<div><span class="ls-legend-swatch ls-legend-diagnostic"></span>検証・制約</div>' +
            '<div><span class="ls-legend-swatch ls-legend-conclusion"></span>結論・出力</div>' +
            '<div><span class="ls-legend-swatch ls-legend-uncertainty"></span>注意・不確実性</div>' +
            '<div><span class="ls-legend-anchor">★</span>主張の到達点（アンカー）</div>' +
            '<div><span class="ls-legend-line ls-legend-line-negative"></span>負の関係（抑制・否定）</div>' +
            '<div><span class="ls-legend-line ls-legend-line-positive"></span>正・非極性の関係</div>' +
            (readingPath.length ? '<div><span class="ls-legend-path-num">①</span>推奨経路（順序）</div>' : '') +
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
    lsBindGraphReadingPathToggle(documentId);
    if (window.DoubtAtlas) {
      window.DoubtAtlas.bindCounterfactualToolbar(container, { documentId: documentId });
    }

    if (!window.vis || !window.vis.Network) {
      lsRenderGraphFallback(container, view);
      return;
    }

    lsInitComponentGraphNetwork(view, lsState.graphReadingPath ? readingPath : []);
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

  // Issue #452: recommended reading path on the main graph. Domain-agnostic — nodes
  // are ordered by theory stage (the support-structure-derived canonical order in
  // LS_MAIN_STAGE_LABELS), the destination is the thesis anchor (node flag), and
  // only nodes on a reachable root→anchor route are kept. No node IDs hardcoded.
  function lsCircledNumber(n) {
    if (n >= 1 && n <= 20) return String.fromCharCode(0x2460 + (n - 1));
    return "(" + n + ")";
  }

  function lsGraphStageIndex(node) {
    return LS_MAIN_STAGE_LABELS.indexOf(lsGraphMainStageLabel(node));
  }

  function lsGraphComputeReadingPath(view) {
    var nodes = (view && view.nodes) || [];
    var edges = (view && view.edges) || [];
    var main = nodes.filter(function (n) {
      return String((n && n.graph_layer) || "main").toLowerCase() === "main";
    });
    if (!main.length) main = nodes.slice();
    if (main.length < 2) return [];
    var idOf = function (n) { return lsGraphNodeId(n); };
    var ordered = main.slice().sort(function (a, b) {
      var ia = lsGraphStageIndex(a), ib = lsGraphStageIndex(b);
      var ra = ia < 0 ? 999 : ia, rb = ib < 0 ? 999 : ib;
      if (ra !== rb) return ra - rb;
      return (Number(a.display_order || 0)) - (Number(b.display_order || 0));
    });
    var mainIds = {};
    main.forEach(function (n) { mainIds[idOf(n)] = true; });
    var adj = {}, radj = {};
    edges.forEach(function (e) {
      var s = e.source_component_id || e.source || e.from;
      var t = e.target_component_id || e.target || e.to;
      if (!mainIds[s] || !mainIds[t] || s === t) return;
      (adj[s] = adj[s] || []).push(t);
      (radj[t] = radj[t] || []).push(s);
    });
    // anchor: prefer the flagged thesis anchor (issue #449); else latest stage.
    var anchorNode = null;
    for (var i = ordered.length - 1; i >= 0; i--) {
      if (ordered[i].is_thesis_anchor) { anchorNode = ordered[i]; break; }
    }
    if (!anchorNode) anchorNode = ordered[ordered.length - 1];
    var anchorId = idOf(anchorNode);
    function reach(startId, g) {
      var seen = {}, stack = [startId]; seen[startId] = true;
      while (stack.length) {
        var cur = stack.pop();
        (g[cur] || []).forEach(function (nx) { if (!seen[nx]) { seen[nx] = true; stack.push(nx); } });
      }
      return seen;
    }
    var canReachAnchor = reach(anchorId, radj); // nodes with a forward route to anchor
    var rootId = null;
    for (var j = 0; j < ordered.length; j++) {
      var oid = idOf(ordered[j]);
      if (canReachAnchor[oid]) { rootId = oid; break; }
    }
    if (!rootId) return [];
    var reachableFromRoot = reach(rootId, adj);
    var pathSet = {};
    Object.keys(reachableFromRoot).forEach(function (id) {
      if (canReachAnchor[id]) pathSet[id] = true;
    });
    pathSet[anchorId] = true;
    var path = [];
    ordered.forEach(function (n) { var id = idOf(n); if (pathSet[id]) path.push(id); });
    if (path.indexOf(anchorId) < 0) path.push(anchorId);
    return path.length >= 2 ? path : [];
  }

  function lsGraphReadingPathToggleHtml(hasPath) {
    if (!hasPath) return "";
    var active = lsState.graphReadingPath ? " is-active" : "";
    return '<button type="button" id="ls-graph-path-btn" class="ls-graph-layer-btn ls-graph-path-btn' + active +
      '" aria-pressed="' + (lsState.graphReadingPath ? "true" : "false") + '">推奨経路</button>';
  }

  function lsGraphReadingPathBannerHtml(view, path) {
    if (!lsState.graphReadingPath || !path.length) return "";
    var byId = {};
    (view.nodes || []).forEach(function (n) { byId[lsGraphNodeId(n)] = n; });
    var items = path.map(function (id, i) {
      var n = byId[id];
      var label = n ? (lsGraphSemanticLabel(n) || id).replace(/\n/g, " ") : id;
      return '<li><span class="ls-graph-path-num">' + escHtml(lsCircledNumber(i + 1)) + '</span> ' + escHtml(label) + '</li>';
    }).join("");
    return '<div class="ls-graph-reading-path">' +
      '<div class="ls-graph-reading-path-title">推奨読解経路（前提 → 主張アンカー）' +
      '<span class="ls-graph-narrative-badge">AI提案</span></div>' +
      '<ol class="ls-graph-reading-path-list">' + items + '</ol></div>';
  }

  function lsBindGraphReadingPathToggle(documentId) {
    var btn = document.getElementById("ls-graph-path-btn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      lsState.graphReadingPath = !lsState.graphReadingPath;
      lsRenderGraphPanel(documentId);
    });
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

  // Issue #450: keep the live network + graph so detail-panel reference links can
  // navigate (select + focus) to neighbouring / referenced nodes.
  var lsActiveComponentNetwork = null;
  var lsActiveComponentGraph = null;

  function lsGraphNavigateToNode(nodeId) {
    var graph = lsActiveComponentGraph;
    if (!graph || !nodeId) return;
    var target = null;
    (graph.nodes || []).some(function (n) {
      if (lsGraphNodeId(n) === nodeId) { target = n; return true; }
      return false;
    });
    if (!target) return;
    if (lsActiveComponentNetwork) {
      try {
        lsActiveComponentNetwork.selectNodes([nodeId]);
        lsActiveComponentNetwork.focus(nodeId, { scale: 1.05, animation: { duration: 350, easingFunction: "easeInOutQuad" } });
      } catch (e) { /* node may be filtered out of the current layer */ }
    }
    lsRenderGraphNodeDetail(target, graph);
  }

  function lsInitComponentGraphNetwork(graph, readingPath) {
    var networkEl = document.getElementById("ls-component-network");
    if (!networkEl) return;
    lsActiveComponentGraph = graph;
    // Issue #452: ordered reading-path overlay (id -> 1-based step).
    var pathOrder = {};
    (readingPath || []).forEach(function (id, i) { pathOrder[id] = i + 1; });

    var nodes = graph.nodes || [];
    var edges = graph.edges || [];
    var graphEdges = lsGraphDisplayEdges(edges);
    var layoutPositions = lsGraphLayoutPositions(nodes, graphEdges);
    var nodeById = {};
    nodes.forEach(function (node) {
      var id = lsGraphNodeId(node);
      if (id) nodeById[id] = node;
    });
    // Issue #450: map vis edge id -> edge so edge selection can render its detail.
    var edgeById = {};
    graphEdges.forEach(function (edge, index) {
      edgeById[edge.edge_id || ("edge-" + index)] = edge;
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
      // Issue #449: thesis-anchor nodes (the goal of the argument) must always be
      // visually distinguishable. Emphasis comes from the node's own
      // is_thesis_anchor flag — never from ID string matching — so it works
      // regardless of any prefix mismatch between artifacts (#443 is separate).
      if (node && node.is_thesis_anchor) {
        visNode.label = "★ " + visNode.label;
        visNode.borderWidth = 4;
        visNode.borderWidthSelected = 5;
        visNode.font = { multi: true, color: "#b45309" };
        visNode.shadow = { enabled: true, color: "rgba(245, 158, 11, 0.45)", size: 16, x: 0, y: 0 };
      }
      // Issue #452: number path nodes in reading order and emphasise their border.
      if (pathOrder[id]) {
        visNode.label = lsCircledNumber(pathOrder[id]) + " " + visNode.label;
        if (!(node && node.is_thesis_anchor)) {
          visNode.borderWidth = Math.max(visNode.borderWidth || 2, 3);
        }
      }
      return visNode;
    }));

    var visEdges = new window.vis.DataSet(graphEdges.map(function (edge, index) {
      var from = edge.source_component_id || edge.source || edge.from;
      var to = edge.target_component_id || edge.target || edge.to;
      var relation = edge.relation || edge.edge_type || edge.type || "RELATED_TO";
      var visEdge = {
        id: edge.edge_id || ("edge-" + index),
        from: from,
        to: to,
        label: lsGraphEdgeLabel(relation),
        arrows: "to",
        dashes: lsGraphEdgeDashed(edge),
        width: Math.max(1, Math.min(4, Number(edge.confidence || 0.7) * 4)),
        color: { color: lsGraphEdgeColor(edge) },
      };
      // Issue #452: highlight forward edges that lie on the recommended path.
      if (pathOrder[from] && pathOrder[to] && pathOrder[to] > pathOrder[from]) {
        visEdge.color = { color: "#4f46e5", highlight: "#4338ca" };
        visEdge.width = 4;
        visEdge.dashes = false;
      }
      return visEdge;
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
    lsActiveComponentNetwork = network;

    var fitBtn = document.getElementById("ls-component-graph-fit");
    if (fitBtn) {
      fitBtn.addEventListener("click", function () {
        network.fit({ animation: { duration: 450, easingFunction: "easeInOutQuad" } });
      });
    }
    network.once("afterDrawing", function () {
      network.fit({ animation: false });
    });
    // Issue #450: a single click handler renders the detail panel for ANY selected
    // element — node (priority) or edge — and resets on background click.
    network.on("click", function (params) {
      var detail = document.getElementById("ls-component-graph-detail");
      // D層 (D3-4): 反実仮想モード中はノードタップを「仮に偽」トグルとして扱う
      if (params.nodes && params.nodes.length &&
          window.DoubtAtlas && window.DoubtAtlas.isCounterfactualActive()) {
        window.DoubtAtlas.toggleCounterfactualNode(params.nodes[0], visNodes);
        return;
      }
      if (params.nodes && params.nodes.length) {
        var node = nodeById[params.nodes[0]];
        if (node) { lsRenderGraphNodeDetail(node, graph); return; }
      }
      if (params.edges && params.edges.length) {
        var edge = edgeById[params.edges[0]];
        if (edge) { lsRenderGraphEdgeDetail(edge, graph); return; }
      }
      if (detail) detail.innerHTML = lsGraphEmptyDetailHtml(nodes.length, edges.length);
    });
  }

  // Issue #448: map an internal component_type/node_type to the user-facing role
  // name shown in the legend. Implementation-derived type names (e.g. *Node) must
  // never reach the UI; only these role names do. Domain-agnostic — derived from
  // the node's group (keyword-based), not from paper-specific vocabulary.
  var LS_GRAPH_ROLE_LABELS = {
    assumption: "前提・定義",
    method: "手法・構成",
    relation: "推論・関係",
    diagnostic: "検証・制約",
    conclusion: "結論・出力",
    uncertainty: "注意・不確実性",
  };

  function lsGraphRoleLabel(node) {
    return LS_GRAPH_ROLE_LABELS[lsGraphNodeGroup(node)] || LS_GRAPH_ROLE_LABELS.relation;
  }

  // Issue #450: common reference resolution. Resolve an id against the actual
  // sets present in the view — graph nodes (always), and claims/equations
  // (best-effort, from loaded chunk state). Unresolvable references are reported
  // explicitly, never silently dropped.
  function lsGraphBuildResolver(graph) {
    var nodeMap = {};
    ((graph && graph.nodes) || []).forEach(function (n) {
      var id = lsGraphNodeId(n);
      if (id) nodeMap[id] = n;
    });
    var claimMap = {};
    var equationMap = {};
    var byChunk = lsState.claimsByChunk || {};
    Object.keys(byChunk).forEach(function (cid) {
      (byChunk[cid] || []).forEach(function (claim) {
        if (!claim) return;
        var clid = claim.claim_id || claim.id;
        if (clid && !claimMap[clid]) claimMap[clid] = claim;
        var eq = claim.equation;
        if (eq) {
          var eqid = eq.equation_id || eq.label;
          if (eqid && !equationMap[eqid]) equationMap[eqid] = eq;
        }
      });
    });
    return { nodeMap: nodeMap, claimMap: claimMap, equationMap: equationMap };
  }

  function lsGraphSnippet(text, max) {
    var s = String(text || "").replace(/\s+/g, " ").trim();
    var limit = max || 90;
    return s.length > limit ? s.slice(0, limit - 1) + "…" : s;
  }

  // kind: "component" | "equation" | "claim" | "evidence" | "derivation"
  function lsGraphResolveRef(resolver, kind, id) {
    var ref = { id: id, kind: kind, resolved: false, label: id, navNodeId: "", latex: "" };
    if (!id) return ref;
    if (kind === "component") {
      var node = resolver.nodeMap[id];
      if (node) {
        ref.resolved = true;
        ref.navNodeId = id;
        ref.label = (lsGraphSemanticLabel(node) || id).replace(/\n/g, " ");
      }
    } else if (kind === "equation") {
      var eq = resolver.equationMap[id];
      if (eq) {
        ref.resolved = true;
        ref.latex = eq.latex || "";
        ref.label = eq.label || id;
      }
    } else if (kind === "claim") {
      var claim = resolver.claimMap[id];
      if (claim) {
        ref.resolved = true;
        ref.label = lsGraphSnippet(claim.text || claim.normalized_text || id);
      }
    }
    return ref;
  }

  function lsGraphRefChipHtml(ref) {
    if (ref.resolved && ref.navNodeId) {
      return '<button type="button" class="ls-graph-ref ls-graph-ref-nav" data-ls-nav-node="' +
        escHtml(ref.navNodeId) + '">' + escHtml(ref.label) + '</button>';
    }
    if (ref.resolved) {
      if (ref.kind === "equation" && ref.latex) {
        return '<span class="ls-graph-ref ls-graph-ref-resolved">' + lsRenderKatex(ref.latex, false) +
          ' <span class="ls-graph-ref-id">' + escHtml(ref.id) + '</span></span>';
      }
      return '<span class="ls-graph-ref ls-graph-ref-resolved">' + escHtml(ref.label) + '</span>';
    }
    return '<span class="ls-graph-ref ls-graph-ref-unresolved" title="参照先を解決できませんでした">' +
      escHtml(ref.id) + ' <span class="ls-graph-ref-flag">未解決</span></span>';
  }

  // Render a labelled row of resolved/unresolved reference chips. Returns "" when
  // there are no ids, so the caller can decide overall section emptiness.
  function lsGraphRefRowHtml(resolver, label, kind, ids) {
    var unique = [];
    var seen = {};
    (ids || []).forEach(function (id) {
      var key = String(id || "");
      if (!key || seen[key]) return;
      seen[key] = true;
      unique.push(key);
    });
    if (!unique.length) return "";
    var chips = unique.map(function (id) {
      return lsGraphRefChipHtml(lsGraphResolveRef(resolver, kind, id));
    }).join(" ");
    return '<div class="ls-graph-detail-refrow"><span class="ls-graph-detail-reflabel">' + escHtml(label) +
      '</span><div class="ls-graph-ref-chips">' + chips + '</div></div>';
  }

  // Issue #450: render incoming/outgoing neighbours with their relation type and a
  // navigable chip to the neighbour node.
  function lsGraphNeighborListHtml(list, heading, resolver, graphNarrative) {
    if (!list.length) return "";
    var html = '<div class="ls-graph-detail-neighbor-group">' +
      '<div class="ls-graph-detail-neighbor-head">' + escHtml(heading) + '</div>' +
      '<ul class="ls-graph-detail-edges">';
    list.forEach(function (item) {
      var edge = item.edge;
      var relation = edge.relation || edge.edge_type || edge.type || "RELATED_TO";
      var ref = lsGraphResolveRef(resolver, "component", item.other);
      html += '<li><span>' + escHtml(lsGraphEdgeLabel(relation)) + '</span>' + lsGraphRefChipHtml(ref);
      var evidence = edge.evidence || {};
      var reason = String(evidence.reason || "").trim();
      if (reason) {
        html += '<div class="ls-graph-detail-edge-reason">' + escHtml(reason) + '</div>';
      }
      var edgeNarrative = (graphNarrative.edge_narratives || {})[edge.edge_id];
      var transition = edgeNarrative ? String(edgeNarrative.transition_text || "").trim() : "";
      if (transition) {
        html += '<div class="ls-graph-detail-edge-narrative">' + escHtml(transition) + ' <span class="ls-graph-narrative-badge">AI提案</span></div>';
      }
      html += '</li>';
    });
    html += '</ul></div>';
    return html;
  }

  function lsRenderGraphNodeDetail(node, graph) {
    var detail = document.getElementById("ls-component-graph-detail");
    if (!detail) return;
    var nodeId = lsGraphNodeId(node);
    var backing = String(node.source_backing_status || "").toLowerCase();
    var resolver = lsGraphBuildResolver(graph);
    var graphNarrative = (graph && graph.narrative) || {};
    var anchorMark = node.is_thesis_anchor
      ? '<span class="ls-graph-detail-anchor" title="主張の到達点（アンカー）">★</span> ' : "";

    // Header: role badge (issue #448 — user-facing role name, no internal type) + title.
    var html =
      '<div class="ls-graph-detail-badge ' + escHtml(lsGraphNodeGroup(node)) + '">' +
      escHtml(lsGraphRoleLabel(node)) + '</div>' +
      '<h4>' + anchorMark + escHtml((lsGraphNodeDisplayLabel(node, nodeId) || nodeId || "無題").replace(/\n/g, " — ")) + '</h4>';

    // ── (a) 説明: 何を表すか（平文）─────────────────────────────────
    html += '<div class="ls-graph-detail-section"><b>説明</b>';
    html += '<p class="ls-graph-detail-role">役割: ' + escHtml(lsGraphRoleLabel(node)) + '</p>';
    var description = String(node.description || "").trim();
    if (description) {
      html += '<p>' + escHtml(description) + '</p>';
    } else {
      var fallbackText = (lsGraphNodeDisplayLabel(node, nodeId) || "").replace(/\n/g, " ").trim();
      html += '<p class="ls-theory-muted">' + escHtml(fallbackText || "説明文はありません。") + '</p>';
    }
    var nodeNarrative = (graphNarrative.node_narratives || {})[nodeId];
    if (nodeNarrative && String(nodeNarrative.narrative_role || "").trim()) {
      html += '<p>議論での役割: ' + escHtml(nodeNarrative.narrative_role) + ' <span class="ls-graph-narrative-badge">AI提案</span></p>';
    }
    var reviewReason = String(node.review_reason || "").trim();
    if (reviewReason) {
      html += '<p class="ls-graph-detail-memo">' + escHtml(reviewReason) + '</p>';
    }
    html += '</div>';

    // ── (b) 根拠リンク: 何に支えられるか（共通の参照解決を介す）──────────
    var equationIds = []
      .concat(node.linked_equation_ids || [])
      .concat(node.input_equation_ids || [])
      .concat(node.output_equation_ids || []);
    var claimIds = []
      .concat(node.linked_claim_ids || [])
      .concat(node.input_claim_ids || [])
      .concat(node.output_claim_ids || [])
      .concat(node.required_claim_ids || []);
    var derivationIds = []
      .concat(node.linked_derivation_ids || [])
      .concat(node.supporting_derivation_ids || []);
    var componentIds = []
      .concat(node.representative_component_id ? [node.representative_component_id] : [])
      .concat(node.parent_component_id ? [node.parent_component_id] : [])
      .concat(node.linked_component_ids || [])
      .concat(node.member_component_ids || []);
    var refRows =
      lsGraphRefRowHtml(resolver, "式", "equation", equationIds) +
      lsGraphRefRowHtml(resolver, "claim", "claim", claimIds) +
      lsGraphRefRowHtml(resolver, "evidence", "evidence", node.linked_evidence_ids) +
      lsGraphRefRowHtml(resolver, "derivation", "derivation", derivationIds) +
      lsGraphRefRowHtml(resolver, "関連要素", "component", componentIds);
    html += '<div class="ls-graph-detail-section"><b>根拠リンク</b>' +
      (refRows || '<p class="ls-theory-muted">根拠リンクはありません。</p>') + '</div>';

    // ── (c) 隣接: 何と繋がるか（入る／出るエッジ、関係種別付き）────────────
    var incoming = [];
    var outgoing = [];
    (graph.edges || []).forEach(function (edge) {
      var source = edge.source_component_id || edge.source || edge.from;
      var target = edge.target_component_id || edge.target || edge.to;
      if (source === nodeId) outgoing.push({ edge: edge, other: target });
      else if (target === nodeId) incoming.push({ edge: edge, other: source });
    });
    html += '<div class="ls-graph-detail-section"><b>隣接</b>';
    if (!incoming.length && !outgoing.length) {
      html += '<p class="ls-theory-muted">接続エッジはありません。</p>';
    } else {
      html += lsGraphNeighborListHtml(outgoing, "→ 出力（このノードから）", resolver, graphNarrative);
      html += lsGraphNeighborListHtml(incoming, "← 入力（このノードへ）", resolver, graphNarrative);
    }
    html += '</div>';

    // 要確認事項
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

    // システム情報（折りたたみ。内部 type 名はユーザー向けには出さず、ここにのみ残す — issue #448）
    html += '<div class="ls-graph-detail-section ls-graph-detail-system">' +
      '<details><summary>システム情報</summary>' +
      '<div class="ls-graph-detail-link"><b>ID</b> <code>' + escHtml(nodeId || "") + '</code></div>' +
      '<div class="ls-graph-detail-link"><b>内部type</b> <code>' + escHtml(node.component_type || node.type || "") + '</code></div>' +
      '<div class="ls-graph-detail-link"><b>レイヤー</b> ' + escHtml(lsGraphLayerLabel(node.graph_layer)) + '</div>' +
      '<div class="ls-graph-detail-link"><b>ステータス</b> ' + escHtml(node.review_status || node.origin || "paper") + '</div>';
    var linkage = lsGraphLayerLinkageHtml(node);
    if (linkage) html += linkage;
    html += '</details></div>';

    detail.innerHTML = html;
    lsGraphBindDetailNav(detail);
    // D層 (D1-4/D1-5/D3-1): 認識的地位台帳セクションを静かに追記する。
    // fail-closed — 取得失敗時はセクション自体を出さず既存表示を壊さない。
    if (window.DoubtAtlas && nodeId) {
      window.DoubtAtlas.renderNodeLedgerSection(detail, "component", nodeId);
    }
  }

  // Issue #450: wire navigable reference chips to select + focus the target node.
  function lsGraphBindDetailNav(detail) {
    var navEls = detail.querySelectorAll("[data-ls-nav-node]");
    for (var ni = 0; ni < navEls.length; ni++) {
      (function (el) {
        el.addEventListener("click", function () {
          lsGraphNavigateToNode(el.getAttribute("data-ls-nav-node"));
        });
      })(navEls[ni]);
    }
  }

  // Issue #450: edge detail panel — same three tiers as nodes (説明 → 根拠リンク →
  // 隣接), so selecting ANY element yields a structured, navigable detail view.
  function lsRenderGraphEdgeDetail(edge, graph) {
    var detail = document.getElementById("ls-component-graph-detail");
    if (!detail) return;
    var resolver = lsGraphBuildResolver(graph);
    var graphNarrative = (graph && graph.narrative) || {};
    var relation = edge.relation || edge.edge_type || edge.type || "RELATED_TO";
    var sourceId = edge.source_component_id || edge.source || edge.from;
    var targetId = edge.target_component_id || edge.target || edge.to;
    var evidence = edge.evidence || {};
    var backing = String(edge.source_backing_status || "").toLowerCase();
    var srcRef = lsGraphResolveRef(resolver, "component", sourceId);
    var tgtRef = lsGraphResolveRef(resolver, "component", targetId);

    // Header: relation badge + relation label.
    var html =
      '<div class="ls-graph-detail-badge relation">関係</div>' +
      '<h4>' + escHtml(lsGraphEdgeLabel(relation)) + '</h4>';

    // ── (a) 説明 ─────────────────────────────────────────────────────
    html += '<div class="ls-graph-detail-section"><b>説明</b>';
    html += '<p class="ls-graph-detail-role">関係種別: ' + escHtml(lsGraphEdgeLabel(relation)) + '</p>';
    var domainVerb = String(edge.domain_verb || "").trim();
    if (domainVerb) html += '<p>動詞: ' + escHtml(domainVerb) + '</p>';
    var reasoning = String(evidence.reason || edge.reasoning || "").trim();
    if (reasoning) html += '<p>' + escHtml(reasoning) + '</p>';
    html += '<p class="ls-theory-muted">' +
      escHtml((srcRef.label || sourceId || "?")) + ' → ' + escHtml((tgtRef.label || targetId || "?")) + '</p>';
    var edgeNarrative = (graphNarrative.edge_narratives || {})[edge.edge_id];
    var transition = edgeNarrative ? String(edgeNarrative.transition_text || "").trim() : "";
    if (transition) {
      html += '<p>遷移: ' + escHtml(transition) + ' <span class="ls-graph-narrative-badge">AI提案</span></p>';
    }
    html += '</div>';

    // ── (b) 根拠リンク（evidence_refs を共通の参照解決で解決）──────────────
    var claimIds = [].concat(evidence.evidence_claim_ids || []).concat(evidence.evidence_claims || []);
    var refRows =
      lsGraphRefRowHtml(resolver, "式", "equation", evidence.evidence_equation_ids) +
      lsGraphRefRowHtml(resolver, "claim", "claim", claimIds) +
      lsGraphRefRowHtml(resolver, "derivation", "derivation", evidence.evidence_derivation_ids) +
      lsGraphRefRowHtml(resolver, "evidence", "evidence", evidence.source_evidence_ids) +
      lsGraphRefRowHtml(resolver, "thesis", "component", evidence.thesis_refs);
    html += '<div class="ls-graph-detail-section"><b>根拠リンク</b>' +
      (refRows || '<p class="ls-theory-muted">根拠リンクはありません。</p>') + '</div>';

    // ── (c) 隣接（始点・終点ノード、ともに遷移可能）────────────────────────
    html += '<div class="ls-graph-detail-section"><b>隣接</b>' +
      '<div class="ls-graph-detail-neighbor-group"><div class="ls-graph-detail-neighbor-head">始点</div>' +
      '<div class="ls-graph-ref-chips">' + lsGraphRefChipHtml(srcRef) + '</div></div>' +
      '<div class="ls-graph-detail-neighbor-group"><div class="ls-graph-detail-neighbor-head">終点</div>' +
      '<div class="ls-graph-ref-chips">' + lsGraphRefChipHtml(tgtRef) + '</div></div></div>';

    // 要確認事項
    var reasons = edge.review_reasons || [];
    if (backing || reasons.length) {
      html += '<div class="ls-graph-detail-section"><b>要確認事項</b>';
      if (backing) {
        html += '<p class="ls-graph-backing ls-graph-backing-' + escHtml(backing) + '">' +
          escHtml(lsGraphSourceBackingLabel(backing)) + '</p>';
      }
      if (reasons.length) {
        html += '<ul class="ls-graph-detail-reasons">';
        reasons.forEach(function (r) { html += '<li>' + escHtml(lsGraphReviewReasonLabel(r)) + '</li>'; });
        html += '</ul>';
      }
      html += '</div>';
    }

    // システム情報（折りたたみ。内部 relation 名はここにのみ残す — issue #448）
    var confidence = edge.confidence != null ? String(edge.confidence) : "";
    html += '<div class="ls-graph-detail-section ls-graph-detail-system"><details><summary>システム情報</summary>' +
      '<div class="ls-graph-detail-link"><b>edge ID</b> <code>' + escHtml(edge.edge_id || "") + '</code></div>' +
      '<div class="ls-graph-detail-link"><b>内部relation</b> <code>' + escHtml(edge.edge_type || edge.relation || "") + '</code></div>' +
      (confidence ? '<div class="ls-graph-detail-link"><b>確信度</b> ' + escHtml(confidence) + '</div>' : '') +
      '</details></div>';

    detail.innerHTML = html;
    lsGraphBindDetailNav(detail);
  }

  function lsRenderGraphFallback(container, graph) {
    var nodes = graph.nodes || [];
    var edges = graph.edges || [];
    var nodeById = {};
    nodes.forEach(function (node) { nodeById[lsGraphNodeId(node)] = node; });
    var html = '<div class="ls-empty-state">ネットワーク描画ライブラリを読み込めませんでした。テキスト表示に切り替えます。</div><div class="ls-graph-flow">';
    nodes.forEach(function (node) {
      var fallbackAnchor = node && node.is_thesis_anchor ? "★ " : "";
      html += '<div class="ls-graph-node' + (node && node.is_thesis_anchor ? ' ls-graph-node-anchor' : '') + '">' + escHtml(fallbackAnchor + (lsGraphSemanticLabel(node) || lsGraphNodeId(node))) + '<div class="ls-theory-muted">' + escHtml(node.origin || "paper") + ' / ' + escHtml(lsGraphRoleLabel(node)) + '</div></div>';
    });
    edges.forEach(function (edge) {
      var sourceId = edge.source_component_id || edge.source || edge.from;
      var targetId = edge.target_component_id || edge.target || edge.to;
      var source = nodeById[sourceId] || {};
      var target = nodeById[targetId] || {};
      html += '<details class="ls-graph-edge"><summary><span class="ls-graph-node-inline">' + escHtml(lsGraphSemanticLabel(source) || sourceId || "") + '</span>' +
        '<span class="ls-graph-edge-label"> -- ' + escHtml(edge.relation || edge.edge_type || edge.type || "RELATED_TO") + ' → </span>' +
        '<span class="ls-graph-node-inline">' + escHtml(lsGraphSemanticLabel(target) || targetId || "") + '</span></summary></details>';
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

  // Issue #447: a node's primary label must contain only human-readable
  // semantic terms — never internal identifiers (equation IDs, parser/operation
  // node IDs, claim IDs, etc.). These are domain-agnostic machine tokens:
  // snake_case runs that carry a digit (eq_2_7, theory_op_0001, claim_12),
  // "eq.(2.7)"-style equation references, or bare dotted/parenthesised numerics.
  function lsGraphLooksLikeInternalId(token) {
    var t = String(token || "").replace(/^[(\[]+|[)\].,;:]+$/g, "").trim();
    if (!t) return false;
    // snake_case machine id carrying a digit: eq_2_7 / theory_op_0001 / claim_12.
    if (t.indexOf("_") >= 0 && /\d/.test(t) && !/\s/.test(t)) return true;
    // equation reference: eq2, eq.2.7, eq(2.7), eq 2.7.
    if (/^eq[\s._-]*\(?\d/i.test(t)) return true;
    // dotted / parenthesised numeric ref: (2.7), 2.7, (3).
    if (/^\(?\d+(?:[._]\d+)+\)?$/.test(t)) return true;
    if (/^\(\d+\)$/.test(t)) return true;
    return false;
  }

  // Issue #447: remove internal-ID tokens (and the separators left dangling
  // around them, e.g. "Derive result — eq_2_7 → eq_2_9") so labels stay readable.
  function lsGraphStripInternalIds(text) {
    var s = String(text || "");
    if (!s.trim()) return "";
    // Pre-remove inline equation references that may be glued to other words.
    s = s.replace(/\beq[\s._-]*\(?\d+(?:[._]\d+)*\)?/gi, " ");
    var SEP = /^[—–→←\-<>|,:;./]+$/; // — – → ← - etc.
    var kept = [];
    s.split(/\s+/).forEach(function (tok) {
      if (!tok || lsGraphLooksLikeInternalId(tok)) return;
      kept.push(tok);
    });
    var out = [];
    kept.forEach(function (tok) {
      var isSep = SEP.test(tok);
      if (isSep && (out.length === 0 || SEP.test(out[out.length - 1]))) return;
      out.push(tok);
    });
    while (out.length && SEP.test(out[out.length - 1])) out.pop();
    // Trim separator punctuation left dangling on a word after ID removal
    // ("Theory basis: " once the equation ID is gone -> "Theory basis").
    return out.join(" ")
      .replace(/\s{2,}/g, " ")
      .replace(/^[\s—–→←<>|,:;]+|[\s—–→←<>|,:;]+$/g, "")
      .trim();
  }

  // "derive_result" -> "Derive result" so the English->Japanese verb map matches.
  function lsGraphHumanizeOperation(op) {
    var s = String(op || "").replace(/_/g, " ").trim();
    if (!s) return "";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  // Issue #447: concise operation label = readable verb + readable target,
  // sourced from semantic fields (operation + theory_object), never from IDs.
  function lsGraphOperationSemanticLabel(node) {
    var operation = String((node && node.operation) || "").trim();
    if (!operation) return "";
    var verb = lsGraphStripInternalIds(lsGraphOperationLabelJa(lsGraphHumanizeOperation(operation)));
    var target = lsGraphStripInternalIds(String((node && node.theory_object) || "").trim());
    if (verb && target && target.toLowerCase() !== verb.toLowerCase()) {
      return verb + ": " + target;
    }
    return verb || target || "";
  }

  // Issue #447: resolve a node's human-readable label without ever leaking an
  // internal ID. Priority: visual_label -> display_label -> operation semantic
  // -> stage/theory_object -> stage -> generic. Every branch is ID-stripped.
  function lsGraphSemanticLabel(node) {
    if (!node) return "";
    var cand;
    var visualLabel = String(node.visual_label || "").trim();
    if (visualLabel) {
      cand = lsGraphStripInternalIds(lsGraphOperationLabelJa(visualLabel));
      if (cand) return cand;
    }
    var explicit = String(node.display_label || "").trim();
    if (explicit) {
      cand = lsGraphStripInternalIds(explicit);
      if (cand) return cand;
    }
    cand = lsGraphOperationSemanticLabel(node);
    if (cand) return cand;
    cand = lsGraphStripInternalIds(lsGraphFullDisplayLabel(node));
    if (cand) return cand;
    cand = lsGraphStripInternalIds(lsGraphMainStageLabel(node));
    if (cand) return cand;
    cand = lsGraphStripInternalIds(lsGraphOperationLabelJa(lsGraphHumanizeOperation(node.operation)));
    return cand || "（無題）";
  }

  // Prefix a warning icon for fallback / inferred nodes so they are not mistaken
  // for confirmed, source-backed theory operations (issue #302).
  // Issue #337: prefer visual_label (short), translate to Japanese.
  // Issue #447: route through lsGraphSemanticLabel so no internal ID can leak.
  function lsGraphNodeDisplayLabel(node, id) {
    var label = lsWrapGraphLabel(lsGraphSemanticLabel(node) || id);
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
    var ctype = String((node && (node.component_type || node.type)) || "");
    // Issue #447: shorten "<Stage>: <long text>" -> "<Stage>" for ALL operation
    // nodes (TheoryOperationNode / EquationOperationNode, or any node carrying an
    // operation), not just the main layer.
    var isOperationNode = /OperationNode$/.test(ctype) ||
      String((node && node.operation) || "").trim() !== "";
    if (!isOperationNode) return label;
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
    // Issue #448: show the user-facing role name, never the internal *Node type.
    parts.push(lsGraphRoleLabel(node));
    if (node.is_thesis_anchor) parts.push("★ 主張の到達点");
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

  // Issue #451: normalised edge polarity from the `polarity` field (not the label).
  // "-" = inhibits/negates, "+" = promotes/holds, "" = non-polar.
  function lsGraphEdgePolarity(edge) {
    var p = String((edge && edge.polarity) || "").trim();
    if (p === "-") return "-";
    if (p === "+") return "+";
    return "";
  }

  function lsGraphEdgeDashed(edge) {
    var status = String((edge && edge.support_status) || "").toLowerCase();
    var relation = String((edge && (edge.relation || edge.edge_type || edge.type)) || "").toLowerCase();
    var review = String((edge && edge.review_status) || "").toLowerCase();
    var backing = String((edge && edge.source_backing_status) || "").toLowerCase();
    // Negative polarity is always rendered dashed so it reads differently from
    // positive / non-polar edges (issue #451).
    if (lsGraphEdgePolarity(edge) === "-") return true;
    if (review === "review_required") return true;
    if (backing === "review_required" || backing === "inferred") return true;
    return /llm|related|qualifies|conflicts|inhibits|uncertain/.test(status + " " + relation);
  }

  function lsGraphEdgeColor(edge) {
    // Issue #451: negative-polarity edges take a distinct crimson hue so they are
    // visually separable from positive / non-polar relations, based on the field.
    if (lsGraphEdgePolarity(edge) === "-") return "#dc2626";
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

  // === 承認・共有レイヤー(C層) UI ==========================================

  function lsEnsureEndorseStyles() {
    if (document.getElementById("ls-endorse-styles")) return;
    var style = document.createElement("style");
    style.id = "ls-endorse-styles";
    style.textContent =
      ".ls-endorse-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9999;display:flex;align-items:flex-start;justify-content:center;overflow:auto;padding:32px 12px;}" +
      ".ls-endorse-modal{background:#fff;max-width:820px;width:100%;border-radius:8px;padding:20px;box-shadow:0 8px 32px rgba(0,0,0,.3);}" +
      ".ls-endorse-modal h3{margin:0 0 4px;} .ls-endorse-modal .ls-endorse-sub{color:#666;font-size:12px;margin-bottom:12px;}" +
      ".ls-exp-card{border:1px solid #ddd;border-radius:6px;padding:12px;margin-bottom:10px;}" +
      ".ls-exp-card.kind-standard{border-left:4px solid #3b82f6;} .ls-exp-card.kind-personal{border-left:4px solid #10b981;}" +
      ".ls-exp-head{display:flex;justify-content:space-between;align-items:center;gap:8px;}" +
      ".ls-exp-badge{font-size:11px;padding:2px 8px;border-radius:10px;background:#eef;}" +
      ".ls-exp-label{font-size:12px;color:#0a7;} .ls-exp-body{white-space:pre-wrap;margin:6px 0;font-size:13px;}" +
      ".ls-exp-controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px;}" +
      ".ls-exp-controls input,.ls-exp-controls select{font-size:12px;padding:3px 6px;}" +
      ".ls-endorse-form{border-top:1px solid #eee;margin-top:12px;padding-top:12px;}" +
      ".ls-endorse-form textarea,.ls-endorse-form input{width:100%;margin:3px 0;font-size:13px;padding:4px;box-sizing:border-box;}" +
      ".ls-endorse-status{font-size:12px;margin:6px 0;min-height:16px;}";
    document.head.appendChild(style);
  }

  function lsCloseEndorseModal() {
    var ov = document.getElementById("ls-endorse-overlay");
    if (ov && ov.parentNode) ov.parentNode.removeChild(ov);
  }

  function lsSetEndorseStatus(msg, kind) {
    var el = document.getElementById("ls-endorse-status");
    if (!el) return;
    el.textContent = msg || "";
    el.style.color = kind === "error" ? "#c00" : kind === "success" ? "#0a7" : "#666";
  }

  function lsExpLevelSelect(id) {
    return '<select data-exp-level="' + escHtml(id) + '">' +
      '<option value="provisional">暫定</option>' +
      '<option value="endorsed" selected>承認</option>' +
      '<option value="strong">強い承認</option>' +
      '</select>';
  }

  function lsRenderExplanationCard(exp) {
    var kindLabel = exp.kind === "standard" ? "標準の説明" : ("教員: " + (exp.author_name || "不明"));
    var reviewLabel = exp.review_status === "teacher_approved" ? "承認済み" :
      exp.review_status === "rejected" ? "却下" :
      exp.review_status === "needs_revision" ? "要修正" : "査読待ち";
    var backing = (exp.backing_claims || []).map(function (b) {
      var cid = b.claim_id || b;
      var confirmed = (b && b.confirmed) ? "✓" : "候補";
      return '<span class="ls-theory-ref">' + escHtml(String(cid)) + " [" + confirmed + "]</span>";
    }).join(" ");
    return '<div class="ls-exp-card kind-' + escHtml(exp.kind) + '" data-exp-id="' + escHtml(exp.id) + '">' +
      '<div class="ls-exp-head">' +
        '<div><span class="ls-exp-badge">' + escHtml(kindLabel) + '</span> <strong>' + escHtml(exp.title || "(無題)") + '</strong></div>' +
        '<span class="ls-exp-label">' + escHtml(exp.endorsement_label || "未承認") + '</span>' +
      '</div>' +
      '<div class="ls-exp-body">' + escHtml(exp.body || "") + '</div>' +
      '<div class="ls-theory-muted">状態: ' + escHtml(reviewLabel) + ' / 共有: ' + (exp.shared ? "ON" : "OFF") +
        ' / 引用: ' + escHtml(String(exp.citation_count || 0)) + (backing ? (' / 根拠: ' + backing) : "") + '</div>' +
      '<div class="ls-exp-controls">' +
        lsExpLevelSelect(exp.id) +
        '<input type="text" data-exp-tag="' + escHtml(exp.id) + '" placeholder="専門タグ (例: 統計物理)" />' +
        '<button class="admin-action-btn" data-exp-action="endorse">承認する</button>' +
        '<button class="admin-action-btn" data-exp-action="revoke">承認取消</button>' +
        '<button class="admin-action-btn" data-exp-action="approve">査読承認</button>' +
        '<button class="admin-action-btn" data-exp-action="share">' + (exp.shared ? "共有OFF" : "共有ON") + '</button>' +
        '<button class="admin-action-btn" data-exp-action="cite">別コースで引用</button>' +
      '</div>' +
      '</div>';
  }

  function lsBindExplanationActions(component) {
    var list = document.getElementById("ls-endorse-list");
    if (!list) return;
    list.querySelectorAll("[data-exp-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var card = this.closest(".ls-exp-card");
        var expId = card.getAttribute("data-exp-id");
        var action = this.getAttribute("data-exp-action");
        if (action === "endorse") {
          var level = (card.querySelector("[data-exp-level]") || {}).value || "endorsed";
          var tag = (card.querySelector("[data-exp-tag]") || {}).value || "";
          lsEndorseExplanation(component, expId, { level: level, expertise_tag: tag, note: "" });
        } else if (action === "revoke") {
          lsCallExplanation(component, "/admin/explanations/" + expId + "/endorse", "DELETE", null, "承認を取り消しました");
        } else if (action === "approve") {
          lsCallExplanation(component, "/admin/explanations/" + expId, "PATCH", { review_status: "teacher_approved" }, "査読承認しました");
        } else if (action === "share") {
          var turnOn = this.textContent.indexOf("ON") >= 0;
          lsCallExplanation(component, "/admin/explanations/" + expId, "PATCH", { shared: turnOn }, "共有設定を更新しました");
        } else if (action === "cite") {
          var target = window.prompt("引用先のコースID を入力してください");
          if (target) lsCallExplanation(component, "/admin/explanations/" + expId + "/cite", "POST", { citing_course_id: target }, "引用しました");
        }
      });
    });
  }

  function lsEndorseExplanation(component, expId, payload) {
    lsCallExplanation(component, "/admin/explanations/" + expId + "/endorse", "POST", payload, "承認しました");
  }

  function lsCallExplanation(component, path, method, body, okMsg) {
    lsSetEndorseStatus("処理中...", "info");
    var opts = { method: method };
    if (body) opts.body = JSON.stringify(body);
    apiFetch(path, opts)
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (b) { throw new Error(b && b.detail ? (typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail)) : "処理に失敗しました"); }, function () { throw new Error("処理に失敗しました"); });
        }
        return res.json();
      })
      .then(function () {
        lsSetEndorseStatus(okMsg || "完了しました", "success");
        lsReloadExplanations(component);
      })
      .catch(function (err) { lsSetEndorseStatus(err.message || "処理に失敗しました", "error"); });
  }

  function lsReloadExplanations(component) {
    var list = document.getElementById("ls-endorse-list");
    if (!list) return;
    apiFetch("/admin/theory-components/" + component.id + "/explanations")
      .then(function (res) { if (!res.ok) throw new Error("読み込みに失敗しました"); return res.json(); })
      .then(function (data) {
        var items = data || [];
        list.innerHTML = items.length ? items.map(lsRenderExplanationCard).join("") : '<div class="ls-empty-state">説明バージョンがありません。</div>';
        lsBindExplanationActions(component);
      })
      .catch(function (err) { list.innerHTML = '<div class="ls-empty-state">' + escHtml(err.message || "読み込みに失敗しました") + '</div>'; });
  }

  function lsAddExplanation(component) {
    var title = (document.getElementById("ls-new-exp-title") || {}).value || "";
    var body = (document.getElementById("ls-new-exp-body") || {}).value || "";
    if (!body.trim()) { lsSetEndorseStatus("説明本文を入力してください", "error"); return; }
    lsSetEndorseStatus("追加中...", "info");
    apiFetch("/admin/theory-components/" + component.id + "/explanations", {
      method: "POST",
      body: JSON.stringify({ title: title, body: body, shared: false }),
    })
      .then(function (res) { if (!res.ok) throw new Error("追加に失敗しました"); return res.json(); })
      .then(function () {
        document.getElementById("ls-new-exp-title").value = "";
        document.getElementById("ls-new-exp-body").value = "";
        lsSetEndorseStatus("独自解釈を追加しました", "success");
        lsReloadExplanations(component);
      })
      .catch(function (err) { lsSetEndorseStatus(err.message || "追加に失敗しました", "error"); });
  }

  function lsGenerateCandidatesFromQuery(component) {
    var question = (document.getElementById("ls-cand-question") || {}).value || "";
    var answer = (document.getElementById("ls-cand-answer") || {}).value || "";
    if (!answer.trim()) { lsSetEndorseStatus("AI回答本文を入力してください", "error"); return; }
    lsSetEndorseStatus("候補生成中...(AIが理論・概念とclaim候補を抽出します)", "info");
    apiFetch("/admin/theory-components/candidates/from-query", {
      method: "POST",
      body: JSON.stringify({ course_id: component.course_id, question: question, answer_text: answer }),
    })
      .then(function (res) { if (!res.ok) throw new Error("候補生成に失敗しました"); return res.json(); })
      .then(function (data) {
        var n = (data.candidates || []).length;
        lsSetEndorseStatus(n + " 件の候補を生成しました(査読待ち)。claim紐づけは教員が確定してください。", "success");
      })
      .catch(function (err) { lsSetEndorseStatus(err.message || "候補生成に失敗しました", "error"); });
  }

  function lsOpenEndorsementModal(component) {
    lsEnsureEndorseStyles();
    lsCloseEndorseModal();
    var overlay = document.createElement("div");
    overlay.id = "ls-endorse-overlay";
    overlay.className = "ls-endorse-overlay";
    overlay.innerHTML =
      '<div class="ls-endorse-modal">' +
        '<div class="ls-exp-head">' +
          '<h3>説明バージョンと承認 — ' + escHtml(component.name || "無題") + '</h3>' +
          '<button class="admin-action-btn" id="ls-endorse-close">閉じる</button>' +
        '</div>' +
        '<div class="ls-endorse-sub">承認は説明バージョン単位。承認の厚みは段階ラベルで表示(点数化しない)。</div>' +
        '<div id="ls-endorse-status" class="ls-endorse-status"></div>' +
        '<div id="ls-endorse-list"><div class="ls-empty-state">読み込み中...</div></div>' +
        '<div class="ls-endorse-form">' +
          '<strong>独自解釈を追加</strong>' +
          '<input type="text" id="ls-new-exp-title" placeholder="タイトル (例: 私ならこう説明する)" />' +
          '<textarea id="ls-new-exp-body" rows="3" placeholder="説明本文"></textarea>' +
          '<button class="admin-action-btn" id="ls-add-exp-btn">この説明を追加</button>' +
        '</div>' +
        '<div class="ls-endorse-form">' +
          '<strong>受講者質問からコンポーネント候補を生成 (AI候補→教員確定)</strong>' +
          '<input type="text" id="ls-cand-question" placeholder="受講者の質問" />' +
          '<textarea id="ls-cand-answer" rows="3" placeholder="AI回答本文"></textarea>' +
          '<button class="admin-action-btn" id="ls-gen-cand-btn">候補を生成</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    document.getElementById("ls-endorse-close").addEventListener("click", lsCloseEndorseModal);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) lsCloseEndorseModal(); });
    document.getElementById("ls-add-exp-btn").addEventListener("click", function () { lsAddExplanation(component); });
    document.getElementById("ls-gen-cand-btn").addEventListener("click", function () { lsGenerateCandidatesFromQuery(component); });
    lsReloadExplanations(component);
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
        // Keep an equation as long as ANY renderable form exists (latex,
        // reading, or raw text). Previously items without latex were dropped,
        // which left their `![[equation:...]]` embeds "未解決" (issue 未解決の数式).
        if (!item || (!item.latex && !item.plain_text && !item.raw_text)) return;
        formulas.push({
          id: item.equation_id || ("TOPIC_FORMULA_" + formulas.length),
          label: item.label || "",
          latex: item.latex || "",
          plain_text: item.plain_text || "",
          raw_text: item.raw_text || "",
          is_display: true,
        });
      });
    });
    return formulas;
  }

  // Render the best available representation of an equation embed: rendered math
  // when LaTeX exists, else its reading (plain_text), else the raw extracted text.
  // Returns "" only when nothing renderable is available (issue 未解決の数式).
  function lsEquationEmbedBody(formula) {
    if (!formula) return "";
    var latex = formula.latex || formula.summary || "";
    if (latex) return lsRenderKatex(latex, true);
    var plain = formula.plain_text || "";
    if (plain) return '<span class="ls-material-formula-plain">' + escHtml(plain) + '</span>';
    var raw = formula.raw_text || "";
    if (raw) return '<span class="ls-material-formula-raw" title="原文（未整形・要確認）">' + escHtml(raw) + '</span>';
    return "";
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

  // テキストが散文ではなく生の TeX 数式かどうかのヒューリスティック判定。
  // 数式環境（\begin{aligned} 等）があれば即 TeX、それ以外は TeX コマンドの
  // 占有率で判定する（散文に数式コマンドが少し混ざる程度では true にしない）。
  // course_content_builder.py の _looks_like_tex_math と同じ基準を使う。
  function lsLooksLikeTexMath(text) {
    var t = String(text || "").trim();
    if (!t) return false;
    if (/\\begin\{[A-Za-z]+\*?\}/.test(t)) return true;
    var commands = t.match(/\\[a-zA-Z]+/g) || [];
    if (commands.length < 2) return false;
    var covered = 0;
    for (var i = 0; i < commands.length; i++) covered += commands[i].length + 1;
    return covered >= Math.max(10, Math.floor(t.length * 0.2));
  }

  // ── レクチャースライド同期 (migration 040 Phase 3) ──────────────────────
  // スライド = 表示と音声の同期最小単位。分割ロジックは backend core/lecture.py の
  // split_slides() を唯一の正本とする（Tier2-11: 講義系の判定共通化 提案11）。
  // 以前はここに split_slides() と同じ意味論をクライアント側で再現するローカル実装
  // （マーカー正規表現・空セグメント除去・分割数不一致時の1枚縮退）があったが、
  // 「プレビューと配信で分割ロジックを共有すること」という設計原則
  // （docs/features/lecture_slide_sync_design.md）に反する手動同期の並行実装だった
  // ため廃止し、教員ゲート付きプレビュー専用エンドポイントに一本化した。
  //
  // (displayText, spokenText, formulas) -> Promise<{ slides: [{slide_index, display, spoken}],
  //   mismatch, displayCount, spokenCount, error }>
  function lsFetchSplitSlides(displayText, spokenText, formulas) {
    return apiFetch("/admin/lecture-studio/preview-split", {
      method: "POST",
      body: JSON.stringify({
        display_text: displayText || "",
        spoken_text: spokenText || null,
        formulas: formulas || [],
      }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("preview-split failed: " + res.status);
        return res.json();
      })
      .then(function (data) {
        var slides = (data.slides || []).map(function (sd) {
          return { slide_index: sd.slide_index, display: sd.display_text, spoken: sd.spoken_text };
        });
        return {
          slides: slides,
          mismatch: !!data.mismatch,
          displayCount: data.display_segment_count || 0,
          spokenCount: data.spoken_segment_count || 0,
        };
      })
      .catch(function (err) {
        // fail-soft: 通信失敗時は「分割数不一致は1枚に縮退」の既定原則に合わせ、
        // クラッシュせず1スライド表示に縮退した上で警告を出す。
        console.warn("lsFetchSplitSlides failed, falling back to a single slide", err);
        return {
          slides: [{ slide_index: 0, display: displayText || "", spoken: spokenText || null }],
          mismatch: false,
          displayCount: 1,
          spokenCount: spokenText ? 1 : 0,
          error: true,
        };
      });
  }

  // display_text の実効長（長さ警告の判定用）。文字数 + [[FORMULA_N]] 1個 = 60文字換算。
  function lsSlideEffectiveLength(displayText) {
    var text = String(displayText || "");
    var placeholderRe = /\[\[FORMULA_\d+\]\]/g;
    var placeholders = text.match(placeholderRe) || [];
    var withoutPlaceholders = text.replace(placeholderRe, "");
    return withoutPlaceholders.length + placeholders.length * 60;
  }

  // 整合インジケータの見た目を1箇所に集約（チャンク編集用・コーストピック用で共有）。
  function lsRenderSlideIndicatorEl(el, split) {
    if (!el) return;
    el.classList.remove("ls-slides-indicator-ok", "ls-slides-indicator-warn");
    if (split.error) {
      el.textContent = "⚠ 分割プレビューの取得に失敗しました — 1枚に統合して表示しています";
      el.classList.add("ls-slides-indicator-warn");
    } else if (split.mismatch) {
      el.textContent = "⚠ 表示 " + split.displayCount + " / 読み上げ " + split.spokenCount +
        " — 1枚に統合して配信されます";
      el.classList.add("ls-slides-indicator-warn");
    } else if (split.spokenCount === 0) {
      el.textContent = "表示 " + split.displayCount + " 枚 / 読み上げ未入力";
    } else {
      el.textContent = "表示 " + split.displayCount + " 枚 / 読み上げ " + split.spokenCount + " 区切り ✓";
      el.classList.add("ls-slides-indicator-ok");
    }
  }

  // チャンク一覧 (§4 表示上の整合): slide_mismatch はバックエンド (get_course_scripts) が
  // 保存済みテキストから確定させた値をそのまま使う。一覧描画は多数チャンクを同期的に
  // 処理するため、ここでクライアント側の分割を再実行しない（Tier2-11: サーバ実装への
  // 一本化）。値が無い場合は「不一致なし」とみなすフェイルセーフ。
  function lsChunkSlideMismatch(chunk) {
    return chunk.slide_mismatch === true;
  }

  // 音声バッジ: 言語不一致 > 音声あり > 音声未生成 の優先順位で判定する。
  // audio_ready_slides はバックエンド未反映時は 0 とみなす（過大表示を避ける防御的デフォルト）。
  function lsSlideAudioBadgeHtml(chunk, slideIndex) {
    var courseLanguage = lsCourseLectureLanguage();
    var chunkLanguage = chunk.spoken_language || "ja";
    if (chunkLanguage !== courseLanguage) {
      return '<span class="ls-slide-badge ls-slide-badge-warn">言語不一致 ⚠</span>';
    }
    var ready = typeof chunk.audio_ready_slides === "number" ? chunk.audio_ready_slides : 0;
    if (ready > slideIndex) {
      return '<span class="ls-slide-badge ls-slide-badge-ready">音声あり (' + escHtml(chunkLanguage) + ')</span>';
    }
    return '<span class="ls-slide-badge ls-slide-badge-none">音声未生成</span>';
  }

  // スライド単位の試聴再生を停止する（カード/ボタンの見た目も元に戻す）。
  function lsStopSlideAudioPlayback() {
    var player = lsState.slideAudioPlayer;
    if (!player) return;
    try { player.audio.pause(); } catch (e) { /* noop */ }
    if (player.card) player.card.classList.remove("ls-slide-card-playing");
    if (player.btn) player.btn.textContent = "▶ 試聴";
    var statusEl = player.card ? player.card.querySelector(".ls-slide-play-status") : null;
    if (statusEl) statusEl.textContent = "";
    lsState.slideAudioPlayer = null;
  }

  // ▶ 試聴ボタン: GET /api/admin/chunks/{chunk_id}/lecture-audio（キャッシュ配信のみ・
  // 404 = 音声未生成）。同じスライドを再クリックすると停止する。
  function lsToggleSlideAudio(chunk, slideIndex, btnEl) {
    var card = btnEl.closest(".ls-slide-card");
    var statusEl = card ? card.querySelector(".ls-slide-play-status") : null;
    var current = lsState.slideAudioPlayer;
    if (current && current.chunkId === chunk.chunk_id && current.slideIndex === slideIndex) {
      lsStopSlideAudioPlayback();
      return;
    }
    lsStopSlideAudioPlayback();
    if (statusEl) statusEl.textContent = "読み込み中...";
    apiFetch("/admin/chunks/" + encodeURIComponent(chunk.chunk_id) + "/lecture-audio?slide_index=" + slideIndex + "&voice=alloy")
      .then(function (res) {
        if (res.status === 404) {
          var notFound = new Error("no_audio");
          notFound.noAudio = true;
          throw notFound;
        }
        if (!res.ok) throw new Error("failed");
        return res.json();
      })
      .then(function (data) {
        var audio = new Audio("data:" + (data.content_type || "audio/mpeg") + ";base64," + data.audio_base64);
        lsState.slideAudioPlayer = { chunkId: chunk.chunk_id, slideIndex: slideIndex, audio: audio, card: card, btn: btnEl };
        if (card) card.classList.add("ls-slide-card-playing");
        btnEl.textContent = "■ 停止";
        if (statusEl) statusEl.textContent = "再生中...";
        audio.addEventListener("ended", function () {
          lsStopSlideAudioPlayback();
        });
        audio.play();
      })
      .catch(function (err) {
        if (statusEl) statusEl.textContent = err && err.noAudio ? "音声未生成です" : "再生に失敗しました";
        lsShowActionStatus(
          err && err.noAudio ? "このスライドの音声はまだ生成されていません" : "音声の再生に失敗しました",
          err && err.noAudio ? "info" : "error"
        );
      });
  }

  // 「スライド」タブ本体: チャンクを split して1枚ずつカード描画する。
  // 表示テキストは受講画面と同じレンダラ lsRenderTextWithFormulas を再利用する。
  // 分割はサーバ（POST /admin/lecture-studio/preview-split）に問い合わせる非同期処理。
  // precomputedSplit を渡すと再フェッチしない（lsUpdateSlideIndicatorsNow が既に取得した
  // 結果を使い回し、同一テキストへの二重リクエストを避けるため）。
  function lsRenderSlidesPanel(chunk, precomputedSplit) {
    var indicatorEl = document.getElementById("ls-slides-indicator");
    var listEl = document.getElementById("ls-slides-list");
    if (!listEl) return;

    function renderWithSplit(split) {
      lsRenderSlideIndicatorEl(indicatorEl, split);

      var formulas = chunk.formulas || [];
      var html = "";
      split.slides.forEach(function (slide, idx) {
        var effLen = lsSlideEffectiveLength(slide.display);
        var lengthWarn = effLen > 600
          ? '<div class="ls-slide-warn">⚠ 受講画面で縮小表示されます。=== で分割を検討してください</div>'
          : "";
        var badge = lsSlideAudioBadgeHtml(chunk, idx);
        var notesHtml = slide.spoken
          ? lsRenderTextWithFormulas(slide.spoken, formulas)
          : '<span class="ls-course-muted">読み上げ原稿がありません</span>';
        html +=
          '<div class="ls-slide-card" data-slide-index="' + idx + '">' +
            '<div class="ls-slide-card-head">' +
              '<span class="ls-slide-card-index">スライド ' + (idx + 1) + ' / ' + split.slides.length + '</span>' +
              badge +
            '</div>' +
            '<div class="ls-slide-card-body">' + lsRenderTextWithFormulas(slide.display, formulas) + '</div>' +
            lengthWarn +
            '<div class="ls-slide-card-notes">' + notesHtml + '</div>' +
            '<div class="ls-slide-card-actions">' +
              '<button type="button" class="ls-slide-play-btn admin-action-btn" data-slide-index="' + idx + '">▶ 試聴</button>' +
              '<span class="ls-slide-play-status"></span>' +
            '</div>' +
          '</div>';
      });
      listEl.innerHTML = html || '<div class="ls-empty-state">スライドがありません</div>';
      listEl.querySelectorAll(".ls-slide-play-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var slideIndex = parseInt(this.getAttribute("data-slide-index"), 10) || 0;
          lsToggleSlideAudio(chunk, slideIndex, this);
        });
      });
    }

    if (precomputedSplit) {
      renderWithSplit(precomputedSplit);
      return;
    }

    var displayEl = document.getElementById("ls-display-text");
    var spokenEl = document.getElementById("ls-spoken-text");
    // displayEl/spokenEl の value は現在の編集内容の正本（hidden でも input リスナーで
    // chunk.display_text / spoken_text と常に同期している）。
    var displayText = (displayEl && displayEl.value) || chunk.display_text || chunk.text || "";
    var spokenText = (spokenEl && spokenEl.value) || chunk.spoken_text || "";
    listEl.innerHTML = '<div class="ls-empty-state">読み込み中...</div>';
    lsFetchSplitSlides(displayText, spokenText, chunk.formulas || []).then(renderWithSplit);
  }

  var lsSlideToolsLastFocus = "ls-display-text";
  var lsSlideIndicatorDebounceTimer = null;
  // サーバへの分割問い合わせは非同期になるため、連続入力時に古いレスポンスが後から
  // 返って新しい入力内容を上書きしないよう、リクエストごとに世代を振って捨てる。
  var lsSlideIndicatorRequestSeq = 0;

  function lsUpdateSlideIndicatorsNow() {
    var chunk = lsGetSelectedChunk();
    if (!chunk) return;
    var displayEl = document.getElementById("ls-display-text");
    var spokenEl = document.getElementById("ls-spoken-text");
    var displayText = (displayEl && displayEl.value) || chunk.display_text || chunk.text || "";
    var spokenText = (spokenEl && spokenEl.value) || chunk.spoken_text || "";
    var seq = ++lsSlideIndicatorRequestSeq;
    lsFetchSplitSlides(displayText, spokenText, chunk.formulas || []).then(function (split) {
      if (seq !== lsSlideIndicatorRequestSeq) return; // 古いレスポンスは無視
      lsRenderSlideIndicatorEl(document.getElementById("ls-slide-tools-indicator"), split);
      if (lsState.displayView === "slides") lsRenderSlidesPanel(chunk, split);
    });
  }

  // textarea の input イベントでライブ更新するが、描画は debounce 300ms にする（§4-1）。
  function lsUpdateSlideIndicatorsDebounced() {
    if (lsSlideIndicatorDebounceTimer) clearTimeout(lsSlideIndicatorDebounceTimer);
    lsSlideIndicatorDebounceTimer = setTimeout(function () {
      lsSlideIndicatorDebounceTimer = null;
      lsUpdateSlideIndicatorsNow();
    }, 300);
  }

  // 「スライド区切りを挿入」ボタン: 最後にフォーカスしていた textarea のカーソル位置に
  // \n===\n を挿入する。もう一方の textarea には挿入しない（対応位置の自動推定は不確実）。
  function lsInsertSlideMarkerIntoTextarea(el) {
    if (!el) return;
    var marker = "\n===\n";
    var start = typeof el.selectionStart === "number" ? el.selectionStart : (el.value || "").length;
    var end = typeof el.selectionEnd === "number" ? el.selectionEnd : start;
    var value = el.value || "";
    el.value = value.slice(0, start) + marker + value.slice(end);
    var newPos = start + marker.length;
    el.selectionStart = el.selectionEnd = newPos;
    el.focus();
    el.dispatchEvent(new Event("input"));
  }

  function lsInsertSlideMarker() {
    var id = lsSlideToolsLastFocus || "ls-display-text";
    var el = document.getElementById(id);
    if (!el || el.hidden || el.disabled) el = document.getElementById("ls-display-text");
    lsInsertSlideMarkerIntoTextarea(el);
    lsUpdateSlideIndicatorsNow();
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

  // 音声準備確認フロー (Issue #491) §2-3 条件4: 全対象チャンクに空でない読み上げ原稿
  // (spoken_text) があるか。API の spoken_text フィールドは display_text へフォールバック
  // するため信頼できないので、サーバ由来の status（"ungenerated" は原稿未生成）で判定する。
  function lsAllChunksHaveSpokenText() {
    var chunks = lsState.chunks || [];
    if (!chunks.length) return false;
    return chunks.every(function (c) {
      return c && c.status && c.status !== "ungenerated";
    });
  }

  // 音声準備確認フロー (Issue #491) §2-2/§2-3: 音声生成の準備状態を1つに集約して返す。
  // "no_course" / "loading" / "content_incomplete" / "script_incomplete" / "busy" / "ready"
  function lsAudioReadinessState() {
    if (!lsState.courseId) return "no_course";
    if (lsState.generating) return "busy";
    if (!lsState.scriptsLoaded || !lsState.structureLoaded) return "loading";
    if (!lsIsCourseContentComplete() || !(lsState.chunks && lsState.chunks.length)) return "content_incomplete";
    if (!lsAllChunksHaveSpokenText()) return "script_incomplete";
    return "ready";
  }

  // 音声準備確認フロー (Issue #491) §2-3: 上記条件をすべて満たすときだけ音声生成可能。
  function lsCanGenerateAudio() {
    return lsAudioReadinessState() === "ready";
  }

  // 音声準備確認フロー (Issue #491) §3-2: 未準備時に近傍へ出す「理由」と「次の操作」。
  function lsAudioReadinessGuidance(state) {
    switch (state) {
      case "no_course":
        return { reason: "音声化するコースを選択してください", action: "コースを選択" };
      case "loading":
        return { reason: "コースの音声準備状態を確認しています", action: "完了を待つ" };
      case "content_incomplete":
        return { reason: "スライドの準備が完了していません", action: "コース内容生成を行う" };
      case "script_incomplete":
        return { reason: "読み上げ原稿が未生成のスライドがあります", action: "原稿生成を行う" };
      default:
        return null;
    }
  }

  // 音声準備確認フロー (Issue #491) §3-2: コース設定メニュー内の音声生成ボタン近傍に
  // 理由と次の操作を描画する（生成可能・タスク進行中は非表示）。
  function lsRenderAudioReadinessNote() {
    var note = document.getElementById("ls-audio-readiness-note");
    if (!note) return;
    var reasonEl = document.getElementById("ls-audio-readiness-reason");
    var actionEl = document.getElementById("ls-audio-readiness-action");
    var state = lsAudioReadinessState();
    var guidance = lsAudioReadinessGuidance(state);
    if (!guidance) {
      // "ready"（生成可能）や "busy"（進捗は #ls-progress が表示）では案内を出さない。
      note.hidden = true;
      if (reasonEl) reasonEl.textContent = "";
      if (actionEl) actionEl.textContent = "";
      return;
    }
    if (reasonEl) reasonEl.textContent = guidance.reason;
    if (actionEl) actionEl.textContent = guidance.action ? "→ " + guidance.action : "";
    note.hidden = false;
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
    var busy = Boolean(lsState.generating);
    var ready = hasCourse && !busy;
    var state = lsCoursePipelineState();

    var moreMenuBtn = document.getElementById("ls-more-menu-btn");
    if (moreMenuBtn) {
      moreMenuBtn.disabled = !hasCourse;
      moreMenuBtn.classList.toggle("ls-menu-trigger-busy", busy);
    }

    // 音声準備確認フロー (Issue #491) §2-3: 音声生成の有効化は lsCanGenerateAudio() の
    // 全条件（コース選択・読み込み完了・コース内容完了・チャンク存在・全チャンクに
    // spoken_text・タスク非進行中）を満たすときのみ。理由と次の操作も近傍に描画する。
    lsSetMenuItemState("ls-audio-all-btn", lsCanGenerateAudio(), lsPipelineStepVisual("audio", state));
    lsRenderAudioReadinessNote();

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

  function lsBatchAudio(language) {
    // レクチャースライド同期 + 音声言語切替 (migration 040 Phase 3/4): 言語は
    // #ls-audio-lang-modal で選択された値（省略時は現在のコース設定言語）を送る。
    // バックエンドが language を未対応でも body に余分なフィールドがあるだけで無害。
    var selectedLanguage = language || lsCourseLectureLanguage();
    lsState.pipelineTask = { step: "audio", status: "running" };
    lsSetCourseTaskBusy(true);
    lsShowProgress("音声生成を開始しています...", "info");

    apiFetch("/admin/courses/" + lsState.courseId + "/lecture-audio/generate", {
      method: "POST",
      body: JSON.stringify({ language: selectedLanguage }),
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
          // レクチャースライド同期 + 音声言語切替 (migration 040 Phase 4): 言語切替時は
          // 「原稿再生成 → 音声生成」のチェーンが result_data.phase で進捗する
          // （フィールド未実装でも動くよう防御的に、無ければ従来表示のまま）。
          var phase = rd.phase;
          var phaseLabel = phase === "script" ? "原稿再生成中... " : "音声生成中... ";

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
            if (lsState.courseId) lsLoadSettings(lsState.courseId);
          } else if (task.status === "failed") {
            clearInterval(timer);
            lsState.pipelineTask = { step: "audio", status: "failed" };
            lsShowProgress("音声生成に失敗しました: " + (task.error_message || "不明なエラー"), "error");
            lsSetCourseTaskBusy(false);
          } else {
            // pending / processing
            lsShowProgress(
              phaseLabel + "(" + processed + " / " + totalChunks + " — " + progress + "%)",
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

  // ============================================================================
  // ↑↑↑ 移設ここまで ↑↑↑
  // ============================================================================

  // ── 公開 API ────────────────────────────────────────────────────────

  function init(options) {
    if (initialized) return;
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    deps.apiFetchRaw = options.apiFetchRaw || null;
    deps.escHtml = options.escHtml || null;
    deps.onTabActivate = options.onTabActivate || null;
    deps.state = options.state || null;
    state = deps.state;

    initLectureStudio();
    initialized = true;
  }

  // 教材管理タブのエクスポートボタンから呼ばれる（旧 lsOpenExportModal の公開ラッパ）。
  function openExportModal(context) {
    lsOpenExportModal(context);
  }

  // Admin Copilot の registerScreenContext("lecture-studio", ...) から呼ばれる。
  // lsState はモジュール読み込み時点で既定値を持つため、init() 前に呼ばれても安全に空を返す。
  function getScreenContext() {
    var chunks = (lsState && lsState.chunks) ? lsState.chunks : [];
    return {
      selection: {
        chunk_id: lsState ? lsState.selectedChunkId : null,
        course_id: lsState ? lsState.courseId : null
      },
      visible_entities: chunks.slice(0, 20).map(function (c) {
        return { type: "chunk", id: c.chunk_id, title: (c.display_text || "").slice(0, 40) };
      })
    };
  }

  window.LectureStudio = {
    init: init,
    openExportModal: openExportModal,
    getScreenContext: getScreenContext
  };
})();
