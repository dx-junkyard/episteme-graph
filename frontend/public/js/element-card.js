/*
 * 統一パーツカード。docs/architecture/admin_ux_issues_2026-08-01.md §3.2 / §3.3 Phase 1。
 *
 * 方針（§3.0）:
 *   P2 パーツ1個の描き方は出現箇所によらず同一。
 *   P3 どこに出してもグラフ近傍が辿れる（近傍チップ → 中心移動）。
 *   P4 差異は編集権限の有無だけ。editable（教員・管理者）/ readonly（学習者）の2バリアント。
 *
 * 入力契約は W層 context_lens の DTO 形に固定する（§3.3 Phase 1）。
 * **カードは独自の取得をしない** — DTO は呼び出し側が渡す。
 *
 *   dto = {focus, upper: [ITEM], lower: [ITEM], notes: [str]}
 *   focus = {element_type, element_id, document_id, label, intrinsic_summary,
 *            contextual_role, contextual_role_status, generic}
 *   ITEM  = {element_type, element_id, document_id, label, relation, relation_label,
 *            relation_status, evidence_refs, navigable}
 *
 * 学習者向け投影（core/element_context.py）は ITEM のキーが element_id ではなく
 * id で、relation / evidence_refs / focus.provenance を落としている。
 * 本カードは両方の形を受ける（element_id → id の順に見る）。
 * dto.available === false の fail-soft 形（{available:false, note}）もそのまま
 * 渡してよい（事実文カードに縮退する）。
 *
 * 表示しないもの:
 *   - focus.provenance（theory_claims:<uuid> 等の内部 ID 列）。出所は段階ラベルの
 *     バッジ（opts.metaBadges / contextual_role_status）で表す（§3.4(c)）。
 *   - confidence の生数値（W8）。カードは confidence を一切読まない。
 *   - readonly では relation_status === "candidate" の近傍・操作行・要確認事項。
 *
 * ES5 / IIFE。window.ElementCard を公開。DOM 生成と fetch は分離してあり、
 * render() は HTML 文字列を返すだけなので呼び出し側の innerHTML 合成に混ぜられる。
 *
 * 公開 API:
 *   ElementCard.render(dto, opts) -> HTML 文字列
 *   ElementCard.bind(containerEl, dto, opts) -> void（render 済み DOM にイベントを張る）
 *   ElementCard.mount(containerEl, dto, opts) -> void（render + bind）
 *   ElementCard.VARIANT_EDITABLE / VARIANT_READONLY
 */
(function (global) {
  "use strict";

  var VARIANT_EDITABLE = "editable";
  var VARIANT_READONLY = "readonly";

  var STATUS_CANDIDATE = "candidate";
  var STATUS_UNIDENTIFIED = "unidentified";

  var ELEMENT_TYPE_EQUATION = "equation";

  // レーン見出しは「上位＝この要素が支えるもの / 下位＝この要素を支えるもの」。
  // context_lens の DTO 定義（上位構造=Why / 下位構造=How）に合わせた文言で、
  // 既存の原稿スタジオ根拠リンク（lsEvidenceContextLaneHtml）と同一。
  var LANE_UPPER = "upper";
  var LANE_LOWER = "lower";
  var LANE_TITLES = {
    upper: "↑ 上位（この要素が支えるもの）",
    lower: "↓ 下位（この要素を支えるもの）"
  };
  var LANE_EMPTY_FACTS = {
    upper: "この要素が支える上位の構造は、まだ同定されていません。",
    lower: "この要素を支える下位の構造は見つかりませんでした。"
  };

  var FACT_NO_BODY = "説明文はありません。";
  var FACT_NO_CONTEXT = "この要素の文脈情報はまだありません。";
  var FACT_UNRESOLVED_ITEM = "参照先の名称を解決できませんでした（ID のまま表示しています）";
  var ROLE_LABEL = "この文脈での役割";
  var GENERIC_PREFIX = "一般には: ";
  var DELIBERATE_LABEL = "深く検討";

  // ---------------------------------------------------------------------------
  // 小道具
  // ---------------------------------------------------------------------------

  function defaultEscapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function vocabElementTypeLabel(elementType) {
    var vocab = global.ElementVocab;
    if (vocab && vocab.elementTypeLabel) return vocab.elementTypeLabel(elementType);
    return String(elementType == null ? "" : elementType);
  }

  function vocabStatusLabel(status) {
    var vocab = global.ElementVocab;
    if (vocab && vocab.statusLabel) return vocab.statusLabel(status);
    return "";
  }

  function isPlainObject(value) {
    return !!value && typeof value === "object" && !(value instanceof Array);
  }

  function asArray(value) {
    return value instanceof Array ? value : [];
  }

  function textOf(value) {
    return String(value == null ? "" : value).trim();
  }

  // ITEM の ID。W層は element_id、学習者投影（element_context.py）は id。
  function itemId(item) {
    if (!isPlainObject(item)) return "";
    return textOf(item.element_id) || textOf(item.id);
  }

  // opts を正規化する。variant は必須だが、未指定・未知の値は **readonly** に
  // fail-closed する（候補・操作行・要確認事項を出さない側が安全）。
  function context(opts) {
    opts = isPlainObject(opts) ? opts : {};
    var variant = opts.variant === VARIANT_EDITABLE ? VARIANT_EDITABLE : VARIANT_READONLY;
    return {
      variant: variant,
      editable: variant === VARIANT_EDITABLE,
      readonly: variant === VARIANT_READONLY,
      esc: typeof opts.escapeHtml === "function" ? opts.escapeHtml : defaultEscapeHtml,
      renderMath: typeof opts.renderMath === "function" ? opts.renderMath : null,
      onCenter: typeof opts.onCenter === "function" ? opts.onCenter : null,
      onDeliberate: typeof opts.onDeliberate === "function" ? opts.onDeliberate : null,
      deliberateLabel: textOf(opts.deliberateLabel) || DELIBERATE_LABEL,
      deliberateAnchor: textOf(opts.deliberateAnchor),
      extraActions: asArray(opts.extraActions),
      // ITEM 行ごとの操作（例: 原稿スタジオの「この根拠リンク内で見る」）。
      // {label, onClick(item), when?(item), className?, anchor?} の配列。
      itemActions: asArray(opts.itemActions),
      // レーン見出しの上書き（焦点の種別で主語を変えたい呼び出し側向け）。
      laneTitles: isPlainObject(opts.laneTitles) ? opts.laneTitles : {},
      // 外殻（呼び出し画面のカード等）が既に種別チップ・タイトル・要約を表示している
      // ときの重複回避。hideHead は headerNote も含めて頭全体を出さない。
      hideHead: !!opts.hideHead,
      hideBody: !!opts.hideBody,
      // 「この文脈での役割」見出しの上書き（document スコープの画面が
      // 「この論文での役割」等のより正確な主語を使うため）。
      roleLabel: textOf(opts.roleLabel),
      headerNote: textOf(opts.headerNote),
      metaBadges: asArray(opts.metaBadges),
      reviewNotes: asArray(opts.reviewNotes),
      className: textOf(opts.className),
      cardId: textOf(opts.cardId)
    };
  }

  function focusOf(dto) {
    return isPlainObject(dto) && isPlainObject(dto.focus) ? dto.focus : {};
  }

  // レーンの可視 ITEM。render と bind が同じ関数を通ることで、
  // data 属性のインデックスとハンドラに渡す ITEM が必ず一致する。
  function visibleItems(dto, lane, ctx) {
    var raw = isPlainObject(dto) ? asArray(dto[lane]) : [];
    var out = [];
    for (var i = 0; i < raw.length; i++) {
      var item = raw[i];
      if (!isPlainObject(item)) continue;
      // readonly の二重ガード: AI 候補の関係は描かない。サーバ側
      // （core/element_context.py）でも除去されるが、カード側でも防ぐ。
      if (ctx.readonly && textOf(item.relation_status) === STATUS_CANDIDATE) continue;
      out.push(item);
    }
    return out;
  }

  // ---------------------------------------------------------------------------
  // 部品ごとの HTML
  // ---------------------------------------------------------------------------

  function statusBadgeHtml(status, ctx, fallbackLabel) {
    var raw = textOf(status);
    var label = vocabStatusLabel(raw) || textOf(fallbackLabel);
    if (!label) return "";
    var cls = "element-card-status";
    if (raw) cls += " element-card-status-" + ctx.esc(raw);
    return '<span class="' + cls + '">' + ctx.esc(label) + "</span>";
  }

  function headHtml(focus, ctx) {
    var typeLabel = textOf(vocabElementTypeLabel(focus.element_type));
    var title = textOf(focus.label);
    // 見出しと本文が同じ文字列（equation は context_lens が両方に式本文を入れる）の
    // ときは見出し側を出さない（同じ内容の二重表示を防ぐ。本文側は数式レンダリングが
    // 効くため本文を残す）。
    if (title && title === textOf(focus.intrinsic_summary)) title = "";
    return '<div class="element-card-head">' +
      (typeLabel
        ? '<span class="element-card-kind">' + ctx.esc(typeLabel) + "</span>"
        : "") +
      (title
        ? '<span class="element-card-title">' + ctx.esc(title) + "</span>"
        : "") +
      (ctx.headerNote
        ? '<span class="element-card-head-note">' + ctx.esc(ctx.headerNote) + "</span>"
        : "") +
      "</div>";
  }

  // meta 行（§3.4(c) 出所バッジは上部）。呼び出し側が渡した段階ラベルのみを出す。
  // 数値・煽り文言は呼び出し側の責務で入れないこと。
  function metaHtml(ctx) {
    var parts = [];
    for (var i = 0; i < ctx.metaBadges.length; i++) {
      var badge = ctx.metaBadges[i];
      if (!isPlainObject(badge)) continue;
      var html = statusBadgeHtml(badge.status, ctx, badge.label);
      if (html) parts.push(html);
    }
    if (!parts.length) return "";
    return '<div class="element-card-meta">' + parts.join("") + "</div>";
  }

  function bodyHtml(focus, ctx) {
    var summary = textOf(focus.intrinsic_summary);
    if (!summary) {
      return '<div class="element-card-body element-card-fact">' +
        ctx.esc(FACT_NO_BODY) + "</div>";
    }
    if (textOf(focus.element_type) === ELEMENT_TYPE_EQUATION && ctx.renderMath) {
      // renderMath は呼び出し側の数式レンダラ（katex 等）。katex への直接依存は持たない。
      var rendered = "";
      try {
        rendered = ctx.renderMath(summary, true);
      } catch (e) {
        rendered = "";
      }
      if (rendered) {
        return '<div class="element-card-body element-card-formula">' + rendered + "</div>";
      }
    }
    return '<div class="element-card-body">' + ctx.esc(summary) + "</div>";
  }

  // この文脈での役割。unidentified / candidate 由来の空欄は語らない
  // （推測で穴埋めしない。readonly 側はサーバがキーごと落としている）。
  function roleHtml(focus, ctx) {
    var role = textOf(focus.contextual_role);
    var status = textOf(focus.contextual_role_status);
    if (!role || status === STATUS_UNIDENTIFIED) return "";
    if (ctx.readonly && status === STATUS_CANDIDATE) return "";
    return '<div class="element-card-role">' +
      '<span class="element-card-role-label">' + ctx.esc(ctx.roleLabel || ROLE_LABEL) + "</span>" +
      '<span class="element-card-role-value">' + ctx.esc(role) + "</span>" +
      statusBadgeHtml(status, ctx, "") +
      "</div>";
  }

  // 汎用（共通部品）側の説明。固有情報と混ぜず別行で出す（context_lens の設計 §2.1）。
  function genericHtml(focus, ctx) {
    var generic = isPlainObject(focus.generic) ? focus.generic : null;
    if (!generic) return "";
    var name = textOf(generic.name);
    var summary = textOf(generic.summary);
    if (!name && !summary) return "";
    var text = GENERIC_PREFIX + name + (summary ? " — " + summary : "");
    return '<div class="element-card-generic">' + ctx.esc(text) + "</div>";
  }

  // この ITEM に適用される itemActions の添字リスト（when() でフィルタ）。
  function itemActionIndexes(item, ctx) {
    var out = [];
    for (var i = 0; i < ctx.itemActions.length; i++) {
      var action = ctx.itemActions[i];
      if (!isPlainObject(action)) continue;
      if (!textOf(action.label) || typeof action.onClick !== "function") continue;
      if (typeof action.when === "function" && !action.when(item)) continue;
      out.push(i);
    }
    return out;
  }

  function itemHtml(item, lane, index, ctx) {
    var id = itemId(item);
    var navigable = !!item.navigable && !!id && !!ctx.onCenter;
    var label = textOf(item.label);
    // 種別チップとラベルが同じ文字列のときはチップを出さない（「中心命題 中心命題」を防ぐ）。
    var typeLabel = textOf(vocabElementTypeLabel(item.element_type));
    if (typeLabel === label) typeLabel = "";
    // 参照先の名称が解決できず ID のまま表示している ITEM は淡色 + ツールチップで明示する。
    var unresolvedCls = item.unresolved ? " element-card-item-unresolved" : "";
    var unresolvedAttr = item.unresolved
      ? ' title="' + ctx.esc(FACT_UNRESOLVED_ITEM) + '"'
      : "";
    var inner =
      (typeLabel
        ? '<span class="element-card-item-kind">' + ctx.esc(typeLabel) + "</span>"
        : "") +
      (textOf(item.relation_label)
        ? '<span class="element-card-item-relation">' +
          ctx.esc(textOf(item.relation_label)) + "</span>"
        : "") +
      '<span class="element-card-item-label">' + ctx.esc(label) + "</span>" +
      statusBadgeHtml(item.relation_status, ctx, "");
    var itemEl;
    if (!navigable) {
      itemEl = '<div class="element-card-item' + unresolvedCls + '"' + unresolvedAttr + ">" +
        inner + "</div>";
    } else {
      itemEl = '<button type="button" class="element-card-item element-card-item-navigable' +
        unresolvedCls + '"' + unresolvedAttr +
        ' data-element-card-item="' + ctx.esc(lane + ":" + index) + '">' +
        inner + "</button>";
    }
    // ITEM 行の追加操作。ネストした button を作らないため、操作は ITEM の隣に並べて
    // 行ラッパーで包む（操作が無い ITEM は従来どおりラッパーなし）。
    var actionIdx = itemActionIndexes(item, ctx);
    var refsHtml = itemRefsHtml(item, ctx);
    if (!actionIdx.length && !refsHtml) return itemEl;
    var rowEl = itemEl;
    if (actionIdx.length) {
      var actionParts = [];
      for (var a = 0; a < actionIdx.length; a++) {
        var action = ctx.itemActions[actionIdx[a]];
        actionParts.push('<button type="button" class="element-card-item-action' +
          (textOf(action.className) ? " " + ctx.esc(textOf(action.className)) : "") + '"' +
          (textOf(action.anchor) ? ' data-ui-anchor="' + ctx.esc(textOf(action.anchor)) + '"' : "") +
          ' data-element-card-itemaction="' + ctx.esc(lane + ":" + index + ":" + actionIdx[a]) + '">' +
          ctx.esc(textOf(action.label)) + "</button>");
      }
      rowEl = '<div class="element-card-item-row">' + itemEl + actionParts.join("") + "</div>";
    }
    if (!refsHtml) return rowEl;
    return '<div class="element-card-item-block">' + rowEl + refsHtml + "</div>";
  }

  // ITEM の根拠参照（evidence_refs = ev_0001 等の内部参照）。editable のみ・折りたたみで
  // 描く（学習者投影はサーバ側で evidence_refs を落とすが、カード側でも出さない）。
  // <details> は button の中に置けないため ITEM の下に隣接させ、ブロックで包む。
  function itemRefsHtml(item, ctx) {
    if (!ctx.editable) return "";
    var refs = asArray(item.evidence_refs);
    var parts = [];
    for (var i = 0; i < refs.length; i++) {
      var text = textOf(refs[i]);
      if (text) parts.push("<li>" + ctx.esc(text) + "</li>");
    }
    if (!parts.length) return "";
    return '<details class="element-card-item-refs"><summary>根拠</summary><ul>' +
      parts.join("") + "</ul></details>";
  }

  function laneHtml(dto, lane, ctx) {
    var items = visibleItems(dto, lane, ctx);
    var rows;
    if (!items.length) {
      rows = '<div class="element-card-fact">' + ctx.esc(LANE_EMPTY_FACTS[lane]) + "</div>";
    } else {
      var parts = [];
      for (var i = 0; i < items.length; i++) {
        parts.push(itemHtml(items[i], lane, i, ctx));
      }
      rows = '<div class="element-card-items">' + parts.join("") + "</div>";
    }
    var laneTitle = textOf(ctx.laneTitles[lane]) || LANE_TITLES[lane];
    return '<div class="element-card-lane element-card-lane-' + ctx.esc(lane) + '">' +
      '<div class="element-card-lane-title">' + ctx.esc(laneTitle) + "</div>" +
      rows +
      "</div>";
  }

  function lanesHtml(dto, ctx) {
    return '<div class="element-card-lanes">' +
      laneHtml(dto, LANE_UPPER, ctx) +
      laneHtml(dto, LANE_LOWER, ctx) +
      "</div>";
  }

  function factListHtml(values, cls, ctx) {
    var parts = [];
    for (var i = 0; i < values.length; i++) {
      var text = textOf(values[i]);
      if (text) parts.push("<li>" + ctx.esc(text) + "</li>");
    }
    if (!parts.length) return "";
    return '<ul class="' + cls + '">' + parts.join("") + "</ul>";
  }

  // notes（fail-soft の事実文）は editable のみ。学習者には内部事情
  // （参照解決の失敗・artifact 欠損等）を語らない。
  function notesHtml(dto, ctx) {
    if (!ctx.editable) return "";
    return factListHtml(isPlainObject(dto) ? asArray(dto.notes) : [], "element-card-notes", ctx);
  }

  // 要確認事項は editable のみ（§3.2 差分表）。事実文の配列を呼び出し側が渡す。
  function reviewHtml(ctx) {
    if (!ctx.editable) return "";
    return factListHtml(ctx.reviewNotes, "element-card-review", ctx);
  }

  function actionButtonHtml(label, index, anchor, className, ctx) {
    var cls = "element-card-action" + (className ? " " + className : "");
    return '<button type="button" class="' + ctx.esc(cls) + '"' +
      (anchor ? ' data-ui-anchor="' + ctx.esc(anchor) + '"' : "") +
      ' data-element-card-action="' + ctx.esc(String(index)) + '">' +
      ctx.esc(label) + "</button>";
  }

  // 操作行は editable のみ。ハンドラ未指定のボタンは出さない（fail-closed）。
  function actionsHtml(ctx) {
    if (!ctx.editable) return "";
    var parts = [];
    if (ctx.onDeliberate) {
      parts.push('<button type="button" class="element-card-action"' +
        (ctx.deliberateAnchor ? ' data-ui-anchor="' + ctx.esc(ctx.deliberateAnchor) + '"' : "") +
        ' data-element-card-deliberate="1">' + ctx.esc(ctx.deliberateLabel) + "</button>");
    }
    for (var i = 0; i < ctx.extraActions.length; i++) {
      var action = ctx.extraActions[i];
      if (!isPlainObject(action)) continue;
      var label = textOf(action.label);
      if (!label || typeof action.onClick !== "function") continue;
      parts.push(actionButtonHtml(
        label, i, textOf(action.anchor), textOf(action.className), ctx));
    }
    if (!parts.length) return "";
    return '<div class="element-card-actions">' + parts.join("") + "</div>";
  }

  // ---------------------------------------------------------------------------
  // 公開 API
  // ---------------------------------------------------------------------------

  function rootOpenTag(ctx) {
    var cls = "element-card element-card-" + ctx.variant + (ctx.className ? " " + ctx.className : "");
    return '<div class="' + ctx.esc(cls) + '" data-element-card="' + ctx.esc(ctx.cardId) + '">';
  }

  function render(dto, opts) {
    var ctx = context(opts);
    if (isPlainObject(dto) && dto.available === false) {
      // fail-soft 形（context API の {available:false, note}）。事実文だけ出す。
      return rootOpenTag(ctx) +
        '<div class="element-card-body element-card-fact">' +
        ctx.esc(textOf(dto.note) || FACT_NO_CONTEXT) + "</div></div>";
    }
    var focus = focusOf(dto);
    return rootOpenTag(ctx) +
      (ctx.hideHead ? "" : headHtml(focus, ctx)) +
      metaHtml(ctx) +
      (ctx.hideBody ? "" : bodyHtml(focus, ctx)) +
      roleHtml(focus, ctx) +
      genericHtml(focus, ctx) +
      lanesHtml(dto, ctx) +
      notesHtml(dto, ctx) +
      reviewHtml(ctx) +
      actionsHtml(ctx) +
      "</div>";
  }

  // bind の対象カード。containerEl 自身がカードでもよい。1つの container に複数枚
  // mount する呼び出し側は opts.cardId で区別する（近傍チップの index 衝突を防ぐ）。
  function rootElement(containerEl, ctx) {
    if (!containerEl || !containerEl.querySelector) return null;
    if (containerEl.classList && containerEl.classList.contains("element-card")) {
      return containerEl;
    }
    if (ctx.cardId) {
      var byId = containerEl.querySelector('.element-card[data-element-card="' + ctx.cardId + '"]');
      if (byId) return byId;
    }
    return containerEl.querySelector(".element-card");
  }

  function each(root, selector, fn) {
    var nodes = root.querySelectorAll(selector);
    for (var i = 0; i < nodes.length; i++) fn(nodes[i]);
  }

  function bind(containerEl, dto, opts) {
    var ctx = context(opts);
    var root = rootElement(containerEl, ctx);
    if (!root) return;
    var lanes = {};
    lanes[LANE_UPPER] = visibleItems(dto, LANE_UPPER, ctx);
    lanes[LANE_LOWER] = visibleItems(dto, LANE_LOWER, ctx);

    if (ctx.onCenter) {
      each(root, "[data-element-card-item]", function (btn) {
        btn.addEventListener("click", function (event) {
          event.stopPropagation();
          var ref = String(btn.getAttribute("data-element-card-item") || "").split(":");
          var items = lanes[ref[0]] || [];
          var item = items[parseInt(ref[1], 10)];
          if (item) ctx.onCenter(item);
        });
      });
    }
    if (ctx.editable && ctx.onDeliberate) {
      each(root, "[data-element-card-deliberate]", function (btn) {
        btn.addEventListener("click", function (event) {
          event.stopPropagation();
          ctx.onDeliberate(focusOf(dto));
        });
      });
    }
    if (ctx.editable) {
      each(root, "[data-element-card-action]", function (btn) {
        btn.addEventListener("click", function (event) {
          event.stopPropagation();
          var action = ctx.extraActions[parseInt(
            btn.getAttribute("data-element-card-action"), 10)];
          if (action && typeof action.onClick === "function") action.onClick(focusOf(dto));
        });
      });
    }
    if (ctx.itemActions.length) {
      each(root, "[data-element-card-itemaction]", function (btn) {
        btn.addEventListener("click", function (event) {
          event.stopPropagation();
          var ref = String(btn.getAttribute("data-element-card-itemaction") || "").split(":");
          var items = lanes[ref[0]] || [];
          var item = items[parseInt(ref[1], 10)];
          var action = ctx.itemActions[parseInt(ref[2], 10)];
          if (item && action && typeof action.onClick === "function") action.onClick(item);
        });
      });
    }
  }

  function mount(containerEl, dto, opts) {
    if (!containerEl) return;
    containerEl.innerHTML = render(dto, opts);
    bind(containerEl, dto, opts);
  }

  global.ElementCard = {
    VARIANT_EDITABLE: VARIANT_EDITABLE,
    VARIANT_READONLY: VARIANT_READONLY,
    render: render,
    bind: bind,
    mount: mount
  };
})(typeof window !== "undefined" ? window : this);
