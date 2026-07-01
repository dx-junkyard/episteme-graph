/* ===================================================================
   Episteme Graph — Learning UI Application Logic
   =================================================================== */

(function () {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────
  const state = {
    token: localStorage.getItem("eg_token") || null,
    username: localStorage.getItem("eg_username") || null,
    role: null,
    courseId: localStorage.getItem("eg_course") || null,
    course: null,        // master_course（不変の教材データ）
    personalLayer: null, // personal_layer（個人の誤解・注釈データ）
    currentTopicId: null,
    chatMessages: [], // {role, content}
    topicMaterial: [], // {id, text, chunk_index, chapter, section}
    learningSupport: null, // {mode, status_label, origin}
    sending: false,
    checkingUnderstanding: false,
    topicHasAudio: false, // 現トピックに再生可能なキャッシュ済み音声があるか
    // ── 学習者体験レイヤー(B層) Stage M ──
    lastSources: [],        // L1: 直近回答の根拠 tier 一覧 [{source_title, tier, score}]
    lastOverallTier: null,  // L1: 直近回答全体の格
    interestTraces: null,   // L3: UnfinishedQuestionBox（mock）
  };

  // 🚧 Mock 検知: API レスポンスが mock データを含むか（_mock / mock の両方を許容）。
  function isMock(obj) {
    return !!(obj && (obj._mock === true || obj.mock === true));
  }

  // 🚧 MOCK バッジ。Stage M の mock データに必ず付与し、デモ時に一目で分かるようにする。
  function mockFlag(note) {
    return '<span class="mock-flag" title="' + escHtml(note || "Stage M のモックデータです") + '">🚧 MOCK</span>';
  }

  // L1 tier の表示メタ（ラベル・CSS クラス）。
  var TIER_META = {
    approved: { label: "承認済み", cls: "tier-approved" },
    source: { label: "原典", cls: "tier-source" },
    out_of_source: { label: "参考", cls: "tier-out" },
  };
  function tierMeta(tier) {
    return TIER_META[tier] || TIER_META.out_of_source;
  }
  function tierBadge(tier) {
    var m = tierMeta(tier);
    return '<span class="tier-badge ' + m.cls + '">' + escHtml(m.label) + "</span>";
  }

  function learningSupportStorageKey() {
    return "eg_learning_support:" + (state.courseId || "");
  }

  function saveLearningSupportContext() {
    if (!state.courseId) return;
    if (state.learningSupport && state.learningSupport.origin) {
      localStorage.setItem(learningSupportStorageKey(), JSON.stringify(state.learningSupport));
    } else {
      localStorage.removeItem(learningSupportStorageKey());
    }
  }

  function loadLearningSupportContext() {
    state.learningSupport = null;
    if (!state.courseId) return;
    try {
      var raw = localStorage.getItem(learningSupportStorageKey());
      state.learningSupport = raw ? JSON.parse(raw) : null;
    } catch (_) {
      state.learningSupport = null;
    }
  }

  function setLearningSupportFromResponse(data) {
    if (data && data.origin && data.status_label) {
      state.learningSupport = {
        mode: data.support_mode || "detail_explanation",
        status_label: data.status_label,
        origin: data.origin,
      };
    } else if (data && data.support_mode === "return_to_learning_path") {
      state.learningSupport = null;
    }
    saveLearningSupportContext();
  }

  // ── Learning session (single source of truth) ──────────────────────
  // 学習セッションの「現在位置」を1か所に集約する facade。
  //   anchor  = state.currentTopicId（パス上のトピック）
  //   detour  = state.learningSupport（寄り道。origin に復帰先アンカーを保持）
  //   segment = lectureState.currentSegmentIndex（レクチャー再生位置）
  // 位置情報の読み書きはすべてここを経由し、永続化は learningSupport の
  // localStorage を継続利用する。
  var Session = {
    anchorTopicId: function () { return state.currentTopicId; },
    inDetour: function () {
      return !!(state.learningSupport && state.learningSupport.origin);
    },
    detourOrigin: function () {
      return state.learningSupport ? state.learningSupport.origin : null;
    },
    // detour 中なら support_context を含む payload 断片を返す（型付き送信の補助）。
    contextPayload: function () {
      return Session.inDetour() ? { support_context: state.learningSupport } : {};
    },
    // L2 位置・復帰: いまの正確な読み位置 {topic_id, segment_id, scroll_offset}。
    // 寄り道に入る瞬間にこれを送ると、origin（戻り先）が正確な位置を保持する。
    currentAnchor: function () {
      var seg = (typeof lectureState !== "undefined" && lectureState.active)
        ? (lectureState.currentSegmentIndex || 0) : 0;
      var area = document.getElementById("chat-area");
      var scroll = area ? Math.round(area.scrollTop) : 0;
      return { topic_id: state.currentTopicId, segment_id: seg, scroll_offset: scroll };
    },
    // origin（戻り先）の正確な位置へ復帰する（セグメント＋スクロール、ベストエフォート）。
    restorePosition: function (anchor) {
      if (!anchor) return;
      if (typeof lectureState !== "undefined" && lectureState.active &&
          typeof anchor.segment_id === "number" && anchor.segment_id > 0 &&
          Array.isArray(lectureState.segments) && lectureState.segments.length) {
        lectureState.currentSegmentIndex = Math.min(anchor.segment_id, lectureState.segments.length - 1);
        if (typeof renderLectureContent === "function") renderLectureContent();
      }
      if (typeof anchor.scroll_offset === "number") {
        var area = document.getElementById("chat-area");
        if (area) area.scrollTop = anchor.scroll_offset;
      }
    },
    // 寄り道を終了し、アンカーへ復帰する準備をする（再描画は呼び出し側）。
    clearDetour: function () {
      if (!state.learningSupport) return;
      state.learningSupport = null;
      saveLearningSupportContext();
    },
  };

  function parseJwtPayload(token) {
    try {
      var parts = token.split(".");
      if (parts.length !== 3) return null;
      var payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(payload));
    } catch (e) { return null; }
  }

  if (state.token) {
    var decoded = parseJwtPayload(state.token);
    if (decoded) state.role = decoded.role || "STUDENT";
  }

  // ── API helpers ────────────────────────────────────────────────────
  const API = "/api";

  async function apiFetch(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    const res = await fetch(API + path, { ...opts, headers });
    if (res.status === 401) {
      state.token = null;
      localStorage.removeItem("eg_token");
      renderAuth();
      throw new Error("Unauthorized");
    }
    return res;
  }

  // ── Auth ───────────────────────────────────────────────────────────
  function renderAuth() {
    let overlay = document.getElementById("auth-overlay");
    if (state.token) {
      if (overlay) overlay.remove();
      return;
    }
    if (overlay) return; // already showing

    overlay = document.createElement("div");
    overlay.id = "auth-overlay";
    overlay.className = "auth-overlay";
    overlay.innerHTML = `
      <div class="auth-box">
        <h2>Episteme Graph</h2>
        <form id="auth-form">
          <input id="auth-user" type="text" placeholder="ユーザー名" required autocomplete="username">
          <input id="auth-pass" type="password" placeholder="パスワード" required autocomplete="current-password">
          <button type="submit" id="auth-btn">ログイン</button>
        </form>
        <div class="auth-toggle" id="auth-toggle">
          アカウントがない場合 <a id="auth-switch">新規登録</a>
        </div>
        <div class="auth-error" id="auth-error"></div>
      </div>
    `;
    document.body.appendChild(overlay);

    let isLogin = true;
    document.getElementById("auth-switch").addEventListener("click", function () {
      isLogin = !isLogin;
      document.getElementById("auth-btn").textContent = isLogin ? "ログイン" : "登録";
      document.getElementById("auth-toggle").innerHTML = isLogin
        ? 'アカウントがない場合 <a id="auth-switch">新規登録</a>'
        : '既にアカウントがある場合 <a id="auth-switch">ログイン</a>';
      document.getElementById("auth-switch").addEventListener("click", arguments.callee);
    });

    document.getElementById("auth-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const username = document.getElementById("auth-user").value.trim();
      const password = document.getElementById("auth-pass").value;
      const errEl = document.getElementById("auth-error");
      errEl.textContent = "";

      const endpoint = isLogin ? "/auth/login" : "/auth/register";
      const payload = isLogin
        ? { username, password }
        : { username, password, email: username + "@learning.local" };
      try {
        const res = await fetch(API + endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          errEl.textContent = data.detail || "認証に失敗しました";
          return;
        }
        const data = await res.json();
        state.token = data.access_token;
        state.username = username;
        var decoded = parseJwtPayload(data.access_token);
        state.role = decoded ? (decoded.role || "STUDENT") : "STUDENT";
        localStorage.setItem("eg_token", data.access_token);
        localStorage.setItem("eg_username", username);
        overlay.remove();
        initApp();
      } catch (err) {
        errEl.textContent = "サーバーに接続できません";
      }
    });
  }

  // ── Course Data ────────────────────────────────────────────────────
  async function loadCourses() {
    try {
      const res = await apiFetch("/learning/courses");
      if (res.ok) return await res.json();
    } catch (_) { /* ignore */ }
    return [];
  }

  async function loadCourse(courseId) {
    try {
      const res = await apiFetch("/learning/courses/" + courseId);
      if (res.ok) {
        const data = await res.json();
        // Issue #145: レイヤー型レスポンスを展開する
        if (data && data.master_course) {
          return {
            master: data.master_course,
            personal: data.personal_layer || { misconceptions_by_topic: {}, chat_anchors: {} },
          };
        }
        // 旧形式フォールバック
        return { master: data, personal: { misconceptions_by_topic: {}, chat_anchors: {} } };
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  async function loadProgress(courseId) {
    try {
      const res = await apiFetch("/learning/courses/" + courseId + "/progress");
      if (res.ok) return await res.json();
    } catch (_) { /* ignore */ }
    return null;
  }

  async function loadChatHistory(courseId, topicId) {
    try {
      const res = await apiFetch("/learning/courses/" + courseId + "/topics/" + topicId + "/chat");
      if (res.ok) {
        const data = await res.json();
        return data.history || [];
      }
    } catch (_) { /* ignore */ }
    return [];
  }

  async function clearChatHistory() {
    if (!state.courseId || !state.currentTopicId || state.sending) return;
    if (state.chatMessages.length === 0) return;
    if (!confirm("このトピックの質疑応答履歴を削除します。よろしいですか？")) return;

    var btn = document.getElementById("chat-clear-btn");
    if (btn) btn.disabled = true;
    try {
      const res = await apiFetch(
        "/learning/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/chat",
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error("Delete failed");
      state.chatMessages = [];
      renderChat();
    } catch (err) {
      alert("質疑応答履歴の削除に失敗しました。");
      if (btn) btn.disabled = false;
    }
  }

  // ── Render: Sidebar ────────────────────────────────────────────────
  function renderSidebar() {
    const sb = document.getElementById("sidebar");
    if (!state.course) {
      sb.innerHTML = '<div class="sb-hd">コースを選択してください</div>';
      return;
    }
    const course = state.course;
    let html = '<div class="sb-hd">コースツリー</div>';
    html += '<div class="course-tree-title">' + escHtml(course.title || "コース") + '</div>';

    (course.chapters || []).forEach(function (ch, ci) {
      const chNum = ci + 1;
      const chActive = (course.topics || []).some(function (t) {
        return t.chapter_index === ci && t.id === state.currentTopicId;
      });
      const chStatus = ch.status || "locked";
      const dotClass = chStatus === "completed" ? "dot-g" : chStatus === "in_progress" ? "dot-b" : "dot-x";
      const style = chStatus === "locked" ? ' style="color:var(--color-text-tertiary)"' : "";

      html += '<div class="ni' + (chActive ? " act" : "") + '"' + style + '>';
      html += '<span class="num">' + chNum + "</span>" + escHtml(ch.title);
      html += '<span class="dot ' + dotClass + '"></span></div>';

      // Sub-topics
      (course.topics || []).filter(function (t) { return t.chapter_index === ci; }).forEach(function (t) {
        const tActive = t.id === state.currentTopicId;
        const tStatus = t.status || "locked";
        const cls = tActive ? "ni sub act" : tStatus === "locked" ? "ni sub lk" : "ni sub";
        const dotCls = tStatus === "completed" ? "dot-g" : tStatus === "in_progress" ? "dot-b" : "dot-x";

        // Issue #145: 個人誤解がある場合は注釈マーカーを表示
        const personalLayer = state.personalLayer || {};
        const misconsByTopic = personalLayer.misconceptions_by_topic || {};
        const misconsCount = (misconsByTopic[t.id] || []).length;
        const annotationBadge = misconsCount > 0
          ? '<span class="mc-badge" title="' + misconsCount + '件の誤解が記録されています">⚑ ' + misconsCount + '</span>'
          : "";
        const support = state.learningSupport;
        const supportOrigin = support && support.origin;
        const supportBadge = supportOrigin && supportOrigin.topic_id === t.id
          ? '<span class="learning-support-badge" title="' + escHtml(supportOrigin.topic_title || t.title) + ' から派生した説明です">' +
              escHtml(support.status_label || "詳細説明中") + '</span>'
          : "";

        html += '<div class="' + cls + '" data-topic="' + t.id + '" style="padding-left:36px">';
        html += escHtml(t.title) + annotationBadge + supportBadge;
        html += '<span class="dot ' + dotCls + '" style="margin-left:auto"></span></div>';
      });
    });

    // Concept map
    html += '<div class="sb-hd" style="margin-top:14px">概念マップ</div><div class="ct">';
    (course.concepts || []).forEach(function (c) {
      const sCls = c.status === "mastered" ? "ct-i ms" :
                   c.status === "learning" ? "ct-i cur" : "ct-i fut";
      const icon = c.children && c.children.length > 0 ? (c.expanded ? "-" : "+") : "";
      html += '<div class="' + sCls + '" data-concept="' + escHtml(c.name) + '">';
      html += '<span class="ct-ind">' + icon + "</span>" + escHtml(c.name) + "</div>";
      if (c.expanded && c.children) {
        c.children.forEach(function (child) {
          html += '<div class="ct-i ct-sub"><span class="ct-ind"></span>' + escHtml(child) + "</div>";
        });
      }
    });
    html += "</div>";

    sb.innerHTML = html;

    // Bind topic clicks
    sb.querySelectorAll("[data-topic]").forEach(function (el) {
      el.addEventListener("click", function () {
        var tid = this.getAttribute("data-topic");
        selectTopic(tid);
      });
    });
  }

  // ── Render: Chat ───────────────────────────────────────────────────
  function getCurrentTopic() {
    if (!state.course || !state.currentTopicId) return null;
    return (state.course.topics || []).find(function (t) { return t.id === state.currentTopicId; }) || null;
  }

  function _renderInitialSuggestions() {
    var courseTitle = state.course ? escHtml(state.course.title || "") : "";

    // 順路の前進は上部の教材＋「確認して次へ」で行う。ここでは順路と紛らわしい
    // 「学習を開始する」ボタンは出さず、前提知識の確認とフリー質問だけを促す。
    var html = '<div class="mg ai">';
    html += "「" + courseTitle + "」の学習サポートへようこそ！<br>";
    html += "上の教材を読み進めながら、分からない点は質問してください。次のセクションへは「確認して次へ」で進めます。";
    html += "</div>";
    html += '<div class="initial-suggestions">';
    html += '<button class="suggest-btn initial-suggest-btn" data-suggest="このコースに必要な前提知識を確認する" data-support-action="check_prerequisites">';
    html += "このコースに必要な前提知識を確認する</button>";
    html += "</div>";
    return html;
  }

  function renderChat() {
    const ca = document.getElementById("chat-area");
    if (!state.course || !state.currentTopicId) {
      ca.innerHTML = '<div class="mg ai" style="color:var(--color-text-tertiary)">左のサイドバーからトピックを選択してください。</div>';
      renderMaterialRegion();
      renderModeBar();
      return;
    }

    let html = "";

    // 教材は上部の「教材区画」に分離（renderMaterialRegion）。ここはチャット（探索）のみ。
    // 初期状態（チャット履歴なし）ならサジェストUIを表示
    if (state.chatMessages.length === 0 && !state.sending) {
      html += _renderInitialSuggestions();
    }

    state.chatMessages.forEach(function (msg) {
      if (msg.role === "user") {
        html += '<div class="mg usr">' + escHtml(msg.content) + "</div>";
      } else {
        html += '<div class="mg ai">' + renderAiContent(msg.content, msg) + "</div>";
      }
    });

    if (state.sending) {
      html += '<div class="mg ai"><div class="typing"><span></span><span></span><span></span></div></div>';
    }

    ca.innerHTML = html;
    ca.scrollTop = ca.scrollHeight;
    var clearBtn = document.getElementById("chat-clear-btn");
    if (clearBtn) clearBtn.disabled = !state.course || !state.currentTopicId || state.sending || state.chatMessages.length === 0;

    // 出典チップ: クリックで該当チャンク全文（数式込み）をポップアップ表示。
    ca.querySelectorAll(".src-cite").forEach(function (el) {
      el.addEventListener("click", function () { openSourcePopup(this); });
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSourcePopup(this); }
      });
    });

    // Bind suggest buttons (drill-down + initial suggestions)
    ca.querySelectorAll(".suggest-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var suggest = this.getAttribute("data-suggest") || this.textContent.replace(/\s*↗$/, "");
        var payload = {};
        var supportAction = this.getAttribute("data-support-action") || "";
        if (supportAction === "return_to_learning_path") {
          var origin = Session.detourOrigin();
          var targetTopicId = this.getAttribute("data-target-topic-id") ||
            (origin && origin.topic_id);
          returnToLearningPath(targetTopicId);
          return;
        }
        if (supportAction) payload.support_action = supportAction;
        Object.assign(payload, Session.contextPayload());
        sendMessage(suggest, payload);
      });
    });

    // Render KaTeX for any remaining raw LaTeX (fallback)
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(ca, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "\\[", right: "\\]", display: true },
            { left: "\\(", right: "\\)", display: false },
            { left: "$", right: "$", display: false },
          ],
          throwOnError: false,
        });
      } catch (e) { /* KaTeX not yet loaded */ }
    }

    // 教材区画（本筋）とモードバー（現在地）を更新する。
    renderMaterialRegion();
    renderModeBar();
  }

  // 教材区画（本筋・順路）: 教材本文を独立スクロール領域に描画する。
  // ここは renderChat とは別の #material-body に出すため、会話で流れて消えない。
  function renderMaterialRegion() {
    var body = document.getElementById("material-body");
    var here = document.getElementById("material-here");
    if (!body) return;
    if (!state.course || !state.currentTopicId) {
      body.innerHTML = '<div style="color:var(--color-text-tertiary);font-size:13px">トピックを選択すると教材が表示されます。</div>';
      if (here) here.textContent = "";
      updateNextTopicBtn();
      return;
    }
    if (!state.topicMaterial || state.topicMaterial.length === 0) {
      body.innerHTML = '<div style="color:var(--color-text-tertiary);font-size:13px">教材を読み込み中…</div>';
      if (here) here.textContent = "";
      updateNextTopicBtn();
      return;
    }
    var html = '<div class="material-block-header">教材</div>';
    state.topicMaterial.forEach(function (chunk) {
      html += '<div class="material-chunk">';
      if (chunk.chapter || chunk.section) {
        var loc = [chunk.chapter, chunk.section].filter(Boolean).join(" › ");
        html += '<div class="material-chunk-loc">' + escHtml(loc) + '</div>';
      }
      html += '<div class="material-chunk-text">' + renderMaterialChunk(chunk) + '</div>';
      if (chunk.graph_mentions && chunk.graph_mentions.length > 0) {
        html += '<div class="graph-suggestions">';
        chunk.graph_mentions.slice(0, 4).forEach(function (m) {
          var label = m.label || m.surface_text || m.element_id || "この要素";
          var actionText = (m.element_type === "citation") ? "引用情報" :
            (m.element_type === "reference") ? "参照先" : "説明";
          html += '<button class="graph-suggest-btn"'
            + ' data-chunk-id="' + escHtml(chunk.id || "") + '"'
            + ' data-element-id="' + escHtml(m.element_id || "") + '"'
            + ' data-element-type="' + escHtml(m.element_type || "concept") + '"'
            + ' data-element-label="' + escHtml(label) + '"'
            + ' title="' + escHtml(label) + '">';
          html += escHtml(label + "の" + actionText) + '</button>';
        });
        html += '</div>';
      }
      html += '</div>';
    });
    body.innerHTML = html;

    // 現在地ラベル（本筋）
    var topic = getCurrentTopic();
    if (here) here.textContent = topic ? ("本筋：" + (topic.title || "")) : "";

    // グラフサジェストの配線（教材区画内）
    body.querySelectorAll(".graph-suggest-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var label = this.getAttribute("data-element-label") ||
          this.textContent.replace(/\s*の(?:説明|引用情報|参照先)$/, "");
        var type = this.getAttribute("data-element-type") || "concept";
        // 要素が理論コンポーネントの場合は、標準/各教員の説明バージョンを表示する(C層 Phase 2)。
        if (type === "component") {
          showComponentExplanations(this, this.getAttribute("data-element-id") || "", label);
          return;
        }
        var payload = {
          action: "EXPLAIN_GRAPH_ELEMENT",
          chunk_id: this.getAttribute("data-chunk-id") || "",
          element_id: this.getAttribute("data-element-id") || "",
          element_type: type,
          element_label: label,
        };
        Object.assign(payload, Session.contextPayload());
        var suffix = type === "citation" ? "の引用情報" : type === "reference" ? "の参照先" : "を説明";
        sendMessage(label + suffix, payload);
      });
    });
    updateNextTopicBtn();
  }

  // チャット区画のモードバー: 本筋（青）か寄り道（アンバー＋戻る）かを常時表示する。
  function renderModeBar() {
    var bar = document.getElementById("mode-bar");
    if (!bar) return;
    if (!state.course || !state.currentTopicId) { bar.hidden = true; return; }
    bar.hidden = false;
    if (Session.inDetour()) {
      var origin = Session.detourOrigin() || {};
      var label = (state.learningSupport && (state.learningSupport.detour_label || state.learningSupport.status_label)) || "寄り道中";
      if (label.length > 28) label = label.slice(0, 28) + "…";
      bar.className = "mode-bar detour";
      bar.innerHTML =
        '<span class="mb-label">🔍 寄り道中：' + escHtml(label) +
        '<span class="mb-sub"> ／ 本筋〈' + escHtml(origin.topic_title || "") + '〉に戻れます</span></span>' +
        '<button class="mb-return" data-mode-return="' + escHtml(origin.topic_id || "") + '">↩ 本筋へ戻る</button>';
      var rbtn = bar.querySelector("[data-mode-return]");
      if (rbtn) rbtn.addEventListener("click", function () {
        returnToLearningPath(this.getAttribute("data-mode-return"));
      });
    } else {
      var topic = getCurrentTopic();
      bar.className = "mode-bar on-path";
      bar.innerHTML = '<span class="mb-label">📘 本筋に沿って学習中：' + escHtml(topic ? (topic.title || "") : "") +
        '<span class="mb-sub"> ／ 次のセクションは上の「確認して次へ」</span></span>';
    }
  }

  // 既にHTMLエスケープ済みのテキストを行単位で走査し、見出し・箇条書き・
  // 番号付きリスト・段落へ変換する。inline はボールド/コード変換コールバック。
  function mdBlocksToHtml(escaped, inline) {
    var lines = escaped.split("\n");
    var out = [];
    var listType = null; // "ul" | "ol"
    var para = [];
    function closeList() { if (listType) { out.push("</" + listType + ">"); listType = null; } }
    function flushPara() {
      if (para.length) { out.push("<p>" + para.join("<br>") + "</p>"); para = []; }
    }
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].replace(/\s+$/, "");
      if (line === "") { flushPara(); closeList(); continue; }
      var h = /^(#{1,6})\s+(.*)$/.exec(line);
      if (h) {
        flushPara(); closeList();
        var lvl = h[1].length;
        out.push("<h" + lvl + ">" + inline(h[2]) + "</h" + lvl + ">");
        continue;
      }
      var ul = /^\s*[-*+]\s+(.*)$/.exec(line);
      if (ul) {
        flushPara();
        if (listType !== "ul") { closeList(); out.push("<ul>"); listType = "ul"; }
        out.push("<li>" + inline(ul[1]) + "</li>");
        continue;
      }
      var ol = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
      if (ol) {
        flushPara();
        // 連続しない番号付き項目（各 "2." "3." が段落で分断される節見出しなど）でも
        // 元の番号を保つため、リスト開始時に start 属性へ実番号を反映する。
        if (listType !== "ol") {
          closeList();
          var start = parseInt(ol[1], 10);
          out.push(start > 1 ? '<ol start="' + start + '">' : "<ol>");
          listType = "ol";
        }
        out.push("<li>" + inline(ol[2]) + "</li>");
        continue;
      }
      closeList();
      para.push(inline(line));
    }
    flushPara(); closeList();
    return out.join("");
  }

  function renderAiContent(text, msg) {
    // Preserve LaTeX expressions before HTML escaping
    var latexBlocks = [];
    var preserved = text || "";

    // Preserve display math $$...$$ first
    preserved = preserved.replace(/\$\$([\s\S]+?)\$\$/g, function (m, expr) {
      var idx = latexBlocks.length;
      latexBlocks.push({ display: true, expr: expr });
      return "\x00LATEX_BLOCK_" + idx + "\x00";
    });
    // Preserve display math \[...\]
    preserved = preserved.replace(/\\\[([\s\S]+?)\\\]/g, function (m, expr) {
      var idx = latexBlocks.length;
      latexBlocks.push({ display: true, expr: expr });
      return "\x00LATEX_BLOCK_" + idx + "\x00";
    });
    // Preserve inline math \(...\)
    preserved = preserved.replace(/\\\(([\s\S]+?)\\\)/g, function (m, expr) {
      var idx = latexBlocks.length;
      latexBlocks.push({ display: false, expr: expr });
      return "\x00LATEX_BLOCK_" + idx + "\x00";
    });
    // Preserve inline math $...$
    preserved = preserved.replace(/\$([^\$\n]+?)\$/g, function (m, expr) {
      var idx = latexBlocks.length;
      latexBlocks.push({ display: false, expr: expr });
      return "\x00LATEX_BLOCK_" + idx + "\x00";
    });

    // Click targets are delivered exclusively via the structured `next_actions`
    // contract (normalized server-side). Here we only strip leftover inline
    // markers from legacy stored messages so they never leak as visible text or
    // the old \x00SUGGEST_n\x00 sentinels.
    preserved = preserved
      .replace(/\[ACTION_BUTTON:\s*[^\]\n]{1,120}\]/g, "")
      .replace(/\[[^\]\n]{2,80}?について(?:詳しく)?(?:聞く|教えて|教えてください|知りたい)\]/g, "")
      .replace(/(?:^|\n)(?:\d+[.．]\s*)?【ネクストアクション】[\s\S]*$/m, "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();

    // Escape HTML
    var html = escHtml(preserved);
    // Inline formatting (applied per-line below): bold / inline code
    function inlineMd(s) {
      s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
      return s;
    }
    // Block-level markdown: headings (#..######), ordered/unordered lists,
    // and paragraphs. Operates on already-escaped text so markers survive.
    html = mdBlocksToHtml(html, inlineMd);

    // Restore LaTeX blocks
    html = html.replace(/\x00LATEX_BLOCK_(\d+)\x00/g, function (_, idx) {
      var block = latexBlocks[parseInt(idx)];
      try {
        return window.katex
          ? window.katex.renderToString(normalizeKatexFormula(block.expr, block.display), { displayMode: block.display, throwOnError: false })
          : (block.display ? "$$" + block.expr + "$$" : "$" + block.expr + "$");
      } catch (e) {
        return block.display ? "$$" + escHtml(block.expr) + "$$" : "$" + escHtml(block.expr) + "$";
      }
    });

    // Render next-step buttons — single source: the structured next_actions
    // contract. Each button carries a typed support-action + an explicit
    // message, so clicking never round-trips a display label through intent
    // classification.
    if (msg && msg.next_actions && msg.next_actions.length > 0) {
      html += renderNextActions(msg.next_actions);
    }

    // L1 信頼性: 回答全体の格（tier）を末尾に明示する（実データ）。
    if (msg && msg.overall_tier) {
      var bar = '<div class="answer-tier-bar">';
      bar += '<span class="answer-tier-label">この回答の根拠の格</span>';
      bar += tierBadge(msg.overall_tier);
      if (msg.mock) bar += mockFlag("この回答に mock データが含まれます");
      bar += "</div>";
      html += bar;
    }

    // 本文中の連番出典 [出典N] を、該当チャンクをポップアップ表示できる span に変換する。
    return linkifyCitations(html, msg);
  }

  // [出典N] → クリック可能な出典チップ（対応する根拠が無ければそのまま素通し）。
  function linkifyCitations(html, msg) {
    var byIndex = {};
    ((msg && msg.sources) || []).forEach(function (s) {
      if (s && s.index) byIndex[s.index] = s;
    });
    return html.replace(/\[出典(\d+)\]/g, function (m, n) {
      var s = byIndex[parseInt(n, 10)];
      if (!s || !s.chunk_id) return m;
      return '<span class="src-cite" role="button" tabindex="0"'
        + ' data-chunk-id="' + escHtml(s.chunk_id) + '"'
        + ' data-tier="' + escHtml(s.tier || "") + '"'
        + ' data-score="' + escHtml(String(s.score != null ? s.score : "")) + '"'
        + ' data-title="' + escHtml(s.source_title || "") + '">'
        + escHtml(m) + '</span>';
    });
  }

  // ── 出典ポップアップ ────────────────────────────────────────────────
  function _srcOutsideClose(e) {
    var pop = document.getElementById("src-popup");
    if (!pop) return;
    if (pop.contains(e.target) || (e.target.classList && e.target.classList.contains("src-cite"))) return;
    closeSourcePopup();
  }

  function closeSourcePopup() {
    var p = document.getElementById("src-popup");
    if (p) p.remove();
    document.removeEventListener("mousedown", _srcOutsideClose, true);
    document.removeEventListener("keydown", _srcEscClose, true);
  }

  function _srcEscClose(e) { if (e.key === "Escape") closeSourcePopup(); }

  function _positionSourcePopup(pop, anchor) {
    var r = anchor.getBoundingClientRect();
    var pw = Math.min(420, window.innerWidth - 24);
    pop.style.width = pw + "px";
    var left = Math.max(12, Math.min(r.left, window.innerWidth - pw - 12));
    var top = r.bottom + 8;
    // 下に入りきらなければ上に出す。
    if (top + 280 > window.innerHeight && r.top > window.innerHeight - r.bottom) {
      pop.style.bottom = (window.innerHeight - r.top + 8) + "px";
      pop.style.top = "auto";
    } else {
      pop.style.top = top + "px";
    }
    pop.style.left = left + "px";
  }

  async function openSourcePopup(anchor) {
    closeSourcePopup();
    var chunkId = anchor.getAttribute("data-chunk-id");
    if (!chunkId || !state.courseId) return;
    var tier = anchor.getAttribute("data-tier") || "";
    var score = anchor.getAttribute("data-score") || "";
    var title = anchor.getAttribute("data-title") || "";

    var pop = document.createElement("div");
    pop.className = "src-popup";
    pop.id = "src-popup";
    var head = '<div class="src-popup-head">' +
      '<span class="src-popup-title">' + escHtml(title) + '</span>' +
      tierBadge(tier) +
      (score ? '<span class="src-popup-score">類似度 ' + escHtml(score) + '</span>' : '') +
      '<button class="src-popup-close" aria-label="閉じる">×</button></div>';
    pop.innerHTML = head + '<div class="src-popup-body">読み込み中…</div>';
    document.body.appendChild(pop);
    _positionSourcePopup(pop, anchor);
    pop.querySelector(".src-popup-close").addEventListener("click", closeSourcePopup);
    document.addEventListener("mousedown", _srcOutsideClose, true);
    document.addEventListener("keydown", _srcEscClose, true);

    try {
      var res = await apiFetch("/learning/courses/" + state.courseId + "/source-chunk/" + encodeURIComponent(chunkId));
      var body = pop.querySelector(".src-popup-body");
      if (!body) return;
      if (res.ok) {
        var data = await res.json();
        var inner = "";
        if (data.section) inner += '<div class="src-popup-section">' + escHtml(data.section) + '</div>';
        // renderMaterialChunk は [[FORMULA_N]] を KaTeX 描画した自己完結 HTML を返す。
        inner += '<div class="material-chunk-text">' +
          renderMaterialChunk({ text: data.text, formulas: data.formulas }) + '</div>';
        body.innerHTML = inner;
      } else {
        body.textContent = "出典を取得できませんでした。";
      }
    } catch (_) {
      var b = pop.querySelector(".src-popup-body");
      if (b) b.textContent = "サーバーに接続できません。";
    }
  }

  // ── 説明バージョン ポップアップ(C層 Phase 2) ───────────────────────────
  // 1つの理論・概念に対する「標準の説明」と「各教員の説明」を並べて表示する。
  // 承認済み(teacher_approved)のみ。承認の厚みは段階ラベルで示し、点数は出さない。
  async function showComponentExplanations(anchor, componentId, label) {
    closeSourcePopup();
    if (!componentId || !state.courseId) return;
    const pop = document.createElement("div");
    pop.className = "src-popup";
    pop.id = "src-popup";
    pop.innerHTML = '<div class="src-popup-head">' +
      '<span class="src-popup-title">' + escHtml(label || "説明バージョン") + '</span>' +
      '<button class="src-popup-close" aria-label="閉じる">×</button></div>' +
      '<div class="src-popup-body">読み込み中…</div>';
    document.body.appendChild(pop);
    _positionSourcePopup(pop, anchor);
    pop.querySelector(".src-popup-close").addEventListener("click", closeSourcePopup);
    document.addEventListener("mousedown", _srcOutsideClose, true);
    document.addEventListener("keydown", _srcEscClose, true);
    try {
      const res = await apiFetch("/learning/courses/" + state.courseId +
        "/components/" + encodeURIComponent(componentId) + "/explanations");
      const body = pop.querySelector(".src-popup-body");
      if (!body) return;
      if (!res.ok) { body.textContent = "説明を取得できませんでした。"; return; }
      const data = await res.json();
      const items = (data && data.explanations) || [];
      if (!items.length) { body.textContent = "承認済みの説明はまだありません。"; return; }
      body.innerHTML = items.map(function (e) {
        const who = e.kind === "standard" ? "標準の説明" : ("教員: " + (e.author_name || "不明"));
        return '<div class="material-chunk" style="margin-bottom:10px">' +
          '<div class="material-chunk-loc">' + escHtml(who) +
          (e.endorsement_label ? ' ・ ' + escHtml(e.endorsement_label) : "") + '</div>' +
          (e.title ? '<div style="font-weight:600">' + escHtml(e.title) + '</div>' : "") +
          '<div class="material-chunk-text">' + escHtml(e.body || "") + '</div></div>';
      }).join("");
    } catch (_) {
      const b = pop.querySelector(".src-popup-body");
      if (b) b.textContent = "サーバーに接続できません。";
    }
  }

  function renderNextActions(actions) {
    var html = '<div class="learning-actions">';
    actions.forEach(function (action) {
      html += '<button class="suggest-btn learning-action-btn"'
        + ' data-suggest="' + escHtml(action.message || action.label || "") + '"'
        + ' data-support-action="' + escHtml(action.type || "") + '"'
        + (action.target_topic_id ? ' data-target-topic-id="' + escHtml(action.target_topic_id) + '"' : "")
        + '>' + escHtml(action.label || action.message || "次へ") + '</button>';
    });
    return html + "</div>";
  }

  // ── Render: Right panel ────────────────────────────────────────────
  function renderRightPanel() {
    renderContextTab();
    renderProgressTab();
    renderSourcesTab();
  }

  function renderContextTab() {
    const el = document.getElementById("tab-context");
    if (!state.course || !state.currentTopicId) {
      el.innerHTML = '<div class="ps"><div class="cc">トピックを選択してください</div></div>';
      return;
    }

    const topic = (state.course.topics || []).find(function (t) { return t.id === state.currentTopicId; });
    const chapter = (state.course.chapters || [])[topic ? topic.chapter_index : 0];

    let html = "";

    if (state.learningSupport && state.learningSupport.origin) {
      var origin = state.learningSupport.origin;
      html += '<div class="ps learning-support-panel"><h4>' + escHtml(state.learningSupport.status_label || "詳細説明中") + '</h4>';
      html += '<div class="cc"><div class="lb">元の学習パス</div>';
      html += '<strong>' + escHtml(origin.topic_title || "") + '</strong>';
      if (origin.chapter_title) html += '<br>' + escHtml(origin.chapter_title);
      html += '<div class="learning-support-inline-actions">';
      html += '<button class="suggest-btn" data-suggest="学習パスに戻る" data-support-action="return_to_learning_path" data-target-topic-id="' + escHtml(origin.topic_id || "") + '">学習パスに戻る</button>';
      html += '</div></div></div>';
    }

    // Current topic
    html += '<div class="ps"><h4>現在のトピック</h4>';
    html += '<div class="cc"><div class="lb">学習中</div>';
    html += '<strong style="color:var(--color-text-primary)">' + escHtml(topic ? topic.title : "") + "</strong><br>";
    html += escHtml(chapter ? chapter.title : "") + "</div></div>";

    // Prerequisites
    if (topic && topic.prerequisites && topic.prerequisites.length > 0) {
      html += '<div class="ps"><h4>このトピックの前提知識</h4><div class="pq">';
      topic.prerequisites.forEach(function (p) {
        const dotColor = p.status === "mastered" ? "#5DCAA5" :
                         p.status === "partial" ? "#EF9F27" : "#E24B4A";
        const stLabel = p.status === "mastered" ? "習得済み" :
                        p.status === "partial" ? "部分的" : "未着手";
        const stColor = p.status === "mastered" ? "var(--color-text-success)" :
                        p.status === "partial" ? "var(--color-text-warning)" : "var(--color-text-danger)";
        html += '<div class="pq-i" data-prereq="' + escHtml(p.name) + '">';
        html += '<span class="pq-d" style="background:' + dotColor + '"></span>';
        html += escHtml(p.name);
        html += '<span class="pq-st" style="color:' + stColor + '">' + stLabel + "</span></div>";
      });
      html += "</div></div>";
    }

    // Issue #145: 誤解は personal_layer から取得（マスターデータには含まれない）
    const personalLayer = state.personalLayer || {};
    const misconsByTopic = personalLayer.misconceptions_by_topic || {};
    const misconceptions = topic ? (misconsByTopic[topic.id] || []) : [];
    if (misconceptions.length > 0) {
      html += '<div class="ps"><h4>あなたの誤解メモ <span class="mc-bd">' + misconceptions.length + '件</span></h4>';
      html += '<div class="mc-layer-note">過去のチャットで指摘された理解の誤りです。</div>';
      misconceptions.forEach(function (m) {
        html += '<div class="cc mc-annotation"><div class="lb" style="color:#A32D2D">⚑ ' + escHtml(m.label || "訂正") + "</div>";
        html += escHtml(m.wrong) + "<br>→ " + escHtml(m.correct) + "</div>";
      });
      html += "</div>";
    }

    el.innerHTML = html;

    // Bind prerequisite clicks
    el.querySelectorAll("[data-prereq]").forEach(function (pEl) {
      pEl.addEventListener("click", function () {
        var payload = Session.contextPayload();
        sendMessage(this.getAttribute("data-prereq") + "について教えてください", payload);
      });
    });
    el.querySelectorAll('[data-support-action="return_to_learning_path"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var origin = Session.detourOrigin();
        var targetTopicId = this.getAttribute("data-target-topic-id") ||
          (origin && origin.topic_id);
        returnToLearningPath(targetTopicId);
      });
    });
  }

  function renderProgressTab() {
    const el = document.getElementById("tab-progress");
    if (!state.course) {
      el.innerHTML = "";
      return;
    }
    const p = state.course.progress || {};
    let html = "";

    html += '<div class="progress-head"><h3>あなたの学習の現在地</h3>' +
      '<p>コース全体のどこにいるか、寄り道中ならどこへ戻ればいいかが分かります。</p></div>';

    // ① コース行程（位置・復帰レイヤー）
    html += renderJourneyBlock();

    // ② 問いの軌跡（資産化レイヤー・mock）
    html += renderProblemTrails();

    // 既存の学習サマリ（章ごとの進捗・連続日数など）は補助情報として下に残す。
    html += '<div class="progress-head" style="margin:20px 0 8px"><h3 style="font-size:14px">学習サマリ</h3></div>';
    html += '<div class="ps"><div class="prog-ov">';
    html += '<div class="prog-card"><div class="val" style="color:var(--color-text-success)">' + (p.mastered_concepts || 0) + '</div><div class="lbl">習得済み概念</div></div>';
    html += '<div class="prog-card"><div class="val" style="color:var(--color-text-info)">' + (p.learning_concepts || 0) + '</div><div class="lbl">学習中</div></div>';
    html += '<div class="prog-card"><div class="val" style="color:var(--color-text-warning)">' + (p.misconceptions || 0) + '</div><div class="lbl">訂正された誤解</div></div>';
    html += '<div class="prog-card"><div class="val">' + (p.streak_days || 0) + '</div><div class="lbl">連続学習日数</div></div>';
    html += "</div></div>";

    html += '<div class="ps"><h4>章ごとの進捗</h4>';
    (state.course.chapters || []).forEach(function (ch, i) {
      const pct = ch.progress_pct || 0;
      const barColor = pct >= 100 ? "#5DCAA5" : pct > 0 ? "#378ADD" : "transparent";
      const label = pct >= 100 ? "完了" : pct > 0 ? pct + "%" : "--";
      const labelColor = pct > 0 ? "var(--color-text-secondary)" : "var(--color-text-tertiary)";
      html += '<div class="pi"><span style="width:110px">' + (i + 1) + ". " + escHtml(ch.title) + "</span>";
      html += '<div class="pb"><div class="pf" style="width:' + pct + "%;background:" + barColor + '"></div></div>';
      html += '<span style="font-size:11px;color:' + labelColor + '">' + label + "</span></div>";
    });
    html += "</div>";

    html += '<p class="lx-note">数を消すためのリストではありません。点数やランキングではなく、自分の関心がどこへ向かったかを見るための地図です。</p>';

    el.innerHTML = html;

    // interest-traces を未取得なら遅延ロード（取得後 renderProgressTab を再実行）。
    if (state.interestTraces === null) {
      loadInterestTraces();
    }
    bindProgressTabEvents(el);
  }

  // コース行程ブロック（全行程レール＋現在地＋一本パス＋戻りCTA）。
  function renderJourneyBlock() {
    var ordered = _getOrderedTopics();
    var total = ordered.length || 1;
    var chapters = state.course.chapters || [];
    var nChap = chapters.length || 1;

    var inDetour = Session.inDetour();
    var origin = inDetour ? Session.detourOrigin() : null;
    var baseTopicId = (origin && origin.topic_id) || state.currentTopicId;
    var baseIdx = ordered.findIndex(function (t) { return t.id === baseTopicId; });
    if (baseIdx < 0) baseIdx = 0;
    var baseTopic = ordered[baseIdx] || {};
    var curChap = (typeof baseTopic.chapter_index === "number") ? baseTopic.chapter_index : 0;
    var baseTitle = baseTopic.title || (origin && origin.topic_title) || "現在のトピック";

    // 到達率・進捗バーは実進捗（chapters[].progress_pct の平均）。現在地マーカーは現在位置。
    var pctVals = chapters.map(function (c) {
      return typeof c.progress_pct === "number" ? c.progress_pct : 0;
    });
    var reachedPct = pctVals.length
      ? Math.round(pctVals.reduce(function (a, b) { return a + b; }, 0) / pctVals.length) : 0;
    var positionPct = Math.round((baseIdx + 1) / total * 100);

    var html = '<div class="lx-journey"><div class="lx-journey-head">';
    html += '<span class="t">' + escHtml(state.course.title || "コース") + '</span>';
    html += '<span class="pct">全' + nChap + '章 ・ ' + reachedPct + '% 到達</span></div>';

    // 全行程レール：done バー＝実進捗、here マーカー＝現在位置（戻る場所）
    html += '<div class="lx-track" aria-hidden="true"><div class="lx-done" style="width:' + reachedPct + '%"></div>';
    for (var k = 1; k < nChap; k++) {
      html += '<div class="lx-tick" style="left:' + (k / nChap * 100) + '%"></div>';
    }
    html += '<div class="lx-here" style="left:' + positionPct + '%" title="本筋の現在地（戻る場所）"></div></div>';
    html += '<div class="lx-chapters">';
    chapters.forEach(function (ch, i) {
      html += '<span class="' + (i === curChap ? "cur" : "") + '">' + (i + 1) + "章</span>";
    });
    html += '</div>';

    // 本筋の現在地ラベル（セグメント精度があれば段番号を添える）
    var baseSeg = (origin && origin.segment_id) || 0;
    var baseLabel = baseTitle + (baseSeg > 0 ? " · 第" + (baseSeg + 1) + "段" : "");

    // 一本パス：本筋の現在地 →（寄り道中なら）寄り道先（入口となった問い本文）
    html += '<div class="lx-path">';
    html += '<span class="lx-seg course"><span class="pin"></span>' + escHtml(baseLabel) + '</span>';
    if (inDetour) {
      var detourRaw = (state.learningSupport &&
        (state.learningSupport.detour_label || state.learningSupport.status_label)) || "寄り道中";
      var detourLabel = detourRaw.length > 24 ? detourRaw.slice(0, 24) + "…" : detourRaw;
      html += '<span class="lx-conn detour">⟿</span>';
      html += '<span class="lx-seg detour"><span class="pin"></span>寄り道：' + escHtml(detourLabel) + '</span>';
    }
    html += '</div>';

    // 戻りCTA（寄り道中のみ）
    if (inDetour) {
      html += '<div class="lx-return-cta"><span class="r-text">いまは<b>寄り道中</b>。本筋の <b>' +
        escHtml(baseTitle) + '</b> に戻れます</span>' +
        '<button data-journey-return="' + escHtml(baseTopicId || "") + '">本筋へ戻る</button></div>';
    }
    html += '<p class="lx-journey-foot">寄り道に至るまでの細かな経路は記録していますが、ここでは戻り先だけを示します。</p>';
    html += '</div>';
    return html;
  }

  // 進捗タブのクリック配線（フィルタ・トレース操作・戻りCTA）。
  function bindProgressTabEvents(el) {
    el.querySelectorAll("[data-trace-filter]").forEach(function (b) {
      b.addEventListener("click", function () {
        state.traceFilter = this.getAttribute("data-trace-filter");
        renderProgressTab();
      });
    });
    el.querySelectorAll("[data-trace-return]").forEach(function (b) {
      b.addEventListener("click", function () {
        var text = this.getAttribute("data-trace-return");
        if (text) sendMessage(text, Session.contextPayload());
      });
    });
    el.querySelectorAll("[data-journey-return]").forEach(function (b) {
      b.addEventListener("click", function () {
        returnToLearningPath(this.getAttribute("data-journey-return"));
      });
    });
    // 「解決済みにする」: interest_traces の status を実 API で更新（Stage 3）。
    el.querySelectorAll("[data-trace-resolve]").forEach(function (b) {
      b.addEventListener("click", function () {
        resolveTrace(this.getAttribute("data-trace-resolve"));
      });
    });
    // 「なぜ気になった？」(Internalization Prompt): その場で理由を言語化させ payload に保存。
    el.querySelectorAll("[data-trace-why]").forEach(function (b) {
      b.addEventListener("click", function () {
        openInternalizationInput(this);
      });
    });
  }

  // 痕跡を「解決済み」にして問いの軌跡を再取得する（Stage 3）。
  async function resolveTrace(traceId) {
    if (!traceId || !state.courseId) return;
    try {
      var res = await apiFetch(
        "/learning/courses/" + state.courseId + "/interest-traces/" +
        encodeURIComponent(traceId) + "/resolve",
        { method: "POST", body: JSON.stringify({}) }
      );
      if (res.ok) loadInterestTraces();  // 再取得 → renderProgressTab
    } catch (_) { /* best-effort */ }
  }

  // Internalization Prompt: ボタンをインライン入力に差し替え、理由を保存する（Stage 4）。
  function openInternalizationInput(btn) {
    var traceId = btn.getAttribute("data-trace-id-why") || btn.getAttribute("data-trace-why");
    var box = document.createElement("div");
    box.className = "lx-why-input";
    box.innerHTML =
      '<textarea rows="2" placeholder="なぜ自分にとって重要だと感じた？（言葉にすると定着しやすくなります）"></textarea>' +
      '<div class="lx-why-actions"><button class="lx-ghost lx-why-save">保存</button>' +
      '<button class="lx-ghost secondary lx-why-cancel">やめる</button></div>';
    btn.replaceWith(box);
    var ta = box.querySelector("textarea");
    ta.focus();
    box.querySelector(".lx-why-cancel").addEventListener("click", function () {
      loadInterestTraces();  // 再描画で元に戻す
    });
    box.querySelector(".lx-why-save").addEventListener("click", async function () {
      var reason = (ta.value || "").trim();
      if (!reason) { ta.focus(); return; }
      try {
        await apiFetch(
          "/learning/courses/" + state.courseId + "/interest-traces/" +
          encodeURIComponent(traceId) + "/internalize",
          { method: "POST", body: JSON.stringify({ reason: reason }) }
        );
      } catch (_) { /* best-effort */ }
      loadInterestTraces();
    });
  }

  // ============ 出典タブ：信頼性レイヤーだけ（再設計）============
  // 3ブロックのみ: 全体格バナー / 根拠の合流(並列枝) / 参照した根拠カード。
  // 位置情報や問いの軌跡は持たせない（前者は進捗タブのコース行程、後者は問いの軌跡へ）。
  var TIER_LABEL = { approved: "承認", source: "原典", out_of_source: "参考" };
  function tierCls(tier) {
    return tier === "approved" ? "approved" : tier === "source" ? "source" : "oos";
  }

  function renderSourcesTab() {
    const el = document.getElementById("tab-sources");
    if (!state.course) {
      el.innerHTML = "";
      return;
    }
    let html = "";

    var overall = state.lastOverallTier;
    var srcs = state.lastSources || [];

    if (!overall) {
      html += '<div class="ps"><p class="lx-note" style="margin:0">質問するとこのタブに、回答が依拠した根拠と信頼性（tier）が表示されます。</p></div>';
    } else {
      // ① 全体格バナー（overall_tier）
      var oc = tierCls(overall);
      var note = (overall === "out_of_source")
        ? 'この回答は出典が提示できない<b>「参考」</b>情報です。教材の裏づけはないため、断定を避けます。'
        : 'この回答の格は <b>' + escHtml(TIER_LABEL[overall] || overall) +
          '</b>。複数の根拠のうち最も弱いものに合わせて、安全側で格付けしています。';
      html += '<div class="ps" style="border:none;padding:0">';
      html += '<div class="lx-overall ' + oc + '">' + lxTierIcon(overall) +
        '<span>' + note + '</span></div>';

      // ② 根拠の合流（並列の枝・順序なし）。最弱（=overall）に注記。
      if (srcs.length > 0) {
        // overall と同 tier の最初の根拠を「全体格を決めた最弱」として注記する。
        var weakestIdx = -1;
        srcs.forEach(function (s, i) {
          if (weakestIdx === -1 && s.tier === overall) weakestIdx = i;
        });
        html += '<p class="lx-sec-head">この回答が依拠した根拠</p>';
        html += '<div class="lx-merge"><div class="lx-merge-node"><span class="lx-ans">この回答</span></div>';
        html += '<div class="lx-merge-link" aria-hidden="true"></div><div class="lx-branches">';
        srcs.forEach(function (s, i) {
          var c = tierCls(s.tier);
          html += '<div class="lx-branch"><span class="lx-crumb ' + c + '"><span class="lx-sw"></span>' +
            escHtml(s.source_title || "不明な教材") + (s.tier === "out_of_source" ? "（根拠なし）" : "") + '</span>';
          if (i === weakestIdx && overall !== "approved") {
            html += '<span class="lx-weakest">← 全体格はこれに合わせる</span>';
          }
          html += '</div>';
        });
        html += '</div></div>';
        html += '<p class="lx-merge-foot">根拠は並列です。全体格は最も弱い根拠に安全側で引きずられます。</p>';
      } else if (overall === "out_of_source") {
        html += '<p class="lx-merge-foot">この回答に依拠できる教材根拠は見つかりませんでした。</p>';
      }

      // tier 凡例
      html += '<div class="lx-legend">' +
        '<span><i style="background:var(--lx-approved)"></i>承認</span>' +
        '<span><i style="background:var(--lx-source)"></i>原典</span>' +
        '<span><i style="background:var(--lx-oos)"></i>参考</span></div>';

      // ③ 参照した根拠カード
      if (srcs.length > 0) {
        html += '<p class="lx-sec-head">参照した根拠</p>';
        srcs.forEach(function (s) {
          var c = tierCls(s.tier);
          html += '<div class="lx-src ' + c + '"><div class="lx-src-top">';
          html += '<span class="lx-src-title">' + escHtml(s.source_title || "不明な教材") + '</span>';
          html += '<span class="lx-badge ' + c + '">' + escHtml(TIER_LABEL[s.tier] || s.tier) + '</span></div>';
          if (s.meta) html += '<div class="lx-src-meta">' + escHtml(s.meta) +
            (s.tier === "source" ? "（未承認）" : "") + '</div>';
          if (s.tier === "out_of_source") {
            html += '<div class="lx-oos-note">この点は登録教材に十分な根拠が見つかりませんでした。断定は避けます。</div>';
          } else {
            if (s.quote) html += '<div class="lx-src-quote">「' + escHtml(s.quote) + '」</div>';
            if (typeof s.score === "number") html += '<div class="lx-score">類似度 ' + s.score.toFixed(2) + '</div>';
          }
          html += '</div>';
        });
      }
      html += '</div>';
      html += '<p class="lx-note">出典タブは「いま見ている回答が信頼できるか」だけを扱います。過去の問いや寄り道は〈進捗〉タブへ。</p>';
    }

    // 既存: 登録済み教材 / 参照セクション（再設計対象外。コース教材情報として残す）
    const sources = state.course.sources || [];
    if (sources.length > 0) {
      html += '<div class="ps"><h4>登録済み教材</h4>';
      sources.forEach(function (s, i) {
        html += '<div class="src-item"><span class="src-num">' + (i + 1) + "</span>";
        html += '<div class="src-detail"><div class="src-title">' + escHtml(s.title) + "</div>";
        if (s.subtitle) html += '<div class="src-meta">' + escHtml(s.subtitle) + "</div>";
        if (s.license) html += '<div class="src-meta">' + escHtml(s.license) + "</div>";
        if (s.used_section) html += '<div class="src-used">' + escHtml(s.used_section) + "</div>";
        html += "</div></div>";
      });
      html += "</div>";
    }
    const refs = state.course.referenced_sections || [];
    if (refs.length > 0) {
      html += '<div class="ps"><h4>本セッションで参照されたセクション</h4>';
      refs.forEach(function (r) {
        html += '<div class="cc"><div class="lb">' + escHtml(r.source) + "</div>";
        html += '<strong style="color:var(--color-text-primary)">' + escHtml(r.section) + "</strong> " + escHtml(r.title) + "<br>";
        html += '<span style="font-size:11px">' + escHtml(r.note) + "</span></div>";
      });
      html += "</div>";
    }

    el.innerHTML = html;
  }

  // 全体格バナー用の小アイコン（tier色の丸＋!）。
  function lxTierIcon(tier) {
    var col = tier === "approved" ? "var(--lx-approved)" : tier === "source" ? "var(--lx-source)" : "var(--lx-oos)";
    return '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" style="flex:0 0 auto">' +
      '<circle cx="8" cy="8" r="7" stroke="' + col + '" stroke-width="1.4"/>' +
      '<path d="M8 4.5v4.5M8 11.2h.01" stroke="' + col + '" stroke-width="1.6" stroke-linecap="round"/></svg>';
  }

  // ============ 問いの軌跡（資産化レイヤー）— 進捗タブで使用 ============
  // 表示・「解決済みにする」は interest_traces の実データ（Stage 3）。
  var TRACE_KIND_LABEL = { question: "問い", detour: "寄り道", misconception: "誤答", raw: "記録" };
  var TRACE_KIND_CLS = { question: "q", detour: "detour", misconception: "mis", raw: "q" };
  var STATUS_META = {
    open: { label: "未解決", cls: "open" },
    revisited: { label: "再訪推奨", cls: "revisit" },
    resolved: { label: "解決済み", cls: "resolved" },
  };

  function renderProblemTrails() {
    var data = state.interestTraces;
    var html = '<div class="progress-head" style="margin:18px 0 8px"><h3 style="font-size:14px">問いの軌跡</h3></div>';

    if (!data) {
      html += '<p class="lx-note" style="margin-top:0">読み込み中…</p>';
      return html;
    }
    var traces = Array.isArray(data.traces) ? data.traces : [];

    // 再訪のころ合いカード（DecayPolicy の入口）
    var cue = data.revisit_cue;
    if (cue) {
      html += '<div class="lx-revisit"><div class="k">' + escHtml(cue.kind_label || "再訪のころ合い") + '</div>';
      html += '<div class="h">' + escHtml(cue.headline || "") + '</div>';
      html += '<div class="s">' + escHtml(cue.sub || "") + '</div></div>';
    }

    if (traces.length === 0) {
      html += '<p class="lx-note" style="margin-top:0">まだ記録された問いはありません。学習で生まれた問い・寄り道・誤答がここに溜まります。</p>';
      return html;
    }

    // 種別フィルタ（消化リストではなく関心の地図として）
    var f = state.traceFilter || "all";
    function fbtn(key, label, colorVar) {
      var on = f === key;
      var dot = colorVar ? '<i style="background:' + colorVar + '"></i>' : "";
      return '<button class="lx-filter" aria-pressed="' + on + '" data-trace-filter="' + key + '">' + dot + label + '</button>';
    }
    html += '<div class="lx-filters">' +
      fbtn("all", "すべて", "") +
      fbtn("question", "問い", "var(--lx-kind-q)") +
      fbtn("detour", "寄り道", "var(--lx-kind-detour)") +
      fbtn("misconception", "誤答", "var(--lx-kind-mis)") + '</div>';

    // トレース群（status を主役に）
    traces.forEach(function (t) {
      if (f !== "all" && t.kind !== f) return;
      var kc = TRACE_KIND_CLS[t.kind] || "q";
      var sm = STATUS_META[t.status] || { label: t.status || "", cls: "open" };
      html += '<div class="lx-trace ' + kc + '"><div class="lx-rail"></div><div class="lx-trace-body">';
      html += '<div class="lx-trace-top"><span class="lx-kind-tag">' + escHtml(TRACE_KIND_LABEL[t.kind] || t.kind || "記録") + '</span>';
      html += '<span class="lx-status-tag ' + sm.cls + '">' + escHtml(sm.label) + '</span></div>';
      html += '<div class="lx-trace-text">' + escHtml(t.text || "") + '</div>';
      if (t.context_label) html += '<div class="lx-trace-ctx">' + escHtml(t.context_label) + '</div>';
      // アクション。「この問いに戻る」「解決済みにする」は実データ配線済み（Stage 3）。
      if (t.status !== "resolved") {
        html += '<div class="lx-trace-actions">';
        html += '<button class="lx-ghost" data-trace-return="' + escHtml(t.text || "") + '">この問いに戻る</button>';
        html += '<button class="lx-ghost secondary" data-trace-resolve="' + escHtml(t.id || "") + '">解決済みにする</button>';
        // Internalization Prompt: 「なぜ自分に重要か」を言語化させ payload に保存（内発的動機の支援）。
        html += '<button class="lx-ghost secondary" data-trace-why="' + escHtml(t.id || "") + '">なぜ気になった？</button>';
        html += '</div>';
      }
      html += '</div></div>';
    });
    return html;
  }

  // 進捗タブに再訪推奨ドットを点す（タブ見出し）。
  function updateProgressTabDot() {
    var btn = document.querySelector('#tabBar button[data-tab="progress"]');
    if (!btn) return;
    var data = state.interestTraces;
    var hasRevisit = !!(data && Array.isArray(data.traces) &&
      data.traces.some(function (t) { return t.status === "revisited"; }));
    var dot = btn.querySelector(".lx-tab-dot");
    if (hasRevisit && !dot) {
      btn.insertAdjacentHTML("beforeend", '<span class="lx-tab-dot" title="再訪推奨あり"></span>');
    } else if (!hasRevisit && dot) {
      dot.remove();
    }
  }

  async function loadInterestTraces() {
    if (!state.courseId) return;
    state.interestTraces = {}; // 多重ロード防止のプレースホルダ
    try {
      var res = await apiFetch(
        "/learning/courses/" + state.courseId + "/interest-traces" +
        (state.currentTopicId ? "?topic_id=" + encodeURIComponent(state.currentTopicId) : "")
      );
      state.interestTraces = res.ok ? await res.json() : { traces: [] };
    } catch (_) {
      state.interestTraces = { traces: [] };
    }
    renderProgressTab();
    updateProgressTabDot();
  }

  // ── Topic Navigation ───────────────────────────────────────────────
  function _getOrderedTopics() {
    if (!state.course) return [];
    var topics = state.course.topics || [];
    var ordered = [];
    (state.course.chapters || []).forEach(function (ch, ci) {
      topics.filter(function (t) { return t.chapter_index === ci; }).forEach(function (t) {
        ordered.push(t);
      });
    });
    return ordered;
  }

  function getNextTopic() {
    if (!state.currentTopicId) return null;
    var ordered = _getOrderedTopics();
    var idx = ordered.findIndex(function (t) { return t.id === state.currentTopicId; });
    if (idx === -1 || idx >= ordered.length - 1) return null;
    return ordered[idx + 1];
  }

  function updateNextTopicBtn() {
    var btn = document.getElementById("next-topic-btn");
    if (!btn) return;
    var next = getNextTopic();
    if (next) {
      btn.style.display = "";
      btn.textContent = "確認して次へ";
      btn.title = "確認問題に回答して次のセクションへ進む: " + (next.title || "");
    } else {
      btn.style.display = "none";
    }
  }

  // ── Topic Material Fetch ──────────────────────────────────────────
  async function fetchTopicMaterial(courseId, topicId) {
    try {
      const res = await apiFetch("/learning/courses/" + courseId + "/topics/" + topicId + "/material");
      if (res.ok) {
        const data = await res.json();
        return data.chunks || [];
      }
    } catch (err) {
      // ネットワークエラー時は空を返す（UIを壊さない）
    }
    return [];
  }

  // detour（寄り道）を終了し、元の学習パス（アンカー）へ復帰する。
  // どの入口（本文ボタン / 右ペイン / レクチャー）から呼ばれても挙動を一本化する。
  async function returnToLearningPath(targetTopicId) {
    // L2 非破壊リターン: 寄り道に入った時点の正確な位置（segment/scroll）を origin から読み、
    // 復帰後に同じ位置へ戻す。寄り道の会話は各トピック履歴に残るため破壊しない。
    var origin = Session.detourOrigin();
    Session.clearDetour();
    var dest = targetTopicId || Session.anchorTopicId();
    renderSidebar();
    renderRightPanel();
    if (dest) {
      await selectTopic(dest, { keepDetour: true });
      if (origin && (origin.segment_id || origin.scroll_offset)) {
        Session.restorePosition(origin);
      }
    }
  }

  // ── Topic Selection ────────────────────────────────────────────────
  // opts.keepDetour: true のとき detour 状態を維持する（復帰処理側で既に確定済みの場合）。
  // 既定（サイドツリーからの手動選択など）では detour を閉じて再アンカーする。
  async function selectTopic(topicId, opts) {
    opts = opts || {};
    if (!opts.keepDetour) {
      // 手動ナビゲーション = 再アンカー。開いている寄り道は閉じる。
      Session.clearDetour();
    }
    // レクチャー中に別トピックへ移ると、再生中セグメントが旧トピックのまま残り
    // 状態が割れる。手動切替時はレクチャーを終了してテキスト表示へ戻す。
    if (lectureState.active && !opts.keepLecture) {
      deactivateLecture();
    }
    state.currentTopicId = topicId;
    state.chatMessages = [];
    state.topicMaterial = [];
    renderSidebar();
    renderChat();
    renderRightPanel();
    updateNextTopicBtn();
    refreshLectureAvailability();

    if (state.courseId && topicId) {
      // 教材チャンクとチャット履歴を並行取得
      const [material, history] = await Promise.all([
        fetchTopicMaterial(state.courseId, topicId),
        loadChatHistory(state.courseId, topicId),
      ]);
      state.topicMaterial = material;
      state.chatMessages = history;
      renderChat();
    }
  }

  function getCheckQuestionForCurrentTopic() {
    var topic = getCurrentTopic();
    var fallback = { question: "このセクションの要点を自分の言葉で説明してください。", model_answer: "", answer_requirements: [], explanation: "" };
    if (!topic) return fallback;
    var questions = topic.check_questions || topic.assessment_prompts || [];
    if (questions.length > 0) return normalizeCheckQuestion(questions[0]);
    if (topic.learning_objectives && topic.learning_objectives.length > 0) {
      return { question: "次の学習目標を説明してください: " + topic.learning_objectives[0], model_answer: "", answer_requirements: [], explanation: "" };
    }
    return fallback;
  }

  function normalizeCheckQuestion(item) {
    if (typeof item === "string") {
      return { question: item, model_answer: "", answer_requirements: [], explanation: "" };
    }
    item = item || {};
    return {
      question: item.question || item.text || "このセクションの要点を自分の言葉で説明してください。",
      model_answer: item.model_answer || item.answer || "",
      answer_requirements: Array.isArray(item.answer_requirements || item.required_elements)
        ? (item.answer_requirements || item.required_elements)
        : [],
      explanation: item.explanation || item.rationale || "",
    };
  }

  function openCheckModal() {
    if (!state.currentTopicId || state.checkingUnderstanding) return;
    var next = getNextTopic();
    if (!next) return;
    var existing = document.getElementById("check-overlay");
    if (existing) existing.remove();

    var topic = getCurrentTopic();
    var question = getCheckQuestionForCurrentTopic();
    var overlay = document.createElement("div");
    overlay.id = "check-overlay";
    overlay.className = "check-overlay";
    overlay.innerHTML =
      '<div class="check-box">' +
        '<div class="check-title">確認問題</div>' +
        '<div class="check-section">' + escHtml(topic ? topic.title : "") + '</div>' +
        '<div class="check-question">' + escHtml(question.question || "") + '</div>' +
        '<textarea id="check-answer" rows="5" placeholder="回答を入力してください"></textarea>' +
        '<div id="check-feedback" class="check-feedback"></div>' +
        '<div class="check-actions">' +
          '<button id="check-cancel" class="check-secondary">戻る</button>' +
          '<button id="check-submit" class="check-primary">回答する</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    document.getElementById("check-cancel").addEventListener("click", function () {
      overlay.remove();
    });
    document.getElementById("check-submit").addEventListener("click", submitCheckAnswer);
    var answer = document.getElementById("check-answer");
    if (answer) answer.focus();
  }

  async function submitCheckAnswer() {
    if (state.checkingUnderstanding) return;
    var answerEl = document.getElementById("check-answer");
    var feedbackEl = document.getElementById("check-feedback");
    var submitBtn = document.getElementById("check-submit");
    if (submitBtn && submitBtn.getAttribute("data-advance") === "true") {
      var directNext = getNextTopic();
      var directOverlay = document.getElementById("check-overlay");
      if (directOverlay) directOverlay.remove();
      // 前進は再アンカー（selectTopic 既定で detour を閉じ、レクチャー中なら終了する）。
      if (directNext) selectTopic(directNext.id);
      return;
    }
    var answer = answerEl ? answerEl.value.trim() : "";
    var question = getCheckQuestionForCurrentTopic();
    if (!answer) {
      if (feedbackEl) {
        feedbackEl.textContent = "回答を入力してください。";
        feedbackEl.className = "check-feedback fail";
      }
      return;
    }

    state.checkingUnderstanding = true;
    if (submitBtn) submitBtn.disabled = true;
    if (feedbackEl) {
      feedbackEl.textContent = "確認しています...";
      feedbackEl.className = "check-feedback";
    }
    try {
      var res = await apiFetch(
        "/learning/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/check",
        {
          method: "POST",
          body: JSON.stringify({ question: question.question || "", check_question: question, answer: answer }),
        }
      );
      if (!res.ok) throw new Error("check failed");
      var data = await res.json();
      if (data.passed) {
        var next = getNextTopic();
        var overlay = document.getElementById("check-overlay");
        if (overlay) overlay.remove();
        // 合格 → 次トピックへ再アンカー。detour 残はここで自動的に解消される。
        if (next) selectTopic(next.id);
      } else {
        if (feedbackEl) {
          feedbackEl.innerHTML = '<strong>もう一度確認しましょう。</strong><br>' +
            escHtml(data.feedback || "") +
            (data.answer_requirements && data.answer_requirements.length
              ? '<div class="check-model-answer"><span>回答に必要な要素</span><ul>' + data.answer_requirements.map(function (r) { return '<li>' + escHtml(r) + '</li>'; }).join("") + '</ul></div>'
              : "") +
            (data.model_answer ? '<div class="check-model-answer"><span>解答例</span>' + escHtml(data.model_answer) + '</div>' : "") +
            (data.explanation ? '<div class="check-model-answer"><span>解説</span>' + escHtml(data.explanation) + '</div>' : "");
          feedbackEl.className = "check-feedback fail";
        }
        if (submitBtn) {
          submitBtn.textContent = "理解したので次へ";
          submitBtn.disabled = false;
          submitBtn.setAttribute("data-advance", "true");
        }
      }
    } catch (err) {
      if (feedbackEl) {
        feedbackEl.textContent = "確認に失敗しました。もう一度お試しください。";
        feedbackEl.className = "check-feedback fail";
      }
      if (submitBtn) submitBtn.disabled = false;
    } finally {
      state.checkingUnderstanding = false;
    }
  }

  // ── Send Message ───────────────────────────────────────────────────
  async function sendMessage(text, actionPayload) {
    if (!text || state.sending || !state.currentTopicId) return;

    state.chatMessages.push({ role: "user", content: text });
    state.sending = true;
    renderChat();

    // Clear input
    const input = document.getElementById("chat-input");
    if (input) input.value = "";

    // detour 中の自由質問でも origin（復帰先）が失われないよう、明示 payload が
    // support_context を持たない場合は現在のセッション文脈を補完する。
    const payload = Object.assign({}, Session.contextPayload(), actionPayload || {});
    // L2: いまの読み位置（segment/scroll）。寄り道に入る瞬間の origin に正確に焼き込む。
    const anchorAtAsk = Session.currentAnchor();

    try {
      const res = await apiFetch("/learning/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/chat", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          history: state.chatMessages.slice(0, -1),
          position_anchor: anchorAtAsk,
          ...payload,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setLearningSupportFromResponse(data);
        // L2 寄り道先ラベル: 寄り道に入ったら、その入口となった問い本文を origin に添える
        // （status_label のような汎用語ではなく、何に寄り道したかを実体で示す）。
        if (Session.inDetour() && state.learningSupport) {
          state.learningSupport.detour_label = text;
          saveLearningSupportContext();
        }
        // L1/L2: tier・根拠・位置アンカーを保持（Stage M は mock を含む）。
        state.lastSources = data.sources || [];
        state.lastOverallTier = data.overall_tier || null;
        state.chatMessages.push({
          role: "assistant",
          content: data.answer,
          next_actions: data.next_actions || [],
          support_mode: data.support_mode || "",
          status_label: data.status_label || "",
          origin: data.origin || null,
          // ── B層 Stage M ──
          overall_tier: data.overall_tier || null,
          sources: data.sources || [],
          position_anchor: data.position_anchor || null,
          mock: isMock(data),
        });
        // Issue #145: 個人レイヤーの更新を反映する
        if (data.course_update) {
          if (data.course_update.personal_layer) {
            // レイヤー型更新: personal_layer をマージする
            if (!state.personalLayer) {
              state.personalLayer = { misconceptions_by_topic: {}, chat_anchors: {} };
            }
            const newPersonal = data.course_update.personal_layer;
            if (newPersonal.misconceptions_by_topic) {
              Object.assign(
                state.personalLayer.misconceptions_by_topic,
                newPersonal.misconceptions_by_topic
              );
            }
          } else {
            // 旧形式フォールバック（topics/concepts の直接更新）
            Object.assign(state.course, data.course_update);
          }
          renderSidebar();
          renderRightPanel();
        }
      } else {
        state.chatMessages.push({ role: "assistant", content: "エラーが発生しました。もう一度お試しください。" });
      }
    } catch (err) {
      state.chatMessages.push({ role: "assistant", content: "サーバーに接続できません。" });
    }

    state.sending = false;
    renderChat();
    renderRightPanel();  // L1: 直近回答の tier を Sources タブへ反映
  }

  // ── Tab Switching ──────────────────────────────────────────────────
  function initTabs() {
    document.getElementById("tabBar").addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn || !btn.dataset.tab) return;
      this.querySelectorAll("button").forEach(function (b) { b.classList.remove("on"); });
      btn.classList.add("on");
      document.querySelectorAll(".tp").forEach(function (p) { p.classList.remove("vis"); });
      var target = document.getElementById("tab-" + btn.dataset.tab);
      if (target) target.classList.add("vis");
    });
  }

  // ── Input handling ─────────────────────────────────────────────────
  function initInput() {
    const input = document.getElementById("chat-input");
    const btn = document.getElementById("send-btn");          // 教材に沿って質問（本筋維持）
    const exploreBtn = document.getElementById("send-explore"); // 自由に質問・探索（寄り道）
    const clearBtn = document.getElementById("chat-clear-btn");

    // intent_mode を付けて送信する。on_path は寄り道状態を畳んでから送る。
    function sendWith(mode) {
      var text = input.value.trim();
      if (!text) return;
      if (mode === "on_path") Session.clearDetour();
      sendMessage(text, { intent_mode: mode });
    }

    btn.addEventListener("click", function () { sendWith("on_path"); });
    if (exploreBtn) exploreBtn.addEventListener("click", function () { sendWith("explore"); });
    if (clearBtn) clearBtn.addEventListener("click", clearChatHistory);

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        // Enter＝現在のモードで送信（寄り道中なら explore、そうでなければ on_path）
        sendWith(Session.inDetour() ? "explore" : "on_path");
      }
    });
  }

  // ── Course selector (topbar dropdown) ───────────────────────────
  let _allCourses = []; // cached course list for re-render

  async function initCourseSelector() {
    const courses = await loadCourses();
    _allCourses = courses;

    const ownCourses = courses.filter(function (c) { return !c.is_enrollable; });
    const enrollableCourses = courses.filter(function (c) { return c.is_enrollable; });

    // No courses at all → empty state
    if (ownCourses.length === 0 && enrollableCourses.length === 0) {
      showNoCourseState();
      return;
    }

    // Resolve which course to load
    if (ownCourses.length > 0) {
      if (!state.courseId || !ownCourses.find(function (c) { return c.id === state.courseId; })) {
        state.courseId = ownCourses[0].id;
        localStorage.setItem("eg_course", state.courseId);
      }
    } else {
      state.courseId = null;
    }

    // Build <select> with optgroups
    renderCourseSelect(ownCourses, enrollableCourses);
    initCourseSelectHandler();

    // Load course if we have one
    if (state.courseId) {
      await loadAndRenderCourse();
    } else {
      showNoCourseState(enrollableCourses.length > 0);
    }
  }

  function renderCourseSelect(ownCourses, enrollableCourses) {
    const select = document.getElementById("course-select");
    let html = "";

    if (ownCourses.length === 0 && enrollableCourses.length > 0) {
      html += '<option value="" disabled selected>受講するコースを選択...</option>';
    }

    // マイコース optgroup
    if (ownCourses.length > 0) {
      html += '<optgroup label="マイコース">';
      ownCourses.forEach(function (c) {
        const selected = c.id === state.courseId ? " selected" : "";
        html += '<option value="' + escHtml(c.id) + '"' + selected + '>' + escHtml(c.title) + '</option>';
      });
      html += '</optgroup>';
    }

    // 受講可能なコース optgroup
    if (enrollableCourses.length > 0) {
      html += '<optgroup label="新しく受講可能なコース">';
      enrollableCourses.forEach(function (c) {
        html += '<option value="enroll:' + escHtml(c.id) + '">' + escHtml(c.title) + '</option>';
      });
      html += '</optgroup>';
    }

    select.innerHTML = html;
  }

  function initCourseSelectHandler() {
    const select = document.getElementById("course-select");

    select.addEventListener("change", function () {
      const val = this.value;
      if (val.indexOf("enroll:") === 0) {
        // 未受講コース → 受講処理
        const courseId = val.substring(7);
        enrollCourse(courseId);
      } else if (val && val !== state.courseId) {
        // マイコース → 切り替え
        switchCourse(val);
      }
    });
  }

  async function switchCourse(courseId) {
    state.courseId = courseId;
    localStorage.setItem("eg_course", courseId);

    // Clear current state
    state.currentTopicId = null;
    state.chatMessages = [];
    state.course = null;
    state.personalLayer = null;
    state.learningSupport = null;

    // Re-render with clean state
    renderSidebar();
    renderChat();
    renderRightPanel();

    // Update select active state
    const ownCourses = _allCourses.filter(function (c) { return !c.is_enrollable; });
    const enrollableCourses = _allCourses.filter(function (c) { return c.is_enrollable; });
    renderCourseSelect(ownCourses, enrollableCourses);

    // Re-enable chat input
    setChatEnabled(true);

    // Load the new course data
    await loadAndRenderCourse();
  }

  async function enrollCourse(courseId) {
    try {
      const res = await apiFetch("/learning/courses/" + courseId + "/enroll", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        // Refresh course list and switch to the new course
        const courses = await loadCourses();
        _allCourses = courses;
        await switchCourse(data.id);
      }
    } catch (_) { /* ignore */ }
  }

  function showNoCourseState(hasEnrollable = false) {
    const select = document.getElementById("course-select");
    if (!hasEnrollable) {
      select.innerHTML = '<option value="">コースなし</option>';
      select.disabled = true;
    } else {
      select.disabled = false; // 受講可能コースがあれば有効化しておく
    }

    var ca = document.getElementById("chat-area");
    if (hasEnrollable) {
      ca.innerHTML = '<div class="no-course-message">左上のプルダウンから受講するコースを選択してください。</div>';
    } else {
      ca.innerHTML = '<div class="no-course-message">現在受講可能なコースはありません。<br>教員がコースを公開するまでお待ちください。</div>';
    }

    setChatEnabled(false);

    var sb = document.getElementById("sidebar");
    sb.innerHTML = '<div class="sb-hd">コース未選択</div>';
  }

  function setChatEnabled(enabled) {
    var input = document.getElementById("chat-input");
    var btn = document.getElementById("send-btn");
    var clearBtn = document.getElementById("chat-clear-btn");
    if (input) {
      input.disabled = !enabled;
      input.placeholder = enabled ? "質問を入力してください..." : "コースを選択してください";
    }
    if (btn) btn.disabled = !enabled;
    if (clearBtn) clearBtn.disabled = !enabled || !state.currentTopicId || state.chatMessages.length === 0;
  }

  async function loadAndRenderCourse() {
    const courseData = await loadCourse(state.courseId);
    if (!courseData) return;
    const progress = await loadProgress(state.courseId);

    // Issue #145: マスターデータと個人レイヤーを分離して管理する
    state.course = courseData.master;
    state.personalLayer = courseData.personal;
    loadLearningSupportContext();
    if (progress) state.course.progress = progress;

    const course = state.course;

    // Set initial topic to first in_progress topic
    const inProgress = (course.topics || []).find(function (t) { return t.status === "in_progress"; });
    state.currentTopicId = inProgress ? inProgress.id : (course.topics && course.topics.length > 0 ? course.topics[0].id : null);

    // Restore select state in case it was disabled by empty state
    const select = document.getElementById("course-select");
    if (select) select.disabled = false;

    const streakEl = document.getElementById("streak");
    if (streakEl && progress) {
      streakEl.textContent = (progress.streak_days || 0) + "日連続学習中";
      streakEl.style.color = "var(--color-text-success)";
    }

    renderSidebar();
    if (state.currentTopicId) {
      const [material, history] = await Promise.all([
        fetchTopicMaterial(state.courseId, state.currentTopicId),
        loadChatHistory(state.courseId, state.currentTopicId),
      ]);
      state.topicMaterial = material;
      state.chatMessages = history;
    }
    renderChat();
    renderRightPanel();
    updateNextTopicBtn();
    refreshLectureAvailability();
  }

  // ── Utilities ──────────────────────────────────────────────────────
  function escHtml(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── Logout ───────────────────────────────────────────────────────
  function initLogout() {
    var btn = document.getElementById("logout-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        state.token = null;
        state.username = null;
        localStorage.removeItem("eg_token");
        localStorage.removeItem("eg_username");
        localStorage.removeItem("eg_course");
        renderAuth();
      });
    }
  }

  // ── Role-based UI setup ───────────────────────────────────────────
  function setupRoleUI() {
    // Show username
    var usernameEl = document.getElementById("username");
    if (usernameEl) usernameEl.textContent = state.username || "";

    // Show admin link for TEACHER / SYSTEM_ADMIN
    if (state.role === "TEACHER" || state.role === "SYSTEM_ADMIN") {
      var topbarR = document.querySelector(".topbar-r");
      if (topbarR && !document.getElementById("admin-link")) {
        var adminLink = document.createElement("a");
        adminLink.id = "admin-link";
        adminLink.href = "/admin.html";
        adminLink.textContent = "管理画面へ";
        adminLink.style.cssText = "color:var(--color-text-info);text-decoration:none;font-size:12px";
        var sep = document.createElement("span");
        sep.style.opacity = ".3";
        sep.textContent = "|";
        topbarR.insertBefore(sep, topbarR.firstChild);
        topbarR.insertBefore(adminLink, topbarR.firstChild);
      }
    }
  }

  // ── Init ───────────────────────────────────────────────────────────
  async function initApp() {
    if (!state.token) {
      renderAuth();
      return;
    }
    setupRoleUI();
    initTabs();
    initInput();
    initLogout();
    initGroups();
    await initCourseSelector();
    loadInvitationBadge();
  }

  // ── Groups (Issue #121) ────────────────────────────────────────────
  function initGroups() {
    var btn = document.getElementById("groups-btn");
    if (btn) btn.addEventListener("click", openGroupsModal);
  }

  async function loadInvitationBadge() {
    try {
      var res = await apiFetch("/me/invitations");
      if (!res.ok) return;
      var list = await res.json();
      var pending = (list || []).filter(function (i) { return i.status === "pending"; });
      var badge = document.getElementById("groups-badge");
      if (badge) {
        if (pending.length > 0) {
          badge.textContent = String(pending.length);
          badge.style.display = "";
        } else {
          badge.style.display = "none";
        }
      }
    } catch (_) { /* ignore */ }
  }

  function openGroupsModal() {
    if (document.getElementById("groups-overlay")) return;
    var overlay = document.createElement("div");
    overlay.id = "groups-overlay";
    overlay.className = "auth-overlay";
    overlay.innerHTML =
      '<div class="auth-box" style="width:440px;max-height:80vh;overflow-y:auto">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">' +
          '<h2 style="margin:0">グループ</h2>' +
          '<button id="groups-close" style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--color-text-secondary)">&times;</button>' +
        '</div>' +
        '<div id="groups-invitations-section">' +
          '<div style="font-size:12px;font-weight:600;color:var(--color-text-secondary);margin-bottom:6px">招待</div>' +
          '<div id="groups-invitations-list" style="margin-bottom:14px;font-size:13px">読み込み中...</div>' +
        '</div>' +
        '<div>' +
          '<div style="font-size:12px;font-weight:600;color:var(--color-text-secondary);margin-bottom:6px">招待コードで参加</div>' +
          '<form id="groups-join-form" style="display:flex;gap:6px;margin-bottom:14px">' +
            '<input id="groups-join-code" type="text" placeholder="招待コード" style="flex:1;padding:8px 10px;border:0.5px solid var(--color-border-secondary);border-radius:8px;font-size:13px" required>' +
            '<button type="submit" style="padding:8px 14px;border-radius:8px;background:var(--color-text-info);color:#fff;border:none;cursor:pointer;font-size:13px">参加</button>' +
          '</form>' +
        '</div>' +
        '<div>' +
          '<div style="font-size:12px;font-weight:600;color:var(--color-text-secondary);margin-bottom:6px">参加中のグループ</div>' +
          '<div id="groups-my-list" style="font-size:13px">読み込み中...</div>' +
        '</div>' +
        '<div id="groups-status" style="margin-top:10px;font-size:12px;min-height:16px"></div>' +
      '</div>';
    document.body.appendChild(overlay);

    document.getElementById("groups-close").addEventListener("click", closeGroupsModal);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeGroupsModal();
    });
    document.getElementById("groups-join-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var code = document.getElementById("groups-join-code").value.trim();
      if (code) joinGroupByCode(code);
    });

    refreshGroupsModal();
  }

  function closeGroupsModal() {
    var overlay = document.getElementById("groups-overlay");
    if (overlay) overlay.remove();
  }

  function setGroupsModalStatus(msg, isError) {
    var el = document.getElementById("groups-status");
    if (!el) return;
    el.textContent = msg || "";
    el.style.color = isError ? "var(--color-text-danger)" : "var(--color-text-success)";
  }

  async function refreshGroupsModal() {
    await Promise.all([renderInvitations(), renderMyGroups()]);
    loadInvitationBadge();
  }

  async function renderInvitations() {
    var container = document.getElementById("groups-invitations-list");
    if (!container) return;
    try {
      var res = await apiFetch("/me/invitations");
      if (!res.ok) { container.textContent = "読み込みに失敗しました"; return; }
      var invites = await res.json();
      var pending = (invites || []).filter(function (i) { return i.status === "pending"; });
      if (pending.length === 0) {
        container.innerHTML = '<div style="color:var(--color-text-tertiary)">保留中の招待はありません</div>';
        return;
      }
      var html = "";
      pending.forEach(function (inv) {
        html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px;border:0.5px solid var(--color-border-secondary);border-radius:8px;margin-bottom:6px">';
        html += '<div><div style="font-weight:500">' + escHtml(inv.group_name || "(グループ)") + '</div>';
        html += '<div style="font-size:11px;color:var(--color-text-tertiary)">招待者: ' + escHtml(inv.inviter_username || "?") + '</div></div>';
        html += '<div style="display:flex;gap:4px">';
        html += '<button data-action="accept" data-id="' + escHtml(inv.id) + '" style="padding:4px 10px;border-radius:6px;background:var(--color-text-info);color:#fff;border:none;cursor:pointer;font-size:12px">承諾</button>';
        html += '<button data-action="decline" data-id="' + escHtml(inv.id) + '" style="padding:4px 10px;border-radius:6px;background:var(--color-background-secondary);color:var(--color-text-primary);border:0.5px solid var(--color-border-secondary);cursor:pointer;font-size:12px">辞退</button>';
        html += '</div></div>';
      });
      container.innerHTML = html;
      container.querySelectorAll("button[data-action]").forEach(function (b) {
        b.addEventListener("click", function () {
          respondInvitation(b.getAttribute("data-id"), b.getAttribute("data-action"));
        });
      });
    } catch (_) {
      container.textContent = "読み込みに失敗しました";
    }
  }

  async function renderMyGroups() {
    var container = document.getElementById("groups-my-list");
    if (!container) return;
    try {
      var res = await apiFetch("/groups");
      if (!res.ok) { container.textContent = "読み込みに失敗しました"; return; }
      var groups = await res.json();
      if (!groups || groups.length === 0) {
        container.innerHTML = '<div style="color:var(--color-text-tertiary)">参加中のグループはありません</div>';
        return;
      }
      var html = "";
      groups.forEach(function (g) {
        html += '<div style="padding:8px;border:0.5px solid var(--color-border-secondary);border-radius:8px;margin-bottom:6px">';
        html += '<div style="font-weight:500">' + escHtml(g.name) + '</div>';
        if (g.description) {
          html += '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:2px">' + escHtml(g.description) + '</div>';
        }
        html += '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:2px">ロール: ' + escHtml(g.my_role || "member") + '</div>';
        if (g.invite_code) {
          html += '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:2px">招待コード: <code>' + escHtml(g.invite_code) + '</code></div>';
        }
        html += '</div>';
      });
      container.innerHTML = html;
    } catch (_) {
      container.textContent = "読み込みに失敗しました";
    }
  }

  async function respondInvitation(invitationId, action) {
    try {
      var res = await apiFetch("/me/invitations/" + invitationId + "/" + action, { method: "POST" });
      if (!res.ok) {
        var err = await res.json().catch(function () { return {}; });
        setGroupsModalStatus(err.detail || "処理に失敗しました", true);
        return;
      }
      setGroupsModalStatus(action === "accept" ? "グループに参加しました" : "招待を辞退しました", false);
      await refreshGroupsModal();
      // New group → refresh course list so group-shared courses appear
      if (action === "accept") {
        var courses = await loadCourses();
        _allCourses = courses;
        var ownCourses = courses.filter(function (c) { return !c.is_enrollable; });
        var enrollableCourses = courses.filter(function (c) { return c.is_enrollable; });
        renderCourseSelect(ownCourses, enrollableCourses);
      }
    } catch (_) {
      setGroupsModalStatus("処理に失敗しました", true);
    }
  }

  async function joinGroupByCode(code) {
    try {
      var res = await apiFetch("/groups/join-by-code", {
        method: "POST",
        body: JSON.stringify({ invite_code: code }),
      });
      if (!res.ok) {
        var err = await res.json().catch(function () { return {}; });
        setGroupsModalStatus(err.detail || "参加に失敗しました", true);
        return;
      }
      setGroupsModalStatus("グループに参加しました", false);
      document.getElementById("groups-join-code").value = "";
      await refreshGroupsModal();
      var courses = await loadCourses();
      _allCourses = courses;
      var ownCourses = courses.filter(function (c) { return !c.is_enrollable; });
      var enrollableCourses = courses.filter(function (c) { return c.is_enrollable; });
      renderCourseSelect(ownCourses, enrollableCourses);
    } catch (_) {
      setGroupsModalStatus("参加に失敗しました", true);
    }
  }

  // ── Expose sendPrompt globally for inline onclick ──────────────────
  window.sendPrompt = function (text) {
    sendMessage(text);
  };

  // ── Interactive Lecture Mode (Issue #66) ───────────────────────────
  const lectureState = {
    active: false,
    segments: [],
    currentSegmentIndex: 0,
    playing: false,
    audio: null,
    pausePositionMs: 0,
    sendingInterrupt: false,
    highlightTimer: null,
    voiceRecognition: null,
    loadingAudio: false, // TTS フェッチ中フラグ（連打による多重再生を防ぐ）
  };

  // 現トピックに再生可能な音声があるかを確認し、レクチャーボタンの有効/無効を更新する。
  // 音声生成は管理画面のみで行う方針のため、ここでは生成は一切トリガーしない。
  async function refreshLectureAvailability() {
    state.topicHasAudio = false;
    updateLectureToggleAvailability();
    if (!state.courseId || !state.currentTopicId) return;
    var requestedTopicId = state.currentTopicId;
    try {
      var res = await apiFetch(
        "/learning/lecture/courses/" + state.courseId + "/topics/" + requestedTopicId + "/audio-status"
      );
      if (res.ok) {
        var data = await res.json();
        // 取得中にトピックが切り替わっていたら結果を破棄する。
        if (state.currentTopicId === requestedTopicId) {
          state.topicHasAudio = !!data.has_audio;
        }
      }
    } catch (e) {
      /* 取得失敗時は無効化のまま（生成はさせない） */
    }
    updateLectureToggleAvailability();
  }

  function updateLectureToggleAvailability() {
    var btn = document.getElementById("lecture-toggle");
    if (!btn) return;
    // レクチャー再生中はトグル（テキストへ戻る）を常に許可する。
    var enabled = lectureState.active || state.topicHasAudio;
    btn.disabled = !enabled;
    if (enabled) {
      btn.classList.remove("disabled");
      btn.title = "レクチャーモード切替";
    } else {
      btn.classList.add("disabled");
      btn.title = "このトピックの音声はまだ生成されていません（管理画面で音声を生成してください）";
    }
  }

  function initLectureMode() {
    var toggleBtn = document.getElementById("lecture-toggle");
    var playBtn = document.getElementById("lecture-play");
    var prevBtn = document.getElementById("lecture-prev");
    var nextBtn = document.getElementById("lecture-next");
    var questionBtn = document.getElementById("lecture-question");
    var chatCloseBtn = document.getElementById("lecture-chat-close");
    var chatSendBtn = document.getElementById("lecture-chat-send");
    var chatInput = document.getElementById("lecture-chat-input");
    var chatMicBtn = document.getElementById("lecture-chat-mic");
    var resumeBtn = document.getElementById("lecture-chat-resume");
    var nextTopicBtn = document.getElementById("next-topic-btn");

    if (toggleBtn) toggleBtn.addEventListener("click", toggleLectureMode);
    updateLectureToggleAvailability();
    if (playBtn) playBtn.addEventListener("click", togglePlayPause);
    if (prevBtn) prevBtn.addEventListener("click", prevSegment);
    if (nextBtn) nextBtn.addEventListener("click", nextSegment);
    if (questionBtn) questionBtn.addEventListener("click", openInterruptChat);
    if (chatCloseBtn) chatCloseBtn.addEventListener("click", closeInterruptChat);
    if (chatSendBtn) chatSendBtn.addEventListener("click", sendInterruptMessage);
    if (chatMicBtn) chatMicBtn.addEventListener("click", toggleVoiceInput);
    if (resumeBtn) resumeBtn.addEventListener("click", resumeLecture);
    if (nextTopicBtn) nextTopicBtn.addEventListener("click", openCheckModal);

    if (chatInput) {
      chatInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          sendInterruptMessage();
        }
      });
    }
  }

  function toggleVoiceInput() {
    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    var micBtn = document.getElementById("lecture-chat-mic");

    // Stop if already recording
    if (lectureState.voiceRecognition) {
      lectureState.voiceRecognition.stop();
      return;
    }

    if (!SpeechRecognition) {
      alert("このブラウザは音声認識に対応していません。Chrome または Edge をお試しください。");
      return;
    }

    var recognition = new SpeechRecognition();
    recognition.lang = "ja-JP";
    recognition.interimResults = true;
    recognition.continuous = false;
    lectureState.voiceRecognition = recognition;

    recognition.onstart = function () {
      if (micBtn) micBtn.classList.add("recording");
    };

    recognition.onresult = function (event) {
      var transcript = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      var input = document.getElementById("lecture-chat-input");
      if (input) input.value = transcript;
    };

    recognition.onend = function () {
      lectureState.voiceRecognition = null;
      if (micBtn) micBtn.classList.remove("recording");
      var input = document.getElementById("lecture-chat-input");
      if (input && input.value.trim()) {
        sendInterruptMessage();
      }
    };

    recognition.onerror = function (event) {
      lectureState.voiceRecognition = null;
      if (micBtn) micBtn.classList.remove("recording");
      if (event.error === "not-allowed") {
        alert("マイクの使用が許可されていません。ブラウザのアドレスバーからマイクの許可を確認してください。");
      }
    };

    recognition.start();
  }

  // レクチャーモードを終了してテキスト表示へ戻す（トグル・トピック切替の両方から使う）。
  function deactivateLecture() {
    if (!lectureState.active) return;
    var toggleBtn = document.getElementById("lecture-toggle");
    var chatArea = document.getElementById("chat-area");
    var lectureContent = document.getElementById("lecture-content");
    var lecturePlayer = document.getElementById("lecture-player");
    var chatInput = document.getElementById("chat-input");
    var sendBtn = document.getElementById("send-btn");

    lectureState.active = false;
    stopPlayback();
    closeInterruptChat();
    if (toggleBtn) {
      toggleBtn.classList.remove("active");
      toggleBtn.innerHTML = "&#127897; レクチャー";
    }
    if (chatArea) chatArea.style.display = "";
    if (lectureContent) lectureContent.classList.remove("visible");
    if (lecturePlayer) {
      lecturePlayer.classList.remove("visible");
      lecturePlayer.style.display = "";
    }
    if (chatInput) chatInput.style.display = "";
    if (sendBtn) sendBtn.style.display = "";
    var mrOff = document.getElementById("material-region");
    var mbOff = document.getElementById("mode-bar");
    var exOff = document.getElementById("send-explore");
    if (mrOff) mrOff.style.display = "";
    if (mbOff) mbOff.style.display = "";
    if (exOff) exOff.style.display = "";
    renderModeBar();
    updateLectureToggleAvailability();
  }

  async function toggleLectureMode() {
    var toggleBtn = document.getElementById("lecture-toggle");
    var chatArea = document.getElementById("chat-area");
    var lectureContent = document.getElementById("lecture-content");
    var lecturePlayer = document.getElementById("lecture-player");
    var chatInput = document.getElementById("chat-input");
    var sendBtn = document.getElementById("send-btn");

    if (lectureState.active) {
      deactivateLecture();
      return;
    }

    if (!state.courseId || !state.currentTopicId) return;

    // 音声未生成のトピックではレクチャーを開始しない（音声生成は管理画面のみ）。
    if (!state.topicHasAudio) {
      updateLectureToggleAvailability();
      return;
    }

    // Activate lecture mode
    lectureState.active = true;
    toggleBtn.classList.add("active");
    toggleBtn.innerHTML = "&#128196; テキスト";
    chatArea.style.display = "none";
    chatInput.style.display = "none";
    sendBtn.style.display = "none";
    var mrOn = document.getElementById("material-region");
    var mbOn = document.getElementById("mode-bar");
    var exOn = document.getElementById("send-explore");
    if (mrOn) mrOn.style.display = "none";
    if (mbOn) mbOn.style.display = "none";
    if (exOn) exOn.style.display = "none";
    lectureContent.classList.add("visible");
    lecturePlayer.classList.add("visible");

    // Ensure player is actually visible (override any CSS hiding)
    if (lecturePlayer) {
      lecturePlayer.style.display = "flex";
    }

    // Load lecture sequence with failsafe
    try {
      await loadLectureSequence();

      // 【追加・重要】シーケンス読み込み完了後、自動的に再生を開始する
      if (lectureState.segments && lectureState.segments.length > 0) {
        await startPlayback();
      }
    } catch (err) {
      _restoreChatUI();
    }
  }

  function _restoreChatUI() {
    var chatInput = document.getElementById("chat-input");
    var sendBtn = document.getElementById("send-btn");
    if (chatInput) chatInput.style.display = "";
    if (sendBtn) sendBtn.style.display = "";
  }

  async function loadLectureSequence() {
    var lectureContent = document.getElementById("lecture-content");
    lectureContent.innerHTML = '<div class="mg ai"><div class="typing"><span></span><span></span><span></span></div></div>';

    try {
      var res = await apiFetch(
        "/learning/lecture/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/sequence"
      );
      if (!res.ok) throw new Error("Failed to load sequence");
      var data = await res.json();
      lectureState.segments = data.segments || [];
      lectureState.currentSegmentIndex = 0;
      renderLectureContent();
      updateLectureControls();
    } catch (err) {
      lectureContent.innerHTML = '<div class="mg ai" style="color:var(--color-text-danger)">レクチャーシーケンスの読み込みに失敗しました。</div>';
      _restoreChatUI();
    }
  }

  function renderLectureContent() {
    var lectureContent = document.getElementById("lecture-content");
    if (!lectureState.segments.length) {
      lectureContent.innerHTML = '<div class="mg ai" style="color:var(--color-text-tertiary)">このトピックにはレクチャーコンテンツがありません。</div>';
      return;
    }

    var html = "";
    lectureState.segments.forEach(function (seg, idx) {
      var cls = "lecture-segment";
      if (idx < lectureState.currentSegmentIndex) cls += " visible past";
      else if (idx === lectureState.currentSegmentIndex) cls += " visible current";

      var content = renderSegmentContent(seg);
      html += '<div class="' + cls + '" data-segment="' + idx + '">' + content + '</div>';
    });

    lectureContent.innerHTML = html;

    // Render KaTeX
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(lectureContent, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
          ],
          throwOnError: false,
        });
      } catch (e) { /* ignore */ }
    }

    // Scroll to current segment
    var currentEl = lectureContent.querySelector(".lecture-segment.current");
    if (currentEl) currentEl.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function renderSegmentContent(seg) {
    var rawText = seg.text || seg.display_text || seg.spoken_text || "";

    // 1. 残存する ![[xxx:yyy]] / [[xxx:yyy]] 埋め込みを除去（バックエンドで未解決のもの）
    rawText = rawText.replace(/!\[\[[^\]]+\]\]/g, "");
    rawText = rawText.replace(/\[\[(?!FORMULA_)[^\]]+\]\]/g, "");

    // 2. 本文全体を安全にエスケープ（数式プレースホルダーはこの後に置換）
    var text = escHtml(rawText);

    // 3. Markdown 処理
    text = text.replace(/^### (.+)$/gm, "<h4>$1</h4>");
    text = text.replace(/^## (.+)$/gm, "<h3>$1</h3>");
    text = text.replace(/^# (.+)$/gm, "<h2>$1</h2>");
    text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/^- (.+)$/gm, "<li>$1</li>");
    text = text.split("\n\n").map(function (p) { return "<p>" + p + "</p>"; }).join("");
    text = text.replace(/\n/g, "<br>");

    // 4. IDをキーにして確実に数式をはめ込む（正規表現を使わない堅牢な置換）
    if (seg.formulas && seg.formulas.length > 0) {
      seg.formulas.forEach(function(f) {
        var rendered = "";
        try {
          if (window.katex) {
            rendered = window.katex.renderToString(normalizeKatexFormula(f.latex, f.is_display === true), {
              displayMode: f.is_display === true,
              throwOnError: false,
            });
            var cls = f.is_display ? "lecture-formula-block visible" : "lecture-formula visible";
            rendered = '<span class="' + cls + '">' + rendered + '</span>';
          }
        } catch (e) {
          rendered = "<span>" + escHtml(f.latex) + "</span>";
        }
        // split/join を使って本文中の [[FORMULA_x]] を確実にHTMLに置換
        text = text.split(escHtml(f.id)).join(rendered);
      });
    }

    return text;
  }

  // ── Material chunk renderer（教材スタジオプレビュー互換）───────────
  function renderMaterialChunk(chunk) {
    var rawText = chunk.text || "";
    var formulas = chunk.formulas || [];
    var formulaById = {};
    formulas.forEach(function (formula, idx) {
      var id = formula.id || ("FORMULA_" + idx);
      var normalizedId = normalizeMaterialEvidenceId(id);
      formulaById[String(id)] = formula;
      formulaById[normalizedId] = formula;
      formulaById["[[" + normalizedId + "]]"] = formula;
      formulaById["FORMULA_" + idx] = formula;
      formulaById["[[" + "FORMULA_" + idx + "]]"] = formula;
    });

    var embedBlocks = [];
    var mathBlocks = [];
    function preserveMath(expr, display) {
      var idx = mathBlocks.length;
      mathBlocks.push({ expr: expr, display: display });
      return "\x00MATERIAL_MATH_" + idx + "\x00";
    }
    function preserveEmbed(kind, id, inline) {
      var idx = embedBlocks.length;
      embedBlocks.push({ kind: kind, id: id, inline: inline });
      return "\x00MATERIAL_EMBED_" + idx + "\x00";
    }

    var preserved = normalizeMaterialLineBreaks(rawText);
    preserved = preserved.replace(/!\[\[equation:\s*\[\[([^\]]+)\]\]\s*\]\]/g, "![[equation:$1]]");
    preserved = preserved.replace(/\[\[equation:\s*\[\[([^\]]+)\]\]\s*\]\]/g, "[[equation:$1]]");
    preserved = preserved.replace(/!\[\[([a-z_]+):([^\]]+)\]\]/g, function (_m, kind, id) {
      return preserveEmbed(kind, id, false);
    });
    preserved = preserved.replace(/\[\[([a-z_]+):([^\]]+)\]\]/g, function (_m, kind, id) {
      return preserveEmbed(kind, id, true);
    });
    preserved = preserved.replace(/\[\[([^\[\]:]+)\]\]/g, function (m, id) {
      var formula = formulaById[m] || formulaById[id] || formulaById[normalizeMaterialEvidenceId(id)];
      if (!formula) return m;
      return preserveMath(formula.latex || formula.summary || id, true);
    });
    preserved = preserved.replace(/\\\[([\s\S]+?)\\\]/g, function (_m, expr) {
      return preserveMath(expr, true);
    });
    preserved = preserved.replace(/\$\$([\s\S]+?)\$\$/g, function (_m, expr) {
      return preserveMath(expr, true);
    });
    preserved = preserved.replace(/\\\(([\s\S]+?)\\\)/g, function (_m, expr) {
      return preserveMath(expr, false);
    });
    preserved = preserved.replace(/\$([^\$\n]+?)\$/g, function (_m, expr) {
      return preserveMath(expr, false);
    });

    var html = escHtml(preserved);
    html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^# (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.split("\n\n").map(function (p) {
      return "<p>" + p.replace(/\n/g, "<br>") + "</p>";
    }).join("");

    html = html.replace(/\x00MATERIAL_MATH_(\d+)\x00/g, function (_m, idx) {
      var block = mathBlocks[parseInt(idx, 10)];
      return block ? renderMaterialKatex(block.expr, block.display) : "";
    });
    html = html.replace(/\x00MATERIAL_EMBED_(\d+)\x00/g, function (_m, idx) {
      var embed = embedBlocks[parseInt(idx, 10)];
      if (!embed) return "";
      var embedId = normalizeMaterialEvidenceId(embed.id);
      if (embed.kind === "equation") {
        var formula = formulaById[String(embed.id)] || formulaById[embedId];
        var body = renderMaterialEquationBody(formula);
        if (body) {
          return '<span class="ls-material-embed ls-material-formula-only" data-evidence-ref="equation:' + escHtml(embedId) + '">' +
            body +
          '</span>';
        }
        // 本体（LaTeX/reading/原文）が無い数式埋め込みは、学習者には不安を与える
        // 赤いエラーカードではなく、落ち着いた「準備中」表示にする。直前の本文に式の
        // 説明（- ラベル: 意味）が出ているため、ここでは控えめな注記に留める。
        return '<span class="ls-material-embed ls-material-formula-pending"' +
          ' data-evidence-ref="equation:' + escHtml(embedId) + '"' +
          ' title="この数式は本文を準備中です（' + escHtml(embedId || "数式") + '）">' +
          '数式は準備中です' +
        '</span>';
      }
      return '<span class="ls-material-embed ls-material-evidence-card ls-material-missing" data-evidence-ref="' + escHtml(embed.kind + ":" + embedId) + '">' +
        '<span class="ls-material-embed-kind">未解決</span>' +
        '<strong>' + escHtml(embed.kind + ":" + embed.id) + '</strong>' +
        '<span class="ls-material-embed-summary">このIDに対応する教材要素を取得できませんでした。</span>' +
      '</span>';
    });

    return html || "";
  }

  // Best available representation of an equation embed: rendered math when LaTeX
  // exists, else its reading (plain_text), else raw extracted text. "" only when
  // nothing renderable is available (issue 未解決の数式).
  function renderMaterialEquationBody(formula) {
    if (!formula) return "";
    var latex = formula.latex || formula.summary || "";
    if (latex) return renderMaterialKatex(latex, true);
    var plain = formula.plain_text || "";
    if (plain) return '<span class="ls-material-formula-plain">' + escHtml(plain) + '</span>';
    var raw = formula.raw_text || "";
    if (raw) return '<span class="ls-material-formula-raw" title="原文（未整形・要確認）">' + escHtml(raw) + '</span>';
    return "";
  }

  function renderMaterialKatex(expr, display) {
    var formula = normalizeKatexFormula(expr, display);
    if (!formula) return "";
    var cls = display ? "lecture-formula-block visible" : "lecture-formula visible";
    if (window.katex) {
      try {
        return '<span class="' + cls + '">' + window.katex.renderToString(formula, {
          displayMode: !!display,
          throwOnError: false,
          strict: "ignore",
          trust: false,
        }) + '</span>';
      } catch (e) {
        // Fall through to escaped fallback.
      }
    }
    return '<span class="' + cls + '"><code>' + escHtml(formula) + '</code></span>';
  }

  function normalizeMaterialEvidenceId(value) {
    return String(value || "")
      .trim()
      .replace(/^\[\[/, "")
      .replace(/\]\]$/, "")
      .replace(/^equation:/, "")
      .trim()
      // Collapse a duplicated "eq_" prefix from the legacy double-prefix bug
      // ("eq_eq_F2" → "eq_F2") so legacy and corrected ids resolve alike.
      .replace(/^(?:eq_){2,}/i, "eq_");
  }

  function normalizeMaterialLineBreaks(text) {
    return String(text || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .replace(/<br\s*\/?>/gi, "\n");
  }

  function normalizeKatexFormula(expr, display) {
    var formula = String(expr || "").trim();
    if (!formula) return "";
    formula = formula.replace(/\\(?:nonumber|notag)\b/g, "").trim();
    var hasEnv = /\\begin\{[^{}]+\}/.test(formula);
    var hasAlignment = /(^|[^\\])&/.test(formula) || /\\\\/.test(formula);
    if (display && hasAlignment && !hasEnv) {
      formula = "\\begin{aligned} " + formula + " \\end{aligned}";
    }
    return formula;
  }

  function updateLectureControls() {
    var label = document.getElementById("lecture-segment-label");
    var progressFill = document.getElementById("lecture-progress-fill");
    var playBtn = document.getElementById("lecture-play");

    var total = lectureState.segments.length;
    var current = lectureState.currentSegmentIndex + 1;

    if (label) label.textContent = "セグメント " + current + " / " + total;
    // 修正: セグメントの「完了度」ではなく、開始インデックスベースの進捗にする
    if (progressFill) progressFill.style.width = (total > 0 ? (lectureState.currentSegmentIndex / total * 100) : 0) + "%";

    if (playBtn) {
      playBtn.innerHTML = lectureState.playing ? "&#9646;&#9646;" : "&#9654;";
      playBtn.className = lectureState.playing ? "lecture-pause-btn" : "lecture-play-btn";
      playBtn.title = lectureState.playing ? "一時停止" : "再生";
    }
  }

  async function togglePlayPause() {
    if (lectureState.playing) {
      pausePlayback();
    } else {
      await startPlayback();
    }
  }

  async function startPlayback() {
    if (!lectureState.segments.length) return;
    // 音声フェッチ中の連打による多重再生を防ぐ。
    if (lectureState.loadingAudio) return;
    lectureState.loadingAudio = true;

    var seg = lectureState.segments[lectureState.currentSegmentIndex];

    // Show loading spinner before TTS fetch
    var lectureContent = document.getElementById("lecture-content");
    var currentEl = lectureContent
      ? lectureContent.querySelector('.lecture-segment[data-segment="' + lectureState.currentSegmentIndex + '"]')
      : null;
    if (currentEl) {
      currentEl.insertAdjacentHTML("afterbegin",
        '<div class="lecture-loading" id="lecture-loading">' +
        '<div class="typing"><span></span><span></span><span></span></div>' +
        '<span style="font-size:12px;color:var(--color-text-tertiary);margin-left:8px;">音声を準備中...</span></div>');
    }

    try {
      // Fetch TTS audio first (while spinner is visible)
      var ttsData = null;
      try {
        var res = await apiFetch(
          "/learning/lecture/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/tts",
          {
            method: "POST",
            body: JSON.stringify({ chunk_id: seg.chunk_id, voice: "alloy" }),
          }
        );
        if (res.ok) {
          ttsData = await res.json();
        }
      } catch (_ttsErr) {
        // TTS unavailable — will fall back to simulation
      }

      // Remove spinner
      var loadingEl = document.getElementById("lecture-loading");
      if (loadingEl) loadingEl.remove();

      // Now enter playing state and re-render with fade-in sentences
      lectureState.playing = true;
      updateLectureControls();
      renderLectureContent();

      if (ttsData) {
        var audioSrc = "data:" + (ttsData.content_type || "audio/mp3") + ";base64," + ttsData.audio_base64;

        if (lectureState.audio) {
          lectureState.audio.pause();
          lectureState.audio = null;
        }

        lectureState.audio = new Audio(audioSrc);

        lectureState.audio.addEventListener("ended", function () {
          stopHighlighting();
          autoAdvance();
        });

        lectureState.audio.addEventListener("timeupdate", function () {
          updateTimeDisplay();
        });

        // 【修正・重要】自動再生エラー（ブラウザブロック）の安全なハンドリング
        var playPromise = lectureState.audio.play();
        if (playPromise !== undefined) {
          playPromise.then(function() {
            // 再生成功時
          }).catch(function(error) {
            // ブラウザに自動再生をブロックされた場合
            console.warn("Autoplay blocked by browser:", error);
            lectureState.playing = false;
            updateLectureControls();

            // ユーザーに再生ボタンを押すよう案内を出す
            var currentEl = document.querySelector(".lecture-segment.current");
            if (currentEl && !document.getElementById("autoplay-warning")) {
              currentEl.insertAdjacentHTML("afterbegin",
                '<div id="autoplay-warning" style="color: var(--color-text-danger); background: #fcebeb; padding: 10px; border-radius: 8px; margin-bottom: 12px; font-size: 12px;">' +
                'ブラウザの制限により自動再生がブロックされました。下の「▶（再生）」ボタンを押して開始してください。</div>'
              );
            }
          });
        }
      } else {
        // No TTS available, simulate with timer
        simulatePlayback(seg);
      }
    } catch (err) {
      // Remove spinner if still present
      var loadingEl2 = document.getElementById("lecture-loading");
      if (loadingEl2) loadingEl2.remove();
      lectureState.playing = false;
      updateLectureControls();
      _restoreChatUI();
    } finally {
      lectureState.loadingAudio = false;
    }
  }

  function simulatePlayback(seg) {
    // Estimate reading time: ~300 chars/min for Japanese
    var chars = (seg.spoken_text || seg.text || "").length;
    var durationMs = Math.max(3000, chars * 200);
    var startTime = Date.now();

    lectureState.highlightTimer = setInterval(function () {
      var elapsed = Date.now() - startTime;
      var timeEl = document.getElementById("lecture-time");
      if (timeEl) {
        var sec = Math.floor(elapsed / 1000);
        var min = Math.floor(sec / 60);
        timeEl.textContent = min + ":" + (sec % 60 < 10 ? "0" : "") + (sec % 60);
      }

      if (elapsed >= durationMs) {
        stopHighlighting();
        autoAdvance();
      }
    }, 200);
  }

  function pausePlayback() {
    lectureState.playing = false;
    if (lectureState.audio) {
      lectureState.pausePositionMs = Math.floor((lectureState.audio.currentTime || 0) * 1000);
      lectureState.audio.pause();
    }
    stopHighlighting();
    updateLectureControls();
  }

  function stopPlayback() {
    lectureState.playing = false;
    if (lectureState.audio) {
      lectureState.audio.pause();
      lectureState.audio = null;
    }
    lectureState.pausePositionMs = 0;
    stopHighlighting();
    updateLectureControls();
  }

  function autoAdvance() {
    if (lectureState.currentSegmentIndex < lectureState.segments.length - 1) {
      lectureState.currentSegmentIndex++;
      renderLectureContent();
      updateLectureControls();
      if (lectureState.playing) startPlayback();
    } else {
      lectureState.playing = false;
      updateLectureControls();
      onLectureComplete();
    }
  }

  // レクチャー最終セグメント到達時、テキストパスと同じ確認フローへ合流する。
  function onLectureComplete() {
    var lectureContent = document.getElementById("lecture-content");
    if (lectureContent && !document.getElementById("lecture-complete-banner")) {
      var next = getNextTopic();
      var label = next ? "確認問題に進む" : "レクチャー完了";
      lectureContent.insertAdjacentHTML("beforeend",
        '<div class="lecture-complete" id="lecture-complete-banner">' +
        '<p>このトピックのレクチャーは以上です。</p>' +
        '<button class="suggest-btn" id="lecture-complete-next">' + label + '</button>' +
        '</div>');
      var btn = document.getElementById("lecture-complete-next");
      if (btn) {
        btn.addEventListener("click", function () {
          if (getNextTopic()) {
            openCheckModal();
          } else {
            deactivateLecture();
          }
        });
      }
    }
  }

  function prevSegment() {
    if (lectureState.currentSegmentIndex > 0) {
      stopPlayback();
      lectureState.currentSegmentIndex--;
      renderLectureContent();
      updateLectureControls();
    }
  }

  function nextSegment() {
    if (lectureState.currentSegmentIndex < lectureState.segments.length - 1) {
      stopPlayback();
      lectureState.currentSegmentIndex++;
      renderLectureContent();
      updateLectureControls();
    }
  }

  function startHighlighting() {
    // No-op: segment-based coloring is handled by CSS classes
  }

  function stopHighlighting() {
    if (lectureState.highlightTimer) {
      clearInterval(lectureState.highlightTimer);
      lectureState.highlightTimer = null;
    }
  }

  function updateTimeDisplay() {
    if (!lectureState.audio) return;
    var timeEl = document.getElementById("lecture-time");
    if (timeEl) {
      var sec = Math.floor(lectureState.audio.currentTime);
      var min = Math.floor(sec / 60);
      timeEl.textContent = min + ":" + (sec % 60 < 10 ? "0" : "") + (sec % 60);
    }
  }

  // ── Interrupt Chat ──────────────────────────────────────────────
  // 割込みチャットはレクチャー（origin=現セグメント）からの寄り道。会話は本トピックの
  // state.chatMessages と同一スレッドで管理し、テキスト表示へ戻っても継続して見える。
  function openInterruptChat() {
    pausePlayback();
    var popup = document.getElementById("lecture-chat-popup");
    popup.classList.add("visible");
    renderInterruptMessages();
    var input = document.getElementById("lecture-chat-input");
    if (input) input.focus();
  }

  function closeInterruptChat() {
    // Stop any active voice recognition before closing
    if (lectureState.voiceRecognition) {
      lectureState.voiceRecognition.abort();
      lectureState.voiceRecognition = null;
      var micBtn = document.getElementById("lecture-chat-mic");
      if (micBtn) micBtn.classList.remove("recording");
    }
    var popup = document.getElementById("lecture-chat-popup");
    if (popup) popup.classList.remove("visible");
  }

  // ポップアップへ会話スレッドを描画し、AI 応答のボタンを bind する（型付き送信）。
  function renderInterruptMessages(pendingUserText) {
    var messages = document.getElementById("lecture-chat-messages");
    if (!messages) return;
    var html = '<div class="mg ai">講義を一時停止しました。ご質問をどうぞ。</div>';
    state.chatMessages.forEach(function (m) {
      if (m.role === "user") {
        html += '<div class="mg usr">' + escHtml(m.content) + '</div>';
      } else {
        html += '<div class="mg ai">' + renderAiContent(m.content, m) + '</div>';
      }
    });
    if (pendingUserText) {
      html += '<div class="mg usr">' + escHtml(pendingUserText) + '</div>';
      html += '<div class="mg ai"><div class="typing"><span></span><span></span><span></span></div></div>';
    }
    messages.innerHTML = html;

    // AI 応答内のアクションボタンは、押すとさらに割込み質問として送信する。
    messages.querySelectorAll(".suggest-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var text = this.getAttribute("data-suggest") || this.textContent.replace(/\s*↗$/, "");
        if (text) sendInterruptMessage(text);
      });
    });

    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(messages, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
          ],
          throwOnError: false,
        });
      } catch (e) { /* ignore */ }
    }
    messages.scrollTop = messages.scrollHeight;
  }

  async function sendInterruptMessage(presetText) {
    var input = document.getElementById("lecture-chat-input");
    var message = (presetText != null ? presetText : (input ? input.value : "")).trim();
    if (!message || lectureState.sendingInterrupt) return;
    if (input && presetText == null) input.value = "";

    lectureState.sendingInterrupt = true;
    var priorHistory = state.chatMessages.slice();
    renderInterruptMessages(message);

    var seg = lectureState.segments[lectureState.currentSegmentIndex];
    var messages = document.getElementById("lecture-chat-messages");

    try {
      var res = await apiFetch(
        "/learning/lecture/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/interrupt",
        {
          method: "POST",
          body: JSON.stringify({
            message: message,
            current_chunk_id: seg ? seg.chunk_id : "",
            pause_position_ms: lectureState.pausePositionMs,
            history: priorHistory,
          }),
        }
      );

      if (res.ok) {
        var data = await res.json();
        // 同一スレッドへ追記（テキスト表示へ戻っても継続して見える）。
        state.chatMessages.push({ role: "user", content: message });
        state.chatMessages.push({
          role: "assistant",
          content: data.answer,
          next_actions: data.next_actions || [],
        });
        renderInterruptMessages();

        if (data.course_update && state.course) {
          Object.assign(state.course, data.course_update);
          renderSidebar();
          renderRightPanel();
        }
      } else if (messages) {
        var typingEl = messages.querySelector(".typing");
        if (typingEl) typingEl.parentElement.remove();
        messages.innerHTML += '<div class="mg ai" style="color:var(--color-text-danger)">エラーが発生しました。</div>';
      }
    } catch (err) {
      if (messages) {
        var typingEl2 = messages.querySelector(".typing");
        if (typingEl2) typingEl2.parentElement.remove();
        messages.innerHTML += '<div class="mg ai" style="color:var(--color-text-danger)">サーバーに接続できません。</div>';
      }
    } finally {
      lectureState.sendingInterrupt = false;
    }
  }

  function resumeLecture() {
    closeInterruptChat();
    startPlayback();
  }

  // ── Init ───────────────────────────────────────────────────────────
  async function initApp() {
    if (!state.token) {
      renderAuth();
      return;
    }
    setupRoleUI();
    initTabs();
    initInput();
    initLogout();
    initLectureMode();
    await initCourseSelector();
  }

  // Boot
  document.addEventListener("DOMContentLoaded", function () {
    renderAuth();
    if (state.token) initApp();
  });
})();
