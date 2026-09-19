/**
 * 束の取り込み（知識の転用層 P4-1 / §4.3）— 教材行の「⋯」メニューから開くモーダル。
 *
 * 正本: docs/features/knowledge_transfer_design.md（不変条項 KT1〜KT8、判断 T-1 / T-2）。
 *
 *   KT2 取り込みは候補・確定は人間 — 束の承認状態はこのインスタンスの承認にしない。
 *       画面は「未確認の候補として着地する」というサーバの事実文をそのまま出す。
 *   T-2  live 行のある教材への取り込みは既定で 409。教員が「置き換える」を明示した
 *       ときだけ replace=true を送る（無言で上書きしない）。
 *   KT5 情報を落とさない — dry-run の結果・エラーの事実文を握り潰さずそのまま描く。
 *   KT7 数値・内部 ID を見せない — 描くのは束に入っている項目の件数（counts）だけで、
 *       スコア・比率・進捗率は作らない。
 *
 * 2 段構成（確認 → 取り込む）であることがこのモジュールの要石:
 *   ① [確認] = `dry_run=true`（**書き込み 0**）。何が入っているか・置き換えになるかを先に見る。
 *   ② [取り込む] = `dry_run=false`。①を通していないと押せない（確認なしの実行を作らない）。
 *
 * ES5 で書く（開発ルール5。admin-paper-radar.js / admin-cartridge-fit.js と同じ流儀）。
 * DI は init(deps)。deps = { apiFetchRaw, escHtml, onImported }。ポーリングしない。
 */
(function () {
  "use strict";

  var deps = { apiFetchRaw: null, escHtml: null, onImported: null };

  // counts のキー → 教員向けの見出し（束に何が入っているかの内訳。件数以外は出さない）。
  var COUNT_LABELS = [
    ["claims", "主張"],
    ["components", "部品"],
    ["equations", "式"],
    ["evidence", "根拠"],
    ["derivation_steps", "導出の手順"],
    ["graph_nodes", "グラフのノード"]
  ];

  // 置き換えの確認文（T-2）。サーバの 409 と同じ事実を、押す前に見せる。
  // P4-R2: 「保たれます」とだけ言わない。束に無い既存の項目が表示対象から外れることを
  // 先に言い、そのうえで教員が確定した項目は外さないと分けて言う。
  var REPLACE_CONFIRM_TEXT =
    "この教材には解析結果があります。取り込むと、束に無い既存の項目は" +
    "この教材の表示対象から外れます（教員が確定した項目は外しません）。";

  var FILE_REQUIRED_TEXT = "取り込む束（zip）を選んでください。";
  var DRY_RUN_REQUIRED_TEXT = "先に［確認］で束の中身を確かめてください。";
  var GENERIC_ERROR_TEXT = "束を取り込めませんでした。";

  var state = {
    open: false,
    documentId: "",
    title: "",
    plan: null,      // dry-run の結果（null なら未確認 = 取り込みは押せない）
    busy: false,
    replace: false
  };

  function esc(text) {
    var fn = deps.escHtml;
    if (typeof fn === "function") return fn(text);
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function api(path, opts) {
    var fn = deps.apiFetchRaw || window.apiFetchRaw;
    if (typeof fn !== "function") {
      return Promise.reject(new Error("KnowledgeImport: apiFetchRaw is not injected"));
    }
    try {
      return Promise.resolve(fn(path, opts));
    } catch (err) {
      return Promise.reject(err);
    }
  }

  function el(id) {
    return document.getElementById(id);
  }

  function init(options) {
    if (!options) return;
    if (typeof options.apiFetchRaw === "function") deps.apiFetchRaw = options.apiFetchRaw;
    if (typeof options.escHtml === "function") deps.escHtml = options.escHtml;
    if (typeof options.onImported === "function") deps.onImported = options.onImported;
  }

  function close() {
    var overlay = el("knowledge-import-modal");
    if (overlay) overlay.remove();
    state.open = false;
    state.plan = null;
    state.busy = false;
    state.replace = false;
  }

  function modalHtml() {
    return (
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:560px;max-width:760px;width:82vw;max-height:86vh;display:flex;flex-direction:column;overflow-y:auto">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">束を取り込む</h3>' +
          '<button type="button" id="ki-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        "</div>" +

        '<div id="ki-target" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:8px"></div>' +

        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          "別のインスタンスで書き出した束（zip）を、この教材の知識として取り込みます。" +
          "取り込んだ項目は未確認（教員の確認待ち）の候補として着地します。" +
        "</p>" +

        // ① 束を選ぶ → 確認（dry-run・書き込み 0）
        '<div style="border:1px solid var(--color-border-tertiary);border-radius:6px;padding:10px;margin-bottom:10px">' +
          '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
            '<input type="file" id="ki-file" accept=".zip,application/zip" ' +
              'style="font-size:12px;color:var(--color-text-primary)">' +
            '<button type="button" id="ki-dryrun-btn" class="admin-action-btn">確認</button>' +
          "</div>" +
          '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:6px">' +
            "［確認］では何も書き込まれません。束の中身と、置き換えになるかどうかを先に表示します。" +
          "</div>" +
        "</div>" +

        // ② dry-run の結果（出所・内訳・事実文）
        '<div id="ki-plan" style="overflow-y:auto;flex:1;min-height:80px"></div>' +

        // ③ 置き換えの明示（live 行があるときだけ出す = T-2）
        '<div id="ki-replace-row" style="margin-top:8px" hidden>' +
          '<label style="display:flex;gap:6px;align-items:flex-start;font-size:12px;color:var(--color-text-primary)">' +
            '<input type="checkbox" id="ki-replace" style="margin-top:2px">' +
            "<span>" + esc(REPLACE_CONFIRM_TEXT) + "<br>" +
            "置き換えて取り込む場合はチェックしてください。</span>" +
          "</label>" +
        "</div>" +

        '<div id="ki-message" style="font-size:12px;color:var(--color-text-secondary);margin-top:8px"></div>' +

        '<div style="border-top:1px solid var(--color-border-tertiary);margin-top:10px;padding-top:10px;display:flex;gap:8px;align-items:center">' +
          '<span style="flex:1"></span>' +
          '<button type="button" id="ki-cancel" class="admin-action-btn" style="background:var(--color-bg-tertiary);color:var(--color-text)">閉じる</button>' +
          '<button type="button" id="ki-submit-btn" data-ui-anchor="materials.import-submit" class="admin-action-btn" disabled>取り込む</button>' +
        "</div>" +
      "</div>"
    );
  }

  function openModal(documentId, title) {
    if (!documentId) return;
    close();
    state.open = true;
    state.documentId = String(documentId);
    state.title = title || "";
    state.plan = null;
    state.busy = false;
    state.replace = false;

    var overlay = document.createElement("div");
    overlay.id = "knowledge-import-modal";
    overlay.setAttribute("data-ui-anchor", "materials.import-modal");
    overlay.style.cssText =
      "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML = modalHtml();
    document.body.appendChild(overlay);

    var target = el("ki-target");
    if (target) target.textContent = "取り込み先: " + (state.title || state.documentId);

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });
    el("ki-close").addEventListener("click", close);
    el("ki-cancel").addEventListener("click", close);
    el("ki-dryrun-btn").addEventListener("click", runDryRun);
    el("ki-submit-btn").addEventListener("click", runImport);
    el("ki-replace").addEventListener("change", function () {
      state.replace = !!this.checked;
      renderSubmitState();
    });
    // 束を選び直したら確認をやり直す（別の束の確認結果で実行させない）。
    el("ki-file").addEventListener("change", function () {
      state.plan = null;
      state.replace = false;
      var box = el("ki-replace");
      if (box) box.checked = false;
      renderPlan();
      setMessage("");
    });

    renderPlan();
    renderSubmitState();
  }

  function setMessage(text, isError) {
    var box = el("ki-message");
    if (!box) return;
    box.textContent = text || "";
    box.style.color = isError
      ? "var(--color-text-danger, #c0392b)"
      : "var(--color-text-secondary)";
  }

  function selectedFile() {
    var input = el("ki-file");
    if (!input || !input.files || !input.files.length) return null;
    return input.files[0];
  }

  // 取り込みが押せるのは「確認済み」かつ「置き換えが必要なら明示済み」のときだけ。
  function canSubmit() {
    if (state.busy) return false;
    if (!state.plan) return false;
    var target = state.plan.target || {};
    if (target.has_live_rows && !state.replace) return false;
    return true;
  }

  function renderSubmitState() {
    var btn = el("ki-submit-btn");
    if (!btn) return;
    btn.disabled = !canSubmit();
    var dryBtn = el("ki-dryrun-btn");
    if (dryBtn) dryBtn.disabled = !!state.busy;
  }

  function renderPlan() {
    var box = el("ki-plan");
    if (!box) return;
    var plan = state.plan;
    var replaceRow = el("ki-replace-row");
    if (!plan) {
      box.innerHTML =
        '<div style="font-size:12px;color:var(--color-text-tertiary)">' +
        esc("束を選んで［確認］を押すと、束の中身がここに表示されます。") +
        "</div>";
      if (replaceRow) replaceRow.hidden = true;
      renderSubmitState();
      return;
    }

    var source = plan.source || {};
    var counts = plan.counts || {};
    var html = '<div style="border:1px solid var(--color-border-tertiary);border-radius:6px;padding:10px">';

    html += '<div style="font-size:12px;color:var(--color-text-primary);font-weight:600;margin-bottom:4px">束の出所</div>';
    // 書き出し元はサーバが組み立てた 1 行（app は {name, version, git_commit} の dict なので
    // そのまま連結すると [object Object] になる）。
    var appLabel = source.app_label || (source.app && source.app.name) || "";
    html += '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:2px">' +
      esc("書き出し元: " + (appLabel || "不明")) + "</div>";
    if (source.exported_at) {
      html += '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:2px">' +
        esc("書き出し日時: " + source.exported_at) + "</div>";
    }
    if (source.object_type) {
      html += '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:2px">' +
        esc("書き出しの範囲: " + source.object_type) + "</div>";
    }

    html += '<div style="font-size:12px;color:var(--color-text-primary);font-weight:600;margin:8px 0 4px">束に入っているもの</div>';
    html += '<ul style="margin:0 0 4px;padding-left:18px;font-size:12px;color:var(--color-text-secondary)">';
    for (var i = 0; i < COUNT_LABELS.length; i++) {
      var key = COUNT_LABELS[i][0];
      var label = COUNT_LABELS[i][1];
      var value = counts[key];
      if (typeof value !== "number") continue;
      html += "<li>" + esc(label + ": " + value) + "</li>";
    }
    html += "</ul>";

    // P4-R2: 置き換えで「何が表示対象から外れ、何が残るか」をラベルで見せる
    // （件数だけの数値バッジにしない。サーバが返した種別ごとの事実をそのまま描く）。
    var supersede = plan.would_supersede_counts || {};
    var supersedeRows = "";
    for (var s = 0; s < COUNT_LABELS.length; s++) {
      var sKey = COUNT_LABELS[s][0];
      var entry = supersede[sKey];
      if (!entry) continue;
      var dropped = entry.superseded || 0;
      var kept = entry.kept_human_decided || 0;
      if (!dropped && !kept) continue;
      var line = (entry.label || COUNT_LABELS[s][1]) + ": ";
      line += "表示対象から外れる " + dropped + " 件";
      if (kept) line += " / 教員が確定しているため残る " + kept + " 件";
      supersedeRows += "<li>" + esc(line);
      var labels = (entry.labels || []).concat([]);
      if (labels.length) {
        supersedeRows += '<div style="color:var(--color-text-tertiary);margin:2px 0 0 0">' +
          esc(labels.join(" / ") + (entry.labels_truncated ? " ほか" : "")) + "</div>";
      }
      supersedeRows += "</li>";
    }
    if (supersedeRows) {
      html += '<div style="font-size:12px;color:var(--color-text-primary);font-weight:600;margin:8px 0 4px">取り込むと表示対象から外れるもの</div>';
      html += '<ul style="margin:0 0 4px;padding-left:18px;font-size:12px;color:var(--color-text-secondary)">' +
        supersedeRows + "</ul>";
    }

    var facts = plan.facts || [];
    if (facts.length) {
      html += '<div style="font-size:12px;color:var(--color-text-primary);font-weight:600;margin:8px 0 4px">取り込むとどうなるか</div>';
      for (var f = 0; f < facts.length; f++) {
        html += '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:3px">' +
          esc(facts[f]) + "</div>";
      }
    }

    var warnings = plan.warnings || [];
    if (warnings.length) {
      html += '<div style="font-size:12px;color:var(--color-text-primary);font-weight:600;margin:8px 0 4px">束の中で解決できなかった参照</div>';
      for (var w = 0; w < warnings.length; w++) {
        html += '<div style="font-size:11.5px;color:var(--color-text-tertiary);margin-bottom:2px">' +
          esc(String(warnings[w])) + "</div>";
      }
    }

    html += "</div>";
    box.innerHTML = html;

    if (replaceRow) replaceRow.hidden = !(plan.target && plan.target.has_live_rows);
    renderSubmitState();
  }

  // エラー本文（detail.facts / detail.message / detail 文字列）を事実文として取り出す。
  // サーバが返した文言をクライアントで言い換えない。
  function detailLines(body, fallback) {
    var detail = body && body.detail !== undefined ? body.detail : body;
    if (!detail) return [fallback];
    if (typeof detail === "string") return [detail];
    var lines = [];
    if (detail.facts && detail.facts.length) {
      for (var i = 0; i < detail.facts.length; i++) lines.push(String(detail.facts[i]));
    } else if (detail.message) {
      lines.push(String(detail.message));
    }
    var report = detail.report || {};
    var errors = report.errors || [];
    for (var e = 0; e < errors.length && e < 10; e++) lines.push(String(errors[e]));
    return lines.length ? lines : [fallback];
  }

  function showError(res, fallback) {
    return res
      .json()
      .catch(function () { return null; })
      .then(function (body) {
        setMessage(detailLines(body, fallback).join(" / "), true);
      });
  }

  function buildFormData(file) {
    var form = new FormData();
    form.append("bundle", file, file.name || "bundle.zip");
    return form;
  }

  function importPath(dryRun) {
    if (dryRun) {
      return (
        "/documents/" + encodeURIComponent(state.documentId) +
        "/import-bundle?dry_run=true"
      );
    }
    // 確定は「確認した束」に対してだけ効く。確認で受け取ったハッシュを送り返し、
    // サーバが再計算した値と照合する（違えば 409・事実文）。
    var sha = (state.plan && state.plan.bundle_sha256) || "";
    return (
      "/documents/" + encodeURIComponent(state.documentId) +
      "/import-bundle?dry_run=false" +
      "&replace=" + (state.replace ? "true" : "false") +
      "&bundle_sha256=" + encodeURIComponent(sha)
    );
  }

  // ① 確認（dry_run=true・書き込み 0）
  function runDryRun() {
    if (state.busy) return;
    var file = selectedFile();
    if (!file) { setMessage(FILE_REQUIRED_TEXT, true); return; }
    state.busy = true;
    state.plan = null;
    renderSubmitState();
    setMessage("束を確認しています…");
    var documentId = state.documentId;

    api(importPath(true), { method: "POST", body: buildFormData(file) })
      .then(function (res) {
        if (state.documentId !== documentId || !state.open) return null;
        if (!res.ok) return showError(res, GENERIC_ERROR_TEXT).then(function () { return null; });
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        if (state.documentId !== documentId || !state.open) return;
        state.plan = data;
        state.replace = false;
        var box = el("ki-replace");
        if (box) box.checked = false;
        renderPlan();
        setMessage("");
      })
      .catch(function () {
        setMessage(GENERIC_ERROR_TEXT, true);
      })
      .then(function () {
        state.busy = false;
        renderSubmitState();
      });
  }

  // ② 取り込む（dry_run=false）。確認を通していないと実行しない。
  function runImport() {
    if (state.busy) return;
    if (!state.plan) { setMessage(DRY_RUN_REQUIRED_TEXT, true); return; }
    var file = selectedFile();
    if (!file) { setMessage(FILE_REQUIRED_TEXT, true); return; }
    state.busy = true;
    renderSubmitState();
    setMessage("取り込んでいます…");
    var documentId = state.documentId;

    api(importPath(false), { method: "POST", body: buildFormData(file) })
      .then(function (res) {
        if (state.documentId !== documentId || !state.open) return null;
        if (!res.ok) return showError(res, GENERIC_ERROR_TEXT).then(function () { return null; });
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        if (state.documentId !== documentId || !state.open) return;
        setMessage(
          "取り込みました。取り込んだ項目は未確認（教員の確認待ち）の候補として着地しています。"
        );
        if (typeof deps.onImported === "function") {
          try { deps.onImported(); } catch (err) { /* 一覧の再読込に失敗しても閉じない */ }
        }
      })
      .catch(function () {
        setMessage(GENERIC_ERROR_TEXT, true);
      })
      .then(function () {
        state.busy = false;
        renderSubmitState();
      });
  }

  window.KnowledgeImport = { init: init, openModal: openModal, close: close };
})();
