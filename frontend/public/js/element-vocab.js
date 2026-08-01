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

  function lookup(table, key) {
    var raw = String(key == null ? "" : key);
    if (Object.prototype.hasOwnProperty.call(table, raw)) return table[raw];
    return raw;
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

  global.ElementVocab = {
    kindLabel: kindLabel,
    elementTypeLabel: elementTypeLabel,
    statusLabel: statusLabel,
    equationRoleLabel: equationRoleLabel
  };
})(typeof window !== "undefined" ? window : this);
