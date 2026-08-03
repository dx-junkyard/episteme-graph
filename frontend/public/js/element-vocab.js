/*
 * 種別表示名の正本。docs/architecture/admin_ux_issues_2026-08-01.md §3.3 Phase 0。
 * 新しい画面はここを参照し、独自辞書を作らないこと。
 *
 * 背景（§3.1(1)）: 要素種別の表示名がフロント3ファイル・6辞書に分裂し、同じ
 * theory_component が「論理要素」と「コンポーネント」、同じ theory_claim が「主張」と
 * 「claim」になっていた（P1「種別語彙は1つ」の違反）。本ファイルはその正本で、
 * admin-lecture-studio.js / app.js / deliberation.js はここへ委譲する。
 *
 * ES5 / IIFE。window.ElementVocab を公開（依存なし・DOM に触らない）。
 * 公開 API:
 *   ElementVocab.kindLabel(kind)          根拠リンク系 kind の表示名
 *   ElementVocab.elementTypeLabel(type)   backend element_type / context_lens ITEM の表示名
 *   ElementVocab.statusLabel(status)      裏付け状態（relation_status 等）の段階ラベル
 *   ElementVocab.zoneForGroup(group)      ITEM の group → 4区画ゾーン
 *   ElementVocab.zoneHeading(zone)        ゾーン見出し
 *   ElementVocab.groupHeading(group)      ゾーン内の group 小見出し
 *   ElementVocab.qualifierLabel(type,key) ITEM の qualifier（種別内サブ種別）の表示名
 * kindLabel / elementTypeLabel は未知キーをそのまま返す（fail-soft・情報を落とさない）。
 * statusLabel だけは未知キーで "" を返す（呼び出し側がバッジ自体を出さない判断に使う。
 * 内部語彙をそのまま画面に出さないため — 既存 lsContextStatusBadgeHtml の挙動）。
 */
(function (global) {
  "use strict";

  // 根拠リンク（evidence_items / graph の参照行）で使う kind 語彙。
  var KIND_LABELS = {
    component: "論理要素",
    claim: "主張",
    equation: "数式",
    figure: "図",
    source: "出典",
    evidence: "根拠箇所",
    derivation: "導出",
    shared_part: "共通部品"
  };

  // backend の element_type / W層 context_lens の ITEM 語彙。
  // 正本は core/deliberation/refs.py（解決対象5種）+ core/deliberation/context_lens.py
  // （表示・近傍の11種）。訳語の確定は §3.4(a)。
  var ELEMENT_TYPE_LABELS = {
    theory_component: "論理要素",
    theory_claim: "主張",
    equation: "数式",
    figure: "図",
    shared_part: "共通部品",
    evidence: "根拠箇所",
    derivation: "導出",
    section: "節",
    thesis: "中心命題",
    symbol: "記号",
    stage: "理論ステージ",
    part: "パーツ"
  };

  // 関係・役割の裏付け状態（context_lens ITEM の relation_status /
  // focus.contextual_role_status）の段階ラベル。**数値は持たない**（W8）。
  // 語彙は core/deliberation/context_lens.py の CONTEXT_STATUS_* に対応する。
  // "unidentified" は意図的に載せない（未同定はバッジを出さずレーンの事実文で語る）。
  var STATUS_LABELS = {
    source_backed: "出典に裏付け",
    confirmed: "教員確定",
    candidate: "AI候補"
  };

  // 数式の「論証における役割」（A層 ROLE_IN_ARGUMENT_VOCAB）の表示名。正本の語彙は
  // src/episteme_graph/agents/equation_semantics/schema.py にあり、スナップショットには
  // キーのまま載る（訳語を焼き込まない）。ここが唯一の訳語表。
  // docs/features/equation_hover_content_design.md §3.1。
  var EQUATION_ROLE_LABELS = {
    premise: "前提",
    definition: "定義",
    derived: "導出結果",
    result: "結果",
    constraint: "制約"
  };

  // ──────────────────────────────────────────────────────────────────────
  // 統制語彙の訳語ミラー。
  // docs/features/element_context_presentation_redesign.md §2.4 / CP4。
  // **サーバ側正本は backend/core/element_vocab.py** で、キー集合と訳語文字列の
  // 完全一致を backend/tests/test_element_vocab_mirror.py が固定する。
  // ここと Python 正本以外に訳語を直書きしないこと。
  // これらの lookup は未知キーで "" を返す（fail-closed。内部語彙を画面へ出さない。
  // statusLabel / equationRoleLabel と同じ規約で、呼び出し側は空文字を
  // 「その欄を出さない」判断に使う）。
  // ──────────────────────────────────────────────────────────────────────

  // theory stage（src/episteme_graph/agents/component_graph/schema.py の THEORY_STAGES）。
  // かつて3箇所に分裂していた訳語の統一先（"理論的前提" は D層 assumption と衝突するため不採用）。
  var THEORY_STAGE_LABELS = {
    theory_basis: "理論の土台",
    observation_model: "観測モデル",
    observable_construction: "観測量の構成",
    equation_system: "式の体系",
    elimination: "消去",
    consistency_relation: "整合関係",
    diagnostic_application: "診断・応用"
  };

  // A層が main ノードの label に載せる英語表示名 → stage キーの逆引き（小文字化して引く）。
  var THEORY_STAGE_DISPLAY_TO_KEY = {
    "theory basis": "theory_basis",
    "observation model": "observation_model",
    "observable construction": "observable_construction",
    "equation system": "equation_system",
    "elimination": "elimination",
    "consistency relation": "consistency_relation",
    "diagnostic / application": "diagnostic_application",
    "diagnostic/application": "diagnostic_application"
  };

  // 導出・グラフの操作動詞。キーは snake_case（derivation_chain の OPERATION_ONTOLOGY）。
  // 英語表示形（"Apply definition"）も operationLabel が正規化して引く。
  var OPERATION_LABELS = {
    apply_definition: "定義を適用",
    apply_criterion: "基準を適用",
    apply_constraint: "制約を適用",
    apply_equation: "式を適用",
    apply_measurement: "測定を適用",
    apply_independence: "独立性を適用",
    infer_conclusion: "結論を導出",
    infer_intermediate_claim: "中間主張を導出",
    derive_result: "結果を導出",
    derive_consistency_relation: "整合関係を導出",
    flag_limitation: "制約を確認",
    eliminate_parameter: "パラメータを消去",
    solve_linear_system: "連立方程式を求解",
    state_assumption: "仮定を提示",
    branch_on_condition: "条件分岐",
    introduce_observable: "観測量を導入",
    compare: "比較・検証",
    define: "定義",
    construct: "構成",
    linearize: "線形化",
    normalize: "正規化",
    approximate: "近似",
    substitute: "代入",
    eliminate: "消去",
    derive: "導出",
    constrain: "制約",
    diagnose: "診断",
    solve: "求解",
    infer: "推論",
    apply: "適用",
    flag: "確認",
    state: "提示",
    introduce: "導入",
    apply_measurement_or_update: "測定・更新を適用",
    apply_non_disturbance_or_independence: "独立性・非擾乱を適用",
    transform: "変換",
    relate: "関係づけ",
    integrate: "積分",
    eliminate_variable: "変数を消去"
  };

  // 導出チェーンの種別（derivation_chain の chain_type）。
  var CHAIN_TYPE_LABELS = {
    equation_chain: "式の導出",
    claim_chain: "主張の導出",
    mixed_chain: "混在した導出",
    system_level: "系レベルの操作"
  };

  // 式の前段リンク状態（equation_semantics の LINK_STATUSES）。
  // 空欄の**理由の事実文**（CP10）はサーバ側で組み立てて sublabel に載るため、
  // ここは一覧・チップ用の短い名前だけを持つ。
  var LINK_STATUS_LABELS = {
    derived: "前段の式から導出",
    axiomatic: "出発点として置かれる",
    external_reference: "本論文の外部を参照",
    unresolved: "前段は未同定"
  };

  // 記号の定義状態（symbol_registry の DEFINITION_STATUSES + definition_missing）。
  var DEFINITION_STATUS_LABELS = {
    defined: "この論文で定義",
    redefined: "この論文で再定義",
    used: "使用のみ（定義は別の箇所）",
    definition_missing: "定義なし",
    unknown: "不明"
  };

  // 記号の有効範囲（symbol_registry の SYMBOL_SCOPES）。
  var SYMBOL_SCOPE_LABELS = {
    equation_local: "この式の中だけ",
    section: "この節の中",
    document: "論文全体"
  };

  // 主張の種別（claim_object_builder の CLAIM_TYPE_ONTOLOGY ∪ theory_claims の CHECK 語彙）。
  var CLAIM_TYPE_LABELS = {
    definition: "定義",
    criterion: "判定基準",
    assumption: "仮定",
    approximation: "近似",
    setup: "設定",
    observable_definition: "観測量の定義",
    operator_relation: "演算子の関係",
    equation_definition: "式による定義",
    equation_relation: "式の関係",
    equation_transformation: "式の変形",
    equation_approximation: "式の近似",
    equation_constraint: "式の制約",
    equation: "数式",
    relation: "関係",
    derivation_step: "導出ステップ",
    derivation_result: "導出結果",
    measurement_or_update: "測定・更新",
    causal_or_dependency_claim: "因果・依存関係",
    incompatibility_or_constraint: "非両立・制約",
    comparison: "比較",
    result: "結果",
    main_result: "主要な結果",
    conclusion: "結論",
    limitation: "限界",
    correction: "補正",
    uncertainty: "不確かさ",
    diagnostic_claim: "診断的主張",
    method_choice: "手法の選択",
    method: "手法",
    method_motivation: "手法の動機",
    background: "背景",
    prior_work: "先行研究",
    meta: "メタ情報",
    problem_statement: "問題設定",
    theory_encoding: "理論の定式化",
    structural_property: "構造的性質",
    interpretation: "解釈",
    unknown: "不明"
  };

  // ──────────────────────────────────────────────────────────────────────
  // 提示ゾーン（4区画モデル）の写像。
  // docs/features/element_context_presentation_redesign.md §2.2 / §4.3 / §6〜§7。
  //
  // ここは**フロント専用**の表示写像で Python 正本を持たない（サーバは
  // ITEM に group キーだけを載せ、どの区画に置くか・どんな見出しにするかは
  // 描画側の責務）。したがって test_element_vocab_mirror.py の MIRRORED_TABLES
  // には入れない。訳語（統制語彙）の正本が1組であることとは別の話。
  // ──────────────────────────────────────────────────────────────────────

  var ZONE_POSITIONING = "positioning";
  var ZONE_COMPOSITION = "composition";
  var ZONE_FORWARD = "forward";
  var ZONE_RELATED = "related";

  // group（ITEM の区画キー）→ ゾーン。未知の group は「関連」へ落とす（P4:
  // 知らない語彙でも ITEM を捨てない）。
  var GROUP_ZONES = {
    stage: ZONE_POSITIONING,
    thesis: ZONE_POSITIONING,
    claim: ZONE_POSITIONING,
    section: ZONE_POSITIONING,
    symbol_defined: ZONE_COMPOSITION,
    symbol_used: ZONE_COMPOSITION,
    equation_up: ZONE_COMPOSITION,
    derivation_in: ZONE_COMPOSITION,
    claim_required: ZONE_COMPOSITION,
    evidence: ZONE_COMPOSITION,
    derivation_out: ZONE_FORWARD,
    equation_down: ZONE_FORWARD,
    operation: ZONE_FORWARD
  };

  // ゾーン見出し（読者の問いに対応する4区画。§2.2）。
  var ZONE_HEADINGS = {
    positioning: "この論文での位置づけ",
    composition: "この要素を組み立てているもの",
    forward: "この要素が支えているもの／ここから先",
    related: "関連"
  };

  // ゾーン内の group 小見出し。空文字は「小見出しを出さない」の意味。
  var GROUP_HEADINGS = {
    symbol_defined: "定義する記号",
    symbol_used: "使う記号",
    equation_up: "もとになる式",
    equation_down: "次の式",
    derivation_in: "この要素を導く導出",
    derivation_out: "導出",
    claim_required: "前提となる主張",
    evidence: "本文の根拠",
    stage: "理論の段階",
    section: "掲載",
    thesis: "中心命題",
    claim: "支える主張",
    operation: "式の詳細層"
  };

  // ITEM の qualifier（種別内サブ種別の統制語彙キー）の訳。要素種別ごとに
  // 参照する表が違うため、種別で分岐して既存の strictLookup 系へ委譲する
  // （訳語文字列をここに新設しない = CP4）。
  var QUALIFIER_EQUATION_DETAIL = "equation_detail";
  var QUALIFIER_EQUATION_DETAIL_LABEL = "式の詳細";

  function lookup(table, key) {
    var raw = String(key == null ? "" : key);
    if (Object.prototype.hasOwnProperty.call(table, raw)) return table[raw];
    return raw;
  }

  // 統制語彙用の lookup。未知キーは "" （fail-closed）。
  function strictLookup(table, key) {
    var raw = String(key == null ? "" : key).replace(/^\s+|\s+$/g, "");
    if (Object.prototype.hasOwnProperty.call(table, raw)) return table[raw];
    return "";
  }

  function kindLabel(kind) {
    return lookup(KIND_LABELS, kind);
  }

  function elementTypeLabel(type) {
    return lookup(ELEMENT_TYPE_LABELS, type);
  }

  // 未知の状態値は "" を返す（キー文字列を漏らさない）。呼び出し側はこの空文字で
  // 「バッジを出さない」を判断する。
  function statusLabel(status) {
    var raw = String(status == null ? "" : status);
    if (Object.prototype.hasOwnProperty.call(STATUS_LABELS, raw)) return STATUS_LABELS[raw];
    return "";
  }

  // 未知の役割キーは "" を返す（内部語彙を画面に漏らさない。statusLabel と同じ規約）。
  function equationRoleLabel(role) {
    var raw = String(role == null ? "" : role);
    if (Object.prototype.hasOwnProperty.call(EQUATION_ROLE_LABELS, raw)) {
      return EQUATION_ROLE_LABELS[raw];
    }
    return "";
  }

  // theory stage: キー・英語表示名・"<Stage>: 説明" 形のいずれからも訳語を引く。
  function theoryStageKey(value) {
    var raw = String(value == null ? "" : value).replace(/^\s+|\s+$/g, "");
    if (!raw) return "";
    if (Object.prototype.hasOwnProperty.call(THEORY_STAGE_LABELS, raw)) return raw;
    var lowered = raw.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(THEORY_STAGE_DISPLAY_TO_KEY, lowered)) {
      return THEORY_STAGE_DISPLAY_TO_KEY[lowered];
    }
    var underscored = lowered.split(" ").join("_");
    if (Object.prototype.hasOwnProperty.call(THEORY_STAGE_LABELS, underscored)) return underscored;
    var colon = raw.indexOf(":");
    if (colon > 0) {
      var head = raw.slice(0, colon).replace(/^\s+|\s+$/g, "").toLowerCase();
      if (Object.prototype.hasOwnProperty.call(THEORY_STAGE_DISPLAY_TO_KEY, head)) {
        return THEORY_STAGE_DISPLAY_TO_KEY[head];
      }
      var headUnderscored = head.split(" ").join("_");
      if (Object.prototype.hasOwnProperty.call(THEORY_STAGE_LABELS, headUnderscored)) {
        return headUnderscored;
      }
    }
    return "";
  }

  function theoryStageLabel(key) {
    return strictLookup(THEORY_STAGE_LABELS, theoryStageKey(key));
  }

  // snake_case キーと英語表示形（"Apply definition"）の両方を引ける。
  function operationLabel(key) {
    var raw = String(key == null ? "" : key).replace(/^\s+|\s+$/g, "");
    if (!raw) return "";
    var normalized = raw.toLowerCase().split(" ").join("_").split("-").join("_");
    return strictLookup(OPERATION_LABELS, normalized);
  }

  function chainTypeLabel(key) {
    return strictLookup(CHAIN_TYPE_LABELS, key);
  }

  function linkStatusLabel(key) {
    return strictLookup(LINK_STATUS_LABELS, key);
  }

  function definitionStatusLabel(key) {
    return strictLookup(DEFINITION_STATUS_LABELS, key);
  }

  function symbolScopeLabel(key) {
    return strictLookup(SYMBOL_SCOPE_LABELS, key);
  }

  function claimTypeLabel(key) {
    return strictLookup(CLAIM_TYPE_LABELS, key);
  }

  // group → ゾーン。未知・未設定は "related"（捨てずに「関連」区画へ）。
  function zoneForGroup(group) {
    var raw = String(group == null ? "" : group).replace(/^\s+|\s+$/g, "");
    if (Object.prototype.hasOwnProperty.call(GROUP_ZONES, raw)) return GROUP_ZONES[raw];
    return ZONE_RELATED;
  }

  function zoneHeading(zone) {
    return strictLookup(ZONE_HEADINGS, zone);
  }

  function groupHeading(group) {
    return strictLookup(GROUP_HEADINGS, group);
  }

  // 要素種別 × qualifier キー → 表示名。未知は ""（内部語彙を画面に出さない）。
  function qualifierLabel(elementType, key) {
    var type = String(elementType == null ? "" : elementType).replace(/^\s+|\s+$/g, "");
    var raw = String(key == null ? "" : key).replace(/^\s+|\s+$/g, "");
    if (!raw) return "";
    if (type === "derivation") return chainTypeLabel(raw);
    if (type === "stage") return theoryStageLabel(raw);
    if (type === "theory_component") {
      return raw === QUALIFIER_EQUATION_DETAIL ? QUALIFIER_EQUATION_DETAIL_LABEL : "";
    }
    if (type === "symbol") return definitionStatusLabel(raw);
    if (type === "equation") return equationRoleLabel(raw);
    return "";
  }

  global.ElementVocab = {
    kindLabel: kindLabel,
    elementTypeLabel: elementTypeLabel,
    statusLabel: statusLabel,
    equationRoleLabel: equationRoleLabel,
    theoryStageKey: theoryStageKey,
    theoryStageLabel: theoryStageLabel,
    operationLabel: operationLabel,
    chainTypeLabel: chainTypeLabel,
    linkStatusLabel: linkStatusLabel,
    definitionStatusLabel: definitionStatusLabel,
    symbolScopeLabel: symbolScopeLabel,
    claimTypeLabel: claimTypeLabel,
    zoneForGroup: zoneForGroup,
    zoneHeading: zoneHeading,
    groupHeading: groupHeading,
    qualifierLabel: qualifierLabel
  };
})(typeof window !== "undefined" ? window : this);
