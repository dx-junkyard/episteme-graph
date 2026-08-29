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
 */
(function () {
  "use strict";

  var deps = { apiFetch: null, escHtml: null };

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
    preserveViewOnce: false, // 次の再描画でズーム・パンを維持する（fit しない）
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

  function esc(text) {
    return deps.escHtml ? deps.escHtml(text == null ? "" : String(text)) : String(text == null ? "" : text);
  }

  function gv() {
    return (window.LectureStudio && window.LectureStudio.graphView) || null;
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
          '<div class="graph-review-title">🕸 グラフレビュー — <span id="graph-review-title-text"></span></div>' +
          '<div class="graph-review-toolbar">' +
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
            '<div id="graph-review-network" class="graph-review-network" tabindex="0"></div>' +
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
              '</div>' +
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
    modal.querySelector("#graph-review-next-unreviewed").addEventListener("click", gotoNextUnreviewed);
    modal.querySelector("#graph-review-chat-tab-graph").addEventListener("click", function () {
      switchChatMode("graph");
    });
    modal.querySelector("#graph-review-chat-tab-node").addEventListener("click", function () {
      if (state.selectedNodeId) switchChatMode("node");
    });
    modal.querySelector("#graph-review-chat-send").addEventListener("click", sendChat);
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
    var modal = ensureModal();
    modal.hidden = false;
    document.getElementById("graph-review-title-text").textContent = state.title;
    document.getElementById("graph-review-unreviewed-toggle").checked = false;
    setStatus("graph-review-graph-status", "グラフを読み込み中...", "info");
    renderChatShell();
    loadGraph();
  }

  function close() {
    var modal = document.getElementById("graph-review-modal");
    if (modal) modal.hidden = true;
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

  // -------------------------------------------------------------------------
  // 描画
  // -------------------------------------------------------------------------

  function render() {
    renderLayerToolbar();
    renderUnreviewedCount();
    renderNetwork();
    renderDetail();
    renderChatShell();
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
      return {
        agent_id: agentId,
        claim_id: resolved ? resolved.claim_id : "",
        text: resolved ? resolved.text : "",
        review_status: resolved ? (resolved.review_status || "teacher_review_required") : "",
      };
    });
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
        ? '<div class="graph-review-detail-desc">' + esc(node.description) + "</div>"
        : "") +
      (reasons.length
        ? '<div class="graph-review-detail-reasons">要確認の理由: ' + esc(reasons.join(" / ")) + "</div>"
        : "");

    // component 承認・却下（server が最終判定。GR1: 教員の明示操作のみ）。
    // 集約 main ノード（DB UUID でない graph-native ID）は theory_components の行を
    // 持たないため、承認・却下ボタン自体を出さない。
    var reviewable = isDbUuid(nodeId);
    if (reviewable) {
      var approveDisabled = isApproved(status) ? " disabled" : "";
      var rejectDisabled = isRejected(status) ? " disabled" : "";
      html += '<div class="graph-review-detail-actions">' +
        '<button type="button" class="admin-action-btn" data-graph-review-action="approve" data-ui-anchor="graph-review.approve"' + approveDisabled + ">承認</button>" +
        '<button type="button" class="admin-action-btn" data-graph-review-action="reject" data-ui-anchor="graph-review.reject"' + rejectDisabled + ">却下</button>" +
        '<button type="button" class="admin-action-btn" data-graph-review-action="deliberate" data-ui-anchor="graph-review.open-deliberation">深く検討</button>' +
        "</div>";
    } else {
      html += '<div class="graph-review-empty">このノードは複数の要素を集約した表示用ノードのため、承認・却下は集約元の各要素（式の詳細層のノードや根拠 claim）で行います。</div>' +
        '<div class="graph-review-detail-actions">' +
        '<button type="button" class="admin-action-btn" data-graph-review-action="deliberate" data-ui-anchor="graph-review.open-deliberation">深く検討</button>' +
        "</div>";
    }
    html += '<div id="graph-review-detail-status" class="graph-review-status"></div>';

    if (claims.length) {
      html += '<div class="graph-review-claims"><div class="graph-review-claims-title">根拠 claim</div>' +
        claims.map(function (claim) {
          var label = claim.claim_id ? reviewStatusLabel(claim.review_status) : "未解決";
          var approved = isApproved(claim.review_status);
          var button = claim.claim_id
            ? '<button type="button" class="admin-action-btn graph-review-claim-approve" data-graph-review-claim="' +
              esc(claim.claim_id) + '" data-ui-anchor="graph-review.claim-approve"' + (approved ? " disabled" : "") + ">承認</button>"
            : "";
          // 未解決の claim でも内部 ID（agent_id）は教員 UI に出さない。
          var claimText = String(claim.text || "").trim()
            ? esc(claim.text)
            : "未解決の根拠（本文を取得できません）";
          return '<div class="graph-review-claim-row">' +
            '<div class="graph-review-claim-text">' + claimText + "</div>" +
            '<div class="graph-review-claim-meta"><span class="graph-review-chip">' + esc(label) + "</span>" + button + "</div>" +
            "</div>";
        }).join("") +
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
    window.Deliberation.openElement("theory_component", g.nodeId(node), {
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
    var componentId = gv().nodeId(node);
    deps.apiFetch("/admin/deliberation/sessions", {
      method: "POST",
      body: JSON.stringify({
        scope: "document",
        element_type: "theory_component",
        element_id: componentId,
        document_id: state.documentId,
        title: gv().detailHeading(node, componentId),
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
    if (state.chatBusy) return;
    var input = document.getElementById("graph-review-chat-input");
    var content = (input && input.value || "").trim();
    if (!content) return;
    // 送信時点の表示コンテキストを固定する（応答到着までに切り替わっても混線させない）。
    var mode = state.chatMode;
    var nodeKey = state.selectedNodeId;
    var documentId = state.documentId;
    if (mode === "node" && !nodeKey) {
      setStatus("graph-review-chat-status", "ノードが選択されていません。", "error");
      return;
    }
    state.chatBusy = true;
    setStatus("graph-review-chat-status", "AI が応答を作成中...", "info");
    ensureSession(mode, nodeKey, function (session) {
      if (!session) { state.chatBusy = false; return; }
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
      if (input) input.value = "";
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
          if (!isCurrentContext(mode, nodeKey)) return;
          state.chatMessages.push(replyMessage);
          state.chatAnnotations = (data.annotations || []).concat(state.chatAnnotations);
          renderChatLog();
          renderChatAnnotations();
          setStatus("graph-review-chat-status", data.degraded ? "AI 応答を生成できなかったため縮退応答を表示しています。" : "", data.degraded ? "info" : "");
        })
        .catch(function (err) {
          setStatus("graph-review-chat-status", (err && err.message) || "応答の取得に失敗しました", "error");
          // グラフ全体対話が上限に達したときのみ、新しい対話を始める手段を出す
          // （ノード対話は別ノードを選べば別セッションになるため出さない）。
          if (err && err.httpStatus === 429 && mode === "graph" && isCurrentContext(mode, nodeKey)) {
            renderNewChatButton();
          }
        })
        .finally(function () {
          state.chatBusy = false;
        });
    });
  }

  // -------------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------------

  window.GraphReview = {
    init: function (options) {
      deps.apiFetch = options.apiFetch;
      deps.escHtml = options.escHtml;
    },
    open: open,
    close: close,
  };
})();
