/*
 * LLM モデル選択（M層: Model Policy, migration 061）管理 UI。
 *
 * ES5 / IIFE。window.AdminLlmModels を公開。admin.js の initApp() から
 * AdminLlmModels.init({apiFetch, escHtml, onTabActivate, state, activateTabView}) を呼んで
 * 起動する（admin-manual-editor.js / admin-llm-usage.js と同型の DI 注入パターン）。
 *
 * 設計の正本: docs/features/llm_model_selection_design.md §6。
 *
 * バックエンド API（実装済み、backend/api/routes/llm_models.py）:
 *   GET    /api/admin/llm-models/catalog?scene=            — 選択肢 + 実効モデル（TEACHER 以上）
 *   PUT    /api/admin/llm-models/my-policies/{scene_key}    — 本人の既定を保存（TEACHER 以上）
 *   DELETE /api/admin/llm-models/my-policies/{scene_key}    — 本人の既定を解除
 *   GET    /api/admin/llm-models/policies                   — システム既定一覧（SYSTEM_ADMIN のみ）
 *   PUT    /api/admin/llm-models/policies/{scene_key}        — システム既定の変更（SYSTEM_ADMIN のみ）
 *   DELETE /api/admin/llm-models/policies/{scene_key}        — システム既定の解除（SYSTEM_ADMIN のみ）
 *
 * Phase 3（設計書 §6.2〜6.5）: AdminLlmModels.createModelChip(opts) が汎用モデルチップを
 * 提供する。コース構築チャット / 原稿スタジオ / 分野の地図 / 要素検討ワークスペースの
 * 各画面（admin.js / admin-lecture-studio.js / deliberation.js）から呼ばれる。
 *
 * 不変条項（本 UI での担保）:
 *   - M2 既定で完結する: サマリ行は常に実効モデル名を表示し、未選択のプレースホルダを作らない。
 *   - M3 表示は実モデル名: tier 名（fast/standard/deep/analysis）は一切表示しない。
 *   - M4 選択肢を捏造しない: カタログ未設定・取得失敗時は「選択肢が未設定です」の事実文のみで、
 *     架空の候補は並べない。[変更] は無効化する（fail-closed）。
 *   - M8 数値・金額を出さない: cost_hint は低/中/高のみ、$・円・USD は使わない。
 *   - システム既定の変更は影響範囲を明示した confirm を経由する。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null, escHtml: null, onTabActivate: null, state: null, activateTabView: null };
  var initialized = false;

  var COST_HINT_LABELS = { low: "低", medium: "中", high: "高" };

  // ---------------------------------------------------------------------
  // ユーティリティ
  // ---------------------------------------------------------------------

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    if (typeof fn !== "function") {
      // init(deps) 前に呼ばれた / グローバルが無い場合に同期 TypeError で呼び出し元
      // （initApp 等）を巻き込まないよう、必ず rejected Promise で返す（fail-closed）。
      return Promise.reject(new Error("AdminLlmModels: apiFetch is not available (init() not called yet)"));
    }
    return fn(path, opts);
  }

  function escHtml(value) {
    if (deps.escHtml) return deps.escHtml(value);
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

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

  function _costLabel(hint) {
    if (!hint) return null;
    return COST_HINT_LABELS[hint] || hint;
  }

  function _validModelForScene(scene, model) {
    if (!scene || !scene.options || !model) return false;
    for (var i = 0; i < scene.options.length; i++) {
      if (scene.options[i].id === model) return true;
    }
    return false;
  }

  function _modelsEqual(a, b) {
    var keysA = Object.keys(a || {});
    var keysB = Object.keys(b || {});
    if (keysA.length !== keysB.length) return false;
    for (var i = 0; i < keysA.length; i++) {
      var k = keysA[i];
      if (a[k] !== b[k]) return false;
    }
    return true;
  }

  function _hasKeys(obj) {
    for (var k in obj) { if (obj.hasOwnProperty(k)) return true; }
    return false;
  }

  // ラジオ/セレクトの選択肢 HTML を組み立てる。カタログ options に無い値は並べない（M4）。
  function _buildRadioOptionsHtml(scene, groupName) {
    var opts = (scene && scene.options) || [];
    var html = "";
    opts.forEach(function (opt) {
      var metaParts = [];
      if (opt.is_current) metaParts.push("既定");
      var costLabel = _costLabel(opt.cost_hint);
      if (costLabel) metaParts.push("コスト目安 " + costLabel);
      var metaText = metaParts.length ? ("　" + metaParts.join(" ・ ")) : "";
      html += '<label style="display:block;margin-bottom:4px;font-size:12.5px;color:var(--color-text-secondary)">' +
        '<input type="radio" name="' + escHtml(groupName) + '" value="' + escHtml(opt.id) + '"' +
        (opt.is_current ? " checked" : "") + '> ' +
        escHtml(opt.id) + metaText +
        "</label>";
    });
    return html;
  }

  function _buildSelectOptionsHtml(scene) {
    var opts = (scene && scene.options) || [];
    var html = "";
    opts.forEach(function (opt) {
      html += '<option value="' + escHtml(opt.id) + '"' + (opt.is_current ? " selected" : "") + '>' +
        escHtml(opt.id) + "</option>";
    });
    return html;
  }

  // 「継承」を先頭に持つステージ別 select 用: is_current による selected 付与をしない
  // （先頭の「継承（現在の実効モデル）」だけを既定選択にするため二重 selected を避ける）。
  function _buildSelectOptionsHtmlPlain(scene) {
    var opts = (scene && scene.options) || [];
    var html = "";
    opts.forEach(function (opt) {
      html += '<option value="' + escHtml(opt.id) + '">' + escHtml(opt.id) + "</option>";
    });
    return html;
  }

  function _unavailableNoteHtml() {
    return '<div style="font-size:12px;color:var(--color-text-tertiary)">選択肢が未設定です（モデルカタログ未設定）。</div>';
  }

  // ---------------------------------------------------------------------
  // 教材管理: 1行サマリ + 変更パネル（設計書 §6.1）
  // ---------------------------------------------------------------------

  var mtCatalog = { pipeline: null, vision: null };
  var mtCatalogAvailable = false;
  var mtPanelOpen = false;

  // ステージ別に指定する（詳細, 設計書 §6.1・Phase 4）。既定で閉じている（M2）。
  // 選択は run-only（その実行だけ）— scope='user' ポリシーとしては保存しない。
  var mtStagesOpen = false;
  var mtStagesLoaded = false;
  var mtStagesList = null;
  var mtStagesOverrides = {}; // feature("pipeline:<stage>") -> model id

  function _fetchCatalogPair() {
    return Promise.all([
      _fetchJson("/admin/llm-models/catalog?scene=pipeline"),
      _fetchJson("/admin/llm-models/catalog?scene=pipeline.vision")
    ]);
  }

  function loadMaterialsCatalog(onDone) {
    _fetchCatalogPair().then(function (results) {
      var pipelineData = results[0];
      var visionData = results[1];
      mtCatalogAvailable = !!(pipelineData && pipelineData.catalog_available);
      mtCatalog.pipeline = (pipelineData && pipelineData.scenes && pipelineData.scenes[0]) || null;
      mtCatalog.vision = (visionData && visionData.scenes && visionData.scenes[0]) || null;
      renderMaterialsSummary();
      if (typeof onDone === "function") onDone();
    }).catch(function () {
      renderMaterialsSummaryError();
    });
  }

  // [変更] を無効化するときは必ず理由を併記する（無効なボタンだけを見せて理由を伏せない）。
  function _setMaterialsSummaryNote(text) {
    var noteEl = document.getElementById("llm-model-summary-note");
    if (!noteEl) return;
    if (text) {
      noteEl.textContent = text;
      noteEl.hidden = false;
    } else {
      noteEl.textContent = "";
      noteEl.hidden = true;
    }
  }

  function renderMaterialsSummary() {
    var valueEl = document.getElementById("llm-model-summary-value");
    var btn = document.getElementById("llm-model-change-btn");
    var scene = mtCatalog.pipeline;
    if (!scene) {
      if (valueEl) valueEl.textContent = "取得できませんでした";
      if (btn) btn.disabled = true;
      _setMaterialsSummaryNote("選択肢を取得できませんでした。");
      return;
    }
    if (valueEl) {
      valueEl.textContent = scene.effective.model + "（" + scene.effective.source_label + "）";
    }
    if (btn) btn.disabled = !mtCatalogAvailable;
    _setMaterialsSummaryNote(
      mtCatalogAvailable ? "" : "選択肢が未設定です（モデルカタログ未設定）。"
    );
  }

  function renderMaterialsSummaryError() {
    var valueEl = document.getElementById("llm-model-summary-value");
    var btn = document.getElementById("llm-model-change-btn");
    if (valueEl) valueEl.textContent = "取得に失敗しました";
    if (btn) btn.disabled = true;
    _setMaterialsSummaryNote("選択肢を取得できませんでした。");
  }

  function renderMaterialsPanel() {
    var panel = document.getElementById("llm-model-panel");
    if (!panel) return;
    if (!mtCatalogAvailable) {
      panel.innerHTML = _unavailableNoteHtml();
      return;
    }
    var pipelineScene = mtCatalog.pipeline;
    var visionScene = mtCatalog.vision;
    var analyzeImagesEl = document.getElementById("upload-analyze-images");
    var showVision = !!(analyzeImagesEl && analyzeImagesEl.checked);

    var html = '<div style="border:1px solid var(--color-border);border-radius:6px;padding:10px;margin-top:8px">' +
      '<div style="font-size:12.5px;font-weight:600;margin-bottom:6px;color:var(--color-text-primary)">解析モデル</div>' +
      '<div id="llm-model-pipeline-options">' + _buildRadioOptionsHtml(pipelineScene, "llm-model-pipeline-radio") + "</div>";
    if (showVision) {
      html += '<div style="margin-top:8px;font-size:12.5px;color:var(--color-text-secondary)">図の解析（vision）: ' +
        '<select id="llm-model-vision-select">' + _buildSelectOptionsHtml(visionScene) + "</select></div>";
    }
    html += '<p style="font-size:11px;color:var(--color-text-tertiary);margin:8px 0 0">' +
      "ここで選んだモデルは、あなたのアップロード・再解析の既定として保存されます（他の教員には影響しません）。</p>" +
      '<div style="margin-top:10px">' +
        '<div id="llm-model-stages-toggle" style="cursor:pointer;font-size:12px;color:var(--color-text-secondary)">' +
          (mtStagesOpen ? "▾" : "▸") + " ステージ別に指定する（詳細）" +
        "</div>" +
        '<div id="llm-model-stages-body"' + (mtStagesOpen ? "" : " hidden") + "></div>" +
      "</div>" +
      '<div style="margin-top:8px;display:flex;gap:8px">' +
        '<button type="button" id="llm-model-apply-btn" class="admin-action-btn">この設定で解析する</button>' +
        '<button type="button" id="llm-model-reset-btn" class="admin-action-btn">既定に戻す</button>' +
      "</div>" +
      '<div id="llm-model-panel-status" style="font-size:12px;margin-top:6px;color:var(--color-text-secondary)"></div>' +
    "</div>";
    panel.innerHTML = html;

    var applyBtn = document.getElementById("llm-model-apply-btn");
    if (applyBtn) applyBtn.addEventListener("click", applyMaterialsSelection);
    var resetBtn = document.getElementById("llm-model-reset-btn");
    if (resetBtn) resetBtn.addEventListener("click", resetMaterialsSelection);
    var stagesToggle = document.getElementById("llm-model-stages-toggle");
    if (stagesToggle) stagesToggle.addEventListener("click", toggleMaterialsStages);
    if (mtStagesOpen) {
      if (mtStagesLoaded) {
        _renderMaterialsStagesBody();
      } else {
        _loadMaterialsStages();
      }
    }
  }

  // ステージ別に指定する（詳細）の開閉。既定で閉じている（M2）。
  function toggleMaterialsStages() {
    var toggleEl = document.getElementById("llm-model-stages-toggle");
    var bodyEl = document.getElementById("llm-model-stages-body");
    if (!bodyEl) return;
    mtStagesOpen = !mtStagesOpen;
    bodyEl.hidden = !mtStagesOpen;
    if (toggleEl) toggleEl.textContent = (mtStagesOpen ? "▾" : "▸") + " ステージ別に指定する（詳細）";
    if (mtStagesOpen) {
      if (mtStagesLoaded) {
        _renderMaterialsStagesBody();
      } else {
        _loadMaterialsStages();
      }
    }
  }

  function _loadMaterialsStages() {
    var bodyEl = document.getElementById("llm-model-stages-body");
    if (bodyEl) bodyEl.innerHTML = '<div style="font-size:12px;color:var(--color-text-tertiary)">読み込み中...</div>';
    _fetchJson("/admin/llm-models/pipeline-stages").then(function (data) {
      mtStagesList = (data && data.stages) || [];
      mtStagesLoaded = true;
      _renderMaterialsStagesBody();
    }).catch(function () {
      var el = document.getElementById("llm-model-stages-body");
      if (el) el.innerHTML = '<div style="font-size:12px;color:var(--color-text-tertiary)">取得できませんでした。</div>';
    });
  }

  // ステージ別テーブル本体を描画する。各行の既定値は「継承（そのステージの実効モデル）」で、
  // 変更した行だけ mtStagesOverrides に積む（run-only。scope='user' には保存しない）。
  function _renderMaterialsStagesBody() {
    var bodyEl = document.getElementById("llm-model-stages-body");
    if (!bodyEl) return;
    if (!mtStagesList || !mtStagesList.length) {
      bodyEl.innerHTML = '<div style="font-size:12px;color:var(--color-text-tertiary)">ステージ情報がありません。</div>';
      return;
    }
    var html = '<table style="width:100%;font-size:12px;margin-top:4px;border-collapse:collapse"><tbody>';
    mtStagesList.forEach(function (stage) {
      var scene = stage.vision ? mtCatalog.vision : mtCatalog.pipeline;
      html += "<tr>" +
        '<td style="padding:2px 8px 2px 0;color:var(--color-text-secondary)">' + escHtml(stage.label) + "</td>" +
        '<td><select class="llm-model-stage-select" data-feature="' + escHtml(stage.feature) + '">' +
          '<option value="">継承（' + escHtml(stage.effective.model) + "）</option>" +
          _buildSelectOptionsHtmlPlain(scene) +
        "</select></td>" +
      "</tr>";
    });
    html += "</tbody></table>" +
      '<p style="font-size:11px;color:var(--color-text-tertiary);margin:6px 0 0">' +
        "ここでの変更は今回の実行だけに適用され、既定としては保存されません。</p>";
    bodyEl.innerHTML = html;

    var selects = bodyEl.querySelectorAll(".llm-model-stage-select");
    selects.forEach(function (sel) {
      var feature = sel.getAttribute("data-feature");
      if (mtStagesOverrides.hasOwnProperty(feature)) sel.value = mtStagesOverrides[feature];
      sel.addEventListener("change", function () {
        var f = this.getAttribute("data-feature");
        if (this.value) {
          mtStagesOverrides[f] = this.value;
        } else {
          delete mtStagesOverrides[f];
        }
      });
    });
  }

  function toggleMaterialsPanel() {
    var panel = document.getElementById("llm-model-panel");
    if (!panel) return;
    mtPanelOpen = !mtPanelOpen;
    if (mtPanelOpen) {
      renderMaterialsPanel();
      panel.hidden = false;
    } else {
      panel.hidden = true;
    }
  }

  function putMyPolicy(sceneKey, model) {
    return _fetchJson("/admin/llm-models/my-policies/" + encodeURIComponent(sceneKey), {
      method: "PUT",
      body: JSON.stringify({ model: model })
    });
  }

  function deleteMyPolicy(sceneKey) {
    return _fetchJson("/admin/llm-models/my-policies/" + encodeURIComponent(sceneKey), { method: "DELETE" })
      .catch(function (err) {
        if (err && err.status === 404) return null; // 既にシステム既定のまま
        throw err;
      });
  }

  function applyMaterialsSelection() {
    var statusEl = document.getElementById("llm-model-panel-status");
    var selectedRadio = document.querySelector('input[name="llm-model-pipeline-radio"]:checked');
    var pipelineModel = selectedRadio ? selectedRadio.value : null;
    var visionSelect = document.getElementById("llm-model-vision-select");
    var visionModel = visionSelect ? visionSelect.value : null;

    var tasks = [];
    if (pipelineModel && mtCatalog.pipeline && pipelineModel !== mtCatalog.pipeline.effective.model) {
      tasks.push(putMyPolicy("pipeline", pipelineModel));
    }
    if (visionSelect && visionModel && mtCatalog.vision && visionModel !== mtCatalog.vision.effective.model) {
      tasks.push(putMyPolicy("pipeline.vision", visionModel));
    }
    if (!tasks.length) {
      if (statusEl) statusEl.textContent = "変更はありません。";
      return;
    }
    if (statusEl) statusEl.textContent = "保存しています...";
    Promise.all(tasks).then(function () {
      loadMaterialsCatalog(function () {
        mtPanelOpen = false;
        var panel = document.getElementById("llm-model-panel");
        if (panel) panel.hidden = true;
      });
    }).catch(function () {
      if (statusEl) statusEl.textContent = "保存に失敗しました。";
    });
  }

  function resetMaterialsSelection() {
    var statusEl = document.getElementById("llm-model-panel-status");
    if (statusEl) statusEl.textContent = "既定に戻しています...";
    Promise.all([deleteMyPolicy("pipeline"), deleteMyPolicy("pipeline.vision")]).then(function () {
      mtStagesOverrides = {};
      loadMaterialsCatalog(function () {
        renderMaterialsPanel();
        var el = document.getElementById("llm-model-panel-status");
        if (el) el.textContent = "既定に戻しました。";
      });
    }).catch(function () {
      if (statusEl) statusEl.textContent = "既定に戻すのに失敗しました。";
    });
  }

  function initMaterialsPanel() {
    var summaryEl = document.getElementById("llm-model-summary");
    if (!summaryEl) return;
    var changeBtn = document.getElementById("llm-model-change-btn");
    if (changeBtn) changeBtn.addEventListener("click", toggleMaterialsPanel);
    var analyzeImagesEl = document.getElementById("upload-analyze-images");
    if (analyzeImagesEl) {
      analyzeImagesEl.addEventListener("change", function () {
        if (mtPanelOpen) renderMaterialsPanel();
      });
    }
    loadMaterialsCatalog();
  }

  // アップロード時に formData へ乗せる models dict を返す（カタログ外の値は含めない。422回避）。
  function getUploadModels(analyzeImages) {
    var models = {};
    if (mtCatalogAvailable && mtCatalog.pipeline &&
        _validModelForScene(mtCatalog.pipeline, mtCatalog.pipeline.effective.model)) {
      models.pipeline = mtCatalog.pipeline.effective.model;
    }
    if (analyzeImages && mtCatalogAvailable && mtCatalog.vision &&
        _validModelForScene(mtCatalog.vision, mtCatalog.vision.effective.model)) {
      models["pipeline.vision"] = mtCatalog.vision.effective.model;
    }
    // ステージ別に指定する（詳細）で変更した行だけ pipeline:<stage> キーとして乗せる
    // （run-only。§6.1「ステージ別の選択はその実行だけ」）。
    for (var feature in mtStagesOverrides) {
      if (mtStagesOverrides.hasOwnProperty(feature)) {
        models[feature] = mtStagesOverrides[feature];
      }
    }
    return _hasKeys(models) ? models : null;
  }

  // ---------------------------------------------------------------------
  // 再解析モーダル: 前回値表示 + 簡易変更パネル（設計書 §6.1-B）
  // ---------------------------------------------------------------------

  var rzCatalog = { pipeline: null, vision: null };
  var rzCatalogAvailable = false;
  var rzPanelOpen = false;

  // lastOpts.models のうち pipeline:<stage> キー（ステージ別の指定）の件数を数える。
  // 再解析モーダルには編集 UI を付けない（前回値継承のまま送る。編集したい場合は
  // アップロード時に、というスコープ判断。設計書 §6.1-B）。
  function _stageOverrideCount(modelsObj) {
    var count = 0;
    for (var key in modelsObj) {
      if (modelsObj.hasOwnProperty(key) && key.indexOf("pipeline:") === 0) count++;
    }
    return count;
  }

  function initReanalyzePanel(containerEl, lastOpts) {
    if (!containerEl) return;
    rzPanelOpen = false;
    rzCatalog = { pipeline: null, vision: null };
    rzCatalogAvailable = false;
    var lastModel = lastOpts && lastOpts.models && lastOpts.models.pipeline;
    var lastModels = (lastOpts && lastOpts.models) || null;
    var stageCount = lastModels ? _stageOverrideCount(lastModels) : 0;

    containerEl.innerHTML =
      '<div id="llm-model-reanalyze-summary" style="font-size:12.5px;color:var(--color-text-secondary);margin:8px 0">' +
        "解析モデル: " +
        '<span id="llm-model-reanalyze-value">' +
        (lastModel ? escHtml(lastModel) + "（前回と同じ）" : "取得中...") +
        "</span> " +
        '<button type="button" id="llm-model-reanalyze-change-btn" class="admin-action-btn" style="padding:1px 8px;font-size:11px">変更</button>' +
      "</div>" +
      (stageCount > 0
        ? '<div id="llm-model-reanalyze-stage-note" style="font-size:11px;color:var(--color-text-tertiary);margin:0 0 8px">' +
            "ステージ別の指定があります（" + stageCount + "件・前回と同じ）。</div>"
        : "") +
      '<div id="llm-model-reanalyze-panel" hidden></div>';

    var changeBtn = document.getElementById("llm-model-reanalyze-change-btn");
    if (changeBtn) {
      changeBtn.addEventListener("click", function () {
        rzPanelOpen = !rzPanelOpen;
        var panel = document.getElementById("llm-model-reanalyze-panel");
        if (!panel) return;
        if (rzPanelOpen) {
          _renderReanalyzePanelBody();
          panel.hidden = false;
        } else {
          panel.hidden = true;
        }
      });
    }
    var analyzeImagesEl = document.getElementById("reanalyze-analyze-images");
    if (analyzeImagesEl) {
      analyzeImagesEl.addEventListener("change", function () {
        if (rzPanelOpen) _renderReanalyzePanelBody();
      });
    }

    _fetchCatalogPair().then(function (results) {
      var pipelineData = results[0];
      var visionData = results[1];
      rzCatalogAvailable = !!(pipelineData && pipelineData.catalog_available);
      rzCatalog.pipeline = (pipelineData && pipelineData.scenes && pipelineData.scenes[0]) || null;
      rzCatalog.vision = (visionData && visionData.scenes && visionData.scenes[0]) || null;
      var valueEl = document.getElementById("llm-model-reanalyze-value");
      if (valueEl && !lastModel && rzCatalog.pipeline) {
        valueEl.textContent = rzCatalog.pipeline.effective.model + "（" + rzCatalog.pipeline.effective.source_label + "）";
      }
      if (changeBtn) changeBtn.disabled = !rzCatalogAvailable;
    }).catch(function () {
      var valueEl = document.getElementById("llm-model-reanalyze-value");
      if (valueEl && !lastModel) valueEl.textContent = "取得できませんでした";
      if (changeBtn) changeBtn.disabled = true;
    });
  }

  function _renderReanalyzePanelBody() {
    var panel = document.getElementById("llm-model-reanalyze-panel");
    if (!panel) return;
    if (!rzCatalogAvailable) {
      panel.innerHTML = _unavailableNoteHtml();
      return;
    }
    var analyzeImagesEl = document.getElementById("reanalyze-analyze-images");
    var showVision = !!(analyzeImagesEl && analyzeImagesEl.checked);
    var html = '<div style="border:1px solid var(--color-border);border-radius:6px;padding:8px;margin-top:4px">' +
      '<select id="llm-model-reanalyze-select">' + _buildSelectOptionsHtml(rzCatalog.pipeline) + "</select>";
    if (showVision) {
      html += '<div style="margin-top:6px;font-size:12px;color:var(--color-text-secondary)">図の解析（vision）: ' +
        '<select id="llm-model-reanalyze-vision-select">' + _buildSelectOptionsHtml(rzCatalog.vision) + "</select></div>";
    }
    html += "</div>";
    panel.innerHTML = html;
  }

  // 再解析実行時に body へ含める models を返す（前回と同一なら null = 前回継承に任せる, M6）。
  function getReanalyzeModels(lastOpts, analyzeImages) {
    var models = {};
    var pipelineSelect = document.getElementById("llm-model-reanalyze-select");
    var pipelineModel = pipelineSelect ? pipelineSelect.value :
      (rzCatalog.pipeline && rzCatalog.pipeline.effective.model);
    if (pipelineModel && rzCatalogAvailable && _validModelForScene(rzCatalog.pipeline, pipelineModel)) {
      models.pipeline = pipelineModel;
    }
    if (analyzeImages) {
      var visionSelect = document.getElementById("llm-model-reanalyze-vision-select");
      var visionModel = visionSelect ? visionSelect.value :
        (rzCatalog.vision && rzCatalog.vision.effective.model);
      if (visionModel && rzCatalogAvailable && _validModelForScene(rzCatalog.vision, visionModel)) {
        models["pipeline.vision"] = visionModel;
      }
    }
    // ステージ別の指定（pipeline:<stage>）はこのモーダルで編集しないが、黙って消さない。
    // orchestrator は body.models を渡すと前回 run の options.models を丸ごと置き換える
    // ため（マージしない）、ここで引き継がないと再解析のたびにステージ別上書きが失われる。
    var prev = (lastOpts && lastOpts.models) || null;
    if (prev) {
      for (var key in prev) {
        if (prev.hasOwnProperty(key) && key.indexOf("pipeline:") === 0) {
          models[key] = prev[key];
        }
      }
    }

    if (!_hasKeys(models)) return null;
    if (prev && _modelsEqual(prev, models)) return null;
    return models;
  }

  // ---------------------------------------------------------------------
  // 運用タブ「AIモデル」（SYSTEM_ADMIN のみ、設計書 §6.6）
  // ---------------------------------------------------------------------

  var opsPolicies = [];
  var opsScenesMeta = [];
  // ステージ別の指定（設計書 §6.6「▸ ステージ別の指定（N件）」、Phase 4）。
  var opsStageSectionOpen = false;
  var opsStagesList = null; // GET /pipeline-stages のキャッシュ（「追加」導線用）

  function _policyRow(sceneKey) {
    for (var i = 0; i < opsPolicies.length; i++) {
      if (opsPolicies[i].scene_key === sceneKey) return opsPolicies[i];
    }
    return null;
  }

  function _featureLevelPolicies() {
    return opsPolicies.filter(function (p) { return p.is_feature_level; });
  }

  function _sceneMeta(sceneKey) {
    for (var i = 0; i < opsScenesMeta.length; i++) {
      if (opsScenesMeta[i].scene_key === sceneKey) return opsScenesMeta[i];
    }
    // ステージ別 feature キー（pipeline:<stage> 等）は opsScenesMeta（scene 単位）に
    // 無いので、既存の policy 行から label/current を組み立てて代用する
    // （openOpsChangeRow / doOpsSave をステージ別行にもそのまま流用するため）。
    var policyRow = _policyRow(sceneKey);
    if (policyRow) {
      return { scene_key: sceneKey, label: policyRow.label, current: { model: policyRow.model } };
    }
    return null;
  }

  function _goToUsageTab() {
    var fn = deps.activateTabView || window.activateTabView;
    if (typeof fn === "function") fn("llm-usage");
  }

  function buildOpsTab(panel) {
    panel.innerHTML =
      '<div class="admin-section">' +
        '<h3 class="admin-section-title">AIモデル（システム既定）</h3>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin-bottom:10px">' +
          "各場面の実行に使うモデルのシステム既定です。教員個人の既定はここでは変更できません" +
          "（各教員の画面でのみ設定・解除できます）。" +
        "</p>" +
        '<table class="admin-table" id="llm-models-ops-table">' +
          "<thead><tr><th>場面</th><th>現在のモデル</th><th>出所</th><th></th></tr></thead>" +
          '<tbody id="llm-models-ops-tbody"><tr><td colspan="4">読み込み中...</td></tr></tbody>' +
        "</table>" +
        '<div id="llm-models-ops-status" style="font-size:12px;margin-top:8px;color:var(--color-text-secondary)"></div>' +
        '<div id="llm-models-ops-stage-section" data-ui-anchor="llm-models.stage-overrides" hidden style="margin-top:16px">' +
          '<div id="llm-models-ops-stage-toggle" data-ui-anchor="llm-models.stage-overrides" style="cursor:pointer;font-size:12.5px;color:var(--color-text-secondary)"></div>' +
          '<div id="llm-models-ops-stage-body" data-ui-anchor="llm-models.stage-overrides" hidden style="margin-top:6px"></div>' +
        "</div>" +
        '<p style="font-size:12px;margin-top:14px">選択の結果は' +
          '<a href="#" id="llm-models-ops-usage-link" data-ui-anchor="llm-models.usage-link">「LLM使用量」タブ</a>で確認できます。</p>' +
      "</div>";

    var refreshLink = document.getElementById("llm-models-ops-usage-link");
    if (refreshLink) {
      refreshLink.addEventListener("click", function (e) {
        e.preventDefault();
        _goToUsageTab();
      });
    }
    // ステージ別の指定は 0 件のとき非表示（renderOpsStageSection が判定する。§6.6）。
    var stageToggle = document.getElementById("llm-models-ops-stage-toggle");
    if (stageToggle) {
      stageToggle.addEventListener("click", function () {
        opsStageSectionOpen = !opsStageSectionOpen;
        renderOpsStageSection();
      });
    }
  }

  function loadOpsTab() {
    var tbody = document.getElementById("llm-models-ops-tbody");
    if (tbody) tbody.innerHTML = '<tr><td colspan="4">読み込み中...</td></tr>';
    _fetchJson("/admin/llm-models/policies").then(function (data) {
      opsPolicies = data.policies || [];
      opsScenesMeta = data.scenes || [];
      renderOpsTable();
      renderOpsStageSection();
    }).catch(function () {
      if (tbody) {
        tbody.innerHTML = '<tr><td colspan="4" style="color:var(--color-text-danger)">読み込みに失敗しました</td></tr>';
      }
    });
  }

  function renderOpsTable() {
    var tbody = document.getElementById("llm-models-ops-tbody");
    if (!tbody) return;
    if (!opsScenesMeta.length) {
      tbody.innerHTML = '<tr><td colspan="4">場面がありません</td></tr>';
      return;
    }
    var html = "";
    opsScenesMeta.forEach(function (scene) {
      var current = scene.current || {};
      var hasSystemRow = false;
      opsPolicies.forEach(function (p) { if (p.scene_key === scene.scene_key) hasSystemRow = true; });
      html += '<tr data-scene-key="' + escHtml(scene.scene_key) + '">' +
        "<td>" + escHtml(scene.label) + "</td>" +
        "<td>" + escHtml(current.model || "") + "</td>" +
        "<td>" + escHtml(current.source_label || "") + "</td>" +
        "<td>" +
          '<button type="button" class="admin-action-btn llm-models-ops-change-btn" data-ui-anchor="llm-models.change-scene-model" data-scene-key="' +
            escHtml(scene.scene_key) + '" style="font-size:11px;padding:2px 8px">変更</button> ' +
          '<button type="button" class="admin-action-btn llm-models-ops-reset-btn" data-ui-anchor="llm-models.reset-scene-model" data-scene-key="' +
            escHtml(scene.scene_key) + '" style="font-size:11px;padding:2px 8px"' +
            (hasSystemRow ? "" : " disabled") + ">既定に戻す</button>" +
        "</td>" +
      "</tr>" +
      '<tr class="llm-models-ops-edit-row" data-scene-key-edit="' + escHtml(scene.scene_key) + '" hidden>' +
        '<td colspan="4"></td>' +
      "</tr>";
    });
    tbody.innerHTML = html;

    var changeBtns = tbody.querySelectorAll(".llm-models-ops-change-btn");
    changeBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        openOpsChangeRow(this.getAttribute("data-scene-key"));
      });
    });
    var resetBtns = tbody.querySelectorAll(".llm-models-ops-reset-btn");
    resetBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        doOpsReset(this.getAttribute("data-scene-key"));
      });
    });
  }

  function openOpsChangeRow(sceneKey) {
    var editRow = document.querySelector('tr.llm-models-ops-edit-row[data-scene-key-edit="' + sceneKey + '"]');
    if (!editRow) return;
    var wasOpen = editRow.hidden === false;
    document.querySelectorAll(".llm-models-ops-edit-row").forEach(function (r) { r.hidden = true; });
    if (wasOpen) return; // トグルで閉じる

    var meta = _sceneMeta(sceneKey);
    var cell = editRow.querySelector("td");
    cell.innerHTML = "読み込み中...";
    editRow.hidden = false;

    _fetchJson("/admin/llm-models/catalog?scene=" + encodeURIComponent(sceneKey)).then(function (data) {
      var scene = (data.scenes || [])[0];
      if (!scene || !data.catalog_available) {
        cell.innerHTML = _unavailableNoteHtml();
        return;
      }
      cell.innerHTML = '<select class="llm-models-ops-select">' + _buildSelectOptionsHtml(scene) + "</select>" +
        ' <button type="button" class="admin-action-btn llm-models-ops-save-btn" style="font-size:11px;padding:2px 8px">保存</button>' +
        ' <span class="llm-models-ops-row-status" style="font-size:11px;margin-left:6px;color:var(--color-text-secondary)"></span>';
      var saveBtn = cell.querySelector(".llm-models-ops-save-btn");
      var select = cell.querySelector(".llm-models-ops-select");
      var statusEl = cell.querySelector(".llm-models-ops-row-status");
      if (saveBtn && select) {
        saveBtn.addEventListener("click", function () {
          var oldModel = meta && meta.current ? meta.current.model : null;
          doOpsSave(sceneKey, meta ? meta.label : sceneKey, select.value, oldModel, statusEl);
        });
      }
    }).catch(function () {
      cell.innerHTML = '<div style="font-size:12px;color:var(--color-text-danger)">読み込みに失敗しました。</div>';
    });
  }

  // ---------------------------------------------------------------------
  // ステージ別の指定（設計書 §6.6「▸ ステージ別の指定（N件）」、Phase 4）
  //
  // is_feature_level な policy 行（pipeline:<stage> 等）が1件以上あるときだけ表示する
  // （0件ならセクション自体を出さない）。行の変更・解除は既存の openOpsChangeRow /
  // doOpsSave / doOpsReset をそのまま流用する（_sceneMeta が policy 行から
  // label/current を代用するため、システム既定表と同じ配線で動く）。
  // ---------------------------------------------------------------------

  function renderOpsStageSection() {
    var container = document.getElementById("llm-models-ops-stage-section");
    var toggle = document.getElementById("llm-models-ops-stage-toggle");
    var body = document.getElementById("llm-models-ops-stage-body");
    if (!container || !toggle || !body) return;
    var rows = _featureLevelPolicies();
    if (!rows.length) {
      container.hidden = true;
      return;
    }
    container.hidden = false;
    toggle.textContent = (opsStageSectionOpen ? "▾" : "▸") + " ステージ別の指定（" + rows.length + "件）";
    body.hidden = !opsStageSectionOpen;
    if (opsStageSectionOpen) _renderOpsStageBody(rows);
  }

  function _renderOpsStageBody(rows) {
    var body = document.getElementById("llm-models-ops-stage-body");
    if (!body) return;
    var html = '<table class="admin-table" id="llm-models-ops-stage-table">' +
      "<thead><tr><th>ステージ</th><th>現在のモデル</th><th></th></tr></thead><tbody>";
    rows.forEach(function (row) {
      html += '<tr data-scene-key="' + escHtml(row.scene_key) + '">' +
        "<td>" + escHtml(row.label) + "</td>" +
        "<td>" + escHtml(row.model) + "</td>" +
        "<td>" +
          '<button type="button" class="admin-action-btn llm-models-ops-change-btn" data-ui-anchor="llm-models.change-scene-model" data-scene-key="' +
            escHtml(row.scene_key) + '" style="font-size:11px;padding:2px 8px">変更</button> ' +
          '<button type="button" class="admin-action-btn llm-models-ops-reset-btn" data-ui-anchor="llm-models.reset-scene-model" data-scene-key="' +
            escHtml(row.scene_key) + '" style="font-size:11px;padding:2px 8px">解除</button>' +
        "</td>" +
      "</tr>" +
      '<tr class="llm-models-ops-edit-row" data-scene-key-edit="' + escHtml(row.scene_key) + '" hidden>' +
        '<td colspan="3"></td>' +
      "</tr>";
    });
    html += "</tbody></table>" +
      '<p data-ui-anchor="llm-models.add-stage-override" style="font-size:12px;margin-top:8px">' +
        '<span id="llm-models-ops-stage-add-toggle" data-ui-anchor="llm-models.add-stage-override" style="cursor:pointer;color:var(--color-text-secondary)">' +
          "ステージ別の既定を追加</span>" +
      "</p>" +
      '<div id="llm-models-ops-stage-add-body" hidden style="margin-top:6px"></div>';
    body.innerHTML = html;

    var changeBtns = body.querySelectorAll(".llm-models-ops-change-btn");
    changeBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        openOpsChangeRow(this.getAttribute("data-scene-key"));
      });
    });
    var resetBtns = body.querySelectorAll(".llm-models-ops-reset-btn");
    resetBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        doOpsReset(this.getAttribute("data-scene-key"));
      });
    });
    var addToggle = document.getElementById("llm-models-ops-stage-add-toggle");
    if (addToggle) addToggle.addEventListener("click", toggleOpsStageAdd);
  }

  // 「ステージ別の既定を追加」: すでに指定済みのステージは追加候補から除外する。
  function toggleOpsStageAdd() {
    var body = document.getElementById("llm-models-ops-stage-add-body");
    if (!body) return;
    var opening = body.hidden;
    body.hidden = !opening;
    if (!opening) return;
    if (opsStagesList) {
      _renderOpsStageAddBody();
    } else {
      body.innerHTML = "読み込み中...";
      _fetchJson("/admin/llm-models/pipeline-stages").then(function (data) {
        opsStagesList = (data && data.stages) || [];
        _renderOpsStageAddBody();
      }).catch(function () {
        var el = document.getElementById("llm-models-ops-stage-add-body");
        if (el) el.innerHTML = '<div style="font-size:12px;color:var(--color-text-tertiary)">取得できませんでした。</div>';
      });
    }
  }

  function _renderOpsStageAddBody() {
    var body = document.getElementById("llm-models-ops-stage-add-body");
    if (!body || !opsStagesList) return;
    var existing = {};
    _featureLevelPolicies().forEach(function (p) { existing[p.scene_key] = true; });
    var available = opsStagesList.filter(function (s) { return !existing[s.feature]; });
    if (!available.length) {
      body.innerHTML = '<div style="font-size:12px;color:var(--color-text-tertiary)">追加できるステージはありません（すべて指定済みです）。</div>';
      return;
    }
    var stageOptionsHtml = "";
    available.forEach(function (s) {
      stageOptionsHtml += '<option value="' + escHtml(s.feature) + '">' + escHtml(s.label) + "</option>";
    });
    body.innerHTML =
      '<select id="llm-models-ops-stage-add-select">' + stageOptionsHtml + "</select> " +
      '<span id="llm-models-ops-stage-add-model-wrap">読み込み中...</span> ' +
      '<button type="button" id="llm-models-ops-stage-add-save-btn" class="admin-action-btn" style="font-size:11px;padding:2px 8px">保存</button>' +
      '<span id="llm-models-ops-stage-add-status" style="font-size:11px;margin-left:6px;color:var(--color-text-secondary)"></span>';

    var stageSelect = document.getElementById("llm-models-ops-stage-add-select");

    function _loadStageAddModelOptions() {
      var feature = stageSelect.value;
      var wrap = document.getElementById("llm-models-ops-stage-add-model-wrap");
      if (wrap) wrap.textContent = "読み込み中...";
      _fetchJson("/admin/llm-models/catalog?scene=" + encodeURIComponent(feature)).then(function (data) {
        var scene = (data.scenes || [])[0];
        var w = document.getElementById("llm-models-ops-stage-add-model-wrap");
        if (!w) return;
        if (!scene || !data.catalog_available) {
          w.innerHTML = _unavailableNoteHtml();
          return;
        }
        w.innerHTML = '<select id="llm-models-ops-stage-add-model">' + _buildSelectOptionsHtml(scene) + "</select>";
      });
    }
    if (stageSelect) {
      stageSelect.addEventListener("change", _loadStageAddModelOptions);
      _loadStageAddModelOptions();
    }

    var saveBtn = document.getElementById("llm-models-ops-stage-add-save-btn");
    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        var feature = stageSelect.value;
        var modelSelect = document.getElementById("llm-models-ops-stage-add-model");
        var model = modelSelect ? modelSelect.value : null;
        var statusEl = document.getElementById("llm-models-ops-stage-add-status");
        if (!feature || !model) return;
        var stageMeta = null;
        available.forEach(function (s) { if (s.feature === feature) stageMeta = s; });
        doOpsSave(feature, stageMeta ? stageMeta.label : feature, model, null, statusEl);
      });
    }
  }

  // システム既定の変更は全ユーザーに影響するため、事実文の confirm を必ず経由する。
  function doOpsSave(sceneKey, sceneLabel, newModel, oldModel, statusEl) {
    var message = "システム既定を " + (oldModel || "(未設定)") + " → " + newModel +
      " に変更します。全ユーザーの既定に影響します。";
    if (!window.confirm(message)) return;
    if (statusEl) statusEl.textContent = "保存しています...";
    _fetchJson("/admin/llm-models/policies/" + encodeURIComponent(sceneKey), {
      method: "PUT",
      body: JSON.stringify({ model: newModel })
    }).then(function () {
      loadOpsTab();
    }).catch(function () {
      if (statusEl) statusEl.textContent = "保存に失敗しました。";
    });
  }

  function doOpsReset(sceneKey) {
    var statusEl = document.getElementById("llm-models-ops-status");
    _fetchJson("/admin/llm-models/policies/" + encodeURIComponent(sceneKey), { method: "DELETE" }).then(function () {
      loadOpsTab();
    }).catch(function (err) {
      if (err && err.status === 404) {
        if (statusEl) statusEl.textContent = "システム既定は未設定です。";
      } else if (statusEl) {
        statusEl.textContent = "解除に失敗しました。";
      }
    });
  }

  function initOpsTab() {
    var panel = document.getElementById("tab-llm-models");
    if (!panel) return;
    if (panel.getAttribute("data-llm-models-built") !== "1") {
      panel.setAttribute("data-llm-models-built", "1");
      buildOpsTab(panel);
    }
    loadOpsTab();
  }

  // ---------------------------------------------------------------------
  // 汎用モデルチップ（設計書 §6.2〜6.5）: createModelChip(opts)
  //
  // コース構築チャット / 原稿スタジオ / 分野の地図 / 要素検討ワークスペースなど、
  // 「実行を起こす瞬間の近傍に1つだけ選択を置く」場面向けの軽量チップ部品。
  // 教材管理の1行サマリ（§6.1、上記 initMaterialsPanel 系）とは異なり、選択は
  // scope='user' ポリシーとして永続化しない（呼び出し側が opts.onChange で受け取り、
  // セッション JSON・run options・コース設定など各画面の保存先に反映する）。
  //
  // opts:
  //   sceneKey    scene_key（例: "course_builder" / "atlas" / "deliberation" /
  //               "deliberation:vision"）
  //   mountEl     チップを描画する DOM 要素（innerHTML を専有する）
  //   onChange    function(model|null) — モデル選択時は model id、
  //               「既定に戻す」選択時は null で呼ばれる
  //   compact     true でひとまわり小さい見た目にする
  //   initialModel 初期状態で「選択済み」として扱う model id（例: コース設定で
  //               既に保存済みの値を出所付きで見せたい場合）。省略時はカタログの
  //               実効モデルに追従する（M2）。
  //   anchorId    指定時、インスペクト・モード用の data-ui-anchor をチップの外枠 +
  //               ボタンへ付与する（呼び出し元の manifest 上の anchor_id。省略可）。
  //
  // 戻り値: {getModel, getSelected, setModel(model|null), refresh()}
  // ---------------------------------------------------------------------

  var _sceneCatalogCache = {};

  function _fetchSceneCatalogCached(sceneKey) {
    if (!_sceneCatalogCache[sceneKey]) {
      _sceneCatalogCache[sceneKey] = _fetchJson("/admin/llm-models/catalog?scene=" + encodeURIComponent(sceneKey))
        .then(function (data) {
          return {
            available: !!(data && data.catalog_available),
            scene: (data && data.scenes && data.scenes[0]) || null
          };
        })
        .catch(function () {
          return { available: false, scene: null };
        });
    }
    return _sceneCatalogCache[sceneKey];
  }

  // scene のカタログキャッシュを破棄する（システム既定/自分の既定を変更した直後などに使う）。
  function invalidateSceneCatalogCache(sceneKey) {
    if (sceneKey) {
      delete _sceneCatalogCache[sceneKey];
    } else {
      _sceneCatalogCache = {};
    }
  }

  // 同時に開けるチップメニューは1つだけにする（複数チップが同一画面に並ぶため）。
  var _activeChipClose = null;
  var _chipDocClickBound = false;
  function _ensureChipDocClick() {
    if (_chipDocClickBound) return;
    _chipDocClickBound = true;
    document.addEventListener("click", function () {
      if (_activeChipClose) {
        var fn = _activeChipClose;
        _activeChipClose = null;
        fn();
      }
    });
  }

  function _chipMenuItemsHtml(scene, current) {
    var options = (scene && scene.options) || [];
    var html = "";
    options.forEach(function (opt) {
      var isSel = current ? (opt.id === current) : !!opt.is_current;
      var costLabel = _costLabel(opt.cost_hint);
      var metaParts = [];
      if (opt.is_current) metaParts.push("既定");
      if (costLabel) metaParts.push("コスト目安 " + costLabel);
      html += '<div class="llm-model-chip-item" data-model="' + escHtml(opt.id) + '" style="' +
        "padding:4px 8px;font-size:12px;border-radius:4px;cursor:pointer;white-space:nowrap;" +
        (isSel ? "background:var(--color-background-tertiary);font-weight:600;" : "") +
        'color:var(--color-text-primary)">' + escHtml(opt.id) +
        (metaParts.length
          ? '<span style="color:var(--color-text-tertiary);font-weight:normal">　' + metaParts.join(" ・ ") + "</span>"
          : "") +
        "</div>";
    });
    var resetLabel = (scene && scene.effective && scene.effective.source_label) || "既定";
    html += '<div class="llm-model-chip-item llm-model-chip-reset" data-model="" style="' +
      "padding:4px 8px;font-size:12px;border-top:1px solid var(--color-border-tertiary);margin-top:4px;" +
      'cursor:pointer;color:var(--color-text-secondary);white-space:nowrap">既定に戻す（' +
      escHtml(resetLabel) + "）</div>";
    return html;
  }

  function createModelChip(opts) {
    opts = opts || {};
    var sceneKey = opts.sceneKey;
    var mountEl = opts.mountEl;
    var onChange = typeof opts.onChange === "function" ? opts.onChange : function () {};
    var compact = !!opts.compact;
    var anchorId = opts.anchorId || null;
    if (!mountEl || !sceneKey) return null;

    _ensureChipDocClick();

    var selectedModel = opts.initialModel || null; // null = カタログの実効モデルに追従
    var catalogState = { available: false, scene: null };
    var loaded = false;
    var menuOpen = false;

    function effectiveModel() {
      return catalogState.scene ? catalogState.scene.effective.model : null;
    }

    function displayModel() {
      return selectedModel || effectiveModel();
    }

    function closeMenu() {
      if (!menuOpen) return;
      menuOpen = false;
      render();
    }

    function render() {
      var model = displayModel();
      var openable = loaded && catalogState.available && !!catalogState.scene;
      var padStyle = compact ? "padding:1px 6px;font-size:11px" : "padding:2px 9px;font-size:12.5px";
      var labelText = model ? escHtml(model) : (loaded ? "取得できません" : "…");

      var anchorAttr = anchorId ? (' data-ui-anchor="' + escHtml(anchorId) + '"') : "";
      var html = '<span class="llm-model-chip"' + anchorAttr + ' style="position:relative;display:inline-block">' +
        '<button type="button" class="llm-model-chip-btn"' + anchorAttr + ' style="' + padStyle +
          ";border:1px solid var(--color-border);border-radius:12px;background:var(--color-background-secondary);" +
          "color:var(--color-text-primary);cursor:" + (openable ? "pointer" : "default") + '"' +
          (openable ? "" : " disabled") + ">" +
          labelText + (openable ? " ▾" : "") +
        "</button>";
      if (menuOpen && openable) {
        html += '<div class="llm-model-chip-menu" style="position:absolute;top:100%;left:0;margin-top:4px;' +
          "background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:6px;" +
          'box-shadow:0 2px 8px rgba(0,0,0,0.2);padding:6px;z-index:500;min-width:190px">' +
          _chipMenuItemsHtml(catalogState.scene, selectedModel) +
        "</div>";
      }
      html += "</span>";
      mountEl.innerHTML = html;

      var btn = mountEl.querySelector(".llm-model-chip-btn");
      if (btn && openable) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          if (menuOpen) {
            _activeChipClose = null;
            menuOpen = false;
            render();
          } else {
            if (_activeChipClose) _activeChipClose();
            menuOpen = true;
            _activeChipClose = closeMenu;
            render();
          }
        });
      }
      if (menuOpen && openable) {
        var items = mountEl.querySelectorAll(".llm-model-chip-item");
        items.forEach(function (item) {
          item.addEventListener("click", function (e) {
            e.stopPropagation();
            var val = this.getAttribute("data-model") || null;
            _activeChipClose = null;
            menuOpen = false;
            selectedModel = val || null;
            render();
            onChange(selectedModel);
          });
        });
      }
    }

    render(); // 取得中表示（"…"）

    function loadCatalog() {
      loaded = false;
      _fetchSceneCatalogCached(sceneKey).then(function (result) {
        catalogState = result;
        loaded = true;
        render();
      });
    }
    loadCatalog();

    return {
      getModel: displayModel,
      getSelected: function () { return selectedModel; },
      setModel: function (model) { selectedModel = model || null; render(); },
      refresh: function () {
        invalidateSceneCatalogCache(sceneKey);
        loadCatalog();
      }
    };
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
    deps.activateTabView = options.activateTabView || null;

    if (deps.state && deps.state.role === "SYSTEM_ADMIN") {
      if (deps.onTabActivate) deps.onTabActivate("llm-models", initOpsTab);
    }
    initialized = true;
  }

  window.AdminLlmModels = {
    init: init,
    initMaterialsPanel: initMaterialsPanel,
    getUploadModels: getUploadModels,
    initReanalyzePanel: initReanalyzePanel,
    getReanalyzeModels: getReanalyzeModels,
    initOpsTab: initOpsTab,
    createModelChip: createModelChip,
    invalidateSceneCatalogCache: invalidateSceneCatalogCache
  };
})();
