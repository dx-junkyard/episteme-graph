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
  var LANDSCAPE_EMPTY =
    "この論文の分野マップ上の位置づけはまだありません。このまま公開できます。";
  var LANDSCAPE_INTRO =
    "この論文が分野マップ（基準地図）のどこに位置づくかの候補です。1つの論文が複数の領域・複数の観点に置かれることがあります。";
  var STATUS_FALLBACK_LABELS = {
    confirmed: "教員確認済み",
    inferred: "AIによる推定（未確認）",
    review_required: "確認待ち（AI推定）",
    rejected: "却下",
    superseded: "旧候補"
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
    mapSaved: false,
    published: false
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
    node.style.color = isError
      ? "var(--color-text-danger, #e53935)"
      : "var(--color-text-secondary)";
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
    state.mapSaved = false;
    state.published = false;

    close();
    var overlay = document.createElement("div");
    overlay.id = "release-review-modal";
    overlay.setAttribute("data-ui-anchor", "release-review.modal");
    overlay.style.cssText =
      "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div style="background:var(--color-background-primary);border:1px solid var(--color-border);border-radius:8px;padding:22px;min-width:600px;max-width:760px;max-height:86vh;display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">リリース前の確認: ' +
            esc(state.courseTitle) +
          "</h3>" +
          '<button id="release-review-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        "</div>" +
        '<div id="release-review-steps" style="font-size:12px;color:var(--color-text-tertiary);margin-bottom:6px"></div>' +
        '<p id="release-review-intro" style="font-size:12px;color:var(--color-text-secondary);margin:0 0 8px"></p>' +
        '<div id="release-review-notice" style="font-size:12px;color:var(--color-text-secondary);margin:0 0 8px"></div>' +
        '<div id="release-review-body" style="overflow-y:auto;flex:1;min-height:180px"></div>' +
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:12px;padding-top:10px;border-top:1px solid var(--color-border-tertiary)">' +
          '<div id="release-review-facts" style="font-size:11px;color:var(--color-text-tertiary)"></div>' +
          '<div id="release-review-actions" style="display:flex;gap:8px;flex-wrap:wrap"></div>' +
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

  function renderStepBar() {
    var node = el("release-review-steps");
    if (!node) return;
    var parts = [];
    for (var i = 0; i < STEPS.length; i++) {
      var marker = i === state.stepIndex ? "▶ " : "";
      parts.push(marker + "ステップ " + (i + 1) + "/" + STEPS.length + " " + STEPS[i].label);
    }
    node.textContent = parts.join("　／　");
  }

  function renderStep() {
    renderStepBar();
    setNotice("");
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
    if (isPrimary) {
      btn.style.cssText =
        "background:var(--color-text-info);color:#fff;border-color:var(--color-text-info)";
    }
    btn.addEventListener("click", handler);
    container.appendChild(btn);
    return btn;
  }

  // ── ステップ1: 学習マップの割り当て ─────────────────────────────────────
  // 既存 atlasBindingRenderEditor に委譲する（分割・保存ペイロードを再実装しない）。
  // 主ボタンのラベルだけ saveLabel で差し替え、onSaved で次へ進む。
  function renderMapStep(body, actions) {
    body.innerHTML =
      '<div id="release-review-map-status" style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px"></div>' +
      '<div id="release-review-map-body"></div>';
    var facts = el("release-review-facts");
    if (facts) facts.textContent = NOTICE_NEXT_MEANING;

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
          saveAnchor: "release-review.next"
        }
      );
    } else {
      // RR7: 割り当て UI が使えなくても確認と公開は続けられる。
      body.innerHTML =
        '<div style="font-size:13px;color:var(--color-text-tertiary)">学習マップの割り当て画面を読み込めませんでした。「分野の地図」タブから後で割り当てられます。</div>';
    }

    addButton(actions, "あとで", "", function () {
      advance();
    });
  }

  // ── ステップ2: 論文の位置づけ ──────────────────────────────────────────
  function renderLandscapeStep(body, actions) {
    body.innerHTML =
      '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:8px">' +
      esc(LANDSCAPE_INTRO) +
      "</div>" +
      '<div id="release-review-landscape-body" style="font-size:13px;color:var(--color-text-tertiary)">読み込み中...</div>';
    var facts = el("release-review-facts");
    if (facts) facts.textContent = NOTICE_NEXT_MEANING;

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

  function placementRowHtml(placement) {
    var meta = [];
    if (placement && placement.perspective_label) meta.push(placement.perspective_label);
    var label =
      (placement && placement.node_label) || (placement && placement.node_id) || "";
    var domain =
      (placement && placement.domain_name) || (placement && placement.domain_key) || "";
    var html =
      '<div class="release-review-placement-row" data-placement-id="' +
      esc((placement && placement.id) || "") +
      '" style="padding:7px 0;border-bottom:1px solid var(--color-border-tertiary)">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
      '<span style="font-size:13px;color:var(--color-text-primary)">' +
      esc(label) +
      "</span>" +
      '<span style="font-size:11px;color:var(--color-text-tertiary)">' +
      esc(domain + (meta.length ? " ・ " + meta.join(" ・ ") : "")) +
      "</span>" +
      '<span style="font-size:11px;color:var(--color-text-secondary)">' +
      esc(statusLabel(placement)) +
      "</span>" +
      "</div>";
    if (placement && placement.reason) {
      html +=
        '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:3px">' +
        esc(placement.reason) +
        "</div>";
    }
    // RR4: 個別の修正手段（却下・再検討）を常に出す。
    if (placement && placement.status !== "rejected") {
      html += '<div style="margin-top:4px;display:flex;gap:6px">';
      for (var i = 0; i < ROW_ACTIONS.length; i++) {
        html +=
          '<button class="admin-action-btn release-review-row-btn" type="button" data-placement-id="' +
          esc(placement.id || "") +
          '" data-status="' +
          esc(ROW_ACTIONS[i][0]) +
          '" style="font-size:11px;padding:1px 7px">' +
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
        '" style="margin-bottom:12px">' +
        '<div style="font-size:12.5px;font-weight:600;color:var(--color-text-primary)">' +
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
          '<div class="release-review-unplaced" style="font-size:11.5px;color:var(--color-text-tertiary);padding:3px 0">' +
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
        '<div style="font-size:13px;color:var(--color-text-tertiary)">' +
        esc(LANDSCAPE_EMPTY) +
        "</div>";
    }
    node.innerHTML = html;

    var facts = el("release-review-facts");
    if (facts) {
      var parts = [NOTICE_NEXT_MEANING];
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
            versions.push((dom.domain_name || dom.domain_key) + " 骨格版 " + dom.frozen_version);
          }
        }
        if (versions.length) parts.push("使用した骨格: " + versions.join(" ・ "));
      }
      facts.textContent = parts.join(" ・ ");
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
        // サーバ状態を正本として読み直す（ローカルで書き換えない）。
        reloadLandscape();
      })
      .catch(function (err) {
        state.busy = false;
        btn.disabled = false;
        setNotice((err && err.message) || "この配置の状態を変更できませんでした。", true);
      });
  }

  function reloadLandscape() {
    api("/admin/landscape/courses/" + encodeURIComponent(state.courseId) + "/placements")
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .then(function (data) {
        if (!data) return;
        state.landscape = data;
        renderLandscapeBody(data);
      })
      .catch(function () {});
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
      { method: "POST" }
    )
      .then(function (res) {
        if (!res.ok) {
          return errorDetail(res, "確認を記録できませんでした。").then(function (msg) {
            throw new Error(msg);
          });
        }
        return res.json();
      })
      .then(function () {
        state.busy = false;
        advance();
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

  // ── ステップ3: 公開 ───────────────────────────────────────────────────
  function renderPublishStep(body, actions) {
    var lines = [];
    lines.push(
      state.mapSaved
        ? "学習マップの割り当てを保存しました。"
        : "学習マップの割り当ては変更していません。"
    );
    if (state.landscape) {
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

    var html = '<ul style="margin:0;padding-left:18px;font-size:13px;color:var(--color-text-secondary)">';
    for (var i = 0; i < lines.length; i++) {
      html += "<li style='margin-bottom:4px'>" + esc(lines[i]) + "</li>";
    }
    html += "</ul>";
    if (state.published) {
      html +=
        '<div id="release-review-published" style="margin-top:10px;font-size:13px;color:var(--color-text-success)">公開しました。</div>';
    }
    body.innerHTML = html;

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
      .then(function () {
        state.busy = false;
        state.published = true;
        if (deps.refreshNextSteps) deps.refreshNextSteps();
        if (deps.onPublished) deps.onPublished(state.courseId);
        renderStep();
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
