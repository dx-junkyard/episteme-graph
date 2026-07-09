/* 分野の地図 — 骨格 draft の AIアシスト編集パネル (skeleton editor upgrade P3/P4/P5)
 *
 * 骨格レビュー画面の「AI agent」ボタンで開くチャットパネル。全体再生成とは別に、
 * draft の一部分を対話で修正する。設計 (field_atlas_skeleton_editor_upgrade.md §4):
 *
 * - 意図解釈 (interpret) を修正提案 (propose) の前に必ず挟む。教員が「何を・どう
 *   変えたいか」を確認・確定するまで編集案を生成しない (§4.2 → §4.3)。
 * - AIは draft を直接確定させない。propose は JSON Patch 案を返すだけで、教員が
 *   「適用」を押して初めて PUT (saveDraft) が走る (§4.3)。
 * - 会話履歴はブラウザ内メモリのみ (MVP。リロードで消える。§4.1 / §7-4)。
 * - draft が保存・再生成・凍結されると古い revision の提案を無効化する (§4.1)。
 *
 * 公開 API:
 *   window.AtlasAssistPanel.open(config)
 *     config = { panelEl, cartridgeId, apiFetch, basePath, getRevision, getSkeleton,
 *                getSelectionHint, onApply, onHighlight }
 *   window.AtlasAssistPanel.reset()
 *   window.AtlasAssistPanel.setSelectionHint(detail)   // P5: プレビューのノードクリック
 *   window.AtlasAssistPanel.notifyDraftChanged(revision)
 */
(function () {
  "use strict";

  var S = {
    config: null,
    history: [],            // API 用 [{role, content, resolved_target?}]
    selectionHint: null,
    pendingInterpretation: null,
    boundRevision: null,
    busy: false,
    applying: false,        // 自分の「適用」による保存中 (notifyDraftChanged を静かに通す)
  };

  function elem(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k) && attrs[k] != null) {
          if (k === "style") node.style.cssText = attrs[k];
          else node.setAttribute(k, attrs[k]);
        }
      }
    }
    if (text != null) node.textContent = text;
    return node;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---- DOM 参照 ----
  function panel() { return S.config && S.config.panelEl; }
  function q(sel) { var p = panel(); return p ? p.querySelector(sel) : null; }

  function buildShell() {
    var p = panel();
    p.innerHTML = "";
    var head = elem("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:6px" });
    head.appendChild(elem("b", { style: "font-size:13px" }, "AI agent（骨格の部分編集）"));
    head.appendChild(elem("span", { style: "flex:1" }));
    var resetBtn = elem("button", { class: "admin-action-btn", "data-role": "assist-reset", type: "button", style: "font-size:11.5px" }, "会話をリセット");
    head.appendChild(resetBtn);
    p.appendChild(head);

    p.appendChild(elem("p", { style: "font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 6px" },
      "修正したい対象と要望を書いてください。AIがまず意図を言い直して確認します（AIが勝手に確定しません）。"));

    p.appendChild(elem("div", { "data-role": "assist-hint", style: "font-size:11.5px;margin-bottom:6px" }));

    var log = elem("div", {
      "data-role": "assist-log",
      style: "max-height:280px;overflow-y:auto;border:1px solid var(--color-border);border-radius:6px;padding:8px;background:var(--color-bg-primary,#fff);margin-bottom:6px",
    });
    p.appendChild(log);

    var inputRow = elem("div", { style: "display:flex;gap:6px;align-items:flex-end" });
    var ta = elem("textarea", {
      "data-role": "assist-input", rows: "2", placeholder: "例: この領域の概念ラベルを教科書的な用語に揃えて",
      style: "flex:1;font-size:12.5px;border:1px solid var(--color-border);border-radius:4px;padding:6px;box-sizing:border-box;resize:vertical",
    });
    inputRow.appendChild(ta);
    var sendBtn = elem("button", { class: "admin-action-btn", "data-role": "assist-send", type: "button" }, "送信");
    inputRow.appendChild(sendBtn);
    p.appendChild(inputRow);

    resetBtn.addEventListener("click", function () { doReset(true); });
    sendBtn.addEventListener("click", function () { onSend(); });
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onSend(); }
    });
    renderHint();
  }

  function renderHint() {
    var hint = q("[data-role='assist-hint']");
    if (!hint) return;
    if (S.selectionHint && (S.selectionHint.concept_id || S.selectionHint.region_id)) {
      hint.innerHTML = "対象候補: <span style='background:var(--color-background-tertiary,#f0efe9);border-radius:10px;padding:1px 8px'>" +
        esc(S.selectionHint.label || S.selectionHint.concept_id || S.selectionHint.region_id) +
        "</span> <button class='admin-action-btn' data-role='assist-clear-hint' type='button' style='font-size:10.5px;padding:0 6px'>×</button>" +
        " <span style='color:var(--color-text-tertiary)'>（ヒント。発言内容と矛盾すればAIが問い返します）</span>";
      var clr = hint.querySelector("[data-role='assist-clear-hint']");
      if (clr) clr.addEventListener("click", function () { S.selectionHint = null; renderHint(); });
    } else {
      hint.innerHTML = "<span style='color:var(--color-text-tertiary)'>プレビュー上のノードをクリックすると対象候補として渡せます。</span>";
    }
  }

  // ---- ログ描画 ----
  function appendBubble(role, node) {
    var log = q("[data-role='assist-log']");
    if (!log) return;
    var isTeacher = role === "teacher";
    var wrap = elem("div", { style: "display:flex;margin:4px 0;" + (isTeacher ? "justify-content:flex-end" : "justify-content:flex-start") });
    var bubble = elem("div", {
      style: "max-width:88%;font-size:12.5px;border-radius:8px;padding:6px 9px;" +
        (isTeacher
          ? "background:var(--color-accent,#378ADD);color:#fff"
          : "background:var(--color-background-tertiary,#f0efe9);color:var(--color-text-primary)"),
    });
    if (typeof node === "string") bubble.textContent = node;
    else bubble.appendChild(node);
    wrap.appendChild(bubble);
    log.appendChild(wrap);
    log.scrollTop = log.scrollHeight;
  }

  function appendSystem(text) {
    var log = q("[data-role='assist-log']");
    if (!log) return;
    log.appendChild(elem("div", {
      style: "text-align:center;font-size:11px;color:var(--color-text-tertiary);margin:6px 0",
    }, text));
    log.scrollTop = log.scrollHeight;
  }

  function targetLabel(t) {
    if (!t) return "（対象不明）";
    if (t.kind === "concept") return "概念「" + (t.label_snapshot || t.concept_id || "?") + "」";
    if (t.kind === "region") return "領域「" + (t.label_snapshot || t.region_id || "?") + "」";
    if (t.kind === "edge") return "エッジ " + (t.edge_from || "?") + " → " + (t.edge_to || "?");
    if (t.kind === "region_set") return "領域 " + (t.region_ids || []).join(", ");
    return "（対象未特定）";
  }

  // 意図確認カード (§4.4 (a))。合っています / 別の話です。
  function renderIntentCard(interp) {
    var card = elem("div", { style: "min-width:200px" });
    card.appendChild(elem("div", { style: "font-size:11px;color:var(--color-text-secondary);margin-bottom:2px" }, "意図の確認"));
    card.appendChild(elem("div", { style: "font-weight:600;margin-bottom:2px" }, targetLabel(interp.target)));
    card.appendChild(elem("div", { style: "margin-bottom:6px" }, "→ " + (interp.requested_change || "")));
    if (interp.is_continuation) {
      card.appendChild(elem("div", { style: "font-size:11px;color:var(--color-text-tertiary);margin-bottom:6px" }, "（直前のやりとりの続きとして解釈しました）"));
    }
    var btns = elem("div", { style: "display:flex;gap:6px;flex-wrap:wrap" });
    var ok = elem("button", { class: "admin-action-btn", type: "button", style: "font-size:11.5px" }, "合っています → 提案へ");
    var other = elem("button", { class: "admin-action-btn", type: "button", style: "font-size:11.5px" }, "別の話です");
    btns.appendChild(ok);
    btns.appendChild(other);
    card.appendChild(btns);
    card.appendChild(elem("div", { style: "font-size:10.5px;color:var(--color-text-tertiary);margin-top:4px" },
      "違う場合はそのまま訂正を入力してください（例: 違う、隣の領域の方）。"));

    ok.addEventListener("click", function () {
      if (S.busy) return;
      ok.disabled = true; other.disabled = true;
      requestProposal(interp);
    });
    other.addEventListener("click", function () {
      doReset(false);
      appendSystem("新しい会話として続けます。次の発言をどうぞ。");
    });
    appendBubble("agent", card);
  }

  // 差分提案カード (§4.4 (b))。適用 / 破棄。
  function renderProposalCard(resp) {
    var proposal = resp.proposal || {};
    var patch = proposal.patch || [];
    var validation = resp.validation || { errors: [], warnings: [] };
    var applicable = !!resp.result_skeleton && !(validation.errors || []).length;

    var card = elem("div", { style: "min-width:220px" });
    card.appendChild(elem("div", { style: "font-size:11px;color:var(--color-text-secondary);margin-bottom:2px" }, "編集案"));
    card.appendChild(elem("div", { style: "font-weight:600;margin-bottom:4px" }, proposal.summary || "（変更）"));

    if (!patch.length) {
      card.appendChild(elem("div", { style: "color:var(--color-text-tertiary)" }, "変更点はありませんでした。"));
    }
    patch.forEach(function (op) {
      var line = elem("div", { style: "font-family:monospace;font-size:11px;border-left:2px solid var(--color-border);padding-left:6px;margin:3px 0" });
      var before = op.before != null ? op.before : (op.op === "remove" ? "(削除)" : "");
      var after = op.after != null ? op.after : (op.value_json != null ? op.value_json : "");
      line.innerHTML = "<span style='color:var(--color-text-tertiary)'>" + esc(op.op) + " " + esc(op.path) + "</span><br>" +
        (before ? "<span style='color:#c0392b'>- " + esc(before) + "</span><br>" : "") +
        (after ? "<span style='color:#0F6E56'>+ " + esc(after) + "</span>" : "");
      card.appendChild(line);
    });

    (validation.warnings || []).forEach(function (w) {
      card.appendChild(elem("div", { style: "font-size:11px;color:#854F0B;margin-top:3px" }, "警告: " + (w.message || w)));
    });
    (validation.errors || []).forEach(function (e) {
      card.appendChild(elem("div", { style: "font-size:11px;color:var(--color-text-danger,#e53935);margin-top:3px" }, "エラー: " + (e.message || e)));
    });

    var btns = elem("div", { style: "display:flex;gap:6px;margin-top:6px" });
    var apply = elem("button", { class: "admin-action-btn", type: "button", style: "font-size:11.5px" }, "適用（保存）");
    var discard = elem("button", { class: "admin-action-btn", type: "button", style: "font-size:11.5px" }, "破棄");
    if (!applicable) { apply.disabled = true; apply.title = "検証エラーのため適用できません"; }
    btns.appendChild(apply);
    btns.appendChild(discard);
    card.appendChild(btns);

    apply.addEventListener("click", function () {
      if (S.busy || !applicable) return;
      apply.disabled = true; discard.disabled = true;
      applyProposal(resp.result_skeleton);
    });
    discard.addEventListener("click", function () {
      apply.disabled = true; discard.disabled = true;
      appendSystem("提案を破棄しました。");
    });
    appendBubble("agent", card);
  }

  // ---- API 呼び出し ----
  function currentRevision() {
    return S.config && typeof S.config.getRevision === "function" ? S.config.getRevision() : null;
  }

  function assistUrl(kind) {
    return S.config.basePath() + "/assist/" + kind;
  }

  function handleResp(res) {
    return res.json().then(function (body) {
      if (!res.ok) {
        var detail = body.detail;
        if (res.status === 409) {
          throw new Error("draft が更新されています。骨格を再読込してから続けてください（会話をリセットします）");
        }
        if (res.status === 429) {
          throw new Error(typeof detail === "string" ? detail : "本日のAIアシスト上限に達しました");
        }
        throw new Error(typeof detail === "string" ? detail : (detail && detail.message) || ("HTTP " + res.status));
      }
      return body;
    });
  }

  function onSend() {
    if (S.busy) return;
    var ta = q("[data-role='assist-input']");
    var text = ta ? ta.value.trim() : "";
    if (!text) return;
    ta.value = "";
    appendBubble("teacher", text);
    S.boundRevision = currentRevision();

    var hint = S.selectionHint && (S.selectionHint.concept_id || S.selectionHint.region_id)
      ? { region_id: S.selectionHint.region_id || null, concept_id: S.selectionHint.concept_id || null }
      : null;

    S.busy = true;
    var appended = appendPending("解釈しています…");
    S.config.apiFetch(assistUrl("interpret"), {
      method: "POST",
      body: JSON.stringify({
        message: text,
        revision: S.boundRevision,
        history: S.history,
        selection_hint: hint,
      }),
    })
      .then(handleResp)
      .then(function (body) {
        removePending(appended);
        var interp = body.interpretation || {};
        // 履歴に積む (teacher 発言 + AI 解釈の対象)
        S.history.push({ role: "teacher", content: text, resolved_target: interp.target || null });
        highlightTarget(interp.target);
        if (interp.ambiguous || (interp.target && interp.target.kind === "unresolved")) {
          var qtext = interp.clarifying_question || "どの対象のことか特定できませんでした。もう少し具体的に教えてください。";
          S.history.push({ role: "agent", content: qtext });
          appendBubble("agent", qtext);
        } else {
          S.pendingInterpretation = interp;
          S.history.push({ role: "agent", content: interp.requested_change || "" });
          renderIntentCard(interp);
        }
      })
      .catch(function (err) {
        removePending(appended);
        appendSystem("エラー: " + err.message);
        if (/draft が更新/.test(err.message)) doReset(false);
      })
      .finally(function () { S.busy = false; });
  }

  function requestProposal(interp) {
    S.busy = true;
    var appended = appendPending("編集案を作成しています…");
    S.config.apiFetch(assistUrl("propose"), {
      method: "POST",
      body: JSON.stringify({
        confirmed_interpretation: interp,
        revision: currentRevision(),
      }),
    })
      .then(handleResp)
      .then(function (body) {
        removePending(appended);
        S.history.push({ role: "agent", content: (body.proposal && body.proposal.summary) || "編集案を提示" });
        renderProposalCard(body);
      })
      .catch(function (err) {
        removePending(appended);
        appendSystem("エラー: " + err.message);
        if (/draft が更新/.test(err.message)) doReset(false);
      })
      .finally(function () { S.busy = false; });
  }

  function applyProposal(resultSkeleton) {
    if (!S.config || typeof S.config.onApply !== "function") return;
    S.busy = true;
    S.applying = true;
    appendSystem("適用して保存しています…");
    Promise.resolve(S.config.onApply(resultSkeleton))
      .then(function () {
        appendSystem("適用しました。draft を保存しました。");
        // 保存で revision が進む。以降の会話は最新 draft を基準に再開する
        S.history = [];
        S.pendingInterpretation = null;
      })
      .catch(function (err) {
        appendSystem("適用に失敗しました: " + (err && err.message ? err.message : err));
      })
      .finally(function () { S.busy = false; S.applying = false; });
  }

  // 進行中プレースホルダ
  function appendPending(text) {
    var log = q("[data-role='assist-log']");
    if (!log) return null;
    var node = elem("div", { style: "text-align:center;font-size:11px;color:var(--color-text-tertiary);margin:6px 0" }, text);
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }
  function removePending(node) { if (node && node.parentNode) node.parentNode.removeChild(node); }

  function highlightTarget(target) {
    if (!target || !S.config || typeof S.config.onHighlight !== "function") return;
    var detail = null;
    if (target.kind === "concept" && target.concept_id) {
      detail = { kind: "concept", concept_id: target.concept_id, region_id: target.region_id || "", label: target.label_snapshot || target.concept_id };
    } else if (target.kind === "region" && target.region_id) {
      detail = { kind: "region", region_id: target.region_id, concept_id: "", label: target.label_snapshot || target.region_id };
    }
    if (detail) {
      S.selectionHint = detail;
      renderHint();
      S.config.onHighlight(detail);
    }
  }

  function doReset(rebuildNotice) {
    S.history = [];
    S.pendingInterpretation = null;
    if (rebuildNotice) {
      var log = q("[data-role='assist-log']");
      if (log) log.innerHTML = "";
      appendSystem("会話をリセットしました。");
    }
  }

  // ---- 公開 API ----
  window.AtlasAssistPanel = {
    open: function (config) {
      S.config = config;
      S.selectionHint = (config && typeof config.getSelectionHint === "function") ? config.getSelectionHint() : null;
      S.boundRevision = currentRevision();
      buildShell();
      var input = q("[data-role='assist-input']");
      if (input) input.focus();
    },
    reset: function () { doReset(false); },
    setSelectionHint: function (detail) {
      S.selectionHint = detail;
      renderHint();
    },
    notifyDraftChanged: function (revision) {
      if (revision === S.boundRevision) return;
      S.boundRevision = revision;
      // 自分の「適用」による保存では通知しない (applyProposal 側で履歴を再開する)
      if (S.applying) return;
      // 古い revision に基づく会話は無効化する (§4.1)
      if (S.history.length || S.pendingInterpretation) {
        S.pendingInterpretation = null;
        S.history = [];
        if (panel() && panel().style.display !== "none") {
          appendSystem("draft が更新されました。会話を続ける場合は最新の骨格に対して新しく話しかけてください。");
        }
      }
    },
  };
})();
