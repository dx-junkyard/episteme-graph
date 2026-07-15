/*
 * W層（Element Deliberation Workspace / 要素検討ワークスペース）Phase 0 統合パネル。
 *
 * ES5 / IIFE。window.Deliberation を公開。admin.js の initApp() から
 * Deliberation.init({apiFetch, escHtml}) を呼んで起動する
 * （admin-lecture-studio.js / admin-assistant.js と同型の DI 注入パターン）。
 *
 * 正本: docs/features/element_deliberation_workspace_design.md
 *   §8 API: GET /api/admin/deliberation/elements/{element_type}/{element_id}/overview
 *   §9 フロント: 各要素の「深く検討」ボタン → モーダルで面①内訳 + 面②位置づけを表示。
 *
 * Phase 0 の範囲: overview の統合表示のみ（読み取り専用）。対話・候補注釈（面③、§5）は
 * migration 046 と合わせて Phase 2 で追加する（本ファイルはそれまで書き込み API を呼ばない）。
 *
 * 不変条項（設計書 §0）:
 *   W5 権限 fail-closed — overview API 側が document 権限をゲートする（本ファイルは何もしない）。
 *   W8 数値を見せない — confidence・件数の生数値は表示しない（Phase 0 の overview 自体に
 *     confidence フィールドが無いため、内訳の生データをそのまま列挙する分には抵触しない）。
 *   §9 ポーリング禁止・都度 fetch（キャッシュしない）。
 */
(function () {
  "use strict";

  // 注入される依存（疎結合）。未注入なら window グローバルへフォールバック
  // （admin-lecture-studio.js / admin-assistant.js と同型）。
  var deps = { apiFetch: null, escHtml: null };
  var initialized = false;

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    return fn(path, opts);
  }

  function escHtml(s) {
    var fn = deps.escHtml || window.escHtml;
    if (fn) return fn(s);
    if (s === null || s === undefined || s === "") return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // 面②の4レンズ（設計書 §4）。null のレンズは区画ごと非表示にする。
  var LENS_LABELS = {
    intra_document: "論文内",
    atlas: "分野の地図",
    endorsement: "承認・共有",
    epistemic: "検証・疑義"
  };
  var LENS_ORDER = ["intra_document", "atlas", "endorsement", "epistemic"];

  var ELEMENT_TYPE_LABELS = {
    figure: "図",
    theory_component: "コンポーネント",
    theory_claim: "claim",
    equation: "数式",
    shared_part: "共通部品"
  };

  // ── 公開 API: init ───────────────────────────────────────────────────
  function init(options) {
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    deps.escHtml = options.escHtml || null;
    initialized = true;
  }

  // ── モーダル DOM ──────────────────────────────────────────────────────
  function _closeModal() {
    var m = document.getElementById("deliberation-modal");
    if (m) m.remove();
  }

  function _renderValue(value) {
    if (value === null || value === undefined || value === "") {
      return '<span style="color:var(--color-text-tertiary)">(空)</span>';
    }
    if (typeof value === "object") {
      // ネストした dict/配列はデータをそのまま簡潔に提示する（要約・件数化はしない・W4）。
      try {
        return '<pre style="white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px">' +
          escHtml(JSON.stringify(value, null, 2)) + '</pre>';
      } catch (e) {
        return escHtml(String(value));
      }
    }
    return escHtml(String(value));
  }

  function _kvTableHtml(pairs) {
    // pairs: [[label, value], ...]
    if (!pairs.length) {
      return '<p style="font-size:12.5px;color:var(--color-text-tertiary);margin:4px 0">情報がありません</p>';
    }
    var rows = pairs.map(function (pair) {
      return '<tr>' +
        '<td style="padding:4px 10px 4px 0;font-size:12.5px;color:var(--color-text-tertiary);white-space:nowrap;vertical-align:top">' +
          escHtml(pair[0]) +
        '</td>' +
        '<td style="padding:4px 0;font-size:13px;color:var(--color-text-primary);vertical-align:top">' +
          _renderValue(pair[1]) +
        '</td>' +
      '</tr>';
    }).join("");
    return '<table style="width:100%;border-collapse:collapse">' + rows + '</table>';
  }

  function _fieldsHtml(fields) {
    var keys = Object.keys(fields || {});
    if (!keys.length) {
      return '<p style="font-size:13px;color:var(--color-text-tertiary)">内訳データがありません</p>';
    }
    return _kvTableHtml(keys.map(function (k) { return [k, fields[k]]; }));
  }

  function _notesHtml(notes) {
    if (!notes || !notes.length) return "";
    return '<div style="margin-top:10px;padding:8px 10px;background:var(--color-background-tertiary);border-radius:4px">' +
      notes.map(function (n) {
        return '<div style="font-size:12px;color:var(--color-text-secondary)">・' + escHtml(n) + '</div>';
      }).join("") +
    '</div>';
  }

  function _lensSectionHtml(key, lens) {
    if (!lens) return "";
    var items = lens.items || [];
    return '<div style="margin-bottom:10px">' +
      '<div style="font-size:12.5px;font-weight:600;color:var(--color-text-secondary);margin-bottom:4px">' +
        escHtml(LENS_LABELS[key]) +
      '</div>' +
      _kvTableHtml(items.map(function (it) { return [it.label, it.value]; })) +
    '</div>';
  }

  function _positioningHtml(positioning) {
    if (!positioning || !positioning.available) return "";
    var lenses = positioning.lenses || {};
    var sections = LENS_ORDER.map(function (key) {
      return _lensSectionHtml(key, lenses[key]);
    }).join("");
    if (!sections.trim()) return "";
    return '<div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 10px;font-size:14px;color:var(--color-text-primary)">位置づけ</h4>' +
      sections +
    '</div>';
  }

  function _renderModalBody(data) {
    var body = document.getElementById("deliberation-modal-body");
    if (!body) return;
    var decomposition = data.decomposition || {};
    var typeLabel = ELEMENT_TYPE_LABELS[decomposition.element_type] || decomposition.element_type || "";
    body.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">' +
        '<span class="admin-status" style="background:var(--color-background-info);color:var(--color-text-info)">' +
          escHtml(typeLabel) +
        '</span>' +
        '<h4 style="margin:0;font-size:15px;color:var(--color-text-primary)">' + escHtml(decomposition.label || "") + '</h4>' +
      '</div>' +
      '<div style="margin-bottom:6px">' +
        '<div style="font-size:12.5px;font-weight:600;color:var(--color-text-secondary);margin-bottom:4px">内訳</div>' +
        _fieldsHtml(decomposition.fields) +
      '</div>' +
      _notesHtml(decomposition.notes) +
      _positioningHtml(data.positioning);
  }

  function _renderError(status) {
    var body = document.getElementById("deliberation-modal-body");
    if (!body) return;
    var message = "内訳の読み込みに失敗しました";
    if (status === 404) message = "この要素は見つかりませんでした";
    else if (status === 422) message = "この要素の指定が不正です（equation は document_id が必要です）";
    body.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">' + escHtml(message) + '</div>';
  }

  // ── 公開 API: openElement ────────────────────────────────────────────
  // opts = { documentId: string|null, title: string|null }
  // equation は document_id が必須（無ければ何もしない。設計書 §2 の equation 一意化の要件）。
  function openElement(elementType, elementId, opts) {
    opts = opts || {};
    if (!deps.apiFetch && !window.apiFetch) return;
    if (elementType === "equation" && !opts.documentId) return;

    _closeModal();

    var overlay = document.createElement("div");
    overlay.id = "deliberation-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:520px;max-width:760px;max-height:82vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">深く検討' +
            (opts.title ? ' — ' + escHtml(opts.title) : '') +
          '</h3>' +
          '<button id="deliberation-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          'この要素の内訳と位置づけです。表示はすべて既存データの読み出しで、確定済みの判断ではありません。' +
        '</p>' +
        '<div id="deliberation-modal-body" style="overflow-y:auto;flex:1">' +
          '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) _closeModal(); });
    document.getElementById("deliberation-modal-close").addEventListener("click", _closeModal);

    var path = "/admin/deliberation/elements/" + encodeURIComponent(elementType) + "/" + encodeURIComponent(elementId) + "/overview";
    if (elementType === "equation" && opts.documentId) {
      path += "?document_id=" + encodeURIComponent(opts.documentId);
    }

    apiFetch(path)
      .then(function (res) {
        if (!res.ok) {
          var status = res.status;
          var err = new Error("status " + status);
          err.status = status;
          throw err;
        }
        return res.json();
      })
      .then(function (data) {
        _renderModalBody(data);
      })
      .catch(function (err) {
        _renderError(err && err.status);
      });
  }

  window.Deliberation = {
    init: init,
    openElement: openElement
  };
})();
