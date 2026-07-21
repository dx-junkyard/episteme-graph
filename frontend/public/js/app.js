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
    editingMessageId: null, // 機能3: 書き直し中の user メッセージ id（送信で replace_message_id として使う）
    topicMaterial: [], // {id, text, chunk_index, chapter, section}
    learningSupport: null, // {mode, status_label, origin}
    sending: false,
    checkingUnderstanding: false,
    // P1: 直前の /check 応答が返した course_completed（サーバー正本）。
    // data-advance（サーバー応答なしの前進）経路が完了カードを出す判断に使う。
    // トピック切替・コース切替でリセットする（古いコースの完了状態を持ち越さない）。
    lastCheckCourseCompleted: false,
    topicHasAudio: false, // 現トピックに再生可能なキャッシュ済み音声があるか
    topicStaleLanguage: false, // audio-status.stale_language（表示のみ・判定には使わない）
    // ── 学習者体験レイヤー(B層) Stage M ──
    lastSources: [],        // L1: 直近回答の根拠 tier 一覧 [{source_title, tier, score}]
    lastOverallTier: null,  // L1: 直近回答全体の格
    lastGrounding: null,    // 直近回答の出所分類: course_material | other_material | model_generated
    interestTraces: null,   // L3: UnfinishedQuestionBox（mock）
    tensionDigest: null,    // 違和感ダイジェスト {items: [...]}（TensionMiningAgent Stage 2）
    tensionDeferred: {},    // [あとで] で今セッション中は隠す trace_id の集合
    // ── 構造帰属（Structure-Anchored Questions） ──
    anchorDigest: null,     // 帰属候補ダイジェスト {items: [...]}（StructureAnchorAgent Stage 2）
    anchorDeferred: {},     // [あとで] で今セッション中は隠す trace_id の集合
    pendingSelection: null, // 方法A: 「ここについて質問」で選択したテキスト {text, segment_id}
  };

  // 構造帰属: 疑いの様相の1タップ選択肢（サーバの DOUBT_TYPE_LABELS と同語彙。
  // unclassified は提示しない — 未選択のまま閉じれば unclassified が保たれる）。
  var ANCHOR_DOUBT_OPTIONS = [
    { doubt_type: "definition", label: "定義がわからない" },
    { doubt_type: "justification_gap", label: "なぜ成り立つのか" },
    { doubt_type: "premise", label: "前提への疑い" },
    { doubt_type: "prior_conflict", label: "既有知識との衝突" },
    { doubt_type: "scope", label: "どこまで成り立つのか" },
    { doubt_type: "connection", label: "他とどう繋がるのか" },
  ];

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

  // 回答の出所分類（教材 / 別の資料 / 出典を追えないモデル生成）の表示メタ。
  // tier（教員承認状況）とは別軸: 「そもそも教材が使われたか」を示す。
  var GROUNDING_META = {
    course_material: { label: "教材から回答", cls: "grounding-course", icon: "📘" },
    other_material: { label: "別の資料から回答", cls: "grounding-other", icon: "📄" },
    model_generated: { label: "AIの一般知識（出典なし）", cls: "grounding-model", icon: "💭" },
  };
  function groundingMeta(g) {
    return GROUNDING_META[g] || null;
  }
  function groundingBadge(g) {
    var m = groundingMeta(g);
    if (!m) return "";
    return '<span class="grounding-badge ' + m.cls + '">' + m.icon + " " + escHtml(m.label) + "</span>";
  }
  var ORIGIN_LABEL = { course_material: "教材", other_material: "別の資料" };

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
    // レクチャーはデッキ（スライド）基準で進行するため、対象セグメントの先頭スライドへ
    // マッピングしてから復帰する。
    restorePosition: function (anchor) {
      if (!anchor) return;
      if (typeof lectureState !== "undefined" && lectureState.active &&
          typeof anchor.segment_id === "number" && anchor.segment_id > 0 &&
          Array.isArray(lectureState.segments) && lectureState.segments.length &&
          Array.isArray(lectureState.deck) && lectureState.deck.length) {
        var targetSegIdx = Math.min(anchor.segment_id, lectureState.segments.length - 1);
        var deckIdx = 0;
        for (var i = 0; i < lectureState.deck.length; i++) {
          if (lectureState.deck[i].segment_index === targetSegIdx) { deckIdx = i; break; }
        }
        lectureState.currentDeckIndex = deckIdx;
        lectureState.currentSegmentIndex = lectureState.deck[deckIdx].segment_index;
        if (typeof renderLectureStage === "function") renderLectureStage();
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

  // apiFetch と同じ 401 処理だが Content-Type を強制しない生 fetch（画像 blob 取得用）。
  // 図画像 endpoint は Authorization ヘッダ必須のため <img src> を直接指定できず、
  // fetch + blob + createObjectURL で取得する（admin.js の apiFetchRaw と同じ方式）。
  async function apiFetchRaw(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
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
    removeVersionNoticeBanner();  // ログアウト時に削除予定バナーを残さない

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
    // #auth-switch は innerHTML 差し替えで毎回作り直されるため、安定な親 #auth-toggle に委任する
    document.getElementById("auth-toggle").addEventListener("click", function (e) {
      if (!e.target || e.target.id !== "auth-switch") return;
      isLogin = !isLogin;
      document.getElementById("auth-btn").textContent = isLogin ? "ログイン" : "登録";
      document.getElementById("auth-toggle").innerHTML = isLogin
        ? 'アカウントがない場合 <a id="auth-switch">新規登録</a>'
        : '既にアカウントがある場合 <a id="auth-switch">ログイン</a>';
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

  // ── 機能3: メッセージの書き直し・削除（以降を削除して再処理） ──────────
  function _findMessageIndexById(msgId) {
    for (var i = 0; i < state.chatMessages.length; i++) {
      if (state.chatMessages[i].id === msgId) return i;
    }
    return -1;
  }

  // 書き直し開始: 対象 user メッセージ本文を入力欄に戻し、送信で以降を差し替える。
  function startEditMessage(msgId) {
    if (state.sending) return;
    var idx = _findMessageIndexById(msgId);
    if (idx === -1 || state.chatMessages[idx].role !== "user") return;
    var input = document.getElementById("chat-input");
    if (input) {
      input.value = state.chatMessages[idx].content || "";
      input.focus();
    }
    state.editingMessageId = msgId;
    showEditIndicator();
  }

  function cancelEditMessage() {
    state.editingMessageId = null;
    var input = document.getElementById("chat-input");
    if (input) input.value = "";
    hideEditIndicator();
  }

  // 入力欄の直前に「書き直し中（送信すると以降を削除して作り直します）」バナーを出す。
  function showEditIndicator() {
    var input = document.getElementById("chat-input");
    if (!input) return;
    var el = document.getElementById("chat-edit-indicator");
    if (!el) {
      el = document.createElement("div");
      el.id = "chat-edit-indicator";
      el.className = "chat-edit-indicator";
      input.parentNode.insertBefore(el, input);
    }
    el.innerHTML = '✏️ 書き直し中 — 送信するとこのメッセージ以降の会話を削除して作り直します ' +
      '<button type="button" id="chat-edit-cancel" class="chat-edit-cancel">取消</button>';
    var cancel = document.getElementById("chat-edit-cancel");
    if (cancel) cancel.addEventListener("click", cancelEditMessage);
  }

  function hideEditIndicator() {
    var el = document.getElementById("chat-edit-indicator");
    if (el) el.remove();
  }

  // 削除: このメッセージ以降の往復を本人の履歴から取り除く（サーバ側で supersede）。
  async function deleteMessageFrom(msgId) {
    if (state.sending || !state.courseId || !state.currentTopicId) return;
    if (!confirm("このメッセージ以降の会話を削除します。よろしいですか？")) return;
    // 書き直し中の対象を消す場合は編集状態も解除
    if (state.editingMessageId === msgId) cancelEditMessage();
    var idx = _findMessageIndexById(msgId);
    try {
      const res = await apiFetch(
        "/learning/courses/" + state.courseId + "/topics/" + state.currentTopicId +
        "/chat/messages/" + encodeURIComponent(msgId),
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error("Delete failed");
      if (idx !== -1) state.chatMessages = state.chatMessages.slice(0, idx);
      renderChat();
      renderRightPanel();
    } catch (err) {
      alert("メッセージの削除に失敗しました。");
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
    // P1: リロード後の復元。サーバー正本の progress（loadAndRenderCourse が
    // course.progress に格納する）から、確認済みトピックと全完了状態を控えめに示す
    // （件数・割合は数値として出さない設計不変条項に従う）。
    const progress = course.progress || {};
    const completedTopicIds = Array.isArray(progress.completed_topic_ids) ? progress.completed_topic_ids : [];
    let html = '<div class="sb-hd">コースツリー</div>';
    html += '<div class="course-tree-title">' + escHtml(course.title || "コース") + '</div>';
    if (progress.course_completed) {
      html += '<div class="course-tree-done">全トピックの確認を終えています</div>';
    }

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

        const doneBadge = completedTopicIds.indexOf(t.id) !== -1
          ? '<span class="ni-done" title="確認済み">✓</span>'
          : "";

        html += '<div class="' + cls + '" data-topic="' + t.id + '" style="padding-left:36px">';
        html += escHtml(t.title) + annotationBadge + supportBadge + doneBadge;
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
        // N32: ロック表示（グレー/鍵）のトピックも遷移は従来どおり許可する
        // （押し付けない原則）。視覚と挙動の齟齬は一行の事実文で埋める。
        maybeShowLockedTopicFact(tid);
        selectTopic(tid);
      });
    });

    // 分野の地図 (Issue F-1): 学習パスパネル下部の常設ミニマップ。
    // 骨格を同梱したカートリッジでのみ表示される (なければ領域ごと非表示)。
    if (window.AtlasMinimap) window.AtlasMinimap.mount(sb);
  }

  // 一行の事実文トースト。評価・催促の語彙を使わず、事実のみを短く示して自動で消える。
  // 常に最新1枚のみ（連打で積み重ねない）。
  var _factToastTimer = null;
  function showFactToast(message) {
    var existing = document.getElementById("fact-toast");
    if (existing) existing.remove();
    if (_factToastTimer) { clearTimeout(_factToastTimer); _factToastTimer = null; }
    var div = document.createElement("div");
    div.id = "fact-toast";
    div.className = "fact-toast";
    div.setAttribute("role", "status");
    div.textContent = message;
    document.body.appendChild(div);
    _factToastTimer = setTimeout(function () {
      div.remove();
      _factToastTimer = null;
    }, 4000);
  }

  // N32: ロック表示のトピックをクリックしたとき、視覚（鍵/グレー）と挙動（遷移可）の
  // 齟齬を一行の事実文で埋める。遷移自体は従来どおり許可する（押し付けない原則）。
  // 「前のトピックの確認が未完了です」が事実であるときだけ表示する
  // （前のトピックが確認済みなら表示が古いだけなので何も出さない — fail-closed）。
  function maybeShowLockedTopicFact(topicId) {
    if (!state.course) return;
    var topics = state.course.topics || [];
    var idx = -1;
    for (var i = 0; i < topics.length; i++) {
      if (topics[i].id === topicId) { idx = i; break; }
    }
    if (idx <= 0) return; // 先頭トピック・不明トピックには「前のトピック」が無い
    var topic = topics[idx];
    if ((topic.status || "locked") !== "locked") return;
    if (topic.id === state.currentTopicId) return;
    var progress = state.course.progress || {};
    if (progress.course_completed) return;
    var completed = Array.isArray(progress.completed_topic_ids) ? progress.completed_topic_ids : [];
    if (completed.indexOf(topic.id) !== -1) return; // 本人確認済み（ロック表示が古いだけ）
    var prev = topics[idx - 1];
    if (!prev || prev.status === "completed" || completed.indexOf(prev.id) !== -1) return;
    showFactToast("前のトピックの確認が未完了です");
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
      // 「この問いに戻る」のジャンプ先アンカー（id を持つメッセージのみ）。
      var idAttr = msg.id ? ' id="msg-' + escHtml(msg.id) + '"' : "";
      if (msg.role === "user") {
        var anchorChip = "";
        // 構造帰属（方法A）: この問いが指した構造要素のチップ（learner_selected）
        if (msg.structure_anchor && msg.structure_anchor.anchor_label) {
          anchorChip = '<div style="font-size:11px;opacity:.75;margin-top:4px">📍 ' +
            escHtml(msg.structure_anchor.anchor_label) + '</div>';
        }
        // 機能3: 書き直し（✏️）/ 以降削除（🗑）。id を持つメッセージのみ・送信中は出さない。
        var msgActions = "";
        if (msg.id && !state.sending) {
          msgActions = '<div class="mg-actions">' +
            '<button class="mg-act-btn" data-edit-msg="' + escHtml(msg.id) + '" title="このメッセージを書き直す">✏️</button>' +
            '<button class="mg-act-btn" data-del-msg="' + escHtml(msg.id) + '" title="このメッセージ以降を削除">🗑</button>' +
            '</div>';
        }
        html += '<div class="mg usr"' + idAttr + '>' + escHtml(msg.content) + anchorChip + msgActions + "</div>";
      } else {
        html += '<div class="mg ai"' + idAttr + '>' + renderAiContent(msg.content, msg) +
          renderAnchorConfirmPrompt(msg) + "</div>";
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

    // 構造帰属（方法C）: 回答末尾の1タップ様相選択（選択がそのまま帰属の確定になる）。
    ca.querySelectorAll("[data-anchor-prompt-doubt]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var traceId = this.getAttribute("data-anchor-prompt-trace");
        var doubt = this.getAttribute("data-anchor-prompt-doubt");
        markAnchorPromptDone(traceId);
        if (doubt) confirmAnchor(traceId, doubt);
        renderChat();
      });
    });

    // 機能3: メッセージの書き直し・削除ボタンを配線
    ca.querySelectorAll("[data-edit-msg]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        startEditMessage(this.getAttribute("data-edit-msg"));
      });
    });
    ca.querySelectorAll("[data-del-msg]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        deleteMessageFrom(this.getAttribute("data-del-msg"));
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

    // 分野の地図 (Issue C-3): 学習パス提案カードの三択と「自分で繋ぐ」を配線
    bindAtlasPathCards(ca);

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

  // ── 構造帰属（方法A）: 教材区画のテキスト選択 →「ここについて質問」 ──
  // 選択テキストは次の1問にだけ selection_text として添えられ、learner_selected の
  // 明示アンカー（ground truth）として同期記録される。

  function clearPendingSelection() {
    state.pendingSelection = null;
    var chip = document.getElementById("anchor-selection-chip");
    if (chip) chip.remove();
  }

  // 入力欄の直前に「選択中: …」チップを出す（解除可能）。
  function showPendingSelectionChip() {
    var input = document.getElementById("chat-input");
    if (!input || !state.pendingSelection) return;
    var chip = document.getElementById("anchor-selection-chip");
    if (!chip) {
      chip = document.createElement("div");
      chip.id = "anchor-selection-chip";
      chip.style.cssText =
        "font-size:11px;padding:4px 8px;margin:0 0 4px;border-radius:6px;" +
        "background:var(--color-bg-secondary,#f3f4f6);display:flex;align-items:center;gap:6px";
      input.parentNode.insertBefore(chip, input);
    }
    var t = state.pendingSelection.text;
    chip.innerHTML = '📍 ここについて質問: <span style="opacity:.8">' +
      escHtml(t.length > 40 ? t.slice(0, 40) + "…" : t) + '</span>' +
      '<button class="lx-ghost secondary" id="anchor-selection-clear" style="margin-left:auto">解除</button>';
    chip.querySelector("#anchor-selection-clear").addEventListener("click", clearPendingSelection);
  }

  function hideSelectionAskButton() {
    var b = document.getElementById("anchor-ask-btn");
    if (b) b.remove();
  }

  // 教材区画（#material-body）内のテキスト選択にフローティングボタンを出す。
  function initSelectionAnchor() {
    if (document._anchorSelectionWired) return;
    document._anchorSelectionWired = true;
    document.addEventListener("mouseup", function () {
      // click 直後に selection が確定するのを待つ
      setTimeout(function () {
        hideSelectionAskButton();
        var body = document.getElementById("material-body");
        var sel = window.getSelection();
        var text = sel ? String(sel.toString() || "").trim() : "";
        if (!body || !text || text.length < 4 || text.length > 300) return;
        if (!sel.rangeCount) return;
        var range = sel.getRangeAt(0);
        if (!body.contains(range.commonAncestorContainer)) return;
        var rect = range.getBoundingClientRect();
        var btn = document.createElement("button");
        btn.id = "anchor-ask-btn";
        btn.textContent = "ここについて質問";
        btn.style.cssText =
          "position:fixed;z-index:1000;left:" + Math.round(rect.left) + "px;" +
          "top:" + Math.round(rect.bottom + 6) + "px;font-size:12px;padding:4px 10px;" +
          "border-radius:14px;border:1px solid var(--color-border,#ccc);" +
          "background:var(--color-bg,#fff);box-shadow:0 2px 8px rgba(0,0,0,.15);cursor:pointer";
        // mousedown で選択が消えるのを防ぐ
        btn.addEventListener("mousedown", function (e) { e.preventDefault(); });
        btn.addEventListener("click", function () {
          var seg = (Session.currentAnchor() || {}).segment_id || 0;
          state.pendingSelection = { text: text, segment_id: seg };
          hideSelectionAskButton();
          showPendingSelectionChip();
          var input = document.getElementById("chat-input");
          if (input) input.focus();
        });
        document.body.appendChild(btn);
      }, 0);
    });
    document.addEventListener("mousedown", function (e) {
      if (e.target && e.target.id === "anchor-ask-btn") return;
      hideSelectionAskButton();
    });
  }

  // 構造帰属（方法C）: 回答末尾の1タップ確認プロンプト。ゲート済み応答にのみ付く。
  // 選択しないまま流しても問い自体は unclassified で保持される（P4/P7）。
  function renderAnchorConfirmPrompt(msg) {
    var ac = msg.anchor_confirm;
    if (!ac || ac.done || !ac.trace_id) return "";
    var tid = escHtml(ac.trace_id);
    var html = '<div class="anchor-confirm" style="margin-top:10px;padding-top:8px;border-top:1px dashed var(--color-border,#ccc);font-size:12px">';
    html += '<div style="opacity:.8;margin-bottom:6px">この疑問はどれに近いですか？（任意・1タップ）</div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:6px">';
    (ac.options || ANCHOR_DOUBT_OPTIONS).forEach(function (o) {
      html += '<button class="lx-ghost" data-anchor-prompt-trace="' + tid +
        '" data-anchor-prompt-doubt="' + escHtml(o.doubt_type) + '">' + escHtml(o.label) + '</button>';
    });
    html += '<button class="lx-ghost secondary" data-anchor-prompt-trace="' + tid +
      '" data-anchor-prompt-doubt="">スキップ</button>';
    html += '</div></div>';
    return html;
  }

  // 方法C のプロンプトを消化済みにする（再描画で出さない）。
  function markAnchorPromptDone(traceId) {
    state.chatMessages.forEach(function (m) {
      if (m.anchor_confirm && m.anchor_confirm.trace_id === traceId) {
        m.anchor_confirm.done = true;
      }
    });
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
    hydrateMaterialFigures(body);

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

    // 分野の地図 (Issue C-3): 学習パス提案カード (§8)
    if (msg && msg.atlas_path_card) {
      html += renderAtlasPathCard(msg.atlas_path_card);
    }

    // 回答内容の出所（教材/別の資料/モデル生成）と L1 信頼性（tier）を末尾に明示する。
    if (msg && (msg.content_grounding || msg.overall_tier)) {
      var bar = '<div class="answer-tier-bar">';
      if (msg.content_grounding) bar += groundingBadge(msg.content_grounding);
      if (msg.overall_tier) {
        bar += '<span class="answer-tier-label">この回答の根拠の格</span>';
        bar += tierBadge(msg.overall_tier);
      }
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
        // renderMaterialChunk は [[FORMULA_N]] / [[FIGURE_N]] を解決した自己完結 HTML を返す。
        inner += '<div class="material-chunk-text">' +
          renderMaterialChunk({ text: data.text, formulas: data.formulas, figures: data.figures }) + '</div>';
        body.innerHTML = inner;
        hydrateMaterialFigures(body);
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
      // D層 (D3-6): 台帳の正直表示 — 検証状態を一行の事実として静かに併記
      appendLearnerLedgerLine(body, "component", componentId);
    } catch (_) {
      const b = pop.querySelector(".src-popup-body");
      if (b) b.textContent = "サーバーに接続できません。";
    }
  }

  // D層 (D3-6): 台帳の一行事実文を取得する。台帳未記帳 (404)・取得失敗時は null
  // （fail-closed）。段階ラベルと事実文のみで、数値スコアは扱わない。
  // 同一 (targetType, targetId) はセッション内でキャッシュする（再描画のたびに
  // 再取得しない。null＝未記帳もキャッシュして 404 の連打を避ける）。
  var _ledgerFactLineCache = {};
  async function fetchLearnerLedgerFactLine(targetType, targetId) {
    if (!targetId || !state.courseId) return null;
    var cacheKey = state.courseId + "|" + targetType + "|" + targetId;
    if (Object.prototype.hasOwnProperty.call(_ledgerFactLineCache, cacheKey)) {
      return _ledgerFactLineCache[cacheKey];
    }
    var text = null;
    try {
      const res = await apiFetch("/learning/courses/" + state.courseId +
        "/ledger/" + encodeURIComponent(targetType) + "/" + encodeURIComponent(targetId));
      if (res.ok) {
        const data = await res.json();
        if (data && data.fact_line) {
          text = data.fact_line;
          var axes = (data.scopes || []).map(function (s) {
            return [s.condition, s.domain, s.precision, s.system].filter(Boolean).join(" / ");
          }).filter(Boolean);
          if (axes.length > 1) text += "（記帳スコープ: " + axes.join("；") + "）";
        }
      }
    } catch (_) { /* fail-closed */ }
    _ledgerFactLineCache[cacheKey] = text;
    return text;
  }

  // 台帳一行の共通様式（component ポップアップと出典タブで同一の見た目にする）。
  function makeLedgerLineDiv(text) {
    var div = document.createElement("div");
    div.className = "learner-ledger-line";
    div.style.cssText = "margin-top:8px;padding-top:6px;border-top:1px dashed var(--color-border,#ddd);" +
      "font-size:12px;color:var(--color-text-secondary,#64748b)";
    div.textContent = text;
    return div;
  }

  // D層 (D3-6): 対象 claim/equation/component の検証状態の一行事実文を併記する。
  // 台帳未記帳 (404)・取得失敗時はセクション自体を出さない（fail-closed）。
  // 検証済みスコープも未記帳も同じ精度で示す（§8-1・8-2 — 不安を煽らない中立文言）。
  async function appendLearnerLedgerLine(container, targetType, targetId) {
    if (!container) return;
    var text = await fetchLearnerLedgerFactLine(targetType, targetId);
    if (text) container.appendChild(makeLedgerLineDiv(text));
  }

  // ── 分野の地図: 学習パス提案カード (Issue C-3, 仕様書 §8) ─────────────
  // 表示規則: 各ステップに出所 (教材 / AI一般知識) と状態を明示。行間ステップは
  // 「先生に聞くポイント」として質問テンプレートを添付。暗黙の前提は台帳状態を
  // そのまま表示するのみ (評価しない)。終端は可能なら並置 + 「自分で繋ぐ」入力。
  // 評価語・推薦理由・誘導文言は置かない。
  var atlasCardSeq = 0;
  var atlasPathCards = {}; // card_key → card (再描画・決定ハンドラ用)

  function registerAtlasPathCard(card) {
    if (!card) return null;
    card.card_key = "apc-" + (++atlasCardSeq);
    card.decision = card.decision || ""; // proceed/edit/dismiss 後の再描画用
    atlasPathCards[card.card_key] = card;
    return card;
  }

  function renderAtlasPathStep(step, isJuxta) {
    var html = '<div class="atlas-path-step' + (step.is_gap ? " gap" : "") + '">';
    html += '<span class="atlas-path-step-label">' + escHtml(step.label || "") + '</span>';
    if (step.status_label) {
      html += '<span class="atlas-path-badge">' + escHtml(step.status_label) + '</span>';
    }
    html += '<span class="atlas-path-badge ' +
      (step.source === "model_knowledge" ? "src-model" : "src-material") + '">' +
      escHtml(step.source_label || "") + '</span>';
    if (step.is_gap && step.teacher_question) {
      html += '<p class="atlas-path-teacherq">先生に聞くポイント: ' + escHtml(step.teacher_question) + '</p>';
    } else if (step.ledger_note) {
      html += '<p class="atlas-path-note">' + escHtml(step.ledger_note) + '</p>';
    }
    if (!isJuxta && step.visited_note) {
      html += '<p class="atlas-path-note">' + escHtml(step.visited_note) + '</p>';
    }
    return html + "</div>";
  }

  function renderAtlasPathCard(card) {
    var html = '<div class="atlas-path-card" data-card-key="' + escHtml(card.card_key || "") + '">';
    html += '<div class="atlas-path-title">学習パスの候補 — 「' + escHtml((card.target || {}).label || "") + '」から</div>';
    if (card.current_thread) {
      html += '<div class="atlas-path-thread">いまの糸: ' + escHtml(card.current_thread) + '</div>';
    }
    (card.steps || []).forEach(function (step) { html += renderAtlasPathStep(step, false); });
    (card.notes || []).forEach(function (note) {
      html += '<p class="atlas-path-note">' + escHtml(note) + '</p>';
    });

    var terminal = card.terminal || {};
    html += '<div class="atlas-path-terminal">';
    if (terminal.mode === "juxtapose" && (terminal.pair || []).length === 2) {
      // 並置: 2つの構造を黙って並べる。接続の言語化はしない (§8)
      html += '<div class="atlas-path-juxta">';
      terminal.pair.forEach(function (step) {
        html += '<div class="atlas-path-juxta-cell">' + renderAtlasPathStep(step, true) + '</div>';
      });
      html += '</div>';
    }
    if (terminal.connect_input) {
      html += '<div class="atlas-path-connect">'
        + '<input type="text" class="atlas-path-connect-input" placeholder="自分で繋ぐ（自分の言葉で）">'
        + '<button class="lx-ghost atlas-path-connect-save" type="button">記録する</button>'
        + '</div>';
      html += '<p class="atlas-path-connect-ack atlas-path-decided" style="display:none">自分の言葉で記録しました。</p>';
    }
    html += '</div>';

    if (card.decision === "dismiss") {
      html += '<p class="atlas-path-decided">この提案は閉じ、記録に残しました。</p>';
    } else if (card.decision === "proceed") {
      html += '<p class="atlas-path-decided">この糸で進んでいます。</p>';
    } else {
      html += '<div class="atlas-path-decisions">'
        + '<button class="lx-ghost atlas-path-decide" data-decision="proceed" type="button">この糸で進む</button>'
        + '<button class="lx-ghost secondary atlas-path-decide" data-decision="edit" type="button">編集する</button>'
        + '<button class="lx-ghost secondary atlas-path-decide" data-decision="dismiss" type="button">今はやめる</button>'
        + '</div>';
    }
    return html + "</div>";
  }

  // 三択 ([この糸で進む]/[編集する]/[今はやめる]) と「自分で繋ぐ」の記録。
  // 却下 (今はやめる) も interest_traces に記録する — 情報を落とさない。
  async function postAtlasPathDecision(card, decision, learnerText) {
    try {
      await apiFetch("/learning/courses/" + state.courseId + "/atlas/path-decision", {
        method: "POST",
        body: JSON.stringify({
          node_id: (card.target || {}).node_id || "",
          node_label: (card.target || {}).label || "",
          level: card.level || 1,
          skeleton_version: card.skeleton_version || "",
          decision: decision,
          learner_text: learnerText || "",
          steps: (card.steps || []).map(function (s) { return s.label; }),
          topic_id: state.currentTopicId || null,
        }),
      });
    } catch (e) { /* best-effort: 記録失敗で操作は止めない */ }
  }

  function bindAtlasPathCards(container) {
    container.querySelectorAll(".atlas-path-card").forEach(function (cardEl) {
      var card = atlasPathCards[cardEl.getAttribute("data-card-key")];
      if (!card) return;
      cardEl.querySelectorAll(".atlas-path-decide").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var decision = this.getAttribute("data-decision");
          postAtlasPathDecision(card, decision);
          if (decision === "proceed") {
            card.decision = "proceed";
            var first = (card.steps || [])[0];
            if (first) {
              sendMessage("この学習パスで進めたい。まず「" + first.label + "」から説明して", {
                atlas_context: {
                  node_id: (card.target || {}).node_id || "",
                  level: card.level || 1,
                  skeleton_version: card.skeleton_version || "",
                  action: "path_proceed",
                  node_label: (card.target || {}).label || "",
                },
              });
            } else {
              renderChat();
            }
          } else if (decision === "edit") {
            // 本人の言葉で編集して送る (§1.2-5): パスの文字列を入力欄に展開する
            var input = document.getElementById("chat-input");
            if (input) {
              input.value = "この学習パスを編集したい: " +
                (card.steps || []).map(function (s) { return s.label; }).join(" → ");
              input.focus();
            }
          } else if (decision === "dismiss") {
            card.decision = "dismiss";
            renderChat();
          }
        });
      });
      var saveBtn = cardEl.querySelector(".atlas-path-connect-save");
      if (saveBtn) {
        saveBtn.addEventListener("click", function () {
          var input = cardEl.querySelector(".atlas-path-connect-input");
          var text = input ? input.value.trim() : "";
          if (!text) return;
          postAtlasPathDecision(card, "connect", text);
          var ack = cardEl.querySelector(".atlas-path-connect-ack");
          if (ack) ack.style.display = "";
          if (input) input.value = "";
        });
      }
    });
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
      updateProgressTabDot();
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

    // Phase P-1: 「わたしの地図」との相互リンク（問いの軌跡→地図上の位置）。
    // 未ロード時は静的コンテナが無いため実質何もしない（fail-closed）。
    if (window.PersonalMap) {
      var trajList = document.getElementById("lx-trajectory-list");
      if (trajList) window.PersonalMap.annotateTrajectoryList(trajList);
    }
    updateProgressTabDot();
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
        // まず元の往復へジャンプ（前後の文脈ごと見返せる）。前後の会話次第で同じ問いに
        // まともな答えが返る保証はないため、再送信は履歴に該当メッセージが無い場合の
        // フォールバックに限定する。
        if (jumpToChatMessage(this.getAttribute("data-trace-msg"))) return;
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
    // 違和感ダイジェスト: [そう、これ] [違う] [あとで]（確定は本人のみ。P1）
    el.querySelectorAll("[data-tension-confirm]").forEach(function (b) {
      b.addEventListener("click", function () {
        openTensionConfirmInput(this.getAttribute("data-tension-confirm"));
      });
    });
    el.querySelectorAll("[data-tension-dismiss]").forEach(function (b) {
      b.addEventListener("click", function () {
        dismissTension(this.getAttribute("data-tension-dismiss"));
      });
    });
    el.querySelectorAll("[data-tension-defer]").forEach(function (b) {
      b.addEventListener("click", function () {
        state.tensionDeferred[this.getAttribute("data-tension-defer")] = true;
        renderProgressTab();  // 今セッション中は隠すだけ（candidate のまま保持）
      });
    });
    // 帰属候補の確認カード: [そう、これ] [違う] [あとで] + 様相の訂正チップ（P1）
    el.querySelectorAll("[data-anchor-confirm]").forEach(function (b) {
      b.addEventListener("click", function () {
        confirmAnchor(this.getAttribute("data-anchor-confirm"), "");
      });
    });
    el.querySelectorAll("[data-anchor-correct]").forEach(function (b) {
      b.addEventListener("click", function () {
        confirmAnchor(this.getAttribute("data-anchor-correct"), this.getAttribute("data-anchor-doubt"));
      });
    });
    el.querySelectorAll("[data-anchor-dismiss]").forEach(function (b) {
      b.addEventListener("click", function () {
        dismissAnchor(this.getAttribute("data-anchor-dismiss"));
      });
    });
    el.querySelectorAll("[data-anchor-defer]").forEach(function (b) {
      b.addEventListener("click", function () {
        state.anchorDeferred[this.getAttribute("data-anchor-defer")] = true;
        renderProgressTab();  // 今セッション中は隠すだけ（llm_candidate のまま保持）
      });
    });
  }

  // [そう、これ] の後にだけ任意の一行編集欄を出す（空のままでも open で確定できる）。
  function openTensionConfirmInput(traceId) {
    var card = document.querySelector('[data-tension-card="' + traceId + '"]');
    if (!card) return;
    var actions = card.querySelector(".lx-trace-actions");
    if (!actions) return;
    var box = document.createElement("div");
    box.className = "lx-why-input";
    box.innerHTML =
      '<textarea rows="2" placeholder="自分の言葉で言い直すと?（任意・空のままでも確定できます）"></textarea>' +
      '<div class="lx-why-actions"><button class="lx-ghost lx-tension-save">この違和感を引き受ける</button>' +
      '<button class="lx-ghost secondary lx-tension-cancel">やめる</button></div>';
    actions.replaceWith(box);
    var ta = box.querySelector("textarea");
    ta.focus();
    box.querySelector(".lx-tension-cancel").addEventListener("click", function () {
      renderProgressTab();
    });
    box.querySelector(".lx-tension-save").addEventListener("click", async function () {
      await confirmTension(traceId, (ta.value || "").trim());
    });
  }

  async function confirmTension(traceId, learnerText) {
    try {
      await apiFetch(
        "/learning/tension/" + encodeURIComponent(traceId) + "/confirm",
        { method: "POST", body: JSON.stringify({ learner_text: learnerText || "" }) }
      );
    } catch (_) { /* best-effort */ }
    await loadTensionDigest();
    loadInterestTraces();  // 確定した違和感は問いの軌跡に現れる
  }

  async function dismissTension(traceId) {
    try {
      await apiFetch(
        "/learning/tension/" + encodeURIComponent(traceId) + "/dismiss",
        { method: "POST", body: JSON.stringify({}) }
      );
    } catch (_) { /* best-effort */ }
    await loadTensionDigest();
    renderProgressTab();
  }

  // G3: 「候補ゼロ」と「取得できなかった」を区別するため、非 ok 応答/例外は
  // items:[] に丸めつつ _error フラグを立てる（renderTensionDigestCard が読む）。
  // P2: 開始時のコースIDを保持し、応答時に別コースへ切替済みなら state への書き込みを
  // 破棄する（コースAの候補がコースBの画面に残る汚染を防ぐ）。
  async function loadTensionDigest() {
    if (!state.courseId) return;
    var cid = state.courseId;
    try {
      var res = await apiFetch("/learning/courses/" + cid + "/tension/digest");
      var digest = res.ok ? await res.json() : { items: [], _error: true };
    } catch (_) {
      digest = { items: [], _error: true };
    }
    if (state.courseId !== cid) return; // 遅延応答ガード
    state.tensionDigest = digest;
  }

  // ── 構造帰属（Structure-Anchored Questions）Stage 2: ダイジェスト・本人確定 ──

  // P2: loadTensionDigest と同様、コース切替後の遅延応答を破棄する。
  async function loadAnchorDigest() {
    if (!state.courseId) return;
    var cid = state.courseId;
    try {
      var res = await apiFetch("/learning/courses/" + cid + "/anchors/digest");
      var digest = res.ok ? await res.json() : { items: [], _error: true };
    } catch (_) {
      digest = { items: [], _error: true };
    }
    if (state.courseId !== cid) return; // 遅延応答ガード
    state.anchorDigest = digest;
  }

  // 帰属を本人が確定/訂正する（doubtType は任意。空なら候補のまま確定）。
  async function confirmAnchor(traceId, doubtType) {
    try {
      var res = await apiFetch(
        "/learning/anchors/" + encodeURIComponent(traceId) + "/confirm",
        { method: "POST", body: JSON.stringify({ doubt_type: doubtType || "" }) }
      );
      // D層 (D3-6): 確定した anchor が分野で明示化済みの前提に対応していれば、
      // 事後に静かに併記する（通知・ポップアップにしない。帰属事実のみ）。
      if (res && res.ok) {
        var data = await res.json();
        var related = data && data.related_assumption;
        if (related && related.statement) {
          appendSystemNote(
            "この問いは、分野で明示化されている前提「" + related.statement + "」に関わっています。"
          );
        }
      }
    } catch (_) { /* best-effort */ }
    await loadAnchorDigest();
    loadInterestTraces();  // 確定した帰属は問いの軌跡のチップに現れる
  }

  // D層 (D3-6): チャット欄の末尾に静かな注記を 1 行足す（割り込み要素ゼロ）。
  function appendSystemNote(text) {
    var messages = document.getElementById("chat-area");
    if (!messages || !text) return;
    var div = document.createElement("div");
    div.style.cssText = "font-size:12px;color:var(--color-text-tertiary,#94a3b8);margin:6px 12px";
    div.textContent = text;
    messages.appendChild(div);
  }

  async function dismissAnchor(traceId) {
    try {
      await apiFetch(
        "/learning/anchors/" + encodeURIComponent(traceId) + "/dismiss",
        { method: "POST", body: JSON.stringify({}) }
      );
    } catch (_) { /* best-effort */ }
    await loadAnchorDigest();
    renderProgressTab();
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
      // ⓪ 出所バナー: 教材 / 別の資料 / 出典を追えないモデル生成。
      var gMeta = groundingMeta(state.lastGrounding);
      if (gMeta) {
        var gNote = state.lastGrounding === "course_material"
          ? "このコースの教材に基づいて回答しています。"
          : state.lastGrounding === "other_material"
            ? "このコース以外の資料（システム内の他教材）を根拠に回答しています。"
            : "教材の根拠が見つからず、AIの一般知識のみで回答しています。内容の裏づけはありません。";
        html += '<div class="ps" style="border:none;padding:0 0 8px">';
        html += '<div class="lx-grounding-banner ' + gMeta.cls + '">' + gMeta.icon +
          ' <span><b>' + escHtml(gMeta.label) + '</b> — ' + gNote + '</span></div></div>';
      }
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
          // D層 (D3-6): 台帳併記用にチャンク ID をカードへ持たせる（描画後に非同期併記）。
          html += '<div class="lx-src ' + c + '" data-lx-ledger-chunk="' + escHtml(s.chunk_id || "") + '"><div class="lx-src-top">';
          html += '<span class="lx-src-title">' + escHtml(s.source_title || "不明な教材") + '</span>';
          html += '<span class="lx-src-badges">';
          if (s.origin) html += '<span class="lx-origin-badge">' + escHtml(ORIGIN_LABEL[s.origin] || s.origin) + '</span>';
          html += '<span class="lx-badge ' + c + '">' + escHtml(TIER_LABEL[s.tier] || s.tier) + '</span></span></div>';
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

    // D層 (D3-6): 未検証合意リストの閲覧（プル型・読み取り専用。開いた人だけが見る）
    html += '<details class="ps" id="lx-open-assumptions"><summary style="cursor:pointer;font-size:12.5px;' +
      'color:var(--color-text-secondary)">この分野の未検証の前提を見る</summary>' +
      '<div id="lx-open-assumptions-body" style="font-size:12px;margin-top:6px">' +
      '<span style="color:var(--color-text-tertiary)">開くと読み込みます…</span></div></details>';

    el.innerHTML = html;

    var openDetails = el.querySelector("#lx-open-assumptions");
    if (openDetails) {
      openDetails.addEventListener("toggle", function () {
        if (openDetails.open) loadLearnerOpenAssumptions();
      });
    }

    // D層 (D3-6): 根拠カードへ台帳の検証状態を一行併記（非同期・fail-closed）。
    annotateSourceCardsWithLedger(el);
  }

  // D層 (D3-6): 出典タブの根拠カードに、そのチャンクに含まれる数式（equation）・
  // claim の台帳検証状態を一行の事実として併記する。学習者に開かれた ID 供給源は
  // equation は source-chunk API の formulas[].id（解析パイプラインの equation_id）、
  // claim は学習者向け読み取り専用 API `/chunks/{chunk_id}/claim-refs`（コース教材
  // 厳密一致のみを返す）。台帳未記帳 (404)・API 失敗時は何も出さない
  // （fail-closed: 台帳未記帳コースでは表示が一切変わらない）。数値は出さない。
  var _chunkEquationIdsCache = {};
  var _chunkClaimIdsCache = {};
  function annotateSourceCardsWithLedger(root) {
    if (!root || !state.courseId) return;
    var cards = root.querySelectorAll("[data-lx-ledger-chunk]");
    var seen = {};
    Array.prototype.forEach.call(cards, function (card) {
      var chunkId = card.getAttribute("data-lx-ledger-chunk");
      if (!chunkId || seen[chunkId]) return; // 同一チャンクの重複カードは最初の1枚だけ
      seen[chunkId] = true;
      annotateOneSourceCardLedger(card, chunkId);
    });
  }

  async function annotateOneSourceCardLedger(card, chunkId) {
    try {
      var entries = _chunkEquationIdsCache[chunkId];
      if (!entries) {
        entries = [];
        try {
          const res = await apiFetch("/learning/courses/" + state.courseId +
            "/source-chunk/" + encodeURIComponent(chunkId));
          if (res.ok) {
            const data = await res.json();
            var seenIds = {};
            ((data && data.formulas) || []).forEach(function (f) {
              var id = f && f.id ? String(f.id) : "";
              // パイプライン由来の equation_id のみ対象。旧フォーマット変換で付く
              // [[FORMULA_N]] やトピック教材の TOPIC_FORMULA_N は台帳対象ではない。
              if (!id || /^\[\[FORMULA_\d+\]\]$/.test(id) || /^TOPIC_FORMULA_/.test(id)) return;
              if (seenIds[id]) return;
              seenIds[id] = true;
              entries.push({ id: id, label: (f && f.label) ? String(f.label) : "" });
            });
          }
        } catch (_) { /* fail-closed */ }
        _chunkEquationIdsCache[chunkId] = entries;
      }
      // claim 拡張: チャンク→claim ID は学習者向け読み取り専用 API から解決する。
      // equation 経路の成否とは独立に fail-closed（一方の失敗が他方を巻き込まない）。
      var claimEntries = _chunkClaimIdsCache[chunkId];
      if (!claimEntries) {
        claimEntries = [];
        try {
          const cres = await apiFetch("/learning/courses/" + state.courseId +
            "/chunks/" + encodeURIComponent(chunkId) + "/claim-refs");
          if (cres.ok) {
            const cdata = await cres.json();
            ((cdata && cdata.claims) || []).forEach(function (c) {
              var id = c && c.id ? String(c.id) : "";
              if (!id) return;
              claimEntries.push({ id: id, label: (c && c.label) ? String(c.label) : "" });
            });
          }
        } catch (_) { /* fail-closed */ }
        _chunkClaimIdsCache[chunkId] = claimEntries;
      }
      var combined = entries.map(function (e) {
        return { kind: "equation", id: e.id, label: e.label, prefix: "数式 " };
      }).concat(claimEntries.map(function (e) {
        return { kind: "claim", id: e.id, label: e.label, prefix: "主張 " };
      }));
      var shown = {};
      var count = 0;
      // 1カード最大3行（過密防止）。equation・claim を合算した上限で維持する。
      for (var i = 0; i < combined.length && count < 3; i++) {
        var text = await fetchLearnerLedgerFactLine(combined[i].kind, combined[i].id);
        if (!text) continue;
        var line = combined[i].label ? (combined[i].prefix + combined[i].label + " — " + text) : text;
        if (shown[line]) continue; // 同一文言の重複行は出さない
        shown[line] = true;
        count++;
        if (card.isConnected) card.appendChild(makeLedgerLineDiv(line));
      }
    } catch (_) { /* fail-closed */ }
  }

  // D層 (D3-6): 未検証合意リスト（読み取り専用・台帳の投影）。
  // 台帳未記帳のコースでは何も出ない（fail-closed: セクションを畳んだまま空表示）。
  async function loadLearnerOpenAssumptions() {
    var body = document.getElementById("lx-open-assumptions-body");
    if (!body || !state.courseId) return;
    try {
      const res = await apiFetch("/learning/courses/" + state.courseId + "/open-assumptions");
      if (!res.ok) { body.textContent = "現在、表示できる項目はありません。"; return; }
      const data = await res.json();
      const items = (data && data.items) || [];
      if (!items.length) { body.textContent = "現在、表示できる項目はありません。"; return; }
      body.innerHTML = items.map(function (item) {
        var line = escHtml(item.statement || item.target_id);
        var meta = [];
        if (item.scope_count_is_zero) meta.push("検証スコープの記帳なし");
        else meta.push("検証: 記帳あり");
        if (item.has_verification_proposal) meta.push("検証提案あり");
        return '<div style="padding:6px 0;border-bottom:1px dashed var(--color-border,#eee)">' + line +
          '<div style="color:var(--color-text-tertiary);font-size:11px;margin-top:2px">' +
          escHtml(meta.join(" ・ ")) + '</div></div>';
      }).join("");
    } catch (_) {
      body.textContent = "現在、表示できる項目はありません。";
    }
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
  var TRACE_KIND_LABEL = { question: "問い", detour: "寄り道", misconception: "誤答", raw: "記録", tension: "違和感" };
  var TRACE_KIND_CLS = { question: "q", detour: "detour", misconception: "mis", raw: "q", tension: "q" };
  var STATUS_META = {
    open: { label: "未解決", cls: "open" },
    revisited: { label: "再訪推奨", cls: "revisit" },
    resolved: { label: "解決済み", cls: "resolved" },
    articulated: { label: "言語化済み", cls: "open" },
    connected: { label: "接続済み", cls: "resolved" },
    abstracted: { label: "抽象化済み", cls: "resolved" },
  };

  // 違和感ダイジェストカード（TensionMiningAgent Stage 2）。
  // 会話中の割り込み表示はしない: セッション末尾または次回ログイン時に控えめに1枚だけ。
  // 件数バッジ・ランキング化はしない（P7）。
  function renderTensionDigestCard() {
    var digest = state.tensionDigest;
    if (!digest) return ""; // 未ロード（読み込み中）は何も出さない
    // G3: 通信エラーと「候補ゼロ」を区別する。デフォードで隠している間は静かにしておく
    // （押し付けない。P7）— 候補が本当に無いときだけ理由を一行添える。
    if (digest._error) {
      return '<div class="lx-digest-empty">引っかかりの候補を取得できませんでした。</div>';
    }
    var items = Array.isArray(digest.items) ? digest.items : [];
    if (items.length === 0) {
      return '<div class="lx-digest-empty">引っかかりの候補はまだありません。学習が進むと表示されることがあります。</div>';
    }
    var item = null;
    for (var i = 0; i < items.length; i++) {
      if (!state.tensionDeferred[items[i].trace_id]) { item = items[i]; break; }
    }
    if (!item) return "";
    var html = '<div class="lx-revisit lx-tension" data-tension-card="' + escHtml(item.trace_id) + '">';
    html += '<div class="k">引っかかりの気配' + (item.context_label ? ' · ' + escHtml(item.context_label) : '') + '</div>';
    html += '<div class="h">今日の会話で、こんな引っかかりがあったかもしれません</div>';
    html += '<div class="s">『' + escHtml(item.evidence_quote || "") + '』' +
      (item.paraphrase ? '——' + escHtml(item.paraphrase) : '') + '</div>';
    html += '<div class="lx-trace-actions" style="margin-top:8px">';
    html += '<button class="lx-ghost" data-tension-confirm="' + escHtml(item.trace_id) + '">そう、これ</button>';
    html += '<button class="lx-ghost secondary" data-tension-dismiss="' + escHtml(item.trace_id) + '">違う</button>';
    html += '<button class="lx-ghost secondary" data-tension-defer="' + escHtml(item.trace_id) + '">あとで</button>';
    html += '</div></div>';
    return html;
  }

  // 帰属候補の確認カード:「この疑問は◯◯についてでしたか？」（確定は本人のみ。P1）。
  // 様相チップをタップするとその doubt_type で訂正確定できる（自己説明の入口。方法C と連続）。
  function renderAnchorDigestCard() {
    var digest = state.anchorDigest;
    if (!digest) return ""; // 未ロード（読み込み中）は何も出さない
    if (digest._error) {
      return '<div class="lx-digest-empty">問いの帰属候補を取得できませんでした。</div>';
    }
    var items = Array.isArray(digest.items) ? digest.items : [];
    if (items.length === 0) {
      return '<div class="lx-digest-empty">確認できる問いの候補はまだありません。学習が進むと表示されることがあります。</div>';
    }
    var item = null;
    for (var i = 0; i < items.length; i++) {
      if (!state.anchorDeferred[items[i].trace_id]) { item = items[i]; break; }
    }
    if (!item) return "";
    var tid = escHtml(item.trace_id);
    var html = '<div class="lx-revisit lx-anchor" data-anchor-card="' + tid + '">';
    html += '<div class="k">疑問の在り処' + (item.context_label ? ' · ' + escHtml(item.context_label) : '') + '</div>';
    html += '<div class="h">この疑問は「' + escHtml(item.anchor_label || item.anchor_type_label || "") +
      '」の<b>' + escHtml(item.doubt_type_label || "") + '</b>についてでしたか？</div>';
    html += '<div class="s">『' + escHtml(item.question_text || "") + '』</div>';
    html += '<div class="lx-trace-actions" style="margin-top:8px">';
    html += '<button class="lx-ghost" data-anchor-confirm="' + tid + '">そう、これ</button>';
    html += '<button class="lx-ghost secondary" data-anchor-dismiss="' + tid + '">違う</button>';
    html += '<button class="lx-ghost secondary" data-anchor-defer="' + tid + '">あとで</button>';
    html += '</div>';
    // 様相の訂正チップ（提案と違う様相ならタップで訂正確定）
    html += '<div class="lx-trace-actions" style="margin-top:6px;flex-wrap:wrap">';
    ANCHOR_DOUBT_OPTIONS.forEach(function (o) {
      if (o.doubt_type === item.doubt_type) return;
      html += '<button class="lx-ghost secondary" data-anchor-correct="' + tid +
        '" data-anchor-doubt="' + escHtml(o.doubt_type) + '">' + escHtml(o.label) + '</button>';
    });
    html += '</div></div>';
    return html;
  }

  function renderProblemTrails() {
    var data = state.interestTraces;
    var html = '<div class="progress-head" style="margin:18px 0 8px"><h3 style="font-size:14px">問いの軌跡</h3></div>';

    // 違和感ダイジェスト（候補は本人の確定までダイジェスト経由でのみ提示する）
    html += renderTensionDigestCard();
    // 帰属候補の確認カード（llm_candidate は本人の確定までここでのみ提示する。P1）
    html += renderAnchorDigestCard();

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

    // トレース群（status を主役に）。「わたしの地図」との相互リンクのためコンテナに id を付与する
    // (Phase P-1: PersonalMap.annotateTrajectoryList / openTrajectory が data-trace-id を参照する)。
    html += '<div id="lx-trajectory-list">';
    traces.forEach(function (t) {
      if (f !== "all" && t.kind !== f) return;
      var kc = TRACE_KIND_CLS[t.kind] || "q";
      var sm = STATUS_META[t.status] || { label: t.status || "", cls: "open" };
      // data-map-excluded: 「わたしの地図」に反映しない設定の有無（Phase P-3 §6）。
      // PersonalMap.annotateTrajectoryList がこの属性を見て「地図に戻す」チップを付ける。
      html += '<div class="lx-trace ' + kc + '" data-trace-id="' + escHtml(t.id || "") +
        '" data-map-excluded="' + (t.map_excluded ? "1" : "0") + '"><div class="lx-rail"></div><div class="lx-trace-body">';
      html += '<div class="lx-trace-top"><span class="lx-kind-tag">' + escHtml(TRACE_KIND_LABEL[t.kind] || t.kind || "記録") + '</span>';
      html += '<span class="lx-status-tag ' + sm.cls + '">' + escHtml(sm.label) + '</span></div>';
      html += '<div class="lx-trace-text">' + escHtml(t.text || "") + '</div>';
      if (t.context_label) html += '<div class="lx-trace-ctx">' + escHtml(t.context_label) + '</div>';
      // 構造帰属チップ: 「どこに・どう引っかかったか」（確定済みのみサーバが返す）
      if (t.structure_anchor && (t.structure_anchor.anchor_label || t.structure_anchor.doubt_type_label)) {
        html += '<div class="lx-trace-ctx" style="margin-top:2px">📍 ' +
          escHtml(t.structure_anchor.anchor_label || t.structure_anchor.anchor_type_label || "") +
          (t.structure_anchor.doubt_type !== "unclassified"
            ? ' · ' + escHtml(t.structure_anchor.doubt_type_label || "") : '') +
          '</div>';
      }
      // アクション。「この問いに戻る」「解決済みにする」は実データ配線済み（Stage 3）。
      if (t.status !== "resolved") {
        html += '<div class="lx-trace-actions">';
        html += '<button class="lx-ghost" data-trace-return="' + escHtml(t.text || "") + '" data-trace-msg="' + escHtml(t.message_id || "") + '">この問いに戻る</button>';
        html += '<button class="lx-ghost secondary" data-trace-resolve="' + escHtml(t.id || "") + '">解決済みにする</button>';
        // Internalization Prompt: 「なぜ自分に重要か」を言語化させ payload に保存（内発的動機の支援）。
        html += '<button class="lx-ghost secondary" data-trace-why="' + escHtml(t.id || "") + '">なぜ気になった？</button>';
        html += '</div>';
      }
      html += '</div></div>';
    });
    html += '</div>';
    return html;
  }

  // 進捗タブに再訪推奨ドット・違和感/帰属候補の気づきドットを点す（タブ見出し）。
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

    // G4-T: 違和感/帰属候補ダイジェストに本人未確定の候補があるときも、既存の再訪推奨
    // ドットとは区別できる表示で気づけるようにする（件数は出さない・押し付けない）。
    var tensionItems = (state.tensionDigest && Array.isArray(state.tensionDigest.items)) ? state.tensionDigest.items : [];
    var anchorItems = (state.anchorDigest && Array.isArray(state.anchorDigest.items)) ? state.anchorDigest.items : [];
    var hasDigestCandidate =
      tensionItems.some(function (i) { return !state.tensionDeferred[i.trace_id]; }) ||
      anchorItems.some(function (i) { return !state.anchorDeferred[i.trace_id]; });
    var hintDot = btn.querySelector(".lx-tab-dot-hint");
    if (hasDigestCandidate && !hintDot) {
      btn.insertAdjacentHTML(
        "beforeend",
        '<span class="lx-tab-dot-hint" title="確認できる引っかかり・問いの候補があります"></span>'
      );
    } else if (!hasDigestCandidate && hintDot) {
      hintDot.remove();
    }
  }

  // P2: コース切替後の遅延応答を破棄する（cid を開始時に保持し、state へ反映する直前・
  // renderProgressTab/updateProgressTabDot を呼ぶ直前に courseId 一致を確認する）。
  async function loadInterestTraces() {
    if (!state.courseId) return;
    var cid = state.courseId;
    state.interestTraces = {}; // 多重ロード防止のプレースホルダ
    try {
      var res = await apiFetch(
        "/learning/courses/" + cid + "/interest-traces" +
        (state.currentTopicId ? "?topic_id=" + encodeURIComponent(state.currentTopicId) : "")
      );
      var traces = res.ok ? await res.json() : { traces: [] };
    } catch (_) {
      traces = { traces: [] };
    }
    if (state.courseId !== cid) return; // 遅延応答ガード: state 反映・再描画を破棄
    state.interestTraces = traces;
    // 違和感ダイジェスト（次回ログイン時の提示。会話への割り込みはしない）
    if (state.tensionDigest === null) {
      await loadTensionDigest();
    }
    if (state.courseId !== cid) return;
    // 帰属候補ダイジェスト（同上。確認カードは進捗タブでのみ提示する）
    if (state.anchorDigest === null) {
      await loadAnchorDigest();
    }
    if (state.courseId !== cid) return;
    renderProgressTab();
    updateProgressTabDot();
  }

  // Phase P-1: 「わたしの地図」からの遷移先（PersonalMap.init の openTrajectory）。
  // 進捗タブへ切替 → 軌跡が未ロードならロード → 該当アイテムへスクロールし一時ハイライト
  // (jumpToChatMessage と同じ mg-highlight の一時強調パターンを流用)。
  async function openTrajectory(traceId) {
    if (!traceId) return;
    var progressBtn = document.querySelector('#tabBar button[data-tab="progress"]');
    if (progressBtn && !progressBtn.classList.contains("on")) progressBtn.click();
    if (state.interestTraces === null) {
      await loadInterestTraces();
    }
    var target = document.querySelector('[data-trace-id="' + traceId + '"]');
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("mg-highlight");
    setTimeout(function () { target.classList.remove("mg-highlight"); }, 2400);
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

  // G1-4: 最終トピックでもボタンを消さず「確認して完了」を出す（コース完走時の完了体験）。
  function updateNextTopicBtn() {
    var btn = document.getElementById("next-topic-btn");
    if (!btn) return;
    if (!state.currentTopicId) {
      btn.style.display = "none";
      return;
    }
    var next = getNextTopic();
    btn.style.display = "";
    if (next) {
      btn.textContent = "確認して次へ";
      btn.title = "確認問題に回答して次のセクションへ進む: " + (next.title || "");
    } else {
      btn.textContent = "確認して完了";
      btn.title = "確認問題に回答してこのコースの学習を完了する";
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
      // 分野の地図 (Issue F-2 導線3): 寄り道から戻った文脈で「地図で現在地を見る」
      // を提示する。開いたときは戻った位置をハイライトする (§3-4)。
      var destTopic = (state.course && state.course.topics || [])
        .find(function (t) { return t.id === dest; });
      if (window.AtlasCues && destTopic) {
        window.AtlasCues.showCue("detour_return", {
          container: document.getElementById("chat-area"),
          text: "寄り道から「" + destTopic.title + "」へ戻りました。",
          focusTopicId: destTopic.id,
          focusLabel: destTopic.title,
          highlight: true,
        });
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
    state.editingMessageId = null; // 機能3: トピック切替で書き直し状態を解除
    hideEditIndicator();
    state.topicMaterial = [];
    // P1: トピック切替で「直前の check 応答の course_completed」を持ち越さない。
    state.lastCheckCourseCompleted = false;
    // 分野の地図 (gap3): course/topic の文脈を配線し、地図データを正しいカートリッジ・
    // 現在地で取得できるようにする (AtlasData / AtlasMinimap / AtlasCues が参照)。
    var _topicForAtlas = (state.course && (state.course.topics || []).find(function (t) {
      return t.id === topicId;
    })) || null;
    window.AtlasContext = {
      courseId: state.courseId || "",
      topicId: topicId || "",
      topicLabel: _topicForAtlas ? (_topicForAtlas.title || "") : "",
    };
    renderSidebar();
    renderChat();
    renderRightPanel();
    updateNextTopicBtn();
    refreshLectureAvailability();
    // 分野の地図 (Issue F-1): ミニマップはトピック遷移時に再取得する (ポーリングしない)
    if (window.AtlasMinimap) window.AtlasMinimap.refresh();
    // 再構成ループ (R層): トピックの文脈を配線する。自動割り込みはしない（本人が開く）。
    if (window.Reconstruction) window.Reconstruction.setContext(state.courseId || "", topicId || "");

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

  // 分野の地図 (Issue F-2 導線1・2): トピック完了直後の「見晴らしの瞬間」。
  // 完了が章の最後のトピックなら章末 (chapter_end)、それ以外は topic_complete。
  // カードの提示に留め、地図を自動で開かない。抑制ルールは AtlasCues 側が持つ。
  function showAtlasCueAfterAdvance(completedTopic, nextTopic) {
    if (!window.AtlasCues || !completedTopic) return;
    var crossedChapter = nextTopic &&
      completedTopic.chapter_index !== nextTopic.chapter_index;
    var chapters = (state.course && state.course.chapters) || [];
    var chapterTitle = (chapters[completedTopic.chapter_index] || {}).title || "";
    window.AtlasCues.showCue(crossedChapter ? "chapter_end" : "topic_complete", {
      container: document.getElementById("chat-area"),
      text: crossedChapter
        ? "章「" + (chapterTitle || completedTopic.title) + "」を終えました。"
        : "「" + completedTopic.title + "」を確認しました。",
      // gap2: focus はサーバ側 (binding) 解決を優先する。id を渡し、title はラベル縮退用。
      focusTopicId: completedTopic.id,
      focusLabel: completedTopic.title,
    });
  }

  function openCheckModal() {
    if (!state.currentTopicId || state.checkingUnderstanding) return;
    // G1-4: 最終トピック（次が無い）でも確認問題フローへ進み、合格後は完了カードへ繋ぐ。
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
      var directCompleted = getCurrentTopic();
      var directOverlay = document.getElementById("check-overlay");
      if (directOverlay) directOverlay.remove();
      // 前進は再アンカー（selectTopic 既定で detour を閉じ、レクチャー中なら終了する）。
      if (directNext) {
        await selectTopic(directNext.id);
        showAtlasCueAfterAdvance(directCompleted, directNext);
      } else {
        // G1-4: 最終トピックでは完了カードへ繋ぐ（次の案内が消えるだけで終わらせない）。
        // P1: このパスはサーバー応答を伴わないため、断定文言は直前の /check 応答が
        // 確認済みの course_completed（state.lastCheckCourseCompleted）でのみ出す。
        if (lectureState.active) deactivateLecture();
        showCourseCompletionCard(directCompleted, state.lastCheckCourseCompleted);
      }
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
      // P1: サーバー正本の完了状態を保持する（data-advance 経路が後で参照する）。
      state.lastCheckCourseCompleted = !!data.course_completed;
      if (data.passed) {
        var next = getNextTopic();
        var completedTopic = getCurrentTopic();
        var overlay = document.getElementById("check-overlay");
        if (overlay) overlay.remove();
        // 合格 → 次トピックへ再アンカー。detour 残はここで自動的に解消される。
        if (next) {
          await selectTopic(next.id);
          // 分野の地図 (Issue F-2 導線1・2): 完了直後に「地図で現在地を見る」を提示
          showAtlasCueAfterAdvance(completedTopic, next);
        } else {
          // G1-4: 最終トピック合格 → コース完走の完了カードへ繋ぐ（事実文のみ・数値なし）。
          // P1: サーバーが course_completed===true を確認したときのみ断定文言を出す。
          if (lectureState.active) deactivateLecture();
          showCourseCompletionCard(completedTopic, data.course_completed === true);
        }
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

  // G1-4 / P1: コース完走時の完了カード。事実文のみ（数値・スコア・祝祭演出は出さない）で、
  // 他のコースへの導線とわたしの地図への導線を添える。
  // courseCompleted は呼び出し元が保持するサーバー正本の判定（/check レスポンスの
  // course_completed）。true のときだけ「全トピックを学習しました」と断定してよい。
  // falsy（未確認・不明を含む）なら fail-closed で縮退した事実文にする（途中を飛ばしても
  // 断定文言が出ないようにする）。
  function showCourseCompletionCard(completedTopic, courseCompleted) {
    var existing = document.getElementById("course-complete-overlay");
    if (existing) existing.remove();
    var courseTitle = state.course ? (state.course.title || "") : "";
    var titleText = courseCompleted ? "学習を完了しました" : "最後のトピックの確認を終えました";
    var bodyText = courseCompleted
      ? ('「' + escHtml(courseTitle) + '」の全トピックを学習しました。')
      : "まだ確認を終えていないトピックがあります。";
    var overlay = document.createElement("div");
    overlay.id = "course-complete-overlay";
    overlay.className = "check-overlay";
    overlay.innerHTML =
      '<div class="check-box">' +
        '<div class="check-title">' + escHtml(titleText) + '</div>' +
        '<div class="check-section">' + bodyText + '</div>' +
        (completedTopic
          ? '<div class="check-question">最後のトピック「' + escHtml(completedTopic.title || "") +
            '」の確認問題に回答しました。</div>'
          : "") +
        '<div class="check-actions" style="justify-content:flex-start;flex-wrap:wrap">' +
          '<button class="check-secondary" id="course-complete-other">他のコースを見る</button>' +
          '<button class="check-secondary" id="course-complete-map">わたしの地図を見る</button>' +
          '<button class="check-primary" id="course-complete-close">閉じる</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    var closeBtn = document.getElementById("course-complete-close");
    if (closeBtn) closeBtn.addEventListener("click", function () { overlay.remove(); });
    var otherBtn = document.getElementById("course-complete-other");
    if (otherBtn) {
      otherBtn.addEventListener("click", function () {
        overlay.remove();
        var select = document.getElementById("course-select");
        if (select) select.focus();
      });
    }
    var mapBtn = document.getElementById("course-complete-map");
    if (mapBtn) {
      mapBtn.addEventListener("click", function () {
        overlay.remove();
        if (window.PersonalMapHome) window.PersonalMapHome.open();
      });
    }
  }

  // ── Send Message ───────────────────────────────────────────────────
  async function sendMessage(text, actionPayload) {
    if (!text || state.sending || !state.currentTopicId) return null;
    let respData = null;  // 音声会話モードが answer/sources を使うため応答を返す

    // 機能3（書き直し）: _replace_message_id が指定されていれば、その往復以降を
    // クライアント履歴から取り除いてから新しいターンを積む。サーバへは replace_message_id を
    // 送り、サーバ正本の履歴も同位置で切り詰め・派生痕跡を supersede させる。
    let replaceMessageId = null;
    if (actionPayload && actionPayload._replace_message_id) {
      replaceMessageId = actionPayload._replace_message_id;
      delete actionPayload._replace_message_id;
      const ri = _findMessageIndexById(replaceMessageId);
      if (ri !== -1) state.chatMessages = state.chatMessages.slice(0, ri);
    }

    // クライアントで採番し、サーバ永続履歴・関心痕跡と同じ id を in-memory にも持たせる
    // （「この問いに戻る」でこの往復へジャンプするためのアンカー）。
    const userMsgId = genMsgId();
    state.chatMessages.push({ role: "user", content: text, id: userMsgId });
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
    // 構造帰属（方法A）: 「ここについて質問」で選択したテキストをこの1問にだけ添える。
    if (state.pendingSelection && !payload.selection_text) {
      payload.selection_text = state.pendingSelection.text;
      payload.selection_segment_id = state.pendingSelection.segment_id;
    }
    clearPendingSelection();

    try {
      const res = await apiFetch("/learning/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/chat", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          message_id: userMsgId,
          history: state.chatMessages.slice(0, -1),
          position_anchor: anchorAtAsk,
          ...(replaceMessageId ? { replace_message_id: replaceMessageId } : {}),
          ...payload,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        respData = data;
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
        state.lastGrounding = data.content_grounding || null;
        // 構造帰属（方法A）: 同期記録された明示アンカーを発話バブルのチップに反映する。
        if (data.structure_anchor) {
          for (let i = state.chatMessages.length - 1; i >= 0; i--) {
            if (state.chatMessages[i].id === userMsgId) {
              state.chatMessages[i].structure_anchor = data.structure_anchor;
              break;
            }
          }
        }
        state.chatMessages.push({
          role: "assistant",
          content: data.answer,
          next_actions: data.next_actions || [],
          support_mode: data.support_mode || "",
          status_label: data.status_label || "",
          origin: data.origin || null,
          // ── B層 Stage M ──
          overall_tier: data.overall_tier || null,
          content_grounding: data.content_grounding || null,
          sources: data.sources || [],
          position_anchor: data.position_anchor || null,
          // 分野の地図 (Issue C-3): 学習パス提案カード
          atlas_path_card: registerAtlasPathCard(data.atlas_path_card),
          // 構造帰属（方法C）: 回答末尾の1タップ確認プロンプト（ゲート済みのときのみ）
          anchor_confirm: data.anchor_confirm || null,
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
    return respData;
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
  // 「教材に沿って質問」「自由に質問・探索」の2ボタンは廃止し「質問」1つに統合。
  // 事前に意図を選ばせず、常に RAG 検索を行った上で回答が何に基づくか
  // （教材/別の資料/モデル生成）を事後に判定してバッジ表示する（groundingBadge 参照）。
  // intent_mode 自体（on_path/explore）は寄り道の復帰導線のため内部的に残し、
  // 現在すでに寄り道中なら explore として送る（Enter キーと同じ判定）。
  function initInput() {
    const input = document.getElementById("chat-input");
    const btn = document.getElementById("send-btn");          // 質問（統合ボタン）
    const clearBtn = document.getElementById("chat-clear-btn");

    function sendWith(mode) {
      var text = input.value.trim();
      if (!text) return;
      if (mode === "on_path") Session.clearDetour();
      // 機能3: 書き直し中なら replace_message_id を添えて以降を差し替える
      if (state.editingMessageId) {
        var replaceId = state.editingMessageId;
        state.editingMessageId = null;
        hideEditIndicator();
        sendMessage(text, { intent_mode: mode, _replace_message_id: replaceId });
        return;
      }
      sendMessage(text, { intent_mode: mode });
    }

    function sendCurrent() { sendWith(Session.inDetour() ? "explore" : "on_path"); }

    btn.addEventListener("click", sendCurrent);
    if (clearBtn) clearBtn.addEventListener("click", clearChatHistory);
    // 🤖 気軽に話せる先生（ハンズフリー音声会話）
    const voiceBtn = document.getElementById("voice-mode-btn");
    if (voiceBtn) voiceBtn.addEventListener("click", toggleVoiceMode);
    const voiceClose = document.getElementById("voice-close");
    if (voiceClose) voiceClose.addEventListener("click", stopVoiceMode);

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        sendCurrent();
      }
    });
  }

  // ============ ハンズフリー音声会話（気軽に話せる先生・casual モード） ============
  // MediaRecorder + WebAudio の無音検知で発話を区切り、Whisper 文字起こし →
  // casual チャット → 応答を TTS 再生、のループを回す。再生中はマイクを止める。
  // 応答の第1根拠チャンクをパネルに教材表示する（会話しながら教材が見える）。
  const VOICE_SILENCE_MS = 1400;      // 発話後この長さの沈黙で区切って送信
  const VOICE_MIN_SPEECH_MS = 400;    // これ未満の発話は物音とみなして破棄
  const VOICE_RMS_THRESHOLD = 0.015;  // 発話とみなす音量（RMS）
  const VOICE_IDLE_RESET_MS = 60000;  // 無発話でセグメントを作り直す（メモリ抑制）
  // N33: エラー定型文の TTS はこの間隔をあける（失敗が続くときに TTS 呼び出しを
  // 連打しない。間隔内はパネル表示のみに縮退）。
  const VOICE_ERROR_TTS_COOLDOWN_MS = 15000;
  const VOICE_ERROR_MESSAGE = "エラーが発生しました。もう一度どうぞ。";

  const voiceState = {
    active: false,
    stream: null,
    recorder: null,
    chunks: [],
    mimeType: "",
    audioCtx: null,
    analyser: null,
    vadTimer: null,
    speechDetected: false,
    speechStart: 0,
    silenceStart: 0,
    segmentStart: 0,
    busy: false,   // 文字起こし〜応答再生中（この間は区切り検知を止める）
    player: null,
    lastErrorTtsAt: 0,  // N33: エラー定型文 TTS の最終試行時刻
  };

  function setVoiceStatus(kind, label) {
    const el = document.getElementById("voice-status");
    if (!el) return;
    el.className = "voice-status" + (kind ? " " + kind : "");
    el.textContent = label;
  }

  function setVoiceTranscript(text) {
    const el = document.getElementById("voice-transcript");
    if (el) el.textContent = text || "";
  }

  function toggleVoiceMode() {
    if (voiceState.active) stopVoiceMode();
    else startVoiceMode();
  }

  async function startVoiceMode() {
    if (!state.currentTopicId) {
      alert("トピックを選択してから音声会話を開始してください。");
      return;
    }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      alert("このブラウザは録音に対応していません。Chrome または Edge をお試しください。");
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (_) {
      alert("マイクの使用が許可されていません。ブラウザのアドレスバーからマイクの許可を確認してください。");
      return;
    }
    voiceState.stream = stream;
    voiceState.mimeType = pickVoiceMimeType();
    voiceState.active = true;
    voiceState.busy = false;

    // 無音検知用の AnalyserNode（録音とは独立に音量だけを監視する）
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    voiceState.audioCtx = new AudioCtx();
    const source = voiceState.audioCtx.createMediaStreamSource(stream);
    voiceState.analyser = voiceState.audioCtx.createAnalyser();
    voiceState.analyser.fftSize = 2048;
    source.connect(voiceState.analyser);

    const panel = document.getElementById("voice-panel");
    if (panel) panel.classList.add("on");
    const btn = document.getElementById("voice-mode-btn");
    if (btn) btn.classList.add("active");
    setVoiceTranscript("");
    setVoiceStatus("listening", "聞いています… 話し終えると自動で送信されます");

    startVoiceSegment();
    voiceState.vadTimer = setInterval(voiceVadTick, 100);
  }

  function stopVoiceMode() {
    voiceState.active = false;
    if (voiceState.vadTimer) { clearInterval(voiceState.vadTimer); voiceState.vadTimer = null; }
    if (voiceState.recorder && voiceState.recorder.state !== "inactive") {
      voiceState.recorder.onstop = null;  // 終了時の残りセグメントは送信しない
      try { voiceState.recorder.stop(); } catch (_) { /* noop */ }
    }
    voiceState.recorder = null;
    if (voiceState.stream) {
      voiceState.stream.getTracks().forEach(function (t) { t.stop(); });
      voiceState.stream = null;
    }
    if (voiceState.audioCtx) { try { voiceState.audioCtx.close(); } catch (_) { /* noop */ } voiceState.audioCtx = null; }
    if (voiceState.player) { try { voiceState.player.pause(); } catch (_) { /* noop */ } voiceState.player = null; }
    const panel = document.getElementById("voice-panel");
    if (panel) panel.classList.remove("on");
    const material = document.getElementById("voice-material");
    if (material) { material.classList.remove("on"); material.innerHTML = ""; }
    const btn = document.getElementById("voice-mode-btn");
    if (btn) btn.classList.remove("active");
  }

  function pickVoiceMimeType() {
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    for (const c of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c)) return c;
    }
    return "";
  }

  function startVoiceSegment() {
    if (!voiceState.active || !voiceState.stream) return;
    voiceState.chunks = [];
    voiceState.speechDetected = false;
    voiceState.speechStart = 0;
    voiceState.silenceStart = 0;
    voiceState.segmentStart = Date.now();
    const opts = voiceState.mimeType ? { mimeType: voiceState.mimeType } : undefined;
    const rec = new MediaRecorder(voiceState.stream, opts);
    rec.ondataavailable = function (e) { if (e.data && e.data.size > 0) voiceState.chunks.push(e.data); };
    rec.onstop = function () { handleVoiceSegment(); };
    rec.start();
    voiceState.recorder = rec;
  }

  function voiceRms() {
    const analyser = voiceState.analyser;
    if (!analyser) return 0;
    const buf = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    return Math.sqrt(sum / buf.length);
  }

  function voiceVadTick() {
    if (!voiceState.active || voiceState.busy || !voiceState.recorder) return;
    const now = Date.now();
    const rms = voiceRms();
    if (rms >= VOICE_RMS_THRESHOLD) {
      if (!voiceState.speechDetected) {
        voiceState.speechDetected = true;
        voiceState.speechStart = now;
        setVoiceStatus("listening", "聞き取り中…");
      }
      voiceState.silenceStart = 0;
      return;
    }
    if (voiceState.speechDetected) {
      if (!voiceState.silenceStart) {
        voiceState.silenceStart = now;
      } else if (now - voiceState.silenceStart >= VOICE_SILENCE_MS) {
        // 会話の区切れ目: 発話が短すぎればセグメントを捨てて作り直す
        if (voiceState.silenceStart - voiceState.speechStart < VOICE_MIN_SPEECH_MS) {
          restartVoiceSegment();
        } else if (voiceState.recorder.state !== "inactive") {
          voiceState.busy = true;  // onstop → handleVoiceSegment で送信
          voiceState.recorder.stop();
        }
      }
    } else if (now - voiceState.segmentStart >= VOICE_IDLE_RESET_MS) {
      restartVoiceSegment();  // 長時間無発話: 録りっぱなしを避ける
    }
  }

  function restartVoiceSegment() {
    if (!voiceState.recorder) return;
    voiceState.recorder.onstop = null;  // 破棄（handleVoiceSegment を呼ばない）
    try { voiceState.recorder.stop(); } catch (_) { /* noop */ }
    startVoiceSegment();
  }

  async function handleVoiceSegment() {
    if (!voiceState.active) return;
    const blob = new Blob(voiceState.chunks, { type: voiceState.mimeType || "audio/webm" });
    voiceState.chunks = [];
    if (blob.size < 1000) { resumeVoiceListening(); return; }

    setVoiceStatus("thinking", "文字起こし中…");
    let text = "";
    let transcribeFailed = false;
    try {
      text = await transcribeVoiceBlob(blob);
    } catch (_) { transcribeFailed = true; }
    if (!voiceState.active) return;
    if (transcribeFailed) {
      // N33: サーバーエラーを無言で聞き取りに戻さない。短い定型文で知らせてから再開する。
      await voiceErrorFeedback(VOICE_ERROR_MESSAGE);
      if (voiceState.active) resumeVoiceListening();
      return;
    }
    if (!text) { resumeVoiceListening(); return; }  // 無音・聞き取れず（エラーではない）

    setVoiceTranscript("あなた: " + text);
    setVoiceStatus("thinking", "先生が考えています…");
    const data = await sendMessage(text, { intent_mode: "casual" });
    if (!voiceState.active) return;
    if (!data || !data.answer) {
      // N33: チャット失敗もエラーとして知らせる（sendMessage はエラー時 null を返す）。
      await voiceErrorFeedback(VOICE_ERROR_MESSAGE);
      if (voiceState.active) resumeVoiceListening();
      return;
    }

    showVoiceSourceMaterial(data.sources || []);
    const spoken = await speakVoiceAnswer(data.answer);
    if (!spoken && voiceState.active) {
      // N33: TTS 自体が失敗しているのでパネル表示のみに縮退（TTS 再試行しない）。
      setVoiceTranscript("音声を再生できませんでした。回答はチャット欄に表示されています。");
    }
    if (voiceState.active) resumeVoiceListening();
  }

  // N33: ハンズフリー中のサーバーエラーを無言にしない。可能なら短い定型文を TTS で
  // 再生し、TTS 自体が失敗している場合はボイスパネルへの表示のみに縮退する。
  // 定型文 TTS は VOICE_ERROR_TTS_COOLDOWN_MS に1回までに抑え、失敗の連鎖で
  // TTS 呼び出しのループにならないようにする（再帰・自動再試行はしない）。
  async function voiceErrorFeedback(message) {
    if (!voiceState.active) return;
    setVoiceTranscript(message);
    const now = Date.now();
    if (voiceState.lastErrorTtsAt && now - voiceState.lastErrorTtsAt < VOICE_ERROR_TTS_COOLDOWN_MS) {
      return; // パネル表示のみに縮退
    }
    voiceState.lastErrorTtsAt = now;
    try {
      const res = await apiFetch("/learning/voice/speak", {
        method: "POST", body: JSON.stringify({ text: message }),
      });
      if (!res.ok) return; // パネル表示のみに縮退
      const audioB64 = (await res.json()).audio_base64 || "";
      if (!audioB64 || !voiceState.active) return;
      setVoiceStatus("speaking", "先生が話しています…");
      await new Promise(function (resolve) {
        const player = new Audio("data:audio/mp3;base64," + audioB64);
        voiceState.player = player;
        player.onended = resolve;
        player.onerror = resolve;
        player.play().catch(resolve);
      });
      voiceState.player = null;
    } catch (_) { /* パネル表示のみに縮退 */ }
  }

  function resumeVoiceListening() {
    voiceState.busy = false;
    setVoiceStatus("listening", "聞いています… 話し終えると自動で送信されます");
    startVoiceSegment();
  }

  async function transcribeVoiceBlob(blob) {
    const ext = (voiceState.mimeType || "").indexOf("mp4") >= 0 ? "mp4" : "webm";
    const form = new FormData();
    form.append("audio", blob, "speech." + ext);
    // apiFetch は Content-Type: application/json を強制するため multipart は素の fetch で送る
    const headers = {};
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    const res = await fetch(API + "/learning/voice/transcribe?language=ja", {
      method: "POST", headers: headers, body: form,
    });
    // N33: サーバーエラーは「無音（空文字）」と区別して throw する
    // （呼び出し側がエラーフィードバックを返せるように）。
    if (!res.ok) throw new Error("transcribe failed: " + res.status);
    const data = await res.json();
    return (data.text || "").trim();
  }

  // 応答を TTS で再生する。再生できたら true、TTS が使えない環境・失敗時は false
  //（N33: 呼び出し側がパネル表示のみへ縮退できるように成否を返す。テキストは残る）。
  async function speakVoiceAnswer(answerText) {
    let audioB64 = "";
    try {
      const res = await apiFetch("/learning/voice/speak", {
        method: "POST", body: JSON.stringify({ text: answerText }),
      });
      if (res.ok) audioB64 = (await res.json()).audio_base64 || "";
    } catch (_) { /* best-effort */ }
    if (!audioB64 || !voiceState.active) return false;
    setVoiceStatus("speaking", "先生が話しています…");
    await new Promise(function (resolve) {
      const player = new Audio("data:audio/mp3;base64," + audioB64);
      voiceState.player = player;
      player.onended = resolve;
      player.onerror = resolve;
      player.play().catch(resolve);
    });
    voiceState.player = null;
    return true;
  }

  // 応答の第1根拠チャンクをパネルに教材表示する（会話中に教材が画面に出る）。
  async function showVoiceSourceMaterial(sources) {
    const el = document.getElementById("voice-material");
    if (!el) return;
    const top = (sources || []).find(function (s) { return s && s.chunk_id; });
    if (!top) { el.classList.remove("on"); el.innerHTML = ""; return; }
    try {
      const res = await apiFetch(
        "/learning/courses/" + state.courseId + "/source-chunk/" + encodeURIComponent(top.chunk_id));
      if (!res.ok) return;
      const data = await res.json();
      el.innerHTML =
        '<div class="voice-material-title">📖 いま話している題材: ' +
        escHtml(data.source_title || top.source_title || "教材") +
        (data.section ? " · " + escHtml(data.section) : "") + "</div>" +
        renderMaterialChunk({ text: data.text, formulas: data.formulas, figures: data.figures });
      hydrateMaterialFigures(el);
      el.classList.add("on");
    } catch (_) { /* best-effort */ }
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
        // G4-D: description をホバーで見られるよう title 属性に入れる。
        const titleAttr = c.description ? ' title="' + escHtml(c.description) + '"' : "";
        html += '<option value="' + escHtml(c.id) + '"' + titleAttr + selected + '>' + escHtml(c.title) + '</option>';
      });
      html += '</optgroup>';
    }

    // 受講可能なコース optgroup
    if (enrollableCourses.length > 0) {
      html += '<optgroup label="新しく受講可能なコース">';
      enrollableCourses.forEach(function (c) {
        const titleAttr = c.description ? ' title="' + escHtml(c.description) + '"' : "";
        html += '<option value="enroll:' + escHtml(c.id) + '"' + titleAttr + '>' + escHtml(c.title) + '</option>';
      });
      html += '</optgroup>';
    }

    select.innerHTML = html;
  }

  // G1-5 / 指摘4: 受講登録の確認モーダル。タイトル＋description を提示し、キャンセル時は
  // select を元の値へ戻す（確認なし即時 enroll をやめる）。
  // callback(true) は enrollCourse を await する非同期関数を渡す想定。失敗（例外）時は
  // モーダルを閉じずにエラーを表示し、ボタンを再有効化して再試行できるようにする
  // （通信中は二重送信を防ぐため両ボタンを disabled にする）。
  function openEnrollConfirm(course, callback) {
    var existing = document.getElementById("enroll-confirm-overlay");
    if (existing) existing.remove();
    var overlay = document.createElement("div");
    overlay.id = "enroll-confirm-overlay";
    overlay.className = "check-overlay";
    var title = course ? (course.title || "") : "";
    var desc = course && course.description ? course.description : "";
    overlay.innerHTML =
      '<div class="check-box">' +
        '<div class="check-title">このコースを受講しますか？</div>' +
        '<div class="check-section">' + escHtml(title) + '</div>' +
        (desc ? '<div class="check-question">' + escHtml(desc) + '</div>' : '') +
        '<div id="enroll-confirm-error" class="enroll-confirm-error" hidden></div>' +
        '<div class="check-actions">' +
          '<button class="check-secondary" id="enroll-confirm-cancel">キャンセル</button>' +
          '<button class="check-primary" id="enroll-confirm-ok">受講する</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    var cancelBtn = document.getElementById("enroll-confirm-cancel");
    var okBtn = document.getElementById("enroll-confirm-ok");
    var errorEl = document.getElementById("enroll-confirm-error");
    var busy = false;
    function setBusy(next) {
      busy = next;
      cancelBtn.disabled = busy;
      okBtn.disabled = busy;
      okBtn.textContent = busy ? "受講手続き中…" : "受講する";
    }
    function showError(message) {
      errorEl.textContent = message;
      errorEl.hidden = false;
    }
    cancelBtn.addEventListener("click", function () {
      if (busy) return;
      overlay.remove();
      callback(false);
    });
    okBtn.addEventListener("click", async function () {
      if (busy) return;
      setBusy(true);
      errorEl.hidden = true;
      try {
        await callback(true);
        overlay.remove();
      } catch (err) {
        showError("受講登録に失敗しました。もう一度お試しください。" +
          (err && err.message ? "（" + err.message + "）" : ""));
        setBusy(false);
      }
    });
    overlay.addEventListener("click", function (e) {
      if (busy) return;
      if (e.target === overlay) {
        overlay.remove();
        callback(false);
      }
    });
  }

  function initCourseSelectHandler() {
    const select = document.getElementById("course-select");

    select.addEventListener("change", function () {
      const val = this.value;
      const prevVal = state.courseId || "";
      if (val.indexOf("enroll:") === 0) {
        // 未受講コース → 受講前に確認（G1-5）。キャンセル時・失敗時は select を元に戻す。
        const courseId = val.substring(7);
        const course = (_allCourses || []).find(function (c) { return c.id === courseId; });
        openEnrollConfirm(course, async function (confirmed) {
          if (confirmed) {
            try {
              await enrollCourse(courseId);
            } catch (err) {
              // 指摘4: 失敗時も select を元に戻し、再試行できるようにする
              // （openEnrollConfirm 側がエラー表示・ボタン再有効化を行う）。
              select.value = prevVal;
              throw err;
            }
          } else {
            select.value = prevVal;
          }
        });
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
    state.lastCheckCourseCompleted = false;

    // P2: 違和感/帰属候補ダイジェスト・関心痕跡は前のコースのものを残さない
    // （state 宣言時の初期値と同じ形に戻す）。
    state.interestTraces = null;
    state.tensionDigest = null;
    state.tensionDeferred = {};
    state.anchorDigest = null;
    state.anchorDeferred = {};

    // Phase P-1: コース切替で「わたしの地図」のキャッシュ・表示状態を破棄させる。
    if (window.PersonalMap) window.PersonalMap.invalidate();
    // Phase P-3: 最上位「わたしの地図」も同様にキャッシュを破棄する（本人スコープの
    // コース横断ネットワークだが、コース切替のたびに古い表示を残さないよう揃える）。
    if (window.PersonalMapHome) window.PersonalMapHome.invalidate();

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

  // 指摘4: 失敗（非2xx・通信例外）を握りつぶさず throw する。呼び出し元
  // （openEnrollConfirm 経由の initCourseSelectHandler）がこれを捕捉して
  // select を復元し、再試行できる状態に戻す。
  async function enrollCourse(courseId) {
    const res = await apiFetch("/learning/courses/" + courseId + "/enroll", { method: "POST" });
    if (!res.ok) {
      let detail = "";
      try {
        const errBody = await res.json();
        detail = errBody && errBody.detail ? errBody.detail : "";
      } catch (_) { /* detail を JSON 化できない応答は detail なし扱い */ }
      throw new Error(detail || ("HTTP " + res.status));
    }
    const data = await res.json();
    // Refresh course list and switch to the new course
    const courses = await loadCourses();
    _allCourses = courses;
    await switchCourse(data.id);
    return true;
  }

  function showNoCourseState(hasEnrollable = false) {
    removeVersionNoticeBanner();
    const select = document.getElementById("course-select");
    if (!hasEnrollable) {
      select.innerHTML = '<option value="">コースなし</option>';
      select.disabled = true;
    } else {
      select.disabled = false; // 受講可能コースがあれば有効化しておく
    }

    var ca = document.getElementById("chat-area");
    if (hasEnrollable) {
      // G4-D: 受講可能コースの description をここでも見られるようにする（プルダウンの
      // title 属性はホバーでしか気づけないため）。
      var enrollable = (_allCourses || []).filter(function (c) { return c.is_enrollable; });
      var html = '<div class="no-course-empty">';
      html += '<div class="no-course-message" style="height:auto">左上のプルダウンから受講するコースを選択してください。</div>';
      if (enrollable.length > 0) {
        html += '<div class="no-course-list">';
        enrollable.forEach(function (c) {
          html += '<div class="no-course-item"><div class="no-course-item-title">' + escHtml(c.title) + '</div>';
          if (c.description) {
            html += '<div class="no-course-item-desc">' + escHtml(c.description) + '</div>';
          }
          html += '</div>';
        });
        html += '</div>';
      }
      html += '</div>';
      ca.innerHTML = html;
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

  // V層（migration 037）: 受講中コースが削除予約されていれば猶予バナーを表示する。
  // 学習者は版に追従（opt-in 取り込みは教員向け）。fail-open: 取得失敗時は何もしない。
  async function showVersionNoticeBanner(courseId) {
    const existing = document.getElementById("vg-learner-banner");
    if (existing) existing.remove();
    let notice = null;
    try {
      const res = await apiFetch("/learning/courses/" + courseId + "/version-notice");
      if (res.ok) notice = await res.json();
    } catch (_) { return; }
    if (!notice || notice.lifecycle !== "pending_deletion") return;
    const when = notice.delete_purge_after ? new Date(notice.delete_purge_after) : null;
    const whenText = when && !isNaN(when.getTime())
      ? when.getFullYear() + "/" + (when.getMonth() + 1) + "/" + when.getDate()
      : "";
    const banner = document.createElement("div");
    banner.id = "vg-learner-banner";
    banner.style.cssText = "background:#fdecea;color:#8a1c1c;border:1px solid #e0a0a0;border-radius:6px;padding:10px 14px;margin:8px;font-size:13px";
    // whenText 欠落時に「近日中に以降に」とならないよう、期限の有無で語句を組み立てる。
    const lead = whenText ? (whenText + " 以降に") : "近日中に";
    banner.textContent = "このコースは" + lead +
      "削除される予定です。それまでは通常どおり学習できます。" +
      (notice.delete_reason ? "（理由: " + notice.delete_reason + "）" : "");
    // index.html に <main>/#app は無い。学習ビューの主カラム .mn に載せ、
    // コース離脱時（showNoCourseState / renderAuth）に確実に除去して他画面へ残さない。
    const host = document.querySelector(".mn") || document.querySelector(".app") || document.body;
    host.insertBefore(banner, host.firstChild);
  }

  function removeVersionNoticeBanner() {
    const b = document.getElementById("vg-learner-banner");
    if (b) b.remove();
  }

  async function loadAndRenderCourse() {
    const courseData = await loadCourse(state.courseId);
    if (!courseData) return;
    const progress = await loadProgress(state.courseId);

    // Issue #145: マスターデータと個人レイヤーを分離して管理する
    state.course = courseData.master;
    state.personalLayer = courseData.personal;
    loadLearningSupportContext();
    showVersionNoticeBanner(state.courseId);
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

  // クライアント側でメッセージ id を採番する。サーバへ送って永続履歴・関心痕跡に焼き込み、
  // 「この問いに戻る」で該当往復へジャンプできるようにする。
  function genMsgId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "m-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  // 指定 id のチャットメッセージへスクロール＋一時ハイライトする。DOM に無ければ false。
  function jumpToChatMessage(msgId) {
    if (!msgId) return false;
    var elm = document.getElementById("msg-" + msgId);
    if (!elm) return false;
    elm.scrollIntoView({ behavior: "smooth", block: "center" });
    elm.classList.add("mg-highlight");
    setTimeout(function () { elm.classList.remove("mg-highlight"); }, 2400);
    return true;
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
  // 第2引数 payload は分野の地図 (Issue C-2) の構造化ペイロード
  // ({atlas_context: {node_id, level, skeleton_version, action, ...}}) などを
  // チャット API へそのまま引き渡すためのもの (自由文のみに依存しない)。
  window.sendPrompt = function (text, payload) {
    sendMessage(text, payload);
  };

  // ── Interactive Lecture Mode (Issue #66 / スライド同期 migration 040) ─────
  // lectureState.deck は全セグメントのスライドをフラット化した一覧で、再生・表示・
  // ナビゲーションはすべてこのデッキ基準で行う（§1 レクチャースライド同期設計）。
  // segments / currentSegmentIndex は互換のため残す（Session の detour 位置復帰などが
  // セグメント＝チャンク粒度で参照する）。
  const lectureState = {
    active: false,
    segments: [],
    currentSegmentIndex: 0,
    deck: [], // [{chunk_id, segment_index, slide_index, display_text, spoken_text, formulas, has_audio, duration_ms, language, segment_mode}]
    currentDeckIndex: 0,
    playing: false,
    audio: null,
    pausePositionMs: 0,
    sendingInterrupt: false,
    highlightTimer: null,
    resizeTimer: null,
    voiceRecognition: null,
    loadingAudio: false, // TTS フェッチ中フラグ（連打による多重再生を防ぐ）
    readingSentence: -1, // 現在読み上げ中の文インデックス（-1 = 未設定）
  };

  // 現トピックに再生可能な音声があるかを確認し、レクチャーボタンの有効/無効を更新する。
  // 音声生成は管理画面のみで行う方針のため、ここでは生成は一切トリガーしない。
  async function refreshLectureAvailability() {
    state.topicHasAudio = false;
    state.topicStaleLanguage = false;
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
          state.topicStaleLanguage = !!data.stale_language;
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
    // G4-L: 無効理由はツールチップだけでなく、ボタン近傍のインライン表示でも分かるようにする。
    var hint = document.getElementById("lecture-toggle-hint");
    // レクチャー再生中はトグル（テキストへ戻る）を常に許可する。
    var enabled = lectureState.active || state.topicHasAudio;
    btn.disabled = !enabled;
    if (enabled) {
      btn.classList.remove("disabled");
      // stale_language は表示のみ（トグルの有効/無効判定は従来どおり has_audio）。
      btn.title = state.topicStaleLanguage
        ? "音声の言語が更新待ちです"
        : "レクチャーモード切替";
      if (hint) { hint.hidden = true; hint.textContent = ""; }
    } else {
      btn.classList.add("disabled");
      btn.title = "このトピックの音声はまだ生成されていません（管理画面で音声を生成してください）";
      if (hint) { hint.hidden = false; hint.textContent = "音声未生成"; }
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
    // レクチャー中は下段（チャット）を畳んでいるため、質問はフローティングの
    // 割込みポップアップで受ける（講義を一時停止して開く）。
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

    // ウィンドウリサイズ時にスライドのフィット（フォント縮小/等比縮小）を再計算する。
    window.addEventListener("resize", function () {
      if (!lectureState.active) return;
      if (lectureState.resizeTimer) clearTimeout(lectureState.resizeTimer);
      lectureState.resizeTimer = setTimeout(function () {
        lectureState.resizeTimer = null;
        fitLectureSlideContent();
      }, 150);
    });
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

  // レクチャーモードを終了して通常表示へ戻す（トグル・トピック切替の両方から使う）。
  // レクチャー中は下段（チャット/ホワイトボード）と分割ハンドルを畳んでいるので、
  // ここでその畳み（.lecture-on）を解除し、専用UIの後片付けを行う。
  function deactivateLecture() {
    if (!lectureState.active) return;
    var toggleBtn = document.getElementById("lecture-toggle");
    var lecturePlayer = document.getElementById("lecture-player");
    var mn = document.querySelector(".mn");

    lectureState.active = false;
    stopPlayback();
    closeInterruptChat();
    if (mn) mn.classList.remove("lecture-on");
    if (toggleBtn) {
      toggleBtn.classList.remove("active");
      toggleBtn.innerHTML = "&#127897; レクチャー";
    }
    hideLectureSlideNotice();
    var banner = document.getElementById("lecture-complete-banner");
    if (banner) banner.remove();
    if (lecturePlayer) {
      lecturePlayer.classList.remove("visible");
      lecturePlayer.style.display = "";
    }
    renderModeBar();
    updateLectureToggleAvailability();
  }

  async function toggleLectureMode() {
    var toggleBtn = document.getElementById("lecture-toggle");
    var lecturePlayer = document.getElementById("lecture-player");
    var mn = document.querySelector(".mn");

    if (lectureState.active) {
      deactivateLecture();
      return;
    }

    if (!state.courseId || !state.currentTopicId) return;

    // ハンズフリー音声会話とマイク/音声再生を奪い合うため、レクチャー開始時は止める。
    if (voiceState.active) stopVoiceMode();

    // 音声未生成のトピックではレクチャーを開始しない（音声生成は管理画面のみ）。
    if (!state.topicHasAudio) {
      updateLectureToggleAvailability();
      return;
    }

    // Activate lecture mode。上段の教材区画（#material-region）を隠し、代わりに
    // スライドステージ（#lecture-slide-stage）を表示する。下段（チャット/
    // ホワイトボード）と分割ハンドルは .lecture-on で畳む。
    lectureState.active = true;
    if (mn) mn.classList.add("lecture-on");
    toggleBtn.classList.add("active");
    toggleBtn.innerHTML = "&#9209; レクチャー終了";
    lecturePlayer.classList.add("visible");

    // Ensure player is actually visible (override any CSS hiding)
    if (lecturePlayer) {
      lecturePlayer.style.display = "flex";
    }

    // Load lecture sequence with failsafe
    try {
      await loadLectureSequence();

      // 【追加・重要】シーケンス読み込み完了後、自動的に再生を開始する
      if (lectureState.deck && lectureState.deck.length > 0) {
        await startPlayback();
      }
    } catch (err) {
      // 読み込み失敗はステージにエラー表示済み（renderLectureStageMessage）。
      // レクチャーUIは開いたままにして、学習者がトグルで通常表示へ戻れるようにする。
    }
  }

  // ── レクチャーデッキ（スライド一覧）の構築 ───────────────────────
  // 全セグメントのスライドをフラット化した「デッキ」を作る。segment.slides が
  // 無い/空（旧バックエンド互換）の場合は、セグメント本文をそのまま1スライドに
  // 合成する（設計書 §Phase2 の防御的実装）。
  function buildLectureDeck(segments) {
    var deck = [];
    (segments || []).forEach(function (seg, segIdx) {
      var slides = (seg.slides && seg.slides.length) ? seg.slides : [{
        slide_index: 0,
        display_text: seg.text || seg.display_text || "",
        spoken_text: seg.spoken_text || "",
        formulas: seg.formulas || [],
        figures: seg.figures || [],
        has_audio: seg.has_audio,
        duration_ms: seg.duration_ms,
      }];
      slides.forEach(function (slide) {
        deck.push({
          chunk_id: seg.chunk_id,
          segment_index: segIdx,
          slide_index: slide.slide_index || 0,
          display_text: slide.display_text || "",
          spoken_text: slide.spoken_text || "",
          formulas: slide.formulas || seg.formulas || [],
          figures: slide.figures || seg.figures || [],
          has_audio: !!slide.has_audio,
          duration_ms: slide.duration_ms || 0,
          language: seg.language || "ja",
          segment_mode: seg.segment_mode || "full",
        });
      });
    });
    return deck;
  }

  // ── レクチャースライドの一時的な通知（音声準備中・警告など）─────────
  function showLectureSlideNotice(text, opts) {
    opts = opts || {};
    var notice = document.getElementById("lecture-slide-notice");
    if (!notice) return;
    notice.textContent = text;
    notice.classList.toggle("warn", !!opts.warn);
    notice.hidden = false;
  }

  function hideLectureSlideNotice() {
    var notice = document.getElementById("lecture-slide-notice");
    if (notice) notice.hidden = true;
  }

  // スライドが1枚も無い（読み込み失敗・レクチャーコンテンツ無し）ときだけ、
  // ステージ本体にメッセージを描画する（通常のスライド表示を上書きする用途には使わない）。
  function renderLectureStageMessage(text, opts) {
    opts = opts || {};
    var inner = document.getElementById("lecture-slide-inner");
    if (!inner) return;
    inner.style.fontSize = "";
    inner.style.transform = "";
    inner.innerHTML = '<div class="lecture-slide-message' + (opts.warn ? " warn" : "") + '">' + escHtml(text) + '</div>';
    var badge = document.getElementById("lecture-slide-noaudio");
    if (badge) badge.hidden = true;
  }

  async function loadLectureSequence() {
    hideLectureSlideNotice();
    renderLectureStageMessage("レクチャーを読み込み中…");

    try {
      var res = await apiFetch(
        "/learning/lecture/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/sequence"
      );
      if (!res.ok) throw new Error("Failed to load sequence");
      var data = await res.json();
      lectureState.segments = data.segments || [];
      lectureState.deck = buildLectureDeck(lectureState.segments);
      lectureState.currentDeckIndex = 0;
      lectureState.currentSegmentIndex = lectureState.deck.length ? lectureState.deck[0].segment_index : 0;
      renderLectureStage();
      updateLectureControls();
    } catch (err) {
      renderLectureStageMessage("レクチャーシーケンスの読み込みに失敗しました。", { warn: true });
      throw err;
    }
  }

  // ── レクチャースライドステージ（表示の一本化）───────────────────
  // 現在デッキスライドの display_text を、通常閲覧ビューと同じ renderMaterialChunk
  // （Markdown + KaTeX + [[FORMULA_N]] / [[FIGURE_N]] 解決）で描画する。レンダラは
  // 複製しない（スライドを同形の疑似チャンク {text, formulas, figures} に包んで
  // 渡すだけの薄い適配）。
  function renderLectureStage() {
    var inner = document.getElementById("lecture-slide-inner");
    var badge = document.getElementById("lecture-slide-noaudio");
    if (!inner) return;
    // 新しく描画するたびに読み上げ追従状態はリセットする（DOM が作り直されるため）。
    lectureState.readingSentence = -1;
    hideLectureSlideNotice();

    if (!lectureState.deck.length) {
      renderLectureStageMessage("このトピックにはレクチャーコンテンツがありません。");
      return;
    }

    var slide = lectureState.deck[lectureState.currentDeckIndex];
    var pseudoChunk = { text: slide.display_text || "", formulas: slide.formulas || [], figures: slide.figures || [] };
    inner.style.fontSize = "";
    inner.style.transform = "";
    inner.innerHTML = '<div class="lecture-slide-text">' + renderMaterialChunk(pseudoChunk) + '</div>';
    hydrateMaterialFigures(inner);

    if (badge) badge.hidden = !!slide.has_audio;

    fitLectureSlideContent();
    updateReadingHighlight(0);
  }

  // スライド本文をステージ高さに収める: まず基準フォントサイズから段階的に縮小し
  // （100% → 90% → 80% → 70%）、それでも収まらない場合は等比縮小（CSS transform
  // scale）で全文を1画面に収める（プレゼンテーションソフトと同じ挙動。隠さない）。
  var LECTURE_SLIDE_FONT_STEPS = [16, 14.4, 12.8, 11.2]; // 100% / 90% / 80% / 70% of 16px

  function fitLectureSlideContent() {
    var stage = document.getElementById("lecture-slide-stage");
    var inner = document.getElementById("lecture-slide-inner");
    if (!stage || !inner) return;
    inner.style.transform = "";
    var available = stage.clientHeight;
    if (!available) return;

    var fit = false;
    for (var i = 0; i < LECTURE_SLIDE_FONT_STEPS.length; i++) {
      inner.style.fontSize = LECTURE_SLIDE_FONT_STEPS[i] + "px";
      if (inner.scrollHeight <= available) { fit = true; break; }
    }
    if (!fit && inner.scrollHeight > available) {
      var ratio = available / inner.scrollHeight;
      inner.style.transform = "scale(" + Math.max(ratio, 0.01) + ")";
    }
  }

  // 描画済みスライド本文内のプレーンテキストを文単位の <span> に包む。
  // 音声再生位置に合わせて「読んでいるところ」を最小限ハイライトするための下ごしらえ。
  // KaTeX（数式）配下のテキストは対象外にして数式描画を壊さない。
  function annotateSentences(segEl) {
    if (!segEl || segEl.getAttribute("data-sentences-annotated")) return;

    var textNodes = [];
    var walker = document.createTreeWalker(segEl, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (!node.nodeValue || !node.nodeValue.replace(/\s/g, "")) continue;
      var skip = false;
      var p = node.parentNode;
      while (p && p !== segEl) {
        if (p.nodeType === 1 && p.className && /katex|lecture-formula/.test(p.className)) {
          skip = true;
          break;
        }
        p = p.parentNode;
      }
      if (!skip) textNodes.push(node);
    }

    var idx = 0;
    textNodes.forEach(function (tn) {
      // 文末記号（。．！？!?）または改行までを1文とみなして分割（区切り文字は残す）。
      var pieces = tn.nodeValue.match(/[^。．！？!?\n]*[。．！？!?\n]+|[^。．！？!?\n]+$/g) || [tn.nodeValue];
      var frag = document.createDocumentFragment();
      pieces.forEach(function (piece) {
        if (piece === "") return;
        var span = document.createElement("span");
        span.className = "lecture-sentence";
        span.setAttribute("data-sentence", String(idx));
        span.textContent = piece;
        frag.appendChild(span);
        // 文末記号／改行で終わっていれば次の文へ。数式をまたぐ文は同一 idx を共有する。
        if (/[。．！？!?\n]\s*$/.test(piece)) idx++;
      });
      tn.parentNode.replaceChild(frag, tn);
    });

    segEl.setAttribute("data-sentences-annotated", "1");
  }

  // 音声再生の進捗（ratio: 0〜1）から、現在読み上げている文を推定してハイライトする。
  // 実際の word-level タイムスタンプは無いため、文の文字数比で近似する。表示と読み上げが
  // スライド単位で1:1ペアになったため、この近似でも実用精度になる（設計書 §6-3）。
  // ハイライトは表示中のスライド本文（#lecture-slide-inner）に直接適用する
  // （キャプションへの複製は廃止）。
  function updateReadingHighlight(ratio) {
    if (!lectureState.active) return;
    var inner = document.getElementById("lecture-slide-inner");
    if (!inner) return;
    var segEl = inner.querySelector(".lecture-slide-text");
    if (!segEl) return;

    annotateSentences(segEl);
    var spans = segEl.querySelectorAll(".lecture-sentence");
    if (!spans.length) return;

    // 文インデックスごとの重み（文字数）を集計する（数式をまたいだ文は合算される）。
    var weights = {};
    var maxIdx = 0;
    spans.forEach(function (s) {
      var i = parseInt(s.getAttribute("data-sentence"), 10) || 0;
      weights[i] = (weights[i] || 0) + (s.textContent ? s.textContent.length : 1);
      if (i > maxIdx) maxIdx = i;
    });

    var totalW = 0;
    for (var k = 0; k <= maxIdx; k++) totalW += weights[k] || 0;
    totalW = totalW || 1;

    var r = Math.max(0, Math.min(1, ratio || 0));
    var target = r * totalW;
    var cum = 0;
    var cur = 0;
    for (var j = 0; j <= maxIdx; j++) {
      cum += weights[j] || 0;
      if (target <= cum) { cur = j; break; }
      cur = j;
    }

    if (lectureState.readingSentence === cur) return; // 変化なし
    lectureState.readingSentence = cur;

    spans.forEach(function (s) {
      var i = parseInt(s.getAttribute("data-sentence"), 10) || 0;
      if (i === cur) s.classList.add("reading");
      else s.classList.remove("reading");
    });
  }

  // ── component/claim 引用チップ + 文脈ポップオーバー（component 根拠カード再設計）────
  // 設計書: docs/features/component_evidence_redesign.md。component/claim は本文を壊す
  // ブロックカードをやめ、内容を持たないインラインチップにする（それ自体が内容を持つ
  // equation/figure/source はブロックカードのまま維持する）。
  var MATERIAL_EVIDENCE_KIND_LABELS = {
    component: "論理要素",
    claim: "主張",
    equation: "数式",
    figure: "図",
    source: "出典",
  };

  // supports.equations[].role（component_assembly 由来）の日本語バッジ。
  var MATERIAL_EVIDENCE_EQUATION_ROLE_LABELS = {
    input: "入力",
    intermediate: "中間",
    output: "出力",
    constraint: "制約",
    definition: "定義",
    linked: "関連",
  };

  // L層 LibraryEntry.standardization_status の日本語ラベル（Phase 2「共通部品として」面）。
  var MATERIAL_LIBRARY_STATUS_LABELS = {
    standard: "標準",
    field_standard: "分野標準",
    emerging_common: "共通化進行中",
    novel: "新規",
    unknown: "未判定",
  };

  // チップ ref（"kind:id"）→ { item, resolver } のレジストリ。renderMaterialChunk は
  // 教材区画・出典ポップアップ・ボイスパネル・レクチャースライドの複数箇所から呼ばれるため、
  // クリック配線は document への委譲リスナー1本（initMaterialEvidenceChipDelegation）にし、
  // ここに登録された最新の item/resolver を都度引く。
  var materialEvidenceChipItems = {};

  function registerMaterialEvidenceChipEntry(ref, item, resolver) {
    materialEvidenceChipItems[ref] = { item: item, resolver: resolver || null };
  }

  var _materialEvidenceChipDelegationWired = false;
  function initMaterialEvidenceChipDelegation() {
    if (_materialEvidenceChipDelegationWired) return;
    _materialEvidenceChipDelegationWired = true;
    document.addEventListener("click", function (e) {
      var chip = e.target && e.target.closest ? e.target.closest(".ls-material-evidence-chip") : null;
      if (!chip) return;
      var ref = chip.getAttribute("data-evidence-ref") || "";
      var entry = materialEvidenceChipItems[ref];
      if (!entry || !entry.item) return;
      openEvidenceChipPopover(chip, entry);
    });
  }

  // ── Material chunk renderer（教材スタジオプレビュー互換・レクチャースライドでも再利用）───
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

    // [[FIGURE_N]] プレースホルダ（セグメント内 1 始まり、chunk.figures 由来。
    // [[FORMULA_N]] と同方式）。figures が無い/空の payload では何も置換しない
    // （後方互換・防御的スキップ。設計書 Phase4 §7.2）。
    var figures = chunk.figures || [];
    var figureById = {};
    figures.forEach(function (figure, idx) {
      if (!figure) return;
      var placeholderId = "FIGURE_" + (idx + 1);
      figureById[placeholderId] = figure;
      figureById["[[" + placeholderId + "]]"] = figure;
      if (figure.figure_id) figureById[String(figure.figure_id)] = figure;
    });

    // 読み取り専用 evidence DTO（backend build_topic_evidence_items）。![[component:id]]
    // / ![[claim:id]] / ![[source:id]]（および数式・図の補助）を、そのトピックで公開済み
    // の参照だけから解決する。exact キー "kind:id" 優先、kind 取り違え時は同一 ID の
    // 別 kind へフォールバック（管理画面 lsEvidenceItemByRef と同一挙動。仕様として両画面
    // 一致させる）。chunk.evidence_items が無い呼び出し（出典ポップアップ等）では空。
    var evidenceByRef = {};
    var evidenceById = {};
    (chunk.evidence_items || []).forEach(function (item) {
      if (!item || !item.kind) return;
      var norm = normalizeMaterialEvidenceId(item.id);
      evidenceByRef[item.kind + ":" + norm] = item;
      if (!Object.prototype.hasOwnProperty.call(evidenceById, norm)) evidenceById[norm] = item;
    });
    function lookupEvidenceItem(kind, normId) {
      return evidenceByRef[kind + ":" + normId] || evidenceById[normId] || null;
    }

    // component の supports.equations / supports.claims を、同じチャンクの
    // formulas / evidence_items から解決するためのリゾルバ（チップのポップオーバーが
    // 後から参照する。設計書 §5.1「接続の説明文を見せる」の実装）。
    var chunkEvidenceResolver = {
      resolveEquation: function (rawId) {
        if (!rawId) return null;
        var norm = normalizeMaterialEvidenceId(rawId);
        var formula = formulaById[String(rawId)] || formulaById[norm] || formulaById["[[" + norm + "]]"];
        if (formula) return formula;
        var ev = lookupEvidenceItem("equation", norm);
        if (ev && (ev.latex || ev.plain_text)) return ev;
        return null;
      },
      resolveClaim: function (rawId) {
        if (!rawId) return null;
        return lookupEvidenceItem("claim", normalizeMaterialEvidenceId(rawId));
      },
    };

    var embedBlocks = [];
    var mathBlocks = [];
    var figureBlocks = [];
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
    function preserveFigure(figure) {
      var idx = figureBlocks.length;
      figureBlocks.push(figure);
      return "\x00MATERIAL_FIGURE_" + idx + "\x00";
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
      if (formula) return preserveMath(formula.latex || formula.summary || id, true);
      var figure = figureById[m] || figureById[id];
      if (figure) return preserveFigure(figure);
      return m;
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
      var evidenceItem = lookupEvidenceItem(embed.kind, embedId);
      if (embed.kind === "equation") {
        // 数式は既存専用描画（LaTeX → plain_text → raw_text）を維持しつつ、
        // formulas に無い ID も evidence_items（管理画面と同じ根拠リンク由来）で裏付ける。
        var formula = formulaById[String(embed.id)] || formulaById[embedId] ||
          (evidenceItem && (evidenceItem.latex || evidenceItem.plain_text || evidenceItem.raw_text) ? evidenceItem : null);
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
      if (embed.kind === "figure") {
        // 通常 ![[figure:id]] は backend が [[FIGURE_N]] に解決済み（figures 経由で
        // 認可付き画像配信）。ここに来るのは未配信の figure_id のみ。evidence_items に
        // caption があれば、画像なしの読み取りカードを出す（新たな画像取得経路は作らない）。
        if (evidenceItem && evidenceItem.kind === "figure") {
          return '<span class="ls-material-embed ls-material-evidence-card" data-evidence-ref="figure:' + escHtml(embedId) + '">' +
            '<span class="ls-material-embed-kind">' + escHtml(MATERIAL_EVIDENCE_KIND_LABELS.figure || "図") + '</span>' +
            '<strong>' + escHtml(evidenceItem.title || ("図: " + (evidenceItem.caption || embedId))) + '</strong>' +
            '<span class="ls-material-embed-summary">' + escHtml(shortMaterialEvidenceSummary(evidenceItem.caption || evidenceItem.summary) || "この図の画像は現在配信対象ではありません。") + '</span>' +
          '</span>';
        }
        return renderMaterialMissingEmbed(embed, embedId);
      }
      // component / claim: それ自体は内容を持たない引用チップにする（設計書 §3 方向A）。
      // クリックで文脈ポップオーバー（この論文での位置づけ・下位接続・「詳しく見る」）を
      // 開く。解決できない場合のみ従来どおり未解決カードに留める。
      if (embed.kind === "component" || embed.kind === "claim") {
        if (evidenceItem) {
          return renderMaterialEvidenceChip(embed.kind, embedId, evidenceItem, chunkEvidenceResolver);
        }
        return renderMaterialMissingEmbed(embed, embedId);
      }
      // source: 原文抜粋・数式引用などそれ自体が内容を持つ埋め込みなのでブロックカードを
      // 維持する。role/confidence（パイプライン照合の来歴メタデータ）は学習UIには出さない
      // （admin 側のみで表示する。設計書 §7 受け入れ基準）。
      if (evidenceItem) {
        var cardBody = evidenceItem.latex
          ? '<span class="ls-material-embed-summary ls-material-embed-formula">' + renderMaterialKatex(evidenceItem.latex, true) + '</span>'
          : '<span class="ls-material-embed-summary">' + escHtml(shortMaterialEvidenceSummary(evidenceItem.summary) || "この教材要素に紐づく根拠です。") + '</span>';
        return '<span class="ls-material-embed ls-material-evidence-card" data-evidence-ref="' + escHtml(embed.kind + ":" + embedId) + '">' +
          '<span class="ls-material-embed-kind">' + escHtml(MATERIAL_EVIDENCE_KIND_LABELS[evidenceItem.kind] || evidenceItem.kind) + '</span>' +
          '<strong>' + escHtml(evidenceItem.title || evidenceItem.id) + '</strong>' +
          cardBody +
        '</span>';
      }
      return renderMaterialMissingEmbed(embed, embedId);
    });

    html = html.replace(/\x00MATERIAL_FIGURE_(\d+)\x00/g, function (_m, idx) {
      var figure = figureBlocks[parseInt(idx, 10)];
      return figure ? renderMaterialFigureCard(figure) : "";
    });

    return html || "";
  }

  // 本当に未解決の参照は、ID・種別を含む控えめなフォールバック表示を維持する
  // （教材画面全体を壊さない。管理画面の同種フォールバックと文言を揃える）。
  function renderMaterialMissingEmbed(embed, embedId) {
    return '<span class="ls-material-embed ls-material-evidence-card ls-material-missing" data-evidence-ref="' + escHtml(embed.kind + ":" + embedId) + '">' +
      '<span class="ls-material-embed-kind">未解決</span>' +
      '<strong>' + escHtml(embed.kind + ":" + embed.id) + '</strong>' +
      '<span class="ls-material-embed-summary">このIDに対応する教材要素を取得できませんでした。</span>' +
    '</span>';
  }

  // 根拠カードの summary を読み取りやすい長さに丸める（管理画面 lsShortSummary(260) 相当）。
  function shortMaterialEvidenceSummary(text) {
    var t = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    return t.length > 260 ? t.slice(0, 260).trim() + "..." : t;
  }

  // 教材本文中の component/claim 引用チップ本体。ブロックカードにはしない
  // （内容を持たない出典アンカーのため。設計書 §3 方向A）。
  function renderMaterialEvidenceChip(kind, embedId, evidenceItem, resolver) {
    var ref = kind + ":" + embedId;
    registerMaterialEvidenceChipEntry(ref, evidenceItem, resolver);
    var label = evidenceItem.title || evidenceItem.label || evidenceItem.id || embedId;
    return '<button type="button" class="ls-material-evidence-chip" data-evidence-ref="' + escHtml(ref) + '" data-evidence-kind="' + escHtml(kind) + '">' +
      '<span class="ls-material-evidence-chip-icon">⚓</span>' +
      '<span class="ls-material-evidence-chip-label">' + escHtml(label) + '</span>' +
    '</button>';
  }

  // ── チップ ポップオーバー（Phase 1: トピック snapshot 内で完結）────────────
  // 既存の出典ポップアップ基盤（.src-popup / _positionSourcePopup / closeSourcePopup /
  // 外側クリック・Esc）を踏襲する。
  function evidenceChipPopoverHead(kind, title) {
    var kindLabel = MATERIAL_EVIDENCE_KIND_LABELS[kind] || kind;
    return '<div class="src-popup-head">' +
      '<span class="src-popup-title">' + escHtml(kindLabel) + ' ・ ' + escHtml(title || "") + '</span>' +
      '<button class="src-popup-close" aria-label="閉じる">×</button></div>';
  }

  function openEvidenceChipPopover(anchor, entry) {
    closeSourcePopup();
    var item = entry && entry.item;
    if (!item) return;
    var kind = item.kind === "component" ? "component" : "claim";
    var title = item.title || item.label || item.id || "";

    var pop = document.createElement("div");
    pop.className = "src-popup evidence-chip-popover";
    pop.id = "src-popup";
    pop.innerHTML = evidenceChipPopoverHead(kind, title) +
      '<div class="src-popup-body evidence-chip-popover-body">' +
        (kind === "component" ? renderComponentChipPopoverBody(item, entry.resolver) : renderClaimChipPopoverBody(item)) +
      '</div>';
    document.body.appendChild(pop);
    _positionSourcePopup(pop, anchor);
    pop.querySelector(".src-popup-close").addEventListener("click", closeSourcePopup);
    document.addEventListener("mousedown", _srcOutsideClose, true);
    document.addEventListener("keydown", _srcEscClose, true);

    var moreBtn = pop.querySelector(".evidence-chip-popover-more-btn");
    if (moreBtn) {
      moreBtn.addEventListener("click", function () {
        openEvidenceChipContextPanel(pop, item, entry.resolver);
      });
    }
  }

  function renderClaimChipPopoverBody(item) {
    var summary = (item && item.summary) || "この主張の詳細はまだ登録されていません。";
    return '<div class="evidence-chip-popover-claim">' + escHtml(summary) + '</div>';
  }

  function renderComponentChipPopoverBody(item, resolver) {
    var position = (item && (item.narrative_role || item.teaching_takeaway || item.summary)) || "";
    var supports = item && item.supports;
    var html = "";
    if (position) html += '<div class="evidence-chip-popover-position">' + escHtml(position) + '</div>';
    if (!supports) {
      // rich フィールドが無い旧データ: summary のみ + 「詳しく見る」に留める（劣化許容）。
      if (!position) html += '<div class="evidence-chip-popover-position">この論文での位置づけはまだ登録されていません。</div>';
      html += evidenceChipPopoverMoreButton();
      return html;
    }
    html += renderSupportsSummaryLine(supports);
    html += renderSupportsTextSection("前提", supports.preconditions);
    html += renderSupportsTextSection("入力", supports.inputs);
    html += renderSupportsTextSection("出力", supports.outputs);
    html += renderSupportsTextSection("注意", supports.cautions);
    html += renderSupportsEquationSection(supports.equations, resolver);
    html += renderSupportsDependencySection(supports.dependencies);
    html += renderSupportsClaimSection(supports.claims, resolver);
    html += evidenceChipPopoverMoreButton();
    return html;
  }

  function evidenceChipPopoverMoreButton() {
    return '<div class="evidence-chip-popover-footer">' +
      '<button type="button" class="evidence-chip-popover-more-btn">詳しく見る</button>' +
    '</div>';
  }

  // 前提/入力/出力/注意/数式/主張のうち「非ゼロのみ」の摘要行（例: 前提 2件 · 数式 3件）。
  // これは学習内容の構成数であり評価数値ではない（設計書 §5.4）。
  function renderSupportsSummaryLine(supports) {
    var groups = [
      ["前提", supports.preconditions],
      ["入力", supports.inputs],
      ["出力", supports.outputs],
      ["注意", supports.cautions],
      ["数式", supports.equations],
      ["主張", supports.claims],
    ];
    var parts = [];
    groups.forEach(function (pair) {
      var list = pair[1];
      if (list && list.length) parts.push(pair[0] + " " + list.length + "件");
    });
    if (!parts.length) return "";
    return '<div class="evidence-chip-popover-summary-line">' + escHtml(parts.join(" · ")) + '</div>';
  }

  // preconditions/inputs/outputs/cautions は { text, claim_ids?, equation_ids? } の配列。
  // 裸のIDは出さず text のみを文リストにする（設計書 §5.4）。
  function renderSupportsTextSection(label, list) {
    var texts = (list || []).map(function (it) { return it && it.text; }).filter(Boolean);
    if (!texts.length) return "";
    return '<div class="evidence-chip-popover-section">' +
      '<div class="evidence-chip-popover-section-title">' + escHtml(label) + '</div>' +
      '<ul class="evidence-chip-popover-list">' +
      texts.map(function (t) { return '<li>' + escHtml(t) + '</li>'; }).join("") +
      '</ul></div>';
  }

  // supports.equations は Phase1 では { id, role } のみ（label 無し）なので、同じチャンクの
  // resolver で解決できたものだけ KaTeX 表示する（解決できない id はスキップ。設計書 §5.1）。
  function renderSupportsEquationSection(equations, resolver) {
    var items = (equations || []).map(function (eq) {
      if (!eq || !eq.id) return "";
      var resolved = resolver && resolver.resolveEquation ? resolver.resolveEquation(eq.id) : null;
      var latex = resolved ? (resolved.latex || resolved.summary || "") : "";
      if (!latex) return "";
      var roleLabel = MATERIAL_EVIDENCE_EQUATION_ROLE_LABELS[eq.role] || "";
      return '<div class="evidence-chip-popover-equation-item">' +
        (roleLabel ? '<span class="evidence-chip-popover-equation-role">' + escHtml(roleLabel) + '</span>' : "") +
        '<span class="evidence-chip-popover-equation-body">' + renderMaterialKatex(latex, true) + '</span>' +
      '</div>';
    }).filter(Boolean);
    if (!items.length) return "";
    return '<div class="evidence-chip-popover-section">' +
      '<div class="evidence-chip-popover-section-title">数式</div>' + items.join("") + '</div>';
  }

  function renderSupportsDependencySection(dependencies) {
    var texts = (dependencies || []).map(function (d) { return d && d.reason; }).filter(Boolean);
    if (!texts.length) return "";
    return '<div class="evidence-chip-popover-section">' +
      '<div class="evidence-chip-popover-section-title">関連</div>' +
      '<ul class="evidence-chip-popover-list">' +
      texts.map(function (t) { return '<li>' + escHtml(t) + '</li>'; }).join("") +
      '</ul></div>';
  }

  // supports.claims は claim_id の配列（Phase1）。同じチャンクの evidence registry
  // （kind=claim）で解決できたものだけ summary 文を表示する（裸のID一覧は出さない）。
  function renderSupportsClaimSection(claimIds, resolver) {
    var texts = [];
    (claimIds || []).forEach(function (cid) {
      var claimItem = resolver && resolver.resolveClaim ? resolver.resolveClaim(cid) : null;
      if (claimItem && claimItem.summary) texts.push(claimItem.summary);
    });
    if (!texts.length) return "";
    return '<div class="evidence-chip-popover-section">' +
      '<div class="evidence-chip-popover-section-title">主張</div>' +
      '<ul class="evidence-chip-popover-list">' +
      texts.map(function (t) { return '<li>' + escHtml(t) + '</li>'; }).join("") +
      '</ul></div>';
  }

  // ── 文脈パネル（Phase 2）+ グラフ（Phase 3）─────────────────────────────
  // 「詳しく見る」で GET /api/learning/courses/{courseId}/components/{componentId}/context
  // を取得し、ポップオーバー本体を「この論文では」「共通部品として」の2面に差し替える。
  function openEvidenceChipContextPanel(pop, item, resolver) {
    var body = pop.querySelector(".evidence-chip-popover-body");
    if (!body || !item) return;
    body.innerHTML = '<div class="evidence-chip-popover-loading">読み込み中…</div>';
    fetchComponentContextAndRender(pop, body, item.id, resolver);
  }

  async function fetchComponentContextAndRender(pop, body, componentId, resolver) {
    if (!componentId || !state.courseId) {
      body.textContent = "詳細情報を取得できませんでした。";
      return;
    }
    try {
      var res = await apiFetch("/learning/courses/" + state.courseId +
        "/components/" + encodeURIComponent(componentId) + "/context");
      if (!res.ok) { body.textContent = "詳細情報を取得できませんでした。"; return; }
      var data = await res.json();
      renderComponentContextPanel(pop, body, data, resolver);
    } catch (_) {
      body.textContent = "詳細情報を取得できませんでした。";
    }
  }

  function renderComponentContextPanel(pop, body, data, resolver) {
    var instance = (data && data.instance) || {};
    var sharedPart = data && data.shared_part;
    var graph = data && data.graph;

    var headTitle = pop.querySelector(".src-popup-title");
    if (headTitle && instance.component && instance.component.label) {
      headTitle.textContent = (MATERIAL_EVIDENCE_KIND_LABELS.component || "論理要素") + " ・ " + instance.component.label;
    }

    var html = '<div class="evidence-chip-context-tabs" role="tablist">' +
      '<button type="button" class="evidence-chip-context-tab active" data-tab="instance">この論文では</button>' +
      (sharedPart ? '<button type="button" class="evidence-chip-context-tab" data-tab="shared">共通部品として</button>' : "") +
    '</div>' +
    '<div class="evidence-chip-context-pane" data-pane="instance">' + renderContextInstancePane(instance, resolver) + '</div>' +
    (sharedPart ? '<div class="evidence-chip-context-pane" data-pane="shared" hidden>' + renderContextSharedPane(sharedPart) + '</div>' : "") +
    (graph ? '<div class="evidence-chip-context-graph-toggle">' +
        '<button type="button" class="evidence-chip-context-graph-btn">グラフで見る</button>' +
      '</div><div class="evidence-chip-context-graph" hidden></div>' : "");

    body.innerHTML = html;

    var tabs = body.querySelectorAll(".evidence-chip-context-tab");
    tabs.forEach(function (tabBtn) {
      tabBtn.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tabBtn.classList.add("active");
        body.querySelectorAll(".evidence-chip-context-pane").forEach(function (pane) {
          pane.hidden = pane.getAttribute("data-pane") !== tabBtn.getAttribute("data-tab");
        });
      });
    });

    if (graph) {
      var graphBtn = body.querySelector(".evidence-chip-context-graph-btn");
      var graphContainer = body.querySelector(".evidence-chip-context-graph");
      if (graphBtn && graphContainer) {
        graphBtn.addEventListener("click", function () {
          graphContainer.hidden = !graphContainer.hidden;
          if (!graphContainer.hidden && !graphContainer._rendered) {
            graphContainer.appendChild(buildEvidenceContextGraphSvg(graph, function (node) {
              navigateEvidenceContextGraph(pop, body, node);
            }));
            graphContainer._rendered = true;
          }
        });
      }
    }
  }

  // 「グラフで見る」内のノードクリック（旅）。theory_component かつ navigable な
  // ノードのみ辿れる。パンくずは持たず、パネルをその場で置き換える。
  function navigateEvidenceContextGraph(pop, body, node) {
    if (!node || node.element_type !== "theory_component" || !node.navigable || !node.id) return;
    body.innerHTML = '<div class="evidence-chip-popover-loading">読み込み中…</div>';
    fetchComponentContextAndRender(pop, body, node.id, null);
  }

  function renderContextInstancePane(instance, resolver) {
    var inPaper = instance.in_paper || {};
    var supports = instance.supports || {};
    var component = instance.component || {};
    var html = "";
    var position = inPaper.narrative_role || component.teaching_takeaway || component.summary || "";
    if (position) html += '<div class="evidence-chip-popover-position">' + escHtml(position) + '</div>';
    if (inPaper.graph_summary_excerpt) {
      html += '<div class="evidence-chip-context-graph-summary">' + escHtml(inPaper.graph_summary_excerpt) + '</div>';
    }
    html += renderSupportsSummaryLine(supports);
    html += renderSupportsTextSection("前提", supports.preconditions);
    html += renderSupportsTextSection("入力", supports.inputs);
    html += renderSupportsTextSection("出力", supports.outputs);
    html += renderSupportsTextSection("注意", supports.cautions);
    html += renderContextEquationSection(supports.equations, resolver);
    html += renderContextDependencySection(supports.dependencies);
    html += renderContextClaimSection(supports.claims);
    if (inPaper.document && inPaper.document.title) {
      html += '<div class="evidence-chip-context-document">出典論文: ' + escHtml(inPaper.document.title) + '</div>';
    }
    if (instance.explanation && (instance.explanation.body || instance.explanation.title)) {
      html += '<div class="evidence-chip-context-explanation">' +
        '<div class="evidence-chip-popover-section-title">教員の説明</div>' +
        (instance.explanation.title ? '<div class="evidence-chip-context-explanation-title">' + escHtml(instance.explanation.title) + '</div>' : "") +
        (instance.explanation.body ? '<div>' + escHtml(instance.explanation.body) + '</div>' : "") +
        (instance.explanation.endorsement_label ? '<div class="evidence-chip-context-explanation-endorsement">' + escHtml(instance.explanation.endorsement_label) + '</div>' : "") +
      '</div>';
    }
    return html || '<div class="evidence-chip-popover-position">この論文での位置づけはまだ登録されていません。</div>';
  }

  // Phase2 API は equations に label を持つため、latex が解決できなくても label + role は
  // 常に表示する（Phase1のポップオーバーと違いスキップしない。設計書 §5.4）。
  function renderContextEquationSection(equations, resolver) {
    var items = (equations || []).map(function (eq) {
      if (!eq) return "";
      var roleLabel = MATERIAL_EVIDENCE_EQUATION_ROLE_LABELS[eq.role] || "";
      var resolved = resolver && resolver.resolveEquation ? resolver.resolveEquation(eq.id) : null;
      var latex = resolved ? (resolved.latex || resolved.summary || "") : "";
      return '<div class="evidence-chip-popover-equation-item">' +
        (roleLabel ? '<span class="evidence-chip-popover-equation-role">' + escHtml(roleLabel) + '</span>' : "") +
        '<span class="evidence-chip-popover-equation-body">' +
        (latex ? renderMaterialKatex(latex, true) : escHtml(eq.label || eq.id || "")) +
        '</span></div>';
    }).join("");
    if (!items) return "";
    return '<div class="evidence-chip-popover-section">' +
      '<div class="evidence-chip-popover-section-title">数式</div>' + items + '</div>';
  }

  function renderContextDependencySection(dependencies) {
    var texts = (dependencies || []).map(function (d) {
      if (!d) return "";
      var t = d.reason || "";
      if (d.target_label) t = t ? (d.target_label + " — " + t) : d.target_label;
      return t;
    }).filter(Boolean);
    if (!texts.length) return "";
    return '<div class="evidence-chip-popover-section">' +
      '<div class="evidence-chip-popover-section-title">関連</div>' +
      '<ul class="evidence-chip-popover-list">' +
      texts.map(function (t) { return '<li>' + escHtml(t) + '</li>'; }).join("") +
      '</ul></div>';
  }

  function renderContextClaimSection(claims) {
    var texts = (claims || []).map(function (c) { return c && c.excerpt; }).filter(Boolean);
    if (!texts.length) return "";
    return '<div class="evidence-chip-popover-section">' +
      '<div class="evidence-chip-popover-section-title">主張</div>' +
      '<ul class="evidence-chip-popover-list">' +
      texts.map(function (t) { return '<li>' + escHtml(t) + '</li>'; }).join("") +
      '</ul></div>';
  }

  function renderContextSharedPane(sharedPart) {
    var entry = sharedPart.entry || {};
    var html = "";
    if (entry.name) html += '<div class="evidence-chip-context-shared-name">' + escHtml(entry.name) + '</div>';
    if (entry.aliases && entry.aliases.length) {
      html += '<div class="evidence-chip-context-shared-aliases">別名: ' + escHtml(entry.aliases.join(" ・ ")) + '</div>';
    }
    if (entry.summary) html += '<div class="evidence-chip-popover-position">' + escHtml(entry.summary) + '</div>';
    if (entry.standardization_status) {
      var statusLabel = MATERIAL_LIBRARY_STATUS_LABELS[entry.standardization_status] || entry.standardization_status;
      html += '<div class="evidence-chip-context-shared-status">標準化状況: ' + escHtml(statusLabel) + '</div>';
    }
    if (entry.other_documents_count) {
      html += '<div class="evidence-chip-context-shared-count">他 ' + escHtml(String(entry.other_documents_count)) + ' 論文でも使用</div>';
    }
    return html || '<div class="evidence-chip-popover-position">共通部品としての情報はまだ登録されていません。</div>';
  }

  // ── 1-hop 近傍グラフ（Phase 3・「グラフで見る」・「旅」の実装）───────────────
  var EVIDENCE_GRAPH_SVG_NS = "http://www.w3.org/2000/svg";

  function evidenceGraphSvgEl(tag, attrs) {
    var node = document.createElementNS(EVIDENCE_GRAPH_SVG_NS, tag);
    if (attrs) {
      for (var k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k)) node.setAttribute(k, attrs[k]);
      }
    }
    return node;
  }

  function truncateGraphLabel(label, max) {
    var s = String(label == null ? "" : label);
    return s.length > max ? s.slice(0, max - 1) + "…" : s;
  }

  // 上段= upper、中央= focus、下段= lower の小型 SVG。エッジは実線のみ（candidate は
  // API 側で除外済みのため点線は使わない）。navigable な theory_component ノードのみ
  // クリック可（旅の起点。設計書 §5.4・Phase3）。
  function buildEvidenceContextGraphSvg(graph, onNodeClick) {
    var width = 320, height = 220, nodeW = 100, nodeH = 30;
    var svg = evidenceGraphSvgEl("svg", {
      viewBox: "0 0 " + width + " " + height,
      width: "100%",
      height: String(height),
      class: "evidence-chip-context-graph-svg",
    });

    var focus = graph.focus || {};
    var upper = graph.upper || [];
    var lower = graph.lower || [];
    var focusX = width / 2, focusY = height / 2;

    function layoutRow(nodes, y) {
      var n = nodes.length;
      if (!n) return [];
      var gap = width / (n + 1);
      return nodes.map(function (node, i) {
        return { node: node, x: gap * (i + 1), y: y };
      });
    }

    var upperPositions = layoutRow(upper, 22);
    var lowerPositions = layoutRow(lower, height - 22);

    function drawEdge(x1, y1, x2, y2, relationLabel) {
      svg.appendChild(evidenceGraphSvgEl("line", {
        x1: x1, y1: y1, x2: x2, y2: y2,
        class: "evidence-chip-context-graph-edge",
      }));
      if (relationLabel) {
        var text = evidenceGraphSvgEl("text", {
          x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 4,
          class: "evidence-chip-context-graph-edge-label",
          "text-anchor": "middle",
        });
        text.textContent = truncateGraphLabel(relationLabel, 12);
        svg.appendChild(text);
      }
    }

    function drawNode(x, y, node, isFocus) {
      var navigable = !isFocus && node && node.navigable && node.element_type === "theory_component";
      var group = evidenceGraphSvgEl("g", {
        class: "evidence-chip-context-graph-node" + (isFocus ? " is-focus" : "") + (navigable ? " is-navigable" : ""),
        transform: "translate(" + (x - nodeW / 2) + "," + (y - nodeH / 2) + ")",
      });
      group.appendChild(evidenceGraphSvgEl("rect", {
        width: nodeW, height: nodeH, rx: 8, ry: 8,
        class: "evidence-chip-context-graph-node-rect",
      }));
      var text = evidenceGraphSvgEl("text", {
        x: nodeW / 2, y: nodeH / 2 + 4,
        class: "evidence-chip-context-graph-node-label",
        "text-anchor": "middle",
      });
      text.textContent = truncateGraphLabel((node && (node.label || node.id)) || "", 16);
      group.appendChild(text);
      if (navigable && typeof onNodeClick === "function") {
        group.style.cursor = "pointer";
        group.addEventListener("click", function () { onNodeClick(node); });
      }
      svg.appendChild(group);
    }

    upperPositions.forEach(function (p) { drawEdge(p.x, p.y + nodeH / 2, focusX, focusY - nodeH / 2, p.node.relation_label); });
    lowerPositions.forEach(function (p) { drawEdge(focusX, focusY + nodeH / 2, p.x, p.y - nodeH / 2, p.node.relation_label); });
    upperPositions.forEach(function (p) { drawNode(p.x, p.y, p.node, false); });
    drawNode(focusX, focusY, focus, true);
    lowerPositions.forEach(function (p) { drawNode(p.x, p.y, p.node, false); });

    return svg;
  }

  // [[FIGURE_N]] の解決先マークアップ。画像本体は同期関数の中で fetch できないため、
  // data-figure-fetch-url に URL を仕込んだ <img> を描き、hydrateMaterialFigures()
  // が DOM 挿入後に Authorization ヘッダ付きで取得する（学習者向け画像 endpoint も
  // 認証必須のため <img src> を直接指定できない。admin.js の図サムネイル読込と同方式）。
  function renderMaterialFigureCard(figure) {
    var caption = figure.caption || "";
    var explanation = figure.explanation || "";
    var imgUrl = figure.image_url || "";
    if (!imgUrl) {
      return '<figure class="material-figure">' +
        '<span class="material-figure-placeholder material-figure-missing">この図の画像を取得できませんでした。</span>' +
        (caption ? '<figcaption class="material-figure-caption">' + escHtml(caption) + '</figcaption>' : "") +
      '</figure>';
    }
    return '<figure class="material-figure">' +
      '<span class="material-figure-frame">' +
        '<img class="material-figure-img" data-figure-fetch-url="' + escHtml(imgUrl) + '" style="display:none" alt="' + escHtml(caption || "図") + '">' +
        '<span class="material-figure-placeholder">画像を読み込み中…</span>' +
      '</span>' +
      (caption ? '<figcaption class="material-figure-caption">' + escHtml(caption) + '</figcaption>' : "") +
      (explanation ? '<div class="material-figure-explanation">' + escHtml(explanation) + '</div>' : "") +
    '</figure>';
  }

  // renderMaterialChunk が返した HTML を DOM へ挿入した後に呼ぶ。container 内の
  // 未取得 <img data-figure-fetch-url> を Authorization ヘッダ付きで取得して差し替える。
  // container は教材区画本体 / 出典ポップアップ / ボイスパネル / レクチャースライドの
  // いずれか（呼び出し側で再構築されるたびに呼ばれるため、前回分の blob URL は
  // container 自身に積んで都度 revoke する）。figures が無い payload では
  // querySelectorAll が空になり何もしない（防御的スキップ）。
  function hydrateMaterialFigures(container) {
    if (!container || !container.querySelectorAll) return;
    if (container._materialFigureObjectUrls) {
      container._materialFigureObjectUrls.forEach(function (u) {
        try { URL.revokeObjectURL(u); } catch (e) { /* noop */ }
      });
    }
    container._materialFigureObjectUrls = [];
    container.querySelectorAll("img.material-figure-img[data-figure-fetch-url]").forEach(function (img) {
      var url = img.getAttribute("data-figure-fetch-url");
      img.removeAttribute("data-figure-fetch-url");
      if (!url) { markMaterialFigureFailed(img); return; }
      if (url.indexOf("/api/") === 0) url = url.substring(4);
      apiFetchRaw(url)
        .then(function (res) {
          if (!res.ok) throw new Error("figure image fetch failed");
          return res.blob();
        })
        .then(function (blob) {
          var objectUrl = URL.createObjectURL(blob);
          container._materialFigureObjectUrls.push(objectUrl);
          img.src = objectUrl;
          img.style.display = "";
          var placeholder = img.parentNode && img.parentNode.querySelector(".material-figure-placeholder");
          if (placeholder) placeholder.remove();
          // レクチャースライドは1画面に収める必要があるため、画像サイズ確定後に
          // 再フィットする（非レクチャー表示では #lecture-slide-stage が非表示のため no-op）。
          img.addEventListener("load", function () { fitLectureSlideContent(); }, { once: true });
        })
        .catch(function () {
          markMaterialFigureFailed(img);
        });
    });
  }

  function markMaterialFigureFailed(img) {
    img.style.display = "none";
    var placeholder = img.parentNode && img.parentNode.querySelector(".material-figure-placeholder");
    if (placeholder) {
      placeholder.textContent = "画像を表示できません";
      placeholder.classList.add("material-figure-missing");
    }
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

  // 教材埋め込み ![[kind:id]] の id 正規化。管理画面 lsNormalizeEvidenceId
  // （admin-lecture-studio.js）と backend normalize_evidence_id
  // （core/course_content_builder.py）と**同一仕様**にする（両画面で同じ ID が同じ
  // 解決キーになることを保証。test_topic_material_evidence_items が差分を固定）。
  // 空白除去 → 先頭 [[ / 末尾 ]] を最大2回剥がす → 旧二重 "eq_" プレフィックス
  // ("eq_eq_F2" → "eq_F2") を畳み込む。
  function normalizeMaterialEvidenceId(value) {
    var s = String(value || "").trim();
    s = s.replace(/^\[\[/, "").replace(/\]\]$/, "");
    s = s.replace(/^\[\[/, "").replace(/\]\]$/, "");
    s = s.trim();
    s = s.replace(/^(?:eq_){2,}/i, "eq_");
    return s;
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

    var total = lectureState.deck.length;
    var current = lectureState.currentDeckIndex + 1;

    if (label) label.textContent = "スライド " + current + " / " + total;
    if (progressFill) progressFill.style.width = (total > 0 ? (current / total * 100) : 0) + "%";

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
    if (!lectureState.deck.length) return;
    // 音声フェッチ中の連打による多重再生を防ぐ。
    if (lectureState.loadingAudio) return;

    // 同一スライドの一時停止からの再開: 音声を取得し直さず途中から続きを再生する。
    if (lectureState.audio && !lectureState.audio.ended) {
      lectureState.playing = true;
      updateLectureControls();
      var resumePromise = lectureState.audio.play();
      if (resumePromise !== undefined) {
        resumePromise.catch(function () {
          lectureState.playing = false;
          updateLectureControls();
        });
      }
      return;
    }

    lectureState.loadingAudio = true;
    var slide = lectureState.deck[lectureState.currentDeckIndex];

    try {
      // has_audio な場合のみ TTS をフェッチする（無音スライドは無駄なリクエストを出さず
      // 即座にタイマー送りへフォールバックする）。
      var ttsData = null;
      if (slide.has_audio) {
        showLectureSlideNotice("音声を準備中…");
        try {
          var res = await apiFetch(
            "/learning/lecture/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/tts",
            {
              method: "POST",
              body: JSON.stringify({ chunk_id: slide.chunk_id, slide_index: slide.slide_index, voice: "alloy" }),
            }
          );
          if (res.ok) {
            ttsData = await res.json();
          }
        } catch (_ttsErr) {
          // TTS unavailable — will fall back to simulation
        }
      }

      lectureState.playing = true;
      updateLectureControls();

      if (ttsData) {
        hideLectureSlideNotice();
        var audioSrc = "data:" + (ttsData.content_type || "audio/mp3") + ";base64," + ttsData.audio_base64;

        if (lectureState.audio) {
          lectureState.audio.pause();
          lectureState.audio = null;
        }

        lectureState.audio = new Audio(audioSrc);

        lectureState.audio.addEventListener("ended", function () {
          stopHighlighting();
          // 再開パスが終了済み音声を拾わないよう、次のスライドへ進む前に破棄する。
          lectureState.audio = null;
          autoAdvance();
        });

        lectureState.audio.addEventListener("timeupdate", function () {
          updateTimeDisplay();
          if (lectureState.audio && lectureState.audio.duration) {
            updateReadingHighlight(lectureState.audio.currentTime / lectureState.audio.duration);
          }
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
            showLectureSlideNotice("ブラウザの制限により自動再生がブロックされました。下の「▶（再生）」ボタンを押して開始してください。", { warn: true });
          });
        }
      } else {
        // 音声キャッシュ無し（has_audio=false、または取得失敗）: タイマー送りで進行する。
        hideLectureSlideNotice();
        simulatePlayback(slide);
      }
    } catch (err) {
      lectureState.playing = false;
      updateLectureControls();
      showLectureSlideNotice("音声の再生に失敗しました。", { warn: true });
    } finally {
      lectureState.loadingAudio = false;
    }
  }

  // has_audio=false のスライドをタイマー送りで進行する（設計書 §6-4）。
  // 日本語: 300字/分（=200ms/字）、英語: 150 wpm（=400ms/word）、最低3秒。
  function simulatePlayback(slide) {
    var text = slide.spoken_text || slide.display_text || "";
    var durationMs;
    if (slide.language === "en") {
      var words = text.trim() ? text.trim().split(/\s+/).length : 0;
      durationMs = Math.max(3000, words * 400);
    } else {
      durationMs = Math.max(3000, text.length * 200);
    }
    var startTime = Date.now();

    lectureState.highlightTimer = setInterval(function () {
      var elapsed = Date.now() - startTime;
      var timeEl = document.getElementById("lecture-time");
      if (timeEl) {
        var sec = Math.floor(elapsed / 1000);
        var min = Math.floor(sec / 60);
        timeEl.textContent = min + ":" + (sec % 60 < 10 ? "0" : "") + (sec % 60);
      }

      // 音声が無い場合も推定所要時間に対する経過比で読み上げ位置を追従表示する。
      updateReadingHighlight(elapsed / durationMs);

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

  // 次のスライドへ自動送りする。チャンク境界（segment_index の変化）もデッキが
  // フラット化済みのため自然に連続する。最終スライドで onLectureComplete へ合流する。
  function autoAdvance() {
    if (lectureState.currentDeckIndex < lectureState.deck.length - 1) {
      lectureState.currentDeckIndex++;
      lectureState.currentSegmentIndex = lectureState.deck[lectureState.currentDeckIndex].segment_index;
      renderLectureStage();
      updateLectureControls();
      if (lectureState.playing) startPlayback();
    } else {
      lectureState.playing = false;
      updateLectureControls();
      onLectureComplete();
    }
  }

  // レクチャー最終スライド到達時、テキストパスと同じ確認フローへ合流する。
  // 下段（チャット）は畳んでいるので、完了バナーはスライドステージとプレイヤーの間に出す。
  function onLectureComplete() {
    var mn = document.querySelector(".mn");
    var player = document.getElementById("lecture-player");
    if (!mn || !player || document.getElementById("lecture-complete-banner")) return;
    var next = getNextTopic();
    // G1-4: 最終トピックでも「確認して完了」で同じ確認問題フローに合流させ、
    // 合格後の完了カードへ繋ぐ（レクチャーを黙って終わらせない）。
    var label = next ? "確認問題に進む" : "確認して完了";
    var banner = document.createElement("div");
    banner.className = "lecture-complete";
    banner.id = "lecture-complete-banner";
    banner.innerHTML = '<span>このトピックのレクチャーは以上です。</span>' +
      '<button class="suggest-btn" id="lecture-complete-next">' + label + '</button>';
    mn.insertBefore(banner, player);
    var btn = document.getElementById("lecture-complete-next");
    if (btn) btn.addEventListener("click", openCheckModal);
    // 分野の地図 (Issue F-2 導線2): 講義の章末 (章の最後のトピックのレクチャーを
    // 聴き終えたとき) にサマリーの末尾で「地図で現在地を見る」を提示する。
    // 章の途中のトピックでは出さない (完了時の導線1がその役を持つ)。
    // gap5(将来対応): 本アプリに明示的な「章末サマリー画面」が無いため、導線2は章末を
    // 跨ぐ完了とこのレクチャー完了バナーで代替している。章末サマリー UI ができたら
    // その画面へ移設するのが本来形 (issue の付随項目5)。
    var lectureTopic = getCurrentTopic();
    var lectureNext = getNextTopic();
    var chapterDone = lectureTopic &&
      (!lectureNext || lectureNext.chapter_index !== lectureTopic.chapter_index);
    if (window.AtlasCues && chapterDone) {
      window.AtlasCues.showCue("chapter_end", {
        container: banner,
        text: "この章のレクチャーはここまでです。",
        focusTopicId: lectureTopic.id,
        focusLabel: lectureTopic.title,
      });
    }
  }

  // ◀/▶ = デッキ上のスライド移動（チャンク境界をまたいで連続。頭出し・停止して切替）。
  function prevSegment() {
    if (lectureState.currentDeckIndex > 0) {
      stopPlayback();
      lectureState.currentDeckIndex--;
      lectureState.currentSegmentIndex = lectureState.deck[lectureState.currentDeckIndex].segment_index;
      renderLectureStage();
      updateLectureControls();
    }
  }

  function nextSegment() {
    if (lectureState.currentDeckIndex < lectureState.deck.length - 1) {
      stopPlayback();
      lectureState.currentDeckIndex++;
      lectureState.currentSegmentIndex = lectureState.deck[lectureState.currentDeckIndex].segment_index;
      renderLectureStage();
      updateLectureControls();
    }
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

    var slide = lectureState.deck[lectureState.currentDeckIndex];
    var messages = document.getElementById("lecture-chat-messages");

    try {
      var res = await apiFetch(
        "/learning/lecture/courses/" + state.courseId + "/topics/" + state.currentTopicId + "/interrupt",
        {
          method: "POST",
          body: JSON.stringify({
            message: message,
            current_chunk_id: slide ? slide.chunk_id : "",
            current_slide_index: slide ? slide.slide_index : 0,
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

  // ── 分割ハンドル ─────────────────────────────────────────────────
  // 教材区画（上段）と下段（読み上げ行＋ホワイトボード）の比率をドラッグで調整する。
  // 高さは localStorage に保存し、次回以降も同じ比率で表示する。
  var SPLIT_STORAGE_KEY = "eg_material_split_px";

  function initSplitHandle() {
    var handle = document.getElementById("split-handle");
    var region = document.getElementById("material-region");
    if (!handle || !region) return;
    var mn = region.parentElement;

    function applyHeight(px) {
      var max = Math.max(120, (mn.clientHeight || window.innerHeight) * 0.75);
      px = Math.max(80, Math.min(px, max));
      region.style.height = px + "px";
      region.style.maxHeight = "none";
      region.style.flex = "0 0 auto";
      return px;
    }

    // 保存済みの高さを復元（未保存なら CSS 既定の max-height 42% のまま）。
    var saved = parseInt(localStorage.getItem(SPLIT_STORAGE_KEY) || "", 10);
    if (saved > 0) applyHeight(saved);

    var dragging = false, startY = 0, startH = 0;
    handle.addEventListener("pointerdown", function (e) {
      dragging = true;
      startY = e.clientY;
      startH = region.getBoundingClientRect().height;
      handle.classList.add("dragging");
      if (handle.setPointerCapture) handle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    handle.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      applyHeight(startH + (e.clientY - startY));
    });
    function endDrag() {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove("dragging");
      var h = Math.round(region.getBoundingClientRect().height);
      try { localStorage.setItem(SPLIT_STORAGE_KEY, String(h)); } catch (_) { /* noop */ }
    }
    handle.addEventListener("pointerup", endDrag);
    handle.addEventListener("pointercancel", endDrag);
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
    initSelectionAnchor();
    initLogout();
    initGroups();
    initLectureMode();
    initSplitHandle();
    // component/claim 引用チップのクリック配線（教材区画・出典ポップアップ・ボイスパネル・
    // レクチャースライドの複数箇所から renderMaterialChunk が呼ばれるため、document への
    // 委譲リスナー1本にまとめる。1度だけ登録）。
    initMaterialEvidenceChipDelegation();
    // Phase P-1: 「わたしの地図」から問いの軌跡へ戻る導線 (openTrajectory) を一度だけ登録する。
    if (window.PersonalMap) window.PersonalMap.init({ openTrajectory: openTrajectory });
    // Phase P-3: 最上位「わたしの地図」パネル。openTrajectory は将来のノード詳細導線用（任意）。
    if (window.PersonalMapHome) window.PersonalMapHome.init({ openTrajectory: openTrajectory });
    var myMapBtn = document.getElementById("my-map-btn");
    if (myMapBtn) {
      myMapBtn.addEventListener("click", function () {
        if (window.PersonalMapHome) window.PersonalMapHome.open();
      });
    }
    await initCourseSelector();
    loadInvitationBadge();
    // 分野の地図 (Issue F-2 導線4): 初回ログイン時のみ L1 を俯瞰位置で自動表示する。
    // フラグはサーバに永続化され、再ログイン・別端末でも繰り返さない。
    if (window.AtlasCues) window.AtlasCues.maybeAutoOpenFirstLogin();
  }

  // Boot
  document.addEventListener("DOMContentLoaded", function () {
    renderAuth();
    if (state.token) initApp();
  });
})();
