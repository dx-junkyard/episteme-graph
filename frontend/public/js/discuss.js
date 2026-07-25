// discuss モード（「論文と話す」）Phase 2 — 開幕画面・着地画面・分岐チップの基盤。
//
// docs/features/discussion_mode_design.md §3.3（開幕）/ §3.5（着地・consolidation）を実装する。
// reconstruction.js と同型の自己完結 IIFE（自前 token()/apiFetch()/esc() ヘルパー、
// window 公開は1オブジェクトのみ）。app.js からの配線は最小限に留める:
//   - renderMaterialRegion() の discuss 分岐から renderOpening(body, courseId) を呼ぶ
//   - sendMessage() 成功パスから notifyActivity() を呼ぶ（無活動タイムアウト用）
//   - selectTopic() の discuss→通常トピック遷移直後に maybeShowLanding(courseId, "topic_switch")
//   - discuss バーの「議論を終える」ボタンから maybeShowLanding(courseId, "explicit")
//
// 開幕・着地とも非LLM・既存 API の束ねのみ（DM8）。数値・件数・網羅率は出さない（DM6）。
// explore（コース逸脱時の内部語彙）に使われる語はここでは使わない（DM5）。
(function () {
  "use strict";

  var API = "/api";
  // discuss モードの予約疑似トピック（app.js の DISCUSS_TOPIC_ID と同じ値）。
  // モジュールを自己完結させるため、意図的に値を複製している。
  var DISCUSS_TOPIC_ID = "_discussion";

  var INACTIVITY_MS = 15 * 60 * 1000; // 無活動タイムアウト（トリガー③）
  var SUPPRESS_MS = 10 * 60 * 1000;   // 直近表示済みの抑制窓（うるさくしない）

  var ctx = { courseId: "" };
  var turnCount = 0;
  var lastShownAt = 0;
  var inactivityTimer = null;

  var openingCache = { courseId: "", data: null };
  var openingReqSeq = 0;

  function token() {
    return localStorage.getItem("eg_token") || null;
  }

  async function apiFetch(path, opts) {
    opts = opts || {};
    var headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    var t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    return fetch(API + path, Object.assign({}, opts, { headers: headers }));
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // 開幕画面は #material-here が「論文と議論中」であるあいだだけ有効なコンテキスト
  // とみなす（app.js が discuss モード中に同期でこの文言をセットする）。非同期応答が
  // 戻ったときに、既にトピック切替済みなら教材区画を上書きしない（新規APIを増やさず
  // 既存 DOM の合図だけで遅延応答を破棄する）。
  function stillInDiscussContext() {
    var here = document.getElementById("material-here");
    return !!here && here.textContent === "論文と議論中";
  }

  // ── 開幕画面（§3.3）─────────────────────────────────────────────────

  function discussChip(label, documentId) {
    if (!label) return "";
    var text = "「" + label + "」について、この論文での位置づけと根拠を教えてください。";
    return '<button type="button" class="discuss-chip" data-discuss-ask="' + esc(text) + '">' +
      esc(label) + '</button>';
  }

  function renderThesisSection(doc, fragilePoints) {
    var thesis = doc.thesis;
    var docFragile = fragilePoints.filter(function (f) {
      return f && f.document_id === doc.document_id;
    });
    var html = '<div class="discuss-section discuss-section-thesis">';
    html += '<div class="discuss-section-hd">この論文が賭けているもの</div>';
    if (thesis) {
      var claims = Array.isArray(thesis.central_claims) ? thesis.central_claims : [];
      var equations = Array.isArray(thesis.central_equations) ? thesis.central_equations : [];
      if (claims.length || equations.length) {
        html += '<div class="discuss-chip-row">';
        claims.forEach(function (c) { html += discussChip(c && c.label, doc.document_id); });
        equations.forEach(function (e) { html += discussChip(e && e.label, doc.document_id); });
        html += '</div>';
      }
      var sections = Array.isArray(thesis.support_sections) ? thesis.support_sections : [];
      sections.forEach(function (sec) {
        if (!sec || !Array.isArray(sec.items) || sec.items.length === 0) return;
        html += '<details class="discuss-support-section">';
        html += '<summary>' + esc(sec.label || "") + '</summary>';
        html += '<div class="discuss-chip-row">';
        sec.items.forEach(function (item) {
          html += discussChip(item && item.label, doc.document_id);
        });
        html += '</div></details>';
      });
    } else {
      html += '<div class="discuss-muted">この論文の中心命題はまだ整理されていません。</div>';
    }
    if (docFragile.length) {
      html += '<div class="discuss-fragile">';
      html += '<div class="discuss-fragile-hd">最も脆い一手</div>';
      docFragile.forEach(function (f) {
        html += '<div class="discuss-fragile-item">' + esc(f.fact_line || "") + '</div>';
      });
      html += '</div>';
    }
    html += '</div>';
    return html;
  }

  function renderBackboneSection(doc) {
    var nodes = Array.isArray(doc.backbone) ? doc.backbone : [];
    var html = '<div class="discuss-section discuss-section-backbone">';
    html += '<div class="discuss-section-hd">理論のバックボーン</div>';
    if (!nodes.length) {
      html += '<div class="discuss-muted">この論文のバックボーンはまだ整理されていません。</div>';
    } else {
      html += '<div class="discuss-backbone-list">';
      nodes.forEach(function (n) {
        var backed = n && n.source_backing_status === "source_backed";
        var cls = "discuss-backbone-node" + (backed ? "" : " discuss-backbone-node--review");
        var label = (n && n.label) || "";
        var text = "「" + label + "」について、この論文での位置づけと根拠を教えてください。";
        html += '<button type="button" class="' + cls + '" data-discuss-ask="' + esc(text) + '"' +
          (n && n.description ? ' title="' + esc(n.description) + '"' : '') + '>';
        html += '<span class="discuss-backbone-stage">' + esc((n && n.stage_label) || "") + '</span>';
        html += '<span class="discuss-backbone-label">' + esc(label) + '</span>';
        html += '</button>';
      });
      html += '</div>';
    }
    if (doc.truncated) {
      html += '<div class="discuss-muted discuss-truncated-note">この一覧は主要なものに絞って表示しています。</div>';
    }
    html += '</div>';
    return html;
  }

  var FIRST_MOVE_PROMPTS = [
    { label: "なぜこの設計?", text: "なぜこの設計・アプローチを選んだのか、根拠を教えてください。" },
    { label: "前提は何?", text: "この議論の前提になっている仮定は何か教えてください。" },
    { label: "他と矛盾しない?", text: "この主張は他の知見や結果と矛盾しないか教えてください。" },
  ];

  function renderFirstMoveSection() {
    var html = '<div class="discuss-section discuss-section-first-move">';
    html += '<div class="discuss-section-hd">最初の一手</div>';
    html += '<div class="discuss-chip-row">';
    FIRST_MOVE_PROMPTS.forEach(function (p) {
      html += '<button type="button" class="discuss-chip discuss-first-move-chip" data-discuss-ask="' +
        esc(p.text) + '">' + esc(p.label) + '</button>';
    });
    html += '</div>';
    html += '<div class="discuss-muted discuss-free-input-note">自由に入力してもかまいません。</div>';
    html += '</div>';
    return html;
  }

  function buildOpeningHtml(data) {
    var docs = Array.isArray(data.documents) ? data.documents : [];
    if (!docs.length) return "";
    var multi = docs.length > 1;
    var fragile = Array.isArray(data.fragile_points) ? data.fragile_points : [];
    var html = '<div class="discuss-opening">';
    html += '<div class="discuss-opening-note">' +
      '回答の根拠（教材由来か、AIの一般知識か）は各回答に表示されます。</div>';
    docs.forEach(function (doc) {
      html += '<div class="discuss-opening-doc">';
      if (multi) html += '<div class="discuss-opening-doc-title">' + esc(doc.title || "") + '</div>';
      html += renderThesisSection(doc, fragile);
      html += renderBackboneSection(doc);
      html += '</div>';
    });
    html += renderFirstMoveSection();
    if (data.truncated) {
      html += '<div class="discuss-muted discuss-truncated-note">' +
        '論文・資料の一覧は主要なものに絞って表示しています。</div>';
    }
    html += '</div>';
    return html;
  }

  function bindOpeningEvents(containerEl) {
    containerEl.querySelectorAll("[data-discuss-ask]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var text = this.getAttribute("data-discuss-ask");
        if (text && window.sendPrompt) window.sendPrompt(text);
      });
    });
  }

  // 教材区画（#material-body）を開幕画面へ置き換える。取得できない/該当なしのときは
  // 何もしない（呼び出し側 app.js が既に描画済みの Phase 1 プレースホルダのまま
  // fail-closed に縮退する）。
  async function renderOpening(containerEl, courseId) {
    if (!containerEl) return;
    courseId = courseId || "";
    ctx.courseId = courseId;
    if (!courseId) return;

    // 既に同じコースで取得済みなら再フェッチせず即描画する（送信のたびに
    // renderMaterialRegion が呼ばれても毎回ネットワーク往復させないため）。
    if (openingCache.courseId === courseId && openingCache.data) {
      var cachedHtml = buildOpeningHtml(openingCache.data);
      if (cachedHtml && stillInDiscussContext()) {
        containerEl.innerHTML = cachedHtml;
        bindOpeningEvents(containerEl);
      }
      return;
    }

    var reqId = ++openingReqSeq;
    try {
      var res = await apiFetch("/learning/courses/" + encodeURIComponent(courseId) + "/discuss/opening");
      if (!res.ok) return; // fail-closed: プレースホルダのまま
      var data = await res.json();
      if (reqId !== openingReqSeq) return; // 遅延応答ガード（別コースへ切替済み）
      if (!data || !data.available) return; // fail-closed
      var html = buildOpeningHtml(data);
      if (!html) return; // documents 空 → プレースホルダのまま
      openingCache = { courseId: courseId, data: data };
      if (stillInDiscussContext()) {
        containerEl.innerHTML = html;
        bindOpeningEvents(containerEl);
      }
    } catch (e) {
      // fail-closed: プレースホルダのまま（フィクスチャ・偽データを出さない）
    }
  }

  // ── 分岐チップ（§3.4、app.js の renderAiContent から呼ばれる）────────────

  function renderBranchChips() {
    return '<div class="discuss-branch-chips">' +
      '<button type="button" class="discuss-branch-btn suggest-btn" data-suggest="' +
      esc("いまの回答の前提と根拠を、もう一段掘り下げてください。") + '">🔎 深掘り</button>' +
      '<button type="button" class="discuss-branch-btn suggest-btn" data-suggest="' +
      esc("いまの話題と隣り合う概念や、関連する別の論点に広げてください。") + '">🧭 横展開</button>' +
      '</div>';
  }

  // ── 活動通知・無活動タイムアウト（トリガー③）─────────────────────────

  function armInactivityTimer() {
    if (inactivityTimer) clearTimeout(inactivityTimer);
    inactivityTimer = setTimeout(function () {
      inactivityTimer = null;
      maybeShowLanding(ctx.courseId, "timeout");
    }, INACTIVITY_MS);
  }

  function clearInactivityTimer() {
    if (inactivityTimer) { clearTimeout(inactivityTimer); inactivityTimer = null; }
  }

  function notifyActivity() {
    turnCount += 1;
    armInactivityTimer();
  }

  // ── 着地画面（§3.5, consolidation）───────────────────────────────────

  function landingRoot() {
    return document.getElementById("discuss-landing-region");
  }

  var landingRootBound = false;

  function bindLandingRootOnce(root) {
    if (landingRootBound) return;
    landingRootBound = true;
    root.addEventListener("click", function (e) {
      if (e.target === root) closeLanding();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && root && !root.hidden) closeLanding();
    });
  }

  function closeLanding() {
    var root = landingRoot();
    if (!root) return;
    root.hidden = true;
    root.innerHTML = "";
  }

  async function fetchDigest(path) {
    try {
      var res = await apiFetch(path);
      if (!res.ok) return { items: [] };
      var data = await res.json();
      return { items: Array.isArray(data && data.items) ? data.items : [] };
    } catch (e) {
      return { items: [] };
    }
  }

  async function fetchReconNext(courseId) {
    try {
      var res = await apiFetch(
        "/learning/courses/" + encodeURIComponent(courseId) +
        "/topics/" + encodeURIComponent(DISCUSS_TOPIC_ID) + "/reconstruction/next"
      );
      if (!res.ok) return null;
      var data = await res.json();
      return (data && data.item) ? data.item : null;
    } catch (e) {
      return null;
    }
  }

  function tensionCardHtml(item) {
    var tid = esc(item.trace_id);
    return '<div class="discuss-landing-card" data-discuss-tension-card="' + tid + '">' +
      (item.context_label ? '<div class="discuss-landing-card-ctx">' + esc(item.context_label) + '</div>' : "") +
      '<div class="discuss-landing-card-quote">『' + esc(item.evidence_quote || "") + '』' +
      (item.paraphrase ? "——" + esc(item.paraphrase) : "") + '</div>' +
      '<div class="discuss-landing-card-actions">' +
      '<button type="button" class="discuss-landing-card-btn" data-discuss-tension-confirm="' + tid + '">' +
      '自分の言葉で残す</button>' +
      '<button type="button" class="discuss-landing-card-btn secondary" data-discuss-tension-dismiss="' + tid + '">' +
      '違う</button>' +
      '</div></div>';
  }

  function anchorCardHtml(item) {
    var tid = esc(item.trace_id);
    return '<div class="discuss-landing-card" data-discuss-anchor-card="' + tid + '">' +
      (item.context_label ? '<div class="discuss-landing-card-ctx">' + esc(item.context_label) + '</div>' : "") +
      '<div class="discuss-landing-card-quote">『' + esc(item.question_text || "") + '』</div>' +
      '<div class="discuss-landing-card-actions">' +
      '<button type="button" class="discuss-landing-card-btn" data-discuss-anchor-confirm="' + tid + '">' +
      'この理解で残す</button>' +
      '<button type="button" class="discuss-landing-card-btn secondary" data-discuss-anchor-dismiss="' + tid + '">' +
      '違う</button>' +
      '</div></div>';
  }

  function landingShellHtml(bodyHtml) {
    return '' +
      '<div class="discuss-landing-panel" role="dialog" aria-label="今日の議論を終える">' +
      '<div class="discuss-landing-header">' +
      '<span class="discuss-landing-title">今日の議論を振り返る</span>' +
      '<button type="button" class="discuss-landing-close-btn" id="discuss-landing-skip-top-btn" title="閉じる">&times;</button>' +
      '</div>' +
      '<div class="discuss-landing-content" id="discuss-landing-content">' + bodyHtml + '</div>' +
      '<div class="discuss-landing-footer">' +
      '<div class="discuss-landing-skip-note">スキップしても、ここまでの記録は残ります。あとから「わたしの地図」で確認できます。</div>' +
      '<button type="button" class="discuss-landing-skip-btn" id="discuss-landing-skip-btn">スキップ</button>' +
      '</div>' +
      '</div>';
  }

  function bindLandingContentEvents(root, reconItem) {
    root.querySelectorAll("[data-discuss-tension-confirm]").forEach(function (btn) {
      btn.addEventListener("click", function () { openTensionInlineConfirm(this.getAttribute("data-discuss-tension-confirm")); });
    });
    root.querySelectorAll("[data-discuss-tension-dismiss]").forEach(function (btn) {
      btn.addEventListener("click", function () { dismissTensionCard(this.getAttribute("data-discuss-tension-dismiss")); });
    });
    root.querySelectorAll("[data-discuss-anchor-confirm]").forEach(function (btn) {
      btn.addEventListener("click", function () { confirmAnchorCard(this.getAttribute("data-discuss-anchor-confirm")); });
    });
    root.querySelectorAll("[data-discuss-anchor-dismiss]").forEach(function (btn) {
      btn.addEventListener("click", function () { dismissAnchorCard(this.getAttribute("data-discuss-anchor-dismiss")); });
    });
    var reconBtn = document.getElementById("discuss-landing-recon-btn");
    if (reconBtn) {
      reconBtn.addEventListener("click", function () {
        closeLanding();
        var region = document.getElementById("reconstruction-region");
        if (!region) return;
        region.scrollIntoView({ behavior: "smooth", block: "center" });
        var openBtn = document.getElementById("recon-open-btn");
        if (openBtn) openBtn.click();
      });
    }
    var contBtn = document.getElementById("discuss-landing-continue-btn");
    if (contBtn) {
      contBtn.addEventListener("click", function () {
        closeLanding();
        if (window.discussReturnToSequential) window.discussReturnToSequential();
      });
    }
    // スキップボタン（ヘッダ×・フッタ）はシェル描画直後（maybeShowLanding）で
    // 既に配線済み — 読み込み中でも即スキップできるようにするため。ここで
    // 二重に bind しない。
  }

  function openTensionInlineConfirm(traceId) {
    var card = document.querySelector('[data-discuss-tension-card="' + traceId + '"]');
    if (!card) return;
    var actions = card.querySelector(".discuss-landing-card-actions");
    if (!actions) return;
    var box = document.createElement("div");
    box.className = "discuss-landing-card-input";
    box.innerHTML =
      '<textarea rows="2" placeholder="自分の言葉で言い直すと?（任意・空のままでも残せます）"></textarea>' +
      '<div class="discuss-landing-card-actions">' +
      '<button type="button" class="discuss-landing-card-btn">確定</button>' +
      '<button type="button" class="discuss-landing-card-btn secondary">やめる</button>' +
      '</div>';
    actions.replaceWith(box);
    var buttons = box.querySelectorAll("button");
    var confirmBtn = buttons[0];
    var cancelBtn = buttons[1];
    var ta = box.querySelector("textarea");
    if (ta) ta.focus();
    cancelBtn.addEventListener("click", function () {
      // actions は DOM から一時的に外しただけの同一ノード（クローンしていない）なので、
      // 元のイベントリスナーはそのまま残っている。再バインドすると他カードのボタンまで
      // 二重配線してしまうため、ここでは戻すだけにする。
      box.replaceWith(actions);
    });
    confirmBtn.addEventListener("click", async function () {
      var text = ta ? (ta.value || "").trim() : "";
      try {
        await apiFetch("/learning/tension/" + encodeURIComponent(traceId) + "/confirm", {
          method: "POST", body: JSON.stringify({ learner_text: text }),
        });
      } catch (e) { /* best-effort */ }
      card.innerHTML = '<div class="discuss-landing-card-done">地図に置きました。</div>';
    });
  }

  async function dismissTensionCard(traceId) {
    var card = document.querySelector('[data-discuss-tension-card="' + traceId + '"]');
    try {
      await apiFetch("/learning/tension/" + encodeURIComponent(traceId) + "/dismiss", {
        method: "POST", body: JSON.stringify({}),
      });
    } catch (e) { /* best-effort */ }
    if (card) card.remove();
  }

  async function confirmAnchorCard(traceId) {
    var card = document.querySelector('[data-discuss-anchor-card="' + traceId + '"]');
    try {
      await apiFetch("/learning/anchors/" + encodeURIComponent(traceId) + "/confirm", {
        method: "POST", body: JSON.stringify({ doubt_type: "" }),
      });
    } catch (e) { /* best-effort */ }
    if (card) card.innerHTML = '<div class="discuss-landing-card-done">地図に置きました。</div>';
  }

  async function dismissAnchorCard(traceId) {
    var card = document.querySelector('[data-discuss-anchor-card="' + traceId + '"]');
    try {
      await apiFetch("/learning/anchors/" + encodeURIComponent(traceId) + "/dismiss", {
        method: "POST", body: JSON.stringify({}),
      });
    } catch (e) { /* best-effort */ }
    if (card) card.remove();
  }

  function buildLandingBodyHtml(tensionItems, anchorItems, reconItem) {
    var html = "";
    html += '<div class="discuss-landing-section">';
    html += '<div class="discuss-landing-section-hd">今日話した内容を地図に置く</div>';
    if (tensionItems.length === 0 && anchorItems.length === 0) {
      html += '<div class="discuss-landing-empty">今回の対話からの候補はありません。' +
        '痕跡は残っており、後から「わたしの地図」で確認できます。</div>';
    } else {
      tensionItems.forEach(function (item) { html += tensionCardHtml(item); });
      anchorItems.forEach(function (item) { html += anchorCardHtml(item); });
    }
    html += '</div>';

    if (reconItem) {
      html += '<div class="discuss-landing-section">';
      html += '<div class="discuss-landing-section-hd">理解の確認</div>';
      html += '<p class="discuss-landing-section-body">理解の確認に1問挑戦できます。</p>';
      html += '<button type="button" class="discuss-landing-btn" id="discuss-landing-recon-btn">挑戦する</button>';
      html += '</div>';
    }

    html += '<div class="discuss-landing-section">';
    html += '<div class="discuss-landing-section-hd">このトピックで続きを学ぶ</div>';
    html += '<button type="button" class="discuss-landing-btn" id="discuss-landing-continue-btn">' +
      'このトピックで続きを学ぶ</button>';
    html += '</div>';

    // Field Atlas 現在地チップ: 既にロード済みの状態からのみ安価に読む（新規APIは呼ばない）。
    // 現状 atlas-minimap.js は「いまここ」情報を外部から読める形で公開していないため、
    // fail-closed で常に非表示にする（実装可否は呼び出し元へ正直に報告する）。

    return html;
  }

  // 討議終了トリガー（① 明示終了 / ② discuss→通常トピック切替 / ③ 無活動タイムアウト）。
  // 直近表示済み（10分以内）または discuss での往復が0なら出さない（うるさくしない）。
  async function maybeShowLanding(courseId, reason) {
    clearInactivityTimer();
    if (courseId) ctx.courseId = courseId;
    if (!ctx.courseId) return;
    if (turnCount <= 0) return;
    var now = Date.now();
    if (now - lastShownAt < SUPPRESS_MS) return;
    lastShownAt = now;
    turnCount = 0;

    var root = landingRoot();
    if (!root) return;
    bindLandingRootOnce(root);
    root.hidden = false;
    root.innerHTML = landingShellHtml('<div class="discuss-landing-loading">読み込み中…</div>');
    var contentEl = document.getElementById("discuss-landing-content");
    var skipBtn0 = document.getElementById("discuss-landing-skip-btn");
    if (skipBtn0) skipBtn0.addEventListener("click", closeLanding);
    var skipTop0 = document.getElementById("discuss-landing-skip-top-btn");
    if (skipTop0) skipTop0.addEventListener("click", closeLanding);

    var courseIdForFetch = ctx.courseId;
    var tensionDigest = { items: [] };
    var anchorDigest = { items: [] };
    var reconItem = null;
    try {
      var results = await Promise.all([
        fetchDigest("/learning/courses/" + encodeURIComponent(courseIdForFetch) + "/tension/digest"),
        fetchDigest("/learning/courses/" + encodeURIComponent(courseIdForFetch) + "/anchors/digest"),
        fetchReconNext(courseIdForFetch),
      ]);
      tensionDigest = results[0];
      anchorDigest = results[1];
      reconItem = results[2];
    } catch (e) { /* best-effort。空扱いで続行 */ }

    if (root.hidden) return; // その間にスキップ/閉じられていた
    if (contentEl) {
      contentEl.innerHTML = buildLandingBodyHtml(tensionDigest.items, anchorDigest.items, reconItem);
      bindLandingContentEvents(root, reconItem);
    }
  }

  window.Discuss = {
    renderOpening: renderOpening,
    maybeShowLanding: maybeShowLanding,
    notifyActivity: notifyActivity,
    renderBranchChips: renderBranchChips,
    reset: function () {
      clearInactivityTimer();
      turnCount = 0;
      closeLanding();
    },
  };
})();
