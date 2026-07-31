/*
 * 教材図スタジオ（Teaching Figure Studio）— 図の生成・調整・挿入モーダル。
 *
 * ES5 / IIFE。window.FigureStudio を公開。原稿スタジオ（admin-lecture-studio.js）の
 * init() から FigureStudio.init({apiFetch, apiFetchRaw, escHtml}) を呼んで起動する
 * （deliberation.js / admin-assistant.js と同型の DI 注入 + window フォールバック）。
 *
 * 正本: docs/features/teaching_figure_studio_design.md
 *   §6.1 フロント（2ペインモーダル・状態はブラウザ内のみ）
 *   §6.2 起動導線（ツールバーの [🖼 図を挿入] / 提案カードの [この図を作る] / 既存図タブ）
 *   §7.1b 採用時の参照登録（本文挿入と同時に topic へ登録する。サーバ側で行う）
 *   §7.4 回収（retire）前の事実文確認と、回収後の学習者側の見え方
 *   §8 API:
 *     POST  /api/admin/courses/{course_id}/figure-studio/turn
 *     POST  /api/admin/courses/{course_id}/teaching-figures
 *     PATCH /api/admin/courses/{course_id}/teaching-figures/{fid}
 *           （caption 修正 / status 遷移 / register_topic_id によるトピック登録）
 *     GET   /api/admin/courses/{course_id}/teaching-figures
 *     GET   /api/admin/courses/{course_id}/teaching-figures/{fid}/image
 *
 * 不変条項（本 UI での担保）:
 *   FG2 確定は人間 — 生成した図は「採用して挿入」を押すまで教材に入らず、さらに
 *     トピックを保存するまで学習者には出ない（二重ゲート）。その事実文を採用直後に出す。
 *   FG3 SVG は <img>（blob URL）でしか描画しない — innerHTML への SVG 直挿入は
 *     絶対にしない（サーバ側サニタイザとの二重防御）。
 *   FG5/FG8 数値を出さない・断定しない — 429 / degraded はサーバの detail（事実文）を
 *     そのまま表示し、上限値や信頼度スコアを画面に出さない。
 *   FG6 同期パスを重くしない — 1送信=1ターン。ポーリングしない。会話履歴は
 *     永続化せず、モーダルを閉じたら破棄する（成果物＝SVG のみ DB に残る）。
 */
(function () {
  "use strict";

  // 注入される依存（疎結合）。未注入なら window グローバルへフォールバックする。
  var deps = { apiFetch: null, apiFetchRaw: null, escHtml: null };
  var initialized = false;

  var MODAL_ID = "figure-studio-modal";

  // 図タイプ語彙（設計書 §5.2）の表示ラベル。サーバの schema.py が正本で、ここは
  // 表示用の写像のみ。未知の値は原文へフォールバックする（情報を落とさない）。
  var FIGURE_KIND_LABELS = {
    concept_map: "概念関係図",
    process_flow: "プロセス図",
    structure_diagram: "構造・構成図",
    comparison: "対比図",
    timeline: "時系列図",
    coordinate: "座標・幾何図",
    data_plot_schematic: "模式グラフ",
    other: "その他"
  };

  var TEACHING_STATUS_LABELS = {
    draft: "下書き",
    adopted: "採用済み",
    retired: "回収済み"
  };

  // 採用直後に出す事実文（設計書 §6.1 / §7.5）。煽らず、起きることだけを書く。
  var ADOPT_NOTICE =
    "保存するとこのトピックの生成済み音声は作り直しになります。" +
    "トピックを保存するまで学習者には表示されません。";

  var DRAFT_NOTICE = "下書きとして保存しました。教材には挿入していません（既存の図から選ぶタブに出ます）。";

  var DRAFT_INSERT_NOTICE = "下書きの図は、採用済みにするまで受講者には表示されません。";

  var RETIRED_INSERT_NOTICE =
    "回収済みの図は受講者に表示されません。挿入するには先に [採用に戻す] を押してください。";

  // 配信スナップショット（MinIO）の更新に失敗したときの事実文。保存自体は成立している。
  var SNAPSHOT_FAILED_NOTICE =
    "保存されました（配信用の画像の更新に失敗したため、再保存をおすすめします）。";

  // 未保存の作業を捨てる前の確認（背景クリック / × / Escape）。
  var CLOSE_CONFIRM_MESSAGE = "作成中の図は保存されていません。閉じますか？";

  // モーダル1つ分の状態。すべてモジュール内メモリで、閉じたら破棄する（§6.1）。
  var studio = null;

  function _emptyState() {
    return {
      courseId: null,
      topicId: "",
      insertTarget: null,
      preset: null,
      existingOnly: false,
      onInsert: null,
      onClose: null,
      countTopicReferences: null,
      figuresIndex: {},
      documentIds: [],
      tab: "generate",
      history: [],
      currentSvg: "",
      title: "",
      caption: "",
      figureKind: "",
      suggestionId: null,
      selectedModel: null,
      sending: false,
      saving: false,
      adoptedFigureId: null,
      savedDraftId: null,
      cardBusy: false,
      objectUrls: [],
      previewUrl: null,
      existingLoading: false,
      existingLoaded: false,
      existingItems: [],
      insertedIds: {},
      requestId: 0
    };
  }

  // ── 依存ラッパ ────────────────────────────────────────────────────────
  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    if (typeof fn !== "function") {
      return Promise.reject(new Error("FigureStudio: apiFetch is not available (init() not called yet)"));
    }
    return fn(path, opts);
  }

  function apiFetchRaw(path, opts) {
    var fn = deps.apiFetchRaw || window.apiFetchRaw || deps.apiFetch || window.apiFetch;
    if (typeof fn !== "function") {
      return Promise.reject(new Error("FigureStudio: apiFetchRaw is not available (init() not called yet)"));
    }
    return fn(path, opts);
  }

  function escHtml(s) {
    var fn = deps.escHtml || window.escHtml;
    if (fn) return fn(s);
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function _parseJson(res) {
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

  function _errorText(err, fallback) {
    if (err && err.detail) return err.detail;
    if (err && err.message) return err.message;
    return fallback || "処理に失敗しました";
  }

  function _kindLabel(kind) {
    if (!kind) return "";
    return FIGURE_KIND_LABELS[kind] || kind;
  }

  function _shorten(text, limit) {
    var value = String(text === null || text === undefined ? "" : text).replace(/\s+/g, " ");
    var max = limit || 120;
    if (value.length <= max) return value;
    return value.substring(0, max) + "…";
  }

  // ── モーダルの生成・破棄 ──────────────────────────────────────────────
  function _revokeObjectUrls() {
    if (!studio) return;
    studio.objectUrls.forEach(function (url) {
      try { URL.revokeObjectURL(url); } catch (e) { /* noop */ }
    });
    studio.objectUrls = [];
  }

  function _closeModal() {
    _revokeObjectUrls();
    var modal = document.getElementById(MODAL_ID);
    var onClose = modal && studio ? studio.onClose : null;
    document.removeEventListener("keydown", _onModalKeyDown);
    if (modal) modal.remove();
    studio = _emptyState();
    // 呼び出し元の後始末（隠れた textarea へのフォーカス復帰など）は閉じた後に行う。
    if (typeof onClose === "function") onClose();
  }

  // 未保存の図があるときだけ確認する（保存済み・図なしはそのまま閉じる）。
  // 確認は admin.js の共通2段確認モーダル（z-index 9999）を使い、無ければ confirm へ。
  function _requestClose() {
    var unsaved = !!(studio && studio.currentSvg && !studio.adoptedFigureId && !studio.savedDraftId);
    if (!unsaved) { _closeModal(); return; }
    if (window.AdminDangerConfirm && typeof window.AdminDangerConfirm.open === "function") {
      window.AdminDangerConfirm.open({
        title: "教材図スタジオを閉じる",
        message: CLOSE_CONFIRM_MESSAGE,
        confirmLabel: "閉じる"
      }, _closeModal);
      return;
    }
    if (window.confirm(CLOSE_CONFIRM_MESSAGE)) _closeModal();
  }

  function _onModalKeyDown(e) {
    if (!document.getElementById(MODAL_ID)) return;
    // 確認モーダル（admin.js の2段確認）が前面にあるときは、そちらの操作に任せる。
    if (document.getElementById("danger-confirm-modal")) return;
    if (e.key === "Escape" || e.keyCode === 27) {
      e.preventDefault();
      _requestClose();
    }
  }

  function _tabsHtml() {
    if (studio.existingOnly) return "";
    return '<div class="figure-studio-tabs">' +
      '<button type="button" class="ls-mini-tab' + (studio.tab === "generate" ? " active" : "") + '" ' +
        'data-figure-studio-tab="generate">AIで図を作る</button>' +
      '<button type="button" class="ls-mini-tab' + (studio.tab === "existing" ? " active" : "") + '" ' +
        'data-figure-studio-tab="existing" data-ui-anchor="lecture-studio.figure-studio-modal">既存の図から選ぶ</button>' +
    '</div>';
  }

  function _generatePaneHtml() {
    return '<div class="deliberation-modal-columns" id="figure-studio-generate"' +
        (studio.tab === "generate" ? "" : " hidden") + '>' +
      '<div class="figure-studio-preview-pane">' +
        '<div class="figure-studio-preview-frame">' +
          '<img id="figure-studio-preview-img" class="figure-studio-preview-img" alt="生成した図のプレビュー" hidden>' +
          '<div id="figure-studio-preview-empty" class="figure-studio-preview-empty">' +
            'まだ図はありません。右の欄に「どんな図がほしいか」を書いて送ると、ここにプレビューが出ます。' +
          '</div>' +
        '</div>' +
        '<div class="figure-studio-meta">' +
          '<label class="figure-studio-field">' +
            '<span>図のタイトル</span>' +
            '<input type="text" id="figure-studio-title" class="figure-studio-input" ' +
              'placeholder="教材内での見出し（任意）">' +
          '</label>' +
          '<label class="figure-studio-field">' +
            '<span>キャプション（受講者に表示されます）</span>' +
            '<textarea id="figure-studio-caption" class="figure-studio-input" rows="2" ' +
              'placeholder="図の説明"></textarea>' +
          '</label>' +
          '<div class="figure-studio-kind" id="figure-studio-kind"></div>' +
        '</div>' +
      '</div>' +
      '<div class="deliberation-chat-pane">' +
        '<div class="figure-studio-chat-head">' +
          '<span>AIとの相談</span>' +
          '<span id="figure-studio-model-chip"></span>' +
        '</div>' +
        '<div class="deliberation-chat-messages" id="figure-studio-chat-messages">' +
          '<div class="deliberation-chat-empty">' +
            '例: 「熱平衡に達するまでの過程を3段階のプロセス図で」「ラベルを日本語にして」' +
          '</div>' +
        '</div>' +
        '<div class="deliberation-chat-inputrow">' +
          '<textarea id="figure-studio-input" class="deliberation-chat-input" rows="2" ' +
            'placeholder="どんな図にするか、どこを直すかを書いてください"></textarea>' +
          '<button type="button" id="figure-studio-send" class="deliberation-chat-send" ' +
            'data-ui-anchor="lecture-studio.figure-studio-send">送信</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  function _existingPaneHtml() {
    return '<div class="figure-studio-existing" id="figure-studio-existing"' +
        (studio.tab === "existing" ? "" : " hidden") + '>' +
      '<div class="figure-studio-existing-list" id="figure-studio-existing-list">' +
        '<div class="figure-studio-muted">読み込み中…</div>' +
      '</div>' +
    '</div>';
  }

  function _footerHtml() {
    return '<div class="figure-studio-footer">' +
      '<div class="figure-studio-status" id="figure-studio-status"></div>' +
      '<div class="figure-studio-actions" id="figure-studio-actions"' +
          (studio.existingOnly ? " hidden" : "") + '>' +
        '<button type="button" id="figure-studio-adopt" class="admin-action-btn figure-studio-primary" ' +
          'data-ui-anchor="lecture-studio.figure-studio-adopt" disabled>採用して挿入</button>' +
        '<button type="button" id="figure-studio-save-draft" class="admin-action-btn" disabled>下書きとして保存</button>' +
        '<button type="button" id="figure-studio-discard" class="admin-action-btn" ' +
          'title="この図の作業内容を破棄して閉じます（保存した図は残ります）">破棄</button>' +
      '</div>' +
    '</div>';
  }

  function _renderModal() {
    var overlay = document.createElement("div");
    overlay.id = MODAL_ID;
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;" +
      "background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9500";
    overlay.innerHTML =
      '<div class="figure-studio-dialog" data-ui-anchor="lecture-studio.figure-studio-modal">' +
        '<div class="figure-studio-head">' +
          '<h3 class="figure-studio-title-text">教材図スタジオ</h3>' +
          '<button type="button" id="figure-studio-close" class="figure-studio-close" aria-label="閉じる">&times;</button>' +
        '</div>' +
        '<p class="figure-studio-note">' +
          'ここで作った図は候補です。[採用して挿入] を押すと教材本文に ![[figure:id]] が入り、' +
          'トピックを保存したあとに受講者へ表示されます。' +
        '</p>' +
        _tabsHtml() +
        '<div class="figure-studio-body">' +
          _generatePaneHtml() +
          _existingPaneHtml() +
        '</div>' +
        _footerHtml() +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) _requestClose(); });
    var closeBtn = document.getElementById("figure-studio-close");
    if (closeBtn) closeBtn.addEventListener("click", _requestClose);
    document.addEventListener("keydown", _onModalKeyDown);

    Array.prototype.forEach.call(
      overlay.querySelectorAll("[data-figure-studio-tab]"),
      function (btn) {
        btn.addEventListener("click", function () {
          _switchTab(btn.getAttribute("data-figure-studio-tab") || "generate");
        });
      }
    );

    var input = document.getElementById("figure-studio-input");
    var sendBtn = document.getElementById("figure-studio-send");
    if (sendBtn) sendBtn.addEventListener("click", _sendTurn);
    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
          e.preventDefault();
          _sendTurn();
        }
      });
    }

    var titleEl = document.getElementById("figure-studio-title");
    var captionEl = document.getElementById("figure-studio-caption");
    if (titleEl) {
      titleEl.value = studio.title || "";
      titleEl.addEventListener("input", function () { studio.title = titleEl.value; });
    }
    if (captionEl) {
      captionEl.value = studio.caption || "";
      captionEl.addEventListener("input", function () { studio.caption = captionEl.value; });
    }

    var adoptBtn = document.getElementById("figure-studio-adopt");
    var draftBtn = document.getElementById("figure-studio-save-draft");
    var discardBtn = document.getElementById("figure-studio-discard");
    if (adoptBtn) adoptBtn.addEventListener("click", function () { _saveFigure(true); });
    if (draftBtn) draftBtn.addEventListener("click", function () { _saveFigure(false); });
    // [破棄] は「捨てて閉じる」ことが明示された操作なので確認を挟まない。
    if (discardBtn) discardBtn.addEventListener("click", _closeModal);

    // M層 Phase 3（§6.5 の共通部品）: この対話1回だけのモデル上書き。
    // カタログが無い環境では表示のみに縮退する（AdminLlmModels 側が fail-closed）。
    var chipMount = document.getElementById("figure-studio-model-chip");
    if (chipMount && window.AdminLlmModels && window.AdminLlmModels.createModelChip) {
      window.AdminLlmModels.createModelChip({
        sceneKey: "figure_studio",
        mountEl: chipMount,
        compact: true,
        onChange: function (model) { studio.selectedModel = model; }
      });
    }

    _renderKindLabel();
    _updateActionButtons();
  }

  function _switchTab(tab) {
    if (!studio) return;
    studio.tab = tab === "existing" ? "existing" : "generate";
    var modal = document.getElementById(MODAL_ID);
    if (!modal) return;
    Array.prototype.forEach.call(modal.querySelectorAll("[data-figure-studio-tab]"), function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-figure-studio-tab") === studio.tab);
    });
    var generatePane = document.getElementById("figure-studio-generate");
    var existingPane = document.getElementById("figure-studio-existing");
    var actions = document.getElementById("figure-studio-actions");
    if (generatePane) generatePane.hidden = studio.tab !== "generate";
    if (existingPane) existingPane.hidden = studio.tab !== "existing";
    if (actions) actions.hidden = studio.tab !== "generate";
    if (studio.tab === "existing") _loadExistingFigures();
  }

  // ── プレビュー（blob + <img> のみ。innerHTML へ SVG を入れない）─────────
  function _setPreviewSvg(svg) {
    var img = document.getElementById("figure-studio-preview-img");
    var empty = document.getElementById("figure-studio-preview-empty");
    if (!img) return;
    var blob;
    try {
      blob = new Blob([String(svg || "")], { type: "image/svg+xml" });
    } catch (e) {
      _appendNote("図のプレビューを作成できませんでした。");
      return;
    }
    // 差し替え時は前のプレビュー URL を解放する（何ターンも回すと blob が溜まるため。
    // 閉じたときの一括解放だけに頼らない）。
    if (studio.previewUrl) {
      var stale = studio.previewUrl;
      var pos = studio.objectUrls.indexOf(stale);
      if (pos >= 0) studio.objectUrls.splice(pos, 1);
      try { URL.revokeObjectURL(stale); } catch (e2) { /* noop */ }
      studio.previewUrl = null;
    }
    var url = URL.createObjectURL(blob);
    studio.objectUrls.push(url);
    studio.previewUrl = url;
    img.src = url;
    img.hidden = false;
    if (empty) empty.hidden = true;
  }

  function _renderKindLabel() {
    var el = document.getElementById("figure-studio-kind");
    if (!el) return;
    var label = _kindLabel(studio.figureKind);
    el.innerHTML = label
      ? '図の種類: <strong>' + escHtml(label) + '</strong>'
      : '';
  }

  // 保存済み（採用 / 下書き）の図をもう一度押せると、行・トピック登録・本文の埋め込みが
  // 二重になる。保存後は無効化し、次のターンで図が変わったら（= 別の図になったら）
  // ふたたび保存できるようにする（_sendTurn で保存済みフラグを落とす）。
  function _updateActionButtons() {
    var adoptBtn = document.getElementById("figure-studio-adopt");
    var draftBtn = document.getElementById("figure-studio-save-draft");
    var hasSvg = !!(studio && studio.currentSvg);
    var busy = !!(studio && (studio.saving || studio.sending));
    var saved = !!(studio && (studio.adoptedFigureId || studio.savedDraftId));
    if (adoptBtn) {
      adoptBtn.disabled = !hasSvg || busy || saved;
      adoptBtn.textContent = studio && studio.adoptedFigureId ? "採用済み" : "採用して挿入";
    }
    if (draftBtn) {
      draftBtn.disabled = !hasSvg || busy || saved;
      draftBtn.textContent = studio && studio.savedDraftId ? "保存済み" : "下書きとして保存";
    }
  }

  function _setStatus(text) {
    var el = document.getElementById("figure-studio-status");
    if (el) el.textContent = text || "";
  }

  // ── チャット表示 ──────────────────────────────────────────────────────
  function _appendMessage(role, text) {
    var container = document.getElementById("figure-studio-chat-messages");
    if (!container) return;
    var empty = container.querySelector(".deliberation-chat-empty");
    if (empty) empty.remove();
    var div = document.createElement("div");
    div.className = "deliberation-chat-msg " + (role === "user" ? "user" : "ai");
    div.innerHTML = escHtml(text).replace(/\n/g, "<br>");
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  // degraded / 429 等はサーバの事実文だけを添える（断定・煽り・数値を足さない）。
  function _appendNote(text) {
    var container = document.getElementById("figure-studio-chat-messages");
    if (!container) return;
    var div = document.createElement("div");
    div.className = "deliberation-chat-note";
    div.innerHTML = escHtml(text);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  function _setSending(flag) {
    studio.sending = flag;
    var btn = document.getElementById("figure-studio-send");
    var input = document.getElementById("figure-studio-input");
    if (btn) {
      btn.disabled = flag;
      btn.textContent = flag ? "送信中…" : "送信";
    }
    if (input) input.disabled = flag;
    _updateActionButtons();
  }

  // ── 対話1ターン（POST .../figure-studio/turn）───────────────────────────
  function _sendTurn(presetInstruction) {
    if (!studio || studio.sending) return;
    var input = document.getElementById("figure-studio-input");
    var text = presetInstruction || (input ? input.value.trim() : "");
    if (!text) return;
    if (!presetInstruction && input) input.value = "";
    _appendMessage("user", text);
    _setSending(true);

    var body = {
      history: studio.history.slice(0),
      instruction: text,
      current_svg: studio.currentSvg || "",
      topic_id: studio.topicId || ""
    };
    if (studio.suggestionId) body.suggestion_id = studio.suggestionId;
    if (studio.selectedModel) body.model = studio.selectedModel;

    var requestId = ++studio.requestId;
    apiFetch("/admin/courses/" + encodeURIComponent(studio.courseId) + "/figure-studio/turn", {
      method: "POST",
      body: JSON.stringify(body)
    })
      .then(_parseJson)
      .then(function (data) {
        if (!studio || requestId !== studio.requestId || !document.getElementById(MODAL_ID)) return;
        var reply = (data && data.reply) || "";
        if (reply) _appendMessage("ai", reply);
        studio.history.push({ role: "user", content: text });
        studio.history.push({ role: "assistant", content: reply });
        // svg_source が空文字のターンは「変更なし」。前回のプレビューを保持する。
        if (data && data.svg_source) {
          studio.currentSvg = data.svg_source;
          // 図が変わったら別の図として保存できる（保存済みフラグを落とす）。
          studio.adoptedFigureId = null;
          studio.savedDraftId = null;
          _setPreviewSvg(data.svg_source);
        } else {
          _appendNote("この応答では図は変わっていません。");
        }
        if (data && data.title && !studio.title) {
          studio.title = data.title;
          var titleEl = document.getElementById("figure-studio-title");
          if (titleEl) titleEl.value = studio.title;
        }
        if (data && data.caption && !studio.caption) {
          studio.caption = data.caption;
          var captionEl = document.getElementById("figure-studio-caption");
          if (captionEl) captionEl.value = studio.caption;
        }
        if (data && data.figure_kind) {
          studio.figureKind = data.figure_kind;
          _renderKindLabel();
        }
        if (data && data.degraded) {
          _appendNote("（AI 応答は生成できませんでした）");
        }
      })
      .catch(function (err) {
        if (!studio || requestId !== studio.requestId) return;
        _appendNote(_errorText(err, "送信に失敗しました"));
      })
      .then(function () {
        if (!studio || !document.getElementById(MODAL_ID)) return;
        _setSending(false);
      });
  }

  // ── 保存（採用 / 下書き）──────────────────────────────────────────────
  function _saveFigure(adopt) {
    if (!studio || studio.saving || !studio.currentSvg) return;
    studio.saving = true;
    _updateActionButtons();
    _setStatus(adopt ? "採用しています…" : "下書きを保存しています…");
    var body = {
      svg_source: studio.currentSvg,
      title: studio.title || "",
      caption: studio.caption || "",
      figure_kind: studio.figureKind || "other",
      topic_id: studio.topicId || "",
      adopt: !!adopt
    };
    if (studio.suggestionId) body.suggestion_id = studio.suggestionId;

    apiFetch("/admin/courses/" + encodeURIComponent(studio.courseId) + "/teaching-figures", {
      method: "POST",
      body: JSON.stringify(body)
    })
      .then(_parseJson)
      .then(function (data) {
        if (!studio) return;
        var figureId = (data && data.figure_id) || "";
        // 配信スナップショットの失敗はサーバが正直に返す。保存は成立しているので
        // エラーにせず、再保存を勧める事実文を添える。
        var snapshotNote = data && data.image_snapshot_failed ? " " + SNAPSHOT_FAILED_NOTICE : "";
        studio.existingLoaded = false;
        if (!adopt) {
          studio.savedDraftId = figureId;
          _setStatus(DRAFT_NOTICE + snapshotNote);
          return;
        }
        studio.adoptedFigureId = figureId;
        if (figureId && typeof studio.onInsert === "function") {
          studio.onInsert(figureId, "![[figure:" + figureId + "]]", data || null);
        }
        _setStatus(ADOPT_NOTICE + snapshotNote);
      })
      .catch(function (err) {
        _setStatus(_errorText(err, "保存に失敗しました"));
      })
      .then(function () {
        if (!studio) return;
        studio.saving = false;
        _updateActionButtons();
      });
  }

  // ── 既存の図から選ぶ（LLM 0回）────────────────────────────────────────
  function _normalizeImagePath(url) {
    var path = String(url || "");
    if (!path) return "";
    if (path.indexOf("/api/") === 0) path = path.substring(4);
    return path;
  }

  function _itemsFromFiguresIndex() {
    var out = [];
    var index = studio.figuresIndex || {};
    Object.keys(index).forEach(function (figureId) {
      var entry = index[figureId] || {};
      out.push({
        id: figureId,
        caption: entry.caption || "",
        teaching: !!entry.teaching,
        documentId: entry.document_id || "",
        imageUrl: _normalizeImagePath(entry.image_url),
        status: ""
      });
    });
    return out;
  }

  function _figureImagePath(item) {
    if (item.imageUrl) return item.imageUrl;
    if (item.teaching) {
      return "/admin/courses/" + encodeURIComponent(studio.courseId) +
        "/teaching-figures/" + encodeURIComponent(item.id) + "/image";
    }
    if (item.documentId) {
      return "/admin/documents/" + encodeURIComponent(item.documentId) +
        "/figures/" + encodeURIComponent(item.id) + "/image";
    }
    return "";
  }

  function _mergeItems(base, extra) {
    var byId = {};
    var out = [];
    base.concat(extra).forEach(function (item) {
      if (!item || !item.id) return;
      var known = byId[item.id];
      if (known) {
        // 後から来た情報（caption / status / 由来）で欠けている分だけ補う。
        if (!known.caption && item.caption) known.caption = item.caption;
        if (!known.status && item.status) known.status = item.status;
        if (!known.documentId && item.documentId) known.documentId = item.documentId;
        if (item.teaching) known.teaching = true;
        if (!known.kind && item.kind) known.kind = item.kind;
        return;
      }
      byId[item.id] = item;
      out.push(item);
    });
    return out;
  }

  function _loadExistingFigures() {
    if (!studio || studio.existingLoading) return;
    if (studio.existingLoaded) { _renderExistingList(); return; }
    studio.existingLoading = true;
    var indexItems = _itemsFromFiguresIndex();
    var requests = [];

    // このコースの生成図（採用済み + 下書きのストック）。
    requests.push(
      apiFetch("/admin/courses/" + encodeURIComponent(studio.courseId) + "/teaching-figures")
        .then(_parseJson)
        .then(function (data) {
          return ((data && data.figures) || []).map(function (fig) {
            return {
              id: fig.id,
              caption: fig.caption || fig.title || "",
              teaching: true,
              documentId: "",
              imageUrl: "",
              kind: fig.figure_kind || "",
              status: fig.status || ""
            };
          });
        })
        .catch(function () { return []; })
    );

    // コース構造の figures_index に抽出図が載っていない場合の保険（設計書 §6.2-3）。
    var hasExtracted = indexItems.some(function (item) { return !item.teaching; });
    if (!hasExtracted) {
      (studio.documentIds || []).slice(0, 5).forEach(function (documentId) {
        if (!documentId) return;
        requests.push(
          apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/figures")
            .then(_parseJson)
            .then(function (data) {
              return ((data && data.figures) || []).map(function (fig) {
                return {
                  id: fig.id,
                  caption: fig.caption_text || fig.figure_label || fig.figure_key || "",
                  teaching: false,
                  documentId: documentId,
                  imageUrl: _normalizeImagePath(fig.image_url),
                  status: ""
                };
              });
            })
            .catch(function () { return []; })
        );
      });
    }

    Promise.all(requests).then(function (results) {
      if (!studio || !document.getElementById(MODAL_ID)) return;
      var extra = [];
      results.forEach(function (list) { extra = extra.concat(list || []); });
      studio.existingItems = _mergeItems(indexItems, extra);
      studio.existingLoaded = true;
      studio.existingLoading = false;
      _renderExistingList();
    });
  }

  function _existingCardHtml(item, idx) {
    var statusLabel = item.status ? (TEACHING_STATUS_LABELS[item.status] || item.status) : "";
    var originLabel = item.teaching ? "この教材で作った図" : "論文から抽出した図";
    var inserted = !!studio.insertedIds[item.id];
    var busy = !!(studio && studio.cardBusy);
    var retired = item.teaching && item.status === "retired";
    var captionText = item.caption || "";
    var html = '<div class="figure-studio-card" data-figure-card-idx="' + idx + '">' +
      '<div class="figure-studio-card-thumb">' +
        '<img class="figure-studio-card-img" data-figure-thumb-idx="' + idx + '" alt="" hidden>' +
        '<span class="figure-studio-card-placeholder">読込中…</span>' +
      '</div>' +
      '<div class="figure-studio-card-body">' +
        '<div class="figure-studio-card-meta">' +
          escHtml(originLabel) +
          (statusLabel ? ' / ' + escHtml(statusLabel) : '') +
          (item.kind ? ' / ' + escHtml(_kindLabel(item.kind)) : '') +
        '</div>' +
        '<div class="figure-studio-card-caption">' +
          escHtml(_shorten(captionText, 160) || "(キャプションなし)") +
        '</div>';

    // キャプションのインライン編集（生成図のみ。PATCH で保存し、応答の値で表示を更新する
    // — サーバが「（模式図）」を付けることがあるため、送った文字列では上書きしない）。
    if (item.teaching) {
      // 既存クラス（.figure-studio-actions = flex + gap、[hidden] で非表示）を再利用する。
      html += '<div class="figure-studio-actions figure-studio-card-caption-edit" ' +
          'data-figure-caption-row-idx="' + idx + '" hidden>' +
        '<input type="text" class="figure-studio-input" data-figure-caption-input-idx="' + idx + '" ' +
          'value="' + escHtml(captionText) + '">' +
        '<button type="button" class="admin-action-btn" data-figure-caption-save-idx="' + idx + '">' +
          'キャプションを保存</button>' +
        '<button type="button" class="admin-action-btn" data-figure-caption-cancel-idx="' + idx + '">やめる</button>' +
      '</div>';
    }

    if (item.status === "draft") {
      html += '<div class="figure-studio-card-note">' + escHtml(DRAFT_INSERT_NOTICE) + '</div>';
    } else if (retired) {
      html += '<div class="figure-studio-card-note">' + escHtml(RETIRED_INSERT_NOTICE) + '</div>';
    }
    if (item.notice) {
      html += '<div class="figure-studio-card-note" data-figure-card-notice-idx="' + idx + '">' +
        escHtml(item.notice) + '</div>';
    }

    html += '<div class="figure-studio-actions figure-studio-card-actions">';
    if (!retired) {
      html += '<button type="button" class="admin-action-btn figure-studio-insert-btn" ' +
        'data-figure-insert-idx="' + idx + '" ' +
        'data-ui-anchor="lecture-studio.figure-studio-insert"' +
        (inserted || busy ? ' disabled' : '') + '>' +
        (inserted ? '挿入済み' : (item.status === "draft" ? '採用して挿入' : '教材に挿入')) +
      '</button>';
    }
    if (item.teaching) {
      html += '<button type="button" class="admin-action-btn" ' +
        'data-figure-caption-edit-idx="' + idx + '" ' +
        'data-ui-anchor="lecture-studio.figure-studio-caption"' + (busy ? ' disabled' : '') + '>' +
        'キャプションを直す</button>';
      if (item.status === "adopted") {
        html += '<button type="button" class="admin-action-btn" ' +
          'data-figure-retire-idx="' + idx + '" ' +
          'data-ui-anchor="lecture-studio.figure-studio-retire"' + (busy ? ' disabled' : '') + '>' +
          '回収する</button>';
      } else if (item.status === "retired") {
        html += '<button type="button" class="admin-action-btn" ' +
          'data-figure-restore-idx="' + idx + '" ' +
          'data-ui-anchor="lecture-studio.figure-studio-restore"' + (busy ? ' disabled' : '') + '>' +
          '採用に戻す</button>';
      }
    }
    html += '</div>' +
      '</div>' +
    '</div>';
    return html;
  }

  function _renderExistingList() {
    var container = document.getElementById("figure-studio-existing-list");
    if (!container) return;
    var items = studio.existingItems || [];
    if (!items.length) {
      container.innerHTML = '<div class="figure-studio-muted">' +
        'このコースから参照できる図はまだありません。' +
        '</div>';
      return;
    }
    container.innerHTML = items.map(function (item, idx) {
      return _existingCardHtml(item, idx);
    }).join("");

    _bindCardButtons(container, items, "data-figure-insert-idx", function (item) {
      _insertExisting(item);
    });
    _bindCardButtons(container, items, "data-figure-retire-idx", function (item) {
      _retireFigure(item);
    });
    _bindCardButtons(container, items, "data-figure-restore-idx", function (item) {
      _restoreFigure(item);
    });
    _bindCardButtons(container, items, "data-figure-caption-edit-idx", function (item, idx) {
      _toggleCaptionEdit(idx, true);
    });
    _bindCardButtons(container, items, "data-figure-caption-cancel-idx", function (item, idx) {
      _toggleCaptionEdit(idx, false);
    });
    _bindCardButtons(container, items, "data-figure-caption-save-idx", function (item, idx) {
      _saveCaption(item, idx);
    });

    items.forEach(function (item, idx) { _loadExistingThumb(item, idx); });
  }

  function _bindCardButtons(container, items, attr, handler) {
    Array.prototype.forEach.call(
      container.querySelectorAll("[" + attr + "]"),
      function (btn) {
        btn.addEventListener("click", function () {
          var idx = parseInt(btn.getAttribute(attr), 10);
          var item = items[idx];
          if (!item) return;
          handler(item, idx);
        });
      }
    );
  }

  function _toggleCaptionEdit(idx, open) {
    var row = document.querySelector('[data-figure-caption-row-idx="' + idx + '"]');
    if (!row) return;
    row.hidden = !open;
    if (!open) return;
    var input = document.querySelector('[data-figure-caption-input-idx="' + idx + '"]');
    if (input) input.focus();
  }

  function _showThumb(img, url) {
    img.src = url;
    img.hidden = false;
    var placeholder = img.parentNode && img.parentNode.querySelector(".figure-studio-card-placeholder");
    if (placeholder) placeholder.remove();
  }

  function _loadExistingThumb(item, idx) {
    var img = document.querySelector('.figure-studio-card-img[data-figure-thumb-idx="' + idx + '"]');
    if (!img) return;
    // 一覧は状態操作のたびに再描画するため、取得済みの blob URL は使い回す
    // （同じ画像を何度も取りに行かない・URL を溜めない）。
    if (item.thumbUrl) { _showThumb(img, item.thumbUrl); return; }
    var path = _figureImagePath(item);
    if (!path) { _markThumbFailed(img, "取得先がありません"); return; }
    apiFetchRaw(path, { _noJson: true })
      .then(function (res) {
        if (!res.ok) throw new Error("image load failed");
        return res.blob();
      })
      .then(function (blob) {
        if (!studio || !document.getElementById(MODAL_ID)) return;
        var url = URL.createObjectURL(blob);
        studio.objectUrls.push(url);
        item.thumbUrl = url;
        _showThumb(img, url);
      })
      .catch(function () {
        _markThumbFailed(img, "表示できません");
      });
  }

  function _markThumbFailed(img, message) {
    img.hidden = true;
    var placeholder = img.parentNode && img.parentNode.querySelector(".figure-studio-card-placeholder");
    if (placeholder) placeholder.textContent = message;
  }

  // ── 生成図のライフサイクル操作（PATCH。設計書 §7.1b / §7.4 / §8）─────────
  //
  // 行削除 API は無い（FG7）。カードからできるのは
  //   draft → adopted（採用して挿入）/ adopted → retired（回収）/ retired → adopted（採用に戻す）
  //   + キャプションの修正 + トピックへの参照登録
  // だけで、いずれも教員の明示操作（FG2）。
  //
  // **挿入は必ずサーバ経由でトピック登録まで行う**（§7.1b の要石）。本文へ
  // 図の埋め込み（![[figure:id]]）を書くだけだと ①AI 書き換えで embed が消える
  // ②未配信時に学習者へ生 UUID が露出する、という 2 つの壊れ方をする。

  function _teachingFigurePath(figureId) {
    return "/admin/courses/" + encodeURIComponent(studio.courseId) +
      "/teaching-figures/" + encodeURIComponent(figureId);
  }

  function _patchTeachingFigure(item, body) {
    return apiFetch(_teachingFigurePath(item.id), {
      method: "PATCH",
      body: JSON.stringify(body || {})
    }).then(_parseJson);
  }

  function _setCardNotice(item, message) {
    if (item) item.notice = message || "";
  }

  // カード操作は1つずつ（二重送信でトピック登録・埋め込みが重複しないように）。
  function _runCardAction(item, busyMessage, run) {
    if (!studio || studio.cardBusy) return;
    studio.cardBusy = true;
    _setCardNotice(item, busyMessage);
    _renderExistingList();
    run()
      .catch(function (err) {
        _setCardNotice(item, _errorText(err, "操作に失敗しました"));
      })
      .then(function () {
        if (!studio) return;
        studio.cardBusy = false;
        if (document.getElementById(MODAL_ID)) _renderExistingList();
      });
  }

  function _confirmThen(title, message, confirmLabel, onConfirm) {
    if (window.AdminDangerConfirm && typeof window.AdminDangerConfirm.open === "function") {
      window.AdminDangerConfirm.open({
        title: title, message: message, confirmLabel: confirmLabel
      }, onConfirm);
      return;
    }
    if (window.confirm(message)) onConfirm();
  }

  // 回収前の事実文（§7.4）。参照中のトピック数は呼び出し元（原稿スタジオ）が
  // コース構造から数えて渡す。数えられない場合は件数を書かない（推測しない）。
  function _countTopicReferences(figureId) {
    if (!studio || typeof studio.countTopicReferences !== "function") return -1;
    var count = studio.countTopicReferences(figureId);
    return typeof count === "number" && count >= 0 ? count : -1;
  }

  function _retireMessage(count) {
    var tail = "回収しても図は削除されず、あとから [採用に戻す] で戻せます。";
    if (count > 0) {
      return "この図を参照中のトピックが " + count + " 件あります。" +
        "回収すると学習者には「配信対象ではありません」カードが表示されます。" + tail;
    }
    if (count === 0) {
      return "この図を参照しているトピックは、いま確認できる範囲ではありません。" +
        "回収すると、この図は受講者に表示されなくなります。" + tail;
    }
    return "回収すると、この図を参照しているトピックでは学習者に" +
      "「配信対象ではありません」カードが表示されます。" + tail;
  }

  function _finishInsert(item, result) {
    if (typeof studio.onInsert === "function") {
      studio.onInsert(item.id, "![[figure:" + item.id + "]]", result || null);
    }
    studio.insertedIds[item.id] = true;
    _setStatus(ADOPT_NOTICE);
  }

  function _insertExisting(item) {
    if (!item || !item.id || !studio) return;
    if (!item.teaching) {
      // 論文から抽出した図は本文への挿入のみ（設計書 §6.2-3。状態も登録も持たない）。
      _finishInsert(item, null);
      _renderExistingList();
      return;
    }
    if (item.status === "retired") {
      _setCardNotice(item, RETIRED_INSERT_NOTICE);
      _renderExistingList();
      return;
    }
    if (!studio.topicId) {
      _setCardNotice(item, "挿入先のトピックが分かりません。トピックを選び直してから挿入してください。");
      _renderExistingList();
      return;
    }
    var body = { register_topic_id: studio.topicId };
    // 下書きは「採用して挿入」（採用と登録を同じ操作で済ませる）。
    if (item.status !== "adopted") body.status = "adopted";
    _runCardAction(item, "挿入しています…", function () {
      return _patchTeachingFigure(item, body).then(function (data) {
        item.status = "adopted";
        if (data && data.figure && data.figure.caption) item.caption = data.figure.caption;
        _finishInsert(item, data || null);
        _setCardNotice(item, data && data.image_snapshot_failed ? SNAPSHOT_FAILED_NOTICE : "");
      });
    });
  }

  function _retireFigure(item) {
    if (!item || !item.id || !studio) return;
    _confirmThen("図を回収する", _retireMessage(_countTopicReferences(item.id)), "回収する", function () {
      _runCardAction(item, "回収しています…", function () {
        return _patchTeachingFigure(item, { status: "retired" }).then(function (data) {
          item.status = (data && data.figure && data.figure.status) || "retired";
          _setCardNotice(
            item,
            data && data.image_snapshot_failed
              ? SNAPSHOT_FAILED_NOTICE
              : "回収しました。受講者には表示されません（本文の埋め込みはそのまま残ります）。"
          );
        });
      });
    });
  }

  function _restoreFigure(item) {
    if (!item || !item.id || !studio) return;
    _runCardAction(item, "採用に戻しています…", function () {
      return _patchTeachingFigure(item, { status: "adopted" }).then(function (data) {
        item.status = (data && data.figure && data.figure.status) || "adopted";
        _setCardNotice(
          item,
          data && data.image_snapshot_failed
            ? SNAPSHOT_FAILED_NOTICE
            : "採用に戻しました。トピックを保存したあとに受講者へ表示されます。"
        );
      });
    });
  }

  function _saveCaption(item, idx) {
    if (!item || !item.id || !studio) return;
    var input = document.querySelector('[data-figure-caption-input-idx="' + idx + '"]');
    if (!input) return;
    var value = input.value;
    _runCardAction(item, "キャプションを保存しています…", function () {
      return _patchTeachingFigure(item, { caption: value }).then(function (data) {
        // 表示は応答の caption を使う（サーバが「（模式図）」を付けることがある・FG5）。
        var saved = data && data.figure ? data.figure.caption : value;
        item.caption = saved || "";
        _setCardNotice(
          item,
          data && data.image_snapshot_failed
            ? SNAPSHOT_FAILED_NOTICE
            : "キャプションを保存しました。"
        );
      });
    });
  }

  // ── 公開 API ──────────────────────────────────────────────────────────
  // opts = {
  //   courseId, topicId,
  //   insertTarget: {textarea, selectionStart, selectionEnd} | null,
  //   preset: {figureBrief, figureKind, anchorExcerpt, suggestionId} | null,
  //   existingOnly: bool,
  //   onInsert: function(figureId, embedText, result|null),
  //   onClose: function(),            // モーダルを閉じた後の後始末（フォーカス復帰など）
  //   countTopicReferences: function(figureId) -> number,  // 回収前の事実文用（§7.4）
  //   figuresIndex: {figure_id: {caption, image_url, document_id, teaching}},
  //   documentIds: [document_id]
  // }
  function open(opts) {
    opts = opts || {};
    if (!opts.courseId) return;
    _closeModal();
    studio = _emptyState();
    studio.courseId = opts.courseId;
    studio.topicId = opts.topicId || "";
    studio.insertTarget = opts.insertTarget || null;
    studio.preset = opts.preset || null;
    studio.existingOnly = !!opts.existingOnly;
    studio.onInsert = typeof opts.onInsert === "function" ? opts.onInsert : null;
    studio.onClose = typeof opts.onClose === "function" ? opts.onClose : null;
    studio.countTopicReferences =
      typeof opts.countTopicReferences === "function" ? opts.countTopicReferences : null;
    studio.figuresIndex = opts.figuresIndex || {};
    studio.documentIds = opts.documentIds || [];
    studio.tab = studio.existingOnly ? "existing" : "generate";
    if (studio.preset) {
      studio.figureKind = studio.preset.figureKind || "";
      studio.suggestionId = studio.preset.suggestionId || null;
    }

    _renderModal();

    if (studio.tab === "existing") {
      _loadExistingFigures();
      return;
    }
    // 提案起点の場合は初回ターンを自動送信する（設計書 §6.2-2）。
    if (studio.preset && studio.preset.figureBrief) {
      var instruction = "次の方針で初版を描いてください: " + studio.preset.figureBrief;
      if (studio.preset.anchorExcerpt) {
        instruction += "\n対象箇所（教材本文からの引用）: " + studio.preset.anchorExcerpt;
      }
      if (studio.preset.figureKind) {
        instruction += "\n図の種類: " + _kindLabel(studio.preset.figureKind);
      }
      _sendTurn(instruction);
    }
  }

  function isOpen() {
    return !!document.getElementById(MODAL_ID);
  }

  function close() {
    _closeModal();
  }

  function init(options) {
    if (initialized) return;
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    deps.apiFetchRaw = options.apiFetchRaw || null;
    deps.escHtml = options.escHtml || null;
    studio = _emptyState();
    initialized = true;
  }

  window.FigureStudio = {
    init: init,
    open: open,
    close: close,
    isOpen: isOpen,
    FIGURE_KIND_LABELS: FIGURE_KIND_LABELS
  };
})();
