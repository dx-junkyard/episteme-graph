/* ===================================================================
   Episteme Graph — D層（Doubt Layer）管理 UI
   前提の地図（Assumption Atlas）/ 認識的地位台帳 / 疑義 / 反実仮想

   注意: この「前提の地図」は実装済みの「分野の地図（Field Atlas,
   atlas-*.js）」とは**別機能**。命名衝突回避のため本ファイルの
   すべての識別子・CSS クラスは doubt- プレフィックスを使う。

   UX の一線（docs/features/doubt_layer_issues.md §0）:
   - ゾーン名・煽り文句・推奨マークを描かない。軸ラベルは事実記述のみ。
   - 生数値スコアを表示しない（段階ラベルのみ）。
   - 空欄（検証スコープ 0 件）はエラー表示にしない。事実として静かに表示。
   - プル型: タブを開いた人だけが見る。通知・バッジ・ログイン時表示なし。
   - 反実仮想は可逆・非破壊（「元のグラフは変更されません」を明示）。
   =================================================================== */

(function () {
  "use strict";

  var API = "/api";

  function token() {
    return localStorage.getItem("eg_token") || null;
  }

  function apiFetch(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (!opts._noJson) headers["Content-Type"] = "application/json";
    var t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    opts.headers = headers;
    return fetch(API + path, opts);
  }

  function escHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ── 語彙ラベル（事実記述のみ。評価語を使わない）─────────────────────
  //    正本はサーバ（core/doubt/schema.py の各 *_LABELS / STATUS_LABELS は
  //    core/label_vocab.py::VERIFICATION_STATUS_LABELS_LEDGER）。この表は
  //    backend/tests/test_doubt_vocab_mirror.py が逐語一致を固定するミラーで、
  //    片側だけを直すとテストが落ちる。
  var STATUS_LABELS = {
    directly_verified: "直接検証の記帳あり",
    indirectly_supported: "間接的な支持あり",
    untested: "未検証",
    refuted: "反証の記帳あり",
    unknown: "検証情報なし",
  };
  var LOAD_LABELS = { low: "低", medium: "中", high: "高", highest: "最高位" };
  var COVERAGE_LABELS = {
    none: "記帳なし", single: "1件", several: "少数", broad: "広い",
  };
  var CHALLENGE_TYPE_LABELS = {
    scope_extrapolation: "検証スコープ外への外挿という疑義",
    untested_in_domain: "この領域では未検証という疑義",
    definitional: "定義の曖昧さという疑義",
    hidden_lemma: "暗黙の補題に依存しているという疑義",
  };
  var CHALLENGE_STATUS_LABELS = {
    open: "未対応", answered: "対応済み", withdrawn: "取り下げ済み",
    led_to_verification: "検証提案へ昇格済み",
  };
  // 疑いの様相（doubt_type）の表はこのファイルに持たない。教員画面で使う場面が
  // 無く（参照ゼロ）、学習者画面は API の doubt_type_label をそのまま描くため、
  // 表を置くと分裂の種にしかならない（正本は core/structure_anchor/schema.py）。

  // ── SL層（賭け金の台帳）語彙ラベル。サーバ（core/doubt/schema.py）と同一の表を
  //    フロントに複製する（設計書 §2-10）。逐語で使う。─────────────────────
  var FALSIFICATION_KIND_LABELS = {
    observation_value: "観測値そのもの",
    auxiliary_hypothesis: "較正・装置などの補助仮説",
    not_formulable: "反証条件を定式化できないという記帳",
  };
  var REACHABILITY_LABELS = {
    reachable: "現在の観測で検証可能",
    next_generation: "次世代の装置・観測なら可能",
    unreachable: "現状では困難",
    unassessed: "未評価",
  };
  // 反実仮想「観測を仮に倒す」の Duhem 区別（aspect）。kind と語彙キーは異なるが
  // 教員に見せる日本語は同じ区別を指すため文言を揃える。
  var FALSIFICATION_ASPECT_LABELS = {
    value: "観測値そのもの",
    systematics: "較正・装置などの補助仮説",
  };
  // 支持線の段階（SL-3, 数値は出さない）。
  var SUPPORT_LEVEL_BADGE_LABELS = {
    none: "支持記録なし",
    single: "単一の支持線",
    several: "複数の支持線",
  };

  function reachabilityOptionsHtml(selected) {
    selected = selected || "unassessed";
    return Object.keys(REACHABILITY_LABELS).map(function (key) {
      var sel = (key === selected) ? " selected" : "";
      return '<option value="' + escHtml(key) + '"' + sel + '>' + escHtml(REACHABILITY_LABELS[key]) + '</option>';
    }).join("");
  }

  // ── スタイル注入（このファイルで完結させる）───────────────────────────
  function injectStyles() {
    if (document.getElementById("doubt-atlas-styles")) return;
    var css = "" +
      ".doubt-section{margin-top:12px;padding-top:10px;border-top:1px dashed #cbd5e1}" +
      ".doubt-section b{font-size:12px;color:#475569}" +
      ".doubt-fact{font-size:12px;color:#475569;margin:4px 0}" +
      ".doubt-muted{font-size:12px;color:#94a3b8;margin:4px 0}" +
      ".doubt-scope{border:1px solid #e2e8f0;border-radius:6px;padding:6px 8px;margin:6px 0;font-size:12px}" +
      ".doubt-scope-axis{color:#0f172a}" +
      ".doubt-scope-meta{color:#94a3b8;font-size:11px;margin-top:2px}" +
      ".doubt-candidate{border:1px dashed #c4b5fd;background:#faf5ff;border-radius:6px;padding:6px 8px;margin:6px 0;font-size:12px;opacity:0.9}" +
      ".doubt-chip-btn{font-size:11px;padding:2px 8px;margin-right:6px;margin-top:4px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;cursor:pointer}" +
      ".doubt-chip-btn:hover{background:#f1f5f9}" +
      ".doubt-form{margin-top:6px;padding:8px;border:1px solid #e2e8f0;border-radius:6px;background:#f8fafc}" +
      ".doubt-form input,.doubt-form textarea,.doubt-form select{width:100%;box-sizing:border-box;margin:3px 0;padding:4px 6px;font-size:12px;border:1px solid #cbd5e1;border-radius:4px}" +
      ".doubt-form label{font-size:11px;color:#64748b}" +
      ".doubt-atlas-wrap{display:flex;gap:16px;flex-wrap:wrap}" +
      ".doubt-atlas-plot{flex:1 1 460px;min-width:380px}" +
      ".doubt-atlas-detail{flex:1 1 300px;min-width:280px;border:1px solid #e2e8f0;border-radius:8px;padding:10px;background:#fff;max-height:520px;overflow-y:auto}" +
      ".doubt-list-row{border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px;margin:6px 0;font-size:12.5px;background:#fff;cursor:pointer}" +
      ".doubt-list-row:hover{background:#f8fafc}" +
      ".doubt-badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;background:#f1f5f9;color:#475569;margin-right:6px}" +
      ".doubt-cf-note{font-size:11.5px;color:#64748b;margin:4px 0}" +
      ".doubt-cf-btn{font-size:12px;padding:3px 10px;border:1px solid #cbd5e1;border-radius:6px;background:#fff;cursor:pointer;margin-right:6px}" +
      ".doubt-cf-btn.on{background:#eef2ff;border-color:#818cf8}" +
      ".doubt-svg-point{cursor:pointer}" +
      ".doubt-card{border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;margin:8px 0;background:#fff}" +
      ".doubt-card h5{margin:0 0 4px;font-size:13px}";
    var style = document.createElement("style");
    style.id = "doubt-atlas-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }

  /* =================================================================
     D1-4 / D1-5 / D3-1: ノード詳細ペインの「認識的地位」セクション
     ================================================================= */

  function renderNodeLedgerSection(detailEl, targetType, targetId) {
    if (!detailEl || !targetId) return;
    injectStyles();
    var container = document.createElement("div");
    container.className = "doubt-section";
    container.setAttribute("data-doubt-target", targetType + ":" + targetId);
    detailEl.appendChild(container);

    apiFetch("/admin/doubt/ledger/" + encodeURIComponent(targetType) + "/" + encodeURIComponent(targetId))
      .then(function (res) {
        if (res.status === 404) return null;
        if (!res.ok) throw new Error("ledger fetch failed");
        return res.json();
      })
      .then(function (entry) {
        renderLedgerInto(container, targetType, targetId, entry);
      })
      .catch(function () {
        // fail-closed: worker/API 失敗時は候補セクションを出さず、既存表示を壊さない
        if (container.parentNode) container.parentNode.removeChild(container);
      });
  }

  // ── SL-1: 「覆る条件」区画（確定済み条件・候補カード・手動記帳フォーム）─────
  // entry が無い（台帳行がまだ無い）場合も呼べる（entry=null のとき候補・確定済みは
  // 空扱い。記帳フォーム自体は台帳行の有無に関わらず操作できる — スコープ記帳と同型）。
  function falsificationSectionHtml(entry) {
    var conditions = (entry && entry.falsification_conditions) || [];
    var candidates = (entry && entry.falsification_candidates) || [];
    var html = '<div class="doubt-section"><b>覆る条件</b>';
    if (conditions.length) {
      conditions.forEach(function (c) {
        html += '<div class="doubt-scope"><span class="doubt-scope-axis">' + escHtml(c.statement) + '</span>';
        html += '<div class="doubt-scope-meta">区分: ' +
          escHtml(FALSIFICATION_KIND_LABELS[c.kind] || c.kind) + ' / 到達可能性: ' +
          escHtml(REACHABILITY_LABELS[c.reachability] || REACHABILITY_LABELS.unassessed) + '</div>';
        if (c.evidence_quote) {
          html += '<div class="doubt-scope-meta">根拠: 「' + escHtml(c.evidence_quote) + '」</div>';
        }
        html += '<div class="doubt-scope-meta">出所: 教員の記帳' +
          (c.reason ? ' — ' + escHtml(c.reason) : '') + '</div></div>';
      });
    } else {
      html += '<p class="doubt-muted">覆る条件はまだ記帳されていません。</p>';
    }
    candidates.forEach(function (c) {
      html += '<div class="doubt-candidate" data-doubt-falsification-candidate="' + escHtml(c.candidate_id) + '">' +
        '候補: ' + escHtml(c.statement) +
        '<div class="doubt-scope-meta">区分: ' + escHtml(FALSIFICATION_KIND_LABELS[c.kind] || c.kind) + '</div>';
      if (c.evidence_quote) html += '<div class="doubt-scope-meta">「' + escHtml(c.evidence_quote) + '」</div>';
      if (c.reason) html += '<div class="doubt-scope-meta">' + escHtml(c.reason) + '</div>';
      html += '<label>到達可能性（確認時に選択）</label>' +
        '<select data-doubt-falsification-reachability="' + escHtml(c.candidate_id) + '">' +
        reachabilityOptionsHtml() + '</select>';
      html += '<div>' +
        '<button type="button" class="doubt-chip-btn" data-doubt-falsification-confirm="' + escHtml(c.candidate_id) +
        '" data-ui-anchor="doubt-atlas.falsification-candidate-decide">確認して記帳</button>' +
        '<button type="button" class="doubt-chip-btn" data-doubt-falsification-dismiss="' + escHtml(c.candidate_id) +
        '" data-ui-anchor="doubt-atlas.falsification-candidate-decide">見送る</button>' +
        '</div></div>';
    });
    html += '<button type="button" class="doubt-chip-btn" data-doubt-falsification-form="1" ' +
      'data-ui-anchor="doubt-atlas.falsification-record">覆る条件を記帳する</button>';
    html += '<div data-doubt-falsification-form-slot="1"></div>';
    html += '<button type="button" class="doubt-chip-btn" data-doubt-falsification-refresh="1" ' +
      'data-ui-anchor="doubt-atlas.falsification-refresh">AIに候補を出してもらう</button>';
    html += '<span class="doubt-muted" data-doubt-falsification-refresh-msg=""></span>';
    html += '</div>';
    return html;
  }

  // ── SL-3: 独立支持経路の事実文（数値は出さない・導出失敗はキーなしで非表示）───
  function supportLinesFactHtml(entry) {
    var sl = entry && entry.support_lines;
    if (!sl || !sl.fact_line) return "";
    var html = '<div class="doubt-section"><b>支持線</b><p class="doubt-fact">' + escHtml(sl.fact_line) + '</p>';
    if (sl.level === "single" && (sl.cut_members || []).length) {
      var cutLabels = sl.cut_members.map(function (m) { return m.label || m.node_id; }).join("、");
      html += '<p class="doubt-fact">同時に崩れると支持が途切れる箇所: ' + escHtml(cutLabels) + '</p>';
    }
    if ((sl.observation_roots || []).length) {
      var rootLabels = sl.observation_roots.map(function (r) { return r.label || r.claim_id; }).join("、");
      html += '<p class="doubt-muted">観測からの支持根: ' + escHtml(rootLabels) + '</p>';
    }
    html += '</div>';
    return html;
  }

  function renderLedgerInto(container, targetType, targetId, entry) {
    var html = "<b>認識的地位</b>";
    if (!entry) {
      // 台帳行なし = 記帳がまだ無いという事実（エラーではない）
      html += '<p class="doubt-muted">検証スコープの記帳なし</p>';
      html += '<button type="button" class="doubt-chip-btn" data-doubt-scope-form="1" data-ui-anchor="doubt-atlas.record-scope">スコープを記帳</button>';
      html += '<div data-doubt-form-slot="1"></div>';
      // T1(G2-D): 検証状態の記帳導線（台帳行が無くても記帳操作自体は可能）
      html += '<button type="button" class="doubt-chip-btn" data-doubt-vstatus-form="1" data-ui-anchor="doubt-atlas.record-verification-status">検証状態を記帳する</button>';
      html += '<div data-doubt-vstatus-slot="1"></div>';
      // SL-1: 台帳行が無くても「覆る条件」の記帳自体は可能（スコープ記帳と同型）。
      html += falsificationSectionHtml(null);
      container.innerHTML = html;
      bindScopeForm(container, targetType, targetId);
      bindVerificationStatusForm(container, targetType, targetId, 0);
      bindFalsificationSection(container, targetType, targetId);
      return;
    }

    html += '<p class="doubt-fact">検証状態: ' +
      escHtml(STATUS_LABELS[entry.verification_status] || entry.verification_status) + '</p>';

    var scopes = entry.verification_scopes || [];
    if (scopes.length) {
      scopes.forEach(function (s) {
        var axes = [s.condition, s.domain, s.precision, s.system]
          .filter(function (v) { return v; }).join(" / ");
        html += '<div class="doubt-scope"><span class="doubt-scope-axis">' + escHtml(axes) + '</span>';
        if (s.evidence_quote) {
          html += '<div class="doubt-scope-meta">根拠: 「' + escHtml(s.evidence_quote) + '」</div>';
        }
        if ((s.evidence_ids || []).length) {
          html += '<div class="doubt-scope-meta">evidence: ' + escHtml(s.evidence_ids.join(", ")) + '</div>';
        }
        html += '<div class="doubt-scope-meta">記帳者: ' + escHtml(s.recorded_by || "-") +
          (s.reason ? ' — ' + escHtml(s.reason) : '') + '</div></div>';
      });
    } else {
      html += '<p class="doubt-muted">検証スコープの記帳なし</p>';
    }

    // 合意の 2 軸は検証とは**別々の行**で表示（混同不可能に, D1-4）
    if (entry.consensus_explicit_label) {
      html += '<p class="doubt-fact">明示的合意: ' + escHtml(entry.consensus_explicit_label) + '</p>';
    } else {
      html += '<p class="doubt-muted">明示的合意: 承認記録なし</p>';
    }
    html += '<p class="doubt-fact">行動上の合意: ' +
      escHtml(String(entry.consensus_behavioral_dependents || 0)) + ' 箇所が依存</p>';
    if (entry.load_level) {
      html += '<p class="doubt-fact">依存の広がり（段階）: ' +
        escHtml(LOAD_LABELS[entry.load_level] || entry.load_level) + '</p>';
    }

    // D1-5: 学習者の問い（k≥3 のときのみ・件数は出さない）
    if (entry.naive_signal && entry.naive_signal.present) {
      html += '<p class="doubt-fact">学習者の問い: 複数の学習者がここで問いを立てています</p>';
    }

    // D1-4: LLM スコープ候補（薄色チップ・タップ 1 回で確定 / 却下）
    var candidates = entry.scope_candidates || [];
    candidates.forEach(function (c) {
      var axes = [c.condition, c.domain, c.precision, c.system]
        .filter(function (v) { return v; }).join(" / ");
      html += '<div class="doubt-candidate">候補: ' + escHtml(axes || "(軸未設定)");
      if (c.evidence_quote) html += '<div class="doubt-scope-meta">「' + escHtml(c.evidence_quote) + '」</div>';
      if (c.reason) html += '<div class="doubt-scope-meta">' + escHtml(c.reason) + '</div>';
      html += '<div>' +
        '<button type="button" class="doubt-chip-btn" data-doubt-candidate-confirm="' + escHtml(c.candidate_id) + '" data-ui-anchor="doubt-atlas.record-scope">確定</button>' +
        '<button type="button" class="doubt-chip-btn" data-doubt-candidate-dismiss="' + escHtml(c.candidate_id) + '" data-ui-anchor="doubt-atlas.record-scope">却下</button>' +
        '</div></div>';
    });

    html += '<button type="button" class="doubt-chip-btn" data-doubt-scope-form="1" data-ui-anchor="doubt-atlas.record-scope">スコープを記帳</button>';
    html += '<div data-doubt-form-slot="1"></div>';

    // T1(G2-D): 検証状態の記帳（「確定は人間」の実行手段）。directly_verified への
    // 昇格はスコープ1件以上が前提（サーバも422で弾くが、UI側でも選択肢を無効化する）。
    html += '<button type="button" class="doubt-chip-btn" data-doubt-vstatus-form="1" data-ui-anchor="doubt-atlas.record-verification-status">検証状態を記帳する</button>';
    html += '<div data-doubt-vstatus-slot="1"></div>';

    // D3-1: 疑義（併記。主語は常に型 — 人格対立の文面にしない）
    html += '<div class="doubt-section"><b>疑義</b>';
    var challenges = entry.challenges || [];
    var openChallenges = challenges.filter(function (c) { return c.status !== "withdrawn"; });
    if (openChallenges.length) {
      openChallenges.forEach(function (c) {
        html += '<div class="doubt-scope" data-doubt-challenge-item="' + escHtml(c.id) + '">' +
          escHtml(CHALLENGE_TYPE_LABELS[c.challenge_type] || c.challenge_type) +
          '<div class="doubt-scope-meta">' + escHtml(c.challenger_name || "") +
          (c.reason ? ' — ' + escHtml(c.reason) : '') + '</div>' +
          '<div class="doubt-scope-meta">状態: ' + escHtml(CHALLENGE_STATUS_LABELS[c.status] || c.status) + '</div>';
        if (c.id && (c.status === "open" || c.status === "answered")) {
          // T2: 自分の疑義への取り下げ・検証提案への昇格（取り下げは本人のみ・API 403 で保護）
          html += '<div>' +
            '<button type="button" class="doubt-chip-btn" data-doubt-challenge-withdraw="' + escHtml(c.id) + '" data-ui-anchor="doubt-atlas.manage-challenge">取り下げ</button>' +
            '<button type="button" class="doubt-chip-btn" data-doubt-challenge-proposal="' + escHtml(c.id) + '" data-ui-anchor="doubt-atlas.manage-challenge">検証提案にする</button>' +
            '</div>';
          html += '<div data-doubt-proposal-slot="' + escHtml(c.id) + '"></div>';
          html += '<span class="doubt-muted" data-doubt-challenge-action-msg="' + escHtml(c.id) + '"></span>';
        }
        html += '</div>';
      });
    } else {
      html += '<p class="doubt-muted">疑義はありません。</p>';
    }
    if (targetType === "assumption" || targetType === "claim") {
      html += '<button type="button" class="doubt-chip-btn" data-doubt-challenge-form="1" data-ui-anchor="doubt-atlas.record-challenge">疑義を残す</button>';
      html += '<div data-doubt-challenge-slot="1"></div>';
    }
    html += '</div>';

    // SL-1: 覆る条件（反証条件レジストリ）。SL-3: 支持線の事実文（optional キー）。
    html += falsificationSectionHtml(entry);
    html += supportLinesFactHtml(entry);

    container.innerHTML = html;
    bindScopeForm(container, targetType, targetId);
    bindCandidateButtons(container, targetType, targetId);
    bindVerificationStatusForm(container, targetType, targetId, scopes.length);
    bindChallengeForm(container, targetType, targetId);
    bindChallengeActions(container, targetType, targetId);
    bindFalsificationSection(container, targetType, targetId);
  }

  function refreshLedgerSection(container, targetType, targetId) {
    apiFetch("/admin/doubt/ledger/" + encodeURIComponent(targetType) + "/" + encodeURIComponent(targetId))
      .then(function (res) { return res.status === 404 ? null : res.json(); })
      .then(function (entry) { renderLedgerInto(container, targetType, targetId, entry); })
      .catch(function () { /* keep current view */ });
  }

  function bindScopeForm(container, targetType, targetId) {
    var btn = container.querySelector("[data-doubt-scope-form]");
    var slot = container.querySelector("[data-doubt-form-slot]");
    if (!btn || !slot) return;
    btn.addEventListener("click", function () {
      if (slot.firstChild) { slot.innerHTML = ""; return; }
      // 軽量フォーム: 必須は「1 軸 + 根拠 + 理由」だけ（記帳 1 件 30 秒以内の導線）
      slot.innerHTML =
        '<div class="doubt-form">' +
        '<label>成立条件（condition）</label><input data-f="condition" placeholder="例: 低エネルギー極限で">' +
        '<label>領域（domain）</label><input data-f="domain" placeholder="例: 電磁相互作用で">' +
        '<label>精度（precision）</label><input data-f="precision" placeholder="例: 一次摂動の精度で">' +
        '<label>系（system）</label><input data-f="system" placeholder="例: 水素原子で">' +
        '<label>根拠（原文の引用など）</label><textarea data-f="evidence_quote" rows="2"></textarea>' +
        '<label>理由</label><input data-f="reason" placeholder="なぜこのスコープと言えるか">' +
        '<button type="button" class="doubt-chip-btn" data-doubt-scope-submit="1" data-ui-anchor="doubt-atlas.record-scope">記帳する</button>' +
        '<span class="doubt-muted" data-doubt-scope-msg=""></span>' +
        '</div>';
      var submit = slot.querySelector("[data-doubt-scope-submit]");
      submit.addEventListener("click", function () {
        var body = {};
        ["condition", "domain", "precision", "system", "evidence_quote", "reason"].forEach(function (f) {
          body[f] = (slot.querySelector('[data-f="' + f + '"]').value || "").trim();
        });
        body.evidence_ids = [];
        apiFetch("/admin/doubt/ledger/" + encodeURIComponent(targetType) + "/" +
          encodeURIComponent(targetId) + "/scopes", { method: "POST", body: JSON.stringify(body) })
          .then(function (res) {
            if (res.status === 422) {
              return res.json().then(function (data) {
                slot.querySelector("[data-doubt-scope-msg]").textContent =
                  (data && data.detail) || "1 軸以上 + 根拠 + 理由が必要です";
                throw new Error("validation");
              });
            }
            if (!res.ok) throw new Error("failed");
            return res.json();
          })
          .then(function () { refreshLedgerSection(container, targetType, targetId); })
          .catch(function () { /* message already shown */ });
      });
    });
  }

  // T1(G2-D): 検証状態の記帳フォーム。「確定は人間」の実行手段そのもの。
  // directly_verified はスコープ1件以上が前提（サーバ422で最終的に弾くが、UI側でも
  // 選択肢を無効化し、事実文で案内する）。reason は必須入力（記帳は人間の行為として
  // 帰属される正式な学術行為, docs/features/doubt_layer_issues.md D1-3）。
  function bindVerificationStatusForm(container, targetType, targetId, scopesCount) {
    var btn = container.querySelector("[data-doubt-vstatus-form]");
    var slot = container.querySelector("[data-doubt-vstatus-slot]");
    if (!btn || !slot) return;
    btn.addEventListener("click", function () {
      if (slot.firstChild) { slot.innerHTML = ""; return; }
      var options = "";
      Object.keys(STATUS_LABELS).forEach(function (key) {
        var disabled = (key === "directly_verified" && scopesCount < 1) ? " disabled" : "";
        options += '<option value="' + escHtml(key) + '"' + disabled + '>' +
          escHtml(STATUS_LABELS[key]) + '</option>';
      });
      slot.innerHTML =
        '<div class="doubt-form">' +
        '<label>検証状態</label><select data-f="verification_status" data-ui-anchor="doubt-atlas.record-verification-status">' + options + '</select>' +
        (scopesCount < 1
          ? '<p class="doubt-muted">直接検証の記帳には検証スコープが1件以上必要です' +
            '（空欄は異常ではなく発見です。まずスコープを記帳してください）。</p>'
          : '') +
        '<label>理由（必須）</label><textarea data-f="reason" rows="2"></textarea>' +
        '<p class="doubt-muted">この記帳はあなたの操作として帰属され、監査ログに記録されます。</p>' +
        '<button type="button" class="doubt-chip-btn" data-doubt-vstatus-submit="1" data-ui-anchor="doubt-atlas.record-verification-status">記帳する</button>' +
        '<span class="doubt-muted" data-doubt-vstatus-msg=""></span>' +
        '</div>';
      var submit = slot.querySelector("[data-doubt-vstatus-submit]");
      var msg = slot.querySelector("[data-doubt-vstatus-msg]");
      submit.addEventListener("click", function () {
        var status = slot.querySelector('[data-f="verification_status"]').value;
        var reason = (slot.querySelector('[data-f="reason"]').value || "").trim();
        if (!reason) {
          msg.textContent = "理由の入力が必要です。";
          return;
        }
        if (status === "directly_verified" && scopesCount < 1) {
          msg.textContent = "直接検証の記帳には検証スコープの記帳が1件以上必要です。";
          return;
        }
        apiFetch("/admin/doubt/ledger/" + encodeURIComponent(targetType) + "/" +
          encodeURIComponent(targetId) + "/verification-status",
          { method: "PUT", body: JSON.stringify({ verification_status: status, reason: reason }) })
          .then(function (res) {
            if (res.status === 422) {
              return res.json().then(function (data) {
                msg.textContent = (data && data.detail) || "この検証状態には記帳できませんでした。";
                throw new Error("validation");
              });
            }
            if (!res.ok) throw new Error("failed");
            return res.json();
          })
          .then(function () { refreshLedgerSection(container, targetType, targetId); })
          .catch(function () { /* message already shown */ });
      });
    });
  }

  function bindCandidateButtons(container, targetType, targetId) {
    var base = "/admin/doubt/ledger/" + encodeURIComponent(targetType) + "/" +
      encodeURIComponent(targetId) + "/scope-candidates/";
    var confirms = container.querySelectorAll("[data-doubt-candidate-confirm]");
    var dismisses = container.querySelectorAll("[data-doubt-candidate-dismiss]");
    Array.prototype.forEach.call(confirms, function (btn) {
      btn.addEventListener("click", function () {
        apiFetch(base + encodeURIComponent(btn.getAttribute("data-doubt-candidate-confirm")) + "/confirm",
          { method: "POST", body: JSON.stringify({}) })
          .then(function () { refreshLedgerSection(container, targetType, targetId); });
      });
    });
    Array.prototype.forEach.call(dismisses, function (btn) {
      btn.addEventListener("click", function () {
        apiFetch(base + encodeURIComponent(btn.getAttribute("data-doubt-candidate-dismiss")) + "/dismiss",
          { method: "POST", body: JSON.stringify({}) })
          .then(function () { refreshLedgerSection(container, targetType, targetId); });
      });
    });
  }

  // ── SL-1: 覆る条件（反証条件レジストリ）区画の配線 ─────────────────────
  function bindFalsificationSection(container, targetType, targetId) {
    bindFalsificationForm(container, targetType, targetId);
    bindFalsificationCandidateButtons(container, targetType, targetId);
    bindFalsificationRefresh(container);
  }

  function bindFalsificationForm(container, targetType, targetId) {
    var btn = container.querySelector("[data-doubt-falsification-form]");
    var slot = container.querySelector("[data-doubt-falsification-form-slot]");
    if (!btn || !slot) return;
    btn.addEventListener("click", function () {
      if (slot.firstChild) { slot.innerHTML = ""; return; }
      var kindOptions = Object.keys(FALSIFICATION_KIND_LABELS).map(function (k) {
        return '<option value="' + escHtml(k) + '">' + escHtml(FALSIFICATION_KIND_LABELS[k]) + '</option>';
      }).join("");
      slot.innerHTML =
        '<div class="doubt-form">' +
        '<label>何が起これば覆るか（statement）</label><textarea data-f="statement" rows="2" placeholder="観測・測定の言明として書く"></textarea>' +
        '<label>区分（kind）</label><select data-f="kind">' + kindOptions + '</select>' +
        '<p class="doubt-muted" data-doubt-falsification-kind-note style="display:none">' +
        'この区分は反証条件を定式化できないという記帳です。根拠の引用は必須ではありません。</p>' +
        '<label>根拠（原文の引用）</label><textarea data-f="evidence_quote" rows="2"></textarea>' +
        '<label>理由（必須）</label><textarea data-f="reason" rows="2"></textarea>' +
        '<label>到達可能性</label><select data-f="reachability">' + reachabilityOptionsHtml() + '</select>' +
        '<button type="button" class="doubt-chip-btn" data-doubt-falsification-submit="1" data-ui-anchor="doubt-atlas.falsification-record">記帳する</button>' +
        '<span class="doubt-muted" data-doubt-falsification-msg=""></span>' +
        '</div>';
      var kindSelect = slot.querySelector('[data-f="kind"]');
      var kindNote = slot.querySelector("[data-doubt-falsification-kind-note]");
      kindSelect.addEventListener("change", function () {
        kindNote.style.display = (kindSelect.value === "not_formulable") ? "" : "none";
      });
      var submit = slot.querySelector("[data-doubt-falsification-submit]");
      var msg = slot.querySelector("[data-doubt-falsification-msg]");
      submit.addEventListener("click", function () {
        var statement = (slot.querySelector('[data-f="statement"]').value || "").trim();
        var evidenceQuote = (slot.querySelector('[data-f="evidence_quote"]').value || "").trim();
        var body = {
          statement: statement,
          kind: kindSelect.value,
          reason: (slot.querySelector('[data-f="reason"]').value || "").trim(),
          reachability: slot.querySelector('[data-f="reachability"]').value,
          evidence_quote: evidenceQuote,
        };
        apiFetch("/admin/doubt/ledger/" + encodeURIComponent(targetType) + "/" +
          encodeURIComponent(targetId) + "/falsification-conditions",
          { method: "POST", body: JSON.stringify(body) })
          .then(function (res) {
            if (res.status === 422) {
              return res.json().then(function (data) {
                msg.textContent = (data && data.detail) || "statement・kind・reason と根拠（引用または evidence）が必要です";
                throw new Error("validation");
              });
            }
            if (!res.ok) throw new Error("failed");
            return res.json();
          })
          .then(function () { refreshLedgerSection(container, targetType, targetId); })
          .catch(function () { /* message already shown */ });
      });
    });
  }

  // ボタン連打防止: 送信直後に disabled = true にする（confirm/dismiss の二重送信を防ぐ）。
  function bindFalsificationCandidateButtons(container, targetType, targetId) {
    var base = "/admin/doubt/ledger/" + encodeURIComponent(targetType) + "/" +
      encodeURIComponent(targetId) + "/falsification-candidates/";
    Array.prototype.forEach.call(
      container.querySelectorAll("[data-doubt-falsification-confirm]"),
      function (btn) {
        btn.addEventListener("click", function () {
          if (btn.disabled) return;
          btn.disabled = true;
          var cid = btn.getAttribute("data-doubt-falsification-confirm");
          var reachSel = container.querySelector('[data-doubt-falsification-reachability="' + cid + '"]');
          var body = { reachability: reachSel ? reachSel.value : "unassessed" };
          apiFetch(base + encodeURIComponent(cid) + "/confirm", { method: "POST", body: JSON.stringify(body) })
            .then(function () { refreshLedgerSection(container, targetType, targetId); })
            .catch(function () { btn.disabled = false; });
        });
      }
    );
    Array.prototype.forEach.call(
      container.querySelectorAll("[data-doubt-falsification-dismiss]"),
      function (btn) {
        btn.addEventListener("click", function () {
          if (btn.disabled) return;
          btn.disabled = true;
          var cid = btn.getAttribute("data-doubt-falsification-dismiss");
          apiFetch(base + encodeURIComponent(cid) + "/dismiss", { method: "POST", body: JSON.stringify({}) })
            .then(function () { refreshLedgerSection(container, targetType, targetId); })
            .catch(function () { btn.disabled = false; });
        });
      }
    );
  }

  // AI 候補の再生成はコース単位（worker が対象コース内の claim/assumption を横断処理する
  // ため、target 単位ではない — 前提マイニング実行ボタンと同じ非同期トリガー型）。
  function bindFalsificationRefresh(container) {
    var btn = container.querySelector("[data-doubt-falsification-refresh]");
    var msg = container.querySelector("[data-doubt-falsification-refresh-msg]");
    if (!btn) return;
    btn.addEventListener("click", function () {
      if (!tabState.courseId) {
        if (msg) msg.textContent = "コースを選択すると実行できます。";
        return;
      }
      if (btn.disabled) return;
      btn.disabled = true;
      apiFetch("/admin/doubt/courses/" + encodeURIComponent(tabState.courseId) + "/falsification-candidates/refresh",
        { method: "POST", body: JSON.stringify({}) })
        .then(function (res) { return res.json ? res.json().catch(function () { return {}; }) : {}; })
        .then(function (data) {
          if (msg) {
            msg.textContent = (data && data.scheduled)
              ? "候補生成を予約しました（しばらくしてから再読込してください）。"
              : "実行しました。";
          }
        })
        .catch(function () { if (msg) msg.textContent = "実行に失敗しました。"; })
        .then(function () { btn.disabled = false; });
    });
  }

  // SL-8: 検証提案（proposal）の最小ステータス遷移カード。GET での一覧 API が無いため
  // （設計書 §2-8）、作成直後のレスポンス（proposal_id）を使ってその場に描く「既存カード内
  // のボタン群」に留める。ページ遷移・再読込後は表示が引き継がれない（既知の限界・最小実装）。
  var PROPOSAL_STATUS_LABELS = {
    proposed: "検証に着手可能", in_progress: "検証に着手済み",
    completed: "完了", withdrawn: "取り下げ済み",
  };

  function renderProposalStatusCard(slot, proposalId, status) {
    var html = '<div class="doubt-card"><p class="doubt-fact">検証提案（コーパス外の確認あり）: ' +
      escHtml(PROPOSAL_STATUS_LABELS[status] || status) + '</p>';
    if (status === "proposed" || status === "in_progress") {
      html += '<div>';
      if (status === "proposed") {
        html += '<button type="button" class="doubt-chip-btn" data-doubt-proposal-status="in_progress" ' +
          'data-ui-anchor="doubt-atlas.manage-challenge">検証に着手</button>';
      }
      if (status === "in_progress") {
        html += '<button type="button" class="doubt-chip-btn" data-doubt-proposal-status="completed" ' +
          'data-ui-anchor="doubt-atlas.manage-challenge">完了を記録</button>';
      }
      html += '<button type="button" class="doubt-chip-btn" data-doubt-proposal-status="withdrawn" ' +
        'data-ui-anchor="doubt-atlas.manage-challenge">取り下げ</button>';
      html += '</div>';
    }
    html += '<span class="doubt-muted" data-doubt-proposal-status-msg=""></span></div>';
    slot.innerHTML = html;
    Array.prototype.forEach.call(slot.querySelectorAll("[data-doubt-proposal-status]"), function (btn) {
      btn.addEventListener("click", function () {
        if (btn.disabled) return;
        btn.disabled = true;
        var newStatus = btn.getAttribute("data-doubt-proposal-status");
        apiFetch("/admin/doubt/proposals/" + encodeURIComponent(proposalId),
          { method: "PATCH", body: JSON.stringify({ status: newStatus }) })
          .then(function (res) {
            if (!res.ok) throw new Error("failed");
            return res.json();
          })
          .then(function () { renderProposalStatusCard(slot, proposalId, newStatus); })
          .catch(function () {
            var msg = slot.querySelector("[data-doubt-proposal-status-msg]");
            if (msg) msg.textContent = "更新に失敗しました。";
            btn.disabled = false;
          });
      });
    });
  }

  function bindChallengeForm(container, targetType, targetId) {
    var btn = container.querySelector("[data-doubt-challenge-form]");
    var slot = container.querySelector("[data-doubt-challenge-slot]");
    if (!btn || !slot) return;
    btn.addEventListener("click", function () {
      if (slot.firstChild) { slot.innerHTML = ""; return; }
      // 型選択を先・自由記述を後（地位勾配への配慮, §8-3）
      var options = Object.keys(CHALLENGE_TYPE_LABELS).map(function (k) {
        return '<option value="' + k + '">' + escHtml(CHALLENGE_TYPE_LABELS[k]) + '</option>';
      }).join("");
      slot.innerHTML =
        '<div class="doubt-form">' +
        '<label>疑義の型</label><select data-f="challenge_type">' + options + '</select>' +
        '<label>理由（本人の言葉・必須）</label><textarea data-f="reason" rows="2"></textarea>' +
        '<button type="button" class="doubt-chip-btn" data-doubt-challenge-submit="1" data-ui-anchor="doubt-atlas.record-challenge">疑義を残す</button>' +
        '<span class="doubt-muted" data-doubt-challenge-msg=""></span>' +
        '</div>';
      slot.querySelector("[data-doubt-challenge-submit]").addEventListener("click", function () {
        var body = {
          challenge_type: slot.querySelector('[data-f="challenge_type"]').value,
          reason: (slot.querySelector('[data-f="reason"]').value || "").trim(),
        };
        apiFetch("/admin/doubt/targets/" + encodeURIComponent(targetType) + "/" +
          encodeURIComponent(targetId) + "/challenges", { method: "POST", body: JSON.stringify(body) })
          .then(function (res) {
            if (res.status === 422) {
              slot.querySelector("[data-doubt-challenge-msg]").textContent = "理由（本人の言葉）が必要です";
              throw new Error("validation");
            }
            if (!res.ok) throw new Error("failed");
            return res.json();
          })
          .then(function () { refreshLedgerSection(container, targetType, targetId); })
          .catch(function () { /* message shown */ });
      });
    });
  }

  // T2: 疑義ライフサイクルの完結（取り下げ・検証提案への昇格）。
  // 取り下げは行削除ではなく status 遷移（P4）。本人以外は API が 403 を返す
  // （サーバ側 fail-closed。フロントはメッセージを事実文で表示するのみ）。
  function bindChallengeActions(container, targetType, targetId) {
    Array.prototype.forEach.call(
      container.querySelectorAll("[data-doubt-challenge-withdraw]"),
      function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.getAttribute("data-doubt-challenge-withdraw");
          var msg = container.querySelector('[data-doubt-challenge-action-msg="' + id + '"]');
          apiFetch("/admin/doubt/challenges/" + encodeURIComponent(id) + "/withdraw",
            { method: "POST", body: JSON.stringify({}) })
            .then(function (res) {
              if (res.status === 403) {
                if (msg) msg.textContent = "この疑義の取り下げは本人のみ可能です。";
                throw new Error("forbidden");
              }
              if (!res.ok) throw new Error("failed");
              return res.json();
            })
            .then(function () { refreshLedgerSection(container, targetType, targetId); })
            .catch(function () { /* message already shown（表示済みの場合）*/ });
        });
      }
    );
    Array.prototype.forEach.call(
      container.querySelectorAll("[data-doubt-challenge-proposal]"),
      function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.getAttribute("data-doubt-challenge-proposal");
          var slot = container.querySelector('[data-doubt-proposal-slot="' + id + '"]');
          if (!slot) return;
          if (slot.firstChild) { slot.innerHTML = ""; return; }
          slot.innerHTML =
            '<div class="doubt-form">' +
            '<label>検証提案（どの実験・計算で検証可能か）</label><textarea data-f="proposal" rows="2"></textarea>' +
            '<label>コーパス外の確認（必須）</label>' +
            '<textarea data-f="external_check" rows="2" placeholder="コーパス外で確認した文献・根拠を記録してください"></textarea>' +
            '<label>到達可能性</label><select data-f="reachability">' + reachabilityOptionsHtml() + '</select>' +
            '<button type="button" class="doubt-chip-btn" data-doubt-proposal-submit="1" data-ui-anchor="doubt-atlas.manage-challenge">提案として登録</button>' +
            '<span class="doubt-muted" data-doubt-proposal-msg=""></span>' +
            '</div>';
          slot.querySelector("[data-doubt-proposal-submit]").addEventListener("click", function () {
            var proposal = (slot.querySelector('[data-f="proposal"]').value || "").trim();
            var externalCheck = (slot.querySelector('[data-f="external_check"]').value || "").trim();
            var reachability = slot.querySelector('[data-f="reachability"]').value;
            var pmsg = slot.querySelector("[data-doubt-proposal-msg]");
            if (!proposal) { pmsg.textContent = "検証提案の内容が必要です。"; return; }
            // SL-8: コーパス外の文献確認の記録は必須（晴れ間は、まずコーパスの穴であり、
            // それが空の穴であることを人間が確かめたあとにだけ proposal が立つ）。
            if (!externalCheck) { pmsg.textContent = "コーパス外の文献確認の記録が必要です。"; return; }
            apiFetch("/admin/doubt/challenges/" + encodeURIComponent(id) + "/proposals",
              {
                method: "POST",
                body: JSON.stringify({ proposal: proposal, external_check: externalCheck, reachability: reachability }),
              })
              .then(function (res) {
                if (res.status === 422) {
                  return res.json().then(function (data) {
                    pmsg.textContent = (data && data.detail) || "登録できませんでした。";
                    throw new Error("validation");
                  });
                }
                if (!res.ok) throw new Error("failed");
                return res.json();
              })
              .then(function (data) {
                // 昇格に成功した時点で疑義は led_to_verification へ遷移済み。ここでは
                // フルリフレッシュ（refreshLedgerSection）を呼ばない — 呼ぶと GET 一覧を
                // 持たない proposal_id を失い、直後のステータス操作ができなくなるため
                // （設計書 §2-8）。代わりにこのカードだけをステータス遷移 UI に置き換え、
                // トグルボタン自体は無効化して誤操作でカードが消えないようにする。
                btn.disabled = true;
                var withdrawBtn = container.querySelector('[data-doubt-challenge-withdraw="' + id + '"]');
                if (withdrawBtn) withdrawBtn.disabled = true;
                var proposalId = data && data.proposal_id;
                if (proposalId) renderProposalStatusCard(slot, proposalId, "proposed");
              })
              .catch(function () { /* message shown */ });
          });
        });
      }
    );
  }

  /* =================================================================
     D3-4: 反実仮想モード（理論グラフ画面へのオーバーレイ）
     ================================================================= */

  var cfState = {
    active: false,
    toggledIds: [],          // トグル中の前提/ノード id スタック
    toggledObservations: [], // SL-2: [{claim_id, aspect, label}]（観測を仮に倒す）
    savedStyles: {},      // nodeId -> {opacity, dashes} 復帰用
    lastResult: null,
    visNodes: null,
    documentId: "",
    courseId: "",
  };

  function isCounterfactualActive() { return cfState.active; }

  function counterfactualToolbarHtml() {
    return '<div class="doubt-section" id="doubt-cf-bar">' +
      '<button type="button" class="doubt-cf-btn" id="doubt-cf-toggle">反実仮想モード</button>' +
      '<span class="doubt-cf-note">前提ノードをタップすると「仮に偽」にできます。元のグラフは変更されません。</span>' +
      '<span id="doubt-cf-status" class="doubt-cf-note"></span>' +
      '<span id="doubt-cf-actions"></span>' +
      '<div class="doubt-cf-note">' +
      '<button type="button" class="doubt-cf-btn" id="doubt-cf-obs-toggle" ' +
      'data-ui-anchor="doubt-atlas.counterfactual-observation-toggle">観測を仮に倒す</button>' +
      '<span class="doubt-cf-note">この観測に依存する範囲を確認します。元のグラフは変更されません。</span>' +
      '<div id="doubt-cf-obs-form-slot"></div>' +
      '<div id="doubt-cf-obs-list"></div>' +
      '</div>' +
      '</div>';
  }

  function bindCounterfactualToolbar(container, meta) {
    injectStyles();
    var toggle = container.querySelector("#doubt-cf-toggle");
    if (!toggle) return;
    cfState.documentId = (meta && meta.documentId) || "";
    cfState.courseId = (meta && meta.courseId) || "";
    toggle.addEventListener("click", function () {
      cfState.active = !cfState.active;
      toggle.className = "doubt-cf-btn" + (cfState.active ? " on" : "");
      if (!cfState.active) resetCounterfactual();
      updateCfStatus(container);
    });
    var obsToggle = container.querySelector("#doubt-cf-obs-toggle");
    if (obsToggle) {
      obsToggle.addEventListener("click", function () {
        var slot = container.querySelector("#doubt-cf-obs-form-slot");
        if (!slot) return;
        if (slot.firstChild) { slot.innerHTML = ""; return; }
        renderObservationToggleForm(slot, container);
      });
    }
  }

  // SL-2: 「観測を仮に倒す」— observation-targets から選び、aspect（Duhem 区別）を選んで
  // 追加する。グラフ上のノードクリックに依存しない（観測系 claim は必ずしもグラフ上の
  // ノードとして選べるとは限らないため、専用の一覧から選ぶ）。
  function renderObservationToggleForm(slot, container) {
    slot.innerHTML = '<p class="doubt-muted">読み込み中...</p>';
    apiFetch("/admin/doubt/courses/" + encodeURIComponent(cfState.courseId) + "/observation-targets")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var targets = (data && data.targets) || [];
        if (!targets.length) {
          slot.innerHTML = '<p class="doubt-muted">観測系の主張はこのコーパスの中では見つかりません。</p>';
          return;
        }
        var options = targets.map(function (t) {
          return '<option value="' + escHtml(t.claim_id) + '">' + escHtml(t.label || t.claim_id) + '</option>';
        }).join("");
        slot.innerHTML =
          '<div class="doubt-form">' +
          '<label>観測</label>' +
          '<select data-f="claim_id" data-ui-anchor="doubt-atlas.counterfactual-observation-toggle">' + options + '</select>' +
          '<label><input type="radio" name="doubt-cf-obs-aspect" value="value" checked> ' +
          escHtml(FALSIFICATION_ASPECT_LABELS.value) + '</label>' +
          '<label><input type="radio" name="doubt-cf-obs-aspect" value="systematics"> ' +
          escHtml(FALSIFICATION_ASPECT_LABELS.systematics) + '</label>' +
          '<button type="button" class="doubt-cf-btn" id="doubt-cf-obs-add" ' +
          'data-ui-anchor="doubt-atlas.counterfactual-observation-toggle">仮に倒す</button>' +
          '</div>';
        slot.querySelector("#doubt-cf-obs-add").addEventListener("click", function () {
          var sel = slot.querySelector('[data-f="claim_id"]');
          var claimId = sel.value;
          var label = (sel.options[sel.selectedIndex] && sel.options[sel.selectedIndex].textContent) || claimId;
          var aspect = "value";
          Array.prototype.forEach.call(
            slot.querySelectorAll('input[name="doubt-cf-obs-aspect"]'),
            function (r) { if (r.checked) aspect = r.value; }
          );
          addToggledObservation(claimId, aspect, label, container);
        });
      })
      .catch(function () { slot.innerHTML = '<p class="doubt-muted">読み込みに失敗しました。</p>'; });
  }

  function addToggledObservation(claimId, aspect, label, container) {
    if (!claimId) return;
    var exists = cfState.toggledObservations.some(function (o) {
      return o.claim_id === claimId && o.aspect === aspect;
    });
    if (!exists) cfState.toggledObservations.push({ claim_id: claimId, aspect: aspect, label: label });
    recomputeCounterfactual();
    updateCfStatus(container);
  }

  function removeToggledObservation(index, container) {
    cfState.toggledObservations.splice(index, 1);
    recomputeCounterfactual();
    updateCfStatus(container);
  }

  function updateCfStatus(container) {
    var status = document.getElementById("doubt-cf-status");
    var actions = document.getElementById("doubt-cf-actions");
    var obsList = document.getElementById("doubt-cf-obs-list");
    if (!status || !actions) return;
    if (!cfState.active) {
      status.textContent = "";
      actions.innerHTML = "";
      if (obsList) obsList.innerHTML = "";
      return;
    }
    if (!cfState.toggledIds.length && !cfState.toggledObservations.length) {
      status.textContent = "（トグル中の前提・観測はありません）";
      actions.innerHTML = "";
    } else {
      status.textContent = "仮に偽（前提）: " + cfState.toggledIds.length + " 件";
      actions.innerHTML =
        '<button type="button" class="doubt-cf-btn" id="doubt-cf-clear">すべて解除</button>' +
        '<button type="button" class="doubt-cf-btn" id="doubt-cf-save">セッションを保存</button>';
      var clearBtn = document.getElementById("doubt-cf-clear");
      if (clearBtn) clearBtn.addEventListener("click", function () {
        resetCounterfactual();
        updateCfStatus(container);
      });
      var saveBtn = document.getElementById("doubt-cf-save");
      if (saveBtn) saveBtn.addEventListener("click", function () {
        var notes = window.prompt("メモ（本人の言葉・任意）:", "") || "";
        apiFetch("/admin/doubt/counterfactual/sessions", {
          method: "POST",
          body: JSON.stringify({
            toggled_assumption_ids: cfState.toggledIds,
            toggled_observations: cfState.toggledObservations.map(function (o) {
              return { claim_id: o.claim_id, aspect: o.aspect };
            }),
            course_id: cfState.courseId,
            document_id: cfState.documentId,
            notes: notes,
            shared_scope: "private",
          }),
        }).then(function (res) {
          status.textContent = res.ok ? "保存しました（共有範囲: private）" : "保存に失敗しました";
        });
      });
    }
    if (obsList) {
      if (!cfState.toggledObservations.length) {
        obsList.innerHTML = "";
      } else {
        var html = "";
        cfState.toggledObservations.forEach(function (o, idx) {
          html += '<span class="doubt-badge">' + escHtml(o.label) + '（' +
            escHtml(FALSIFICATION_ASPECT_LABELS[o.aspect] || o.aspect) + '）' +
            ' <button type="button" class="doubt-chip-btn" data-doubt-cf-obs-remove="' + idx +
            '" data-ui-anchor="doubt-atlas.counterfactual-observation-toggle">解除</button></span>';
        });
        obsList.innerHTML = html;
        Array.prototype.forEach.call(obsList.querySelectorAll("[data-doubt-cf-obs-remove]"), function (btn) {
          btn.addEventListener("click", function () {
            removeToggledObservation(Number(btn.getAttribute("data-doubt-cf-obs-remove")), container);
          });
        });
      }
    }
  }

  function toggleCounterfactualNode(nodeId, visNodes) {
    if (!cfState.active) return false;
    cfState.visNodes = visNodes;
    var idx = cfState.toggledIds.indexOf(nodeId);
    if (idx >= 0) cfState.toggledIds.splice(idx, 1);
    else cfState.toggledIds.push(nodeId);

    var bar = document.getElementById("doubt-cf-bar");
    if (bar) updateCfStatus(bar.parentNode || document);

    recomputeCounterfactual();
    return true;
  }

  // 前提トグル（toggledIds）と観測トグル（toggledObservations）の併用可（設計書 §4.3）。
  // 両方空のときのみ非破壊表示に戻す（それ以外は必ず compute を呼ぶ）。
  function recomputeCounterfactual() {
    if (!cfState.toggledIds.length && !cfState.toggledObservations.length) {
      resetCounterfactual();
      return;
    }
    apiFetch("/admin/doubt/counterfactual/compute", {
      method: "POST",
      body: JSON.stringify({
        toggled_assumption_ids: cfState.toggledIds,
        toggled_observations: cfState.toggledObservations.map(function (o) {
          return { claim_id: o.claim_id, aspect: o.aspect };
        }),
        document_id: cfState.documentId,
        course_id: cfState.courseId,
      }),
    })
      .then(function (res) { return res.json(); })
      .then(function (result) { applyCounterfactualStyles(result); })
      .catch(function () { /* 表示は現状維持（非破壊） */ });
  }

  function applyCounterfactualStyles(result) {
    var visNodes = cfState.visNodes;
    if (!visNodes || !result) return;
    cfState.lastResult = result;
    restoreStyles();
    var updates = [];
    function dim(ids, opacity, dashed) {
      (ids || []).forEach(function (id) {
        var current = visNodes.get(id);
        if (!current) return;
        if (!(id in cfState.savedStyles)) {
          cfState.savedStyles[id] = {
            opacity: ("opacity" in current) ? current.opacity : undefined,
            shapeProperties: current.shapeProperties,
          };
        }
        var update = { id: id, opacity: opacity };
        if (dashed) update.shapeProperties = { borderDashes: [4, 4] };
        updates.push(update);
      });
    }
    // 「崩壊」という語は使わない — 表示は「この前提に依存する範囲」（薄色 + 点線）
    dim(result.collapsed_node_ids, 0.25, true);
    dim(result.indeterminate_node_ids, 0.55, true);
    (result.toggled_assumption_ids || []).forEach(function (id) {
      var current = visNodes.get(id);
      if (current) updates.push({ id: id, opacity: 0.35, shapeProperties: { borderDashes: [2, 3] } });
    });
    if (updates.length) visNodes.update(updates);
  }

  function restoreStyles() {
    var visNodes = cfState.visNodes;
    if (!visNodes) return;
    var updates = [];
    Object.keys(cfState.savedStyles).forEach(function (id) {
      var saved = cfState.savedStyles[id];
      var update = { id: id };
      update.opacity = (saved.opacity === undefined) ? 1.0 : saved.opacity;
      update.shapeProperties = saved.shapeProperties || { borderDashes: false };
      updates.push(update);
    });
    if (updates.length) visNodes.update(updates);
  }

  function resetCounterfactual() {
    restoreStyles();
    cfState.savedStyles = {};
    cfState.toggledIds = [];
    cfState.toggledObservations = [];
    cfState.lastResult = null;
  }

  /* =================================================================
     D2-3: 前提の地図タブ + D2-4 候補レビュー + D3-2 未検証合意リスト
     ================================================================= */

  var tabState = { courseId: "", initialized: false };

  function initTab() {
    injectStyles();
    var panel = document.getElementById("tab-doubt-atlas");
    if (!panel) return;
    if (!tabState.initialized) {
      panel.innerHTML =
        '<div class="admin-section">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px">' +
        '<h3 class="admin-section-title" style="margin:0">前提の地図</h3>' +
        '<span class="doubt-muted">※「分野の地図」とは別機能です（負荷度×検証度の事実の投影）</span>' +
        '<select id="doubt-course-select" data-ui-anchor="doubt-atlas.course-select" style="padding:4px 8px;font-size:13px"><option value="">コースを選択...</option></select>' +
        '<button type="button" class="doubt-cf-btn" id="doubt-refresh-btn" data-ui-anchor="doubt-atlas.refresh">再読込</button>' +
        '<button type="button" class="doubt-cf-btn" id="doubt-mining-btn" data-ui-anchor="doubt-atlas.mining-run">前提マイニング実行</button>' +
        '<button type="button" class="doubt-cf-btn" id="doubt-audit-btn" data-ui-anchor="doubt-atlas.audit-run">コーパス監査実行</button>' +
        '<button type="button" class="doubt-cf-btn" id="doubt-load-btn" data-ui-anchor="doubt-atlas.load-recompute">負荷再計算</button>' +
        '</div>' +
        '<div class="doubt-atlas-wrap">' +
        '<div class="doubt-atlas-plot"><div id="doubt-atlas-svg"></div></div>' +
        '<div class="doubt-atlas-detail" id="doubt-atlas-detail">' +
        '<p class="doubt-muted">点をタップすると台帳の詳細を表示します。</p></div>' +
        '</div>' +
        '</div>' +
        '<div class="admin-section"><h3 class="admin-section-title">前提候補のレビュー</h3>' +
        '<div style="margin-bottom:6px"><label class="doubt-muted">表示: </label>' +
        '<select id="doubt-assumption-filter" data-ui-anchor="doubt-atlas.assumption-filter" style="font-size:12px">' +
        '<option value="candidate">候補</option><option value="confirmed">確定済み</option>' +
        '<option value="dismissed">却下済み（保持）</option><option value="all">すべて</option></select></div>' +
        '<div id="doubt-assumption-list"></div></div>' +
        '<div class="admin-section"><h3 class="admin-section-title">未検証合意リスト</h3>' +
        '<p class="doubt-muted">台帳の投影です（編集不可）。スコープの記帳で自動的に増減します。</p>' +
        '<div style="margin-bottom:6px"><label style="font-size:12px;color:#475569">' +
        '<input type="checkbox" id="doubt-open-reachable-filter" ' +
        'data-ui-anchor="doubt-atlas.open-assumptions-reachable-filter"> ' +
        '到達可能な反証条件がある項目だけ表示</label></div>' +
        '<div id="doubt-open-list"></div></div>' +
        '<div class="admin-section"><h3 class="admin-section-title">反実仮想セッション</h3>' +
        '<div id="doubt-cf-sessions"></div></div>';
      bindTabControls();
      tabState.initialized = true;
    }
    loadCourses();
  }

  function bindTabControls() {
    document.getElementById("doubt-course-select").addEventListener("change", function (e) {
      tabState.courseId = e.target.value || "";
      reloadAll();
    });
    document.getElementById("doubt-refresh-btn").addEventListener("click", reloadAll);
    document.getElementById("doubt-mining-btn").addEventListener("click", function () {
      if (!tabState.courseId) return;
      apiFetch("/admin/doubt/courses/" + encodeURIComponent(tabState.courseId) + "/assumption-mining/run",
        { method: "POST", body: JSON.stringify({}) }).then(reloadAll);
    });
    document.getElementById("doubt-audit-btn").addEventListener("click", function () {
      if (!tabState.courseId) return;
      apiFetch("/admin/doubt/courses/" + encodeURIComponent(tabState.courseId) + "/corpus-audit/run",
        { method: "POST", body: JSON.stringify({}) }).then(reloadAll);
    });
    document.getElementById("doubt-load-btn").addEventListener("click", function () {
      if (!tabState.courseId) return;
      apiFetch("/admin/doubt/courses/" + encodeURIComponent(tabState.courseId) + "/load/recompute",
        { method: "POST", body: JSON.stringify({}) }).then(reloadAll);
    });
    document.getElementById("doubt-assumption-filter").addEventListener("change", loadAssumptions);
    // 到達可能フィルタは教員の明示操作（既定 off）。再取得はせず、直近取得済みの一覧を
    // クライアント側で絞り込むだけ（教員の意思確認, 設計書 §9-3）。
    document.getElementById("doubt-open-reachable-filter").addEventListener("change", renderOpenAssumptionsList);
  }

  function loadCourses() {
    apiFetch("/admin/courses")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var courses = (data && (data.courses || data.items)) || data || [];
        var select = document.getElementById("doubt-course-select");
        if (!select) return;
        var current = select.value;
        select.innerHTML = '<option value="">コースを選択...</option>';
        (courses || []).forEach(function (c) {
          var id = c.id || c.course_id;
          var title = c.title || id;
          if (!id) return;
          var opt = document.createElement("option");
          opt.value = id;
          opt.textContent = title;
          select.appendChild(opt);
        });
        if (current) select.value = current;
        if (!tabState.courseId && select.options.length > 1) {
          select.selectedIndex = 1;
          tabState.courseId = select.value;
        }
        reloadAll();
      })
      .catch(function () { /* keep */ });
  }

  function reloadAll() {
    if (!tabState.courseId) return;
    loadAtlas();
    loadAssumptions();
    loadOpenAssumptions();
    loadCfSessions();
  }

  // ── 散布図（SVG・ゾーン名や推奨マークを描かない）────────────────────
  var COVERAGE_ORDER = { none: 0, single: 1, several: 2, broad: 3 };
  var LOAD_ORDER = { low: 0, medium: 1, high: 2, highest: 3 };

  function loadAtlas() {
    apiFetch("/admin/doubt/courses/" + encodeURIComponent(tabState.courseId) + "/assumption-atlas")
      .then(function (res) { return res.json(); })
      .then(function (data) { renderAtlasSvg(data); })
      .catch(function () { /* keep */ });
  }

  function renderAtlasSvg(data) {
    var host = document.getElementById("doubt-atlas-svg");
    if (!host) return;
    var points = (data && data.points) || [];
    var W = 460, H = 340, PAD = 52;
    var cell = { x: (W - PAD * 2) / 3, y: (H - PAD * 2) / 3 };
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" style="max-width:560px;background:#fff;border:1px solid #e2e8f0;border-radius:8px">';
    // 軸（事実記述のみ）
    svg += '<line x1="' + PAD + '" y1="' + (H - PAD) + '" x2="' + (W - PAD / 2) + '" y2="' + (H - PAD) + '" stroke="#cbd5e1"/>';
    svg += '<line x1="' + PAD + '" y1="' + (H - PAD) + '" x2="' + PAD + '" y2="' + (PAD / 2) + '" stroke="#cbd5e1"/>';
    svg += '<text x="' + (W / 2) + '" y="' + (H - 12) + '" font-size="11" fill="#64748b" text-anchor="middle">検証スコープの被覆</text>';
    svg += '<text x="14" y="' + (H / 2) + '" font-size="11" fill="#64748b" text-anchor="middle" transform="rotate(-90 14 ' + (H / 2) + ')">依存の広がり</text>';
    ["none", "single", "several", "broad"].forEach(function (k, i) {
      svg += '<text x="' + (PAD + cell.x * i) + '" y="' + (H - PAD + 16) + '" font-size="10" fill="#94a3b8" text-anchor="middle">' + escHtml(COVERAGE_LABELS[k]) + '</text>';
    });
    ["low", "medium", "high", "highest"].forEach(function (k, i) {
      svg += '<text x="' + (PAD - 8) + '" y="' + (H - PAD - cell.y * i + 4) + '" font-size="10" fill="#94a3b8" text-anchor="end">' + escHtml(LOAD_LABELS[k]) + '</text>';
    });
    points.forEach(function (p, idx) {
      var cx = PAD + cell.x * (COVERAGE_ORDER[p.scope_coverage] || 0);
      var cy = (H - PAD) - cell.y * (LOAD_ORDER[p.load_level] || 0);
      // 同セル内の重なりは決定論的にずらす（index 由来・乱数を使わない）
      cx += ((idx * 29) % 26) - 13;
      cy -= ((idx * 17) % 20) - 10;
      var fill = p.unscoped ? "none" : "#64748b";
      var dash = p.unscoped ? ' stroke-dasharray="3,3"' : "";
      var stroke = p.target_type === "assumption" ? "#7c3aed" : "#64748b";
      svg += '<circle class="doubt-svg-point" data-doubt-point="' + idx + '" data-ui-anchor="doubt-atlas.plot-detail" cx="' + cx + '" cy="' + cy +
        '" r="7" fill="' + fill + '" fill-opacity="0.55" stroke="' + stroke + '"' + dash + '>' +
        '<title>' + escHtml(p.label || p.target_id) + '</title></circle>';
      if (p.has_naive_signal) {
        svg += '<circle cx="' + cx + '" cy="' + cy + '" r="11" fill="none" stroke="#a5b4fc" stroke-width="1"/>';
      }
    });
    svg += '</svg>';
    host.innerHTML = svg;
    Array.prototype.forEach.call(host.querySelectorAll("[data-doubt-point]"), function (el) {
      el.addEventListener("click", function () {
        var p = points[Number(el.getAttribute("data-doubt-point"))];
        if (!p) return;
        var detail = document.getElementById("doubt-atlas-detail");
        detail.innerHTML = '<h5 style="font-size:13px;margin:0 0 6px">' + escHtml(p.label || p.target_id) + '</h5>';
        // 地図専用の別詳細は作らない — 既存の台帳セクション描画を再利用
        renderNodeLedgerSection(detail, p.target_type, p.target_id);
      });
    });
  }

  // ── D2-4: 前提候補レビュー ───────────────────────────────────────────
  function loadAssumptions() {
    var filter = document.getElementById("doubt-assumption-filter");
    var status = filter ? filter.value : "candidate";
    apiFetch("/admin/doubt/courses/" + encodeURIComponent(tabState.courseId) +
      "/assumptions?status=" + encodeURIComponent(status))
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var host = document.getElementById("doubt-assumption-list");
        if (!host) return;
        var items = (data && data.items) || [];
        if (!items.length) {
          host.innerHTML = '<p class="doubt-muted">該当する前提はありません。</p>';
          return;
        }
        var html = "";
        items.forEach(function (a) {
          html += '<div class="doubt-card">' +
            '<h5>' + escHtml(a.statement) + '</h5>' +
            '<div><span class="doubt-badge">' + escHtml(a.origin) + '</span>' +
            '<span class="doubt-badge">' + escHtml(a.status) + '</span>' +
            (a.has_naive_signal ? '<span class="doubt-badge">複数の学習者がここで問いを立てています</span>' : '') +
            '</div>';
          if (a.evidence_quote) {
            html += '<p class="doubt-fact">出所（逐語）: 「' + escHtml(a.evidence_quote) + '」</p>';
          }
          var cf = a.created_from || {};
          if ((cf.document_ids || []).length) {
            html += '<p class="doubt-muted">出典ドキュメント: ' + escHtml((cf.document_ids || []).join(", ")) + '</p>';
          }
          if (a.status === "candidate") {
            html += '<div>' +
              '<button type="button" class="doubt-chip-btn" data-doubt-assumption-confirm="' + escHtml(a.id) + '" data-ui-anchor="doubt-atlas.assumption-confirm">確定</button>' +
              '<button type="button" class="doubt-chip-btn" data-doubt-assumption-edit="' + escHtml(a.id) + '" data-ui-anchor="doubt-atlas.assumption-confirm">文面を訂正して確定</button>' +
              '<button type="button" class="doubt-chip-btn" data-doubt-assumption-dismiss="' + escHtml(a.id) + '" data-ui-anchor="doubt-atlas.assumption-dismiss">却下</button>' +
              '</div>';
          }
          html += '</div>';
        });
        host.innerHTML = html;
        bindAssumptionButtons(host, items);
      })
      .catch(function () { /* keep */ });
  }

  function bindAssumptionButtons(host, items) {
    function statementOf(id) {
      for (var i = 0; i < items.length; i++) if (items[i].id === id) return items[i].statement;
      return "";
    }
    Array.prototype.forEach.call(host.querySelectorAll("[data-doubt-assumption-confirm]"), function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-doubt-assumption-confirm");
        var reason = window.prompt("確定の理由（既定文の選択に相当・追記可）:", "検出根拠を確認し、暗黙の前提として妥当と判断した") || "";
        if (!reason.trim()) return;
        apiFetch("/admin/doubt/assumptions/" + encodeURIComponent(id) + "/confirm",
          { method: "POST", body: JSON.stringify({ reason: reason }) }).then(reloadAll);
      });
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-doubt-assumption-edit]"), function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-doubt-assumption-edit");
        var statement = window.prompt("前提文を訂正:", statementOf(id)) || "";
        if (!statement.trim()) return;
        var reason = window.prompt("確定の理由:", "文面を訂正のうえ暗黙の前提として妥当と判断した") || "";
        if (!reason.trim()) return;
        apiFetch("/admin/doubt/assumptions/" + encodeURIComponent(id) + "/confirm",
          { method: "POST", body: JSON.stringify({ statement: statement, reason: reason }) }).then(reloadAll);
      });
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-doubt-assumption-dismiss]"), function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-doubt-assumption-dismiss");
        apiFetch("/admin/doubt/assumptions/" + encodeURIComponent(id) + "/dismiss",
          { method: "POST", body: JSON.stringify({}) }).then(reloadAll);
      });
    });
  }

  // ── D3-2 / SL-4: 未検証合意リスト ──────────────────────────────────────
  // 取得結果はキャッシュし、到達可能フィルタの切替は再取得せずクライアント側で絞り込む
  // （教員の明示操作のみで結果が動く。ポーリング・自動再取得はしない）。
  var lastOpenAssumptionItems = [];

  function loadOpenAssumptions() {
    apiFetch("/admin/doubt/courses/" + encodeURIComponent(tabState.courseId) + "/open-assumptions")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        lastOpenAssumptionItems = (data && data.items) || [];
        renderOpenAssumptionsList();
      })
      .catch(function () { /* keep */ });
  }

  function renderOpenAssumptionsList() {
    var host = document.getElementById("doubt-open-list");
    if (!host) return;
    var filterCb = document.getElementById("doubt-open-reachable-filter");
    var onlyReachable = !!(filterCb && filterCb.checked);
    var items = lastOpenAssumptionItems;
    if (onlyReachable) {
      items = items.filter(function (it) { return it.reachability_summary === "reachable"; });
    }
    if (!items.length) {
      host.innerHTML = '<p class="doubt-muted">高負荷 × 低検証の対象は現在ありません。</p>';
      return;
    }
    var html = "";
    items.forEach(function (item, idx) {
      html += '<div class="doubt-list-row" data-doubt-open="' + idx + '" data-ui-anchor="doubt-atlas.open-assumptions-row">' +
        '<div>' + escHtml(item.statement) + '</div>' +
        '<div style="margin-top:3px">' +
        '<span class="doubt-badge">依存の広がり: ' + escHtml(LOAD_LABELS[item.load_level] || item.load_level) + '</span>' +
        '<span class="doubt-badge">検証: ' + escHtml(STATUS_LABELS[item.verification_status] || item.verification_status) +
        (item.scope_count_is_zero ? '（スコープの記帳なし）' : '') + '</span>' +
        (item.challenge_count_label ? '<span class="doubt-badge">疑義: ' + escHtml(item.challenge_count_label) + '</span>' : '') +
        (item.has_verification_proposal ? '<span class="doubt-badge">検証提案あり</span>' : '') +
        (item.has_naive_signal ? '<span class="doubt-badge">学習者の問いあり</span>' : '') +
        // SL-1/SL-3: 覆る条件・到達可能性・支持線（すべて事実の段階表示。数値は出さない）。
        (item.has_falsification_condition
          ? '<span class="doubt-badge">覆る条件: あり</span>'
          : '<span class="doubt-muted" style="display:inline-block;margin-right:6px">覆る条件: なし</span>') +
        (item.reachability_summary
          ? '<span class="doubt-badge">到達可能性: ' + escHtml(REACHABILITY_LABELS[item.reachability_summary] || item.reachability_summary) + '</span>'
          : '<span class="doubt-muted" style="display:inline-block;margin-right:6px">到達可能性: 記録なし</span>') +
        (item.support_line_level
          ? '<span class="doubt-badge">支持: ' + escHtml(SUPPORT_LEVEL_BADGE_LABELS[item.support_line_level] || item.support_line_level) + '</span>'
          : '<span class="doubt-muted" style="display:inline-block;margin-right:6px">支持: 未算出</span>') +
        '</div></div>';
    });
    host.innerHTML = html;
    Array.prototype.forEach.call(host.querySelectorAll("[data-doubt-open]"), function (row) {
      row.addEventListener("click", function () {
        var item = items[Number(row.getAttribute("data-doubt-open"))];
        if (!item) return;
        var detail = document.getElementById("doubt-atlas-detail");
        detail.innerHTML = '<h5 style="font-size:13px;margin:0 0 6px">' + escHtml(item.statement) + '</h5>';
        renderNodeLedgerSection(detail, item.target_type, item.target_id);
      });
    });
  }

  // ── D3-4: 保存済み反実仮想セッション一覧 ──────────────────────────────
  function loadCfSessions() {
    apiFetch("/admin/doubt/counterfactual/sessions?course_id=" + encodeURIComponent(tabState.courseId))
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var host = document.getElementById("doubt-cf-sessions");
        if (!host) return;
        var items = (data && data.items) || [];
        if (!items.length) {
          host.innerHTML = '<p class="doubt-muted">保存済みセッションはありません。</p>';
          return;
        }
        var html = "";
        items.forEach(function (s) {
          var collapsed = (s.collapsed_subgraph && s.collapsed_subgraph.node_ids) || [];
          var indeterminate = (s.indeterminate_subgraph && s.indeterminate_subgraph.node_ids) || [];
          html += '<div class="doubt-card">' +
            '<div><span class="doubt-badge">' + escHtml(s.shared_scope) + '</span>' +
            '<span class="doubt-badge">作成: ' + escHtml(s.owner_name || "") + '</span></div>' +
            (s.notes ? '<p class="doubt-fact">' + escHtml(s.notes) + '</p>' : '') +
            '<p class="doubt-muted">この前提に依存する範囲: ' + collapsed.length + ' ノード / 両者に依存（判定不能）: ' +
            indeterminate.length + ' ノード</p>' +
            '</div>';
        });
        host.innerHTML = html;
      })
      .catch(function () { /* keep */ });
  }

  // ── 公開 API ─────────────────────────────────────────────────────────
  window.DoubtAtlas = {
    renderNodeLedgerSection: renderNodeLedgerSection,
    initTab: initTab,
    counterfactualToolbarHtml: counterfactualToolbarHtml,
    bindCounterfactualToolbar: bindCounterfactualToolbar,
    isCounterfactualActive: isCounterfactualActive,
    toggleCounterfactualNode: toggleCounterfactualNode,
    resetCounterfactual: resetCounterfactual,
  };
})();
