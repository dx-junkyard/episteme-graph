/**
 * リリース前の確認（Release Review Flow）
 *
 * 正本: docs/features/release_review_flow_design.md（不変条項 RR1〜RR7）。
 *
 *   RR1 既定は「提示されたものが出る」— 閉じてもスキップしても学習者側の表示は変わらない
 *   RR2 承認は人間の1操作 —「次へ」を教員の明示操作として承認に使う
 *   RR3 一括承認の出所を偽らない — accept API が review_note と監査 action を残す
 *   RR4 修正はいつでも — 各行の [却下] [再検討] を出したままにする
 *   RR6 数値を見せない — weight / confidence は読まない（件数のみ事実として出す）
 *   RR7 リリースを止めない — API 失敗・骨格なし・配置ゼロでも次へ進める（fail-open）
 *
 * ES5 で書く（admin.js / admin-llm-usage.js と同じ流儀）。DI は init(deps)。
 * ポーリングしない。
 */
(function () {
  "use strict";

  var deps = null;

  // ステップ定義（順序は設計書 §2 の図と一致させる）。
  var STEPS = [
    { key: "map", label: "学習マップの割り当て", next: "この対応で次へ" },
    { key: "landscape", label: "論文の位置づけ", next: "この配置で次へ" },
    { key: "publish", label: "公開", next: "" }
  ];

  var NOTICE_INTRO =
    "AI が作成した内容を確認します。修正しなければ、ここに表示されているものがそのまま受講者に届きます。";
  var NOTICE_AUTO_OPENED =
    "コースを登録しました。受講者に何が届くかを確認して公開まで進めます。";
  // RR1/RR2: 「次へ」の意味を画面上で明示する（黙って承認扱いにしない）。
  var NOTICE_NEXT_MEANING =
    "「次へ」を押すと、表示されている内容を確認したものとして記録します。個別に直したいものは各行の[却下][再検討]で変更できます。";
  // 改訂原則1（vision.md §4）: 確定は「後から再構成・異議申立できる手続」にのみ与える。
  // 「次へ」の意味（NOTICE_NEXT_MEANING）に続けて、確定を覆せる経路を画面に書く。
  var NOTICE_REOPEN =
    "確認後も、教材管理の「位置づけ（分野マップ）」から個別に再検討・却下へ戻せます。";
  // 記録した結果、提示されていたものと確認したものが一致したかの事実文（数値は出さない）。
  var NOTICE_ACCEPT_MATCHED = "表示されていた配置と確認した配置は一致しています";
  // 第 23 波 F5: 差があったときの出口は「再読み込み」（ウィザードが消える）ではなく、
  // ウィザードの中でステップ 2 に戻って最新の配置を取り直す操作にする。
  var NOTICE_ACCEPT_MISMATCH =
    "表示と確認した配置に差がありました。下の「ステップ 2 に戻って確かめる」で最新の配置を表示し直せます";
  var BACK_TO_LANDSCAPE_LABEL = "ステップ 2 に戻って確かめる";
  // 第 23 波・第 2 波 F5: ステップ 3 の「確認として記録したもの」（件数のみ・RR6）。
  var RECORDED_HEADING = "ステップ 2 で確認として記録したもの";
  var RECORDED_CONFIRMED_PREFIX = "確認済みにした配置: ";
  var RECORDED_EVIDENCE_PREFIX = "根拠を開いて読んだ配置: ";
  var RECORDED_DECIDED_PREFIX = "却下・再検討にした配置: ";
  // accept 後に配置を取り直せなかったときの事実文（確認前の件数で要約しない）。
  var NOTICE_LANDSCAPE_STALE =
    "確認を記録しました。最新の位置づけを読み込めなかったため、件数の要約は表示していません。";
  var EVIDENCE_SUMMARY = "根拠を見る";
  var EVIDENCE_EMPTY = "この配置には論文からの引用が残っていません。";
  var LANDSCAPE_EMPTY =
    "この論文の分野マップ上の位置づけはまだありません。このまま公開できます。";
  var LANDSCAPE_INTRO =
    "この論文が分野マップ（基準地図）のどこに位置づくかの候補です。1つの論文が複数の領域・複数の観点に置かれることがあります。";
  // サーバの status_label が取れないときのフォールバック。正本は
  // backend/core/label_vocab.py::LANDSCAPE_STATUS_LABELS の逐語ミラー
  // （test_landscape_status_vocab_mirror.py）。行のボタン「再検討」の結果は「再検討中」。
  var STATUS_FALLBACK_LABELS = {
    inferred: "AI推定（未確認）",
    confirmed: "教員確認済み",
    rejected: "却下",
    review_required: "再検討中",
    superseded: "再解析により置換"
  };
  // RR4: 一括の「次へ」とは独立に、個別の修正手段を常に出す。
  var ROW_ACTIONS = [
    ["rejected", "却下"],
    ["review_required", "再検討"]
  ];

  var state = {
    courseId: null,
    courseTitle: "",
    stepIndex: 0,
    autoOpened: false,
    busy: false,
    landscape: null,
    // accept の後に配置を取り直せなかった（ステップ 3 で確認前の件数を言わない）。
    landscapeStale: false,
    // 提示と適用に差があった（ステップ 3 にステップ 2 へ戻る操作を出す）。
    acceptMismatch: false,
    mapSaved: false,
    published: false,
    // 是正 F7（2026-09-10・六つのレンズ §4 第1波 #5 / 02_teacher.md 提案7）:
    // 「根拠（逐語引用）の折りたたみを実際に開いた行」を DOM の toggle イベントから
    // だけ集める。開いた/開かなかったの2値のみで、滞在時間・視線などは測らない
    // （原則5: 監視しない）。値は来歴申告としてサーバへ渡し、サーバは検証しない
    // （DC4）。「根拠を描くコードがある」ことを「根拠が出ていた」と申告しない。
    evidenceExpanded: {},
    // 第 23 波・第 2 波 F5: ステップ 3 の「確認として記録したもの」の材料。
    // acceptRecord は accept 応答の decision_context から読み時に作る（サーバ変更なし）。
    // rowDecided はこのウィザードで却下・再検討にした配置の id（件数だけを出す — RR6）。
    acceptRecord: null,
    rowDecided: {}
  };

  function esc(text) {
    return deps && deps.escHtml ? deps.escHtml(text == null ? "" : text) : "";
  }

  function api(path, options) {
    return deps.apiFetch(path, options);
  }

  function el(id) {
    return document.getElementById(id);
  }

  function setNotice(message, isError) {
    var node = el("release-review-notice");
    if (!node) return;
    node.textContent = message || "";
    node.className = isError
      ? "release-review-notice release-review-notice-error"
      : "release-review-notice";
  }

  // IK-0376: 一括確認で対象外になった論文があったときの事実文（サーバの skipped_note を
  // そのまま描く。文言をここに書き写さない）。色で強調しない・操作要素ではないので
  // data-ui-anchor を付けない。空なら隠す。
  function setSkippedNote(message) {
    var node = el("release-review-skipped-note");
    if (!node) return;
    node.textContent = message || "";
    node.hidden = !message;
  }

  function errorDetail(res, fallback) {
    return res
      .json()
      .then(function (body) {
        var detail = body && body.detail;
        return typeof detail === "string" && detail ? detail : fallback;
      })
      .catch(function () {
        return fallback;
      });
  }

  // ── 骨組み ────────────────────────────────────────────────────────────
  function close() {
    var overlay = el("release-review-modal");
    if (overlay) overlay.remove();
  }

  function open(courseId, courseTitle, options) {
    if (!deps || !courseId) return;
    options = options || {};
    state.courseId = courseId;
    state.courseTitle = courseTitle || "";
    state.stepIndex = 0;
    state.autoOpened = !!options.autoOpened;
    state.busy = false;
    state.landscape = null;
    state.landscapeStale = false;
    state.acceptMismatch = false;
    state.mapSaved = false;
    state.published = false;
    // 是正 F7: 開き直しは前回の「開いた」を引き継がない（この確定の来歴だけを申告する）。
    state.evidenceExpanded = {};
    state.acceptRecord = null;
    state.rowDecided = {};

    close();
    var overlay = document.createElement("div");
    overlay.id = "release-review-modal";
    overlay.setAttribute("data-ui-anchor", "release-review.modal");
    overlay.className = "release-review-overlay";
    // 第 23 波・第 2 波 F5: 見た目は styles.css の release-review-* クラスが持つ
    // （inline style をやめ、狭い画面でははみ出さない幅 min(600px, 96vw) にする）。
    overlay.innerHTML =
      '<div class="release-review-dialog">' +
        '<div class="release-review-head">' +
          '<h3 class="release-review-title">リリース前の確認: ' +
            esc(state.courseTitle) +
          "</h3>" +
          '<button id="release-review-close" class="release-review-close" type="button" aria-label="閉じる">&times;</button>' +
        "</div>" +
        '<ol id="release-review-steps" class="release-review-stepbar"></ol>' +
        '<p id="release-review-intro" class="release-review-intro"></p>' +
        '<div id="release-review-notice" class="release-review-notice"></div>' +
        '<div id="release-review-skipped-note" class="release-review-skipped-note" hidden></div>' +
        '<div id="release-review-body" class="release-review-body"></div>' +
        // 第 23 波 F5（RR2）: 「次へ」の意味は主ボタンの直上に 1 行で置く（脚注に埋めない）。
        '<div id="release-review-next-meaning" class="release-review-next-meaning" hidden></div>' +
        '<div class="release-review-foot">' +
          '<div id="release-review-facts" class="release-review-facts"></div>' +
          '<div id="release-review-actions" class="release-review-actions"></div>' +
        "</div>" +
      "</div>";
    document.body.appendChild(overlay);

    el("release-review-close").addEventListener("click", close);
    overlay.addEventListener("click", function (event) {
      // RR1: 閉じても配信は起こる（承認だけが記録されない）。閉じるのを妨げない。
      if (event.target === overlay) close();
    });

    el("release-review-intro").textContent = state.autoOpened
      ? NOTICE_AUTO_OPENED
      : NOTICE_INTRO;
    renderStep();
  }

  // 第 23 波・第 2 波 F5: ステップ表示は「済み・現在・未」が見て分かる 3 段の部品
  // （1 行の文字列連結をやめる）。状態は class と aria-current で持ち、語は付け足さない。
  function renderStepBar() {
    var node = el("release-review-steps");
    if (!node) return;
    var html = "";
    for (var i = 0; i < STEPS.length; i++) {
      var phase = i < state.stepIndex ? "done" : (i === state.stepIndex ? "current" : "todo");
      html +=
        '<li class="release-review-step is-' + phase + '"' +
        (phase === "current" ? ' aria-current="step"' : "") +
        '><span class="release-review-step-num">' + (i + 1) + "</span>" +
        '<span class="release-review-step-label">' +
        esc("ステップ " + (i + 1) + "/" + STEPS.length + " " + STEPS[i].label) +
        "</span></li>";
    }
    node.innerHTML = html;
  }

  // RR2: 「次へ」= 確認したものとして記録する、をボタンの直上に出す（空なら隠す）。
  function setNextMeaning(message) {
    var node = el("release-review-next-meaning");
    if (!node) return;
    node.textContent = message || "";
    node.hidden = !message;
  }

  function renderStep() {
    renderStepBar();
    setNotice("");
    setSkippedNote("");
    setNextMeaning("");
    var body = el("release-review-body");
    var actions = el("release-review-actions");
    var facts = el("release-review-facts");
    if (!body || !actions) return;
    body.innerHTML = "";
    actions.innerHTML = "";
    if (facts) facts.textContent = "";
    var key = STEPS[state.stepIndex].key;
    if (key === "map") renderMapStep(body, actions);
    else if (key === "landscape") renderLandscapeStep(body, actions);
    else renderPublishStep(body, actions);
  }

  function advance() {
    if (state.stepIndex < STEPS.length - 1) {
      state.stepIndex += 1;
      renderStep();
    }
  }

  function addButton(container, label, anchor, handler, isPrimary) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "admin-action-btn";
    btn.textContent = label;
    if (anchor) btn.setAttribute("data-ui-anchor", anchor);
    if (isPrimary) btn.className += " release-review-primary";
    btn.addEventListener("click", handler);
    container.appendChild(btn);
    return btn;
  }

  // ── ステップ1: 学習マップの割り当て ─────────────────────────────────────
  // 既存 atlasBindingRenderEditor に委譲する（分割・保存ペイロードを再実装しない）。
  // 主ボタンのラベルだけ saveLabel で差し替え、onSaved で次へ進む。
  function renderMapStep(body, actions) {
    body.innerHTML =
      '<div id="release-review-map-status" class="release-review-map-status"></div>' +
      '<div id="release-review-map-body"></div>';
    setNextMeaning(NOTICE_NEXT_MEANING);
    // 「あとで」は割り当て画面が読めないときの出口でもあるので、まず下の操作欄に置く。
    // 割り当て画面が描けたら、主ボタン「この対応で次へ」の隣へ移す（主と副を同じ欄に）。
    var laterBtn = addButton(actions, "あとで", "", function () {
      advance();
    });

    if (deps.atlasBindingPropose) {
      deps.atlasBindingPropose(
        state.courseId,
        el("release-review-map-body"),
        el("release-review-map-status"),
        function () {
          // 保存できたら次のステップへ（この保存ボタンが「次へ」を兼ねる）。
          state.mapSaved = true;
          advance();
        },
        {
          courseTitle: state.courseTitle,
          courseDescription: "",
          saveLabel: STEPS[0].next,
          saveAnchor: "release-review.next",
          onRendered: function (saveBtn) {
            if (!saveBtn || !saveBtn.parentNode) return;
            saveBtn.className += " release-review-primary";
            saveBtn.parentNode.insertBefore(laterBtn, saveBtn.nextSibling);
          }
        }
      );
    } else {
      // RR7: 割り当て UI が使えなくても確認と公開は続けられる。
      body.innerHTML =
        '<div class="release-review-empty">学習マップの割り当て画面を読み込めませんでした。「分野の地図」タブから後で割り当てられます。</div>';
    }
  }

  // ── ステップ2: 論文の位置づけ ──────────────────────────────────────────
  function renderLandscapeStep(body, actions) {
    body.innerHTML =
      '<div class="release-review-landscape-intro">' +
      esc(LANDSCAPE_INTRO) +
      "</div>" +
      '<div id="release-review-landscape-body" class="release-review-landscape-body">読み込み中...</div>';
    setNextMeaning(NOTICE_NEXT_MEANING);

    var nextBtn = addButton(
      actions,
      STEPS[1].next,
      "release-review.next",
      function () {
        acceptPlacements(nextBtn);
      },
      true
    );
    nextBtn.disabled = true;
    addButton(actions, "あとで", "", function () {
      advance();
    });

    api("/admin/landscape/courses/" + encodeURIComponent(state.courseId) + "/placements")
      .then(function (res) {
        if (!res.ok) {
          return errorDetail(res, "配置の読み込みに失敗しました。").then(function (msg) {
            throw new Error(msg);
          });
        }
        return res.json();
      })
      .then(function (data) {
        state.landscape = data || {};
        renderLandscapeBody(state.landscape);
        // 未確認が1件も無ければ承認するものが無い（進むだけ）。
        var pending = Number(state.landscape.pending_count || 0);
        nextBtn.disabled = false;
        if (!pending) nextBtn.textContent = "次へ";
      })
      .catch(function (err) {
        // RR7: 読めなくても公開まで進める。
        state.landscape = null;
        var node = el("release-review-landscape-body");
        if (node) {
          node.textContent =
            (err && err.message) || "配置の読み込みに失敗しました。";
        }
        nextBtn.disabled = false;
        nextBtn.textContent = "次へ";
      });
  }

  function statusLabel(placement) {
    var status = (placement && placement.status) || "";
    return (
      (placement && placement.status_label) ||
      STATUS_FALLBACK_LABELS[status] ||
      status
    );
  }

  // 根拠（逐語引用）の折りたたみ。引用が無い行でも「根拠を見る」は出し、無いことを
  // 事実文で書く（無い行だけ静かに欠けると「見た/見ていない」が再構成できない）。
  function evidenceHtml(placement) {
    var items = (placement && placement.evidence) || [];
    var inner = "";
    for (var i = 0; i < items.length; i++) {
      var quote = (items[i] && items[i].quote) || "";
      if (!quote) continue;
      inner +=
        '<div class="release-review-evidence-quote">' +
        "&quot;" +
        esc(quote) +
        "&quot;" +
        // IK-0677: 引用の出所の印。
        (items[i].source_label
          ? '<div class="release-review-evidence-empty">' + esc(items[i].source_label) + "</div>"
          : "") +
        "</div>";
    }
    if (!inner) {
      inner =
        '<div class="release-review-evidence-empty">' +
        esc(EVIDENCE_EMPTY) +
        "</div>";
    }
    return (
      '<details class="release-review-evidence" data-ui-anchor="release-review.evidence"' +
      ' data-placement-id="' +
      esc((placement && placement.id) || "") +
      '">' +
      "<summary>" +
      esc(EVIDENCE_SUMMARY) +
      "</summary>" +
      inner +
      "</details>"
    );
  }

  // ノード版間対応（K-6 / atlas_node_correspondence_design.md §7）のチップ。
  // node_status が無い（旧サーバ・導出不能）ときは描かない fail-soft。
  // current は無表示。件数・一致率は出さない（NC6）。
  var NODE_STATUS_LABELS = {
    migrated: "前の版から対応づけ",
    unmapped: "現行版に対応する場所なし",
  };

  function nodeStatusChipHtml(placement) {
    var label = NODE_STATUS_LABELS[(placement && placement.node_status) || ""];
    if (!label) return "";
    return (
      '<span class="release-review-node-status" data-node-status="' +
      esc((placement && placement.node_status) || "") +
      '">' +
      esc(label) +
      "</span>"
    );
  }

  function placementRowHtml(placement) {
    var meta = [];
    if (placement && placement.perspective_label) meta.push(placement.perspective_label);
    var label =
      (placement && placement.node_label) || (placement && placement.node_id) || "";
    var domain =
      (placement && placement.domain_name) || (placement && placement.domain_key) || "";
    // 第 23 波・第 2 波 F5: 状態は左罫の色（data-status）と状態チップで区別する。
    // 語はサーバの status_label（label_vocab.LANDSCAPE_STATUS_LABELS の逐語）のまま。
    var html =
      '<div class="release-review-placement-row" data-placement-id="' +
      esc((placement && placement.id) || "") +
      '" data-status="' +
      esc((placement && placement.status) || "") +
      '">' +
      '<div class="release-review-placement-top">' +
      '<span class="release-review-placement-label">' +
      esc(label) +
      "</span>" +
      '<span class="release-review-placement-status">' +
      esc(statusLabel(placement)) +
      "</span>" +
      nodeStatusChipHtml(placement) +
      "</div>" +
      '<div class="release-review-placement-meta">' +
      esc(domain + (meta.length ? " ・ " + meta.join(" ・ ") : "")) +
      "</div>";
    if (placement && placement.reason) {
      html +=
        '<div class="release-review-placement-reason">' +
        // IK-0967: 理由は AI の要約（印の文はサーバが解決）。
        (placement.reason_label ? "〔" + esc(placement.reason_label) + "〕" : "") +
        esc(placement.reason) +
        "</div>" +
        (placement.reason_note
          ? '<div class="release-review-placement-reason">' + esc(placement.reason_note) + "</div>"
          : "");
    }
    // 改訂原則1（確定文脈）: 何を見て判断したかを再構成できるように、判断の材料
    // （論文からの逐語引用）を各行に畳んで置く。既定は閉じたまま（RR1: 画面を
    // 重くしない）で、開けば原文がそのまま出る。
    html += evidenceHtml(placement);
    // RR4: 個別の修正手段（却下・再検討）を常に出す。
    if (placement && placement.status !== "rejected") {
      html += '<div class="release-review-row-actions">';
      for (var i = 0; i < ROW_ACTIONS.length; i++) {
        html +=
          '<button class="admin-action-btn release-review-row-btn" type="button" data-placement-id="' +
          esc(placement.id || "") +
          '" data-status="' +
          esc(ROW_ACTIONS[i][0]) +
          '">' +
          esc(ROW_ACTIONS[i][1]) +
          "</button>";
      }
      html += "</div>";
    }
    html += "</div>";
    return html;
  }

  function renderLandscapeBody(data) {
    var node = el("release-review-landscape-body");
    if (!node) return;
    var documents = (data && data.documents) || [];
    var html = "";
    var anyPlacement = false;

    for (var i = 0; i < documents.length; i++) {
      var doc = documents[i] || {};
      var placements = doc.placements || [];
      var unplaced = doc.unplaced_domains || [];
      if (!placements.length && !unplaced.length) continue;
      html +=
        '<div class="release-review-document" data-document-id="' +
        esc(doc.document_id || "") +
        '">' +
        '<div class="release-review-document-title">' +
        esc(doc.title || doc.document_id || "") +
        "</div>";
      for (var j = 0; j < placements.length; j++) {
        anyPlacement = true;
        html += placementRowHtml(placements[j]);
      }
      // LS10: 配置できなかった分野は失敗ではなく信号。事実文で並べる。
      for (var k = 0; k < unplaced.length; k++) {
        var entry = unplaced[k] || {};
        html +=
          '<div class="release-review-unplaced">' +
          esc(
            "この論文は「" +
              (entry.domain_name || entry.domain_key || "") +
              "」の地図に配置できませんでした: " +
              (entry.reason || "")
          ) +
          "</div>";
      }
      html += "</div>";
    }

    if (!anyPlacement && !html) {
      html =
        '<div class="release-review-empty">' +
        esc(LANDSCAPE_EMPTY) +
        "</div>";
    }
    node.innerHTML = html;

    var facts = el("release-review-facts");
    if (facts) {
      // 「次へ」の意味はボタン直上（setNextMeaning）に置いたので、脚注は再審経路と件数・骨格版だけ。
      var parts = [NOTICE_REOPEN];
      var pending = Number((data && data.pending_count) || 0);
      if (pending) parts.push("未確認 " + pending + "件");
      var hidden = Number((data && data.hidden_document_count) || 0);
      if (hidden) parts.push("権限のない論文 " + hidden + "件は対象外");
      var domains = (data && data.domains) || [];
      if (domains.length) {
        var versions = [];
        for (var d = 0; d < domains.length; d++) {
          var dom = domains[d] || {};
          if (dom.frozen_version) {
            // 版の呼び名は core/label_vocab.py::ATLAS_VERSION_PREFIX の逐語ミラー（IK-0942）
            versions.push((dom.domain_name || dom.domain_key) + " 地図 版 " + dom.frozen_version);
          }
        }
        if (versions.length) parts.push("使用した地図: " + versions.join(" ・ "));
      }
      facts.textContent = parts.join(" ・ ");
    }

    // 是正 F7: 根拠を開いた事実だけを DOM の toggle イベントから拾う（開いた行の
    // id を記録するだけ。閉じた操作・回数・滞在時間は記録しない）。
    var evidenceDetails = node.querySelectorAll("details.release-review-evidence");
    for (var e = 0; e < evidenceDetails.length; e++) {
      evidenceDetails[e].addEventListener("toggle", function () {
        if (!this.open) return;
        var placementId = this.getAttribute("data-placement-id");
        if (placementId) state.evidenceExpanded[placementId] = true;
      });
    }

    var rowButtons = node.querySelectorAll(".release-review-row-btn");
    for (var b = 0; b < rowButtons.length; b++) {
      rowButtons[b].addEventListener("click", function () {
        updateRowStatus(
          this.getAttribute("data-placement-id"),
          this.getAttribute("data-status"),
          this
        );
      });
    }
  }

  function updateRowStatus(placementId, status, btn) {
    if (!placementId || !status || state.busy) return;
    state.busy = true;
    btn.disabled = true;
    setNotice("");
    api("/admin/landscape/placements/" + encodeURIComponent(placementId), {
      method: "PATCH",
      body: JSON.stringify({ status: status })
    })
      .then(function (res) {
        if (!res.ok) {
          return errorDetail(res, "この配置の状態を変更できませんでした。").then(
            function (msg) {
              throw new Error(msg);
            }
          );
        }
        return res.json();
      })
      .then(function () {
        state.busy = false;
        state.rowDecided[placementId] = true;
        // サーバ状態を正本として読み直す（ローカルで書き換えない）。
        reloadLandscape();
      })
      .catch(function (err) {
        state.busy = false;
        btn.disabled = false;
        setNotice((err && err.message) || "この配置の状態を変更できませんでした。", true);
      });
  }

  // 配置をサーバから取り直す（失敗は null。ローカルで状態を書き換えない）。
  function fetchLandscape() {
    return api("/admin/landscape/courses/" + encodeURIComponent(state.courseId) + "/placements")
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .catch(function () {
        return null;
      });
  }

  function reloadLandscape() {
    fetchLandscape().then(function (data) {
      if (!data) return;
      state.landscape = data;
      renderLandscapeBody(data);
    });
  }

  // 是正 F7: この確定までに根拠の折りたたみを開いた行の id（DOM の toggle イベント
  // 由来の事実。開いていなければ空配列で、それがそのまま監査に残る）。
  function expandedEvidencePlacementIds() {
    var ids = [];
    var map = state.evidenceExpanded || {};
    for (var key in map) {
      if (Object.prototype.hasOwnProperty.call(map, key) && map[key]) ids.push(key);
    }
    return ids;
  }

  // いま画面に「未確認（AI推定）」として描かれている配置の id（来歴申告用）。
  function pendingPlacementIds() {
    var ids = [];
    var documents = (state.landscape && state.landscape.documents) || [];
    for (var i = 0; i < documents.length; i++) {
      var placements = (documents[i] && documents[i].placements) || [];
      for (var j = 0; j < placements.length; j++) {
        var p = placements[j] || {};
        if (p.status === "inferred" && p.id) ids.push(p.id);
      }
    }
    return ids;
  }

  function acceptPlacements(btn) {
    if (state.busy) return;
    var pending = Number((state.landscape && state.landscape.pending_count) || 0);
    if (!pending) {
      advance();
      return;
    }
    state.busy = true;
    if (btn) btn.disabled = true;
    setNotice("");
    api(
      "/admin/landscape/courses/" +
        encodeURIComponent(state.courseId) +
        "/placements/accept",
      {
        method: "POST",
        // 来歴申告（サーバ側の提示集合の正本はサーバが取り直す）。是正 F7:
        // 「根拠を描いている」という固定の申告はやめ、
        // 実際に折りたたみを開いた行の id だけを送る（1件も開いていなければ空配列
        // ＝「開かずに確定した」が正直に残る）。確定は止めない（RR7）。
        body: JSON.stringify({
          presented_placement_ids: pendingPlacementIds(),
          evidence_expanded_placement_ids: expandedEvidencePlacementIds()
        })
      }
    )
      .then(function (res) {
        if (!res.ok) {
          return errorDetail(res, "確認を記録できませんでした。").then(function (msg) {
            throw new Error(msg);
          });
        }
        return res.json();
      })
      .then(function (data) {
        // 第 23 波 F5: 書き込んだ後は配置を取り直してからステップ 3 を描く（確認前の
        // pending_count で「未確認 N件」と要約しない）。取り直せなければ件数を言わない。
        return fetchLandscape().then(function (fresh) {
          state.busy = false;
          if (fresh) {
            state.landscape = fresh;
            state.landscapeStale = false;
          } else {
            state.landscapeStale = true;
          }
          var ctx = data && data.decision_context;
          state.acceptMismatch = !!(ctx && !ctx.presented_matches_applied);
          state.acceptRecord = acceptRecordFrom(ctx);
          // RR7: 記録の結果に関わらず先へ進める。事実文は次ステップの描画
          // （renderStep が notice を空にする）の後に出す。
          advance();
          // 提示と確認の一致を事実文で残す（DC2: 一致を偽らない）。
          // 第 23 周（第 3 波）: 不一致のときはサーバの decision_note（何件のうち何件・
          // 対象外の理由）をそのまま描く。無い（旧サーバ）ときだけ固定文に縮退する。
          if (ctx) {
            var mismatchNote =
              data && typeof data.decision_note === "string" && data.decision_note
                ? data.decision_note
                : NOTICE_ACCEPT_MISMATCH;
            setNotice(
              ctx.presented_matches_applied ? NOTICE_ACCEPT_MATCHED : mismatchNote,
              !ctx.presented_matches_applied
            );
          }
          // IK-0376: 対象外にした論文があったときだけ、その事実を件数の隣に1行で残す。
          setSkippedNote(
            data && typeof data.skipped_note === "string" ? data.skipped_note : ""
          );
        });
      })
      .catch(function (err) {
        // RR7: 記録に失敗しても公開は止めない（未確認のまま配信される）。
        state.busy = false;
        if (btn) btn.disabled = false;
        setNotice(
          ((err && err.message) || "確認を記録できませんでした。") +
            " このまま公開すると、受講者には「AIによる推定（未確認）」として表示されます。",
          true
        );
      });
  }

  // accept 応答の decision_context から「確認として記録したもの」の件数を作る
  // （applied = 確認済みにした配置 / client_reported = 根拠を開いた行の申告）。
  // ctx が無ければ null（件数を推測しない）。
  function acceptRecordFrom(ctx) {
    if (!ctx) return null;
    var applied = Number((ctx.applied && ctx.applied.count) || 0);
    var reported = ctx.client_reported || {};
    var opened = reported.evidence_expanded_placement_ids;
    var openedCount = Object.prototype.toString.call(opened) === "[object Array]"
      ? opened.length
      : expandedEvidencePlacementIds().length;
    return { confirmed: applied, evidenceOpened: openedCount };
  }

  function rowDecidedCount() {
    var n = 0;
    for (var key in state.rowDecided) {
      if (Object.prototype.hasOwnProperty.call(state.rowDecided, key)) n += 1;
    }
    return n;
  }

  // ステップ 3 の先頭: このウィザードで確認として記録したものの事実文（件数のみ — RR6）。
  // 記録が何も無ければ区画ごと出さない（P-0008）。
  function recordedSummaryHtml() {
    var decided = rowDecidedCount();
    var rec = state.acceptRecord;
    if (!rec && !decided) return "";
    var lines = [];
    if (rec) {
      lines.push(RECORDED_CONFIRMED_PREFIX + rec.confirmed + "件");
      lines.push(RECORDED_EVIDENCE_PREFIX + rec.evidenceOpened + "件");
    }
    lines.push(RECORDED_DECIDED_PREFIX + decided + "件");
    var html = '<div class="release-review-recorded">';
    html += '<div class="release-review-recorded-hd">' + esc(RECORDED_HEADING) + "</div>";
    html += '<ul class="release-review-recorded-list">';
    for (var i = 0; i < lines.length; i++) html += "<li>" + esc(lines[i]) + "</li>";
    html += "</ul></div>";
    return html;
  }

  // ── ステップ3: 公開 ───────────────────────────────────────────────────
  function renderPublishStep(body, actions) {
    var lines = [];
    lines.push(
      state.mapSaved
        ? "学習マップの割り当てを保存しました。"
        : "学習マップの割り当ては変更していません。"
    );
    if (state.landscapeStale) {
      lines.push(NOTICE_LANDSCAPE_STALE);
    } else if (state.landscape) {
      var count = Number(state.landscape.placement_count || 0);
      var pending = Number(state.landscape.pending_count || 0);
      if (count) {
        lines.push(
          "分野マップ上の位置づけ " +
            count +
            "件を受講者に表示します" +
            (pending ? "（うち未確認 " + pending + "件は「AIによる推定（未確認）」と表示されます）" : "") +
            "。"
        );
      } else {
        lines.push("分野マップ上の位置づけはまだありません。");
      }
    }
    lines.push(
      "公開すると受講者が受講できるようになります。公開後も内容の修正はいつでもできます。"
    );

    var html = recordedSummaryHtml();
    html += '<ul class="release-review-publish-list">';
    for (var i = 0; i < lines.length; i++) {
      html += "<li>" + esc(lines[i]) + "</li>";
    }
    html += "</ul>";
    if (state.published) {
      html +=
        '<div id="release-review-published" class="release-review-published">公開しました。</div>';
    }
    body.innerHTML = html;

    // 第 23 波 F5: 提示と確認に差があったときは、ウィザードの中でステップ 2 に戻って
    // 最新の配置を取り直せる（再読み込みでウィザードを消さない）。
    if (state.acceptMismatch && !state.published) {
      addButton(actions, BACK_TO_LANDSCAPE_LABEL, "", function () {
        state.acceptMismatch = false;
        state.stepIndex = 1;
        renderStep();
      });
    }
    if (!state.published) {
      var publishBtn = addButton(
        actions,
        "公開する",
        "release-review.publish",
        function () {
          publish(publishBtn);
        },
        true
      );
    }
    addButton(actions, state.published ? "閉じる" : "公開しないで閉じる", "", close);
  }

  function publish(btn) {
    if (state.busy) return;
    state.busy = true;
    if (btn) btn.disabled = true;
    setNotice("");
    api("/admin/courses/" + encodeURIComponent(state.courseId) + "/visibility", {
      method: "PUT",
      body: JSON.stringify({ visibility: "public" })
    })
      .then(function (res) {
        if (!res.ok) {
          return errorDetail(res, "公開できませんでした。").then(function (msg) {
            throw new Error(msg);
          });
        }
        return res.json();
      })
      .then(function (data) {
        state.busy = false;
        state.published = true;
        if (deps.refreshNextSteps) deps.refreshNextSteps();
        if (deps.onPublished) deps.onPublished(state.courseId);
        renderStep();
        // IK-0947: 本文の無いトピックが残るまま公開したならサーバの事実文を出す。
        if (data && data.readiness_note) setNotice(data.readiness_note);
      })
      .catch(function (err) {
        state.busy = false;
        if (btn) btn.disabled = false;
        setNotice((err && err.message) || "公開できませんでした。", true);
      });
  }

  window.AdminReleaseReview = {
    init: function (injected) {
      deps = injected || null;
    },
    open: open,
    close: close
  };
})();
