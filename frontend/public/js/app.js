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
    sending: false,
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

  // ── Render: Sidebar ────────────────────────────────────────────────
  function renderSidebar() {
    const sb = document.getElementById("sidebar");
    if (!state.course) {
      sb.innerHTML = '<div class="sb-hd">コースを選択してください</div>';
      return;
    }
    const course = state.course;
    let html = '<div class="sb-hd">学習パス</div>';

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

        html += '<div class="' + cls + '" data-topic="' + t.id + '" style="padding-left:36px">';
        html += escHtml(t.title) + annotationBadge;
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
  function _getFirstTopicTitle() {
    if (!state.course || !state.currentTopicId) return null;
    var topic = (state.course.topics || []).find(function (t) { return t.id === state.currentTopicId; });
    return topic ? topic.title : null;
  }

  function _renderInitialSuggestions() {
    var courseTitle = state.course ? escHtml(state.course.title || "") : "";
    var topicTitle = _getFirstTopicTitle();
    var topicLabel = topicTitle ? escHtml(topicTitle) : "最初のトピック";

    var html = '<div class="mg ai">';
    html += "「" + courseTitle + "」の学習サポートへようこそ！<br>";
    html += "現在のあなたのレベルと前提知識に合わせてサポートします。何から始めますか？";
    html += "</div>";
    html += '<div class="initial-suggestions">';
    html += '<button class="suggest-btn initial-suggest-btn" data-suggest="' + topicLabel + 'の学習を開始する">';
    html += topicLabel + "の学習を開始する</button>";
    html += '<button class="suggest-btn initial-suggest-btn" data-suggest="このコースに必要な前提知識を確認する">';
    html += "このコースに必要な前提知識を確認する</button>";
    html += "</div>";
    return html;
  }

  function renderChat() {
    const ca = document.getElementById("chat-area");
    if (!state.course || !state.currentTopicId) {
      ca.innerHTML = '<div class="mg ai" style="color:var(--color-text-tertiary)">左のサイドバーからトピックを選択してください。</div>';
      return;
    }

    let html = "";

    // 教材チャンクをチャット上部に静的表示（RAG検索不使用）
    if (state.topicMaterial && state.topicMaterial.length > 0) {
      html += '<div class="material-block">';
      html += '<div class="material-block-header">📖 教材</div>';
      state.topicMaterial.forEach(function (chunk) {
        html += '<div class="material-chunk">';
        if (chunk.chapter || chunk.section) {
          var loc = [chunk.chapter, chunk.section].filter(Boolean).join(" › ");
          html += '<div class="material-chunk-loc">' + escHtml(loc) + '</div>';
        }
        html += '<div class="material-chunk-text">' + escHtml(chunk.text) + '</div>';
        html += '</div>';
      });
      html += '</div>';
    }

    // 初期状態（チャット履歴なし）ならサジェストUIを表示
    if (state.chatMessages.length === 0 && !state.sending) {
      html += _renderInitialSuggestions();
    }

    state.chatMessages.forEach(function (msg) {
      if (msg.role === "user") {
        html += '<div class="mg usr">' + escHtml(msg.content) + "</div>";
      } else {
        html += '<div class="mg ai">' + renderAiContent(msg.content) + "</div>";
      }
    });

    if (state.sending) {
      html += '<div class="mg ai"><div class="typing"><span></span><span></span><span></span></div></div>';
    }

    ca.innerHTML = html;
    ca.scrollTop = ca.scrollHeight;

    // Bind suggest buttons (drill-down + initial suggestions)
    ca.querySelectorAll(".suggest-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var suggest = this.getAttribute("data-suggest") || this.textContent.replace(/\s*↗$/, "");
        sendMessage(suggest);
      });
    });

    // Render KaTeX for any remaining raw LaTeX (fallback)
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(ca, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
          ],
          throwOnError: false,
        });
      } catch (e) { /* KaTeX not yet loaded */ }
    }
  }

  function renderAiContent(text) {
    // Preserve LaTeX expressions before HTML escaping
    var latexBlocks = [];
    var preserved = text;

    // Preserve display math $$...$$ first
    preserved = preserved.replace(/\$\$([\s\S]+?)\$\$/g, function (m, expr) {
      var idx = latexBlocks.length;
      latexBlocks.push({ display: true, expr: expr });
      return "\x00LATEX_BLOCK_" + idx + "\x00";
    });
    // Preserve inline math $...$
    preserved = preserved.replace(/\$([^\$\n]+?)\$/g, function (m, expr) {
      var idx = latexBlocks.length;
      latexBlocks.push({ display: false, expr: expr });
      return "\x00LATEX_BLOCK_" + idx + "\x00";
    });

    // Extract drill-down suggestions [〇〇について詳しく聞く] before escaping
    var suggestions = [];
    preserved = preserved.replace(/\[([^\]]*について詳しく聞く[^\]]*)\]/g, function (_, s) {
      suggestions.push(s);
      return "\x00SUGGEST_" + (suggestions.length - 1) + "\x00";
    });

    // Escape HTML
    var html = escHtml(preserved);
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Inline code (but not LaTeX placeholders)
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Line breaks → paragraphs
    html = html.split("\n\n").map(function (p) { return "<p>" + p + "</p>"; }).join("");
    html = html.replace(/\n/g, "<br>");

    // Restore LaTeX blocks
    html = html.replace(/\x00LATEX_BLOCK_(\d+)\x00/g, function (_, idx) {
      var block = latexBlocks[parseInt(idx)];
      try {
        return window.katex
          ? window.katex.renderToString(block.expr, { displayMode: block.display, throwOnError: false })
          : (block.display ? "$$" + block.expr + "$$" : "$" + block.expr + "$");
      } catch (e) {
        return block.display ? "$$" + escHtml(block.expr) + "$$" : "$" + escHtml(block.expr) + "$";
      }
    });

    // Restore suggestions as placeholder (remove from inline text)
    html = html.replace(/\x00SUGGEST_(\d+)\x00/g, "");

    // Render suggestion buttons
    if (suggestions.length > 0) {
      html += '<div class="dd">';
      suggestions.forEach(function (s) {
        html += '<button class="suggest-btn" data-suggest="' + escHtml(s) + '">' + escHtml(s) + ' ↗</button>';
      });
      html += "</div>";
    }

    return html;
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
        sendMessage(this.getAttribute("data-prereq") + "について教えてください");
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

    // Overview cards
    html += '<div class="ps"><h4>全体の概要</h4><div class="prog-ov">';
    html += '<div class="prog-card"><div class="val" style="color:var(--color-text-success)">' + (p.mastered_concepts || 0) + '</div><div class="lbl">習得済み概念</div></div>';
    html += '<div class="prog-card"><div class="val" style="color:var(--color-text-info)">' + (p.learning_concepts || 0) + '</div><div class="lbl">学習中</div></div>';
    html += '<div class="prog-card"><div class="val" style="color:var(--color-text-warning)">' + (p.misconceptions || 0) + '</div><div class="lbl">訂正された誤解</div></div>';
    html += '<div class="prog-card"><div class="val">' + (p.streak_days || 0) + '</div><div class="lbl">連続学習日数</div></div>';
    html += "</div></div>";

    // Chapter progress
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

    // Recent sessions
    if (p.sessions && p.sessions.length > 0) {
      html += '<div class="ps"><h4>最近のセッション</h4>';
      p.sessions.forEach(function (s) {
        html += '<div class="sess-item">';
        html += '<span class="sess-date">' + escHtml(s.date) + "</span>";
        html += '<span class="sess-topic">' + escHtml(s.topic) + "</span>";
        html += '<span class="sess-dur">' + escHtml(s.duration) + "</span></div>";
      });
      html += "</div>";
    }

    el.innerHTML = html;
  }

  function renderSourcesTab() {
    const el = document.getElementById("tab-sources");
    if (!state.course) {
      el.innerHTML = "";
      return;
    }
    let html = "";

    // Registered materials
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

    // Referenced sections
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
      btn.title = "次のトピック: " + (next.title || "");
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

  // ── Topic Selection ────────────────────────────────────────────────
  async function selectTopic(topicId) {
    state.currentTopicId = topicId;
    state.chatMessages = [];
    state.topicMaterial = [];
    renderSidebar();
    renderChat();
    renderRightPanel();
    updateNextTopicBtn();

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

  // ── Send Message ───────────────────────────────────────────────────
  async function sendMessage(text) {
    if (!text || state.sending || !state.currentTopicId) return;

    state.chatMessages.push({ role: "user", content: text });
    state.sending = true;
    renderChat();

    // Clear input
    const input = document.getElementById("chat-input");
    if (input) input.value = "";

    try {
      const res = await apiFetch("/learning/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/chat", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          history: state.chatMessages.slice(0, -1),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        state.chatMessages.push({ role: "assistant", content: data.answer });
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
    const btn = document.getElementById("send-btn");

    btn.addEventListener("click", function () {
      sendMessage(input.value.trim());
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        sendMessage(input.value.trim());
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
    if (input) {
      input.disabled = !enabled;
      input.placeholder = enabled ? "質問を入力してください..." : "コースを選択してください";
    }
    if (btn) btn.disabled = !enabled;
  }

  async function loadAndRenderCourse() {
    const courseData = await loadCourse(state.courseId);
    if (!courseData) return;
    const progress = await loadProgress(state.courseId);

    // Issue #145: マスターデータと個人レイヤーを分離して管理する
    state.course = courseData.master;
    state.personalLayer = courseData.personal;
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
      state.chatMessages = await loadChatHistory(state.courseId, state.currentTopicId);
    }
    renderChat();
    renderRightPanel();
    updateNextTopicBtn();
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
    interruptHistory: [],
    highlightTimer: null,
    voiceRecognition: null,
  };

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
    if (playBtn) playBtn.addEventListener("click", togglePlayPause);
    if (prevBtn) prevBtn.addEventListener("click", prevSegment);
    if (nextBtn) nextBtn.addEventListener("click", nextSegment);
    if (questionBtn) questionBtn.addEventListener("click", openInterruptChat);
    if (chatCloseBtn) chatCloseBtn.addEventListener("click", closeInterruptChat);
    if (chatSendBtn) chatSendBtn.addEventListener("click", sendInterruptMessage);
    if (chatMicBtn) chatMicBtn.addEventListener("click", toggleVoiceInput);
    if (resumeBtn) resumeBtn.addEventListener("click", resumeLecture);
    if (nextTopicBtn) nextTopicBtn.addEventListener("click", function () {
      var next = getNextTopic();
      if (next) selectTopic(next.id);
    });

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

  async function toggleLectureMode() {
    var toggleBtn = document.getElementById("lecture-toggle");
    var chatArea = document.getElementById("chat-area");
    var lectureContent = document.getElementById("lecture-content");
    var lecturePlayer = document.getElementById("lecture-player");
    var chatInput = document.getElementById("chat-input");
    var sendBtn = document.getElementById("send-btn");

    if (lectureState.active) {
      // Deactivate lecture mode
      lectureState.active = false;
      stopPlayback();
      toggleBtn.classList.remove("active");
      toggleBtn.innerHTML = "&#127897; レクチャー";
      chatArea.style.display = "";
      lectureContent.classList.remove("visible");
      lecturePlayer.classList.remove("visible");
      lecturePlayer.style.display = "";
      chatInput.style.display = "";
      sendBtn.style.display = "";
      return;
    }

    if (!state.courseId || !state.currentTopicId) return;

    // Activate lecture mode
    lectureState.active = true;
    toggleBtn.classList.add("active");
    toggleBtn.innerHTML = "&#128196; テキスト";
    chatArea.style.display = "none";
    chatInput.style.display = "none";
    sendBtn.style.display = "none";
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

    // 1. 本文全体を安全にエスケープ（数式部分はプレースホルダーなので壊れない）
    var text = escHtml(rawText);

    // 2. 段落・改行処理
    text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    text = text.split("\n\n").map(function (p) { return "<p>" + p + "</p>"; }).join("");
    text = text.replace(/\n/g, "<br>");

    // 3. IDをキーにして確実に数式をはめ込む（正規表現を使わない堅牢な置換）
    if (seg.formulas && seg.formulas.length > 0) {
      seg.formulas.forEach(function(f) {
        var rendered = "";
        try {
          if (window.katex) {
            rendered = window.katex.renderToString(f.latex.trim(), {
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
  function openInterruptChat() {
    pausePlayback();
    lectureState.interruptHistory = [];
    var popup = document.getElementById("lecture-chat-popup");
    popup.classList.add("visible");
    var messages = document.getElementById("lecture-chat-messages");
    messages.innerHTML = '<div class="mg ai">講義を一時停止しました。ご質問をどうぞ。</div>';
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
    popup.classList.remove("visible");
  }

  async function sendInterruptMessage() {
    var input = document.getElementById("lecture-chat-input");
    var message = input.value.trim();
    if (!message) return;
    input.value = "";

    var messages = document.getElementById("lecture-chat-messages");
    messages.innerHTML += '<div class="mg usr">' + escHtml(message) + '</div>';
    messages.innerHTML += '<div class="mg ai"><div class="typing"><span></span><span></span><span></span></div></div>';
    messages.scrollTop = messages.scrollHeight;

    var seg = lectureState.segments[lectureState.currentSegmentIndex];

    try {
      var res = await apiFetch(
        "/learning/lecture/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/interrupt",
        {
          method: "POST",
          body: JSON.stringify({
            message: message,
            current_chunk_id: seg ? seg.chunk_id : "",
            pause_position_ms: lectureState.pausePositionMs,
            history: lectureState.interruptHistory,
          }),
        }
      );

      // Remove typing indicator
      var typingEl = messages.querySelector(".typing");
      if (typingEl) typingEl.parentElement.remove();

      if (res.ok) {
        var data = await res.json();
        messages.innerHTML += '<div class="mg ai">' + renderAiContent(data.answer) + '</div>';
        lectureState.interruptHistory.push({ role: "user", content: message });
        lectureState.interruptHistory.push({ role: "assistant", content: data.answer });

        if (data.course_update && state.course) {
          Object.assign(state.course, data.course_update);
          renderSidebar();
          renderRightPanel();
        }
      } else {
        messages.innerHTML += '<div class="mg ai" style="color:var(--color-text-danger)">エラーが発生しました。</div>';
      }
    } catch (err) {
      var typingEl2 = messages.querySelector(".typing");
      if (typingEl2) typingEl2.parentElement.remove();
      messages.innerHTML += '<div class="mg ai" style="color:var(--color-text-danger)">サーバーに接続できません。</div>';
    }

    messages.scrollTop = messages.scrollHeight;

    // Render KaTeX in popup
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
