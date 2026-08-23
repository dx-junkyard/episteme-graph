/* 分野の地図 — 骨格 draft のビジュアルプレビュー (skeleton editor upgrade P1/P2 → 段階表示改修)
 *
 * 管理画面の骨格レビューで、生の JSON textarea と並べて「学習者にはこう見える」を
 * 読み取り専用で描画する。学習者向け atlas-overlay.js (L1) の視覚言語
 * (verified/contested/assumed の色・破線・二重円) をそのまま流用し、教員が新しい
 * 見え方を覚えなくて済むようにする。
 *
 * ■ 段階表示 (drill-down) の設計方針
 *   全ての領域・概念・エッジを一度に描くと詰まって読めない。そこで 2 段階にする:
 *   - 概要 (overview): 領域を「カード」として並べるだけ。カード内には個々の概念円を
 *     置かず、概念数・seed_status の内訳ドット・概念名の要約文で「どんな要素があるか」を
 *     文章で示す。領域間の線は「強い関係」(depends 相当) だけに絞る (弱い・ほぼ全結合の
 *     線は描かない)。カードをクリックすると 2) に入る。
 *   - 領域フォーカス (focus): 選んだ領域の概念だけをキャンバス全体に広げて描く。概念を
 *     クリックすると seed_status と「何と結びついているか」(領域内 / 他領域) を詳細に出す。
 *   概要文はデータから機械生成する (prose フィールドは骨格に無い)。煽らず宣言せず、
 *   概念数・状態内訳・概念名の列挙のみ。
 *
 * P1/P2 の維持:
 *   - サーバーを経由しないクライアント内の座標変換のみ。
 *   - draft は学習者非公開なので、seed_status 未レビューの概念にも色を付ける。
 *   - validate_skeleton の error_issues / warning_issues を受け取り、該当ノードを
 *     赤 (error) / 橙 (warning) でハイライトする。概要では概念を畳むため、領域カードに
 *     配下 issue 件数のバッジを出して隠さない。フォーカス時は個別概念で強調する。
 *
 * 公開 API (不変):
 *   window.AtlasDraftPreview.render(container, skeleton, opts)
 *     opts : { errorIssues, warningIssues, onSelect, selectedId }
 *       - onSelect(detail) : detail = { kind:"region"|"concept", region_id, concept_id, label }
 */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var VW = 1000;
  var VH = 560;
  var PAD = 16; // viewBox 端の余白
  var R = 9; // 概念ノード半径 (フォーカス時)

  // ラベルのフォント上限・下限 (px, viewBox 単位)
  var LABEL_FONT_MAX = 13;
  var LABEL_FONT_COMFORT = 11;
  var LABEL_FONT_MIN = 9;

  // atlas-overlay.js §5 の視覚言語をそのまま複製する
  var C = {
    verifiedFill: "#E1F5EE", verifiedStroke: "#0F6E56",
    contestedFill: "#FAEEDA", contestedStroke: "#854F0B",
    assumedFill: "#FFFFFF", assumedStroke: "#BA7517",
    neutralFill: "var(--color-background-tertiary, #f0efe9)", neutralStroke: "#888780",
    regionStroke: "#B4B2A9", regionFill: "rgba(180,178,169,0.06)",
    cardFill: "#FBFAF6", cardHover: "rgba(55,138,221,0.06)",
    edge: "#B4B2A9",
    strongEdge: "#5F5E5A",
    errorStroke: "#e53935", warnStroke: "#e8a020",
    label: "var(--color-text-primary, #2b2a26)",
    sub: "var(--color-text-secondary, #63615c)",
    accent: "#378ADD",
  };

  // seed_status → 表示ラベル (学習者向け凡例と揃える)
  // 正本は backend/core/atlas_state.py::PILL_LABELS
  // （固定は backend/tests/test_atlas_vocab_mirror.py）。
  var SEED_LABEL = {
    verified: "原文に裏付け", contested: "解釈が分かれる", assumed: "暗黙の前提",
  };
  // エッジ種別 → 強さ重み / ラベル
  var KIND_WEIGHT = { depends: 3, related: 2, adjacent: 1 };
  var STRONG_THRESHOLD = 3; // これ以上の集約重みの領域ペアのみ概要で線を描く
  var STRONG_MAX = 6; // 概要で描く領域間リンクの上限本数

  // 段階表示のナビ状態 (モジュール内)。render 間で維持し、骨格が構造的に変わったら破棄。
  var nav = { sig: null, focus: null, sel: null, lastSelectedId: undefined };
  var lastArgs = null;

  // -------------------------------------------------------------------
  // DOM helpers
  // -------------------------------------------------------------------

  function svgEl(tag, attrs, text) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (var k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k) && attrs[k] != null) {
          node.setAttribute(k, attrs[k]);
        }
      }
    }
    if (text != null) node.textContent = text;
    return node;
  }

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k) && attrs[k] != null) {
          node.setAttribute(k, attrs[k]);
        }
      }
    }
    if (text != null) node.textContent = text;
    return node;
  }

  // 正規化 (0-1) → viewBox 座標。端に PAD の余白を残す。
  function nx(x) { return PAD + x * (VW - 2 * PAD); }
  function ny(y) { return PAD + y * (VH - 2 * PAD); }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  // --- ラベルの折り返し・自動縮小 --------------------------------------------
  function isSpace(ch) { return ch === " " || ch === "　"; }

  function estTextWidth(str, font) {
    var w = 0;
    for (var i = 0; i < str.length; i++) {
      if (isSpace(str[i])) { w += font * 0.32; continue; }
      w += str.charCodeAt(i) < 0x2e80 ? font * 0.56 : font * 1.0;
    }
    return w;
  }

  function tokenizeLabel(text) {
    text = String(text == null ? "" : text);
    var tokens = [];
    var buf = "";
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (/[0-9A-Za-z._+\-]/.test(ch)) { buf += ch; continue; }
      if (buf) { tokens.push(buf); buf = ""; }
      tokens.push(ch);
    }
    if (buf) tokens.push(buf);
    return tokens;
  }

  function greedyWrap(tokens, allowedWidth, font) {
    var lines = [];
    var cur = "";
    for (var i = 0; i < tokens.length; i++) {
      var tok = tokens[i];
      if (cur === "" && isSpace(tok)) continue;
      var cand = cur + tok;
      if (cur === "" || estTextWidth(cand, font) <= allowedWidth) {
        cur = cand;
      } else {
        lines.push(cur);
        cur = isSpace(tok) ? "" : tok;
      }
    }
    if (cur !== "") lines.push(cur);
    return lines.length ? lines : [""];
  }

  // ラベルを allowedWidth に収める (2 行→3 行と段階的に緩め、最後は … で省略)。
  function fitLabel(label, allowedWidth) {
    var tokens = tokenizeLabel(label);
    for (var f2 = LABEL_FONT_MAX; f2 >= LABEL_FONT_COMFORT; f2 -= 0.5) {
      var l2 = greedyWrap(tokens, allowedWidth, f2);
      if (l2.length <= 2) return { lines: l2, font: f2 };
    }
    for (var f3 = LABEL_FONT_MAX; f3 >= LABEL_FONT_MIN; f3 -= 0.5) {
      var l3 = greedyWrap(tokens, allowedWidth, f3);
      if (l3.length <= 3) return { lines: l3, font: f3 };
    }
    var wrapped = greedyWrap(tokens, allowedWidth, LABEL_FONT_MIN);
    var out = wrapped.slice(0, 3);
    if (wrapped.length > 3) {
      var last = out[2];
      while (last.length > 1 && estTextWidth(last + "…", LABEL_FONT_MIN) > allowedWidth) {
        last = last.slice(0, -1);
      }
      out[2] = last + "…";
    }
    return { lines: out, font: LABEL_FONT_MIN };
  }

  // 指定行数までに収める省略 (概要カードの概念名リスト用)。
  function wrapClamp(text, allowedWidth, font, maxLines) {
    var lines = greedyWrap(tokenizeLabel(text), allowedWidth, font);
    if (lines.length <= maxLines) return lines;
    var out = lines.slice(0, maxLines);
    var last = out[maxLines - 1];
    while (last.length > 1 && estTextWidth(last + "…", font) > allowedWidth) {
      last = last.slice(0, -1);
    }
    out[maxLines - 1] = last + "…";
    return out;
  }

  // -------------------------------------------------------------------
  // issue 索引 (P2)
  // -------------------------------------------------------------------

  function indexIssues(issues) {
    var idx = { regions: {}, concepts: {}, edges: {}, other: [] };
    (issues || []).forEach(function (it) {
      var msg = (it && it.message) || String(it || "");
      var placed = false;
      if (it && it.concept_id) {
        (idx.concepts[it.concept_id] = idx.concepts[it.concept_id] || []).push(msg);
        placed = true;
      }
      if (it && it.region_id) {
        (idx.regions[it.region_id] = idx.regions[it.region_id] || []).push(msg);
        placed = true;
      }
      if (it && it.edge && it.edge.length === 2) {
        var key = edgeKey(it.edge[0], it.edge[1]);
        (idx.edges[key] = idx.edges[key] || []).push(msg);
        it.edge.forEach(function (endpoint) {
          if (endpoint === it.region_id || endpoint === it.concept_id) return;
          (idx.regions[endpoint] = idx.regions[endpoint] || []).push(msg);
          (idx.concepts[endpoint] = idx.concepts[endpoint] || []).push(msg);
        });
        placed = true;
      }
      if (!placed) idx.other.push(msg);
    });
    return idx;
  }

  function edgeKey(a, b) { return a < b ? a + "→" + b : b + "→" + a; }

  function edgeHasIssue(idx, edge) { return !!idx.edges[edgeKey(edge.from, edge.to)]; }

  function seedStroke(seed, hasError, hasWarn) {
    if (hasError) return C.errorStroke;
    if (hasWarn) return C.warnStroke;
    if (seed === "verified") return C.verifiedStroke;
    if (seed === "contested") return C.contestedStroke;
    if (seed === "assumed") return C.assumedStroke;
    return C.neutralStroke;
  }

  function seedFill(seed) {
    if (seed === "verified") return C.verifiedFill;
    if (seed === "contested") return C.contestedFill;
    if (seed === "assumed") return C.assumedFill;
    return C.neutralFill;
  }

  // -------------------------------------------------------------------
  // 骨格の索引・派生量
  // -------------------------------------------------------------------

  function buildIndex(skeleton) {
    var ix = {
      regionById: {}, conceptById: {}, conceptRegion: {},
      edgesByEndpoint: {}, // id → [{edge, isFrom}]
      pairs: {}, // "ra|rb" → { a, b, w }
    };
    (skeleton.regions || []).forEach(function (r) {
      ix.regionById[r.id] = r;
      (r.concepts || []).forEach(function (c) {
        ix.conceptById[c.id] = c;
        ix.conceptRegion[c.id] = r.id;
      });
    });
    (skeleton.edges || []).forEach(function (e) {
      (ix.edgesByEndpoint[e.from] = ix.edgesByEndpoint[e.from] || []).push({ edge: e, isFrom: true });
      (ix.edgesByEndpoint[e.to] = ix.edgesByEndpoint[e.to] || []).push({ edge: e, isFrom: false });
      // 領域ペアへ集約 (領域直結 or 他領域の概念どうし)
      var ra = ix.regionById[e.from] ? e.from : ix.conceptRegion[e.from];
      var rb = ix.regionById[e.to] ? e.to : ix.conceptRegion[e.to];
      if (!ra || !rb || ra === rb) return;
      var key = ra < rb ? ra + "|" + rb : rb + "|" + ra;
      var p = ix.pairs[key] || (ix.pairs[key] = { a: ra < rb ? ra : rb, b: ra < rb ? rb : ra, w: 0 });
      p.w += KIND_WEIGHT[e.kind] || 1;
    });
    return ix;
  }

  // 概要で描く「強い」領域ペア (重み STRONG_THRESHOLD 以上、上位 STRONG_MAX 本)。
  function strongPairs(ix) {
    var arr = [];
    for (var k in ix.pairs) {
      if (Object.prototype.hasOwnProperty.call(ix.pairs, k) && ix.pairs[k].w >= STRONG_THRESHOLD) {
        arr.push(ix.pairs[k]);
      }
    }
    arr.sort(function (x, y) { return y.w - x.w; });
    return arr.slice(0, STRONG_MAX);
  }

  function seedOf(concept) {
    return concept && concept.seed_status ? concept.seed_status.value : null;
  }

  // 領域の seed_status 内訳
  function statusBreakdown(region) {
    var b = { verified: 0, contested: 0, assumed: 0, none: 0, total: 0 };
    (region.concepts || []).forEach(function (c) {
      var s = seedOf(c);
      if (s === "verified") b.verified++;
      else if (s === "contested") b.contested++;
      else if (s === "assumed") b.assumed++;
      else b.none++;
      b.total++;
    });
    return b;
  }

  // 領域配下 (領域自身 + 概念) の issue 件数
  function regionIssueCount(region, idx) {
    var n = (idx.regions[region.id] || []).length;
    (region.concepts || []).forEach(function (c) { n += (idx.concepts[c.id] || []).length; });
    return n;
  }

  // 領域の矩形 (layout 無しはグリッドへフォールバック)
  function regionRect(region, gridPos) {
    var lo = region.layout;
    if (lo) {
      return {
        x: nx(lo.x), y: ny(lo.y),
        w: Math.max(lo.w * (VW - 2 * PAD), 60),
        h: Math.max(lo.h * (VH - 2 * PAD), 40),
      };
    }
    return gridPos;
  }

  // -------------------------------------------------------------------
  // render
  // -------------------------------------------------------------------

  function render(container, skeleton, opts) {
    opts = opts || {};
    if (!container) return;
    lastArgs = { container: container, skeleton: skeleton, opts: opts };
    container.innerHTML = "";

    if (!skeleton || !Array.isArray(skeleton.regions)) {
      container.appendChild(el("div", { style: "color:var(--color-text-tertiary);font-size:12px;padding:10px" },
        "プレビューできる骨格データがありません（regions が空、または JSON が不正です）。"));
      return;
    }

    var ix = buildIndex(skeleton);
    var errIdx = indexIssues(opts.errorIssues);
    var warnIdx = indexIssues(opts.warningIssues);

    // ナビ状態: 骨格が構造的に変わったら破棄する
    var sig = (skeleton.regions || []).map(function (r) {
      return r.id + "[" + (r.concepts || []).map(function (c) { return c.id; }).join(",") + "]";
    }).join(";");
    if (sig !== nav.sig) { nav.sig = sig; nav.focus = null; nav.sel = null; }
    // フォーカス先が消えたら概要へ戻す
    if (nav.focus && !ix.regionById[nav.focus]) { nav.focus = null; nav.sel = null; }

    // 外部からの選択 (AI agent 連動) が新しく変わったときだけ同期する。
    // 同じ selectedId での再描画 (validation 更新など) では利用者のナビを上書きしない。
    if (opts.selectedId !== nav.lastSelectedId) {
      nav.lastSelectedId = opts.selectedId;
      if (opts.selectedId) {
        if (ix.conceptById[opts.selectedId]) {
          nav.focus = ix.conceptRegion[opts.selectedId];
          nav.sel = opts.selectedId;
        } else if (ix.regionById[opts.selectedId]) {
          nav.focus = opts.selectedId;
          nav.sel = null;
        }
      }
    }

    // draft 注記
    container.appendChild(el("div", {
      class: "atlas-preview-note",
      style: "font-size:11.5px;color:var(--color-text-secondary);background:var(--color-background-tertiary,#f0efe9);border-radius:4px;padding:6px 8px;margin-bottom:8px",
    }, "これは draft のプレビューです。学習者には表示されません（未レビューの seed_status も色付きで見えています）。"));

    // パンくず / 戻る導線
    container.appendChild(buildCrumb(skeleton, ix, opts));

    // キャンバス
    var svg = svgEl("svg", {
      width: "100%", viewBox: "0 0 " + VW + " " + VH,
      role: "img", "aria-label": nav.focus ? "領域の概念プレビュー" : "領域一覧プレビュー",
      style: "border:1px solid var(--color-border);border-radius:6px;background:var(--color-bg-secondary,#fff);display:block",
    });
    if (nav.focus) {
      renderFocus(svg, skeleton, ix, errIdx, warnIdx, opts);
    } else {
      renderOverview(svg, skeleton, ix, errIdx, warnIdx, opts);
    }
    container.appendChild(svg);

    // 詳細パネル
    var detail = el("div", {
      class: "atlas-preview-detail",
      style: "font-size:12px;margin-top:8px;min-height:20px;color:var(--color-text-secondary);line-height:1.55",
    });
    container.appendChild(detail);
    container._atlasPreviewDetail = detail;
    renderDetail(container, skeleton, ix, errIdx, warnIdx, opts);

    // どこにも紐づかない issue はまとめて下部に出す
    var orphan = errIdx.other.concat(warnIdx.other);
    if (orphan.length) {
      var ol = el("div", { style: "font-size:11.5px;margin-top:4px;color:var(--color-text-tertiary)" });
      ol.textContent = "全体: " + orphan.join(" / ");
      container.appendChild(ol);
    }
  }

  function rerender() {
    if (lastArgs) render(lastArgs.container, lastArgs.skeleton, lastArgs.opts);
  }

  // -------------------------------------------------------------------
  // パンくず
  // -------------------------------------------------------------------

  function buildCrumb(skeleton, ix, opts) {
    var bar = el("div", { style: "display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12px;min-height:24px" });
    if (!nav.focus) {
      var n = (skeleton.regions || []).length;
      bar.appendChild(el("span", { style: "color:var(--color-text-secondary)" },
        "分野全体（領域 " + n + " 件）— 領域をクリックすると中の概念と接続先が見られます"));
      return bar;
    }
    var back = el("button", {
      type: "button",
      style: "border:1px solid var(--color-border);background:var(--color-bg-secondary,#fff);border-radius:4px;padding:2px 10px;cursor:pointer;font-size:12px;color:var(--color-text-primary)",
    }, "← 分野全体");
    back.addEventListener("click", function () {
      nav.focus = null; nav.sel = null;
      nav.lastSelectedId = opts.selectedId; // 戻った後の再描画で再ドリルしない
      rerender();
    });
    bar.appendChild(back);
    var region = ix.regionById[nav.focus];
    bar.appendChild(el("span", { style: "color:var(--color-text-tertiary)" }, "›"));
    bar.appendChild(el("span", { style: "font-weight:600;color:var(--color-text-primary)" },
      (region && region.label) || nav.focus));
    return bar;
  }

  // -------------------------------------------------------------------
  // 概要 (領域カード)
  // -------------------------------------------------------------------

  function renderOverview(svg, skeleton, ix, errIdx, warnIdx, opts) {
    var regions = skeleton.regions || [];

    // layout 無しの領域はグリッドへ
    var cols = Math.ceil(Math.sqrt(regions.length)) || 1;
    var rows = Math.ceil(regions.length / cols) || 1;
    var gw = (VW - 2 * PAD) / cols, gh = (VH - 2 * PAD) / rows;

    var rects = {};
    regions.forEach(function (region, i) {
      var gp = {
        x: PAD + (i % cols) * gw + 8, y: PAD + Math.floor(i / cols) * gh + 8,
        w: gw - 16, h: gh - 16,
      };
      rects[region.id] = regionRect(region, gp);
    });

    // 強い領域リンク (カードの下に敷く)
    strongPairs(ix).forEach(function (p) {
      var a = rects[p.a], b = rects[p.b];
      if (!a || !b) return;
      var ca = { x: a.x + a.w / 2, y: a.y + a.h / 2 };
      var cb = { x: b.x + b.w / 2, y: b.y + b.h / 2 };
      svg.appendChild(svgEl("line", {
        x1: ca.x, y1: ca.y, x2: cb.x, y2: cb.y,
        stroke: C.strongEdge, "stroke-width": clamp(p.w / 3, 1, 2.5), opacity: 0.4,
      }));
    });

    regions.forEach(function (region) {
      svg.appendChild(overviewCard(region, rects[region.id], ix, errIdx, warnIdx, opts));
    });
  }

  function overviewCard(region, rect, ix, errIdx, warnIdx, opts) {
    var g = svgEl("g", { class: "atlas-preview-region", "data-region": region.id, tabindex: "0", role: "button", style: "cursor:pointer" });
    var rErr = (errIdx.regions[region.id] || []);
    var rWarn = (warnIdx.regions[region.id] || []);
    var issueN = regionIssueCount(region, errIdx) + regionIssueCount(region, warnIdx);
    var childErr = regionIssueCount(region, errIdx) > 0;
    var childWarn = regionIssueCount(region, warnIdx) > 0;
    var border = childErr ? C.errorStroke : (childWarn ? C.warnStroke : C.regionStroke);
    var sw = (childErr || childWarn) ? 2 : 1;
    var selected = opts.selectedId === region.id;

    if (selected) {
      g.appendChild(svgEl("rect", {
        x: rect.x - 3, y: rect.y - 3, width: rect.w + 6, height: rect.h + 6, rx: 14,
        fill: "none", stroke: C.accent, "stroke-width": 1.5,
      }));
    }
    g.appendChild(svgEl("rect", {
      x: rect.x, y: rect.y, width: rect.w, height: rect.h, rx: 12,
      fill: C.cardFill, stroke: border, "stroke-width": sw,
      "stroke-dasharray": (childErr || childWarn) ? "6 3" : null,
    }));

    var pad = 12;
    var innerW = rect.w - 2 * pad;
    var y = rect.y + 20;

    // ヘッダ (領域名) — 最大 2 行
    var head = fitLabel(region.label || region.id, innerW - 18);
    var headFont = Math.min(head.font, 13);
    head.lines.slice(0, 2).forEach(function (line, li) {
      g.appendChild(svgEl("text", {
        x: rect.x + pad, y: y + li * (headFont + 3),
        style: "font-size:" + headFont + "px;font-weight:600;fill:" + C.label,
      }, line));
    });
    y += (Math.min(head.lines.length, 2) - 1) * (headFont + 3);

    // ドリルインを示す ›
    g.appendChild(svgEl("text", {
      x: rect.x + rect.w - pad, y: rect.y + 20, "text-anchor": "end",
      style: "font-size:16px;fill:" + C.sub,
    }, "›"));

    // issue バッジ (概念を畳んでも配下 issue を隠さない)
    if (issueN > 0) {
      g.appendChild(svgEl("text", {
        x: rect.x + rect.w - pad, y: rect.y + rect.h - 8, "text-anchor": "end",
        style: "font-size:10.5px;fill:" + (childErr ? C.errorStroke : C.warnStroke),
      }, "⚠ " + issueN + " 件"));
    }

    var b = statusBreakdown(region);

    // 状態ドット (概念 1 個 = 1 ドット、上限 14。超過は +N)
    y += headFont + 8;
    if (b.total > 0) {
      var dotY = y;
      var dotX = rect.x + pad + 4;
      var shown = 0, maxDots = 14;
      var order = [];
      (region.concepts || []).forEach(function (c) { order.push(seedOf(c)); });
      for (var di = 0; di < order.length && shown < maxDots; di++) {
        var s = order[di];
        g.appendChild(svgEl("circle", {
          cx: dotX, cy: dotY, r: 3.6, fill: seedFill(s), stroke: seedStroke(s), "stroke-width": 1,
        }));
        dotX += 10;
        shown++;
      }
      if (b.total > maxDots) {
        g.appendChild(svgEl("text", { x: dotX + 1, y: dotY + 3.5, style: "font-size:10px;fill:" + C.sub }, "+" + (b.total - maxDots)));
      }
      y += 14;
    }

    // 概念数 + 内訳文
    var parts = [];
    if (b.verified) parts.push("確認 " + b.verified);
    if (b.contested) parts.push("解釈 " + b.contested);
    if (b.assumed) parts.push("前提 " + b.assumed);
    if (b.none) parts.push("未設定 " + b.none);
    var countLine = "概念 " + b.total + " 件" + (parts.length ? "（" + parts.join(" / ") + "）" : "");
    g.appendChild(svgEl("text", { x: rect.x + pad, y: y + 4, style: "font-size:11px;fill:" + C.sub }, countLine));
    y += 16;

    // 概念名の要約 (どんな要素があるか) — カード残り高で行数を決める
    var names = (region.concepts || []).map(function (c) { return c.label || c.id; });
    var nameText = names.length ? names.join(" ・ ") : "（概念は未登録）";
    var lineH = 14;
    var avail = Math.floor((rect.y + rect.h - pad - (y - 4)) / lineH);
    if (avail >= 1) {
      var nameLines = wrapClamp(nameText, innerW, 11, Math.min(avail, 3));
      nameLines.forEach(function (line, li) {
        g.appendChild(svgEl("text", { x: rect.x + pad, y: y + 4 + li * lineH, style: "font-size:11px;fill:" + C.label }, line));
      });
    }

    g.appendChild(svgEl("title", null,
      (region.label || region.id) + " — 概念 " + b.total + " 件" + (names.length ? "：" + names.join("、") : "")));

    bindActivate(g, function () {
      nav.focus = region.id; nav.sel = null;
      nav.lastSelectedId = opts.selectedId;
      fireSelect(opts, { kind: "region", region_id: region.id, concept_id: "", label: region.label || region.id });
      rerender();
    });
    return g;
  }

  // -------------------------------------------------------------------
  // 領域フォーカス (概念を広げて描く)
  // -------------------------------------------------------------------

  function renderFocus(svg, skeleton, ix, errIdx, warnIdx, opts) {
    var region = ix.regionById[nav.focus];
    if (!region) return;
    var concepts = region.concepts || [];

    // 空領域
    if (!concepts.length) {
      svg.appendChild(svgEl("text", {
        x: VW / 2, y: VH / 2, "text-anchor": "middle",
        style: "font-size:14px;fill:" + C.sub,
      }, "この領域には概念が登録されていません。"));
      return;
    }

    var PADX = 96, TOP = 64, BOTTOM = 84;
    var pos = {};
    var cnodes = [];
    concepts.forEach(function (c) {
      var cl = c.layout || { x: 0.5, y: 0.5 };
      var p = {
        x: PADX + clamp(cl.x, 0, 1) * (VW - 2 * PADX),
        y: TOP + clamp(cl.y, 0, 1) * (VH - TOP - BOTTOM),
      };
      pos[c.id] = p;
      cnodes.push({ id: c.id, x: p.x, y: p.y });
    });

    // ラベル許容幅: 最近傍距離から (広い分だけ余裕がある)
    var labelWidth = {};
    for (var i = 0; i < cnodes.length; i++) {
      var nn = Infinity;
      for (var j = 0; j < cnodes.length; j++) {
        if (i === j) continue;
        var dx = cnodes[i].x - cnodes[j].x, dy = cnodes[i].y - cnodes[j].y;
        var d = Math.sqrt(dx * dx + dy * dy);
        if (d < nn) nn = d;
      }
      if (nn === Infinity) nn = 320;
      labelWidth[cnodes[i].id] = clamp(nn * 0.92, 80, 260);
    }

    // 領域内エッジ (両端がこの領域の概念)
    (skeleton.edges || []).forEach(function (e) {
      if (ix.conceptRegion[e.from] !== nav.focus || ix.conceptRegion[e.to] !== nav.focus) return;
      var a = pos[e.from], b = pos[e.to];
      if (!a || !b) return;
      var hit = edgeHasIssue(errIdx, e), warn = edgeHasIssue(warnIdx, e);
      svg.appendChild(svgEl("line", {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        stroke: hit ? C.errorStroke : (warn ? C.warnStroke : C.edge),
        "stroke-width": (hit || warn) ? 1.5 : 0.9,
        "stroke-dasharray": e.kind === "depends" ? "1 0" : "4 4",
        opacity: 0.8,
      }));
    });

    // 概念ノード
    concepts.forEach(function (concept) {
      var p = pos[concept.id];
      var g = svgEl("g", { class: "atlas-preview-concept", "data-concept": concept.id, tabindex: "0", role: "button", style: "cursor:pointer" });
      var cErr = (errIdx.concepts[concept.id] || []);
      var cWarn = (warnIdx.concepts[concept.id] || []);
      var seed = seedOf(concept);
      var reviewed = concept.seed_status ? !!concept.seed_status.reviewed : false;
      var stroke = seedStroke(seed, cErr.length, cWarn.length);
      var fill = seedFill(seed);
      var dash = null;
      if (seed === "assumed") { fill = "none"; dash = "4 3"; }
      var sw = (cErr.length || cWarn.length) ? 2.25 : (seed === "assumed" ? 1.5 : 1);
      if (cErr.length || cWarn.length) dash = "5 3";

      if (nav.sel === concept.id || opts.selectedId === concept.id) {
        g.appendChild(svgEl("circle", { cx: p.x, cy: p.y, r: R + 5, fill: "none", stroke: C.accent, "stroke-width": 1.5 }));
      }
      g.appendChild(svgEl("circle", { cx: p.x, cy: p.y, r: R, fill: fill, stroke: stroke, "stroke-width": sw, "stroke-dasharray": dash }));
      if (seed && !reviewed) {
        g.appendChild(svgEl("text", { x: p.x + R + 1, y: p.y - R + 1, style: "font-size:9px;fill:" + C.sub }, "?"));
      }

      // 他領域との接続がある概念には外向きヒント (↗N)
      var ext = externalConnections(concept.id, ix);
      if (ext.length) {
        g.appendChild(svgEl("text", { x: p.x - R - 2, y: p.y - R + 1, "text-anchor": "end", style: "font-size:9.5px;fill:" + C.accent }, "↗" + ext.length));
      }

      var fit = fitLabel(concept.label || concept.id, labelWidth[concept.id] || 160);
      var lineH = fit.font * 1.16;
      var textEl = svgEl("text", {
        x: p.x, y: p.y + R + fit.font + 3, "text-anchor": "middle",
        style: "font-size:" + fit.font + "px;fill:" + C.label,
      });
      fit.lines.forEach(function (line, li) {
        textEl.appendChild(svgEl("tspan", { x: p.x, dy: li === 0 ? 0 : lineH }, line));
      });
      g.appendChild(textEl);
      g.appendChild(svgEl("title", null, concept.label || concept.id));

      bindActivate(g, function () {
        nav.sel = concept.id;
        nav.lastSelectedId = opts.selectedId;
        fireSelect(opts, { kind: "concept", region_id: nav.focus, concept_id: concept.id, label: concept.label || concept.id });
        renderDetail(lastArgs.container, skeleton, ix, errIdx, warnIdx, opts);
        // 選択リングだけ描き直すため全体 rerender
        rerender();
      });
      svg.appendChild(g);
    });
  }

  // 概念の他領域接続一覧
  function externalConnections(cid, ix) {
    var out = [];
    (ix.edgesByEndpoint[cid] || []).forEach(function (rec) {
      var other = rec.isFrom ? rec.edge.to : rec.edge.from;
      var otherRegion = ix.conceptRegion[other] || (ix.regionById[other] ? other : null);
      if (otherRegion && otherRegion !== ix.conceptRegion[cid]) out.push(rec);
    });
    return out;
  }

  // -------------------------------------------------------------------
  // 詳細パネル
  // -------------------------------------------------------------------

  function relationPhrase(kind, isFrom) {
    if (kind === "depends") return isFrom ? "に依存" : "から依存される";
    if (kind === "related") return "と関連";
    if (kind === "adjacent") return "と隣接";
    return "と関連";
  }

  function renderDetail(container, skeleton, ix, errIdx, warnIdx, opts) {
    var box = container && container._atlasPreviewDetail;
    if (!box) return;
    box.innerHTML = "";

    if (!nav.focus) {
      box.appendChild(legendRow());
      return;
    }

    var region = ix.regionById[nav.focus];
    if (nav.sel && ix.conceptById[nav.sel]) {
      renderConceptDetail(box, ix, nav.sel, errIdx, warnIdx);
      return;
    }

    // 領域のみ選択 (概念未選択)
    var b = region ? statusBreakdown(region) : { total: 0 };
    var head = el("div", { style: "color:var(--color-text-primary)" });
    head.appendChild(el("b", null, (region && region.label) || nav.focus));
    head.appendChild(document.createTextNode("（概念 " + b.total + " 件）— 概念をクリックすると seed_status と接続先が表示されます。"));
    box.appendChild(head);

    // 領域自身が結ぶエッジ (領域直結) があれば出す
    var regEdges = (ix.edgesByEndpoint[nav.focus] || []);
    if (regEdges.length) {
      var ul = el("div", { style: "margin-top:4px;color:var(--color-text-secondary)" });
      ul.appendChild(document.createTextNode("この領域の接続: "));
      ul.appendChild(document.createTextNode(regEdges.map(function (rec) {
        var other = rec.isFrom ? rec.edge.to : rec.edge.from;
        return labelOf(ix, other) + " " + relationPhrase(rec.edge.kind, rec.isFrom);
      }).join(" / ")));
      box.appendChild(ul);
    }
  }

  function renderConceptDetail(box, ix, cid, errIdx, warnIdx) {
    var c = ix.conceptById[cid];
    var seed = seedOf(c);
    var reviewed = c.seed_status ? !!c.seed_status.reviewed : false;
    var seedText = seed ? (SEED_LABEL[seed] || seed) + (reviewed ? "" : "（未レビュー）") : "seed_status 未設定";

    var head = el("div", { style: "color:var(--color-text-primary)" });
    head.appendChild(el("b", null, c.label || cid));
    head.appendChild(document.createTextNode("　" + seedText + "　/　id: " + cid));
    box.appendChild(head);

    // validation メッセージ
    var msgs = (errIdx.concepts[cid] || []).concat(warnIdx.concepts[cid] || []);
    if (msgs.length) {
      box.appendChild(el("div", { style: "color:var(--color-text-danger,#e53935);margin-top:2px" }, "検証: " + msgs.join(" / ")));
    }

    // 接続一覧 (領域内 / 他領域)
    var recs = (ix.edgesByEndpoint[cid] || []);
    var conn = el("div", { style: "margin-top:4px" });
    if (!recs.length) {
      conn.style.color = "var(--color-text-tertiary)";
      conn.textContent = "接続なし（この骨格では他要素と線で結ばれていません）。";
    } else {
      conn.appendChild(el("span", { style: "color:var(--color-text-secondary)" }, "接続:"));
      var list = el("ul", { style: "margin:2px 0 0 18px;padding:0" });
      recs.forEach(function (rec) {
        var other = rec.isFrom ? rec.edge.to : rec.edge.from;
        var otherRegion = ix.conceptRegion[other];
        var sameRegion = otherRegion === ix.conceptRegion[cid];
        var where = otherRegion ? (ix.regionById[otherRegion].label || otherRegion) : (ix.regionById[other] ? "領域" : "");
        var li = el("li", { style: "color:var(--color-text-primary)" });
        li.appendChild(document.createTextNode(labelOf(ix, other) + " " + relationPhrase(rec.edge.kind, rec.isFrom)));
        var tag = sameRegion ? "領域内" : (where ? where : "他");
        li.appendChild(el("span", { style: "color:var(--color-text-tertiary);font-size:11px;margin-left:6px" }, "（" + tag + "）"));
        list.appendChild(li);
      });
      conn.appendChild(list);
    }
    box.appendChild(conn);
  }

  function labelOf(ix, id) {
    return (ix.conceptById[id] && ix.conceptById[id].label) ||
      (ix.regionById[id] && ix.regionById[id].label) || id;
  }

  function legendRow() {
    var row = el("div", { style: "display:flex;flex-wrap:wrap;gap:14px;align-items:center;color:var(--color-text-secondary)" });
    function item(fill, stroke, dash, label) {
      var span = el("span", { style: "display:inline-flex;align-items:center;gap:5px" });
      var s = svgEl("svg", { width: 14, height: 14, viewBox: "0 0 14 14" });
      s.appendChild(svgEl("circle", { cx: 7, cy: 7, r: 5.5, fill: fill, stroke: stroke, "stroke-width": dash ? 1.5 : 1, "stroke-dasharray": dash }));
      span.appendChild(s);
      span.appendChild(document.createTextNode(label));
      return span;
    }
    row.appendChild(item(C.verifiedFill, C.verifiedStroke, null, "原文に裏付け"));
    row.appendChild(item(C.contestedFill, C.contestedStroke, null, "解釈が分かれる"));
    row.appendChild(item("none", C.assumedStroke, "3 2", "暗黙の前提"));
    row.appendChild(item(C.neutralFill, C.neutralStroke, null, "未設定"));
    return row;
  }

  // -------------------------------------------------------------------
  // 共通イベント
  // -------------------------------------------------------------------

  function bindActivate(g, handler) {
    g.addEventListener("click", function (e) { if (e) e.preventDefault(); handler(); });
    g.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(); }
    });
  }

  function fireSelect(opts, detail) {
    if (typeof opts.onSelect === "function") {
      opts.onSelect({ kind: detail.kind, region_id: detail.region_id, concept_id: detail.concept_id, label: detail.label });
    }
  }

  window.AtlasDraftPreview = { render: render };
})();
