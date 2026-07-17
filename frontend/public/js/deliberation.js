/*
 * W層（Element Deliberation Workspace / 要素検討ワークスペース）Phase 0/1/2 統合パネル。
 *
 * ES5 / IIFE。window.Deliberation を公開。admin.js の initApp() から
 * Deliberation.init({apiFetch, escHtml}) を呼んで起動する
 * （admin-lecture-studio.js / admin-assistant.js と同型の DI 注入パターン）。
 *
 * 正本: docs/features/element_deliberation_workspace_design.md
 *   §8 API:
 *     GET  /api/admin/deliberation/elements/{element_type}/{element_id}/overview
 *     POST /api/admin/deliberation/sessions
 *     GET  /api/admin/deliberation/sessions/{id}
 *     POST /api/admin/deliberation/sessions/{id}/messages
 *     GET  /api/admin/deliberation/elements/{element_type}/{element_id}/annotations
 *     POST /api/admin/deliberation/annotations/{id}/commit
 *     POST /api/admin/deliberation/annotations/{id}/dismiss
 *   §9 フロント: 各要素の「深く検討」ボタン → モーダルで
 *     左=面①内訳+面②位置づけ（overview） / 右=面③対話的検討（sessions/annotations）。
 *
 * Phase 0/1 の範囲: overview の統合表示のみ（読み取り専用）。
 * Phase 2（本増分）: 右ペインに対話（sessions/messages）+ 候補注釈カード
 *   （annotations の一覧・commit・dismiss）を追加する。
 *
 * 不変条項（設計書 §0）:
 *   W2 確定は人間・AIは候補のみ — 候補注釈は commit されるまで status='candidate'。
 *   W4 情報を落とさない — 却下も削除せず status='dismissed' で保持。モーダル再オープン時は
 *     既存注釈（candidate/committed/dismissed すべて）を GET .../annotations で復元表示する。
 *     セッション履歴自体の復元（GET /sessions/{id}）は v1 では行わない（新規対話開始のみ）。
 *     過去ログは DB に保持されており、セッション一覧 UI は後続 issue で追加する（TODO）。
 *   W5 権限 fail-closed — overview/annotations/commit/dismiss は API 側がゲートする
 *     （本ファイルは何もしない）。
 *   W6 同期パスを重くしない — 対話は1送信=1応答。セッションは最初の送信時にだけ作成する
 *     （モーダルを開いただけでは POST /sessions を呼ばない。無駄な行・コストを作らない）。
 *   W8 数値を見せない — confidence は API が返す confidence_label のみ描画し、生値・件数は
 *     出さない。§9 ポーリング禁止・都度 fetch（キャッシュしない）。
 */
(function () {
  "use strict";

  // 注入される依存（疎結合）。未注入なら window グローバルへフォールバック
  // （admin-lecture-studio.js / admin-assistant.js と同型）。
  var deps = { apiFetch: null, apiFetchRaw: null, escHtml: null };
  var initialized = false;

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    return fn(path, opts);
  }

  // 図の原画像は JSON ではなく blob として取得する。管理画面の認証ヘッダーを
  // そのまま使うため apiFetchRaw を DI し、未注入時だけ apiFetch にフォールバックする。
  function apiFetchRaw(path, opts) {
    var fn = deps.apiFetchRaw || window.apiFetchRaw || deps.apiFetch || window.apiFetch;
    return fn(path, opts);
  }

  function escHtml(s) {
    var fn = deps.escHtml || window.escHtml;
    if (fn) return fn(s);
    if (s === null || s === undefined || s === "") return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // 面②の5レンズ（設計書 §4。cross_corpus は Phase 1 で追加）。null のレンズは区画ごと非表示にする。
  // cross_corpus の各 item は document_id を追加で持つが（route 層の権限フィルタ用）、
  // _lensSectionHtml は item.label / item.value しか読まないため余剰キーは自然と描画されない。
  // 同様に lens.hidden_count（隠した件数）も読まない＝表示しない（W8）。
  var LENS_LABELS = {
    intra_document: "論文内",
    cross_corpus: "コーパス横断",
    atlas: "分野の地図",
    endorsement: "承認・共有",
    epistemic: "検証・疑義"
  };
  var LENS_ORDER = ["intra_document", "cross_corpus", "atlas", "endorsement", "epistemic"];

  var ELEMENT_TYPE_LABELS = {
    figure: "図",
    theory_component: "コンポーネント",
    theory_claim: "claim",
    equation: "数式",
    shared_part: "共通部品"
  };

  // ── 要素インベントリ（Element Inventory / 検出要素の一覧）─────────────────
  // 正本: docs/features/element_inventory_design.md §4/§6/§7/§9。
  // 教材単位の統合入口。種別チップ + キーワードのフィルタは全面クライアントサイド
  // （§6: 1回のフェッチで全件を取得し、キー入力ごとの再フェッチをしない）。
  var INVENTORY_TYPE_ORDER = ["all", "theory_component", "theory_claim", "equation", "figure"];
  var INVENTORY_TYPE_CHIP_LABELS = {
    all: "すべて",
    theory_component: "論理要素",
    theory_claim: "主張",
    equation: "数式",
    figure: "図・画像"
  };
  var INVENTORY_TYPE_BADGE_LABELS = {
    theory_component: "論理要素",
    theory_claim: "主張",
    equation: "数式",
    figure: "図"
  };

  // 面③ 候補注釈の kind → 日本語ラベル（設計書 §5 コミットルーティング表 / §6）。
  var ANNOTATION_KIND_LABELS = {
    meaning: "意味づけ",
    decomposition: "内訳",
    positioning_note: "位置づけメモ",
    interpretation: "解釈",
    identity: "同一性",
    standardization: "標準化"
  };

  // Phase W-β: 同一性リンク（element_identity_links）の状態ラベル。
  // KN-3: 確定は人間のみ・行削除はしない。candidate/confirmed/rejected を明示区別する（G2-W）。
  var IDENTITY_LINK_STATUS_LABELS = {
    candidate: "候補（未確定）",
    confirmed: "確定済み",
    rejected: "却下"
  };

  // 面③ 対話状態。モーダルを開くたび（_closeModal で）リセットする単一セッション分の状態
  // （1モーダル=1対話。複数セッションの並行管理は v1 では行わない）。
  var chatState = { sessionId: null, ref: null, sending: false, selectedContext: null };
  // 教員指示付き再解析（guided reanalysis, focusMode/focusBbox/hintText）は
  // _reloadOverview を跨いで保持する（設計書 §7-3「送信した guidance は成功・失敗に
  // かかわらず消さない」）。別の要素でモーダルを開くとき（_closeModal 経由）にのみ
  // _resetFigureGuidanceState でリセットする（_resetFigureImageState とは別管理）。
  var figureImageState = {
    objectUrls: [], requestId: 0,
    focusMode: false,
    focusBbox: null,
    hintText: ""
  };

  // 要素インベントリモーダルの状態（1モーダル分）。フィルタ（typeFilter/keyword）は
  // クライアントサイドのみで完結し、変更のたびに再フェッチはしない（§6）。
  // 「深く検討」で別モーダル（#deliberation-modal）を開いても、このモーダルと
  // 状態は独立 DOM のため保持されたまま残る（再フェッチは「再読込」ボタンのみ）。
  var inventoryState = { documentId: null, title: null, data: null, typeFilter: "all", keyword: "" };

  function _resetInventoryState() {
    inventoryState = { documentId: null, title: null, data: null, typeFilter: "all", keyword: "" };
  }

  function _resetChatState() {
    chatState = { sessionId: null, ref: null, sending: false, selectedContext: null };
  }

  function _resetFigureImageState() {
    figureImageState.requestId += 1;
    figureImageState.objectUrls.forEach(function (url) {
      try { URL.revokeObjectURL(url); } catch (e) { /* noop */ }
    });
    figureImageState.objectUrls = [];
  }

  function _resetFigureGuidanceState() {
    figureImageState.focusMode = false;
    figureImageState.focusBbox = null;
    figureImageState.hintText = "";
  }

  // ── 公開 API: init ───────────────────────────────────────────────────
  function init(options) {
    options = options || {};
    deps.apiFetch = options.apiFetch || null;
    deps.apiFetchRaw = options.apiFetchRaw || null;
    deps.escHtml = options.escHtml || null;
    initialized = true;
  }

  // ── モーダル DOM ──────────────────────────────────────────────────────
  function _closeModal() {
    _resetFigureImageState();
    _resetFigureGuidanceState();
    var m = document.getElementById("deliberation-modal");
    if (m) m.remove();
    _resetChatState();
  }

  function _renderValue(value) {
    if (value === null || value === undefined || value === "") {
      return '<span style="color:var(--color-text-tertiary)">(空)</span>';
    }
    if (typeof value === "object") {
      // ネストした dict/配列はデータをそのまま簡潔に提示する（要約・件数化はしない・W4）。
      try {
        return '<pre style="white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px">' +
          escHtml(JSON.stringify(value, null, 2)) + '</pre>';
      } catch (e) {
        return escHtml(String(value));
      }
    }
    return escHtml(String(value));
  }

  function _kvTableHtml(pairs) {
    // pairs: [[label, value], ...]
    if (!pairs.length) {
      return '<p style="font-size:12.5px;color:var(--color-text-tertiary);margin:4px 0">情報がありません</p>';
    }
    var rows = pairs.map(function (pair) {
      return '<tr>' +
        '<td style="padding:4px 10px 4px 0;font-size:12.5px;color:var(--color-text-tertiary);white-space:nowrap;vertical-align:top">' +
          escHtml(pair[0]) +
        '</td>' +
        '<td style="padding:4px 0;font-size:13px;color:var(--color-text-primary);vertical-align:top">' +
          _renderValue(pair[1]) +
        '</td>' +
      '</tr>';
    }).join("");
    return '<table style="width:100%;border-collapse:collapse">' + rows + '</table>';
  }

  function _fieldsHtml(fields) {
    var keys = Object.keys(fields || {});
    if (!keys.length) {
      return '<p style="font-size:13px;color:var(--color-text-tertiary)">内訳データがありません</p>';
    }
    return _kvTableHtml(keys.map(function (k) { return [k, fields[k]]; }));
  }

  function _notesHtml(notes) {
    if (!notes || !notes.length) return "";
    return '<div style="margin-top:10px;padding:8px 10px;background:var(--color-background-tertiary);border-radius:4px">' +
      notes.map(function (n) {
        return '<div style="font-size:12px;color:var(--color-text-secondary)">・' + escHtml(n) + '</div>';
      }).join("") +
    '</div>';
  }

  function _lensSectionHtml(key, lens) {
    if (!lens) return "";
    var items = lens.items || [];
    return '<div style="margin-bottom:10px">' +
      '<div style="font-size:12.5px;font-weight:600;color:var(--color-text-secondary);margin-bottom:4px">' +
        escHtml(LENS_LABELS[key]) +
      '</div>' +
      _kvTableHtml(items.map(function (it) { return [it.label, it.value]; })) +
    '</div>';
  }

  function _positioningHtml(positioning) {
    if (!positioning || !positioning.available) return "";
    var lenses = positioning.lenses || {};
    var sections = LENS_ORDER.map(function (key) {
      return _lensSectionHtml(key, lenses[key]);
    }).join("");
    if (!sections.trim()) return "";
    return '<div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 10px;font-size:14px;color:var(--color-text-primary)">位置づけ</h4>' +
      sections +
    '</div>';
  }

  // ── 図・画像の読み解き UI（Issue #496）─────────────────────────────
  // API の移行期間中も表示を止めないよう、analysis_profile 配下と各専用キーの
  // どちらも受け付ける。分類や解析が無い場合は原図＋一般案内へ fail-soft する。
  var FIGURE_MODE_LABELS = {
    functional_diagram: "機能構成図",
    data_plot: "グラフ",
    descriptive_image: "写真・解説画像",
    mixed: "複合図",
    unknown: "未分類"
  };
  var FIGURE_MODE_REASON_LABELS = {
    caption_or_legacy_type_contains_multiple_mode_cues: "captionに複数種類の手がかりがあります",
    caption_or_legacy_type_heuristic: "captionと周辺情報から推定しました",
    insufficient_classification_signal: "分類に十分な手がかりがありません",
    legacy_apparatus_artifact: "既存の装置解析結果から推定しました"
  };

  function _asArray(value) {
    if (Array.isArray(value)) return value;
    if (value === null || value === undefined || value === "") return [];
    return [value];
  }

  function _itemText(item) {
    if (item === null || item === undefined) return "";
    if (typeof item !== "object") return String(item);
    return String(item.name || item.label || item.title || item.text || item.value ||
      item.description || item.summary || "");
  }

  function _itemId(item, fallback) {
    if (!item || typeof item !== "object") return String(fallback || _itemText(item));
    return String(item.id || item.function_id || item.component_id || item.part_id || item.subject_id ||
      item.observation_id || item.key || fallback || _itemText(item));
  }

  function _contextAttrs(kind, id, label, bbox) {
    var attrs = ' data-deliberation-context-kind="' + escHtml(kind) + '"' +
      ' data-deliberation-context-id="' + escHtml(String(id || "")) + '"' +
      ' data-deliberation-context-label="' + escHtml(label || "") + '"';
    if (bbox) {
      try { attrs += ' data-deliberation-bbox="' + escHtml(JSON.stringify(bbox)) + '"'; } catch (e) { /* noop */ }
    }
    return attrs;
  }

  function _normalizedFigureMode(value) {
    var mode = String(value || "unknown").toLowerCase();
    if (mode === "functional" || mode === "schematic" || mode === "apparatus" || mode === "system_diagram") {
      return "functional_diagram";
    }
    if (mode === "graph" || mode === "plot" || mode === "chart") return "data_plot";
    if (mode === "photo" || mode === "photograph" || mode === "illustration" || mode === "descriptive") {
      return "descriptive_image";
    }
    if (!FIGURE_MODE_LABELS[mode]) return "unknown";
    return mode;
  }

  function _figureAnalysis(fields, mode) {
    fields = fields || {};
    var profile = fields.analysis_profile || {};
    var keyMap = {
      functional_diagram: "functional_analysis",
      data_plot: "data_plot_analysis",
      descriptive_image: "descriptive_analysis"
    };
    var key = keyMap[mode];
    if (!key) return null;
    var nested = fields[key] || profile[key] || profile[mode];
    if (nested) return nested;
    if (profile.mode === mode || profile.effective_mode === mode) return profile;
    // Terra の主契約は effective_mode ごとの flat analysis_profile。mode フィールドが
    // 無い場合も代表キーで安全に識別し、旧 nested 形式と併存させる。
    if (mode === "functional_diagram" &&
        (profile.overall_function || profile.functions || profile.functional_units || profile.connections)) return profile;
    if (mode === "data_plot" &&
        (profile.plot_type || profile.axes || profile.x_axis || profile.y_axis || profile.y_axes || profile.series ||
          profile.observations || profile.interpretations || profile.highlights)) return profile;
    if (mode === "descriptive_image" &&
        (profile.subjects || profile.objects || profile.entities || profile.regions || profile.scene ||
          profile.description || profile.teaching_points || profile.key_points)) return profile;
    return null;
  }

  function _simpleListHtml(items, emptyText) {
    var values = _asArray(items).map(_itemText).filter(Boolean);
    if (!values.length) {
      return emptyText ? '<span class="deliberation-figure-muted">' + escHtml(emptyText) + '</span>' : "";
    }
    return '<ul class="deliberation-figure-list">' + values.map(function (value) {
      return '<li>' + escHtml(value) + '</li>';
    }).join("") + '</ul>';
  }

  function _ioHtml(label, items) {
    var values = _asArray(items).map(_itemText).filter(Boolean);
    return '<div class="deliberation-figure-io">' +
      '<span class="deliberation-figure-io-label">' + escHtml(label) + '</span>' +
      (values.length ? values.map(function (value) {
        return '<span class="deliberation-figure-chip">' + escHtml(value) + '</span>';
      }).join("") : '<span class="deliberation-figure-muted">情報なし</span>') +
    '</div>';
  }

  function _endpointText(value, port) {
    var text = _itemText(value);
    if (!text && value && typeof value === "object") {
      text = _itemText(value.function || value.component || value.part || value.node);
      port = port || value.port || value.output || value.input;
    }
    return text + (port ? "（" + _itemText(port) + "）" : "");
  }

  function _functionLookup(functions) {
    var lookup = {};
    _asArray(functions).forEach(function (item, index) {
      if (!item || typeof item !== "object") return;
      var id = _itemId(item, index + 1);
      var entry = { name: _itemText(item.name || item.label || item.title) || id, inputs: {}, outputs: {} };
      _asArray(item.inputs || item.input_ports).forEach(function (port, portIndex) {
        if (!port || typeof port !== "object") return;
        entry.inputs[_itemId(port, portIndex + 1)] = _itemText(port) || _itemId(port, portIndex + 1);
      });
      _asArray(item.outputs || item.output_ports).forEach(function (port, portIndex) {
        if (!port || typeof port !== "object") return;
        entry.outputs[_itemId(port, portIndex + 1)] = _itemText(port) || _itemId(port, portIndex + 1);
      });
      lookup[id] = entry;
      lookup[entry.name] = entry;
    });
    return lookup;
  }

  function _resolvedEndpoint(functionValue, portValue, lookup, direction) {
    var functionKey = _itemText(functionValue);
    var portKey = _itemText(portValue);
    var entry = lookup[functionKey];
    if (!entry) return _endpointText(functionValue, portValue);
    var portName = (direction === "output" ? entry.outputs : entry.inputs)[portKey] || portKey;
    return entry.name + (portName ? "（" + portName + "）" : "");
  }

  function _connectionText(connection, functionLookup) {
    if (typeof connection !== "object" || connection === null) return _itemText(connection);
    functionLookup = functionLookup || {};
    var fromFunction = connection.from || connection.source || connection.source_function || connection.from_function ||
      connection.from_function_id || connection.source_function_id || connection.source_id || connection.upstream;
    var fromPort = connection.from_port || connection.source_port || connection.output_port || connection.from_output ||
      connection.from_output_id || connection.source_output_id;
    var toFunction = connection.to || connection.target || connection.target_function || connection.to_function ||
      connection.to_function_id || connection.target_function_id || connection.target_id || connection.downstream;
    var toPort = connection.to_port || connection.target_port || connection.input_port || connection.to_input ||
      connection.to_input_id || connection.target_input_id;
    var from = _resolvedEndpoint(fromFunction, fromPort, functionLookup, "output");
    var to = _resolvedEndpoint(toFunction, toPort, functionLookup, "input");
    var transfer = _itemText(connection.transfer || connection.carries || connection.signal ||
      connection.flow || connection.relation || connection.medium || connection.description);
    var path = from && to ? from + " → " + to : (from || to);
    return path + (path && transfer ? "：" : "") + transfer;
  }

  function _connectionIds(connection) {
    if (!connection || typeof connection !== "object") return [];
    return [
      connection.from_function_id, connection.source_function_id, connection.from_function,
      connection.source_function, connection.from, connection.source,
      connection.to_function_id, connection.target_function_id, connection.to_function,
      connection.target_function, connection.to, connection.target
    ].map(_itemText).filter(Boolean);
  }

  function _functionalDiagramHtml(analysis, nested) {
    analysis = analysis || {};
    var functions = _asArray(analysis.functions || analysis.functional_units || analysis.components || analysis.parts);
    var connections = _asArray(analysis.connections || analysis.links || analysis.flows);
    var functionLookup = _functionLookup(functions);
    var overall = _itemText(analysis.overall_function || analysis.system_function || analysis.summary || analysis.description);
    var hasData = overall || functions.length || connections.length || analysis.inputs || analysis.external_inputs ||
      analysis.outputs || analysis.external_outputs;
    return '<section class="deliberation-figure-analysis' + (nested ? ' nested' : '') + '">' +
      (nested ? '<h5>機能構成</h5>' : '') +
      (!hasData ? '<p class="deliberation-figure-empty">機能・接続の解析結果はまだありません。原図と周辺本文を確認しながら質問できます。</p>' : '') +
      (overall ? '<div class="deliberation-figure-overall"><span>全体の機能</span><strong>' + escHtml(overall) + '</strong></div>' : '') +
      '<div class="deliberation-figure-system-io">' +
        _ioHtml("外部入力", analysis.external_inputs || analysis.inputs) +
        _ioHtml("外部出力", analysis.external_outputs || analysis.outputs) +
      '</div>' +
      (functions.length ? '<div class="deliberation-figure-subheading">機能と入出力</div><div class="deliberation-function-grid">' +
        functions.map(function (item, index) {
          item = (item && typeof item === "object") ? item : { name: item };
          var name = _itemText(item.name || item.label || item.title) || ("機能 " + (index + 1));
          var itemId = _itemId(item, index + 1);
          var role = _itemText(item.role || item.function || item.description || item.purpose);
          return '<article class="deliberation-function-card">' +
            '<h6><button type="button" class="deliberation-context-target"' +
              _contextAttrs("part", itemId, name, item.bbox || item.region) + '>' +
              escHtml(name) + '</button></h6>' +
            (role ? '<p>' + escHtml(role) + '</p>' : '<p class="deliberation-figure-muted">役割の説明はまだありません</p>') +
            _ioHtml("入力", item.inputs || item.input_ports) +
            _ioHtml("出力", item.outputs || item.output_ports) +
          '</article>';
        }).join("") + '</div>' : '') +
      (connections.length ? '<div class="deliberation-figure-subheading">接続と流れ</div><ol class="deliberation-connection-list">' +
        connections.map(function (connection) {
          return '<li data-deliberation-connection="' + escHtml(_connectionIds(connection).join("|")) + '">' +
            escHtml(_connectionText(connection, functionLookup) || "接続情報") + '</li>';
        }).join("") + '</ol>' : '') +
    '</section>';
  }

  function _axisText(key, axis) {
    if (typeof axis !== "object" || axis === null) return key + "：" + _itemText(axis);
    key = _itemText(axis.orientation || axis.axis) || key;
    var name = _itemText(axis.name || axis.label || axis.variable) || key;
    var unit = _itemText(axis.unit);
    var scale = _itemText(axis.scale || axis.scale_type);
    return key + "：" + name + (unit ? "（" + unit + "）" : "") + (scale ? "・" + scale : "");
  }

  function _axesHtml(axes) {
    if (!axes) return '<span class="deliberation-figure-muted">軸情報なし</span>';
    var rows = [];
    if (Array.isArray(axes)) {
      rows = axes.map(function (axis, index) { return _axisText("軸" + (index + 1), axis); });
    } else if (typeof axes === "object") {
      rows = Object.keys(axes).map(function (key) { return _axisText(key, axes[key]); });
    } else {
      rows = [_itemText(axes)];
    }
    return _simpleListHtml(rows);
  }

  function _observationHtml(item, index, interpretation) {
    if (typeof item !== "object" || item === null) item = { observation: item };
    var observed = _itemText(item.observation || item.observed || item.fact || item.text || item.description);
    interpretation = interpretation || {};
    var meaning = _itemText(item.interpretation || item.meaning || item.implication || item.meaning_candidate ||
      interpretation.meaning_candidate || interpretation.interpretation || interpretation.meaning || interpretation.text);
    var approximate = item.approximate || item.is_approximate || item.estimated || item.value_kind === "approximate";
    var itemId = _itemId(item, index + 1);
    var contextLabel = observed || ("観測 " + (index + 1));
    return '<li>' +
      '<div><strong><button type="button" class="deliberation-context-target"' +
        _contextAttrs("observation", itemId, contextLabel, item.bbox || item.region) + '>' +
        '観測' + (approximate ? '（概算）' : '') + '</button></strong>' +
        (observed ? '<p>' + escHtml(observed) + '</p>' : '<p class="deliberation-figure-muted">記述なし</p>') +
      '</div>' +
      (meaning ? '<div><strong>意味候補</strong><p>' + escHtml(meaning) + '</p></div>' : '') +
    '</li>';
  }

  function _interpretationHtml(item) {
    var text = _itemText(item && typeof item === "object"
      ? (item.meaning_candidate || item.interpretation || item.meaning || item.text || item.description)
      : item);
    return '<li><strong>意味候補</strong><p>' + escHtml(text || "記述なし") + '</p></li>';
  }

  function _selectableSubjectsHtml(items) {
    var values = _asArray(items);
    if (!values.length) return "";
    return '<ul class="deliberation-figure-list deliberation-subject-list">' + values.map(function (item, index) {
      var label = _itemText(item) || ("対象 " + (index + 1));
      return '<li><button type="button" class="deliberation-context-target"' +
        _contextAttrs("subject", _itemId(item, index + 1), label,
          item && typeof item === "object" ? (item.bbox || item.region) : null) + '>' +
        escHtml(label) + '</button></li>';
    }).join("") + '</ul>';
  }

  function _dataPlotHtml(analysis, nested) {
    analysis = analysis || {};
    var series = _asArray(analysis.series || analysis.data_series || analysis.legend);
    var observations = _asArray(analysis.observations || analysis.insights || analysis.findings || analysis.notable_points);
    var interpretations = _asArray(analysis.interpretations || analysis.meanings || analysis.meaning_candidates);
    var highlights = _asArray(analysis.highlights || analysis.highlight_regions);
    var hasData = analysis.axes || analysis.x_axis || analysis.y_axis || analysis.y_axes || series.length ||
      observations.length || interpretations.length || highlights.length || analysis.summary;
    var axes = analysis.axes || null;
    if (!axes && (analysis.x_axis || analysis.y_axis || analysis.y_axes)) {
      axes = {};
      if (analysis.x_axis) axes.x = analysis.x_axis;
      if (analysis.y_axis) axes.y = analysis.y_axis;
      _asArray(analysis.y_axes).forEach(function (axis, index) { axes["y" + (index + 1)] = axis; });
    }
    return '<section class="deliberation-figure-analysis' + (nested ? ' nested' : '') + '">' +
      (nested ? '<h5>グラフ</h5>' : '') +
      (!hasData ? '<p class="deliberation-figure-empty">軸・系列・観測点の解析結果はまだありません。値を断定せず原図を確認してください。</p>' : '') +
      (analysis.summary ? '<div class="deliberation-figure-overall"><span>グラフの概要</span><strong>' + escHtml(_itemText(analysis.summary)) + '</strong></div>' : '') +
      '<div class="deliberation-plot-basics"><div><h6>軸・単位・尺度</h6>' + _axesHtml(axes) + '</div>' +
      '<div><h6>系列</h6>' + _simpleListHtml(series, "系列情報なし") + '</div></div>' +
      (observations.length ? '<div class="deliberation-figure-subheading">読み取り</div><ul class="deliberation-observation-list">' +
        observations.map(function (observation, index) {
          var observationId = _itemId(observation, index + 1);
          var matched = interpretations.filter(function (candidate) {
            if (!candidate || typeof candidate !== "object") return false;
            return String(candidate.observation_id || candidate.observation_ref || candidate.source_observation_id || "") === observationId;
          })[0];
          return _observationHtml(observation, index, matched);
        }).join("") + '</ul>' : '') +
      (interpretations.filter(function (candidate) {
        if (!candidate || typeof candidate !== "object") return true;
        var reference = String(candidate.observation_id || candidate.observation_ref || candidate.source_observation_id || "");
        return !reference || !observations.some(function (observation, index) { return _itemId(observation, index + 1) === reference; });
      }).length ? '<div class="deliberation-figure-subheading">意味候補</div><ul class="deliberation-observation-list deliberation-interpretation-list">' +
        interpretations.filter(function (candidate) {
          if (!candidate || typeof candidate !== "object") return true;
          var reference = String(candidate.observation_id || candidate.observation_ref || candidate.source_observation_id || "");
          return !reference || !observations.some(function (observation, index) { return _itemId(observation, index + 1) === reference; });
        }).map(_interpretationHtml).join("") + '</ul>' : '') +
      (highlights.length ? '<div class="deliberation-figure-subheading">注目箇所</div><ul class="deliberation-observation-list">' +
        highlights.map(function (highlight, index) { return _observationHtml(highlight, index, null); }).join("") + '</ul>' : '') +
      '<p class="deliberation-figure-caution">画像から読み取った数値は概算です。原図の目盛り・凡例・誤差表示を確認してください。</p>' +
    '</section>';
  }

  function _descriptiveImageHtml(analysis, nested) {
    analysis = analysis || {};
    var summary = _itemText(analysis.summary || analysis.description || analysis.explanation || analysis.scene);
    var subjects = _asArray(analysis.subjects).concat(
      _asArray(analysis.objects || analysis.entities), _asArray(analysis.regions)
    );
    var points = _asArray(analysis.teaching_points).concat(
      _asArray(analysis.key_points || analysis.observations || analysis.details)
    );
    var hasData = summary || _asArray(subjects).length || _asArray(points).length;
    return '<section class="deliberation-figure-analysis' + (nested ? ' nested' : '') + '">' +
      (nested ? '<h5>写真・解説画像</h5>' : '') +
      (!hasData ? '<p class="deliberation-figure-empty">画像の短い解説はまだありません。原図とcaptionを確認しながら質問できます。</p>' : '') +
      (summary ? '<div class="deliberation-figure-overall"><span>この画像について</span><strong>' + escHtml(summary) + '</strong></div>' : '') +
      (_asArray(subjects).length ? '<div class="deliberation-figure-subheading">写っているもの・領域</div>' + _selectableSubjectsHtml(subjects) : '') +
      (_asArray(points).length ? '<div class="deliberation-figure-subheading">確認ポイント</div>' + _simpleListHtml(points) : '') +
    '</section>';
  }

  function _mixedPanelsHtml(fields) {
    var profile = (fields && fields.analysis_profile) || {};
    var summary = _itemText(profile.summary || profile.description);
    var panels = _asArray(profile.panels);
    return (summary ? '<div class="deliberation-figure-overall"><span>図全体</span><strong>' + escHtml(summary) + '</strong></div>' : '') +
      (panels.length ? '<div class="deliberation-figure-subheading">パネル</div><div class="deliberation-function-grid">' +
        panels.map(function (panel, index) {
          panel = panel && typeof panel === "object" ? panel : { label: panel };
          var label = _itemText(panel.label || panel.name || panel.title) || ("パネル " + (index + 1));
          var panelMode = _normalizedFigureMode(panel.mode || panel.presentation_mode || panel.type);
          var description = _itemText(panel.description || panel.summary || panel.role);
          var panelAnalysis = panel.analysis || panel.analysis_profile || null;
          var detail = "";
          if (panelAnalysis && typeof panelAnalysis === "object" && Object.keys(panelAnalysis).length) {
            if (panelMode === "functional_diagram") detail = _functionalDiagramHtml(panelAnalysis, true);
            else if (panelMode === "data_plot") detail = _dataPlotHtml(panelAnalysis, true);
            else if (panelMode === "descriptive_image") detail = _descriptiveImageHtml(panelAnalysis, true);
          }
          return '<article class="deliberation-function-card deliberation-mixed-panel"><h6><button type="button" class="deliberation-context-target"' +
            _contextAttrs("subject", _itemId(panel, index + 1), label, panel.bbox || panel.region) + '>' + escHtml(label) + '</button></h6>' +
            '<p>' + escHtml(FIGURE_MODE_LABELS[panelMode]) + (description ? ' — ' + escHtml(description) : '') + '</p>' +
            (detail ? '<details class="deliberation-mixed-panel-detail"><summary>パネルの解析を見る</summary>' + detail + '</details>' : '') +
            '</article>';
        }).join("") + '</div>' : '');
  }

  // ── 教員指示付き図再解析（Guided Figure Re-analysis）───────────────────
  // 正本: docs/features/guided_figure_reanalysis_design.md §7。
  // GF1: 指示は注意誘導であって確定ではない（送信しても候補が生まれるだけ）。
  // 状態は figureImageState.focusMode / focusBbox / hintText に持つ
  // （_reloadOverview を跨いで保持・別要素を開いたときのみリセット。上部の
  // _resetFigureGuidanceState 参照）。

  function _figureFocusControlsHtml() {
    var active = !!figureImageState.focusMode;
    var hasBbox = !!figureImageState.focusBbox;
    return '<div class="deliberation-figure-focus-row">' +
      '<button id="deliberation-focus-toggle" type="button" ' +
        'class="deliberation-focus-toggle' + (active ? ' is-active' : '') + '" ' +
        'aria-pressed="' + (active ? 'true' : 'false') + '">領域を指定して再解析</button>' +
      '<button id="deliberation-focus-clear" type="button"' + (hasBbox ? '' : ' hidden') + '>領域をクリア</button>' +
      '</div>' +
      '<textarea id="deliberation-reanalyze-hint" class="deliberation-reanalyze-hint" rows="2" maxlength="2000" ' +
        'placeholder="例：左下の EOM と書かれた箱が変調器。3.2節の説明に対応する">' +
        escHtml(figureImageState.hintText || "") +
      '</textarea>';
  }

  function _setFigureFocusLayerActive(active) {
    var layer = document.getElementById("deliberation-figure-focus-layer");
    if (layer) layer.classList.toggle("is-drawable", !!active);
  }

  // 描画済みの矩形は focus モード OFF でも常時表示する（設計書 §7-2）。
  function _renderFigureFocusRect() {
    var layer = document.getElementById("deliberation-figure-focus-layer");
    if (!layer) return;
    var existing = layer.querySelector(".deliberation-figure-focus-rect");
    if (existing) existing.remove();
    var bbox = figureImageState.focusBbox;
    if (!bbox) return;
    var rect = document.createElement("div");
    rect.className = "deliberation-figure-focus-rect";
    rect.style.left = (bbox[0] * 100) + "%";
    rect.style.top = (bbox[1] * 100) + "%";
    rect.style.width = ((bbox[2] - bbox[0]) * 100) + "%";
    rect.style.height = ((bbox[3] - bbox[1]) * 100) + "%";
    layer.appendChild(rect);
  }

  function _bindFigureFocusToggle() {
    var toggle = document.getElementById("deliberation-focus-toggle");
    if (!toggle) return;
    toggle.addEventListener("click", function () {
      figureImageState.focusMode = !figureImageState.focusMode;
      toggle.classList.toggle("is-active", figureImageState.focusMode);
      toggle.setAttribute("aria-pressed", figureImageState.focusMode ? "true" : "false");
      _setFigureFocusLayerActive(figureImageState.focusMode);
    });
  }

  function _bindFigureFocusClear() {
    var clearBtn = document.getElementById("deliberation-focus-clear");
    if (!clearBtn) return;
    clearBtn.addEventListener("click", function () {
      figureImageState.focusBbox = null;
      clearBtn.hidden = true;
      _renderFigureFocusRect();
    });
  }

  // touch/mouse 共通のイベント座標抽出（touchend/touchcancel は changedTouches のみ持つ）。
  function _figureFocusEventClientPoint(event) {
    if (event.touches && event.touches.length) {
      return { x: event.touches[0].clientX, y: event.touches[0].clientY };
    }
    if (event.changedTouches && event.changedTouches.length) {
      return { x: event.changedTouches[0].clientX, y: event.changedTouches[0].clientY };
    }
    return { x: event.clientX, y: event.clientY };
  }

  // layer の実表示サイズに対する相対座標 0..1 に正規化する（画像内相対座標、設計書 §3）。
  function _figureFocusRelativePoint(layer, clientPoint) {
    var rect = layer.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    var x = (clientPoint.x - rect.left) / rect.width;
    var y = (clientPoint.y - rect.top) / rect.height;
    return [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
  }

  // #deliberation-figure-overlays の兄弟レイヤーに矩形ドラッグ描画を配線する
  // （既存オーバーレイのクリック選択と干渉させない。focus モード ON のときのみ
  // pointer-events を有効化する ── _setFigureFocusLayerActive / CSS is-drawable）。
  function _bindFigureFocusDrawing() {
    _bindFigureFocusToggle();
    _bindFigureFocusClear();
    _setFigureFocusLayerActive(figureImageState.focusMode);
    _renderFigureFocusRect();

    var hintInput = document.getElementById("deliberation-reanalyze-hint");
    if (hintInput) {
      hintInput.addEventListener("input", function () {
        figureImageState.hintText = hintInput.value;
      });
    }

    var layer = document.getElementById("deliberation-figure-focus-layer");
    if (!layer) return;

    var dragStart = null;
    var dragPreviousBbox = null;

    function pointFromEvent(event) {
      return _figureFocusRelativePoint(layer, _figureFocusEventClientPoint(event));
    }

    function onDown(event) {
      if (!figureImageState.focusMode) return;
      var point = pointFromEvent(event);
      if (!point) return;
      event.preventDefault();
      dragStart = point;
      dragPreviousBbox = figureImageState.focusBbox;
    }

    function onMove(event) {
      if (!dragStart) return;
      var point = pointFromEvent(event);
      if (!point) return;
      event.preventDefault();
      figureImageState.focusBbox = [
        Math.min(dragStart[0], point[0]),
        Math.min(dragStart[1], point[1]),
        Math.max(dragStart[0], point[0]),
        Math.max(dragStart[1], point[1])
      ];
      _renderFigureFocusRect();
    }

    function onUp() {
      if (!dragStart) return;
      dragStart = null;
      var bbox = figureImageState.focusBbox;
      // 幅または高さが 0.02 未満の極小ドラッグは誤クリックとして無視する
      // （サーバ側 422 の閾値と整合。設計書 §7-2 / §4-1）。
      var tooSmall = !bbox || (bbox[2] - bbox[0] < 0.02) || (bbox[3] - bbox[1] < 0.02);
      if (tooSmall) figureImageState.focusBbox = dragPreviousBbox;
      dragPreviousBbox = null;
      _renderFigureFocusRect();
      var clearBtn = document.getElementById("deliberation-focus-clear");
      if (clearBtn) clearBtn.hidden = !figureImageState.focusBbox;
    }

    layer.addEventListener("mousedown", onDown);
    layer.addEventListener("mousemove", onMove);
    layer.addEventListener("mouseup", onUp);
    layer.addEventListener("mouseleave", onUp);
    layer.addEventListener("touchstart", onDown, { passive: false });
    layer.addEventListener("touchmove", onMove, { passive: false });
    layer.addEventListener("touchend", onUp);
    layer.addEventListener("touchcancel", onUp);
  }

  function _figureModeHtml(fields) {
    fields = fields || {};
    var mode = _normalizedFigureMode(fields.effective_mode || fields.reviewed_mode || fields.suggested_mode);
    var status = fields.mode_review_status || "";
    var reason = fields.mode_reason || "";
    var reasonText = FIGURE_MODE_REASON_LABELS[reason] ||
      (reason && reason.indexOf("_") === -1 ? reason : "");
    var reviewed = fields.reviewed_mode;
    var header = '<div class="deliberation-figure-mode-header"><span class="deliberation-figure-mode-badge">' +
      escHtml(FIGURE_MODE_LABELS[mode]) + '</span>' +
      (reviewed ? '<span class="deliberation-figure-reviewed">教員確認済み</span>' :
        (status === "pending" ? '<span class="deliberation-figure-muted">AI候補・要確認</span>' : '')) +
      (fields.analysis_source === "teacher_reviewed"
        ? '<span class="deliberation-figure-reviewed">構成も確認済み</span>' : '') +
      '</div>' +
      (reasonText ? '<p class="deliberation-figure-mode-reason">' + escHtml(reasonText) + '</p>' : '') +
      '<div class="deliberation-mode-review">' +
        '<label for="deliberation-mode-select">表示分類</label>' +
        '<select id="deliberation-mode-select">' +
          '<option value="">AI候補に戻す</option>' +
          Object.keys(FIGURE_MODE_LABELS).filter(function (key) { return key !== "unknown"; }).map(function (key) {
            return '<option value="' + escHtml(key) + '"' + (reviewed === key ? ' selected' : '') + '>' +
              escHtml(FIGURE_MODE_LABELS[key]) + '</option>';
          }).join("") +
        '</select>' +
        '<button id="deliberation-mode-save" type="button">保存</button>' +
        '<span id="deliberation-mode-save-status" role="status"></span>' +
        '<button id="deliberation-figure-reanalyze" type="button">AIで図を再解析</button>' +
        '<span id="deliberation-figure-reanalyze-status" role="status"></span>' +
      '</div>' +
      _figureFocusControlsHtml();
    if (mode === "functional_diagram") return header + _functionalDiagramHtml(_figureAnalysis(fields, mode), false);
    if (mode === "data_plot") return header + _dataPlotHtml(_figureAnalysis(fields, mode), false);
    if (mode === "descriptive_image") return header + _descriptiveImageHtml(_figureAnalysis(fields, mode), false);
    if (mode === "mixed") {
      var functional = _figureAnalysis(fields, "functional_diagram");
      var plot = _figureAnalysis(fields, "data_plot");
      var descriptive = _figureAnalysis(fields, "descriptive_image");
      return header + '<p class="deliberation-figure-empty">複数の表現を含む図です。取得できた解析を種類ごとに示します。</p>' +
        _mixedPanelsHtml(fields) +
        (functional ? _functionalDiagramHtml(functional, true) : '') +
        (plot ? _dataPlotHtml(plot, true) : '') +
        (descriptive ? _descriptiveImageHtml(descriptive, true) : '') +
        (!functional && !plot && !descriptive ? _descriptiveImageHtml(null, true) : '');
    }
    var unknownProfile = fields.analysis_profile || {};
    var unknownSummary = _itemText(unknownProfile.summary || unknownProfile.description);
    return header + '<p class="deliberation-figure-empty">図の種類を判定できませんでした。原図・caption・周辺本文を確認しながら質問できます。</p>' +
      (unknownSummary ? '<div class="deliberation-figure-overall"><span>取得できた説明</span><strong>' + escHtml(unknownSummary) + '</strong></div>' : '');
  }

  function _figureWorkspaceHtml(decomposition, positioning) {
    var fields = decomposition.fields || {};
    return '<div class="deliberation-figure-workspace">' +
      '<div class="deliberation-figure-image-card">' +
        '<div class="deliberation-figure-image-toolbar"><span>原図</span>' +
          '<button id="deliberation-figure-expand" type="button" disabled>拡大表示</button></div>' +
        '<div class="deliberation-figure-image-stage" data-figure-bbox="' +
          escHtml(JSON.stringify(fields.bbox || fields.figure_bbox || null)) + '">' +
          '<div id="deliberation-figure-image-status">画像を読み込み中...</div>' +
          '<div id="deliberation-figure-image-canvas" class="deliberation-figure-image-canvas" hidden>' +
            '<img id="deliberation-figure-image" alt="' + escHtml(decomposition.label || "検討対象の図") + '">' +
            '<div id="deliberation-figure-overlays" class="deliberation-figure-overlays"></div>' +
            '<div id="deliberation-figure-focus-layer" class="deliberation-figure-focus-layer"></div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div id="deliberation-figure-mode-container">' + _figureModeHtml(fields) + '</div>' +
      '<details class="deliberation-figure-raw"><summary>抽出データ・根拠を見る</summary>' +
        '<div class="deliberation-figure-raw-body">' + _fieldsHtml(fields) + _notesHtml(decomposition.notes) + '</div>' +
      '</details>' +
      _positioningHtml(positioning) +
      '<div id="deliberation-figure-lightbox" class="deliberation-figure-lightbox" aria-hidden="true">' +
        '<button id="deliberation-figure-lightbox-close" type="button" aria-label="拡大表示を閉じる">&times;</button>' +
        '<img id="deliberation-figure-lightbox-image" alt="' + escHtml(decomposition.label || "検討対象の図") + '">' +
      '</div>' +
    '</div>';
  }

  function _renderModalBody(data) {
    var body = document.getElementById("deliberation-modal-body");
    if (!body) return;
    var decomposition = data.decomposition || {};
    var typeLabel = ELEMENT_TYPE_LABELS[decomposition.element_type] || decomposition.element_type || "";
    var isFigure = decomposition.element_type === "figure";
    body.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">' +
        '<span class="admin-status" style="background:var(--color-background-info);color:var(--color-text-info)">' +
          escHtml(typeLabel) +
        '</span>' +
        '<h4 style="margin:0;font-size:15px;color:var(--color-text-primary)">' + escHtml(decomposition.label || "") + '</h4>' +
      '</div>' +
      (isFigure ? _figureWorkspaceHtml(decomposition, data.positioning) :
        '<div style="margin-bottom:6px">' +
          '<div style="font-size:12.5px;font-weight:600;color:var(--color-text-secondary);margin-bottom:4px">内訳</div>' +
          _fieldsHtml(decomposition.fields) +
        '</div>' +
        _notesHtml(decomposition.notes) +
        _positioningHtml(data.positioning)) +
      _identityLinksSectionHtml() +
      _standardizationSectionHtml(decomposition.element_type);
    if (isFigure) {
      _bindFigureImageActions();
      _bindFigureContextActions();
      _bindFigureModeReview(decomposition);
      _bindFigureFocusDrawing();
      _bindFigureReanalysis(decomposition);
      _loadFigureImage(decomposition);
    }
  }

  function _figureImagePath(decomposition) {
    var fields = decomposition.fields || {};
    var path = fields.image_url || decomposition.image_url || "";
    var documentId = fields.document_id || decomposition.document_id ||
      (chatState.ref && chatState.ref.documentId);
    var figureId = decomposition.element_id || (chatState.ref && chatState.ref.elementId);
    if (!path && documentId && figureId) {
      path = "/admin/documents/" + encodeURIComponent(documentId) + "/figures/" +
        encodeURIComponent(figureId) + "/image";
    }
    // apiFetchRaw は内部で /api を付加するため、overview が返す公開形式から剥がす。
    if (path.indexOf("/api/") === 0) path = path.substring(4);
    // 認証情報を意図しない外部 URL へ送らない。契約外 URL は fail-soft 表示にする。
    if (path.indexOf("/admin/") !== 0) return "";
    return path;
  }

  function _setFigureLightbox(open) {
    var lightbox = document.getElementById("deliberation-figure-lightbox");
    if (!lightbox) return;
    lightbox.classList.toggle("is-open", !!open);
    lightbox.setAttribute("aria-hidden", open ? "false" : "true");
    if (open) {
      var close = document.getElementById("deliberation-figure-lightbox-close");
      if (close) close.focus();
    }
  }

  function _bindFigureImageActions() {
    var expand = document.getElementById("deliberation-figure-expand");
    var image = document.getElementById("deliberation-figure-image");
    var lightbox = document.getElementById("deliberation-figure-lightbox");
    var close = document.getElementById("deliberation-figure-lightbox-close");
    if (expand) expand.addEventListener("click", function () { _setFigureLightbox(true); });
    if (image) image.addEventListener("click", function () { _setFigureLightbox(true); });
    if (close) close.addEventListener("click", function () { _setFigureLightbox(false); });
    if (lightbox) lightbox.addEventListener("click", function (event) {
      if (event.target === lightbox) _setFigureLightbox(false);
    });
  }

  function _updateSelectedContextUi() {
    var bar = document.getElementById("deliberation-chat-context");
    var label = document.getElementById("deliberation-chat-context-label");
    if (!bar || !label) return;
    if (!chatState.selectedContext) {
      bar.hidden = true;
      label.textContent = "";
      return;
    }
    bar.hidden = false;
    label.textContent = chatState.selectedContext.label;
  }

  function _activateFigureContext(button) {
    var kind = button.getAttribute("data-deliberation-context-kind");
    var id = button.getAttribute("data-deliberation-context-id");
    Array.prototype.forEach.call(document.querySelectorAll("[data-deliberation-context-kind]"), function (candidate) {
      var selected = candidate.getAttribute("data-deliberation-context-kind") === kind &&
        candidate.getAttribute("data-deliberation-context-id") === id;
      candidate.classList.toggle("is-selected", selected);
      candidate.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-deliberation-connection]"), function (connection) {
      var linked = (connection.getAttribute("data-deliberation-connection") || "").split("|");
      connection.classList.toggle("is-related", kind === "part" && linked.indexOf(id) !== -1);
    });
    chatState.selectedContext = {
      kind: kind,
      id: id,
      label: button.getAttribute("data-deliberation-context-label")
    };
    _updateSelectedContextUi();
  }

  function _bindFigureContextActions(root) {
    root = root || document;
    Array.prototype.forEach.call(root.querySelectorAll("[data-deliberation-context-kind]"), function (button) {
      if (button.getAttribute("data-deliberation-context-bound") === "true") return;
      button.setAttribute("data-deliberation-context-bound", "true");
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", function () { _activateFigureContext(button); });
    });
  }

  function _bboxValues(value) {
    if (!value) return null;
    if (value.bbox) return _bboxValues(value.bbox);
    var values;
    if (Array.isArray(value) && value.length >= 4) {
      values = [value[0], value[1], value[2], value[3]];
    } else if (typeof value === "object") {
      if (value.x0 !== undefined) values = [value.x0, value.y0, value.x1, value.y1];
      else if (value.left !== undefined) values = [value.left, value.top, value.right, value.bottom];
      else if (value.x !== undefined && value.width !== undefined) {
        values = [value.x, value.y, Number(value.x) + Number(value.width), Number(value.y) + Number(value.height)];
      }
    }
    if (!values) return null;
    values = values.map(Number);
    if (values.some(function (number) { return !isFinite(number); }) || values[2] <= values[0] || values[3] <= values[1]) return null;
    return values;
  }

  function _relativeBbox(target, figure) {
    var bbox = _bboxValues(target);
    if (!bbox) return null;
    if (bbox.every(function (number) { return number >= 0 && number <= 1; })) return bbox;
    var figureBbox = _bboxValues(figure);
    if (!figureBbox) return null;
    var width = figureBbox[2] - figureBbox[0];
    var height = figureBbox[3] - figureBbox[1];
    var relative = [
      (bbox[0] - figureBbox[0]) / width,
      (bbox[1] - figureBbox[1]) / height,
      (bbox[2] - figureBbox[0]) / width,
      (bbox[3] - figureBbox[1]) / height
    ];
    relative = relative.map(function (number) { return Math.max(0, Math.min(1, number)); });
    if (relative[2] <= relative[0] || relative[3] <= relative[1]) return null;
    return relative;
  }

  function _renderFigureOverlays() {
    var stage = document.querySelector(".deliberation-figure-image-stage");
    var layer = document.getElementById("deliberation-figure-overlays");
    if (!stage || !layer) return;
    layer.innerHTML = "";
    var figureBbox = null;
    try { figureBbox = JSON.parse(stage.getAttribute("data-figure-bbox") || "null"); } catch (e) { /* noop */ }
    var seen = {};
    Array.prototype.forEach.call(document.querySelectorAll(".deliberation-context-target[data-deliberation-bbox]"), function (source, index) {
      var raw;
      try { raw = JSON.parse(source.getAttribute("data-deliberation-bbox")); } catch (e) { return; }
      var bbox = _relativeBbox(raw, figureBbox);
      if (!bbox) return;
      var key = source.getAttribute("data-deliberation-context-kind") + ":" + source.getAttribute("data-deliberation-context-id");
      if (seen[key]) return;
      seen[key] = true;
      var marker = document.createElement("button");
      marker.type = "button";
      marker.className = "deliberation-figure-overlay deliberation-context-target";
      marker.setAttribute("data-deliberation-context-kind", source.getAttribute("data-deliberation-context-kind"));
      marker.setAttribute("data-deliberation-context-id", source.getAttribute("data-deliberation-context-id"));
      marker.setAttribute("data-deliberation-context-label", source.getAttribute("data-deliberation-context-label"));
      marker.setAttribute("aria-label", source.getAttribute("data-deliberation-context-label") + "を選択");
      marker.title = source.getAttribute("data-deliberation-context-label");
      marker.style.left = (bbox[0] * 100) + "%";
      marker.style.top = (bbox[1] * 100) + "%";
      marker.style.width = ((bbox[2] - bbox[0]) * 100) + "%";
      marker.style.height = ((bbox[3] - bbox[1]) * 100) + "%";
      marker.textContent = String(index + 1);
      layer.appendChild(marker);
    });
    _bindFigureContextActions(layer);
    if (chatState.selectedContext) {
      var selected = null;
      Array.prototype.some.call(document.querySelectorAll("[data-deliberation-context-kind]"), function (candidate) {
        if (candidate.getAttribute("data-deliberation-context-kind") === chatState.selectedContext.kind &&
            candidate.getAttribute("data-deliberation-context-id") === chatState.selectedContext.id) {
          selected = candidate;
          return true;
        }
        return false;
      });
      if (selected) _activateFigureContext(selected);
    }
  }

  function _bindFigureModeReview(decomposition) {
    var select = document.getElementById("deliberation-mode-select");
    var save = document.getElementById("deliberation-mode-save");
    var status = document.getElementById("deliberation-mode-save-status");
    var fields = (decomposition && decomposition.fields) || {};
    var documentId = fields.document_id || decomposition.document_id || (chatState.ref && chatState.ref.documentId);
    var figureId = decomposition.element_id || (chatState.ref && chatState.ref.elementId);
    if (!select || !save) return;
    if (!documentId || !figureId) {
      save.disabled = true;
      if (status) status.textContent = "保存先を特定できません";
      return;
    }
    save.addEventListener("click", function () {
      save.disabled = true;
      if (status) status.textContent = "保存中...";
      apiFetch(
        "/admin/documents/" + encodeURIComponent(documentId) + "/figures/" + encodeURIComponent(figureId) + "/presentation-mode",
        { method: "PATCH", body: JSON.stringify({ presentation_mode: select.value || null }) }
      )
        .then(_parseJsonResponse)
        .then(function () {
          if (status) status.textContent = "保存しました";
          chatState.selectedContext = null;
          _updateSelectedContextUi();
          return _reloadOverview();
        })
        .catch(function (err) {
          if (status) status.textContent = (err && err.detail) || "保存に失敗しました";
        })
        .then(function () { save.disabled = false; });
    });
  }

  // 教員指示（hint_text / focusBbox）から reanalyze リクエストの body を組み立てる。
  // 両方とも無ければ null を返し、呼び出し側は従来どおり body なしで POST する
  // （後方互換。設計書 §4-1「body なし / 両フィールド null = 従来動作」）。
  function _figureReanalyzeGuidancePayload() {
    var hint = (figureImageState.hintText || "").trim();
    var focusBbox = figureImageState.focusBbox;
    var payload = {};
    var hasGuidance = false;
    if (hint) {
      payload.hint_text = hint;
      hasGuidance = true;
    }
    if (focusBbox) {
      payload.focus_bbox = focusBbox;
      hasGuidance = true;
    }
    return hasGuidance ? payload : null;
  }

  function _bindFigureReanalysis(decomposition) {
    var button = document.getElementById("deliberation-figure-reanalyze");
    var status = document.getElementById("deliberation-figure-reanalyze-status");
    var fields = (decomposition && decomposition.fields) || {};
    var documentId = fields.document_id || decomposition.document_id || (chatState.ref && chatState.ref.documentId);
    var figureId = decomposition.element_id || (chatState.ref && chatState.ref.elementId);
    if (!button) return;
    if (!documentId || !figureId) {
      button.disabled = true;
      if (status) status.textContent = "再解析先を特定できません";
      return;
    }
    button.addEventListener("click", function () {
      button.disabled = true;
      if (status) status.textContent = "原図を解析中...";
      var guidance = _figureReanalyzeGuidancePayload();
      var requestOptions = guidance ?
        { method: "POST", body: JSON.stringify(guidance) } :
        { method: "POST" };
      apiFetch(
        "/admin/documents/" + encodeURIComponent(documentId) + "/figures/" +
          encodeURIComponent(figureId) + "/reanalyze",
        requestOptions
      )
        .then(_parseJsonResponse)
        .then(function (data) {
          // GF3: 指示した要素が見つからなかった場合も guidance_note の事実文で
          // 教員はここで即座に知れる（無言で無視しない）。
          var note = data && data.guidance_note;
          if (status) {
            status.textContent = note ?
              "構造化候補を作成しました。AIの応答: " + note.substring(0, 120) :
              "構造化候補を作成しました。内容を確認して確定してください";
          }
          chatState.selectedContext = null;
          _updateSelectedContextUi();
          return _reloadOverview();
        })
        .then(function () {
          _loadAnnotations("figure", figureId, documentId);
        })
        .catch(function (err) {
          if (status) status.textContent = (err && err.detail) || "図を再解析できませんでした";
        })
        .then(function () { button.disabled = false; });
    });
  }

  function _loadFigureImage(decomposition) {
    var path = _figureImagePath(decomposition);
    var requestId = ++figureImageState.requestId;
    var status = document.getElementById("deliberation-figure-image-status");
    if (!path) {
      if (status) status.textContent = "原図の取得先がありません";
      return;
    }
    apiFetchRaw(path, { _noJson: true })
      .then(function (res) {
        if (!res.ok) throw new Error("image load failed");
        return res.blob();
      })
      .then(function (blob) {
        if (requestId !== figureImageState.requestId || !document.getElementById("deliberation-modal")) return;
        var url = URL.createObjectURL(blob);
        figureImageState.objectUrls.push(url);
        var image = document.getElementById("deliberation-figure-image");
        var largeImage = document.getElementById("deliberation-figure-lightbox-image");
        var expand = document.getElementById("deliberation-figure-expand");
        var canvas = document.getElementById("deliberation-figure-image-canvas");
        if (image) {
          image.addEventListener("load", _renderFigureOverlays);
          image.src = url;
        }
        if (canvas) canvas.hidden = false;
        if (largeImage) largeImage.src = url;
        if (expand) expand.disabled = false;
        if (status) status.remove();
      })
      .catch(function () {
        if (requestId !== figureImageState.requestId) return;
        var currentStatus = document.getElementById("deliberation-figure-image-status");
        if (currentStatus) currentStatus.textContent = "原図を表示できません。メタデータと対話は引き続き利用できます。";
      });
  }

  // ── Phase W-β: 同一性リンク（identity-links）セクション ─────────────────
  // 「この要素は別の資料・共通部品と同じものだ」という対応づけの一覧。候補
  // （candidate）・確定（confirmed）・却下（rejected）を明示区別し（G2-W）、
  // 確定・却下は教員のみが行う（KN-3）。インスタンス側の表記は書き換えない（KN-2）。
  function _identityLinksSectionHtml() {
    return '<div class="deliberation-identity-links-wrap" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 6px;font-size:14px;color:var(--color-text-primary)">同一性リンク</h4>' +
      '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px">' +
        'この要素が別の資料・共通部品と同じものだという対応づけです。候補は AI 対話または教員が作成し、' +
        '確定・却下は教員のみが行います。既存の表記は書き換えません（リンクの追加のみ）。' +
      '</p>' +
      '<div id="deliberation-identity-links"><p class="deliberation-identity-empty">読み込み中...</p></div>' +
    '</div>';
  }

  function _identityLinkRowHtml(link) {
    link = link || {};
    var statusLabel = IDENTITY_LINK_STATUS_LABELS[link.status] || link.status || "";
    var localExpr = link.local_expression || {};
    var exprLabel = localExpr.label || localExpr.notation || "";
    var pending = link.status === "candidate";
    return '<div class="deliberation-identity-link-row" data-identity-link-id="' + escHtml(link.id) + '">' +
      '<span class="deliberation-annotation-status ' + (link.status === "confirmed" ? "committed" : (link.status === "rejected" ? "dismissed" : "")) + '">' +
        escHtml(statusLabel) +
      '</span>' +
      (exprLabel ? '<div class="deliberation-annotation-body">' + escHtml(exprLabel) + '</div>' : '') +
      (link.shared_part_id ? '<div class="deliberation-annotation-reason">共通部品: ' + escHtml(link.shared_part_id) + '</div>' : '') +
      (link.instance_element_type
        ? '<div class="deliberation-annotation-reason">インスタンス: ' + escHtml(link.instance_element_type) +
          (link.instance_document_id ? ' / ' + escHtml(link.instance_document_id) : '') + '</div>'
        : '') +
      (link.reason ? '<div class="deliberation-annotation-reason">' + escHtml(link.reason) + '</div>' : '') +
      (link.confidence_label ? '<span class="deliberation-annotation-confidence">' + escHtml(link.confidence_label) + '</span>' : '') +
      (pending
        ? '<div class="deliberation-annotation-actions">' +
            '<button type="button" class="deliberation-annotation-btn commit" data-identity-action="confirm">確定</button>' +
            '<button type="button" class="deliberation-annotation-btn dismiss" data-identity-action="reject">却下</button>' +
          '</div>'
        : '') +
      // 確定・却下失敗時の表示先。catch 側が引く専用クラス（スタイルは annotation-error を流用）
      '<div class="deliberation-annotation-error deliberation-identity-link-error" style="display:none"></div>' +
    '</div>';
  }

  function _bindIdentityLinkActions(root) {
    Array.prototype.forEach.call(root.querySelectorAll("[data-identity-action]"), function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest(".deliberation-identity-link-row");
        if (!row) return;
        _decideIdentityLink(row.getAttribute("data-identity-link-id"), btn.getAttribute("data-identity-action"), row);
      });
    });
  }

  function _decideIdentityLink(linkId, action, row) {
    if (!linkId) return;
    var buttons = row.querySelectorAll("[data-identity-action]");
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    apiFetch("/admin/deliberation/identity-links/" + encodeURIComponent(linkId) + "/" + action, { method: "POST" })
      .then(_parseJsonResponse)
      .then(function (link) {
        var wrapper = document.createElement("div");
        wrapper.innerHTML = _identityLinkRowHtml(link || {});
        var newRow = wrapper.firstChild;
        if (row.parentNode) row.parentNode.replaceChild(newRow, row);
        _bindIdentityLinkActions(newRow);
      })
      .catch(function (err) {
        var errEl = row.querySelector(".deliberation-identity-link-error");
        var message = (err && err.detail) || "操作に失敗しました（既に確定・却下済みの可能性があります）";
        if (errEl) {
          errEl.style.display = "";
          errEl.innerHTML = escHtml(message);
        }
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      });
  }

  function _renderIdentityLinks(links, hiddenCount) {
    var container = document.getElementById("deliberation-identity-links");
    if (!container) return;
    links = links || [];
    if (!links.length) {
      container.innerHTML = '<p class="deliberation-identity-empty">同一性リンクはまだありません。</p>';
    } else {
      container.innerHTML = links.map(_identityLinkRowHtml).join("");
    }
    if (hiddenCount) {
      container.innerHTML += '<p class="deliberation-identity-hidden">閲覧権限のない資料に由来する ' +
        escHtml(String(hiddenCount)) + ' 件は非表示です。</p>';
    }
    _bindIdentityLinkActions(container);
  }

  // ref = chatState.ref（elementType/elementId/documentId）。shared_part は domain-scoped
  // なので別エンドポイント（GET /shared-parts/{id}/identity-links）を使う（§5 W5）。
  function _loadIdentityLinks(ref) {
    ref = ref || {};
    var container = document.getElementById("deliberation-identity-links");
    if (!container) return;
    var path;
    if (ref.elementType === "shared_part") {
      path = "/admin/deliberation/shared-parts/" + encodeURIComponent(ref.elementId) + "/identity-links";
    } else {
      path = "/admin/deliberation/elements/" + encodeURIComponent(ref.elementType) + "/" + encodeURIComponent(ref.elementId) + "/identity-links";
      if (ref.elementType === "equation" && ref.documentId) {
        path += "?document_id=" + encodeURIComponent(ref.documentId);
      }
    }
    apiFetch(path)
      .then(_parseJsonResponse)
      .then(function (data) {
        _renderIdentityLinks((data && data.identity_links) || [], data && data.hidden_count);
      })
      .catch(function () {
        // fail-soft: 同一性リンクの読み込みに失敗しても内訳・対話は継続できる
        container.innerHTML = '<p class="deliberation-identity-empty">同一性リンクの読み込みに失敗しました。</p>';
      });
  }

  // ── Phase S: 標準化度の評価（三角測量 worker の手動起動）。shared_part（共通部品）
  // にのみ表示する。評価結果は element_annotations(kind='standardization') の候補として
  // 既存の候補注釈カード（対話ペイン下）に現れる。自動確定はしない（教員の commit のみ）。
  function _standardizationSectionHtml(elementType) {
    if (elementType !== "shared_part") return "";
    return '<div class="deliberation-standardization" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 6px;font-size:14px;color:var(--color-text-primary)">標準化度</h4>' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<button type="button" id="deliberation-standardization-assess" class="deliberation-chat-send" style="padding:4px 10px;font-size:12px">標準化度を評価</button>' +
        '<span id="deliberation-standardization-note" style="font-size:12px;color:var(--color-text-tertiary)"></span>' +
      '</div>' +
      '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:6px 0 0">' +
        '評価は三角測量（LLM 事前知識・ライブラリ内の類似・コーパス内の反復）による候補です。' +
        '確定は下の候補カード（対話ペイン下）から教員が行います。自動では確定しません。' +
      '</p>' +
    '</div>';
  }

  function _bindStandardizationAssessButton(ref) {
    var btn = document.getElementById("deliberation-standardization-assess");
    if (!btn || !ref || ref.elementType !== "shared_part") return;
    btn.addEventListener("click", function () {
      btn.disabled = true;
      apiFetch("/admin/deliberation/shared-parts/" + encodeURIComponent(ref.elementId) + "/standardization/assess", {
        method: "POST"
      })
        .then(_parseJsonResponse)
        .then(function (data) {
          var note = document.getElementById("deliberation-standardization-note");
          if (note) note.textContent = (data && data.note) || "";
          // 評価は非同期（daemon thread）。間に合えば候補一覧に反映されるが、
          // 反映前でもエラーにはしない（W4: 後でモーダルを開き直しても拾える）。
          _loadAnnotations(ref.elementType, ref.elementId, ref.documentId);
        })
        .catch(function (err) {
          var note = document.getElementById("deliberation-standardization-note");
          if (note) note.textContent = (err && err.detail) || "評価の開始に失敗しました";
        })
        .then(function () {
          btn.disabled = false;
        });
    });
  }

  function _renderError(status) {
    var body = document.getElementById("deliberation-modal-body");
    if (!body) return;
    var message = "内訳の読み込みに失敗しました";
    if (status === 404) message = "この要素は見つかりませんでした";
    else if (status === 422) message = "この要素の指定が不正です（equation は document_id が必要です）";
    body.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">' + escHtml(message) + '</div>';
  }

  // ── 面③ 対話ペイン: 共通ユーティリティ ──────────────────────────────────

  // POST 系レスポンスを共通に処理する。res.ok なら body を返し、そうでなければ
  // detail（サーバ側の事実文。429=対話上限・422=未対応 kind 等）を保持した Error を投げる。
  function _parseJsonResponse(res) {
    return res.json().catch(function () { return {}; }).then(function (body) {
      if (!res.ok) {
        var err = new Error((body && body.detail) || ("status " + res.status));
        err.status = res.status;
        err.detail = body && body.detail;
        throw err;
      }
      return body;
    });
  }

  function _chatEmptyStateHtml() {
    return '<div class="deliberation-chat-empty">この要素について質問できます。まだ対話はありません。</div>';
  }

  function _appendChatMessage(role, text) {
    var container = document.getElementById("deliberation-chat-messages");
    if (!container) return;
    var empty = container.querySelector(".deliberation-chat-empty");
    if (empty) empty.remove();
    var div = document.createElement("div");
    div.className = "deliberation-chat-msg " + (role === "user" ? "user" : "ai");
    div.innerHTML = escHtml(text).replace(/\n/g, "<br>");
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  // degraded / 429 等、断定・煽りを足さずサーバの事実文だけを添える注記行。
  function _appendChatNote(text) {
    var container = document.getElementById("deliberation-chat-messages");
    if (!container) return;
    var div = document.createElement("div");
    div.className = "deliberation-chat-note";
    div.innerHTML = escHtml(text);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  function _setChatSending(flag) {
    chatState.sending = flag;
    var btn = document.getElementById("deliberation-chat-send");
    var input = document.getElementById("deliberation-chat-input");
    if (btn) {
      btn.disabled = flag;
      btn.textContent = flag ? "送信中…" : "送信";
    }
    if (input) input.disabled = flag;
  }

  // セッションは最初の送信時にだけ作成する（W6）。モーダルを開いた時点では呼ばない
  // （openElement からは直接呼ばれず、_sendChatMessage 経由でのみ到達する）。
  function _ensureChatSession() {
    if (chatState.sessionId) return Promise.resolve(chatState.sessionId);
    var ref = chatState.ref || {};
    var body = {
      scope: ref.elementType === "shared_part" ? "domain" : "document",
      element_type: ref.elementType,
      element_id: ref.elementId
    };
    if (ref.documentId) body.document_id = ref.documentId;
    if (ref.title) body.title = ref.title;
    return apiFetch("/admin/deliberation/sessions", {
      method: "POST",
      body: JSON.stringify(body)
    }).then(_parseJsonResponse).then(function (data) {
      var session = (data && data.session) || {};
      chatState.sessionId = session.id;
      return chatState.sessionId;
    });
  }

  function _sendChatMessage() {
    if (chatState.sending) return;
    var input = document.getElementById("deliberation-chat-input");
    var text = input ? input.value.trim() : "";
    if (!text) return;
    input.value = "";
    _appendChatMessage("user", text);
    _setChatSending(true);
    _ensureChatSession()
      .then(function (sessionId) {
        var messageBody = { content: text };
        if (chatState.selectedContext) {
          // content は変更せず、選択中の機能・観測・被写体を補助文脈として渡す。
          // backend が未対応の期間も通常の本文契約を維持できる形にする。
          messageBody.selected_context = chatState.selectedContext;
        }
        return apiFetch(
          "/admin/deliberation/sessions/" + encodeURIComponent(sessionId) + "/messages",
          { method: "POST", body: JSON.stringify(messageBody) }
        );
      })
      .then(_parseJsonResponse)
      .then(function (data) {
        _appendChatMessage("ai", (data && data.reply) || "");
        if (data && data.degraded) {
          _appendChatNote("（AI 応答は生成できませんでした）");
        }
        _renderAnnotationCards((data && data.annotations) || [], { append: true });
      })
      .catch(function (err) {
        var message;
        if (err && err.status === 429) {
          // 429 = 対話上限。サーバの detail（事実文）をそのまま出す（断定・煽りを足さない）。
          message = err.detail || "本日の対話上限に達しました";
        } else {
          message = (err && err.detail) || (err && err.message) || "送信に失敗しました";
        }
        _appendChatNote(message);
      })
      .then(function () {
        _setChatSending(false);
      });
  }

  // ── 面③ 候補注釈カード ───────────────────────────────────────────────

  function _annotationStatusHtml(status) {
    if (status === "committed") return '<span class="deliberation-annotation-status committed">確定済み</span>';
    if (status === "dismissed") return '<span class="deliberation-annotation-status dismissed">却下</span>';
    return "";
  }

  function _annotationBodyText(body) {
    if (typeof body === "string") return body;
    if (body && typeof body === "object" && body.text) return String(body.text);
    try {
      return JSON.stringify(body);
    } catch (e) {
      return String(body);
    }
  }

  function _structuredFigureCandidateHtml(body) {
    if (!body || typeof body !== "object" || body.candidate_type !== "figure_analysis") return "";
    var mode = _normalizedFigureMode(body.presentation_mode || body.suggested_mode);
    var analysis = body.analysis_profile || {};
    var detail = "";
    if (mode === "functional_diagram") detail = _functionalDiagramHtml(analysis, true);
    else if (mode === "data_plot") detail = _dataPlotHtml(analysis, true);
    else if (mode === "descriptive_image") detail = _descriptiveImageHtml(analysis, true);
    else if (mode === "mixed") {
      detail = '<section class="deliberation-figure-analysis nested"><h5>複合図</h5>' +
        _mixedPanelsHtml({ analysis_profile: analysis }) + '</section>';
    }
    if (!detail) return "";
    return '<details class="deliberation-figure-candidate-preview" open>' +
      '<summary>再解析で検出した構成を確認（' + escHtml(FIGURE_MODE_LABELS[mode]) + '）</summary>' +
      detail + '</details>';
  }

  // カード DOM の中身を注釈データから（再）構築する。commit/dismiss 成功後の
  // ステータス反映にも使う（新規カード生成と同じ経路で描画を一本化する）。
  function _fillAnnotationCard(card, ann) {
    ann = ann || {};
    var kindLabel = ANNOTATION_KIND_LABELS[ann.kind] || ann.kind || "";
    var isPending = ann.status !== "committed" && ann.status !== "dismissed";
    var canCommit = ann.commit_supported !== false;
    card.setAttribute("data-annotation-id", ann.id || "");
    card.innerHTML =
      '<div class="deliberation-annotation-kind">' +
        escHtml(kindLabel) +
        (ann.confidence_label
          ? ' <span class="deliberation-annotation-confidence">' + escHtml(ann.confidence_label) + '</span>'
          : '') +
      '</div>' +
      '<div class="deliberation-annotation-body">' + escHtml(_annotationBodyText(ann.body)) + '</div>' +
      _structuredFigureCandidateHtml(ann.body) +
      (ann.reason ? '<div class="deliberation-annotation-reason">' + escHtml(ann.reason) + '</div>' : '') +
      (isPending && !canCommit && ann.commit_note
        ? '<div class="deliberation-annotation-note">' + escHtml(ann.commit_note) + '</div>' : '') +
      '<div class="deliberation-annotation-error" style="display:none"></div>' +
      _annotationStatusHtml(ann.status) +
      (isPending
        ? '<div class="deliberation-annotation-actions">' +
            (canCommit ? '<button type="button" class="deliberation-annotation-btn commit" data-action="commit">確定</button>' : '') +
            '<button type="button" class="deliberation-annotation-btn dismiss" data-action="dismiss">却下</button>' +
          '</div>'
        : '');

    var commitBtn = card.querySelector('[data-action="commit"]');
    var dismissBtn = card.querySelector('[data-action="dismiss"]');
    if (commitBtn) {
      commitBtn.addEventListener("click", function () {
        _decideAnnotation(ann.id, "commit", card, commitBtn, dismissBtn);
      });
    }
    if (dismissBtn) {
      dismissBtn.addEventListener("click", function () {
        _decideAnnotation(ann.id, "dismiss", card, commitBtn, dismissBtn);
      });
    }
    _bindFigureContextActions(card);
  }

  function _buildAnnotationCard(ann) {
    var card = document.createElement("div");
    card.className = "deliberation-annotation-card";
    _fillAnnotationCard(card, ann);
    return card;
  }

  // opts.append=false（既定）なら一覧を置き換える（モーダル再オープン時の復元・W4）。
  // opts.append=true なら既存カードの下に追加する（対話1ターンの新規候補）。
  function _renderAnnotationCards(annotations, opts) {
    opts = opts || {};
    var container = document.getElementById("deliberation-chat-annotations");
    if (!container) return;
    if (!opts.append) container.innerHTML = "";
    (annotations || []).forEach(function (ann) {
      container.appendChild(_buildAnnotationCard(ann));
    });
    if (chatState.ref && chatState.ref.elementType === "figure") _renderFigureOverlays();
  }

  function _decideAnnotation(id, action, card, commitBtn, dismissBtn) {
    if (!id) return;
    if (commitBtn) commitBtn.disabled = true;
    if (dismissBtn) dismissBtn.disabled = true;
    var path = "/admin/deliberation/annotations/" + encodeURIComponent(id) + "/" + action;
    apiFetch(path, { method: "POST" })
      .then(_parseJsonResponse)
      .then(function (data) {
        var updatedAnn = (data && data.annotation) || {};
        _fillAnnotationCard(card, updatedAnn);
        if (action === "commit" && chatState.ref && chatState.ref.elementType === "figure") {
          return _reloadOverview().then(function () {
            _loadAnnotations("figure", chatState.ref.elementId, chatState.ref.documentId);
          });
        }
        // figure 以外の要素型（theory_component/theory_claim/equation/shared_part）は
        // _reloadOverview を呼ばないため、identity 注釈のコミットでは同一性リンク一覧を
        // ここで明示的に再読込する（さもないと確定直後も candidate のまま表示され続ける）。
        if (action === "commit" && updatedAnn.kind === "identity" && chatState.ref) {
          _loadIdentityLinks(chatState.ref);
        }
      })
      .catch(function (err) {
        var errEl = card.querySelector(".deliberation-annotation-error");
        var message = (err && err.detail) ||
          (action === "commit" ? "この候補は確定できませんでした（このコミットは未対応の可能性があります）" : "却下できませんでした");
        if (errEl) {
          errEl.style.display = "";
          errEl.innerHTML = escHtml(message);
        }
        if (action === "commit") {
          // commit 失敗（422=このkindのコミット未対応 等）は却下のみ可能にする。
          if (dismissBtn) dismissBtn.disabled = false;
        } else {
          // dismiss 失敗は両方を再試行可能にする。
          if (commitBtn) commitBtn.disabled = false;
          if (dismissBtn) dismissBtn.disabled = false;
        }
      });
  }

  // モーダル再オープン時に既存注釈（candidate/committed/dismissed すべて）を復元する（W4）。
  function _loadAnnotations(elementType, elementId, documentId) {
    var path = "/admin/deliberation/elements/" + encodeURIComponent(elementType) + "/" + encodeURIComponent(elementId) + "/annotations";
    if (elementType === "equation" && documentId) {
      path += "?document_id=" + encodeURIComponent(documentId);
    }
    apiFetch(path)
      .then(_parseJsonResponse)
      .then(function (data) {
        _renderAnnotationCards((data && data.annotations) || [], { append: false });
      })
      .catch(function () {
        // 既存注釈の復元に失敗しても対話自体は継続できる（fail-soft）。
      });
  }

  function _overviewPath(ref) {
    ref = ref || {};
    var path = "/admin/deliberation/elements/" + encodeURIComponent(ref.elementType) + "/" +
      encodeURIComponent(ref.elementId) + "/overview";
    if (ref.elementType === "equation" && ref.documentId) {
      path += "?document_id=" + encodeURIComponent(ref.documentId);
    }
    return path;
  }

  function _reloadOverview() {
    var ref = chatState.ref || {};
    return apiFetch(_overviewPath(ref))
      .then(_parseJsonResponse)
      .then(function (data) {
        _resetFigureImageState();
        _renderModalBody(data);
        _bindStandardizationAssessButton(ref);
        _loadIdentityLinks(ref);
        return data;
      });
  }

  // ── 公開 API: openElement ────────────────────────────────────────────
  // opts = { documentId: string|null, title: string|null }
  // equation は document_id が必須（無ければ何もしない。設計書 §2 の equation 一意化の要件）。
  function openElement(elementType, elementId, opts) {
    opts = opts || {};
    if (!deps.apiFetch && !window.apiFetch) return;
    if (elementType === "equation" && !opts.documentId) return;

    _closeModal();
    chatState.ref = {
      elementType: elementType,
      elementId: elementId,
      documentId: opts.documentId || null,
      title: opts.title || null
    };

    var overlay = document.createElement("div");
    overlay.id = "deliberation-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML =
      '<div class="deliberation-modal-dialog">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">深く検討' +
            (opts.title ? ' — ' + escHtml(opts.title) : '') +
          '</h3>' +
          '<button id="deliberation-modal-close" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          'この要素の内訳と位置づけです。表示はすべて既存データの読み出しで、確定済みの判断ではありません。' +
        '</p>' +
        '<div class="deliberation-modal-columns">' +
          '<div class="deliberation-modal-left">' +
            '<div id="deliberation-modal-body">' +
              '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
            '</div>' +
          '</div>' +
          '<div class="deliberation-chat-pane">' +
            '<div class="deliberation-chat-messages" id="deliberation-chat-messages">' +
              _chatEmptyStateHtml() +
            '</div>' +
            '<div class="deliberation-chat-annotations" id="deliberation-chat-annotations"></div>' +
            '<div id="deliberation-chat-context" class="deliberation-chat-context" hidden>' +
              '<span>質問対象: <strong id="deliberation-chat-context-label"></strong></span>' +
              '<button id="deliberation-chat-context-clear" type="button" aria-label="質問対象を解除">解除</button>' +
            '</div>' +
            '<div class="deliberation-chat-inputrow">' +
              '<textarea id="deliberation-chat-input" class="deliberation-chat-input" rows="2" placeholder="この要素について質問..."></textarea>' +
              '<button id="deliberation-chat-send" type="button" class="deliberation-chat-send">送信</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) _closeModal(); });
    document.getElementById("deliberation-modal-close").addEventListener("click", _closeModal);

    var chatInput = document.getElementById("deliberation-chat-input");
    var chatSendBtn = document.getElementById("deliberation-chat-send");
    var chatContextClear = document.getElementById("deliberation-chat-context-clear");
    if (chatSendBtn) chatSendBtn.addEventListener("click", _sendChatMessage);
    if (chatContextClear) chatContextClear.addEventListener("click", function () {
      chatState.selectedContext = null;
      Array.prototype.forEach.call(document.querySelectorAll("[data-deliberation-context-kind]"), function (candidate) {
        candidate.classList.remove("is-selected");
        candidate.setAttribute("aria-pressed", "false");
      });
      Array.prototype.forEach.call(document.querySelectorAll("[data-deliberation-connection]"), function (connection) {
        connection.classList.remove("is-related");
      });
      _updateSelectedContextUi();
    });
    if (chatInput) {
      chatInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
          e.preventDefault();
          _sendChatMessage();
        }
      });
    }

    // 面③の既存候補注釈を復元する（GET のみ・DB 非変更）。対話セッション自体は
    // ここでは作らない（POST /sessions は最初の送信時にのみ _ensureChatSession が呼ぶ）。
    _loadAnnotations(elementType, elementId, opts.documentId);

    apiFetch(_overviewPath(chatState.ref))
      .then(function (res) {
        if (!res.ok) {
          var status = res.status;
          var err = new Error("status " + status);
          err.status = status;
          throw err;
        }
        return res.json();
      })
      .then(function (data) {
        _renderModalBody(data);
        _bindStandardizationAssessButton(chatState.ref);
        _loadIdentityLinks(chatState.ref);
      })
      .catch(function (err) {
        _renderError(err && err.status);
      });
  }

  // ── 要素インベントリ: モーダル DOM / 描画 ───────────────────────────────

  function _closeInventoryModal() {
    var modal = document.getElementById("deliberation-inventory-modal");
    if (modal) modal.remove();
    _resetInventoryState();
  }

  function _inventoryTotalCount(counts) {
    counts = counts || {};
    var total = 0;
    INVENTORY_TYPE_ORDER.forEach(function (key) {
      if (key === "all") return;
      total += counts[key] || 0;
    });
    return total;
  }

  function _inventoryStatusBadgeHtml(deliberation) {
    deliberation = deliberation || {};
    var annotations = deliberation.annotations || {};
    var identityLinks = deliberation.identity_links || {};
    var committed = (annotations.committed || 0) > 0 || (identityLinks.confirmed || 0) > 0;
    var candidate = (annotations.candidate || 0) > 0 || (identityLinks.candidate || 0) > 0;
    // dismissed は意図的に表示しない（設計書 §7: 情報は落とさない=APIは返す、表示だけ抑制）。
    if (committed) return '<span class="deliberation-annotation-status committed">検討済み</span>';
    if (candidate) return '<span class="deliberation-annotation-status">候補あり</span>';
    return '<span class="deliberation-annotation-status dismissed">未検討</span>';
  }

  function _inventoryBadgesHtml(badges) {
    var keys = Object.keys(badges || {});
    if (!keys.length) return "";
    var chips = keys.map(function (key) {
      var value = badges[key];
      if (value === null || value === undefined || value === "") return "";
      return '<span style="font-size:11px;padding:1px 6px;border-radius:8px;' +
        'background:var(--color-background-tertiary,#eeeef0);color:var(--color-text-secondary,#6e6e73)">' +
        escHtml(String(value)) + '</span>';
    }).join("");
    if (!chips) return "";
    return '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px">' + chips + '</div>';
  }

  function _inventorySnippetText(snippet) {
    var text = snippet || "";
    if (text.length > 240) text = text.substring(0, 240) + "…";
    return text;
  }

  function _inventoryCardHtml(el) {
    el = el || {};
    var label = el.label || "(無題)";
    return '<article class="deliberation-annotation-card">' +
      '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
        '<span class="admin-status" style="background:var(--color-background-info);color:var(--color-text-info)">' +
          escHtml(INVENTORY_TYPE_BADGE_LABELS[el.element_type] || el.element_type || "") +
        '</span>' +
        _inventoryStatusBadgeHtml(el.deliberation) +
      '</div>' +
      '<div style="font-size:13.5px;font-weight:600;color:var(--color-text-primary);margin-top:6px">' +
        escHtml(label) +
      '</div>' +
      (el.snippet
        ? '<div style="font-size:12.5px;color:var(--color-text-secondary);margin-top:2px;white-space:pre-wrap;word-break:break-word">' +
            escHtml(_inventorySnippetText(el.snippet)) +
          '</div>'
        : '') +
      _inventoryBadgesHtml(el.badges) +
      '<div style="margin-top:8px">' +
        '<button type="button" class="deliberation-annotation-btn" data-inventory-deliberate="true" ' +
          'data-inventory-element-type="' + escHtml(el.element_type) + '" ' +
          'data-inventory-element-id="' + escHtml(el.element_id) + '" ' +
          'data-inventory-label="' + escHtml(label) + '">深く検討</button>' +
      '</div>' +
    '</article>';
  }

  // 「深く検討」クリック: 既存 openElement をそのまま呼ぶ（非改変・同一モジュール内）。
  // インベントリモーダル（#deliberation-inventory-modal）とは独立 DOM のため、
  // 深く検討モーダルを閉じてもインベントリのフィルタ状態は保持されたまま残る。
  function _bindInventoryDeliberateButtons(root) {
    root.querySelectorAll("[data-inventory-deliberate]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var elementType = btn.getAttribute("data-inventory-element-type");
        var elementId = btn.getAttribute("data-inventory-element-id");
        var label = btn.getAttribute("data-inventory-label");
        openElement(elementType, elementId, {
          documentId: inventoryState.documentId,
          title: label
        });
      });
    });
  }

  function _renderInventoryList() {
    var list = document.getElementById("deliberation-inventory-list");
    if (!list) return;
    var data = inventoryState.data || {};
    var elements = data.elements || [];
    var typeFilter = inventoryState.typeFilter || "all";
    var keyword = (inventoryState.keyword || "").trim().toLowerCase();
    var filtered = elements.filter(function (el) {
      if (typeFilter !== "all" && el.element_type !== typeFilter) return false;
      if (!keyword) return true;
      var haystack = ((el.label || "") + " " + (el.snippet || "")).toLowerCase();
      return haystack.indexOf(keyword) !== -1;
    });
    if (!filtered.length) {
      list.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">' +
        (elements.length ? "条件に一致する要素がありません" : "検出された要素はありません") +
        '</div>';
      return;
    }
    list.innerHTML = filtered.map(_inventoryCardHtml).join("");
    _bindInventoryDeliberateButtons(list);
  }

  function _renderInventoryToolbar() {
    var toolbar = document.getElementById("deliberation-inventory-toolbar");
    if (!toolbar) return;
    var data = inventoryState.data || {};
    var counts = data.counts || {};
    var total = _inventoryTotalCount(counts);
    var totalEl = document.getElementById("deliberation-inventory-total");
    if (totalEl) totalEl.textContent = "(" + total + "件)";

    var chipsHtml = INVENTORY_TYPE_ORDER.map(function (key) {
      var active = (inventoryState.typeFilter || "all") === key;
      var count = key === "all" ? total : (counts[key] || 0);
      return '<button type="button" class="deliberation-inventory-chip" data-inventory-type="' +
        escHtml(key) + '" style="padding:4px 10px;border-radius:14px;font-size:12px;cursor:pointer;' +
        'border:1px solid ' + (active ? "var(--color-text-info)" : "var(--color-border-secondary,#d2d2d7)") + ';' +
        'background:' + (active ? "var(--color-background-info,#eef6ff)" : "none") + ';' +
        'color:' + (active ? "var(--color-text-info)" : "var(--color-text-secondary)") + '">' +
        escHtml(INVENTORY_TYPE_CHIP_LABELS[key]) + '（' + count + '）</button>';
    }).join("");

    toolbar.innerHTML = chipsHtml +
      '<input id="deliberation-inventory-keyword" type="text" placeholder="キーワードで絞り込み" ' +
        'value="' + escHtml(inventoryState.keyword || "") + '" style="flex:1;min-width:160px;padding:4px 8px;' +
        'font-size:12px;border:1px solid var(--color-border-secondary,#d2d2d7);border-radius:6px">' +
      '<button id="deliberation-inventory-reload" type="button" style="padding:4px 10px;font-size:12px;' +
        'border-radius:6px;border:1px solid var(--color-border-secondary,#d2d2d7);' +
        'background:var(--color-background-secondary,#f5f5f7);color:var(--color-text-primary);cursor:pointer">再読込</button>';

    toolbar.querySelectorAll("[data-inventory-type]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        inventoryState.typeFilter = btn.getAttribute("data-inventory-type");
        _renderInventoryToolbar();
        _renderInventoryList();
      });
    });
    // キーワード入力は _renderInventoryList のみを呼ぶ（ツールバー全体は再構築しない）。
    // ツールバーを作り直すと入力中のフォーカス・カーソル位置が失われるため。
    var keywordInput = document.getElementById("deliberation-inventory-keyword");
    if (keywordInput) {
      keywordInput.addEventListener("input", function () {
        inventoryState.keyword = keywordInput.value || "";
        _renderInventoryList();
      });
    }
    var reloadBtn = document.getElementById("deliberation-inventory-reload");
    if (reloadBtn) reloadBtn.addEventListener("click", _loadInventory);

    var truncatedTypes = data.truncated_types || [];
    var truncatedNote = document.getElementById("deliberation-inventory-truncated-note");
    if (truncatedNote) {
      truncatedNote.textContent = truncatedTypes.length
        ? truncatedTypes.map(function (t) {
            return (INVENTORY_TYPE_CHIP_LABELS[t] || t) + "は500件で省略されています";
          }).join(" / ")
        : "";
    }
  }

  // GET のみ・DB 非変更（I1）。「再読込」ボタンだけがこれを呼ぶ（§9）。
  function _loadInventory() {
    var list = document.getElementById("deliberation-inventory-list");
    if (list) {
      list.innerHTML = '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>';
    }
    var documentId = inventoryState.documentId;
    apiFetch("/admin/deliberation/documents/" + encodeURIComponent(documentId) + "/elements")
      .then(function (res) {
        if (!res.ok) {
          var status = res.status;
          var err = new Error("status " + status);
          err.status = status;
          throw err;
        }
        return res.json();
      })
      .then(function (data) {
        inventoryState.data = data || {};
        _renderInventoryToolbar();
        _renderInventoryList();
      })
      .catch(function (err) {
        var currentList = document.getElementById("deliberation-inventory-list");
        if (!currentList) return;
        var message = (err && err.status === 404)
          ? "この教材は見つからないか、閲覧できません"
          : "検出要素の読み込みに失敗しました";
        currentList.innerHTML = '<div style="padding:16px;color:var(--color-text-danger);font-size:13px">' +
          escHtml(message) + '</div>';
      });
  }

  // ── 公開 API: openInventory ──────────────────────────────────────────
  // opts = { title: string|null }
  function openInventory(documentId, opts) {
    opts = opts || {};
    if (!deps.apiFetch && !window.apiFetch) return;
    if (!documentId) return;

    _closeInventoryModal();
    inventoryState.documentId = documentId;
    inventoryState.title = opts.title || null;
    inventoryState.typeFilter = "all";
    inventoryState.keyword = "";
    inventoryState.data = null;

    var overlay = document.createElement("div");
    overlay.id = "deliberation-inventory-modal";
    // z-index は既存の深く検討モーダル（#deliberation-modal）より低い 9400 にする
    // （インベントリの上に深く検討モーダルが重なる。設計書 §9）。
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;" +
      "background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9400";
    overlay.innerHTML =
      '<div class="deliberation-modal-dialog" style="width:min(920px,92vw)">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">検出要素の一覧' +
            (inventoryState.title ? ' — ' + escHtml(inventoryState.title) : '') +
            '<span id="deliberation-inventory-total" style="margin-left:8px;font-size:12px;font-weight:400;color:var(--color-text-tertiary)"></span>' +
          '</h3>' +
          '<button id="deliberation-inventory-close" type="button" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          'この教材からパイプラインが検出した要素の一覧です。表示はすべて既存データの読み出しで、確定済みの判断ではありません。' +
        '</p>' +
        '<div id="deliberation-inventory-toolbar" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px"></div>' +
        '<div id="deliberation-inventory-truncated-note" style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px"></div>' +
        '<div id="deliberation-inventory-list" style="overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:8px">' +
          '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) _closeInventoryModal(); });
    document.getElementById("deliberation-inventory-close").addEventListener("click", _closeInventoryModal);

    _loadInventory();
  }

  window.Deliberation = {
    init: init,
    openElement: openElement,
    openInventory: openInventory
  };
})();
