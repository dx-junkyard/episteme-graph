---
id: IK-0854
title: "反実仮想の試算が「グラフ上に対応するノードが見つからなかった前提」を warnings で返しても画面が描かず、依存する範囲が無い結果と見分けがつかない — 仮に偽にした前提は件数だけで名前が出ず、保存の結果と一覧には共有範囲の内部値（private）がそのまま出る"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が原稿スタジオのグラフで前提を仮に偽にし、依存する範囲を確かめて保存する"
  layers: [doubt_d, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: `core/doubt/counterfactual.py::compute_counterfactual` は解決できなかった seed を warnings に入れて返すが
    （情報を落とさない設計）、`doubt-atlas.js::recomputeCounterfactual` → `applyCounterfactualStyles`（1114-1140）は collapsed /
    indeterminate の id でノードを薄くするだけで warnings を一度も読まない（ファイル内に warnings の参照が無い）。統制軸 completion:
    「依存する範囲が無い」と「試算できなかった」が同じ見た目になり、試算の失敗が成功（影響なし）として読まれる。砂場では
    IK-0852 の範囲のずれでノードが見つからなかったが、画面は何も言わなかった。処理軸 wording: 状態行は `updateCfStatus`（1043）で
    「仮に偽（前提）: N 件」と件数だけ、保存は「保存しました（共有範囲: private）」（1068）、セッション一覧のバッジは
    `escHtml(s.shared_scope)`（1518）で内部値を出す。構造軸 none（warnings の受け皿はある）。
generalization:
  level: repo_pattern
  general_form: "計算が「対象を解決できなかった」ことを副次の警告として返し、画面が主結果（影響の集合）だけを描くため、解決できなかったことが「影響なし」に見える"
pattern: failure-reported-as-success
discovery:
  perspective: [reproduction, data_inspection]
  note: "教員 te-02 が暗黒エネルギー論文のノードを仮に偽にすると「1 件」と出るだけで何を倒したかが分からず、依存範囲が「なし」で「倒しても何も揺らがないなら意味があるのか」と記した。compute の応答に `グラフ上に対応するノードが見つからなかった前提: theory_op_0001` があった。 run: uxsim/runs/c-r25-doubt-r1/20261022T124129Z-35838（seq16〜19）。"
resolution:
  perspective: [carry_through]
  note: >-
    compute_counterfactual が warnings を表示名で組み（UNRESOLVED_WARNING_PREFIX + 名前、名前が引けなければ「名前を引けない対象」）、toggled_labels / unresolved_labels / graph_available を返す。doubt-atlas.js は warnings を状態行の下に事実文で描き（renderCfWarnings）、トグル中の前提を件数でなく名前で列挙する（cfToggledNames）。共有範囲は label_vocab.SHARED_SCOPE_LABELS（既存 Visibility 語彙の日本語）をサーバが shared_scope_label として返し、保存時とセッション一覧で描く。ノードが見つからない根は IK-0852 で解消。
  landed_in:
    - "backend/core/doubt/counterfactual.py"
    - "backend/core/label_vocab.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_course_scope_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "反実仮想モードの画面表示はブラウザで未確認"
related: [IK-0852]
view_of: []
history: []
---

## 課題

症状: 反実仮想で前提を仮に偽にすると「仮に偽（前提）: 1 件」とだけ出て、依存範囲が何も薄くならない。実際はノードが
見つからず試算できていないが、画面はそれを言わない。保存とセッション一覧には「private」が出る。原因: compute の warnings を
画面が読まず、状態行が件数と内部値だけを描く。

## 発見の観点

ペルソナの通し受講（reproduction）と compute 応答の読み取り（data_inspection）。

## 解決の観点

解決済み（2026-10-23）。warnings を事実文として描き、トグル対象を名前で示す。

## 一般化

主結果と警告を分けて返す API で、画面が主結果だけを描くと、失敗が「該当なし」に化ける。確かめ方は、解決できない id を
わざと送って画面に試算できなかった旨の一文が出ることを見ること。
