// 再構成ループ (Reconstruction Loop, R層) — 学習画面カード UI。
//
// トピック学習ビューに「再構成に挑戦」導線を出す（自動では開かない。P7: 演技化させない）。
// フロー: ELICIT（出題）→ CAPTURE（提出）→ DIFF/REVEAL（開示）→ SELF-CHECK（必須）
//         →（REVISE 再挑戦 / DESCEND 記号葉へ降下）。
// 判定は「食い違いの可能性」という仮説文体で表示し、権威は出典のリビールに置く。
// 数値スコア・正答率は出さない（サーバ側でも出さない）。
(function () {
  "use strict";

  var API = "/api";
  var ctx = { courseId: "", topicId: "" };
  var el = null; // #reconstruction-region
  var current = { item: null, reconId: "", isRevision: false };

  // 選択式モード（predict = 型の予測 / regime・next_step = 式スケール ELICIT）。
  // restate だけが自由記述。判定方式（option_id 照合）が同じ3モードをまとめて扱う。
  var CHOICE_MODES = { predict: 1, regime: 1, next_step: 1 };
  function isChoiceMode(mode) { return !!CHOICE_MODES[mode]; }

  function token() {
    return localStorage.getItem("eg_token") || null;
  }

  async function apiFetch(path, opts) {
    opts = opts || {};
    var headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    var t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    var res = await fetch(API + path, Object.assign({}, opts, { headers: headers }));
    return res;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function clear() {
    if (el) el.innerHTML = "";
  }

  function hideRegion() {
    if (el) { el.hidden = true; el.innerHTML = ""; }
  }

  function renderEntry() {
    // 折りたたみ状態: 「再構成に挑戦」ボタンのみ。
    if (!el) return;
    el.hidden = false;
    el.innerHTML =
      '<button class="recon-open" id="recon-open-btn" title="学んだ内容を自分で再構成してみる">' +
      '🧩 再構成に挑戦</button>';
    var btn = document.getElementById("recon-open-btn");
    if (btn) btn.addEventListener("click", openNext);
  }

  function cardShell(bodyHtml) {
    return '' +
      '<div class="recon-card">' +
        '<div class="recon-head">' +
          '<span class="recon-title">🧩 再構成に挑戦</span>' +
          '<button class="recon-close" id="recon-close-btn" title="閉じる">&times;</button>' +
        '</div>' +
        '<div class="recon-auto-note">この問いは AI が自動生成したものです。</div>' +
        '<div class="recon-body" id="recon-body">' + bodyHtml + '</div>' +
      '</div>';
  }

  function bindClose() {
    var c = document.getElementById("recon-close-btn");
    if (c) c.addEventListener("click", renderEntry);
  }

  async function openNext() {
    if (!ctx.courseId || !ctx.topicId) { renderEntry(); return; }
    el.hidden = false;
    el.innerHTML = cardShell('<div class="recon-muted">問いを読み込み中…</div>');
    bindClose();
    try {
      var res = await apiFetch(
        "/learning/courses/" + encodeURIComponent(ctx.courseId) +
        "/topics/" + encodeURIComponent(ctx.topicId) + "/reconstruction/next"
      );
      if (!res.ok) throw new Error("next failed");
      var data = await res.json();
      if (!data.item) {
        el.innerHTML = cardShell(
          '<div class="recon-muted">この範囲で挑戦できる再構成課題はまだありません。' +
          '教員が承認した主張が揃うと自動で用意されます。学習を進めると増えていきます。</div>'
        );
        bindClose();
        return;
      }
      current = { item: data.item, reconId: "", isRevision: false };
      renderElicit();
    } catch (e) {
      el.innerHTML = cardShell('<div class="recon-muted">読み込みに失敗しました。時間をおいて再度お試しください。</div>');
      bindClose();
    }
  }

  function renderElicit() {
    var item = current.item;
    var html = '<p class="recon-prompt">' + esc(item.prompt) + '</p>';

    var ctxFields = (item.claim_context && item.claim_context.concepts) || [];
    if (ctxFields.length) {
      html += '<div class="recon-chips">';
      ctxFields.forEach(function (name) {
        html += '<span class="recon-chip">' + esc(name) + '</span>';
      });
      html += '</div>';
    }

    if (isChoiceMode(item.elicit_mode) && (item.response_space || []).length) {
      html += '<div class="recon-options">';
      item.response_space.forEach(function (opt, i) {
        html += '<label class="recon-option">' +
          '<input type="radio" name="recon-choice" value="' + esc(opt.id) + '"' + (i === 0 ? "" : "") + '>' +
          '<span>' + esc(opt.label) + '</span></label>';
      });
      html += '</div>';
    } else {
      html += '<textarea class="recon-restate" id="recon-restate" ' +
        'placeholder="この結果を、自分の言葉で一文で述べてみてください。"></textarea>';
    }
    html += '<div class="recon-actions">' +
      '<button class="recon-primary" id="recon-submit-btn">提出する</button></div>';

    document.getElementById("recon-body").innerHTML = html;
    var sb = document.getElementById("recon-submit-btn");
    if (sb) sb.addEventListener("click", submit);
  }

  function collectResponse() {
    var item = current.item;
    if (isChoiceMode(item.elicit_mode)) {
      var checked = el.querySelector('input[name="recon-choice"]:checked');
      return checked ? { option_id: checked.value } : null;
    }
    var ta = document.getElementById("recon-restate");
    var text = ta ? ta.value.trim() : "";
    return text ? { text: text } : null;
  }

  async function submit() {
    var item = current.item;
    var response = collectResponse();
    if (!response) {
      var warn = document.getElementById("recon-warn");
      if (!warn) {
        warn = document.createElement("div");
        warn.className = "recon-muted recon-warn";
        warn.id = "recon-warn";
        warn.textContent = isChoiceMode(item.elicit_mode)
          ? "選択肢を一つ選んでください。" : "一文を入力してください。";
        var actions = el.querySelector(".recon-actions");
        if (actions) actions.parentNode.insertBefore(warn, actions);
      }
      return;
    }
    var body = { course_id: ctx.courseId, response: response };
    var path;
    if (current.isRevision && current.reconId) {
      body.revision_of = current.reconId;
      path = "/learning/reconstruction/" + encodeURIComponent(item.item_id) + "/revise";
    } else {
      path = "/learning/reconstruction/" + encodeURIComponent(item.item_id) + "/submit";
    }
    var sb = document.getElementById("recon-submit-btn");
    if (sb) { sb.disabled = true; sb.textContent = "照合中…"; }
    try {
      var res = await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error("submit failed");
      var data = await res.json();
      current.reconId = data.recon_id || "";
      renderReveal(data.reflection || {});
    } catch (e) {
      if (sb) { sb.disabled = false; sb.textContent = "提出する"; }
    }
  }

  function renderReveal(reflection) {
    var reveal = reflection.reveal || {};
    var statements = reflection.statements || [];
    var html = '<div class="recon-reflect">';
    statements.forEach(function (s) {
      html += '<p class="recon-statement">' + esc(s) + '</p>';
    });
    html += '</div>';

    html += '<div class="recon-reveal">';
    html += '<div class="recon-reveal-label">出典</div>';
    if (reveal.text) html += '<div class="recon-reveal-text">' + esc(reveal.text) + '</div>';
    if (reveal.equation_latex) {
      html += '<div class="recon-reveal-eq" id="recon-reveal-eq" data-latex="' +
        esc(reveal.equation_latex) + '">' + esc(reveal.equation_latex) + '</div>';
    }
    if (reveal.evidence_text) {
      html += '<div class="recon-reveal-evidence">原文根拠: ' + esc(reveal.evidence_text) + '</div>';
    }
    html += '</div>';

    // SELF-CHECK（必須・1タップ）
    html += '<div class="recon-selfcheck" id="recon-selfcheck">' +
      '<div class="recon-selfcheck-q">あなたの見立てはどうでしたか？</div>' +
      '<div class="recon-selfcheck-btns">' +
        '<button class="recon-sc" data-sc="agreed">合っていた</button>' +
        '<button class="recon-sc" data-sc="disagreed">違っていた</button>' +
        '<button class="recon-sc" data-sc="verdict_wrong">判定がおかしい</button>' +
      '</div></div>';
    html += '<div class="recon-followup" id="recon-followup" hidden></div>';

    document.getElementById("recon-body").innerHTML = html;
    renderEquation();

    el.querySelectorAll(".recon-sc").forEach(function (b) {
      b.addEventListener("click", function () { selfCheck(this.getAttribute("data-sc"), this); });
    });
  }

  function renderEquation() {
    var eq = document.getElementById("recon-reveal-eq");
    if (!eq) return;
    var latex = eq.getAttribute("data-latex") || "";
    if (window.katex) {
      try {
        eq.innerHTML = window.katex.renderToString(latex, { throwOnError: false });
      } catch (e) { /* 生テキストのまま */ }
    }
  }

  async function selfCheck(result, btn) {
    if (!current.reconId) return;
    el.querySelectorAll(".recon-sc").forEach(function (b) {
      b.classList.remove("active");
      b.disabled = true;
    });
    if (btn) btn.classList.add("active");
    try {
      await apiFetch("/learning/reconstruction/" + encodeURIComponent(current.reconId) + "/self-check",
        { method: "POST", body: JSON.stringify({ result: result }) });
    } catch (e) { /* best-effort。UI は続行 */ }
    renderFollowup();
  }

  function renderFollowup() {
    var fu = document.getElementById("recon-followup");
    if (!fu) return;
    fu.hidden = false;
    fu.innerHTML =
      '<div class="recon-actions">' +
        '<button class="recon-secondary" id="recon-revise-btn">もう一度挑戦</button>' +
        '<button class="recon-secondary" id="recon-descend-btn">記号を確認する</button>' +
        '<button class="recon-primary" id="recon-next-btn">次の問いへ</button>' +
      '</div>';
    var rb = document.getElementById("recon-revise-btn");
    var db = document.getElementById("recon-descend-btn");
    var nb = document.getElementById("recon-next-btn");
    if (rb) rb.addEventListener("click", function () { current.isRevision = true; renderElicit(); });
    if (db) db.addEventListener("click", descend);
    if (nb) nb.addEventListener("click", openNext);
  }

  async function descend() {
    if (!current.reconId) return;
    try {
      var res = await apiFetch("/learning/reconstruction/" + encodeURIComponent(current.reconId) + "/descend",
        { method: "POST", body: JSON.stringify({}) });
      if (!res.ok) throw new Error("descend failed");
      var data = await res.json();
      var probe = data.probe || {};
      var fu = document.getElementById("recon-followup");
      if (!fu) return;
      fu.innerHTML =
        '<div class="recon-descend">' +
          '<div class="recon-descend-label">点検口: 記号を確認</div>' +
          '<p class="recon-prompt">' + esc(probe.prompt || "") + '</p>' +
          '<textarea class="recon-restate" id="recon-descend-text" ' +
            'placeholder="自分の言葉で確認してみましょう（機械判定はありません。記録は残ります）。"></textarea>' +
          '<div class="recon-actions">' +
            '<button class="recon-primary" id="recon-next-btn2">主張に戻って次の問いへ</button>' +
          '</div>' +
        '</div>';
      var nb = document.getElementById("recon-next-btn2");
      if (nb) nb.addEventListener("click", function () { submitDescend(probe); });
    } catch (e) { /* no-op */ }
  }

  async function submitDescend(probe) {
    // 記述があれば記号葉の産出物として同じ器（learner_reconstructions）に保存する。
    // 空ならそのまま次へ（保存は強制しない）。
    var ta = document.getElementById("recon-descend-text");
    var text = ta ? ta.value.trim() : "";
    if (text && probe && probe.item_id) {
      try {
        await apiFetch("/learning/reconstruction/" + encodeURIComponent(probe.item_id) + "/submit",
          { method: "POST", body: JSON.stringify({ course_id: ctx.courseId, response: { text: text } }) });
      } catch (e) { /* best-effort。UI は続行 */ }
    }
    openNext();
  }

  window.Reconstruction = {
    setContext: function (courseId, topicId) {
      var changed = ctx.courseId !== courseId || ctx.topicId !== topicId;
      ctx.courseId = courseId || "";
      ctx.topicId = topicId || "";
      el = document.getElementById("reconstruction-region");
      if (!el) return;
      if (!ctx.courseId || !ctx.topicId) { hideRegion(); return; }
      // トピック切替で折りたたみ状態に戻す（自動では開かない）。
      if (changed) renderEntry();
    },
    refresh: function () { if (el && !el.hidden) renderEntry(); },
  };
})();
