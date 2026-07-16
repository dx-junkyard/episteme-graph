/*
 * W層（Element Deliberation Workspace / 要素検討ワークスペース）Phase 0/1/2 統合パネル。
 *
 * ES5 / IIFE。window.Deliberation を公開。admin.js の initApp() から
 * Deliberation.init({apiFetch, escHtml}) を呼んで起動する
 * （admin-lecture-studio.js / admin-assistant.js と同型の DI 注入パターン）。
 *
 * 正本: docs/features/element_deliberation_workspace_design.md
 *   §8 API:
 *     GET  /api/admin/deliberation/elements/{element_type}/{element_id}/overview
 *     POST /api/admin/deliberation/sessions
 *     GET  /api/admin/deliberation/sessions/{id}
 *     POST /api/admin/deliberation/sessions/{id}/messages
 *     GET  /api/admin/deliberation/elements/{element_type}/{element_id}/annotations
 *     POST /api/admin/deliberation/annotations/{id}/commit
 *     POST /api/admin/deliberation/annotations/{id}/dismiss
 *   §9 フロント: 各要素の「深く検討」ボタン → モーダルで
 *     左=面①内訳+面②位置づけ（overview） / 右=面③対話的検討（sessions/annotations）。
 *
 * Phase 0/1 の範囲: overview の統合表示のみ（読み取り専用）。
 * Phase 2（本増分）: 右ペインに対話（sessions/messages）+ 候補注釈カード
 *   （annotations の一覧・commit・dismiss）を追加する。
 *
 * 不変条項（設計書 §0）:
 *   W2 確定は人間・AIは候補のみ — 候補注釈は commit されるまで status='candidate'。
 *   W4 情報を落とさない — 却下も削除せず status='dismissed' で保持。モーダル再オープン時は
 *     既存注釈（candidate/committed/dismissed すべて）を GET .../annotations で復元表示する。
 *     セッション履歴自体の復元（GET /sessions/{id}）は v1 では行わない（新規対話開始のみ）。
 *     過去ログは DB に保持されており、セッション一覧 UI は後続 issue で追加する（TODO）。
 *   W5 権限 fail-closed — overview/annotations/commit/dismiss は API 側がゲートする
 *     （本ファイルは何もしない）。
 *   W6 同期パスを重くしない — 対話は1送信=1応答。セッションは最初の送信時にだけ作成する
 *     （モーダルを開いただけでは POST /sessions を呼ばない。無駄な行・コストを作らない）。
 *   W8 数値を見せない — confidence は API が返す confidence_label のみ描画し、生値・件数は
 *     出さない。§9 ポーリング禁止・都度 fetch（キャッシュしない）。
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

  // 面②の5レンズ（設計書 §4。cross_corpus は Phase 1 で追加）。null のレンズは区画ごと非表示にする。
  // cross_corpus の各 item は document_id を追加で持つが（route 層の権限フィルタ用）、
  // _lensSectionHtml は item.label / item.value しか読まないため余剰キーは自然と描画されない。
  // 同様に lens.hidden_count（隠した件数）も読まない＝表示しない（W8）。
  var LENS_LABELS = {
    intra_document: "論文内",
    cross_corpus: "コーパス横断",
    atlas: "分野の地図",
    endorsement: "承認・共有",
    epistemic: "検証・疑義"
  };
  var LENS_ORDER = ["intra_document", "cross_corpus", "atlas", "endorsement", "epistemic"];

  var ELEMENT_TYPE_LABELS = {
    figure: "図",
    theory_component: "コンポーネント",
    theory_claim: "claim",
    equation: "数式",
    shared_part: "共通部品"
  };

  // 面③ 候補注釈の kind → 日本語ラベル（設計書 §5 コミットルーティング表 / §6）。
  var ANNOTATION_KIND_LABELS = {
    meaning: "意味づけ",
    decomposition: "内訳",
    positioning_note: "位置づけメモ",
    interpretation: "解釈",
    identity: "同一性",
    standardization: "標準化"
  };

  // Phase W-β: 同一性リンク（element_identity_links）の状態ラベル。
  // KN-3: 確定は人間のみ・行削除はしない。candidate/confirmed/rejected を明示区別する（G2-W）。
  var IDENTITY_LINK_STATUS_LABELS = {
    candidate: "候補（未確定）",
    confirmed: "確定済み",
    rejected: "却下"
  };

  // 面③ 対話状態。モーダルを開くたび（_closeModal で）リセットする単一セッション分の状態
  // （1モーダル=1対話。複数セッションの並行管理は v1 では行わない）。
  var chatState = { sessionId: null, ref: null, sending: false };

  function _resetChatState() {
    chatState = { sessionId: null, ref: null, sending: false };
  }

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
    _resetChatState();
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
      _positioningHtml(data.positioning) +
      _identityLinksSectionHtml() +
      _standardizationSectionHtml(decomposition.element_type);
  }

  // ── Phase W-β: 同一性リンク（identity-links）セクション ─────────────────
  // 「この要素は別の資料・共通部品と同じものだ」という対応づけの一覧。候補
  // （candidate）・確定（confirmed）・却下（rejected）を明示区別し（G2-W）、
  // 確定・却下は教員のみが行う（KN-3）。インスタンス側の表記は書き換えない（KN-2）。
  function _identityLinksSectionHtml() {
    return '<div class="deliberation-identity-links-wrap" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 6px;font-size:14px;color:var(--color-text-primary)">同一性リンク</h4>' +
      '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px">' +
        'この要素が別の資料・共通部品と同じものだという対応づけです。候補は AI 対話または教員が作成し、' +
        '確定・却下は教員のみが行います。既存の表記は書き換えません（リンクの追加のみ）。' +
      '</p>' +
      '<div id="deliberation-identity-links"><p class="deliberation-identity-empty">読み込み中...</p></div>' +
    '</div>';
  }

  function _identityLinkRowHtml(link) {
    link = link || {};
    var statusLabel = IDENTITY_LINK_STATUS_LABELS[link.status] || link.status || "";
    var localExpr = link.local_expression || {};
    var exprLabel = localExpr.label || localExpr.notation || "";
    var pending = link.status === "candidate";
    return '<div class="deliberation-identity-link-row" data-identity-link-id="' + escHtml(link.id) + '">' +
      '<span class="deliberation-annotation-status ' + (link.status === "confirmed" ? "committed" : (link.status === "rejected" ? "dismissed" : "")) + '">' +
        escHtml(statusLabel) +
      '</span>' +
      (exprLabel ? '<div class="deliberation-annotation-body">' + escHtml(exprLabel) + '</div>' : '') +
      (link.shared_part_id ? '<div class="deliberation-annotation-reason">共通部品: ' + escHtml(link.shared_part_id) + '</div>' : '') +
      (link.instance_element_type
        ? '<div class="deliberation-annotation-reason">インスタンス: ' + escHtml(link.instance_element_type) +
          (link.instance_document_id ? ' / ' + escHtml(link.instance_document_id) : '') + '</div>'
        : '') +
      (link.reason ? '<div class="deliberation-annotation-reason">' + escHtml(link.reason) + '</div>' : '') +
      (link.confidence_label ? '<span class="deliberation-annotation-confidence">' + escHtml(link.confidence_label) + '</span>' : '') +
      (pending
        ? '<div class="deliberation-annotation-actions">' +
            '<button type="button" class="deliberation-annotation-btn commit" data-identity-action="confirm">確定</button>' +
            '<button type="button" class="deliberation-annotation-btn dismiss" data-identity-action="reject">却下</button>' +
          '</div>'
        : '') +
      '<div class="deliberation-annotation-error" style="display:none"></div>' +
    '</div>';
  }

  function _bindIdentityLinkActions(root) {
    Array.prototype.forEach.call(root.querySelectorAll("[data-identity-action]"), function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest(".deliberation-identity-link-row");
        if (!row) return;
        _decideIdentityLink(row.getAttribute("data-identity-link-id"), btn.getAttribute("data-identity-action"), row);
      });
    });
  }

  function _decideIdentityLink(linkId, action, row) {
    if (!linkId) return;
    var buttons = row.querySelectorAll("[data-identity-action]");
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    apiFetch("/admin/deliberation/identity-links/" + encodeURIComponent(linkId) + "/" + action, { method: "POST" })
      .then(_parseJsonResponse)
      .then(function (link) {
        var wrapper = document.createElement("div");
        wrapper.innerHTML = _identityLinkRowHtml(link || {});
        var newRow = wrapper.firstChild;
        if (row.parentNode) row.parentNode.replaceChild(newRow, row);
        _bindIdentityLinkActions(newRow);
      })
      .catch(function (err) {
        var errEl = row.querySelector(".deliberation-identity-link-error");
        var message = (err && err.detail) || "操作に失敗しました（既に確定・却下済みの可能性があります）";
        if (errEl) {
          errEl.style.display = "";
          errEl.innerHTML = escHtml(message);
        }
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      });
  }

  function _renderIdentityLinks(links, hiddenCount) {
    var container = document.getElementById("deliberation-identity-links");
    if (!container) return;
    links = links || [];
    if (!links.length) {
      container.innerHTML = '<p class="deliberation-identity-empty">同一性リンクはまだありません。</p>';
    } else {
      container.innerHTML = links.map(_identityLinkRowHtml).join("");
    }
    if (hiddenCount) {
      container.innerHTML += '<p class="deliberation-identity-hidden">閲覧権限のない資料に由来する ' +
        escHtml(String(hiddenCount)) + ' 件は非表示です。</p>';
    }
    _bindIdentityLinkActions(container);
  }

  // ref = chatState.ref（elementType/elementId/documentId）。shared_part は domain-scoped
  // なので別エンドポイント（GET /shared-parts/{id}/identity-links）を使う（§5 W5）。
  function _loadIdentityLinks(ref) {
    ref = ref || {};
    var container = document.getElementById("deliberation-identity-links");
    if (!container) return;
    var path;
    if (ref.elementType === "shared_part") {
      path = "/admin/deliberation/shared-parts/" + encodeURIComponent(ref.elementId) + "/identity-links";
    } else {
      path = "/admin/deliberation/elements/" + encodeURIComponent(ref.elementType) + "/" + encodeURIComponent(ref.elementId) + "/identity-links";
      if (ref.elementType === "equation" && ref.documentId) {
        path += "?document_id=" + encodeURIComponent(ref.documentId);
      }
    }
    apiFetch(path)
      .then(_parseJsonResponse)
      .then(function (data) {
        _renderIdentityLinks((data && data.identity_links) || [], data && data.hidden_count);
      })
      .catch(function () {
        // fail-soft: 同一性リンクの読み込みに失敗しても内訳・対話は継続できる
        container.innerHTML = '<p class="deliberation-identity-empty">同一性リンクの読み込みに失敗しました。</p>';
      });
  }

  // ── Phase S: 標準化度の評価（三角測量 worker の手動起動）。shared_part（共通部品）
  // にのみ表示する。評価結果は element_annotations(kind='standardization') の候補として
  // 既存の候補注釈カード（対話ペイン下）に現れる。自動確定はしない（教員の commit のみ）。
  function _standardizationSectionHtml(elementType) {
    if (elementType !== "shared_part") return "";
    return '<div class="deliberation-standardization" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 6px;font-size:14px;color:var(--color-text-primary)">標準化度</h4>' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<button type="button" id="deliberation-standardization-assess" class="deliberation-chat-send" style="padding:4px 10px;font-size:12px">標準化度を評価</button>' +
        '<span id="deliberation-standardization-note" style="font-size:12px;color:var(--color-text-tertiary)"></span>' +
      '</div>' +
      '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:6px 0 0">' +
        '評価は三角測量（LLM 事前知識・ライブラリ内の類似・コーパス内の反復）による候補です。' +
        '確定は下の候補カード（対話ペイン下）から教員が行います。自動では確定しません。' +
      '</p>' +
    '</div>';
  }

  function _bindStandardizationAssessButton(ref) {
    var btn = document.getElementById("deliberation-standardization-assess");
    if (!btn || !ref || ref.elementType !== "shared_part") return;
    btn.addEventListener("click", function () {
      btn.disabled = true;
      apiFetch("/admin/deliberation/shared-parts/" + encodeURIComponent(ref.elementId) + "/standardization/assess", {
        method: "POST"
      })
        .then(_parseJsonResponse)
        .then(function (data) {
          var note = document.getElementById("deliberation-standardization-note");
          if (note) note.textContent = (data && data.note) || "";
          // 評価は非同期（daemon thread）。間に合えば候補一覧に反映されるが、
          // 反映前でもエラーにはしない（W4: 後でモーダルを開き直しても拾える）。
          _loadAnnotations(ref.elementType, ref.elementId, ref.documentId);
        })
        .catch(function (err) {
          var note = document.getElementById("deliberation-standardization-note");
          if (note) note.textContent = (err && err.detail) || "評価の開始に失敗しました";
        })
        .then(function () {
          btn.disabled = false;
        });
    });
  }

  function _renderError(status) {
    var body = document.getElementById("deliberation-modal-body");
    if (!body) return;
    var message = "内訳の読み込みに失敗しました";
    if (status === 404) message = "この要素は見つかりませんでした";
    else if (status === 422) message = "この要素の指定が不正です（equation は document_id が必要です）";
    body.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">' + escHtml(message) + '</div>';
  }

  // ── 面③ 対話ペイン: 共通ユーティリティ ──────────────────────────────────

  // POST 系レスポンスを共通に処理する。res.ok なら body を返し、そうでなければ
  // detail（サーバ側の事実文。429=対話上限・422=未対応 kind 等）を保持した Error を投げる。
  function _parseJsonResponse(res) {
    return res.json().catch(function () { return {}; }).then(function (body) {
      if (!res.ok) {
        var err = new Error((body && body.detail) || ("status " + res.status));
        err.status = res.status;
        err.detail = body && body.detail;
        throw err;
      }
      return body;
    });
  }

  function _chatEmptyStateHtml() {
    return '<div class="deliberation-chat-empty">この要素について質問できます。まだ対話はありません。</div>';
  }

  function _appendChatMessage(role, text) {
    var container = document.getElementById("deliberation-chat-messages");
    if (!container) return;
    var empty = container.querySelector(".deliberation-chat-empty");
    if (empty) empty.remove();
    var div = document.createElement("div");
    div.className = "deliberation-chat-msg " + (role === "user" ? "user" : "ai");
    div.innerHTML = escHtml(text).replace(/\n/g, "<br>");
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  // degraded / 429 等、断定・煽りを足さずサーバの事実文だけを添える注記行。
  function _appendChatNote(text) {
    var container = document.getElementById("deliberation-chat-messages");
    if (!container) return;
    var div = document.createElement("div");
    div.className = "deliberation-chat-note";
    div.innerHTML = escHtml(text);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  function _setChatSending(flag) {
    chatState.sending = flag;
    var btn = document.getElementById("deliberation-chat-send");
    var input = document.getElementById("deliberation-chat-input");
    if (btn) {
      btn.disabled = flag;
      btn.textContent = flag ? "送信中…" : "送信";
    }
    if (input) input.disabled = flag;
  }

  // セッションは最初の送信時にだけ作成する（W6）。モーダルを開いた時点では呼ばない
  // （openElement からは直接呼ばれず、_sendChatMessage 経由でのみ到達する）。
  function _ensureChatSession() {
    if (chatState.sessionId) return Promise.resolve(chatState.sessionId);
    var ref = chatState.ref || {};
    var body = {
      scope: ref.elementType === "shared_part" ? "domain" : "document",
      element_type: ref.elementType,
      element_id: ref.elementId
    };
    if (ref.documentId) body.document_id = ref.documentId;
    if (ref.title) body.title = ref.title;
    return apiFetch("/admin/deliberation/sessions", {
      method: "POST",
      body: JSON.stringify(body)
    }).then(_parseJsonResponse).then(function (data) {
      var session = (data && data.session) || {};
      chatState.sessionId = session.id;
      return chatState.sessionId;
    });
  }

  function _sendChatMessage() {
    if (chatState.sending) return;
    var input = document.getElementById("deliberation-chat-input");
    var text = input ? input.value.trim() : "";
    if (!text) return;
    input.value = "";
    _appendChatMessage("user", text);
    _setChatSending(true);
    _ensureChatSession()
      .then(function (sessionId) {
        return apiFetch(
          "/admin/deliberation/sessions/" + encodeURIComponent(sessionId) + "/messages",
          { method: "POST", body: JSON.stringify({ content: text }) }
        );
      })
      .then(_parseJsonResponse)
      .then(function (data) {
        _appendChatMessage("ai", (data && data.reply) || "");
        if (data && data.degraded) {
          _appendChatNote("（AI 応答は生成できませんでした）");
        }
        _renderAnnotationCards((data && data.annotations) || [], { append: true });
      })
      .catch(function (err) {
        var message;
        if (err && err.status === 429) {
          // 429 = 対話上限。サーバの detail（事実文）をそのまま出す（断定・煽りを足さない）。
          message = err.detail || "本日の対話上限に達しました";
        } else {
          message = (err && err.detail) || (err && err.message) || "送信に失敗しました";
        }
        _appendChatNote(message);
      })
      .then(function () {
        _setChatSending(false);
      });
  }

  // ── 面③ 候補注釈カード ───────────────────────────────────────────────

  function _annotationStatusHtml(status) {
    if (status === "committed") return '<span class="deliberation-annotation-status committed">確定済み</span>';
    if (status === "dismissed") return '<span class="deliberation-annotation-status dismissed">却下</span>';
    return "";
  }

  function _annotationBodyText(body) {
    if (typeof body === "string") return body;
    try {
      return JSON.stringify(body);
    } catch (e) {
      return String(body);
    }
  }

  // カード DOM の中身を注釈データから（再）構築する。commit/dismiss 成功後の
  // ステータス反映にも使う（新規カード生成と同じ経路で描画を一本化する）。
  function _fillAnnotationCard(card, ann) {
    ann = ann || {};
    var kindLabel = ANNOTATION_KIND_LABELS[ann.kind] || ann.kind || "";
    var isPending = ann.status !== "committed" && ann.status !== "dismissed";
    card.setAttribute("data-annotation-id", ann.id || "");
    card.innerHTML =
      '<div class="deliberation-annotation-kind">' +
        escHtml(kindLabel) +
        (ann.confidence_label
          ? ' <span class="deliberation-annotation-confidence">' + escHtml(ann.confidence_label) + '</span>'
          : '') +
      '</div>' +
      '<div class="deliberation-annotation-body">' + escHtml(_annotationBodyText(ann.body)) + '</div>' +
      (ann.reason ? '<div class="deliberation-annotation-reason">' + escHtml(ann.reason) + '</div>' : '') +
      '<div class="deliberation-annotation-error" style="display:none"></div>' +
      _annotationStatusHtml(ann.status) +
      (isPending
        ? '<div class="deliberation-annotation-actions">' +
            '<button type="button" class="deliberation-annotation-btn commit" data-action="commit">確定</button>' +
            '<button type="button" class="deliberation-annotation-btn dismiss" data-action="dismiss">却下</button>' +
          '</div>'
        : '');

    var commitBtn = card.querySelector('[data-action="commit"]');
    var dismissBtn = card.querySelector('[data-action="dismiss"]');
    if (commitBtn) {
      commitBtn.addEventListener("click", function () {
        _decideAnnotation(ann.id, "commit", card, commitBtn, dismissBtn);
      });
    }
    if (dismissBtn) {
      dismissBtn.addEventListener("click", function () {
        _decideAnnotation(ann.id, "dismiss", card, commitBtn, dismissBtn);
      });
    }
  }

  function _buildAnnotationCard(ann) {
    var card = document.createElement("div");
    card.className = "deliberation-annotation-card";
    _fillAnnotationCard(card, ann);
    return card;
  }

  // opts.append=false（既定）なら一覧を置き換える（モーダル再オープン時の復元・W4）。
  // opts.append=true なら既存カードの下に追加する（対話1ターンの新規候補）。
  function _renderAnnotationCards(annotations, opts) {
    opts = opts || {};
    var container = document.getElementById("deliberation-chat-annotations");
    if (!container) return;
    if (!opts.append) container.innerHTML = "";
    (annotations || []).forEach(function (ann) {
      container.appendChild(_buildAnnotationCard(ann));
    });
  }

  function _decideAnnotation(id, action, card, commitBtn, dismissBtn) {
    if (!id) return;
    if (commitBtn) commitBtn.disabled = true;
    if (dismissBtn) dismissBtn.disabled = true;
    var path = "/admin/deliberation/annotations/" + encodeURIComponent(id) + "/" + action;
    apiFetch(path, { method: "POST" })
      .then(_parseJsonResponse)
      .then(function (data) {
        _fillAnnotationCard(card, (data && data.annotation) || {});
      })
      .catch(function (err) {
        var errEl = card.querySelector(".deliberation-annotation-error");
        var message = (err && err.detail) ||
          (action === "commit" ? "この候補は確定できませんでした（このコミットは未対応の可能性があります）" : "却下できませんでした");
        if (errEl) {
          errEl.style.display = "";
          errEl.innerHTML = escHtml(message);
        }
        if (action === "commit") {
          // commit 失敗（422=このkindのコミット未対応 等）は却下のみ可能にする。
          if (dismissBtn) dismissBtn.disabled = false;
        } else {
          // dismiss 失敗は両方を再試行可能にする。
          if (commitBtn) commitBtn.disabled = false;
          if (dismissBtn) dismissBtn.disabled = false;
        }
      });
  }

  // モーダル再オープン時に既存注釈（candidate/committed/dismissed すべて）を復元する（W4）。
  function _loadAnnotations(elementType, elementId, documentId) {
    var path = "/admin/deliberation/elements/" + encodeURIComponent(elementType) + "/" + encodeURIComponent(elementId) + "/annotations";
    if (elementType === "equation" && documentId) {
      path += "?document_id=" + encodeURIComponent(documentId);
    }
    apiFetch(path)
      .then(_parseJsonResponse)
      .then(function (data) {
        _renderAnnotationCards((data && data.annotations) || [], { append: false });
      })
      .catch(function () {
        // 既存注釈の復元に失敗しても対話自体は継続できる（fail-soft）。
      });
  }

  // ── 公開 API: openElement ────────────────────────────────────────────
  // opts = { documentId: string|null, title: string|null }
  // equation は document_id が必須（無ければ何もしない。設計書 §2 の equation 一意化の要件）。
  function openElement(elementType, elementId, opts) {
    opts = opts || {};
    if (!deps.apiFetch && !window.apiFetch) return;
    if (elementType === "equation" && !opts.documentId) return;

    _closeModal();
    chatState.ref = {
      elementType: elementType,
      elementId: elementId,
      documentId: opts.documentId || null,
      title: opts.title || null
    };

    var overlay = document.createElement("div");
    overlay.id = "deliberation-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:760px;max-width:1080px;width:92vw;max-height:86vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">深く検討' +
            (opts.title ? ' — ' + escHtml(opts.title) : '') +
          '</h3>' +
          '<button id="deliberation-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          'この要素の内訳と位置づけです。表示はすべて既存データの読み出しで、確定済みの判断ではありません。' +
        '</p>' +
        '<div class="deliberation-modal-columns">' +
          '<div class="deliberation-modal-left">' +
            '<div id="deliberation-modal-body">' +
              '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
            '</div>' +
          '</div>' +
          '<div class="deliberation-chat-pane">' +
            '<div class="deliberation-chat-messages" id="deliberation-chat-messages">' +
              _chatEmptyStateHtml() +
            '</div>' +
            '<div class="deliberation-chat-annotations" id="deliberation-chat-annotations"></div>' +
            '<div class="deliberation-chat-inputrow">' +
              '<textarea id="deliberation-chat-input" class="deliberation-chat-input" rows="2" placeholder="この要素について質問..."></textarea>' +
              '<button id="deliberation-chat-send" type="button" class="deliberation-chat-send">送信</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) _closeModal(); });
    document.getElementById("deliberation-modal-close").addEventListener("click", _closeModal);

    var chatInput = document.getElementById("deliberation-chat-input");
    var chatSendBtn = document.getElementById("deliberation-chat-send");
    if (chatSendBtn) chatSendBtn.addEventListener("click", _sendChatMessage);
    if (chatInput) {
      chatInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
          e.preventDefault();
          _sendChatMessage();
        }
      });
    }

    // 面③の既存候補注釈を復元する（GET のみ・DB 非変更）。対話セッション自体は
    // ここでは作らない（POST /sessions は最初の送信時にのみ _ensureChatSession が呼ぶ）。
    _loadAnnotations(elementType, elementId, opts.documentId);

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
        _bindStandardizationAssessButton(chatState.ref);
        _loadIdentityLinks(chatState.ref);
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
