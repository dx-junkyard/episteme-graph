/* ===================================================================
   Episteme Graph — Learning UI Application Logic
   =================================================================== */

(function () {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────
  const state = {
    token: localStorage.getItem("eg_token") || null,
    username: localStorage.getItem("eg_username") || null,
    courseId: localStorage.getItem("eg_course") || null,
    course: null, // loaded course data
    currentTopicId: null,
    chatMessages: [], // {role, content}
    sending: false,
  };

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
      if (res.ok) return await res.json();
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
        html += '<div class="' + cls + '" data-topic="' + t.id + '" style="padding-left:36px">';
        html += escHtml(t.title);
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
  function renderChat() {
    const ca = document.getElementById("chat-area");
    if (!state.course || !state.currentTopicId) {
      ca.innerHTML = '<div class="mg ai" style="color:var(--color-text-tertiary)">左のサイドバーからトピックを選択してください。</div>';
      return;
    }

    let html = "";
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

    // Bind drill-down buttons
    ca.querySelectorAll(".dd button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        sendMessage(this.textContent.replace(/\s*↗$/, ""));
      });
    });
  }

  function renderAiContent(text) {
    // Simple markdown-like rendering
    let html = text;
    // Escape HTML first
    html = escHtml(html);
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Line breaks → paragraphs
    html = html.split("\n\n").map(function (p) { return "<p>" + p + "</p>"; }).join("");
    html = html.replace(/\n/g, "<br>");

    // Extract drill-down suggestions [〇〇について詳しく聞く]
    const suggestions = [];
    html = html.replace(/\[([^\]]+)\]/g, function (_, s) {
      suggestions.push(s);
      return "";
    });

    if (suggestions.length > 0) {
      html += '<div class="dd">';
      suggestions.forEach(function (s) {
        html += "<button>" + s + " ↗</button>";
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

    // Misconceptions
    const misconceptions = topic ? (topic.misconceptions || []) : [];
    if (misconceptions.length > 0) {
      html += '<div class="ps"><h4>指摘された誤解 <span class="mc-bd">' + misconceptions.length + '件</span></h4>';
      misconceptions.forEach(function (m) {
        html += '<div class="cc"><div class="lb" style="color:#A32D2D">' + escHtml(m.label || "訂正") + "</div>";
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

  // ── Topic Selection ────────────────────────────────────────────────
  async function selectTopic(topicId) {
    state.currentTopicId = topicId;
    state.chatMessages = [];
    renderSidebar();
    renderChat();
    renderRightPanel();

    // Load chat history
    if (state.courseId && topicId) {
      const history = await loadChatHistory(state.courseId, topicId);
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
        // Update course data if side-effects returned
        if (data.course_update) {
          Object.assign(state.course, data.course_update);
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
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage(input.value.trim());
      }
    });
  }

  // ── Course selector ────────────────────────────────────────────────
  async function initCourseSelector() {
    const courses = await loadCourses();
    const topbarCrse = document.getElementById("course-name");

    if (courses.length === 0) {
      topbarCrse.textContent = "コースなし";
      return;
    }

    // If we have a saved courseId, use it; otherwise use first
    if (!state.courseId || !courses.find(function (c) { return c.id === state.courseId; })) {
      state.courseId = courses[0].id;
      localStorage.setItem("eg_course", state.courseId);
    }

    // Create selector if multiple courses
    if (courses.length > 1) {
      const sel = document.createElement("select");
      sel.className = "course-select";
      courses.forEach(function (c) {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.title;
        if (c.id === state.courseId) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener("change", function () {
        state.courseId = this.value;
        localStorage.setItem("eg_course", this.value);
        loadAndRenderCourse();
      });
      topbarCrse.textContent = "";
      topbarCrse.appendChild(sel);
    } else {
      topbarCrse.textContent = courses[0].title;
    }

    await loadAndRenderCourse();
  }

  async function loadAndRenderCourse() {
    const course = await loadCourse(state.courseId);
    if (!course) return;
    const progress = await loadProgress(state.courseId);
    if (progress) course.progress = progress;

    state.course = course;

    // Set initial topic to first in_progress topic
    const inProgress = (course.topics || []).find(function (t) { return t.status === "in_progress"; });
    state.currentTopicId = inProgress ? inProgress.id : (course.topics && course.topics.length > 0 ? course.topics[0].id : null);

    // Update topbar
    const nameEl = document.getElementById("course-name");
    if (nameEl && nameEl.tagName !== "SELECT") nameEl.textContent = course.title;

    const streakEl = document.getElementById("streak");
    if (streakEl && progress) {
      streakEl.textContent = (progress.streak_days || 0) + "日連続学習中";
      streakEl.style.color = "var(--color-text-success)";
    }

    const usernameEl = document.getElementById("username");
    if (usernameEl) usernameEl.textContent = state.username || "";

    renderSidebar();
    if (state.currentTopicId) {
      state.chatMessages = await loadChatHistory(state.courseId, state.currentTopicId);
    }
    renderChat();
    renderRightPanel();
  }

  // ── Utilities ──────────────────────────────────────────────────────
  function escHtml(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── Init ───────────────────────────────────────────────────────────
  async function initApp() {
    if (!state.token) {
      renderAuth();
      return;
    }
    initTabs();
    initInput();
    await initCourseSelector();
  }

  // ── Expose sendPrompt globally for inline onclick ──────────────────
  window.sendPrompt = function (text) {
    sendMessage(text);
  };

  // Boot
  document.addEventListener("DOMContentLoaded", function () {
    renderAuth();
    if (state.token) initApp();
  });
})();
