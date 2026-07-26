// discuss モード（「論文と話す」）Phase 2 — 開幕画面・着地画面・分岐チップの基盤。
//
// docs/features/discussion_mode_design.md §3.3（開幕）/ §3.5（着地・consolidation）を実装する。
// reconstruction.js と同型の自己完結 IIFE（自前 token()/apiFetch()/esc() ヘルパー、
// window 公開は1オブジェクトのみ）。app.js からの配線は最小限に留める:
//   - renderMaterialRegion() の discuss 分岐から renderOpening(body, courseId) を呼ぶ
//   - sendMessage() 成功パスから notifyActivity() を呼ぶ（無活動タイムアウト用）
//   - selectTopic() の discuss→通常トピック遷移直後に maybeShowLanding(courseId, "topic_switch")
//   - discuss バーの「議論を終える」ボタンから maybeShowLanding(courseId, "explicit")
//   - initApp() から Discuss.init({getActiveCourseId}) で現在コースの getter を注入
//     （着地判定のコース一致ガードに使う。未注入でも後方互換で動く）
//   - switchCourse() / logout / 401 失効時に Discuss.reset() を呼ぶ（旧コースの
//     discuss 内部状態＝無活動タイマー・往復回数・ctx.courseId を持ち越さない）
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

  // app.js からの DI（任意）。現在アプリが表示しているコース ID を読めるようにする
  // getter。未注入（null）のときは従来どおり（コース一致チェックをスキップ）動作する
  // 後方互換フォールバック。
  var getActiveCourseId = null;

  function init(opts) {
    opts = opts || {};
    getActiveCourseId = (typeof opts.getActiveCourseId === "function") ? opts.getActiveCourseId : null;
  }

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

  // discuss 観測基盤（docs/features/discuss_observation_design.md §3）: UI イベントの
  // fire-and-forget 送信。await しない・失敗は握りつぶす（DO6）。レスポンス
  // （{recorded:n}）は学習者に見せないため中身を読まない・DOM にも出さない（DO3）。
  function sendDiscussMetric(event, payload) {
    if (!ctx.courseId) return;
    apiFetch("/learning/discuss/metric-events", {
      method: "POST",
      body: JSON.stringify({
        events: [{ event: event, course_id: ctx.courseId, payload: payload || {} }],
      }),
    }).catch(function () { /* fire-and-forget: 失敗は無視 (DO6) */ });
  }

  // 開幕画面は #material-body の data-discuss-active="true" であるあいだだけ有効な
  // コンテキストとみなす（app.js の renderMaterialRegion が discuss モード判定のたびに
  // 同期でこの属性をセットする。学習UI再編で表示専用だった #material-here を廃止した
  // ため、不可視の合図として data 属性へ移した）。非同期応答が戻ったときに、既に
  // トピック切替済みなら教材区画を上書きしない（新規APIを増やさず既存 DOM の合図だけで
  // 遅延応答を破棄する）。
  function stillInDiscussContext() {
    var body = document.getElementById("material-body");
    return !!body && body.dataset.discussActive === "true";
  }

  // ── 開幕画面（§3.3）─────────────────────────────────────────────────

  // 起点となる定型質問。押すとこの文がそのまま送信される（bindOpeningEvents）。
  function askText(label) {
    return "「" + label + "」について、この論文での位置づけと根拠を教えてください。";
  }

  function discussChip(label) {
    if (!label) return "";
    return '<button type="button" class="discuss-chip" data-discuss-ask="' + esc(askText(label)) + '">' +
      esc(label) + '</button>';
  }

  // 中心命題は「押すもの」ではなく「読むもの」として出す（claim の label は論文原文
  // ＝長い1文になりうるため、チップに詰めると読めない）。本文は CSS で3行に抑え、
  // 全文は明示操作で開く。行動（質問開始）は下のボタンに分離する。
  // 原文を要約・和訳して出すことはしない（開幕画面は非LLM・既存成果の投影のみ, DM8）。
  var CLAIM_CLAMP_HINT_CHARS = 60;

  function claimBlockHtml(item) {
    var label = (item && item.label) || "";
    if (!label) return "";
    var html = '<div class="discuss-claim">';
    html += '<div class="discuss-claim-text">' + esc(label) + '</div>';
    html += '<div class="discuss-claim-actions">';
    html += '<button type="button" class="discuss-claim-ask" data-discuss-ask="' +
      esc(askText(label)) + '">この主張について聞く</button>';
    if (label.length > CLAIM_CLAMP_HINT_CHARS) {
      html += '<button type="button" class="discuss-claim-more" data-discuss-expand="1">全文を見る</button>';
    }
    html += '</div></div>';
    return html;
  }

  // 支持構造（前提 / 導出の核 / 訂正の源 …）は A層の分類語彙で、閉じた状態では
  // 専門用語の縦積みにしか見えない。区画ごとに details を開かず、1つに畳んでおく。
  function renderSupportDetails(sections) {
    var usable = (sections || []).filter(function (sec) {
      return sec && Array.isArray(sec.items) && sec.items.length > 0;
    });
    if (!usable.length) return "";
    var html = '<details class="discuss-support-section discuss-support-details">';
    html += '<summary>支持構造をくわしく見る</summary>';
    usable.forEach(function (sec) {
      html += '<div class="discuss-support-group">';
      html += '<div class="discuss-support-group-hd">' + esc(sec.label || "") + '</div>';
      html += '<div class="discuss-chip-row">';
      sec.items.forEach(function (item) { html += discussChip(item && item.label); });
      html += '</div></div>';
    });
    html += '</details>';
    return html;
  }

  function renderThesisSection(doc, fragilePoints) {
    var thesis = doc.thesis;
    var docFragile = fragilePoints.filter(function (f) {
      return f && f.document_id === doc.document_id;
    });
    var html = '<div class="discuss-section discuss-section-thesis">';
    html += '<div class="discuss-section-hd">この論文が賭けているもの</div>';
    html += '<div class="discuss-section-sub">この論文がいちばん言いたいことです。</div>';
    if (thesis) {
      var claims = Array.isArray(thesis.central_claims) ? thesis.central_claims : [];
      var equations = Array.isArray(thesis.central_equations) ? thesis.central_equations : [];
      claims.forEach(function (c) { html += claimBlockHtml(c); });
      if (equations.length) {
        html += '<div class="discuss-section-sub discuss-central-eq-hd">中心となる式</div>';
        html += '<div class="discuss-chip-row">';
        equations.forEach(function (e) { html += discussChip(e && e.label); });
        html += '</div>';
      }
      html += renderSupportDetails(thesis.support_sections);
    } else {
      html += '<div class="discuss-muted">この論文の中心命題はまだ整理されていません。</div>';
    }
    if (docFragile.length) {
      html += '<details class="discuss-support-section discuss-fragile">';
      html += '<summary>最も脆い一手（根拠がまだ確認されていない箇所）</summary>';
      docFragile.forEach(function (f) {
        html += '<div class="discuss-fragile-item">' + esc(f.fact_line || "") + '</div>';
      });
      html += '</details>';
    }
    html += '</div>';
    return html;
  }

  function renderBackboneSection(doc) {
    var nodes = Array.isArray(doc.backbone) ? doc.backbone : [];
    var html = '<div class="discuss-section discuss-section-backbone">';
    html += '<div class="discuss-section-hd">理論のバックボーン</div>';
    html += '<div class="discuss-section-sub">この論文は、この順に組み立てられています。押すと、その段階から話せます。</div>';
    if (!nodes.length) {
      html += '<div class="discuss-muted">この論文のバックボーンはまだ整理されていません。</div>';
    } else {
      var hasReview = false;
      // 段階の並び（stage 順）が読み取れるよう、横並びの矢印つなぎで出す。
      // ラベルは stage_label（学習者向け日本語）を優先し、stage が未知のときだけ
      // ノードの label（A層の保存値）へ縮退する — main ノードの label は stage 名
      // そのものなので、両方描くと同じ文字が二重に出る。
      html += '<div class="discuss-backbone-list discuss-backbone-flow">';
      nodes.forEach(function (n, i) {
        var backed = n && n.source_backing_status === "source_backed";
        if (!backed) hasReview = true;
        var cls = "discuss-backbone-node" + (backed ? "" : " discuss-backbone-node--review");
        var label = (n && n.stage_label) || (n && n.label) || "";
        if (i > 0) html += '<span class="discuss-backbone-arrow" aria-hidden="true">›</span>';
        html += '<button type="button" class="' + cls + '" data-discuss-ask="' + esc(askText(label)) + '"' +
          (n && n.description ? ' title="' + esc(n.description) + '"' : '') + '>';
        html += '<span class="discuss-backbone-num">' + (i + 1) + '</span>';
        html += '<span class="discuss-backbone-label">' + esc(label) + '</span>';
        html += '</button>';
      });
      html += '</div>';
      if (hasReview) {
        html += '<div class="discuss-muted discuss-backbone-legend">' +
          '点線は、根拠がまだ確認されていない段階です。</div>';
      }
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
    html += '<div class="discuss-section-sub">ボタンを押すと、その話題から質問が始まります。</div>';
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
      'トピック順に縛られず、論文全体について話せます。' +
      '回答の根拠（教材由来か、AIの一般知識か）は各回答に表示されます。</div>';
    // 行動の起点（最初の一手）を最上部に置く。以前は最下部にあり、初見の学習者が
    // 最初に取れる操作が折り返し線の外にあった。
    html += renderFirstMoveSection();
    docs.forEach(function (doc) {
      html += '<div class="discuss-opening-doc">';
      if (multi) html += '<div class="discuss-opening-doc-title">' + esc(doc.title || "") + '</div>';
      html += renderThesisSection(doc, fragile);
      html += renderBackboneSection(doc);
      html += '</div>';
    });
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
        // 観測: バックボーンノードと、それ以外の起点チップ（中心命題・最初の一手）を区別する。
        if (this.classList.contains("discuss-backbone-node")) {
          sendDiscussMetric("opening_backbone_clicked", {});
        } else {
          sendDiscussMetric("opening_starter_clicked", {});
        }
        if (text && window.sendPrompt) window.sendPrompt(text);
      });
    });
    // 中心命題の本文は既定で3行に抑える（CSS）。全文表示は明示操作のみ。
    containerEl.querySelectorAll("[data-discuss-expand]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var block = this.closest(".discuss-claim");
        if (!block) return;
        var expanded = block.classList.toggle("expanded");
        this.textContent = expanded ? "折りたたむ" : "全文を見る";
      });
    });
  }

  var openingShownCourseId = "";

  // 観測: 開幕画面が実際に描画されたときに一度だけ opening_shown を送る
  // （renderChat 経由で renderOpening は何度も呼ばれるため、同一コース内での
  // 再描画は重複計上しない）。
  function notifyOpeningShown(courseId) {
    if (openingShownCourseId === courseId) return;
    openingShownCourseId = courseId;
    sendDiscussMetric("opening_shown", {});
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
        notifyOpeningShown(courseId);
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
        notifyOpeningShown(courseId);
      }
    } catch (e) {
      // fail-closed: プレースホルダのまま（フィクスチャ・偽データを出さない）
    }
  }

  // ── 分岐チップ（§3.4、app.js の renderAiContent から呼ばれる）────────────

  function renderBranchChips() {
    return '<div class="discuss-branch-chips">' +
      '<button type="button" class="discuss-branch-btn suggest-btn" data-discuss-branch="deep" data-suggest="' +
      esc("いまの回答の前提と根拠を、もう一段掘り下げてください。") + '">🔎 深掘り</button>' +
      '<button type="button" class="discuss-branch-btn suggest-btn" data-discuss-branch="wide" data-suggest="' +
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
      if (e.target === root) skipLanding();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && root && !root.hidden) skipLanding();
    });
  }

  function closeLanding() {
    var root = landingRoot();
    if (!root) return;
    root.hidden = true;
    root.innerHTML = "";
  }

  // 観測: 「読まずに閉じた」明示スキップ（ヘッダ×・フッタのスキップボタン・背景クリック・
  // Escape）。「挑戦する」「このトピックで続きを学ぶ」経由の close は landing_skipped に
  // 数えない（別イベントで区別する）。
  function skipLanding() {
    sendDiscussMetric("landing_skipped", {});
    closeLanding();
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

  // 帰属様相（doubt_type）の語彙。正本は
  // backend/core/structure_anchor/schema.py::DOUBT_TYPE_LABELS で、app.js の
  // ANCHOR_DOUBT_OPTIONS と同じ並び。このモジュールを自己完結させるため意図的に
  // 複製している（DISCUSS_TOPIC_ID と同じ方針）。
  var ANCHOR_DOUBT_OPTIONS = [
    { doubt_type: "definition", label: "定義がわからない" },
    { doubt_type: "justification_gap", label: "なぜ成り立つのか" },
    { doubt_type: "premise", label: "前提への疑い" },
    { doubt_type: "prior_conflict", label: "既有知識との衝突" },
    { doubt_type: "scope", label: "どこまで成り立つのか" },
    { doubt_type: "connection", label: "他とどう繋がるのか" },
  ];

  // 帰属候補カード。確定するのは「どこへの・どの様相の引っかかりだったか」（帰属）で
  // あって「理解」ではない。API が返す anchor_label / doubt_type_label を落として
  // 質問文だけを見せると、自分が書いた質問がそのまま返ってくるだけのカードになるため、
  // app.js の renderAnchorDigestCard と同じ問いかけの形に揃える（確定は本人のみ。P1）。
  function anchorCardHtml(item) {
    var tid = esc(item.trace_id);
    var where = item.anchor_label || item.anchor_type_label || "";
    var doubtLabel = item.doubt_type_label || "";
    var vague = !item.doubt_type || item.doubt_type === "unclassified" || !doubtLabel;
    var head;
    if (where && !vague) {
      head = 'この疑問は「' + esc(where) + '」の<b>' + esc(doubtLabel) + '</b>についてでしたか?';
    } else if (where) {
      head = 'この疑問は「' + esc(where) + '」についてでしたか?';
    } else if (!vague) {
      head = 'この疑問は<b>' + esc(doubtLabel) + '</b>についてでしたか?';
    } else {
      head = 'この疑問はどこへの引っかかりでしたか?';
    }
    var html = '<div class="discuss-landing-card" data-discuss-anchor-card="' + tid + '">';
    html += '<div class="discuss-landing-card-ctx">疑問の在り処' +
      (item.context_label ? ' · ' + esc(item.context_label) : "") + '</div>';
    html += '<div class="discuss-landing-card-head">' + head + '</div>';
    html += '<div class="discuss-landing-card-quote">『' + esc(item.question_text || "") + '』</div>';
    html += '<div class="discuss-landing-card-actions">';
    html += '<button type="button" class="discuss-landing-card-btn" data-discuss-anchor-confirm="' + tid + '">' +
      'そう、これ</button>';
    html += '<button type="button" class="discuss-landing-card-btn secondary" data-discuss-anchor-dismiss="' + tid + '">' +
      '違う</button>';
    html += '</div>';
    // 様相の訂正チップ（提案と違う様相ならタップでその doubt_type に訂正して確定する）。
    html += '<div class="discuss-landing-card-chips">';
    html += '<span class="discuss-landing-card-chips-hd">様相が違うなら:</span>';
    ANCHOR_DOUBT_OPTIONS.forEach(function (o) {
      if (o.doubt_type === item.doubt_type) return;
      html += '<button type="button" class="discuss-landing-card-btn secondary" ' +
        'data-discuss-anchor-correct="' + tid + '" data-discuss-anchor-doubt="' + esc(o.doubt_type) + '">' +
        esc(o.label) + '</button>';
    });
    html += '</div></div>';
    return html;
  }

  // 「今日の理解を自分の言葉で」（着地画面の先頭）。候補（tension / anchor）は本人が
  // 書いた発話から非同期 LLM が起こすため、質問しかしていない対話からは残す価値のある
  // 理解が生まれない。ここは候補生成を待たず、本人の記述をそのまま tension として
  // 残す唯一の直接経路（確定するのは本人・LLM 非経由。P1）。
  function reflectionSectionHtml() {
    var html = '<div class="discuss-landing-section">';
    html += '<div class="discuss-landing-section-hd">今日の理解を自分の言葉で</div>';
    html += '<p class="discuss-landing-section-body">議論して分かったこと・まだ引っかかっていることを1〜2文で。' +
      '書いたものはそのまま「わたしの地図」に残ります（あなたにだけ表示されます）。</p>';
    html += '<div class="discuss-landing-reflect" id="discuss-landing-reflect">';
    html += '<textarea id="discuss-landing-reflect-input" rows="2" ' +
      'placeholder="例: 感度が効くのは共振の幅の内側だけだと理解した。ただし雑音の効き方はまだ腑に落ちない。"></textarea>';
    html += '<div class="discuss-landing-card-actions">';
    html += '<button type="button" class="discuss-landing-card-btn" id="discuss-landing-reflect-save">残す</button>';
    html += '</div></div></div>';
    return html;
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
    // 様相の訂正チップ: 提案とは違う doubt_type で確定する（訂正も本人の操作。P1）。
    root.querySelectorAll("[data-discuss-anchor-correct]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        confirmAnchorCard(
          this.getAttribute("data-discuss-anchor-correct"),
          this.getAttribute("data-discuss-anchor-doubt") || ""
        );
      });
    });
    var reflectBtn = document.getElementById("discuss-landing-reflect-save");
    if (reflectBtn) reflectBtn.addEventListener("click", saveReflection);
    var reconBtn = document.getElementById("discuss-landing-recon-btn");
    if (reconBtn) {
      reconBtn.addEventListener("click", function () {
        sendDiscussMetric("landing_probe_clicked", {});
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
        sendDiscussMetric("landing_continue_clicked", {});
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
      sendDiscussMetric("landing_confirmed", { kind: "tension" });
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
    sendDiscussMetric("landing_dismissed", { kind: "tension" });
    try {
      await apiFetch("/learning/tension/" + encodeURIComponent(traceId) + "/dismiss", {
        method: "POST", body: JSON.stringify({}),
      });
    } catch (e) { /* best-effort */ }
    if (card) card.remove();
  }

  // doubtType を渡すとその様相に訂正して確定する（空文字なら候補どおり確定）。
  async function confirmAnchorCard(traceId, doubtType) {
    var card = document.querySelector('[data-discuss-anchor-card="' + traceId + '"]');
    sendDiscussMetric("landing_confirmed", { kind: "anchor" });
    try {
      await apiFetch("/learning/anchors/" + encodeURIComponent(traceId) + "/confirm", {
        method: "POST", body: JSON.stringify({ doubt_type: doubtType || "" }),
      });
    } catch (e) { /* best-effort */ }
    if (card) card.innerHTML = '<div class="discuss-landing-card-done">地図に置きました。</div>';
  }

  // 「今日の理解を自分の言葉で」の保存。候補の confirm と違い、失敗を握りつぶすと
  // 本人が書いた文章が消えるだけになるので、失敗時は入力を残したまま事実文を出す。
  async function saveReflection() {
    var box = document.getElementById("discuss-landing-reflect");
    var input = document.getElementById("discuss-landing-reflect-input");
    if (!box || !input) return;
    var text = (input.value || "").trim();
    if (!text) { input.focus(); return; }
    var btn = document.getElementById("discuss-landing-reflect-save");
    if (btn) btn.disabled = true;
    var ok = false;
    try {
      var res = await apiFetch(
        "/learning/courses/" + encodeURIComponent(ctx.courseId) + "/discuss/reflection",
        { method: "POST", body: JSON.stringify({ text: text }) }
      );
      ok = !!(res && res.ok);
    } catch (e) {
      ok = false;
    }
    if (!ok) {
      if (btn) btn.disabled = false;
      var err = box.querySelector(".discuss-landing-reflect-error");
      if (!err) {
        err = document.createElement("div");
        err.className = "discuss-landing-reflect-error";
        box.appendChild(err);
      }
      err.textContent = "保存できませんでした。入力はそのまま残しています。";
      return;
    }
    sendDiscussMetric("landing_reflection_saved", {});
    box.innerHTML = '<div class="discuss-landing-card-done">地図に置きました。</div>';
  }

  async function dismissAnchorCard(traceId) {
    var card = document.querySelector('[data-discuss-anchor-card="' + traceId + '"]');
    sendDiscussMetric("landing_dismissed", { kind: "anchor" });
    try {
      await apiFetch("/learning/anchors/" + encodeURIComponent(traceId) + "/dismiss", {
        method: "POST", body: JSON.stringify({}),
      });
    } catch (e) { /* best-effort */ }
    if (card) card.remove();
  }

  function buildLandingBodyHtml(tensionItems, anchorItems, reconItem) {
    var html = "";
    // 本人の言葉を先頭に置く（候補の有無に関わらず常に出す）。
    html += reflectionSectionHtml();
    html += '<div class="discuss-landing-section">';
    html += '<div class="discuss-landing-section-hd">今日話した内容を地図に置く</div>';
    if (tensionItems.length === 0 && anchorItems.length === 0) {
      html += '<div class="discuss-landing-empty">今回の対話からの候補はありません。' +
        '痕跡は残っており、後から「わたしの地図」で確認できます。</div>';
    } else {
      tensionItems.forEach(function (item) { html += tensionCardHtml(item); });
      anchorItems.forEach(function (item) { html += anchorCardHtml(item); });
    }
    // 設計 §9.1 軌道修正 #5: connect（他の理解とつなぐ）操作はこのモーダル内には
    // 実装しない（component/edge ピッカーを要し過大なため）。既存の別導線が
    // あることだけを事実文で示す。
    html += '<div class="discuss-muted discuss-landing-connect-note">候補を他の理解とつなぐ（接続）は、' +
      '「わたしの地図」の既存の導線から行えます。</div>';
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
    // 防御の二重化: switchCourse 側の Discuss.reset() 呼び出しが（将来の改修漏れ・
    // ログアウト等の未整備経路で）効かなかった場合でも、発火時点でアプリが表示して
    // いるコースと discuss セッションのコースが一致しなければ何もしない
    // （landing_shown メトリクスも送らない）。DI 未注入（getActiveCourseId が
    // 無い）ときは従来どおり常にチェックをスキップする後方互換。
    if (getActiveCourseId) {
      var activeCourseId = getActiveCourseId();
      if (activeCourseId && activeCourseId !== ctx.courseId) return;
    }
    if (turnCount <= 0) return;
    var now = Date.now();
    if (now - lastShownAt < SUPPRESS_MS) return;
    lastShownAt = now;
    turnCount = 0;

    var root = landingRoot();
    if (!root) return;
    bindLandingRootOnce(root);
    root.hidden = false;
    sendDiscussMetric("landing_shown", { reason: reason || "" });
    root.innerHTML = landingShellHtml('<div class="discuss-landing-loading">読み込み中…</div>');
    var contentEl = document.getElementById("discuss-landing-content");
    var skipBtn0 = document.getElementById("discuss-landing-skip-btn");
    if (skipBtn0) skipBtn0.addEventListener("click", skipLanding);
    var skipTop0 = document.getElementById("discuss-landing-skip-top-btn");
    if (skipTop0) skipTop0.addEventListener("click", skipLanding);

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
    init: init,
    renderOpening: renderOpening,
    maybeShowLanding: maybeShowLanding,
    notifyActivity: notifyActivity,
    renderBranchChips: renderBranchChips,
    // コース切替・ログアウト時に呼ぶ。無活動タイマー・往復回数・開幕表示済み
    // フラグに加え、旧コースの discuss コンテキスト（ctx.courseId）も破棄する
    // （そうしないと reset 後に stale な courseId で着地判定が動きうる）。
    reset: function () {
      clearInactivityTimer();
      turnCount = 0;
      openingShownCourseId = "";
      ctx.courseId = "";
      closeLanding();
    },
  };
})();
