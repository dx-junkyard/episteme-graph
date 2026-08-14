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

  // 要素種別の表示名は element-vocab.js（window.ElementVocab）が正本。ここに独自辞書は
  // 持たない（admin_ux_issues_2026-08-01.md §3.3 Phase 0。かつて theory_component=
  // 「コンポーネント」/ theory_claim="claim" と他画面と食い違っていた）。
  function elementTypeLabel(elementType) {
    var vocab = window.ElementVocab;
    if (vocab && vocab.elementTypeLabel) return vocab.elementTypeLabel(elementType);
    return elementType == null ? "" : String(elementType);
  }

  // ── 要素インベントリ（Element Inventory / 検出要素の一覧）─────────────────
  // 正本: docs/features/element_inventory_design.md §4/§6/§7/§9。
  // 教材単位の統合入口。種別チップ + キーワードのフィルタは全面クライアントサイド
  // （§6: 1回のフェッチで全件を取得し、キー入力ごとの再フェッチをしない）。
  var INVENTORY_TYPE_ORDER = ["all", "theory_component", "theory_claim", "equation", "figure"];
  // 種別の表示名は正本（element-vocab.js）へ委譲し、語彙外の "all"（フィルタの
  // 「すべて」）だけをここで扱う。
  function inventoryTypeLabel(type) {
    if (type === "all") return "すべて";
    return elementTypeLabel(type);
  }

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

  // 要素説明（element_explanations, migration 056）の kind/status ラベル。
  // 正本: docs/features/hierarchical_context_explanation_design.md §5.2。
  // generic=汎用説明（L層引用がある場合のみ生成）/ contextual=文脈説明（この論文での位置づけ）。
  // status は candidate（AI候補）/ approved（承認済み）の2値のみを対象とする
  // （dismissed/superseded は overview に同梱されない・W2/E2）。
  var EXPLANATION_KIND_LABELS = {
    generic: "汎用説明",
    contextual: "文脈説明"
  };
  var EXPLANATION_STATUS_LABELS = {
    candidate: "AI候補",
    approved: "承認済み"
  };

  // Phase 3（設計書 §6）: L層エントリの標準化判定（standardization_status）。
  // 段階ラベルのみ（生スコアは持たない語彙のため、そのまま日本語ラベルに変換する）。
  // 正本は backend/core/library/schema.py::STANDARDIZATION_STATUS_LABELS。
  // app.js / admin.js の同語彙表とバイト一致させる
  // （固定は backend/tests/test_library_vocab_mirror.py）。
  var STANDARDIZATION_STATUS_LABELS = {
    standard: "標準",
    field_standard: "分野標準",
    emerging_common: "共通化しつつある",
    novel: "新規",
    unknown: "未評価"
  };

  // ── 要素型の性質（backend core/deliberation/schema.py の語彙に対応）──────────
  // DOCUMENT_ID_REQUIRED: 独立テーブルを持たず artifact 内 ID で一意化する要素型。
  //   API 呼び出しに document_id を必ず添える（無いと backend が 404 を返す）。
  //   equation は従来から、evidence / derivation は設計書 §16 で追加。
  var DOCUMENT_ID_REQUIRED_ELEMENT_TYPES = {
    equation: true,
    evidence: true,
    derivation: true
  };
  // IDENTITY_LINKABLE: 共通部品（shared_part）との同一性リンク・標準化判定の対象。
  //   evidence（原文の引用そのもの）と derivation（この論文の導出手順）は共通部品化の
  //   単位ではないため対象外（§16。backend は 422 を返す）。
  var IDENTITY_LINKABLE_ELEMENT_TYPES = {
    figure: true,
    theory_component: true,
    theory_claim: true,
    equation: true
  };

  function _needsDocumentId(elementType) {
    return !!DOCUMENT_ID_REQUIRED_ELEMENT_TYPES[String(elementType || "")];
  }

  function _isIdentityLinkable(elementType) {
    return !!IDENTITY_LINKABLE_ELEMENT_TYPES[String(elementType || "")];
  }

  function _documentIdQuery(ref) {
    ref = ref || {};
    if (!_needsDocumentId(ref.elementType) || !ref.documentId) return "";
    return "?document_id=" + encodeURIComponent(ref.documentId);
  }

  // 面③ 対話状態。モーダルを開くたび（_closeModal で）リセットする単一セッション分の状態
  // （1モーダル=1対話。複数セッションの並行管理は v1 では行わない）。
  // selectedModel: M層 Phase 3（llm_model_selection_design.md §6.5）のこの対話1回だけの
  // モデル上書き。null ならサーバ既定（resolve_model）に委ねる。
  var chatState = { sessionId: null, ref: null, sending: false, selectedContext: null, selectedModel: null };
  // 要素中心コンテキストビュー（Issue #498 §2.3）の中心移動履歴。openElement で
  // その要素1件から再スタートし、_navigateToElement が隣接ノード選択のたびに
  // 積み増す（パンくず・「← 戻る」が this を描画する）。
  var navState = { trail: [] };
  // 教員指示付き再解析（guided reanalysis, focusMode/focusBbox/hintText）は
  // _reloadOverview を跨いで保持する（設計書 §7-3「送信した guidance は成功・失敗に
  // かかわらず消さない」）。別の要素でモーダルを開くとき（_closeModal 経由）にのみ
  // _resetFigureGuidanceState でリセットする（_resetFigureImageState とは別管理）。
  var figureImageState = {
    objectUrls: [], requestId: 0,
    focusMode: false,
    focusBbox: null,
    hintText: "",
    // 照合解析（#499）のレビュー質問カード「この箇所を再解析」が積む一回消費の
    // 状態。_figureReanalyzeGuidancePayload が読み取ると同時に空へ戻す
    // （consume-once。以降の通常の「AIで図を再解析」クリックへ持ち越さない）。
    unresolvedItemIds: []
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
    chatState = { sessionId: null, ref: null, sending: false, selectedContext: null, selectedModel: null };
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
    figureImageState.unresolvedItemIds = [];
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

  // opts.skipIntraDocument: 要素中心コンテキストビュー（Issue #498）が利用可能なとき、
  // 論文内レンズ（intra_document）は上位/下位構造投影に再構成されるため二重表示しない
  // （設計書 §6 Phase 0）。opts 省略時は従来どおり全レンズを表示する（後方互換）。
  function _positioningHtml(positioning, opts) {
    if (!positioning || !positioning.available) return "";
    opts = opts || {};
    var lenses = positioning.lenses || {};
    var order = opts.skipIntraDocument
      ? LENS_ORDER.filter(function (key) { return key !== "intra_document"; })
      : LENS_ORDER;
    var sections = order.map(function (key) {
      return _lensSectionHtml(key, lenses[key]);
    }).join("");
    if (!sections.trim()) return "";
    return '<div data-ui-anchor="deliberation.positioning-lenses" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 10px;font-size:14px;color:var(--color-text-primary)">位置づけ</h4>' +
      sections +
    '</div>';
  }

  // ── 要素中心コンテキストビュー（Element-Centered Context Lens, Issue #498）───
  // 正本: docs/features/element_context_lens_design.md §2.3/§3/§6 Phase 2/§8。
  // overview.context を、選択要素を中心に上位構造（Why）/ 選択要素（What）/
  // 下位構造（How）へ投影する。AI の解釈を原文事実に昇格させない（§2.2）:
  // 関係が得られない場合もレーンを消さず、カード側の事実文（「まだ同定されていません」）
  // と「未同定」バッジで明示する。
  // 描画は統一パーツカード（element-card.js / window.ElementCard）へ委譲する
  // （admin_ux_issues_2026-08-01.md §3.2 / §3.3 Phase 3。P2「パーツ1個の描き方は
  // 出現箇所によらず同一」）。旧 .deliberation-context-lane / -item / -focus / -nav /
  // -status 系の独自レーン HTML は撤去した — **ここに再実装しないこと。**
  // カードが構造的に描かない情報は落とさず、カード直後の補足欄に残す:
  //   - focus.provenance（内部参照の列。カードは §3.2 の表示契約により描かない）
  //   - focus.generic.standardization_status（L層エントリの標準化判定ラベル）
  //   - ITEM の evidence_refs（カード本体が editable 限定の折りたたみで描く）
  var CONTEXT_LENS_ID = "deliberation-context-lens";
  var CONTEXT_LANE_TITLES = { upper: "上位構造（Why）", lower: "下位構造（How）" };
  // "unidentified" は ElementVocab.statusLabel が語彙に持たない（バッジ自体を出さない
  // 判断に使うため）。W層は「未同定」を事実として明示する画面なので、明示ラベルを
  // metaBadges の fallback として渡す（§2.2: 推測で穴埋めせず未同定と書く）。
  var CONTEXT_STATUS_UNIDENTIFIED = "unidentified";
  var CONTEXT_STATUS_UNIDENTIFIED_LABEL = "未同定";
  // 語彙外・未知の状態は「未同定」へ寄せる（内部語彙をクラス名にも文字列にも漏らさない。
  // 旧 _contextStatusBadgeHtml の modifier フォールバックと同じ挙動）。
  var CONTEXT_KNOWN_STATUSES = { source_backed: true, candidate: true, confirmed: true };
  // render と bind には同一の dto / opts を渡さなければならない（近傍チップは
  // onCenter が設定されているときだけ描かれるため、食い違うと配線が消える）。
  var contextLensRender = null;

  // 管理画面（admin.html）は KaTeX を CDN から読み込む（window.katex）。カード側に
  // ゲート（looksLikeRenderableTex）があるので、ここは素の描画関数を渡すだけでよい
  // — かつて renderMath 未注入だったため W層モーダルだけ生 TeX が出ていた
  // （element_context_presentation_redesign.md RC8 / §6 S4）。
  function _renderMath(expr, display) {
    var text = String(expr == null ? "" : expr);
    if (!text || !window.katex) return "";
    try {
      return '<span class="' + (display ? "lecture-formula-block visible" : "lecture-formula visible") + '">' +
        window.katex.renderToString(text, {
          displayMode: !!display,
          throwOnError: false,
          strict: "ignore",
          trust: false
        }) + '</span>';
    } catch (e) {
      return "";
    }
  }

  function _contextLensOpts(focus) {
    focus = focus || {};
    var card = window.ElementCard;
    // 既知の状態だけをバッジにする（ラベルは正本 ElementVocab.statusLabel が引く）。
    // 未同定はバッジではなく補足欄の事実文で明示する（_contextSupplementHtml）。
    var status = focus.contextual_role_status || CONTEXT_STATUS_UNIDENTIFIED;
    var metaBadges = CONTEXT_KNOWN_STATUSES[status] ? [{ status: status }] : [];
    return {
      variant: card ? card.VARIANT_EDITABLE : "editable",
      escapeHtml: escHtml,
      renderMath: _renderMath,
      className: "deliberation-context-card",
      laneTitles: CONTEXT_LANE_TITLES,
      metaBadges: metaBadges,
      // 隣接 ITEM を選ぶと、その要素を新しい中心に再配置する（設計書 §2.3。
      // モーダルは破棄せずパンくずに積む）。
      onCenter: function (item) {
        item = item || {};
        _navigateToElement(
          item.element_type,
          item.element_id || item.id,
          item.document_id || null,
          item.label
        );
      }
    };
  }

  // カード直後の補足欄（カードが描かない事実だけを持つ。既存 CSS を流用する）。
  // 役割が未同定のときカードは role 行を描かない（推測で埋めない設計）ので、
  // 「この文脈での役割 = 未同定」という事実文はここで明示して落とさない（§2.2）。
  function _contextSupplementHtml(focus) {
    focus = focus || {};
    var status = focus.contextual_role_status || CONTEXT_STATUS_UNIDENTIFIED;
    var roleUnidentified = !focus.contextual_role || !CONTEXT_KNOWN_STATUSES[status];
    var generic = focus.generic;
    var stdLabel = generic
      ? (STANDARDIZATION_STATUS_LABELS[generic.standardization_status] ||
         generic.standardization_status || "")
      : "";
    var provenance = focus.provenance || [];
    return '<div class="deliberation-context-focus">' +
      (roleUnidentified
        ? '<div class="deliberation-context-focus-section">' +
            '<div class="deliberation-context-focus-label">この文脈での役割</div>' +
            '<p class="deliberation-context-fact">' +
              escHtml(CONTEXT_STATUS_UNIDENTIFIED_LABEL) + '</p>' +
          '</div>'
        : "") +
      (stdLabel
        ? '<div class="deliberation-context-focus-section">' +
            '<div class="deliberation-context-focus-label">共通部品の標準化判定</div>' +
            '<span class="deliberation-context-generic-status">' + escHtml(stdLabel) + '</span>' +
          '</div>'
        : "") +
      '<div class="deliberation-context-focus-section">' +
        '<div class="deliberation-context-focus-label">出典・内部参照</div>' +
        (provenance.length
          ? '<ul class="deliberation-context-provenance">' +
              provenance.map(function (p) { return '<li>' + escHtml(p) + '</li>'; }).join("") +
            '</ul>'
          : '<p class="deliberation-context-fact">出典情報はありません</p>') +
      '</div>' +
    '</div>';
  }

  function _contextLensShellHtml(inner, extraClass) {
    return '<div id="' + CONTEXT_LENS_ID + '" class="deliberation-context-lens' +
        (extraClass ? " " + extraClass : "") + '" data-ui-anchor="deliberation.context-lens">' +
      inner +
    '</div>';
  }

  // 中心カード（focus + 上位/下位レーン + notes）+ 補足欄の順で描画する。
  // context が無い run（旧run・fail-closed）は空文字で縮退し、呼び出し側は従来の
  // 内訳・位置づけ表示にフォールバックする。
  function _contextLensHtml(context) {
    contextLensRender = null;
    if (!context) return "";
    if (!context.available) {
      if (!context.note) return "";
      return _contextLensShellHtml(
        '<p class="deliberation-context-fact">' + escHtml(context.note) + '</p>',
        "deliberation-context-lens-unavailable");
    }
    var focus = context.focus || {};
    var card = window.ElementCard;
    if (!card || !card.render) {
      // 部品未読み込みは事実文へ縮退する（独自レーン HTML の二重実装は持たない）。
      return _contextLensShellHtml(
        '<p class="deliberation-context-fact">' +
          escHtml(focus.intrinsic_summary || "この要素の文脈情報を表示できませんでした") +
        '</p>' + _contextSupplementHtml(focus),
        "deliberation-context-lens-unavailable");
    }
    var dto = {
      focus: focus,
      upper: context.upper || [],
      lower: context.lower || [],
      notes: context.notes || [],
      // 導出ストーリー（DTO v2）。focus 配下で来る場合はカード側が拾う。
      derivations: context.derivations || []
    };
    var opts = _contextLensOpts(focus);
    contextLensRender = { dto: dto, opts: opts };
    return _contextLensShellHtml(card.render(dto, opts) + _contextSupplementHtml(focus));
  }

  // ITEM の evidence_refs はカード本体（element-card.js の itemRefsHtml、editable のみ）が
  // 折りたたみで描く。かつてここにあった mount 後の DOM 後付け
  // （_augmentContextEvidenceRefs）は撤去済み — 再実装しないこと。

  // カードの近傍チップ（上位/下位レーン）クリックで中心移動する配線。再描画のたびに
  // 呼ばれるため data-context-nav-bound で二重バインドを防ぐ
  // （_bindFigureContextActions と同型の idempotent bind パターン）。
  function _bindContextNavigation() {
    var container = document.getElementById(CONTEXT_LENS_ID);
    var card = window.ElementCard;
    if (!container || !card || !card.bind || !contextLensRender) return;
    if (container.getAttribute("data-context-nav-bound") === "true") return;
    container.setAttribute("data-context-nav-bound", "true");
    card.bind(container, contextLensRender.dto, contextLensRender.opts);
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
        '<button id="deliberation-mode-save" type="button" data-ui-anchor="deliberation.mode-save">保存</button>' +
        '<span id="deliberation-mode-save-status" role="status"></span>' +
        '<button id="deliberation-figure-reanalyze" type="button" data-ui-anchor="deliberation.figure-reanalyze">AIで図を再解析</button>' +
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

  function _figureWorkspaceHtml(decomposition, positioning, positioningOpts) {
    var fields = decomposition.fields || {};
    return '<div class="deliberation-figure-workspace">' +
      '<div class="deliberation-figure-image-card">' +
        '<div class="deliberation-figure-image-toolbar" data-ui-anchor="deliberation.figure-expand"><span>原図</span>' +
          '<button id="deliberation-figure-expand" type="button" disabled data-ui-anchor="deliberation.figure-expand">拡大表示</button></div>' +
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
      _iterativeAnalysisHtml(fields) +
      '<div data-ui-anchor="deliberation.decomposition">' +
      '<details class="deliberation-figure-raw"><summary>抽出データ・根拠を見る</summary>' +
        '<div class="deliberation-figure-raw-body">' + _fieldsHtml(fields) + _notesHtml(decomposition.notes) + '</div>' +
      '</details>' +
      '</div>' +
      _positioningHtml(positioning, positioningOpts) +
      '<div id="deliberation-figure-lightbox" class="deliberation-figure-lightbox" aria-hidden="true">' +
        '<button id="deliberation-figure-lightbox-close" type="button" aria-label="拡大表示を閉じる">&times;</button>' +
        '<img id="deliberation-figure-lightbox-image" alt="' + escHtml(decomposition.label || "検討対象の図") + '">' +
      '</div>' +
    '</div>';
  }

  // ── 照合解析（Contextual Figure Analysis Iterative Verification, #499 Wave 4）───
  // 正本: docs/features/contextual_figure_analysis_iterative_verification.md
  // 「実装記録（2026-07-18 実装決定事項）」節。バックエンド（別Wave）が
  // fields.iterative_analysis に投影済みの契約（confidence 生値は含まれず
  // confidence_label のみ）を返す。ここは読み取り専用の表示 + 「この箇所を
  // 再解析」ボタンの配線のみを担う（判定・確定はしない・W2/W8 継承）。
  var ITERATIVE_CONVERGENCE_LABELS = {
    converged: "照合済み",
    max_iterations_reached: "未収束（上限到達・要確認）",
    no_progress: "未収束（新しい検証課題なし・要確認）",
    aborted_error: "解析が途中で失敗（部分結果）",
    aborted_cost_limit: "コスト上限で中断（部分結果）",
    not_run: "未実行"
  };

  var ALIGNMENT_STATUS_LABELS = {
    supported_by_both: "文章と画像の両方で確認",
    visual_only: "画像のみ（本文での意味は未確認）",
    text_only: "文章のみ（画像では未確認）",
    contradicted: "文章と画像が矛盾",
    unresolved: "未解決"
  };
  var ALIGNMENT_STATUS_ORDER = ["supported_by_both", "visual_only", "text_only", "contradicted", "unresolved"];
  // 注意色（点線枠）を当てる区分。煽らず区分を視覚的に見分けやすくするだけ。
  var ALIGNMENT_CAUTION_STATUSES = { text_only: true, contradicted: true, unresolved: true };

  function _alignmentItemRowHtml(item) {
    item = item || {};
    var statusKey = item.status || "unresolved";
    var label = ALIGNMENT_STATUS_LABELS[statusKey] || statusKey;
    var caution = !!ALIGNMENT_CAUTION_STATUSES[statusKey];
    var textEvidence = _itemText(item.text_evidence);
    var visualEvidence = _itemText(item.visual_evidence);
    return '<li class="deliberation-iterative-alignment-item' + (caution ? ' is-caution' : '') + '">' +
      '<div class="deliberation-iterative-alignment-label">' + escHtml(item.label || label) + '</div>' +
      (textEvidence ? '<div class="deliberation-iterative-evidence">本文: &quot;' + escHtml(textEvidence) + '&quot;</div>' : '') +
      (visualEvidence ? '<div class="deliberation-iterative-evidence">画像: ' + escHtml(visualEvidence) + '</div>' : '') +
      (item.confidence_label ? '<span class="deliberation-annotation-confidence">' + escHtml(item.confidence_label) + '</span>' : '') +
    '</li>';
  }

  // status ごとにグループ化して表示する（設計書: 区分別 alignment 表示）。
  function _alignmentItemsHtml(items) {
    items = _asArray(items);
    if (!items.length) {
      return '<p class="deliberation-figure-muted">照合項目はありません。</p>';
    }
    var groups = {};
    ALIGNMENT_STATUS_ORDER.forEach(function (key) { groups[key] = []; });
    items.forEach(function (item) {
      var key = (item && item.status) || "unresolved";
      if (!groups[key]) groups[key] = [];
      groups[key].push(item);
    });
    return ALIGNMENT_STATUS_ORDER.map(function (key) {
      var group = groups[key] || [];
      if (!group.length) return "";
      return '<div class="deliberation-iterative-alignment-group">' +
        '<div class="deliberation-iterative-alignment-heading">' + escHtml(ALIGNMENT_STATUS_LABELS[key]) + '</div>' +
        '<ul class="deliberation-iterative-alignment-list">' + group.map(_alignmentItemRowHtml).join("") + '</ul>' +
      '</div>';
    }).join("");
  }

  // review_questions と unresolved_conflicts を「AIからの確認事項」として統合表示する。
  // 各カードの「この箇所を再解析」は既存の「AIで図を再解析」ボタン（_bindFigureReanalysis）
  // をプログラム的にクリックして送信処理を完全共有する（_bindIterativeReverify 参照。
  // 新規 fetch 先を増やさない＝許可リストの /admin/documents/ カウントを壊さない）。
  function _reviewQuestionCardHtml(entry) {
    entry = entry || {};
    var questionId = entry.question_id || entry.item_id || "";
    var text = entry.question || entry.reason || "";
    var regionHint = entry.region_hint || "";
    return '<div class="deliberation-iterative-review-card">' +
      '<p class="deliberation-iterative-review-question">' + escHtml(text) + '</p>' +
      (entry.label ? '<p class="deliberation-iterative-review-label">対象: ' + escHtml(entry.label) + '</p>' : '') +
      '<button type="button" class="deliberation-iterative-reverify" ' +
        'data-question-id="' + escHtml(questionId) + '" ' +
        'data-region-hint="' + escHtml(regionHint) + '">この箇所を再解析</button>' +
    '</div>';
  }

  function _reviewQuestionsHtml(ia) {
    ia = ia || {};
    var entries = _asArray(ia.review_questions).concat(_asArray(ia.unresolved_conflicts));
    if (!entries.length) return "";
    return '<div class="deliberation-iterative-review-section">' +
      '<h5>AIからの確認事項（この図について教えてください）</h5>' +
      entries.map(_reviewQuestionCardHtml).join("") +
    '</div>';
  }

  function _expectedElementsHtml(items) {
    items = _asArray(items);
    if (!items.length) return '<p class="deliberation-figure-muted">情報なし</p>';
    return '<ul class="deliberation-figure-list">' + items.map(function (el) {
      el = el || {};
      var evidence = el.evidence_quote ? ' — &quot;' + escHtml(el.evidence_quote) + '&quot;' : "";
      var confidence = el.confidence_label
        ? ' <span class="deliberation-annotation-confidence">' + escHtml(el.confidence_label) + '</span>' : "";
      return '<li>' + escHtml(el.name || "") + evidence + confidence + '</li>';
    }).join("") + '</ul>';
  }

  function _expectedRelationsHtml(items) {
    items = _asArray(items);
    if (!items.length) return '<p class="deliberation-figure-muted">情報なし</p>';
    return '<ul class="deliberation-figure-list">' + items.map(function (rel) {
      rel = rel || {};
      var evidence = rel.evidence_quote ? ' — &quot;' + escHtml(rel.evidence_quote) + '&quot;' : "";
      return '<li>' + escHtml(rel.relation || "") + evidence + '</li>';
    }).join("") + '</ul>';
  }

  // context_hypothesis（画像を見る前の文脈仮説）の折り畳み表示。
  function _contextHypothesisHtml(hyp) {
    if (!hyp) return '<p class="deliberation-figure-muted">仮説はありません。</p>';
    hyp = hyp || {};
    return '<div class="deliberation-iterative-subsection">' +
      (hyp.role_in_paper ? '<p><strong>論文中の役割</strong> ' + escHtml(hyp.role_in_paper) + '</p>' : '') +
      (hyp.overall_subject ? '<p><strong>想定される主題</strong> ' + escHtml(hyp.overall_subject) + '</p>' : '') +
      '<div class="deliberation-figure-subheading">期待される要素</div>' + _expectedElementsHtml(hyp.expected_elements) +
      '<div class="deliberation-figure-subheading">期待される関係</div>' + _expectedRelationsHtml(hyp.expected_relations) +
      (_asArray(hyp.unstated_points).length
        ? '<div class="deliberation-figure-subheading">未明示点</div>' + _simpleListHtml(hyp.unstated_points) : '') +
      (_asArray(hyp.falsification_conditions).length
        ? '<div class="deliberation-figure-subheading">反証条件</div>' + _simpleListHtml(hyp.falsification_conditions) : '') +
      (hyp.confidence_label ? '<span class="deliberation-annotation-confidence">' + escHtml(hyp.confidence_label) + '</span>' : '') +
    '</div>';
  }

  // visual_observations（caption・本文を渡さない独立観察。確証バイアス遮断）の折り畳み表示。
  function _visualObservationsHtml(obs) {
    if (!obs) return '<p class="deliberation-figure-muted">観察はありません。</p>';
    obs = obs || {};
    var elements = _asArray(obs.elements);
    var connections = _asArray(obs.connections);
    var unreadable = _asArray(obs.unreadable_regions).map(function (r) {
      return (r && typeof r === "object") ? (r.reason || r.region_hint || "") : r;
    });
    return '<div class="deliberation-iterative-subsection">' +
      '<div class="deliberation-figure-subheading">要素</div>' +
      (elements.length ? '<ul class="deliberation-figure-list">' + elements.map(function (el) {
        el = el || {};
        return '<li>' + escHtml(el.description || el.label_text || el.kind || "") + '</li>';
      }).join("") + '</ul>' : '<p class="deliberation-figure-muted">情報なし</p>') +
      '<div class="deliberation-figure-subheading">接続</div>' +
      (connections.length ? '<ul class="deliberation-figure-list">' + connections.map(function (conn) {
        conn = conn || {};
        return '<li>' + escHtml(conn.description || conn.connector || "") + '</li>';
      }).join("") + '</ul>' : '<p class="deliberation-figure-muted">情報なし</p>') +
      (_asArray(obs.ocr_labels).length
        ? '<div class="deliberation-figure-subheading">OCR</div>' + _simpleListHtml(obs.ocr_labels) : '') +
      (unreadable.length
        ? '<div class="deliberation-figure-subheading">判読不能領域</div>' + _simpleListHtml(unreadable) : '') +
      (obs.visual_mode_guess ? '<p><strong>見た目の分類推定</strong> ' + escHtml(obs.visual_mode_guess) + '</p>' : '') +
      (obs.confidence_label ? '<span class="deliberation-annotation-confidence">' + escHtml(obs.confidence_label) + '</span>' : '') +
    '</div>';
  }

  // alternative_hypotheses（競合仮説2〜3件）の折り畳み表示。類似形状は証拠に使わない
  // （設計記録: 「形状一致は機能一致の証拠にしない」）── ここは受け取ったデータの
  // 表示に徹し、証拠の重み付けの判断はしない。
  function _alternativeHypothesesHtml(items) {
    items = _asArray(items);
    if (!items.length) return '<p class="deliberation-figure-muted">競合仮説はありません。</p>';
    return items.map(function (alt) {
      alt = alt || {};
      return '<div class="deliberation-iterative-subsection">' +
        (alt.description ? '<p>' + escHtml(alt.description) + '</p>' : '') +
        (_asArray(alt.supporting_evidence).length
          ? '<div class="deliberation-figure-subheading">支持する根拠</div>' + _simpleListHtml(alt.supporting_evidence) : '') +
        (_asArray(alt.counter_evidence).length
          ? '<div class="deliberation-figure-subheading">反する根拠</div>' + _simpleListHtml(alt.counter_evidence) : '') +
        (_asArray(alt.unverified_conditions).length
          ? '<div class="deliberation-figure-subheading">未確認の条件</div>' + _simpleListHtml(alt.unverified_conditions) : '') +
        (alt.confidence_label ? '<span class="deliberation-annotation-confidence">' + escHtml(alt.confidence_label) + '</span>' : '') +
      '</div>';
    }).join("");
  }

  // verification_iterations（再スキャンの履歴）の折り畳み表示。
  function _verificationIterationsHtml(items) {
    items = _asArray(items);
    if (!items.length) return '<p class="deliberation-figure-muted">検証履歴はありません。</p>';
    return '<ol class="deliberation-iterative-iteration-list">' + items.map(function (rec) {
      rec = rec || {};
      var findings = _asArray(rec.findings);
      var indexText = (rec.iteration_index === null || rec.iteration_index === undefined) ? "" : String(rec.iteration_index);
      return '<li>' +
        '<div><strong>反復 ' + escHtml(indexText) + '</strong></div>' +
        (findings.length ? '<ul class="deliberation-figure-list">' + findings.map(function (finding) {
          finding = finding || {};
          var observation = finding.observation ? '：' + escHtml(finding.observation) : "";
          return '<li>' + escHtml(finding.outcome || "") + observation + '</li>';
        }).join("") + '</ul>' : '') +
        (_asArray(rec.changes).length
          ? '<div class="deliberation-figure-subheading">変更</div>' + _simpleListHtml(rec.changes) : '') +
        (rec.notes ? '<p class="deliberation-figure-muted">' + escHtml(rec.notes) + '</p>' : '') +
      '</li>';
    }).join("") + '</ol>';
  }

  // fields.iterative_analysis（バックエンド投影済み。confidence 生値は含まれず
  // confidence_label のみ）を描画する。available が falsy か中身が空なら
  // 「まだ実行されていません」の事実文のみを出す（W4: 存在しない結果を捏造しない）。
  function _iterativeAnalysisHtml(fields) {
    fields = fields || {};
    var ia = fields.iterative_analysis || null;
    var available = !!(ia && ia.available);
    var statusKey = (ia && ia.convergence_status) || "not_run";
    var statusLabel = ITERATIVE_CONVERGENCE_LABELS[statusKey] || statusKey;
    var header = '<div class="deliberation-iterative-header">' +
      '<h5>照合解析（文脈仮説 × 画像観察）</h5>' +
      '<span class="deliberation-iterative-status deliberation-iterative-status--' + escHtml(statusKey) + '">' +
        escHtml(statusLabel) +
      '</span>' +
    '</div>';
    if (!available) {
      return '<section class="deliberation-iterative-analysis">' +
        header +
        '<p class="deliberation-figure-muted">反復照合解析はまだ実行されていません。</p>' +
      '</section>';
    }
    ia = ia || {};
    var stageFailures = _asArray(ia.stage_failures);
    return '<section class="deliberation-iterative-analysis">' +
      header +
      '<p class="deliberation-iterative-note">' +
        '以下は AI の候補です。「画像で直接確認」「本文の記述」「推論」「未確認」を区別して表示しています。' +
        '確定には教員のレビューが必要です。' +
      '</p>' +
      '<div class="deliberation-figure-subheading">照合結果</div>' +
      _alignmentItemsHtml(ia.alignment_items) +
      _reviewQuestionsHtml(ia) +
      '<details class="deliberation-iterative-details"><summary>文脈からの期待（画像を見る前の仮説）</summary>' +
        _contextHypothesisHtml(ia.context_hypothesis) +
      '</details>' +
      '<details class="deliberation-iterative-details"><summary>画像の直接観察</summary>' +
        _visualObservationsHtml(ia.visual_observations) +
      '</details>' +
      '<details class="deliberation-iterative-details"><summary>競合仮説</summary>' +
        _alternativeHypothesesHtml(ia.alternative_hypotheses) +
      '</details>' +
      '<details class="deliberation-iterative-details"><summary>検証の履歴</summary>' +
        _verificationIterationsHtml(ia.verification_iterations) +
      '</details>' +
      (stageFailures.length
        ? '<details class="deliberation-iterative-details"><summary>解析の途中失敗</summary>' +
            _simpleListHtml(stageFailures) +
          '</details>'
        : '') +
    '</section>';
  }

  function _renderModalBody(data) {
    var body = document.getElementById("deliberation-modal-body");
    if (!body) return;
    var decomposition = data.decomposition || {};
    var typeLabel = elementTypeLabel(decomposition.element_type);
    var isFigure = decomposition.element_type === "figure";
    // 要素中心コンテキストビュー（Issue #498）: context が利用可能なら論文内レンズ
    // （intra_document）は上位/下位構造投影に再構成されるため二重表示しない
    // （設計書 §6 Phase 0）。旧run等で context が無ければ従来の位置づけ表示のまま。
    var contextAvailable = !!(data.context && data.context.available);
    var positioningOpts = { skipIntraDocument: contextAvailable };
    var contextHtml = _contextLensHtml(data.context);
    body.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">' +
        '<span class="admin-status" style="background:var(--color-background-info);color:var(--color-text-info)">' +
          escHtml(typeLabel) +
        '</span>' +
        '<h4 style="margin:0;font-size:15px;color:var(--color-text-primary)">' + escHtml(decomposition.label || "") + '</h4>' +
      '</div>' +
      (isFigure ? "" : contextHtml) +
      (isFigure ? _figureWorkspaceHtml(decomposition, data.positioning, positioningOpts) + contextHtml :
        '<div data-ui-anchor="deliberation.decomposition" style="margin-bottom:6px">' +
          '<div style="font-size:12.5px;font-weight:600;color:var(--color-text-secondary);margin-bottom:4px">内訳</div>' +
          _fieldsHtml(decomposition.fields) +
        '</div>' +
        _notesHtml(decomposition.notes) +
        _positioningHtml(data.positioning, positioningOpts)) +
      _explanationsSectionHtml(data.explanations) +
      _identityLinksSectionHtml(decomposition.element_type) +
      _standardizationSectionHtml(decomposition.element_type);
    if (isFigure) {
      _bindFigureImageActions();
      _bindFigureContextActions();
      _bindFigureModeReview(decomposition);
      _bindFigureFocusDrawing();
      _bindFigureReanalysis(decomposition);
      _bindIterativeReverify();
      _loadFigureImage(decomposition);
    }
    _bindContextNavigation();
    _bindExplanationActions();
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
    // 照合解析（#499）のレビュー質問カードが積んだ unresolved_item_ids。読み取ると
    // 同時に消費する（consume-once。以降の通常クリックへ持ち越さない）。
    var unresolvedItemIds = figureImageState.unresolvedItemIds || [];
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
    if (unresolvedItemIds.length) {
      payload.unresolved_item_ids = unresolvedItemIds;
      hasGuidance = true;
    }
    figureImageState.unresolvedItemIds = [];
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

  // 照合解析（#499）のレビュー質問カード「この箇所を再解析」。新しい fetch 先を
  // 増やさず、既存の「AIで図を再解析」ボタン（上の _bindFigureReanalysis が配線した
  // 同一クリックハンドラ）をプログラム的にクリックして送信処理を完全に共有する
  // （status 表示 → _reloadOverview() → _loadAnnotations という既存フローがそのまま
  // 動く。許可リストの /admin/documents/ 出現回数を増やさない）。
  function _bindIterativeReverify() {
    Array.prototype.forEach.call(document.querySelectorAll(".deliberation-iterative-reverify"), function (btn) {
      if (btn.getAttribute("data-iterative-reverify-bound") === "true") return;
      btn.setAttribute("data-iterative-reverify-bound", "true");
      btn.addEventListener("click", function () {
        var questionId = btn.getAttribute("data-question-id");
        figureImageState.unresolvedItemIds = questionId ? [questionId] : [];
        var reanalyzeBtn = document.getElementById("deliberation-figure-reanalyze");
        if (reanalyzeBtn && !reanalyzeBtn.disabled) reanalyzeBtn.click();
      });
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
  // N2: インスタンス要素（document-scoped）には手動でリンク候補を作る導線
  // 「共通部品と結びつける」を出す（shared_part 自身には出さない — リンクの
  // source は常にインスタンス側）。作成されるのは常に候補（candidate）で、
  // 確定は既存の確定/却下ボタンが担う（KN-3）。
  function _identityLinksSectionHtml(elementType) {
    // §16: evidence / derivation は共通部品化の単位ではないためセクションごと出さない
    // （backend も identity-links 系を 422 で拒否する）。shared_part 自身はリンク先
    // なのでセクションは出すが作成導線は出さない（従来どおり）。
    if (elementType && elementType !== "shared_part" && !_isIdentityLinkable(elementType)) {
      return "";
    }
    var createHtml = "";
    if (elementType && elementType !== "shared_part") {
      createHtml =
        '<div style="margin-top:8px">' +
          '<button type="button" id="deliberation-identity-link-open-search" class="deliberation-chat-send" data-ui-anchor="deliberation.identity-link-create" style="padding:4px 10px;font-size:12px">共通部品と結びつける</button>' +
        '</div>' +
        '<div id="deliberation-identity-link-search" hidden style="margin-top:8px;padding:8px;border:1px solid var(--color-border-tertiary);border-radius:6px">' +
          '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">' +
            '<input type="text" id="deliberation-identity-search-input" class="deliberation-chat-input" style="flex:1;min-width:160px;padding:4px 8px;font-size:12.5px" placeholder="検索テキスト（空欄ならこの要素の内容から自動検索）">' +
            '<button type="button" id="deliberation-identity-search-btn" class="deliberation-chat-send" style="padding:4px 10px;font-size:12px">検索</button>' +
          '</div>' +
          '<input type="text" id="deliberation-identity-search-reason" class="deliberation-chat-input" style="width:100%;margin-top:6px;padding:4px 8px;font-size:12.5px" placeholder="結びつける理由（任意）">' +
          '<div id="deliberation-identity-search-results" style="margin-top:8px"></div>' +
          '<div id="deliberation-identity-search-note" style="font-size:11.5px;color:var(--color-text-tertiary);margin-top:6px"></div>' +
        '</div>';
    }
    return '<div class="deliberation-identity-links-wrap" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 6px;font-size:14px;color:var(--color-text-primary)">同一性リンク</h4>' +
      '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px">' +
        'この要素が別の資料・共通部品と同じものだという対応づけです。候補は AI 対話または教員が作成し、' +
        '確定・却下は教員のみが行います。既存の表記は書き換えません（リンクの追加のみ）。' +
      '</p>' +
      '<div id="deliberation-identity-links"><p class="deliberation-identity-empty">読み込み中...</p></div>' +
      createHtml +
    '</div>';
  }

  // ── N2: 手動リンク作成（共通部品の候補検索 → candidate 作成）────────────────
  // 検索は GET .../shared-part-candidates（domain はサーバ側で document → cartridge_id
  // から決定論的に解決。フロントは domain を知らない）。作成は既存の
  // POST /identity-links（常に candidate・確定は既存 UI）。数値は表示しない（W8）。
  function _bindIdentityLinkSearch(ref) {
    var openBtn = document.getElementById("deliberation-identity-link-open-search");
    if (!openBtn || !ref || ref.elementType === "shared_part") return;
    openBtn.addEventListener("click", function () {
      var panel = document.getElementById("deliberation-identity-link-search");
      if (!panel) return;
      panel.hidden = !panel.hidden;
      if (!panel.hidden) {
        var results = document.getElementById("deliberation-identity-search-results");
        if (results && !results.childNodes.length) _searchSharedPartCandidates(ref);
      }
    });
    var searchBtn = document.getElementById("deliberation-identity-search-btn");
    if (searchBtn) {
      searchBtn.addEventListener("click", function () { _searchSharedPartCandidates(ref); });
    }
    var input = document.getElementById("deliberation-identity-search-input");
    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) {
          e.preventDefault();
          _searchSharedPartCandidates(ref);
        }
      });
    }
  }

  function _searchSharedPartCandidates(ref) {
    var results = document.getElementById("deliberation-identity-search-results");
    var note = document.getElementById("deliberation-identity-search-note");
    if (!results) return;
    if (note) note.textContent = "";
    var input = document.getElementById("deliberation-identity-search-input");
    var q = input ? input.value.trim() : "";
    results.innerHTML = '<p class="deliberation-identity-empty">検索中...</p>';
    var path = "/admin/deliberation/elements/" + encodeURIComponent(ref.elementType) + "/" +
      encodeURIComponent(ref.elementId) + "/shared-part-candidates";
    var params = [];
    if (_needsDocumentId(ref.elementType) && ref.documentId) {
      params.push("document_id=" + encodeURIComponent(ref.documentId));
    }
    if (q) params.push("q=" + encodeURIComponent(q));
    if (params.length) path += "?" + params.join("&");
    apiFetch(path)
      .then(_parseJsonResponse)
      .then(function (data) {
        _renderSharedPartCandidates(ref, data || {});
      })
      .catch(function (err) {
        results.innerHTML = '<p class="deliberation-identity-empty">' +
          escHtml((err && err.detail) || "候補の検索に失敗しました。") + '</p>';
      });
  }

  function _sharedPartCandidateRowHtml(entry) {
    entry = entry || {};
    var aliases = (entry.aliases || []).filter(function (a) { return a; });
    return '<div class="deliberation-identity-link-row" data-shared-part-id="' + escHtml(entry.shared_part_id) + '">' +
      '<div class="deliberation-annotation-body">' + escHtml(entry.name || "(無名)") +
        (aliases.length ? '<span style="font-size:11.5px;color:var(--color-text-tertiary)">（別名: ' + escHtml(aliases.join("、")) + '）</span>' : '') +
      '</div>' +
      (entry.summary ? '<div class="deliberation-annotation-reason">' + escHtml(entry.summary) + '</div>' : '') +
      '<div class="deliberation-annotation-actions">' +
        '<button type="button" class="deliberation-annotation-btn commit" data-identity-create="1">この部品と結びつける（候補を作成）</button>' +
      '</div>' +
      '<div class="deliberation-annotation-error deliberation-identity-create-error" style="display:none"></div>' +
    '</div>';
  }

  function _renderSharedPartCandidates(ref, data) {
    var results = document.getElementById("deliberation-identity-search-results");
    if (!results) return;
    var entries = data.entries || [];
    if (!entries.length) {
      // 事実文のみ（0件は正常状態。煽らない・数値を出さない）。
      results.innerHTML = '<p class="deliberation-identity-empty">' +
        escHtml(data.note || "同分野の共通部品が見つかりません。") + '</p>';
      return;
    }
    results.innerHTML = entries.map(_sharedPartCandidateRowHtml).join("");
    Array.prototype.forEach.call(results.querySelectorAll("[data-identity-create]"), function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest(".deliberation-identity-link-row");
        if (!row) return;
        _createIdentityLink(ref, row.getAttribute("data-shared-part-id"), row);
      });
    });
  }

  function _createIdentityLink(ref, sharedPartId, row) {
    if (!sharedPartId) return;
    var buttons = row.querySelectorAll("[data-identity-create]");
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    var reasonInput = document.getElementById("deliberation-identity-search-reason");
    var body = {
      instance_element_type: ref.elementType,
      instance_element_id: ref.elementId,
      shared_part_id: sharedPartId,
      reason: reasonInput ? reasonInput.value.trim() : ""
    };
    if (ref.documentId) body.document_id = ref.documentId;
    apiFetch("/admin/deliberation/identity-links", {
      method: "POST",
      body: JSON.stringify(body)
    })
      .then(_parseJsonResponse)
      .then(function () {
        var note = document.getElementById("deliberation-identity-search-note");
        if (note) note.textContent = "候補を作成しました。上の一覧から確定・却下できます。";
        // 作成済み（または既存）の候補を一覧に反映する。
        _loadIdentityLinks(ref);
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      })
      .catch(function (err) {
        var errEl = row.querySelector(".deliberation-identity-create-error");
        if (errEl) {
          errEl.style.display = "";
          errEl.innerHTML = escHtml((err && err.detail) || "リンク候補の作成に失敗しました。");
        }
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      });
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
      // 脱UUID（設計書 §6）: サーバが同梱するエントリ name/summary を優先表示し、
      // 生 UUID は title 属性（ツールチップ）にのみ残す。API が未対応（name 無し）の
      // 場合は従来どおり UUID を本文に出す fail-soft フォールバック。
      (link.shared_part_id
        ? '<div class="deliberation-annotation-reason" title="' + escHtml(link.shared_part_id) + '">共通部品: ' +
            escHtml(link.shared_part_name || link.shared_part_id) +
            (link.shared_part_summary ? '｜' + escHtml(link.shared_part_summary) : '') +
          '</div>'
        : '') +
      (link.instance_element_type
        ? '<div class="deliberation-annotation-reason">インスタンス: ' + escHtml(link.instance_element_type) +
          (link.instance_document_id ? ' / ' + escHtml(link.instance_document_id) : '') + '</div>'
        : '') +
      (link.reason ? '<div class="deliberation-annotation-reason">' + escHtml(link.reason) + '</div>' : '') +
      (link.confidence_label ? '<span class="deliberation-annotation-confidence">' + escHtml(link.confidence_label) + '</span>' : '') +
      (pending
        ? '<div class="deliberation-annotation-actions" data-ui-anchor="deliberation.identity-link-decide">' +
            '<button type="button" class="deliberation-annotation-btn commit" data-identity-action="confirm" data-ui-anchor="deliberation.identity-link-decide">確定</button>' +
            '<button type="button" class="deliberation-annotation-btn dismiss" data-identity-action="reject" data-ui-anchor="deliberation.identity-link-decide">却下</button>' +
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
      path = "/admin/deliberation/elements/" + encodeURIComponent(ref.elementType) + "/" +
        encodeURIComponent(ref.elementId) + "/identity-links" + _documentIdQuery(ref);
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

  // ── 説明（element_explanations, migration 056）カード ─────────────────────
  // 正本: docs/features/hierarchical_context_explanation_design.md §5.2/§5.3。
  // overview.explanations（candidate + approved のみ。dismissed/superseded は既に
  // サーバ側で除外済み）を、面③の候補注釈カードと同じ見た目・操作パターン
  // （confirm/dismiss ボタン）で表示する。承認・却下は既存の element-explanations
  // 承認 API（/api/admin/element-explanations/{id}/approve|dismiss）を呼ぶ
  // （W層の annotations commit/dismiss とは別の独立した承認台帳・E2）。

  function _explanationCardHtml(exp) {
    exp = exp || {};
    var kindLabel = EXPLANATION_KIND_LABELS[exp.kind] || exp.kind || "";
    var statusLabel = EXPLANATION_STATUS_LABELS[exp.status] || exp.status || "";
    var evidence = exp.evidence || {};
    var pending = exp.status === "candidate";
    return '<div class="deliberation-annotation-card deliberation-explanation-card" data-explanation-id="' +
        escHtml(exp.id) + '">' +
      '<div class="deliberation-annotation-kind">' +
        escHtml(kindLabel) +
        (evidence.confidence_label
          ? ' <span class="deliberation-annotation-confidence">' + escHtml(evidence.confidence_label) + '</span>'
          : '') +
      '</div>' +
      '<div class="deliberation-annotation-body">' + escHtml(exp.body || "") + '</div>' +
      (evidence.evidence_quote ? '<div class="deliberation-annotation-reason">' + escHtml(evidence.evidence_quote) + '</div>' : '') +
      (evidence.reason ? '<div class="deliberation-annotation-reason">' + escHtml(evidence.reason) + '</div>' : '') +
      '<span class="deliberation-annotation-status' + (exp.status === "approved" ? " committed" : "") + '">' +
        escHtml(statusLabel) +
      '</span>' +
      '<div class="deliberation-annotation-error" style="display:none"></div>' +
      (pending
        ? '<div class="deliberation-annotation-actions">' +
            '<button type="button" class="deliberation-annotation-btn commit" data-explanation-action="approve">承認</button>' +
            '<button type="button" class="deliberation-annotation-btn dismiss" data-explanation-action="dismiss">却下</button>' +
          '</div>'
        : '') +
    '</div>';
  }

  function _explanationsSectionHtml(explanations) {
    if (!explanations || !explanations.available) return "";
    var items = explanations.items || [];
    var body = items.length
      ? items.map(_explanationCardHtml).join("")
      : '<p class="deliberation-identity-empty">説明はまだありません。</p>';
    return '<div class="deliberation-explanations-wrap" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 6px;font-size:14px;color:var(--color-text-primary)">説明（AI候補・承認済み）</h4>' +
      '<p style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px">' +
        'この要素の汎用説明・この論文での位置づけの説明候補です。承認するまで学習者には表示されません。' +
      '</p>' +
      '<div id="deliberation-explanations">' + body + '</div>' +
    '</div>';
  }

  function _decideExplanation(explanationId, action, card) {
    if (!explanationId) return;
    var buttons = card.querySelectorAll("[data-explanation-action]");
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    var path = "/admin/element-explanations/" + encodeURIComponent(explanationId) + "/" + action;
    apiFetch(path, { method: "POST" })
      .then(_parseJsonResponse)
      .then(function (data) {
        var updated = (data && data.explanation) || {};
        var wrapper = document.createElement("div");
        wrapper.innerHTML = _explanationCardHtml(updated);
        var newCard = wrapper.firstChild;
        if (card.parentNode) card.parentNode.replaceChild(newCard, card);
        _bindExplanationActions(newCard);
      })
      .catch(function (err) {
        var errEl = card.querySelector(".deliberation-annotation-error");
        var message = (err && err.detail) ||
          (action === "approve" ? "承認できませんでした（既に処理済みの可能性があります）" : "却下できませんでした");
        if (errEl) {
          errEl.style.display = "";
          errEl.innerHTML = escHtml(message);
        }
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      });
  }

  // root 省略時は document 全体から未バインドのボタンを拾う（_renderModalBody からの
  // 初回描画用）。個別カード差し替え後の再バインドは対象カードだけを渡す。
  function _bindExplanationActions(root) {
    var scope = root || document;
    Array.prototype.forEach.call(scope.querySelectorAll("[data-explanation-action]"), function (btn) {
      if (btn.getAttribute("data-explanation-bound") === "true") return;
      btn.setAttribute("data-explanation-bound", "true");
      btn.addEventListener("click", function () {
        var card = btn.closest(".deliberation-explanation-card");
        if (!card) return;
        _decideExplanation(card.getAttribute("data-explanation-id"), btn.getAttribute("data-explanation-action"), card);
      });
    });
  }

  // ── Phase S: 標準化度の評価（三角測量 worker の手動起動）。shared_part（共通部品）
  // にのみ表示する。評価結果は element_annotations(kind='standardization') の候補として
  // 既存の候補注釈カード（対話ペイン下）に現れる。自動確定はしない（教員の commit のみ）。
  function _standardizationSectionHtml(elementType) {
    if (elementType !== "shared_part") return "";
    return '<div class="deliberation-standardization" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--color-border-tertiary)">' +
      '<h4 style="margin:0 0 6px;font-size:14px;color:var(--color-text-primary)">標準化度</h4>' +
      '<div data-ui-anchor="deliberation.standardization-assess" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<button type="button" id="deliberation-standardization-assess" class="deliberation-chat-send" data-ui-anchor="deliberation.standardization-assess" style="padding:4px 10px;font-size:12px">標準化度を評価</button>' +
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
        // M層 Phase 3（§6.5）: この対話1回だけのモデル上書き。未選択ならサーバ既定に委ねる。
        if (chatState.selectedModel) messageBody.model = chatState.selectedModel;
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
        ? '<div class="deliberation-annotation-actions" data-ui-anchor="deliberation.annotation-decide">' +
            (canCommit ? '<button type="button" class="deliberation-annotation-btn commit" data-action="commit" data-ui-anchor="deliberation.annotation-decide">確定</button>' : '') +
            '<button type="button" class="deliberation-annotation-btn dismiss" data-action="dismiss" data-ui-anchor="deliberation.annotation-decide">却下</button>' +
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
    var path = "/admin/deliberation/elements/" + encodeURIComponent(elementType) + "/" +
      encodeURIComponent(elementId) + "/annotations" +
      _documentIdQuery({ elementType: elementType, documentId: documentId });
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
    return "/admin/deliberation/elements/" + encodeURIComponent(ref.elementType) + "/" +
      encodeURIComponent(ref.elementId) + "/overview" + _documentIdQuery(ref);
  }

  function _reloadOverview() {
    var ref = chatState.ref || {};
    return apiFetch(_overviewPath(ref))
      .then(_parseJsonResponse)
      .then(function (data) {
        _resetFigureImageState();
        _renderModalBody(data);
        _bindStandardizationAssessButton(ref);
        _bindIdentityLinkSearch(ref);
        _loadIdentityLinks(ref);
        return data;
      });
  }

  // ── 要素中心コンテキストビュー: 中心移動（Issue #498 §2.3/§6 Phase 2）────────
  // openElement によるモーダル破棄・再構築とは別に、隣接ノード選択・パンくずクリック
  // では既存モーダルを壊さず chatState.ref だけを差し替えて内容を再読込する
  // （読解の開始点を失わせない・パンくず履歴を保持する）。

  // モーダルを開いたままの要素切替の共通部分（overview 再取得＋描画・面③リセット）。
  // openElement 自身は使わない（モーダル構築前は #deliberation-modal-body 等の DOM が
  // 無いため）。
  function _loadAndRenderElement() {
    return apiFetch(_overviewPath(chatState.ref))
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
        _bindIdentityLinkSearch(chatState.ref);
        _loadIdentityLinks(chatState.ref);
        return data;
      })
      .catch(function (err) {
        _renderError(err && err.status);
      });
  }

  // 別要素へ中心移動するときの面③リセット（W6: セッションは次の送信時に新規作成
  // されるだけで、ここでは何も POST しない）。_closeModal は呼ばない
  // （モーダル自体・パンくず履歴を保持したまま要素だけ差し替える）。
  function _resetElementFocusState() {
    _resetFigureImageState();
    _resetFigureGuidanceState();
    chatState.sessionId = null;
    chatState.selectedContext = null;
    var messages = document.getElementById("deliberation-chat-messages");
    if (messages) messages.innerHTML = _chatEmptyStateHtml();
    var annotations = document.getElementById("deliberation-chat-annotations");
    if (annotations) annotations.innerHTML = "";
    _updateSelectedContextUi();
  }

  function _switchCenterElement(entry) {
    chatState.ref = {
      elementType: entry.elementType,
      elementId: entry.elementId,
      documentId: entry.documentId || null,
      title: entry.title || null
    };
    _resetElementFocusState();
    _loadAndRenderElement().then(function () {
      _loadAnnotations(entry.elementType, entry.elementId, entry.documentId);
    });
    _renderBreadcrumb();
  }

  // 上位/下位レーンの隣接ノードを選ぶと、そのノードを新しい中心に再配置する
  // （設計書 §2.3）。documentId が無い項目（例: equation は document_id 必須）は
  // 現在表示中の要素の documentId へフォールバックする。
  function _navigateToElement(elementType, elementId, documentId, title) {
    var entry = {
      elementType: elementType,
      elementId: elementId,
      documentId: documentId || (chatState.ref && chatState.ref.documentId) || null,
      title: title || null
    };
    navState.trail.push(entry);
    _switchCenterElement(entry);
  }

  // パンくずクリック: そのノードまで履歴を切り詰めて再表示する（新規に積み増さない）。
  function _goToTrailIndex(index) {
    if (index < 0 || index >= navState.trail.length) return;
    navState.trail = navState.trail.slice(0, index + 1);
    _switchCenterElement(navState.trail[navState.trail.length - 1]);
  }

  // パンくず＋「← 戻る」を描画する（設計書 §2.3: 読解の開始点を失わせない）。
  // 履歴が1件（初回表示）のときは何も出さない。
  function _renderBreadcrumb() {
    var container = document.getElementById("deliberation-breadcrumb");
    if (!container) return;
    var trail = navState.trail || [];
    if (trail.length < 2) {
      container.innerHTML = "";
      return;
    }
    var crumbsHtml = trail.map(function (entry, index) {
      var label = entry.title || elementTypeLabel(entry.elementType);
      if (index === trail.length - 1) {
        return '<span class="deliberation-breadcrumb-current">' + escHtml(label) + '</span>';
      }
      return '<button type="button" class="deliberation-breadcrumb-item" data-trail-index="' + index + '">' +
        escHtml(label) +
      '</button>';
    }).join('<span class="deliberation-breadcrumb-sep">›</span>');
    container.innerHTML =
      '<button type="button" id="deliberation-breadcrumb-back" class="deliberation-breadcrumb-back">← 戻る</button>' +
      '<span class="deliberation-breadcrumb-trail">' + crumbsHtml + '</span>';
    var backBtn = document.getElementById("deliberation-breadcrumb-back");
    if (backBtn) {
      backBtn.addEventListener("click", function () { _goToTrailIndex(trail.length - 2); });
    }
    Array.prototype.forEach.call(container.querySelectorAll("[data-trail-index]"), function (btn) {
      btn.addEventListener("click", function () {
        _goToTrailIndex(parseInt(btn.getAttribute("data-trail-index"), 10));
      });
    });
  }

  // ── 公開 API: openElement ────────────────────────────────────────────
  // opts = { documentId: string|null, title: string|null }
  // equation / evidence / derivation は document_id が必須（無ければ何もしない。
  // 設計書 §2 の equation 一意化の要件 + §16 で evidence / derivation にも適用）。
  function openElement(elementType, elementId, opts) {
    opts = opts || {};
    if (!deps.apiFetch && !window.apiFetch) return;
    if (_needsDocumentId(elementType) && !opts.documentId) return;

    _closeModal();
    chatState.ref = {
      elementType: elementType,
      elementId: elementId,
      documentId: opts.documentId || null,
      title: opts.title || null
    };
    // 中心移動の履歴をこの要素1件から開始する（設計書 §2.3）。
    navState.trail = [{
      elementType: elementType,
      elementId: elementId,
      documentId: opts.documentId || null,
      title: opts.title || null
    }];

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
            '<div id="deliberation-breadcrumb" class="deliberation-breadcrumb"></div>' +
            '<div id="deliberation-modal-body">' +
              '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
            '</div>' +
          '</div>' +
          '<div class="deliberation-chat-pane">' +
            '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">' +
              '<span style="font-size:11px;color:var(--color-text-tertiary)">対話</span>' +
              '<span id="deliberation-model-chip"></span>' +
            '</div>' +
            '<div class="deliberation-chat-messages" id="deliberation-chat-messages">' +
              _chatEmptyStateHtml() +
            '</div>' +
            '<div class="deliberation-chat-annotations" id="deliberation-chat-annotations"></div>' +
            '<div id="deliberation-chat-context" class="deliberation-chat-context" hidden>' +
              '<span>質問対象: <strong id="deliberation-chat-context-label"></strong></span>' +
              '<button id="deliberation-chat-context-clear" type="button" aria-label="質問対象を解除">解除</button>' +
            '</div>' +
            '<div class="deliberation-chat-inputrow" data-ui-anchor="deliberation.chat-send">' +
              '<textarea id="deliberation-chat-input" class="deliberation-chat-input" rows="2" placeholder="この要素について質問..."></textarea>' +
              '<button id="deliberation-chat-send" type="button" class="deliberation-chat-send" data-ui-anchor="deliberation.chat-send">送信</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    // M層 Phase 3（§6.5）: 対話のモデルチップ。figure 要素は vision 対応モデルのみに
    // 絞る（バックエンドの検証 scene と同じ "deliberation:vision" を使う。W層 dialogue.py の
    // MessageCreateRequest 検証がこの scene 名と一致していることが前提）。
    var modelChipMount = document.getElementById("deliberation-model-chip");
    if (modelChipMount && window.AdminLlmModels) {
      var chipScene = elementType === "figure" ? "deliberation:vision" : "deliberation";
      window.AdminLlmModels.createModelChip({
        sceneKey: chipScene,
        mountEl: modelChipMount,
        compact: true,
        onChange: function (model) { chatState.selectedModel = model; }
      });
    }

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
    // 初回表示は履歴1件のため何も描画しない（_renderBreadcrumb 内部の早期returnで空欄）。
    _renderBreadcrumb();
    _loadAndRenderElement();
  }

  // ── 説明レビューキュー（Element Explanation Review Queue）───────────────
  // element_explanations（migration 056）の candidate を document 単位で一覧し、
  // 要素ごとにグループ化して一括承認/却下できるようにする。1件ずつ「深く検討」を
  // 開いて承認する既存 UX（_explanationCardHtml/_decideExplanation, overview 内）は
  // そのまま残す（併存・非改変）。candidate-only 原則は維持: 承認するまで学習者には
  // 表示されない。取得はインベントリを開いた時の1回のみ（ポーリング禁止）。
  //
  // discuss 開幕素材（migration 062, element_type='document' / role='discussion_seed'）は
  // 係留先の要素が無いため、要素グループより上の独立グループとして先頭に置く
  // （discuss_opening_authoring_design.md §6.1）。このグループのカードは「深く検討」
  // 導線を持たず、代わりに本文のインライン編集（既存 PATCH: 旧行 superseded → 教員名義の
  // 新行 INSERT）を持つ。鮮度（§7.1）で元の解析結果が変わった承認済みの素材も同じ
  // グループに事実文付きで並べる（自動で非承認に落とさない）。
  var explanationReviewState = { documentId: null, items: null, selected: {} };
  var EXPLANATION_REVIEW_MAX_BULK = 200;
  var EXPLANATION_DOCUMENT_GROUP_LABEL = "この論文の議論のきっかけ";
  var EXPLANATION_STALE_NOTICE = "元の解析結果が変わっています";
  var EXPLANATION_REVIEW_STATUS_LABELS = {
    candidate: "候補",
    approved: "承認済み",
    dismissed: "却下",
    superseded: "差し替え済み"
  };

  function _resetExplanationReviewState() {
    explanationReviewState = { documentId: null, items: null, selected: {} };
  }

  // GET/POST とも同じ document スコープのベースパスを共有する（許可リストの
  // admin/documents 出現をこのヘルパー1箇所に集約し、呼び出し側では
  // 文字列リテラルを増やさない）。
  function _explanationReviewBasePath(documentId) {
    return "/admin/documents/" + encodeURIComponent(documentId) + "/element-explanations";
  }

  function _inventoryElementLabel(elementType, elementId) {
    var elements = (inventoryState.data && inventoryState.data.elements) || [];
    for (var i = 0; i < elements.length; i++) {
      var el = elements[i];
      if (el.element_type === elementType && el.element_id === elementId) {
        return el.label || elementId;
      }
    }
    var typeLabel = elementTypeLabel(elementType);
    return (typeLabel ? typeLabel + " " : "") + String(elementId || "").substring(0, 8);
  }

  // 開幕素材（document スコープ）か。element_type だけで判定する（role は
  // サーバ側の語彙で、キューの表示分岐はスコープの違いだけで足りる）。
  function _isDocumentScopeExplanation(exp) {
    return !!exp && exp.element_type === "document";
  }

  // 一括選択・単件承認/却下の対象になるのは candidate のみ（E2）。鮮度で並ぶ
  // approved 行はチェックボックスも承認/却下ボタンも持たない（API も 422 を返す）。
  function _explanationReviewSelectable(exp) {
    return !!exp && (exp.status || "candidate") === "candidate";
  }

  function _groupExplanationsByElement(items) {
    var groups = [];
    var index = {};
    var documentItems = [];
    (items || []).forEach(function (exp) {
      if (_isDocumentScopeExplanation(exp)) {
        documentItems.push(exp);
        return;
      }
      var key = exp.element_type + "|" + exp.element_id;
      if (!index[key]) {
        index[key] = {
          elementType: exp.element_type,
          elementId: exp.element_id,
          label: _inventoryElementLabel(exp.element_type, exp.element_id),
          items: []
        };
        groups.push(index[key]);
      }
      index[key].items.push(exp);
    });
    if (documentItems.length) {
      groups.unshift({
        elementType: "document",
        elementId: "",
        label: EXPLANATION_DOCUMENT_GROUP_LABEL,
        items: documentItems,
        documentScope: true
      });
    }
    return groups;
  }

  function _explanationReviewCardHtml(exp) {
    exp = exp || {};
    var kindLabel = EXPLANATION_KIND_LABELS[exp.kind] || exp.kind || "";
    var evidence = exp.evidence || {};
    var checked = explanationReviewState.selected[exp.id] ? " checked" : "";
    var selectable = _explanationReviewSelectable(exp);
    var editable = _isDocumentScopeExplanation(exp);
    var statusLabel = selectable ? "" : (EXPLANATION_REVIEW_STATUS_LABELS[exp.status] || exp.status || "");
    return '<div class="deliberation-annotation-card deliberation-explanation-review-card" data-explanation-id="' +
        escHtml(exp.id) + '">' +
      '<label style="display:flex;align-items:flex-start;gap:6px;cursor:pointer">' +
        (selectable
          ? '<input type="checkbox" data-explanation-review-checkbox="true" data-explanation-id="' +
              escHtml(exp.id) + '"' + checked + ' style="margin-top:3px">'
          : '') +
        '<span class="deliberation-annotation-kind">' +
          escHtml(kindLabel) +
          (evidence.confidence_label
            ? ' <span class="deliberation-annotation-confidence">' + escHtml(evidence.confidence_label) + '</span>'
            : '') +
          (statusLabel
            ? ' <span class="deliberation-annotation-status">' + escHtml(statusLabel) + '</span>'
            : '') +
          (exp.stale
            ? ' <span class="deliberation-annotation-status">' +
                escHtml(exp.stale_notice || EXPLANATION_STALE_NOTICE) + '</span>'
            : '') +
        '</span>' +
      '</label>' +
      '<div class="deliberation-annotation-body" style="margin-top:4px">' + escHtml(exp.body || "") + '</div>' +
      (evidence.evidence_quote ? '<div class="deliberation-annotation-reason">' + escHtml(evidence.evidence_quote) + '</div>' : '') +
      (evidence.reason ? '<div class="deliberation-annotation-reason">' + escHtml(evidence.reason) + '</div>' : '') +
      (editable
        ? '<div class="deliberation-explanation-review-editor" style="display:none;margin-top:6px">' +
            '<textarea data-explanation-edit-input="true" rows="4" ' +
              'style="width:100%;box-sizing:border-box;font-size:12.5px">' +
              escHtml(exp.body || "") + '</textarea>' +
            '<div style="display:flex;gap:6px;margin-top:4px">' +
              '<button type="button" class="deliberation-annotation-btn commit" data-explanation-edit-action="save" data-ui-anchor="deliberation.explanation-body-edit">本文を保存</button>' +
              '<button type="button" class="deliberation-annotation-btn" data-explanation-edit-action="cancel" data-ui-anchor="deliberation.explanation-body-edit">編集をやめる</button>' +
            '</div>' +
          '</div>'
        : '') +
      '<div class="deliberation-annotation-error" style="display:none"></div>' +
      '<div class="deliberation-annotation-actions">' +
        (selectable
          ? '<button type="button" class="deliberation-annotation-btn commit" data-explanation-review-action="approve">承認</button>' +
            '<button type="button" class="deliberation-annotation-btn dismiss" data-explanation-review-action="dismiss">却下</button>'
          : '') +
        (editable
          ? '<button type="button" class="deliberation-annotation-btn" data-explanation-edit-action="open" data-ui-anchor="deliberation.explanation-body-edit">本文を編集</button>'
          : '') +
      '</div>' +
    '</div>';
  }

  function _explanationReviewGroupHtml(group) {
    // document スコープのグループは、係留先の要素が無いこと（＝「深く検討」導線を
    // 持たないこと）を事実文で1行添える。要素グループの見た目は変えない。
    var note = group.documentScope
      ? '<p style="margin:0 0 6px;font-size:11.5px;color:var(--color-text-tertiary)">' +
          '論文全体に対する素材です。本文を編集してから承認できます。</p>'
      : '';
    // 「？使い方」の係留: 開幕素材グループだけは専用の節を持つ（要素グループは
    // 一括操作のツールバー側 deliberation.inventory-bulk-review が説明する）。
    var anchorAttr = group.documentScope
      ? ' data-ui-anchor="deliberation.discussion-seed-group"'
      : '';
    return '<section class="deliberation-explanation-review-group"' + anchorAttr + ' style="margin-bottom:10px">' +
      '<h4 style="margin:0 0 6px;font-size:13px;color:var(--color-text-primary)">' + escHtml(group.label) + '</h4>' +
      note +
      '<div style="display:flex;flex-direction:column;gap:8px">' +
        group.items.map(_explanationReviewCardHtml).join("") +
      '</div>' +
    '</section>';
  }

  function _explanationReviewSelectedIds() {
    var ids = [];
    (explanationReviewState.items || []).forEach(function (exp) {
      if (!_explanationReviewSelectable(exp)) return;
      if (explanationReviewState.selected[exp.id]) ids.push(exp.id);
    });
    return ids;
  }

  function _updateExplanationReviewToolbar() {
    var count = _explanationReviewSelectedIds().length;
    var approveBtn = document.getElementById("deliberation-explanation-review-approve-selected");
    var dismissBtn = document.getElementById("deliberation-explanation-review-dismiss-selected");
    if (approveBtn) {
      approveBtn.textContent = "選択した" + count + "件を承認";
      approveBtn.disabled = count === 0;
    }
    if (dismissBtn) {
      dismissBtn.textContent = "選択した" + count + "件を却下";
      dismissBtn.disabled = count === 0;
    }
  }

  function _setExplanationReviewMessage(text, isError) {
    var el = document.getElementById("deliberation-explanation-review-message");
    if (!el) return;
    el.style.color = isError ? "var(--color-text-danger)" : "var(--color-text-tertiary)";
    el.innerHTML = text ? escHtml(text) : "";
  }

  function _renderExplanationReviewList() {
    var list = document.getElementById("deliberation-explanation-review-list");
    if (!list) return;
    var items = explanationReviewState.items || [];
    if (!items.length) {
      list.innerHTML = '<p style="padding:16px;color:var(--color-text-tertiary);font-size:13px">' +
        'レビュー待ちの説明候補はありません。</p>';
    } else {
      list.innerHTML = _groupExplanationsByElement(items).map(_explanationReviewGroupHtml).join("");
    }
    _bindExplanationReviewCardEvents(list);
    _updateExplanationReviewToolbar();
  }

  function _removeExplanationsFromReviewQueue(ids) {
    var removed = {};
    (ids || []).forEach(function (id) { removed[id] = true; });
    explanationReviewState.items = (explanationReviewState.items || []).filter(function (exp) {
      return !removed[exp.id];
    });
    (ids || []).forEach(function (id) { delete explanationReviewState.selected[id]; });
  }

  // 本文編集の応答（新 revision 行）でキュー内の1件を差し替える。PATCH は旧行を
  // superseded にして新 id の行を返すため、選択状態と鮮度の印を新 id へ引き継ぐ
  // （キュー全体の再取得はしない）。
  function _replaceExplanationInReviewQueue(oldId, newRow) {
    if (!newRow || !newRow.id) return;
    var wasSelected = !!explanationReviewState.selected[oldId];
    explanationReviewState.items = (explanationReviewState.items || []).map(function (exp) {
      if (exp.id !== oldId) return exp;
      if (newRow.stale === undefined && exp.stale !== undefined) newRow.stale = exp.stale;
      if (newRow.stale_notice === undefined && exp.stale_notice !== undefined) {
        newRow.stale_notice = exp.stale_notice;
      }
      return newRow;
    });
    delete explanationReviewState.selected[oldId];
    if (wasSelected && _explanationReviewSelectable(newRow)) {
      explanationReviewState.selected[newRow.id] = true;
    }
  }

  function _toggleExplanationReviewEditor(card, open) {
    var editor = card.querySelector(".deliberation-explanation-review-editor");
    if (editor) editor.style.display = open ? "" : "none";
  }

  function _setExplanationReviewCardError(card, message) {
    var errEl = card.querySelector(".deliberation-annotation-error");
    if (!errEl) return;
    if (!message) {
      errEl.style.display = "none";
      errEl.innerHTML = "";
      return;
    }
    errEl.style.display = "";
    errEl.innerHTML = escHtml(message);
  }

  // 本文のインライン編集（既存 PATCH /api/admin/element-explanations/{id}）。
  // 教員が直した時点で書き手は教員になる（created_by=user_id の新行）。
  function _saveExplanationReviewBody(explanationId, card) {
    var input = card.querySelector("[data-explanation-edit-input]");
    if (!explanationId || !input) return;
    var newBody = String(input.value || "").trim();
    if (!newBody) {
      _setExplanationReviewCardError(card, "本文が空です");
      return;
    }
    _setExplanationReviewCardError(card, "");
    var buttons = card.querySelectorAll("[data-explanation-edit-action],[data-explanation-review-action]");
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    apiFetch("/admin/element-explanations/" + encodeURIComponent(explanationId), {
      method: "PATCH",
      body: JSON.stringify({ body: newBody })
    })
      .then(_parseJsonResponse)
      .then(function (data) {
        _replaceExplanationInReviewQueue(explanationId, (data && data.explanation) || null);
        _renderExplanationReviewList();
      })
      .catch(function (err) {
        _setExplanationReviewCardError(card, (err && err.detail) || "本文を保存できませんでした");
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      });
  }

  // 単件の承認/却下（キュー内カードのボタン）。既存 _decideExplanation は
  // overview 内カードの DOM 差し替え専用のため、キュー専用に別実装する
  // （呼ぶ API は同じ既存 element-explanations 承認 API）。
  function _decideExplanationReviewCard(explanationId, action, card) {
    if (!explanationId) return;
    var buttons = card.querySelectorAll("[data-explanation-review-action]");
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    var path = "/admin/element-explanations/" + encodeURIComponent(explanationId) + "/" + action;
    apiFetch(path, { method: "POST" })
      .then(_parseJsonResponse)
      .then(function () {
        _removeExplanationsFromReviewQueue([explanationId]);
        _renderExplanationReviewList();
        _renderExplanationReviewEntry();
      })
      .catch(function (err) {
        var errEl = card.querySelector(".deliberation-annotation-error");
        var message = (err && err.detail) ||
          (action === "approve" ? "承認できませんでした（既に処理済みの可能性があります）" : "却下できませんでした");
        if (errEl) {
          errEl.style.display = "";
          errEl.innerHTML = escHtml(message);
        }
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      });
  }

  function _bindExplanationReviewCardEvents(root) {
    if (!root) return;
    Array.prototype.forEach.call(root.querySelectorAll("[data-explanation-review-checkbox]"), function (cb) {
      cb.addEventListener("change", function () {
        var id = cb.getAttribute("data-explanation-id");
        if (cb.checked) explanationReviewState.selected[id] = true;
        else delete explanationReviewState.selected[id];
        _updateExplanationReviewToolbar();
      });
    });
    Array.prototype.forEach.call(root.querySelectorAll("[data-explanation-review-action]"), function (btn) {
      btn.addEventListener("click", function () {
        var card = btn.closest(".deliberation-explanation-review-card");
        if (!card) return;
        _decideExplanationReviewCard(card.getAttribute("data-explanation-id"), btn.getAttribute("data-explanation-review-action"), card);
      });
    });
    Array.prototype.forEach.call(root.querySelectorAll("[data-explanation-edit-action]"), function (btn) {
      btn.addEventListener("click", function () {
        var card = btn.closest(".deliberation-explanation-review-card");
        if (!card) return;
        var action = btn.getAttribute("data-explanation-edit-action");
        if (action === "open") {
          _toggleExplanationReviewEditor(card, true);
          return;
        }
        if (action === "cancel") {
          _setExplanationReviewCardError(card, "");
          _toggleExplanationReviewEditor(card, false);
          return;
        }
        _saveExplanationReviewBody(card.getAttribute("data-explanation-id"), card);
      });
    });
  }

  // 一括承認/却下: 実行前に事実文で確認する（admin.js の共通2段確認モーダルが
  // あればそれを使い、無ければ window.confirm にフォールバックする。versioning.js
  // の _doScheduleDeletion と同型のフォールバックパターン）。
  function _bulkReviewExplanations(action) {
    var ids = _explanationReviewSelectedIds();
    if (!ids.length) return;
    if (ids.length > EXPLANATION_REVIEW_MAX_BULK) {
      _setExplanationReviewMessage("一度に承認できるのは200件までです。選択数を減らしてください。", true);
      return;
    }
    var count = ids.length;
    var confirmMessage = action === "approve"
      ? count + "件の説明候補を承認します。承認済みの説明は学習者に表示されます。よろしいですか？"
      : count + "件の説明候補を却下します（候補は削除されず保持されます）。よろしいですか？";

    function _doBulkReview() {
      var approveBtn = document.getElementById("deliberation-explanation-review-approve-selected");
      var dismissBtn = document.getElementById("deliberation-explanation-review-dismiss-selected");
      if (approveBtn) approveBtn.disabled = true;
      if (dismissBtn) dismissBtn.disabled = true;
      _setExplanationReviewMessage("", false);
      var documentId = explanationReviewState.documentId;
      apiFetch(_explanationReviewBasePath(documentId) + "/bulk-review", {
        method: "POST",
        body: JSON.stringify({ action: action, explanation_ids: ids })
      })
        .then(_parseJsonResponse)
        .then(function (data) {
          var updated = (data && data.updated) || [];
          var skipped = (data && data.skipped) || [];
          _removeExplanationsFromReviewQueue(updated.map(function (row) { return row.id; }));
          _renderExplanationReviewList();
          _renderExplanationReviewEntry();
          if (skipped.length) {
            _setExplanationReviewMessage(skipped.length + "件は既に処理済みのためスキップされました", false);
          }
        })
        .catch(function (err) {
          _setExplanationReviewMessage((err && err.detail) || "一括処理に失敗しました", true);
        })
        .then(function () {
          _updateExplanationReviewToolbar();
        });
    }

    if (window.AdminDangerConfirm && typeof window.AdminDangerConfirm.open === "function") {
      window.AdminDangerConfirm.open({
        title: action === "approve" ? "説明候補の承認" : "説明候補の却下",
        message: confirmMessage,
        confirmLabel: action === "approve" ? "承認する" : "却下する"
      }, _doBulkReview);
    } else {
      if (!window.confirm(confirmMessage)) return;
      _doBulkReview();
    }
  }

  function _closeExplanationReviewModal() {
    var modal = document.getElementById("deliberation-explanation-review-modal");
    if (modal) modal.remove();
  }

  function _openExplanationReviewModal() {
    if (!explanationReviewState.items || !explanationReviewState.items.length) return;
    _closeExplanationReviewModal();

    var overlay = document.createElement("div");
    overlay.id = "deliberation-explanation-review-modal";
    // インベントリ（9400）より上・深く検討モーダル（9999）より下に置く
    // （キューから深く検討へ潜る操作導線は現状無いため単純に中間の値でよい）。
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;" +
      "background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9500";
    overlay.innerHTML =
      '<div class="deliberation-modal-dialog" style="width:min(760px,92vw);display:flex;flex-direction:column">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">説明レビュー</h3>' +
          '<button id="deliberation-explanation-review-close" type="button" style="background:none;border:none;color:var(--color-text-secondary);cursor:pointer;font-size:18px;padding:4px">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 10px">' +
          'AIが生成した説明の候補です。承認すると学習者に表示されます。' +
          '元の解析結果が変わった承認済みの素材も一覧に含まれます（承認は自動では外れません）。' +
        '</p>' +
        '<div data-ui-anchor="deliberation.inventory-bulk-review" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px">' +
          '<button type="button" id="deliberation-explanation-review-select-all" class="deliberation-annotation-btn">全選択</button>' +
          '<button type="button" id="deliberation-explanation-review-deselect-all" class="deliberation-annotation-btn">選択解除</button>' +
          '<button type="button" id="deliberation-explanation-review-approve-selected" class="deliberation-annotation-btn commit" data-ui-anchor="deliberation.inventory-bulk-review" disabled>選択した0件を承認</button>' +
          '<button type="button" id="deliberation-explanation-review-dismiss-selected" class="deliberation-annotation-btn dismiss" data-ui-anchor="deliberation.inventory-bulk-review" disabled>選択した0件を却下</button>' +
        '</div>' +
        '<div id="deliberation-explanation-review-message" style="font-size:11.5px;margin:0 0 8px"></div>' +
        '<div id="deliberation-explanation-review-list" style="overflow-y:auto;max-height:60vh;display:flex;flex-direction:column;gap:8px"></div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) _closeExplanationReviewModal(); });
    document.getElementById("deliberation-explanation-review-close").addEventListener("click", _closeExplanationReviewModal);
    document.getElementById("deliberation-explanation-review-select-all").addEventListener("click", function () {
      (explanationReviewState.items || []).forEach(function (exp) {
        // 鮮度で並ぶ承認済み行は一括操作の対象にしない（candidate-only, E2）。
        if (_explanationReviewSelectable(exp)) explanationReviewState.selected[exp.id] = true;
      });
      _renderExplanationReviewList();
    });
    document.getElementById("deliberation-explanation-review-deselect-all").addEventListener("click", function () {
      explanationReviewState.selected = {};
      _renderExplanationReviewList();
    });
    document.getElementById("deliberation-explanation-review-approve-selected").addEventListener("click", function () {
      _bulkReviewExplanations("approve");
    });
    document.getElementById("deliberation-explanation-review-dismiss-selected").addEventListener("click", function () {
      _bulkReviewExplanations("dismiss");
    });

    _renderExplanationReviewList();
  }

  // インベントリモーダルのツールバー付近に置く入口ボタン。件数はラベルにのみ
  // 出す（W8: 数値以外の煽り表現はしない・件数そのものは事実として出してよい）。
  // items が null（未取得 or 取得失敗）のときはボタンごと出さない（fail-closed）。
  function _renderExplanationReviewEntry() {
    var container = document.getElementById("deliberation-explanation-review-entry");
    if (!container) return;
    var items = explanationReviewState.items;
    if (!items) {
      container.innerHTML = "";
      return;
    }
    var count = items.length;
    container.innerHTML = '<button type="button" id="deliberation-explanation-review-open" ' +
      'class="deliberation-annotation-btn"' + (count === 0 ? " disabled" : "") + '>' +
      '説明レビュー (' + count + ')</button>';
    var btn = document.getElementById("deliberation-explanation-review-open");
    if (btn) btn.addEventListener("click", _openExplanationReviewModal);
  }

  // 鮮度（設計書 §7.1）: 承認済みの開幕素材のうち、元の解析結果が変わったもの
  // （サーバが stale=true を付ける）だけをキューに合流させる。承認は自動で外さない
  // ため、教員が気づける場所はこのキューだけになる。取得失敗時は候補一覧のみで続ける
  // （キュー自体を殺さない）。
  function _loadStaleApprovedDocumentExplanations(documentId, candidates) {
    return apiFetch(_explanationReviewBasePath(documentId) + "?element_type=document&status=approved")
      .then(_parseJsonResponse)
      .then(function (data) {
        var stale = ((data && data.explanations) || []).filter(function (exp) {
          return !!exp && exp.stale === true;
        });
        return candidates.concat(stale);
      })
      .catch(function () {
        return candidates;
      });
  }

  // GET のみ・DB非変更。openInventory 時に1回だけ呼ぶ（ポーリング禁止・§9 と同じ規約）。
  function _loadExplanationReviewQueue(documentId) {
    return apiFetch(_explanationReviewBasePath(documentId) + "?status=candidate")
      .then(_parseJsonResponse)
      .then(function (data) {
        return _loadStaleApprovedDocumentExplanations(documentId, (data && data.explanations) || []);
      })
      .then(function (items) {
        explanationReviewState.documentId = documentId;
        explanationReviewState.items = items || [];
        explanationReviewState.selected = {};
        _renderExplanationReviewEntry();
      })
      .catch(function () {
        // fail-closed: 一覧取得に失敗したらボタンごと出さない（インベントリ本体の
        // 表示は止めない・エラーを目立たせない）。
        explanationReviewState.items = null;
        _renderExplanationReviewEntry();
      });
  }

  // ── 要素インベントリ: モーダル DOM / 描画 ───────────────────────────────

  function _closeInventoryModal() {
    _closeExplanationReviewModal();
    _resetExplanationReviewState();
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
          escHtml(elementTypeLabel(el.element_type)) +
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
        escHtml(inventoryTypeLabel(key)) + '（' + count + '）</button>';
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
            return inventoryTypeLabel(t) + "は500件で省略されています";
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
    _resetExplanationReviewState();

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
        '<div id="deliberation-inventory-toolbar" data-ui-anchor="deliberation.inventory-filter" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px"></div>' +
        '<div id="deliberation-explanation-review-entry" style="margin:0 0 8px"></div>' +
        '<div id="deliberation-inventory-truncated-note" style="font-size:11.5px;color:var(--color-text-tertiary);margin:0 0 8px"></div>' +
        '<div id="deliberation-inventory-list" style="overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:8px">' +
          '<div style="padding:16px;color:var(--color-text-tertiary);font-size:13px">読み込み中...</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (e) { if (e.target === overlay) _closeInventoryModal(); });
    document.getElementById("deliberation-inventory-close").addEventListener("click", _closeInventoryModal);

    _loadInventory();
    _loadExplanationReviewQueue(documentId);
  }

  window.Deliberation = {
    init: init,
    openElement: openElement,
    openInventory: openInventory
  };
})();
