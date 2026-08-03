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
 *   dto = {focus, upper: [ITEM], lower: [ITEM], notes: [str], derivations: [DERIVATION]}
 *   focus = {element_type, element_id, document_id, label, intrinsic_summary,
 *            contextual_role, contextual_role_status, generic}
 *   ITEM  = {element_type, element_id, document_id, label, relation, relation_label,
 *            relation_status, evidence_refs, navigable}
 *
 * DTO v2（docs/features/element_context_presentation_redesign.md §4、additive）:
 *   ITEM に sublabel / qualifier / group / unresolved / label_source が増える。
 *   group を持つ ITEM が1つでもあれば**4区画ゾーン描画**へ切り替える
 *   （位置づけ / 組み立て / この先 / 関連）。group が無い旧 DTO は従来の
 *   upper・lower 2レーン描画のまま（完全後方互換）。
 *   focus に headline / intrinsic / placement / derivations が増える。
 *   derivations（equation focus）は「入力 →[操作]→ 出力」のストーリーカードで描く。
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
  var ELEMENT_TYPE_SYMBOL = "symbol";
  var ELEMENT_TYPE_DERIVATION = "derivation";

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

  // ── 4区画ゾーン描画（DTO v2）──────────────────────────────────────────
  // ゾーンの順序と語彙は S1〜S4 で共通（場面差は「どこまで出すか」だけ）。
  var ZONE_ORDER = ["positioning", "composition", "forward", "related"];
  var ZONE_FORWARD = "forward";
  var ZONE_RELATED = "related";
  var GROUP_OPERATION = "operation";
  var GROUP_DERIVATION_IN = "derivation_in";
  var GROUP_DERIVATION_OUT = "derivation_out";
  // group ごとの既定表示件数。超過分は「ほかN件」の折りたたみへ（情報は落とさない）。
  var GROUP_ITEM_LIMIT = 5;
  var MORE_PREFIX = "ほか";
  var MORE_SUFFIX = "件";
  // 式の詳細層（traceability）は教員のみ・既定折りたたみ（CP3）。
  var OPERATION_SUMMARY_PREFIX = "式の詳細層（traceability・";
  var OPERATION_SUMMARY_SUFFIX = "件）";

  // ── 「これは何か」（focus.intrinsic / focus.placement）の行見出し ──────────
  var INTRINSIC_LABEL_ROLE = "役割";
  var INTRINSIC_LABEL_SUMMARY = "意味";
  var INTRINSIC_LABEL_READING = "読み";
  var INTRINSIC_LABEL_SYMBOLS = "記号";
  var INTRINSIC_LABEL_CONDITIONS = "成立条件";
  var INTRINSIC_LABEL_SECTION = "掲載";
  var INTRINSIC_LABEL_STAGE = "理論の段階";
  var INTRINSIC_LABEL_THESIS = "中心命題での役割";
  // 英語自由文はそのまま完全表示し、出所だけ注記する（§2.4 / Q3）。
  var SOURCE_LANGUAGE_NOTE = "（論文の原文）";
  var SYMBOL_DEFINED_HERE = "（この式で定義）";

  // ── 導出ストーリーカード（focus.derivations）──────────────────────────
  var DERIVATION_SELF = "この要素";
  var DERIVATION_UNKNOWN = "（同定されていません）";
  var DERIVATION_ROLE_LABEL = "この導出でのこの要素の役割";
  var DERIVATION_ROLE_LABELS = {
    input: "入力",
    output: "出力",
    intermediate: "中間量",
    condition: "条件",
    unspecified: "（同定されていません）"
  };
  var DERIVATION_ELIMINATED_LABEL = "消える記号";
  var DERIVATION_RETAINED_LABEL = "残る記号";
  var DERIVATION_JOIN = " ／ ";
  var IDENTIFIER_SUMMARY = "識別子";
  var CENTER_LABEL = "中心にする";

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

  function vocabCall(name, a, b) {
    var vocab = global.ElementVocab;
    if (vocab && typeof vocab[name] === "function") return vocab[name](a, b);
    return "";
  }

  function vocabZone(group) {
    return vocabCall("zoneForGroup", group) || ZONE_RELATED;
  }

  function vocabZoneHeading(zone) {
    return vocabCall("zoneHeading", zone);
  }

  function vocabGroupHeading(group) {
    return vocabCall("groupHeading", group);
  }

  function vocabQualifierLabel(elementType, key) {
    return vocabCall("qualifierLabel", elementType, key);
  }

  // 統制語彙キー（intrinsic.role_key 等）の表示名。種別で引ける qualifierLabel を
  // 第一候補にし、種別横断の統制語彙表を順に試す。未知は "" のまま（fail-closed:
  // 内部語彙を画面に出さない）。訳語はすべて element-vocab.js が正本（CP4）。
  var TERM_LOOKUPS = [
    "equationRoleLabel",
    "claimTypeLabel",
    "chainTypeLabel",
    "definitionStatusLabel",
    "theoryStageLabel",
    "linkStatusLabel",
    "operationLabel"
  ];

  function vocabTermLabel(elementType, key) {
    var raw = textOf(key);
    if (!raw) return "";
    var direct = vocabQualifierLabel(elementType, raw);
    if (direct) return direct;
    for (var i = 0; i < TERM_LOOKUPS.length; i++) {
      var value = vocabCall(TERM_LOOKUPS[i], raw);
      if (value) return value;
    }
    return "";
  }

  // 数式レンダラ（opts.renderMath）へ渡してよい文字列かのゲート。
  // 設計書 §6 S3-5: このガードは「admin=無ガードで赤いエラー / W層=未注入で生 TeX /
  // 学習=ガード付き」の3通りの壊れ方を終わらせるためカード内製とする。
  // 呼び出し側に同じ判定を再実装しないこと（唯一の実装）。
  // symbolMode: 記号（\delta(t,x) のような短い1コマンド式）はコマンド1個でも
  // レンダリングを許す。プロンプト由来の長い平文が紛れ込まないよう長さで縛る。
  var SYMBOL_TEX_MAX_LENGTH = 40;

  function looksLikeRenderableTex(text, symbolMode) {
    var t = textOf(text);
    if (!t) return false;
    if (symbolMode && t.length > SYMBOL_TEX_MAX_LENGTH) return false;
    var hasEnv = /\\begin\{[a-zA-Z*]+\}/.test(t);
    var commands = t.match(/\\[a-zA-Z]+/g) || [];
    if (!hasEnv && commands.length < (symbolMode ? 1 : 2)) return false;
    if (hasEnv && !/\\end\{[a-zA-Z*]+\}/.test(t)) return false; // 環境が閉じていない
    var opens = (t.match(/(^|[^\\])\{/g) || []).length;
    var closes = (t.match(/(^|[^\\])\}/g) || []).length;
    return opens === closes; // 切り詰めで壊れた TeX を弾く
  }

  // ゲートを通ったときだけ opts.renderMath を呼ぶ。失敗・非 TeX は "" を返し、
  // 呼び出し側が素のテキスト表示へ落とす。
  function renderMathGated(ctx, expr, display, symbolMode) {
    if (!ctx.renderMath) return "";
    if (!looksLikeRenderableTex(expr, symbolMode)) return "";
    var rendered = "";
    try {
      rendered = ctx.renderMath(textOf(expr), !!display);
    } catch (e) {
      rendered = "";
    }
    return rendered || "";
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
      // 「これは何か」（focus.intrinsic / placement）の定義リスト。外殻カードが
      // 既に同じ内訳を出している画面だけが明示的に隠す（hideBody とは独立 —
      // hideBody は旧 intrinsic_summary 本文のみを対象にする）。
      hideIntrinsic: !!opts.hideIntrinsic,
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
      // 式の詳細層（traceability）は教員のみ（CP3）。学習者射影でも除去されるが
      // ここでも fail-closed で落とす。
      if (ctx.readonly && textOf(item.group) === GROUP_OPERATION) continue;
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
    // DTO v2 ではラベルラダーの結果が headline に載る（focus.label と同値の契約）。
    // 旧 DTO は label しか持たないため両方を見る。
    var title = textOf(focus.headline) || textOf(focus.label);
    // 見出しと本文が同じ文字列（equation は context_lens が両方に式本文を入れる）の
    // ときは見出し側を出さない（同じ内容の二重表示を防ぐ。本文側は数式レンダリングが
    // 効くため本文を残す）。CP1 は生成側の契約だが、旧応答向けの保険として残す。
    if (title && title === textOf(focus.intrinsic_summary)) title = "";
    if (title && isPlainObject(focus.intrinsic) && title === textOf(focus.intrinsic.summary)) {
      title = "";
    }
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
    // 「これは何か」の定義リストが出ているかは同じ生成関数の結果で判定する
    // （render 側の判断と食い違わせないため。純粋な文字列組み立てなので再呼び出し可）。
    var hasIntrinsic = !ctx.hideIntrinsic && !!intrinsicHtml(focus, ctx);
    if (!summary) {
      // 「これは何か」の定義リストが出ているなら、そこが本文の役目を果たす。
      if (hasIntrinsic) return "";
      return '<div class="element-card-body element-card-fact">' +
        ctx.esc(FACT_NO_BODY) + "</div>";
    }
    // intrinsic.summary と同じ文字列は二度出さない（CP1）。
    if (hasIntrinsic && isPlainObject(focus.intrinsic) &&
        textOf(focus.intrinsic.summary) === summary) {
      return "";
    }
    if (textOf(focus.element_type) === ELEMENT_TYPE_EQUATION) {
      // renderMath は呼び出し側の数式レンダラ。ゲート（looksLikeRenderableTex）を
      // 通った文字列だけを渡す。レンダラへの直接依存は持たない。
      var rendered = renderMathGated(ctx, summary, true);
      if (rendered) {
        return '<div class="element-card-body element-card-formula">' + rendered + "</div>";
      }
    }
    return '<div class="element-card-body">' + ctx.esc(summary) + "</div>";
  }

  // ── ①これは何か（focus.intrinsic）+ ②この論文での位置づけ（focus.placement）──
  // 値の無いキーは行ごと出さない（推測で穴埋めしない）。confidence は読まない（W8）。

  function textListItems(values) {
    var arr = asArray(values);
    var out = [];
    for (var i = 0; i < arr.length; i++) {
      var text = textOf(arr[i]);
      if (text) out.push(text);
    }
    return out;
  }

  function defRowHtml(label, valueHtml, ctx) {
    return '<div class="element-card-def-row">' +
      '<span class="element-card-def-label">' + ctx.esc(label) + "</span>" +
      '<span class="element-card-def-value">' + valueHtml + "</span>" +
      "</div>";
  }

  // 記号は「式の再掲」ではなく読解の部品なので表示する（EH1 の対象外）。
  // 記号そのものはゲート付きでレンダリングし、通らなければ素のまま出す。
  function intrinsicSymbolsHtml(symbols, ctx) {
    var arr = asArray(symbols);
    var parts = [];
    for (var i = 0; i < arr.length; i++) {
      var sym = arr[i];
      if (!isPlainObject(sym)) continue;
      var name = textOf(sym.symbol);
      if (!name) continue;
      var nameHtml = renderMathGated(ctx, name, false, true) || ctx.esc(name);
      var meaning = textOf(sym.meaning);
      if (!meaning) meaning = vocabCall("definitionStatusLabel", sym.definition_status);
      parts.push('<span class="element-card-symbol">' +
        '<span class="element-card-symbol-name">' + nameHtml + "</span>" +
        (meaning
          ? '<span class="element-card-symbol-meaning">' + ctx.esc(meaning) + "</span>"
          : "") +
        (sym.defined_here
          ? '<span class="element-card-symbol-note">' + ctx.esc(SYMBOL_DEFINED_HERE) + "</span>"
          : "") +
        "</span>");
    }
    return parts.join("");
  }

  function intrinsicHtml(focus, ctx) {
    var intrinsic = isPlainObject(focus.intrinsic) ? focus.intrinsic : null;
    var placement = isPlainObject(focus.placement) ? focus.placement : null;
    if (!intrinsic && !placement) return "";
    var rows = [];
    var facts = [];
    if (intrinsic) {
      var role = vocabTermLabel(focus.element_type, intrinsic.role_key);
      if (role) rows.push(defRowHtml(INTRINSIC_LABEL_ROLE, ctx.esc(role), ctx));
      var summary = textOf(intrinsic.summary);
      if (summary) {
        rows.push(defRowHtml(INTRINSIC_LABEL_SUMMARY,
          ctx.esc(summary) +
          (intrinsic.summary_is_source_language
            ? '<span class="element-card-source-note">' +
              ctx.esc(SOURCE_LANGUAGE_NOTE) + "</span>"
            : ""),
          ctx));
      }
      var reading = textOf(intrinsic.reading);
      if (reading) rows.push(defRowHtml(INTRINSIC_LABEL_READING, ctx.esc(reading), ctx));
      var symbolsHtml = intrinsicSymbolsHtml(intrinsic.symbols, ctx);
      if (symbolsHtml) rows.push(defRowHtml(INTRINSIC_LABEL_SYMBOLS, symbolsHtml, ctx));
      var conditions = textListItems(intrinsic.conditions);
      if (conditions.length) {
        rows.push(defRowHtml(INTRINSIC_LABEL_CONDITIONS,
          ctx.esc(conditions.join(DERIVATION_JOIN)), ctx));
      }
      facts = textListItems(intrinsic.facts);
    }
    if (placement) {
      var section = textOf(placement.section_label);
      if (section) rows.push(defRowHtml(INTRINSIC_LABEL_SECTION, ctx.esc(section), ctx));
      var stage = isPlainObject(placement.stage) ? placement.stage : null;
      if (stage) {
        var stageLabel = vocabCall("theoryStageLabel", stage.key);
        var description = textOf(stage.description);
        if (stageLabel || description) {
          rows.push(defRowHtml(INTRINSIC_LABEL_STAGE,
            ctx.esc(stageLabel) +
            (description
              ? '<span class="element-card-def-note">' + ctx.esc(description) + "</span>"
              : ""),
            ctx));
        }
      }
      var thesisRole = textOf(placement.thesis_role);
      if (thesisRole) {
        rows.push(defRowHtml(INTRINSIC_LABEL_THESIS, ctx.esc(thesisRole), ctx));
      }
    }
    var factsHtml = "";
    for (var f = 0; f < facts.length; f++) {
      factsHtml += '<div class="element-card-fact">' + ctx.esc(facts[f]) + "</div>";
    }
    if (!rows.length && !factsHtml) return "";
    return '<div class="element-card-intrinsic">' + rows.join("") + factsHtml + "</div>";
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

  // ITEM 1行の中身。DTO v2 では
  //   1行目 = 種別チップ / 関係語 / ラベル / qualifier チップ / 裏付けバッジ
  //   2行目 = sublabel（1行の区別材料・事実文。**readonly でも描く**）
  // 記号（symbol）のラベルは読解の部品なのでゲート付きでレンダリングする。
  function itemInnerHtml(item, ctx) {
    var label = textOf(item.label);
    // 種別チップとラベルが同じ文字列のときはチップを出さない（「中心命題 中心命題」を防ぐ）。
    var typeLabel = textOf(vocabElementTypeLabel(item.element_type));
    if (typeLabel === label) typeLabel = "";
    var labelHtml = "";
    if (textOf(item.element_type) === ELEMENT_TYPE_SYMBOL) {
      labelHtml = renderMathGated(ctx, label, false, true);
    }
    if (!labelHtml) labelHtml = ctx.esc(label);
    var qualifier = vocabQualifierLabel(item.element_type, item.qualifier);
    // ラベルと同じ文字列の qualifier はチップにしない（stage は label が段階訳語、
    // qualifier が段階キーなので訳すと一致する）。
    if (qualifier === label) qualifier = "";
    var sublabel = textOf(item.sublabel);
    return (typeLabel
        ? '<span class="element-card-item-kind">' + ctx.esc(typeLabel) + "</span>"
        : "") +
      (textOf(item.relation_label)
        ? '<span class="element-card-item-relation">' +
          ctx.esc(textOf(item.relation_label)) + "</span>"
        : "") +
      '<span class="element-card-item-label">' + labelHtml + "</span>" +
      (qualifier
        ? '<span class="element-card-item-qualifier">' + ctx.esc(qualifier) + "</span>"
        : "") +
      statusBadgeHtml(item.relation_status, ctx, "") +
      (sublabel
        ? '<span class="element-card-item-sub">' + ctx.esc(sublabel) + "</span>"
        : "");
  }

  function itemHtml(item, lane, index, ctx) {
    var id = itemId(item);
    var navigable = !!item.navigable && !!id && !!ctx.onCenter;
    // 参照先の名称が解決できず ID のまま表示している ITEM は淡色 + ツールチップで明示する。
    var unresolvedCls = item.unresolved ? " element-card-item-unresolved" : "";
    var unresolvedAttr = item.unresolved
      ? ' title="' + ctx.esc(FACT_UNRESOLVED_ITEM) + '"'
      : "";
    // ゾーン描画では ITEM が元のレーン順に並ばないため、呼び出し側（教材内ジャンプ等）が
    // 行 → ITEM を引けるよう参照キーを全行に付ける（値は visibleItems 内の添字）。
    var refAttr = ' data-element-card-itemref="' + ctx.esc(lane + ":" + index) + '"';
    var inner = itemInnerHtml(item, ctx);
    var itemEl;
    if (!navigable) {
      itemEl = '<div class="element-card-item' + unresolvedCls + '"' + unresolvedAttr +
        refAttr + ">" + inner + "</div>";
    } else {
      itemEl = '<button type="button" class="element-card-item element-card-item-navigable' +
        unresolvedCls + '"' + unresolvedAttr + refAttr +
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

  // 従来の上位/下位2レーン描画（group を持たない旧 DTO 用・完全後方互換）。
  function legacyLanesHtml(dto, ctx) {
    return '<div class="element-card-lanes">' +
      laneHtml(dto, LANE_UPPER, ctx) +
      laneHtml(dto, LANE_LOWER, ctx) +
      "</div>";
  }

  // ---------------------------------------------------------------------------
  // 4区画ゾーン描画（DTO v2 の group を持つ ITEM がある場合）
  // ---------------------------------------------------------------------------

  // 上位・下位の ITEM を1本にまとめる。lane と添字は data 属性へ残すため保持する
  // （bind は従来どおり visibleItems の添字で ITEM を引く）。
  function laneEntries(dto, ctx) {
    var lanes = [LANE_UPPER, LANE_LOWER];
    var out = [];
    for (var l = 0; l < lanes.length; l++) {
      var items = visibleItems(dto, lanes[l], ctx);
      for (var i = 0; i < items.length; i++) {
        out.push({ item: items[i], lane: lanes[l], index: i });
      }
    }
    return out;
  }

  function hasGroupedItems(entries) {
    for (var i = 0; i < entries.length; i++) {
      if (textOf(entries[i].item.group)) return true;
    }
    return false;
  }

  // 導出ストーリーカード（focus.derivations / dto.derivations）。
  // readonly では candidate な導出を出さない（LE2 の二重ガード）。
  function derivationsOf(dto, ctx) {
    if (!isPlainObject(dto)) return [];
    var raw = asArray(dto.derivations);
    if (!raw.length) raw = asArray(focusOf(dto).derivations);
    var out = [];
    for (var i = 0; i < raw.length; i++) {
      var d = raw[i];
      if (!isPlainObject(d)) continue;
      if (ctx.readonly && textOf(d.relation_status) === STATUS_CANDIDATE) continue;
      out.push(d);
    }
    return out;
  }

  function miniLabelsText(list) {
    var arr = asArray(list);
    var parts = [];
    for (var i = 0; i < arr.length; i++) {
      if (!isPlainObject(arr[i])) continue;
      var label = textOf(arr[i].label);
      if (label) parts.push(label);
    }
    return parts.join("・");
  }

  function derivationHeadHtml(d, ctx) {
    var chain = vocabQualifierLabel(ELEMENT_TYPE_DERIVATION, d.chain_type);
    return '<div class="element-card-derivation-head">' +
      '<span class="element-card-derivation-title">' + ctx.esc(textOf(d.label)) + "</span>" +
      (chain
        ? '<span class="element-card-item-qualifier">' + ctx.esc(chain) + "</span>"
        : "") +
      statusBadgeHtml(d.relation_status, ctx, "") +
      "</div>";
  }

  // 「この要素 ─[操作]→ 出力」。向きが同定できていない側は事実文で明示する（CP10）。
  function derivationFlowHtml(d, ctx) {
    var role = textOf(d.focus_role);
    var left = (role === "input" || role === "intermediate")
      ? DERIVATION_SELF
      : miniLabelsText(d.inputs);
    var right = role === "output" ? DERIVATION_SELF : miniLabelsText(d.outputs);
    if (!left) left = DERIVATION_UNKNOWN;
    if (!right) right = DERIVATION_UNKNOWN;
    var operation = textOf(d.operation_text);
    var arrow = operation ? " ─[" + operation + "]→ " : " → ";
    return '<div class="element-card-derivation-flow">' +
      ctx.esc(left + arrow + right) + "</div>";
  }

  function derivationMetaHtml(d, ctx) {
    var parts = [];
    var role = DERIVATION_ROLE_LABELS[textOf(d.focus_role)];
    if (role) parts.push(DERIVATION_ROLE_LABEL + ": " + role);
    var eliminated = textListItems(d.eliminated_symbols);
    if (eliminated.length) {
      parts.push(DERIVATION_ELIMINATED_LABEL + ": " + eliminated.join("・"));
    }
    var retained = textListItems(d.retained_symbols);
    if (retained.length) {
      parts.push(DERIVATION_RETAINED_LABEL + ": " + retained.join("・"));
    }
    if (!parts.length) return "";
    return '<div class="element-card-derivation-meta">' +
      ctx.esc(parts.join(DERIVATION_JOIN)) + "</div>";
  }

  // 内部 ID（derivation_id / evidence_refs）は教員の折りたたみの中だけ（EC3′）。
  function derivationIdentifiersHtml(d, ctx) {
    if (!ctx.editable) return "";
    var values = [];
    var id = textOf(d.derivation_id);
    if (id) values.push(id);
    var refs = asArray(d.evidence_refs);
    for (var i = 0; i < refs.length; i++) {
      var ref = textOf(refs[i]);
      if (ref) values.push(ref);
    }
    if (!values.length) return "";
    var lis = [];
    for (var v = 0; v < values.length; v++) lis.push("<li>" + ctx.esc(values[v]) + "</li>");
    return '<details class="element-card-item-refs"><summary>' +
      ctx.esc(IDENTIFIER_SUMMARY) + "</summary><ul>" + lis.join("") + "</ul></details>";
  }

  function derivationActionsHtml(d, index, ctx) {
    if (!ctx.editable) return "";
    var parts = [];
    var id = textOf(d.derivation_id);
    if (ctx.onCenter && d.navigable && id) {
      parts.push('<button type="button" class="element-card-item-action"' +
        ' data-element-card-derivation="' + ctx.esc(index + ":center") + '">' +
        ctx.esc(CENTER_LABEL) + "</button>");
    }
    if (ctx.onDeliberate && id) {
      parts.push('<button type="button" class="element-card-item-action"' +
        ' data-element-card-derivation="' + ctx.esc(index + ":deliberate") + '">' +
        ctx.esc(ctx.deliberateLabel) + "</button>");
    }
    if (!parts.length) return "";
    return '<div class="element-card-derivation-actions">' + parts.join("") + "</div>";
  }

  function derivationStoriesHtml(derivations, ctx) {
    if (!derivations.length) return "";
    var parts = [];
    for (var i = 0; i < derivations.length; i++) {
      var d = derivations[i];
      var sublabel = textOf(d.sublabel);
      var reason = textOf(d.reason);
      parts.push('<div class="element-card-derivation">' +
        derivationHeadHtml(d, ctx) +
        (sublabel
          ? '<div class="element-card-derivation-sub">' + ctx.esc(sublabel) + "</div>"
          : "") +
        derivationFlowHtml(d, ctx) +
        derivationMetaHtml(d, ctx) +
        (reason && reason !== sublabel
          ? '<div class="element-card-derivation-sub">' + ctx.esc(reason) + "</div>"
          : "") +
        derivationIdentifiersHtml(d, ctx) +
        derivationActionsHtml(d, i, ctx) +
        "</div>");
    }
    return '<div class="element-card-derivations">' + parts.join("") + "</div>";
  }

  function groupItemsHtml(entries, ctx) {
    var parts = [];
    for (var i = 0; i < entries.length; i++) {
      parts.push(itemHtml(entries[i].item, entries[i].lane, entries[i].index, ctx));
    }
    return '<div class="element-card-items">' + parts.join("") + "</div>";
  }

  // 式の詳細層は教員のみ・既定折りたたみ（CP3 / §6 の差分表）。
  function operationGroupHtml(entries, ctx) {
    if (!ctx.editable || !entries.length) return "";
    return '<details class="element-card-group element-card-group-operation"><summary>' +
      ctx.esc(OPERATION_SUMMARY_PREFIX + entries.length + OPERATION_SUMMARY_SUFFIX) +
      "</summary>" + groupItemsHtml(entries, ctx) + "</details>";
  }

  function groupHtml(group, entries, ctx) {
    if (group === GROUP_OPERATION) return operationGroupHtml(entries, ctx);
    var heading = vocabGroupHeading(group);
    var head = entries.slice(0, GROUP_ITEM_LIMIT);
    var rest = entries.slice(GROUP_ITEM_LIMIT);
    return '<div class="element-card-group">' +
      (heading
        ? '<div class="element-card-group-title">' + ctx.esc(heading) + "</div>"
        : "") +
      groupItemsHtml(head, ctx) +
      (rest.length
        ? '<details class="element-card-group-more"><summary>' +
          ctx.esc(MORE_PREFIX + rest.length + MORE_SUFFIX) + "</summary>" +
          groupItemsHtml(rest, ctx) + "</details>"
        : "") +
      "</div>";
  }

  function zoneBuckets(entries, skipDerivationGroups) {
    var buckets = {};
    for (var i = 0; i < entries.length; i++) {
      var group = textOf(entries[i].item.group);
      // 導出ストーリーカードを描くときは、同じ導出の ITEM 行を重複させない。
      if (skipDerivationGroups &&
          (group === GROUP_DERIVATION_IN || group === GROUP_DERIVATION_OUT)) {
        continue;
      }
      var zone = vocabZone(group);
      if (!buckets[zone]) buckets[zone] = { order: [], groups: {} };
      var bucket = buckets[zone];
      if (!Object.prototype.hasOwnProperty.call(bucket.groups, group)) {
        bucket.groups[group] = [];
        bucket.order.push(group);
      }
      bucket.groups[group].push(entries[i]);
    }
    return buckets;
  }

  function zoneHtml(zone, bucket, ctx, leadHtml) {
    var parts = [];
    if (leadHtml) parts.push(leadHtml);
    if (bucket) {
      for (var i = 0; i < bucket.order.length; i++) {
        var group = bucket.order[i];
        parts.push(groupHtml(group, bucket.groups[group], ctx));
      }
    }
    var inner = parts.join("");
    if (!inner) return ""; // 0件ゾーンは描かない
    var heading = vocabZoneHeading(zone);
    return '<div class="element-card-zone element-card-zone-' + ctx.esc(zone) + '">' +
      (heading
        ? '<div class="element-card-zone-title">' + ctx.esc(heading) + "</div>"
        : "") +
      inner + "</div>";
  }

  function zonesHtml(ctx, entries, derivations) {
    var stories = derivationStoriesHtml(derivations, ctx);
    var buckets = zoneBuckets(entries, !!stories);
    var parts = [];
    for (var i = 0; i < ZONE_ORDER.length; i++) {
      var zone = ZONE_ORDER[i];
      parts.push(zoneHtml(zone, buckets[zone], ctx, zone === ZONE_FORWARD ? stories : ""));
    }
    var inner = parts.join("");
    if (!inner) return "";
    return '<div class="element-card-lanes element-card-zones">' + inner + "</div>";
  }

  // 近傍セクションの入口。group を持つ ITEM が1つでもある（または導出ストーリーが
  // ある）ならゾーン描画、そうでなければ従来の上位/下位2レーン描画へ縮退する。
  function lanesHtml(dto, ctx) {
    var entries = laneEntries(dto, ctx);
    var derivations = derivationsOf(dto, ctx);
    if (hasGroupedItems(entries) || derivations.length) {
      var zoned = zonesHtml(ctx, entries, derivations);
      if (zoned) return zoned;
    }
    return legacyLanesHtml(dto, ctx);
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
      (ctx.hideIntrinsic ? "" : intrinsicHtml(focus, ctx)) +
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

  // 導出ストーリーカードのハンドラへ渡す要素参照。ITEM と同じ形にしておくことで
  // 呼び出し側（onCenter / onDeliberate）が分岐を持たずに済む。
  function derivationRef(derivation) {
    var id = textOf(derivation.derivation_id);
    return {
      element_type: ELEMENT_TYPE_DERIVATION,
      element_id: id,
      id: id,
      document_id: derivation.document_id,
      label: textOf(derivation.label),
      navigable: !!derivation.navigable
    };
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
    // 導出ストーリーカードの操作（中心にする / 深く検討）。ITEM 行ではなく
    // derivations[] の添字で引く（render と bind で同じ derivationsOf を通る）。
    if (ctx.editable && (ctx.onCenter || ctx.onDeliberate)) {
      var derivations = derivationsOf(dto, ctx);
      each(root, "[data-element-card-derivation]", function (btn) {
        btn.addEventListener("click", function (event) {
          event.stopPropagation();
          var ref = String(btn.getAttribute("data-element-card-derivation") || "").split(":");
          var derivation = derivations[parseInt(ref[0], 10)];
          if (!derivation) return;
          var target = derivationRef(derivation);
          if (ref[1] === "center" && ctx.onCenter) ctx.onCenter(target);
          else if (ref[1] === "deliberate" && ctx.onDeliberate) ctx.onDeliberate(target);
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
