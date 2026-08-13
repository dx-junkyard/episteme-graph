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
//
// 開幕画面の区画は**主語ごとに固定**する（docs/features/discuss_opening_authoring_design.md
// §2 / §3、Phase 0 + 0b）: 教員（このコースで議論したいこと）/ 論文（答えようとした問い・
// 主張・確かめていないこと）/ システム（まだ確認できていないところ = 解析が裏付けを取れて
// いない箇所）/ AI の推測（別の見方。出所ラベル必須）。混ぜて1区画に積まない。
// 「議論のきっかけ」（同 §7、Phase 3）は主語=論文の区画だが、他と違い投影ではなく
// **教員が承認した**素材の配信で、承認済みが無ければ区画ごと出ない（OA4）。
// 一度に並べるのは少数で、残りは同じ画面の「くわしく見る」から到達できる
// （この画面から到達できない情報は作らない, OA7）。原文の要約・和訳はしない（DM8）。
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

  // 理解サイクル Phase 1（docs/features/understanding_cycle_design.md §5.3）: 精読モードの
  // 状態は localStorage 側（app.js）が正本。discuss.js からは直接 localStorage を触らず、
  // この DI 経由でだけ読む（未注入なら常に off 扱いの後方互換）。
  var isPrecisionReadingFn = null;

  function init(opts) {
    opts = opts || {};
    getActiveCourseId = (typeof opts.getActiveCourseId === "function") ? opts.getActiveCourseId : null;
    isPrecisionReadingFn = (typeof opts.isPrecisionReading === "function") ? opts.isPrecisionReading : null;
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

  // 引用符も含めてエスケープする。本文（テキストノード）では &quot; / &#39; がそのまま
  // 引用符として表示されるので見た目は変わらず、`data-discuss-ask="…"` のような属性値に
  // 論文原文・生成文をそのまま埋める箇所での属性の破れ（値の外へ抜ける事故）を防ぐ。
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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

  // ── 理解サイクル Phase 1（OPEN / ELICIT / DIFF, docs/features/understanding_cycle_design.md
  // §5.1〜§5.3）───────────────────────────────────────────────────────
  // opening DTO の任意フィールド data.intention（{carryover, has_motive}）を読み、
  // 初回の動機記録・持ち越し問いへの再訪・（精読モード時のみ既定表示の）予想してから
  // 開くを描画する。すべて非LLM・既存 API の束ねのみ（UC8）。数値は出さない（UC9）。
  // AI が要約や差分候補を作ることはしない — 動機・予想・回答はすべて本人の逐語のまま
  // 送る（裁定 §1-1）。

  function isPrecisionReadingOn() {
    return !!(isPrecisionReadingFn && ctx.courseId && isPrecisionReadingFn(ctx.courseId));
  }

  async function postCycleIntention(body) {
    try {
      var res = await apiFetch(
        "/learning/courses/" + encodeURIComponent(ctx.courseId) + "/cycle/intention",
        { method: "POST", body: JSON.stringify(body) }
      );
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      return null;
    }
  }

  // 保存が成功したら開幕データのキャッシュを捨てる。次回の開幕取得で最新の
  // intention 状態（動機記録済み・持ち越し更新後）から組み立て直させるため
  // （古い DTO を再描画しない）。
  function invalidateOpeningCache() {
    openingCache = { courseId: "", data: null };
  }

  // 並置 DIFF（v1）: 判定・採点はしない（UC2）。左に本人の逐語、右に
  // paper_skeleton / thesis の骨格（既に投影済みのフィールドを再利用）。
  function cycleDiffHtml(docs, predictionText) {
    var html = '<div class="cycle-diff">';
    (docs || []).forEach(function (doc) {
      var thesis = (doc && doc.thesis) || {};
      var right = [thesis.central_question, thesis.central_thesis_text]
        .filter(Boolean).join(" ／ ");
      html += '<div class="cycle-diff-row">';
      if (docs.length > 1 && doc.title) {
        html += '<div class="cycle-diff-doc-title">' + esc(doc.title) + '</div>';
      }
      html += '<div class="cycle-diff-col">';
      html += '<div class="cycle-diff-hd">あなたの予想</div>';
      html += '<div class="cycle-diff-body">' + esc(predictionText || "") + '</div>';
      html += '</div>';
      html += '<div class="cycle-diff-col">';
      html += '<div class="cycle-diff-hd">論文の骨格</div>';
      html += '<div class="cycle-diff-body">' +
        (right ? esc(right) : 'まだ整理されていません') + '</div>';
      html += '</div>';
      html += '</div>';
    });
    html += '</div>';
    return html;
  }

  // REVISIT（再回答）成功後: 前回からの変化を最大3件の事実文として表示する
  // （件数・k は出さない。バックエンドが返した facts をそのまま列挙するだけ）。
  function renderCycleRevisitFacts(facts) {
    var block = document.getElementById("cycle-carryover-block");
    if (!block) return;
    var html = '<div class="discuss-landing-card-done">記録しました。</div>';
    if (facts && facts.length) {
      html += '<div class="cycle-facts">';
      html += '<div class="discuss-section-sub">前回からの変化</div>';
      facts.forEach(function (f) { html += '<div class="cycle-fact-item">' + esc(f) + '</div>'; });
      html += '</div>';
    }
    block.innerHTML = html;
  }

  // REVISIT の保存失敗時: 本人が書いた文章を消さない（P4 の趣旨、saveReflection と同型）。
  function showCycleRevisitError() {
    var block = document.getElementById("cycle-carryover-block");
    if (!block) return;
    var err = block.querySelector(".cycle-revisit-error");
    if (!err) {
      err = document.createElement("div");
      err.className = "cycle-revisit-error discuss-landing-reflect-error";
      block.appendChild(err);
    }
    err.textContent = "保存できませんでした。入力はそのまま残しています。";
  }

  function renderCycleDiff(docs, predictionText) {
    var diffArea = document.getElementById("cycle-diff-area");
    if (!diffArea) return;
    var html = cycleDiffHtml(docs, predictionText);
    html += '<div class="discuss-section-sub">予想と何が違いましたか？（任意）</div>';
    html += '<textarea class="cycle-textarea" id="cycle-diff-reflect-input" rows="2" ' +
      'placeholder="予想と何が違いましたか？（任意）"></textarea>';
    html += '<div class="discuss-landing-card-actions">';
    html += '<button type="button" class="discuss-landing-card-btn" id="cycle-diff-reflect-save">残す</button>';
    html += '<button type="button" class="discuss-landing-card-btn secondary" ' +
      'id="cycle-diff-ask-ai-btn">AIに違いの観点を出してもらう</button>';
    html += '</div>';
    diffArea.innerHTML = html;
    diffArea.hidden = false;
    sendDiscussMetric("cycle_diff_viewed", {});
    var saveBtn = document.getElementById("cycle-diff-reflect-save");
    if (saveBtn) {
      saveBtn.addEventListener("click", async function () {
        var input = document.getElementById("cycle-diff-reflect-input");
        var text = input ? (input.value || "").trim() : "";
        if (!text) return;
        saveBtn.disabled = true;
        // UPDATE: 既存の discuss reflection API をそのまま使う（新 API を作らない）。
        try {
          await apiFetch(
            "/learning/courses/" + encodeURIComponent(ctx.courseId) + "/discuss/reflection",
            { method: "POST", body: JSON.stringify({ text: text }) }
          );
        } catch (e) { /* best-effort */ }
        diffArea.innerHTML = '<div class="discuss-landing-card-done">残しました。</div>';
      });
    }
    // 理解サイクル Phase 2（AI Diff モード, 設計書 §8）: 予想と論文・出典の差分の
    // 観点候補を仮説文体で出してもらう。既存 learning_chat の1コール地点に
    // cycle_mode="diff" を添えて相乗りする（新エンドポイントを作らない）。
    var askAiBtn = document.getElementById("cycle-diff-ask-ai-btn");
    if (askAiBtn) {
      askAiBtn.addEventListener("click", function () {
        if (askAiBtn.disabled || typeof window.sendPrompt !== "function") return;
        askAiBtn.disabled = true;
        var pText = (predictionText || "").slice(0, 200);
        window.sendPrompt(
          "私の予想は『" + pText + "』でした。論文の内容と、どこが違いうるか観点を挙げてください。",
          { cycle_mode: "diff" }
        );
        setTimeout(function () { askAiBtn.disabled = false; }, 3000);
      });
    }
  }

  // ELICIT（精読モード時のみ既定表示。off でも小さなリンクから入れる, §5.3）。
  function openCyclePredictArea() {
    var area = document.getElementById("cycle-predict-area");
    if (!area || !area.hidden) return;
    var docs = (openingCache.data && Array.isArray(openingCache.data.documents))
      ? openingCache.data.documents : [];
    var html = '<div class="discuss-section-sub">この論文は何を示すと思いますか？</div>';
    if (docs.length) {
      html += '<ul class="cycle-predict-doc-list">';
      docs.forEach(function (doc) { html += '<li>' + esc(doc.title || "") + '</li>'; });
      html += '</ul>';
    }
    html += '<textarea class="cycle-textarea" id="cycle-predict-input" rows="2" ' +
      'placeholder="この論文は何を示すと思いますか？"></textarea>';
    html += '<div class="discuss-landing-card-actions">';
    html += '<button type="button" class="discuss-landing-card-btn" id="cycle-predict-save">予想を記録して開く</button>';
    html += '<button type="button" class="discuss-landing-card-btn secondary" id="cycle-predict-skip">そのまま開く</button>';
    html += '</div>';
    html += '<button type="button" class="cycle-predict-link" ' +
      'id="cycle-elicit-ask-btn">AIから問いをもらう</button>';
    html += '<div class="cycle-diff-area" id="cycle-diff-area" hidden></div>';
    area.innerHTML = html;
    area.hidden = false;
    // 理解サイクル Phase 2（AI Elicit モード, 設計書 §8）: 答えを提示せず、予測を立てる
    // ための問いを一つだけもらう。既存 learning_chat の1コール地点に
    // cycle_mode="elicit" を添えて相乗りする（新エンドポイントを作らない）。
    var elicitBtn = document.getElementById("cycle-elicit-ask-btn");
    if (elicitBtn) {
      elicitBtn.addEventListener("click", function () {
        if (elicitBtn.disabled || typeof window.sendPrompt !== "function") return;
        elicitBtn.disabled = true;
        window.sendPrompt(
          "予想を立てる前に、考えるための問いを一つください。",
          { cycle_mode: "elicit" }
        );
        setTimeout(function () { elicitBtn.disabled = false; }, 3000);
      });
    }
    var skipBtn = document.getElementById("cycle-predict-skip");
    if (skipBtn) {
      skipBtn.addEventListener("click", function () {
        area.hidden = true;
        area.innerHTML = "";
      });
    }
    var saveBtn = document.getElementById("cycle-predict-save");
    if (saveBtn) {
      saveBtn.addEventListener("click", async function () {
        var predictInput = document.getElementById("cycle-predict-input");
        var predictionText = predictInput ? (predictInput.value || "").trim() : "";
        var motiveInput = document.getElementById("cycle-motive-input");
        var motiveText = motiveInput ? (motiveInput.value || "").trim() : "";
        saveBtn.disabled = true;
        // 動機（空可・そのままの値。予想のみのときの穴埋め文言は作らない）+ 予想を
        // 同じ intention 行に同居させる（行を増やさない, §5.3）。
        await postCycleIntention({
          role: "opening_motive", text: motiveText, prediction: { text: predictionText },
        });
        sendDiscussMetric("cycle_prediction_saved", {});
        invalidateOpeningCache();
        renderCycleDiff(docs, predictionText);
      });
    }
  }

  function renderCycleMotiveBlock() {
    var precisionOn = isPrecisionReadingOn();
    var html = '<div class="cycle-opening-block" id="cycle-motive-block">';
    html += '<div class="discuss-section-hd">この論文を、なぜ今開きましたか？</div>';
    html += '<textarea class="cycle-textarea" id="cycle-motive-input" rows="2" ' +
      'placeholder="任意・書かなくても進めます"></textarea>';
    html += '<div class="discuss-landing-card-actions">';
    html += '<button type="button" class="discuss-landing-card-btn" data-cycle-motive-save>記録する</button>';
    if (precisionOn) {
      html += '<button type="button" class="discuss-landing-card-btn secondary" data-cycle-predict-open>予想してから開く</button>';
    }
    html += '</div>';
    if (!precisionOn) {
      html += '<button type="button" class="cycle-predict-link" data-cycle-predict-open>予想してから開く</button>';
    }
    html += '<div class="cycle-predict-area" id="cycle-predict-area" hidden></div>';
    html += '</div>';
    return html;
  }

  function renderCycleCarryoverBlock(carryover) {
    var tid = esc(carryover.trace_id);
    var html = '<div class="cycle-opening-block cycle-carryover" id="cycle-carryover-block">';
    html += '<div class="discuss-section-hd">前回、この問いを残しました</div>';
    html += '<div class="discuss-claim-text">' + esc(carryover.text || "") + '</div>';
    html += '<div class="discuss-section-sub">いまならどう考えますか？（任意）</div>';
    html += '<textarea class="cycle-textarea" id="cycle-revisit-input" rows="2" ' +
      'placeholder="いまならどう考えますか？（任意）"></textarea>';
    html += '<div class="discuss-landing-card-actions">';
    html += '<button type="button" class="discuss-landing-card-btn" data-cycle-revisit-save="' + tid + '">書いて進む</button>';
    html += '<button type="button" class="discuss-landing-card-btn secondary" data-cycle-revisit-skip>そのまま進む</button>';
    html += '</div>';
    html += '</div>';
    return html;
  }

  // opening DTO の data.intention を読み、初回動機／持ち越し再訪のいずれかを描画する
  // （両方同時には出さない・二問目は出さない, §5.1/§5.2）。intention フィールド自体が
  // 無い場合は何も描画しない（fail-open: 旧 DTO・未対応環境でも壊れない）。
  function renderCycleOpeningSection(data) {
    var intention = data && data.intention;
    if (!intention) return "";
    if (intention.carryover) return renderCycleCarryoverBlock(intention.carryover);
    if (intention.has_motive === false) return renderCycleMotiveBlock();
    return "";
  }

  // ── 開幕画面（§3.3）─────────────────────────────────────────────────

  // 起点となる定型質問。押すとこの文がそのまま送信される（bindOpeningEvents）。
  function askText(label) {
    return "「" + label + "」について、この論文での位置づけと根拠を教えてください。";
  }

  // 構造帰属（Structure-Anchored Questions, 経路A = 明示アンカー）の element_type 語彙。
  // 正本の対応表は backend/core/structure_anchor/schema.py::ELEMENT_TYPE_TO_ANCHOR_TYPE。
  // app.js の materialAnchorElementType と同じ規約に揃える — 数式だけ専用語彙
  // ("formula" → anchor_type "equation") があり、claim / stage には対応語彙が無いので
  // backend 側の既定フォールバック（"concept"）に委ねる（backend/core は変更しない）。
  function anchorElementType(kind) {
    return kind === "equation" ? "formula" : "concept";
  }

  // 開幕画面のチップ・ノードが指す元要素（{id, kind, label}）。id を持たないもの
  // （agent が合成した文・定型文・教員の入力）にはアンカーを付けない — 実在しない
  // 要素 id を捏造しない。DTO の items[] は type ("claim" | "equation") を持つ。
  function itemRef(item, kind) {
    if (!item || !item.id) return null;
    return { id: item.id, kind: item.type || kind || "", label: item.label || "" };
  }

  // 送信時に structure_anchor 経路A（learner_selected）へ渡す属性。無ければ空文字。
  function anchorAttrs(ref) {
    if (!ref || !ref.id) return "";
    return ' data-discuss-el-id="' + esc(ref.id) + '"' +
      ' data-discuss-el-type="' + esc(anchorElementType(ref.kind)) + '"' +
      ' data-discuss-el-label="' + esc(ref.label || ref.id) + '"';
  }

  function discussChip(label, ref) {
    if (!label) return "";
    return '<button type="button" class="discuss-chip" data-discuss-ask="' + esc(askText(label)) + '"' +
      anchorAttrs(ref) + '>' + esc(label) + '</button>';
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
      esc(askText(label)) + '"' + anchorAttrs(itemRef(item, "claim")) + '>この主張について聞く</button>';
    if (label.length > CLAIM_CLAMP_HINT_CHARS) {
      html += '<button type="button" class="discuss-claim-more" data-discuss-expand="1">全文を見る</button>';
    }
    html += '</div></div>';
    return html;
  }

  // 支持構造（前提 / 導出の核 / 訂正の源 …）は A層の分類語彙で、閉じた状態では
  // 専門用語の縦積みにしか見えない。区画ごとに details を開かず、1つに畳んでおく。
  // entries[]（agent が合成した1文）があればそれを本文として読ませ、無い場合だけ
  // 従来の参照チップ（items[]）へ縮退する。
  function renderSupportDetails(sections) {
    var usable = (sections || []).filter(function (sec) {
      if (!sec) return false;
      var hasEntries = Array.isArray(sec.entries) && sec.entries.length > 0;
      var hasItems = Array.isArray(sec.items) && sec.items.length > 0;
      return hasEntries || hasItems;
    });
    if (!usable.length) return "";
    var html = '<details class="discuss-support-section discuss-support-details">';
    html += '<summary>支持構造をくわしく見る</summary>';
    usable.forEach(function (sec) {
      html += '<div class="discuss-support-group">';
      html += '<div class="discuss-support-group-hd">' + esc(sec.label || "") + '</div>';
      var entries = Array.isArray(sec.entries) ? sec.entries : [];
      if (entries.length) {
        entries.forEach(function (entry) { html += supportEntryHtml(entry); });
      } else {
        html += '<div class="discuss-chip-row">';
        sec.items.forEach(function (item) { html += discussChip(item && item.label, itemRef(item)); });
        html += '</div>';
      }
      html += '</div>';
    });
    html += '</details>';
    return html;
  }

  // 支持構造の1エントリ（agent が合成した1文 + その参照）。合成文があればそれを読ませ、
  // 参照はチップとして下に並べる（従来は claim の生ラベルのチップだけだった）。
  function supportEntryHtml(entry) {
    var text = (entry && entry.text) || "";
    var items = (entry && Array.isArray(entry.items)) ? entry.items : [];
    if (!text && !items.length) return "";
    var html = '<div class="discuss-support-entry">';
    if (text) html += '<div class="discuss-support-entry-text">' + esc(text) + '</div>';
    if (items.length) {
      html += '<div class="discuss-chip-row">';
      items.forEach(function (item) { html += discussChip(item && item.label, itemRef(item)); });
      html += '</div>';
    }
    html += '</div>';
    return html;
  }

  // 「この論文が答えようとした問い」（主語=論文）。thesis artifact の central_question /
  // paper_skeleton の paper_goal をそのまま出す（投影の是正 Phase 0。要約・和訳はしない）。
  function renderQuestionSection(doc) {
    var thesis = doc.thesis || {};
    var question = thesis.central_question || "";
    var goal = thesis.paper_goal || "";
    if (!question && !goal) return "";
    var html = '<div class="discuss-section discuss-section-question">';
    html += '<div class="discuss-section-hd">この論文が答えようとした問い</div>';
    html += '<div class="discuss-section-sub">ここから議論を始めると、話がつながります。</div>';
    if (question) {
      html += '<div class="discuss-claim">';
      html += '<div class="discuss-claim-text">' + esc(question) + '</div>';
      html += '<div class="discuss-claim-actions">';
      html += '<button type="button" class="discuss-claim-ask" data-discuss-ask="' +
        esc(askText(question)) + '">この問いから聞く</button>';
      html += '</div></div>';
    }
    if (goal) {
      html += '<div class="discuss-section-sub discuss-paper-goal">この論文が目指したこと: ' +
        esc(goal) + '</div>';
    }
    html += '</div>';
    return html;
  }

  function renderThesisSection(doc) {
    var thesis = doc.thesis;
    var html = '<div class="discuss-section discuss-section-thesis">';
    html += '<div class="discuss-section-hd">この論文の主張</div>';
    html += '<div class="discuss-section-sub">この論文がいちばん言いたいことです。</div>';
    if (thesis) {
      // 中心命題は agent が合成した1文（central_thesis_text）を優先して読ませる。
      // claim の生ラベル（論文原文）も従来どおり併記する（置き換えではなく併記）。
      if (thesis.central_thesis_text) {
        html += claimBlockHtml({ label: thesis.central_thesis_text });
      }
      var claims = Array.isArray(thesis.central_claims) ? thesis.central_claims : [];
      var equations = Array.isArray(thesis.central_equations) ? thesis.central_equations : [];
      claims.forEach(function (c) { html += claimBlockHtml(c); });
      if (equations.length) {
        html += '<div class="discuss-section-sub discuss-central-eq-hd">中心となる式</div>';
        html += '<div class="discuss-chip-row">';
        equations.forEach(function (e) { html += discussChip(e && e.label, itemRef(e, "equation")); });
        html += '</div>';
      }
    } else {
      html += '<div class="discuss-muted">この論文の中心命題はまだ整理されていません。</div>';
    }
    html += '</div>';
    return html;
  }

  // 「議論のきっかけ」（主語=論文）。ここだけは投影ではなく、解析結果をもとに生成され
  // **担当教員が承認した**素材（discussion_seeds）を出す
  // （docs/features/discuss_opening_authoring_design.md §2 / §7）。
  //  - 承認済みが1件も無ければ区画ごと出さない（Phase 0 の画面と同一・OA4）。
  //  - 署名（authored_by_label）はサーバが付ける文をそのまま出す。ここで文を作らない。
  //  - body は「立場を求める問い」そのものなので、**学習者の発話としては送らない**。
  //    送ってしまうと AI が発話タイプ別ルール1（質問には即答）でその問いに自分で
  //    答えきってしまい、係留（学習者が先に立場を述べ、AI が言い直す）が起動しない
  //    （discuss_dialogue_alignment_design.md §3-1）。押したら LLM を呼ばずに
  //    （DM8）アシスタント側の問いとしてチャット欄へ置き、学習者の入力を待つ
  //    （app.js の window.discussPostSeedPrompt）。観測は既存の
  //    opening_starter_clicked に相乗りする。
  //  - evidence_quote の照合先は A層が生成したテキスト（thesis 合成文・正規化済み
  //    statement・導出の理由文など）であって論文原文ではない。「論文の記述」と
  //    名乗らせない（出所の正直さ, OA7）。
  function renderDiscussionSeedsSection(doc) {
    var seeds = (doc && Array.isArray(doc.discussion_seeds)) ? doc.discussion_seeds : [];
    var usable = seeds.filter(function (s) { return s && s.body; });
    if (!usable.length) return "";
    var notice = "";
    usable.forEach(function (s) {
      if (!notice && s.authored_by_label) notice = s.authored_by_label;
    });
    var html = '<div class="discuss-section discuss-section-seeds">';
    html += '<div class="discuss-section-hd">議論のきっかけ</div>';
    html += '<div class="discuss-section-sub">立場を決めて答えると、そこから議論が進みます。</div>';
    usable.forEach(function (s) {
      html += '<div class="discuss-seed-item">';
      html += '<div class="discuss-seed-body">' + esc(s.body) + '</div>';
      if (s.evidence_quote) {
        html += '<div class="discuss-seed-quote">解析結果にもとづく記述: 『' + esc(s.evidence_quote) + '』</div>';
      }
      html += '<div class="discuss-claim-actions">';
      html += '<button type="button" class="discuss-claim-ask discuss-seed-ask" data-discuss-seed-ask="' +
        esc(s.body) + '">この問いから話す</button>';
      html += '</div></div>';
    });
    if (notice) {
      html += '<div class="discuss-attribution discuss-seed-attribution">' + esc(notice) + '</div>';
    }
    html += '</div>';
    return html;
  }

  // 「別の見方（AI の提示）」（主語=AI の推測）。alternative_theses は出典（claim_ids /
  // evidence_block_ids）を持たない artifact なので、サーバが付ける出所ラベル
  // （attribution_label）を必ず添えて出す。無署名で論文の主張として並べない。
  function renderAlternativesSection(thesis) {
    var alts = (thesis && Array.isArray(thesis.alternatives)) ? thesis.alternatives : [];
    if (!alts.length) return "";
    var html = '<div class="discuss-section discuss-section-alternatives">';
    html += '<div class="discuss-section-hd">別の見方（AI の提示）</div>';
    html += '<div class="discuss-section-sub">論文の主張そのものではありません。' +
      '解析が別の言い方の候補として挙げたものです。</div>';
    alts.forEach(function (alt) {
      var text = (alt && alt.text) || "";
      if (!text) return;
      html += '<div class="discuss-alt-item">';
      html += '<div class="discuss-alt-text">' + esc(text) + '</div>';
      if (alt.attribution_label) {
        html += '<div class="discuss-attribution">' + esc(alt.attribution_label) + '</div>';
      }
      html += '</div>';
    });
    html += '</div>';
    return html;
  }

  // 脆い箇所は**主語ごとに2区画**に分ける（投影の是正 §2/§3）。
  //  - kind='assumption'（subject='paper'）: この論文が確かめていないこと
  //  - kind='backbone_node'（subject='system'）: 解析がまだ裏付けを取れていないところ
  // 混ぜて1つの見出しに積むと（旧実装）、システムの解析状態が論文の弱点として
  // 読まれてしまう。
  function fragileItemsHtml(points) {
    var html = "";
    points.forEach(function (f) {
      html += '<div class="discuss-fragile-item">';
      if (f && f.label) html += '<div class="discuss-fragile-label">' + esc(f.label) + '</div>';
      html += '<div class="discuss-fragile-fact">' + esc((f && f.fact_line) || "") + '</div>';
      html += '</div>';
    });
    return html;
  }

  // D層台帳由来（コース全体の未検証前提）。document_id を持たないため、以前は
  // renderThesisSection の document_id 一致フィルタで画面に一度も出ていなかった。
  // 開幕画面から到達できる場所を必ず持たせる（OA7）。
  function renderPaperUnverifiedSection(fragilePoints) {
    var points = (fragilePoints || []).filter(function (f) { return f && f.kind === "assumption"; });
    if (!points.length) return "";
    var html = '<details class="discuss-section discuss-section-unverified">';
    html += '<summary class="discuss-section-hd">この論文が確かめていないこと</summary>';
    html += '<div class="discuss-section-sub">前提として置かれていて、検証の記録がない箇所です。</div>';
    html += fragileItemsHtml(points);
    html += '</details>';
    return html;
  }

  function renderSystemUnconfirmedSection(fragilePoints, documentId) {
    var points = (fragilePoints || []).filter(function (f) {
      return f && f.kind === "backbone_node" && (!documentId || f.document_id === documentId);
    });
    if (!points.length) return "";
    var html = '<div class="discuss-section discuss-section-unconfirmed">';
    html += '<div class="discuss-section-hd">まだ確認できていないところ</div>';
    html += '<div class="discuss-section-sub">論文の弱点ではありません。' +
      'この教材を解析したときに、裏付けを取れなかった箇所です。</div>';
    html += fragileItemsHtml(points);
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
        // 構造帰属（経路A）: main 層のバックボーンノードは theory_component なので
        // node_id をそのままアンカー id にする（anchor_type は concept）。
        html += '<button type="button" class="' + cls + '" data-discuss-ask="' + esc(askText(label)) + '"' +
          anchorAttrs(n && n.node_id ? { id: n.node_id, kind: "concept", label: label } : null) +
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

  // 「このコースで議論したいこと」（主語=教員）。教員の任意入力のみで AI 生成は
  // 関与しない。未入力なら区画ごと出さない（欠落を警告・催促しない）。
  function renderCourseFocusSection(focus) {
    if (!focus) return "";
    var html = '<div class="discuss-section discuss-section-focus">';
    html += '<div class="discuss-section-hd">このコースで議論したいこと</div>';
    html += '<div class="discuss-section-sub">担当教員が書いたものです。</div>';
    html += '<div class="discuss-focus-text">' + esc(focus) + '</div>';
    html += '</div>';
    return html;
  }

  // 開幕画面に一度に並べるのは少数（このコースで議論したいこと・きっかけ・問い・主張）。
  // 残り（バックボーン・支持構造・別の見方・まだ確認できていないところ）は同じ画面の
  // 「くわしく見る」から到達できるようにする。この画面から到達できない情報は作らない（OA7）。
  function buildOpeningHtml(data) {
    var docs = Array.isArray(data.documents) ? data.documents : [];
    var focus = (data && data.course_focus) || "";
    // fail-closed: 出せる中身（論文の投影 or 教員の入力）が何も無ければ描画しない。
    if (!docs.length && !focus) return "";
    var multi = docs.length > 1;
    var fragile = Array.isArray(data.fragile_points) ? data.fragile_points : [];
    var html = '<div class="discuss-opening">';
    // 理解サイクル Phase 1（OPEN, §5.1/§5.2）: 動機記録・持ち越し再訪は、誰の発話でも
    // ない「本人への問いかけ」なので、教員の提示（誰が言っているか）よりも先に置く。
    html += renderCycleOpeningSection(data);
    // 教員の提示を最初に読ませる（誰が開いても同じ画面、を非LLMで解く唯一の手段）。
    html += renderCourseFocusSection(focus);
    // 進行の型を先に予告する（discuss_dialogue_alignment_design.md §6）。序盤の
    // 「言い直し＋確認」の往復が冗長に見えないよう、静的文言で流れだけ伝える（DM6）。
    html += '<div class="discuss-opening-note">' +
      'トピック順に縛られず、論文全体について話せます。' +
      '回答の根拠（教材由来か、AIの一般知識か）は各回答に表示されます。' +
      'まず互いの読みを突き合わせてから、論文の主張を一緒に検討します。</div>';
    // 行動の起点（最初の一手）を最上部に置く。以前は最下部にあり、初見の学習者が
    // 最初に取れる操作が折り返し線の外にあった。
    html += renderFirstMoveSection();
    docs.forEach(function (doc) {
      html += '<div class="discuss-opening-doc">';
      if (multi) html += '<div class="discuss-opening-doc-title">' + esc(doc.title || "") + '</div>';
      html += renderQuestionSection(doc);
      html += renderThesisSection(doc);
      // 承認済みの「議論のきっかけ」は一等地（折りたたみの外）に置く。無ければ何も出ない。
      html += renderDiscussionSeedsSection(doc);
      html += '<details class="discuss-more">';
      html += '<summary>この論文の組み立てをくわしく見る</summary>';
      html += renderBackboneSection(doc);
      html += renderSupportDetails(doc.thesis && doc.thesis.support_sections);
      html += renderAlternativesSection(doc.thesis);
      html += renderSystemUnconfirmedSection(fragile, doc.document_id);
      html += '</details>';
      html += '</div>';
    });
    // D層台帳由来（コース全体の前提）は document に紐づかないので docs ループの外に出す。
    html += renderPaperUnverifiedSection(fragile);
    // トップレベルの truncated は fragile_points（確かめていないこと／確認できていない
    // ところ）の切り詰めを意味する（core/discuss/opening.py の fragile_truncated）。
    // 論文・資料の一覧の話にすり替えない。
    if (data.truncated) {
      html += '<div class="discuss-muted discuss-truncated-note">' +
        '確かめていないこと・確認できていないところは、主要なものに絞って表示しています。</div>';
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
        if (!text || !window.sendPrompt) return;
        // 構造帰属（経路A・明示アンカー, DM3 / 設計 §3.4）: 元要素の id があれば
        // 既存の element_id / element_type / element_label で添える。これで
        // attribution_source='learner_selected' として同期確定し、開幕チップ起点の
        // 問いも「どこへの問いか」が残る（doubt_type は unclassified のまま）。
        var elId = this.getAttribute("data-discuss-el-id") || "";
        if (!elId) { window.sendPrompt(text); return; }
        window.sendPrompt(text, {
          element_id: elId,
          element_type: this.getAttribute("data-discuss-el-type") || "concept",
          element_label: this.getAttribute("data-discuss-el-label") || elId,
        });
      });
    });
    // 「議論のきっかけ」（§7）は立場を求める問い。学習者の発話として送らず、
    // アシスタントの問いとしてチャット欄へ置いて学習者の応答を待つ（係留の起動。
    // discuss_dialogue_alignment_design.md §3-1）。LLM は呼ばない（DM8）。
    // 係留対象は document 全体なので構造帰属（経路A）の対象にはしない。
    containerEl.querySelectorAll("[data-discuss-seed-ask]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var text = this.getAttribute("data-discuss-seed-ask");
        sendDiscussMetric("opening_starter_clicked", {});
        if (text && window.discussPostSeedPrompt) window.discussPostSeedPrompt(text);
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

    // 理解サイクル Phase 1（OPEN, §5.1〜§5.3）: 初回動機の記録・持ち越し問いへの
    // 再訪（書いて進む／そのまま進む）・予想してから開くの配線。
    var cycleMotiveSaveBtn = containerEl.querySelector("[data-cycle-motive-save]");
    if (cycleMotiveSaveBtn) {
      cycleMotiveSaveBtn.addEventListener("click", async function () {
        var input = document.getElementById("cycle-motive-input");
        var text = input ? (input.value || "").trim() : "";
        cycleMotiveSaveBtn.disabled = true;
        await postCycleIntention({ role: "opening_motive", text: text });
        sendDiscussMetric("cycle_motive_saved", {});
        invalidateOpeningCache();
        var block = document.getElementById("cycle-motive-block");
        if (block) block.innerHTML = '<div class="discuss-landing-card-done">記録しました。</div>';
      });
    }
    containerEl.querySelectorAll("[data-cycle-predict-open]").forEach(function (btn) {
      btn.addEventListener("click", function () { openCyclePredictArea(); });
    });
    var cycleRevisitSkipBtn = containerEl.querySelector("[data-cycle-revisit-skip]");
    if (cycleRevisitSkipBtn) {
      cycleRevisitSkipBtn.addEventListener("click", function () {
        var block = document.getElementById("cycle-carryover-block");
        if (block) block.remove();
      });
    }
    var cycleRevisitSaveBtn = containerEl.querySelector("[data-cycle-revisit-save]");
    if (cycleRevisitSaveBtn) {
      cycleRevisitSaveBtn.addEventListener("click", async function () {
        var traceId = cycleRevisitSaveBtn.getAttribute("data-cycle-revisit-save");
        var input = document.getElementById("cycle-revisit-input");
        var text = input ? (input.value || "").trim() : "";
        cycleRevisitSaveBtn.disabled = true;
        var data = await postCycleIntention({
          role: "revisit_answer", text: text, source_trace_id: traceId,
        });
        if (!data) {
          cycleRevisitSaveBtn.disabled = false;
          showCycleRevisitError();
          return;
        }
        sendDiscussMetric("cycle_revisit_answered", {});
        invalidateOpeningCache();
        renderCycleRevisitFacts(data.facts || []);
      });
    }
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

  // 理解サイクル Phase 1（LEAVE, §5.5）: 次に持ち越す問いの候補（当日セッションの
  // 本人痕跡）。並び順はサーバが決定する（最大5件・数値は出さない）。
  // buildLandingBodyHtml のシグネチャは既存テストが文字列一致で固定しているため
  // 引数を増やさず、この変数経由で受け渡す（maybeShowLanding が描画直前に更新する）。
  var _cycleLandingCandidates = [];

  async function fetchLandingCandidates(courseId) {
    try {
      var res = await apiFetch(
        "/learning/courses/" + encodeURIComponent(courseId) + "/cycle/landing-candidates"
      );
      if (!res.ok) return [];
      var data = await res.json();
      return Array.isArray(data && data.candidates) ? data.candidates : [];
    } catch (e) {
      return [];
    }
  }

  function cycleLeaveSectionHtml(candidates) {
    var html = '<div class="discuss-landing-section cycle-leave-section" id="cycle-leave-section">';
    html += '<div class="discuss-landing-section-hd">次に持ち越すなら、どの問いにしますか？</div>';
    (candidates || []).forEach(function (c) {
      var tid = esc(c.trace_id);
      var label = esc(c.label || "");
      html += '<button type="button" class="cycle-leave-option" data-cycle-leave-pick="' + tid +
        '" data-cycle-leave-label="' + label + '">' + label;
      if (c.revisit) html += '<span class="cycle-leave-chip">あとで戻る</span>';
      html += '</button>';
    });
    html += '<button type="button" class="cycle-leave-free-link" id="cycle-leave-free-link">自分の言葉で書く</button>';
    html += '<div class="cycle-leave-input" id="cycle-leave-input" hidden></div>';
    html += '</div>';
    return html;
  }

  // 「自分の言葉で書く」を押すと、リンクを隠して入力欄をその場に展開する
  // （openTensionInlineConfirm と同型のインライン展開パターン）。
  function openCycleLeaveFreeInput() {
    var link = document.getElementById("cycle-leave-free-link");
    var box = document.getElementById("cycle-leave-input");
    if (!link || !box) return;
    link.hidden = true;
    box.hidden = false;
    box.innerHTML =
      '<textarea rows="2" id="cycle-leave-free-textarea" placeholder="次に考えたい問いを書く（任意）"></textarea>' +
      '<div class="discuss-landing-card-actions">' +
      '<button type="button" class="discuss-landing-card-btn" id="cycle-leave-free-save">残す</button>' +
      '</div>';
    var ta = document.getElementById("cycle-leave-free-textarea");
    if (ta) ta.focus();
    var saveBtn = document.getElementById("cycle-leave-free-save");
    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        var text = ta ? (ta.value || "").trim() : "";
        if (!text) return;
        saveCycleCarryover(text, "");
      });
    }
  }

  // 選択 or 自由入力の確定。旧 carryover は role='carryover_question' の新規記録で
  // サーバ側が superseded に遷移させる（UC6・行削除しない）。
  async function saveCycleCarryover(text, sourceTraceId) {
    var body = { role: "carryover_question", text: text };
    if (sourceTraceId) body.source_trace_id = sourceTraceId;
    await postCycleIntention(body);
    sendDiscussMetric("cycle_carryover_saved", {});
    invalidateOpeningCache();
    var section = document.getElementById("cycle-leave-section");
    if (section) {
      section.innerHTML =
        '<div class="discuss-landing-section-hd">次に持ち越すなら、どの問いにしますか？</div>' +
        '<div class="discuss-landing-card-done">次回の開幕で待っています。</div>';
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
    // 理解サイクル Phase 1（LEAVE, §5.5）: 次に持ち越す問いの選択・自由入力。
    root.querySelectorAll("[data-cycle-leave-pick]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        saveCycleCarryover(
          this.getAttribute("data-cycle-leave-label") || "",
          this.getAttribute("data-cycle-leave-pick") || ""
        );
      });
    });
    var cycleLeaveFreeLink = document.getElementById("cycle-leave-free-link");
    if (cycleLeaveFreeLink) {
      cycleLeaveFreeLink.addEventListener("click", openCycleLeaveFreeInput);
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

    // 理解サイクル Phase 1（LEAVE, §5.5）: 新規入力欄ではなく選択リスト。何も選ばず
    // 閉じても何も起きない（既存のスキップ動線に干渉しない）。
    html += cycleLeaveSectionHtml(_cycleLandingCandidates);

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
    _cycleLandingCandidates = [];
    try {
      var results = await Promise.all([
        fetchDigest("/learning/courses/" + encodeURIComponent(courseIdForFetch) + "/tension/digest"),
        fetchDigest("/learning/courses/" + encodeURIComponent(courseIdForFetch) + "/anchors/digest"),
        fetchReconNext(courseIdForFetch),
        fetchLandingCandidates(courseIdForFetch),
      ]);
      tensionDigest = results[0];
      anchorDigest = results[1];
      reconItem = results[2];
      _cycleLandingCandidates = results[3];
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
    // lastShownAt（抑制窓）も必ず落とす — 残すと切替後の新しいコースの着地画面が
    // 最初の10分だけ「直近に出した」と誤判定されて出なくなる。
    reset: function () {
      clearInactivityTimer();
      turnCount = 0;
      lastShownAt = 0;
      openingShownCourseId = "";
      ctx.courseId = "";
      _cycleLandingCandidates = [];
      invalidateOpeningCache();
      closeLanding();
    },
  };
})();
