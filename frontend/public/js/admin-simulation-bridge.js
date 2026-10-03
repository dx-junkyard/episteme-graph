/**
 * 検証シミュレーション（Simulation Bridge, SB層）管理 UI
 * 正本: docs/features/simulation_bridge_design.md（SB1〜SB10・§6 API・§7 UI）
 *
 * 教材管理「アップロード済み教材」の各行の「⋯」メニューから開くフルスクリーンモーダル。
 * 4 区画: ①理論モジュール → コード生成（候補）→ 確定 / 見送り
 *         ②実験機の配線（ナレッジライブラリの凍結済み部品を並べる。AI の改訂案は点線枠）
 *         ③実行（確定済みのコード・配線・観測系の主張・入力値）→ 比較の段階ラベル + 事実文 + 数値表
 *         ④台帳に候補として記帳 / 配線の改訂案を求める
 *
 * - ES5 / IIFE。window.SimulationBridge を公開。admin.js の initApp() から init({...}) で DI。
 * - モーダルの外枠はグラフレビューの CSS（graph-review-modal / -frame / -header）を再利用する。
 * - SB1: 一致は検証ではない。画面の冒頭に固定の事実文を常に出し、結果は台帳の「候補」としてのみ記帳する。
 * - SB6: 比較の段階ラベルはサーバが解決した日本語（run.result.labels）をそのまま描く。
 *   ラベルの対応表をこのファイルに持たない。数値は教員向けの数値表にだけ出す。
 * - ポーリングしない。すべて教員のボタン操作で 1 回ずつ取得・実行する（SB9）。
 * - 応答に欠けたフィールドがあっても例外にせず、事実文 1 行に縮退する。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null, escHtml: null, getToken: null };

  // 画面の冒頭に常に出す固定の事実文（core/simulation_bridge/schema.py::FACT_NOT_VERIFICATION と同文）。
  var FACT_NOT_VERIFICATION = "シミュレーションの一致は検証ではなく、分析の妥当性の候補にとどまります。";
  var SCREEN_CONTEXT_SCREEN = "simulation_bridge";
  var DEFAULT_INDICATOR_ID = "simulation.agreement";

  var PROGRAM_STATUS_LABELS = {
    candidate: "候補",
    confirmed: "確定",
    dismissed: "見送り",
    superseded: "置き換え済み",
  };
  var WIRING_STATUS_LABELS = { candidate: "候補", confirmed: "確定", dismissed: "見送り" };
  var RUN_STATUS_LABELS = { succeeded: "実行済み", failed: "失敗", rejected: "実行前に停止" };
  var CHANGE_OP_LABELS = {
    add: "追加",
    add_part: "追加",
    remove: "除去",
    remove_part: "除去",
    set_param: "パラメータ変更",
    param: "パラメータ変更",
  };
  var METRIC_LABELS = {
    relative_difference: "理論の出力と模擬観測の相対差",
    separation_ratio: "差とばらつきの比",
    reference_relative_difference: "論文の記載値との相対差",
  };

  var state = {
    documentId: null,
    title: "",
    overview: null,
    loadError: "",
    step: "generate", // "generate" | "wiring" | "run" | "ledger"
    model: null,
    modelChip: null,
    busy: {},
    notices: {}, // section -> {text, kind}
    builder: { name: "", parts: [] },
    runForm: null,
    runProgramId: "",
    runWiringId: "",
    selectedRunId: "",
    lastRun: null,
    proposal: null,
  };

  // -------------------------------------------------------------------------
  // ユーティリティ
  // -------------------------------------------------------------------------

  function esc(value) {
    if (deps.escHtml) return deps.escHtml(value == null ? "" : String(value));
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function arr(value) {
    return Object.prototype.toString.call(value) === "[object Array]" ? value : [];
  }

  function obj(value) {
    return value && typeof value === "object" && !(Object.prototype.toString.call(value) === "[object Array]")
      ? value : {};
  }

  function fmtValue(value) {
    if (value === null || value === undefined) return "—";
    if (typeof value === "number") return String(value);
    if (typeof value === "string") return value;
    try {
      var text = JSON.stringify(value);
      return text.length > 160 ? text.slice(0, 160) + "…" : text;
    } catch (e) {
      return String(value);
    }
  }

  function parseNumber(raw) {
    var text = String(raw == null ? "" : raw).trim();
    if (!text) return null;
    var n = Number(text);
    return isFinite(n) ? n : null;
  }

  function factsHtml(facts, cls) {
    var list = arr(facts).filter(function (f) { return f && typeof f === "string"; });
    if (!list.length) return "";
    return '<ul class="simulation-facts' + (cls ? " " + cls : "") + '">' +
      list.map(function (f) { return "<li>" + esc(f) + "</li>"; }).join("") +
      "</ul>";
  }

  function chip(text, cls) {
    if (!text) return "";
    return '<span class="simulation-chip' + (cls ? " " + cls : "") + '">' + esc(text) + "</span>";
  }

  function detailText(detail, fallback) {
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string" && detail.message) return detail.message;
      var facts = arr(detail.facts).filter(function (f) { return typeof f === "string"; });
      if (facts.length) return facts.join(" ");
      if (arr(detail).length) return "入力の形式を確認してください。";
    }
    return fallback;
  }

  // 応答をJSONに読み、失敗時は事実文の Error にする（例外の中身は教員向けの事実文のみ）。
  function readJson(res, fallback) {
    return res.json().catch(function () { return {}; }).then(function (data) {
      if (res.ok) return data || {};
      var msg;
      if (res.status === 429) {
        msg = detailText(data && data.detail, "本日の上限に達しました。明日以降にもう一度お試しください。");
      } else if (res.status === 422 || res.status === 409) {
        msg = detailText(data && data.detail, fallback);
      } else if (res.status === 403 || res.status === 404) {
        msg = detailText(data && data.detail, "この教材を操作する権限が無いか、教材が見つかりません。");
      } else {
        msg = fallback;
      }
      throw new Error(msg);
    });
  }

  function post(path, body) {
    return deps.apiFetch(path, { method: "POST", body: JSON.stringify(body || {}) });
  }

  function base() {
    return "/admin/simulation";
  }

  function docPath(suffix) {
    return base() + "/documents/" + encodeURIComponent(state.documentId) + suffix;
  }

  function setNotice(section, text, kind) {
    state.notices[section] = text ? { text: text, kind: kind || "info" } : null;
  }

  function noticeHtml(section) {
    var n = state.notices[section];
    if (!n || !n.text) return "";
    return '<div class="simulation-notice is-' + esc(n.kind) + '">' + esc(n.text) + "</div>";
  }

  function ov() {
    return state.overview || {};
  }

  function catalog() {
    return arr(ov().parts_catalog);
  }

  function catalogEntry(entryId, versionNo) {
    var hit = null;
    catalog().forEach(function (p) {
      if (hit) return;
      if (String(p.entry_id) === String(entryId) &&
          (versionNo === undefined || versionNo === null || String(p.version_no) === String(versionNo))) {
        hit = p;
      }
    });
    return hit;
  }

  function programs() {
    return arr(ov().programs);
  }

  function wirings() {
    return arr(ov().wirings);
  }

  function runs() {
    return arr(ov().runs).slice().sort(function (a, b) {
      return String(b.created_at || "").localeCompare(String(a.created_at || ""));
    });
  }

  function findById(list, id) {
    var hit = null;
    list.forEach(function (x) { if (!hit && x && String(x.id) === String(id)) hit = x; });
    return hit;
  }

  // -------------------------------------------------------------------------
  // モーダル骨格（グラフレビューの外枠 CSS を再利用）
  // -------------------------------------------------------------------------

  function ensureModal() {
    var modal = document.getElementById("simulation-bridge-modal");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "simulation-bridge-modal";
    modal.className = "graph-review-modal simulation-bridge-modal";
    modal.hidden = true;
    modal.innerHTML = "" +
      '<div class="graph-review-frame simulation-frame" role="dialog" aria-label="検証シミュレーション" data-ui-anchor="simulation.modal">' +
        '<div class="graph-review-header">' +
          '<div class="graph-review-title">検証シミュレーション — <span id="simulation-title-text"></span></div>' +
          '<div class="graph-review-toolbar">' +
            '<span id="simulation-model-chip" class="simulation-model-chip"></span>' +
            '<button type="button" id="simulation-reload" class="admin-action-btn">再読み込み</button>' +
            '<button type="button" id="simulation-close" class="admin-action-btn">閉じる</button>' +
          "</div>" +
        "</div>" +
        '<div class="simulation-lead">' +
          '<p class="simulation-disclaimer">' + esc(FACT_NOT_VERIFICATION) + "</p>" +
          '<div id="simulation-sandbox-fact" class="simulation-sandbox-fact" hidden></div>' +
          '<div id="simulation-overview-facts"></div>' +
        "</div>" +
        '<div id="simulation-body" class="simulation-body"></div>' +
      "</div>";
    document.body.appendChild(modal);
    modal.querySelector("#simulation-close").addEventListener("click", close);
    modal.querySelector("#simulation-reload").addEventListener("click", function () { loadOverview(); });
    modal.addEventListener("click", function (e) {
      if (e.target === modal) close();
    });
    modal.querySelector("#simulation-body").addEventListener("click", onBodyClick);
    modal.querySelector("#simulation-body").addEventListener("change", onBodyChange);
    modal.querySelector("#simulation-body").addEventListener("input", onBodyInput);
    mountModelChip(modal);
    return modal;
  }

  // コード生成のモデル選択（M層の汎用チップ）。部品が無ければ何も出さない（fail-closed）。
  function mountModelChip(modal) {
    var mountEl = modal.querySelector("#simulation-model-chip");
    if (!mountEl || !window.AdminLlmModels || typeof window.AdminLlmModels.createModelChip !== "function") return;
    try {
      state.modelChip = window.AdminLlmModels.createModelChip({
        sceneKey: "simulation",
        mountEl: mountEl,
        compact: true,
        onChange: function (model) { state.model = model || null; },
      });
    } catch (e) {
      state.modelChip = null;
    }
  }

  // -------------------------------------------------------------------------
  // 起動・読み込み
  // -------------------------------------------------------------------------

  function open(documentId, title) {
    if (!deps.apiFetch) {
      window.alert("検証シミュレーションの画面を初期化できていません。ページを再読み込みしてください。");
      return;
    }
    state.documentId = documentId;
    state.title = title || "教材";
    state.overview = null;
    state.loadError = "";
    state.step = "generate";
    state.busy = {};
    state.notices = {};
    state.builder = { name: "", parts: [] };
    state.runForm = null;
    state.runProgramId = "";
    state.runWiringId = "";
    state.selectedRunId = "";
    state.lastRun = null;
    state.proposal = null;
    var modal = ensureModal();
    modal.hidden = false;
    document.getElementById("simulation-title-text").textContent = state.title;
    renderAll();
    loadOverview();
  }

  function close() {
    var modal = document.getElementById("simulation-bridge-modal");
    if (modal) modal.hidden = true;
  }

  function loadOverview() {
    var documentId = state.documentId;
    if (!documentId) return Promise.resolve();
    captureRunForm();
    state.busy.overview = true;
    renderAll();
    return deps.apiFetch(docPath("/overview"))
      .then(function (res) { return readJson(res, "検証シミュレーションの情報を読み込めませんでした。"); })
      .then(function (data) {
        if (state.documentId !== documentId) return; // 別教材へ切替済みの遅延応答は破棄
        state.overview = data || {};
        state.loadError = "";
      })
      .catch(function (err) {
        if (state.documentId !== documentId) return;
        state.loadError = (err && err.message) || "検証シミュレーションの情報を読み込めませんでした。";
      })
      .then(function () {
        if (state.documentId !== documentId) return;
        state.busy.overview = false;
        renderAll();
      });
  }

  // -------------------------------------------------------------------------
  // 描画
  // -------------------------------------------------------------------------

  function renderAll() {
    var modal = document.getElementById("simulation-bridge-modal");
    if (!modal) return;
    renderLead(modal);
    var body = modal.querySelector("#simulation-body");
    if (state.busy.overview && !state.overview) {
      body.innerHTML = '<p class="simulation-muted">読み込み中...</p>';
      return;
    }
    if (state.loadError && !state.overview) {
      body.innerHTML = '<div class="simulation-notice is-error">' + esc(state.loadError) + "</div>";
      return;
    }
    body.innerHTML =
      (state.loadError ? '<div class="simulation-notice is-error">' + esc(state.loadError) + "</div>" : "") +
      renderModulesSection() +
      renderWiringSection() +
      renderRunSection() +
      renderLedgerSection();
    mountIndicatorLine(body);
  }

  function renderLead(modal) {
    var o = ov();
    var sandboxEl = modal.querySelector("#simulation-sandbox-fact");
    var factsEl = modal.querySelector("#simulation-overview-facts");
    var facts = arr(o.facts).filter(function (f) { return typeof f === "string" && f; });
    var sandboxFact = "";
    if (state.overview && o.sandbox_configured === false) {
      facts.forEach(function (f) {
        if (!sandboxFact && f.indexOf("実行環境") >= 0) sandboxFact = f;
      });
      if (!sandboxFact) sandboxFact = "実行環境が構成されていないため、この画面から実行はできません。";
    }
    if (sandboxFact) {
      sandboxEl.textContent = sandboxFact;
      sandboxEl.hidden = false;
    } else {
      sandboxEl.textContent = "";
      sandboxEl.hidden = true;
    }
    factsEl.innerHTML = factsHtml(facts.filter(function (f) { return f !== sandboxFact; }));
  }

  // ---- ①理論モジュール ------------------------------------------------------

  function equationChips(list) {
    var items = arr(list);
    if (!items.length) return '<span class="simulation-muted">（なし）</span>';
    return items.map(function (eq) {
      var e = obj(eq);
      return chip(e.display_label || e.equation_id || "式", "simulation-eq-chip");
    }).join(" ");
  }

  function renderModulesSection() {
    var o = ov();
    var modules = arr(o.modules).slice().sort(function (a, b) {
      var la = a && a.level === "inner" ? 1 : 0;
      var lb = b && b.level === "inner" ? 1 : 0;
      return la - lb;
    });
    var html = '<section class="simulation-section" data-ui-anchor="simulation.generate">' +
      '<h3 class="simulation-section-title">1. 理論モジュールとコード</h3>' +
      noticeHtml("generate");
    if (o.available === false || !modules.length) {
      html += '<p class="simulation-muted">この教材には、コード化できる理論モジュールがまだありません。</p>';
      return html + "</section>";
    }
    html += modules.map(renderModule).join("");
    return html + "</section>";
  }

  function renderModule(module) {
    var m = obj(module);
    var key = String(m.module_key || "");
    var inner = m.level === "inner";
    var assumptions = arr(m.assumptions).map(function (a) {
      var x = obj(a);
      return "<li>" + esc(x.text || "") + (x.origin_label ? ' <span class="simulation-muted">（' + esc(x.origin_label) + "）</span>" : "") + "</li>";
    }).join("");
    var canGenerate = m.has_equation_text !== false;
    var generating = !!state.busy["generate:" + key];
    var genBtn = '<button type="button" class="admin-action-btn" data-sim-action="generate" data-module-key="' + esc(key) + '"' +
      (canGenerate && !generating ? "" : " disabled") + ">" + (generating ? "生成中..." : "コードを生成") + "</button>";
    var genReason = canGenerate ? "" :
      '<span class="simulation-disabled-reason">この理論モジュールには式の本文が引けないため、コードを生成できません。</span>';
    return '<div class="simulation-module' + (inner ? " is-inner" : "") + '">' +
      '<div class="simulation-module-head">' +
        '<span class="simulation-module-label">' + esc(m.visual_label || m.label || "理論モジュール") + "</span>" +
        (inner ? chip("内側", "simulation-chip-muted") : "") +
      "</div>" +
      (m.theory_object ? '<div class="simulation-muted">理論対象: ' + esc(m.theory_object) + "</div>" : "") +
      '<div class="simulation-io">受ける式: ' + equationChips(m.inputs) + ' <span class="simulation-arrow">→</span> 返す式: ' + equationChips(m.outputs) + "</div>" +
      (assumptions ? '<div class="simulation-muted">要求される前提:</div><ul class="simulation-assumptions">' + assumptions + "</ul>" : "") +
      (m.reconstructed_inputs ? '<p class="simulation-fact-line">入力の式に PDF から復元された式が含まれています。</p>' : "") +
      '<div class="simulation-actions">' + genBtn + genReason + "</div>" +
      (m.program ? renderProgramCard(m.program) : "") +
    "</div>";
  }

  function ioRows(label, list) {
    return arr(list).map(function (item) {
      var x = obj(item);
      return "<tr><td>" + esc(label) + "</td><td><code>" + esc(x.symbol || "") + "</code></td><td>" +
        esc(x.kind || "") + "</td><td>" + esc(x.unit || "") + "</td><td>" + esc(fmtValue(x["default"])) + "</td></tr>";
    }).join("");
  }

  function renderProgramCard(program) {
    var p = obj(program);
    var prov = obj(p.provenance);
    var contract = obj(p.io_contract);
    var status = String(p.review_status || "");
    var rows = ioRows("入力", contract.inputs) + ioRows("出力", contract.outputs) + ioRows("パラメータ", contract.parameters);
    var astBlocked = prov.ast_ok === false;
    var deciding = !!state.busy["program:" + p.id];
    var decide = "";
    if (status === "candidate") {
      decide = '<span class="simulation-decide" data-ui-anchor="simulation.program-decide">' +
        '<button type="button" class="admin-action-btn" data-sim-action="program-confirm" data-program-id="' + esc(p.id) + '"' +
          (astBlocked || deciding ? " disabled" : "") + ">確定</button>" +
        '<button type="button" class="admin-action-btn" data-sim-action="program-dismiss" data-program-id="' + esc(p.id) + '"' +
          (deciding ? " disabled" : "") + ">見送り</button>" +
        (astBlocked ? '<span class="simulation-disabled-reason">許可されていない操作を含むため、このコードは確定できません。</span>' : "") +
        "</span>";
    }
    return '<div class="simulation-program-card">' +
      '<div class="simulation-card-head">コード ' + chip(PROGRAM_STATUS_LABELS[status] || status, "simulation-status-" + esc(status)) + "</div>" +
      factsHtml([].concat(arr(prov.facts), arr(p.facts))) +
      "<pre class=\"simulation-source\">" + esc(p.source_code || "") + "</pre>" +
      (rows
        ? '<table class="simulation-table"><thead><tr><th>区分</th><th>記号</th><th>種類</th><th>単位</th><th>既定値</th></tr></thead><tbody>' + rows + "</tbody></table>"
        : '<p class="simulation-muted">入出力の契約が記録されていません。</p>') +
      decide +
    "</div>";
  }

  // ---- ②実験機の配線 ---------------------------------------------------------

  function wiringChain(w) {
    return arr(w.parts).map(function (part) {
      var x = obj(part);
      return esc(x.name || (catalogEntry(x.entry_id, x.version_no) || {}).name || "部品");
    }).join(' <span class="simulation-arrow">→</span> ');
  }

  function renderWiringCard(wiring) {
    var w = obj(wiring);
    var isAi = w.origin === "llm_candidate";
    var status = String(w.review_status || "");
    var deciding = !!state.busy["wiring:" + w.id];
    var decide = "";
    if (status === "candidate") {
      decide = '<span class="simulation-decide" data-ui-anchor="simulation.wiring-decide">' +
        '<button type="button" class="admin-action-btn" data-sim-action="wiring-confirm" data-wiring-id="' + esc(w.id) + '"' + (deciding ? " disabled" : "") + ">確定</button>" +
        '<button type="button" class="admin-action-btn" data-sim-action="wiring-dismiss" data-wiring-id="' + esc(w.id) + '"' + (deciding ? " disabled" : "") + ">見送り</button>" +
        "</span>";
    }
    return '<div class="simulation-wiring-card' + (isAi ? " is-ai-proposal" : "") + '">' +
      '<div class="simulation-card-head">' + esc(w.name || "配線") + " " +
        chip(WIRING_STATUS_LABELS[status] || status, "simulation-status-" + esc(status)) +
        (isAi ? " " + chip("AI の提案（未確認）", "simulation-chip-ai") : "") +
      "</div>" +
      '<div class="simulation-chain">' + (wiringChain(w) || '<span class="simulation-muted">（部品なし）</span>') + "</div>" +
      (w.revision_note ? '<p class="simulation-muted">' + esc(w.revision_note) + "</p>" : "") +
      decide +
    "</div>";
  }

  function renderWiringSection() {
    var list = wirings();
    var html = '<section class="simulation-section" data-ui-anchor="simulation.wiring-new">' +
      '<h3 class="simulation-section-title">2. 実験機の配線</h3>' +
      noticeHtml("wiring");
    html += list.length
      ? list.map(renderWiringCard).join("")
      : '<p class="simulation-muted">この教材の配線はまだありません。</p>';
    if (!catalog().length) {
      var catalogFacts = arr(ov().facts).filter(function (f) { return typeof f === "string" && f.indexOf("部品") >= 0; });
      html += catalogFacts.length
        ? factsHtml(catalogFacts)
        : '<p class="simulation-muted">凍結された部品がまだありません。ナレッジライブラリに部品を登録して凍結してください。</p>';
      return html + "</section>";
    }
    html += renderBuilder();
    return html + "</section>";
  }

  function catalogOptionsHtml() {
    var groups = {};
    var order = [];
    catalog().forEach(function (p) {
      var g = p.entry_type_label || p.entry_type || "部品";
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(p);
    });
    return '<option value="">部品を選ぶ</option>' + order.map(function (g) {
      return '<optgroup label="' + esc(g) + '">' + groups[g].map(function (p) {
        return '<option value="' + esc(p.entry_id + "@" + p.version_no) + '">' + esc(p.name || p.entry_id) + "</option>";
      }).join("") + "</optgroup>";
    }).join("");
  }

  function renderBuilder() {
    var b = state.builder;
    var rows = b.parts.map(function (part, idx) {
      var entry = catalogEntry(part.entry_id, part.version_no) || {};
      var params = obj(obj(entry.body).params);
      var paramInputs = Object.keys(params).map(function (name) {
        var spec = obj(params[name]);
        var hint = [];
        if (spec.min !== undefined && spec.min !== null) hint.push("最小 " + spec.min);
        if (spec.max !== undefined && spec.max !== null) hint.push("最大 " + spec.max);
        var current = part.params[name] !== undefined ? part.params[name] : (spec["default"] !== undefined ? spec["default"] : "");
        return '<label class="simulation-param">' + esc(name) + (spec.unit ? " [" + esc(spec.unit) + "]" : "") +
          ' <input type="number" step="any" data-builder-index="' + idx + '" data-builder-param="' + esc(name) + '" value="' + esc(current) + '"' +
          (hint.length ? ' placeholder="' + esc(hint.join(" / ")) + '"' : "") + "></label>";
      }).join("");
      return '<li class="simulation-builder-part">' +
        '<div><strong>' + esc(entry.name || part.entry_id) + "</strong> " + chip(entry.entry_type_label || entry.entry_type || "", "simulation-chip-muted") + "</div>" +
        (entry.summary ? '<div class="simulation-muted">' + esc(entry.summary) + "</div>" : "") +
        (paramInputs ? '<div class="simulation-params">' + paramInputs + "</div>" : "") +
        '<div class="simulation-actions">' +
          '<button type="button" class="admin-action-btn" data-sim-action="builder-up" data-builder-index="' + idx + '"' + (idx === 0 ? " disabled" : "") + ">↑</button>" +
          '<button type="button" class="admin-action-btn" data-sim-action="builder-down" data-builder-index="' + idx + '"' + (idx === b.parts.length - 1 ? " disabled" : "") + ">↓</button>" +
          '<button type="button" class="admin-action-btn" data-sim-action="builder-remove" data-builder-index="' + idx + '">削除</button>' +
        "</div>" +
      "</li>";
    }).join("");
    var saving = !!state.busy.wiringSave;
    return '<div class="simulation-builder">' +
      '<div class="simulation-card-head">新しい配線を組む</div>' +
      '<label class="simulation-param">配線の名前 <input type="text" id="simulation-builder-name" value="' + esc(b.name) + '"></label>' +
      '<p class="simulation-muted">部品は上から順に信号が通ります（理論の出力 → 1 番目の部品 → … → 模擬観測）。</p>' +
      (rows ? '<ol class="simulation-builder-list">' + rows + "</ol>" : "") +
      '<div class="simulation-actions">' +
        '<select id="simulation-builder-select">' + catalogOptionsHtml() + "</select>" +
        '<button type="button" class="admin-action-btn" data-sim-action="builder-add">部品を追加</button>' +
        '<button type="button" class="admin-action-btn" data-sim-action="wiring-save"' + (b.parts.length && !saving ? "" : " disabled") + ">" +
          (saving ? "保存中..." : "配線を保存") + "</button>" +
      "</div>" +
    "</div>";
  }

  // ---- ③実行 -----------------------------------------------------------------

  function confirmedPrograms() {
    return programs().filter(function (p) { return p && p.review_status === "confirmed"; });
  }

  function confirmedWirings() {
    return wirings().filter(function (w) { return w && w.review_status === "confirmed"; });
  }

  function moduleLabelFor(program) {
    var label = "";
    arr(ov().modules).forEach(function (m) {
      if (!label && m && m.module_key === program.module_key) label = m.visual_label || m.label || "";
    });
    return label || "理論モジュールのコード";
  }

  function runFormValue(group, key, sub, fallback) {
    var f = state.runForm;
    if (!f || !f[group]) return fallback;
    var v = f[group][key];
    if (sub) v = v ? v[sub] : undefined;
    return v === undefined || v === null ? fallback : v;
  }

  function renderRunSection() {
    var progs = confirmedPrograms();
    var html = '<section class="simulation-section" data-ui-anchor="simulation.run">' +
      '<h3 class="simulation-section-title">3. 実行と比較</h3>' +
      noticeHtml("run");
    if (!progs.length) {
      html += '<p class="simulation-muted">確定したコードがまだありません。区画 1 でコードを確定すると実行できます。</p>';
      return html + renderRunHistory() + "</section>";
    }
    if (!state.runProgramId || !findById(progs, state.runProgramId)) state.runProgramId = String(progs[0].id);
    var wires = confirmedWirings();
    if (state.runWiringId && !findById(wires, state.runWiringId)) state.runWiringId = "";
    var program = findById(progs, state.runProgramId);
    var wiring = state.runWiringId ? findById(wires, state.runWiringId) : null;
    var contract = obj(program.io_contract);

    var progSelect = '<select id="simulation-run-program">' + progs.map(function (p) {
      return '<option value="' + esc(p.id) + '"' + (String(p.id) === state.runProgramId ? " selected" : "") + ">" + esc(moduleLabelFor(p)) + "</option>";
    }).join("") + "</select>";
    var wiringSelect = '<select id="simulation-run-wiring"><option value="">配線なし（理論の出力をそのまま見る）</option>' +
      wires.map(function (w) {
        return '<option value="' + esc(w.id) + '"' + (String(w.id) === state.runWiringId ? " selected" : "") + ">" + esc(w.name || "配線") + "</option>";
      }).join("") + "</select>";

    var targets = arr(ov().observation_targets);
    var claimsHtml = targets.length
      ? '<div class="simulation-claims">' + targets.map(function (t) {
          var checked = runFormValue("claims", t.claim_id, null, false);
          return '<label class="simulation-claim"><input type="checkbox" data-run-claim="' + esc(t.claim_id) + '"' + (checked ? " checked" : "") + "> " +
            esc(t.label || "観測系の主張") + (t.identified_via ? ' <span class="simulation-muted">（' + esc(t.identified_via) + "）</span>" : "") + "</label>";
        }).join("") + "</div>"
      : '<p class="simulation-muted">この教材では観測系の主張が見つかっていません。主張を選ばずに実行できます。</p>';

    function symbolInputs(list, group) {
      return arr(list).map(function (item) {
        var x = obj(item);
        var sym = String(x.symbol || "");
        if (!sym) return "";
        var current = runFormValue(group, sym, null, x["default"] !== undefined && x["default"] !== null ? x["default"] : "");
        return '<label class="simulation-param"><code>' + esc(sym) + "</code>" + (x.unit ? " [" + esc(x.unit) + "]" : "") +
          ' <input type="number" step="any" data-run-' + group + '="' + esc(sym) + '" value="' + esc(current) + '"></label>';
      }).join("");
    }

    var valuesHtml = symbolInputs(contract.inputs, "values");
    var paramsHtml = symbolInputs(contract.parameters, "params");

    var instHtml = "";
    if (wiring) {
      instHtml = arr(wiring.parts).map(function (part) {
        var x = obj(part);
        var slot = String(x.slot || "");
        var entry = catalogEntry(x.entry_id, x.version_no) || {};
        var specs = obj(obj(entry.body).params);
        var saved = obj(x.params);
        var names = Object.keys(specs);
        Object.keys(saved).forEach(function (n) { if (names.indexOf(n) < 0) names.push(n); });
        if (!names.length) return "";
        return '<div class="simulation-inst-slot"><div>' + esc(x.name || entry.name || slot) + "</div>" + names.map(function (n) {
          var spec = obj(specs[n]);
          var fallback = saved[n] !== undefined ? saved[n] : (spec["default"] !== undefined ? spec["default"] : "");
          var current = runFormValue("inst", slot, n, fallback);
          return '<label class="simulation-param">' + esc(n) + (spec.unit ? " [" + esc(spec.unit) + "]" : "") +
            ' <input type="number" step="any" data-run-inst-slot="' + esc(slot) + '" data-run-inst-param="' + esc(n) + '" value="' + esc(current) + '"></label>';
        }).join("") + "</div>";
      }).join("");
    }

    var refHtml = arr(contract.outputs).map(function (item) {
      var sym = String(obj(item).symbol || "");
      if (!sym) return "";
      return '<div class="simulation-ref-row"><code>' + esc(sym) + "</code>" +
        ' <label>値 <input type="number" step="any" data-run-ref="' + esc(sym) + '" data-run-ref-field="value" value="' + esc(runFormValue("ref", sym, "value", "")) + '"></label>' +
        ' <label>単位 <input type="text" data-run-ref="' + esc(sym) + '" data-run-ref-field="unit" value="' + esc(runFormValue("ref", sym, "unit", obj(item).unit || "")) + '"></label>' +
        ' <label>出典の覚え書き <input type="text" data-run-ref="' + esc(sym) + '" data-run-ref-field="source_note" value="' + esc(runFormValue("ref", sym, "source_note", "")) + '"></label>' +
      "</div>";
    }).join("");

    var seed = state.runForm && state.runForm.seed !== undefined ? state.runForm.seed : "";
    var running = !!state.busy.run;
    var sandboxOff = ov().sandbox_configured === false;

    html += '<div class="simulation-run-form">' +
      '<label class="simulation-param">コード ' + progSelect + "</label>" +
      '<label class="simulation-param">配線 ' + wiringSelect + "</label>" +
      '<div class="simulation-card-head">観測系の主張</div>' + claimsHtml +
      (valuesHtml ? '<div class="simulation-card-head">入力値</div><div class="simulation-params">' + valuesHtml + "</div>" : "") +
      (paramsHtml ? '<div class="simulation-card-head">パラメータ</div><div class="simulation-params">' + paramsHtml + "</div>" : "") +
      (instHtml ? "<details><summary>実験機の部品のパラメータ</summary>" + instHtml + "</details>" : "") +
      (refHtml ? "<details><summary>論文の記載値（任意）</summary>" + refHtml + "</details>" : "") +
      '<label class="simulation-param">乱数の種（任意） <input type="number" step="1" id="simulation-run-seed" value="' + esc(seed) + '"></label>' +
      '<div class="simulation-actions">' +
        '<button type="button" class="admin-action-btn" data-sim-action="run"' + (running || sandboxOff ? " disabled" : "") + ">" + (running ? "実行中..." : "実行") + "</button>" +
        (sandboxOff ? '<span class="simulation-disabled-reason">実行環境が構成されていないため実行できません。</span>' : "") +
      "</div>" +
    "</div>";
    html += renderSelectedRun();
    html += renderRunHistory();
    return html + "</section>";
  }

  function selectedRun() {
    var list = runs();
    if (state.selectedRunId) {
      var hit = findById(list, state.selectedRunId);
      if (hit) return hit;
      if (state.lastRun && String(state.lastRun.id) === String(state.selectedRunId)) return state.lastRun;
    }
    return list.length ? list[0] : null;
  }

  function metricsTable(metrics) {
    var m = obj(metrics);
    var rows = "";
    Object.keys(METRIC_LABELS).forEach(function (k) {
      if (m[k] === undefined || m[k] === null) return;
      rows += "<tr><td>" + esc(METRIC_LABELS[k]) + "</td><td>—</td><td>" + esc(fmtValue(m[k])) + "</td></tr>";
    });
    var bySym = obj(m.by_symbol);
    Object.keys(bySym).forEach(function (sym) {
      var per = bySym[sym];
      if (per && typeof per === "object") {
        Object.keys(per).forEach(function (k) {
          rows += "<tr><td>" + esc(METRIC_LABELS[k] || k) + "</td><td><code>" + esc(sym) + "</code></td><td>" + esc(fmtValue(per[k])) + "</td></tr>";
        });
      } else {
        rows += "<tr><td>記号ごとの値</td><td><code>" + esc(sym) + "</code></td><td>" + esc(fmtValue(per)) + "</td></tr>";
      }
    });
    if (!rows) return "";
    return '<table class="simulation-table"><thead><tr><th>計器</th><th>記号</th><th>値</th></tr></thead><tbody>' + rows + "</tbody></table>";
  }

  function outputsTable(result) {
    var theory = obj(result.theory_outputs);
    var observed = obj(result.observed);
    var syms = Object.keys(theory);
    Object.keys(observed).forEach(function (s) { if (syms.indexOf(s) < 0) syms.push(s); });
    if (!syms.length) return "";
    return '<table class="simulation-table"><thead><tr><th>記号</th><th>理論の出力</th><th>模擬観測の平均</th><th>模擬観測の標準偏差</th></tr></thead><tbody>' +
      syms.map(function (s) {
        var o = obj(observed[s]);
        return "<tr><td><code>" + esc(s) + "</code></td><td>" + esc(fmtValue(theory[s])) + "</td><td>" +
          esc(fmtValue(o.mean)) + "</td><td>" + esc(fmtValue(o.std)) + "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  function renderSelectedRun() {
    var run = selectedRun();
    if (!run) return "";
    var r = obj(run);
    var result = obj(r.result);
    var comparison = obj(result.comparison);
    var labels = obj(result.labels);
    var status = String(r.status || "");
    var facts = [].concat(arr(comparison.facts), arr(result.facts));
    facts = facts.filter(function (f, i) { return f && facts.indexOf(f) === i; });
    if (!facts.length && status !== "succeeded") facts.push("実行は完了しませんでした。");
    return '<div class="simulation-run-card">' +
      '<div class="simulation-card-head">実行結果 ' + chip(RUN_STATUS_LABELS[status] || status, "simulation-status-" + esc(status)) +
        " " + chip(labels.agreement, "simulation-chip-label") +
        " " + chip(labels.separation, "simulation-chip-label") +
        " " + chip(labels.reference, "simulation-chip-label") +
        (r.created_at ? ' <span class="simulation-muted">' + esc(r.created_at) + "</span>" : "") +
        sharedChip(r) +
      "</div>" +
      (result.statement ? '<p class="simulation-statement">' + esc(result.statement) + "</p>" : "") +
      factsHtml(facts) +
      outputsTable(result) +
      (metricsTable(comparison.metrics)
        ? '<div class="simulation-metrics"><div class="simulation-indicator-slot"></div>' + metricsTable(comparison.metrics) + "</div>"
        : "") +
    "</div>";
  }

  // 数値表の直上に、制度指標カタログの定義の 1 行を出す（取得できなければ何も描かない）。
  function mountIndicatorLine(body) {
    if (!window.AdminIndicators || typeof window.AdminIndicators.mount !== "function") return;
    var slot = body.querySelector(".simulation-indicator-slot");
    if (!slot) return;
    try {
      window.AdminIndicators.mount(slot, ov().indicator_id || DEFAULT_INDICATOR_ID);
    } catch (e) { /* fail-soft */ }
  }

  // v1.1（SB12/SB13）: 学習者が本人の操作で共有した実行だけがここに現れる。
  // 学習者の識別子は出さない（サーバが shared_by_label を付けたときだけ添える）。
  function sharedChip(r) {
    if (!r || r.shared !== true) return "";
    var label = typeof r.shared_by_label === "string" && r.shared_by_label ? " " + esc(r.shared_by_label) : "";
    return " " + chip("学習者が共有", "simulation-chip-shared") + label;
  }

  function renderRunHistory() {
    var list = runs();
    if (!list.length) return '<p class="simulation-muted">この教材の実行記録はまだありません。</p>';
    var current = selectedRun();
    return '<div class="simulation-history"><div class="simulation-card-head">実行の記録（新しい順）</div><ul>' +
      list.map(function (r) {
        var labels = obj(obj(r.result).labels);
        var active = current && String(current.id) === String(r.id);
        return '<li><button type="button" class="simulation-history-btn' + (active ? " is-active" : "") + '" data-sim-action="select-run" data-run-id="' + esc(r.id) + '">' +
          esc(r.created_at || "") + " " + esc(RUN_STATUS_LABELS[r.status] || r.status || "") +
          (labels.agreement ? " / " + esc(labels.agreement) : "") + "</button>" +
          sharedChip(r) + "</li>";
      }).join("") + "</ul></div>";
  }

  // ---- ④記帳と改訂 ------------------------------------------------------------

  function renderLedgerSection() {
    var run = selectedRun();
    var html = '<section class="simulation-section">' +
      '<h3 class="simulation-section-title">4. 台帳への記帳と改訂案</h3>' +
      noticeHtml("ledger");
    if (!run || run.status !== "succeeded") {
      html += '<p class="simulation-muted">実行済みの結果を選ぶと、台帳への記帳と改訂案を求める操作ができます。</p>';
      return html + renderProposal() + "</section>";
    }
    var claimIds = arr(run.observation_claim_ids);
    var recorded = !!run.ledger_candidate_id;
    var ledgerBusy = !!state.busy.ledger;
    var ledgerReason = recorded
      ? "この実行は台帳に候補として記帳済みです。"
      : (claimIds.length ? "" : "観測系の主張を選ばずに実行したため、記帳する先がありません。");
    var revisionBusy = !!state.busy.revision;
    var revisionReason = run.wiring_id ? "" : "配線なしの実行には改訂案を求められません。";
    html += '<div class="simulation-actions">' +
      '<span data-ui-anchor="simulation.ledger-candidate">' +
        '<button type="button" class="admin-action-btn" data-sim-action="ledger"' + (ledgerReason || ledgerBusy ? " disabled" : "") + ">台帳に候補として記帳</button>" +
        (ledgerReason ? '<span class="simulation-disabled-reason">' + esc(ledgerReason) + "</span>" : "") +
      "</span>" +
      '<span data-ui-anchor="simulation.propose-revision">' +
        '<button type="button" class="admin-action-btn" data-sim-action="revision"' + (revisionReason || revisionBusy ? " disabled" : "") + ">" + (revisionBusy ? "改訂案を作成中..." : "改訂案を求める") + "</button>" +
        (revisionReason ? '<span class="simulation-disabled-reason">' + esc(revisionReason) + "</span>" : "") +
      "</span>" +
    "</div>" +
    '<p class="simulation-muted">台帳での確定は前提の地図タブで行います。確定しても「間接的に支持」より上には上がりません。</p>';
    return html + renderProposal() + "</section>";
  }

  function renderProposal() {
    var data = state.proposal;
    if (!data) return "";
    var proposal = obj(data.proposal);
    var changes = arr(proposal.changes);
    var rows = changes.map(function (c) {
      var x = obj(c);
      var entry = x.entry_id ? catalogEntry(x.entry_id) : null;
      return "<tr><td>" + esc(CHANGE_OP_LABELS[x.op] || x.op || "") + "</td><td>" + esc(x.slot || "") + "</td><td>" +
        esc(entry ? entry.name : (x.entry_id ? "（目録の部品）" : "")) + "</td><td>" + esc(x.param || "") + "</td><td>" +
        esc(x.value === undefined ? "" : fmtValue(x.value)) + "</td><td>" + esc(x.reason || "") + "</td></tr>";
    }).join("");
    var dropped = arr(proposal.dropped).map(function (d) {
      if (typeof d === "string") return d;
      var x = obj(d);
      return x.reason || x.fact || "目録に無い提案を落としました。";
    });
    return '<div class="simulation-proposal">' +
      '<div class="simulation-card-head">改訂案 ' + chip("AI の提案（未確認）", "simulation-chip-ai") + "</div>" +
      (rows
        ? '<table class="simulation-table"><thead><tr><th>操作</th><th>位置</th><th>部品</th><th>パラメータ</th><th>値</th><th>理由</th></tr></thead><tbody>' + rows + "</tbody></table>"
        : "") +
      factsHtml([].concat(arr(proposal.facts), dropped)) +
      (data.wiring ? '<p class="simulation-muted">改訂案は区画 2 に候補の配線として追加されました。確定すると実行に使えます。</p>' : "") +
    "</div>";
  }

  // -------------------------------------------------------------------------
  // 入力の控え（再描画しても教員の入力を落とさない）
  // -------------------------------------------------------------------------

  function captureRunForm() {
    var modal = document.getElementById("simulation-bridge-modal");
    if (!modal) return;
    var body = modal.querySelector("#simulation-body");
    if (!body || !body.querySelector(".simulation-run-form")) return;
    var f = { values: {}, params: {}, inst: {}, ref: {}, claims: {}, seed: "" };
    body.querySelectorAll("[data-run-values]").forEach(function (el) { f.values[el.getAttribute("data-run-values")] = el.value; });
    body.querySelectorAll("[data-run-params]").forEach(function (el) { f.params[el.getAttribute("data-run-params")] = el.value; });
    body.querySelectorAll("[data-run-inst-slot]").forEach(function (el) {
      var slot = el.getAttribute("data-run-inst-slot");
      if (!f.inst[slot]) f.inst[slot] = {};
      f.inst[slot][el.getAttribute("data-run-inst-param")] = el.value;
    });
    body.querySelectorAll("[data-run-ref]").forEach(function (el) {
      var sym = el.getAttribute("data-run-ref");
      if (!f.ref[sym]) f.ref[sym] = {};
      f.ref[sym][el.getAttribute("data-run-ref-field")] = el.value;
    });
    body.querySelectorAll("[data-run-claim]").forEach(function (el) { f.claims[el.getAttribute("data-run-claim")] = !!el.checked; });
    var seedEl = body.querySelector("#simulation-run-seed");
    f.seed = seedEl ? seedEl.value : "";
    state.runForm = f;
  }

  function buildRunBody() {
    captureRunForm();
    var f = state.runForm || { values: {}, params: {}, inst: {}, ref: {}, claims: {}, seed: "" };
    var body = {
      program_id: state.runProgramId,
      observation_claim_ids: Object.keys(f.claims).filter(function (k) { return f.claims[k]; }),
      values: {},
      params: {},
    };
    if (state.runWiringId) body.wiring_id = state.runWiringId;
    var bad = [];
    function numbers(src, dst) {
      Object.keys(src).forEach(function (k) {
        if (String(src[k]).trim() === "") return;
        var n = parseNumber(src[k]);
        if (n === null) { bad.push(k); return; }
        dst[k] = n;
      });
    }
    numbers(f.values, body.values);
    numbers(f.params, body.params);
    var inst = {};
    Object.keys(f.inst).forEach(function (slot) {
      var dst = {};
      numbers(f.inst[slot], dst);
      if (Object.keys(dst).length) inst[slot] = dst;
    });
    if (state.runWiringId && Object.keys(inst).length) body.instrument_params = inst;
    var ref = {};
    Object.keys(f.ref).forEach(function (sym) {
      var row = f.ref[sym] || {};
      var v = parseNumber(row.value);
      if (v === null) {
        if (String(row.value || "").trim() !== "") bad.push(sym);
        return;
      }
      ref[sym] = { value: v };
      if (row.unit) ref[sym].unit = String(row.unit);
      if (row.source_note) ref[sym].source_note = String(row.source_note);
    });
    if (Object.keys(ref).length) body.reference = ref;
    var seed = parseNumber(f.seed);
    if (seed !== null) body.seed = Math.round(seed);
    return { body: body, bad: bad };
  }

  // -------------------------------------------------------------------------
  // 操作
  // -------------------------------------------------------------------------

  function onBodyInput(e) {
    var t = e.target;
    if (!t) return;
    if (t.id === "simulation-builder-name") {
      state.builder.name = t.value;
      return;
    }
    if (t.hasAttribute && t.hasAttribute("data-builder-param")) {
      var idx = Number(t.getAttribute("data-builder-index"));
      var part = state.builder.parts[idx];
      if (part) part.params[t.getAttribute("data-builder-param")] = t.value;
    }
  }

  function onBodyChange(e) {
    var t = e.target;
    if (!t) return;
    if (t.id === "simulation-run-program" || t.id === "simulation-run-wiring") {
      captureRunForm();
      if (t.id === "simulation-run-program") state.runProgramId = t.value;
      else state.runWiringId = t.value;
      state.step = "run";
      renderAll();
    }
  }

  function onBodyClick(e) {
    var btn = e.target && e.target.closest ? e.target.closest("[data-sim-action]") : null;
    if (!btn || btn.disabled) return;
    var action = btn.getAttribute("data-sim-action");
    switch (action) {
      case "generate": generateProgram(btn.getAttribute("data-module-key")); break;
      case "program-confirm": decideProgram(btn.getAttribute("data-program-id"), "confirm"); break;
      case "program-dismiss": decideProgram(btn.getAttribute("data-program-id"), "dismiss"); break;
      case "wiring-confirm": decideWiring(btn.getAttribute("data-wiring-id"), "confirm"); break;
      case "wiring-dismiss": decideWiring(btn.getAttribute("data-wiring-id"), "dismiss"); break;
      case "builder-add": builderAdd(); break;
      case "builder-up": builderMove(Number(btn.getAttribute("data-builder-index")), -1); break;
      case "builder-down": builderMove(Number(btn.getAttribute("data-builder-index")), 1); break;
      case "builder-remove": builderRemove(Number(btn.getAttribute("data-builder-index"))); break;
      case "wiring-save": saveWiring(); break;
      case "run": runSimulation(); break;
      case "select-run":
        captureRunForm();
        state.selectedRunId = btn.getAttribute("data-run-id");
        state.proposal = null;
        state.step = "run";
        renderAll();
        break;
      case "ledger": recordLedgerCandidate(); break;
      case "revision": proposeRevision(); break;
      default: break;
    }
  }

  function withBusy(key, section, promise) {
    state.busy[key] = true;
    captureRunForm();
    renderAll();
    return promise
      .catch(function (err) {
        setNotice(section, (err && err.message) || "操作を完了できませんでした。", "error");
      })
      .then(function () {
        state.busy[key] = false;
        return loadOverview();
      });
  }

  function generateProgram(moduleKey) {
    state.step = "generate";
    setNotice("generate", "", "");
    var body = { module_key: moduleKey };
    if (state.model) body.model = state.model;
    return withBusy("generate:" + moduleKey, "generate",
      post(docPath("/programs/generate"), body)
        .then(function (res) { return readJson(res, "コードを生成できませんでした。"); })
        .then(function (data) {
          var facts = arr(data.facts).filter(function (f) { return typeof f === "string"; });
          setNotice("generate", facts.length ? facts.join(" ") : "コードの候補を作りました。内容を確認して確定か見送りを選んでください。", "success");
        }));
  }

  function decideProgram(programId, action) {
    state.step = "generate";
    setNotice("generate", "", "");
    return withBusy("program:" + programId, "generate",
      post(base() + "/programs/" + encodeURIComponent(programId) + "/decide", { action: action })
        .then(function (res) { return readJson(res, "コードの確定・見送りを記録できませんでした。"); })
        .then(function () {
          setNotice("generate", action === "confirm" ? "コードを確定しました。" : "コードを見送りました。", "success");
        }));
  }

  function decideWiring(wiringId, action) {
    state.step = "wiring";
    setNotice("wiring", "", "");
    return withBusy("wiring:" + wiringId, "wiring",
      post(base() + "/wirings/" + encodeURIComponent(wiringId) + "/decide", { action: action })
        .then(function (res) { return readJson(res, "配線の確定・見送りを記録できませんでした。"); })
        .then(function () {
          setNotice("wiring", action === "confirm" ? "配線を確定しました。" : "配線を見送りました。", "success");
        }));
  }

  function builderAdd() {
    state.step = "wiring";
    var sel = document.getElementById("simulation-builder-select");
    if (!sel || !sel.value) return;
    var at = sel.value.lastIndexOf("@");
    var entryId = sel.value.slice(0, at);
    var versionNo = sel.value.slice(at + 1);
    var entry = catalogEntry(entryId, versionNo);
    if (!entry) return;
    var params = {};
    var specs = obj(obj(entry.body).params);
    Object.keys(specs).forEach(function (n) {
      var d = obj(specs[n])["default"];
      params[n] = d === undefined || d === null ? "" : d;
    });
    state.builder.parts.push({ entry_id: entry.entry_id, version_no: entry.version_no, params: params });
    captureRunForm();
    renderAll();
  }

  function builderMove(idx, delta) {
    var parts = state.builder.parts;
    var to = idx + delta;
    if (to < 0 || to >= parts.length) return;
    var tmp = parts[idx];
    parts[idx] = parts[to];
    parts[to] = tmp;
    captureRunForm();
    renderAll();
  }

  function builderRemove(idx) {
    state.builder.parts.splice(idx, 1);
    captureRunForm();
    renderAll();
  }

  function saveWiring() {
    state.step = "wiring";
    setNotice("wiring", "", "");
    var b = state.builder;
    var name = String(b.name || "").trim();
    if (!name) {
      setNotice("wiring", "配線の名前を入力してください。", "error");
      renderAll();
      return;
    }
    var bad = [];
    var parts = b.parts.map(function (p, idx) {
      var params = {};
      Object.keys(p.params).forEach(function (k) {
        if (String(p.params[k]).trim() === "") return;
        var n = parseNumber(p.params[k]);
        if (n === null) { bad.push(k); return; }
        params[k] = n;
      });
      return { slot: "s" + (idx + 1), entry_id: p.entry_id, version_no: p.version_no, params: params };
    });
    if (bad.length) {
      setNotice("wiring", "数値として読めないパラメータがあります: " + bad.join("、"), "error");
      renderAll();
      return;
    }
    var edges = [];
    for (var i = 0; i + 1 < parts.length; i++) edges.push({ from_slot: parts[i].slot, to_slot: parts[i + 1].slot });
    return withBusy("wiringSave", "wiring",
      post(docPath("/wirings"), { name: name, parts: parts, edges: edges })
        .then(function (res) { return readJson(res, "配線を保存できませんでした。"); })
        .then(function () {
          state.builder = { name: "", parts: [] };
          setNotice("wiring", "配線を保存しました。", "success");
        }));
  }

  function runSimulation() {
    state.step = "run";
    setNotice("run", "", "");
    var built = buildRunBody();
    if (built.bad.length) {
      setNotice("run", "数値として読めない入力があります: " + built.bad.join("、"), "error");
      renderAll();
      return;
    }
    state.proposal = null;
    return withBusy("run", "run",
      post(docPath("/runs"), built.body)
        .then(function (res) { return readJson(res, "実行できませんでした。"); })
        .then(function (data) {
          var run = obj(data.run);
          if (run.id) {
            state.selectedRunId = String(run.id);
            state.lastRun = run;
          }
          setNotice("run", run.id ? "実行を記録しました。" : "実行の結果を受け取れませんでした。", run.id ? "success" : "info");
        }));
  }

  function recordLedgerCandidate() {
    state.step = "ledger";
    setNotice("ledger", "", "");
    var run = selectedRun();
    if (!run) return;
    var claimIds = arr(run.observation_claim_ids);
    var ok = window.confirm("観測系の主張 " + claimIds.length + " 件の台帳に候補として記帳します。確定は台帳の画面で行います。");
    if (!ok) return;
    return withBusy("ledger", "ledger",
      post(base() + "/runs/" + encodeURIComponent(run.id) + "/ledger-candidate", { claim_ids: claimIds })
        .then(function (res) { return readJson(res, "台帳に記帳できませんでした。"); })
        .then(function () {
          setNotice("ledger", "記帳しました（候補）。確定は前提の地図タブの台帳で行います。", "success");
        }));
  }

  function proposeRevision() {
    state.step = "ledger";
    setNotice("ledger", "", "");
    var run = selectedRun();
    if (!run) return;
    return withBusy("revision", "ledger",
      post(base() + "/runs/" + encodeURIComponent(run.id) + "/propose-revision", {})
        .then(function (res) { return readJson(res, "改訂案を作れませんでした。"); })
        .then(function (data) {
          state.proposal = data || {};
          if (!data || !data.wiring) {
            var facts = arr(obj(data && data.proposal).facts).filter(function (f) { return typeof f === "string"; });
            setNotice("ledger", facts.length ? facts.join(" ") : "目録にある部品の組み替えでは改訂案を作れませんでした。", "info");
          } else {
            setNotice("ledger", "改訂案を受け取りました（AI の提案・未確認）。", "success");
          }
        }));
  }

  // -------------------------------------------------------------------------
  // 画面文脈（参照だけを返す。画面の文字列は送らない — SA1）
  // -------------------------------------------------------------------------

  function getScreenContext() {
    var run = state.overview ? selectedRun() : null;
    return {
      screen: SCREEN_CONTEXT_SCREEN,
      selection: {
        document_id: String(state.documentId || ""),
        program_id: String(state.runProgramId || ""),
        wiring_id: String(state.runWiringId || ""),
        run_id: run ? String(run.id || "") : "",
      },
      view: { step: state.step },
      visible_entities: [],
    };
  }

  // -------------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------------

  window.SimulationBridge = {
    init: function (options) {
      options = options || {};
      deps.apiFetch = options.apiFetch || null;
      deps.escHtml = options.escHtml || null;
      deps.getToken = options.getToken || null;
    },
    open: open,
    close: close,
    getScreenContext: getScreenContext,
  };
})();
