/**
 * グラフ対話レビュー（Graph Dialogue Review）
 * 正本: docs/features/graph_dialogue_review_design.md
 *
 * 教材管理「アップロード済み教材」の各行から起動するフルスクリーンモーダル。
 * 理論操作グラフを見取り図に、①構造を見る ②AI と確かめる ③その場で確定する
 * （component 承認/却下・backing claim 承認）を1画面で行う。
 *
 * - ES5 / IIFE。window.GraphReview を公開。admin.js の initApp() から init({...}) で DI。
 * - グラフ描画は window.LectureStudio.graphView（原稿スタジオの lsGraph* 純関数群の
 *   公開面）へ委譲する（GR8: 描画ロジックを二重実装しない）。
 * - 承認・却下は教員の明示ボタンのみ（GR1）。AI 応答から承認 API を呼ぶ経路は無い。
 * - ノード対話 = 既存 W層 sessions API / グラフ全体対話 = graph-sessions API。
 *   confidence の生値は扱わない（GR3 — API 側が confidence_label のみ返す）。
 * - ハンズフリー音声対話は window.AdminVoiceChat（DOM 非依存エンジン）へ委譲し、
 *   ここは「文字起こし → sendChatText → 読み上げ」の配線だけを持つ。音声から
 *   承認・却下 API を呼ぶ経路は作らない（GR1: 確定は教員の明示ボタンのみ）。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null, escHtml: null, getToken: null };

  var state = {
    documentId: null,
    title: "",
    graph: null,
    loading: false,
    layer: "main",
    unreviewedOnly: false,
    selectedNodeId: "",
    network: null,
    chatMode: "graph", // "graph" | "node"
    nodeSessions: {},  // component_id -> session dict
    graphSession: null,
    chatMessages: [],  // 表示中モードのメッセージ [{role, content}]
    chatAnnotations: [], // 直近応答の候補注釈
    chatBusy: false,
    detailNotice: null, // 再描画をまたいで一度だけ再表示する操作結果 {message, kind}
    view: "graph",      // "graph" | "paper"（左ペインの表示。論文層 = graph_paper_layer_design.md）
    paperLayer: null,   // 論文層 DTO（読み時射影。取得前は null）
    paperLayerError: null, // 論文層の取得失敗の事実文（グラフ・レビュー操作は止めない）
    preserveViewOnce: false, // 次の再描画でズーム・パンを維持する（fit しない）
    voiceLoop: null,    // AdminVoiceChat のコントローラ（起動中のみ）
    voicePlayer: null,  // 読み上げ中の Audio（停止時に止める）
  };

  // component / claim の review_status → 表示ラベル（graphView と同じ語彙世界。
  // graphView.sourceBackingLabel は backing 用なので、承認状態はここで持つ）。
  var REVIEW_STATUS_LABELS = {
    teacher_approved: "承認済み",
    teacher_reviewed: "承認済み",
    endorsed: "承認済み",
    teacher_review_required: "未レビュー",
    review_required: "要確認",
    needs_revision: "要修正",
    rejected: "却下",
    source_backed: "原文裏付けあり（未承認）",
  };
  var APPROVED_STATUSES = { teacher_approved: true, teacher_reviewed: true, endorsed: true };

  // 解析結果にしか無い根拠 claim の出所（reference_index の origin）→ 表示ラベル。
  // claim_object（通常の claim 生成物）は出所を書かない（情報量ゼロのチップを増やさない）。
  var CLAIM_ORIGIN_LABELS = {
    equation_synthesis: "式から合成",
    atomic_rewrite: "主張の細分化",
  };

  // 論文層（graph_paper_layer_design.md §3）の語彙 → 表示ラベル。
  // いずれも論文層 DTO だけで閉じる小さな表で、graphView の語彙とは重ならない。
  var EQUATION_ROLE_LABELS = {
    input: "入力",
    intermediate: "中間",
    output: "出力",
    definition: "定義",
    constraint: "制約",
    linked: "関連",
  };
  var SYMBOL_ROLE_LABELS = { eliminated: "消去", retained: "保持" };
  var EXPLANATION_STATUS_LABELS = { approved: "承認済み", candidate: "候補" };

  // 論文層の事実文（PL8: 欠落は例外にせず1行の事実として出す）。
  var PAPER_LOADING_TEXT = "論文層を読み込んでいます…";
  var PAPER_ERROR_TEXT = "論文層を取得できませんでした。";
  var PAPER_UNAVAILABLE_TEXT = "この教材では、論文の順で表示できる解析結果がありません。";
  var PAPER_SECTION_EMPTY_TEXT = "このフレームには掛かっていません";
  var PAPER_UNLOCATED_TEXT = "論文上の位置を特定できませんでした（式・根拠・claim へのリンクがありません）";
  var PAPER_NODE_MISSING_TEXT = "このノードに対応する論文側の情報はありません。";

  function esc(text) {
    return deps.escHtml ? deps.escHtml(text == null ? "" : String(text)) : String(text == null ? "" : text);
  }

  function gv() {
    return (window.LectureStudio && window.LectureStudio.graphView) || null;
  }

  // claim 本文・ノードの説明は「地の文に TeX が混ざる」文字列。数式は数式として出す
  // （生の $P_{\rm L}(k)$ を教員に読ませない）。描画の実装は原稿スタジオの正本
  // graphView.inlineMathHtml に委譲し、未ロード時は素のエスケープへ縮退する（GR8）。
  function richText(text) {
    var view = gv();
    if (view && view.inlineMathHtml) return view.inlineMathHtml(text);
    return esc(text);
  }

  function reviewStatus(node) {
    return String((node && node.review_status) || "teacher_review_required");
  }

  function reviewStatusLabel(status) {
    return REVIEW_STATUS_LABELS[String(status || "")] || String(status || "") || "未レビュー";
  }

  function isApproved(status) { return !!APPROVED_STATUSES[String(status || "")]; }
  function isRejected(status) { return String(status || "") === "rejected"; }
  function isUnreviewedNode(node) {
    var status = reviewStatus(node);
    return !isApproved(status) && !isRejected(status);
  }

  function graphNodes() {
    return (state.graph && state.graph.nodes) || [];
  }

  function nodeById(nodeId) {
    var view = gv();
    var nodes = graphNodes();
    for (var i = 0; i < nodes.length; i++) {
      if (view.nodeId(nodes[i]) === nodeId) return nodes[i];
    }
    return null;
  }

  function unreviewedNodesInView() {
    // グラフ未ロード・読み込み失敗中は空扱い（filterByLayer に null を渡さない）。
    if (!state.graph) return [];
    var view = gv().filterByLayer(state.graph, state.layer);
    return (view.nodes || []).filter(isUnreviewedNode);
  }

  // theory_components の行 ID（DB UUID）かどうか。集約 main ノード（theory_op_0001 等の
  // graph-native ID）は行を持たないため、承認・却下の対象にしない。
  function isDbUuid(value) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(value || ""));
  }

  // 「深く検討」／ノード対話が指す実体要素の ID。
  //
  // 理論操作グラフのノードは3種類の ID を持ちうる:
  //   - DB UUID（component ノード。debug 層や旧グラフ）→ そのまま theory_components の行。
  //   - graph-native ID（theory_op_0001 = 集約 main ノード / eq_op_0001 = 式の詳細層）
  //     → theory_components の行を持たないため、それ自体では要素として開けない。
  //     代わりに集約元の代表要素（representative_component_id。component_assembly の
  //     agent 側 ID）を使う。backend は agent 側 ID を document_id スコープで解決する。
  // どれも取れないノードは実体要素に解決できないので、ボタンを出さず事実文で案内する
  // （422 を「指定が不正」として見せない）。
  function deliberationTargetId(node) {
    if (!node) return "";
    var nodeId = gv().nodeId(node);
    if (isDbUuid(nodeId)) return nodeId;
    var representative = String(node.representative_component_id || "").trim();
    if (representative) return representative;
    var linked = node.linked_component_ids;
    if (linked && linked.length) {
      for (var i = 0; i < linked.length; i++) {
        var candidate = String(linked[i] || "").trim();
        if (candidate) return candidate;
      }
    }
    return "";
  }

  // 送信時にキャプチャした表示コンテキストが、応答到着時もまだ表示中かどうか。
  function isCurrentContext(mode, nodeKey) {
    if (state.chatMode !== mode) return false;
    if (mode === "node" && state.selectedNodeId !== nodeKey) return false;
    return true;
  }

  // -------------------------------------------------------------------------
  // モーダル骨格
  // -------------------------------------------------------------------------

  function ensureModal() {
    var modal = document.getElementById("graph-review-modal");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "graph-review-modal";
    modal.className = "graph-review-modal";
    modal.hidden = true;
    modal.innerHTML = "" +
      '<div class="graph-review-frame" role="dialog" aria-label="グラフレビュー" data-ui-anchor="graph-review.modal">' +
        '<div class="graph-review-header">' +
          '<div class="graph-review-title">🕸 グラフレビュー — <span id="graph-review-title-text"></span>' +
            '<span id="graph-review-graph-updated" class="graph-review-graph-updated"></span></div>' +
          '<div class="graph-review-toolbar">' +
            '<span class="graph-review-viewtoggle" data-ui-anchor="graph-review.paper-view">表示: ' +
              '<button type="button" class="graph-review-view-btn" data-graph-review-view="graph">グラフ</button>' +
              '<button type="button" class="graph-review-view-btn" data-graph-review-view="paper">論文の順</button>' +
            "</span>" +
            '<span id="graph-review-layer-toolbar" data-ui-anchor="graph-review.layer"></span>' +
            '<label class="graph-review-filter" data-ui-anchor="graph-review.filter-unreviewed">' +
              '<input type="checkbox" id="graph-review-unreviewed-toggle"> 未レビューのみ強調' +
            '</label>' +
            '<span id="graph-review-unreviewed-count" class="graph-review-count"></span>' +
            '<button type="button" id="graph-review-next-unreviewed" class="admin-action-btn" data-ui-anchor="graph-review.next-unreviewed">次の未レビューへ</button>' +
            '<button type="button" id="graph-review-close" class="admin-action-btn">閉じる</button>' +
          '</div>' +
        '</div>' +
        '<div class="graph-review-body">' +
          '<div class="graph-review-graph-pane">' +
            '<div id="graph-review-network-wrap" class="graph-review-network-wrap">' +
              '<div id="graph-review-network" class="graph-review-network" tabindex="0"></div>' +
            "</div>" +
            '<div id="graph-review-paper" class="graph-review-paper" hidden></div>' +
            '<div id="graph-review-graph-status" class="graph-review-status"></div>' +
          '</div>' +
          '<div class="graph-review-side">' +
            '<div id="graph-review-detail" class="graph-review-detail"></div>' +
            '<div class="graph-review-chat" data-ui-anchor="graph-review.chat">' +
              '<div class="graph-review-chat-tabs">' +
                '<button type="button" id="graph-review-chat-tab-graph" class="graph-review-chat-tab" data-ui-anchor="graph-review.graph-chat">グラフ全体とAI対話</button>' +
                '<button type="button" id="graph-review-chat-tab-node" class="graph-review-chat-tab">選択中のノードとAI対話</button>' +
              '</div>' +
              '<div id="graph-review-chat-log" class="graph-review-chat-log"></div>' +
              '<div id="graph-review-chat-newchat" class="graph-review-chat-newchat"></div>' +
              '<div id="graph-review-chat-annotations" class="graph-review-chat-annotations"></div>' +
              '<div class="graph-review-chat-input-row">' +
                '<textarea id="graph-review-chat-input" rows="2" placeholder="このグラフについて質問（例: 裏付けが弱いのはどこですか）"></textarea>' +
                '<button type="button" id="graph-review-chat-send" class="admin-action-btn">送信</button>' +
                '<button type="button" id="graph-review-voice-btn" class="admin-action-btn graph-review-voice-btn" data-ui-anchor="graph-review.voice">🎤 音声</button>' +
              '</div>' +
              '<span id="graph-review-voice-status" class="graph-review-voice-status"></span>' +
              '<div id="graph-review-chat-status" class="graph-review-status"></div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
    bindModalEvents(modal);
    return modal;
  }

  function bindModalEvents(modal) {
    modal.querySelector("#graph-review-close").addEventListener("click", close);
    modal.addEventListener("click", function (e) {
      if (e.target === modal) close();
    });
    modal.querySelector("#graph-review-unreviewed-toggle").addEventListener("change", function () {
      state.unreviewedOnly = !!this.checked;
      // 強調の切替は見ている範囲を変えない（ズーム・パンを保つ）。
      state.preserveViewOnce = true;
      renderNetwork();
    });
    modal.querySelectorAll("[data-graph-review-view]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setView(this.getAttribute("data-graph-review-view"));
      });
    });
    modal.querySelector("#graph-review-next-unreviewed").addEventListener("click", gotoNextUnreviewed);
    modal.querySelector("#graph-review-chat-tab-graph").addEventListener("click", function () {
      switchChatMode("graph");
    });
    modal.querySelector("#graph-review-chat-tab-node").addEventListener("click", function () {
      if (state.selectedNodeId) switchChatMode("node");
    });
    modal.querySelector("#graph-review-chat-send").addEventListener("click", sendChat);
    modal.querySelector("#graph-review-voice-btn").addEventListener("click", toggleVoice);
    modal.querySelector("#graph-review-chat-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        sendChat();
      }
    });
  }

  function setStatus(elementId, message, kind) {
    var el = document.getElementById(elementId);
    if (!el) return;
    el.textContent = message || "";
    el.className = el.className.replace(/\bis-(info|error|success)\b/g, "").trim();
    if (message && kind) el.className += " is-" + kind;
  }

  // -------------------------------------------------------------------------
  // 起動・読み込み
  // -------------------------------------------------------------------------

  function open(documentId, title) {
    if (!deps.apiFetch || !gv()) {
      // 無言の no-op にしない（依存の読み込み漏れを教員に事実として伝える）。
      window.alert("グラフ描画モジュールを読み込めていないため、グラフレビューを開けません。ページを再読み込みしてください。");
      return;
    }
    state.documentId = documentId;
    state.title = title || "教材";
    state.graph = null;
    state.layer = "main";
    state.unreviewedOnly = false;
    state.selectedNodeId = "";
    state.chatMode = "graph";
    state.nodeSessions = {};
    state.graphSession = null;
    state.chatMessages = [];
    state.chatAnnotations = [];
    state.detailNotice = null;
    state.preserveViewOnce = false;
    state.view = "graph";
    state.paperLayer = null;
    state.paperLayerError = null;
    var modal = ensureModal();
    modal.hidden = false;
    stopVoice(); // 前回の音声セッションを持ち越さない
    document.getElementById("graph-review-title-text").textContent = state.title;
    document.getElementById("graph-review-graph-updated").textContent = "";
    document.getElementById("graph-review-unreviewed-toggle").checked = false;
    setStatus("graph-review-graph-status", "グラフを読み込み中...", "info");
    renderViewToggle();
    renderPaperOutline();
    renderChatShell();
    loadGraph();
    // 論文層はグラフと並行して遅延取得する（設計書 §4.1）。失敗してもグラフ表示・
    // レビュー操作は止めない（PL8）。
    loadPaperLayer(documentId);
  }

  function close() {
    var modal = document.getElementById("graph-review-modal");
    if (modal) modal.hidden = true;
    // 画面を閉じたらマイクを必ず解放する（見えない場所で録音を続けない）。
    stopVoice();
    if (state.network) {
      try { state.network.destroy(); } catch (e) { /* noop */ }
      state.network = null;
    }
  }

  function loadGraph(keepSelection) {
    var documentId = state.documentId;
    state.loading = true;
    deps.apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/component-graph")
      .then(function (res) {
        if (!res.ok) throw new Error("グラフの読み込みに失敗しました");
        return res.json();
      })
      .then(function (graph) {
        if (state.documentId !== documentId) return; // 別教材へ切替済みの遅延応答は破棄
        state.graph = graph || {};
        if (!keepSelection) state.selectedNodeId = "";
        setStatus("graph-review-graph-status", "", "");
        if (!graphNodes().length) {
          setStatus(
            "graph-review-graph-status",
            "この教材にはまだ理論操作グラフが構築されていません。解析パイプラインの完了後に開き直してください。",
            "info"
          );
        }
        // レビュー確定後の再読み込みでは、見ている範囲（ズーム・パン）を保つ。
        if (keepSelection) state.preserveViewOnce = true;
        render();
      })
      .catch(function (err) {
        setStatus("graph-review-graph-status", (err && err.message) || "グラフの読み込みに失敗しました", "error");
      })
      .finally(function () {
        state.loading = false;
      });
  }

  // 論文層（読み時射影・LLM 0回）。グラフとは別エンドポイントで、取得の成否は
  // グラフ表示・承認操作に影響させない（PL8 / 設計書 §4.1）。
  function loadPaperLayer(documentId) {
    deps.apiFetch("/admin/documents/" + encodeURIComponent(documentId) + "/paper-layer")
      .then(function (res) {
        if (!res.ok) throw new Error(PAPER_ERROR_TEXT);
        return res.json();
      })
      .then(function (data) {
        if (state.documentId !== documentId) return; // 別教材へ切替済みの遅延応答は破棄
        state.paperLayer = data || {};
        state.paperLayerError = null;
        renderPaperOutline();
        renderDetail();
      })
      .catch(function () {
        if (state.documentId !== documentId) return;
        state.paperLayer = null;
        state.paperLayerError = PAPER_ERROR_TEXT;
        renderPaperOutline();
        renderDetail();
      });
  }

  // -------------------------------------------------------------------------
  // 描画
  // -------------------------------------------------------------------------

  function render() {
    renderGraphUpdatedAt();
    renderViewToggle();
    renderLayerToolbar();
    renderUnreviewedCount();
    renderNetwork();
    renderPaperOutline();
    renderDetail();
    renderChatShell();
  }

  // 左ペインの表示切替（グラフ / 論文の順）。network インスタンスは破棄せず、
  // 包みの hidden だけを切り替える。
  function setView(view) {
    var next = view === "paper" ? "paper" : "graph";
    if (next === state.view) return;
    state.view = next;
    renderViewToggle();
    renderPaperOutline();
    if (next === "graph") {
      // vis は非表示コンテナでの初期化・描画を苦手とするため、戻した時点で描き直す。
      renderNetwork();
      if (state.network) {
        try { state.network.redraw(); } catch (e) { /* noop */ }
      }
    }
  }

  function renderViewToggle() {
    var modal = document.getElementById("graph-review-modal");
    if (!modal) return;
    modal.querySelectorAll("[data-graph-review-view]").forEach(function (btn) {
      var active = btn.getAttribute("data-graph-review-view") === state.view;
      btn.className = "graph-review-view-btn" + (active ? " is-active" : "");
    });
    var wrap = document.getElementById("graph-review-network-wrap");
    if (wrap) wrap.hidden = state.view !== "graph";
  }

  // グラフの鮮度の事実文。裏付け・要確認の情報は graph_json に解析時点で焼き込まれる
  // ため、解析処理の修正・承認後の状況は再解析まで反映されない — いつのグラフを
  // 見ているかを教員に隠さない（GR6 と同じ「正直な提示」の流儀）。
  function renderGraphUpdatedAt() {
    var el = document.getElementById("graph-review-graph-updated");
    if (!el) return;
    var updatedAt = String((state.graph && state.graph.graph_updated_at) || "");
    el.textContent = updatedAt ? "（" + updatedAt.slice(0, 10) + " の解析結果を表示しています）" : "";
  }

  function renderLayerToolbar() {
    var container = document.getElementById("graph-review-layer-toolbar");
    if (!container || !state.graph) return;
    // layerOptions は単層グラフ（main のみ）では空配列を返す = トグル不要。
    var options = gv().layerOptions(graphNodes());
    container.innerHTML = options.map(function (opt) {
      var active = opt.value === state.layer ? " is-active" : "";
      return '<button type="button" class="graph-review-layer-btn' + active + '" data-graph-review-layer="' +
        esc(opt.value) + '">' + esc(opt.label) +
        ' <span class="graph-review-layer-count">' + esc(String(opt.count)) + "</span></button>";
    }).join("");
    container.querySelectorAll("[data-graph-review-layer]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var layer = this.getAttribute("data-graph-review-layer");
        if (layer === state.layer) return;
        state.layer = layer;
        state.selectedNodeId = "";
        renderLayerToolbar();
        renderUnreviewedCount();
        renderNetwork();
        renderDetail();
        // 選択が外れたのでノード対話タブは維持しない（送信時のエラーを防ぐ）。
        switchChatMode("graph");
      });
    });
  }

  function renderUnreviewedCount() {
    var el = document.getElementById("graph-review-unreviewed-count");
    if (!el) return;
    if (!state.graph || !graphNodes().length) { el.textContent = ""; return; }
    el.textContent = "未レビュー " + unreviewedNodesInView().length + " 件";
  }

  function renderNetwork() {
    var container = document.getElementById("graph-review-network");
    if (!container || !state.graph) return;
    // 論文の順で見ている間は vis を組まない（非表示コンテナでのレイアウトを避ける）。
    // 戻したときに setView が描き直す。
    if (state.view === "paper") return;
    // 破棄前に現在の視点を控える（preserveViewOnce のときだけ新 network へ引き継ぐ）。
    var savedPosition = null;
    var savedScale = null;
    if (state.network) {
      try {
        savedPosition = state.network.getViewPosition();
        savedScale = state.network.getScale();
      } catch (e) { savedPosition = null; savedScale = null; }
      try { state.network.destroy(); } catch (e) { /* noop */ }
      state.network = null;
    }
    if (!graphNodes().length) {
      // ノード0件の事実文は loadGraph が出しているので、ここではステータスを触らない。
      container.innerHTML = "";
      return;
    }
    if (!window.vis || !window.vis.Network) {
      container.innerHTML = "";
      setStatus(
        "graph-review-graph-status",
        "グラフ描画ライブラリを読み込めていません。ページを再読み込みしてください。",
        "error"
      );
      return;
    }
    var view = gv().filterByLayer(state.graph, state.layer);
    var nodes = view.nodes || [];
    var displayEdges = gv().displayEdges(view.edges || []);
    var positions = gv().layoutPositions(nodes, displayEdges);
    var g = gv();

    var nodeSpecs = nodes.map(function (node, index) {
      var spec = g.visNodeSpec(node, index, positions, {});
      // 未レビューのみ強調: 承認/却下済みノードを薄く残す（構造の文脈を消さない）。
      if (state.unreviewedOnly && !isUnreviewedNode(node)) {
        spec.opacity = 0.22;
      }
      if (g.nodeId(node) === state.selectedNodeId) {
        spec.borderWidth = Math.max(spec.borderWidth || 2, 4);
      }
      return spec;
    });
    var edgeSpecs = displayEdges.map(function (edge, index) {
      return g.visEdgeSpec(edge, index, {});
    }).filter(function (edge) { return edge.from && edge.to; });

    var byId = {};
    nodes.forEach(function (node) {
      var id = g.nodeId(node);
      if (id) byId[id] = node;
    });

    var network = new window.vis.Network(container, {
      nodes: new window.vis.DataSet(nodeSpecs),
      edges: new window.vis.DataSet(edgeSpecs),
    }, g.networkOptions());
    state.network = network;
    var keepView = !!(state.preserveViewOnce && savedPosition && typeof savedScale === "number");
    state.preserveViewOnce = false; // フラグは一度きり
    network.once("afterDrawing", function () {
      if (keepView) {
        try {
          network.moveTo({ position: savedPosition, scale: savedScale, animation: false });
          return;
        } catch (e) { /* 失敗時は fit へ落とす */ }
      }
      network.fit({ animation: false });
    });
    network.on("click", function (params) {
      if (params.nodes && params.nodes.length && byId[params.nodes[0]]) {
        selectNode(params.nodes[0]);
        return;
      }
      state.selectedNodeId = "";
      renderDetail();
      if (state.chatMode === "node") {
        // 選択が外れたのでノード対話タブは維持しない。
        switchChatMode("graph");
      } else {
        renderChatShell();
      }
    });
    if (state.selectedNodeId && byId[state.selectedNodeId]) {
      try { network.selectNodes([state.selectedNodeId]); } catch (e) { /* noop */ }
    }
  }

  function selectNode(nodeId) {
    state.selectedNodeId = nodeId;
    renderDetail();
    markSelectedPaperChips(); // 論文の順で見ているときの選択強調（描き直さず class のみ）
    if (state.chatMode === "node") {
      // ノードを移ったら対話文脈も移す（セッションはノード単位）。
      switchChatMode("node");
    } else {
      renderChatShell();
    }
  }

  function gotoNextUnreviewed() {
    var pending = unreviewedNodesInView();
    if (!pending.length) {
      setStatus("graph-review-graph-status", "この層に未レビューのノードはありません。", "info");
      return;
    }
    var g = gv();
    var ids = pending.map(function (node) { return g.nodeId(node); }).filter(Boolean);
    var index = ids.indexOf(state.selectedNodeId);
    var nextId = ids[(index + 1) % ids.length];
    selectNode(nextId);
    if (state.network) {
      try {
        state.network.selectNodes([nextId]);
        state.network.focus(nextId, { scale: 1.0, animation: { duration: 300, easingFunction: "easeInOutQuad" } });
      } catch (e) { /* noop */ }
    }
  }

  // -------------------------------------------------------------------------
  // 論文層（graph_paper_layer_design.md §3/§4.1）
  //
  // フレーム（理論操作グラフ）を触らず、論文側の骨格（章・式・図表・claim）と
  // 各ノードの「論文での対応」を読み時射影で表示する。表示するのは display_label /
  // title / text だけで、内部 ID（eq_op_0001 等）は描かない（PL7）。件数・
  // confidence は出さない（PL4）。
  // -------------------------------------------------------------------------

  function paperData() {
    return state.paperLayer || null;
  }

  function paperFactLine(text) {
    return '<div class="graph-review-paper-fact">' + esc(text) + "</div>";
  }

  function paperFactLines(facts, fallback) {
    var list = (facts || []).filter(function (fact) { return String(fact || "").trim(); });
    if (!list.length) return fallback ? paperFactLine(fallback) : "";
    return list.map(function (fact) { return paperFactLine(String(fact)); }).join("");
  }

  // 論文層 DTO のノード面（main は member を合算済みでサーバから来る）。
  function paperNodeEntry(nodeId) {
    var data = paperData();
    var nodes = (data && data.nodes) || null;
    if (!nodes || !nodeId) return null;
    return nodes[nodeId] || null;
  }

  // 章タイトルの索引（evidence / 式の所在表示に使う。DTO は section_id しか持たない）。
  function paperSectionTitles() {
    var data = paperData();
    var sections = (data && data.paper && data.paper.sections) || [];
    var index = {};
    for (var i = 0; i < sections.length; i++) {
      var sid = String(sections[i].section_id || "");
      if (sid) index[sid] = String(sections[i].title || "");
    }
    return index;
  }

  function paperPageLabel(start, end) {
    var from = start == null || start === "" ? "" : String(start);
    var to = end == null || end === "" ? "" : String(end);
    if (from && to && from !== to) return "p." + from + "–" + to;
    if (from) return "p." + from;
    if (to) return "p." + to;
    return "";
  }

  // 「章タイトル ・ p.4」。どちらも無ければ空文字（推測しない — PL3）。
  function paperSourceLabel(sectionId, page, titles) {
    var parts = [];
    var title = titles ? String(titles[String(sectionId || "")] || "") : "";
    if (title) parts.push(title);
    var pageLabel = paperPageLabel(page, null);
    if (pageLabel) parts.push(pageLabel);
    return parts.join(" ・ ");
  }

  // ノードチップの表示名。グラフ側の見出し（stage ラベル / 式の見出し）を優先し、
  // 取れないときだけ DTO の label を使う。どちらも無ければ内部 ID は出さない（PL7）。
  function paperNodeLabel(nodeId) {
    var view = gv();
    var node = state.graph ? nodeById(nodeId) : null;
    if (node && view && view.detailHeading) {
      var heading = String(view.detailHeading(node, nodeId) || "");
      if (heading && heading !== nodeId) return heading;
    }
    var entry = paperNodeEntry(nodeId);
    var label = entry ? String(entry.label || "") : "";
    if (label && label !== nodeId) return label;
    return "（表示名なし）";
  }

  function paperNodeChips(nodeIds) {
    var ids = (nodeIds || []).filter(function (id) { return String(id || "").trim(); });
    if (!ids.length) return "";
    return '<div class="graph-review-paper-chips">' + ids.map(function (nodeId) {
      var selected = nodeId === state.selectedNodeId ? " is-selected" : "";
      return '<button type="button" class="graph-review-paper-chip' + selected +
        '" data-graph-review-node-id="' + esc(nodeId) + '">' + esc(paperNodeLabel(nodeId)) + "</button>";
    }).join("") + "</div>";
  }

  function paperStaticChips(items) {
    var list = (items || []).filter(function (item) {
      return item && String(item.display_label || "").trim();
    });
    if (!list.length) return "";
    return '<div class="graph-review-paper-chips">' + list.map(function (item) {
      return '<span class="graph-review-paper-chip is-static">' + esc(String(item.display_label)) + "</span>";
    }).join("") + "</div>";
  }

  function paperSectionHtml(section) {
    var level = parseInt(section.level, 10);
    if (!(level > 0)) level = 1;
    if (level > 3) level = 3;
    var pageLabel = paperPageLabel(section.page_start, section.page_end);
    var title = String(section.title || "").trim() || "（無題の章）";
    var nodeIds = section.node_ids || [];
    var claims = (section.claims || []).filter(function (claim) {
      return claim && String(claim.text || "").trim();
    });
    var body = "";
    if (nodeIds.length) {
      body += paperNodeChips(nodeIds);
    } else {
      body += paperFactLine(PAPER_SECTION_EMPTY_TEXT);
    }
    body += paperStaticChips(section.equations);
    body += paperStaticChips(section.figures);
    body += paperStaticChips(section.tables);
    if (claims.length) {
      body += '<details class="graph-review-paper-claims"><summary>この章の主張</summary><ul>' +
        claims.map(function (claim) {
          return "<li>" + richText(claim.text) + "</li>";
        }).join("") + "</ul></details>";
    }
    return '<div class="graph-review-paper-section is-level-' + level + '" data-level="' + level + '">' +
      '<div class="graph-review-paper-section-head">' + esc(title) +
      (pageLabel ? '<span class="graph-review-paper-page">' + esc(pageLabel) + "</span>" : "") +
      "</div>" + body + "</div>";
  }

  function paperHeaderHtml(paper) {
    var rows = [];
    function row(label, value, useMath) {
      var text = String(value == null ? "" : value).trim();
      if (!text) return;
      rows.push('<div class="graph-review-paper-headrow"><span class="graph-review-paper-headlabel">' +
        esc(label) + "</span>" + (useMath ? richText(text) : esc(text)) + "</div>");
    }
    row("タイトル", paper.title, false);
    row("目的", paper.goal, true);
    row("中心の問い", paper.central_question, true);
    if (paper.central_thesis) row("中心命題", paper.central_thesis.text, true);
    if (!rows.length) return "";
    return '<div class="graph-review-paper-header">' + rows.join("") + "</div>";
  }

  function paperBackboneHtml(backbone) {
    var list = (backbone || []).filter(function (block) {
      return block && (String(block.label || "").trim() || String(block.summary || "").trim());
    });
    if (!list.length) return "";
    return '<div class="graph-review-paper-block"><div class="graph-review-paper-block-title">論文の論理ブロック</div><ul>' +
      list.map(function (block) {
        var label = String(block.label || "").trim();
        var summary = String(block.summary || "").trim();
        return "<li>" + (label ? '<span class="graph-review-paper-strong">' + esc(label) + "</span>" : "") +
          (summary ? " " + richText(summary) : "") + "</li>";
      }).join("") + "</ul></div>";
  }

  // 被覆（掛かっていない論文要素）。失敗ではなく信号なので警告色にせず、件数も出さない
  // （PL4 / 設計書 §3.2）。
  function paperCoverageHtml(coverage) {
    if (!coverage) return "";
    var groups = [
      { title: "掛かっていない式", items: coverage.unbound_equations, key: "display_label" },
      { title: "掛かっていない図", items: coverage.unbound_figures, key: "display_label" },
      { title: "掛かっていない主張", items: coverage.unbound_claims, key: "text" },
    ];
    var html = "";
    groups.forEach(function (group) {
      var list = (group.items || []).filter(function (item) {
        return item && String(item[group.key] || "").trim();
      });
      if (!list.length) return;
      html += '<div class="graph-review-paper-coverage-group"><div class="graph-review-paper-subtitle">' +
        esc(group.title) + "</div><ul>" + list.map(function (item) {
          var text = String(item[group.key]);
          return "<li>" + (group.key === "text" ? richText(text) : esc(text)) + "</li>";
        }).join("") + "</ul></div>";
    });
    if (!html) return "";
    return '<div class="graph-review-paper-coverage"><div class="graph-review-paper-block-title">' +
      "フレームに掛かっていない要素</div>" + html + "</div>";
  }

  function renderPaperOutline() {
    var container = document.getElementById("graph-review-paper");
    if (!container) return;
    container.hidden = state.view !== "paper";
    if (state.view !== "paper") { container.innerHTML = ""; return; }

    if (state.paperLayerError) {
      container.innerHTML = paperFactLine(state.paperLayerError);
      return;
    }
    var data = paperData();
    if (!data) {
      container.innerHTML = paperFactLine(PAPER_LOADING_TEXT);
      return;
    }
    if (data.available === false) {
      container.innerHTML = paperFactLines(data.facts, PAPER_UNAVAILABLE_TEXT);
      return;
    }
    var paper = data.paper || {};
    var sections = paper.sections || [];
    var html = paperFactLines(data.facts, "");
    html += paperHeaderHtml(paper);
    if (sections.length) {
      html += sections.map(paperSectionHtml).join("");
    } else {
      html += paperFactLine(PAPER_UNAVAILABLE_TEXT);
    }
    html += paperBackboneHtml(paper.backbone);
    html += paperCoverageHtml(data.coverage);
    container.innerHTML = html;

    container.querySelectorAll("[data-graph-review-node-id]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var nodeId = this.getAttribute("data-graph-review-node-id");
        // 論文の順で見ている間は network を触らない（フォーカス移動もしない）。
        selectNode(nodeId);
      });
    });
  }

  function markSelectedPaperChips() {
    var container = document.getElementById("graph-review-paper");
    if (!container) return;
    container.querySelectorAll("[data-graph-review-node-id]").forEach(function (chip) {
      var selected = chip.getAttribute("data-graph-review-node-id") === state.selectedNodeId;
      chip.className = "graph-review-paper-chip" + (selected ? " is-selected" : "");
    });
  }

  // 右ペイン「論文での対応」。取得前・失敗・available:false は区画ごと事実文1行に
  // 縮退し、既存のレビュー操作には影響させない（設計書 §4.1）。
  function paperFacingHtml(nodeId) {
    var head = '<div class="graph-review-paper-facing" data-ui-anchor="graph-review.paper-facing">' +
      '<div class="graph-review-paper-facing-title">論文での対応</div>';
    var tail = "</div>";
    if (state.paperLayerError) return head + paperFactLine(state.paperLayerError) + tail;
    var data = paperData();
    if (!data) return head + paperFactLine(PAPER_LOADING_TEXT) + tail;
    if (data.available === false) {
      return head + paperFactLines(data.facts, PAPER_UNAVAILABLE_TEXT) + tail;
    }
    var entry = paperNodeEntry(nodeId);
    if (!entry) return head + paperFactLine(PAPER_NODE_MISSING_TEXT) + tail;

    var titles = paperSectionTitles();
    var html = "";

    function block(title, body) {
      if (!body) return;
      html += '<div class="graph-review-paper-block"><div class="graph-review-paper-subtitle">' +
        esc(title) + "</div>" + body + "</div>";
    }

    var narrativeRole = String(entry.narrative_role || "").trim();
    if (narrativeRole) {
      html += '<div class="graph-review-paper-role">' + esc(narrativeRole) + "</div>";
    }

    // 論文上の位置（PL3: リンクの実所在のみ。無ければ推定せず事実文）。
    var sections = entry.sections || [];
    if (entry.unlocated || !sections.length) {
      html += paperFactLine(PAPER_UNLOCATED_TEXT);
    } else {
      block("論文上の位置", "<ul>" + sections.map(function (section) {
        var pageLabel = paperPageLabel(section.page_start, null);
        return "<li>" + esc(String(section.title || "").trim() || "（無題の章）") +
          (pageLabel ? '<span class="graph-review-paper-page">' + esc(pageLabel) + "</span>" : "") + "</li>";
      }).join("") + "</ul>");
    }

    var thesisRoles = (entry.thesis_roles || []).filter(function (role) {
      return role && (String(role.section_label || "").trim() || String(role.text || "").trim());
    });
    if (thesisRoles.length) {
      block("中心命題での役割", "<ul>" + thesisRoles.map(function (role) {
        var label = String(role.section_label || "").trim();
        return "<li>" + (label ? '<span class="graph-review-paper-strong">' + esc(label) + "</span> " : "") +
          richText(String(role.text || "")) + "</li>";
      }).join("") + "</ul>");
    }

    var equations = entry.equations || [];
    if (equations.length) {
      block("式", equations.map(function (equation) {
        var chips = [];
        var roleLabel = EQUATION_ROLE_LABELS[String(equation.role || "")] || "";
        if (roleLabel) chips.push(roleLabel);
        if (equation.needs_math_review) chips.push("数式要確認");
        var latex = String(equation.latex || "").trim();
        var body = latex ? richText("$" + latex + "$") : esc(String(equation.plain_text || ""));
        return '<div class="graph-review-paper-item">' +
          '<div class="graph-review-paper-item-head">' +
            '<span class="graph-review-paper-chip is-static">' + esc(String(equation.display_label || "")) + "</span>" +
            chips.map(function (chip) {
              return '<span class="graph-review-chip">' + esc(chip) + "</span>";
            }).join("") +
          "</div>" +
          '<div class="graph-review-paper-item-body">' + body + "</div>" +
          "</div>";
      }).join(""));
    }

    var evidence = (entry.evidence || []).filter(function (item) {
      return item && String(item.text || "").trim();
    });
    if (evidence.length) {
      block("根拠の逐語引用", evidence.map(function (item) {
        var source = paperSourceLabel(item.section_id, item.page, titles);
        return '<blockquote class="graph-review-paper-quote">' + richText(String(item.text)) +
          (source ? '<cite class="graph-review-paper-source">' + esc(source) + "</cite>" : "") +
          "</blockquote>";
      }).join(""));
    }

    var media = [].concat(entry.figures || []).concat(entry.tables || []).filter(function (item) {
      return item && (String(item.display_label || "").trim() || String(item.caption || "").trim());
    });
    if (media.length) {
      block("図・表", "<ul>" + media.map(function (item) {
        var label = String(item.display_label || "").trim();
        var caption = String(item.caption || "").trim();
        return "<li>" + (label ? '<span class="graph-review-paper-strong">' + esc(label) + "</span>" : "") +
          (caption ? " " + esc(caption) : "") + "</li>";
      }).join("") + "</ul>");
    }

    var symbols = (entry.symbols || []).filter(function (item) {
      return item && String(item.symbol || "").trim();
    });
    if (symbols.length) {
      block("記号", "<ul>" + symbols.map(function (item) {
        var roleLabel = SYMBOL_ROLE_LABELS[String(item.role || "")] || "";
        var quote = String(item.definition_quote || "").trim();
        return "<li>" + richText("$" + String(item.symbol) + "$") +
          (roleLabel ? '<span class="graph-review-chip">' + esc(roleLabel) + "</span>" : "") +
          (quote ? '<div class="graph-review-paper-note">' + richText(quote) + "</div>" : "") +
          "</li>";
      }).join("") + "</ul>");
    }

    var derivations = entry.derivations || [];
    if (derivations.length) {
      block("導出", derivations.map(function (derivation) {
        var steps = (derivation.steps || []).map(function (step) {
          var inputs = (step.input_labels || []).join(" , ");
          var outputs = (step.output_labels || []).join(" , ");
          var flow = inputs || outputs ? esc(inputs) + " → " + esc(outputs) : "";
          var reason = String(step.reason || "").trim();
          return "<li>" + esc(String(step.operation || "")) +
            (flow ? '<div class="graph-review-paper-flow">' + flow + "</div>" : "") +
            (reason ? '<div class="graph-review-paper-note">' + richText(reason) + "</div>" : "") +
            "</li>";
        }).join("");
        return '<div class="graph-review-paper-item">' +
          '<div class="graph-review-paper-item-head">' +
            '<span class="graph-review-paper-strong">' + esc(String(derivation.operation || "")) + "</span>" +
          "</div>" +
          (steps ? "<ul>" + steps + "</ul>" : "") +
          "</div>";
      }).join(""));
    }

    if (entry.explanation && String(entry.explanation.body || "").trim()) {
      var statusLabel = EXPLANATION_STATUS_LABELS[String(entry.explanation.status || "")] || "";
      block("この論文での説明",
        (statusLabel ? '<span class="graph-review-chip">' + esc(statusLabel) + "</span>" : "") +
        '<div class="graph-review-paper-item-body">' + richText(String(entry.explanation.body)) + "</div>");
    }

    if (entry.component) {
      var componentRows = "";
      [
        { label: "要約", value: entry.component.summary },
        { label: "教えどころ", value: entry.component.teaching_takeaway },
        { label: "中心命題での位置", value: entry.component.role_in_thesis },
      ].forEach(function (row) {
        var text = String(row.value == null ? "" : row.value).trim();
        if (!text) return;
        componentRows += '<div class="graph-review-paper-headrow"><span class="graph-review-paper-headlabel">' +
          esc(row.label) + "</span>" + richText(text) + "</div>";
      });
      if (componentRows) block("論理要素の説明", componentRows);
    }

    if (!html) html = paperFactLine(PAPER_NODE_MISSING_TEXT);
    return head + html + tail;
  }

  // -------------------------------------------------------------------------
  // ノード詳細ペイン（レビュー専用の投影。語彙は graphView から引く）
  // -------------------------------------------------------------------------

  function collectClaimRefs(node) {
    var ids = [].concat(node.linked_claim_ids || [])
      .concat(node.input_claim_ids || [])
      .concat(node.output_claim_ids || [])
      .concat(node.required_claim_ids || []);
    var seen = {};
    var unique = [];
    ids.forEach(function (id) {
      var key = String(id || "").trim();
      if (key && !seen[key]) { seen[key] = true; unique.push(key); }
    });
    var claimIndex = (state.graph && state.graph.reference_index && state.graph.reference_index.claims) || {};
    return unique.map(function (agentId) {
      var resolved = claimIndex[agentId];
      if (!resolved) {
        // 参照インデックスに無い＝本当に解決できない参照（新契約では稀）。
        return { agent_id: agentId, resolution: "", claim_id: "", text: "", review_status: "" };
      }
      var claimId = String(resolved.claim_id || "");
      // 旧グラフ・旧バックエンドは resolution を持たない。claim_id があれば DB 由来。
      var resolution = String(resolved.resolution || (claimId ? "db" : ""));
      return {
        agent_id: agentId,
        resolution: resolution,
        claim_id: claimId,
        text: resolved.text || "",
        // DB 行を持たない解析由来の claim は review_status を持たない（既定を捏造しない）。
        review_status: claimId ? (resolved.review_status || "teacher_review_required") : "",
        origin: String(resolved.origin || ""),
        support_status: String(resolved.support_status || ""),
        is_atomic: !!resolved.is_atomic,
        parent_claim_id: String(resolved.parent_claim_id || ""),
        parent_review_status: String(resolved.parent_review_status || ""),
      };
    });
  }

  // 根拠 claim 1行分の HTML。
  //
  // 参照インデックスの解決元は2種類ある（設計書 §11 / backend の reference_index）:
  //   - resolution="db"       … theory_claims の行がある。従来どおり承認できる。
  //   - resolution="artifact" … 解析結果（atomic rewrite の細分化 claim / 式から合成した
  //                             claim）にしか存在せず、承認行を持たない。本文は出したうえで
  //                             「未承認（解析結果）」と明示し、元の主張がある場合だけ
  //                             そちらの承認へ導く（GR1: 確定は教員の明示操作のみ）。
  // どちらでも内部 ID（agent_id）は教員 UI に出さない。
  function claimRowHtml(claim) {
    var artifact = claim.resolution === "artifact";
    var chips = [];
    var button = "";
    var note = "";

    if (artifact) {
      chips.push("未承認（解析結果）");
      var originLabel = CLAIM_ORIGIN_LABELS[claim.origin] || "";
      if (originLabel) chips.push(originLabel);
      if (claim.parent_claim_id) {
        chips.push("元の主張: " + reviewStatusLabel(claim.parent_review_status));
        button = '<button type="button" class="admin-action-btn graph-review-claim-approve" data-graph-review-claim="' +
          esc(claim.parent_claim_id) + '" data-ui-anchor="graph-review.claim-approve"' +
          (isApproved(claim.parent_review_status) ? " disabled" : "") + ">元の主張を承認</button>";
      } else {
        note = "解析結果のみの根拠で、承認対象の行はありません。";
      }
    } else if (claim.claim_id) {
      chips.push(reviewStatusLabel(claim.review_status));
      button = '<button type="button" class="admin-action-btn graph-review-claim-approve" data-graph-review-claim="' +
        esc(claim.claim_id) + '" data-ui-anchor="graph-review.claim-approve"' +
        (isApproved(claim.review_status) ? " disabled" : "") + ">承認</button>";
    } else {
      chips.push("未解決");
    }

    var claimText = String(claim.text || "").trim()
      ? richText(claim.text)
      : "未解決の根拠（本文を取得できません）";
    return '<div class="graph-review-claim-row">' +
      '<div class="graph-review-claim-text">' + claimText + "</div>" +
      (note ? '<div class="graph-review-claim-note">' + esc(note) + "</div>" : "") +
      '<div class="graph-review-claim-meta">' +
        chips.map(function (chip) {
          return '<span class="graph-review-chip">' + esc(chip) + "</span>";
        }).join("") +
        button +
      "</div>" +
      "</div>";
  }

  function renderDetail() {
    var container = document.getElementById("graph-review-detail");
    if (!container) return;
    if (!state.graph || !graphNodes().length) {
      container.innerHTML = '<div class="graph-review-empty">グラフがありません。</div>';
      state.detailNotice = null; // 表示先が無くなったので持ち越さない
      return;
    }
    var node = state.selectedNodeId ? nodeById(state.selectedNodeId) : null;
    if (!node) {
      state.detailNotice = null; // 対象ノードが無いので持ち越さない
      var pending = unreviewedNodesInView().length;
      container.innerHTML = '<div class="graph-review-empty">' +
        'ノードを選ぶと詳細とレビュー操作が表示されます。' +
        (pending ? "<br>この層の未レビューは " + pending + " 件です。「次の未レビューへ」で順に確認できます。" : "") +
        "</div>";
      return;
    }
    var g = gv();
    var nodeId = g.nodeId(node);
    var status = reviewStatus(node);
    var backing = String(node.source_backing_status || "");
    var reasons = (node.review_reasons || []).map(function (reason) {
      return g.reviewReasonLabel(reason);
    }).filter(Boolean);
    // サーバの読み時射影（theory_components.py）: 承認済みノードの理由は
    // review_reasons_at_analysis へ移り、source_backed の warning は advisory 宣言される。
    // 「要確認の理由」の見出しはレビュー要求のときだけ使う（承認済み・参考メモに使わない）。
    var reasonsAdvisory = !!node.review_reasons_advisory;
    var archivedReasons = (node.review_reasons_at_analysis || []).map(function (reason) {
      return g.reviewReasonLabel(reason);
    }).filter(Boolean);
    var claims = collectClaimRefs(node);

    var html = "" +
      '<div class="graph-review-detail-head">' +
        '<div class="graph-review-detail-title">' + esc(g.detailHeading(node, nodeId)) + "</div>" +
        '<div class="graph-review-detail-chips">' +
          '<span class="graph-review-chip">' + esc(g.roleLabel(node)) + "</span>" +
          (backing ? '<span class="graph-review-chip">裏付け: ' + esc(g.sourceBackingLabel(backing)) + "</span>" : "") +
          '<span class="graph-review-chip graph-review-chip-status">' + esc(reviewStatusLabel(status)) + "</span>" +
        "</div>" +
      "</div>" +
      (String(node.description || "").trim()
        ? '<div class="graph-review-detail-desc">' + richText(node.description) + "</div>"
        : "") +
      (reasons.length
        ? '<div class="graph-review-detail-reasons' + (reasonsAdvisory ? " graph-review-detail-reasons-advisory" : "") + '">' +
          (reasonsAdvisory ? "解析メモ（参考）: " : "要確認の理由: ") + esc(reasons.join(" / ")) + "</div>"
        : "") +
      (archivedReasons.length
        ? '<div class="graph-review-detail-reasons graph-review-detail-reasons-archived">解析時点のメモ（承認済みのため確認は不要です）: ' +
          esc(archivedReasons.join(" / ")) + "</div>"
        : "") +
      // 論文での対応（読み時射影。承認状態は変えない — PL1/PL5）。
      paperFacingHtml(nodeId);

    // component 承認・却下（server が最終判定。GR1: 教員の明示操作のみ）。
    // 集約 main ノード（DB UUID でない graph-native ID）は theory_components の行を
    // 持たないため、承認・却下ボタン自体を出さない。
    var reviewable = isDbUuid(nodeId);
    // 「深く検討」は実体要素に解決できるときだけ出す（解決できないノードで押させて
    // サーバ 422 を見せない）。集約ノードでは代表要素を開くことを事実文で明示する。
    var deliberationTarget = deliberationTargetId(node);
    var deliberateBtn = deliberationTarget
      ? '<button type="button" class="admin-action-btn" data-graph-review-action="deliberate" data-ui-anchor="graph-review.open-deliberation">深く検討</button>'
      : "";
    if (reviewable) {
      var approveDisabled = isApproved(status) ? " disabled" : "";
      var rejectDisabled = isRejected(status) ? " disabled" : "";
      html += '<div class="graph-review-detail-actions">' +
        '<button type="button" class="admin-action-btn" data-graph-review-action="approve" data-ui-anchor="graph-review.approve"' + approveDisabled + ">承認</button>" +
        '<button type="button" class="admin-action-btn" data-graph-review-action="reject" data-ui-anchor="graph-review.reject"' + rejectDisabled + ">却下</button>" +
        deliberateBtn +
        "</div>";
    } else {
      html += '<div class="graph-review-empty">このノードは複数の要素を集約した表示用ノードのため、承認・却下は集約元の各要素（式の詳細層のノードや根拠 claim）で行います。' +
        (deliberationTarget
          ? "「深く検討」は集約元の代表要素を開きます。"
          : "集約元の要素を特定できないため、このノードでは「深く検討」を開けません。式の詳細層のノードや根拠 claim から確認してください。") +
        "</div>" +
        (deliberateBtn ? '<div class="graph-review-detail-actions">' + deliberateBtn + "</div>" : "");
    }
    html += '<div id="graph-review-detail-status" class="graph-review-status"></div>';

    if (claims.length) {
      html += '<div class="graph-review-claims"><div class="graph-review-claims-title">根拠 claim</div>' +
        claims.map(claimRowHtml).join("") +
        "</div>";
    }

    container.innerHTML = html;

    container.querySelectorAll("[data-graph-review-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var action = this.getAttribute("data-graph-review-action");
        if (action === "approve") reviewComponent(node, "approve");
        else if (action === "reject") reviewComponent(node, "reject");
        else if (action === "deliberate") openDeliberation(node);
      });
    });
    container.querySelectorAll("[data-graph-review-claim]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        approveClaim(this.getAttribute("data-graph-review-claim"));
      });
    });

    // 操作結果は再読み込み後の再描画で消えるため、ここで一度だけ再表示する。
    if (state.detailNotice) {
      setStatus("graph-review-detail-status", state.detailNotice.message, state.detailNotice.kind);
      state.detailNotice = null;
    }
  }

  function openDeliberation(node) {
    if (!window.Deliberation || !window.Deliberation.openElement) return;
    var g = gv();
    var targetId = deliberationTargetId(node);
    if (!targetId) {
      // ボタン自体を出していない経路だが、状態変化と競合した場合も 422 を見せない。
      setStatus("graph-review-detail-status",
        "このノードは実体要素に解決できないため、深く検討を開けません。", "info");
      return;
    }
    window.Deliberation.openElement("theory_component", targetId, {
      documentId: state.documentId,
      title: g.detailHeading(node, g.nodeId(node)),
    });
  }

  // -------------------------------------------------------------------------
  // レビュー確定（既存 API の呼び出しのみ。成功後はサーバの状態で再描画）
  // -------------------------------------------------------------------------

  function reviewComponent(node, action) {
    var componentId = gv().nodeId(node);
    if (!componentId) return;
    setStatus("graph-review-detail-status", action === "approve" ? "承認中..." : "却下中...", "info");
    deps.apiFetch("/admin/theory-components/" + encodeURIComponent(componentId) + "/" + action, {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (res.ok) return res.json();
        return res.json().then(function (body) {
          var detail = body && body.detail;
          throw new Error(typeof detail === "string" ? detail :
            (action === "approve" ? "承認できませんでした" : "却下できませんでした"));
        }, function () {
          throw new Error(res.status === 404
            ? "このノードは論理要素の行を持たないため、ここでは承認・却下できません。"
            : (action === "approve" ? "承認できませんでした" : "却下できませんでした"));
        });
      })
      .then(function () {
        var message = action === "approve" ? "承認しました" : "却下しました";
        setStatus("graph-review-detail-status", message, "success");
        // 直後の再読み込み → renderDetail() で消えないよう、再表示用に積む。
        state.detailNotice = { message: message, kind: "success" };
        loadGraph(true);
      })
      .catch(function (err) {
        setStatus("graph-review-detail-status", (err && err.message) || "操作に失敗しました", "error");
      });
  }

  function approveClaim(claimId) {
    if (!claimId) return;
    setStatus("graph-review-detail-status", "claim を承認中...", "info");
    deps.apiFetch("/admin/claims/" + encodeURIComponent(claimId) + "/review", {
      method: "POST",
      body: JSON.stringify({ review_status: "teacher_approved" }),
    })
      .then(function (res) {
        if (res.ok) return res.json();
        return res.json().then(function (body) {
          var detail = body && body.detail;
          throw new Error(typeof detail === "string" ? detail : "claim の承認に失敗しました");
        }, function () {
          throw new Error("claim の承認に失敗しました");
        });
      })
      .then(function () {
        setStatus("graph-review-detail-status", "claim を承認しました", "success");
        state.detailNotice = { message: "claim を承認しました", kind: "success" };
        loadGraph(true);
      })
      .catch(function (err) {
        setStatus("graph-review-detail-status", (err && err.message) || "claim の承認に失敗しました", "error");
      });
  }

  // -------------------------------------------------------------------------
  // AI 対話（ノード = W層 sessions API / グラフ全体 = graph-sessions API）
  // -------------------------------------------------------------------------

  function switchChatMode(mode) {
    state.chatMode = mode;
    state.chatMessages = [];
    state.chatAnnotations = [];
    clearNewChatButton();
    renderChatShell();
    // 履歴の復元: セッションが既にあればそのメッセージを流し込む。
    var session = activeSession();
    if (session) {
      state.chatMessages = sessionMessages(session);
      renderChatLog();
    }
  }

  function activeSession() {
    if (state.chatMode === "graph") return state.graphSession;
    return state.nodeSessions[state.selectedNodeId] || null;
  }

  function sessionMessages(session) {
    return ((session && session.messages) || []).filter(function (m) {
      return m && typeof m === "object";
    }).map(function (m) {
      return { role: String(m.role || "user"), content: String(m.content || "") };
    });
  }

  function renderChatShell() {
    var tabGraph = document.getElementById("graph-review-chat-tab-graph");
    var tabNode = document.getElementById("graph-review-chat-tab-node");
    if (!tabGraph || !tabNode) return;
    tabGraph.className = "graph-review-chat-tab" + (state.chatMode === "graph" ? " is-active" : "");
    tabNode.className = "graph-review-chat-tab" + (state.chatMode === "node" ? " is-active" : "");
    tabNode.disabled = !state.selectedNodeId;
    var input = document.getElementById("graph-review-chat-input");
    if (input) {
      input.placeholder = state.chatMode === "graph"
        ? "このグラフについて質問（例: 裏付けが弱いのはどこですか）"
        : "選択中のノードについて質問（例: この段の根拠は本文のどこですか）";
    }
    renderChatLog();
    renderChatAnnotations();
  }

  function renderChatLog() {
    var log = document.getElementById("graph-review-chat-log");
    if (!log) return;
    if (!state.chatMessages.length) {
      log.innerHTML = '<div class="graph-review-empty">' +
        (state.chatMode === "graph"
          ? "グラフ全体を文脈に AI と検討できます。応答は仮説であり、承認・却下の判断は教員が行います。"
          : "選択中のノードについて AI と検討できます。応答は仮説であり、確定は教員の操作のみです。") +
        "</div>";
      return;
    }
    log.innerHTML = state.chatMessages.map(function (m) {
      var roleClass = m.role === "assistant" ? "assistant" : "user";
      return '<div class="graph-review-chat-msg is-' + roleClass + '">' + esc(m.content) + "</div>";
    }).join("");
    log.scrollTop = log.scrollHeight;
  }

  function renderChatAnnotations() {
    var container = document.getElementById("graph-review-chat-annotations");
    if (!container) return;
    if (!state.chatAnnotations.length) {
      container.innerHTML = "";
      return;
    }
    container.innerHTML = state.chatAnnotations.map(function (annotation) {
      var body = annotation.body || {};
      var bodyText = typeof body === "string" ? body : (body.text || body.summary || JSON.stringify(body));
      var decided = annotation.status !== "candidate";
      var commitBtn = annotation.commit_supported && !decided
        ? '<button type="button" class="admin-action-btn" data-graph-review-annotation-commit="' + esc(annotation.id) + '">確定</button>'
        : "";
      var dismissBtn = !decided
        ? '<button type="button" class="admin-action-btn" data-graph-review-annotation-dismiss="' + esc(annotation.id) + '">却下</button>'
        : "";
      return '<div class="graph-review-annotation">' +
        '<div class="graph-review-annotation-head">AI候補注釈（' + esc(annotation.kind || "") + " / " + esc(annotation.confidence_label || "") + "）</div>" +
        '<div class="graph-review-annotation-body">' + esc(bodyText) + "</div>" +
        (annotation.reason ? '<div class="graph-review-annotation-reason">理由: ' + esc(annotation.reason) + "</div>" : "") +
        '<div class="graph-review-annotation-actions">' + commitBtn + dismissBtn +
          (decided ? '<span class="graph-review-chip">' + esc(annotation.status) + "</span>" : "") +
        "</div>" +
      "</div>";
    }).join("");
    container.querySelectorAll("[data-graph-review-annotation-commit]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        decideAnnotation(this.getAttribute("data-graph-review-annotation-commit"), "commit");
      });
    });
    container.querySelectorAll("[data-graph-review-annotation-dismiss]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        decideAnnotation(this.getAttribute("data-graph-review-annotation-dismiss"), "dismiss");
      });
    });
  }

  function decideAnnotation(annotationId, action) {
    if (!annotationId) return;
    deps.apiFetch("/admin/deliberation/annotations/" + encodeURIComponent(annotationId) + "/" + action, {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (!res.ok) throw new Error(action === "commit" ? "確定に失敗しました" : "却下に失敗しました");
        return res.json();
      })
      .then(function (data) {
        var updated = data.annotation || {};
        state.chatAnnotations = state.chatAnnotations.map(function (annotation) {
          return annotation.id === annotationId ? Object.assign({}, annotation, updated) : annotation;
        });
        renderChatAnnotations();
      })
      .catch(function (err) {
        setStatus("graph-review-chat-status", (err && err.message) || "操作に失敗しました", "error");
      });
  }

  // mode / nodeKey は呼び出し時にキャプチャした値を使う（セッション作成中にモードや
  // ノードを切り替えても、応答は必ず開始時の対象へ紐づける）。
  function ensureSession(mode, nodeKey, cb) {
    var called = false;
    function done(session) {
      // 二重コールバック防止（cb 内の同期例外が失敗経路を再び呼ばないようにする）。
      if (called) return;
      called = true;
      cb(session);
    }
    var existing = mode === "graph" ? state.graphSession : (state.nodeSessions[nodeKey] || null);
    if (existing) { done(existing); return; }
    if (mode === "graph") {
      var documentId = state.documentId;
      deps.apiFetch("/admin/deliberation/documents/" + encodeURIComponent(documentId) + "/graph-sessions", {
        method: "POST",
        body: "{}",
      })
        .then(function (res) {
          if (res.ok) return res.json();
          return res.json().then(function (body) {
            throw new Error((body && typeof body.detail === "string" && body.detail) || "対話を開始できませんでした");
          }, function () { throw new Error("対話を開始できませんでした"); });
        })
        .then(function (data) {
          state.graphSession = data.session;
          if (isCurrentContext(mode, nodeKey)) {
            state.chatMessages = sessionMessages(data.session);
            renderChatLog();
          }
          done(data.session);
        }, function (err) {
          setStatus("graph-review-chat-status", (err && err.message) || "対話を開始できませんでした", "error");
          done(null);
        });
      return;
    }
    var node = nodeById(nodeKey);
    if (!node) { done(null); return; }
    // 「深く検討」と同じ解決規則（DB UUID → 代表要素の agent 側 ID）。解決できない
    // ノードはリクエストせずに事実文だけ出す（サーバの 422 を待たない）。
    var componentId = deliberationTargetId(node);
    if (!componentId) {
      setStatus("graph-review-chat-status",
        "このノードは論理要素として解決できないため、ノード対話は開始できません。グラフ全体との対話をご利用ください。",
        "info");
      done(null);
      return;
    }
    deps.apiFetch("/admin/deliberation/sessions", {
      method: "POST",
      body: JSON.stringify({
        scope: "document",
        element_type: "theory_component",
        element_id: componentId,
        document_id: state.documentId,
        title: gv().detailHeading(node, gv().nodeId(node)),
      }),
    })
      .then(function (res) {
        if (res.ok) return res.json();
        // 404 / 422 はいずれも「論理要素として解決できない」縮退（サーバの内部文言を素通ししない）。
        var unresolvable = res.status === 404 || res.status === 422;
        return res.json().then(function (body) {
          throw new Error(unresolvable
            ? "このノードは論理要素として解決できないため、ノード対話は開始できません。グラフ全体との対話をご利用ください。"
            : ((body && typeof body.detail === "string" && body.detail) || "対話を開始できませんでした"));
        }, function () {
          throw new Error(unresolvable
            ? "このノードは論理要素として解決できないため、ノード対話は開始できません。グラフ全体との対話をご利用ください。"
            : "対話を開始できませんでした");
        });
      })
      .then(function (data) {
        state.nodeSessions[nodeKey] = data.session;
        done(data.session);
      }, function (err) {
        setStatus("graph-review-chat-status", (err && err.message) || "対話を開始できませんでした", "error");
        done(null);
      });
  }

  // グラフ全体対話の上限（429）に達したときだけ出す再開ボタン。
  function clearNewChatButton() {
    var container = document.getElementById("graph-review-chat-newchat");
    if (container) container.innerHTML = "";
  }

  function renderNewChatButton() {
    var container = document.getElementById("graph-review-chat-newchat");
    if (!container || container.innerHTML) return; // 一度だけ
    container.innerHTML = '<button type="button" class="admin-action-btn" id="graph-review-new-chat" data-ui-anchor="graph-review.new-chat">新しい対話を開始</button>';
    var button = document.getElementById("graph-review-new-chat");
    if (button) button.addEventListener("click", startNewGraphChat);
  }

  function startNewGraphChat() {
    var documentId = state.documentId;
    setStatus("graph-review-chat-status", "新しい対話を開始しています...", "info");
    deps.apiFetch("/admin/deliberation/documents/" + encodeURIComponent(documentId) + "/graph-sessions?force_new=true", {
      method: "POST",
      body: "{}",
    })
      .then(function (res) {
        if (res.ok) return res.json();
        return res.json().then(function (body) {
          throw new Error((body && typeof body.detail === "string" && body.detail) || "新しい対話を開始できませんでした");
        }, function () { throw new Error("新しい対話を開始できませんでした"); });
      })
      .then(function (data) {
        if (state.documentId !== documentId) return; // 別教材へ切替済みの遅延応答は破棄
        state.graphSession = data.session;
        state.chatMessages = [];
        state.chatAnnotations = [];
        clearNewChatButton();
        setStatus("graph-review-chat-status", "", "");
        renderChatLog();
        renderChatAnnotations();
      }, function (err) {
        setStatus("graph-review-chat-status", (err && err.message) || "新しい対話を開始できませんでした", "error");
      });
  }

  function sendChat() {
    var input = document.getElementById("graph-review-chat-input");
    var content = (input && input.value || "").trim();
    if (!content) return;
    sendChatText(content);
  }

  // テキスト送信の中核。入力欄からもハンズフリー音声からも同じ経路を通す。
  // cb(err, replyText) — err.httpStatus は呼び出し側の分岐（429 等）に使う。
  function sendChatText(content, cb) {
    var finished = false;
    function finish(err, reply) {
      if (finished) return;
      finished = true;
      // 呼び出し側（音声ループ）の失敗を送信経路のエラー扱いにしない。
      try {
        if (typeof cb === "function") cb(err || null, reply || "");
      } catch (e) { /* noop */ }
    }
    if (state.chatBusy) { finish(new Error("応答を作成中です。")); return; }
    content = String(content == null ? "" : content).trim();
    if (!content) { finish(new Error("送信する内容がありません。")); return; }
    var input = document.getElementById("graph-review-chat-input");
    // 送信時点の表示コンテキストを固定する（応答到着までに切り替わっても混線させない）。
    var mode = state.chatMode;
    var nodeKey = state.selectedNodeId;
    var documentId = state.documentId;
    if (mode === "node" && !nodeKey) {
      setStatus("graph-review-chat-status", "ノードが選択されていません。", "error");
      finish(new Error("ノードが選択されていません。"));
      return;
    }
    state.chatBusy = true;
    setStatus("graph-review-chat-status", "AI が応答を作成中...", "info");
    ensureSession(mode, nodeKey, function (session) {
      if (!session) {
        state.chatBusy = false;
        finish(new Error("対話を開始できませんでした"));
        return;
      }
      var path = mode === "graph"
        ? "/admin/deliberation/documents/" + encodeURIComponent(documentId) + "/graph-sessions/" + encodeURIComponent(session.id) + "/messages"
        : "/admin/deliberation/sessions/" + encodeURIComponent(session.id) + "/messages";
      // セッションキャッシュにも積む（タブ往復で表示上の会話が消えないように）。
      session.messages = session.messages || [];
      var userMessage = { role: "user", content: content };
      session.messages.push(userMessage);
      if (isCurrentContext(mode, nodeKey)) {
        state.chatMessages.push(userMessage);
        renderChatLog();
      }
      // 入力欄から送った分だけを消す（音声経路や入力し直しの途中文字を消さない）。
      if (input && input.value.trim() === content) input.value = "";
      deps.apiFetch(path, { method: "POST", body: JSON.stringify({ content: content }) })
        .then(function (res) {
          if (res.ok) return res.json();
          var httpStatus = res.status;
          return res.json().then(function (body) {
            var error = new Error((body && typeof body.detail === "string" && body.detail) || "応答の取得に失敗しました");
            error.httpStatus = httpStatus;
            throw error;
          }, function () {
            var error = new Error("応答の取得に失敗しました");
            error.httpStatus = httpStatus;
            throw error;
          });
        })
        .then(function (data) {
          var replyMessage = { role: "assistant", content: data.reply || "" };
          session.messages.push(replyMessage);
          // 表示コンテキストが変わっていたら書き戻しだけで終える（再描画しない）。
          if (isCurrentContext(mode, nodeKey)) {
            state.chatMessages.push(replyMessage);
            state.chatAnnotations = (data.annotations || []).concat(state.chatAnnotations);
            renderChatLog();
            renderChatAnnotations();
            setStatus("graph-review-chat-status", data.degraded ? "AI 応答を生成できなかったため縮退応答を表示しています。" : "", data.degraded ? "info" : "");
          }
          finish(null, replyMessage.content);
        })
        .catch(function (err) {
          setStatus("graph-review-chat-status", (err && err.message) || "応答の取得に失敗しました", "error");
          // グラフ全体対話が上限に達したときのみ、新しい対話を始める手段を出す
          // （ノード対話は別ノードを選べば別セッションになるため出さない）。
          if (err && err.httpStatus === 429 && mode === "graph" && isCurrentContext(mode, nodeKey)) {
            renderNewChatButton();
          }
          finish(err || new Error("応答の取得に失敗しました"));
        })
        .finally(function () {
          state.chatBusy = false;
        });
    });
  }

  // -------------------------------------------------------------------------
  // ハンズフリー音声対話（エンジンは AdminVoiceChat。ここは配線のみ）
  // 音声からは対話しか行わない。承認・却下は教員のボタン操作だけ（GR1）。
  // -------------------------------------------------------------------------

  function setVoiceStatus(kind, label) {
    var el = document.getElementById("graph-review-voice-status");
    if (!el) return;
    el.textContent = label || "";
    el.className = "graph-review-voice-status" + (kind && kind !== "off" ? " is-" + kind : "");
  }

  function updateVoiceButton() {
    var btn = document.getElementById("graph-review-voice-btn");
    if (!btn) return;
    var active = !!(state.voiceLoop && state.voiceLoop.isActive());
    btn.textContent = active ? "⏹ 音声停止" : "🎤 音声";
    btn.className = "admin-action-btn graph-review-voice-btn" + (active ? " is-active" : "");
  }

  function toggleVoice() {
    if (state.voiceLoop && state.voiceLoop.isActive()) {
      stopVoice("音声対話を終了しました。");
      return;
    }
    startVoice();
  }

  function startVoice() {
    if (!window.AdminVoiceChat || !window.AdminVoiceChat.createLoop) {
      setVoiceStatus("error", "音声対話モジュールを読み込めていないため開始できません。ページを再読み込みしてください。");
      return;
    }
    state.voiceLoop = window.AdminVoiceChat.createLoop({
      transcribe: voiceTranscribe,
      speak: voiceSpeak,
      onUtterance: voiceUtterance,
      onStatus: function (kind, label) {
        setVoiceStatus(kind, label);
        updateVoiceButton();
      },
      onStopped: function () {
        state.voiceLoop = null;
        updateVoiceButton();
      },
    });
    state.voiceLoop.start();
    updateVoiceButton();
  }

  function stopVoice(message) {
    if (state.voicePlayer) {
      try { state.voicePlayer.pause(); } catch (e) { /* noop */ }
      state.voicePlayer = null;
    }
    if (state.voiceLoop) {
      var loop = state.voiceLoop;
      state.voiceLoop = null;
      try { loop.stop(); } catch (e) { /* noop */ }
    }
    updateVoiceButton();
    setVoiceStatus(message ? "error" : "off", message || "");
  }

  // 発話 → 既存のテキスト送信経路（表示・セッション・上限の扱いはテキストと同一）。
  function voiceUtterance(text, done) {
    sendChatText(text, function (err, reply) {
      if (err && err.httpStatus === 429) {
        // 上限に達したら回し続けない（事実文だけを残して終了する）。
        stopVoice("利用の上限に達したため音声対話を終了しました。テキストで続けるか、新しい対話を開始してください。");
        return;
      }
      done(err, reply);
    });
  }

  // multipart は JSON 前提の apiFetch を通せないため、素の fetch で送る。
  function voiceTranscribe(blob, done) {
    var token = deps.getToken ? deps.getToken() : null;
    var headers = {};
    if (token) headers["Authorization"] = "Bearer " + token;
    var ext = String(blob && blob.type || "").indexOf("mp4") >= 0 ? "mp4" : "webm";
    var form = new FormData();
    form.append("audio", blob, "speech." + ext);
    fetch("/api/admin/deliberation/voice/transcribe?language=ja", {
      method: "POST",
      headers: headers,
      body: form,
    })
      .then(function (res) {
        if (res.ok) return res.json();
        var error = new Error("音声を文字起こしできませんでした");
        error.httpStatus = res.status;
        throw error;
      })
      .then(function (data) { done(null, (data && data.text) || ""); })
      .catch(function (err) { done(err || new Error("音声を文字起こしできませんでした")); });
  }

  function voiceSpeak(text, done) {
    var called = false;
    function finish(err, played) {
      if (called) return;
      called = true;
      state.voicePlayer = null;
      done(err || null, !!played);
    }
    deps.apiFetch("/admin/deliberation/voice/speak", {
      method: "POST",
      body: JSON.stringify({ text: text }),
    })
      .then(function (res) {
        if (res.ok) return res.json();
        var error = new Error("音声を再生できませんでした");
        error.httpStatus = res.status;
        throw error;
      })
      .then(function (data) {
        var audioB64 = (data && data.audio_base64) || "";
        if (!audioB64) { finish(null, false); return; }
        var player = new Audio("data:audio/mp3;base64," + audioB64);
        state.voicePlayer = player;
        player.onended = function () { finish(null, true); };
        player.onerror = function () { finish(null, false); };
        var playing = player.play();
        if (playing && playing.catch) {
          playing.catch(function () { finish(null, false); });
        }
      })
      .catch(function (err) { finish(err || new Error("音声を再生できませんでした"), false); });
  }

  // -------------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------------

  window.GraphReview = {
    init: function (options) {
      deps.apiFetch = options.apiFetch;
      deps.escHtml = options.escHtml;
      // multipart（音声の文字起こし）だけは素の fetch を使うため、認証トークンの
      // 取得関数を受け取る（トークンの保持は admin.js 側の責務のまま）。
      deps.getToken = options.getToken || null;
    },
    open: open,
    close: close,
  };
})();
