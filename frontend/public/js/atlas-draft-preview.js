/* 分野の地図 — 骨格 draft のビジュアルプレビュー (skeleton editor upgrade P1/P2)
 *
 * 管理画面の骨格レビューで、生の JSON textarea と並べて「学習者にはこう見える」を
 * 読み取り専用で描画する。学習者向け atlas-overlay.js (L1) の視覚言語
 * (verified/contested/assumed の色・破線・二重円) をそのまま流用し、教員が新しい
 * 見え方を覚えなくて済むようにする。
 *
 * P1 の設計方針 (field_atlas_skeleton_editor_upgrade.md §3):
 * - サーバーを経由しないクライアント内の座標変換のみ (正規化 0-1 → viewBox)。
 * - draft は学習者非公開なので、seed_status 未レビューの概念にも色を付ける
 *   (学習者向け描画が課す display_seed_status の隠蔽ルールは適用しない)。
 * - P2: validate_skeleton の error_issues / warning_issues (region_id / concept_id /
 *   edge) を受け取り、該当ノードを赤 (error) / 橙 (warning) 枠でハイライトする。
 *   クリックで該当メッセージを表示する。
 *
 * 公開 API:
 *   window.AtlasDraftPreview.render(container, skeleton, opts)
 *     container : 描画先の DOM 要素
 *     skeleton  : atlas_skeleton 相当の dict ({regions, edges, ...})
 *     opts      : { errorIssues, warningIssues, onSelect, selectedId }
 *       - onSelect(detail) : ノードクリック時のコールバック (P5 のスコープヒント用)
 *         detail = { kind: "region"|"concept", region_id, concept_id, label }
 */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var VW = 680;
  var VH = 400;
  var PAD = 8; // viewBox 端の余白 (正規化 0-1 を内側に収める)

  // atlas-overlay.js §5 の視覚言語をそのまま複製する (テーマ変数ではなく仕様の定数)
  var C = {
    verifiedFill: "#E1F5EE", verifiedStroke: "#0F6E56",
    contestedFill: "#FAEEDA", contestedStroke: "#854F0B",
    assumedStroke: "#BA7517",
    neutralFill: "var(--color-background-tertiary, #f0efe9)", neutralStroke: "#888780",
    regionStroke: "#B4B2A9", regionFill: "rgba(180,178,169,0.06)",
    edge: "#B4B2A9",
    errorStroke: "#e53935", warnStroke: "#e8a020",
    label: "var(--color-text-primary, #2b2a26)",
    sub: "var(--color-text-secondary, #63615c)",
  };

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

  // 概念の絶対正規化座標。concept.layout は領域内の相対 (0-1)。
  function conceptAbs(region, concept) {
    var rl = region.layout || { x: 0, y: 0, w: 1, h: 1 };
    var cl = concept.layout || { x: 0.5, y: 0.5 };
    return {
      x: (rl.x || 0) + (cl.x || 0) * (rl.w || 0),
      y: (rl.y || 0) + (cl.y || 0) * (rl.h || 0),
    };
  }

  // issue リストを {regions:{id:[msg]}, concepts:{id:[msg]}, edges:{key:[msg]}} に索引化する。
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
        // エッジ固有の issue はエッジ索引に積み (向きは無視)、両端のノードにも
        // 波及させてハイライトする。endpoint が領域か概念かは issue だけでは
        // 分からないため両方のバケツに積む (id は領域・概念を跨いで一意)。
        var key = edgeKey(it.edge[0], it.edge[1]);
        (idx.edges[key] = idx.edges[key] || []).push(msg);
        it.edge.forEach(function (endpoint) {
          if (endpoint === it.region_id || endpoint === it.concept_id) return; // 付与済み
          (idx.regions[endpoint] = idx.regions[endpoint] || []).push(msg);
          (idx.concepts[endpoint] = idx.concepts[endpoint] || []).push(msg);
        });
        placed = true;
      }
      if (!placed) idx.other.push(msg);
    });
    return idx;
  }

  function edgeKey(a, b) {
    return a < b ? a + "→" + b : b + "→" + a;
  }

  function seedStroke(seed, hasError, hasWarn) {
    if (hasError) return C.errorStroke;
    if (hasWarn) return C.warnStroke;
    if (seed === "verified") return C.verifiedStroke;
    if (seed === "contested") return C.contestedStroke;
    if (seed === "assumed") return C.assumedStroke;
    return C.neutralStroke;
  }

  function render(container, skeleton, opts) {
    opts = opts || {};
    if (!container) return;
    container.innerHTML = "";
    if (!skeleton || !Array.isArray(skeleton.regions)) {
      container.appendChild(el("div", { style: "color:var(--color-text-tertiary);font-size:12px;padding:10px" },
        "プレビューできる骨格データがありません（regions が空、または JSON が不正です）。"));
      return;
    }

    var errIdx = indexIssues(opts.errorIssues);
    var warnIdx = indexIssues(opts.warningIssues);

    // draft 注記 (§3.1: 学習者には表示されない旨)
    container.appendChild(el("div", {
      class: "atlas-preview-note",
      style: "font-size:11.5px;color:var(--color-text-secondary);background:var(--color-background-tertiary,#f0efe9);border-radius:4px;padding:6px 8px;margin-bottom:8px",
    }, "これは draft のプレビューです。学習者には表示されません（未レビューの seed_status も色付きで見えています）。"));

    var svg = svgEl("svg", {
      width: "100%", viewBox: "0 0 " + VW + " " + VH,
      role: "img", "aria-label": "骨格プレビュー",
      style: "border:1px solid var(--color-border);border-radius:6px;background:var(--color-bg-secondary,#fff)",
    });

    // 位置索引 (エッジ描画用)。領域 id → 中心、概念 id → 座標。
    // エッジは領域の下・概念の下に描くため、先に全ノードの座標を確定させる
    // (概念描画後に計算すると概念間エッジの座標が解決できない)。
    var pos = {};
    skeleton.regions.forEach(function (region) {
      (region.concepts || []).forEach(function (concept) {
        var abs = conceptAbs(region, concept);
        pos[concept.id] = { x: nx(abs.x), y: ny(abs.y) };
      });
    });

    // --- 領域矩形 ---
    skeleton.regions.forEach(function (region) {
      var lo = region.layout;
      if (lo) {
        var rx = nx(lo.x), ry = ny(lo.y), rw = lo.w * (VW - 2 * PAD), rh = lo.h * (VH - 2 * PAD);
        pos[region.id] = { x: rx + rw / 2, y: ry + rh / 2 };
        var rErr = (errIdx.regions[region.id] || []);
        var rWarn = (warnIdx.regions[region.id] || []);
        var stroke = rErr.length ? C.errorStroke : (rWarn.length ? C.warnStroke : C.regionStroke);
        var sw = (rErr.length || rWarn.length) ? 2 : 0.75;
        var g = svgEl("g", { class: "atlas-preview-region", "data-region": region.id, tabindex: "0", role: "button", style: "cursor:pointer" });
        // 選択中 (AI agent の対象など) は外側に青枠を描く (§4.4: チャットと連動)
        if (opts.selectedId && opts.selectedId === region.id) {
          g.appendChild(svgEl("rect", {
            x: rx - 3, y: ry - 3, width: Math.max(rw, 1) + 6, height: Math.max(rh, 1) + 6, rx: 14,
            fill: "none", stroke: "#378ADD", "stroke-width": 1.5,
          }));
        }
        g.appendChild(svgEl("rect", {
          x: rx, y: ry, width: Math.max(rw, 1), height: Math.max(rh, 1), rx: 12,
          fill: C.regionFill, stroke: stroke, "stroke-width": sw,
          "stroke-dasharray": (rErr.length || rWarn.length) ? "6 3" : null,
        }));
        g.appendChild(svgEl("text", { x: rx + 10, y: ry + 18, style: "font-size:12px;font-weight:600;fill:" + C.label }, region.label || region.id));
        bindSelect(g, {
          kind: "region", region_id: region.id, concept_id: "",
          label: region.label || region.id,
          messages: rErr.concat(rWarn),
        }, opts);
        svg.appendChild(g);
      } else {
        pos[region.id] = { x: VW / 2, y: VH / 2 };
      }
    });

    // --- エッジ (領域間 / 概念間) ---
    (skeleton.edges || []).forEach(function (edge) {
      var a = pos[edge.from], b = pos[edge.to];
      if (!a || !b) return;
      var eErr = edgeHasIssue(errIdx, edge), eWarn = edgeHasIssue(warnIdx, edge);
      svg.appendChild(svgEl("line", {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        stroke: eErr ? C.errorStroke : (eWarn ? C.warnStroke : C.edge),
        "stroke-width": (eErr || eWarn) ? 1.5 : 0.75,
        "stroke-dasharray": edge.kind === "depends" ? "1 0" : "4 4",
        opacity: 0.7,
      }));
    });

    // --- 概念ノード ---
    skeleton.regions.forEach(function (region) {
      (region.concepts || []).forEach(function (concept) {
        var p = pos[concept.id] || { x: VW / 2, y: VH / 2 };
        var cx = p.x, cy = p.y;
        var cErr = (errIdx.concepts[concept.id] || []);
        var cWarn = (warnIdx.concepts[concept.id] || []);
        var seed = concept.seed_status ? concept.seed_status.value : null;
        var reviewed = concept.seed_status ? !!concept.seed_status.reviewed : false;
        var g = svgEl("g", { class: "atlas-preview-concept", "data-concept": concept.id, tabindex: "0", role: "button", style: "cursor:pointer" });

        var stroke = seedStroke(seed, cErr.length, cWarn.length);
        var fill = C.neutralFill;
        var dash = null;
        if (seed === "verified") fill = C.verifiedFill;
        else if (seed === "contested") fill = C.contestedFill;
        else if (seed === "assumed") { fill = "none"; dash = "4 3"; }
        // error/warn は枠を強調 (点線)。ハイライトは既存の色付けに上書き。
        var sw = (cErr.length || cWarn.length) ? 2.25 : (seed === "assumed" ? 1.5 : 1);
        if (cErr.length || cWarn.length) dash = "5 3";

        // 選択中は外輪を描く
        if (opts.selectedId && (opts.selectedId === concept.id)) {
          g.appendChild(svgEl("circle", { cx: cx, cy: cy, r: 15, fill: "none", stroke: "#378ADD", "stroke-width": 1.5 }));
        }
        g.appendChild(svgEl("circle", { cx: cx, cy: cy, r: 10, fill: fill, stroke: stroke, "stroke-width": sw, "stroke-dasharray": dash }));
        // 未レビューの seed_status は小さな「?」で明示 (draft のみの情報)
        if (seed && !reviewed) {
          g.appendChild(svgEl("text", { x: cx + 9, y: cy - 8, style: "font-size:9px;fill:" + C.sub }, "?"));
        }
        g.appendChild(svgEl("text", {
          x: cx, y: cy + 22, "text-anchor": "middle",
          style: "font-size:10.5px;fill:" + C.label,
        }, concept.label || concept.id));
        bindSelect(g, {
          kind: "concept", region_id: region.id, concept_id: concept.id,
          label: concept.label || concept.id,
          messages: cErr.concat(cWarn),
        }, opts);
        svg.appendChild(g);
      });
    });

    container.appendChild(svg);

    // クリックしたノードの検証メッセージ表示欄
    var msg = el("div", { class: "atlas-preview-msg", style: "font-size:12px;margin-top:6px;min-height:18px;color:var(--color-text-secondary)" });
    container.appendChild(msg);
    container._atlasPreviewMsg = msg;

    // どこにも紐づかない issue (cartridge 未設定など) はまとめて下部に出す
    var orphan = errIdx.other.concat(warnIdx.other);
    if (orphan.length) {
      var ol = el("div", { style: "font-size:11.5px;margin-top:4px;color:var(--color-text-tertiary)" });
      ol.textContent = "全体: " + orphan.join(" / ");
      container.appendChild(ol);
    }
  }

  function edgeHasIssue(idx, edge) {
    // エッジ固有の issue (参照切れ・kind 不正・領域重なり) のみ強調する。
    // 端点ノードだけの issue (label 欠落等) でエッジまで赤くしない。
    return !!idx.edges[edgeKey(edge.from, edge.to)];
  }

  function bindSelect(g, detail, opts) {
    function activate(e) {
      if (e) e.preventDefault();
      // 検証メッセージがあれば表示欄に出す
      var container = g.ownerSVGElement && g.ownerSVGElement.parentNode;
      var msgEl = container && container._atlasPreviewMsg;
      if (msgEl) {
        if (detail.messages && detail.messages.length) {
          msgEl.style.color = "var(--color-text-danger,#e53935)";
          msgEl.textContent = detail.label + ": " + detail.messages.join(" / ");
        } else {
          msgEl.style.color = "var(--color-text-secondary)";
          msgEl.textContent = detail.label + "（" + (detail.kind === "region" ? "領域" : "概念") + " id: " +
            (detail.concept_id || detail.region_id) + "）";
        }
      }
      if (typeof opts.onSelect === "function") {
        opts.onSelect({
          kind: detail.kind, region_id: detail.region_id,
          concept_id: detail.concept_id, label: detail.label,
        });
      }
    }
    g.addEventListener("click", activate);
    g.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") activate(e);
    });
  }

  window.AtlasDraftPreview = { render: render };
})();
