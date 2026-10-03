// 動かして確かめる（検証シミュレーション v1.1 — 主語は学習者）学習画面パネル。
//
// 設計正本: docs/features/simulation_bridge_design.md §14（SB11〜SB15）。
// - SB11 試行に承認ゲートを置かない: 生成・編集・配線・実行は本人の操作だけで完結する。
//   「採用」は本人がこの版を使うと決める操作であって、教員の承認ではない。
// - SB12 所有は本人・可視は本人: この画面の記録はあなたにだけ表示される。
// - SB13 共有は本人の 1 操作: 「共有する」を押したときだけ教員に見え、台帳の候補になる。
// - SB14 予想は並置・採点しない: 予想の列には判定の語・色を付けない（UC2）。
//   段階ラベルが付くのは理論の出力と模擬観測の間だけで、文言はサーバ（result.labels）が正本。
// - 自動では開かない・ポーリングしない・督促しない（原則 12）。
//
// window.SimulationLab = { init, open, close, refresh, setContext }
(function () {
  "use strict";

  const NOT_VERIFICATION = "シミュレーションの一致は検証ではなく、分析の妥当性の候補にとどまります。";
  const OWN_ONLY = "この記録はあなたにだけ表示されます。成績評価には使用されません。";
  const SHARE_CONFIRM = "この実行を教員に共有し、関係する主張の台帳に候補として記帳します。確定は教員が行います。";
  const SANDBOX_OFF = "この環境ではコードを実行する場所が用意されていません。コードの生成と編集はできますが、実行はできません。";
  const GENERIC_ERROR = "処理を完了できませんでした。時間をおいてもう一度試してください。";
  const CANDIDATE_WIRING_NOTE = "この提案は参考表示です";
  const PROGRAM_STATUS_LABELS = {
    candidate: "候補",
    confirmed: "採用済み",
    dismissed: "見送り",
    superseded: "旧版",
  };
  const RUN_STATUS_LABELS = {
    succeeded: "実行できました",
    failed: "実行は完了しませんでした",
    rejected: "実行しませんでした",
  };
  const METRIC_LABELS = {
    relative_difference: "理論の出力と模擬観測の相対差",
    separation_ratio: "差と模擬観測のばらつきの比",
    reference_relative_difference: "理論の出力と論文の記載値の相対差",
  };

  let deps = {
    apiFetch: null,
    getCourseId: () => "",
    getTopicId: () => "",
    getDocumentId: () => null,
    escHtml: null,
  };
  let region = null;
  let button = null;
  const st = {
    open: false,
    loading: false,
    overview: null,
    loadError: "",
    moduleKey: "",
    programId: "",
    wiringId: "",
    builder: [],
    builderName: "",
    busy: {},
    notice: {},
    selectedRunId: "",
    proposal: null,
    historyOpen: false,
    chosenDocumentId: "",
    seq: 0,
  };

  // ---- 小道具 -----------------------------------------------------------------
  function esc(s) {
    if (typeof deps.escHtml === "function") return deps.escHtml(s == null ? "" : String(s));
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  const arr = (v) => (Array.isArray(v) ? v : []);
  const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
  const str = (v) => (v == null ? "" : String(v));
  function fmt(v) {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "number") {
      if (!isFinite(v)) return String(v);
      const a = Math.abs(v);
      if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(4);
      return String(Math.round(v * 1e6) / 1e6);
    }
    return String(v);
  }
  function factsHtml(list) {
    const items = arr(list).filter((f) => typeof f === "string" && f);
    if (!items.length) return "";
    return '<ul class="sim-lab-facts">' + items.map((f) => "<li>" + esc(f) + "</li>").join("") + "</ul>";
  }
  function chip(text, cls) {
    if (!text) return "";
    return '<span class="sim-lab-chip' + (cls ? " " + cls : "") + '">' + esc(text) + "</span>";
  }
  function symOf(item) {
    const o = obj(item);
    return str(o.symbol || o.name);
  }
  function ctxIds() {
    return {
      courseId: str(deps.getCourseId && deps.getCourseId()),
      topicId: str(deps.getTopicId && deps.getTopicId()),
      documentId: st.chosenDocumentId || str(deps.getDocumentId && deps.getDocumentId()),
    };
  }
  function docId() {
    const ov = obj(st.overview);
    return str(ov.document_id) || ctxIds().documentId;
  }

  async function request(path, opts) {
    if (typeof deps.apiFetch !== "function") throw new Error(GENERIC_ERROR);
    const res = await deps.apiFetch(path, opts || {});
    let body = null;
    try { body = await res.json(); } catch (_) { body = null; }
    if (!res.ok) {
      const detail = body && typeof body.detail === "string" && body.detail ? body.detail : "";
      const err = new Error(detail && res.status >= 400 && res.status < 500 ? detail : GENERIC_ERROR);
      err.status = res.status;
      throw err;
    }
    return obj(body);
  }
  function post(path, payload) {
    return request(path, { method: "POST", body: JSON.stringify(payload || {}) });
  }

  // ---- データの読み ------------------------------------------------------------
  const ov = () => obj(st.overview);
  const modules = () => arr(ov().modules);
  const programs = () => arr(ov().programs);
  const wirings = () => arr(ov().wirings);
  const runs = () => arr(ov().runs).slice().sort((a, b) => str(obj(b).created_at).localeCompare(str(obj(a).created_at)));

  function currentModule() {
    return modules().find((m) => str(obj(m).module_key) === st.moduleKey) || null;
  }
  function programForModule(mod) {
    if (!mod) return null;
    if (st.programId) {
      const hit = programs().find((p) => str(obj(p).id) === st.programId) ||
        (obj(mod.program).id && str(mod.program.id) === st.programId ? mod.program : null);
      if (hit && str(obj(hit).module_key || mod.module_key) === str(mod.module_key)) return hit;
    }
    if (mod.program && typeof mod.program === "object") return mod.program;
    const live = programs().filter((p) => str(obj(p).module_key) === str(mod.module_key) &&
      ["candidate", "confirmed"].indexOf(str(obj(p).review_status)) >= 0);
    return live.length ? live[live.length - 1] : null;
  }
  function currentProgram() { return programForModule(currentModule()); }

  function upsert(list, item) {
    const id = str(obj(item).id);
    const out = arr(list).filter((x) => str(obj(x).id) !== id);
    out.push(item);
    return out;
  }
  function replaceProgram(program) {
    if (!program) return;
    const o = obj(st.overview);
    o.programs = upsert(arr(o.programs).filter((p) => !(str(obj(p).module_key) === str(program.module_key) && str(obj(p).id) !== str(program.id) && str(obj(p).review_status) === "superseded")), program);
    o.modules = arr(o.modules).map((m) => (str(obj(m).module_key) === str(program.module_key) ? Object.assign({}, m, { program }) : m));
    st.overview = o;
    st.programId = str(program.id);
  }

  // ---- 開閉・文脈 --------------------------------------------------------------
  function updateButton() {
    if (!button) return;
    const c = ctxIds();
    button.hidden = !(c.courseId && c.topicId && c.topicId !== "_discussion");
    button.setAttribute("aria-pressed", st.open ? "true" : "false");
  }
  function applyOpenClass() {
    const host = document.getElementById("material-region");
    if (host) host.classList.toggle("simulation-lab-open", !!st.open);
  }

  function resetData() {
    st.overview = null; st.chosenDocumentId = "";
    st.loadError = "";
    st.moduleKey = "";
    st.programId = "";
    st.wiringId = "";
    st.builder = [];
    st.builderName = "";
    st.busy = {};
    st.notice = {};
    st.selectedRunId = "";
    st.proposal = null;
    st.historyOpen = false;
  }

  let lastCtxKey = "";
  function setContext() {
    // トピックが変わったら前の文脈の結果を持ち越さない（自動では開かない）。
    // 同じコース・トピックのままの呼び出しはボタンの表示だけを合わせる（何度呼ばれても閉じない）。
    const c = ctxIds();
    const key = c.courseId + "|" + c.topicId;
    if (key === lastCtxKey) { updateButton(); return; }
    lastCtxKey = key;
    st.seq += 1;
    resetData();
    if (st.open) close();
    updateButton();
  }

  function open() {
    const c = ctxIds();
    if (!region || !c.courseId || !c.topicId) return;
    st.open = true;
    region.hidden = false;
    applyOpenClass();
    updateButton();
    if (!st.overview) refresh();
    else render();
  }

  function close() {
    st.open = false;
    if (region) { region.hidden = true; region.innerHTML = ""; }
    applyOpenClass();
    updateButton();
  }

  async function refresh() {
    const c = ctxIds();
    if (!c.courseId) return;
    const seq = ++st.seq;
    st.loading = true;
    st.loadError = "";
    render();
    const qs = [];
    if (c.documentId) qs.push("document_id=" + encodeURIComponent(c.documentId));
    if (c.topicId) qs.push("topic_id=" + encodeURIComponent(c.topicId));
    try {
      const data = await request("/learning/courses/" + encodeURIComponent(c.courseId) +
        "/simulation/overview" + (qs.length ? "?" + qs.join("&") : ""));
      if (seq !== st.seq) return;
      st.overview = data;
      const docs = arr(obj(data).documents);
      if (!str(obj(data).document_id) && docs.length === 1 && str(obj(docs[0]).document_id) && !st.chosenDocumentId) {
        st.chosenDocumentId = str(docs[0].document_id);
        st.loading = false;
        return refresh();
      }
      if (st.moduleKey && !currentModule()) { st.moduleKey = ""; st.programId = ""; }
    } catch (e) {
      if (seq !== st.seq) return;
      st.loadError = (e && e.message) || GENERIC_ERROR;
    } finally {
      if (seq === st.seq) {
        st.loading = false;
        render();
      }
    }
  }

  // ---- 描画 --------------------------------------------------------------------
  // 再描画で本人が打ちかけた値を消さない（入力欄の値だけを控えて戻す）。
  function fieldKey(el) {
    const attrs = ["id", "data-sl-predict", "data-sl-value", "data-sl-param-run", "data-sl-ref",
      "data-sl-ref-field", "data-sl-claim", "data-sl-builder", "data-sl-param", "data-sl-program-id"];
    return attrs.map((a) => (el.hasAttribute(a) ? a + "=" + el.getAttribute(a) : "")).join("|");
  }
  function snapshotFields() {
    const snap = {};
    if (!region) return snap;
    region.querySelectorAll("input, textarea, select").forEach((el) => {
      snap[fieldKey(el)] = el.type === "checkbox" ? { checked: el.checked } : { value: el.value };
    });
    return snap;
  }
  function restoreFields(snap) {
    region.querySelectorAll("input, textarea, select").forEach((el) => {
      const v = snap[fieldKey(el)];
      if (!v) return;
      if (el.type === "checkbox") el.checked = !!v.checked;
      else el.value = v.value;
    });
  }

  function render() {
    if (!region || !st.open) return;
    const snap = snapshotFields();
    renderInto();
    restoreFields(snap);
  }

  function renderInto() {
    let html = '<div class="sim-lab-card" data-ui-anchor="material.simulation-lab">' +
      '<div class="sim-lab-head"><span class="sim-lab-title">🧪 動かして確かめる</span>' +
      '<button type="button" class="sim-lab-close" data-sl-action="close" title="閉じる">&times;</button></div>' +
      '<p class="sim-lab-lead">' + esc(NOT_VERIFICATION) + "</p>" +
      '<p class="sim-lab-lead sim-lab-muted">' + esc(OWN_ONLY) + "</p>";
    if (st.loading && !st.overview) {
      html += '<p class="sim-lab-muted">読み込み中…</p></div>';
      region.innerHTML = html;
      return;
    }
    if (st.loadError) {
      html += '<p class="sim-lab-fact">' + esc(st.loadError) + "</p></div>";
      region.innerHTML = html;
      return;
    }
    const o = ov();
    const docList = arr(o.documents).filter((d) => str(obj(d).document_id));
    if (docList.length > 1 || (!str(o.document_id) && docList.length)) {
      html += '<div class="sim-lab-row"><label>動かす論文 <select class="sim-lab-select" data-sl-change="choose-doc">' +
        (!str(o.document_id) ? '<option value="">選んでください</option>' : '') +
        docList.map((d) => '<option value="' + esc(d.document_id) + '"' + (str(d.document_id) === str(o.document_id) ? ' selected' : '') + '>' + esc(d.title || d.document_id) + '</option>').join('') +
        '</select></label></div>';
    } else if (o.document_title) html += '<p class="sim-lab-doc">論文: ' + esc(o.document_title) + "</p>";
    if (o.sandbox_configured === false) html += '<p class="sim-lab-fact">' + esc(SANDBOX_OFF) + "</p>";
    html += factsHtml(o.facts);
    if (o.available === false || !modules().length) {
      if (!arr(o.facts).length) html += '<p class="sim-lab-fact">この論文には、動かせる理論モジュールが見つかっていません。</p>';
      html += "</div>";
      region.innerHTML = html;
      return;
    }
    html += '<ol class="sim-lab-steps">' +
      renderModulesStep() + renderCodeStep() + renderWiringStep() + renderPredictStep() + renderRunStep() +
      "</ol></div>";
    region.innerHTML = html;
  }

  function stepHead(n, title) {
    return '<div class="sim-lab-step-head"><span class="sim-lab-step-no">' + n + "</span> " + esc(title) + "</div>";
  }
  function notice(key) {
    return st.notice[key] ? '<p class="sim-lab-fact">' + esc(st.notice[key]) + "</p>" : "";
  }
  function eqList(list) {
    const labels = arr(list).map((e) => str(obj(e).display_label)).filter(Boolean);
    return labels.length ? labels.map(esc).join("、") : "—";
  }

  function renderModulesStep() {
    const list = modules().slice().sort((a, b) => {
      const la = str(obj(a).level) === "inner" ? 1 : 0;
      const lb = str(obj(b).level) === "inner" ? 1 : 0;
      return la - lb;
    });
    const items = list.map((m) => {
      const mod = obj(m);
      const key = str(mod.module_key);
      const inner = str(mod.level) === "inner";
      const can = mod.has_equation_text !== false;
      const active = key === st.moduleKey;
      const assumptions = arr(mod.assumptions).map((a) => {
        const ao = obj(a);
        return esc(ao.text) + (ao.origin_label ? "（" + esc(ao.origin_label) + "）" : "");
      }).filter(Boolean);
      return '<li class="sim-lab-module' + (inner ? " is-inner" : "") + (active ? " is-active" : "") + '">' +
        '<button type="button" class="sim-lab-module-btn" data-sl-action="select-module" data-module-key="' + esc(key) + '"' +
        (can ? "" : " disabled") + ">" + esc(mod.visual_label || mod.label || "理論モジュール") + "</button>" +
        (mod.theory_object ? '<div class="sim-lab-muted">対象: ' + esc(mod.theory_object) + "</div>" : "") +
        '<div class="sim-lab-io">' + eqList(mod.inputs) + " → " + eqList(mod.outputs) + "</div>" +
        (assumptions.length ? '<div class="sim-lab-muted">前提: ' + assumptions.join(" / ") + "</div>" : "") +
        (can ? "" : '<div class="sim-lab-fact">このモジュールには式の本文が引けないため、コードを作れません。</div>') +
        (mod.reconstructed_inputs ? '<div class="sim-lab-muted">入力の式に PDF から復元された式が含まれています。</div>' : "") +
        "</li>";
    }).join("");
    return '<li class="sim-lab-step">' + stepHead("①", "理論モジュールを選ぶ") +
      '<ul class="sim-lab-modules">' + items + "</ul></li>";
  }

  function renderCodeStep() {
    const mod = currentModule();
    let body = "";
    if (!mod) {
      body = '<p class="sim-lab-muted">先に①で理論モジュールを選んでください。</p>';
    } else {
      const program = currentProgram();
      const generating = !!st.busy.generate;
      body += '<div class="sim-lab-row"><button type="button" class="sim-lab-btn" data-sl-action="generate"' +
        (generating ? " disabled" : "") + ">" + (generating ? "生成中…" : (program ? "作り直す" : "生成する")) + "</button>" +
        '<span class="sim-lab-muted">AI がこのモジュールの式からコードの下書きを作ります。自分で書き直せます。</span></div>';
      body += notice("generate");
      if (program) body += renderProgramCard(program);
    }
    return '<li class="sim-lab-step">' + stepHead("②", "コード（生成する・自分で書く・採用する）") + body + "</li>";
  }

  function renderProgramCard(program) {
    const p = obj(program);
    const status = str(p.review_status);
    const prov = obj(p.provenance);
    const astOk = prov.ast_ok !== false;
    const reasons = arr(prov.ast_reasons).filter(Boolean);
    const facts = [].concat(arr(prov.facts), arr(p.facts));
    const saving = !!st.busy.edit;
    const adopting = !!st.busy.adopt;
    let html = '<div class="sim-lab-program">' +
      '<div class="sim-lab-card-head">コード ' + chip(PROGRAM_STATUS_LABELS[status] || status, "sim-lab-status-" + esc(status)) + "</div>" +
      factsHtml(facts) +
      '<textarea class="sim-lab-code" id="sim-lab-code" data-sl-program-id="' + esc(p.id) + '" spellcheck="false" rows="12">' + esc(p.source_code) + "</textarea>" +
      '<div class="sim-lab-row">' +
      '<button type="button" class="sim-lab-btn" data-sl-action="edit"' + (saving ? " disabled" : "") + ">" + (saving ? "保存中…" : "この内容で保存") + "</button>" +
      '<button type="button" class="sim-lab-btn sim-lab-primary" data-sl-action="adopt"' +
      (astOk && status !== "confirmed" && !adopting ? "" : " disabled") + ">" +
      (status === "confirmed" ? "採用済み" : "採用する") + "</button></div>";
    if (!astOk) {
      html += '<p class="sim-lab-fact">許可されていない操作が含まれているため、採用・実行できません。</p>' +
        (reasons.length ? '<ul class="sim-lab-facts">' + reasons.map((r) => "<li>" + esc(r) + "</li>").join("") + "</ul>" : "");
    } else if (status !== "confirmed") {
      html += '<p class="sim-lab-muted">採用は「この版を使う」とあなたが決める操作です。採用した版で実行できます。</p>';
    }
    html += notice("program");
    return html + "</div>";
  }

  function renderWiringStep() {
    const own = wirings();
    const parts = arr(ov().parts_catalog);
    let html = '<li class="sim-lab-step">' + stepHead("③", "実験機の配線（任意）");
    if (own.length) {
      html += '<ul class="sim-lab-wirings">' + own.map((w) => {
        const wo = obj(w);
        const cand = str(wo.origin) === "llm_candidate";
        const chain = arr(wo.parts).map((pt) => esc(obj(pt).name || obj(pt).entry_type || "部品")).join(" → ");
        return '<li class="sim-lab-wiring' + (cand ? " is-candidate" : "") + '">' +
          (cand ? chip("AI の提案（未確認）", "sim-lab-chip-candidate") + " " : "") +
          "<strong>" + esc(wo.name || "配線") + "</strong> " + '<span class="sim-lab-muted">' + chain + "</span>" +
          (wo.revision_note ? '<div class="sim-lab-muted">' + esc(wo.revision_note) + "</div>" : "") +
          (cand ? '<div class="sim-lab-muted">' + esc(CANDIDATE_WIRING_NOTE) + "</div>" : "") +
          "</li>";
      }).join("") + "</ul>";
    }
    if (!parts.length) {
      html += '<p class="sim-lab-muted">使える部品がまだありません。配線なしでも実行できます（模擬観測は理論の出力そのものになります）。</p>';
      return html + "</li>";
    }
    html += '<details class="sim-lab-builder"' + (st.builder.length ? " open" : "") + "><summary>部品を順につないで配線を作る</summary>";
    html += '<div class="sim-lab-row"><select id="sim-lab-part-select">' + parts.map((pt, i) => {
      const po = obj(pt);
      return '<option value="' + i + '">' + esc(po.name) + (po.entry_type_label ? "（" + esc(po.entry_type_label) + "）" : "") + "</option>";
    }).join("") + '</select> <button type="button" class="sim-lab-btn" data-sl-action="add-part">部品を足す</button></div>';
    if (st.builder.length) {
      html += '<ol class="sim-lab-chain">' + st.builder.map((b, idx) => {
        const pt = obj(b.part);
        const params = arr(pt.params).map((pr) => {
          const po = obj(pr);
          const name = str(po.name);
          const val = Object.prototype.hasOwnProperty.call(b.values, name) ? b.values[name] : po.default;
          return '<label class="sim-lab-param">' + esc(name) + (po.unit ? " [" + esc(po.unit) + "]" : "") +
            ' <input type="number" step="any" data-sl-builder="' + idx + '" data-sl-param="' + esc(name) + '" value="' + esc(val == null ? "" : val) + '"></label>';
        }).join("");
        return '<li><strong>' + esc(pt.name) + "</strong>" + (pt.summary ? ' <span class="sim-lab-muted">' + esc(pt.summary) + "</span>" : "") +
          ' <button type="button" class="sim-lab-link" data-sl-action="remove-part" data-index="' + idx + '">外す</button>' +
          (params ? '<div class="sim-lab-params">' + params + "</div>" : "") + "</li>";
      }).join("") + "</ol>";
      html += '<div class="sim-lab-row"><label>配線の名前 <input type="text" id="sim-lab-wiring-name" value="' + esc(st.builderName) + '" placeholder="例: 検出器つき"></label> ' +
        '<button type="button" class="sim-lab-btn" data-sl-action="save-wiring"' + (st.busy.wiring ? " disabled" : "") + ">配線を保存</button></div>";
    }
    html += notice("wiring") + "</details>";
    return html + "</li>";
  }

  function contractOf(program) {
    return obj(obj(program).io_contract);
  }

  function renderPredictStep() {
    const program = currentProgram();
    let html = '<li class="sim-lab-step">' + stepHead("④", "予想を書いてから条件を決める");
    if (!program) return html + '<p class="sim-lab-muted">②でコードを用意すると、ここに入力欄が出ます。</p></li>';
    const io = contractOf(program);
    const outs = arr(io.outputs);
    if (outs.length) {
      html += '<p class="sim-lab-muted">実行する前に、出力がいくつになると思うかを書けます（任意）。予想は採点されません。</p>' +
        '<div class="sim-lab-params">' + outs.map((o) => {
          const s = symOf(o);
          return '<label class="sim-lab-param">あなたの予想 <code>' + esc(s) + "</code>" +
            (obj(o).unit ? " [" + esc(obj(o).unit) + "]" : "") +
            ' <input type="number" step="any" data-sl-predict="' + esc(s) + '"></label>';
        }).join("") + "</div>";
    }
    const numberInputs = (list, attr) => arr(list).map((it) => {
      const io2 = obj(it);
      const s = symOf(io2);
      if (!s) return "";
      // 入力（data-sl-value）はベクトルも受ける（カンマ区切り）。パラメータは数値 1 つ。
      const vector = attr === "data-sl-value";
      const input = vector
        ? ' <input type="text" inputmode="decimal" placeholder="数値（ベクトルはカンマ区切り）" '
        : ' <input type="number" step="any" ';
      return '<label class="sim-lab-param"><code>' + esc(s) + "</code>" + (io2.unit ? " [" + esc(io2.unit) + "]" : "") +
        input + attr + '="' + esc(s) + '" value="' + esc(io2.default == null ? "" : io2.default) + '"></label>';
    }).join("");
    const ins = numberInputs(io.inputs, "data-sl-value");
    const pars = numberInputs(io.parameters, "data-sl-param-run");
    if (ins) html += '<div class="sim-lab-sub">入力</div><div class="sim-lab-params">' + ins + "</div>";
    if (pars) html += '<div class="sim-lab-sub">パラメータ</div><div class="sim-lab-params">' + pars + "</div>";
    if (outs.length) {
      html += '<details class="sim-lab-ref"><summary>論文の記載値と並べる（任意）</summary><div class="sim-lab-params">' +
        outs.map((o) => {
          const s = symOf(o);
          return '<div class="sim-lab-ref-row"><code>' + esc(s) + "</code>" +
            ' <label>値 <input type="number" step="any" data-sl-ref="' + esc(s) + '" data-sl-ref-field="value"></label>' +
            ' <label>単位 <input type="text" data-sl-ref="' + esc(s) + '" data-sl-ref-field="unit" value="' + esc(obj(o).unit || "") + '"></label>' +
            ' <label>出典の覚え書き <input type="text" data-sl-ref="' + esc(s) + '" data-sl-ref-field="source_note"></label></div>';
        }).join("") + "</div></details>";
    }
    const targets = arr(ov().observation_targets);
    if (targets.length) {
      html += '<div class="sim-lab-sub">関係する主張（観測）</div><div class="sim-lab-claims">' + targets.map((t) => {
        const to = obj(t);
        return '<label class="sim-lab-claim"><input type="checkbox" data-sl-claim="' + esc(to.claim_id) + '"> ' + esc(to.label) + "</label>";
      }).join("") + "</div>";
    }
    html += '<label class="sim-lab-param">乱数の種（任意） <input type="number" step="1" id="sim-lab-seed"></label>';
    return html + "</li>";
  }

  function renderRunStep() {
    const program = currentProgram();
    let html = '<li class="sim-lab-step">' + stepHead("⑤", "実行して並べて見る");
    if (!program) return html + '<p class="sim-lab-muted">②でコードを用意すると実行できます。</p></li>';
    const status = str(obj(program).review_status);
    const sandboxOff = ov().sandbox_configured === false;
    const usable = wirings().filter((w) => str(obj(w).origin) !== "llm_candidate");
    html += '<div class="sim-lab-row"><label>配線 <select id="sim-lab-run-wiring"><option value="">配線なし</option>' +
      usable.map((w) => '<option value="' + esc(obj(w).id) + '"' + (str(obj(w).id) === st.wiringId ? " selected" : "") + ">" + esc(obj(w).name || "配線") + "</option>").join("") +
      "</select></label> " +
      '<button type="button" class="sim-lab-btn sim-lab-primary" data-sl-action="run"' +
      (st.busy.run || sandboxOff || status !== "confirmed" ? " disabled" : "") + ">" + (st.busy.run ? "実行中…" : "実行する") + "</button></div>";
    if (status !== "confirmed") html += '<p class="sim-lab-muted">実行するには、②でコードを採用してください。</p>';
    html += notice("run");
    const run = selectedRun();
    if (run) html += renderRunCard(run);
    html += renderHistory();
    return html + "</li>";
  }

  function selectedRun() {
    const list = runs();
    if (st.selectedRunId) {
      const hit = list.find((r) => str(obj(r).id) === st.selectedRunId);
      if (hit) return hit;
    }
    return null;
  }

  function listToMap(list, valueKey) {
    const out = {};
    arr(list).forEach((it) => {
      const o = obj(it);
      const s = symOf(o);
      if (s) out[s] = valueKey ? o[valueKey] : o;
    });
    return out;
  }

  function renderRunCard(run) {
    const r = obj(run);
    const result = obj(r.result);
    const comparison = obj(result.comparison);
    const labels = obj(result.labels);
    const status = str(r.status);
    const prediction = listToMap(r.prediction, "value");
    const theory = listToMap(result.theory_outputs, "value");
    const observed = listToMap(result.observed);
    const syms = [];
    [prediction, theory, observed].forEach((m) => Object.keys(m).forEach((s) => { if (syms.indexOf(s) < 0) syms.push(s); }));
    let html = '<div class="sim-lab-run">' +
      '<div class="sim-lab-card-head">実行の結果 ' + chip(RUN_STATUS_LABELS[status] || status, "sim-lab-status-" + esc(status)) +
      (r.shared ? " " + chip("共有済み", "sim-lab-chip-shared") : "") +
      (r.created_at ? ' <span class="sim-lab-muted">' + esc(r.created_at) + "</span>" : "") + "</div>";
    if (syms.length) {
      // 予想の列には判定の語・色を付けない（SB14 / UC2）。
      html += '<table class="sim-lab-table"><thead><tr><th>記号</th><th>あなたの予想</th><th>理論の出力</th><th>模擬観測（平均 ± ばらつき）</th></tr></thead><tbody>' +
        syms.map((s) => {
          const o = obj(observed[s]);
          const obsText = (o.mean === undefined && o.std === undefined) ? "—" : fmt(o.mean) + " ± " + fmt(o.std);
          return "<tr><td><code>" + esc(s) + "</code></td><td>" + esc(fmt(prediction[s])) + "</td><td>" +
            esc(fmt(theory[s])) + "</td><td>" + esc(obsText) + "</td></tr>";
        }).join("") + "</tbody></table>";
    }
    const chips = [labels.agreement, labels.separation, labels.reference].filter(Boolean);
    if (chips.length) {
      html += '<div class="sim-lab-row"><span class="sim-lab-muted">理論の出力と模擬観測:</span> ' +
        chips.map((c) => chip(c, "sim-lab-chip-label")).join(" ") + "</div>";
    }
    if (result.statement) html += '<p class="sim-lab-statement">' + esc(result.statement) + "</p>";
    let facts = [].concat(arr(comparison.facts), arr(result.facts));
    facts = facts.filter((f, i) => f && facts.indexOf(f) === i);
    if (!facts.length && status && status !== "succeeded") facts.push("実行は完了しませんでした。");
    html += factsHtml(facts);
    html += metricsTable(comparison.metrics);
    if (status === "succeeded") {
      const hasWiring = !!r.wiring_id;
      html += '<div class="sim-lab-row">' +
        '<button type="button" class="sim-lab-btn" data-sl-action="share" data-run-id="' + esc(r.id) + '"' +
        (r.shared || st.busy.share ? " disabled" : "") + ">" + (r.shared ? "共有済み" : "共有する") + "</button>" +
        '<button type="button" class="sim-lab-btn" data-sl-action="propose" data-run-id="' + esc(r.id) + '"' +
        (hasWiring && !st.busy.propose ? "" : " disabled") + ">改訂案を求める</button></div>";
      if (!hasWiring) html += '<p class="sim-lab-muted">改訂案は、配線を使った実行にだけ求められます。</p>';
      if (!r.shared) html += '<p class="sim-lab-muted">共有すると、この実行が教員に見え、関係する主張の台帳に候補として記帳されます。</p>';
    }
    html += notice("share") + notice("propose");
    if (st.proposal && str(st.proposal.runId) === str(r.id)) html += renderProposal(st.proposal);
    return html + "</div>";
  }

  function metricsTable(metrics) {
    const m = obj(metrics);
    let rows = "";
    Object.keys(METRIC_LABELS).forEach((k) => {
      if (m[k] === undefined || m[k] === null) return;
      rows += "<tr><td>" + esc(METRIC_LABELS[k]) + "</td><td>—</td><td>" + esc(fmt(m[k])) + "</td></tr>";
    });
    arr(m.by_symbol).forEach((it) => {
      const o = obj(it);
      const s = str(o.symbol);
      Object.keys(o).forEach((k) => {
        if (k === "symbol" || o[k] === null || o[k] === undefined) return;
        rows += "<tr><td>" + esc(METRIC_LABELS[k] || k) + "</td><td><code>" + esc(s) + "</code></td><td>" + esc(fmt(o[k])) + "</td></tr>";
      });
    });
    if (!rows) return "";
    return '<details class="sim-lab-metrics"><summary>あなたの実行の数値</summary><table class="sim-lab-table"><thead><tr><th>計器</th><th>記号</th><th>値</th></tr></thead><tbody>' +
      rows + "</tbody></table></details>";
  }

  function renderProposal(p) {
    const proposal = obj(p.proposal);
    let html = '<div class="sim-lab-proposal">' + chip("AI の提案（未確認）", "sim-lab-chip-candidate");
    const changes = arr(proposal.changes);
    if (changes.length) {
      html += '<ul class="sim-lab-facts">' + changes.map((c) => {
        if (typeof c === "string") return "<li>" + esc(c) + "</li>";
        const co = obj(c);
        return "<li>" + esc(co.fact || co.description || co.reason || co.kind || "") + "</li>";
      }).join("") + "</ul>";
    }
    html += factsHtml(proposal.facts);
    if (p.wiring) html += '<p class="sim-lab-muted">提案された配線は③に「AI の提案（未確認）」として並びます。' + esc(CANDIDATE_WIRING_NOTE) + "。</p>";
    return html + "</div>";
  }

  function renderHistory() {
    const list = runs();
    if (!list.length) return "";
    return '<details class="sim-lab-history"' + (st.historyOpen ? " open" : "") + '><summary data-sl-action="toggle-history">これまでの実行（あなたの記録）</summary><ul>' +
      list.map((r) => {
        const ro = obj(r);
        const lab = obj(obj(ro.result).labels);
        return '<li><button type="button" class="sim-lab-link" data-sl-action="select-run" data-run-id="' + esc(ro.id) + '">' +
          esc(ro.created_at || "") + " " + esc(RUN_STATUS_LABELS[str(ro.status)] || str(ro.status)) +
          (lab.agreement ? " / " + esc(lab.agreement) : "") + (ro.shared ? " / 共有済み" : "") + "</button></li>";
      }).join("") + "</ul></details>";
  }

  // ---- 操作 --------------------------------------------------------------------
  function readNumber(el) {
    if (!el) return null;
    const raw = str(el.value).trim();
    if (!raw) return null;
    const n = Number(raw);
    return isFinite(n) ? n : null;
  }
  // 入力の値: 「0.1, 0, 0」のようなカンマ区切りはベクトル（数値の配列）として送る。
  function readValue(el) {
    if (!el) return null;
    const raw = str(el.value).trim();
    if (!raw) return null;
    if (raw.indexOf(",") < 0) return readNumber(el);
    const parts = raw.split(",").map((x) => Number(x.trim()));
    return parts.length && parts.every((n) => isFinite(n)) ? parts : null;
  }
  function collect(selector, attr) {
    const out = [];
    region.querySelectorAll(selector).forEach((el) => {
      const v = attr === "data-sl-value" ? readValue(el) : readNumber(el);
      if (v !== null) out.push({ name: el.getAttribute(attr), value: v });
    });
    return out;
  }

  async function act(key, fn) {
    st.busy[key] = true;
    st.notice[key] = "";
    render();
    try {
      await fn();
    } catch (e) {
      st.notice[key] = (e && e.message) || GENERIC_ERROR;
    } finally {
      st.busy[key] = false;
      render();
    }
  }

  function generate() {
    const mod = currentModule();
    const c = ctxIds();
    if (!mod) return;
    act("generate", async () => {
      const data = await post("/learning/courses/" + encodeURIComponent(c.courseId) + "/simulation/programs/generate",
        { document_id: docId(), module_key: str(mod.module_key) });
      if (data.program) replaceProgram(data.program);
      const facts = arr(data.facts).filter(Boolean);
      if (facts.length && !data.program) st.notice.generate = facts.join(" ");
    });
  }

  function editProgram() {
    const program = currentProgram();
    const ta = region.querySelector("#sim-lab-code");
    if (!program || !ta) return;
    const code = ta.value;
    act("edit", async () => {
      st.notice.program = "";
      const data = await post("/learning/simulation/programs/" + encodeURIComponent(str(program.id)) + "/edit", { source_code: code });
      if (data.program) replaceProgram(data.program);
    }).then(() => { if (st.notice.edit) { st.notice.program = st.notice.edit; st.notice.edit = ""; render(); } });
  }

  function adoptProgram() {
    const program = currentProgram();
    if (!program) return;
    act("adopt", async () => {
      const data = await post("/learning/simulation/programs/" + encodeURIComponent(str(program.id)) + "/adopt", {});
      if (data.program) replaceProgram(data.program);
    }).then(() => { if (st.notice.adopt) { st.notice.program = st.notice.adopt; st.notice.adopt = ""; render(); } });
  }

  function addPart() {
    const sel = region.querySelector("#sim-lab-part-select");
    const parts = arr(ov().parts_catalog);
    if (!sel) return;
    const part = parts[Number(sel.value)];
    if (!part) return;
    syncBuilderInputs();
    st.builder.push({ part: obj(part), values: {} });
    render();
  }
  function removePart(idx) {
    syncBuilderInputs();
    st.builder.splice(idx, 1);
    render();
  }
  function syncBuilderInputs() {
    if (!region) return;
    region.querySelectorAll("[data-sl-builder]").forEach((el) => {
      const b = st.builder[Number(el.getAttribute("data-sl-builder"))];
      if (!b) return;
      const v = readNumber(el);
      if (v !== null) b.values[el.getAttribute("data-sl-param")] = v;
    });
    const nameEl = region.querySelector("#sim-lab-wiring-name");
    if (nameEl) st.builderName = nameEl.value;
  }

  function saveWiring() {
    syncBuilderInputs();
    const c = ctxIds();
    if (!st.builder.length) return;
    const parts = st.builder.map((b, i) => {
      const p = obj(b.part);
      const params = arr(p.params).map((pr) => {
        const name = str(obj(pr).name);
        const val = Object.prototype.hasOwnProperty.call(b.values, name) ? b.values[name] : obj(pr).default;
        return { name, value: val };
      }).filter((x) => x.name && x.value !== null && x.value !== undefined && x.value !== "");
      return { slot: "s" + (i + 1), entry_id: p.entry_id, version_no: p.version_no, params };
    });
    const edges = [];
    for (let i = 1; i < parts.length; i += 1) edges.push({ from_slot: parts[i - 1].slot, to_slot: parts[i].slot });
    const name = st.builderName.trim() || "わたしの配線";
    act("wiring", async () => {
      const data = await post("/learning/courses/" + encodeURIComponent(c.courseId) + "/simulation/wirings",
        { document_id: docId(), name, parts, edges });
      if (data.wiring) {
        const o = obj(st.overview);
        o.wirings = upsert(o.wirings, data.wiring);
        st.overview = o;
        st.wiringId = str(data.wiring.id);
        st.builder = [];
        st.builderName = "";
      }
    });
  }

  function runProgram() {
    const program = currentProgram();
    const c = ctxIds();
    if (!program) return;
    const wiringSel = region.querySelector("#sim-lab-run-wiring");
    const wiringId = wiringSel ? str(wiringSel.value) : "";
    st.wiringId = wiringId;
    const prediction = collect("[data-sl-predict]", "data-sl-predict").map((x) => ({ symbol: x.name, value: x.value }));
    const values = collect("[data-sl-value]", "data-sl-value").map((x) => ({ symbol: x.name, value: x.value }));
    const params = collect("[data-sl-param-run]", "data-sl-param-run");
    const refs = {};
    region.querySelectorAll("[data-sl-ref]").forEach((el) => {
      const s = el.getAttribute("data-sl-ref");
      const field = el.getAttribute("data-sl-ref-field");
      refs[s] = refs[s] || { symbol: s };
      if (field === "value") refs[s].value = readNumber(el);
      else refs[s][field] = str(el.value).trim();
    });
    const reference = Object.keys(refs).map((k) => refs[k]).filter((r) => r.value !== null && r.value !== undefined);
    const claimIds = [];
    region.querySelectorAll("[data-sl-claim]").forEach((el) => { if (el.checked) claimIds.push(el.getAttribute("data-sl-claim")); });
    const seed = readNumber(region.querySelector("#sim-lab-seed"));
    const payload = {
      document_id: docId(),
      program_id: str(program.id),
      observation_claim_ids: claimIds,
      values,
      params,
    };
    if (wiringId) payload.wiring_id = wiringId;
    if (prediction.length) payload.prediction = prediction;
    if (reference.length) payload.reference = reference;
    if (seed !== null) payload.seed = Math.trunc(seed);
    act("run", async () => {
      const data = await post("/learning/courses/" + encodeURIComponent(c.courseId) + "/simulation/runs", payload);
      if (data.run) {
        const o = obj(st.overview);
        o.runs = upsert(o.runs, data.run);
        st.overview = o;
        st.selectedRunId = str(data.run.id);
        st.proposal = null;
      }
    });
  }

  function shareRun(runId) {
    if (!runId) return;
    if (!window.confirm(SHARE_CONFIRM)) return;
    act("share", async () => {
      const data = await post("/learning/simulation/runs/" + encodeURIComponent(runId) + "/share", {});
      if (data.run) {
        const o = obj(st.overview);
        o.runs = upsert(o.runs, data.run);
        st.overview = o;
      }
    });
  }

  function proposeRevision(runId) {
    if (!runId) return;
    act("propose", async () => {
      const data = await post("/learning/simulation/runs/" + encodeURIComponent(runId) + "/propose-revision", {});
      st.proposal = { runId, wiring: data.wiring || null, proposal: obj(data.proposal) };
      if (data.wiring) {
        const o = obj(st.overview);
        o.wirings = upsert(o.wirings, data.wiring);
        st.overview = o;
      }
    });
  }

  function onClick(e) {
    const target = e.target && e.target.closest ? e.target.closest("[data-sl-action]") : null;
    if (!target || !region.contains(target)) return;
    const action = target.getAttribute("data-sl-action");
    if (action === "toggle-history") {
      // <details> の既定の開閉に任せ、状態だけ覚える（再描画で畳まれないように）。
      const d = target.closest("details");
      st.historyOpen = d ? !d.open : !st.historyOpen;
      return;
    }
    if (target.disabled) return;
    switch (action) {
      case "close": close(); break;
      case "select-module":
        st.moduleKey = str(target.getAttribute("data-module-key"));
        st.programId = "";
        st.notice = {};
        st.selectedRunId = "";
        st.proposal = null;
        render();
        break;
      case "generate": generate(); break;
      case "edit": editProgram(); break;
      case "adopt": adoptProgram(); break;
      case "add-part": addPart(); break;
      case "remove-part": removePart(Number(target.getAttribute("data-index"))); break;
      case "save-wiring": saveWiring(); break;
      case "run": runProgram(); break;
      case "share": shareRun(str(target.getAttribute("data-run-id"))); break;
      case "propose": proposeRevision(str(target.getAttribute("data-run-id"))); break;
      case "select-run":
        st.selectedRunId = str(target.getAttribute("data-run-id"));
        st.proposal = null;
        render();
        break;
      default: break;
    }
  }

  function init(options) {
    deps = Object.assign({}, deps, options || {});
    region = document.getElementById("simulation-lab-region");
    button = document.getElementById("simulation-lab-btn");
    if (region && !region.dataset.slBound) {
      region.addEventListener("click", onClick);
      region.addEventListener("change", (e) => {
        const t = e.target && e.target.closest ? e.target.closest('[data-sl-change="choose-doc"]') : null;
        if (!t) return;
        st.chosenDocumentId = str(t.value);
        st.moduleKey = ""; st.programId = ""; st.wiringId = ""; st.selectedRunId = "";
        refresh();
      });
      region.dataset.slBound = "1";
    }
    if (button && !button.dataset.slBound) {
      button.addEventListener("click", () => { if (st.open) close(); else open(); });
      button.dataset.slBound = "1";
    }
    updateButton();
  }

  window.SimulationLab = { init, open, close, refresh, setContext };
})();
